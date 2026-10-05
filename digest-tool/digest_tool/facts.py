"""Facts and constraints with values ("gripper target weight = 750g"), and contradictions.

The LLM extracts; the application decides what becomes project truth:
  - a CONSTRAINT (target, limit, requirement, deadline) changes on a decision, or when its owner or the
    engineering manager states the new value;
  - a FACT (measurement, current value, quantity) changes when someone reports it;
  - a statement that disagrees with the current value but isn't authoritative ("I thought we were
    still at 800g?") is recorded as CONFLICTING: both values are kept and the disagreement is shown,
    nothing is overwritten.

Consistent names: extraction sees only the thread, so its cached output never goes stale when rules
change. Before a new fact name is created, same_quantity() compares it with a short list of existing
facts whose names share words (bounded retrieval) and asks the LLM whether it's the same quantity
("motor driver.tmc9660_dvt_build_constraint" may really be "project.dvt_build_target"). With no LLM
and nothing cached, the fact is kept under its own name: never lost, only not merged.
"""
import hashlib
import json
import re

from . import config
from .catalog import match_parts
from .extract import call_provider, model_name
from .memory import conflict, current_state, latest, record
from .slack_loader import clean_text, day_of

FACTS_PROMPT_VERSION = "f3"
CERTAINTY = ["decided", "reported", "unsure", "question"]


def facts_prompt(thread, day, team):
    names = {p["id"]: p["name"] for p in team["people"]}
    parts = ", ".join(e["name"] for e in team["catalog"]["entries"] if e["kind"] == "part")
    system = (
        "You extract project facts from a Slack thread of a hardware team. Only from messages marked (new). "
        "A fact is a specific value: a target, limit, requirement, deadline, measurement, quantity, lead time, "
        "price or date. Skip statuses and activities (\"printed\", \"testing this week\"), part numbers "
        "mentioned in passing, opinions, and plans without a value.\n"
        f"entity: one of these parts if the fact is about one ({parts}), otherwise \"project\".\n"
        "attribute: short, general snake_case name of the quantity (\"case_temp_max\", \"lead_time\", "
        "\"dvt_build_date\"), not a sentence.\n"
        "value: as stated, with its unit (\"750g\", \"16 weeks\", \"10/26\").\n"
        "kind: constraint (a target, limit, requirement or deadline) or fact (a measured or current value).\n"
        "certainty: decided (an authoritative decision or approval), reported (stated as fact, e.g. a "
        "measurement or a supplier's answer), unsure (hedged: \"I think\", \"I thought\", \"probably\"), "
        "question (asked, not stated).\n"
        "said_by: the name of the person who stated it.\n"
        "Return an empty list if the new messages state no facts.")
    lines = [f"{'(new) ' if day_of(m['ts']) == day else ''}{names.get(m.get('user'), m.get('user'))}: "
             f"{clean_text(m['text'], names)}" for m in thread["messages"] if day_of(m["ts"]) <= day]
    schema = {"type": "object", "properties": {"facts": {"type": "array", "items": {
        "type": "object", "properties": {
            "entity": {"type": "string"}, "attribute": {"type": "string"}, "value": {"type": "string"},
            "kind": {"type": "string", "enum": ["constraint", "fact"]},
            "certainty": {"type": "string", "enum": CERTAINTY},
            "said_by": {"type": "string", "enum": [p["name"] for p in team["people"]]}},
        "required": ["entity", "attribute", "value", "kind", "certainty", "said_by"], "additionalProperties": False}}},
        "required": ["facts"], "additionalProperties": False}
    return system, "\n".join(lines), schema


def load_fact_cache():
    path = config.CACHE_DIR / "facts.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_fact_cache(cache):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (config.CACHE_DIR / "facts.json").write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def canonical(fact, team):
    """Validate one extracted fact and give it a memory key, or None if it doesn't hold up."""
    cat = team["catalog"]
    attribute = re.sub(r"[^a-z0-9_]", "", fact["attribute"].lower().replace(" ", "_"))
    # A value has a number in it ("750g", "10/26", "16 weeks") or is explicitly open ("TBD").
    # "updated", "approved", "in spec" are statuses: the issue and decision memories cover those.
    if not attribute or not (re.search(r"\d", fact["value"]) or fact["value"].strip().upper() == "TBD"):
        return None
    found = match_parts(fact["entity"], cat)
    entity = next(iter(found)) if found else "project"
    prefix = "constraint" if fact["kind"] == "constraint" else "fact"
    return f"{prefix}:{entity}.{attribute}", entity


def extract_facts(thread, day, team, provider, cache):
    """Facts stated in today's messages of this thread. Threads with no digits today are skipped:
    nearly every fact has a number in it, and it keeps LLM calls down."""
    if not any(day_of(m["ts"]) == day and re.search(r"\d", m["text"]) for m in thread["messages"]):
        return []
    system, user, schema = facts_prompt(thread, day, team)
    key = hashlib.sha256((FACTS_PROMPT_VERSION + system + user).encode()).hexdigest()[:16]
    if key not in cache:
        if provider == "none":
            return []
        cache[key] = {"facts": call_provider(system, user, schema, provider)["facts"], "model": model_name(provider)}
        save_fact_cache(cache)
    return cache[key]["facts"]


