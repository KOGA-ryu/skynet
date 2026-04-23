from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.study_quality import (
    BOOK_OVERVIEW_BAD_CHAPTER_LABEL,
    DEFAULT_STUDY_QA_DIR,
    DEFAULT_FINAL_REVIEW_PACKET,
    READER_PAGE_JUNK_RESIDUE,
    INCOMPLETE_EXTRACT,
    READER_JUNK,
    UPSTREAM_NOISY_CARD_LABEL,
    WEAK_CARD_TERM,
    ZERO_CARD_DECK,
    row_looks_like_page_junk,
    study_quality_show,
    study_quality_summary,
    write_study_quality_reports,
)
from wiki_tool.study_streams import DEFAULT_STUDY_DIR, DEFAULT_STUDY_SHELF, build_study_materials


class StudyQualityTests(unittest.TestCase):
    def test_study_quality_summary_reports_known_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_quality_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            summary = study_quality_summary(db, output_dir=output_dir)

            self.assertEqual(summary["book_count"], 5)
            self.assertEqual(summary["completion_bar"], "quality_done")
            self.assertEqual(summary["completion_status"], "fail")
            self.assertEqual(summary["remaining_severe_count"], 3)
            self.assertEqual(summary["remaining_warning_count"], 3)
            self.assertRegex(summary["summary_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("canonical_status_path", summary)
            self.assertIn("consumer_checks", summary)
            self.assertIn("vox_study_library", summary["consumer_checks"])
            self.assertIn("report_statuses", summary)
            self.assertEqual(summary["issue_counts"]["by_category"][READER_JUNK], 1)
            self.assertEqual(summary["issue_counts"]["by_category"][READER_PAGE_JUNK_RESIDUE], 1)
            self.assertEqual(summary["issue_counts"]["by_category"][ZERO_CARD_DECK], 1)
            self.assertEqual(summary["issue_counts"]["by_category"]["structural_card_term"], 0)
            self.assertEqual(summary["issue_counts"]["by_category"]["bad_title"], 0)
            self.assertEqual(summary["issue_counts"]["by_category"][WEAK_CARD_TERM], 0)
            self.assertEqual(summary["issue_counts"]["by_category"][UPSTREAM_NOISY_CARD_LABEL], 0)
            self.assertEqual(summary["issue_counts"]["by_category"][BOOK_OVERVIEW_BAD_CHAPTER_LABEL], 0)
            self.assertEqual(summary["issue_counts"]["by_category"][INCOMPLETE_EXTRACT], 1)
            self.assertEqual(summary["reader_ready_count"], 3)
            self.assertEqual(summary["flashcard_ready_count"], 3)
            self.assertEqual(summary["priority_queue"][0]["document_id"], "junk_text")

            books = {book["document_id"]: book for book in summary["books"]}
            self.assertEqual(books["probability_measure"]["qa_status"], "pass")
            self.assertTrue(books["probability_measure"]["reader_ready"])
            self.assertTrue(books["probability_measure"]["flashcard_ready"])
            self.assertEqual(books["junk_text"]["qa_status"], "fail")
            self.assertFalse(books["junk_text"]["reader_ready"])
            self.assertTrue(books["junk_text"]["flashcard_ready"])
            self.assertEqual(books["zero_cards"]["qa_status"], "fail")
            self.assertTrue(books["zero_cards"]["reader_ready"])
            self.assertFalse(books["zero_cards"]["flashcard_ready"])
            self.assertEqual(books["preface_notes"]["qa_status"], "warn")
            self.assertEqual(books["broken_extract"]["qa_status"], "fail")
            self.assertFalse(books["broken_extract"]["reader_ready"])
            self.assertFalse(books["broken_extract"]["flashcard_ready"])

    def test_study_quality_summary_reports_completion_pass_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            summary = study_quality_summary(db, output_dir=output_dir)

            self.assertEqual(summary["completion_bar"], "quality_done")
            self.assertEqual(summary["completion_status"], "pass")
            self.assertEqual(summary["remaining_severe_count"], 0)
            self.assertEqual(summary["remaining_warning_count"], 0)
            self.assertIn(summary["consumer_checks"]["vox_study_library"]["status"], {"pass", "skip"})

    def test_study_quality_show_returns_book_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_quality_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            detail = study_quality_show(db, "junk_text", output_dir=output_dir)

            self.assertEqual(detail["document_id"], "junk_text")
            self.assertEqual(detail["book_title"], "Junk Text")
            categories = {issue["category"] for issue in detail["issues"]}
            self.assertIn(READER_JUNK, categories)
            self.assertFalse(detail["reader_ready"])
            self.assertTrue(detail["flashcard_ready"])
            self.assertEqual(detail["blocked_reasons"]["reader"], [READER_JUNK])
            self.assertEqual(detail["blocked_reasons"]["flashcard"], [])

    def test_write_study_quality_reports_writes_summary_and_flagged_books(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_quality_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            qa_dir = Path(tmp) / "study_quality"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            result = write_study_quality_reports(db, output_dir=output_dir, qa_output_dir=qa_dir)

            self.assertGreaterEqual(result["flagged_book_count"], 3)
            self.assertEqual(result["completion_bar"], "quality_done")
            self.assertEqual(result["completion_status"], "fail")
            target_root = qa_dir / DEFAULT_STUDY_SHELF
            self.assertTrue((target_root / "summary.json").exists())
            self.assertTrue((target_root / "README.md").exists())
            self.assertTrue((target_root / DEFAULT_FINAL_REVIEW_PACKET).exists())
            self.assertTrue((target_root / "books" / "junk_text.md").exists())
            self.assertRegex(result["summary_sha256"], r"^[0-9a-f]{64}$")

    def test_write_study_quality_reports_prunes_stale_book_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_quality_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            qa_dir = Path(tmp) / "study_quality"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            target_root = qa_dir / DEFAULT_STUDY_SHELF / "books"
            target_root.mkdir(parents=True, exist_ok=True)
            stale_path = target_root / "stale_old_issue.md"
            stale_path.write_text("stale\n", encoding="utf-8")
            qa_root = qa_dir / DEFAULT_STUDY_SHELF
            legacy_markdown = qa_root / "corpus_deficiency_list.md"
            legacy_inventory = qa_root / "deficiency_inventory.json"
            legacy_markdown.parent.mkdir(parents=True, exist_ok=True)
            legacy_markdown.write_text("legacy deficiency report\n", encoding="utf-8")
            legacy_inventory.write_text("{}\n", encoding="utf-8")

            result = write_study_quality_reports(db, output_dir=output_dir, qa_output_dir=qa_dir)

            self.assertFalse(stale_path.exists())
            self.assertTrue((target_root / "junk_text.md").exists())
            self.assertFalse(legacy_markdown.exists())
            self.assertFalse(legacy_inventory.exists())
            self.assertEqual(len(result["archived_files"]), 2)
            for archived in result["archived_files"]:
                self.assertTrue(Path(archived).exists())
            summary_data = json.loads((qa_root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary_data["report_statuses"]["README.md"]["status"], "current")
            self.assertEqual(summary_data["report_statuses"][DEFAULT_FINAL_REVIEW_PACKET]["status"], "current")
            self.assertEqual(summary_data["report_statuses"]["corpus_deficiency_list.md"]["status"], "absent")
            self.assertEqual(summary_data["report_statuses"]["deficiency_inventory.json"]["status"], "absent")

    def test_study_quality_summary_requires_built_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "run `wiki study build` first"):
                study_quality_summary(output_dir=Path(tmp) / "study_materials")

    def test_study_quality_summary_uses_manifests_when_index_is_narrowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_quality_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            index_path = output_dir / DEFAULT_STUDY_SHELF / "index.json"
            narrowed = json.loads(index_path.read_text(encoding="utf-8"))
            narrowed["book_count"] = 1
            narrowed["built_count"] = 1
            narrowed["materialized_count"] = 1
            narrowed["books"] = narrowed["books"][:1]
            index_path.write_text(json.dumps(narrowed, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            summary = study_quality_summary(db, output_dir=output_dir)

            self.assertEqual(summary["book_count"], 5)
            self.assertEqual(summary["issue_counts"]["by_category"][INCOMPLETE_EXTRACT], 1)

    def test_cli_parses_study_qa_commands(self) -> None:
        parser = build_parser()

        summary_args = parser.parse_args(["study", "qa", "summary"])
        show_args = parser.parse_args(["study", "qa", "show", "probability_measure"])
        write_args = parser.parse_args(["study", "qa", "write"])

        self.assertEqual(summary_args.output_dir, DEFAULT_STUDY_DIR)
        self.assertEqual(summary_args.qa_output_dir, DEFAULT_STUDY_QA_DIR)
        self.assertEqual(show_args.book, "probability_measure")
        self.assertEqual(show_args.output_dir, DEFAULT_STUDY_DIR)
        self.assertEqual(write_args.qa_output_dir, DEFAULT_STUDY_QA_DIR)

    def test_row_page_junk_detector_ignores_normal_notation_exposition_but_flags_back_matter(self) -> None:
        self.assertFalse(
            row_looks_like_page_junk(
                "Hilbert Spaces > §1 Elementary properties",
                "There is no universally accepted notation for an inner product and the reader will often see (x, y) used in the literature.",
            )
        )
        self.assertFalse(
            row_looks_like_page_junk(
                "Probability > Bernoulli and binomial distributions",
                "| Bernoulli | 1 | x in {0,1} |\n| Binomial | N | x in {0,1,...,N} |",
            )
        )
        self.assertFalse(
            row_looks_like_page_junk(
                "Convex Optimization > Scalar composition",
                "``` f is convex if h is convex, h~ is nondecreasing, and g is convex. ```",
            )
        )
        self.assertTrue(
            row_looks_like_page_junk(
                "Functional Analysis > PURE AND APPLIED MATHEMATICS",
                "A Wiley-Interscience Series of Texts, Monographs, and Tracts\nFounded by RICHARD COURANT",
            )
        )
        self.assertTrue(
            row_looks_like_page_junk(
                "Some Underlying Geometric Notions > Some Underlying Geometric Notions",
                "| 2.2PrefaceIXStandardNotationsxii. |\n| SomeChapterUnderlyingGeometricNotions0.1 |\n| Chapter 2. Homology |\n| 2.1. Simplicial and Singular Homology 160 |",
            )
        )
        self.assertTrue(
            row_looks_like_page_junk(
                "Homology > Homology",
                "| Chapter 2. Homology | |--------------------------------------------------------------| "
                "| 2.1. Simplicial and Singular Homology | | 2.2. Computations and Applications | "
                "| 2.3. The Formal Viewpoint |",
            )
        )
        self.assertFalse(
            row_looks_like_page_junk(
                "3.2 The main theorem and key estimate",
                "with $c' = c ||f||_{L^2(\\Omega)}$ . Hence $\\ell_0$ is bounded on the pre-Hilbert space "
                "$\\mathcal{H}_0$ . See Section 5.1, Chapter 4 and Theorem 5.3 in Chapter 4.",
            )
        )


def build_quality_catalog(tmp: str) -> tuple[Path, Path, Path]:
    db, root, source_root = build_study_catalog(tmp)
    create_junk_heavy_extract(source_root / "junk_text")
    create_zero_card_extract(source_root / "zero_cards")
    create_preface_extract(source_root / "preface_notes")
    create_incomplete_extract(source_root / "broken_extract")
    (root / "sources" / "math" / "junk_text.md").write_text(
        "# junk text\n\n"
        "- corpus: `math`\n"
        "- document_id: `junk_text`\n"
    )
    (root / "sources" / "math" / "preface_notes.md").write_text(
        "# preface notes\n\n"
        "- corpus: `math`\n"
        "- document_id: `preface_notes`\n"
    )
    scan_wiki(root, db)
    return db, root, source_root


def build_study_catalog(tmp: str) -> tuple[Path, Path, Path]:
    root = Path(tmp) / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "sources" / "math").mkdir(parents=True)

    (root / "concepts" / "sigma_algebra.md").write_text(
        "# Sigma Algebra\n\n"
        "A sigma algebra is a collection of sets closed under complements and countable unions.\n\n"
        "## Relevant Sources\n\n"
        "- [Probability and Measure](../sources/math/probability_measure.md)\n"
    )
    (root / "concepts" / "measure.md").write_text(
        "# Measure\n\n"
        "A measure assigns a non-negative size to sets in a sigma algebra and is countably additive.\n\n"
        "## Relevant Sources\n\n"
        "- [Probability and Measure](../sources/math/probability_measure.md)\n"
    )
    (root / "sources" / "math" / "README.md").write_text("# Math Source Notes\n")
    (root / "sources" / "math" / "probability_measure.md").write_text(
        "# Probability and Measure\n\n"
        "- corpus: `math`\n"
        "- document_id: `probability_measure`\n\n"
        "## Why This Source Matters\n\n"
        "This source ties measure theory to probability spaces and convergence.\n"
    )

    db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, db)

    source_root = Path(tmp) / "source_root"
    create_probability_measure_extract(source_root)
    return db, root, source_root


def create_probability_measure_extract(source_root: Path) -> None:
    book_root = source_root / "probability_measure"
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)
    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {
                "book_title": "Probability and Measure",
                "chapter_count": 1,
                "document_id": "probability_measure",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Measure Spaces"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Measure Spaces",
                "document_id": "probability_measure",
                "sections": [
                    {
                        "content": f"Term {index} is a formal concept in the probability fixture.",
                        "level": 2,
                        "title": f"Definition 1.{index} Term {index}",
                    }
                    for index in range(1, 11)
                ],
                "source_pdf": "probability_measure_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Measure Spaces\n\n"
        "### Definition 1.1 Sigma Algebra\n\n"
        "A sigma algebra is a collection of sets closed under complements and countable unions.\n"
    )


def create_junk_heavy_extract(book_root: Path) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)
    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {
                "book_title": "junk text",
                "chapter_count": 1,
                "document_id": "junk_text",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "table of contents"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "table of contents",
                "document_id": "junk_text",
                "sections": [
                    {
                        "content": "A random variable is a measurable function on a probability space.",
                        "level": 2,
                        "title": "Definition 1.1 Random Variable",
                    },
                    {
                        "content": "Rayleigh quotient, 317. SUBJECT INDEX 579.",
                        "level": 2,
                        "title": "Closing Notes",
                    },
                ],
                "source_pdf": "junk_text_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Random Variable\n\n"
        "A random variable is a measurable function on a probability space.\n"
    )


def create_zero_card_extract(book_root: Path) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)
    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {"book_title": "Zero Cards", "chapter_count": 1, "document_id": "zero_cards"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Theorems"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Theorems",
                "document_id": "zero_cards",
                "sections": [
                    {
                        "content": "Theorem statements alone should not produce definition cards.",
                        "level": 2,
                        "title": "Theorem 1.1 Main Result",
                    }
                ],
                "source_pdf": "zero_cards_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Theorems\n\n"
        "### Theorem 1.1 Main Result\n\n"
        "Theorem statements alone should not produce definition cards.\n"
    )


def create_preface_extract(book_root: Path) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)
    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {"book_title": "preface notes", "chapter_count": 1, "document_id": "preface_notes"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Preface"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Preface",
                "document_id": "preface_notes",
                "sections": [
                    {
                        "content": "A preface is introductory material and should not become a study term.",
                        "level": 2,
                        "title": "Definition 0 Preface",
                    },
                    {
                        "content": "A metric space is a set equipped with a distance function.",
                        "level": 2,
                        "title": "Definition 1.1 Metric Space",
                    }
                ],
                "source_pdf": "preface_notes_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Preface\n\n"
        "### Definition 0 Preface\n\n"
        "A preface is introductory material and should not become a study term.\n\n"
        "### Definition 1.1 Metric Space\n\n"
        "A metric space is a set equipped with a distance function.\n"
    )


def create_incomplete_extract(book_root: Path) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {"book_title": "Broken Extract", "chapter_count": 1, "document_id": "broken_extract"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Missing Json"}, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    unittest.main()
