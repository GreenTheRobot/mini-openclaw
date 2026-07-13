"""在隔离目录运行真实 CLI 任务并输出评测/消融 CSV 与报告。"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS = ROOT / "eval" / "research_tasks.json"


def evaluate(task: dict[str, Any], workspace: Path, output: str, returncode: int) -> tuple[bool, str]:
    failures = []
    if returncode != 0:
        failures.append(f"returncode={returncode}")
    expected_output = task.get("expected_output", [])
    if expected_output and not any(token.lower() in output.lower() for token in expected_output):
        failures.append("missing_expected_output")
    for relative, expected in task.get("expected_files", {}).items():
        path = workspace / relative
        if not path.exists() or expected not in path.read_text(encoding="utf-8", errors="replace"):
            failures.append(f"bad_file:{relative}")
    return not failures, ",".join(failures)


def run_task(task: dict[str, Any], variant: str, timeout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mini-openclaw-eval-") as raw_workspace:
        workspace = Path(raw_workspace)
        for relative, content in task.get("fixtures", {}).items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        trace = workspace / "trace.jsonl"
        command = [
            sys.executable, "-m", "agent.cli", task["prompt"],
            "--auto-approve", "--no-mcp", "--trace", str(trace),
            "--ablation", variant,
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command, cwd=workspace, env=environment, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
            output = completed.stdout + completed.stderr
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            returncode = 124
        duration = round(time.perf_counter() - started, 3)
        success, reason = evaluate(task, workspace, output, returncode)
        trace_events = 0
        tool_calls = 0
        prompt_tokens = completion_tokens = 0
        if trace.exists():
            for line in trace.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                trace_events += 1
                if event.get("event") == "step":
                    tool_calls += len(event.get("tool_calls", []))
                    prompt_tokens += event.get("prompt_tokens", 0)
                    completion_tokens += event.get("completion_tokens", 0)
        return {
            "task": task["name"], "variant": variant, "success": success,
            "reason": reason, "returncode": returncode,
            "duration_seconds": duration, "trace_events": trace_events,
            "tool_calls": tool_calls, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }


def write_report(rows: list[dict[str, Any]], path: Path) -> None:
    variants = sorted({row["variant"] for row in rows})
    lines = ["# 真实任务消融实验", "", "| 变体 | 成功率 | 平均工具调用 | 平均 Token | 平均耗时(s) |", "| --- | ---: | ---: | ---: | ---: |"]
    for variant in variants:
        group = [row for row in rows if row["variant"] == variant]
        n = max(len(group), 1)
        lines.append(
            f"| {variant} | {sum(bool(row['success']) for row in group) / n:.2%} | "
            f"{sum(row['tool_calls'] for row in group) / n:.1f} | "
            f"{sum(row['prompt_tokens'] + row['completion_tokens'] for row in group) / n:.0f} | "
            f"{sum(row['duration_seconds'] for row in group) / n:.2f} |"
        )
    lines.extend(["", "结论必须依据上表真实数据填写，不得在运行前预设。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--variants", nargs="+", default=["none", "no-planning", "no-memory"])
    parser.add_argument("--output", type=Path, default=ROOT / "eval" / "results.csv")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("缺少 DEEPSEEK_API_KEY；真实评测不会使用 FakeBackend 伪造数据。", file=sys.stderr)
        return 2
    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    rows = [run_task(task, variant, args.timeout) for variant in args.variants for task in tasks]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    write_report(rows, args.output.with_name("ablation-report.md"))
    print(f"wrote {args.output} and {args.output.with_name('ablation-report.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())