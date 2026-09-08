from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from tools import build_next_source_conversion_batch


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fieldnames or list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_batch_selects_machine_ready_release_candidates(tmp_path: Path) -> None:
    candidate_fields = [
        "candidate_id",
        "domain",
        "task_fit",
        "rights_tier",
        "source_url",
        "source_family",
        "revision_family_potential",
        "annotation_yield",
        "priority_bucket",
        "notes",
        "priority_score",
    ]
    write_csv(
        tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
        [
            {
                "candidate_id": "civil_101",
                "domain": "civil",
                "task_fit": "visualdiff,microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/civil",
                "source_family": "civil_family",
                "revision_family_potential": "high",
                "annotation_yield": "high",
                "priority_bucket": "A",
                "notes": "Strong civil source.",
                "priority_score": "12",
            },
            {
                "candidate_id": "pcb_101",
                "domain": "pcb_schematic",
                "task_fit": "visualdiff",
                "rights_tier": "open_hardware_candidate",
                "source_url": "https://example.test/pcb",
                "source_family": "pcb_family",
                "revision_family_potential": "high",
                "annotation_yield": "high",
                "priority_bucket": "A",
                "notes": "Already packaged.",
                "priority_score": "10",
            },
            {
                "candidate_id": "pid_101",
                "domain": "pid",
                "task_fit": "microtext",
                "rights_tier": "internal_only_candidate",
                "source_url": "https://example.test/pid",
                "source_family": "pid_family",
                "revision_family_potential": "medium",
                "annotation_yield": "high",
                "priority_bucket": "B",
                "notes": "Useful but not release-safe.",
                "priority_score": "11",
            },
            {
                "candidate_id": "arch_101",
                "domain": "architectural",
                "task_fit": "microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/arch",
                "source_family": "arch_family",
                "revision_family_potential": "medium",
                "annotation_yield": "medium",
                "priority_bucket": "B",
                "notes": "Second intake tier.",
                "priority_score": "9",
            },
        ],
        fieldnames=candidate_fields,
    )
    write_csv(
        tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
        [
            {
                "candidate_id": "civil_101",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            },
            {
                "candidate_id": "pcb_101",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_open_hardware_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            },
            {
                "candidate_id": "pid_101",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_internal_only_candidate",
                "release_posture": "internal_only_candidate",
                "next_action": "prototype_only",
                "notes": "hold",
            },
            {
                "candidate_id": "arch_101",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_second",
                "notes": "later",
            },
        ],
    )
    write_csv(
        tmp_path / "docs" / "SOURCE_CONVERSION_CANDIDATE_QUEUE.csv",
        [
            {
                "candidate_id": "civil_101",
                "next_step": "import_render_extract",
                "imported_or_staged": "False",
                "open_review_rows": "0",
                "packeted_open_review_rows": "0",
                "fresh_open_review_rows": "0",
                "rights_blocked_open_review_rows": "0",
                "linked_local_source_count": "0",
                "linked_local_gold_rows": "0",
                "priority_score": "100",
            },
            {
                "candidate_id": "pcb_101",
                "next_step": "await_human_return",
                "imported_or_staged": "True",
                "open_review_rows": "100",
                "packeted_open_review_rows": "100",
                "fresh_open_review_rows": "0",
                "rights_blocked_open_review_rows": "0",
                "linked_local_source_count": "2",
                "linked_local_gold_rows": "0",
                "priority_score": "90",
            },
            {
                "candidate_id": "pid_101",
                "next_step": "rights_review_or_hold",
                "imported_or_staged": "False",
                "open_review_rows": "0",
                "packeted_open_review_rows": "0",
                "fresh_open_review_rows": "0",
                "rights_blocked_open_review_rows": "0",
                "linked_local_source_count": "0",
                "linked_local_gold_rows": "0",
                "priority_score": "80",
            },
            {
                "candidate_id": "arch_101",
                "next_step": "import_render_extract",
                "imported_or_staged": "False",
                "open_review_rows": "0",
                "packeted_open_review_rows": "0",
                "fresh_open_review_rows": "0",
                "rights_blocked_open_review_rows": "0",
                "linked_local_source_count": "0",
                "linked_local_gold_rows": "0",
                "priority_score": "70",
            },
        ],
    )

    report = build_next_source_conversion_batch.build_report(tmp_path, batch_size=3)

    assert report["totals"]["selected_machine_ready"] == 2
    assert [row["candidate_id"] for row in report["selected_machine_batch"]] == [
        "civil_101",
        "arch_101",
    ]
    assert report["selected_machine_batch"][0]["recommended_next_step"] == "import_render_extract"
    assert report["totals"]["blocked_human_return"] == 1
    assert report["totals"]["blocked_rights_or_nonrelease"] == 1
    assert report["blocked_human_return"][0]["candidate_id"] == "pcb_101"
    assert report["blocked_rights_or_nonrelease"][0]["candidate_id"] == "pid_101"
    assert report["domain_coverage"]["selected"] == {"architectural": 1, "civil": 1}


