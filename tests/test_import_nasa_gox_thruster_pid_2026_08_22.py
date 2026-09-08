from __future__ import annotations

import unittest

from tools import import_nasa_gox_thruster_pid_2026_08_22 as importer


class NasaGoxThrusterPidImportTests(unittest.TestCase):
    def valid_metadata(self) -> dict:
        return {
            "id": int(importer.NTRS_ID),
            "distribution": "PUBLIC",
            "copyright": {
                "determinationType": "PUBLIC_USE_PERMITTED",
                "containsThirdPartyMaterial": False,
                "thirdPartyPermissionsProduced": False,
            },
            "exportControl": {"isExportControl": "NO", "ear": "NO", "itar": "NO"},
            "downloads": [
                {"mimetype": "application/pdf", "name": f"{importer.NTRS_ID}.pdf"}
            ],
        }

    def test_metadata_gate_accepts_exact_public_use_posture(self) -> None:
        importer.verify_metadata(self.valid_metadata())

    def test_metadata_gate_fails_closed(self) -> None:
        cases = (
            (None, "distribution", "LIMITED"),
            ("copyright", "determinationType", "OTHER"),
            ("copyright", "containsThirdPartyMaterial", True),
            ("copyright", "thirdPartyPermissionsProduced", True),
            ("exportControl", "isExportControl", "YES"),
            ("exportControl", "ear", "YES"),
        )
        for section, key, value in cases:
            metadata = self.valid_metadata()
            target = metadata if section is None else metadata[section]
            target[key] = value
            with self.subTest(section=section, key=key):
                with self.assertRaises(ValueError):
                    importer.verify_metadata(metadata)

    def test_metadata_gate_requires_official_pdf(self) -> None:
        metadata = self.valid_metadata()
        metadata["downloads"] = []
        with self.assertRaises(ValueError):
            importer.verify_metadata(metadata)

    def test_candidate_spec_is_review_only(self) -> None:
        spec = importer.candidate_spec()
        self.assertEqual(spec["candidate"]["candidate_id"], "pid_086")
        self.assertEqual(spec["doc_ids"], [importer.DOC_ID])
        self.assertEqual(spec["validation"]["release_posture"], "release_candidate")
        self.assertEqual(importer.SELECTED_PAGE_1BASED, 181)
        self.assertEqual(len(importer.PDF_SHA256), 64)
        self.assertEqual(len(importer.METADATA_SHA256), 64)


if __name__ == "__main__":
    unittest.main()
