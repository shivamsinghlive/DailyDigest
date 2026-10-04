"""Design changes after freeze: a new revision of a part whose subsystem is in DVT+, with no ECO in sight."""
from digest_tool.notebook import changed_after_freeze

DAY = "2026-09-21"


def thread(*msgs):
    return {"thread_ts": msgs[0]["ts"], "channel_name": "mech", "messages": list(msgs)}


def state(**phases):
    return {"phases": {"gripper": "EVT", "wrist": "EVT", "base": "EVT", "cabling": "EVT", **phases}}


def test_new_revision_of_a_frozen_part_is_flagged(team, make_msg):
    t = thread(make_msg("base casting: added a rib, pushed rev F"))
    assert changed_after_freeze(t, DAY, state(base="DVT"), team) == ["base casting"]


def test_before_freeze_a_revision_is_just_work(team, make_msg):
    t = thread(make_msg("base casting: added a rib, pushed rev F"))
    assert changed_after_freeze(t, DAY, state(base="EVT"), team) == []


def test_an_eco_in_the_thread_means_it_is_handled(team, make_msg):
    root = make_msg("base casting rib change, ECO-12 submitted", day=20)
    t = thread(root, make_msg("pushed rev F", thread_ts=root["ts"]))
    assert changed_after_freeze(t, DAY, state(base="DVT"), team) == []


def test_mentioning_a_revision_is_not_releasing_one(team, make_msg):
    t = thread(make_msg("releasing the base casting POs monday, casting at rev E"))
    assert changed_after_freeze(t, DAY, state(base="DVT"), team) == []


def test_the_revised_part_is_the_thread_subject_not_what_a_follow_up_touches(team, make_msg):
    root = make_msg("tweaking the base casting: thicker boss", day=20)
    t = thread(root, make_msg("moved the boss 2mm to clear the J4 crimps, pushed rev F", thread_ts=root["ts"]))
    assert changed_after_freeze(t, DAY, state(base="DVT", cabling="DVT"), team) == ["base casting"]
