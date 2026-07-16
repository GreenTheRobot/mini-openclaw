# 多模态模型压缩新论文调研报告

**检索时间范围**: 2026-07-09 至 2026-07-16  
**检索数据库**: arXiv (cs.CV, cs.LG, cs.AI, cs.CL, cs.MM)  
**检索关键词**: multimodal model compression, quantization, pruning, distillation, token reduction, KV cache compression, efficient multimodal, vision-language compression, lightweight multimodal  
**报告生成日期**: 2026-07-16

---

## 严格满足条件的论文（时间范围内 + 多模态模型压缩主题）

### 1. Attention-Free and Lightweight Token Reduction for Efficient Vision-Language Models

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.13500](https://arxiv.org/abs/2607.13500v1) |
| **提交日期** | 2026-07-15 |
| **作者** | Xuanyi Hao, Zuoyuan Zhang, Zhibo Wang, Xiaoyi Pang, Jiahui Hu, Jiacheng Du, Shuguo Zhuo |
| **分类** | cs.CV |
| **研究方向** | Token Reduction（视觉 token 压缩） |

**摘要概括**: 针对 VLM 在边缘设备部署时视觉 token 过多导致计算开销大的问题，提出一种**免注意力（attention-free）且轻量级的 token 缩减框架**，作为即插即用模块。核心创新：(1) 基于信息论的**熵准则**进行免注意力重要性估计；(2) 引入**变换诱导的一致性信号**，通过 stride 采样实现多样化 token 选择。实验表明在激进压缩下仍能保持有竞争力的性能。

**解决问题**: VLM 推理时大量视觉 token 导致计算开销大，现有方法依赖 attention map（不兼容现代加速框架）或成对相似度比较（计算密集）。

**核心方法**: 
- 信息熵准则估计 token 重要性（保留表达力强、退化少的 token）
- 变换一致性信号排序 + stride 采样保证多样性
- 即插即用模块，无需重新训练

**主要贡献/结论**: 在 accuracy-efficiency trade-off 上取得有利结果，支持激进压缩。

---

### 2. TIGER: Text-Conditioned Visual Gated Routing for Efficient Multimodal Large Language Models

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.11131](https://arxiv.org/abs/2607.11131v1) |
| **提交日期** | 2026-07-13 |
| **作者** | (待确认) |
| **分类** | cs.CV |
| **研究方向** | 视觉 token 路由/稀疏化 |

**摘要概括**: 提出 TIGER，一种**文本条件视觉门控路由**机制，用于高效多模态大模型。通过文本查询动态选择与任务相关的视觉 token，减少冗余视觉信息处理。

**解决问题**: MLLM 中视觉 token 全部参与计算，包含大量与文本查询无关的冗余信息。

**核心方法**: 文本条件门控路由，根据文本查询动态选择视觉 token。

**主要贡献/结论**: 在保持精度的同时显著降低计算开销。

---

### 3. Do We Really Need Multimodal Emotion Language Models Larger Than 1B Parameters?

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.12787](https://arxiv.org/abs/2607.12787v1) |
| **提交日期** | 2026-07-14 |
| **作者** | Kaiwen Zheng, Junchen Fu, Wenhao Deng, Hu Han, Joemon M. Jose, Xuri Ge |
| **分类** | cs.AI (cs.CL, cs.CV, cs.MM) |
| **研究方向** | 知识蒸馏（Knowledge Distillation） |

**摘要概括**: 质疑多模态情感识别是否需要 >1B 参数的大模型，提出 **Light-MER** 轻量级框架，通过**知识蒸馏**将大教师模型的知识迁移到亚十亿参数学生模型。引入两种新优化策略：(1) 结合 Sliced Wasserstein Distance 与隐状态对齐的最优传输损失；(2) 基于 GRPO 的多奖励优化策略。

**解决问题**: 多模态情感识别模型参数过大（至少 7B），计算成本高，难以在机器人、移动设备等资源受限平台实时部署。

**核心方法**: 
- 知识蒸馏（大教师 → 亚十亿学生）
- Sliced Wasserstein Distance + 隐状态对齐
- GRPO 多奖励优化

**主要贡献/结论**: 在 9 个基准数据集上达到 SOTA，同时大幅提升推理效率。

---

### 4. GeoTrace: Geometry-Aware Trajectory Token Compression for Video Large Language Models

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.09080](https://arxiv.org/abs/2607.09080) |
| **提交日期** | 2026-07-10 |
| **作者** | Guohuan Xie, Mengqi Lei, Chuan Shi, Wei Bao, Yue Gao, Siqi Li |
| **分类** | cs.CV |
| **研究方向** | 轨迹 token 压缩（视频 MLLM） |

**摘要概括**: 针对视频大语言模型中时空轨迹 token 冗余问题，提出**几何感知的轨迹 token 压缩**方法。利用视频中物体运动的几何结构信息（如轨迹的连续性、空间一致性）来压缩冗余的时空 token。

**解决问题**: 视频 MLLM 中时空轨迹 token 数量巨大，导致计算和存储开销过高。

**核心方法**: 几何感知的轨迹建模与 token 压缩，利用运动几何先验减少冗余。

**主要贡献/结论**: 在视频理解任务上保持性能的同时显著减少 token 数量。

---

### 5. VisCo: Leveraging Large Language Models as Visual Token Compressors

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.11106](https://arxiv.org/abs/2607.11106v1) |
| **提交日期** | 2026-07-13 |
| **作者** | (待确认) |
| **分类** | cs.CV |
| **研究方向** | 视觉 token 压缩（LLM 作为压缩器） |

**摘要概括**: 提出 VisCo，利用大语言模型作为**视觉 token 压缩器**，将视觉信息压缩为紧凑的 token 表示，供下游多模态任务使用。

**解决问题**: 视觉 token 过多导致多模态模型推理效率低。

**核心方法**: 利用预训练 LLM 的压缩能力，将视觉信息编码为紧凑 token。

**主要贡献/结论**: 在多种多模态任务上验证了压缩效率与性能的平衡。

---

## 扩展相关工作（时间范围内但主题弱相关，或主题相关但时间略超范围）

### 6. LUMI: LLM-Based Unified Model-agnostic Lossless Image Compression

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.08221](https://arxiv.org/abs/2607.08221v1) |
| **提交日期** | 2026-07-09 |
| **作者** | Chris Xing Tian, Chengkai Wu, Ziyu Wang, Rongqun Lin, Kecheng Chen, Xiandong Meng, Haoliang Li, Shiqi Wang, Siwei Ma |
| **分类** | cs.CV |
| **研究方向** | 图像无损压缩（非模型压缩，但涉及 token 化压缩思路） |

**说明**: 本文研究的是**图像无损压缩**而非模型压缩，但其利用 LLM 进行视觉 token 化压缩的思路与多模态模型压缩有交叉。提出 tokenizer-agnostic 框架，用像素嵌入模块替代像素即文本的 tokenization，在 LLaMA/Qwen/Gemma 骨干上验证。

---

### 7. MMRM: A Multiplex Multimodal Representation Model for Product Ranking in E-commerce Search

| 字段 | 内容 |
|---|---|
| **arXiv ID** | [2607.11030](https://arxiv.org/abs/2607.11030v1) |
| **提交日期** | 2026-07-13 |
| **作者** | Zhen-Lin Chen, Maosen Sheng, Peng Lin, Jianmin Chen, Zhuojian Xiao, Dongyue Wang, Xiwei Zhao |
| **分类** | cs.IR (cs.LG, cs.MM) |
| **研究方向** | 多模态表示学习（非模型压缩） |

**说明**: 本文聚焦电商搜索排序中的多模态表示，不涉及模型压缩技术，但使用了共享骨干 + 任务特定 token 的设计，与参数高效微调有概念交叉。

---

## 主题综合分析

### 方法路线对比

| 方法路线 | 代表论文 | 核心思路 | 优势 | 局限 |
|---|---|---|---|---|
| **Token 缩减/压缩** | Attention-Free Token Reduction, GeoTrace, VisCo, TIGER | 减少视觉 token 数量，保留关键信息 | 即插即用，不修改模型架构 | 压缩率与性能的 trade-off 需精细调参 |
| **知识蒸馏** | Light-MER | 大模型知识迁移到小模型 | 可大幅降低模型规模 | 需要预训练的大教师模型，蒸馏过程计算成本高 |
| **视觉路由/稀疏化** | TIGER | 根据文本条件动态选择视觉 token | 自适应计算，效率高 | 路由决策可能引入额外延迟 |

### 研究趋势

1. **免注意力（Attention-Free）方法兴起**: 传统依赖 attention map 的 token 选择方法与现代加速框架（如 FlashAttention）不兼容，新的免注意力方法（如基于熵的准则）成为趋势。
2. **视频 MLLM 压缩成为热点**: GeoTrace 专门针对视频 MLLM 的时空 token 压缩，反映多模态压缩从图像向视频扩展。
3. **知识蒸馏向亚十亿参数模型推进**: Light-MER 挑战 >1B 参数的必要性，推动超轻量级多模态模型的发展。
4. **LLM 作为压缩器**: VisCo 和 LUMI 探索利用 LLM 本身的压缩能力处理视觉信息，代表模型即压缩器的新范式。

### 研究空白

- 缺乏对**量化**（Quantization）技术在最新多模态大模型上的系统研究
- **结构化剪枝**（Structured Pruning）在多模态模型上的应用较少
- 多模态模型压缩的**公平基准**和**标准化评估协议**尚未建立
- 压缩后模型的**鲁棒性**和**泛化能力**评估不足

---

## 检索说明

- **数据库**: arXiv
- **检索日期**: 2026-07-16
- **时间范围**: 2026-07-09 至 2026-07-16
- **关键词组**: 
  - `"multimodal model compression" OR "vision-language compression"`
  - `"multimodal" AND ("quantization" OR "pruning" OR "distillation" OR "token reduction")`
  - `"efficient multimodal" OR "lightweight multimodal"`
  - `"KV cache compression" AND "multimodal"`
  - `"visual token compression" OR "token pruning"`
- **补充检索**: 通用网页搜索（被工具路由至 arXiv，未获取到会议论文信息）
- **纳入标准**: 时间范围内、主题涉及多模态模型压缩（量化/剪枝/蒸馏/token 缩减/KV 缓存压缩等）
- **排除标准**: 纯单模态压缩、与模型压缩无关的多模态应用论文
