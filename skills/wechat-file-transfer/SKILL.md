---
name: wechat-file-transfer
description: "当用户要求通过微信、文件传输助手、wxauto、Windows 微信客户端发送一条文本消息、提醒、摘要或结果通知时使用。适用于 mini-openclaw 主体运行在 WSL，而 Windows 侧通过 wxauto4 桥接服务实际操作微信的场景。"
---

# 微信文件传输助手 Skill

仅当用户明确要求向微信、文件传输助手或另一个指定微信会话发送文本时，才使用这个 Skill。

## 执行流程

1. 先确认要发送的精确文本。
2. 调用 `wechat_file_transfer` 工具，并传入 `message`。
3. 除非用户指定了其它微信会话，否则使用默认目标 `文件传输助手`。
4. 如果桥接服务无法连接，提示用户登录微信客户端并在具备 wxauto4 的 Windows 环境中启动 Windows 侧服务：

   ```powershell
   python services\wx_file_transfer_server.py
   ```

5. 如果启用了 token 鉴权，WSL/客户端环境必须设置和 Windows 服务端相同的 `WX_BRIDGE_TOKEN`。

## 安全规则

- 这个工具会发送真实微信消息。除非用户清楚要求发送消息，否则不要调用。
- 这个 Skill 只发送文本，不读取微信私聊、群聊或其它私人内容。
