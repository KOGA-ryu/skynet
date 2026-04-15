from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

from wiki_tool.aliases import DEFAULT_ALIAS_MAP, aliases_as_dicts, load_alias_entries
from wiki_tool.catalog import (
    DEFAULT_DB,
    DEFAULT_WIKI_ROOT,
    alias_map_validation,
    audit_summary,
    broken_link_categories,
    broken_links,
    find_references,
    gaps,
    get_headings,
    list_aliases,
    open_path,
    query_catalog,
    scan_freshness,
    scan_wiki,
)
from wiki_tool.devrefs import (
    DEFAULT_CONFIG,
    DEFAULT_MAC_DEV_ROOT,
    build_devref_patch_bundle,
    devref_audit,
    is_dev_uri,
    resolve_dev_uri,
)
from wiki_tool.eval import (
    compare_retrieval_profiles,
    DEFAULT_BASELINE_RETRIEVAL_PROFILE,
    DEFAULT_CLEANUP_COMPARISON_PROFILE,
    DEFAULT_CLEANUP_TARGET_LIMIT,
    DEFAULT_EVAL_FILE,
    DEFAULT_EVAL_REPORT_DIR,
    eval_cleanup_targets,
    run_eval,
)
from wiki_tool.file_links import build_file_links_patch_bundle, file_link_audit
from wiki_tool.harness import (
    DEFAULT_HARNESS_DB,
    DEFAULT_SPEC_DIR,
    diff_harness_runs,
    get_harness_run,
    list_harness_runs,
    run_answer_with_citations,
    validate_harness_specs,
)
from wiki_tool.health import DEFAULT_TESTS_DIR, run_health
from wiki_tool.jsonrpc_api import DEFAULT_API_TRACE, handle_jsonrpc_text
from wiki_tool.llm import DEFAULT_OPENAI_MODEL
from wiki_tool.missing_notes import build_missing_notes_patch_bundle, missing_note_audit
from wiki_tool.patch_bundle import (
    apply_patch_bundle,
    report_patch_bundle,
    rollback_patch_bundle,
    validate_patch_bundle,
)
from wiki_tool.page_quality import (
    DEFAULT_PAGE_QUALITY_DIR,
    generated_stubs_report,
    missing_summaries_report,
    page_quality_summary,
    thin_notes_report,
    unclear_hubs_report,
    write_page_quality_reports,
)
from wiki_tool.project_reports import (
    DEFAULT_PROJECT_REPORT_LIMIT,
    DEFAULT_PROJECT_REPORT_DIR,
    project_report,
    project_report_summary,
    write_project_reports,
)
from wiki_tool.scheduled_audit import (
    DEFAULT_SCHEDULED_AUDIT_DIR,
    DEFAULT_SCHEDULED_CLEANUP_TARGET_LIMIT,
    run_scheduled_audit,
)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if payload is not None:
        print_payload(payload, json_output=args.json)
    if getattr(args, "exit_fail_on_status", False) and isinstance(payload, dict):
        if payload.get("status") == "fail":
            raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wiki", description="Local NAS wiki usability tooling")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = parser.add_subparsers(required=True)

    scan = sub.add_parser("scan", help="scan a Markdown wiki into the local catalog")
    add_json_flag(scan)
    scan.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    scan.add_argument("--alias-map", type=Path, default=DEFAULT_ALIAS_MAP)
    scan.set_defaults(func=cmd_scan)

    scan_status = sub.add_parser("scan-status", help="compare the catalog to a wiki root for stale-scan detection")
    add_json_flag(scan_status)
    scan_status.add_argument("--wiki-root", type=Path, help="root to compare; defaults to the catalog scan root")
    scan_status.add_argument("--limit", type=int, default=25, help="maximum changed paths to include per section")
    scan_status.set_defaults(func=cmd_scan_status)

    find = sub.add_parser("find", help="search notes, headings, and symbol seeds")
    add_json_flag(find)
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=10)
    find.set_defaults(func=cmd_find)

    headings = sub.add_parser("headings", help="list headings for a note")
    add_json_flag(headings)
    headings.add_argument("path")
    headings.set_defaults(func=cmd_headings)

    refs = sub.add_parser("refs", help="find backlinks/references to a note")
    add_json_flag(refs)
    refs.add_argument("target")
    refs.set_defaults(func=cmd_refs)

    broken = sub.add_parser("broken-links", help="list unresolved Markdown/wiki links")
    add_json_flag(broken)
    broken.add_argument("--limit", type=int, help="maximum unresolved links to show")
    broken.add_argument("--category", help="filter by broken-link category")
    broken.set_defaults(func=cmd_broken_links)

    gap_cmd = sub.add_parser("gaps", help="show catalog gaps")
    add_json_flag(gap_cmd)
    gap_cmd.set_defaults(func=cmd_gaps)

    project_reports = sub.add_parser("project-reports", help="per-project backlink and gap reports")
    add_json_flag(project_reports)
    project_reports_sub = project_reports.add_subparsers(required=True)
    project_reports_summary = project_reports_sub.add_parser("summary", help="summarize all top-level projects")
    add_json_flag(project_reports_summary)
    project_reports_summary.set_defaults(func=cmd_project_reports_summary)
    project_reports_show = project_reports_sub.add_parser("show", help="show one top-level project report")
    add_json_flag(project_reports_show)
    project_reports_show.add_argument("project")
    project_reports_show.add_argument("--limit", type=int, default=DEFAULT_PROJECT_REPORT_LIMIT)
    project_reports_show.set_defaults(func=cmd_project_reports_show)
    project_reports_write = project_reports_sub.add_parser("write", help="write local Markdown project reports")
    add_json_flag(project_reports_write)
    project_reports_write.add_argument("--output-dir", type=Path, default=DEFAULT_PROJECT_REPORT_DIR)
    project_reports_write.add_argument("--limit", type=int, default=DEFAULT_PROJECT_REPORT_LIMIT)
    project_reports_write.set_defaults(func=cmd_project_reports_write)

    page_quality = sub.add_parser("page-quality", help="page quality reports for librarian review")
    add_json_flag(page_quality)
    page_quality_sub = page_quality.add_subparsers(required=True)
    page_quality_summary_cmd = page_quality_sub.add_parser("summary", help="summarize page quality queues")
    add_json_flag(page_quality_summary_cmd)
    page_quality_summary_cmd.set_defaults(func=cmd_page_quality_summary)
    page_quality_thin = page_quality_sub.add_parser("thin", help="list thin notes")
    add_json_flag(page_quality_thin)
    page_quality_thin.set_defaults(func=cmd_page_quality_thin)
    page_quality_missing = page_quality_sub.add_parser(
        "missing-summaries",
        help="list notes with missing or weak summaries",
    )
    add_json_flag(page_quality_missing)
    page_quality_missing.set_defaults(func=cmd_page_quality_missing_summaries)
    page_quality_hubs = page_quality_sub.add_parser("unclear-hubs", help="list unclear hub pages")
    add_json_flag(page_quality_hubs)
    page_quality_hubs.set_defaults(func=cmd_page_quality_unclear_hubs)
    page_quality_stubs = page_quality_sub.add_parser("stubs", help="list generated stubs needing human content")
    add_json_flag(page_quality_stubs)
    page_quality_stubs.set_defaults(func=cmd_page_quality_stubs)
    page_quality_write = page_quality_sub.add_parser("write", help="write local Markdown page quality reports")
    add_json_flag(page_quality_write)
    page_quality_write.add_argument("--output-dir", type=Path, default=DEFAULT_PAGE_QUALITY_DIR)
    page_quality_write.set_defaults(func=cmd_page_quality_write)

    audit = sub.add_parser("audit", help="summarize catalog health")
    add_json_flag(audit)
    audit.add_argument("--wiki-root", type=Path, help="root to compare for stale-scan detection")
    audit.add_argument("--freshness-limit", type=int, default=25)
    audit.add_argument("--write", action="store_true", help="write a local audit markdown report")
    audit.set_defaults(func=cmd_audit)

    health = sub.add_parser("health", help="run scan, audit, harness validation, and unit tests")
    add_json_flag(health)
    health.add_argument("--wiki-root", type=Path, default=DEFAULT_WIKI_ROOT)
    health.add_argument("--alias-map", type=Path, default=DEFAULT_ALIAS_MAP)
    health.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    health.add_argument("--tests-dir", type=Path, default=DEFAULT_TESTS_DIR)
    health.set_defaults(func=cmd_health, exit_fail_on_status=True)

    scheduled_audit = sub.add_parser("scheduled-audit", help="scheduler-friendly local audit reports")
    add_json_flag(scheduled_audit)
    scheduled_audit_sub = scheduled_audit.add_subparsers(required=True)
    scheduled_audit_run = scheduled_audit_sub.add_parser("run", help="run a local scheduled audit checkpoint")
    add_json_flag(scheduled_audit_run)
    scheduled_audit_run.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    scheduled_audit_run.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    scheduled_audit_run.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    scheduled_audit_run.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    scheduled_audit_run.add_argument("--output-dir", type=Path, default=DEFAULT_SCHEDULED_AUDIT_DIR)
    scheduled_audit_run.add_argument("--freshness-root", type=Path)
    scheduled_audit_run.add_argument("--eval-limit", type=int)
    scheduled_audit_run.add_argument("--cleanup-target-limit", type=int, default=DEFAULT_SCHEDULED_CLEANUP_TARGET_LIMIT)
    scheduled_audit_run.add_argument("--require-eval", action="store_true")
    scheduled_audit_run.add_argument("--skip-eval", action="store_true")
    scheduled_audit_run.add_argument("--skip-cleanup-targets", action="store_true")
    scheduled_audit_run.add_argument("--write-report", action="store_true", default=True)
    scheduled_audit_run.set_defaults(func=cmd_scheduled_audit_run, exit_fail_on_status=True)

    open_cmd = sub.add_parser("open", help="translate a catalog identifier to a platform path")
    add_json_flag(open_cmd)
    open_cmd.add_argument("identifier")
    open_cmd.add_argument("--platform", choices=["mac", "windows"], default="mac")
    open_cmd.add_argument("--mac-root", default="/Volumes/wiki")
    open_cmd.add_argument("--windows-root", default="W:\\")
    open_cmd.add_argument("--mac-dev-root")
    open_cmd.add_argument("--windows-dev-root")
    open_cmd.add_argument("--devref-config", type=Path, default=DEFAULT_CONFIG)
    open_cmd.set_defaults(func=cmd_open)

    devrefs = sub.add_parser("devrefs", help="portable dev:// reference helpers")
    add_json_flag(devrefs)
    devrefs_sub = devrefs.add_subparsers(required=True)
    devrefs_audit = devrefs_sub.add_parser("audit", help="summarize local dev paths that can become dev:// refs")
    add_json_flag(devrefs_audit)
    devrefs_audit.add_argument("--mac-dev-root", default=DEFAULT_MAC_DEV_ROOT)
    devrefs_audit.set_defaults(func=cmd_devrefs_audit)
    devrefs_bundle = devrefs_sub.add_parser("bundle", help="write a patch bundle for dev:// link conversion")
    add_json_flag(devrefs_bundle)
    devrefs_bundle.add_argument("--mac-dev-root", default=DEFAULT_MAC_DEV_ROOT)
    devrefs_bundle.add_argument("--output", type=Path, required=True)
    devrefs_bundle.set_defaults(func=cmd_devrefs_bundle)

    missing_notes = sub.add_parser("missing-notes", help="missing Markdown note helpers")
    add_json_flag(missing_notes)
    missing_notes_sub = missing_notes.add_subparsers(required=True)
    missing_notes_audit = missing_notes_sub.add_parser("audit", help="summarize missing Markdown note candidates")
    add_json_flag(missing_notes_audit)
    missing_notes_audit.add_argument("--limit", type=int)
    missing_notes_audit.set_defaults(func=cmd_missing_notes_audit)
    missing_notes_bundle = missing_notes_sub.add_parser("bundle", help="write a patch bundle for missing note stubs")
    add_json_flag(missing_notes_bundle)
    missing_notes_bundle.add_argument("--limit", type=int)
    missing_notes_bundle.add_argument("--output", type=Path, required=True)
    missing_notes_bundle.set_defaults(func=cmd_missing_notes_bundle)

    file_links = sub.add_parser("file-links", help="non-Markdown file link helpers")
    add_json_flag(file_links)
    file_links_sub = file_links.add_subparsers(required=True)
    file_links_audit = file_links_sub.add_parser("audit", help="summarize non-Markdown file link repairs")
    add_json_flag(file_links_audit)
    file_links_audit.add_argument("--mac-dev-root", default=DEFAULT_MAC_DEV_ROOT)
    file_links_audit.set_defaults(func=cmd_file_links_audit)
    file_links_bundle = file_links_sub.add_parser("bundle", help="write a patch bundle for file link repairs")
    add_json_flag(file_links_bundle)
    file_links_bundle.add_argument("--mac-dev-root", default=DEFAULT_MAC_DEV_ROOT)
    file_links_bundle.add_argument("--output", type=Path, required=True)
    file_links_bundle.set_defaults(func=cmd_file_links_bundle)

    aliases = sub.add_parser("aliases", help="wiki alias map helpers")
    add_json_flag(aliases)
    aliases_sub = aliases.add_subparsers(required=True)
    aliases_validate = aliases_sub.add_parser("validate", help="validate the source alias map against the catalog")
    add_json_flag(aliases_validate)
    aliases_validate.add_argument("--alias-map", type=Path, default=DEFAULT_ALIAS_MAP)
    aliases_validate.set_defaults(func=cmd_aliases_validate)
    aliases_list = aliases_sub.add_parser("list", help="list source alias map entries")
    add_json_flag(aliases_list)
    aliases_list.add_argument("--alias-map", type=Path, default=DEFAULT_ALIAS_MAP)
    aliases_list.add_argument("--catalog", action="store_true", help="list aliases stored in the current catalog")
    aliases_list.set_defaults(func=cmd_aliases_list)

    explain = sub.add_parser("explain", help="explain the read-guard path for a query")
    add_json_flag(explain)
    explain.add_argument("query")
    explain.add_argument("--limit", type=int, default=5)
    explain.set_defaults(func=cmd_explain)

    eval_cmd = sub.add_parser("eval", help="wiki eval helpers")
    add_json_flag(eval_cmd)
    eval_sub = eval_cmd.add_subparsers(required=True)
    eval_run = eval_sub.add_parser("run", help="run the wiki eval query set")
    add_json_flag(eval_run)
    eval_run.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    eval_run.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    eval_run.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    eval_run.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    eval_run.add_argument("--limit", type=int)
    eval_run.add_argument("--write-report", action="store_true")
    eval_run.add_argument("--report-dir", type=Path, default=DEFAULT_EVAL_REPORT_DIR)
    eval_run.set_defaults(func=cmd_eval_run)
    eval_compare = eval_sub.add_parser("compare-profiles", help="compare eval-only retrieval profiles")
    add_json_flag(eval_compare)
    eval_compare.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    eval_compare.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    eval_compare.add_argument("--profiles", help="comma-separated retrieval profile IDs")
    eval_compare.add_argument("--baseline-profile", default=DEFAULT_BASELINE_RETRIEVAL_PROFILE)
    eval_compare.add_argument("--k", type=int, default=8)
    eval_compare.add_argument("--limit", type=int)
    eval_compare.add_argument("--write-report", action="store_true")
    eval_compare.add_argument("--report-dir", type=Path, default=DEFAULT_EVAL_REPORT_DIR)
    eval_compare.set_defaults(func=cmd_eval_compare_profiles)
    eval_cleanup = eval_sub.add_parser("cleanup-targets", help="rank eval-driven wiki cleanup targets")
    add_json_flag(eval_cleanup)
    eval_cleanup.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    eval_cleanup.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    eval_cleanup.add_argument("--profile", default=DEFAULT_BASELINE_RETRIEVAL_PROFILE)
    eval_cleanup.add_argument("--comparison-profile", default=DEFAULT_CLEANUP_COMPARISON_PROFILE)
    eval_cleanup.add_argument("--k", type=int, default=8)
    eval_cleanup.add_argument("--limit", type=int)
    eval_cleanup.add_argument("--target-limit", type=int, default=DEFAULT_CLEANUP_TARGET_LIMIT)
    eval_cleanup.add_argument("--write-report", action="store_true")
    eval_cleanup.add_argument("--report-dir", type=Path, default=DEFAULT_EVAL_REPORT_DIR)
    eval_cleanup.set_defaults(func=cmd_eval_cleanup_targets)

    api = sub.add_parser("api", help="bounded JSON-RPC knowledge API helpers")
    add_json_flag(api)
    api_sub = api.add_subparsers(required=True)
    api_request = api_sub.add_parser("request", help="handle one JSON-RPC request")
    add_json_flag(api_request)
    api_request.add_argument("--request-json", required=True)
    api_request.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    api_request.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    api_request.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    api_request.add_argument("--trace-path", type=Path, default=DEFAULT_API_TRACE)
    api_request.set_defaults(func=cmd_api_request)
    api_serve = api_sub.add_parser("serve", help="serve newline-delimited JSON-RPC over stdin/stdout")
    add_json_flag(api_serve)
    api_serve.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    api_serve.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    api_serve.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    api_serve.add_argument("--trace-path", type=Path, default=DEFAULT_API_TRACE)
    api_serve.set_defaults(func=cmd_api_serve)

    harness = sub.add_parser("harness", help="executable harness helpers")
    add_json_flag(harness)
    harness_sub = harness.add_subparsers(required=True)
    harness_validate = harness_sub.add_parser("validate", help="validate harness Markdown/YAML specs")
    add_json_flag(harness_validate)
    harness_validate.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    harness_validate.set_defaults(func=cmd_harness_validate)
    harness_answer = harness_sub.add_parser("answer", help="run the wiki answer-with-citations harness")
    add_json_flag(harness_answer)
    harness_answer.add_argument("query")
    harness_answer.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    harness_answer.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    harness_answer.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    harness_answer.add_argument(
        "--synthesis",
        choices=["deterministic", "openai"],
        default="deterministic",
        help="synthesis adapter to use after retrieval",
    )
    harness_answer.add_argument(
        "--llm-model",
        default=None,
        help=f"model name for LLM-backed synthesis modes (default: {DEFAULT_OPENAI_MODEL})",
    )
    harness_answer.set_defaults(func=cmd_harness_answer)
    harness_runs = harness_sub.add_parser("runs", help="list recent harness runs")
    add_json_flag(harness_runs)
    harness_runs.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    harness_runs.add_argument("--limit", type=int, default=10)
    harness_runs.set_defaults(func=cmd_harness_runs)
    harness_show = harness_sub.add_parser("show", help="show a harness run trace")
    add_json_flag(harness_show)
    harness_show.add_argument("run_id")
    harness_show.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    harness_show.set_defaults(func=cmd_harness_show)
    harness_diff = harness_sub.add_parser("diff", help="compare two harness run traces")
    add_json_flag(harness_diff)
    harness_diff.add_argument("run_ids", nargs="*", help="base and head run IDs")
    harness_diff.add_argument("--harness-db", type=Path, default=DEFAULT_HARNESS_DB)
    harness_diff.add_argument("--latest", action="store_true", help="compare the two newest harness runs")
    harness_diff.add_argument("--limit", type=int, default=25, help="maximum changed items to include per section")
    harness_diff.set_defaults(func=cmd_harness_diff)

    patch = sub.add_parser("patch-bundle", help="patch bundle helpers")
    add_json_flag(patch)
    patch_sub = patch.add_subparsers(required=True)
    validate = patch_sub.add_parser("validate", help="validate a patch bundle JSON file")
    add_json_flag(validate)
    validate.add_argument("path", type=Path)
    validate.add_argument("--wiki-root", type=Path)
    validate.set_defaults(func=cmd_patch_validate)
    apply_cmd = patch_sub.add_parser("apply", help="apply a validated patch bundle to a wiki root")
    add_json_flag(apply_cmd)
    apply_cmd.add_argument("path", type=Path)
    apply_cmd.add_argument("--wiki-root", type=Path, required=True)
    apply_cmd.add_argument("--backup-dir", type=Path, default=Path("backups"))
    apply_cmd.add_argument("--dry-run", action="store_true")
    apply_cmd.set_defaults(func=cmd_patch_apply)
    report = patch_sub.add_parser("report", help="summarize a patch bundle or applied manifest")
    add_json_flag(report)
    report.add_argument("path", type=Path)
    report.add_argument("--wiki-root", type=Path)
    report.set_defaults(func=cmd_patch_report)
    rollback = patch_sub.add_parser("rollback", help="restore files from an applied bundle manifest")
    add_json_flag(rollback)
    rollback.add_argument("manifest", type=Path)
    rollback.add_argument("--wiki-root", type=Path, required=True)
    rollback.add_argument("--dry-run", action="store_true")
    rollback.set_defaults(func=cmd_patch_rollback)

    return parser


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def cmd_scan(args: argparse.Namespace) -> dict[str, Any]:
    result = scan_wiki(root=args.wiki_root, db_path=args.db, alias_map_path=args.alias_map)
    return {"scan": result.__dict__}


