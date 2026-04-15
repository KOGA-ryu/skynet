# Wiki Build Roadmap

The NAS wiki remains the canonical source. This repo is the local control plane
for scans, audits, patch bundles, and future query services.

For active checkbox tracking, use [ROADMAP_TASKLIST.md](ROADMAP_TASKLIST.md).

## Phase 0: Local Catalog Foundation

Status: implemented.

- Initialize local tooling repo.
- Scan `/Volumes/wiki` without modifying NAS content.
- Build a derived SQLite catalog in `state/catalog.sqlite`.
- Index Markdown documents, heading spans, links, and symbols.
- Support full-text search across documents, spans, and symbols.
- Support read-guard style `explain` output that prefers symbols and spans over
  full-file reads.
- Translate catalog paths for Mac and Windows access.
- Generate local audit reports under `state/`.

Acceptance check:

```bash
python3 -m unittest discover -s tests
python3 -m wiki_tool scan --wiki-root /Volumes/wiki --json
python3 -m wiki_tool audit --json
```

## Phase 1: Link Hygiene and Navigation Value

Goal: turn audit output into a practical cleanup queue.

Status: portable `dev://` references implemented and applied to the NAS.
Missing Markdown note stubs have also been generated from the catalog and
applied to the NAS.

- Triage unresolved links by category:
  - `missing_markdown_note`
  - `local_absolute_path`
  - `missing_non_markdown_file`
- Convert local dev repo paths to portable `dev://<repo>/<path>` references in
  reviewable patch bundles.
- Create conservative stub pages for real missing Markdown note targets so
  existing navigation resolves while content is filled in later.
- Convert code and test file links to portable `dev://` references when they
  point into local repos instead of canonical wiki files.
- Classify template placeholder links separately so templates do not pollute the
  actionable broken-link queue.
- Keep Windows dev roots unconfigured until a machine-specific local config is
  added under `state/devrefs_config.json`.
- Add alias maps for common renamed notes and project shorthand.
- Add per-project reports for backlinks, orphan notes, and missing hub pages.
- Keep all suggested edits in patch bundles until explicitly applied to the NAS.

Acceptance check:

```bash
python3 -m wiki_tool broken-links --limit 25
python3 -m wiki_tool devrefs audit --json
python3 -m wiki_tool devrefs bundle --output patch_bundles/devrefs_preview.json --json
python3 -m wiki_tool missing-notes audit --json
python3 -m wiki_tool missing-notes bundle --output patch_bundles/missing_notes_preview.json --json
python3 -m wiki_tool file-links audit --json
python3 -m wiki_tool file-links bundle --output patch_bundles/file_links_preview.json --json
python3 -m wiki_tool patch-bundle apply patch_bundles/devrefs_preview.json --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool refs concepts/scanner_evidence_and_snapshot_replay.md
```

## Phase 2: Patch Bundle Workflow

Goal: make multi-file wiki edits reviewable before touching the NAS.

- Define patch-bundle JSON schema for proposed note creates, edits, moves, and
  link repairs.
- Add bundle validation tests and dry-run summaries.
- Add local backups before any write operation.
- Add a human approval checkpoint before applying a bundle to `/Volumes/wiki`.

Implemented target types:

- `replace_link_target`
- `replace_markdown_link`
- `create_markdown_stub`

Acceptance check:

```bash
python3 -m wiki_tool patch-bundle validate patch_bundles/example.json
```

## Phase 3: Knowledge Server

Goal: expose the catalog through a stable API that tools and agents can use.

- Add an executable harness layer:
  - Markdown/YAML specs for task contracts, reasoning chains, and failures.
  - A deterministic runtime for `wiki.answer_with_citations`.
  - Step traces and retrieval candidates persisted to `state/harness.sqlite`.
- Add JSON-RPC methods for:
  - `symbol.search`
  - `span.searchText`
  - `span.listHeadings`
  - `link.findReferences`
  - `audit.summary`
- Record query traces and policy decisions.
- Keep method responses bounded so sub-agents receive handles and spans, not
  whole documents by default.

Acceptance check:

```bash
python3 -m wiki_tool explain "adapter boundary" --json
python3 -m wiki_tool harness validate --json
python3 -m wiki_tool harness answer "adapter boundary" --json
```

## Phase 4: Evaluation Harness

Goal: measure whether the wiki is becoming more usable.

- Expand `eval/wiki_queries_v1.jsonl` into a gold query set.
- Score symbol hit rate, span relevance, and broken-link regression.
- Add a repeatable report for retrieval quality and audit health.
- Use the metrics to pick the next cleanup work instead of relying on memory.

Acceptance check:

```bash
python3 -m unittest discover -s tests
python3 -m wiki_tool audit --write --json
```
