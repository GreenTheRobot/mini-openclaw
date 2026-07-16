---
name: codebase-research
description: 分析科研代码库，定位训练入口、模型、配置、数据、损失、日志和输出目录。
---

# 科研代码理解流程

1. 用 `glob` 查看结构，用 `grep` 搜索 main/train/config/dataloader/loss/output 等线索。
2. 用 `read` 验证候选文件，不根据文件名猜测实现。
3. 回答必须给出具体路径；必要时附函数或类名。
4. 修改遵循 `read → edit → bash`，先冒烟测试，再运行正式实验。
5. 输出训练入口、调用链、配置来源、数据流、损失、日志和结果路径。