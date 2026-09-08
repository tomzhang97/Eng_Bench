from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import adopt_nrcs_nd_stockwater_members as adopt


class AdoptNrcsNdStockwaterMembersTests(unittest.TestCase):
    def test_adopts_verified_numbered_pdf(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            member_path = root / "member.pdf"
            member_path.write_bytes(b"%PDF-test")
            digest = hashlib.sha256(member_path.read_bytes()).hexdigest()
            parent = {
                "receipts": [
                    {
                        "status": "downloaded",
                        "candidate_id": "civil_027",
                        "domain": "civil",
                        "download_url": "https://example.test/source.zip",
                        "source_url": "https://example.test/catalog",
                        "version_json": "{\"archive_date\":\"2025-04-30\"}",
                    }
                ]
            }
            extracted = {
                "extract_receipts": [
                    {
                        "status": "extracted",
                        "member_path": "ND-100 Tank Detail.pdf",
                        "output_path": "member.pdf",
                        "sha256": digest,
                    }
                ]
            }

            receipts, issues = adopt.build_receipts(
                root,
                parent_report=parent,
                extract_report=extracted,
            )

            self.assertEqual(issues, [])
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["doc_id"], "nrcs_nd_stockwater_nd_100_tank_detail")
            self.assertEqual(receipts[0]["sha256"], digest)


if __name__ == "__main__":
    unittest.main()
