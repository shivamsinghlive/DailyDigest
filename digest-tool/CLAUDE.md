# Daily Digest Tool — Project Context

## What this is
A take-home assignment for EverCurrent. A tool that gives each member of a robotics
hardware team a personalized daily digest of what happened in Slack.
Personalization depends on three things:
1. Role (mechanical eng, electrical eng, supply chain, eng manager, product manager)
2. Project phase (Concept → EVT → DVT → PVT → Production)
3. Current personal focus, which changes over time (inferred from recent activity)

## Core idea
We don't just summarize messages. We maintain a "project notebook" (project state):
parts, owners, open problems, decisions, unanswered questions, and the current phase.
Each Slack thread updates the notebook. The digest = what changed in the notebook
that matters to this person, with a reason for each item.

Key feature: cross-team alerts. If supply chain posts about a part, the engineer
who owns that part gets alerted even if they never read that channel.

## Tech stack
- Python 3.11+ (code also stays 3.9-compatible: no `match`, no `X | None` hints)
- Streamlit for the UI
- LLM is optional and swappable: LLM_PROVIDER = "anthropic" | "ollama" | "none"
- No database; JSON files

## Folder structure
```
digest-tool/
  data/
    team.json                 # people, roles, declared owners (about half left out on purpose)
    true_owners.json          # TEST ONLY: real owners. Only evaluate.py may read it.
    parts.json                # parts catalog: official names, nicknames, subsystem
    messages.json             # fake Slack messages in real Slack format
    ground_truth.json         # tuned stories: who should be alerted, pulse vs personal, nickname labels
    ground_truth_holdout.json # holdout stories (written separately). Never tune on these.
    cache/                    # saved LLM outputs (extractions.json, digests.json) — committed
    feedback.json             # 👍/👎 log (not committed)
  app.py                # Streamlit UI (entry point, stays at the root for `streamlit run app.py`)
  digest_tool/          # the library; run modules with `python -m digest_tool.<module>`
    config.py           # settings (provider, model, weights)
    slack_loader.py     # load_messages(source="fake"|"slack"), thread grouping
    catalog.py          # nickname -> official part name (exact, normalized, fuzzy) + unknown parts
    extract.py          # LLM turns each thread into structured data
    notebook.py         # project state: subsystem phases, owners (declared/likely/possible), open items
    ranker.py           # scoring + reasons
    feedback.py         # per-person, per-item-type multipliers from 👍/👎
    digest.py           # Team Pulse + For You per person per day
    evaluate.py         # compares results to ground truth
  tests/                # pytest; tiny made-up inputs only, never the real dataset
  scripts/make_fake_data.py   # regenerates data/messages.json + ground truth from scripts/sources/
```
Inside digest_tool/ use relative imports (`from .catalog import match_parts`).
Run `pytest` after any change to catalog, phase detection or ownership.

## Rules
- Keep code simple and readable; it will be reviewed by engineers.
  Short comments explaining WHY, not just what.
- Do not over-engineer. No classes unless they clearly help.
- LLM cost control: every LLM result is cached in data/cache/. If a cached result
  exists, never call the LLM again. The project MUST run fully with provider="none"
  using only cached results, so reviewers can run it without an API key.
- Every digest item must carry a human-readable reason (e.g. "You own the J4 connector").
- Messages use real Slack fields: channel, user, ts, thread_ts, text.
- Work one step at a time. After each step, explain what was built and how to test it.
- Holdout stories (ground_truth_holdout.json) are never tuned on. Don't read them while changing
  scoring; don't change scoring, weights or the catalog to fix a holdout miss; write a new holdout
  set first. Once holdout results have been looked at in detail, that set is spent: move it to tuned.
- The tool must never read data/true_owners.json; only evaluate.py does.
