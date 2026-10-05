"""Persistent project memory: what the project knows, and how it came to know it.

Unbounded memory, bounded retrieval:
- Nothing expires. A decision from six months ago stays a memory until something resolves or
  supersedes it. (Recency still matters for *relevance*, e.g. focus and ownership evidence fade,
  but that's ranking, not memory.)
- Every change is an event in an append-only log: NEW, UPDATED, RESOLVED, REOPENED, CONFLICTING. Events are never
  edited, so "what was true on day X?" is answered from the log, and "what is true now?" from the
  latest versions.
- A memory has a key (what it's about: "phase:wrist", "issue:<thread>", "owner:J4 connector") and
  versions. When a key gets a new value, the old version is SUPERSEDED (it gets valid_to) and the new
  one is ACTIVE. Old values are kept: 850g -> 790g -> 745g stays visible.
- retrieve() returns a small shortlist of memories that share parts, subsystems or people with
  something new. That shortlist is all any later step (or LLM call) gets to see, never the whole log.

The notebook (notebook.py) writes here as it replays Slack; nothing in this module reads Slack.
"""
import json

TYPES = ("ISSUE", "DECISION", "QUESTION", "MILESTONE", "OWNERSHIP", "FACT", "CONSTRAINT")


def new_store():
    return {"memories": {}, "events": []}   # memory_id -> record; append-only event log


def _versions(store, key):
    return [m for m in store["memories"].values() if m["key"] == key]


def latest(store, key):
    """The newest version of a key (ACTIVE or RESOLVED), or None."""
    versions = _versions(store, key)
    return max(versions, key=lambda m: m["version"]) if versions else None


def _event(store, day, key, change, thread_ts, before=None, after=None, note=""):
    store["events"].append({"day": day, "key": key, "change": change, "thread_ts": thread_ts,
                            "before": before, "after": after, "note": note})


def record(store, day, key, mtype, value, thread_ts=None, summary=None, parts=(), subsystems=(), people=(),
           confidence=1.0, note=""):
    """Remember `key = value` as of `day`. Returns what happened: NEW, UPDATED or UNCHANGED.
    An UPDATED key keeps its old version, marked SUPERSEDED with valid_to = day."""
    assert mtype in TYPES, mtype
    cur = latest(store, key)
    if cur and cur["value"] == value:
        if thread_ts and thread_ts not in cur["source_threads"]:
            cur["source_threads"].append(thread_ts)   # more evidence for the same thing
        return "UNCHANGED"
    version = cur["version"] + 1 if cur else 1
    if cur:
        cur["status"], cur["valid_to"] = "SUPERSEDED", day
    rec = {
        "memory_id": f"{key}#{version}", "key": key, "version": version, "type": mtype,
        "value": value, "summary": summary or str(value),
        "parts": sorted(parts), "subsystems": sorted(subsystems), "people": sorted(people),
        "status": "ACTIVE", "created_at": cur["created_at"] if cur else day, "updated_at": day,
        "valid_from": day, "valid_to": None,
        "source_threads": [thread_ts] if thread_ts else [], "confidence": confidence,
        "conflicts": [],   # statements that disagree with this value but weren't authoritative enough to change it
    }
    store["memories"][rec["memory_id"]] = rec
    change = "UPDATED" if cur else "NEW"
    _event(store, day, key, change, thread_ts, before=cur["value"] if cur else None, after=value, note=note)
    return change


def resolve(store, day, key, thread_ts=None, note=""):
    """Close an issue or question. It stays in memory (and can be reopened)."""
    cur = latest(store, key)
    if not cur or cur["status"] == "RESOLVED":
        return "UNCHANGED"
    cur["status"], cur["updated_at"] = "RESOLVED", day
    if thread_ts and thread_ts not in cur["source_threads"]:
        cur["source_threads"].append(thread_ts)
    _event(store, day, key, "RESOLVED", thread_ts, before="open", after="resolved", note=note)
    return "RESOLVED"


def conflict(store, day, key, value, thread_ts=None, note=""):
    """Someone stated a different value without the authority to change it ("I thought it was 800g?").
    Keep both: the current value stands, the disagreement is recorded and shown until settled."""
    cur = latest(store, key)
    cur["conflicts"].append({"day": day, "value": value, "thread_ts": thread_ts, "note": note})
    cur["updated_at"] = day
    _event(store, day, key, "CONFLICTING", thread_ts, before=cur["value"], after=value, note=note)
    return "CONFLICTING"


def reopen(store, day, key, thread_ts=None, note=""):
    cur = latest(store, key)
    if not cur or cur["status"] != "RESOLVED":
        return "UNCHANGED"
    cur["status"], cur["updated_at"] = "ACTIVE", day
    _event(store, day, key, "REOPENED", thread_ts, before="resolved", after="open", note=note)
    return "REOPENED"


# ---------- queries ----------

def status_as_of(store, key, day):
    """ACTIVE / RESOLVED on `day`, replayed from the event log (None if the key didn't exist yet)."""
    status = None
    for e in store["events"]:
        if e["key"] == key and e["day"] <= day:
            status = "RESOLVED" if e["change"] == "RESOLVED" else "ACTIVE"
    return status


def value_as_of(store, key, day):
    """The value `key` had at the end of `day`: what was true then, even if it changed since."""
    for m in _versions(store, key):
        if m["valid_from"] <= day and (m["valid_to"] is None or m["valid_to"] > day):
            return m["value"]
    return None


def current_state(store, as_of=None, types=None):
    """The latest version of every key as of `as_of` (default: now), with its status then."""
    out = []
    for key in sorted({m["key"] for m in store["memories"].values()}):
        versions = [m for m in _versions(store, key) if as_of is None or m["valid_from"] <= as_of]
        if not versions:
            continue
        m = max(versions, key=lambda v: v["version"])
        if types and m["type"] not in types:
            continue
        out.append({**m, "status": status_as_of(store, key, as_of) if as_of else m["status"]})
    return out


def history(store, key):
    """Every event for one key, oldest first: the story of how it got to its current state."""
    return [e for e in store["events"] if e["key"] == key]


def retrieve(store, parts=(), subsystems=(), people=(), as_of=None, limit=15):
    """Bounded retrieval: the few memories most related to something new, never the whole log.
    Shared parts count most, then subsystems, then people; open items before closed ones;
    then the most recently updated. Age alone never drops a memory, it only breaks ties."""
    parts, subsystems, people = set(parts), set(subsystems), set(people)
    scored = []
    for m in current_state(store, as_of):
        overlap = 3 * len(parts & set(m["parts"])) + 2 * len(subsystems & set(m["subsystems"])) + len(people & set(m["people"]))
        if overlap:
            scored.append((overlap + (2 if m["status"] == "ACTIVE" else 0), m["updated_at"], m))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [m for _, _, m in scored[:limit]]


def save(store, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    import sys
    from . import config
    from .notebook import load_notebook

    timeline, team = load_notebook("none")
    store = timeline[max(timeline)]["state"]["memory"]
    as_of = sys.argv[1] if len(sys.argv) > 1 else None
    save(store, config.CACHE_DIR / "memory.json")
    print(f"{len(store['memories'])} memory versions, {len(store['events'])} events "
          f"(saved to {config.CACHE_DIR / 'memory.json'})\n")
    for mtype in TYPES:
        rows = current_state(store, as_of, types={mtype})
        if rows:
            print(f"== {mtype} ({len(rows)})")
            for m in rows:
                print(f"   [{m['status']:<8}] {m['key']:<32} {str(m['summary'])[:70]}")
