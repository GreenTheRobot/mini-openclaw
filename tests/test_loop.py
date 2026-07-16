from pathlib import Path

from agent.loop import AgentLoop
from eval.tracer import Tracer, summarize
from tools.base import Tool, ToolRegistry, ToolResult
from tools.fs import write_tool
from tools.todo import todo_write_tool, update_todo_tool


class SequenceBackend:
    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, messages, tools=None):
        return next(self.responses)


def test_loop_returns_validation_error_as_observation_and_recovers(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(Tool("echo", "", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, lambda text: text))
    backend = SequenceBackend([
        {"content": "", "tool_calls": [{"id": "1", "name": "echo", "arguments": {}}]},
        {"content": "已修复参数并结束", "tool_calls": []},
    ])
    trace = tmp_path / "trace.jsonl"
    loop = AgentLoop(backend, registry, "system", workdir=tmp_path, auto_approve=True, tracer=Tracer(trace))
    assert loop.run("任务") == "已修复参数并结束"
    assert loop.last_run_status == "success"
    summary = summarize(trace)
    assert summary["errors"] == 1
    assert "estimated_cost_usd" in summary


def test_loop_preserves_multi_turn_conversation(tmp_path: Path):
    seen_messages = []

    class MultiTurnBackend:
        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            users = [message["content"] for message in messages if message["role"] == "user"]
            return {"content": f"已看到{len(users)}个用户回合", "tool_calls": []}

    loop = AgentLoop(MultiTurnBackend(), ToolRegistry(), "system", workdir=tmp_path)
    assert loop.run("第一轮") == "已看到1个用户回合"
    assert loop.run("第二轮") == "已看到2个用户回合"
    assert any(message.get("content") == "第一轮" for message in seen_messages[-1])
    loop.reset()
    assert loop.run("新会话") == "已看到1个用户回合"


def test_loop_treats_semantic_tool_failure_as_recoverable_observation(tmp_path: Path):
    seen_messages = []

    class RecoveringBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{"id": "bad-1", "name": "failing", "arguments": {}}]}
            return {"content": "检测到失败后已改用替代方案。", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(Tool("failing", "", {"type": "object", "properties": {}}, lambda: ToolResult("boom", False, "nonzero_exit")))
    trace = tmp_path / "semantic-failure.jsonl"
    loop = AgentLoop(RecoveringBackend(), registry, "system", workdir=tmp_path, auto_approve=True, tracer=Tracer(trace))
    assert loop.run("执行任务") == "检测到失败后已改用替代方案。"
    assert summarize(trace)["errors"] == 1
    assert any("[TOOL_ERROR]" in message.get("content", "") for message in seen_messages[-1])


def test_loop_emits_tool_failure_category(tmp_path: Path):
    events = []
    registry = ToolRegistry()
    registry.register(Tool(
        "vision", "", {"type": "object", "properties": {}, "additionalProperties": False},
        lambda: ToolResult("missing key", False, "vision_backend_unavailable"),
    ))
    backend = SequenceBackend([
        {"content": "", "tool_calls": [{"id": "vision-1", "name": "vision", "arguments": {}}]},
        {"content": "recovered", "tool_calls": []},
    ])
    loop = AgentLoop(
        backend,
        registry,
        "system",
        workdir=tmp_path,
        auto_approve=True,
        event_callback=lambda event, payload: events.append((event, payload)),
    )

    assert loop.run("task") == "recovered"
    failures = [payload for event, payload in events if event == "tool_result" and not payload["success"]]
    assert failures[0]["category"] == "vision_backend_unavailable"


def test_loop_corrects_unverified_save_claim_after_write_denied(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(write_tool)
    backend = SequenceBackend([
        {"content": "", "tool_calls": [{
            "id": "write-notes",
            "name": "write",
            "arguments": {"path": "structured_notes.md", "content": "# notes\n"},
        }]},
        {"content": "结构化笔记已保存到 structured_notes.md。", "tool_calls": []},
    ])

    loop = AgentLoop(backend, registry, "system", workdir=tmp_path)
    answer = loop.run("读 PDF 出结构化笔记")

    assert "结构化笔记已保存到 structured_notes.md" in answer
    assert "没有实际保存这些文件" in answer
    assert "`write` `structured_notes.md`：confirmation_required" in answer
    assert not (tmp_path / "structured_notes.md").exists()


def test_loop_blocks_experiment_final_without_git_evidence(tmp_path: Path):
    seen_messages = []

    class ExperimentBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "实验已完成。", "tool_calls": []}
            if self.turn == 2:
                return {"content": "", "tool_calls": [{
                    "id": "git-status",
                    "name": "bash",
                    "arguments": {"command": "git status --short"},
                }]}
            return {"content": "实验未正式运行；已补充 Git 状态记录。", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(Tool(
        "bash", "", {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        lambda command: "clean",
    ))
    trace = tmp_path / "experiment-git.jsonl"
    loop = AgentLoop(ExperimentBackend(), registry, "system", workdir=tmp_path, auto_approve=True, tracer=Tracer(trace))

    answer = loop.run("跑一个训练实验")

    assert "已补充 Git 状态记录" in answer
    assert any("没有可复现的 Git 记录证据" in str(message.get("content", "")) for message in seen_messages[1])
    assert any("git init" in str(message.get("content", "")) for message in seen_messages[1])
    assert any("初始 baseline commit" in str(message.get("content", "")) for message in seen_messages[1])
    assert "missing_experiment_git_evidence" in trace.read_text(encoding="utf-8")


def test_loop_corrects_experiment_success_claim_after_smoke_failure(tmp_path: Path):
    class ExperimentBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{
                    "id": "prepare",
                    "name": "experiment_prepare",
                    "arguments": {"command": "python train.py", "name": "demo"},
                }]}
            if self.turn == 2:
                return {"content": "", "tool_calls": [{
                    "id": "smoke",
                    "name": "experiment_smoke_test",
                    "arguments": {"command": "python train.py", "timeout_seconds": 5},
                }]}
            return {"content": "训练实验已成功完成。", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(Tool(
        "experiment_prepare", "", {
            "type": "object",
            "properties": {"command": {"type": "string"}, "name": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        lambda command, name="experiment": '{"git_commit": "abc123", "status": "prepared"}',
    ))
    registry.register(Tool(
        "experiment_smoke_test", "", {
            "type": "object",
            "properties": {"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        lambda command, timeout_seconds=60: ToolResult(
            '{"success": false, "returncode": 1, "stderr": "boom"}',
            False,
            "smoke_test_failed",
        ),
    ))
    loop = AgentLoop(ExperimentBackend(), registry, "system", workdir=tmp_path, auto_approve=True)

    answer = loop.run("跑一个训练实验")

    assert "训练实验已成功完成" in answer
    assert "不能声称实验已经成功完成" in answer
    assert "`experiment_smoke_test`：smoke_test_failed" in answer


def test_session_domain_grant_avoids_repeated_confirmation(tmp_path: Path):
    from agent.permissions import ConfirmationResponse, PermissionManager

    registry = ToolRegistry()
    registry.register(Tool(
        "web_fetch", "", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        lambda url: f"fetched {url}",
    ))
    backend = SequenceBackend([
        {"content": "", "tool_calls": [
            {"id": "a", "name": "web_fetch", "arguments": {"url": "https://arxiv.org/a"}},
            {"id": "b", "name": "web_fetch", "arguments": {"url": "https://arxiv.org/b"}},
        ]},
        {"content": "first done", "tool_calls": []},
        {"content": "", "tool_calls": [
            {"id": "c", "name": "web_fetch", "arguments": {"url": "https://arxiv.org/c"}},
        ]},
        {"content": "second done", "tool_calls": []},
    ])
    confirmations = []

    def confirm(name, arguments, decision):
        confirmations.append((name, arguments["url"]))
        return ConfirmationResponse(True, "session")

    loop = AgentLoop(
        backend, registry, "system", workdir=tmp_path, confirm_callback=confirm,
        permission_manager=PermissionManager("default"),
    )
    assert loop.run("first") == "first done"
    assert loop.run("second") == "second done"
    assert confirmations == [("web_fetch", "https://arxiv.org/a")]


def test_loop_normalizes_missing_tool_call_ids(tmp_path: Path):
    from agent.context import validate_tool_protocol

    seen = []

    class Backend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{"name": "echo", "arguments": {"text": "ok"}}]}
            assert validate_tool_protocol(messages) == []
            return {"content": "done", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(Tool(
        "echo", "", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        lambda text: text,
    ))
    loop = AgentLoop(Backend(), registry, "system", workdir=tmp_path, auto_approve=True)
    assert loop.run("task") == "done"
    assistant_call = next(message for message in seen[-1] if message.get("tool_calls"))
    tool_result = next(message for message in seen[-1] if message.get("role") == "tool")
    assert assistant_call["tool_calls"][0]["id"] == tool_result["tool_call_id"]


def test_repeated_multi_tool_compaction_remains_protocol_valid(tmp_path: Path):
    import json
    from agent.context import validate_tool_protocol

    class ResearchBackend:
        def __init__(self):
            self.main_turn = 0
            self.seen_main_messages = []

        def chat(self, messages, tools=None):
            if tools == []:
                return {"content": "目标与约束已保留；继续检索并核验日期。", "tool_calls": []}
            assert validate_tool_protocol(messages) == []
            self.seen_main_messages.append([dict(message) for message in messages])
            self.main_turn += 1
            if self.main_turn == 6:
                return {"content": "已完成最近论文检索。", "tool_calls": []}
            step = self.main_turn
            return {"content": "", "tool_calls": [
                {"id": f"call-{step}-a", "name": "bulk", "arguments": {"label": f"{step}a"}},
                {"id": f"call-{step}-b", "name": "bulk", "arguments": {"label": f"{step}b"}},
            ]}

    registry = ToolRegistry()
    registry.register(Tool(
        "bulk", "", {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]},
        lambda label: label + ("x" * 1800),
    ))
    backend = ResearchBackend()
    trace = tmp_path / "multi-tool-compaction.jsonl"
    loop = AgentLoop(
        backend, registry, "system", workdir=tmp_path, auto_approve=True,
        tracer=Tracer(trace), context_budget=100,
    )
    assert loop.run("执行重复的多工具任务") == "已完成最近论文检索。"
    assert backend.main_turn == 6
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("event") == "compaction" for record in records)
    assert all(validate_tool_protocol(messages) == [] for messages in backend.seen_main_messages)


def test_error_budget_produces_partial_answer_from_existing_evidence(tmp_path: Path):
    import json
    from agent.context import validate_tool_protocol

    class Backend:
        def __init__(self):
            self.main_turn = 0
            self.fallback_called = False

        def chat(self, messages, tools=None):
            assert validate_tool_protocol(messages) == []
            if tools == []:
                self.fallback_called = True
                assert any(
                    "停止探索并交付结果" in str(item.get("content", ""))
                    for item in messages
                )
                return {
                    "content": "已验证论文与项目页；代码入口因四次路径错误尚未核验。",
                    "tool_calls": [],
                }
            self.main_turn += 1
            if self.main_turn == 1:
                return {"content": "", "tool_calls": [
                    {"id": "good", "name": "fetch", "arguments": {"path": "project"}},
                ]}
            if self.main_turn == 2:
                return {"content": "", "tool_calls": [
                    {
                        "id": f"bad-{index}",
                        "name": "fetch",
                        "arguments": {"path": f"missing-{index}"},
                    }
                    for index in range(3)
                ]}
            return {"content": "", "tool_calls": [
                {"id": "bad-4", "name": "fetch", "arguments": {"path": "missing-4"}},
            ]}

    def fetch(path: str):
        if path == "project":
            return ToolResult("verified project evidence", True, "ok")
        return ToolResult(f"missing: {path}", False, "http_not_found")

    registry = ToolRegistry()
    registry.register(Tool(
        "fetch", "", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }, fetch,
    ))
    backend = Backend()
    trace = tmp_path / "error-budget.jsonl"
    loop = AgentLoop(
        backend, registry, "system", workdir=tmp_path,
        auto_approve=True, tracer=Tracer(trace), max_consecutive_errors=4,
    )

    answer = loop.run("研究项目")

    assert answer == "已验证论文与项目页；代码入口因四次路径错误尚未核验。"
    assert backend.fallback_called is True
    assert loop.last_run_status == "partial"
    records = [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    assert any(record.get("event") == "error_budget_exhausted" for record in records)
    assert records[-1]["event"] == "run_end"
    assert records[-1]["status"] == "partial"
    assert records[-1]["reason"] == "tool_error_budget"


def test_loop_rejects_status_only_research_answer(tmp_path: Path):
    seen_messages = []

    class ResearchBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {
                    "content": "根据历史压缩备忘，当前 task_list 为空。请问您希望我下一步做什么？",
                    "tool_calls": [],
                }
            return {
                "content": (
                    "# 项目调研报告\n\n项目解决机器人策略泛化问题。\n\n"
                    "项目链接：https://graph-robots.github.io/gap/\n"
                    "论文：arXiv:2607.05369\n"
                    "GitHub 仓库：https://github.com/graph-robots/graph-as-policy\n\n"
                    "方法和思路：用图节点与边表达物体、机器人状态和动作关系，"
                    "策略网络在结构化图上推理动作。训练流程抽取任务结构并学习可复用策略。\n\n"
                    "创新点是结构化策略表示；局限是仍需验证真实机器人部署与数据规模。\n\n"
                    "信息来源：项目定位来自项目页，论文编号来自论文链接，代码结论来自仓库。"
                ),
                "tool_calls": [],
            }

    trace = tmp_path / "research-final.jsonl"
    loop = AgentLoop(ResearchBackend(), ToolRegistry(), "system", workdir=tmp_path, tracer=Tracer(trace))
    answer = loop.run("阅读网页项目并找到论文和 GitHub 仓库，详细讲解方法和思路")

    assert "# 项目调研报告" in answer
    assert "https://graph-robots.github.io/gap/" in answer
    assert any("不满足科研智能体" in str(message.get("content", "")) for message in seen_messages[1])
    assert "insufficient_research_answer" in trace.read_text(encoding="utf-8")


def test_loop_rejects_linkless_literature_research_answer(tmp_path: Path):
    seen_messages = []

    class LiteratureResearchBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {
                    "content": (
                        "# 多模态压缩论文调研\n\n"
                        "## 严格匹配论文\n"
                        "### Efficient Multimodal Compression\n"
                        "- 提交日期：2026-07-13\n"
                        "- 摘要：研究视觉 token 压缩。\n"
                        "- 解决问题：降低多模态模型推理成本。\n"
                        "- 核心方法：筛选高信息量视觉 token。\n"
                        "- 主要贡献/结论：在减少 token 的同时保持主要能力。\n\n"
                        "## 检索说明\n使用论文关键词检索。"
                    ),
                    "tool_calls": [],
                }
            return {
                "content": (
                    "# 多模态压缩论文调研\n\n"
                    "## 严格匹配论文\n"
                    "### Efficient Multimodal Compression\n"
                    "- 提交日期：2026-07-13\n"
                    "- 研究方向：多模态模型压缩。\n"
                    "- 摘要：研究视觉 token 压缩。\n"
                    "- 解决问题：降低多模态模型推理成本。\n"
                    "- 核心方法：筛选高信息量视觉 token。\n"
                    "- 主要贡献/结论：在减少 token 的同时保持主要能力。\n"
                    "- 来源：https://arxiv.org/abs/2607.12345\n\n"
                    "## 检索说明\n使用论文关键词检索。"
                ),
                "tool_calls": [],
            }

    trace = tmp_path / "linkless-literature.jsonl"
    loop = AgentLoop(LiteratureResearchBackend(), ToolRegistry(), "system", workdir=tmp_path, tracer=Tracer(trace))
    answer = loop.run("做一个多模态压缩论文调研")

    assert "https://arxiv.org/abs/2607.12345" in answer
    assert any("不满足科研智能体" in str(message.get("content", "")) for message in seen_messages[1])
    assert "insufficient_research_answer" in trace.read_text(encoding="utf-8")


def test_literature_report_accepts_equivalent_field_names(tmp_path: Path):
    class EquivalentReportBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            self.turn += 1
            return {
                "content": (
                    "# 最近一周多模态压缩论文\n\n"
                    "检索范围：2026-07-07 至 2026-07-14。匹配论文 1 篇。\n\n"
                    "## Efficient Multimodal Token Compression\n"
                    "- 作者：Alice Zhang 等\n"
                    "- 日期：2026-07-13\n"
                    "- 方向：多模态模型压缩与视觉 token 裁剪\n"
                    "- 摘要概括：这篇工作研究如何减少视觉 token 数量，同时保留跨模态理解能力。\n"
                    "- 目标问题：多模态大模型推理时视觉 token 带来的显存和计算开销过高。\n"
                    "- 方法思路：先估计视觉 token 信息量，再保留对语言生成最有帮助的 token，并在跨模态层继续对齐。\n"
                    "- 主要结论：在若干视觉问答和图文理解任务上降低计算量，同时保持主要性能。\n"
                    "- 链接：https://arxiv.org/abs/2607.12345\n\n"
                    "## 检索说明\n使用 arXiv 日期过滤和 multimodal compression / visual token compression 关键词。"
                ),
                "tool_calls": [],
            }

    trace = tmp_path / "equivalent-literature.jsonl"
    backend = EquivalentReportBackend()
    loop = AgentLoop(backend, ToolRegistry(), "system", workdir=tmp_path, tracer=Tracer(trace))

    answer = loop.run("找最近一周多模态模型压缩的新论文")

    assert "https://arxiv.org/abs/2607.12345" in answer
    assert backend.turn == 1
    assert "insufficient_research_answer" not in trace.read_text(encoding="utf-8")


def test_loop_blocks_final_answer_while_todo_has_open_items(tmp_path: Path):
    seen_messages = []

    class PrematureFinalBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{
                    "id": "create-tasks",
                    "name": "todo_write",
                    "arguments": {"items": ["微信通知"]},
                }]}
            if self.turn == 2:
                return {"content": "实验和微信通知都已完成。", "tool_calls": []}
            if self.turn == 3:
                return {"content": "", "tool_calls": [{
                    "id": "finish-notify",
                    "name": "update_todo",
                    "arguments": {"id": 1, "status": "completed"},
                }]}
            return {"content": "已完成全部任务。", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(todo_write_tool)
    registry.register(update_todo_tool)
    trace = tmp_path / "blocked-final.jsonl"
    loop = AgentLoop(PrematureFinalBackend(), registry, "system", workdir=tmp_path, auto_approve=True, tracer=Tracer(trace))

    assert loop.run("做实验，最后微信通知。") == "已完成全部任务。"
    assert loop.last_run_status == "success"
    assert any("权威 TODO 仍显示有未完成项" in str(message.get("content", "")) for message in seen_messages[2])
    assert "final_blocked" in trace.read_text(encoding="utf-8")


def test_loop_reuses_successful_identical_fetch_observation(tmp_path: Path):
    seen_messages = []
    calls = {"count": 0}

    class RepeatFetchBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn in {1, 2}:
                return {"content": "", "tool_calls": [{
                    "id": f"fetch-{self.turn}",
                    "name": "web_fetch",
                    "arguments": {"url": "https://example.com/project"},
                }]}
            return {"content": "final report from reused observation", "tool_calls": []}

    def fake_fetch(url: str):
        calls["count"] += 1
        return "project page with paper and github"

    registry = ToolRegistry()
    registry.register(Tool(
        "web_fetch", "", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, fake_fetch,
    ))
    loop = AgentLoop(RepeatFetchBackend(), registry, "system", workdir=tmp_path, auto_approve=True)

    assert loop.run("read the project page") == "final report from reused observation"
    assert calls["count"] == 1
    assert any("已复用此前相同调用" in str(message.get("content", "")) for message in seen_messages[-1])


def test_recent_literature_web_search_is_routed_to_arxiv(tmp_path: Path):
    seen_messages = []
    calls = {"web": 0, "arxiv": []}

    class LiteratureBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{
                    "id": "search-1",
                    "name": "web_search",
                    "arguments": {"query": "recent multimodal compression papers", "max_results": 5},
                }]}
            return {
                "content": (
                    "最近一周论文检索报告\n"
                    "提交日期区间：本周。严格匹配 1 篇。\n"
                    "标题：Efficient Multimodal Compression\n"
                    "摘要：研究视觉 token 压缩。\n"
                    "解决问题：多模态模型推理成本。\n"
                    "核心方法：筛选视觉 token 并保留跨模态交互。\n"
                    "来源：https://arxiv.org/abs/2607.12345"
                ),
                "tool_calls": [],
            }

    def fake_web_search(**kwargs):
        calls["web"] += 1
        return "should not be used"

    def fake_arxiv_search(**kwargs):
        calls["arxiv"].append(kwargs)
        return "arXiv paper metadata with source link"

    registry = ToolRegistry()
    registry.register(Tool(
        "web_search", "", {
            "type": "object",
            "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
            "required": ["query"],
        },
        fake_web_search,
    ))
    registry.register(Tool(
        "arxiv_search", "", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
        fake_arxiv_search,
    ))

    system = "# 运行时日期\n当前本地日期为 2026-07-14，时区为 Asia/Shanghai."
    loop = AgentLoop(LiteratureBackend(), registry, system, workdir=tmp_path, auto_approve=True)
    answer = loop.run("帮我找最近一周多模态压缩论文")

    assert "https://arxiv.org/abs/2607.12345" in answer
    assert calls["web"] == 0
    assert len(calls["arxiv"]) == 1
    assert calls["arxiv"][0]["start_date"] == "2026-07-07"
    assert calls["arxiv"][0]["end_date"] == "2026-07-14"
    assert calls["arxiv"][0]["max_results"] == 5
    assert any("自动从 web_search 路由到 arxiv_search" in str(message.get("content", "")) for message in seen_messages[-1])


