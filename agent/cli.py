"""User-facing Claude Code-style command-line interface."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.permissions import (
    ConfirmationResponse,
    PERMISSION_MODES,
    PermissionDecision,
    PermissionManager,
)
from agent.prompts import SYSTEM_PROMPT
from agent.ui import EventRenderer
from tools.base import ToolRegistry, build_default_registry


def _configure_terminal_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


_configure_terminal_encoding()

try:
    from rich.console import Console
    from rich.markdown import Markdown
except ImportError:  # pragma: no cover
    Console = None
    Markdown = None

_console = Console(markup=False) if Console else None


def _print(text: str = "") -> None:
    if _console:
        _console.print(text)
    else:
        print(text)


def _print_markdown(text: str) -> None:
    if _console and Markdown:
        _console.print(Markdown(_unwrap_markdown_fence(text)))
    else:
        print(text)


def _unwrap_markdown_fence(text: str) -> str:
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", text, flags=re.S | re.I)
    return match.group(1) if match else text


def _input(prompt: str) -> str:
    return _console.input(prompt) if _console else input(prompt)


def _confirm_tool_call(
    name: str,
    arguments: dict[str, Any],
    decision: PermissionDecision,
) -> ConfirmationResponse:
    if not sys.stdin.isatty():
        _print(f"[权限层] 非交互终端，无法确认 {name}，默认拒绝。")
        return ConfirmationResponse(False)
    _print("\n[权限确认]")
    _print(f"工具：{name}")
    _print(f"原因：{decision.reason}")
    _print("参数：")
    _print(json.dumps(arguments, ensure_ascii=False, indent=2))
    if decision.grant_scopes:
        _print("[1/y] 仅这一次  [2] 当前任务  [3] 当前会话  [4/n] 拒绝")
        value = _input("是否执行？请选择：").strip().lower()
        mapping = {
            "1": ConfirmationResponse(True, "once"),
            "y": ConfirmationResponse(True, "once"),
            "yes": ConfirmationResponse(True, "once"),
            "2": ConfirmationResponse(True, "task"),
            "task": ConfirmationResponse(True, "task"),
            "3": ConfirmationResponse(True, "session"),
            "session": ConfirmationResponse(True, "session"),
        }
        response = mapping.get(value, ConfirmationResponse(False))
        if response.scope not in decision.grant_scopes and response.scope != "once":
            return ConfirmationResponse(False)
        return response
    value = _input("是否执行？输入 y/yes 确认，其它任意输入拒绝：").strip().lower()
    return ConfirmationResponse(value in {"y", "yes", "1"})


def selfcheck() -> int:
    _print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        registry = build_default_registry()
        _print(f"[ok] 工具注册表：{len(registry)} 个内置工具")
    except Exception as exc:
        _print(f"[FAIL] 工具注册表：{exc}")
        ok = False
    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        _print("[ok] FakeBackend 可用")
    except Exception as exc:
        _print(f"[FAIL] FakeBackend：{exc}")
        ok = False
    try:
        from agent.loop import AgentLoop  # noqa: F401
        from agent.memory import KVMemory, Memory  # noqa: F401
        from eval.tracer import Tracer  # noqa: F401
        from mcp.client import MCPClient  # noqa: F401
        from prompt.render import parse_tool_calls
        from skills.loader import load_skills
        parse_tool_calls('<tool_call>{"name":"read","arguments":{"path":"README.md"}}</tool_call>')
        count = len(load_skills(str(Path(__file__).resolve().parents[1] / "skills")))
        _print(f"[ok] Agent / Memory / Prompt / MCP / Skills({count}) / Trace")
    except Exception as exc:
        _print(f"[FAIL] 扩展架构：{exc}")
        ok = False
    _print(f"== 自检 {'通过 ✅' if ok else '未通过 ❌'} ==")
    return 0 if ok else 1


def _load_skills() -> list[Any]:
    try:
        from skills.loader import load_skills
        return load_skills(str(Path(__file__).resolve().parents[1] / "skills"))
    except Exception as exc:
        _print(f"[提示] Skills 未加载：{exc}")
        return []


def _runtime_context(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    today = current.date()
    week_start = today - timedelta(days=7)
    return (
        "# 运行时日期\n"
        f"当前本地日期为 {today.isoformat()}，时区为 {current.tzinfo}. "
        f"用户说‘最近一周’时，默认使用 {week_start.isoformat()} 至 {today.isoformat()} 的明确范围。\n"
        "处理最新/近期论文时必须核验论文页面的发布日期或最近更新日期；"
        "不得用旧年份搜索结果冒充近期结果。"
    )

def _prepare_turn_context(agent: Any, task: str, skills: list[Any], planning: bool) -> None:
    if planning:
        from agent.planner import planning_guidance
        guidance = planning_guidance(task)
        if guidance:
            agent.add_context("planner-guidance", guidance)
    if skills:
        from skills.loader import render_skill_bodies, select_skills
        for skill in select_skills(task, skills):
            agent.add_context(f"skill:{skill.name}", render_skill_bodies([skill]))


def _audit(backend: Any, tracer: Any, task: str, answer: str, evidence: str) -> None:
    from agent.reviewer import review_answer
    review = review_answer(backend, task, answer, evidence)
    tracer.log_event("review", trigger="manual", content=review)
    _print_markdown("\n# 审查结果\n\n" + review)


def _show_help() -> None:
    _print_markdown("""
