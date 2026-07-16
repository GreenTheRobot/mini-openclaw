# Multi Agent Ablation 测试报告

## 1. 测试口径

本次对比的是同一个图像理解任务：

> 帮我看看 `demo_project/attention_paper_parsed/images` 里的 6 张图片分别是什么内容。

对比 trace：

| 模式           | Trace                                                                         | 说明                                                                                      |
| -------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 有 multi agent | `traces/ablation.jsonl` + `traces/subagents/ablation.image-analysis-1*.jsonl` | 主 Agent 启用编排，工作下沉到 Multimodal 子 Agent，并在子 Agent 内继续拆成 6 个图片子任务 |
| 无 multi agent | `traces/ablation-nomultiagent.jsonl`                                          | 单 Agent 直接 ReAct，连续调用工具完成全部图片分析和最终总结                               |

## 2. 结果概览

| 指标              |                                             有 multi agent |                             无 multi agent |
| ----------------- | ---------------------------------------------------------: | -----------------------------------------: |
| 顶层运行结果      |                                              review passed |                                run success |
| 顶层总耗时        |                                                    约 739s |                                    约 441s |
| 主执行链耗时      |                                    Multimodal root 约 587s |                           单 Agent 约 441s |
| LLM 调用次数      |                                          64 次（含子任务） |                                      12 次 |
| 工具调用次数      |                                   56 次（49 成功，7 失败） |                   18 次（13 成功，5 失败） |
| 总 token          |                                                 约 961,688 |                                 约 215,420 |
| 最终阻塞/修订信号 | 顶层 synthesis 阶段 2 次 `final_blocked`，随后 review 通过 | 1 次 `final_blocked`，随后继续补证据并成功 |

结论：这次 ablation 中，multi agent 在总耗时和 token 成本上没有胜出，主要因为 `marker-004` 子任务多次调用 `paper_figure_analyze` 失败并触发了较长 fallback ，在此期间其他子任务均并行执行完毕；但它展示了更清晰的任务隔离、更强的可审查性，以及在局部子任务异常时继续综合交付的能力。

## 3. Multi Agent 技术路径

当前 multi agent 路径是轻量级编排，而不是独立系统之间的硬切分：

1. 主 Agent 先做 orchestration 判断，输出 `use_subagents`、`reason`、`main_task`、`subagents` / `assignments`。
2. 调度层按 role 选择提示词和能力边界：Research、Engineering、Multimodal。
3. 子 Agent 复用现有 `AgentLoop`、权限层、工具注册表和 trace 机制，但拥有独立任务上下文和独立 trace 文件。
4. 并行执行使用 `ThreadPoolExecutor`，每个子 Agent 完成后返回正文输出和工具证据。
5. 子 Agent 也能调用 `subagent_dispatch` 继续拆分任务，当前最多支持多层递归调度。
6. Synthesis 阶段把 `## Main Agent`、`## Multimodal Agent` 等结果块合并为最终答案。
7. Reviewer 基于子 Agent 工具调用记录和输出进行审查；如发现证据不足，会触发修订或复审。

本次实际路径如下：

```text
Main Agent
  -> Multimodal Agent: 分析 images 目录下 6 张图片
       -> subagent_dispatch: 按图片拆成 6 个 multimodal 子任务
            -> img1: marker-001.jpeg
            -> img2: marker-002.jpeg
            -> img3: marker-003.jpeg
            -> img4: marker-004.jpeg
            -> img5: marker-005.jpeg
            -> img6: marker-006.jpeg
       -> 汇总 6 张图的内容
  -> Synthesis
  -> Reviewer
```

这条路径的优点是图片之间天然独立，可以并行分析；缺点是本次主编排先派了一个宽泛 Multimodal Agent，它二次拆分 6 张图，产生了额外编排和总结成本。更理想的路径是主 Agent 直接 dispatch 6 个图片子 Agent ，这有赖于主 agent 自身的决策能力，但反映了本项目自身具有多层递归分发的能力。

## 4. 任务效果

### 4.1 输出质量

