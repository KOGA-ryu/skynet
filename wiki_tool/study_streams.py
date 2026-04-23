from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any

from wiki_tool.catalog import DEFAULT_DB
from wiki_tool.flashcards import math_flashcard_bundle
from wiki_tool.markdown import normalize_name
from wiki_tool.source_shelves import source_shelf_report


DEFAULT_STUDY_DIR = Path("state/study_materials")
DEFAULT_STUDY_SOURCE_ROOT = Path("state/local_corpus/ml-letsgo/outputs/math")
DEFAULT_STUDY_SHELF = "math"
DEFAULT_STUDY_VIEW = "reader"
DEFAULT_STUDY_EXPORT_TARGET = "canonical"
DEFAULT_STUDY_SELECTION = "all_structured"
DEFAULT_STUDY_DISCOFLASH_EXPORT = "discoflash_definition_matching.txt"
DEFAULT_READER_STREAM = "reader_stream.jsonl"
DEFAULT_READER_PLAIN = "reader_plain.txt"
DEFAULT_DEFINITION_CARDS = "definition_cards.jsonl"
DEFAULT_BOOK_MANIFEST = "manifest.json"
DEFAULT_INDEX = "index.json"
THIN_DECK_MIN_CARDS = 10

READER_VIEW = "reader"
CARDS_VIEW = "cards"
CANONICAL_TARGET = "canonical"
DISCOFLASH_TARGET = "discoflash"
ALL_STRUCTURED_SELECTION = "all_structured"
MAINTAINED_ONLY_SELECTION = "maintained_only"
STUDY_VIEWS = {READER_VIEW, CARDS_VIEW}
STUDY_TARGETS = {CANONICAL_TARGET, DISCOFLASH_TARGET}
STUDY_SELECTIONS = {ALL_STRUCTURED_SELECTION, MAINTAINED_ONLY_SELECTION}
MATERIALIZED_STATUSES = {"built", "partial"}

STRICT_KINDS = {"definition", "theorem"}
DISPLAY_TARGET_CHARS = 420
DISPLAY_MAX_CHARS = 560
STRICT_TARGET_CHARS = 900

