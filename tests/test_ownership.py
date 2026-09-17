"""Ownership record strictness and boundary tests (read-only)."""

from __future__ import annotations

import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tinjis.errors import OwnershipError
from tinjis.ownership import (
    OWNED_RELATIVE,
    OWNER,
    OWNERSHIP_SCHEMA,
    Boundary,
    owned_path,
    read_owned,
    validate_owned_destination,
    validate_owned_target,
    within_boundary,
)

BOUNDARY = Boundary(
    exact=((".config", "example", "config.toml"),),
    prefix=(".local", "share", "example"),
)


class BoundaryTest(unittest.TestCase):
    def test_exact_destination_is_inside(self):
        self.assertTrue(within_boundary((".config", "example", "config.toml"), BOUNDARY))

    def test_one_direct_child_of_the_prefix_is_inside(self):
        self.assertTrue(within_boundary((".local", "share", "example", "leaf"), BOUNDARY))

    def test_deeper_path_below_the_prefix_is_outside(self):
        self.assertFalse(
            within_boundary((".local", "share", "example", "leaf", "deeper"), BOUNDARY)
        )

    def test_prefix_itself_is_outside(self):
        self.assertFalse(within_boundary((".local", "share", "example"), BOUNDARY))

    def test_case_variant_of_an_exact_destination_is_outside(self):
        self.assertFalse(within_boundary((".config", "EXAMPLE", "config.toml"), BOUNDARY))

    def test_unrelated_path_is_outside(self):
        self.assertFalse(within_boundary((".ssh", "authorized_keys"), BOUNDARY))


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))

    def test_absolute_normalized_owned_path_is_accepted(self):
        validate_owned_destination(
            str(self.home / ".config" / "example" / "config.toml"), self.home, BOUNDARY
        )

    def test_relative_destination_is_refused(self):
        with self.assertRaises(OwnershipError):
            validate_owned_destination(".config/example/config.toml", self.home, BOUNDARY)

    def test_escaping_destination_is_refused(self):
        with self.assertRaises(OwnershipError):
            validate_owned_destination("/etc/passwd", self.home, BOUNDARY)

    def test_unnormalized_destination_is_refused(self):
        with self.assertRaises(OwnershipError):
            validate_owned_destination(
                f"{self.home}/.config/example/../example/config.toml", self.home, BOUNDARY
            )

    def test_non_nfc_destination_is_refused(self):
        decomposed = unicodedata.normalize("NFD", "caf\u00e9")
        boundary = Boundary(exact=(), prefix=(".local", "share", "example"))
        with self.assertRaises(OwnershipError) as caught:
            validate_owned_destination(
                str(self.home / ".local" / "share" / "example" / decomposed),
                self.home,
                boundary,
            )
        self.assertIn("NFC", str(caught.exception))

    def test_out_of_boundary_destination_is_refused(self):
        with self.assertRaises(OwnershipError) as caught:
            validate_owned_destination(f"{self.home}/.ssh/authorized_keys", self.home, BOUNDARY)
        self.assertIn("outside the declared boundary", str(caught.exception))

    def test_control_character_target_is_refused(self):
        with self.assertRaises(OwnershipError):
            validate_owned_target("/tmp/target\n/etc/passwd")

    def test_empty_target_is_refused(self):
        with self.assertRaises(OwnershipError):
            validate_owned_target("")

    def test_relative_target_is_accepted(self):
        validate_owned_target("../relative/target")


class ReadWriteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.home.mkdir()
        self.record = owned_path(self.home)

    def test_record_location_is_fixed_and_under_config(self):
        self.assertEqual(OWNED_RELATIVE, (".config", "tinjis", "owned.json"))
        self.assertEqual(self.record, self.home / ".config" / "tinjis" / "owned.json")

    def test_missing_record_means_no_owned_leaves(self):
        self.assertEqual(read_owned(self.record, self.home, BOUNDARY), {})

    def test_read_does_not_create_anything(self):
        read_owned(self.record, self.home, BOUNDARY)
        self.assertFalse(self.record.parent.exists())

    def test_module_exposes_no_writer(self):
        import tinjis.ownership as own

        self.assertFalse(hasattr(own, "write_owned"))

    def test_a_well_formed_record_is_returned_unchanged(self):
        dest = str(self.home / ".config" / "example" / "config.toml")
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps(
                {"owner": OWNER, "schema": OWNERSHIP_SCHEMA, "owned": {dest: "/some/source"}}
            ),
            encoding="utf-8",
        )
        self.assertEqual(read_owned(self.record, self.home, BOUNDARY), {dest: "/some/source"})

    def test_a_case_shouted_record_entry_is_outside_the_boundary(self):
        self.record.parent.mkdir(parents=True)
        shouted = str(self.home / ".config" / "EXAMPLE" / "config.toml")
        self.record.write_text(
            json.dumps({"owner": OWNER, "schema": OWNERSHIP_SCHEMA, "owned": {shouted: "/x"}}),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError) as caught:
            read_owned(self.record, self.home, BOUNDARY)
        self.assertIn("outside the declared boundary", str(caught.exception))

    def test_symlinked_record_is_refused(self):
        other = self.home / "other.json"
        other.write_text("{}", encoding="utf-8")
        self.record.parent.mkdir(parents=True)
        self.record.symlink_to(other)
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_unknown_top_level_key_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps({"owner": OWNER, "schema": OWNERSHIP_SCHEMA, "owned": {}, "extra": 1}),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError) as caught:
            read_owned(self.record, self.home, BOUNDARY)
        self.assertIn("unknown top-level keys", str(caught.exception))

    def test_wrong_owner_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps({"owner": "someone-else", "schema": OWNERSHIP_SCHEMA, "owned": {}}),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_wrong_schema_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps({"owner": OWNER, "schema": OWNERSHIP_SCHEMA + 1, "owned": {}}),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_boolean_and_float_schemas_are_refused(self):
        self.record.parent.mkdir(parents=True)
        for schema in (True, 1.0):
            with self.subTest(schema=schema):
                self.record.write_text(
                    json.dumps({"owner": OWNER, "schema": schema, "owned": {}}),
                    encoding="utf-8",
                )
                with self.assertRaises(OwnershipError):
                    read_owned(self.record, self.home, BOUNDARY)

    def test_missing_owned_map_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps({"owner": OWNER, "schema": OWNERSHIP_SCHEMA}), encoding="utf-8"
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_non_string_entry_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps({"owner": OWNER, "schema": OWNERSHIP_SCHEMA, "owned": {"a": 1}}),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_duplicate_json_key_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            '{"owner": "tinjis", "owner": "tinjis", "schema": 1, "owned": {}}',
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_out_of_boundary_entry_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps(
                {
                    "owner": OWNER,
                    "schema": OWNERSHIP_SCHEMA,
                    "owned": {str(self.home / ".ssh" / "authorized_keys"): "/x"},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_escaping_entry_is_refused(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps(
                {"owner": OWNER, "schema": OWNERSHIP_SCHEMA, "owned": {"/etc/passwd": "/x"}}
            ),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError):
            read_owned(self.record, self.home, BOUNDARY)

    def test_case_aliases_under_selection_prefix_are_refused(self):
        lower = str(self.home / ".local" / "share" / "example" / "report")
        upper = str(self.home / ".local" / "share" / "example" / "Report")
        self.record.parent.mkdir(parents=True)
        self.record.write_text(
            json.dumps(
                {
                    "owner": OWNER,
                    "schema": OWNERSHIP_SCHEMA,
                    "owned": {lower: "/one", upper: "/two"},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(OwnershipError) as caught:
            read_owned(self.record, self.home, BOUNDARY)
        self.assertIn("aliases", str(caught.exception))

    def test_symlinked_ancestor_is_refused_before_the_read(self):
        real = Path(self.temp.name).resolve() / "real-config"
        real.mkdir()
        (self.home / ".config").symlink_to(real)
        with self.assertRaises(OwnershipError) as caught:
            read_owned(self.record, self.home, BOUNDARY)
        self.assertIn("symlinked ancestor", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