def test_recent_literature_arxiv_dates_are_corrected_from_runtime_context(tmp_path: Path):
    seen_messages = []
    calls = []

    class StaleDateBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{
                    "id": "arxiv-1",
                    "name": "arxiv_search",
                    "arguments": {
                        "query": "multimodal compression",
                        "start_date": "2025-04-01",
                        "end_date": "2025-04-08",
                    },
                }]}
            return {
                "content": (
                    "最近一周论文检索报告\n"
                    "严格匹配 1 篇。提交日期：2026-07-13。\n"
                    "摘要：多模态压缩。解决问题：推理成本。核心方法：token 压缩。\n"
                    "来源：https://arxiv.org/abs/2607.12345"
                ),
                "tool_calls": [],
            }

    def fake_arxiv_search(**kwargs):
        calls.append(kwargs)
        return "arXiv result from corrected dates"

    registry = ToolRegistry()
    registry.register(Tool(
        "arxiv_search", "", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["query"],
        },
        fake_arxiv_search,
    ))

    system = "# 运行时日期\n当前本地日期为 2026-07-14，时区为 Asia/Shanghai."
    loop = AgentLoop(StaleDateBackend(), registry, system, workdir=tmp_path, auto_approve=True)

    answer = loop.run("找最近一周多模态模型压缩的新论文")

    assert "https://arxiv.org/abs/2607.12345" in answer
    assert calls == [{
        "query": "multimodal compression",
        "start_date": "2026-07-07",
        "end_date": "2026-07-14",
    }]
    assert any("自动校正 arxiv_search 日期区间" in str(message.get("content", "")) for message in seen_messages[-1])


