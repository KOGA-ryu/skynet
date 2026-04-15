from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any

from wiki_tool.catalog import DEFAULT_DB
from wiki_tool.page_quality import build_page_quality_report


DEFAULT_SOURCE_SHELF_REPORT_DIR = Path("state/source_shelf_reports")
DEFAULT_SOURCE_SHELF_LIMIT = 25
DEFAULT_SOURCE_SHELVES = ("math", "computer")


def source_shelf_summary(
    db_path: Path = DEFAULT_DB,
    *,
    shelves: tuple[str, ...] = DEFAULT_SOURCE_SHELVES,
) -> dict[str, Any]:
    reports = [source_shelf_report(db_path, shelf, limit=DEFAULT_SOURCE_SHELF_LIMIT) for shelf in shelves]
    return {
        "catalog_db": str(db_path),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_stub_count": sum(report["generated_stub_count"] for report in reports),
        "no_inbound_count": sum(report["no_inbound_count"] for report in reports),
        "no_outbound_count": sum(report["no_outbound_count"] for report in reports),
        "placeholder_count": sum(report["placeholder_count"] for report in reports),
        "shelf_count": len(reports),
        "shelves": [shelf_summary(report) for report in reports],
        "thin_note_count": sum(report["thin_note_count"] for report in reports),
        "total_source_notes": sum(report["source_note_count"] for report in reports),
        "weak_summary_count": sum(report["weak_summary_count"] for report in reports),
    }


def source_shelf_report(
    db_path: Path = DEFAULT_DB,
    shelf: str = "math",
    *,
    limit: int = DEFAULT_SOURCE_SHELF_LIMIT,
) -> dict[str, Any]:
    if limit < 0:
        raise ValueError("limit must be greater than or equal to 0")
    normalized = normalize_shelf(shelf)
    if normalized not in DEFAULT_SOURCE_SHELVES:
        known = ", ".join(DEFAULT_SOURCE_SHELVES)
        raise KeyError(f"unknown source shelf {shelf!r}; known shelves: {known}")

    docs, links, headings = load_source_shelf_rows(db_path)
    quality = quality_index(build_page_quality_report(db_path))
    docs_by_path = {str(doc["path"]): doc for doc in docs}
    root = f"sources/{normalized}/"
    hub_path = f"{root}README.md"
    shelf_docs = sorted(
        [
            doc
            for doc in docs
            if str(doc["path"]).startswith(root) and str(doc["path"]) != hub_path
        ],
        key=lambda item: str(item["path"]),
    )
    inbound_by_target = inbound_links_by_target(links)
    outbound_by_source = outbound_links_by_source(links)
    notes = [
        build_source_note_entry(
            doc,
            inbound_links=inbound_by_target.get(str(doc["path"]), []),
            outbound_links=outbound_by_source.get(str(doc["path"]), []),
            quality=quality.get(str(doc["path"]), {}),
            shelf=normalized,
            heading_count=len(headings.get(str(doc["path"]), [])),
        )
        for doc in shelf_docs
    ]
    priority_queue = sorted(notes, key=priority_sort_key)
    weak_summaries = sorted([note for note in notes if "weak_summary" in note["quality_flags"]], key=priority_sort_key)
    thin_notes = sorted([note for note in notes if "thin_note" in note["quality_flags"]], key=priority_sort_key)
    generated_stubs = sorted([note for note in notes if "generated_stub" in note["quality_flags"]], key=priority_sort_key)
    no_inbound = sorted([note for note in notes if "no_inbound" in note["quality_flags"]], key=priority_sort_key)
    no_outbound = sorted([note for note in notes if "no_outbound_concept_or_project_links" in note["quality_flags"]], key=priority_sort_key)
    placeholders = sorted([note for note in notes if note["source_type"] == "placeholder"], key=priority_sort_key)
    high_use = sorted(notes, key=lambda item: (-item["inbound_count"], item["path"]))[:limit]
    top_actions = top_source_shelf_actions(
        generated_stub_count=len(generated_stubs),
        no_inbound_count=len(no_inbound),
        no_outbound_count=len(no_outbound),
        placeholder_count=len(placeholders),
        thin_note_count=len(thin_notes),
        weak_summary_count=len(weak_summaries),
    )
    return {
        "catalog_db": str(db_path),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_stub_count": len(generated_stubs),
        "generated_stubs": limit_items(generated_stubs, limit),
        "high_use_sources": high_use,
        "hub_path": hub_path,
        "hub_present": hub_path in docs_by_path,
        "lane_counts": lane_counts(notes),
        "limit": limit,
        "no_inbound_count": len(no_inbound),
        "no_inbound_sources": limit_items(no_inbound, limit),
        "no_outbound_count": len(no_outbound),
        "no_outbound_sources": limit_items(no_outbound, limit),
        "notes": notes,
        "placeholder_count": len(placeholders),
        "placeholders": limit_items(placeholders, limit),
        "priority_queue": limit_items(priority_queue, limit),
        "root": root,
        "shelf": normalized,
        "source_note_count": len(notes),
        "thin_note_count": len(thin_notes),
        "thin_notes": limit_items(thin_notes, limit),
        "top_actions": top_actions,
        "weak_summary_count": len(weak_summaries),
        "weak_summaries": limit_items(weak_summaries, limit),
    }


