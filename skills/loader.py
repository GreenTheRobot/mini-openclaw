"""Skills 加载器（Day9）。

Skill 与 Tool 的区别：
  - Tool 是一次函数调用（read 一个文件）。
  - Skill 是一包"领域知识 + 操作流程 + 可选脚本/资源"，用一个 SKILL.md 描述，
    在合适的时候被加载进上下文，告诉模型"面对这类任务该怎么一步步做"。

SKILL.md 结构（约定）：
  ---
  name: pdf-report
  description: 一句话说明何时该用这个 skill（用于召回判断）
  ---
  正文：步骤、注意事项、可调用的脚本路径、示例。

加载器要做：扫描 skills/ 下每个含 SKILL.md 的目录，解析 frontmatter，
按需把正文注入系统提示词 / 作为可发现的能力清单。
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def parse_skill_md(text: str, path: Path) -> Skill:
    import yaml
    name = description = ""
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)   # 头尾两个 --- 之间是 frontmatter
        meta = yaml.safe_load(fm) or {}
        name = meta.get("name", "")
        description = meta.get("description", "")
    return Skill(name=name, description=description, body=body.strip(), path=path)


def load_skills(root: str = "skills") -> list[Skill]:
    """扫描 root 下所有 SKILL.md。"""
    skills: list[Skill] = []
    for md in Path(root).glob("*/SKILL.md"):
        skills.append(parse_skill_md(md.read_text(encoding="utf-8"), md))
    return skills


def skills_catalog(skills: list[Skill]) -> str:
    """生成给模型看的可用 skill 清单（name + description），用于按需召回。"""
    # TODO[Day9] 渲染成一段文本，放进系统提示词
    return "\n".join(f"- {s.name}: {s.description}" for s in skills)


def _match_score(task: str, skill: Skill) -> int:
    """用轻量关键词匹配估算某个 skill 是否和当前任务相关。"""
    task_l = task.lower()
    haystack = f"{skill.name} {skill.description}".lower()
    score = 0

    # 英文、扩展名、数字等 token，例如 csv / markdown / .csv。
    for token in re.findall(r"[a-z0-9_.+-]+", haystack):
        if len(token) >= 2 and token in task_l:
            score += 3

    # 中文没有空格，退化为 2~4 字短语匹配；命中多个短语才更可信。
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", haystack))
    seen: set[str] = set()
    for n in (4, 3, 2):
        for i in range(0, max(0, len(chinese) - n + 1)):
            token = chinese[i:i + n]
            if token in seen:
                continue
            seen.add(token)
            if token in task:
                score += n
    return score


def select_skills(task: str, skills: list[Skill], limit: int = 3,
                  min_score: int = 6) -> list[Skill]:
    """根据用户任务和 skill 描述做按需召回。"""
    scored = [(score, skill) for skill in skills
              if (score := _match_score(task, skill)) >= min_score]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [skill for _, skill in scored[:limit]]


def render_skill_bodies(skills: list[Skill]) -> str:
    """把命中的 skill 正文渲染成可注入系统提示词的文本。"""
    parts = []
    for skill in skills:
        parts.append(
            f"## Skill: {skill.name}\n"
            f"用途：{skill.description}\n\n"
            f"{skill.body}"
        )
    return "\n\n".join(parts)
