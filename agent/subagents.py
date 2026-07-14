"""Lightweight multi-agent orchestration built on the existing AgentLoop."""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from agent.loop import AgentLoop, _is_insufficient_research_answer, _research_answer_repair_prompt
from agent.permissions import PermissionManager
from agent.reviewer import review_answer, review_needs_revision
from agent.sanitize import sanitize_for_json
from eval.tracer import Tracer
from tools.base import ToolRegistry

NO_ASSIGNMENT_VALUES = {
    "", "不需要", "无需", "无", "没有", "否", "不用", "不必",
    "none", "no", "n/a", "na", "not needed", "not required",
}
RUNTIME_HINT_RE = re.compile(
    r"(# 运行时日期\s*\n.*?不得用旧年份搜索结果冒充近期结果。)",
    flags=re.S,
)

ORCHESTRATION_PROMPT = """你是主 Agent，负责把控全局，而不是亲自完成所有细节。
你要先判断当前任务是否真的需要子 agent。简单、单步、无需跨角色协作的任务应由主 Agent 直接完成；复杂任务、需要并行证据、代码实验、论文/图表多源分析或用户明确要求子 agent 时，才启用子 agent。

请只输出一个 JSON 对象，不要输出 Markdown：
{
  "use_subagents": true,
  "reason": "为什么启用或不启用子 agent",
  "main_task": "如果不启用子 agent，主 Agent 应直接完成的任务",
  "assignments": {
    "research": "分配给 Research Agent 的具体工作；不需要则为空字符串",
    "engineering": "分配给 Engineering Agent 的具体工作；不需要则为空字符串",
    "multimodal": "分配给 Multimodal Agent 的具体工作；不需要则为空字符串"
  }
}

分配要求：每个子 agent 的任务必须具体、可执行、边界清楚，并说明需要交付什么证据或产物。不要把原始任务原封不动丢给所有子 agent。"""

RESEARCH_PROMPT = """你是 Research Agent，负责查论文、读论文、网页调研和证据整理。
优先使用 arxiv_search、web_search、web_fetch、download_file、pdf_extract_text、paper_figure_analyze。
输出必须区分已验证事实、合理推断和缺口，并保留来源链接或文件路径。"""

ENGINEERING_PROMPT = """你是 Engineering Agent，负责执行型、集成型和验证型工作，包括代码阅读、文件修改、实验准备、运行验证、外部通知、调度、记忆写入和可复现记录。
你拥有完整工具集；必须优先使用已经注册的精确工具名，不要在存在专用工具时改用 bash 绕行。不要猜测代码内容；修改、发送、调度或实验后必须说明验证结果。"""

MULTIMODAL_PROMPT = """你是 Multimodal Agent，负责理解用户附带的图片、论文图表、终端截图和视觉证据。
输出可见事实、合理推断、不确定信息，以及这些视觉信息对原任务的影响。"""

SYNTHESIS_PROMPT = """你是 multi-agent coordinator。你会收到 Planner、Research、Engineering 和 Multimodal 子 agent 的结果。
请综合为最终答复：直接回答用户原始任务，最终正文必须以任务本身的专业内容为中心，依据、产物、未完成项和风险只作为支撑信息。
对文献检索、论文阅读、网页/项目/GitHub 调研任务，最终答案必须保留可点击的论文、网页或仓库来源链接；如果子 agent 没有找到链接，要明确说明缺口和已尝试路径，不能静默省略。
论文阅读/分析任务应优先讲清问题背景、核心方法、模型结构、实验结论、贡献、局限和你的综合理解；不要把正文写成工具证据清单或审查报告。
不要编造子 agent 没有验证的信息。"""

REVISION_PROMPT = SYNTHESIS_PROMPT + """

你还会收到 Reviewer 的审查意见。Reviewer 意见是内部质量约束，不是最终答案的主题。
请对待审答案做最小必要修订：保留原本有价值的论文分析、结构化解释和综合判断，只修正 Reviewer 指出的实质问题。
如果某个断言缺少依据，请删除、降级为推断或补充简短限定；如果有失败、缺口和风险，请自然融入相关段落。
不要把最终答案写成“我修复了哪些 Reviewer 问题”的审查报告；不要为了列依据而牺牲对论文内容的分析深度；不要新增子 agent 输出和工具记录中没有的事实。"""

