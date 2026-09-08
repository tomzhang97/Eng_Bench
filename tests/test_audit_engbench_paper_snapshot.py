import json
import tempfile
import unittest
from pathlib import Path

from tools import audit_engbench_paper_snapshot as audit


def table_report() -> dict:
    return {
        "date_label": "2026-fixture",
        "active_gold": {
            "rows": 10,
            "task_counts": {"microtext": 6, "visualdiff": 4},
            "split_counts": {"train": 5, "dev": 2, "test": 3},
        },
        "provenance": {"totals": {"active_source_docs": 2}},
        "formal_gate_status": {
            "passed": 3,
            "total": 9,
            "gates": {
                "gold_source_docs": {"current": 1},
                "paper_ready_provenance": {"current": "1/2 paper-ready"},
                "visualdiff_revision_families": {"current": 1},
                "baselines_or_external_submissions": {"current": 4},
                "leaderboard_infrastructure": {"current": 7},
                "release_safe_inventory_docs": {"current": 3},
                "human_agreement_audit": {"current": "0/5 complete"},
            },
        },
        "staged_capacity_projection": {
            "targets": {
                "rows": {"all_staged_upper_bound": 30},
                "test_rows": {"explicit_all_staged_upper_bound": 8},
            },
            "row_target_projection": {"additional_canonical_non_pin_rows_needed": 12},
        },
    }


def paper_text(release: str = "false", active_rows: str = "10") -> str:
    values = {
        "SnapshotLabel": "2026-fixture", "ReleaseClaimReady": release,
        "ActiveRows": active_rows, "MicrotextRows": "6", "VisualdiffRows": "4",
        "TrainRows": "5", "DevRows": "2", "TestRows": "3", "ActiveSourceDocs": "2",
        "PaperReadyPayloads": "1", "RevisionFamilies": "1", "FormalGatesPassed": "3",
        "PaperReadyActiveDocs": "1", "BaselineCount": "4",
        "LeaderboardInfrastructure": "7", "ReleaseSafeInventory": "3",
        "FormalGatesTotal": "9", "AgreementComplete": "0", "AgreementTarget": "5",
        "StagedUpperRows": "30", "StagedUpperTestRows": "8",
        "CanonicalNonPinGap": "12",
    }
    lines = [f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in values.items()]
    lines.append("NOT RELEASE CLAIM READY")
    lines.extend(f"\\section{{{section}}}" for section in sorted(audit.REQUIRED_SECTIONS))
    lines.extend(f"\\cite{{{citation}}}" for citation in sorted(audit.REQUIRED_CITATIONS))
    return "\n".join(lines)


class PaperSnapshotAuditTests(unittest.TestCase):
    def run_audit(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "paper.tex"
            tables = root / "tables.json"
            paper.write_text(text, encoding="utf-8")
            tables.write_text(json.dumps(table_report()), encoding="utf-8")
            return audit.build_report(paper, tables)

    def test_valid_draft_matches_incomplete_snapshot(self):
        report = self.run_audit(paper_text())
        self.assertTrue(report["valid"])
        self.assertEqual(report["formal_gates"], "3/9")
        self.assertFalse(report["release_claim_ready"])

    def test_detects_drift_and_premature_release_claim(self):
        report = self.run_audit(paper_text(release="true", active_rows="11"))
        issues = {issue["issue"] for issue in report["issues"]}
        self.assertIn("macro_mismatch", issues)
        self.assertIn("premature_release_claim", issues)


if __name__ == "__main__":
    unittest.main()
