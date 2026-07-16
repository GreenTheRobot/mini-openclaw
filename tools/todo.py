"""Claude Code style todo tools backed by the persistent task list."""
from __future__ import annotations

import json
from typing import Any

from agent.planning import TodoList

from .base import Tool
from .task import _task_list


def _todo_from_task_state(data: dict[str, Any]) -> TodoList:
    items = []
    for index, item in enumerate(data.get("items", []), start=1):
        items.append({
            "id": item.get("id", index),
            "text": item.get("title", ""),
            "status": item.get("status", "pending"),
        })
    return TodoList(items)


def _render_state(text: str) -> str:
    data = json.loads(text)
    rendered = _todo_from_task_state(data).render()
    return rendered or "暂无 TODO。"


def _todo_write(items: list[str]) -> str:
    task_items = [
        {"id": str(index + 1), "title": str(text).strip()}
        for index, text in enumerate(items)
        if str(text).strip()
    ]
    if not task_items:
        raise ValueError("todo_write 需要至少一个非空 TODO")
    return _render_state(_task_list("create", items=task_items))


def _update_todo(id: int, status: str) -> str:
    return _render_state(_task_list("update", task_id=str(id), status=status))


todo_write_tool = Tool(
    name="todo_write",
    description="面对多步任务时，先把它分解成有序 TODO 清单。传入子任务文本数组。",
    parameters={
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["items"],
        "additionalProperties": False,
    },
    run=_todo_write,
)

update_todo_tool = Tool(
    name="update_todo",
    description="开始、完成或阻塞某条 TODO 时更新其状态；每推进一步都要调用。",
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "status": {"type": "string", "enum": ["in_progress", "completed", "blocked"]},
        },
        "required": ["id", "status"],
        "additionalProperties": False,
    },
    run=_update_todo,
)
