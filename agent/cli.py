"""命令行入口。

用法：
  python -m agent.cli --selfcheck          # Day1：自检骨架是否装好
  python -m agent.cli "创建 hello.py 并运行"  # Day5 起：真正跑任务（v1 在 Day6）
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import sys

from tools.base import build_default_registry
from agent.prompts import SYSTEM_PROMPT


def selfcheck() -> int:
    print("== mini-OpenClaw 自检 ==")
    ok = True
    try:
        reg = build_default_registry()
        print(f"[ok] 工具注册表加载成功，当前内置工具数：{len(reg)}（Day5 起会变多）")
    except Exception as e:  # noqa
        print(f"[FAIL] 工具注册表：{e}"); ok = False

    try:
        from backend.fake_backend import FakeBackend
        FakeBackend().chat([{"role": "user", "content": "hi"}], tools=[])
        print("[ok] FakeBackend 可用（未配 DEEPSEEK_API_KEY 时的离线占位后端）")
    except Exception as e:  # noqa
        print(f"[FAIL] FakeBackend：{e}"); ok = False

    try:
        from agent.loop import AgentLoop  # noqa
        print("[ok] 主循环模块可导入（Day5 实现 run 逻辑）")
    except Exception as e:  # noqa
        print(f"[FAIL] 主循环：{e}"); ok = False

    print("== 自检", "通过 ✅" if ok else "未通过 ❌", "==")
    print("\n下一步：按 dayNN 的 lab-guide 填 # TODO 标记。")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mini-openclaw")
    p.add_argument("task", nargs="?", help="要让 agent 完成的任务（自然语言）")
    p.add_argument("--selfcheck", action="store_true", help="只做骨架自检")
    p.add_argument("--image", action="append", default=[],
                   help="随用户消息发送的图片路径；可多次传入")
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
        print(f"[提示] MCP 未接入（{e}），仅用内置工具。")
    if args.image:
        try:
            from backend.qwen_vision import QwenVisionBackend
            backend = QwenVisionBackend()                 # 需要 QWEN_* 占位配置
        except Exception as e:  # noqa
            from backend.fake_backend import FakeBackend
            print(f"[提示] 未启用视觉后端（{e}），回退 FakeBackend。配置 QWEN_* 后即用视觉模型。")
            backend = FakeBackend()
    else:
        try:
            from backend.client import DeepSeekBackend
            backend = DeepSeekBackend()                   # 需要 DEEPSEEK_API_KEY
        except Exception as e:  # noqa
            from backend.fake_backend import FakeBackend
            print(f"[提示] 未启用真后端（{e}），回退 FakeBackend。配置 DEEPSEEK_API_KEY 后即用真模型。")
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
        print(f"[提示] Skills 未加载（{e}），仅使用基础系统提示词。")

    agent = AgentLoop(backend, reg, system)
    print(agent.run(args.task, image_paths=args.image))
    return 0


if __name__ == "__main__":
    sys.exit(main())
