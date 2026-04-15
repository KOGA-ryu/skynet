import unittest

from wiki_tool.ids import doc_id
from wiki_tool.markdown import parse_links, parse_spans


class MarkdownParsingTests(unittest.TestCase):
    def test_parse_spans_uses_headings(self) -> None:
        doc = doc_id("concepts/retrieval.md")
        spans = parse_spans(
            doc=doc,
            path="concepts/retrieval.md",
            text="# Retrieval\n\nIntro.\n\n## Symbol First\n\nBody.\n",
        )
        self.assertEqual([span.heading for span in spans], ["Retrieval", "Symbol First"])
        self.assertEqual(spans[0].start_line, 1)
        self.assertEqual(spans[1].level, 2)

    def test_parse_links_resolves_markdown_and_wikilinks(self) -> None:
        doc = doc_id("index.md")
        links = parse_links(
            doc=doc,
            path="index.md",
            text="[Retrieval](concepts/retrieval.md) and [[Scanner Hub]]",
            known_paths={"concepts/retrieval.md", "projects/demo/README.md"},
            title_to_path={"scannerhub": "projects/demo/README.md"},
        )
        self.assertEqual([link.resolved for link in links], [True, True])
        self.assertEqual(links[0].target_path, "concepts/retrieval.md")
        self.assertEqual(links[1].target_path, "projects/demo/README.md")

    def test_parse_links_normalizes_parent_segments(self) -> None:
        doc = doc_id("concepts/algorithms.md")
        links = parse_links(
            doc=doc,
            path="concepts/algorithms.md",
            text="[Book](../sources/computer/book.md)",
            known_paths={"sources/computer/book.md"},
        )
        self.assertTrue(links[0].resolved)
        self.assertEqual(links[0].target_path, "sources/computer/book.md")

    def test_dev_uri_is_treated_as_resolved_external_target(self) -> None:
        doc = doc_id("index.md")
        links = parse_links(
            doc=doc,
            path="index.md",
            text="[Main](dev://RD_UI/qml/Main.qml)",
            known_paths=set(),
        )
        self.assertTrue(links[0].resolved)
        self.assertEqual(links[0].target_path, "dev://RD_UI/qml/Main.qml")

    def test_aliases_resolve_wikilinks_and_unqualified_markdown_targets(self) -> None:
        doc = doc_id("index.md")
        links = parse_links(
            doc=doc,
            path="index.md",
            text="[[Scanner App]] and [Scanner](scanner-app)",
            known_paths={"projects/stock_trading/apps/scanner.md"},
            alias_to_path={"scannerapp": "projects/stock_trading/apps/scanner.md"},
        )
        self.assertEqual([link.resolved for link in links], [True, True])
        self.assertEqual(
            [link.target_path for link in links],
            ["projects/stock_trading/apps/scanner.md", "projects/stock_trading/apps/scanner.md"],
        )

    def test_aliases_do_not_resolve_path_like_markdown_targets(self) -> None:
        doc = doc_id("index.md")
        links = parse_links(
            doc=doc,
            path="index.md",
            text="[Scanner](missing/scanner-app)",
            known_paths={"projects/stock_trading/apps/scanner.md"},
            alias_to_path={"scannerapp": "projects/stock_trading/apps/scanner.md"},
        )
        self.assertFalse(links[0].resolved)
        self.assertEqual(links[0].target_path, "missing/scanner-app")


if __name__ == "__main__":
    unittest.main()
