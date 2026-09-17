"""Bookkeeping-namespace reservation and the CLI import graph."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fixture_topology import example_payload

from tinjis.errors import ManifestError, OwnershipError
from tinjis.manifest import parse_manifest
from tinjis.ownership import (
    BOOKKEEPING_RELATIVE,
    Boundary,
    bookkeeping_overlap,
    validate_owned_destination,
)


class PredicateTest(unittest.TestCase):
    def test_the_namespace_itself_and_descendants_overlap(self):
        self.assertTrue(bookkeeping_overlap((".config", "tinjis")))
        self.assertTrue(bookkeeping_overlap((".config", "tinjis", "owned.json")))
        self.assertTrue(bookkeeping_overlap((".config", "tinjis", "a", "b")))

    def test_case_and_unicode_spellings_overlap(self):
        self.assertTrue(bookkeeping_overlap((".config", "TINJIS")))
        self.assertTrue(bookkeeping_overlap((".CONFIG", "Tinjis", "owned.json")))

    def test_ancestors_of_the_namespace_overlap(self):
        self.assertTrue(bookkeeping_overlap((".config",)))

    def test_siblings_and_empty_paths_do_not_overlap(self):
        self.assertFalse(bookkeeping_overlap((".config", "tinjis-example")))
        self.assertFalse(bookkeeping_overlap((".config", "tinjis-example", "herdr")))
        self.assertFalse(bookkeeping_overlap((".local", "share", "example")))
        self.assertFalse(bookkeeping_overlap(()))


class ManifestReservationTest(unittest.TestCase):
    def parse(self, mutate):
        payload = example_payload()
        mutate(payload)
        return parse_manifest(payload)

    def assert_reserved(self, mutate):
        with self.assertRaises(ManifestError) as caught:
            self.parse(mutate)
        self.assertIn("bookkeeping", str(caught.exception))

    def test_file_projection_inside_the_namespace_is_refused(self):
        self.assert_reserved(
            lambda p: p["files"].append(
                {
                    "label": "evil",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config/tinjis/owned.json",
                }
            )
        )

    def test_file_projection_equal_to_the_namespace_is_refused(self):
        self.assert_reserved(
            lambda p: p["files"].append(
                {
                    "label": "evil",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config/tinjis",
                }
            )
        )

    def test_file_projection_containing_the_namespace_is_refused(self):
        self.assert_reserved(
            lambda p: p["files"].append(
                {
                    "label": "evil",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config",
                }
            )
        )

    def test_consumer_root_inside_the_namespace_is_refused(self):
        self.assert_reserved(lambda p: p["consumers"][0].update(root=".config/tinjis"))

    def test_selection_destination_inside_the_namespace_is_refused(self):
        self.assert_reserved(lambda p: p["selection"].update(destination=".config/tinjis/shared"))

    def test_case_spelled_namespace_is_refused(self):
        self.assert_reserved(
            lambda p: p["files"].append(
                {
                    "label": "evil",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config/TINJIS/owned.json",
                }
            )
        )

    def test_a_sibling_of_the_namespace_is_allowed(self):
        self.parse(
            lambda p: p["files"].append(
                {
                    "label": "fine",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config/tinjis-example/ok.sh",
                }
            )
        )


class IndependentOwnershipReservationTest(unittest.TestCase):
    def test_destination_is_refused_even_when_the_boundary_claims_it(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            # A boundary that (hypothetically) claims the reserved namespace.
            boundary = Boundary(
                exact=((".config", "tinjis", "owned.json"),),
                prefix=(".local", "share", "example"),
            )
            with self.assertRaises(OwnershipError) as caught:
                validate_owned_destination(
                    str(home / ".config" / "tinjis" / "owned.json"), home, boundary
                )
            self.assertIn("bookkeeping", str(caught.exception))

    def test_bookkeeping_relative_is_the_documented_namespace(self):
        self.assertEqual(BOOKKEEPING_RELATIVE, (".config", "tinjis"))


class CliImportGraphTest(unittest.TestCase):
    """No command path may import the journal or the writer."""

    def _modules_after(self, statement: str) -> list:
        code = (
            "import sys; sys.dont_write_bytecode = True; "
            f"sys.path.insert(0, {str(CHECKOUT)!r}); {statement}; "
            "print(sorted(m for m in ('tinjis.journal', 'tinjis.writer') if m in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(CHECKOUT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip().splitlines()[-1]

    def test_importing_the_cli_loads_neither_journal_nor_writer(self):
        self.assertEqual(self._modules_after("import tinjis.cli"), "[]")

    def test_importing_the_package_loads_neither_module(self):
        self.assertEqual(self._modules_after("import tinjis"), "[]")

    def test_validate_and_example_commands_load_neither_module(self):
        for command in ("validate", "example"):
            with self.subTest(command=command):
                statement = f"from tinjis.cli import main; main([{command!r}])"
                self.assertEqual(self._modules_after(statement), "[]")

    def test_check_command_loads_neither_module(self):
        with tempfile.TemporaryDirectory() as temp:
            statement = (
                "from tinjis.cli import main; "
                f"main(['check', '--home', {str(Path(temp).resolve())!r}])"
            )
            self.assertEqual(self._modules_after(statement), "[]")


if __name__ == "__main__":
    unittest.main()
