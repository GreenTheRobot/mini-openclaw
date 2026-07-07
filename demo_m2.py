from backend.client import DeepSeekBackend
from agent.prompts import SYSTEM_PROMPT

tools = [{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "返回当前时间。用户询问现在几点、当前时间时使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]

backend = DeepSeekBackend()
resp = backend.chat(
    [{"role": "user", "content": "现在几点？"}],
    tools=tools,
)
print(resp)
"""
output:
{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_00_SzorG9GHUOyLFYaxWcE56187', 'name': 'get_time', 'arguments': {}}]}
"""

resp2 = backend.chat(
    [{"role": "system", "content": SYSTEM_PROMPT},
     {"role": "user", "content": "现在几点？"}],
    tools=tools,
)
print(resp2)
"""
output:
{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_00_F07wdl8IGSOwuLCBsuKc7447', 'name': 'get_time', 'arguments': {}}]}
"""