"""Honest testing. Reports, separately for tuned and holdout stories:

  1. Alerts       did the right people see the right threads on the right day? vs two baselines
  2. Team Pulse   did whole-team items reach the pulse, and did personal ones stay out?
  3. Nicknames    were part mentions mapped to the right official part (catalog matcher vs LLM)?
                  were parts missing from the catalog flagged as unknown?
  4. Ownership    inferred owners vs data/true_owners.json (only this file reads it)

tuned    data/ground_truth.json: stories that existed while scoring was built and tuned
holdout  data/ground_truth_holdout.json: written by a separate agent that never saw the code,
         merged after the scoring was frozen. Nothing was changed after seeing these results.

Scoring unit for alerts: (person, thread, day).
- seen:     the thread was in their digest that day (Team Pulse or For You)
- For You:  the personalized section only. The pulse goes to everyone by design, so precision
            is measured on For You.
- People who already posted in the thread are skipped either way: they saw it.
"""
import json
import sys

from . import config
from .catalog import match_parts
from .digest import team_pulse
from .extract import extract, load_cache
from .notebook import build_notebook, changes_on, declared_owners, state_as_of
from .ranker import CATEGORY, IMPORTANT_FOR, phases_for, rank_for_person
from .slack_loader import all_days, day_of, end_of_day, group_into_threads, load_messages, load_team, threads_active_on

SPLITS = ("tuned", "holdout")
# Holdout per-item detail (event descriptions, nicknames used) is hidden by default: seeing it
# invites tuning on it. Aggregate numbers are always shown.
SHOW_HOLDOUT = "--show-holdout" in sys.argv


def load_ground_truth():
    # Either file may be missing: an imported dataset (scripts/import_slack_export.py) has only a holdout.
    empty = {"events": [], "noise": [], "mentions": []}
    gt = {}
    for split, name in (("tuned", "ground_truth.json"), ("holdout", "ground_truth_holdout.json")):
        path = config.DATA_DIR / name
        gt[split] = json.load(open(path)) if path.exists() else empty
    return gt


# ---------- systems: each returns (seen, for_you, pulse); dicts keyed (person, day) / day -> set of thread_ts ----------

def ours(timeline, team):
    seen, mine, pulses = {}, {}, {}
    for d in timeline:
        pulse = pulses[d] = {i["thread_ts"] for i in team_pulse(d, timeline, team)}
        for p in team["people"]:
            mine[(p["id"], d)] = {i["change"]["thread_ts"] for i in rank_for_person(p, d, timeline, team, exclude=pulse)}
            seen[(p["id"], d)] = mine[(p["id"], d)] | pulse
    return seen, mine, pulses


def everyone_gets_everything(messages, team):
    out = {}
    for d in all_days(messages):
        for t in threads_active_on(messages, d):
            authors_today = {m.get("user") for m in t["messages"] if day_of(m["ts"]) == d}
            for p in team["people"]:
                if p["id"] not in authors_today:  # same rule as ours: you've seen what you just wrote in
                    out.setdefault((p["id"], d), set()).add(t["thread_ts"])
    return out, out, {}


def role_only(timeline, team):
    out = {}
    for d in timeline:
        state = state_as_of(timeline, d)
        for p in team["people"]:
            items = [c for c in changes_on(timeline, d)
                     if p["id"] not in c["authors_today"]
                     and (c["kind"] == "phase_change" or any(CATEGORY[c["kind"]] in IMPORTANT_FOR[p["role"]].get(ph, [])
                                                              for ph in phases_for(c, state, team).values()))]
            items.sort(key=lambda c: c["urgency"], reverse=True)
            out[(p["id"], d)] = {c["thread_ts"] for c in items[:config.TOP_N]}
    return out, out, {}


# ---------- 1. alerts ----------

def participants_as_of(messages, thread_ts, day):
    for t in group_into_threads(messages, until=end_of_day(day)):
        if t["thread_ts"] == thread_ts:
            return set(t["participants"])
    return set()


def score_alerts(seen, mine, gt, messages, team):
    hits_seen = hits_mine = total = false_alerts = 0
    detail = {}
    for e in gt["events"]:
        expected = {a["user"] for a in e["should_alert"]}
        got_seen = {p["id"] for p in team["people"] if e["thread_ts"] in seen.get((p["id"], e["day"]), set())}
        got_mine = {p["id"] for p in team["people"] if e["thread_ts"] in mine.get((p["id"], e["day"]), set())}
        extra = got_mine - expected - participants_as_of(messages, e["thread_ts"], e["day"])
        detail[e["id"]] = {"seen": expected & got_seen, "mine": expected & got_mine, "miss": expected - got_seen, "extra": extra}
        total += len(expected)
        hits_seen += len(expected & got_seen)
        hits_mine += len(expected & got_mine)
        false_alerts += len(extra)
    noise = {n["thread_ts"] for n in gt["noise"]}
    noise_alerts = sum(len(v & noise) for v in mine.values())
    false_alerts += noise_alerts
    pct = lambda a, b: a / b if b else 0.0
    return {"seen_recall": pct(hits_seen, total), "mine_recall": pct(hits_mine, total),
            "mine_precision": pct(hits_mine, hits_mine + false_alerts),
            "total": total, "false": false_alerts, "noise": noise_alerts,
            "items_per_day": sum(len(v) for v in seen.values()) / (len(team["people"]) * len(all_days(messages))),
            "detail": detail}


