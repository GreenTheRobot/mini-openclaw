from pathlib import Path

import json

from agent.tracer import Tracer
from eval.trace_cli import main as trace_cli_main
from eval.trace_report import cost_report
from eval.tracer import summarize
from eval.run_suite import evaluate, write_report


def test_evaluate_checks_output_and_files(tmp_path: Path):
    (tmp_path / "x.py").write_text("hello research", encoding="utf-8")
    task = {"expected_output": ["完成"], "expected_files": {"x.py": "hello research"}}
    assert evaluate(task, tmp_path, "已经完成", 0) == (True, "")


def test_write_report_uses_real_rows(tmp_path: Path):
    rows = [
        {"variant": "none", "success": True, "tool_calls": 2, "prompt_tokens": 10, "completion_tokens": 5, "duration_seconds": 1},
        {"variant": "no-memory", "success": False, "tool_calls": 1, "prompt_tokens": 8, "completion_tokens": 4, "duration_seconds": 2},
    ]
    path = tmp_path / "report.md"
    write_report(rows, path)
    text = path.read_text(encoding="utf-8")
    assert "100.00%" in text and "0.00%" in text


def test_trace_summary_can_include_subagent_traces(tmp_path: Path):
    trace = tmp_path / "session.jsonl"
    subdir = tmp_path / "subagents"
    subdir.mkdir()
    trace.write_text(json.dumps({"event": "step", "tool_calls": []}) + "\n", encoding="utf-8")
    (subdir / "session.research.jsonl").write_text(
        json.dumps({"event": "step", "tool_calls": [{"name": "read"}], "prompt_tokens": 4, "completion_tokens": 2}) + "\n"
        + json.dumps({"event": "tool_result", "success": True}) + "\n",
        encoding="utf-8",
    )

    summary = summarize(trace, include_children=True)

    assert summary["trace_files"] == 2
    assert summary["steps"] == 2
    assert summary["tool_calls"] == 1


def test_trace_cost_can_include_subagent_traces(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENCLAW_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("OPENCLAW_OUTPUT_USD_PER_MILLION", raising=False)
    trace = tmp_path / "session.jsonl"
    subdir = tmp_path / "subagents"
    subdir.mkdir()
    trace.write_text("", encoding="utf-8")

    tracer = Tracer(subdir / "session.research.jsonl")
    tracer.start_run(task="research", workdir=tmp_path)
    with tracer.span("llm", "decide", attributes={"model": "deepseek-v4-flash"}) as span:
        span.finish(usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    tracer.finish_run(status="success")

    local = cost_report(trace)
    combined = cost_report(trace, include_children=True)

    assert local["summary"]["steps"] == 0
    assert combined["summary"]["trace_files"] == 2
    assert combined["summary"]["steps"] == 1
    assert combined["summary"]["estimated_cost_usd"] == 0.42

    assert trace_cli_main(["cost", str(trace)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["trace_files"] == 2
    assert output["summary"]["estimated_cost_usd"] == 0.42


def test_trace_replay_can_include_subagent_traces(tmp_path: Path, capsys):
    trace = tmp_path / "session.jsonl"
    subdir = tmp_path / "subagents"
    subdir.mkdir()
    trace.write_text("", encoding="utf-8")

    tracer = Tracer(subdir / "session.research.jsonl")
    tracer.start_run(task="research", workdir=tmp_path)
    with tracer.span("llm", "decide", attributes={"model": "deepseek-v4-flash"}) as span:
        span.finish(usage={"prompt_tokens": 12, "completion_tokens": 3})
    tracer.finish_run(status="success")

    assert trace_cli_main(["replay", str(trace)]) == 0
    output = capsys.readouterr().out
    assert "DECIDE" in output.upper()
    assert '"trace_files": 2' in output

    assert trace_cli_main(["replay", str(trace), "--no-children"]) == 0
    output = capsys.readouterr().out
    assert "DECIDE" not in output.upper()
    assert '"trace_files": 1' in output
