from datetime import datetime, timezone
import json
import os

from agent.cli import _build_parser, _ensure_session_todo_path, _prepare_turn_context, _runtime_context
from agent.memory import Memory
from agent.ui import EventRenderer


def test_cli_parser_defaults_to_quiet_default_permission_mode():
    args = _build_parser().parse_args([])
    assert args.permission_mode == "default"
    assert args.verbose is False
    assert args.multi_agent is True
    assert _build_parser().parse_args(["--permission-mode", "plan"]).permission_mode == "plan"
    assert _build_parser().parse_args(["--multi-agent"]).multi_agent is True
    assert _build_parser().parse_args(["--no-multi-agent"]).multi_agent is False


def test_quiet_renderer_hides_tool_arguments_but_steps_reveals_summary():
    output = []
    renderer = EventRenderer(output.append, verbose=False)
    renderer.begin_turn()
    renderer("model_start", {"turn": 1})
    renderer("tool_start", {"name": "web_fetch", "arguments": {"url": "https://example.com/secret"}})
    renderer("tool_result", {"name": "web_fetch", "success": True, "observation": "page content"})
    visible = "\n".join(output)
    assert "secret" not in visible
    assert "page content" not in visible
    steps = renderer.steps_markdown()
    assert "web_fetch" in steps
    assert "page content" in steps


def test_verbose_renderer_shows_observable_tool_events():
    output = []
    renderer = EventRenderer(output.append, verbose=True)
    renderer.begin_turn()
    renderer("tool_start", {"name": "read", "arguments": {"path": "README.md"}})
    renderer("tool_result", {"name": "read", "success": True, "observation": "ok"})
    text = "\n".join(output)
    assert "[tool] read" in text
    assert "[ok] read" in text


def test_quiet_renderer_shows_multi_agent_progress_without_tool_details():
    output = []
    renderer = EventRenderer(output.append, verbose=False)
    renderer.begin_turn()

    renderer("orchestration", {"use_subagents": True, "reason": "需要分工"})
    renderer("subagent_start", {"role": "Research Agent", "assignment": "读取 paper.md 并总结"})
    renderer("tool_start", {"name": "read", "arguments": {"path": "paper.md"}})
    renderer("tool_result", {"name": "read", "success": True, "observation": "paper content"})
    renderer("subagent_done", {"role": "Research Agent"})
    renderer("synthesis_start", {})
    renderer("review_start", {})

    visible = "\n".join(output)
    assert "主 Agent 调度" in visible
    assert "Research Agent 开始" in visible
    assert "Research Agent 完成" in visible
    assert "正在综合" in visible
    assert "Reviewer" in visible
    assert "paper content" not in visible


def test_renderer_can_load_steps_from_trace_files(tmp_path):
    trace = tmp_path / "sub.jsonl"
    trace.write_text(
        json.dumps({
            "event": "tool_result",
            "tool": "read",
            "arguments": {"path": "paper.md"},
            "success": True,
            "observation": "paper content",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    renderer = EventRenderer(lambda text: None)
    renderer.load_trace_steps([trace])

    steps = renderer.steps_markdown()

    assert "read" in steps
    assert "paper.md" in steps
    assert "paper content" in steps


def test_renderer_load_trace_steps_filters_by_start_time(tmp_path):
    trace = tmp_path / "sub.jsonl"
    trace.write_text(
        "\n".join([
            json.dumps({
                "ts": 10.0,
                "event": "tool_result",
                "tool": "old_tool",
                "arguments": {},
                "success": True,
                "observation": "old",
            }, ensure_ascii=False),
            json.dumps({
                "ts": 20.0,
                "event": "tool_result",
                "tool": "wechat_file_transfer",
                "arguments": {"message": "晚上好"},
                "success": True,
                "observation": "sent",
            }, ensure_ascii=False),
        ]) + "\n",
        encoding="utf-8",
    )
    renderer = EventRenderer(lambda text: None)
    renderer.load_trace_steps([trace], since_ts=15.0)

    steps = renderer.steps_markdown()

    assert "wechat_file_transfer" in steps
    assert "晚上好" in steps
    assert "old_tool" not in steps

def test_runtime_context_resolves_recent_week_to_exact_dates():
    context = _runtime_context(datetime(2026, 7, 13, tzinfo=timezone.utc))
    assert "2026-07-13" in context
    assert "2026-07-06" in context
    assert "最近一周" in context


def test_turn_context_loads_latest_disk_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Memory("MEMORY.md").write("跨会话约定：报告先写结论")
    loaded = {}

    class DummyAgent:
        def add_context(self, key, content):
            loaded[key] = content

    _prepare_turn_context(DummyAgent(), "写报告", [], planning=False)

    assert any(key.startswith("project-memory:") for key in loaded)
    assert "跨会话约定：报告先写结论" in "\n".join(loaded.values())


def test_cli_assigns_isolated_todo_path_when_unset(monkeypatch):
    monkeypatch.delenv("MINI_OPENCLAW_TODO_PATH", raising=False)

    try:
        path = _ensure_session_todo_path("run/id:one")

        assert path.as_posix() == ".mini-openclaw/sessions/run-id-one/tasks.json"
        assert os.environ["MINI_OPENCLAW_TODO_PATH"] == path.as_posix()
    finally:
        os.environ.pop("MINI_OPENCLAW_TODO_PATH", None)


def test_cli_preserves_existing_todo_path(monkeypatch):
    monkeypatch.setenv("MINI_OPENCLAW_TODO_PATH", ".mini-openclaw/scheduler-runs/x.tasks.json")

    path = _ensure_session_todo_path("new-run")

    assert path.as_posix() == ".mini-openclaw/scheduler-runs/x.tasks.json"
