from agent.context import maybe_compact, truncate_observation


class SummaryBackend:
    def chat(self, messages, tools=None):
        return {"content": "目标：保留实验约束；已完成：读取配置；下一步：运行验证。", "tool_calls": []}


def test_compaction_keeps_system_and_recent_turn():
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "早期约束：随机种子必须为 42。" * 30},
        {"role": "assistant", "content": "已记录。" * 30},
        {"role": "user", "content": "继续完成实验。"},
        {"role": "assistant", "content": "正在处理。"},
    ]
    compacted = maybe_compact(messages, SummaryBackend(), budget=50, recent_turns=1)
    assert compacted[0]["role"] == "system"
    assert compacted[0]["content"].startswith("system rules")
    assert "# 历史压缩备忘" in compacted[0]["content"]
    assert sum(message["role"] == "system" for message in compacted) == 1
    assert compacted[-2:] == messages[-2:]


def test_long_observation_keeps_head_tail_and_archive_location():
    text = "HEAD" + ("x" * 5000) + "TAIL"
    result = truncate_observation(text, max_chars=100, archive_path=".mini-openclaw/observations/result.txt")
    assert "HEAD" in result
    assert "TAIL" in result
    assert ".mini-openclaw/observations/result.txt" in result