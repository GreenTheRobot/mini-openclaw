import pytest

from prompt.render import parse_tool_calls, render_prompt


def _tool_schema():
    return [{"type": "function", "function": {"name": "read", "description": "读文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]


def test_render_prompt_injects_tools_and_roles():
    result = render_prompt([
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "任务"},
        {"role": "tool", "content": "结果"},
    ], _tool_schema())
    assert "read" in result
    assert "<|system|>\n系统" in result
    assert "<|user|>\n任务" in result
    assert "<|observation|>\n结果" in result
    assert result.endswith("<|assistant|>\n")


def test_parse_multiple_tool_calls():
    text = '<tool_call>{"name":"read","arguments":{"path":"论文.md"}}</tool_call>' + '<tool_call>{"name":"grep","arguments":{"pattern":"TODO"}}</tool_call>'
    assert [call["name"] for call in parse_tool_calls(text)] == ["read", "grep"]


@pytest.mark.parametrize("text", [
    '<tool_call>{bad}</tool_call>',
    '<tool_call>{"name":"read","arguments":[]}</tool_call>',
    '<tool_call>{"name":"read"}',
])
def test_parse_tool_calls_rejects_malformed_input(text):
    with pytest.raises(ValueError):
        parse_tool_calls(text)