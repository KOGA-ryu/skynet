# Backup Restore Guide

This guide covers restoring NAS wiki changes made through patch bundles.
Patch-bundle applies create backups and manifests under:

```text
backups/<safe_bundle_id>/manifest.json
```

Use this guide only for patch-bundle manifests created by
`python3 -m wiki_tool patch-bundle apply`. It covers the current supported
actions: link replacements and generated Markdown stubs.

## Standard Restore Flow

Confirm the intended wiki root before any rollback. The normal NAS write root is
`/Volumes/wiki`; local mirror roots such as `state/wiki_mirror` are for build and
review work.

Inspect the applied manifest:

```bash
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
```

Dry-run the rollback:

```bash
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --dry-run --json
```

Review the dry-run output:

- `blocked_count` must be `0` before a real rollback.
- `actions` shows what would be restored or deleted.
- `status: "ready"` means rollback can safely touch that file.
- Any `blocked_*` status means the real rollback will refuse to run.

Run the rollback only after the dry run is clean:

```bash
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
```

Verify afterward:

```bash
python3 -m wiki_tool scan --wiki-root /Volumes/wiki --json
python3 -m wiki_tool audit --json
```

## What Rollback Does

For replacement edits, rollback restores the file from the manifest
`backup_path`. The tool only restores when:

- the backup exists
- the backup hash matches `old_sha256` when recorded
- the current wiki file hash matches the manifest `new_sha256`

For generated stubs, rollback deletes the created Markdown file. The tool only
deletes when the current file hash still matches the manifest `new_sha256`.

These checks prevent rollback from overwriting user edits made after the bundle
was applied.

## Rollback Statuses

`ready` means the file can be restored or deleted by rollback.

`already_missing` means a generated stub is already gone. Rollback records no
change for that file.

`blocked_current_mismatch` means the target file changed after the bundle was
applied. Do not force rollback. Inspect the current file, decide what content to
keep, and make a new reviewed patch bundle if needed.

`blocked_missing_backup` means a replacement file cannot be restored because the
backup path is missing from the manifest or the backup file is gone.

`blocked_backup_hash_mismatch` means the backup file exists but no longer
matches the hash recorded when the bundle was applied.

`blocked_current_missing` means a replacement target file is missing from the
wiki root.

`blocked_missing_expected_hash` means the manifest does not contain the current
hash needed for safe rollback.

`blocked_unsafe_path` means the manifest path is absolute, escapes the wiki
root, or is otherwise unsafe.

`blocked_unsupported_action` means the manifest contains an action the rollback
tool does not support.

## Manual Restore Last Resort

Manual restore should be rare. Use it only when rollback is blocked and you have
confirmed the blocked status by reading the manifest report.

Before copying anything manually:

- save the current wiki file somewhere outside the NAS wiki
- confirm the manifest `path` is the wiki-relative file you intend to restore
- confirm `backup_path` points to the old content for that file
- compare the current file with the backup before replacing content

After a manual restore, rescan and audit:

```bash
python3 -m wiki_tool scan --wiki-root /Volumes/wiki --json
python3 -m wiki_tool audit --json
```

If multiple files are affected, prefer creating a new patch bundle over manual
multi-file copying.
