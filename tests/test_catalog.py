from pathlib import Path
import tempfile
import unittest

from wiki_tool.catalog import audit_summary, broken_links, find_references, get_headings, query_catalog, scan_wiki


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


if __name__ == "__main__":
    unittest.main()
