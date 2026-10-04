"""Turn a Slack thread into structured data: type, parts, urgency, people, summary.

Split of work, on purpose:
- Plain code does what must be reliable and explainable: alias matching for parts
  ("43045-0412" -> J4 connector), @-mentions and names, and whether a question got a reply.
- The LLM does what needs language understanding: the type of thread, urgency,
  a one-line summary, and parts or people referred to without their exact name.

Caching: data/cache/extractions.json, keyed by thread id, message count and a content hash
("<thread_ts>:<n>:<hash>"). A thread that gets a new reply is a new snapshot and is extracted
again; a snapshot we've seen before never goes back to the LLM.
"""
import hashlib
import json
import re

import requests

import config
from catalog import find_mentions, match_parts
from slack_loader import clean_text, load_messages, load_team, ts_to_dt

TYPES = ["problem", "decision", "question", "update", "noise"]


# ---------- deterministic part ----------


def match_people(thread, team):
    """Return (tagged, named): people @-tagged with <@U_ID>, and people referred to by first name.
    A tag is a direct ask; a name ("Mei is chasing it") is only a mention. Self-mentions don't count."""
    tagged, named = set(), set()
    for m in thread["messages"]:
        tagged |= {u for u in re.findall(r"<@(\w+)>", m["text"]) if u != m.get("user")}
        for p in team["people"]:
            first = p["name"].split()[0]
            if p["id"] != m.get("user") and re.search(rf"\b{first}\b", m["text"]):
                named.add(p["id"])
    return tagged, named


def unanswered_hours(thread, as_of):
    """If the thread opens with a question that nobody but the asker has replied to,
    return how many hours it has been waiting at `as_of`. Otherwise None.
    (The asker bumping their own question doesn't count as an answer.)"""
    root = thread["messages"][0]
    if "?" not in root["text"]:
        return None
    replies = [m for m in thread["messages"][1:] if ts_to_dt(m["ts"]) <= as_of]
    if any(m.get("user") != root.get("user") for m in replies):
        return None
    return (as_of - ts_to_dt(root["ts"])).total_seconds() / 3600


# ---------- LLM prompt ----------

def build_prompt(thread, team):
    names = {p["id"]: p["name"] for p in team["people"]}
    roles = {p["id"]: p["role"] for p in team["people"]}
    people = "\n".join(f"- {p['id']}: {p['name']} ({p['role']})" for p in team["people"])
    parts = "\n".join(f"- {e['name']} (also called: {', '.join(e['aliases'])})" for e in team["catalog"]["entries"])
    system = (
        f"You read Slack threads from a hardware team building the {team['project']['name']}.\n"
        "Return structured data about the thread.\n\n"
        f"People:\n{people}\n\nParts and subsystems:\n{parts}\n\n"
        "type, pick one:\n"
        "- problem: something is broken, failing, late, at risk, or someone is blocked\n"
        "- decision: the team settled on a choice or the project changed phase\n"
        "- question: someone needs an answer from someone else\n"
        "- update: work progress or information, nothing broken\n"
        "- noise: social or off-topic (food, pets, lost tools, office logistics)\n"
        "parts_mentioned: official names from the parts list above that the thread is about "
        "(people use nicknames; always answer with the official name).\n"
        "urgency: 1 (ignore) to 5 (drop everything). Schedule risk or a blocked person is 4+.\n"
        "unknown_parts: hardware parts mentioned that are NOT in the parts list, exactly as written "
        "(e.g. a part number or nickname you can't map). Empty list if none.\n"
        "people_mentioned: IDs of people addressed or referred to in the text.\n"
        "summary: one short sentence."
    )
    lines = [f"{names.get(m.get('user'), m.get('user'))} ({roles.get(m.get('user'), '?')}): "
             f"{clean_text(m['text'], names)}" for m in thread["messages"]]
    user = f"Channel: #{thread['channel_name']}\n" + "\n".join(lines)
    schema = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": TYPES},
            "parts_mentioned": {"type": "array", "items": {"type": "string", "enum": team["catalog"]["names"]}},
            "unknown_parts": {"type": "array", "items": {"type": "string"}},
            "urgency": {"type": "integer"},
            "people_mentioned": {"type": "array", "items": {"type": "string", "enum": [p["id"] for p in team["people"]]}},
            "summary": {"type": "string"},
        },
        "required": ["type", "parts_mentioned", "unknown_parts", "urgency", "people_mentioned", "summary"],
        "additionalProperties": False,
    }
    return system, user, schema


# ---------- providers ----------

def call_ollama(system, user, schema):
    resp = requests.post(f"{config.OLLAMA_URL}/api/chat", json={
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,           # Ollama constrains decoding to this JSON schema
        "stream": False,
        "think": False,             # small model + fixed schema: thinking adds latency, not accuracy
        "options": {"temperature": 0},
    }, timeout=180)
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])


def call_anthropic(system, user, schema):
    import anthropic  # imported lazily so "none"/"ollama" don't need an API key or the SDK

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY (loaded from .env by config)
    response = client.beta.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        # Short classification task: low effort keeps it cheap. The schema guarantees valid JSON.
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        # If a safety classifier declines, the API retries on a recommended fallback model server-side.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Model declined to process this thread")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def call_provider(system, user, schema, provider):
    """One structured-output call to whichever provider is configured. Also used by digest.py."""
    if provider == "ollama":
        return call_ollama(system, user, schema)
    if provider == "anthropic":
        return call_anthropic(system, user, schema)
    raise ValueError(f"Unknown provider {provider!r}")


def model_name(provider):
    return f"{provider}:{config.OLLAMA_MODEL if provider == 'ollama' else config.ANTHROPIC_MODEL}"