RESEARCH_MARKERS = {
    "论文", "文献", "paper", "arxiv", "github", "网页", "调研", "前沿", "动态",
    "方法", "综述", "阅读", "多模态", "模型压缩", "token", "cache",
}
ENGINEERING_MARKERS = {
    "代码", "仓库", "实现", "修复", "测试", "实验", "运行", "报错", "debug",
    "配置", "训练", "复现", "指标", "脚本", "benchmark",
}

RESEARCH_TOOLS = {
    "task_list", "todo_write", "update_todo",
    "read", "glob", "grep",
    "arxiv_search", "web_search", "web_fetch", "download_file",
    "pdf_extract_text", "pdf_metadata", "paper_figure_analyze",
    "remember",
}
MULTIMODAL_TOOLS = {
    "task_list", "todo_write", "update_todo",
    "read", "glob", "pdf_extract_text", "pdf_metadata", "paper_figure_analyze",
}


@contextlib.contextmanager
def _temporary_env(name: str, value: str):
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "agent"


def _registry_subset(registry: ToolRegistry, names: set[str]) -> ToolRegistry:
    subset = ToolRegistry()
    for name in registry.names():
        if name in names:
            tool = registry.get(name)
            if tool is not None:
                subset.register(tool)
    return subset


def _wants_research(task: str) -> bool:
    lowered = task.lower()
    return any(marker.lower() in lowered for marker in RESEARCH_MARKERS)


def _wants_engineering(task: str) -> bool:
    lowered = task.lower()
    return any(marker.lower() in lowered for marker in ENGINEERING_MARKERS)


def _json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _fallback_orchestration(task: str, image_paths: list[str]) -> dict[str, Any]:
    wants_research = _wants_research(task)
    wants_engineering = _wants_engineering(task)
    explicit_multi = any(marker in task.lower() for marker in ("子agent", "子 agent", "多agent", "多 agent", "分工", "调度", "多层次"))
    assignments = {
        "research": "",
        "engineering": "",
        "multimodal": "",
    }
    if wants_research:
        assignments["research"] = "围绕原始任务收集、阅读和整理论文、网页、PDF 或项目证据，输出关键事实、来源、推断和缺口。"
    if wants_engineering:
        assignments["engineering"] = "围绕原始任务检查代码、运行必要验证或实验，输出修改点、命令结果、失败原因和可复现记录。"
    if image_paths:
        assignments["multimodal"] = "分析用户附带的图像内容，输出可见事实、不确定点，以及图像证据对原始任务的影响。"
    use_subagents = explicit_multi or bool(image_paths) or sum(bool(value) for value in assignments.values()) >= 2
    return {
        "use_subagents": use_subagents,
        "reason": "主 Agent 调度输出无法解析，使用本地保守规则。",
        "main_task": task,
        "assignments": assignments,
    }


def _normalize_assignment(value: Any) -> str:
    text = str(value or "").strip()
    if text.strip("。.!！ ").lower() in NO_ASSIGNMENT_VALUES:
        return ""
    return text


def _runtime_hint(system_prompt: str) -> str:
    match = RUNTIME_HINT_RE.search(system_prompt)
    return match.group(1).strip() if match else ""


