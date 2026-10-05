"""Issue identity: several threads, one underlying problem.

"Slew bearing lot failed hardness check" (#supply-chain) and "slew bearings slipping 3 weeks,
vendor heat treat" (#general) are one issue. When a thread reports a new problem:

  1. Code shortlists existing issues that share a specific part with it (memory.retrieve, capped).
     No shared part, no link: wording alone ("the latch keeps popping") is too weak.
  2. Haiku picks one of those candidates or "none". It can only choose from the shortlist,
     so it can't invent an issue, and the result is cached like every LLM output.
  3. With no LLM and nothing cached, code links only on strong word overlap.

The notebook then keeps one memory key per issue, whatever thread or channel it shows up in.
"""
import hashlib
import json
import re

from . import config
from .extract import call_provider, model_name
from .memory import retrieve

LINK_PROMPT_VERSION = "l1"
STOPWORDS = set("""the a an and or of to in on for with at by from is are was were be been it its this that
these those we our they their has have had not no but if then than so as into after before over under
about due still need needs needed can could will would should may might just also only more less very""".split())


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def similarity(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def specific_parts(x, team):
    """Parts written in the thread that are specific enough to link on (not "wrist assembly", not topics)."""
    from .notebook import linkable
    cat = team["catalog"]
    return {p for p, ev in x["parts"].items() if ev.startswith("mentioned as") and linkable(p, cat)}


def candidates(mem, x, team, day, own_key):
    parts = specific_parts(x, team)
    if not parts:
        return []
    # Issues only: other memories (facts, ownership) must not change the shortlist, or cached verdicts go stale.
    found = retrieve(mem, parts=parts, as_of=day, limit=config.LINK_MAX_CANDIDATES + 1, types={"ISSUE"})
    return [m for m in found if m["key"] != own_key and set(m["parts"]) & parts][:config.LINK_MAX_CANDIDATES]


def link_prompt(x, cands):
    system = ("You link Slack threads from a hardware team to the issue they are about. "
              "Pick the existing issue that is the SAME underlying problem as the new report (same failure, "
              "same supplier problem, same root cause), or \"none\". Sharing a part is not enough: a connector "
              "latch popping and the same connector going end-of-life are different issues.")
    listing = "\n".join(f"- {m['key']}: {m['summary']} (since {m['created_at']}, status {m['status'].lower()})" for m in cands)
    user = f"New report: {x['summary']}\n\nExisting issues:\n{listing}"
    schema = {"type": "object", "properties": {
        "same_as": {"type": "string", "enum": [m["key"] for m in cands] + ["none"]},
        "reason": {"type": "string"}}, "required": ["same_as", "reason"], "additionalProperties": False}
    return system, user, schema


def link_key(x, cands):
    content = LINK_PROMPT_VERSION + x["summary"] + "".join(m["key"] + m["summary"] for m in cands)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def load_links():
    path = config.CACHE_DIR / "links.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_links(links):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (config.CACHE_DIR / "links.json").write_text(json.dumps(links, indent=2, ensure_ascii=False))


def find_issue(mem, x, team, day, own_key, provider, links):
    """The memory key of the existing issue this new problem belongs to, and why; (None, None) if it's new."""
    cands = candidates(mem, x, team, day, own_key)
    if not cands:
        return None, None
    key = link_key(x, cands)
    if key not in links and provider != "none":
        verdict = call_provider(*link_prompt(x, cands), provider)
        links[key] = {"same_as": verdict["same_as"], "reason": verdict["reason"], "model": model_name(provider)}
        save_links(links)  # save after every call so a crash doesn't lose paid-for work
    if key in links:
        same = links[key]["same_as"]
        valid = {m["key"] for m in cands}
        return (same, f"linked by {links[key]['model']}: {links[key]['reason']}") if same in valid else (None, None)
    # No LLM: only near-identical wording counts.
    best = max(cands, key=lambda m: similarity(x["summary"], m["summary"]))
    if similarity(x["summary"], best["summary"]) >= config.LINK_MIN_SIMILARITY:
        return best["key"], f"same part and similar wording ({similarity(x['summary'], best['summary']):.0%} overlap)"
    return None, None
