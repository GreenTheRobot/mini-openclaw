"""Lightweight multi-agent orchestration built on the existing AgentLoop."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agent.loop import AgentLoop, _is_insufficient_research_answer, _research_answer_repair_prompt
from agent.permissions import PermissionManager
from agent.reviewer import review_answer, review_needs_revision
from agent.sanitize import sanitize_for_json
from agent.todo_context import todo_path
from eval.tracer import Tracer
from tools.base import Tool, ToolRegistry, ToolResult

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

ORCHESTRATION_PROMPT += """

Dispatch model: role is only a subagent attribute used to choose that subagent's prompt.
Do not group work by role. Dispatch a flat list of concrete subagent tasks.
Preferred JSON shape:
{
  "use_subagents": true,
  "reason": "why this needs parallel subagents",
  "main_task": "",
  "subagents": [
    {"id": "paper-context", "role": "research", "task": "collect paper metadata and source context"},
    {"id": "figure-1", "role": "multimodal", "task": "analyze only marker-001.jpeg"},
    {"id": "figure-2", "role": "multimodal", "task": "analyze only marker-002.jpeg"}
  ]
}
All items in subagents run in parallel. After their results return, the main agent receives those results
and may dispatch another parallel wave if more work is needed.

Compatibility rule: assignments may still be one string or an array of strings per role.
Example:
{
  "assignments": {
    "research": ["collect recent papers with source links", "read local docs and extract constraints"],
    "engineering": ["inspect the implementation path", "run the focused regression tests"],
    "multimodal": ""
  }
}
Prefer multiple specific subagents over one broad subagent when the work can be split safely.
"""

RESEARCH_PROMPT = """你是 Research Agent，负责查论文、读论文、网页调研和证据整理。
优先使用 arxiv_search、web_search、web_fetch、download_file、pdf_extract_text、paper_figure_analyze。
输出必须区分已验证事实、合理推断和缺口，并保留来源链接或文件路径。"""

ENGINEERING_PROMPT = """你是 Engineering Agent，负责执行型、集成型和验证型工作，包括代码阅读、文件修改、实验准备、运行验证、外部通知、调度、记忆写入和可复现记录。
你拥有完整工具集；必须优先使用已经注册的精确工具名，不要在存在专用工具时改用 bash 绕行。不要猜测代码内容；修改、发送、调度或实验后必须说明验证结果。
当用户要求运行实验、训练、测试或对比配置时，必须实际执行对应命令或 experiment_* 工具，并在输出中列出命令、returncode/状态、日志路径或关键 stdout；不得只根据代码逻辑推断结果。"""

WORKDIR_CONTEXT_TEMPLATE = """当前工作目录是：
{workdir}

所有相对路径都基于这个目录。不要假设存在 `/workspace`、`/app` 或其他容器路径；用户提到的 `demo_project/` 对应当前工作目录下的 `demo_project/`。如果路径不确定，先用 `pwd`、`glob` 或 `ls` 在当前工作目录内核验，再运行命令。"""

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

FINAL_DELIVERY_PROMPT = SYNTHESIS_PROMPT + """

