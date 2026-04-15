from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.cli import build_parser
from wiki_tool.intake import (
    build_intake_patch_bundle,
    validate_intake_manifest,
    write_intake_outputs,
)
from wiki_tool.patch_bundle import validate_patch_bundle


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "intake"
FIXTURE_MANIFEST = FIXTURE_ROOT / "demo_manifest.json"
FIXTURE_REPO = FIXTURE_ROOT / "repo"


class IntakeWorkflowTests(unittest.TestCase):
    def test_validate_normalizes_manifest_and_priorities(self) -> None:
        result = validate_intake_manifest(FIXTURE_MANIFEST, repo_root=FIXTURE_REPO)

        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["finding_count"], 3)
        self.assertEqual(result["intake_id"], "demo_repo_demand")
        self.assertEqual(result["priority_counts"], {"P0": 1, "P1": 1, "P2": 1})
        self.assertIn("docs/missing.md", "\n".join(result["warnings"]))

    def test_validate_reports_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "bad.json"
            manifest.write_text(json.dumps({"intake_id": "bad"}))

            result = validate_intake_manifest(manifest, repo_root=FIXTURE_REPO)

            self.assertFalse(result["valid"])
            self.assertIn("missing required top-level field: title", result["errors"])
            self.assertIn("findings must be a non-empty list", result["errors"])

    def test_validate_rejects_invalid_status_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "bad.json"
            payload = fixture_payload()
            payload["findings"][0]["status"] = "decided"
            payload["findings"][0]["confidence"] = "probably"
            manifest.write_text(json.dumps(payload))

            result = validate_intake_manifest(manifest, repo_root=FIXTURE_REPO)

            self.assertFalse(result["valid"])
            self.assertIn("unsupported value: decided", "\n".join(result["errors"]))
            self.assertIn("unsupported value: probably", "\n".join(result["errors"]))

    def test_validate_rejects_evidence_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "bad.json"
            payload = fixture_payload()
            payload["findings"][0]["evidence"] = ["../secret.md"]
            manifest.write_text(json.dumps(payload))

            result = validate_intake_manifest(manifest, repo_root=FIXTURE_REPO)

            self.assertFalse(result["valid"])
            self.assertIn("escapes repo_root", "\n".join(result["errors"]))

    def test_write_outputs_creates_local_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "intake"

            result = write_intake_outputs(FIXTURE_MANIFEST, repo_root=FIXTURE_REPO, output_dir=output_dir)

            run_dir = output_dir / "demo_repo_demand"
            self.assertEqual(result["file_count"], 5)
            self.assertTrue((run_dir / "README.md").exists())
            self.assertTrue((run_dir / "intake_queue.md").exists())
            self.assertTrue((run_dir / "promotion_candidates.md").exists())
            self.assertTrue((run_dir / "librarian_packet.md").exists())
            self.assertTrue((run_dir / "manifest_normalized.json").exists())
            self.assertIn("Adapter boundary before core logic", (run_dir / "promotion_candidates.md").read_text())

    def test_bundle_generation_validates_against_wiki_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wiki_root = build_wiki_fixture(Path(tmp) / "wiki")
            bundle = build_intake_patch_bundle(
                FIXTURE_MANIFEST,
                repo_root=FIXTURE_REPO,
                wiki_root=wiki_root,
            )
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))

            validation = validate_patch_bundle(bundle_path, wiki_root=wiki_root)

            self.assertTrue(validation["valid"], validation["errors"])
            self.assertEqual(validation["target_count"], 2)
            self.assertEqual(bundle["targets"][0]["type"], "create_markdown_file")
            self.assertEqual(bundle["targets"][1]["type"], "replace_text_block")
            self.assertEqual(bundle["skipped"], [])

    def test_bundle_skips_queue_update_without_wiki_root(self) -> None:
        bundle = build_intake_patch_bundle(FIXTURE_MANIFEST, repo_root=FIXTURE_REPO)

        self.assertEqual(len(bundle["targets"]), 1)
        self.assertEqual(bundle["targets"][0]["type"], "create_markdown_file")
        self.assertEqual(bundle["skipped"][0]["kind"], "library_intake_queue_update")

    def test_cli_parses_intake_commands(self) -> None:
        parser = build_parser()

        validate_args = parser.parse_args(["intake", "validate", "--input", "demo.json"])
        write_args = parser.parse_args(["intake", "write", "--input", "demo.json"])
        bundle_args = parser.parse_args(
            ["intake", "bundle", "--input", "demo.json", "--output", "patch_bundles/intake_demo.json"]
        )

        self.assertEqual(validate_args.func.__name__, "cmd_intake_validate")
        self.assertEqual(write_args.func.__name__, "cmd_intake_write")
        self.assertEqual(bundle_args.func.__name__, "cmd_intake_bundle")


def fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE_MANIFEST.read_text())


def build_wiki_fixture(root: Path) -> Path:
    queue = root / "projects" / "library_operations" / "library_intake_queue.md"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        "# Library Intake Queue\n\n"
        "## Purpose\n\n"
        "Capture incoming road packets.\n\n"
        "## Active Intake Sources\n\n"
        "### Existing\n\n"
        "- source_type: `teardown`\n"
        "- overall_state: `routed`\n\n"
        "## Promoted Items\n\n"
        "None yet.\n"
    )
    return root
