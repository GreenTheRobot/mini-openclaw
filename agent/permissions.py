from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


WRITE = {"write", "edit"}
EXEC = {"bash"}
NETWORK = {"web_search", "web_fetch", "wechat_file_transfer"}


@dataclass(frozen=True)
class PermissionDecision:
    verdict: str
    reason: str = ""

    def __str__(self) -> str:
        return self.verdict


def _resolve_in_workdir(raw_path: str, workdir: Path) -> Path:
    p = Path(raw_path)
    if not p.is_absolute():
        p = workdir / p
    return p.resolve()


def _is_inside(path: Path, workdir: Path) -> bool:
    try:
        path.relative_to(workdir.resolve())
        return True
    except ValueError:
        return False


def _path_decision(args: dict[str, Any], workdir: Path, *, confirm: bool) -> PermissionDecision:
    raw_path = args.get("path")
    if not raw_path:
        return PermissionDecision("deny", "缺少 path 参数")

    path = _resolve_in_workdir(str(raw_path), workdir)
    if not _is_inside(path, workdir):
        return PermissionDecision("deny", f"路径越过工作目录：{path}")

    if confirm:
        return PermissionDecision("confirm", f"将修改工作目录内文件：{path}")
    return PermissionDecision("allow", f"访问工作目录内文件：{path}")


def _glob_decision(args: dict[str, Any]) -> PermissionDecision:
    pattern = str(args.get("pattern", ""))
    parts = Path(pattern).parts
    if Path(pattern).is_absolute() or ".." in parts:
        return PermissionDecision("deny", f"glob pattern 不允许越过工作目录：{pattern}")
    return PermissionDecision("allow", "glob 限定在工作目录递归查找")


def _bash_decision(args: dict[str, Any]) -> PermissionDecision:
    command = str(args.get("command", "")).strip()
    if not command:
        return PermissionDecision("deny", "缺少 command 参数")
    return PermissionDecision("confirm", "shell 命令需要确认后执行")


def check(tool: str, args: dict[str, Any], workdir: Path) -> PermissionDecision:
    """返回工具调用的权限判定：allow / confirm / deny。"""
    if tool == "glob":
        return _glob_decision(args)
    if tool == "read":
        return _path_decision(args, workdir, confirm=False)
    if tool == "grep":
        grep_args = dict(args)
        grep_args.setdefault("path", ".")
        return _path_decision(grep_args, workdir, confirm=False)
    if tool in WRITE:
        return _path_decision(args, workdir, confirm=True)
    if tool in EXEC:
        return _bash_decision(args)
    if tool in NETWORK:
        return PermissionDecision("confirm", "网络访问或外部发送需要确认后执行")
    return PermissionDecision("confirm", f"未知或外部工具 {tool} 需要确认后执行")
