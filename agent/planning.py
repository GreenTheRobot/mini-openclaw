"""TodoList planning state machine used by the planning tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TODO_STATUSES = {"pending", "in_progress", "completed", "blocked", "failed"}


@dataclass
class TodoItem:
    id: int
    text: str
    status: str = "pending"


class TodoList:
    """Small ordered todo state machine.

    The class mirrors the Day 8 course API. The default tools persist the same
    shape through ``tools.task`` so compaction and final-answer checks can use a
    single source of truth.
    """

    def __init__(self, items: list[dict[str, Any]] | None = None):
        self.items: list[dict[str, Any]] = []
        if items:
            self.items = [self._normalize_item(item, index + 1) for index, item in enumerate(items)]

    @staticmethod
    def _normalize_item(item: dict[str, Any], fallback_id: int) -> dict[str, Any]:
        raw_id = item.get("id", fallback_id)
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            item_id = fallback_id
        text = str(item.get("text") or item.get("title") or "").strip()
        status = str(item.get("status", "pending"))
        if status not in TODO_STATUSES:
            status = "pending"
        return {"id": item_id, "text": text, "status": status}

    def write(self, texts: list[str]) -> None:
        self.items = [
            {"id": index + 1, "text": str(text).strip(), "status": "pending"}
            for index, text in enumerate(texts)
            if str(text).strip()
        ]

    def update(self, id: int, status: str) -> None:
        if status not in TODO_STATUSES:
            raise ValueError(f"status 必须是 {sorted(TODO_STATUSES)} 之一")
        for item in self.items:
            if item["id"] == id:
                item["status"] = status
                return
        raise ValueError(f"没有 TODO：{id}")

    def insert(self, text: str) -> None:
        text = text.strip()
        if not text:
            raise ValueError("TODO 文本不能为空")
        self.items.append({"id": len(self.items) + 1, "text": text, "status": "pending"})

    def render(self) -> str:
        mark = {
            "pending": "[ ]",
            "in_progress": "[~]",
            "completed": "[x]",
            "blocked": "[!]",
            "failed": "[!]",
        }
        return "\n".join(
            f"{mark.get(item['status'], '[ ]')} {item['id']} {item['text']}"
            for item in self.items
        )

    def all_done(self) -> bool:
        return bool(self.items) and all(item["status"] == "completed" for item in self.items)
