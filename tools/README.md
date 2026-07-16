# Tools 模块

工具通过 `Tool(name, description, parameters, run)` 统一注册。执行前由 JSON Schema 子集校验参数，再经过权限层。包含文件、Shell、检索、PDF、论文图片、实验、任务清单、定时科研任务、记忆和微信工具。

## PDF 与论文图片

`pdf_extract_text` 默认先评估 GPU：显存达到 Marker 阈值时使用 Marker，否则使用 MarkItDown；主解析器失败后才使用 pypdf 兜底。产物均落在项目内相对目录，包含 `paper.md`、`image_manifest.json` 和 `images/`。`paper_figure_analyze` 使用配置的视觉后端分析这些图片；分析规则来自 `skills/paper-figure-reader`，因此该 Skill 仍然有效。

## 定时科研任务

`schedule_task` 将任务保存到 `.mini-openclaw/schedules.json`，只接受项目内相对 `workdir`。创建任务时默认安装一个带项目专属标记的用户级 cron 规则，每分钟执行一次 `python -m agent.scheduler run-due`；因此终端关闭后仍可唤醒到期任务。每次运行独立启动 Agent CLI，并在 `.mini-openclaw/scheduler-runs/` 保存 stdout、stderr、Trace 和独立 TODO 状态；设置 `max_runs` 后达到指定轮数会自动禁用。可用 `schedule_task(action="wakeup_status")` 或 `python -m agent.scheduler wakeup-status` 检查，`disable_wakeup` 可安全删除仅属于本项目的 cron 块。cron 会加载项目根目录的 `.env`，以获取未提交的模型 API 配置。

## wechat

微信发送工具支持 `WECHAT_DRY_RUN=1`。开启后 Agent 仍按正常方式调用 `wechat_file_transfer`，但工具只在命令行打印发送目标和内容，不连接桥接服务，也不发送真实微信消息，用于 demo 展示。

非 dry-run 下，如果桥接服务不可连接，工具会默认通过 `services\wechat_bridge\start.ps1` 自动启动 Windows 侧微信桥接服务子进程，在 agent 进程退出时回收。原生 Windows PowerShell 下直接调用 `powershell.exe -File ...`；WSL 下通过 `/init` 调 Windows PowerShell。该脚本会管理 `services\wechat_bridge\.venv`，要求 Windows Python 3.12 并安装 `wxauto4`；找不到 Python 3.12 时会尝试用 `winget` 自动安装。需要自定义启动方式时，设置 `WECHAT_BRIDGE_START_CMD` 覆盖默认命令；需要指定 Python 时，设置 `WECHAT_BRIDGE_PYTHON`；首次使用需安装环境，等待时间较久，可用 `WECHAT_BRIDGE_START_TIMEOUT` 调整。
