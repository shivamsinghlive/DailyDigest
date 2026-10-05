"""Tiny made-up fixtures shared by the tests. Nothing here reads the real dataset."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from digest_tool import config
from digest_tool.catalog import load_catalog

TINY_PARTS = {
    "subsystems": ["gripper", "wrist", "base", "cabling"],
    "parts": [
        {"name": "gripper", "subsystem": "gripper", "aliases": ["fingers", "pads"]},
        {"name": "wrist assembly", "subsystem": "wrist", "aliases": ["wrist"]},
        {"name": "motor driver", "subsystem": "wrist", "aliases": ["driver board", "TMC9660"]},
        {"name": "base casting", "subsystem": "base", "aliases": ["base"]},
        {"name": "J4 connector", "subsystem": "cabling",
         "aliases": ["J4", "43045-0412", "molex connector", "wrist conn"]},
    ],
    "topics": [{"name": "firmware", "aliases": ["fw"]}],
}


@pytest.fixture(autouse=True)
def no_real_cache(tmp_path, monkeypatch):
    """Tests never read or write data/cache: LLM verdicts faked in a test must not end up in the real cache."""
    from digest_tool import digest
    cache = tmp_path / "cache"
    monkeypatch.setattr(config, "CACHE_DIR", cache)
    monkeypatch.setattr(config, "EXTRACTIONS_CACHE", cache / "extractions.json")
    monkeypatch.setattr(config, "FEEDBACK_FILE", tmp_path / "feedback.json")
    monkeypatch.setattr(digest, "DIGEST_CACHE", cache / "digests.json")


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """A 5-part catalog, loaded through the real load_catalog() from a temp parts.json."""
    (tmp_path / "parts.json").write_text(json.dumps(TINY_PARTS))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return load_catalog()


@pytest.fixture
def team(catalog):
    """Three people. Only Ana has a declared part."""
    return {
        "project": {"name": "Test arm", "phases": ["Concept", "EVT", "DVT", "PVT", "Production"], "start_phase": "EVT"},
        "catalog": catalog,
        "people": [
            {"id": "U_ANA", "name": "Ana Lee", "role": "mechanical_engineer", "owns": ["gripper"]},
            {"id": "U_BEN", "name": "Ben Ortiz", "role": "electrical_engineer", "owns": []},
            {"id": "U_CY", "name": "Cy Park", "role": "supply_chain", "owns": []},
        ],
    }


@pytest.fixture
def make_msg():
    """Build a Slack-shaped message on a given September 2026 day (team time zone)."""
    tz = ZoneInfo(config.TIMEZONE)
    counter = iter(range(1000))

    def _make(text, user="U_ANA", day=21, hhmm="10:00", thread_ts=None, channel_name="general"):
        h, m = map(int, hhmm.split(":"))
        ts = f"{int(datetime(2026, 9, day, h, m, tzinfo=tz).timestamp())}.{next(counter):06d}"
        msg = {"type": "message", "channel": "C_" + channel_name.upper(), "channel_name": channel_name,
               "user": user, "ts": ts, "text": text}
        if thread_ts:
            msg["thread_ts"] = thread_ts
        return msg

    return _make
