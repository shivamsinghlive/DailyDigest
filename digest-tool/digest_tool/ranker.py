"""Score each notebook change for each person and explain why.

Score = owns a part in it              (declared in team.json > likely > possible, inferred from activity)
      + mentioned by name
      + matters to role at this stage  (IMPORTANT_FOR[role][phase of the subsystem it touches])
      + recent focus on its parts      (exponential decay)
      + urgency
      then × this person's feedback multiplier for this type of item (feedback.py)

Weights in config.WEIGHTS are starting guesses.
Some items skip scoring and are always included ("must"): you were asked directly,
something is blocked on you, or a subsystem changed phase.
Every point added comes with a plain-English reason, so the digest can say *why*.
"""
from datetime import date

from . import config
from .notebook import changes_on, overall_phase, state_as_of

# What kinds of change each role cares about in each phase. Ownership and focus are
# handled separately, so this table only covers "I'd want to know even if it's not my part".
# Example: in EVT a manager needs every problem (things are breaking), while a PM mostly cares
# about schedule, which reaches them anyway through owning the DVT build schedule.
IMPORTANT_FOR = {
    "mechanical_engineer": {
        "Concept": ["decision", "question"],
        "EVT": ["problem", "question"],
        "DVT": ["problem", "decision", "unanswered_question"],
        "PVT": ["problem"],
        "Production": ["problem"],
    },
    "electrical_engineer": {
        "Concept": ["decision", "question"],
        "EVT": ["problem", "question"],
        "DVT": ["problem", "decision", "unanswered_question"],
        "PVT": ["problem"],
        "Production": ["problem"],
    },
    "supply_chain": {
        "Concept": [],
        "EVT": ["decision"],                 # design choices turn into parts to source
        "DVT": ["decision", "problem"],      # build volumes: any problem may mean a re-order
        "PVT": ["decision", "problem"],
        "Production": ["problem"],
    },
    "engineering_manager": {
        "Concept": ["decision"],
        "EVT": ["problem", "decision", "unanswered_question"],
        "DVT": ["problem", "decision", "unanswered_question"],
        "PVT": ["problem", "unanswered_question"],
        "Production": ["problem"],
    },
    "product_manager": {
        "Concept": ["decision", "question"],
        "EVT": [],
        "DVT": ["problem"],                  # after freeze, any problem threatens the date
        "PVT": ["problem"],
        "Production": ["problem", "decision"],
    },
}

# notebook change kind -> category used in IMPORTANT_FOR
CATEGORY = {
    "new_problem": "problem", "problem_update": "problem", "decision": "decision",
    "new_question": "question", "question_unanswered": "unanswered_question",
    "question_answered": "update", "update": "update", "phase_change": "phase_change",
    "change_after_freeze": "change_after_freeze",
}

ROLE_LABEL = {
    "mechanical_engineer": "mechanical engineers", "electrical_engineer": "electrical engineers",
    "supply_chain": "supply chain", "engineering_manager": "engineering managers",
    "product_manager": "product managers",
}
CATEGORY_LABEL = {
    "problem": "Problems", "decision": "Decisions", "question": "Open questions",
    "unanswered_question": "Unanswered questions", "update": "Updates", "phase_change": "Phase changes",
    "change_after_freeze": "Changes after design freeze",
}


def focus_scores(person_id, state, day):
    """{part: decayed mention count} over the last FOCUS_WINDOW_DAYS.
    A mention today counts 1, FOCUS_HALF_LIFE_DAYS ago counts 0.5, and so on,
    so the score follows what someone is working on *now*, not last month."""
    today = date.fromisoformat(day)
    scores = {}
    for d, counts in state["activity"].get(person_id, {}).items():
        age = (today - date.fromisoformat(d)).days
        if 0 <= age < config.FOCUS_WINDOW_DAYS:
            weight = 0.5 ** (age / config.FOCUS_HALF_LIFE_DAYS)
            for part, n in counts.items():
                scores[part] = scores.get(part, 0) + n * weight
    return scores


def raw_mentions(person_id, state, day, part):
    """Plain count for the reason text ("6 mentions this week") — decay is for scoring, not for people."""
    today = date.fromisoformat(day)
    return sum(counts.get(part, 0) for d, counts in state["activity"].get(person_id, {}).items()
               if 0 <= (today - date.fromisoformat(d)).days < config.FOCUS_WINDOW_DAYS)


