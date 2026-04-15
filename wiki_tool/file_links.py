from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
import posixpath
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any

from wiki_tool.catalog import broken_links, collect_known_files


DEFAULT_RUDEDUDE_REPO = "rudedude"


def file_link_audit(
    db_path: Path,
    *,
    mac_dev_root: str = "/Users/kogaryu/dev",
) -> dict[str, Any]:
    candidates, skipped = file_link_candidates(db_path, mac_dev_root=mac_dev_root)
    source_counts: dict[str, int] = {}
    for candidate in candidates:
        source_counts[candidate["source_path"]] = source_counts.get(candidate["source_path"], 0) + 1
    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
        "skipped": skipped,
        "skipped_count": len(skipped),
        "source_files": sorted(
            [{"source_path": path, "count": count} for path, count in source_counts.items()],
            key=lambda item: (-item["count"], item["source_path"]),
        ),
    }


def file_link_candidates(
    db_path: Path,
    *,
    mac_dev_root: str = "/Users/kogaryu/dev",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = broken_links(db_path, category="missing_non_markdown_file")
    root = scan_root(db_path)
    known_files = collect_known_files(root) if root and root.exists() else set()
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in rows:
        if is_rudedude_code_row(row):
            repo_path = rudedude_repo_path(str(row["target_raw"]))
            if repo_path is None:
                skipped.append(skip(row, "unsupported_rudedude_code_path"))
                continue
            target_exists = (Path(mac_dev_root) / DEFAULT_RUDEDUDE_REPO / repo_path).exists()
            dev_target = f"dev://{DEFAULT_RUDEDUDE_REPO}/{repo_path}"
            candidates.append(
                replace_link_candidate(
                    row,
                    new_label=repo_path,
                    new_target=dev_target,
                    reason=(
                        "Convert Rudedude code/test link to portable dev:// reference"
                        if target_exists
                        else "Convert historical Rudedude code/test path to portable dev:// reference"
                    ),
                    repair_kind="dev_ref" if target_exists else "historical_dev_ref",
                )
            )
            continue

        replacement = unique_existing_wiki_target(row, known_files)
        if replacement:
            candidates.append(
                replace_link_candidate(
                    row,
                    new_label=str(row["label"]),
                    new_target=relative_link_target(str(row["source_path"]), replacement),
                    reason="Repair stale wiki-relative non-Markdown file link",
                    repair_kind="wiki_relative",
                )
            )
            continue

        skipped.append(skip(row, "no_unique_repair"))

    candidates.sort(key=lambda item: (item["source_path"], item["line"], item["old_target"]))
    return candidates, skipped


def build_file_links_patch_bundle(
    db_path: Path,
    *,
    mac_dev_root: str = "/Users/kogaryu/dev",
) -> dict[str, Any]:
    candidates, skipped = file_link_candidates(db_path, mac_dev_root=mac_dev_root)
    return {
        "backup_manifest": {
            "required_before_apply": True,
            "status": "not_created",
        },
        "bundle_id": f"bundle:file-links:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "rationale": "Repair non-Markdown wiki links by using valid wiki-relative targets or portable dev:// references.",
        "skipped": skipped,
        "targets": [
            {
                "line": candidate["line"],
                "new_label": candidate["new_label"],
                "new_target": candidate["new_target"],
                "old_label": candidate["old_label"],
                "old_target": candidate["old_target"],
                "path": candidate["source_path"],
                "reason": candidate["reason"],
                "repair_kind": candidate["repair_kind"],
                "source_path": candidate["source_path"],
                "type": "replace_markdown_link",
            }
            for candidate in candidates
        ],
    }


def scan_root(db_path: Path) -> Path | None:
    with closing(sqlite3.connect(db_path)) as con:
        row = con.execute("SELECT root FROM scan_runs LIMIT 1").fetchone()
    return Path(row[0]) if row and row[0] else None


def is_rudedude_code_row(row: dict[str, Any]) -> bool:
    source = str(row["source_path"])
    target = str(row["target_raw"]).split("#", 1)[0].split("?", 1)[0].strip()
    return source.startswith("projects/stock_trading/apps/rudedude/") and (
        target.startswith("app/") or target.startswith("tests/")
    )


def rudedude_repo_path(target_raw: str) -> str | None:
    clean = target_raw.split("#", 1)[0].split("?", 1)[0].strip()
    if clean == "app/market_data/lineage_registry.py":
        return "app/lineage/registry.py"
    if clean.startswith("app/") or clean.startswith("tests/"):
        return clean
    return None


def unique_existing_wiki_target(row: dict[str, Any], known_files: set[str]) -> str | None:
    target_path = str(row.get("target_path") or "")
    if target_path in known_files:
        return target_path
    basename = PurePosixPath(target_path or str(row["target_raw"])).name
    matches = sorted(path for path in known_files if PurePosixPath(path).name == basename)
    return matches[0] if len(matches) == 1 else None


def relative_link_target(source_path: str, target_path: str) -> str:
    source_parent = PurePosixPath(source_path).parent.as_posix()
    return posixpath.relpath(target_path, start=source_parent)


def replace_link_candidate(
    row: dict[str, Any],
    *,
    new_label: str,
    new_target: str,
    reason: str,
    repair_kind: str,
) -> dict[str, Any]:
    return {
        "line": row["line"],
        "new_label": new_label,
        "new_target": new_target,
        "old_label": row["label"],
        "old_target": row["target_raw"],
        "reason": reason,
        "repair_kind": repair_kind,
        "source_path": row["source_path"],
        "target_path": row.get("target_path"),
    }


def skip(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "line": row["line"],
        "reason": reason,
        "source_path": row["source_path"],
        "target_path": row.get("target_path"),
        "target_raw": row["target_raw"],
    }
