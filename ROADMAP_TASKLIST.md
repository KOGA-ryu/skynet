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
- [x] Catalog size: 845 documents, 4,258 links, 6,659 spans, 7,482 symbols.
- [x] Latest scan run: `scan:20260415T100809Z:f4351838b68707a7`.
- [x] Latest harness run: `run:20260415T100815Z:838b2e4cc6371c4c`.
- [x] Latest harness task: `wiki.answer_with_citations`.
- [x] Latest harness status: pass.
- [x] Latest source checkpoint: `8f073d2` (`Keep mirror guard marker outside sync root`).
- [ ] Current active task: add preflight check that refuses writes when catalog
  root and bundle root disagree.
- [x] Local mirror status: complete, with `state/wiki_mirror/` refreshed from
  the NAS and heavy stock-trading payloads excluded.
- [x] Project report implementation status: complete, with 7 top-level projects
  summarized and local reports written under `state/project_reports/`.
- [x] Alias map implementation status: complete, with 6 validated source
  aliases.
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
- [ ] Add stale-scan detection when the NAS has changed after the last catalog
  build.
- [x] Add a single health command that runs scan, audit, harness validation, and
  unit tests.
- [ ] Add optional scheduled scan/audit runner.

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
- [ ] Add a stub-fill queue so generated placeholder notes can be promoted into
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
- [ ] Add preflight check that refuses writes when catalog root and bundle root
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
- [ ] Tighten contract and schema validation.
- [ ] Add trace diffing between harness runs.

## Phase 5: Retrieval Quality And Evals

- [x] Create initial `eval/wiki_queries_v1.jsonl`.
- [x] Expand the gold query set to at least 30 wiki queries.
- [x] Mark expected documents, evidence hints, and citation requirements for
  each eval query.
- [x] Build eval runner over the catalog and harness.
- [x] Score retrieval hit rate.
- [x] Score citation validity.
- [ ] Score broken-link regression.
- [x] Produce repeatable eval reports.
- [ ] Compare retrieval profiles before changing search behavior.
- [ ] Use eval results to choose cleanup targets instead of relying on memory.

## Phase 6: Knowledge Server And API

- [x] Choose initial API shape: JSON-RPC, MCP-style local server, or simple CLI
  wrapper service.
- [x] Add bounded method for `symbol.search`.
- [x] Add bounded method for `span.searchText`.
- [x] Add bounded method for `span.listHeadings`.
- [x] Add bounded method for `link.findReferences`.
- [x] Add bounded method for `audit.summary`.
- [ ] Add bounded method for `harness.run`.
- [ ] Add bounded method for `harness.show`.
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
- [ ] Build a report of generated stubs that still need human content.
- [x] Add page quality reports for thin notes, missing summaries, and unclear
  hub pages.
- [ ] Add project-level librarian reports.
- [ ] Add an intake process for new notes.
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
- [ ] Add backup restore docs.
- [ ] Add package entry point if CLI use becomes frequent.
- [ ] Add release notes for the first usable build.

## Next 10 Tasks

1. [ ] Add preflight check that refuses writes when catalog root and bundle root
   disagree.
2. [ ] Tighten contract and schema validation.
3. [ ] Add trace diffing between harness runs.
4. [ ] Add backup restore docs.
5. [ ] Add broken-link regression scoring to eval reports.
6. [ ] Add bounded JSON-RPC methods for `harness.run` and `harness.show`.
7. [ ] Add release notes for the first usable build.
8. [ ] Build a report of generated stubs that still need human content.
9. [ ] Add project-level librarian reports.
10. [ ] Revisit PC access after the Windows-to-Linux decision is final.

## Core Commands

Health:

```bash
python3 -m wiki_tool health --wiki-root /Volumes/wiki --json
```

Local mirror:

```bash
tools/sync_wiki_mirror.sh
python3 -m wiki_tool scan --wiki-root state/wiki_mirror --json
python3 -m wiki_tool audit --json
```

Harness:

```bash
python3 -m wiki_tool harness validate --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis deterministic --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis openai --llm-model gpt-5.4-mini --json
python3 -m wiki_tool harness runs --json
python3 -m wiki_tool harness show <run_id> --json
```

Eval:

```bash
python3 -m wiki_tool eval run --json
python3 -m wiki_tool eval run --write-report --json
```

API:

```bash
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":1,"method":"symbol.search","params":{"query":"adapter boundary"}}' --json
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
python3 -m wiki_tool project-reports show stock_trading --json
python3 -m wiki_tool page-quality summary --json
python3 -m wiki_tool page-quality write --output-dir state/page_quality --json
```

Patch bundles:

Schema: `PATCH_BUNDLE_SCHEMA.md`

```bash
python3 -m wiki_tool patch-bundle validate <bundle.json> --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report <bundle.json> --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle apply <bundle.json> --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool patch-bundle apply <bundle.json> --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --dry-run --json
```
