# Eng_Bench Leaderboard

This directory is reserved for public leaderboard scaffolding and maintainer-scored submission reports.

Use `docs/LEADERBOARD_PROTOCOL.md` for the challenge split, submission validation, and private scoring commands.

Do not place hidden labels in this directory.

Only machine-valid, content-distinct scored entry wrappers under `submissions/*.json`
count toward the v2.0 adoption gate. Run `python tools\audit_v2_0_gate.py --root .`
to see counted entries and exclusion reasons.

Use `python tools\register_leaderboard_submission.py --help` to create a
countable entry from a frozen split manifest, prediction JSONL, and scored
report with bootstrap confidence intervals.
