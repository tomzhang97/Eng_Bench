from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import build_current_return_plan


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["candidate_id", "review_status"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def make_indexed_packet(root: Path) -> Path:
    packet_root = root / "derived/human_adjudication/2026-06-16_demo_review"
    write_csv(
        packet_root / "demo_candidates_checklist.csv",
        [{"candidate_id": "cand_001", "review_status": ""}],
    )
    write_jsonl(
        packet_root / "review_packs/demo_candidates/manifest.jsonl",
        [{"candidate_id": "cand_001", "review_status": "needs_review"}],
    )
    index = {
        "date_label": "2026-06-16",
        "totals": {"packets": 1},
        "packets": [
            {
                "priority": 1,
                "packet_id": "demo",
                "packet_root": "derived/human_adjudication/2026-06-16_demo_review",
                "zip_path": "derived/human_adjudication/demo.zip",
                "ready_to_send": True,
                "checklist_files": 1,
                "checklist_rows": 1,
            }
        ],
    }
    index_path = root / "derived/quality/current_handoff_index_2026-06-16.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return index_path


def test_return_plan_maps_checklists_to_pack_manifests(tmp_path: Path) -> None:
    index_path = make_indexed_packet(tmp_path)

    report = build_current_return_plan.build_report(
        tmp_path,
        index_json=index_path,
        return_label="returned_2026-06-20",
        returned_root="<returned_handoff_root>",
    )

    assert report["totals"]["packets"] == 1
    assert report["totals"]["checklists"] == 1
    assert report["totals"]["processable_checklists"] == 1
    assert report["totals"]["uncovered_checklists"] == 0
    step = report["steps"][0]
    assert step["kind"] == "microtext_checklist_apply"
    assert "tools\\microtext_review_checklist.py apply" in step["command"]
    assert "--review-jsonl" in step["command"]
    assert "review_packs\\demo_candidates\\manifest.jsonl" in step["command"]
    assert "<returned_handoff_root>" in step["command"]


def test_cli_writes_return_plan_outputs(tmp_path: Path) -> None:
    index_path = make_indexed_packet(tmp_path)
    out_json = tmp_path / "plan.json"
    out_md = tmp_path / "plan.md"
    out_csv = tmp_path / "plan.csv"

    exit_code = build_current_return_plan.main(
        [
            "--root",
            str(tmp_path),
            "--index-json",
            str(index_path),
            "--return-label",
            "returned_2026-06-20",
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
        ]
    )

    assert exit_code == 0
    assert out_json.exists()
    assert out_md.exists()
    assert out_csv.exists()
    assert "Current Return Processing Plan" in out_md.read_text(encoding="utf-8")


def test_return_plan_includes_full_merge_gate_list(tmp_path: Path) -> None:
    index_path = make_indexed_packet(tmp_path)

    report = build_current_return_plan.build_report(
        tmp_path,
        index_json=index_path,
        return_label="returned_2026-06-20",
        returned_root="<returned_handoff_root>",
    )

    gates = "\n".join(report["post_return_gates"])
    assert "tools\\audit_unmerged_reviewed_rows.py --root ." in gates
    assert "tools\\audit_microtext_quality.py --root ." in gates
    assert "tools\\audit_question_leakage.py --root ." in gates
    assert "splits\\leakage_check.py --root ." in gates
    assert "tools\\audit_v2_0_gate.py --root ." in gates
    assert "tools\\validate_engbench_v2.py --root ." in gates
