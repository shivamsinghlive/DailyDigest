"""All settings in one place. Values come from .env / environment where it makes sense,
so reviewers can switch providers without editing code."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # the project folder (this file is in digest_tool/)
load_dotenv(ROOT / ".env")
# DIGEST_DATA_DIR points the whole tool at another dataset (e.g. one converted by
# scripts/import_slack_export.py). Relative paths are taken from the project folder.
DATA_DIR = ROOT / os.getenv("DIGEST_DATA_DIR", "data")
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
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_PRICE_PER_MTOK = {"input": 1.00, "output": 5.00}  # Haiku 4.5, USD; used for cost estimates
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")

# Part of the cache key. Bump it when the extraction prompt changes so old outputs aren't reused.
PROMPT_VERSION = "v4"

# A question with no reply from anyone else after this long becomes "unanswered".
UNANSWERED_AFTER_HOURS = 48

# A decision or fix in another thread closes an open problem that shares a part with it, if the
# problem's thread was active within this many days (notebook.close_linked_problems).
LINK_WINDOW_DAYS = 5

# Issue identity (issues.py): how many existing issues a new problem is compared against, and how
# similar the wording must be to link them when no LLM verdict is available.
LINK_MAX_CANDIDATES = 5
LINK_MIN_SIMILARITY = 0.4

# ---------- inferred ownership (notebook.py) ----------
# team.json is incomplete on purpose. Each person gets an ownership score per part from evidence,
# with older evidence counting less. Declared owners (team.json) always rank highest.
OWNERSHIP_EVIDENCE = {
    "mention": 1.0,       # you said something about it
    "question": 0.5,      # you asked about it (askers usually aren't owners)
    "answer": 2.0,        # you answered someone else's question about it
    "asked_about": 1.5,   # someone tagged or named you next to it
}
# Supply chain and the eng manager talk about every part (sourcing, schedule), so what they say
# about a hardware part is weak evidence of owning it. Topics like purchase orders are exempt.
OWNERSHIP_ROLE_FACTOR = {"supply_chain": 0.3, "engineering_manager": 0.3}
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
PULSE_SIZE = 3                     # at most; same items for everyone, for alignment. Can be 0.
PULSE_MIN_SCORE = 12.0             # type + severity + breadth; see digest.pulse_score
