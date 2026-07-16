# Agent 模块

`loop.py` 实现 ReAct 主循环、参数校验、权限、错误恢复、上下文压缩和 Trace；`context.py` 管理长上下文；`memory.py` 提供跨会话记忆；`permissions.py` 实现权限模式。

## 本地架构移植

当前核心逻辑以本地版本为主体，并保留新版中已验证有效的科研回答质量检查和只读 observation 复用。微信工具及 PDF Skill 继续使用 dev 分支版本。

## 权限与运行模式

`permissions.py` 提供 `plan/default/accept-edits/auto-safe` 四种模式。网络读取授权可按精确工具与域名保存到当前任务或会话；微信真实发送、bash 与实验执行不会被 `auto-safe` 静默放行。

交互中可使用 `/mode`、`/permissions`、`/steps`、`/audit` 和 `/verbose on|off`。默认输出会显示主 Agent 调度、子 Agent 启停、综合、后台审核和可恢复失败等可观察进度；这不是模型内部推理逐字展示，而是让用户知道系统正在执行哪一段工作。需要看每次模型轮次和工具调用/结果时，使用 `/verbose on`。

## 跨会话长期记忆

长期记忆分为两层：`MEMORY.md` 是人可读的项目记忆，由 `remember` 工具追加条目；`memory.json` 是结构化键值记忆，由 `KVMemory` 维护覆盖、召回和遗忘语义。`/memory` 会直接读取磁盘上的 `MEMORY.md`，新进程启动或当前会话下一轮任务都会重新读取最新磁盘记忆并**按内容 hash 去重**将新内容注入上下文，这样多个agent进程同时工作时，每个 agent 仍能读到其他 agent 新写入的内容；`--ablation no-memory` 会关闭这一路径。

并发写入使用 `agent.memory` 内部的跨平台 lock-file：对同一个记忆文件会创建 `<filename>.lock`，通过原子创建锁文件串行化读写，并在锁陈旧（距离最后修改时间超过5min）后自动清理，避免崩溃进程永久占用。Markdown 记忆在锁内追加，KV 记忆在锁内重新读取、合并、写入唯一临时文件并用 `os.replace` 原子替换，避免两个会话各自持有旧快照时出现 lost update。所有写入前都会清理非法 surrogate，保证记忆文件可用 UTF-8 稳定读写。

## TODO 状态隔离

普通 CLI 会话启动时会为本次 run 分配 `.mini-openclaw/sessions/<run-id>/tasks.json`，并通过 `MINI_OPENCLAW_TODO_PATH` 传给 `task_list`、`todo_write`、`update_todo` 和上下文压缩快照读取逻辑；`/tasks` 默认显示当前会话的 TODO 文件。外部已经设置 `MINI_OPENCLAW_TODO_PATH` 时不会覆盖，因此调度器仍可使用 `.mini-openclaw/scheduler-runs/<run-id>.tasks.json` 作为每次定时任务的独立 TODO。旧的 `.mini-openclaw/tasks.json` 仅作为未设置环境变量时的兼容默认值。

## 轻量多 Agent

默认启用轻量多 Agent 编排；启动时可用 `--no-multi-agent` 禁用并回到单 Agent ReAct 主循环，`--multi-agent` 保留为显式开启/兼容开关。交互会话中可用 `/multi-agent on` 和 `/multi-agent off` 运行时切换，`/status` 会显示当前 `multi_agent` 状态。

当前编排顺序是主 Agent 先做全局调度：判断是否启用子 agent，并输出结构化分工方案。简单、单步、无需跨角色协作的任务会由主 Agent 直接执行，不启动子 agent；复杂任务、需要多源证据、代码实验、论文/图表联合分析或用户明确要求子 agent 时，主 Agent 才会分配具体工作给 Research、Engineering 和 Multimodal Agent。Research Agent 负责论文、网页、PDF 和图表证据，Engineering Agent 负责执行型、集成型和验证型工作并拥有完整工具集，附带图片时可增加 Multimodal Agent。Reviewer 基于**子 agent 工具调用记录及其输出**审查关键依据和风险。Reviewer 是质量护栏，不是最终答案的主题；论文类任务的最终正文仍以论文分析、方法理解和结论讨论为中心。

每个子 agent 复用现有 `AgentLoop`、权限层、工具注册表和 Trace，但会分配 `.mini-openclaw/subagents/<parent-run>/<role>/tasks.json` 作为独立 TODO，避免多角色互相覆盖状态。子 agent trace 写入主 trace 同目录下的 `subagents/`。