# Words too generic to suggest two fact names are the same quantity.
GENERIC = set("date status value number count total current new old target actual".split())


def name_words(key):
    return {w for w in re.split(r"[^a-z0-9]+", key.split(":", 1)[1].lower()) if len(w) > 2 and w not in GENERIC}


def same_quantity(mem, key, f, day, provider, cache):
    """Before creating a new fact name, check whether it's a new value for an existing one:
    "motor driver.tmc9660_dvt_build_constraint = TBD" may really be "project.dvt_build_target".
    Code shortlists existing facts whose names share words; the LLM picks one or "none" (cached)."""
    words_ = name_words(key)
    kind = key.split(":", 1)[0]  # a target only merges into a target, a measurement into a measurement
    cands = sorted((m for m in current_state(mem, day, types={"FACT", "CONSTRAINT"})
                    if m["key"] != key and m["key"].split(":", 1)[0] == kind and words_ & name_words(m["key"])),
                   key=lambda m: -len(words_ & name_words(m["key"])))[:5]
    if not cands:
        return None
    system = ("You keep a hardware project's facts consistent. Is the new statement a new value for one of the "
              "existing facts (the same quantity, possibly named differently), or a different quantity? "
              "Answer with the existing key, or \"none\".")
    user = (f"New: {key.split(':', 1)[1]} = {f['value']} (said by {f.get('said_by', '?')})\nExisting:\n"
            + "\n".join(f"- {m['key']}: {m['value']}" for m in cands))
    schema = {"type": "object", "properties": {"same_as": {"type": "string", "enum": [m["key"] for m in cands] + ["none"]}},
              "required": ["same_as"], "additionalProperties": False}
    ckey = "match:" + hashlib.sha256((FACTS_PROMPT_VERSION + system + user).encode()).hexdigest()[:16]
    if ckey not in cache:
        if provider == "none":
            return None
        cache[ckey] = {"same_as": call_provider(system, user, schema, provider)["same_as"], "model": model_name(provider)}
        save_fact_cache(cache)
    same = cache[ckey]["same_as"]
    return same if same in {m["key"] for m in cands} else None


def normalized(value):
    """"10k" == "10000", "~1.5 deg" == "~1.5deg": the same value written differently isn't a change."""
    v = re.sub(r"[\s~≈]", "", str(value).lower())
    return re.sub(r"(\d+(?:\.\d+)?)k\b", lambda m: str(int(float(m.group(1)) * 1000)), v)


def has_authority(f, entity, mtype, team, owners):
    """Can this statement change project truth? Decisions can. Reported measurements can. A reported
    constraint only from the engineering manager or the part's (declared/likely) owner."""
    if f["certainty"] == "decided":
        return True
    if f["certainty"] != "reported":
        return False
    if mtype == "FACT":
        return True
    speaker = next((p for p in team["people"] if p["name"] == f.get("said_by")), None)
    if not speaker:
        return False
    owns = any(o["person"] == speaker["id"] and o["confidence"] in ("declared", "likely") for o in owners.get(entity, []))
    return speaker["role"] == "engineering_manager" or owns


def apply_facts(mem, facts, thread, day, team, owners, provider="none", cache=None):
    """Write extracted facts to memory under the application's rules. Returns what changed."""
    changes = []
    for f in facts:
        valid = canonical(f, team)
        if not valid:
            continue
        key, entity = valid
        if not latest(mem, key):  # a name we haven't seen: is it really a new quantity?
            key = same_quantity(mem, key, f, day, provider, cache if cache is not None else {}) or key
            entity = key.split(":", 1)[1].split(".", 1)[0]
        mtype = "CONSTRAINT" if key.startswith("constraint:") else "FACT"
        cur = latest(mem, key)
        if cur and normalized(cur["value"]) == normalized(f["value"]):
            continue  # unchanged, however it's written
        sub = team["catalog"]["subsystem_of"].get(entity)
        about = dict(thread_ts=thread["thread_ts"], summary=f"{entity} {key.split('.', 1)[1].replace('_', ' ')} = {f['value']}",
                     parts=[entity] if entity != "project" else [], subsystems=[sub] if sub else [])
        authoritative = has_authority(f, entity, mtype, team, owners)
        if cur and cur["value"] != f["value"] and not authoritative:
            change = conflict(mem, day, key, f["value"], thread["thread_ts"],
                              note=f"{f['certainty']} statement by {f.get('said_by', 'someone')} in #{thread['channel_name']}")
        elif not cur and f["certainty"] == "question":
            continue  # a question about a value we don't know isn't a fact
        else:
            before = cur["value"] if cur else None
            change = record(mem, day, key, mtype, f["value"], confidence=1.0 if authoritative else 0.5, **about)
            if change == "UPDATED":
                changes.append({"key": key, "change": change, "before": before, "after": f["value"], "certainty": f["certainty"]})
            elif change == "NEW":
                changes.append({"key": key, "change": change, "before": None, "after": f["value"], "certainty": f["certainty"]})
            continue
        if change == "CONFLICTING":
            changes.append({"key": key, "change": change, "before": cur["value"], "after": f["value"], "certainty": f["certainty"]})
    return changes
