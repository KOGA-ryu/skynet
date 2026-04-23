from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import scan_wiki
from wiki_tool.cli import build_parser
from wiki_tool.flashcards import (
    BOTH_PROFILES,
    EXPANDED_PROFILE,
    STRICT_PROFILE,
    clean_chapter_topic,
    flashcard_chain,
    flashcard_freshness,
    flashcard_summary,
    question_topic,
    write_flashcard_exports,
)


class FlashcardTests(unittest.TestCase):
    def test_flashcard_summary_reports_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            summary = flashcard_summary(db)

            self.assertEqual(summary["profile"], BOTH_PROFILES)
            self.assertEqual(summary["profiles"][STRICT_PROFILE]["book_count"], 3)
            self.assertEqual(summary["profiles"][STRICT_PROFILE]["exported_card_count"], 7)
            self.assertEqual(summary["profiles"][STRICT_PROFILE]["review_item_count"], 3)
            self.assertEqual(summary["profiles"][EXPANDED_PROFILE]["exported_card_count"], 14)
            self.assertEqual(summary["profiles"][EXPANDED_PROFILE]["review_item_count"], 0)
            self.assertEqual(summary["flashcard_freshness"]["status"], "pass")

    def test_strict_flashcard_chain_keeps_prerequisite_order_and_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            chain = flashcard_chain(db, "probability_measure", profile=STRICT_PROFILE)
            cards = chain["cards"]

            self.assertEqual(chain["profile"], STRICT_PROFILE)
            self.assertEqual([card["concept_title"] for card in cards], ["Sigma Algebra", "Measure", "Probability", "Filtration"])
            self.assertTrue(all(card["card_kind"] == "concept" for card in cards))
            self.assertTrue(all(card["profile"] == STRICT_PROFILE for card in cards))
            self.assertEqual(cards[0]["association_reason"], "heading_match")
            self.assertEqual(cards[1]["definition_source"], "concept_section")
            self.assertEqual(cards[2]["association_reason"], "explicit_link")
            self.assertEqual(cards[1]["prereq_card_ids"], ["flashcard:math:probability_measure:sigmaalgebra"])
            self.assertEqual(cards[2]["prereq_card_ids"], ["flashcard:math:probability_measure:measure"])
            self.assertNotIn("Retrieval", [card["concept_title"] for card in cards])

    def test_expanded_chain_adds_chapter_study_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            chain = flashcard_chain(db, "probability_measure", profile=EXPANDED_PROFILE)
            cards = chain["cards"]
            anchors = [card for card in cards if card["card_kind"] == "study_anchor"]

            self.assertEqual(chain["profile"], EXPANDED_PROFILE)
            self.assertEqual(chain["card_count"], 6)
            self.assertEqual([card["card_kind"] for card in cards[:4]], ["concept", "concept", "concept", "concept"])
            self.assertEqual(len(anchors), 2)
            self.assertEqual(
                [card["concept_title"] for card in anchors],
                ["Measure construction and extension theorems", "Probability spaces and convergence"],
            )
            self.assertTrue(all(card["association_reason"] == "chapter_topic" for card in anchors))
            self.assertTrue(all(card["profile"] == EXPANDED_PROFILE for card in anchors))
            self.assertTrue(all(card["concept_path"] is None for card in anchors))
            self.assertEqual(anchors[0]["front"], "Define Measure construction and extension theorems.")

    def test_expanded_chain_uses_question_fallback_for_thin_books(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            chain = flashcard_chain(db, "topological_manifolds", profile=EXPANDED_PROFILE)
            cards = chain["cards"]
            anchors = [card for card in cards if card["card_kind"] == "study_anchor"]

            self.assertEqual(chain["card_count"], 4)
            self.assertEqual([card["association_reason"] for card in anchors], ["chapter_topic", "question_topic", "question_topic"])
            self.assertEqual(
                [card["concept_title"] for card in anchors],
                [
                    "The Universal Covering Space",
                    "covering spaces, universal coverings, or quotient-space",
                    "topological-manifold prerequisites",
                ],
            )

    def test_expanded_chain_rejects_malformed_chapters_and_uses_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            chain = flashcard_chain(db, "stochastic_differential_equations", profile=EXPANDED_PROFILE)
            anchors = [card for card in chain["cards"] if card["card_kind"] == "study_anchor"]

            self.assertEqual(chain["card_count"], 4)
            self.assertEqual([card["association_reason"] for card in anchors], ["question_topic", "question_topic"])
            self.assertEqual(
                [card["concept_title"] for card in anchors],
                [
                    "Ito calculus, stochastic integration, or diffusion",
                    "stochastic differential equations",
                ],
            )

    def test_live_like_chapter_cleanup_normalizes_or_drops_bad_fragments(self) -> None:
        self.assertEqual(
            clean_chapter_topic("Chapter 1: ° Introduction; 1.1. PARTIAL DIFFERENTIAL EQUATIONS; DEFINITIONS."),
            "Partial Differential Equations",
        )
        self.assertIsNone(
            clean_chapter_topic("Chapter 2: chapter 2 is a detailedstudy of four exactly solvablepartialdifferential; 1.5. PROBLEMS")
        )
        self.assertEqual(
            clean_chapter_topic("Chapter 3: CHAPTER <sup>3</sup>; Stochastic Integration; 3.1. Introduction"),
            "Stochastic Integration",
        )
        self.assertEqual(
            clean_chapter_topic("Chapter 1: Riemannian Manifolds; Chapter 1 Riemannian Manifolds"),
            "Riemannian Manifolds",
        )
        self.assertEqual(
            clean_chapter_topic("Chapter 1: chapter 1 Mathematics Review; Mathematics Review"),
            "Mathematics Review",
        )

    def test_live_like_question_cleanup_trims_project_language_tail(self) -> None:
        self.assertEqual(
            question_topic("How should we connect modeling, equations, and matrix reasoning in a single project-facing explanation?"),
            "modeling, equations, and matrix reasoning",
        )

    def test_flashcard_chain_routes_missing_definitions_to_strict_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            chain = flashcard_chain(db, "Probability and Measure", profile=STRICT_PROFILE)
            reasons = {(item["concept_title"], item["reason"], item["profile"]) for item in chain["review_items"]}

            self.assertIn(("Filtration", "medium_confidence", STRICT_PROFILE), reasons)
            self.assertIn(("Sigma Algebra", "medium_confidence", STRICT_PROFILE), reasons)
            self.assertIn(("Measure", "medium_confidence", STRICT_PROFILE), reasons)
            self.assertNotIn(("Retrieval", "medium_confidence", STRICT_PROFILE), reasons)

    def test_write_flashcard_exports_writes_both_profiles_and_review_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)
            output_dir = Path(tmp) / "flashcards"

            result = write_flashcard_exports(db, output_dir=output_dir)

            strict_export = output_dir / "math_flashcards.jsonl"
            expanded_export = output_dir / "math_flashcards_expanded.jsonl"
            review_path = output_dir / "review_queue.md"
            summary_path = output_dir / "README.md"
            self.assertEqual(result["profile"], BOTH_PROFILES)
            self.assertEqual(result["file_count"], 4)
            self.assertTrue(strict_export.exists())
            self.assertTrue(expanded_export.exists())
            self.assertTrue(review_path.exists())
            self.assertTrue(summary_path.exists())

            strict_records = [json.loads(line) for line in strict_export.read_text().splitlines() if line.strip()]
            expanded_records = [json.loads(line) for line in expanded_export.read_text().splitlines() if line.strip()]
            self.assertEqual(len(strict_records), 7)
            self.assertEqual(len(expanded_records), 14)
            self.assertEqual(strict_records[0]["profile"], STRICT_PROFILE)
            self.assertEqual(expanded_records[-1]["profile"], EXPANDED_PROFILE)
            self.assertIn("## Strict Profile", review_path.read_text())
            self.assertIn("## Expanded Profile", review_path.read_text())
            self.assertIn("## Strict Profile", summary_path.read_text())
            self.assertIn("## Expanded Profile", summary_path.read_text())
            self.assertEqual(result["flashcard_freshness"]["status"], "pass")

    def test_expanded_chain_fronts_stay_short_in_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            for book in ("probability_measure", "topological_manifolds", "stochastic_differential_equations"):
                chain = flashcard_chain(db, book, profile=EXPANDED_PROFILE)
                for card in chain["cards"]:
                    if card["card_kind"] != "study_anchor":
                        continue
                    self.assertLessEqual(len(card["front"]), 72)
                    self.assertNotRegex(card["concept_title"], r"[<>;]")
                    self.assertFalse(card["concept_title"].lower().startswith("is "))

    def test_flashcard_chain_does_not_infer_plain_body_mentions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, _root = build_flashcard_catalog(tmp)

            chain = flashcard_chain(db, "probability_measure", profile=STRICT_PROFILE)

            self.assertNotIn("Retrieval", [card["concept_title"] for card in chain["cards"]])
            self.assertTrue(all(item["association_reason"] != "mention_match" for item in chain["review_items"]))

    def test_flashcard_chain_keeps_explicit_cross_domain_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp)
            source = root / "sources" / "math" / "probability_measure.md"
            source.write_text(source.read_text() + "- [Retrieval](../../concepts/retrieval.md)\n")
            scan_wiki(root, db)

            chain = flashcard_chain(db, "probability_measure", profile=STRICT_PROFILE)
            retrieval = next(card for card in chain["cards"] if card["concept_title"] == "Retrieval")

            self.assertEqual(retrieval["association_reason"], "explicit_link")
            self.assertEqual(retrieval["association_confidence"], "high")

    def test_flashcard_summary_ignores_unrelated_project_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp, include_project=True)
            (root / "projects" / "demo.md").write_text("# Demo\n\nUpdated unrelated project note.\n")

            summary = flashcard_summary(db, profile=STRICT_PROFILE)

            self.assertEqual(summary["flashcard_freshness"]["status"], "pass")
            self.assertEqual(summary["exported_card_count"], 7)

    def test_flashcard_summary_fails_when_math_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp)
            (root / "sources" / "math" / "probability_measure.md").write_text(
                (root / "sources" / "math" / "probability_measure.md").read_text() + "\nNew scoped change.\n"
            )

            with self.assertRaisesRegex(ValueError, "sources/math/probability_measure.md"):
                flashcard_summary(db, profile=STRICT_PROFILE)

    def test_flashcard_summary_fails_when_concept_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp)
            (root / "concepts" / "measure.md").write_text(
                (root / "concepts" / "measure.md").read_text() + "\nExtra concept change.\n"
            )

            with self.assertRaisesRegex(ValueError, "concepts/measure.md"):
                flashcard_summary(db, profile=STRICT_PROFILE)

    def test_flashcard_summary_fails_when_scoped_document_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp)
            (root / "concepts" / "martingale.md").write_text("# Martingale\n\nA martingale preserves conditional expectation.\n")

            with self.assertRaisesRegex(ValueError, "concepts/martingale.md"):
                flashcard_summary(db, profile=STRICT_PROFILE)

    def test_flashcard_summary_fails_when_scoped_document_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp)
            (root / "sources" / "math" / "probability_measure.md").unlink()

            with self.assertRaisesRegex(ValueError, "sources/math/probability_measure.md"):
                flashcard_summary(db, profile=STRICT_PROFILE)

    def test_flashcard_summary_ignores_generated_math_hubs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root = build_flashcard_catalog(tmp)
            (root / "sources" / "math" / "README.md").write_text("# Math Source Notes\n\nGenerated hub changed.\n")
            (root / "sources" / "math" / "book_to_concept_bridge_map.md").write_text(
                "# Math Book-to-Concept Bridge Map\n\n- Generated route.\n"
            )

            freshness = flashcard_freshness(db)
            summary = flashcard_summary(db, profile=STRICT_PROFILE)

            self.assertEqual(freshness["status"], "pass")
            self.assertEqual(summary["flashcard_freshness"]["status"], "pass")

    def test_flashcard_summary_fails_without_scan_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing.sqlite"

            with self.assertRaisesRegex(ValueError, "no scan run found"):
                flashcard_summary(db, profile=STRICT_PROFILE)

    def test_cli_exposes_flashcard_profile_defaults(self) -> None:
        parser = build_parser()

        summary_args = parser.parse_args(["flashcards", "summary"])
        show_args = parser.parse_args(["flashcards", "show", "probability_measure"])
        write_args = parser.parse_args(["flashcards", "write"])

        self.assertEqual(summary_args.profile, BOTH_PROFILES)
        self.assertEqual(show_args.profile, EXPANDED_PROFILE)
        self.assertEqual(write_args.profile, BOTH_PROFILES)


