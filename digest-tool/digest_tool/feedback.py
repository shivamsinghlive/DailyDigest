"""👍/👎 on digest items, turned into a per-person multiplier for each *type* of item.

Simple on purpose, so it can be explained in one sentence:
    "You've rated updates 👎 3 times, so they count less for you (×0.7)."

multiplier = 1 + FEEDBACK_STEP × (👍 − 👎) for that person and item type, kept within
FEEDBACK_BOUNDS. The log in data/feedback.json is the only state: weights are recomputed
from it, so deleting a line undoes a vote, and reset_feedback() clears the demo.
"""
import json
from datetime import datetime

from . import config


def load_feedback():
    if config.FEEDBACK_FILE.exists():
        return json.loads(config.FEEDBACK_FILE.read_text())
    return []


def record_feedback(person_id, day, thread_ts, category, vote):
    """vote: +1 (👍) or -1 (👎). category: the item type (problem, decision, update, ...)."""
    log = load_feedback()
    log.append({"person": person_id, "day": day, "thread_ts": thread_ts, "category": category, "vote": vote,
                "at": datetime.now().isoformat(timespec="seconds")})
    config.FEEDBACK_FILE.write_text(json.dumps(log, indent=2))


def reset_feedback():
    if config.FEEDBACK_FILE.exists():
        config.FEEDBACK_FILE.unlink()


def type_preferences(person_id, feedback=None):
    """{category: {"up": n, "down": n, "multiplier": x}} for this person's rated item types."""
    feedback = load_feedback() if feedback is None else feedback
    prefs = {}
    for f in feedback:
        if f["person"] == person_id:
            p = prefs.setdefault(f["category"], {"up": 0, "down": 0})
            p["up" if f["vote"] > 0 else "down"] += 1
    lo, hi = config.FEEDBACK_BOUNDS
    for p in prefs.values():
        p["multiplier"] = round(min(hi, max(lo, 1 + config.FEEDBACK_STEP * (p["up"] - p["down"]))), 2)
    return prefs


def votes_by(person_id, feedback=None):
    """{(day, thread_ts): vote} so the UI can show what someone already voted."""
    feedback = load_feedback() if feedback is None else feedback
    return {(f["day"], f["thread_ts"]): f["vote"] for f in feedback if f["person"] == person_id}
