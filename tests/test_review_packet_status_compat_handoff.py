from __future__ import annotations

import json
from pathlib import Path

from tools import review_packet_status


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_summarize_handoff_recurses_packets_and_agreement(tmp_path: Path) -> None:
    handoff = tmp_path / "returned_handoff"
    write(
        handoff / "01_packets" / "p01" / "pack" / "microtext_validation_checklist.csv",
        "candidate_id,review_status,proposed_text,corrected_text\n"
        "m1,accepted,R12,\n"
        "m2,,C7,\n",
    )
    write(
        handoff / "01_packets" / "p02" / "visualdiff_validation_checklist.csv",
        "pair_id,human_status,human_description\n"
        "v1,edit,\n"
        "v2,needs_full_page,\n",
    )
    write(
        handoff / "02_agreement" / "reviewer_a_checklist.csv",
        "id,answer_correct,bbox_correct,accept_reject,notes\n"
        "a1,yes,yes,accept,\n"
        "a2,,,,\n",
    )

    report = review_packet_status.summarize_handoff(handoff)

    assert report["packet_roots"] == 2
    assert report["agreement_files"] == 1
    assert report["totals"]["files"] == 3
    assert report["totals"]["rows"] == 6
    assert report["totals"]["blank_rows"] == 2
    assert report["totals"]["issues"] == 1
    assert report["complete"] is False
    assert {item["packet_id"] for item in report["packets"]} == {"p01", "p02"}
    agreement = report["agreement"][0]
    assert agreement["kind"] == "agreement_audit"
    assert agreement["status_counts"]["blank"] == 1


def test_handoff_status_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    handoff = tmp_path / "returned_handoff"
    write(
        handoff / "01_packets" / "p01" / "microtext_validation_checklist.csv",
        "candidate_id,review_status,proposed_text,corrected_text\n"
        "m1,accepted,R12,\n",
    )
    out_json = tmp_path / "status.json"
    out_md = tmp_path / "status.md"

    exit_code = review_packet_status.main(
        [
            "--handoff-root",
            str(handoff),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
        ]
    )

    assert exit_code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["complete"] is True
    assert payload["totals"]["rows"] == 1
    assert "Human Handoff Status" in out_md.read_text(encoding="utf-8")
