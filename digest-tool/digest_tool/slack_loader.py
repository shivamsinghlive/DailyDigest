"""Load Slack messages (fake JSON or the live Slack Web API) and group them into threads.

Everything downstream works on threads, not single messages: a reply like
"+1 from mech side" means nothing without the message it answers.
"""
import json
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from . import config

TZ = ZoneInfo(config.TIMEZONE)


def load_team():
    """team.json plus the parts catalog, so every module gets both from one object."""
    from .catalog import load_catalog
    with open(config.DATA_DIR / "team.json") as f:
        team = json.load(f)
    team["catalog"] = load_catalog()
    return team


def load_messages(source=config.MESSAGE_SOURCE):
    """Return all messages sorted by time, each with a readable 'channel_name' added."""
    if source == "fake":
        with open(config.DATA_DIR / "messages.json") as f:
            data = json.load(f)
        channel_names = {c["id"]: c["name"] for c in data["channels"]}
        messages = data["messages"]
    elif source == "slack":
        messages, channel_names = fetch_from_slack()
    else:
        raise ValueError(f"Unknown source {source!r}, expected 'fake' or 'slack'")

    for m in messages:
        m["channel_name"] = channel_names.get(m["channel"], m["channel"])
    return sorted(messages, key=lambda m: float(m["ts"]))


# ---------- live Slack (written to work in principle; the demo uses fake data) ----------

def slack_api(method, **params):
    """Call one Slack Web API method and follow cursor pagination. Returns the list of pages."""
    pages = []
    while True:
        resp = requests.get(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
            params=params, timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):  # Slack returns HTTP 200 even on errors
            raise RuntimeError(f"Slack {method} failed: {data.get('error')}")
        pages.append(data)
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return pages
        params["cursor"] = cursor


def fetch_from_slack(days_back=14):
    """Pull recent history from every public channel the bot is a member of.
    Bot token scopes needed: channels:read, channels:history."""
    if not config.SLACK_BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN is not set (see .env.example)")
    oldest = (datetime.now(TZ) - timedelta(days=days_back)).timestamp()

    channels = [c for page in slack_api("conversations.list", types="public_channel", limit=200)
                for c in page["channels"]]
    messages = []
    for ch in channels:
        if not ch.get("is_member"):  # bots can only read channels they've been added to
            continue
        for page in slack_api("conversations.history", channel=ch["id"], oldest=oldest, limit=200):
            for msg in page["messages"]:
                messages.append({**msg, "channel": ch["id"]})
                # conversations.history only returns thread roots; replies need their own call.
                if msg.get("reply_count"):
                    for rpage in slack_api("conversations.replies", channel=ch["id"], ts=msg["ts"], limit=200):
                        messages += [{**r, "channel": ch["id"]} for r in rpage["messages"] if r["ts"] != msg["ts"]]
    return messages, {c["id"]: c["name"] for c in channels}


# ---------- time helpers ----------

def ts_to_dt(ts):
    return datetime.fromtimestamp(float(ts), TZ)


def day_of(ts):
    return ts_to_dt(ts).strftime("%Y-%m-%d")


def end_of_day(day):
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return datetime.combine(d, time.max, TZ)


def all_days(messages):
    """Every calendar day from first to last message, including quiet days
    (a question can cross the 48h mark on a day nobody posts)."""
    first = datetime.strptime(day_of(messages[0]["ts"]), "%Y-%m-%d")
    last = datetime.strptime(day_of(messages[-1]["ts"]), "%Y-%m-%d")
    return [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]


# ---------- text helpers ----------

def clean_text(text, names):
    """Slack markup -> plain text: <@U_RAVI> -> @Ravi Menon, <!channel> -> @channel, <url|label> -> label."""
    text = re.sub(r"<@(\w+)>", lambda m: "@" + names.get(m.group(1), m.group(1)), text)
    text = re.sub(r"<!(\w+)>", r"@\1", text)
    text = re.sub(r"<[^|>]+\|([^>]+)>", r"\1", text)
    return text


# ---------- threads ----------

def group_into_threads(messages, until=None):
    """Group messages by thread_ts; a message outside any thread is its own one-message thread.

    `until` drops later messages so we can see a thread exactly as it looked at that moment.
    That matters: Tuesday's unanswered question may be answered on Friday.
    """
    threads = {}
    for m in messages:
        if until and ts_to_dt(m["ts"]) > until:
            continue
        key = m.get("thread_ts", m["ts"])
        t = threads.setdefault(key, {
            "thread_ts": key,
            "channel": m["channel"],
            "channel_name": m["channel_name"],
            "messages": [],
        })
        t["messages"].append(m)

    for t in threads.values():
        t["participants"] = sorted({m["user"] for m in t["messages"] if m.get("user")})
        t["last_ts"] = t["messages"][-1]["ts"]
    return sorted(threads.values(), key=lambda t: float(t["last_ts"]))


def threads_active_on(messages, day):
    """Threads with at least one message on `day`, each cut off at the end of that day."""
    return [t for t in group_into_threads(messages, until=end_of_day(day))
            if any(day_of(m["ts"]) == day for m in t["messages"])]


if __name__ == "__main__":
    team = load_team()
    names = {p["id"]: p["name"] for p in team["people"]}
    msgs = load_messages()
    threads = group_into_threads(msgs)
    print(f"{len(msgs)} messages, {len(threads)} threads, {len(all_days(msgs))} days\n")
    for day in all_days(msgs):
        print(day)
        for t in threads_active_on(msgs, day):
            root = t["messages"][0]
            print(f"   #{t['channel_name']:<13} {len(t['messages'])} msg  "
                  f"{names.get(root['user'], root['user'])}: {clean_text(root['text'], names)[:70]}")
