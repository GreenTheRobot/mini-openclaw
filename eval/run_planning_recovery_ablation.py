"""Evaluate TODO-backed recovery after a deliberately interrupted CLI session.

The two arms use the same fixture, model, first-session instruction, second-session
instruction and persistent work directory.  The treatment is whether the first
session can create and preserve the explicit TODO state that the new session sees.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from eval.run_core_ablations import _run_trace_commands, _write_fixture
from eval.trace_report import load_records, spans_from_records, summarize


ROOT = Path(__file__).resolve().parents[1]
TASKS = [
    "阅读 README.md、docs/spec.md 和 config/release.json，完成准备并保留后续待办",
    "修复 src/normalizer.py：normalize 必须 strip 后转小写",
    "修复 src/scorer.py：score 必须返回预测与真值的准确率",
    "运行 python scripts/run_pipeline.py；依据真实输出写 artifacts/final_report.md",
    "运行 python scripts/audit.py；确认审计通过后完成所有 TODO",
]
PHASE_ONE = (
    "这是一个会被中断的复杂工程任务的第一会话。先调用 todo_write，并且必须使用以下五条有序 TODO：\n"
    + "\n".join(f"{index}. {item}" for index, item in enumerate(TASKS, 1))
    + "\n本会话只完成第 1 项准备工作，并将其标为 completed；第 2—5 项必须保持 pending。"
    "不要修改 src/，不要运行 scripts/，不要创建 artifacts/final_report.md。"
    "只在 handoff.md 写入“准备完成；请在新会话根据已保存 TODO 继续。”然后停止。不要使用网络。"
)
PHASE_TWO = "这是同一工作目录的一个新会话。继续完成已保存 TODO 中仍未完成的工作；不要把 pending 任务当作完成。不要使用网络。"
FIXTURE = {
    "README.md": "# Recovery fixture\n本项目需要在准备完成后恢复代码修复、运行验证、报告写入和审计。\n",
    "docs/spec.md": "# 规范\n数据集为 demo-v2，seed 为 17；最终报告必须记录 score=1.00、status=verified 和两个代码路径。\n",
    "config/release.json": '{"dataset": "demo-v2", "seed": 17}\n',
    "src/normalizer.py": "def normalize(value):\n    # TODO\n    return value\n",
    "src/scorer.py": "def score(predictions, truth):\n    # TODO\n    return 0.0\n",
    "scripts/run_pipeline.py": (
        "import json\nimport sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from src.normalizer import normalize\nfrom src.scorer import score\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "config = json.loads((root / 'config/release.json').read_text())\n"
        "values = [normalize(value) for value in [' Alpha ', 'Beta']]\n"
        "value = score(values, ['alpha', 'beta'])\n"
        "if values != ['alpha', 'beta'] or value != 1.0: raise SystemExit('pipeline=failed')\n"
        "print(f'pipeline=complete dataset={config[\"dataset\"]} seed={config[\"seed\"]} score={value:.2f} status=verified')\n"
    ),
    "scripts/audit.py": (
        "from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\n"
        "report = root / 'artifacts/final_report.md'\n"
        "required = ['dataset=demo-v2', 'seed=17', 'score=1.00', 'status=verified', 'src/normalizer.py', 'src/scorer.py']\n"
        "if not report.exists(): raise SystemExit('audit=failed missing_report')\n"
        "missing = [item for item in required if item not in report.read_text()]\n"
        "if missing: raise SystemExit('audit=failed missing=' + ','.join(missing))\n"
        "print('audit=pass')\n"
    ),
}


def _run(command: list[str], *, workspace: Path, env: dict[str, str], log_path: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True, timeout=300, check=False)
    log_path.write_text((completed.stdout + "\n--- STDERR ---\n" + completed.stderr).strip() + "\n", encoding="utf-8")
    return completed


def _tool_outputs(trace: Path) -> str:
    if not trace.exists():
        return ""
    return "\n".join(
        str(span.get("output_preview", ""))
        for span in spans_from_records(load_records(trace))
        if span.get("kind") == "tool" and span.get("status") == "ok"
    )


def _evaluate(workspace: Path, phase_two_trace: Path, *, requires_todo_closure: bool) -> tuple[bool, str]:
    report = workspace / "artifacts/final_report.md"
    required = ("dataset=demo-v2", "seed=17", "score=1.00", "status=verified", "src/normalizer.py", "src/scorer.py")
    if not report.exists():
        return False, "新会话没有生成最终报告"
    missing = [item for item in required if item not in report.read_text(encoding="utf-8")]
    if missing:
        return False, f"最终报告遗漏：{', '.join(missing)}"
    outputs = _tool_outputs(phase_two_trace)
    if not all(marker in outputs for marker in ("pipeline=complete", "score=1.00", "audit=pass")):
        return False, "新会话 trace 未记录流水线与审计成功输出"
    if requires_todo_closure:
        todo_path = workspace / ".mini-openclaw" / "recovery-tasks.json"
        if not todo_path.exists():
            return False, "完整规划条件缺少持久化 TODO 文件"
        tasks = json.loads(todo_path.read_text(encoding="utf-8")).get("items", [])
        if len(tasks) != 5 or any(item.get("status") != "completed" for item in tasks):
            return False, "恢复后 TODO 未全部闭环"
    return True, "新会话完成恢复、验证、报告和审计"


def _metrics(traces: list[Path]) -> dict[str, float | int]:
    summaries = [summarize(trace) for trace in traces if trace.exists()]
    return {
        "tool_calls": sum(int(item.get("tool_calls", 0)) for item in summaries),
        "total_tokens": sum(int(item.get("total_tokens", 0)) for item in summaries),
        "duration_ms": sum(float(item.get("duration_ms", 0)) for item in summaries),
        "errors": sum(int(item.get("errors", 0)) for item in summaries),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats 必须至少为 1")

    artifact_root = ROOT / "eval" / "ablation_artifacts" / f"planning-recovery-{args.run_id}"
    trace_root = ROOT / "traces" / "ablations" / f"planning-recovery-{args.run_id}"
    artifact_root.mkdir(parents=True, exist_ok=False)
    trace_root.mkdir(parents=True, exist_ok=False)
    rows: list[dict] = []
    for variant, ablation in (("none", "none"), ("no-planning", "no-planning")):
        for repetition in range(1, args.repeats + 1):
            run_name = f"planning-recovery-{variant}-r{repetition}"
            result_dir = artifact_root / "runs" / run_name
            workspace = result_dir / "workspace"
            workspace.mkdir(parents=True)
            _write_fixture(workspace, FIXTURE)
            phase_one_trace = trace_root / f"{run_name}-phase1.jsonl"
            phase_two_trace = trace_root / f"{run_name}-phase2.jsonl"
            environment = {**os.environ, "PYTHONPATH": str(ROOT), "MINI_OPENCLAW_TODO_PATH": ".mini-openclaw/recovery-tasks.json"}
            common = ["--auto-approve", "--no-mcp", "--no-multi-agent", "--ablation", ablation]
            print(f"RUN {run_name} phase1", flush=True)
            first = _run([sys.executable, "-m", "agent.cli", PHASE_ONE, "--trace", str(phase_one_trace), *common], workspace=workspace, env=environment, log_path=result_dir / "phase1.log")
            print(f"RUN {run_name} phase2", flush=True)
            second = _run([sys.executable, "-m", "agent.cli", PHASE_TWO, "--trace", str(phase_two_trace), *common], workspace=workspace, env=environment, log_path=result_dir / "phase2.log")
            for trace in (phase_one_trace, phase_two_trace):
                if trace.exists():
                    trace_artifact_dir = result_dir / trace.stem
                    trace_artifact_dir.mkdir(parents=True, exist_ok=True)
                    _run_trace_commands(trace, trace_artifact_dir)
            passed, reason = _evaluate(workspace, phase_two_trace, requires_todo_closure=variant == "none")
            if first.returncode != 0 or second.returncode != 0:
                passed = False
                reason = f"CLI 退出码 phase1={first.returncode}, phase2={second.returncode}；{reason}"
            rows.append({
                "experiment": "A4. TODO 中断恢复", "variant": variant, "repetition": repetition,
                "success": passed, "reason": reason, "phase1_trace": str(phase_one_trace.relative_to(ROOT)),
                "phase2_trace": str(phase_two_trace.relative_to(ROOT)), "artifact": str(result_dir.relative_to(ROOT)),
                "metrics": _metrics([phase_one_trace, phase_two_trace]),
            })
            print(f"  {'PASS' if passed else 'FAIL'} | {reason}", flush=True)
    aggregate = []
    for variant in ("none", "no-planning"):
        group = [row for row in rows if row["variant"] == variant]
        aggregate.append({
            "variant": variant, "n": len(group),
            "success_rate": round(sum(bool(row["success"]) for row in group) / len(group), 2),
            "avg_tool_calls": round(sum(row["metrics"]["tool_calls"] for row in group) / len(group), 2),
            "avg_total_tokens": round(sum(row["metrics"]["total_tokens"] for row in group) / len(group), 2),
            "avg_duration_ms": round(sum(row["metrics"]["duration_ms"] for row in group) / len(group), 2),
            "total_trace_errors": sum(row["metrics"]["errors"] for row in group),
        })
    payload = {"run_id": args.run_id, "repeats": args.repeats, "rows": rows, "aggregate": aggregate}
    (artifact_root / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (artifact_root / "README.md").write_text(
        "# A4 TODO 中断恢复消融原始记录\n\n"
        "第一会话仅建立并保存 TODO；第二会话只接收统一的继续指令。每个运行目录保留两阶段 CLI 输出、工作区和 trace 报告。\n",
        encoding="utf-8",
    )
    print(f"ARTIFACT_ROOT={artifact_root}")
    print(f"TRACE_ROOT={trace_root}")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
