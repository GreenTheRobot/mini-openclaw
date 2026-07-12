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
