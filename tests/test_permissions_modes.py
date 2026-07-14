from pathlib import Path

from agent.permissions import ConfirmationResponse, PermissionManager, check


def test_permission_modes_have_distinct_write_and_execution_behavior(tmp_path: Path):
    args = {"path": "notes.md"}
    assert check("write", args, tmp_path, mode="plan").verdict == "deny"
    assert check("write", args, tmp_path, mode="default").verdict == "confirm"
    assert check("write", args, tmp_path, mode="accept-edits").verdict == "allow"
    assert check("write", args, tmp_path, mode="auto-safe").verdict == "allow"
    assert check("bash", {"command": "python -V"}, tmp_path, mode="auto-safe").verdict == "confirm"
    assert check("wechat_file_transfer", {"message": "x", "dry_run": True}, tmp_path, mode="auto-safe").verdict == "allow"
    assert check("wechat_file_transfer", {"message": "x", "dry_run": False}, tmp_path, mode="auto-safe").verdict == "confirm"
    assert check("wechat_file_transfer", {"message": "x"}, tmp_path, mode="auto-safe").verdict == "confirm"


def test_network_grants_are_scoped_to_exact_tool_and_hostname(tmp_path: Path):
    manager = PermissionManager("default")
    arxiv = manager.decide("arxiv_search", {"query": "multimodal compression"}, tmp_path)
    assert arxiv.verdict == "confirm"
    assert arxiv.grant_key == "network-read:arxiv_search:export.arxiv.org"
    first = manager.decide("web_fetch", {"url": "https://graph-robots.github.io/gap/"}, tmp_path)
    assert first.verdict == "confirm"
    manager.grant(first, "session")
    same = manager.decide("web_fetch", {"url": "https://graph-robots.github.io/gap/paper"}, tmp_path)
    sibling = manager.decide("web_fetch", {"url": "https://other.github.io/page"}, tmp_path)
    download = manager.decide("download_file", {"url": "https://arxiv.org/pdf/x.pdf", "path": "x.pdf"}, tmp_path)
    assert same.verdict == "allow"
    assert sibling.verdict == "confirm"
    assert download.verdict == "confirm"


def test_task_grant_expires_but_session_grant_persists(tmp_path: Path):
    manager = PermissionManager("default")
    decision = manager.decide("web_search", {"query": "agent"}, tmp_path)
    manager.grant(decision, "task")
    assert manager.decide("web_search", {"query": "paper"}, tmp_path).verdict == "allow"
    manager.begin_task()
    assert manager.decide("web_search", {"query": "paper"}, tmp_path).verdict == "confirm"
    manager.grant(decision, "session")
    manager.begin_task()
    assert manager.decide("web_search", {"query": "paper"}, tmp_path).verdict == "allow"
    manager.reset_session()
    assert manager.decide("web_search", {"query": "paper"}, tmp_path).verdict == "confirm"


def test_plan_denies_unknown_external_tools(tmp_path: Path):
    assert check("mcp__unknown", {}, tmp_path, mode="plan").verdict == "deny"


def test_glob_permission_allows_relative_path_and_rejects_escape(tmp_path: Path):
    assert check("glob", {"pattern": "**/*.py", "path": "demo/project"}, tmp_path).verdict == "allow"
    assert check("glob", {"pattern": "**/*.py", "path": "../outside"}, tmp_path).verdict == "deny"
    assert check("glob", {"pattern": "../*.py", "path": "."}, tmp_path).verdict == "deny"
