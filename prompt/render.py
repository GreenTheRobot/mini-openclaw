"""把结构化消息与工具 schema 渲染为文本，并解析文本工具调用。"""
from __future__ import annotations

import json
import re
from typing import Any

ROLE_TOKENS = {
    "system": "<|system|>",
    "user": "<|user|>",
    "assistant": "<|assistant|>",
    "tool": "<|observation|>",
}


def render_tools_block(tools: list[dict[str, Any]]) -> str:
    if not tools:
        return ""
    lines = [
        "你可以调用以下工具。调用格式："
        '<tool_call>{"name": "工具名", "arguments": {}}</tool_call>'
    ]
    for tool in tools:
        function = tool["function"]
        schema = json.dumps(function["parameters"], ensure_ascii=False, sort_keys=True)
        lines.append(f"- {function['name']}: {function['description']} 参数 schema={schema}")
    return "\n".join(lines)


def render_prompt(messages: list[dict[str, Any]],
                  tools: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = []
    tools_block = render_tools_block(tools or [])
    injected_tools = False
    for message in messages:
        role = str(message.get("role", ""))
        if role not in ROLE_TOKENS:
            raise ValueError(f"不支持的消息角色：{role}")
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if role == "system" and tools_block and not injected_tools:
            content = content.rstrip() + "\n\n" + tools_block
            injected_tools = True
        parts.append(f"{ROLE_TOKENS[role]}\n{content}\n")
    if tools_block and not injected_tools:
        parts.insert(0, f"{ROLE_TOKENS['system']}\n{tools_block}\n")
    parts.append(ROLE_TOKENS["assistant"] + "\n")
    return "".join(parts)


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    blocks = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, flags=re.S)
    if "<tool_call>" in text and not blocks:
        raise ValueError("工具调用缺少闭合的 </tool_call> 标签")
    calls: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, 1):
        try:
            call = json.loads(block)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {index} 个工具调用不是合法 JSON：{exc.msg}") from exc
        if not isinstance(call, dict):
            raise ValueError(f"第 {index} 个工具调用必须是 JSON 对象")
        name = call.get("name")
        arguments = call.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"第 {index} 个工具调用缺少有效 name")
        if not isinstance(arguments, dict):
            raise ValueError(f"第 {index} 个工具调用的 arguments 必须是对象")
        calls.append({"name": name, "arguments": arguments})
    return calls