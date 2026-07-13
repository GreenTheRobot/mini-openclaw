import json
import os

import pytest

from tools.task import _task_list


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