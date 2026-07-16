"""忽略依赖与运行产物的文件查找工具。"""
from __future__ import annotations

from pathlib import Path
from .base import Tool, ToolResult

_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "runs", "traces", ".mini-openclaw"}


def _glob(pattern: str, path: str = ".", max_items: int = 100) -> str | ToolResult:
    """Find files below a relative directory without leaving the workdir."""
    pattern_path = Path(pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        return ToolResult("[glob 出错] pattern 不允许越过工作目录", False, "pattern_outside_workdir")
    root = Path(path or ".")
    workdir = Path.cwd().resolve()
    if root.is_absolute() or ".." in root.parts:
        return ToolResult("[glob 出错] path 必须是工作目录内的相对路径", False, "path_outside_workdir")
    resolved_root = (workdir / root).resolve()
    try:
        resolved_root.relative_to(workdir)
    except ValueError:
        return ToolResult("[glob 出错] path 不能越过工作目录", False, "path_outside_workdir")
    if not resolved_root.exists():
        return ToolResult(f"[glob 出错] path 不存在：{path}", False, "path_not_found")
    if not resolved_root.is_dir():
        return ToolResult(f"[glob 出错] path 必须是目录：{path}", False, "invalid_path")
    paths = sorted(
        str(candidate.relative_to(workdir)) for candidate in resolved_root.rglob(pattern)
        if candidate.is_file() and not any(part in _IGNORED_DIRS for part in candidate.relative_to(workdir).parts)
    )
    if not paths:
        return f"[无匹配] path={path} pattern={pattern}"
    if len(paths) > max_items:
        return "\n".join(paths[:max_items]) + f"\n... [共 {len(paths)} 个，已截断前 {max_items} 个]"
    return "\n".join(paths)


glob_tool = Tool(
    "glob", "在工作目录内指定的相对 path 下按通配模式递归查找文件；默认 path 为 .，跳过依赖、Git 和运行产物目录。",
    {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "max_items": {"type": "integer"}}, "required": ["pattern"], "additionalProperties": False},
    _glob,
)
