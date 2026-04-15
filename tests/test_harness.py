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
    failure_actions_for,
    get_harness_run,
    list_harness_runs,
    load_specs,
    parse_yaml_subset,
    run_answer_with_citations,
    validate_harness_specs,
)
from wiki_tool.llm import StructuredSynthesisAdapter, StructuredSynthesisError, SynthesisResult


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

    def test_failure_taxonomy_resolves_actions_in_order(self) -> None:
        registry = load_specs(SPEC_DIR)
        actions = failure_actions_for(
            registry,
            step_id="s3_synthesize",
            failure_code="OUTPUT_SCHEMA_INVALID",
            status="planned",
            reason="test",
        )
        self.assertEqual([item["action"] for item in actions], ["retry", "abort"])
        self.assertEqual({item["source_failure_code"] for item in actions}, {"OUTPUT_SCHEMA_INVALID"})

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
            self.assertIn(
                ("RETRIEVAL_EMPTY", "expand_retrieval", "deferred"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in result["failure_actions"]
                },
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
            adapter = FakeMalformedAdapter()

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=adapter,
            )

            self.assertEqual(result["status"], "fail")
            self.assertEqual(adapter.calls, 2)
            self.assertIn(
                "OUTPUT_SCHEMA_INVALID",
                {item["failure_code"] for item in result["failures"]},
            )
            self.assertIn(
                ("OUTPUT_SCHEMA_INVALID", "retry", "applied"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in result["failure_actions"]
                },
            )

    def test_schema_invalid_synthesis_retries_once_and_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)
            adapter = FakeSchemaRetryAdapter()

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=adapter,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(adapter.calls, 2)
            self.assertEqual(result["failures"], [])
            shown = get_harness_run(result["run_id"], harness_db)
            synth_output = shown["steps"][2]["output"]
            self.assertEqual([item["status"] for item in synth_output["synthesis_attempts"]], ["failed", "ok"])
            self.assertIn(
                ("OUTPUT_SCHEMA_INVALID", "retry", "applied"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in synth_output["failure_actions"]
                },
            )

    def test_llm_synthesis_error_retries_once_and_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)
            adapter = FakeTransientErrorAdapter()

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=adapter,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(adapter.calls, 2)
            self.assertEqual(result["failures"], [])
            self.assertIn(
                ("LLM_SYNTHESIS_ERROR", "retry", "applied"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in result["failure_actions"]
                },
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
            self.assertIn(
                ("GROUNDEDNESS_FAIL", "expand_retrieval", "deferred"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in result["failure_actions"]
                },
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
            self.assertEqual(
                [item["action"] for item in result["failure_actions"]],
                ["abort"],
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
        return fake_valid_synthesis_result(chunks, min_citations)


class FakeMalformedAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        self.calls += 1
        return SynthesisResult(
            output={"answer_markdown": "Missing citations."},
            metadata={"model": "fake-model", "provider": self.provider, "token_usage": None},
        )


class FakeSchemaRetryAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        self.calls += 1
        if self.calls == 1:
            return SynthesisResult(
                output={"answer_markdown": "Missing citations."},
                metadata={"model": "fake-model", "provider": self.provider, "token_usage": None},
            )
        return fake_valid_synthesis_result(chunks, min_citations)


class FakeTransientErrorAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        self.calls += 1
        if self.calls == 1:
            raise StructuredSynthesisError("temporary structured synthesis failure")
        return fake_valid_synthesis_result(chunks, min_citations)


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


def fake_valid_synthesis_result(chunks, min_citations):
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
        metadata={"model": "fake-model", "provider": "fake", "token_usage": {"total_tokens": 7}},
    )


if __name__ == "__main__":
    unittest.main()
