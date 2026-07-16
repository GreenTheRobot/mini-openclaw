"""Context budgeting, protocol-safe compaction and observation truncation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.todo_context import current_todo_path


TASK_STATE_PATH = Path(".mini-openclaw/tasks.json")
OPEN_TASK_STATUSES = {"pending", "in_progress"}


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Conservative tokenizer-free estimate; sufficient for budget decisions."""
    characters = sum(len(str(message.get("content", ""))) for message in messages)
    characters += sum(len(json.dumps(message.get("tool_calls", []), ensure_ascii=False)) for message in messages)
    return max(1, characters // 2)


def validate_tool_protocol(messages: list[dict[str, Any]]) -> list[str]:
    """Validate OpenAI/DeepSeek assistant-tool message ordering."""
    errors: list[str] = []
    pending: set[str] = set()
    first_conversation_seen = False

    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "system":
            if index != 0:
                errors.append(f"messages[{index}] 出现了中途 system 消息")
            continue
        if not first_conversation_seen:
            first_conversation_seen = True
            if role != "user":
                errors.append(f"第一条非 system 消息必须是 user，实际为 {role}")

        calls = message.get("tool_calls") or []
        if role == "assistant" and calls:
            if pending:
                errors.append(f"messages[{index}] 开始新工具调用前仍缺少结果：{sorted(pending)}")
            ids = [str(call.get("id") or "") for call in calls]
            if any(not call_id for call_id in ids):
                errors.append(f"messages[{index}] 的 tool_call 缺少 id")
            if len(ids) != len(set(ids)):
                errors.append(f"messages[{index}] 的 tool_call id 重复")
            pending = {call_id for call_id in ids if call_id}
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if not pending:
                errors.append(f"messages[{index}] 是孤立 tool 消息：{call_id or 'missing-id'}")
            elif call_id not in pending:
                errors.append(f"messages[{index}] 的 tool_call_id 无对应调用：{call_id}")
            else:
                pending.remove(call_id)
            continue
        if pending:
            errors.append(f"messages[{index}] 前缺少工具结果：{sorted(pending)}")
            pending.clear()
        if role not in {"user", "assistant"}:
            errors.append(f"messages[{index}] role 无效：{role}")

    if pending:
        errors.append(f"历史末尾缺少工具结果：{sorted(pending)}")
    return errors


def repair_tool_protocol(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop incomplete/orphaned legacy tool groups and restore a valid user boundary."""
    if not messages:
        return messages
    system = dict(messages[0]) if messages[0].get("role") == "system" else {
        "role": "system", "content": "你是一个命令行智能体。",
    }
    source = messages[1:] if messages[0].get("role") == "system" else messages
    repaired: list[dict[str, Any]] = [system]
    recovery_needed = False
    index = 0

    while index < len(source):
        message = source[index]
        role = message.get("role")
        calls = message.get("tool_calls") or []
        if role == "assistant" and calls:
            normalized_calls = []
            expected: list[str] = []
            for call_index, raw_call in enumerate(calls):
                call = dict(raw_call)
                call_id = str(call.get("id") or f"recovered_{index}_{call_index}")
                call["id"] = call_id
                expected.append(call_id)
                normalized_calls.append(call)
            results: dict[str, dict[str, Any]] = {}
            cursor = index + 1
            while cursor < len(source) and source[cursor].get("role") == "tool":
                tool_message = dict(source[cursor])
                call_id = str(tool_message.get("tool_call_id") or "")
                if call_id in expected and call_id not in results:
                    results[call_id] = tool_message
                else:
                    recovery_needed = True
                cursor += 1
            if all(call_id in results for call_id in expected):
                assistant = dict(message)
                assistant["tool_calls"] = normalized_calls
                repaired.append(assistant)
                repaired.extend(results[call_id] for call_id in expected)
            else:
                recovery_needed = True
            index = cursor
            continue
        if role == "tool":
            recovery_needed = True
            index += 1
            continue
        if role == "system":
            content = str(message.get("content", ""))
            repaired.append({"role": "user", "content": "[历史系统备忘]\n" + content})
        else:
            repaired.append(dict(message))
        index += 1

    if len(repaired) == 1 or repaired[1].get("role") != "user":
        repaired.insert(1, {
            "role": "user",
            "content": "请继续当前任务；较早历史已做协议安全修复，必要时重新调用工具核验。",
        })
    elif recovery_needed:
        repaired[1] = dict(repaired[1])
        repaired[1]["content"] = (
            "[协议恢复提示：一组不完整或孤立的历史工具消息已移除。]\n"
            + str(repaired[1].get("content", ""))
        )
    return repaired


def _load_task_items(workdir: str | Path | None = None) -> list[dict[str, Any]]:
    if workdir is None:
        return []
    path = Path(workdir) / current_todo_path(TASK_STATE_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def has_open_tasks(workdir: str | Path | None = None) -> bool:
    return any(str(item.get("status", "")) in OPEN_TASK_STATUSES for item in _load_task_items(workdir))


def task_state_snapshot(workdir: str | Path | None = None) -> str:
    items = _load_task_items(workdir)
    if not items:
        return ""

    lines = [
        "## 权威 TODO 快照",
        "来源：当前 TODO 状态文件。这个状态优先于模型生成的压缩摘要。",
    ]
    open_ids: list[str] = []
    for item in items:
        task_id = str(item.get("id", "")).strip() or "(no id)"
        title = str(item.get("title", "")).strip() or "(no title)"
        status = str(item.get("status", "")).strip() or "(no status)"
        result = str(item.get("result", "")).strip()
        if status in OPEN_TASK_STATUSES:
            open_ids.append(task_id)
        line = f"- {task_id}: {title} [{status}]"
        if result:
            line += f" result={result}"
        lines.append(line)

    if open_ids:
        lines.append(f"未完成任务 id：{', '.join(open_ids)}")
        lines.append("只要存在 pending 或 in_progress 项，就不能声称整体任务已经完成。")
    else:
        lines.append("没有 pending 或 in_progress 的 TODO 项。")
    return "\n".join(lines)


def _atomic_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group one assistant tool call with all immediately following tool results."""
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            unit = [message]
            index += 1
            while index < len(messages) and messages[index].get("role") == "tool":
                unit.append(messages[index])
                index += 1
            units.append(unit)
            continue
        units.append([message])
        index += 1
    return units


def _summarize(backend: Any, chunk: list[dict[str, Any]]) -> str:
    text = "\n".join(json.dumps(message, ensure_ascii=False) for message in chunk)
    prompt = (
        "请把以下历史压缩成不超过 1200 字的结构化备忘。不得遗漏用户硬约束、"
        "已完成步骤、失败原因、已修改文件、关键工具证据、当前任务和下一步。"
        "不要复述工具原始长输出，也不要新增事实。\n\n" + text
    )
    response = backend.chat([{"role": "user", "content": prompt}], tools=[])
    return str(response.get("content", "")).strip()[:2400]


def _recent_unit_start(units: list[list[dict[str, Any]]], recent_turns: int) -> int:
    user_units = [
        index for index, unit in enumerate(units)
        if unit and unit[0].get("role") == "user"
    ]
    if len(user_units) >= recent_turns:
        return user_units[-recent_turns]
    # A single long agentic task has only one user message. Keep the latest
    # three complete interaction units instead of cutting raw messages.
    return max(0, len(units) - 3)


def maybe_compact(
    messages: list[dict[str, Any]],
    backend: Any,
    budget: int = 20000,
    recent_turns: int = 2,
    workdir: str | Path | None = None,
) -> list[dict[str, Any]]:
    if len(messages) <= 3:
        return messages
    protocol_errors = validate_tool_protocol(messages)
    if protocol_errors:
        repaired = repair_tool_protocol(messages)
        if validate_tool_protocol(repaired):
            return messages
        messages = repaired

    system = messages[0]
    # Never let a large system prompt consume the complete budget. Keep at
    # least 4k estimated tokens for user/tool history before compacting.
    effective_budget = max(budget, estimate_tokens([system]) + 4000)
    before = estimate_tokens(messages)
    if before <= effective_budget:
        return messages

    units = _atomic_units(messages[1:])
    start = _recent_unit_start(units, recent_turns)
    if start <= 0:
        return messages
    middle = [message for unit in units[:start] for message in unit]
    recent = [message for unit in units[start:] for message in unit]
    if not middle or not recent:
        return messages

    try:
        summary = _summarize(backend, middle)
    except Exception:
        return messages
    if not summary:
        return messages

    snapshot = task_state_snapshot(workdir)
    memo_content = "# 历史压缩备忘（必须继续遵循）\n" + summary
    if snapshot:
        memo_content += (
            "\n\n" + snapshot
            + "\n最终答复前必须核对权威 TODO 快照；如果仍有 pending 或 in_progress 项，请继续执行而不是宣布完成。"
        )
    memo = {"role": "user", "content": memo_content}
    compacted = [system, memo, *recent]
    if validate_tool_protocol(compacted):
        return messages
    # A compaction that does not reduce the context is counterproductive and
    # previously caused repeated summarization on every tool turn.
    if estimate_tokens(compacted) >= before:
        return messages
    return compacted


def truncate_observation(
    text: str,
    max_chars: int = 4000,
    archive_path: str | None = None,
) -> str:
    """Keep both head and tail while pointing to the archived full result."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    location = f"完整结果：{archive_path}\n" if archive_path else ""
    return (
        location + text[:half]
        + f"\n...[中间已截断；原始长度 {len(text)} 字符]...\n"
        + text[-half:]
    )
