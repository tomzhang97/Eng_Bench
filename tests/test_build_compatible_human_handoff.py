from __future__ import annotations

import io
import zipfile
from pathlib import Path

from tools.build_compatible_human_handoff import build_from_aggregate, make_split_zips, make_zip


def make_inner_zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buffer.getvalue()


def test_compatible_handoff_shortens_paths_and_avoids_nested_zips(tmp_path: Path) -> None:
    packet = make_inner_zip(
        {
            (
                "packet_root/01_current_5_25_packet/derived/human_adjudication/"
                "2026-05-18_v1_expansion_handoff/review_packs/"
                "very_long_original_review_pack_name/crops/crop.png"
            ): "png",
            "packet_root/03_new_machine_review_packs/source_pack/manifest.jsonl": "{}\n",
        }
    )
    agreement = make_inner_zip({"agreement_root/reviewer_a_checklist.csv": "id,status\n1,\n"})
    aggregate = tmp_path / "aggregate.zip"
    with zipfile.ZipFile(aggregate, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle/01_review_packets/p01_example.zip", packet)
        zf.writestr("bundle/02_agreement_audit/agreement.zip", agreement)

    output_dir = tmp_path / "compat"
    summary = build_from_aggregate(tmp_path, aggregate, output_dir)
    zip_stats = make_zip(output_dir, tmp_path / "compat.zip")
    split_stats = make_split_zips(output_dir, tmp_path / "splits")

    assert summary["packet_folders"] == 1
    assert summary["agreement_included"] is True
    assert (output_dir / "01_packets" / "p01" / "current" / "packs" / "pack01" / "crops" / "crop.png").exists()
    assert zip_stats.crc_ok
    assert zip_stats.nested_zip_entries == 0
    assert zip_stats.max_internal_path_length < 160
    assert split_stats
    assert all(stat.crc_ok and stat.nested_zip_entries == 0 for stat in split_stats)
