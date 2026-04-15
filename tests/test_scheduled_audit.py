from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser, main
from wiki_tool.scheduled_audit import run_scheduled_audit


SPEC_DIR = Path(__file__).parents[1] / "harness_specs"


class ScheduledAuditTests(unittest.TestCase):
    def test_scheduled_audit_passes_on_clean_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)
            eval_file = write_eval_file(tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=eval_file,
                output_dir=Path(tmp) / "reports",
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual([step["name"] for step in result["steps"]], ["audit", "harness_validate", "eval", "cleanup_targets"])
            self.assertTrue(Path(result["report_path"]).exists())

    def test_scheduled_audit_fails_when_audit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = clean_wiki(Path(tmp) / "wiki")
            (root / "index.md").write_text("# Home\n\n[Missing](missing.md)\n")
            catalog_db = build_catalog(root, tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=write_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                skip_eval=True,
                skip_cleanup_targets=True,
            )

            self.assertEqual(result["status"], "fail")
            audit_step = result["steps"][0]
            self.assertEqual(audit_step["name"], "audit")
            self.assertEqual(audit_step["status"], "fail")
            self.assertEqual(audit_step["audit"]["broken_links"], 1)

    def test_scheduled_audit_fails_when_harness_specs_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)
            spec_dir = Path(tmp) / "specs"
            spec_dir.mkdir()
            (spec_dir / "invalid.md").write_text("# Invalid\n\n```yaml\nkind: task_contract\nid: broken\n```\n")

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=spec_dir,
                eval_file=write_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                skip_eval=True,
                skip_cleanup_targets=True,
            )

            self.assertEqual(result["status"], "fail")
            harness_step = result["steps"][1]
            self.assertEqual(harness_step["name"], "harness_validate")
            self.assertEqual(harness_step["status"], "fail")
            self.assertFalse(harness_step["harness"]["valid"])

    def test_scheduled_audit_skip_eval_still_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=write_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                skip_eval=True,
            )

            self.assertEqual(result["status"], "pass")
            eval_step = result["steps"][2]
            self.assertEqual(eval_step["name"], "eval")
            self.assertEqual(eval_step["status"], "skip")
            self.assertTrue(eval_step["skipped"])

    def test_scheduled_audit_eval_is_advisory_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=write_failing_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                skip_cleanup_targets=True,
            )

            self.assertEqual(result["status"], "pass")
            eval_step = result["steps"][2]
            self.assertEqual(eval_step["name"], "eval")
            self.assertEqual(eval_step["status"], "fail")
            self.assertFalse(eval_step["required"])

    def test_scheduled_audit_require_eval_fails_on_eval_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=write_failing_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                require_eval=True,
                skip_cleanup_targets=True,
            )

            self.assertEqual(result["status"], "fail")
            eval_step = result["steps"][2]
            self.assertEqual(eval_step["name"], "eval")
            self.assertEqual(eval_step["status"], "fail")
            self.assertTrue(eval_step["required"])

    def test_scheduled_audit_skip_cleanup_targets_omits_cleanup_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=write_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                skip_cleanup_targets=True,
            )

            self.assertEqual(result["status"], "pass")
            cleanup_step = result["steps"][3]
            self.assertEqual(cleanup_step["name"], "cleanup_targets")
            self.assertEqual(cleanup_step["status"], "skip")
            self.assertTrue(cleanup_step["skipped"])

    def test_scheduled_audit_report_contains_step_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog_db = build_catalog(clean_wiki(Path(tmp) / "wiki"), tmp)

            result = run_scheduled_audit(
                catalog_db=catalog_db,
                harness_db=Path(tmp) / "harness.sqlite",
                spec_dir=SPEC_DIR,
                eval_file=write_eval_file(tmp),
                output_dir=Path(tmp) / "reports",
                skip_eval=True,
                skip_cleanup_targets=True,
            )

            report = Path(result["report_path"]).read_text()
            self.assertIn("# Scheduled Audit Report", report)
            self.assertIn("| audit | yes | pass |", report)

    def test_cli_help_exposes_scheduled_audit(self) -> None:
        parser = build_parser()

        help_text = parser.format_help()

        self.assertIn("scheduled-audit", help_text)

    def test_cli_exits_nonzero_when_scheduled_audit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = clean_wiki(Path(tmp) / "wiki")
            (root / "index.md").write_text("# Home\n\n[Missing](missing.md)\n")
            catalog_db = build_catalog(root, tmp)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout), redirect_stderr(stderr):
                main(
                    [
                        "scheduled-audit",
                        "run",
                        "--catalog-db",
                        str(catalog_db),
                        "--harness-db",
                        str(Path(tmp) / "harness.sqlite"),
                        "--eval-file",
                        str(write_eval_file(tmp)),
                        "--output-dir",
                        str(Path(tmp) / "reports"),
                        "--skip-eval",
                        "--skip-cleanup-targets",
                        "--json",
                    ]
                )

            self.assertEqual(raised.exception.code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "fail")


def build_catalog(root: Path, tmp: str) -> Path:
    catalog_db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, catalog_db)
    return catalog_db


def clean_wiki(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "concepts").mkdir()
    (root / "index.md").write_text("# Home\n\n[Retrieval](concepts/retrieval.md)\n")
    (root / "concepts" / "retrieval.md").write_text(
        "# Retrieval\n\nRetrieval resolves questions to evidence.\n\n"
        "## Search Notes\n\nSearch notes with grounded paths.\n"
    )
    return root


def write_eval_file(tmp: str) -> Path:
    path = Path(tmp) / "eval.jsonl"
    row = {
        "category": "concept",
        "expected_hints": ["Retrieval"],
        "expected_paths": ["concepts/retrieval.md"],
        "min_citations": 1,
        "query": "retrieval",
    }
    path.write_text(json.dumps(row, sort_keys=True) + "\n")
    return path


def write_failing_eval_file(tmp: str) -> Path:
    path = Path(tmp) / "eval_fail.jsonl"
    row = {
        "category": "concept",
        "expected_hints": ["Retrieval"],
        "expected_paths": ["concepts/retrieval.md"],
        "min_citations": 1,
        "query": "notfoundtoken",
    }
    path.write_text(json.dumps(row, sort_keys=True) + "\n")
    return path


if __name__ == "__main__":
    unittest.main()