def test_direct_url_web_search_is_routed_to_fetch(tmp_path: Path):
    seen_messages = []
    calls = {"web": 0, "fetch": []}

    class UrlBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{
                    "id": "search-1",
                    "name": "web_search",
                    "arguments": {"query": "https://example.com/project"},
                }]}
            return {"content": "已读取页面：https://example.com/project", "tool_calls": []}

    def fake_web_search(**kwargs):
        calls["web"] += 1
        return "should not be used"

    def fake_fetch(**kwargs):
        calls["fetch"].append(kwargs)
        return "example project page"

    registry = ToolRegistry()
    registry.register(Tool(
        "web_search", "", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        fake_web_search,
    ))
    registry.register(Tool(
        "web_fetch", "", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        fake_fetch,
    ))

    loop = AgentLoop(UrlBackend(), registry, "system", workdir=tmp_path, auto_approve=True)
    assert loop.run("读取这个地址 https://example.com/project") == "已读取页面：https://example.com/project"
    assert calls["web"] == 0
    assert calls["fetch"] == [{"url": "https://example.com/project"}]
    assert any("自动从 web_search 路由到 web_fetch" in str(message.get("content", "")) for message in seen_messages[-1])


def test_loop_warns_against_network_probe_through_bash(tmp_path: Path):
    seen_messages = []

    class BashProbeBackend:
        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            seen_messages.append([dict(message) for message in messages])
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{
                    "id": "bash-1",
                    "name": "bash",
                    "arguments": {"command": "which curl wget && python -c \"import requests\""},
                }]}
            return {"content": "will use web_search instead", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(Tool(
        "bash", "", {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        lambda command: ToolResult("blocked", False, "sandbox_denied"),
    ))
    loop = AgentLoop(BashProbeBackend(), registry, "system", workdir=tmp_path, auto_approve=True)

    assert loop.run("check network tools") == "will use web_search instead"
    assert any("改用 web_search/web_fetch" in str(message.get("content", "")) for message in seen_messages[-1])


def test_loop_summarizes_evidence_after_max_turns(tmp_path: Path):
    class TurnLimitBackend:
        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None):
            self.calls.append(tools)
            if len(self.calls) == 1:
                return {"content": "", "tool_calls": [{
                    "id": "read-1", "name": "echo", "arguments": {"text": "evidence"},
                }]}
            return {"content": "基于已有证据生成的部分报告", "tool_calls": []}

    registry = ToolRegistry()
    registry.register(Tool(
        "echo", "", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        lambda text: text,
    ))
    backend = TurnLimitBackend()
    trace = tmp_path / "turn-limit.jsonl"
    loop = AgentLoop(
        backend, registry, "system", max_turns=1, workdir=tmp_path,
        auto_approve=True, tracer=Tracer(trace),
    )

    assert loop.run("执行复杂任务") == "基于已有证据生成的部分报告"
    assert backend.calls[-1] == []
    assert loop.last_run_status == "partial"
    assert "max_turns_summarized" in trace.read_text(encoding="utf-8")


def test_literature_search_budget_forces_structured_report_and_rewrite(tmp_path: Path):
    class LiteratureBackend:
        def __init__(self):
            self.main_calls = 0
            self.summary_calls = 0

        def chat(self, messages, tools=None):
            if tools == []:
                self.summary_calls += 1
                if self.summary_calls == 1:
                    return {"content": "搜索过程：我进行了很多轮检索，但结果有限。", "tool_calls": []}
                return {
                    "content": (
                        "# 最近一周论文检索报告\n\n"
                        "## 检索范围\n2026-07-07 至 2026-07-14。严格匹配 1 篇。\n\n"
                        "## 严格匹配论文\n"
                        "### Efficient Multimodal Compression\n"
                        "- 作者：Alice Zhang、Bob Li\n"
                        "- 提交日期：2026-07-13\n"
                        "- 研究方向：多模态模型压缩与视觉 token 压缩\n"
                        "- 摘要：在尽量保持推理精度的前提下压缩视觉 token。\n"
                        "- 解决问题：多模态推理中视觉 token 带来的计算和显存开销。\n"
                        "- 核心方法：按信息量筛选视觉 token，并在跨模态层中保留关键交互。\n"
                        "- 主要贡献/结论：减少 token 数量，同时维持主要基准性能。\n"
                        "- 来源：https://arxiv.org/abs/2607.12345\n\n"
                        "## 扩展相关工作\n无。\n\n"
                        "## 检索说明\n使用 arXiv 日期过滤和多模态压缩关键词检索。"
                    ),
                    "tool_calls": [],
                }
            self.main_calls += 1
            return {"content": "", "tool_calls": [{
                "id": f"arxiv-{self.main_calls}",
                "name": "arxiv_search",
                "arguments": {
                    "query": f"multimodal compression {self.main_calls}",
                    "start_date": "2026-07-07",
                    "end_date": "2026-07-14",
                },
            }]}

    registry = ToolRegistry()
    registry.register(Tool(
        "arxiv_search", "", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["query"],
        },
        lambda **kwargs: "structured arxiv paper metadata and abstract",
    ))
    backend = LiteratureBackend()
    trace = tmp_path / "literature-budget.jsonl"
    loop = AgentLoop(
        backend, registry, "system", workdir=tmp_path, auto_approve=True,
        tracer=Tracer(trace), max_research_calls=2,
    )

    answer = loop.run("找最近一周多模态模型压缩的新论文")

    assert backend.main_calls == 2
    assert backend.summary_calls == 2
    assert "严格匹配论文" in answer
    assert "解决问题" in answer
    assert "核心方法" in answer
    assert "https://arxiv.org/abs/2607.12345" in answer
    assert "research_search_budget" in trace.read_text(encoding="utf-8")
