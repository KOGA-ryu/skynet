# First Usable Build Release Notes

Date: 2026-04-15

This is the first usable checkpoint for the private NAS wiki tooling. The NAS
Markdown tree remains the canonical wiki. This repo is the local control plane
for scans, catalogs, audits, patch bundles, harness runs, evals, and local query
APIs.

## Status Snapshot

- Current audit status: pass.
- Actionable broken links: 0.
- Excluded template placeholder links: 34.
- Current catalog size: 845 documents, 4,258 links, 6,659 spans, and 7,482
  symbols.
- Latest known scan: `scan:20260415T112457Z:f4351838b68707a7`.
- Latest known harness run: `run:20260415T105602Z:4be3b4fc6f51c811`.
- Local mirror support is in place under ignored `state/wiki_mirror/`, with
  heavy stock-trading payloads excluded by `config/wiki_mirror_excludes.txt`.

## What Works Now

- Read-only catalog builds from `/Volumes/wiki` or `state/wiki_mirror`.
- Full local health checks cover scan, audit, harness validation, and unit
  tests.
- Scan freshness checks compare the catalog against the current wiki root and
  fail audit output when Markdown documents or tracked file paths have moved
  ahead of the latest scan.
- Search/navigation commands can find documents, headings, references, symbols,
  explanations, project reports, and page-quality queues.
- Project reports include librarian-priority counts for generated stubs,
  reviewable orphans, weak summaries, thin notes, unclear hubs, templates, and
  generated state artifacts.
- Source shelf reports inventory the local math and computer source shelves,
  flag weak source notes, and build a local staging queue before NAS promotion.
- Computer source shelf cleanup bundles can now add exact source-summary blocks
  and remove reviewed generated placeholder pages in the local mirror.
- Computer source shelf bridge bundles can now generate a source-to-project map
  for books and OSS pattern notes in the local mirror.
- Page-quality queues include a focused generated-stub report for placeholder
  Markdown pages that still need human-written content.
- Stub-fill queues rank generated stubs and write local evidence packets for
  human promotion work without replacing stub content.
- Patch bundles provide the guarded path for NAS edits with validation, dry-run
  summaries, backups, manifests, reports, and rollback.
- Portable `dev://<repo>/<path>` references work for the Mac dev root and are
  intentionally not finalized for the future PC yet.
- The executable harness can answer wiki questions with citations, persist run
  traces, validate task specs, map failures to actions, retry bounded retrieval
  fallbacks, and diff runs.
- Retrieval profile comparison can score eval-only candidates against the
  current span FTS baseline without changing production search behavior.
- Eval cleanup targets turn retrieval misses and low-ranked expected paths into
  a local editorial priority queue enriched with page-quality signals.
- Scheduled audit runs combine audit, harness spec validation, eval regression,
  and cleanup-target generation into one scheduler-friendly local checkpoint.
  Eval is advisory by default and can be made strict with `--require-eval`.
- The local JSON-RPC API exposes bounded methods for search, headings,
  references, audit summary, `harness.run`, and `harness.show`.

## Safe Operating Boundary

- Do normal build and review work against `state/wiki_mirror/` when possible to
  avoid repeated NAS traversal.
- Treat `state/catalog.sqlite`, `state/harness.sqlite`, `state/api_traces.jsonl`,
  and generated reports under `state/` as rebuildable local state.
- Do not edit NAS Markdown directly for multi-file cleanup. Use patch bundles,
  dry runs, backups, manifests, and post-apply verification.
- Before any real NAS write, rebuild the catalog from `/Volumes/wiki`; mirror
  catalogs are for local read/build work.
- OpenAI-backed harness synthesis is opt-in. Deterministic synthesis is the
  default for routine verification.

## Known Good Commands

Run the full local checkpoint:

```bash
python3 -m wiki_tool health --wiki-root /Volumes/wiki --json
```

Refresh the local mirror and rebuild the derived catalog:

```bash
tools/sync_wiki_mirror.sh
python3 -m wiki_tool scan --wiki-root state/wiki_mirror --json
python3 -m wiki_tool scan-status --json
python3 -m wiki_tool audit --json
```

Run a deterministic harness answer and inspect the run:

```bash
python3 -m wiki_tool harness answer "adapter boundary" --synthesis deterministic --json
python3 -m wiki_tool harness show <run_id> --json
```

Run the eval suite:

```bash
python3 -m wiki_tool eval run --json
python3 -m wiki_tool eval compare-profiles --json
python3 -m wiki_tool eval cleanup-targets --json
```

Run the scheduled audit checkpoint:

```bash
python3 -m wiki_tool scheduled-audit run --json
python3 -m wiki_tool scheduled-audit run --write-report --json
```

Review project and generated-stub librarian queues:

```bash
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
python3 -m wiki_tool page-quality stubs --json
python3 -m wiki_tool page-quality stub-fill-queue --limit 25 --json
python3 -m wiki_tool page-quality write --output-dir state/page_quality --json
```

Use the local JSON-RPC API:

```bash
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":1,"method":"harness.run","params":{"query":"adapter boundary","limit":3}}' --json
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":2,"method":"harness.show","params":{"run_id":"<run_id>","limit":3}}' --json
```

## Deferred Work

- Future PC access remains tapped off until the Windows-to-Linux decision is
  final.
- Linux path support and future PC `dev://` verification should be implemented
  only after that environment is known.
- Scheduled audits have a command-only runner. Installing a cron, launchd, or
  systemd timer is still deferred until the review cadence is chosen.
- Generated stub pages now have a local promotion queue and evidence packets;
  replacing stub content remains a separate reviewed patch-bundle pass.
- Math source notes now have a generated book-to-concept bridge bundle for
  local-mirror staging.
- Computer-science source notes now have a generated source-to-project bridge
  bundle for local-mirror staging.
- Recurring editorial review cadence remains a next-stage editorial operation.

## Verification For This Checkpoint

The release checkpoint should pass:

```bash
python3 -m unittest discover -s tests
python3 -m compileall wiki_tool tests
python3 -m wiki_tool source-shelves summary --json
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root state/wiki_mirror --json
python3 -m wiki_tool scheduled-audit run --json
python3 -m wiki_tool audit --json
git diff --check
```
