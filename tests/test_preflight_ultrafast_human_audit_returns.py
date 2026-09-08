from __future__ import annotations

import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from tools import preflight_ultrafast_human_audit_returns as preflight


def primary(
    task: str,
    identifier: str,
    decision: str,
    index: int,
    **extra: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "reviewer_id": "primary_reviewer",
        "reviewer_role": "primary",
        "task_type": task,
        "primary_index": index,
        "decision": decision,
        "source_workbook": preflight.PRIMARY_WORKBOOK,
    }
    row["candidate_id" if task == "microtext" else "pair_id"] = identifier
    row.update(extra)
    return row


def auditor(
    task: str,
    identifier: str,
    decision: str,
    index: int,
    reviewer: str = "01",
) -> dict[str, object]:
    row: dict[str, object] = {
        "reviewer_id": f"auditor_{reviewer}",
        "reviewer_role": "auditor",
        "task_type": task,
        "primary_index": index,
        "decision": decision,
        "source_workbook": f"AUDITOR_{reviewer}_REVIEW_12.xlsx",
    }
    row["candidate_id" if task == "microtext" else "pair_id"] = identifier
    return row


class PreflightUltrafastHumanAuditReturnsTests(unittest.TestCase):
    def test_pixel_hash_ignores_lossless_png_compression(self) -> None:
        image = Image.new("RGB", (3, 2), (12, 34, 56))
        low = BytesIO()
        high = BytesIO()
        image.save(low, format="PNG", compress_level=0)
        image.save(high, format="PNG", compress_level=9)
        self.assertNotEqual(low.getvalue(), high.getvalue())
        self.assertEqual(
            preflight.image_pixel_hash(low.getvalue()),
            preflight.image_pixel_hash(high.getvalue()),
        )

    def test_primary_and_auditor_answers_match_question_semantics(self) -> None:
        self.assertEqual(
            preflight.primary_audit_answer(primary("microtext", "m1", "accepted", 1)),
            "yes",
        )
        self.assertEqual(
            preflight.primary_audit_answer(primary("microtext", "m1", "edited", 1)),
            "no",
        )
        self.assertEqual(
            preflight.auditor_audit_answer(auditor("microtext", "m1", "issue", 1)),
            "no",
        )
        self.assertEqual(
            preflight.primary_audit_answer(primary("visualdiff", "v1", "edit", 2)),
            "yes",
        )

    def test_agreement_pairs_flag_disagreement_and_unclear(self) -> None:
        primary_rows = [
            primary("microtext", "m1", "accepted", 1),
            primary("visualdiff", "v1", "needs_full_page", 2),
        ]
        audit_rows = [
            auditor("microtext", "m1", "issue", 1),
            auditor("visualdiff", "v1", "needs_full_page", 2),
        ]
        pairs = preflight.build_agreement_pairs(primary_rows, audit_rows)
        self.assertEqual(pairs[0]["adjudication_reasons"], ["decision_disagreement"])
        self.assertEqual(
            pairs[1]["adjudication_reasons"],
            ["primary_unclear", "auditor_unclear"],
        )
        metrics = preflight.agreement_metrics(pairs)
        self.assertEqual(metrics["overall"]["rows"], 2)
        self.assertEqual(metrics["overall"]["adjudication_rows"], 2)

    def test_duplicate_auditor_assignment_is_rejected(self) -> None:
        row = auditor("microtext", "m1", "pass", 1)
        with self.assertRaisesRegex(ValueError, "duplicate record"):
            preflight.build_agreement_pairs(
                [primary("microtext", "m1", "accepted", 1)], [row, dict(row)]
            )

    def test_staging_holds_disagreement_and_invalid_corrected_category(self) -> None:
        manifest = [
            {
                "task": "microtext",
                "candidate_id": "m1",
                "primary_index": "1",
                "category": "pin_label",
                "proposed_text": "PIN1",
            },
            {
                "task": "microtext",
                "candidate_id": "m2",
                "primary_index": "2",
                "category": "pin_label",
                "proposed_text": "PIN2",
            },
        ]
        primary_rows = [
            primary("microtext", "m1", "accepted", 1),
            primary(
                "microtext",
                "m2",
                "edited",
                2,
                corrected_text="PIN-2",
                corrected_category="not_a_taxonomy_category",
            ),
        ]
        pairs = preflight.build_agreement_pairs(
            primary_rows,
            [auditor("microtext", "m1", "issue", 1)],
        )
        all_rows, eligible, held = preflight.stage_primary_rows(
            manifest,
            primary_rows,
            pairs,
            allowed_categories={"pin_label"},
        )
        self.assertEqual(len(all_rows), 2)
        self.assertEqual(eligible, [])
        self.assertEqual(len(held), 2)
        self.assertIn("decision_disagreement", held[0]["preflight_hold_reasons"])
        self.assertIn("invalid_corrected_category", held[1]["preflight_hold_reasons"])
        self.assertFalse(any(row["safe_to_merge_gold"] for row in all_rows))

    def test_staging_outputs_only_positive_nonconflicting_rows(self) -> None:
        manifest = [
            {
                "task": "visualdiff",
                "pair_id": "v1",
                "primary_index": "1",
                "project_id": "family",
            },
            {
                "task": "visualdiff",
                "pair_id": "v2",
                "primary_index": "2",
                "project_id": "family",
            },
        ]
        primary_rows = [
            primary("visualdiff", "v1", "edit", 1, change_description="A to B"),
            primary("visualdiff", "v2", "reject_unclear", 2),
        ]
        pairs = preflight.build_agreement_pairs(
            primary_rows,
            [auditor("visualdiff", "v1", "edit", 1)],
        )
        _all_rows, eligible, held = preflight.stage_primary_rows(
            manifest, primary_rows, pairs
        )
        self.assertEqual([row["pair_id"] for row in eligible], ["v1"])
        self.assertEqual(eligible[0]["description"], "A to B")
        self.assertEqual([row["pair_id"] for row in held], ["v2"])

    def test_output_directory_is_restricted_to_processed_returns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = root / "derived" / "human_adjudication" / "processed_returns"
            allowed.mkdir(parents=True)
            target = allowed / "return_01"
            preflight.ensure_output_dir(target, root, overwrite=False)
            self.assertTrue(target.is_dir())
            with self.assertRaises(ValueError):
                preflight.ensure_output_dir(root / "outside", root, overwrite=False)

    def test_integrity_check_rejects_macro_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            returned = root / "returned.xlsx"
            canonical = root / "canonical.xlsx"
            with zipfile.ZipFile(returned, "w") as archive:
                archive.writestr("xl/vbaProject.bin", b"macro")
            with zipfile.ZipFile(canonical, "w") as archive:
                archive.writestr("placeholder", b"canonical")
            with (
                mock.patch.object(
                    preflight.workbook_contract,
                    "sheet_states",
                    return_value={"review": "visible"},
                ),
                mock.patch.object(
                    preflight,
                    "workbook_anchor_pixel_hashes",
                    return_value=[("review", 0, 1, "hash")],
                ),
                mock.patch.object(
                    preflight.workbook_contract,
                    "validate_picture_layout",
                    return_value=(12, []),
                ),
            ):
                issues = preflight.validate_return_workbook_integrity(
                    returned, canonical, "auditor"
                )
            self.assertIn("macro payload is not allowed", issues)

    def test_integrity_check_supports_single_sheet_primary_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            returned = root / "returned.xlsx"
            canonical = root / "canonical.xlsx"
            for path in (returned, canonical):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("placeholder", b"workbook")
            states = {
                preflight.workbook_contract.SINGLE_PRIMARY_SHEET: "visible",
                preflight.workbook_contract.MACHINE_SHEET: "veryHidden",
            }
            with (
                mock.patch.object(
                    preflight.workbook_contract,
                    "sheet_states",
                    return_value=states,
                ),
                mock.patch.object(
                    preflight,
                    "workbook_anchor_pixel_hashes",
                    return_value=[
                        (
                            preflight.workbook_contract.SINGLE_PRIMARY_SHEET,
                            6,
                            1,
                            "hash",
                        )
                    ],
                ),
                mock.patch.object(
                    preflight.workbook_contract,
                    "validate_picture_layout",
                    return_value=(498, []),
                ) as layout,
            ):
                issues = preflight.validate_return_workbook_integrity(
                    returned, canonical, "primary"
                )
            self.assertEqual(issues, [])
            layout.assert_called_once_with(returned, "primary", True)


if __name__ == "__main__":
    unittest.main()
