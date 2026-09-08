from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from tools import apply_reviewed_gold_promotion as promotion

from tools.apply_reviewed_gold_promotion import (
    apply_transaction,
    file_sha256,
    staged_split_bytes,
    validate_prepared_rows,
)
from tools.preview_reviewed_gold_promotion import build_preview, write_json


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class ApplyReviewedGoldPromotionTest(unittest.TestCase):
    def test_atomic_write_retries_transient_windows_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target.jsonl"
            path.write_bytes(b"before")
            error = PermissionError("temporary sharing lock")
            error.winerror = 5
            original_replace = promotion.os.replace
            calls = []

            def replace(source, target):
                calls.append((source, target))
                if len(calls) == 1:
                    raise error
                return original_replace(source, target)

            with patch.object(promotion.os, "replace", side_effect=replace), patch.object(promotion.time, "sleep") as sleep:
                promotion.atomic_write(path, b"after")
            self.assertEqual(b"after", path.read_bytes())
            self.assertEqual(2, len(calls))
            sleep.assert_called_once_with(0.05)
            self.assertEqual([path], list(path.parent.iterdir()))

    def test_atomic_write_persistent_lock_is_bounded_and_preserves_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target.jsonl"
            path.write_bytes(b"before")
            error = PermissionError("persistent sharing lock")
            error.winerror = 32
            with patch.object(promotion.os, "replace", side_effect=error) as replace, patch.object(promotion.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    promotion.atomic_write(path, b"after")
            self.assertEqual(6, replace.call_count)
            self.assertEqual(5, sleep.call_count)
            self.assertEqual(b"before", path.read_bytes())
            self.assertEqual([path], list(path.parent.iterdir()))

    def test_atomic_write_does_not_retry_other_permission_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target.jsonl"
            with patch.object(promotion.os, "replace", side_effect=PermissionError("denied")) as replace, patch.object(promotion.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    promotion.atomic_write(path, b"after")
            self.assertEqual(1, replace.call_count)
            sleep.assert_not_called()

    def test_prepared_tentative_description_cannot_bypass_preview(self) -> None:
        row = self.localized_visualdiff_row()
        row["change_desc_gt"] = "Localized text may have been added: 'U3'."
        row["desc_source"] = "human"
        with self.assertRaisesRegex(ValueError, "tentative_visualdiff_description"):
            validate_prepared_rows([], [row])

    def localized_visualdiff_row(self) -> dict:
        return {
            "pair_id": "vdiff__family__v1__to__v2__p0000__000",
            "project_id": "vdiff__family__v1__to__v2",
            "change_desc_gt": "The highlighted equipment tag changes from P-101 to P-102.",
            "change_type": ["text"],
            "desc_source": "human_semantics_machine_localized",
            "review_confidence": "high",
            "review_status": "edit",
            "human_review_status": "edit",
            "review_evidence": {
                "original_human_description": "高亮设备标签从 P-101 变为 P-102。",
                "localized_human_description": "The highlighted equipment tag changes from P-101 to P-102.",
                "localization_method": "machine_translation_and_visual_reconciliation",
                "reconciliation_evidence_sheet": "derived/quality/evidence.jpg#1",
            },
        }

    def test_localized_human_visualdiff_requires_complete_provenance(self) -> None:
        validate_prepared_rows([], [self.localized_visualdiff_row()])

        row = self.localized_visualdiff_row()
        del row["review_evidence"]["reconciliation_evidence_sheet"]
        with self.assertRaisesRegex(ValueError, "localized_evidence_sheet_missing"):
            validate_prepared_rows([], [row])

    def test_plain_machine_visualdiff_description_remains_rejected(self) -> None:
        row = self.localized_visualdiff_row()
        row["desc_source"] = "machine"
        with self.assertRaisesRegex(ValueError, "description_not_human"):
            validate_prepared_rows([], [row])

    def test_visualdiff_split_addition_uses_project_family(self) -> None:
        temp, root, _report_path, _report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        untouched_path = root / "splits/visualdiff_train.txt"
        untouched_bytes = b"# frozen policy\r\nexisting_family\r\n"
        untouched_path.write_bytes(untouched_bytes)

        staged, additions = staged_split_bytes(
            root,
            [],
            [{
                "pair_id": "vdiff__family__v1__to__v2__p0000__000",
                "project_id": "vdiff__family__v1__to__v2",
                "split": "dev",
            }],
        )

        self.assertEqual(
            b"vdiff__family__v1__to__v2\n",
            staged["splits/visualdiff_dev.txt"],
        )
        self.assertEqual(
            ["vdiff__family__v1__to__v2"],
            additions["visualdiff:dev"],
        )
        self.assertEqual(
            untouched_bytes,
            staged["splits/visualdiff_train.txt"],
        )

    def fixture(self) -> tuple[tempfile.TemporaryDirectory, Path, Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        source = root / "sources/micro.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"micro")
        source_hash = hashlib.sha256(b"micro").hexdigest()
        write_jsonl(root / "manifest.jsonl", [{
            "type": "doc",
            "doc_id": "micro",
            "task": "microtext",
            "path": "sources/micro.pdf",
            "sha256": source_hash,
            "source_url": "https://example.test/micro",
            "public_status": "public_domain",
            "version": {"revision": "v1"},
        }])
        with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["doc_id", "task", "path", "source_url", "public_status"],
            )
            writer.writeheader()
            writer.writerow({
                "doc_id": "micro",
                "task": "microtext",
                "path": "sources/micro.pdf",
                "source_url": "https://example.test/micro",
                "public_status": "public_domain",
            })
        image = root / "derived/pages_300dpi/micro/page_000.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (100, 100), "white").save(image)
        for relative in (
            "microtext/annotations/microtext_items.jsonl",
            "microtext/annotations/microtext_questions.jsonl",
            "visualdiff/annotations/visualdiff_pairs.jsonl",
            "visualdiff/annotations/visualdiff_questions.jsonl",
            "eng_bench.jsonl",
        ):
            write_jsonl(root / relative, [])
        for task in ("microtext", "visualdiff"):
            for split in ("train", "dev", "test"):
                path = root / "splits" / f"{task}_{split}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
        plan = root / "plan.json"
        plan.write_text(json.dumps({
            "valid": True,
            "reservations": [{
                "reservation_id": "r1",
                "task": "microtext",
                "unit_id": "micro",
                "split": "test",
            }],
        }), encoding="utf-8")
        reviewed = root / "reviewed.jsonl"
        write_jsonl(reviewed, [{
            "candidate_id": "candidate_1",
            "doc_id": "micro",
            "version_id": "v1",
            "page_index": 0,
            "bbox": [1, 2, 30, 40],
            "image_path": "derived/pages_300dpi/micro/page_000.png",
            "category": "equipment_tag",
            "proposed_text": "P-101",
            "review_status": "accepted",
            "safe_to_merge_gold": False,
            "reserved_split": "test",
            "split_reservation_id": "r1",
        }])
        preview_dir = root / "derived/quality/preview"
        preview = build_preview(
            root, [reviewed], [], plan, preview_dir, "fixture"
        )
        report_path = preview_dir / "promotion_preview_report.json"
        write_json(report_path, preview)
        return temp, root, report_path, file_sha256(report_path)

    def active_bytes(self, root: Path) -> dict[str, bytes]:
        relatives = (
            "microtext/annotations/microtext_items.jsonl",
            "microtext/annotations/microtext_questions.jsonl",
            "visualdiff/annotations/visualdiff_pairs.jsonl",
            "visualdiff/annotations/visualdiff_questions.jsonl",
            "eng_bench.jsonl",
            "manifest.jsonl",
            "splits/microtext_train.txt",
            "splits/microtext_dev.txt",
            "splits/microtext_test.txt",
            "splits/visualdiff_train.txt",
            "splits/visualdiff_dev.txt",
            "splits/visualdiff_test.txt",
        )
        return {relative: (root / relative).read_bytes() for relative in relatives}

    def test_dry_run_validates_without_modifying_active_files(self) -> None:
        temp, root, report_path, report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        before = self.active_bytes(root)

        report = apply_transaction(root, report_path, report_hash)

        self.assertTrue(report["ready_to_apply"])
        self.assertFalse(report["applied"])
        self.assertEqual(1, report["validation"]["promoted_microtext_rows"])
        self.assertEqual(before, self.active_bytes(root))

    def test_apply_snapshots_and_promotes_green_preview(self) -> None:
        temp, root, report_path, report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        snapshot = root / "derived/snapshots/fixture"

        report = apply_transaction(
            root,
            report_path,
            report_hash,
            snapshot_dir=snapshot,
            apply=True,
        )

        self.assertTrue(report["applied"])
        self.assertFalse(report["rolled_back"])
        self.assertEqual(1, len((root / "eng_bench.jsonl").read_text().splitlines()))
        self.assertEqual("micro\n", (root / "splits/microtext_test.txt").read_text())
        self.assertTrue((snapshot / "snapshot_manifest.json").is_file())
        self.assertEqual(1, report["post_apply_validation"]["active_rows"])

    def test_stale_active_hash_blocks_before_write(self) -> None:
        temp, root, report_path, report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        before = self.active_bytes(root)
        (root / "splits/microtext_train.txt").write_text("unexpected\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "active_file_changed_since_preview"):
            apply_transaction(root, report_path, report_hash)

        self.assertEqual(before["eng_bench.jsonl"], (root / "eng_bench.jsonl").read_bytes())

    def test_tampered_artifact_is_rejected(self) -> None:
        temp, root, report_path, report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        preview = json.loads(report_path.read_text(encoding="utf-8"))
        artifact = root / preview["artifacts"]["unified"]
        artifact.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "preview_artifact_sha256_mismatch"):
            apply_transaction(root, report_path, report_hash)

    def test_tampered_prepared_human_status_is_rejected_even_with_rehashed_report(self) -> None:
        temp, root, report_path, _report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        preview = json.loads(report_path.read_text(encoding="utf-8"))
        prepared = root / preview["artifacts"]["prepared_microtext_items"]
        rows = [json.loads(line) for line in prepared.read_text(encoding="utf-8").splitlines()]
        rows[0]["review_status"] = "needs_review"
        write_jsonl(prepared, rows)
        preview["artifact_sha256"]["prepared_microtext_items"] = file_sha256(prepared)
        combined = root / preview["artifacts"]["combined_microtext_items"]
        combined_rows = [json.loads(line) for line in combined.read_text(encoding="utf-8").splitlines()]
        combined_rows[-1]["review_status"] = "needs_review"
        write_jsonl(combined, combined_rows)
        preview["artifact_sha256"]["combined_microtext_items"] = file_sha256(combined)
        write_json(report_path, preview)

        with self.assertRaisesRegex(ValueError, "prepared_row_contract_failed"):
            apply_transaction(root, report_path, file_sha256(report_path))

    def test_post_write_failure_restores_every_active_file(self) -> None:
        temp, root, report_path, report_hash = self.fixture()
        self.addCleanup(temp.cleanup)
        before = self.active_bytes(root)
        snapshot = root / "derived/snapshots/rollback"

        report = apply_transaction(
            root,
            report_path,
            report_hash,
            snapshot_dir=snapshot,
            apply=True,
            fail_after_replacements=3,
        )

        self.assertFalse(report["applied"])
        self.assertTrue(report["rolled_back"], report)
        self.assertIn("injected_post_replace_failure", report["failure"])
        self.assertEqual(before, self.active_bytes(root))


if __name__ == "__main__":
    unittest.main()
