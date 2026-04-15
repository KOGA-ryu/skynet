from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import json
import re
from typing import Any


REQUIRED_KEYS = {"bundle_id", "created_at_utc", "targets", "rationale", "backup_manifest"}


def validate_patch_bundle(path: Path, *, wiki_root: Path | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    missing = sorted(REQUIRED_KEYS - set(payload))
    targets = payload.get("targets", [])
    errors: list[str] = []
    if missing:
        errors.append(f"missing required keys: {', '.join(missing)}")
    if not isinstance(targets, list) or not targets:
        errors.append("targets must be a non-empty list")
    if not payload.get("backup_manifest"):
        errors.append("backup_manifest is required before applying a patch bundle")
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            errors.append(f"target {index} must be an object")
            continue
        if not target.get("path"):
            errors.append(f"target {index} missing path")
        if not target.get("reason"):
            errors.append(f"target {index} missing reason")
        if target.get("type") == "replace_link_target":
            validate_replace_link_target(index, target, errors, wiki_root=wiki_root)
        elif target.get("type") == "replace_markdown_link":
            validate_replace_markdown_link(index, target, errors, wiki_root=wiki_root)
        elif target.get("type") == "create_markdown_stub":
            validate_create_markdown_stub(index, target, errors, wiki_root=wiki_root)
    return {
        "path": str(path),
        "valid": not errors,
        "errors": errors,
        "target_count": len(targets) if isinstance(targets, list) else 0,
    }


def apply_patch_bundle(
    path: Path,
    *,
    wiki_root: Path,
    backup_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    validation = validate_patch_bundle(path, wiki_root=wiki_root)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))

    targets = payload["targets"]
    supported = {"replace_link_target", "replace_markdown_link", "create_markdown_stub"}
    unsupported = sorted({target.get("type", "<missing>") for target in targets if target.get("type") not in supported})
    if unsupported:
        raise ValueError(f"unsupported patch target types: {', '.join(unsupported)}")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in targets:
        if target.get("type") in {"replace_link_target", "replace_markdown_link"}:
            grouped[str(target["source_path"])].append(target)

    bundle_id = str(payload["bundle_id"])
    safe_bundle_id = safe_path_component(bundle_id)
    backup_root = backup_dir / safe_bundle_id
    file_summaries: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []

    for rel_path in sorted(grouped):
        source = wiki_root / rel_path
        original_bytes = source.read_bytes()
        original_text = original_bytes.decode("utf-8", errors="surrogateescape")
        lines = original_text.splitlines(keepends=True)
        replacements = 0
        for target in sorted(grouped[rel_path], key=lambda item: item["line"]):
            line_index = int(target["line"]) - 1
            if target.get("type") == "replace_markdown_link":
                old_label = str(target["old_label"])
                old_target = str(target["old_target"])
                new_label = str(target["new_label"])
                new_target = str(target["new_target"])
                if not line_has_markdown_link(lines[line_index], old_label, old_target):
                    raise ValueError(
                        f"stale target at {rel_path}:{target['line']}: [{old_label}]({old_target})"
                    )
                lines[line_index] = replace_markdown_link(
                    lines[line_index],
                    old_label=old_label,
                    old_target=old_target,
                    new_label=new_label,
                    new_target=new_target,
                )
            else:
                old_target = str(target["old_target"])
                new_target = str(target["new_target"])
                if not line_has_link_target(lines[line_index], old_target):
                    raise ValueError(f"stale target at {rel_path}:{target['line']}: {old_target}")
                lines[line_index] = replace_link_target(lines[line_index], old_target, new_target)
            replacements += 1

        new_text = "".join(lines)
        new_bytes = new_text.encode("utf-8", errors="surrogateescape")
        file_summary = {
            "backup_path": str((backup_root / rel_path).as_posix()),
            "new_sha256": sha256(new_bytes).hexdigest(),
            "old_sha256": sha256(original_bytes).hexdigest(),
            "path": rel_path,
            "replacement_count": replacements,
            "would_change": new_bytes != original_bytes,
        }
        file_summaries.append(file_summary)

        if dry_run:
            continue

        backup_path = backup_root / rel_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(original_bytes)
        source.write_bytes(new_bytes)
        manifests.append(file_summary)

    for target in sorted(
        (item for item in targets if item.get("type") == "create_markdown_stub"),
        key=lambda item: item["path"],
    ):
        rel_path = str(target["path"])
        source = wiki_root / rel_path
        if source.exists():
            raise ValueError(f"refusing to overwrite existing file: {rel_path}")
        body = str(target["body"])
        new_bytes = body.encode("utf-8")
        file_summary = {
            "action": "create",
            "backup_path": None,
            "new_sha256": sha256(new_bytes).hexdigest(),
            "old_sha256": None,
            "path": rel_path,
            "replacement_count": 0,
            "would_change": True,
        }
        file_summaries.append(file_summary)
        if dry_run:
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(new_bytes)
        manifests.append(file_summary)

    manifest_path = backup_root / "manifest.json"
    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "applied_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                    "bundle_id": bundle_id,
                    "bundle_path": str(path),
                    "files": manifests,
                    "wiki_root": str(wiki_root),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    return {
        "backup_dir": str(backup_root),
        "bundle_id": bundle_id,
        "dry_run": dry_run,
        "file_count": len(file_summaries),
        "files": file_summaries,
        "manifest_path": str(manifest_path),
        "target_count": len(targets),
        "would_write": not dry_run,
    }