def cmd_scan_status(args: argparse.Namespace) -> dict[str, Any]:
    return {"scan_freshness": scan_freshness(args.db, root=args.wiki_root, limit=args.limit)}


def cmd_find(args: argparse.Namespace) -> dict[str, Any]:
    symbols = query_catalog(args.db, "symbol.search", args.query, args.limit)
    spans = query_catalog(args.db, "span.searchText", args.query, args.limit)
    docs = query_catalog(args.db, "document.search", args.query, args.limit)
    return {"query": args.query, "symbols": symbols, "spans": spans, "documents": docs}


def cmd_headings(args: argparse.Namespace) -> dict[str, Any]:
    return {"path": args.path, "headings": get_headings(args.db, args.path)}


def cmd_refs(args: argparse.Namespace) -> dict[str, Any]:
    return {"target": args.target, "references": find_references(args.db, args.target)}


def cmd_broken_links(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "categories": broken_link_categories(args.db),
        "broken_links": broken_links(args.db, limit=args.limit, category=args.category),
    }


def cmd_gaps(args: argparse.Namespace) -> dict[str, Any]:
    return gaps(args.db)


def cmd_project_reports_summary(args: argparse.Namespace) -> dict[str, Any]:
    return project_report_summary(args.db)


def cmd_project_reports_show(args: argparse.Namespace) -> dict[str, Any]:
    return project_report(args.db, args.project, limit=args.limit)


