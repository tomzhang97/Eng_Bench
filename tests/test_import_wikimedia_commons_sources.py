from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageStat

from tools import import_wikimedia_commons_sources as commons_import


class ImportWikimediaCommonsSourcesTest(unittest.TestCase):
    def test_license_allowlist_rejects_noncommercial_and_no_derivatives(self) -> None:
        self.assertEqual(commons_import.license_tier("CC0"), "cc0_commons_candidate")
        self.assertEqual(
            commons_import.license_tier("Public domain"),
            "public_domain_commons_candidate",
        )
        self.assertEqual(
            commons_import.license_tier("CC BY-SA 4.0"),
            "cc_by_sa_4_0_commons_candidate",
        )
        self.assertIsNone(commons_import.license_tier("CC BY-NC 4.0"))
        self.assertIsNone(commons_import.license_tier("CC BY-ND 3.0"))
        self.assertIsNone(commons_import.license_tier("All rights reserved"))

    def test_slugify_title_is_unicode_stable(self) -> None:
        self.assertEqual(
            commons_import.slugify_title("File:Schéma P&ID1.jpg"),
            "wikimedia_schema_p_id1",
        )
        self.assertEqual(
            commons_import.normalized_title("Schéma_P&ID1.jpg"),
            commons_import.normalized_title("File:Schéma P&ID1.jpg"),
        )

    def test_source_plan_rejects_duplicate_document_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sources.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["candidate_id", "title", "domain", "same_model_id"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"candidate_id": "pid_1", "title": "File:A-B.svg", "domain": "pid", "same_model_id": "a"},
                        {"candidate_id": "pid_2", "title": "File:A B.svg", "domain": "pid", "same_model_id": "b"},
                    ]
                )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                commons_import.read_source_plan(path)

    def test_existing_payload_hashes_reads_manifest_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sha256 = hashlib.sha256(b"payload").hexdigest()
            (root / "manifest.jsonl").write_text(
                json.dumps({"type": "doc", "doc_id": "existing", "sha256": sha256}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(commons_import.existing_payload_hashes(root), {sha256: ["existing"]})

    def test_file_sha1_matches_commons_style_hex_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.svg"
            path.write_bytes(b"commons payload")
            self.assertEqual(
                commons_import.file_sha1(path),
                hashlib.sha1(b"commons payload").hexdigest(),
            )

    def test_pillow_image_stat_handles_large_render_without_python_pixel_loop(self) -> None:
        image = Image.new("L", (2048, 2048), 240)
        self.assertEqual(ImageStat.Stat(image).mean[0], 240.0)

    def test_textlayer_quality_rejects_degenerate_svg_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "textlayer.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"text": "bad", "bbox_px": [10, 10, 11, 11]}),
                        json.dumps({"text": "good", "bbox_px": [20, 20, 50, 40]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                commons_import.textlayer_quality(path),
                {"text_spans": 2, "nondegenerate_text_spans": 1},
            )

    def test_sparse_svg_override_is_explicit(self) -> None:
        values = {
            "suffix": ".svg",
            "mean_gray": 254.54,
            "black_fraction": 0.000576,
            "nondegenerate_text_spans": 7,
            "min_svg_spans": 5,
        }
        self.assertFalse(
            commons_import.sparse_svg_override_allowed(
                **values,
                allow_sparse_svg=False,
            )
        )
        self.assertTrue(
            commons_import.sparse_svg_override_allowed(
                **values,
                allow_sparse_svg=True,
            )
        )

    def test_sparse_svg_override_rejects_blank_page(self) -> None:
        self.assertFalse(
            commons_import.sparse_svg_override_allowed(
                suffix=".svg",
                mean_gray=255.0,
                black_fraction=0.0,
                nondegenerate_text_spans=7,
                min_svg_spans=5,
                allow_sparse_svg=True,
            )
        )

    def test_sparse_svg_override_rejects_textless_page(self) -> None:
        self.assertFalse(
            commons_import.sparse_svg_override_allowed(
                suffix=".svg",
                mean_gray=254.54,
                black_fraction=0.000576,
                nondegenerate_text_spans=0,
                min_svg_spans=0,
                allow_sparse_svg=True,
            )
        )

    def test_sparse_svg_override_rejects_raster_input(self) -> None:
        self.assertFalse(
            commons_import.sparse_svg_override_allowed(
                suffix=".png",
                mean_gray=254.54,
                black_fraction=0.000576,
                nondegenerate_text_spans=7,
                min_svg_spans=5,
                allow_sparse_svg=True,
            )
        )

    def test_inventory_upsert_replaces_placeholder_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory = root / "SOURCE_INVENTORY.csv"
            inventory.write_text(
                "doc_id,domain,task,public_status,source_path,rendered_pages\n"
                "diagram,unknown,microtext,unknown,,1\n"
                "other,pid,microtext,cc0,other.svg,1\n",
                encoding="utf-8",
            )

            commons_import.upsert_inventory(
                root,
                [
                    {
                        "doc_id": "diagram",
                        "domain": "pid",
                        "task": "microtext",
                        "public_status": "cc_by_sa_3_0_commons_candidate",
                        "source_path": "diagram.svg",
                        "rendered_pages": "1",
                    }
                ],
            )

            with inventory.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["doc_id"] for row in rows], ["diagram", "other"])
            self.assertEqual(rows[0]["domain"], "pid")
            self.assertEqual(rows[0]["source_path"], "diagram.svg")


if __name__ == "__main__":
    unittest.main()
