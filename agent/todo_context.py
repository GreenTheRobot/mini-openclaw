"""Thread-local todo path context shared by agent loops and todo tools."""
from __future__ import annotations

import contextlib
import contextvars
import os
from pathlib import Path
from typing import Iterator


_TODO_PATH: contextvars.ContextVar[str] = contextvars.ContextVar("mini_openclaw_todo_path", default="")


def current_todo_path(default: str | Path = ".mini-openclaw/tasks.json") -> Path:
    configured = _TODO_PATH.get().strip() or os.environ.get("MINI_OPENCLAW_TODO_PATH", "").strip()
    return Path(configured) if configured else Path(default)


@contextlib.contextmanager
def todo_path(path: str | Path) -> Iterator[None]:
    token = _TODO_PATH.set(Path(path).as_posix())
    try:
        yield
    finally:
        _TODO_PATH.reset(token)