def _orchestration_plan(backend: Any, task: str, image_paths: list[str], system_prompt: str = "") -> dict[str, Any]:
    runtime_hint = _runtime_hint(system_prompt)
    response = backend.chat([
        {"role": "system", "content": ORCHESTRATION_PROMPT},
        {"role": "user", "content": (
            f"原始任务：\n{task}\n\n"
            + (f"运行时日期约束：\n{runtime_hint}\n\n" if runtime_hint else "")
            + f"是否包含直接图像输入：{'是' if image_paths else '否'}"
        )},
    ], tools=[])
    parsed = _json_object(str(response.get("content", "")))
    fallback = _fallback_orchestration(task, image_paths)
    if not parsed:
        return fallback
    assignments = parsed.get("assignments")
    if not isinstance(assignments, dict):
        assignments = {}
    normalized_assignments = {
        "research": _normalize_assignment(assignments.get("research", "")),
        "engineering": _normalize_assignment(assignments.get("engineering", "")),
        "multimodal": _normalize_assignment(assignments.get("multimodal", "")),
    }
    if not image_paths:
        normalized_assignments["multimodal"] = ""
    use_subagents = bool(parsed.get("use_subagents", fallback["use_subagents"]))
    if not any(normalized_assignments.values()):
        use_subagents = False
    return {
        "use_subagents": use_subagents,
        "reason": str(parsed.get("reason", "") or fallback["reason"]).strip(),
        "main_task": str(parsed.get("main_task", "") or task).strip(),
        "assignments": normalized_assignments,
    }


def _agent_trace_path(trace_path: Path, role: str) -> Path:
    directory = trace_path.parent / "subagents"
    return directory / f"{trace_path.stem}.{_safe_name(role)}.jsonl"


def _agent_todo_path(parent_run_id: str, role: str) -> str:
    return (Path(".mini-openclaw") / "subagents" / _safe_name(parent_run_id) / _safe_name(role) / "tasks.json").as_posix()


def _append_trace_event(trace_path: Path, run_id: str, event: str, **payload: Any) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    record = sanitize_for_json({
        "ts": round(time.time(), 3),
        "run_id": run_id,
        "event": event,
        **payload,
    })
    with trace_path.open("a", encoding="utf-8", errors="backslashreplace") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _trace_tool_evidence(trace_path: Path, role: str, max_chars: int = 3000) -> str:
    if not trace_path.exists():
        return ""
    rows: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") != "tool_result":
            continue
        rows.append({
            "role": role,
            "step": record.get("step"),
            "tool": record.get("tool"),
            "arguments": record.get("arguments"),
            "success": bool(record.get("success")),
            "observation": str(record.get("observation", ""))[:700],
        })
    if not rows:
        return ""
    text = json.dumps(rows, ensure_ascii=False, indent=2)
    return text[:max_chars]


def _role_event_callback(
    event_callback: Callable[[str, dict[str, Any]], None] | None,
    role: str,
) -> Callable[[str, dict[str, Any]], None] | None:
    if event_callback is None:
        return None

    def callback(event: str, payload: dict[str, Any]) -> None:
        tagged = dict(payload)
        tagged.setdefault("role", role)
        event_callback(event, tagged)

    return callback


def _run_role(
    *,
    role: str,
    role_prompt: str,
    original_task: str,
    assigned_task: str,
    backend: Any,
    registry: ToolRegistry,
    workdir: Path,
    trace_path: Path,
    parent_run_id: str,
    permission_mode: str,
    auto_approve: bool,
    confirm_callback: Callable[..., Any] | None,
    context_budget: int,
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    image_paths: list[str] | None = None,
) -> tuple[str, str]:
    role_trace_path = _agent_trace_path(trace_path, role)
    tracer = Tracer(role_trace_path)
    manager = PermissionManager(permission_mode)
    loop = AgentLoop(
        backend,
        registry,
        role_prompt,
        max_turns=8,
        workdir=workdir,
        auto_approve=auto_approve,
        confirm_callback=confirm_callback,
        tracer=tracer,
        event_callback=_role_event_callback(event_callback, role),
        context_budget=context_budget,
        permission_manager=manager,
    )
    role_task = (
        f"原始用户任务：\n{original_task}\n\n"
        f"主 Agent 分配给你的具体工作：\n{assigned_task}\n\n"
        f"请只完成 {role} 职责范围内的工作，并把证据、产物路径、失败原因和未完成项写清楚。"
    )
    with _temporary_env("MINI_OPENCLAW_TODO_PATH", _agent_todo_path(parent_run_id, role)):
        answer = loop.run(role_task, image_paths=image_paths)
    return answer, _trace_tool_evidence(role_trace_path, role)


