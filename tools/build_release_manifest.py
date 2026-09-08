#!/usr/bin/env python3
"""Build a hash manifest for release-critical Eng_Bench files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_PATHS = [
    "eng_bench.jsonl",
    "manifest.jsonl",
    "dataset_infos.json",
    "pyproject.toml",
    "README.md",
    "BENCHMARK_CARD.md",
    "BENCHMARK_RELEASE.md",
    "DATACARD.md",
    "SCHEMA.md",
    "SOURCE_INVENTORY.csv",
    "docs/ACTIVE_GOLD_PROVENANCE.csv",
    "docs/ANNOTATION_PROTOCOL.md",
    "docs/EVALUATION_PROTOCOL.md",
    "docs/RELEASE_CHECKLIST.md",
    "docs/SOURCE_QUOTAS.md",
    "docs/V1_EXPANSION_QUEUE.md",
    "docs/V1_EXPANSION_QUEUE.csv",
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "microtext/annotations/microtext_items.jsonl",
    "microtext/annotations/microtext_questions.jsonl",
    "splits/visualdiff_train.txt",
    "splits/visualdiff_dev.txt",
    "splits/visualdiff_test.txt",
    "splits/microtext_train.txt",
    "splits/microtext_dev.txt",
    "splits/microtext_test.txt",
    "derived/quality/microtext_quality_audit.json",
    "derived/quality/microtext_split_audit.json",
    "derived/quality/visualdiff_quality_audit.json",
    "derived/quality/question_diversity_report.json",
    "derived/quality/question_leakage_audit.json",
    "results/health/loader_smoke.json",
    "results/baselines/README.md",
    "results/baselines/BASELINE_TABLE.md",
    "release/public_inputs/eng_bench_dev_inputs.jsonl",
    "release/public_inputs/eng_bench_test_inputs.jsonl",
    "baselines/simple_diff_baseline.py",
    "baselines/textlayer_microtext_baseline.py",
    "baselines/tesseract_microtext_baseline.py",
    "tools/export_public_inputs.py",
    "tools/audit_active_gold_provenance.py",
    "tools/build_v1_expansion_queue.py",
    "tools/benchmark_health_report.py",
    "tools/benchmark_runner.py",
    "tools/validate_engbench.py",
    "tools/validate_engbench_v2.py",
    "splits/leakage_check.py",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(root: Path, rel_path: str) -> dict[str, Any]:
    normalized = rel_path.replace("\\", "/")
    path = root / normalized
    if not path.exists():
        return {"path": normalized, "exists": False, "size_bytes": None, "sha256": None}
    return {
        "path": normalized,
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_manifest(root: str | Path, paths: list[str] | None = None) -> dict[str, Any]:
    root = Path(root)
    entries = [file_entry(root, rel_path) for rel_path in (paths or DEFAULT_PATHS)]
    missing = [entry["path"] for entry in entries if not entry["exists"]]
    total_bytes = sum(int(entry["size_bytes"] or 0) for entry in entries)
    return {
        "schema_version": 1,
        "release_target": "v0.95-package-clean-candidate",
        "file_count": len(entries),
        "missing_count": len(missing),
        "missing_paths": missing,
        "total_bytes": total_bytes,
        "files": entries,
    }


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Eng_Bench Release File Manifest",
        "",
        f"- Release target: {manifest['release_target']}",
        f"- File count: {manifest['file_count']}",
        f"- Missing files: {manifest['missing_count']}",
        f"- Total bytes: {manifest['total_bytes']}",
        "",
        "| Path | Size bytes | SHA256 |",
        "| --- | ---: | --- |",
    ]
    for entry in manifest["files"]:
        size = entry["size_bytes"] if entry["exists"] else "MISSING"
        digest = entry["sha256"] or ""
        lines.append(f"| `{entry['path']}` | {size} | `{digest}` |")
    lines.append("")
    return "\n".join(lines)


def write_manifest(manifest: dict[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(manifest), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a release-critical file hash manifest.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="Specific relative path to include; can be passed multiple times",
    )
    parser.add_argument(
        "--output-json",
        default="results/health/release_file_manifest.json",
        help="Output JSON manifest path",
    )
    parser.add_argument(
        "--output-md",
        default="results/health/release_file_manifest.md",
        help="Output Markdown manifest path",
    )
    parser.add_argument("--allow-missing", action="store_true", help="Return success even if files are missing")
    args = parser.parse_args(argv)

    root = Path(args.root)
    manifest = build_manifest(root, args.paths)
    write_manifest(manifest, root / args.output_json, root / args.output_md)
    print(f"[OK] Wrote {root / args.output_json}")
    print(f"[OK] Wrote {root / args.output_md}")
    if manifest["missing_count"]:
        print(f"[ERR] Missing release files: {manifest['missing_paths']}")
    return 0 if args.allow_missing or manifest["missing_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
