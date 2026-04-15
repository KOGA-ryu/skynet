from pathlib import Path
from contextlib import closing, redirect_stdout
import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.harness import (
    extract_yaml_blocks,
    get_harness_run,
    list_harness_runs,
    parse_yaml_subset,
    run_answer_with_citations,
    validate_harness_specs,
)
from wiki_tool.llm import StructuredSynthesisAdapter, SynthesisResult


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

            with closing(sqlite3.connect(harness_db)) as con:
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
            self.assertEqual(shown["steps"][2]["step_type"], "deterministic")
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

    def test_answer_harness_accepts_schema_valid_structured_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=FakeValidAdapter(),
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["synthesis"]["provider"], "fake")
            shown = get_harness_run(result["run_id"], harness_db)
            synth_step = shown["steps"][2]
            self.assertEqual(synth_step["step_type"], "llm")
            self.assertEqual(synth_step["tool_name"], "llm.structured_synthesis")
            self.assertTrue(synth_step["output"]["schema_valid"])
            self.assertEqual(shown["run"]["metrics"]["synthesis_provider"], "fake")

    def test_answer_harness_rejects_malformed_structured_adapter_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=FakeMalformedAdapter(),
            )

            self.assertEqual(result["status"], "fail")
            self.assertIn(
                "OUTPUT_SCHEMA_INVALID",
                {item["failure_code"] for item in result["failures"]},
            )

    def test_answer_harness_rejects_unknown_citation_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=FakeUnknownCitationAdapter(),
            )

            self.assertEqual(result["status"], "fail")
            self.assertIn(
                "GROUNDEDNESS_FAIL",
                {item["failure_code"] for item in result["failures"]},
            )

    def test_openai_synthesis_without_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                result = run_answer_with_citations(
                    "retrieval",
                    catalog_db=catalog_db,
                    harness_db=harness_db,
                    spec_dir=SPEC_DIR,
                    synthesis="openai",
                    llm_model="fake-model",
                )

            self.assertEqual(result["status"], "fail")
            self.assertIn(
                "LLM_PROVIDER_CONFIG_MISSING",
                {item["failure_code"] for item in result["failures"]},
            )

    def test_cli_help_exposes_synthesis_controls(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["harness", "answer", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("--synthesis", help_text)
        self.assertIn("--llm-model", help_text)


class FakeValidAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        citations = [
            {
                "artifact_id": chunk["artifact_id"],
                "chunk_id": chunk["chunk_id"],
                "quote": chunk["text"].splitlines()[0],
                "relevance_note": "fake structured synthesis citation",
            }
            for chunk in chunks[:min_citations]
        ]
        return SynthesisResult(
            output={"answer_markdown": "Fake structured answer.", "citations": citations},
            metadata={"model": "fake-model", "provider": self.provider, "token_usage": {"total_tokens": 7}},
        )


class FakeMalformedAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        return SynthesisResult(
            output={"answer_markdown": "Missing citations."},
            metadata={"model": "fake-model", "provider": self.provider, "token_usage": None},
        )


class FakeUnknownCitationAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        return SynthesisResult(
            output={
                "answer_markdown": "Fake answer with bad citation.",
                "citations": [
                    {
                        "artifact_id": "missing.md",
                        "chunk_id": "span:missing",
                        "quote": "not present",
                        "relevance_note": "bad citation",
                    }
                ],
            },
            metadata={"model": "fake-model", "provider": self.provider, "token_usage": None},
        )


if __name__ == "__main__":
    unittest.main()
