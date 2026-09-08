from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import import_nasa_review_sources as importer


class NasaReviewSourceBatchTests(unittest.TestCase):
    def source(self) -> dict:
        return {
            "ntrs_id": "123",
            "candidate_id": "pid_999",
            "doc_id": "nasa_test",
            "version_id": "nasa_test_v1",
            "domain": "pid",
            "same_model_id": "nasa_test_family",
            "doc_type": "federal_pid_report_pdf",
            "source_family": "nasa_test_family",
            "likely_asset_type": "federal_pid_report_pdf",
            "pdf_input": "source.pdf",
            "metadata_input": "source.json",
            "pdf_sha256": "a" * 64,
            "metadata_sha256": "b" * 64,
            "pdf_pages": 1,
            "selected_pages_1based": [1],
            "expected_determination_type": "GOV_PUBLIC_USE_PERMITTED",
            "public_status": "government_public_use_permitted_nasa_candidate",
            "source_rel_dir": "microtext/docs/test",
            "source_filename": "source.pdf",
            "metadata_filename": "metadata.json",
            "path_hint": "candidate/pid/test",
            "validation_next_action": "mine_guarded_pid_labels",
            "candidate_notes": "Review-only test source.",
        }

    def metadata(self) -> dict:
        return {
            "id": 123,
            "distribution": "PUBLIC",
            "copyright": {
                "determinationType": "GOV_PUBLIC_USE_PERMITTED",
                "containsThirdPartyMaterial": False,
                "thirdPartyPermissionsProduced": False,
            },
            "exportControl": {"isExportControl": "NO", "ear": "NO", "itar": "NO"},
            "downloads": [{"mimetype": "application/pdf", "name": "123.pdf"}],
        }

    def test_metadata_gate_accepts_exact_public_use_posture(self) -> None:
        importer.verify_metadata(self.metadata(), self.source())

    def test_metadata_gate_accepts_omitted_irrelevant_permissions_field(self) -> None:
        metadata = self.metadata()
        metadata["copyright"].pop("thirdPartyPermissionsProduced")
        importer.verify_metadata(metadata, self.source())

    def test_metadata_gate_fails_closed(self) -> None:
        mutations = (
            ("distribution", "LIMITED"),
            ("determinationType", "OTHER"),
            ("containsThirdPartyMaterial", True),
            ("thirdPartyPermissionsProduced", True),
            ("isExportControl", "YES"),
            ("ear", "YES"),
            ("itar", "YES"),
        )
        for key, value in mutations:
            metadata = self.metadata()
            if key in metadata:
                metadata[key] = value
            elif key in metadata["copyright"]:
                metadata["copyright"][key] = value
            else:
                metadata["exportControl"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                importer.verify_metadata(metadata, self.source())

    def test_metadata_gate_requires_official_pdf(self) -> None:
        metadata = self.metadata()
        metadata["downloads"] = []
        with self.assertRaises(ValueError):
            importer.verify_metadata(metadata, self.source())

    def test_spec_rejects_duplicate_candidate_ids(self) -> None:
        source = self.source()
        payload = {
            "wave": "wave_test",
            "import_date": "2026-08-22",
            "sources": [source, {**source, "ntrs_id": "124", "doc_id": "nasa_test_2"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate candidate_id"):
                importer.load_spec(path)

    def test_candidate_spec_is_review_only_registration(self) -> None:
        spec = importer.candidate_spec(self.source())
        self.assertEqual(spec["candidate"]["candidate_id"], "pid_999")
        self.assertEqual(spec["doc_ids"], ["nasa_test"])
        self.assertEqual(spec["validation"]["release_posture"], "release_candidate")
        self.assertNotIn("safe_to_merge_gold", spec)

    def test_spec_accepts_supported_page_rotation(self) -> None:
        source = {**self.source(), "rotate_cw_degrees": 90}
        payload = {
            "wave": "wave_test",
            "import_date": "2026-08-31",
            "sources": [source],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = importer.load_spec(path)
        self.assertEqual(loaded["sources"][0]["rotate_cw_degrees"], 90)

    def test_spec_rejects_unsupported_page_rotation(self) -> None:
        source = {**self.source(), "rotate_cw_degrees": 45}
        payload = {
            "wave": "wave_test",
            "import_date": "2026-08-31",
            "sources": [source],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "rotate_cw_degrees"):
                importer.load_spec(path)

    def test_render_and_extract_rotates_pixels_and_text_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "source.pdf"
            document = importer.fitz.open()
            page = document.new_page(width=200, height=100)
            page.insert_text((20, 30), "P-101")
            document.save(pdf_path)
            document.close()

            counts = importer.render_and_extract(
                pdf_path,
                [1],
                root / "pages",
                root / "textlayer",
                rotate_cw_degrees=90,
            )
            textlayer = json.loads(
                (root / "textlayer" / "page_000.json").read_text(encoding="utf-8")
            )
            image = importer.fitz.Pixmap(root / "pages" / "page_000.png")

        self.assertEqual(counts, {0: 1})
        self.assertEqual(textlayer["rotate_cw_degrees"], 90)
        self.assertEqual(textlayer["page_size_points"], [100.0, 200.0])
        self.assertEqual((image.width, image.height), (417, 834))
        bbox = textlayer["spans"][0]["bbox"]
        self.assertGreaterEqual(bbox[0], 60)
        self.assertLessEqual(bbox[1], 20)


if __name__ == "__main__":
    unittest.main()
