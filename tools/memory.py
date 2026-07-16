"""让模型主动写入长期记忆的 remember 工具。"""
import re

from agent.memory import Memory
from .base import Tool

_SENSITIVE = re.compile(r"(?i)(api[_ -]?key|password|passwd|secret|token|sk-[a-z0-9]|密码|密钥|令牌)")

def _remember(note: str) -> str:
    if _SENSITIVE.search(note):
        raise ValueError("拒绝把疑似密钥、密码或令牌写入长期记忆")
    Memory("MEMORY.md").write(note)
    return "已记住：" + note.strip()

remember_tool = Tool(
    name="remember",
    description="当用户明确要求记住，或告知值得跨会话保留的科研项目约定、偏好、关键决策时调用。不要保存密钥、密码、个人隐私或临时信息。",
    parameters={"type": "object", "properties": {"note": {"type": "string", "description": "简洁、独立、可在未来会话直接理解的长期记忆"}}, "required": ["note"]},
    run=_remember,
)
