# Wiki Usability Tooling

This repo is the local build/tooling layer for the private NAS wiki. The NAS
Markdown tree remains the canonical editorial source; this repo builds derived
navigation, audit, and patch-bundle artifacts around it.

Defaults:

- Mac wiki mount: `/Volumes/wiki`
- Windows wiki mount: `W:\`
- Local catalog: `state/catalog.sqlite`

Active build tracker: [ROADMAP_TASKLIST.md](ROADMAP_TASKLIST.md)

Run the full local health checkpoint:

```bash
python3 -m wiki_tool health --json
```

Run a read-only scan:

```bash
python3 -m wiki_tool scan --wiki-root /Volumes/wiki
```

Search the derived catalog:

```bash
python3 -m wiki_tool find "scanner evidence"
python3 -m wiki_tool refs concepts/retrieval.md
python3 -m wiki_tool headings projects/stock_trading/README.md
python3 -m wiki_tool broken-links --limit 25
python3 -m wiki_tool audit --write
python3 -m wiki_tool health --json
python3 -m wiki_tool explain "adapter boundary"
python3 -m wiki_tool open projects/stock_trading/apps/scanner.md --platform windows
python3 -m wiki_tool project-reports summary --json
python3 -m wiki_tool project-reports show stock_trading --json
python3 -m wiki_tool project-reports write --output-dir state/project_reports --json
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
- Markdown report writes are local-only under ignored `state/project_reports/`;
  they do not create NAS pages.

Design rules:

- Markdown on the NAS is canonical.
- SQLite is a derived read layer.
- v1 indexes Markdown text and validates links to existing wiki files.
- Template placeholder links are excluded from actionable broken-link counts.
- Harness specs live in `harness_specs/` as Markdown with fenced YAML blocks.
- Harness runs are persisted separately in `state/harness.sqlite`.
- Harness synthesis is deterministic by default; OpenAI structured-output
  synthesis is opt-in with `--synthesis openai` and requires `OPENAI_API_KEY`.
- Harness failures are mapped through the failure taxonomy, with safe retries
  and deferred remediation actions recorded in run traces.
- Alias maps are source-controlled read-layer metadata.
- Project reports are local read-layer outputs unless explicitly promoted
  through a later review workflow.
- Applied patch manifests can be reported and rolled back. Rollback verifies
  current file hashes before restoring backups or deleting generated stubs.
- No mass frontmatter rollout.
- No embeddings-first retrieval.
- Multi-file edits should go through local patch bundles and backups before any
  NAS write.
