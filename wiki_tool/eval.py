from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from wiki_tool.catalog import DEFAULT_DB, audit_summary
from wiki_tool.harness import (
    build_fallback_search_queries,
    build_search_queries,
    DEFAULT_HARNESS_DB,
    DEFAULT_SPEC_DIR,
    get_harness_run,
    retrieve_catalog_chunks,
    run_answer_with_citations,
)
from wiki_tool.page_quality import build_page_quality_report, word_count


DEFAULT_EVAL_FILE = Path("eval/wiki_queries_v1.jsonl")
DEFAULT_EVAL_REPORT_DIR = Path("state/eval_reports")
DEFAULT_BASELINE_RETRIEVAL_PROFILE = "catalog.fts_spans.primary"
DEFAULT_CLEANUP_COMPARISON_PROFILE = "catalog.fts_spans.expanded"
DEFAULT_CLEANUP_TARGET_LIMIT = 50
DEFAULT_RETRIEVAL_PROFILES = [
    "catalog.fts_spans.primary",
    "catalog.fts_spans.expanded",
    "catalog.fts_documents.primary",
    "catalog.hybrid.spans_documents",
]
REQUIRED_EVAL_KEYS = {"category", "expected_hints", "expected_paths", "min_citations", "query"}


def load_eval_cases(path: Path = DEFAULT_EVAL_FILE) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        missing = REQUIRED_EVAL_KEYS - set(case)
        if missing:
            raise ValueError(f"{path}:{line_number}: missing keys: {sorted(missing)}")
        validate_eval_case(path, line_number, case)
        cases.append(case)
    return cases


def validate_eval_case(path: Path, line_number: int, case: dict[str, Any]) -> None:
    if not isinstance(case["query"], str) or not case["query"].strip():
        raise ValueError(f"{path}:{line_number}: query must be a non-empty string")
    if not isinstance(case["category"], str) or not case["category"].strip():
        raise ValueError(f"{path}:{line_number}: category must be a non-empty string")
    if not isinstance(case["expected_paths"], list) or not case["expected_paths"]:
        raise ValueError(f"{path}:{line_number}: expected_paths must be a non-empty list")
    if not isinstance(case["expected_hints"], list) or not case["expected_hints"]:
        raise ValueError(f"{path}:{line_number}: expected_hints must be a non-empty list")
    if not isinstance(case["min_citations"], int) or case["min_citations"] < 1:
        raise ValueError(f"{path}:{line_number}: min_citations must be an integer >= 1")
    for item in case["expected_paths"]:
        if not isinstance(item, str) or not item.endswith(".md") or item.startswith("/"):
            raise ValueError(f"{path}:{line_number}: expected path must be a relative Markdown path")
    for item in case["expected_hints"]:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}:{line_number}: expected hints must be non-empty strings")


def run_eval(
    *,
    eval_file: Path = DEFAULT_EVAL_FILE,
    catalog_db: Path = DEFAULT_DB,
    harness_db: Path = DEFAULT_HARNESS_DB,
    spec_dir: Path = DEFAULT_SPEC_DIR,
    limit: int | None = None,
    write_report: bool = False,
    report_dir: Path = DEFAULT_EVAL_REPORT_DIR,
) -> dict[str, Any]:
    cases = load_eval_cases(eval_file)
    if limit is not None:
        cases = cases[:limit]

    started_at = utc_now()
    results = [
        score_case(
            case,
            catalog_db=catalog_db,
            harness_db=harness_db,
            spec_dir=spec_dir,
        )
        for case in cases
    ]
    ended_at = utc_now()
    broken_link_regression = score_broken_link_regression(catalog_db)
    summary = summarize_results(results, broken_link_regression=broken_link_regression)
    query_status = "pass" if summary["query_pass_count"] == summary["total_cases"] else "fail"
    payload: dict[str, Any] = {
        "broken_link_regression": broken_link_regression,
        "ended_at_utc": ended_at,
        "eval_file": str(eval_file),
        "harness_db": str(harness_db),
        "results": results,
        "started_at_utc": started_at,
        "status": "pass" if query_status == "pass" and broken_link_regression["status"] == "pass" else "fail",
        "summary": summary,
    }
    if write_report:
        report_path = write_eval_report(payload, report_dir=report_dir)
        payload["report_path"] = str(report_path)
    return payload


