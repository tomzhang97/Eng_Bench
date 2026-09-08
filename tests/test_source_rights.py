import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from source_rights import is_release_safe_status, rights_blocker


class SourceRightsTest(unittest.TestCase):
    def test_accepts_explicit_redistribution_statuses(self) -> None:
        for status in (
            "public_domain_commons_candidate",
            "cc_by_sa_4_0_documentation_candidate",
            "cc0_1_0_commons_verified",
            "gpl_3_0_open_source_candidate",
            "apache_2_0_open_hardware_candidate",
            "cern_ohl_1_2_open_hardware_candidate",
            "tapr_ohl_1_0_open_hardware_candidate",
            "public_agency_source_url_terms_sha256",
        ):
            with self.subTest(status=status):
                self.assertTrue(is_release_safe_status(status))
                self.assertEqual(rights_blocker(status), "")

    def test_public_availability_without_license_is_held(self) -> None:
        for status in (
            "public_candidate",
            "misc_public_candidate",
            "public_vendor_docs_candidate",
            "public_vendor_datasheet_candidate",
            "public_municipal_pdf_candidate",
            "open_hardware_candidate",
        ):
            with self.subTest(status=status):
                self.assertFalse(is_release_safe_status(status))
                self.assertEqual(rights_blocker(status), "license_evidence_missing")

    def test_restrictive_creative_commons_and_existing_blockers_are_held(self) -> None:
        self.assertEqual(rights_blocker("cc_by_nd_4_0_vendor_docs"), "cc_by_nd")
        self.assertEqual(rights_blocker("cc_by_nc_sa_reference_candidate"), "cc_by_nc")
        self.assertEqual(
            rights_blocker("attribution-noncommercial-sharealike"),
            "attribution-noncommercial",
        )
        self.assertEqual(rights_blocker("rights_uncertain"), "rights_uncertain")
        self.assertEqual(rights_blocker(""), "missing_public_status")


if __name__ == "__main__":
    unittest.main()
