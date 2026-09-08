from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/import_espressif_dimension_images_2026_08_30.py"
SPEC = importlib.util.spec_from_file_location("espressif_dimension_import", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_records_are_review_only_and_unique() -> None:
    docs, inventory, candidates = MODULE.build_records()

    assert len(docs) == 4
    assert len(inventory) == 4
    assert len(candidates) == 9
    assert len({row["doc_id"] for row in docs}) == 4
    assert len({row["candidate_id"] for row in candidates}) == 9
    assert {row["category"] for row in candidates} == {"dimension_value"}
    assert {row["same_model_id"] for row in docs + candidates} == {
        MODULE.SAME_MODEL_ID
    }
    assert {row["source_candidate_id"] for row in docs + candidates} == {
        MODULE.SOURCE_CANDIDATE_ID
    }
    assert all(row["safe_to_merge_gold"] is False for row in candidates)
    assert all(row["split"] == "provisional_review" for row in candidates)
    assert all(row["reserved_split"] == "test" for row in candidates)


def test_all_label_boxes_fit_source_dimensions() -> None:
    for source in MODULE.SOURCES:
        for _, bbox in source["labels"]:
            MODULE.validate_bbox(bbox, source["size"])


def test_append_manifest_is_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text('{"type":"doc","doc_id":"existing"}\n', encoding="utf-8")
    docs, _, _ = MODULE.build_records()

    assert MODULE.append_manifest(path, docs) == 4
    assert MODULE.append_manifest(path, docs) == 0
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5

    conflict = dict(docs[0])
    conflict["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest conflict"):
        MODULE.append_manifest(path, [conflict])


def test_append_inventory_is_idempotent_and_conflict_safe(tmp_path: Path) -> None:
    path = tmp_path / "SOURCE_INVENTORY.csv"
    fields = [
        "doc_id",
        "domain",
        "task",
        "public_status",
        "source_path",
        "source_url",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()

    _, inventory, _ = MODULE.build_records()
    assert MODULE.append_inventory(path, inventory) == 4
    assert MODULE.append_inventory(path, inventory) == 0

    conflict = dict(inventory[0])
    conflict["source_url"] = "https://example.invalid/conflict"
    with pytest.raises(ValueError, match="inventory conflict"):
        MODULE.append_inventory(path, [conflict])
