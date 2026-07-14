from agent.context import (
    estimate_tokens,
    maybe_compact,
    repair_tool_protocol,
    truncate_observation,
    validate_tool_protocol,
)


class SummaryBackend:
    def chat(self, messages, tools=None):
        return {"content": "目标：保留实验约束；已完成：检索；下一步：核验日期。", "tool_calls": []}


class ExpandingBackend:
    def chat(self, messages, tools=None):
        return {"content": "summary" * 3000, "tool_calls": []}


def test_compaction_keeps_system_and_recent_user_turn():
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "早期约束：随机种子必须为 42。" + "x" * 5000},
        {"role": "assistant", "content": "已记录。" + "y" * 3000},
        {"role": "user", "content": "继续完成实验。"},
        {"role": "assistant", "content": "正在处理。"},
    ]
    compacted = maybe_compact(messages, SummaryBackend(), budget=50, recent_turns=1)
    assert compacted is not messages
    assert compacted[0] == messages[0]
    assert compacted[1]["role"] == "user"
    assert "# 历史压缩备忘" in compacted[1]["content"]
    assert compacted[-2:] == messages[-2:]
    assert estimate_tokens(compacted) < estimate_tokens(messages)
    assert validate_tool_protocol(compacted) == []


def test_compaction_never_splits_multi_tool_call_groups():
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "研究任务"}]
    for step in range(5):
        calls = [
            {"id": f"call-{step}-a", "name": "web_search", "arguments": {"query": "a"}},
            {"id": f"call-{step}-b", "name": "web_search", "arguments": {"query": "b"}},
        ]
        messages.append({"role": "assistant", "content": "", "tool_calls": calls})
        messages.append({"role": "tool", "tool_call_id": f"call-{step}-a", "name": "web_search", "content": "A" * 1800})
        messages.append({"role": "tool", "tool_call_id": f"call-{step}-b", "name": "web_search", "content": "B" * 1800})
    assert validate_tool_protocol(messages) == []
    compacted = maybe_compact(messages, SummaryBackend(), budget=100)
    assert compacted is not messages
    assert validate_tool_protocol(compacted) == []
    for index, message in enumerate(compacted):
        if message.get("role") == "tool":
            assert index > 0
            assert any(
                earlier.get("role") == "assistant"
                and message["tool_call_id"] in {call["id"] for call in earlier.get("tool_calls", [])}
                for earlier in compacted[:index]
            )


def test_compaction_is_abandoned_if_summary_makes_context_larger():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "old result"},
        {"role": "assistant", "content": "recent one" + "x" * 3000},
        {"role": "assistant", "content": "recent two" + "y" * 3000},
        {"role": "assistant", "content": "recent three" + "z" * 3000},
    ]
    assert maybe_compact(messages, ExpandingBackend(), budget=100) is messages


def test_protocol_repair_removes_orphan_tool_message():
    broken = [
        {"role": "system", "content": "system"},
        {"role": "tool", "tool_call_id": "orphan", "name": "read", "content": "data"},
        {"role": "assistant", "content": "continue"},
    ]
    assert validate_tool_protocol(broken)
    repaired = repair_tool_protocol(broken)
    assert validate_tool_protocol(repaired) == []
    assert all(message.get("role") != "tool" for message in repaired)


def test_long_observation_keeps_head_tail_and_archive_location():
    text = "HEAD" + ("x" * 5000) + "TAIL"
    result = truncate_observation(text, max_chars=100, archive_path=".mini-openclaw/observations/result.txt")
    assert "HEAD" in result
    assert "TAIL" in result
    assert ".mini-openclaw/observations/result.txt" in result