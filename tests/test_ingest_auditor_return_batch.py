import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import ingest_auditor_return_batch as ingest
from tools import process_partial_auditor_returns as parser


def observation(code="1", **extra):
    return {"reviewer_id": "auditor_01", "record_id": "target", "decision_code": code,
            "task_type": "microtext", "safe_to_merge_gold": False, **extra}


class IngestAuditorReturnTests(unittest.TestCase):
    def index_fixture(self, micro_rows, visual_rows=()):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for task, name, rows in (("microtext", "microtext_items", micro_rows),
                                 ("visualdiff", "visualdiff_pairs", visual_rows)):
            path = root / task / "annotations" / f"{name}.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return root

    def test_microtext_source_candidate_link_is_not_new_capacity(self):
        root = self.index_fixture([{"item_id": "mt__active", "source_candidate_id": "target"}])
        active = ingest.active_identity_index(root)
        action = ingest.summarize_actions([observation()], active, {})[0]
        self.assertEqual("active_gold_audit_support_only", action["action"])
        self.assertEqual("mt__active", action["active_gold_identity"])
        self.assertTrue(action["active_gold_match"])
        self.assertFalse(action["active_gold_direct_identity_match"])
        self.assertEqual(["source_candidate_id"], action["active_gold_match_basis"])

    def test_negative_provenance_alias_routes_to_active_recheck(self):
        root = self.index_fixture([{"item_id": "mt__active", "source_candidate_id": "target"}])
        action = ingest.summarize_actions([observation("2")], ingest.active_identity_index(root), {})[0]
        self.assertEqual("active_gold_semantic_recheck", action["action"])
        self.assertFalse(action["safe_to_merge_gold"])

    def test_ambiguous_provenance_alias_is_not_last_write_wins(self):
        root = self.index_fixture([
            {"item_id": "mt__one", "source_candidate_id": "target"},
            {"item_id": "mt__two", "source_candidate_id": "target"},
        ])
        with self.assertRaisesRegex(ValueError, "ambiguous_active_identity_alias"):
            ingest.active_identity_index(root)

    def test_visualdiff_source_asset_is_not_a_row_alias(self):
        root = self.index_fixture([], [{"pair_id": "v__one", "source_candidate_id": "pcb_001"}])
        self.assertNotIn("pcb_001", ingest.active_identity_index(root))

    def test_same_reviewer_replay_is_not_registered_twice(self):
        new, replayed, changed = ingest.reconcile_history([observation()], [observation()])
        self.assertEqual((len(new), len(replayed), len(changed)), (0, 1, 0))

    def test_new_reviewer_is_independent_not_a_replay(self):
        rows = [observation(reviewer_id="auditor_02")]
        new, replayed, changed = ingest.reconcile_history(rows, [observation()])
        self.assertEqual((len(new), len(replayed), len(changed)), (1, 0, 0))

    def test_changed_historical_vote_is_held(self):
        new, replayed, changed = ingest.reconcile_history([observation("2")], [observation()])
        self.assertEqual((len(new), len(replayed), len(changed)), (0, 0, 1))
        self.assertEqual(changed[0]["prior_codes"], ["1"])

    def test_active_gold_negative_and_uncertain_are_rechecks(self):
        for code, action in (("2", "active_gold_semantic_recheck"),
                             ("3", "active_gold_evidence_recheck")):
            rows = ingest.summarize_actions([observation(code)], {"target": {}}, {})
            self.assertEqual(rows[0]["action"], action)
            self.assertFalse(rows[0]["safe_to_merge_gold"])

    def test_positive_primary_plus_auditor_never_grants_promotion(self):
        rows = ingest.summarize_actions([observation()], {},
                                        {"target": {"primary_reviewer_status": "accepted"}})
        self.assertEqual(rows[0]["action"], "primary_and_auditor_support_pending_release_gates")
        self.assertFalse(rows[0]["safe_to_merge_gold"])

    def test_positive_without_primary_needs_primary(self):
        rows = ingest.summarize_actions([observation()], {}, {})
        self.assertEqual(rows[0]["action"], "auditor_support_pending_primary_and_release_gates")

    def test_visualdiff_valid_is_a_completed_primary_review_not_missing(self):
        rows = ingest.summarize_actions([observation(task_type="visualdiff")], {},
                                        {"target": {"primary_reviewer_status": "valid"}})
        self.assertEqual(rows[0]["action"], "primary_and_auditor_support_pending_release_gates")
        self.assertFalse(rows[0]["safe_to_merge_gold"])

    def test_output_cannot_escape_processed_return_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "output must"):
                ingest.ingest(root, root / "input.zip", root / "payload.json",
                              root / "issued", root / "output", [])

    def test_formula_answers_are_not_human_votes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "return.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="7"><c r="D7"><f>1</f><v>1</v></c></row></sheetData></worksheet>')
            with patch.object(parser.workbook_io, "workbook_sheet_paths", return_value={"Review": "xl/worksheets/sheet1.xml"}):
                with self.assertRaisesRegex(ValueError, "formula in human answer D7"):
                    ingest.require_literal_answers(path, "Review", 24)


