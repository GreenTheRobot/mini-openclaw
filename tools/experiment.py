"""可复现实验的准备、冒烟测试、启动、监控与报告工具。"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from .shell import is_dangerous_command

_RUN_ROOT = Path("runs")
_ACTIVE_PROCESSES: dict[int, subprocess.Popen[Any]] = {}
_ACTIVE_PROCESSES_LOCK = threading.Lock()

_DEFAULT_GITIGNORE_LINES = [
    "runs/",
    "traces/",
    ".mini-openclaw/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".pytest_cache/",
    ".venv/",
    "venv/",
    "out-*/",
]


def _git_result(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )


def _git(*args: str) -> str:
    result = _git_result(*args)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _ensure_gitignore() -> bool:
    path = Path(".gitignore")
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [line for line in _DEFAULT_GITIGNORE_LINES if line not in existing_lines]
    if not missing:
        return False
    prefix = existing.rstrip() + "\n" if existing.strip() else ""
    path.write_text(prefix + "\n".join(missing) + "\n", encoding="utf-8")
    return True


def _ensure_git_identity() -> None:
    email = _git_result("config", "user.email")
    if email.returncode != 0 or not email.stdout.strip():
        _git_result("config", "user.email", "mini-openclaw@example.invalid")
    name = _git_result("config", "user.name")
    if name.returncode != 0 or not name.stdout.strip():
        _git_result("config", "user.name", "mini-openclaw")


def _git_context() -> dict[str, Any]:
    initialized = False
    initial_commit_created = False
    gitignore_initialized = False
    git_error = ""

    if not Path(".git").exists():
        init = _git_result("init")
        initialized = init.returncode == 0
        if not initialized:
            return {
                "git_repository": False,
                "git_initialized": False,
                "gitignore_initialized": False,
                "git_initial_commit_created": False,
                "git_has_commit": False,
                "git_commit": "unknown",
                "git_branch": "unknown",
                "git_status": "",
                "git_dirty": False,
                "git_error": (init.stderr or init.stdout or "git init failed").strip(),
            }

    inside = _git_result("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        return {
            "git_repository": False,
            "git_initialized": initialized,
            "gitignore_initialized": False,
            "git_initial_commit_created": False,
            "git_has_commit": False,
            "git_commit": "unknown",
            "git_branch": "unknown",
            "git_status": "",
            "git_dirty": False,
            "git_error": (inside.stderr or inside.stdout or "not a git repository").strip(),
        }

    commit = _git_result("rev-parse", "HEAD")
    has_commit = commit.returncode == 0
    if not has_commit:
        gitignore_initialized = _ensure_gitignore()
        _ensure_git_identity()
        add = _git_result("add", ".")
        if add.returncode != 0:
            git_error = (add.stderr or add.stdout or "git add failed").strip()
        else:
            baseline = _git_result("commit", "-m", "chore: initialize experiment baseline")
            if baseline.returncode == 0:
                initial_commit_created = True
                commit = _git_result("rev-parse", "HEAD")
                has_commit = commit.returncode == 0
            else:
                git_error = (baseline.stderr or baseline.stdout or "git commit failed").strip()

    branch = _git_result("branch", "--show-current")
    status = _git_result("status", "--short")
    if not has_commit and not git_error:
        git_error = (commit.stderr or commit.stdout or "git has no commits").strip()

    return {
        "git_repository": True,
        "git_initialized": initialized,
        "gitignore_initialized": gitignore_initialized,
        "git_initial_commit_created": initial_commit_created,
        "git_has_commit": has_commit,
        "git_commit": commit.stdout.strip() if has_commit else "unknown",
        "git_branch": branch.stdout.strip() if branch.returncode == 0 else "unknown",
        "git_status": status.stdout.strip() if status.returncode == 0 else "",
        "git_dirty": bool(status.stdout.strip()) if status.returncode == 0 else False,
        "git_error": git_error,
    }


def _load(run_id: str) -> tuple[Path, dict[str, Any]]:
    directory = _RUN_ROOT / run_id
    path = directory / "metadata.json"
    if not path.exists():
        raise FileNotFoundError(f"没有实验：{run_id}")
    return directory, json.loads(path.read_text(encoding="utf-8"))


def _save(directory: Path, metadata: dict[str, Any]) -> None:
    path = directory / "metadata.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _log_declares_failure(text: str) -> bool:
    lowered = text.lower()
    failure_markers = (
        "status=failed", "status: failed", "status failed",
        "status=error", "status: error", "traceback", "exception",
        "failed",
    )
    return any(marker in lowered for marker in failure_markers)


def _log_declares_success(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("status=completed", "status: completed", "status=success"))


def _refresh_status(directory: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    log_path = directory / "train.log"
    error_path = directory / "error.log"
    returncode_path = directory / "returncode.txt"
    log = _read_text(log_path)
    error = _read_text(error_path)
    returncode: int | None = None
    if returncode_path.exists():
        raw_returncode = returncode_path.read_text(encoding="utf-8", errors="replace").strip()
        try:
            returncode = int(raw_returncode)
        except ValueError:
            metadata["returncode_parse_error"] = raw_returncode
    process_returncode: int | None = None
    if metadata.get("pid"):
        running, process_returncode = _poll_process(int(metadata["pid"]))
    else:
        running = False
    # Prefer the live Popen handle in the process that launched the job.
    # On Windows, os.kill(pid, 0) may briefly report an exited process as alive.
    if returncode is None and process_returncode is not None:
        returncode = process_returncode
        returncode_path.write_text(str(returncode), encoding="utf-8")
        metadata.pop("returncode_parse_error", None)
    finished = returncode is not None or (metadata.get("status") == "running" and not running)
    if returncode is not None:
        metadata["returncode"] = returncode
    if finished:
        if returncode not in (None, 0) or error.strip() or _log_declares_failure(log):
            metadata["status"] = "failed"
        elif _log_declares_success(log) or returncode == 0:
            metadata["status"] = "completed"
        else:
            metadata["status"] = "unknown"
        metadata.setdefault("finished_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        _save(directory, metadata)
    return {**metadata, "running": running, "log_tail": log[-2000:], "error_tail": error[-2000:]}


def _experiment_prepare(command: str, name: str = "experiment", config: str = "", seed: int = 42) -> str:
    git = _git_context()
    if not git["git_repository"]:
        return ToolResult(
            json.dumps({
                "error": "实验准备需要 Git 仓库，但 git init 失败",
                **git,
                "suggestion": "请检查 git 是否安装、当前目录是否可写，然后重新准备实验。",
            }, ensure_ascii=False, indent=2),
            False,
            "git_unavailable",
        )
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")[:30] or "experiment"
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + safe_name
    directory = _RUN_ROOT / run_id
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {
        "run_id": run_id,
        "name": name,
        "status": "prepared",
        "command": command,
        "config": config,
        "seed": seed,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "log_path": str(directory / "train.log"),
        "error_path": str(directory / "error.log"),
        "output_path": str(directory),
        **git,
    }
    _save(directory, metadata)
    return json.dumps(metadata, ensure_ascii=False, indent=2)


def _experiment_smoke_test(command: str, timeout_seconds: int = 60) -> ToolResult:
    if is_dangerous_command(command):
        raise ValueError("实验命令触发高危命令拦截")
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        payload = json.dumps({"success": False, "error": "timeout", "timeout_seconds": timeout_seconds}, ensure_ascii=False, indent=2)
        return ToolResult(payload, False, "timeout")
    payload = json.dumps({
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }, ensure_ascii=False, indent=2)
    category = "ok" if result.returncode == 0 else "smoke_test_failed"
    return ToolResult(payload, result.returncode == 0, category)


def _experiment_start(run_id: str) -> str:
    directory, metadata = _load(run_id)
    if is_dangerous_command(metadata["command"]):
        raise ValueError("实验命令触发高危命令拦截")
    if metadata["status"] not in {"prepared", "failed"}:
        raise ValueError(f"实验当前状态不可启动：{metadata['status']}")
    command = metadata["command"]
    returncode_path = directory / "returncode.txt"
    if returncode_path.exists():
        returncode_path.unlink()
    if os.name == "nt":
        runner = directory / "run.cmd"
        runner.write_text(
            "@echo off\r\n"
            f"{command}\r\n"
            "set code=%ERRORLEVEL%\r\n"
            f'> "{returncode_path.resolve()}" echo %code%\r\n'
            "exit /b %code%\r\n",
            encoding="utf-8",
        )
        command = f'"{runner.resolve()}"'
    else:
        runner = directory / "run.sh"
        escaped_returncode_path = str(returncode_path.resolve()).replace('"', '\\"')
        runner.write_text(
            "#!/bin/sh\n"
            f"( {command} )\n"
            "code=$?\n"
            f'printf "%s" "$code" > "{escaped_returncode_path}"\n'
            'exit "$code"\n',
            encoding="utf-8",
        )
        command = f'sh "{runner.resolve()}"'
    stdout = (directory / "train.log").open("a", encoding="utf-8")
    stderr = (directory / "error.log").open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()), "stdout": stdout, "stderr": stderr, "shell": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES[process.pid] = process
    stdout.close()
    stderr.close()
    metadata.update({
        "status": "running", "pid": process.pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    _save(directory, metadata)
    return json.dumps(metadata, ensure_ascii=False, indent=2)


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _poll_process(pid: int) -> tuple[bool, int | None]:
    """Return live state and an exit code when this process launched the job."""
    with _ACTIVE_PROCESSES_LOCK:
        process = _ACTIVE_PROCESSES.get(pid)
    if process is None:
        return _pid_running(pid), None
    returncode = process.poll()
    if returncode is None:
        return True, None
    with _ACTIVE_PROCESSES_LOCK:
        if _ACTIVE_PROCESSES.get(pid) is process:
            _ACTIVE_PROCESSES.pop(pid, None)
    return False, int(returncode)


def _experiment_status(run_id: str) -> str:
    directory, metadata = _load(run_id)
    status = _refresh_status(directory, metadata)
    return json.dumps(status, ensure_ascii=False, indent=2)


def _extract_metrics(text: str) -> dict[str, list[float]]:
    metrics: dict[str, list[float]] = {}
    for name, raw in re.findall(r"\b([A-Za-z][A-Za-z0-9_/-]{1,30})\s*[:=]\s*(-?\d+(?:\.\d+)?)", text):
        metrics.setdefault(name, []).append(float(raw))
    return metrics


def _experiment_report(run_id: str) -> str | ToolResult:
    directory, metadata = _load(run_id)
    metadata = _refresh_status(directory, metadata)
    log_path = directory / "train.log"
    error_path = directory / "error.log"
    log = _read_text(log_path)
    error = _read_text(error_path)
    metrics = _extract_metrics(log)
    lines = [
        f"# 实验报告：{metadata['name']}", "", f"- Run ID：`{run_id}`",
        f"- 状态：{metadata['status']}",
        f"- Git：`{metadata['git_commit']}`（{metadata['git_branch']}）",
        f"- Git 仓库：{'yes' if metadata.get('git_repository') else 'no'}；"
        f"基线提交：{'yes' if metadata.get('git_has_commit') else 'no'}；"
        f"本次是否初始化：{'yes' if metadata.get('git_initialized') else 'no'}；"
        f"是否创建初始提交：{'yes' if metadata.get('git_initial_commit_created') else 'no'}",
        f"- Git 工作区：{'dirty' if metadata.get('git_dirty') else 'clean'}",
        f"- 命令：`{metadata['command']}`",
        f"- 配置：`{metadata['config'] or '未指定'}`",
        f"- 随机种子：{metadata['seed']}", f"- Python：{metadata['python']}",
        "", "## 指标",
    ]
    if metrics:
        for name, values in metrics.items():
            lines.append(f"- {name}：final={values[-1]:g}，min={min(values):g}，max={max(values):g}")
    else:
        lines.append("- 日志中未识别到 `metric=value` 格式的指标。")
    lines.extend([
        "", "## 错误摘要", error[-2000:] or "无错误输出。",
        "", "## 复现命令", f"```bash\n{metadata['command']}\n```",
    ])
    report = "\n".join(lines) + "\n"
    path = directory / "report.md"
    path.write_text(report, encoding="utf-8")
    content = f"报告已生成：{path}\n\n{report}"
    if metadata.get("status") == "failed":
        return ToolResult(content, False, "experiment_failed")
    if metadata.get("status") not in {"prepared", "completed"}:
        return ToolResult(content, False, "experiment_status_unknown")
    return content


experiment_prepare_tool = Tool(
    "experiment_prepare", "创建可复现实验目录并记录 Git、环境、配置、随机种子和路径。",
    {"type": "object", "properties": {"command": {"type": "string"}, "name": {"type": "string"}, "config": {"type": "string"}, "seed": {"type": "integer"}}, "required": ["command"], "additionalProperties": False},
    _experiment_prepare,
)
experiment_smoke_test_tool = Tool(
    "experiment_smoke_test", "在正式实验前运行有超时限制的冒烟测试。",
    {"type": "object", "properties": {"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, "required": ["command"], "additionalProperties": False},
    _experiment_smoke_test,
)
experiment_start_tool = Tool(
    "experiment_start", "后台启动已准备的实验并重定向日志。",
    {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"], "additionalProperties": False},
    _experiment_start,
)
experiment_status_tool = Tool(
    "experiment_status", "检查实验进程、状态与最新日志。",
    {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"], "additionalProperties": False},
    _experiment_status,
)
experiment_report_tool = Tool(
    "experiment_report", "从元数据和日志提取指标并生成可复现 Markdown 报告。",
    {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"], "additionalProperties": False},
    _experiment_report,
)
