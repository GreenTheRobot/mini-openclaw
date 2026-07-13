"""忽略依赖与运行产物的文件查找工具。"""
from __future__ import annotations

from pathlib import Path
from .base import Tool

_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "runs", "traces", ".mini-openclaw"}


def _glob(pattern: str, max_items: int = 100) -> str:
    paths = sorted(
        str(path) for path in Path(".").rglob(pattern)
        if path.is_file() and not any(part in _IGNORED_DIRS for part in path.parts)
    )
    if not paths:
        return f"[无匹配] pattern={pattern}"
    if len(paths) > max_items:
        return "\n".join(paths[:max_items]) + f"\n... [共 {len(paths)} 个，已截断前 {max_items} 个]"
    return "\n".join(paths)


glob_tool = Tool(
    "glob", "按通配模式递归查找项目文件，默认跳过依赖、Git 和运行产物目录。",
    {"type": "object", "properties": {"pattern": {"type": "string"}, "max_items": {"type": "integer"}}, "required": ["pattern"], "additionalProperties": False},
    _glob,
)