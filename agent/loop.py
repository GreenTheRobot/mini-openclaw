"""ReAct 主循环：规划、校验、权限、错误恢复、压缩与可观测性。"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from backend.multimodal import image_block
from agent.context import estimate_tokens, has_open_tasks, maybe_compact, task_state_snapshot, truncate_observation
from tools.base import ToolRegistry, ToolResult, normalize_tool_result
from . import permissions


class AgentLoop:
    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 20, workdir: str | Path | None = None,
                 auto_approve: bool = False,
                 confirm_callback: Callable[[str, dict[str, Any], permissions.PermissionDecision], bool] | None = None,
                 tracer: Any | None = None, max_consecutive_errors: int = 4,
                 max_repeated_call: int = 3,
                 event_callback: Callable[[str, dict[str, Any]], None] | None = None,
                 context_budget: int = 6000):
        self.backend = backend
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.workdir = Path(workdir or os.getcwd()).resolve()
        self.auto_approve = auto_approve
        self.confirm_callback = confirm_callback
        self.tracer = tracer
        self.event_callback = event_callback
        self.context_budget = context_budget
        self.messages: list[dict[str, Any]] = []
        self.loaded_contexts: set[str] = set()
        self.max_consecutive_errors = max_consecutive_errors
        self.max_repeated_call = max_repeated_call
        self.last_run_status = "not_started"

    def _emit(self, event: str, **payload: Any) -> None:
        if self.event_callback:
            self.event_callback(event, payload)

    def reset(self) -> None:
        """清空当前对话上下文，但保留磁盘记忆和任务文件。"""
        self.messages = []
        self.loaded_contexts = set()
        self.last_run_status = "not_started"
        self._emit("session_reset")

    def add_context(self, key: str, content: str) -> None:
        """在交互会话中按需注入一次 Skill/约束。"""
        if not content.strip() or key in self.loaded_contexts:
            return
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        # 保持唯一 system 消息位于会话开头，兼容 OpenAI/DeepSeek 消息协议。
        self.messages[0]["content"] = str(self.messages[0].get("content", "")).rstrip() + "\n\n" + content
        self.loaded_contexts.add(key)
        self._emit("context_loaded", key=key)
    def _run_tool(self, tool: Any, arguments: dict[str, Any]) -> ToolResult:
        old_cwd = os.getcwd()
        os.chdir(self.workdir)
        try:
            return normalize_tool_result(tool.run(**arguments))
        finally:
            os.chdir(old_cwd)

    def _observation_content(self, step: int, tool: str, text: str) -> str:
        if len(text) <= 4000:
            return text
        directory = self.workdir / ".mini-openclaw" / "observations"
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(char if char.isalnum() or char in "-_" else "-" for char in tool)
        path = directory / f"step-{step:02d}-{safe_name}.txt"
        path.write_text(text, encoding="utf-8")
        return truncate_observation(text, archive_path=str(path.relative_to(self.workdir)))
    @staticmethod
    def _error(tool: str, category: str, message: str,
               recoverable: bool = True, suggestion: str = "修正后重试") -> str:
        return (
            "[TOOL_ERROR]\n"
            f"tool: {tool}\ncategory: {category}\nmessage: {message}\n"
            f"recoverable: {str(recoverable).lower()}\nsuggestion: {suggestion}"
        )

    def run(self, user_task: str, image_paths: list[str] | None = None) -> str:
        self.last_run_status = "running"
        user_content: Any = user_task
        if image_paths:
            user_content = [{"type": "text", "text": user_task}]
            user_content.extend(image_block(path) for path in image_paths)
        if not self.messages:
            self.messages = [{"role": "system", "content": self.system_prompt}]
        self.messages.append({"role": "user", "content": user_content})
        messages = self.messages
        repeated_calls: Counter[str] = Counter()
        consecutive_errors = 0
        used_task_list = any(message.get("role") == "tool" and message.get("name") == "task_list" for message in messages)
        if self.tracer:
            self.tracer.log_event("run_start", task=user_task, workdir=str(self.workdir))

        for turn in range(self.max_turns):
            tools = self.registry.schemas() if getattr(self.backend, "supports_tools", True) else []
            started = time.perf_counter()
            self._emit("model_start", turn=turn + 1)
            assistant = self.backend.chat(messages, tools=tools)
            self._emit("model_end", turn=turn + 1)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            tool_calls = assistant.get("tool_calls") or []
            messages.append({
                "role": "assistant",
                "content": assistant.get("content", ""),
                "tool_calls": tool_calls,
            })
            if self.tracer:
                usage = assistant.get("usage") or {}
                self.tracer.log_step(
                    turn + 1, tool_calls, usage.get("prompt_tokens", estimate_tokens(messages[:-1])),
                    usage.get("completion_tokens", len(str(assistant.get("content", ""))) // 4),
                    duration_ms=duration_ms, success=True, note="model_response",
                )
            if not tool_calls:
                answer = str(assistant.get("content", "")).strip()
                if used_task_list and has_open_tasks(self.workdir):
                    snapshot = task_state_snapshot(self.workdir)
                    messages.append({
                        "role": "user",
                        "content": (
                            "你刚才准备给最终答复，但权威 task_list 仍显示有未完成项。\n\n"
                            f"{snapshot}\n\n"
                            "请继续执行未完成项；只有所有 task_list 项都不是 pending/in_progress 后，"
                            "才能给最终完成答复。"
                        ),
                    })
                    self._emit("final_blocked", reason="open_task_list", turn=turn + 1)
                    if self.tracer:
                        self.tracer.log_event("final_blocked", reason="open_task_list", turn=turn + 1)
                    continue
                self._emit("answer", content=assistant.get("content", ""), turn=turn + 1)
                self.last_run_status = "success" if answer else "failed"
                if self.tracer:
                    self.tracer.log_event("run_end", status=self.last_run_status, turns=turn + 1)
                return answer or "[模型未返回内容，任务失败]"

            for call in tool_calls:
                call_name = str(call.get("name", ""))
                if call_name == "task_list":
                    used_task_list = True
                arguments = call.get("arguments")
                tool = self.registry.get(call_name)
                success = False
                tool_started = time.perf_counter()
                self._emit("tool_start", name=call_name, arguments=arguments)
                if tool is None:
                    obs = self._error(call_name, "unknown_tool", "工具未注册")
                elif not isinstance(arguments, dict):
                    obs = self._error(call_name, "invalid_arguments", "arguments 必须是对象")
                else:
                    validation_errors = tool.validate(arguments)
                    signature = json.dumps([call_name, arguments], ensure_ascii=False, sort_keys=True)
                    repeated_calls[signature] += 1
                    if validation_errors:
                        obs = self._error(call_name, "schema_validation", "; ".join(validation_errors))
                    elif repeated_calls[signature] > self.max_repeated_call:
                        obs = self._error(
                            call_name, "repeated_call",
                            f"相同调用已出现 {repeated_calls[signature]} 次",
                            suggestion="检查已有 observation，改变参数或结束任务",
                        )
                    else:
                        try:
                            decision = permissions.check(call_name, arguments, self.workdir)
                            if decision.verdict == "deny":
                                obs = self._error(call_name, "permission_denied", decision.reason, False, "请求用户调整任务")
                            elif decision.verdict == "confirm":
                                approved = self.auto_approve
                                if not approved and self.confirm_callback:
                                    approved = self.confirm_callback(call_name, arguments, decision)
                                if not approved:
                                    obs = self._error(call_name, "confirmation_required", decision.reason, True, "请求用户确认或选择只读方案")
                                else:
                                    result = self._run_tool(tool, arguments)
                                    success = result.success
                                    obs = result.content if success else self._error(call_name, result.category, result.content)
                            else:
                                result = self._run_tool(tool, arguments)
                                success = result.success
                                obs = result.content if success else self._error(call_name, result.category, result.content)
                        except Exception as exc:  # 工具错误必须回填给模型自修复
                            obs = self._error(call_name, "execution_error", str(exc))
                consecutive_errors = 0 if success else consecutive_errors + 1
                messages.append({
                    "role": "tool",
                    "name": call_name,
                    "tool_call_id": call.get("id") or call_name,
                    "content": self._observation_content(turn + 1, call_name, str(obs)),
                })
                self._emit("tool_result", name=call_name, success=success, observation=str(obs))
                if self.tracer:
                    self.tracer.log_event(
                        "tool_result", step=turn + 1, tool=call_name, arguments=arguments,
                        success=success,
                        duration_ms=round((time.perf_counter() - tool_started) * 1000, 2),
                        observation=str(obs)[:1000],
                    )
                if consecutive_errors >= self.max_consecutive_errors:
                    self.last_run_status = "failed"
                    result = f"[连续 {consecutive_errors} 次工具失败，已安全终止；请检查 Trace]"
                    if self.tracer:
                        self.tracer.log_event("run_end", status="failed", reason="consecutive_errors")
                    return result
            tokens_before = estimate_tokens(messages)
            compacted = maybe_compact(messages, self.backend, budget=self.context_budget, workdir=self.workdir)
            if compacted is not messages:
                tokens_after = estimate_tokens(compacted)
                self._emit("compaction", before=tokens_before, after=tokens_after)
                if self.tracer:
                    self.tracer.log_event("compaction", before=tokens_before, after=tokens_after)
            messages = compacted
            self.messages = messages

        self.last_run_status = "partial"
        if self.tracer:
            self.tracer.log_event("run_end", status="partial", reason="max_turns")
        return "[达到最大轮数上限，任务未完全完成；请查看任务清单与 Trace]"
