"""Facts with values: updates keep history, conflicts never silently overwrite, names stay consistent."""
from digest_tool import facts
from digest_tool.facts import apply_facts, canonical, normalized
from digest_tool.memory import history, latest, new_store, record

THREAD = {"thread_ts": "T9", "channel_name": "mech"}
OWNERS = {"base casting": [{"person": "U_ANA", "confidence": "declared"}]}


def fact(value, certainty, said_by="Ben Ortiz", kind="constraint", entity="base casting", attribute="max_weight"):
    return {"entity": entity, "attribute": attribute, "value": value, "kind": kind, "certainty": certainty, "said_by": said_by}


def store_with_target():
    m = new_store()
    record(m, "2026-09-01", "constraint:base casting.max_weight", "CONSTRAINT", "750g", parts=["base casting"])
    return m


def test_an_unsure_statement_does_not_overwrite_the_target(team):
    m = store_with_target()
    out = apply_facts(m, [fact("800g", "unsure")], THREAD, "2026-09-20", team, OWNERS)
    cur = latest(m, "constraint:base casting.max_weight")
    assert out[0]["change"] == "CONFLICTING" and cur["value"] == "750g"
    assert cur["conflicts"][0]["value"] == "800g" and "Ben Ortiz" in cur["conflicts"][0]["note"]


def test_a_reported_target_needs_the_owner_or_the_manager(team):
    m = store_with_target()
    assert apply_facts(m, [fact("800g", "reported", said_by="Cy Park")], THREAD, "2026-09-20", team, OWNERS)[0]["change"] == "CONFLICTING"
    assert apply_facts(m, [fact("700g", "reported", said_by="Ana Lee")], THREAD, "2026-09-21", team, OWNERS)[0]["change"] == "UPDATED"
    assert latest(m, "constraint:base casting.max_weight")["value"] == "700g"


def test_a_decision_supersedes_and_keeps_the_old_value(team):
    m = store_with_target()
    apply_facts(m, [fact("720g", "decided", said_by="Cy Park")], THREAD, "2026-09-22", team, OWNERS)
    assert [(e["change"], e["before"], e["after"]) for e in history(m, "constraint:base casting.max_weight")][-1] == ("UPDATED", "750g", "720g")


def test_measurements_update_when_reported(team):
    m = new_store()
    for day, v in (("2026-09-01", "850g"), ("2026-09-05", "790g"), ("2026-09-12", "745g")):
        apply_facts(m, [fact(v, "reported", kind="fact", attribute="weight")], THREAD, day, team, OWNERS)
    assert [e["after"] for e in history(m, "fact:base casting.weight")] == ["850g", "790g", "745g"]


def test_same_value_written_differently_is_not_a_change(team):
    assert normalized("10k") == normalized("10000") and normalized("~1.5 deg") == normalized("~1.5deg")
    m = new_store()
    apply_facts(m, [fact("10k", "reported", kind="fact", attribute="flex_cycles")], THREAD, "2026-09-01", team, OWNERS)
    assert apply_facts(m, [fact("10000", "reported", kind="fact", attribute="flex_cycles")], THREAD, "2026-09-02", team, OWNERS) == []


def test_statuses_and_questions_about_unknown_values_are_not_facts(team):
    assert canonical(fact("approved", "decided"), team) is None
    assert canonical(fact("TBD", "decided"), team) is not None
    assert apply_facts(new_store(), [fact("800g", "question")], THREAD, "2026-09-01", team, OWNERS) == []


def test_a_new_name_for_an_existing_quantity_is_merged(team, monkeypatch):
    m = new_store()
    record(m, "2026-09-01", "constraint:project.dvt_build_date", "CONSTRAINT", "10/26")
    monkeypatch.setattr(facts, "call_provider", lambda *a: {"same_as": "constraint:project.dvt_build_date"})
    out = apply_facts(m, [fact("TBD", "decided", entity="project", attribute="dvt_build_schedule")], THREAD,
                      "2026-09-24", team, OWNERS, provider="anthropic", cache={})
    assert out[0]["key"] == "constraint:project.dvt_build_date" and out[0]["before"] == "10/26"


def test_a_target_is_never_merged_into_a_measurement(team, monkeypatch):
    m = new_store()
    record(m, "2026-09-01", "fact:base casting.max_weight_measured", "FACT", "790g")
    monkeypatch.setattr(facts, "call_provider", lambda *a: (_ for _ in ()).throw(AssertionError("no candidates expected")))
    out = apply_facts(m, [fact("750g", "decided", attribute="max_weight")], THREAD, "2026-09-02", team, OWNERS,
                      provider="anthropic", cache={})
    assert out[0]["key"] == "constraint:base casting.max_weight"
