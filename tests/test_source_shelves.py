from pathlib import Path
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.source_shelves import (
    source_shelf_report,
    source_shelf_summary,
    write_source_shelf_reports,
)


class SourceShelfTests(unittest.TestCase):
    def test_source_shelf_summary_counts_math_and_computer_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_source_shelf_catalog(tmp)

            summary = source_shelf_summary(db)
            shelves = {item["shelf"]: item for item in summary["shelves"]}

            self.assertEqual(summary["shelf_count"], 2)
            self.assertEqual(summary["total_source_notes"], 4)
            self.assertEqual(set(shelves), {"math", "computer"})
            self.assertEqual(shelves["math"]["source_note_count"], 2)
            self.assertEqual(shelves["computer"]["source_note_count"], 2)
            self.assertTrue(shelves["math"]["hub_present"])
            self.assertTrue(shelves["computer"]["hub_present"])

    def test_source_shelf_report_excludes_readme_and_tracks_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_source_shelf_catalog(tmp)

            report = source_shelf_report(db, "math")
            notes = {item["path"]: item for item in report["notes"]}

            self.assertEqual(report["hub_path"], "sources/math/README.md")
            self.assertEqual(set(notes), {"sources/math/page--1-0.md", "sources/math/probability_measure.md"})
            self.assertEqual(notes["sources/math/probability_measure.md"]["inbound_count"], 1)
            self.assertEqual(notes["sources/math/probability_measure.md"]["outbound_count"], 1)
            self.assertEqual(
                notes["sources/math/probability_measure.md"]["concept_project_links"],
                [{"label": "Probability", "path": "concepts/probability.md"}],
            )

    def test_source_shelf_report_classifies_patterns_and_quality_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_source_shelf_catalog(tmp)

            computer = source_shelf_report(db, "computer")
            notes = {item["path"]: item for item in computer["notes"]}
            pattern = notes["sources/computer/libqalculate_patterns.md"]
            weak = notes["sources/computer/clean_architecture__martin.md"]

            self.assertEqual(pattern["source_type"], "oss_pattern")
            self.assertEqual(pattern["curation_status"], "pattern")
            self.assertEqual(pattern["document_id"], "n/a")
            self.assertIn("quant_calculator_patterns", {pattern["lane"], weak["lane"]})
            self.assertIn("weak_summary", weak["quality_flags"])
            self.assertIn("no_outbound_concept_or_project_links", weak["quality_flags"])

    def test_source_shelf_report_flags_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_source_shelf_catalog(tmp)

            math = source_shelf_report(db, "math")
            placeholder = next(item for item in math["notes"] if item["path"] == "sources/math/page--1-0.md")

            self.assertEqual(placeholder["source_type"], "placeholder")
            self.assertEqual(placeholder["priority"], "P0")
            self.assertIn("placeholder_artifact", placeholder["quality_flags"])
            self.assertIn("generated_stub", placeholder["quality_flags"])

    def test_source_shelf_limit_trims_details_without_changing_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_source_shelf_catalog(tmp)

            report = source_shelf_report(db, "math", limit=1)

            self.assertEqual(report["source_note_count"], 2)
            self.assertEqual(len(report["priority_queue"]), 1)
            self.assertEqual(report["limit"], 1)

    def test_write_source_shelf_reports_creates_local_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_source_shelf_catalog(tmp)
            output_dir = Path(tmp) / "reports"

            result = write_source_shelf_reports(db, output_dir=output_dir, limit=1)

            self.assertEqual(result["shelf_count"], 2)
            self.assertTrue((output_dir / "README.md").exists())
            self.assertTrue((output_dir / "math.md").exists())
            self.assertTrue((output_dir / "computer.md").exists())
            self.assertIn("Source Shelf Report: math", (output_dir / "math.md").read_text())

    def test_cli_exposes_source_shelf_commands(self) -> None:
        parser = build_parser()

        summary_args = parser.parse_args(["source-shelves", "summary"])
        show_args = parser.parse_args(["source-shelves", "show", "math", "--limit", "3"])
        write_args = parser.parse_args(["source-shelves", "write", "--limit", "4"])

        self.assertEqual(summary_args.func.__name__, "cmd_source_shelves_summary")
        self.assertEqual(show_args.shelf, "math")
        self.assertEqual(show_args.limit, 3)
        self.assertEqual(write_args.limit, 4)


def build_source_shelf_catalog(tmp: str) -> Path:
    root = Path(tmp) / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "projects" / "rag_system").mkdir(parents=True)
    (root / "sources" / "math").mkdir(parents=True)
    (root / "sources" / "computer").mkdir(parents=True)

    (root / "concepts" / "probability.md").write_text(
        "# Probability\n\n"
        "Probability concepts route to the maintained math shelf.\n\n"
        "[Probability and Measure](../sources/math/probability_measure.md)\n"
    )
    (root / "projects" / "rag_system" / "README.md").write_text(
        "# RAG System\n\n"
        "This project uses architecture references.\n\n"
        "[Clean Architecture](../../sources/computer/clean_architecture__martin.md)\n"
    )
    (root / "sources" / "math" / "README.md").write_text(
        "# Math Source Notes\n\n"
        "Curated math shelf.\n\n"
        "- `probability_measure`\n"
    )
    (root / "sources" / "math" / "probability_measure.md").write_text(
        "# Probability and Measure\n\n"
        "- corpus: `math`\n"
        "- document_id: `probability_measure`\n"
        "- output_root: `C:\\dev\\outputs\\math\\probability_measure`\n\n"
        "This source supports measure-theoretic probability foundations and links probability concepts to rigorous random-variable reasoning.\n\n"
        "## Strongest Chapters\n\n"
        "- Measure spaces\n"
        "- Random variables\n\n"
        "## Related Concepts\n\n"
        "- [Probability](../../concepts/probability.md)\n"
    )
    (root / "sources" / "math" / "page--1-0.md").write_text(
        "# Generated Page\n\n"
        "Generated stub.\n\n"
        "- Status: stub\n"
        "- Content has not been filled in yet.\n"
    )
    (root / "sources" / "computer" / "README.md").write_text(
        "# Computer Source Notes\n\n"
        "Computer shelf.\n\n"
        "- [Clean Architecture](clean_architecture__martin.md)\n"
    )
    (root / "sources" / "computer" / "clean_architecture__martin.md").write_text(
        "# Clean Architecture\n\n"
        "- corpus: `computer`\n"
        "- document_id: `clean_architecture__martin`\n"
        "- output_root: `C:\\dev\\outputs\\computer\\clean_architecture__martin`\n\n"
        "Short.\n"
    )
    (root / "sources" / "computer" / "libqalculate_patterns.md").write_text(
        "# libqalculate Patterns\n\n"
        "- corpus: `computer`\n"
        "- document_id: `n/a`\n"
        "- output_root: `n/a`\n\n"
        "This pattern note connects calculator engine implementation choices to computational math project design.\n\n"
        "## Related Projects\n\n"
        "- [RAG System](../../projects/rag_system/README.md)\n"
    )

    db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, db)
    return db


if __name__ == "__main__":
    unittest.main()