# ---------- 2. Team Pulse ----------

def score_pulse(pulses, gt):
    should = [e for e in gt["events"] if e["section"] == "team_pulse"]
    personal = [e for e in gt["events"] if e["section"] == "personal"]
    noise = {n["thread_ts"] for n in gt["noise"]}
    return {
        "should_hit": sum(e["thread_ts"] in pulses.get(e["day"], set()) for e in should), "should_total": len(should),
        "personal_in": sum(e["thread_ts"] in pulses.get(e["day"], set()) for e in personal), "personal_total": len(personal),
        "noise_in": sum(len(p & noise) for p in pulses.values()),
        "detail": {e["id"]: e["thread_ts"] in pulses.get(e["day"], set()) for e in gt["events"]},
    }


# ---------- 3. nicknames ----------

def snapshot_containing(messages, ts):
    """The thread containing message `ts`, as it looked at the end of that message's day."""
    for t in group_into_threads(messages, until=end_of_day(day_of(ts))):
        if any(m["ts"] == ts for m in t["messages"]):
            return t


def score_nicknames(gt, messages, team, cache):
    by_ts = {m["ts"]: m for m in messages}
    rows = []
    for lab in gt["mentions"]:
        text = by_ts[lab["ts"]]["text"]
        x = extract(snapshot_containing(messages, lab["ts"]), team, cache, "none")
        matcher = match_parts(text, team["catalog"])
        if lab["in_catalog"] and lab["written"] and lab["official"] in team["catalog"]["names"] and \
                normalize_written(lab["written"]) not in normalized_aliases(team):
            kind = "nickname not in catalog"   # the part is known, this way of saying it isn't
        elif lab["in_catalog"]:
            kind = "catalog nickname"
        else:
            kind = "part not in catalog"
        flagged = any(lab["written"].lower() in u.lower() or u.lower() in lab["written"].lower() for u in x["unknown_parts"])
        rows.append({
            "kind": kind, "written": lab["written"], "official": lab["official"],
            "matcher": lab["official"] is not None and lab["official"] in matcher,
            "llm": lab["official"] is not None and lab["official"] in x["parts_llm"],
            "llm_available": x["source"] == "cache",
            "flagged_unknown": flagged,
        })
    return rows


def normalize_written(s):
    import re
    return re.sub(r"[^a-z0-9]", "", s.lower())


def normalized_aliases(team):
    return {normalize_written(a) for a, _ in team["catalog"]["aliases"]}


# ---------- 4. ownership ----------

def score_ownership(timeline, team):
    truth = json.load(open(config.DATA_DIR / "true_owners.json"))["owners"]
    final = state_as_of(timeline, max(timeline))["owners"]
    declared = {(part, p) for part, ps in declared_owners(team).items() for p in ps}
    true_pairs = {(part, p) for part, ps in truth.items() for p in ps}
    hidden = true_pairs - declared
    levels = {"declared": {"declared"}, "+ likely": {"declared", "likely"}, "+ possible": {"declared", "likely", "possible"}}
    rows = []
    for name, allowed in levels.items():
        pred = {(part, o["person"]) for part, os in final.items() for o in os if o["confidence"] in allowed}
        rows.append({"level": name, "predicted": len(pred), "correct": len(pred & true_pairs),
                     "precision": len(pred & true_pairs) / len(pred) if pred else 0.0,
                     "recall": len(pred & true_pairs) / len(true_pairs),
                     "hidden_found": len(pred & hidden), "hidden_total": len(hidden)})
    per_part = []
    for part, ps in sorted(truth.items()):
        got = {o["person"]: o["confidence"] for o in final.get(part, [])}
        per_part.append((part, ps, got))
    return rows, per_part


# ---------- report ----------