def safe_path_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "bundle"


def line_has_link_target(line: str, target: str) -> bool:
    return f"]({target})" in line


def replace_link_target(line: str, old_target: str, new_target: str) -> str:
    return line.replace(f"]({old_target})", f"]({new_target})", 1)


def line_has_markdown_link(line: str, label: str, target: str) -> bool:
    return f"[{label}]({target})" in line


def replace_markdown_link(
    line: str,
    *,
    old_label: str,
    old_target: str,
    new_label: str,
    new_target: str,
) -> str:
    return line.replace(f"[{old_label}]({old_target})", f"[{new_label}]({new_target})", 1)


def validate_replace_link_target(
    index: int,
    target: dict[str, Any],
    errors: list[str],
    *,
    wiki_root: Path | None,
) -> None:
    required = {
        "category",
        "label",
        "line",
        "new_target",
        "old_target",
        "source_path",
    }
    missing = sorted(key for key in required if not target.get(key))
    if missing:
        errors.append(f"target {index} missing replace_link_target keys: {', '.join(missing)}")
        return
    if not str(target["new_target"]).startswith("dev://"):
        errors.append(f"target {index} new_target must be a dev:// reference")
    if not isinstance(target["line"], int) or target["line"] < 1:
        errors.append(f"target {index} line must be a positive integer")
        return
    if wiki_root is None:
        return
    source = wiki_root / str(target["source_path"])
    if not source.exists():
        errors.append(f"target {index} source_path does not exist: {target['source_path']}")
        return
    lines = source.read_text(errors="replace").splitlines()
    if target["line"] > len(lines):
        errors.append(f"target {index} line {target['line']} is past end of {target['source_path']}")
        return
    if not line_has_link_target(lines[target["line"] - 1], str(target["old_target"])):
        errors.append(
            f"target {index} old_target not found at {target['source_path']}:{target['line']}"
        )


def validate_replace_markdown_link(
    index: int,
    target: dict[str, Any],
    errors: list[str],
    *,
    wiki_root: Path | None,
) -> None:
    required = {
        "line",
        "new_label",
        "new_target",
        "old_label",
        "old_target",
        "source_path",
    }
    missing = sorted(key for key in required if not target.get(key))
    if missing:
        errors.append(f"target {index} missing replace_markdown_link keys: {', '.join(missing)}")
        return
    if not isinstance(target["line"], int) or target["line"] < 1:
        errors.append(f"target {index} line must be a positive integer")
        return
    if wiki_root is None:
        return
    source = wiki_root / str(target["source_path"])
    if not source.exists():
        errors.append(f"target {index} source_path does not exist: {target['source_path']}")
        return
    lines = source.read_text(errors="replace").splitlines()
    if target["line"] > len(lines):
        errors.append(f"target {index} line {target['line']} is past end of {target['source_path']}")
        return
    if not line_has_markdown_link(
        lines[target["line"] - 1],
        str(target["old_label"]),
        str(target["old_target"]),
    ):
        errors.append(
            f"target {index} old markdown link not found at {target['source_path']}:{target['line']}"
        )
        return
    if not target_exists_after_replacement(
        wiki_root=wiki_root,
        source_path=str(target["source_path"]),
        new_target=str(target["new_target"]),
    ):
        errors.append(f"target {index} new_target does not resolve: {target['new_target']}")


def validate_create_markdown_stub(
    index: int,
    target: dict[str, Any],
    errors: list[str],
    *,
    wiki_root: Path | None,
) -> None:
    required = {"body", "inbound_references", "path", "title"}
    missing = sorted(key for key in required if not target.get(key))
    if missing:
        errors.append(f"target {index} missing create_markdown_stub keys: {', '.join(missing)}")
        return
    path = str(target["path"])
    if not path.endswith(".md"):
        errors.append(f"target {index} path must end with .md")
    if path.startswith("/") or ".." in Path(path).parts:
        errors.append(f"target {index} path must be wiki-relative")
    body = str(target["body"])
    if not body.startswith(f"# {target['title']}"):
        errors.append(f"target {index} body must start with title heading")
    if not isinstance(target["inbound_references"], list) or not target["inbound_references"]:
        errors.append(f"target {index} inbound_references must be a non-empty list")
    if wiki_root is not None and (wiki_root / path).exists():
        errors.append(f"target {index} path already exists: {path}")


def target_exists_after_replacement(*, wiki_root: Path, source_path: str, new_target: str) -> bool:
    lowered = new_target.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("mailto:")
        or lowered.startswith("obsidian:")
        or lowered.startswith("dev://")
        or lowered.startswith("#")
    ):
        return True
    clean = new_target.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean:
        return True
    if clean.startswith("/"):
        candidate = wiki_root / clean.lstrip("/")
    else:
        candidate = wiki_root / Path(source_path).parent / clean
    return candidate.resolve().is_relative_to(wiki_root.resolve()) and candidate.exists()