def write_source_shelf_reports(
    db_path: Path = DEFAULT_DB,
    output_dir: Path = DEFAULT_SOURCE_SHELF_REPORT_DIR,
    *,
    limit: int = DEFAULT_SOURCE_SHELF_LIMIT,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = source_shelf_summary(db_path)
    files: list[str] = []

    index_path = output_dir / "README.md"
    index_path.write_text(render_source_shelf_summary_markdown(summary))
    files.append(str(index_path))

    for shelf in DEFAULT_SOURCE_SHELVES:
        report = source_shelf_report(db_path, shelf, limit=limit)
        path = output_dir / f"{shelf}.md"
        path.write_text(render_source_shelf_markdown(report))
        files.append(str(path))

    return {
        "file_count": len(files),
        "files": files,
        "generated_stub_count": summary["generated_stub_count"],
        "limit": limit,
        "no_inbound_count": summary["no_inbound_count"],
        "no_outbound_count": summary["no_outbound_count"],
        "output_dir": str(output_dir),
        "shelf_count": summary["shelf_count"],
        "thin_note_count": summary["thin_note_count"],
        "total_source_notes": summary["total_source_notes"],
        "weak_summary_count": summary["weak_summary_count"],
    }


def load_source_shelf_rows(
    db_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        docs = con.execute(
            """
            SELECT doc_id, path, title, kind, byte_size, text
            FROM documents
            WHERE path LIKE 'sources/%'
            ORDER BY path
            """
        ).fetchall()
        links = con.execute(
            """
            SELECT source_path, target_raw, target_path, label, link_kind, line, resolved
            FROM links
            WHERE resolved = 1 AND target_path IS NOT NULL
            ORDER BY source_path, target_path, line
            """
        ).fetchall()
        span_rows = con.execute(
            """
            SELECT path, heading, level
            FROM spans
            WHERE level > 0
            ORDER BY path, ordinal
            """
        ).fetchall()
    headings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in span_rows:
        headings[str(row["path"])].append(dict(row))
    return [dict(row) for row in docs], [dict(row) for row in links], headings


def build_source_note_entry(
    doc: dict[str, Any],
    *,
    inbound_links: list[dict[str, Any]],
    outbound_links: list[dict[str, Any]],
    quality: dict[str, Any],
    shelf: str,
    heading_count: int,
) -> dict[str, Any]:
    path = str(doc["path"])
    text = str(doc["text"])
    concept_project_links = concept_project_targets(outbound_links)
    inbound_usage = concept_project_sources(inbound_links)
    source_type = classify_source_note(path, text)
    flags = quality_flags(
        path,
        source_type=source_type,
        has_source_summary=has_source_summary(text),
        quality=quality,
        inbound_usage=inbound_usage,
        concept_project_links=concept_project_links,
    )
    return {
        "byte_size": int(doc["byte_size"]),
        "concept_project_links": concept_project_links,
        "curation_status": curation_status(source_type),
        "document_id": metadata_value(text, "document_id"),
        "heading_count": heading_count,
        "inbound_count": len(inbound_links),
        "inbound_usage": inbound_usage,
        "lane": classify_lane(shelf, path, str(doc["title"]), concept_project_links),
        "outbound_count": len(outbound_links),
        "output_root": metadata_value(text, "output_root"),
        "path": path,
        "priority": priority_for_flags(flags),
        "quality_flags": flags,
        "recommended_action": recommended_action(flags),
        "shelf": shelf,
        "source_type": source_type,
        "summary_word_count": int(quality.get("summary_word_count", 0)),
        "title": doc["title"],
        "word_count": int(quality.get("word_count", 0)),
    }


def quality_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for key in ("generated_stubs", "missing_summaries", "thin_notes", "unclear_hubs"):
        for item in report[key]:
            path = str(item["path"])
            entry = by_path.setdefault(path, {})
            entry.update(item)
            flags = entry.setdefault("_quality_sets", set())
            flags.add(key)
    return by_path


def inbound_links_by_target(links: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        grouped[str(link["target_path"])].append(link)
    return grouped


def outbound_links_by_source(links: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        grouped[str(link["source_path"])].append(link)
    return grouped


def concept_project_targets(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"label": str(link["label"]), "path": str(link["target_path"])}
        for link in links
        if is_concept_or_project_path(str(link["target_path"]))
    ]


def concept_project_sources(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"label": str(link["label"]), "path": str(link["source_path"])}
        for link in links
        if is_concept_or_project_path(str(link["source_path"]))
    ]


def is_concept_or_project_path(path: str) -> bool:
    return path.startswith("concepts/") or path.startswith("projects/")


def classify_source_note(path: str, text: str) -> str:
    name = PurePosixPath(path).name.lower()
    if name == "readme.md":
        return "readme"
    if name.startswith("page--") or "generated stub" in text.lower():
        return "placeholder"
    if metadata_value(text, "document_id") == "n/a" or metadata_value(text, "output_root") == "n/a":
        return "oss_pattern"
    return "book"


def curation_status(source_type: str) -> str:
    if source_type == "book":
        return "active_or_unlisted"
    if source_type == "oss_pattern":
        return "pattern"
    if source_type == "placeholder":
        return "placeholder"
    return "unknown"


def metadata_value(text: str, key: str) -> str | None:
    match = re.search(rf"^- {re.escape(key)}:\s*`([^`]+)`\s*$", text, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def quality_flags(
    path: str,
    *,
    source_type: str,
    has_source_summary: bool,
    quality: dict[str, Any],
    inbound_usage: list[dict[str, Any]],
    concept_project_links: list[dict[str, Any]],
) -> list[str]:
    sets = quality.get("_quality_sets", set())
    flags: list[str] = []
    if "missing_summaries" in sets and not has_source_summary:
        flags.append("weak_summary")
    if "thin_notes" in sets:
        flags.append("thin_note")
    if "generated_stubs" in sets:
        flags.append("generated_stub")
    if not inbound_usage:
        flags.append("no_inbound")
    if not concept_project_links:
        flags.append("no_outbound_concept_or_project_links")
    if source_type == "placeholder" or PurePosixPath(path).name.lower().startswith("page--"):
        flags.append("placeholder_artifact")
    return unique(flags)


def has_source_summary(text: str) -> bool:
    match = re.search(
        r"^## Why This Source Matters\s*\n+(?P<body>.*?)(?:\n## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return False
    body = re.sub(r"[*_>#`-]+", " ", match.group("body"))
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]*", body)) >= 12


def classify_lane(shelf: str, path: str, title: str, links: list[dict[str, Any]]) -> str:
    haystack = " ".join([path, title, *[item["path"] for item in links]]).lower()
    if shelf == "math":
        return classify_math_lane(haystack)
    return classify_computer_lane(haystack)


def classify_math_lane(haystack: str) -> str:
    rules = [
        ("heavy_tails", ("heavy", "tail", "resnick")),
        ("stochastic_processes", ("stochastic", "brownian", "sde", "karatzas", "oksendal")),
        ("probability_measure", ("probability", "measure", "billingsley", "durrett", "kallenberg")),
        ("functional_analysis", ("functional", "sobolev", "banach", "hilbert", "brezis", "conway", "lax")),
        ("geometry_manifolds", ("manifold", "riemannian", "geometry", "petersen", "jost")),
        ("spectral_numerical_methods", ("spectral", "chebyshev", "fourier")),
        ("numerical_linear_algebra", ("matrix", "linear_algebra", "linear algebra", "golub", "trefethen", "meckes")),
        ("differential_equations_applied_math", ("differential", "equation", "applied", "pde", "evans", "strang")),
        ("algebra", ("algebra", "jacobson")),
        ("quantum_operator_methods", ("quantum", "operator", "beyer")),
    ]
    return first_matching_lane(haystack, rules, default="math_general")


def classify_computer_lane(haystack: str) -> str:
    rules = [
        ("storage_retrieval", ("data_intensive", "data-intensive", "storage", "retrieval", "kleppmann")),
        ("architecture_systems", ("architecture", "clean", "pragmatic", "boundary")),
        ("algorithms_data_structures", ("algorithm", "data_structures", "data structures", "cormen", "roughgarden")),
        ("numerical_stability", ("accuracy", "stability", "numerical", "higham")),
        ("performance_concurrency", ("performance", "concurrency", "optimizing", "thread")),
        ("networking_distributed", ("networking", "distributed", "kurose", "ross")),
        ("language_systems_programming", ("programming", "modern_c", "modern c", "stroustrup", "meyers")),
        ("quant_calculator_patterns", ("quant", "pricing", "qalculate", "portfolio", "filterpy")),
    ]
    return first_matching_lane(haystack, rules, default="computer_general")


def first_matching_lane(
    haystack: str,
    rules: list[tuple[str, tuple[str, ...]]],
    *,
    default: str,
) -> str:
    for lane, needles in rules:
        if any(needle in haystack for needle in needles):
            return lane
    return default


def priority_for_flags(flags: list[str]) -> str:
    if "placeholder_artifact" in flags or "generated_stub" in flags:
        return "P0"
    if "weak_summary" in flags and ("no_inbound" in flags or "no_outbound_concept_or_project_links" in flags):
        return "P0"
    if "weak_summary" in flags or "thin_note" in flags:
        return "P1"
    if "no_inbound" in flags or "no_outbound_concept_or_project_links" in flags:
        return "P2"
    return "P3"


def recommended_action(flags: list[str]) -> str:
    if "placeholder_artifact" in flags or "generated_stub" in flags:
        return "review placeholder artifact and replace or remove from the source shelf"
    if "weak_summary" in flags:
        return "add a clear opening summary describing when to use this source"
    if "thin_note" in flags:
        return "expand source note with strongest ideas, questions, and bridge links"
    if "no_outbound_concept_or_project_links" in flags:
        return "add concept or project bridge links"
    if "no_inbound" in flags:
        return "decide where this source should be linked from"
    return "keep as maintained source note"


def priority_sort_key(note: dict[str, Any]) -> tuple[int, int, int, str]:
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return (
        priority_rank.get(str(note["priority"]), 9),
        -len(note["quality_flags"]),
        -int(note["inbound_count"]),
        str(note["path"]),
    )


def top_source_shelf_actions(
    *,
    generated_stub_count: int,
    no_inbound_count: int,
    no_outbound_count: int,
    placeholder_count: int,
    thin_note_count: int,
    weak_summary_count: int,
) -> list[dict[str, Any]]:
    candidates = [
        (
            "review_placeholders",
            max(placeholder_count, generated_stub_count),
            "remove or replace generated/page artifacts",
        ),
        ("add_source_summaries", weak_summary_count, "add clear opening summaries for source cards"),
        ("expand_thin_source_notes", thin_note_count, "expand source notes that are too short to stand alone"),
        ("add_bridge_links", no_outbound_count, "link source notes to concepts or projects they support"),
        ("add_inbound_routes", no_inbound_count, "link useful sources from concepts, projects, or shelf hubs"),
    ]
    return [
        {"action": action, "count": count, "reason": reason}
        for action, count, reason in candidates
        if count > 0
    ][:5]


def shelf_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_stub_count": report["generated_stub_count"],
        "hub_path": report["hub_path"],
        "hub_present": report["hub_present"],
        "lane_counts": report["lane_counts"],
        "no_inbound_count": report["no_inbound_count"],
        "no_outbound_count": report["no_outbound_count"],
        "placeholder_count": report["placeholder_count"],
        "shelf": report["shelf"],
        "source_note_count": report["source_note_count"],
        "thin_note_count": report["thin_note_count"],
        "top_actions": report["top_actions"],
        "weak_summary_count": report["weak_summary_count"],
    }


def lane_counts(notes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for note in notes:
        counts[str(note["lane"])] += 1
    return dict(sorted(counts.items()))


def normalize_shelf(value: str) -> str:
    parts = PurePosixPath(value.strip().strip("/")).parts
    if len(parts) >= 2 and parts[0] == "sources":
        return parts[1]
    return value.strip().strip("/")


def render_source_shelf_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Source Shelf Reports",
        "",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- catalog_db: `{summary['catalog_db']}`",
        f"- shelf_count: `{summary['shelf_count']}`",
        f"- total_source_notes: `{summary['total_source_notes']}`",
        f"- weak_summary_count: `{summary['weak_summary_count']}`",
        f"- thin_note_count: `{summary['thin_note_count']}`",
        f"- no_inbound_count: `{summary['no_inbound_count']}`",
        f"- no_outbound_count: `{summary['no_outbound_count']}`",
        f"- placeholder_count: `{summary['placeholder_count']}`",
        "",
        "## Shelf Priority",
        "",
        "| shelf | notes | weak summaries | thin notes | no inbound | no outbound | placeholders | hub present |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for shelf in summary["shelves"]:
        lines.append(
            "| {shelf} | {notes} | {weak} | {thin} | {inbound} | {outbound} | {placeholders} | {hub} |".format(
                hub="yes" if shelf["hub_present"] else "no",
                inbound=shelf["no_inbound_count"],
                notes=shelf["source_note_count"],
                outbound=shelf["no_outbound_count"],
                placeholders=shelf["placeholder_count"],
                shelf=f"[{shelf['shelf']}]({shelf['shelf']}.md)",
                thin=shelf["thin_note_count"],
                weak=shelf["weak_summary_count"],
            )
        )
    lines.extend(["", "## Shelves", ""])
    for shelf in summary["shelves"]:
        lines.append(
            "- "
            f"[{shelf['shelf']}]({shelf['shelf']}.md): "
            f"notes `{shelf['source_note_count']}`, "
            f"weak summaries `{shelf['weak_summary_count']}`, "
            f"thin notes `{shelf['thin_note_count']}`, "
            f"bridge gaps `{shelf['no_outbound_count']}`"
        )
    lines.append("")
    return "\n".join(lines)


def render_source_shelf_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Source Shelf Report: {report['shelf']}",
        "",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- catalog_db: `{report['catalog_db']}`",
        f"- root: `{report['root']}`",
        f"- hub_path: `{report['hub_path']}`",
        f"- hub_present: `{'true' if report['hub_present'] else 'false'}`",
        f"- source_note_count: `{report['source_note_count']}`",
        f"- weak_summary_count: `{report['weak_summary_count']}`",
        f"- thin_note_count: `{report['thin_note_count']}`",
        f"- no_inbound_count: `{report['no_inbound_count']}`",
        f"- no_outbound_count: `{report['no_outbound_count']}`",
        f"- placeholder_count: `{report['placeholder_count']}`",
        f"- detail_limit: `{report['limit']}`",
        "",
        "## Librarian Summary",
        "",
        "| queue | count |",
        "|---|---:|",
        f"| weak summaries | {report['weak_summary_count']} |",
        f"| thin notes | {report['thin_note_count']} |",
        f"| no inbound routes | {report['no_inbound_count']} |",
        f"| no concept/project bridge links | {report['no_outbound_count']} |",
        f"| placeholders | {report['placeholder_count']} |",
        "",
        "## Top Actions",
        "",
    ]
    append_actions(lines, report["top_actions"])
    lines.extend(["", "## Priority Queue", ""])
    append_note_queue(lines, report["priority_queue"], empty="none")
    lines.extend(["## Source Notes", ""])
    append_note_queue(lines, report["notes"], empty="none")
    lines.extend(["## High-Use Sources", ""])
    append_note_queue(lines, report["high_use_sources"], empty="none")
    lines.extend(["## Weak Summaries", ""])
    append_note_queue(lines, report["weak_summaries"], empty="none")
    lines.extend(["## Thin Notes", ""])
    append_note_queue(lines, report["thin_notes"], empty="none")
    lines.extend(["## Unbridged Sources", ""])
    append_note_queue(lines, report["no_outbound_sources"], empty="none")
    return "\n".join(lines)


def append_actions(lines: list[str], actions: list[dict[str, Any]]) -> None:
    if not actions:
        lines.append("- none")
        return
    for action in actions:
        lines.append(f"- `{action['action']}` count `{action['count']}`: {action['reason']}")


def append_note_queue(lines: list[str], notes: list[dict[str, Any]], *, empty: str) -> None:
    if not notes:
        lines.extend([f"- {empty}", ""])
        return
    for note in notes:
        flags = ", ".join(note["quality_flags"]) or "none"
        lines.append(
            "- `{path}` priority `{priority}` lane `{lane}` title `{title}` inbound `{inbound}` outbound `{outbound}` flags `{flags}`".format(
                flags=flags,
                inbound=note["inbound_count"],
                lane=note["lane"],
                outbound=note["outbound_count"],
                path=note["path"],
                priority=note["priority"],
                title=note["title"],
            )
        )
    lines.append("")


def limit_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return list(items[:limit])


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
