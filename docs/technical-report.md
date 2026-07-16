# mini-OpenClaw 技术报告：实现机制、运行边界与验证证据

## 1. 报告范围与实现基线

mini-OpenClaw 是一个面向科研任务的命令行 Agent。它不是把聊天接口简单包装成 CLI，而是把模型决策、工具执行、权限控制、状态持久化、并行子 Agent、结果审查和运行追踪组合成一条可验证的执行链。

本文以当前工作区的真实代码为准，而不是只复述 README 或设计设想。核对时的仓库状态为：

- 分支：`verified_version`；
- HEAD：`1a6b16f`；
- 默认内置工具：23 个；
- Skills：9 个；
- 测试文件：25 个，源码中定义 165 个 `test_*` 函数，参数化后本次实际收集并执行 169 个测试用例；
- 当前工作区还包含尚未提交的并行多 Agent、线程级 TODO 隔离等改动，因此本文描述的是“HEAD + 当前工作区”的实际行为。

项目的核心设计原则可以概括为三点：

1. **模型提出动作，宿主程序裁决动作。** 模型可以生成工具名和参数，但 schema 校验、权限判定、路径边界、实际执行和失败分类都由 Python 代码完成。
2. **完成声明必须服从外部状态。** TODO 文件、工具返回值、Git 信息、进程 return code 和 Trace 比模型自然语言更权威。
3. **失败是结构化 observation，而不是异常终止的唯一形式。** 大部分可恢复错误会转换成带类别的 `[TOOL_ERROR]`，回填给模型继续修正；达到预算后再降级为部分交付。

## 2. 系统边界与分层架构

项目按职责分成六层：

| 层           | 主要目录                                                          | 真实职责                                                          |
| ------------ | ----------------------------------------------------------------- | ----------------------------------------------------------------- |
| 交互与编排层 | `agent/cli.py`、`agent/subagents.py`                          | 参数解析、会话命令、后端选择、上下文装配、多 Agent 调度与最终合成 |
| Agent 控制层 | `agent/loop.py`、`agent/context.py`、`agent/permissions.py` | ReAct 循环、消息协议、权限、失败预算、压缩和最终答案门禁          |
| 模型适配层   | `backend/`                                                      | OpenAI-compatible 请求、视觉消息编码、响应归一化和离线占位后端    |
| 执行能力层   | `tools/`、`mcp/`、`skills/`                                 | 内置工具、外部 MCP 工具和按任务召回的领域流程                     |
| 持久状态层   | `MEMORY.md`、`.mini-openclaw/`、`runs/`、`traces/`        | 记忆、TODO、调度、实验元数据、完整 observation 与 Trace           |
| 验证与安全层 | `tests/`、`eval/`、`security/`                              | 单元/集成测试、Trace 报告、真实任务消融和红队用例                 |

一次单 Agent 任务的控制流如下：

```text
用户任务 / --image
        |
        v
CLI：加载系统提示、日期、权限模式、Skill catalog、Memory、Planner guidance
        |
        v
AgentLoop：校验历史协议 -> 调用 Backend
        |
        +-- assistant 无 tool_calls
        |       |
        |       +-- TODO / 调研质量 / 实验 Git 证据门禁
        |       +-- 对失败写入或失败实验做确定性纠偏
        |       `-- 输出 success / partial / failed
        |
        `-- assistant 有 tool_calls
                |
                +-- 补齐 tool_call id
                +-- 研究工具确定性路由
                +-- JSON Schema 子集校验
                +-- PermissionManager：allow / confirm / deny
                +-- Tool.run -> ToolResult
                +-- observation 归档、回填与 Trace
                +-- 错误/搜索/重复调用预算
                `-- 必要时协议安全压缩，进入下一轮
```

多 Agent 模式不是另一套执行引擎。每个角色仍创建独立 `AgentLoop`，复用同一套工具、权限和 Trace 抽象；编排层只负责决定任务图、并发执行、证据汇总和 Reviewer 闭环。

## 3. 启动、配置与后端选择

### 3.1 安装与入口

项目要求 Python 3.10 以上，`pyproject.toml` 同时注册两个等价一键启动入口：

```toml
[project.scripts]
claw = "agent.cli:main"
openclaw = "agent.cli:main"
```

推荐在独立环境安装：

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

CLI 本身没有调用 `python-dotenv`，因此普通交互运行不会自动解析 `.env`。需要在父进程中导出变量；只有 scheduler 生成的 POSIX cron 命令会主动 source 项目 `.env`。

PowerShell 示例：

```powershell
$env:DEEPSEEK_API_KEY = "<key>"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_MODEL = "<OpenAI-compatible model name>"

