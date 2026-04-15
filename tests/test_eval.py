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
from wiki_tool.eval import run_eval


ROOT = Path(__file__).parents[1]
EVAL_FILE = ROOT / "eval" / "wiki_queries_v1.jsonl"
CATALOG_DB = ROOT / "state" / "catalog.sqlite"
FIXTURE = ROOT / "tests" / "fixtures" / "sample_wiki"
ALLOWED_CATEGORIES = {"concept", "project", "source", "operation", "template", "fallback"}


class EvalDatasetTests(unittest.TestCase):
    def test_wiki_queries_v1_schema(self) -> None:
        rows = load_eval_rows()
        self.assertGreaterEqual(len(rows), 30)
        queries = [row["query"] for row in rows]
        self.assertEqual(len(queries), len(set(queries)))

        categories = {row["category"] for row in rows}
        self.assertLessEqual(categories, ALLOWED_CATEGORIES)
        self.assertTrue(ALLOWED_CATEGORIES <= categories)

        for row in rows:
            self.assertIsInstance(row["query"], str)
            self.assertTrue(row["query"].strip())
            self.assertIsInstance(row["expected_paths"], list)
            self.assertTrue(row["expected_paths"])
            self.assertIsInstance(row["expected_hints"], list)
            self.assertTrue(row["expected_hints"])
            self.assertIsInstance(row["min_citations"], int)
            self.assertGreaterEqual(row["min_citations"], 1)
            for path in row["expected_paths"]:
                self.assertIsInstance(path, str)
                self.assertTrue(path.endswith(".md"))
                self.assertFalse(path.startswith("/"))
            for hint in row["expected_hints"]:
                self.assertIsInstance(hint, str)
                self.assertTrue(hint.strip())

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

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["summary"]["total_cases"], 1)
            self.assertEqual(result["summary"]["pass_count"], 1)
            self.assertEqual(result["summary"]["retrieval_hit_count"], 1)
            self.assertEqual(result["results"][0]["status"], "pass")

    def test_run_eval_scores_retrieval_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db, harness_db = build_fixture_catalog(tmp)
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
            catalog_db, harness_db = build_fixture_catalog(tmp)
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

    def test_run_eval_writes_markdown_report(self) -> None:
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
            self.assertIn("# Wiki Eval Report", report_path.read_text())

    def test_cli_help_exposes_eval_run(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["eval", "run", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("--eval-file", help_text)
        self.assertIn("--write-report", help_text)


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


def write_eval_file(tmp: str, rows: list[dict[str, object]]) -> Path:
    path = Path(tmp) / "eval.jsonl"
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")
    return path


def eval_case(
    *,
    query: str,
    expected_paths: list[str],
    expected_hints: list[str],
    min_citations: int = 2,
) -> dict[str, object]:
    return {
        "category": "concept",
        "expected_hints": expected_hints,
        "expected_paths": expected_paths,
        "min_citations": min_citations,
        "query": query,
    }


if __name__ == "__main__":
    unittest.main()
