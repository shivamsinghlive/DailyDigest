"""Ownership inference: declared owners win; inferred owners get the expected confidence level."""
import pytest

from digest_tool import config
from digest_tool.notebook import infer_owners, ownership_evidence

DAY = "2026-09-21"


@pytest.fixture(autouse=True)
def fixed_thresholds(monkeypatch):
    """Pin the settings so these tests don't break when config.py is retuned."""
    monkeypatch.setattr(config, "OWNERSHIP_EVIDENCE", {"mention": 1.0, "question": 0.5, "answer": 2.0, "asked_about": 1.5})
    monkeypatch.setattr(config, "OWNERSHIP_HALF_LIFE_DAYS", 7)
    monkeypatch.setattr(config, "LIKELY_MIN_SCORE", 3.0)
    monkeypatch.setattr(config, "LIKELY_MIN_SHARE", 0.4)
    monkeypatch.setattr(config, "POSSIBLE_MIN_SCORE", 1.5)
    monkeypatch.setattr(config, "POSSIBLE_MIN_SHARE", 0.25)


def state_with(evidence):
    """evidence: {person: {part: [kind, ...]}}, all dated today."""
    return {"ownership_evidence": {person: {part: [[DAY, k] for k in kinds] for part, kinds in parts.items()}
                                   for person, parts in evidence.items()}}


def levels(owners, part):
    return {o["person"]: o["confidence"] for o in owners.get(part, [])}


# ---------- declared owners ----------

def test_declared_owner_listed_even_with_no_activity(team):
    owners = infer_owners(state_with({}), DAY, team)
    assert levels(owners, "gripper") == {"U_ANA": "declared"}


def test_declared_owner_comes_first_even_when_someone_else_talks_more(team):
    owners = infer_owners(state_with({"U_BEN": {"gripper": ["answer", "answer", "mention"]}}), DAY, team)  # Ben: 5.0
    first = owners["gripper"][0]
    assert (first["person"], first["confidence"]) == ("U_ANA", "declared")
    assert levels(owners, "gripper")["U_BEN"] == "likely"  # strong evidence can still add a co-owner


def test_weak_evidence_does_not_add_owners_to_a_declared_part(team):
    owners = infer_owners(state_with({"U_BEN": {"gripper": ["mention", "mention"]}}), DAY, team)  # Ben: 2.0
    assert levels(owners, "gripper") == {"U_ANA": "declared"}


# ---------- inferred levels on an undeclared part ----------

def test_confidence_levels_on_undeclared_part(team):
    owners = infer_owners(state_with({
        "U_BEN": {"J4 connector": ["answer", "answer"]},   # 4.0 of 6.0 -> likely
        "U_CY": {"J4 connector": ["mention", "mention"]},  # 2.0 of 6.0 -> possible
        "U_ANA": {"J4 connector": []},
    }), DAY, team)
    assert levels(owners, "J4 connector") == {"U_BEN": "likely", "U_CY": "possible"}


def test_a_single_question_is_not_ownership(team):
    owners = infer_owners(state_with({"U_ANA": {"J4 connector": ["question"]}}), DAY, team)  # 0.5
    assert "J4 connector" not in owners


def test_old_evidence_counts_less(team):
    two_weeks_ago = {"ownership_evidence": {"U_BEN": {"J4 connector": [["2026-09-07", "answer"], ["2026-09-07", "answer"]]}}}
    owners = infer_owners(two_weeks_ago, DAY, team)  # 4.0 halved twice = 1.0, below "possible"
    assert "J4 connector" not in owners


# ---------- where the evidence comes from ----------

def test_evidence_from_a_question_and_its_answer(team, make_msg):
    question = make_msg("<@U_BEN> J4 latch keeps popping, can you look?", user="U_ANA")
    answer = make_msg("yeah the J4 latch is flimsy, printing a clip", user="U_BEN", thread_ts=question["ts"])
    answered = set()
    facts = ownership_evidence(question, question, team, answered) + ownership_evidence(answer, question, team, answered)
    assert ("U_ANA", "J4 connector", "question") in facts      # asking
    assert ("U_BEN", "J4 connector", "asked_about") in facts   # being @-tagged about it
    assert ("U_BEN", "J4 connector", "answer") in facts        # answering
    assert ("U_BEN", "J4 connector", "mention") in facts


def test_naming_someone_is_not_ownership_evidence(team, make_msg):
    # Third-person mentions ("Cy is chasing the J4 order") say nothing about who owns the part.
    m = make_msg("Cy is chasing the J4 order", user="U_ANA")
    facts = ownership_evidence(m, m, team, set())
    assert not any(person == "U_CY" for person, _, _ in facts)


def test_answer_credit_counted_once_per_thread(team, make_msg):
    question = make_msg("anyone know the J4 pinout?", user="U_ANA")
    r1 = make_msg("J4 pin 1 is VM", user="U_BEN", thread_ts=question["ts"])
    r2 = make_msg("and J4 pin 2 is GND", user="U_BEN", thread_ts=question["ts"])
    answered = set()
    facts = ownership_evidence(r1, question, team, answered) + ownership_evidence(r2, question, team, answered)
    assert sum(1 for f in facts if f == ("U_BEN", "J4 connector", "answer")) == 1
