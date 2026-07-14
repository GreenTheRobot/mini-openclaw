import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.scheduler as scheduler
from agent.scheduler import (
    _todo_status,
    add_schedule,
    install_wakeup,
    list_schedules,
    remove_schedule,
    uninstall_wakeup,
    update_schedule,
    wakeup_status,
)
from tools.base import build_default_registry
from tools.schedule import _schedule_task


class FakeCron:
    """Small in-memory crontab implementation for wake-up integration tests."""

    def __init__(self) -> None:
        self.content = ""

    def __call__(self, args, **kwargs):
        if args == ["crontab", "-l"]:
            if self.content:
                return SimpleNamespace(returncode=0, stdout=self.content, stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="no crontab for test")
        if args == ["crontab", "-"]:
            self.content = kwargs["input"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args == ["pgrep", "-x", "cron"]:
            return SimpleNamespace(returncode=0, stdout="123\n", stderr="")
        if args == ["pgrep", "-x", "crond"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {args}")


def test_schedule_persists_only_relative_paths(tmp_path: Path):
    (tmp_path / "research").mkdir()
    spec = add_schedule(
        "weekly review", "整理本周论文", "once", "2099-01-01T10:00:00+08:00",
        root=tmp_path, schedule_id="weekly-review", workdir="research",
        max_runs=3,
    )

    raw = (tmp_path / ".mini-openclaw" / "schedules.json").read_text(encoding="utf-8")
    assert spec["workdir"] == "research"
    assert spec["max_runs"] == 3
    assert spec["run_count"] == 0
    assert str(tmp_path) not in raw
    assert list_schedules(tmp_path)[0]["id"] == "weekly-review"
    assert update_schedule("weekly-review", root=tmp_path, enabled=False)["enabled"] is False
    assert remove_schedule("weekly-review", root=tmp_path) is True


def test_schedule_tool_is_registered():
    assert "schedule_task" in build_default_registry().names()


def test_open_todo_is_not_treated_as_completed(tmp_path: Path):
    todo = tmp_path / ".mini-openclaw" / "run.tasks.json"
    todo.parent.mkdir()
    todo.write_text(json.dumps({"items": [{"id": "step-1", "status": "in_progress"}]}), encoding="utf-8")

    state = _todo_status(todo, tmp_path)

    assert state["open"] == ["step-1"]
    assert state["path"] == ".mini-openclaw/run.tasks.json"


def test_cron_wakeup_is_idempotent_and_project_scoped(tmp_path: Path):
    cron = FakeCron()
    which = lambda name: "/usr/bin/crontab" if name == "crontab" else None

    first = install_wakeup(root=tmp_path, runner=cron, which=which)
    second = install_wakeup(root=tmp_path, runner=cron, which=which)

    assert first["installed"] is True
    assert second["active"] is True
    assert cron.content.count("BEGIN") == 1
    assert "* * * * *" in cron.content
    assert str(tmp_path) in cron.content  # Absolute paths exist only in the external cron entry.

    removed = uninstall_wakeup(root=tmp_path, runner=cron, which=which)

    assert removed["installed"] is False
    assert cron.content == ""


def test_schedule_add_reports_wakeup_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "tools.schedule.install_wakeup",
        lambda: {"backend": "cron", "installed": True, "active": True},
    )

    result = json.loads(_schedule_task(
        "add", name="recurring", prompt="write a short review", schedule_type="interval",
        workdir="research", interval_minutes=3,
    ))

    assert result["wakeup"]["active"] is True
    assert list_schedules(tmp_path)[0]["id"] == result["id"]


def test_schedule_add_rolls_back_if_wakeup_installation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()
    monkeypatch.chdir(tmp_path)

    def fail_install() -> dict[str, object]:
        raise RuntimeError("crontab unavailable")

    monkeypatch.setattr("tools.schedule.install_wakeup", fail_install)

    with pytest.raises(RuntimeError, match="crontab unavailable"):
        _schedule_task(
            "add", name="recurring", prompt="write a short review", schedule_type="interval",
            workdir="research", interval_minutes=3,
        )

    assert list_schedules(tmp_path) == []


def test_scheduled_run_without_a_todo_trail_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()

    def successful_cli(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="placeholder backend", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", successful_cli)
    result = scheduler._execute({
        "id": "missing-todo",
        "workdir": "research",
        "prompt": "create and complete a TODO",
        "permission_mode": "plan",
        "timeout_seconds": 10,
    }, tmp_path)

    assert result["returncode"] == 0
    assert result["todo"]["present"] is False
    assert result["status"] == "incomplete"
