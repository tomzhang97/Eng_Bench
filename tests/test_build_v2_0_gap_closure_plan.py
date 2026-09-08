from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import build_v2_0_gap_closure_plan


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_gap_plan_keeps_review_rows_out_of_current_gold_forecast(tmp_path: Path) -> None:
    gates = {
        "gates": {
            "total_rows": {"current": 2681, "target": "25000-50000", "passes": False},
            "gold_source_docs": {"current": 17, "target": 150, "passes": False},
            "hidden_public_test_examples": {"current": 382, "target": 5000, "passes": False},
            "visualdiff_revision_families": {"current": 3, "target": 30, "passes": False},
            "human_agreement_audit": {
                "current": "0/163 complete",
                "target": "all sampled rows complete and agreement thresholds pass",
                "passes": False,
            },
        }
    }
    handoff = {
        "valid": True,
        "handoff_dir": {
            "valid": True,
            "path": "derived/human_adjudication/hv",
            "packet_folders": 2,
            "worklist_review_rows": 8,
            "totals": {"checklist_rows": 8, "png_files": 12},
        },
        "split_dir": {"valid": True, "zip_count": 1},
    }
    write_json(tmp_path / "gate.json", gates)
    write_json(tmp_path / "handoff.json", handoff)
    write_csv(
        tmp_path
        / "derived"
        / "human_adjudication"
        / "hv"
        / "01_packets"
        / "p01"
        / "visualdiff_example_validation_checklist.csv",
        [
            {"review_index": "1", "pair_id": "vdiff__a__1__to__2__001", "split": "test", "project_id": "vdiff__a__1__to__2"},
            {"review_index": "2", "pair_id": "vdiff__a__1__to__2__002", "split": "train", "project_id": "vdiff__a__1__to__2"},
            {"review_index": "3", "pair_id": "vdiff__b__1__to__2__001", "split": "provisional_review", "project_id": "vdiff__b__1__to__2"},
        ],
    )
    write_csv(
        tmp_path
        / "derived"
        / "human_adjudication"
        / "hv"
        / "01_packets"
        / "p02"
        / "microtext_example_validation_checklist.csv",
        [
            {"review_index": "1", "candidate_id": "mtcand__doc1__p0__1", "doc_id": "doc1", "category": "room_label"},
            {"review_index": "2", "candidate_id": "mtcand__doc2__p0__1", "doc_id": "doc2", "category": "dimension_value"},
        ],
    )

    report = build_v2_0_gap_closure_plan.build_report(
        root=tmp_path,
        gate_status=gates,
        handoff_report=handoff,
    )

    assert report["current_gates"]["total_rows"]["current"] == 2681
    assert report["current_gates"]["total_rows"]["remaining_to_target"] == 22319
    assert report["active_handoff"]["review_rows"] == 8
    assert report["active_handoff"]["checklist_rows_scanned"] == 5
    assert report["active_handoff"]["rows_by_task"] == {"microtext": 2, "visualdiff": 3}
    assert report["active_handoff"]["rows_by_split"]["test"] == 1
    assert report["acceptance_scenarios"][0]["acceptance_rate"] == 1.0
    assert report["acceptance_scenarios"][0]["total_rows_after"] == 2689
    assert report["acceptance_scenarios"][0]["total_rows_remaining_to_v2_min"] == 22311
    assert report["acceptance_scenarios"][0]["hidden_public_test_examples_after_known_test_rows"] == 383
    assert report["review_rows_counted_as_current_gold"] is False
    assert "Human returns" in report["next_human_actions"][0]


def test_gap_plan_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    gate_path = tmp_path / "gate.json"
    handoff_path = tmp_path / "handoff.json"
    out_json = tmp_path / "gap.json"
    out_md = tmp_path / "gap.md"
    write_json(
        gate_path,
        {
            "gates": {
                "total_rows": {"current": 100, "target": "25000-50000", "passes": False},
                "hidden_public_test_examples": {"current": 10, "target": 5000, "passes": False},
            }
        },
    )
    write_json(
        handoff_path,
        {
            "valid": True,
            "handoff_dir": {
                "valid": True,
                "path": "missing_handoff_folder",
                "packet_folders": 1,
                "worklist_review_rows": 20,
                "totals": {"checklist_rows": 0, "png_files": 0},
            },
        },
    )

    exit_code = build_v2_0_gap_closure_plan.main(
        [
            "--root",
            str(tmp_path),
            "--gate-report",
            str(gate_path),
            "--handoff-report",
            str(handoff_path),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
        ]
    )

    assert exit_code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    markdown = out_md.read_text(encoding="utf-8")
    assert payload["active_handoff"]["review_rows"] == 20
    assert "100% acceptance" in markdown
    assert "review rows are not current gold" in markdown
