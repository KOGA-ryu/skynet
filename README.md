# Skynet Cleanroom

This repo is a review-gated cleanroom that turns messy source material into
traceable knowledge nodes through a dense operator shell.

The current codebase has two layers:

1. The Rust cleanroom core under `src/`, which owns staged packet processing,
   persistence, replay, and bounded cloud integration.
2. The legacy Python wiki tooling under `wiki_tool/`, which remains useful
   local infrastructure but is no longer the project identity.

Canonical project docs:

- [ROADMAP.md](ROADMAP.md)
- [ROADMAP_TASKLIST.md](ROADMAP_TASKLIST.md)
- [docs/DOCTRINE.md](docs/DOCTRINE.md)
- [docs/REVIEW_GATE_CONTRACT.md](docs/REVIEW_GATE_CONTRACT.md)
- [docs/UI_SHELL_CONTRACT.md](docs/UI_SHELL_CONTRACT.md)
- [docs/DECISIONS.md](docs/DECISIONS.md)
- [docs/RISK_REGISTER.md](docs/RISK_REGISTER.md)

Defaults:

- Mac wiki mount: `/Volumes/wiki`
- Windows wiki mount: `W:\`
- Local working mirror: `state/wiki_mirror`
- Local catalog: `state/catalog.sqlite`

Active build tracker: [ROADMAP_TASKLIST.md](ROADMAP_TASKLIST.md)

Setup guide: [SETUP.md](SETUP.md)

First usable build notes: [RELEASE_NOTES.md](RELEASE_NOTES.md)

Patch-bundle schema: [PATCH_BUNDLE_SCHEMA.md](PATCH_BUNDLE_SCHEMA.md)

Backup restore guide: [BACKUP_RESTORE.md](BACKUP_RESTORE.md)

Repo-demand intake manifest guide: [INTAKE_MANIFEST.md](INTAKE_MANIFEST.md)

Optional command shortcut after `python3 -m pip install -e .`:

```bash
wiki health --json
```

The long form `python3 -m wiki_tool ...` remains the no-install fallback.

Run the full local health checkpoint:

```bash
python3 -m wiki_tool health --json
```

Run a read-only scan:

```bash
python3 -m wiki_tool scan --wiki-root /Volumes/wiki
```

Create or refresh the local working mirror:

```bash
tools/sync_wiki_mirror.sh
python3 -m wiki_tool scan --wiki-root state/wiki_mirror --json
python3 -m wiki_tool scan-status --json
```

Search the derived catalog:

```bash
python3 -m wiki_tool find "scanner evidence"
python3 -m wiki_tool refs concepts/retrieval.md
python3 -m wiki_tool headings projects/stock_trading/README.md
python3 -m wiki_tool broken-links --limit 25
python3 -m wiki_tool scan-status --json
python3 -m wiki_tool scan-status --wiki-root /Volumes/wiki --limit 25 --json
python3 -m wiki_tool audit --write
python3 -m wiki_tool health --json
python3 -m wiki_tool explain "adapter boundary"
python3 -m wiki_tool open projects/stock_trading/apps/scanner.md --platform windows
python3 -m wiki_tool project-reports summary --json
python3 -m wiki_tool project-reports show stock_trading --limit 25 --json
python3 -m wiki_tool project-reports write --output-dir state/project_reports --limit 25 --json
python3 -m wiki_tool source-shelves summary --json
python3 -m wiki_tool source-shelves show math --json
python3 -m wiki_tool source-shelves show computer --json
python3 -m wiki_tool source-shelves write --output-dir state/source_shelf_reports --json
python3 -m wiki_tool source-shelves cleanup-bundle computer --output patch_bundles/source_shelves_computer_cleanup.json --json
python3 -m wiki_tool source-shelves bridge-bundle math --output patch_bundles/source_shelves_math_bridge_map.json --json
python3 -m wiki_tool source-shelves bridge-bundle computer --output patch_bundles/source_shelves_computer_project_bridge_map.json --json
python3 -m wiki_tool flashcards summary --profile both --json
python3 -m wiki_tool flashcards show probability_measure --profile expanded --json
python3 -m wiki_tool flashcards write --profile both --output-dir state/flashcards --json
python3 -m wiki_tool study probe-source-root --path /candidate/math_extracts --path /another/candidate --json
python3 -m wiki_tool study inventory --json
python3 -m wiki_tool study inventory --selection maintained_only --json
python3 -m wiki_tool study inventory --book probability_measure --json
python3 -m wiki_tool study build --output-dir state/study_materials --json
python3 -m wiki_tool study build --selection maintained_only --output-dir state/study_materials --json
python3 -m wiki_tool study build --book probability_measure --output-dir state/study_materials --json
python3 -m wiki_tool study show probability_measure --view reader --output-dir state/study_materials --json
python3 -m wiki_tool study show probability_measure --view cards --output-dir state/study_materials --json
python3 -m wiki_tool study export --book probability_measure --target canonical --output-dir state/study_materials --json
python3 -m wiki_tool study export --book probability_measure --target discoflash --output-dir state/study_materials --json
python3 -m wiki_tool study qa summary --json
python3 -m wiki_tool study qa show probability_measure --json
python3 -m wiki_tool study qa write --json
python3 -m wiki_tool study pages summary --json
python3 -m wiki_tool study pages show probability_measure --json
python3 -m wiki_tool study pages build --json
python3 -m wiki_tool page-quality summary --json
python3 -m wiki_tool page-quality thin --json
python3 -m wiki_tool page-quality missing-summaries --json
python3 -m wiki_tool page-quality stubs --json
python3 -m wiki_tool page-quality stub-fill-queue --limit 25 --json
python3 -m wiki_tool page-quality unclear-hubs --json
python3 -m wiki_tool page-quality write --output-dir state/page_quality --json
python3 -m wiki_tool intake validate --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --json
python3 -m wiki_tool intake write --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --output-dir state/intake --json
python3 -m wiki_tool intake bundle --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --wiki-root state/wiki_mirror --output patch_bundles/intake_demo_repo_demand.json --json
python3 -m wiki_tool devrefs audit --json
python3 -m wiki_tool devrefs bundle --output patch_bundles/devrefs_preview.json --json
python3 -m wiki_tool patch-bundle validate patch_bundles/devrefs_preview.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report patch_bundles/devrefs_preview.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle apply patch_bundles/devrefs_preview.json --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool patch-bundle apply patch_bundles/devrefs_preview.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool open dev://RD_UI/qml/Main.qml --platform mac --json
python3 -m wiki_tool missing-notes audit --json
python3 -m wiki_tool missing-notes bundle --output patch_bundles/missing_notes_preview.json --json
python3 -m wiki_tool file-links audit --json
python3 -m wiki_tool file-links bundle --output patch_bundles/file_links_preview.json --json
python3 -m wiki_tool aliases validate --json
python3 -m wiki_tool aliases list --json
python3 -m wiki_tool aliases list --catalog --json
python3 -m wiki_tool harness validate --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis deterministic --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis local --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis openai --llm-model gpt-5.4-mini --json
python3 -m wiki_tool harness runs --json
python3 -m wiki_tool harness show <run_id> --json
python3 -m wiki_tool eval run --json
python3 -m wiki_tool eval run --split holdout --synthesis local --json
python3 -m wiki_tool eval run --write-report --json
python3 -m wiki_tool eval export-training --output state/training_exports/training_examples.jsonl --json
python3 -m wiki_tool eval compare-profiles --json
python3 -m wiki_tool eval compare-profiles --write-report --json
python3 -m wiki_tool eval cleanup-targets --json
python3 -m wiki_tool eval cleanup-targets --write-report --json
python3 -m wiki_tool scheduled-audit run --json
python3 -m wiki_tool scheduled-audit run --write-report --json
python3 -m wiki_tool scheduled-audit run --require-eval --json
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":1,"method":"symbol.search","params":{"query":"adapter boundary"}}' --json
python3 -m wiki_tool api serve
```

Portable code references:

- Use `dev://<repo>/<path>` for links into local repos.
- The default Mac dev root is `/Users/kogaryu/dev`.
- Windows dev roots are intentionally unconfigured by default. To enable them,
  create local ignored config at `state/devrefs_config.json`:

