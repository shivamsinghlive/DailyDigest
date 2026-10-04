"""The project notebook: what the project looks like on any given day.

We replay Slack one day at a time. Each thread that had activity that day is
extracted (extract.py) and folded into the state:
  - phase per subsystem (gripper can be in DVT while the wrist is still in EVT)
  - owners per part: declared in team.json, or inferred from activity (likely / possible)
  - open problems, decisions, unanswered questions
  - who's been working on what (focus), and unknown parts a human should add to the catalog
Every fold also produces a "change" record. The digest is built from these changes,
not from raw messages: "the J4 connector went EOL", not "Mei posted 3 messages".
"""
import copy
import re
from datetime import date

from . import config
from .catalog import match_parts
from .extract import extract, load_cache, match_people, unanswered_hours
from .slack_loader import all_days, day_of, end_of_day, group_into_threads, load_messages, load_team, threads_active_on

# ---------- phases ----------

# Phase changes are announced in plain words, so patterns are more reliable than a small LLM.
# Each pattern returns the phase the named subsystems are in after the sentence.
PHASES = ["Concept", "EVT", "DVT", "PVT", "Production"]
_P = r"(Concept|EVT|DVT|PVT|Production)"
PHASE_PATTERNS = [
    (re.compile(r"\bdesign freeze\b", re.I), lambda m: "DVT"),                       # freeze = entering DVT
    (re.compile(rf"\b{_P}\s+(?:exit|build)(?:\s+review)?\s+(?:is\s+)?(?:done|complete|completed|passed|finished)\b", re.I),
     lambda m: PHASES[min(PHASES.index(canon(m.group(1))) + 1, len(PHASES) - 1)]),  # "EVT build done" -> DVT
    (re.compile(rf"\b{_P}\s+units?\s+(?:shipped|built|delivered)\b", re.I), lambda m: canon(m.group(1))),
    (re.compile(rf"\b(?:officially in|now in|entering|enters|mov(?:es|ing|ed) (?:in)?to|stays? in|remains? in)\s+{_P}\b", re.I),
     lambda m: canon(m.group(1))),
]


def canon(phase):
    return next(p for p in PHASES if p.lower() == phase.lower())


def detect_phase_changes(thread, day, team):
    """{subsystem: phase} announced today. Read sentence by sentence: a sentence that names
    subsystems applies to those only ("gripper and base are officially in DVT"), one that names
    none applies to the whole project. Later sentences win ("... the wrist stays in EVT")."""
    cat = team["catalog"]
    found = {}
    for m in thread["messages"]:
        if day_of(m["ts"]) != day:
            continue
        for sentence in re.split(r"(?<=[.!?\n])\s+", m["text"]):
            phase = next((fn(hit) for rx, fn in PHASE_PATTERNS for hit in [rx.search(sentence)] if hit), None)
            if not phase:
                continue
            named = {cat["subsystem_of"][n] for n in match_parts(sentence, cat)} - {None}
            for subsystem in sorted(named) or cat["subsystems"]:
                found[subsystem] = phase
    return found


def overall_phase(phases):
    """The project is only as far along as its slowest subsystem."""
    return min(phases.values(), key=PHASES.index)


# ---------- ownership ----------

def declared_owners(team):
    """{part: [person ids]} as listed in team.json. A list, because ownership overlaps."""
    owners = {}
    for person in team["people"]:
        for part in person["owns"]:
            owners.setdefault(part, []).append(person["id"])
    return owners


def ownership_evidence(msg, root, team, answered):
    """(person, part, kind) facts from one message. `answered` dedupes answer credit per thread."""
    cat, out = team["catalog"], []
    author, parts = msg.get("user"), match_parts(msg["text"], cat)
    if not author:
        return out
    out += [(author, p, "question" if "?" in msg["text"] else "mention") for p in parts]
    # Answering someone else's question about a part is a strong sign you own it.
    if root is not msg and "?" in root["text"] and author != root.get("user"):
        for p in set(match_parts(root["text"], cat)) | set(parts):
            if (root["ts"], author, p) not in answered:
                answered.add((root["ts"], author, p))
                out.append((author, p, "answer"))
    # Being @-tagged next to a part means others think it's yours. Only tags count: a name is often
    # third-person ("Mei is chasing broker stock"), and that's not a sign Mei owns the part.
    tagged, _ = match_people({"messages": [msg]}, team)
    out += [(pid, p, "asked_about") for pid in tagged for p in parts]
    return out


