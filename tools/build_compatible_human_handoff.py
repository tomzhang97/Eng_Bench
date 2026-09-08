#!/usr/bin/env python3
"""Build compatibility-first human handoff ZIPs from an aggregate handoff.

The aggregate handoff intentionally preserves packet ZIP provenance, but that
ZIP-of-ZIPs shape and very long internal paths are fragile for Windows Explorer
and chat-file transfer tools. This builder extracts verified packet ZIPs into a
flat, short-path layout and can also create several standalone split ZIPs.
It does not read or modify active gold annotations.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


ARCHIVE_ROOT = "EB_HV_0615"
PACKET_RE = re.compile(r"p(\d+)", re.IGNORECASE)
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}.*")


@dataclass(frozen=True)
class ZipStats:
    path: Path
    entries: int
    bytes: int
    crc_ok: bool
    bad_entry: str
    nested_zip_entries: int
    max_internal_path_length: int
    sha256: str


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def clean_part(part: str) -> str:
    return part.strip().replace("\\", "/").strip("/")


def safe_parts(name: str) -> list[str]:
    parts = [clean_part(part) for part in PurePosixPath(name.replace("\\", "/")).parts]
    clean = [part for part in parts if part and part not in (".", "..")]
    if len(clean) > 1:
        clean = clean[1:]
    return clean


def short_pack_name(original: str, mapping: dict[str, str]) -> str:
    if original not in mapping:
        mapping[original] = f"pack{len(mapping) + 1:02d}"
    return mapping[original]


def shorten_parts(parts: list[str], pack_mapping: dict[str, str]) -> list[str]:
    """Return stable, short, human-readable path components."""
    if not parts:
        return []

    rewritten: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        lower = part.lower()
        if (
            lower == "derived"
            and index + 2 < len(parts)
            and parts[index + 1].lower() == "human_adjudication"
            and DATE_DIR_RE.match(parts[index + 2])
        ):
            index += 3
            continue
        if lower == "01_current_5_25_packet":
            rewritten.append("current")
        elif lower == "02_status_and_queues":
            rewritten.append("status")
        elif lower == "03_new_machine_review_packs":
            rewritten.append("extra")
        elif lower == "review_packs":
            rewritten.append("packs")
        elif lower in {"full_pages", "full-page", "full_pages_old"}:
            rewritten.append("pages")
        else:
            rewritten.append(part)
        index += 1

    compact: list[str] = []
    for part in rewritten:
        if compact and compact[-1] == "packs":
            compact.append(short_pack_name(part, pack_mapping))
        else:
            compact.append(part)
    return compact


def detect_packet_label(inner_name: str, ordinal: int) -> str:
    match = PACKET_RE.search(inner_name)
    if match:
        return f"p{int(match.group(1)):02d}"
    return f"p{ordinal:02d}"


def is_agreement_zip(name: str) -> bool:
    return "agreement" in name.lower()


def extract_inner_zip(
    inner_bytes: bytes,
    destination: Path,
    packet_label: str,
    path_rows: list[dict[str, str]],
    pack_rows: list[dict[str, str]],
) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    pack_mapping: dict[str, str] = {}
    count = 0
    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as zf:
        for info in sorted(zf.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            parts = shorten_parts(safe_parts(info.filename), pack_mapping)
            if not parts:
                continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            path_rows.append(
                {
                    "packet": packet_label,
                    "original_path": info.filename,
                    "compat_path": target.relative_to(destination.parent.parent).as_posix(),
                }
            )
            count += 1
    for original, short in sorted(pack_mapping.items(), key=lambda item: item[1]):
        pack_rows.append({"packet": packet_label, "short_pack": short, "original_pack": original})
    return count


def read_manifest_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def write_root_docs(
    output_dir: Path,
    packet_rows: list[dict[str, str]],
    pack_rows: list[dict[str, str]],
    path_rows: list[dict[str, str]],
) -> None:
    readme = """# Eng_Bench Human Verification Handoff - Compatible ZIP

