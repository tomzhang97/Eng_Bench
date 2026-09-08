from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_unused_release_safe_sources import build_report


class UnusedReleaseSafeSourceAuditTest(unittest.TestCase):
    def test_only_genuinely_unused_source_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "visualdiff" / "docs" / "family" / "source_bundle.json"
            bundle.parent.mkdir(parents=True)
            bundle.write_text(
                json.dumps(
                    {
                        "pairs": [
                            {
                                "pair_id": "vdiff__family__a__to__b",
                                "from_doc_id": "revision-a",
                                "to_doc_id": "revision-b",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            base = {
                "domain": "mechanical_cad",
                "public_status": "public_domain_candidate",
                "rendered_pages": 1,
                "textlayer_spans": 10,
                "mineable_candidates": 5,
                "gold_rows": 0,
                "staged_future_rows": 0,
                "review_rows": 0,
                "open_review_rows": 0,
                "conversion_exhaustion_status": "",
                "duplicate_payload_alias": False,
            }
            rows = [
                {**base, "doc_id": "fresh", "task": "microtext"},
                {**base, "doc_id": "revision-a", "task": "visualdiff"},
                {**base, "doc_id": "blocked", "task": "microtext", "public_status": "unknown"},
                {**base, "doc_id": "exhausted", "task": "microtext", "conversion_exhaustion_status": "machine_exhausted_no_candidate"},
            ]
            readiness = root / "readiness.json"
            readiness.write_text(json.dumps({"local_sources": rows}), encoding="utf-8")
            report = build_report(root, readiness, date_label="test")
            self.assertEqual(report["actionable_source_count"], 1)
            self.assertEqual(report["actionable_sources"][0]["doc_id"], "fresh")
            pair_row = next(row for row in report["sources"] if row["doc_id"] == "revision-a")
            self.assertIn("represented_by_registered_revision_pair", pair_row["reason_codes"])


if __name__ == "__main__":
    unittest.main()
