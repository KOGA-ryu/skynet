from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any


DEFAULT_PAGE_QUALITY_DIR = Path("state/page_quality")
DEFAULT_STUB_FILL_PACKET_DIR_NAME = "stub_fill_packets"
THIN_WORD_LIMIT = 120
THIN_BYTE_LIMIT = 800
SUMMARY_WORD_LIMIT = 25
HUB_OVERVIEW_WORD_LIMIT = 40
HUB_OUTBOUND_LINK_LIMIT = 3
GENERATED_STUB_MARKERS = (
    "this stub exists because current wiki notes link to",
    "- status: stub",
    "content has not been filled in yet",
)


def page_quality_summary(db_path: Path) -> dict[str, Any]:
    report = build_page_quality_report(db_path)
    return {
        "generated_at_utc": report["generated_at_utc"],
        "generated_stub_count": len(report["generated_stubs"]),
        "missing_summary_count": len(report["missing_summaries"]),
        "thin_note_count": len(report["thin_notes"]),
        "thresholds": report["thresholds"],
        "total_candidates": len(report["thin_notes"])
        + len(report["missing_summaries"])
        + len(report["unclear_hubs"]),
        "unclear_hub_count": len(report["unclear_hubs"]),
    }


def generated_stubs_report(db_path: Path) -> dict[str, Any]:
    report = build_page_quality_report(db_path)
    stubs = report["generated_stubs"]
    return {
        "generated_at_utc": report["generated_at_utc"],
        "stub_count": len(stubs),
        "stubs": stubs,
        "total_inbound_references": sum(int(stub["inbound_count"]) for stub in stubs),
    }