def cmd_project_reports_write(args: argparse.Namespace) -> dict[str, Any]:
    return write_project_reports(args.db, output_dir=args.output_dir, limit=args.limit)


def cmd_page_quality_summary(args: argparse.Namespace) -> dict[str, Any]:
    return page_quality_summary(args.db)


def cmd_page_quality_thin(args: argparse.Namespace) -> dict[str, Any]:
    return thin_notes_report(args.db)


def cmd_page_quality_missing_summaries(args: argparse.Namespace) -> dict[str, Any]:
    return missing_summaries_report(args.db)


def cmd_page_quality_unclear_hubs(args: argparse.Namespace) -> dict[str, Any]:
    return unclear_hubs_report(args.db)


def cmd_page_quality_stubs(args: argparse.Namespace) -> dict[str, Any]:
    return generated_stubs_report(args.db)


def cmd_page_quality_write(args: argparse.Namespace) -> dict[str, Any]:
    return write_page_quality_reports(args.db, output_dir=args.output_dir)


def cmd_audit(args: argparse.Namespace) -> dict[str, Any]:
    summary = audit_summary(args.db, freshness_root=args.wiki_root, freshness_limit=args.freshness_limit)
    if args.write:
        path = Path("state") / f"audit_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_audit(summary))
        summary["written_report"] = str(path)
    return summary


