from pathlib import Path
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.project_reports import (
    project_name_from_path,
    project_report,
    project_report_summary,
    write_project_reports,
)


class ProjectReportTests(unittest.TestCase):
    def test_project_name_from_path_uses_top_level_project_directory(self) -> None:
        self.assertEqual(
            project_name_from_path("projects/alpha/apps/tool.md"),
            "alpha",
        )
        self.assertEqual(
            project_name_from_path("projects/alpha/README.md"),
            "alpha",
        )
        self.assertIsNone(project_name_from_path("projects/alpha.md"))
        self.assertIsNone(project_name_from_path("concepts/alpha.md"))

    def test_project_reports_detect_hubs_backlinks_and_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            (root / "projects" / "alpha" / "notes").mkdir(parents=True)
            (root / "projects" / "beta" / "notes").mkdir(parents=True)
            (root / "projects" / "alpha" / "README.md").write_text(
                "# Alpha Hub\n\n[Alpha Note](notes/linked.md)\n[Beta Note](../beta/notes/beta.md)\n"
            )
            (root / "projects" / "alpha" / "notes" / "linked.md").write_text(
                "# Linked Alpha\n\n[Hub](../README.md)\n"
            )
            (root / "projects" / "alpha" / "notes" / "orphan.md").write_text(
                "# Orphan Alpha\n"
            )
            (root / "projects" / "beta" / "notes" / "beta.md").write_text(
                "# Beta Note\n\n[Alpha Hub](../../alpha/README.md)\n"
            )
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)

            summary = project_report_summary(db)
            projects = {item["project"]: item for item in summary["projects"]}
            self.assertEqual(set(projects), {"alpha", "beta"})
            self.assertTrue(projects["alpha"]["hub_present"])
            self.assertFalse(projects["beta"]["hub_present"])
            self.assertEqual(projects["beta"]["missing_hub"], True)

            alpha = project_report(db, "alpha")
            self.assertEqual(alpha["note_count"], 3)
            self.assertEqual(
                {note["path"] for note in alpha["orphan_notes"]},
                {"projects/alpha/notes/orphan.md"},
            )
            hub = next(note for note in alpha["notes"] if note["path"] == "projects/alpha/README.md")
            self.assertEqual(hub["inbound_count"], 2)
            self.assertEqual(
                {source["source_path"] for source in hub["inbound_sources"]},
                {"projects/alpha/notes/linked.md", "projects/beta/notes/beta.md"},
            )

            beta = project_report(db, "projects/beta")
            self.assertEqual(beta["hub_path"], "projects/beta/README.md")
            self.assertEqual(beta["orphan_count"], 0)

    def test_write_project_reports_creates_local_markdown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            (root / "projects" / "alpha").mkdir(parents=True)
            (root / "projects" / "alpha" / "README.md").write_text("# Alpha Hub\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)

            output_dir = Path(tmp) / "reports"
            result = write_project_reports(db, output_dir=output_dir)
            self.assertEqual(result["project_count"], 1)
            self.assertTrue((output_dir / "README.md").exists())
            self.assertTrue((output_dir / "alpha.md").exists())
            self.assertIn("Project Report: alpha", (output_dir / "alpha.md").read_text())


if __name__ == "__main__":
    unittest.main()