class PartialAnswerValidationTests(unittest.TestCase):
    def validate_fixture(self, codes, *, allow=False, canonical=False, changed_prompt=False,
                         frozen_drift=False, extra_visible=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "AUDITOR_01_RETURN.xlsx"
            with zipfile.ZipFile(path, "w"):
                pass
            evidence = root / "image.png"
            evidence.write_bytes(b"evidence")
            evidence_hash = parser.sha256_file(evidence)
            expected = [{"primary_index": str(i), "display_index": str(i),
                         "task": "microtext", "candidate_id": f"r{i}",
                         "machine_suggestion": "label", "evidence_path": str(evidence),
                         "evidence_sha256": "changed" if frozen_drift else evidence_hash,
                         "preserved_answer_code": "1" if i == 1 else ""}
                        for i in range(1, len(codes) + 1)]
            machine = [{"primary_index": e["primary_index"], "display_index": e["display_index"],
                        "task": e["task"], "record_id": e["candidate_id"],
                        "machine_suggestion": e["machine_suggestion"]} for e in expected]
            visible = [{"#": str(i), "prompt": "label", parser.normalizer.ULTRA_VISUAL_DECISION: code}
                       for i, code in enumerate(codes, 1)]
            issued = [dict(row) for row in visible]
            if changed_prompt:
                visible[0]["prompt"] = "different label"
            if extra_visible:
                visible.append(dict(visible[0]))
            states = {parser.delivery.AUDIT_SHEET: "visible", parser.delivery.MACHINE_SHEET: "hidden"}
            auditor = {"number": 1, "rows": expected}
            with patch.object(parser.delivery, "sheet_states", return_value=states), \
                 patch.object(parser.normalizer, "_machine_rows", return_value=machine), \
                 patch.object(parser.normalizer, "_auditor_records", side_effect=[visible, issued]), \
                 patch.object(parser, "embedded_evidence_hashes", return_value=[evidence_hash] * len(expected)):
                return parser.validate_workbook(path, auditor, allow_incomplete_answers=allow,
                                                canonical_workbook=path if canonical else None)

    def test_default_still_rejects_blank(self):
        with self.assertRaisesRegex(ValueError, "invalid auditor decision"):
            self.validate_fixture(["1", ""])

    def test_opt_in_retains_filled_rows_without_inventing_blank_vote(self):
        rows, report = self.validate_fixture(["1", "", "2"], allow=True, canonical=True)
        self.assertEqual([r["decision_code"] for r in rows], ["1", "2"])
        self.assertEqual(report["completed_rows"], 2)
        self.assertEqual(report["incomplete_rows"][0]["answer_cell"], "D8")
        self.assertFalse(report["complete"])
        self.assertTrue(rows[0]["carried_forward_answer"])
        self.assertTrue(all(r["safe_to_merge_gold"] is False for r in rows))

    def test_opt_in_does_not_accept_unknown_code(self):
        with self.assertRaisesRegex(ValueError, "invalid auditor decision"):
            self.validate_fixture(["1", "7"], allow=True)

    def test_visible_prompt_tampering_rejected(self):
        with self.assertRaisesRegex(ValueError, "immutable visible cells"):
            self.validate_fixture(["1"], canonical=True, changed_prompt=True)

    def test_frozen_evidence_hash_drift_rejected(self):
        with self.assertRaisesRegex(ValueError, "canonical evidence changed"):
            self.validate_fixture(["1"], frozen_drift=True)

    def test_appended_visible_row_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "expected 1 visible rows"):
            self.validate_fixture(["1"], extra_visible=True)


