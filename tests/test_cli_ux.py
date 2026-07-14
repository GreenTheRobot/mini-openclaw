from datetime import datetime, timezone

from agent.cli import _build_parser, _runtime_context
from agent.ui import EventRenderer


def test_cli_parser_defaults_to_quiet_default_permission_mode():
    args = _build_parser().parse_args([])
    assert args.permission_mode == "default"
    assert args.verbose is False
    assert _build_parser().parse_args(["--permission-mode", "plan"]).permission_mode == "plan"


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

def test_runtime_context_resolves_recent_week_to_exact_dates():
    context = _runtime_context(datetime(2026, 7, 13, tzinfo=timezone.utc))
    assert "2026-07-13" in context
    assert "2026-07-06" in context
    assert "最近一周" in context