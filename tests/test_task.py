import json
import os

import pytest

from tools.task import _task_list
from tools.todo import _todo_write, _update_todo


def test_task_list_persists_and_enforces_single_active(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        created = json.loads(_task_list("create", items=[{"id": "a", "title": "检索论文"}, {"id": "b", "title": "生成报告"}]))
        assert len(created["items"]) == 2
        _task_list("update", task_id="a", status="in_progress")
        with pytest.raises(ValueError, match="已有进行中任务"):
            _task_list("update", task_id="b", status="in_progress")
        _task_list("update", task_id="a", status="completed", result="完成")
        reloaded = json.loads(_task_list("list"))
        assert reloaded["items"][0]["result"] == "完成"
    finally:
        os.chdir(old)


def test_todo_tools_render_and_share_persistent_state(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        rendered = _todo_write(["检索论文", "生成报告"])
        assert "[ ] 1 检索论文" in rendered
        assert "[ ] 2 生成报告" in rendered
        rendered = _update_todo(1, "in_progress")
        assert "[~] 1 检索论文" in rendered
        rendered = _update_todo(1, "completed")
        assert "[x] 1 检索论文" in rendered
        reloaded = json.loads(_task_list("list"))
        assert reloaded["items"][0]["status"] == "completed"
    finally:
        os.chdir(old)
