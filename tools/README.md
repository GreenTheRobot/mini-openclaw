# Tools 模块

工具通过 `Tool(name, description, parameters, run)` 统一注册。执行前由 JSON Schema 子集校验参数，再经过权限层。包含文件、Shell、检索、PDF、实验、任务清单、记忆和微信工具。