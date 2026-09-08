#!/usr/bin/env python3
"""Atomically apply a pinned active-Gold VisualDiff finality correction preview."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
SPLITS_DIR = ROOT_DIR / "splits"
for module_dir in (TOOLS_DIR, SPLITS_DIR):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))

import audit_active_gold_provenance
import audit_question_leakage
import leakage_check
import unify_dataset
import validate_engbench_v2
from apply_reviewed_gold_promotion import atomic_write, post_apply_validation, restore_snapshot
from preview_reviewed_gold_promotion import ACTIVE_PATHS, annotation_errors, file_sha256, read_jsonl
from visualdiff_description_finality import (
    definitive_description,
    tentative_description_details,
    visualdiff_description_finality,
)
from visualdiff_relocation_finality import (
    MAX_BOX_BIND_DISTANCE_PX,
    MAX_MOVE_DISTANCE_PX,
    MIN_MOVE_DISTANCE_PX,
    POLICY_VERSION as RELOCATION_POLICY_VERSION,
    movement_from_probes,
    relocation_description,
    span_shape_matches,
)
from visualdiff_replacement_finality import (
    MAX_BOX_BIND_DISTANCE_PX as REPLACEMENT_MAX_BOX_BIND_DISTANCE_PX,
    POLICY_VERSION as REPLACEMENT_POLICY_VERSION,
    replacement_description,
    same_slot_matches,
)


TEXTLAYER_POLICY_VERSION = "active_visualdiff_textlayer_finality_v1"
POLICY_VERSION = TEXTLAYER_POLICY_VERSION
DESC_SOURCE = "human_semantics_machine_textlayer_finalized"
SUPPORTED_PREVIEW_MODES = {
    TEXTLAYER_POLICY_VERSION: "read_only_active_visualdiff_finality_correction_preview",
    RELOCATION_POLICY_VERSION: "read_only_active_visualdiff_relocation_correction_preview",
    REPLACEMENT_POLICY_VERSION: "read_only_active_visualdiff_replacement_correction_preview",
}
DESC_SOURCE_BY_POLICY = {
    TEXTLAYER_POLICY_VERSION: DESC_SOURCE,
    RELOCATION_POLICY_VERSION: "human_reviewed_machine_unique_text_relocation",
    REPLACEMENT_POLICY_VERSION: "human_reviewed_machine_same_slot_text_replacement",
}
METHOD_BY_POLICY = {
    TEXTLAYER_POLICY_VERSION: "human_semantics_machine_textlayer_finalization",
    RELOCATION_POLICY_VERSION: "machine_unique_textlayer_relocation_of_human_reviewed_gap",
    REPLACEMENT_POLICY_VERSION: "machine_same_slot_textlayer_replacement_of_human_reviewed_gap",
}
MUTABLE_PATHS = (
    "visualdiff/annotations/visualdiff_pairs.jsonl",
    "visualdiff/annotations/visualdiff_questions.jsonl",
    "eng_bench.jsonl",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode("utf-8")


def require_under(root: Path, value: Path, parent: str, label: str) -> Path:
    path = value if value.is_absolute() else root / value
    resolved = path.resolve()
    allowed = (root / parent).resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"{label} must be under {parent}")
    return resolved


def load_preview(root: Path, path: Path, expected_sha256: str) -> tuple[Path, dict[str, Any]]:
    resolved = require_under(root, path, "derived/quality", "preview report")
    if not resolved.is_file():
        raise ValueError(f"preview report does not exist: {resolved}")
    actual = file_sha256(resolved).lower()
    expected = expected_sha256.strip().lower()
    if not expected or actual != expected:
        raise ValueError(f"preview_report_sha256_mismatch:{actual}:{expected or 'blank'}")
    report = json.loads(resolved.read_text(encoding="utf-8"))
    policy_version = str(report.get("policy_version") or "")
    if report.get("mode") != SUPPORTED_PREVIEW_MODES.get(policy_version):
        raise ValueError("unsupported_preview_mode")
    if policy_version not in SUPPORTED_PREVIEW_MODES:
        raise ValueError("unsupported_preview_policy")
    if report.get("status") != "PASS" or report.get("ready_for_apply") is not True:
        raise ValueError("preview_not_ready_for_apply")
    if report.get("active_gold_modified") is not False:
        raise ValueError("preview_says_active_gold_was_modified")
    if report.get("active_file_hashes_before") != report.get("active_file_hashes_after"):
        raise ValueError("preview_active_before_after_hashes_differ")
    return resolved, report


def verify_prestate(root: Path, report: dict[str, Any]) -> dict[str, str]:
    expected = report.get("active_file_hashes_before") or {}
    if set(expected) != set(ACTIVE_PATHS):
        raise ValueError("preview_active_hash_set_mismatch")
    actual = {relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS}
    for relative in ACTIVE_PATHS:
        if actual[relative] != str(expected[relative]).lower():
            raise ValueError(f"active_file_changed_since_preview:{relative}")
    return actual


def load_corrections(
    root: Path,
    report: dict[str, Any],
) -> tuple[Path, list[dict[str, Any]]]:
    path = require_under(
        root, Path(str((report.get("artifacts") or {}).get("corrections") or "")),
        "derived/quality", "corrections artifact",
    )
    expected = str((report.get("artifact_sha256") or {}).get("corrections") or "").lower()
    if not path.is_file() or not expected or file_sha256(path).lower() != expected:
        raise ValueError("corrections_artifact_missing_or_hash_mismatch")
    corrections = read_jsonl(path)
    count = int((report.get("counts") or {}).get("prepared_corrections") or 0)
    if not corrections or len(corrections) != count:
        raise ValueError("correction_count_mismatch")
    identities = [str(row.get("pair_id") or "") for row in corrections]
    if any(not identity for identity in identities) or len(identities) != len(set(identities)):
        raise ValueError("correction_ids_missing_or_duplicated")
    policy_version = str(report.get("policy_version") or "")
    if any(row.get("policy_version") != policy_version for row in corrections):
        raise ValueError("correction_policy_mismatch")
    for relative, expected_hash in (report.get("evidence_hashes") or {}).items():
        evidence = require_under(root, Path(relative), ".", "evidence artifact")
        if not evidence.is_file() or file_sha256(evidence).lower() != str(expected_hash).lower():
            raise ValueError(f"evidence_hash_mismatch:{relative}")
    return path, corrections


def index_unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, row in enumerate(rows):
        identity = str(row.get(field) or "")
        if not identity or identity in result:
            raise ValueError(f"{label}_missing_or_duplicate_identity:{identity or 'blank'}")
        result[identity] = index
    return result


def changed_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}


def deterministic_final_description(
    correction: dict[str, Any], details: dict[str, str]
) -> str:
    policy_version = str(correction.get("policy_version") or "")
    if policy_version == TEXTLAYER_POLICY_VERSION:
        return definitive_description(details)
    if policy_version == REPLACEMENT_POLICY_VERSION:
        old_text = str(correction.get("old_text") or "")
        new_text = str(correction.get("new_text") or "")
        target = str(details.get("target") or "")
        if details.get("kind") == "text_removed" and target != old_text:
            raise ValueError("replacement_old_target_mismatch")
        if details.get("kind") == "text_added" and target != new_text:
            raise ValueError("replacement_new_target_mismatch")
        if str(correction.get("original_tentative_kind") or "") != str(details.get("kind") or ""):
            raise ValueError("replacement_original_kind_mismatch")
        old_probe = correction.get("old_probe") or {}
        new_probe = correction.get("new_probe") or {}
        for side, probe in (("old", old_probe), ("new", new_probe)):
            if probe.get("status") != "observed":
                raise ValueError(f"replacement_{side}_probe_not_observed")
            if int(probe.get("nearby_matches") or 0) != 1:
                raise ValueError(f"replacement_{side}_match_not_unique_in_gap")
            best = probe.get("best") or {}
            if not best or float(best.get("distance_px") or float("inf")) > REPLACEMENT_MAX_BOX_BIND_DISTANCE_PX:
                raise ValueError(f"replacement_{side}_match_not_bound_to_gap")
        if not same_slot_matches(old_probe.get("best") or {}, new_probe.get("best") or {}):
            raise ValueError("replacement_spans_not_same_slot")
        return replacement_description(old_text, new_text)
    if policy_version != RELOCATION_POLICY_VERSION:
        raise ValueError("unsupported_correction_policy")
    target = str(correction.get("target") or "")
    if target != str(details.get("target") or ""):
        raise ValueError("relocation_target_mismatch")
    old_probe = correction.get("old_probe") or {}
    new_probe = correction.get("new_probe") or {}
    if old_probe.get("status") != "observed" or new_probe.get("status") != "observed":
        raise ValueError("relocation_probe_not_observed")
    if int(old_probe.get("page_matches") or 0) != 1 or int(new_probe.get("page_matches") or 0) != 1:
        raise ValueError("relocation_match_not_unique")
    old_best = old_probe.get("best") or {}
    new_best = new_probe.get("best") or {}
    if not old_best or not new_best:
        raise ValueError("relocation_best_match_missing")
    if min(
        float(old_best.get("distance_px") or float("inf")),
        float(new_best.get("distance_px") or float("inf")),
    ) > MAX_BOX_BIND_DISTANCE_PX:
        raise ValueError("relocation_not_bound_to_gap_box")
    if str(old_best.get("font") or "") != str(new_best.get("font") or ""):
        raise ValueError("relocation_font_mismatch")
    if int(old_best.get("flags") or 0) != int(new_best.get("flags") or 0):
        raise ValueError("relocation_font_flags_mismatch")
    if abs(float(old_best.get("size") or 0) - float(new_best.get("size") or 0)) > 0.001:
        raise ValueError("relocation_font_size_mismatch")
    if not span_shape_matches(old_best.get("bbox_px"), new_best.get("bbox_px")):
        raise ValueError("relocation_span_shape_mismatch")
    if int(correction.get("revision_target_count") or 0) != 1:
        raise ValueError("relocation_target_repeated")
    observed_movement = movement_from_probes(old_probe, new_probe)
    claimed_movement = correction.get("movement") or {}
    if observed_movement != claimed_movement:
        raise ValueError("relocation_movement_mismatch")
    distance = float(observed_movement["distance_px"])
    if not MIN_MOVE_DISTANCE_PX <= distance <= MAX_MOVE_DISTANCE_PX:
        raise ValueError("relocation_distance_out_of_policy")
    return relocation_description(target, observed_movement)


def apply_corrections_to_rows(
    pairs: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    *,
    report_path: str,
    report_sha256: str,
    corrections_path: str,
    corrections_sha256: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    new_pairs = copy.deepcopy(pairs)
    new_questions = copy.deepcopy(questions)
    pair_index = index_unique(pairs, "pair_id", "visualdiff_pairs")
    question_index = index_unique(questions, "pair_id", "visualdiff_questions")
    for correction in corrections:
        pair_id = str(correction.get("pair_id") or "")
        if pair_id not in pair_index or pair_id not in question_index:
            raise ValueError(f"correction_identity_not_active:{pair_id}")
        policy_version = str(correction.get("policy_version") or "")
        if policy_version not in SUPPORTED_PREVIEW_MODES or correction.get("safe_to_apply") is not True:
            raise ValueError(f"invalid_correction_contract:{pair_id}")
        pair = pairs[pair_index[pair_id]]
        question = questions[question_index[pair_id]]
        original = str(correction.get("original_description") or "")
        final = str(correction.get("final_description") or "")
        if str(pair.get("change_desc_gt") or "") != original:
            raise ValueError(f"active_description_mismatch:{pair_id}")
        if str(question.get("answer_text") or "") != original:
            raise ValueError(f"active_question_answer_mismatch:{pair_id}")
        details = tentative_description_details(original)
        if not details or details.get("kind") not in {"text_added", "text_removed"}:
            raise ValueError(f"original_description_not_supported_tentative_text:{pair_id}")
        if deterministic_final_description(correction, details) != final:
            raise ValueError(f"final_description_not_deterministic:{pair_id}")
        if str(pair.get("desc_source") or "").lower() != "human":
            raise ValueError(f"active_description_not_human_sourced:{pair_id}")
        if str(pair.get("review_confidence") or "").lower() != "high":
            raise ValueError(f"active_review_confidence_not_high:{pair_id}")
        if pair.get("bbox_old") != correction.get("bbox_old") or pair.get("bbox_new") != correction.get("bbox_new"):
            raise ValueError(f"active_bbox_changed_since_preview:{pair_id}")

        certification = {
            "method": METHOD_BY_POLICY[policy_version],
            "policy_version": policy_version,
            "preview_report": report_path,
            "preview_report_sha256": report_sha256,
            "corrections_artifact": corrections_path,
            "corrections_artifact_sha256": corrections_sha256,
            "original_human_description": original,
            "final_description": final,
            "kind": correction["kind"],
            "target": correction["target"],
            "old_doc_id": correction["old_doc_id"],
            "new_doc_id": correction["new_doc_id"],
        }
        if policy_version == TEXTLAYER_POLICY_VERSION:
            certification.update({
                "expected_side": correction["expected_side"],
                "expected_probe": correction["expected_probe"],
                "opposite_probe": correction["opposite_probe"],
            })
        elif policy_version == RELOCATION_POLICY_VERSION:
            certification.update({
                "old_probe": correction["old_probe"],
                "new_probe": correction["new_probe"],
                "movement": correction["movement"],
                "revision_target_count": correction["revision_target_count"],
            })
        else:
            certification.update({
                "original_tentative_kind": correction["original_tentative_kind"],
                "old_text": correction["old_text"],
                "new_text": correction["new_text"],
                "old_probe": correction["old_probe"],
                "new_probe": correction["new_probe"],
                "localized_alternatives": correction["localized_alternatives"],
            })
        updated_pair = new_pairs[pair_index[pair_id]]
        updated_pair["change_desc_gt"] = final
        updated_pair["desc_source"] = DESC_SOURCE_BY_POLICY[policy_version]
        review_evidence = copy.deepcopy(updated_pair.get("review_evidence") or {})
        review_evidence["machine_finality_certification"] = certification
        updated_pair["review_evidence"] = review_evidence

        updated_question = new_questions[question_index[pair_id]]
        updated_question["answer_text"] = final
        updated_question["desc_source"] = DESC_SOURCE_BY_POLICY[policy_version]
        updated_question["answer_provenance"] = certification
    return new_pairs, new_questions


def validate_exact_mutations(
    old_pairs: list[dict[str, Any]], new_pairs: list[dict[str, Any]],
    old_questions: list[dict[str, Any]], new_questions: list[dict[str, Any]],
    corrected_ids: set[str],
) -> None:
    if len(old_pairs) != len(new_pairs) or len(old_questions) != len(new_questions):
        raise ValueError("annotation_row_count_changed")
    for before, after in zip(old_pairs, new_pairs):
        identity = str(before.get("pair_id") or "")
        changes = changed_keys(before, after)
        if identity in corrected_ids:
            if not changes or not changes.issubset({"change_desc_gt", "desc_source", "review_evidence"}):
                raise ValueError(f"unexpected_pair_mutation:{identity}:{sorted(changes)}")
        elif changes:
            raise ValueError(f"uncorrected_pair_changed:{identity}:{sorted(changes)}")
    for before, after in zip(old_questions, new_questions):
        identity = str(before.get("pair_id") or "")
        changes = changed_keys(before, after)
        if identity in corrected_ids:
            if not changes or not changes.issubset({"answer_text", "desc_source", "answer_provenance"}):
                raise ValueError(f"unexpected_question_mutation:{identity}:{sorted(changes)}")
        elif changes:
            raise ValueError(f"uncorrected_question_changed:{identity}:{sorted(changes)}")


def validate_staged(
    root: Path,
    old_pairs: list[dict[str, Any]],
    old_questions: list[dict[str, Any]],
    new_pairs: list[dict[str, Any]],
    new_questions: list[dict[str, Any]],
    corrected_ids: set[str],
) -> tuple[bytes, dict[str, Any]]:
    validate_exact_mutations(old_pairs, new_pairs, old_questions, new_questions, corrected_ids)
    items = read_jsonl(root / "microtext/annotations/microtext_items.jsonl")
    micro_questions = read_jsonl(root / "microtext/annotations/microtext_questions.jsonl")
    annotation_issue_list = annotation_errors(new_pairs, new_questions, items, micro_questions)
    if annotation_issue_list:
        raise ValueError("annotation_validation_failed:" + ";".join(annotation_issue_list[:10]))

    active_unified = read_jsonl(root / "eng_bench.jsonl")
    rebuilt_active = unify_dataset.process_visualdiff(root) + unify_dataset.process_microtext(root)
    if rebuilt_active != active_unified:
        raise ValueError("active_unified_does_not_match_active_annotations")
    with tempfile.TemporaryDirectory(dir=root / "derived" / "quality") as temporary:
        temp = Path(temporary)
        pairs_path = temp / "visualdiff_pairs.jsonl"
        questions_path = temp / "visualdiff_questions.jsonl"
        pairs_path.write_bytes(jsonl_bytes(new_pairs))
        questions_path.write_bytes(jsonl_bytes(new_questions))
        unified = unify_dataset.process_visualdiff(root, pairs_path, questions_path)
    unified += unify_dataset.process_microtext(root)
    unified_by_id = {str(row["metadata"]["pair_id"]): row for row in unified if row["task"] == "visualdiff"}
    active_by_id = {
        str(row["metadata"]["pair_id"]): row for row in active_unified if row["task"] == "visualdiff"
    }
    if set(unified_by_id) != set(active_by_id):
        raise ValueError("unified_visualdiff_identity_set_changed")
    for pair_id, before in active_by_id.items():
        changes = changed_keys(before, unified_by_id[pair_id])
        if pair_id in corrected_ids:
            if changes != {"answer"}:
                raise ValueError(f"unexpected_unified_mutation:{pair_id}:{sorted(changes)}")
        elif changes:
            raise ValueError(f"uncorrected_unified_row_changed:{pair_id}:{sorted(changes)}")

    manifest = validate_engbench_v2.load_manifest(str(root / "manifest.jsonl"))
    strict, bad = validate_engbench_v2.validate_all(
        unified, manifest, str(root), strict=True, skip_textlayer=True
    )
    if strict.errors or bad:
        raise ValueError("strict_unified_validation_failed:" + ";".join(strict.errors[:10]))
    question_leakage = audit_question_leakage.audit(unified)
    if question_leakage["critical_failures"]:
        raise ValueError(f"question_answer_leakage:{question_leakage['critical_failures']}")
    split_issues = leakage_check.check_leakage(root)
    if split_issues:
        raise ValueError("split_leakage:" + ";".join(split_issues[:10]))
    before_finality = visualdiff_description_finality(old_pairs)
    after_finality = visualdiff_description_finality(new_pairs)
    if before_finality["tentative_rows"] - after_finality["tentative_rows"] != len(corrected_ids):
        raise ValueError("tentative_description_delta_mismatch")
    return jsonl_bytes(unified), {
        "active_rows": len(unified),
        "corrected_visualdiff_rows": len(corrected_ids),
        "tentative_before": before_finality["tentative_rows"],
        "tentative_after": after_finality["tentative_rows"],
        "strict_errors": 0,
        "question_leaks": 0,
        "split_leaks": 0,
    }


def create_snapshot(root: Path, snapshot: Path, before_hashes: dict[str, str]) -> None:
    snapshot.mkdir(parents=True, exist_ok=True)
    if any(snapshot.iterdir()):
        raise ValueError(f"snapshot_directory_not_empty:{snapshot}")
    for relative in ACTIVE_PATHS:
        source = root / relative
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (snapshot / "snapshot_manifest.json").write_text(
        json.dumps({
            "mode": "active_visualdiff_finality_correction_snapshot",
            "root": root.as_posix(),
            "files": before_hashes,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_transaction(
    root: Path,
    preview_path: Path,
    expected_preview_sha256: str,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, str], dict[str, Any]]:
    root = root.resolve()
    report_path, report = load_preview(root, preview_path, expected_preview_sha256)
    before_hashes = verify_prestate(root, report)
    corrections_path, corrections = load_corrections(root, report)
    pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
    questions = read_jsonl(root / "visualdiff/annotations/visualdiff_questions.jsonl")
    corrected_ids = {str(row["pair_id"]) for row in corrections}
    new_pairs, new_questions = apply_corrections_to_rows(
        pairs, questions, corrections,
        report_path=report_path.relative_to(root).as_posix(),
        report_sha256=file_sha256(report_path),
        corrections_path=corrections_path.relative_to(root).as_posix(),
        corrections_sha256=file_sha256(corrections_path),
    )
    unified_bytes, validation = validate_staged(
        root, pairs, questions, new_pairs, new_questions, corrected_ids
    )
    staged = {
        "visualdiff/annotations/visualdiff_pairs.jsonl": jsonl_bytes(new_pairs),
        "visualdiff/annotations/visualdiff_questions.jsonl": jsonl_bytes(new_questions),
        "eng_bench.jsonl": unified_bytes,
    }
    expected_after = dict(before_hashes)
    expected_after.update({path: sha256_bytes(data) for path, data in staged.items()})
    transaction = {
        "goal": "Gold v2.0 Global",
        "mode": "active_visualdiff_finality_correction_transaction",
        "policy_version": report["policy_version"],
        "preview_report": report_path.relative_to(root).as_posix(),
        "preview_report_sha256": file_sha256(report_path),
        "corrections_artifact": corrections_path.relative_to(root).as_posix(),
        "corrections_artifact_sha256": file_sha256(corrections_path),
        "correction_count": len(corrections),
        "ready_to_apply": True,
        "applied": False,
        "rolled_back": False,
        "before_hashes": before_hashes,
        "expected_after_hashes": expected_after,
        "validation": validation,
    }
    return transaction, staged, before_hashes, audit_active_gold_provenance.build_report(root)


def apply_transaction(
    root: Path,
    preview_path: Path,
    expected_preview_sha256: str,
    *,
    snapshot_dir: Path | None = None,
    apply: bool = False,
    fail_after_replacements: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    transaction, staged, before_hashes, before_provenance = build_transaction(
        root, preview_path, expected_preview_sha256
    )
    if not apply:
        return transaction
    if snapshot_dir is None:
        raise ValueError("snapshot_dir_is_required_for_apply")
    snapshot = require_under(root, snapshot_dir, "derived/snapshots", "snapshot directory")
    create_snapshot(root, snapshot, before_hashes)
    transaction["snapshot_dir"] = snapshot.relative_to(root).as_posix()
    replacements = 0
    try:
        for relative in MUTABLE_PATHS:
            atomic_write(root / relative, staged[relative])
            replacements += 1
            if fail_after_replacements is not None and replacements >= fail_after_replacements:
                raise RuntimeError("injected_post_replace_failure")
        post = post_apply_validation(
            root, transaction["expected_after_hashes"], before_provenance
        )
        pairs = read_jsonl(root / "visualdiff/annotations/visualdiff_pairs.jsonl")
        finality = visualdiff_description_finality(pairs)
        if finality["tentative_rows"] != transaction["validation"]["tentative_after"]:
            raise ValueError("post_apply_tentative_count_mismatch")
        transaction.update({
            "applied": True,
            "after_hashes": {relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS},
            "post_apply_validation": {**post, "tentative_visualdiff_rows": finality["tentative_rows"]},
        })
        return transaction
    except Exception as exc:
        try:
            restore_snapshot(root, snapshot, before_hashes)
            transaction.update({
                "applied": False,
                "rolled_back": True,
                "failure": f"{type(exc).__name__}:{exc}",
                "restored_hashes": {
                    relative: file_sha256(root / relative).lower() for relative in ACTIVE_PATHS
                },
            })
        except Exception as rollback_exc:
            transaction.update({
                "applied": False,
                "rolled_back": False,
                "failure": f"{type(exc).__name__}:{exc}",
                "rollback_failure": f"{type(rollback_exc).__name__}:{rollback_exc}",
            })
        return transaction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--preview-report", type=Path, required=True)
    parser.add_argument("--expected-preview-sha256", required=True)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        report_path = require_under(root, args.report, "derived/quality", "transaction report")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        result = apply_transaction(
            root,
            args.preview_report,
            args.expected_preview_sha256,
            snapshot_dir=args.snapshot_dir,
            apply=args.apply,
        )
        report_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(json.dumps({
        "ready_to_apply": result.get("ready_to_apply"),
        "applied": result.get("applied"),
        "rolled_back": result.get("rolled_back"),
        "correction_count": result.get("correction_count"),
        "validation": result.get("validation"),
    }, indent=2))
    return 0 if result.get("ready_to_apply") and (not args.apply or result.get("applied")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
