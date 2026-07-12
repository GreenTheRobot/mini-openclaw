"""命令行入口。

用法：
  python -m agent.cli --selfcheck          # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"  # Day5 起：真正跑任务（v1 在 Day6）
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import shutil
import sys
from typing import Any

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT
from agent.permissions import PermissionDecision


def _configure_terminal_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


_configure_terminal_encoding()

try:
    from rich.console import Console
    from rich.markdown import Markdown
except ImportError:  # pragma: no cover - 方便未安装依赖时仍可自检
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
    """模型有时会把整份答案包进 ```markdown 代码块，渲染前剥掉这一层。"""
    match = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", text, flags=re.S | re.I)
    if match:
        return match.group(1)
    return text


def _confirm_tool_call(name: str, arguments: dict[str, Any],
                       decision: PermissionDecision) -> bool:
    if not sys.stdin.isatty():
        _print(f"[权限层] 非交互终端，无法确认 {name}，默认拒绝。")
        return False

    args_text = json.dumps(arguments, ensure_ascii=False, indent=2)
    _print("\n[权限确认]")
    _print(f"工具：{name}")
    _print(f"原因：{decision.reason}")
    _print("参数：")
    _print(args_text)

    prompt = "是否执行？输入 y/yes 确认，其它任意输入拒绝："
    answer = _console.input(prompt) if _console else input(prompt)
    return answer.strip().lower() in {"y", "yes"}


def selfcheck() -> int:
    _print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        _print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}（Day5 起会变多）")
    except Exception as e:  # noqa
        _print(f"[FAIL] 工具注册表：{e}"); ok = False

    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        _print("[ok] FakeBackend 可用（未配 DEEPSEEK_API_KEY 时的离线占位后端）")
    except Exception as e:  # noqa
        _print(f"[FAIL] FakeBackend：{e}"); ok = False

    try:
        from agent.loop import AgentLoop  # noqa
        _print("[ok] 主循环模块可导入（Day5 实现 run 逻辑）")
    except Exception as e:  # noqa
        _print(f"[FAIL] 主循环：{e}"); ok = False

    _print(f"== 自检 {'通过 ✅' if ok else '未通过 ❌'} ==")
    _print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("--image", action="append", default=[],
                   help="随用户消息发送的图片路径；可多次传入")
    p.add_argument("--auto-approve", action="store_true",
                   help="自动放行需要确认的工具调用（适合本地演示，谨慎使用）")
    args = p.parse_args(argv)

    if args.selfcheck or not args.task:
        return selfcheck()

    # 真正跑任务：优先用 DeepSeek API；没配 key 时回退到 FakeBackend（离线打通管道）
    from agent.loop import AgentLoop
    reg = build_default_registry()
    try:
        from mcp.client import MCPClient, register_mcp_tools
        if shutil.which("npx"):
            mcp = MCPClient([
                "npx", "-y", "@modelcontextprotocol/server-filesystem",
                str(Path.cwd()),
            ])
        else:
            mcp = MCPClient(["python", "mcp/calc_server.py"])
        mcp.start()
        register_mcp_tools(reg, mcp)
    except Exception as e:  # noqa
        _print(f"[提示] MCP 未接入（{e}），仅用内置工具。")
    if args.image:
        try:
            from backend.qwen_vision import QwenVisionBackend
            backend = QwenVisionBackend()                 # 需要 QWEN_* 占位配置
        except Exception as e:  # noqa
            from backend.fake_backend import FakeBackend
            _print(f"[提示] 未启用视觉后端（{e}），回退 FakeBackend。配置 QWEN_* 后即用视觉模型。")
            backend = FakeBackend()
    else:
        try:
            from backend.client import DeepSeekBackend
            backend = DeepSeekBackend()                   # 需要 DEEPSEEK_API_KEY
        except Exception as e:  # noqa
            from backend.fake_backend import FakeBackend
            _print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
            backend = FakeBackend()
    system = SYSTEM_PROMPT
    try:
        from skills.loader import (
            load_skills,
            render_skill_bodies,
            select_skills,
            skills_catalog,
        )
        skills = load_skills()
        catalog = skills_catalog(skills)
        if catalog:
            system += "\n\n# 可用 Skills（相关时按其流程执行）\n" + catalog
        selected = select_skills(args.task, skills)
        selected_bodies = render_skill_bodies(selected)
        if selected_bodies:
            system += "\n\n# 本次任务召回的 Skill 细则\n" + selected_bodies
    except Exception as e:  # noqa
        _print(f"[提示] Skills 未加载（{e}），仅使用基础系统提示词。")

    try:
        from agent.memory import Memory
        mem = Memory("MEMORY.md")
        recalled = mem.recall()
        if recalled.strip():
            system += "\n\n# 关于本项目 / 用户的已知记忆（相关时遵循）\n" + recalled
    except Exception as e:
        _print(f"[提示] 记忆系统未加载（{e}）。")

    agent = AgentLoop(
        backend,
        reg,
        system,
        auto_approve=args.auto_approve,
        confirm_callback=_confirm_tool_call,
    )
    _print_markdown(agent.run(args.task, image_paths=args.image))
    return 0


if __name__ == "__main__":
    sys.exit(main())
