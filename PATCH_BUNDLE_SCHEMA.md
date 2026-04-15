# Patch Bundle Schema

Patch bundles are reviewable local plans for editing the NAS wiki. They are
generated under `patch_bundles/`, validated before use, applied to a selected
wiki root, and recorded with rollback manifests under `backups/`.

The implemented bundle system supports only three target types:

- `replace_link_target`
- `replace_markdown_link`
- `create_markdown_stub`

It does not support arbitrary text edits, deletes, moves, renames, or bulk
frontmatter changes.

## Bundle Envelope

Every patch bundle is a JSON object with these required fields:

| field | type | meaning |
|---|---|---|
| `bundle_id` | string | Stable bundle identifier, usually prefixed with `bundle:`. |
| `created_at_utc` | string | UTC creation timestamp. |
| `rationale` | string | Human-readable reason for the bundle. |
| `backup_manifest` | object or truthy value | Declares that backup/manifest safety is required before apply. |
| `targets` | array | Non-empty list of patch targets. |

Envelope skeleton:

```json
{
  "backup_manifest": {
    "required_before_apply": true,
    "status": "not_created"
  },
  "bundle_id": "bundle:example:20260415T000000Z",
  "created_at_utc": "2026-04-15T00:00:00+00:00",
  "rationale": "Repair wiki links through reviewed, reversible edits.",
  "source_catalog": {
    "db_path": "state/catalog.sqlite",
    "root": "/Volumes/wiki",
    "run_id": "scan:20260415T000000Z:example",
    "scanned_at_utc": "2026-04-15T00:00:00+00:00"
  },
  "targets": []
}
```

`backup_manifest` is required in the bundle. The actual applied manifest is
written only after `patch-bundle apply` succeeds without `--dry-run`. Real
bundles must use a non-empty `targets` array.

`source_catalog` is optional for legacy bundles, but current bundle generators
write it from the active catalog scan. Real applies refuse to write when
`source_catalog.root` or the active catalog database root does not match the
requested `--wiki-root`.

## Common Target Fields

Every target must include:

| field | type | meaning |
|---|---|---|
| `type` | string | One of the supported target types. |
| `path` | string | Affected wiki-relative path used for validation/report grouping. |
| `reason` | string | Human-readable reason for this target. |

For replacement targets, `source_path` is the wiki-relative Markdown file that
will be edited. For stub targets, `path` is the wiki-relative Markdown file that
will be created.

## `replace_link_target`

Use this when a Markdown link label is correct but the target should be swapped,
usually from a machine-specific local path to a portable `dev://` reference.

Required fields:

| field | type | validation |
|---|---|---|
| `category` | string | Classification of the original broken or local link. |
| `label` | string | Existing Markdown link label. |
| `line` | integer | 1-based source line containing the old target. |
| `new_target` | string | Must start with `dev://`. |
| `old_target` | string | Existing target expected at `source_path:line`. |
| `source_path` | string | Wiki-relative Markdown file to edit. |

Example:

```json
{
  "category": "local_absolute_path",
  "label": "Main",
  "line": 3,
  "new_target": "dev://repo/Main.qml",
  "old_target": "/Users/kogaryu/dev/repo/Main.qml",
  "path": "index.md",
  "reason": "Convert machine-specific local dev path to portable dev:// reference",
  "source_path": "index.md",
  "type": "replace_link_target"
}
```

Safety behavior:

- Validation checks that `source_path` exists when `--wiki-root` is supplied.
- Validation checks that `old_target` appears on the declared line.
- Apply changes only the first matching `](old_target)` on that line.

## `replace_markdown_link`

Use this when both the link label and target may need to change.

Required fields:

| field | type | validation |
|---|---|---|
| `line` | integer | 1-based source line containing the old Markdown link. |
| `new_label` | string | Replacement Markdown link label. |
| `new_target` | string | Replacement target. |
| `old_label` | string | Existing Markdown link label. |
| `old_target` | string | Existing Markdown link target. |
| `source_path` | string | Wiki-relative Markdown file to edit. |

Example:

```json
{
  "line": 12,
  "new_label": "Scanner Store Architecture",
  "new_target": "projects/stock_trading/scanner_store_architecture.md",
  "old_label": "scanner store",
  "old_target": "scanner_store.json",
  "path": "projects/stock_trading/README.md",
  "reason": "Repair non-Markdown file link to the canonical wiki note",
  "repair_kind": "wiki_note",
  "source_path": "projects/stock_trading/README.md",
  "type": "replace_markdown_link"
}
```

