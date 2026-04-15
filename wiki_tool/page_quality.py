from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any


DEFAULT_PAGE_QUALITY_DIR = Path("state/page_quality")
THIN_WORD_LIMIT = 120
THIN_BYTE_LIMIT = 800
SUMMARY_WORD_LIMIT = 25
HUB_OVERVIEW_WORD_LIMIT = 40
HUB_OUTBOUND_LINK_LIMIT = 3


def page_quality_summary(db_path: Path) -> dict[str, Any]:
    report = build_page_quality_report(db_path)
    return {
        "generated_at_utc": report["generated_at_utc"],
        "missing_summary_count": len(report["missing_summaries"]),
        "thin_note_count": len(report["thin_notes"]),
        "thresholds": report["thresholds"],
        "total_candidates": len(report["thin_notes"])
        + len(report["missing_summaries"])
        + len(report["unclear_hubs"]),
        "unclear_hub_count": len(report["unclear_hubs"]),
    }


def thin_notes_report(db_path: Path) -> dict[str, Any]:
    report = build_page_quality_report(db_path)
    return {
        "generated_at_utc": report["generated_at_utc"],
        "thin_notes": report["thin_notes"],
        "thresholds": report["thresholds"],
        "total": len(report["thin_notes"]),
    }


def missing_summaries_report(db_path: Path) -> dict[str, Any]:
    report = build_page_quality_report(db_path)
    return {
        "generated_at_utc": report["generated_at_utc"],
        "missing_summaries": report["missing_summaries"],
        "thresholds": report["thresholds"],
        "total": len(report["missing_summaries"]),
    }


def unclear_hubs_report(db_path: Path) -> dict[str, Any]:
    report = build_page_quality_report(db_path)
    return {
        "generated_at_utc": report["generated_at_utc"],
        "thresholds": report["thresholds"],
        "total": len(report["unclear_hubs"]),
        "unclear_hubs": report["unclear_hubs"],
    }


def write_page_quality_reports(
    db_path: Path,
    output_dir: Path = DEFAULT_PAGE_QUALITY_DIR,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_page_quality_report(db_path)
    files = {
        "README.md": render_index_markdown(report),
        "thin_notes.md": render_candidates_markdown(
            "Thin Notes",
            report["thin_notes"],
            intro="Notes that appear too short to be useful canonical pages.",
        ),
        "missing_summaries.md": render_candidates_markdown(
            "Missing Summaries",
            report["missing_summaries"],
            intro="Notes whose opening summary is missing, very short, or still stub-like.",
        ),
        "unclear_hubs.md": render_candidates_markdown(
            "Unclear Hubs",
            report["unclear_hubs"],
            intro="Hub pages that need stronger overview text, links, or section structure.",
        ),
    }
    written: list[str] = []
    for filename, text in files.items():
        path = output_dir / filename
        path.write_text(text)
        written.append(str(path))
    return {
        "file_count": len(written),
        "files": written,
        "missing_summary_count": len(report["missing_summaries"]),
        "output_dir": str(output_dir),
        "thin_note_count": len(report["thin_notes"]),
        "unclear_hub_count": len(report["unclear_hubs"]),
    }


def build_page_quality_report(db_path: Path) -> dict[str, Any]:
    docs, links, headings = load_quality_rows(db_path)
    inbound_counts, outbound_counts = link_counts(links)
    heading_counts = {
        path: sum(1 for heading in items if int(heading["level"]) > 0)
        for path, items in headings.items()
    }
    entries = [
        build_quality_entry(
            doc,
            inbound_count=inbound_counts.get(str(doc["path"]), 0),
            outbound_count=outbound_counts.get(str(doc["path"]), 0),
            heading_count=heading_counts.get(str(doc["path"]), 0),
        )
        for doc in docs
    ]
    thin = sorted(
        [entry for entry in entries if is_thin_note(entry)],
        key=lambda item: (item["word_count"], item["byte_size"], item["path"]),
    )
    missing = sorted(
        [entry for entry in entries if is_missing_summary(entry)],
        key=lambda item: (item["summary_word_count"], item["word_count"], item["path"]),
    )
    unclear = sorted(
        [entry for entry in entries if is_unclear_hub(entry)],
        key=lambda item: (-len(item["reasons"]), item["outbound_link_count"], item["path"]),
    )
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "missing_summaries": missing,
        "thin_notes": thin,
        "thresholds": thresholds(),
        "unclear_hubs": unclear,
    }