def cmd_health(args: argparse.Namespace) -> dict[str, Any]:
    return run_health(
        wiki_root=args.wiki_root,
        db_path=args.db,
        alias_map_path=args.alias_map,
        spec_dir=args.spec_dir,
        tests_dir=args.tests_dir,
    )


def cmd_scheduled_audit_run(args: argparse.Namespace) -> dict[str, Any]:
    return run_scheduled_audit(
        catalog_db=args.catalog_db,
        harness_db=args.harness_db,
        spec_dir=args.spec_dir,
        eval_file=args.eval_file,
        output_dir=args.output_dir,
        freshness_root=args.freshness_root,
        eval_limit=args.eval_limit,
        cleanup_target_limit=args.cleanup_target_limit,
        require_eval=args.require_eval,
        skip_eval=args.skip_eval,
        skip_cleanup_targets=args.skip_cleanup_targets,
        write_report=args.write_report,
    )


def cmd_open(args: argparse.Namespace) -> dict[str, Any]:
    if is_dev_uri(args.identifier):
        return resolve_dev_uri(
            args.identifier,
            platform=args.platform,
            mac_root=args.mac_dev_root,
            windows_root=args.windows_dev_root,
            config_path=args.devref_config,
        )
    return open_path(
        args.db,
        args.identifier,
        platform=args.platform,
        mac_root=args.mac_root,
        windows_root=args.windows_root,
    )


