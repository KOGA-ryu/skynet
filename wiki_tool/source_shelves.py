from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any

from wiki_tool.catalog import DEFAULT_DB, latest_scan_run
from wiki_tool.page_quality import build_page_quality_report


DEFAULT_SOURCE_SHELF_CLEANUP_BUNDLE = Path("patch_bundles/source_shelves_computer_cleanup.json")
DEFAULT_SOURCE_SHELF_BRIDGE_BUNDLE = Path("patch_bundles/source_shelves_math_bridge_map.json")
DEFAULT_SOURCE_SHELF_REPORT_DIR = Path("state/source_shelf_reports")
DEFAULT_SOURCE_SHELF_LIMIT = 25
DEFAULT_SOURCE_SHELVES = ("math", "computer")
MATH_BRIDGE_MAP_PATH = "sources/math/book_to_concept_bridge_map.md"
MATH_README_PATH = "sources/math/README.md"
GENERATED_SOURCE_SHELF_HUBS = {MATH_BRIDGE_MAP_PATH, MATH_README_PATH}
COMPUTER_SOURCE_SUMMARIES = {
    "sources/computer/audio_shader_studio_patterns.md": (
        "Use this pattern note when a design needs to turn streaming inputs into named rolling "
        "features, thresholds, triggers, and reusable downstream signal surfaces."
    ),
    "sources/computer/arch_patterns.md": (
        "Use this pattern note when volatility modeling needs explicit diagnostics, stationarity "
        "checks, bootstrap tools, and covariance estimators instead of a single opaque risk signal."
    ),
    "sources/computer/field_generation_patterns.md": (
        "Use this pattern note when local vector-field rules, perturbations, and particle traces "
        "need to explain how repeated local updates create global structure."
    ),
    "sources/computer/filterpy_patterns.md": (
        "Use this pattern note when latent-state estimation needs a clear prediction/update loop, "
        "measurement uncertainty, smoothing, and recursive inference boundaries."
    ),
    "sources/computer/libqalculate_patterns.md": (
        "Use this pattern note when calculator-engine design needs clean boundaries between parsing, "
        "symbolic structure, precision policy, units, definitions, and thin interface shells."
    ),
    "sources/computer/option_pricing_patterns.md": (
        "Use this pattern note when an option-pricing system needs a stable instrument interface "
        "with explicit analytical, tree-based, and Monte Carlo method selection."
    ),
    "sources/computer/pyportfolioopt_patterns.md": (
        "Use this pattern note when portfolio construction needs separate return estimates, risk "
        "models, objectives, constraints, and allocation translation layers."
    ),
    "sources/computer/quantlib_patterns.md": (
        "Use this pattern note when a later heavyweight pricing framework needs separation between "
        "instruments, pricing engines, market objects, and reusable numerical layers."
    ),
}


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
            if str(doc["path"]).startswith(root)
            and str(doc["path"]) != hub_path
            and str(doc["path"]) not in GENERATED_SOURCE_SHELF_HUBS
        ],
        key=lambda item: str(item["path"]),
    )
    inbound_by_target = inbound_links_by_target(
        [link for link in links if str(link["source_path"]) not in GENERATED_SOURCE_SHELF_HUBS]
    )
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