This is a compatibility rebuild. It contains no nested ZIP files and uses short
paths so Windows Explorer, WeChat, and ordinary unzip tools are less likely to
fail.

## Start Here

1. Work through `01_packets/` in priority order: `p01`, `p02`, ...
2. Open `PACKET_WORKLIST.csv` to track packet status and reviewer initials.
3. Inside each packet folder, open `PACKET_INFO.md`, then open
   `HUMAN_REVIEW_STEPS.md` if present.
4. Fill only the validation checklist CSV files. Do not rename files, move
   folders, or edit images.
5. Use crop images first. Use page/panel/full-page evidence only when the crop
   is ambiguous.

## Agreement Audit

`02_agreement/` requires TWO independent reviewers. Reviewer A fills only the
Reviewer A checklist, Reviewer B fills only the Reviewer B checklist, and the
reviewers must not discuss rows until both sheets are returned.

## Folder Maps

- `PACKET_WORKLIST.csv`: packet order and row counts.
- `PACK_MAP.csv`: maps short pack folders such as `pack01` to original names.
- `PATH_MAP.csv`: audit map from original paths to compatibility paths.
"""
    output_dir.joinpath("README_FIRST.md").write_text(readme, encoding="utf-8")
    write_csv(
        output_dir / "PACKET_WORKLIST.csv",
        packet_rows,
        ("packet", "source_zip", "assigned_path", "files", "status_when_done", "reviewer_initials"),
    )
    write_csv(output_dir / "PACK_MAP.csv", pack_rows, ("packet", "short_pack", "original_pack"))
    write_csv(output_dir / "PATH_MAP.csv", path_rows, ("packet", "original_path", "compat_path"))


def make_zip(source_dir: Path, zip_path: Path, archive_root: str = ARCHIVE_ROOT) -> ZipStats:
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, f"{archive_root}/{path.relative_to(source_dir).as_posix()}")
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
        max_name = max(names, key=len) if names else ""
        nested = [name for name in names if name.lower().endswith(".zip")]
    return ZipStats(
        path=zip_path,
        entries=len(names),
        bytes=zip_path.stat().st_size,
        crc_ok=bad is None,
        bad_entry=bad or "",
        nested_zip_entries=len(nested),
        max_internal_path_length=len(max_name),
        sha256=sha256_of(zip_path),
    )


def make_split_zips(output_dir: Path, split_dir: Path) -> list[ZipStats]:
    if split_dir.exists():
        shutil.rmtree(split_dir)
    split_dir.mkdir(parents=True)
    packet_dirs = sorted((output_dir / "01_packets").iterdir()) if (output_dir / "01_packets").exists() else []
    groups: list[tuple[str, list[Path]]] = []
    for packet_dir in packet_dirs:
        groups.append((packet_dir.name, [packet_dir]))
    agreement = output_dir / "02_agreement"
    if agreement.exists():
        groups.append(("agreement_audit", [agreement]))

    stats: list[ZipStats] = []
    for label, paths in groups:
        staging = split_dir / f"_stage_{label}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        for doc in ("README_FIRST.md", "PACKET_WORKLIST.csv", "PACK_MAP.csv", "PATH_MAP.csv"):
            src = output_dir / doc
            if src.exists():
                shutil.copy2(src, staging / doc)
        for src in paths:
            rel_parent = "02_agreement" if src.name == "02_agreement" else f"01_packets/{src.name}"
            dst = staging / rel_parent
            shutil.copytree(src, dst)
        zip_path = split_dir / f"Eng_Bench_HV_0615_{label}.zip"
        stats.append(make_zip(staging, zip_path))
        shutil.rmtree(staging)
    write_csv(
        split_dir / "SPLIT_ZIP_MANIFEST.csv",
        [
            {
                "zip": stat.path.name,
                "entries": str(stat.entries),
                "bytes": str(stat.bytes),
                "mb": f"{stat.bytes / (1024 * 1024):.2f}",
                "crc_ok": str(stat.crc_ok),
                "nested_zip_entries": str(stat.nested_zip_entries),
                "max_internal_path_length": str(stat.max_internal_path_length),
                "sha256": stat.sha256,
            }
            for stat in stats
        ],
        ("zip", "entries", "bytes", "mb", "crc_ok", "nested_zip_entries", "max_internal_path_length", "sha256"),
    )
    split_dir.joinpath("README_FOR_SPLIT_ZIPS.md").write_text(
        "These ZIPs are standalone files, not multipart archives. Extract each one directly.\n",
        encoding="utf-8",
    )
    return stats


def build_from_aggregate(root: Path, aggregate_zip: Path, output_dir: Path) -> dict[str, object]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    (output_dir / "01_packets").mkdir(parents=True)
    (output_dir / "02_agreement").mkdir(parents=True)

    packet_rows: list[dict[str, str]] = []
    pack_rows: list[dict[str, str]] = []
    path_rows: list[dict[str, str]] = []
    with zipfile.ZipFile(aggregate_zip) as aggregate:
        inner_zip_names = sorted(name for name in aggregate.namelist() if name.lower().endswith(".zip"))
        packet_ordinal = 1
        for name in inner_zip_names:
            inner_bytes = aggregate.read(name)
            if is_agreement_zip(name):
                files = extract_inner_zip(inner_bytes, output_dir / "02_agreement", "agreement", path_rows, pack_rows)
                packet_rows.append(
                    {
                        "packet": "agreement",
                        "source_zip": Path(name).name,
                        "assigned_path": "02_agreement",
                        "files": str(files),
                        "status_when_done": "",
                        "reviewer_initials": "",
                    }
                )
                continue
            label = detect_packet_label(name, packet_ordinal)
            packet_ordinal += 1
            assigned = f"01_packets/{label}"
            files = extract_inner_zip(inner_bytes, output_dir / assigned, label, path_rows, pack_rows)
            packet_rows.append(
                {
                    "packet": label,
                    "source_zip": Path(name).name,
                    "assigned_path": assigned,
                    "files": str(files),
                    "status_when_done": "",
                    "reviewer_initials": "",
                }
            )
    write_root_docs(output_dir, packet_rows, pack_rows, path_rows)
    return {
        "packet_folders": sum(1 for row in packet_rows if row["packet"] != "agreement"),
        "agreement_included": any(row["packet"] == "agreement" for row in packet_rows),
        "path_map_rows": len(path_rows),
        "pack_map_rows": len(pack_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--aggregate-zip", required=True)
    parser.add_argument("--output-dir", default="derived/human_adjudication/hv_0615_compat")
    parser.add_argument("--zip-output", default="derived/human_adjudication/Eng_Bench_human_verification_compat_2026-06-15.zip")
    parser.add_argument("--split-output-dir", default="")
    args = parser.parse_args()

    root = Path(args.root)
    aggregate_zip = root / args.aggregate_zip
    output_dir = root / args.output_dir
    summary = build_from_aggregate(root, aggregate_zip, output_dir)
    zip_stats = make_zip(output_dir, root / args.zip_output)
    split_stats = make_split_zips(output_dir, root / args.split_output_dir) if args.split_output_dir else []
    report = {
        **summary,
        "output_dir": str(output_dir),
        "zip_output": str(root / args.zip_output),
        "zip": zip_stats.__dict__ | {"path": str(zip_stats.path)},
        "split_zips": [stat.__dict__ | {"path": str(stat.path)} for stat in split_stats],
        "valid": zip_stats.crc_ok and zip_stats.nested_zip_entries == 0 and all(
            stat.crc_ok and stat.nested_zip_entries == 0 for stat in split_stats
        ),
    }
    report_path = root / "derived" / "quality" / "compatible_handoff_build_report_2026-06-15.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