多 Agent 执行时会把主调度、子 Agent 开始/完成、综合、Reviewer 后台检查和必要修订作为实时事件打印出来；子 Agent 内部的工具调用也会接入同一个事件流，安静模式只显示关键失败，`/verbose on` 会显示完整工具调用进度。

后端选择采用文本主后端加视觉旁路：普通任务只初始化并使用 DeepSeek 文本后端；只有命令带 `--image` 时才额外初始化 Qwen 视觉后端。多 Agent 模式下主 Agent、Research、Engineering、Synthesis 和 Reviewer 继续使用文本后端，**只有带直接图像输入的 Multimodal Agent 接收图像并使用视觉后端**，避免昂贵视觉模型被非图像角色继承使用。单 Agent 且带图运行时才把该轮交给视觉后端。

### 子 Agent 上下文结构

每个子 agent 都是一个新的 `AgentLoop` 实例，不直接继承父 agent 的完整 `messages` 历史。它拥有独立的消息列表、`Tracer`、`PermissionManager`、TODO 文件、工具子集和 `max_turns=20` 的执行预算。子 agent 的 system prompt 由主 CLI 的 `system_prompt` 加对应角色提示组成，例如 Research Agent 会收到通用系统约束、运行时日期、权限规则和 Skills catalog，再追加 `RESEARCH_PROMPT`。

子 agent 的 user message 会包装原始任务和主 Agent 分配的具体工作，而不是复制父会话历史：

```text
原始用户任务：
<task>

主 Agent 分配给你的具体工作：
<assigned_task>

请只完成 <role> 职责范围内的工作，并把证据、产物路径、失败原因和未完成项写清楚。
```

工具上下文按角色设置：Research Agent 主要看到论文、网页、PDF、图表分析和记忆工具；Engineering Agent 使用完整工具注册表，适合真实执行、文件修改、实验、外部通知、调度和集成验证；Multimodal Agent 主要看到图片、PDF 和图表分析相关工具。每个子 agent 运行时会临时设置 `MINI_OPENCLAW_TODO_PATH=.mini-openclaw/subagents/<parent-run>/<role>/tasks.json`，因此角色之间的 TODO 状态互不覆盖。子 agent trace 写入 `traces/subagents/<parent-trace-stem>.<role>.jsonl`。带 `--image` 时，只有 Multimodal Agent 收到 image block 并使用视觉后端；Research、Engineering、Synthesis 和 Reviewer 继续使用文本后端。

主 Agent 的调度方案会写入主 trace 的 `orchestration` 事件。启用子 agent 时，所有子 agent 输出会被拼成 `## Main Agent`、`## Multimodal Agent`、`## Research Agent`、`## Engineering Agent` 等证据块，再交给 Synthesis 生成最终回答；不启用子 agent 时，主 Agent 使用完整工具集直接完成任务。Reviewer 审查时会优先收到从各子 agent trace 中抽取的工具调用记录（工具名、参数、成功状态和截断 observation），再收到子 agent 输出文本，用于核对关键结论是否有执行证据。如果 Reviewer 返回“需修订”，Synthesis 会带着初版答案、Reviewer 意见和原始证据自动重写一次，要求保留原有分析深度，只做必要修正；随后再做复审。Reviewer 的初审、复审、证据摘要和是否触发修订只写入 trace，不打印到用户最终答案中。

当前限制是：子 agent 主要继承基础 system prompt、原始任务、工作目录、工具注册表和磁盘状态；父 agent 临时注入到自身 `messages` 里的额外上下文不会自动复制到子 agent。

## 上下文压缩

压缩采用本地版结构：

```text
system = 原始系统提示，不重复追加摘要
user = 历史压缩备忘
recent = 最近的完整消息与 assistant/tool 原子组
```

压缩前后都会校验 OpenAI/DeepSeek 工具消息协议。默认估算预算为 20000 token，并设置至少 3 轮的压缩冷却；只有上下文超过两倍预算时才跳过冷却立即压缩。摘要如果没有实际缩短上下文就放弃本次压缩。`task_list` 不再作为压缩或最终回答的强制门槛。

## 文献搜索与工具恢复

成功的 `arxiv_search`、`web_fetch`、`web_search`、`read`、`grep`、`glob` observation 会在同一任务内按完整参数复用。`arxiv_search` 支持关键词、arXiv 分类和提交日期区间，直接返回标题、作者、日期、分类、摘要和来源链接。网页访问使用内置搜索工具，不通过 bash、curl、wget 或 requests 绕行。

