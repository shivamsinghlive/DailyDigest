"""Regenerates data/messages.json, data/ground_truth.json and data/ground_truth_holdout.json.

    python scripts/make_fake_data.py

Writing messages by hand with Slack epoch timestamps is error-prone, so they're written here as
(day, "HH:MM") and converted, keeping ts / thread_ts consistent.

Sources:
  M below                        tuned stories (1-5) + extra tuned messages
  sources/former_holdout.json    the V2 holdout, spent once its results were seen -> now TUNED (F-stories)
  sources/holdout_v2.json        current holdout, written by a separate agent. Don't read it while
                                 tuning; this script never prints its content, only counts.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent / "sources"
OUT = Path(__file__).resolve().parent.parent / "data"
TZ = ZoneInfo("America/Los_Angeles")
DAY1 = date(2026, 9, 14)  # Monday. Day 8 = Mon 9/21 (freeze), day 9 = Tue 9/22 (pinout q)

CH = {"general": "C01GENERAL", "mech": "C02MECH", "elec": "C03ELEC",
      "supply-chain": "C04SUPPLY", "firmware": "C05FIRMWARE"}

RAVI, PRIYA, MEI, TOM, SARA = "U_RAVI", "U_PRIYA", "U_MEI", "U_TOM", "U_SARA"

# (key, reply_to, day, "HH:MM", channel, user, text)
M = [
    # ---- Day 1, Mon 9/14 ----
    ("kickoff", None, 1, "09:00", "general", TOM,
     "morning all 👋 EVT2 is our last EVT build. thermal + drop testing this wk and next, EVT exit review is mon 9/21. pls get open issues into jira before then"),
    (None, "kickoff", 1, "09:10", "general", SARA, "👍 updated PRD coming thurs"),
    ("grip_v4", None, 1, "10:15", "mech", RAVI, "gripper finger v4 printed, starting grip force tests on the foam boxes today"),
    ("fw_pr212", None, 1, "11:30", "firmware", PRIYA, "merged PR #212, encoder filter fix for the elbow axis. jitter's gone"),
    ("lunch", None, 1, "12:05", "general", MEI, "anyone want pho for lunch?"),
    (None, "lunch", 1, "12:06", "general", RAVI, "in"),
    (None, "lunch", 1, "12:08", "general", SARA, "omw"),
    ("alu_po", None, 1, "14:00", "supply-chain", MEI, "PO placed for EVT3 6061 bar stock, ETA 9/24"),

    # ---- Day 2, Tue 9/15 ----
    ("thermal", None, 2, "09:20", "mech", RAVI,
     "thermal chamber results from last night: wrist motor hit 92C case temp after 40 min @ 45C ambient 🔥 spec is 80C max. this is a problem"),
    (None, "thermal", 2, "09:45", "mech", PRIYA,
     "case or winding? if the driver is pushing too much current at hold that'd do it. I can scope it today"),
    (None, "thermal", 2, "09:50", "mech", RAVI, "case, TC taped to the can. pls do, I'll check heatsink contact on my side"),
    ("base_cast", None, 2, "11:00", "mech", RAVI, "base casting rev B back from the foundry, flatness 0.05 across the mounting face ✅"),
    ("grip_force", None, 2, "15:00", "mech", RAVI, "grip force test: 38N avg, target is 40N. gonna try softer pads"),
    ("calipers", None, 2, "16:30", "mech", TOM, "has anyone seen the good mitutoyo calipers? not in the drawer again 🙃"),
    (None, "calipers", 2, "16:41", "mech", RAVI, "check the thermal chamber bench lol"),

    # ---- Day 3, Wed 9/16 ----
    ("j4_latch", None, 3, "09:40", "elec", PRIYA,
     "<@U_RAVI> J-4 latch keeps popping on unit 2 when the wrist rotates past 160°, can you take a look?"),
    (None, "j4_latch", 3, "10:05", "elec", RAVI, "yeah the latch on that wrist conn is flimsy, I'll print a retainer clip today"),
    ("scope", None, 3, "10:30", "elec", PRIYA,
     "scoped the wrist driver (DRV8412): ~2.8A RMS just holding position. way more than the load needs. fw current limit isn't doing anything"),
    (None, "scope", 3, "10:50", "elec", RAVI, "so it's not (just) a mech problem 😅 fwiw heatsink contact looked fine"),
    ("cl_config", None, 3, "11:00", "firmware", PRIYA, "q: who set the wrist axis current limit to 4A in config? intentional?"),
    (None, "cl_config", 3, "11:20", "firmware", TOM, "that was me, quick hack for the july investor demo. should've reverted it, sorry"),
    (None, "cl_config", 3, "11:22", "firmware", PRIYA, "np, dropping it to 2A and retesting tmrw"),
    ("pads", None, 3, "14:00", "mech", RAVI, "ordered 50A shore pads for the gripper from mcmaster, here tmrw"),
    ("pack_soak", None, 3, "15:00", "elec", PRIYA, "48V pack cell balancing looks fine after the overnight soak, BMS logs are in the drive"),

    # ---- Day 4, Thu 9/17 ----
    ("j4_eol", None, 4, "09:30", "supply-chain", MEI,
     "heads up: Molex sent an EOL notice for 43045-0412 (Micro-Fit 4 pin). last time buy deadline is 11/30. "
     "we either LTB enough for DVT+PVT or eng picks a replacement. need a call in the next couple wks"),
    (None, "j4_eol", 4, "09:50", "supply-chain", TOM, "who owns that connector again?"),
    (None, "j4_eol", 4, "09:55", "supply-chain", MEI, "pretty sure it's a mech part, I'll chase it down"),
    ("slew", None, 4, "10:30", "mech", RAVI, "re-shimmed the slew ring on unit 3, preload is back in spec"),
    ("prd", None, 4, "11:00", "general", SARA, "updated PRD is up in confluence. main change: payload stays 3kg, reach +20mm"),
    (None, "thermal", 4, "15:30", "mech", PRIYA,
     "retest w/ 2A current limit: 84C. better but still over spec, and torque margin drops to 1.1x which is too tight"),
    (None, "thermal", 4, "15:40", "mech", RAVI, "ugh. so CL alone won't get us there"),
    ("visit", None, 4, "16:00", "general", SARA, "customer visit next thurs 9/24, pls keep the lab tidy 🙏"),

    # ---- Day 5, Fri 9/18 ----
    ("tmc", None, 5, "10:00", "elec", PRIYA,
     "proposal for the wrist overheating: swap DRV8412 -> TMC9660. integrated FOC, much better efficiency at low speed. bench test this afternoon"),
    ("grip_done", None, 5, "11:00", "mech", RAVI,
     "gripper v4 w/ 50A pads: 41N avg ✅ closing out the grip force issue. since the wrist driver is changing anyway "
     "I'm gonna help Priya with the cable harness routing next wk"),
    (None, "tmc", 5, "15:00", "elec", PRIYA, "bench results: TMC9660 runs the wrist motor 18C cooler at the same torque. I think this is the move"),
    (None, "tmc", 5, "15:10", "elec", RAVI, "+1 from mech side, fixes it without a new motor"),
    (None, "tmc", 5, "15:30", "elec", TOM,
     "ok decision: we switch the wrist motor driver to TMC9660 for DVT. Priya pls update the schematic. <@U_MEI> can you check availability + lead time?"),
    ("drinks", None, 5, "16:30", "general", TOM, "drinks on the patio at 5 🍻"),

    # ---- Day 6, Sat 9/19 ----
    ("harness_cad", None, 6, "13:00", "elec", RAVI,
     "started routing the cable harness in CAD. current bundle through the elbow is way too stiff, fights the joint at full rotation. "
     "wrist conn sits right at the bend too"),

    # ---- Day 7, Sun 9/20 ----
    ("dog", None, 7, "18:00", "general", PRIYA, "my dog just ate a JST connector. he's fine. the connector is not."),
    (None, "dog", 7, "18:05", "general", SARA, "lol 💀"),

    # ---- Day 8, Mon 9/21 ----
    ("tmc_lt", None, 8, "10:30", "supply-chain", MEI,
     "re TMC9660 for the wrist: lead time from ADI is 16 wks 😬 distis have ~50 pcs total and we need 120 for the DVT build. "
     "if we wait for factory stock the DVT build slips from 10/26 to late jan"),
    (None, "tmc_lt", 8, "10:45", "supply-chain", TOM, "that's bad. can you check brokers? let's discuss at thurs review"),
    (None, "tmc_lt", 8, "10:50", "supply-chain", MEI, "on it"),
    ("harness_split", None, 8, "14:00", "mech", RAVI,
     "harness idea: split into 2 smaller bundles through the elbow instead of 1 fat one, + 30mm service loop. Priya does that work electrically?"),
    (None, "harness_split", 8, "14:30", "mech", PRIYA, "yeah should be fine, just keep the motor phases together"),
    ("freeze", None, 8, "16:00", "general", TOM,
     "<!channel> EVT exit review done ✅ design freeze for the gripper, the base and the harness: all three are officially in DVT as of today. "
     "the wrist stays in EVT till we've sorted the motor overheating. "
     "from now on any design change on a DVT subsystem needs an ECO approved by me. DVT build target is still 10/26"),
    (None, "freeze", 8, "16:10", "general", MEI, "🎉 kicking off DVT POs tmrw"),

    # ---- Day 9, Tue 9/22 ----
    ("standup", None, 9, "09:15", "general", SARA, "reminder: no standup tmrw, all-hands instead"),
    ("pinout", None, 9, "10:00", "elec", RAVI,
     "<@U_PRIYA> what's the pinout on J7 on the new TMC9660 driver board? need it to finish the harness drawing"),
    ("fw_branch", None, 9, "11:00", "firmware", PRIYA, "started the TMC9660 FOC port on branch fw/tmc9660"),
    ("base_po", None, 9, "14:00", "supply-chain", MEI, "DVT PO for 120 base castings placed w/ the foundry, 6 wk lead time"),

    # ---- Day 10, Wed 9/23 ----
    ("allhands", None, 10, "10:00", "general", TOM, "all-hands slides are in the drive"),
    ("clips", None, 10, "13:00", "mech", RAVI, "printed P-clips for the harness at the elbow, fit is good"),
    ("fw_loop", None, 10, "16:00", "firmware", PRIYA, "TMC9660 FOC loop closing on the bench board 🎉 still need to tune gains"),

    # ---- Day 11, Thu 9/24 ----
    ("visit_today", None, 11, "08:30", "general", SARA, "customer is here at 10, they'll walk through the lab ~11"),
    (None, "pinout", 11, "09:30", "elec", RAVI, "bump, I'm blocked on the harness drawing until I have the J7 pinout"),
    (None, "j4_eol", 11, "10:00", "supply-chain", MEI,
     "still need eng input on a replacement for the molex conector. LTB qty calc is due 10/2, after that I'll just buy 2 yrs worth (~$9k)"),
    ("review", None, 11, "15:00", "general", TOM,
     "thurs review notes: DVT build date is now TBD until we secure TMC9660s. Mei is chasing broker stock. Sara I know you need a date for the customer"),
    (None, "review", 11, "15:10", "general", SARA, "yes, need a firm date by EOM or the pilot slips"),

    # ---- Day 12, Fri 9/25 ----
    (None, "pinout", 12, "11:00", "elec", PRIYA, "sorry, was buried in the schematic update 🙈 J7: 1 VM, 2 GND, 3-5 phase A/B/C, 6 NTC"),
    (None, "pinout", 12, "11:05", "elec", RAVI, "perfect, thanks!"),
    ("pizza", None, 12, "12:00", "general", MEI, "pizza in the kitchen 🍕"),
    ("rev_c", None, 12, "16:00", "elec", PRIYA,
     "harness rev C released: split into 2 bundles at the elbow, +30mm service loop, P-clips added. drawings are in PDM"),

    # ---- Day 13, Sat 9/26 ----
    ("ci_down", None, 13, "14:00", "firmware", PRIYA, "fyi CI is down for maintenance this weekend"),

    # ---- Day 14, Sun 9/27 ----
    ("ac", None, 14, "19:00", "general", TOM, "heads up lab AC is getting serviced mon morning, it'll be warm"),
]

# ---- former holdout (spent) -> tuned. One edit: it was written for 3 subsystems, now there are 5. ----
FORMER = json.loads((HERE / "former_holdout.json").read_text())
for m in FORMER["messages"]:
    m["text"] = m["text"].replace("wrist moves to DVT as of today, so all 3 subsystems are DVT now.",
                                  "wrist moves to DVT as of today. power stays in EVT until the new pack is qualified.")
M += [(m["key"], m["reply_to"], m["day"], m["time"], m["channel"], m["user"], m["text"]) for m in FORMER["messages"]]

# ---- new holdout, merged blind ----
HOLDOUT_FILE = HERE / "holdout_v2.json"
HOLDOUT = json.loads(HOLDOUT_FILE.read_text()) if HOLDOUT_FILE.exists() else {"messages": [], "events": [], "noise": [], "mentions": [], "stories": {}}
M += [(m["key"], m["reply_to"], m["day"], m["time"], m["channel"], m["user"], m["text"]) for m in HOLDOUT["messages"]]

roots, messages = {}, []
for i, (key, reply_to, day, hhmm, ch, user, text) in enumerate(M):
    h, m = map(int, hhmm.split(":"))
    d = DAY1 + timedelta(days=day - 1)
    dt = datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)
    ts = f"{int(dt.timestamp())}.{i:06d}"  # unique suffix, like real Slack ts
    msg = {"type": "message", "channel": CH[ch], "user": user, "ts": ts, "text": text}
    if reply_to:
        msg["thread_ts"] = roots[reply_to]
    else:
        assert key not in roots, f"duplicate key {key}"
        roots[key] = ts
    messages.append(msg)

# Real Slack also puts thread_ts on a root once it has replies
replied = {m["thread_ts"] for m in messages if "thread_ts" in m}
for msg in messages:
    if msg["ts"] in replied:
        msg["thread_ts"] = msg["ts"]
    if "thread_ts" in msg:
        assert float(msg["ts"]) >= float(msg["thread_ts"]), "reply before its root"

messages.sort(key=lambda m: float(m["ts"]))
channels = [{"id": cid, "name": name} for name, cid in CH.items()]
(OUT / "messages.json").write_text(json.dumps({"channels": channels, "messages": messages}, indent=2, ensure_ascii=False))


def day_str(n):
    return str(DAY1 + timedelta(days=n - 1))


def event(eid, story, key, day, what, section, alerts):
    return {"id": eid, "story": story, "section": section, "thread_ts": roots[key], "day": day_str(day),
            "day_number": day, "what": what, "should_alert": [{"user": u, "why": w} for u, w in alerts]}


def mention(contains, written, official, in_catalog=True):
    """A labelled nickname: in the message containing `contains`, `written` refers to `official`."""
    hits = [m for m in messages if contains in m["text"]]
    assert len(hits) == 1, f"label {contains!r} matched {len(hits)} messages"
    return {"ts": hits[0]["ts"], "written": written, "official": official, "in_catalog": in_catalog}


SECTION_NOTE = ("section: team_pulse = the whole team should see it (phase changes, schedule risks, major decisions); "
                "personal = only the listed people need it.")

# Former-holdout events keep their ground truth; only ids change (H -> F) and a section is added.
FORMER_SECTION = {"H3b": "team_pulse"}
former_events = [event("F" + e["id"][1:], "F" + e["story"][1:], e["key"], e["day"], e["what"],
                       FORMER_SECTION.get(e["id"], "personal"),
                       [(a["user"], a["why"]) for a in e["should_alert"]]) for e in FORMER["events"]]

tuned = {
    "notes": [
        "Day 1 = 2026-09-14. An event is a hit if the person's digest for that day contains the thread.",
        "People who already posted in the thread are NOT listed: they saw it. Alerting them is not counted either way.",
        "Noise threads should alert nobody. Threads that are neither events nor noise are not scored.",
        SECTION_NOTE,
        "F-stories were the V2 holdout; once their results were seen they became tuned data.",
        "mentions: labelled nicknames for measuring part matching. in_catalog=false means the right behaviour is "
        "to flag it as an unknown part (or map it correctly if the LLM can).",
    ],
    "stories": {
        "1": "Wrist motor overheats -> ME+EE debug -> switch motor driver -> 16 wk lead time -> DVT build date at risk",
        "2": "J4 connector EOL posted only in #supply-chain; Ravi (owner) never comments there",
        "3": "Design freeze on day 8: gripper, base, cabling -> DVT; wrist stays EVT",
        "4": "Ravi asks Priya a J7 pinout question on day 9; unanswered until day 12",
        "5": "Ravi's activity shifts from gripper to cable harness around day 5-6",
        **{"F" + k[1:]: v for k, v in FORMER["stories"].items()},
    },
    "events": [
        event("E1", 1, "thermal", 2, "Wrist motor hits 92C in thermal test (spec 80C)", "personal",
              [(TOM, "EM should know about a test failure on a core subsystem")]),
        event("E2", 1, "tmc", 5, "Decision: switch wrist motor driver to TMC9660", "team_pulse",
              [(MEI, "Directly asked to check availability and lead time")]),
        event("E3", 1, "tmc_lt", 8, "TMC9660 has 16 wk lead time; DVT build slips to late Jan", "team_pulse",
              [(PRIYA, "Owns the motor driver"), (RAVI, "Owns the wrist assembly / wrist motor"),
               (SARA, "DVT build date (and customer pilot) at risk")]),
        event("E4", 2, "j4_eol", 4, "Molex EOL notice on 43045-0412 = J4 connector", "personal",
              [(RAVI, "Owns the J4 connector; never reads #supply-chain")]),
        event("E5", 2, "j4_eol", 11, "J4 EOL follow-up: still no eng decision, $9k LTB due 10/2", "personal",
              [(RAVI, "Owns the J4 connector; decision deadline is close")]),
        event("E6", 3, "freeze", 8, "Design freeze: gripper, base, cabling -> DVT; wrist stays EVT", "team_pulse",
              [(RAVI, "Phase change affects everyone"), (PRIYA, "Phase change affects everyone"),
               (SARA, "Phase change affects everyone")]),
        event("E7", 4, "pinout", 9, "Ravi asks Priya for the J7 pinout", "personal",
              [(PRIYA, "Directly asked; owns J7")]),
        event("E8", 4, "pinout", 11, "Pinout question unanswered for 48h; Ravi is blocked", "personal",
              [(PRIYA, "Still waiting on her answer"), (TOM, "A teammate is blocked")]),
        event("E9", 5, "rev_c", 12, "Harness rev C released", "personal",
              [(RAVI, "Has been working on the harness all week (doesn't own it)")]),
    ] + former_events,
    "noise": [{"thread_ts": roots[k], "what": k} for k in
              ["lunch", "calipers", "drinks", "dog", "standup", "allhands", "visit_today", "pizza", "ci_down", "ac"]
              + FORMER["noise"]],
    "mentions": [
        mention("J-4 latch keeps popping", "J-4", "J4 connector"),
        mention("latch on that wrist conn", "wrist conn", "J4 connector"),
        mention("EOL notice for 43045-0412", "43045-0412", "J4 connector"),
        mention("replacement for the molex conector", "molex conector", "J4 connector"),
        mention("wrist conn sits right at the bend", "wrist conn", "J4 connector"),
        mention("scoped the wrist driver", "wrist driver", "motor driver"),
        mention("swap DRV8412 -> TMC9660", "DRV8412", "motor driver"),
        mention("re-shimmed the slew ring", "slew ring", "slew bearing"),
        mention("48V pack cell balancing", "48V pack", "battery pack"),
        mention("base casting rev B", "base casting", "base casting"),
        mention("tweaking the wrist mtr mount", "wrist mtr mount", "wrist motor bracket"),
        mention("what's the pinout on J7", "J7", "J7 connector"),
        mention("harness idea: split", "bundles", "cable harness"),
        mention("jacket compound for the arm umbilical", "arm umbilical", "cable harness", in_catalog=False),
        mention("wrist abs encoder (the mag puck)", "mag puck", None, in_catalog=False),
        mention("flagged AS5048A", "AS5048A", None, in_catalog=False),
    ],
}
(OUT / "ground_truth.json").write_text(json.dumps(tuned, indent=2, ensure_ascii=False))

holdout = {
    "notes": ["HOLDOUT: written by a separate agent that never saw the code. Never tune on these. "
              "Same format and rules as ground_truth.json.", SECTION_NOTE],
    "stories": HOLDOUT["stories"],
    "events": [event(e["id"], e["story"], e["key"], e["day"], e["what"], e["section"],
                     [(a["user"], a["why"]) for a in e["should_alert"]]) for e in HOLDOUT["events"]],
    "noise": [{"thread_ts": roots[k], "what": k} for k in HOLDOUT["noise"]],
    "mentions": [mention(x["contains"], x["written"], x["official"], x["in_catalog"]) for x in HOLDOUT.get("mentions", [])],
}
(OUT / "ground_truth_holdout.json").write_text(json.dumps(holdout, indent=2, ensure_ascii=False))

print(f"{len(messages)} messages, {len(roots)} threads | tuned: {len(tuned['events'])} events, "
      f"{len(tuned['noise'])} noise, {len(tuned['mentions'])} mention labels | "
      f"holdout: {len(holdout['events'])} events, {len(holdout['noise'])} noise, {len(holdout['mentions'])} mention labels")
