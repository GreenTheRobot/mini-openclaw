from agent.planner import needs_plan, planning_guidance
from agent.reviewer import review_answer


class Backend:
    def chat(self, messages, tools=None):
        return {"content": "审查结论：通过", "tool_calls": []}


def test_planner_only_triggers_for_complex_tasks():
    assert needs_plan("读取 README") is False
    assert needs_plan("分析代码，然后修改配置，运行实验，最后生成报告") is True
    guidance = planning_guidance("分析代码，然后修改配置，运行实验，最后生成报告")
    assert "todo_write" in guidance
    assert "update_todo" in guidance


def test_reviewer_is_separate_model_stage():
    assert review_answer(Backend(), "任务", "答案") == "审查结论：通过"


def test_reviewer_receives_bounded_tool_evidence():
    class EvidenceBackend:
        def __init__(self):
            self.messages = None

        def chat(self, messages, tools=None):
            self.messages = messages
            return {"content": "审查结论：通过", "tool_calls": []}

    backend = EvidenceBackend()
    review_answer(backend, "任务", "答案", "tool=read success=true")
    assert "tool=read success=true" in backend.messages[-1]["content"]
