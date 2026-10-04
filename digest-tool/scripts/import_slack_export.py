"""Converts an independently written dataset (plain Slack export + team/parts/ground truth) into
the format the tool reads, so it can be evaluated as a holdout without touching any scoring.

    python scripts/import_slack_export.py data/holdout2            # writes data/holdout2/tool/
    python scripts/import_slack_export.py data/holdout2 --spent    # same, ground truth as tuned
    DIGEST_DATA_DIR=data/holdout2/tool python -m digest_tool.evaluate

A holdout is "spent" once its results have been looked at in detail: from then on it's tuning
data (ground_truth.json), and a new holdout is needed for an honest check.

Input (as written by the separate agent):
  messages.json      [{channel (name), user, ts, thread_ts (null on top-level posts), text}]
  team.json          [{id, name, role ("Mechanical Engineer"), parts_owned: [part ids]}]
  parts.json         [{id, official_name, nicknames, subsystem, owner}]
  ground_truth.json  {events: [{event_id, title, date, source_ts: [ts], alert_users, whole_team}]}

Like make_fake_data.py, this never prints holdout content, only counts and format warnings.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from digest_tool import config  # noqa: E402
from digest_tool.notebook import PHASES  # noqa: E402
from digest_tool.slack_loader import day_of  # noqa: E402

# The ranker has role profiles for these five roles. Roles it doesn't know get the closest profile.
ROLES = {
    "mechanical engineer": "mechanical_engineer",
    "electrical engineer": "electrical_engineer",
    "firmware engineer": "electrical_engineer",   # no firmware profile; EE is the closest
    "supply chain lead": "supply_chain",
    "engineering manager": "engineering_manager",
    "product manager": "product_manager",
}
START_PHASE = "EVT"  # not given by the dataset; the tool's default. Phase changes are still read from the text.


def load(src, name):
    with open(src / name) as f:
        return json.load(f)


def convert(src, out, spent=False):
    raw_msgs, raw_team, raw_parts, raw_gt = (load(src, f) for f in
                                             ("messages.json", "team.json", "parts.json", "ground_truth.json"))
    warnings = []

    # messages: channel names become ids too; a null thread_ts means "not in a thread", so drop it
    # (the loader groups by m.get("thread_ts", m["ts"]), and None would lump all top-level posts together)
    messages = []
    for m in raw_msgs:
        msg = {"type": "message", "channel": m["channel"], "user": m["user"], "ts": m["ts"], "text": m["text"]}
        if m.get("thread_ts"):
            msg["thread_ts"] = m["thread_ts"]
        messages.append(msg)
    channels = [{"id": c, "name": c} for c in sorted({m["channel"] for m in messages})]

    # parts: official_name/nicknames -> name/aliases. No topics in this dataset.
    part_name = {p["id"]: p["official_name"] for p in raw_parts}
    parts = [{"name": p["official_name"], "subsystem": p["subsystem"], "aliases": p.get("nicknames", [])}
             for p in raw_parts]
    if len(set(part_name.values())) != len(part_name):
        warnings.append("duplicate official part names")

    # team: role labels -> ranker role keys, owned part ids -> official names
    people = []
    for p in raw_team:
        role = ROLES.get(p["role"].strip().lower())
        if role is None:
            sys.exit(f"Unknown role {p['role']!r}: add it to ROLES")
        if p["role"].strip().lower() == "firmware engineer":
            warnings.append(f"{p['id']}: role 'Firmware Engineer' mapped to {role} (no firmware profile)")
        people.append({"id": p["id"], "name": p["name"], "role": role,
                       "owns": [part_name[i] for i in p.get("parts_owned", [])]})
    team = {"project": {"name": raw_gt.get("dataset", src.name), "phases": PHASES, "start_phase": START_PHASE},
            "people": people}

    # true owners (evaluation only): the parts file's owner plus everyone who lists the part
    owners = {}
    for p in raw_parts:
        if p.get("owner"):
            owners.setdefault(p["official_name"], set()).add(p["owner"])
    for p in people:
        for part in p["owns"]:
            owners.setdefault(part, set()).add(p["id"])
    true_owners = {"description": "TEST ONLY. Derived from the dataset's parts.json owner + team.json parts_owned.",
                   "owners": {k: sorted(v) for k, v in owners.items()}}

    # ground truth: an event is scored on one day. source_ts can list several messages in several
    # threads; reaching someone through any of them counts (evaluate.event_threads).
    thread_of = {m["ts"]: m.get("thread_ts", m["ts"]) for m in messages}
    events, multi_thread, missing, off_day = [], 0, 0, 0
    for e in raw_gt["events"]:
        found = [ts for ts in e["source_ts"] if ts in thread_of]
        if len(found) < len(e["source_ts"]):
            missing += 1
        if not found:
            warnings.append(f"{e['event_id']}: none of its source_ts are in messages.json, skipped")
            continue
        threads = list(dict.fromkeys(thread_of[ts] for ts in found))
        multi_thread += len(threads) > 1
        thread_ts = threads[0]
        if not any(day_of(m["ts"]) == e["date"] for m in messages if thread_of[m["ts"]] == thread_ts):
            off_day += 1
        events.append({"id": e["event_id"], "section": "team_pulse" if e["whole_team"] else "personal",
                       "thread_ts": thread_ts, "also_threads": threads[1:], "day": e["date"], "what": e["title"],
                       "should_alert": [{"user": u} for u in e["alert_users"]]})
    gt = {"notes": f"Converted from {src.name}/ground_truth.json by scripts/import_slack_export.py",
          "events": events, "noise": [], "mentions": []}

    out.mkdir(parents=True, exist_ok=True)
    files = {"messages.json": {"channels": channels, "messages": messages},
             "team.json": team,
             "parts.json": {"subsystems": sorted({p["subsystem"] for p in parts}), "parts": parts, "topics": []},
             "ground_truth.json" if spent else "ground_truth_holdout.json": gt,
             "true_owners.json": true_owners}
    for name, data in files.items():
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    print(f"wrote {out}: {len(messages)} messages, {len(channels)} channels, {len(people)} people, "
          f"{len(parts)} parts, {len(events)} events")
    if missing:
        warnings.append(f"{missing} event(s) cite source_ts not found in messages.json")
    if multi_thread:
        warnings.append(f"{multi_thread} event(s) span several threads; any of them counts")
    if off_day:
        warnings.append(f"{off_day} event(s) whose thread has no message on the event date "
                        f"(days are counted in {config.TIMEZONE})")
    for w in warnings:
        print("  warning:", w)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--spent"]
    if len(args) != 1:
        sys.exit(__doc__)
    src = Path(args[0])
    convert(src, src / "tool", spent="--spent" in sys.argv)
