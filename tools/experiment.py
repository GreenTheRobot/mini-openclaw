"""可复现实验的准备、冒烟测试、启动、监控与报告工具。"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from .shell import is_dangerous_command

_RUN_ROOT = Path("runs")


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


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


def _experiment_prepare(command: str, name: str = "experiment", config: str = "", seed: int = 42) -> str:
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
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("branch", "--show-current"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "log_path": str(directory / "train.log"),
        "error_path": str(directory / "error.log"),
        "output_path": str(directory),
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
    stdout = (directory / "train.log").open("a", encoding="utf-8")
    stderr = (directory / "error.log").open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": str(Path.cwd()), "stdout": stdout, "stderr": stderr, "shell": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(metadata["command"], **kwargs)
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


def _experiment_status(run_id: str) -> str:
    directory, metadata = _load(run_id)
    running = bool(metadata.get("pid")) and _pid_running(int(metadata["pid"]))
    if metadata.get("status") == "running" and not running:
        error_path = directory / "error.log"
        error_text = error_path.read_text(encoding="utf-8", errors="replace") if error_path.exists() else ""
        metadata["status"] = "failed" if error_text.strip() else "completed"
        metadata["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _save(directory, metadata)
    log_path = directory / "train.log"
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if log_path.exists() else ""
    return json.dumps({**metadata, "running": running, "log_tail": tail}, ensure_ascii=False, indent=2)


def _extract_metrics(text: str) -> dict[str, list[float]]:
    metrics: dict[str, list[float]] = {}
    for name, raw in re.findall(r"\b([A-Za-z][A-Za-z0-9_/-]{1,30})\s*[:=]\s*(-?\d+(?:\.\d+)?)", text):
        metrics.setdefault(name, []).append(float(raw))
    return metrics


def _experiment_report(run_id: str) -> str:
    directory, metadata = _load(run_id)
    log_path = directory / "train.log"
    error_path = directory / "error.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    error = error_path.read_text(encoding="utf-8", errors="replace") if error_path.exists() else ""
    metrics = _extract_metrics(log)
    lines = [
        f"# 实验报告：{metadata['name']}", "", f"- Run ID：`{run_id}`",
        f"- 状态：{metadata['status']}",
        f"- Git：`{metadata['git_commit']}`（{metadata['git_branch']}）",
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
    return f"报告已生成：{path}\n\n{report}"


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