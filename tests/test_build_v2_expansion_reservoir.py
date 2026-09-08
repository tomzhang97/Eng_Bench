from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.build_v2_expansion_reservoir import build_reservoir, row_aliases


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ExpansionReservoirTests(unittest.TestCase):
    def test_padded_candidate_exposes_unpadded_fingerprint_as_alias(self) -> None:
        aliases = row_aliases(
            {
                "candidate_id": "mtcand__padded",
                "pre_padding_candidate_id": "mtcand__original",
            }
        )

        self.assertEqual(aliases, {"mtcand__padded", "mtcand__original"})

    def test_filters_existing_unsafe_and_bad_evidence_and_conserves_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            annotations = root / "microtext" / "annotations"
            annotations.mkdir(parents=True)
            (root / "visualdiff" / "annotations").mkdir(parents=True)
            write_jsonl(annotations / "microtext_items.jsonl", [])
            write_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl", [])

            source_dir = root / "sources"
            source_dir.mkdir()
            safe_source = source_dir / "safe.pdf"
            unsafe_source = source_dir / "unsafe.pdf"
            safe_source.write_bytes(b"safe-source")
            unsafe_source.write_bytes(b"unsafe-source")
            safe_sha = hashlib.sha256(safe_source.read_bytes()).hexdigest()
            unsafe_sha = hashlib.sha256(unsafe_source.read_bytes()).hexdigest()
            write_jsonl(
                root / "manifest.jsonl",
                [
                    {
                        "type": "doc",
                        "task": "microtext",
                        "doc_id": "safe_doc",
                        "path": "sources/safe.pdf",
                        "source_url": "https://example.test/safe",
                        "public_status": "public_domain",
                        "sha256": safe_sha,
                    },
                    {
                        "type": "doc",
                        "task": "microtext",
                        "doc_id": "unsafe_doc",
                        "path": "sources/unsafe.pdf",
                        "source_url": "https://example.test/unsafe",
                        "public_status": "rights_uncertain",
                        "sha256": unsafe_sha,
                    },
                ],
            )
            with (root / "SOURCE_INVENTORY.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["doc_id", "path", "source_url", "public_status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "doc_id": "safe_doc",
                        "path": "sources/safe.pdf",
                        "source_url": "https://example.test/safe",
                        "public_status": "public_domain",
                    }
                )
                writer.writerow(
                    {
                        "doc_id": "unsafe_doc",
                        "path": "sources/unsafe.pdf",
                        "source_url": "https://example.test/unsafe",
                        "public_status": "rights_uncertain",
                    }
                )

            image_dir = root / "derived" / "pages_300dpi" / "safe_doc"
            image_dir.mkdir(parents=True)
            page = Image.new("RGB", (100, 100), "white")
            draw = ImageDraw.Draw(page)
            draw.line((10, 20, 30, 20), fill="black", width=3)
            draw.line((10, 50, 30, 50), fill="black", width=3)
            draw.line((40, 20, 60, 20), fill="black", width=3)
            draw.line((65, 20, 85, 20), fill="black", width=3)
            page.save(image_dir / "page_000.png")

            def candidate(candidate_id: str, bbox: list[int], **updates: object) -> dict:
                row = {
                    "candidate_id": candidate_id,
                    "source_candidate_id": "pcb_001",
                    "doc_id": "safe_doc",
                    "version_id": "v1",
                    "page_index": 0,
                    "bbox": bbox,
                    "target_text": candidate_id.upper(),
                    "raw_text": candidate_id.upper(),
                    "category": "pin_label",
                    "image_path": "derived/pages_300dpi/safe_doc/page_000.png",
                    "review_status": "needs_review",
                }
                row.update(updates)
                return row

            existing = candidate("old_capacity", [40, 10, 60, 30])
            write_jsonl(root / "existing.jsonl", [existing])
            capacity_report = {
                "cohorts": [{"phase": "future", "name": "existing", "path": "existing.jsonl"}]
            }
            (root / "capacity.json").write_text(json.dumps(capacity_report), encoding="utf-8")
            split_plan = {
                "valid": True,
                "reservations": [{"task": "microtext", "unit_id": "safe_doc", "split": "test"}],
            }
            (root / "split.json").write_text(json.dumps(split_plan), encoding="utf-8")

            rows = [
                candidate("cand_new", [10, 10, 30, 30]),
                candidate("cand_near_input", [12, 10, 32, 30], raw_text="different"),
                candidate("cand_dup", [10, 10, 30, 30]),
                candidate("cand_second", [10, 40, 30, 60]),
                candidate("cand_existing", [40, 10, 60, 30]),
                candidate("cand_near_existing", [42, 10, 62, 30]),
                candidate("cand_unsafe", [10, 10, 30, 30], doc_id="unsafe_doc"),
                candidate("cand_missing", [10, 10, 30, 30], image_path="missing.png"),
                candidate("cand_unknown", [65, 10, 85, 30], version_id="unknown"),
                candidate("cand_oob", [90, 90, 120, 120]),
                candidate(
                    "cand_svg_coordinate",
                    [65, 10, 85, 30],
                    target_text='1.587500"',
                    raw_text='<tspan x="0" y="1.587500">BMEx80 Digital Sensor</tspan>',
                ),
                candidate(
                    "cand_upstream_hold",
                    [65, 10, 85, 30],
                    unstaged_capacity_tier="machine_semantic_hold",
                    unstaged_capacity_tier_reasons=["domain_category_mismatch"],
                ),
                candidate(
                    "cand_upstream_manual",
                    [65, 10, 85, 30],
                    unstaged_capacity_tier="manual_transcription_or_taxonomy",
                    unstaged_capacity_tier_reasons=["unresolved_version"],
                ),
            ]
            write_jsonl(root / "candidates.jsonl", rows)

            selected, held, report = build_reservoir(
                root=root,
                input_path=Path("candidates.jsonl"),
                capacity_report_path=Path("capacity.json"),
                split_plan_path=Path("split.json"),
                target_rows=10,
                max_per_doc=10,
                max_per_doc_category=10,
                date_label="test",
            )

            self.assertEqual({row["candidate_id"] for row in selected}, {"cand_new", "cand_second"})
            self.assertEqual(len(held), 11)
            self.assertTrue(report["valid"])
            self.assertEqual(report["selected_splits"], {"test": 2})
            self.assertTrue(report["checks"]["input_conserved"])
            self.assertTrue(report["checks"]["selected_aliases_unique"])
            self.assertTrue(all(row["safe_to_merge_gold"] is False for row in selected))
            self.assertEqual(
                report["held_reasons"],
                {
                    "bbox_out_of_bounds": 1,
                    "duplicate_input_near_region": 1,
                    "duplicate_input_region": 1,
                    "existing_gold_review_or_capacity_near_overlap": 1,
                    "existing_gold_review_or_capacity_overlap": 1,
                    "missing_image_file": 1,
                    "source_not_paper_ready": 1,
                    "textlayer_target_not_in_visible_text": 1,
                    "unresolved_version": 1,
                    "upstream_manual_transcription_or_taxonomy": 1,
                    "upstream_machine_semantic_hold": 1,
                },
            )

    def test_enforces_document_cap_deterministically(self) -> None:
        from tools.build_v2_expansion_reservoir import select_source_balanced

        rows = [
            {
                "candidate_id": f"cand_{index}",
                "doc_id": "doc",
                "page_index": 0,
                "bbox": [index * 10, 0, index * 10 + 5, 5],
                "category": "pin_label",
            }
            for index in range(4)
        ]
        selected, held = select_source_balanced(rows, target_rows=10, max_per_doc=2, max_per_doc_category=10)
        self.assertEqual([row["candidate_id"] for row in selected], ["cand_0", "cand_1"])
        self.assertEqual(len(held), 2)
        self.assertTrue(all(row["reservoir_hold_reason"] == "per_doc_cap" for row in held))


if __name__ == "__main__":
    unittest.main()
