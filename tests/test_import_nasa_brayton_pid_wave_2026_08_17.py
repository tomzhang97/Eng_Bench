from __future__ import annotations

import unittest

from tools import import_nasa_brayton_pid_wave_2026_08_17 as importer


class NasaBraytonPidImportTests(unittest.TestCase):
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
        for mutation in (
            ("distribution", "LIMITED"),
            ("determinationType", "OTHER"),
            ("containsThirdPartyMaterial", True),
            ("thirdPartyPermissionsProduced", True),
            ("isExportControl", "YES"),
        ):
            metadata = self.valid_metadata()
            key, value = mutation
            if key in metadata:
                metadata[key] = value
            elif key in metadata["copyright"]:
                metadata["copyright"][key] = value
            else:
                metadata["exportControl"][key] = value
            with self.assertRaises(ValueError):
                importer.verify_metadata(metadata)

    def test_candidate_spec_is_review_only_source_registration(self) -> None:
        spec = importer.candidate_spec()
        self.assertEqual(spec["candidate"]["candidate_id"], "pid_083")
        self.assertEqual(spec["doc_ids"], [importer.DOC_ID])
        self.assertEqual(spec["validation"]["release_posture"], "release_candidate")
        self.assertEqual(len(importer.PDF_SHA256), 64)
        self.assertEqual(len(importer.METADATA_SHA256), 64)
        self.assertEqual(importer.SELECTED_PAGES_1BASED, (20, 21))


if __name__ == "__main__":
    unittest.main()
