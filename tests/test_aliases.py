import json
from pathlib import Path
import tempfile
import unittest

from wiki_tool.aliases import AliasEntry, load_alias_entries, validate_alias_entries
from wiki_tool.catalog import alias_map_validation, find_references, list_aliases, open_path, scan_wiki


class AliasMapTests(unittest.TestCase):
    def test_load_alias_entries_normalizes_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "aliases": [
                            {
                                "alias": "Scanner App",
                                "target_path": "projects/demo/README.md",
                                "reason": "test",
                            }
                        ],
                    }
                )
            )
            aliases = load_alias_entries(path)
            self.assertEqual(aliases[0].normalized, "scannerapp")
            self.assertEqual(aliases[0].target_path, "projects/demo/README.md")

    def test_validation_rejects_missing_targets_and_title_conflicts(self) -> None:
        entries = [
            AliasEntry(
                alias="Retrieval",
                normalized="retrieval",
                target_path="projects/demo/README.md",
                reason="conflicts with title",
            ),
            AliasEntry(
                alias="Missing",
                normalized="missing",
                target_path="missing.md",
                reason="missing target",
            ),
        ]
        validation = validate_alias_entries(
            entries,
            known_paths={"concepts/retrieval.md", "projects/demo/README.md"},
            title_to_path={"retrieval": "concepts/retrieval.md"},
        )
        self.assertFalse(validation["valid"])
        self.assertEqual(len(validation["errors"]), 2)

    def test_scan_stores_aliases_and_resolves_refs_and_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wiki"
            root.mkdir()
            (root / "index.md").write_text("# Home\n\n[[Demo Hub]]\n[Demo](demo-hub)\n")
            (root / "projects" / "demo").mkdir(parents=True)
            (root / "projects" / "demo" / "README.md").write_text("# Demo Project\n")
            alias_map = Path(tmp) / "aliases.json"
            alias_map.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "aliases": [
                            {
                                "alias": "demo hub",
                                "target_path": "projects/demo/README.md",
                                "reason": "test alias",
                            }
                        ],
                    }
                )
            )
            db = Path(tmp) / "catalog.sqlite"
            result = scan_wiki(root, db, alias_map_path=alias_map)
            self.assertEqual(result.broken_link_count, 0)

            aliases = list_aliases(db)
            self.assertEqual(aliases[0]["alias"], "demo hub")

            refs = find_references(db, "demo hub")
            self.assertEqual(len(refs), 2)
            self.assertEqual({item["target_path"] for item in refs}, {"projects/demo/README.md"})

            opened = open_path(
                db,
                "demo hub",
                platform="mac",
                mac_root="/Volumes/wiki",
                windows_root="W:\\",
            )
            self.assertEqual(opened["relative_path"], "projects/demo/README.md")

            validation = alias_map_validation(db, alias_map_path=alias_map)
            self.assertTrue(validation["valid"])


if __name__ == "__main__":
    unittest.main()
