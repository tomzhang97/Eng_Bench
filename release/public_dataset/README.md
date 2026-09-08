# Public GitHub Snapshot

This directory is the public, release-screened view of the current Eng_Bench
Silver working dataset.

- `eng_bench_train.jsonl` and `eng_bench_dev.jsonl` contain labeled rows whose
  source payloads pass the current rights, provenance, and SHA-256 checks.
- `eng_bench_test_inputs.jsonl` is input-only. Answer, evidence, and other
  ground-truth fields are deliberately omitted.
- `snapshot_manifest.json` records the source hash, output hashes, exclusions,
  and the exact image paths required by the snapshot.

The maintainer-side `eng_bench.jsonl`, reviewer packets, human votes, and
private challenge labels are not part of this public snapshot. This is a
working Silver checkpoint, not a completed Gold v2.0 Global release.

Regenerate from the maintainer checkout with:

```powershell
python tools\build_public_repository_snapshot.py --root . --date-label YYYY-MM-DD
```
