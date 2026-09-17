"""Intent-journal strictness, consistency, and pure recovery decisions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tinjis.errors import JournalError, OwnershipError
from tinjis.journal import (
    ABSENT,
    CREATE,
    JOURNAL_RELATIVE,
    LINK,
    OTHER,
    RETIRE,
    JournalEntry,
    JournalTransaction,
    dumps_journal,
    journal_path,
    parse_journal,
    read_journal,
    recovery_action,
    validate_transaction,
)
from tinjis.ownership import Boundary

BOUNDARY = Boundary(
    exact=((".config", "example", "config.toml"),),
    prefix=(".local", "share", "example"),
)


class JournalTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.home.mkdir()
        self.dest = str(self.home / ".config" / "example" / "config.toml")
        self.leaf = str(self.home / ".local" / "share" / "example" / "leaf")
        self.target = str(Path(self.temp.name).resolve() / "source")

    def transaction(self, *, before=None, after=None, entries=()) -> JournalTransaction:
        return JournalTransaction(
            before={} if before is None else before,
            after={} if after is None else after,
            entries=tuple(entries),
        )

    def create_tx(self):
        return self.transaction(
            after={self.dest: self.target},
            entries=(JournalEntry(self.dest, CREATE, self.target),),
        )

    def retire_tx(self):
        return self.transaction(
            before={self.dest: self.target},
            after={},
            entries=(JournalEntry(self.dest, RETIRE, self.target),),
        )


class PathTest(JournalTestCase):
    def test_journal_location_is_fixed_next_to_the_ownership_record(self):
        self.assertEqual(JOURNAL_RELATIVE, (".config", "tinjis", "journal.json"))
        self.assertEqual(journal_path(self.home), self.home / ".config/tinjis/journal.json")


class RoundTripTest(JournalTestCase):
    def test_dumps_then_parses_unchanged(self):
        transaction = self.create_tx()
        text = dumps_journal(transaction)
        self.assertEqual(
            parse_journal(json.loads(text), home=self.home, boundary=BOUNDARY), transaction
        )

    def test_encoding_is_deterministic_and_key_sorted(self):
        self.assertEqual(dumps_journal(self.create_tx()), dumps_journal(self.create_tx()))
        text = dumps_journal(self.create_tx())
        self.assertLess(text.index('"after"'), text.index('"before"'))

    def test_validate_accepts_a_well_formed_create_transaction(self):
        validate_transaction(self.create_tx(), home=self.home, boundary=BOUNDARY)


class StrictnessTest(JournalTestCase):
    def write(self, payload) -> None:
        path = journal_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_journal_returns_none_and_creates_nothing(self):
        self.assertIsNone(read_journal(self.home, BOUNDARY))
        self.assertFalse((self.home / ".config").exists())

    def test_duplicate_json_key_is_refused(self):
        path = journal_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"owner":"tinjis","owner":"tinjis","schema":1}', encoding="utf-8")
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_unknown_top_level_key_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        payload["extra"] = 1
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_wrong_owner_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        payload["owner"] = "someone-else"
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_boolean_and_wrong_schemas_are_refused(self):
        for schema in (True, 0, 2, 1.0):
            with self.subTest(schema=schema):
                payload = json.loads(dumps_journal(self.create_tx()))
                payload["schema"] = schema
                self.write(payload)
                with self.assertRaises(JournalError):
                    read_journal(self.home, BOUNDARY)

    def test_destination_outside_the_boundary_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        outside = str(self.home / ".ssh" / "authorized_keys")
        payload["transaction"]["after"] = {outside: self.target}
        payload["transaction"]["entries"][0]["dest"] = outside
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_relative_destination_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        payload["transaction"]["entries"][0]["dest"] = "relative/path"
        payload["transaction"]["after"] = {"relative/path": self.target}
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_reserved_bookkeeping_destination_is_refused(self):
        reserved = str(self.home / ".config" / "tinjis" / "evil")
        transaction = self.transaction(
            after={reserved: self.target},
            entries=(JournalEntry(reserved, CREATE, self.target),),
        )
        with self.assertRaises(OwnershipError):
            validate_transaction(transaction, home=self.home, boundary=BOUNDARY)
        self.write(
            {
                "owner": "tinjis",
                "schema": 1,
                "transaction": {
                    "before": {},
                    "after": {reserved: self.target},
                    "entries": [{"dest": reserved, "action": CREATE, "target": self.target}],
                },
            }
        )
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_unknown_action_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        payload["transaction"]["entries"][0]["action"] = "replace"
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_retirement_is_refused_before_any_mutation(self):
        transaction = self.retire_tx()
        with self.assertRaises(JournalError) as caught:
            validate_transaction(transaction, home=self.home, boundary=BOUNDARY)
        self.assertIn("retire", str(caught.exception))
        self.assertFalse(journal_path(self.home).exists())

    def test_retirement_payload_is_refused(self):
        payload = {
            "owner": "tinjis",
            "schema": 1,
            "transaction": {
                "before": {self.dest: self.target},
                "after": {},
                "entries": [{"dest": self.dest, "action": RETIRE, "target": self.target}],
            },
        }
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_create_of_an_already_owned_destination_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        payload["transaction"]["before"] = {self.dest: self.target}
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_inconsistent_after_is_refused(self):
        payload = json.loads(dumps_journal(self.create_tx()))
        payload["transaction"]["after"] = {}
        self.write(payload)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_duplicate_folded_entries_are_refused(self):
        upper = str(self.home / ".local" / "share" / "example" / "Leaf")
        two = self.transaction(
            after={self.leaf: self.target},
            entries=(
                JournalEntry(self.leaf, CREATE, self.target),
                JournalEntry(upper, CREATE, self.target),
            ),
        )
        with self.assertRaises(JournalError):
            validate_transaction(two, home=self.home, boundary=BOUNDARY)

    def test_symlinked_journal_is_refused(self):
        other = self.home / "other.json"
        other.write_text("{}", encoding="utf-8")
        path = journal_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(other)
        with self.assertRaises(JournalError):
            read_journal(self.home, BOUNDARY)

    def test_symlinked_ancestor_is_refused_before_the_read(self):
        real = Path(self.temp.name).resolve() / "real-config"
        real.mkdir()
        (self.home / ".config").symlink_to(real)
        with self.assertRaises(JournalError) as caught:
            read_journal(self.home, BOUNDARY)
        self.assertIn("symlinked ancestor", str(caught.exception))


class RecoveryDecisionTest(JournalTestCase):
    def test_create_is_final_when_absent_or_exact_target(self):
        entry = JournalEntry(self.dest, CREATE, self.target)
        self.assertEqual(recovery_action(entry, ABSENT, None), "create")
        self.assertEqual(recovery_action(entry, LINK, self.target), "ok")

    def test_create_refuses_any_other_observed_state(self):
        entry = JournalEntry(self.dest, CREATE, self.target)
        for observed, current in ((LINK, "/other"), (OTHER, None)):
            with self.subTest(observed=observed), self.assertRaises(JournalError):
                recovery_action(entry, observed, current)

    def test_retirement_is_not_recoverable(self):
        entry = JournalEntry(self.dest, RETIRE, self.target)
        with self.assertRaises(JournalError):
            recovery_action(entry, ABSENT, None)


if __name__ == "__main__":
    unittest.main()