TITLE_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("definition", r"^definition\b"),
    ("theorem", r"^theorem\b"),
    ("theorem", r"^lemma\b"),
    ("theorem", r"^proposition\b"),
    ("theorem", r"^corollary\b"),
    ("proof", r"^proof\b"),
    ("example", r"^example\b"),
    ("remark", r"^remark\b"),
    ("exercise", r"^exercise\b"),
)
ENTITY_NUMBER_PATTERN = r"(?:[A-Za-z]\.)?\d+(?:\.\d+)*"
TITLE_PREFIX_RE = re.compile(
    r"^(?:definition|theorem|lemma|proposition|corollary|proof|example|remark|exercise)\s*"
    rf"(?:{ENTITY_NUMBER_PATTERN})?\s*[:.\-]?\s*",
    re.IGNORECASE,
)
CARD_HEADING_RE = re.compile(
    r"^(?P<label>definition|notation|theorem|lemma|proposition|corollary)\s*"
    rf"(?P<number>{ENTITY_NUMBER_PATTERN})?\s*[:.\-]?\s*(?P<tail>.*)$",
    re.IGNORECASE,
)
SOURCE_ENTITY_RE = re.compile(
    r"^\**\s*(?P<label>definition|notation|theorem|lemma|proposition|corollary)\s*"
    rf"(?P<number>{ENTITY_NUMBER_PATTERN})\s*[:.\-]?\**\s*(?P<tail>.*)$",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_INLINE_RE = re.compile(r"[*`]+")
WHITESPACE_RE = re.compile(r"[ \t]+")
YEAR_CITATION_RE = re.compile(r"\((?:[^()]*\d{4}[^()]*)\)")
FORMULA_LINE_RE = re.compile(r"(=|\\|∑|∫|∀|∃|→|↦|≤|≥)")
TERM_SENTENCE_RE = re.compile(
    r"^(?:a|an|the)?\s*(?P<term>[A-Za-z0-9][A-Za-z0-9()'`\- ,/]{1,96}?)\s+"
    r"(?:is|are|denotes|means)\b",
    re.IGNORECASE,
)
ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
INLINE_DEFINE_TO_BE_RE = re.compile(
    r"\bwe define\s+(?:an?|the)?\s*(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)\s+to\s+be\b",
    re.IGNORECASE,
)
INLINE_WE_SAY_IS_RE = re.compile(
    r"\bwe say that\s+(?:the|a|an)?\s*.+?\s+is\s+(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)"
    r"(?=(?:\s+(?:if|when|whenever|on|with|for|relative to|with respect to)\b|[.,;:]|$))",
    re.IGNORECASE,
)
INLINE_IS_CALLED_RE = re.compile(
    r"\b(?:is|are)\s+called\s+(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)"
    r"(?=(?:\s+(?:if|when|whenever|on|with|for)\b|[.,;:]|$))",
    re.IGNORECASE,
)
INLINE_DENOTES_RE = re.compile(
    r"^(?:a|an|the)?\s*(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)\s+denotes\b",
    re.IGNORECASE,
)
THIN_RESCUE_DEFINE_RE = re.compile(
    r"\bdefine\s+(?:the\s+following\s+|the\s+|an?\s+)?(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)"
    r"(?=(?:\s+(?:on|by|as follows)\b|\s*:|[.,;:]|$))",
    re.IGNORECASE,
)
THIN_RESCUE_DENOTE_RE = re.compile(
    r"\blet\s+[A-Za-z][A-Za-z0-9_]*\s+denote\s+(?:the\s+)?(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)"
    r"(?=(?:[.,;:]|$))",
    re.IGNORECASE,
)
THIN_RESCUE_THERE_IS_RE = re.compile(
    r"\bthere\s+(?:is|exists)\s+(?:an?\s+|the\s+)(?P<term>[A-Za-z][A-Za-z0-9()'`\- /]{1,96}?)"
    r"(?=(?:\s+[A-Za-z][A-Za-z0-9_]*\s*[:=]|[.,;:]|$))",
    re.IGNORECASE,
)
THIN_RESCUE_EXACT_TERM_RE = re.compile(
    r"\b(?P<term>(?:Borel|Cantor|Lebesgue(?:-measurable)?|non[- ](?:measurable|Borel))\s+sets?|"
    r"choice function|equivalence relations?|equivalence classes?|Cantor function)\b",
    re.IGNORECASE,
)
THIN_RESCUE_NOUN_PHRASE_RE = re.compile(
    r"\b(?P<term>(?:Borel|Cantor|Lebesgue(?:-measurable)?|non[- ](?:measurable|Borel)|"
    r"[A-Za-z]+(?:[- ][A-Za-z]+){0,2})\s+"
    r"(?:set|sets|function|functions|measure|measures|distribution|distributions|"
    r"relation|relations|class|classes|field|probability|space))\b",
    re.IGNORECASE,
)
GENERIC_RESCUE_TERM_RE = re.compile(
    r"^(set|sets|function|functions|measure|measures|distribution|distributions|relation|relations|class|classes|field|probability|space)$",
    re.IGNORECASE,
)
WEAK_INLINE_PREFIX_RE = re.compile(
    r"^(it|which|that|there|here|where|when|then|thus|hence|project|re|one|some|such|"
    r"main objective|above discussion|conditions? above|situation|situation here|"
    r"if\b|where [A-Za-z]|set consisting of|set [A-Z0-9]\b|class [A-Z0-9]\b|"
    r"function [A-Z0-9]\b|sequence [A-Z0-9]\b)\b",
    re.IGNORECASE,
)
VERBISH_HEADING_TAIL_RE = re.compile(
    r"^(is|are|was|were|shows?|gives?|implies?|states?|yields?|establishes?|proves?|provides?|concerns?)\b",
    re.IGNORECASE,
)
GENERIC_ENTITY_NAME_RE = re.compile(
    r"^(main (?:result|theorem|lemma|proposition|corollary)|result|preliminary results?)$",
    re.IGNORECASE,
)
DISCOURSE_TERM_PREFIX_RE = re.compile(
    r"^(if|then|but|so|and|or|for|suppose|let|there|thus|hence|when|whenever|while|since|because)\b",
    re.IGNORECASE,
)
CHAPTER_ID_NUMBER_RE = re.compile(r"(\d+)")
STRUCTURAL_TERM_RE = re.compile(
    r"\b(proof|proofs|example|examples|remark|remarks|exercise|exercises|problem|problems|section|sections|chapter|chapters|appendix|appendices)\b",
    re.IGNORECASE,
)
STRUCTURAL_TERM_PREFIX_RE = re.compile(
    r"^(preface|appendix|appendices|proof|proofs|example|examples|remark|remarks|exercise|exercises|problem|problems|section|sections|chapter|chapters)\b",
    re.IGNORECASE,
)
RAW_BAD_TITLE_RE = re.compile(r"(_|\b\dEd\b|\bVol \d\b)", re.IGNORECASE)
JUNK_TITLE_RE = re.compile(
    r"^(?:table of contents|contents|index|subject index|name index)\b",
    re.IGNORECASE,
)
JUNK_TEXT_RE = re.compile(
    r"(z-library|singlelogin|downloaded from|generated by unregistered|table of contents)",
    re.IGNORECASE,
)
BACK_MATTER_TITLE_RE = re.compile(
    r"(?:references?\s+and\s+name\s+index|subject\s+index|name\s+index|author\s+index|notation|"
    r"notation\s+index|index of notation|list of frequently used notation and symbols|"
    r"frequently used notation and symbols|bibliography|references?|pure and applied mathematics)$",
    re.IGNORECASE,
)
BACK_MATTER_TEXT_RE = re.compile(
    r"(numbers in square brackets following each entry give the pages of this book|pure and applied mathematics|subject index|"
    r"selected published titles in this series|author index|index of notation)",
    re.IGNORECASE,
)
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
IMAGE_ONLY_RE = re.compile(r"^(?:!\[[^\]]*\]\([^)]+\)|<img\b[^>]*>|image\s+\d+[:.]?)$", re.IGNORECASE)
TRAILING_CITATION_RE = re.compile(r"\s*\[[0-9]+\]\s*$")
LEADING_NUMBERING_RE = re.compile(rf"^(?:{ENTITY_NUMBER_PATTERN})\s*[:.\-]?\s*")
TRAILING_CONNECTOR_RE = re.compile(r"\b(?:or|and|of|to|for)\b\s*$", re.IGNORECASE)
RESULT_STATEMENT_RE = re.compile(r"\b(?:is|are|was|were|implies?|shows?|states?|yields?|gives?)\b", re.IGNORECASE)
FORMULAISH_TEXT_RE = re.compile(r"[$\\=^_{}]|(?:\b[A-Za-z]_[A-Za-z0-9])")
UNMATCHED_BRACKET_TEXT_RE = re.compile(r"[\(\[][^)\]]*$")
VARIABLE_TAIL_RE = re.compile(r"\bof\s+[a-z]\b$", re.IGNORECASE)
SECTION_MARKER_RE = re.compile(r"^(?:§+\s*\d+[A-Za-z.]*(?:\.\d+)*\s*[:.\-]?\s*|[■▪◆]+\s*)", re.IGNORECASE)
SECTION_STYLE_RE = re.compile(
    r"^(?:§+\s*\d+|case\s+\d+|algorithm(?:\s+\d+(?:\.\d+)*)?|consider\b|step\s+\d+|part\s+[a-z]\b)",
    re.IGNORECASE,
)
PROMPT_TAIL_RE = re.compile(r"\)\.?\s*(Suppose|Assume|Let|Define)\b.*$", re.IGNORECASE)
THEOREM_REF_ONLY_RE = re.compile(
    rf"^(?:\[[0-9]+\],?\s*)?(?:theorem|lemma|proposition|corollary)\s+{ENTITY_NUMBER_PATTERN}\b.*$",
    re.IGNORECASE,
)
AUTHOR_YEAR_ONLY_RE = re.compile(
    r"^[A-Z][A-Za-z.'\-]+(?:\s+(?:and\s+)?[A-Z][A-Za-z.'\-]+)*(?:,\s*\d{4}(?:\s*\[[0-9]+\])?)"
    r"(?:\s+and\s+[A-Z][A-Za-z.'\-]+(?:,\s*\d{4}(?:\s*\[[0-9]+\])?)?)*$"
)
YEAR_ONLY_TAIL_RE = re.compile(r"(?:,\s*\d{4}(?:\s*\[[0-9]+\])?|\s*\[[0-9]+\])(?:\s+and\s+.*)?$", re.IGNORECASE)
READER_INDEX_LINE_RE = re.compile(
    r"^[A-Za-zΑ-Ωα-ω0-9][A-Za-zΑ-Ωα-ω0-9'\"`().,;:/\\_{}^*+\-=\s]{0,180}\b\d{1,4}(?:[-–]\d{1,4})?(?:,\s*\d{1,4}(?:[-–]\d{1,4})?){1,}\s*$"
)
PAGE_NOISE_RE = re.compile(r"^\|+|\|+$")
TITLE_SENTENCE_RE = re.compile(r"\b(?:since|suppose|assume|let|then|we have|there exists)\b", re.IGNORECASE)
PURE_DIGITS_RE = re.compile(r"^\d{1,4}$")
SINGLE_LETTER_RE = re.compile(r"^[A-Za-z]$")
CONTENTS_HINT_RE = re.compile(r"\b(preface|chapter|appendix|contents?)\b", re.IGNORECASE)
PAGE_NUMBER_CLUSTER_RE = re.compile(r"\b\d{1,4}(?:\.\d+)*(?:[-–]\d{1,4})?\b")
PROMOTED_HEADING_NUMBER_RE = re.compile(
    r"^(?:(?:chapter|section|lecture)\s+\d+(?:\.\d+)*\s*[:.\-]?\s*|"
    r"(?:[A-Za-z]\.\s+|\d+(?:\.\d+)*(?:\s+|[:.\-]\s*)))",
    re.IGNORECASE,
)
PROMOTED_HEADING_REJECT_RE = re.compile(
    r"^(?:summary|introduction|preliminaries|preliminary results?|examples?|remarks?|proofs?|"
    r"exercises?|problems?|problem set|programs?|appendix|chapter\s+\d+|lecture\s+\d+|section\s+\d+)\b",
    re.IGNORECASE,
)
CODEISH_HEADING_RE = re.compile(
    r"(%|\\|=|subplot|markersize|num2str|axis\(|clf|toeplitz|cot\(|plot\()",
    re.IGNORECASE,
)
PROMOTED_DISCOURSE_PREFIX_RE = re.compile(
    r"^(?:has|have|having|allows?|using|uses?|assum(?:e|ing)|shows?|gives?|states?)\b",
    re.IGNORECASE,
)
PROMOTED_RESULT_KEYWORD_RE = re.compile(
    r"\b(theorem|lemma|proposition|corollary|criterion|inequality|identity|formula|"
    r"decomposition|factorization|representation)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractChapter:
    chapter_id: str
    chapter_title: str
    chapter_number: int | None
    chapter_count: int | None
    source_pdf: str
    book_manifest_path: str
    chapter_json_path: str
    chapter_md_path: str
    chapter_manifest_path: str | None
    chapter_json: dict[str, Any]
    chapter_markdown: str
    chapter_manifest: dict[str, Any]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractBook:
    document_id: str
    book_title: str
    root_path: str
    book_manifest_path: str
    book_manifest: dict[str, Any]
    valid_chapters: tuple[ExtractChapter, ...]
    skipped_chapters: tuple[dict[str, Any], ...]


def probe_study_source_roots(
    db_path: Path = DEFAULT_DB,
    *,
    paths: list[Path],
    shelf: str = DEFAULT_STUDY_SHELF,
    book: str | None = None,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one candidate path is required")
    normalized_shelf = validate_shelf(shelf)
    maintained = select_maintained_books(
        maintained_shelf_books(db_path, normalized_shelf),
        identifier=book,
    )
    probes = [
        probe_source_root_candidate(
            candidate.resolve(),
            maintained_books=maintained,
            shelf=normalized_shelf,
        )
        for candidate in paths
    ]
    probes.sort(key=probe_sort_key)
    return {
        "catalog_db": str(db_path),
        "candidate_count": len(probes),
        "candidates": probes,
        "generated_at_utc": utc_now(),
        "maintained_book_count": len(maintained),
        "shelf": normalized_shelf,
    }


def study_inventory(
    db_path: Path = DEFAULT_DB,
    *,
    source_root: Path = DEFAULT_STUDY_SOURCE_ROOT,
    shelf: str = DEFAULT_STUDY_SHELF,
    selection: str = DEFAULT_STUDY_SELECTION,
    book: str | None = None,
) -> dict[str, Any]:
    normalized_shelf = validate_shelf(shelf)
    normalized_selection = validate_selection(selection)
    source_root = source_root.resolve()
    inventory_books = inventory_books_for_selection(
        db_path,
        source_root=source_root,
        shelf=normalized_shelf,
        selection=normalized_selection,
        identifier=book,
    )

    ready_count = sum(1 for book in inventory_books if book["status"] in {"ready", "partial"})
    missing_count = sum(1 for book in inventory_books if book["status"] == "missing_extract")
    invalid_count = sum(1 for book in inventory_books if book["status"] == "no_valid_chapters")
    partial_books = [dict(book) for book in inventory_books if book["status"] == "partial"]
    missing_books = [dict(book) for book in inventory_books if book["status"] == "missing_extract"]
    no_valid_books = [dict(book) for book in inventory_books if book["status"] == "no_valid_chapters"]
    unmatched_extract_roots = discover_unmatched_extract_roots(source_root, inventory_books)
    return {
        "catalog_db": str(db_path),
        "generated_at_utc": utc_now(),
        "ready_count": ready_count,
        "missing_extract_count": missing_count,
        "missing_books": missing_books,
        "no_valid_chapters_count": invalid_count,
        "no_valid_books": no_valid_books,
        "book_count": len(inventory_books),
        "books": inventory_books,
        "partial_books": partial_books,
        "partial_count": len(partial_books),
        "selection": normalized_selection,
        "shelf": normalized_shelf,
        "source_root": str(source_root),
        "unmatched_extract_roots": unmatched_extract_roots,
    }


def build_study_materials(
    db_path: Path = DEFAULT_DB,
    *,
    source_root: Path = DEFAULT_STUDY_SOURCE_ROOT,
    output_dir: Path = DEFAULT_STUDY_DIR,
    shelf: str = DEFAULT_STUDY_SHELF,
    selection: str = DEFAULT_STUDY_SELECTION,
    book: str | None = None,
) -> dict[str, Any]:
    normalized_shelf = validate_shelf(shelf)
    normalized_selection = validate_selection(selection)
    source_root = source_root.resolve()
    target_root = output_dir / normalized_shelf
    target_root.mkdir(parents=True, exist_ok=True)

    inventory = study_inventory(
        db_path,
        source_root=source_root,
        shelf=normalized_shelf,
        selection=normalized_selection,
        book=book,
    )
    strict_bundle = math_flashcard_bundle(db_path)
    strict_cards_by_book = {
        str(book["book_document_id"]): [dict(card) for card in book["cards"]]
        for book in strict_bundle["strict"]["books"]
    }

    files: list[str] = []
    index_books: list[dict[str, Any]] = []
    built_count = 0
    missing_count = 0
    no_valid_count = 0
    partial_count = 0

    for inventory_book in inventory["books"]:
        if inventory_book["status"] == "missing_extract":
            missing_count += 1
            index_books.append(
                {
                    "book_title": inventory_book["book_title"],
                    "document_id": inventory_book["document_id"],
                    "has_source_note": inventory_book["has_source_note"],
                    "note_path": inventory_book["note_path"],
                    "status": "missing_extract",
                    "title_source": inventory_book["title_source"],
                }
            )
            continue

        extract = discover_extract_book(Path(str(inventory_book["extract_root"])))
        if extract is None or not extract.valid_chapters:
            no_valid_count += 1
            index_books.append(
                {
                    "book_title": inventory_book["book_title"],
                    "document_id": inventory_book["document_id"],
                    "extract_root": inventory_book["extract_root"],
                    "has_source_note": inventory_book["has_source_note"],
                    "note_path": inventory_book["note_path"],
                    "status": "no_valid_chapters",
                    "skipped_chapters": [] if extract is None else list(extract.skipped_chapters),
                    "title_source": inventory_book["title_source"],
                }
            )
            continue

        built = build_book_material(
            extract,
            book_title=inventory_book["book_title"],
            strict_cards=strict_cards_by_book.get(extract.document_id, []),
        )
        book_status = "partial" if extract.skipped_chapters else "built"
        book_root = target_root / extract.document_id
        book_root.mkdir(parents=True, exist_ok=True)

        reader_stream_path = book_root / DEFAULT_READER_STREAM
        reader_stream_path.write_text(jsonl_text(built["rows"]))
        reader_plain_path = book_root / DEFAULT_READER_PLAIN
        reader_plain_path.write_text(render_reader_plain_text(built["rows"], extract.book_title), encoding="utf-8")
        definition_cards_path = book_root / DEFAULT_DEFINITION_CARDS
        definition_cards_path.write_text(jsonl_text(built["cards"]))

        manifest = {
            "book_title": inventory_book["book_title"],
            "built_at_utc": utc_now(),
            "chapter_count": len(extract.valid_chapters),
            "definition_card_count": len(built["cards"]),
            "document_id": extract.document_id,
            "extract_root": extract.root_path,
            "files": {
                "definition_cards": str(definition_cards_path),
                "reader_plain": str(reader_plain_path),
                "reader_stream": str(reader_stream_path),
            },
            "has_source_note": inventory_book["has_source_note"],
            "note_path": inventory_book["note_path"],
            "row_count": len(built["rows"]),
            "selection": normalized_selection,
            "shelf": normalized_shelf,
            "skipped_chapters": list(extract.skipped_chapters),
            "source_refs": {
                "book_manifest_path": extract.book_manifest_path,
                "chapter_refs": [
                    {
                        "chapter_id": chapter.chapter_id,
                        "chapter_json_path": chapter.chapter_json_path,
                        "chapter_manifest_path": chapter.chapter_manifest_path,
                        "chapter_md_path": chapter.chapter_md_path,
                        "source_pdf": chapter.source_pdf,
                    }
                    for chapter in extract.valid_chapters
                ],
            },
            "status": book_status,
            "title_source": inventory_book["title_source"],
        }
        manifest_path = book_root / DEFAULT_BOOK_MANIFEST
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        files.extend(
            [
                str(reader_stream_path),
                str(reader_plain_path),
                str(definition_cards_path),
                str(manifest_path),
            ]
        )
        if book_status == "partial":
            partial_count += 1
        else:
            built_count += 1
        index_books.append(
            {
                "book_title": inventory_book["book_title"],
                "definition_card_count": len(built["cards"]),
                "document_id": extract.document_id,
                "extract_root": extract.root_path,
                "has_source_note": inventory_book["has_source_note"],
                "manifest_path": str(manifest_path),
                "note_path": inventory_book["note_path"],
                "row_count": len(built["rows"]),
                "status": book_status,
                "title_source": inventory_book["title_source"],
            }
        )

    index = {
        "book_count": len(index_books),
        "books": index_books,
        "built_count": built_count,
        "catalog_db": str(db_path),
        "generated_at_utc": utc_now(),
        "materialized_count": built_count + partial_count,
        "missing_extract_count": missing_count,
        "no_valid_chapters_count": no_valid_count,
        "output_dir": str(target_root),
        "partial_count": partial_count,
        "selection": normalized_selection,
        "shelf": normalized_shelf,
        "source_root": str(source_root),
        "unmatched_extract_roots": inventory["unmatched_extract_roots"],
    }
    index_path = target_root / DEFAULT_INDEX
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files.append(str(index_path))
    return {
        "book_count": len(index_books),
        "built_count": built_count,
        "catalog_db": str(db_path),
        "file_count": len(files),
        "files": files,
        "index_path": str(index_path),
        "materialized_count": built_count + partial_count,
        "missing_extract_count": missing_count,
        "no_valid_chapters_count": no_valid_count,
        "output_dir": str(target_root),
        "partial_count": partial_count,
        "books": index_books,
        "selection": normalized_selection,
        "shelf": normalized_shelf,
        "source_root": str(source_root),
        "unmatched_extract_roots": inventory["unmatched_extract_roots"],
    }


def study_view(
    db_path: Path = DEFAULT_DB,
    identifier: str = "",
    *,
    view: str = DEFAULT_STUDY_VIEW,
    output_dir: Path = DEFAULT_STUDY_DIR,
    shelf: str = DEFAULT_STUDY_SHELF,
) -> dict[str, Any]:
    normalized_shelf = validate_shelf(shelf)
    normalized_view = validate_view(view)
    book_root, manifest = resolve_built_book(output_dir / normalized_shelf, identifier)
    if normalized_view == READER_VIEW:
        rows = load_jsonl(book_root / DEFAULT_READER_STREAM)
        return {
            "book_title": manifest["book_title"],
            "document_id": manifest["document_id"],
            "row_count": manifest["row_count"],
            "rows": rows,
            "status": manifest["status"],
            "view": normalized_view,
        }
    cards = load_jsonl(book_root / DEFAULT_DEFINITION_CARDS)
    return {
        "book_title": manifest["book_title"],
        "card_count": manifest["definition_card_count"],
        "cards": cards,
        "document_id": manifest["document_id"],
        "status": manifest["status"],
        "view": normalized_view,
    }


def export_study_materials(
    db_path: Path = DEFAULT_DB,
    *,
    source_root: Path = DEFAULT_STUDY_SOURCE_ROOT,
    output_dir: Path = DEFAULT_STUDY_DIR,
    shelf: str = DEFAULT_STUDY_SHELF,
    target: str = DEFAULT_STUDY_EXPORT_TARGET,
    book: str | None = None,
    export_all: bool = False,
) -> dict[str, Any]:
    normalized_target = validate_target(target)
    if bool(book) == bool(export_all):
        raise ValueError("choose exactly one of --book or --all")
    build_result = build_study_materials(
        db_path,
        source_root=source_root,
        output_dir=output_dir,
        shelf=shelf,
        book=book if not export_all else None,
    )
    shelf_root = output_dir / validate_shelf(shelf)
    index = json.loads((shelf_root / DEFAULT_INDEX).read_text(encoding="utf-8"))
    selected_books = select_index_books(index["books"], identifier=book, export_all=export_all)

    if normalized_target == CANONICAL_TARGET:
        files: list[str] = []
        exports: list[dict[str, Any]] = []
        for entry in selected_books:
            if entry["status"] not in MATERIALIZED_STATUSES:
                continue
            book_root = shelf_root / str(entry["document_id"])
            manifest = json.loads((book_root / DEFAULT_BOOK_MANIFEST).read_text(encoding="utf-8"))
            book_files = [str(book_root / DEFAULT_READER_STREAM), str(book_root / DEFAULT_READER_PLAIN), str(book_root / DEFAULT_DEFINITION_CARDS), str(book_root / DEFAULT_BOOK_MANIFEST)]
            files.extend(book_files)
            exports.append(
                {
                    "book_title": manifest["book_title"],
                    "document_id": manifest["document_id"],
                    "files": book_files,
                    "status": manifest["status"],
                }
            )
        return {
            "build": build_result,
            "export_count": len(exports),
            "exports": exports,
            "files": files,
            "target": normalized_target,
        }

    files = []
    exports = []
    for entry in selected_books:
        if entry["status"] not in MATERIALIZED_STATUSES:
            continue
        book_root = shelf_root / str(entry["document_id"])
        manifest = json.loads((book_root / DEFAULT_BOOK_MANIFEST).read_text(encoding="utf-8"))
        cards = load_jsonl(book_root / DEFAULT_DEFINITION_CARDS)
        if not cards:
            if export_all:
                exports.append(
                    {
                        "book_title": manifest["book_title"],
                        "card_count": 0,
                        "document_id": manifest["document_id"],
                        "status": "no_definition_cards",
                    }
                )
                continue
            raise ValueError(
                f"no definition cards available for discoflash export: {manifest['document_id']}"
            )
        export_text = render_discoflash_export(cards, manifest["book_title"])
        export_path = book_root / DEFAULT_STUDY_DISCOFLASH_EXPORT
        export_path.write_text(export_text, encoding="utf-8")
        validation = validate_discoflash_export(export_text)
        files.append(str(export_path))
        exports.append(
            {
                "book_title": manifest["book_title"],
                "card_count": len(cards),
                "document_id": manifest["document_id"],
                "export_path": str(export_path),
                "validation": validation,
            }
        )

    return {
        "build": build_result,
        "export_count": len(exports),
        "exports": exports,
        "files": files,
        "target": normalized_target,
    }


def maintained_shelf_books(db_path: Path, shelf: str) -> list[dict[str, Any]]:
    report = source_shelf_report(db_path, shelf, limit=1000)
    return [
        dict(note)
        for note in report["notes"]
        if str(note["source_type"]) == "book"
    ]


def maintained_shelf_book_map(db_path: Path, shelf: str) -> dict[str, dict[str, Any]]:
    return {
        str(book["document_id"]): dict(book)
        for book in maintained_shelf_books(db_path, shelf)
    }


def inventory_books_for_selection(
    db_path: Path,
    *,
    source_root: Path,
    shelf: str,
    selection: str,
    identifier: str | None,
) -> list[dict[str, Any]]:
    note_map = maintained_shelf_book_map(db_path, shelf)
    if selection == MAINTAINED_ONLY_SELECTION:
        books = select_maintained_books(list(note_map.values()), identifier=identifier)
        return [inventory_entry_from_note(book, source_root) for book in books]

    entries = [
        inventory_entry_from_extract_root(root, note_map)
        for root in discover_extract_candidate_roots(source_root)
    ]
    entries.sort(key=inventory_sort_key)
    if identifier is None:
        return entries
    return [resolve_inventory_book(entries, identifier)]


def probe_source_root_candidate(
    source_root: Path,
    *,
    maintained_books: list[dict[str, Any]],
    shelf: str,
) -> dict[str, Any]:
    inventory_books = [inventory_entry_from_note(book, source_root) for book in maintained_books]
    matched_books = [dict(book) for book in inventory_books if book["status"] != "missing_extract"]
    missing_books = [dict(book) for book in inventory_books if book["status"] == "missing_extract"]
    partial_books = [dict(book) for book in inventory_books if book["status"] == "partial"]
    no_valid_books = [dict(book) for book in inventory_books if book["status"] == "no_valid_chapters"]
    ready_books = [dict(book) for book in inventory_books if book["status"] == "ready"]
    unmatched_extract_roots = discover_unmatched_extract_roots(source_root, inventory_books)
    if not matched_books:
        status = "no_match"
    elif missing_books or partial_books or no_valid_books:
        status = "partial_candidate"
    else:
        status = "good_candidate"
    return {
        "candidate_path": str(source_root),
        "matched_book_count": len(matched_books),
        "matched_books": matched_books,
        "missing_book_count": len(missing_books),
        "missing_books": missing_books,
        "no_valid_book_count": len(no_valid_books),
        "no_valid_books": no_valid_books,
        "partial_book_count": len(partial_books),
        "partial_books": partial_books,
        "ready_book_count": len(ready_books),
        "ready_books": ready_books,
        "shelf": shelf,
        "status": status,
        "unmatched_extract_root_count": len(unmatched_extract_roots),
        "unmatched_extract_roots": unmatched_extract_roots,
    }


def inventory_entry_from_note(book: dict[str, Any], source_root: Path) -> dict[str, Any]:
    document_id = str(book["document_id"])
    extract_root = source_root / document_id
    if not extract_root.is_dir():
        display_title, title_source = select_app_title(
            source_note=book,
            manifest_title=None,
            fallback_name=document_id,
        )
        return {
            "book_title": display_title,
            "document_id": document_id,
            "extract_root": str(extract_root),
            "has_source_note": True,
            "note_path": str(book["path"]),
            "skipped_chapters": [],
            "status": "missing_extract",
            "title_source": title_source,
            "valid_chapter_count": 0,
        }

    extract = discover_extract_book(extract_root)
    display_title, title_source = select_app_title(
        source_note=book,
        manifest_title=None if extract is None else extract.book_title,
        fallback_name=document_id,
    )
    if extract is None:
        status = "missing_extract"
        valid_count = 0
        skipped_count = 0
        skipped_chapters: list[dict[str, Any]] = []
    else:
        valid_count = len(extract.valid_chapters)
        skipped_count = len(extract.skipped_chapters)
        skipped_chapters = list(extract.skipped_chapters)
        if valid_count == 0:
            status = "no_valid_chapters"
        elif skipped_count:
            status = "partial"
        else:
            status = "ready"
    return {
        "book_title": display_title,
        "document_id": document_id,
        "extract_root": str(extract_root),
        "has_source_note": True,
        "note_path": str(book["path"]),
        "skipped_chapters": skipped_chapters,
        "skipped_chapter_count": skipped_count,
        "status": status,
        "title_source": title_source,
        "valid_chapter_count": valid_count,
    }


def inventory_entry_from_extract_root(root: Path, note_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    extract = discover_extract_book(root)
    document_id = str(extract.document_id if extract is not None else root.name)
    source_note = note_map.get(document_id)
    display_title, title_source = select_app_title(
        source_note=source_note,
        manifest_title=None if extract is None else extract.book_title,
        fallback_name=document_id if extract is not None else root.name,
    )
    if extract is None:
        return {
            "book_title": display_title,
            "document_id": document_id,
            "extract_root": str(root),
            "has_source_note": source_note is not None,
            "note_path": None if source_note is None else str(source_note["path"]),
            "skipped_chapters": [],
            "skipped_chapter_count": 0,
            "status": "no_valid_chapters",
            "title_source": title_source,
            "valid_chapter_count": 0,
        }

    valid_count = len(extract.valid_chapters)
    skipped_count = len(extract.skipped_chapters)
    if valid_count == 0:
        status = "no_valid_chapters"
    elif skipped_count:
        status = "partial"
    else:
        status = "ready"
    return {
        "book_title": display_title,
        "document_id": document_id,
        "extract_root": str(root),
        "has_source_note": source_note is not None,
        "note_path": None if source_note is None else str(source_note["path"]),
        "skipped_chapters": list(extract.skipped_chapters),
        "skipped_chapter_count": skipped_count,
        "status": status,
        "title_source": title_source,
        "valid_chapter_count": valid_count,
    }


def discover_extract_book(root: Path) -> ExtractBook | None:
    if not root.is_dir():
        return None
    manifests_dir = root / "manifests"
    markdown_dir = root / "normalized_markdown"
    book_manifest_path = manifests_dir / "book.json"
    book_manifest = load_json_file(book_manifest_path)
    document_id = str(book_manifest.get("document_id") or root.name)
    book_title = normalize_display_title(str(book_manifest.get("book_title") or root.name.replace("_", " ")))
    chapter_count = book_manifest.get("chapter_count")
    chapter_ids = discover_chapter_ids(root)
    valid: list[ExtractChapter] = []
    skipped: list[dict[str, Any]] = []

    for chapter_id in chapter_ids:
        chapter_manifest_path = manifests_dir / f"{chapter_id}.json"
        chapter_manifest = load_json_file(chapter_manifest_path)
        chapter_json_path = resolve_chapter_json_path(root, chapter_id)
        chapter_md_path = markdown_dir / chapter_id / "chapter.md"
        reasons: list[str] = []
        if not chapter_md_path.exists():
            reasons.append("missing_normalized_markdown")
        chapter_json = load_json_file(chapter_json_path)
        chapter_markdown = chapter_md_path.read_text(encoding="utf-8") if chapter_md_path.exists() else ""
        markdown_title = markdown_chapter_title(chapter_markdown, chapter_id=chapter_id)
        chapter_number = chapter_manifest.get("chapter_number")
        if chapter_number is None:
            chapter_number = parse_chapter_number(chapter_id)
        chapter_title = normalize_chapter_title(
            str(
                chapter_json.get("chapter_title")
                or chapter_manifest.get("chapter_title")
                or markdown_title
                or chapter_id
            ),
            chapter_number=chapter_number,
        )
        if reasons:
            skipped.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "reasons": reasons,
                }
            )
            continue
        valid.append(
            ExtractChapter(
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                chapter_number=chapter_number,
                chapter_count=chapter_count if isinstance(chapter_count, int) else None,
                source_pdf=str(chapter_json.get("source_pdf") or ""),
                book_manifest_path=str(book_manifest_path),
                chapter_json_path=str(chapter_json_path),
                chapter_md_path=str(chapter_md_path),
                chapter_manifest_path=str(chapter_manifest_path) if chapter_manifest_path.exists() else None,
                chapter_json=chapter_json,
                chapter_markdown=chapter_markdown,
                chapter_manifest=chapter_manifest,
            )
        )
    valid.sort(key=chapter_sort_key)
    return ExtractBook(
        document_id=document_id,
        book_title=book_title,
        root_path=str(root),
        book_manifest_path=str(book_manifest_path),
        book_manifest=book_manifest,
        valid_chapters=tuple(valid),
        skipped_chapters=tuple(skipped),
    )


def discover_chapter_ids(root: Path) -> list[str]:
    chapter_ids: set[str] = set()
    chapter_ids.update(path.stem for path in (root / "manifests").glob("ch_*.json") if path.name != "book.json")
    chapter_ids.update(path.parent.name for path in (root / "chapter_json").glob("ch_*/chapter.json"))
    chapter_ids.update(path.parent.name for path in root.glob("ch_*/chapter.json"))
    chapter_ids.update(path.parent.name for path in (root / "normalized_markdown").glob("ch_*/chapter.md"))
    return sorted(chapter_ids)


def resolve_chapter_json_path(root: Path, chapter_id: str) -> Path:
    modern = root / "chapter_json" / chapter_id / "chapter.json"
    if modern.exists():
        return modern
    return root / chapter_id / "chapter.json"


def chapter_sort_key(chapter: ExtractChapter) -> tuple[int, str]:
    number = chapter.chapter_number if isinstance(chapter.chapter_number, int) else 10**9
    return (number, chapter.chapter_id)


def parse_chapter_number(chapter_id: str) -> int | None:
    match = CHAPTER_ID_NUMBER_RE.search(chapter_id)
    if match is None:
        return None
    return int(match.group(1))


def build_book_material(
    extract: ExtractBook,
    *,
    strict_cards: list[dict[str, Any]],
    book_title: str | None = None,
) -> dict[str, Any]:
    display_book_title = normalize_display_title(book_title or extract.book_title)
    known_terms = [str(card.get("concept_title") or "").strip() for card in strict_cards if str(card.get("concept_title") or "").strip()]
    rows: list[dict[str, Any]] = []
    ordinal = 1
    for chapter in extract.valid_chapters:
        sections = chapter_sections(chapter)
        for section_index, section in enumerate(sections, start=1):
            title = normalize_display_title(str(section.get("title") or chapter.chapter_title))
            content = clean_source_text(str(section.get("content") or ""))
            chapter_title = normalize_chapter_title(chapter.chapter_title, chapter_number=chapter.chapter_number)
            if not content or is_junk_section(title, content):
                continue
            base_kind = classify_chunk_kind(title, content)
            section_path = f"{chapter.chapter_id}/{section_index:03d}"
            title_path = chapter_title if not title else f"{chapter_title} > {title}"
            for chunk_text in split_section_content(content, kind=base_kind):
                source_text = clean_source_text(chunk_text)
                if not source_text or is_junk_section(title, source_text):
                    continue
                chunk_kind = classify_chunk_kind(title, source_text)
                row = {
                    "book_title": display_book_title,
                    "chapter_id": chapter.chapter_id,
                    "chapter_number": chapter.chapter_number,
                    "chapter_title": chapter_title,
                    "citation": {
                        "book_manifest_path": chapter.book_manifest_path,
                        "chapter_json_path": chapter.chapter_json_path,
                        "chapter_md_path": chapter.chapter_md_path,
                        "chapter_number": chapter.chapter_number,
                        "ordinal": ordinal,
                        "section_path": section_path,
                        "source_pdf": chapter.source_pdf,
                        "title_path": title_path,
                    },
                    "chunk_kind": chunk_kind,
                    "concept_tags": infer_concept_tags(title, source_text, known_terms),
                    "document_id": extract.document_id,
                    "formula_lines": extract_formula_lines(source_text),
                    "ordinal": ordinal,
                    "reader_text": reader_text_for_chunk(source_text, kind=chunk_kind),
                    "row_id": f"reader:math:{extract.document_id}:{ordinal:05d}",
                    "section_path": section_path,
                    "source_text": source_text,
                    "title_path": title_path,
                }
                rows.append(row)
                ordinal += 1
    cards = build_definition_cards(extract.document_id, rows, strict_cards)
    return {"cards": cards, "rows": rows}


def chapter_sections(chapter: ExtractChapter) -> list[dict[str, Any]]:
    sections = chapter.chapter_json.get("sections")
    if isinstance(sections, list) and sections:
        return [dict(section) for section in sections]
    parsed_sections = parse_markdown_sections(
        chapter.chapter_markdown,
        default_title=chapter.chapter_title,
        chapter_number=chapter.chapter_number,
    )
    if parsed_sections:
        return parsed_sections
    fallback_text = chapter.chapter_markdown
    fallback_body = clean_source_text(strip_markdown_headings(fallback_text))
    if not fallback_body:
        return []
    return [
        {
            "content": fallback_body,
            "level": 1,
            "title": normalize_chapter_title(chapter.chapter_title, chapter_number=chapter.chapter_number),
        }
    ]


def clean_heading(value: str) -> str:
    text = HTML_TAG_RE.sub("", value or "")
    text = MARKDOWN_INLINE_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    text = re.sub(r"^#+\s*", "", text)
    return text


def normalize_display_title(value: str) -> str:
    text = clean_heading(value).replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    if not text:
        return ""
    text = re.sub(r"\b([1-9])Ed\b", lambda m: f"{ordinal_label(int(m.group(1)))} Edition", text, flags=re.IGNORECASE)
    text = re.sub(r"\bVol(?:ume)?\.?\s+([0-9]+)\b", r"Volume \1", text, flags=re.IGNORECASE)
    if text.islower() or text[0].islower():
        text = smart_title_case(text)
    text = uppercase_roman_numerals(text)
    return text


def normalize_chapter_title(chapter_title: str, *, chapter_number: int | None) -> str:
    title = clean_heading(chapter_title)
    title = PAGE_NOISE_RE.sub("", title).strip()
    if not re.match(r"^chapter\s+\d+\s*$", title, flags=re.IGNORECASE):
        title = re.sub(r"\s+\d{1,4}$", "", title).strip()
    title = re.sub(r"^\d{1,4}\s+\|?\s*", "", title).strip()
    title = normalize_display_title(title)
    title = strip_matching_chapter_number(title, chapter_number)
    title = strip_leading_section_number(title)
    title = strip_short_noisy_prefix(title)
    title = re.sub(r"\s+\d{1,4}$", "", title).strip()
    title = re.sub(r"^\|+\s*", "", title).strip()
    title = title.lstrip(" ([{,.:;-")
    title = normalize_display_title(title)
    if title and title_is_chapter_noise(title):
        title = ""
    if title:
        return title
    if chapter_number is not None:
        return f"Chapter {chapter_number}"
    return normalize_display_title(chapter_title)


def clean_source_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = HTML_TAG_RE.sub("", text)
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        cleaned_lines = [WHITESPACE_RE.sub(" ", line.strip()) for line in paragraph.splitlines() if line.strip()]
        if not cleaned_lines:
            continue
        paragraphs.append(" ".join(cleaned_lines))
    return "\n\n".join(paragraphs).strip()


def is_junk_section(title: str, text: str) -> bool:
    normalized_title = clean_heading(title)
    normalized_text = clean_source_text(text)
    lowered_title = normalized_title.lower()
    lowered_text = normalized_text.lower()
    if normalized_title and JUNK_TITLE_RE.match(normalized_title):
        return True
    if normalized_title and BACK_MATTER_TITLE_RE.search(normalized_title):
        return True
    if JUNK_TEXT_RE.search(normalized_title) or JUNK_TEXT_RE.search(normalized_text):
        return True
    if BACK_MATTER_TEXT_RE.search(normalized_text):
        return True
    if lowered_text.startswith("table of contents"):
        return True
    if lowered_title == "selected published titles in this series":
        return True
    if looks_like_contents_table(text):
        return True
    if looks_like_index_dump(text):
        return True
    if IMAGE_ONLY_RE.fullmatch(normalized_text):
        return True
    if not re.sub(r"!\[[^\]]*\]\([^)]+\)|<img\b[^>]*>", "", normalized_text, flags=re.IGNORECASE).strip():
        return True
    return False


def looks_like_contents_table(text: str) -> bool:
    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines() if line.strip()]
    if len(lines) < 3:
        stripped = text.strip()
        if not (
            stripped.startswith("|")
            and stripped.endswith("|")
            and stripped.count("|") >= 8
        ):
            return False
    clean_text = clean_source_text(text)
    has_code_fence = clean_text.startswith("```")
    table_like_lines = [
        line
        for line in lines[:10]
        if line.startswith("|")
        or line.endswith("|")
        or re.fullmatch(r"[|:\- ]{5,}", line) is not None
    ]
    if not has_code_fence:
        if len(lines) >= 3:
            if len(table_like_lines) < 3:
                return False
        elif not (
            text.strip().startswith("|")
            and text.strip().endswith("|")
            and text.strip().count("|") >= 8
        ):
            return False
    sample = " ".join(line.strip("`*| ") for line in lines[:10])
    hint_hits = len(CONTENTS_HINT_RE.findall(sample))
    number_hits = len(PAGE_NUMBER_CLUSTER_RE.findall(sample))
    section_hits = len(re.findall(r"\b\d+\.\d+\b", sample))
    if len(lines) == 1 and "chapter" in sample.lower() and section_hits >= 2 and number_hits >= 4:
        return True
    if hint_hits >= 2 and number_hits >= 4:
        return True
    if "preface" in sample.lower() and number_hits >= 3:
        return True
    if "table of contents" in sample.lower():
        return True
    if "chapter" in sample.lower() and section_hits >= 2 and number_hits >= 4:
        return True
    return False


def strip_markdown_headings(text: str) -> str:
    lines = text.replace("\r\n", "\n").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def markdown_chapter_title(markdown: str, *, chapter_id: str) -> str | None:
    chapter_number = parse_chapter_number(chapter_id)
    for line in markdown.replace("\r\n", "\n").splitlines():
        match = MARKDOWN_HEADING_RE.match(line.strip())
        if match is None:
            continue
        title = normalize_chapter_title(clean_heading(match.group(2)), chapter_number=chapter_number)
        if title:
            return title
    return None


def parse_markdown_sections(markdown: str, *, default_title: str, chapter_number: int | None) -> list[dict[str, Any]]:
    lines = markdown.replace("\r\n", "\n").splitlines()
    sections: list[dict[str, Any]] = []
    current_title: str | None = None
    current_level = 1
    current_lines: list[str] = []
    first_heading_consumed = False

    def flush() -> None:
        content = clean_source_text("\n".join(current_lines))
        if not content:
            return
        sections.append(
            {
                "content": content,
                "level": current_level,
                "title": normalize_display_title(current_title or default_title),
            }
        )

    for raw_line in lines:
        line = raw_line.rstrip()
        heading = MARKDOWN_HEADING_RE.match(line.strip())
        if heading is None:
            current_lines.append(line)
            continue
        heading_title = normalize_display_title(
            strip_short_noisy_prefix(
                strip_leading_section_number(
                    strip_matching_chapter_number(clean_heading(heading.group(2)), chapter_number)
                )
            )
        )
        if not heading_title:
            continue
        if not first_heading_consumed:
            first_heading_consumed = True
            current_title = None
            current_level = 1
            current_lines = []
            continue
        flush()
        current_title = heading_title
        current_level = len(heading.group(1))
        current_lines = []
    flush()
    return sections


def strip_matching_chapter_number(title: str, chapter_number: int | None) -> str:
    if chapter_number is None:
        return title.strip()
    text = title.strip()
    if not text:
        return text
    chapter_prefix = re.match(rf"^chapter\s+{chapter_number}\b[:.\- ]*\s*(.*)$", text, re.IGNORECASE)
    if chapter_prefix is not None:
        return chapter_prefix.group(1).strip()
    raw_prefix = re.match(r"^([0-9OoIlVv.\-() ]+)\s+(.*)$", text)
    if raw_prefix is None:
        return text
    numericish = re.sub(r"[^0-9OoIl]", "", raw_prefix.group(1))
    numericish = (
        numericish.replace("O", "0")
        .replace("o", "0")
        .replace("I", "1")
        .replace("l", "1")
        .replace("V", "7")
        .replace("v", "7")
    )
    chapter_digits = str(chapter_number)
    if numericish == chapter_digits or (
        numericish.startswith(chapter_digits)
        and len(numericish) <= len(chapter_digits) + 2
    ):
        return raw_prefix.group(2).strip()
    return text


def strip_leading_section_number(title: str) -> str:
    stripped = re.sub(r"^\d+(?:\.\d+)*\s+", "", title).strip()
    return stripped or title.strip()


def strip_short_noisy_prefix(title: str) -> str:
    text = title.strip()
    match = re.match(r"^(?P<prefix>[0-9A-ZOoIlVv().-]{1,5})\s+(?P<rest>.+)$", text)
    if match is None:
        return text
    prefix = match.group("prefix")
    rest = match.group("rest").strip()
    if not rest:
        return text
    if not any(char.isdigit() for char in prefix):
        if prefix not in {"O", "0"}:
            return text
    if prefix in {"O", "0"} and len(rest.split()) >= 2 and re.search(r"[a-z]{3,}", rest):
        return rest
    if not any(char.isdigit() for char in prefix):
        return text
    if len(rest.split()) < 2:
        return text
    if not re.search(r"[a-z]{3,}", rest):
        return text
    return rest


def title_is_chapter_noise(title: str) -> bool:
    cleaned = clean_heading(title)
    if not cleaned:
        return True
    if PURE_DIGITS_RE.fullmatch(cleaned):
        return True
    if TITLE_SENTENCE_RE.search(cleaned) and len(cleaned.split()) > 6:
        return True
    if "..." in cleaned:
        return True
    compact = re.sub(r"\s+", "", cleaned)
    if len(compact) > 28 and re.search(r"[a-z][A-Z]", cleaned):
        return True
    if re.match(r"^\d+(?:\.\d+)+\S", cleaned):
        return True
    return False


def looks_like_index_dump(text: str) -> bool:
    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    sample = " ".join(lines[:6]).lower()
    if "a wiley-interscience series of texts" in sample:
        return True
    if "founded by richard courant" in sample:
        return True
    index_hits = 0
    for line in lines[:80]:
        plain = re.sub(r"[`*|]+", "", line).strip()
        if not plain:
            continue
        if READER_INDEX_LINE_RE.match(plain):
            index_hits += 1
            continue
        if re.match(r"^[A-Z][A-Z ,.'’\-&]+[—-]", plain):
            index_hits += 1
            continue
        digits = sum(1 for char in plain if char.isdigit())
        commas = plain.count(",")
        if digits >= 8 and commas >= 3:
            index_hits += 1
            continue
        if len(plain) > 120 and digits >= 10:
            index_hits += 1
    return index_hits >= max(3, min(8, len(lines) // 4))


def classify_chunk_kind(title: str, text: str) -> str:
    lowered_title = title.strip().lower()
    for kind, pattern in TITLE_KIND_PATTERNS:
        if re.match(pattern, lowered_title):
            return kind
    first_sentence = first_sentence_of(text).lower()
    if looks_like_formula_block(text):
        return "formula_block"
    if looks_like_definition(lowered_title, first_sentence):
        return "definition"
    return "exposition"


def looks_like_formula_block(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    formula_like = sum(1 for line in lines if FORMULA_LINE_RE.search(line))
    return formula_like >= 2 and formula_like >= max(2, len(lines) // 2)


def looks_like_definition(title: str, first_sentence: str) -> bool:
    if title and title not in {"chapter", "introduction", "examples"}:
        if TERM_SENTENCE_RE.match(first_sentence):
            return True
        if 1 <= len(title.split()) <= 8 and not re.search(r"\b(example|remark|exercise|proof)\b", title):
            if TERM_SENTENCE_RE.match(first_sentence):
                return True
    return False


def split_section_content(text: str, *, kind: str) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    if not paragraphs:
        return []
    if kind in STRICT_KINDS:
        return chunk_paragraphs(paragraphs, target_chars=STRICT_TARGET_CHARS, max_chars=STRICT_TARGET_CHARS + 240)
    if kind == "formula_block":
        return chunk_paragraphs(paragraphs, target_chars=DISPLAY_TARGET_CHARS, max_chars=DISPLAY_TARGET_CHARS + 140)
    return chunk_paragraphs(paragraphs, target_chars=DISPLAY_TARGET_CHARS, max_chars=DISPLAY_MAX_CHARS)


def chunk_paragraphs(paragraphs: list[str], *, target_chars: int, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        paragraph_parts = split_long_paragraph(paragraph, max_chars=max_chars)
        for part in paragraph_parts:
            next_len = current_len + len(part) + (2 if current else 0)
            if current and next_len > target_chars:
                chunks.append("\n\n".join(current))
                current = [part]
                current_len = len(part)
            else:
                current.append(part)
                current_len = next_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def split_long_paragraph(paragraph: str, *, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    parts: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        next_len = current_len + len(sentence) + (1 if current else 0)
        if current and next_len > max_chars:
            parts.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len = next_len
    if current:
        parts.append(" ".join(current))
    return parts or [paragraph]


def reader_text_for_chunk(text: str, *, kind: str) -> str:
    if kind in STRICT_KINDS or kind == "formula_block":
        return text
    compact = YEAR_CITATION_RE.sub("", text)
    compact = WHITESPACE_RE.sub(" ", compact)
    return compact.strip()


def extract_formula_lines(text: str) -> list[str]:
    formulas = [line.strip() for line in text.splitlines() if line.strip() and FORMULA_LINE_RE.search(line)]
    return formulas[:8]


def infer_concept_tags(title: str, text: str, known_terms: list[str]) -> list[str]:
    tags: list[str] = []
    term = extracted_term(title, text)
    if term:
        tags.append(term)
    combined = normalize_name(f"{title} {text}")
    for known in known_terms:
        normalized = normalize_name(known)
        if normalized and normalized in combined and known not in tags:
            tags.append(known)
    return tags


def build_definition_cards(
    document_id: str,
    rows: list[dict[str, Any]],
    strict_cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    ordinal = 1

    for row_index, row in enumerate(rows):
        section_title = row_section_title(row)
        anchor = explicit_card_anchor(section_title, str(row["source_text"]))
        if anchor is None:
            continue
        raw_term, source_kind = anchor
        resolved = resolve_card_term(
            raw_term,
            source_kind=source_kind,
            row=row,
            rows=rows,
            row_index=row_index,
        )
        if resolved is None:
            continue
        term, resolution_kind = resolved
        if not is_display_quality_card_term(term, source_kind=source_kind):
            continue
        candidates.append(
            candidate_definition_card(
                document_id=document_id,
                row=row,
                source_kind=source_kind,
                raw_term=raw_term,
                term=term,
                term_resolution_kind=resolution_kind,
                stage="explicit",
            )
        )

    for row_index, row in enumerate(rows):
        anchor = promoted_heading_card_anchor(row)
        if anchor is None:
            continue
        raw_term, source_kind = anchor
        resolved = resolve_card_term(
            raw_term,
            source_kind=source_kind,
            row=row,
            rows=rows,
            row_index=row_index,
        )
        if resolved is None:
            continue
        term, _resolution_kind = resolved
        if not is_display_quality_card_term(term, source_kind=source_kind):
            continue
        candidates.append(
            candidate_definition_card(
                document_id=document_id,
                row=row,
                source_kind=source_kind,
                raw_term=raw_term,
                term=term,
                term_resolution_kind="promoted_heading",
                stage="promoted_heading",
            )
        )

    candidates = dedupe_definition_candidates(candidates)
    cards: list[dict[str, Any]] = []
    for candidate in candidates:
        normalized = normalize_name(str(candidate["term"]))
        if normalized in seen_terms:
            continue
        seen_terms.add(normalized)
        cards.append(finalize_candidate_definition_card(candidate, ordinal=ordinal))
        ordinal += 1

    if len(cards) < THIN_DECK_MIN_CARDS and len(rows) >= THIN_DECK_MIN_CARDS:
        rescue_candidates: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows):
            for raw_term, source_kind, resolution_kind in thin_deck_rescue_anchors(
                row=row,
                rows=rows,
                row_index=row_index,
            ):
                resolved = normalize_candidate_term(raw_term, source_kind=source_kind)
                if resolved is None:
                    continue
                if not is_display_quality_card_term(resolved, source_kind=source_kind):
                    continue
                rescue_candidates.append(
                    candidate_definition_card(
                        document_id=document_id,
                        row=row,
                        source_kind=source_kind,
                        raw_term=raw_term,
                        term=resolved,
                        term_resolution_kind=resolution_kind,
                        stage="thin_deck_rescue",
                    )
                )
        for candidate in dedupe_definition_candidates(rescue_candidates):
            normalized = normalize_name(str(candidate["term"]))
            if normalized in seen_terms:
                continue
            seen_terms.add(normalized)
            cards.append(finalize_candidate_definition_card(candidate, ordinal=ordinal))
            ordinal += 1
            if len(cards) >= THIN_DECK_MIN_CARDS:
                break

    for strict_card in strict_cards:
        term = str(strict_card.get("concept_title") or "").strip()
        if not term:
            continue
        if not is_display_quality_card_term(term, source_kind="strict_concept"):
            continue
        normalized = normalize_name(term)
        if normalized in seen_terms:
            continue
        seen_terms.add(normalized)
        cards.append(
            {
                "card_id": f"studycard:math:{document_id}:{ordinal:04d}:{normalized}",
                "chapter_id": None,
                "chapter_number": None,
                "card_source_kind": "strict_concept",
                "concept_tags": [term],
                "confidence": str(strict_card.get("association_confidence") or "high"),
                "definition": str(strict_card.get("back") or "").strip(),
                "document_id": document_id,
                "raw_term": term,
                "source_row_id": None,
                "term": term,
                "term_resolution_kind": "original",
            }
        )
        ordinal += 1
    return cards


def candidate_definition_card(
    *,
    document_id: str,
    row: dict[str, Any],
    source_kind: str,
    raw_term: str,
    term: str,
    term_resolution_kind: str,
    stage: str,
) -> dict[str, Any]:
    return {
        "chapter_id": row["chapter_id"],
        "chapter_number": row["chapter_number"],
        "card_source_kind": source_kind,
        "concept_tags": row["concept_tags"],
        "confidence": "high",
        "definition": row["reader_text"],
        "document_id": document_id,
        "raw_term": raw_term,
        "source_row_id": row["row_id"],
        "stage": stage,
        "term": term,
        "term_resolution_kind": term_resolution_kind,
    }


def finalize_candidate_definition_card(candidate: dict[str, Any], *, ordinal: int) -> dict[str, Any]:
    term = str(candidate["term"])
    normalized = normalize_name(term)
    return {
        "card_id": f"studycard:math:{candidate['document_id']}:{ordinal:04d}:{normalized}",
        "chapter_id": candidate["chapter_id"],
        "chapter_number": candidate["chapter_number"],
        "card_source_kind": candidate["card_source_kind"],
        "concept_tags": candidate["concept_tags"],
        "confidence": candidate["confidence"],
        "definition": candidate["definition"],
        "document_id": candidate["document_id"],
        "raw_term": candidate["raw_term"],
        "source_row_id": candidate["source_row_id"],
        "term": candidate["term"],
        "term_resolution_kind": candidate["term_resolution_kind"],
    }


def dedupe_definition_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for candidate in candidates:
        source_row_id = str(candidate.get("source_row_id") or "")
        if not source_row_id:
            passthrough.append(candidate)
            continue
        family = candidate_source_family(str(candidate.get("card_source_kind") or ""))
        grouped.setdefault((source_row_id, family), []).append(candidate)

    selected: list[dict[str, Any]] = list(passthrough)
    for _key, group in grouped.items():
        group.sort(key=candidate_sort_key)
        chosen: list[dict[str, Any]] = []
        for candidate in group:
            candidate_norm = normalize_name(str(candidate.get("term") or ""))
            candidate_text = clean_heading(str(candidate.get("term") or "")).lower()
            reject = False
            for existing in list(chosen):
                existing_norm = normalize_name(str(existing.get("term") or ""))
                existing_text = clean_heading(str(existing.get("term") or "")).lower()
                if candidate_norm == existing_norm:
                    reject = True
                    break
                if existing_text and candidate_text and (
                    existing_text in candidate_text or candidate_text in existing_text
                ):
                    if candidate_sort_key(candidate) < candidate_sort_key(existing):
                        chosen.remove(existing)
                        continue
                    reject = True
                    break
            if not reject:
                chosen.append(candidate)
        selected.extend(chosen)
    return sorted(selected, key=lambda item: (str(item.get("source_row_id") or ""), candidate_sort_key(item)))


def candidate_source_family(source_kind: str) -> str:
    return "result" if source_kind.startswith("named_") else "definition"


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, str]:
    stage_rank = {
        "explicit": 0,
        "promoted_heading": 1,
        "thin_deck_rescue": 2,
    }
    source_kind = str(candidate.get("card_source_kind") or "")
    source_rank = {
        "named_theorem": 0,
        "named_lemma": 0,
        "named_proposition": 0,
        "named_corollary": 0,
        "definition_heading": 1,
        "inline_definition": 2,
    }.get(source_kind, 5)
    term = str(candidate.get("term") or "")
    return (
        stage_rank.get(str(candidate.get("stage") or ""), 9),
        source_rank,
        len(clean_heading(term)),
        normalize_name(term),
    )


def row_section_title(row: dict[str, Any]) -> str:
    return str(row.get("title_path") or "").split(">")[-1].strip()


def extracted_term(title: str, text: str) -> str | None:
    stripped_title = TITLE_PREFIX_RE.sub("", clean_heading(title)).strip(" .:-")
    if stripped_title and stripped_title.lower() not in {"definition", "theorem", "lemma", "proposition", "corollary"}:
        if not looks_like_term_candidate(stripped_title):
            return None
        return stripped_title
    sentence = first_sentence_of(text)
    match = TERM_SENTENCE_RE.match(sentence)
    if match is None:
        return None
    term = ARTICLE_RE.sub("", match.group("term")).strip(" .,:;-")
    if not term or len(term.split()) > 10:
        return None
    if not looks_like_term_candidate(term):
        return None
    return term


def explicit_card_anchor(section_title: str, text: str) -> tuple[str, str] | None:
    if row_is_structural_only(section_title, text):
        return None
    heading_anchor = heading_card_anchor(section_title)
    if heading_anchor is not None:
        return heading_anchor
    source_heading_anchor = source_heading_card_anchor(text)
    if source_heading_anchor is not None:
        return source_heading_anchor
    return inline_definition_card_anchor(text)


def thin_deck_rescue_anchors(
    *,
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    row_index: int,
) -> list[tuple[str, str, str]]:
    del rows, row_index
    section_title = clean_heading(row_section_title(row)).lower()
    if re.match(r"^(exercise|exercises|problem|problems|problem set|program|programs|summary|preface|proof|proofs)\b", section_title):
        return []
    source_text = clean_source_text(str(row.get("source_text") or ""))
    if not source_text:
        return []
    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(term: str | None) -> None:
        if not term:
            return
        cleaned = thin_rescue_candidate_term(term)
        if not cleaned:
            return
        normalized = normalize_name(cleaned)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append((cleaned, "definition_heading", "thin_deck_rescue"))

    for pattern in (THIN_RESCUE_DEFINE_RE, THIN_RESCUE_DENOTE_RE, THIN_RESCUE_THERE_IS_RE, THIN_RESCUE_EXACT_TERM_RE):
        for match in pattern.finditer(source_text):
            add(match.group("term"))
    for match in THIN_RESCUE_NOUN_PHRASE_RE.finditer(source_text):
        add(match.group("term"))
    return candidates


def promoted_heading_card_anchor(row: dict[str, Any]) -> tuple[str, str] | None:
    chunk_kind = str(row.get("chunk_kind") or "")
    if chunk_kind not in {"definition", "theorem", "exposition"}:
        return None
    section_title = row_section_title(row)
    chapter_title = str(row.get("chapter_title") or "")
    candidate = promoted_heading_term_candidate(section_title, chapter_title=chapter_title)
    if candidate is None:
        return None
    source_kind = promoted_heading_source_kind(candidate, chunk_kind=chunk_kind)
    return candidate, source_kind


def promoted_heading_term_candidate(section_title: str, *, chapter_title: str) -> str | None:
    title = clean_heading(section_title)
    if not title:
        return None
    if normalize_name(title) == normalize_name(chapter_title):
        return None
    if CODEISH_HEADING_RE.search(title):
        return None
    title = PROMOTED_HEADING_NUMBER_RE.sub("", title).strip(" .:-")
    title = TITLE_PREFIX_RE.sub("", title).strip(" .:-")
    if not title:
        return None
    lowered = title.lower()
    if PROMOTED_HEADING_REJECT_RE.match(lowered):
        return None
    if lowered == "selected published titles in this series":
        return None
    if lowered.startswith("program "):
        return None
    if (
        DISCOURSE_TERM_PREFIX_RE.match(title)
        or VERBISH_HEADING_TAIL_RE.match(title)
        or PROMOTED_DISCOURSE_PREFIX_RE.match(title)
    ):
        return None
    return title


def promoted_heading_source_kind(term: str, *, chunk_kind: str) -> str:
    if PROMOTED_RESULT_KEYWORD_RE.search(term):
        return "named_theorem"
    if chunk_kind == "theorem" and looks_like_named_result_candidate(term):
        return "named_theorem"
    return "definition_heading"


def resolve_card_term(
    raw_term: str,
    *,
    source_kind: str,
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    row_index: int,
) -> tuple[str, str] | None:
    direct = normalize_candidate_term(raw_term, source_kind=source_kind)
    prefer_context = (
        direct is not None
        and source_kind == "inline_definition"
        and prefers_contextual_inline_term(direct)
    )
    if direct is not None and not prefer_context:
        resolution_kind = "original" if clean_heading(raw_term).strip() == direct else "direct_cleanup"
        return direct, resolution_kind

    for candidate in contextual_card_candidates(
        row=row,
        rows=rows,
        row_index=row_index,
        source_kind=source_kind,
    ):
        resolved = normalize_candidate_term(candidate, source_kind=source_kind)
        if resolved is not None:
            return resolved, "context_recovery"
    if direct is not None:
        resolution_kind = "original" if clean_heading(raw_term).strip() == direct else "direct_cleanup"
        return direct, resolution_kind
    return None


def contextual_card_candidates(
    *,
    row: dict[str, Any],
    rows: list[dict[str, Any]],
    row_index: int,
    source_kind: str,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        if not candidate:
            return
        cleaned = clean_heading(candidate)
        if not cleaned or cleaned in seen or is_rejected_card_heading(cleaned):
            return
        seen.add(cleaned)
        candidates.append(cleaned)

    section_title = str(row.get("title_path") or "").split(">")[-1].strip()
    source_text = str(row.get("source_text") or "")

    if source_kind.startswith("named_") or source_kind == "definition_heading":
        heading_anchor = heading_card_anchor(section_title)
        if heading_anchor is not None:
            add(heading_anchor[0])
        source_anchor = source_heading_card_anchor(source_text)
        if source_anchor is not None:
            add(source_anchor[0])
    if source_kind == "inline_definition":
        inline_anchor = inline_definition_card_anchor(source_text)
        if inline_anchor is not None:
            add(inline_anchor[0])
        add(section_title)

    title_path = str(row.get("title_path") or "")
    start = max(0, row_index - 2)
    end = min(len(rows), row_index + 3)
    for neighbor in rows[start:end]:
        if neighbor is row:
            continue
        if str(neighbor.get("title_path") or "") != title_path:
            continue
        neighbor_section = str(neighbor.get("title_path") or "").split(">")[-1].strip()
        neighbor_source_text = str(neighbor.get("source_text") or "")
        if source_kind.startswith("named_") or source_kind == "definition_heading":
            heading_anchor = heading_card_anchor(neighbor_section)
            if heading_anchor is not None:
                add(heading_anchor[0])
            source_anchor = source_heading_card_anchor(neighbor_source_text)
            if source_anchor is not None:
                add(source_anchor[0])
        if source_kind == "inline_definition":
            inline_anchor = inline_definition_card_anchor(neighbor_source_text)
            if inline_anchor is not None:
                add(inline_anchor[0])

    return candidates


def row_is_structural_only(section_title: str, text: str) -> bool:
    title = clean_heading(section_title)
    if title and is_rejected_card_heading(title):
        return True
    source_start = leading_source_fragment(text)
    return bool(source_start and is_rejected_card_heading(source_start))


def heading_card_anchor(section_title: str) -> tuple[str, str] | None:
    match = CARD_HEADING_RE.match(clean_heading(section_title))
    if match is None:
        return None
    label = match.group("label").lower()
    if label == "notation":
        source_kind = "definition_heading"
    elif label == "definition":
        source_kind = "definition_heading"
    else:
        source_kind = f"named_{label}"
    term = extract_heading_term_candidate(match.group("tail"), label=label)
    if term is None:
        return None
    return term, source_kind


def source_heading_card_anchor(text: str) -> tuple[str, str] | None:
    source_start = leading_source_fragment(text)
    if not source_start:
        return None
    match = SOURCE_ENTITY_RE.match(source_start)
    if match is None:
        return None
    label = match.group("label").lower()
    if label == "notation":
        source_kind = "definition_heading"
    elif label == "definition":
        source_kind = "definition_heading"
    else:
        source_kind = f"named_{label}"
    term = extract_heading_term_candidate(match.group("tail"), label=label)
    if term is None:
        return None
    return term, source_kind


def inline_definition_card_anchor(text: str) -> tuple[str, str] | None:
    sentence = clean_heading(first_sentence_of(text))
    if not sentence:
        return None
    for pattern in (INLINE_DEFINE_TO_BE_RE, INLINE_WE_SAY_IS_RE, INLINE_IS_CALLED_RE, INLINE_DENOTES_RE):
        match = pattern.search(sentence)
        if match is None:
            continue
        return match.group("term"), "inline_definition"
    return None


def extract_heading_term_candidate(tail: str, *, label: str) -> str | None:
    cleaned_tail = clean_heading(tail).strip(" .:-")
    if not cleaned_tail:
        return None if label != "definition" else None
    parenthetical = re.match(r"^\(([^)]+)\)", cleaned_tail)
    if parenthetical is not None:
        return parenthetical.group(1)
    truncated = re.split(r"(?:(?<=\.)\s+|\b(?:Let|Suppose|If|For|Show|Then|There exists)\b)", cleaned_tail, maxsplit=1)[0]
    truncated = truncated.strip(" .:-")
    if not truncated:
        return None
    if VERBISH_HEADING_TAIL_RE.match(truncated):
        return None
    return truncated


def leading_source_fragment(text: str) -> str:
    cleaned = clean_source_text(text)
    if not cleaned:
        return ""
    return clean_heading(first_sentence_of(cleaned))


def normalize_inline_card_term(term: str) -> str | None:
    cleaned = preclean_card_term(term, source_kind="inline_definition")
    if not cleaned:
        return None
    if "," in cleaned:
        return None
    if DISCOURSE_TERM_PREFIX_RE.match(cleaned):
        return None
    if WEAK_INLINE_PREFIX_RE.match(cleaned):
        return None
    if GENERIC_ENTITY_NAME_RE.fullmatch(cleaned):
        return None
    if is_rejected_card_heading(cleaned):
        return None
    if SECTION_STYLE_RE.match(cleaned):
        return None
    if VARIABLE_TAIL_RE.search(cleaned):
        return None
    if len(cleaned.split()) > 6:
        return None
    if not looks_like_term_candidate(cleaned):
        return None
    if symbol_heavy_term(cleaned):
        return None
    return cleaned


def thin_rescue_candidate_term(term: str) -> str | None:
    cleaned = preclean_card_term(term, source_kind="definition_heading")
    cleaned = re.sub(r"^definition of\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:define|denote)\s+(?:the\s+|an?\s+)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:is|are)\s+(?:an?\s+|the\s+)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b([A-Za-z])$", "", cleaned).strip(" .,:;-)('\"")
    cleaned = re.sub(r"\bsets\b", "set", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bclasses\b", "class", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfunctions\b", "function", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmeasures\b", "measure", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bdistributions\b", "distribution", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\brelations\b", "relation", cleaned, flags=re.IGNORECASE)
    cleaned = clean_heading(cleaned).replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-)('\"")
    cleaned = re.sub(r"\bC\s+antor\b", "Cantor", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bBorel\b", "Borel", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bLebesgue\b", "Lebesgue", cleaned, flags=re.IGNORECASE)
    cleaned = uppercase_roman_numerals(cleaned)
    if not cleaned:
        return None
    if DISCOURSE_TERM_PREFIX_RE.match(cleaned):
        return None
    if GENERIC_RESCUE_TERM_RE.fullmatch(cleaned):
        return None
    if is_rejected_card_heading(cleaned):
        return None
    if re.match(r"^(?:contains|contain|images?\s+of|complement\s+of|of\s+the|up\s+to)\b", cleaned, re.IGNORECASE):
        return None
    if len(cleaned.split()) > 5:
        return None
    if symbol_heavy_term(cleaned):
        return None
    if not any(char.isalpha() for char in cleaned):
        return None
    return cleaned


def normalize_named_card_term(term: str) -> str | None:
    cleaned = preclean_card_term(term, source_kind="named_result")
    if not cleaned:
        return None
    if THEOREM_REF_ONLY_RE.match(cleaned):
        return None
    if AUTHOR_YEAR_ONLY_RE.match(cleaned):
        return None
    cleaned = strip_authorish_result_tail(cleaned)
    cleaned = YEAR_ONLY_TAIL_RE.sub("", cleaned).strip(" .,:;-)('\"")
    if GENERIC_ENTITY_NAME_RE.fullmatch(cleaned):
        return None
    if TRAILING_CITATION_RE.search(cleaned):
        return None
    if UNMATCHED_BRACKET_TEXT_RE.search(cleaned):
        return None
    if SINGLE_LETTER_RE.fullmatch(cleaned):
        return None
    if RESULT_STATEMENT_RE.search(cleaned):
        return None
    if len(cleaned.split()) > 10:
        return None
    if FORMULAISH_TEXT_RE.search(cleaned):
        return None
    if not any(char.isalpha() for char in cleaned):
        return None
    if not looks_like_named_result_candidate(cleaned):
        return None
    return cleaned


def normalize_candidate_term(term: str, *, source_kind: str) -> str | None:
    if source_kind == "inline_definition":
        return normalize_inline_card_term(term)
    if source_kind in {"named_theorem", "named_lemma", "named_proposition", "named_corollary"}:
        return normalize_named_card_term(term)
    cleaned = preclean_card_term(term, source_kind=source_kind)
    if not cleaned:
        return None
    if SECTION_STYLE_RE.match(cleaned):
        return None
    if not looks_like_term_candidate(cleaned):
        return None
    if symbol_heavy_term(cleaned):
        return None
    return cleaned


def preclean_card_term(term: str, *, source_kind: str) -> str:
    cleaned = clean_heading(term)
    cleaned = SECTION_MARKER_RE.sub("", cleaned)
    cleaned = LEADING_NUMBERING_RE.sub("", cleaned)
    cleaned = TRAILING_CITATION_RE.sub("", cleaned)
    cleaned = PROMPT_TAIL_RE.sub("", cleaned)
    if source_kind == "inline_definition":
        cleaned = re.split(r"\)\s+or\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = cleaned.strip(" .,:;-)('\"")
    cleaned = re.sub(r"\s+", " ", cleaned)
    while True:
        next_cleaned = TRAILING_CONNECTOR_RE.sub("", cleaned).rstrip(" .,:;-)('\"")
        if next_cleaned == cleaned:
            break
        cleaned = next_cleaned
    if UNMATCHED_BRACKET_TEXT_RE.search(cleaned):
        cleaned = re.sub(r"[\(\[][^)\]]*$", "", cleaned).rstrip(" .,:;-)('\"")
    if source_kind != "named_result" and cleaned.endswith(")") and "(" not in cleaned:
        cleaned = cleaned.rstrip(")")
    return ARTICLE_RE.sub("", cleaned).strip(" .,:;-)('\"")


def prefers_contextual_inline_term(term: str) -> bool:
    return VARIABLE_TAIL_RE.search(term) is not None


def strip_authorish_result_tail(term: str) -> str:
    parts = [part.strip() for part in term.split(",")]
    if len(parts) < 2:
        return term
    head = parts[0]
    tail = parts[1:]
    if not head or not all(is_authorish_chunk(part) for part in tail):
        return term
    return head


def is_authorish_chunk(chunk: str) -> bool:
    text = chunk.strip()
    if not text:
        return False
    tokens = [
        token
        for token in re.split(r"\s+|&", text)
        if token
    ]
    if not tokens:
        return False
    saw_name = False
    for token in tokens:
        cleaned = token.strip(" .,'\"`()[]")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in {"and", "et", "al"}:
            continue
        if lowered == "ok":
            saw_name = True
            continue
        if cleaned[0].isupper():
            saw_name = True
            continue
        return False
    return saw_name


def is_rejected_card_heading(value: str) -> bool:
    cleaned = clean_heading(value)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered == "selected published titles in this series":
        return True
    if SECTION_STYLE_RE.match(cleaned):
        return True
    return bool(STRUCTURAL_TERM_PREFIX_RE.match(lowered))


def symbol_heavy_term(term: str) -> bool:
    letters = sum(1 for char in term if char.isalpha())
    non_space = sum(1 for char in term if not char.isspace())
    if non_space == 0:
        return True
    return letters < max(2, non_space // 3)


def first_sentence_of(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    return parts[0].strip()


def render_reader_plain_text(rows: list[dict[str, Any]], book_title: str) -> str:
    lines = [f"# {book_title}", ""]
    current_chapter = None
    current_title = None
    for row in rows:
        chapter_key = (row["chapter_number"], row["chapter_title"])
        if chapter_key != current_chapter:
            current_chapter = chapter_key
            current_title = None
            if lines[-1] != "":
                lines.append("")
            chapter_label = format_chapter_label(
                chapter_number=row["chapter_number"],
                chapter_title=row["chapter_title"],
            )
            lines.extend([f"## {chapter_label}", ""])
        if row["title_path"] != current_title:
            current_title = row["title_path"]
            section_title = str(row["title_path"]).split(">")[-1].strip()
            if section_title and section_title != row["chapter_title"]:
                lines.extend([f"### {section_title}", ""])
        lines.extend([str(row["reader_text"]), ""])
    return "\n".join(lines).strip() + "\n"


def render_discoflash_export(cards: list[dict[str, Any]], book_title: str) -> str:
    lines = [
        "[definition_matching]",
        f"prompt=Match each term to its definition from {book_title}.",
        "",
        "[terms]",
    ]
    lines.extend(discoflash_export_value(str(card["term"])) for card in cards)
    lines.extend(["", "[definitions]"])
    lines.extend(discoflash_export_value(str(card["definition"])) for card in cards)
    return "\n".join(lines).strip() + "\n"


def validate_discoflash_export(text: str) -> dict[str, Any]:
    parser_path = Path("/Users/kogaryu/dev/discoflash/src/app/tools/definition_matching/import_parser.py")
    if parser_path.exists():
        spec = importlib.util.spec_from_file_location("discoflash_definition_matching_import_parser", parser_path)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.parse_definition_matching_block(text)
            errors = list(getattr(result, "errors", []))
            warnings = list(getattr(result, "warnings", []))
            return {
                "errors": errors,
                "status": "fail" if errors else "pass",
                "warnings": warnings,
            }
    required_sections = ["[definition_matching]", "[terms]", "[definitions]"]
    missing = [section for section in required_sections if section not in text]
    return {
        "errors": [f"missing section {section}" for section in missing],
        "status": "fail" if missing else "pass",
        "warnings": [],
    }


def discoflash_export_value(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def resolve_built_book(shelf_root: Path, identifier: str) -> tuple[Path, dict[str, Any]]:
    index_path = shelf_root / DEFAULT_INDEX
    if not index_path.exists():
        raise ValueError(f"study materials not built in {shelf_root}; run `wiki study build` first")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    candidates = [
        entry
        for entry in index["books"]
        if str(entry.get("status")) in MATERIALIZED_STATUSES
    ]
    resolved = resolve_index_book(candidates, identifier)
    book_root = shelf_root / str(resolved["document_id"])
    manifest_path = book_root / DEFAULT_BOOK_MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"missing book manifest for {resolved['document_id']}: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return book_root, manifest


def resolve_index_book(books: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    cleaned = identifier.strip()
    normalized = normalize_name(cleaned)
    matches = [
        book
        for book in books
        if cleaned == str(book["document_id"])
        or normalized == normalize_name(str(book["document_id"]))
        or normalized == normalize_name(str(book["book_title"]))
        or normalized == normalize_name(PurePosixPath(str(book.get("note_path") or "")).stem)
    ]
    if not matches:
        raise ValueError(f"unknown book identifier: {identifier}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous book identifier: {identifier}")
    return matches[0]


def select_index_books(books: list[dict[str, Any]], *, identifier: str | None, export_all: bool) -> list[dict[str, Any]]:
    if export_all:
        return [dict(book) for book in books]
    assert identifier is not None
    return [resolve_index_book(books, identifier)]


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ValueError(f"missing study artifact: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def jsonl_text(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def select_maintained_books(books: list[dict[str, Any]], *, identifier: str | None) -> list[dict[str, Any]]:
    if identifier is None:
        return books
    return [resolve_maintained_book(books, identifier)]


def resolve_maintained_book(books: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    cleaned = identifier.strip()
    normalized = normalize_name(cleaned)
    matches = [
        book
        for book in books
        if cleaned == str(book["document_id"])
        or normalized == normalize_name(str(book["document_id"]))
        or normalized == normalize_name(str(book["title"]))
        or normalized == normalize_name(PurePosixPath(str(book["path"])).stem)
    ]
    if not matches:
        raise ValueError(f"unknown maintained book identifier: {identifier}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous maintained book identifier: {identifier}")
    return dict(matches[0])


def resolve_inventory_book(books: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    cleaned = identifier.strip()
    normalized = normalize_name(cleaned)
    matches = [
        book
        for book in books
        if cleaned == str(book["document_id"])
        or normalized == normalize_name(str(book["document_id"]))
        or normalized == normalize_name(str(book["book_title"]))
        or normalized == normalize_name(PurePosixPath(str(book.get("note_path") or "")).stem)
    ]
    if not matches:
        raise ValueError(f"unknown study book identifier: {identifier}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous study book identifier: {identifier}")
    return dict(matches[0])


def discover_unmatched_extract_roots(source_root: Path, inventory_books: list[dict[str, Any]]) -> list[str]:
    matched = {str(book["document_id"]) for book in inventory_books}
    matched_roots = {str(Path(str(book["extract_root"])).resolve()) for book in inventory_books if book.get("extract_root")}
    roots: list[str] = []
    if not source_root.is_dir():
        return roots
    for child in sorted(source_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        if child.name in matched or str(child.resolve()) in matched_roots:
            continue
        if not looks_like_extract_root(child):
            continue
        roots.append(str(child))
    return roots


def discover_extract_candidate_roots(source_root: Path) -> list[Path]:
    if not source_root.is_dir():
        return []
    return [
        child
        for child in sorted(source_root.iterdir(), key=lambda item: item.name)
        if child.is_dir() and looks_like_extract_candidate_root(child)
    ]


def looks_like_extract_candidate_root(path: Path) -> bool:
    if looks_like_extract_root(path):
        return True
    return (
        (path / "manifests").exists()
        or (path / "chapter_json").exists()
        or (path / "normalized_markdown").exists()
        or (path / "raw_marker").exists()
    )


def inventory_sort_key(book: dict[str, Any]) -> tuple[int, str, str]:
    status_rank = {
        "ready": 0,
        "partial": 1,
        "no_valid_chapters": 2,
        "missing_extract": 3,
    }
    return (
        status_rank.get(str(book.get("status")), 9),
        normalize_name(str(book.get("book_title") or "")),
        str(book.get("document_id") or ""),
    )


def note_title_or_fallback(source_note: dict[str, Any] | None, fallback: str) -> str:
    if source_note is not None:
        return str(source_note["title"])
    return normalize_display_title(fallback)


def select_app_title(
    *,
    source_note: dict[str, Any] | None,
    manifest_title: str | None,
    fallback_name: str,
) -> tuple[str, str]:
    source_note_title = "" if source_note is None else str(source_note.get("title") or "")
    if source_note_title and raw_title_is_app_ready(source_note_title):
        return normalize_display_title(source_note_title), "source_note"
    if manifest_title and normalized_title_is_app_ready(manifest_title):
        return normalize_display_title(manifest_title), "book_manifest"
    fallback_title = normalize_display_title(fallback_name)
    if fallback_title:
        return fallback_title, "directory_name"
    if manifest_title:
        return normalize_display_title(manifest_title), "book_manifest"
    if source_note_title:
        return normalize_display_title(source_note_title), "source_note"
    return normalize_display_title(fallback_name), "directory_name"


def raw_title_is_app_ready(value: str) -> bool:
    cleaned = clean_heading(value).replace("_", " ").strip()
    if not cleaned:
        return False
    if RAW_BAD_TITLE_RE.search(cleaned):
        return False
    return cleaned != cleaned.lower()


def normalized_title_is_app_ready(value: str) -> bool:
    cleaned = normalize_display_title(value)
    if not cleaned:
        return False
    return not RAW_BAD_TITLE_RE.search(cleaned)


def probe_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    status_rank = {
        "good_candidate": 0,
        "partial_candidate": 1,
        "no_match": 2,
    }
    return (
        status_rank.get(str(candidate["status"]), 9),
        -int(candidate["matched_book_count"]),
        -int(candidate["ready_book_count"]),
        int(candidate["partial_book_count"]) + int(candidate["no_valid_book_count"]),
        int(candidate["unmatched_extract_root_count"]) + int(candidate["missing_book_count"]),
        str(candidate["candidate_path"]),
    )


def looks_like_extract_root(path: Path) -> bool:
    manifests_dir = path / "manifests"
    markdown_dir = path / "normalized_markdown"
    if not manifests_dir.is_dir() or not markdown_dir.is_dir():
        return False
    if (manifests_dir / "book.json").exists():
        return True
    if any((path / "chapter_json").glob("ch_*/chapter.json")):
        return True
    if any(path.glob("ch_*/chapter.json")):
        return True
    return any(markdown_dir.glob("ch_*/chapter.md"))


def looks_like_term_candidate(term: str) -> bool:
    cleaned = clean_heading(term).strip(" .:-")
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if is_structural_card_term(lowered):
        return False
    if SECTION_STYLE_RE.match(cleaned):
        return False
    if PURE_DIGITS_RE.fullmatch(cleaned):
        return False
    if SINGLE_LETTER_RE.fullmatch(cleaned):
        return False
    if len(cleaned.split()) > 8:
        return False
    if len(cleaned.split()) > 1 and cleaned.split()[0].lower().endswith("ing"):
        return False
    return True


def looks_like_named_result_candidate(term: str) -> bool:
    cleaned = clean_heading(term).strip(" .:-")
    if not cleaned:
        return False
    if is_structural_card_term(cleaned):
        return False
    if SECTION_STYLE_RE.match(cleaned):
        return False
    if PURE_DIGITS_RE.fullmatch(cleaned):
        return False
    if SINGLE_LETTER_RE.fullmatch(cleaned):
        return False
    if len(cleaned.split()) > 12:
        return False
    if len(cleaned) > 96:
        return False
    if cleaned.lower().endswith((" theorem", " lemma", " proposition", " corollary")) and len(cleaned.split()) == 1:
        return False
    return True


def is_structural_card_term(term: str) -> bool:
    cleaned = clean_heading(term).strip(" .:-")
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if STRUCTURAL_TERM_PREFIX_RE.match(lowered):
        return True
    return bool(STRUCTURAL_TERM_RE.fullmatch(lowered))


def is_display_quality_card_term(term: str, *, source_kind: str | None = None) -> bool:
    cleaned = clean_heading(term).strip(" .:-")
    if not cleaned:
        return False
    if is_structural_card_term(cleaned):
        return False
    if source_kind == "inline_definition":
        return normalize_inline_card_term(cleaned) is not None
    if source_kind in {"named_theorem", "named_lemma", "named_proposition", "named_corollary"}:
        return normalize_named_card_term(cleaned) is not None
    return looks_like_term_candidate(cleaned) and not symbol_heavy_term(cleaned)


def format_chapter_label(*, chapter_number: int | None, chapter_title: str) -> str:
    title = normalize_chapter_title(chapter_title, chapter_number=chapter_number)
    if chapter_number is None:
        return title
    chapter_prefix = f"Chapter {chapter_number}"
    if normalize_name(title) == normalize_name(chapter_prefix):
        return chapter_prefix
    if re.match(rf"^chapter\s+{chapter_number}\b", title, flags=re.IGNORECASE):
        return title
    return f"{chapter_prefix}: {title}"


def ordinal_label(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def uppercase_roman_numerals(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        return token.upper() if len(token) <= 4 else token

    return re.sub(r"\b[ivxlcdm]{1,4}\b", repl, text, flags=re.IGNORECASE)


def smart_title_case(text: str) -> str:
    small_words = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "the", "to", "vs", "via"}
    words = text.split()
    titled: list[str] = []
    last_index = len(words) - 1
    for index, word in enumerate(words):
        lowered = word.lower()
        if index not in {0, last_index} and lowered in small_words:
            titled.append(lowered)
            continue
        if word.isupper():
            titled.append(word)
            continue
        titled.append(word[:1].upper() + word[1:].lower())
    return " ".join(titled)


def validate_shelf(shelf: str) -> str:
    normalized = shelf.strip().lower()
    if normalized != DEFAULT_STUDY_SHELF:
        raise ValueError("study pipeline currently supports only the math shelf")
    return normalized


def validate_view(view: str) -> str:
    normalized = view.strip().lower()
    if normalized not in STUDY_VIEWS:
        raise ValueError(f"unknown study view: {view}")
    return normalized


def validate_target(target: str) -> str:
    normalized = target.strip().lower()
    if normalized not in STUDY_TARGETS:
        raise ValueError(f"unknown study export target: {target}")
    return normalized


def validate_selection(selection: str) -> str:
    normalized = selection.strip().lower()
    if normalized not in STUDY_SELECTIONS:
        raise ValueError(f"unknown study selection: {selection}")
    return normalized