def load_quality_rows(
    db_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        docs = con.execute(
            """
            SELECT doc_id, path, title, kind, byte_size, text
            FROM documents
            ORDER BY path
            """
        ).fetchall()
        links = con.execute(
            """
            SELECT source_path, target_path, resolved
            FROM links
            ORDER BY source_path, target_path
            """
        ).fetchall()
        heading_rows = con.execute(
            """
            SELECT path, heading, level
            FROM spans
            WHERE level > 0
            ORDER BY path, ordinal
            """
        ).fetchall()
    headings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in heading_rows:
        headings[str(row["path"])].append(dict(row))
    return [dict(row) for row in docs], [dict(row) for row in links], headings


def build_quality_entry(
    doc: dict[str, Any],
    *,
    inbound_count: int,
    outbound_count: int,
    heading_count: int,
) -> dict[str, Any]:
    text = str(doc["text"])
    summary = first_summary_paragraph(text)
    summary_words = word_count(summary)
    words = word_count(text)
    entry = {
        "byte_size": int(doc["byte_size"]),
        "heading_count": heading_count,
        "inbound_count": inbound_count,
        "kind": doc["kind"],
        "outbound_link_count": outbound_count,
        "path": doc["path"],
        "summary": summary,
        "summary_word_count": summary_words,
        "suggested_next_action": "add a clear summary and enough context for standalone use",
        "title": doc["title"],
        "word_count": words,
    }
    entry["reasons"] = candidate_reasons(entry)
    return entry


def candidate_reasons(entry: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if int(entry["word_count"]) < THIN_WORD_LIMIT:
        reasons.append("low_word_count")
    if int(entry["byte_size"]) < THIN_BYTE_LIMIT:
        reasons.append("low_byte_size")
    if not entry["summary"]:
        reasons.append("missing_opening_summary")
    elif int(entry["summary_word_count"]) < SUMMARY_WORD_LIMIT:
        reasons.append("short_opening_summary")
    if looks_like_stub(str(entry["summary"])):
        reasons.append("stub_like_summary")
    if is_hub_path(str(entry["path"])):
        if int(entry["summary_word_count"]) < HUB_OVERVIEW_WORD_LIMIT:
            reasons.append("hub_overview_too_short")
        if int(entry["outbound_link_count"]) < HUB_OUTBOUND_LINK_LIMIT:
            reasons.append("hub_has_few_outbound_links")
        if int(entry["heading_count"]) < 2:
            reasons.append("hub_has_few_section_headings")
    return unique(reasons)


def is_thin_note(entry: dict[str, Any]) -> bool:
    path = str(entry["path"])
    if excluded_quality_path(path) or is_hub_path(path):
        return False
    return int(entry["word_count"]) < THIN_WORD_LIMIT or int(entry["byte_size"]) < THIN_BYTE_LIMIT


def is_missing_summary(entry: dict[str, Any]) -> bool:
    path = str(entry["path"])
    if excluded_quality_path(path):
        return False
    return (
        not entry["summary"]
        or int(entry["summary_word_count"]) < SUMMARY_WORD_LIMIT
        or looks_like_stub(str(entry["summary"]))
    )


def is_unclear_hub(entry: dict[str, Any]) -> bool:
    path = str(entry["path"])
    if excluded_quality_path(path) or not is_hub_path(path):
        return False
    return (
        int(entry["summary_word_count"]) < HUB_OVERVIEW_WORD_LIMIT
        or int(entry["outbound_link_count"]) < HUB_OUTBOUND_LINK_LIMIT
        or int(entry["heading_count"]) < 2
    )


def link_counts(links: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    inbound: dict[str, int] = defaultdict(int)
    outbound: dict[str, int] = defaultdict(int)
    for link in links:
        source = str(link["source_path"])
        if int(link["resolved"]) and link.get("target_path"):
            inbound[str(link["target_path"])] += 1
            outbound[source] += 1
    return inbound, outbound


def first_summary_paragraph(text: str) -> str:
    in_frontmatter = False
    lines: list[str] = []
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("#"):
            if stripped.startswith("##") and not lines:
                break
            continue
        if stripped.startswith("```"):
            break
        lines.append(stripped)
    return clean_markdown_text(" ".join(lines))


def clean_markdown_text(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda match: match.group(2) or match.group(1), value)
    value = re.sub(r"[*_>#-]+", " ", value)
    return " ".join(value.split())


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]*", clean_markdown_text(text)))


def looks_like_stub(summary: str) -> bool:
    lowered = summary.lower()
    phrases = [
        "generated stub",
        "status: stub",
        "this stub exists",
        "needs human content",
        "placeholder",
        "todo",
        "tbd",
    ]
    return any(phrase in lowered for phrase in phrases)


def excluded_quality_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        path.startswith("templates/")
        or path.startswith("state/")
        or path.startswith("backups/")
        or path.startswith("patch_bundles/")
        or "state" in parts
        or "runtime" in parts
        or "tmp" in parts
    )


def is_hub_path(path: str) -> bool:
    return path == "README.md" or path.endswith("/README.md")


def thresholds() -> dict[str, int]:
    return {
        "hub_outbound_link_limit": HUB_OUTBOUND_LINK_LIMIT,
        "hub_overview_word_limit": HUB_OVERVIEW_WORD_LIMIT,
        "summary_word_limit": SUMMARY_WORD_LIMIT,
        "thin_byte_limit": THIN_BYTE_LIMIT,
        "thin_word_limit": THIN_WORD_LIMIT,
    }


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def render_index_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Page Quality Reports",
        "",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- thin_note_count: `{len(report['thin_notes'])}`",
        f"- missing_summary_count: `{len(report['missing_summaries'])}`",
        f"- unclear_hub_count: `{len(report['unclear_hubs'])}`",
        "",
        "## Reports",
        "",
        "- [Thin Notes](thin_notes.md)",
        "- [Missing Summaries](missing_summaries.md)",
        "- [Unclear Hubs](unclear_hubs.md)",
        "",
    ]
    return "\n".join(lines)


def render_candidates_markdown(title: str, candidates: list[dict[str, Any]], *, intro: str) -> str:
    lines = [
        f"# {title}",
        "",
        intro,
        "",
        f"- generated_at_utc: `{datetime.now(UTC).isoformat(timespec='seconds')}`",
        f"- candidate_count: `{len(candidates)}`",
        "",
        "| path | title | words | bytes | inbound | outbound | reasons | next action |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not candidates:
        lines.append("| none | none | 0 | 0 | 0 | 0 | none | none |")
    for item in candidates:
        lines.append(
            "| {path} | {title} | {words} | {bytes} | {inbound} | {outbound} | {reasons} | {action} |".format(
                action=escape_table(str(item["suggested_next_action"])),
                bytes=item["byte_size"],
                inbound=item["inbound_count"],
                outbound=item["outbound_link_count"],
                path=escape_table(f"`{item['path']}`"),
                reasons=escape_table(", ".join(item["reasons"])),
                title=escape_table(str(item["title"])),
                words=item["word_count"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def escape_table(value: str) -> str:
    return value.replace("|", "\\|")
