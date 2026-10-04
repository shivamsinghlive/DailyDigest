"""Ranking rules that don't depend on ownership."""
from digest_tool import config
from digest_tool.ranker import score_change

MANAGER = {"id": "U_EM", "name": "Em Grey", "role": "engineering_manager", "owns": []}
ENGINEER = {"id": "U_ANA", "name": "Ana Lee", "role": "mechanical_engineer", "owns": []}
STATE = {"owners": {}, "phases": {"gripper": "DVT", "wrist": "DVT", "base": "DVT", "cabling": "DVT"},
         "activity": {}, "channels": {}}


def question(urgency):
    return {"kind": "new_question", "day": "2026-09-21", "thread_ts": "T1", "channel_name": "mech", "type": "question",
            "summary": "q", "urgency": urgency, "parts": {}, "subsystems": [], "schedule_risk": False,
            "people_tagged": [], "people_mentioned": [], "participants": [], "authors_today": [], "root_author": "U_X"}


def test_urgent_question_reaches_the_manager(team):
    score, _, reasons = score_change(question(4), MANAGER, STATE, team, {})
    assert score >= config.MIN_SCORE and any("blocked" in r for r in reasons)


def test_routine_question_does_not(team):
    score, _, _ = score_change(question(3), MANAGER, STATE, team, {})
    assert score < config.MIN_SCORE


def test_urgent_question_is_not_everyones_business(team):
    score, _, _ = score_change(question(4), ENGINEER, STATE, team, {})
    assert score < config.MIN_SCORE


def problem(parts=None):
    return {**question(4), "kind": "new_problem", "type": "problem", "parts": parts or {}}


def test_role_alone_does_not_send_an_urgent_problem_to_an_engineer(team):
    score, must, _ = score_change(problem(), ENGINEER, STATE, team, {})  # DVT: problems matter to MEs, but not theirs
    assert score < config.MIN_SCORE and not must


def test_the_manager_still_gets_it_on_role_alone(team):
    score, _, reasons = score_change(problem(), MANAGER, STATE, team, {})
    assert score >= config.MIN_SCORE and any("priority for engineering managers" in r for r in reasons)


def test_with_a_personal_link_role_adds_up(team):
    state = {**STATE, "owners": {"base casting": [{"person": "U_ANA", "confidence": "possible", "score": 2, "evidence": {}}]}}
    score, _, reasons = score_change(problem({"base casting": 'mentioned as "base"'}), ENGINEER, state, team, {})
    assert score >= config.MIN_SCORE and any("priority for mechanical engineers" in r for r in reasons)
