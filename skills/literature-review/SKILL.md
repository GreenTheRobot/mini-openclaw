---
name: literature-review
description: 面向特定领域论文复现/调研的 literature review 文献综述 skill。用户要求文献检索、网页检索、论文网页抓取、PDF 下载、补充材料下载、GitHub 仓库拉取、PDF 解析、论文阅读、提取贡献/方法/实验/结论、生成单篇 paper summary、汇总多篇文献并撰写 literature review 报告时使用。触发词包括 文献综述、literature review、survey、related work、论文调研、paper summary、arXiv、PDF、GitHub 论文仓库、网页抓取、maker、marker、markitdown。
---

# Literature Review

## 目标

帮助用户从目标研究方向出发，完成“检索与抓取 → PDF/补充材料/代码收集 → PDF 解析 → 单篇论文结构化总结 → 多篇论文主题综合 → literature review 报告”的端到端流程。默认服务于论文复现和科研选题调研，禁止虚构论文内容、实验结果或引用。

## 工作流

1. **任务界定**
   - 明确研究主题、时间范围、目标领域、综述类型：快速调研、related work、系统性综述、复现导向综述。
   - 如果问题太宽，先生成 2-4 个可检索子问题和关键词组。
   - 记录纳入/排除标准：年份、会议/期刊、任务、模型类型、数据集、是否必须有代码。

2. **网页检索与抓取**
   - 优先用 `web_search` 搜论文、项目页、arXiv、OpenReview、ACL Anthology、Papers with Code、GitHub。
   - 用 `web_fetch` 抓取论文页、项目页、README、排行榜页和补充材料页面。
   - 没有 web 工具时，用 `bash` 调用可用命令行工具，或让用户提供 URL/PDF/本地目录。
   - 对每条候选文献记录：标题、作者、年份、venue/arXiv、URL、PDF URL、代码 URL、数据集/任务关键词。

3. **下载 PDF、补充材料和代码**
   - 用 `bash` 下载 PDF 和 supplement，保存到清晰目录，例如 `literature/<topic>/pdfs/`。
   - 如果用户要求复现导向调研，优先收集 GitHub 仓库链接；需要本地分析时再 clone。
   - 不要下载无关大文件；下载失败时记录失败原因和源链接。

4. **选择 PDF 解析工具**
   - 优先用 `markitdown`：适合快速把 PDF、网页导出文档、docx/pptx 等转成 markdown，速度优先。CPU 上可用。
   - 优先用 `marker`：适合版式复杂、公式/表格/多栏论文，或需要更高质量 markdown/结构保留时。此方法需要 GPU ，使用前必须检查 GPU 是否可用及其占用情况，如条件不允许则回退至 markitdown 并告知用户 PDF 解析降级。
   - 如果两者都不可用，用 `bash` 检查可用替代工具；仍不可用时说明限制，并基于网页摘要/README/用户提供文本工作。
   - 解析后保存 markdown，例如 `literature/<topic>/parsed/<paper_id>.md`，再用 `read` 阅读，不要凭文件名猜内容。

5. **论文图表与多模态补充**
   - 当用户提供论文图、方法框架图、实验曲线、结果表格截图，或 PDF 解析结果缺失关键 figure/table 信息时，配合 `paper-figure-reader` skill。
   - 先用多模态能力提取图中可见文字、模块、坐标轴、指标、趋势和不确定项。
   - 将图像证据写入单篇 summary 的 contribution、method、experiments、limitations 字段；不要把看不清的数值写成确定结论。

6. **单篇论文阅读与 summary**
   - 对每篇论文至少抽取：
     - bibliographic：标题、作者、年份、venue、链接
     - problem：研究问题和应用场景
     - contribution：核心贡献，区分作者声称和可验证事实
     - method：方法框架、关键模块、训练/推理流程
     - experiments：数据集、baseline、指标、主要结果
     - conclusion：结论、适用边界、局限性
     - reproducibility：代码/数据/配置是否可得，复现风险
   - 单篇 summary 应短而结构化，避免整段复述摘要。

7. **多篇文献综合**
   - 不要只堆叠“逐篇摘要”。按主题、方法路线、任务设置、数据集、评价指标或时间线组织。
   - 对比每类方法的共同点、差异、优势、局限、证据强度和未解决问题。
   - 标出研究空白：缺少数据、缺少公平比较、评价指标不统一、无代码、只在小规模数据验证等。

8. **生成 literature review 报告**
   - 用 `write` 输出 markdown 报告，建议结构：
     - 研究问题与检索范围
     - 检索来源、关键词、纳入/排除标准
     - 文献清单表
     - 单篇 paper summary 表
     - 主题综合分析
     - 方法/数据集/指标对比
     - 研究空白与未来方向
     - 复现建议
     - 限制与未验证事项
   - 报告中每个事实性判断尽量附来源链接或论文标识。

## 工具使用约定

- `web_search`：发现候选论文、项目页、代码仓库和数据集。
- `web_fetch`：抓取论文页、README、项目主页、补充材料页。
- `bash`：下载 PDF、运行 `marker`/`markitdown`、clone GitHub、统计文件、检查环境。
- `glob`/`grep`：定位本地 PDF、解析后的 markdown、README、代码入口、关键词。
- `read`：阅读解析后的 markdown、README、日志和中间结果。
- `write`：写入单篇 summary、文献矩阵和最终 review 报告。
- `edit`：只用于修订已有报告或修正小范围记录；修改前必须先 `read`。

## 输出模板

单篇 summary：

```markdown
## <Paper Title> (<Year>)

- Source: <URL / DOI / arXiv>
- Problem:
- Contributions:
- Method:
- Experiments:
- Key Findings:
- Limitations:
- Reproducibility Notes:
```

文献矩阵：

```markdown
| Paper | Year | Task | Method | Dataset | Metrics | Code | Main Finding | Limitation |
|---|---:|---|---|---|---|---|---|---|
```

最终报告文件名优先使用：

```text
literature/<topic>/literature_review.md
```

## 质量约束

- 不编造论文、作者、实验数字、DOI、GitHub 地址或引用。
- 区分“论文声称”“解析得到的事实”“模型推断”“未验证”。
- 检索结果不足时明确说明覆盖不足，并给出下一步检索关键词。
- 如果 PDF 解析质量差，先尝试换工具或抓取 HTML/arXiv 页面，不要基于乱码总结。
- 对复现导向综述，必须单独记录代码可用性、依赖风险、数据可用性和实验入口。
