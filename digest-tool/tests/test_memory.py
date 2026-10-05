"""Project memory: history is kept, current state is clear, nothing is silently overwritten."""
from digest_tool.memory import (current_state, history, latest, new_store, record, reopen, resolve, retrieve,
                                status_as_of, value_as_of)
from digest_tool.notebook import build_notebook


# ---------- the store on its own ----------

def test_a_fact_updated_days_later_supersedes_the_old_value():
    m = new_store()
    assert record(m, "2026-09-01", "gripper:weight", "FACT", "850g") == "NEW"
    assert record(m, "2026-09-05", "gripper:weight", "FACT", "790g") == "UPDATED"
    assert record(m, "2026-09-12", "gripper:weight", "FACT", "745g") == "UPDATED"
    versions = sorted((v for v in m["memories"].values() if v["key"] == "gripper:weight"), key=lambda v: v["version"])
    assert [(v["value"], v["status"], v["valid_to"]) for v in versions] == [
        ("850g", "SUPERSEDED", "2026-09-05"), ("790g", "SUPERSEDED", "2026-09-12"), ("745g", "ACTIVE", None)]
    assert [(e["before"], e["after"]) for e in history(m, "gripper:weight")] == [(None, "850g"), ("850g", "790g"), ("790g", "745g")]


def test_what_is_true_now_and_what_was_true_then():
    m = new_store()
    record(m, "2026-09-01", "gripper:weight", "FACT", "850g")
    record(m, "2026-09-05", "gripper:weight", "FACT", "790g")
    assert value_as_of(m, "gripper:weight", "2026-09-03") == "850g"
    assert value_as_of(m, "gripper:weight", "2026-09-30") == "790g"
    assert [x["value"] for x in current_state(m)] == ["790g"]
    assert [x["value"] for x in current_state(m, as_of="2026-09-02")] == ["850g"]


def test_the_same_value_again_is_not_a_change():
    m = new_store()
    record(m, "2026-09-01", "phase:base", "MILESTONE", "EVT", thread_ts="T1")
    assert record(m, "2026-09-03", "phase:base", "MILESTONE", "EVT", thread_ts="T2") == "UNCHANGED"
    assert len(history(m, "phase:base")) == 1
    assert latest(m, "phase:base")["source_threads"] == ["T1", "T2"]  # more evidence, same fact


def test_resolved_issue_can_reopen_and_its_status_is_known_for_any_day():
    m = new_store()
    record(m, "2026-09-01", "issue:T1", "ISSUE", "casting cracks")
    assert resolve(m, "2026-09-04", "issue:T1", "T9", note="fixed in #supply") == "RESOLVED"
    assert reopen(m, "2026-09-10", "issue:T1", "T1") == "REOPENED"
    assert [e["change"] for e in history(m, "issue:T1")] == ["NEW", "RESOLVED", "REOPENED"]
    assert [status_as_of(m, "issue:T1", d) for d in ("2026-09-02", "2026-09-05", "2026-09-11")] == ["ACTIVE", "RESOLVED", "ACTIVE"]


def test_old_memories_never_expire_and_retrieval_stays_bounded():
    m = new_store()
    record(m, "2026-03-01", "constraint:base", "CONSTRAINT", "no welds", parts=["base casting"])  # six months old
    for i in range(30):
        record(m, "2026-09-01", f"issue:N{i}", "ISSUE", f"noise {i}", parts=["J4 connector"])
    found = retrieve(m, parts=["base casting"], limit=5)
    assert [x["key"] for x in found] == ["constraint:base"]          # still there, and found by what it's about
    assert len(retrieve(m, parts=["J4 connector"], limit=5)) == 5     # never the whole log


# ---------- through the notebook replay ----------

def replay(team, msgs):
    return build_notebook(msgs, team, "none", cache={})  # keyword rules: no LLM, no cache


def test_problem_resolved_in_another_channel_then_reopened(team, make_msg):
    problem = make_msg("the base casting cracked on unit 2, this is a problem", day=20, channel_name="mech")
    fix = make_msg("decision: switched the base casting vendor, the new lot is fixed", user="U_CY", day=21,
                   channel_name="supply-chain")
    again = make_msg("base casting cracked again on unit 4, same problem", day=23, thread_ts=problem["ts"],
                     channel_name="mech")
    timeline = replay(team, [problem, fix, again])
    mem = timeline["2026-09-23"]["state"]["memory"]
    events = history(mem, f"issue:{problem['ts']}")
    assert [e["change"] for e in events][-2:] == ["RESOLVED", "REOPENED"]
    assert events[-2]["thread_ts"] == fix["ts"] and "supply-chain" in events[-2]["note"]
    assert [c["delta"] for c in timeline["2026-09-23"]["changes"]] == ["REOPENED"]


def test_phase_change_supersedes_the_old_phase(team, make_msg):
    timeline = replay(team, [make_msg("design freeze for the base: officially in DVT as of today", day=21)])
    mem = timeline["2026-09-21"]["state"]["memory"]
    assert [(e["before"], e["after"]) for e in history(mem, "phase:base")] == [(None, "EVT"), ("EVT", "DVT")]
    change = timeline["2026-09-21"]["changes"][0]
    assert change["delta"] == "UPDATED" and change["before"] == {"base": "EVT"}