# 交互命令

- `/help`：显示帮助
- `/tools`：列出当前工具
- `/mode [plan|default|accept-edits|auto-safe]`：查看或切换权限模式
- `/permissions [clear]`：查看或清空临时授权
- `/steps`：查看上一轮可观察工具步骤
- `/verbose on|off`：切换简洁/详细过程显示
- `/audit`：按需审查上一轮回答
- `/tasks`：查看任务清单
- `/memory`：查看项目长期记忆
- `/trace`：汇总当前会话 Trace
- `/status`：查看后端、权限、显示模式和会话状态
- `/history`：查看当前对话消息摘要
- `/clear` 或 `/new`：清空对话上下文和临时授权
- `/exit` 或 `/quit`：退出

直接输入自然语言即可开始或继续任务。
""")


def _show_intro(
    registry: ToolRegistry,
    skills: list[Any],
    trace_path: Path,
    permission_manager: PermissionManager,
    renderer: EventRenderer,
) -> None:
    output = "详细" if renderer.verbose else "简洁"
    _print("\nmini-OpenClaw 科研智能体")
    _print("直接输入自然语言描述目标；我可以阅读项目、检索资料、修改文件和运行实验。")
    _print("联网、写入和执行命令会按照当前权限模式自动处理或请求确认。")
    _print("常用：/help 帮助 · /steps 上轮过程 · /audit 审查回答 · /exit 退出")
    _print(f"当前：权限 {permission_manager.mode} · 输出 {output}（/verbose on 查看细节）")
    _print(f"工作目录：{Path.cwd()}")
    _print(f"工具：{len(registry)} 个 · Skills：{len(skills)} 个 · Trace：{trace_path}\n")


def _show_modes(current: str) -> None:
    _print_markdown(f"""
# 权限模式

- `plan`：只读分析和规划，禁止写入、下载、bash 和实验执行
- `default`：读操作自动允许；网络按域名授权；写入和执行逐次确认
- `accept-edits`：工作目录内 write/edit 自动允许，其余敏感操作仍确认
- `auto-safe`：白名单网络读取、受控 PDF 下载和工作区编辑自动允许；bash、实验执行和外部发送仍确认

