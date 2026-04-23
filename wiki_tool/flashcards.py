from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any

from wiki_tool.catalog import (
    DEFAULT_DB,
    catalog_freshness_inputs,
    collect_documents,
    document_item_from_doc,
    document_item_from_row,
    latest_scan_run,
    limit_items,
    paths_same_file,
)
from wiki_tool.markdown import normalize_name
from wiki_tool.source_shelves import source_shelf_report


DEFAULT_FLASHCARD_DIR = Path("state/flashcards")
DEFAULT_FLASHCARD_EXPORT = "math_flashcards.jsonl"
DEFAULT_FLASHCARD_EXPANDED_EXPORT = "math_flashcards_expanded.jsonl"
DEFAULT_FLASHCARD_REVIEW = "review_queue.md"
DEFAULT_FLASHCARD_SUMMARY = "README.md"

HIGH_CONFIDENCE = "high"
MEDIUM_CONFIDENCE = "medium"
DEFINITION_MIN_WORDS = 12
FLASHCARD_SCOPE_DESCRIPTION = "sources/math + concepts"
IGNORED_FLASHCARD_PATHS = {
    "sources/math/README.md",
    "sources/math/book_to_concept_bridge_map.md",
}
SUBSTANTIVE_SKIP_HEADINGS = {
    "furtherreading",
    "links",
    "navigation",
    "notes",
    "references",
    "relatedconcepts",
    "relatedprojects",
    "seealso",
    "sources",
}
STRICT_PROFILE = "strict"
EXPANDED_PROFILE = "expanded"
BOTH_PROFILES = "both"
FLASHCARD_PROFILES = {STRICT_PROFILE, EXPANDED_PROFILE}
SUMMARY_PROFILES = {STRICT_PROFILE, EXPANDED_PROFILE, BOTH_PROFILES}
DEFAULT_SUMMARY_PROFILE = BOTH_PROFILES
DEFAULT_SHOW_PROFILE = EXPANDED_PROFILE
DEFAULT_WRITE_PROFILE = BOTH_PROFILES
MIN_EXPANDED_CARD_COUNT = 4
MAX_EXPANDED_CARD_COUNT = 6
WHY_THIS_SOURCE_HEADING = "Why This Source Matters"
STRONGEST_CHAPTERS_HEADING = "Strongest Chapters"
EXAMPLE_QUESTIONS_HEADING = "Example Questions"
STRICT_CARD_KIND = "concept"
STUDY_ANCHOR_CARD_KIND = "study_anchor"
MAX_STUDY_ANCHOR_FRONT_LEN = 72
SECTION_LOCATOR_RE = re.compile(
    r"^(?:chapter|chapters|lecture|lectures|early chapters|middle chapters|later chapters|"
    r"early lectures|middle lectures|later lectures)\s*[^:]*:\s*",
    re.IGNORECASE,
)
BAD_CHAPTER_TOPIC_TOKENS = {
    "chapter",
    "chapters",
    "lecture",
    "lectures",
    "preface",
    "references",
    "introduction",
}
BAD_CHAPTER_TOPIC_PHRASES = {
    "hence section",
    "i found it natural",
    "preface to the",
    "detailedstudy",
}
BAD_QUESTION_TOPIC_PREFIXES = ("which source should we use for ", "where should we look when ")
QUESTION_TRIM_TAIL_RE = re.compile(
    r"\s+(?:before|rather than|instead of|when|without|once|after)\b.*$",
    re.IGNORECASE,
)
QUESTION_PATTERNS = (
    re.compile(r"\buse for (?P<topic>.+)$", re.IGNORECASE),
    re.compile(r"\bdepends on (?P<topic>.+)$", re.IGNORECASE),
    re.compile(r"\bneeds (?P<topic>.+)$", re.IGNORECASE),
    re.compile(r"\binto (?P<topic>.+)$", re.IGNORECASE),
    re.compile(r"\bconnect (?P<topic>.+)$", re.IGNORECASE),
    re.compile(r"\bexplain (?P<topic>.+)$", re.IGNORECASE),
    re.compile(r"\bfor (?P<topic>.+)$", re.IGNORECASE),
)
STUDY_ANCHOR_TOPIC_REPLACEMENTS = (
    (re.compile(r"\badvanced modern probability\b", re.IGNORECASE), "modern probability"),
    (re.compile(r"\bbasic Banach-space machinery\b", re.IGNORECASE), "Banach-space basics"),
    (re.compile(r"\boperator-theory structure\b", re.IGNORECASE), "operator theory"),
    (re.compile(r"\bdistribution-style tools\b", re.IGNORECASE), "distributions"),
    (re.compile(r"\bfiniteness-style results\b", re.IGNORECASE), "finiteness results"),
    (re.compile(r"\bglobal Riemannian\b", re.IGNORECASE), "Riemannian"),
    (re.compile(r"\bbroad applied-mathematics framing\b", re.IGNORECASE), "applied mathematics"),
)


def flashcard_summary(
    db_path: Path = DEFAULT_DB,
    *,
    profile: str = DEFAULT_SUMMARY_PROFILE,
) -> dict[str, Any]:
    profile = validated_profile(profile, allow_both=True)
    bundle = math_flashcard_bundle(db_path)
    if profile in FLASHCARD_PROFILES:
        summary = library_summary(bundle[profile])
        summary["profile"] = profile
        return summary
    return {
        "catalog_db": bundle["catalog_db"],
        "flashcard_freshness": bundle["flashcard_freshness"],
        "generated_at_utc": bundle["generated_at_utc"],
        "profile": profile,
        "profiles": {
            name: library_summary(bundle[name])
            for name in (STRICT_PROFILE, EXPANDED_PROFILE)
        },
        "scan_root": bundle["scan_root"],
    }


def flashcard_chain(
    db_path: Path,
    identifier: str,
    *,
    profile: str = DEFAULT_SHOW_PROFILE,
) -> dict[str, Any]:
    profile = validated_profile(profile, allow_both=False)
    bundle = math_flashcard_bundle(db_path)
    book = resolve_book(bundle[profile]["books"], identifier)
    return {
        "book_document_id": book["book_document_id"],
        "book_path": book["book_path"],
        "book_title": book["book_title"],
        "card_count": book["card_count"],
        "cards": book["cards"],
        "confidence_counts": book["confidence_counts"],
        "flashcard_freshness": bundle["flashcard_freshness"],
        "profile": profile,
        "review_count": book["review_count"],
        "review_items": book["review_items"],
    }


