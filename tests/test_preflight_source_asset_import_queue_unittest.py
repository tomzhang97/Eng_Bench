from __future__ import annotations

import unittest

from tools import preflight_source_asset_import_queue


class CommonsTitleFromUrlTests(unittest.TestCase):
    def test_commons_title_preserves_semicolon_path_parameters(self) -> None:
        url = (
            "https://commons.wikimedia.org/wiki/"
            "File:Cyclopedia_of_locomotive_engineering,_with_examination_questions_and_answers;"
            "_a_practical_manual_on_the_construction_care_and_management_of_modern_locomotives_"
            "(1916)_(14590044400).jpg"
        )

        title = preflight_source_asset_import_queue.commons_title_from_url(url)

        self.assertIn("; a practical manual", title)
        self.assertTrue(title.endswith("(14590044400).jpg"))

    def test_preflight_preserves_provenance_metadata(self) -> None:
        row = {
            "queue_rank": "1",
            "candidate_id": "civil_018",
            "domain": "civil",
            "asset_kind": "pdf",
            "import_action": "download_hash_render_textlayer",
            "source_url": "https://example.test/index",
            "page_url": "https://example.test/index",
            "direct_asset_url": "https://example.test/drawing.pdf",
            "proposed_doc_id": "drawing",
            "proposed_local_path": "docs/drawing.pdf",
            "rights_capture": "public_domain_us_federal_candidate",
            "review_gate": "review_packet_required_before_gold",
            "public_status": "public_domain_us_federal_candidate",
            "license_note": "Federal public-domain basis recorded.",
            "rights_evidence_url": "https://example.test/rights",
            "rights_evidence_path": "docs/RIGHTS.md",
            "same_model_id": "standard_drawings",
            "doc_type": "federal_standard_drawing_pdf",
            "version_json": '{"standard":"W101-1"}',
            "page_selection": "all",
            "notes": "Review only.",
        }

        def probe(url: str, timeout: int) -> preflight_source_asset_import_queue.ProbeResult:
            del timeout
            return preflight_source_asset_import_queue.ProbeResult(
                True, url, url, 200, "application/pdf", 123, ""
            )

        result = preflight_source_asset_import_queue.preflight_row(
            row,
            timeout=5,
            no_network=False,
            probe=probe,
            commons_resolver=preflight_source_asset_import_queue.resolve_commons_file,
        )
        self.assertTrue(result["ready_for_intake"])
        self.assertEqual(result["public_status"], "public_domain_us_federal_candidate")
        self.assertEqual(result["version_json"], '{"standard":"W101-1"}')


if __name__ == "__main__":
    unittest.main()
