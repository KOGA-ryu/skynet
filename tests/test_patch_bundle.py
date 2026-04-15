from hashlib import sha256
from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.patch_bundle import (
    apply_patch_bundle,
    report_patch_bundle,
    rollback_patch_bundle,
)


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class PatchBundleReportRollbackTests(unittest.TestCase):
    def test_report_summarizes_patch_bundle_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "backup_manifest": True,
                        "bundle_id": "bundle:test",
                        "created_at_utc": "2026-04-15T00:00:00Z",
                        "rationale": "test bundle",
                        "targets": [
                            {
                                "category": "local_absolute_path",
                                "label": "Main",
                                "line": 3,
                                "new_target": "dev://repo/Main.qml",
                                "old_target": "/Users/kogaryu/dev/repo/Main.qml",
                                "path": "/Users/kogaryu/dev/repo/Main.qml",
                                "reason": "portable dev ref",
                                "source_path": "index.md",
                                "type": "replace_link_target",
                            },
                            {
                                "body": "# Missing\n",
                                "inbound_references": [{"path": "index.md", "line": 5}],
                                "path": "docs/missing.md",
                                "reason": "missing note",
                                "title": "Missing",
                                "type": "create_markdown_stub",
                            },
                        ],
                    }
                )
            )

            report = report_patch_bundle(bundle_path)
            self.assertEqual(report["kind"], "patch_bundle")
            self.assertEqual(report["target_count"], 2)
            self.assertEqual(report["affected_paths"], ["docs/missing.md", "index.md"])
            self.assertEqual(
                report["target_types"],
                [
                    {"count": 1, "type": "create_markdown_stub"},
                    {"count": 1, "type": "replace_link_target"},
                ],
            )
            self.assertTrue(report["valid"], report["validation_errors"])

    def test_rollback_restores_replaced_file_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            note = root / "index.md"
            original = "# Home\n\n[Main](/Users/kogaryu/dev/repo/Main.qml)\n"
            note.write_text(original)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(replace_bundle()))

            applied = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=Path(tmp) / "backups",
            )
            self.assertIn("dev://repo/Main.qml", note.read_text())

            dry_run = rollback_patch_bundle(
                Path(applied["manifest_path"]),
                wiki_root=root,
                dry_run=True,
            )
            self.assertFalse(dry_run["rolled_back"])
            self.assertEqual(dry_run["actions"][0]["action"], "restore")
            self.assertIn("dev://repo/Main.qml", note.read_text())

            result = rollback_patch_bundle(Path(applied["manifest_path"]), wiki_root=root)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(note.read_text(), original)

    def test_rollback_deletes_created_stub_when_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(create_bundle()))

            applied = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=Path(tmp) / "backups",
            )
            stub = root / "docs" / "missing.md"
            self.assertTrue(stub.exists())

            result = rollback_patch_bundle(Path(applied["manifest_path"]), wiki_root=root)
            self.assertTrue(result["rolled_back"])
            self.assertFalse(stub.exists())

            dry_run = rollback_patch_bundle(
                Path(applied["manifest_path"]),
                wiki_root=root,
                dry_run=True,
            )
            self.assertEqual(dry_run["actions"][0]["status"], "already_missing")

    def test_rollback_refuses_current_file_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            note = root / "index.md"
            note.write_text("# Home\n\n[Main](/Users/kogaryu/dev/repo/Main.qml)\n")
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(replace_bundle()))

            applied = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=Path(tmp) / "backups",
            )
            note.write_text("# Home\n\n[Main](dev://repo/Changed.qml)\n")

            dry_run = rollback_patch_bundle(
                Path(applied["manifest_path"]),
                wiki_root=root,
                dry_run=True,
            )
            self.assertEqual(dry_run["blocked"][0]["status"], "blocked_current_mismatch")
            with self.assertRaises(ValueError):
                rollback_patch_bundle(Path(applied["manifest_path"]), wiki_root=root)
            self.assertIn("Changed.qml", note.read_text())

    def test_legacy_manifest_can_report_and_rollback_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            note = root / "index.md"
            old_text = "# Home\n\n[Main](/Users/kogaryu/dev/repo/Main.qml)\n"
            current_text = "# Home\n\n[Main](dev://repo/Main.qml)\n"
            note.write_text(current_text)
            backup_root = Path(tmp) / "backups" / "legacy"
            backup_root.mkdir(parents=True)
            backup = backup_root / "index.md"
            backup.write_text(old_text)
            manifest_path = backup_root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "bundle_id": "bundle:legacy",
                        "files": [
                            {
                                "backup_path": str(backup),
                                "current_exists": True,
                                "current_sha256": digest(current_text),
                                "old_sha256": digest(old_text),
                                "path": "index.md",
                            }
                        ],
                    }
                )
            )

            report = report_patch_bundle(manifest_path, wiki_root=root)
            self.assertEqual(report["files"][0]["status"], "ready")
            result = rollback_patch_bundle(manifest_path, wiki_root=root)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(note.read_text(), old_text)


def replace_bundle() -> dict[str, object]:
    return {
        "backup_manifest": True,
        "bundle_id": "bundle:replace",
        "created_at_utc": "2026-04-15T00:00:00Z",
        "rationale": "replace test",
        "targets": [
            {
                "category": "local_absolute_path",
                "label": "Main",
                "line": 3,
                "new_target": "dev://repo/Main.qml",
                "old_target": "/Users/kogaryu/dev/repo/Main.qml",
                "path": "/Users/kogaryu/dev/repo/Main.qml",
                "reason": "portable dev ref",
                "source_path": "index.md",
                "type": "replace_link_target",
            }
        ],
    }


def create_bundle() -> dict[str, object]:
    return {
        "backup_manifest": True,
        "bundle_id": "bundle:create",
        "created_at_utc": "2026-04-15T00:00:00Z",
        "rationale": "create test",
        "targets": [
            {
                "body": "# Missing\n\nGenerated stub.\n",
                "inbound_references": [{"path": "index.md", "line": 1}],
                "path": "docs/missing.md",
                "reason": "missing note",
                "title": "Missing",
                "type": "create_markdown_stub",
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
