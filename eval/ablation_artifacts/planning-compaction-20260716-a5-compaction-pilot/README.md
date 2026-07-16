# A5 TODO 长上下文压缩保护：原始记录

两组均以 `--context-budget 6000` 运行相同的 12 dossier 发布审计。只有 trace 记录 compaction 且至少逐个读取 10 份 dossier 的运行才具备长上下文压力有效性。最终完成由本地 verifier 的 `release=pass` 判定，不能由模型文本替代。

> 汇总中的 dossier 读取数由 JSONL 工具 span 的 `read` 路径重新提取；原始 Trace 与 CLI 输出保持不变。
