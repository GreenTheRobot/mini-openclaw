from agent.reviewer import review_needs_revision


def test_review_pass_conclusion_wins_over_risk_words():
    review = (
        "审查结论：通过\n"
        "待审答案如实说明了实验未执行的状态，没有将理论推断伪装成实际结果，"
        "并明确指出了未完成项和风险。"
    )

    assert review_needs_revision(review) is False


def test_review_revision_conclusion_requests_changes():
    review = "审查结论：需修订\n1. key result has no evidence."

    assert review_needs_revision(review) is True


def test_review_without_conclusion_falls_back_to_revision_markers():
    assert review_needs_revision("这段答案需要修订，因为缺少执行证据。") is True
