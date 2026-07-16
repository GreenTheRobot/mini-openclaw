# 微信文件传输助手桥接服务

这个功能让 mini-openclaw 可以通过一个运行在 Windows 侧的小型 HTTP 桥接服务，把文本消息发送到微信的文件传输助手。

典型运行方式：

- mini-openclaw 在 WSL 或普通终端里运行。
- Windows 微信保持登录，并且能被 `wxauto4` 操作。
- `services/wx_file_transfer_server.py` 使用已安装 `wxauto4` 的 Windows Python 环境启动。
- Agent 调用内置的 `wechat_file_transfer` 工具，由该工具向桥接服务发送 HTTP 请求。默认目标是文件传输助手；如果用户明确指定其它微信会话，工具只会在该会话已由运行环境加入固定允许列表时通过 `target` 参数发送。

## 需求

- 较早的微信版本（最新支持到客户端 4.1.8.107 ，可以从这里下载 [text](https://github.com/SiverKing/wechat4.0-windows-versions/releases/tag/v4.1.8.107)
- python 3.9-3.12
- wxauto4

运行不出问题的话后两个都是自动下载，第一个需要自己安一下，但是 demo 演示中只需要 dry-run 即可，嘿嘿

## 文件说明

- `services/wx_file_transfer_server.py`：Windows 侧 HTTP 桥接服务，负责通过 `wxauto4` 操作微信。
- `tools/wechat.py`：mini-openclaw 内置工具封装，负责调用桥接服务。
- `tools/base.py`：把 `wechat_file_transfer` 注册到默认工具表。
- `skills/wechat-file-transfer/SKILL.md`：Skill 说明，约束 Agent 只有在用户明确要求发送微信消息时才使用该工具。

## 自动启动 Windows 桥接服务

默认情况下，`wechat_file_transfer` 在连接不上桥接服务时会自动调用：

```powershell
services\wechat_bridge\start.ps1
```

原生 Windows PowerShell 运行 agent 时，工具会直接启动 `powershell.exe -File services\wechat_bridge\start.ps1`。WSL 运行 agent 时，工具会通过 WSL 的 `/init` 启动 Windows PowerShell，避免 WSL 无法直接执行 Windows `.exe` 的问题。

这个脚本会在 `services\wechat_bridge\.venv` 创建 Windows 侧专用虚拟环境，要求使用 Windows Python 3.12，并在该环境中安装 `wxauto4`，然后启动 `services\wx_file_transfer_server.py`。第一次运行会慢一些，后续会复用已有虚拟环境；如果已有 `.venv` 不是 Python 3.12 或缺少 `wxauto4`，脚本会重建它。

`wxauto4` 只能在 Windows 侧 Python 环境中工作，不支持 Linux/WSL Python。默认脚本会自动查找 Windows Python 3.12；如果找不到，会尝试通过 `winget install Python.Python.3.12 --scope user` 自动安装。机器上有多个 Python 时，可以设置：

```bash
export WECHAT_BRIDGE_PYTHON='C:\Path\To\Python312\python.exe'
```

由工具自动启动的 bridge 会挂在当前 agent 进程下：同一个 agent 会话内多次微信发送会复用同一个 bridge，agent 进程退出时再自动回收。

如果需要覆盖默认启动方式，可以设置：

Windows PowerShell：

```powershell
$env:WECHAT_BRIDGE_START_CMD = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "E:\mini-openclaw\services\wechat_bridge\start.ps1"'
```

WSL：

```bash
export WECHAT_BRIDGE_START_CMD='powershell.exe -NoProfile -ExecutionPolicy Bypass -File "E:\mini-openclaw\services\wechat_bridge\start.ps1"'
```

手动启动时，使用以下命令。

Windows PowerShell：

```powershell
.\services\wechat_bridge\start.ps1
```

WSL：

```bash
/init /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w services/wechat_bridge/start.ps1)"
```

默认监听地址：

```text
http://0.0.0.0:8765
```

在 Windows 侧做健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

期望返回：

```json
{"ok": true, "service": "wx_file_transfer"}
```

## WSL 连接方式

当前 WSL 环境里通常应使用：

```bash
export WX_BRIDGE_URL=http://127.0.0.1:8765
python -c 'import os, urllib.request; print(urllib.request.urlopen(os.environ.get("WX_BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/") + "/health", timeout=2).read().decode())'
```

期望返回：

```json
{"ok": true, "service": "wx_file_transfer"}
```

`tools/wechat.py` 也会自动探测这个场景。如果没有设置 `WX_BRIDGE_URL`，并且 WSL 能访问 `127.0.0.1:8765`，工具会优先使用 localhost；只有 localhost 不可用时，才回退到 `/etc/resolv.conf` 里的 nameserver 地址。

如果 `WX_BRIDGE_URL` 被设置成过期的 WLAN 地址或 nameserver 地址，它会覆盖自动探测。可以这样修复：

```bash
unset WX_BRIDGE_URL
# 或者
export WX_BRIDGE_URL=http://127.0.0.1:8765
```

## 发送消息

桥接服务的发送接口是：

```text
POST /send_to_file_transfer
```

请求示例：

```bash
python - <<'PY'
import json
import os
import urllib.request

base_url = os.environ.get("WX_BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/")
payload = json.dumps(
    {"message": "hello from mini-openclaw", "target": "文件传输助手"},
    ensure_ascii=False,
).encode("utf-8")
request = urllib.request.Request(
    base_url + "/send_to_file_transfer",
    data=payload,
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST",
)
print(urllib.request.urlopen(request, timeout=15).read().decode("utf-8"))
PY
```

Agent 只有在用户明确要求通过微信发送文本时，才应该调用 `wechat_file_transfer` 工具。

## 配置项

Windows 桥接服务环境变量：

- `WX_BRIDGE_HOST`：监听 host，默认 `0.0.0.0`。
- `WX_BRIDGE_PORT`：监听端口，默认 `8765`。
- `WX_FILE_TRANSFER_TARGET`：默认微信会话名，默认 `文件传输助手`。
- `WX_ALLOWED_TARGETS`：Agent 可发送的额外固定微信会话名，使用逗号或分号分隔；未配置时只允许 `文件传输助手`。
- `WX_BRIDGE_TOKEN`：可选共享 token。
- `WX_BRIDGE_CERTFILE`：可选 TLS 证书文件。
- `WX_BRIDGE_KEYFILE`：可选 TLS 私钥文件。

客户端环境变量：

- `WX_BRIDGE_URL`：桥接服务基础 URL。未设置时，工具会自动探测本地 WSL/Windows 路由。
- `WX_BRIDGE_TOKEN`：可选 token，会作为 `X-OpenClaw-Token` 请求头发送。
- `WX_FILE_TRANSFER_TARGET`：可选默认发送目标，默认 `文件传输助手`；该目标会自动加入允许列表。
- `WX_ALLOWED_TARGETS`：可选额外允许发送目标，使用逗号或分号分隔。工具调用传入 `target` 时，目标必须属于 `文件传输助手`、`WX_FILE_TRANSFER_TARGET` 或 `WX_ALLOWED_TARGETS`。
- `WECHAT_DRY_RUN`：设为 `1`、`true`、`yes` 或 `on` 时启用 dry-run。Agent 仍正常调用 `wechat_file_transfer`，但工具不会连接微信桥接服务，只会在命令行 stderr 打印目标和消息内容，并向 Agent 返回与真实发送成功相同的结果。
- `WECHAT_BRIDGE_START_CMD`：可选自动启动命令。未设置时使用项目内置的 `services\wechat_bridge\start.ps1`；设置后覆盖默认命令。
- `WECHAT_BRIDGE_PYTHON`：可选 Windows Python 3.12 路径，用于创建 `services\wechat_bridge\.venv`。未设置时脚本会自动查找常见 Python 3.12 命令和安装路径；找不到时尝试用 `winget` 安装。
- `WECHAT_BRIDGE_START_TIMEOUT`：可选自动启动等待秒数，默认 `300`。首次安装 Python、创建 venv 和安装 `wxauto4` 可能需要更长时间。

如果桥接服务启用了 token 校验，客户端环境里也必须设置相同的 `WX_BRIDGE_TOKEN`。

## Dry-run 演示

当只想验证 Agent 会向谁发送、发送什么内容，而不希望真的发微信消息时，可以开启 dry-run：

```bash
export WECHAT_DRY_RUN=1
python -m agent.cli "把本次实验摘要发送到微信文件传输助手"
python -m agent.cli "把本次实验摘要发送到微信里的张三"
```

命令行会打印类似日志：

```text
[wechat dry-run] would send message
[wechat dry-run] target: 文件传输助手
[wechat dry-run] message: ...
```

注意：dry-run 不改变工具 schema，模型不会看到 `dry_run` 参数，也无法主动切换 dry-run。

## 排障

如果 `/health` 连接不上：

1. 确认 Windows 微信正在运行并已登录。
2. 在 Windows 侧确认端口已监听：

   ```powershell
   Get-NetTCPConnection -LocalPort 8765
   ```

3. 如果 WSL 仍然使用错误地址，检查并重置环境变量：

   ```bash
   echo "$WX_BRIDGE_URL"
   export WX_BRIDGE_URL=http://127.0.0.1:8765
   ```

4. 如果自动启动没有生效，在对应环境手动运行。

   Windows PowerShell：

   ```powershell
   .\services\wechat_bridge\start.ps1
   ```

   WSL：

   ```bash
   /init /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w services/wechat_bridge/start.ps1)"
   ```

如果 `/health` 正常，但发送失败：

1. 确认 Windows 微信正在运行并已登录。
2. 确认 `services\wechat_bridge\.venv` 使用 Windows Python 3.12，且能 `import wxauto4`。
3. 确认目标会话名和当前微信语言匹配，默认是 `文件传输助手`。
4. 查看桥接服务的 stdout/stderr 日志，或在可见终端里运行桥接服务，读取 `wxauto4` 报错。

## 安全说明

这个功能会发送真实微信消息。它不会读取微信消息。Skill 规则要求 Agent 只有在用户明确要求发送消息时才使用该工具。
