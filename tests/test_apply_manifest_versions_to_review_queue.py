import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "apply_manifest_versions_to_review_queue.py"
SPEC = importlib.util.spec_from_file_location("apply_manifest_versions_to_review_queue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ApplyManifestVersionsToReviewQueueTests(unittest.TestCase):
    def test_normalizes_release_labels(self):
        self.assertEqual(MODULE.normalize_version_id("V1.0"), "v1_0")
        self.assertEqual(MODULE.normalize_version_id(" Rev C "), "rev_c")

    def test_enriches_unknown_and_preserves_known_versions(self):
        rows = [
            {"candidate_id": "a", "doc_id": "board-v1", "version_id": "unknown"},
            {"candidate_id": "b", "doc_id": "board-v2", "version_id": "release_7"},
        ]
        manifest = [
            {
                "type": "doc",
                "doc_id": "board-v1",
                "version": {"board_revision": "V1.0"},
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "v1_0")
        self.assertEqual(output[0]["machine_version_source"], "manifest.version.board_revision")
        self.assertEqual(output[1]["version_id"], "release_7")
        self.assertEqual(report["totals"]["enriched_rows"], 1)
        self.assertEqual(report["totals"]["preserved_rows"], 1)
        self.assertTrue(report["valid"])

    def test_reports_unresolved_documents(self):
        output, report = MODULE.enrich_rows(
            [{"candidate_id": "a", "doc_id": "missing", "version_id": "unknown"}],
            [],
        )

        self.assertEqual(output[0]["version_id"], "unknown")
        self.assertEqual(report["unresolved_documents"], {"missing": 1})
        self.assertFalse(report["valid"])

    def test_enriches_from_manifest_rev_alias(self):
        rows = [{"candidate_id": "a", "doc_id": "drawing", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "drawing",
                "version": {"rev": "Hunt"},
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "hunt")
        self.assertEqual(output[0]["machine_version_source"], "manifest.version.rev")
        self.assertTrue(report["valid"])

    def test_enriches_from_manifest_schematic_revision(self):
        rows = [{"candidate_id": "a", "doc_id": "schematic", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "schematic",
                "version": {"sch_rev": "C3"},
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "c3")
        self.assertEqual(output[0]["machine_version_source"], "manifest.version.sch_rev")
        self.assertTrue(report["valid"])

    def test_enriches_historical_document_from_publication_year(self):
        rows = [{"candidate_id": "a", "doc_id": "drawing-book", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "drawing-book",
                "version": {"publication_year": "1898"},
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "1898")
        self.assertEqual(
            output[0]["machine_version_source"],
            "manifest.version.publication_year",
        )
        self.assertTrue(report["valid"])

    def test_enriches_official_standard_from_edition(self):
        rows = [{"candidate_id": "a", "doc_id": "standard-sheet", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "standard-sheet",
                "version": {"standard": "W101-1", "edition": "FP-24"},
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "fp_24")
        self.assertEqual(output[0]["machine_version_source"], "manifest.version.edition")
        self.assertTrue(report["valid"])

    def test_enriches_official_standard_code_when_no_edition_exists(self):
        rows = [{"candidate_id": "a", "doc_id": "standard-sheet", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "standard-sheet",
                "version": {
                    "standard": "PA-064",
                    "catalog": "PA NRCS Standard Drawings",
                    "retrieved": "2026-08-11-wave117",
                },
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "pa_064")
        self.assertEqual(output[0]["machine_version_source"], "manifest.version.standard")
        self.assertTrue(report["valid"])

    def test_enriches_nasa_document_from_stable_ntrs_id(self):
        rows = [{"candidate_id": "a", "doc_id": "nasa-report", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "nasa-report",
                "version": {
                    "ntrs_id": "20200004338",
                    "report_number": "NASA/TM-2020-220344",
                    "receipt_date": "2026-08-17-wave200",
                },
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "20200004338")
        self.assertEqual(output[0]["machine_version_source"], "manifest.version.ntrs_id")
        self.assertTrue(report["valid"])

    def test_uses_manifest_sha256_when_no_semantic_version_exists(self):
        digest = "ab" * 32
        rows = [{"candidate_id": "a", "doc_id": "drawing", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "drawing",
                "version": {"imported": "2026-06-16"},
                "sha256": digest,
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], f"sha256_{digest}")
        self.assertEqual(output[0]["machine_version_source"], "manifest.sha256")
        self.assertEqual(output[0]["machine_version_manifest_sha256"], digest)
        self.assertEqual(report["resolution_sources"], {"manifest.sha256": 1})
        self.assertTrue(report["valid"])

    def test_invalid_manifest_sha256_does_not_resolve_version(self):
        rows = [{"candidate_id": "a", "doc_id": "drawing", "version_id": "unknown"}]
        manifest = [
            {
                "type": "doc",
                "doc_id": "drawing",
                "version": {"imported": "2026-06-16"},
                "sha256": "not-a-sha256",
            }
        ]

        output, report = MODULE.enrich_rows(rows, manifest)

        self.assertEqual(output[0]["version_id"], "unknown")
        self.assertFalse(report["valid"])

    def test_filters_only_unresolved_version_capacity_tier(self):
        selected, excluded = MODULE.unresolved_version_tier_rows(
            [
                {
                    "candidate_id": "a",
                    "unstaged_capacity_tier_reasons": ["unresolved_version"],
                },
                {
                    "candidate_id": "b",
                    "unstaged_capacity_tier_reasons": ["missing_proposed_text"],
                },
                {
                    "candidate_id": "c",
                    "unstaged_capacity_tier_reasons": [
                        "unresolved_version",
                        "secondary_reason",
                    ],
                },
            ]
        )

        self.assertEqual([row["candidate_id"] for row in selected], ["a", "c"])
        self.assertEqual(excluded, 1)

    def test_uses_one_known_active_gold_version_as_fallback(self):
        rows = [{"candidate_id": "a", "doc_id": "board", "version_id": "unknown"}]
        active_gold = [
            {"item_id": "gold-a", "doc_id": "board", "version_id": "Rev D"},
            {"item_id": "gold-b", "doc_id": "board", "version_id": "rev-d"},
        ]

        output, report = MODULE.enrich_rows(rows, [], active_gold)

        self.assertEqual(output[0]["version_id"], "rev_d")
        self.assertEqual(output[0]["machine_version_source"], "active_gold.unique_version_id")
        self.assertEqual(report["totals"]["enriched_rows"], 1)
        self.assertTrue(report["valid"])

    def test_conflicting_active_gold_versions_do_not_resolve(self):
        rows = [{"candidate_id": "a", "doc_id": "board", "version_id": "unknown"}]
        active_gold = [
            {"item_id": "gold-a", "doc_id": "board", "version_id": "rev_a"},
            {"item_id": "gold-b", "doc_id": "board", "version_id": "rev_b"},
        ]

        output, report = MODULE.enrich_rows(rows, [], active_gold)

        self.assertEqual(output[0]["version_id"], "unknown")
        self.assertFalse(report["valid"])

    def test_partition_marks_unresolved_rows_as_machine_held(self):
        rows = [
            {"candidate_id": "known", "version_id": "rev_a"},
            {"candidate_id": "unknown", "version_id": "unknown"},
        ]

        resolved, unresolved = MODULE.partition_rows_by_version(rows)

        self.assertEqual([row["candidate_id"] for row in resolved], ["known"])
        self.assertEqual([row["candidate_id"] for row in unresolved], ["unknown"])
        self.assertEqual(unresolved[0]["review_status"], "machine_held")
        self.assertEqual(unresolved[0]["machine_hold_reason"], "unresolved_version_id")


if __name__ == "__main__":
    unittest.main()
