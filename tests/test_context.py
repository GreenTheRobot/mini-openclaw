from agent.context import maybe_compact, truncate_observation


class SummaryBackend:
    def chat(self, messages, tools=None):
        return {"content": "目标：保留实验约束；已完成：读取配置；下一步：运行验证。", "tool_calls": []}


def test_compaction_keeps_system_and_safe_recent_suffix():
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
    assert compacted[-3:-1] == messages[-2:]
    assert compacted[-1]["role"] == "user"
    assert "继续完成当前任务" in compacted[-1]["content"]


def test_compaction_removes_tool_protocol_messages():
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "跑实验并汇报。" * 100},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "name": "bash", "arguments": {"command": "python train.py"}}
        ]},
        {"role": "tool", "name": "bash", "tool_call_id": "call_1", "content": "epoch=1\n" * 1000},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_2", "name": "wechat_file_transfer", "arguments": {"message": "完成"}}
        ]},
    ]
    compacted = maybe_compact(messages, SummaryBackend(), budget=50)
    assert len(compacted) == 2
    assert [message["role"] for message in compacted] == ["system", "user"]
    assert all("tool_calls" not in message for message in compacted)
    assert all(message.get("role") != "tool" for message in compacted)


def test_compaction_keeps_complete_tool_block():
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "早期历史" * 1000},
        {"role": "assistant", "content": "已处理早期历史"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "name": "bash", "arguments": {"command": "python train.py"}}
        ]},
        {"role": "tool", "name": "bash", "tool_call_id": "call_1", "content": "status=completed"},
    ]
    compacted = maybe_compact(messages, SummaryBackend(), budget=50)
    assert compacted[-3:] == messages[-2:] + [{
        "role": "user",
        "content": (
            "请根据系统消息中的历史压缩备忘和保留的最近上下文继续完成当前任务。"
            "不要重复已完成步骤；如果还需要行动，请继续调用合适工具。"
        ),
    }]


def test_long_observation_keeps_head_tail_and_archive_location():
    text = "HEAD" + ("x" * 5000) + "TAIL"
    result = truncate_observation(text, max_chars=100, archive_path=".mini-openclaw/observations/result.txt")
    assert "HEAD" in result
    assert "TAIL" in result
    assert ".mini-openclaw/observations/result.txt" in result