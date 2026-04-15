from pathlib import Path
import tempfile
import unittest

from wiki_tool.cli import build_parser
from wiki_tool.health import run_health


SPEC_DIR = Path(__file__).parents[1] / "harness_specs"


class HealthTests(unittest.TestCase):
    def test_health_passes_on_clean_fixture_and_passing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = clean_wiki(Path(tmp) / "wiki")
            tests_dir = passing_tests(Path(tmp) / "tests")
            result = run_health(
                wiki_root=root,
                db_path=Path(tmp) / "catalog.sqlite",
                alias_map_path=None,
                spec_dir=SPEC_DIR,
                tests_dir=tests_dir,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual([step["status"] for step in result["steps"]], ["pass"] * 4)
            self.assertEqual(result["steps"][0]["scan"]["document_count"], 2)

    def test_health_fails_when_audit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = clean_wiki(Path(tmp) / "wiki")
            (root / "index.md").write_text("# Home\n\n[Missing](missing.md)\n")
            tests_dir = passing_tests(Path(tmp) / "tests")

            result = run_health(
                wiki_root=root,
                db_path=Path(tmp) / "catalog.sqlite",
                alias_map_path=None,
                spec_dir=SPEC_DIR,
                tests_dir=tests_dir,
            )

            self.assertEqual(result["status"], "fail")
            audit_step = result["steps"][1]
            self.assertEqual(audit_step["name"], "audit")
            self.assertEqual(audit_step["status"], "fail")
            self.assertEqual(audit_step["audit"]["broken_links"], 1)

    def test_health_fails_when_harness_specs_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = clean_wiki(Path(tmp) / "wiki")
            tests_dir = passing_tests(Path(tmp) / "tests")
            spec_dir = Path(tmp) / "specs"
            spec_dir.mkdir()
            (spec_dir / "invalid.md").write_text(
                "# Invalid\n\n```yaml\nkind: task_contract\nid: broken\n```\n"
            )

            result = run_health(
                wiki_root=root,
                db_path=Path(tmp) / "catalog.sqlite",
                alias_map_path=None,
                spec_dir=spec_dir,
                tests_dir=tests_dir,
            )

            self.assertEqual(result["status"], "fail")
            harness_step = result["steps"][2]
            self.assertEqual(harness_step["name"], "harness_validate")
            self.assertEqual(harness_step["status"], "fail")
            self.assertFalse(harness_step["harness"]["valid"])

    def test_health_fails_when_unit_tests_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = clean_wiki(Path(tmp) / "wiki")
            tests_dir = failing_tests(Path(tmp) / "tests")

            result = run_health(
                wiki_root=root,
                db_path=Path(tmp) / "catalog.sqlite",
                alias_map_path=None,
                spec_dir=SPEC_DIR,
                tests_dir=tests_dir,
            )

            self.assertEqual(result["status"], "fail")
            unit_step = result["steps"][3]
            self.assertEqual(unit_step["name"], "unit_tests")
            self.assertEqual(unit_step["status"], "fail")
            self.assertNotEqual(unit_step["return_code"], 0)
            self.assertIn("FAILED", unit_step["stderr"])

    def test_cli_help_lists_health_command(self) -> None:
        help_text = build_parser().format_help()
        self.assertIn("health", help_text)

    def test_cli_accepts_scan_status_command(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["scan-status", "--wiki-root", "/tmp/wiki", "--limit", "3"])

        self.assertEqual(args.wiki_root, Path("/tmp/wiki"))
        self.assertEqual(args.limit, 3)


def clean_wiki(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "concepts").mkdir()
    (root / "index.md").write_text("# Home\n\n[Retrieval](concepts/retrieval.md)\n")
    (root / "concepts" / "retrieval.md").write_text("# Retrieval\n\nSearch notes.\n")
    return root


def passing_tests(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "test_smoke.py").write_text(
        "import unittest\n\n"
        "class SmokeTests(unittest.TestCase):\n"
        "    def test_passes(self):\n"
        "        self.assertTrue(True)\n"
    )
    return root


def failing_tests(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "test_smoke.py").write_text(
        "import unittest\n\n"
        "class SmokeTests(unittest.TestCase):\n"
        "    def test_fails(self):\n"
        "        self.assertTrue(False)\n"
    )
    return root


if __name__ == "__main__":
    unittest.main()
