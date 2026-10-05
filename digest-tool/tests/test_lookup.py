"""Lookup: a part by any nickname, a person, or words; conversations within a date range."""
from digest_tool.lookup import conversations, part_profile, person_profile, query_words, resolve
from digest_tool.notebook import build_notebook


def timeline_of(team, make_msg):
    msgs = [make_msg("the wrist conn latch keeps popping on unit 2, this is a problem", day=20, channel_name="mech"),
            make_msg("decision: we order the base casting from the second foundry", user="U_CY", day=22,
                     channel_name="supply-chain")]
    return build_notebook(msgs, team, "none", cache={})  # keyword rules: no LLM, no cache


def test_a_part_is_found_by_any_nickname_or_typo(team):
    assert resolve("wrist conn", team)[0] == ["J4 connector"]
    assert resolve("43045-0412", team)[0] == ["J4 connector"]
    assert resolve("molex conector", team)[0] == ["J4 connector"]   # typo
    assert resolve("firmware", team)[0] == ["firmware"]             # a topic, typed in full


def test_a_person_is_found_by_name_or_first_name(team):
    assert [p["id"] for p in resolve("ana", team)[1]] == ["U_ANA"]
    assert [p["id"] for p in resolve("Ben Ortiz", team)[1]] == ["U_BEN"]


def test_conversations_about_a_part_within_a_date_range(team, make_msg):
    tl = timeline_of(team, make_msg)
    found = conversations(tl, "2026-09-01", "2026-09-30", parts=["J4 connector"])
    assert len(found) == 1 and found[0]["channel_name"] == "mech"
    assert conversations(tl, "2026-09-21", "2026-09-30", parts=["J4 connector"]) == []   # before the range


def test_conversations_by_words_and_by_person(team, make_msg):
    tl = timeline_of(team, make_msg)
    assert [c["channel_name"] for c in conversations(tl, "2026-09-01", "2026-09-30", words=query_words("second foundry"))] \
        == ["supply-chain"]
    assert [c["channel_name"] for c in conversations(tl, "2026-09-01", "2026-09-30", people=["U_CY"])] == ["supply-chain"]


def test_part_and_person_profiles(team, make_msg):
    tl = timeline_of(team, make_msg)
    state = tl["2026-09-22"]["state"]
    j4 = part_profile("J4 connector", state, team, "2026-09-22")
    assert j4["subsystem"] == "cabling" and j4["phase"] == "EVT" and len(j4["open_issues"]) == 1
    cy = person_profile(next(p for p in team["people"] if p["id"] == "U_CY"), state, tl, "2026-09-22")
    assert [c["channel_name"] for c in cy["started"]] == ["supply-chain"]
