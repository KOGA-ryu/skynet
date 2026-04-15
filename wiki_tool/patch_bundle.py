from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import json
import re
from typing import Any

from wiki_tool.catalog import latest_scan_run


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
    catalog_db: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    validation = validate_patch_bundle(path, wiki_root=wiki_root)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    preflight = patch_write_preflight(
        payload,
        wiki_root=wiki_root,
        catalog_db=catalog_db,
        require_match=not dry_run,
    )

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
        "preflight": preflight,
        "target_count": len(targets),
        "would_write": not dry_run,
    }


def report_patch_bundle(path: Path, *, wiki_root: Path | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if isinstance(payload.get("targets"), list):
        return report_bundle_payload(path, payload, wiki_root=wiki_root)
    if isinstance(payload.get("files"), list):
        return report_manifest_payload(path, payload, wiki_root=wiki_root)
    raise ValueError("path is neither a patch bundle nor an applied manifest")


def rollback_patch_bundle(
    manifest_path: Path,
    *,
    wiki_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text())
    if not isinstance(payload.get("files"), list):
        raise ValueError("rollback requires an applied manifest with a files list")

    report = report_manifest_payload(manifest_path, payload, wiki_root=wiki_root)
    blockers = [item for item in report["files"] if str(item["status"]).startswith("blocked")]
    result = {
        "blocked_count": len(blockers),
        "bundle_id": payload.get("bundle_id"),
        "dry_run": dry_run,
        "file_count": len(report["files"]),
        "manifest_path": str(manifest_path),
        "rolled_back": False,
        "actions": [],
    }
    if blockers:
        result["blocked"] = blockers
        if dry_run:
            return result
        details = "; ".join(f"{item.get('path')}: {item['status']}" for item in blockers[:5])
        raise ValueError(f"rollback blocked: {details}")

    for item in report["files"]:
        action = item["action"]
        status = item["status"]
        rel_path = str(item["path"])
        if status == "already_missing":
            result["actions"].append(
                {"action": "delete", "changed": False, "path": rel_path, "status": status}
            )
            continue
        if status != "ready":
            result["actions"].append(
                {"action": action, "changed": False, "path": rel_path, "status": status}
            )
            continue

        target = safe_wiki_target(wiki_root, rel_path)
        if action == "create":
            result["actions"].append(
                {"action": "delete", "changed": True, "path": rel_path, "status": status}
            )
            if not dry_run:
                target.unlink()
            continue

        backup_path = resolve_backup_path(item["backup_path"], manifest_path)
        result["actions"].append(
            {"action": "restore", "changed": True, "path": rel_path, "status": status}
        )
        if not dry_run:
            target.write_bytes(backup_path.read_bytes())

    result["rolled_back"] = not dry_run
    return result


def report_bundle_payload(
    path: Path,
    payload: dict[str, Any],
    *,
    wiki_root: Path | None,
) -> dict[str, Any]:
    targets = payload.get("targets", [])
    target_types: Counter[str] = Counter()
    affected_paths: set[str] = set()
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                target_types["<invalid>"] += 1
                continue
            target_types[str(target.get("type", "<missing>"))] += 1
            affected = target.get("source_path") or target.get("path")
            if affected:
                affected_paths.add(str(affected))
    validation = validate_patch_bundle(path, wiki_root=wiki_root)
    return {
        "affected_paths": sorted(affected_paths),
        "backup_manifest": payload.get("backup_manifest"),
        "bundle_id": payload.get("bundle_id"),
        "kind": "patch_bundle",
        "path": str(path),
        "rationale": payload.get("rationale"),
        "source_catalog": payload.get("source_catalog"),
        "target_count": len(targets) if isinstance(targets, list) else 0,
        "target_types": [
            {"type": kind, "count": count} for kind, count in sorted(target_types.items())
        ],
        "valid": validation["valid"],
        "validation_errors": validation["errors"],
    }


def report_manifest_payload(
    path: Path,
    payload: dict[str, Any],
    *,
    wiki_root: Path | None,
) -> dict[str, Any]:
    files = payload.get("files", [])
    if not isinstance(files, list):
        files = []
    file_reports = [
        manifest_file_status(entry, manifest_path=path, wiki_root=wiki_root)
        for entry in files
        if isinstance(entry, dict)
    ]
    statuses: Counter[str] = Counter(str(item["status"]) for item in file_reports)
    actions: Counter[str] = Counter(str(item["action"]) for item in file_reports)
    return {
        "actions": [{"action": action, "count": count} for action, count in sorted(actions.items())],
        "applied_at_utc": payload.get("applied_at_utc"),
        "blocked_count": sum(
            count for status, count in statuses.items() if status.startswith("blocked")
        ),
        "bundle_id": payload.get("bundle_id"),
        "bundle_path": payload.get("bundle_path"),
        "checked_wiki_root": str(wiki_root) if wiki_root is not None else None,
        "file_count": len(file_reports),
        "files": file_reports,
        "kind": "patch_manifest",
        "manifest_path": str(path),
        "ready_count": statuses.get("ready", 0),
        "status_counts": [
            {"status": status, "count": count} for status, count in sorted(statuses.items())
        ],
        "wiki_root": payload.get("wiki_root"),
    }


def manifest_file_status(
    entry: dict[str, Any],
    *,
    manifest_path: Path,
    wiki_root: Path | None,
) -> dict[str, Any]:
    rel_path = entry.get("path")
    action = manifest_action(entry)
    expected_current_sha256 = expected_manifest_current_sha256(entry)
    raw_backup_path = entry.get("backup_path")
    backup_path = resolve_backup_path(raw_backup_path, manifest_path) if raw_backup_path else None
    backup_sha256 = None
    backup_exists = backup_path.exists() if backup_path is not None else False
    if backup_exists:
        backup_sha256 = sha256_file(backup_path)

    status = "unchecked"
    current_exists = None
    current_sha256 = None

    if rel_path is None:
        status = "blocked_missing_path"
    elif action not in {"replace", "create"}:
        status = "blocked_unsupported_action"
    elif action == "replace" and backup_path is None:
        status = "blocked_missing_backup"
    elif action == "replace" and not backup_exists:
        status = "blocked_missing_backup"
    elif action == "replace" and entry.get("old_sha256") and backup_sha256 != entry.get("old_sha256"):
        status = "blocked_backup_hash_mismatch"
    elif expected_current_sha256 is None:
        status = "blocked_missing_expected_hash"
    elif wiki_root is not None:
        try:
            target = safe_wiki_target(wiki_root, str(rel_path))
        except ValueError:
            status = "blocked_unsafe_path"
        else:
            current_exists = target.exists()
            if action == "create" and not current_exists:
                status = "already_missing"
            elif action == "replace" and not current_exists:
                status = "blocked_current_missing"
            else:
                current_sha256 = sha256_file(target)
                if current_sha256 != expected_current_sha256:
                    status = "blocked_current_mismatch"
                else:
                    status = "ready"

    return {
        "action": action,
        "backup_exists": backup_exists if backup_path is not None else None,
        "backup_path": str(backup_path) if backup_path is not None else None,
        "backup_sha256": backup_sha256,
        "current_exists": current_exists,
        "current_sha256": current_sha256,
        "expected_current_sha256": expected_current_sha256,
        "old_sha256": entry.get("old_sha256"),
        "path": rel_path,
        "status": status,
    }


def manifest_action(entry: dict[str, Any]) -> str:
    action = entry.get("action")
    if action:
        return str(action)
    if entry.get("backup_path"):
        return "replace"
    return "unknown"


def expected_manifest_current_sha256(entry: dict[str, Any]) -> str | None:
    value = entry.get("new_sha256") or entry.get("current_sha256")
    return str(value) if value else None


def resolve_backup_path(raw_path: Any, manifest_path: Path) -> Path:
    path = Path(str(raw_path))
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, manifest_path.parent / path]
    parts = path.parts
    if len(parts) >= 2 and parts[1] == manifest_path.parent.name:
        backup_root_parent = manifest_path.parent.parent
        if parts[0] == backup_root_parent.name:
            candidates.append(backup_root_parent.parent / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def safe_wiki_target(wiki_root: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"path must be wiki-relative: {rel_path}")
    target = (wiki_root / path).resolve()
    if not target.is_relative_to(wiki_root.resolve()):
        raise ValueError(f"path escapes wiki root: {rel_path}")
    return target


def patch_write_preflight(
    payload: dict[str, Any],
    *,
    wiki_root: Path,
    catalog_db: Path | None,
    require_match: bool,
) -> dict[str, Any]:
    requested_root = str(wiki_root.resolve())
    checked_roots: list[dict[str, Any]] = []

    source_catalog = payload.get("source_catalog")
    if isinstance(source_catalog, dict) and source_catalog.get("root"):
        checked_roots.append(
            {
                "root": str(source_catalog["root"]),
                "run_id": source_catalog.get("run_id"),
                "source": "bundle.source_catalog",
            }
        )

    if catalog_db is not None:
        scan_run = latest_scan_run(catalog_db)
        if scan_run is None:
            if require_match:
                raise ValueError(f"catalog root preflight failed: no scan run in {catalog_db}")
            checked_roots.append(
                {
                    "db_path": str(catalog_db),
                    "root": None,
                    "source": "catalog_db",
                    "status": "missing_scan_run",
                }
            )
        else:
            checked_roots.append(
                {
                    "db_path": str(catalog_db),
                    "root": str(scan_run["root"]),
                    "run_id": scan_run.get("run_id"),
                    "source": "catalog_db",
                }
            )

    mismatches = [
        item
        for item in checked_roots
        if item.get("root") and str(Path(str(item["root"])).resolve()) != requested_root
    ]
    if mismatches and require_match:
        details = "; ".join(
            f"{item['source']} root {item['root']} != write root {requested_root}"
            for item in mismatches
        )
        raise ValueError(f"catalog root preflight failed: {details}")

    return {
        "checked_roots": checked_roots,
        "mismatch_count": len(mismatches),
        "requested_wiki_root": requested_root,
        "status": "mismatch" if mismatches else "pass",
    }


def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


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
