#!/usr/bin/env python3
"""Build the label-safe, rights-screened snapshot published in the GitHub repo."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .audit_active_gold_provenance import (
        build_report as build_provenance_report,
        manifest_maps,
        resolve_visualdiff_docs,
    )
    from .export_public_inputs import strip_labels
except ImportError:
    from audit_active_gold_provenance import (
        build_report as build_provenance_report,
        manifest_maps,
        resolve_visualdiff_docs,
    )
    from export_public_inputs import strip_labels


LABEL_FIELDS = {"answer", "answer_text", "evidence", "evidence_ids", "text_gt", "change_desc_gt"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def required_docs_for_row(
    row: dict[str, Any],
    visualdiff_docs_by_pair: dict[str, tuple[list[str], str]],
) -> tuple[list[str], str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if row.get("task") == "microtext":
        doc_id = str(metadata.get("doc_id") or "")
        return ([doc_id], "") if doc_id else ([], "missing_microtext_doc_id")
    if row.get("task") == "visualdiff":
        pair_id = str(metadata.get("pair_id") or row.get("id") or "")
        return visualdiff_docs_by_pair.get(pair_id, ([], "unresolved_visualdiff_pair"))
    return [], "unsupported_task"


def partition_public_rows(
    rows: list[dict[str, Any]],
    release_ready_doc_ids: set[str],
    visualdiff_docs_by_pair: dict[str, tuple[list[str], str]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    outputs: dict[str, list[dict[str, Any]]] = {"train": [], "dev": [], "test": []}
    excluded: list[dict[str, Any]] = []
    for row in rows:
        required_docs, resolution_error = required_docs_for_row(row, visualdiff_docs_by_pair)
        blocked_docs = sorted(set(required_docs) - release_ready_doc_ids)
        split = str(row.get("split") or "")
        if resolution_error or not required_docs or blocked_docs or split not in outputs:
            excluded.append(
                {
                    "id": str(row.get("id") or ""),
                    "reason": resolution_error or ("rights_or_provenance_block" if blocked_docs else "invalid_split"),
                    "blocked_doc_ids": blocked_docs,
                }
            )
            continue
        outputs[split].append(strip_labels(row) if split == "test" else row)
    return outputs, excluded


def assert_test_is_label_free(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        leaked = LABEL_FIELDS.intersection(row)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        leaked.update(key for key in metadata if key in LABEL_FIELDS or str(key).endswith("_gt"))
        if leaked:
            raise ValueError(f"test row {row.get('id')} contains label fields: {sorted(leaked)}")


def build_snapshot(root: Path, output_dir: Path, date_label: str) -> dict[str, Any]:
    provenance = build_provenance_report(root, date_label=date_label)
    release_ready_doc_ids = {
        str(row["doc_id"])
        for row in provenance["documents"]
        if row.get("release_ready") and row.get("paper_ready")
    }
    blocked_docs = sorted(
        str(row["doc_id"])
        for row in provenance["documents"]
        if not (row.get("release_ready") and row.get("paper_ready"))
    )

    docs, manifest_pairs = manifest_maps(root)
    visualdiff_pairs = read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
    visualdiff_docs_by_pair = {
        str(pair.get("pair_id") or ""): resolve_visualdiff_docs(pair, docs, manifest_pairs)
        for pair in visualdiff_pairs
    }
    unified_rows = read_jsonl(root / "eng_bench.jsonl")
    outputs, excluded = partition_public_rows(
        unified_rows,
        release_ready_doc_ids,
        visualdiff_docs_by_pair,
    )
    assert_test_is_label_free(outputs["test"])

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "train": output_dir / "eng_bench_train.jsonl",
        "dev": output_dir / "eng_bench_dev.jsonl",
        "test": output_dir / "eng_bench_test_inputs.jsonl",
    }
    for split, path in output_paths.items():
        write_jsonl(path, outputs[split])

    required_images = sorted({image for rows in outputs.values() for row in rows for image in row.get("images", [])})
    missing_images = [image for image in required_images if not (root / image).is_file()]
    if missing_images:
        raise FileNotFoundError(f"public snapshot references {len(missing_images)} missing images")

    excluded_reason_counts = Counter(row["reason"] for row in excluded)
    excluded_blocked_doc_counts = Counter(
        doc_id for row in excluded for doc_id in row.get("blocked_doc_ids", [])
    )
    manifest = {
        "date_label": date_label,
        "source": "eng_bench.jsonl",
        "source_sha256": file_sha256(root / "eng_bench.jsonl"),
        "policy": {
            "train_and_dev": "labeled rows from paper-ready, release-ready sources",
            "test": "input-only rows; answer and evidence label fields removed",
            "excluded": "rows with unresolved source mapping or any non-paper-ready source payload",
        },
        "counts": {
            "source_rows": len(unified_rows),
            "published_rows": sum(len(rows) for rows in outputs.values()),
            "train": len(outputs["train"]),
            "dev": len(outputs["dev"]),
            "test_inputs": len(outputs["test"]),
            "excluded_rows": len(excluded),
            "required_images": len(required_images),
            "release_ready_source_docs": len(release_ready_doc_ids),
            "blocked_source_docs": len(blocked_docs),
        },
        "blocked_source_doc_ids": blocked_docs,
        "excluded_reason_counts": dict(sorted(excluded_reason_counts.items())),
        "excluded_blocked_doc_counts": dict(sorted(excluded_blocked_doc_counts.items())),
        "required_images": required_images,
        "outputs": {
            split: {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
            }
            for split, path in output_paths.items()
        },
    }
    manifest_path = output_dir / "snapshot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="release/public_dataset")
    parser.add_argument("--date-label", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    output_dir = root / args.output_dir
    manifest = build_snapshot(root, output_dir, args.date_label)
    print(json.dumps(manifest["counts"], indent=2, sort_keys=True))
    print(f"[OK] Wrote {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
