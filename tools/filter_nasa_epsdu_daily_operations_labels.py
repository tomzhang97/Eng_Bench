#!/usr/bin/env python3
"""Keep distinct engineering labels from the NASA EPSDU operations tables."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


EXPECTED_DOC_ID = "nasa_19810021977_epsdu_equipment_procurement"
EXPECTED_PAGES = {19, 20, 21, 22}


def normalized_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


LABEL_ALIASES_RAW: dict[str, tuple[str, str]] = {
    # Page 1: source-wide mass-flow headings.
    "OVERALL MASS FLOW - SILANE FALILITY": (
        "Overall Mass Flow - Silane Facility",
        "process_label",
    ),
    "HYDROGENATION MAGS FLOWS": ("Hydrogenation Mass Flows", "process_label"),
    "DISTILLATION MASS FLOWS": ("Distillation Mass Flows", "process_label"),
    "PYROLYSIS/MELTING MA-=. FLOWS": (
        "Pyrolysis/Melting Mass Flows",
        "process_label",
    ),
    "CONSUMED(I A/HR)": ("Consumed (lb/hr)", "process_label"),
    "PROFUCT(LB/HR)": ("Product (lb/hr)", "process_label"),
    "PRODUCT(LE/HR)": ("Product (lb/hr)", "process_label"),
    "WASTE(LB/HR)": ("Waste (lb/hr)", "process_label"),
    "RECYCLE(LA/HR)": ("Recycle (lb/hr)", "process_label"),
    "RECYCLE(LP/HR)": ("Recycle (lb/hr)", "process_label"),
    # Page 2: utilities, inventory, and hydrogenation detail.
    "UTILITIES USAGE": ("Utilities Usage", "process_label"),
    "C WATER": ("C Water", "process_label"),
    "THERMINOL =": ("Therminol", "process_label"),
    "REFRICERNT=": ("Refrigerant", "process_label"),
    "NAT GAS": ("Nat Gas", "process_label"),
    "ELECTRICAL-": ("Electrical", "process_label"),
    "NITROGEN": ("Nitrogen", "process_label"),
    "RAW MATERIALS- ON HAND": ("Raw Materials - On Hand", "process_label"),
    "HYDROGENATION DETAIIFT REPORT": (
        "Hydrogenation Detailed Report",
        "process_label",
    ),
    "ATOMIC MASS FLOW": ("Atomic Mass Flow", "process_label"),
    "HT EXCHANGER DTY ETU/HR": (
        "HT Exchanger Duty (BTU/hr)",
        "process_label",
    ),
    "VAPOR!ZOR": ("Vaporizer", "equipment_tag"),
    "SUPERHEATR=": ("Superheater", "equipment_tag"),
    "Q CONDENER=": ("Q Condenser", "equipment_tag"),
    "REACTOR": ("Reactor", "equipment_tag"),
    "W SETTLER": ("W Settler", "equipment_tag"),
    "STC TANK": ("STC Tank", "equipment_tag"),
    "MG SI BIN": ("MG Si Bin", "equipment_tag"),
    "PART SIZE =": ("Part Size", "process_label"),
    "RECYCLE FR=": ("Recycle Fr", "process_label"),
    "WASH FLOW": ("Wash Flow", "process_label"),
    "TELTA P": ("Delta P", "process_label"),
    "TCE TANT": ("TCS Tank", "equipment_tag"),
    # Page 3: distillation columns and redistribution reactors.
    "DISTILLATION AREA DETAIIFD REPORT": (
        "Distillation Area Detailed Report",
        "process_label",
    ),
    "ETRIPFERCOLUMN": ("Stripper Column", "equipment_tag"),
    "TCS COLUMN": ("TCS Column", "equipment_tag"),
    "DCS C ILMN": ("DCS Column", "equipment_tag"),
    "SIH4 CCLUMN": ("SiH4 Column", "equipment_tag"),
    "TOTAL FEED": ("Total Feed", "process_label"),
    "DISTILLATE": ("Distillate", "process_label"),
    "BOTTOMS": ("Bottoms", "process_label"),
    "REFLIIX RATE": ("Reflux Rate", "process_label"),
    "CONDENSER =": ("Condenser", "process_label"),
    "REBOILER": ("Reboiler", "process_label"),
    "SEPARATION=": ("Separation", "process_label"),
    "TCS REDISTRIBUTION RFALTOR": (
        "TCS Redistribution Reactor",
        "equipment_tag",
    ),
    "OCS REOISTRIBUTION RFACTOR": (
        "DCS Redistribution Reactor",
        "equipment_tag",
    ),
    "CONVERSION=": ("Conversion", "process_label"),
    # Page 4: pyrolysis/melting and waste treatment.
    "PYROLYSIS/MELTING DETAILED REPORT": (
        "Pyrolysis/Melting Detailed Report",
        "process_label",
    ),
    "MEL TERS": ("Melters", "equipment_tag"),
    "ASTF TREATMENT AREA DETAILED REPORT": (
        "Waste Treatment Area Detailed Report",
        "process_label",
    ),
    "WASTE BURNER #1": ("Waste Burner #1", "equipment_tag"),
    "WASTE BJRNER #2": ("Waste Burner #2", "equipment_tag"),
    "WASTF FURNER #3": ("Waste Burner #3", "equipment_tag"),
    "LILJID FURNER": ("Liquid Burner", "equipment_tag"),
    "THROUGHPIJT=": ("Throughput", "process_label"),
    "COMB AIR": ("Comb Air", "process_label"),
    "ARGON": ("Argon", "process_label"),
}
LABEL_ALIASES = {
    normalized_key(raw): value for raw, value in LABEL_ALIASES_RAW.items()
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    if header[12:16] != b"IHDR":
        raise ValueError(f"PNG is missing IHDR: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid PNG dimensions: {path}")
    return width, height


def parse_bbox(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = row.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def shortlist(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    confidence_floor: float = 0.77,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    held: list[dict[str, Any]] = []
    provisional: list[dict[str, Any]] = []
    hold_reasons: Counter[str] = Counter()
    image_sizes: dict[Path, tuple[int, int]] = {}
    seen_candidate_ids: set[str] = set()

    def hold(source_row: dict[str, Any], reason: str) -> None:
        output = dict(source_row)
        output["machine_qa_status"] = "machine_held_epsdu_operations_label_filter"
        output["machine_hold_reason"] = reason
        output["safe_to_merge_gold"] = False
        held.append(output)
        hold_reasons[reason] += 1

    for source_row in rows:
        row = dict(source_row)
        if str(row.get("doc_id") or "") != EXPECTED_DOC_ID:
            hold(row, "unexpected_doc_id")
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen_candidate_ids:
            hold(row, "missing_or_duplicate_candidate_id")
            continue
        seen_candidate_ids.add(candidate_id)
        try:
            page_index = int(row.get("page_index", -1))
        except (TypeError, ValueError):
            hold(row, "invalid_page_index")
            continue
        if page_index not in EXPECTED_PAGES:
            hold(row, "outside_selected_operations_pages")
            continue
        raw_text = " ".join(str(row.get("proposed_text") or "").strip().split())
        specification = LABEL_ALIASES.get(normalized_key(raw_text))
        if specification is None:
            hold(row, "unsupported_or_placeholder_detection")
            continue
        try:
            confidence = float(row.get("ocr_confidence"))
        except (TypeError, ValueError):
            hold(row, "missing_or_invalid_confidence")
            continue
        if confidence < confidence_floor:
            hold(row, "below_confidence_floor")
            continue
        bbox = parse_bbox(row)
        if bbox is None:
            hold(row, "invalid_bbox")
            continue
        image_value = str(row.get("image_path") or "").strip()
        image_path = (root / image_value).resolve()
        try:
            image_path.relative_to(root)
        except ValueError:
            hold(row, "image_path_outside_root")
            continue
        if not image_path.is_file():
            hold(row, "missing_page_image")
            continue
        try:
            if image_path not in image_sizes:
                image_sizes[image_path] = png_size(image_path)
            width, height = image_sizes[image_path]
        except (OSError, ValueError):
            hold(row, "invalid_page_image")
            continue
        if bbox[2] > width or bbox[3] > height:
            hold(row, "bbox_outside_page")
            continue

        canonical_text, category = specification
        output = dict(row)
        output["raw_text"] = raw_text
        output["machine_original_text"] = raw_text
        output["target_text"] = canonical_text
        output["proposed_text"] = canonical_text
        output["text_context"] = canonical_text
        output["category"] = category
        output["question_text"] = (
            "What equipment label is shown in this region?"
            if category == "equipment_tag"
            else "What engineering process label is shown in this region?"
        )
        output["review_status"] = "needs_review"
        output["promotion_state"] = "unreviewed_candidate"
        output["machine_original_category"] = row.get("category")
        output["machine_filter_classification"] = "visual_allowlisted_operations_label"
        output["machine_filter_confidence_floor"] = confidence_floor
        output["machine_qa_status"] = "operations_label_pass_pending_visual_qa"
        output["machine_qa_notes"] = (
            "Source-specific visual vocabulary mapping removed placeholder values and "
            "normalized a confirmed OCR distortion. Human review remains required."
        )
        output["safe_to_merge_gold"] = False
        provisional.append(output)

    provisional.sort(
        key=lambda row: (
            -float(row.get("ocr_confidence") or 0),
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    selected: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for row in provisional:
        key = normalized_key(str(row["proposed_text"]))
        if key in seen_labels:
            hold(row, "duplicate_canonical_label_in_source")
            continue
        seen_labels.add(key)
        selected.append(row)

    selected.sort(
        key=lambda row: (
            int(row.get("page_index", -1)),
            float(row["bbox"][1]),
            float(row["bbox"][0]),
        )
    )
    report = {
        "goal": "Gold v2.0 Global",
        "source_doc_id": EXPECTED_DOC_ID,
        "source_rows": len(rows),
        "selected_rows": len(selected),
        "held_rows": len(held),
        "selected_unique_canonical_labels": len(seen_labels),
        "selected_by_category": dict(
            sorted(Counter(str(row["category"]) for row in selected).items())
        ),
        "selected_by_page": dict(
            sorted(
                Counter(str(row["page_index"]) for row in selected).items(),
                key=lambda item: int(item[0]),
            )
        ),
        "hold_reasons": dict(sorted(hold_reasons.items())),
        "confidence_floor": confidence_floor,
        "placeholder_values_retained": 0,
        "safe_to_merge_gold": False,
    }
    return selected, held, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--selected-output", type=Path, required=True)
    parser.add_argument("--held-output", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--confidence-floor", type=float, default=0.77)
    args = parser.parse_args()

    root = args.root.resolve()
    input_path = (root / args.input).resolve()
    selected_path = (root / args.selected_output).resolve()
    held_path = (root / args.held_output).resolve()
    report_path = (root / args.report_json).resolve()
    selected, held, report = shortlist(
        read_jsonl(input_path),
        root=root,
        confidence_floor=args.confidence_floor,
    )
    write_jsonl(selected_path, selected)
    write_jsonl(held_path, held)
    report.update(
        {
            "input": input_path.as_posix(),
            "input_sha256": sha256(input_path),
            "selected_output": selected_path.as_posix(),
            "selected_sha256": sha256(selected_path),
            "held_output": held_path.as_posix(),
            "held_sha256": sha256(held_path),
        }
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
