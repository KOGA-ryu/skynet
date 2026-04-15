from pathlib import Path
import tempfile
import unittest

from wiki_tool.catalog import (
    audit_summary,
    broken_links,
    find_references,
    get_headings,
    query_catalog,
    scan_freshness,
    scan_wiki,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sample_wiki"


class CatalogTests(unittest.TestCase):
    def test_scan_builds_catalog_and_searches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite"
            result = scan_wiki(FIXTURE, db)
            self.assertEqual(result.document_count, 3)
            self.assertEqual(result.broken_link_count, 1)

            found = query_catalog(db, "span.searchText", "scanner evidence", 5)
            self.assertTrue(found)
            self.assertEqual(found[0]["path"], "projects/demo/README.md")

    def test_references_and_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(FIXTURE, db)

            refs = find_references(db, "concepts/retrieval.md")
            self.assertEqual(
                {item["source_path"] for item in refs},
                {"index.md", "projects/demo/README.md"},
            )

            headings = get_headings(db, "projects/demo/README.md")
            self.assertEqual(
                [item["heading"] for item in headings],
                ["Scanner Hub", "Scanner Evidence"],
            )

    def test_broken_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(FIXTURE, db)

            broken = broken_links(db)
            self.assertEqual(len(broken), 1)
            self.assertEqual(broken[0]["target_raw"], "missing.md")

    def test_template_placeholder_links_are_excluded_from_audit_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            (root / "templates").mkdir(parents=True)
            (root / "templates" / "example.md").write_text("# Template\n\n[Path](<path>)\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)

            broken = broken_links(db)
            self.assertEqual(broken[0]["category"], "template_placeholder")
            summary = audit_summary(db)
            self.assertEqual(summary["broken_links"], 0)
            self.assertEqual(summary["excluded_links"], 1)

    def test_scan_freshness_passes_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text("# Home\n\nStable catalog.\n")
            db = Path(tmp) / "catalog.sqlite"

            scan_wiki(root, db)
            freshness = scan_freshness(db)
            summary = audit_summary(db)

            self.assertEqual(freshness["status"], "pass")
            self.assertFalse(freshness["stale"])
            self.assertEqual(freshness["reason"], "catalog_matches_checked_root")
            self.assertEqual(summary["scan_freshness"]["status"], "pass")
            self.assertEqual(summary["status"], "pass")

    def test_scan_freshness_detects_added_modified_and_removed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text("# Home\n\nOriginal catalog.\n")
            (root / "removed.md").write_text("# Removed\n\nThis note will be removed.\n")
            (root / "asset.txt").write_text("original asset\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)

            (root / "index.md").write_text("# Home\n\nChanged catalog.\n")
            (root / "removed.md").unlink()
            (root / "added.md").write_text("# Added\n\nNew note.\n")
            (root / "new_asset.txt").write_text("new asset\n")
            (root / "asset.txt").unlink()

            freshness = scan_freshness(db)
            summary = audit_summary(db)

            self.assertEqual(freshness["status"], "fail")
            self.assertTrue(freshness["stale"])
            self.assertEqual(freshness["reason"], "source_changed_since_scan")
            self.assertEqual(freshness["added_document_count"], 1)
            self.assertEqual(freshness["modified_document_count"], 1)
            self.assertEqual(freshness["removed_document_count"], 1)
            self.assertEqual(freshness["added_file_count"], 1)
            self.assertEqual(freshness["removed_file_count"], 1)
            self.assertEqual(summary["status"], "fail")

    def test_scan_freshness_can_compare_an_override_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mirror = Path(tmp) / "mirror"
            nas = Path(tmp) / "nas"
            mirror.mkdir()
            nas.mkdir()
            (mirror / "index.md").write_text("# Home\n\nSame content.\n")
            (nas / "index.md").write_text("# Home\n\nSame content.\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(mirror, db)

            freshness = scan_freshness(db, root=nas)
            self.assertEqual(freshness["status"], "pass")
            self.assertFalse(freshness["root_matches_catalog_root"])

            (nas / "index.md").write_text("# Home\n\nNAS moved ahead.\n")
            stale = scan_freshness(db, root=nas)
            self.assertEqual(stale["status"], "fail")
            self.assertEqual(stale["modified_document_count"], 1)


if __name__ == "__main__":
    unittest.main()
