from __future__ import annotations

from hashlib import sha256
import re


def digest(value: str | bytes, length: int = 16) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return sha256(data).hexdigest()[:length]


def doc_id(path: str) -> str:
    return f"doc:{digest(path)}"


def span_id(doc: str, heading_path: str, ordinal: int) -> str:
    return f"span:{digest(f'{doc}|{heading_path}|{ordinal}')}"


def symbol_id(kind: str, name: str, path: str, span: str | None = None) -> str:
    return f"sym:{digest(f'{kind}|{name}|{path}|{span or ''}')}"


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "untitled"