def build_flashcard_catalog(tmp: str, *, include_project: bool = False) -> tuple[Path, Path]:
    root = Path(tmp) / "wiki"
    (root / "concepts").mkdir(parents=True)
    (root / "sources" / "math").mkdir(parents=True)
    if include_project:
        (root / "projects").mkdir(parents=True)
        (root / "projects" / "demo.md").write_text("# Demo\n\nOriginal unrelated project note.\n")

    (root / "concepts" / "sigma_algebra.md").write_text(
        "# Sigma Algebra\n\n"
        "A sigma algebra is a collection of sets closed under complements and countable unions, "
        "which gives measure theory a stable domain for assigning size.\n\n"
        "## Relevant Sources\n\n"
        "- [Probability and Measure](../sources/math/probability_measure.md)\n"
    )
    (root / "concepts" / "measure.md").write_text(
        "# Measure\n\n"
        "## Definition\n\n"
        "A measure assigns a non-negative size to sets in a sigma algebra and is countably additive "
        "across disjoint collections, making probability spaces rigorous.\n\n"
        "## Relevant Sources\n\n"
        "- [Probability and Measure](../sources/math/probability_measure.md)\n\n"
        "## Related Concepts\n\n"
        "- [Sigma Algebra](sigma_algebra.md)\n"
    )
    (root / "concepts" / "probability.md").write_text(
        "# Probability\n\n"
        "Probability is a measure normalized so that the whole sample space has total mass one, "
        "letting random events be compared consistently.\n\n"
        "- [Measure](measure.md)\n"
    )
    (root / "concepts" / "retrieval.md").write_text(
        "# Retrieval\n\n"
        "Retrieval finds relevant source material before an answer is written, ranked, and prepared "
        "for grounded synthesis across the wider build system.\n"
    )
    (root / "concepts" / "filtration.md").write_text(
        "# Filtration\n\n"
        "Thin pointer note.\n\n"
        "## Relevant Sources\n\n"
        "- [Probability and Measure](../sources/math/probability_measure.md)\n\n"
        "## Related Concepts\n\n"
        "- [Probability](probability.md)\n"
    )
    (root / "concepts" / "manifolds_geometry.md").write_text(
        "# Manifolds Geometry\n\n"
        "Manifolds geometry is the layer for topological, smooth, and Riemannian structure when geometry "
        "notes need a clean escalation path across the shelf.\n\n"
        "## Relevant Sources\n\n"
        "- [Introduction to Topological Manifolds](../sources/math/topological_manifolds.md)\n"
    )
    (root / "concepts" / "stochastic_processes.md").write_text(
        "# Stochastic Processes\n\n"
        "Stochastic processes track random evolution over time, including filtrations, Brownian motion, "
        "and the structures used before stochastic calculus.\n\n"
        "## Relevant Sources\n\n"
        "- [Stochastic Differential Equations](../sources/math/stochastic_differential_equations.md)\n"
    )
    (root / "sources" / "math" / "README.md").write_text(
        "# Math Source Notes\n\n"
        "Curated math shelf.\n"
    )
    (root / "sources" / "math" / "probability_measure.md").write_text(
        "# Probability and Measure\n\n"
        "- corpus: `math`\n"
        "- document_id: `probability_measure`\n"
        "- output_root: `C:\\dev\\outputs\\math\\probability_measure`\n\n"
        "## Why This Source Matters\n\n"
        "This source ties measure theory to probability spaces, emphasizes measure constructions, "
        "later introduces filtration for stochastic reasoning, and mentions retrieval workflows "
        "without making them a math concept route.\n\n"
        "## Sigma Algebra\n\n"
        "Build the closure properties first.\n\n"
        "## Measure\n\n"
        "Measure construction should follow once the sigma algebra setup is clear.\n\n"
        "## Filtration\n\n"
        "Filtration appears later when process-level probability becomes important.\n\n"
        "## Strongest Chapters\n\n"
        "- Measure construction and extension theorems.\n"
        "- Probability spaces and convergence.\n\n"
        "## Example Questions\n\n"
        "- Which source should we use for sigma-algebras and measure construction?\n"
        "- How should we explain random variables before convergence results matter?\n"
        "- Where should we look when a note needs filtration or process-level probability?\n\n"
        "## Related Concepts\n\n"
        "- [Probability](../../concepts/probability.md)\n"
    )
    (root / "sources" / "math" / "topological_manifolds.md").write_text(
        "# Introduction to Topological Manifolds\n\n"
        "- corpus: `math`\n"
        "- document_id: `topological_manifolds`\n"
        "- output_root: `C:\\dev\\outputs\\math\\topological_manifolds`\n\n"
        "## Why This Source Matters\n\n"
        "This source extends the geometry shelf backward from smooth and Riemannian structure to the "
        "topological prerequisites that geometry notes often need first.\n\n"
        "## Strongest Chapters\n\n"
        "- Chapter 11: Classification of Coverings; Covering Homomorphisms; The Universal Covering Space\n\n"
        "## Example Questions\n\n"
        "- Which source should we use for covering spaces, universal coverings, or quotient-space arguments before switching to the smooth-manifold shelf?\n"
        "- How should we explain topological-manifold prerequisites before using smooth or Riemannian language?\n"
        "- Where should we look when a geometry note depends on compactness, connectedness, or covering constructions rather than differential structure?\n\n"
        "## Related Concepts\n\n"
        "- [Manifolds Geometry](../../concepts/manifolds_geometry.md)\n"
    )
    (root / "sources" / "math" / "stochastic_differential_equations.md").write_text(
        "# Stochastic Differential Equations\n\n"
        "- corpus: `math`\n"
        "- document_id: `stochastic_differential_equations`\n"
        "- output_root: `C:\\dev\\outputs\\math\\stochastic_differential_equations`\n\n"
        "## Why This Source Matters\n\n"
        "This is the dedicated SDE source in the live shelf for Ito calculus, stochastic integration, "
        "and diffusion-style arguments once general probability notes are no longer specific enough.\n\n"
        "## Strongest Chapters\n\n"
        "- Chapter 10: chapter 10 , and hence Section; Preface to the Fourth Edition\n"
        "- Chapter 12: chapter 12 , on applications to mathematical finance. I found it natural to; Preface to the Fifth Edition\n\n"
        "## Example Questions\n\n"
        "- Which source should we use for Ito calculus, stochastic integration, or diffusion language?\n"
        "- How should we escalate from general probability notes into stochastic differential equations?\n"
        "- Where should we look when a project question needs SDE intuition rather than only Brownian-motion foundations?\n\n"
        "## Related Concepts\n\n"
        "- [Probability](../../concepts/probability.md)\n"
        "- [Stochastic Processes](../../concepts/stochastic_processes.md)\n"
    )

    db = Path(tmp) / "catalog.sqlite"
    scan_wiki(root, db)
    return db, root


if __name__ == "__main__":
    unittest.main()