def compare_retrieval_profiles(
    *,
    eval_file: Path = DEFAULT_EVAL_FILE,
    catalog_db: Path = DEFAULT_DB,
    profile_ids: list[str] | None = None,
    baseline_profile: str = DEFAULT_BASELINE_RETRIEVAL_PROFILE,
    k: int = 8,
    limit: int | None = None,
    write_report: bool = False,
    report_dir: Path = DEFAULT_EVAL_REPORT_DIR,
) -> dict[str, Any]:
    if k < 1:
        raise ValueError("k must be greater than or equal to 1")
    profiles = profile_ids or list(DEFAULT_RETRIEVAL_PROFILES)
    profiles = unique_strings([baseline_profile, *profiles])
    unknown = sorted(set(profiles) - set(DEFAULT_RETRIEVAL_PROFILES))
    if unknown:
        raise ValueError(f"unknown retrieval profiles: {', '.join(unknown)}")

    cases = load_eval_cases(eval_file)
    if limit is not None:
        cases = cases[:limit]

    started_at = utc_now()
    profile_reports = [
        score_retrieval_profile(
            profile,
            cases,
            catalog_db=catalog_db,
            k=k,
        )
        for profile in profiles
    ]
    ended_at = utc_now()
    by_profile = {profile["profile_id"]: profile for profile in profile_reports}
    comparison = compare_profile_reports(by_profile[baseline_profile], profile_reports)
    payload: dict[str, Any] = {
        "baseline_profile": baseline_profile,
        "catalog_db": str(catalog_db),
        "ended_at_utc": ended_at,
        "eval_file": str(eval_file),
        "k": k,
        "limit": limit,
        "profiles": profile_reports,
        "recommendation": profile_comparison_recommendation(comparison),
        "started_at_utc": started_at,
        "status": "pass",
        "summary": {
            "baseline_profile": baseline_profile,
            "profile_count": len(profile_reports),
            "total_cases": len(cases),
        },
    }
    payload["comparison"] = comparison
    if write_report:
        report_path = write_retrieval_profile_report(payload, report_dir=report_dir)
        payload["report_path"] = str(report_path)
    return payload


def eval_cleanup_targets(
    *,
    eval_file: Path = DEFAULT_EVAL_FILE,
    catalog_db: Path = DEFAULT_DB,
    profile: str = DEFAULT_BASELINE_RETRIEVAL_PROFILE,
    comparison_profile: str = DEFAULT_CLEANUP_COMPARISON_PROFILE,
    k: int = 8,
    limit: int | None = None,
    target_limit: int = DEFAULT_CLEANUP_TARGET_LIMIT,
    write_report: bool = False,
    report_dir: Path = DEFAULT_EVAL_REPORT_DIR,
) -> dict[str, Any]:
    if k < 1:
        raise ValueError("k must be greater than or equal to 1")
    if target_limit < 0:
        raise ValueError("target_limit must be greater than or equal to 0")
    unknown = sorted({profile, comparison_profile} - set(DEFAULT_RETRIEVAL_PROFILES))
    if unknown:
        raise ValueError(f"unknown retrieval profiles: {', '.join(unknown)}")

    cases = load_eval_cases(eval_file)
    if limit is not None:
        cases = cases[:limit]

    baseline_report = score_retrieval_profile(profile, cases, catalog_db=catalog_db, k=k)
    comparison_report = score_retrieval_profile(comparison_profile, cases, catalog_db=catalog_db, k=k)
    quality_by_path = cleanup_quality_index(catalog_db)
    metadata_by_path = cleanup_document_metadata(catalog_db)
    all_targets = build_eval_cleanup_targets(
        baseline_report,
        comparison_report,
        quality_by_path=quality_by_path,
        metadata_by_path=metadata_by_path,
    )
    targets = all_targets[:target_limit]
    generated_at = utc_now()
    payload: dict[str, Any] = {
        "catalog_db": str(catalog_db),
        "comparison_profile": comparison_profile,
        "eval_file": str(eval_file),
        "generated_at_utc": generated_at,
        "k": k,
        "limit": limit,
        "profile": profile,
        "status": "pass",
        "summary": summarize_cleanup_targets(targets, total_candidate_targets=len(all_targets)),
        "target_limit": target_limit,
        "targets": targets,
    }
    if write_report:
        report_path = write_eval_cleanup_report(payload, report_dir=report_dir)
        payload["report_path"] = str(report_path)
    return payload


def score_retrieval_profile(
    profile_id: str,
    cases: list[dict[str, Any]],
    *,
    catalog_db: Path,
    k: int,
) -> dict[str, Any]:
    results = [
        score_retrieval_profile_case(
            case,
            profile_id=profile_id,
            catalog_db=catalog_db,
            k=k,
        )
        for case in cases
    ]
    return {
        "profile_id": profile_id,
        "results": results,
        "summary": summarize_retrieval_profile_results(results),
    }


def score_retrieval_profile_case(
    case: dict[str, Any],
    *,
    profile_id: str,
    catalog_db: Path,
    k: int,
) -> dict[str, Any]:
    candidates = retrieve_profile_candidates(profile_id, str(case["query"]), catalog_db=catalog_db, k=k)
    retrieved_paths = unique_strings(candidate["path"] for candidate in candidates)
    expected_paths = [str(path) for path in case["expected_paths"]]
    matched_expected_paths = [path for path in expected_paths if path in retrieved_paths]
    top_expected_rank = first_expected_rank(retrieved_paths, expected_paths)
    expected_path_recall = len(matched_expected_paths) / len(expected_paths)
    return {
        "category": case["category"],
        "expected_path_recall": round(expected_path_recall, 4),
        "expected_paths": expected_paths,
        "matched_expected_paths": matched_expected_paths,
        "profile_id": profile_id,
        "query": case["query"],
        "reciprocal_rank": round(1 / top_expected_rank, 4) if top_expected_rank else 0.0,
        "retrieval_hit": bool(matched_expected_paths),
        "retrieved_paths": retrieved_paths,
        "top_expected_rank": top_expected_rank,
    }


