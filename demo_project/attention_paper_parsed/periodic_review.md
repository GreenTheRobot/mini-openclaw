# Periodic Review Log

## Review Round 1

- **Review Time**: 2026-07-14 CST
- **Parser Used**: Marker (GPU 满足条件)
- **Total Images**: 6
- **Image List**:
  | # | Source Name | SHA256 |
  |---|-------------|--------|
  | 1 | `_page_2_Figure_0.jpeg` | `7dcf8ded...` |
  | 2 | `_page_3_Figure_1.jpeg` | `8c7cad58...` |
  | 3 | `_page_3_Picture_2.jpeg` | `2a96eb76...` |
  | 4 | `_page_12_Figure_1.jpeg` | `65b28e50...` |
  | 5 | `_page_13_Figure_0.jpeg` | `54c5413d...` |
  | 6 | `_page_14_Figure_0.jpeg` | `ed742d42...` |

### Verified Facts (from paper.md)
1. **论文标题**: "Attention Is All You Need" — Transformer 架构原始论文。
2. **解析器**: Marker（从图片命名 `marker-*.jpeg` 确认）。
3. **论文结构**: 包含 Abstract、1 Introduction、2 Background、3 Model Architecture（含 3.1 Encoder and Decoder Stacks、3.2 Attention 含 Scaled Dot-Product Attention 和 Multi-Head Attention）、参考文献 40 篇。
4. **图片内容**:
   - Figure 0 (page 2): 模型架构图（Encoder-Decoder 结构）。
   - Figure 1 (page 3): Scaled Dot-Product Attention 与 Multi-Head Attention 示意图。
   - Picture 2 (page 3): 公式/补充说明图。
   - Figure 3 (page 12): 编码器第 5 层自注意力可视化，展示长距离依赖（"making...more difficult"）。
   - Figure 4 (page 13): 第 5 层两个注意力头，涉及指代消解（anaphora resolution）。
   - Figure 5 (page 14): 编码器第 5 层自注意力头展示句子结构相关行为。
5. **参考文献**: 共 40 篇，涵盖 seq2seq、注意力机制、NMT 等领域。

### Unverified / Uncertain Items
1. 图片中具体数值（如注意力权重分布数值）未从 paper.md 文本中提取，需通过 `paper_figure_analyze` 进一步分析。
2. 论文的实验结果表格（BLEU 分数、训练成本等）在 paper.md 中未以结构化表格形式呈现，需确认是否在图片中。
3. 未验证图片 SHA256 是否与原始 PDF 嵌入图片一致（仅记录 manifest 中的值）。

### This Round's Artifacts
- **Read**: `attention_paper_parsed/paper.md` (61225 chars), `attention_paper_parsed/image_manifest.json` (6 entries)
- **Created**: `attention_paper_parsed/periodic_review.md` (this file)
- **Unmodified**: `paper.md`, `image_manifest.json`, `images/` directory

## Review Round 2

- **Review Time**: 2026-07-14 CST (Round 2)
- **Parser Used**: Marker (与 Round 1 一致，从图片命名 `marker-*.jpeg` 确认)
- **Total Images**: 6（与 Round 1 一致）
- **Image List**（与 Round 1 一致，SHA256 完整值已验证）:
  | # | Source Name | SHA256 |
  |---|-------------|--------|
  | 1 | `_page_2_Figure_0.jpeg` | `7dcf8ded143763ef7ae8ed303154e8e804ea07f3188e06f3aa7b1bf47b3fa480` |
  | 2 | `_page_3_Figure_1.jpeg` | `8c7cad581405836a5858326f4f00dea7c042ddba5059b8ead7c00d1ff1a3bc17` |
  | 3 | `_page_3_Picture_2.jpeg` | `2a96eb76f9cf8f64a14547e0ea7b82da8d849b421a44e1472e1d9106804946b2` |
  | 4 | `_page_12_Figure_1.jpeg` | `65b28e50b254d5956268a14f4a24f42276e855da080a7a5f88a991712cc8418a` |
  | 5 | `_page_13_Figure_0.jpeg` | `54c5413d3bd19b60c8b457b08cf06c14260410671ac0af1ce6f075fd30f34e09` |
  | 6 | `_page_14_Figure_0.jpeg` | `ed742d42c77bed08b4057158d349095b5528f118917ac321ed2f6f210dd5483c` |

