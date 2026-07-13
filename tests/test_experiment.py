import json
import os
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
        directory = Path("runs") / metadata["run_id"]
        (directory / "train.log").write_text("loss=0.8\nloss=0.3\naccuracy=0.9\n", encoding="utf-8")
        report = _experiment_report(metadata["run_id"])
        assert "accuracy：final=0.9" in report
        assert "随机种子：7" in report
    finally:
        os.chdir(old)