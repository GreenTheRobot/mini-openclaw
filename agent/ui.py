"""User-facing rendering of observable agent events."""
from __future__ import annotations

import json
from typing import Any, Callable


class EventRenderer:
    """Keep full observable events while showing either quiet or verbose output."""

    def __init__(self, print_fn: Callable[[str], None], *, verbose: bool = False):
        self.print_fn = print_fn
        self.verbose = verbose
        self.last_events: list[tuple[str, dict[str, Any]]] = []
        self._working_shown = False

    def begin_turn(self) -> None:
        self.last_events = []
        self._working_shown = False

    def set_verbose(self, enabled: bool) -> None:
        self.verbose = enabled

    def __call__(self, event: str, payload: dict[str, Any]) -> None:
        self.last_events.append((event, dict(payload)))
        if self.verbose:
            self._render_verbose(event, payload)
            return
        if event == "model_start" and not self._working_shown:
            self.print_fn("\n● 正在处理，请稍候...")
            self._working_shown = True
        elif event == "tool_result" and not payload.get("success"):
            self.print_fn(f"  ⚠ {payload.get('name')} 失败，智能体正在尝试恢复。")
        elif event == "compaction":
            self.print_fn("  ● 已压缩较早的对话历史并保留关键约束。")
        elif event == "protocol_repaired":
            self.print_fn("  ⚠ 已安全修复一组不完整的历史工具消息。")

    def _render_verbose(self, event: str, payload: dict[str, Any]) -> None:
        if event == "model_start":
            self.print_fn(f"\n[model] 第 {payload.get('turn')} 轮模型调用")
        elif event == "tool_start":
            arguments = json.dumps(payload.get("arguments"), ensure_ascii=False)
            if len(arguments) > 180:
                arguments = arguments[:177] + "..."
            self.print_fn(f"\n[tool] {payload.get('name')} {arguments}")
        elif event == "tool_result":
            status = "ok" if payload.get("success") else "error"
            observation = str(payload.get("observation", "")).replace("\n", " ")
            if len(observation) > 220:
                observation = observation[:217] + "..."
            self.print_fn(f"[{status}] {payload.get('name')}: {observation}")
        elif event == "context_loaded":
            self.print_fn(f"[context] 已加载 {payload.get('key')}")
        elif event == "compaction":
            self.print_fn(
                f"[context] 已压缩历史：约 {payload.get('before')} → {payload.get('after')} tokens"
            )
        elif event == "protocol_repaired":
            self.print_fn(f"[context] 已修复消息协议：{payload.get('errors')}")
        elif event == "session_reset":
            self.print_fn("[session] 对话上下文和临时授权已清空，磁盘记忆保持不变。")

    def steps_markdown(self) -> str:
        calls: list[dict[str, Any]] = []
        for event, payload in self.last_events:
            if event == "tool_start":
                calls.append({
                    "name": payload.get("name", "?"),
                    "arguments": payload.get("arguments"),
                    "success": None,
                    "observation": "",
                })
            elif event == "tool_result":
                for call in reversed(calls):
                    if call["success"] is None and call["name"] == payload.get("name"):
                        call["success"] = bool(payload.get("success"))
                        call["observation"] = str(payload.get("observation", ""))
                        break
        if not calls:
            return "上一轮没有调用工具。"
        lines = ["# 上一轮可观察执行步骤", ""]
        for index, call in enumerate(calls, 1):
            state = "成功" if call["success"] else "失败"
            args = json.dumps(call["arguments"], ensure_ascii=False)
            observation = call["observation"].replace("\n", " ")
            if len(args) > 240:
                args = args[:237] + "..."
            if len(observation) > 320:
                observation = observation[:317] + "..."
            lines.extend([
                f"{index}. **{call['name']}** — {state}",
                f"   - 参数：`{args}`",
                f"   - 结果：{observation or '无可见输出'}",
            ])
        return "\n".join(lines)

    def audit_evidence(self, max_chars: int = 5000) -> str:
        rows = []
        for event, payload in self.last_events:
            if event != "tool_result":
                continue
            observation = str(payload.get("observation", ""))[:600]
            rows.append({
                "tool": payload.get("name"),
                "success": bool(payload.get("success")),
                "observation": observation,
            })
        text = json.dumps(rows, ensure_ascii=False, indent=2)
        return text[:max_chars]