def phases_for(change, state, team):
    """The phases that apply to a change: those of the subsystems it touches, or the project's
    overall phase if it touches none (a schedule or supplier thread, say)."""
    if change["subsystems"]:
        return {s: state["phases"][s] for s in change["subsystems"]}
    return {"project": overall_phase(state["phases"])}


CONFIDENCE_RANK = {"declared": 3, "likely": 2, "possible": 1}


def evidence_text(evidence):
    """'you've mentioned it 4 times, answered 2 questions about it' from the evidence counts."""
    n = lambda k: evidence.get(k, 0)
    times = lambda k: "once" if n(k) == 1 else f"{n(k)} times"
    bits = []
    if n("mention") + n("question"):
        bits.append(f"you've mentioned it {n('mention') + n('question')} time{'s' if n('mention') + n('question') > 1 else ''}")
    if n("answer"):
        bits.append(f"answered {n('answer')} question{'s' if n('answer') > 1 else ''} about it")
    if n("asked_about"):
        bits.append(f"been asked about it {times('asked_about')}")
    return ", ".join(bits)


def ownership_reason(part, owner, evidence_of_mention):
    where = f" ({evidence_of_mention})" if evidence_of_mention.startswith("mentioned") else " (linked by the LLM)"
    if owner["confidence"] == "declared":
        return f"You own the {part}{where}, per the team roster"
    word = "likely own" if owner["confidence"] == "likely" else "may own"
    return f"You {word} the {part}{where}: {evidence_text(owner['evidence'])} (not on the roster, inferred from activity)"


def score_change(change, person, state, team, focus, prefs=None):
    """Return (score, must_include, reasons) for one change and one person.
    `prefs` is {category: multiplier} from this person's 👍/👎 (feedback.py)."""
    pid, w = person["id"], config.WEIGHTS
    points, must, reasons = 0.0, False, []
    category = CATEGORY[change["kind"]]
    # Parts named in the text are facts; parts only the LLM linked are guesses and count for less.
    named = [p for p, ev in change["parts"].items() if ev.startswith("mentioned")]
    mine = {p: o for p in change["parts"] for o in state["owners"].get(p, []) if o["person"] == pid}
    mine_named = {p: o for p, o in mine.items() if p in named}
    mine_llm = {p: o for p, o in mine.items() if p not in named and o["confidence"] != "possible"}
    confident = {p for p, o in mine_named.items() if o["confidence"] in ("declared", "likely")}

    # --- always-include rules ---
    if change["kind"] == "phase_change":
        must = True
        moved = ", ".join(f"{s} → {p}" for s, p in change["new_phases"].items())
        still = ", ".join(f"{s} still {p}" for s, p in change["unchanged_phases"].items())
        reasons.append(f"Phase change: {moved}" + (f" ({still})" if still else ""))
    if pid in change["people_tagged"]:
        must = True
        if change["kind"] == "question_unanswered":
            reasons.append(f"Someone has been waiting {change.get('waiting_hours', 48)}h for your answer")
        else:
            reasons.append("You were tagged directly")
    elif change["kind"] == "question_unanswered" and confident:
        must = True
        reasons.append(f"A question about your {sorted(confident)[0]} has had no answer for {change.get('waiting_hours', 48)}h")
    if category == "problem" and confident and change["urgency"] >= 4:
        must = True  # a serious problem on your own part is blocking you whether or not you're tagged
    if change["kind"] == "change_after_freeze":
        # The manager approves ECOs; the owner has to know their frozen part just changed.
        owns_frozen = [p for p in change["frozen_parts"] for o in state["owners"].get(p, [])
                       if o["person"] == pid and o["confidence"] in ("declared", "likely")]
        if person["role"] == "engineering_manager" or owns_frozen:
            must = True
            where = ", ".join(f"{s} is in {ph}" for s, ph in change["frozen_phases"].items())
            reasons.append(f"Design change after freeze with no ECO yet: {', '.join(change['frozen_parts'])} ({where})")

    # --- scored signals ---
    if mine_named:
        best = sorted(mine_named.items(), key=lambda po: -CONFIDENCE_RANK[po[1]["confidence"]])
        points += w["owns_" + best[0][1]["confidence"]]
        reasons += [ownership_reason(p, o, change["parts"][p]) for p, o in best[:2]]
    elif mine_llm:
        part, owner = next(iter(mine_llm.items()))
        points += w["owns_llm_linked"]
        reasons.append(ownership_reason(part, owner, change["parts"][part]))

    if pid in change["people_mentioned"] and pid not in change["people_tagged"]:
        points += w["mentioned"]
        reasons.append("You're mentioned by name")

    phases = phases_for(change, state, team)
    matching = [s for s, ph in phases.items() if category in IMPORTANT_FOR[person["role"]].get(ph, [])]
    if matching:
        points += w["role_phase"]
        where = ", ".join(f"{s} is in {phases[s]}" for s in matching)
        reasons.append(f"{CATEGORY_LABEL[category]} are a priority for {ROLE_LABEL[person['role']]} at this stage ({where})")
    elif category == "question" and change["urgency"] >= 4 and person["role"] == "engineering_manager":
        # An urgent open question means someone is blocked on it, in any phase.
        points += w["role_phase"]
        reasons.append("An urgent open question: someone is blocked, and unblocking people is the manager's job")

    # Focus only uses parts named in the text, so one LLM guess can't pull in unrelated people.
    best_part = max(named, key=lambda p: focus.get(p, 0), default=None)
    # "Lately" means repeated activity: one passing mention isn't focus.
    if best_part and focus.get(best_part, 0) > 0 and raw_mentions(pid, state, change["day"], best_part) >= config.FOCUS_MIN_MENTIONS:
        strength = min(1.0, focus[best_part] / config.FOCUS_SATURATION)
        points += w["focus"] * strength
        if strength >= 0.3 and best_part not in mine_named:  # "you own it" already says enough
            n = raw_mentions(pid, state, change["day"], best_part)
            reasons.append(f"You've been working on the {best_part} lately ({n} mentions in the last week)")

    if change["urgency"] > 2:
        points += w["urgency_per_level"] * (change["urgency"] - 2)
        if change["urgency"] >= 4:
            reasons.append(f"Marked urgent ({change['urgency']}/5)")

    # Your 👍/👎 on this kind of item scale the score up or down (feedback.py).
    multiplier = (prefs or {}).get(category, {}).get("multiplier", 1.0)
    if multiplier != 1.0:
        votes = prefs[category]
        reasons.append(f"You've rated {CATEGORY_LABEL[category].lower()} 👍 {votes['up']}× / 👎 {votes['down']}×, "
                       f"so they count {'more' if multiplier > 1 else 'less'} for you (×{multiplier:.1f})")
    score = round(points * multiplier, 2)

    # Not scored, but it's the point of cross-team alerts: say where this came from.
    if (must or score >= config.MIN_SCORE) and change["channel_name"] not in state["channels"].get(pid, []):
        reasons.append(f"Posted in #{change['channel_name']}, a channel you don't post in")

    return score, must, reasons