你是最终答复作者。你会收到一版答案和内部质量意见；这些意见只能用来约束最终答复，不能成为最终答复的主题。
请直接回答用户的原始任务，输出用户期望的交付物形态：例如实验任务就给出配置、命令、结果表、对比结论、失败项或缺口。
不得输出“当前还不能把这次任务报告为已完成”“最终质量审查仍要求修订”“审查结论”等内部流程措辞。
如果证据不足以给出某个结果，简洁说明该结果没有可核验数据，并列出已有证据和下一步需要实际执行的命令；不要编造结果，也不要用内部审查报告替代任务答案。"""

RESEARCH_MARKERS = {
    "论文", "文献", "paper", "arxiv", "github", "网页", "调研", "前沿", "动态",
    "方法", "综述", "阅读", "多模态", "模型压缩", "token", "cache",
}
ENGINEERING_MARKERS = {
    "代码", "仓库", "实现", "修复", "测试", "实验", "运行", "报错", "debug",
    "配置", "训练", "复现", "指标", "脚本", "benchmark",
}

ROLE_MAX_TURNS = 20
MAIN_AGENT_MAX_TURNS = 40
SUBAGENT_DISPATCH_TOOL = "subagent_dispatch"
SUBAGENT_DISPATCH_MAX_DEPTH = 3
SUBAGENT_DISPATCH_MAX_ITEMS = 8
_SUBAGENT_TOOL_CONTEXT = threading.local()
SUBAGENT_TOOL_PROMPT = """
You have a tool named `subagent_dispatch` for parallel delegation.
Use it whenever a task can be split into independent parts, including multiple local images,
multiple paper sections, multiple files, or multiple implementation checks.
Dispatch a flat `subagents` list; `role` is only a prompt/capability tag and must not be used
as a bucket. For local image or figure paths, dispatch one `multimodal` subagent per image/path
instead of calling `paper_figure_analyze` repeatedly in the same agent.
After `subagent_dispatch` returns, synthesize the returned results and decide whether another
parallel wave is needed.
"""


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "agent"


def _active_assignment(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    noop_markers = (
        "不需要", "无需", "不用", "没有", "无", "none", "n/a", "na",
        "not needed", "no need", "skip", "omit",
        "涓嶉渶瑕", "鏃犻渶", "涓嶇敤",
    )
    return "" if lowered in noop_markers else text


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


def _workdir_context(workdir: Path) -> str:
    return WORKDIR_CONTEXT_TEMPLATE.format(workdir=workdir.as_posix())


def _normalize_assignment(value: Any) -> str:
    text = str(value or "").strip()
    if text.strip("。.!！ ").lower() in NO_ASSIGNMENT_VALUES:
        return ""
    return _active_assignment(text)


def _assignment_items(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = [value]
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _normalize_assignment(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _assignment_value(value: Any) -> str | list[str]:
    items = _assignment_items(value)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return items


def _normalize_role(value: Any) -> str:
    role = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "research-agent": "research",
        "researcher": "research",
        "engineer": "engineering",
        "engineering-agent": "engineering",
        "multimodal-agent": "multimodal",
        "vision": "multimodal",
        "visual": "multimodal",
    }
    role = aliases.get(role, role)
    return role if role in {"research", "engineering", "multimodal"} else "research"


def _role_display_base(role: str) -> str:
    return {
        "research": "Research Agent",
        "engineering": "Engineering Agent",
        "multimodal": "Multimodal Agent",
    }.get(role, "Subagent")


def _normalize_subagent_specs(parsed: dict[str, Any], image_paths: list[str]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    raw_subagents = parsed.get("subagents")
    if isinstance(raw_subagents, list):
        for index, item in enumerate(raw_subagents, start=1):
            if isinstance(item, dict):
                role = _normalize_role(item.get("role", "research"))
                task = _normalize_assignment(item.get("task") or item.get("assignment") or item.get("work") or "")
                raw_id = str(item.get("id") or item.get("name") or f"{role}-{index}").strip()
            else:
                role = "research"
                task = _normalize_assignment(item)
                raw_id = f"{role}-{index}"
            if not task:
                continue
            specs.append({
                "id": _safe_name(raw_id),
                "role": role,
                "task": task,
                "explicit_id": "1",
            })
    if specs:
        return specs

    assignments = parsed.get("assignments")
    if not isinstance(assignments, dict):
        assignments = {}
    for role in ("multimodal", "research", "engineering"):
        tasks = _assignment_items(assignments.get(role, ""))
        total = len(tasks)
        for suffix, task in enumerate(tasks, start=1):
            specs.append({
                "id": _safe_name(_role_instance_name(role, suffix, total)),
                "role": role,
                "task": task,
            })
    return specs


def _legacy_assignments_from_specs(specs: list[dict[str, str]]) -> dict[str, str | list[str]]:
    grouped: dict[str, list[str]] = {"research": [], "engineering": [], "multimodal": []}
    for spec in specs:
        role = spec.get("role", "research")
        if role in grouped:
            grouped[role].append(spec.get("task", ""))
    return {role: (tasks[0] if len(tasks) == 1 else tasks) if tasks else "" for role, tasks in grouped.items()}


def _runtime_hint(system_prompt: str) -> str:
    match = RUNTIME_HINT_RE.search(system_prompt)
    return match.group(1).strip() if match else ""


def _orchestration_plan(
    backend: Any,
    task: str,
    image_paths: list[str],
    system_context: str = "",
    workdir: Path | None = None,
) -> dict[str, Any]:
    if workdir is not None:
        system_context = (system_context.rstrip() + "\n\n" + _workdir_context(workdir)).strip()
    runtime_hint = _runtime_hint(system_context)
    response = backend.chat([
        {"role": "system", "content": ORCHESTRATION_PROMPT},
        {"role": "user", "content": (
            f"原始任务：\n{task}\n\n"
            + (f"运行时日期约束：\n{runtime_hint}\n\n" if runtime_hint else "")
            + f"是否包含直接图像输入：{'是' if image_paths else '否'}\n\n"
            + f"当前系统上下文：\n{system_context[-2000:]}"
        )},
    ], tools=[])
    parsed = _json_object(str(response.get("content", "")))
    fallback = _fallback_orchestration(task, image_paths)
    if not parsed:
        parsed = fallback
    specs = _normalize_subagent_specs(parsed, image_paths)
    normalized_assignments = _legacy_assignments_from_specs(specs)
    use_subagents = bool(parsed.get("use_subagents", fallback["use_subagents"]))
    if not specs:
        use_subagents = False
    return {
        "use_subagents": use_subagents,
        "reason": str(parsed.get("reason", "") or fallback["reason"]).strip(),
        "main_task": str(parsed.get("main_task", "") or task).strip(),
        "subagents": specs,
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


def _locked_event_callback(
    event_callback: Callable[[str, dict[str, Any]], None] | None,
) -> Callable[[str, dict[str, Any]], None] | None:
    if event_callback is None:
        return None
    lock = threading.Lock()

    def callback(event: str, payload: dict[str, Any]) -> None:
        with lock:
            event_callback(event, payload)

    return callback


def _locked_confirm_callback(confirm_callback: Callable[..., Any] | None) -> Callable[..., Any] | None:
    if confirm_callback is None:
        return None
    lock = threading.Lock()

    def callback(*args: Any, **kwargs: Any) -> Any:
        with lock:
            return confirm_callback(*args, **kwargs)

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
        max_turns=ROLE_MAX_TURNS,
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
        f"{_workdir_context(workdir)}\n\n"
        f"主 Agent 分配给你的具体工作：\n{assigned_task}\n\n"
        f"请只完成 {role} 职责范围内的工作，并把证据、产物路径、失败原因和未完成项写清楚。"
    )
    previous_depth = getattr(_SUBAGENT_TOOL_CONTEXT, "depth", 0)
    previous_prefix = getattr(_SUBAGENT_TOOL_CONTEXT, "prefix", "")
    _SUBAGENT_TOOL_CONTEXT.depth = previous_depth + 1 if previous_depth else 1
    _SUBAGENT_TOOL_CONTEXT.prefix = f"{previous_prefix}.{role}" if previous_prefix else role
    try:
        with todo_path(_agent_todo_path(parent_run_id, role)):
            answer = loop.run(role_task, image_paths=image_paths)
    finally:
        _SUBAGENT_TOOL_CONTEXT.depth = previous_depth
        _SUBAGENT_TOOL_CONTEXT.prefix = previous_prefix
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
    system_prompt = system_prompt + "\n\n" + SUBAGENT_TOOL_PROMPT
    loop = AgentLoop(
        backend,
        registry,
        system_prompt + "\n\n你是主 Agent。请直接完成当前任务；只有用户可见答案应聚焦任务本身。",
        max_turns=MAIN_AGENT_MAX_TURNS,
        workdir=workdir,
        auto_approve=auto_approve,
        confirm_callback=confirm_callback,
        tracer=tracer,
        event_callback=_role_event_callback(event_callback, "Main Agent"),
        context_budget=context_budget,
        permission_manager=manager,
    )
    with todo_path(_agent_todo_path(parent_run_id, "main")):
        return loop.run(f"{_workdir_context(workdir)}\n\n{task}", image_paths=image_paths)


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


def _final_delivery_answer(
    backend: Any,
    task: str,
    evidence: str,
    previous_answer: str,
    review: str,
) -> str:
    response = backend.chat([
        {"role": "system", "content": FINAL_DELIVERY_PROMPT},
        {"role": "user", "content": (
            f"原始任务：\n{task}\n\n"
            f"子 agent 输出和工具证据：\n{evidence}\n\n"
            f"上一版答案：\n{previous_answer}\n\n"
            f"内部质量意见（只用于修正最终答复，不得作为最终答复主题）：\n{review}"
        )},
    ], tools=[])
    return str(response.get("content", "")).strip() or previous_answer or evidence


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
            + "\n\n请严格依据下面的子 agent 证据重写；保留证据中的 URL、arXiv ID 和关键结论，"
            + "不要添加证据中不存在的论文、链接或事实。只输出面向用户的最终答案。\n\n"
            f"子 agent 证据：\n{evidence}"
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


def _role_instance_name(role: str, index: int, total: int) -> str:
    return role if total == 1 else f"{role}-{index}"


def _display_role_name(display_role: str, index: int, total: int) -> str:
    return display_role if total == 1 else f"{display_role} {index}"


def _role_prompt(system_prompt: str, role: str) -> str:
    role_prompt = {
        "research": RESEARCH_PROMPT,
        "engineering": ENGINEERING_PROMPT,
        "multimodal": MULTIMODAL_PROMPT,
    }.get(role, RESEARCH_PROMPT)
    return system_prompt + "\n\n" + SUBAGENT_TOOL_PROMPT + "\n\n" + role_prompt


def _dispatch_subagent_specs(
    *,
    subagent_specs: list[dict[str, Any]],
    task: str,
    backend: Any,
    vision_backend: Any | None,
    registry: ToolRegistry,
    system_prompt: str,
    workdir: Path,
    trace_path: Path,
    parent_run_id: str,
    image_paths: list[str],
    permission_mode: str,
    auto_approve: bool,
    confirm_callback: Callable[..., Any] | None,
    context_budget: int,
    event_callback: Callable[[str, dict[str, Any]], None] | None,
    prefix_ids: bool = False,
) -> list[dict[str, Any]]:
    subagent_jobs: list[dict[str, Any]] = []
    role_totals: dict[str, int] = {}
    role_seen: dict[str, int] = {}
    id_prefix = str(getattr(_SUBAGENT_TOOL_CONTEXT, "prefix", "") or "") if prefix_ids else ""

    for spec in subagent_specs:
        role = _normalize_role(spec.get("role", "research"))
        role_totals[role] = role_totals.get(role, 0) + 1
    for spec in subagent_specs:
        role = _normalize_role(spec.get("role", "research"))
        role_seen[role] = role_seen.get(role, 0) + 1
        role_index = role_seen[role]
        role_total = role_totals.get(role, 1)
        assigned_task = _normalize_assignment(spec.get("task", ""))
        if not assigned_task:
            continue
        raw_id = str(spec.get("id") or _role_instance_name(role, role_index, role_total))
        spec_id = _safe_name(f"{id_prefix}.{raw_id}" if id_prefix else raw_id)
        display_role = str(spec.get("display_role") or _display_role_name(_role_display_base(role), role_index, role_total))
        include_subagent_id = bool(spec.get("explicit_id", "1"))

        if role == "multimodal" and image_paths and vision_backend is None:
            if event_callback is not None:
                payload = {"role": display_role, "subagent_id": spec_id, "skipped": True}
                event_callback("subagent_done", payload)
            subagent_jobs.append({
                "skipped": True,
                "display_role": display_role,
                "subagent_id": spec_id,
                "role": role,
                "output": "未运行：本次任务包含直接图像输入，但视觉后端不可用；未把图像转交给纯文本后端。",
                "tool_evidence": "",
            })
            continue

        subagent_jobs.append({
            "display_role": display_role,
            "subagent_id": spec_id,
            "include_subagent_id": include_subagent_id,
            "role": role,
            "run_kwargs": {
                "role": spec_id,
                "role_prompt": _role_prompt(system_prompt, role),
                "original_task": task,
                "assigned_task": assigned_task,
                "backend": vision_backend if role == "multimodal" and image_paths and vision_backend is not None else backend,
                "registry": registry,
                "workdir": workdir,
                "trace_path": trace_path,
                "parent_run_id": parent_run_id,
                "permission_mode": permission_mode,
                "auto_approve": auto_approve,
                "confirm_callback": confirm_callback,
                "context_budget": context_budget,
                "event_callback": event_callback,
                "image_paths": image_paths if role == "multimodal" and image_paths and vision_backend is not None else None,
            },
        })

    results: dict[int, dict[str, Any]] = {}
    runnable_jobs = [job for job in subagent_jobs if not job.get("skipped")]
    for index, job in enumerate(subagent_jobs):
        if job.get("skipped"):
            results[index] = {
                "id": job["subagent_id"],
                "role": job["role"],
                "display_role": job["display_role"],
                "status": "skipped",
                "answer": job["output"],
                "tool_evidence": "",
            }
    if not runnable_jobs:
        return [results[index] for index in sorted(results)]

    with ThreadPoolExecutor(max_workers=len(runnable_jobs), thread_name_prefix="mini-openclaw-subagent") as executor:
        futures = {}
        for index, job in enumerate(subagent_jobs):
            if job.get("skipped"):
                continue
            display_role = str(job["display_role"])
            subagent_id = str(job["subagent_id"])
            include_subagent_id = bool(job.get("include_subagent_id"))
            run_kwargs = dict(job["run_kwargs"])
            if event_callback is not None:
                payload = {
                    "role": display_role,
                    "assignment": run_kwargs["assigned_task"],
                }
                if include_subagent_id:
                    payload["subagent_id"] = subagent_id
                event_callback("subagent_start", payload)
            future = executor.submit(_run_role, **run_kwargs)
            futures[future] = (index, display_role, subagent_id, str(job["role"]), include_subagent_id)
        for future in as_completed(futures):
            index, display_role, subagent_id, role, include_subagent_id = futures[future]
            try:
                output, evidence_part = future.result()
            except Exception as exc:
                if event_callback is not None:
                    payload = {
                        "role": display_role,
                        "error": repr(exc),
                    }
                    if include_subagent_id:
                        payload["subagent_id"] = subagent_id
                    event_callback("subagent_done", payload)
                raise
            if event_callback is not None:
                payload = {"role": display_role}
                if include_subagent_id:
                    payload["subagent_id"] = subagent_id
                event_callback("subagent_done", payload)
            results[index] = {
                "id": subagent_id,
                "role": role,
                "display_role": display_role,
                "status": "ok",
                "answer": output,
                "tool_evidence": evidence_part,
            }
    return [results[index] for index in sorted(results)]


def _subagent_dispatch_tool(
    *,
    task: str,
    backend: Any,
    vision_backend: Any | None,
    registry: ToolRegistry,
    system_prompt: str,
    workdir: Path,
    trace_path: Path,
    parent_run_id: str,
    image_paths: list[str],
    permission_mode: str,
    auto_approve: bool,
    confirm_callback: Callable[..., Any] | None,
    context_budget: int,
    event_callback: Callable[[str, dict[str, Any]], None] | None,
) -> Tool:
    def run(subagents: list[dict[str, Any]], reason: str = "") -> ToolResult:
        current_depth = int(getattr(_SUBAGENT_TOOL_CONTEXT, "depth", 0) or 0)
        if current_depth >= SUBAGENT_DISPATCH_MAX_DEPTH:
            return ToolResult(
                f"subagent_dispatch depth limit reached: {current_depth}",
                success=False,
                category="subagent_depth_limit",
            )
        if not isinstance(subagents, list) or not subagents:
            return ToolResult("subagent_dispatch requires a non-empty subagents list", False, "invalid_arguments")
        if len(subagents) > SUBAGENT_DISPATCH_MAX_ITEMS:
            return ToolResult(
                f"subagent_dispatch supports at most {SUBAGENT_DISPATCH_MAX_ITEMS} subagents per call",
                success=False,
                category="too_many_subagents",
            )
        specs = _normalize_subagent_specs({"subagents": subagents}, image_paths)
        if not specs:
            return ToolResult("subagent_dispatch found no runnable subagent tasks", False, "invalid_arguments")
        _append_trace_event(
            trace_path,
            parent_run_id,
            "subagent_dispatch",
            depth=current_depth,
            reason=reason,
            subagents=specs,
        )
        results = _dispatch_subagent_specs(
            subagent_specs=specs,
            task=task,
            backend=backend,
            vision_backend=vision_backend,
            registry=registry,
            system_prompt=system_prompt,
            workdir=workdir,
            trace_path=trace_path,
            parent_run_id=parent_run_id,
            image_paths=image_paths,
            permission_mode=permission_mode,
            auto_approve=auto_approve,
            confirm_callback=confirm_callback,
            context_budget=context_budget,
            event_callback=event_callback,
            prefix_ids=True,
        )
        payload = {
            "depth": current_depth,
            "reason": reason,
            "results": [
                {
                    "id": item["id"],
                    "role": item["role"],
                    "status": item["status"],
                    "answer": item["answer"],
                    "tool_evidence": item.get("tool_evidence", ""),
                }
                for item in results
            ],
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False, indent=2))

    return Tool(
        name=SUBAGENT_DISPATCH_TOOL,
        description=(
            "Dispatch a flat list of role-tagged subagents in parallel, wait for all results, "
            "and return their answers. Use this for independent evidence collection, local image "
            "or figure analysis, code inspection, experiments, or any task that benefits from parallel work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this parallel dispatch is useful.",
                },
                "subagents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "role": {
                                "type": "string",
                                "enum": ["research", "engineering", "multimodal"],
                            },
                            "task": {"type": "string"},
                        },
                        "required": ["id", "role", "task"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["subagents"],
            "additionalProperties": False,
        },
        run=run,
    )


def _registry_with_subagent_tool(
    *,
    registry: ToolRegistry,
    task: str,
    backend: Any,
    vision_backend: Any | None,
    system_prompt: str,
    workdir: Path,
    trace_path: Path,
    parent_run_id: str,
    image_paths: list[str],
    permission_mode: str,
    auto_approve: bool,
    confirm_callback: Callable[..., Any] | None,
    context_budget: int,
    event_callback: Callable[[str, dict[str, Any]], None] | None,
) -> ToolRegistry:
    runtime_registry = ToolRegistry(dict(registry._tools))
    runtime_registry.remove(SUBAGENT_DISPATCH_TOOL)
    runtime_registry.register(_subagent_dispatch_tool(
        task=task,
        backend=backend,
        vision_backend=vision_backend,
        registry=runtime_registry,
        system_prompt=system_prompt,
        workdir=workdir,
        trace_path=trace_path,
        parent_run_id=parent_run_id,
        image_paths=image_paths,
        permission_mode=permission_mode,
        auto_approve=auto_approve,
        confirm_callback=confirm_callback,
        context_budget=context_budget,
        event_callback=event_callback,
    ))
    return runtime_registry


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
    event_callback = _locked_event_callback(event_callback)
    confirm_callback = _locked_confirm_callback(confirm_callback)
    registry = _registry_with_subagent_tool(
        registry=registry,
        task=task,
        backend=backend,
        vision_backend=vision_backend,
        system_prompt=system_prompt,
        workdir=workdir,
        trace_path=trace_path,
        parent_run_id=parent_run_id,
        image_paths=image_paths,
        permission_mode=permission_mode,
        auto_approve=auto_approve,
        confirm_callback=confirm_callback,
        context_budget=context_budget,
        event_callback=event_callback,
    )
    orchestration = _orchestration_plan(backend, task, image_paths, system_prompt, workdir)
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

    subagent_jobs: list[dict[str, Any]] = []
    subagent_specs = list(orchestration.get("subagents") or [])
    role_totals: dict[str, int] = {}
    role_seen: dict[str, int] = {}
    for spec in subagent_specs:
        role = _normalize_role(spec.get("role", "research"))
        role_totals[role] = role_totals.get(role, 0) + 1
    for position, spec in enumerate(subagent_specs):
        role = _normalize_role(spec.get("role", "research"))
        role_seen[role] = role_seen.get(role, 0) + 1
        role_index = role_seen[role]
        role_total = role_totals.get(role, 1)
        assigned_task = _normalize_assignment(spec.get("task", ""))
        if not assigned_task:
            continue
        spec_id = _safe_name(str(spec.get("id") or _role_instance_name(role, role_index, role_total)))
        display_role = str(spec.get("display_role") or _display_role_name(_role_display_base(role), role_index, role_total))
        if role == "multimodal" and image_paths and vision_backend is None:
            if event_callback is not None:
                event_callback("subagent_done", {
                    "role": display_role,
                    "subagent_id": spec_id,
                    "skipped": True,
                })
            role_outputs.append((
                display_role,
                "未运行：本次任务包含图像输入，但视觉后端不可用；未把图像转交给纯文本后端。",
            ))
            continue
        subagent_jobs.append({
            "display_role": display_role,
            "subagent_id": spec_id,
            "include_subagent_id": bool(spec.get("explicit_id")),
            "run_kwargs": {
                "role": spec_id,
                "role_prompt": _role_prompt(system_prompt, role),
                "original_task": task,
                "assigned_task": assigned_task,
                "backend": vision_backend if role == "multimodal" and image_paths and vision_backend is not None else backend,
                "registry": registry,
                "workdir": workdir,
                "trace_path": trace_path,
                "parent_run_id": parent_run_id,
                "permission_mode": permission_mode,
                "auto_approve": auto_approve,
                "confirm_callback": confirm_callback,
                "context_budget": context_budget,
                "event_callback": event_callback,
                "image_paths": image_paths if role == "multimodal" and image_paths and vision_backend is not None else None,
            },
        })
    if subagent_specs:
        assignments["multimodal"] = ""
        assignments["research"] = ""
        assignments["engineering"] = ""

    multimodal_tasks_for_parallel = _assignment_items(assignments.get("multimodal", ""))
    if multimodal_tasks_for_parallel and image_paths and vision_backend is None:
        total = len(multimodal_tasks_for_parallel)
        for index, _assigned_task in enumerate(multimodal_tasks_for_parallel, start=1):
            display_role = _display_role_name("Multimodal Agent", index, total)
            if event_callback is not None:
                event_callback("subagent_done", {
                    "role": display_role,
                    "skipped": True,
                })
            role_outputs.append((
                display_role,
                "未运行：本次任务包含图像输入，但视觉后端不可用；未把图像转交给纯文本后端。",
            ))
    elif multimodal_tasks_for_parallel:
        total = len(multimodal_tasks_for_parallel)
        for index, assigned_task in enumerate(multimodal_tasks_for_parallel, start=1):
            subagent_jobs.append({
                "display_role": _display_role_name("Multimodal Agent", index, total),
                "run_kwargs": {
                    "role": _role_instance_name("multimodal", index, total),
                    "role_prompt": system_prompt + "\n\n" + MULTIMODAL_PROMPT,
                    "original_task": task,
                    "assigned_task": assigned_task,
                    "backend": vision_backend if image_paths and vision_backend is not None else backend,
                    "registry": registry,
                    "workdir": workdir,
                    "trace_path": trace_path,
                    "parent_run_id": parent_run_id,
                    "permission_mode": permission_mode,
                    "auto_approve": auto_approve,
                    "confirm_callback": confirm_callback,
                    "context_budget": context_budget,
                    "event_callback": event_callback,
                    "image_paths": image_paths if image_paths and vision_backend is not None else None,
                },
            })

    research_tasks_for_parallel = _assignment_items(assignments.get("research", ""))
    total = len(research_tasks_for_parallel)
    for index, assigned_task in enumerate(research_tasks_for_parallel, start=1):
        subagent_jobs.append({
            "display_role": _display_role_name("Research Agent", index, total),
            "run_kwargs": {
                "role": _role_instance_name("research", index, total),
                "role_prompt": system_prompt + "\n\n" + RESEARCH_PROMPT,
                "original_task": task,
                "assigned_task": assigned_task,
                "backend": backend,
                "registry": registry,
                "workdir": workdir,
                "trace_path": trace_path,
                "parent_run_id": parent_run_id,
                "permission_mode": permission_mode,
                "auto_approve": auto_approve,
                "confirm_callback": confirm_callback,
                "context_budget": context_budget,
                "event_callback": event_callback,
            },
        })

    engineering_tasks_for_parallel = _assignment_items(assignments.get("engineering", ""))
    total = len(engineering_tasks_for_parallel)
    for index, assigned_task in enumerate(engineering_tasks_for_parallel, start=1):
        subagent_jobs.append({
            "display_role": _display_role_name("Engineering Agent", index, total),
            "run_kwargs": {
                "role": _role_instance_name("engineering", index, total),
                "role_prompt": system_prompt + "\n\n" + ENGINEERING_PROMPT,
                "original_task": task,
                "assigned_task": assigned_task,
                "backend": backend,
                "registry": registry,
                "workdir": workdir,
                "trace_path": trace_path,
                "parent_run_id": parent_run_id,
                "permission_mode": permission_mode,
                "auto_approve": auto_approve,
                "confirm_callback": confirm_callback,
                "context_budget": context_budget,
                "event_callback": event_callback,
            },
        })

    if subagent_jobs:
        results: dict[int, tuple[str, str, str]] = {}
        with ThreadPoolExecutor(max_workers=len(subagent_jobs), thread_name_prefix="mini-openclaw-subagent") as executor:
            futures = {}
            for index, job in enumerate(subagent_jobs):
                display_role = str(job["display_role"])
                subagent_id = str(job.get("subagent_id") or _safe_name(display_role))
                include_subagent_id = bool(job.get("include_subagent_id"))
                run_kwargs = dict(job["run_kwargs"])
                if event_callback is not None:
                    payload = {
                        "role": display_role,
                        "assignment": run_kwargs["assigned_task"],
                    }
                    if include_subagent_id:
                        payload["subagent_id"] = subagent_id
                    event_callback("subagent_start", payload)
                future = executor.submit(_run_role, **run_kwargs)
                futures[future] = (index, display_role, subagent_id, include_subagent_id)
            for future in as_completed(futures):
                index, display_role, subagent_id, include_subagent_id = futures[future]
                try:
                    output, evidence_part = future.result()
                except Exception as exc:
                    if event_callback is not None:
                        payload = {
                            "role": display_role,
                            "error": repr(exc),
                        }
                        if include_subagent_id:
                            payload["subagent_id"] = subagent_id
                        event_callback("subagent_done", payload)
                    raise
                if event_callback is not None:
                    payload = {"role": display_role}
                    if include_subagent_id:
                        payload["subagent_id"] = subagent_id
                    event_callback("subagent_done", payload)
                results[index] = (display_role, output, evidence_part)
        for index in sorted(results):
            display_role, output, evidence_part = results[index]
            role_outputs.append((display_role, output))
            if evidence_part:
                tool_evidence.append(evidence_part)

    if subagent_jobs or multimodal_tasks_for_parallel:
        assignments["multimodal"] = ""
        assignments["research"] = ""
        assignments["engineering"] = ""

    multimodal_task = _active_assignment(assignments.get("multimodal", ""))
    if multimodal_task and image_paths and vision_backend is not None:
        if event_callback is not None:
            event_callback("subagent_start", {"role": "Multimodal Agent", "assignment": multimodal_task})
        output, evidence_part = _run_role(
            role="multimodal",
            role_prompt=system_prompt + "\n\n" + MULTIMODAL_PROMPT,
            original_task=task,
            assigned_task=multimodal_task,
            backend=vision_backend,
            registry=registry,
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
            event_callback("subagent_done", {"role": "Multimodal Agent"})
        role_outputs.append((
            "Multimodal Agent",
            output,
        ))
        if evidence_part:
            tool_evidence.append(evidence_part)

    research_task = _active_assignment(assignments.get("research", ""))
    if research_task:
        if event_callback is not None:
            event_callback("subagent_start", {"role": "Research Agent", "assignment": research_task})
        output, evidence_part = _run_role(
            role="research",
            role_prompt=system_prompt + "\n\n" + RESEARCH_PROMPT,
            original_task=task,
            assigned_task=research_task,
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
            event_callback("subagent_done", {"role": "Research Agent"})
        role_outputs.append((
            "Research Agent",
            output,
        ))
        if evidence_part:
            tool_evidence.append(evidence_part)

    engineering_task = _active_assignment(assignments.get("engineering", ""))
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
        if final_needs_revision:
            _append_trace_event(
                trace_path,
                parent_run_id,
                "final_delivery_repair",
                reason="final_review_needs_revision",
                review=final_review,
            )
            return _final_delivery_answer(
                backend,
                task,
                evidence,
                previous_answer=answer,
                review=final_review,
            )
    return answer
