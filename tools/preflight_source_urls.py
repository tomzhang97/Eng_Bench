#!/usr/bin/env python3
"""Preflight source URLs before writing Eng_Bench source importers."""
from __future__ import annotations

import argparse
import csv
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable


USER_AGENT = "curl/8.14.1 Eng-Bench-source-preflight/1.0"
OUTPUT_FIELDS = [
    "rank",
    "candidate_id",
    "domain",
    "task_fit",
    "source_url",
    "probe_url",
    "preflight_status",
    "http_status",
    "final_url",
    "content_type",
    "content_length",
    "import_strategy",
    "operator_next_step",
    "error",
]


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    status_code: int
    final_url: str
    content_type: str
    content_length: int
    error: str


Fetcher = Callable[[str, int], FetchResult]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def header_int(headers: Any, name: str) -> int:
    value = headers.get(name) if headers else None
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


def fetch_url(url: str, timeout: int) -> FetchResult:
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return FetchResult(
                    ok=200 <= int(response.status) < 400,
                    status_code=int(response.status),
                    final_url=str(response.geturl() or url),
                    content_type=str(response.headers.get("Content-Type") or ""),
                    content_length=header_int(response.headers, "Content-Length"),
                    error="",
                )
        except urllib.error.HTTPError as error:
            if method == "HEAD" and error.code in {403, 405, 429, 500, 501}:
                continue
            return FetchResult(
                ok=False,
                status_code=int(error.code),
                final_url=url,
                content_type=str(error.headers.get("Content-Type") or "") if error.headers else "",
                content_length=header_int(error.headers, "Content-Length"),
                error=f"http_error:{error.code}",
            )
        except Exception as error:  # noqa: BLE001 - preflight should record any network failure
            if method == "HEAD":
                continue
            return FetchResult(
                ok=False,
                status_code=0,
                final_url=url,
                content_type="",
                content_length=0,
                error=type(error).__name__,
            )
    return FetchResult(False, 0, url, "", 0, "unreachable")


def classify_strategy(url: str, content_type: str) -> tuple[str, str]:
    lowered_url = url.lower()
    lowered_type = content_type.lower()
    if lowered_url.endswith(".pdf") or "application/pdf" in lowered_type:
        return "direct_pdf", "download_exact_url_and_hash"
    if lowered_url.endswith(".zip") or "application/zip" in lowered_type:
        return "direct_archive", "download_hash_and_inspect_archive"
    if "commons.wikimedia.org/wiki/category:" in lowered_url:
        return "wikimedia_commons_category", "select_files_and_capture_per_file_license"
    if "commons.wikimedia.org/wiki/file:" in lowered_url:
        return "wikimedia_commons_file_page", "resolve_direct_file_url_and_capture_license"
    if "github.com" in lowered_url or "gitlab.com" in lowered_url:
        return "git_repository_page", "select_release_assets_or_repo_paths"
    if "loc.gov/pictures/search" in lowered_url:
        return "loc_search_page", "select_specific_items_and_capture_item_metadata"
    if "loc.gov/pictures/collection" in lowered_url:
        return "loc_collection_page", "select_specific_items_and_capture_item_metadata"
    if "text/html" in lowered_type or lowered_url.startswith("http"):
        return "html_index_or_product_page", "inspect_page_and_select_direct_assets"
    return "unknown", "manual_review"


def offline_result(url: str) -> FetchResult:
    return FetchResult(
        ok=False,
        status_code=0,
        final_url=url,
        content_type="",
        content_length=0,
        error="network_disabled",
    )


