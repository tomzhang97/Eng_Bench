# Public Repository Sync Policy

The GitHub repository is a public, reproducible view of the Eng_Bench working
benchmark. It is not a mirror of every maintainer-side artifact.

## Included

- Public documentation, loader/evaluation code, validation tools, and tests.
- Rights and provenance metadata.
- Canonical images referenced by the generated public snapshot.
- Labeled train/dev rows from paper-ready, release-ready source payloads.
- Input-only test rows.

## Excluded

- Hidden-test labels and private challenge manifests.
- Human-review workbooks, reviewer assignments, votes, and adjudication packets.
- Rights-blocked source payloads and rows that depend on them.
- Temporary renders, caches, virtual environments, dependency trees, and local
  Codex work directories.

Every automated sync must run the baseline validator, strict v2 validator,
split audit, question-leakage audit, and active-Gold provenance audit. A failed
check blocks the push; the automation must never force-push or discard local
work to resolve a conflict.
