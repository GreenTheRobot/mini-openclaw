from pathlib import Path
import threading
import time

from PIL import Image

from agent.permissions import ConfirmationResponse
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


class ParallelConfirmBackend:
    supports_tools = True

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sent_tools: set[str] = set()

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        user_message = str(messages[-1].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "split shell checks", "main_task": "", '
                    '"subagents": ['
                    '{"id": "alpha-check", "role": "engineering", "task": "run alpha shell check"},'
                    '{"id": "beta-check", "role": "engineering", "task": "run beta shell check"}'
                    "]}"
                ),
                "tool_calls": [],
            }
        if "Engineering Agent" in system:
            key = "alpha" if "alpha" in user_message else "beta"
            with self.lock:
                sent = key in self.sent_tools
                if not sent:
                    self.sent_tools.add(key)
            if not sent:
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"call_{key}",
                        "name": "bash",
                        "arguments": {"command": f"echo {key}"},
                    }],
                }
            return {"content": f"{key} done", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "combined result", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


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

class FinalReviewRejectBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.review_calls = 0
        self.final_delivery_calls = 0

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "JSON" in system:
            return {"content": '{"use_subagents": true, "reason": "needs evidence", "main_task": "", "assignments": {"research": "provide evidence", "engineering": "", "multimodal": ""}}', "tool_calls": []}
        if "Research Agent" in system:
            return {"content": "research output", "tool_calls": []}
        if "最终答复作者" in system:
            self.final_delivery_calls += 1
            return {"content": "实验结果：没有可核验的运行数据，因此不能给出指标对比。已确认缺口是 key result has no evidence；需要实际运行训练命令后再填入结果表。", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "unsupported final answer", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            self.review_calls += 1
            return {"content": "审查结论：需修订\n1. key result has no evidence.", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


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


class DirectResearchBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.answer_turn = 0

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system and "JSON" in system:
            return {"content": '{"use_subagents": false, "reason": "主 Agent 足够", "main_task": "直接总结候选论文", "assignments": {"research": "", "engineering": "", "multimodal": ""}}', "tool_calls": []}
        self.answer_turn += 1
        if self.answer_turn == 1:
            return {
                "content": (
                    "# 论文调研\n\n"
                    "## 严格匹配论文\n"
                    "### Efficient Multimodal Compression\n"
                    "- 提交日期：2026-07-13\n"
                    "- 摘要：研究视觉 token 压缩。\n"
                    "- 解决问题：降低多模态模型推理成本。\n"
                    "- 核心方法：筛选高信息量视觉 token。\n"
                    "- 主要贡献/结论：减少 token 并保持能力。\n\n"
                    "## 检索说明\n使用论文关键词检索。"
                ),
                "tool_calls": [],
            }
        return {
            "content": (
                "# 论文调研\n\n"
                "## 检索范围\n"
                "本轮围绕多模态模型压缩和视觉 token 压缩进行候选论文整理，严格匹配 1 篇。\n\n"
                "## 严格匹配论文\n"
                "### Efficient Multimodal Compression\n"
                "- 提交日期：2026-07-13\n"
                "- 研究方向：多模态模型压缩。\n"
                "- 摘要：研究视觉 token 压缩。\n"
                "- 解决问题：降低多模态模型推理成本。\n"
                "- 核心方法：筛选高信息量视觉 token。\n"
                "- 主要贡献/结论：减少 token 并保持能力。\n"
                "- 来源：https://arxiv.org/abs/2607.12345\n\n"
                "## 扩展相关工作\n"
                "当前证据中没有其他可核验候选论文，因此不补旧论文凑数。\n\n"
                "## 检索说明\n使用论文关键词检索。"
            ),
            "tool_calls": [],
        }


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


class NoOpAssignmentBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.orchestration_user_message = ""
        self.research_ran = False
        self.engineering_ran = False
        self.multimodal_ran = False

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system and "JSON" in system:
            self.orchestration_user_message = str(messages[-1].get("content", ""))
            return {"content": '{"use_subagents": true, "reason": "需要论文检索", "main_task": "", "assignments": {"research": "检索最近一周多模态压缩论文并保留来源", "engineering": "不需要", "multimodal": "不需要"}}', "tool_calls": []}
        if "Research Agent" in system:
            self.research_ran = True
            return {
                "content": (
                    "最近一周论文检索报告\n"
                    "严格匹配 1 篇。提交日期：2026-07-13。\n"
                    "摘要：多模态模型压缩。解决问题：推理成本。核心方法：token 压缩。\n"
                    "来源：https://arxiv.org/abs/2607.12345"
                ),
                "tool_calls": [],
            }
        if "Engineering Agent" in system:
            self.engineering_ran = True
            return {"content": "should not run", "tool_calls": []}
        if "Multimodal Agent" in system:
            self.multimodal_ran = True
            return {"content": "should not run", "tool_calls": []}
        if "coordinator" in system:
            return {
                "content": (
                    "最近一周论文检索报告\n"
                    "严格匹配 1 篇。提交日期：2026-07-13。\n"
                    "摘要：多模态模型压缩。解决问题：推理成本。核心方法：token 压缩。\n"
                    "来源：https://arxiv.org/abs/2607.12345"
                ),
                "tool_calls": [],
            }
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "审查结论：通过。", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}

class EngineeringToolBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sent_tool_call = False

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system and "JSON" in system:
            return {"content": '{"use_subagents": true, "reason": "需要工程执行", "main_task": "", "assignments": {"research": "", "engineering": "调用 wechat_file_transfer 发送你好", "multimodal": ""}}', "tool_calls": []}
        if "Engineering Agent" in system:
            tool_names = {tool["function"]["name"] for tool in tools or []}
            if "wechat_file_transfer" in tool_names and not self.sent_tool_call:
                self.sent_tool_call = True
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_wechat",
                        "name": "wechat_file_transfer",
                        "arguments": {"message": "你好"},
                    }],
                }
            return {"content": "Engineering 完成发送。", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "综合结果。", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "审查结论：通过。", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class ResearchBashBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sent_tool_call = False
        self.saw_bash = False
        self.research_message = ""

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "needs research execution", '
                    '"main_task": "", "assignments": {"research": "use bash to inspect local environment", '
                    '"engineering": "", "multimodal": ""}}'
                ),
                "tool_calls": [],
            }
        if "Research Agent" in system:
            if not self.research_message:
                self.research_message = str(messages[-1].get("content", ""))
            tool_names = {tool["function"]["name"] for tool in tools or []}
            self.saw_bash = "bash" in tool_names
            if self.saw_bash and not self.sent_tool_call:
                self.sent_tool_call = True
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_bash",
                        "name": "bash",
                        "arguments": {"command": "printf research-bash"},
                    }],
                }
            return {"content": "Research bash done.", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "synthesis done.", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed.", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class MultimodalBashBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sent_tool_call = False
        self.saw_bash = False
        self.saw_image = False

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "needs multimodal execution", '
                    '"main_task": "", "assignments": {"research": "", "engineering": "", '
                    '"multimodal": "inspect the image and local environment"}}'
                ),
                "tool_calls": [],
            }
        if "Multimodal Agent" in system:
            for message in messages:
                content = message.get("content")
                if isinstance(content, list) and any(
                    block.get("type") == "image"
                    for block in content
                    if isinstance(block, dict)
                ):
                    self.saw_image = True
            tool_names = {tool["function"]["name"] for tool in tools or []}
            self.saw_bash = "bash" in tool_names
            if self.saw_bash and not self.sent_tool_call:
                self.sent_tool_call = True
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_bash",
                        "name": "bash",
                        "arguments": {"command": "printf multimodal-bash"},
                    }],
                }
            return {"content": "Multimodal bash done.", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "synthesis done.", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed.", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class LinkRepairBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.synthesis_calls = 0

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "主 Agent" in system and "JSON" in system:
            return {"content": '{"use_subagents": true, "reason": "需要论文调研", "main_task": "", "assignments": {"research": "找论文并保留来源链接", "engineering": "", "multimodal": ""}}', "tool_calls": []}
        if "Research Agent" in system:
            return {
                "content": (
                    "找到论文：Efficient Multimodal Compression。\n"
                    "来源：https://arxiv.org/abs/2607.12345\n"
                    "核心方法：压缩视觉 token。"
                ),
                "tool_calls": [],
            }
        if "coordinator" in system:
            self.synthesis_calls += 1
            if self.synthesis_calls == 1:
                return {"content": "综合结果：找到一篇多模态压缩论文，方法是压缩视觉 token。", "tool_calls": []}
            return {
                "content": (
                    "# 论文调研结果\n\n"
                    "## 论文\n"
                    "Efficient Multimodal Compression 聚焦多模态模型中的视觉 token 开销问题。\n\n"
                    "## 方法和思路\n"
                    "该工作围绕视觉 token 压缩展开，通过保留更关键的视觉 token 来降低推理成本，"
                    "同时尽量维持跨模态理解能力。这个结论来自 Research Agent 对论文条目的整理。\n\n"
                    "## 核心贡献\n"
                    "它把多模态压缩问题具体落到视觉 token 选择上，适合作为后续阅读和复现实验的候选论文。\n\n"
                    "## 局限\n"
                    "当前证据只包含论文条目摘要级信息，尚未验证实验表格和代码实现。\n\n"
                    "## 依据说明\n"
                    "上述论文标题、主题和方法概括均来自 Research Agent 输出；最终答案没有新增其他论文事实，"
                    "也没有声称已经完成代码复现或实验验证。\n\n"
                    "## 来源链接\n"
                    "论文来源：https://arxiv.org/abs/2607.12345"
                ),
                "tool_calls": [],
            }
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "审查结论：通过。", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class ParallelBackend(NoToolBackend):
    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "needs parallel work", '
                    '"main_task": "", "assignments": {"research": "research branch", '
                    '"engineering": "engineering branch", "multimodal": ""}}'
                ),
                "tool_calls": [],
            }
        if "Research Agent" in system:
            time.sleep(0.3)
            return {"content": "research finished", "tool_calls": []}
        if "Engineering Agent" in system:
            time.sleep(0.3)
            return {"content": "engineering finished", "tool_calls": []}
        if "coordinator" in system:
            return {"content": "parallel synthesis", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed.", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class SameRoleFanoutBackend(NoToolBackend):
    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        user = str(messages[-1].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "fan out same role", '
                    '"main_task": "", "assignments": {"research": ["inspect alpha", "inspect beta"], '
                    '"engineering": "", "multimodal": ""}}'
                ),
                "tool_calls": [],
            }
        if "coordinator" in system:
            return {"content": "same-role synthesis", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed.", "tool_calls": []}
        if "Research Agent" in system:
            time.sleep(0.3)
            if "inspect alpha" in user:
                return {"content": "alpha done", "tool_calls": []}
            if "inspect beta" in user:
                return {"content": "beta done", "tool_calls": []}
            return {"content": "research done", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class FlatSubagentBackend(NoToolBackend):
    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        user = str(messages[-1].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "flat dispatch", "main_task": "", '
                    '"subagents": ['
                    '{"id": "alpha-reader", "role": "research", "task": "inspect alpha only"}, '
                    '{"id": "beta-reader", "role": "research", "task": "inspect beta only"}'
                    ']}'
                ),
                "tool_calls": [],
            }
        if "coordinator" in system:
            return {"content": "flat synthesis", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed.", "tool_calls": []}
        if "Research Agent" in system:
            time.sleep(0.3)
            if "inspect alpha only" in user:
                return {"content": "alpha flat done", "tool_calls": []}
            if "inspect beta only" in user:
                return {"content": "beta flat done", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class DirectDispatchToolBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.saw_dispatch_tool = False
        self.dispatched = False

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        user = str(messages[-1].get("content", "")) if messages else ""
        tool_names = {tool["function"]["name"] for tool in tools or []}
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": false, "reason": "let main dispatch dynamically", '
                    '"main_task": "dispatch dynamically", '
                    '"assignments": {"research": "", "engineering": "", "multimodal": ""}}'
                ),
                "tool_calls": [],
            }
        if "Engineering Agent" in system:
            time.sleep(0.3)
            if "alpha" in user:
                return {"content": "alpha via dispatch", "tool_calls": []}
            if "beta" in user:
                return {"content": "beta via dispatch", "tool_calls": []}
            return {"content": "engineering via dispatch", "tool_calls": []}
        if "subagent_dispatch" in tool_names:
            self.saw_dispatch_tool = "subagent_dispatch" in tool_names
            if not self.dispatched:
                self.dispatched = True
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "dispatch-1",
                        "name": "subagent_dispatch",
                        "arguments": {
                            "reason": "parallel independent checks",
                            "subagents": [
                                {"id": "alpha-check", "role": "engineering", "task": "inspect alpha"},
                                {"id": "beta-check", "role": "engineering", "task": "inspect beta"},
                            ],
                        },
                    }],
                }
            return {"content": "main synthesized dispatch results", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


class LocalPathMultimodalBackend(NoToolBackend):
    def __init__(self) -> None:
        super().__init__()
        self.multimodal_ran = False

    def chat(self, messages, tools=None):
        system = str(messages[0].get("content", "")) if messages else ""
        if "JSON" in system:
            return {
                "content": (
                    '{"use_subagents": true, "reason": "local image path", "main_task": "", '
                    '"subagents": ['
                    '{"id": "figure-local", "role": "multimodal", '
                    '"task": "analyze local image path demo_project/images/marker-001.jpeg"}'
                    ']}'
                ),
                "tool_calls": [],
            }
        lowered_system = system.lower()
        if "multimodal agent" in lowered_system:
            self.multimodal_ran = True
            return {"content": "local path multimodal done", "tool_calls": []}
        if "coordinator" in lowered_system:
            return {"content": "local path synthesis", "tool_calls": []}
        if "Reviewer" in system or "Reviewer" in str(messages):
            return {"content": "review passed.", "tool_calls": []}
        return {"content": "ok", "tool_calls": []}


def test_subagent_todo_paths_are_role_isolated():
    assert _agent_todo_path("parent/run", "research") != _agent_todo_path("parent/run", "engineering")
    assert ".mini-openclaw/subagents/parent-run/research/tasks.json" == _agent_todo_path("parent/run", "research")


def test_subagents_run_in_parallel(tmp_path: Path):
    backend = ParallelBackend()
    events = []

    started = time.monotonic()
    answer = run_multi_agent(
        task="parallel test",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        event_callback=lambda event, payload: events.append((event, payload)),
    )
    elapsed = time.monotonic() - started

    assert answer == "parallel synthesis"
    assert elapsed < 0.55
    assert [payload["role"] for event, payload in events if event == "subagent_start"] == [
        "Research Agent",
        "Engineering Agent",
    ]


def test_same_role_assignments_fan_out_to_parallel_subagents(tmp_path: Path):
    backend = SameRoleFanoutBackend()
    events = []

    started = time.monotonic()
    answer = run_multi_agent(
        task="fan out work",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        event_callback=lambda event, payload: events.append((event, payload)),
    )
    elapsed = time.monotonic() - started

    assert answer == "same-role synthesis"
    assert elapsed < 0.55
    assert [payload["role"] for event, payload in events if event == "subagent_start"] == [
        "Research Agent 1",
        "Research Agent 2",
    ]
    assert (tmp_path / "subagents" / "trace.research-1.jsonl").exists()
    assert (tmp_path / "subagents" / "trace.research-2.jsonl").exists()


def test_flat_subagent_list_uses_role_as_attribute_not_bucket(tmp_path: Path):
    backend = FlatSubagentBackend()
    events = []

    started = time.monotonic()
    answer = run_multi_agent(
        task="flat dispatch work",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        event_callback=lambda event, payload: events.append((event, payload)),
    )
    elapsed = time.monotonic() - started

    assert answer == "flat synthesis"
    assert elapsed < 0.55
    starts = [payload for event, payload in events if event == "subagent_start"]
    assert [payload["subagent_id"] for payload in starts] == ["alpha-reader", "beta-reader"]
    assert [payload["role"] for payload in starts] == ["Research Agent 1", "Research Agent 2"]
    assert (tmp_path / "subagents" / "trace.alpha-reader.jsonl").exists()
    assert (tmp_path / "subagents" / "trace.beta-reader.jsonl").exists()


def test_main_agent_can_dispatch_parallel_subagents_as_tool(tmp_path: Path):
    backend = DirectDispatchToolBackend()
    events = []
    trace = tmp_path / "trace.jsonl"

    started = time.monotonic()
    answer = run_multi_agent(
        task="dispatch from main",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
        event_callback=lambda event, payload: events.append((event, payload)),
    )
    elapsed = time.monotonic() - started

    assert answer == "main synthesized dispatch results"
    assert backend.saw_dispatch_tool is True
    assert elapsed < 0.55
    starts = [payload for event, payload in events if event == "subagent_start"]
    assert [payload["subagent_id"] for payload in starts] == ["alpha-check", "beta-check"]
    assert '"event": "subagent_dispatch"' in trace.read_text(encoding="utf-8")
    assert (tmp_path / "subagents" / "trace.alpha-check.jsonl").exists()
    assert (tmp_path / "subagents" / "trace.beta-check.jsonl").exists()


def test_local_path_multimodal_role_is_not_demoted_to_research(tmp_path: Path):
    backend = LocalPathMultimodalBackend()

    answer = run_multi_agent(
        task="analyze local image path",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
    )

    assert answer == "local path synthesis"
    assert backend.multimodal_ran is True
    assert (tmp_path / "subagents" / "trace.figure-local.jsonl").exists()


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


def test_direct_main_agent_keeps_original_research_delivery_requirements(tmp_path: Path):
    backend = DirectResearchBackend()
    trace = tmp_path / "trace.jsonl"

    answer = run_multi_agent(
        task="做一个多模态压缩论文调研",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
    )

    assert backend.answer_turn == 2
    assert "https://arxiv.org/abs/2607.12345" in answer
    assert "insufficient_research_answer" in (tmp_path / "subagents" / "trace.main.jsonl").read_text(encoding="utf-8")


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


def test_multi_agent_emits_live_progress_events(tmp_path: Path):
    backend = AssignmentBackend()
    events = []

    run_multi_agent(
        task="分析论文并检查测试",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        event_callback=lambda event, payload: events.append((event, payload)),
    )

    event_names = [event for event, _payload in events]
    assert "orchestration" in event_names
    assert ("subagent_start", {
        "role": "Research Agent",
        "assignment": "只阅读论文第 3 节并总结模型结构",
    }) in events
    assert ("subagent_done", {"role": "Research Agent"}) in events
    assert ("subagent_start", {
        "role": "Engineering Agent",
        "assignment": "只检查 tests/test_subagents.py 是否覆盖调度",
    }) in events
    assert "synthesis_start" in event_names
    assert "review_start" in event_names
    assert "review_done" in event_names


def test_noop_assignments_are_not_started_and_planner_gets_runtime_date(tmp_path: Path):
    backend = NoOpAssignmentBackend()
    events = []
    system = (
        "system\n\n# 运行时日期\n"
        "当前本地日期为 2026-07-14，时区为 Asia/Shanghai. "
        "用户说‘最近一周’时，默认使用 2026-07-07 至 2026-07-14 的明确范围。\n"
        "处理最新/近期论文时必须核验论文页面的发布日期或最近更新日期；"
        "不得用旧年份搜索结果冒充近期结果。"
    )

    answer = run_multi_agent(
        task="找最近一周多模态模型压缩的新论文",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt=system,
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        event_callback=lambda event, payload: events.append((event, payload)),
    )

    assert "https://arxiv.org/abs/2607.12345" in answer
    assert "当前本地日期为 2026-07-14" in backend.orchestration_user_message
    assert backend.research_ran is True
    assert backend.engineering_ran is False
    assert backend.multimodal_ran is False
    started_roles = [payload.get("role") for event, payload in events if event == "subagent_start"]
    assert started_roles == ["Research Agent"]
    orchestration_events = [payload for event, payload in events if event == "orchestration"]
    assert orchestration_events[0]["assignments"]["engineering"] == ""
    assert orchestration_events[0]["assignments"]["multimodal"] == ""

def test_engineering_agent_receives_full_tool_registry(tmp_path: Path):
    backend = EngineeringToolBackend()
    registry = ToolRegistry()
    registry.register(Tool(
        name="wechat_file_transfer",
        description="send wechat",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        run=lambda message: f"sent: {message}",
    ))

    run_multi_agent(
        task="让工程子 agent 发送微信",
        backend=backend,
        registry=registry,
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
    )

    assert backend.sent_tool_call is True


def test_research_agent_receives_full_tool_registry_including_bash(tmp_path: Path):
    backend = ResearchBashBackend()
    registry = ToolRegistry()
    registry.register(Tool(
        name="bash",
        description="run shell",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        run=lambda command: f"ran: {command}",
    ))

    run_multi_agent(
        task="let the research subagent inspect the local environment",
        backend=backend,
        registry=registry,
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        permission_mode="auto-local",
    )

    assert backend.saw_bash is True
    assert backend.sent_tool_call is True
    assert tmp_path.as_posix() in backend.research_message.replace("\\", "/")
    assert "不要假设存在 `/workspace`" in backend.research_message


def test_multimodal_agent_receives_full_tool_registry_including_bash(tmp_path: Path):
    image_path = tmp_path / "figure.png"
    Image.new("RGB", (1, 1), "white").save(image_path)
    backend = MultimodalBashBackend()
    registry = ToolRegistry()
    registry.register(Tool(
        name="bash",
        description="run shell",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        run=lambda command: f"ran: {command}",
    ))

    run_multi_agent(
        task="let the multimodal subagent inspect the image and local environment",
        backend=backend,
        vision_backend=backend,
        registry=registry,
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        image_paths=[str(image_path)],
        permission_mode="auto-local",
    )

    assert backend.saw_image is True
    assert backend.saw_bash is True
    assert backend.sent_tool_call is True


def test_multi_agent_repairs_research_answer_when_synthesis_drops_links(tmp_path: Path):
    backend = LinkRepairBackend()
    trace = tmp_path / "trace.jsonl"

    answer = run_multi_agent(
        task="找一篇多模态压缩论文并总结",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
    )

    assert backend.synthesis_calls == 2
    assert "https://arxiv.org/abs/2607.12345" in answer
    log = trace.read_text(encoding="utf-8")
    assert '"reason": "insufficient_research_answer"' in log


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


def test_parallel_subagent_confirmations_are_serialized(tmp_path: Path):
    backend = ParallelConfirmBackend()
    registry = ToolRegistry()
    registry.register(Tool(
        name="bash",
        description="run shell",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        run=lambda command: f"ran {command}",
    ))
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    confirmations = 0

    def confirm(_name, _arguments, _decision):
        nonlocal active, max_active, confirmations
        with state_lock:
            confirmations += 1
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.1)
        with state_lock:
            active -= 1
        return ConfirmationResponse(True)

    run_multi_agent(
        task="run two independent shell checks",
        backend=backend,
        registry=registry,
        system_prompt="system",
        workdir=tmp_path,
        trace_path=tmp_path / "trace.jsonl",
        parent_run_id="parent",
        confirm_callback=confirm,
    )

    assert confirmations == 2
    assert max_active == 1


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


def test_multi_agent_delivers_user_answer_when_final_review_still_requests_changes(tmp_path: Path):
    backend = FinalReviewRejectBackend()
    trace = tmp_path / "trace.jsonl"

    answer = run_multi_agent(
        task="run an experiment and report results",
        backend=backend,
        registry=ToolRegistry(),
        system_prompt="system",
        workdir=tmp_path,
        trace_path=trace,
        parent_run_id="parent",
    )

    assert backend.review_calls == 2
    assert backend.final_delivery_calls == 1
    assert "unsupported final answer" not in answer
    assert "实验结果" in answer
    assert "key result has no evidence" in answer
    assert "当前还不能把这次任务报告为已完成" not in answer
    assert "最终质量审查仍要求修订" not in answer
    assert "审查结论" not in answer
    log = trace.read_text(encoding="utf-8")
    assert '"phase": "final"' in log
    assert '"status": "needs_revision"' in log
    assert '"event": "final_delivery_repair"' in log
    assert "审查结论：需修订" in log
