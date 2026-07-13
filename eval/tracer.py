"""Agent 运行轨迹：逐事件 JSONL，支持回放与汇总。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


class Tracer:
    def __init__(self, path: str | Path, run_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self.run_id = run_id or uuid4().hex

    def log_event(self, event: str, **payload: Any) -> None:
        record = {
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_step(self, step: int, tool_calls: list, prompt_tokens: int,
                 completion_tokens: int, note: str = "", **extra: Any) -> None:
        self.log_event(
            "step",
            step=step,
            tool_calls=tool_calls,
            note=note,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            **extra,
        )


def summarize(path: str | Path) -> dict[str, Any]:
    records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    steps = [record for record in records if record.get("event") == "step"]
    prompt_tokens = sum(step.get("prompt_tokens", 0) for step in steps)
    completion_tokens = sum(step.get("completion_tokens", 0) for step in steps)
    input_price = float(os.environ.get("OPENCLAW_INPUT_USD_PER_MILLION", "0"))
    output_price = float(os.environ.get("OPENCLAW_OUTPUT_USD_PER_MILLION", "0"))
    estimated_cost = (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
    return {
        "events": len(records),
        "steps": len(steps),
        "tool_calls": sum(len(step.get("tool_calls", [])) for step in steps),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": round(estimated_cost, 8),
        "errors": sum(1 for record in records if record.get("success") is False),
        "duration_ms": sum(step.get("duration_ms", 0) for step in steps),
    }


def replay(path: str | Path) -> None:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "step":
            names = [call.get("name", "?") for call in event.get("tool_calls", [])] or ["(无工具)"]
            print(f"step {event['step']}: {names} | {event.get('duration_ms', 0)} ms | {event.get('note', '')}")
    print(json.dumps(summarize(path), ensure_ascii=False, indent=2))