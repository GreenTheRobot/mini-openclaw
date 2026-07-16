# 消融草稿（Day3 · 样本轨迹）
- 变量：system-prompt（有 / 无），其余（任务集、模型 deepseek-v4-flash）固定
- 结果：有=1.00 / 无=0.00；token：有更高（多了工具说明）
- 归因：无 system-prompt 时 agent 不知道 <tool_call> 约定 → 从不调工具 → 全失败
- 局限：样本量太小（各 2 条）、样本是构造的；后续各组均使用真实 trace，并以多次运行的均值描述结果

## A 组：任务规划（TODO）消融

- 假设：面对“阅读说明 → 定位代码 → 执行验证 → 写报告”的多阶段任务，TODO 规划不一定提高简单任务的完成率，但应提供可追踪的状态推进，并降低遗漏/恢复成本。
- 自变量：`none`（完整规划）与 `no-planning`（移除 `todo_write`、`update_todo` 与规划提示）。
- 固定条件：同一深度学习后端、同一 3 阶段本地 fixture、同一用户提示、每臂 2 次独立运行；均使用 `--auto-approve --no-mcp --no-multi-agent`，不访问网络。因此多 Agent、MCP、权限交互不构成混杂因素。
- 成功判定：`report.md` 同时包含入口 `app/entry.py`、核心模块 `lib/metrics.py`、结果 `accuracy=0.67`；并且 JSONL trace 中必须有成功工具调用输出 `accuracy=0.67`，以排除只写结论而未执行验证的假阳性。
- 主要指标：成功率、成功 TODO 调用数、工具调用数、总 Token、端到端耗时、trace 中的诊断错误与估算成本。

## B 组：跨会话记忆消融

本组关注一个新会话能否自然沿用项目约定。实验开始前，在独立工作区写入长期记忆：“用户姓名是空吧哇；偏好使用中文简洁汇报。”随后启动新的 CLI 会话，请 Agent 仅依据已注入的长期记忆回答姓名，不读取文件，也不调用工具。

完整系统与 `no-memory` 各进行 2 次独立运行。两边使用相同的工作区、记忆文件、用户问题、模型和 `--auto-approve --no-mcp --no-multi-agent` 运行参数；唯一差异是是否在会话开始时自动加载 `MEMORY.md`。以“回答为空吧哇且工具调用数为 0”作为正确回忆的判定标准，同时记录 Token、耗时、估算成本和 trace 诊断结果。

## C 组：System Prompt 消融

本组考察完整 System Prompt 对基础工程任务的影响。测试任务要求 Agent 读取 `config.json` 中的 `seed`，把 `hello.py` 的占位内容改为打印 `seed=123`，执行脚本验证，并在 `final.txt` 中留下结果。完整系统使用项目默认的 System Prompt；`minimal-prompt` 仅保留“你是一个命令行助手。完成用户任务。”这一简短指令。

两种条件保留完全相同的工具 schema、模型、fixture、用户任务和 `--auto-approve --no-mcp --no-multi-agent` 运行参数。因此，本组比较的是完整提示词提供的工作流、安全和工具使用指导，而不是“是否允许调用工具”。每个条件独立执行 2 次。只有 `final.txt` 同时包含 `seed=123` 与“验证通过”，并且 trace 中出现成功输出 `seed=123`，才计为完成。

本组只覆盖一个无对抗、无高风险的短工程任务，不能据此判断完整 System Prompt 中的安全约束或复杂任务指导是否必要。

## D 组：多 Agent 协作消融

本组只保留高信息密度的“研究—工程—审查”任务，用于检验多 Agent 是否能在保持交付质量的前提下降低长任务的上下文开销。固定 fixture 包含四份材料：方法与 35% FLOPs 声称、8-bit quantization 与 calibration drift 风险、OOD 泛化尚未验证的限制，以及一份要求跳过限制的未可信指令；同时包含 `ImageEncoder`、`quantize`、`evaluate` 三个代码模块和三个可运行脚本。