def retrieve_profile_candidates(
    profile_id: str,
    query: str,
    *,
    catalog_db: Path,
    k: int,
) -> list[dict[str, Any]]:
    if profile_id == "catalog.fts_spans.primary":
        return retrieve_catalog_chunks(catalog_db, build_search_queries(query), k=k)
    if profile_id == "catalog.fts_spans.expanded":
        return retrieve_catalog_chunks(
            catalog_db,
            build_fallback_search_queries(query),
            k=k,
            method="catalog_fts_span_expanded_eval",
        )
    if profile_id == "catalog.fts_documents.primary":
        return retrieve_document_candidates(catalog_db, build_search_queries(query), k=k)
    if profile_id == "catalog.hybrid.spans_documents":
        return merge_candidates(
            [
                *retrieve_catalog_chunks(catalog_db, build_search_queries(query), k=k),
                *retrieve_document_candidates(catalog_db, build_search_queries(query), k=k),
            ],
            k=k,
        )
    raise ValueError(f"unknown retrieval profile: {profile_id}")


def retrieve_document_candidates(catalog_db: Path, queries: list[str], *, k: int) -> list[dict[str, Any]]:
    from wiki_tool.catalog import fts_query

    merged: dict[str, dict[str, Any]] = {}
    with closing(sqlite3.connect(catalog_db)) as con:
        con.row_factory = sqlite3.Row
        for query in queries:
            match = fts_query(query)
            if not match:
                continue
            rows = con.execute(
                """
                SELECT d.doc_id, d.path, d.title, d.text, bm25(documents_fts) AS rank
                FROM documents_fts
                JOIN documents d ON d.doc_id = documents_fts.doc_id
                WHERE documents_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, k),
            ).fetchall()
            for row in rows:
                score = max(0.0, -float(row["rank"]))
                existing = merged.get(row["doc_id"])
                if existing and existing["score"] >= score:
                    continue
                merged[row["doc_id"]] = {
                    "artifact_id": row["path"],
                    "chunk_id": row["doc_id"],
                    "end_line": None,
                    "heading": row["title"],
                    "method": "catalog_fts_document_eval",
                    "path": row["path"],
                    "score": score,
                    "start_line": 1,
                    "text": row["text"],
                }
    return sorted(merged.values(), key=lambda item: (-item["score"], item["path"]))[:k]


def merge_candidates(candidates: list[dict[str, Any]], *, k: int) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        path = str(candidate["path"])
        existing = by_path.get(path)
        if existing and existing["score"] >= candidate["score"]:
            continue
        by_path[path] = {**candidate, "method": f"hybrid:{candidate['method']}"}
    return sorted(by_path.values(), key=lambda item: (-item["score"], item["path"]))[:k]


def first_expected_rank(retrieved_paths: list[str], expected_paths: list[str]) -> int | None:
    expected = set(expected_paths)
    for index, path in enumerate(retrieved_paths, start=1):
        if path in expected:
            return index
    return None


def summarize_retrieval_profile_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    hits = sum(1 for result in results if result["retrieval_hit"])
    recall_sum = sum(float(result["expected_path_recall"]) for result in results)
    rr_sum = sum(float(result["reciprocal_rank"]) for result in results)
    by_category: dict[str, dict[str, Any]] = {}
    for category, items in group_by_category(results).items():
        count = len(items)
        category_hits = sum(1 for item in items if item["retrieval_hit"])
        category_recall = sum(float(item["expected_path_recall"]) for item in items)
        category_rr = sum(float(item["reciprocal_rank"]) for item in items)
        by_category[category] = {
            "average_expected_path_recall": round(category_recall / count, 4) if count else 0.0,
            "mean_reciprocal_rank": round(category_rr / count, 4) if count else 0.0,
            "retrieval_hit_count": category_hits,
            "retrieval_hit_rate": ratio(category_hits, count),
            "total_cases": count,
        }
    return {
        "average_expected_path_recall": round(recall_sum / total, 4) if total else 0.0,
        "mean_reciprocal_rank": round(rr_sum / total, 4) if total else 0.0,
        "retrieval_hit_count": hits,
        "retrieval_hit_rate": ratio(hits, total),
        "total_cases": total,
        "by_category": dict(sorted(by_category.items())),
    }


def compare_profile_reports(
    baseline: dict[str, Any],
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_results = {result["query"]: result for result in baseline["results"]}
    reports: list[dict[str, Any]] = []
    for profile in profiles:
        if profile["profile_id"] == baseline["profile_id"]:
            continue
        improvements: list[str] = []
        regressions: list[str] = []
        unchanged: list[str] = []
        for result in profile["results"]:
            base = baseline_results[str(result["query"])]
            delta_key = retrieval_result_delta_key(base, result)
            if delta_key > 0:
                improvements.append(str(result["query"]))
            elif delta_key < 0:
                regressions.append(str(result["query"]))
            else:
                unchanged.append(str(result["query"]))
        reports.append(
            {
                "average_expected_path_recall_delta": round(
                    profile["summary"]["average_expected_path_recall"]
                    - baseline["summary"]["average_expected_path_recall"],
                    4,
                ),
                "improvement_count": len(improvements),
                "improvements": improvements,
                "mean_reciprocal_rank_delta": round(
                    profile["summary"]["mean_reciprocal_rank"]
                    - baseline["summary"]["mean_reciprocal_rank"],
                    4,
                ),
                "profile_id": profile["profile_id"],
                "regression_count": len(regressions),
                "regressions": regressions,
                "retrieval_hit_rate_delta": round(
                    profile["summary"]["retrieval_hit_rate"] - baseline["summary"]["retrieval_hit_rate"],
                    4,
                ),
                "unchanged_count": len(unchanged),
            }
        )
    return {
        "baseline_profile": baseline["profile_id"],
        "profiles": reports,
    }


def retrieval_result_delta_key(baseline: dict[str, Any], candidate: dict[str, Any]) -> float:
    baseline_rank = baseline["top_expected_rank"] or 10_000
    candidate_rank = candidate["top_expected_rank"] or 10_000
    return (
        float(candidate["expected_path_recall"]) - float(baseline["expected_path_recall"])
        + float(candidate["reciprocal_rank"]) - float(baseline["reciprocal_rank"])
        + (0.0001 if candidate_rank < baseline_rank else -0.0001 if candidate_rank > baseline_rank else 0.0)
    )


def profile_comparison_recommendation(comparison: dict[str, Any]) -> str:
    if not comparison["profiles"]:
        return "Only the baseline profile was evaluated; keep current retrieval behavior."
    clean_improvers = [
        profile
        for profile in comparison["profiles"]
        if profile["regression_count"] == 0
        and (
            profile["improvement_count"] > 0
            or profile["retrieval_hit_rate_delta"] > 0
            or profile["average_expected_path_recall_delta"] > 0
            or profile["mean_reciprocal_rank_delta"] > 0
        )
    ]
    if clean_improvers:
        best = sorted(
            clean_improvers,
            key=lambda item: (
                item["retrieval_hit_rate_delta"],
                item["average_expected_path_recall_delta"],
                item["mean_reciprocal_rank_delta"],
                item["improvement_count"],
            ),
            reverse=True,
        )[0]
        return f"{best['profile_id']} improved the eval set without per-query regressions; keep it eval-only until reviewed."
    return "Keep current retrieval behavior; candidate profiles have no clean no-regression win."


def cleanup_quality_index(catalog_db: Path) -> dict[str, dict[str, Any]]:
    report = build_page_quality_report(catalog_db)
    index: dict[str, dict[str, Any]] = {}
    queue_flags = [
        ("generated_stubs", "generated_stub"),
        ("missing_summaries", "missing_summary"),
        ("thin_notes", "thin_note"),
        ("unclear_hubs", "unclear_hub"),
    ]
    for queue, flag in queue_flags:
        for item in report[queue]:
            path = str(item["path"])
            entry = index.setdefault(
                path,
                {
                    "quality_flags": [],
                    "quality_reasons": [],
                },
            )
            entry["quality_flags"] = unique_strings([*entry["quality_flags"], flag])
            entry["quality_reasons"] = unique_strings(
                [*entry["quality_reasons"], *[str(reason) for reason in item.get("reasons", [])]]
            )
            for key in [
                "byte_size",
                "inbound_count",
                "outbound_link_count",
                "source_count",
                "summary_word_count",
                "title",
                "word_count",
            ]:
                if key in item:
                    entry[key] = item[key]
    return index


def cleanup_document_metadata(catalog_db: Path) -> dict[str, dict[str, Any]]:
    inbound_counts: Counter[str] = Counter()
    outbound_counts: Counter[str] = Counter()
    with closing(sqlite3.connect(catalog_db)) as con:
        con.row_factory = sqlite3.Row
        for row in con.execute(
            """
            SELECT source_path, target_path
            FROM links
            WHERE resolved = 1 AND target_path IS NOT NULL
            """
        ):
            inbound_counts[str(row["target_path"])] += 1
            outbound_counts[str(row["source_path"])] += 1
        rows = con.execute(
            """
            SELECT path, title, kind, byte_size, text
            FROM documents
            ORDER BY path
            """
        ).fetchall()
    return {
        str(row["path"]): {
            "byte_size": int(row["byte_size"]),
            "inbound_count": int(inbound_counts[str(row["path"])]),
            "kind": row["kind"],
            "outbound_link_count": int(outbound_counts[str(row["path"])]),
            "path": row["path"],
            "title": row["title"],
            "word_count": word_count(str(row["text"])),
        }
        for row in rows
    }


def build_eval_cleanup_targets(
    baseline_report: dict[str, Any],
    comparison_report: dict[str, Any],
    *,
    quality_by_path: dict[str, dict[str, Any]],
    metadata_by_path: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    comparison_by_query = {str(result["query"]): result for result in comparison_report["results"]}
    targets: list[dict[str, Any]] = []
    for baseline in baseline_report["results"]:
        comparison = comparison_by_query[str(baseline["query"])]
        for expected_path in baseline["expected_paths"]:
            baseline_rank = path_rank(baseline["retrieved_paths"], expected_path)
            if baseline_rank is not None and baseline_rank <= 3:
                continue
            targets.append(
                cleanup_target(
                    baseline,
                    comparison,
                    expected_path,
                    baseline_rank=baseline_rank,
                    comparison_rank=path_rank(comparison["retrieved_paths"], expected_path),
                    quality=quality_by_path.get(expected_path, {}),
                    metadata=metadata_by_path.get(expected_path),
                )
            )
    return sorted(targets, key=cleanup_target_sort_key)


def cleanup_target(
    baseline: dict[str, Any],
    comparison: dict[str, Any],
    expected_path: str,
    *,
    baseline_rank: int | None,
    comparison_rank: int | None,
    quality: dict[str, Any],
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    quality_flags = [str(flag) for flag in quality.get("quality_flags", [])]
    quality_reasons = [str(reason) for reason in quality.get("quality_reasons", [])]
    catalog_present = metadata is not None
    action = cleanup_action(catalog_present=catalog_present, quality_flags=quality_flags)
    reasons = cleanup_reasons(
        baseline,
        baseline_rank=baseline_rank,
        comparison_rank=comparison_rank,
        catalog_present=catalog_present,
        quality_flags=quality_flags,
        quality_reasons=quality_reasons,
    )
    merged = {**(metadata or {}), **quality}
    priority = cleanup_priority(
        baseline,
        catalog_present=catalog_present,
        quality_flags=quality_flags,
    )
    return {
        "action": action,
        "baseline_expected_path_recall": baseline["expected_path_recall"],
        "baseline_rank": baseline_rank,
        "baseline_retrieval_hit": baseline["retrieval_hit"],
        "baseline_retrieved_paths": baseline["retrieved_paths"][:5],
        "byte_size": merged.get("byte_size"),
        "catalog_present": catalog_present,
        "category": baseline["category"],
        "comparison_expected_path_recall": comparison["expected_path_recall"],
        "comparison_profile_recovered": baseline_rank is None and comparison_rank is not None,
        "comparison_rank": comparison_rank,
        "comparison_retrieved_paths": comparison["retrieved_paths"][:5],
        "inbound_count": int(merged.get("inbound_count", 0)),
        "kind": merged.get("kind"),
        "outbound_link_count": int(merged.get("outbound_link_count", 0)),
        "path": expected_path,
        "priority": priority,
        "quality_flags": quality_flags,
        "quality_reasons": quality_reasons,
        "query": baseline["query"],
        "reasons": reasons,
        "source_count": int(merged.get("source_count", 0)),
        "summary_word_count": merged.get("summary_word_count"),
        "title": merged.get("title") or expected_path,
        "word_count": merged.get("word_count"),
    }


def cleanup_action(*, catalog_present: bool, quality_flags: list[str]) -> str:
    if not catalog_present:
        return "review_eval_gold_target"
    if "generated_stub" in quality_flags:
        return "fill_generated_stub"
    if "unclear_hub" in quality_flags:
        return "strengthen_hub_navigation"
    if "missing_summary" in quality_flags:
        return "add_opening_summary"
    if "thin_note" in quality_flags:
        return "expand_thin_note"
    return "add_search_terms_or_bridge_links"


def cleanup_priority(
    baseline: dict[str, Any],
    *,
    catalog_present: bool,
    quality_flags: list[str],
) -> str:
    if not baseline["retrieval_hit"] or not catalog_present or "generated_stub" in quality_flags:
        return "P0"
    if quality_flags:
        return "P1"
    return "P2"


def cleanup_reasons(
    baseline: dict[str, Any],
    *,
    baseline_rank: int | None,
    comparison_rank: int | None,
    catalog_present: bool,
    quality_flags: list[str],
    quality_reasons: list[str],
) -> list[str]:
    reasons: list[str] = []
    if not baseline["retrieval_hit"]:
        reasons.append("baseline_query_retrieval_miss")
    if baseline_rank is None:
        reasons.append("expected_path_not_retrieved")
    elif baseline_rank > 3:
        reasons.append("expected_path_low_rank")
    if baseline_rank is None and comparison_rank is not None:
        reasons.append("comparison_profile_recovers_path")
    if not catalog_present:
        reasons.append("expected_path_missing_from_catalog")
    return unique_strings([*reasons, *quality_flags, *quality_reasons])


def cleanup_target_sort_key(target: dict[str, Any]) -> tuple[Any, ...]:
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    action_order = {
        "review_eval_gold_target": 0,
        "fill_generated_stub": 1,
        "strengthen_hub_navigation": 2,
        "add_opening_summary": 3,
        "expand_thin_note": 4,
        "add_search_terms_or_bridge_links": 5,
    }
    baseline_rank = target["baseline_rank"] if target["baseline_rank"] is not None else 10_000
    return (
        priority_order[target["priority"]],
        action_order[target["action"]],
        0 if target["baseline_rank"] is None else 1,
        -int(target["inbound_count"]),
        baseline_rank,
        str(target["query"]),
        str(target["path"]),
    )


def path_rank(paths: list[str], path: str) -> int | None:
    for index, candidate in enumerate(paths, start=1):
        if candidate == path:
            return index
    return None


def summarize_cleanup_targets(
    targets: list[dict[str, Any]],
    *,
    total_candidate_targets: int,
) -> dict[str, Any]:
    priorities = Counter(str(target["priority"]) for target in targets)
    actions = Counter(str(target["action"]) for target in targets)
    reasons = Counter(reason for target in targets for reason in target["reasons"])
    return {
        "action_counts": dict(sorted(actions.items())),
        "emitted_target_count": len(targets),
        "hidden_target_count": max(total_candidate_targets - len(targets), 0),
        "priority_counts": dict(sorted(priorities.items())),
        "reason_counts": dict(sorted(reasons.items())),
        "target_count": len(targets),
        "total_candidate_targets": total_candidate_targets,
    }


def score_case(
    case: dict[str, Any],
    *,
    catalog_db: Path,
    harness_db: Path,
    spec_dir: Path,
) -> dict[str, Any]:
    harness_result = run_answer_with_citations(
        str(case["query"]),
        catalog_db=catalog_db,
        harness_db=harness_db,
        spec_dir=spec_dir,
        synthesis="deterministic",
    )
    trace = get_harness_run(harness_result["run_id"], harness_db)
    retrieved_paths = unique_strings(candidate["path"] for candidate in trace["retrieval_candidates"])
    citations = harness_result.get("citations", [])
    citation_paths = unique_strings(str(citation.get("artifact_id", "")) for citation in citations)
    expected_paths = [str(item) for item in case["expected_paths"]]
    expected_hints = [str(item) for item in case["expected_hints"]]
    matched_expected_paths = [path for path in expected_paths if path in retrieved_paths]
    matched_citation_paths = [path for path in expected_paths if path in citation_paths]
    evidence_text = scoring_text(harness_result, trace)

    retrieval_hit = bool(matched_expected_paths)
    expected_path_recall = len(matched_expected_paths) / len(expected_paths)
    citation_count = len(citations)
    citation_count_ok = citation_count >= int(case["min_citations"])
    citation_path_hit = bool(matched_citation_paths)
    matched_hints = [hint for hint in expected_hints if hint.lower() in evidence_text]
    hint_hit = bool(matched_hints)

    failure_reasons: list[str] = []
    if not retrieval_hit:
        failure_reasons.append("retrieval_miss")
    if not citation_count_ok:
        failure_reasons.append("citation_count")
    warnings: list[str] = []
    if not citation_path_hit:
        warnings.append("citation_path_miss")
    if not hint_hit:
        warnings.append("hint_miss")

    return {
        "category": case["category"],
        "citation_count": citation_count,
        "citation_count_ok": citation_count_ok,
        "citation_path_hit": citation_path_hit,
        "citation_paths": citation_paths,
        "expected_path_recall": round(expected_path_recall, 4),
        "expected_paths": expected_paths,
        "failure_reasons": failure_reasons,
        "hint_hit": hint_hit,
        "matched_citation_paths": matched_citation_paths,
        "matched_expected_paths": matched_expected_paths,
        "matched_hints": matched_hints,
        "min_citations": case["min_citations"],
        "query": case["query"],
        "retrieval_hit": retrieval_hit,
        "retrieved_paths": retrieved_paths,
        "run_id": harness_result["run_id"],
        "status": "pass" if retrieval_hit and citation_count_ok else "fail",
        "warnings": warnings,
    }


def score_broken_link_regression(catalog_db: Path) -> dict[str, Any]:
    summary = audit_summary(catalog_db)
    scan_run = summary.get("scan_run") or {}
    actionable_broken_links = int(summary.get("broken_links", 0))
    return {
        "actionable_broken_links": actionable_broken_links,
        "categories": summary.get("broken_link_categories", []),
        "excluded_links": int(summary.get("excluded_links", 0)),
        "scan_root": scan_run.get("root"),
        "scan_run_id": scan_run.get("run_id"),
        "status": "pass" if actionable_broken_links == 0 else "fail",
    }


def summarize_results(
    results: list[dict[str, Any]],
    *,
    broken_link_regression: dict[str, Any],
) -> dict[str, Any]:
    total = len(results)
    pass_count = sum(1 for item in results if item["status"] == "pass")
    retrieval_hits = sum(1 for item in results if item["retrieval_hit"])
    citation_count_passes = sum(1 for item in results if item["citation_count_ok"])
    citation_path_hits = sum(1 for item in results if item["citation_path_hit"])
    hint_hits = sum(1 for item in results if item["hint_hit"])
    recall_sum = sum(float(item["expected_path_recall"]) for item in results)
    by_category: dict[str, dict[str, Any]] = {}
    for category, items in group_by_category(results).items():
        count = len(items)
        category_passes = sum(1 for item in items if item["status"] == "pass")
        by_category[category] = {
            "pass_count": category_passes,
            "pass_rate": ratio(category_passes, count),
            "total_cases": count,
        }
    failure_counts = Counter(reason for item in results for reason in item["failure_reasons"])
    return {
        "average_expected_path_recall": round(recall_sum / total, 4) if total else 0.0,
        "actionable_broken_links": broken_link_regression["actionable_broken_links"],
        "broken_link_regression_status": broken_link_regression["status"],
        "by_category": dict(sorted(by_category.items())),
        "citation_count_pass_count": citation_count_passes,
        "citation_count_pass_rate": ratio(citation_count_passes, total),
        "citation_path_hit_count": citation_path_hits,
        "citation_path_hit_rate": ratio(citation_path_hits, total),
        "failure_counts": dict(sorted(failure_counts.items())),
        "hint_hit_count": hint_hits,
        "hint_hit_rate": ratio(hint_hits, total),
        "pass_count": pass_count,
        "pass_rate": ratio(pass_count, total),
        "query_pass_count": pass_count,
        "query_pass_rate": ratio(pass_count, total),
        "retrieval_hit_count": retrieval_hits,
        "retrieval_hit_rate": ratio(retrieval_hits, total),
        "total_cases": total,
    }


def write_eval_report(payload: dict[str, Any], *, report_dir: Path = DEFAULT_EVAL_REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"wiki_eval_{timestamp}.md"
    path.write_text(render_eval_report(payload))
    return path


def write_retrieval_profile_report(
    payload: dict[str, Any],
    *,
    report_dir: Path = DEFAULT_EVAL_REPORT_DIR,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"retrieval_profiles_{timestamp}.md"
    path.write_text(render_retrieval_profile_report(payload))
    return path


def write_eval_cleanup_report(
    payload: dict[str, Any],
    *,
    report_dir: Path = DEFAULT_EVAL_REPORT_DIR,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"eval_cleanup_targets_{timestamp}.md"
    path.write_text(render_eval_cleanup_report(payload))
    return path


def render_eval_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    regression = payload["broken_link_regression"]
    lines = [
        "# Wiki Eval Report",
        "",
        f"- started: `{payload['started_at_utc']}`",
        f"- ended: `{payload['ended_at_utc']}`",
        f"- eval file: `{payload['eval_file']}`",
        f"- status: `{payload['status']}`",
        "",
        "## Summary",
        "",
        f"- total cases: {summary['total_cases']}",
        f"- query pass rate: {summary['query_pass_rate']}",
        f"- retrieval hit rate: {summary['retrieval_hit_rate']}",
        f"- average expected-path recall: {summary['average_expected_path_recall']}",
        f"- citation count pass rate: {summary['citation_count_pass_rate']}",
        f"- citation path hit rate: {summary['citation_path_hit_rate']}",
        f"- hint hit rate: {summary['hint_hit_rate']}",
        "",
        "## Broken Link Regression",
        "",
        f"- status: {regression['status']}",
        f"- actionable broken links: {regression['actionable_broken_links']}",
        f"- excluded links: {regression['excluded_links']}",
        f"- scan run: `{regression['scan_run_id']}`",
        f"- scan root: `{regression['scan_root']}`",
        "",
        "| category | count |",
        "|---|---:|",
    ]
    if regression["categories"]:
        for item in regression["categories"]:
            lines.append(f"| {item['category']} | {item['count']} |")
    else:
        lines.append("| none | 0 |")
    lines.extend([
        "",
        "## Category Pass Rates",
        "",
        "| category | pass | total | pass rate |",
        "|---|---:|---:|---:|",
    ])
    for category, metrics in summary["by_category"].items():
        lines.append(
            f"| {category} | {metrics['pass_count']} | {metrics['total_cases']} | {metrics['pass_rate']} |"
        )
    failed = [item for item in payload["results"] if item["status"] != "pass"]
    lines.extend(["", "## Failed Cases", ""])
    if not failed:
        lines.append("All eval cases passed.")
    else:
        lines.extend(
            [
                "| query | category | reasons | expected paths | retrieved paths | citation paths |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in failed:
            lines.append(
                "| {query} | {category} | {reasons} | {expected} | {retrieved} | {citations} |".format(
                    category=item["category"],
                    citations="<br>".join(item["citation_paths"]),
                    expected="<br>".join(item["expected_paths"]),
                    query=str(item["query"]).replace("|", "\\|"),
                    reasons=", ".join(item["failure_reasons"]),
                    retrieved="<br>".join(item["retrieved_paths"][:5]),
                )
            )
    return "\n".join(lines) + "\n"


def render_retrieval_profile_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Retrieval Profile Comparison",
        "",
        f"- started: `{payload['started_at_utc']}`",
        f"- ended: `{payload['ended_at_utc']}`",
        f"- eval file: `{payload['eval_file']}`",
        f"- catalog db: `{payload['catalog_db']}`",
        f"- baseline profile: `{payload['baseline_profile']}`",
        f"- k: `{payload['k']}`",
        f"- status: `{payload['status']}`",
        "",
        "## Profile Metrics",
        "",
        "| profile | hit rate | recall | mrr | cases |",
        "|---|---:|---:|---:|---:|",
    ]
    for profile in payload["profiles"]:
        summary = profile["summary"]
        lines.append(
            "| {profile} | {hit} | {recall} | {mrr} | {cases} |".format(
                cases=summary["total_cases"],
                hit=summary["retrieval_hit_rate"],
                mrr=summary["mean_reciprocal_rank"],
                profile=profile["profile_id"],
                recall=summary["average_expected_path_recall"],
            )
        )
    lines.extend(
        [
            "",
            "## Baseline Deltas",
            "",
            "| profile | hit delta | recall delta | mrr delta | improvements | regressions |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for profile in payload["comparison"]["profiles"]:
        lines.append(
            "| {profile} | {hit} | {recall} | {mrr} | {improvements} | {regressions} |".format(
                hit=profile["retrieval_hit_rate_delta"],
                improvements=profile["improvement_count"],
                mrr=profile["mean_reciprocal_rank_delta"],
                profile=profile["profile_id"],
                recall=profile["average_expected_path_recall_delta"],
                regressions=profile["regression_count"],
            )
        )
    lines.extend(["", "## Recommendation", "", payload["recommendation"], ""])
    return "\n".join(lines)


def render_eval_cleanup_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Eval Cleanup Targets",
        "",
        f"- generated_at_utc: `{payload['generated_at_utc']}`",
        f"- eval file: `{payload['eval_file']}`",
        f"- catalog db: `{payload['catalog_db']}`",
        f"- profile: `{payload['profile']}`",
        f"- comparison profile: `{payload['comparison_profile']}`",
        f"- k: `{payload['k']}`",
        f"- target limit: `{payload['target_limit']}`",
        f"- status: `{payload['status']}`",
        "",
        "## Summary",
        "",
        f"- total candidate targets: {summary['total_candidate_targets']}",
        f"- emitted targets: {summary['emitted_target_count']}",
        f"- hidden targets: {summary['hidden_target_count']}",
        "",
        "## Action Breakdown",
        "",
        "| action | count |",
        "|---|---:|",
    ]
    if summary["action_counts"]:
        for action, count in summary["action_counts"].items():
            lines.append(f"| {markdown_cell(action)} | {count} |")
    else:
        lines.append("| none | 0 |")
    lines.extend(
        [
            "",
            "## Top Targets",
            "",
            "| priority | action | path | query | baseline rank | comparison rank | reasons |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    if not payload["targets"]:
        lines.append("| none | none | none | none | 0 | 0 | none |")
    for target in payload["targets"]:
        lines.append(
            "| {priority} | {action} | `{path}` | {query} | {baseline_rank} | {comparison_rank} | {reasons} |".format(
                action=markdown_cell(target["action"]),
                baseline_rank=target["baseline_rank"] if target["baseline_rank"] is not None else "missing",
                comparison_rank=target["comparison_rank"] if target["comparison_rank"] is not None else "missing",
                path=markdown_cell(str(target["path"])),
                priority=target["priority"],
                query=markdown_cell(str(target["query"])),
                reasons=markdown_cell(", ".join(target["reasons"])),
            )
        )
    lines.append("")
    return "\n".join(lines)


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|")


def scoring_text(harness_result: dict[str, Any], trace: dict[str, Any]) -> str:
    parts: list[str] = [str(harness_result.get("answer_markdown", ""))]
    for citation in harness_result.get("citations", []):
        parts.extend(
            [
                str(citation.get("artifact_id", "")),
                str(citation.get("quote", "")),
                str(citation.get("relevance_note", "")),
            ]
        )
    for candidate in trace.get("retrieval_candidates", []):
        parts.extend([str(candidate.get("path", "")), str(candidate.get("heading", ""))])
    return "\n".join(parts).lower()


def group_by_category(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        grouped[str(item["category"])].append(item)
    return grouped


def unique_strings(items: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