def main():
    team, messages = load_team(), load_messages()
    gt = load_ground_truth()
    names = {p["id"]: p["name"].split()[0] for p in team["people"]}
    fmt = lambda s: ", ".join(sorted(names[u] for u in s)) or "-"
    cache = load_cache()

    llm_timeline = build_notebook(messages, team, "none")             # uses data/cache/
    kw_timeline = build_notebook(messages, team, "none", cache={})    # keyword rules only
    systems = {
        "ours (LLM extraction)": ours(llm_timeline, team),
        "ours (keywords only)": ours(kw_timeline, team),
        "everyone gets everything": everyone_gets_everything(messages, team),
        "role only": role_only(llm_timeline, team),
    }

    print("\n" + "=" * 30 + " 1. ALERTS " + "=" * 30)
    for split in SPLITS:
        if not gt[split]["events"]:
            print(f"\n({split}: no events)")
            continue
        if split == "holdout" and not SHOW_HOLDOUT:
            print("\nHOLDOUT: per-event detail hidden so the stories stay unseen (run with --show-holdout to see it)")
        else:
            detail = score_alerts(*systems["ours (LLM extraction)"][:2], gt[split], messages, team)["detail"]
            pulse_detail = score_pulse(systems["ours (LLM extraction)"][2], gt[split])["detail"]
            print(f"\n{split.upper()}: per event, ours (LLM extraction)   [P = reached them via Team Pulse]")
            print(f"{'event':<6}{'day':<12}{'what':<52}{'seen':<26}{'missed':<16}extra in For You")
            for e in gt[split]["events"]:
                r = detail[e["id"]]
                seen = ", ".join(sorted(names[u] + (" P" if pulse_detail[e["id"]] and u not in r["mine"] else "") for u in r["seen"])) or "-"
                print(f"{e['id']:<6}{e['day']:<12}{e['what'][:50]:<52}{seen:<26}{fmt(r['miss']):<16}{fmt(r['extra'])}")
        print(f"\n{'system':<28}{'seen recall':>12}{'For You recall':>16}{'For You precision':>19}{'false':>7}{'noise':>7}{'items/day':>11}")
        for name, (seen, mine, _) in systems.items():
            r = score_alerts(seen, mine, gt[split], messages, team)
            print(f"{name:<28}{r['seen_recall']:>12.0%}{r['mine_recall']:>16.0%}{r['mine_precision']:>19.0%}"
                  f"{r['false']:>7}{r['noise']:>7}{r['items_per_day']:>11.1f}")
        print(f"({r['total']} expected alerts)")

    print("\n" + "=" * 30 + " 2. TEAM PULSE " + "=" * 30)
    print(f"\n{'split':<10}{'system':<26}{'team-wide items in pulse':>26}{'personal items in pulse':>25}{'noise in pulse':>16}")
    for split in SPLITS:
        for name in ("ours (LLM extraction)", "ours (keywords only)"):
            r = score_pulse(systems[name][2], gt[split])
            print(f"{split:<10}{name:<26}{r['should_hit']:>20}/{r['should_total']:<5}{r['personal_in']:>19}/{r['personal_total']:<5}{r['noise_in']:>16}")
    print("(left: higher is better; middle and right: lower is better)")

    print("\n" + "=" * 30 + " 3. NICKNAMES " + "=" * 30)
    for split in SPLITS:
        rows = score_nicknames(gt[split], messages, team, cache)
        if not rows:
            continue
        print(f"\n{split.upper()}: {'written':<20}{'should be':<22}{'kind':<26}{'matcher':>9}{'LLM':>6}{'flagged unknown':>17}")
        for r in rows if (split == "tuned" or SHOW_HOLDOUT) else []:
            yn = lambda b: "✓" if b else "✗"
            print(f"{'':<9}{r['written']:<20}{str(r['official']):<22}{r['kind']:<26}{yn(r['matcher']):>9}"
                  f"{(yn(r['llm']) if r['llm_available'] else 'n/a'):>6}{yn(r['flagged_unknown']):>17}")
        known = [r for r in rows if r["kind"] != "part not in catalog"]
        unknown = [r for r in rows if r["kind"] == "part not in catalog"]
        llm_rows = [r for r in known if r["llm_available"]]
        print(f"{'':<9}known parts mapped correctly: matcher {sum(r['matcher'] for r in known)}/{len(known)}, "
              f"LLM {sum(r['llm'] for r in llm_rows)}/{len(llm_rows)}, either {sum(r['matcher'] or r['llm'] for r in known)}/{len(known)}")
        print(f"{'':<9}parts not in catalog flagged as unknown: {sum(r['flagged_unknown'] for r in unknown)}/{len(unknown)}")

    print("\n" + "=" * 30 + " 4. OWNERSHIP (vs true_owners.json, end of period) " + "=" * 30)
    rows, per_part = score_ownership(llm_timeline, team)
    print(f"\n{'owners counted':<16}{'predicted':>10}{'correct':>9}{'precision':>11}{'recall':>8}{'hidden owners found':>21}")
    for r in rows:
        print(f"{r['level']:<16}{r['predicted']:>10}{r['correct']:>9}{r['precision']:>11.0%}{r['recall']:>8.0%}"
              f"{r['hidden_found']:>15}/{r['hidden_total']}")
    print("(hidden owners = true owners that team.json leaves out)\n")
    for part, truth, got in per_part:
        got_s = ", ".join(f"{names[p]} ({c})" for p, c in got.items()) or "-"
        ok = "✓" if set(truth) <= set(got) else ("~" if set(truth) & set(got) else "✗")
        print(f"   {ok} {part:<24} true: {fmt(truth):<14} inferred: {got_s}")


if __name__ == "__main__":
    main()