近期文献任务最多使用 30 次搜索/抓取调用，达到预算后停止扩展并整理结果。普通 Agent 主循环默认最多 40 轮，主 Agent 直接执行分支也是 40 轮，子 Agent 为 20 轮；这些预算用于防止无限循环，但已放宽以支持长任务。最终报告区分“严格匹配论文”和“扩展相关工作”，逐篇给出摘要、解决问题、核心方法、贡献和来源。如果第一版仍是搜索流水账，会在禁用工具的情况下自动重写一次。连续工具失败或达到最大轮数时也沿用同一交付要求。

## 科研回答与 Reviewer

网页、项目、论文、GitHub 和方法调研任务会在 `AgentLoop` 的最终出口检查答复质量，而不只依赖系统提示。`_is_insufficient_research_answer()` 会识别调研类任务，如果最终答复没有 `http(s)`/`arXiv` 来源链接、缺少方法/来源等关键结构、过短，或只是汇报 `task_list`/搜索过程，就会拦截本次最终回答，向模型追加 `_research_answer_repair_prompt()` 要求基于已有 observation 重写。文献检索任务还会要求严格匹配论文卡片包含提交日期、摘要、解决问题、核心方法、主要贡献/结论和来源链接；因此“文献调研不贴链接”会被视为不合格最终答复并触发修复。

实验、训练和复现任务同样在 `AgentLoop` 最终出口做可复现性检查。`runs/` 只是实验元数据、日志和报告目录，默认不进入 Git，避免把日志和大文件污染仓库；真正的版本证据必须来自项目 Git。`_needs_experiment_tracking()` 命中后，最终答复前必须已有版本信息证据：优先来自 `experiment_prepare` 记录的 Git 状态，或通过 `bash` 成功执行 `git init`（如需要）、初始化 `.gitignore`、创建初始提交、`git status --short`、`git rev-parse HEAD`、`git branch --show-current` 等 Git 查询。`experiment_prepare` 如果检测到没有 Git 仓库或没有提交历史，会主动初始化 `.gitignore` 并创建 `chore: initialize experiment baseline` 基线提交。工作区 dirty 是允许的，但必须记录 `git_status`/`git_dirty` 并在最终答复中透明说明。若模型准备在没有任何 Git 状态证据的情况下结束，`AgentLoop` 会以 `missing_experiment_git_evidence` 拦截并要求先补 Git 记录。后续提交是可选动作，只有用户明确要求或确认时才执行。若冒烟测试、实验启动、状态检查或实验相关 bash 命令失败，而模型仍声称实验成功完成，最终答案会自动追加失败纠偏说明，明确失败工具、类别和原因。

Reviewer 是独立模型审查阶段，不调用工具、不新增事实，并接收受限长度的工具证据；默认按需使用 `/audit`，单次命令可使用 `--audit`。

## 实验 Git 基线

实验任务应优先使用 `experiment_prepare` 记录可复现信息。该工具会在目标项目没有 Git 仓库时执行 `git init`，初始化 `.gitignore`，并创建一次 `chore: initialize experiment baseline` 初始提交，用来保存实验开始前的原始状态；如果目录已有 Git 但没有任何提交，也会补齐 `.gitignore` 并创建这次 baseline commit。

后续实验修改不自动提交，是否提交由用户决定。最终实验回答需要透明说明基线提交、当前 Git 版本、工作区是否有未提交改动，以及实验产物路径；如果 Git 初始化或基线提交失败，必须明确告知失败原因，不能把无版本实验说成已可追踪。

## Unicode 与 Trace 鲁棒性

模型响应、PDF/网页解析、终端输入和外部 API 错误体都可能带入 lone surrogate，例如 `\udce5`。这类字符不是合法 Unicode 标量，直接写入 UTF-8 trace、打印到终端，或随历史消息再次发给 DeepSeek 时会触发 `surrogates not allowed`。`sanitize.py` 提供统一清理函数，把这类字符替换为 `�`，并递归处理列表和字典。

当前清理边界包括：`cli.py` 的用户输入和最终输出，`loop.py` 的用户任务、模型返回和长 observation 归档，`backend/client.py` 发请求前的消息与工具 schema、收到的模型内容和错误体，以及 `eval/tracer.py` 写 JSONL 前的 payload。这样即便某一轮返回了非法 surrogate，也不会污染 `agent.messages`，后续 `/history`、Trace 写入和下一轮模型请求仍能继续运行。
