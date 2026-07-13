"""上下文预算、结构化 compaction 与长 observation 截断。"""
from __future__ import annotations

from typing import Any


SUMMARY_MAX_CHARS = 3000
MESSAGE_MAX_CHARS = 1200
RECENT_MAX_TOKENS = 2000


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """无需额外 tokenizer 的保守估计；中文按约 1.5 字/token 处理。"""
    characters = sum(len(str(message.get("content", ""))) for message in messages)
    return max(1, characters // 2)


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n...[截断 {len(text) - max_chars} 字符]...\n" + text[-half:]


def _message_to_text(message: dict[str, Any]) -> str:
    role = str(message.get("role", ""))
    lines = [f"{role}:"]
    if message.get("name"):
        lines.append(f"name={message.get('name')}")
    if message.get("tool_call_id"):
        lines.append(f"tool_call_id={message.get('tool_call_id')}")
    if message.get("tool_calls"):
        calls = []
        for call in message.get("tool_calls", []):
            calls.append({
                "name": call.get("name"),
                "arguments": call.get("arguments", {}),
            })
        lines.append("tool_calls=" + str(calls))
    content = str(message.get("content", ""))
    if content:
        lines.append(_clip(content, MESSAGE_MAX_CHARS))
    return "\n".join(lines)


def _fallback_summary(chunk: list[dict[str, Any]]) -> str:
    tail = "\n\n".join(_message_to_text(message) for message in chunk[-10:])
    return (
        "## 原始任务目标\n"
        "见下方最近历史。\n"
        "## 用户硬性约束\n"
        "继续遵循系统提示、已加载 Skill 和用户原始任务。\n"
        "## 已完成步骤\n"
        "模型摘要失败，保留最近工具调用与结果的本地摘要。\n"
        "## 已修改文件\n"
        "见最近历史。\n"
        "## 关键工具结果\n"
        f"{_clip(tail, SUMMARY_MAX_CHARS)}\n"
        "## 失败与原因\n"
        "无额外记录。\n"
        "## 当前任务清单\n"
        "根据最近历史继续。\n"
        "## 下一步\n"
        "不要重复已完成步骤，继续完成用户任务。"
    )


def _summarize(backend: Any, chunk: list[dict[str, Any]]) -> str:
    text = "\n\n".join(_message_to_text(message) for message in chunk)
    prompt = (
        "请把历史压缩成结构化备忘，不得遗漏硬约束。总长度不超过 1200 个汉字。"
        "严格使用以下标题：\n"
        "## 原始任务目标\n## 用户硬性约束\n## 已完成步骤\n## 已修改文件\n"
        "## 关键工具结果\n## 失败与原因\n## 当前任务清单\n## 下一步\n\n" + text
    )
    try:
        response = backend.chat([{"role": "user", "content": prompt}], tools=[])
        summary = str(response.get("content", "")).strip()
    except Exception:
        summary = _fallback_summary(chunk)
    return _clip(summary or _fallback_summary(chunk), SUMMARY_MAX_CHARS)


def _recent_window_start(messages: list[dict[str, Any]], recent_turns: int) -> int:
    seen = 0
    for index in range(len(messages) - 1, 0, -1):
        if messages[index].get("role") == "user":
            seen += 1
            if seen == recent_turns:
                return index
    # 单任务 Agent 的 user 消息通常只有一条，此时至少保留最近 6 条消息。
    return max(1, len(messages) - 6)


def _tool_call_ids(message: dict[str, Any]) -> set[str]:
    ids = set()
    for index, call in enumerate(message.get("tool_calls") or []):
        ids.add(str(call.get("id") or call.get("name") or f"call_{index}"))
    return ids


def _message_block_start(messages: list[dict[str, Any]], end: int) -> int | None:
    message = messages[end]
    role = message.get("role")
    if role == "tool":
        start = end
        tool_ids = set()
        while start >= 0 and messages[start].get("role") == "tool":
            tool_ids.add(str(messages[start].get("tool_call_id") or messages[start].get("name") or "tool"))
            start -= 1
        if start < 0:
            return None
        assistant = messages[start]
        if assistant.get("role") != "assistant" or not assistant.get("tool_calls"):
            return None
        call_ids = _tool_call_ids(assistant)
        return start if call_ids and tool_ids == call_ids else None
    if role == "assistant" and message.get("tool_calls"):
        return None
    return end


def _safe_recent_suffix(messages: list[dict[str, Any]], max_tokens: int = RECENT_MAX_TOKENS) -> list[dict[str, Any]]:
    blocks: list[list[dict[str, Any]]] = []
    index = len(messages) - 1
    while index >= 0:
        start = _message_block_start(messages, index)
        if start is None:
            break
        block = messages[start:index + 1]
        candidate = [message for group in reversed(blocks) for message in group]
        if estimate_tokens(block + candidate) > max_tokens:
            break
        blocks.append(block)
        index = start - 1
    return [message for group in reversed(blocks) for message in group]


def maybe_compact(messages: list[dict[str, Any]], backend: Any,
                  budget: int = 6000, recent_turns: int = 2) -> list[dict[str, Any]]:
    if estimate_tokens(messages) <= budget or len(messages) <= 3:
        return messages
    system = messages[0]
    recent = _safe_recent_suffix(messages[1:])
    middle_end = len(messages) - len(recent)
    history = messages[1:middle_end]
    if not history and not recent:
        return messages
    summary = _summarize(backend, history)
    compacted_system = dict(system)
    compacted_system["content"] = (
        str(system.get("content", "")).rstrip()
        + "\n\n# 历史压缩备忘（必须继续遵循）\n"
        + summary
    )
    compacted = [compacted_system, *recent]
    if not recent or compacted[-1].get("role") != "user":
        compacted.append({
            "role": "user",
            "content": (
                "请根据系统消息中的历史压缩备忘和保留的最近上下文继续完成当前任务。"
                "不要重复已完成步骤；如果还需要行动，请继续调用合适工具。"
            ),
        })
    if estimate_tokens(compacted) >= estimate_tokens(messages):
        compacted_system["content"] = (
            str(system.get("content", "")).rstrip()
            + "\n\n# 历史压缩备忘（必须继续遵循）\n"
            + _clip(summary, 1000)
        )
    return compacted


def truncate_observation(text: str, max_chars: int = 4000,
                         archive_path: str | None = None) -> str:
    """同时保留头尾；完整内容可由主循环保存并提供 archive_path。"""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    location = f"完整结果：{archive_path}\n" if archive_path else ""
    return (
        location + text[:half] +
        f"\n...[中间已截断；原始长度 {len(text)} 字符]...\n" + text[-half:]
    )
