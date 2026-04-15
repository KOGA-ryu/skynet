# Wiki Usability Tooling

This repo is the local build/tooling layer for the private NAS wiki. The NAS
Markdown tree remains the canonical editorial source; this repo builds derived
navigation, audit, and patch-bundle artifacts around it.

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
python3 -m wiki_tool page-quality summary --json
python3 -m wiki_tool page-quality thin --json
python3 -m wiki_tool page-quality missing-summaries --json
python3 -m wiki_tool page-quality stubs --json
python3 -m wiki_tool page-quality stub-fill-queue --limit 25 --json
python3 -m wiki_tool page-quality unclear-hubs --json
python3 -m wiki_tool page-quality write --output-dir state/page_quality --json
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
python3 -m wiki_tool harness answer "adapter boundary" --synthesis openai --llm-model gpt-5.4-mini --json
python3 -m wiki_tool harness runs --json
python3 -m wiki_tool harness show <run_id> --json
python3 -m wiki_tool eval run --json
python3 -m wiki_tool eval run --write-report --json
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
- Harness failures are mapped through the failure taxonomy, with safe retries
  and deferred remediation actions recorded in run traces.
- Empty primary retrieval automatically applies a bounded lexical fallback
  before synthesis.
- `eval compare-profiles` compares eval-only retrieval profiles against the
  current span FTS baseline before any production search behavior changes.
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
