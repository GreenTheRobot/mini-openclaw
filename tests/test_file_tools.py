from pathlib import Path

from tools.edit import _edit
from tools.glob import _glob
from tools.grep import _grep


def test_grep_has_python_fallback_without_rg(tmp_path: Path, monkeypatch):
    (tmp_path / "main.py").write_text("def train():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("tools.grep.shutil.which", lambda name: None)
    result = _grep("train", ".")
    assert "main.py:1:def train" in str(result).replace("\\", "/")


def test_glob_ignores_dependency_directories(tmp_path: Path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "hidden.py").write_text("ignored", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = _glob("**/*.py")
    assert "src/app.py" in result.replace("\\", "/")
    assert "node_modules" not in result


def test_glob_scopes_to_relative_path_and_rejects_escape(tmp_path: Path, monkeypatch):
    (tmp_path / "demo" / "project").mkdir(parents=True)
    (tmp_path / "demo" / "project" / "train.py").write_text("print('train')", encoding="utf-8")
    (tmp_path / "outside.py").write_text("print('outside')", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = _glob("**/*.py", path="demo/project")
    escaped = _glob("**/*.py", path="../")
    escaped_pattern = _glob("../*.py", path="demo/project")

    assert str(result).replace("\\", "/") == "demo/project/train.py"
    assert escaped.success is False
    assert escaped.category == "path_outside_workdir"
    assert escaped_pattern.success is False
    assert escaped_pattern.category == "pattern_outside_workdir"


def test_edit_rejects_empty_or_nonunique_match(tmp_path: Path):
    path = tmp_path / "sample.txt"
    path.write_text("same\nsame\n", encoding="utf-8")
    empty = _edit(str(path), "", "x")
    duplicate = _edit(str(path), "same", "changed")
    assert empty.success is False
    assert duplicate.success is False
    assert path.read_text(encoding="utf-8") == "same\nsame\n"
