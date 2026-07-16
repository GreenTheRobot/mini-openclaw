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