```json
{
  "roots": {
    "mac": "/Users/kogaryu/dev",
    "windows": "D:\\dev"
  }
}
```

Alias maps:

- Source-controlled wiki aliases live in `alias_maps/wiki_aliases.json`.
- Aliases are read-layer only in v1. They improve scan-time resolution, search,
  `refs`, and `open`; they do not rewrite NAS Markdown.
- Keep aliases conservative. Do not add broad aliases when a shorthand could
  reasonably point at multiple pages.

Project reports:

- `project-reports` treats each direct child of `projects/` as a top-level
  project.
- Reports summarize hub presence, inbound links, high-link notes, and orphan
  notes.
- Librarian report fields rank project cleanup by generated stubs, reviewable
  orphans, weak summaries, thin notes, unclear hubs, templates, and generated
  state artifacts.
- Markdown report writes are local-only under ignored `state/project_reports/`;
  they do not create NAS pages.

Source shelf reports:

- `source-shelves` inventories the local math and computer source shelves from
  the derived catalog.
- Reports flag weak summaries, thin notes, placeholder artifacts, sources with
  no inbound routes, and sources without concept/project bridge links.
- Markdown report writes are local-only under ignored
  `state/source_shelf_reports/`; they do not edit NAS Markdown, move books, or
  create patch bundles.
