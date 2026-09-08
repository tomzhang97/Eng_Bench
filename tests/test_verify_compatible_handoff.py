from __future__ import annotations

import zipfile
from pathlib import Path

from tools.verify_compatible_handoff import verify_handoff_dir, verify_split_dir, verify_zip


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_zip(source: Path, output: Path, root: str = "EB_HV_0615") -> None:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zf.write(path, f"{root}/{path.relative_to(source).as_posix()}")


def test_verify_compatible_handoff_accepts_flat_short_layout(tmp_path: Path) -> None:
    handoff = tmp_path / "handoff"
    write(handoff / "README_FIRST.md", "start")
    write(
        handoff / "PACKET_WORKLIST.csv",
        "packet,folder,review_rows\np01,01_packets/p01,2\n",
    )
    write(handoff / "PACK_MAP.csv", "packet,short_pack,original_pack\np01,pack01,long\n")
    write(
        handoff / "PATH_MAP.csv",
        "original_relative_path,compat_relative_path\nold,01_packets/p01/crops/a.png\n",
    )
    write(handoff / "01_packets" / "p01" / "review_checklist.csv", "id,status\n1,\n2,\n")
    (handoff / "01_packets" / "p01" / "crops").mkdir(parents=True)
    (handoff / "01_packets" / "p01" / "crops" / "a.png").write_bytes(b"png")
    write(handoff / "01_packets" / "p01" / "manifest.jsonl", "{}\n")
    write(handoff / "02_agreement" / "reviewer_a_checklist.csv", "id,status\n1,\n")

    zip_path = tmp_path / "handoff.zip"
    make_zip(handoff, zip_path)
    split_dir = tmp_path / "splits"
    split_dir.mkdir()
    split_zip = split_dir / "part01.zip"
    make_zip(handoff, split_zip)
    write(split_dir / "SPLIT_ZIP_MANIFEST.csv", "zip\npart01.zip\n")

    dir_report = verify_handoff_dir(handoff, 160)
    zip_report = verify_zip(zip_path, 160)
    split_report = verify_split_dir(split_dir, 160)

    assert dir_report["valid"]
    assert dir_report["packet_folders"] == 1
    assert dir_report["worklist_review_rows"] == 2
    assert zip_report["valid"]
    assert zip_report["nested_zip_entries"] == 0
    assert split_report["valid"]


def test_verify_zip_rejects_nested_zip(tmp_path: Path) -> None:
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inner.txt", "x")
    handoff_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(handoff_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("EB_HV_0615/README_FIRST.md", "start")
        zf.write(nested, "EB_HV_0615/nested.zip")

    report = verify_zip(handoff_zip, 160)

    assert not report["valid"]
    assert report["nested_zip_entries"] == 1
