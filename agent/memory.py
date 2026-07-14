"""跨会话记忆：可读 Markdown 记忆与结构化键值记忆。"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from agent.sanitize import clean_text, sanitize_for_json


class MemoryLockTimeout(TimeoutError):
    """Raised when another process holds a memory lock for too long."""


@contextlib.contextmanager
def _file_lock(path: Path, timeout_seconds: float = 10.0, stale_seconds: float = 300.0):
    """Cross-process lock based on atomic lock-file creation.

    This avoids platform-specific fcntl/msvcrt code and works on Windows and
    POSIX filesystems. A stale lock can be removed after ``stale_seconds`` so a
    crashed session does not permanently block future memory writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout_seconds
    payload = json.dumps({
        "pid": os.getpid(),
        "created_at": time.time(),
        "target": str(path),
    }, ensure_ascii=False)

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, payload.encode("utf-8", errors="replace"))
            finally:
                os.close(fd)
            break
        except (FileExistsError, PermissionError):
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_seconds:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise MemoryLockTimeout(f"memory lock timeout: {lock_path}")
            time.sleep(0.02)

    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


class Memory:
    def __init__(self, path: str | Path = "MEMORY.md"):
        self.path = Path(path)

    def write(self, note: str) -> None:
        """追加一条非空记忆并立即落盘。"""
        normalized = " ".join(clean_text(note).strip().splitlines())
        if not normalized:
            raise ValueError("记忆内容不能为空")
        with _file_lock(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            prefix = "" if not self.path.exists() or self.path.stat().st_size == 0 else "\n"
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(f"{prefix}- {normalized}\n")

    def recall(self, query: str = "") -> str:
        """召回全部记忆，或按关键词筛选条目。"""
        with _file_lock(self.path):
            if not self.path.exists():
                return ""
            text = self.path.read_text(encoding="utf-8", errors="replace")
        query = clean_text(query).strip().casefold()
        if not query:
            return text
        return "\n".join(line for line in text.splitlines() if query in line.casefold())


class KVMemory:
    """JSON 键值记忆：同名 key 覆盖，支持显式遗忘。"""

    def __init__(self, path: str | Path = "memory.json"):
        self.path = Path(path)
        self.data: dict[str, Any] = self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"记忆文件不是合法 JSON：{self.path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"记忆文件顶层必须是对象：{self.path}")
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(
            json.dumps(sanitize_for_json(data), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def remember(self, key: str, value: Any) -> None:
        key = clean_text(key).strip()
        if not key:
            raise ValueError("记忆 key 不能为空")
        with _file_lock(self.path):
            data = self._read_unlocked()
            data[key] = sanitize_for_json(value)
            self._write_unlocked(data)
            self.data = dict(data)

    def forget(self, key: str) -> bool:
        key = clean_text(key)
        with _file_lock(self.path):
            data = self._read_unlocked()
            existed = key in data
            data.pop(key, None)
            self._write_unlocked(data)
            self.data = dict(data)
            return existed

    def recall(self, key: str | None = None) -> Any:
        with _file_lock(self.path):
            self.data = self._read_unlocked()
            if key is None:
                return dict(self.data)
            return self.data.get(clean_text(key))
