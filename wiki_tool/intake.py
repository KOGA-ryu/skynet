from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import json
import re
from typing import Any


DEFAULT_INTAKE_DIR = Path("state/intake")
LIBRARY_INTAKE_QUEUE_PATH = "projects/library_operations/library_intake_queue.md"
LIBRARY_INTAKE_PACKET_DIR = "projects/library_operations/intake"
VALID_STATUSES = {"captured", "staged", "routed", "promoted", "deferred", "rejected"}
VALID_CONFIDENCES = {
    "code_confirmed",
    "docs_confirmed",
    "docs_and_code_confirmed",
    "operator_confirmed",
    "repo_confirmed",
    "user_confirmed",
    "inference",
    "unreviewed",
}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def validate_intake_manifest(input_path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    result = intake_manifest_result(input_path, repo_root=repo_root)
    normalized = result["manifest"]
    return {
        "errors": result["errors"],
        "finding_count": len(normalized.get("findings", [])),
        "input": str(input_path),
        "intake_id": normalized.get("intake_id"),
        "priority_counts": priority_counts(normalized.get("findings", [])),
        "repo_root": str(repo_root) if repo_root is not None else None,
        "valid": not result["errors"],
        "warnings": result["warnings"],
    }


def write_intake_outputs(
    input_path: Path,
    *,
    repo_root: Path | None = None,
    output_dir: Path = DEFAULT_INTAKE_DIR,
) -> dict[str, Any]:
    result = require_valid_intake(input_path, repo_root=repo_root)
    manifest = with_generated_timestamp(result["manifest"])
    run_dir = output_dir / manifest["safe_intake_id"]
    run_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "README.md": render_intake_index_markdown(manifest),
        "intake_queue.md": render_intake_queue_markdown(manifest),
        "promotion_candidates.md": render_promotion_candidates_markdown(manifest),
        "librarian_packet.md": render_librarian_packet_markdown(manifest),
        "manifest_normalized.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    }
    written: list[str] = []
    for filename, text in files.items():
        path = run_dir / filename
        path.write_text(text)
        written.append(str(path))
    return {
        "errors": [],
        "file_count": len(written),
        "files": written,
        "finding_count": len(manifest["findings"]),
        "input": str(input_path),
        "intake_id": manifest["intake_id"],
        "output_dir": str(run_dir),
        "priority_counts": priority_counts(manifest["findings"]),
        "warnings": result["warnings"],
    }


def build_intake_patch_bundle(
    input_path: Path,
    *,
    repo_root: Path | None = None,
    wiki_root: Path | None = None,
) -> dict[str, Any]:
    result = require_valid_intake(input_path, repo_root=repo_root)
    manifest = with_generated_timestamp(result["manifest"])
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    packet_path = librarian_packet_wiki_path(manifest)
    targets: list[dict[str, Any]] = [
        {
            "body": render_librarian_packet_markdown(manifest),
            "path": packet_path,
            "reason": "Create local review packet for repo-demand intake",
            "title": librarian_packet_title(manifest),
            "type": "create_markdown_file",
        }
    ]
    skipped: list[dict[str, str]] = []
    target = library_intake_queue_update_target(manifest, wiki_root=wiki_root)
    if target is None:
        skipped.append(
            {
                "kind": "library_intake_queue_update",
                "reason": "library intake queue was not available or did not have a unique active-source section",
            }
        )
    elif target == {}:
        skipped.append(
            {
                "kind": "library_intake_queue_update",
                "reason": "intake source is already present in the library intake queue",
            }
        )
    else:
        targets.append(target)
    return {
        "backup_manifest": {
            "required_before_apply": True,
            "status": "not_created",
        },
        "bundle_id": f"bundle:intake:{manifest['safe_intake_id']}:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": created_at,
        "intake": intake_bundle_metadata(manifest),
        "rationale": "Stage a repo-demand intake packet and queue entry for librarian review.",
        "skipped": skipped,
        "targets": targets,
    }


def intake_manifest_result(input_path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        payload = json.loads(input_path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "errors": [f"invalid JSON: {exc}"],
            "manifest": {},
            "warnings": [],
        }
    if not isinstance(payload, dict):
        return {
            "errors": ["manifest must be a JSON object"],
            "manifest": {},
            "warnings": [],
        }
    manifest = normalize_intake_manifest(payload, repo_root=repo_root, errors=errors, warnings=warnings)
    return {
        "errors": errors,
        "manifest": manifest,
        "warnings": warnings,
    }


def require_valid_intake(input_path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    result = intake_manifest_result(input_path, repo_root=repo_root)
    if result["errors"]:
        raise ValueError("; ".join(result["errors"]))
    return result


def normalize_intake_manifest(
    payload: dict[str, Any],
    *,
    repo_root: Path | None,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    for key in ["intake_id", "title", "topic", "source_type", "repo", "findings"]:
        if not payload.get(key):
            errors.append(f"missing required top-level field: {key}")

    root = normalized_repo_root(repo_root, errors)
    repo = normalize_repo(payload.get("repo"), errors=errors)
    findings = normalize_findings(payload.get("findings"), repo_root=root, errors=errors, warnings=warnings)
    findings = sorted(findings, key=finding_sort_key)
    status_counts = dict(sorted(Counter(finding["status"] for finding in findings).items()))
    return {
        "finding_count": len(findings),
        "findings": findings,
        "intake_id": str(payload.get("intake_id", "")).strip(),
        "overall_status": overall_status(findings),
        "priority_counts": priority_counts(findings),
        "repo": repo,
        "repo_root": str(root) if root is not None else None,
        "safe_intake_id": safe_path_component(str(payload.get("intake_id", "")).strip()),
        "source_type": normalize_token(str(payload.get("source_type", "")).strip()),
        "status_counts": status_counts,
        "title": str(payload.get("title", "")).strip(),
        "topic": normalize_token(str(payload.get("topic", "")).strip()),
    }


def normalized_repo_root(repo_root: Path | None, errors: list[str]) -> Path | None:
    if repo_root is None:
        return None
    root = repo_root.expanduser().resolve()
    if not root.exists():
        errors.append(f"repo_root does not exist: {repo_root}")
    elif not root.is_dir():
        errors.append(f"repo_root is not a directory: {repo_root}")
    return root


def normalize_repo(value: Any, *, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("repo must be an object")
        return {"name": "", "url": None, "branch": None, "commit": None}
    name = str(value.get("name", "")).strip()
    if not name:
        errors.append("repo.name is required")
    return {
        "branch": optional_string(value.get("branch")),
        "commit": optional_string(value.get("commit")),
        "name": name,
        "url": optional_string(value.get("url")),
    }


def normalize_findings(
    value: Any,
    *,
    repo_root: Path | None,
    errors: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        errors.append("findings must be a non-empty list")
        return []
    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            errors.append(f"finding {index} must be an object")
            continue
        finding_id = str(raw.get("id", "")).strip()
        if not finding_id:
            errors.append(f"finding {index} missing required field: id")
        elif finding_id in seen_ids:
            errors.append(f"duplicate finding id: {finding_id}")
        seen_ids.add(finding_id)
        for key in ["title", "summary", "evidence", "confidence"]:
            if not raw.get(key):
                errors.append(f"finding {finding_id or index} missing required field: {key}")
        status = normalize_enum(
            raw.get("status", "captured"),
            allowed=VALID_STATUSES,
            default="captured",
            field=f"finding {finding_id or index} status",
            errors=errors,
        )
        confidence = normalize_enum(
            raw.get("confidence"),
            allowed=VALID_CONFIDENCES,
            default="",
            field=f"finding {finding_id or index} confidence",
            errors=errors,
        )
        evidence = normalize_evidence_list(
            raw.get("evidence"),
            repo_root=repo_root,
            finding_id=finding_id or str(index),
            errors=errors,
            warnings=warnings,
        )
        selected_targets = normalize_string_list(
            raw.get("selected_targets", []),
            field=f"finding {finding_id or index} selected_targets",
            errors=errors,
        )
        tags = normalize_string_list(raw.get("tags", []), field=f"finding {finding_id or index} tags", errors=errors)
        finding = {
            "confidence": confidence,
            "evidence": evidence,
            "evidence_count": len(evidence),
            "id": finding_id,
            "notes": optional_string(raw.get("notes")),
            "priority": "P2",
            "selected_targets": selected_targets,
            "status": status,
            "summary": str(raw.get("summary", "")).strip(),
            "tags": tags,
            "title": str(raw.get("title", "")).strip(),
        }
        finding["priority"] = finding_priority(finding)
        findings.append(finding)
    return findings


def normalize_evidence_list(
    value: Any,
    *,
    repo_root: Path | None,
    finding_id: str,
    errors: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        errors.append(f"finding {finding_id} evidence must be a non-empty list")
        return []
    evidence: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        entry = normalize_evidence_entry(
            raw,
            repo_root=repo_root,
            finding_id=finding_id,
            evidence_index=index,
            errors=errors,
            warnings=warnings,
        )
        if entry is not None:
            evidence.append(entry)
    return evidence


def normalize_evidence_entry(
    value: Any,
    *,
    repo_root: Path | None,
    finding_id: str,
    evidence_index: int,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    if isinstance(value, str):
        raw_path = value.strip()
        label = None
        line = None
        note = None
    elif isinstance(value, dict):
        raw_path = str(value.get("path", "")).strip()
        label = optional_string(value.get("label"))
        line = value.get("line")
        note = optional_string(value.get("note"))
    else:
        errors.append(f"finding {finding_id} evidence {evidence_index} must be a string or object")
        return None

    if not raw_path:
        errors.append(f"finding {finding_id} evidence {evidence_index} missing path")
        return None
    if is_url(raw_path):
        return {
            "exists": None,
            "kind": "url",
            "label": label,
            "line": normalize_line(line, finding_id, evidence_index, warnings),
            "note": note,
            "path": raw_path,
        }

    path = Path(raw_path).expanduser()
    if path.is_absolute():
        if repo_root is None:
            errors.append(f"finding {finding_id} evidence {evidence_index} absolute path requires --repo-root")
            return None
        resolved = path.resolve()
        if not resolved.is_relative_to(repo_root):
            errors.append(f"finding {finding_id} evidence {evidence_index} escapes repo_root: {raw_path}")
            return None
        relative_path = resolved.relative_to(repo_root).as_posix()
        target = resolved
    else:
        pure = PurePosixPath(raw_path)
        if ".." in pure.parts:
            errors.append(f"finding {finding_id} evidence {evidence_index} escapes repo_root: {raw_path}")
            return None
        relative_path = pure.as_posix().lstrip("./")
        target = (repo_root / relative_path).resolve() if repo_root is not None else None
        if repo_root is not None and not target.is_relative_to(repo_root):
            errors.append(f"finding {finding_id} evidence {evidence_index} escapes repo_root: {raw_path}")
            return None

    exists = None
    absolute_path = None
    if target is not None:
        exists = target.exists()
        absolute_path = str(target)
        if not exists:
            warnings.append(f"finding {finding_id} evidence missing under repo_root: {relative_path}")
    return {
        "absolute_path": absolute_path,
        "exists": exists,
        "kind": "repo_path",
        "label": label,
        "line": normalize_line(line, finding_id, evidence_index, warnings),
        "note": note,
        "path": relative_path,
    }


def normalize_line(value: Any, finding_id: str, evidence_index: int, warnings: list[str]) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int) and value > 0:
        return value
    warnings.append(f"finding {finding_id} evidence {evidence_index} line is not a positive integer")
    return None


def normalize_string_list(value: Any, *, field: str, errors: list[str]) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        errors.append(f"{field} must be a list of strings")
        return []
    result = [str(item).strip() for item in value if str(item).strip()]
    return result


def normalize_enum(
    value: Any,
    *,
    allowed: set[str],
    default: str,
    field: str,
    errors: list[str],
) -> str:
    if value in (None, ""):
        if default:
            return default
        errors.append(f"{field} is required")
        return ""
    normalized = normalize_token(str(value))
    if normalized not in allowed:
        errors.append(f"{field} unsupported value: {value}")
    return normalized


def normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip() or None


def is_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def finding_priority(finding: dict[str, Any]) -> str:
    status = str(finding["status"])
    if status in {"routed", "promoted"} or finding.get("selected_targets"):
        return "P0"
    if status in {"captured", "staged"} and finding.get("evidence") and finding.get("tags"):
        return "P1"
    return "P2"


def finding_sort_key(finding: dict[str, Any]) -> tuple[int, str, str]:
    return (PRIORITY_ORDER.get(str(finding["priority"]), 9), str(finding["status"]), str(finding["id"]))


def priority_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(finding["priority"]) for finding in findings)
    return {priority: counts.get(priority, 0) for priority in ["P0", "P1", "P2"]}


def overall_status(findings: list[dict[str, Any]]) -> str:
    statuses = {str(finding["status"]) for finding in findings}
    if not statuses:
        return "captured"
    if statuses == {"promoted"}:
        return "promoted"
    if statuses & {"routed", "promoted"}:
        return "routed"
    if "captured" in statuses:
        return "captured"
    if "staged" in statuses:
        return "staged"
    if "deferred" in statuses:
        return "deferred"
    return "rejected"


def with_generated_timestamp(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        **manifest,
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def intake_bundle_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_count": manifest["finding_count"],
        "intake_id": manifest["intake_id"],
        "overall_status": manifest["overall_status"],
        "priority_counts": manifest["priority_counts"],
        "repo": manifest["repo"],
        "topic": manifest["topic"],
    }


def library_intake_queue_update_target(
    manifest: dict[str, Any],
    *,
    wiki_root: Path | None,
) -> dict[str, Any] | None:
    if wiki_root is None:
        return None
    queue_path = wiki_root / LIBRARY_INTAKE_QUEUE_PATH
    if not queue_path.exists():
        return None
    text = queue_path.read_bytes().decode("utf-8", errors="surrogateescape")
    if f"local_intake_run: `{manifest['intake_id']}`" in text:
        return {}
    old_text = active_intake_sources_section(text)
    if old_text is None:
        return None
    block = render_active_intake_source_block(manifest)
    new_text = old_text.rstrip() + "\n\n" + block + "\n"
    return {
        "new_text": new_text,
        "old_text": old_text,
        "path": LIBRARY_INTAKE_QUEUE_PATH,
        "reason": "Add repo-demand intake source to the library intake queue",
        "source_path": LIBRARY_INTAKE_QUEUE_PATH,
        "type": "replace_text_block",
    }


def active_intake_sources_section(text: str) -> str | None:
    matches = list(re.finditer(r"(?ms)^## Active Intake Sources\r?\n.*?(?=^## |\Z)", text))
    if len(matches) != 1:
        return None
    return matches[0].group(0)


def render_active_intake_source_block(manifest: dict[str, Any]) -> str:
    repo = manifest["repo"]
    packet_name = Path(librarian_packet_wiki_path(manifest)).name
    summary = status_summary(manifest)
    repo_label = repo["url"] or repo["name"]
    return "\n".join(
        [
            f"### {repo['name']}",
            "",
            f"- source_type: `{manifest['source_type']}`",
            f"- repo: `{repo_label}`",
            f"- overall_state: `{manifest['overall_status']}`",
            f"- summary: {summary}",
            f"- downstream_operator_packet: [{librarian_packet_title(manifest)}](intake/{packet_name})",
            f"- local_intake_run: `{manifest['intake_id']}`",
        ]
    )


def status_summary(manifest: dict[str, Any]) -> str:
    counts = manifest.get("status_counts", {})
    if not counts:
        return "0 findings"
    parts = [f"{count} {status} item{'s' if count != 1 else ''}" for status, count in counts.items()]
    return ", ".join(parts)


def librarian_packet_title(manifest: dict[str, Any]) -> str:
    return f"{manifest['title']} Librarian Operation Packet"


def librarian_packet_wiki_path(manifest: dict[str, Any]) -> str:
    return f"{LIBRARY_INTAKE_PACKET_DIR}/{manifest['safe_intake_id']}_librarian_packet.md"


def render_intake_index_markdown(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Intake Run: {manifest['title']}",
            "",
            f"- intake_id: `{manifest['intake_id']}`",
            f"- generated_at_utc: `{manifest['generated_at_utc']}`",
            f"- repo: `{manifest['repo']['name']}`",
            f"- topic: `{manifest['topic']}`",
            f"- source_type: `{manifest['source_type']}`",
            f"- finding_count: `{manifest['finding_count']}`",
            f"- overall_status: `{manifest['overall_status']}`",
            "",
            "## Local Artifacts",
            "",
            "- [Intake Queue](intake_queue.md)",
            "- [Promotion Candidates](promotion_candidates.md)",
            "- [Librarian Packet](librarian_packet.md)",
            "- [Normalized Manifest](manifest_normalized.json)",
            "",
        ]
    )


def render_intake_queue_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        f"# Repo Demand Intake Queue: {manifest['title']}",
        "",
        "Local-only queue for reviewed repo findings before wiki promotion.",
        "",
        f"- intake_id: `{manifest['intake_id']}`",
        f"- repo: `{manifest['repo']['name']}`",
        f"- overall_status: `{manifest['overall_status']}`",
        f"- finding_count: `{manifest['finding_count']}`",
        "",
        "## Priority Counts",
        "",
        "| priority | findings |",
        "|---|---:|",
    ]
    for priority, count in manifest["priority_counts"].items():
        lines.append(f"| `{priority}` | {count} |")
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "| priority | status | finding | confidence | targets | evidence |",
            "|---|---|---|---|---:|---:|",
        ]
    )
    for finding in manifest["findings"]:
        lines.append(
            "| `{priority}` | `{status}` | `{id}`: {title} | `{confidence}` | {targets} | {evidence} |".format(
                confidence=finding["confidence"],
                evidence=finding["evidence_count"],
                id=escape_table(finding["id"]),
                priority=finding["priority"],
                status=finding["status"],
                targets=len(finding["selected_targets"]),
                title=escape_table(finding["title"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_promotion_candidates_markdown(manifest: dict[str, Any]) -> str:
    candidates = [finding for finding in manifest["findings"] if finding["priority"] == "P0"]
    lines = [
        f"# Promotion Candidates: {manifest['title']}",
        "",
        "P0 findings have selected targets or already carry routed/promoted status.",
        "",
    ]
    if not candidates:
        lines.extend(["No promotion candidates in this intake run.", ""])
        return "\n".join(lines)
    for finding in candidates:
        lines.extend(render_finding_section(finding))
    return "\n".join(lines)


def render_librarian_packet_markdown(manifest: dict[str, Any]) -> str:
    repo = manifest["repo"]
    lines = [
        f"# {librarian_packet_title(manifest)}",
        "",
        "## Intake Metadata",
        "",
        f"- intake_id: `{manifest['intake_id']}`",
        f"- generated_at_utc: `{manifest['generated_at_utc']}`",
        f"- repo: `{repo['name']}`",
        f"- repo_url: `{repo['url'] or 'none'}`",
        f"- branch: `{repo['branch'] or 'unknown'}`",
        f"- commit: `{repo['commit'] or 'unknown'}`",
        f"- topic: `{manifest['topic']}`",
        f"- source_type: `{manifest['source_type']}`",
        f"- overall_status: `{manifest['overall_status']}`",
        "",
        "## Operator Guidance",
        "",
        "- Keep this packet local until a reviewed patch bundle is accepted.",
        "- Promote only into existing durable wiki surfaces unless Planner review opens a new branch.",
        "- Treat repo findings as evidence candidates, not architecture mandates.",
        "",
        "## Findings",
        "",
    ]
    for finding in manifest["findings"]:
        lines.extend(render_finding_section(finding))
    return "\n".join(lines)


def render_finding_section(finding: dict[str, Any]) -> list[str]:
    lines = [
        f"### {finding['title']}",
        "",
        f"- id: `{finding['id']}`",
        f"- priority: `{finding['priority']}`",
        f"- status: `{finding['status']}`",
        f"- confidence: `{finding['confidence']}`",
        f"- summary: {finding['summary']}",
    ]
    if finding["tags"]:
        lines.append(f"- tags: `{', '.join(finding['tags'])}`")
    if finding["selected_targets"]:
        lines.append("- selected_targets:")
        for target in finding["selected_targets"]:
            lines.append(f"  - `{target}`")
    if finding["notes"]:
        lines.append(f"- notes: {finding['notes']}")
    lines.extend(["", "Evidence:", ""])
    for evidence in finding["evidence"]:
        detail = f"- `{evidence['path']}`"
        if evidence.get("line"):
            detail += f" line `{evidence['line']}`"
        if evidence.get("exists") is False:
            detail += " (missing locally)"
        if evidence.get("note"):
            detail += f" - {evidence['note']}"
        lines.append(detail)
    lines.append("")
    return lines


def safe_path_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "intake"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|")