def cmd_devrefs_audit(args: argparse.Namespace) -> dict[str, Any]:
    return devref_audit(args.db, mac_dev_root=args.mac_dev_root)


def cmd_devrefs_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle = build_devref_patch_bundle(args.db, mac_dev_root=args.mac_dev_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    validation = validate_patch_bundle(args.output)
    return {
        "bundle_id": bundle["bundle_id"],
        "output": str(args.output),
        "target_count": len(bundle["targets"]),
        "valid": validation["valid"],
        "validation_errors": validation["errors"],
    }


def cmd_missing_notes_audit(args: argparse.Namespace) -> dict[str, Any]:
    return missing_note_audit(args.db, limit=args.limit)


def cmd_missing_notes_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle = build_missing_notes_patch_bundle(args.db, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    validation = validate_patch_bundle(args.output)
    return {
        "bundle_id": bundle["bundle_id"],
        "output": str(args.output),
        "target_count": len(bundle["targets"]),
        "valid": validation["valid"],
        "validation_errors": validation["errors"],
    }


def cmd_file_links_audit(args: argparse.Namespace) -> dict[str, Any]:
    return file_link_audit(args.db, mac_dev_root=args.mac_dev_root)


def cmd_file_links_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle = build_file_links_patch_bundle(args.db, mac_dev_root=args.mac_dev_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    validation = validate_patch_bundle(args.output)
    return {
        "bundle_id": bundle["bundle_id"],
        "output": str(args.output),
        "skipped_count": len(bundle.get("skipped", [])),
        "target_count": len(bundle["targets"]),
        "valid": validation["valid"],
        "validation_errors": validation["errors"],
    }


def cmd_aliases_validate(args: argparse.Namespace) -> dict[str, Any]:
    return alias_map_validation(args.db, alias_map_path=args.alias_map)


def cmd_aliases_list(args: argparse.Namespace) -> dict[str, Any]:
    if args.catalog:
        return {"aliases": list_aliases(args.db), "source": "catalog"}
    return {
        "aliases": aliases_as_dicts(load_alias_entries(args.alias_map)),
        "path": str(args.alias_map),
        "source": "alias_map",
    }


def cmd_explain(args: argparse.Namespace) -> dict[str, Any]:
    symbol_results = query_catalog(args.db, "symbol.search", args.query, args.limit)
    span_results = query_catalog(args.db, "span.searchText", args.query, args.limit)
    decision = "symbol-first"
    if not symbol_results and span_results:
        decision = "bounded-span-fallback"
    elif not symbol_results and not span_results:
        decision = "insufficient-evidence"
    return {
        "query": args.query,
        "policy": {
            "decision": decision,
            "steps": [
                "attempt symbol.search",
                "if unresolved, attempt span.searchText",
                "avoid full-file reads unless explicitly authorized",
            ],
            "symbol_matches": len(symbol_results),
            "span_matches": len(span_results),
        },
        "symbols": symbol_results,
        "spans": span_results,
    }


def cmd_harness_validate(args: argparse.Namespace) -> dict[str, Any]:
    return validate_harness_specs(args.spec_dir)


def cmd_harness_answer(args: argparse.Namespace) -> dict[str, Any]:
    return run_answer_with_citations(
        args.query,
        catalog_db=args.catalog_db,
        harness_db=args.harness_db,
        spec_dir=args.spec_dir,
        synthesis=args.synthesis,
        llm_model=args.llm_model,
    )


def cmd_harness_runs(args: argparse.Namespace) -> dict[str, Any]:
    return list_harness_runs(args.harness_db, limit=args.limit)


def cmd_harness_show(args: argparse.Namespace) -> dict[str, Any]:
    return get_harness_run(args.run_id, args.harness_db)


def cmd_harness_diff(args: argparse.Namespace) -> dict[str, Any]:
    run_ids = args.run_ids
    if args.latest and run_ids:
        raise ValueError("Use either --latest or two explicit run IDs, not both")
    if args.latest:
        runs = list_harness_runs(args.harness_db, limit=2)["runs"]
        if len(runs) < 2:
            raise ValueError("--latest requires at least two harness runs")
        head_run_id = runs[0]["run_id"]
        base_run_id = runs[1]["run_id"]
        return diff_harness_runs(base_run_id, head_run_id, args.harness_db, limit=args.limit)
    if len(run_ids) != 2:
        raise ValueError("harness diff requires BASE_RUN_ID and HEAD_RUN_ID, or --latest")
    return diff_harness_runs(run_ids[0], run_ids[1], args.harness_db, limit=args.limit)


def cmd_eval_run(args: argparse.Namespace) -> dict[str, Any]:
    return run_eval(
        eval_file=args.eval_file,
        catalog_db=args.catalog_db,
        harness_db=args.harness_db,
        spec_dir=args.spec_dir,
        limit=args.limit,
        write_report=args.write_report,
        report_dir=args.report_dir,
    )


def cmd_eval_compare_profiles(args: argparse.Namespace) -> dict[str, Any]:
    profiles = split_csv(args.profiles) if args.profiles else None
    return compare_retrieval_profiles(
        eval_file=args.eval_file,
        catalog_db=args.catalog_db,
        profile_ids=profiles,
        baseline_profile=args.baseline_profile,
        k=args.k,
        limit=args.limit,
        write_report=args.write_report,
        report_dir=args.report_dir,
    )


def cmd_eval_cleanup_targets(args: argparse.Namespace) -> dict[str, Any]:
    return eval_cleanup_targets(
        eval_file=args.eval_file,
        catalog_db=args.catalog_db,
        profile=args.profile,
        comparison_profile=args.comparison_profile,
        k=args.k,
        limit=args.limit,
        target_limit=args.target_limit,
        write_report=args.write_report,
        report_dir=args.report_dir,
    )


def cmd_api_request(args: argparse.Namespace) -> dict[str, Any] | None:
    return handle_jsonrpc_text(
        args.request_json,
        db_path=args.catalog_db,
        harness_db=args.harness_db,
        spec_dir=args.spec_dir,
        trace_path=args.trace_path,
    )


def cmd_api_serve(args: argparse.Namespace) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_jsonrpc_text(
            line,
            db_path=args.catalog_db,
            harness_db=args.harness_db,
            spec_dir=args.spec_dir,
            trace_path=args.trace_path,
        )
        if response is not None:
            print(json.dumps(response, sort_keys=True), flush=True)
    return None


def cmd_patch_validate(args: argparse.Namespace) -> dict[str, Any]:
    return validate_patch_bundle(args.path, wiki_root=args.wiki_root)


def cmd_patch_apply(args: argparse.Namespace) -> dict[str, Any]:
    return apply_patch_bundle(
        args.path,
        wiki_root=args.wiki_root,
        backup_dir=args.backup_dir,
        catalog_db=args.db,
        dry_run=args.dry_run,
    )


def cmd_patch_report(args: argparse.Namespace) -> dict[str, Any]:
    return report_patch_bundle(args.path, wiki_root=args.wiki_root)


def cmd_patch_rollback(args: argparse.Namespace) -> dict[str, Any]:
    return rollback_patch_bundle(
        args.manifest,
        wiki_root=args.wiki_root,
        dry_run=args.dry_run,
    )


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def print_payload(payload: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(render_text(payload))


def render_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    lines: list[str] = []
    for key, value in payload.items():
        lines.append(f"{key}:")
        lines.extend(indent(render_value(value)))
    return "\n".join(lines)


def render_value(value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return ["  []"]
        rows = []
        for item in value:
            rows.append(f"  - {compact(item)}")
        return rows
    if isinstance(value, dict):
        if not value:
            return ["  {}"]
        return [f"  {key}: {compact(val)}" for key, val in value.items()]
    return [f"  {value}"]


def compact(value: Any) -> str:
    if isinstance(value, dict):
        preferred = [
            "path",
            "title",
            "name",
            "heading",
            "kind",
            "line",
            "target_raw",
            "snippet",
            "span_id",
            "symbol_id",
            "new_target",
            "old_target",
            "repo",
            "count",
            "candidate_count",
            "inbound_reference_count",
            "new_label",
            "old_label",
            "repair_kind",
            "normalized",
            "target_path",
            "reason",
            "project",
            "root",
            "hub_path",
            "hub_present",
            "missing_hub",
            "note_count",
            "orphan_count",
            "inbound_count",
            "source_path",
            "line_count",
            "action",
            "status",
            "changed",
            "target_count",
            "file_count",
            "blocked_count",
            "ready_count",
        ]
        parts = [f"{key}={value[key]!r}" for key in preferred if key in value]
        return ", ".join(parts) if parts else json.dumps(value, sort_keys=True)
    return repr(value)


def indent(lines: list[str]) -> list[str]:
    return [f"  {line}" for line in lines]


def render_audit(summary: dict[str, Any]) -> str:
    freshness = summary.get("scan_freshness", {})
    return "\n".join(
        [
            "# Wiki Catalog Audit",
            "",
            f"- generated_at_utc: `{datetime.now(UTC).isoformat(timespec='seconds')}`",
            f"- status: `{summary['status']}`",
            f"- broken_links: `{summary['broken_links']}`",
            f"- notes_without_headings: `{summary['notes_without_headings']}`",
            f"- notes_without_inbound_links: `{summary['notes_without_inbound_links']}`",
            f"- scan_freshness: `{freshness.get('status', 'unknown')}`",
            "",
            "## Counts",
            "",
            *[f"- {key}: `{value}`" for key, value in summary.get("counts", {}).items()],
            "",
            "## Scan Freshness",
            "",
            f"- status: `{freshness.get('status', 'unknown')}`",
            f"- stale: `{freshness.get('stale', 'unknown')}`",
            f"- reason: `{freshness.get('reason', 'unknown')}`",
            f"- catalog_root: `{freshness.get('catalog_root')}`",
            f"- checked_root: `{freshness.get('checked_root')}`",
            f"- added_document_count: `{freshness.get('added_document_count')}`",
            f"- modified_document_count: `{freshness.get('modified_document_count')}`",
            f"- removed_document_count: `{freshness.get('removed_document_count')}`",
            f"- added_file_count: `{freshness.get('added_file_count')}`",
            f"- removed_file_count: `{freshness.get('removed_file_count')}`",
            "",
            "## Broken Link Categories",
            "",
            *[
                f"- {item['category']}: `{item['count']}`"
                for item in summary.get("broken_link_categories", [])
            ],
            "",
        ]
    )