def preflight_row(
    row: dict[str, str],
    *,
    fetcher: Fetcher | None,
    timeout: int,
) -> dict[str, Any]:
    source_url = str(row.get("source_url") or "").strip()
    probe_url = str(
        row.get("download_url")
        or row.get("direct_asset_url")
        or row.get("resolved_direct_asset_url")
        or source_url
        or ""
    ).strip()
    if not probe_url:
        result = FetchResult(False, 0, "", "", 0, "missing_source_url")
        strategy, next_step = "missing_url", "fill_source_url"
        status = "failed"
    elif fetcher is None:
        result = offline_result(probe_url)
        strategy, next_step = classify_strategy(probe_url, "")
        status = "not_checked"
    else:
        result = fetcher(probe_url, timeout)
        strategy, next_step = classify_strategy(
            result.final_url or probe_url, result.content_type
        )
        status = "reachable" if result.ok else "failed"
    return {
        "rank": row.get("rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "task_fit": row.get("task_fit", ""),
        "source_url": source_url,
        "probe_url": probe_url,
        "preflight_status": status,
        "http_status": result.status_code,
        "final_url": result.final_url,
        "content_type": result.content_type,
        "content_length": result.content_length,
        "import_strategy": strategy,
        "operator_next_step": next_step,
        "error": result.error,
    }


def build_report(
    root: str | Path,
    *,
    input_csv: str | Path,
    fetcher: Fetcher | None = fetch_url,
    timeout: int = 20,
    date_label: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(input_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = [
        preflight_row(row, fetcher=fetcher, timeout=timeout)
        for row in read_csv(input_path)
    ]
    totals = {
        "date_label": date_label or date.today().isoformat(),
        "rows": len(rows),
        "reachable": sum(1 for row in rows if row["preflight_status"] == "reachable"),
        "failed": sum(1 for row in rows if row["preflight_status"] == "failed"),
        "not_checked": sum(1 for row in rows if row["preflight_status"] == "not_checked"),
    }
    strategy_counts: dict[str, int] = {}
    for row in rows:
        strategy = str(row.get("import_strategy") or "unknown")
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    return {
        "date_label": totals["date_label"],
        "input_csv": input_path.as_posix(),
        "totals": totals,
        "strategy_counts": dict(sorted(strategy_counts.items())),
        "rows": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source URL Preflight",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Input CSV: `{report['input_csv']}`",
        f"- Rows: `{totals['rows']}`",
        f"- Reachable: `{totals['reachable']}`",
        f"- Failed: `{totals['failed']}`",
        f"- Not checked: `{totals['not_checked']}`",
        "",
        "## Strategy Counts",
        "",
    ]
    for strategy, count in report["strategy_counts"].items():
        lines.append(f"- {strategy}: `{count}`")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Rank | Candidate | Status | HTTP | Strategy | Next Step | URL |",
            "| ---: | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in report["rows"]:
        lines.append(
            f"| {row['rank']} | `{row['candidate_id']}` | {row['preflight_status']} | "
            f"{row['http_status']} | {row['import_strategy']} | {row['operator_next_step']} | {row['probe_url']} |"
        )
    lines.extend(
        [
            "",
            "## Operator Notes",
            "",
            "- This preflight does not download, import, render, mine, or promote benchmark rows.",
            "- For HTML index, repository, category, and search pages, select specific direct assets before import.",
            "- For Wikimedia Commons rows, capture per-file license, author, file page URL, and direct payload URL.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight source URLs for a conversion batch.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument(
        "--input-csv",
        default="derived/quality/next_source_conversion_batch_2026-06-16.csv",
    )
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--no-network", action="store_true", help="Classify URLs without network checks.")
    parser.add_argument("--output-json", default="derived/quality/source_url_preflight_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_url_preflight_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_url_preflight_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    fetcher = None if args.no_network else fetch_url
    report = build_report(
        root,
        input_csv=args.input_csv,
        fetcher=fetcher,
        timeout=args.timeout,
        date_label=args.date_label,
    )
    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    output_csv = Path(args.output_csv)
    if not output_json.is_absolute():
        output_json = root / output_json
    if not output_md.is_absolute():
        output_md = root / output_md
    if not output_csv.is_absolute():
        output_csv = root / output_csv
    write_json(output_json, report)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report), encoding="utf-8")
    write_csv(output_csv, report["rows"])
    print(f"[OK] Wrote {output_json}")
    print(f"[OK] Wrote {output_md}")
    print(f"[OK] Wrote {output_csv}")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
