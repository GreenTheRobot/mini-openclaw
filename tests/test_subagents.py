from pathlib import Path

from PIL import Image

from agent.subagents import _agent_todo_path, run_multi_agent
from tools.base import Tool, ToolRegistry


class NoToolBackend:
    supports_tools = True

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        system = str(messages[0].get("content", "")) if messages else ""
        if "coordinator" in system:
            return {"content": "综合结果：已有研究和工程结论。", "tool_calls": []}
        if "Planner" in system:
            return {"content": "计划：Research 读论文；Engineering 验证代码。", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "审查结论：通过。证据来自子 agent 输出。", "tool_calls": []}
        return {"content": "子 agent 完成。", "tool_calls": []}


class RecordingBackend(NoToolBackend):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label
        self.saw_image = False

    def chat(self, messages, tools=None):
        for message in messages:
            content = message.get("content")
            if isinstance(content, list) and any(block.get("type") == "image" for block in content if isinstance(block, dict)):
                self.saw_image = True
        return {"content": f"{self.label} 完成。", "tool_calls": []}


class ToolCallingBackend:
    supports_tools = True

    def __init__(self) -> None:
        self.sent_tool_call = False
        self.reviewer_message = ""

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system:
            return {"content": '{"use_subagents": true, "reason": "需要论文证据", "main_task": "", "assignments": {"research": "读取 paper.md 并摘取关键证据", "engineering": "", "multimodal": ""}}', "tool_calls": []}
        if "Planner" in system:
            return {"content": "计划：Research 调工具核对证据。", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "综合结果：已基于工具证据整理。", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            self.reviewer_message = str(messages[-1].get("content", ""))
            return {"content": "审查结论：通过。", "tool_calls": []}
        if not self.sent_tool_call:
            self.sent_tool_call = True
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call_read",
                    "name": "read",
                    "arguments": {"path": "paper.md"},
                }],
            }
        return {"content": "Research Agent 已读取 paper.md。", "tool_calls": []}


class RevisionBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.synthesis_calls = 0
        self.review_calls = 0

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system:
            return {"content": '{"use_subagents": true, "reason": "需要论文证据", "main_task": "", "assignments": {"research": "阅读论文并给出证据", "engineering": "", "multimodal": ""}}', "tool_calls": []}
        if "coordinator" in system:
            self.synthesis_calls += 1
            if self.synthesis_calls == 1:
                return {"content": "初版答案：结论。", "tool_calls": []}
            return {"content": "修订版答案：结论，并补充风险说明。", "tool_calls": []}
        if "Planner" in system:
            return {"content": "计划：Research 给证据。", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            self.review_calls += 1
            if self.review_calls == 1:
                return {"content": "审查结论：需修订\n1. 答案缺少风险说明。", "tool_calls": []}
            return {"content": "审查结论：通过\n已补充风险说明。", "tool_calls": []}
        return {"content": "Research Agent 输出：证据。", "tool_calls": []}


class DirectDecisionBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.saw_subagent_prompt = False

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system and "JSON" in system:
            return {"content": '{"use_subagents": false, "reason": "简单任务", "main_task": "直接回答 README 是什么", "assignments": {"research": "", "engineering": "", "multimodal": ""}}', "tool_calls": []}
        if "Research Agent" in system or "Engineering Agent" in system or "Multimodal Agent" in system:
            self.saw_subagent_prompt = True
        return {"content": "主 Agent 直接完成。", "tool_calls": []}


class AssignmentBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.research_message = ""
        self.engineering_message = ""

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system and "JSON" in system:
            return {"content": '{"use_subagents": true, "reason": "需要分工", "main_task": "", "assignments": {"research": "只阅读论文第 3 节并总结模型结构", "engineering": "只检查 tests/test_subagents.py 是否覆盖调度", "multimodal": ""}}', "tool_calls": []}
        if "Research Agent" in system:
            self.research_message = str(messages[-1].get("content", ""))
            return {"content": "Research 完成。", "tool_calls": []}
        if "Engineering Agent" in system:
            self.engineering_message = str(messages[-1].get("content", ""))
            return {"content": "Engineering 完成。", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "综合结果。", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "审查结论：通过。", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


def test_subagent_todo_paths_are_role_isolated():
    assert _agent_todo_path("parent/run", "research") != _agent_todo_path("parent/run", "engineering")
    assert ".mini-openclaw/subagents/parent-run/research/tasks.json" == _agent_todo_path("parent/run", "research")


def test_run_multi_agent_returns_reviewed_synthesis(tmp_path: Path):
    trace = tmp_path / "trace.jsonl"
    answer = run_multi_agent(
        task="查一篇论文并检查代码实验",
        backend=NoToolBackend(),
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
    )

    assert "综合结果" in answer
    assert "## Reviewer" not in answer
    assert "审查结论" not in answer
    assert '"event": "review"' in trace.read_text(encoding="utf-8")


def test_main_agent_can_skip_subagents_for_simple_task(tmp_path: Path):
    backend = DirectDecisionBackend()
    trace = tmp_path / "trace.jsonl"

    answer = run_multi_agent(
        task="README 是什么",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
    )

    assert answer == "主 Agent 直接完成。"
    assert backend.saw_subagent_prompt is False
    log = trace.read_text(encoding="utf-8")
    assert '"event": "orchestration"' in log
    assert '"use_subagents": false' in log


def test_main_agent_assigns_concrete_work_to_subagents(tmp_path: Path):
    backend = AssignmentBackend()

    answer = run_multi_agent(
        task="分析论文并检查测试",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
    )

    assert answer == "综合结果。"
    assert "只阅读论文第 3 节并总结模型结构" in backend.research_message
    assert "只检查 tests/test_subagents.py 是否覆盖调度" in backend.engineering_message


def test_multi_agent_uses_vision_backend_only_for_images(tmp_path: Path):
    image_path = tmp_path / "figure.png"
    Image.new("RGB", (1, 1), "white").save(image_path)
    text_backend = RecordingBackend("text")
    vision_backend = RecordingBackend("vision")

    run_multi_agent(
        task="查论文并检查代码，同时分析这张图片",
        backend=text_backend,
        vision_backend=vision_backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        image_paths=[str(image_path)],
    )

    assert text_backend.saw_image is False
    assert vision_backend.saw_image is True


def test_reviewer_receives_subagent_tool_trace_evidence(tmp_path: Path):
    backend = ToolCallingBackend()
    registry = ToolRegistry()
    registry.register(Tool(
        name="read",
        description="read test file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        run=lambda path: f"file content from {path}",
    ))

    run_multi_agent(
        task="阅读论文",
        backend=backend,
        registry=registry,
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
    )

    assert "# 子 agent 工具调用记录" in backend.reviewer_message
    assert '"tool": "read"' in backend.reviewer_message
    assert '"path": "paper.md"' in backend.reviewer_message
    assert "file content from paper.md" in backend.reviewer_message


def test_multi_agent_revises_answer_when_reviewer_requests_changes(tmp_path: Path):
    backend = RevisionBackend()
    trace = tmp_path / "trace.jsonl"

    answer = run_multi_agent(
        task="阅读论文",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
    )

    assert backend.synthesis_calls == 2
    assert backend.review_calls == 2
    assert "修订版答案" in answer
    assert "审查结论：需修订" not in answer
    assert "审查结论：通过" not in answer
    log = trace.read_text(encoding="utf-8")
    assert '"event": "review"' in log
    assert '"phase": "initial"' in log
    assert '"phase": "final"' in log
    assert "审查结论：需修订" in log
    assert "审查结论：通过" in log
