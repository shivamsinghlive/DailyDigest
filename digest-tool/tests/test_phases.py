"""Phase detection: natural announcements change the right subsystem's phase; other messages don't."""
import pytest

from digest_tool.notebook import build_notebook, detect_phase_changes

DAY = "2026-09-21"


def thread_of(*messages):
    return {"messages": list(messages)}


def test_design_freeze_moves_only_the_named_subsystems(team, make_msg):
    m = make_msg("design freeze for the gripper and the base: both officially in DVT as of today")
    assert detect_phase_changes(thread_of(m), DAY, team) == {"gripper": "DVT", "base": "DVT"}


def test_later_sentence_can_hold_a_subsystem_back(team, make_msg):
    m = make_msg("EVT exit review done ✅ gripper is officially in DVT. the wrist stays in EVT till the motor is sorted.")
    assert detect_phase_changes(thread_of(m), DAY, team) == {"gripper": "DVT", "wrist": "EVT"}


@pytest.mark.parametrize("text, expected", [
    ("EVT build done for the wrist", {"wrist": "DVT"}),             # EVT done -> next phase
    ("DVT units shipped for the base", {"base": "DVT"}),
    ("wrist moves to DVT as of today", {"wrist": "DVT"}),
])
def test_other_natural_phrasings(team, make_msg, text, expected):
    assert detect_phase_changes(thread_of(make_msg(text)), DAY, team) == expected


def test_announcement_naming_no_subsystem_applies_to_all(team, make_msg):
    m = make_msg("we are officially in DVT 🎉")
    assert detect_phase_changes(thread_of(m), DAY, team) == {s: "DVT" for s in team["catalog"]["subsystems"]}


@pytest.mark.parametrize("text", [
    "pizza in the kitchen 🍕",
    "DVT build target is still 10/26",                 # mentions a phase, announces nothing
    "EVT exit review is mon 9/21, get issues in jira",  # a review is scheduled, not done
    "any change on a DVT subsystem needs an ECO",
])
def test_unrelated_messages_change_nothing(team, make_msg, text):
    assert detect_phase_changes(thread_of(make_msg(text)), DAY, team) == {}


def test_only_messages_from_that_day_count(team, make_msg):
    yesterday = make_msg("gripper is officially in DVT", day=20)
    assert detect_phase_changes(thread_of(yesterday), DAY, team) == {}


def test_notebook_tracks_phase_per_subsystem(team, make_msg):
    """End to end through the notebook (keyword extraction, no LLM, no cache)."""
    messages = [
        make_msg("lunch anyone?", day=20, user="U_BEN"),
        make_msg("design freeze for the gripper: officially in DVT. the wrist stays in EVT.", day=21, user="U_CY"),
        make_msg("DVT build target is still 10/26", day=22, user="U_ANA"),
    ]
    timeline = build_notebook(messages, team, provider="none", cache={})

    assert timeline["2026-09-20"]["state"]["phases"] == {"gripper": "EVT", "wrist": "EVT", "base": "EVT", "cabling": "EVT"}
    assert timeline["2026-09-21"]["state"]["phases"] == {"gripper": "DVT", "wrist": "EVT", "base": "EVT", "cabling": "EVT"}
    assert timeline["2026-09-22"]["state"]["phases"] == timeline["2026-09-21"]["state"]["phases"]
    phase_changes = [c for day in timeline.values() for c in day["changes"] if c["kind"] == "phase_change"]
    assert len(phase_changes) == 1 and phase_changes[0]["new_phases"] == {"gripper": "DVT"}


def test_a_planned_freeze_is_not_a_phase_change(team, make_msg):
    t = thread_of(make_msg("reminder: design freeze for DVT is this Fri (10/10) EOD. after that changes need an ECO", day=21))
    assert detect_phase_changes(t, DAY, team) == {}


def test_a_finished_freeze_is(team, make_msg):
    t = thread_of(make_msg("Mechanical design freeze for DVT is DONE as of today. thanks all", day=21))
    assert set(detect_phase_changes(t, DAY, team).values()) == {"DVT"}


def test_moving_to_a_phase_next_week_is_a_plan(team, make_msg):
    t = thread_of(make_msg("we'll move the gripper to DVT next week", day=21))
    assert detect_phase_changes(t, DAY, team) == {}
