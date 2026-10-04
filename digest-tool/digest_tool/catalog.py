"""Parts catalog: one official name per part, many nicknames.

Engineers say "J4", "J-4", "the molex conector" and "wrist conn" for the same part. Everything
downstream (ownership, focus, reasons) works on the official name, so every mention is mapped
here. The reason text still shows what was actually written.

Matching, in order:
  1. exact nickname (case-insensitive, word boundaries; longest match wins, no overlaps)
  2. normalized: punctuation and spaces ignored, so "J-4" == "J4"
  3. fuzzy: close spelling for longer nicknames, so "molex conector" == "molex connector"
Anything that looks like a part but matches nothing is reported as an unknown part, so a
human can add it to the catalog.
"""
import json
import re
from difflib import SequenceMatcher

from . import config

FUZZY_MIN_RATIO = 0.88   # how close a misspelling must be
FUZZY_MIN_LENGTH = 6     # don't fuzzy-match short nicknames: "pads" vs "pass" is not a typo

# Unknown-part detection (the backup when no LLM is available): part-number-like tokens, and
# "<modifier> <hardware word>" phrases that the catalog doesn't cover.
PART_NUMBER_RE = re.compile(r"\b(?:[A-Z]{2,}\d{4,}[A-Z]?|\d{5}-\d{4})\b")
HARDWARE_WORDS = r"connector|conn|encoder|bracket|cable|umbilical|sensor|puck|bearing|gearbox|magnet|relay|fuse"
HARDWARE_RE = re.compile(rf"\b({HARDWARE_WORDS})\b", re.IGNORECASE)
NOT_A_MODIFIER = set("""the a an that this those these our my your his her their its new old same current replacement
spare other another any some one two each every which what owns own is are was on in at of for to from with and or
not no so too very just also all both 4 pin i we you they he she it""".split())

_memo = {}


def load_catalog():
    with open(config.DATA_DIR / "parts.json") as f:
        cat = json.load(f)
    # Parts belong to a subsystem (which has a phase); topics like "firmware" or "purchase orders" don't.
    cat["entries"] = ([{**p, "kind": "part"} for p in cat["parts"]] +
                      [{**t, "kind": "topic", "subsystem": None} for t in cat["topics"]])
    cat["subsystem_of"] = {e["name"]: e["subsystem"] for e in cat["entries"]}
    cat["names"] = [e["name"] for e in cat["entries"]]
    cat["aliases"] = [(alias, e["name"]) for e in cat["entries"] for alias in [e["name"]] + e["aliases"]]
    return cat


def normalize(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _overlaps(start, end, taken):
    return any(start < t_end and t_start < end for t_start, t_end in taken)


def find_mentions(text, catalog):
    """Return (found, unknown): found = {official name: text as written}, unknown = [phrases]."""
    key = (id(catalog), text)
    if key in _memo:
        return _memo[key]

    # 1. exact nickname, longest first
    candidates = []
    for alias, name in catalog["aliases"]:
        for m in re.finditer(rf"(?<![\w-]){re.escape(alias)}(?!\w)", text, re.IGNORECASE):
            candidates.append((m.start(), m.end(), name, m.group(0)))
    candidates.sort(key=lambda c: c[1] - c[0], reverse=True)
    taken, found = [], {}
    for start, end, name, written in candidates:
        if not _overlaps(start, end, taken):
            taken.append((start, end))
            found.setdefault(name, written)

    # 2 + 3. normalized and fuzzy, on 1-3 word windows not already matched
    tokens = [(m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9\-./]*", text)]
    for n in (3, 2, 1):
        for i in range(len(tokens) - n + 1):
            start, end = tokens[i][0], tokens[i + n - 1][1]
            if _overlaps(start, end, taken):
                continue
            window = normalize(text[start:end])
            best = None
            for alias, name in catalog["aliases"]:
                target = normalize(alias)
                if window == target:
                    best = (1.0, name)
                    break
                # A typo keeps the length and first letter. Without this, a bare "connector" would
                # fuzzy-match "J7 connector" (only 2 characters apart).
                if (len(target) >= FUZZY_MIN_LENGTH and abs(len(window) - len(target)) <= 1
                        and window[:1] == target[:1]):
                    ratio = SequenceMatcher(None, window, target).ratio()
                    if ratio >= FUZZY_MIN_RATIO and (best is None or ratio > best[0]):
                        best = (ratio, name)
            if best:
                taken.append((start, end))
                found.setdefault(best[1], text[start:end])

    # Unknown parts: part numbers and "<modifier> <hardware word>" the catalog didn't cover.
    unknown = [m.group(0) for m in PART_NUMBER_RE.finditer(text) if not _overlaps(m.start(), m.end(), taken)]
    for m in HARDWARE_RE.finditer(text):
        if _overlaps(m.start(), m.end(), taken):
            continue
        words = text[:m.start()].split()
        modifiers = []
        for w in reversed(words[-2:]):  # walk back over up to 2 words that look like part of the name
            clean = w.strip("(),.:;!?\"'").lower()
            if (not re.fullmatch(r"[a-z][a-z0-9-]*", clean) or clean in NOT_A_MODIFIER   # "#212," isn't a name word
                    or _overlaps(text.rfind(w, 0, m.start()), m.start(), taken)):
                break
            modifiers.insert(0, w.strip("(),.:;!?\"'"))
        if modifiers:
            unknown.append(" ".join(modifiers + [m.group(0)]))

    _memo[key] = (found, unknown)
    return found, unknown


def match_parts(text, catalog):
    """{official name: text as written}."""
    return find_mentions(text, catalog)[0]


if __name__ == "__main__":
    cat = load_catalog()
    for text in ["J-4 latch keeps popping", "replacement for the molex conector", "wrist conn sits right at the bend",
                 "scoped the wrist driver (DRV8412)", "re-shimmed the slew ring", "the TPE jacket compound for the arm umbilical",
                 "wrist abs encoder (the mag puck) zero offset", "ams osram flagged AS5048A (the wrist mag encoder)",
                 "who owns that connector again?", "my dog just ate a JST connector"]:
        found, unknown = find_mentions(text, cat)
        print(f"{text!r:52} -> {found}  unknown={unknown}")
