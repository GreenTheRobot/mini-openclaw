# Agent 模块

`loop.py` 实现 ReAct 主循环、参数校验、权限、重复调用限制、错误恢复、上下文压缩和 Trace；`context.py` 管理长上下文；`memory.py` 提供 Markdown 与 KV 跨会话记忆；`permissions.py` 实现 allow/confirm/deny。