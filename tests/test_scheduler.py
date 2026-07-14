import json
from pathlib import Path

from agent.scheduler import _todo_status, add_schedule, list_schedules, remove_schedule, update_schedule
from tools.base import build_default_registry


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