def write_flashcard_exports(
    db_path: Path = DEFAULT_DB,
    *,
    output_dir: Path = DEFAULT_FLASHCARD_DIR,
    profile: str = DEFAULT_WRITE_PROFILE,
) -> dict[str, Any]:
    profile = validated_profile(profile, allow_both=True)
    bundle = math_flashcard_bundle(db_path)
    requested_profiles = requested_profile_names(profile)
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    profile_payloads: dict[str, dict[str, Any]] = {}
    for name in requested_profiles:
        library = bundle[name]
        export_name = DEFAULT_FLASHCARD_EXPORT if name == STRICT_PROFILE else DEFAULT_FLASHCARD_EXPANDED_EXPORT
        export_path = output_dir / export_name
        export_path.write_text(
            "".join(json.dumps(card, sort_keys=True) + "\n" for book in library["books"] for card in book["cards"])
        )
        files.append(str(export_path))
        profile_payloads[name] = {
            "book_count": library["book_count"],
            "export_path": str(export_path),
            "exported_card_count": library["exported_card_count"],
            "review_item_count": library["review_item_count"],
        }

    review_path = output_dir / DEFAULT_FLASHCARD_REVIEW
    review_path.write_text(render_review_queue_markdown(bundle, requested_profiles))
    summary_path = output_dir / DEFAULT_FLASHCARD_SUMMARY
    summary_path.write_text(render_flashcard_summary_markdown(bundle, requested_profiles))
    files.extend([str(review_path), str(summary_path)])

    result: dict[str, Any] = {
        "catalog_db": bundle["catalog_db"],
        "file_count": len(files),
        "files": files,
        "flashcard_freshness": bundle["flashcard_freshness"],
        "generated_at_utc": bundle["generated_at_utc"],
        "output_dir": str(output_dir),
        "profile": profile,
        "scan_root": bundle["scan_root"],
    }
    if profile in FLASHCARD_PROFILES:
        result.update(profile_payloads[profile])
    else:
        result["profiles"] = profile_payloads
    return result


