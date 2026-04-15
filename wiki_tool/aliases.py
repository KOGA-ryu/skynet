from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from wiki_tool.markdown import normalize_name


DEFAULT_ALIAS_MAP = Path("alias_maps/wiki_aliases.json")


@dataclass(frozen=True)
class AliasEntry:
    alias: str
    normalized: str
    target_path: str
    reason: str


def load_alias_entries(path: Path = DEFAULT_ALIAS_MAP) -> list[AliasEntry]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    entries = payload.get("aliases", [])
    if not isinstance(entries, list):
        raise ValueError("aliases must be a list")
    aliases: list[AliasEntry] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"alias entry {index} must be an object")
        alias = str(raw.get("alias", "")).strip()
        target_path = str(raw.get("target_path", "")).strip().replace("\\", "/")
        reason = str(raw.get("reason", "")).strip()
        normalized = normalize_name(alias)
        if not alias:
            raise ValueError(f"alias entry {index} missing alias")
        if not normalized:
            raise ValueError(f"alias entry {index} has empty normalized alias")
        if not target_path:
            raise ValueError(f"alias entry {index} missing target_path")
        if target_path.startswith("/") or ".." in Path(target_path).parts:
            raise ValueError(f"alias entry {index} target_path must be wiki-relative")
        aliases.append(
            AliasEntry(
                alias=alias,
                normalized=normalized,
                target_path=target_path,
                reason=reason,
            )
        )
    return aliases


def alias_lookup(entries: list[AliasEntry]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entry in entries:
        existing = lookup.get(entry.normalized)
        if existing and existing != entry.target_path:
            raise ValueError(
                f"conflicting alias {entry.alias!r}: {existing} vs {entry.target_path}"
            )
        lookup[entry.normalized] = entry.target_path
    return lookup


def validate_alias_entries(
    entries: list[AliasEntry],
    *,
    known_paths: set[str],
    title_to_path: dict[str, str],
) -> dict[str, Any]:
    errors: list[str] = []
    seen: dict[str, AliasEntry] = {}
    for entry in entries:
        if entry.normalized in seen and seen[entry.normalized].target_path != entry.target_path:
            errors.append(
                "conflicting alias "
                f"{entry.alias!r}: {seen[entry.normalized].target_path} vs {entry.target_path}"
            )
        seen[entry.normalized] = entry
        if entry.target_path not in known_paths:
            errors.append(f"alias {entry.alias!r} target does not exist: {entry.target_path}")
        title_target = title_to_path.get(entry.normalized)
        if title_target and title_target != entry.target_path:
            errors.append(
                f"alias {entry.alias!r} conflicts with document title at {title_target}"
            )
    return {
        "alias_count": len(entries),
        "errors": errors,
        "valid": not errors,
    }


def aliases_as_dicts(entries: list[AliasEntry]) -> list[dict[str, str]]:
    return [
        {
            "alias": entry.alias,
            "normalized": entry.normalized,
            "reason": entry.reason,
            "target_path": entry.target_path,
        }
        for entry in entries
    ]
