#!/usr/bin/env python3
"""Import a curated Wikimedia Commons source list with provenance gates.

The importer never creates annotation rows. It registers only sources whose
license, payload hash, render, and (for SVG) text layer pass the configured
checks. Failed or duplicate sources remain visible in the import report.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "Eng-Bench-source-intake/1.0 (engineering-document benchmark; contact: maintainer)"
SUPPORTED_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg"}


def normalized_title(value: str) -> str:
    title = value.strip().replace("_", " ")
    if not title.lower().startswith("file:"):
        title = f"File:{title}"
    return " ".join(title.split()).casefold()


def slugify_title(title: str) -> str:
    stem = title.strip().removeprefix("File:").rsplit(".", 1)[0]
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_stem.lower()).strip("_")
    if not slug:
        raise ValueError(f"title cannot produce a stable document id: {title!r}")
    return f"wikimedia_{slug}"


def metadata_value(extmetadata: dict[str, Any], key: str) -> str:
    raw = str((extmetadata.get(key) or {}).get("value") or "")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def license_tier(short_name: str) -> str | None:
    normalized = " ".join(short_name.strip().lower().replace("_", " ").split())
    if not normalized:
        return None
    if normalized in {"cc0", "cc zero", "public domain", "pd", "pd-self"}:
        return "public_domain_commons_candidate" if "public" in normalized or normalized.startswith("pd") else "cc0_commons_candidate"
    if normalized.startswith("cc by") and not any(marker in normalized for marker in ("-nc", " nc", "-nd", " nd")):
        slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        return f"{slug}_commons_candidate"
    return None


def fetch(url: str, *, timeout: int = 90, attempts: int = 5) -> bytes:
    delay = 5.0
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
            retry_after = error.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            time.sleep(min(max(wait, 1.0), 90.0))
            delay = min(delay * 2, 60.0)
    raise RuntimeError("unreachable")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_source_plan(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"candidate_id", "title", "domain", "same_model_id"}
    missing = required - set(rows[0] if rows else [])
    if missing:
        raise ValueError(f"source plan is missing columns: {', '.join(sorted(missing))}")
    seen_titles: set[str] = set()
    seen_doc_ids: set[str] = set()
    normalized_rows: list[dict[str, str]] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        candidate_id = str(row.get("candidate_id") or "").strip()
        domain = str(row.get("domain") or "").strip()
        same_model_id = str(row.get("same_model_id") or "").strip()
        if not all((title, candidate_id, domain, same_model_id)):
            raise ValueError("every source-plan row requires candidate_id, title, domain, and same_model_id")
        title_key = normalized_title(title)
        doc_id = slugify_title(title)
        if title_key in seen_titles or doc_id in seen_doc_ids:
            raise ValueError(f"duplicate source-plan title or document id: {title}")
        seen_titles.add(title_key)
        seen_doc_ids.add(doc_id)
        normalized_rows.append(
            {
                "candidate_id": candidate_id,
                "title": title if title.lower().startswith("file:") else f"File:{title}",
                "domain": domain,
                "same_model_id": same_model_id,
                "doc_id": doc_id,
            }
        )
    return normalized_rows


def query_imageinfo(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    titles = "|".join(row["title"] for row in rows)
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "redirects": "1",
            "titles": titles,
            "prop": "imageinfo",
            "iiprop": "url|sha1|size|mime|extmetadata",
        }
    )
    payload = json.loads(fetch(f"{API}?{params}", timeout=90))
    pages = payload.get("query", {}).get("pages", {})
    result: dict[str, dict[str, Any]] = {}
    for page in pages.values():
        title = str(page.get("title") or "")
        infos = page.get("imageinfo") or []
        if title and infos:
            result[normalized_title(title)] = infos[0]
    for entry in payload.get("query", {}).get("normalized", []):
        source = normalized_title(str(entry.get("from") or ""))
        target = normalized_title(str(entry.get("to") or ""))
        if target in result:
            result[source] = result[target]
    for entry in payload.get("query", {}).get("redirects", []):
        source = normalized_title(str(entry.get("from") or ""))
        target = normalized_title(str(entry.get("to") or ""))
        if target in result:
            result[source] = result[target]
    return result


def run_tool(root: Path, arguments: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(
            f"tool failed: {' '.join(arguments)}\n{result.stdout[-500:]}\n{result.stderr[-500:]}"
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def existing_payload_hashes(root: Path) -> dict[str, list[str]]:
    hashes: dict[str, list[str]] = {}
    for row in read_jsonl(root / "manifest.jsonl"):
        if row.get("type") != "doc":
            continue
        sha256 = str(row.get("sha256") or "").strip().lower()
        doc_id = str(row.get("doc_id") or "").strip()
        if sha256 and doc_id:
            hashes.setdefault(sha256, []).append(doc_id)
    return hashes


def snapshot_mutable_files(root: Path, date_label: str) -> Path:
    snapshot = root / "derived" / "snapshots" / f"{date_label}-commons-source-import"
    snapshot.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.jsonl", "SOURCE_INVENTORY.csv"):
        source = root / name
        target = snapshot / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
    return snapshot


def upsert_inventory(root: Path, rows: list[dict[str, str]]) -> None:
    path = root / "SOURCE_INVENTORY.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        existing = list(reader)
    updates = {str(row.get("doc_id") or ""): row for row in rows}
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in existing:
        doc_id = str(row.get("doc_id") or "")
        update = updates.get(doc_id)
        if update is not None:
            if doc_id in seen:
                continue
            merged = dict(row)
            merged.update(update)
            output.append(merged)
            seen.add(doc_id)
        else:
            output.append(row)
    for doc_id, row in updates.items():
        if doc_id not in seen:
            output.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output)


def rendered_image_stats(path: Path) -> dict[str, float]:
    with Image.open(path) as image:
        grayscale = image.convert("L")
        histogram = grayscale.histogram()
        total = image.width * image.height
        return {
            "mean_gray": float(ImageStat.Stat(grayscale).mean[0]),
            "black_fraction": sum(histogram[:16]) / total,
        }


def textlayer_quality(path: Path) -> dict[str, int]:
    rows = read_jsonl(path)
    nondegenerate = 0
    for row in rows:
        bbox = row.get("bbox_px") or row.get("bbox") or []
        if (
            isinstance(bbox, list)
            and len(bbox) == 4
            and float(bbox[2]) - float(bbox[0]) > 2
            and float(bbox[3]) - float(bbox[1]) > 2
        ):
            nondegenerate += 1
    return {"text_spans": len(rows), "nondegenerate_text_spans": nondegenerate}


def sparse_svg_override_allowed(
    *,
    suffix: str,
    mean_gray: float,
    black_fraction: float,
    nondegenerate_text_spans: int,
    min_svg_spans: int,
    allow_sparse_svg: bool,
) -> bool:
    """Allow explicitly approved, text-bearing sparse SVGs past the white-page heuristic."""
    return (
        allow_sparse_svg
        and suffix == ".svg"
        and 253.0 < mean_gray <= 255.0
        and 0.0005 <= black_fraction <= 0.35
        and nondegenerate_text_spans >= max(1, min_svg_spans)
    )


def append_manifest(root: Path, rows: list[dict[str, Any]]) -> None:
    with (root / "manifest.jsonl").open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def import_sources(
    root: Path,
    source_rows: list[dict[str, str]],
    *,
    date_label: str,
    min_svg_spans: int,
    min_width: int,
    min_height: int,
    request_delay_seconds: float,
    allow_sparse_svg: bool,
    dry_run: bool,
) -> dict[str, Any]:
    imageinfo = query_imageinfo(source_rows)
    manifest = read_jsonl(root / "manifest.jsonl")
    existing_doc_ids = {str(row.get("doc_id") or "") for row in manifest if row.get("type") == "doc"}
    payload_hashes = existing_payload_hashes(root)
    imported: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    inventory_rows: list[dict[str, str]] = []
    manifest_rows: list[dict[str, Any]] = []
    staging_root = root / "derived" / "source_imports" / f"wikimedia_commons_{date_label}_staging"
    if not dry_run:
        staging_root.mkdir(parents=True, exist_ok=True)

    for source in source_rows:
        doc_id = source["doc_id"]
        title = source["title"]
        print(f"[SOURCE] {doc_id}: {title}", flush=True)
        if doc_id in existing_doc_ids:
            skipped.append({**source, "reason": "already_in_manifest"})
            print(f"[SKIP] {doc_id}: already_in_manifest", flush=True)
            continue
        info = imageinfo.get(normalized_title(title))
        if not info:
            held.append({**source, "reason": "commons_imageinfo_missing"})
            print(f"[HOLD] {doc_id}: commons_imageinfo_missing", flush=True)
            continue
        metadata = info.get("extmetadata") or {}
        license_name = metadata_value(metadata, "LicenseShortName")
        tier = license_tier(license_name)
        artist = metadata_value(metadata, "Artist") or "unknown Commons contributor"
        license_url = metadata_value(metadata, "LicenseUrl")
        direct_url = str(info.get("url") or "")
        suffix = Path(urllib.parse.urlparse(direct_url).path).suffix.lower()
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        base_record = {
            **source,
            "license_short_name": license_name,
            "license_url": license_url,
            "artist": artist,
            "commons_sha1": str(info.get("sha1") or ""),
            "direct_source_url": direct_url,
            "width": width,
            "height": height,
            "mime": str(info.get("mime") or ""),
        }
        if tier is None:
            held.append({**base_record, "reason": "license_not_allowlisted"})
            print(f"[HOLD] {doc_id}: license_not_allowlisted ({license_name or 'missing'})", flush=True)
            continue
        if not direct_url or suffix not in SUPPORTED_SUFFIXES:
            held.append({**base_record, "reason": "unsupported_or_missing_payload_url"})
            print(f"[HOLD] {doc_id}: unsupported_or_missing_payload_url", flush=True)
            continue
        if width < min_width or height < min_height:
            held.append({**base_record, "reason": "source_dimensions_below_threshold"})
            print(f"[HOLD] {doc_id}: source_dimensions_below_threshold ({width}x{height})", flush=True)
            continue
        if dry_run:
            imported.append({**base_record, "status": "metadata_pass_dry_run"})
            print(f"[PASS] {doc_id}: metadata_pass_dry_run", flush=True)
            continue

        stage_path = staging_root / f"{doc_id}{suffix}"
        final_rel = f"microtext/docs/{doc_id}{suffix}"
        final_path = root / final_rel
        try:
            expected_sha1 = str(info.get("sha1") or "").strip().lower()
            payload_path: Path | None = None
            for cached_path in (final_path, stage_path):
                if cached_path.is_file() and expected_sha1 and file_sha1(cached_path) == expected_sha1:
                    payload_path = cached_path
                    print(f"[CACHE] {doc_id}: Commons SHA-1 match", flush=True)
                    break
            if payload_path is None:
                if request_delay_seconds > 0:
                    time.sleep(request_delay_seconds)
                print(f"[FETCH] {doc_id}: {direct_url}", flush=True)
                stage_path.write_bytes(fetch(direct_url, timeout=120))
                payload_path = stage_path
            sha256 = file_sha256(payload_path)
            if sha256 in payload_hashes:
                payload_path.unlink()
                held.append(
                    {
                        **base_record,
                        "sha256": sha256,
                        "reason": "duplicate_payload",
                        "duplicate_doc_ids": payload_hashes[sha256],
                    }
                )
                print(f"[HOLD] {doc_id}: duplicate_payload", flush=True)
                continue
            stage_rel = payload_path.relative_to(root).as_posix()
            run_tool(
                root,
                [
                    "tools/01_render_pdf.py",
                    "--root",
                    ".",
                    "--doc_id",
                    doc_id,
                    "--pdf_relpath",
                    stage_rel,
                    "--dpi",
                    "300",
                    "--grayscale",
                ],
            )
            page_path = root / "derived" / "pages_300dpi" / doc_id / "page_000.png"
            image_quality = rendered_image_stats(page_path)
            mean_gray = image_quality["mean_gray"]
            black_fraction = image_quality["black_fraction"]
            textlayer_spans = 0
            nondegenerate_text_spans = 0
            textlayer_rel = ""
            renderer = "pymupdf"
            svg_compat_report_rel = ""
            if suffix == ".svg":
                run_tool(
                    root,
                    [
                        "tools/extract_svg_textlayer.py",
                        "--root",
                        ".",
                        "--doc-id",
                        doc_id,
                        "--svg",
                        stage_rel,
                        "--image",
                        f"derived/pages_300dpi/{doc_id}/page_000.png",
                    ],
                )
                textlayer_path = root / "derived" / "textlayer" / f"{doc_id}.jsonl"
                text_quality = textlayer_quality(textlayer_path)
                textlayer_spans = text_quality["text_spans"]
                nondegenerate_text_spans = text_quality["nondegenerate_text_spans"]
                textlayer_rel = textlayer_path.relative_to(root).as_posix()
                if black_fraction > 0.35 or nondegenerate_text_spans < min_svg_spans:
                    svg_compat_report_rel = (
                        f"derived/quality/{doc_id}_legacy_svg_render_{date_label}.json"
                    )
                    run_tool(
                        root,
                        [
                            "tools/render_legacy_svg.py",
                            "--root",
                            ".",
                            "--input",
                            stage_rel,
                            "--output",
                            f"derived/pages_300dpi/{doc_id}/page_000.png",
                            "--textlayer-output",
                            textlayer_rel,
                            "--report",
                            svg_compat_report_rel,
                            "--sanitized-svg-output",
                            f"derived/quality/{doc_id}_sanitized_{date_label}.svg",
                            "--dpi",
                            "300",
                        ],
                    )
                    renderer = "chromium_headless_svg_compat"
                    image_quality = rendered_image_stats(page_path)
                    mean_gray = image_quality["mean_gray"]
                    black_fraction = image_quality["black_fraction"]
                    text_quality = textlayer_quality(textlayer_path)
                    textlayer_spans = text_quality["text_spans"]
                    nondegenerate_text_spans = text_quality["nondegenerate_text_spans"]
            sparse_svg_override_used = sparse_svg_override_allowed(
                suffix=suffix,
                mean_gray=mean_gray,
                black_fraction=black_fraction,
                nondegenerate_text_spans=nondegenerate_text_spans,
                min_svg_spans=min_svg_spans,
                allow_sparse_svg=allow_sparse_svg,
            )
            if (
                mean_gray < 25.0 or mean_gray > 253.0 or black_fraction > 0.35
            ) and not sparse_svg_override_used:
                held.append(
                    {
                        **base_record,
                        "sha256": sha256,
                        "render_mean_gray": round(mean_gray, 2),
                        "render_black_fraction": round(black_fraction, 6),
                        "textlayer_spans": textlayer_spans,
                        "reason": "render_quality_hold",
                    }
                )
                print(f"[HOLD] {doc_id}: render_quality_hold mean={mean_gray:.2f}", flush=True)
                continue
            if suffix == ".svg" and nondegenerate_text_spans < min_svg_spans:
                held.append(
                    {
                        **base_record,
                        "sha256": sha256,
                        "render_mean_gray": round(mean_gray, 2),
                        "textlayer_spans": textlayer_spans,
                        "nondegenerate_text_spans": nondegenerate_text_spans,
                        "reason": "svg_textlayer_nondegenerate_below_threshold",
                    }
                )
                print(
                    f"[HOLD] {doc_id}: svg_textlayer_nondegenerate_below_threshold "
                    f"spans={nondegenerate_text_spans}",
                    flush=True,
                )
                continue

            final_path.parent.mkdir(parents=True, exist_ok=True)
            if payload_path != final_path:
                payload_path.replace(final_path)
            file_page = f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'), safe=':()')}"
            attribution_rel = f"microtext/docs/{doc_id}_ATTRIBUTION.md"
            (root / attribution_rel).write_text(
                "\n".join(
                    [
                        f"# {title.removeprefix('File:')} Attribution",
                        "",
                        f"- Author: {artist}",
                        f"- License: {license_name}" + (f" ({license_url})" if license_url else ""),
                        f"- Commons file page: {file_page}",
                        f"- Direct payload: {direct_url}",
                        f"- Commons SHA-1: `{info.get('sha1') or ''}`",
                        f"- Local SHA-256: `{sha256}`",
                        f"- Import date: {date_label}",
                        "",
                        "Preserve the attribution, source URL, exact payload hash, and license terms",
                        "when redistributing this source or benchmark evidence derived from it.",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            inventory_rows.append(
                {
                    "doc_id": doc_id,
                    "domain": source["domain"],
                    "task": "microtext",
                    "public_status": tier,
                    "source_path": final_rel,
                    "rendered_pages": "1",
                    "textlayer_spans": str(textlayer_spans),
                    "duplicate_payload_alias": "False",
                    "next_step": "mine_candidates_and_export_review" if suffix == ".svg" else "manual_region_mining",
                    "source_url": file_page,
                }
            )
            derived: dict[str, Any] = {
                "pages_dir": f"derived/pages_300dpi/{doc_id}",
                "rendered_source_path": f"derived/pages_300dpi/{doc_id}/page_000.png",
            }
            if textlayer_rel:
                derived["textlayer_jsonl"] = textlayer_rel
            if svg_compat_report_rel:
                derived["svg_compat_report"] = svg_compat_report_rel
            manifest_rows.append(
                {
                    "type": "doc",
                    "doc_id": doc_id,
                    "task": "microtext",
                    "source_candidate_id": source["candidate_id"],
                    "same_model_id": source["same_model_id"],
                    "domain": source["domain"],
                    "doc_type": "svg_process_diagram" if suffix == ".svg" else "raster_process_diagram",
                    "version": {"snapshot": f"commons_current_{date_label}", "imported": date_label},
                    "path": final_rel,
                    "sha256": sha256,
                    "pages": 1,
                    "render": {
                        "dpi": 300,
                        "colorspace": "rgb" if renderer == "chromium_headless_svg_compat" else "gray",
                        "rotate_cw90": False,
                        "renderer": renderer,
                        "sparse_svg_override_used": sparse_svg_override_used,
                    },
                    "derived": derived,
                    "source_url": file_page,
                    "direct_source_url": direct_url,
                    "public_status": tier,
                    "license_note": f"Commons metadata identifies {artist} under {license_name}; see attribution file.",
                    "attribution_path": attribution_rel,
                    "notes": (
                        "Review-only source import. No annotation row was promoted by the importer; "
                        f"textlayer_spans={textlayer_spans}; "
                        f"sparse_svg_override_used={sparse_svg_override_used}."
                    ),
                }
            )
            payload_hashes.setdefault(sha256, []).append(doc_id)
            imported.append(
                {
                    **base_record,
                    "sha256": sha256,
                    "render_mean_gray": round(mean_gray, 2),
                    "render_black_fraction": round(black_fraction, 6),
                    "textlayer_spans": textlayer_spans,
                    "nondegenerate_text_spans": nondegenerate_text_spans,
                    "renderer": renderer,
                    "sparse_svg_override_used": sparse_svg_override_used,
                    "local_path": final_rel,
                    "status": "imported_and_registered",
                }
            )
            print(
                f"[OK] {doc_id}: {license_name}; spans={textlayer_spans}; mean={mean_gray:.2f}",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - source failures must be reported and isolated
            held.append({**base_record, "reason": "import_exception", "error": str(error)})
            print(f"[HOLD] {doc_id}: import_exception: {error}", flush=True)

    snapshot_path = ""
    if not dry_run and (inventory_rows or manifest_rows):
        snapshot_path = snapshot_mutable_files(root, date_label).relative_to(root).as_posix()
        upsert_inventory(root, inventory_rows)
        append_manifest(root, manifest_rows)
    return {
        "date_label": date_label,
        "dry_run": dry_run,
        "allow_sparse_svg": allow_sparse_svg,
        "source_plan_rows": len(source_rows),
        "imported_count": len(imported),
        "held_count": len(held),
        "skipped_count": len(skipped),
        "snapshot_path": snapshot_path,
        "imported": imported,
        "held": held,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--date-label", required=True)
    parser.add_argument("--output-report", required=True)
    parser.add_argument("--min-svg-spans", type=int, default=8)
    parser.add_argument("--min-width", type=int, default=500)
    parser.add_argument("--min-height", type=int, default=200)
    parser.add_argument("--max-files", type=int, default=50)
    parser.add_argument("--only-doc-id", action="append", default=[])
    parser.add_argument("--request-delay-seconds", type=float, default=2.0)
    parser.add_argument(
        "--allow-sparse-svg",
        action="store_true",
        help=(
            "Allow explicitly selected sparse SVGs past the white-page heuristic only when "
            "they retain measurable ink and enough nondegenerate text spans."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    input_path = Path(args.input_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    rows = read_source_plan(input_path)
    if args.only_doc_id:
        requested = set(args.only_doc_id)
        rows = [row for row in rows if row["doc_id"] in requested]
        missing = requested - {row["doc_id"] for row in rows}
        if missing:
            parser.error(f"--only-doc-id not found in source plan: {', '.join(sorted(missing))}")
    rows = rows[: max(0, args.max_files)]
    report = import_sources(
        root,
        rows,
        date_label=args.date_label,
        min_svg_spans=max(0, args.min_svg_spans),
        min_width=max(1, args.min_width),
        min_height=max(1, args.min_height),
        request_delay_seconds=max(0.0, args.request_delay_seconds),
        allow_sparse_svg=args.allow_sparse_svg,
        dry_run=args.dry_run,
    )
    output = Path(args.output_report)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("source_plan_rows", "imported_count", "held_count", "skipped_count")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
