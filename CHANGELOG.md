# Eng_Bench Changelog

All notable dataset, tooling, and release-protocol changes should be recorded here.

## Unreleased

- v2.0 infrastructure: added hidden/public challenge split tooling, submission-format validation, private-label safety files, leaderboard protocol documentation, and v2.0 gate auditing.
- v1.5 preparation: packaged a 725-row human review handoff for microtext and visualdiff adjudication.
- Review operations: added staged review-queue inventory reporting so open, mergeable, rejected, and missing-evidence review rows can be tracked before gold promotion.

## v0.95 Packaging-Clean Candidate

- Structural package health passes locally with zero missing unified image paths.
- Split leakage checks pass for current visualdiff families and microtext source documents.
- Low-confidence dev/test visualdiff rows from the earlier human polish packet were adjudicated or quarantined.
- Public input-only dev/test exports exist for the current silver benchmark.
- Baseline runner, baseline reports, dataset metadata rehearsal, release manifest, loader smoke, and question leakage/diversity audits are available.

## Historical Notes

- v0.9 Silver established the current two-task benchmark shape: visualdiff revision understanding and microtext reading in engineering documents.
- Gold status is reserved for releases whose dev/test labels are human-adjudicated, whose public assets are rights-reviewed, and whose release gates pass.
