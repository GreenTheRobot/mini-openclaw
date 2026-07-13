"""面向用户的 Claude Code 式命令行入口。"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
import shutil
import sys
import time
from typing import Any

from agent.permissions import PermissionDecision
from agent.prompts import SYSTEM_PROMPT
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


def _confirm_tool_call(name: str, arguments: dict[str, Any],
                       decision: PermissionDecision) -> bool:
    if not sys.stdin.isatty():
        _print(f"[权限层] 非交互终端，无法确认 {name}，默认拒绝。")
        return False
    _print("\n[权限确认]")
    _print(f"工具：{name}")
    _print(f"原因：{decision.reason}")
    _print("参数：")
    _print(json.dumps(arguments, ensure_ascii=False, indent=2))
    return _input("是否执行？输入 y/yes 确认，其它任意输入拒绝：").strip().lower() in {"y", "yes"}


def _render_agent_event(event: str, payload: dict[str, Any]) -> None:
    """把主循环事件转换成用户可见的简洁状态，而不是暴露隐藏推理。"""
    if event == "model_start":
        _print(f"\n[model] 第 {payload.get('turn')} 步：正在决定下一步...")
    elif event == "tool_start":
        arguments = json.dumps(payload.get("arguments"), ensure_ascii=False)
        if len(arguments) > 180:
            arguments = arguments[:177] + "..."
        _print(f"\n[tool] {payload.get('name')} {arguments}")
    elif event == "tool_result":
        status = "ok" if payload.get("success") else "error"
        observation = str(payload.get("observation", "")).replace("\n", " ")
        if len(observation) > 220:
            observation = observation[:217] + "..."
        _print(f"[{status}] {payload.get('name')}: {observation}")
    elif event == "context_loaded":
        _print(f"[context] 已加载 {payload.get('key')}")
    elif event == "compaction":
        _print(f"[context] 已压缩历史：约 {payload.get('before')} → {payload.get('after')} tokens")
    elif event == "session_reset":
        _print("[session] 当前对话上下文已清空，磁盘记忆保持不变。")


def selfcheck() -> int:
    _print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        registry = build_default_registry()
        _print(f"[ok] 工具注册表：{len(registry)} 个内置工具")
    except Exception as exc:
        _print(f"[FAIL] 工具注册表：{exc}"); ok = False
    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        _print("[ok] FakeBackend 可用")
    except Exception as exc:
        _print(f"[FAIL] FakeBackend：{exc}"); ok = False
    try:
        from agent.loop import AgentLoop  # noqa
        from agent.memory import KVMemory, Memory  # noqa
        from eval.tracer import Tracer  # noqa
        from mcp.client import MCPClient  # noqa
        from prompt.render import parse_tool_calls
        from skills.loader import load_skills
        parse_tool_calls('<tool_call>{"name":"read","arguments":{"path":"README.md"}}</tool_call>')
        skill_count = len(load_skills(str(Path(__file__).resolve().parents[1] / "skills")))
        _print(f"[ok] Agent / Memory / Prompt / MCP / Skills({skill_count}) / Trace")
    except Exception as exc:
        _print(f"[FAIL] 扩展架构：{exc}"); ok = False
    _print(f"== 自检 {'通过 ✅' if ok else '未通过 ❌'} ==")
    return 0 if ok else 1


def _load_skills() -> list[Any]:
    try:
        from skills.loader import load_skills
        return load_skills(str(Path(__file__).resolve().parents[1] / "skills"))
    except Exception as exc:
        _print(f"[提示] Skills 未加载：{exc}")
        return []


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


def _review(backend: Any, tracer: Any, task: str, answer: str) -> None:
    from agent.reviewer import review_answer
    review = review_answer(backend, task, answer)
    tracer.log_event("review", content=review)
    _print_markdown("\n## Reviewer 审查\n\n" + review)


def _show_help() -> None:
    _print_markdown("""
# 交互命令

- `/help`：显示帮助
- `/tools`：列出当前工具
- `/tasks`：查看持久化任务清单
- `/memory`：查看项目长期记忆
- `/trace`：汇总当前会话 Trace
- `/status`：查看后端、会话状态、消息数和工作目录
- `/history`：查看当前会话的可见消息摘要
- `/review on|off`：开启或关闭 Reviewer
- `/clear` 或 `/new`：清空当前对话上下文
- `/exit` 或 `/quit`：退出

