"""Seeds a test Slack workspace with a few realistic project conversations, for demoing live mode.

    python scripts/seed_slack.py --round 2          # show what round 2 would post (nothing is sent)
    python scripts/seed_slack.py --round 2 --post   # post it

The bot can only post as itself, so each message shows the person's name + "(via Daily Digest)" and
carries them in Slack message metadata; slack_loader counts it as that person's message.
People are picked by role from data/slack/team.json.
"""
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from digest_tool import config  # noqa: E402

TEAM = json.loads((Path(__file__).resolve().parent.parent / "data/slack/team.json").read_text())
ICON = {"mechanical_engineer": ":wrench:", "electrical_engineer": ":zap:", "supply_chain": ":package:",
        "engineering_manager": ":compass:", "product_manager": ":dart:"}

# (channel, role of the author, text, replies as [(role, text)]). {me} {ee} {sc} {em} @-mention that role.
ROUNDS = {}
ROUNDS["1"] = [
    ("mech", "mechanical_engineer",
     "J4 latch keeps popping on unit 2 during wrist rotation. This is a problem; printing a retainer clip to test tomorrow.", []),
    ("supply-chain", "supply_chain",
     "Heads up: Molex says 43045-0412 (the J4 connector) is going EOL, last-time buy 11/30. "
     "Do we qualify a replacement or stock up for the DVT build?",
     [("engineering_manager", "Let's decide by Friday. {me} can you look at drop-in alternatives?")]),
    ("elec", "electrical_engineer",
     "Bench test: the TMC9660 runs the wrist motor 18C cooler than the DRV8412 at the same torque.",
     [("engineering_manager", "Decision: we switch the motor driver to TMC9660 for DVT. Laddu, please check lead time."),
      ("supply_chain", "On it. Distis show about 50 pcs and factory lead time is 16 weeks, so the DVT build could slip.")]),
    ("elec", "electrical_engineer",
     "{me} how much clearance is there around the wrist motor bracket for the new driver board? "
     "I need it before I route the cable harness.", []),
    ("mech", "mechanical_engineer", "Gripper grip force test: 41N average with the new pads, target is 40N.", []),
]
# Round 2 exercises project memory: a freeze and a change after it, a value that changes and resolves a problem,
# a hedged contradiction, a schedule slip, one issue across two channels, a question to another person, chatter.
ROUNDS["2"] = [
    ("mech", "engineering_manager",
     "Design freeze for the gripper: it is officially in DVT as of today. Any change from here needs an ECO.", []),
    ("elec", "electrical_engineer", "Wrist motor case temp measured 84C at 45C ambient, spec max is 80C. Still too hot.", []),
    ("supply-chain", "supply_chain",
     "Slew bearing lot for DVT failed incoming hardness check. Re-heat-treat adds 3 weeks; "
     "the first 4 DVT units can use EVT spares.",
     [("engineering_manager", "Thanks. Moving the DVT build start from 10/27 to 11/10.")]),
    ("mech", "mechanical_engineer",
     "Pushed rev C of the gripper finger: moved the pad pocket 1mm to clear the screw heads.", []),
    ("mech", "electrical_engineer", "I thought the gripper grip force target was still 45N? {me}", []),
    ("supply-chain", "mechanical_engineer",
     "The cable harness is too stiff through the elbow and it pulls on the J4 latch when the wrist rotates.", []),
    ("elec", "engineering_manager", "{ee} is the power board BOM final for the DVT PO? Need it this week.", []),
    ("new-channel", "supply_chain", "anyone want coffee from the place downstairs? going in 10", []),
]
# Follow-ups to post on a later day, as replies to a thread found by how its first message starts:
# (channel, start of the thread's first message, role, reply).
FOLLOW_UPS = {"3": [
    ("elec", "Wrist motor case temp measured 84C", "electrical_engineer",
     "Update: with the TMC9660 and the new current limit it is 76C now, back in spec."),
    ("elec", "{ee} is the power board BOM final", "electrical_engineer", "Yes, BOM rev B is final. Sent to {sc} for the PO."),
]}


def person(role):
    return next(p for p in TEAM["people"] if p["role"] == role)


def api(method, **payload):
    resp = requests.post(f"https://slack.com/api/{method}", json=payload, timeout=30,
                         headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}",
                                  "Content-Type": "application/json; charset=utf-8"})
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method} failed: {data.get('error')}")
    return data


TAGS = {"{me}": "mechanical_engineer", "{ee}": "electrical_engineer", "{sc}": "supply_chain", "{em}": "engineering_manager"}


def post(channel_id, role, text, thread_ts=None):
    p = person(role)
    for tag, tagged_role in TAGS.items():
        text = text.replace(tag, f"<@{person(tagged_role)['id']}>")
    return api("chat.postMessage", channel=channel_id, text=text, thread_ts=thread_ts,
               username=f"{p['name']} (via Daily Digest)", icon_emoji=ICON.get(role, ":speech_balloon:"),
               metadata={"event_type": "digest_seed", "event_payload": {"as_user": p["id"]}})["ts"]


if __name__ == "__main__":
    send = "--post" in sys.argv
    rnd = sys.argv[sys.argv.index("--round") + 1] if "--round" in sys.argv else "1"
    SCRIPT = ROUNDS.get(rnd, [])
    channels = {c["name"]: c["id"] for c in requests.get(
        "https://slack.com/api/conversations.list", params={"types": "public_channel", "limit": 200}, timeout=30,
        headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"}).json()["channels"]}
    for channel, role, text, replies in SCRIPT:
        print(f"#{channel:<13} {person(role)['name']:<18} {text[:80]}")
        for r_role, r_text in replies:
            print(f"{'':<15}↳ {person(r_role)['name']:<16} {r_text[:78]}")
        if send:
            root = post(channels[channel], role, text)
            for r_role, r_text in replies:
                post(channels[channel], r_role, r_text, thread_ts=root)
    for channel, starts, role, text in FOLLOW_UPS.get(rnd, []):
        key = re.sub(r"\{\w+\}\s*", "", starts)  # match on the words, not on the @-mention placeholders
        root = next((m for m in requests.get(
            "https://slack.com/api/conversations.history", params={"channel": channels[channel], "limit": 200}, timeout=30,
            headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"}).json()["messages"]
            if key in m.get("text", "")), None)
        print(f"#{channel:<13} ↳ {person(role)['name']:<16} {text[:70]}  (reply to: {'found' if root else 'NOT FOUND'})")
        if send and root:
            post(channels[channel], role, text, thread_ts=root["ts"])
    print("\nposted" if send else "\n(dry run: nothing sent; add --post to send)")