- `source-shelves cleanup-bundle computer` writes a reviewable local patch
  bundle for computer-shelf cleanup. Apply it to `state/wiki_mirror` first;
  NAS promotion is a separate reviewed bundle pass.
- `source-shelves bridge-bundle math` writes a reviewable local patch bundle
  that creates `sources/math/book_to_concept_bridge_map.md` and refreshes the
  math shelf hub from the current catalog. Apply it to `state/wiki_mirror`
  first; NAS promotion is a separate reviewed bundle pass.
- `source-shelves bridge-bundle computer` writes a reviewable local patch
  bundle that creates `sources/computer/source_to_project_bridge_map.md` and
  refreshes the computer shelf hub from the current catalog. Apply it to
  `state/wiki_mirror` first; NAS promotion is a separate reviewed bundle pass.

Flashcard exports:

- `flashcards` derives local-only math flashcard chains from maintained
  `sources/math` book notes and concept pages in the catalog.
- `flashcards` supports `strict` and `expanded` profiles. `strict` preserves the
  conservative concept-page export; `expanded` adds grounded study-anchor cards
  from `Strongest Chapters` and thin-book question fallbacks.
- `flashcards write` emits profile-specific JSONL exports plus review artifacts
  under ignored `state/flashcards/`; it does not edit the NAS or
  `state/wiki_mirror`.
- Flashcard freshness is scoped to `sources/math` and `concepts/`, so unrelated
  mirror drift elsewhere does not block generation.
- Inferred concept associations stay deterministic and grounded; unresolved
  definitions are routed to the local review queue instead of being invented.

Study materials:

- NAS migration checklist for study data and local app state:
  [NAS_STUDY_MIGRATION_CHECKLIST.md](NAS_STUDY_MIGRATION_CHECKLIST.md)
- `study` builds front-to-back per-book artifacts from the local extract corpus
  under `state/local_corpus/ml-letsgo/outputs/math` and writes outputs under
  ignored `state/study_materials/math/`.
- Source notes under `sources/math` are optional enrichment, not a requirement.
  Books with matching notes keep `note_path` metadata and can merge in strict
  concept-card enrichment; books without notes still build from extract
  manifests alone.
- `study probe-source-root` is the read-only first step when the real extract
  root is unclear. It scores one or more candidate paths against the maintained
  math shelf and ranks them as `good_candidate`, `partial_candidate`, or
  `no_match`.
- The expected extract layout is one directory per `document_id` with:
  - `manifests/book.json`
  - `manifests/ch_*.json`
  - `chapter_json/ch_*/chapter.json` or legacy `ch_*/chapter.json`
  - `normalized_markdown/ch_*/chapter.md`