def _run_main_agent(
    *,
    task: str,
    backend: Any,
    registry: ToolRegistry,
    system_prompt: str,
    workdir: Path,
    trace_path: Path,
    parent_run_id: str,
    permission_mode: str,
    auto_approve: bool,
    confirm_callback: Callable[..., Any] | None,
    context_budget: int,
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    image_paths: list[str] | None = None,
) -> str:
    tracer = Tracer(_agent_trace_path(trace_path, "main"))
    manager = PermissionManager(permission_mode)
    loop = AgentLoop(
        backend,
        registry,
        system_prompt + "\n\n你是主 Agent。请直接完成当前任务；只有用户可见答案应聚焦任务本身。",
        max_turns=12,
        workdir=workdir,
        auto_approve=auto_approve,
        confirm_callback=confirm_callback,
        tracer=tracer,
        event_callback=_role_event_callback(event_callback, "Main Agent"),
        context_budget=context_budget,
        permission_manager=manager,
    )
    with _temporary_env("MINI_OPENCLAW_TODO_PATH", _agent_todo_path(parent_run_id, "main")):
        return loop.run(task, image_paths=image_paths)


def _synthesize_answer(
    backend: Any,
    task: str,
    evidence: str,
    *,
    previous_answer: str = "",
    review: str = "",
) -> str:
    if review:
        messages = [
            {"role": "system", "content": REVISION_PROMPT},
            {"role": "user", "content": (
                f"原始任务：\n{task}\n\n"
                f"子 agent 输出：\n{evidence}\n\n"
                f"待修订答案：\n{previous_answer}\n\n"
                f"Reviewer 审查意见：\n{review}"
            )},
        ]
    else:
        messages = [
            {"role": "system", "content": SYNTHESIS_PROMPT},
            {"role": "user", "content": f"原始任务：\n{task}\n\n子 agent 输出：\n{evidence}"},
        ]
    response = backend.chat(messages, tools=[])
    return str(response.get("content", "")).strip() or evidence


def _repair_research_answer(
    backend: Any,
    task: str,
    evidence: str,
    answer: str,
) -> str:
    response = backend.chat([
        {"role": "system", "content": SYNTHESIS_PROMPT},
        {"role": "user", "content": (
            _research_answer_repair_prompt(task, answer)
            + "\n\n现在禁止调用工具；请只基于下面已有子 agent 输出和工具证据重写最终答案。"
            + "如果证据中存在 URL、arXiv ID、论文页、项目页或仓库地址，必须在最终答案中保留为可点击链接。"
            + "如果证据中确实没有来源链接，必须明确写出“未找到可点击来源链接”并说明缺口。\n\n"
            f"已有子 agent 输出和证据：\n{evidence}"
        )},
    ], tools=[])
    return str(response.get("content", "")).strip() or answer


def _has_source_reference(text: str) -> bool:
    lowered = text.lower()
    return "http://" in lowered or "https://" in lowered or "arxiv:" in lowered


def _main_agent_task(original_task: str, main_task: str) -> str:
    main_task = (main_task or original_task).strip()
    if not main_task or main_task == original_task:
        return original_task
    return (
        f"原始用户任务：\n{original_task}\n\n"
        f"主 Agent 决定直接执行的具体任务：\n{main_task}"
    )