def rank_for_person(person, day, timeline, team, top_n=config.TOP_N, prefs=None, exclude=()):
    """Top items for one person on one day, most important first.
    `prefs`: this person's feedback multipliers per item type (feedback.type_preferences).
    `exclude`: thread_ts already shown elsewhere (the Team Pulse), so they aren't repeated."""
    state = state_as_of(timeline, day)
    focus = focus_scores(person["id"], state, day)
    items = []
    for change in changes_on(timeline, day):
        # You already took part in this thread today, so you've seen it.
        if person["id"] in change["authors_today"] or change["thread_ts"] in exclude:
            continue
        score, must, reasons = score_change(change, person, state, team, focus, prefs)
        if must or score >= config.MIN_SCORE:
            items.append({"change": change, "score": score, "must": must, "reasons": reasons, "category": CATEGORY[change["kind"]]})
    items.sort(key=lambda i: (i["must"], i["score"]), reverse=True)
    return items[:top_n]


if __name__ == "__main__":
    import sys
    from .notebook import load_notebook

    timeline, team = load_notebook()
    days = sys.argv[1:] or sorted(timeline)
    for day in days:
        print(f"\n==================== {day}  ({state_as_of(timeline, day)['phases']}) ====================")
        for person in team["people"]:
            items = rank_for_person(person, day, timeline, team)
            if not items:
                continue
            print(f"  {person['name']} ({person['role']})")
            for i in items:
                tag = "MUST" if i["must"] else f"{i['score']:>4}"
                print(f"    [{tag}] #{i['change']['channel_name']}: {i['change']['summary'][:70]}")
                for r in i["reasons"]:
                    print(f"           - {r}")
