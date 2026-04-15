# Wiki Build Tasklist

Last updated: 2026-04-15

This is the working tasklist for the private wiki build. Keep this file current
as tasks move from planned work into implemented, verified tooling.

## Current Snapshot

- [x] NAS wiki remains the canonical Markdown source.
- [x] Local tooling repo is the control plane for scans, audits, patch bundles,
  harness specs, and future query services.
- [x] Latest audit status: pass.
- [x] Broken actionable links: 0.
- [x] Excluded template placeholder links: 34.
- [x] Catalog size: 846 documents, 4,952 links, 6,776 spans, 7,600 symbols.
- [x] Latest scan run: `scan:20260415T131916Z:f4351838b68707a7`.
- [x] Latest harness run: `run:20260415T105602Z:4be3b4fc6f51c811`.
- [x] Latest harness task: `wiki.answer_with_citations`.
- [x] Latest harness status: pass.
- [x] Latest source checkpoint: `65ce62c` (`Add repo demand intake workflow`).
- [ ] Current active task: add a promote process for rough notes becoming
  canonical pages.
- [x] Generated stub report status: complete; `page-quality stubs` found 79
  generated stubs with 247 inbound references, and
  `page-quality stub-fill-queue` now ranks them for promotion work with
  P0/P1/P2 counts of 31/38/10.
- [x] Stub-fill packet status: complete; `page-quality write` now emits
  `generated_stubs.md`, `stub_fill_queue.md`, and 79 local evidence packets
  under `state/page_quality/stub_fill_packets/`.
- [x] Repo-demand intake status: complete; `intake validate`, `intake write`,
  and `intake bundle` now stage reviewed repo findings locally, with the demo
  manifest producing 3 findings, 5 local artifacts, and a valid 2-target patch
  bundle without applying it.
- [x] Urgent repo-demand intake test status: complete; Rudedude Pack 2 signal
  quality review generated 11 routed P0 findings, 5 local intake artifacts,
  and a valid 2-target patch bundle from
  `docs/60_reviews/active/review_signal_quality_pack_2_manifest.md` without
  applying it.
- [x] First usable build release notes status: complete; see
  `RELEASE_NOTES.md`.
- [x] JSON-RPC harness API status: complete; `harness.run` and `harness.show`
  return bounded answer and trace summaries.
- [x] Backup restore docs status: complete; operator restore workflow and
  rollback blocker meanings are documented in `BACKUP_RESTORE.md`.
- [x] Broken-link regression status: complete; eval runs now fail when the
  catalog has actionable broken links and continue to exclude template
  placeholders.
- [x] Trace diffing status: complete; `harness diff` compares persisted run
  traces without generating new harness runs.
- [x] Contract/schema validation status: complete; harness specs and synthesis
  outputs now fail closed against stricter declared schemas.
- [x] Root preflight implementation status: complete; real applies now require
  bundle/catalog roots to match `--wiki-root`.
- [x] Local mirror status: complete, with `state/wiki_mirror/` refreshed from
  the NAS and heavy stock-trading payloads excluded.
- [x] Stale-scan detection status: complete; `scan-status` and `audit` report
  freshness against the catalog root or an override root, with live mirror
  freshness passing across 845 documents and 1,314 tracked files.
- [x] Project-level librarian reports status: complete; live reports summarize
  7 top-level projects, 79 project generated stubs, 331 reviewable orphans,
  658 weak summaries, 223 thin notes, and 51 unclear hubs.
- [x] Alias map implementation status: complete, with 6 validated source
  aliases.
- [x] Retrieval profile comparison status: complete; `catalog.fts_spans.expanded`
  improved the eval set without per-query regressions versus
  `catalog.fts_spans.primary` (+0.1795 hit rate, +0.1667 expected-path recall,
  +0.1475 MRR), while hybrid span/document retrieval had smaller no-regression
  gains and document-only retrieval regressed on 8 queries.
- [x] Eval cleanup targets status: complete; current local catalog produces 34
  eval-driven cleanup targets: 14 P0, 18 P1, and 2 P2, with actions split
  across 24 opening summaries, 4 hub-navigation fixes, and 6 search-term or
  bridge-link improvements.