class EvidenceAnchorTests(unittest.TestCase):
    def check_anchors(self, *, header=False, overlap=False, foreign=False, duplicate_row=False,
                      shifted_boundary=False, ambiguous_boundary=False):
        drawing_ns = parser.payload_io.DRAWING_NS
        main_ns = parser.payload_io.DRAWINGML_NS
        office_ns = parser.payload_io.OFFICE_REL_NS

        def anchor(row, relation, height=1000, offset=0):
            return f'<x:oneCellAnchor><x:from><x:row>{row}</x:row><x:rowOff>{offset}</x:rowOff></x:from><x:ext cx="100" cy="{height}"/><x:pic><a:blip r:embed="{relation}"/></x:pic></x:oneCellAnchor>'

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "return.xlsx"
            if shifted_boundary:
                second = anchor(6, "r2", offset=253990)
            elif ambiguous_boundary:
                second = anchor(6, "r2", height=100000, offset=204000)
            else:
                second = anchor(6 if duplicate_row else 7, "r2")
            body = anchor(6, "r1") + second
            if header:
                body += anchor(0, "extra", 2000000 if overlap else 1000)
            drawing = f'<x:wsDr xmlns:x="{drawing_ns}" xmlns:a="{main_ns}" xmlns:r="{office_ns}">{body}</x:wsDr>'
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetFormatPr defaultRowHeight="20"/></worksheet>')
                archive.writestr("xl/drawings/drawing1.xml", drawing)
                archive.writestr("xl/media/image1.png", b"one")
                archive.writestr("xl/media/image2.png", b"two")
                archive.writestr("xl/media/extra.png", b"foreign" if foreign else b"one")
            relations = [{"draw": "xl/drawings/drawing1.xml"},
                         {"r1": "xl/media/image1.png", "r2": "xl/media/image2.png", "extra": "xl/media/extra.png"}]
            report = {}
            with patch.object(parser.workbook_io, "workbook_sheet_paths", return_value={"Review": "xl/worksheets/sheet1.xml"}), \
                 patch.object(parser.payload_io, "relation_targets", side_effect=relations):
                hashes = parser.embedded_evidence_hashes(path, "Review", 2, report)
            return hashes, report

    def test_nonoverlapping_header_duplicate_is_reported(self):
        hashes, report = self.check_anchors(header=True)
        self.assertEqual(len(hashes), 2)
        self.assertEqual(report["nonoverlapping_header_duplicate_images"], 1)

    def test_extra_image_cannot_cover_review_rows(self):
        with self.assertRaisesRegex(ValueError, "overlaps review rows"):
            self.check_anchors(header=True, overlap=True)

    def test_extra_image_cannot_be_unassigned_evidence(self):
        with self.assertRaisesRegex(ValueError, "unassigned extra image"):
            self.check_anchors(header=True, foreign=True)

    def test_two_images_on_one_row_fail_even_if_total_count_matches(self):
        with self.assertRaisesRegex(ValueError, "evidence anchors"):
            self.check_anchors(duplicate_row=True)

    def test_boundary_shift_is_recovered_by_geometric_row(self):
        hashes, report = self.check_anchors(shifted_boundary=True)
        self.assertEqual(2, len(hashes))
        self.assertEqual(7, report["geometrically_recovered_anchors"][0]["encoded_row"])
        self.assertEqual(8, report["geometrically_recovered_anchors"][0]["geometric_row"])
        self.assertGreaterEqual(report["geometrically_recovered_anchors"][0]["containment"], 0.99)

    def test_ambiguous_cross_row_image_is_not_recovered(self):
        with self.assertRaisesRegex(ValueError, "not contained"):
            self.check_anchors(ambiguous_boundary=True)


if __name__ == "__main__":
    unittest.main()
