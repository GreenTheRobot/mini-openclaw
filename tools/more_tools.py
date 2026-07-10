"""完整工具集：edit / grep / glob（Day6，→ v1）+ web_fetch / task_list（Day7）。

每个工具上午讲设计权衡，下午实现。这里只给签名与 TODO，便于你拆到独立文件。
建议最终拆成 edit.py / search.py / web.py / todo.py，再在 base.build_default_registry 注册。
"""
from __future__ import annotations
from .base import Tool
from .edit import _edit, edit_tool
from .grep import _grep, grep_tool
from .glob import _glob, glob_tool
from .web import _web_fetch, web_fetch_tool


# --- edit：三种策略权衡（整文件重写 / unified diff / search-replace）---
# 已拆分到 edit.py；这里保留 edit_tool 导出，兼容旧的导入路径。
# --- grep：基于 ripgrep ---
# --- glob：按文件名模式找文件 ---
# --- web_fetch：URL -> markdown，控 token 预算 ---


# --- task_list（TodoWrite）：自维护待办，提升长任务成功率 ---
def _task_list(action: str, items: list | None = None) -> str:
    # TODO[Day7] 维护一个结构化待办（add/update/complete），作为模型的 scratchpad
    raise NotImplementedError("Day7：实现 task_list")





task_list_tool = Tool("task_list", "维护任务待办清单（add/update/complete）。",
                      {"type": "object", "properties": {"action": {"type": "string"},
                       "items": {"type": "array"}}, "required": ["action"]}, _task_list)
