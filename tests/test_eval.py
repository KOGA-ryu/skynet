from pathlib import Path
from contextlib import closing
import json
import sqlite3
import unittest


ROOT = Path(__file__).parents[1]
EVAL_FILE = ROOT / "eval" / "wiki_queries_v1.jsonl"
CATALOG_DB = ROOT / "state" / "catalog.sqlite"
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


if __name__ == "__main__":
    unittest.main()
