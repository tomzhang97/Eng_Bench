#!/usr/bin/env python3
"""Import exact drawing revisions from explicitly licensed Git repositories.

The importer is intentionally additive. It materializes pinned blobs, preserves
upstream license evidence, emits manifest additions, and only edits the active
manifest when --apply-manifest is supplied. It never edits gold annotations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

from source_rights import rights_blocker


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def write_bytes_verified(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"existing file differs from pinned source: {path}")
        return
    path.write_bytes(payload)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def safe_relative_path(value: str, field: str) -> Path:
    posix = PurePosixPath(str(value).strip())
    if not str(posix) or posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"{field} must be a safe relative path: {value!r}")
    return Path(*posix.parts)


def safe_identifier(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError(f"{field} must be a non-empty path-safe identifier")
    return text


def normalized_page_mapping(value: Any, field: str) -> dict[str, Any]:
    """Validate an optional explicit page map while preserving the legacy default."""
    if value is None:
        return {"type": "by_index_after_render"}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    mapping_type = str(value.get("type") or "").strip()
    if mapping_type != "explicit":
        raise ValueError(f"{field}.type must be explicit when page_mapping is supplied")
    entries = value.get("pairs")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{field}.pairs must contain at least one page pair")

    normalized_pairs: list[dict[str, Any]] = []
    used_a: set[int] = set()
    used_b: set[int] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{field}.pairs[{index}] must be an object")
        page_a = entry.get("page_A")
        page_b = entry.get("page_B")
        if (
            isinstance(page_a, bool)
            or isinstance(page_b, bool)
            or not isinstance(page_a, int)
            or not isinstance(page_b, int)
            or page_a < 0
            or page_b < 0
        ):
            raise ValueError(
                f"{field}.pairs[{index}] page_A and page_B must be non-negative integers"
            )
        if page_a in used_a or page_b in used_b:
            raise ValueError(f"{field}.pairs must map each page at most once")
        used_a.add(page_a)
        used_b.add(page_b)
        normalized = {"page_A": page_a, "page_B": page_b}
        label = str(entry.get("label") or "").strip()
        if label:
            normalized["label"] = label
        normalized_pairs.append(normalized)

    result: dict[str, Any] = {"type": "explicit", "pairs": normalized_pairs}
    for key, used in (("unmatched_A", used_a), ("unmatched_B", used_b)):
        raw_pages = value.get(key, [])
        if not isinstance(raw_pages, list):
            raise ValueError(f"{field}.{key} must be a list")
        pages: list[int] = []
        for page in raw_pages:
            if isinstance(page, bool) or not isinstance(page, int) or page < 0:
                raise ValueError(f"{field}.{key} must contain non-negative integers")
            if page in pages:
                raise ValueError(f"{field}.{key} contains duplicate page {page}")
            if page in used:
                raise ValueError(f"{field}.{key} overlaps a mapped page: {page}")
            pages.append(page)
        if pages:
            result[key] = pages
    return result


def pinned_blob_url(repo_url: str, commit: str, artifact_path: str) -> str:
    base = repo_url.removesuffix(".git").rstrip("/")
    encoded_path = quote(artifact_path, safe="/")
    route = "/-/blob/" if urlsplit(base).hostname == "gitlab.com" else "/blob/"
    return f"{base}{route}{commit}/{encoded_path}"


def git(repo: Path, *args: str, binary: bool = False, check: bool = True) -> bytes | str:
    command = [
        "git",
        "-c",
        f"safe.directory={repo.resolve().as_posix()}",
        "-C",
        str(repo),
        *args,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if binary else result.stderr
        raise RuntimeError(f"git command failed ({' '.join(command)}): {stderr.strip()}")
    return result.stdout


def ensure_repo(root: Path, source: dict[str, Any], slug: str) -> Path:
    repo_value = source.get("repo_path")
    if repo_value:
        repo_path = Path(str(repo_value))
        repo = repo_path if repo_path.is_absolute() else root / repo_path
    else:
        repo = root / ".codex_work" / "versioned_git_sources" / slug
    if (repo / ".git").exists():
        return repo.resolve()
    repo_url = str(source.get("repo_url") or "").strip()
    if not repo_url:
        raise ValueError(f"source {slug}: repo_url is required when repo_path is absent")
    repo.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", repo_url, str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed for {repo_url}: {result.stderr.strip()}")
    return repo.resolve()


def resolve_commit(repo: Path, revision: str) -> str:
    result = git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}", check=False)
    assert isinstance(result, str)
    commit = result.strip()
    if commit:
        return commit
    git(repo, "fetch", "--no-tags", "origin", revision)
    resolved = git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    assert isinstance(resolved, str)
    return resolved.strip()


def git_blob(repo: Path, commit: str, path: str) -> bytes:
    value = git(repo, "show", f"{commit}:{path}", binary=True)
    assert isinstance(value, bytes)
    return value


def commit_metadata(repo: Path, commit: str) -> dict[str, str]:
    value = git(repo, "show", "-s", "--format=%H%x00%aI%x00%an%x00%s", commit)
    assert isinstance(value, str)
    parts = value.rstrip("\n").split("\x00", 3)
    if len(parts) != 4:
        raise ValueError(f"unexpected commit metadata for {commit}")
    return dict(zip(("commit", "author_date", "author", "subject"), parts))


def verify_ancestor(repo: Path, old_commit: str, new_commit: str) -> None:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repo.resolve().as_posix()}",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            old_commit,
            new_commit,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"old commit is not an ancestor of new commit: {old_commit} -> {new_commit}")


def read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        rows.append(row)
    return rows


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    row_type = str(row.get("type") or "")
    row_id = str(row.get("doc_id") if row_type == "doc" else row.get("pair_id") or "")
    return row_type, row_id


def apply_manifest_rows(path: Path, additions: list[dict[str, Any]]) -> dict[str, int]:
    current = read_manifest(path)
    by_key = {row_key(row): row for row in current}
    if len(by_key) != len(current):
        raise ValueError(f"active manifest already contains duplicate identities: {path}")
    added = 0
    already_present = 0
    for row in additions:
        key = row_key(row)
        if not key[0] or not key[1]:
            raise ValueError(f"manifest addition has an invalid identity: {row}")
        existing = by_key.get(key)
        if existing is not None:
            if existing != row:
                raise ValueError(f"manifest identity conflicts with existing row: {key}")
            already_present += 1
            continue
        current.append(row)
        by_key[key] = row
        added += 1
    temporary = path.with_name(f".{path.name}.tmp")
    write_jsonl(temporary, current)
    temporary.replace(path)
    return {"added": added, "already_present": already_present}


def import_source(root: Path, source: dict[str, Any], imported_date: str) -> dict[str, Any]:
    slug = safe_identifier(source.get("slug"), "source.slug")
    candidate_id = safe_identifier(source.get("candidate_id"), "source.candidate_id")
    task = str(source.get("task") or "visualdiff").strip().lower()
    if task not in {"microtext", "visualdiff"}:
        raise ValueError(f"source {slug}: task must be microtext or visualdiff")
    repo_url = str(source.get("repo_url") or "").strip()
    public_status = str(source.get("public_status") or "").strip()
    blocker = rights_blocker(public_status)
    if blocker:
        raise ValueError(f"source {slug}: public_status is not release-safe ({blocker})")

    repo = ensure_repo(root, source, slug)
    revisions = source.get("revisions")
    pairs = source.get("pairs")
    if not isinstance(revisions, list) or not revisions:
        raise ValueError(f"source {slug}: revisions must contain at least one entry")
    if pairs is None:
        pairs = []
    if not isinstance(pairs, list):
        raise ValueError(f"source {slug}: pairs must be a list")
    if task == "visualdiff" and len(revisions) < 2:
        raise ValueError(
            f"source {slug}: visualdiff sources require at least two revisions"
        )
    if task == "visualdiff" and not pairs:
        raise ValueError(f"source {slug}: visualdiff sources require at least one pair")
    if task != "visualdiff" and pairs:
        raise ValueError(f"source {slug}: only visualdiff sources may define pairs")

    revision_records: dict[str, dict[str, Any]] = {}
    task_docs_dir = Path(task) / "docs" / slug
    docs: list[dict[str, Any]] = []
    for revision in revisions:
        if not isinstance(revision, dict):
            raise ValueError(f"source {slug}: each revision must be an object")
        label = safe_identifier(revision.get("label"), "revision.label")
        if label in revision_records:
            raise ValueError(f"source {slug}: duplicate revision label {label}")
        doc_id = safe_identifier(revision.get("doc_id"), "revision.doc_id")
        commit = resolve_commit(repo, str(revision.get("commit") or "").strip())
        artifact_path = str(revision.get("artifact_path") or source.get("artifact_path") or "").strip()
        safe_relative_path(artifact_path, "artifact_path")
        payload = git_blob(repo, commit, artifact_path)
        extension = PurePosixPath(artifact_path).suffix.lower()
        if extension not in {".sch", ".brd", ".pdf"}:
            raise ValueError(f"source {slug}: unsupported drawing extension {extension}")
        local_rel = task_docs_dir / "revisions" / f"{doc_id}{extension}"
        write_bytes_verified(root / local_rel, payload)
        metadata = commit_metadata(repo, commit)
        record = {
            "label": label,
            "doc_id": doc_id,
            "commit": commit,
            "artifact_path": artifact_path,
            "local_path": local_rel.as_posix(),
            "sha256": sha256_bytes(payload),
            **metadata,
        }
        revision_records[label] = record
        doc_type = "eagle_sch_xml" if extension == ".sch" else "eagle_board_xml" if extension == ".brd" else "pdf_technical_drawing"
        doc_row = {
                "type": "doc",
                "doc_id": doc_id,
                "task": task,
                "source_candidate_id": candidate_id,
                "same_model_id": slug,
                "domain": str(source.get("domain") or "pcb_schematic"),
                "doc_type": doc_type,
                "version": {"revision": label, "git_commit": commit, "imported": imported_date},
                "path": local_rel.as_posix(),
                "sha256": sha256_bytes(payload),
                "render": {"dpi": 300, "colorspace": "gray", "rotate_cw90": False},
                "derived": {
                    "pages_dir": f"derived/pages_300dpi/{doc_id}",
                    "textlayer_jsonl": f"derived/textlayer/{doc_id}.jsonl",
                },
                "source_url": repo_url,
                "direct_source_url": pinned_blob_url(repo_url, commit, artifact_path),
                "public_status": public_status,
                "license_note": str(source.get("license_note") or ""),
                "provenance": {
                    "git_commit": commit,
                    "git_path": artifact_path,
                    "git_author_date": metadata["author_date"],
                },
            }
        rendered_pages = len(
            list((root / "derived" / "pages_300dpi" / doc_id).glob("page_*.png"))
        )
        if rendered_pages:
            doc_row["pages"] = rendered_pages
        docs.append(doc_row)

    newest_commit = revision_records[str(revisions[-1]["label"])]["commit"]
    license_commit = resolve_commit(
        repo,
        str(source.get("license_commit") or newest_commit).strip(),
    )
    verify_ancestor(repo, newest_commit, license_commit)
    license_path = str(source.get("license_path") or "").strip()
    safe_relative_path(license_path, "license_path")
    license_payload = git_blob(repo, license_commit, license_path)
    license_text = license_payload.decode("utf-8", errors="replace")
    markers = [str(value).strip() for value in source.get("license_markers", []) if str(value).strip()]
    if not markers:
        raise ValueError(f"source {slug}: license_markers must be non-empty")
    missing_markers = [marker for marker in markers if marker.casefold() not in license_text.casefold()]
    if missing_markers:
        raise ValueError(f"source {slug}: license evidence is missing markers {missing_markers}")
    license_extension = PurePosixPath(license_path).suffix or ".txt"
    license_rel = task_docs_dir / f"UPSTREAM_LICENSE{license_extension}"
    write_bytes_verified(root / license_rel, license_payload)
    license_sha = sha256_bytes(license_payload)
    scope_evidence: dict[str, str] | None = None
    scope_path = str(source.get("license_scope_path") or "").strip()
    if scope_path:
        safe_relative_path(scope_path, "license_scope_path")
        scope_commit = resolve_commit(
            repo,
            str(source.get("license_scope_commit") or license_commit).strip(),
        )
        verify_ancestor(repo, newest_commit, scope_commit)
        scope_payload = git_blob(repo, scope_commit, scope_path)
        scope_text = scope_payload.decode("utf-8", errors="replace")
        scope_markers = [
            str(value).strip()
            for value in source.get("license_scope_markers", [])
            if str(value).strip()
        ]
        if not scope_markers:
            raise ValueError(
                f"source {slug}: license_scope_markers must be non-empty "
                "when license_scope_path is supplied"
            )
        missing_scope_markers = [
            marker
            for marker in scope_markers
            if marker.casefold() not in scope_text.casefold()
        ]
        if missing_scope_markers:
            raise ValueError(
                f"source {slug}: license scope evidence is missing markers "
                f"{missing_scope_markers}"
            )
        scope_extension = PurePosixPath(scope_path).suffix or ".txt"
        scope_rel = task_docs_dir / f"UPSTREAM_LICENSE_SCOPE{scope_extension}"
        write_bytes_verified(root / scope_rel, scope_payload)
        scope_evidence = {
            "path": scope_rel.as_posix(),
            "sha256": sha256_bytes(scope_payload),
            "git_commit": scope_commit,
            "git_path": scope_path,
        }
    for doc in docs:
        doc["license_evidence"] = {
            "path": license_rel.as_posix(),
            "sha256": license_sha,
            "git_commit": license_commit,
            "git_path": license_path,
        }
        if scope_evidence is not None:
            doc["license_scope_evidence"] = scope_evidence

    manifest_pairs: list[dict[str, Any]] = []
    pair_records: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError(f"source {slug}: each pair must be an object")
        pair_id = safe_identifier(pair.get("pair_id"), "pair.pair_id")
        old_label = str(pair.get("from") or "")
        new_label = str(pair.get("to") or "")
        if old_label not in revision_records or new_label not in revision_records:
            raise ValueError(f"source {slug}: pair {pair_id} references an unknown revision")
        old = revision_records[old_label]
        new = revision_records[new_label]
        verify_ancestor(repo, old["commit"], new["commit"])
        if old["sha256"] == new["sha256"]:
            raise ValueError(f"source {slug}: pair {pair_id} has identical drawing content")
        page_mapping = normalized_page_mapping(
            pair.get("page_mapping"), f"source {slug}: pair {pair_id}.page_mapping"
        )
        manifest_pairs.append(
            {
                "type": "pair",
                "pair_id": pair_id,
                "task": "visualdiff",
                "pair_type": "same_model_revision",
                "source_candidate_id": candidate_id,
                "from_doc_id": old["doc_id"],
                "to_doc_id": new["doc_id"],
                "page_mapping": page_mapping,
                "derived": {"align_dir": f"derived/align/{pair_id}"},
                "provenance": {
                    "old_commit": old["commit"],
                    "new_commit": new["commit"],
                    "old_git_path": old["artifact_path"],
                    "new_git_path": new["artifact_path"],
                },
                "notes": "Pinned, content-distinct Git drawing revisions; human review required before gold promotion.",
            }
        )
        pair_records.append(
            {
                "pair_id": pair_id,
                "from": old_label,
                "to": new_label,
                "from_doc_id": old["doc_id"],
                "to_doc_id": new["doc_id"],
                "old_sha256": old["sha256"],
                "new_sha256": new["sha256"],
                "old_artifact_path": old["artifact_path"],
                "new_artifact_path": new["artifact_path"],
                "page_mapping": page_mapping,
            }
        )

    bundle = {
        "candidate_id": candidate_id,
        "slug": slug,
        "repo_url": repo_url,
        "imported_date": imported_date,
        "public_status": public_status,
        "license": {
            "upstream_path": license_path,
            "local_path": license_rel.as_posix(),
            "sha256": license_sha,
            "commit": license_commit,
            "matched_markers": markers,
            "scope_evidence": scope_evidence,
        },
        "revisions": list(revision_records.values()),
        "pairs": pair_records,
    }
    bundle_rel = task_docs_dir / "source_bundle.json"
    write_json(root / bundle_rel, bundle)
    return {
        "slug": slug,
        "candidate_id": candidate_id,
        "bundle_path": bundle_rel.as_posix(),
        "doc_count": len(docs),
        "pair_count": len(manifest_pairs),
        "docs": docs,
        "pairs": manifest_pairs,
    }


def import_spec(root: Path, spec_path: Path, apply_manifest: bool = False) -> dict[str, Any]:
    root = root.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    imported_date = str(spec.get("imported_date") or "").strip()
    if not imported_date:
        raise ValueError("spec.imported_date is required")
    additions_value = str(spec.get("manifest_additions") or "").strip()
    if not additions_value:
        raise ValueError("spec.manifest_additions is required")
    additions_rel = safe_relative_path(additions_value, "manifest_additions")
    sources = spec.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("spec.sources must be a non-empty list")

    source_reports = [import_source(root, source, imported_date) for source in sources]
    additions = [doc for report in source_reports for doc in report["docs"]]
    additions.extend(pair for report in source_reports for pair in report["pairs"])
    identities = [row_key(row) for row in additions]
    if len(identities) != len(set(identities)):
        raise ValueError("import spec produces duplicate manifest identities")
    write_jsonl(root / additions_rel, additions)

    manifest_result = {"added": 0, "already_present": 0}
    if apply_manifest:
        manifest_result = apply_manifest_rows(root / "manifest.jsonl", additions)
    return {
        "goal": "Gold v2.0 Global",
        "mode": "pinned_git_revision_import",
        "imported_date": imported_date,
        "source_count": len(source_reports),
        "doc_count": sum(report["doc_count"] for report in source_reports),
        "pair_count": sum(report["pair_count"] for report in source_reports),
        "manifest_additions": additions_rel.as_posix(),
        "manifest_applied": apply_manifest,
        "manifest_result": manifest_result,
        "active_gold_rows_modified": 0,
        "sources": [
            {key: value for key, value in report.items() if key not in {"docs", "pairs"}}
            for report in source_reports
        ],
        "valid": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--apply-manifest", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    spec_path = args.spec if args.spec.is_absolute() else root / args.spec
    report_path = args.report_json if args.report_json.is_absolute() else root / args.report_json
    report = import_spec(root, spec_path, apply_manifest=args.apply_manifest)
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
