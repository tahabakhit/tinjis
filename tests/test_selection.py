"""Selection inventory strictness tests."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tinjis.errors import SelectionError
from tinjis.selection import read_selection


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "shared"
        self.source.mkdir()
        for name in ("beta", "alpha"):
            leaf = self.source / name
            leaf.mkdir()
            (leaf / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        self.inventory = self.root / "inventory.json"

    def write(self, payload: object) -> None:
        self.inventory.write_text(json.dumps(payload), encoding="utf-8")

    def test_valid_selection_is_returned_sorted(self):
        self.write({"skills": ["beta", "alpha"]})
        self.assertEqual(read_selection(self.inventory, self.source), ["alpha", "beta"])

    def test_reordered_inventory_does_not_reorder_the_result(self):
        self.write({"skills": ["alpha", "beta"]})
        first = read_selection(self.inventory, self.source)
        self.write({"skills": ["beta", "alpha"]})
        self.assertEqual(read_selection(self.inventory, self.source), first)

    def test_empty_selection_is_explicit_and_allowed(self):
        self.write({"skills": []})
        self.assertEqual(read_selection(self.inventory, self.source), [])

    def test_missing_inventory_is_refused(self):
        with self.assertRaises(SelectionError) as caught:
            read_selection(self.inventory, self.source)
        self.assertIn("missing", str(caught.exception))

    def test_symlinked_inventory_is_refused(self):
        real = self.root / "real.json"
        real.write_text('{"skills": []}', encoding="utf-8")
        self.inventory.symlink_to(real)
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_symlinked_source_root_is_refused(self):
        link = self.root / "linked"
        link.symlink_to(self.source)
        self.write({"skills": ["alpha"]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, link)

    def test_unknown_key_is_refused(self):
        self.write({"skills": ["alpha"], "extra": []})
        with self.assertRaises(SelectionError) as caught:
            read_selection(self.inventory, self.source)
        self.assertIn("unknown keys", str(caught.exception))

    def test_duplicate_entry_is_refused(self):
        self.write({"skills": ["alpha", "alpha"]})
        with self.assertRaises(SelectionError) as caught:
            read_selection(self.inventory, self.source)
        self.assertIn("duplicate", str(caught.exception))

    def test_case_duplicate_entry_is_refused(self):
        """`Alpha` and `alpha` are one directory on a case-insensitive filesystem."""
        self.write({"skills": ["alpha", "Alpha"]})
        with self.assertRaises(SelectionError) as caught:
            read_selection(self.inventory, self.source)
        self.assertIn("duplicate", str(caught.exception))

    def test_unicode_normalized_duplicate_entry_is_refused(self):
        # A non-ASCII name is refused by the ASCII name grammar, so the folded
        # check is exercised with a case variant instead. This test pins the
        # grammar rule that makes NFC/NFD duplicates unreachable here.
        self.write({"skills": ["\u00e9tape"]})
        with self.assertRaises(SelectionError) as caught:
            read_selection(self.inventory, self.source)
        self.assertIn("normalized skill-name component", str(caught.exception))

    def test_duplicate_json_key_is_refused(self):
        self.inventory.write_text('{"skills": [], "skills": []}', encoding="utf-8")
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_traversal_entry_is_refused(self):
        self.write({"skills": ["../escape"]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_separator_entry_is_refused(self):
        self.write({"skills": ["nested/alpha"]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_dot_entry_is_refused(self):
        self.write({"skills": [".."]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_entry_without_a_directory_is_refused(self):
        self.write({"skills": ["missing"]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_entry_without_a_marker_is_refused(self):
        (self.source / "alpha" / "SKILL.md").unlink()
        self.write({"skills": ["alpha"]})
        with self.assertRaises(SelectionError) as caught:
            read_selection(self.inventory, self.source)
        self.assertIn("SKILL.md", str(caught.exception))

    def test_symlinked_marker_is_refused(self):
        target = self.root / "marker.md"
        target.write_text("x\n", encoding="utf-8")
        marker = self.source / "alpha" / "SKILL.md"
        marker.unlink()
        marker.symlink_to(target)
        self.write({"skills": ["alpha"]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_symlinked_entry_directory_is_refused(self):
        shutil.rmtree(self.source / "alpha")
        (self.source / "alpha").symlink_to(self.source / "beta")
        self.write({"skills": ["alpha"]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_non_string_entry_is_refused(self):
        self.write({"skills": [1]})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_missing_skills_key_is_refused(self):
        self.write({})
        with self.assertRaises(SelectionError):
            read_selection(self.inventory, self.source)

    def test_real_example_inventory_is_valid(self):
        names = read_selection(
            CHECKOUT / "examples" / "project" / "inventory.json",
            CHECKOUT / "examples" / "project" / "shared",
        )
        self.assertEqual(names, ["example-checklist", "example-report"])


if __name__ == "__main__":
    unittest.main()