def run_multi_agent(
    *,
    task: str,
    backend: Any,
    vision_backend: Any | None = None,
    registry: ToolRegistry,
    system_prompt: str,
    workdir: str | Path,
    trace_path: str | Path,
    parent_run_id: str,
    image_paths: list[str] | None = None,
    permission_mode: str = "default",
    auto_approve: bool = False,
    confirm_callback: Callable[..., Any] | None = None,
    context_budget: int = 20000,
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> str:
    """Let the main agent decide whether and how to use role-based subagents."""
    workdir = Path(workdir).resolve()
    trace_path = Path(trace_path)
    image_paths = image_paths or []
    orchestration = _orchestration_plan(backend, task, image_paths, system_prompt)
    _append_trace_event(
        trace_path,
        parent_run_id,
        "orchestration",
        plan=orchestration,
    )
    if event_callback is not None:
        event_callback("orchestration", {
            "use_subagents": orchestration["use_subagents"],
            "reason": orchestration.get("reason", ""),
            "assignments": orchestration.get("assignments", {}),
        })
    if not orchestration["use_subagents"]:
        direct_backend = vision_backend if image_paths and vision_backend is not None else backend
        main_task = _main_agent_task(task, orchestration["main_task"] or task)
        if event_callback is not None:
            event_callback("main_agent_start", {"task": main_task})
        answer = _run_main_agent(
            task=main_task,
            backend=direct_backend,
            registry=registry,
            system_prompt=system_prompt,
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
            image_paths=image_paths if direct_backend is vision_backend else None,
        )
        if event_callback is not None:
            event_callback("main_agent_done", {})
        return answer

    assignments = dict(orchestration.get("assignments") or {})
    role_outputs: list[tuple[str, str]] = [(
        "Main Agent",
        "调度理由：" + str(orchestration.get("reason", "")).strip()
        + "\n\n分配：\n"
        + json.dumps(assignments, ensure_ascii=False, indent=2),
    )]
    tool_evidence: list[str] = []

    multimodal_task = str(assignments.get("multimodal", "") or "").strip()
    if multimodal_task and image_paths and vision_backend is not None:
        if event_callback is not None:
            event_callback("subagent_start", {"role": "Multimodal Agent", "assignment": multimodal_task})
        output, evidence_part = _run_role(
            role="multimodal",
            role_prompt=system_prompt + "\n\n" + MULTIMODAL_PROMPT,
            original_task=task,
            assigned_task=multimodal_task,
            backend=vision_backend,
            registry=_registry_subset(registry, MULTIMODAL_TOOLS),
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
            image_paths=image_paths,
        )
        if event_callback is not None:
            event_callback("subagent_done", {"role": "Multimodal Agent"})
        role_outputs.append((
            "Multimodal Agent",
            output,
        ))
        if evidence_part:
            tool_evidence.append(evidence_part)
    elif multimodal_task and image_paths:
        if event_callback is not None:
            event_callback("subagent_done", {
                "role": "Multimodal Agent",
                "skipped": True,
            })
        role_outputs.append((
            "Multimodal Agent",
            "未运行：本次任务包含图像输入，但视觉后端不可用；未把图像转交给纯文本后端。",
        ))
    elif multimodal_task:
        if event_callback is not None:
            event_callback("subagent_start", {"role": "Multimodal Agent", "assignment": multimodal_task})
        output, evidence_part = _run_role(
            role="multimodal",
            role_prompt=system_prompt + "\n\n" + MULTIMODAL_PROMPT,
            original_task=task,
            assigned_task=multimodal_task,
            backend=backend,
            registry=_registry_subset(registry, MULTIMODAL_TOOLS),
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
        )
        if event_callback is not None:
            event_callback("subagent_done", {"role": "Multimodal Agent"})
        role_outputs.append((
            "Multimodal Agent",
            output,
        ))
        if evidence_part:
            tool_evidence.append(evidence_part)

    research_task = str(assignments.get("research", "") or "").strip()
    if research_task:
        if event_callback is not None:
            event_callback("subagent_start", {"role": "Research Agent", "assignment": research_task})
        output, evidence_part = _run_role(
            role="research",
            role_prompt=system_prompt + "\n\n" + RESEARCH_PROMPT,
            original_task=task,
            assigned_task=research_task,
            backend=backend,
            registry=_registry_subset(registry, RESEARCH_TOOLS),
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
        )
        if event_callback is not None:
            event_callback("subagent_done", {"role": "Research Agent"})
        role_outputs.append((
            "Research Agent",
            output,
        ))
        if evidence_part:
            tool_evidence.append(evidence_part)

    engineering_task = str(assignments.get("engineering", "") or "").strip()
    if engineering_task:
        if event_callback is not None:
            event_callback("subagent_start", {"role": "Engineering Agent", "assignment": engineering_task})
        output, evidence_part = _run_role(
            role="engineering",
            role_prompt=system_prompt + "\n\n" + ENGINEERING_PROMPT,
            original_task=task,
            assigned_task=engineering_task,
            backend=backend,
            registry=registry,
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
        )
        if event_callback is not None:
            event_callback("subagent_done", {"role": "Engineering Agent"})
        role_outputs.append((
            "Engineering Agent",
            output,
        ))
        if evidence_part:
            tool_evidence.append(evidence_part)

    if len(role_outputs) == 1:
        main_task = _main_agent_task(task, orchestration["main_task"] or task)
        if event_callback is not None:
            event_callback("main_agent_start", {"task": main_task})
        answer = _run_main_agent(
            task=main_task,
            backend=backend,
            registry=registry,
            system_prompt=system_prompt,
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
        )
        if event_callback is not None:
            event_callback("main_agent_done", {})
        return answer

    evidence = "\n\n".join(f"## {role}\n{content}" for role, content in role_outputs)
    if event_callback is not None:
        event_callback("synthesis_start", {})
    answer = _synthesize_answer(backend, task, evidence)
    repair_attempts = 0
    while (
        repair_attempts < 2
        and _has_source_reference(evidence)
        and _is_insufficient_research_answer(task, answer)
    ):
        repair_attempts += 1
        _append_trace_event(
            trace_path,
            parent_run_id,
            "final_blocked",
            reason="insufficient_research_answer",
            phase="synthesis",
            attempt=repair_attempts,
        )
        if event_callback is not None:
            event_callback("research_answer_repair", {"attempt": repair_attempts})
        answer = _repair_research_answer(backend, task, evidence, answer)
    if event_callback is not None:
        event_callback("synthesis_done", {})
    reviewer_evidence = (
        "# 子 agent 工具调用记录\n"
        + ("\n\n".join(tool_evidence) if tool_evidence else "（子 agent 没有产生工具调用记录）")
        + "\n\n# 子 agent 输出\n"
        + evidence
    )
    if event_callback is not None:
        event_callback("review_start", {})
    review = review_answer(backend, task, answer, reviewer_evidence)
    needs_revision = review_needs_revision(review)
    if event_callback is not None:
        event_callback("review_done", {"needs_revision": needs_revision})
    _append_trace_event(
        trace_path,
        parent_run_id,
        "review",
        phase="initial",
        status="needs_revision" if needs_revision else "passed",
        content=review,
        evidence=reviewer_evidence[:5000],
    )
    if needs_revision:
        if event_callback is not None:
            event_callback("revision_start", {})
        answer = _synthesize_answer(
            backend,
            task,
            evidence,
            previous_answer=answer,
            review=review,
        )
        repair_attempts = 0
        while (
            repair_attempts < 2
            and _has_source_reference(evidence)
            and _is_insufficient_research_answer(task, answer)
        ):
            repair_attempts += 1
            _append_trace_event(
                trace_path,
                parent_run_id,
                "final_blocked",
                reason="insufficient_research_answer",
                phase="revision",
                attempt=repair_attempts,
            )
            if event_callback is not None:
                event_callback("research_answer_repair", {"phase": "revision", "attempt": repair_attempts})
            answer = _repair_research_answer(backend, task, evidence, answer)
        if event_callback is not None:
            event_callback("review_start", {"phase": "final"})
        final_review = review_answer(backend, task, answer, reviewer_evidence)
        final_needs_revision = review_needs_revision(final_review)
        if event_callback is not None:
            event_callback("review_done", {
                "phase": "final",
                "needs_revision": final_needs_revision,
            })
        _append_trace_event(
            trace_path,
            parent_run_id,
            "review",
            phase="final",
            status="needs_revision" if final_needs_revision else "passed",
            content=final_review,
            initial_review=review,
            answer_revised=True,
        )
    return answer
