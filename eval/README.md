# Eval 模块

`tracer.py` 是兼容入口；实际 Trace 实现在 `agent/tracer.py`。它以 JSONL 写入根 Agent、LLM 和工具 span 的开始/结束事件，包含父子关联、耗时、token、工具调用 ID、脱敏后的摘要和相对路径调度上下文。旧版逐步事件仍可读取。

```bash
# 概览、成本、终端回放和静态 HTML 报告
python -m eval.trace_cli summary traces/demo.jsonl
python -m eval.trace_cli cost traces/demo.jsonl
python -m eval.trace_cli replay traces/demo.jsonl --details
python -m eval.trace_cli render traces/demo.jsonl --format html --output traces/demo.html

# 均为只读：不会调用模型、不会再次执行工具
python -m eval.trace_cli simulate traces/demo.jsonl
python -m eval.trace_cli diagnose traces/demo.jsonl
```

若要显示估算费用，在运行报告命令前配置所用模型的价格（美元/百万 token）：

```bash
export OPENCLAW_INPUT_USD_PER_MILLION=0.27
export OPENCLAW_OUTPUT_USD_PER_MILLION=1.10
```

诊断会标出慢 span、失败、工具耗时与墙钟时间不一致、上下文 token 增长、重复工具调用、协议修复，以及稳定前缀变化。`run_suite.py` 在隔离目录执行科研任务并输出 CSV；没有真实模型 Key 时拒绝生成伪造消融数据。
