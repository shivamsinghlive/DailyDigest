"""Build one person's digest for one day. Two parts:

  Team Pulse  the same few items for everyone, so personalization doesn't create silos.
              Ranked by type (phase change > schedule risk > major decision > ...) + severity
              + breadth (how many people and subsystems it touches).
              On quiet days, still-open problems and questions fill the slots.
  For You     the person's own top items (ranker.py), never repeating what's in the pulse.

The LLM only writes the short intro. Items, channels and reasons always come straight from the
notebook and ranker, so a reason can never be invented or dropped by the model.
"""
import hashlib
import json
import re
from datetime import date

from . import config
from .extract import call_provider, model_name
from .notebook import changes_on, state_as_of
from .ranker import CATEGORY, focus_scores, rank_for_person, score_change

DIGEST_CACHE = config.CACHE_DIR / "digests.json"

KIND_LABEL = {
    "new_problem": "New problem", "problem_update": "Problem update", "decision": "Decision",
    "new_question": "Question", "question_unanswered": "Unanswered question",
    "question_answered": "Question answered", "update": "Update", "phase_change": "Phase change",
    "still_open": "Still open",
}
# Team-wide importance of each type of change, before severity and breadth. Type dominates on
# purpose: phase changes, schedule risks and major decisions are what everyone must know; a routine
# problem only makes the pulse if it's both severe and touches nearly everyone.
PULSE_TYPE = {"phase_change": 8, "decision": 1, "new_problem": 1, "question_unanswered": 1,
              "problem_update": 0.5, "new_question": 0, "update": 0, "question_answered": 0}
SCHEDULE_RISK_BONUS = 6   # anything that moves the build date matters to everyone
MAJOR_DECISION_BONUS = 4  # an urgent decision (4+/5) is a major one


def pretty_day(day):
    return date.fromisoformat(day).strftime("%a %b %-d")


def first_name(person):
    return person["name"].split()[0]


# ---------- Team Pulse ----------

def relevant_people(change, state, team):
    """Everyone whose personal score (with no feedback applied) would put this change in their digest."""
    out = []
    for p in team["people"]:
        score, must, _ = score_change(change, p, state, team, focus_scores(p["id"], state, change["day"]))
        if must or score >= config.MIN_SCORE:
            out.append(p)
    return out


def pulse_score(change, relevant):
    """type + severity + breadth. Returns (score, is_major_decision)."""
    major = change["kind"] == "decision" and change["urgency"] >= 4
    score = (PULSE_TYPE[change["kind"]] + (SCHEDULE_RISK_BONUS if change["schedule_risk"] else 0)
             + (MAJOR_DECISION_BONUS if major else 0)
             + change["urgency"]                                      # severity
             + len(relevant) + 0.5 * len(change["subsystems"]))      # breadth: people + subsystems
    return score, major


def team_reasons(change, relevant, major, team):
    kind, reasons = change["kind"], []
    if kind == "phase_change":
        moved = ", ".join(f"{s} → {p}" for s, p in change["new_phases"].items())
        still = ", ".join(f"{s} still {p}" for s, p in change["unchanged_phases"].items())
        reasons.append(f"Phase change: {moved}" + (f" ({still})" if still else ""))
    if change["schedule_risk"]:
        reasons.append("Schedule risk: this could move the build date")
    if major:
        reasons.append("A major decision the whole team should know about")
    elif kind == "decision":
        reasons.append("A decision")
    elif kind in ("new_problem", "problem_update") and not change["schedule_risk"]:
        reasons.append("Open problem")
    elif kind == "question_unanswered":
        reasons.append(f"Unanswered for {change.get('waiting_hours', 48)}h; someone is waiting on it")
    if len(relevant) >= 2:
        reasons.append(f"Matters to {len(relevant)} of {len(team['people'])} people: "
                       + ", ".join(first_name(p) for p in relevant))
    if len(change["subsystems"]) >= 2:
        reasons.append(f"Touches {len(change['subsystems'])} subsystems: {', '.join(change['subsystems'])}")
    if change["urgency"] >= 4:
        reasons.append(f"Marked urgent ({change['urgency']}/5)")
    return reasons


