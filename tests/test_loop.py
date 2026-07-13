from pathlib import Path

from agent.loop import AgentLoop
from eval.tracer import Tracer, summarize
from tools.base import Tool, ToolRegistry, ToolResult


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