### Verified Facts (from paper.md — Round 2 新增/细化)
1. **论文完整标题与元数据**: "Attention Is All You Need"，PDF 元数据包含完整目录结构，涵盖 Abstract、1 Introduction、2 Background、3 Model Architecture（含 3.1 Encoder and Decoder Stacks、3.2 Attention 含 Scaled Dot-Product Attention 和 Multi-Head Attention）、3.3 Position-wise Feed-Forward Networks、3.4 Embeddings and Softmax、3.5 Positional Encoding、4 Why Self-Attention、5 Training（含 5.1-5.4 子节）、6 Results（含 6.1-6.4 子节）、7 Conclusion、参考文献 40 篇。
2. **图片与正文关联验证**:
   - Figure 0 (page 2): 模型架构图，位于 3.1-3.2 节附近。
   - Figure 1 (page 3): Scaled Dot-Product Attention 与 Multi-Head Attention 示意图，位于 3.2.1-3.2.2 节。
   - Picture 2 (page 3): 公式/补充说明图，位于 3.2.1 节附近。
   - Figure 3 (page 12): 编码器第 5 层自注意力可视化，展示动词 "making" 的长距离依赖（"making...more difficult"），不同颜色代表不同注意力头。
   - Figure 4 (page 13): 第 5 层两个注意力头，涉及指代消解（anaphora resolution），head 5 和 head 6 对 "its" 的注意力非常尖锐。
   - Figure 5 (page 14): 编码器第 5 层自注意力头展示句子结构相关行为，不同头学习执行不同任务。
3. **参考文献完整性**: 共 40 篇，编号 [1]-[40]，涵盖 seq2seq (Sutskever et al.), 注意力机制 (Bahdanau et al., Luong et al.), NMT (Wu et al., Gehring et al.), Transformer 同期工作等。
4. **图片路径一致性**: manifest 中 `path` 字段包含 `demo_project/` 前缀，而 `source_name` 为纯文件名；实际磁盘文件为 `attention_paper_parsed/images/marker-*.jpeg`，与 manifest 中 `path` 的 basename 一致。

### Unverified / Uncertain Items（Round 2 更新）
1. **实验结果表格未结构化提取**: paper.md 中第 6 节（Results）的 BLEU 分数、训练成本等实验数据以段落文本形式存在，未以 markdown 表格结构化呈现，需通过 `paper_figure_analyze` 分析对应图片或手动结构化。
2. **图片 SHA256 完整性**: manifest 中的 SHA256 已完整记录（Round 2 使用完整值），但未与原始 PDF 嵌入图片交叉验证。
3. **图片中精确数值**: 注意力权重分布、BLEU 分数等数值在图片中，未通过 `paper_figure_analyze` 提取。
4. **论文具体实验配置**: 训练超参数（batch size、学习率调度细节等）在 paper.md 中以文本形式存在，但未单独提取为结构化配置表。

### This Round's Artifacts
- **Read**: `attention_paper_parsed/paper.md` (61225 chars, 重新读取确认内容稳定), `attention_paper_parsed/image_manifest.json` (6 entries, 完整 SHA256 值)
- **Read (preexisting)**: `attention_paper_parsed/periodic_review.md` (Round 1 记录)
- **Appended**: `attention_paper_parsed/periodic_review.md` (Round 2 记录)
- **Unmodified**: `paper.md`, `image_manifest.json`, `images/` directory

## Review Round 3

