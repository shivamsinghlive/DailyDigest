"""Live Slack: system messages are dropped, content is kept."""
from digest_tool.slack_loader import attribute, is_conversation


def test_join_and_topic_notices_are_not_conversation():
    assert not is_conversation({"subtype": "channel_join", "text": "<@U1> has joined the channel"})
    assert not is_conversation({"subtype": "channel_topic", "text": "set the topic"})


def test_messages_replies_and_file_shares_are():
    assert is_conversation({"text": "J4 latch keeps popping"})
    assert is_conversation({"subtype": "thread_broadcast", "text": "also posted to the channel"})
    assert is_conversation({"subtype": "file_share", "text": "", "files": [{"name": "bracket_revF.pdf"}]})


def test_empty_messages_are_not():
    assert not is_conversation({"text": ""})


def test_a_message_the_app_posted_for_someone_is_theirs():
    seeded = {"subtype": "bot_message", "bot_id": "B1", "text": "J4 latch keeps popping",
              "metadata": {"event_type": "digest_seed", "event_payload": {"as_user": "U_RAVI"}}}
    assert is_conversation(seeded) and attribute(seeded)["user"] == "U_RAVI"


def test_other_bot_messages_are_not_conversation():
    assert not is_conversation({"subtype": "bot_message", "bot_id": "B9", "text": "Deploy finished"})
