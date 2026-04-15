from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any

from wiki_tool.aliases import (
    DEFAULT_ALIAS_MAP,
    alias_lookup,
    aliases_as_dicts,
    load_alias_entries,
    validate_alias_entries,
)
from wiki_tool.ids import doc_id, digest, symbol_id
from wiki_tool.markdown import infer_kind, normalize_name, parse_links, parse_spans, title_from_markdown
from wiki_tool.models import CatalogAlias, Document, Link, ScanResult, Span, Symbol


DEFAULT_DB = Path("state/catalog.sqlite")
DEFAULT_WIKI_ROOT = Path("/Volumes/wiki")
EXCLUDED_DIRS = {
    ".git",
    ".obsidian",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "@Recycle",
    "@Recently-Snapshot",
    "miniforge3",
    "node_modules",
    "runtime",
    "site-packages",
    "tmp",
}


def scan_wiki(
    root: Path,
    db_path: Path = DEFAULT_DB,
    *,
    alias_map_path: Path | None = None,
) -> ScanResult:
    root = root.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    docs = collect_documents(root)
    known_paths = {doc.path for doc in docs}
    known_targets = known_paths | collect_known_files(root)
    title_to_path = {normalize_name(doc.title): doc.path for doc in docs}
    alias_entries = load_alias_entries(alias_map_path) if alias_map_path else []
    alias_validation = validate_alias_entries(
        alias_entries,
        known_paths=known_paths,
        title_to_path=title_to_path,
    )
    if not alias_validation["valid"]:
        raise ValueError("; ".join(alias_validation["errors"]))
    alias_to_path = alias_lookup(alias_entries)
    aliases_by_path: dict[str, set[str]] = {}
    for alias in alias_entries:
        aliases_by_path.setdefault(alias.target_path, set()).add(alias.alias)
    catalog_aliases = [
        CatalogAlias(
            alias=alias.alias,
            normalized=alias.normalized,
            target_path=alias.target_path,
            reason=alias.reason,
        )
        for alias in alias_entries
    ]
    spans: list[Span] = []
    links: list[Link] = []
    symbols: list[Symbol] = []

    for doc in docs:
        doc_spans = parse_spans(doc=doc.doc_id, path=doc.path, text=doc.text)
        spans.extend(doc_spans)
        links.extend(
            parse_links(
                doc=doc.doc_id,
                path=doc.path,
                text=doc.text,
                known_paths=known_targets,
                title_to_path=title_to_path,
                alias_to_path=alias_to_path,
            )
        )
        symbols.extend(symbols_for_document(doc, doc_spans, aliases_by_path=aliases_by_path))

    broken_links = [link for link in links if not link.resolved]
    run_id = f"scan:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}:{digest(str(root))}"
    write_catalog(
        db_path=db_path,
        root=root,
        run_id=run_id,
        docs=docs,
        spans=spans,
        links=links,
        symbols=symbols,
        aliases=catalog_aliases,
    )
    return ScanResult(
        root=str(root),
        run_id=run_id,
        document_count=len(docs),
        span_count=len(spans),
        link_count=len(links),
        broken_link_count=len(broken_links),
        symbol_count=len(symbols),
    )


def collect_documents(root: Path) -> list[Document]:
    docs: list[Document] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = Path(current) / filename
            if should_exclude(path, root):
                continue
            rel = path.relative_to(root).as_posix()
            data = path.read_bytes()
            text = data.decode("utf-8", errors="replace")
            stat = path.stat()
            docs.append(
                Document(
                    doc_id=doc_id(rel),
                    path=rel,
                    title=title_from_markdown(rel, text),
                    kind=infer_kind(rel),
                    content_hash=f"sha256:{sha256(data).hexdigest()}",
                    byte_size=len(data),
                    modified_ns=stat.st_mtime_ns,
                    text=text,
                )
            )
    return docs


def collect_known_files(root: Path) -> set[str]:
    paths: set[str] = set()
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in filenames:
            path = Path(current) / filename
            if should_exclude(path, root):
                continue
            paths.add(path.relative_to(root).as_posix())
    return paths


