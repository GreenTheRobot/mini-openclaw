"""Structured, privacy-aware span tracing for mini-OpenClaw runs.

Trace files remain JSONL so an interrupted run is still inspectable.  Version 2
adds ``span_start``/``span_end`` records while retaining the old event records
for backwards compatibility with existing traces and evaluation scripts.
"""
from __future__ import annotations

import contextvars
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .sanitize import clean_text, sanitize_for_json


TRACE_SCHEMA_VERSION = 2
_ACTIVE_SPANS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("trace_spans", default=())
_SECRET_KEY_NAMES = {"api_key", "apikey", "authorization", "token", "password", "secret"}
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[^\s,;\]\}\"']+")
_API_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_KEY_VALUE_PATTERN = re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;\]\}\"']+")


def preview(value: Any, limit: int = 500) -> str:
    """Produce a compact display-safe preview without retaining full observations."""
    text = clean_text(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _safe_key(key: Any) -> str:
    return clean_text(key).lower().replace("-", "_")


def _redact_text(value: Any, workdir: Path | None = None) -> str:
    text = clean_text(value)
    if workdir:
        root = str(workdir.resolve())
        text = text.replace(root + "/", "./").replace(root, ".")
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _API_KEY_PATTERN.sub("[REDACTED]", text)
    return _KEY_VALUE_PATTERN.sub(lambda match: match.group(1) + "[REDACTED]", text)


def redact_for_trace(value: Any, workdir: Path | None = None) -> Any:
    """Sanitize text, remove known secrets, and make workdir paths relative."""
    if isinstance(value, str):
        return _redact_text(value, workdir)
    if isinstance(value, list):
        return [redact_for_trace(item, workdir) for item in value]
    if isinstance(value, tuple):
        return [redact_for_trace(item, workdir) for item in value]
    if isinstance(value, dict):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            if _safe_key(key) in _SECRET_KEY_NAMES:
                output[key] = "[REDACTED]"
            else:
                output[key] = redact_for_trace(item, workdir)
        return output
    return sanitize_for_json(value)


@dataclass
class Span:
    """Mutable handle for one started span; it is persisted exactly once on finish."""

    tracer: "Tracer"
    span_id: str
    kind: str
    name: str
    started_at: float
    attributes: dict[str, Any] = field(default_factory=dict)
    input_preview: str = ""
    _finished: bool = False

    def finish(
        self,
        *,
        status: str = "ok",
        output: Any = "",
        error: Any = "",
        usage: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        self.tracer._finish_span(
            self,
            status=status,
            output=output,
            error=error,
            usage=usage or {},
            attributes=attributes or {},
        )


class Tracer:
    """Append-only JSONL tracer with nested spans and legacy event compatibility."""

    def __init__(
        self,
        path: str | Path,
        run_id: str | None = None,
        *,
        append: bool = False,
        metadata: dict[str, Any] | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not append:
            self.path.write_text("", encoding="utf-8")
        self.run_id = run_id or uuid4().hex
        self.trace_id = self.run_id
        self.workdir: Path | None = None
        self.current_run_span_id: str | None = None
        self._run_span: Span | None = None
        self._sequence = 0
        self.metadata = dict(metadata or {})

    def _write(self, event: str, **payload: Any) -> None:
        self._sequence += 1
        record = redact_for_trace({
            "schema_version": TRACE_SCHEMA_VERSION,
            "ts": round(time.time(), 3),
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "sequence": self._sequence,
            "event": event,
            **payload,
        }, self.workdir)
        with self.path.open("a", encoding="utf-8", errors="backslashreplace") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_event(self, event: str, **payload: Any) -> None:
        """Record a non-span lifecycle event; retained for prior consumers."""
        self._write(event, **payload)

    def start_run(self, *, task: str, workdir: str | Path, **attributes: Any) -> Span:
        self.workdir = Path(workdir).resolve()
        if self.current_run_span_id:
            self.finish_run(status="interrupted", reason="new_run_started")
        run_attributes = {**self.metadata, **attributes}
        span = self.start_span(
            "agent",
            "run",
            input_value=task,
            attributes={"workdir": ".", **run_attributes},
            parent_span_id=None,
        )
        self.current_run_span_id = span.span_id
        self._run_span = span
        self.log_event("run_start", task=preview(task), workdir=".", metadata=run_attributes)
        return span

    def finish_run(self, *, status: str, reason: str = "", error: Any = "", **attributes: Any) -> None:
        if self._run_span:
            self._run_span.finish(
                status=status,
                error=error,
                attributes={"reason": reason, **attributes},
            )
            self.current_run_span_id = None
            self._run_span = None
        self.log_event("run_end", status=status, reason=reason, error=preview(error) if error else "", **attributes)

    def start_span(
        self,
        kind: str,
        name: str,
        *,
        input_value: Any = "",
        attributes: dict[str, Any] | None = None,
        parent_span_id: str | None = None,
    ) -> Span:
        parent = parent_span_id
        active = _ACTIVE_SPANS.get()
        if parent is None:
            parent = active[-1] if active else self.current_run_span_id
        span = Span(
            tracer=self,
            span_id=uuid4().hex,
            kind=kind,
            name=name,
            started_at=time.perf_counter(),
            attributes=dict(attributes or {}),
            input_preview=preview(redact_for_trace(input_value, self.workdir)),
        )
        self._write(
            "span_start",
            span_id=span.span_id,
            parent_span_id=parent,
            kind=kind,
            name=name,
            started_at=round(time.time(), 3),
            input_preview=span.input_preview,
            attributes=span.attributes,
        )
        return span

    def _finish_span(
        self,
        span: Span,
        *,
        status: str,
        output: Any,
        error: Any,
        usage: dict[str, Any],
        attributes: dict[str, Any],
    ) -> None:
        merged = {**span.attributes, **attributes}
        self._write(
            "span_end",
            span_id=span.span_id,
            kind=span.kind,
            name=span.name,
            status=status,
            ended_at=round(time.time(), 3),
            duration_ms=round((time.perf_counter() - span.started_at) * 1000, 2),
            output_preview=preview(redact_for_trace(output, self.workdir)),
            error_preview=preview(redact_for_trace(error, self.workdir)) if error else "",
            usage=usage,
            attributes=merged,
        )

    @contextmanager
    def span(self, kind: str, name: str, **kwargs: Any) -> Iterator[Span]:
        span = self.start_span(kind, name, **kwargs)
        token = _ACTIVE_SPANS.set(_ACTIVE_SPANS.get() + (span.span_id,))
        try:
            yield span
        except Exception as exc:
            span.finish(status="error", error=repr(exc))
            raise
        finally:
            if not span._finished:
                span.finish()
            _ACTIVE_SPANS.reset(token)

    def log_step(self, step: int, tool_calls: list, prompt_tokens: int,
                 completion_tokens: int, note: str = "", **extra: Any) -> None:
        """Emit the legacy model-step event alongside v2 LLM spans."""
        self.log_event(
            "step",
            step=step,
            tool_calls=tool_calls,
            note=note,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            **extra,
        )
