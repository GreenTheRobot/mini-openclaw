"""离线占位后端，只用于验证 CLI 和主循环能启动。"""
from __future__ import annotations

from typing import Any


class FakeBackend:
    """不伪装成真实智能体，也不制造无效工具调用。"""

    supports_tools = True

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None) -> dict[str, Any]:
        if messages and messages[-1].get("role") == "tool":
            result = str(messages[-1].get("content", ""))
            return {
                "role": "assistant",
                "content": f"[FakeBackend] 已收到工具 observation：{result[:120]}",
                "tool_calls": [],
            }
        return {
            "role": "assistant",
            "content": (
                "[FakeBackend] 当前是离线占位后端，只能验证命令行外壳。"
                "请配置 DEEPSEEK_API_KEY 后重新启动，才能测试自然语言规划、工具选择和科研任务执行。"
            ),
            "tool_calls": [],
        }