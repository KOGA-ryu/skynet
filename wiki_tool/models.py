from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    doc_id: str
    path: str
    title: str
    kind: str
    content_hash: str
    byte_size: int
    modified_ns: int
    text: str


@dataclass(frozen=True)
class Span:
    span_id: str
    doc_id: str
    path: str
    heading: str
    level: int
    start_line: int
    end_line: int
    ordinal: int
    text: str


@dataclass(frozen=True)
class Link:
    source_doc_id: str
    source_path: str
    target_raw: str
    target_path: str | None
    label: str
    link_kind: str
    line: int
    resolved: bool


@dataclass(frozen=True)
class Symbol:
    symbol_id: str
    name: str
    kind: str
    path: str
    doc_id: str
    span_id: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ScanResult:
    root: str
    run_id: str
    document_count: int
    span_count: int
    link_count: int
    broken_link_count: int
    symbol_count: int