$env:QWEN_API_KEY = "<key>"
$env:QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:QWEN_VISION_MODEL = "<vision model name>"
```

可选配置还包括：

- `QWEN_CHAT_URL`：覆盖视觉后端的完整请求 URL；
- `OPENCLAW_MCP_COMMAND`：覆盖 MCP server 启动命令；
- `MINI_OPENCLAW_PYTHON`：为 scheduler 固定 Python 解释器；
- `OPENCLAW_INPUT_USD_PER_MILLION`、`OPENCLAW_OUTPUT_USD_PER_MILLION`：覆盖 Trace 成本估算；
- `WECHAT_DRY_RUN`、`WX_ALLOWED_TARGETS`、`WX_TRUSTED_TARGETS`：控制微信预览、允许目标和免确认目标；
- `MINIOPENCLAW_MARKER_MIN_FREE_VRAM_MB`：Marker 路由所需的最小空闲显存，默认 6144 MiB。

### 3.2 文本、视觉与离线后端

`DeepSeekBackend` 使用 OpenAI-compatible `/v1/chat/completions` 协议。它在发请求前递归清理非法 Unicode surrogate，并再次校验 assistant/tool 消息协议；请求只在有工具 schema 时携带 `tools` 和 `tool_choice="auto"`。响应中的 JSON 字符串参数会被解析成内部字典，最终统一为：

```python
{
    "role": "assistant",
    "content": "...",
    "tool_calls": [
        {"id": "...", "name": "read", "arguments": {"path": "README.md"}}
    ],
    "usage": {...},
    "model": "..."
}
```

`QwenVisionBackend` 复用同一 HTTP 和消息转换逻辑，但声明 `supports_tools = False`。因此直接图像任务只把文本和 base64 图片交给视觉模型，不发送工具 schema。图片进入请求前会由 Pillow 解码，最长边超过 1568 像素时缩放，并按 PNG/JPEG/WEBP 重新编码。

后端选择是惰性的：

- 普通文本任务先尝试 `DeepSeekBackend`；缺少 Key 或初始化失败时回退 `FakeBackend`；
- 只有命令行带 `--image` 时才初始化 `QwenVisionBackend`；视觉初始化失败则该分支回退 `FakeBackend`；
- `FakeBackend` 只证明 CLI 和消息链路可启动，不具备自然语言规划或真实工具选择能力，不能作为功能评测后端。

### 3.3 常用命令

```bash
python -m agent.cli --selfcheck
python -m agent.cli "分析这个仓库的执行链" --trace traces/analysis.jsonl
python -m agent.cli "解释这张论文图" --image path/to/figure.png
python -m agent.cli                         # 进入交互会话
```

默认开启多 Agent；`--no-multi-agent` 可回到单 Agent。交互模式提供 `/mode`、`/permissions`、`/steps`、`/tasks`、`/memory`、`/multi-agent`、`/trace`、`/status`、`/history`、`/clear` 和 `/audit` 等命令。

## 4. 核心运行时契约

### 4.1 Tool 与 ToolResult

`tools/base.py` 定义所有工具共享的最小协议：

```python
Tool(name, description, parameters, run)
ToolRegistry
ToolResult(content, success=True, category="ok")
```

`Tool.schema()` 把内部定义转换为 OpenAI function schema。`validate_arguments()` 实现了一个刻意受限的 JSON Schema 子集：

- `type`；
- `required`；
- `enum`；
- `properties` 与递归对象校验；
- `items` 与递归数组校验；
- `additionalProperties: false`。

它不实现完整 JSON Schema，例如数值范围、字符串 pattern、`oneOf` 等。因此安全关键约束不能只写在 schema 描述里，必须在权限层或工具函数内部再次验证。

`ToolResult` 解决了“错误字符串被误当成功”的问题。新工具应显式返回 `success` 和 `category`；旧工具的纯字符串结果会由 `normalize_tool_result()` 按已知错误前缀做兼容判断。错误类别随后用于模型恢复提示、Trace 诊断和最终成功声明纠偏。

默认注册表的 23 个工具为：

| 类别       | 工具                                                                                                                  |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| 文件与代码 | `read`、`write`、`edit`、`grep`、`glob`                                                                     |
| 本地执行   | `bash`                                                                                                              |
| 网络与论文 | `arxiv_search`、`web_search`、`web_fetch`、`download_file`                                                    |
| PDF 与图像 | `pdf_extract_text`、`pdf_metadata`、`paper_figure_analyze`                                                      |
| 任务与记忆 | `todo_write`、`update_todo`、`remember`                                                                         |
| 实验       | `experiment_prepare`、`experiment_smoke_test`、`experiment_start`、`experiment_status`、`experiment_report` |
| 调度与通知 | `schedule_task`、`wechat_file_transfer`                                                                           |

`tools/task.py` 还定义了兼容型 `task_list`，但 `build_default_registry()` 没有注册它；默认模型只能看到 `todo_write` 和 `update_todo`。启用默认 MCP 后通常再增加 2 个计算工具；进入多 Agent 模式时运行时注册表还会临时增加 `subagent_dispatch`。因此自检的“23”与交互界面可能显示的“25/26”并不矛盾，后者取决于扩展是否成功接入。

### 4.2 消息协议

内部消息遵循 OpenAI/DeepSeek 的原子关系：一个带 `tool_calls` 的 assistant 消息后，必须紧跟与全部 call id 一一对应的 tool 消息，之后才能出现下一条 user/assistant 消息。

`validate_tool_protocol()` 检查：

- system 只能位于索引 0；
- 第一条非 system 消息必须是 user；
- tool call id 非空且同批不重复；
- 下一轮开始前所有 call 都已有结果；
- 不存在孤立 tool 消息或错误 `tool_call_id`。

旧 Trace 或被中断的会话可能留下不完整调用组。`repair_tool_protocol()` 只保留完整的 assistant/tool 原子组，删除孤立或缺结果的工具消息，把中途 system 转成 user 备忘，并在必要时补一条恢复提示。修复后仍不合法则主循环拒绝继续发送，避免把损坏历史交给模型 API。

### 4.3 工作目录语义

`AgentLoop` 把 `workdir` 解析为绝对路径。每次工具执行前暂时 `chdir(workdir)`，执行后恢复原 cwd，因此大多数工具可以使用相对路径。路径型动作还要经过 `PermissionManager` 和工具内部校验。

这里有一个重要实现边界：`os.chdir()` 是进程级状态，不是线程级状态。当前并行子 Agent 通常共享同一个 workdir，所以实际冲突较小，但若未来允许不同 workdir 并行执行，需要改成显式向工具传根目录或使用子进程，不能继续依赖全局 cwd。

## 5. AgentLoop：从模型决策到可信交付

### 5.1 上下文装配与稳定前缀

CLI 首先构造系统提示：基础科研规则、few-shot demonstrations、安全下载规则、运行时日期、当前权限模式和 Skill catalog。每轮开始前再按需加载：

- `MEMORY.md` 的最新磁盘内容；
- Planner 对复杂任务生成的 TODO 指导；
- 最多 3 个关键词匹配得分达标的 Skill 正文。

`AgentLoop.add_context()` 在第一次模型调用前把这些内容合并进 system；会话已经开始后则追加为带“不改变系统约束”说明的 user 上下文，不再重写 system。Trace 会记录 system 前缀的 SHA-256 截断摘要和长度，用来检查相邻请求的稳定前缀是否保持一致，以及提示缓存是否可能复用。

Memory 用内容 hash 作为上下文 key。磁盘记忆发生变化后会产生新 hash，因此长交互会话可以在下一轮看到其他进程刚写入的新内容；相同内容不会重复注入。

### 5.2 每轮决策流程

`AgentLoop` 默认最多 40 轮，子 Agent 最多 20 轮。每轮按以下顺序运行：

1. 校验并必要时修复历史工具协议；
2. 根据 `backend.supports_tools` 决定是否发送注册表 schema；
3. 启动 LLM span，记录消息数、估算上下文 token、工具 schema 数和稳定前缀；
4. 调用后端并清理响应；
5. 为缺失 call id 的调用补 `call_<turn>_<index>`；
6. 先把完整 assistant 消息写入历史；
7. 逐个执行同一 assistant 声明的所有工具调用，并为每个 call 回填 tool 消息；
8. 本批全部结果写完后才计算错误预算和压缩，保证跨轮历史始终原子完整。

同一个 assistant 消息可以请求多个工具。连续错误预算按“整轮”而不是按单个 call 计数：只要该轮至少一个工具成功，连续错误计数就清零。这避免一批参数错误同时耗尽恢复预算，但也意味着一批中“一个成功、多个失败”不会推进连续错误计数。

### 5.3 确定性工具路由

对科研发现任务，代码会在权限判定前修正部分模型选择：

- 用户任务包含明确 URL，而模型请求 `web_search` 时，改为 `web_fetch`；
- 文献检索任务请求 `web_search` 时，若有 `arxiv_search`，改为结构化 arXiv 检索；
- “最近/近一周”论文任务会根据系统提示中的运行时日期，把 arXiv `start_date` 和 `end_date` 强制改成最近七天。

路由后的 observation 会同时记录原始调用、实际调用和路由原因。因此 Trace 仍能解释“模型请求了什么”与“宿主实际执行了什么”的差异。

### 5.4 重复调用与 observation 复用

工具签名由 `[tool_name, arguments]` 的排序 JSON 生成。完全相同调用默认最多出现 3 次，第 4 次返回 `repeated_call`。

对于 `arxiv_search`、`web_fetch`、`web_search`、`read`、`grep`、`glob`，第一次成功 observation 会放入当前 run 的内存缓存；相同调用再次出现时，不重新访问磁盘或网络，而是回填“已复用”说明和原结果。这个缓存不跨进程、不持久化，也不会复用失败结果。

文献检索另有 `max_research_calls=30`。达到上限后系统禁止继续调用工具，要求模型基于现有 observation 生成结构化报告。

### 5.5 错误恢复与降级交付

未知工具、非对象参数、schema 错误、权限拒绝、未获确认、工具语义失败和 Python 异常都会转换为：

```text
[TOOL_ERROR]
tool: <name>
category: <category>
message: <detail>
recoverable: true|false
suggestion: <next action>
```

网络 404、超时、权限拒绝、确认缺失等类别有专门的恢复建议。若 `bash` 因 `curl`、`wget`、`requests` 等网络探测被沙箱拒绝，提示会明确要求改用 `web_search` / `web_fetch`。

默认连续 4 个“整轮全失败”后停止工具探索。此时系统不是直接报错退出，而是额外调用最多两次不带工具的模型：第一次根据已有证据生成部分报告；若调研交付仍不合格，第二次只允许重写。最终状态为 `partial`，后端总结本身失败才为 `failed`。

达到最大轮数也走相同的无工具总结路径，而不是只返回“已达到轮数上限”。

### 5.6 最终答案的程序化门禁

当模型返回无工具调用的 assistant 消息时，不一定立即结束。代码按顺序检查：

1. **TODO 门禁。** 只有本次历史确实使用过 `task_list`、`todo_write` 或 `update_todo` 时才启用；当前 TODO 文件仍有 `pending` / `in_progress` 则要求继续执行。TODO 不是所有任务的无条件门禁。
2. **调研质量门禁。** 论文、网页、项目和 GitHub 调研答案过短、只有状态汇报、缺来源链接或缺关键结构字段时，最多触发两次重写。
3. **实验 Git 门禁。** 训练、实验、复现类任务若本轮没有成功 `experiment_prepare` 或 Git 查询命令证据，最多阻塞一次并要求补充版本信息。
4. **确定性声明纠偏。** 如果 `write` / `edit` 失败但答案声称已保存，程序追加明确纠正；如果冒烟测试、实验启动或状态检查失败但答案声称实验成功，也会追加失败纠正。

前两类门禁依赖关键词启发式，不是语义证明器；最后一类是对已记录失败与成功措辞的确定性对照。它们共同降低“模型把计划写成完成”的风险。

## 6. 上下文预算与长 observation 管理

### 6.1 Token 粗估

`estimate_tokens()` 不调用模型 tokenizer，而是把消息 content 和 tool_calls JSON 的字符数相加后除以 2。这是偏保守、低成本的调度估计，适合决定何时压缩，但不能替代供应商 usage。

默认预算为 20,000 估算 token。若 system 本身很长，有效预算会提升到 `system_estimate + 4000`，保证至少给用户/工具历史预留约 4,000 估算 token，避免 system 一开始就触发无意义压缩。

### 6.2 协议安全压缩

压缩先把消息切成“原子单元”：带 tool calls 的 assistant 及其随后全部 tool results 是一个不可拆单元，普通 user/assistant 各自是一个单元。系统保留：

- 原始 system；
- 对较早完整单元生成的不超过 1200 字结构化备忘；
- 最近两个 user turn 对应的完整单元，或单个长任务下最后三个完整交互单元；
- 当前 TODO 文件生成的权威快照。

压缩前后都校验工具协议。如果总结失败、结果为空、压缩后不合法，或估算长度没有真正下降，本次压缩会被放弃。主循环还有 3 轮冷却；只有上下文超过预算两倍时才跳过冷却立即再次尝试。

压缩调用本身会消耗一次模型请求，但目前不单独创建 LLM span，只通过主循环的 `compaction` 事件记录压缩前后估算值。这是当前可观测性上的一个缺口。

### 6.3 长 observation 归档

单个工具输出超过 4000 字符时，完整文本写入：

```text
.mini-openclaw/observations/step-<turn>-<tool>.txt
```

上下文只保留头部、尾部、原始长度和相对归档路径。这样既保留错误尾部和结果开头，也避免长网页/PDF/日志持续占用上下文。归档文件名只含 turn 和 tool；同一轮重复调用同名工具时会覆盖前一次归档，这是需要改进的边界。

## 7. 权限模型与安全边界

### 7.1 判定与授权范围

权限层返回 `allow`、`confirm` 或 `deny`。网络读取的确认可以授予 `once`、`task` 或 `session` 范围；授权 key 精确到“工具 + 主机名”，例如 `network-read:web_fetch:arxiv.org`，不会把一个域名的授权扩散到另一个域名或另一类工具。

任务开始时清空 task grant；会话 reset 或切换权限模式时同时清空 task/session grant。非交互终端遇到 confirm 默认拒绝。

### 7.2 五种权限模式

| 能力                                      | `plan`          | `default`                  | `accept-edits` | `auto-safe` | `auto-local` |
| ----------------------------------------- | ----------------- | ---------------------------- | ---------------- | ------------- | -------------- |
| 工作区读、PDF/图片读、TODO、子 Agent 派发 | allow             | allow                        | allow            | allow         | allow          |
| 工作区`write` / `edit`                | deny              | confirm                      | allow            | allow         | allow          |
| `bash`                                  | deny              | confirm                      | confirm          | confirm       | allow          |
| 白名单网络读                              | confirm           | confirm                      | confirm          | allow         | allow          |
| 白名单 PDF 下载                           | deny              | confirm                      | confirm          | allow         | allow          |
| 实验状态读取                              | deny              | allow                        | allow            | allow         | allow          |
| 实验元数据/报告写入                       | deny              | confirm                      | confirm          | allow         | confirm        |
| 冒烟测试/实验启动                         | deny              | confirm                      | confirm          | confirm       | confirm        |
| 长期记忆写入                              | deny              | confirm                      | confirm          | confirm       | confirm        |
| 调度配置变更                              | 独立规则：confirm | confirm                      | confirm          | confirm       | confirm        |
| 微信发送                                  | deny              | 受信目标 allow，否则 confirm | 同左             | 同左          | 同左           |
| 未知/MCP 工具                             | deny              | confirm                      | confirm          | confirm       | confirm        |

`plan` 的含义是禁止副作用，不是完全离线：白名单网络读取仍可在确认后发生。`auto-local` 也没有自动放行实验启动和外部发送。

### 7.3 路径和 Shell 边界

`read`、`write`、`edit`、`grep`、PDF 和图片工具先把路径 resolve，再要求其位于 workdir 内。`glob` 更严格地拒绝绝对路径和任意 `..`。`edit` 只有 `old` 在文件中恰好出现一次才修改，零次或多次都返回失败。

`bash` 使用字符串黑名单和跨平台危险删除检测，拦截 `curl`、`wget`、`mkfs`、`dd if=`、fork bomb、大范围 `rm -rf`、Windows 系统盘递归删除等。如果检测到 `bwrap`，执行命令时只读绑定根文件系统、可写绑定当前目录并 `--unshare-net`；否则退回本机 `bash -c`，Windows 再退回 `shell=True`。

因此必须准确理解边界：

- `bwrap` 存在时提供较强的网络隔离和有限写入边界；
- 没有 `bwrap` 时主要依赖权限确认和危险命令黑名单，不是完整沙箱；
- 尤其 `auto-local` 会自动允许未命中黑名单的本地 shell，代码没有证明命令只能访问项目目录，应该只在受控任务和可信仓库中使用。

### 7.4 网络、下载和提示注入

`web_fetch` 只允许预置学术、文档、代码托管等域名及其子域名；禁止 URL 用户名/密码；禁用环境代理；手工跟随最多 6 次重定向。重定向若改变主机名，会返回 `redirect_requires_confirmation`，要求模型对新 URL 单独发起调用，使权限层重新确认域名。

`download_file` 的限制更强：

- 只接受 HTTPS、443 端口和 PDF 域名白名单；
- DNS 结果必须是全局公网地址，阻止解析到回环、私网或链路本地地址；
- 跨域重定向需要重新授权；
- 校验 Content-Type 和最大体积；
- 先写 `.part` 临时文件，完成后原子替换，并返回 SHA-256。

网页、项目内 HTML 和 PDF 都被包裹成“不可信数据”，明确告诉模型其中内容不能成为新指令。该措施降低提示注入风险，但不是形式化隔离：最终仍依赖系统提示优先级、工具权限和模型遵循能力。

### 7.5 Unicode 与 Trace 脱敏

外部 API、PDF、终端和模型响应可能含 lone surrogate，直接 UTF-8 编码会触发 `surrogates not allowed`。`clean_text()` 将其替换为 `U+FFFD`，`sanitize_for_json()` 递归处理列表和字典。清理覆盖 CLI 输入输出、模型请求/响应、observation、记忆和 Trace。

Trace 写入前还会：

- 按敏感键名隐藏 `api_key`、`authorization`、`token`、`password`、`secret`；
- 替换 Bearer token 和 `sk-*` 形式密钥；
- 把绝对 workdir 前缀改成 `.`；
- 只保存输入输出预览，而不是把所有原文复制进 span。

脱敏是模式匹配，不保证识别任意自定义密钥格式，因此任务文本中仍不应粘贴真实秘密。

## 8. 多 Agent 编排与并发实现

### 8.1 编排计划不是固定角色流水线

默认 CLI 先用文本后端做一次无工具 orchestration。首选输出是扁平子任务列表：

```json
{
  "use_subagents": true,
  "reason": "任务可并行拆分",
  "main_task": "",
  "subagents": [
    {"id": "paper-context", "role": "research", "task": "核对论文上下文与来源"},
    {"id": "figure-1", "role": "multimodal", "task": "只分析第一张图"},
    {"id": "tests", "role": "engineering", "task": "运行聚焦测试并报告结果"}
  ]
}
```

`role` 只是提示词和后端选择标签，不是任务容器，也不是硬安全边界。多个同角色任务会扇出为 `Research Agent 1/2` 等独立实例。旧版 `assignments` 格式仍兼容字符串或字符串数组。

若 orchestration 不是合法 JSON，代码用本地关键词规则回退：根据研究、工程、图像和显式“多 Agent/分工”标记决定任务。若没有任何有效子任务，则强制改为主 Agent 直接执行。

### 8.2 子 Agent 的上下文和能力

每个子 Agent 都是新的 `AgentLoop`，不复制父 Agent 的完整 `messages`。它收到：

- 基础 system prompt、运行时日期、权限模式、Skill catalog，以及 CLI 已加载进有效 system 的 Memory/Skill 上下文；
- 角色提示词；
- 原始用户任务；
- 主 Agent 分配的具体任务；
- 显式工作目录说明。

Research、Engineering、Multimodal 当前都接收同一个完整运行时 `ToolRegistry`。角色专门化主要来自提示词，而不是代码级工具裁剪；因此“Research 只能读、Engineering 才能改”并不成立，真正边界仍由统一权限层决定。

直接 `--image` 输入只发送给 Multimodal 子 Agent 并使用视觉后端，其他角色继续使用文本后端。若没有直接图片块，仅有本地图片路径，角色仍保持 Multimodal，但通常通过文本后端调用 `paper_figure_analyze` 间接使用 Qwen。

### 8.3 并发、隔离和同步

同一波子任务通过 `ThreadPoolExecutor(max_workers=len(jobs))` 并行执行，结果用原任务顺序重新排列，而不是按完成顺序合成。每个实例拥有：

- 独立 `AgentLoop.messages`；
- 独立 `PermissionManager`；
- 独立 TODO：`.mini-openclaw/subagents/<parent-run>/<id>/tasks.json`；
- 独立 Trace：`traces/subagents/<parent-trace>.<id>.jsonl`；
- 默认 20 轮预算。

TODO 路径不再通过并发修改全局环境变量实现，而是由 `contextvars.ContextVar` 保存线程/上下文局部值；环境变量 `MINI_OPENCLAW_TODO_PATH` 只作为兼容回退。并行任务不会再互相覆盖 TODO。

终端事件回调和权限确认回调分别加锁。前者避免多线程输出交错破坏 UI，后者保证两个子 Agent 同时请求高风险操作时，确认提示串行出现。工具执行本身没有全局写锁；多个子 Agent 若修改同一文件，仍可能发生业务级竞态，编排任务必须主动划分互斥写入范围。

### 8.4 动态 `subagent_dispatch`

多 Agent 模式会复制注册表并临时注入 `subagent_dispatch`。主 Agent 或子 Agent可以在执行中再次派发一波扁平任务，等待全部结果后把结构化 JSON observation 交回当前 Agent。这支持“先检查目录，再按实际发现的 6 张图继续扇出”的动态工作流。

动态派发的硬限制为：

- 每次最多 8 个子任务；
- 最大嵌套深度 3；
- 空任务、非法 schema 和超限分别返回明确错误类别；
- 嵌套 ID 带父级前缀，降低 Trace/TODO 路径冲突。

深度由 thread-local 上下文维护。初始 orchestration 返回的顶层列表目前没有同样的 8 项代码上限，只有动态工具调用受限；这是需要统一的资源治理边界。

### 8.5 合成与 Reviewer 闭环

有子 Agent 结果时，编排层把输出拼成 `## Main Agent`、`## Research Agent` 等 evidence block，调用不带工具的 synthesis 模型。对于调研任务，如果 evidence 中有来源，而合成答案丢失链接或结构不足，最多重写两次。

