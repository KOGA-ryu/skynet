from pathlib import Path
from contextlib import closing
from contextlib import redirect_stdout
import io
import json
import sqlite3
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.eval import (
    compare_profile_reports,
    compare_retrieval_profiles,
    eval_cleanup_targets,
    export_training_examples,
    run_eval,
)


ROOT = Path(__file__).parents[1]
EVAL_FILE = ROOT / "eval" / "wiki_queries_v1.jsonl"
CATALOG_DB = ROOT / "state" / "catalog.sqlite"
FIXTURE = ROOT / "tests" / "fixtures" / "sample_wiki"
ALLOWED_CATEGORIES = {"concept", "project", "source", "operation", "template", "fallback"}
ALLOWED_BUCKETS = {
    "adversarial_citation",
    "ambiguous_retrieval",
    "contradiction_handling",
    "multi_document_synthesis",
    "straight_retrieval",
    "unsupported_refusal",
}
ALLOWED_SPLITS = {"dev", "holdout"}
ALLOWED_EXPECTED_OUTCOMES = {"answer", "refuse"}


class EvalDatasetTests(unittest.TestCase):
    def test_wiki_queries_v1_schema(self) -> None:
        rows = load_eval_rows()
        self.assertGreaterEqual(len(rows), 30)
        queries = [row["query"] for row in rows]
        self.assertEqual(len(queries), len(set(queries)))

        categories = {row["category"] for row in rows}
        self.assertLessEqual(categories, ALLOWED_CATEGORIES)
        self.assertTrue(ALLOWED_CATEGORIES <= categories)
        splits = {row["split"] for row in rows}
        self.assertEqual(splits, ALLOWED_SPLITS)
        buckets = {row["bucket"] for row in rows}
        self.assertLessEqual(buckets, ALLOWED_BUCKETS)
        self.assertTrue(
            {
                "adversarial_citation",
                "ambiguous_retrieval",
                "contradiction_handling",
                "multi_document_synthesis",
                "unsupported_refusal",
            }
            <= buckets
        )
        self.assertIn("refuse", {row["expected_outcome"] for row in rows})
        self.assertGreaterEqual(sum(1 for row in rows if row["gold_claim_units"]), 10)

        for row in rows:
            self.assertIsInstance(row["query"], str)
            self.assertTrue(row["query"].strip())
            self.assertIn(row["split"], ALLOWED_SPLITS)
            self.assertIn(row["bucket"], ALLOWED_BUCKETS)
            self.assertIn(row["expected_outcome"], ALLOWED_EXPECTED_OUTCOMES)
            self.assertIsInstance(row["expected_paths"], list)
            self.assertTrue(row["expected_paths"])
            self.assertIsInstance(row["expected_hints"], list)
            self.assertTrue(row["expected_hints"])
            self.assertIsInstance(row["gold_claim_units"], list)
            self.assertIsInstance(row["min_citations"], int)
            self.assertGreaterEqual(row["min_citations"], 1)
            for path in row["expected_paths"]:
                self.assertIsInstance(path, str)
                self.assertTrue(path.endswith(".md"))
                self.assertFalse(path.startswith("/"))
            for hint in row["expected_hints"]:
                self.assertIsInstance(hint, str)
                self.assertTrue(hint.strip())
            for claim in row["gold_claim_units"]:
                self.assertIsInstance(claim, str)
                self.assertTrue(claim.strip())

    def test_expected_paths_exist_in_local_catalog_when_available(self) -> None:
        if not CATALOG_DB.exists():
            self.skipTest("state/catalog.sqlite is generated local state")
        rows = load_eval_rows()
        with closing(sqlite3.connect(CATALOG_DB)) as con:
            known_paths = {row[0] for row in con.execute("SELECT path FROM documents")}
        expected_paths = {path for row in rows for path in row["expected_paths"]}
        self.assertFalse(sorted(expected_paths - known_paths))

    def test_run_eval_scores_passing_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            result = run_eval(eval_file=eval_file, catalog_db=catalog_db, harness_db=harness_db)

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["summary"]["total_cases"], 1)
            self.assertEqual(result["summary"]["pass_count"], 1)
            self.assertEqual(result["summary"]["query_pass_count"], 1)
            self.assertEqual(result["summary"]["retrieval_hit_count"], 1)
            self.assertEqual(result["summary"]["broken_link_regression_status"], "pass")
            self.assertEqual(result["broken_link_regression"]["actionable_broken_links"], 0)
            self.assertEqual(result["results"][0]["status"], "pass")

    def test_run_eval_scores_retrieval_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="zzzz no matching fixture term",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                        min_citations=1,
                    )
                ],
            )

            result = run_eval(eval_file=eval_file, catalog_db=catalog_db, harness_db=harness_db)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["summary"]["failure_counts"]["retrieval_miss"], 1)
            self.assertEqual(result["summary"]["failure_counts"]["citation_count"], 1)
            self.assertEqual(
                set(result["results"][0]["failure_reasons"]),
                {"retrieval_miss", "citation_count"},
            )

    def test_run_eval_scores_citation_count_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                        min_citations=99,
                    )
                ],
            )

            result = run_eval(eval_file=eval_file, catalog_db=catalog_db, harness_db=harness_db)

            self.assertEqual(result["status"], "fail")
            self.assertTrue(result["results"][0]["retrieval_hit"])
            self.assertFalse(result["results"][0]["citation_count_ok"])
            self.assertEqual(result["results"][0]["failure_reasons"], ["citation_count"])

    def test_run_eval_fails_on_actionable_broken_link_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            result = run_eval(eval_file=eval_file, catalog_db=catalog_db, harness_db=harness_db)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["results"][0]["status"], "pass")
            self.assertEqual(result["broken_link_regression"]["status"], "fail")
            self.assertEqual(result["broken_link_regression"]["actionable_broken_links"], 1)
            self.assertEqual(result["summary"]["actionable_broken_links"], 1)
            self.assertEqual(result["summary"]["broken_link_regression_status"], "fail")

    def test_run_eval_excludes_template_placeholders_from_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_template_placeholder_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            result = run_eval(eval_file=eval_file, catalog_db=catalog_db, harness_db=harness_db)

            self.assertEqual(result["status"], "pass")
            regression = result["broken_link_regression"]
            self.assertEqual(regression["status"], "pass")
            self.assertEqual(regression["actionable_broken_links"], 0)
            self.assertEqual(regression["excluded_links"], 1)
            self.assertEqual(regression["categories"], [{"category": "template_placeholder", "count": 1}])

    def test_run_eval_writes_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_template_placeholder_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )
            report_dir = Path(tmp) / "reports"

            result = run_eval(
                eval_file=eval_file,
                catalog_db=catalog_db,
                harness_db=harness_db,
                write_report=True,
                report_dir=report_dir,
            )

            report_path = Path(result["report_path"])
            self.assertTrue(report_path.exists())
            self.assertEqual(report_path.parent, report_dir)
            report = report_path.read_text()
            self.assertIn("# Wiki Eval Report", report)
            self.assertIn("## Broken Link Regression", report)
            self.assertIn("| template_placeholder | 1 |", report)

    def test_cli_help_exposes_eval_run(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["eval", "run", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("--eval-file", help_text)
        self.assertIn("--write-report", help_text)
        self.assertIn("--split", help_text)
        self.assertIn("--synthesis", help_text)

    def test_run_eval_filters_by_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                        split="dev",
                    ),
                    eval_case(
                        query="scanner evidence",
                        expected_paths=["projects/demo/README.md"],
                        expected_hints=["Scanner Evidence"],
                        split="holdout",
                    ),
                ],
            )

            result = run_eval(
                eval_file=eval_file,
                catalog_db=catalog_db,
                harness_db=harness_db,
                split="dev",
            )

            self.assertEqual(result["summary"]["total_cases"], 1)
            self.assertEqual(result["results"][0]["split"], "dev")

    def test_export_training_examples_excludes_eval_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                        split="holdout",
                    )
                ],
            )
            run_eval(
                eval_file=eval_file,
                catalog_db=catalog_db,
                harness_db=harness_db,
                split="holdout",
            )
            run_eval(
                eval_file=write_named_eval_file(
                    tmp,
                    "train_eval.jsonl",
                    [
                        eval_case(
                            query="scanner evidence",
                            expected_paths=["projects/demo/README.md"],
                            expected_hints=["Scanner Evidence"],
                        )
                    ],
                ),
                catalog_db=catalog_db,
                harness_db=harness_db,
            )
            output_path = Path(tmp) / "training.jsonl"

            result = export_training_examples(
                eval_file=eval_file,
                catalog_db=catalog_db,
                harness_db=harness_db,
                output_path=output_path,
            )

            lines = [json.loads(line) for line in output_path.read_text().splitlines() if line.strip()]
            self.assertEqual(result["exported_example_count"], 1)
            self.assertEqual(lines[0]["query"], "scanner evidence")

    def test_compare_retrieval_profiles_scores_selected_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            result = compare_retrieval_profiles(
                eval_file=eval_file,
                catalog_db=catalog_db,
                profile_ids=["catalog.fts_documents.primary"],
                k=4,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(
                [profile["profile_id"] for profile in result["profiles"]],
                ["catalog.fts_spans.primary", "catalog.fts_documents.primary"],
            )
            self.assertEqual(result["profiles"][0]["summary"]["total_cases"], 1)
            self.assertTrue(result["profiles"][0]["results"][0]["retrieval_hit"])
            self.assertEqual(result["comparison"]["baseline_profile"], "catalog.fts_spans.primary")

    def test_compare_retrieval_profiles_writes_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )
            report_dir = Path(tmp) / "reports"

            result = compare_retrieval_profiles(
                eval_file=eval_file,
                catalog_db=catalog_db,
                profile_ids=["catalog.fts_documents.primary"],
                write_report=True,
                report_dir=report_dir,
            )

            report_path = Path(result["report_path"])
            self.assertTrue(report_path.exists())
            self.assertEqual(report_path.parent, report_dir)
            report = report_path.read_text()
            self.assertIn("# Retrieval Profile Comparison", report)
            self.assertIn("catalog.fts_documents.primary", report)

    def test_compare_profile_reports_classifies_improvements_regressions_and_unchanged(self) -> None:
        baseline = profile_report(
            "baseline",
            [
                profile_result("q1", recall=0.5, reciprocal_rank=0.5, top_expected_rank=2),
                profile_result("q2", recall=1.0, reciprocal_rank=1.0, top_expected_rank=1),
                profile_result("q3", recall=0.0, reciprocal_rank=0.0, top_expected_rank=None),
            ],
        )
        candidate = profile_report(
            "candidate",
            [
                profile_result("q1", recall=1.0, reciprocal_rank=1.0, top_expected_rank=1),
                profile_result("q2", recall=0.5, reciprocal_rank=0.5, top_expected_rank=2),
                profile_result("q3", recall=0.0, reciprocal_rank=0.0, top_expected_rank=None),
            ],
        )

        comparison = compare_profile_reports(baseline, [baseline, candidate])
        candidate_report = comparison["profiles"][0]

        self.assertEqual(candidate_report["improvements"], ["q1"])
        self.assertEqual(candidate_report["regressions"], ["q2"])
        self.assertEqual(candidate_report["unchanged_count"], 1)

    def test_cli_help_exposes_eval_compare_profiles(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["eval", "compare-profiles", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("--profiles", help_text)
        self.assertIn("--baseline-profile", help_text)

    def test_eval_cleanup_targets_lists_missing_expected_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/missing.md"],
                        expected_hints=["Missing"],
                    )
                ],
            )

            result = eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db)

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["summary"]["target_count"], 1)
            target = result["targets"][0]
            self.assertEqual(target["action"], "review_eval_gold_target")
            self.assertEqual(target["priority"], "P0")
            self.assertFalse(target["catalog_present"])
            self.assertIn("expected_path_missing_from_catalog", target["reasons"])

    def test_eval_cleanup_targets_prioritizes_generated_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog_from_root(write_generated_stub_wiki_root(Path(tmp) / "wiki"), tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="zzzz no matching fixture term",
                        expected_paths=["concepts/stub.md"],
                        expected_hints=["stub"],
                    )
                ],
            )

            result = eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db)

            target = result["targets"][0]
            self.assertEqual(target["action"], "fill_generated_stub")
            self.assertEqual(target["priority"], "P0")
            self.assertIn("generated_stub", target["quality_flags"])

    def test_eval_cleanup_targets_maps_weak_summary_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="zzzz no matching fixture term",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            result = eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db)

            target = result["targets"][0]
            self.assertEqual(target["action"], "add_opening_summary")
            self.assertIn("missing_summary", target["quality_flags"])

    def test_eval_cleanup_targets_maps_thin_note_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog_from_root(write_thin_note_wiki_root(Path(tmp) / "wiki"), tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="zzzz no matching fixture term",
                        expected_paths=["concepts/thin.md"],
                        expected_hints=["Thin"],
                    )
                ],
            )

            result = eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db)

            target = result["targets"][0]
            self.assertEqual(target["action"], "expand_thin_note")
            self.assertIn("thin_note", target["quality_flags"])
            self.assertNotIn("missing_summary", target["quality_flags"])

    def test_eval_cleanup_targets_records_comparison_profile_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval absenttoken",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            result = eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db)

            target = result["targets"][0]
            self.assertIsNone(target["baseline_rank"])
            self.assertIsNotNone(target["comparison_rank"])
            self.assertTrue(target["comparison_profile_recovered"])
            self.assertIn("comparison_profile_recovers_path", target["reasons"])

    def test_eval_cleanup_targets_writes_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="zzzz no matching fixture term",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )
            report_dir = Path(tmp) / "reports"

            result = eval_cleanup_targets(
                eval_file=eval_file,
                catalog_db=catalog_db,
                write_report=True,
                report_dir=report_dir,
            )

            report_path = Path(result["report_path"])
            self.assertTrue(report_path.exists())
            self.assertEqual(report_path.parent, report_dir)
            report = report_path.read_text()
            self.assertIn("# Eval Cleanup Targets", report)
            self.assertIn("add_opening_summary", report)

    def test_eval_cleanup_targets_rejects_invalid_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, _ = build_clean_fixture_catalog(tmp)
            eval_file = write_eval_file(
                tmp,
                [
                    eval_case(
                        query="retrieval",
                        expected_paths=["concepts/retrieval.md"],
                        expected_hints=["Retrieval"],
                    )
                ],
            )

            with self.assertRaisesRegex(ValueError, "k must be greater than or equal to 1"):
                eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db, k=0)
            with self.assertRaisesRegex(ValueError, "unknown retrieval profiles"):
                eval_cleanup_targets(eval_file=eval_file, catalog_db=catalog_db, profile="missing.profile")

    def test_cli_help_exposes_eval_cleanup_targets(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["eval", "cleanup-targets", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("--comparison-profile", help_text)
        self.assertIn("--target-limit", help_text)


def load_eval_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(EVAL_FILE.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{EVAL_FILE}:{line_number}: invalid JSON: {exc}") from exc
        required = {"category", "expected_hints", "expected_paths", "min_citations", "query"}
        missing = required - set(row)
        if missing:
            raise AssertionError(f"{EVAL_FILE}:{line_number}: missing keys: {sorted(missing)}")
        rows.append(row)
    return rows


def build_fixture_catalog(tmp: str) -> tuple[Path, Path]:
    catalog_db = Path(tmp) / "catalog.sqlite"
    harness_db = Path(tmp) / "harness.sqlite"
    scan_wiki(FIXTURE, catalog_db)
    return catalog_db, harness_db


def build_clean_fixture_catalog(tmp: str) -> tuple[Path, Path]:
    root = write_clean_wiki_root(Path(tmp) / "wiki")
    catalog_db = Path(tmp) / "catalog.sqlite"
    harness_db = Path(tmp) / "harness.sqlite"
    scan_wiki(root, catalog_db)
    return catalog_db, harness_db


def build_template_placeholder_catalog(tmp: str) -> tuple[Path, Path]:
    root = write_clean_wiki_root(Path(tmp) / "wiki")
    templates = root / "templates"
    templates.mkdir(parents=True)
    (templates / "example.md").write_text("# Template\n\n[Path](<path>)\n")
    catalog_db = Path(tmp) / "catalog.sqlite"
    harness_db = Path(tmp) / "harness.sqlite"
    scan_wiki(root, catalog_db)
    return catalog_db, harness_db


def build_catalog_from_root(root: Path, tmp: str) -> Path:
    catalog_db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, catalog_db)
    return catalog_db


def write_clean_wiki_root(root: Path) -> Path:
    (root / "concepts").mkdir(parents=True)
    (root / "projects" / "demo").mkdir(parents=True)
    (root / "index.md").write_text(
        "# Sample Wiki\n\nStart at [Retrieval](concepts/retrieval.md) and [[Scanner Hub]].\n"
    )
    (root / "concepts" / "retrieval.md").write_text(
        "# Retrieval\n\nRetrieval resolves questions to evidence.\n\n"
        "## Symbol First\n\nPrefer symbols and spans before full file reads.\n"
    )
    (root / "projects" / "demo" / "README.md").write_text(
        "# Scanner Hub\n\nSee [[Retrieval]] for the read guard idea.\n\n"
        "## Scanner Evidence\n\nSnapshots should explain why a symbol fired.\n"
    )
    return root


def write_generated_stub_wiki_root(root: Path) -> Path:
    (root / "concepts").mkdir(parents=True)
    (root / "index.md").write_text("# Sample Wiki\n\nSee [[Stub]].\n")
    (root / "concepts" / "stub.md").write_text(
        "# Stub\n\n"
        "This stub exists because current wiki notes link to this page.\n\n"
        "- status: stub\n\n"
        "Content has not been filled in yet.\n"
    )
    return root


def write_thin_note_wiki_root(root: Path) -> Path:
    (root / "concepts").mkdir(parents=True)
    (root / "index.md").write_text("# Sample Wiki\n\nSee [[Thin]].\n")
    (root / "concepts" / "thin.md").write_text(
        "# Thin\n\n"
        "This opening summary contains more than twenty five words so the page is not classified as missing a "
        "summary but it remains too short to stand alone for useful research.\n"
    )
    return root


def write_eval_file(tmp: str, rows: list[dict[str, object]]) -> Path:
    return write_named_eval_file(tmp, "eval.jsonl", rows)


def write_named_eval_file(tmp: str, name: str, rows: list[dict[str, object]]) -> Path:
    path = Path(tmp) / name
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")
    return path


def eval_case(
    *,
    query: str,
    expected_paths: list[str],
    expected_hints: list[str],
    min_citations: int = 2,
    split: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "category": "concept",
        "expected_hints": expected_hints,
        "expected_paths": expected_paths,
        "min_citations": min_citations,
        "query": query,
    }
    if split is not None:
        row["split"] = split
    return row


def profile_report(profile_id: str, results: list[dict[str, object]]) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "results": results,
        "summary": {
            "average_expected_path_recall": sum(float(item["expected_path_recall"]) for item in results)
            / len(results),
            "mean_reciprocal_rank": sum(float(item["reciprocal_rank"]) for item in results) / len(results),
            "retrieval_hit_rate": sum(1 for item in results if item["retrieval_hit"]) / len(results),
        },
    }


def profile_result(
    query: str,
    *,
    recall: float,
    reciprocal_rank: float,
    top_expected_rank: int | None,
) -> dict[str, object]:
    return {
        "expected_path_recall": recall,
        "query": query,
        "reciprocal_rank": reciprocal_rank,
        "retrieval_hit": recall > 0,
        "top_expected_rank": top_expected_rank,
    }


if __name__ == "__main__":
    unittest.main()
