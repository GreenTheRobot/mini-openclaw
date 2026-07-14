"""Helpers for removing lone Unicode surrogates from external text."""
from __future__ import annotations

from typing import Any


def clean_text(text: Any) -> str:
    """Return text that can always be encoded as UTF-8."""
    value = str(text)
    if not any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        return value
    return "".join(
        "\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char
        for char in value
    )


def sanitize_for_json(value: Any) -> Any:
    """Recursively clean values before JSON, HTTP, trace, or terminal output."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, dict):
        return {
            clean_text(key) if isinstance(key, str) else key: sanitize_for_json(item)
            for key, item in value.items()
        }
    return value
