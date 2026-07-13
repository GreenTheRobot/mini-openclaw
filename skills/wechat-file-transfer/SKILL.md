---
name: wechat-file-transfer
description: "当用户要求通过微信、文件传输助手、wxauto、Windows 微信客户端发送一条文本消息、提醒、摘要或结果通知时使用。适用于 mini-openclaw 主体运行在 WSL，而 Windows 侧通过 wxauto4 桥接服务实际操作微信的场景。"
---

# 微信文件传输助手 Skill

仅当用户明确要求向微信、文件传输助手或另一个指定微信会话发送文本时，才使用这个 Skill。

## 执行流程

1. 先确认要发送的精确文本。
2. 调用 `wechat_file_transfer` 工具，并传入 `message`。
3. 如果用户明确指定了其它微信会话名，同时传入 `target`；否则不传 `target`，使用默认目标 `文件传输助手`。
4. 如果桥接服务无法连接，工具会优先尝试通过默认 PowerShell 脚本自动启动 Windows 侧服务；用户设置 `WECHAT_BRIDGE_START_CMD` 时工具自动使用该命令覆盖默认启动方式。
5. 不要调用 `bash` 使用 `curl`、`wget`、`ps aux | grep` 等命令探测或启动微信 bridge；这些外联/进程探测组合会被 shell 沙箱拒绝。bridge 的启动和健康检查交给 `wechat_file_transfer` 工具内部处理。
6. bridge 由工具自动管理和启动。若自动启动失败，显式提示用户登录微信客户端、确认 `winget` 或 Windows Python 3.12 可用，并给出这个手动启动命令：

   ```bash
   /init /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w services/wechat_bridge/start.ps1)"
   ```

7. 如果启用了 token 鉴权，WSL/客户端环境必须设置和 Windows 服务端相同的 `WX_BRIDGE_TOKEN`。

## 安全规则

- 这个工具会发送真实微信消息。除非用户清楚要求发送消息，否则不要调用。
- 这个 Skill 只发送文本，不读取微信私聊、群聊或其它私人内容。