def build_source_shelf_cleanup_bundle(
    db_path: Path = DEFAULT_DB,
    *,
    shelf: str = "computer",
) -> dict[str, Any]:
    normalized = normalize_shelf(shelf)
    if normalized != "computer":
        raise ValueError("source shelf cleanup bundles currently support only the computer shelf")
    scan_run = latest_scan_run(db_path)
    if scan_run is None:
        raise ValueError(f"no scan run found in {db_path}")
    wiki_root = Path(str(scan_run["root"]))
    report = source_shelf_report(db_path, "computer", limit=1000)
    weak_summary_paths = {str(note["path"]) for note in report["weak_summaries"]}
    targets: list[dict[str, Any]] = []

    cpp_target = stroustrup_placeholder_repair_target(wiki_root)
    if cpp_target is not None:
        targets.append(cpp_target)
    for path, summary in sorted(COMPUTER_SOURCE_SUMMARIES.items()):
        if path not in weak_summary_paths:
            continue
        target = source_summary_insert_target(wiki_root, path, summary)
        if target is not None:
            targets.append(target)
    placeholder_target = delete_markdown_file_target(
        wiki_root,
        "sources/computer/page--1-0.md",
        reason="Remove generated C++ placeholder after replacing chapter links with plain chapter names",
    )
    if placeholder_target is not None:
        targets.append(placeholder_target)
    if not targets:
        raise ValueError("no computer source shelf cleanup targets found")

    return {
        "backup_manifest": {
            "required_before_apply": True,
            "status": "not_created",
        },
        "bundle_id": f"bundle:source-shelves:computer-cleanup:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "rationale": "Clean local computer source shelf summaries and remove the generated C++ placeholder page.",
        "source_catalog": {
            "db_path": str(db_path),
            "root": str(scan_run["root"]),
            "run_id": scan_run.get("run_id"),
            "scanned_at_utc": scan_run.get("scanned_at_utc"),
        },
        "targets": targets,
    }


def build_source_shelf_bridge_bundle(
    db_path: Path = DEFAULT_DB,
    *,
    shelf: str = "math",
) -> dict[str, Any]:
    normalized = normalize_shelf(shelf)
    if normalized != "math":
        raise ValueError("source shelf bridge bundles currently support only the math shelf")
    scan_run = latest_scan_run(db_path)
    if scan_run is None:
        raise ValueError(f"no scan run found in {db_path}")
    wiki_root = Path(str(scan_run["root"]))
    bridge = math_book_concept_bridge_map(db_path)
    outputs = [
        (
            MATH_BRIDGE_MAP_PATH,
            "Math Book-to-Concept Bridge Map",
            render_math_book_concept_bridge_markdown(bridge),
            "Create generated math book-to-concept bridge map from the local catalog",
        ),
        (
            MATH_README_PATH,
            "Math Source Notes",
            render_math_source_readme(bridge),
            "Refresh math source shelf hub from the current maintained source notes",
        ),
    ]
    targets = [
        target
        for target in (
            create_or_replace_markdown_target(
                wiki_root,
                path,
                title=title,
                body=body,
                reason=reason,
            )
            for path, title, body, reason in outputs
        )
        if target is not None
    ]
    if not targets:
        raise ValueError("no math source shelf bridge targets found")

    return {
        "backup_manifest": {
            "required_before_apply": True,
            "status": "not_created",
        },
        "bundle_id": f"bundle:source-shelves:math-bridge-map:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "rationale": "Create local math source shelf bridge maps that route concepts to useful books.",
        "source_catalog": {
            "db_path": str(db_path),
            "root": str(scan_run["root"]),
            "run_id": scan_run.get("run_id"),
            "scanned_at_utc": scan_run.get("scanned_at_utc"),
        },
        "targets": targets,
    }