默认多 Agent 条件由 Research Agent 整理文档证据、Engineering Agent 检查代码并执行 `python scripts/evaluate.py`、`python scripts/benchmark.py`、`python scripts/regression.py`，随后由主 Agent 汇总并经 Reviewer 审查。对照条件使用 `--no-multi-agent`，由单 Agent 完成完全相同的用户任务。两边均使用同一模型、同一 fixture、同一用户提示、`--auto-approve --no-mcp` 参数，各独立运行 2 次；唯一自变量是是否启用多 Agent。

成功判定同时要求最终答复覆盖研究、代码、运行和安全限制事实，包括 `token pruning`、35% FLOPs、8-bit、`calibration drift`、`ImageEncoder`、`quantize`、`evaluate`、三个脚本输出与 OOD 证据缺口；合并 JSONL trace 还必须实际记录 `score=0.92`、`latency_ms=12.5`、`regression=pass` 三个成功输出。评分仅归一化 `ImageEncoder` / `image encoder`、以及“未验证” / “尚未验证”等等价表述；所有样本均保留，未重新挑选。

结果显示两个条件均为 2/2 完成。默认多 Agent 平均使用 154,142 Token，低于单 Agent 的 193,959，减少约 20.5%；但多 Agent 的平均工具调用更多（35.5 对 28.5）、平均耗时更长（91.92 秒对 75.66 秒），估算成本略高（0.005146 USD 对 0.004721 USD）。因此，本组结论限定为：**在该高信息密度、跨研究—工程—审查任务中，多 Agent 以相同的完成率和证据覆盖换取了更低的 Token 消耗，但并不更快或更便宜。**

## E 组：文献链接交付硬门槛消融

本组检验文献调研任务的最终交付是否只依赖模型自觉，还是需要在最终出口设置可验证的来源链接门槛。统一任务为：**“找最近一周多模态模型压缩的新论文，不用下载具体论文，给出结构化报告即可。”** 两个条件使用相同模型、相同运行日期、相同检索工具、相同工作目录和单 Agent CLI 参数；均只完成论文检索与信息整理，不下载或解析 PDF。唯一自变量是最终回答出口是否保留“缺少来源链接或必要结构时打回重写”的硬门槛。

关闭门槛后，Agent 在完成近期论文的内容概括后即可结束，最终报告没有可直接点击的论文链接。启用门槛后，若初稿遗漏链接，主循环会将已有检索结果和不合格答案一并交给模型重写；只有最终报告形成结构化论文卡片并保留来源 URL，才允许交付。

成功判定不以工具 observation 中出现 URL 为准，而是直接检查**最终回答**：每篇论文需具有题目、提交日期、研究内容概括、解决问题、核心方法、主要贡献/结论与可点击的 `http(s)` 来源链接。Trace 还需保留“初稿被拦截—补齐链接后交付”的质量控制证据。

## F 组：多模态多 Agent 消融

本组以 `demo_project/attention_paper_parsed/images` 中 6 张论文图片的内容理解为任务，比较默认多 Agent 路径与 `--no-multi-agent` 单 Agent 路径。多 Agent 先由主 Agent 调度 Multimodal Agent，再按图片递归拆分为 6 个独立子任务，并由 Synthesis 与 Reviewer 汇总、审查；单 Agent 则在同一 ReAct 链中连续完成读图、补证据和报告生成。两边使用相同图片集与用户任务，评估最终交付、LLM/工具调用、总 Token、端到端耗时以及 trace 中的局部失败恢复。

两种条件都完成了最终交付：多 Agent 的 review 为 `passed`，单 Agent 也在补充证据后成功。多 Agent 的优势不体现在本轮性能，而在于每张图都有独立子 trace；即使 `marker-004` 的分析多次失败并最终为 `partial`，其余 5 张图仍能独立完成，父级仍可汇总并通过审查。相比之下，单 Agent 的失败与补救均累积在同一主循环上下文中。

代价同样显著：多 Agent 顶层耗时约 739 秒、总 Token 约 961,688，高于单 Agent 的约 441 秒和 215,420 Token。主要原因是主 Agent 到 Multimodal Agent 再到 6 个图片子任务的两级拆分，以及 `marker-004` 的长时间重试拖尾。因此，本组仅证明当前多 Agent 具备并行拆解、证据隔离和局部故障容忍能力；要获得速度和成本优势，还需要减少中间编排层、限制慢任务重试并复用已有工具结果。