- [x] Scheduled audit runner status: complete; latest local run passed with
  audit and harness required, eval advisory at 35/39 query pass rate, and 20
  emitted cleanup targets from 34 candidates.
- [x] Package entry point status: complete; editable installs now expose the
  `wiki` console command while `python3 -m wiki_tool ...` remains supported.
- [x] Source shelf cleanup status: complete in the local mirror; local catalog
  reports 43 math and computer source notes across 2 shelves, with math at 23
  maintained notes and computer at 20 maintained notes, and zero source-shelf
  weak summaries, thin notes, generated stubs, placeholder artifacts, missing
  inbound routes, or missing concept/project bridges.
- [x] Local cleanup bundle status: applied to `state/wiki_mirror` only via
  `patch_bundles/source_shelves_computer_cleanup.json`, with rollback manifest
  under `backups/bundle_source-shelves_computer-cleanup_20260415T124251Z/`.
- [x] Math book-to-concept bridge status: complete in the local mirror;
  `sources/math/book_to_concept_bridge_map.md` now routes 23 maintained math
  source notes across 9 concept routes, and `sources/math/README.md` now points
  to the generated bridge map. Latest local rollback manifest:
  `backups/bundle_source-shelves_math-bridge-map_20260415T125921Z/`.
- [x] Computer source-to-project bridge status: complete in the local mirror;
  `sources/computer/source_to_project_bridge_map.md` now routes 20 maintained
  computer source notes across 56 project routes, and
  `sources/computer/README.md` now points to the generated bridge map. Local
  rollback manifests:
  `backups/bundle_source-shelves_computer-project-bridge_20260415T131755Z/`
  and `backups/bundle_source-shelves_computer-project-bridge_20260415T131900Z/`.
- [ ] Deferred environment task: Windows PC access is tapped off until the
  machine direction is settled, likely after a Linux conversion.

## Operating Rules

- [x] Treat `/Volumes/wiki` as canonical editorial storage.
- [x] Treat `state/catalog.sqlite` as derived and rebuildable.
- [x] Keep generated state, backups, and patch bundles out of committed source
  unless explicitly promoted.
- [x] Route NAS edits through patch bundles, dry runs, backups, and manifests.
- [x] Use `dev://<repo>/<path>` for links into local development repos.
- [x] Keep Windows dev-root configuration local in `state/devrefs_config.json`.
- [x] Persist executable harness traces in `state/harness.sqlite`.
- [x] Add rollback and restore instructions before larger NAS edit waves.

## Phase 0: Orientation And Safety Rails

- [x] Confirm the private wiki is mounted at `/Volumes/wiki`.
- [x] Create local tooling repo under `~/dev/wiki`.
- [x] Add `.gitignore` coverage for derived state and local artifacts.
- [x] Establish that Markdown on the NAS is canonical.
- [x] Establish that SQLite/catalog artifacts are derived read layers.
- [x] Exclude noisy/generated directories from scans:
  `.git`, `.obsidian`, `.venv`, `__pycache__`, `@Recycle`,
  `@Recently-Snapshot`, `runtime`, `site-packages`, `node_modules`, and `tmp`.
- [x] Create first git commit checkpoint for the local tooling repo:
  `6a592ab`.
- [x] Add guarded local wiki mirror under ignored `state/wiki_mirror/`.
- [ ] Decide whether roadmap/tasklist docs should also be mirrored into the NAS
  wiki.

## Phase 1: Catalog And Audit Foundation

- [x] Implement read-only NAS scan command.
- [x] Build `state/catalog.sqlite`.
- [x] Index Markdown documents.
- [x] Index heading spans.
- [x] Index Markdown links.
- [x] Index symbols.
- [x] Add full-text search over documents, spans, and symbols.
- [x] Add `find` command.
- [x] Add `refs` command.
- [x] Add `headings` command.
- [x] Add `broken-links` command.
- [x] Add `audit` command.
- [x] Add `explain` command that prefers bounded symbols and spans over
  full-file reads.
