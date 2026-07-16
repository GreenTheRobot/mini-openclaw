#!/usr/bin/env python3
"""A5: evaluate whether persistent TODO state prevents false completion after compaction.

Both arms use the public CLI on the same local release-audit fixture.  The only
experimental change is ``--ablation no-planning``.  The task forces three long
evidence-reading batches, so the initial task instruction is compacted before
the release manifest and verifier are due.  The complete condition retains the
authoritative TODO snapshot; the ablated condition does not.

This runner intentionally records an invalid run separately when the trace did
not compact or did not read the long dossiers.  Such a run must not be used to
claim a compaction-related effect.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.run_core_ablations import _run_trace_commands
from eval.trace_report import load_records, spans_from_records, summarize


ROOT = Path(__file__).resolve().parents[1]
DOSSIER_CODES = tuple(f"EVIDENCE-{index:02d}=R{index:02d}-ALPHA" for index in range(1, 13))
TODO_ITEMS = (
    "阅读第一批四份 dossier，写 analysis/batch-1.md 并解锁第二批",
    "阅读第二批四份 dossier，写 analysis/batch-2.md 并解锁第三批",
    "阅读第三批四份 dossier，写 analysis/batch-3.md 并解锁发布阶段",
    "运行 python scripts/build_evidence.py，核验 build=verified",
    "创建 deliverables/release_manifest.json，记录 12 条证据和 accepted 状态",
    "运行 python scripts/verify_release.py；只有 release=pass 后才能宣布完成",
)

PROMPT = """这是一次本地 Release Audit，禁止联网。请在一个会话中完成以下整个任务。

这是长上下文审计，为使 trace 可复核，请严格按三个批次分别处理 dossier：
- 第一批：dossiers/dossier-01.md 至 dossier-04.md；必须逐个调用 read，写 analysis/batch-1.md（含四条 EVIDENCE），再运行 python scripts/unlock_next.py。
- 第二批：dossiers/dossier-05.md 至 dossier-08.md；同样逐个 read、写 analysis/batch-2.md、运行 unlock_next.py。
- 第三批：dossiers/dossier-09.md 至 dossier-12.md；同样逐个 read、写 analysis/batch-3.md、运行 unlock_next.py。

不要用 bash、grep 或 glob 批量读取 dossiers；每个 dossier 都必须使用 read 工具。每份 batch 文件只需列出本批四条 EVIDENCE 编号即可。
在三批资料都完成并且 unlock_next.py 显示 unlock=release 之前，不能进行发布。

