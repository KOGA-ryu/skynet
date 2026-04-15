from pathlib import Path
from contextlib import closing, redirect_stdout
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.harness import (
    build_fallback_search_queries,
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

    def test_contract_validation_requires_core_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = base_task_contract()
            del contract["outputs"]
            spec_dir = write_spec_dir(tmp, contract=contract)

            validation = validate_harness_specs(spec_dir)

            self.assertFalse(validation["valid"])
            self.assertIn("missing contract sections: outputs", "\n".join(validation["errors"]))

    def test_contract_validation_rejects_bad_output_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = base_task_contract()
            contract["outputs"]["citations"]["type"] = "bag"
            spec_dir = write_spec_dir(tmp, contract=contract)

            validation = validate_harness_specs(spec_dir)

            self.assertFalse(validation["valid"])
            self.assertIn("wiki.answer_with_citations.outputs.citations.type", "\n".join(validation["errors"]))

    def test_reasoning_chain_validation_rejects_duplicate_steps_and_bad_tool_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chain = base_reasoning_chain()
            chain["steps"] = [
                {"id": "s1_plan", "type": "deterministic"},
                {"id": "s1_plan", "type": "tool"},
                {"id": "s3_synthesize", "type": "unknown"},
            ]
            spec_dir = write_spec_dir(tmp, chain=chain)

            validation = validate_harness_specs(spec_dir)
            error_text = "\n".join(validation["errors"])

            self.assertFalse(validation["valid"])
            self.assertIn("duplicate step id s1_plan", error_text)
            self.assertIn("tool_name is required for tool steps", error_text)
            self.assertIn("steps[2].type must be one of", error_text)

    def test_failure_taxonomy_validation_rejects_unknown_values_and_missing_runtime_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            taxonomy = base_failure_taxonomy()
            taxonomy["enums"]["action"].append("abort")
            taxonomy["failures"] = [
                item for item in taxonomy["failures"] if item["code"] != "TOOL_CALL_ERROR"
            ]
            taxonomy["failures"][0]["severity"] = "urgent"
            taxonomy["failures"][0]["respond"][0]["action"] = "panic"
            spec_dir = write_spec_dir(tmp, taxonomy=taxonomy)

            validation = validate_harness_specs(spec_dir)
            error_text = "\n".join(validation["errors"])

            self.assertFalse(validation["valid"])
            self.assertIn("enums.action has duplicate value abort", error_text)
            self.assertIn("uses unknown severity urgent", error_text)
            self.assertIn("uses unknown response action panic", error_text)
            self.assertIn("missing runtime failure codes: TOOL_CALL_ERROR", error_text)

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

    def test_fallback_search_queries_expand_to_or_and_terms(self) -> None:
        self.assertEqual(
            build_fallback_search_queries("what retrieves absenttoken retrieval"),
            ["retrieves OR absenttoken OR retrieval", "retrieves", "absenttoken", "retrieval"],
        )

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
                ("RETRIEVAL_EMPTY", "expand_retrieval", "applied"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in result["failure_actions"]
                },
            )
            shown = get_harness_run(result["run_id"], harness_db)
            self.assertEqual(shown["steps"][2]["step_id"], "s2b_retrieve_fallback")
            self.assertEqual(shown["steps"][2]["status"], "failed")
            self.assertEqual(shown["run"]["metrics"]["retrieval_fallback_hit_count"], 0)

    def test_answer_harness_uses_retrieval_fallback_after_primary_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)

            result = run_answer_with_citations(
                "retrieval absenttoken",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=SPEC_DIR,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["failures"], [])
            self.assertIn(
                ("RETRIEVAL_EMPTY", "expand_retrieval", "applied"),
                {
                    (item["source_failure_code"], item["action"], item["status"])
                    for item in result["failure_actions"]
                },
            )
            shown = get_harness_run(result["run_id"], harness_db)
            self.assertEqual([step["step_id"] for step in shown["steps"]], [
                "s1_plan",
                "s2_retrieve",
                "s2b_retrieve_fallback",
                "s3_synthesize",
                "s4_verify_groundedness",
                "s5_persist",
            ])
            self.assertEqual(shown["steps"][1]["status"], "failed")
            self.assertEqual(shown["steps"][2]["status"], "ok")
            self.assertTrue(shown["run"]["metrics"]["retrieval_fallback_used"])
            self.assertGreaterEqual(shown["run"]["metrics"]["retrieval_fallback_hit_count"], 2)
            self.assertIn(
                "catalog_fts_span_fallback",
                {item["method"] for item in shown["retrieval_candidates"]},
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

    def test_answer_harness_rejects_wrong_citation_runtime_type(self) -> None:
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
                synthesis_adapter=FakeWrongCitationTypeAdapter(),
            )

            self.assertEqual(result["status"], "fail")
            shown = get_harness_run(result["run_id"], harness_db)
            schema_errors = shown["steps"][2]["output"]["schema_errors"]
            self.assertIn("citations must be array", schema_errors)

    def test_answer_harness_enforces_chain_output_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = Path(tmp) / "catalog.sqlite"
            harness_db = Path(tmp) / "harness.sqlite"
            scan_wiki(FIXTURE, catalog_db)
            chain = base_reasoning_chain()
            chain["steps"][2]["output_schema"]["required_keys"].append("confidence")
            spec_dir = write_spec_dir(tmp, chain=chain)
            validation = validate_harness_specs(spec_dir)
            self.assertTrue(validation["valid"], validation["errors"])

            result = run_answer_with_citations(
                "retrieval",
                catalog_db=catalog_db,
                harness_db=harness_db,
                spec_dir=spec_dir,
                synthesis="openai",
                llm_model="fake-model",
                synthesis_adapter=FakeValidAdapter(),
            )

            self.assertEqual(result["status"], "fail")
            shown = get_harness_run(result["run_id"], harness_db)
            schema_errors = shown["steps"][2]["output"]["schema_errors"]
            self.assertIn("chain output missing required key confidence", schema_errors)

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


