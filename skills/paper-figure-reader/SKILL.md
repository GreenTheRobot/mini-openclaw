---
name: paper-figure-reader
description: 面向科研论文和机器学习实验的多模态图像理解 skill。用户上传论文截图、paper figure、方法框架图、实验结果图、曲线图、柱状图、表格截图、消融实验图，并要求解释图中内容、提取方法流程/关键模块/实验指标/主要趋势/结论，或把图像信息整理进 paper summary、literature review、related work、复现报告时使用。触发词包括 论文图、图表、figure、table、plot、chart、method diagram、framework、实验曲线、结果表、消融、literature review 图像分析。
---

# Paper Figure Reader

## 目标

把论文图、实验图表和表格截图转换为可验证的结构化文本，用于单篇 paper summary、literature review、实验结果整理和论文复现报告。它是 `literature-review` 工作流的多模态补充阶段：当文献资料中包含重要 figure/table/screenshot 时，先用本 skill 提取图像证据，再交给 `literature-review` 做主题综合。

## 使用前提

- 用户应通过 `--image <path>` 提供至少一张清晰图片。
- 先描述可见内容，再做解释；不要凭论文题目或常识补全看不见的信息。
- 如果图中数字、坐标轴或小字不清楚，明确标为“无法可靠读取”。

## 图像类型判断

先把图片归为一种或多种类型：

- 方法框架图：模块、箭头、数据流、训练/推理流程。
- 实验曲线图：loss/accuracy/reward/F1 等随 step/epoch/setting 变化。
- 柱状图/折线图/散点图：不同方法、任务、数据集或指标的比较。
- 结果表格：方法名、数据集、指标、最好结果、消融结果。
- 论文页面截图：标题、摘要、正文段落、公式、图注或表注。

## 分析步骤

1. **视觉转写**
   - 列出图中可见文字：标题、图注、坐标轴、legend、方法名、表头、关键数字。
   - 保持原词，不要先翻译导致术语漂移。

2. **结构化提取**
   - 方法框架图：提取输入、输出、模块、箭头关系、训练/推理阶段。
   - 实验曲线：提取横轴、纵轴、曲线含义、趋势、收敛情况、异常点。
   - 结果表格：提取行/列含义、最好结果、次优结果、消融差异。

3. **科研解释**
   - 说明图支持的主要结论。
   - 区分图中直接可见事实与基于图像的合理推断。
   - 对精确数值保持谨慎；必要时建议回到 PDF/原表核对。

4. **接入 literature review**
   - 如果任务目标是文献综述，将图像信息转成 `literature-review` 可用的 evidence note。
   - 输出应包含：图像来源、图像类型、支持的论文贡献/方法/实验结论、局限或不确定点。

## 输出模板

```markdown
## Figure Analysis

- Image Type:
- Visible Text:
- Source / Paper Context:

### Structured Extraction
- Modules / Variables / Rows:
- Axes / Metrics / Columns:
- Relationships / Trends:
- Key Values:

### Research Interpretation
- Directly Supported Findings:
- Reasonable Inferences:
- Uncertain / Need Verification:

### Literature Review Note
- Contribution Evidence:
- Method Evidence:
- Experiment Evidence:
- Limitation Evidence:
```

## 质量约束

- 不编造图中不存在的文字、数值、方法名或结论。
- 不把“看起来更高/更低”写成精确数值，除非数值清晰可读。
- 如果用户要求写入报告，先生成结构化分析，再用 `write` 保存到合适路径，例如 `literature/<topic>/figure_notes.md`。
- 如果图像来自某篇论文，尽量与 `literature-review` 的单篇 summary 字段对齐：contribution、method、experiments、conclusion、limitations。
