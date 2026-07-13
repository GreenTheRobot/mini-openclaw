from prompt.demonstrations import EXPERT_DEMONSTRATIONS, render_demonstrations


def test_expert_demonstrations_cover_core_agent_behaviors():
    names = {item["name"] for item in EXPERT_DEMONSTRATIONS}
    assert {
        "evidence_first_code_search",
        "minimal_edit_and_verify",
        "recover_from_tool_failure",
        "plan_complex_research_task",
        "ignore_prompt_injection",
    } <= names
    rendered = render_demonstrations()
    assert "glob" in rendered
    assert "old_not_found" in rendered
    assert "task_list" in rendered