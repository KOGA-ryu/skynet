from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any


DEFAULT_PROJECT_REPORT_DIR = Path("state/project_reports")


def project_report_summary(db_path: Path) -> dict[str, Any]:
    projects = build_project_reports(db_path)
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "missing_hub_count": sum(1 for project in projects if not project["hub_present"]),
        "orphan_count": sum(project["orphan_count"] for project in projects),
        "project_count": len(projects),
        "projects": [project_summary(project) for project in projects],
        "total_notes": sum(project["note_count"] for project in projects),
    }


def project_report(db_path: Path, project: str) -> dict[str, Any]:
    normalized = normalize_project_name(project)
    projects = {item["project"]: item for item in build_project_reports(db_path)}
    if normalized not in projects:
        known = ", ".join(sorted(projects)) or "<none>"
        raise KeyError(f"unknown project {project!r}; known projects: {known}")
    return projects[normalized]


def write_project_reports(db_path: Path, output_dir: Path = DEFAULT_PROJECT_REPORT_DIR) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = project_report_summary(db_path)
    files: list[str] = []

    index_path = output_dir / "README.md"
    index_path.write_text(render_summary_markdown(summary))
    files.append(str(index_path))

    for project in summary["projects"]:
        detail = project_report(db_path, project["project"])
        path = output_dir / f"{project['project']}.md"
        path.write_text(render_project_markdown(detail))
        files.append(str(path))

    return {
        "file_count": len(files),
        "files": files,
        "output_dir": str(output_dir),
        "project_count": summary["project_count"],
    }


def build_project_reports(db_path: Path) -> list[dict[str, Any]]:
    docs, links = load_project_report_rows(db_path)
    docs_by_path = {doc["path"]: doc for doc in docs}
    grouped_docs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        project = project_name_from_path(str(doc["path"]))
        if project:
            grouped_docs[project].append(doc)

    inbound_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        target = str(link.get("target_path") or "")
        if target in docs_by_path:
            inbound_by_target[target].append(link)

    reports: list[dict[str, Any]] = []
    for project in sorted(grouped_docs):
        docs_for_project = sorted(grouped_docs[project], key=lambda item: item["path"])
        hub_path = f"projects/{project}/README.md"
        note_reports = [
            note_report(doc, inbound_by_target.get(str(doc["path"]), []), project=project)
            for doc in docs_for_project
        ]
        orphan_notes = [note for note in note_reports if note["inbound_count"] == 0]
        high_link_notes = sorted(
            [note for note in note_reports if note["inbound_count"] > 0],
            key=lambda item: (-item["inbound_count"], item["path"]),
        )[:10]
        reports.append(
            {
                "project": project,
                "root": f"projects/{project}/",
                "hub_path": hub_path,
                "hub_present": hub_path in docs_by_path,
                "inbound_count": sum(note["inbound_count"] for note in note_reports),
                "missing_hub": hub_path not in docs_by_path,
                "note_count": len(note_reports),
                "notes": note_reports,
                "orphan_count": len(orphan_notes),
                "orphan_notes": orphan_notes,
                "high_link_notes": high_link_notes,
            }
        )
    return reports


def load_project_report_rows(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        docs = con.execute(
            """
            SELECT path, title, kind
            FROM documents
            WHERE path LIKE 'projects/%'
            ORDER BY path
            """
        ).fetchall()
        links = con.execute(
            """
            SELECT source_path, target_path, target_raw, label, link_kind, line
            FROM links
            WHERE resolved = 1 AND target_path IS NOT NULL
            ORDER BY target_path, source_path, line
            """
        ).fetchall()
    return [dict(row) for row in docs], [dict(row) for row in links]


def note_report(doc: dict[str, Any], inbound: list[dict[str, Any]], *, project: str) -> dict[str, Any]:
    inbound_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in inbound:
        inbound_sources[str(link["source_path"])].append(link)
    source_reports = [
        {
            "line_count": len(items),
            "lines": [int(item["line"]) for item in items],
            "source_path": source,
        }
        for source, items in sorted(inbound_sources.items())
    ]
    return {
        "inbound_count": len(inbound),
        "inbound_sources": source_reports,
        "kind": doc["kind"],
        "path": doc["path"],
        "project": project,
        "title": doc["title"],
    }


def project_summary(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "high_link_notes": [
            {
                "inbound_count": note["inbound_count"],
                "path": note["path"],
                "title": note["title"],
            }
            for note in project["high_link_notes"]
        ],
        "hub_path": project["hub_path"],
        "hub_present": project["hub_present"],
        "inbound_count": project["inbound_count"],
        "missing_hub": project["missing_hub"],
        "note_count": project["note_count"],
        "orphan_count": project["orphan_count"],
        "project": project["project"],
        "root": project["root"],
    }


def project_name_from_path(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if len(parts) < 3 or parts[0] != "projects":
        return None
    return parts[1]


def normalize_project_name(value: str) -> str:
    parts = PurePosixPath(value.strip().strip("/")).parts
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    return value.strip().strip("/")


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Project Reports",
        "",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- project_count: `{summary['project_count']}`",
        f"- total_notes: `{summary['total_notes']}`",
        f"- orphan_count: `{summary['orphan_count']}`",
        f"- missing_hub_count: `{summary['missing_hub_count']}`",
        "",
        "## Projects",
        "",
    ]
    for project in summary["projects"]:
        lines.append(
            "- "
            f"[{project['project']}]({project['project']}.md): "
            f"notes `{project['note_count']}`, "
            f"orphans `{project['orphan_count']}`, "
            f"hub `{'present' if project['hub_present'] else 'missing'}`"
        )
    lines.append("")
    return "\n".join(lines)


def render_project_markdown(project: dict[str, Any]) -> str:
    lines = [
        f"# Project Report: {project['project']}",
        "",
        f"- generated_at_utc: `{datetime.now(UTC).isoformat(timespec='seconds')}`",
        f"- root: `{project['root']}`",
        f"- hub_path: `{project['hub_path']}`",
        f"- hub_present: `{'true' if project['hub_present'] else 'false'}`",
        f"- note_count: `{project['note_count']}`",
        f"- inbound_count: `{project['inbound_count']}`",
        f"- orphan_count: `{project['orphan_count']}`",
        "",
        "## High-Link Notes",
        "",
    ]
    if project["high_link_notes"]:
        for note in project["high_link_notes"]:
            lines.append(
                f"- `{note['path']}` inbound `{note['inbound_count']}` title `{note['title']}`"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Orphan Notes", ""])
    if project["orphan_notes"]:
        for note in project["orphan_notes"]:
            lines.append(f"- `{note['path']}` title `{note['title']}`")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)