def stub_fill_queue(
    db_path: Path,
    *,
    limit: int | None = None,
    output_dir: Path = DEFAULT_PAGE_QUALITY_DIR,
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("limit must be greater than or equal to 0")
    report = build_page_quality_report(db_path)
    return build_stub_fill_queue(report, limit=limit, output_dir=output_dir)


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
    stub_queue = build_stub_fill_queue(report, output_dir=output_dir)
    packet_dir = output_dir / DEFAULT_STUB_FILL_PACKET_DIR_NAME
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
        "generated_stubs.md": render_generated_stubs_markdown(report["generated_stubs"]),
        "stub_fill_queue.md": render_stub_fill_queue_markdown(stub_queue),
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
    packet_dir.mkdir(parents=True, exist_ok=True)
    for entry in stub_queue["queue"]:
        packet_path = Path(str(entry["packet_path"]))
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(render_stub_fill_packet_markdown(entry))
        written.append(str(packet_path))
    return {
        "file_count": len(written),
        "files": written,
        "generated_stub_count": len(report["generated_stubs"]),
        "missing_summary_count": len(report["missing_summaries"]),
        "output_dir": str(output_dir),
        "stub_fill_packet_count": len(stub_queue["queue"]),
        "stub_fill_queue_path": str(output_dir / "stub_fill_queue.md"),
        "thin_note_count": len(report["thin_notes"]),
        "unclear_hub_count": len(report["unclear_hubs"]),
    }


def build_page_quality_report(db_path: Path) -> dict[str, Any]:
    docs, links, headings = load_quality_rows(db_path)
    inbound_counts, outbound_counts = link_counts(links)
    inbound_by_target = inbound_links_by_target(links)
    heading_counts = {
        path: sum(1 for heading in items if int(heading["level"]) > 0)
        for path, items in headings.items()
    }
    entries = [
        build_quality_entry(
            doc,
            inbound_count=inbound_counts.get(str(doc["path"]), 0),
            inbound_links=inbound_by_target.get(str(doc["path"]), []),
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
    generated_stubs = sorted(
        [generated_stub_entry(entry) for entry in entries if entry["generated_stub"]],
        key=lambda item: (
            -int(item["inbound_count"]),
            -int(item["source_count"]),
            0 if is_hub_path(str(item["path"])) else 1,
            item["path"],
        ),
    )
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_stubs": generated_stubs,
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
            SELECT source_path, target_raw, target_path, label, link_kind, line, resolved
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
    inbound_links: list[dict[str, Any]],
    outbound_count: int,
    heading_count: int,
) -> dict[str, Any]:
    text = str(doc["text"])
    summary = first_summary_paragraph(text)
    summary_words = word_count(summary)
    words = word_count(text)
    inbound_sources = inbound_source_reports(inbound_links)
    entry = {
        "byte_size": int(doc["byte_size"]),
        "generated_stub": is_generated_stub_text(text),
        "heading_count": heading_count,
        "inbound_count": inbound_count,
        "inbound_sources": inbound_sources,
        "kind": doc["kind"],
        "outbound_link_count": outbound_count,
        "path": doc["path"],
        "source_count": len(inbound_sources),
        "summary": summary,
        "summary_word_count": summary_words,
        "suggested_next_action": "add a clear summary and enough context for standalone use",
        "title": doc["title"],
        "word_count": words,
    }
    entry["reasons"] = candidate_reasons(entry)
    return entry


def generated_stub_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "byte_size": entry["byte_size"],
        "inbound_count": entry["inbound_count"],
        "inbound_sources": entry["inbound_sources"],
        "path": entry["path"],
        "reasons": unique(["generated_stub_marker", *entry["reasons"]]),
        "source_count": entry["source_count"],
        "suggested_next_action": "replace the generated stub with a useful summary, decisions, evidence, and links",
        "title": entry["title"],
        "word_count": entry["word_count"],
    }


def build_stub_fill_queue(
    report: dict[str, Any],
    *,
    limit: int | None = None,
    output_dir: Path = DEFAULT_PAGE_QUALITY_DIR,
) -> dict[str, Any]:
    packet_dir = output_dir / DEFAULT_STUB_FILL_PACKET_DIR_NAME
    entries = sorted(
        [stub_fill_entry(stub, packet_dir=packet_dir) for stub in report["generated_stubs"]],
        key=stub_fill_sort_key,
    )
    for rank, entry in enumerate(entries, start=1):
        entry["rank"] = rank
    queue = entries if limit is None else entries[:limit]
    return {
        "generated_at_utc": report["generated_at_utc"],
        "limit": limit,
        "priority_counts": priority_counts(entries),
        "queue": queue,
        "queue_count": len(queue),
        "stub_count": len(entries),
        "total_inbound_references": sum(int(entry["inbound_count"]) for entry in entries),
    }


def stub_fill_entry(stub: dict[str, Any], *, packet_dir: Path) -> dict[str, Any]:
    path = str(stub["path"])
    entry = {
        "byte_size": int(stub["byte_size"]),
        "group": stub_group(path),
        "inbound_count": int(stub["inbound_count"]),
        "inbound_sources": stub["inbound_sources"],
        "packet_path": str(packet_dir / stub_packet_filename(path)),
        "path": path,
        "priority": stub_fill_priority(stub),
        "rank": 0,
        "reasons": list(stub["reasons"]),
        "source_count": int(stub["source_count"]),
        "suggested_next_action": stub["suggested_next_action"],
        "title": stub["title"],
        "word_count": int(stub["word_count"]),
    }
    return entry


def stub_fill_priority(stub: dict[str, Any]) -> str:
    path = str(stub["path"])
    inbound_count = int(stub["inbound_count"])
    source_count = int(stub["source_count"])
    if is_hub_path(path) or inbound_count >= 5 or source_count >= 3:
        return "P0"
    if inbound_count >= 2 or source_count >= 2:
        return "P1"
    return "P2"


def stub_fill_sort_key(entry: dict[str, Any]) -> tuple[int, int, int, int, str]:
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    return (
        priority_order.get(str(entry["priority"]), 9),
        -int(entry["inbound_count"]),
        -int(entry["source_count"]),
        0 if is_hub_path(str(entry["path"])) else 1,
        str(entry["path"]),
    )


def priority_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for entry in entries:
        counts[str(entry["priority"])] += 1
    return dict(sorted(counts.items()))


def stub_group(path: str) -> str:
    parts = PurePosixPath(path).parts
    if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "apps":
        return "/".join(parts[:4])
    if len(parts) >= 2 and parts[0] == "projects":
        return "/".join(parts[:2])
    if parts and parts[0] == "concepts":
        return "concepts"
    if len(parts) >= 2 and parts[0] == "sources":
        return "/".join(parts[:2])
    if parts:
        return parts[0]
    return "unknown"


def stub_packet_filename(path: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "__", path)
    safe = safe.replace("/", "__")
    if safe.endswith(".md"):
        safe = safe[:-3]
    return f"{safe}.md"


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


def inbound_links_by_target(links: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    inbound: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        if int(link["resolved"]) and link.get("target_path"):
            inbound[str(link["target_path"])].append(link)
    return inbound


def inbound_source_reports(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        grouped[str(link["source_path"])].append(link)
    return [
        {
            "labels": [str(item["label"]) for item in items],
            "line_count": len(items),
            "lines": [int(item["line"]) for item in items],
            "source_path": source_path,
        }
        for source_path, items in sorted(grouped.items())
    ]


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


def is_generated_stub_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in GENERATED_STUB_MARKERS)


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
        f"- generated_stub_count: `{len(report['generated_stubs'])}`",
        f"- unclear_hub_count: `{len(report['unclear_hubs'])}`",
        "",
        "## Reports",
        "",
        "- [Thin Notes](thin_notes.md)",
        "- [Missing Summaries](missing_summaries.md)",
        "- [Generated Stubs](generated_stubs.md)",
        "- [Stub-Fill Queue](stub_fill_queue.md)",
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


def render_generated_stubs_markdown(stubs: list[dict[str, Any]]) -> str:
    lines = [
        "# Generated Stubs",
        "",
        "Generated Markdown stubs that still need human-written content.",
        "",
        f"- generated_at_utc: `{datetime.now(UTC).isoformat(timespec='seconds')}`",
        f"- stub_count: `{len(stubs)}`",
        f"- total_inbound_references: `{sum(int(stub['inbound_count']) for stub in stubs)}`",
        "",
        "| path | title | inbound | sources | words | bytes | reasons | next action |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not stubs:
        lines.append("| none | none | 0 | 0 | 0 | 0 | none | none |")
    for stub in stubs:
        lines.append(
            "| {path} | {title} | {inbound} | {sources} | {words} | {bytes} | {reasons} | {action} |".format(
                action=escape_table(str(stub["suggested_next_action"])),
                bytes=stub["byte_size"],
                inbound=stub["inbound_count"],
                path=escape_table(f"`{stub['path']}`"),
                reasons=escape_table(", ".join(stub["reasons"])),
                sources=stub["source_count"],
                title=escape_table(str(stub["title"])),
                words=stub["word_count"],
            )
        )
    lines.extend(["", "## Inbound Sources", ""])
    if not stubs:
        lines.append("- none")
    for stub in stubs:
        lines.append(f"### `{stub['path']}`")
        if stub["inbound_sources"]:
            for source in stub["inbound_sources"]:
                labels = ", ".join(str(label) for label in source["labels"])
                lines.append(
                    f"- `{source['source_path']}` lines `{', '.join(str(line) for line in source['lines'])}` "
                    f"labels `{labels}`"
                )
        else:
            lines.append("- no inbound sources")
        lines.append("")
    return "\n".join(lines)


def render_stub_fill_queue_markdown(queue: dict[str, Any]) -> str:
    lines = [
        "# Stub-Fill Queue",
        "",
        "Ranked generated stubs that need human-written promotion packets before content replacement.",
        "",
        f"- generated_at_utc: `{queue['generated_at_utc']}`",
        f"- stub_count: `{queue['stub_count']}`",
        f"- queue_count: `{queue['queue_count']}`",
        f"- total_inbound_references: `{queue['total_inbound_references']}`",
        "",
        "## Priority Counts",
        "",
        "| priority | stubs |",
        "|---|---:|",
    ]
    for priority, count in queue["priority_counts"].items():
        lines.append(f"| `{priority}` | {count} |")
    if not queue["priority_counts"]:
        lines.append("| none | 0 |")

    lines.extend(
        [
            "",
            "## Queue",
            "",
            "| rank | priority | path | group | inbound | sources | packet | next action |",
            "|---:|---|---|---|---:|---:|---|---|",
        ]
    )
    if not queue["queue"]:
        lines.append("| 0 | none | none | none | 0 | 0 | none | none |")
    for entry in queue["queue"]:
        lines.append(
            "| {rank} | `{priority}` | {path} | `{group}` | {inbound} | {sources} | {packet} | {action} |".format(
                action=escape_table(str(entry["suggested_next_action"])),
                group=escape_table(str(entry["group"])),
                inbound=entry["inbound_count"],
                packet=escape_table(f"[packet]({relative_report_link(str(entry['packet_path']))})"),
                path=escape_table(f"`{entry['path']}`"),
                priority=entry["priority"],
                rank=entry["rank"],
                sources=entry["source_count"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_stub_fill_packet_markdown(entry: dict[str, Any]) -> str:
    lines = [
        f"# Stub-Fill Packet: {entry['title']}",
        "",
        f"- rank: `{entry['rank']}`",
        f"- priority: `{entry['priority']}`",
        f"- path: `{entry['path']}`",
        f"- group: `{entry['group']}`",
        f"- inbound_count: `{entry['inbound_count']}`",
        f"- source_count: `{entry['source_count']}`",
        f"- word_count: `{entry['word_count']}`",
        f"- byte_size: `{entry['byte_size']}`",
        f"- reasons: `{', '.join(entry['reasons'])}`",
        "",
        "## Fill Checklist",
        "",
        "- Replace generated-stub wording with a direct opening summary.",
        "- Explain what the page is for and when it should be used.",
        "- Preserve useful inbound intent from the source links below.",
        "- Add decisions, evidence, or links only when supported by existing wiki context.",
        "- Keep NAS promotion behind the reviewed patch-bundle workflow.",
        "",
        "## Inbound Sources",
        "",
    ]
    if entry["inbound_sources"]:
        for source in entry["inbound_sources"]:
            labels = ", ".join(str(label) for label in source["labels"])
            lines.append(
                "- `{source_path}` lines `{lines}` labels `{labels}`".format(
                    labels=labels,
                    lines=", ".join(str(line) for line in source["lines"]),
                    source_path=source["source_path"],
                )
            )
    else:
        lines.append("- no inbound sources")
    lines.extend(
        [
            "",
            "## Replacement Scaffold",
            "",
            f"# {entry['title']}",
            "",
            "<write a specific opening summary from the inbound context>",
            "",
            "## Purpose",
            "",
            "<state what this page should help the wiki user understand or do>",
            "",
            "## Key Details",
            "",
            "- <supported detail>",
            "- <supported detail>",
            "",
            "## Related Links",
            "",
            "- <add only validated wiki links>",
            "",
        ]
    )
    return "\n".join(lines)


def relative_report_link(path: str) -> str:
    parts = PurePosixPath(path).parts
    if len(parts) >= 3 and parts[-2] == DEFAULT_STUB_FILL_PACKET_DIR_NAME:
        return f"{DEFAULT_STUB_FILL_PACKET_DIR_NAME}/{parts[-1]}"
    return path


def escape_table(value: str) -> str:
    return value.replace("|", "\\|")
