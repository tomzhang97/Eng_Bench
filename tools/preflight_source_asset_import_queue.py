#!/usr/bin/env python3
"""Preflight queued source assets before download/hash/render intake."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


USER_AGENT = "Eng-Bench-source-intake-preflight/1.0 (engineering-document benchmark)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSE_PATTERN = re.compile(r"^(cc0|cc[ -]by(?:[ -]sa)?[ -]\d|public domain|pd)", re.IGNORECASE)
OUTPUT_FIELDS = [
    "queue_rank",
    "candidate_id",
    "domain",
    "asset_kind",
    "import_action",
    "preflight_status",
    "ready_for_intake",
    "http_status",
    "content_type",
    "content_length",
    "error",
    "source_url",
    "page_url",
    "direct_asset_url",
    "resolved_direct_asset_url",
    "proposed_doc_id",
    "proposed_local_path",
    "license_short_name",
    "license_url",
    "artist",
    "commons_sha1",
    "rights_capture",
    "review_gate",
    "public_status",
    "license_note",
    "rights_evidence_url",
    "rights_evidence_path",
    "same_model_id",
    "doc_type",
    "version_json",
    "page_selection",
    "full_page_count",
    "notes",
]


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    url: str
    final_url: str
    http_status: int
    content_type: str
    content_length: int
    error: str


@dataclass(frozen=True)
class CommonsResult:
    ok: bool
    file_page_url: str
    direct_payload_url: str
    license_short_name: str
    license_url: str
    artist: str
    commons_sha1: str
    error: str


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


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(str(value or ""))).strip()


def commons_title_from_url(file_page_url: str) -> str:
    parsed = urlparse(file_page_url)
    raw_path = parsed.path
    if parsed.params:
        raw_path = f"{raw_path};{parsed.params}"
    path = unquote(raw_path)
    marker = "/wiki/"
    if marker not in path:
        return ""
    title = path.split(marker, 1)[1].replace("_", " ")
    return title if title.startswith("File:") else ""


def license_allowed(short_name: str) -> bool:
    return bool(ALLOWED_LICENSE_PATTERN.match(str(short_name or "").strip()))


def content_type_matches(asset_kind: str, content_type: str) -> bool:
    lower = str(content_type or "").lower()
    if not lower:
        return True
    if "text/html" in lower:
        return False
    if asset_kind == "pdf":
        return "pdf" in lower or "octet-stream" in lower
    if asset_kind == "image":
        return lower.startswith("image/") or "octet-stream" in lower
    if asset_kind == "archive":
        return "zip" in lower or "octet-stream" in lower
    if asset_kind == "cad":
        return "octet-stream" in lower or "step" in lower or "dxf" in lower or "dwg" in lower
    return True


def probe_url(url: str, timeout: int) -> ProbeResult:
    if not url:
        return ProbeResult(False, url, url, 0, "", 0, "missing_url")
    best_result: ProbeResult | None = None
    last_error = ""
    for method, headers in (
        ("HEAD", {}),
        ("GET", {"Range": "bytes=0-0"}),
    ):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, **headers}, method=method)
            with urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Range") or response.headers.get("Content-Length", "")
                content_length = parse_content_length(length)
                result = ProbeResult(
                    ok=200 <= int(response.status) < 400,
                    url=url,
                    final_url=response.geturl(),
                    http_status=int(response.status),
                    content_type=response.headers.get("Content-Type", ""),
                    content_length=content_length,
                    error="",
                )
                if result.ok:
                    best_result = result
                    if "text/html" not in result.content_type.lower():
                        return result
        except Exception as exc:  # pragma: no cover - network exceptions vary
            last_error = exc.__class__.__name__
    if best_result is not None:
        return best_result
    return ProbeResult(False, url, url, 0, "", 0, last_error)


def parse_content_length(value: str) -> int:
    value = str(value or "")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else 0


def resolve_commons_file(file_page_url: str, timeout: int) -> CommonsResult:
    title = commons_title_from_url(file_page_url)
    if not title:
        return CommonsResult(False, file_page_url, "", "", "", "", "", "missing_commons_title")
    params = urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|sha1|extmetadata",
        }
    )
    try:
        request = Request(f"{COMMONS_API}?{params}", headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # pragma: no cover - network exceptions vary
        return CommonsResult(False, file_page_url, "", "", "", "", "", exc.__class__.__name__)
    pages = payload.get("query", {}).get("pages", {})
    info = next(iter(pages.values()), {})
    imageinfo = (info.get("imageinfo") or [{}])[0]
    metadata = imageinfo.get("extmetadata") or {}
    short_name = strip_html((metadata.get("LicenseShortName") or {}).get("value", ""))
    license_url = strip_html((metadata.get("LicenseUrl") or {}).get("value", ""))
    artist = strip_html((metadata.get("Artist") or {}).get("value", ""))
    direct_url = str(imageinfo.get("url") or "")
    sha1 = str(imageinfo.get("sha1") or "")
    ok = bool(direct_url and short_name)
    return CommonsResult(
        ok=ok,
        file_page_url=file_page_url,
        direct_payload_url=direct_url,
        license_short_name=short_name,
        license_url=license_url,
        artist=artist,
        commons_sha1=sha1,
        error="" if ok else "missing_commons_imageinfo",
    )


def base_output(row: dict[str, str]) -> dict[str, Any]:
    return {
        "queue_rank": row.get("queue_rank", ""),
        "candidate_id": row.get("candidate_id", ""),
        "domain": row.get("domain", ""),
        "asset_kind": row.get("asset_kind", ""),
        "import_action": row.get("import_action", ""),
        "source_url": row.get("source_url", ""),
        "page_url": row.get("page_url", ""),
        "direct_asset_url": row.get("direct_asset_url", ""),
        "resolved_direct_asset_url": "",
        "proposed_doc_id": row.get("proposed_doc_id", ""),
        "proposed_local_path": row.get("proposed_local_path", ""),
        "rights_capture": row.get("rights_capture", ""),
        "review_gate": row.get("review_gate", ""),
        "public_status": row.get("public_status", ""),
        "license_note": row.get("license_note", ""),
        "rights_evidence_url": row.get("rights_evidence_url", ""),
        "rights_evidence_path": row.get("rights_evidence_path", ""),
        "same_model_id": row.get("same_model_id", ""),
        "doc_type": row.get("doc_type", ""),
        "version_json": row.get("version_json", ""),
        "page_selection": row.get("page_selection", ""),
        "full_page_count": row.get("full_page_count", ""),
        "notes": row.get("notes", ""),
        "http_status": 0,
        "content_type": "",
        "content_length": 0,
        "license_short_name": "",
        "license_url": "",
        "artist": "",
        "commons_sha1": "",
        "error": "",
        "ready_for_intake": False,
    }


def preflight_row(
    row: dict[str, str],
    *,
    timeout: int,
    no_network: bool,
    probe: Callable[[str, int], ProbeResult],
    commons_resolver: Callable[[str, int], CommonsResult],
) -> dict[str, Any]:
    out = base_output(row)
    if row.get("import_action") == "resolve_commons_license_and_payload":
        resolver_is_default = commons_resolver is resolve_commons_file
        if no_network and resolver_is_default:
            out["preflight_status"] = "not_checked_no_network"
            return out
        result = commons_resolver(row.get("source_url", ""), timeout)
        out.update(
            {
                "resolved_direct_asset_url": result.direct_payload_url,
                "license_short_name": result.license_short_name,
                "license_url": result.license_url,
                "artist": result.artist,
                "commons_sha1": result.commons_sha1,
                "error": result.error,
            }
        )
        if not result.ok:
            out["preflight_status"] = "blocked_commons_resolution"
        elif not license_allowed(result.license_short_name):
            out["preflight_status"] = "blocked_commons_license"
        else:
            out["preflight_status"] = "ready_for_intake"
            out["ready_for_intake"] = True
        return out

    direct_url = str(row.get("direct_asset_url") or "").strip()
    if not direct_url:
        out["preflight_status"] = "blocked_missing_direct_url"
        out["error"] = "missing_direct_asset_url"
        return out
    if no_network:
        out["preflight_status"] = "not_checked_no_network"
        return out
    result = probe(direct_url, timeout)
    out.update(
        {
            "http_status": result.http_status,
            "content_type": result.content_type,
            "content_length": result.content_length,
            "resolved_direct_asset_url": result.final_url or direct_url,
            "error": result.error,
        }
    )
    if result.ok and content_type_matches(row.get("asset_kind", ""), result.content_type):
        out["preflight_status"] = "ready_for_intake"
        out["ready_for_intake"] = True
    elif result.ok:
        out["preflight_status"] = "blocked_content_type_mismatch"
        out["error"] = f"content_type_mismatch:{result.content_type}"
    else:
        out["preflight_status"] = "blocked_unreachable"
    return out


def build_report(
    root: str | Path,
    *,
    queue_csv: str | Path,
    date_label: str | None = None,
    timeout: int = 30,
    no_network: bool = False,
    probe: Callable[[str, int], ProbeResult] = probe_url,
    commons_resolver: Callable[[str, int], CommonsResult] = resolve_commons_file,
) -> dict[str, Any]:
    root = Path(root)
    input_path = Path(queue_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    input_rows = read_csv(input_path)
    rows = [
        preflight_row(
            row,
            timeout=timeout,
            no_network=no_network,
            probe=probe,
            commons_resolver=commons_resolver,
        )
        for row in input_rows
    ]
    status_counts = Counter(str(row["preflight_status"]) for row in rows)
    action_counts = Counter(str(row["import_action"]) for row in rows)
    totals = {
        "date_label": date_label or date.today().isoformat(),
        "input_rows": len(input_rows),
        "ready_for_intake": sum(1 for row in rows if row["ready_for_intake"]),
        "blocked_rows": sum(1 for row in rows if not row["ready_for_intake"]),
        "not_checked_rows": status_counts.get("not_checked_no_network", 0),
        "commons_rows": action_counts.get("resolve_commons_license_and_payload", 0),
        "direct_asset_rows": len(input_rows) - action_counts.get("resolve_commons_license_and_payload", 0),
    }
    return {
        "date_label": totals["date_label"],
        "queue_csv": input_path.as_posix(),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "rows": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Source Asset Intake Preflight",
        "",
        f"- Date label: `{report['date_label']}`",
        f"- Queue CSV: `{report['queue_csv']}`",
        f"- Input rows: `{totals['input_rows']}`",
        f"- Ready for intake: `{totals['ready_for_intake']}`",
        f"- Blocked rows: `{totals['blocked_rows']}`",
        f"- Commons rows: `{totals['commons_rows']}`",
        f"- Direct asset rows: `{totals['direct_asset_rows']}`",
        "- Preflight only: do not merge unreviewed rows into gold.",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in report["status_counts"].items():
        lines.append(f"- {status}: `{count}`")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Queue | Candidate | Action | Status | HTTP | Type | Size | License | URL |",
            "| ---: | --- | --- | --- | ---: | --- | ---: | --- | --- |",
        ]
    )
    for row in report["rows"]:
        url = row["resolved_direct_asset_url"] or row["direct_asset_url"] or row["source_url"]
        lines.append(
            f"| {row['queue_rank']} | `{row['candidate_id']}` | {row['import_action']} | "
            f"{row['preflight_status']} | {row['http_status']} | {row['content_type']} | "
            f"{row['content_length']} | {row['license_short_name']} | {url} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This report does not download payloads or compute local hashes.",
            "- Rows marked ready still need exact download, SHA-256 capture, render/convert, candidate mining, and review-packet export.",
            "- Commons rows must preserve author, license URL, file-page URL, direct payload URL, and Commons SHA1 during intake.",
            "- Do not merge unreviewed rows into gold.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight a source asset import queue.")
    parser.add_argument("--root", default=".", help="Eng_Bench root")
    parser.add_argument("--date-label", default=date.today().isoformat())
    parser.add_argument("--queue-csv", default="derived/quality/source_asset_import_queue_2026-06-16.csv")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--output-json", default="derived/quality/source_asset_intake_preflight_2026-06-16.json")
    parser.add_argument("--output-md", default="derived/quality/source_asset_intake_preflight_2026-06-16.md")
    parser.add_argument("--output-csv", default="derived/quality/source_asset_intake_preflight_2026-06-16.csv")
    args = parser.parse_args(argv)

    root = Path(args.root)
    report = build_report(
        root,
        queue_csv=args.queue_csv,
        date_label=args.date_label,
        timeout=args.timeout,
        no_network=args.no_network,
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
