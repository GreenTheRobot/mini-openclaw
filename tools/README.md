# Tools 模块

工具通过 `Tool(name, description, parameters, run)` 统一注册。执行前由 JSON Schema 子集校验参数，再经过权限层。包含文件、Shell、检索、PDF、实验、任务清单、记忆和微信工具。

## wechat

微信发送工具支持 `WECHAT_DRY_RUN=1`。开启后 Agent 仍按正常方式调用 `wechat_file_transfer`，但工具只在命令行打印发送目标和内容，不连接桥接服务，也不发送真实微信消息，用于 demo 展示。

非 dry-run 下，如果桥接服务不可连接，工具会默认通过 `services\wechat_bridge\start.ps1` 自动启动 Windows 侧微信桥接服务子进程，在 agent 进程退出时回收。原生 Windows PowerShell 下直接调用 `powershell.exe -File ...`；WSL 下通过 `/init` 调 Windows PowerShell。该脚本会管理 `services\wechat_bridge\.venv`，要求 Windows Python 3.12 并安装 `wxauto4`；找不到 Python 3.12 时会尝试用 `winget` 自动安装。需要自定义启动方式时，设置 `WECHAT_BRIDGE_START_CMD` 覆盖默认命令；需要指定 Python 时，设置 `WECHAT_BRIDGE_PYTHON`；首次使用需安装环境，等待时间较久，可用 `WECHAT_BRIDGE_START_TIMEOUT` 调整。
