from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_visualdiff_family_review_queue


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class VisualDiffFamilyReviewQueueUnittest(unittest.TestCase):
    def test_selects_only_fresh_release_registered_new_families(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation = root / "SOURCE_CANDIDATE_VALIDATION.csv"
            with validation.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["candidate_id", "release_posture", "next_action"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"candidate_id": "good", "release_posture": "release_candidate", "next_action": "intake_first"},
                        {"candidate_id": "held", "release_posture": "release_candidate", "next_action": "rights_hold"},
                    ]
                )
            evidence = root / "evidence"
            evidence.mkdir()
            for name in ("old.png", "new.png"):
                Image.new("RGB", (8, 8), "white").save(evidence / name)
            base = {
                "split": "provisional_review",
                "review_status": "needs_review",
                "description": "CHANGE_DESC_GT_TODO",
                "page_old": "evidence/old.png",
                "page_new": "evidence/new.png",
                "image_old": "evidence/old.png",
                "image_new": "evidence/new.png",
            }
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl",
                [{"pair_id": "vdiff__active__v1__to__v2__p0000__000"}],
            )
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_review_candidates.jsonl",
                [
                    {**base, "pair_id": "vdiff__new_a__v1__to__v2__p0000__000", "source_candidate_id": "good"},
                    {**base, "pair_id": "vdiff__new_b__v1__to__v2__p0000__000", "source_candidate_id": "held"},
                    {**base, "pair_id": "vdiff__active__v1__to__v2__p0000__001", "source_candidate_id": "good"},
                ],
            )

            rows, report = build_visualdiff_family_review_queue.select_family_rows(
                root,
                packet_date_label="missing-index",
                target_new_families=5,
                rows_per_family=3,
            )

            self.assertEqual([row["pair_id"] for row in rows], ["vdiff__new_a__v1__to__v2__p0000__000"])
            self.assertEqual(report["selected_new_families"], 1)
            self.assertEqual(report["selected_rows"], 1)
            self.assertEqual(report["counters"]["excluded_active_family"], 1)
            self.assertEqual(report["counters"]["excluded_not_release_registered"], 1)

    def test_packet_manifest_excludes_the_entire_family(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            packet_manifest = root / "packet" / "manifest.jsonl"
            write_jsonl(
                packet_manifest,
                [{"pair_id": "vdiff__already_packeted__v1__to__v2__p0000__000"}],
            )

            families = build_visualdiff_family_review_queue.families_from_packet_manifests(
                root,
                ["packet/manifest.jsonl"],
            )

            self.assertEqual(families, {"vdiff__already_packeted__v1"})

    def test_row_file_excludes_only_matching_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (root / "SOURCE_CANDIDATE_VALIDATION.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["candidate_id", "release_posture", "next_action"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "candidate_id": "good",
                        "release_posture": "release_candidate",
                        "next_action": "human_review",
                    }
                )

            evidence = root / "evidence"
            evidence.mkdir()
            for name in ("old.png", "new.png"):
                Image.new("RGB", (8, 8), "white").save(evidence / name)
            base = {
                "split": "provisional_review",
                "review_status": "needs_review",
                "description": "CHANGE_DESC_GT_TODO",
                "page_old": "evidence/old.png",
                "page_new": "evidence/new.png",
                "image_old": "evidence/old.png",
                "image_new": "evidence/new.png",
                "source_candidate_id": "good",
            }
            excluded_id = "vdiff__same_family__v1__to__v2__p0000__000"
            retained_id = "vdiff__same_family__v1__to__v2__p0000__001"
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])
            write_jsonl(
                root / "visualdiff" / "annotations" / "visualdiff_review_candidates.jsonl",
                [
                    {**base, "pair_id": excluded_id},
                    {**base, "pair_id": retained_id},
                ],
            )
            write_jsonl(root / "packet" / "assigned.jsonl", [{"pair_id": excluded_id}])

            excluded = build_visualdiff_family_review_queue.identities_from_row_files(
                root, ["packet/assigned.jsonl"]
            )
            rows, report = build_visualdiff_family_review_queue.select_family_rows(
                root,
                packet_date_label="missing-index",
                target_new_families=5,
                rows_per_family=5,
                excluded_row_identities=excluded,
            )

            self.assertEqual(excluded, {excluded_id})
            self.assertEqual([row["pair_id"] for row in rows], [retained_id])
            self.assertEqual(report["excluded_row_identities"], 1)
            self.assertEqual(report["counters"]["excluded_row_identity"], 1)


if __name__ == "__main__":
    unittest.main()