def math_flashcard_bundle(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    run = require_fresh_catalog(db_path)
    report = source_shelf_report(db_path, "math", limit=1000)
    books = [note for note in report["notes"] if note["source_type"] == "book"]
    (
        docs_by_path,
        spans_by_path,
        outbound_links_by_source,
        aliases_by_concept,
        math_focused_concept_paths,
    ) = load_flashcard_rows(db_path)
    concepts_by_path = {
        path: doc
        for path, doc in docs_by_path.items()
        if str(doc["kind"]) == "concept"
    }

    strict_chains: list[dict[str, Any]] = []
    expanded_chains: list[dict[str, Any]] = []
    for book in sorted(books, key=lambda item: str(item["path"])):
        strict_chain = build_strict_book_chain(
            book,
            docs_by_path=docs_by_path,
            spans_by_path=spans_by_path,
            outbound_links_by_source=outbound_links_by_source,
            concepts_by_path=concepts_by_path,
            aliases_by_concept=aliases_by_concept,
            math_focused_concept_paths=math_focused_concept_paths,
        )
        strict_chains.append(strict_chain)
        expanded_chains.append(
            build_expanded_book_chain(
                book,
                strict_chain=strict_chain,
                book_doc=docs_by_path[str(book["path"])],
                book_spans=spans_by_path.get(str(book["path"]), []),
            )
        )

    generated_at_utc = datetime.now(UTC).isoformat(timespec="seconds")
    freshness = flashcard_freshness(db_path)
    scan_root = str(run["root"])
    return {
        "catalog_db": str(db_path),
        "flashcard_freshness": freshness,
        "generated_at_utc": generated_at_utc,
        STRICT_PROFILE: finalize_library(
            STRICT_PROFILE,
            strict_chains,
            db_path=db_path,
            freshness=freshness,
            generated_at_utc=generated_at_utc,
            scan_root=scan_root,
        ),
        EXPANDED_PROFILE: finalize_library(
            EXPANDED_PROFILE,
            expanded_chains,
            db_path=db_path,
            freshness=freshness,
            generated_at_utc=generated_at_utc,
            scan_root=scan_root,
        ),
        "scan_root": scan_root,
    }


def library_summary(library: dict[str, Any]) -> dict[str, Any]:
    return {
        "book_count": library["book_count"],
        "books": [
            {
                "book_path": book["book_path"],
                "book_title": book["book_title"],
                "card_count": book["card_count"],
                "confidence_counts": book["confidence_counts"],
                "review_count": book["review_count"],
            }
            for book in library["books"]
        ],
        "catalog_db": library["catalog_db"],
        "confidence_counts": library["confidence_counts"],
        "exported_card_count": library["exported_card_count"],
        "flashcard_freshness": library["flashcard_freshness"],
        "generated_at_utc": library["generated_at_utc"],
        "review_item_count": library["review_item_count"],
        "scan_root": library["scan_root"],
    }


def finalize_library(
    profile: str,
    chains: list[dict[str, Any]],
    *,
    db_path: Path,
    freshness: dict[str, Any],
    generated_at_utc: str,
    scan_root: str,
) -> dict[str, Any]:
    confidence_counts = Counter(
        card["association_confidence"]
        for book in chains
        for card in book["cards"]
    )
    return {
        "book_count": len(chains),
        "books": chains,
        "catalog_db": str(db_path),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "exported_card_count": sum(book["card_count"] for book in chains),
        "flashcard_freshness": freshness,
        "generated_at_utc": generated_at_utc,
        "profile": profile,
        "review_item_count": sum(book["review_count"] for book in chains),
        "scan_root": scan_root,
    }


def require_fresh_catalog(db_path: Path) -> dict[str, Any]:
    run = latest_scan_run(db_path)
    if run is None:
        raise ValueError(f"no scan run found in {db_path}")
    freshness = flashcard_freshness(db_path)
    if freshness["status"] != "pass":
        changed_paths = [
            *[item["path"] for item in freshness["added_documents"]],
            *[item["path"] for item in freshness["modified_documents"]],
            *[item["path"] for item in freshness["removed_documents"]],
        ]
        suffix = ""
        if changed_paths:
            suffix = f" Changed paths: {', '.join(changed_paths[:5])}"
        raise ValueError(
            "flashcard inputs are stale or missing against the scanned root for scope "
            f"`{FLASHCARD_SCOPE_DESCRIPTION}`; run `wiki scan` before generating flashcards.{suffix}"
        )
    return run


def flashcard_freshness(db_path: Path = DEFAULT_DB, *, limit: int = 25) -> dict[str, Any]:
    if limit < 0:
        raise ValueError("limit must be greater than or equal to 0")
    run = latest_scan_run(db_path)
    if run is None:
        return flashcard_stale_result(
            status="fail",
            stale=True,
            reason="missing_scan_run",
            limit=limit,
        )

    checked_root = Path(str(run["root"])).expanduser()
    if not checked_root.exists():
        return flashcard_stale_result(
            status="fail",
            stale=True,
            reason="root_missing",
            run=run,
            checked_root=checked_root,
            limit=limit,
        )
    if not checked_root.is_dir():
        return flashcard_stale_result(
            status="fail",
            stale=True,
            reason="root_not_directory",
            run=run,
            checked_root=checked_root,
            limit=limit,
        )

    checked_root = checked_root.resolve()
    catalog_docs, _catalog_files, _file_inventory_available = catalog_freshness_inputs(db_path)
    current_docs = {doc.path: doc for doc in collect_documents(checked_root)}
    catalog_scoped = {path: row for path, row in catalog_docs.items() if in_flashcard_scope(path)}
    current_scoped = {path: doc for path, doc in current_docs.items() if in_flashcard_scope(path)}

    catalog_paths = set(catalog_scoped)
    current_paths = set(current_scoped)
    added_paths = sorted(current_paths - catalog_paths)
    removed_paths = sorted(catalog_paths - current_paths)
    modified_paths = sorted(
        path
        for path in catalog_paths & current_paths
        if str(catalog_scoped[path]["content_hash"]) != current_scoped[path].content_hash
    )
    stale = bool(added_paths or removed_paths or modified_paths)
    return {
        "added_document_count": len(added_paths),
        "added_documents": limit_items(
            [document_item_from_doc(current_scoped[path]) for path in added_paths],
            limit,
        ),
        "catalog_document_count": len(catalog_paths),
        "catalog_root": str(run["root"]),
        "checked_root": str(checked_root),
        "current_document_count": len(current_paths),
        "limit": limit,
        "modified_document_count": len(modified_paths),
        "modified_documents": limit_items(
            [
                {
                    "new_hash": current_scoped[path].content_hash,
                    "old_hash": str(catalog_scoped[path]["content_hash"]),
                    "path": path,
                    "title": current_scoped[path].title,
                }
                for path in modified_paths
            ],
            limit,
        ),
        "reason": "flashcard_inputs_changed_since_scan" if stale else "flashcard_inputs_match_catalog",
        "removed_document_count": len(removed_paths),
        "removed_documents": limit_items(
            [document_item_from_row(catalog_scoped[path]) for path in removed_paths],
            limit,
        ),
        "root_matches_catalog_root": paths_same_file(checked_root, Path(str(run["root"]))),
        "scan_run_id": run["run_id"],
        "scanned_at_utc": run["scanned_at_utc"],
        "scope": FLASHCARD_SCOPE_DESCRIPTION,
        "stale": stale,
        "status": "fail" if stale else "pass",
    }


def flashcard_stale_result(
    *,
    status: str,
    stale: bool,
    reason: str,
    limit: int,
    run: dict[str, Any] | None = None,
    checked_root: Path | None = None,
) -> dict[str, Any]:
    return {
        "added_document_count": 0,
        "added_documents": [],
        "catalog_document_count": None,
        "catalog_root": str(run["root"]) if run else None,
        "checked_root": str(checked_root) if checked_root else None,
        "current_document_count": None,
        "limit": limit,
        "modified_document_count": 0,
        "modified_documents": [],
        "reason": reason,
        "removed_document_count": 0,
        "removed_documents": [],
        "root_matches_catalog_root": None,
        "scan_run_id": run["run_id"] if run else None,
        "scanned_at_utc": run["scanned_at_utc"] if run else None,
        "scope": FLASHCARD_SCOPE_DESCRIPTION,
        "stale": stale,
        "status": status,
    }


def in_flashcard_scope(path: str) -> bool:
    return path.startswith("concepts/") or (
        path.startswith("sources/math/")
        and path.endswith(".md")
        and path not in IGNORED_FLASHCARD_PATHS
    )


def load_flashcard_rows(
    db_path: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[str]],
    set[str],
]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        docs = con.execute(
            """
            SELECT doc_id, path, title, kind, text
            FROM documents
            WHERE path LIKE 'sources/math/%' OR path LIKE 'concepts/%'
            ORDER BY path
            """
        ).fetchall()
        spans = con.execute(
            """
            SELECT span_id, doc_id, path, heading, level, start_line, end_line, ordinal, text
            FROM spans
            WHERE path LIKE 'sources/math/%' OR path LIKE 'concepts/%'
            ORDER BY path, ordinal
            """
        ).fetchall()
        links = con.execute(
            """
            SELECT source_path, target_path, label, line
            FROM links
            WHERE resolved = 1
              AND target_path IS NOT NULL
              AND (source_path LIKE 'sources/math/%' OR source_path LIKE 'concepts/%')
            ORDER BY source_path, line, target_path
            """
        ).fetchall()
        aliases = con.execute(
            """
            SELECT alias, target_path
            FROM aliases
            WHERE target_path LIKE 'concepts/%'
            ORDER BY target_path, alias
            """
        ).fetchall()
    docs_by_path = {str(row["path"]): dict(row) for row in docs}
    spans_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in spans:
        spans_by_path[str(row["path"])].append(dict(row))
    outbound_links_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in links:
        outbound_links_by_source[str(row["source_path"])].append(dict(row))
    aliases_by_concept: dict[str, list[str]] = defaultdict(list)
    for row in aliases:
        aliases_by_concept[str(row["target_path"])].append(str(row["alias"]))
    return (
        docs_by_path,
        spans_by_path,
        outbound_links_by_source,
        aliases_by_concept,
        math_focused_concepts([dict(row) for row in links]),
    )