之后 Reviewer 接收两类有限证据：

1. 从子 Trace 抽取的工具名、参数、成功状态和截断 observation；
2. 子 Agent 的正文输出。

Reviewer 不调用工具、不新增事实，只检查关键结论、数字、实验成功声明、完成状态和来源缺失。若要求修订，系统进行一次“最小必要修改”，再复审；复审仍不通过时，会额外生成一版面向用户的最终交付，避免把“审查仍未通过”当作用户答案主体。

多 Agent 顶层的 orchestration、dispatch、review 和 repair 以手工 JSONL 事件写入父 Trace；各实际 `AgentLoop` 的 LLM/tool span 位于子 Trace。查看完整运行必须使用 `include_children=True`，CLI 的 `/trace` 和 `/steps` 已默认包含相关子 Trace。

## 9. 持久状态：TODO、记忆、调度与实验

### 9.1 TODO 状态隔离

CLI 启动后为当前 run 分配：

```text
.mini-openclaw/sessions/<run-id>/tasks.json
```

`todo_write` 负责建立列表，`update_todo` 更新状态；允许状态包括 `pending`、`in_progress`、`completed`、`failed`、`blocked`。旧 `.mini-openclaw/tasks.json` 只是没有会话路径时的兼容默认值。

普通会话通过环境变量保存当前 session 路径；并行子 Agent通过 `ContextVar` 覆盖；scheduler 为每个 scheduled run 注入独立环境路径。`agent/context.py`、`tools/task.py` 和 `/tasks` 都应读取同一权威文件。

