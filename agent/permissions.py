"""Tool permission policies and scoped approval grants."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PERMISSION_MODES = ("plan", "default", "accept-edits", "auto-safe")
WRITE = {"write", "edit"}
EXEC = {"bash"}
NETWORK_READ = {"arxiv_search", "web_search", "web_fetch"}
EXTERNAL_SEND = {"wechat_file_transfer"}
DOWNLOAD = {"download_file"}
MEMORY_WRITE = {"remember"}
PLANNING = {"task_list", "todo_write", "update_todo"}
SCHEDULING = {"schedule_task"}
PDF_READ = {"pdf_extract_text", "pdf_metadata"}
FIGURE_READ = {"paper_figure_analyze"}
EXPERIMENT_READ = {"experiment_status"}
EXPERIMENT_WRITE = {"experiment_prepare", "experiment_report"}
EXPERIMENT_EXEC = {"experiment_smoke_test", "experiment_start"}


@dataclass(frozen=True)
class PermissionDecision:
    verdict: str
    reason: str = ""
    grant_key: str = ""
    grant_label: str = ""
    grant_scopes: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.verdict


@dataclass(frozen=True)
class ConfirmationResponse:
    approved: bool
    scope: str = "once"


def _resolve_in_workdir(raw_path: str, workdir: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = workdir / path
    return path.resolve()


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


def _glob_decision(args: dict[str, Any], workdir: Path) -> PermissionDecision:
    pattern = str(args.get("pattern", ""))
    parts = Path(pattern).parts
    if Path(pattern).is_absolute() or ".." in parts:
        return PermissionDecision("deny", f"glob pattern 不允许越过工作目录：{pattern}")
    raw_path = str(args.get("path", ".") or ".")
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        return PermissionDecision("deny", f"glob path 必须是工作目录内的相对路径：{raw_path}")
    target = _resolve_in_workdir(raw_path, workdir)
    if not _is_inside(target, workdir):
        return PermissionDecision("deny", f"glob path 越过工作目录：{raw_path}")
    return PermissionDecision("allow", f"glob 限定在工作目录子目录：{raw_path}")


def _bash_decision(args: dict[str, Any]) -> PermissionDecision:
    command = str(args.get("command", "")).strip()
    if not command:
        return PermissionDecision("deny", "缺少 command 参数")
    return PermissionDecision("confirm", "shell 命令需要确认后执行")


def _network_decision(tool: str, args: dict[str, Any]) -> PermissionDecision:
    if tool == "arxiv_search":
        return PermissionDecision(
            "confirm", "将使用 arXiv API 进行只读论文检索",
            grant_key="network-read:arxiv_search:export.arxiv.org",
            grant_label="arxiv_search → export.arxiv.org",
            grant_scopes=("task", "session"),
        )
    if tool == "web_search":
        return PermissionDecision(
            "confirm", "将使用 DuckDuckGo 进行只读网页搜索",
            grant_key="network-read:web_search:duckduckgo.com",
            grant_label="web_search → duckduckgo.com",
            grant_scopes=("task", "session"),
        )
    raw_url = str(args.get("url", ""))
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return PermissionDecision("deny", f"无效的网页 URL：{raw_url}")
    return PermissionDecision(
        "confirm", f"将只读访问外部域名：{host}",
        grant_key=f"network-read:{tool}:{host}",
        grant_label=f"{tool} → {host}",
        grant_scopes=("task", "session"),
    )


def _download_decision(args: dict[str, Any], workdir: Path) -> PermissionDecision:
    path_decision = _path_decision(args, workdir, confirm=False)
    if path_decision.verdict == "deny":
        return path_decision
    raw_url = str(args.get("url", ""))
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() != "https" or not host:
        return PermissionDecision("deny", "download_file 只接受有效的 HTTPS URL")
    from tools.download import is_allowed_pdf_host
    if not is_allowed_pdf_host(host):
        return PermissionDecision("deny", f"PDF 下载域名不在白名单：{host}")
    target = _resolve_in_workdir(str(args["path"]), workdir)
    return PermissionDecision(
        "confirm", f"将从 {host} 下载 PDF 到工作目录：{target}",
        grant_key=f"network-read:download_file:{host}",
        grant_label=f"download_file → {host}",
        grant_scopes=("task", "session"),
    )


def check(tool: str, args: dict[str, Any], workdir: Path, *, mode: str = "default") -> PermissionDecision:
    """Return allow / confirm / deny for one tool invocation."""
    if mode not in PERMISSION_MODES:
        raise ValueError(f"未知权限模式：{mode}")

    if tool == "glob":
        return _glob_decision(args, workdir)
    if tool == "read":
        return _path_decision(args, workdir, confirm=False)
    if tool == "grep":
        grep_args = dict(args)
        grep_args.setdefault("path", ".")
        return _path_decision(grep_args, workdir, confirm=False)
    if tool in PDF_READ:
        return _path_decision(args, workdir, confirm=False)
    if tool in FIGURE_READ:
        return _path_decision(args, workdir, confirm=False)
    if tool in PLANNING:
        return PermissionDecision("allow", "任务规划仅写入工作目录内部状态")
    if tool in SCHEDULING:
        action = str(args.get("action", ""))
        if action in {"list", "wakeup_status"}:
            return PermissionDecision("allow", "读取工作目录内的调度配置")
        return PermissionDecision("confirm", "将创建或改变自动科研任务调度配置")
    if tool in EXTERNAL_SEND and "dry_run" in args and bool(args.get("dry_run")):
        return PermissionDecision("allow", "微信通知处于 dry-run，仅生成预览，不产生外部副作用")

    mutating = WRITE | EXEC | DOWNLOAD | EXTERNAL_SEND | MEMORY_WRITE | EXPERIMENT_READ | EXPERIMENT_WRITE | EXPERIMENT_EXEC
    if mode == "plan" and tool in mutating:
        return PermissionDecision("deny", f"plan 模式禁止执行会产生副作用的工具：{tool}")

    if tool in WRITE:
        return _path_decision(args, workdir, confirm=mode not in {"accept-edits", "auto-safe"})
    if tool in EXEC:
        return _bash_decision(args)
    if tool in NETWORK_READ:
        decision = _network_decision(tool, args)
        if mode == "auto-safe" and decision.verdict == "confirm":
            return PermissionDecision("allow", decision.reason)
        return decision
    if tool in DOWNLOAD:
        decision = _download_decision(args, workdir)
        if mode == "auto-safe" and decision.verdict == "confirm":
            return PermissionDecision("allow", decision.reason)
        return decision
    if tool in EXTERNAL_SEND:
        return PermissionDecision("confirm", "请求真实发送微信消息，必须逐次确认接收方和正文")
    if tool in EXPERIMENT_READ:
        return PermissionDecision("allow", "读取工作目录内实验状态")
    if tool in EXPERIMENT_WRITE:
        if mode == "auto-safe":
            return PermissionDecision("allow", "在工作目录内写入实验元数据或报告")
        return PermissionDecision("confirm", "将写入工作目录内实验元数据或报告")
    if tool in EXPERIMENT_EXEC:
        return PermissionDecision("confirm", "将执行或启动实验命令")
    if tool in MEMORY_WRITE:
        return PermissionDecision("confirm", "将持久化跨会话项目记忆，必须逐次确认")
    if mode == "plan":
        return PermissionDecision("deny", f"plan 模式禁止未知或外部工具：{tool}")
    return PermissionDecision("confirm", f"未知或外部工具 {tool} 必须逐次确认")


@dataclass
class PermissionManager:
    mode: str = "default"
    task_grants: set[str] = field(default_factory=set)
    session_grants: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.set_mode(self.mode)

    def set_mode(self, mode: str) -> None:
        if mode not in PERMISSION_MODES:
            raise ValueError(f"未知权限模式：{mode}")
        self.mode = mode
        self.task_grants.clear()

    def begin_task(self) -> None:
        self.task_grants.clear()

    def reset_session(self) -> None:
        self.task_grants.clear()
        self.session_grants.clear()

    def decide(self, tool: str, args: dict[str, Any], workdir: Path) -> PermissionDecision:
        decision = check(tool, args, workdir, mode=self.mode)
        if decision.verdict == "confirm" and decision.grant_key:
            if decision.grant_key in self.task_grants or decision.grant_key in self.session_grants:
                return PermissionDecision("allow", f"已获得授权：{decision.grant_label}")
        return decision

    def grant(self, decision: PermissionDecision, scope: str) -> None:
        if scope not in {"once", "task", "session"}:
            raise ValueError(f"未知授权范围：{scope}")
        if scope not in decision.grant_scopes or not decision.grant_key:
            return
        if scope == "task":
            self.task_grants.add(decision.grant_key)
        elif scope == "session":
            self.session_grants.add(decision.grant_key)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "task_grants": sorted(self.task_grants),
            "session_grants": sorted(self.session_grants),
        }