def test_batch_keeps_domain_breadth_when_pid_is_available(tmp_path: Path) -> None:
    candidate_fields = [
        "candidate_id",
        "domain",
        "task_fit",
        "rights_tier",
        "source_url",
        "source_family",
        "revision_family_potential",
        "annotation_yield",
        "priority_bucket",
        "notes",
        "priority_score",
    ]
    write_csv(
        tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
        [
            {
                "candidate_id": "civil_hi",
                "domain": "civil",
                "task_fit": "visualdiff,microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/civil-hi",
                "source_family": "civil_hi",
                "revision_family_potential": "high",
                "annotation_yield": "high",
                "priority_bucket": "A",
                "notes": "top civil",
                "priority_score": "30",
            },
            {
                "candidate_id": "civil_lo",
                "domain": "civil",
                "task_fit": "visualdiff,microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/civil-lo",
                "source_family": "civil_lo",
                "revision_family_potential": "high",
                "annotation_yield": "high",
                "priority_bucket": "A",
                "notes": "second civil",
                "priority_score": "29",
            },
            {
                "candidate_id": "pid_ok",
                "domain": "pid",
                "task_fit": "microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/pid",
                "source_family": "pid_ok",
                "revision_family_potential": "low",
                "annotation_yield": "medium",
                "priority_bucket": "B",
                "notes": "available pid",
                "priority_score": "1",
            },
        ],
        fieldnames=candidate_fields,
    )
    write_csv(
        tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
        [
            {
                "candidate_id": "civil_hi",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            },
            {
                "candidate_id": "civil_lo",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            },
            {
                "candidate_id": "pid_ok",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            },
        ],
    )

    report = build_next_source_conversion_batch.build_report(tmp_path, batch_size=2)

    assert [row["candidate_id"] for row in report["selected_machine_batch"]] == [
        "civil_hi",
        "pid_ok",
    ]
    assert report["domain_coverage"]["selected"] == {"civil": 1, "pid": 1}


