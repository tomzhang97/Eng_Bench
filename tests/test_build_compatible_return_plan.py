from __future__ import annotations

import json
from pathlib import Path

from tools import build_compatible_return_plan


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_return_plan_routes_compat_packets_and_flags_uncovered_checklists(tmp_path: Path) -> None:
    handoff = tmp_path / "returned" / "EB_HV_0615"
    write(
        handoff / "PACKET_WORKLIST.csv",
        "packet,packet_id,priority,review_rows,folder,intended_use\n"
        "p01,parallel,1,10,01_packets/p01,old plus extra\n"
        "p02,next,2,5,01_packets/p02,next batch\n",
    )
    write(handoff / "01_packets" / "p01" / "current" / "microtext_caltrans_bridge_standard_details_validation_checklist.csv", "")
    write(
        handoff / "01_packets" / "p01" / "current" / "visualdiff_train_description_rewrite_checklist.csv",
        "pair_id,human_status,human_description\nv1,,\n",
    )
    write(
        handoff / "01_packets" / "p01" / "current" / "02_visualdiff_train_pending_descriptions" / "validation_checklist.csv",
        "pair_id,human_status,human_description\nv1,,\n",
    )
    write(
        handoff
        / "01_packets"
        / "p01"
        / "extra"
        / "microtext_region_proposals_2026-06-03"
        / "microtext_region_proposals_validation_checklist.csv",
        "candidate_id,review_status,proposed_text,corrected_text\nm1,,,\n",
    )
    write(handoff / "01_packets" / "p02" / "NEXT_REVIEW_BATCH_MANIFEST.csv", "pack_name,checklist,source_jsonl\n")
    write(handoff / "02_agreement" / "sample_reference.csv", "id\n1\n")
    write(handoff / "02_agreement" / "reviewer_a_checklist.csv", "id\n1\n")
    write(handoff / "02_agreement" / "reviewer_b_checklist.csv", "id\n1\n")

    report = build_compatible_return_plan.build_plan(
        root=tmp_path,
        handoff_root=handoff,
        return_label="2026-06-15-test",
    )

    commands = "\n".join(step["command"] for step in report["steps"])
    assert "review_packet_status.py --handoff-root" in commands
    assert "process_v1_5_human_return.py" in commands
    assert "01_packets\\p01\\current" in commands
    assert "microtext_review_checklist.py apply" in commands
    assert "microtext_review_region_proposals_2026-06-03.jsonl" in commands
    assert "process_next_review_batch_return.py" in commands
    assert "01_packets\\p02" in commands
    assert "verify_agreement_audit_packet.py" in commands
    assert "agreement_audit.py" in commands
    assert "adjudication_queue.csv" in commands
    assert "adjudication_queue.md" in commands
    assert "\\\\verification" not in commands
    p01 = next(packet for packet in report["packets"] if packet["packet"] == "p01")
    assert p01["processor"] == "process_v1_5_human_return+auxiliary_checklist_apply"
    assert p01["duplicate_checklists"] == [
        "01_packets/p01/current/02_visualdiff_train_pending_descriptions/validation_checklist.csv"
    ]
    assert p01["uncovered_checklists"] == []
    assert report["totals"]["auxiliary_steps"] == 1
    assert report["totals"]["duplicate_checklists"] == 1
    assert report["totals"]["uncovered_checklists"] == 0


def test_return_plan_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    handoff = tmp_path / "returned"
    write(
        handoff / "PACKET_WORKLIST.csv",
        "packet,packet_id,priority,review_rows,folder,intended_use\n"
        "p02,next,2,5,01_packets/p02,next batch\n",
    )
    write(handoff / "01_packets" / "p02" / "NEXT_REVIEW_BATCH_MANIFEST.csv", "pack_name,checklist,source_jsonl\n")
    out_json = tmp_path / "plan.json"
    out_md = tmp_path / "plan.md"

    exit_code = build_compatible_return_plan.main(
        [
            "--root",
            str(tmp_path),
            "--handoff-root",
            str(handoff),
            "--return-label",
            "2026-06-15-test",
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
        ]
    )

    assert exit_code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["totals"]["processable_packets"] == 1
    assert "Compatible Return Processing Plan" in out_md.read_text(encoding="utf-8")
