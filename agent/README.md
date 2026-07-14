# Agent 模块

`loop.py` 实现 ReAct 主循环、参数校验、权限、错误恢复、上下文压缩和 Trace；`context.py` 管理长上下文；`memory.py` 提供跨会话记忆；`permissions.py` 实现权限模式。

## 本地架构移植

当前核心逻辑以本地版本为主体，并保留新版中已验证有效的科研回答质量检查和只读 observation 复用。微信工具及 PDF Skill 继续使用 dev 分支版本。

## 权限与运行模式

`permissions.py` 提供 `plan/default/accept-edits/auto-safe` 四种模式。网络读取授权可按精确工具与域名保存到当前任务或会话；微信真实发送、bash 与实验执行不会被 `auto-safe` 静默放行。

交互中可使用 `/mode`、`/permissions`、`/steps`、`/audit` 和 `/verbose on|off`。

## 跨会话长期记忆

长期记忆分为两层：`MEMORY.md` 是人可读的项目记忆，由 `remember` 工具追加条目；`memory.json` 是结构化键值记忆，由 `KVMemory` 维护覆盖、召回和遗忘语义。`/memory` 会直接读取磁盘上的 `MEMORY.md`，新进程启动或当前会话下一轮任务都会重新读取最新磁盘记忆并**按内容 hash 去重**将新内容注入上下文，这样多个agent进程同时工作时，每个 agent 仍能读到其他 agent 新写入的内容；`--ablation no-memory` 会关闭这一路径。

并发写入使用 `agent.memory` 内部的跨平台 lock-file：对同一个记忆文件会创建 `<filename>.lock`，通过原子创建锁文件串行化读写，并在锁陈旧（距离最后修改时间超过5min）后自动清理，避免崩溃进程永久占用。Markdown 记忆在锁内追加，KV 记忆在锁内重新读取、合并、写入唯一临时文件并用 `os.replace` 原子替换，避免两个会话各自持有旧快照时出现 lost update。所有写入前都会清理非法 surrogate，保证记忆文件可用 UTF-8 稳定读写。

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

近期文献任务最多使用 12 次搜索/抓取调用，达到预算后停止扩展并整理结果。最终报告区分“严格匹配论文”和“扩展相关工作”，逐篇给出摘要、解决问题、核心方法、贡献和来源。如果第一版仍是搜索流水账，会在禁用工具的情况下自动重写一次。连续工具失败或达到最大轮数时也沿用同一交付要求。

## 科研回答与 Reviewer

网页、项目、论文、GitHub 和方法调研任务会检查最终答复是否包含来源链接、方法说明和实际结论；状态汇报、空任务清单或过短回答会被要求重写。Reviewer 是独立模型审查阶段，不调用工具、不新增事实，并接收受限长度的工具证据；默认按需使用 `/audit`，单次命令可使用 `--audit`。

## Unicode 与 Trace 鲁棒性

模型响应、PDF/网页解析、终端输入和外部 API 错误体都可能带入 lone surrogate，例如 `\udce5`。这类字符不是合法 Unicode 标量，直接写入 UTF-8 trace、打印到终端，或随历史消息再次发给 DeepSeek 时会触发 `surrogates not allowed`。`sanitize.py` 提供统一清理函数，把这类字符替换为 `�`，并递归处理列表和字典。

当前清理边界包括：`cli.py` 的用户输入和最终输出，`loop.py` 的用户任务、模型返回和长 observation 归档，`backend/client.py` 发请求前的消息与工具 schema、收到的模型内容和错误体，以及 `eval/tracer.py` 写 JSONL 前的 payload。这样即便某一轮返回了非法 surrogate，也不会污染 `agent.messages`，后续 `/history`、Trace 写入和下一轮模型请求仍能继续运行。
