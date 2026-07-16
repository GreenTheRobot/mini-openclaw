"""代码搜索工具：优先 ripgrep，缺失时自动使用 Python 回退。"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .base import Tool, ToolResult

_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "runs", "traces", ".mini-openclaw"}


def _python_grep(pattern: str, path: str, max_lines: int) -> str | ToolResult:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return ToolResult(f"[grep 出错] 非法正则：{exc}", False, "invalid_regex")
    target = Path(path)
    candidates = [target] if target.is_file() else target.rglob("*")
    matches: list[str] = []
    for candidate in candidates:
        if not candidate.is_file() or any(part in _IGNORED_DIRS for part in candidate.parts):
            continue
        try:
            if candidate.stat().st_size > 2_000_000:
                continue
            lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, 1):
            if regex.search(line):
                matches.append(f"{candidate}:{line_number}:{line}")
                if len(matches) >= max_lines:
                    return "\n".join(matches) + f"\n... [已截断前 {max_lines} 行]"
    return "\n".join(matches) if matches else f"[无匹配] pattern={pattern}"


def _grep(pattern: str, path: str = ".", max_lines: int = 100) -> str | ToolResult:
    if not shutil.which("rg"):
        return _python_grep(pattern, path, max_lines)
    process = subprocess.run(
        ["rg", "--line-number", "--no-heading", "--color", "never", pattern, path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if process.returncode not in (0, 1):
        return ToolResult(f"[grep 出错] {process.stderr.strip()}", False, "grep_failed")
    lines = process.stdout.splitlines()
    if not lines:
        return f"[无匹配] pattern={pattern}"
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... [共 {len(lines)} 行，已截断前 {max_lines} 行]"
    return "\n".join(lines)


grep_tool = Tool(
    "grep", "在文件内容中搜索正则；优先使用 ripgrep，未安装时自动回退 Python。",
    {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "max_lines": {"type": "integer"}}, "required": ["pattern"], "additionalProperties": False},
    _grep,
)