### 9.2 长期记忆

`Memory` 把人可读条目追加到 `MEMORY.md`；`KVMemory` 把结构化键值写入 JSON。二者都使用通过 `O_CREAT | O_EXCL` 原子创建的 `.lock` 文件实现跨进程互斥：

- 等待默认最多 10 秒；
- 锁超过 300 秒视为陈旧，可清理；
- Markdown 在锁内追加；
- KV 在锁内重新读取、合并、写临时文件并 `os.replace`，避免 lost update。

`remember` 工具还会拒绝空内容，并由权限层逐次确认。系统提示明确禁止记忆密钥、隐私和未经验证的猜测。

### 9.3 定时任务

`schedule_task` 支持 `once`、`interval`、`cron`，配置写入 `.mini-openclaw/schedules.json`。任务只接受项目内 workdir，自动任务权限模式仅允许 `plan`、`auto-safe` 或 `auto-local`。

POSIX 平台可安装项目专属的用户 crontab block，每分钟执行：

```text
python -m agent.scheduler run-due
```

block 用项目绝对路径 hash 标记，只更新本项目段落。scheduler 有 `.mini-openclaw/scheduler.lock` 防止重叠唤醒；每次运行创建独立 stdout、stderr、Trace 和 TODO，并把 schedule/run id 放入 Trace metadata。

