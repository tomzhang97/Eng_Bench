import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.audit_staged_promotion_contract import build_report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class StagedPromotionContractTest(unittest.TestCase):
    def build_root(self) -> tuple[Path, tempfile.TemporaryDirectory, Path, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        docs = []
        inventory = []
        for doc_id in ("micro", "old", "new"):
            source = root / "sources" / f"{doc_id}.pdf"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(doc_id.encode())
            docs.append({
                "type": "doc",
                "doc_id": doc_id,
                "task": "visualdiff" if doc_id in {"old", "new"} else "microtext",
                "path": source.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(doc_id.encode()).hexdigest(),
                "source_url": f"https://example.test/{doc_id}",
                "public_status": "public_domain",
                "same_model_id": "family" if doc_id in {"old", "new"} else "",
                "version": {"revision": doc_id},
            })
            inventory.append({
                "doc_id": doc_id,
                "task": docs[-1]["task"],
                "path": source.relative_to(root).as_posix(),
                "source_url": f"https://example.test/{doc_id}",
                "public_status": "public_domain",
            })
        project = "vdiff__family__old__to__new"
        docs.append({"type": "pair", "pair_id": project, "from_doc_id": "old", "to_doc_id": "new"})
        write_jsonl(root / "manifest.jsonl", docs)
        with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=inventory[0].keys())
            writer.writeheader()
            writer.writerows(inventory)
        for doc_id in ("micro", "old", "new"):
            page = root / "pages" / f"{doc_id}.png"
            page.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (100, 100), "white").save(page)
        write_jsonl(root / "microtext/annotations/microtext_items.jsonl", [])
        write_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl", [])
        cohort = root / "cohort.jsonl"
        rows = [
            {
                "candidate_id": "candidate_1",
                "doc_id": "micro",
                "version_id": "v1",
                "page_index": 0,
                "bbox": [1, 2, 30, 40],
                "image_path": "pages/micro.png",
                "category": "pin_label",
                "proposed_text": "P1",
                "review_status": "needs_review",
                "safe_to_merge_gold": False,
                "reserved_split": "test",
                "split_reservation_id": "micro-res",
            },
            {
                "pair_id": f"{project}__p0000__001",
                "project_id": project,
                "page_old": 0,
                "page_new": 0,
                "bbox_old": [1, 2, 30, 40],
                "bbox_new": [5, 6, 35, 45],
                "image_old": "pages/old.png",
                "image_new": "pages/new.png",
                "change_type": "text_added_candidate",
                "review_status": "needs_review",
                "safe_to_merge_gold": False,
                "reserved_split": "dev",
                "split_reservation_id": "visual-res",
            },
        ]
        write_jsonl(cohort, rows)
        plan = root / "plan.json"
        plan.write_text(json.dumps({
            "valid": True,
            "reservations": [
                {"reservation_id": "micro-res", "task": "microtext", "unit_id": "micro", "split": "test"},
                {"reservation_id": "visual-res", "task": "visualdiff", "unit_id": project, "split": "dev"},
            ],
        }), encoding="utf-8")
        return root, temp, cohort, plan

    def test_valid_unreviewed_rows_are_structurally_ready(self) -> None:
        root, temp, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)

        report, issues = build_report(root, [("fixture", cohort)], plan, date_label="fixture")

        self.assertTrue(report["structurally_ready_for_human_return_promotion"])
        self.assertFalse(report["human_review_complete"])
        self.assertEqual(0, report["counts"]["fatal_issue_rows"])
        self.assertEqual(1, report["human_actions"]["microtext:decision_required"])
        self.assertEqual(1, report["human_actions"]["visualdiff:decision_required"])
        self.assertEqual({"addition+text": 1}, report["normalized_visualdiff_change_types"])
        self.assertFalse(issues)

    def test_split_mismatch_and_missing_image_are_fatal(self) -> None:
        root, temp, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        rows = [json.loads(line) for line in cohort.read_text(encoding="utf-8").splitlines()]
        rows[0]["reserved_split"] = "train"
        rows[1]["image_new"] = "pages/missing.png"
        write_jsonl(cohort, rows)

        report, issues = build_report(root, [("fixture", cohort)], plan, date_label="fixture")

        self.assertFalse(report["structurally_ready_for_human_return_promotion"])
        self.assertIn("fatal:split_reservation_mismatch", report["issues"])
        self.assertIn("fatal:missing_evidence_image", report["issues"])
        self.assertEqual(2, len(issues))

    def test_active_gold_overlap_is_fatal(self) -> None:
        root, temp, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        active = {
            "item_id": "mt__active",
            "source_candidate_id": "candidate_1",
            "doc_id": "micro",
            "page_index": 0,
            "bbox": [1, 2, 30, 40],
        }
        write_jsonl(root / "microtext/annotations/microtext_items.jsonl", [active])

        report, _ = build_report(root, [("fixture", cohort)], plan, date_label="fixture")

        self.assertFalse(report["structurally_ready_for_human_return_promotion"])
        self.assertEqual(1, report["issues"]["fatal:active_gold_overlap"])

    def test_reused_lineage_alias_does_not_block_distinct_regions(self) -> None:
        root, temp, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        rows = [json.loads(line) for line in cohort.read_text(encoding="utf-8").splitlines()]
        rows[0]["pre_padding_candidate_id"] = "mtcand__shared_detector_lineage"
        second = dict(rows[0])
        second.update({
            "candidate_id": "candidate_2",
            "bbox": [31, 2, 60, 40],
            "pre_padding_candidate_id": "mtcand__shared_detector_lineage",
        })
        write_jsonl(cohort, [*rows, second])

        report, issues = build_report(root, [("fixture", cohort)], plan, date_label="fixture")

        self.assertTrue(report["structurally_ready_for_human_return_promotion"])
        self.assertEqual(1, report["contract_notes"]["reused_upstream_lineage_aliases"])
        self.assertFalse(issues)

    def test_duplicate_promotion_alias_blocks_distinct_regions(self) -> None:
        root, temp, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        rows = [json.loads(line) for line in cohort.read_text(encoding="utf-8").splitlines()]
        second = dict(rows[0])
        second["bbox"] = [31, 2, 60, 40]
        write_jsonl(cohort, [*rows, second])

        report, issues = build_report(root, [("fixture", cohort)], plan, date_label="fixture")

        self.assertFalse(report["structurally_ready_for_human_return_promotion"])
        self.assertEqual(1, report["issues"]["fatal:duplicate_staged_alias"])
        self.assertEqual(1, len(issues))

    def test_valid_legacy_split_reservation_id_is_preserved(self) -> None:
        root, temp, cohort, plan = self.build_root()
        self.addCleanup(temp.cleanup)
        rows = [json.loads(line) for line in cohort.read_text(encoding="utf-8").splitlines()]
        legacy = root / "legacy-plan.json"
        legacy.write_text(json.dumps({
            "valid": True,
            "reservations": [
                {"reservation_id": "legacy-micro", "task": "microtext", "unit_id": "micro", "split": "test"},
            ],
        }), encoding="utf-8")
        rows[0]["split_reservation_id"] = "legacy-micro"
        rows[0]["split_reservation_plan"] = "legacy-plan.json"
        write_jsonl(cohort, rows)

        report, issues = build_report(root, [("fixture", cohort)], plan, date_label="fixture")

        self.assertTrue(report["structurally_ready_for_human_return_promotion"])
        self.assertEqual(1, report["contract_notes"]["valid_legacy_split_reservation_ids"])
        self.assertFalse(issues)


if __name__ == "__main__":
    unittest.main()