def team_pulse(day, timeline, team):
    """The same items for everyone on `day`. Nobody's feedback changes it, so it stays shared."""
    state = state_as_of(timeline, day)
    scored = []
    for c in changes_on(timeline, day):
        relevant = relevant_people(c, state, team)
        score, major = pulse_score(c, relevant)
        if score >= config.PULSE_MIN_SCORE:
            scored.append((score, {"label": KIND_LABEL[c["kind"]], "summary": c["summary"], "channel": c["channel_name"],
                                   "thread_ts": c["thread_ts"], "urgency": c["urgency"], "change": c,
                                   "category": CATEGORY[c["kind"]], "reasons": team_reasons(c, relevant, major, team)}))
    scored.sort(key=lambda s: s[0], reverse=True)
    pulse = [item for _, item in scored[:config.PULSE_SIZE]]

    # Quiet day: remind everyone what's still open, so nothing quietly drops off the radar.
    if len(pulse) < config.PULSE_SIZE:
        shown = {i["thread_ts"] for i in pulse}
        today = date.fromisoformat(day)
        open_items = ([(o, "Open problem with no resolution yet") for o in state["open_problems"].values()] +
                      [(o, "Question still has no answer") for o in state["unanswered_questions"].values() if o["flagged"]])
        # Only older items: something raised today is news, and competes on its own score above.
        open_items = [(o, why) for o, why in open_items if o["thread_ts"] not in shown and o["since"] < day
                      and (today - date.fromisoformat(o["last_day"])).days <= config.PULSE_CARRY_OVER_DAYS]
        open_items.sort(key=lambda ow: (-ow[0]["urgency"], ow[0]["since"]))
        for o, why in open_items[:config.PULSE_SIZE - len(pulse)]:
            pulse.append({"label": KIND_LABEL["still_open"], "summary": o["summary"], "channel": o["channel_name"],
                          "thread_ts": o["thread_ts"], "urgency": o["urgency"], "change": None, "category": "problem",
                          "reasons": [f"{why} (open since {pretty_day(o['since'])})"]})
    return pulse


# ---------- For You ----------

def to_items(ranked):
    return [{
        "label": KIND_LABEL[r["change"]["kind"]],
        "summary": r["change"]["summary"],
        "channel": r["change"]["channel_name"],
        "thread_ts": r["change"]["thread_ts"],
        "urgency": r["change"]["urgency"],
        "must": r["must"],
        "score": r["score"],
        "reasons": r["reasons"],
        "category": r["category"],  # the item type a 👍/👎 is recorded against
    } for r in ranked]


def personal_note(pulse_item, person, state, team, prefs):
    """Why a team-wide item also matters to *you*, if it does."""
    c = pulse_item["change"]
    if c is None or person["id"] in c["authors_today"]:
        return []
    score, must, reasons = score_change(c, person, state, team, focus_scores(person["id"], state, c["day"]), prefs)
    # Phase-change and team-level reasons are already on the item; keep only the personal ones.
    return [r for r in reasons if not r.startswith("Phase change")] if (must or score >= config.MIN_SCORE) else []


# ---------- intro ----------

def template_intro(person, day, phases, pulse, mine):
    stage = " · ".join(f"{s} {p}" for s, p in phases.items())
    if not pulse and not mine:
        return f"Hi {first_name(person)}, a quiet {pretty_day(day)}: nothing new for you. ({stage})"
    must = sum(i["must"] for i in mine)
    parts = []
    if pulse:
        parts.append(f"{len(pulse)} team-wide item{'s' if len(pulse) > 1 else ''}")
    if mine:
        parts.append(f"{len(mine)} just for you" + (f" ({must} you shouldn't skip)" if must else ""))
    return f"Hi {first_name(person)}, {' and '.join(parts)} from {pretty_day(day)}. ({stage})"


def clean_intro(text):
    """Small models sometimes echo bits of the prompt in braces ("{firmware; why: ...}").
    Strip those chunks; if what's left still looks wrong, the caller uses the template."""
    return re.sub(r"\s*\{[^{}]*\}", "", text or "").strip()


def intro_looks_ok(text):
    return bool(text) and "{" not in text and "}" not in text and 20 <= len(text) <= 600


def load_cache():
    return json.loads(DIGEST_CACHE.read_text()) if DIGEST_CACHE.exists() else {}


def cache_key(person, day, pulse, mine):
    """Same person, day and items -> same intro. If anything shown changes, the key changes too."""
    content = config.PROMPT_VERSION + json.dumps([[i["thread_ts"], i["reasons"]] for i in pulse + mine])
    return f"{person['id']}:{day}:{hashlib.sha256(content.encode()).hexdigest()[:10]}"


