# Eng_Bench Leaderboard Protocol

This protocol defines the machine-readable path for public and hidden Eng_Bench evaluation. It is infrastructure for v2.0 readiness; it does not by itself make the current dataset v2.0.

## Challenge Splits

Use:

```powershell
python tools\make_public_private_split.py --root .
```

The command reads `eng_bench.jsonl` test rows and writes:

| File | Purpose | Public package |
| --- | --- | --- |
| `release/public_inputs/eng_bench_public_test_inputs.jsonl` | Input-only public test examples | yes |
| `release/public_inputs/eng_bench_hidden_test_inputs.jsonl` | Input-only hidden challenge examples | yes, if image licenses allow |
| `release/private_labels/eng_bench_hidden_test_labels.jsonl` | Hidden labels for maintainer scoring | no |
| `release/private_labels/challenge_split_manifest.json` | Deterministic split manifest | no |

Hidden labels must never be included in public release archives, public dataset uploads, or sample packages.

## Submission Format

Submissions are JSONL with one prediction per input row:

```json
{"id":"example_id","answer":"predicted text or change description","evidence":[{"bbox":[10,20,80,90],"image_index":0}]}
```

Required:

- `id`, `question_id`, or `qid` matching the input row.
- One answer field: `answer`, `answer_text`, `change_desc_gt`, or `text_gt`.

Optional but strongly recommended:

- `evidence`: list of bounding boxes.
- `change_types`: visualdiff change type predictions.

Validate locally:

```powershell
python tools\validate_submission_format.py `
  --inputs release\public_inputs\eng_bench_hidden_test_inputs.jsonl `
  --predictions my_predictions.jsonl
```

## Maintainer Scoring

Maintainers score hidden submissions with the private labels:

```powershell
python tools\benchmark_runner.py `
  --gt release\private_labels\eng_bench_hidden_test_labels.jsonl `
  --pred my_predictions.jsonl `
  --split all `
  --task all `
  --model-name submitted_model `
  --bootstrap-samples 1000 `
  --report-json leaderboard\submissions\submitted_model_report.json `
  --report-md leaderboard\submissions\submitted_model_report.md
```

The public report can include aggregate metrics and confidence intervals. It must not expose hidden labels, hidden evidence boxes, or per-row answer strings.

## Counted Leaderboard Entry

The v2.0 adoption gate counts only machine-valid, content-distinct scored entries under
`leaderboard/submissions/*.json`. A counted entry uses this wrapper:

```json
{
  "dataset_version": "v2.0-dev",
  "split_manifest_hash": "64-character-sha256",
  "model_name": "submitted_model",
  "prediction_hash": "64-character-sha256",
  "scorer_command": "python tools/benchmark_runner.py ...",
  "metrics": {
    "microtext.normalized_exact_match": 0.25
  },
  "confidence_intervals": {
    "microtext.normalized_exact_match": {"low": 0.20, "high": 0.30}
  }
}
```

Rules:

- Both hashes must be SHA-256 hex digests.
- `metrics` and `confidence_intervals` must be non-empty.
- Every confidence interval must match a metric and contain finite numeric `low` and `high` values.
- Multiple entries with the same `prediction_hash` count once.
- Empty, malformed, smoke, oracle, and README-like files do not count.

Run `python tools\audit_v2_0_gate.py --root .` to inspect counted and excluded entries.

Create the wrapper from a frozen split manifest, submitted predictions, and the
JSON report produced by `benchmark_runner.py`:

```powershell
python tools\register_leaderboard_submission.py `
  --root . `
  --dataset-version v2.0-dev `
  --manifest release\private_labels\challenge_split_manifest.json `
  --predictions my_predictions.jsonl `
  --report leaderboard\scored\submitted_model_report.json `
  --scorer-command "python tools/benchmark_runner.py --gt ... --pred ... --bootstrap-samples 1000"
```

The registration tool refuses to overwrite an existing entry unless `--force`
is supplied.

## Versioning Rules

- A challenge split is tied to a dataset version and deterministic seed.
- Corrections to labels create a new benchmark patch version.
- Rows removed for rights or quality reasons invalidate only future versions, not already frozen reports.
- A counted leaderboard entry must include the dataset version, split manifest hash, model name, prediction file hash, scorer command, aggregate metrics, and confidence intervals.
