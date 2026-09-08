#!/usr/bin/env python3
"""Verify a flat Eng_Bench human handoff before delivery."""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for key in ("href", "src"):
            value = values.get(key)
            if value:
                self.links.append(value)


def csv_rows(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def workbook_valid(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None and "[Content_Types].xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def broken_html_links(root: Path) -> list[dict[str, str]]:
    broken: list[dict[str, str]] = []
    for html_path in sorted(root.rglob("*.html")):
        parser = LinkCollector()
        parser.feed(html_path.read_text(encoding="utf-8"))
        for link in parser.links:
            parsed = urlsplit(link)
            if parsed.scheme or link.startswith("#") or not parsed.path:
                continue
            target = (html_path.parent / unquote(parsed.path)).resolve()
            if not target.is_file():
                broken.append(
                    {
                        "html": html_path.relative_to(root).as_posix(),
                        "link": link,
                    }
                )
    return broken


def verify_packet(root: Path) -> dict[str, object]:
    agreement_csv = root / "01_agreement_reviewer_b/reviewer_b_checklist.csv"
    agreement_xlsx = root / "01_agreement_reviewer_b/reviewer_b_checklist.xlsx"
    translation_csv = (
        root
        / "02_visualdiff_english_confirmation"
        / "visualdiff_english_confirmation_checklist.csv"
    )
    translation_xlsx = (
        root
        / "02_visualdiff_english_confirmation"
        / "visualdiff_english_confirmation_checklist.xlsx"
    )
    required = [
        root / "README_FIRST_ZH.md",
        root / "RETURN_FILES.md",
        agreement_csv,
        agreement_xlsx,
        root / "01_agreement_reviewer_b/index.html",
        translation_csv,
        translation_xlsx,
        root / "02_visualdiff_english_confirmation/index.html",
    ]
    missing_required = [
        path.relative_to(root).as_posix() for path in required if not path.is_file()
    ]
    agreement_rows = csv_rows(agreement_csv) if agreement_csv.is_file() else -1
    translation_rows = csv_rows(translation_csv) if translation_csv.is_file() else -1
    nested_zips = [
        path.relative_to(root).as_posix() for path in sorted(root.rglob("*.zip"))
    ]
    broken_links = broken_html_links(root)
    workbook_results = {
        agreement_xlsx.relative_to(root).as_posix(): workbook_valid(agreement_xlsx)
        if agreement_xlsx.is_file()
        else False,
        translation_xlsx.relative_to(root).as_posix(): workbook_valid(translation_xlsx)
        if translation_xlsx.is_file()
        else False,
    }
    png_files = list(root.rglob("*.png"))
    failures = []
    if missing_required:
        failures.append("missing_required_files")
    if agreement_rows != 185:
        failures.append("agreement_row_count")
    if translation_rows != 77:
        failures.append("translation_row_count")
    if nested_zips:
        failures.append("nested_zip")
    if broken_links:
        failures.append("broken_html_links")
    if not all(workbook_results.values()):
        failures.append("invalid_workbook")
    if len(png_files) < 262:
        failures.append("insufficient_evidence_images")

    return {
        "root": root.as_posix(),
        "agreement_rows": agreement_rows,
        "translation_rows": translation_rows,
        "total_human_rows": agreement_rows + translation_rows,
        "png_files": len(png_files),
        "html_files": len(list(root.rglob("*.html"))),
        "nested_zips": nested_zips,
        "missing_required": missing_required,
        "broken_html_links": broken_links,
        "workbooks_valid": workbook_results,
        "failures": failures,
        "passed": not failures,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a flat human handoff packet.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report = verify_packet(root)
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = root / output
        write_report(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
