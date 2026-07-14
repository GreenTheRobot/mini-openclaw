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


def test_schedule_normalizes_an_absolute_workdir_inside_the_project(tmp_path: Path):
    research = tmp_path / "research"
    research.mkdir()

    spec = add_schedule(
        "absolute path", "整理本周论文", "interval", "", root=tmp_path,
        schedule_id="absolute-workdir", workdir=str(research), interval_minutes=1,
    )

    assert spec["workdir"] == "research"


def test_schedule_rejects_an_absolute_workdir_outside_the_project(tmp_path: Path):
    with pytest.raises(ValueError, match="项目目录内"):
        add_schedule(
            "outside", "整理本周论文", "interval", "", root=tmp_path,
            schedule_id="outside-workdir", workdir="/tmp", interval_minutes=1,
        )


def test_schedule_tool_is_registered():
    assert "schedule_task" in build_default_registry().names()


def test_open_todo_is_not_treated_as_completed(tmp_path: Path):
    todo = tmp_path / ".mini-openclaw" / "run.tasks.json"
    todo.parent.mkdir()
    todo.write_text(json.dumps({"items": [{"id": "step-1", "status": "in_progress"}]}), encoding="utf-8")

    state = _todo_status(todo, tmp_path)

    assert state["open"] == ["step-1"]
    assert state["path"] == ".mini-openclaw/run.tasks.json"


def test_cron_wakeup_is_idempotent_and_project_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cron = FakeCron()
    which = lambda name: "/usr/bin/crontab" if name == "crontab" else None
    monkeypatch.setattr(scheduler, "_read_crontab", lambda runner, which: cron.content)

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


def test_scheduled_run_without_a_todo_trail_is_completed_when_cli_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()

    seen = {}

    def successful_cli(*args, **kwargs):
        seen["command"] = args[0]
        return SimpleNamespace(returncode=0, stdout="placeholder backend", stderr="")

    monkeypatch.setattr(scheduler.subprocess, "run", successful_cli)
    result = scheduler._execute({
        "id": "missing-todo",
        "workdir": "research",
        "prompt": "create and complete a TODO",
        "python_executable": "/tmp/custom-venv/bin/python",
        "permission_mode": "plan",
        "timeout_seconds": 10,
    }, tmp_path)

    assert result["returncode"] == 0
    assert result["todo"]["present"] is False
    assert result["status"] == "completed"
    assert seen["command"][0] == "/tmp/custom-venv/bin/python"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["completed", "completed", "completed"], "completed"),
        (["completed", "incomplete", "completed"], "partial_complete"),
        (["failed", "timed_out", "failed"], "failed"),
    ],
)
def test_schedule_terminal_status_aggregates_all_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statuses: list[str], expected: str,
):
    (tmp_path / "research").mkdir()
    add_schedule(
        "aggregate", "run task", "interval", "", root=tmp_path,
        schedule_id="aggregate-status", workdir="research", interval_minutes=1, max_runs=3,
    )
    sequence = iter(statuses)

    def execute(_spec, _root):
        status = next(sequence)
        return {
            "run_id": f"run-{status}", "status": status, "returncode": 0,
            "started_at": "2026-07-15T00:00:00+08:00",
            "finished_at": "2026-07-15T00:00:01+08:00",
        }

    monkeypatch.setattr(scheduler, "_execute", execute)
    for _ in statuses:
        scheduler.run_schedule("aggregate-status", root=tmp_path)

    spec = list_schedules(tmp_path)[0]
    assert spec["enabled"] is False
    assert spec["run_count"] == 3
    assert spec["schedule_status"] == expected
    assert spec["run_summary"] == {status: statuses.count(status) for status in ("completed", "incomplete", "failed", "timed_out")}


def test_legacy_schedule_without_status_fields_remains_runnable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()
    spec = add_schedule(
        "legacy", "run task", "interval", "", root=tmp_path,
        schedule_id="legacy-status", workdir="research", interval_minutes=1, max_runs=2,
    )
    for key in ("run_history", "run_summary", "schedule_status", "finished_at"):
        spec.pop(key)
    (tmp_path / ".mini-openclaw" / "schedules.json").write_text(json.dumps([spec]), encoding="utf-8")
    monkeypatch.setattr(scheduler, "_execute", lambda *_args: {
        "run_id": "legacy-run", "status": "completed", "returncode": 0,
        "started_at": "2026-07-15T00:00:00+08:00", "finished_at": "2026-07-15T00:00:01+08:00",
    })

    scheduler.run_schedule("legacy-status", root=tmp_path)

    updated = list_schedules(tmp_path)[0]
    assert updated["run_count"] == 1
    assert updated["schedule_status"] == "running"
    assert updated["run_summary"]["completed"] == 1


def test_added_schedule_captures_preferred_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", "/home/test/openclaw")

    spec = add_schedule(
        "weekly review", "整理本周论文", "once", "2099-01-01T10:00:00+08:00",
        root=tmp_path, schedule_id="weekly-review-python", workdir="research",
    )

    assert spec["python_executable"] == scheduler._venv_python("/home/test/openclaw")


def test_scheduled_run_falls_back_to_env_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "research").mkdir()
    seen = {}

    def successful_cli(*args, **kwargs):
        seen["command"] = args[0]
        return SimpleNamespace(returncode=0, stdout="placeholder backend", stderr="")

    monkeypatch.setenv("MINI_OPENCLAW_PYTHON", "/home/test/openclaw/bin/python")
    monkeypatch.setattr(scheduler.subprocess, "run", successful_cli)
    result = scheduler._execute({
        "id": "missing-python-field",
        "workdir": "research",
        "prompt": "run with env python",
        "permission_mode": "plan",
        "timeout_seconds": 10,
    }, tmp_path)

    assert result["returncode"] == 0
    assert seen["command"][0] == "/home/test/openclaw/bin/python"


def test_cron_block_prefers_env_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MINI_OPENCLAW_PYTHON", "/home/test/openclaw/bin/python")

    block = scheduler._cron_block(tmp_path)

    assert "/home/test/openclaw/bin/python" in block
    assert "MINI_OPENCLAW_PYTHON" in block