def build_strict_book_chain(
    book: dict[str, Any],
    *,
    docs_by_path: dict[str, dict[str, Any]],
    spans_by_path: dict[str, list[dict[str, Any]]],
    outbound_links_by_source: dict[str, list[dict[str, Any]]],
    concepts_by_path: dict[str, dict[str, Any]],
    aliases_by_concept: dict[str, list[str]],
    math_focused_concept_paths: set[str],
) -> dict[str, Any]:
    book_path = str(book["path"])
    book_doc = docs_by_path[book_path]
    book_text = str(book_doc["text"])
    book_spans = spans_by_path.get(book_path, [])
    candidates = association_candidates(
        book,
        book_text=book_text,
        book_spans=book_spans,
        outbound_links=outbound_links_by_source.get(book_path, []),
        concepts_by_path=concepts_by_path,
        aliases_by_concept=aliases_by_concept,
        math_focused_concept_paths=math_focused_concept_paths,
    )

    exported_candidates: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    for candidate in candidates:
        concept_path = str(candidate["concept_path"])
        concept_doc = concepts_by_path[concept_path]
        concept_spans = spans_by_path.get(concept_path, [])
        definition = resolve_definition(
            concept_doc=concept_doc,
            concept_spans=concept_spans,
            source_path=book_path,
            source_text=book_text,
            source_spans=book_spans,
        )
        if definition is None:
            review_items.append(
                {
                    "association_confidence": str(candidate["association_confidence"]),
                    "association_reason": str(candidate["association_reason"]),
                    "book_path": book_path,
                    "book_title": str(book["title"]),
                    "concept_path": concept_path,
                    "concept_title": str(concept_doc["title"]),
                    "profile": STRICT_PROFILE,
                    "reason": "missing_definition",
                }
            )
            continue
        exported_candidates.append({**candidate, **definition})
        if candidate["association_confidence"] == MEDIUM_CONFIDENCE:
            review_items.append(
                {
                    "association_confidence": str(candidate["association_confidence"]),
                    "association_reason": str(candidate["association_reason"]),
                    "book_path": book_path,
                    "book_title": str(book["title"]),
                    "card_id": card_id_for_candidate(book, candidate),
                    "concept_path": concept_path,
                    "concept_title": str(concept_doc["title"]),
                    "profile": STRICT_PROFILE,
                    "reason": "medium_confidence",
                }
            )

    ordered_candidates = order_candidates(
        exported_candidates,
        outbound_links_by_source=outbound_links_by_source,
    )
    cards = build_concept_cards(
        book,
        ordered_candidates=ordered_candidates,
        profile=STRICT_PROFILE,
    )
    confidence_counts = Counter(card["association_confidence"] for card in cards)
    return {
        "book_document_id": str(book.get("document_id") or readable_book_id(book_path)),
        "book_path": book_path,
        "book_title": str(book["title"]),
        "card_count": len(cards),
        "cards": cards,
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "lane": str(book["lane"]),
        "review_count": len(review_items),
        "review_items": sorted(review_items, key=review_sort_key),
    }


