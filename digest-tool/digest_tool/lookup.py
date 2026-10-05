"""Lookup: everything the tool knows about a part, a person or a keyword. No LLM, no cost.

A query is resolved the same way messages are:
  - parts through the catalog, so "wrist conn", "J-4" or "43045-0412" all find the J4 connector;
  - people by name or first name;
  - anything else as words to find in conversations and in project memory.
Conversations come from the notebook's change records, so each one already has a summary, a kind and
what it changed in memory.
"""
import re

from .catalog import find_mentions
from .memory import current_state, history
from .ranker import fact_label

STOPWORDS = {"the", "a", "an", "of", "on", "in", "for", "to", "and", "or", "is", "what", "who", "about"}


def query_words(query):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-./]*", query.lower()) if w not in STOPWORDS]


def resolve(query, team):
    """(parts, people) the query names. Parts via the catalog (nicknames, typos), people by name."""
    cat = team["catalog"]
    parts = list(find_mentions(query, cat)[0])
    q = query.strip().lower()
    parts += [n for n in cat["names"] if q and q == n.lower() and n not in parts]   # topics typed in full
    people = [p for p in team["people"] if q and (q in p["name"].lower() or p["name"].lower().split()[0] == q)]
    return parts, people


def conversations(timeline, start, end, parts=(), people=(), words=(), kinds=None):
    """Change records between `start` and `end` (inclusive) about the parts, by or naming the people, or
    containing all the words. One per thread: its latest change in the range, newest first."""
    latest = {}
    for day in sorted(timeline):
        if not start <= day <= end:
            continue
        for c in timeline[day]["changes"]:
            if kinds and c["kind"] not in kinds:
                continue
            text = (c["summary"] + " " + " ".join(c["parts"])).lower()
            hit = (set(parts) & set(c["parts"])
                   or any(p in c["participants"] or p in c["people_mentioned"] for p in people)
                   or (words and all(w in text for w in words)))
            if hit:
                latest[c["thread_ts"]] = c
    return sorted(latest.values(), key=lambda c: (c["day"], c["thread_ts"]), reverse=True)


def part_profile(part, state, team, day):
    """What the project knows about one part, as of `day`."""
    cat = team["catalog"]
    entry = next((e for e in cat["entries"] if e["name"] == part), {"aliases": []})
    sub = cat["subsystem_of"].get(part)
    mem = state["memory"]
    facts = []
    for m in current_state(mem, as_of=day, types={"FACT", "CONSTRAINT"}):
        if part in m["parts"]:
            trail = [e["after"] for e in history(mem, m["key"]) if e["change"] in ("NEW", "UPDATED") and e["day"] <= day]
            facts.append({"label": fact_label(m["key"]), "value": m["value"], "earlier": trail[:-1],
                          "constraint": m["type"] == "CONSTRAINT", "since": m["valid_from"],
                          "disputed": [c for c in m["conflicts"] if c["day"] <= day]})
    issues = [m for m in current_state(mem, as_of=day, types={"ISSUE"}) if part in m["parts"]]
    return {
        "part": part, "aliases": entry.get("aliases", []), "subsystem": sub,
        "phase": state["phases"].get(sub) if sub else None,
        "owners": state["owners"].get(part, []),
        "facts": sorted(facts, key=lambda f: (not f["disputed"], not f["constraint"], f["label"])),
        "open_issues": [m for m in issues if m["status"] == "ACTIVE"],
        "closed_issues": [m for m in issues if m["status"] == "RESOLVED"],
        "decisions": [d for d in state["decisions"] if part in d["parts"]],
    }


def person_profile(person, state, timeline, day):
    """Role, parts owned, recent focus, threads started and questions waiting on this person."""
    pid = person["id"]
    owns = [(part, o["confidence"]) for part, owners in state["owners"].items() for o in owners if o["person"] == pid]
    waiting = [q for q in state["unanswered_questions"].values() if pid in q.get("people_mentioned", [])]
    started = [c for d in sorted(timeline) if d <= day for c in timeline[d]["changes"] if c["root_author"] == pid]
    return {"person": person, "owns": sorted(owns, key=lambda pc: pc[1]), "waiting_on_them": waiting,
            "started": list({c["thread_ts"]: c for c in started}.values())[::-1]}
