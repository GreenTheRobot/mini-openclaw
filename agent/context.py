"""上下文预算、结构化 compaction 与长 observation 截断。"""
from __future__ import annotations

from typing import Any


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """无需额外 tokenizer 的保守估计；中文按约 1.5 字/token 处理。"""
    characters = sum(len(str(message.get("content", ""))) for message in messages)
    return max(1, characters // 2)


def _summarize(backend: Any, chunk: list[dict[str, Any]]) -> str:
    text = "\n".join(f"{message['role']}: {message.get('content', '')}" for message in chunk)
    prompt = (
        "请把历史压缩成结构化备忘，不得遗漏硬约束。严格使用以下标题：\n"
        "## 原始任务目标\n## 用户硬性约束\n## 已完成步骤\n## 已修改文件\n"
        "## 关键工具结果\n## 失败与原因\n## 当前任务清单\n## 下一步\n\n" + text
    )
    response = backend.chat([{"role": "user", "content": prompt}], tools=[])
    return str(response.get("content", ""))


def _recent_window_start(messages: list[dict[str, Any]], recent_turns: int) -> int:
    seen = 0
    for index in range(len(messages) - 1, 0, -1):
        if messages[index].get("role") == "user":
            seen += 1
            if seen == recent_turns:
                return index
    # 单任务 Agent 的 user 消息通常只有一条，此时至少保留最近 6 条消息。
    return max(1, len(messages) - 6)


def maybe_compact(messages: list[dict[str, Any]], backend: Any,
                  budget: int = 6000, recent_turns: int = 2) -> list[dict[str, Any]]:
    if estimate_tokens(messages) <= budget or len(messages) <= 3:
        return messages
    system = messages[0]
    start = _recent_window_start(messages, recent_turns)
    middle = messages[1:start]
    recent = messages[start:]
    if not middle:
        return messages
    summary = _summarize(backend, middle)
    compacted_system = dict(system)
    compacted_system["content"] = (
        str(system.get("content", "")).rstrip()
        + "\n\n# 历史压缩备忘（必须继续遵循）\n"
        + summary
    )
    return [compacted_system, *recent]


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