直接输入自然语言即可继续同一个多轮会话。
""")


def _interactive(agent: Any, backend: Any, registry: ToolRegistry, tracer: Any,
                 trace_path: Path, skills: list[Any], review_enabled: bool,
                 planning_enabled: bool) -> int:
    _print("\nmini-OpenClaw 科研智能体")
    _print(f"工作目录：{Path.cwd()}")
    _print(f"工具：{len(registry)} 个  Skills：{len(skills)} 个  Trace：{trace_path}")
    _print("输入 /help 查看命令，输入 /exit 退出。\n")
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
            _show_help(); continue
        if command == "/tools":
            _print("\n".join(f"- {name}" for name in registry.names())); continue
        if command == "/tasks":
            path = Path(".mini-openclaw/tasks.json")
            _print(path.read_text(encoding="utf-8") if path.exists() else "暂无任务清单。"); continue
        if command == "/memory":
            from agent.memory import Memory
            memory = Memory("MEMORY.md").recall()
            _print(memory or "暂无长期记忆。"); continue
        if command == "/trace":
            from eval.tracer import summarize
            _print(json.dumps(summarize(trace_path), ensure_ascii=False, indent=2)); continue
        if command == "/history":
            for index, message in enumerate(agent.messages):
                if message.get("role") == "system":
                    continue
                preview = str(message.get("content", "")).replace("\n", " ")[:160]
                _print(f"{index:>3} {message.get('role')}: {preview}")
            continue
        if command == "/status":
            _print(json.dumps({
                "workdir": str(Path.cwd()), "backend": type(backend).__name__, "messages": len(agent.messages),
                "loaded_contexts": sorted(agent.loaded_contexts),
                "last_run_status": agent.last_run_status,
                "review": review_enabled,
            }, ensure_ascii=False, indent=2)); continue
        if command in {"/clear", "/new"}:
            agent.reset(); continue
        if command == "/review":
            value = argument.strip().lower()
            if value not in {"on", "off"}:
                _print("用法：/review on 或 /review off"); continue
            review_enabled = value == "on"
            _print(f"Reviewer 已{'开启' if review_enabled else '关闭'}。"); continue
        if command.startswith("/"):
            _print(f"未知命令：{command}；输入 /help 查看帮助。")
            continue
        _prepare_turn_context(agent, task, skills, planning_enabled)
        try:
            answer = agent.run(task)
            _print_markdown("\n" + answer)
            if review_enabled:
                _review(backend, tracer, task, answer)
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
    parser.add_argument("--auto-approve", action="store_true", help="自动确认高风险工具，仅用于隔离评测")
    parser.add_argument("--trace", help="Trace JSONL 路径；默认写入 traces/")
    parser.add_argument("--no-mcp", action="store_true", help="禁用 MCP")
    parser.add_argument("--ablation", choices=["none", "no-memory", "no-planning", "minimal-prompt"], default="none")
    parser.add_argument("--review", action="store_true", help="开启 Reviewer")
    parser.add_argument("--context-budget", type=int, default=6000, help="触发历史压缩的估算 token 阈值")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.selfcheck:
        return selfcheck()

    from agent.loop import AgentLoop
    registry = build_default_registry()
    if not args.no_mcp:
        try:
            from mcp.client import MCPClient, register_mcp_tools
            configured_mcp = os.environ.get("OPENCLAW_MCP_COMMAND", "").strip()
            command = (
                shlex.split(configured_mcp, posix=(os.name != "nt"))
                if configured_mcp
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
        registry.remove("task_list")
    system = "你是一个命令行助手。完成用户任务。" if args.ablation == "minimal-prompt" else SYSTEM_PROMPT
    if args.ablation != "minimal-prompt":
        from prompt.demonstrations import render_demonstrations
        system += "\n\n" + render_demonstrations()
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
    agent = AgentLoop(
        backend, registry, system,
        auto_approve=args.auto_approve,
        confirm_callback=_confirm_tool_call,
        tracer=tracer,
        event_callback=_render_agent_event,
        context_budget=args.context_budget,
    )
    planning_enabled = args.ablation != "no-planning"

    if not args.task:
        return _interactive(
            agent, backend, registry, tracer, trace_path, skills,
            args.review, planning_enabled,
        )

    _prepare_turn_context(agent, args.task, skills, planning_enabled)
    answer = agent.run(args.task, image_paths=args.image)
    _print_markdown(answer)
    if args.review:
        _review(backend, tracer, args.task, answer)
    _print(f"\n[Trace] {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())