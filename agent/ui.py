"""User-facing rendering of observable agent events."""
from __future__ import annotations

import json
from pathlib import Path
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
        elif event == "orchestration":
            if payload.get("use_subagents"):
                self.print_fn(f"  ● 主 Agent 调度：启用子 Agent。{self._preview(payload.get('reason'))}")
            else:
                self.print_fn(f"  ● 主 Agent 判断：直接执行。{self._preview(payload.get('reason'))}")
        elif event == "main_agent_start":
            self.print_fn(f"  ● Main Agent 开始执行：{self._preview(payload.get('task'))}")
        elif event == "main_agent_done":
            self.print_fn("  ✓ Main Agent 完成。")
        elif event == "subagent_start":
            self.print_fn(
                f"  ● {payload.get('role', 'Subagent')} 开始：{self._preview(payload.get('assignment'))}"
            )
        elif event == "subagent_done":
            self.print_fn(f"  ✓ {payload.get('role', 'Subagent')} 完成。")
        elif event == "synthesis_start":
            self.print_fn("  ● 主 Agent 正在综合子 Agent 结果。")
        elif event == "synthesis_done":
            self.print_fn("  ✓ 综合完成。")
        elif event == "review_start":
            self.print_fn("  ● Reviewer 正在后台做质量检查。")
        elif event == "review_done":
            status = "需要修订" if payload.get("needs_revision") else "通过"
            self.print_fn(f"  ✓ Reviewer 检查完成：{status}。")
        elif event == "revision_start":
            self.print_fn("  ● 主 Agent 正在按内部审核意见修订最终答案。")
        elif event == "research_answer_repair":
            self.print_fn("  ● 调研答案缺少必要来源或结构，正在重写最终报告。")
        elif event == "tool_result" and not payload.get("success"):
            role = f"{payload.get('role')} 的 " if payload.get("role") else ""
            self.print_fn(f"  ⚠ {role}{payload.get('name')} 失败，智能体正在尝试恢复。")
        elif event == "compaction":
            self.print_fn("  ● 已压缩较早的对话历史并保留关键约束。")
        elif event == "protocol_repaired":
            self.print_fn("  ⚠ 已安全修复一组不完整的历史工具消息。")

    def _render_verbose(self, event: str, payload: dict[str, Any]) -> None:
        role = f"{payload.get('role')} " if payload.get("role") else ""
        if event == "model_start":
            self.print_fn(f"\n[model] {role}第 {payload.get('turn')} 轮模型调用")
        elif event == "tool_start":
            arguments = json.dumps(payload.get("arguments"), ensure_ascii=False)
            if len(arguments) > 180:
                arguments = arguments[:177] + "..."
            self.print_fn(f"\n[tool] {role}{payload.get('name')} {arguments}")
        elif event == "tool_result":
            status = "ok" if payload.get("success") else "error"
            observation = str(payload.get("observation", "")).replace("\n", " ")
            if len(observation) > 220:
                observation = observation[:217] + "..."
            self.print_fn(f"[{status}] {role}{payload.get('name')}: {observation}")
        elif event == "orchestration":
            decision = "启用子 Agent" if payload.get("use_subagents") else "直接执行"
            self.print_fn(f"[orchestration] {decision}: {payload.get('reason', '')}")
        elif event == "main_agent_start":
            self.print_fn(f"[main] start: {self._preview(payload.get('task'))}")
        elif event == "main_agent_done":
            self.print_fn("[main] done")
        elif event == "subagent_start":
            self.print_fn(
                f"[subagent] {payload.get('role', 'Subagent')} start: "
                f"{self._preview(payload.get('assignment'), 160)}"
            )
        elif event == "subagent_done":
            self.print_fn(f"[subagent] {payload.get('role', 'Subagent')} done")
        elif event == "synthesis_start":
            self.print_fn("[synthesis] start")
        elif event == "synthesis_done":
            self.print_fn("[synthesis] done")
        elif event == "review_start":
            self.print_fn("[review] start")
        elif event == "review_done":
            status = "needs_revision" if payload.get("needs_revision") else "passed"
            self.print_fn(f"[review] {status}")
        elif event == "revision_start":
            self.print_fn("[revision] start")
        elif event == "research_answer_repair":
            self.print_fn(f"[research-report] repair attempt {payload.get('attempt')}")
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

    @staticmethod
    def _preview(value: Any, limit: int = 72) -> str:
        text = str(value or "").replace("\n", " ").strip()
        if len(text) > limit:
            return text[:limit - 3] + "..."
        return text

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

    def load_trace_steps(self, paths: list[str | Path], max_events: int = 80,
                         since_ts: float | None = None) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        for path in paths:
            trace_path = Path(path)
            if not trace_path.exists():
                continue
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") != "tool_result":
                    continue
                if since_ts is not None and float(record.get("ts", 0)) < since_ts:
                    continue
                events.append((
                    "tool_start",
                    {
                        "name": record.get("tool"),
                        "arguments": record.get("arguments"),
                    },
                ))
                events.append((
                    "tool_result",
                    {
                        "name": record.get("tool"),
                        "success": bool(record.get("success")),
                        "observation": record.get("observation", ""),
                    },
                ))
        self.last_events = events[-max_events:]

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
