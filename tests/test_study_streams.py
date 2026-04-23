from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.study_streams import (
    ALL_STRUCTURED_SELECTION,
    CANONICAL_TARGET,
    CARDS_VIEW,
    DEFAULT_STUDY_DIR,
    DEFAULT_STUDY_DISCOFLASH_EXPORT,
    DEFAULT_STUDY_SELECTION,
    DEFAULT_STUDY_SHELF,
    DEFAULT_STUDY_SOURCE_ROOT,
    DEFAULT_STUDY_VIEW,
    DISCOFLASH_TARGET,
    MAINTAINED_ONLY_SELECTION,
    READER_VIEW,
    build_definition_cards,
    build_study_materials,
    export_study_materials,
    format_chapter_label,
    is_junk_section,
    is_display_quality_card_term,
    looks_like_contents_table,
    normalize_chapter_title,
    normalize_named_card_term,
    probe_study_source_roots,
    select_app_title,
    study_inventory,
    study_view,
)


class StudyStreamTests(unittest.TestCase):
    def test_inventory_reports_ready_and_missing_extracts_for_maintained_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)

            inventory = study_inventory(
                db,
                source_root=source_root,
                selection=MAINTAINED_ONLY_SELECTION,
            )

            self.assertEqual(inventory["book_count"], 2)
            self.assertEqual(inventory["ready_count"], 1)
            self.assertEqual(inventory["missing_extract_count"], 1)
            self.assertEqual(inventory["selection"], MAINTAINED_ONLY_SELECTION)
            statuses = {book["document_id"]: book["status"] for book in inventory["books"]}
            self.assertEqual(statuses["probability_measure"], "ready")
            self.assertEqual(statuses["topological_manifolds"], "missing_extract")
            self.assertEqual(inventory["missing_books"][0]["document_id"], "topological_manifolds")
            self.assertEqual(inventory["unmatched_extract_roots"], [])

    def test_inventory_defaults_to_all_structured_extracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")

            inventory = study_inventory(db, source_root=source_root)

            self.assertEqual(inventory["selection"], ALL_STRUCTURED_SELECTION)
            self.assertEqual(inventory["book_count"], 2)
            self.assertEqual(inventory["missing_extract_count"], 0)
            self.assertEqual(inventory["unmatched_extract_roots"], [])
            docs = {book["document_id"]: book for book in inventory["books"]}
            self.assertIn("orphan_probability_text", docs)
            self.assertFalse(docs["orphan_probability_text"]["has_source_note"])
            self.assertEqual(docs["orphan_probability_text"]["title_source"], "book_manifest")

    def test_inventory_reports_unmatched_extract_roots_and_book_filter_for_maintained_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")

            inventory = study_inventory(
                db,
                source_root=source_root,
                selection=MAINTAINED_ONLY_SELECTION,
                book="probability_measure",
            )

            self.assertEqual(inventory["book_count"], 1)
            self.assertEqual(inventory["books"][0]["document_id"], "probability_measure")
            self.assertEqual(
                inventory["unmatched_extract_roots"],
                [str((source_root / "orphan_probability_text").resolve())],
            )

    def test_probe_source_root_ranks_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            good_root = Path(tmp) / "good_root"
            partial_root = Path(tmp) / "partial_root"
            no_match_root = Path(tmp) / "no_match_root"
            create_probability_measure_extract(good_root)
            create_topological_full_extract(good_root)
            create_probability_measure_extract(partial_root)
            create_probability_like_extract(no_match_root / "orphan_probability_text", document_id="orphan_probability_text")

            probe = probe_study_source_roots(
                db,
                paths=[partial_root, no_match_root, good_root],
            )

            self.assertEqual(probe["candidate_count"], 3)
            self.assertEqual(probe["candidates"][0]["candidate_path"], str(good_root.resolve()))
            self.assertEqual(probe["candidates"][0]["status"], "good_candidate")
            self.assertEqual(probe["candidates"][0]["matched_book_count"], 2)
            self.assertEqual(probe["candidates"][1]["candidate_path"], str(partial_root.resolve()))
            self.assertEqual(probe["candidates"][1]["status"], "partial_candidate")
            self.assertEqual(probe["candidates"][1]["missing_book_count"], 1)
            self.assertEqual(probe["candidates"][2]["candidate_path"], str(no_match_root.resolve()))
            self.assertEqual(probe["candidates"][2]["status"], "no_match")
            self.assertEqual(probe["candidates"][2]["unmatched_extract_root_count"], 1)

    def test_probe_source_root_reports_partial_and_invalid_books(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, _source_root = build_study_catalog(tmp)
            candidate = Path(tmp) / "candidate_root"
            create_probability_measure_extract(candidate)
            create_topological_partial_extract(candidate)

            probe = probe_study_source_roots(db, paths=[candidate])
            candidate_payload = probe["candidates"][0]

            self.assertEqual(candidate_payload["status"], "partial_candidate")
            self.assertEqual(candidate_payload["partial_book_count"], 1)
            self.assertEqual(candidate_payload["partial_books"][0]["document_id"], "topological_manifolds")
            self.assertEqual(candidate_payload["ready_book_count"], 1)
            self.assertEqual(candidate_payload["ready_books"][0]["document_id"], "probability_measure")

    def test_build_writes_canonical_reader_and_card_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"

            result = build_study_materials(db, source_root=source_root, output_dir=output_dir)

            self.assertEqual(result["built_count"], 1)
            self.assertEqual(result["missing_extract_count"], 0)
            shelf_root = output_dir / DEFAULT_STUDY_SHELF
            book_root = shelf_root / "probability_measure"
            self.assertTrue((book_root / "reader_stream.jsonl").exists())
            self.assertTrue((book_root / "reader_plain.txt").exists())
            self.assertTrue((book_root / "definition_cards.jsonl").exists())
            self.assertTrue((book_root / "manifest.json").exists())
            self.assertTrue((shelf_root / "index.json").exists())

            rows = [json.loads(line) for line in (book_root / "reader_stream.jsonl").read_text().splitlines() if line.strip()]
            cards = [json.loads(line) for line in (book_root / "definition_cards.jsonl").read_text().splitlines() if line.strip()]
            manifest = json.loads((book_root / "manifest.json").read_text())

            self.assertGreaterEqual(len(rows), 4)
            self.assertEqual(rows[0]["chunk_kind"], "definition")
            self.assertEqual(rows[0]["ordinal"], 1)
            self.assertEqual(rows[0]["chapter_number"], 1)
            self.assertEqual(cards[0]["term"], "Sigma Algebra")
            self.assertIn("Probability", [card["term"] for card in cards])
            self.assertEqual(manifest["status"], "built")
            self.assertEqual(manifest["selection"], ALL_STRUCTURED_SELECTION)
            self.assertEqual(manifest["definition_card_count"], len(cards))
            index = json.loads((shelf_root / "index.json").read_text())
            self.assertEqual(index["materialized_count"], 1)
            self.assertEqual(index["partial_count"], 0)
            self.assertEqual(index["selection"], ALL_STRUCTURED_SELECTION)

    def test_show_returns_built_reader_and_card_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"
            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            reader = study_view(db, "probability_measure", output_dir=output_dir, view=READER_VIEW)
            cards = study_view(db, "Probability and Measure", output_dir=output_dir, view=CARDS_VIEW)

            self.assertEqual(reader["view"], READER_VIEW)
            self.assertGreaterEqual(reader["row_count"], 4)
            self.assertEqual(cards["view"], CARDS_VIEW)
            self.assertIn("Sigma Algebra", [card["term"] for card in cards["cards"]])

    def test_export_canonical_and_discoflash_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            output_dir = Path(tmp) / "study_materials"

            canonical = export_study_materials(
                db,
                source_root=source_root,
                output_dir=output_dir,
                target=CANONICAL_TARGET,
                book="probability_measure",
            )
            discoflash = export_study_materials(
                db,
                source_root=source_root,
                output_dir=output_dir,
                target=DISCOFLASH_TARGET,
                book="probability_measure",
            )

            self.assertEqual(canonical["target"], CANONICAL_TARGET)
            self.assertEqual(canonical["export_count"], 1)
            self.assertTrue(any(path.endswith("reader_stream.jsonl") for path in canonical["files"]))

            self.assertEqual(discoflash["target"], DISCOFLASH_TARGET)
            self.assertEqual(discoflash["export_count"], 1)
            export_path = Path(discoflash["exports"][0]["export_path"])
            self.assertEqual(export_path.name, DEFAULT_STUDY_DISCOFLASH_EXPORT)
            text = export_path.read_text()
            self.assertIn("[definition_matching]", text)
            self.assertIn("[terms]", text)
            self.assertIn("[definitions]", text)
            self.assertEqual(discoflash["exports"][0]["validation"]["status"], "pass")
            self.assertEqual(discoflash["exports"][0]["validation"]["warnings"], [])

    def test_build_includes_structured_extract_without_source_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")
            output_dir = Path(tmp) / "study_materials"

            result = build_study_materials(db, source_root=source_root, output_dir=output_dir)

            self.assertEqual(result["book_count"], 2)
            built_docs = {book["document_id"]: book for book in result["books"]}
            self.assertIn("orphan_probability_text", built_docs)
            self.assertFalse(built_docs["orphan_probability_text"]["has_source_note"])
            self.assertEqual(built_docs["orphan_probability_text"]["title_source"], "book_manifest")
            manifest = json.loads(
                (output_dir / DEFAULT_STUDY_SHELF / "orphan_probability_text" / "manifest.json").read_text()
            )
            self.assertFalse(manifest["has_source_note"])

    def test_inventory_prefers_manifest_title_when_source_note_title_is_not_app_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root, source_root = build_study_catalog(tmp)
            (root / "sources" / "math" / "probability_measure.md").write_text(
                "# probability measure\n\n"
                "- corpus: `math`\n"
                "- document_id: `probability_measure`\n\n"
                "## Why This Source Matters\n\n"
                "This source ties measure theory to probability spaces and convergence.\n"
            )
            scan_wiki(root, db)

            inventory = study_inventory(
                db,
                source_root=source_root,
                selection=MAINTAINED_ONLY_SELECTION,
            )
            book = next(book for book in inventory["books"] if book["document_id"] == "probability_measure")

            self.assertEqual(book["book_title"], "Probability and Measure")
            self.assertEqual(book["title_source"], "book_manifest")

    def test_select_app_title_falls_back_to_directory_name_when_other_titles_are_unusable(self) -> None:
        title, source = select_app_title(
            source_note={"title": "probability_measure"},
            manifest_title="",
            fallback_name="probability_measure",
        )

        self.assertEqual(title, "Probability Measure")
        self.assertEqual(source, "directory_name")

    def test_build_maintained_only_skips_unmatched_structured_extracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_probability_like_extract(source_root / "orphan_probability_text", document_id="orphan_probability_text")
            output_dir = Path(tmp) / "study_materials"

            result = build_study_materials(
                db,
                source_root=source_root,
                output_dir=output_dir,
                selection=MAINTAINED_ONLY_SELECTION,
            )

            self.assertEqual(result["book_count"], 2)
            built_docs = {book["document_id"] for book in result["books"]}
            self.assertNotIn("orphan_probability_text", built_docs)
            self.assertEqual(result["missing_extract_count"], 1)

    def test_build_book_filter_and_partial_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_topological_partial_extract(source_root)
            output_dir = Path(tmp) / "study_materials"

            result = build_study_materials(
                db,
                source_root=source_root,
                output_dir=output_dir,
                selection=MAINTAINED_ONLY_SELECTION,
                book="topological_manifolds",
            )

            self.assertEqual(result["book_count"], 1)
            self.assertEqual(result["built_count"], 0)
            self.assertEqual(result["partial_count"], 1)
            self.assertEqual(result["materialized_count"], 1)
            self.assertEqual(result["books"][0]["status"], "partial")
            manifest = json.loads((output_dir / DEFAULT_STUDY_SHELF / "topological_manifolds" / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(len(manifest["skipped_chapters"]), 1)

    def test_build_admits_markdown_only_extract_and_sections_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_markdown_only_extract(source_root / "murphy_like_markdown_only", document_id="murphy_like_markdown_only")
            output_dir = Path(tmp) / "study_materials"

            result = build_study_materials(
                db,
                source_root=source_root,
                output_dir=output_dir,
            )

            books = {book["document_id"]: book for book in result["books"]}
            self.assertEqual(books["murphy_like_markdown_only"]["status"], "built")
            self.assertGreater(books["murphy_like_markdown_only"]["row_count"], 0)
            manifest = json.loads(
                (output_dir / DEFAULT_STUDY_SHELF / "murphy_like_markdown_only" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "built")
            self.assertEqual(manifest["chapter_count"], 2)
            rows = [
                json.loads(line)
                for line in (
                    output_dir / DEFAULT_STUDY_SHELF / "murphy_like_markdown_only" / "reader_stream.jsonl"
                ).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            row_text = "\n".join(f"{row['chapter_title']} | {row['title_path']}" for row in rows)
            self.assertIn("Variational inference | Variational inference > Introduction", row_text)
            self.assertIn("Variational inference | Variational inference > Variational free energy", row_text)
            self.assertIn("Normalizing Flows | Normalizing Flows > Introduction", row_text)

    def test_build_filters_junk_sections_and_structural_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_junk_heavy_extract(source_root / "junk_text")
            output_dir = Path(tmp) / "study_materials"

            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            book_root = output_dir / DEFAULT_STUDY_SHELF / "junk_text"
            rows = [json.loads(line) for line in (book_root / "reader_stream.jsonl").read_text().splitlines() if line.strip()]
            cards = [json.loads(line) for line in (book_root / "definition_cards.jsonl").read_text().splitlines() if line.strip()]
            row_text = "\n".join(row["reader_text"] for row in rows)
            terms = [card["term"] for card in cards]

            self.assertNotIn("Z-Library", row_text)
            self.assertNotIn("Table of Contents", row_text)
            self.assertNotIn("Proof of Theorem 1.2", terms)
            self.assertNotIn("Assigning Probabilities", terms)
            self.assertIn("Random Variable", terms)

    def test_build_filters_reference_and_index_back_matter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_reference_back_matter_extract(source_root / "reference_back_matter")
            output_dir = Path(tmp) / "study_materials"

            build_study_materials(db, source_root=source_root, output_dir=output_dir)

            book_root = output_dir / DEFAULT_STUDY_SHELF / "reference_back_matter"
            rows = [json.loads(line) for line in (book_root / "reader_stream.jsonl").read_text().splitlines() if line.strip()]
            row_text = "\n".join(f"{row['title_path']}\n{row['reader_text']}" for row in rows)

            self.assertIn("Definition 1.1 Metric Space", row_text)
            self.assertNotIn("References and Name Index", row_text)
            self.assertNotIn("PURE AND APPLIED MATHEMATICS", row_text)
            self.assertNotIn("numbers in square brackets following each entry", row_text)

    def test_is_junk_section_catches_notation_index_and_series_catalog(self) -> None:
        self.assertTrue(
            is_junk_section(
                "NOTATION INDEX",
                "| Spaces AC(I) 132 | beta(T) 16 | Delta 505 |",
            )
        )
        self.assertTrue(
            is_junk_section(
                "PURE AND APPLIED MATHEMATICS",
                "A Wiley-Interscience Series of Texts, Monographs, and Tracts\nFounded by RICHARD COURANT",
            )
        )
        self.assertTrue(
            is_junk_section(
                "List of Frequently Used Notation and Symbols",
                "| vectors in R^n | x in R^n |\n| x = (x_1, ..., x_n) |",
            )
        )

    def test_looks_like_contents_table_catches_table_of_contents_layout(self) -> None:
        self.assertTrue(
            looks_like_contents_table(
                "| 2.2PrefaceIXStandardNotationsxii. |\n"
                "| SomeChapterUnderlyingGeometricNotions0.1 |\n"
                "| Chapter 2. Homology |\n"
                "| 2.1. Simplicial and Singular Homology 160 |"
            )
        )
        self.assertFalse(
            looks_like_contents_table(
                "| Bernoulli | 1 | x in {0,1} |\n"
                "| Binomial | N | x in {0,1,...,N} |"
            )
        )
        self.assertTrue(
            looks_like_contents_table(
                "| Chapter 2. Homology | |--------------------------------------------------------------| "
                "| 2.1. Simplicial and Singular Homology | | 2.2. Computations and Applications | "
                "| 2.3. The Formal Viewpoint |"
            )
        )
        self.assertFalse(
            looks_like_contents_table(
                "with $c' = c ||f||_{L^2(\\Omega)}$ and see Section 5.1, Chapter 4, "
                "together with Theorem 5.3 in Chapter 4"
            )
        )

    def test_discoflash_export_fails_for_selected_book_without_definition_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, source_root = build_study_catalog(tmp)
            create_topological_partial_extract(source_root)
            create_zero_card_like_extract(source_root / "zero_cards_like")
            output_dir = Path(tmp) / "study_materials"

            with self.assertRaisesRegex(ValueError, "no definition cards available"):
                export_study_materials(
                    db,
                    source_root=source_root,
                    output_dir=output_dir,
                    target=DISCOFLASH_TARGET,
                    book="zero_cards_like",
                )

    def test_build_definition_cards_filters_structural_terms_from_strict_cards(self) -> None:
        cards = build_definition_cards(
            "probability_measure",
            [],
            [
                {"concept_title": "Preface", "back": "Introductory matter."},
                {"concept_title": "Proof of Theorem 1.2", "back": "A proof."},
                {"concept_title": "Probability", "back": "A normalized measure."},
            ],
        )

        self.assertEqual([card["term"] for card in cards], ["Probability"])
        self.assertEqual(cards[0]["card_source_kind"], "strict_concept")

    def test_build_definition_cards_extracts_explicit_named_and_inline_entities(self) -> None:
        rows = [
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00001",
                "source_text": "A measure is a non-negative countably additive set function.",
                "reader_text": "A measure is a non-negative countably additive set function.",
                "title_path": "Measure Spaces > Definition 1.1 Measure",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00002",
                "source_text": "**Proposition 1.1.2** (Weyl equidistribution criterion). Let x be a sequence in the torus.",
                "reader_text": "**Proposition 1.1.2** (Weyl equidistribution criterion). Let x be a sequence in the torus.",
                "title_path": "Higher order Fourier > 1.1. Equidistribution of polynomial sequences in tori",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00003",
                "source_text": "In particular, we say that the sequence x is asymptotically equidistributed on N with respect to a measure mu if the averages converge.",
                "reader_text": "In particular, we say that the sequence x is asymptotically equidistributed on N with respect to a measure mu if the averages converge.",
                "title_path": "Higher order Fourier > 1.1. Equidistribution of polynomial sequences in tori",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(
            [(card["term"], card["card_source_kind"]) for card in cards],
            [
                ("Measure", "definition_heading"),
                ("Weyl equidistribution criterion", "named_proposition"),
                ("asymptotically equidistributed", "inline_definition"),
            ],
        )
        self.assertEqual(
            [card["term_resolution_kind"] for card in cards],
            ["original", "original", "original"],
        )

    def test_build_definition_cards_rejects_exercises_and_series_boilerplate(self) -> None:
        rows = [
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00001",
                "source_text": "We define a Fourier character to be a function on [N].",
                "reader_text": "We define a Fourier character to be a function on [N].",
                "title_path": "Higher order Fourier > **Exercise 1.2.2.** Let delta_* be as above.",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00002",
                "source_text": "However, there are also precursors to this theory in Weyl's classical theory of equidistribution.",
                "reader_text": "However, there are also precursors to this theory in Weyl's classical theory of equidistribution.",
                "title_path": "Series Front Matter > Selected Published Titles in This Series",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00003",
                "source_text": "**Theorem 1.2.1.** Let A be a subset of the integers.",
                "reader_text": "**Theorem 1.2.1.** Let A be a subset of the integers.",
                "title_path": "Roth > 1.2. Roth's theorem",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(cards, [])

    def test_build_definition_cards_rejects_discourse_sentence_false_positives(self) -> None:
        rows = [
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00001",
                "source_text": "If P is already totally 1/F(1)-equidistributed then we are done.",
                "reader_text": "If P is already totally 1/F(1)-equidistributed then we are done.",
                "title_path": "Higher order Fourier > 1.1. Equidistribution of polynomial sequences in tori",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00002",
                "source_text": "But in practice, one can take a simpler decomposition.",
                "reader_text": "But in practice, one can take a simpler decomposition.",
                "title_path": "Higher order Fourier > 1.1. Equidistribution of polynomial sequences in tori",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(cards, [])

    def test_build_definition_cards_rejects_term_sentence_false_positives(self) -> None:
        rows = [
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00001",
                "source_text": "The project is to construct simultaneously a model for coin tossing.",
                "reader_text": "The project is to construct simultaneously a model for coin tossing.",
                "title_path": "Chapter 1 > The Unit Interval",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00002",
                "source_text": "which is the average of the Dirac point masses on each point.",
                "reader_text": "which is the average of the Dirac point masses on each point.",
                "title_path": "Higher order Fourier > 1.1. Equidistribution",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00003",
                "source_text": "Theorem 1.2 is stronger than Theorem 1.1.",
                "reader_text": "Theorem 1.2 is stronger than Theorem 1.1.",
                "title_path": "Chapter 1 > Strong Law Versus Weak",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(cards, [])

    def test_build_definition_cards_leaves_solution_style_rows_without_explicit_terms_empty(self) -> None:
        rows = [
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "concept_tags": [],
                "row_id": "reader:math:test:00001",
                "source_text": "They are bigger so they cover A; the total length is less than epsilon.",
                "reader_text": "They are bigger so they cover A; the total length is less than epsilon.",
                "title_path": "Chapter 2 > Chapter 2",
            },
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "concept_tags": [],
                "row_id": "reader:math:test:00002",
                "source_text": "Write each element of C in ternary form.",
                "reader_text": "Write each element of C in ternary form.",
                "title_path": "Chapter 2 > Chapter 2",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(cards, [])

    def test_build_definition_cards_recovers_or_drops_noisy_labels_from_local_context(self) -> None:
        rows = [
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00001",
                "source_text": "**Theorem C.5** (Doob's martingale convergence theorem I. Let N_t be a right continuous supermartingale.",
                "reader_text": "**Theorem C.5** (Doob's martingale convergence theorem I. Let N_t be a right continuous supermartingale.",
                "title_path": "Appendix > C.5 (Doob's martingale convergence theorem I",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00002",
                "source_text": "**Theorem 5.1** (Carleson [1]). Suppose f is integrable.",
                "reader_text": "**Theorem 5.1** (Carleson [1]). Suppose f is integrable.",
                "title_path": "Fourier analysis > Theorem 5.1 (Carleson [1])",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00003",
                "source_text": "A common experimental setup is called permuted MNIST) or pMNIST in some papers.",
                "reader_text": "A common experimental setup is called permuted MNIST) or pMNIST in some papers.",
                "title_path": "Task-aware setting > Discussion",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00004",
                "source_text": "**Theorem 9.7** (first entry, Doob, Hunt) Let the set A be progressive.",
                "reader_text": "**Theorem 9.7** (first entry, Doob, Hunt) Let the set A be progressive.",
                "title_path": "Martingales > Optional times",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00005",
                "source_text": "**Theorem 1.1.** $B_t^2 - t$ is a martingale.",
                "reader_text": "**Theorem 1.1.** $B_t^2 - t$ is a martingale.",
                "title_path": "Martingales > Theorem 1.1",
            },
            {
                "chapter_id": "ch_01",
                "chapter_number": 1,
                "concept_tags": [],
                "row_id": "reader:math:test:00006",
                "source_text": "The unique linear map f* is called the adjoint of f.",
                "reader_text": "The unique linear map f* is called the adjoint of f.",
                "title_path": "Linear operators > 11.3. Adjoint of a Linear Map",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(
            [(card["term"], card["term_resolution_kind"]) for card in cards],
            [
                ("Doob's martingale convergence theorem I", "direct_cleanup"),
                ("Carleson", "direct_cleanup"),
                ("permuted MNIST", "direct_cleanup"),
                ("first entry", "direct_cleanup"),
                ("Adjoint of a Linear Map", "context_recovery"),
            ],
        )

    def test_named_result_quality_rejects_reference_and_prompt_noise(self) -> None:
        self.assertIsNone(normalize_named_card_term("[4], Theorem 2.1"))
        self.assertEqual(
            normalize_named_card_term("Naturality of Connecting Homomorphisms). Suppose"),
            "Naturality of Connecting Homomorphisms",
        )
        self.assertIsNone(normalize_named_card_term("Kennard, 2012 [68] and Amann and Kennard, 2014"))
        self.assertEqual(
            normalize_named_card_term("C.5 (Doob's martingale convergence theorem I"),
            "Doob's martingale convergence theorem I",
        )

    def test_definition_quality_rejects_section_and_case_noise_but_keeps_symbolic_term(self) -> None:
        self.assertFalse(is_display_quality_card_term("§6 Finite Sets", source_kind="definition_heading"))
        self.assertFalse(is_display_quality_card_term("Case 1", source_kind="definition_heading"))
        self.assertTrue(is_display_quality_card_term("$L^1$ regularization", source_kind="definition_heading"))

    def test_build_definition_cards_promotes_clean_local_headings_without_structural_noise(self) -> None:
        rows = [
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "chunk_kind": "exposition",
                "concept_tags": [],
                "row_id": "reader:math:test:01001",
                "source_text": "The field of values has many useful properties for matrix analysis.",
                "reader_text": "The field of values has many useful properties for matrix analysis.",
                "title_path": "Chapter 2 > 2 The field of values",
                "chapter_title": "Chapter 2",
            },
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "chunk_kind": "definition",
                "concept_tags": [],
                "row_id": "reader:math:test:01002",
                "source_text": "Pivoting is used to improve numerical stability in elimination.",
                "reader_text": "Pivoting is used to improve numerical stability in elimination.",
                "title_path": "Chapter 2 > Lecture 21. Pivoting",
                "chapter_title": "Chapter 2",
            },
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "chunk_kind": "theorem",
                "concept_tags": [],
                "row_id": "reader:math:test:01003",
                "source_text": "This theorem identifies distributions with tempered distributions on products.",
                "reader_text": "This theorem identifies distributions with tempered distributions on products.",
                "title_path": "Test Functions > Schwartz kernel theorem",
                "chapter_title": "Test Functions",
            },
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "chunk_kind": "definition",
                "concept_tags": [],
                "row_id": "reader:math:test:01004",
                "source_text": "Exercises for the chapter appear here.",
                "reader_text": "Exercises for the chapter appear here.",
                "title_path": "Chapter 2 > Exercises",
                "chapter_title": "Chapter 2",
            },
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "chunk_kind": "definition",
                "concept_tags": [],
                "row_id": "reader:math:test:01005",
                "source_text": "Program text and plotting code go here.",
                "reader_text": "Program text and plotting code go here.",
                "title_path": "Chapter 0 > Program 4 % p4.M periodic spectral differentiation",
                "chapter_title": "Chapter 0",
            },
            {
                "chapter_id": "ch_02",
                "chapter_number": 2,
                "chunk_kind": "theorem",
                "concept_tags": [],
                "row_id": "reader:math:test:01006",
                "source_text": "This has an important consequence for the next estimate.",
                "reader_text": "This has an important consequence for the next estimate.",
                "title_path": "Chapter 2 > has an important consequence",
                "chapter_title": "Chapter 2",
            },
        ]

        cards = build_definition_cards("test_book", rows, [])

        self.assertEqual(
            [(card["term"], card["card_source_kind"], card["term_resolution_kind"]) for card in cards],
            [
                ("field of values", "definition_heading", "promoted_heading"),
                ("Pivoting", "definition_heading", "promoted_heading"),
                ("Schwartz kernel theorem", "named_theorem", "promoted_heading"),
            ],
        )

    def test_build_definition_cards_rescues_grounded_terms_for_thin_books(self) -> None:
        rows = [
            {
                "chapter_id": "ch_05",
                "chapter_number": 5,
                "chunk_kind": "definition",
                "concept_tags": [],
                "row_id": "reader:math:test:02001",
                "source_text": "Suppose there is a choice function f : Lambda -> A.",
                "reader_text": "Suppose there is a choice function f : Lambda -> A.",
                "title_path": "Chapter 5 > The Axiom of Choice",
                "chapter_title": "Chapter 5",
            },
            {
                "chapter_id": "ch_05",
                "chapter_number": 5,
                "chunk_kind": "exposition",
                "concept_tags": [],
                "row_id": "reader:math:test:02002",
                "source_text": "First define the following equivalence relation on [0,1]: x ~ y.",
                "reader_text": "First define the following equivalence relation on [0,1]: x ~ y.",
                "title_path": "Chapter 5 > Remark",
                "chapter_title": "Chapter 5",
            },
            {
                "chapter_id": "ch_05",
                "chapter_number": 5,
                "chunk_kind": "exposition",
                "concept_tags": [],
                "row_id": "reader:math:test:02003",
                "source_text": "Let C denote the Cantor set, and define the Cantor function f as follows.",
                "reader_text": "Let C denote the Cantor set, and define the Cantor function f as follows.",
                "title_path": "Chapter 5 > Remark",
                "chapter_title": "Chapter 5",
            },
            {
                "chapter_id": "ch_05",
                "chapter_number": 5,
                "chunk_kind": "exposition",
                "concept_tags": [],
                "row_id": "reader:math:test:02004",
                "source_text": "There are Borel sets, Lebesgue-measurable sets, and non-measurable sets in this construction.",
                "reader_text": "There are Borel sets, Lebesgue-measurable sets, and non-measurable sets in this construction.",
                "title_path": "Chapter 5 > Existence of non measurable and non Borel sets",
                "chapter_title": "Chapter 5",
            },
        ]
        for index in range(5, 11):
            rows.append(
                {
                    "chapter_id": "ch_05",
                    "chapter_number": 5,
                    "chunk_kind": "exposition",
                    "concept_tags": [],
                    "row_id": f"reader:math:test:02{index:03d}",
                    "source_text": f"Background exposition {index}.",
                    "reader_text": f"Background exposition {index}.",
                    "title_path": "Chapter 5 > Remark",
                    "chapter_title": "Chapter 5",
                }
            )

        cards = build_definition_cards("test_book", rows, [])

        self.assertCountEqual(
            [card["term"] for card in cards],
            [
                "Axiom of Choice",
                "Existence of non measurable and non Borel sets",
                "choice function",
                "equivalence relation",
                "Cantor set",
                "Cantor function",
                "Borel set",
                "Lebesgue measurable set",
                "non measurable set",
            ],
        )
        self.assertTrue(all(card["term_resolution_kind"] in {"promoted_heading", "thin_deck_rescue"} for card in cards))

    def test_format_chapter_label_avoids_duplicate_prefix(self) -> None:
        self.assertEqual(
            format_chapter_label(chapter_number=1, chapter_title="Chapter 1"),
            "Chapter 1",
        )
        self.assertEqual(
            format_chapter_label(
                chapter_number=10,
                chapter_title="Chapter 10 , Might Be Postponed for a Laterstudy",
            ),
            "Chapter 10: Might Be Postponed for a Laterstudy",
        )
        self.assertEqual(
            format_chapter_label(
                chapter_number=7,
                chapter_title="7.1 Introduction",
            ),
            "Chapter 7: Introduction",
        )
        self.assertEqual(
            format_chapter_label(
                chapter_number=17,
                chapter_title="1 ( Bayesian neural networks",
            ),
            "Chapter 17: Bayesian neural networks",
        )
        self.assertEqual(
            format_chapter_label(
                chapter_number=27,
                chapter_title="2V4 Generative adversarial networks",
            ),
            "Chapter 27: Generative adversarial networks",
        )
        self.assertEqual(
            format_chapter_label(chapter_number=2, chapter_title="Measure Spaces"),
            "Chapter 2: Measure Spaces",
        )
        self.assertEqual(
            normalize_chapter_title("|Applicationsof Integration 121", chapter_number=2),
            "Applicationsof Integration",
        )
        self.assertEqual(
            normalize_chapter_title("373", chapter_number=9),
            "Chapter 9",
        )

    def test_cli_parses_study_commands(self) -> None:
        parser = build_parser()

        probe_args = parser.parse_args(["study", "probe-source-root", "--path", "/tmp/source", "--path", "/tmp/other"])
        inventory_args = parser.parse_args(["study", "inventory", "--book", "probability_measure"])
        build_args = parser.parse_args(["study", "build", "--book", "probability_measure"])
        show_args = parser.parse_args(["study", "show", "probability_measure"])
        export_args = parser.parse_args(["study", "export", "--book", "probability_measure"])

        self.assertEqual(len(probe_args.path), 2)
        self.assertEqual(inventory_args.shelf, DEFAULT_STUDY_SHELF)
        self.assertEqual(inventory_args.book, "probability_measure")
        self.assertEqual(inventory_args.source_root, DEFAULT_STUDY_SOURCE_ROOT)
        self.assertEqual(inventory_args.selection, DEFAULT_STUDY_SELECTION)
        self.assertEqual(build_args.output_dir, DEFAULT_STUDY_DIR)
        self.assertEqual(build_args.book, "probability_measure")
        self.assertEqual(build_args.source_root, DEFAULT_STUDY_SOURCE_ROOT)
        self.assertEqual(build_args.selection, DEFAULT_STUDY_SELECTION)
        self.assertEqual(show_args.view, DEFAULT_STUDY_VIEW)
        self.assertEqual(export_args.target, CANONICAL_TARGET)
        self.assertEqual(export_args.source_root, DEFAULT_STUDY_SOURCE_ROOT)


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
        "- [Probability and Measure](../sources/math/probability_measure.md)\n\n"
        "## Related Concepts\n\n"
        "- [Sigma Algebra](sigma_algebra.md)\n"
    )
    (root / "concepts" / "probability.md").write_text(
        "# Probability\n\n"
        "Probability is a measure normalized so that the whole sample space has total mass one.\n\n"
        "## Related Concepts\n\n"
        "- [Measure](measure.md)\n"
    )
    (root / "sources" / "math" / "README.md").write_text("# Math Source Notes\n\nCurated math shelf.\n")
    (root / "sources" / "math" / "probability_measure.md").write_text(
        "# Probability and Measure\n\n"
        "- corpus: `math`\n"
        "- document_id: `probability_measure`\n"
        "- output_root: `C:\\dev\\outputs\\math\\probability_measure`\n\n"
        "## Why This Source Matters\n\n"
        "This source ties measure theory to probability spaces and convergence.\n\n"
        "## Sigma Algebra\n\n"
        "Build the closure properties first.\n\n"
        "## Measure\n\n"
        "Measure construction follows the measurable-space setup.\n\n"
        "## Related Concepts\n\n"
        "- [Probability](../../concepts/probability.md)\n"
    )
    (root / "sources" / "math" / "topological_manifolds.md").write_text(
        "# Introduction to Topological Manifolds\n\n"
        "- corpus: `math`\n"
        "- document_id: `topological_manifolds`\n"
        "- output_root: `C:\\dev\\outputs\\math\\topological_manifolds`\n\n"
        "## Why This Source Matters\n\n"
        "This source covers topological prerequisites for later geometry notes.\n"
    )

    db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, db)

    source_root = Path(tmp) / "source_root"
    create_probability_measure_extract(source_root)
    return db, root, source_root


def create_probability_measure_extract(source_root: Path) -> None:
    create_probability_like_extract(source_root / "probability_measure", document_id="probability_measure")


def create_probability_like_extract(book_root: Path, *, document_id: str) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_02").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_02").mkdir(parents=True)

    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {
                "book_title": "Probability and Measure",
                "chapter_count": 2,
                "document_id": document_id,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Measure Spaces"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "manifests" / "ch_02.json").write_text(
        json.dumps({"chapter_number": 2, "chapter_title": "Probability"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Measure Spaces",
                "document_id": document_id,
                "sections": [
                    {
                        "content": "A sigma algebra is a collection of sets closed under complements and countable unions.",
                        "level": 2,
                        "title": "Definition 1.1 Sigma Algebra",
                    },
                    {
                        "content": "A measure is a non-negative countably additive set function on a sigma algebra.",
                        "level": 2,
                        "title": "Measure",
                    },
                    {
                        "content": (
                            "Probability starts with measurable spaces and countable additivity.\n\n"
                            "Longer expository passages should still stay ordered and become app-ready reader chunks."
                        ),
                        "level": 2,
                        "title": "Examples",
                    },
                ],
                "source_pdf": "probability_measure_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "chapter_json" / "ch_02" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_02",
                "chapter_title": "Probability",
                "document_id": document_id,
                "sections": [
                    {
                        "content": "Probability is a measure normalized so that the whole space has total mass one.",
                        "level": 2,
                        "title": "Definition 2.1 Probability",
                    },
                    {
                        "content": "Theorem statements should stay near the source wording even in the reader stream.",
                        "level": 2,
                        "title": "Theorem 2.2 Convergence",
                    },
                ],
                "source_pdf": "probability_measure_ch_02.pdf",
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
    (book_root / "normalized_markdown" / "ch_02" / "chapter.md").write_text(
        "## Probability\n\n"
        "### Definition 2.1 Probability\n\n"
        "Probability is a measure normalized so that the whole space has total mass one.\n"
    )


def create_topological_partial_extract(source_root: Path) -> None:
    book_root = source_root / "topological_manifolds"
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_02").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)

    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {
                "book_title": "Introduction to Topological Manifolds",
                "chapter_count": 2,
                "document_id": "topological_manifolds",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Covering Spaces"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "manifests" / "ch_02.json").write_text(
        json.dumps({"chapter_number": 2, "chapter_title": "Homotopy"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Covering Spaces",
                "document_id": "topological_manifolds",
                "sections": [
                    {
                        "content": "Theorem statements should stay near the source wording for covering-space arguments.",
                        "level": 2,
                        "title": "Theorem 1.1 Lifting Criterion",
                    },
                    {
                        "content": "Expository topology passages still belong in the reader stream even when they are not card material.",
                        "level": 2,
                        "title": "Examples",
                    },
                ],
                "source_pdf": "topological_manifolds_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "chapter_json" / "ch_02" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_02",
                "chapter_title": "Homotopy",
                "document_id": "topological_manifolds",
                "sections": [
                    {
                        "content": "Proof sketches without normalized markdown should force a partial build.",
                        "level": 2,
                        "title": "Proof 2.1",
                    }
                ],
                "source_pdf": "topological_manifolds_ch_02.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Covering Spaces\n\n"
        "### Theorem 1.1 Lifting Criterion\n\n"
        "Theorem statements should stay near the source wording for covering-space arguments.\n"
    )


def create_topological_full_extract(source_root: Path) -> None:
    create_topological_partial_extract(source_root)
    book_root = source_root / "topological_manifolds"
    (book_root / "normalized_markdown" / "ch_02").mkdir(parents=True, exist_ok=True)
    (book_root / "normalized_markdown" / "ch_02" / "chapter.md").write_text(
        "## Homotopy\n\n"
        "### Example 2.1\n\n"
        "Homotopy examples should still materialize as reader rows when normalized markdown is present.\n"
    )


def create_zero_card_like_extract(book_root: Path) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)

    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {
                "book_title": "Zero Cards Like",
                "chapter_count": 1,
                "document_id": "zero_cards_like",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Discussion"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Discussion",
                "document_id": "zero_cards_like",
                "sections": [
                    {
                        "content": "These notes discuss examples and proof sketches without defining a reusable study term.",
                        "level": 2,
                        "title": "Examples",
                    }
                ],
                "source_pdf": "zero_cards_like_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Discussion\n\n"
        "### Examples\n\n"
        "These notes discuss examples and proof sketches without defining a reusable study term.\n"
    )


def create_markdown_only_extract(book_root: Path, *, document_id: str) -> None:
    (book_root / "normalized_markdown" / "ch_10").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_24").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_10" / "chapter.md").write_text(
        "# 1 O Variational inference\n\n"
        "# 10.1 Introduction\n\n"
        "Variational inference reduces posterior inference to optimization.\n\n"
        "# 10.1.1 Variational free energy\n\n"
        "The variational free energy is the expected energy minus the entropy.\n",
        encoding="utf-8",
    )
    (book_root / "normalized_markdown" / "ch_24" / "chapter.md").write_text(
        "# 24 Normalizing Flows\n\n"
        "# 24.1 Introduction\n\n"
        "Normalizing flows are flexible density models.\n\n"
        "## 24.1.1 Preliminaries\n\n"
        "A base distribution is pushed forward through an invertible map.\n",
        encoding="utf-8",
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
                        "content": "Table of Contents\n\n1. Introduction\n2. Index",
                        "level": 1,
                        "title": "Table of Contents",
                    },
                    {
                        "content": "Z-Library download source. singlelogin access preserved here.",
                        "level": 1,
                        "title": "Footer",
                    },
                    {
                        "content": "A random variable is a measurable function on a probability space.",
                        "level": 2,
                        "title": "Definition 1.1 Random Variable",
                    },
                    {
                        "content": "Assigning probabilities is a way to model uncertainty in plain language.",
                        "level": 2,
                        "title": "Definition 1.2 Assigning Probabilities",
                    },
                    {
                        "content": "A proof heading should never become a definition card.",
                        "level": 2,
                        "title": "Definition 1.3 Proof of Theorem 1.2",
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


def create_reference_back_matter_extract(book_root: Path) -> None:
    (book_root / "manifests").mkdir(parents=True)
    (book_root / "chapter_json" / "ch_01").mkdir(parents=True)
    (book_root / "normalized_markdown" / "ch_01").mkdir(parents=True)
    (book_root / "manifests" / "book.json").write_text(
        json.dumps(
            {
                "book_title": "Reference Back Matter",
                "chapter_count": 1,
                "document_id": "reference_back_matter",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "manifests" / "ch_01.json").write_text(
        json.dumps({"chapter_number": 1, "chapter_title": "Metric Spaces"}, indent=2, sort_keys=True) + "\n"
    )
    (book_root / "chapter_json" / "ch_01" / "chapter.json").write_text(
        json.dumps(
            {
                "chapter_id": "ch_01",
                "chapter_title": "Metric Spaces",
                "document_id": "reference_back_matter",
                "sections": [
                    {
                        "content": "A metric space is a set with a distance function satisfying the triangle inequality.",
                        "level": 2,
                        "title": "Definition 1.1 Metric Space",
                    },
                    {
                        "content": (
                            "The numbers in square brackets following each entry give the pages of this book on which "
                            "reference to the entry is made.\n\nARSAC, J. Fourier Transforms and the Theory of "
                            "Distributions. [63, 78]"
                        ),
                        "level": 2,
                        "title": "References and Name Index",
                    },
                    {
                        "content": (
                            "PURE AND APPLIED MATHEMATICS\n\nLAX-Functional Analysis\nBOYARINTSEV-Methods of Solving "
                            "Singular Systems"
                        ),
                        "level": 2,
                        "title": "Appendix Listing",
                    },
                ],
                "source_pdf": "reference_back_matter_ch_01.pdf",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (book_root / "normalized_markdown" / "ch_01" / "chapter.md").write_text(
        "## Metric Spaces\n\n"
        "### Definition 1.1 Metric Space\n\n"
        "A metric space is a set with a distance function satisfying the triangle inequality.\n"
    )


if __name__ == "__main__":
    unittest.main()
