"""持久化任务清单工具，用作长任务的显式 scratchpad。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .base import Tool

_STATE_PATH = Path(".mini-openclaw/tasks.json")
_ALLOWED_STATUS = {"pending", "in_progress", "completed", "failed", "blocked"}


def _load(path: Path = _STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"items": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(f"任务文件格式错误：{path}")
    return data


def _save(data: dict[str, Any], path: Path = _STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _find(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    for item in data["items"]:
        if item.get("id") == task_id:
            return item
    raise ValueError(f"没有任务：{task_id}")


def _task_list(action: str, items: list[dict[str, Any]] | None = None,
               task_id: str = "", status: str = "", result: str = "") -> str:
    data = _load()
    if action == "create":
        if not items:
            raise ValueError("create 需要非空 items")
        data = {"items": []}
        for raw in items:
            title = str(raw.get("title", "")).strip()
            if not title:
                raise ValueError("每个任务都必须有 title")
            data["items"].append({
                "id": str(raw.get("id") or f"task-{uuid4().hex[:8]}"),
                "title": title,
                "status": "pending",
                "result": "",
            })
        _save(data)
    elif action == "update":
        if status not in _ALLOWED_STATUS:
            raise ValueError(f"status 必须是 {sorted(_ALLOWED_STATUS)} 之一")
        item = _find(data, task_id)
        if status == "in_progress":
            for other in data["items"]:
                if other is not item and other.get("status") == "in_progress":
                    raise ValueError(f"已有进行中任务：{other['id']}")
        item["status"] = status
        if result:
            item["result"] = result
        _save(data)
    elif action == "list":
        data = data
    elif action == "clear":
        data = {"items": []}
        _save(data)
    else:
        raise ValueError("action 必须是 create/update/list/clear")
    return json.dumps(data, ensure_ascii=False, indent=2)


task_list_tool = Tool(
    name="task_list",
    description="为复杂任务创建、查看和更新持久化待办；同一时刻只能有一个 in_progress。",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "list", "clear"]},
            "items": {"type": "array", "items": {"type": "object"}},
            "task_id": {"type": "string"},
            "status": {"type": "string", "enum": sorted(_ALLOWED_STATUS)},
            "result": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    run=_task_list,
)