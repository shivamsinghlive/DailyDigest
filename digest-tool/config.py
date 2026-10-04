"""All settings in one place. Values come from .env / environment where it makes sense,
so reviewers can switch providers without editing code."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
EXTRACTIONS_CACHE = CACHE_DIR / "extractions.json"

# ---------- messages ----------
# "fake" reads data/messages.json, "slack" calls the Slack Web API with SLACK_BOT_TOKEN.
MESSAGE_SOURCE = os.getenv("MESSAGE_SOURCE", "fake")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")

# All "what happened today" boundaries use the team's local time.
TIMEZONE = "America/Los_Angeles"

# ---------- LLM ----------
# "none" never calls an LLM: cached results are used, and keyword rules fill any gaps.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")

# Part of the cache key. Bump it when the extraction prompt changes so old outputs aren't reused.
PROMPT_VERSION = "v3"

# A question with no reply from anyone else after this long becomes "unanswered".
UNANSWERED_AFTER_HOURS = 48

# ---------- inferred ownership (notebook.py) ----------
# team.json is incomplete on purpose. Each person gets an ownership score per part from evidence,
# with older evidence counting less. Declared owners (team.json) always rank highest.
OWNERSHIP_EVIDENCE = {
    "mention": 1.0,       # you said something about it
    "question": 0.5,      # you asked about it (askers usually aren't owners)
    "answer": 2.0,        # you answered someone else's question about it
    "asked_about": 1.5,   # someone tagged or named you next to it
}
OWNERSHIP_HALF_LIFE_DAYS = 7
LIKELY_MIN_SCORE, LIKELY_MIN_SHARE = 3.0, 0.4      # "likely owner"
POSSIBLE_MIN_SCORE, POSSIBLE_MIN_SHARE = 1.5, 0.25  # "possible owner"

# ---------- scoring (ranker.py) ----------
# Starting guesses. Each person's 👍/👎 then scales whole item types up or down (feedback.py).
WEIGHTS = {
    "owns_declared": 5.0,     # strongest signal: it's your part per team.json, named in the text
    "owns_likely": 4.0,       # not on the roster, but your activity clearly says it's yours
    "owns_possible": 2.0,     # some evidence it's yours: below MIN_SCORE alone, needs another signal
    "owns_llm_linked": 2.0,   # your (declared/likely) part, but only the LLM linked it to the thread
    "mentioned": 2.0,         # referred to by name (an @-tag is a direct ask and always included)
    "role_phase": 2.0,        # this kind of change matters to your role in this phase
    "focus": 3.0,             # max boost for parts you've been discussing lately
    "urgency_per_level": 0.5, # per urgency level above 2 (so urgency 5 adds 1.5)
}
MIN_SCORE = 2.5             # below this an item is left out (unless it's a must-include)
FOCUS_HALF_LIFE_DAYS = 2.0  # a mention 2 days ago counts half as much as one today
FOCUS_WINDOW_DAYS = 7       # ignore activity older than this
FOCUS_SATURATION = 2.0      # this much decayed activity (~a mention a day for a week) = full focus boost
FOCUS_MIN_MENTIONS = 2      # one passing mention isn't "working on it lately"
TOP_N = 5

# ---------- feedback (feedback.py) ----------
FEEDBACK_FILE = DATA_DIR / "feedback.json"
FEEDBACK_STEP = 0.1                # each 👍 on a type of item: +10% for that type, each 👎: -10%
FEEDBACK_BOUNDS = (0.5, 1.5)       # gentle: feedback can halve a type's score, or add 50%, never more

# ---------- Team Pulse (digest.py) ----------
PULSE_SIZE = 3                     # same items for everyone, for alignment
PULSE_MIN_SCORE = 12.0             # type + severity + breadth; see digest.pulse_score
PULSE_CARRY_OVER_DAYS = 7          # on quiet days, still-open items from the last week fill the pulse
