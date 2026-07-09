"""grep工具"""
from __future__ import annotations
from .base import Tool

import subprocess

def _grep(pattern: str, path: str = ".", max_lines: int = 100) -> str:
    try:
        p = subprocess.run(
            ["rg", "--line-number", "--no-heading", pattern, path],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return "[失败] 未找到 rg，请先安装 ripgrep。"
    if p.returncode not in (0, 1):  # 1 = 无匹配，属正常
        return f"[grep 出错] {p.stderr.strip()}"
    lines = p.stdout.splitlines()
    if not lines:
        return f"[无匹配] pattern={pattern}"
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... [共 {len(lines)} 行，已截断前 {max_lines} 行]"
    return "\n".join(lines)

grep_tool = Tool("grep", "在文件中搜索匹配 pattern 的行（基于 ripgrep）。",
                 {"type": "object", "properties": {"pattern": {"type": "string"},
                  "path": {"type": "string"}}, "required": ["pattern"]}, _grep)