"""可选 Reviewer：对最终答案的证据、数字和完成声明做独立检查。"""
from __future__ import annotations

from typing import Any

_REVIEW_PROMPT = """你是科研任务 Reviewer。只审查，不调用工具，不新增事实。
检查：1) 结论是否有工具/日志证据；2) 数字是否有来源；3) 是否把推测写成事实；
4) 是否在测试失败时声称成功；5) 是否遗漏复现信息。
输出“审查结论：通过/需修订”，再列出最多五条具体问题。"""


def review_answer(backend: Any, task: str, answer: str) -> str:
    response = backend.chat([
        {"role": "system", "content": _REVIEW_PROMPT},
        {"role": "user", "content": f"原任务：\n{task}\n\n待审答案：\n{answer}"},
    ], tools=[])
    return str(response.get("content", "")).strip()