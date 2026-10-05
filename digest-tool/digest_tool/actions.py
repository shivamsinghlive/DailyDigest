"""Actions taken from the app. For now: reply in a Slack thread, and link to a thread in Slack.

A reply is posted by the app's bot (one bot token, no per-user sign-in), so in Slack it shows as
"<name> (via Daily Digest)". It carries the person in Slack message metadata, and slack_loader counts it
as that person's message: answering a question in the app answers it in the project's memory too.
"""
from functools import lru_cache

import requests

from . import config

ROLE_ICON = {"mechanical_engineer": ":wrench:", "electrical_engineer": ":zap:", "supply_chain": ":package:",
             "engineering_manager": ":compass:", "product_manager": ":dart:"}


def live_mode():
    """Replies only make sense against a real workspace: never in the synthetic demo."""
    return config.MESSAGE_SOURCE == "slack" and bool(config.SLACK_BOT_TOKEN)


def _call(method, payload=None, params=None):
    headers = {"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"}
    if payload is not None:
        resp = requests.post(f"https://slack.com/api/{method}", json=payload, timeout=30,
                             headers={**headers, "Content-Type": "application/json; charset=utf-8"})
    else:
        resp = requests.get(f"https://slack.com/api/{method}", params=params, timeout=30, headers=headers)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {data.get('error')}")
    return data


def reply_payload(person, channel_id, thread_ts, text):
    """What gets posted. Separate from sending, so it can be checked without Slack."""
    return {"channel": channel_id, "thread_ts": thread_ts, "text": text.strip(),
            "username": f"{person['name']} (via Daily Digest)",
            "icon_emoji": ROLE_ICON.get(person["role"], ":speech_balloon:"),
            "metadata": {"event_type": "digest_reply", "event_payload": {"as_user": person["id"]}}}


def reply_in_thread(person, channel_id, thread_ts, text):
    """Post `text` as a reply in the thread, for `person`. Returns the link to the new message."""
    if not text.strip():
        raise ValueError("The reply is empty")
    posted = _call("chat.postMessage", reply_payload(person, channel_id, thread_ts, text))
    return _call("chat.getPermalink", params={"channel": channel_id, "message_ts": posted["ts"]})["permalink"]


@lru_cache(maxsize=1)
def workspace_url():
    return _call("auth.test", params={})["url"]          # e.g. https://myteam.slack.com/


def thread_link(channel_id, thread_ts):
    """A link that opens the thread in Slack (web or desktop app)."""
    return f"{workspace_url()}archives/{channel_id}/p{thread_ts.replace('.', '')}"
