"""Closing problems across threads: a decision or fix that names the same part, within the window."""
import pytest

from digest_tool import config
from digest_tool.notebook import close_linked_problems, is_schedule_risk, linkable, resolving_parts


@pytest.fixture(autouse=True)
def fixed_window(monkeypatch):
    monkeypatch.setattr(config, "LINK_WINDOW_DAYS", 5)


def thread(*msgs):
    return {"thread_ts": msgs[0]["ts"], "channel_name": msgs[0]["channel_name"], "messages": list(msgs)}


def extraction(kind, parts=()):
    return {"type": kind, "parts": {p: f'mentioned as "{p}"' for p in parts}}


def state_with_problem(parts, last_active="2026-09-18"):
    problem = {"thread_ts": "P1", "channel_name": "elec", "summary": "driver overheats", "urgency": 4,
               "parts": {}, "linkable_parts": parts, "since": "2026-09-17", "last_day": last_active,
               "last_active": last_active}
    return {"open_problems": {"P1": problem}, "closed_problems": []}


def test_whole_subsystem_parts_and_topics_dont_link(catalog):
    assert linkable("motor driver", catalog)
    assert not linkable("wrist assembly", catalog)   # stands for the whole wrist
    assert not linkable("gripper", catalog)          # part name == subsystem name
    assert not linkable("firmware", catalog)         # a topic


def test_decision_in_another_thread_closes_the_problem(team, make_msg):
    t = thread(make_msg("ok decision: we switch to the TMC9660 for DVT", day=21))
    state = state_with_problem(["motor driver"])
    closed = close_linked_problems(state, t, resolving_parts(t, extraction("decision"), "2026-09-21", team), "2026-09-21")
    assert [c["thread_ts"] for c in closed] == ["P1"]
    assert closed[0]["via_parts"] == ["motor driver"] and closed[0]["closed_by"] == t["thread_ts"]
    assert state["open_problems"] == {}


def test_too_long_after_the_problem_went_quiet_doesnt_close(team, make_msg):
    t = thread(make_msg("ok decision: we switch to the TMC9660 for DVT", day=25))
    state = state_with_problem(["motor driver"], last_active="2026-09-18")  # 7 days earlier
    closed = close_linked_problems(state, t, resolving_parts(t, extraction("decision"), "2026-09-25", team), "2026-09-25")
    assert closed == [] and "P1" in state["open_problems"]


def test_fix_word_only_counts_for_parts_in_the_same_sentence(team, make_msg):
    t = thread(make_msg("pads are fixed, closing out the grip issue. since the driver board is changing I'll help next wk", day=21))
    assert resolving_parts(t, extraction("update"), "2026-09-21", team) == set()


def test_fix_reported_in_an_update_closes(team, make_msg):
    t = thread(make_msg("swapped the driver board on unit 2, runs cool now", day=21))
    assert resolving_parts(t, extraction("update"), "2026-09-21", team) == {"motor driver"}


def test_a_question_is_not_a_fix(team, make_msg):
    t = thread(make_msg("should we have swapped the driver board?", day=21))
    assert resolving_parts(t, extraction("update"), "2026-09-21", team) == set()


def test_decision_that_puts_the_schedule_at_risk_settles_nothing(team, make_msg):
    t = thread(make_msg("DVT build date is now TBD until we secure TMC9660s", day=21))
    assert resolving_parts(t, extraction("decision", ["motor driver"]), "2026-09-21", team) == set()


@pytest.mark.parametrize("text", ["moving DVT build start from 10/27 to 11/10", "pushing the build out a week",
                                  "DVT build slipped to late Nov"])
def test_schedule_moves_are_schedule_risks(team, make_msg, text):
    assert is_schedule_risk(thread(make_msg(text)), extraction("update"))


def test_a_date_without_a_move_is_not(team, make_msg):
    assert not is_schedule_risk(thread(make_msg("DVT build is on 11/10, kickoff at 9")), extraction("update"))
