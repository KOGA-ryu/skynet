from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from wiki_tool.catalog import DEFAULT_DB
from wiki_tool.harness import (
    DEFAULT_HARNESS_DB,
    DEFAULT_SPEC_DIR,
    get_harness_run,
    run_answer_with_citations,
)


DEFAULT_EVAL_FILE = Path("eval/wiki_queries_v1.jsonl")
DEFAULT_EVAL_REPORT_DIR = Path("state/eval_reports")
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
    summary = summarize_results(results)
    payload: dict[str, Any] = {
        "ended_at_utc": ended_at,
        "eval_file": str(eval_file),
        "harness_db": str(harness_db),
        "results": results,
        "started_at_utc": started_at,
        "status": "pass" if summary["pass_count"] == summary["total_cases"] else "fail",
        "summary": summary,
    }
    if write_report:
        report_path = write_eval_report(payload, report_dir=report_dir)
        payload["report_path"] = str(report_path)
    return payload


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


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
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


def render_eval_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
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
        f"- pass rate: {summary['pass_rate']}",
        f"- retrieval hit rate: {summary['retrieval_hit_rate']}",
        f"- average expected-path recall: {summary['average_expected_path_recall']}",
        f"- citation count pass rate: {summary['citation_count_pass_rate']}",
        f"- citation path hit rate: {summary['citation_path_hit_rate']}",
        f"- hint hit rate: {summary['hint_hit_rate']}",
        "",
        "## Category Pass Rates",
        "",
        "| category | pass | total | pass rate |",
        "|---|---:|---:|---:|",
    ]
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