- [x] Add `open` command with Mac and Windows path translation.
- [x] Verify current audit passes with zero actionable broken links.
- [x] Add stale-scan detection when the NAS has changed after the last catalog
  build.
- [x] Add a single health command that runs scan, audit, harness validation, and
  unit tests.
- [x] Add optional scheduled scan/audit runner.

## Phase 2: Link Hygiene And Navigation Value

- [x] Triage unresolved links by category.
- [x] Implement `devrefs audit`.
- [x] Implement `devrefs bundle`.
- [x] Convert local absolute repo links to portable `dev://` references.
- [x] Apply portable dev-reference bundle to the NAS.
- [x] Implement `missing-notes audit`.
- [x] Implement `missing-notes bundle`.
- [x] Create conservative stub pages for real missing Markdown notes.
- [x] Apply missing-note stub bundle to the NAS.
- [x] Implement `file-links audit`.
- [x] Implement `file-links bundle`.
- [x] Convert code/test file links to portable `dev://` references where
  appropriate.
- [x] Apply non-Markdown file-link bundle to the NAS.
- [x] Classify template placeholder links separately.
- [x] Exclude template placeholders from actionable broken-link counts.
- [ ] Configure Windows or Linux PC dev root after the desktop environment is
  finalized.
- [ ] Test `dev://` opening behavior on the future PC environment.
- [x] Add alias maps for renamed notes and project shorthand.
- [x] Add per-project backlink reports.
- [x] Add orphan-note reports.
- [x] Add missing-hub-page reports.
- [x] Add a stub-fill queue so generated placeholder notes can be promoted into
  useful pages.

## Phase 3: Patch Bundle Safety

- [x] Define patch-bundle validation flow.
- [x] Add dry-run summaries before NAS writes.
- [x] Create backups before applying bundles.
- [x] Write manifests for applied NAS bundles.
- [x] Implement target type: `replace_link_target`.
- [x] Implement target type: `replace_markdown_link`.
- [x] Implement target type: `create_markdown_stub`.
- [x] Preserve reviewable bundle files under `patch_bundles/`.
- [x] Preserve applied bundle backups under `backups/`.
- [x] Add rollback command that can restore files from a bundle manifest.
- [x] Add bundle report command that summarizes changed files and target types.
- [x] Add richer bundle schema docs.
- [x] Add preflight check that refuses writes when catalog root and bundle root
  disagree.

## Phase 4: Executable Harness Layer

- [x] Read and incorporate `executable harness layer.md`.
- [x] Add task contract specs in `harness_specs/task_contracts.md`.
- [x] Add reasoning chain specs in `harness_specs/reasoning_chains.md`.
- [x] Add failure taxonomy specs in `harness_specs/failure_taxonomy.md`.
- [x] Implement dependency-free harness spec loader.
- [x] Implement deterministic `wiki.answer_with_citations`.
- [x] Add groundedness checks for generated answers.
- [x] Persist harness runs to `state/harness.sqlite`.
- [x] Add `harness validate`.
- [x] Add `harness answer`.
- [x] Add `harness runs`.
- [x] Add `harness show`.
- [x] Add unit tests for the harness.
- [x] Verify current harness run passes.
- [x] Add structured-output LLM adapter behind the synthesis step.
- [x] Add failure-taxonomy action engine for retries and fallback behavior.
- [x] Add retrieval fallback chain when the first query misses.
- [x] Tighten contract and schema validation.
- [x] Add trace diffing between harness runs.

## Phase 5: Retrieval Quality And Evals

- [x] Create initial `eval/wiki_queries_v1.jsonl`.
- [x] Expand the gold query set to at least 30 wiki queries.
- [x] Mark expected documents, evidence hints, and citation requirements for
  each eval query.
- [x] Build eval runner over the catalog and harness.
- [x] Score retrieval hit rate.
- [x] Score citation validity.
- [x] Score broken-link regression.
- [x] Produce repeatable eval reports.
- [x] Compare retrieval profiles before changing search behavior.
- [x] Use eval results to choose cleanup targets instead of relying on memory.

