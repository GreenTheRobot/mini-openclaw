"""受控 shell 执行（Day5：bash；Day10：加沙箱与权限）。"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess

from .base import Tool


DENY = (":(){", "mkfs", "dd if=", "> /dev/sd", "curl", "wget")  # 兜底黑名单
SHELL_OPERATORS = {"&&", "||", ";", "|", "&"}


def _is_rm_option(token: str) -> bool:
    return token.startswith("-") and len(token) > 1


def _is_dangerous_rm_target(target: str) -> bool:
    stripped = target.strip("'\"")
    return (
        stripped in {"/", "/*", "~", "~/", "$HOME", "${HOME}", ".", "./", "..", "../", "*"}
        or stripped.startswith(("/", "~", "$HOME", "${HOME"))
        or stripped.startswith(("/home/", "/root/", "/Users/", "/mnt/c/", "/mnt/c/Users/"))
        or "*" in stripped
    )


def _has_dangerous_rm(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        tokens = command.split()

    for i, token in enumerate(tokens):
        if token != "rm":
            continue

        recursive = False
        force = False
        j = i + 1
        while j < len(tokens) and _is_rm_option(tokens[j]):
            opt = tokens[j].lower()
            recursive = recursive or "r" in opt or "recursive" in opt
            force = force or "f" in opt or "force" in opt
            j += 1

        while j < len(tokens) and tokens[j] not in SHELL_OPERATORS:
            if recursive and force and _is_dangerous_rm_target(tokens[j]):
                return True
            j += 1

    # Extra belt-and-suspenders check for compact shell text that shlex may not
    # preserve as expected, such as aliases or unusual quoting.
    return bool(re.search(r"\brm\s+-[^\n;|&]*[rf][^\n;|&]*\s+(~|\$HOME|\$\{HOME\}|/|\*|\.)", command))


def _build_command(command: str) -> tuple[list[str] | str, bool]:
    if shutil.which("bwrap"):
        # 只读挂载系统、可写仅工作目录、禁网（--unshare-net）
        return ([
            "bwrap",
            "--ro-bind", "/", "/",
            "--bind", ".", ".",
            "--unshare-net",
            "--dev", "/dev",
            "bash", "-c", command,
        ], False)
    if shutil.which("bash"):
        return (["bash", "-c", command], False)
    return (command, True)


def _bash(command: str, timeout: int = 30) -> str:
    # 沙箱判断
    if any(bad in command for bad in DENY) or _has_dangerous_rm(command):
        return "[沙箱] 拒绝执行高危命令：%s" % command

    cmd, use_shell = _build_command(command)
    # 执行
    try:
        p = subprocess.run(
            cmd, shell=use_shell, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {timeout}s 未结束：{command}"
    out = p.stdout or ""
    if p.stderr:
        out += f"\n[stderr]\n{p.stderr}"
    if p.returncode != 0:
        out += f"\n[returncode={p.returncode}]"
    return out.strip() or "[无输出]"
    


bash_tool = Tool(
    name="bash",
    description="在工作目录中执行一条 shell 命令并返回输出。",
    parameters={"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
    run=_bash,
)
