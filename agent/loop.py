"""ReAct 主循环：规划、校验、权限、错误恢复、压缩与可观测性。"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from backend.multimodal import image_block
from agent.context import (
    estimate_tokens, maybe_compact, repair_tool_protocol, truncate_observation,
    validate_tool_protocol,
)
from tools.base import ToolRegistry, ToolResult, normalize_tool_result
from . import permissions

RESEARCH_TASK_HINTS = (
    "网页", "项目", "论文", "paper", "arxiv", "github", "仓库",
    "方法", "思路", "讲解", "文献", "阅读", "调研", "总结",
)
STATUS_ONLY_ANSWER_HINTS = (
    "task_list", "待办事项", "当前任务清单", "请问您希望我下一步",
    "可能的后续方向", "如果您有新的需求", "我将继续为您服务",
)
RESEARCH_REPORT_TERMS = ("项目", "论文", "github", "仓库", "方法", "思路", "来源", "链接")
LITERATURE_TASK_HINTS = (
    "找论文", "查论文", "新论文", "最新论文", "最近一周", "近期论文",
    "文献检索", "论文检索", "paper search", "find papers", "recent papers",
)
LITERATURE_REPORT_TERMS = ("严格匹配", "提交日期", "摘要", "解决问题", "核心方法", "来源")
REUSABLE_OBSERVATION_TOOLS = {"arxiv_search", "web_fetch", "web_search", "read", "grep", "glob"}
RESEARCH_DISCOVERY_TOOLS = {"arxiv_search", "web_fetch", "web_search"}
NETWORK_PROBE_HINTS = ("curl", "wget", "requests", "httpx", "urllib", "http://", "https://")


def _needs_research_report(user_task: str) -> bool:
    lowered = user_task.lower()
    return any(hint.lower() in lowered for hint in RESEARCH_TASK_HINTS)


def _needs_literature_report(user_task: str) -> bool:
    lowered = user_task.lower()
    return any(hint.lower() in lowered for hint in LITERATURE_TASK_HINTS)


def _literature_delivery_requirements(user_task: str) -> str:
    if not _needs_literature_report(user_task):
        return ""
    return (
        "\n\n这是文献检索任务。正文必须以论文结果为主体，不要复述逐轮搜索过程。"
        "先给出明确日期区间和严格匹配数量；每篇严格匹配论文使用论文卡片，包含标题、作者、"
        "提交日期、研究方向、摘要概括、解决问题、核心方法、主要贡献/结论和来源链接。"
        "时间范围外或弱相关条目只能放在独立的‘扩展相关工作’中。"
        "最后用不超过五行的‘检索说明’概括数据源和关键词。严格匹配为零时明确说明，不得用旧论文凑数。"
    )


def _is_insufficient_research_answer(user_task: str, answer: str) -> bool:
    if not _needs_research_report(user_task):
        return False
    lowered = answer.lower()
    if any(hint.lower() in lowered for hint in STATUS_ONLY_ANSWER_HINTS):
        return True
    if len(answer.strip()) < 320:
        return True
    has_source_link = "http://" in lowered or "https://" in lowered or "arxiv:" in lowered
    if _needs_literature_report(user_task):
        covered = sum(1 for term in LITERATURE_REPORT_TERMS if term in answer)
        return not has_source_link or covered < 5
    covered_terms = sum(1 for term in RESEARCH_REPORT_TERMS if term in lowered)
    return not has_source_link or covered_terms < 3


def _research_answer_repair_prompt(user_task: str, answer: str) -> str:
    return (
        "你刚才准备给最终答复，但该答复不满足科研智能体对调研类任务的交付要求。\n"
        f"用户原始任务：\n{user_task}\n\n上一版答复：\n{answer}\n\n"
        "不要只报告 task_list、历史压缩备忘、搜索流水账或下一步建议。请直接交付科研调研结果。"
        "证据足够时整理成结构化最终报告；证据不足时明确标注缺口。\n\n"
        "一般项目报告至少包含：项目解决的问题；官网、论文和 GitHub 来源链接；方法流程和关键思路；"
        "创新点、局限性或适用场景；以及每项结论的信息依据。没有找到的内容必须明确说明。"
        + _literature_delivery_requirements(user_task)
    )

class AgentLoop:
    _FAILURE_SUGGESTIONS = {
        "http_not_found": "该地址不存在；回到真实目录列表或父级 API，不要继续猜测相邻路径",
        "network_timeout": "不要重复等待同一地址；改用已有来源或其他已授权入口",
        "http_client_error": "检查 URL；不要用猜测路径连续重试",
        "http_server_error": "服务端暂时异常；优先基于已有证据回答并标记未核验项",
        "network_error": "检查网络或换用已有来源；避免原样重复调用",
        "permission_denied": "说明权限限制并请求用户调整任务，不得绕过",
        "confirmation_required": "说明操作尚未获批，改用只读方案或等待确认",
    }

    def __init__(self, backend: Any, registry: ToolRegistry, system_prompt: str,
                 max_turns: int = 20, workdir: str | Path | None = None,
                 auto_approve: bool = False,
                 confirm_callback: Callable[[str, dict[str, Any], permissions.PermissionDecision], bool] | None = None,
                 tracer: Any | None = None, max_consecutive_errors: int = 4,
                 max_repeated_call: int = 3,
                 event_callback: Callable[[str, dict[str, Any]], None] | None = None,
                 context_budget: int = 20000,
                 max_research_calls: int = 12,
                 permission_manager: permissions.PermissionManager | None = None):
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
        self.max_research_calls = max_research_calls
        self.permission_manager = permission_manager or permissions.PermissionManager()
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
        self.permission_manager.reset_session()
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

    def _tool_error(self, tool: str, result: ToolResult,
                    arguments: dict[str, Any] | None = None) -> str:
        suggestion = self._FAILURE_SUGGESTIONS.get(
            result.category, "阅读错误后改变参数、工具或结束任务",
        )
        arguments = arguments or {}
        if tool == "bash" and result.category == "sandbox_denied":
            command = str(arguments.get("command", "")).lower()
            if any(hint in command for hint in NETWORK_PROBE_HINTS):
                suggestion = (
                    "不要用 bash、curl、wget、requests 或 httpx 联网；"
                    "改用 web_search/web_fetch，或基于已有 observation 回答"
                )
        elif tool in {"arxiv_search", "web_fetch", "web_search"}:
            suggestion = (
                "先检查已有 observation，不要重复相同 URL/查询；"
                "改换查询或来源，仍不可用时明确报告限制"
            )
        return self._error(tool, result.category, result.content, suggestion=suggestion)

    def _finalize_without_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        user_task: str,
        successful_tools: int,
        fallback_turn: int,
        reason: str,
        heading: str,
    ) -> str:
        """Stop exploration and synthesize a user-facing answer from existing evidence."""
        messages.append({
            "role": "user",
            "content": (
                f"# {heading}\n"
                f"此前已有 {successful_tools} 次成功工具结果。现在禁止继续调用工具。"
                "请直接基于已有 observation 回答用户原始问题，明确区分已验证事实、未验证部分和失败原因。"
                "不得只回复任务状态、搜索过程、达到轮数上限或下一步建议。"
                + _literature_delivery_requirements(user_task)
            ),
        })
        content = ""
        attempts_used = 0
        for attempt in range(2):
            attempts_used = attempt + 1
            current_turn = fallback_turn + attempt
            started = time.perf_counter()
            self._emit("model_start", turn=current_turn, mode=reason)
            try:
                assistant = self.backend.chat(messages, tools=[])
            except Exception as exc:
                self.last_run_status = "failed"
                if self.tracer:
                    self.tracer.log_event(
                        "run_end", status="failed", reason=f"{reason}_backend_error", error=str(exc),
                    )
                return f"[工具探索已停止，但证据总结失败：{exc}]"
            self._emit("model_end", turn=current_turn, mode=reason)
            content = str(assistant.get("content", "")).strip()
            messages.append({"role": "assistant", "content": content, "tool_calls": []})
            if self.tracer:
                usage = assistant.get("usage") or {}
                self.tracer.log_step(
                    current_turn, [],
                    usage.get("prompt_tokens", estimate_tokens(messages[:-1])),
                    usage.get("completion_tokens", len(content) // 4),
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    success=bool(content), note=f"summary_after_{reason}",
                )
            if attempt == 0 and _is_insufficient_research_answer(user_task, content):
                messages.append({
                    "role": "user",
                    "content": _research_answer_repair_prompt(user_task, content)
                    + "\n禁止继续调用工具；仅重写为合格的最终报告。",
                })
                continue
            break
        self.messages = messages
        self.last_run_status = "partial" if content else "failed"
        if self.tracer:
            self.tracer.log_event(
                "run_end", status=self.last_run_status, reason=reason,
                turns=fallback_turn + attempts_used - 1,
            )
        return content or "[工具探索已停止，模型未能生成部分报告]"

    def _finalize_after_tool_errors(
        self,
        messages: list[dict[str, Any]],
        *,
        user_task: str,
        turn: int,
        consecutive_errors: int,
        successful_tools: int,
        error_categories: list[str],
    ) -> str:
        self._emit(
            "error_budget_exhausted",
            consecutive_errors=consecutive_errors,
            successful_tools=successful_tools,
            categories=error_categories,
        )
        if self.tracer:
            self.tracer.log_event(
                "error_budget_exhausted",
                consecutive_errors=consecutive_errors,
                successful_tools=successful_tools,
                categories=error_categories,
            )
        return self._finalize_without_tools(
            messages,
            user_task=user_task,
            successful_tools=successful_tools,
            fallback_turn=turn + 2,
            reason="tool_error_budget",
            heading=f"连续 {consecutive_errors} 次工具失败，停止探索并交付结果",
        )
    def run(self, user_task: str, image_paths: list[str] | None = None) -> str:
        self.permission_manager.begin_task()
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
        reusable_observations: dict[str, str] = {}
        consecutive_errors = 0
        successful_tools = 0
        error_categories: list[str] = []
        research_answer_repairs = 0
        research_tool_calls = 0
        last_compaction_turn = -3
        if self.tracer:
            self.tracer.log_event("run_start", task=user_task, workdir=str(self.workdir))

        for turn in range(self.max_turns):
            protocol_errors = validate_tool_protocol(messages)
            if protocol_errors:
                repaired = repair_tool_protocol(messages)
                remaining = validate_tool_protocol(repaired)
                if remaining:
                    raise RuntimeError("消息协议无法安全修复：" + "; ".join(remaining))
                messages = repaired
                self.messages = messages
                self._emit("protocol_repaired", errors=protocol_errors)
                if self.tracer:
                    self.tracer.log_event("protocol_repaired", errors=protocol_errors)

            tools = self.registry.schemas() if getattr(self.backend, "supports_tools", True) else []
            started = time.perf_counter()
            self._emit("model_start", turn=turn + 1)
            assistant = self.backend.chat(messages, tools=tools)
            self._emit("model_end", turn=turn + 1)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            normalized_calls = []
            for call_index, raw_call in enumerate(assistant.get("tool_calls") or []):
                call = dict(raw_call)
                call["id"] = str(call.get("id") or f"call_{turn + 1}_{call_index}")
                normalized_calls.append(call)
            tool_calls = normalized_calls
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
                if research_answer_repairs < 2 and _is_insufficient_research_answer(user_task, answer):
                    research_answer_repairs += 1
                    messages.append({
                        "role": "user",
                        "content": _research_answer_repair_prompt(user_task, answer),
                    })
                    self._emit("final_blocked", reason="insufficient_research_answer", turn=turn + 1)
                    if self.tracer:
                        self.tracer.log_event(
                            "final_blocked", reason="insufficient_research_answer", turn=turn + 1,
                        )
                    continue
                self._emit("answer", content=assistant.get("content", ""), turn=turn + 1)
                self.last_run_status = "success" if answer else "failed"
                if self.tracer:
                    self.tracer.log_event("run_end", status=self.last_run_status, turns=turn + 1)
                return answer or "[模型未返回内容，任务失败]"

            for call in tool_calls:
                call_name = str(call.get("name", ""))
                if call_name in RESEARCH_DISCOVERY_TOOLS:
                    research_tool_calls += 1
                arguments = call.get("arguments")
                tool = self.registry.get(call_name)
                success = False
                error_category = "unknown_error"
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
                    elif call_name in REUSABLE_OBSERVATION_TOOLS and signature in reusable_observations:
                        success = True
                        obs = (
                            "[已复用此前相同调用的成功 observation；请基于该结果继续，"
                            "不要再次使用完全相同的参数。]\n"
                            + reusable_observations[signature]
                        )
                    elif repeated_calls[signature] > self.max_repeated_call:
                        obs = self._error(
                            call_name, "repeated_call",
                            f"相同调用已出现 {repeated_calls[signature]} 次",
                            suggestion="检查已有 observation，改变参数或结束任务",
                        )
                    else:
                        try:
                            decision = self.permission_manager.decide(call_name, arguments, self.workdir)
                            if decision.verdict == "deny":
                                obs = self._error(call_name, "permission_denied", decision.reason, False, "请求用户调整任务")
                            elif decision.verdict == "confirm":
                                response: bool | str | permissions.ConfirmationResponse = self.auto_approve
                                if not self.auto_approve and self.confirm_callback:
                                    response = self.confirm_callback(call_name, arguments, decision)
                                if isinstance(response, permissions.ConfirmationResponse):
                                    approved, scope = response.approved, response.scope
                                elif isinstance(response, str):
                                    approved, scope = response in {"once", "task", "session"}, response
                                else:
                                    approved, scope = bool(response), "once"
                                if not approved:
                                    obs = self._error(call_name, "confirmation_required", decision.reason, True, "请求用户确认或选择只读方案")
                                else:
                                    self.permission_manager.grant(decision, scope)
                                    result = self._run_tool(tool, arguments)
                                    success = result.success
                                    error_category = result.category
                                    obs = result.content if success else self._tool_error(call_name, result, arguments)
                            else:
                                result = self._run_tool(tool, arguments)
                                success = result.success
                                error_category = result.category
                                obs = result.content if success else self._tool_error(call_name, result, arguments)
                        except Exception as exc:  # 工具错误必须回填给模型自修复
                            error_category = "execution_error"
                            obs = self._error(call_name, "execution_error", str(exc))
                if success and call_name in REUSABLE_OBSERVATION_TOOLS and isinstance(arguments, dict):
                    reusable_observations.setdefault(signature, str(obs))
                if success:
                    successful_tools += 1
                    consecutive_errors = 0
                    error_categories.clear()
                else:
                    consecutive_errors += 1
                    error_categories.append(error_category)
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
            # 等本批 assistant 声明的全部 tool_calls 都回填结果后再停止探索，
            # 避免把不完整的 assistant/tool 协议保留到跨轮会话历史中。
            if consecutive_errors >= self.max_consecutive_errors:
                return self._finalize_after_tool_errors(
                    messages,
                    user_task=user_task,
                    turn=turn,
                    consecutive_errors=consecutive_errors,
                    successful_tools=successful_tools,
                    error_categories=error_categories,
                )
            if _needs_literature_report(user_task) and research_tool_calls >= self.max_research_calls:
                return self._finalize_without_tools(
                    messages,
                    user_task=user_task,
                    successful_tools=successful_tools,
                    fallback_turn=turn + 2,
                    reason="research_search_budget",
                    heading=f"文献检索已使用 {research_tool_calls} 次搜索/抓取，停止扩展并整理结果",
                )
            tokens_before = estimate_tokens(messages)
            compacted = messages
            if turn - last_compaction_turn >= 3 or tokens_before > self.context_budget * 2:
                compacted = maybe_compact(messages, self.backend, budget=self.context_budget)
            if compacted is not messages:
                last_compaction_turn = turn
                tokens_after = estimate_tokens(compacted)
                self._emit("compaction", before=tokens_before, after=tokens_after)
                if self.tracer:
                    self.tracer.log_event("compaction", before=tokens_before, after=tokens_after)
            messages = compacted
            self.messages = messages

        return self._finalize_without_tools(
            messages,
            user_task=user_task,
            successful_tools=successful_tools,
            fallback_turn=self.max_turns + 1,
            reason="max_turns_summarized",
            heading="已达到工具探索轮次上限，现在交付已有结果",
        )