from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import audit_summary, broken_links, scan_wiki
from wiki_tool.file_links import build_file_links_patch_bundle, file_link_audit
from wiki_tool.patch_bundle import apply_patch_bundle, validate_patch_bundle


class FileLinksTests(unittest.TestCase):
    def test_file_link_audit_builds_wiki_and_dev_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            dev_root = Path(tmp) / "dev"
            (root / "config").mkdir(parents=True)
            (root / "config" / "library_catalog_registry.json").write_text("{}\n")
            catalog = root / "projects" / "library_operations" / "catalog"
            catalog.mkdir(parents=True)
            (catalog / "README.md").write_text(
                "# Catalog\n\n[Library Catalog Registry](../../config/library_catalog_registry.json)\n"
            )
            rudedude = root / "projects" / "stock_trading" / "apps" / "rudedude"
            rudedude.mkdir(parents=True)
            (rudedude / "phase_file_map.md").write_text(
                "# Phase File Map\n\n"
                "[tests/test_retrieval.py](tests/test_retrieval.py)\n"
                "[app/market_data/lineage_registry.py](app/market_data/lineage_registry.py)\n"
            )
            (dev_root / "rudedude" / "tests").mkdir(parents=True)
            (dev_root / "rudedude" / "tests" / "test_retrieval.py").write_text("")
            (dev_root / "rudedude" / "app" / "lineage").mkdir(parents=True)
            (dev_root / "rudedude" / "app" / "lineage" / "registry.py").write_text("")

            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)

            audit = file_link_audit(db, mac_dev_root=str(dev_root))
            self.assertEqual(audit["candidate_count"], 3)
            self.assertEqual(audit["skipped_count"], 0)
            targets = {item["old_target"]: item["new_target"] for item in audit["candidates"]}
            self.assertEqual(
                targets["../../config/library_catalog_registry.json"],
                "../../../config/library_catalog_registry.json",
            )
            self.assertEqual(targets["tests/test_retrieval.py"], "dev://rudedude/tests/test_retrieval.py")
            self.assertEqual(
                targets["app/market_data/lineage_registry.py"],
                "dev://rudedude/app/lineage/registry.py",
            )

    def test_file_link_bundle_validates_and_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            (root / "config").mkdir(parents=True)
            (root / "config" / "library_catalog_registry.json").write_text("{}\n")
            catalog = root / "projects" / "library_operations" / "catalog"
            catalog.mkdir(parents=True)
            note = catalog / "README.md"
            note.write_text(
                "# Catalog\n\n[Library Catalog Registry](../../config/library_catalog_registry.json)\n"
            )
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_file_links_patch_bundle(db)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))

            validation = validate_patch_bundle(bundle_path, wiki_root=root)
            self.assertTrue(validation["valid"], validation["errors"])
            apply_patch_bundle(bundle_path, wiki_root=root, backup_dir=Path(tmp) / "backups")
            self.assertIn(
                "[Library Catalog Registry](../../../config/library_catalog_registry.json)",
                note.read_text(),
            )
            scan_wiki(root, db)
            self.assertEqual(audit_summary(db)["broken_links"], 0)

    def test_replace_markdown_link_rejects_stale_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            (root / "config").mkdir(parents=True)
            (root / "config" / "library_catalog_registry.json").write_text("{}\n")
            catalog = root / "projects" / "library_operations" / "catalog"
            catalog.mkdir(parents=True)
            note = catalog / "README.md"
            note.write_text(
                "# Catalog\n\n[Library Catalog Registry](../../config/library_catalog_registry.json)\n"
            )
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_file_links_patch_bundle(db)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))
            note.write_text("# Catalog\n\n[Library Catalog Registry](../../../config/library_catalog_registry.json)\n")

            validation = validate_patch_bundle(bundle_path, wiki_root=root)
            self.assertFalse(validation["valid"])
            with self.assertRaises(ValueError):
                apply_patch_bundle(bundle_path, wiki_root=root, backup_dir=Path(tmp) / "backups")

    def test_unresolved_file_link_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text("# Home\n\n[Missing](missing.json)\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            audit = file_link_audit(db)
            self.assertEqual(audit["candidate_count"], 0)
            self.assertEqual(audit["skipped_count"], 1)
            self.assertEqual(broken_links(db)[0]["category"], "missing_non_markdown_file")


if __name__ == "__main__":
    unittest.main()