- `study probe-source-root` reports:
  - matched maintained books
  - missing maintained books
  - ready books with valid chapter layouts
  - partial books with at least one usable chapter and at least one skipped chapter
  - books with no valid chapters
  - unmatched extract roots that do not map to any maintained math `document_id`
- `study inventory`, `study build`, and `study export` default to the local
  corpus cache at `state/local_corpus/ml-letsgo/outputs/math`. Use
  `--source-root` only when you intentionally want to override that path.
- `study inventory` and `study build` default to
  `--selection all_structured`, which means every extract-like directory under
  the local corpus root is considered in-scope. Use
  `--selection maintained_only` when you want to restrict the run back to the
  curated `sources/math` shelf.
- `study inventory` reports four live-run states:
  - `built` is not used there
  - `ready` means the extract root exists and all discovered chapters are usable
  - `partial` means at least one chapter is usable and at least one chapter is missing required files
  - `missing_extract` means the maintained math note has no matching extract directory under `--source-root`
  - `no_valid_chapters` means the extract root exists but no discovered chapter has both chapter JSON and normalized markdown
- `study inventory` also reports:
  - `has_source_note` per book
  - `title_source` as `source_note`, `book_manifest`, or `directory_name`
  - `selection` for the run
  - `unmatched_extract_roots` when `--selection maintained_only` leaves valid
    extract directories outside the maintained note set
- `study build` writes:
  - `reader_stream.jsonl`
  - `reader_plain.txt`
  - `definition_cards.jsonl`
  - `manifest.json`
  - shelf `index.json`
- Built book manifests and the shelf index distinguish `built`, `partial`,
  `missing_extract`, and `no_valid_chapters`, and also record
  `has_source_note`, `title_source`, and the run `selection`.
- App-facing study titles are normalized in the derived study outputs only.
  Source-note titles are used only when they already look clean; otherwise the
  build prefers a cleaner manifest title and falls back to a humanized
  directory/document ID when needed.
- `study show --view reader` returns the ordered reader stream; `study show --view cards`
  returns the derived definition-card deck for a built or partial book.
- `study export --target canonical` returns the canonical artifact paths for the
  selected built/partial books.
- `study export --target discoflash` writes `discoflash_definition_matching.txt`
  using the existing `discoflash` definition-matching format. It fails for a
  selected book when there are zero derived definition cards.
  Structural terms like `Preface`, `Proof`, and `Chapter ...` are filtered from
  exported study-card decks.
- `study qa` audits the current built corpus without rebuilding it. It reports:
  - `incomplete_extract`
  - `missing_build_artifact`
  - `empty_reader`
  - `reader_junk`
  - `bad_title`
  - `zero_card_deck`
  - `thin_card_deck`
  - `structural_card_term`
- `study qa summary` returns the aggregate machine-readable audit with
  per-category counts, a ranked priority queue, and per-book readiness fields
  for reader vs flashcard ingestion. It also exposes the closeout gate and
  canonical status metadata:
  - `completion_bar`
  - `completion_status`
  - `remaining_severe_count`
  - `remaining_warning_count`
  - `summary_sha256`
  - `canonical_status_path`
  - `consumer_checks`
  - `report_statuses`
- `study qa show <book>` returns one book's QA packet with sampled row/card
  evidence, plus `reader_ready`, `flashcard_ready`, and blocked-reason fields.
- `state/study_quality/math/summary.json` is the canonical machine-readable
  status authority for the study corpus. Treat all other QA/review documents as
  derived snapshots.
- `study qa write` writes local review files under `state/study_quality/math/`:
  - `summary.json`
  - `README.md`
  - `final_review_packet.md`
  - per-book issue files under `books/` only when warnings or severe issues exist
- `study qa write` also archives stale contradictory deficiency artifacts such
  as `corpus_deficiency_list.md` and `deficiency_inventory.json` under
  `state/study_quality/math/archive/`.