def test_batch_cli_writes_json_markdown_and_csv(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
        [
            {
                "candidate_id": "mech_101",
                "domain": "mechanical_cad",
                "task_fit": "microtext",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/mech",
                "source_family": "mech_family",
                "revision_family_potential": "low",
                "annotation_yield": "medium",
                "priority_bucket": "A",
                "notes": "candidate",
                "priority_score": "8",
            }
        ],
    )
    write_csv(
        tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
        [
            {
                "candidate_id": "mech_101",
                "validation_date": "2026-06-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            }
        ],
    )
    out_json = tmp_path / "batch.json"
    out_md = tmp_path / "batch.md"
    out_csv = tmp_path / "batch.csv"

    exit_code = build_next_source_conversion_batch.main(
        [
            "--root",
            str(tmp_path),
            "--batch-size",
            "5",
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
            "--output-csv",
            str(out_csv),
        ]
    )

    assert exit_code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    markdown = out_md.read_text(encoding="utf-8")
    with out_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert payload["selected_machine_batch"][0]["candidate_id"] == "mech_101"
    assert "Next Source Conversion Batch" in markdown
    assert rows[0]["candidate_id"] == "mech_101"


def test_batch_cli_default_outputs_follow_date_label(tmp_path: Path) -> None:
    write_csv(
        tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
        [
            {
                "candidate_id": "mech_202",
                "domain": "mechanical_cad",
                "task_fit": "visualdiff",
                "rights_tier": "public_candidate",
                "source_url": "https://example.test/mech-202",
                "source_family": "mech_202",
                "revision_family_potential": "high",
                "annotation_yield": "medium",
                "priority_bucket": "A",
                "notes": "dated output fixture",
                "priority_score": "8",
            }
        ],
    )
    write_csv(
        tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
        [
            {
                "candidate_id": "mech_202",
                "validation_date": "2026-07-01",
                "browser_result": "loaded",
                "source_validity": "validated_public_candidate",
                "release_posture": "release_candidate",
                "next_action": "intake_first",
                "notes": "validated",
            }
        ],
    )

    exit_code = build_next_source_conversion_batch.main(
        ["--root", str(tmp_path), "--date-label", "2026-07-01", "--batch-size", "1"]
    )

    assert exit_code == 0
    output_root = tmp_path / "derived" / "quality"
    assert (output_root / "next_source_conversion_batch_2026-07-01.json").exists()
    assert (output_root / "next_source_conversion_batch_2026-07-01.md").exists()
    assert (output_root / "next_source_conversion_batch_2026-07-01.csv").exists()


class FreshReadinessQueueTests(unittest.TestCase):
    def test_batch_prefers_latest_dated_readiness_over_stale_docs_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            write_csv(
                tmp_path / "SOURCE_CANDIDATES_RANKED.csv",
                [
                    {
                        "candidate_id": "pid_041",
                        "domain": "pid",
                        "task_fit": "microtext",
                        "rights_tier": "public_candidate",
                        "source_url": "https://example.test/pid-041",
                        "source_family": "pid_041",
                        "revision_family_potential": "low",
                        "annotation_yield": "medium",
                        "priority_bucket": "A",
                        "notes": "Already imported.",
                        "priority_score": "10",
                    }
                ],
            )
            write_csv(
                tmp_path / "SOURCE_CANDIDATE_VALIDATION.csv",
                [
                    {
                        "candidate_id": "pid_041",
                        "validation_date": "2026-06-01",
                        "browser_result": "loaded",
                        "source_validity": "validated_public_candidate",
                        "release_posture": "release_candidate",
                        "next_action": "intake_first",
                        "notes": "validated",
                    }
                ],
            )
            stale = {
                "candidate_id": "pid_041",
                "next_step": "import_render_extract",
                "imported_or_staged": "False",
                "open_review_rows": "0",
                "packeted_open_review_rows": "0",
                "fresh_open_review_rows": "0",
                "rights_blocked_open_review_rows": "0",
                "linked_local_source_count": "0",
                "linked_local_gold_rows": "0",
                "priority_score": "100",
            }
            fresh = {
                **stale,
                "next_step": "human_review_partial_packeted",
                "imported_or_staged": "True",
                "open_review_rows": "82",
                "fresh_open_review_rows": "72",
                "linked_local_source_count": "4",
                "linked_local_gold_rows": "2",
            }
            write_csv(tmp_path / "docs" / "SOURCE_CONVERSION_CANDIDATE_QUEUE.csv", [stale])
            write_csv(
                tmp_path
                / "derived"
                / "quality"
                / "source_conversion_candidates_2026-06-30.csv",
                [fresh],
            )

            report = build_next_source_conversion_batch.build_report(tmp_path, batch_size=5)

            self.assertEqual(report["totals"]["selected_machine_ready"], 0)
            self.assertEqual(report["blocked_human_return"][0]["candidate_id"], "pid_041")

    def test_batch_deduplicates_candidates_with_the_same_source_url(self) -> None:
        rows = [
            {
                "candidate_id": "mech_043",
                "domain": "mechanical_cad",
                "source_url": "https://github.com/VoronDesign/Voron-Trident",
                "planning_score": 120,
                "priority_score": 20,
                "release_safe": True,
                "validation_next_action": "intake_first",
                "recommended_next_step": "import_render_extract",
            },
            {
                "candidate_id": "mech_011",
                "domain": "mechanical_cad",
                "source_url": "https://github.com/vorondesign/voron-trident/",
                "planning_score": 110,
                "priority_score": 10,
                "release_safe": True,
                "validation_next_action": "intake_first",
                "recommended_next_step": "import_render_extract",
            },
            {
                "candidate_id": "pcb_024",
                "domain": "pcb_schematic",
                "source_url": "https://github.com/espressif/esp-dev-kits",
                "planning_score": 100,
                "priority_score": 15,
                "release_safe": True,
                "validation_next_action": "intake_first",
                "recommended_next_step": "import_render_extract",
            },
        ]

        selected = build_next_source_conversion_batch.select_batch(rows, batch_size=3)

        self.assertEqual(
            [row["candidate_id"] for row in selected],
            ["mech_043", "pcb_024"],
        )
