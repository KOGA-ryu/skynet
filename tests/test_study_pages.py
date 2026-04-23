from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_study_streams import (
    build_study_catalog,
    create_probability_like_extract,
    create_topological_partial_extract,
)
from wiki_tool.cli import build_parser
from wiki_tool.study_pages import (
    build_study_pages,
    chapter_label_text,
    dashboard_book_page_path,
    render_chapter_page,
    study_dashboard_navigation_index,
    study_page_show,
    study_page_summary,
    study_selection_key,
)
from wiki_tool.study_streams import build_study_materials


class StudyPagesTests(unittest.TestCase):
    def test_study_pages_summary_uses_manifests_when_index_is_narrowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            create_topological_partial_extract(source_root)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            index_path = output_dir / "math" / "index.json"
            narrowed = json.loads(index_path.read_text(encoding="utf-8"))
            narrowed["book_count"] = 1
            narrowed["built_count"] = 1
            narrowed["materialized_count"] = 1
            narrowed["books"] = narrowed["books"][:1]
            index_path.write_text(json.dumps(narrowed, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            summary = study_page_summary(
                db,
                source_root=source_root,
                output_dir=output_dir,
                wiki_root=wiki_root,
            )

            self.assertEqual(summary["book_count"], 3)
            self.assertGreaterEqual(summary["definition_term_count"], 2)
            self.assertGreaterEqual(summary["definition_source_count"], 4)
            self.assertEqual(summary["note_backed_count"], 2)
            self.assertEqual(summary["manifest_only_count"], 1)
            self.assertEqual(summary["result_term_count"], 2)
            self.assertEqual(
                {book["document_id"] for book in summary["books"]},
                {"orphan_probability_text", "probability_measure", "topological_manifolds"},
            )

    def test_build_study_pages_writes_hub_book_and_chapter_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            self._seed_named_theorem(source_root / "probability_measure")
            create_topological_partial_extract(source_root)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            result = build_study_pages(
                db,
                source_root=source_root,
                output_dir=output_dir,
                wiki_root=wiki_root,
            )

            self.assertEqual(result["book_count"], 3)
            hub_path = wiki_root / "projects" / "math_library" / "README.md"
            definitions_hub_path = wiki_root / "projects" / "math_library" / "definitions" / "README.md"
            definitions_letter_path = wiki_root / "projects" / "math_library" / "definitions" / "by_letter" / "p.md"
            results_hub_path = wiki_root / "projects" / "math_library" / "results" / "README.md"
            results_letter_path = wiki_root / "projects" / "math_library" / "results" / "by_letter" / "c.md"
            dashboard_hub_path = wiki_root / "projects" / "study_dashboard" / "README.md"
            dashboard_book_path = wiki_root / "projects" / "study_dashboard" / "books" / "probability_measure.md"
            dashboard_index_path = wiki_root / "projects" / "study_dashboard" / "state" / "navigation_index.json"
            probability_path = wiki_root / "projects" / "math_library" / "books" / "probability_measure" / "README.md"
            probability_chapter_path = wiki_root / "projects" / "math_library" / "books" / "probability_measure" / "chapters" / "ch_01.md"
            topological_path = wiki_root / "projects" / "math_library" / "books" / "topological_manifolds" / "README.md"
            orphan_path = wiki_root / "projects" / "math_library" / "books" / "orphan_probability_text" / "README.md"
            nav_index_path = wiki_root / "projects" / "math_library" / "state" / "navigation_index.json"

            for path in [
                hub_path,
                definitions_hub_path,
                definitions_letter_path,
                results_hub_path,
                results_letter_path,
                dashboard_hub_path,
                dashboard_book_path,
                dashboard_index_path,
                probability_path,
                probability_chapter_path,
                topological_path,
                orphan_path,
                nav_index_path,
            ]:
                self.assertTrue(path.exists(), path)

            hub_text = hub_path.read_text(encoding="utf-8")
            self.assertIn("# Math Library Hub", hub_text)
            self.assertIn("Study Dashboard", hub_text)
            self.assertIn("Definitions Index", hub_text)
            self.assertIn("Results Index", hub_text)
            self.assertIn("Blocked Books", hub_text)
            self.assertIn("topological_manifolds", hub_text)

            dashboard_hub_text = dashboard_hub_path.read_text(encoding="utf-8")
            self.assertIn("# Study Dashboard", dashboard_hub_text)
            self.assertIn("Whole book: `<document_id>::__entire__`", dashboard_hub_text)
            self.assertIn("probability_measure::__entire__", dashboard_hub_text)
            self.assertIn("[Math Library Hub](../math_library/README.md)", dashboard_hub_text)
            self.assertIn("`vox` launch commands:", dashboard_hub_text)
            self.assertIn("`discoflash` launch commands:", dashboard_hub_text)
            self.assertIn("## Continue Studying", dashboard_hub_text)
            self.assertIn("### Continue in vox", dashboard_hub_text)
            self.assertIn("### Continue in discoflash", dashboard_hub_text)
            self.assertIn("### Start Fresh", dashboard_hub_text)
            self.assertIn("## Study Journal", dashboard_hub_text)
            self.assertIn("### Books In Progress", dashboard_hub_text)
            self.assertIn("## Next Up", dashboard_hub_text)
            self.assertIn("### Suggested next chapters", dashboard_hub_text)
            self.assertIn("## Review Queue", dashboard_hub_text)
            self.assertIn("- none", dashboard_hub_text)

            dashboard_book_text = dashboard_book_path.read_text(encoding="utf-8")
            self.assertIn("## Study Status", dashboard_book_text)
            self.assertIn("selection_key: `probability_measure::__entire__`", dashboard_book_text)
            self.assertIn("probability_measure::ch_01", dashboard_book_text)
            self.assertIn("[Math Library Overview](../../math_library/books/probability_measure/README.md)", dashboard_book_text)
            self.assertIn("python3 main.py --study-selection 'probability_measure::__entire__'", dashboard_book_text)
            self.assertIn("python3 app/main.py --study-selection 'probability_measure::ch_01'", dashboard_book_text)
            self.assertIn("--resume", dashboard_book_text)
            self.assertIn("## Recent Activity For This Book", dashboard_book_text)

            definitions_hub_text = definitions_hub_path.read_text(encoding="utf-8")
            self.assertIn("# Definitions Index", definitions_hub_text)
            self.assertIn("[P](by_letter/p.md)", definitions_hub_text)

            definitions_letter_text = definitions_letter_path.read_text(encoding="utf-8")
            self.assertIn("## Probability", definitions_letter_text)
            self.assertIn("Probability and Measure", definitions_letter_text)
            self.assertIn("`definition_heading`", definitions_letter_text)

            results_hub_text = results_hub_path.read_text(encoding="utf-8")
            self.assertIn("# Results Index", results_hub_text)
            self.assertIn("[C](by_letter/c.md)", results_hub_text)

            results_letter_text = results_letter_path.read_text(encoding="utf-8")
            self.assertIn("## Convergence theorem", results_letter_text)
            self.assertIn("`named_theorem`", results_letter_text)
            self.assertIn("Probability and Measure", results_letter_text)

            probability_text = probability_path.read_text(encoding="utf-8")
            self.assertIn("## Curated Source Note", probability_text)
            self.assertIn("Why This Source Matters", probability_text)
            self.assertIn("Source Note](../../../../sources/math/probability_measure.md)", probability_text)
            self.assertIn("[Reader Stream](../../../../../study_materials/math/probability_measure/reader_stream.jsonl)", probability_text)
            self.assertIn("Definitions Index", probability_text)
            self.assertIn("Results Index", probability_text)

            chapter_text = probability_chapter_path.read_text(encoding="utf-8")
            self.assertIn("## Key Definitions", chapter_text)
            self.assertIn("### Definition 1.1 Sigma Algebra", chapter_text)
            self.assertIn("A sigma algebra is a collection of sets", chapter_text)

            root_index = (wiki_root / "index.md").read_text(encoding="utf-8")
            self.assertIn("Study Dashboard](projects/study_dashboard/README.md)", root_index)
            self.assertIn("Math Library Hub](projects/math_library/README.md)", root_index)

            math_hub = (wiki_root / "sources" / "math" / "README.md").read_text(encoding="utf-8")
            self.assertIn("Generated Math Library Hub](../../projects/math_library/README.md)", math_hub)

            nav_index = json.loads(nav_index_path.read_text(encoding="utf-8"))
            self.assertEqual(nav_index["book_count"], 3)
            self.assertEqual(nav_index["definition_term_count"], 3)
            self.assertEqual(nav_index["result_term_count"], 3)
            self.assertEqual(nav_index["index_paths"]["dashboard"], "projects/study_dashboard/README.md")
            self.assertEqual(nav_index["index_paths"]["definitions"], "projects/math_library/definitions/README.md")

            dashboard_index = json.loads(dashboard_index_path.read_text(encoding="utf-8"))
            self.assertEqual(dashboard_index["book_count"], 3)
            self.assertEqual(dashboard_index["dashboard_hub_path"], "projects/study_dashboard/README.md")
            self.assertIn("apps", dashboard_index)
            self.assertIn("continue_studying", dashboard_index)
            self.assertIn("study_journal", dashboard_index)
            self.assertIn("review_queue", dashboard_index)
            self.assertIn("vox", dashboard_index["apps"])
            self.assertIn("discoflash", dashboard_index["apps"])
            probability_book = next(book for book in dashboard_index["books"] if book["document_id"] == "probability_measure")
            self.assertEqual(probability_book["selection_key"], "probability_measure::__entire__")
            self.assertEqual(probability_book["dashboard_page_path"], "projects/study_dashboard/books/probability_measure.md")
            self.assertEqual(probability_book["chapters"][0]["selection_key"], "probability_measure::ch_01")
            self.assertIn("vox_commands", probability_book)
            self.assertIn("discoflash_commands", probability_book)
            self.assertIn("fresh", probability_book["vox_commands"])
            self.assertIn("fresh", probability_book["chapters"][0]["discoflash_commands"])
            self.assertEqual(len(dashboard_index["continue_studying"]["vox_resume"]), 0)
            self.assertEqual(len(dashboard_index["continue_studying"]["discoflash_resume"]), 0)
            self.assertGreaterEqual(len(dashboard_index["continue_studying"]["fresh_recommendations"]), 3)
            self.assertEqual(dashboard_index["study_journal"]["summary"]["books_with_active_resume"], 0)
            self.assertEqual(dashboard_index["review_queue"], [])

    def test_study_pages_show_returns_one_book_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            detail = study_page_show(
                db,
                "probability_measure",
                source_root=source_root,
                output_dir=output_dir,
                wiki_root=wiki_root,
            )

            self.assertEqual(detail["book"]["document_id"], "probability_measure")
            self.assertEqual(detail["book"]["chapter_count"], 2)
            self.assertIn("# Probability and Measure", detail["book_markdown"])

    def test_chapter_label_text_avoids_duplicate_prefix(self) -> None:
        self.assertEqual(
            chapter_label_text({"chapter_number": 1, "chapter_title": "Chapter 1", "chapter_id": "ch_01"}),
            "Chapter 1",
        )

    def test_dashboard_selection_helpers(self) -> None:
        self.assertEqual(study_selection_key("probability_measure", None), "probability_measure::__entire__")
        self.assertEqual(
            str(dashboard_book_page_path(document_id="probability_measure")),
            "projects/study_dashboard/books/probability_measure.md",
        )

    def test_render_chapter_page_omits_weak_key_definitions(self) -> None:
        book = {
            "book_title": "Probability and Measure",
            "document_id": "probability_measure",
            "page_paths": {"book": "projects/math_library/books/probability_measure/README.md"},
        }
        chapter = {
            "card_count": 2,
            "cards": [
                {"term": "project", "card_source_kind": "inline_definition"},
                {"term": "which", "card_source_kind": "inline_definition"},
            ],
            "chapter_id": "ch_01",
            "chapter_number": 1,
            "chapter_title": "Chapter 1",
            "page_path": "projects/math_library/books/probability_measure/chapters/ch_01.md",
            "row_count": 1,
            "rows": [{"reader_text": "A sigma algebra is a collection of sets.", "title_path": "Chapter 1 > Definition 1.1 Sigma Algebra"}],
        }

        markdown = render_chapter_page(book, chapter, wiki_root=Path("/tmp/wiki"))

        self.assertNotIn("## Key Definitions", markdown)

    def test_term_indexes_filter_noisy_result_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            self._seed_named_theorem(source_root / "probability_measure")
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            build_study_pages(
                db,
                source_root=source_root,
                output_dir=output_dir,
                wiki_root=wiki_root,
            )

            results_letter_path = wiki_root / "projects" / "math_library" / "results" / "by_letter" / "c.md"
            text = results_letter_path.read_text(encoding="utf-8")
            self.assertNotIn("[4], Theorem 2.1", text)
            self.assertNotIn("Naturality of Connecting Homomorphisms). Suppose", text)

    def test_build_study_pages_prunes_stale_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            stale_path = wiki_root / "projects" / "math_library" / "results" / "by_letter" / "z.md"
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_text("# stale\n", encoding="utf-8")

            build_study_pages(
                db,
                source_root=source_root,
                output_dir=output_dir,
                wiki_root=wiki_root,
            )

            self.assertFalse(stale_path.exists())

    def test_dashboard_progress_overlay_reads_local_app_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            vox_root = Path(tmp) / "vox"
            discoflash_root = Path(tmp) / "discoflash"
            (vox_root / "app").mkdir(parents=True, exist_ok=True)
            (discoflash_root / "app").mkdir(parents=True, exist_ok=True)
            (vox_root / "app" / "main.py").write_text("print('vox')\n", encoding="utf-8")
            (discoflash_root / "app" / "main.py").write_text("print('discoflash')\n", encoding="utf-8")
            (vox_root / ".session_memory").mkdir(parents=True, exist_ok=True)
            (vox_root / ".session_memory" / "reading_progress.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_selection_key": "probability_measure::ch_01",
                        "positions": {
                            "probability_measure::ch_01": {
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1",
                                "sentence_index": 3,
                                "sentence_count": 12,
                                "text_sha256": "hash",
                                "updated_at_utc": "2026-04-19T09:30:00+00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (vox_root / ".session_memory" / "study_events.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "vox-1",
                        "app": "vox",
                        "event_type": "session_checkpoint",
                        "selection_key": "probability_measure::ch_01",
                        "document_id": "probability_measure",
                        "book_title": "Probability and Measure",
                        "chapter_id": "ch_01",
                        "chapter_label": "Chapter 1: Measure Spaces",
                        "occurred_at_utc": "2026-04-19T09:31:00+00:00",
                        "is_resume": True,
                        "source": "in_app",
                        "payload": {
                            "sentence_index": 3,
                            "sentence_count": 12,
                            "progress_percent": 33,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (vox_root / ".session_memory" / "study_completion.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_completed_selection_key": "probability_measure::ch_01",
                        "completed": {
                            "probability_measure::ch_01": {
                                "selection_key": "probability_measure::ch_01",
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1: Measure Spaces",
                                "completed_at_utc": "2026-04-19T09:32:00+00:00",
                                "source": "in_app",
                                "payload": {"sentence_count": 12, "progress_percent": 100},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (vox_root / ".session_memory" / "study_review.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_reviewed_selection_key": "probability_measure::ch_01",
                        "reviews": {
                            "probability_measure::ch_01": {
                                "selection_key": "probability_measure::ch_01",
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1: Measure Spaces",
                                "stage_index": 0,
                                "last_reviewed_at_utc": "2020-04-19T09:30:00+00:00",
                                "next_due_at_utc": "2020-04-18T09:30:00+00:00",
                                "source": "in_app",
                                "payload": {"sentence_count": 12, "progress_percent": 100},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (vox_root / ".session_memory" / "study_review.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_reviewed_selection_key": "probability_measure::ch_01",
                        "reviews": {
                            "probability_measure::ch_01": {
                                "selection_key": "probability_measure::ch_01",
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1: Measure Spaces",
                                "stage_index": 0,
                                "last_reviewed_at_utc": "2020-04-19T09:30:00+00:00",
                                "next_due_at_utc": "2020-04-18T09:30:00+00:00",
                                "source": "in_app",
                                "payload": {"sentence_count": 12, "progress_percent": 100},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (vox_root / ".session_memory" / "study_review.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_reviewed_selection_key": "probability_measure::ch_01",
                        "reviews": {
                            "probability_measure::ch_01": {
                                "selection_key": "probability_measure::ch_01",
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1: Measure Spaces",
                                "stage_index": 0,
                                "last_reviewed_at_utc": "2020-04-19T09:30:00+00:00",
                                "next_due_at_utc": "2020-04-18T09:30:00+00:00",
                                "source": "in_app",
                                "payload": {"sentence_count": 12, "progress_percent": 100},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "wiki_tool.study_pages.study_dashboard_app_roots",
                return_value={
                    "vox": {"root": vox_root, "entrypoint": vox_root / "app" / "main.py"},
                    "discoflash": {
                        "root": discoflash_root,
                        "entrypoint": discoflash_root / "app" / "main.py",
                    },
                },
            ):
                build_study_pages(
                    db,
                    source_root=source_root,
                    output_dir=output_dir,
                    wiki_root=wiki_root,
                )

            dashboard_hub_text = (wiki_root / "projects" / "study_dashboard" / "README.md").read_text(encoding="utf-8")
            dashboard_book_text = (
                wiki_root / "projects" / "study_dashboard" / "books" / "probability_measure.md"
            ).read_text(encoding="utf-8")
            dashboard_index = json.loads(
                (wiki_root / "projects" / "study_dashboard" / "state" / "navigation_index.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertIn("`vox` resumable selections: `1`", dashboard_hub_text)
            self.assertIn("`discoflash` resumable selections: `0`", dashboard_hub_text)
            self.assertIn("`vox` last_selection_key: `probability_measure::ch_01`", dashboard_hub_text)
            self.assertIn("`vox` completed chapters: `1`", dashboard_hub_text)
            self.assertIn("combined completed chapters: `1`", dashboard_hub_text)
            self.assertIn("### Continue in vox", dashboard_hub_text)
            self.assertIn("Probability and Measure — Chapter 1: Measure Spaces", dashboard_hub_text)
            self.assertIn("resume 4/12 (33%); last active", dashboard_hub_text)
            self.assertIn("### Continue in discoflash", dashboard_hub_text)
            self.assertIn("### Start Fresh", dashboard_hub_text)
            self.assertIn("## Study Journal", dashboard_hub_text)
            self.assertIn("### Books In Progress", dashboard_hub_text)
            self.assertIn("Probability and Measure", dashboard_hub_text)
            self.assertIn("1/2", dashboard_hub_text)
            self.assertIn("## Recent Activity", dashboard_hub_text)
            self.assertIn("### Recent in vox", dashboard_hub_text)
            self.assertIn("### Recent in discoflash", dashboard_hub_text)
            self.assertIn("### Recent across apps", dashboard_hub_text)
            self.assertIn("checkpoint 4/12 (33%)", dashboard_hub_text)
            self.assertIn("## Next Up", dashboard_hub_text)
            self.assertIn("### Suggested next chapters", dashboard_hub_text)
            self.assertIn("This reflects append-only local study events.", dashboard_hub_text)
            self.assertIn("## Recently Completed", dashboard_hub_text)
            self.assertIn("## Review Queue", dashboard_hub_text)
            self.assertIn("Chapter 1: Measure Spaces", dashboard_hub_text)
            self.assertIn("- none", dashboard_hub_text)
            self.assertIn("completed_chapters: `1` / `2`", dashboard_book_text)
            self.assertIn("combined completed: `1` / `2` chapters", dashboard_book_text)
            self.assertIn("## Study Status", dashboard_book_text)
            self.assertIn("first_incomplete_chapter: `Chapter 2: Probability`", dashboard_book_text)
            self.assertIn("## Recent Activity For This Book", dashboard_book_text)
            self.assertIn("`session_checkpoint`", dashboard_book_text)
            self.assertIn("## Completed Chapters", dashboard_book_text)
            self.assertIn("`vox` progress: idle", dashboard_book_text)
            self.assertIn("resume 4/12 (33%)", dashboard_book_text)
            self.assertIn("| Chapter 1: Measure Spaces | 3 | 2 | `probability_measure::ch_01` | `vox` |", dashboard_book_text)
            self.assertIn("| Chapter 1: Measure Spaces | `vox` | `2026-04-19T09:32:00+00:00` |", dashboard_book_text)
            self.assertIn("| idle |", dashboard_book_text)

            self.assertEqual(dashboard_index["apps"]["vox"]["status"], "resume_available")
            self.assertEqual(dashboard_index["apps"]["vox"]["last_selection_key"], "probability_measure::ch_01")
            self.assertEqual(dashboard_index["apps"]["discoflash"]["status"], "idle")
            self.assertEqual(dashboard_index["app_completion"]["vox"]["completed_count"], 1)
            self.assertEqual(dashboard_index["completed_chapter_count"], 1)
            self.assertEqual(dashboard_index["continue_studying"]["vox_resume"][0]["selection_key"], "probability_measure::ch_01")
            self.assertEqual(dashboard_index["continue_studying"]["vox_resume"][0]["kind"], "vox_resume")
            self.assertIn("--resume", dashboard_index["continue_studying"]["vox_resume"][0]["preferred_command"])
            self.assertEqual(dashboard_index["recent_activity"]["vox"][0]["selection_key"], "probability_measure::ch_01")
            self.assertEqual(dashboard_index["recent_activity"]["vox"][0]["app"], "vox")
            self.assertIn("--resume", dashboard_index["recent_activity"]["vox"][0]["preferred_command"])
            self.assertEqual(dashboard_index["recent_activity"]["discoflash"], [])
            self.assertEqual(dashboard_index["recent_activity"]["merged"][0]["selection_key"], "probability_measure::ch_01")
            self.assertEqual(dashboard_index["recently_completed"], [])
            self.assertEqual(dashboard_index["next_up"][0]["selection_key"], "probability_measure::ch_02")
            self.assertEqual(dashboard_index["review_queue"], [])
            fresh_keys = {
                entry["selection_key"]
                for entry in dashboard_index["continue_studying"]["fresh_recommendations"]
            }
            self.assertNotIn("probability_measure::__entire__", fresh_keys)
            probability_book = next(book for book in dashboard_index["books"] if book["document_id"] == "probability_measure")
            self.assertEqual(probability_book["vox_progress"]["status"], "idle")
            self.assertEqual(probability_book["chapters"][0]["vox_progress"]["progress_percent"], 33)
            self.assertEqual(probability_book["chapters"][0]["discoflash_progress"]["status"], "idle")
            self.assertEqual(probability_book["completion_counts"]["combined"], 1)
            self.assertEqual(probability_book["chapters"][0]["completion_status"], "vox")
            self.assertEqual(probability_book["study_journal"]["next_incomplete_chapter"], "Chapter 2: Probability")
            self.assertEqual(probability_book["recent_activity_entries"][0]["selection_key"], "probability_measure::ch_01")

    def test_dashboard_next_up_recommends_immediate_next_chapter_after_completed_vox_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            vox_root = Path(tmp) / "vox"
            discoflash_root = Path(tmp) / "discoflash"
            (vox_root / "app").mkdir(parents=True, exist_ok=True)
            (discoflash_root / "app").mkdir(parents=True, exist_ok=True)
            (vox_root / "app" / "main.py").write_text("print('vox')\n", encoding="utf-8")
            (discoflash_root / "app" / "main.py").write_text("print('discoflash')\n", encoding="utf-8")
            (vox_root / ".session_memory").mkdir(parents=True, exist_ok=True)
            (vox_root / ".session_memory" / "study_completion.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_completed_selection_key": "probability_measure::ch_01",
                        "completed": {
                            "probability_measure::ch_01": {
                                "selection_key": "probability_measure::ch_01",
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1: Measure Spaces",
                                "completed_at_utc": "2026-04-19T09:30:00+00:00",
                                "source": "in_app",
                                "payload": {"sentence_count": 12, "progress_percent": 100},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (vox_root / ".session_memory" / "study_review.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_reviewed_selection_key": "probability_measure::ch_01",
                        "reviews": {
                            "probability_measure::ch_01": {
                                "selection_key": "probability_measure::ch_01",
                                "document_id": "probability_measure",
                                "book_title": "Probability and Measure",
                                "chapter_id": "ch_01",
                                "chapter_label": "Chapter 1: Measure Spaces",
                                "stage_index": 0,
                                "last_reviewed_at_utc": "2020-04-19T09:30:00+00:00",
                                "next_due_at_utc": "2020-04-18T09:30:00+00:00",
                                "source": "in_app",
                                "payload": {"sentence_count": 12, "progress_percent": 100},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "wiki_tool.study_pages.study_dashboard_app_roots",
                return_value={
                    "vox": {"root": vox_root, "entrypoint": vox_root / "app" / "main.py"},
                    "discoflash": {
                        "root": discoflash_root,
                        "entrypoint": discoflash_root / "app" / "main.py",
                    },
                },
            ):
                build_study_pages(
                    db,
                    source_root=source_root,
                    output_dir=output_dir,
                    wiki_root=wiki_root,
                )

            dashboard_hub_text = (wiki_root / "projects" / "study_dashboard" / "README.md").read_text(encoding="utf-8")
            dashboard_index = json.loads(
                (wiki_root / "projects" / "study_dashboard" / "state" / "navigation_index.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertIn("## Next Up", dashboard_hub_text)
            self.assertIn("Probability and Measure", dashboard_hub_text)
            self.assertIn("Chapter 2: Probability", dashboard_hub_text)
            self.assertIn("after completing Chapter 1: Measure Spaces", dashboard_hub_text)
            self.assertIn("python3 main.py --study-selection 'probability_measure::ch_02'", dashboard_hub_text)
            self.assertIn("python3 app/main.py --study-selection 'probability_measure::ch_02'", dashboard_hub_text)
            self.assertIn("## Review Queue", dashboard_hub_text)
            self.assertIn("python3 main.py --study-selection 'probability_measure::ch_01'", dashboard_hub_text)

            self.assertEqual(len(dashboard_index["next_up"]), 1)
            next_up = dashboard_index["next_up"][0]
            self.assertEqual(next_up["selection_key"], "probability_measure::ch_02")
            self.assertEqual(next_up["source_selection_key"], "probability_measure::ch_01")
            self.assertEqual(next_up["target_chapter_label"], "Chapter 2: Probability")
            self.assertEqual(next_up["discoflash_supported"], True)
            self.assertIn("python3 main.py --study-selection 'probability_measure::ch_02'", next_up["preferred_command"])
            self.assertEqual(dashboard_index["review_queue"][0]["selection_key"], "probability_measure::ch_01")
            fresh_keys = {
                entry["selection_key"]
                for entry in dashboard_index["continue_studying"]["fresh_recommendations"]
            }
            self.assertNotIn("probability_measure::__entire__", fresh_keys)

    def test_dashboard_recent_activity_includes_discoflash_resumable_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            vox_root = Path(tmp) / "vox"
            discoflash_root = Path(tmp) / "discoflash"
            (vox_root / "app").mkdir(parents=True, exist_ok=True)
            (discoflash_root / "app").mkdir(parents=True, exist_ok=True)
            (vox_root / "app" / "main.py").write_text("print('vox')\n", encoding="utf-8")
            (discoflash_root / "app" / "main.py").write_text("print('discoflash')\n", encoding="utf-8")
            (discoflash_root / ".session_memory").mkdir(parents=True, exist_ok=True)
            (discoflash_root / ".session_memory" / "study_events.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "disco-1",
                        "app": "discoflash",
                        "event_type": "session_checkpoint",
                        "selection_key": "probability_measure::ch_02",
                        "document_id": "probability_measure",
                        "book_title": "Probability and Measure",
                        "chapter_id": "ch_02",
                        "chapter_label": "Chapter 2: Probability",
                        "occurred_at_utc": "2026-04-19T10:00:00+00:00",
                        "is_resume": True,
                        "source": "dashboard",
                        "payload": {
                            "mode": "quiz",
                            "correct_count": 1,
                            "answered_count": 1,
                            "remaining_count": 2,
                            "attempts": 2,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "wiki_tool.study_pages.study_dashboard_app_roots",
                return_value={
                    "vox": {"root": vox_root, "entrypoint": vox_root / "app" / "main.py"},
                    "discoflash": {
                        "root": discoflash_root,
                        "entrypoint": discoflash_root / "app" / "main.py",
                    },
                },
            ):
                build_study_pages(
                    db,
                    source_root=source_root,
                    output_dir=output_dir,
                    wiki_root=wiki_root,
                )

            dashboard_hub_text = (wiki_root / "projects" / "study_dashboard" / "README.md").read_text(encoding="utf-8")
            dashboard_index = json.loads(
                (wiki_root / "projects" / "study_dashboard" / "state" / "navigation_index.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertIn("### Recent in discoflash", dashboard_hub_text)
            self.assertIn("checkpoint quiz 1 correct, 2 remaining", dashboard_hub_text)
            self.assertIn("python3 app/main.py --study-selection 'probability_measure::ch_02' --resume", dashboard_hub_text)
            self.assertIn("### Recent across apps", dashboard_hub_text)

            self.assertEqual(dashboard_index["recent_activity"]["vox"], [])
            self.assertEqual(dashboard_index["recent_activity"]["discoflash"][0]["selection_key"], "probability_measure::ch_02")
            self.assertEqual(dashboard_index["recent_activity"]["discoflash"][0]["app"], "discoflash")
            self.assertEqual(dashboard_index["recent_activity"]["merged"][0]["selection_key"], "probability_measure::ch_02")

    def test_dashboard_recently_completed_uses_append_only_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, wiki_root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)
            self._seed_wiki_root(wiki_root)

            vox_root = Path(tmp) / "vox"
            discoflash_root = Path(tmp) / "discoflash"
            (vox_root / "app").mkdir(parents=True, exist_ok=True)
            (discoflash_root / "app").mkdir(parents=True, exist_ok=True)
            (vox_root / "app" / "main.py").write_text("print('vox')\n", encoding="utf-8")
            (discoflash_root / "app" / "main.py").write_text("print('discoflash')\n", encoding="utf-8")
            (discoflash_root / ".session_memory").mkdir(parents=True, exist_ok=True)
            (discoflash_root / ".session_memory" / "study_events.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "disco-complete",
                        "app": "discoflash",
                        "event_type": "session_completed",
                        "selection_key": "probability_measure::ch_02",
                        "document_id": "probability_measure",
                        "book_title": "Probability and Measure",
                        "chapter_id": "ch_02",
                        "chapter_label": "Chapter 2: Probability",
                        "occurred_at_utc": "2026-04-19T10:05:00+00:00",
                        "is_resume": False,
                        "source": "dashboard",
                        "payload": {
                            "mode": "quiz",
                            "correct_count": 3,
                            "answered_count": 3,
                            "remaining_count": 0,
                            "attempts": 4,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "wiki_tool.study_pages.study_dashboard_app_roots",
                return_value={
                    "vox": {"root": vox_root, "entrypoint": vox_root / "app" / "main.py"},
                    "discoflash": {
                        "root": discoflash_root,
                        "entrypoint": discoflash_root / "app" / "main.py",
                    },
                },
            ):
                build_study_pages(
                    db,
                    source_root=source_root,
                    output_dir=output_dir,
                    wiki_root=wiki_root,
                )

            dashboard_hub_text = (wiki_root / "projects" / "study_dashboard" / "README.md").read_text(encoding="utf-8")
            dashboard_index = json.loads(
                (wiki_root / "projects" / "study_dashboard" / "state" / "navigation_index.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertIn("## Recently Completed", dashboard_hub_text)
            self.assertIn("completed quiz 3 correct, 0 remaining", dashboard_hub_text)
            self.assertIn("python3 app/main.py --study-selection 'probability_measure::ch_02'", dashboard_hub_text)
            self.assertEqual(dashboard_index["recently_completed"][0]["selection_key"], "probability_measure::ch_02")
            self.assertEqual(dashboard_index["recently_completed"][0]["event_type"], "session_completed")

    def test_cli_parses_study_page_commands(self) -> None:
        parser = build_parser()
        summary_args = parser.parse_args(["study", "pages", "summary"])
        show_args = parser.parse_args(["study", "pages", "show", "probability_measure"])
        build_args = parser.parse_args(["study", "pages", "build"])

        self.assertEqual(summary_args.func.__name__, "cmd_study_pages_summary")
        self.assertEqual(show_args.func.__name__, "cmd_study_pages_show")
        self.assertEqual(build_args.func.__name__, "cmd_study_pages_build")

    def _seed_wiki_root(self, wiki_root: Path) -> None:
        (wiki_root / "index.md").write_text(
            "# Wiki Index\n\n"
            "## Projects\n\n"
            "- [Computational Math Project Hub](projects/computational_math/README.md)\n",
            encoding="utf-8",
        )

    def _seed_named_theorem(self, book_root: Path) -> None:
        chapter_path = book_root / "chapter_json" / "ch_02" / "chapter.json"
        chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
        chapter["sections"][1]["title"] = "Theorem 2.2 (Convergence theorem)"
        chapter_path.write_text(json.dumps(chapter, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
