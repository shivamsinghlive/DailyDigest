"""Delta digest: what changed, from project memory, and who has to know."""
from digest_tool import config
from digest_tool.digest import what_changed
from digest_tool.memory import new_store, record
from digest_tool.ranker import rank_for_person, score_change

ANA = {"id": "U_ANA", "name": "Ana Lee", "role": "mechanical_engineer", "owns": ["base casting"]}
BEN = {"id": "U_BEN", "name": "Ben Ortiz", "role": "electrical_engineer", "owns": []}
EM = {"id": "U_EM", "name": "Em Grey", "role": "engineering_manager", "owns": []}


def change(day="2026-09-20", kind="update", **extra):
    return {"kind": kind, "day": day, "thread_ts": extra.pop("thread_ts", "T1"), "channel_name": "supply-chain",
            "type": "update", "summary": "s", "urgency": 2, "parts": {"base casting": 'mentioned as "casting"'},
            "subsystems": ["base"], "schedule_risk": False, "people_tagged": [], "people_mentioned": [],
            "participants": ["U_CY"], "authors_today": ["U_CY"], "root_author": "U_CY", **extra}


def state(mem=None):
    return {"memory": mem or new_store(), "phases": {"gripper": "DVT", "wrist": "DVT", "base": "DVT", "cabling": "DVT"},
            "owners": {"base casting": [{"person": "U_ANA", "confidence": "declared", "score": 5, "evidence": {}}]},
            "activity": {}, "channels": {}}


def test_an_updated_value_shows_before_and_after():
    c = change(fact_changes=[{"key": "fact:base casting.weight", "change": "UPDATED", "before": "790g", "after": "745g",
                              "certainty": "reported"}])
    badge, lines = what_changed(c, state())
    assert badge == "UPDATED" and "base casting weight: 790g → 745g" in lines


def test_a_constraint_set_60_days_ago_still_shows_on_a_new_problem():
    mem = new_store()
    record(mem, "2026-07-20", "constraint:base casting.max_weight", "CONSTRAINT", "750g", parts=["base casting"])
    badge, lines = what_changed(change(kind="new_problem", delta="NEW"), state(mem))
    assert badge == "NEW" and "Still in force: base casting max weight = 750g (since Mon Jul 20)" in lines


def test_a_linked_issue_says_how_old_the_problem_is():
    c = change(kind="new_problem", delta="UPDATED", linked_issue={"issue": "issue:T0", "since": "2026-09-08", "why": "x"})
    assert "Same issue as a problem first reported 12 days ago (Tue Sep 8)" in what_changed(c, state())[1]


def test_a_conflict_must_reach_the_owner_and_the_manager_only(team):
    c = change(fact_changes=[{"key": "constraint:base casting.max_weight", "change": "CONFLICTING", "before": "750g",
                              "after": "800g", "certainty": "unsure"}])
    st = state()
    for person, expected in ((ANA, True), (EM, True), (BEN, False)):
        _, must, reasons = score_change(c, person, st, team, {})
        assert must is expected
    assert what_changed(c, st)[0] == "CONFLICT"
    assert any("Needs clarification: base casting max weight is 750g, but this says 800g" in r
               for r in score_change(c, ANA, st, team, {})[2])


def test_a_change_from_another_channel_reaches_the_owner(team):
    c = change(kind="new_problem", type="problem", urgency=4)  # posted in #supply-chain, which Ana never posts in
    score, must, reasons = score_change(c, ANA, state(), team, {})
    assert must and any("supply-chain" in r for r in reasons)


def test_catch_up_shows_each_thread_once(team):
    days = ["2026-09-19", "2026-09-20", "2026-09-21"]
    timeline = {d: {"state": state(), "changes": [change(day=d, kind="problem_update", type="problem", urgency=4)]}
                for d in days}
    items = rank_for_person(ANA, "2026-09-21", timeline, team, since="2026-09-18")
    assert [i["change"]["thread_ts"] for i in items] == ["T1"]
    assert len(rank_for_person(ANA, "2026-09-21", timeline, team)) == 1  # a normal day: just that day's change
