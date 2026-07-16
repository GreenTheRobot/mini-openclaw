from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
from pathlib import Path

from agent.memory import KVMemory, Memory


def test_markdown_memory_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    Memory(path).write("科研结论必须区分事实与推测")
    assert "科研结论必须区分事实与推测" in Memory(path).recall()
    assert Memory(path).recall("事实") == "- 科研结论必须区分事实与推测"

def test_markdown_memory_rejects_empty_note(tmp_path: Path) -> None:
    memory = Memory(tmp_path / "MEMORY.md")
    try:
        memory.write("  \n ")
    except ValueError as exc:
        assert "不能为空" in str(exc)
    else:
        raise AssertionError("空记忆应被拒绝")

def test_kv_memory_overwrites_forgets_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    memory = KVMemory(path)
    memory.remember("experiment_launcher", "python")
    memory.remember("experiment_launcher", "nohup")
    memory.remember("temporary_note", "待删除")
    reloaded = KVMemory(path)
    assert reloaded.recall("experiment_launcher") == "nohup"
    assert reloaded.forget("temporary_note") is True
    assert reloaded.forget("missing") is False
    assert KVMemory(path).recall() == {"experiment_launcher": "nohup"}


def test_kv_memory_reloads_inside_lock_to_avoid_lost_updates(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    first = KVMemory(path)
    second = KVMemory(path)

    first.remember("first", "1")
    second.remember("second", "2")

    assert KVMemory(path).recall() == {"first": "1", "second": "2"}


def test_markdown_memory_concurrent_appends_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"

    def write(index: int) -> None:
        Memory(path).write(f"note-{index:02d}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(30)))

    recalled = Memory(path).recall()
    for index in range(30):
        assert f"- note-{index:02d}" in recalled
    assert len([line for line in recalled.splitlines() if line.startswith("- note-")]) == 30


def test_markdown_memory_cross_process_appends_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    code = (
        "import sys; "
        "from agent.memory import Memory; "
        "Memory(sys.argv[1]).write(sys.argv[2])"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path), f"process-note-{index:02d}"],
            cwd=os.getcwd(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr

    recalled = Memory(path).recall()
    for index in range(12):
        assert f"- process-note-{index:02d}" in recalled
