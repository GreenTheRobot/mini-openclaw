"""受控 shell 执行与跨平台危险命令拦截。"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess

from .base import Tool, ToolResult


DENY = (
    ":(){", "mkfs", "dd if=", "> /dev/sd", "curl", "wget", "diskpart",
    "format c:", "shutdown /s", "cipher /w",
)
SHELL_OPERATORS = {"&&", "||", ";", "|", "&"}


def _is_rm_option(token: str) -> bool:
    return token.startswith("-") and len(token) > 1


def _is_dangerous_rm_target(target: str) -> bool:
    stripped = target.strip("'\"")
    return (
        stripped in {"/", "/*", "~", "~/", "$HOME", "${HOME}", ".", "./", "..", "../", "*"}
        or stripped.startswith(("/", "~", "$HOME", "${HOME}"))
        or stripped.startswith(("/home/", "/root/", "/Users/", "/mnt/c/", "/mnt/c/Users/"))
        or "*" in stripped
    )


def _has_dangerous_rm(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        tokens = command.split()

    for index, token in enumerate(tokens):
        if token != "rm":
            continue
        recursive = False
        force = False
        cursor = index + 1
        while cursor < len(tokens) and _is_rm_option(tokens[cursor]):
            option = tokens[cursor].lower()
            recursive = recursive or "r" in option or "recursive" in option
            force = force or "f" in option or "force" in option
            cursor += 1
        while cursor < len(tokens) and tokens[cursor] not in SHELL_OPERATORS:
            if recursive and force and _is_dangerous_rm_target(tokens[cursor]):
                return True
            cursor += 1

    return bool(re.search(r"\brm\s+-[^\n;|&]*[rf][^\n;|&]*\s+(~|\$HOME|\$\{HOME\}|/|\*|\.)", command))


def _build_command(command: str) -> tuple[list[str] | str, bool]:
    if shutil.which("bwrap"):
        return ([
            "bwrap", "--ro-bind", "/", "/", "--bind", ".", ".",
            "--unshare-net", "--dev", "/dev", "bash", "-c", command,
        ], False)
    if shutil.which("bash"):
        return (["bash", "-c", command], False)
    return (command, True)


def is_dangerous_command(command: str) -> bool:
    lowered = command.lower()
    dangerous_windows_delete = bool(re.search(
        r"\b(remove-item|del|erase|rd|rmdir)\b[^\n]*(/s|/q|-recurse|-force)[^\n]*(\\users\\|c:\\|\$env:userprofile|~)",
        lowered,
    ))
    return any(bad in lowered for bad in DENY) or _has_dangerous_rm(command) or dangerous_windows_delete


def _bash(command: str, timeout: int = 30) -> ToolResult:
    cwd = os.getcwd()
    if is_dangerous_command(command):
        return ToolResult(f"[沙箱] 拒绝执行高危命令：{command}", False, "sandbox_denied")

    cmd, use_shell = _build_command(command)
    try:
        process = subprocess.run(
            cmd, shell=use_shell, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(f"[超时] 命令超过 {timeout}s 未结束：{command}", False, "timeout")

    output = process.stdout or ""
    if process.stderr:
        output += f"\n[stderr]\n{process.stderr}"
    if process.returncode != 0:
        output += f"\n[returncode={process.returncode}]"
        output += f"\n[cwd={cwd}]"
        return ToolResult(output.strip(), False, "nonzero_exit")
    return ToolResult(output.strip() or "[无输出]", True, "ok")


bash_tool = Tool(
    name="bash",
    description="在工作目录中执行一条受控 shell 命令并返回输出；危险命令会被拦截。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    run=_bash,
)
