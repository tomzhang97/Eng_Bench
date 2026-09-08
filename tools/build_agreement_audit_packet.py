#!/usr/bin/env python3
"""Build a deterministic two-reviewer agreement-audit packet from active gold."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import export_review_packs
except ModuleNotFoundError:  # Imported as tools.build_agreement_audit_packet in tests.
    from tools import export_review_packs


REVIEW_FIELDS = (
    "answer_correct",
    "corrected_answer",
    "bbox_correct",
    "corrected_evidence_json",
    "accept_reject",
    "ambiguity",
    "rights_concern",
    "notes",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return export_review_packs.load_jsonl(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_source_inventory(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {
            str(row.get("doc_id") or "").strip(): row
            for row in csv.DictReader(f)
            if str(row.get("doc_id") or "").strip()
        }


def normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def normalize_revision(value: Any) -> str:
    normalized = normalize_identifier(value)
    for prefix in ("pcb", "revision", "rev"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized


def manifest_revision_values(row: dict[str, Any]) -> set[str]:
    version = row.get("version")
    if not isinstance(version, dict):
        return set()
    values: set[str] = set()
    for value in version.values():
        if isinstance(value, list):
            values.update(normalize_revision(item) for item in value if normalize_revision(item))
        elif normalize_revision(value):
            values.add(normalize_revision(value))
    return values


def source_context_for(
    row: dict[str, Any],
    source_inventory: dict[str, dict[str, str]],
    visualdiff_pairs: dict[str, dict[str, Any]],
    visualdiff_manifest_docs: list[dict[str, Any]],
) -> dict[str, str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    doc_id = str(metadata.get("doc_id") or "")
    source_rows: list[dict[str, str]] = []
    source_doc_ids: list[str] = []

    if row.get("task") == "visualdiff":
        pair_id = str(metadata.get("pair_id") or "")
        pair = visualdiff_pairs.get(pair_id, {})
        family = normalize_identifier(pair.get("doc_id") or doc_id)
        revisions = [pair.get("version_id_old"), pair.get("version_id_new")]
        for revision in revisions:
            revision_key = normalize_revision(revision)
            for manifest_doc in visualdiff_manifest_docs:
                manifest_haystack = normalize_identifier(
                    " ".join(
                        str(manifest_doc.get(field) or "")
                        for field in ("doc_id", "same_model_id", "path")
                    )
                )
                if family and family not in manifest_haystack:
                    continue
                if revision_key and revision_key not in manifest_revision_values(manifest_doc):
                    continue
                manifest_doc_id = str(manifest_doc.get("doc_id") or "")
                source = source_inventory.get(manifest_doc_id)
                if source and manifest_doc_id not in source_doc_ids:
                    source_doc_ids.append(manifest_doc_id)
                    source_rows.append(source)
    direct_source = source_inventory.get(doc_id)
    if not source_rows and direct_source:
        source_doc_ids.append(doc_id)
        source_rows.append(direct_source)

    def unique_values(field: str) -> str:
        values = []
        for source in source_rows:
            value = str(source.get(field) or "").strip()
            if value and value not in values:
                values.append(value)
        return "; ".join(values)

    return {
        "doc_id": doc_id,
        "source_doc_ids": "; ".join(source_doc_ids),
        "source_url": unique_values("source_url"),
        "source_status": unique_values("public_status"),
    }


def category_for(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if row.get("task") == "microtext":
        return str(metadata.get("category") or "unknown")
    change_type = metadata.get("change_type")
    if isinstance(change_type, list):
        return "+".join(sorted(str(value) for value in change_type if value)) or "unknown"
    return str(change_type or "unknown")


def stratum_for(row: dict[str, Any]) -> str:
    return f"{row.get('split') or 'unknown'}|{row.get('task') or 'unknown'}|{category_for(row)}"


def deterministic_key(row: dict[str, Any], seed: str) -> str:
    return hashlib.sha256(f"{seed}:{row.get('id') or ''}".encode("utf-8")).hexdigest()


def select_stratified(
    rows: list[dict[str, Any]],
    sample_fraction: float,
    min_per_stratum: int,
    seed: str,
) -> list[dict[str, Any]]:
    if not 0 < sample_fraction <= 1:
        raise ValueError("sample_fraction must be in (0, 1]")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("split") not in {"dev", "test"}:
            continue
        if row.get("task") not in {"microtext", "visualdiff"}:
            continue
        groups[stratum_for(row)].append(row)
    selected: list[dict[str, Any]] = []
    for stratum, group in sorted(groups.items()):
        target = min(len(group), max(min_per_stratum, math.ceil(len(group) * sample_fraction)))
        selected.extend(sorted(group, key=lambda row: deterministic_key(row, seed))[:target])
    return sorted(selected, key=lambda row: (stratum_for(row), str(row.get("id") or "")))


def select_stratified_exact(
    rows: list[dict[str, Any]],
    sample_size: int,
    min_per_stratum: int,
    seed: str,
) -> list[dict[str, Any]]:
    """Select an exact-size deterministic sample with proportional strata."""
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("split") not in {"dev", "test"}:
            continue
        if row.get("task") not in {"microtext", "visualdiff"}:
            continue
        groups[stratum_for(row)].append(row)
    total = sum(len(group) for group in groups.values())
    if sample_size > total:
        raise ValueError(
            f"sample_size {sample_size} exceeds eligible dev/test rows {total}"
        )
    allocations = {
        stratum: min(len(group), min_per_stratum)
        for stratum, group in sorted(groups.items())
    }
    baseline = sum(allocations.values())
    if baseline > sample_size:
        raise ValueError(
            f"sample_size {sample_size} cannot cover {len(groups)} strata with "
            f"min_per_stratum={min_per_stratum}"
        )
    remaining = sample_size - baseline
    while remaining:
        candidates = [
            stratum
            for stratum in sorted(groups)
            if allocations[stratum] < len(groups[stratum])
        ]
        if not candidates:
            raise ValueError("eligible strata exhausted before exact sample size")
        # Jefferson-style allocation keeps the final sample proportional while
        # deterministic ordering resolves equal priorities.
        chosen = max(
            candidates,
            key=lambda stratum: len(groups[stratum]) / (allocations[stratum] + 1),
        )
        allocations[chosen] += 1
        remaining -= 1
    selected: list[dict[str, Any]] = []
    for stratum, group in sorted(groups.items()):
        selected.extend(
            sorted(group, key=lambda row: deterministic_key(row, seed))[
                : allocations[stratum]
            ]
        )
    return sorted(selected, key=lambda row: (stratum_for(row), str(row.get("id") or "")))


def select_stratified_task_quotas(
    rows: list[dict[str, Any]],
    task_quotas: dict[str, int],
    min_per_stratum: int,
    seed: str,
) -> list[dict[str, Any]]:
    if set(task_quotas) != {"microtext", "visualdiff"}:
        raise ValueError("task quotas must specify microtext and visualdiff")
    selected: list[dict[str, Any]] = []
    for task in ("microtext", "visualdiff"):
        quota = int(task_quotas[task])
        task_rows = [row for row in rows if row.get("task") == task]
        selected.extend(
            select_stratified_exact(
                task_rows,
                sample_size=quota,
                min_per_stratum=min_per_stratum,
                seed=f"{seed}:{task}",
            )
        )
    return sorted(selected, key=lambda row: (stratum_for(row), str(row.get("id") or "")))


def provenance_documents(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("doc_id") or "").strip(): row
        for row in payload.get("documents", [])
        if str(row.get("doc_id") or "").strip()
    }


def filter_release_ready_rows(
    rows: list[dict[str, Any]],
    *,
    source_inventory: dict[str, dict[str, str]],
    visualdiff_pairs: dict[str, dict[str, Any]],
    visualdiff_manifest_docs: list[dict[str, Any]],
    provenance_docs: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]:
    eligible: list[dict[str, Any]] = []
    release_contexts: dict[str, dict[str, Any]] = {}
    exclusions: Counter[str] = Counter()
    for row in rows:
        identifier = str(row.get("id") or "").strip()
        context = source_context_for(
            row,
            source_inventory,
            visualdiff_pairs,
            visualdiff_manifest_docs,
        )
        docs = [
            value.strip()
            for value in str(context.get("source_doc_ids") or "").split(";")
            if value.strip()
        ]
        blockers: list[str] = []
        if not docs:
            blockers.append("missing_source_mapping")
        for doc_id in docs:
            provenance = provenance_docs.get(doc_id)
            if provenance is None:
                blockers.append(f"missing_provenance:{doc_id}")
            elif not provenance.get("release_ready"):
                blockers.append(
                    f"{doc_id}:{provenance.get('blocker') or 'not_release_ready'}"
                )
        release_ready = bool(docs) and not blockers
        release_contexts[identifier] = {
            "source_release_ready": release_ready,
            "source_blockers": ";".join(sorted(set(blockers))),
        }
        if release_ready:
            eligible.append(row)
        else:
            for blocker in blockers or ["not_release_ready"]:
                exclusions[blocker] += 1
    return eligible, release_contexts, dict(sorted(exclusions.items()))


def evidence_for(row: dict[str, Any], image_index: int) -> dict[str, Any]:
    for entry in row.get("evidence") or []:
        if isinstance(entry, dict) and int(entry.get("image_index", 0)) == image_index:
            return entry
    return {}


def page_index_from_image(value: Any) -> int:
    stem = Path(str(value or "")).stem
    match = re.search(r"(?:page_|p)(\d+)$", stem, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def microtext_export_row(row: dict[str, Any]) -> dict[str, Any]:
    evidence = evidence_for(row, 0)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    image_path = (row.get("images") or [""])[0]
    return {
        "candidate_id": row["id"],
        "doc_id": metadata.get("doc_id", "unknown"),
        "page_index": page_index_from_image(image_path),
        "bbox": evidence.get("bbox", []),
        "target_text": row.get("answer", ""),
        "proposed_text": row.get("answer", ""),
        "category": metadata.get("category", "unknown"),
        "image_path": image_path,
        "split": row.get("split", ""),
    }


def visualdiff_export_row(row: dict[str, Any]) -> dict[str, Any]:
    images = row.get("images") or []
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "pair_id": row["id"],
        "project_id": metadata.get("pair_id", row["id"]),
        "doc_id": metadata.get("doc_id", "unknown"),
        "bbox_old": evidence_for(row, 0).get("bbox", []),
        "bbox_new": evidence_for(row, 1).get("bbox", []),
        "image_old": images[0] if images else "",
        "image_new": images[1] if len(images) > 1 else "",
        "description": row.get("answer", ""),
        "change_type": metadata.get("change_type", []),
        "split": row.get("split", ""),
    }


def relative_output(root: Path, output_dir: Path) -> Path:
    resolved_root = root.resolve()
    resolved_output = (output_dir if output_dir.is_absolute() else root / output_dir).resolve()
    try:
        return resolved_output.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("output_dir must be inside root") from exc


def local_path(value: Any, output_rel: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    try:
        return path.relative_to(output_rel).as_posix()
    except ValueError:
        return path.as_posix()


def build_checklist_rows(
    selected: list[dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    source_inventory: dict[str, dict[str, str]],
    visualdiff_pairs: dict[str, dict[str, Any]],
    visualdiff_manifest_docs: list[dict[str, Any]],
    output_rel: Path,
    release_contexts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in selected:
        identifier = str(row["id"])
        manifest = manifests[identifier]
        task = str(row.get("task") or "")
        source_context = source_context_for(
            row,
            source_inventory,
            visualdiff_pairs,
            visualdiff_manifest_docs,
        )
        primary = manifest.get("crop_path") if task == "microtext" else manifest.get("panel_path")
        checklist = {
            "id": identifier,
            **source_context,
            "split": row.get("split", ""),
            "task": task,
            "stratum": stratum_for(row),
            "category": category_for(row),
            "question": row.get("question", ""),
            "reference_answer": row.get("answer", ""),
            "reference_evidence_json": json.dumps(row.get("evidence") or [], separators=(",", ":")),
            "primary_evidence_path": local_path(primary, output_rel),
            "page_path": local_path(manifest.get("page_path"), output_rel),
            "old_page_path": local_path(manifest.get("old_page_path"), output_rel),
            "new_page_path": local_path(manifest.get("new_page_path"), output_rel),
        }
        if release_contexts is not None:
            checklist.update(release_contexts.get(identifier, {}))
        checklist.update({field: "" for field in REVIEW_FIELDS})
        rows.append(checklist)
    return rows


def write_index(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Eng_Bench Agreement Audit</title>",
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:6px;vertical-align:top;font-size:13px}"
        "th{background:#f5f5f5;position:sticky;top:0}img{max-width:620px;max-height:240px}</style>",
        "</head><body><h1>Eng_Bench Agreement Audit</h1>",
        f"<p>Rows: {len(rows)}</p><table>",
        "<tr><th>#</th><th>Evidence</th><th>Full pages</th><th>ID</th><th>Task/category</th><th>Source</th><th>Reference answer</th></tr>",
    ]
    for index, row in enumerate(rows, start=1):
        primary = html.escape(str(row["primary_evidence_path"]))
        links = []
        for field in ("page_path", "old_page_path", "new_page_path"):
            value = str(row.get(field) or "")
            if value:
                links.append(f"<a href=\"{html.escape(value)}\">{html.escape(field)}</a>")
        lines.append(
            f"<tr><td>{index}</td><td><a href=\"{primary}\"><img src=\"{primary}\"></a></td>"
            f"<td>{'<br>'.join(links)}</td><td><code>{html.escape(str(row['id']))}</code></td>"
            f"<td>{html.escape(str(row['task']))}<br>{html.escape(str(row['category']))}</td>"
            f"<td><a href=\"{html.escape(str(row.get('source_url') or ''))}\">{html.escape(str(row.get('source_doc_ids') or row.get('doc_id') or ''))}</a>"
            f"<br>{html.escape(str(row.get('source_status') or ''))}</td>"
            f"<td>{html.escape(str(row['reference_answer']))}</td></tr>"
        )
    lines.extend(["</table></body></html>", ""])
    (output_dir / "index.html").write_text("\n".join(lines), encoding="utf-8")


def write_docs(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                "# Eng_Bench Independent Human Agreement Audit",
                "",
                f"- Sample rows: `{report['sample_rows']}`",
                "- Reviewer A and Reviewer B must work independently.",
                "- Do not compare or copy decisions before both CSVs are complete.",
                "- This packet evaluates existing active-gold labels; it does not merge or modify gold.",
                "",
                "Open `index.html`, then fill only your assigned reviewer checklist.",
                "- Chinese handoff instructions: `INTERN_INSTRUCTIONS_ZH.md`.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "HUMAN_REVIEW_STEPS.md").write_text(
        "\n".join(
            [
                "# Human Agreement Review Steps",
                "",
                "For Chinese instructions, start with `INTERN_INSTRUCTIONS_ZH.md`.",
                "",
                "1. Assign different people to `reviewer_a_checklist.csv` and `reviewer_b_checklist.csv`.",
                "2. Work independently and inspect the crop/panel plus full-page links when needed.",
                "3. Inspect `source_url` and `source_status` before deciding `rights_concern`.",
                "4. Fill only the eight reviewer-response columns from `answer_correct` through `notes`.",
                "5. Do not sort, add, delete, or reorder rows; do not edit IDs, references, evidence, paths, or source fields.",
                "6. Set `answer_correct` and `bbox_correct` to `yes` or `no`.",
                "7. If `answer_correct=no`, fill `corrected_answer`.",
                "8. If `bbox_correct=no`, fill `corrected_evidence_json` using the same JSON format as `reference_evidence_json`.",
                "9. Set `accept_reject` to `accept` or `reject`.",
                "10. Set `ambiguity` and `rights_concern` to `yes` or `no`.",
                "11. Add a short note for every reject, ambiguity, rights concern, or correction.",
                "12. Return both completed CSVs without renaming them.",
                "",
                "The return checker rejects altered immutable cells, missing or duplicate IDs, and row reordering before any agreement metric is counted.",
                "",
                "After return, maintainers run `tools/agreement_audit.py`; rows are adjudicated separately before any gold change.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "INTERN_INSTRUCTIONS_ZH.md").write_text(
        "\n".join(
            [
                "# Eng_Bench 一致性复核说明",
                "",
                "这个包用于检查现有 gold 数据是否稳定可靠，不是让你新增数据，也不是直接改 gold。",
                "",
                "## 分工",
                "",
                "- 两位 reviewer 必须独立完成，不能互相看答案、讨论或复制。",
                "- Reviewer A 只填写 `reviewer_a_checklist.csv`。",
                "- Reviewer B 只填写 `reviewer_b_checklist.csv`。",
                "- 不要修改 `sample_reference.csv`，它只是对照表。",
                "- 不要改文件名，也不要删除 `evidence/`、`index.html` 或任何图片。",
                "- CSV 里只能填写从 `answer_correct` 到 `notes` 的 8 个审核结果列。",
                "- 不要排序、增删或调整行顺序；不要修改 ID、标准答案、证据、路径或来源信息。",
                "- 系统会核对所有不可修改字段；发现改动、缺行、重复 ID 或换序时，整份结果不会计入一致性指标。",
                "",
                "## 怎么看",
                "",
                "1. 打开 `index.html` 浏览每一行的裁剪图或 visualdiff 对比图。",
                "2. 如果裁剪图不够清楚，点 full page / page link 看完整页面。",
                "3. 对 microtext：确认标注文字是否和图片一致，框是否框住了正确位置。",
                "4. 对 visualdiff：确认 old/new 图中框内是否真的有工程内容变化，并判断描述是否正确。",
                "5. 如果只是截图整体偏移、裁剪错位，或 old/new 完全一样，不算有效变化。",
                "",
                "## CSV 字段怎么填",
                "",
                "- `answer_correct`: 标准答案正确填 `yes`，不正确填 `no`。",
                "- `corrected_answer`: 只有 `answer_correct=no` 时填写正确答案。",
                "- `bbox_correct`: 框位置正确填 `yes`，框错了填 `no`。",
                "- `corrected_evidence_json`: 只有 `bbox_correct=no` 时填写修正后的 evidence JSON；不会修就备注原因。",
                "- `accept_reject`: 这一行能作为 gold 数据保留填 `accept`，不能保留填 `reject`。",
                "- `ambiguity`: 看不清、无法判断、有多种解释填 `yes`，否则填 `no`。",
                "- `rights_concern`: 来源或版权看起来有问题填 `yes`，否则填 `no`。",
                "- `notes`: 只要有 reject、no、ambiguity=yes 或 rights_concern=yes，都写一句原因。",
                "",
                "## 返回",
                "",
                "完成后返回整个文件夹，尤其要包含两个 CSV、`index.html` 和 `evidence/` 文件夹。不要只发截图或只发一个 CSV。",
                "",
            ]
        ),
        encoding="utf-8",
    )


def zip_directory(source_dir: Path, zip_output: Path) -> int:
    zip_output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir.parent).as_posix())
                count += 1
    return count


def build_packet(
    root: Path,
    input_path: Path,
    output_dir: Path,
    zip_output: Path,
    sample_fraction: float = 0.1,
    min_per_stratum: int = 1,
    seed: str = "engbench-agreement-v1",
    pad_px: int = 48,
    sample_size: int | None = None,
    provenance_report: Path | None = None,
    require_release_ready: bool = False,
    task_quotas: dict[str, int] | None = None,
) -> dict[str, Any]:
    output_rel = relative_output(root, output_dir)
    output_abs = root / output_rel
    zip_abs = zip_output if zip_output.is_absolute() else root / zip_output
    if output_abs.exists():
        shutil.rmtree(output_abs)
    output_abs.mkdir(parents=True)
    rows = read_jsonl(input_path if input_path.is_absolute() else root / input_path)
    source_inventory = read_source_inventory(root / "SOURCE_INVENTORY.csv")
    visualdiff_pairs = {
        str(row.get("pair_id") or ""): row
        for row in read_jsonl(root / "visualdiff" / "annotations" / "visualdiff_pairs.jsonl")
        if str(row.get("pair_id") or "")
    }
    visualdiff_manifest_docs = [
        row
        for row in read_jsonl(root / "manifest.jsonl")
        if row.get("task") == "visualdiff" and row.get("type", "doc") == "doc"
    ]
    dev_test_rows = [
        row
        for row in rows
        if row.get("split") in {"dev", "test"}
        and row.get("task") in {"microtext", "visualdiff"}
    ]
    if require_release_ready and provenance_report is None:
        raise ValueError("--require-release-ready requires --provenance-report")
    release_contexts: dict[str, dict[str, Any]] | None = None
    release_exclusions: dict[str, int] = {}
    provenance_abs: Path | None = None
    eligible_rows = dev_test_rows
    if provenance_report is not None:
        provenance_abs = (
            provenance_report
            if provenance_report.is_absolute()
            else root / provenance_report
        )
        eligible_rows, release_contexts, release_exclusions = filter_release_ready_rows(
            dev_test_rows,
            source_inventory=source_inventory,
            visualdiff_pairs=visualdiff_pairs,
            visualdiff_manifest_docs=visualdiff_manifest_docs,
            provenance_docs=provenance_documents(provenance_abs),
        )
    if task_quotas is not None:
        if sample_size is not None and sample_size != sum(task_quotas.values()):
            raise ValueError("sample_size must equal the sum of task quotas")
        selected = select_stratified_task_quotas(
            eligible_rows,
            task_quotas,
            min_per_stratum,
            seed,
        )
    elif sample_size is not None:
        selected = select_stratified_exact(
            eligible_rows,
            sample_size,
            min_per_stratum,
            seed,
        )
    else:
        selected = select_stratified(
            eligible_rows,
            sample_fraction,
            min_per_stratum,
            seed,
        )
    micro = [microtext_export_row(row) for row in selected if row.get("task") == "microtext"]
    visual = [visualdiff_export_row(row) for row in selected if row.get("task") == "visualdiff"]
    micro_rel = output_rel / "evidence" / "microtext"
    visual_rel = output_rel / "evidence" / "visualdiff"
    micro_stats = export_review_packs.export_microtext_pack(root, micro, micro_rel, pad_px=pad_px)
    visual_stats = export_review_packs.export_visualdiff_pack(root, visual, visual_rel, pad_px=pad_px)
    manifests = {
        str(row.get("candidate_id") or row.get("pair_id")): row
        for path in (root / micro_rel / "manifest.jsonl", root / visual_rel / "manifest.jsonl")
        for row in read_jsonl(path)
    }
    checklist_rows = build_checklist_rows(
        selected,
        manifests,
        source_inventory,
        visualdiff_pairs,
        visualdiff_manifest_docs,
        output_rel,
        release_contexts,
    )
    write_csv(output_abs / "sample_reference.csv", checklist_rows)
    write_csv(output_abs / "reviewer_a_checklist.csv", checklist_rows)
    write_csv(output_abs / "reviewer_b_checklist.csv", checklist_rows)
    write_index(output_abs, checklist_rows)
    sample_by_stratum = dict(sorted(Counter(row["stratum"] for row in checklist_rows).items()))
    report = {
        "input_rows": len(rows),
        "eligible_dev_test_rows": sum(row.get("split") in {"dev", "test"} for row in rows),
        "eligible_after_release_filter": len(eligible_rows),
        "release_ready_filter_enabled": provenance_report is not None,
        "release_ready_filter_required": require_release_ready,
        "release_filter_exclusions": release_exclusions,
        "provenance_report": provenance_abs.as_posix() if provenance_abs else "",
        "provenance_report_sha256": (
            hashlib.sha256(provenance_abs.read_bytes()).hexdigest()
            if provenance_abs
            else ""
        ),
        "sample_fraction": sample_fraction,
        "requested_sample_size": sample_size,
        "requested_task_rows": task_quotas or {},
        "min_per_stratum": min_per_stratum,
        "seed": seed,
        "sample_rows": len(checklist_rows),
        "sample_by_stratum": sample_by_stratum,
        "microtext_rows": int(micro_stats.get("rows", 0)),
        "visualdiff_rows": int(visual_stats.get("rows", 0)),
        "missing_images": int(micro_stats.get("missing_images", 0))
        + int(visual_stats.get("missing_images", 0)),
        "out_of_frame": int(micro_stats.get("out_of_frame", 0))
        + int(visual_stats.get("out_of_frame", 0)),
        "missing_source_doc_ids": sum(not row.get("source_doc_ids") for row in checklist_rows),
        "missing_source_urls": sum(not row.get("source_url") for row in checklist_rows),
        "missing_source_statuses": sum(not row.get("source_status") for row in checklist_rows),
        "selected_release_ready_rows": sum(
            str(row.get("source_release_ready") or "").lower() == "true"
            for row in checklist_rows
        ),
        "valid": len(checklist_rows) == len(selected)
        and int(micro_stats.get("rows", 0)) + int(visual_stats.get("rows", 0)) == len(selected),
        "zip_output": zip_abs.as_posix(),
    }
    write_docs(output_abs, report)
    write_json(output_abs / "agreement_packet_build_report.json", report)
    report["zip_entries"] = zip_directory(output_abs, zip_abs)
    write_json(output_abs / "agreement_packet_build_report.json", report)
    zip_directory(output_abs, zip_abs)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an Eng_Bench agreement-audit packet.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", default="eng_bench.jsonl")
    parser.add_argument(
        "--output-dir",
        default="derived/human_adjudication/2026-06-05_agreement_audit",
    )
    parser.add_argument(
        "--zip-output",
        default="derived/human_adjudication/Eng_Bench_agreement_audit_2026-06-05.zip",
    )
    parser.add_argument("--sample-fraction", type=float, default=0.1)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--microtext-rows", type=int)
    parser.add_argument("--visualdiff-rows", type=int)
    parser.add_argument("--min-per-stratum", type=int, default=1)
    parser.add_argument("--seed", default="engbench-agreement-v1")
    parser.add_argument("--pad-px", type=int, default=48)
    parser.add_argument("--provenance-report")
    parser.add_argument("--require-release-ready", action="store_true")
    args = parser.parse_args(argv)
    task_quota_args = (args.microtext_rows, args.visualdiff_rows)
    if any(value is not None for value in task_quota_args) and not all(
        value is not None for value in task_quota_args
    ):
        parser.error("--microtext-rows and --visualdiff-rows must be provided together")
    task_quotas = (
        {"microtext": args.microtext_rows, "visualdiff": args.visualdiff_rows}
        if all(value is not None for value in task_quota_args)
        else None
    )
    report = build_packet(
        root=Path(args.root),
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        zip_output=Path(args.zip_output),
        sample_fraction=args.sample_fraction,
        min_per_stratum=args.min_per_stratum,
        seed=args.seed,
        pad_px=args.pad_px,
        sample_size=args.sample_size,
        provenance_report=(
            Path(args.provenance_report) if args.provenance_report else None
        ),
        require_release_ready=args.require_release_ready,
        task_quotas=task_quotas,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