当前模式：`{current}`
""")


def _interactive(
    agent: Any,
    backend: Any,
    registry: ToolRegistry,
    tracer: Any,
    trace_path: Path,
    skills: list[Any],
    planning_enabled: bool,
    renderer: EventRenderer,
    legacy_review_requested: bool = False,
) -> int:
    manager = agent.permission_manager
    _show_intro(registry, skills, trace_path, manager, renderer)
    if legacy_review_requested:
        _print("[提示] 交互模式不再自动追加 Reviewer；需要时输入 /audit。")
    last_task = ""
    last_answer = ""

    while True:
        try:
            task = _input("mini-openclaw> ").strip()
        except EOFError:
            _print("\n会话结束。")
            return 0
        except KeyboardInterrupt:
            _print("\n已取消当前输入；再次输入任务或 /exit。")
            continue
        if not task:
            continue
        command, _, argument = task.partition(" ")
        command = command.lower()

        if command in {"/exit", "/quit"}:
            _print("会话结束。")
            return 0
        if command == "/help":
            _show_help()
            continue
        if command == "/tools":
            _print("\n".join(f"- {name}" for name in registry.names()))
            continue
        if command == "/mode":
            value = argument.strip().lower()
            if not value:
                _show_modes(manager.mode)
            elif value not in PERMISSION_MODES:
                _print(f"未知权限模式：{value}；可选：{', '.join(PERMISSION_MODES)}")
            else:
                manager.set_mode(value)
                manager.reset_session()
                agent.add_context(
                    f"permission-mode:{value}:{time.time_ns()}",
                    f"# 当前权限模式\n当前为 {value} 模式；工具被权限层拒绝时不得尝试绕过。",
                )
                _print(f"权限模式已切换为 {value}；原有临时授权已清空。")
            continue
        if command == "/permissions":
            if argument.strip().lower() == "clear":
                manager.reset_session()
                _print("任务级和会话级临时授权已清空。")
            else:
                _print(json.dumps(manager.snapshot(), ensure_ascii=False, indent=2))
            continue
        if command == "/verbose":
            value = argument.strip().lower()
            if value not in {"on", "off"}:
                _print("用法：/verbose on 或 /verbose off")
            else:
                renderer.set_verbose(value == "on")
                _print(f"详细过程显示已{'开启' if renderer.verbose else '关闭'}。")
            continue
        if command == "/steps":
            _print_markdown(renderer.steps_markdown())
            continue
        if command in {"/audit", "/review"}:
            if command == "/review" and argument.strip().lower() in {"on", "off"}:
                _print("自动 Reviewer 已停用；请在需要时输入 /audit。")
            elif not last_answer:
                _print("还没有可审查的上一轮回答。")
            else:
                try:
                    _audit(backend, tracer, last_task, last_answer, renderer.audit_evidence())
                except Exception as exc:
                    tracer.log_event("review_error", error=str(exc))
                    _print(f"[audit error] {exc}")
            continue
        if command == "/tasks":
            path = Path(".mini-openclaw/tasks.json")
            _print(path.read_text(encoding="utf-8") if path.exists() else "暂无任务清单。")
            continue
        if command == "/memory":
            from agent.memory import Memory
            _print(Memory("MEMORY.md").recall() or "暂无长期记忆。")
            continue
        if command == "/trace":
            from eval.tracer import summarize
            _print(json.dumps(summarize(trace_path), ensure_ascii=False, indent=2))
            continue
        if command == "/history":
            for index, message in enumerate(agent.messages):
                if message.get("role") == "system":
                    continue
                preview = str(message.get("content", "")).replace("\n", " ")[:160]
                _print(f"{index:>3} {message.get('role')}: {preview}")
            continue
        if command == "/status":
            _print(json.dumps({
                "workdir": str(Path.cwd()),
                "backend": type(backend).__name__,
                "permission_mode": manager.mode,
                "output": "verbose" if renderer.verbose else "quiet",
                "messages": len(agent.messages),
                "loaded_contexts": sorted(agent.loaded_contexts),
                "last_run_status": agent.last_run_status,
                "session_grants": len(manager.session_grants),
            }, ensure_ascii=False, indent=2))
            continue
        if command in {"/clear", "/new"}:
            agent.reset()
            renderer.begin_turn()
            last_task = ""
            last_answer = ""
            continue
        if command.startswith("/"):
            _print(f"未知命令：{command}；输入 /help 查看帮助。")
            continue

        _prepare_turn_context(agent, task, skills, planning_enabled)
        renderer.begin_turn()
        try:
            answer = agent.run(task)
            _print_markdown("\n" + answer)
            last_task, last_answer = task, answer
        except KeyboardInterrupt:
            _print("\n当前任务被用户中断，会话仍可继续。")
        except Exception as exc:
            tracer.log_event("interactive_error", error=str(exc))
            _print(f"[error] {exc}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-openclaw")
    parser.add_argument("task", nargs="?", help="单次任务；省略时进入交互会话")
    parser.add_argument("--selfcheck", action="store_true", help="只做系统自检")
    parser.add_argument("--image", action="append", default=[], help="为单次任务附加图片，可重复")
    parser.add_argument("--permission-mode", choices=PERMISSION_MODES, default="default", help="权限模式")
    parser.add_argument("--auto-approve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--trace", help="Trace JSONL 路径；默认写入 traces/")
    parser.add_argument("--no-mcp", action="store_true", help="禁用 MCP")
    parser.add_argument("--ablation", choices=["none", "no-memory", "no-planning", "minimal-prompt"], default="none")
    parser.add_argument("--verbose", action="store_true", help="实时显示每轮模型和工具事件")
    parser.add_argument("--audit", action="store_true", help="单次任务完成后审查最终回答")
    parser.add_argument("--review", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--context-budget", type=int, default=20000, help="触发历史压缩的估算 token 阈值")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.selfcheck:
        return selfcheck()
    if args.auto_approve and args.permission_mode != "default":
        parser.error("--auto-approve 不能和显式 --permission-mode 同时使用")
    if args.auto_approve:
        _print("[警告] --auto-approve 是旧版隔离评测参数；它会自动通过确认，但不能绕过硬拒绝。")

    from agent.loop import AgentLoop
    registry = build_default_registry()
    if not args.no_mcp:
        try:
            from mcp.client import MCPClient, register_mcp_tools
            configured = os.environ.get("OPENCLAW_MCP_COMMAND", "").strip()
            command = (
                shlex.split(configured, posix=(os.name != "nt"))
                if configured
                else ["python", str(Path(__file__).resolve().parents[1] / "mcp/calc_server.py")]
            )
            client = MCPClient(command)
            client.start()
            register_mcp_tools(registry, client)
        except Exception as exc:
            _print(f"[提示] MCP 未接入：{exc}")

    if args.image:
        try:
            from backend.qwen_vision import QwenVisionBackend
            backend = QwenVisionBackend()
        except Exception as exc:
            from backend.fake_backend import FakeBackend
            _print(f"[提示] 视觉后端不可用：{exc}；回退 FakeBackend。")
            backend = FakeBackend()
    else:
        try:
            from backend.client import DeepSeekBackend
            backend = DeepSeekBackend()
        except Exception as exc:
            from backend.fake_backend import FakeBackend
            _print(f"[提示] DeepSeek 后端不可用：{exc}；回退 FakeBackend。")
            backend = FakeBackend()

    if args.ablation == "no-planning":
        registry.remove("todo_write")
        registry.remove("update_todo")
    system = "你是一个命令行助手。完成用户任务。" if args.ablation == "minimal-prompt" else SYSTEM_PROMPT
    if args.ablation != "minimal-prompt":
        from prompt.demonstrations import render_demonstrations
        system += "\n\n" + render_demonstrations()
        system += (
            "\n\n# 安全下载规则\n下载学术 PDF 时必须使用 download_file；"
            "不得使用 bash、curl 或 wget 进行网络下载。网页阅读使用 web_fetch。"
        )
    system += "\n\n" + _runtime_context()
    system += f"\n\n# 当前权限模式\n当前为 {args.permission_mode} 模式；不得绕过权限拒绝。"

    skills = _load_skills()
    if skills:
        from skills.loader import skills_catalog
        system += "\n\n# 可用 Skills（需要时按流程执行）\n" + skills_catalog(skills)
    if args.ablation != "no-memory":
        try:
            from agent.memory import Memory
            recalled = Memory("MEMORY.md").recall()
            if recalled.strip():
                system += "\n\n# 项目长期记忆（相关时遵循）\n" + recalled
        except Exception as exc:
            _print(f"[提示] 记忆未加载：{exc}")

    from eval.tracer import Tracer
    trace_path = Path(args.trace) if args.trace else Path("traces") / time.strftime("session-%Y%m%d-%H%M%S.jsonl")
    tracer = Tracer(trace_path)
    renderer = EventRenderer(_print, verbose=args.verbose)
    manager = PermissionManager(args.permission_mode)
    agent = AgentLoop(
        backend,
        registry,
        system,
        auto_approve=args.auto_approve,
        confirm_callback=_confirm_tool_call,
        tracer=tracer,
        event_callback=renderer,
        context_budget=args.context_budget,
        permission_manager=manager,
    )
    planning_enabled = args.ablation != "no-planning"

    if not args.task:
        return _interactive(
            agent, backend, registry, tracer, trace_path, skills,
            planning_enabled, renderer, legacy_review_requested=args.review,
        )

    _prepare_turn_context(agent, args.task, skills, planning_enabled)
    renderer.begin_turn()
    answer = agent.run(args.task, image_paths=args.image)
    _print_markdown(answer)
    if args.audit or args.review:
        _audit(backend, tracer, args.task, answer, renderer.audit_evidence())
    _print(f"\n[Trace] {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