## Phase 6: Knowledge Server And API

- [x] Choose initial API shape: JSON-RPC, MCP-style local server, or simple CLI
  wrapper service.
- [x] Add bounded method for `symbol.search`.
- [x] Add bounded method for `span.searchText`.
- [x] Add bounded method for `span.listHeadings`.
- [x] Add bounded method for `link.findReferences`.
- [x] Add bounded method for `audit.summary`.
- [x] Add bounded method for `harness.run`.
- [x] Add bounded method for `harness.show`.
- [x] Record query traces and policy decisions.
- [x] Return handles and spans by default instead of whole documents.
- [x] Add tests for API response limits.

## Phase 7: Mac And Windows Access

- [x] Support Mac wiki mount default: `/Volumes/wiki`.
- [x] Support Windows wiki mount default: `W:\`.
- [x] Support Mac dev root default: `/Users/kogaryu/dev`.
- [x] Keep Windows dev root intentionally unconfigured by default.
- [x] Document local Windows dev-root config.
- [ ] Defer Windows PC setup until the planned hardware/Linux decision is
  complete.
- [ ] Configure the future PC dev root after the operating system is final.
- [ ] Verify `wiki_tool open <wiki-path>` on the future PC environment.
- [ ] Verify `wiki_tool open dev://<repo>/<path>` on the future PC environment.
- [ ] Document editor workflow for MacBook.
- [ ] Document editor workflow for Windows PC.
- [ ] Decide whether `dev://` needs a clickable local handler or CLI-only
  resolution is enough.

## Phase 8: NAS Editorial Operations

- [x] Generate conservative stubs for missing Markdown pages.
- [x] Preserve existing wiki navigation while reducing broken links.
- [x] Build a report of generated stubs that still need human content.
- [x] Add page quality reports for thin notes, missing summaries, and unclear
  hub pages.
- [x] Add project-level librarian reports.
- [x] Add local source shelf reports for math and computer science books.
- [x] Use source shelf reports to prioritize math/computer book cleanup.
- [x] Use source shelf reports to fill computer science source-note summaries.
- [x] Apply the first computer source-shelf cleanup bundle to the local mirror.
- [x] Add book-to-concept bridge maps for math.
- [x] Add source-to-project bridge maps for computer science.
- [x] Add a local stub-fill queue and evidence packets for generated stubs.
- [x] Add an intake process for new notes.
- [ ] Add a promote process for rough notes becoming canonical pages.
- [ ] Add a template-placeholder policy so templates stay useful without
  polluting audits.
- [ ] Add recurring audit review cadence.

## Phase 9: Hardening And Release

- [x] Verify unit tests after harness implementation.
- [x] Verify `compileall` after harness implementation.
- [x] Verify live audit passes after NAS cleanup bundles.
- [x] Verify one live harness run passes.
- [x] Create first git commit checkpoint: `6a592ab`.
- [x] Add install/setup instructions.
- [x] Add a single smoke-test command.
- [x] Add backup restore docs.
- [x] Add package entry point if CLI use becomes frequent.
- [x] Add release notes for the first usable build.

## Next 10 Tasks

1. [ ] Add a promote process for rough notes becoming canonical pages.
2. [ ] Add a template-placeholder policy so templates stay useful without
   polluting audits.
3. [ ] Document editor workflow for MacBook.
4. [ ] Add recurring audit review cadence.
5. [ ] Decide when local source-shelf changes should be promoted to NAS.
6. [ ] Verify local mirror bridge pages after the next NAS refresh.
7. [ ] Revisit PC access after the Windows-to-Linux decision is final.
8. [ ] Add Linux path support if the desktop migration lands on Linux.
9. [ ] Decide whether roadmap/tasklist docs should also be mirrored into the
    NAS wiki.
10. [ ] Decide whether the Rudedude Pack 2 intake packet should be promoted to
    the NAS after formula/source review.

## Core Commands

Health:

```bash
wiki health --wiki-root /Volumes/wiki --json
python3 -m wiki_tool health --wiki-root /Volumes/wiki --json
```

