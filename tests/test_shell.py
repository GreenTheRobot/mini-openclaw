import os
from pathlib import Path

from tools.shell import _bash


def test_bash_nonzero_result_includes_current_workdir(tmp_path: Path):
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = _bash("exit 7")
    finally:
        os.chdir(old)

    assert result.success is False
    assert result.category == "nonzero_exit"
    assert "[returncode=7]" in result.content
    assert f"[cwd={tmp_path}]" in result.content
