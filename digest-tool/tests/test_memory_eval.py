"""The long-horizon memory scorer reads the answer key by message timestamps."""
from digest_tool.evaluate import score_memory, value_match
from digest_tool.memory import conflict, new_store, record, resolve, reopen

MSGS = [{"ts": t, "thread_ts": th} for t, th in
        (("1", None), ("2", "1"), ("3", None), ("4", None), ("5", None), ("6", None))]


def timeline():
    m = new_store()
    record(m, "2026-01-05", "fact:pack.runtime", "FACT", "6h", thread_ts="1")
    record(m, "2026-01-20", "fact:pack.runtime", "FACT", "7.5 h", thread_ts="3")
    record(m, "2026-01-06", "constraint:pack.min_runtime", "CONSTRAINT", "8h", thread_ts="1")
    conflict(m, "2026-01-25", "constraint:pack.min_runtime", "6h", thread_ts="4")
    record(m, "2026-01-07", "issue:5", "ISSUE", "trigger sticks")
    resolve(m, "2026-01-10", "issue:5", "5")
    reopen(m, "2026-02-01", "issue:5", "6")
    state = {"memory": m, "issue_of": {"5": "issue:5", "6": "issue:5"}}
    return {"2026-02-01": {"state": state, "changes": []}}


def test_value_match_ignores_formatting():
    assert value_match("7.5 h", "7.5h") and value_match("6.5 hrs", "6.5 hours") and not value_match("6h", "8h")


def test_scores_values_conflicts_and_issues():
    truth = {
        "value_history": [{"name": "runtime", "source_ts": "2", "date": "2026-01-05", "value": "6h"},
                          {"name": "runtime", "source_ts": "3", "date": "2026-01-20", "value": "7.5h"}],
        "as_of": [{"name": "runtime", "first_source_ts": "2", "date": "2026-01-10", "expected_value": "6h"},
                  {"name": "runtime", "first_source_ts": "2", "date": "2026-01-30", "expected_value": "7.5h"}],
        "conflicts": [{"name": "min runtime", "source_ts": "4", "date": "2026-01-25", "stated_value": "6h", "value_in_force": "8h"}],
        "issues": [{"name": "trigger", "source_ts": ["5", "6"], "resolved_date": "2026-01-10",
                    "reopened_date": "2026-02-01", "status_at_end": "open"}],
    }
    counts, _ = score_memory(truth, timeline(), MSGS)
    assert all(ok == total for name, (ok, total) in counts.items() if "lower is better" not in name), counts
