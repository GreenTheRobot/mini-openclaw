"""评测任务集与指标（Day4 体验 / Day7 评测；Day10 任务成功率 / 消融）。

两类评测：
  A) 工具调用质量：在固定测试集上算三项指标（Day4 用 API 体验，Day7 系统化）。
  B) 端到端任务成功率（Day7 起 / Day10 消融）：跑一批任务，看完成率，对比不同配置。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

# 一条“轨迹记录”长这样（步骤 2 会给出完整样本）：
#   {"task": "任务名", "steps": [ {tool_calls, raw, prompt_tokens, completion_tokens}, ... ],
#    "final": "agent 的最终自然语言答复"}
Trajectory = dict

@dataclass
class Task:
    name: str
    instruction: str                       # 给 agent 的指令
    check: Callable[[Trajectory], bool]    # 成功判据：吃一条轨迹，判成败

# ---- 成功判据（程序化优先）----
def _check_read_config(traj: Trajectory) -> bool:
    # 成功 = 期间调用过 read 且最终答复里报出了 timeout 的值
    used_read = any(
        tc["name"] == "read"
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )
    return used_read and "30" in traj.get("final", "")

def _check_list_dir(traj: Trajectory) -> bool:
    return any(
        tc["name"] == "bash" and "ls" in str(tc.get("arguments", {}))
        for s in traj["steps"] for tc in s.get("tool_calls", [])
    )

# 再补一条“你组领域”的任务判据
def _check_search_doi(traj: Trajectory) -> bool:
    # 判断文献调研是否进行搜索并给出了论文的DOI

    used_search = any(
        tc["name"] in ("search", "web_search")
        for s in traj["steps"]
        for tc in s.get("tool_calls", [])
    )

    final = traj.get("final", "").lower()

    return used_search and "doi" in final

SAMPLE_TASKS: list[Task] = [
    Task("read-config", "读取 config.json，告诉我 timeout 是多少", _check_read_config),
    Task("list-dir", "列出当前目录下的文件", _check_list_dir),
    Task("search-doi", "查找 Transformer 论文，并告诉我 DOI", _check_search_doi),
    # 可再加 1 条
]

@dataclass
class ToolCallCase:
    request: str                 # 用户请求
    expected_tool: str           # 期望调用的工具名
    expected_args: dict          # 期望参数（可只校验关键字段）


# Day6 固定测试集（教师会提供 ~50 条；这里给格式示例）
TOOLCALL_TESTSET: list[ToolCallCase] = [
    ToolCallCase("把 a.txt 的内容读出来", "read", {"path": "a.txt"}),
    ToolCallCase("在当前目录运行 ls", "bash", {"command": "ls"}),
    # 科研领域用例在 research_tasks.json 中维护
]


@dataclass
class E2ETask:
    name: str
    instruction: str
    check: str                   # 如何判定成功（人工/脚本）


# Day10 端到端任务集（消融用）
E2E_TASKS: list[E2ETask] = [
    E2ETask("hello", "创建 hello.py 并运行，输出当前时间", "存在 hello.py 且运行打印了时间"),
    E2ETask("todo-report", "扫描本项目所有 Python 文件里的 TODO 注释，生成 markdown 报告",
            "生成的报告列出了真实存在的 TODO"),
    # 真实端到端科研任务由 eval.run_suite 加载
]
