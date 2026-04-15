from pathlib import Path
import tomllib
import unittest

from wiki_tool.cli import build_parser


class PackagingTests(unittest.TestCase):
    def test_console_script_points_at_cli_main(self) -> None:
        pyproject = Path(__file__).parents[1] / "pyproject.toml"
        config = tomllib.loads(pyproject.read_text())

        self.assertEqual(config["project"]["scripts"]["wiki"], "wiki_tool.cli:main")
        self.assertEqual(config["tool"]["setuptools"]["packages"]["find"]["include"], ["wiki_tool*"])
        self.assertEqual(build_parser().prog, "wiki")


if __name__ == "__main__":
    unittest.main()
