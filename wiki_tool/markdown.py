from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re
from urllib.parse import unquote

from wiki_tool.ids import slug, span_id
from wiki_tool.models import Link, Span


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int


def title_from_markdown(path: str, text: str) -> str:
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            return clean_heading(match.group(2))
    return PurePosixPath(path).stem.replace("_", " ").replace("-", " ").title()


def infer_kind(path: str) -> str:
    parts = PurePosixPath(path).parts
    if path == "AGENTS.md":
        return "operating_schema"
    if path == "index.md":
        return "index"
    if "sources" in parts:
        return "source_note"
    if "concepts" in parts:
        return "concept"
    if "methods" in parts:
        return "method"
    if "templates" in parts:
        return "template"
    if "projects" in parts:
        return "project_note"
    return "note"


def parse_spans(*, doc: str, path: str, text: str) -> list[Span]:
    lines = text.splitlines()
    headings = find_headings(text)
    spans: list[Span] = []
    ordinal = 0

    if not headings:
        body = text.strip()
        if body:
            spans.append(
                Span(
                    span_id=span_id(doc, "__document__", ordinal),
                    doc_id=doc,
                    path=path,
                    heading="Document",
                    level=0,
                    start_line=1,
                    end_line=len(lines),
                    ordinal=ordinal,
                    text=body,
                )
            )
        return spans

    first = headings[0]
    if first.line > 1:
        intro = "\n".join(lines[: first.line - 1]).strip()
        if intro:
            spans.append(
                Span(
                    span_id=span_id(doc, "__intro__", ordinal),
                    doc_id=doc,
                    path=path,
                    heading="Intro",
                    level=0,
                    start_line=1,
                    end_line=first.line - 1,
                    ordinal=ordinal,
                    text=intro,
                )
            )
            ordinal += 1

    heading_stack: list[Heading] = []
    for index, heading in enumerate(headings):
        while heading_stack and heading_stack[-1].level >= heading.level:
            heading_stack.pop()
        heading_stack.append(heading)
        next_line = headings[index + 1].line if index + 1 < len(headings) else len(lines) + 1
        section_text = "\n".join(lines[heading.line - 1 : next_line - 1]).strip()
        heading_path = "/".join(slug(item.title) for item in heading_stack)
        spans.append(
            Span(
                span_id=span_id(doc, heading_path, ordinal),
                doc_id=doc,
                path=path,
                heading=heading.title,
                level=heading.level,
                start_line=heading.line,
                end_line=next_line - 1,
                ordinal=ordinal,
                text=section_text,
            )
        )
        ordinal += 1
    return spans


def find_headings(text: str) -> list[Heading]:
    headings: list[Heading] = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            headings.append(
                Heading(
                    level=len(match.group(1)),
                    title=clean_heading(match.group(2)),
                    line=line_no,
                )
            )
    return headings


def parse_links(
    *,
    doc: str,
    path: str,
    text: str,
    known_paths: set[str],
    title_to_path: dict[str, str] | None = None,
) -> list[Link]:
    links: list[Link] = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in MD_LINK_RE.finditer(line):
            label, raw_target = match.group(1), match.group(2)
            target_path, resolved = resolve_markdown_target(path, raw_target, known_paths)
            links.append(
                Link(
                    source_doc_id=doc,
                    source_path=path,
                    target_raw=raw_target,
                    target_path=target_path,
                    label=label,
                    link_kind="markdown",
                    line=line_no,
                    resolved=resolved,
                )
            )
        for match in WIKI_LINK_RE.finditer(line):
            raw_target = match.group(1)
            label = match.group(2) or raw_target
            target_path, resolved = resolve_wikilink_target(
                raw_target,
                known_paths,
                title_to_path=title_to_path or {},
            )
            links.append(
                Link(
                    source_doc_id=doc,
                    source_path=path,
                    target_raw=raw_target,
                    target_path=target_path,
                    label=label,
                    link_kind="wikilink",
                    line=line_no,
                    resolved=resolved,
                )
            )
    return links


def resolve_markdown_target(
    source_path: str, raw_target: str, known_paths: set[str]
) -> tuple[str | None, bool]:
    target = raw_target.strip()
    if is_external_target(target):
        return target, True
    target = unquote(target.split("#", 1)[0].split("?", 1)[0]).strip()
    if not target:
        return source_path, True
    base = PurePosixPath(source_path).parent
    normalized = str((base / target).as_posix())
    return resolve_path_candidates(normalized, known_paths)


def resolve_wikilink_target(
    raw_target: str,
    known_paths: set[str],
    *,
    title_to_path: dict[str, str] | None = None,
) -> tuple[str | None, bool]:
    target = raw_target.strip()
    if not target:
        return None, False
    if "/" in target or target.endswith(".md"):
        return resolve_path_candidates(target, known_paths)
    normalized = normalize_name(target)
    if title_to_path and normalized in title_to_path:
        return title_to_path[normalized], True
    matches = [
        path
        for path in known_paths
        if normalize_name(PurePosixPath(path).stem) == normalized
        or normalize_name(path) == normalized
    ]
    return (sorted(matches)[0], True) if matches else (None, False)


def resolve_path_candidates(path: str, known_paths: set[str]) -> tuple[str | None, bool]:
    clean = posixpath.normpath(path.replace("\\", "/"))
    if clean == ".":
        clean = ""
    if clean.startswith("./"):
        clean = clean[2:]
    clean = clean.lstrip("/")
    candidates = [clean]
    if not clean.endswith(".md"):
        candidates.append(f"{clean}.md")
        candidates.append(f"{clean}/README.md")
    for candidate in candidates:
        if candidate in known_paths:
            return candidate, True
    return clean, False


def is_external_target(target: str) -> bool:
    lowered = target.lower()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("mailto:")
        or lowered.startswith("obsidian:")
        or lowered.startswith("dev://")
        or lowered.startswith("#")
    )


def clean_heading(value: str) -> str:
    return value.strip().strip("#").strip()


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
