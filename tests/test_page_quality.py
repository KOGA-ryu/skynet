from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.page_quality import (
    missing_summaries_report,
    page_quality_summary,
    thin_notes_report,
    unclear_hubs_report,
    write_page_quality_reports,
)


class PageQualityTests(unittest.TestCase):
    def test_page_quality_detects_librarian_queues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_quality_catalog(tmp)

            thin = paths(thin_notes_report(db)["thin_notes"])
            missing = paths(missing_summaries_report(db)["missing_summaries"])
            hubs = paths(unclear_hubs_report(db)["unclear_hubs"])
            summary = page_quality_summary(db)

            self.assertIn("concepts/thin.md", thin)
            self.assertIn("concepts/no_summary.md", missing)
            self.assertIn("concepts/stub.md", missing)
            self.assertIn("projects/alpha/README.md", hubs)
            self.assertNotIn("templates/example.md", thin | missing | hubs)
            self.assertNotIn("projects/alpha/state/generated.md", thin | missing | hubs)
            self.assertNotIn("concepts/strong.md", thin | missing | hubs)
            self.assertEqual(summary["thin_note_count"], len(thin))
            self.assertEqual(summary["missing_summary_count"], len(missing))
            self.assertEqual(summary["unclear_hub_count"], len(hubs))

    def test_page_quality_reports_include_reasons_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_quality_catalog(tmp)

            thin = item_by_path(thin_notes_report(db)["thin_notes"], "concepts/thin.md")
            missing = item_by_path(missing_summaries_report(db)["missing_summaries"], "concepts/stub.md")
            hub = item_by_path(unclear_hubs_report(db)["unclear_hubs"], "projects/alpha/README.md")

            self.assertIn("low_word_count", thin["reasons"])
            self.assertIn("low_byte_size", thin["reasons"])
            self.assertIn("stub_like_summary", missing["reasons"])
            self.assertIn("hub_overview_too_short", hub["reasons"])
            self.assertIn("hub_has_few_outbound_links", hub["reasons"])
            self.assertIn("hub_has_few_section_headings", hub["reasons"])
            self.assertEqual(hub["outbound_link_count"], 1)
            self.assertEqual(thin["inbound_count"], 2)

    def test_write_page_quality_reports_creates_local_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_quality_catalog(tmp)
            output_dir = Path(tmp) / "reports"

            result = write_page_quality_reports(db, output_dir=output_dir)

            self.assertEqual(result["file_count"], 4)
            self.assertTrue((output_dir / "README.md").exists())
            self.assertTrue((output_dir / "thin_notes.md").exists())
            self.assertTrue((output_dir / "missing_summaries.md").exists())
            self.assertTrue((output_dir / "unclear_hubs.md").exists())
            self.assertIn("concepts/thin.md", (output_dir / "thin_notes.md").read_text())
            self.assertIn("projects/alpha/README.md", (output_dir / "unclear_hubs.md").read_text())

    def test_cli_help_exposes_page_quality_commands(self) -> None:
        parser = build_parser()
        stdout = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(stdout):
            parser.parse_args(["page-quality", "--help"])
        help_text = stdout.getvalue()
        self.assertIn("summary", help_text)
        self.assertIn("thin", help_text)
        self.assertIn("missing-summaries", help_text)
        self.assertIn("unclear-hubs", help_text)
        self.assertIn("write", help_text)


def build_quality_catalog(tmp: str) -> Path:
    root = Path(tmp) / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "projects" / "alpha" / "state").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)

    (root / "index.md").write_text(
        "# Home\n\n"
        "This index page links to [Thin](concepts/thin.md), [Strong](concepts/strong.md), "
        "and [Alpha](projects/alpha/README.md).\n"
    )
    (root / "concepts" / "thin.md").write_text("# Thin\n\nTiny note.\n")
    (root / "concepts" / "no_summary.md").write_text(
        "# No Summary\n\n"
        "## Details\n\n"
        "This details section exists without an opening overview paragraph.\n"
    )
    (root / "concepts" / "stub.md").write_text(
        "# Stub\n\n"
        "Generated stub. Status: stub.\n"
    )
    (root / "concepts" / "strong.md").write_text(strong_note())
    (root / "projects" / "alpha" / "README.md").write_text(
        "# Alpha\n\n"
        "Short hub.\n\n"
        "[Thin](../../concepts/thin.md)\n"
    )
    (root / "projects" / "alpha" / "state" / "generated.md").write_text("# Generated\n\nTiny.\n")
    (root / "templates" / "example.md").write_text("# Template\n\nTiny.\n")
    db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, db)
    return db


def strong_note() -> str:
    words = " ".join(f"word{i}" for i in range(140))
    summary = " ".join(f"summary{i}" for i in range(35))
    return f"# Strong\n\n{summary}.\n\n## Details\n\n{words}.\n"


def paths(items: list[dict[str, object]]) -> set[str]:
    return {str(item["path"]) for item in items}


def item_by_path(items: list[dict[str, object]], path: str) -> dict[str, object]:
    for item in items:
        if item["path"] == path:
            return item
    raise AssertionError(f"missing item for {path}")


if __name__ == "__main__":
    unittest.main()
