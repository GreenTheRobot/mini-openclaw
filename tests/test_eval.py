from pathlib import Path

import json

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
