# 微信文件传输助手桥接服务

这个功能让 mini-openclaw 可以通过一个运行在 Windows 侧的小型 HTTP 桥接服务，把文本消息发送到微信的文件传输助手。

典型运行方式：

- mini-openclaw 在 WSL 或普通终端里运行。
- Windows 微信保持登录，并且能被 `wxauto4` 操作。
- `services/wx_file_transfer_server.py` 使用已安装 `wxauto4` 的 Windows Python 环境启动。
- Agent 调用内置的 `wechat_file_transfer` 工具，由该工具向桥接服务发送 HTTP 请求。

## 文件说明

- `services/wx_file_transfer_server.py`：Windows 侧 HTTP 桥接服务，负责通过 `wxauto4` 操作微信。
- `tools/wechat.py`：mini-openclaw 内置工具封装，负责调用桥接服务。
- `tools/base.py`：把 `wechat_file_transfer` 注册到默认工具表。
- `skills/wechat-file-transfer/SKILL.md`：Skill 说明，约束 Agent 只有在用户明确要求发送微信消息时才使用该工具。

## 启动 Windows 桥接服务

在 Windows 侧、仓库根目录下运行：

```powershell
E:\wxauto-mcp\wxauto_env\Scripts\python.exe services\wx_file_transfer_server.py
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
curl --noproxy '*' "$WX_BRIDGE_URL/health"
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
curl --noproxy '*' \
  -H 'Content-Type: application/json; charset=utf-8' \
  -d '{"message":"hello from mini-openclaw"}' \
  "$WX_BRIDGE_URL/send_to_file_transfer"
```

Agent 只有在用户明确要求通过微信发送文本时，才应该调用 `wechat_file_transfer` 工具。

## 配置项

Windows 桥接服务环境变量：

- `WX_BRIDGE_HOST`：监听 host，默认 `0.0.0.0`。
- `WX_BRIDGE_PORT`：监听端口，默认 `8765`。
- `WX_FILE_TRANSFER_TARGET`：默认微信会话名，默认 `文件传输助手`。
- `WX_BRIDGE_TOKEN`：可选共享 token。
- `WX_BRIDGE_CERTFILE`：可选 TLS 证书文件。
- `WX_BRIDGE_KEYFILE`：可选 TLS 私钥文件。

客户端环境变量：

- `WX_BRIDGE_URL`：桥接服务基础 URL。未设置时，工具会自动探测本地 WSL/Windows 路由。
- `WX_BRIDGE_TOKEN`：可选 token，会作为 `X-OpenClaw-Token` 请求头发送。

如果桥接服务启用了 token 校验，客户端环境里也必须设置相同的 `WX_BRIDGE_TOKEN`。

## 排障

如果 `/health` 连接不上：

1. 确认桥接服务进程正在运行。
2. 在 Windows 侧确认端口已监听：

   ```powershell
   Get-NetTCPConnection -LocalPort 8765
   ```

3. 在 WSL 里优先测试 localhost：

   ```bash
   curl --noproxy '*' http://127.0.0.1:8765/health
   ```

4. 如果 WSL 仍然使用错误地址，检查并重置环境变量：

   ```bash
   echo "$WX_BRIDGE_URL"
   export WX_BRIDGE_URL=http://127.0.0.1:8765
   ```

如果 `/health` 正常，但发送失败：

1. 确认 Windows 微信正在运行并已登录。
2. 确认桥接服务使用的是能 `import wxauto4` 的 Python 环境。
3. 确认目标会话名和当前微信语言匹配，默认是 `文件传输助手`。
4. 查看桥接服务的 stdout/stderr 日志，或在可见终端里运行桥接服务，读取 `wxauto4` 报错。

## 安全说明

这个功能会发送真实微信消息。它不会读取微信消息。Skill 规则要求 Agent 只有在用户明确要求发送消息时才使用该工具。
