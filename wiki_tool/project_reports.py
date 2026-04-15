from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any

from wiki_tool.page_quality import build_page_quality_report


DEFAULT_PROJECT_REPORT_DIR = Path("state/project_reports")
DEFAULT_PROJECT_REPORT_LIMIT = 25
NOISE_PATH_PARTS = {"runtime", "state", "templates", "tmp"}


def project_report_summary(db_path: Path) -> dict[str, Any]:
    projects = build_project_reports(db_path)
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_stub_count": sum(project["generated_stub_count"] for project in projects),
        "missing_hub_count": sum(1 for project in projects if not project["hub_present"]),
        "missing_summary_count": sum(project["missing_summary_count"] for project in projects),
        "orphan_count": sum(project["orphan_count"] for project in projects),
        "project_count": len(projects),
        "projects": [project_summary(project) for project in projects],
        "reviewable_orphan_count": sum(project["reviewable_orphan_count"] for project in projects),
        "state_artifact_count": sum(project["state_artifact_count"] for project in projects),
        "template_count": sum(project["template_count"] for project in projects),
        "thin_note_count": sum(project["thin_note_count"] for project in projects),
        "total_notes": sum(project["note_count"] for project in projects),
        "unclear_hub_count": sum(project["unclear_hub_count"] for project in projects),
    }


def project_report(
    db_path: Path,
    project: str,
    *,
    limit: int = DEFAULT_PROJECT_REPORT_LIMIT,
) -> dict[str, Any]:
    normalized = normalize_project_name(project)
    projects = {item["project"]: item for item in build_project_reports(db_path, limit=limit)}
    if normalized not in projects:
        known = ", ".join(sorted(projects)) or "<none>"
        raise KeyError(f"unknown project {project!r}; known projects: {known}")
    return projects[normalized]


