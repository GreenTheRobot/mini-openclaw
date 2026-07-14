# B4 Debug 演示：未修复的数据对齐 Bug

这是明天展示用的独立故障版本。请勿预先修改 `data_pipeline.py`；它应当在修复前稳定失败，以便展示 Agent 的“读文件 → 定位 → 最小修复 → 跑测试验证”流程。

## 复现

```bash
cd demo/debug_data_pipeline_broken
python train.py
python -m unittest discover -s tests -v
```

预期报错：

```text
ValueError: prediction/target count mismatch: 2 != 1. Check that preprocessing filters features and labels together.
```

## 展示指令

在项目根目录运行：

```bash
python -m agent.cli "这个报错咋改：运行 demo/debug_data_pipeline_broken/train.py 时出现 ValueError: prediction/target count mismatch: 2 != 1. Check that preprocessing filters features and labels together. 请先阅读相关文件，做最小修复，再运行单元测试；没有验证通过不要说修复完成。"
```

修复后应输出：

```text
训练样本: 4
验证样本: 1
验证 MAE: 0.000
```
