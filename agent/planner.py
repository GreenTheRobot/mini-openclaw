"""轻量 Planner：判断任务复杂度并要求使用显式 task_list。"""
from __future__ import annotations

import re

_COMPLEX_MARKERS = {
    "并且", "然后", "最后", "修改", "测试", "实验", "报告", "论文", "代码",
    "检索", "分析", "运行", "修复", "复现", "通知", "监控",
}


def needs_plan(task: str) -> bool:
    hits = sum(marker in task for marker in _COMPLEX_MARKERS)
    clauses = len(re.findall(r"[，。；,;]|然后|最后", task)) + 1
    return hits >= 3 or clauses >= 4 or len(task) >= 120


def planning_guidance(task: str) -> str:
    if not needs_plan(task):
        return ""
    return (
        "# 本次任务规划要求\n"
        "这是复杂任务。执行任何写入或实验前，先调用 task_list 创建按依赖排序的待办；"
        "每次只标记一个 in_progress，完成后写入可验证结果，失败时记录原因并更新后续计划。"
    )