Safety behavior:

- Validation checks that `source_path` exists when `--wiki-root` is supplied.
- Validation checks that `[old_label](old_target)` appears on the declared line.
- Validation checks that `new_target` resolves unless it is an external URL,
  `mailto:`, `obsidian:`, `dev://`, an anchor, or an empty anchor target.
- Apply changes only the first exact `[old_label](old_target)` on that line.

## `create_markdown_stub`

Use this to create a conservative Markdown page for a real missing note.

Required fields:

| field | type | validation |
|---|---|---|
| `body` | string | Full Markdown body for the new file. |
| `inbound_references` | array | Non-empty list of references that justify the stub. |
| `path` | string | Wiki-relative `.md` path to create. |
| `title` | string | Expected top-level title. |

Example:

```json
{
  "body": "# Missing Note\n\n## Why This Page Exists\n\n- This stub exists because current wiki notes link to `docs/missing_note.md`.\n",
  "inbound_references": [
    {
      "label": "Missing Note",
      "line": 8,
      "source_path": "index.md",
      "target_raw": "docs/missing_note.md"
    }
  ],
  "path": "docs/missing_note.md",
  "reason": "Create navigable stub for unresolved Markdown note link",
  "title": "Missing Note",
  "type": "create_markdown_stub"
}
```

Safety behavior:

- `path` must be wiki-relative and end with `.md`.
- `path` must not be absolute and must not contain `..`.
- `body` must start with `# {title}`.
- Validation fails if the target file already exists when `--wiki-root` is supplied.
- Apply refuses to overwrite an existing file.

## Write Root Preflight

Patch bundles are relative-path write plans, so the tool verifies the root they
were planned against before any real apply:

- Generated bundles include `source_catalog.root`, `source_catalog.run_id`, and
  `source_catalog.scanned_at_utc`.
- The CLI also checks the active catalog database selected by global `--db`
  against the requested `--wiki-root`.
- A real apply fails if either checked root differs from `--wiki-root`.
- A dry run still validates targets and reports `preflight.status: "mismatch"`
  without writing.
- This prevents a mirror-built or stale catalog from silently writing to a
  different tree.

## Applied Manifest

When a real apply succeeds, the tool writes:

```text
backups/<safe_bundle_id>/manifest.json
```

The manifest contains:

| field | meaning |
|---|---|
| `applied_at_utc` | UTC apply timestamp. |
| `bundle_id` | Source bundle id. |
| `bundle_path` | Bundle path used at apply time. |
| `wiki_root` | Wiki root used for the apply operation. |
| `files` | Per-file replacement/create records. |

Replacement file records include:

- `path`
- `backup_path`
- `old_sha256`
- `new_sha256`
- `replacement_count`
- `would_change`

Created stub records include:

- `action: "create"`
- `path`
- `backup_path: null`
- `old_sha256: null`
- `new_sha256`
- `replacement_count: 0`
- `would_change: true`

## Command Flow

Review a bundle:

```bash
python3 -m wiki_tool patch-bundle validate patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --json
```

Dry-run before writing:

```bash
python3 -m wiki_tool patch-bundle apply patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --dry-run --json
```

Apply after review:

```bash
python3 -m wiki_tool patch-bundle apply patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --json
```

Real applies run a root-agreement preflight. If the active catalog was built
from `state/wiki_mirror`, an apply to `/Volumes/wiki` is blocked until the
catalog is rebuilt from `/Volumes/wiki` or the bundle is regenerated from a
matching catalog. Dry runs report the mismatch without writing.

Inspect an applied manifest:

```bash
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
```

Dry-run rollback:

```bash
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --dry-run --json
```

Rollback after review:

```bash
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
```

## Rollback Safety

Rollback is intentionally conservative:

- Replacement rollback requires the backup file to exist.
- Backup hash must match `old_sha256` when present.
- Current wiki file hash must match `new_sha256` before restore.
- Created-stub rollback deletes only when the current file hash matches
  `new_sha256`.
- If the current file was edited after apply, rollback reports
  `blocked_current_mismatch` and refuses to modify it.
- Unsafe paths, missing files, missing backups, and unsupported actions block
  rollback.

These checks keep rollback from erasing unrelated user edits.