def should_exclude(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(part in EXCLUDED_DIRS for part in rel_parts)


def symbols_for_document(
    doc: Document,
    spans: list[Span],
    *,
    aliases_by_path: dict[str, set[str]] | None = None,
) -> list[Symbol]:
    doc_aliases = {
        doc.title,
        PurePosixPath(doc.path).stem.replace("_", " "),
        *(aliases_by_path or {}).get(doc.path, set()),
    }
    symbols = [
        Symbol(
            symbol_id=symbol_id("note", doc.title, doc.path),
            name=doc.title,
            kind=doc.kind,
            path=doc.path,
            doc_id=doc.doc_id,
            span_id=None,
            aliases=tuple(sorted(doc_aliases)),
        )
    ]
    for span in spans:
        if span.heading in {"Intro", "Document"}:
            continue
        symbols.append(
            Symbol(
                symbol_id=symbol_id("heading", span.heading, doc.path, span.span_id),
                name=span.heading,
                kind="heading",
                path=doc.path,
                doc_id=doc.doc_id,
                span_id=span.span_id,
                aliases=(span.heading,),
            )
        )
    return symbols


def write_catalog(
    *,
    db_path: Path,
    root: Path,
    run_id: str,
    docs: list[Document],
    spans: list[Span],
    links: list[Link],
    symbols: list[Symbol],
    aliases: list[CatalogAlias],
) -> None:
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("PRAGMA journal_mode=WAL")
        create_schema(con)
        con.executescript(
            """
            DELETE FROM scan_runs;
            DELETE FROM documents;
            DELETE FROM spans;
            DELETE FROM links;
            DELETE FROM symbols;
            DELETE FROM aliases;
            DELETE FROM documents_fts;
            DELETE FROM spans_fts;
            DELETE FROM symbols_fts;
            """
        )
        con.execute(
            """
            INSERT INTO scan_runs(run_id, scanned_at_utc, root, document_count, span_count, link_count, symbol_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now(UTC).isoformat(timespec="seconds"),
                str(root),
                len(docs),
                len(spans),
                len(links),
                len(symbols),
            ),
        )
        con.executemany(
            """
            INSERT INTO documents(doc_id, path, title, kind, content_hash, byte_size, modified_ns, text)
            VALUES (:doc_id, :path, :title, :kind, :content_hash, :byte_size, :modified_ns, :text)
            """,
            [asdict(doc) for doc in docs],
        )
        con.executemany(
            """
            INSERT INTO spans(span_id, doc_id, path, heading, level, start_line, end_line, ordinal, text)
            VALUES (:span_id, :doc_id, :path, :heading, :level, :start_line, :end_line, :ordinal, :text)
            """,
            [asdict(span) for span in spans],
        )
        con.executemany(
            """
            INSERT INTO links(source_doc_id, source_path, target_raw, target_path, label, link_kind, line, resolved)
            VALUES (:source_doc_id, :source_path, :target_raw, :target_path, :label, :link_kind, :line, :resolved)
            """,
            [{**asdict(link), "resolved": int(link.resolved)} for link in links],
        )
        con.executemany(
            """
            INSERT INTO symbols(symbol_id, name, kind, path, doc_id, span_id, aliases_json)
            VALUES (:symbol_id, :name, :kind, :path, :doc_id, :span_id, :aliases_json)
            """,
            [
                {
                    **asdict(symbol),
                    "aliases_json": json.dumps(symbol.aliases),
                }
                for symbol in symbols
            ],
        )
        con.executemany(
            """
            INSERT INTO aliases(alias, normalized, target_path, reason)
            VALUES (:alias, :normalized, :target_path, :reason)
            """,
            [asdict(alias) for alias in aliases],
        )
        con.executemany(
            "INSERT INTO documents_fts(doc_id, title, path, content) VALUES (?, ?, ?, ?)",
            [(doc.doc_id, doc.title, doc.path, doc.text) for doc in docs],
        )
        con.executemany(
            "INSERT INTO spans_fts(span_id, heading, path, text) VALUES (?, ?, ?, ?)",
            [(span.span_id, span.heading, span.path, span.text) for span in spans],
        )
        con.executemany(
            "INSERT INTO symbols_fts(symbol_id, name, kind, path, aliases) VALUES (?, ?, ?, ?, ?)",
            [
                (symbol.symbol_id, symbol.name, symbol.kind, symbol.path, " ".join(symbol.aliases))
                for symbol in symbols
            ],
        )
        con.commit()


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            run_id TEXT PRIMARY KEY,
            scanned_at_utc TEXT NOT NULL,
            root TEXT NOT NULL,
            document_count INTEGER NOT NULL,
            span_count INTEGER NOT NULL,
            link_count INTEGER NOT NULL,
            symbol_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS spans (
            span_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            path TEXT NOT NULL,
            heading TEXT NOT NULL,
            level INTEGER NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_doc_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            target_raw TEXT NOT NULL,
            target_path TEXT,
            label TEXT NOT NULL,
            link_kind TEXT NOT NULL,
            line INTEGER NOT NULL,
            resolved INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS symbols (
            symbol_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            span_id TEXT,
            aliases_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aliases (
            alias TEXT PRIMARY KEY,
            normalized TEXT NOT NULL UNIQUE,
            target_path TEXT NOT NULL,
            reason TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            doc_id UNINDEXED, title, path, content
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS spans_fts USING fts5(
            span_id UNINDEXED, heading, path, text
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
            symbol_id UNINDEXED, name, kind, path, aliases
        );
        """
    )


def query_catalog(db_path: Path, mode: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    match = fts_query(query)
    if not match:
        return []
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        if mode == "symbol.search":
            rows = con.execute(
                """
                SELECT s.symbol_id, s.name, s.kind, s.path, s.span_id, bm25(symbols_fts) AS rank
                FROM symbols_fts
                JOIN symbols s ON s.symbol_id = symbols_fts.symbol_id
                WHERE symbols_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        elif mode == "span.searchText":
            rows = con.execute(
                """
                SELECT sp.span_id, sp.heading, sp.path, sp.start_line, sp.end_line, snippet(spans_fts, 3, '[', ']', '...', 12) AS snippet,
                       bm25(spans_fts) AS rank
                FROM spans_fts
                JOIN spans sp ON sp.span_id = spans_fts.span_id
                WHERE spans_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT d.doc_id, d.title, d.kind, d.path, snippet(documents_fts, 3, '[', ']', '...', 12) AS snippet,
                       bm25(documents_fts) AS rank
                FROM documents_fts
                JOIN documents d ON d.doc_id = documents_fts.doc_id
                WHERE documents_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        return [dict(row) for row in rows]


def get_headings(db_path: Path, path: str) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT span_id, heading, level, start_line, end_line
            FROM spans
            WHERE path = ? AND level > 0
            ORDER BY ordinal
            """,
            (normalize_catalog_path(path),),
        ).fetchall()
        return [dict(row) for row in rows]


def alias_map_validation(
    db_path: Path,
    *,
    alias_map_path: Path = DEFAULT_ALIAS_MAP,
) -> dict[str, Any]:
    entries = load_alias_entries(alias_map_path)
    known_paths, title_to_path = catalog_paths_and_titles(db_path)
    validation = validate_alias_entries(
        entries,
        known_paths=known_paths,
        title_to_path=title_to_path,
    )
    return {
        **validation,
        "aliases": aliases_as_dicts(entries),
        "path": str(alias_map_path),
    }


def list_aliases(db_path: Path) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        if not table_exists(con, "aliases"):
            return []
        rows = con.execute(
            """
            SELECT alias, normalized, target_path, reason
            FROM aliases
            ORDER BY alias
            """
        ).fetchall()
        return [dict(row) for row in rows]


def resolve_alias_path(db_path: Path, value: str) -> str | None:
    normalized = normalize_name(value)
    if not normalized:
        return None
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        if not table_exists(con, "aliases"):
            return None
        row = con.execute(
            "SELECT target_path FROM aliases WHERE normalized = ?",
            (normalized,),
        ).fetchone()
        return str(row["target_path"]) if row else None


def catalog_paths_and_titles(db_path: Path) -> tuple[set[str], dict[str, str]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT path, title FROM documents ORDER BY path").fetchall()
    paths = {str(row["path"]) for row in rows}
    titles = {normalize_name(str(row["title"])): str(row["path"]) for row in rows}
    return paths, titles


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def find_references(db_path: Path, target: str) -> list[dict[str, Any]]:
    normalized = normalize_catalog_path(target)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        doc = con.execute(
            "SELECT path FROM documents WHERE path = ? OR doc_id = ?",
            (normalized, target),
        ).fetchone()
        if doc:
            target_path = doc["path"]
        else:
            alias_target = resolve_alias_path(db_path, target)
            target_path = alias_target if alias_target else normalized
        rows = con.execute(
            """
            SELECT source_path, target_raw, target_path, label, link_kind, line
            FROM links
            WHERE target_path = ?
            ORDER BY source_path, line
            """,
            (target_path,),
        ).fetchall()
        return [dict(row) for row in rows]


def broken_links(
    db_path: Path,
    *,
    limit: int | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT source_path, target_raw, target_path, label, link_kind, line
            FROM links
            WHERE resolved = 0
            ORDER BY source_path, line
            """
        ).fetchall()
        results = [
            {**dict(row), "category": classify_broken_link(dict(row))}
            for row in rows
        ]
        if category:
            results = [row for row in results if row["category"] == category]
        if limit is not None:
            results = results[:limit]
        return results


def broken_link_categories(db_path: Path) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in broken_links(db_path):
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return [
        {"category": category, "count": count}
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def classify_broken_link(row: dict[str, Any]) -> str:
    raw = str(row["target_raw"]).strip()
    normalized = raw.split("#", 1)[0].split("?", 1)[0].strip()
    lowered = normalized.lower()
    if is_template_placeholder(row, normalized):
        return "template_placeholder"
    if lowered.startswith("dev://"):
        return "logical_dev_reference"
    if lowered.startswith("/users/") or lowered.startswith("~/"):
        return "local_absolute_path"
    if len(normalized) >= 3 and normalized[1:3] in {":\\", ":/"}:
        return "local_absolute_path"
    if lowered.startswith("/volumes/"):
        return "mounted_absolute_path"
    suffix = PurePosixPath(normalized).suffix.lower()
    if suffix and suffix != ".md":
        return "missing_non_markdown_file"
    return "missing_markdown_note"


def is_template_placeholder(row: dict[str, Any], normalized_target: str) -> bool:
    source_path = str(row.get("source_path", ""))
    raw = str(row.get("target_raw", ""))
    if source_path.startswith("templates/"):
        return True
    return "<" in raw and ">" in raw or "<" in normalized_target and ">" in normalized_target


def gaps(db_path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        no_heading = con.execute(
            "SELECT path, title, kind FROM documents WHERE doc_id NOT IN (SELECT DISTINCT doc_id FROM spans WHERE level > 0) ORDER BY path"
        ).fetchall()
        inbound = con.execute(
            """
            SELECT d.path, COUNT(l.id) AS inbound_count
            FROM documents d
            LEFT JOIN links l ON l.target_path = d.path
            GROUP BY d.path
            HAVING inbound_count = 0
            ORDER BY d.path
            """
        ).fetchall()
        kinds = con.execute(
            "SELECT kind, COUNT(*) AS count FROM documents GROUP BY kind ORDER BY count DESC, kind"
        ).fetchall()
        return {
            "notes_without_headings": [dict(row) for row in no_heading],
            "notes_without_inbound_links": [dict(row) for row in inbound],
            "document_kinds": [dict(row) for row in kinds],
        }


def open_path(
    db_path: Path,
    identifier: str,
    *,
    platform: str,
    mac_root: str,
    windows_root: str,
) -> dict[str, str]:
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT path FROM documents WHERE doc_id = ? OR path = ?
            UNION
            SELECT path FROM spans WHERE span_id = ?
            UNION
            SELECT path FROM symbols WHERE symbol_id = ?
            LIMIT 1
            """,
            (identifier, normalize_catalog_path(identifier), identifier, identifier),
        ).fetchone()
        if row is None and table_exists(con, "aliases"):
            row = con.execute(
                """
                SELECT target_path AS path
                FROM aliases
                WHERE normalized = ?
                LIMIT 1
                """,
                (normalize_name(identifier),),
            ).fetchone()
    if row is None:
        raise KeyError(f"No catalog object found for {identifier!r}")
    rel = row["path"]
    if platform == "windows":
        root = windows_root.rstrip("\\/")
        return {"path": f"{root}\\{rel.replace('/', '\\')}", "relative_path": rel}
    root = mac_root.rstrip("/")
    return {"path": f"{root}/{rel}", "relative_path": rel}


def audit_summary(db_path: Path) -> dict[str, Any]:
    broken = broken_links(db_path)
    actionable_broken = [row for row in broken if row["category"] != "template_placeholder"]
    gap_data = gaps(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        run = con.execute("SELECT * FROM scan_runs LIMIT 1").fetchone()
        counts = con.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM documents) AS documents,
              (SELECT COUNT(*) FROM spans) AS spans,
              (SELECT COUNT(*) FROM links) AS links,
              (SELECT COUNT(*) FROM symbols) AS symbols
            """
        ).fetchone()
    return {
        "scan_run": dict(run) if run else None,
        "counts": dict(counts) if counts else {},
        "broken_links": len(actionable_broken),
        "broken_link_categories": broken_link_categories(db_path),
        "excluded_links": len(broken) - len(actionable_broken),
        "notes_without_headings": len(gap_data["notes_without_headings"]),
        "notes_without_inbound_links": len(gap_data["notes_without_inbound_links"]),
        "status": "fail" if actionable_broken else "pass",
    }


def fts_query(query: str) -> str:
    import re

    tokens = re.findall(r"[A-Za-z0-9_./-]+", query)
    return " ".join(tokens)


def normalize_catalog_path(path: str) -> str:
    path = path.strip().replace("\\", "/")
    if path.startswith("/"):
        parts = PurePosixPath(path).parts
        if "wiki" in parts:
            wiki_index = parts.index("wiki")
            path = "/".join(parts[wiki_index + 1 :])
    return path.lstrip("./")
