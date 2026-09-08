from __future__ import annotations

import unittest

from tools import import_nasa_delta3_pid_wave_2026_08_17 as importer


class NasaDelta3PidImportTests(unittest.TestCase):
    def valid_metadata(self) -> dict:
        return {
            "id": int(importer.NTRS_ID),
            "distribution": "PUBLIC",
            "copyright": {
                "determinationType": "GOV_PUBLIC_USE_PERMITTED",
                "containsThirdPartyMaterial": False,
                "thirdPartyPermissionsProduced": False,
            },
            "exportControl": {"isExportControl": "NO", "ear": "NO", "itar": "NO"},
        }

    def test_metadata_gate_accepts_exact_public_use_posture(self) -> None:
        importer.verify_metadata(self.valid_metadata())

    def test_metadata_gate_fails_closed(self) -> None:
        for section, key, value in (
            (None, "distribution", "LIMITED"),
            ("copyright", "determinationType", "OTHER"),
            ("copyright", "containsThirdPartyMaterial", True),
            ("copyright", "thirdPartyPermissionsProduced", True),
            ("exportControl", "isExportControl", "YES"),
        ):
            metadata = self.valid_metadata()
            target = metadata if section is None else metadata[section]
            target[key] = value
            with self.assertRaises(ValueError):
                importer.verify_metadata(metadata)

    def test_candidate_spec_is_review_only_source_registration(self) -> None:
        spec = importer.candidate_spec()
        self.assertEqual(spec["candidate"]["candidate_id"], "pid_084")
        self.assertEqual(spec["doc_ids"], [importer.DOC_ID])
        self.assertEqual(spec["validation"]["release_posture"], "release_candidate")
        self.assertEqual(len(importer.PDF_SHA256), 64)
        self.assertEqual(len(importer.METADATA_SHA256), 64)
        self.assertEqual(importer.SELECTED_PAGE_1BASED, 65)


if __name__ == "__main__":
    unittest.main()
