# Agent 模块

`loop.py` 实现 ReAct 主循环、参数校验、权限、重复调用限制、错误恢复、上下文压缩和 Trace；`context.py` 管理长上下文；`memory.py` 提供 Markdown 与 KV 跨会话记忆；`permissions.py` 实现 allow/confirm/deny。

## 上下文压缩

`agent/context.py` 的压缩入口是 `maybe_compact(messages, backend, budget=6000, recent_turns=2)`。当前实现不依赖 tokenizer，而是用 `estimate_tokens()` 按消息 `content` 字符数做粗估：所有 content 转字符串后求长度，再除以 2。这个估算偏保守、实现简单，适合在没有模型专用 tokenizer 的 CLI 环境里快速判断是否需要压缩。

触发条件：

- 估算 token 数不超过 `budget` 时不压缩。
- 消息数小于等于 3 时不压缩。
- 否则进入压缩流程。

压缩流程：

1. 保留第一条 `system` 消息作为系统提示主体。
2. 从历史尾部向前扫描一段协议安全（防止消息截断导致的 400 Bad Request）的最近上下文，最多约 `RECENT_MAX_TOKENS = 2000`。
3. 剩余较早历史交给 `_summarize()` 生成结构化摘要。
4. 把摘要追加到 system 消息末尾，标题为 `# 历史压缩备忘（必须继续遵循）`。
5. 压缩后的消息形态为：

   ```text
   system: 原系统提示 + 历史压缩备忘
   ...安全的最近上下文...
   user: 请根据系统消息中的历史压缩备忘和保留的最近上下文继续完成当前任务...
   ```

如果安全最近上下文本身以 `user` 结尾，则不会额外追加 continuation user；否则追加一条 continuation user，避免最后一条消息停在 `assistant` 或 `tool` 上，让模型能自然继续任务。

### 协议安全后缀

压缩不能切断 OpenAI/DeepSeek 的 tool call 协议。`_safe_recent_suffix()` 从消息尾部按块保留最近上下文：

- 普通 `user`、普通 `assistant` 消息可以单独作为块保留。
- `assistant` 如果带 `tool_calls`，不能单独保留。
- `tool` 消息必须和它前面的 `assistant.tool_calls` 一起保留。
- 多个连续 `tool` 消息必须刚好覆盖前一个 assistant 声明的全部 tool call id，才算完整块。
- 孤立 `tool`、半截 `assistant.tool_calls`、缺失 tool result 的块都会停止后缀保留，进入摘要。

这个设计避免压缩后出现非法消息序列，例如只留下 `tool` 消息，或留下带 `tool_calls` 的 assistant 但缺少对应 tool result。这样可以避免 DeepSeek/OpenAI 兼容接口返回 `400 Bad Request`。

### 摘要策略

`_summarize()` 会先把待压缩消息转换成纯文本。转换时保留：

- role
- name
- tool_call_id
- tool call 的 name 与 arguments
- content

单条消息文本最长由 `MESSAGE_MAX_CHARS = 1200` 控制，避免大 observation 原样塞进摘要 prompt。摘要提示要求模型使用固定标题：

```text
## 原始任务目标
## 用户硬性约束
## 已完成步骤
## 已修改文件
## 关键工具结果
## 失败与原因
## 当前任务清单
## 下一步
```

摘要结果最终由 `SUMMARY_MAX_CHARS = 3000` 截断。如果摘要调用失败，或模型返回空内容，则使用 `_fallback_summary()`：它取最近 10 条待摘要消息生成本地摘要，保证主循环不会因为压缩摘要失败而中断。

压缩后如果估算 token 仍不小于压缩前，会把摘要进一步截断到 1000 字符。这是为了防止“压缩后比原上下文更长”的失败模式。

### Observation 截断

`truncate_observation()` 用于单条工具结果过长时的局部截断。默认 `max_chars=4000`，保留头尾各一半，并在中间写入截断说明。如果调用方提供 `archive_path`，结果开头会注明完整内容保存位置。这样模型能看到关键信息，同时完整日志仍可在工作目录中追溯。

### 设计取舍

- 保留系统提示而不是重建系统提示，避免丢失已加载 Skill 和运行约束。
- 摘要较早历史，同时保留最近的协议安全块，兼顾上下文连续性和 API 消息合法性。
- 不追求精确 token 计算，换取无额外依赖和跨后端稳定性。
- **不在关键 tool call 块中间切断，优先保证 DeepSeek/OpenAI 消息协议合法。**
- **摘要失败时降级为本地 fallback summary，优先保证任务可继续。**
