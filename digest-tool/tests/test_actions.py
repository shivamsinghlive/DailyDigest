"""Replies from the app: what gets posted, and where links point. No Slack calls."""
from digest_tool import actions
from digest_tool.slack_loader import attribute, is_conversation

RAVI = {"id": "U_RAVI", "name": "Ravi Menon", "role": "mechanical_engineer"}


def test_a_reply_is_posted_in_the_thread_and_counts_as_the_person():
    payload = actions.reply_payload(RAVI, "C_ELEC", "1791182753.021319", "  12mm all round, drawing in PDM  ")
    assert payload["thread_ts"] == "1791182753.021319" and payload["text"] == "12mm all round, drawing in PDM"
    assert payload["username"] == "Ravi Menon (via Daily Digest)"
    # Read back from Slack, the bot message becomes Ravi's message in the thread.
    as_read = {"subtype": "bot_message", "bot_id": "B1", "text": payload["text"], "metadata": payload["metadata"]}
    assert is_conversation(as_read) and attribute(as_read)["user"] == "U_RAVI"


def test_thread_link_points_at_the_thread(monkeypatch):
    monkeypatch.setattr(actions, "workspace_url", lambda: "https://team.slack.com/")
    assert actions.thread_link("C_ELEC", "1791182753.021319") == "https://team.slack.com/archives/C_ELEC/p1791182753021319"


def test_the_demo_is_never_live(monkeypatch):
    monkeypatch.setattr(actions.config, "MESSAGE_SOURCE", "fake")
    assert not actions.live_mode()