- **Review Time**: 2026-07-14 CST
- **Parser Used**: Marker（与 Round 1/2 一致，从图片命名 `marker-*.jpeg` 确认）
- **Total Images**: 6（与 manifest 一致）
- **Image List**:
  | # | Source Name | Disk File | SHA256 (from manifest) |
  |---|-------------|-----------|------------------------|
  | 1 | `_page_2_Figure_0.jpeg` | `images/marker-001.jpeg` | `7dcf8ded143763ef7ae8ed303154e8e804ea07f3188e06f3aa7b1bf47b3fa480` |
  | 2 | `_page_3_Figure_1.jpeg` | `images/marker-002.jpeg` | `8c7cad581405836a5858326f4f00dea7c042ddba5059b8ead7c00d1ff1a3bc17` |
  | 3 | `_page_3_Picture_2.jpeg` | `images/marker-003.jpeg` | `2a96eb76f9cf8f64a14547e0ea7b82da8d849b421a44e1472e1d9106804946b2` |
  | 4 | `_page_12_Figure_1.jpeg` | `images/marker-004.jpeg` | `65b28e50b254d5956268a14f4a24f42276e855da080a7a5f88a991712cc8418a` |
  | 5 | `_page_13_Figure_0.jpeg` | `images/marker-005.jpeg` | `54c5413d3bd19b60c8b457b08cf06c14260410671ac0af1ce6f075fd30f34e09` |
  | 6 | `_page_14_Figure_0.jpeg` | `images/marker-006.jpeg` | `ed742d42c77bed08b4057158d349095b5528f118917ac321ed2f6f210dd5483c` |

### Verified Facts (Round 3 新增/更新)
1. **文件完整性验证**: paper.md (61225 chars)、image_manifest.json (6 entries)、images/ 目录下 6 个 JPEG 文件全部存在，数量与 manifest 完全匹配。
2. **内容稳定性**: paper.md 内容与 Round 2 读取时一致，无变化（仍为 61225 字符）。
3. **磁盘文件存在性**: 通过 `glob` 确认 `attention_paper_parsed/images/marker-001.jpeg` 至 `marker-006.jpeg` 全部存在。
4. **图片路径映射验证**: manifest 中 `path` 字段包含 `demo_project/` 前缀（如 `demo_project/attention_paper_parsed/images/marker-001.jpeg`），而实际磁盘路径为 `attention_paper_parsed/images/marker-001.jpeg`。`source_name` 字段（如 `_page_2_Figure_0.jpeg`）为纯文件名，与磁盘文件名不同但可通过 manifest 索引一一对应。
5. **SHA256 记录完整性**: manifest 中 6 张图片的 SHA256 均为完整 64 字符十六进制值，格式正确。未通过 `sha256sum` 命令交叉验证（shell 执行受限）。

### Unverified / Uncertain Items（Round 3 更新）
1. **SHA256 交叉验证未完成**: manifest 中的 SHA256 值未与磁盘文件实际哈希值比对（需要 `sha256sum` 命令，当前 shell 受限）。
2. **实验结果表格未结构化提取**: paper.md 中第 6 节（Results）的 BLEU 分数、训练成本等实验数据仍以段落文本形式存在，未以 markdown 表格结构化呈现。
3. **图片中精确数值未提取**: 注意力权重分布、BLEU 分数等数值在图片中，未通过 `paper_figure_analyze` 分析。
4. **训练超参数未结构化**: 训练配置（batch size、学习率调度等）在 paper.md 中以文本形式存在，未提取为结构化配置表。

### This Round's Artifacts
- **Read**: `attention_paper_parsed/paper.md` (61225 chars, 内容稳定), `attention_paper_parsed/image_manifest.json` (6 entries, 完整 SHA256), `attention_paper_parsed/periodic_review.md` (Round 1-2 记录)
- **Glob**: `attention_paper_parsed/images/*` (6 files 全部存在)
- **Appended**: `attention_paper_parsed/periodic_review.md` (Round 3 记录)
- **Unmodified**: `paper.md`, `image_manifest.json`, `images/` directory