scheduled run 实际调用 `agent.cli`，固定 `--no-mcp`，继承默认多 Agent，并从任务记录、`MINI_OPENCLAW_PYTHON`、虚拟环境或当前解释器中选择 Python。当前完成判定以 CLI return code 为准；TODO 是进度证据，但源码注释明确说明“没有 TODO 的确定性脚本任务也可完成”，这与系统提示中“每次调度都必须通过 TODO”并非完全一致，应以 scheduler 代码为实际语义。

Windows 没有用户级 crontab 时，`wakeup_status` 会报告不可用；当前代码没有实现 Windows Task Scheduler 或 systemd timer 后端。

### 9.4 可复现实验

实验工具链包括：

```text
experiment_smoke_test
        -> experiment_prepare
        -> experiment_start
        -> experiment_status
        -> experiment_report
```

`experiment_prepare` 不只是记录状态。获得权限并执行后，它会：

1. 若当前目录没有 `.git`，执行 `git init`；
2. 若仓库没有任何 commit，补充默认 `.gitignore`，设置缺失的本地 Git identity，执行 `git add .` 并创建 `chore: initialize experiment baseline`；
3. 记录 commit、branch、`git status --short`、dirty 状态、命令、配置、seed、Python、平台和路径；
4. 写入 `runs/<run-id>/metadata.json`。

