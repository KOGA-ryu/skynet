from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wiki_tool.catalog import DEFAULT_DB, audit_summary
from wiki_tool.eval import (
    DEFAULT_EVAL_FILE,
    eval_cleanup_targets,
    run_eval,
)
from wiki_tool.harness import DEFAULT_HARNESS_DB, DEFAULT_SPEC_DIR, validate_harness_specs
from wiki_tool.health import run_step


DEFAULT_SCHEDULED_AUDIT_DIR = Path("state/scheduled_audits")
DEFAULT_SCHEDULED_CLEANUP_TARGET_LIMIT = 20


def run_scheduled_audit(
    *,
    catalog_db: Path = DEFAULT_DB,
    harness_db: Path = DEFAULT_HARNESS_DB,
    spec_dir: Path = DEFAULT_SPEC_DIR,
    eval_file: Path = DEFAULT_EVAL_FILE,
    output_dir: Path = DEFAULT_SCHEDULED_AUDIT_DIR,
    freshness_root: Path | None = None,
    eval_limit: int | None = None,
    cleanup_target_limit: int = DEFAULT_SCHEDULED_CLEANUP_TARGET_LIMIT,
    require_eval: bool = False,
    skip_eval: bool = False,
    skip_cleanup_targets: bool = False,
    write_report: bool = True,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    steps: list[dict[str, Any]] = []

    steps.append(
        run_step(
            "audit",
            lambda: scheduled_audit_step(catalog_db, freshness_root=freshness_root),
        )
    )
    steps[-1]["required"] = True

    steps.append(
        run_step(
            "harness_validate",
            lambda: scheduled_harness_step(spec_dir),
        )
    )
    steps[-1]["required"] = True

    if skip_eval:
        steps.append(skipped_step("eval", required=require_eval))
    else:
        steps.append(
            run_step(
                "eval",
                lambda: scheduled_eval_step(
                    eval_file=eval_file,
                    catalog_db=catalog_db,
                    harness_db=harness_db,
                    spec_dir=spec_dir,
                    limit=eval_limit,
                ),
            )
        )
        steps[-1]["required"] = require_eval

    if skip_cleanup_targets:
        steps.append(skipped_step("cleanup_targets", required=False))
    else:
        steps.append(
            run_step(
                "cleanup_targets",
                lambda: scheduled_cleanup_targets_step(
                    eval_file=eval_file,
                    catalog_db=catalog_db,
                    limit=eval_limit,
                    target_limit=cleanup_target_limit,
                ),
            )
        )
        steps[-1]["required"] = False

    ended = datetime.now(UTC)
    status = "pass" if required_steps_passed(steps) else "fail"
    payload: dict[str, Any] = {
        "ended_at_utc": ended.isoformat(timespec="seconds"),
        "duration_seconds": round((ended - started).total_seconds(), 3),
        "inputs": {
            "catalog_db": str(catalog_db),
            "cleanup_target_limit": cleanup_target_limit,
            "eval_file": str(eval_file),
            "eval_limit": eval_limit,
            "freshness_root": str(freshness_root) if freshness_root else None,
            "harness_db": str(harness_db),
            "output_dir": str(output_dir),
            "require_eval": require_eval,
            "skip_cleanup_targets": skip_cleanup_targets,
            "skip_eval": skip_eval,
            "spec_dir": str(spec_dir),
        },
        "started_at_utc": started.isoformat(timespec="seconds"),
        "status": status,
        "steps": steps,
    }
    if write_report:
        report_path = write_scheduled_audit_report(payload, output_dir=output_dir)
        payload["report_path"] = str(report_path)
    return payload


def scheduled_audit_step(catalog_db: Path, *, freshness_root: Path | None) -> dict[str, Any]:
    audit = audit_summary(catalog_db, freshness_root=freshness_root)
    return {
        "audit": {
            "broken_link_categories": audit["broken_link_categories"],
            "broken_links": audit["broken_links"],
            "counts": audit["counts"],
            "excluded_links": audit["excluded_links"],
            "scan_freshness": audit["scan_freshness"],
            "scan_run": audit["scan_run"],
            "status": audit["status"],
        },
        "status": "pass" if audit["status"] == "pass" else "fail",
    }


def scheduled_harness_step(spec_dir: Path) -> dict[str, Any]:
    harness = validate_harness_specs(spec_dir)
    return {
        "harness": {
            "errors": harness["errors"],
            "path": str(spec_dir),
            "spec_count": harness["spec_count"],
            "valid": harness["valid"],
        },
        "status": "pass" if harness["valid"] else "fail",
    }


def scheduled_eval_step(
    *,
    eval_file: Path,
    catalog_db: Path,
    harness_db: Path,
    spec_dir: Path,
    limit: int | None,
) -> dict[str, Any]:
    result = run_eval(
        eval_file=eval_file,
        catalog_db=catalog_db,
        harness_db=harness_db,
        spec_dir=spec_dir,
        limit=limit,
    )
    return {
        "broken_link_regression": result["broken_link_regression"],
        "eval": {
            "eval_file": result["eval_file"],
            "harness_db": result["harness_db"],
            "summary": result["summary"],
        },
        "status": "pass" if result["status"] == "pass" else "fail",
    }


def scheduled_cleanup_targets_step(
    *,
    eval_file: Path,
    catalog_db: Path,
    limit: int | None,
    target_limit: int,
) -> dict[str, Any]:
    result = eval_cleanup_targets(
        eval_file=eval_file,
        catalog_db=catalog_db,
        limit=limit,
        target_limit=target_limit,
    )
    return {
        "cleanup_targets": {
            "comparison_profile": result["comparison_profile"],
            "profile": result["profile"],
            "summary": result["summary"],
            "targets": result["targets"],
        },
        "status": "pass",
    }


def skipped_step(name: str, *, required: bool) -> dict[str, Any]:
    return {
        "duration_seconds": 0.0,
        "name": name,
        "required": required,
        "skipped": True,
        "status": "skip",
    }


def required_steps_passed(steps: list[dict[str, Any]]) -> bool:
    return all(step["status"] in {"pass", "skip"} for step in steps if step.get("required", True))


def write_scheduled_audit_report(payload: dict[str, Any], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"scheduled_audit_{timestamp}.md"
    path.write_text(render_scheduled_audit_report(payload))
    return path


def render_scheduled_audit_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Scheduled Audit Report",
        "",
        f"- started: `{payload['started_at_utc']}`",
        f"- ended: `{payload['ended_at_utc']}`",
        f"- duration_seconds: `{payload['duration_seconds']}`",
        f"- status: `{payload['status']}`",
        "",
        "## Inputs",
        "",
    ]
    for key, value in payload["inputs"].items():
        lines.append(f"- {key}: `{value}`")

    lines.extend(
        [
            "",
            "## Steps",
            "",
            "| step | required | status | duration | summary |",
            "|---|---:|---|---:|---|",
        ]
    )
    for step in payload["steps"]:
        lines.append(
            "| {name} | {required} | {status} | {duration} | {summary} |".format(
                duration=step["duration_seconds"],
                name=markdown_cell(str(step["name"])),
                required="yes" if step.get("required", True) else "no",
                status=markdown_cell(str(step["status"])),
                summary=markdown_cell(step_summary(step)),
            )
        )
    lines.append("")
    return "\n".join(lines)


def step_summary(step: dict[str, Any]) -> str:
    if step.get("skipped"):
        return "skipped"
    if "error" in step:
        return str(step["error"])
    if step["name"] == "audit":
        audit = step["audit"]
        counts = audit.get("counts", {})
        return (
            f"broken_links={audit.get('broken_links')}, "
            f"excluded_links={audit.get('excluded_links')}, "
            f"documents={counts.get('documents')}"
        )
    if step["name"] == "harness_validate":
        harness = step["harness"]
        return f"valid={harness.get('valid')}, specs={harness.get('spec_count')}, errors={len(harness.get('errors', []))}"
    if step["name"] == "eval":
        summary = step["eval"]["summary"]
        return (
            f"pass_rate={summary.get('query_pass_rate')}, "
            f"retrieval_hit_rate={summary.get('retrieval_hit_rate')}, "
            f"cases={summary.get('total_cases')}"
        )
    if step["name"] == "cleanup_targets":
        summary = step["cleanup_targets"]["summary"]
        return (
            f"targets={summary.get('target_count')}, "
            f"P0={summary.get('priority_counts', {}).get('P0', 0)}, "
            f"P1={summary.get('priority_counts', {}).get('P1', 0)}"
        )
    return "complete"


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|")
