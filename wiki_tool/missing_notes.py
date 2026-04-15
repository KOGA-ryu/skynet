from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import PurePosixPath
from typing import Any

from wiki_tool.catalog import broken_links, latest_scan_run


def missing_note_audit(db_path, *, limit: int | None = None) -> dict[str, Any]:
    candidates = missing_note_candidates(db_path, limit=limit)
    source_counts: dict[str, int] = {}
    for candidate in candidates:
        for ref in candidate["inbound_references"]:
            source_counts[ref["source_path"]] = source_counts.get(ref["source_path"], 0) + 1
    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
        "source_files": sorted(
            [{"source_path": path, "count": count} for path, count in source_counts.items()],
            key=lambda item: (-item["count"], item["source_path"]),
        ),
    }


def missing_note_candidates(db_path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = broken_links(db_path, category="missing_markdown_note")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        target_path = row.get("target_path") or normalize_target_path(str(row["target_raw"]))
        if not target_path.endswith(".md"):
            target_path = f"{target_path}.md"
        grouped.setdefault(target_path, []).append(row)

    candidates = []
    for path, refs in grouped.items():
        inbound = [
            {
                "label": ref["label"],
                "line": ref["line"],
                "source_path": ref["source_path"],
                "target_raw": ref["target_raw"],
            }
            for ref in sorted(refs, key=lambda item: (item["source_path"], item["line"], item["target_raw"]))
        ]
        source_count = len({ref["source_path"] for ref in refs})
        title = title_for_stub_path(path)
        candidates.append(
            {
                "body": render_stub_body(path=path, title=title, inbound_references=inbound),
                "inbound_reference_count": len(inbound),
                "inbound_references": inbound,
                "path": path,
                "priority": candidate_priority(path, len(inbound), source_count),
                "reason": "Create navigable stub for unresolved Markdown note link",
                "source_count": source_count,
                "title": title,
            }
        )
    candidates.sort(key=lambda item: (-item["priority"], item["path"]))
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def build_missing_notes_patch_bundle(db_path, *, limit: int | None = None) -> dict[str, Any]:
    candidates = missing_note_candidates(db_path, limit=limit)
    return {
        "backup_manifest": {
            "required_before_apply": True,
            "status": "not_created",
        },
        "bundle_id": f"bundle:missing-notes:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "rationale": "Create conservative wiki stubs for unresolved Markdown note links.",
        "source_catalog": source_catalog_metadata(db_path),
        "targets": [
            {
                "body": candidate["body"],
                "inbound_references": candidate["inbound_references"],
                "path": candidate["path"],
                "reason": candidate["reason"],
                "title": candidate["title"],
                "type": "create_markdown_stub",
            }
            for candidate in candidates
        ],
    }


def source_catalog_metadata(db_path) -> dict[str, Any]:
    run = latest_scan_run(db_path)
    return {
        "db_path": str(db_path),
        "root": run.get("root") if run else None,
        "run_id": run.get("run_id") if run else None,
        "scanned_at_utc": run.get("scanned_at_utc") if run else None,
    }


def render_stub_body(*, path: str, title: str, inbound_references: list[dict[str, Any]]) -> str:
    lines = [
        f"# {title}",
        "",
        "## Why This Page Exists",
        "",
        f"- This stub exists because current wiki notes link to `{path}`.",
        "- It preserves navigation while the final content is still being written.",
        "",
        "## Current Status",
        "",
        "- Status: stub",
        "- Content has not been filled in yet.",
        "",
        "## Inbound References",
        "",
    ]
    for ref in inbound_references:
        lines.append(
            f"- `{ref['source_path']}:{ref['line']}` label `{ref['label']}` target `{ref['target_raw']}`"
        )
    lines.append("")
    return "\n".join(lines)


def title_for_stub_path(path: str) -> str:
    pure = PurePosixPath(path)
    if pure.name.lower() == "readme.md" and pure.parent.name:
        stem = pure.parent.name
    else:
        stem = pure.stem
    return " ".join(part.capitalize() for part in stem.replace("_", " ").replace("-", " ").split())


def candidate_priority(path: str, inbound_count: int, source_count: int) -> int:
    priority = inbound_count * 10 + source_count
    if path.startswith("projects/stock_trading/apps/rudedude/"):
        priority += 1000
    return priority


def normalize_target_path(raw: str) -> str:
    return raw.split("#", 1)[0].split("?", 1)[0].strip().lstrip("./")