def infer_owners(state, day, team):
    """{part: [{person, confidence, score, evidence}]}, declared owners first.
    declared = listed in team.json; likely / possible = inferred from recency-weighted evidence."""
    today = date.fromisoformat(day)
    declared = declared_owners(team)
    scores, counts = {}, {}
    for person, by_part in state["ownership_evidence"].items():
        for part, facts in by_part.items():
            for d, kind in facts:
                weight = config.OWNERSHIP_EVIDENCE[kind] * 0.5 ** ((today - date.fromisoformat(d)).days / config.OWNERSHIP_HALF_LIFE_DAYS)
                scores.setdefault(part, {}).setdefault(person, 0.0)
                scores[part][person] += weight
                c = counts.setdefault(part, {}).setdefault(person, {"mention": 0, "question": 0, "answer": 0, "asked_about": 0})
                c[kind] += 1

    owners = {}
    for part in set(scores) | set(declared):
        by_person = scores.get(part, {})
        total = sum(by_person.values()) or 1.0
        entries = [{"person": p, "confidence": "declared", "score": round(by_person.get(p, 0), 2),
                    "evidence": counts.get(part, {}).get(p, {})} for p in declared.get(part, [])]
        for person, s in sorted(by_person.items(), key=lambda kv: -kv[1]):
            if person in declared.get(part, []):
                continue
            share = s / total
            if s >= config.LIKELY_MIN_SCORE and share >= config.LIKELY_MIN_SHARE:
                level = "likely"
            elif s >= config.POSSIBLE_MIN_SCORE and share >= config.POSSIBLE_MIN_SHARE and part not in declared:
                level = "possible"  # weak evidence only fills gaps; when the roster names an owner, it wins
            else:
                continue
            entries.append({"person": person, "confidence": level, "score": round(s, 2), "evidence": counts[part][person]})
        if entries:
            owners[part] = entries
    return owners


# ---------- changes ----------

SCHEDULE_RE = re.compile(r"\b(slips?|slipping|delay(?:ed)?|late|lead time|TBD|at risk|behind)\b", re.I)


def is_schedule_risk(thread, x):
    """A thread about the build date that also talks about slipping. Cheap and explainable."""
    text = " ".join(m["text"] for m in thread["messages"])
    return bool(SCHEDULE_RE.search(text)) and ("DVT build schedule" in x["parts"] or re.search(r"\bbuild\b", text, re.I) is not None)


def make_change(kind, day, thread, x, team, **extra):
    """One entry in "what changed today". Carries everything the ranker needs to score and explain it."""
    todays = [m for m in thread["messages"] if day_of(m["ts"]) == day]
    return {
        "kind": kind,
        "day": day,
        "thread_ts": thread["thread_ts"],
        "channel_name": thread["channel_name"],
        "type": x["type"],
        "summary": x["summary"],
        "urgency": x["urgency"],
        "parts": x["parts"],
        "subsystems": sorted({team["catalog"]["subsystem_of"][p] for p in x["parts"]} - {None}),
        "schedule_risk": is_schedule_risk(thread, x),
        "people_tagged": x["people_tagged"],
        "people_mentioned": x["people_mentioned"],
        "participants": thread["participants"],   # people who posted in the thread so far
        "authors_today": sorted({m["user"] for m in todays if m.get("user")}),  # they've already seen it
        "root_author": thread["messages"][0].get("user"),
        **extra,
    }


def open_item(x, thread, day, since):
    """What we remember about an open problem / question, enough to show it again later."""
    return {"thread_ts": thread["thread_ts"], "channel_name": thread["channel_name"], "summary": x["summary"],
            "urgency": x["urgency"], "parts": x["parts"], "since": since, "last_day": day}


def change_kind(x, is_open_problem):
    if x["type"] == "problem":
        return "problem_update" if is_open_problem else "new_problem"
    return {"decision": "decision", "question": "new_question"}.get(x["type"], "update")


# ---------- the replay ----------