class FakeWrongCitationTypeAdapter(StructuredSynthesisAdapter):
    provider = "fake"

    def synthesize(self, *, user_query, chunks, min_citations, output_schema):
        return SynthesisResult(
            output={"answer_markdown": "Wrong citation type.", "citations": "not a list"},
            metadata={"model": "fake-model", "provider": self.provider, "token_usage": None},
        )


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


def write_spec_dir(
    tmp: str,
    *,
    contract: dict | None = None,
    chain: dict | None = None,
    taxonomy: dict | None = None,
) -> Path:
    spec_dir = Path(tmp) / "specs"
    spec_dir.mkdir()
    specs = [
        taxonomy or base_failure_taxonomy(),
        chain or base_reasoning_chain(),
        contract or base_task_contract(),
    ]
    for index, spec in enumerate(specs, start=1):
        (spec_dir / f"spec_{index}.md").write_text(
            "```yaml\n" + json.dumps(spec, indent=2, sort_keys=True) + "\n```\n"
        )
    return spec_dir


def base_task_contract() -> dict:
    return {
        "budgets": {
            "max_child_tasks": 0,
            "max_model_calls": 1,
            "max_retrieval_k": 8,
            "max_wall_clock_seconds": 45,
        },
        "chain": {"id": "chain.rag_answer"},
        "completion_condition": {
            "all_of": [
                "output.schema_valid == true",
                "output.citations.count >= 2",
                "verifier.groundedness.pass == true",
            ]
        },
        "description": "Answer a user question using retrieved wiki context and citations.",
        "id": "wiki.answer_with_citations",
        "inputs": {"user_query": {"required": True, "type": "string"}},
        "kind": "task_contract",
        "outputs": {
            "answer_markdown": {"required": True, "type": "string"},
            "citations": {"required": True, "type": "array"},
        },
        "persistence": {
            "retention_days_full_trace": 30,
            "store_retrieval_candidates": True,
            "store_tool_io": True,
        },
        "retrieval_profile": {"id": "catalog.fts_spans", "min_score_threshold": 0.0},
        "tools_allowed": [
            "retriever.search",
            "llm.structured_synthesis",
            "verifier.groundedness_check",
            "store.persist_run",
        ],
        "verification_profile": {
            "require_groundedness": True,
            "require_min_citations": 2,
            "require_schema_valid": True,
        },
        "version": 1,
    }


def base_reasoning_chain() -> dict:
    return {
        "description": "Plan, retrieve, synthesize, verify, persist.",
        "id": "chain.rag_answer",
        "kind": "reasoning_chain",
        "steps": [
            {
                "id": "s1_plan",
                "output_schema": {
                    "required_keys": [
                        "query_intent",
                        "search_queries",
                        "must_answer",
                        "uncertainty_notes",
                    ],
                    "type": "object",
                },
                "type": "deterministic",
            },
            {
                "id": "s2_retrieve",
                "outputs": {"retrieved_chunks": "list"},
                "tool_name": "retriever.search",
                "type": "tool",
            },
            {
                "id": "s3_synthesize",
                "output_schema": {
                    "required_keys": ["answer_markdown", "citations"],
                    "type": "object",
                },
                "tool_name": "llm.structured_synthesis",
                "type": "deterministic_or_llm",
            },
            {
                "id": "s4_verify_groundedness",
                "tool_name": "verifier.groundedness_check",
                "type": "tool",
            },
            {
                "id": "s5_persist",
                "tool_name": "store.persist_run",
                "type": "tool",
            },
        ],
        "version": 1,
    }


def base_failure_taxonomy() -> dict:
    return {
        "description": "Base failure taxonomy for wiki harness.",
        "enums": {
            "action": ["retry", "expand_retrieval", "ask_clarifying", "abort"],
            "severity": ["low", "medium", "high", "critical"],
        },
        "failures": [
            {
                "code": "RETRIEVAL_EMPTY",
                "description": "Retriever returned zero chunks.",
                "respond": [{"action": "expand_retrieval"}, {"action": "retry"}],
                "severity": "high",
            },
            {
                "code": "OUTPUT_SCHEMA_INVALID",
                "description": "Output failed required-field validation.",
                "respond": [{"action": "retry"}, {"action": "abort"}],
                "severity": "critical",
            },
            {
                "code": "LLM_PROVIDER_CONFIG_MISSING",
                "description": "Required LLM provider configuration is missing.",
                "respond": [{"action": "abort"}],
                "severity": "high",
            },
            {
                "code": "LLM_SYNTHESIS_ERROR",
                "description": "Structured synthesis provider failed.",
                "respond": [{"action": "retry"}, {"action": "abort"}],
                "severity": "high",
            },
            {
                "code": "GROUNDEDNESS_FAIL",
                "description": "Answer citations are not supported by retrieved context.",
                "respond": [{"action": "expand_retrieval"}, {"action": "ask_clarifying"}],
                "severity": "high",
            },
            {
                "code": "TOOL_CALL_ERROR",
                "description": "Tool execution failed.",
                "respond": [{"action": "retry"}, {"action": "abort"}],
                "severity": "high",
            },
        ],
        "id": "failures.core",
        "kind": "failure_taxonomy",
        "version": 1,
    }


if __name__ == "__main__":
    unittest.main()
