import json
import os
import subprocess
from pathlib import Path

from tools.experiment import (
    _experiment_prepare,
    _experiment_report,
    _experiment_smoke_test,
    _extract_metrics,
)


def test_extract_metrics():
    assert _extract_metrics("loss=0.8 accuracy: 0.6\nloss=0.4 accuracy=0.9") == {"loss": [0.8, 0.4], "accuracy": [0.6, 0.9]}


def test_experiment_prepare_smoke_and_report(tmp_path: Path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        smoke_result = _experiment_smoke_test('python -c "print(123)"')
        assert smoke_result.success is True
        smoke = json.loads(smoke_result.content)
        assert smoke["success"] is True
        metadata = json.loads(_experiment_prepare('python train.py', name="demo", seed=7))
        assert metadata["git_repository"] is True
        assert metadata["git_has_commit"] is True
        assert metadata["git_commit"] != "unknown"
        directory = Path("runs") / metadata["run_id"]
        (directory / "train.log").write_text("loss=0.8\nloss=0.3\naccuracy=0.9\n", encoding="utf-8")
        report = _experiment_report(metadata["run_id"])
        assert "accuracy：final=0.9" in report
        assert "随机种子：7" in report
    finally:
        os.chdir(old)


def test_experiment_prepare_initializes_gitignore_and_initial_commit(tmp_path: Path):
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        Path("train.py").write_text("print('baseline')\n", encoding="utf-8")
        metadata = json.loads(_experiment_prepare("python train.py", name="nogit"))

        assert (tmp_path / ".git").exists()
        assert (tmp_path / ".gitignore").exists()
        assert "runs/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert metadata["git_repository"] is True
        assert metadata["git_initialized"] is True
        assert metadata["gitignore_initialized"] is True
        assert metadata["git_initial_commit_created"] is True
        assert metadata["git_has_commit"] is True
        assert metadata["git_commit"] != "unknown"

        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
            check=True,
        )
        assert "initialize experiment baseline" in log.stdout
    finally:
        os.chdir(old)