After `python3 -m pip install -e .`, `wiki ...` is the short command form.
`python3 -m wiki_tool ...` remains the no-install fallback.

Local mirror:

```bash
tools/sync_wiki_mirror.sh
python3 -m wiki_tool scan --wiki-root state/wiki_mirror --json
python3 -m wiki_tool scan-status --json
python3 -m wiki_tool scan-status --wiki-root /Volumes/wiki --limit 25 --json
python3 -m wiki_tool audit --json
```

Harness:

```bash
python3 -m wiki_tool harness validate --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis deterministic --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis openai --llm-model gpt-5.4-mini --json
python3 -m wiki_tool harness runs --json
python3 -m wiki_tool harness show <run_id> --json
python3 -m wiki_tool harness diff --latest --json
python3 -m wiki_tool harness diff <base_run_id> <head_run_id> --json
```

Eval:

```bash
python3 -m wiki_tool eval run --json
python3 -m wiki_tool eval run --write-report --json
python3 -m wiki_tool eval compare-profiles --json
python3 -m wiki_tool eval compare-profiles --write-report --json
python3 -m wiki_tool eval cleanup-targets --json
python3 -m wiki_tool eval cleanup-targets --write-report --json
```

Eval runs include broken-link regression scoring from the current catalog.

Scheduled audit:

```bash
python3 -m wiki_tool scheduled-audit run --json
python3 -m wiki_tool scheduled-audit run --write-report --json
python3 -m wiki_tool scheduled-audit run --require-eval --json
```

Scheduled audit reports are local ignored Markdown under
`state/scheduled_audits/`. Eval is advisory by default until the cleanup queue
is burned down; use `--require-eval` when eval should be a blocking gate.

API:

```bash
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":1,"method":"symbol.search","params":{"query":"adapter boundary"}}' --json
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":1,"method":"harness.run","params":{"query":"adapter boundary"}}' --json
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":2,"method":"harness.show","params":{"run_id":"<run_id>"}}' --json
python3 -m wiki_tool api serve
```

Link queues:

```bash
python3 -m wiki_tool devrefs audit --json
python3 -m wiki_tool missing-notes audit --json
python3 -m wiki_tool file-links audit --json
python3 -m wiki_tool aliases validate --json
python3 -m wiki_tool aliases list --json
python3 -m wiki_tool project-reports summary --json
python3 -m wiki_tool project-reports show stock_trading --limit 25 --json
python3 -m wiki_tool project-reports write --output-dir state/project_reports --limit 25 --json
python3 -m wiki_tool source-shelves summary --json
python3 -m wiki_tool source-shelves show math --limit 25 --json
python3 -m wiki_tool source-shelves show computer --limit 25 --json
python3 -m wiki_tool source-shelves write --output-dir state/source_shelf_reports --limit 25 --json
python3 -m wiki_tool source-shelves cleanup-bundle computer --output patch_bundles/source_shelves_computer_cleanup.json --json
python3 -m wiki_tool source-shelves bridge-bundle math --output patch_bundles/source_shelves_math_bridge_map.json --json
python3 -m wiki_tool source-shelves bridge-bundle computer --output patch_bundles/source_shelves_computer_project_bridge_map.json --json
python3 -m wiki_tool page-quality summary --json
python3 -m wiki_tool page-quality stubs --json
python3 -m wiki_tool page-quality stub-fill-queue --limit 25 --json
python3 -m wiki_tool page-quality write --output-dir state/page_quality --json
python3 -m wiki_tool intake validate --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --json
python3 -m wiki_tool intake write --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --output-dir state/intake --json
python3 -m wiki_tool intake bundle --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --wiki-root state/wiki_mirror --output patch_bundles/intake_demo_repo_demand.json --json
```

Patch bundles:

Schema: `PATCH_BUNDLE_SCHEMA.md`
Restore guide: `BACKUP_RESTORE.md`

```bash
python3 -m wiki_tool patch-bundle validate <bundle.json> --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report <bundle.json> --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle apply <bundle.json> --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool patch-bundle apply <bundle.json> --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --dry-run --json
```