因此该工具具有明显副作用，权限层在 `default` 下会确认；它不是纯读取工具。已有 Git 历史时不会自动提交后续改动，只记录当前状态。

建基线前必须特别检查敏感文件和大型数据：工具自动补齐的默认 ignore 列表包含 `runs/`、`traces/`、`.mini-openclaw/`、虚拟环境和日志，但不包含 `.env`、数据集或任意项目私有文件。对于新仓库，直接 `git add .` 可能把这些内容纳入初始提交。本仓库自己的 `.gitignore` 已显式忽略 `.env`，但这不是工具对任意新项目的保证。

`experiment_smoke_test` 同步执行命令并返回 return code 与 stdout/stderr 尾部。`experiment_start` 在后台启动包装脚本，把 stdout/stderr 写入 `train.log` / `error.log`，并把 return code 写入独立文件。`experiment_status` 综合 PID、return code、错误日志和日志中的 `status=failed/completed` 标记更新状态。`experiment_report` 从日志中提取 `name=value` 指标，生成 final/min/max 和复现命令；失败或未知状态返回失败 `ToolResult`，不会把“报告文件已生成”等同于“实验成功”。

## 10. 科研专用工具链

### 10.1 文献检索

`arxiv_search` 直接调用 Atom API，支持：

- 普通关键词或显式 `all:` / `ti:` / `abs:` / `au:` / `cat:` 查询；
- arXiv 分类；
- `YYYY-MM-DD` 提交日期区间；
- 最多 30 条结果；
- 按 submitted date 降序。

输出包含标题、arXiv ID、首次提交、最近更新、作者、主分类、全部分类、摘要和论文页链接。无匹配是成功 observation，不是网络失败；模型应明确报告零结果，而不是用范围外论文凑数。

`web_search` 使用 DuckDuckGo HTML 入口发现候选 URL；`web_fetch` 负责白名单页面正文抓取。主循环优先把论文任务路由到结构化 arXiv，而不是依赖网页搜索结果格式。

### 10.2 PDF 解析与图像提取

`pdf_extract_text(parser="auto")` 的路由为：

```text
CUDA 可用且空闲显存 >= 阈值
        |
        +-- 是：Marker
        |        `-- 失败时 MarkItDown -> pypdf
        |
        `-- 否：MarkItDown
                 `-- 失败时 pypdf
```

解析结果写入工作区相对目录，默认是 `.mini-openclaw/pdf/<pdf-stem>/`：

- `paper.md`：metadata + 正文；
- `image_manifest.json`：图片路径、页码/类型和 SHA-256；
- `images/`：Marker 图片，或 PyMuPDF 提取的嵌入图和页面渲染。

文本超过 `max_chars` 时保留首尾。PDF 内容最终包裹为 `<external_document>` 不可信数据。`max_pages` 对 pypdf 文本和 PyMuPDF 图像路径生效；当前 Marker/MarkItDown 转换函数没有在自身 API 中真正截断页数，这是参数语义不完全一致的限制。

`paper_figure_analyze` 只接受工作区内 PNG/JPEG/WEBP，相对路径验证后单独初始化 Qwen 视觉后端，使用固定的“可见事实/合理推断/不可读信息”提示，不向视觉模型开放工具。

### 10.3 微信发送

微信工具的可选目标由环境预配置，默认只包含“文件传输助手”。实际权限与工具逻辑为：

- `plan` 模式把微信视为外部副作用并拒绝；
- 环境 `WECHAT_DRY_RUN=1` 时，工具执行阶段只打印预览，不连接 bridge；
- 目标在 `WX_TRUSTED_TARGETS` 时可免确认；
- 其他允许目标仍需逐次确认；
- 不在 allowlist 的目标由工具层拒绝，模型不能临时扩大联系人集合。

需要注意，`PermissionManager` 中存在“参数含 `dry_run=true` 则 allow”的分支，但当前 `wechat_file_transfer` schema 没有暴露 `dry_run` 字段，且禁止额外参数。也就是说，环境 dry-run 不会让权限层自动放行一个原本需要确认的非受信目标；权限裁决发生在工具读取 `WECHAT_DRY_RUN` 之前。默认“文件传输助手”本身属于 trusted target，所以常用 dry-run 演示仍可直接执行。

真实发送通过本地/WSL 可达的 bridge HTTP 接口；连接失败时可自动启动 PowerShell bridge。bridge URL、token、TLS 验证和启动命令虽然是内部函数参数/环境配置，但没有暴露在 Agent 工具 schema 中，降低模型任意改写连接控制面的风险。

## 11. MCP 与 Skills 扩展

### 11.1 MCP

`MCPClient` 实现最小 stdio + JSON-RPC 流程：

```text
subprocess.Popen
  -> initialize(protocolVersion=2024-11-05)
  -> notifications/initialized
  -> tools/list
  -> tools/call
