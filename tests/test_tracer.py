import json
from pathlib import Path

from agent.loop import AgentLoop
from agent.tracer import Tracer
from eval.trace_cli import main as trace_cli_main
from eval.trace_report import cost_report, diagnose, render_html, render_terminal, simulate, spans_from_records, summarize
from tools.base import Tool, ToolRegistry


def test_span_trace_links_llm_and_tool_and_redacts_workdir(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"
    tracer = Tracer(trace_path)
    tracer.start_run(task=f"read {tmp_path}/secret.txt", workdir=tmp_path)
    with tracer.span("llm", "decide", input_value="Bearer top-secret") as span:
        span.finish(usage={"prompt_tokens": 12, "completion_tokens": 3}, output="sk-abcdefghijk")
    tracer.finish_run(status="success")

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    spans = spans_from_records(records)
    llm = next(item for item in spans if item["kind"] == "llm")
    root = next(item for item in spans if item["kind"] == "agent")
    assert llm["parent_span_id"] == root["span_id"]
    assert "[REDACTED]" in llm["input_preview"]
    assert "[REDACTED]" in llm["output_preview"]
    assert str(tmp_path) not in trace_path.read_text(encoding="utf-8")


def test_agent_loop_records_correlated_llm_and_tool_spans(tmp_path: Path):
    class Backend:
        supports_tools = True

        def __init__(self):
            self.turn = 0

        def chat(self, messages, tools=None):
            self.turn += 1
            if self.turn == 1:
                return {"content": "", "tool_calls": [{"id": "call-echo", "name": "echo", "arguments": {"text": "ok"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
            return {"content": "done", "tool_calls": [], "usage": {"prompt_tokens": 7, "completion_tokens": 1}}

    registry = ToolRegistry()
    registry.register(Tool("echo", "", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, lambda text: text))
    trace_path = tmp_path / "loop.jsonl"
    loop = AgentLoop(Backend(), registry, "system", workdir=tmp_path, auto_approve=True, tracer=Tracer(trace_path))
    assert loop.run("test trace") == "done"

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    spans = spans_from_records(records)
    tool = next(item for item in spans if item["kind"] == "tool")
    assert tool["attributes"]["tool_call_id"] == "call-echo"
    assert tool["status"] == "ok"
    summary = summarize(trace_path)
    assert summary["spans"] >= 4
    assert summary["prefix_cache"]["available"] is True
    assert summary["prefix_cache"]["adjacent_match_ratio"] == 1.0


def test_trace_renderers_are_read_only_and_escape_content(tmp_path: Path):
    trace_path = tmp_path / "render.jsonl"
    tracer = Tracer(trace_path)
    tracer.start_run(task="render", workdir=tmp_path)
    with tracer.span("tool", "<unsafe>", input_value="<script>alert(1)</script>") as span:
        span.finish(output="ok")
    tracer.finish_run(status="success")

    terminal = render_terminal(trace_path, details=True)
    html = render_html(trace_path)
    output = tmp_path / "trace.html"
    assert "TOOL" in terminal and "<unsafe>" in terminal
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert trace_cli_main(["render", str(trace_path), "--format", "html", "--output", str(output)]) == 0
    assert output.is_file()


def test_cost_and_wall_time_include_tools_and_identify_expensive_span(tmp_path: Path, monkeypatch):
    trace_path = tmp_path / "legacy.jsonl"
    events = [
        {"event": "run_start", "ts": 10.0},
        {"event": "step", "step": 1, "success": True, "duration_ms": 1000, "prompt_tokens": 100, "completion_tokens": 20, "tool_calls": []},
        {"event": "tool_result", "step": 1, "tool": "slow_tool", "success": True, "duration_ms": 5000, "arguments": {}, "observation": "ok"},
        {"event": "run_end", "ts": 18.0, "status": "success"},
    ]
    trace_path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("OPENCLAW_OUTPUT_USD_PER_MILLION", "4")

    summary = summarize(trace_path)
    report = cost_report(trace_path)

    assert summary["duration_ms"] == 8000
    assert summary["tool_duration_ms"] == 5000
    assert summary["slowest_span"]["name"] == "slow_tool"
    assert summary["estimated_cost_usd"] == 0.00028
    assert summary["priciest_span"]["name"] == "decide"
    assert report["pricing"]["status"] == "estimated"


def test_mock_simulation_and_diagnostics_never_execute_tools(tmp_path: Path):
    trace_path = tmp_path / "diagnose.jsonl"
    events = [
        {"event": "run_start", "ts": 1.0},
        {"event": "step", "step": 1, "success": True, "duration_ms": 5, "prompt_tokens": 100, "completion_tokens": 1,
         "tool_calls": [{"id": "call-1", "name": "read", "arguments": {"path": "a.txt"}}]},
        {"event": "tool_result", "step": 1, "tool_call_id": "call-1", "tool": "read", "success": True, "duration_ms": 31_000, "arguments": {"path": "a.txt"}, "observation": "ok"},
        {"event": "step", "step": 2, "success": True, "duration_ms": 5, "prompt_tokens": 200, "completion_tokens": 1, "tool_calls": []},
        {"event": "step", "step": 3, "success": True, "duration_ms": 5, "prompt_tokens": 300, "completion_tokens": 1, "tool_calls": []},
        {"event": "run_end", "ts": 2.0, "status": "success"},
    ]
    trace_path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

    replay = simulate(trace_path)
    report = diagnose(trace_path, slow_ms=30_000)

    assert replay["side_effects"] is False
    assert replay["tool_calls_reexecuted"] == 0
    assert replay["issues"] == []
    kinds = {item["kind"] for item in report["findings"]}
    assert {"slow_span", "prompt_growth", "duration_inconsistency"} <= kinds


def test_trace_metadata_links_scheduled_runs_without_absolute_paths(tmp_path: Path):
    trace_path = tmp_path / "scheduled.jsonl"
    tracer = Tracer(trace_path, metadata={"schedule_id": "weekly", "scheduled_run_id": "weekly-001", "schedule_workdir": "demo_project"})
    tracer.start_run(task="scheduled task", workdir=tmp_path)
    tracer.finish_run(status="success")

    spans = spans_from_records([json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()])
    root = next(item for item in spans if item["kind"] == "agent")
    assert root["attributes"]["schedule_id"] == "weekly"
    assert root["attributes"]["schedule_workdir"] == "demo_project"
    assert str(tmp_path) not in trace_path.read_text(encoding="utf-8")


def test_late_context_does_not_mutate_stable_system_prefix(tmp_path: Path):
    class Backend:
        supports_tools = True

        def chat(self, messages, tools=None):
            return {"content": "done", "tool_calls": []}

    loop = AgentLoop(Backend(), ToolRegistry(), "stable system", workdir=tmp_path)
    assert loop.run("first") == "done"
    frozen_system = loop.messages[0]["content"]
    loop.add_context("late-skill", "dynamic local context")

    assert loop.messages[0]["content"] == frozen_system
    assert loop.messages[-1]["role"] == "user"
    assert "dynamic local context" in loop.messages[-1]["content"]
