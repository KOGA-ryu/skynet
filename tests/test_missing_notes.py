from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import broken_links, scan_wiki
from wiki_tool.missing_notes import (
    build_missing_notes_patch_bundle,
    missing_note_audit,
    title_for_stub_path,
)
from wiki_tool.patch_bundle import apply_patch_bundle, validate_patch_bundle


class MissingNotesTests(unittest.TestCase):
    def test_title_generation(self) -> None:
        self.assertEqual(title_for_stub_path("docs/README.md"), "Docs")
        self.assertEqual(title_for_stub_path("docs/market_data_invariants.md"), "Market Data Invariants")
        self.assertEqual(title_for_stub_path("docs/task-phase-08.md"), "Task Phase 08")

    def test_missing_note_audit_deduplicates_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text(
                "# Home\n\n[One](docs/missing.md)\n[Two](docs/missing.md)\n"
            )
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)

            audit = missing_note_audit(db)
            self.assertEqual(audit["candidate_count"], 1)
            candidate = audit["candidates"][0]
            self.assertEqual(candidate["path"], "docs/missing.md")
            self.assertEqual(candidate["inbound_reference_count"], 2)
            self.assertIn("## Inbound References", candidate["body"])

    def test_missing_note_bundle_validates_and_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text("# Home\n\n[Missing](docs/missing.md)\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_missing_notes_patch_bundle(db)
            self.assertEqual(bundle["source_catalog"]["root"], str(root.resolve()))
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))

            validation = validate_patch_bundle(bundle_path, wiki_root=root)
            self.assertTrue(validation["valid"], validation["errors"])

            dry_run = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=Path(tmp) / "backups",
                dry_run=True,
            )
            self.assertEqual(dry_run["target_count"], 1)
            self.assertFalse((root / "docs" / "missing.md").exists())

            applied = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=Path(tmp) / "backups",
            )
            self.assertEqual(applied["target_count"], 1)
            self.assertTrue((root / "docs" / "missing.md").exists())
            self.assertTrue(Path(applied["manifest_path"]).exists())

            scan_wiki(root, db)
            self.assertEqual(broken_links(db, category="missing_markdown_note"), [])

    def test_missing_note_apply_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text("# Home\n\n[Missing](docs/missing.md)\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_missing_notes_patch_bundle(db)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))

            (root / "docs").mkdir()
            (root / "docs" / "missing.md").write_text("# Existing\n")
            validation = validate_patch_bundle(bundle_path, wiki_root=root)
            self.assertFalse(validation["valid"])
            with self.assertRaises(ValueError):
                apply_patch_bundle(
                    bundle_path,
                    wiki_root=root,
                    backup_dir=Path(tmp) / "backups",
                )


if __name__ == "__main__":
    unittest.main()
