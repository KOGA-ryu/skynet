from pathlib import Path
import json
import tempfile
import unittest

from wiki_tool.catalog import broken_links, scan_wiki
from wiki_tool.devrefs import (
    build_devref_patch_bundle,
    devref_audit,
    local_path_to_devref,
    parse_dev_uri,
    resolve_dev_uri,
)
from wiki_tool.patch_bundle import apply_patch_bundle, validate_patch_bundle


class DevRefTests(unittest.TestCase):
    def test_local_path_converts_to_dev_uri(self) -> None:
        ref = local_path_to_devref("/Users/kogaryu/dev/RD_UI/qml/Main.qml")
        self.assertIsNotNone(ref)
        assert ref is not None
        self.assertEqual(ref.repo, "RD_UI")
        self.assertEqual(ref.path, "qml/Main.qml")
        self.assertEqual(ref.uri, "dev://RD_UI/qml/Main.qml")

    def test_local_path_with_spaces_round_trips(self) -> None:
        ref = local_path_to_devref("/Users/kogaryu/dev/repo name/docs/a file.md")
        self.assertIsNotNone(ref)
        assert ref is not None
        parsed = parse_dev_uri(ref.uri)
        self.assertEqual(parsed.repo, "repo name")
        self.assertEqual(parsed.path, "docs/a file.md")

    def test_resolve_dev_uri_platforms(self) -> None:
        mac = resolve_dev_uri("dev://RD_UI/qml/Main.qml", platform="mac")
        self.assertTrue(mac["configured"])
        self.assertEqual(mac["path"], "/Users/kogaryu/dev/RD_UI/qml/Main.qml")

        windows_missing = resolve_dev_uri("dev://RD_UI/qml/Main.qml", platform="windows")
        self.assertFalse(windows_missing["configured"])
        self.assertIn("not configured", windows_missing["error"])

        windows = resolve_dev_uri(
            "dev://RD_UI/qml/Main.qml",
            platform="windows",
            windows_root="D:\\dev",
        )
        self.assertTrue(windows["configured"])
        self.assertEqual(windows["path"], "D:\\dev\\RD_UI\\qml\\Main.qml")

    def test_devref_audit_and_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text(
                "# Home\n\n[Main](/Users/kogaryu/dev/RD_UI/qml/Main.qml)\n",
            )
            db = Path(tmp) / "catalog.sqlite"
            result = scan_wiki(root, db)
            self.assertEqual(result.broken_link_count, 1)

            audit = devref_audit(db)
            self.assertEqual(audit["candidate_count"], 1)
            self.assertEqual(audit["repos"][0]["repo"], "RD_UI")

            bundle = build_devref_patch_bundle(db)
            self.assertEqual(len(bundle["targets"]), 1)
            target = bundle["targets"][0]
            self.assertEqual(target["type"], "replace_link_target")
            self.assertEqual(target["new_target"], "dev://RD_UI/qml/Main.qml")

            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))
            validation = validate_patch_bundle(bundle_path, wiki_root=root)
            self.assertTrue(validation["valid"], validation["errors"])

            stale = bundle
            stale["targets"][0]["old_target"] = "/Users/kogaryu/dev/RD_UI/qml/Other.qml"
            bundle_path.write_text(json.dumps(stale))
            validation = validate_patch_bundle(bundle_path, wiki_root=root)
            self.assertFalse(validation["valid"])

    def test_patch_bundle_apply_dry_run_and_real_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            note = root / "index.md"
            note.write_text(
                "# Home\n\n[Main](/Users/kogaryu/dev/RD_UI/qml/Main.qml)\n",
            )
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_devref_patch_bundle(db)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))
            backup_dir = Path(tmp) / "backups"

            dry_run = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=backup_dir,
                dry_run=True,
            )
            self.assertEqual(dry_run["target_count"], 1)
            self.assertEqual(dry_run["file_count"], 1)
            self.assertFalse(backup_dir.exists())
            self.assertIn("/Users/kogaryu/dev/RD_UI/qml/Main.qml", note.read_text())

            applied = apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=backup_dir,
            )
            self.assertEqual(applied["target_count"], 1)
            self.assertTrue(Path(applied["manifest_path"]).exists())
            self.assertIn("dev://RD_UI/qml/Main.qml", note.read_text())

            scan_wiki(root, db)
            self.assertEqual(
                [item["category"] for item in broken_links(db, category="local_absolute_path")],
                [],
            )

    def test_patch_bundle_apply_rejects_stale_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            note = root / "index.md"
            note.write_text(
                "# Home\n\n[Main](/Users/kogaryu/dev/RD_UI/qml/Main.qml)\n",
            )
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_devref_patch_bundle(db)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))
            note.write_text("# Home\n\n[Main](dev://RD_UI/qml/Main.qml)\n")

            with self.assertRaises(ValueError):
                apply_patch_bundle(
                    bundle_path,
                    wiki_root=root,
                    backup_dir=Path(tmp) / "backups",
                )

    def test_patch_bundle_apply_replaces_target_not_matching_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            old = "/Users/kogaryu/dev/rudedude/docs/spec.md"
            note = root / "index.md"
            note.write_text(f"# Home\n\n[{old}]({old})\n")
            db = Path(tmp) / "catalog.sqlite"
            scan_wiki(root, db)
            bundle = build_devref_patch_bundle(db)
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(bundle))

            apply_patch_bundle(
                bundle_path,
                wiki_root=root,
                backup_dir=Path(tmp) / "backups",
            )
            self.assertEqual(
                note.read_text(),
                f"# Home\n\n[{old}](dev://rudedude/docs/spec.md)\n",
            )


if __name__ == "__main__":
    unittest.main()