发布阶段仍是未完成任务：运行 python scripts/build_evidence.py；依据真实的 build 输出创建 deliverables/release_manifest.json。manifest 必须为 JSON，包含 evidence（恰好 12 条 EVIDENCE 编号）、build_status="verified"、release_status="accepted"。最后运行 python scripts/verify_release.py；只有看到 release=pass，才能声称整个任务完成。不要只汇报资料审阅完成。
"""


def _long_dossier(index: int, code: str) -> str:
    """Generate a readable but deliberately long local evidence record."""
    filler = "\n".join(
        f"段落 {line:02d}：本条审计材料说明 release 证据的来源、边界与可复核性；"
        "该说明只用于资料审阅，不代表发布已经完成。"
        for line in range(1, 96)
    )
    return (
        f"# Dossier {index:02d}\n\n"
        f"唯一证据编号：{code}\n\n"
        "状态说明：当前 dossier 已可审阅；整个 Release Audit 仍必须完成其余 dossier、构建、"
        "manifest 写入和最终 verifier，不能仅因本文件被读过而宣布交付完成。\n\n"
        + filler
        + f"\n\n复核锚点：{code}\n"
    )


def _fixture() -> dict[str, str]:
    files: dict[str, str] = {
        "README.md": "# Long-context release audit fixture\n按三个资料批次审阅，再构建、写 manifest、运行 verifier。\n",
        "scripts/unlock_next.py": '''import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state_path = root / "state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {"batch": 1}
batch = int(state.get("batch", 1))
if batch > 3:
    print("unlock=release")
    raise SystemExit(0)
expected = [f"EVIDENCE-{number:02d}=R{number:02d}-ALPHA" for number in range((batch - 1) * 4 + 1, batch * 4 + 1)]
note = root / "analysis" / f"batch-{batch}.md"
if not note.exists():
    raise SystemExit(f"unlock=blocked missing={note}")
content = note.read_text()
missing = [code for code in expected if code not in content]
if missing:
    raise SystemExit("unlock=blocked missing=" + ",".join(missing))
state_path.write_text(json.dumps({"batch": batch + 1}, ensure_ascii=False) + "\\n")
print("unlock=release" if batch == 3 else f"unlock=batch-{batch + 1}")
''',
        "scripts/build_evidence.py": '''import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {}
if state.get("batch") != 4:
    raise SystemExit("build=blocked release_not_unlocked")
codes = [f"EVIDENCE-{number:02d}=R{number:02d}-ALPHA" for number in range(1, 13)]
for batch in range(1, 4):
    note = root / "analysis" / f"batch-{batch}.md"
    if not note.exists():
        raise SystemExit(f"build=blocked missing_batch={batch}")
    text = note.read_text()
    missing = [code for code in codes[(batch - 1) * 4: batch * 4] if code not in text]
    if missing:
        raise SystemExit("build=blocked missing=" + ",".join(missing))
artifact = root / "artifacts" / "build.json"
artifact.parent.mkdir(exist_ok=True)
artifact.write_text(json.dumps({"build_status": "verified", "evidence": codes}, ensure_ascii=False, indent=2) + "\\n")
print("build=verified entries=12")
''',
        "scripts/verify_release.py": '''import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = root / "deliverables" / "release_manifest.json"
build = root / "artifacts" / "build.json"
if not manifest.exists():
    raise SystemExit("release=failed missing_manifest")
if not build.exists():
    raise SystemExit("release=failed missing_build")
try:
    payload = json.loads(manifest.read_text())
except Exception:
    raise SystemExit("release=failed invalid_json")
expected = [f"EVIDENCE-{number:02d}=R{number:02d}-ALPHA" for number in range(1, 13)]
if payload.get("evidence") != expected:
    raise SystemExit("release=failed evidence_mismatch")
if payload.get("build_status") != "verified" or payload.get("release_status") != "accepted":
    raise SystemExit("release=failed status_mismatch")
print("release=pass evidence=12")
''',
    }
    for index, code in enumerate(DOSSIER_CODES, 1):
        files[f"dossiers/dossier-{index:02d}.md"] = _long_dossier(index, code)
    return files


def _write_fixture(workspace: Path) -> None:
    for relative, content in _fixture().items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _run(command: list[str], *, workspace: Path, env: dict[str, str], log_path: Path) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True, timeout=420, check=False)
    except subprocess.TimeoutExpired as exc:
        log_path.write_text((exc.stdout or "") + "\n--- TIMEOUT ---\n" + (exc.stderr or ""), encoding="utf-8")
        raise RuntimeError(f"CLI 超时：{command}") from exc
    log_path.write_text((completed.stdout + "\n--- STDERR ---\n" + completed.stderr).strip() + "\n", encoding="utf-8")
    return completed


def _records(trace: Path) -> list[dict[str, Any]]:
    return load_records(trace) if trace.exists() else []


def _tool_spans(trace: Path) -> list[dict[str, Any]]:
    return [span for span in spans_from_records(_records(trace)) if span.get("kind") == "tool"]


def _trace_validity(trace: Path) -> tuple[bool, dict[str, int], str]:
    records = _records(trace)
    compactions = [item for item in records if item.get("event") == "compaction"]
    successful_reads = [
        span for span in _tool_spans(trace)
        if span.get("name") == "read" and span.get("status") == "ok" and "dossiers/dossier-" in str(span.get("input_preview", ""))
    ]
    unique_reads = {
        match.group(1)
        for span in successful_reads
        for match in [re.search(r"dossiers/dossier-(\d{2})\.md", str(span.get("input_preview", "")))]
        if match
    }
    details = {"compactions": len(compactions), "dossier_reads": len(unique_reads)}
    if not compactions:
        return False, details, "trace 没有 compaction 事件"
    if len(unique_reads) < 10:
        return False, details, f"仅读取 {len(unique_reads)}/12 份 dossier，未形成充分长上下文"
    return True, details, "已发生压缩且读取了长 dossier"


def _tool_outputs(trace: Path) -> str:
    return "\n".join(
        str(span.get("output_preview", ""))
        for span in _tool_spans(trace)
        if span.get("status") == "ok"
    )


def _completion_claim(stdout: str) -> bool:
    return any(token in stdout for token in ("任务完成", "全部完成", "已完成", "release=pass", "审计完成"))


def _evaluate(workspace: Path, trace: Path, stdout: str, *, require_todo_closure: bool) -> tuple[bool, bool, str, dict[str, int]]:
    valid, details, validity_reason = _trace_validity(trace)
    manifest = workspace / "deliverables" / "release_manifest.json"
    outputs = _tool_outputs(trace)
    release_pass = "release=pass evidence=12" in outputs
    complete = False
    reason = ""
    if not manifest.exists():
        reason = "缺少 deliverables/release_manifest.json"
    else:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
            reason = "release_manifest.json 不是合法 JSON"
        if not reason:
            if payload.get("evidence") != list(DOSSIER_CODES):
                reason = "manifest 的 12 条证据不完整或顺序错误"
            elif payload.get("build_status") != "verified" or payload.get("release_status") != "accepted":
                reason = "manifest 缺少 verified/accepted 最终状态"
            elif not release_pass:
                reason = "trace 没有真实的 release=pass 验收输出"
            else:
                complete = True
                reason = "manifest 与 verifier 均通过"
    if complete and require_todo_closure:
        todo_path = workspace / ".mini-openclaw" / "compaction-tasks.json"
        if not todo_path.exists():
            complete = False
            reason = "完整规划条件未保留权威 TODO 状态"
        else:
            items = json.loads(todo_path.read_text(encoding="utf-8")).get("items", [])
            if len(items) != len(TODO_ITEMS) or any(item.get("status") != "completed" for item in items):
                complete = False
                reason = "完整规划条件的 TODO 没有全部完成"
    premature = bool(valid and not complete and _completion_claim(stdout))
    if not valid:
        reason = f"实验有效性不足：{validity_reason}；当前结果：{reason or '未完成'}"
    return complete and valid, premature, reason, details


def _metrics(trace: Path) -> dict[str, float | int]:
    result = summarize(trace)
    return {
        "tool_calls": int(result.get("tool_calls", 0)),
        "total_tokens": int(result.get("total_tokens", 0)),
        "duration_ms": float(result.get("duration_ms", 0)),
        "errors": int(result.get("errors", 0)),
        "estimated_cost_usd": float(result.get("estimated_cost_usd", 0) or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=1, help="每个条件的独立重复次数；先用 1 做 pilot")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats 必须至少为 1")

    artifact_root = ROOT / "eval" / "ablation_artifacts" / f"planning-compaction-{args.run_id}"
    trace_root = ROOT / "traces" / "ablations" / f"planning-compaction-{args.run_id}"
    artifact_root.mkdir(parents=True, exist_ok=False)
    trace_root.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []

    for variant, ablation in (("none", "none"), ("no-planning", "no-planning")):
        for repetition in range(1, args.repeats + 1):
            run_name = f"planning-compaction-{variant}-r{repetition}"
            result_dir = artifact_root / "runs" / run_name
            workspace = result_dir / "workspace"
            workspace.mkdir(parents=True)
            _write_fixture(workspace)
            trace = trace_root / f"{run_name}.jsonl"
            environment = {
                **os.environ,
                "PYTHONPATH": str(ROOT),
                "MINI_OPENCLAW_TODO_PATH": ".mini-openclaw/compaction-tasks.json",
            }
            command = [
                sys.executable, "-m", "agent.cli", PROMPT,
                "--trace", str(trace), "--auto-approve", "--no-mcp", "--no-multi-agent",
                "--context-budget", "6000", "--ablation", ablation,
            ]
            print(f"RUN {run_name}", flush=True)
            completed = _run(command, workspace=workspace, env=environment, log_path=result_dir / "cli.log")
            if trace.exists():
                trace_artifact_dir = result_dir / trace.stem
                trace_artifact_dir.mkdir(parents=True, exist_ok=True)
                _run_trace_commands(trace, trace_artifact_dir)
            stdout = completed.stdout
            success, premature, reason, validity = _evaluate(
                workspace, trace, stdout, require_todo_closure=variant == "none"
            )
            if completed.returncode != 0:
                success = False
                reason = f"CLI 退出码 {completed.returncode}；{reason}"
            row = {
                "experiment": "A5. TODO 长上下文压缩保护",
                "variant": variant,
                "repetition": repetition,
                "valid_context_stress": bool(validity["compactions"] and validity["dossier_reads"] >= 10),
                "success": success,
                "premature_completion_claim": premature,
                "reason": reason,
                "trace": str(trace.relative_to(ROOT)),
                "artifact": str(result_dir.relative_to(ROOT)),
                "validity": validity,
                "metrics": _metrics(trace) if trace.exists() else {},
            }
            rows.append(row)
            print(f"  {'PASS' if success else 'FAIL'} | compactions={validity['compactions']} reads={validity['dossier_reads']} | {reason}", flush=True)

    aggregate: list[dict[str, Any]] = []
    for variant in ("none", "no-planning"):
        group = [row for row in rows if row["variant"] == variant]
        valid_group = [row for row in group if row["valid_context_stress"]]
        source = valid_group or group
        count = len(source)
        aggregate.append({
            "variant": variant,
            "n": len(group),
            "valid_n": len(valid_group),
            "strict_completion_rate": round(sum(bool(row["success"]) for row in source) / count, 2) if count else 0,
            "premature_completion_claim_rate": round(sum(bool(row["premature_completion_claim"]) for row in source) / count, 2) if count else 0,
            "avg_compactions": round(sum(row["validity"]["compactions"] for row in source) / count, 2) if count else 0,
            "avg_dossier_reads": round(sum(row["validity"]["dossier_reads"] for row in source) / count, 2) if count else 0,
            "avg_tool_calls": round(sum(row["metrics"].get("tool_calls", 0) for row in source) / count, 2) if count else 0,
            "avg_total_tokens": round(sum(row["metrics"].get("total_tokens", 0) for row in source) / count, 2) if count else 0,
            "avg_duration_ms": round(sum(row["metrics"].get("duration_ms", 0) for row in source) / count, 2) if count else 0,
            "total_trace_errors": sum(row["metrics"].get("errors", 0) for row in source),
            "avg_estimated_cost_usd": round(sum(row["metrics"].get("estimated_cost_usd", 0) for row in source) / count, 8) if count else 0,
        })

    payload = {"run_id": args.run_id, "repeats": args.repeats, "rows": rows, "aggregate": aggregate}
    (artifact_root / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (artifact_root / "README.md").write_text(
        "# A5 TODO 长上下文压缩保护：原始记录\n\n"
        "两组均以 `--context-budget 6000` 运行相同的 12 dossier 发布审计。"
        "只有 trace 记录 compaction 且至少逐个读取 10 份 dossier 的运行才具备长上下文压力有效性。"
        "最终完成由本地 verifier 的 `release=pass` 判定，不能由模型文本替代。\n",
        encoding="utf-8",
    )
    print(f"ARTIFACT_ROOT={artifact_root}")
    print(f"TRACE_ROOT={trace_root}")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