- The canonical quality-done check is:
  - run `study qa summary --json`
  - require `completion_status=pass`
  - require `remaining_severe_count=0`
  - require `remaining_warning_count=0`
  - require `consumer_checks.vox_study_library.status=pass` when the real
    representative review set is available in the current shelf
- When the corpus is quality-done, `study qa write` leaves:
  - `state/study_quality/math/summary.json`
  - `state/study_quality/math/README.md`
  - `state/study_quality/math/final_review_packet.md`
  - no per-book warning files under `state/study_quality/math/books/`
- `study pages` turns the built study corpus into generated wiki pages under
  `state/wiki_mirror/projects/math_library/`.
- `study pages` writes:
  - `projects/math_library/README.md`
  - `projects/math_library/books/<document_id>/README.md`
  - `projects/math_library/books/<document_id>/chapters/<chapter_id>.md`
  - `projects/math_library/state/navigation_index.json`
  - `projects/study_dashboard/README.md`
  - `projects/study_dashboard/books/<document_id>.md`
  - `projects/study_dashboard/state/navigation_index.json`
- `study pages` is manifest-driven. It discovers built books from per-book
  `manifest.json` files and overlays the live study inventory for blocked or
  incomplete books, so it does not collapse when `state/study_materials/math/index.json`
  has been narrowed by a book-scoped export.
- Generated book pages use app-ready study titles, link back to source notes
  when they exist, expose reader/flashcard readiness, and include chapter
  links plus direct study-artifact links.
- Generated chapter pages group the chapter reader stream by `title_path` and
  include chapter-local key-definition lists from `definition_cards.jsonl`.
- The generated Study Dashboard is a cross-app coordination surface above the
  Math Library pages. It exposes stable study selection IDs in the shared form:
  - whole book: `<document_id>::__entire__`
  - chapter: `<document_id>::<chapter_id>`
- Those selection IDs are shared across the generated wiki, `vox`, and
  `discoflash`. The dashboard now also emits concrete local shell commands for
  fresh launch and explicit `--resume` launch against sibling app repos.
- `study pages build` also updates `state/wiki_mirror/index.md` and
  `state/wiki_mirror/sources/math/README.md` so the generated Math Library hub
  is discoverable from the main wiki navigation.
- First live-run sequence:
  - run `study probe-source-root --path <candidate1> --path <candidate2> --json`
  - choose the top-ranked `good_candidate` or best `partial_candidate`
  - run `study inventory --source-root <real_math_root> --json`
  - inspect `missing_books`, `partial_books`, and `unmatched_extract_roots`
  - run `study build --source-root <real_math_root> --json`
  - inspect `state/study_materials/math/index.json`
  - run `study show <book> --view reader --json`
  - run `study export --source-root <real_math_root> --book <book> --target discoflash --json`
  - run `study qa summary --json`
  - run `study qa write --json`
  - run `study pages summary --json`
  - run `study pages build --json`

Page quality reports:

- `page-quality` identifies thin notes, weak/missing summaries, and unclear hub
  pages for librarian review.
- `page-quality stubs` isolates generated stub pages that still need
  human-written content.
- `page-quality stub-fill-queue` ranks generated stubs for human fill work and
  `page-quality write` emits local packet files under
  `state/page_quality/stub_fill_packets/`.
- Markdown report writes are local-only under ignored `state/page_quality/`;
  they do not edit NAS Markdown.

Repo-demand intake:

- `intake validate` checks a reviewed repo-demand manifest and optional local
  repo evidence paths.
- `intake write` emits ignored local Markdown queues and a librarian packet
  under `state/intake/<intake_id>/`.
- `intake bundle` writes a reviewable patch bundle for a library operation
  packet and, when possible, an exact-block update to the library intake queue.
- Intake commands do not apply bundles and do not edit `state/wiki_mirror` or
  the NAS.

Design rules:

- Markdown on the NAS is canonical.
- `state/wiki_mirror/` is an ignored local read mirror for build work; refresh
  it from the NAS with `tools/sync_wiki_mirror.sh`.
