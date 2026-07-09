# TODO 注释汇总报告

> 生成时间：自动扫描  
> 扫描范围：本项目所有 `.py` 文件  
> 统计：共 **32 条** TODO 注释（不含 README.md）

---

## `agent/cli.py`

| 行号 | 内容 |
|------|------|
| 38 | `下一步：按 dayNN 的 lab-guide 填 # TODO 标记。` |

## `agent/context.py`

| 行号 | 内容 |
|------|------|
| 15 | `粗估即可（字符数/4 或用 tokenizer 精确数）` |
| 23 | `实现 compaction：` |

## `agent/loop.py`

| 行号 | 内容 |
|------|------|
| 43 | `分发并执行工具，把每个结果作为 role="tool" 注入 messages：` |
| 49 | `加错误恢复（try/except，把异常文本作为 observation，让模型自我修复）` |
| 54 | `在这里做上下文管理：超出 token 预算时触发 compaction（见 agent/context.py）` |

## `agent/prompts.py`

| 行号 | 内容 |
|------|------|
| 114 | `长任务时引导模型使用 task_list 维护待办。` |

## `eval/metrics.py`

| 行号 | 内容 |
|------|------|
| 87 | `抽出 {...} 部分尝试 json.loads（可复用 prompt.render.parse_tool_calls）` |
| 112 | `从 <tool_call>...</tool_call> 中取出 JSON 串` |

## `eval/tasks.py`

| 行号 | 内容 |
|------|------|
| 69 | `按你组的领域补充更多用例` |
| 83 | `E2ETask("todo-report", "扫描本项目所有 Python 文件里的 TODO 注释，生成 markdown 报告",` |
| 84 | `"生成的报告列出了真实存在的 TODO"),` |
| 85 | `补充你领域的任务` |

## `mcp/client.py`

| 行号 | 内容 |
|------|------|
| 28 | `启动子进程，stdin/stdout 接管，做 initialize 握手` |
| 32 | `发一条 JSON-RPC 请求（带自增 id），读回对应响应` |
| 36 | `调 tools/list，返回工具描述列表` |
| 40 | `调 tools/call，返回结果文本` |

## `prompt/render.py`

| 行号 | 内容 |
|------|------|
| 18 | `校对你所用模型的真实特殊标记！拼错一个 token，模型行为就会跑偏。` |
| 29 | `设计一个清晰的工具说明格式，并约定模型用` |
| 46 | `把 tools 说明并入 system 段` |
| 47 | `逐条 message 用 ROLE_TOKENS 包裹拼接` |
| 48 | `末尾以 assistant 起始标记结尾，提示模型开始生成` |
| 54 | `用正则/状态机提取所有 <tool_call>...</tool_call>，json.loads 出 name/arguments` |

## `skills/loader.py`

| 行号 | 内容 |
|------|------|
| 32 | `解析 YAML frontmatter（name/description）+ 正文 body` |
| 46 | `渲染成一段文本，放进系统提示词` |

## `tools/base.py`

| 行号 | 内容 |
|------|------|
| 62 | `取消注释并实现：` |
| 68 | `再加入完整工具集（→ v1 里程碑）：` |
| 75 | `再加入：` |

## `tools/more_tools.py`

| 行号 | 内容 |
|------|------|
| 3 | `每个工具上午讲设计权衡，下午实现。这里只给签名与 TODO，便于你拆到独立文件。` |
| 25 | `httpx 抓取 -> markdownify 转 markdown -> 截断到预算内` |
| 31 | `维护一个结构化待办（add/update/complete），作为模型的 scratchpad` |

## `tools/shell.py`

| 行号 | 内容 |
|------|------|
| 21 | `接入权限层 + 沙箱（bwrap/firejail/docker），危险命令需确认` |

---

## 按 Day 分组

| Day | 数量 | 涉及文件 |
|-----|------|----------|
| Day3 | 6 | `prompt/render.py` |
| Day5 | 2 | `tools/base.py`, `agent/loop.py` |
| Day6 | 1 | `tools/base.py` |
| Day7 | 8 | `agent/context.py`, `agent/loop.py`, `agent/prompts.py`, `eval/metrics.py`, `eval/tasks.py`, `tools/more_tools.py` |
| Day8 | 4 | `mcp/client.py` |
| Day9 | 2 | `skills/loader.py` |
| Day10 | 2 | `eval/tasks.py`, `tools/shell.py` |
| 未标注 Day | 7 | `agent/cli.py`, `tools/base.py`, `tools/more_tools.py` |

---

*注：README.md 中的 TODO 说明未计入本报告。*