def call_llm(thread, team, provider):
    return call_provider(*build_prompt(thread, team), provider)


# ---------- keyword fallback (provider="none" and no cached result) ----------

NOISE_WORDS = ["lunch", "pizza", "drinks", "dog", "calipers", "coffee", "slides", "standup", "AC "]
PROBLEM_WORDS = ["problem", "over spec", "blocked", "EOL", "lead time", "slips", "bad", "fail", "too stiff"]


def keyword_extract(thread, team):
    """Crude but free. It shows what the LLM adds, and keeps the tool running with no cache."""
    text = " ".join(m["text"] for m in thread["messages"])
    low = text.lower()
    if "decision" in low or "officially in" in low:
        kind = "decision"
    elif any(w.lower() in low for w in PROBLEM_WORDS):
        kind = "problem"
    elif any(w.lower() in low for w in NOISE_WORDS):
        kind = "noise"
    elif "?" in thread["messages"][0]["text"]:
        kind = "question"
    else:
        kind = "update"
    return {
        "type": kind,
        "parts_mentioned": [],  # catalog matching is added for every provider in extract()
        "unknown_parts": [],    # pattern-based unknown-part detection is added in extract() too
        "urgency": {"problem": 4, "decision": 3, "question": 3, "update": 2, "noise": 1}[kind],
        "people_mentioned": [],
        "summary": clean_text(thread["messages"][0]["text"], {p["id"]: p["name"] for p in team["people"]})[:120],
    }


# ---------- cache ----------

def load_cache():
    if config.EXTRACTIONS_CACHE.exists():
        return json.loads(config.EXTRACTIONS_CACHE.read_text())
    return {}


def save_cache(cache):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.EXTRACTIONS_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def cache_key(thread):
    """Thread id + message count + a hash of the text and prompt version. The count makes each
    day's snapshot distinct; the hash makes an edited message or a changed prompt a cache miss
    instead of silently reusing an extraction of different text."""
    content = config.PROMPT_VERSION + "".join(m["ts"] + m["text"] for m in thread["messages"])
    return f"{thread['thread_ts']}:{len(thread['messages'])}:{hashlib.sha256(content.encode()).hexdigest()[:8]}"


# ---------- main entry ----------

def extract(thread, team, cache, provider=config.LLM_PROVIDER):
    """Structured data for one thread snapshot. Mutates `cache` when the LLM is called."""
    key = cache_key(thread)
    if key in cache:
        llm, source = cache[key], "cache"
    elif provider == "none":
        llm, source = keyword_extract(thread, team), "keywords"  # not cached: free to recompute
    else:
        llm = call_llm(thread, team, provider)
        llm["_model"] = model_name(provider)
        cache[key] = llm
        save_cache(cache)  # save after every call so a crash halfway doesn't lose paid-for work
        source = "llm"

    # Deterministic facts are recomputed every time: cheap, and they follow catalog/team edits.
    # Every mention is mapped to its official name; the evidence keeps what was actually written.
    parts, unknown = {}, []
    for m in thread["messages"]:  # per message, so a fuzzy match never spans two messages
        found, unk = find_mentions(m["text"], team["catalog"])
        for name, written in found.items():
            parts.setdefault(name, f'mentioned as "{written}"')
        unknown += unk
    llm_parts = [n for n in llm.get("parts_mentioned", []) if n in team["catalog"]["subsystem_of"]]
    for name in llm_parts:
        parts.setdefault(name, "linked by the LLM")
    # Unknown parts: the LLM's list (minus anything the catalog can map after all) + the pattern backup.
    for phrase in llm.get("unknown_parts", []):
        if phrase.strip() and not match_parts(phrase, team["catalog"]):
            unknown.append(phrase.strip())
    unknown = list(dict.fromkeys(unknown))  # dedupe, keep order
    # People: the text is the source of truth. Small models tend to list everyone in the team,
    # so the LLM's people list is kept for display only and never forces an item into a digest.
    tagged, named = match_people(thread, team)
    valid_ids = {p["id"] for p in team["people"]}

    return {
        "thread_ts": thread["thread_ts"],
        "type": llm["type"],
        "urgency": max(1, min(5, int(llm["urgency"]))),  # clamp: small models sometimes ignore the scale
        "summary": llm["summary"],
        "parts": parts,                    # {part: evidence}, evidence becomes part of the reason text
        "parts_llm": llm_parts,            # what the LLM alone said (for measuring it separately)
        "unknown_parts": unknown,          # mentions that look like parts but aren't in the catalog
        "people_tagged": sorted(tagged),   # <@U_ID>: a direct ask
        "people_mentioned": sorted(tagged | named),
        "people_llm": sorted(p for p in llm.get("people_mentioned", []) if p in valid_ids),
        "source": source,
    }


if __name__ == "__main__":
    import sys
    from slack_loader import all_days, end_of_day, threads_active_on

    provider = sys.argv[1] if len(sys.argv) > 1 else config.LLM_PROVIDER
    team, messages, cache = load_team(), load_messages(), load_cache()
    print(f"provider={provider}\n")
    for day in all_days(messages):
        for thread in threads_active_on(messages, day):
            x = extract(thread, team, cache, provider)
            waiting = unanswered_hours(thread, end_of_day(day))
            flag = f"UNANSWERED {waiting:.0f}h" if waiting and waiting >= config.UNANSWERED_AFTER_HOURS else ""
            print(f"{day} #{thread['channel_name']:<13} {x['type']:<9} u{x['urgency']} {flag:<15} "
                  f"parts={','.join(x['parts']) or '-':<45} people={','.join(x['people_mentioned']) or '-'} [{x['source']}]")
