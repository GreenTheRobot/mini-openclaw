"""Relative-path scheduler for recurring research-agent jobs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


SCHEDULE_PATH = Path(".mini-openclaw/schedules.json")
RUN_ROOT = Path(".mini-openclaw/scheduler-runs")
LOCK_PATH = Path(".mini-openclaw/scheduler.lock")


def _now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def _parse_datetime(value: str, timezone: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.astimezone(ZoneInfo(timezone))


def _relative_path(value: str, default: str = ".") -> str:
    raw = Path(value or default)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("调度任务路径必须是工作目录内的相对路径")
    return raw.as_posix() or "."


def _resolve_workdir(root: Path, relative: str) -> Path:
    target = (root / _relative_path(relative)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("调度任务 workdir 必须位于当前项目目录内") from exc
    if not target.is_dir():
        raise ValueError(f"调度任务 workdir 不存在：{relative}")
    return target


def _next_run(spec: dict[str, Any], after: datetime) -> datetime | None:
    timezone = str(spec["timezone"])
    kind = spec["schedule_type"]
    if kind == "once":
        target = _parse_datetime(str(spec["expression"]), timezone)
        return target if target > after else None
    if kind == "interval":
        minutes = int(spec["interval_minutes"])
        candidate = _parse_datetime(str(spec.get("next_run_at") or after.isoformat()), timezone)
        while candidate <= after:
            candidate += timedelta(minutes=minutes)
        return candidate
    if kind == "cron":
        try:
            from croniter import croniter
        except ImportError as exc:
            raise RuntimeError("cron 调度需要 croniter；请执行 pip install -r requirements.txt") from exc
        return croniter(str(spec["expression"]), after).get_next(datetime).astimezone(ZoneInfo(timezone))
    raise ValueError("schedule_type 必须是 once/interval/cron")


def _load(root: Path) -> list[dict[str, Any]]:
    path = root / SCHEDULE_PATH
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"调度文件格式错误：{SCHEDULE_PATH}")
    return data


def _save(root: Path, schedules: list[dict[str, Any]]) -> None:
    path = root / SCHEDULE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(schedules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def add_schedule(
    name: str,
    prompt: str,
    schedule_type: str,
    expression: str,
    *,
    root: Path | str = ".",
    schedule_id: str = "",
    workdir: str = ".",
    timezone: str = "Asia/Shanghai",
    permission_mode: str = "auto-safe",
    timeout_seconds: int = 1800,
    interval_minutes: int = 0,
    max_runs: int = 0,
) -> dict[str, Any]:
    root = Path(root).resolve()
    if not name.strip() or not prompt.strip():
        raise ValueError("调度任务 name 和 prompt 不能为空")
    if schedule_type not in {"once", "interval", "cron"}:
        raise ValueError("schedule_type 必须是 once/interval/cron")
    if permission_mode not in {"plan", "auto-safe"}:
        raise ValueError("自动任务 permission_mode 只允许 plan 或 auto-safe")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds 必须为正数")
    if max_runs < 0:
        raise ValueError("max_runs 不能为负数；0 表示不限制轮数")
    ZoneInfo(timezone)  # validate timezone
    relative_workdir = _relative_path(workdir)
    _resolve_workdir(root, relative_workdir)
    if schedule_type == "interval" and interval_minutes < 1:
        raise ValueError("interval 任务需要正数 interval_minutes")
    if schedule_type != "interval" and not expression.strip():
        raise ValueError("once/cron 任务需要 expression")
    if schedule_type == "cron":
        try:
            from croniter import croniter
            croniter(expression, _now(timezone))
        except ImportError as exc:
            raise RuntimeError("cron 调度需要 croniter；请执行 pip install -r requirements.txt") from exc
        except (ValueError, KeyError) as exc:
            raise ValueError(f"无效 cron 表达式：{expression}") from exc

    schedule_id = schedule_id.strip() or f"schedule-{uuid4().hex[:8]}"
    now = _now(timezone)
    spec: dict[str, Any] = {
        "id": schedule_id,
        "name": name.strip(),
        "prompt": prompt.strip(),
        "workdir": relative_workdir,
        "schedule_type": schedule_type,
        "expression": expression.strip(),
        "interval_minutes": interval_minutes,
        "timezone": timezone,
        "permission_mode": permission_mode,
        "timeout_seconds": timeout_seconds,
        "max_runs": max_runs,
        "run_count": 0,
        "enabled": True,
        "created_at": now.isoformat(),
        "next_run_at": None,
        "last_run": None,
    }
    if schedule_type == "interval":
        spec["next_run_at"] = (now + timedelta(minutes=interval_minutes)).isoformat()
    else:
        next_run = _next_run(spec, now - timedelta(seconds=1))
        spec["next_run_at"] = next_run.isoformat() if next_run else None

    schedules = _load(root)
    if any(item.get("id") == schedule_id for item in schedules):
        raise ValueError(f"调度任务 ID 已存在：{schedule_id}")
    schedules.append(spec)
    _save(root, schedules)
    return spec


def list_schedules(root: Path | str = ".") -> list[dict[str, Any]]:
    return _load(Path(root).resolve())


def update_schedule(schedule_id: str, *, root: Path | str = ".", enabled: bool | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    schedules = _load(root)
    for spec in schedules:
        if spec.get("id") == schedule_id:
            if enabled is not None:
                spec["enabled"] = bool(enabled)
            _save(root, schedules)
            return spec
    raise ValueError(f"没有调度任务：{schedule_id}")


def remove_schedule(schedule_id: str, *, root: Path | str = ".") -> bool:
    root = Path(root).resolve()
    schedules = _load(root)
    remaining = [item for item in schedules if item.get("id") != schedule_id]
    if len(remaining) == len(schedules):
        return False
    _save(root, remaining)
    return True


def _acquire_lock(root: Path) -> Path | None:
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return path
    except FileExistsError:
        return None


def _todo_status(path: Path, workdir: Path) -> dict[str, Any]:
    """Read the run-local TODO as an execution invariant, not model prose."""
    relative = path.relative_to(workdir).as_posix()
    if not path.exists():
        return {"path": relative, "open": [], "present": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else []
        open_items = [
            str(item.get("id", ""))
            for item in items
            if isinstance(item, dict) and item.get("status") in {"pending", "in_progress"}
        ]
        return {"path": relative, "open": open_items, "present": True}
    except Exception as exc:
        return {"path": relative, "open": ["<invalid-todo>"], "present": True, "error": str(exc)}


def _execute(spec: dict[str, Any], root: Path) -> dict[str, Any]:
    workdir = _resolve_workdir(root, str(spec["workdir"]))
    run_id = f"{spec['id']}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    run_dir = workdir / RUN_ROOT
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / f"{run_id}.output.txt"
    error_path = run_dir / f"{run_id}.error.txt"
    trace_path = run_dir / f"{run_id}.trace.jsonl"
    trace_relative = trace_path.relative_to(workdir).as_posix()
    command = [
        sys.executable, "-m", "agent.cli", spec["prompt"],
        "--permission-mode", spec["permission_mode"],
        "--no-mcp", "--trace", trace_relative,
    ]
    environment = os.environ.copy()
    package_root = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(package_root), environment.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    environment["MINI_OPENCLAW_TODO_PATH"] = (
        run_dir / f"{run_id}.tasks.json"
    ).relative_to(workdir).as_posix()
    environment["MINI_OPENCLAW_TRACE_CONTEXT"] = json.dumps({
        "schedule_id": spec["id"],
        "scheduled_run_id": run_id,
        "schedule_workdir": str(spec["workdir"]),
    }, ensure_ascii=False)
    started = datetime.now().astimezone()
    status = "completed"
    returncode = 0
    try:
        completed = subprocess.run(
            command, cwd=workdir, env=environment, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=int(spec["timeout_seconds"]),
        )
        returncode = completed.returncode
        output_path.write_text(completed.stdout, encoding="utf-8")
        error_path.write_text(completed.stderr, encoding="utf-8")
        if returncode != 0:
            status = "failed"
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        status = "timed_out"
        output_path.write_text(str(exc.stdout or ""), encoding="utf-8")
        error_path.write_text(str(exc.stderr or ""), encoding="utf-8")
    todo = _todo_status(run_dir / f"{run_id}.tasks.json", workdir)
    if status == "completed" and todo["open"]:
        status = "incomplete"
    finished = datetime.now().astimezone()
    return {
        "run_id": run_id,
        "status": status,
        "returncode": returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "workdir": str(spec["workdir"]),
        "output": output_path.relative_to(workdir).as_posix(),
        "error": error_path.relative_to(workdir).as_posix(),
        "trace": trace_relative,
        "todo": todo,
    }


def _run_schedule_unlocked(schedule_id: str, root: Path) -> dict[str, Any]:
    schedules = _load(root)
    for index, spec in enumerate(schedules):
        if spec.get("id") != schedule_id:
            continue
        result = _execute(spec, root)
        spec["run_count"] = int(spec.get("run_count", 0)) + 1
        now = _now(str(spec["timezone"]))
        reached_max_runs = bool(spec.get("max_runs")) and spec["run_count"] >= int(spec["max_runs"])
        if spec["schedule_type"] == "once" or reached_max_runs:
            spec["enabled"] = False
            spec["next_run_at"] = None
        elif spec["schedule_type"] == "interval":
            spec["next_run_at"] = (now + timedelta(minutes=int(spec["interval_minutes"]))).isoformat()
        else:
            next_run = _next_run(spec, now)
            spec["next_run_at"] = next_run.isoformat() if next_run else None
        spec["last_run"] = result
        schedules[index] = spec
        _save(root, schedules)
        return result
    raise ValueError(f"没有调度任务：{schedule_id}")


def run_schedule(schedule_id: str, *, root: Path | str = ".") -> dict[str, Any]:
    root = Path(root).resolve()
    lock = _acquire_lock(root)
    if lock is None:
        return {"status": "skipped_overlap", "schedule_id": schedule_id}
    try:
        return _run_schedule_unlocked(schedule_id, root)
    finally:
        lock.unlink(missing_ok=True)


def run_due(*, root: Path | str = ".", now: datetime | None = None) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    lock = _acquire_lock(root)
    if lock is None:
        return [{"status": "skipped_overlap"}]
    try:
        schedules = _load(root)
        results = []
        current = now or datetime.now().astimezone()
        for spec in schedules:
            if not spec.get("enabled") or not spec.get("next_run_at"):
                continue
            due = _parse_datetime(str(spec["next_run_at"]), str(spec["timezone"]))
            if due <= current.astimezone(due.tzinfo):
                results.append(_run_schedule_unlocked(str(spec["id"]), root))
        return results
    finally:
        lock.unlink(missing_ok=True)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mini-openclaw-scheduler")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("schedule_id")
    sub.add_parser("run-due")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps(list_schedules(), ensure_ascii=False, indent=2))
    elif args.command == "run":
        print(json.dumps(run_schedule(args.schedule_id), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(run_due(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