有 multi agent 的最终审查结果为 `passed`。Reviewer 认为待审答案准确反映了 6 张图片的分析结果，和子 Agent 汇总一致，并对不确定 token 做了标注。

无 multi agent 最终也成功输出了完整报告，但中间经历了更多单链路补救：首次成稿后被判定 `insufficient_research_answer`，随后补读论文、manifest 和 grep 证据才完成最终回答。

从交付质量看，两种模式都能完成任务；multi agent 的优势在于最终结论背后有分图片 trace 和工具证据，可回溯性更强。

### 4.2 鲁棒性

multi agent 中 6 个图片子任务里，5 个成功，`img4` 最终状态为 `partial`。尽管 `img4` 多次遇到 `paper_figure_analyze` execution error，父级 Multimodal Agent 仍然完成了综合汇总，顶层 review 通过。

无 multi agent 也遇到了一次 `paper_figure_analyze` 失败，失败对象是 `marker-006.jpeg`，随后通过再次调用恢复。单 Agent 的恢复依赖同一上下文继续变长，失败和补救都堆在一个主循环里。

因此，multi agent 对局部失败更隔离：单张图的异常不会直接污染其他图片分析；但如果某个子任务反复失败，仍会显著拖慢总耗时。

子任务明细：

| 子任务                   |    状态 |    耗时 | 主要情况                                                    |
| ------------------------ | ------: | ------: | ----------------------------------------------------------- |
| `img1 / marker-001.jpeg` | success |  约 86s | 正常完成                                                    |
| `img2 / marker-002.jpeg` | success | 约 101s | 正常完成                                                    |
| `img3 / marker-003.jpeg` | success | 约 131s | 1 次 `read` 参数校验失败后自恢复                            |
| `img4 / marker-004.jpeg` | partial | 约 470s | 3 次 `paper_figure_analyze` execution error，是本次实验拖尾 |
| `img5 / marker-005.jpeg` | success | 约 104s | 1 次 `read` 参数校验失败后自恢复                            |
| `img6 / marker-006.jpeg` | success | 约 119s | 正常完成                                                    |

### 4.3 性能与成本

本次 multi agent 没有体现速度优势：

- 有 multi agent 顶层耗时约 739s，Multimodal root 约 587s。
- 无 multi agent 耗时约 441s。
- multi agent 总 token 约 96.2 万，是无 multi agent 约 21.5 万的 4.5 倍。

主要原因不是并行机制无效，而是这次实际路径存在两个成本放大点：

1. 两级拆分：Main -> Multimodal root -> 6 个图片子 Agent，多了一层中间 agent 的规划、读取和汇总。
2. 慢子任务拖尾：`img4` 耗时约 470s，消耗约 42 万 token，是总成本的主要来源。

并行系统的总 wall time 由最慢子任务决定；当某个子任务反复失败时，multi agent 会保留可隔离性，但速度优势会被拖尾吃掉。

## 5. 对比结论

这次 ablation 支持以下判断：

| 维度     | 判断                                                                                         |
| -------- | -------------------------------------------------------------------------------------------- |
| 能力覆盖 | multi agent 可以正确识别图像类任务并启用 Multimodal 路径                                     |
| 任务拆解 | 子 Agent 能进一步把 6 张图片拆成独立并行任务，技术路径成立                                   |
| 质量     | multi agent 最终 review passed，输出可追溯性更好                                             |
| 鲁棒性   | 局部失败被限制在单个图片子任务内，父级不受污染，仍能综合交付                                 |
| 速度     | 由最慢子 agent 限制，显著小于所有任务总和；但单个子 agent 任务失败时外在表现可能慢于单 Agent |
| 成本     | token 成本明显更高                                                                           |

整体结论：multi agent 的价值主要体现在复杂任务的结构化拆解、证据隔离和审查闭环，以及即时性能提升。对于 6 张图片这种天然可并行任务，技术方向是正确的；但当前实现需要减少中间层、限制慢任务重试、复用工具结果，才能把并行优势真正转化为 wall time 和成本优势。