def build_notebook(messages, team, provider=config.LLM_PROVIDER, cache=None):
    """Replay all days. Returns {day: {"state": snapshot at end of day, "changes": [...]}}.
    Pass cache={} with provider="none" to see what keyword rules alone produce."""
    cache = load_cache() if cache is None else cache
    start = team["project"]["start_phase"]
    roots = {m["ts"]: m for m in messages if m.get("thread_ts", m["ts"]) == m["ts"]}
    answered = set()
    state = {
        "phases": {s: start for s in team["catalog"]["subsystems"]},
        "phase_history": [],         # [{subsystem, phase, since, thread_ts}]
        "owners": {},                # part -> [{person, confidence, score, evidence}]
        "ownership_evidence": {},    # person -> part -> [[day, kind]]
        "open_problems": {},         # thread_ts -> open_item
        "decisions": [],             # [{day, summary, parts, thread_ts}]
        "unanswered_questions": {},  # thread_ts -> open_item + people_mentioned, flagged
        "unknown_parts": {},         # phrase -> {written, threads, first_day, last_day}: for a human to catalog
        "activity": {},              # person -> day -> {part: mentions}; drives "current focus"
        "channels": {},              # person -> channels they've posted in; explains cross-team alerts
    }
    timeline = {}

    for day in all_days(messages):
        eod = end_of_day(day)
        changes = []

        # Per message: focus activity, ownership evidence, channels.
        for m in messages:
            if day_of(m["ts"]) != day or not m.get("user"):
                continue
            counts = state["activity"].setdefault(m["user"], {}).setdefault(day, {})
            for part in match_parts(m["text"], team["catalog"]):
                counts[part] = counts.get(part, 0) + 1
            root = roots.get(m.get("thread_ts", m["ts"]), m)
            for person, part, kind in ownership_evidence(m, root, team, answered):
                state["ownership_evidence"].setdefault(person, {}).setdefault(part, []).append([day, kind])
            seen = state["channels"].setdefault(m["user"], [])
            if m["channel_name"] not in seen:
                seen.append(m["channel_name"])
        state["owners"] = infer_owners(state, day, team)

        active = threads_active_on(messages, day)
        for thread in active:
            ts = thread["thread_ts"]
            x = extract(thread, team, cache, provider)

            for phrase in x["unknown_parts"]:
                u = state["unknown_parts"].setdefault(phrase.lower(), {"written": phrase, "threads": [], "first_day": day})
                u["last_day"] = day
                if ts not in u["threads"]:
                    u["threads"].append(ts)

            announced = detect_phase_changes(thread, day, team)
            new_phases = {s: p for s, p in announced.items() if state["phases"][s] != p}
            if new_phases:
                state["phases"].update(new_phases)
                state["phase_history"] += [{"subsystem": s, "phase": p, "since": day, "thread_ts": ts}
                                           for s, p in new_phases.items()]
                unchanged = {s: p for s, p in state["phases"].items() if s not in new_phases}
                changes.append(make_change("phase_change", day, thread, x, team,
                                           new_phases=new_phases, unchanged_phases=unchanged))
                continue
            if x["type"] == "noise":
                continue

            kind = change_kind(x, ts in state["open_problems"])
            if kind in ("new_problem", "problem_update"):
                since = state["open_problems"].get(ts, {}).get("since", day)
                state["open_problems"][ts] = open_item(x, thread, day, since)
            elif kind == "decision":
                state["decisions"].append({"day": day, "summary": x["summary"], "parts": list(x["parts"]), "thread_ts": ts})
                state["open_problems"].pop(ts, None)  # a decision in the same thread settles its problem

            # Questions are tracked by reply behaviour, whatever type the LLM gave the thread.
            waiting = unanswered_hours(thread, eod)
            open_q = state["unanswered_questions"].get(ts)
            if waiting is not None:
                if open_q is None:
                    open_q = state["unanswered_questions"][ts] = {
                        **open_item(x, thread, day, day_of(ts)), "people_mentioned": x["people_mentioned"], "flagged": False}
                open_q["last_day"] = day
                if waiting >= config.UNANSWERED_AFTER_HOURS and not open_q["flagged"]:
                    open_q["flagged"] = True
                    kind = "question_unanswered"
            elif open_q is not None:
                del state["unanswered_questions"][ts]
                kind = "question_answered"

            changes.append(make_change(kind, day, thread, x, team, **({"waiting_hours": round(waiting)} if waiting else {})))

        # Quiet threads can still change state: a question crosses 48h without any new message.
        active_ts = {t["thread_ts"] for t in active}
        for ts, q in state["unanswered_questions"].items():
            if ts in active_ts or q["flagged"]:
                continue
            thread = next(t for t in group_into_threads(messages, until=eod) if t["thread_ts"] == ts)
            waiting = unanswered_hours(thread, eod)
            if waiting is not None and waiting >= config.UNANSWERED_AFTER_HOURS:
                q["flagged"] = True
                x = extract(thread, team, cache, provider)  # cached: same snapshot as when it was asked
                changes.append(make_change("question_unanswered", day, thread, x, team, waiting_hours=round(waiting)))

        timeline[day] = {"state": copy.deepcopy(state), "changes": changes}
    return timeline


def state_as_of(timeline, day):
    """Project state at the end of `day` (clamped to the range we have data for)."""
    days = sorted(timeline)
    day = min(max(day, days[0]), days[-1])
    return timeline[day]["state"]


def changes_on(timeline, day):
    return timeline.get(day, {}).get("changes", [])


def load_notebook(provider=config.LLM_PROVIDER):
    team = load_team()
    return build_notebook(load_messages(), team, provider), team


if __name__ == "__main__":
    import sys
    provider = sys.argv[1] if len(sys.argv) > 1 else config.LLM_PROVIDER
    timeline, team = load_notebook(provider)
    names = {p["id"]: p["name"].split()[0] for p in team["people"]}
    for day, entry in timeline.items():
        s = entry["state"]
        phases = ", ".join(f"{k}={v}" for k, v in s["phases"].items())
        print(f"\n{day}  [{phases}]  open_problems={len(s['open_problems'])}  "
              f"decisions={len(s['decisions'])}  unanswered={len(s['unanswered_questions'])}")
        for c in entry["changes"]:
            print(f"   {c['kind']:<20} u{c['urgency']} #{c['channel_name']:<13} {'SCHEDULE ' if c['schedule_risk'] else ''}{c['summary'][:70]}")
    last = timeline[max(timeline)]["state"]
    print("\nOwners at the end of the period:")
    for part, owners in sorted(last["owners"].items()):
        print(f"   {part:<24} " + ", ".join(f"{names[o['person']]} ({o['confidence']}, {o['score']})" for o in owners))
    print("\nUnknown parts (add to the catalog?):")
    for u in last["unknown_parts"].values():
        print(f"   {u['written']!r} in {len(u['threads'])} thread(s), first seen {u['first_day']}")
