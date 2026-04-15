from pathlib import Path
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
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

    def test_project_reports_add_librarian_counts_and_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_librarian_catalog(tmp)

            summary = project_report_summary(db)
            projects = {project["project"]: project for project in summary["projects"]}

            self.assertEqual(summary["generated_stub_count"], 1)
            self.assertEqual(summary["reviewable_orphan_count"], 3)
            self.assertEqual(summary["projects"][0]["project"], "alpha")

            alpha = project_report(db, "alpha")
            self.assertEqual(alpha["generated_stub_count"], 1)
            self.assertEqual(alpha["reviewable_orphan_count"], 2)
            self.assertEqual(alpha["template_count"], 1)
            self.assertEqual(alpha["state_artifact_count"], 1)
            self.assertEqual(alpha["unclear_hub_count"], 1)
            self.assertEqual(alpha["top_librarian_actions"][0]["action"], "fill_generated_stubs")
            self.assertEqual(
                {note["path"] for note in alpha["reviewable_orphans"]},
                {
                    "projects/alpha/notes/orphan.md",
                    "projects/alpha/notes/orphan_two.md",
                },
            )
            self.assertIn("beta", projects)

    def test_project_report_limit_trims_details_without_changing_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_librarian_catalog(tmp)

            alpha = project_report(db, "alpha", limit=1)

            self.assertEqual(alpha["limit"], 1)
            self.assertEqual(alpha["reviewable_orphan_count"], 2)
            self.assertEqual(len(alpha["reviewable_orphans"]), 1)
            self.assertEqual(alpha["generated_stub_count"], 1)
            self.assertEqual(len(alpha["generated_stubs"]), 1)

    def test_write_project_reports_includes_librarian_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = build_librarian_catalog(tmp)
            output_dir = Path(tmp) / "reports"

            result = write_project_reports(db, output_dir=output_dir, limit=1)
            index = (output_dir / "README.md").read_text()
            alpha = (output_dir / "alpha.md").read_text()

            self.assertEqual(result["limit"], 1)
            self.assertEqual(result["generated_stub_count"], 1)
            self.assertIn("## Librarian Priority", index)
            self.assertIn("reviewable orphans", index)
            self.assertIn("## Librarian Summary", alpha)
            self.assertIn("## Top Actions", alpha)
            self.assertIn("## Generated Stubs", alpha)
            self.assertIn("## Reviewable Orphans", alpha)
            self.assertIn("## Weak Summaries", alpha)

    def test_project_report_cli_accepts_limit(self) -> None:
        parser = build_parser()

        show_args = parser.parse_args(["project-reports", "show", "alpha", "--limit", "3"])
        write_args = parser.parse_args(["project-reports", "write", "--limit", "4"])

        self.assertEqual(show_args.limit, 3)
        self.assertEqual(write_args.limit, 4)


def build_librarian_catalog(tmp: str) -> Path:
    root = Path(tmp) / "wiki"
    (root / "projects" / "alpha" / "notes").mkdir(parents=True)
    (root / "projects" / "alpha" / "state").mkdir(parents=True)
    (root / "projects" / "alpha" / "templates").mkdir(parents=True)
    (root / "projects" / "beta" / "notes").mkdir(parents=True)

    (root / "index.md").write_text(
        "# Home\n\n"
        "[Alpha](projects/alpha/README.md)\n"
        "[Beta](projects/beta/README.md)\n"
    )
    (root / "projects" / "alpha" / "README.md").write_text(
        "# Alpha Hub\n\n"
        "Short hub.\n\n"
        "[Linked](notes/linked.md)\n"
        "[Generated Stub](notes/stub.md)\n"
    )
    (root / "projects" / "alpha" / "notes" / "linked.md").write_text(
        "# Linked Alpha\n\n"
        "This linked alpha note has enough context to point back to the hub while staying out of orphan queues.\n\n"
        "[Hub](../README.md)\n"
    )
    (root / "projects" / "alpha" / "notes" / "stub.md").write_text(
        "# Stub Alpha\n\n"
        "Generated stub.\n\n"
        "- Status: stub\n"
        "- Content has not been filled in yet.\n"
    )
    (root / "projects" / "alpha" / "notes" / "orphan.md").write_text(
        "# Orphan Alpha\n\n"
        "This orphan alpha note has no inbound links and should stay in the reviewable orphan queue.\n"
    )
    (root / "projects" / "alpha" / "notes" / "orphan_two.md").write_text(
        "# Orphan Alpha Two\n\n"
        "This second orphan alpha note gives the limit test more than one reviewable orphan to trim.\n"
    )
    (root / "projects" / "alpha" / "state" / "generated.md").write_text(
        "# Generated State\n\n"
        "Generated local state should count as a state artifact, not a reviewable orphan.\n"
    )
    (root / "projects" / "alpha" / "templates" / "template.md").write_text(
        "# Alpha Template\n\n"
        "Template content should not be treated as a reviewable orphan.\n"
    )
    (root / "projects" / "beta" / "README.md").write_text(
        "# Beta Hub\n\n"
        "Short hub.\n\n"
        "[Beta Orphan](notes/orphan.md)\n"
    )
    (root / "projects" / "beta" / "notes" / "orphan.md").write_text(
        "# Beta Orphan\n\n"
        "This beta note is linked from its hub but still has weak content for summary reporting.\n"
    )
    (root / "projects" / "beta" / "notes" / "reviewable.md").write_text(
        "# Beta Reviewable\n\n"
        "This beta reviewable note has no inbound links.\n"
    )

    db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, db)
    return db


if __name__ == "__main__":
    unittest.main()