```

外部工具注册为 `mcp__<name>`，避免与内置工具重名，并继续经过同一个 schema、权限、错误和 Trace 链路。

当前 CLI 的真实默认值是项目内 `mcp/calc_server.py`，会增加 `mcp__add` 和 `mcp__multiply` 两个工具；并不会自动尝试官方 filesystem server。设置 `OPENCLAW_MCP_COMMAND` 才会启动自定义 server，`--no-mcp` 则保持纯 23 个内置工具。

当前 MCP 客户端是同步、逐行 JSON 的最小实现，没有请求超时、并发复用、server 生命周期 close、资源/提示协议或完整 capabilities 协商，更适合教学和工具扩展示范，不是生产级 MCP runtime。

### 11.2 Skills

`skills/loader.py` 扫描 `skills/*/SKILL.md`，用 YAML frontmatter 解析 `name` 和 `description`。启动时仅把 catalog 加入 system；每轮再根据任务文本与 name/description 做轻量匹配：

- 英文、扩展名、数字 token 命中加权；
- 中文退化为 2～4 字 n-gram；
- 默认最低分 6，最多召回 3 个 Skill。

命中的完整正文通过 `add_context()` 注入。Skills 描述“如何完成一类任务”，Tools 执行“一次具体动作”；例如 `paper-figure-reader` 规定证据等级和输出结构，`paper_figure_analyze` 才真正调用视觉模型。

当前 9 个 Skill 为 `literature-review`、`paper-reader`、`paper-figure-reader`、`code-screenshot-debugger`、`codebase-research`、`experiment-runner`、`research-scheduler`、`wechat-file-transfer` 和 `example-skill`。

召回器只看任务文本和 frontmatter，不做 embedding、依赖解析或脚本权限隔离；Skill 正文最终仍是提示词，不能绕过权限层。

## 12. Trace、评测与消融证据

### 12.1 Trace v2

`agent/tracer.py` 使用 append-only JSONL。一次正常单 Agent run 包含：

- agent `run` span；
- 每轮 `llm/decide` span；
- 每次 `tool/<name>` span；
- `run_start` / `run_end`；
- 兼容旧分析器的 `step` 和 `tool_result` 事件；
- `protocol_repaired`、`compaction`、`final_blocked`、`error_budget_exhausted` 等生命周期事件。

span 通过 `trace_id`、`span_id`、`parent_span_id` 和单文件 `sequence` 关联。LLM span 保存 usage 和 model；工具 span保存耗时、类别和脱敏预览。JSONL 每条事件立即追加，因此进程中断后仍可读取已落盘部分。

Trace 报告支持：

```bash
python -m eval.trace_cli summary traces/demo.jsonl
python -m eval.trace_cli cost traces/demo.jsonl
python -m eval.trace_cli replay traces/demo.jsonl --details
python -m eval.trace_cli render traces/demo.jsonl --format html --output traces/demo.html
python -m eval.trace_cli simulate traces/demo.jsonl
python -m eval.trace_cli diagnose traces/demo.jsonl
```

`replay`、`simulate`、`diagnose` 和 HTML 渲染只读 Trace，不调用模型、不重放工具。诊断会检查慢 span、失败、重复调用、上下文增长、协议修复和稳定前缀变化。

### 12.2 Token、耗时和成本口径

汇总分别计算：

- wall duration：从最早 run start 到最晚 run end；
- LLM/tool duration 总和；
- aggregate span duration：并行 span 可能重叠，因此可大于 wall duration；
- prompt、completion、cache hit、cache creation token；
- 最慢 span 和最贵 span。

成本按模型和币种分别累计，不做隐式汇率换算。环境价格覆盖内置价格；无法识别的模型标记为 `unpriced`。只有全部可比较 span 使用同一币种时才报告全局 `priciest_span`。

### 12.3 测试结果

本次实际执行：

```bash
python -m agent.cli --selfcheck
python -m pytest -q tests
```

结果如下：

- 自检通过：23 个内置工具、FakeBackend、Agent / Memory / Prompt / MCP / Skills(9) / Trace 均可加载；
- 完整测试首次运行：`168 passed, 1 failed`，耗时 14.71 秒；
- 唯一失败为 Windows 后台实验状态轮询，日志已输出 `status=failed`，但测试等待窗口内仍读到 `running`；
- 单独复跑该用例：`1 passed`；
- 再运行主循环、上下文、权限、多 Agent 和 Trace 的 65 个聚焦用例：`65 passed`。

这说明核心控制链和当前并发改动通过了聚焦回归，但完整套件仍暴露一个平台相关的进程状态时序抖动。报告不把单独复跑通过写成“完整套件稳定全绿”。

### 12.4 Multi Agent 消融

`docs/ablation_report.md` 对同一组六张论文图片比较了多 Agent 与单 Agent。该次 Trace 的主要数据为：

| 指标     |              多 Agent |              单 Agent |
| -------- | --------------------: | --------------------: |
| 顶层耗时 |              约 739 s |              约 441 s |
| LLM 调用 |                    64 |                    12 |
| 工具调用 | 56（49 成功，7 失败） | 18（13 成功，5 失败） |
| 总 token |            约 961,688 |            约 215,420 |

这次实验不能证明多 Agent 更快：多 Agent token 约为单 Agent 的 4.5 倍，且一个图片子任务失败重试形成约 470 秒拖尾。但它验证了三项机制：六张图可以独立 Trace/TODO 隔离并行；一个局部 `partial` 不阻止父级综合；Reviewer 能基于分任务证据通过最终答案。

因此当前多 Agent 的主要收益是任务隔离、证据结构和局部失败容忍，而不是无条件降低 wall time。性能优化方向是减少 `Main -> broad Multimodal -> six image agents` 的中间层、限制慢任务重试、给子任务增加超时/取消和跨 Agent observation 缓存。

### 12.5 真实任务评测

`eval/run_suite.py` 在临时目录中运行真实 CLI，支持 `none`、`no-planning`、`no-memory`、`minimal-prompt` 消融，记录成功率、工具调用、token 和耗时。没有 `DEEPSEEK_API_KEY` 时直接返回错误码 2，拒绝用 FakeBackend 伪造模型能力数据。

评测成功条件目前是 return code、输出关键词和期望文件内容的规则匹配，适合回归基础操作，不等同于开放式科研质量评估。`eval/judge.py` 和独立 Reviewer 可补充模型评价，但仍需要人工抽查事实和来源。

## 13. 运行产物索引

| 路径                                               | 内容与生命周期                         |
| -------------------------------------------------- | -------------------------------------- |
| `MEMORY.md`                                      | 人可读长期记忆，跨会话保留             |
| `memory.json`                                    | 可选 KVMemory 数据                     |
| `.mini-openclaw/sessions/<run>/tasks.json`       | CLI 会话 TODO                          |
| `.mini-openclaw/subagents/<run>/<id>/tasks.json` | 子 Agent TODO                          |
| `.mini-openclaw/observations/`                   | 超长工具输出全文                       |
| `.mini-openclaw/pdf/<paper>/`                    | PDF Markdown、图片和 manifest          |
| `.mini-openclaw/schedules.json`                  | 定时任务定义、历史和聚合状态           |
| `.mini-openclaw/scheduler-runs/`                 | 定时任务 stdout、stderr、Trace、TODO   |
| `runs/<run-id>/`                                 | 实验 metadata、日志、return code、报告 |
| `traces/*.jsonl`                                 | 父级或单 Agent Trace                   |
| `traces/subagents/*.jsonl`                       | 子 Agent Trace                         |
| `eval/results.csv`                               | 真实任务消融结果                       |
| `security/redteam_results.csv`                   | 红队用例结果                           |

这些目录并非都适合提交 Git。默认实验 `.gitignore` 会忽略 `runs/`、`traces/`、`.mini-openclaw/`、日志、虚拟环境和缓存；需要保留结论时，应把小型汇总报告与原始大产物分开管理。

## 14. 已知限制与后续优先级

1. **并发缺少统一资源治理。** 动态 dispatch 限制 8 项和深度 3，但顶层 orchestration 没有等价项数上限；线程池也没有每子任务 timeout、取消、优先级或全局 token 预算。
2. **全局 cwd 不适合异构并行。** `AgentLoop._run_tool()` 使用进程级 `os.chdir()`；未来多 workdir 并行应改为显式根目录参数或隔离子进程。
3. **角色不是硬能力边界。** 三类子 Agent 当前共享完整注册表，安全只由权限层保证；若需要最小权限，应为角色构造只读/执行型注册表子集。
4. **Shell 不是跨平台强沙箱。** 没有 bwrap 时依赖黑名单；`auto-local` 尤其需要可信环境。可引入 Windows Job Object、容器、seccomp 或按命令 AST 的 allowlist。
5. **MCP 客户端仍是最小实现。** 缺少 timeout、shutdown、并发请求、能力协商和非工具资源；server 异常可能阻塞同步读取。同一 MCPClient 被并行子 Agent 共享时也没有 RPC 读写锁，不能保证多线程请求/响应配对。
6. **上下文估算和压缩可观测性有限。** 字符数/2 不是精确 tokenizer；压缩模型调用没有独立 span，成本和失败原因不够透明。
7. **启发式交付门禁有误判空间。** 调研和实验识别依赖关键词，字段检查依赖字符串覆盖；更稳健的方式是结构化最终结果 schema 与证据引用图。
8. **PDF 参数语义不完全统一。** `max_pages` 没有直接传给 Marker/MarkItDown；长文可能先完整解析再截断，消耗不可控。
9. **Scheduler 主要面向 POSIX cron。** Windows 持久唤醒、systemd timer、锁陈旧恢复和分布式调度尚未实现。
10. **后台实验状态存在 Windows 时序抖动。** 本次完整测试出现一次 `running` 未及时转 `failed`；应让状态判定优先读取 returncode 文件，并增加带退避的完成等待或更可靠的进程句柄管理。
11. **长 observation 归档名可能碰撞。** 同一轮同名工具多次产生超长结果时会覆盖；文件名应加入 tool call id。
12. **实验基线可能纳入未忽略文件。** 新仓库路径会执行 `git add .`，而自动生成的 ignore 列表不含 `.env` 和数据目录；应先做敏感文件扫描，改为显式 allowlist，或至少把常见秘密和大文件规则加入默认模板。
13. **微信 dry-run 的 schema 与权限分支不一致。** 工具只从环境变量读取 dry-run，但权限层只检查调用参数中的 `dry_run`；应把运行时 dry-run 状态显式传给权限层，或在 schema 中增加受控字段。
14. **FakeBackend 只能做外壳自检。** 任何关于规划质量、工具选择或科研正确率的结论都必须基于真实后端 Trace。

## 15. 结论

mini-OpenClaw 的核心价值不在工具数量，而在把模型的不确定决策放进可控制、可观察、可降级的宿主运行时：

- 工具调用先经过协议、schema、权限和工具内约束；
- observation、TODO、Git、return code 和 Trace 构成外部证据；
- 长任务通过预算、缓存、压缩和部分交付避免无限循环；
- 多 Agent 复用同一执行内核，以扁平任务、线程并发、独立状态和 Reviewer 形成证据闭环；
- 科研专用链路把文献日期、PDF 解析、图像证据、实验版本和调度产物纳入统一控制面。

当前实现已经具备完整的教学型科研 Agent 骨架，也清楚暴露了下一阶段工程化重点：强沙箱、并发资源预算、结构化证据引用、生产级 MCP、跨平台调度和更稳定的后台进程状态管理。
