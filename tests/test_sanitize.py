import json
from pathlib import Path

from agent.loop import AgentLoop
from agent.sanitize import clean_text, sanitize_for_json
from backend.client import DeepSeekBackend
from eval.tracer import Tracer
from tools.base import ToolRegistry


def test_clean_text_replaces_lone_surrogates() -> None:
    text = "before " + chr(0xDCE5) + " after"
    cleaned = clean_text(text)
    assert "\udce5" not in cleaned
    cleaned.encode("utf-8")


def test_tracer_logs_payloads_with_lone_surrogates(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    tracer = Tracer(trace)
    tracer.log_event("bad", value="x" + chr(0xDCE5))

    record = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert record["value"] == "x\ufffd"


def test_agent_loop_cleans_backend_response_before_history_and_next_turn(tmp_path: Path) -> None:
    class BadBackend:
        supports_tools = True

        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None):
            self.calls += 1
            for message in messages:
                sanitize_for_json(message)
                json.dumps(message, ensure_ascii=False).encode("utf-8")
            return {
                "role": "assistant",
                "content": "answer " + chr(0xDCE5),
                "tool_calls": [],
            }

    loop = AgentLoop(BadBackend(), ToolRegistry(), "system", workdir=tmp_path, tracer=Tracer(tmp_path / "t.jsonl"))
    assert loop.run("hello") == "answer \ufffd"
    assert loop.run("again") == "answer \ufffd"


def test_deepseek_message_conversion_cleans_lone_surrogates() -> None:
    backend = DeepSeekBackend.__new__(DeepSeekBackend)
    messages = [{"role": "user", "content": "hello " + chr(0xDCE5)}]

    converted = backend._to_openai_messages(sanitize_for_json(messages))
    json.dumps(converted, ensure_ascii=False).encode("utf-8")
    assert converted[0]["content"] == "hello \ufffd"
