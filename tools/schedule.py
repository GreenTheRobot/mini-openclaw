"""Agent-facing tool for creating relative-path scheduled research jobs."""
from __future__ import annotations

import json

from agent.scheduler import (
    add_schedule, install_wakeup, list_schedules, remove_schedule, run_schedule,
    uninstall_wakeup, update_schedule, wakeup_status,
)
from .base import Tool


def _schedule_task(
    action: str,
    schedule_id: str = "",
    name: str = "",
    prompt: str = "",
    schedule_type: str = "once",
    expression: str = "",
    workdir: str = ".",
    timezone: str = "Asia/Shanghai",
    permission_mode: str = "auto-local",
    timeout_seconds: int = 1800,
    interval_minutes: int = 0,
    max_runs: int = 0,
    auto_wakeup: bool = True,
) -> str:
    if action == "add":
        result = add_schedule(
            name, prompt, schedule_type, expression,
            schedule_id=schedule_id, workdir=workdir, timezone=timezone,
            permission_mode=permission_mode, timeout_seconds=timeout_seconds,
            interval_minutes=interval_minutes, max_runs=max_runs,
        )
        if auto_wakeup:
            try:
                result["wakeup"] = install_wakeup()
            except Exception:
                remove_schedule(str(result["id"]))
                raise
        return json.dumps(result, ensure_ascii=False, indent=2)
    if action == "list":
        return json.dumps({"schedules": list_schedules(), "wakeup": wakeup_status()}, ensure_ascii=False, indent=2)
    if action == "wakeup_status":
        return json.dumps(wakeup_status(), ensure_ascii=False, indent=2)
    if action == "enable_wakeup":
        return json.dumps(install_wakeup(), ensure_ascii=False, indent=2)
    if action == "disable_wakeup":
        return json.dumps(uninstall_wakeup(), ensure_ascii=False, indent=2)
    if not schedule_id:
        raise ValueError(f"{action} 需要 schedule_id")
    if action == "pause":
        return json.dumps(update_schedule(schedule_id, enabled=False), ensure_ascii=False, indent=2)
    if action == "resume":
        return json.dumps(update_schedule(schedule_id, enabled=True), ensure_ascii=False, indent=2)
    if action == "remove":
        removed = remove_schedule(schedule_id)
        result: dict[str, object] = {"removed": removed}
        if removed and not list_schedules():
            result["wakeup"] = uninstall_wakeup()
        return json.dumps(result, ensure_ascii=False)
    if action == "run_now":
        return json.dumps(run_schedule(schedule_id), ensure_ascii=False, indent=2)
    raise ValueError("action 必须是 add/list/pause/resume/remove/run_now/wakeup_status/enable_wakeup/disable_wakeup")


schedule_task_tool = Tool(
    "schedule_task",
    "创建、查看、暂停、恢复、删除或立即执行相对路径的科研定时任务。创建时默认安装用户级 cron 唤醒器，终端关闭后仍会每分钟检查到期任务。",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "pause", "resume", "remove", "run_now", "wakeup_status", "enable_wakeup", "disable_wakeup"]},
            "schedule_id": {"type": "string"},
            "name": {"type": "string"},
            "prompt": {"type": "string"},
            "schedule_type": {"type": "string", "enum": ["once", "interval", "cron"]},
            "expression": {"type": "string"},
            "workdir": {"type": "string", "description": "项目内路径；可使用相对路径，项目内绝对路径会自动规范化。默认 ."},
            "timezone": {"type": "string"},
            "permission_mode": {"type": "string", "enum": ["plan", "auto-safe", "auto-local"]},
            "timeout_seconds": {"type": "integer"},
            "interval_minutes": {"type": "integer"},
            "max_runs": {"type": "integer", "description": "最多执行轮数；0 表示不限制"},
            "auto_wakeup": {"type": "boolean", "description": "创建任务时是否自动配置用户级 cron 唤醒；默认 true"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    _schedule_task,
)
