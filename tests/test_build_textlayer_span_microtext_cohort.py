from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image

from tools import build_textlayer_span_microtext_cohort as cohort


class BuildTextlayerSpanMicrotextCohortTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        pdf_path = root / "source.pdf"
        with fitz.open() as document:
            document.new_page(width=100, height=50)
            document.save(pdf_path)
        textlayer_dir = root / "textlayer"
        pages_dir = root / "pages"
        textlayer_dir.mkdir()
        pages_dir.mkdir()
        (textlayer_dir / "page_000.json").write_text(
            json.dumps(
                {
                    "doc_page": 0,
                    "spans": [
                        {"page_index": 0, "text": "PI", "bbox": [10.0, 5.0, 20.0, 10.0]},
                        {"page_index": 0, "text": "P2", "bbox": [30.0, 5.0, 40.0, 10.0]},
                        {"page_index": 0, "text": "Drive", "bbox": [10.0, 20.0, 30.0, 25.0]},
                        {"page_index": 0, "text": "shaft", "bbox": [31.0, 20.0, 50.0, 25.0]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        Image.new("RGB", (200, 100), "white").save(pages_dir / "page_000.png")
        return pdf_path, textlayer_dir, pages_dir

    def payload(self) -> dict[str, object]:
        return {
            "doc_id": "demo_doc",
            "version_id": "demo_v1",
            "source_candidate_id": "pid_999",
            "same_model_id": "demo_model",
            "source_url": "https://example.test/source",
            "selections": [
                {
                    "page_index": 0,
                    "source_text": "PI",
                    "target_text": "P1",
                    "category": "instrument_tag",
                    "bbox_pdf": [10.0, 5.0, 20.0, 10.0],
                }
            ],
        }

    def test_builds_corrected_review_only_row_and_scaled_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path, textlayer_dir, pages_dir = self.make_fixture(root)
            rows, report = cohort.build_cohort(
                root,
                pdf_path,
                textlayer_dir,
                pages_dir,
                self.payload(),
                padding_x=2,
                padding_y=1,
                tolerance=0.01,
            )
            self.assertEqual(1, len(rows))
            self.assertEqual("PI", rows[0]["raw_text"])
            self.assertEqual("P1", rows[0]["target_text"])
            self.assertEqual([18, 9, 42, 21], rows[0]["bbox"])
            self.assertEqual("needs_review", rows[0]["review_status"])
            self.assertFalse(rows[0]["safe_to_merge_gold"])
            self.assertEqual(0, report["active_gold_rows_modified"])

    def test_missing_exact_span_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path, textlayer_dir, pages_dir = self.make_fixture(root)
            payload = self.payload()
            payload["selections"][0]["bbox_pdf"] = [11.0, 5.0, 20.0, 10.0]
            with self.assertRaisesRegex(ValueError, "span selection failed"):
                cohort.build_cohort(
                    root,
                    pdf_path,
                    textlayer_dir,
                    pages_dir,
                    payload,
                    padding_x=2,
                    padding_y=1,
                    tolerance=0.01,
                )

    def test_combines_multiple_exact_spans_into_one_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path, textlayer_dir, pages_dir = self.make_fixture(root)
            payload = self.payload()
            payload["selections"] = [
                {
                    "page_index": 0,
                    "spans": [
                        {
                            "source_text": "Drive",
                            "bbox_pdf": [10.0, 20.0, 30.0, 25.0],
                        },
                        {
                            "source_text": "shaft",
                            "bbox_pdf": [31.0, 20.0, 50.0, 25.0],
                        },
                    ],
                    "target_text": "Drive shaft",
                    "category": "equipment_tag",
                }
            ]
            rows, report = cohort.build_cohort(
                root,
                pdf_path,
                textlayer_dir,
                pages_dir,
                payload,
                padding_x=2,
                padding_y=1,
                tolerance=0.01,
            )
            self.assertEqual(1, len(rows))
            self.assertEqual("Drive shaft", rows[0]["raw_text"])
            self.assertEqual([18, 39, 102, 51], rows[0]["bbox"])
            self.assertEqual("equipment_tag", rows[0]["category"])
            self.assertEqual({"equipment_tag": 1}, report["by_category"])

    def test_duplicate_combined_span_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path, textlayer_dir, pages_dir = self.make_fixture(root)
            payload = self.payload()
            repeated = {
                "source_text": "Drive",
                "bbox_pdf": [10.0, 20.0, 30.0, 25.0],
            }
            payload["selections"] = [
                {
                    "page_index": 0,
                    "spans": [repeated, repeated],
                    "target_text": "Drive",
                    "category": "equipment_tag",
                }
            ]
            with self.assertRaisesRegex(ValueError, "duplicate span"):
                cohort.build_cohort(
                    root,
                    pdf_path,
                    textlayer_dir,
                    pages_dir,
                    payload,
                    padding_x=2,
                    padding_y=1,
                    tolerance=0.01,
                )


if __name__ == "__main__":
    unittest.main()
