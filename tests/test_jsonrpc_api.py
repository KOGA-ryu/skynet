from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser, main
from wiki_tool.jsonrpc_api import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    handle_jsonrpc,
    handle_jsonrpc_text,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sample_wiki"


class JsonRpcApiTests(unittest.TestCase):
    def test_api_methods_lists_search_core_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            response = handle_jsonrpc(
                request(1, "api.methods"),
                db_path=fixture_catalog(tmp),
                trace_path=None,
            )

            self.assertEqual(response["jsonrpc"], "2.0")
            method_names = {item["name"] for item in response["result"]["methods"]}
            self.assertTrue(
                {
                    "api.methods",
                    "harness.run",
                    "harness.show",
                    "symbol.search",
                    "span.searchText",
                    "span.listHeadings",
                    "link.findReferences",
                    "audit.summary",
                }
                <= method_names
            )

    def test_search_core_methods_return_bounded_handles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)

            symbol_response = handle_jsonrpc(
                request(1, "symbol.search", {"query": "Retrieval", "limit": 5}),
                db_path=catalog_db,
                trace_path=None,
            )
            span_response = handle_jsonrpc(
                request(2, "span.searchText", {"query": "scanner evidence", "limit": 5}),
                db_path=catalog_db,
                trace_path=None,
            )
            headings_response = handle_jsonrpc(
                request(3, "span.listHeadings", {"path": "projects/demo/README.md"}),
                db_path=catalog_db,
                trace_path=None,
            )
            refs_response = handle_jsonrpc(
                request(4, "link.findReferences", {"target": "concepts/retrieval.md"}),
                db_path=catalog_db,
                trace_path=None,
            )
            audit_response = handle_jsonrpc(
                request(5, "audit.summary"),
                db_path=catalog_db,
                trace_path=None,
            )

            symbols = symbol_response["result"]["results"]
            spans = span_response["result"]["results"]
            headings = headings_response["result"]["headings"]
            refs = refs_response["result"]["references"]
            self.assertTrue(symbols)
            self.assertTrue(spans)
            self.assertEqual([item["heading"] for item in headings], ["Scanner Hub", "Scanner Evidence"])
            self.assertEqual({item["source_path"] for item in refs}, {"index.md", "projects/demo/README.md"})
            self.assertNotIn("text", symbols[0])
            self.assertNotIn("text", spans[0])
            self.assertEqual(audit_response["result"]["summary"]["status"], "fail")

    def test_harness_run_and_show_return_bounded_api_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)
            harness_db = Path(tmp) / "harness.sqlite"

            run_response = handle_jsonrpc(
                request(1, "harness.run", {"query": "retrieval"}),
                db_path=catalog_db,
                harness_db=harness_db,
                trace_path=None,
            )

            run_result = run_response["result"]
            self.assertEqual(run_result["method"], "harness.run")
            self.assertEqual(run_result["status"], "pass")
            self.assertEqual(run_result["policy"]["returned"], "bounded-harness-answer")
            self.assertTrue(run_result["citations"])
            self.assertIn("answer_markdown", run_result)

            show_response = handle_jsonrpc(
                request(2, "harness.show", {"run_id": run_result["run_id"], "limit": 2}),
                db_path=catalog_db,
                harness_db=harness_db,
                trace_path=None,
            )

            show_result = show_response["result"]
            self.assertEqual(show_result["method"], "harness.show")
            self.assertEqual(show_result["run"]["run_id"], run_result["run_id"])
            self.assertLessEqual(len(show_result["steps"]), 2)
            self.assertLessEqual(len(show_result["retrieval_candidates"]), 2)
            self.assertNotIn("input", show_result["steps"][0])
            self.assertNotIn("output", show_result["steps"][0])
            self.assertEqual(show_result["policy"]["returned"], "bounded-harness-trace")

    def test_harness_jsonrpc_errors_use_invalid_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)
            harness_db = Path(tmp) / "harness.sqlite"

            bad_synthesis = handle_jsonrpc(
                request(1, "harness.run", {"query": "retrieval", "synthesis": "bad"}),
                db_path=catalog_db,
                harness_db=harness_db,
                trace_path=None,
            )
            missing_run = handle_jsonrpc(
                request(2, "harness.show", {"run_id": "run:missing"}),
                db_path=catalog_db,
                harness_db=harness_db,
                trace_path=None,
            )

            self.assertEqual(bad_synthesis["error"]["code"], INVALID_PARAMS)
            self.assertEqual(missing_run["error"]["code"], INVALID_PARAMS)

    def test_harness_run_accepts_local_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)
            harness_db = Path(tmp) / "harness.sqlite"

            with patch(
                "wiki_tool.jsonrpc_api.run_answer_with_citations",
                return_value={
                    "answer_markdown": "local answer",
                    "citations": [],
                    "failure_actions": [],
                    "failures": [],
                    "harness_db": str(harness_db),
                    "run_id": "run:test",
                    "status": "pass",
                    "synthesis": {"provider": "local", "model": "local"},
                },
            ) as mocked, patch(
                "wiki_tool.jsonrpc_api.get_harness_run",
                return_value={"run": {"metrics": {"synthesis_provider": "local"}}},
            ):
                response = handle_jsonrpc(
                    request(1, "harness.run", {"query": "retrieval", "synthesis": "local"}),
                    db_path=catalog_db,
                    harness_db=harness_db,
                    trace_path=None,
                )

            self.assertEqual(response["result"]["status"], "pass")
            self.assertEqual(response["result"]["synthesis"]["provider"], "local")
            self.assertEqual(mocked.call_args.kwargs["synthesis"], "local")

    def test_limits_are_clamped_and_mark_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = many_span_catalog(tmp)
            response = handle_jsonrpc(
                request(1, "span.searchText", {"query": "alpha", "limit": 999}),
                db_path=catalog_db,
                trace_path=None,
            )

            result = response["result"]
            self.assertEqual(result["limit"], 25)
            self.assertEqual(len(result["results"]), 25)
            self.assertTrue(result["truncated"])

    def test_jsonrpc_errors_use_standard_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)

            parse_error = handle_jsonrpc_text("{not json", db_path=catalog_db, trace_path=None)
            unknown = handle_jsonrpc(
                request(1, "missing.method"),
                db_path=catalog_db,
                trace_path=None,
            )
            invalid_params = handle_jsonrpc(
                request(2, "symbol.search", {"query": "", "limit": 1}),
                db_path=catalog_db,
                trace_path=None,
            )

            self.assertEqual(parse_error["error"]["code"], PARSE_ERROR)
            self.assertEqual(unknown["error"]["code"], METHOD_NOT_FOUND)
            self.assertEqual(invalid_params["error"]["code"], INVALID_PARAMS)

    def test_trace_file_records_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)
            trace_path = Path(tmp) / "api_traces.jsonl"

            handle_jsonrpc(
                request(1, "symbol.search", {"query": "Retrieval"}),
                db_path=catalog_db,
                trace_path=trace_path,
            )
            handle_jsonrpc(
                request(2, "missing.method"),
                db_path=catalog_db,
                trace_path=trace_path,
            )

            traces = [json.loads(line) for line in trace_path.read_text().splitlines()]
            self.assertEqual([item["status"] for item in traces], ["ok", "error"])
            self.assertEqual(traces[0]["method"], "symbol.search")
            self.assertEqual(traces[0]["policy"]["returned"], "symbol-handles")
            self.assertEqual(traces[1]["error"]["code"], METHOD_NOT_FOUND)

    def test_trace_file_records_harness_method_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)
            harness_db = Path(tmp) / "harness.sqlite"
            trace_path = Path(tmp) / "api_traces.jsonl"

            handle_jsonrpc(
                request(1, "harness.run", {"query": "retrieval"}),
                db_path=catalog_db,
                harness_db=harness_db,
                trace_path=trace_path,
            )

            traces = [json.loads(line) for line in trace_path.read_text().splitlines()]
            self.assertEqual(traces[0]["method"], "harness.run")
            self.assertEqual(traces[0]["params"]["query"], "retrieval")
            self.assertEqual(traces[0]["policy"]["returned"], "bounded-harness-answer")

    def test_cli_exposes_api_request_and_serve(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["api", "request", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("--request-json", help_text)
        self.assertIn("--harness-db", help_text)
        self.assertIn("--spec-dir", help_text)

    def test_cli_request_and_serve_emit_jsonrpc_responses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = fixture_catalog(tmp)
            trace_path = Path(tmp) / "traces.jsonl"
            payload = json.dumps(request(1, "symbol.search", {"query": "Retrieval"}))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                main([
                    "api",
                    "request",
                    "--request-json",
                    payload,
                    "--catalog-db",
                    str(catalog_db),
                    "--trace-path",
                    str(trace_path),
                    "--json",
                ])
            one_shot = json.loads(stdout.getvalue())
            self.assertEqual(one_shot["result"]["method"], "symbol.search")

            serve_input = "\n".join(
                [
                    json.dumps(request(2, "api.methods")),
                    json.dumps(request(3, "audit.summary")),
                    "",
                ]
            )
            stdout = io.StringIO()
            with patch.object(sys, "stdin", io.StringIO(serve_input)), redirect_stdout(stdout):
                main([
                    "api",
                    "serve",
                    "--catalog-db",
                    str(catalog_db),
                    "--trace-path",
                    str(trace_path),
                ])
            lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual([line["id"] for line in lines], [2, 3])
            self.assertEqual(lines[0]["result"]["method"], "api.methods")
            self.assertEqual(lines[1]["result"]["method"], "audit.summary")


def request(request_id: int, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def fixture_catalog(tmp: str) -> Path:
    catalog_db = Path(tmp) / "catalog.sqlite"
    scan_wiki(FIXTURE, catalog_db)
    return catalog_db


def many_span_catalog(tmp: str) -> Path:
    root = Path(tmp) / "wiki"
    root.mkdir()
    for index in range(30):
        (root / f"note_{index:02d}.md").write_text(
            f"# Alpha Note {index}\n\nAlpha evidence paragraph {index}.\n"
        )
    catalog_db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, catalog_db)
    return catalog_db


if __name__ == "__main__":
    unittest.main()
