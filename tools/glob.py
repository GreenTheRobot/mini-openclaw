"""文件寻找工具（glob）。"""
from __future__ import annotations
from .base import Tool

from pathlib import Path

def _glob(pattern: str, max_items: int = 100) -> str:
    paths = [str(p) for p in Path(".").rglob(pattern) if p.is_file()]
    if not paths:
        return f"[无匹配] pattern={pattern}"
    if len(paths) > max_items:
        return "\n".join(paths[:max_items]) + f"\n... [共 {len(paths)} 个，已截断前 {max_items} 个]"
    return "\n".join(paths)

glob_tool = Tool("glob", "按通配模式查找文件路径。",
                 {"type": "object", "properties": {"pattern": {"type": "string"}},
                  "required": ["pattern"]}, _glob)