def build_expanded_book_chain(
    book: dict[str, Any],
    *,
    strict_chain: dict[str, Any],
    book_doc: dict[str, Any],
    book_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    expanded_cards = [
        {**deepcopy(card), "profile": EXPANDED_PROFILE}
        for card in strict_chain["cards"]
    ]
    used_topics = {
        normalize_name(str(card["concept_title"]))
        for card in expanded_cards
        if normalize_name(str(card["concept_title"]))
    }
    why_sentence = first_sentence_for_source(str(book_doc["text"]))
    next_ordinal = len(expanded_cards) + 1

    for anchor in chapter_study_anchors(
        book,
        book_spans=book_spans,
        source_text=str(book_doc["text"]),
        why_sentence=why_sentence,
    ):
        normalized = normalize_name(str(anchor["concept_title"]))
        if not normalized or normalized in used_topics or len(expanded_cards) >= MAX_EXPANDED_CARD_COUNT:
            continue
        anchor["ordinal"] = next_ordinal
        next_ordinal += 1
        expanded_cards.append(anchor)
        used_topics.add(normalized)

    if len(expanded_cards) < MIN_EXPANDED_CARD_COUNT:
        for anchor in question_study_anchors(
            book,
            book_spans=book_spans,
            source_text=str(book_doc["text"]),
            why_sentence=why_sentence,
        ):
            normalized = normalize_name(str(anchor["concept_title"]))
            if not normalized or normalized in used_topics or len(expanded_cards) >= MIN_EXPANDED_CARD_COUNT:
                continue
            anchor["ordinal"] = next_ordinal
            next_ordinal += 1
            expanded_cards.append(anchor)
            used_topics.add(normalized)

    confidence_counts = Counter(card["association_confidence"] for card in expanded_cards)
    return {
        "book_document_id": str(strict_chain["book_document_id"]),
        "book_path": str(strict_chain["book_path"]),
        "book_title": str(strict_chain["book_title"]),
        "card_count": len(expanded_cards),
        "cards": expanded_cards,
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "lane": str(strict_chain["lane"]),
        "review_count": 0,
        "review_items": [],
    }


def association_candidates(
    book: dict[str, Any],
    *,
    book_text: str,
    book_spans: list[dict[str, Any]],
    outbound_links: list[dict[str, Any]],
    concepts_by_path: dict[str, dict[str, Any]],
    aliases_by_concept: dict[str, list[str]],
    math_focused_concept_paths: set[str],
) -> list[dict[str, Any]]:
    book_path = str(book["path"])
    candidates_by_path: dict[str, dict[str, Any]] = {}

    for link in outbound_links:
        concept_path = str(link["target_path"])
        if concept_path not in concepts_by_path:
            continue
        concept_doc = concepts_by_path[concept_path]
        candidates_by_path[concept_path] = {
            "aliases": sorted(aliases_by_concept.get(concept_path, [])),
            "appearance_line": int(link["line"]),
            "association_confidence": HIGH_CONFIDENCE,
            "association_reason": "explicit_link",
            "association_span_id": None,
            "book_path": book_path,
            "book_title": str(book["title"]),
            "concept_doc_id": str(concept_doc["doc_id"]),
            "concept_path": concept_path,
            "concept_title": str(concept_doc["title"]),
            "lane": str(book["lane"]),
        }

    for concept_path, concept_doc in concepts_by_path.items():
        if concept_path in candidates_by_path:
            continue
        if concept_path not in math_focused_concept_paths:
            continue
        inference = infer_candidate(
            book_text=book_text,
            book_spans=book_spans,
            concept_doc=concept_doc,
            concept_aliases=aliases_by_concept.get(concept_path, []),
        )
        if inference is None:
            continue
        candidates_by_path[concept_path] = {
            "aliases": sorted(aliases_by_concept.get(concept_path, [])),
            "appearance_line": int(inference["appearance_line"]),
            "association_confidence": MEDIUM_CONFIDENCE,
            "association_reason": str(inference["association_reason"]),
            "association_span_id": inference.get("association_span_id"),
            "book_path": book_path,
            "book_title": str(book["title"]),
            "concept_doc_id": str(concept_doc["doc_id"]),
            "concept_path": concept_path,
            "concept_title": str(concept_doc["title"]),
            "lane": str(book["lane"]),
        }

    return sorted(candidates_by_path.values(), key=candidate_sort_key)


def infer_candidate(
    *,
    book_text: str,
    book_spans: list[dict[str, Any]],
    concept_doc: dict[str, Any],
    concept_aliases: list[str],
) -> dict[str, Any] | None:
    title = str(concept_doc["title"])
    normalized_title = normalize_name(title)
    alias_values = unique_strings(
        [
            normalize_name(alias)
            for alias in concept_aliases
            if normalize_name(alias) and normalize_name(alias) != normalized_title
        ]
    )

    heading_title_match = first_heading_match(book_spans, normalized_title)
    if heading_title_match is not None:
        return {
            "appearance_line": heading_title_match["line"],
            "association_reason": "heading_match",
            "association_span_id": heading_title_match["span_id"],
        }

    heading_alias_matches = [
        match
        for alias in alias_values
        if (match := first_heading_match(book_spans, alias)) is not None
    ]
    if heading_alias_matches:
        match = min(heading_alias_matches, key=lambda item: item["line"])
        return {
            "appearance_line": match["line"],
            "association_reason": "alias_match",
            "association_span_id": match["span_id"],
        }

    body_alias_lines = [
        line
        for alias in alias_values
        if (line := first_phrase_line(book_text, alias, normalized=True)) is not None
    ]
    if body_alias_lines:
        return {
            "appearance_line": min(body_alias_lines),
            "association_reason": "alias_match",
            "association_span_id": None,
        }
    return None


def math_focused_concepts(links: list[dict[str, Any]]) -> set[str]:
    focused: set[str] = set()
    for link in links:
        source_path = str(link["source_path"])
        target_path = str(link["target_path"])
        if source_path.startswith("sources/math/") and target_path.startswith("concepts/"):
            focused.add(target_path)
        if source_path.startswith("concepts/") and target_path.startswith("sources/math/"):
            focused.add(source_path)
    return focused


def resolve_definition(
    *,
    concept_doc: dict[str, Any],
    concept_spans: list[dict[str, Any]],
    source_path: str,
    source_text: str,
    source_spans: list[dict[str, Any]],
) -> dict[str, Any] | None:
    intro = concept_intro_span(concept_spans)
    if intro is not None:
        return {
            "back": str(intro["text"]),
            "definition_path": str(concept_doc["path"]),
            "definition_source": "concept_intro",
            "definition_span_id": intro["span_id"],
            "definition_title": str(concept_doc["title"]),
        }

    section = first_substantive_section_span(concept_spans)
    if section is not None:
        return {
            "back": str(section["text"]),
            "definition_path": str(concept_doc["path"]),
            "definition_source": "concept_section",
            "definition_span_id": section["span_id"],
            "definition_title": str(concept_doc["title"]),
        }

    source_summary = source_summary_definition(source_path, source_text, source_spans)
    if source_summary is not None:
        return {
            "back": str(source_summary["text"]),
            "definition_path": str(source_summary["path"]),
            "definition_source": "source_summary",
            "definition_span_id": source_summary.get("span_id"),
            "definition_title": str(concept_doc["title"]),
        }
    return None


def concept_intro_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    for span in spans:
        text = cleaned_span_text(span)
        if len(text.split()) < DEFINITION_MIN_WORDS:
            continue
        if str(span["heading"]) in {"Intro", "Document"}:
            return {"span_id": str(span["span_id"]), "text": text}
        if int(span["level"]) == 1:
            return {"span_id": str(span["span_id"]), "text": text}
    return None


def first_substantive_section_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    for span in spans:
        if int(span["level"]) <= 0:
            continue
        if int(span["level"]) == 1:
            continue
        if normalize_name(str(span["heading"])) in SUBSTANTIVE_SKIP_HEADINGS:
            continue
        text = cleaned_span_text(span)
        if len(text.split()) < DEFINITION_MIN_WORDS:
            continue
        return {"span_id": str(span["span_id"]), "text": text}
    return None


def source_summary_definition(
    source_path: str,
    source_text: str,
    source_spans: list[dict[str, Any]],
) -> dict[str, Any] | None:
    text = section_text(source_text, WHY_THIS_SOURCE_HEADING)
    if text is None:
        return None
    cleaned = clean_markdown_text(text)
    if len(cleaned.split()) < DEFINITION_MIN_WORDS:
        return None
    return {
        "path": source_path,
        "span_id": section_span_id(source_spans, WHY_THIS_SOURCE_HEADING),
        "text": cleaned,
    }


def order_candidates(
    candidates: list[dict[str, Any]],
    *,
    outbound_links_by_source: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_path = {str(candidate["concept_path"]): candidate for candidate in candidates}
    incoming: dict[str, set[str]] = {path: set() for path in by_path}
    outgoing: dict[str, set[str]] = {path: set() for path in by_path}

    for source_path in by_path:
        for link in outbound_links_by_source.get(source_path, []):
            target_path = str(link["target_path"])
            if target_path not in by_path or target_path == source_path:
                continue
            outgoing[target_path].add(source_path)
            incoming[source_path].add(target_path)
    prerequisite_paths = {path: sorted(deps) for path, deps in incoming.items()}

    available = sorted(
        [path for path, deps in incoming.items() if not deps],
        key=lambda path: candidate_sort_key(by_path[path]),
    )
    ordered_paths: list[str] = []
    while available:
        path = available.pop(0)
        ordered_paths.append(path)
        for dependent in sorted(outgoing[path], key=lambda item: candidate_sort_key(by_path[item])):
            incoming[dependent].discard(path)
            if not incoming[dependent] and dependent not in ordered_paths and dependent not in available:
                available.append(dependent)
        available.sort(key=lambda item: candidate_sort_key(by_path[item]))

    remaining = sorted(
        [path for path in by_path if path not in ordered_paths],
        key=lambda path: candidate_sort_key(by_path[path]),
    )
    ordered_paths.extend(remaining)
    for candidate in candidates:
        candidate["prereq_paths"] = list(prerequisite_paths.get(str(candidate["concept_path"]), []))
    return [by_path[path] for path in ordered_paths]


def build_concept_cards(
    book: dict[str, Any],
    *,
    ordered_candidates: list[dict[str, Any]],
    profile: str,
) -> list[dict[str, Any]]:
    concept_to_card_id = {
        str(candidate["concept_path"]): card_id_for_candidate(book, candidate)
        for candidate in ordered_candidates
    }
    cards: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(ordered_candidates, start=1):
        evidence_paths = unique_strings(
            [
                str(candidate["book_path"]),
                str(candidate["concept_path"]),
            ]
        )
        definition_path = str(candidate.get("definition_path") or "")
        if definition_path and definition_path not in {"source_summary", str(candidate["concept_path"])}:
            evidence_paths.append(definition_path)
        evidence_span_ids = unique_strings(
            [
                str(candidate.get("association_span_id") or ""),
                str(candidate.get("definition_span_id") or ""),
            ]
        )
        cards.append(
            {
                "aliases": list(candidate.get("aliases", [])),
                "association_confidence": str(candidate["association_confidence"]),
                "association_reason": str(candidate["association_reason"]),
                "back": str(candidate["back"]),
                "book_document_id": str(book.get("document_id") or readable_book_id(str(book["path"]))),
                "book_path": str(book["path"]),
                "book_title": str(book["title"]),
                "card_id": concept_to_card_id[str(candidate["concept_path"])],
                "card_kind": STRICT_CARD_KIND,
                "concept_path": str(candidate["concept_path"]),
                "concept_title": str(candidate["concept_title"]),
                "definition_source": str(candidate["definition_source"]),
                "evidence_paths": evidence_paths,
                "evidence_span_ids": evidence_span_ids,
                "front": f"Define {candidate['concept_title']}.",
                "lane": str(book["lane"]),
                "ordinal": ordinal,
                "prereq_card_ids": [
                    concept_to_card_id[path]
                    for path in candidate.get("prereq_paths", [])
                    if path in concept_to_card_id
                ],
                "profile": profile,
            }
        )
    return cards


def chapter_study_anchors(
    book: dict[str, Any],
    *,
    book_spans: list[dict[str, Any]],
    source_text: str,
    why_sentence: str | None,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    span_id = section_span_id(book_spans, STRONGEST_CHAPTERS_HEADING)
    for raw in section_bullets(source_text, STRONGEST_CHAPTERS_HEADING):
        topic = clean_chapter_topic(raw)
        if topic is None:
            continue
        anchors.append(
            build_study_anchor_card(
                book,
                topic=topic,
                source_line=clean_markdown_text(raw),
                source_kind="chapter_topic",
                definition_source="source_chapter",
                span_id=span_id,
                why_sentence=why_sentence,
            )
        )
    return anchors


def question_study_anchors(
    book: dict[str, Any],
    *,
    book_spans: list[dict[str, Any]],
    source_text: str,
    why_sentence: str | None,
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    span_id = section_span_id(book_spans, EXAMPLE_QUESTIONS_HEADING)
    for raw in section_bullets(source_text, EXAMPLE_QUESTIONS_HEADING):
        topic = question_topic(raw)
        if topic is None:
            continue
        anchors.append(
            build_study_anchor_card(
                book,
                topic=topic,
                source_line=clean_markdown_text(raw),
                source_kind="question_topic",
                definition_source="source_question",
                span_id=span_id,
                why_sentence=why_sentence,
            )
        )
    return anchors


def build_study_anchor_card(
    book: dict[str, Any],
    *,
    topic: str,
    source_line: str,
    source_kind: str,
    definition_source: str,
    span_id: str | None,
    why_sentence: str | None,
) -> dict[str, Any]:
    topic = normalize_study_anchor_topic(topic)
    book_id = str(book.get("document_id") or readable_book_id(str(book["path"])))
    topic_slug = normalize_name(topic) or readable_book_id(topic)
    back_parts = [
        f"{topic} is a study anchor from {book['title']}.",
        f"Source signal: {source_line}.",
    ]
    if why_sentence:
        back_parts.append(f"Why this source matters: {why_sentence}")
    return {
        "aliases": [],
        "association_confidence": HIGH_CONFIDENCE,
        "association_reason": source_kind,
        "back": " ".join(back_parts).strip(),
        "book_document_id": book_id,
        "book_path": str(book["path"]),
        "book_title": str(book["title"]),
        "card_id": f"flashcard:math:{book_id}:topic:{topic_slug}",
        "card_kind": STUDY_ANCHOR_CARD_KIND,
        "concept_path": None,
        "concept_title": topic,
        "definition_source": definition_source,
        "evidence_paths": [str(book["path"])],
        "evidence_span_ids": [span_id] if span_id else [],
        "front": f"Define {topic}.",
        "lane": str(book["lane"]),
        "ordinal": 0,
        "prereq_card_ids": [],
        "profile": EXPANDED_PROFILE,
    }


def section_text(source_text: str, heading: str) -> str | None:
    match = re.search(
        rf"^## {re.escape(heading)}\s*\n+(?P<body>.*?)(?:\n## |\Z)",
        source_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None
    return match.group("body")


def section_bullets(source_text: str, heading: str) -> list[str]:
    body = section_text(source_text, heading)
    if body is None:
        return []
    return [match.group(1).strip() for match in re.finditer(r"^- (.+)$", body, flags=re.MULTILINE)]


def section_span_id(spans: list[dict[str, Any]], heading: str) -> str | None:
    normalized = normalize_name(heading)
    for span in spans:
        if normalize_name(str(span["heading"])) == normalized:
            return str(span["span_id"])
    return None


def first_sentence_for_source(source_text: str) -> str | None:
    body = section_text(source_text, WHY_THIS_SOURCE_HEADING)
    if body is None:
        return None
    cleaned = clean_markdown_text(body)
    if not cleaned:
        return None
    match = re.match(r"(.+?[.!?])(?:\s|$)", cleaned)
    if match is not None:
        return match.group(1).strip()
    return cleaned


def clean_chapter_topic(raw: str) -> str | None:
    text = clean_markdown_text(raw)
    if not text:
        return None
    locator_stripped = SECTION_LOCATOR_RE.sub("", text).strip()
    if not locator_stripped:
        return None
    raw_fragments = [strip_fragment_noise(part) for part in locator_stripped.split(";")]
    fragments = unique_strings(raw_fragments)
    best_fragment = best_topic_fragment(fragments)
    if best_fragment is not None:
        return normalize_study_anchor_topic(best_fragment)
    if len(raw_fragments) > 1:
        if len(fragments) == 1 and fragments[0]:
            return normalize_study_anchor_topic(fragments[0])
        return None
    cleaned = strip_fragment_noise(locator_stripped)
    deduped_parts = unique_strings([strip_fragment_noise(part) for part in cleaned.split(";") if strip_fragment_noise(part)])
    if len(deduped_parts) == 1 and deduped_parts[0]:
        return normalize_study_anchor_topic(deduped_parts[0])
    if not looks_like_valid_topic(cleaned):
        return None
    return normalize_study_anchor_topic(cleaned)


def best_topic_fragment(fragments: list[str]) -> str | None:
    ranked: list[tuple[int, int, str]] = []
    for index, fragment in enumerate(fragments):
        if not looks_like_valid_topic(fragment):
            continue
        ranked.append((topic_fragment_score(fragment), index, fragment))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][2]


def strip_fragment_noise(fragment: str) -> str:
    cleaned = normalize_topic_markup(fragment)
    cleaned = re.sub(r"(?i)\bchapter\b", "", cleaned)
    cleaned = re.sub(r"(?i)\bsection\b", "", cleaned)
    cleaned = cleaned.strip(" .;:-")
    cleaned = re.sub(r"^(?:chapter|chapters|lecture|lectures)\s+\d+(?:[-–]\d+)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;:-")
    if cleaned.isupper() and len(cleaned.split()) >= 2:
        cleaned = cleaned.title()
    return cleaned


def looks_like_valid_topic(text: str) -> bool:
    if not text:
        return False
    lowered_text = text.lower()
    if any(phrase in lowered_text for phrase in BAD_CHAPTER_TOPIC_PHRASES):
        return False
    if lowered_text.startswith(("is ", "is a ", "is an ")):
        return False
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text)
    if len(words) < 2 or len(words) > 12:
        return False
    if len(words) == 2 and text == text.lower():
        return False
    lowered = {word.lower() for word in words}
    if lowered & BAD_CHAPTER_TOPIC_TOKENS and len(lowered - BAD_CHAPTER_TOPIC_TOKENS) < 2:
        return False
    if text.isupper():
        return False
    if re.fullmatch(r"[0-9 .-]+", text):
        return False
    if re.search(r"\b(?:section|edition)\b", lowered_text) and len(words) <= 5:
        return False
    content_words = [word for word in words if word.lower() not in BAD_CHAPTER_TOPIC_TOKENS]
    return len(content_words) >= 2


def topic_fragment_score(text: str) -> int:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text)
    lowercase_bonus = sum(1 for word in words if not word.isupper())
    signal_bonus = sum(
        2
        for token in ("probability", "measure", "operator", "sobolev", "brownian", "geometry", "manifolds", "algebra", "linear", "spectral", "curvature", "hilbert", "banach", "eigenvalue", "least-squares", "factorization", "stochastic")
        if token in text.lower()
    )
    return lowercase_bonus + signal_bonus


def question_topic(raw: str) -> str | None:
    text = clean_markdown_text(raw).rstrip("?")
    if not text:
        return None
    lowered = text.lower()
    for prefix in BAD_QUESTION_TOPIC_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            lowered = text.lower()
            break
    if lowered.startswith("how should we "):
        text = text[len("How should we "):]
    if lowered.startswith("where should we look when a "):
        text = text[len("Where should we look when a "):]
    if lowered.startswith("where should we look when "):
        text = text[len("Where should we look when "):]

    for pattern in QUESTION_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        topic = QUESTION_TRIM_TAIL_RE.sub("", match.group("topic")).strip(" .,:;")
        topic = strip_question_noise(topic)
        if topic:
            return topic
    fallback = strip_question_noise(QUESTION_TRIM_TAIL_RE.sub("", text).strip(" .,:;"))
    return fallback or None


def strip_question_noise(text: str) -> str:
    cleaned = re.sub(r"^(?:a |an |the )", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+in (?:a|an|the)\s+[^.]*?(?:explanation|framing|note|argument)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:in the current shelf|in the live shelf|in the shelf|in the project|in later notes)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:language|arguments|tools|viewpoints|intuition|foundations)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;")
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", cleaned)
    if len(words) < 2 or len(words) > 12:
        return ""
    return normalize_study_anchor_topic(cleaned)


def normalize_topic_markup(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\$\\sigma\$", "sigma", cleaned)
    cleaned = re.sub(r"\$([^$]+)\$", r"\1", cleaned)
    cleaned = cleaned.replace("\\sigma", "sigma")
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def normalize_study_anchor_topic(topic: str) -> str:
    cleaned = normalize_topic_markup(topic)
    for pattern, replacement in STUDY_ANCHOR_TOPIC_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;")
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    if len(f"Define {cleaned}.") <= MAX_STUDY_ANCHOR_FRONT_LEN:
        return cleaned
    shorter = re.sub(r"\b(?:basic|advanced|global|broad|project-facing)\b\s*", "", cleaned, flags=re.IGNORECASE)
    shorter = re.sub(r"\s+", " ", shorter).strip(" .,:;")
    if len(f"Define {shorter}.") <= MAX_STUDY_ANCHOR_FRONT_LEN:
        return shorter
    shorter = shorter.replace(" and ", ", ", 1)
    shorter = re.sub(r"\s+", " ", shorter).strip(" .,:;")
    return shorter


def resolve_book(books: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    cleaned = identifier.strip()
    normalized = normalize_name(cleaned)
    exact = [book for book in books if str(book["book_path"]) == cleaned]
    if len(exact) == 1:
        return exact[0]
    candidates = [
        book
        for book in books
        if cleaned == str(book["book_document_id"])
        or normalized == normalize_name(str(book["book_document_id"]))
        or normalized == normalize_name(str(book["book_title"]))
        or normalized == normalize_name(PurePosixPath(str(book["book_path"])).stem)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"no math source book matched {identifier!r}")
    matched = ", ".join(sorted(str(book["book_path"]) for book in candidates))
    raise ValueError(f"book identifier {identifier!r} matched multiple books: {matched}")


def card_id_for_candidate(book: dict[str, Any], candidate: dict[str, Any]) -> str:
    book_id = str(book.get("document_id") or readable_book_id(str(book["path"])))
    concept_id = str(
        readable_book_id(str(candidate.get("concept_path") or candidate.get("path") or candidate.get("title") or ""))
        or candidate.get("concept_doc_id")
    )
    return f"flashcard:math:{book_id}:{concept_id}"


def readable_book_id(path: str) -> str:
    return normalize_name(PurePosixPath(path).stem) or PurePosixPath(path).stem


def first_heading_match(spans: list[dict[str, Any]], normalized_phrase: str) -> dict[str, Any] | None:
    for span in spans:
        if int(span["level"]) <= 0:
            continue
        if normalize_name(str(span["heading"])) == normalized_phrase:
            return {"line": int(span["start_line"]), "span_id": str(span["span_id"])}
    return None


def first_phrase_line(text: str, phrase: str, *, normalized: bool = False) -> int | None:
    if normalized:
        phrase_tokens = re.findall(r"[A-Za-z0-9]+", phrase)
    else:
        phrase_tokens = re.findall(r"[A-Za-z0-9]+", phrase)
    if not phrase_tokens:
        return None
    pattern = re.compile(r"(?<!\w)" + r"[\W_]+".join(re.escape(token) for token in phrase_tokens) + r"(?!\w)", re.IGNORECASE)
    for line_no, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return line_no
    return None


def cleaned_span_text(span: dict[str, Any]) -> str:
    lines = str(span["text"]).splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
    return clean_markdown_text("\n".join(lines))


def clean_markdown_text(markdown: str) -> str:
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


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, str]:
    confidence_rank = {HIGH_CONFIDENCE: 0, MEDIUM_CONFIDENCE: 1}
    return (
        int(candidate["appearance_line"]),
        confidence_rank.get(str(candidate["association_confidence"]), 9),
        str(candidate["concept_title"]).lower(),
    )


def review_sort_key(item: dict[str, Any]) -> tuple[str, int, str, str]:
    reason_rank = {"missing_definition": 0, "medium_confidence": 1}
    return (
        str(item.get("profile") or ""),
        reason_rank.get(str(item["reason"]), 9),
        str(item["book_path"]),
        str(item["concept_title"]).lower(),
    )


def unique_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def render_flashcard_summary_markdown(bundle: dict[str, Any], profiles: list[str]) -> str:
    lines = [
        "# Math Flashcards",
        "",
        f"- generated_at_utc: `{bundle['generated_at_utc']}`",
        f"- catalog_db: `{bundle['catalog_db']}`",
        f"- scan_root: `{bundle['scan_root']}`",
        "",
    ]
    for profile in profiles:
        library = bundle[profile]
        lines.extend(
            [
                f"## {profile.title()} Profile",
                "",
                f"- book_count: `{library['book_count']}`",
                f"- exported_card_count: `{library['exported_card_count']}`",
                f"- review_item_count: `{library['review_item_count']}`",
                "",
                "### Confidence Counts",
                "",
            ]
        )
        confidence_counts = library["confidence_counts"] or {}
        if not confidence_counts:
            lines.append("- none")
        else:
            for confidence, count in confidence_counts.items():
                lines.append(f"- `{confidence}`: `{count}`")
        lines.extend(["", "### Books", ""])
        for book in library["books"]:
            lines.append(
                "- `{path}` cards `{cards}` review `{review}` confidence `{confidence}`".format(
                    cards=book["card_count"],
                    confidence=json.dumps(book["confidence_counts"], sort_keys=True),
                    path=book["book_path"],
                    review=book["review_count"],
                )
            )
        lines.append("")
    return "\n".join(lines)


def render_review_queue_markdown(bundle: dict[str, Any], profiles: list[str]) -> str:
    lines = [
        "# Math Flashcard Review Queue",
        "",
        f"- generated_at_utc: `{bundle['generated_at_utc']}`",
        "",
    ]
    has_items = False
    for profile in profiles:
        library = bundle[profile]
        review_items = [item for book in library["books"] for item in book["review_items"]]
        lines.extend([f"## {profile.title()} Profile", ""])
        if not review_items:
            lines.extend(["- none", ""])
            continue
        has_items = True
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in review_items:
            grouped[str(item["book_path"])].append(item)
        for book_path, items in sorted(grouped.items()):
            lines.extend([f"### `{book_path}`", ""])
            for item in items:
                lines.append(
                    "- `{reason}` concept `{concept}` confidence `{confidence}` via `{association}`".format(
                        association=item["association_reason"],
                        concept=item["concept_title"],
                        confidence=item["association_confidence"],
                        reason=item["reason"],
                    )
                )
            lines.append("")
    if not has_items and profiles:
        return "\n".join(lines)
    return "\n".join(lines)


def validated_profile(profile: str, *, allow_both: bool) -> str:
    normalized = profile.strip().lower()
    allowed = SUMMARY_PROFILES if allow_both else FLASHCARD_PROFILES
    if normalized not in allowed:
        allowed_list = ", ".join(sorted(allowed))
        raise ValueError(f"unknown flashcard profile {profile!r}; expected one of: {allowed_list}")
    return normalized


def requested_profile_names(profile: str) -> list[str]:
    if profile == BOTH_PROFILES:
        return [STRICT_PROFILE, EXPANDED_PROFILE]
    return [profile]
