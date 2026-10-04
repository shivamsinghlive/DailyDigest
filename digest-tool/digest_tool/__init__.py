"""Daily Digest: personalized Slack digests for a hardware team.

Pipeline (each module feeds the next):
    slack_loader -> extract (+ catalog) -> notebook -> ranker (+ feedback) -> digest
evaluate.py checks it against data/ground_truth*.json; app.py (project root) is the UI.
"""
