---
name: experiment-runner
description: 准备、冒烟测试、启动、监控科研实验并生成可复现报告。
---

# 实验执行流程

1. 先确认训练入口、配置、数据和资源需求。
2. 调用 `experiment_smoke_test`，通过后才可准备正式实验。
3. 调用 `experiment_prepare` 记录命令、Git、配置、随机种子、环境、日志和输出路径。
4. 经用户确认后调用 `experiment_start`，再用 `experiment_status` 检查状态。
5. 完成或失败后调用 `experiment_report`；报告中的指标必须来自日志，不得编造。
6. 通知失败不得改变实验本身的成功状态。