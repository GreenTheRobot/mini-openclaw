# mini-OpenClaw（学生 starter 仓库）

> 你将在这 10 天里，把这个骨架填成一个能在命令行里干活的通用智能体。
> 每个模块里都有 `# TODO[DayN]` 标记，告诉你哪天该填哪里。

## 这是什么

mini-OpenClaw 是一个 Claude Code 式的命令行 Agent：
一个**主循环**反复调用**大模型后端**，模型输出**工具调用**（read/write/bash/…），
主循环执行工具、把结果喂回模型，直到任务完成。再叠加 **MCP**（可插拔外部工具）、
**Skills**（可加载领域能力）和**安全层**（权限/沙箱/注入防护）。

```
你的请求 ──► [主循环 loop.py] ──► [后端 server.py ──► 大模型]
                  ▲   │  模型输出 <tool_call>{...}</tool_call>
                  │   ▼
            tool result ◄── [工具分发：read/write/bash/edit/grep/...]
                              ├── 内置工具 (tools/)
                              ├── MCP 工具 (mcp/)
                              └── Skills (skills/)
```

## 目录结构与建设节奏

| 模块 | 你要做什么 | 哪天 |
|------|-----------|------|
| `backend/` | DeepSeek API 客户端（已给 `client.py`，配 key 即用）；Day2 连通后端 + 首个工具 schema | Day1–2 |
| `prompt/` | render_prompt(messages, tools) 对话模板渲染 + parse_tool_calls | Day3 |
| `agent/` | 系统提示词（Day2 起草，Day5 完善）、ReAct 主循环、上下文管理 | Day2, Day5, Day7 |
| `tools/` | read/write/bash → edit/grep/glob → web_fetch/task_list | Day5, Day6, Day7 |
| `mcp/` | 最小 MCP 客户端（stdio + JSON-RPC）| Day8 |
| `skills/` | Skills 加载器 + 你领域的 Skill | Day9 |
| `eval/` | 任务集 + 指标评测 + 消融 | Day7, Day10 |

> 逐日构建目标详见各 `course/dayNN/lab-guide.md`；`grep -rn "TODO\[Day" .` 可看全部施工点。
> 里程碑：**v1（Day6）** 端到端可用 · **v3（Day9）** 可扩展 · **终版（Day10）** 含安全层，Demo Day 展示（占总评 95%）。

## 快速开始

```bash
# 1. Python 环境（agent 侧不吃显存）
conda create -n openclaw python=3.11 && conda activate openclaw
pip install -r requirements.txt

# 2. 先跑通骨架的"假后端"自检（Day1 就能跑）
python -m agent.cli --selfcheck

# 3. 之后每天填对应模块，重跑相关入口
```

## 已完成任务清单

> 本清单按当前仓库代码维护，用于记录 mini-openclaw 已经从课程骨架推进完成的部分。

- [x] 完成命令行入口与自检流程：`python -m agent.cli --selfcheck` 可检查工具注册表、FakeBackend 和主循环导入状态。
- [x] 完成 DeepSeek API 后端封装：支持通过 `DEEPSEEK_API_KEY` 调用 OpenAI 兼容的 chat completions 接口，并归一化 tool calls。
- [x] 完成离线 FakeBackend：未配置真实模型 key 时可回退到规则后端，便于打通主流程。
- [x] 完成 Agent ReAct 主循环：支持模型返回工具调用、执行工具、注入 observation，并在无工具调用时返回最终答复。
- [x] 完成基础工具抽象与注册表：统一 `Tool`、`ToolRegistry`、OpenAI tools schema 导出和默认工具注册。
- [x] 完成 Day5 基础工具：`read`、`write`、`bash`。
- [x] 完成 Day6 文件与代码检索工具：`edit`、`grep`、`glob`。
- [x] 完成 Day7 网络抓取工具：`web_fetch`，支持 HTML 转 markdown 并按 token 预算截断。
- [x] 完成上下文管理基础能力：支持 token 粗估、长历史压缩、工具结果截断。
- [x] 完成最小 MCP 客户端：支持 stdio 启动 server、JSON-RPC initialize、`tools/list`、`tools/call`。
- [x] 完成 MCP 工具透明注册：外部 MCP 工具会以 `mcp__*` 命名空间合并进工具注册表。
- [x] 完成示例 MCP server：提供 `echo_server.py`、`calc_server.py` 用于本地验证 MCP 流程。
- [x] 完成 Skills 加载器：支持扫描 `skills/*/SKILL.md`、解析 frontmatter、生成 catalog、按任务关键词召回 skill 正文。
- [x] 完成科研智能体系统提示词：覆盖科研流程、工具使用规则、实验可复现规范和代码修改工作流。
- [x] 完成评测基础模块：包含任务集、轨迹记录、工具调用指标、消融样例和 LLM-as-judge 雏形。

## 已实现功能清单

### Agent 运行能力

- 命令行运行：`python -m agent.cli "任务描述"`。
- 自动选择后端：优先使用 `DeepSeekBackend`，缺少配置时回退 `FakeBackend`。
- 自动接入 MCP：有 `npx` 时尝试启动官方 filesystem MCP server，否则回退本地 `mcp/calc_server.py`。
- 系统提示词增强：启动时注入基础科研 agent 行为规范、工具说明和相关 Skills 内容。
- 多轮工具调用：主循环可连续执行工具调用，直到模型给出最终回答或达到最大轮数。

### 内置工具能力

- `read`：读取文本文件，并带行号返回内容。
- `write`：写入或覆盖文本文件。
- `bash`：在当前工作目录执行 shell 命令，返回 stdout、stderr 和 return code。
- `edit`：基于唯一 `old` 片段做局部替换，避免误改多处。
- `grep`：基于 ripgrep 搜索文件内容，返回文件和行号。
- `glob`：按通配模式递归查找文件。
- `web_fetch`：抓取 URL，转成 markdown，并控制返回长度。

### 扩展能力

- MCP 扩展：可把外部 server 暴露的工具包装成 mini-openclaw 工具。
- Skills 扩展：可通过新增 `skills/<name>/SKILL.md` 添加领域流程和知识。
- 示例 Skill：`csv-quick-report`，用于 CSV 快速统计与 markdown 报告生成场景。

### 评测与可观测能力

- 工具调用评测：支持 JSON 合法率、工具选择正确率、参数正确率等指标。
- 端到端任务样例：包含读配置、列目录、查 DOI、生成 hello 脚本、TODO 报告等任务结构。
- 轨迹记录与回放：支持将每步工具调用、token 统计和备注写入 JSONL 后回放。
- 消融样例：提供有/无 system prompt 的成功率对比样例。
- LLM-as-judge：提供基于 rubric 的回答评分雏形。

## 里程碑

- **v1（Day6）**：`python -m agent.cli "创建 hello.py 并运行输出当前时间"` 能完成。
- **v3（Day9）**：能加载 MCP server 工具 + 自定义 Skill。
- **终版（Day10）**：含安全层，Demo Day 现场任务。

## 约定

- 全程一个 git 仓库，**按 day 打 tag**（`v1`, `v3`, `final`）。
- 每个模块自带一个 `README.md`，记录你的设计决策（技术文档分数来源）。