def math_book_concept_bridge_map(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    report = source_shelf_report(db_path, "math", limit=1000)
    docs, _links, _headings = load_source_shelf_rows(db_path)
    text_by_path = {str(doc["path"]): str(doc["text"]) for doc in docs}
    sources = [
        source_bridge_entry(note, text_by_path.get(str(note["path"]), ""))
        for note in report["notes"]
        if note["source_type"] != "placeholder"
    ]
    concepts_by_path: dict[str, dict[str, Any]] = {}
    lanes_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for source in sources:
        lanes_by_name[str(source["lane"])].append(source)
        seen_concepts: set[str] = set()
        for link in source["concept_links"]:
            concept_path = str(link["path"])
            if concept_path in seen_concepts:
                continue
            seen_concepts.add(concept_path)
            concept = concepts_by_path.setdefault(
                concept_path,
                {
                    "label": str(link["label"]),
                    "path": concept_path,
                    "sources": [],
                },
            )
            concept["sources"].append(source)

    concepts = sorted(
        (
            {
                **concept,
                "source_count": len(concept["sources"]),
                "sources": sorted(
                    concept["sources"],
                    key=lambda item: (-int(item["inbound_count"]), str(item["path"])),
                ),
            }
            for concept in concepts_by_path.values()
        ),
        key=lambda item: (str(item["label"]).lower(), str(item["path"])),
    )
    lanes = [
        {
            "lane": lane,
            "source_count": len(items),
            "sources": sorted(items, key=lambda item: (-int(item["inbound_count"]), str(item["path"]))),
        }
        for lane, items in sorted(lanes_by_name.items())
    ]
    high_use_sources = sorted(sources, key=lambda item: (-int(item["inbound_count"]), str(item["path"])))[:10]

    return {
        "catalog_db": str(db_path),
        "concept_count": len(concepts),
        "concepts": concepts,
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "high_use_sources": high_use_sources,
        "lane_counts": {lane["lane"]: lane["source_count"] for lane in lanes},
        "lanes": lanes,
        "source_note_count": len(sources),
        "shelf": "math",
    }


def source_bridge_entry(note: dict[str, Any], text: str) -> dict[str, Any]:
    concept_links = [
        dict(link)
        for link in note["concept_project_links"]
        if str(link["path"]).startswith("concepts/")
    ]
    return {
        "concept_links": concept_links,
        "document_id": note["document_id"],
        "inbound_count": note["inbound_count"],
        "lane": note["lane"],
        "path": note["path"],
        "source_type": note["source_type"],
        "summary": source_summary_text(text),
        "title": note["title"],
    }


def create_or_replace_markdown_target(
    wiki_root: Path,
    path: str,
    *,
    title: str,
    body: str,
    reason: str,
) -> dict[str, Any] | None:
    source = wiki_root / path
    if not source.exists():
        return {
            "body": body,
            "path": path,
            "reason": reason,
            "title": title,
            "type": "create_markdown_file",
        }
    old_text = source.read_bytes().decode("utf-8", errors="surrogateescape")
    if old_text == body:
        return None
    return {
        "new_text": body,
        "old_text": old_text,
        "path": path,
        "reason": reason,
        "source_path": path,
        "type": "replace_text_block",
    }


def source_summary_insert_target(
    wiki_root: Path,
    path: str,
    summary: str,
) -> dict[str, Any] | None:
    source = wiki_root / path
    if not source.exists():
        raise ValueError(f"source note does not exist: {path}")
    text = source.read_bytes().decode("utf-8", errors="surrogateescape")
    if has_source_summary(text):
        return None
    newline = "\r\n" if "\r\n" in text else "\n"
    marker = f"{newline}## What Problem This Project Is Trying To Solve{newline}"
    if text.count(marker) != 1:
        raise ValueError(f"cannot find insertion point in {path}")
    return {
        "cleanup_kind": "source_summary",
        "new_text": (
            f"{newline}## Why This Source Matters{newline}{newline}"
            f"{summary}{newline}{newline}"
            f"## What Problem This Project Is Trying To Solve{newline}"
        ),
        "old_text": marker,
        "path": path,
        "reason": "Add clear opening source-summary section for the computer shelf",
        "source_path": path,
        "type": "replace_text_block",
    }


def stroustrup_placeholder_repair_target(wiki_root: Path) -> dict[str, Any] | None:
    path = "sources/computer/computer__the_c_programming_language__bjarne_stroustrup.md"
    source = wiki_root / path
    if not source.exists():
        return None
    text = source.read_bytes().decode("utf-8", errors="surrogateescape")
    newline = "\r\n" if "\r\n" in text else "\n"
    old_text = newline.join(
        [
            "- Chapter 1: chapter 1 ptg10564057 1 Notes to the Reader Hurry Slowly; [Notes to the Reader](page--1-0)",
            "- Chapter 2: The Basics; [A Tour of C++: The Basics](page--1-0)",
            "- Chapter 3: Abstraction Mechanisms; [A Tour of C++: Abstraction Mechanisms](page--1-0)",
        ]
    )
    if old_text not in text:
        return None
    return {
        "cleanup_kind": "placeholder_link_repair",
        "new_text": newline.join(
            [
                "- Chapter 1: Notes to the Reader",
                "- Chapter 2: A Tour of C++: The Basics",
                "- Chapter 3: A Tour of C++: Abstraction Mechanisms",
            ]
        ),
        "old_text": old_text,
        "path": path,
        "reason": "Replace generated page links with plain chapter names before deleting the placeholder page",
        "source_path": path,
        "type": "replace_text_block",
    }


def delete_markdown_file_target(
    wiki_root: Path,
    path: str,
    *,
    reason: str,
) -> dict[str, Any] | None:
    source = wiki_root / path
    if not source.exists():
        return None
    return {
        "cleanup_kind": "delete_generated_placeholder",
        "expected_sha256": sha256(source.read_bytes()).hexdigest(),
        "path": path,
        "reason": reason,
        "type": "delete_markdown_file",
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
    if shelf == "math":
        primary = " ".join([path, title]).lower()
        secondary = " ".join(item["path"] for item in links).lower()
        return classify_math_lane(primary, secondary)
    haystack = " ".join([path, title, *[item["path"] for item in links]]).lower()
    return classify_computer_lane(haystack)


def classify_math_lane(primary: str, secondary: str = "") -> str:
    rules = [
        ("heavy_tails", ("heavy", "tail", "resnick")),
        ("quantum_operator_methods", ("quantum", "operator", "beyer")),
        ("geometry_manifolds", ("manifold", "riemannian", "geometry", "petersen", "jost")),
        ("stochastic_processes", ("stochastic", "brownian", "sde", "karatzas", "oksendal")),
        ("probability_measure", ("probability", "measure", "billingsley", "durrett", "kallenberg")),
        ("functional_analysis", ("functional", "sobolev", "banach", "hilbert", "brezis", "conway", "lax")),
        ("differential_equations_applied_math", ("differential", "equation", "applied", "pde", "evans", "strang")),
        ("spectral_numerical_methods", ("spectral", "chebyshev", "fourier")),
        ("numerical_linear_algebra", ("matrix", "linear_algebra", "linear algebra", "golub", "meckes", "numerical")),
        ("algebra", ("basic_algebra", "basic algebra", "jacobson")),
    ]
    primary_lane = first_matching_lane(primary, rules, default="")
    if primary_lane:
        return primary_lane
    return first_matching_lane(secondary, rules, default="math_general")


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


def render_math_book_concept_bridge_markdown(bridge: dict[str, Any]) -> str:
    lines = [
        "# Math Book-to-Concept Bridge Map",
        "",
        f"- source_note_count: `{bridge['source_note_count']}`",
        f"- concept_count: `{bridge['concept_count']}`",
        "",
        "## How To Use This Map",
        "",
        "- Start with a concept route when you need the strongest books for a mathematical idea.",
        "- Use lane routes when you need a shelf section instead of a single concept.",
        "- Regenerate this map after source-note edits so the hub stays aligned with the catalog.",
        "",
        "## Concept Routes",
        "",
    ]
    if not bridge["concepts"]:
        lines.extend(["- none", ""])
    for concept in bridge["concepts"]:
        lines.extend(
            [
                f"### [{concept['label']}]({relative_wiki_link(str(concept['path']))})",
                "",
            ]
        )
        for source in concept["sources"]:
            lines.append(source_route_bullet(source))
        lines.append("")

    lines.extend(["## Lane Routes", ""])
    for lane in bridge["lanes"]:
        lines.extend([f"### `{lane['lane']}`", ""])
        for source in lane["sources"]:
            concept_links = ", ".join(
                f"[{link['label']}]({relative_wiki_link(str(link['path']))})"
                for link in source["concept_links"]
            )
            if not concept_links:
                concept_links = "none"
            lines.append(
                "- [{title}]({path}) - inbound `{inbound}`, concepts: {concepts}. {summary}".format(
                    concepts=concept_links,
                    inbound=source["inbound_count"],
                    path=relative_wiki_link(str(source["path"])),
                    summary=source["summary"],
                    title=source["title"],
                )
            )
        lines.append("")

    lines.extend(["## High-Use Math Sources", ""])
    for source in bridge["high_use_sources"]:
        lines.append(source_route_bullet(source))
    lines.append("")
    return "\n".join(lines)


def render_math_source_readme(bridge: dict[str, Any]) -> str:
    lines = [
        "# Math Source Notes",
        "",
        "This folder tracks the maintained math reference layer for the private wiki.",
        "",
        f"- maintained_source_notes: `{bridge['source_note_count']}`",
        f"- concept_routes: `{bridge['concept_count']}`",
        "",
        "## Navigation",
        "",
        "- [Book-to-Concept Bridge Map](book_to_concept_bridge_map.md)",
        "",
        "## Shelf Lanes",
        "",
        "| lane | sources |",
        "|---|---:|",
    ]
    for lane, count in sorted(bridge["lane_counts"].items()):
        lines.append(f"| `{table_cell(lane)}` | {count} |")

    lines.extend(["", "## Concept Routes", "", "| concept | sources |", "|---|---:|"])
    for concept in bridge["concepts"]:
        lines.append(f"| [{table_cell(concept['label'])}]({relative_wiki_link(str(concept['path']))}) | {concept['source_count']} |")

    lines.extend(["", "## High-Use Math Sources", ""])
    for source in bridge["high_use_sources"][:8]:
        lines.append(
            "- [{title}]({path}) - lane `{lane}`, inbound `{inbound}`".format(
                inbound=source["inbound_count"],
                lane=source["lane"],
                path=relative_wiki_link(str(source["path"])),
                title=source["title"],
            )
        )

    lines.extend(
        [
            "",
            "## Maintenance",
            "",
            "- This hub is generated from `state/catalog.sqlite` and should be refreshed after source-note edits.",
            "- Apply bridge-map bundles to `state/wiki_mirror` first; NAS promotion is a separate reviewed pass.",
            "",
        ]
    )
    return "\n".join(lines)


def source_route_bullet(source: dict[str, Any]) -> str:
    return "- [{title}]({path}) - lane `{lane}`, inbound `{inbound}`. {summary}".format(
        inbound=source["inbound_count"],
        lane=source["lane"],
        path=relative_wiki_link(str(source["path"])),
        summary=source["summary"],
        title=source["title"],
    )


def source_summary_text(text: str) -> str:
    match = re.search(
        r"^## Why This Source Matters\s*\n+(?P<body>.*?)(?:\n## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match:
        summary = clean_markdown_summary(match.group("body"))
        if summary:
            return truncate_words(summary, 36)
    for block in text.split("\n\n"):
        summary = clean_markdown_summary(block)
        if summary and len(summary.split()) >= 8:
            return truncate_words(summary, 36)
    return "No source summary is available yet."


def clean_markdown_summary(markdown: str) -> str:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^- [A-Za-z0-9_ -]+:\s*`", line):
            continue
        line = re.sub(r"^\s*[-*]\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def truncate_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(".,;:") + "..."


def relative_wiki_link(target_path: str) -> str:
    if target_path.startswith("sources/math/"):
        return PurePosixPath(target_path).name
    return f"../../{target_path}"


def table_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


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
