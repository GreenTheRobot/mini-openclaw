"""跨会话记忆：可读 Markdown 记忆与结构化键值记忆。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class Memory:
    def __init__(self, path: str | Path = "MEMORY.md"):
        self.path = Path(path)

    def write(self, note: str) -> None:
        """追加一条非空记忆并立即落盘。"""
        normalized = " ".join(note.strip().splitlines())
        if not normalized:
            raise ValueError("记忆内容不能为空")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not self.path.exists() or self.path.stat().st_size == 0 else "\n"
        with self.path.open("a", encoding="utf-8", newline="\n") as file:
            file.write(f"{prefix}- {normalized}\n")

    def recall(self, query: str = "") -> str:
        """召回全部记忆，或按关键词筛选条目。"""
        if not self.path.exists():
            return ""
        text = self.path.read_text(encoding="utf-8")
        query = query.strip().casefold()
        if not query:
            return text
        return "\n".join(line for line in text.splitlines() if query in line.casefold())

class KVMemory:
    """JSON 键值记忆：同名 key 覆盖，支持显式遗忘。"""
    def __init__(self, path: str | Path = "memory.json"):
        self.path = Path(path)
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"记忆文件不是合法 JSON：{self.path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"记忆文件顶层必须是对象：{self.path}")
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def remember(self, key: str, value: Any) -> None:
        key = key.strip()
        if not key:
            raise ValueError("记忆 key 不能为空")
        self.data[key] = value
        self._save()

    def forget(self, key: str) -> bool:
        existed = key in self.data
        self.data.pop(key, None)
        self._save()
        return existed

    def recall(self, key: str | None = None) -> Any:
        return self.data.get(key) if key is not None else dict(self.data)
