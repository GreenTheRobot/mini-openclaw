"""基于唯一原文匹配的局部编辑工具。"""
from __future__ import annotations

from pathlib import Path
from .base import Tool, ToolResult


def _edit(path: str, old: str = "", new: str = "") -> str | ToolResult:
    if not old:
        return ToolResult("[失败] old 不能为空；请先 read 文件并提供唯一原文。", False, "empty_old")
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        return ToolResult(f"[失败] 未找到待替换文本，请照抄文件原文（含缩进）。path={path}", False, "old_not_found")
    if count > 1:
        return ToolResult(f"[失败] old 在文件中出现 {count} 次，不唯一；请扩大 old 片段。", False, "old_not_unique")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"已在 {path} 完成 1 处替换。"


edit_tool = Tool(
    "edit", "先 read，再把唯一 old 原文替换为 new；匹配零处或多处都不会修改。",
    {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"], "additionalProperties": False},
    _edit,
)