from pathlib import Path
import sqlite3
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.harness import (
    extract_yaml_blocks,
    get_harness_run,
    list_harness_runs,
    parse_yaml_subset,
    run_answer_with_citations,
    validate_harness_specs,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sample_wiki"
SPEC_DIR = Path(__file__).parents[1] / "harness_specs"


class HarnessTests(unittest.TestCase):
    def test_extract_and_parse_yaml_blocks(self) -> None:
        text = """# Specs

```yaml
kind: task_contract
id: demo
version: 1
description: >
  folded text
tools_allowed:
  - retriever.search
```
"""
        blocks = extract_yaml_blocks(text)
        self.assertEqual(len(blocks), 1)
        parsed = parse_yaml_subset(blocks[0])
        self.assertEqual(parsed["kind"], "task_contract")
        self.assertEqual(parsed["tools_allowed"], ["retriever.search"])
        self.assertEqual(parsed["description"], "folded text")

    def test_default_specs_validate(self) -> None:
        validation = validate_harness_specs(SPEC_DIR)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["spec_count"], 3)

    def test_answer_harness_persists_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
            )

            self.assertEqual(result["status"], "pass")
            self.assertGreaterEqual(len(result["citations"]), 2)
            self.assertTrue(harness_db.exists())

            with sqlite3.connect(harness_db) as con:
                run_count = con.execute("SELECT COUNT(*) FROM harness_runs").fetchone()[0]
                step_count = con.execute("SELECT COUNT(*) FROM harness_steps").fetchone()[0]
                candidate_count = con.execute(
                    "SELECT COUNT(*) FROM harness_retrieval_candidates"
                ).fetchone()[0]
            self.assertEqual(run_count, 1)
            self.assertEqual(step_count, 5)
            self.assertGreaterEqual(candidate_count, 2)

            runs = list_harness_runs(harness_db)
            self.assertEqual(len(runs["runs"]), 1)
            shown = get_harness_run(result["run_id"], harness_db)
            self.assertEqual(shown["run"]["run_id"], result["run_id"])
            self.assertEqual(len(shown["steps"]), 5)
            self.assertGreaterEqual(len(shown["retrieval_candidates"]), 2)

    def test_answer_harness_fails_closed_without_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            result = run_answer_with_citations(
                "zzzz no matching fixture term",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(
                {item["failure_code"] for item in result["failures"]},
                {"RETRIEVAL_EMPTY", "GROUNDEDNESS_FAIL"},
            )


if __name__ == "__main__":
    unittest.main()
