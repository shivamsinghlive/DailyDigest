"""Issue identity: threads about the same problem share one issue, wherever they are posted."""
from digest_tool import issues
from digest_tool.memory import history, new_store, record
from digest_tool.notebook import build_notebook


def problem(summary, parts):
    return {"summary": summary, "parts": {p: f'mentioned as "{p}"' for p in parts}}


def store_with(*issues_):
    m = new_store()
    for key, summary, parts in issues_:
        record(m, "2026-09-15", key, "ISSUE", summary, parts=parts)
    return m


def test_different_words_same_issue_via_llm_verdict(team, monkeypatch):
    m = store_with(("issue:A", "wrist driver board runs hot under load", ["motor driver"]))
    monkeypatch.setattr(issues, "call_provider", lambda *a: {"same_as": "issue:A", "reason": "same thermal problem"})
    key, why = issues.find_issue(m, problem("TMC9660 hits 82C at full current", ["motor driver"]), team,
                                 "2026-09-20", "issue:B", "anthropic", {})
    assert key == "issue:A" and "same thermal problem" in why


def test_the_llm_can_only_pick_from_the_shortlist(team, monkeypatch):
    m = store_with(("issue:A", "driver board runs hot", ["motor driver"]))
    monkeypatch.setattr(issues, "call_provider", lambda *a: {"same_as": "issue:INVENTED", "reason": "?"})
    assert issues.find_issue(m, problem("driver board fails", ["motor driver"]), team, "2026-09-20",
                             "issue:B", "anthropic", {}) == (None, None)


def test_no_shared_specific_part_means_no_link(team, monkeypatch):
    m = store_with(("issue:A", "J4 latch keeps popping", ["J4 connector"]))
    monkeypatch.setattr(issues, "call_provider", lambda *a: (_ for _ in ()).throw(AssertionError("no LLM call expected")))
    assert issues.find_issue(m, problem("base casting cracked", ["base casting"]), team, "2026-09-20",
                             "issue:B", "anthropic", {}) == (None, None)


def test_without_an_llm_only_near_identical_wording_links(team):
    m = store_with(("issue:A", "base casting cracked at the mounting boss", ["base casting"]))
    same = problem("base casting cracked at the mounting boss again", ["base casting"])
    other = problem("base casting delivery is two weeks late", ["base casting"])
    assert issues.find_issue(m, same, team, "2026-09-20", "issue:B", "none", {})[0] == "issue:A"
    assert issues.find_issue(m, other, team, "2026-09-20", "issue:B", "none", {}) == (None, None)


def test_one_fix_closes_every_thread_about_the_issue(team, make_msg):
    a = make_msg("the base casting cracked at the mounting boss, this is a problem", day=20, channel_name="mech")
    b = make_msg("base casting cracked at the mounting boss again on unit 4, problem", user="U_CY", day=21,
                 channel_name="supply-chain")
    fix = make_msg("decision: we swap the casting supplier for DVT", user="U_ANA", day=22, thread_ts=a["ts"],
                   channel_name="mech")
    timeline = build_notebook([a, b, fix], team, "none", cache={})
    end = timeline["2026-09-22"]["state"]
    assert end["issue_of"][b["ts"]] == end["issue_of"][a["ts"]] == f"issue:{a['ts']}"
    assert end["open_problems"] == {}                              # b closed with a
    assert [e["change"] for e in history(end["memory"], f"issue:{a['ts']}")] == ["NEW", "UPDATED", "RESOLVED"]
    linked = timeline["2026-09-21"]["changes"][0]["linked_issue"]
    assert linked["issue"] == f"issue:{a['ts']}" and linked["since"] == "2026-09-20"