- SQLite is a derived read layer.
- v1 indexes Markdown text and validates links to existing wiki files.
- `scan-status` and `audit` report stale catalogs by comparing the latest
  cataloged Markdown hashes and tracked file paths against a wiki root.
- Use `scan-status --wiki-root /Volumes/wiki` when a mirror-built catalog needs
  to be checked against the live NAS before a write-oriented workflow.
- Template placeholder links are excluded from actionable broken-link counts.
- Harness specs live in `harness_specs/` as Markdown with fenced YAML blocks.
- Harness runs are persisted separately in `state/harness.sqlite`.
- Harness synthesis is deterministic by default; OpenAI structured-output
  synthesis is opt-in with `--synthesis openai` and requires `OPENAI_API_KEY`.
- Local synthesis with `--synthesis local` is claim-plan-first rather than
  prose-first. The model returns `refusal`, `refusal_reason`, and atomic
  `claims` tied to retrieved `span_ids`; the harness renders the final answer
  and citations after validating that plan.
- The local path now guarantees deterministic citation rendering from retrieved
  spans. That does not mean answer usefulness is solved. Usefulness still
  depends on retrieval quality, claim-plan quality, and refusal thresholds such
  as the minimum unique citation requirement.
- Refusal-heavy local answers are an honest failure mode, not a hidden
  regression. The stricter contract is exposing thin retrieval and conservative
  model behavior instead of letting the model invent polished but unsupported
  prose.
- Claim text is still model-authored. The local path reduces hallucination
  risk; it does not abolish it. Unsupported connective tissue can still appear
  if claims are not truly atomic or the selected spans are weak.
- Harness failures are mapped through the failure taxonomy, with safe retries
  and deferred remediation actions recorded in run traces.
- Empty primary retrieval automatically applies a bounded lexical fallback
  before synthesis.
- The local repair loop is capped at one controlled retry for invalid claim
  plans or bad span references. First-pass validity and repaired validity are
  separate run metrics and should never be blended in reporting.
- `eval compare-profiles` compares eval-only retrieval profiles against the
  current span FTS baseline before any production search behavior changes.
- `eval run` can be scoped by `--split` and exercised through
  `--synthesis local` to measure contract integrity, refusal behavior, and
  retrieval coverage separately.
- `eval export-training` is for non-eval harness traces only. Training export
  must not contaminate dev or holdout eval cases.
- `eval cleanup-targets` turns eval retrieval misses and low-ranked expected
  paths into local editorial cleanup queues with page-quality signals.
- `scheduled-audit run` is the scheduler-friendly checkpoint for audit,
  harness spec validation, eval regression, and cleanup-target generation.
  Eval is advisory by default while the quality queue is being worked; use
  `--require-eval` for strict CI-style gating. Reports are local ignored
  Markdown under `state/scheduled_audits/`; no OS scheduler is installed by
  this repo command.
- The JSON-RPC API is local-first and returns bounded handles, snippets,
  references, and summaries instead of whole Markdown documents.
- JSON-RPC API traces are local ignored state under `state/api_traces.jsonl`.
- Alias maps are source-controlled read-layer metadata.
- Project reports are local read-layer outputs unless explicitly promoted
  through a later review workflow.
- Source shelf reports are local staging-library outputs for organizing math
  and computer-science books before NAS promotion.
- Page quality reports are deterministic local queues for editorial review.
- Patch bundle target schema and rollback safety rules are documented in
  [PATCH_BUNDLE_SCHEMA.md](PATCH_BUNDLE_SCHEMA.md).
- Patch bundles can replace exact Markdown blocks and delete reviewed Markdown
  files when guarded by validation, backups, manifests, and rollback.
- Patch-bundle restore operations are documented in
  [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
- Real patch-bundle applies require the bundle/catalog scan root to match
  `--wiki-root`; mirror-backed catalogs are for read/build work until rescanned
  against the intended write root.
- Applied patch manifests can be reported and rolled back. Rollback verifies
  current file hashes before restoring backups or deleting generated stubs.
- No mass frontmatter rollout.
- No embeddings-first retrieval.
- Multi-file edits should go through local patch bundles and backups before any
  NAS write.