def llm_intro(person, day, phases, pulse, mine, provider):
    system = (
        "You write the opening of a daily work digest for one person on a robotics hardware team. "
        "Write 2 or 3 short, friendly sentences: first what the whole team should know, then what matters to this "
        "person and why. Use only the facts given. Don't ask the reader for anything. No bullet points, no emojis."
    )
    fmt = lambda items: "\n".join(f"- [{i['label']}] {i['summary']} (#{i['channel']}; why: {'; '.join(i['reasons'])})"
                                  for i in items) or "- (none)"
    user = (f"Person: {person['name']}, {person['role'].replace('_', ' ')}. Day: {pretty_day(day)}. "
            f"Subsystem phases: {phases}.\nTeam-wide items:\n{fmt(pulse)}\nFor this person:\n{fmt(mine)}")
    schema = {"type": "object", "properties": {"intro": {"type": "string"}},
              "required": ["intro"], "additionalProperties": False}
    return call_provider(system, user, schema, provider)["intro"]


# ---------- the digest ----------

def build_digest(person, day, timeline, team, provider=config.LLM_PROVIDER, cache=None, prefs=None, pulse=None):
    """Team Pulse + For You for one person and day. `prefs` = feedback.type_preferences(person).
    Pass `pulse` to reuse it across people."""
    state = state_as_of(timeline, day)
    pulse = team_pulse(day, timeline, team) if pulse is None else pulse
    pulse = [{**i, "for_you": personal_note(i, person, state, team, prefs)} for i in pulse]
    mine = to_items(rank_for_person(person, day, timeline, team, prefs=prefs,
                                    exclude={i["thread_ts"] for i in pulse}))

    cache = load_cache() if cache is None else cache
    key = cache_key(person, day, pulse, mine)
    if not mine and not pulse:
        intro, source = template_intro(person, day, state["phases"], pulse, mine), "template"
    elif key in cache:
        intro, source = cache[key]["intro"], f"cache ({cache[key]['model']})"
    elif provider == "none":
        intro, source = template_intro(person, day, state["phases"], pulse, mine), "template"
    else:
        try:
            intro, source = llm_intro(person, day, state["phases"], pulse, mine, provider), model_name(provider)
            cache[key] = {"intro": intro, "model": model_name(provider)}  # cached even if bad: never pay twice
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            DIGEST_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
        except Exception as err:  # the intro is cosmetic: a slow or failed LLM call must not block the digest
            intro, source = template_intro(person, day, state["phases"], pulse, mine), f"template (LLM failed: {type(err).__name__})"
    intro = clean_intro(intro)
    if not intro_looks_ok(intro):
        intro, source = template_intro(person, day, state["phases"], pulse, mine), f"template (rejected {source} intro)"

    return {"person": person, "day": day, "phases": state["phases"], "intro": intro, "intro_source": source,
            "pulse": pulse, "for_you": mine}


def to_markdown(d):
    """Plain-text rendering for the CLI. The Streamlit app renders the same dict its own way."""
    stage = " · ".join(f"{s} {p}" for s, p in d["phases"].items())
    out = [f"### {d['person']['name']} · {pretty_day(d['day'])} · {stage}", "", d["intro"], "", "**Team Pulse**"]
    for i in d["pulse"] or []:
        out.append(f"- **{i['label']}**: {i['summary']}  _(#{i['channel']})_")
        out += [f"   - why: {r}" for r in i["reasons"]]
        out += [f"   - for you: {r}" for r in i["for_you"]]
    out += ["", "**For You**"] + ([] if d["for_you"] else ["- nothing else today"])
    for n, i in enumerate(d["for_you"], 1):
        out.append(f"{n}. **{i['label']}**{' ⚑' if i['must'] else ''}: {i['summary']}  _(#{i['channel']})_")
        out += [f"   - why: {r}" for r in i["reasons"]]
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    from .feedback import type_preferences
    from .notebook import load_notebook

    # usage: python digest.py [day|all] [provider]   e.g.  python digest.py 2026-09-21 ollama
    timeline, team = load_notebook()
    days = [sys.argv[1]] if len(sys.argv) > 1 and sys.argv[1] != "all" else sorted(timeline)
    provider = sys.argv[2] if len(sys.argv) > 2 else config.LLM_PROVIDER
    cache = load_cache()
    for day in days:
        pulse = team_pulse(day, timeline, team)
        for person in team["people"]:
            d = build_digest(person, day, timeline, team, provider, cache, type_preferences(person["id"]), pulse)
            print(to_markdown(d), f"\n   [intro: {d['intro_source']}]\n")