def write_project_reports(
    db_path: Path,
    output_dir: Path = DEFAULT_PROJECT_REPORT_DIR,
    *,
    limit: int = DEFAULT_PROJECT_REPORT_LIMIT,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = project_report_summary(db_path)
    files: list[str] = []

    index_path = output_dir / "README.md"
    index_path.write_text(render_summary_markdown(summary))
    files.append(str(index_path))

    for project in summary["projects"]:
        detail = project_report(db_path, project["project"], limit=limit)
        path = output_dir / f"{project['project']}.md"
        path.write_text(render_project_markdown(detail))
        files.append(str(path))

    return {
        "file_count": len(files),
        "files": files,
        "generated_stub_count": summary["generated_stub_count"],
        "limit": limit,
        "output_dir": str(output_dir),
        "project_count": summary["project_count"],
        "reviewable_orphan_count": summary["reviewable_orphan_count"],
    }


def build_project_reports(
    db_path: Path,
    *,
    limit: int = DEFAULT_PROJECT_REPORT_LIMIT,
) -> list[dict[str, Any]]:
    if limit < 0:
        raise ValueError("limit must be greater than or equal to 0")
    docs, links = load_project_report_rows(db_path)
    quality = build_page_quality_report(db_path)
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
    quality_by_project = project_quality_indexes(quality)
    for project in sorted(grouped_docs):
        docs_for_project = sorted(grouped_docs[project], key=lambda item: item["path"])
        hub_path = f"projects/{project}/README.md"
        note_reports = [
            note_report(doc, inbound_by_target.get(str(doc["path"]), []), project=project)
            for doc in docs_for_project
        ]
        orphan_notes = [note for note in note_reports if note["inbound_count"] == 0]
        reviewable_orphans = sorted(
            [note for note in orphan_notes if is_reviewable_project_note(note)],
            key=lambda item: item["path"],
        )
        high_link_notes = sorted(
            [note for note in note_reports if note["inbound_count"] > 0],
            key=lambda item: (-item["inbound_count"], item["path"]),
        )[:10]
        generated_stubs = quality_by_project["generated_stubs"].get(project, [])
        thin_notes = quality_by_project["thin_notes"].get(project, [])
        weak_summaries = quality_by_project["weak_summaries"].get(project, [])
        unclear_hubs = quality_by_project["unclear_hubs"].get(project, [])
        template_count = sum(1 for note in note_reports if note["kind"] == "template")
        state_artifact_count = sum(1 for note in note_reports if is_state_artifact_path(str(note["path"])))
        top_actions = top_librarian_actions(
            generated_stub_count=len(generated_stubs),
            reviewable_orphan_count=len(reviewable_orphans),
            unclear_hub_count=len(unclear_hubs),
            missing_summary_count=len(weak_summaries),
            thin_note_count=len(thin_notes),
            state_artifact_count=state_artifact_count,
        )
        reports.append(
            {
                "project": project,
                "root": f"projects/{project}/",
                "hub_path": hub_path,
                "hub_present": hub_path in docs_by_path,
                "inbound_count": sum(note["inbound_count"] for note in note_reports),
                "generated_stub_count": len(generated_stubs),
                "generated_stubs": limit_items(generated_stubs, limit),
                "librarian_priority": librarian_priority_key(
                    generated_stub_count=len(generated_stubs),
                    reviewable_orphan_count=len(reviewable_orphans),
                    unclear_hub_count=len(unclear_hubs),
                    missing_summary_count=len(weak_summaries),
                    project=project,
                ),
                "limit": limit,
                "missing_hub": hub_path not in docs_by_path,
                "missing_summary_count": len(weak_summaries),
                "note_count": len(note_reports),
                "notes": note_reports,
                "orphan_count": len(orphan_notes),
                "orphan_notes": orphan_notes,
                "reviewable_orphan_count": len(reviewable_orphans),
                "reviewable_orphans": limit_items(reviewable_orphans, limit),
                "state_artifact_count": state_artifact_count,
                "template_count": template_count,
                "thin_note_count": len(thin_notes),
                "thin_notes": limit_items(thin_notes, limit),
                "high_link_notes": high_link_notes,
                "top_librarian_actions": top_actions,
                "unclear_hub_count": len(unclear_hubs),
                "unclear_hubs": limit_items(unclear_hubs, limit),
                "weak_summaries": limit_items(weak_summaries, limit),
            }
        )
    return sorted(
        reports,
        key=lambda item: (
            -item["generated_stub_count"],
            -item["reviewable_orphan_count"],
            -item["unclear_hub_count"],
            -item["missing_summary_count"],
            item["project"],
        ),
    )


def load_project_report_rows(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with closing(sqlite3.connect(db_path)) as con:
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
        "generated_stub_count": project["generated_stub_count"],
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
        "librarian_priority": project["librarian_priority"],
        "missing_hub": project["missing_hub"],
        "missing_summary_count": project["missing_summary_count"],
        "note_count": project["note_count"],
        "orphan_count": project["orphan_count"],
        "project": project["project"],
        "reviewable_orphan_count": project["reviewable_orphan_count"],
        "root": project["root"],
        "state_artifact_count": project["state_artifact_count"],
        "template_count": project["template_count"],
        "thin_note_count": project["thin_note_count"],
        "top_librarian_actions": project["top_librarian_actions"],
        "unclear_hub_count": project["unclear_hub_count"],
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


def project_quality_indexes(report: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    return {
        "generated_stubs": group_quality_items_by_project(report["generated_stubs"]),
        "thin_notes": group_quality_items_by_project(report["thin_notes"]),
        "weak_summaries": group_quality_items_by_project(report["missing_summaries"]),
        "unclear_hubs": group_quality_items_by_project(report["unclear_hubs"]),
    }


def group_quality_items_by_project(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        project = project_name_from_path(str(item["path"]))
        if project:
            grouped[project].append(librarian_item(item))
    return dict(grouped)


def librarian_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "byte_size",
        "inbound_count",
        "outbound_link_count",
        "path",
        "reasons",
        "source_count",
        "suggested_next_action",
        "summary_word_count",
        "title",
        "word_count",
    ]
    return {key: item[key] for key in keys if key in item}


def is_reviewable_project_note(note: dict[str, Any]) -> bool:
    return note["kind"] != "template" and not is_noise_path(str(note["path"]))


def is_noise_path(path: str) -> bool:
    return any(part in NOISE_PATH_PARTS for part in PurePosixPath(path).parts)


def is_state_artifact_path(path: str) -> bool:
    return any(part in {"runtime", "state", "tmp"} for part in PurePosixPath(path).parts)


def limit_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return list(items[:limit])


def librarian_priority_key(
    *,
    generated_stub_count: int,
    reviewable_orphan_count: int,
    unclear_hub_count: int,
    missing_summary_count: int,
    project: str,
) -> dict[str, Any]:
    return {
        "generated_stub_count": generated_stub_count,
        "missing_summary_count": missing_summary_count,
        "project": project,
        "reviewable_orphan_count": reviewable_orphan_count,
        "unclear_hub_count": unclear_hub_count,
    }


def top_librarian_actions(
    *,
    generated_stub_count: int,
    reviewable_orphan_count: int,
    unclear_hub_count: int,
    missing_summary_count: int,
    thin_note_count: int,
    state_artifact_count: int,
) -> list[dict[str, Any]]:
    candidates = [
        (
            "fill_generated_stubs",
            generated_stub_count,
            "replace generated placeholder pages with useful human-written pages",
        ),
        (
            "review_orphans",
            reviewable_orphan_count,
            "decide whether unreferenced project notes should be linked, merged, archived, or deleted",
        ),
        (
            "strengthen_hubs",
            unclear_hub_count,
            "improve project hub overview text, sections, and navigation",
        ),
        (
            "add_missing_summaries",
            missing_summary_count,
            "add clear opening summaries so notes are useful from search results",
        ),
        (
            "expand_thin_notes",
            thin_note_count,
            "add enough context for short notes to stand alone",
        ),
        (
            "review_state_artifacts",
            state_artifact_count,
            "keep generated state outputs out of editorial cleanup queues unless promoted",
        ),
    ]
    return [
        {"action": action, "count": count, "reason": reason}
        for action, count, reason in candidates
        if count > 0
    ][:5]


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Project Reports",
        "",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- project_count: `{summary['project_count']}`",
        f"- total_notes: `{summary['total_notes']}`",
        f"- orphan_count: `{summary['orphan_count']}`",
        f"- reviewable_orphan_count: `{summary['reviewable_orphan_count']}`",
        f"- generated_stub_count: `{summary['generated_stub_count']}`",
        f"- missing_summary_count: `{summary['missing_summary_count']}`",
        f"- thin_note_count: `{summary['thin_note_count']}`",
        f"- unclear_hub_count: `{summary['unclear_hub_count']}`",
        f"- missing_hub_count: `{summary['missing_hub_count']}`",
        "",
        "## Librarian Priority",
        "",
        "| project | stubs | reviewable orphans | weak summaries | thin notes | unclear hubs | templates | state artifacts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if not summary["projects"]:
        lines.append("| none | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    for project in summary["projects"]:
        lines.append(
            "| {project} | {stubs} | {orphans} | {summaries} | {thin} | {hubs} | {templates} | {state} |".format(
                hubs=project["unclear_hub_count"],
                orphans=project["reviewable_orphan_count"],
                project=f"[{project['project']}]({project['project']}.md)",
                state=project["state_artifact_count"],
                stubs=project["generated_stub_count"],
                summaries=project["missing_summary_count"],
                templates=project["template_count"],
                thin=project["thin_note_count"],
            )
        )
    lines.extend(["", "## Projects", ""])
    for project in summary["projects"]:
        lines.append(
            "- "
            f"[{project['project']}]({project['project']}.md): "
            f"notes `{project['note_count']}`, "
            f"orphans `{project['orphan_count']}`, "
            f"reviewable orphans `{project['reviewable_orphan_count']}`, "
            f"stubs `{project['generated_stub_count']}`, "
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
        f"- reviewable_orphan_count: `{project['reviewable_orphan_count']}`",
        f"- generated_stub_count: `{project['generated_stub_count']}`",
        f"- missing_summary_count: `{project['missing_summary_count']}`",
        f"- thin_note_count: `{project['thin_note_count']}`",
        f"- unclear_hub_count: `{project['unclear_hub_count']}`",
        f"- template_count: `{project['template_count']}`",
        f"- state_artifact_count: `{project['state_artifact_count']}`",
        f"- detail_limit: `{project['limit']}`",
        "",
        "## Librarian Summary",
        "",
        "| queue | count |",
        "|---|---:|",
        f"| generated stubs | {project['generated_stub_count']} |",
        f"| reviewable orphans | {project['reviewable_orphan_count']} |",
        f"| weak summaries | {project['missing_summary_count']} |",
        f"| thin notes | {project['thin_note_count']} |",
        f"| unclear hubs | {project['unclear_hub_count']} |",
        f"| templates | {project['template_count']} |",
        f"| state artifacts | {project['state_artifact_count']} |",
        "",
        "## Top Actions",
        "",
    ]
    if project["top_librarian_actions"]:
        for action in project["top_librarian_actions"]:
            lines.append(f"- `{action['action']}` count `{action['count']}`: {action['reason']}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Generated Stubs",
            "",
        ]
    )
    append_queue(lines, project["generated_stubs"], empty="none")
    lines.extend(
        [
            "## Reviewable Orphans",
            "",
        ]
    )
    append_queue(lines, project["reviewable_orphans"], empty="none")
    lines.extend(
        [
            "## Weak Summaries",
            "",
        ]
    )
    append_quality_queue(lines, project["weak_summaries"], empty="none")
    lines.extend(
        [
            "## Thin Notes",
            "",
        ]
    )
    append_quality_queue(lines, project["thin_notes"], empty="none")
    lines.extend(
        [
            "## Unclear Hubs",
            "",
        ]
    )
    append_quality_queue(lines, project["unclear_hubs"], empty="none")
    lines.extend(["", "## High-Link Notes", ""])
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


def append_queue(lines: list[str], items: list[dict[str, Any]], *, empty: str) -> None:
    if not items:
        lines.extend([f"- {empty}", ""])
        return
    for item in items:
        lines.append(f"- `{item['path']}` title `{item['title']}` inbound `{item.get('inbound_count', 0)}`")
    lines.append("")


def append_quality_queue(lines: list[str], items: list[dict[str, Any]], *, empty: str) -> None:
    if not items:
        lines.extend([f"- {empty}", ""])
        return
    for item in items:
        reasons = ", ".join(item.get("reasons", []))
        lines.append(
            f"- `{item['path']}` title `{item['title']}` words `{item.get('word_count', 0)}` reasons `{reasons}`"
        )
    lines.append("")
