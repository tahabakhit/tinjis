"""Hardened create-only writer tests.

Every test runs against temporary roots. No test touches the real HOME. The
writer is CLI-unreachable; a separate test asserts the import graph.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fixture_topology import example_payload, write_example_tree

from tinjis import writer
from tinjis.errors import JournalError, OwnershipError, RevalidationError, WriterError
from tinjis.journal import (
    CREATE,
    RETIRE,
    JournalEntry,
    JournalTransaction,
    dumps_journal,
    journal_path,
    read_journal,
)
from tinjis.manifest import MANIFEST_NAME, load_manifest
from tinjis.ownership import (
    JOURNAL_LEAF_NAME,
    LOCK_LEAF_NAME,
    OWNED_LEAF_NAME,
    owned_path,
    read_owned,
)
from tinjis.plan import ProjectionPlan, plan_projections, projection_boundary


class WriterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.checkout = self.root / "checkout"
        self.home = self.root / "home"
        self.checkout.mkdir()
        self.home.mkdir()
        write_example_tree(self.checkout)
        (self.checkout / MANIFEST_NAME).write_text(
            json.dumps(example_payload()) + "\n", encoding="utf-8"
        )
        self.manifest = load_manifest(self.checkout)
        self.boundary = projection_boundary(self.manifest)
        self.init_state(self.home)

    def init_state(self, home: Path) -> None:
        (home / ".config" / "tinjis").mkdir(parents=True)

    @property
    def pairs(self):
        return plan_projections(self.home, self.checkout, {}, self.manifest)

    def plans(self, home=None, owned=None):
        home = home or self.home
        return plan_projections(home, self.checkout, owned or {}, self.manifest)

    def create_transaction(self, home=None):
        return writer.plan_create_transaction({}, self.plans(home=home))

    def single_plan(self, action="create", dest=None, source=None):
        first = next(iter(plan_projections(self.home, self.checkout, {}, self.manifest)))
        return ProjectionPlan(
            "single",
            dest or first.dest,
            source or first.source,
            action,
        )

    def leave_journal(self, transaction) -> None:
        """Interrupt an apply after the journal is fsynced, leaving it in place."""
        with (
            mock.patch.object(writer, "_create_link_at", side_effect=WriterError("injected")),
            self.assertRaises(WriterError),
        ):
            writer.apply_transaction(self.home, self.boundary, transaction)

    def assert_no_scratch(self, home=None):
        home = home or self.home
        self.assertEqual(list(home.rglob("*tinjis-tmp-*")), [])

    def owned_now(self):
        return read_owned(owned_path(self.home), self.home, self.boundary)


class PlanTest(WriterTestCase):
    def test_fresh_home_plans_only_creates(self):
        transaction = self.create_transaction()
        self.assertEqual(transaction.before, {})
        self.assertEqual(len(transaction.entries), 3)
        self.assertEqual({entry.action for entry in transaction.entries}, {CREATE})

    def test_empty_plan_yields_an_empty_transaction(self):
        transaction = writer.plan_create_transaction({}, [])
        self.assertEqual(transaction.entries, ())

    def test_retirement_plan_is_refused_before_any_mutation(self):
        with self.assertRaises(WriterError) as caught:
            writer.plan_create_transaction({}, [self.single_plan("retire")])
        self.assertIn("retirement", str(caught.exception))
        self.assertFalse(journal_path(self.home).exists())

    def test_update_plan_is_refused(self):
        with self.assertRaises(WriterError) as caught:
            writer.plan_create_transaction({}, [self.single_plan("update")])
        self.assertIn("migration", str(caught.exception))

    def test_conflict_plan_is_refused(self):
        with self.assertRaises(WriterError):
            writer.plan_create_transaction({}, [self.single_plan("conflict")])

    def test_transaction_with_a_retire_entry_is_refused_before_locking(self):
        dest = str(self.home / ".local" / "share" / "tinjis-example" / "shared" / "gone")
        transaction = JournalTransaction(
            before={dest: "/target"},
            after={},
            entries=(JournalEntry(dest, RETIRE, "/target"),),
        )
        with self.assertRaises(JournalError):
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertFalse(journal_path(self.home).exists())
        self.assertFalse(owned_path(self.home).exists())


class ApplyCreateTest(WriterTestCase):
    def test_apply_creates_links_writes_ownership_and_clears_the_journal(self):
        transaction = self.create_transaction()
        writer.apply_transaction(self.home, self.boundary, transaction)
        for entry in transaction.entries:
            self.assertTrue(Path(entry.dest).is_symlink(), entry.dest)
            self.assertEqual(os.readlink(entry.dest), entry.target)
        self.assertEqual(self.owned_now(), transaction.after)
        self.assertFalse(journal_path(self.home).exists())
        self.assert_no_scratch()

    def test_second_plan_after_apply_is_all_unchanged(self):
        transaction = self.create_transaction()
        writer.apply_transaction(self.home, self.boundary, transaction)
        follow_up = writer.plan_create_transaction(
            self.owned_now(), self.plans(owned=self.owned_now())
        )
        self.assertEqual(follow_up.entries, ())

    def test_advisory_lock_file_is_created_and_never_unlinked(self):
        writer.apply_transaction(self.home, self.boundary, self.create_transaction())
        lock = self.home / ".config" / "tinjis" / LOCK_LEAF_NAME
        self.assertTrue(lock.is_file(), "the stable advisory lock must persist")
        self.assertFalse(lock.is_symlink())

    def test_empty_transaction_writes_nothing(self):
        writer.apply_transaction(self.home, self.boundary, JournalTransaction({}, {}, ()))
        self.assertFalse(journal_path(self.home).exists())
        self.assertFalse(owned_path(self.home).exists())

    def test_apply_refuses_a_pre_existing_exact_target_link(self):
        plan = self.single_plan()
        plan.dest.parent.mkdir(parents=True)
        plan.dest.symlink_to(str(plan.source))
        transaction = writer.plan_create_transaction({}, [plan])
        with self.assertRaises(RevalidationError):
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertEqual(os.readlink(plan.dest), str(plan.source))
        self.assertFalse(owned_path(self.home).exists())
        self.assertFalse(journal_path(self.home).exists())
        self.assertIsNone(writer.recover(self.home, self.boundary))

    def test_apply_refuses_a_pre_existing_real_file(self):
        plan = self.single_plan()
        plan.dest.parent.mkdir(parents=True)
        plan.dest.write_text("user data", encoding="utf-8")
        transaction = writer.plan_create_transaction({}, [plan])
        with self.assertRaises(RevalidationError):
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertEqual(plan.dest.read_text(encoding="utf-8"), "user data")

    def test_apply_refuses_a_reserved_bookkeeping_destination(self):
        reserved = str(self.home / ".config" / "tinjis" / "evil")
        transaction = JournalTransaction(
            before={},
            after={reserved: "/target"},
            entries=(JournalEntry(reserved, CREATE, "/target"),),
        )
        with self.assertRaises(OwnershipError):
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertFalse(journal_path(self.home).exists())

    def test_apply_refuses_a_symlinked_destination_ancestor(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.home / ".local").symlink_to(elsewhere)
        with self.assertRaises(WriterError):
            writer.apply_transaction(self.home, self.boundary, self.create_transaction())
        self.assertEqual([p for p in elsewhere.rglob("*") if p.is_symlink()], [])

    def test_apply_refuses_when_a_journal_already_exists(self):
        transaction = self.create_transaction()
        # A journal already on disk means an earlier transaction is in flight.
        # The writer must fail closed and leave that journal untouched so
        # recovery can still find it.
        journal = journal_path(self.home)
        journal.write_text(dumps_journal(transaction), encoding="utf-8")
        with self.assertRaises(WriterError) as caught:
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertIn("journal", str(caught.exception))
        self.assertTrue(journal.exists())
        self.assertEqual(journal.read_text(encoding="utf-8"), dumps_journal(transaction))
        self.assertFalse(owned_path(self.home).exists())
        self.assert_no_scratch()


class ScratchCleanupTest(WriterTestCase):
    def open_state(self):
        return os.open(
            self.home / ".config" / "tinjis",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )

    def test_scratch_is_removed_when_fsync_fails(self):
        state_fd = self.open_state()
        try:
            with (
                mock.patch.object(writer.os, "fsync", side_effect=OSError("injected fsync")),
                self.assertRaises(OSError),
            ):
                writer._write_state_file_at(state_fd, OWNED_LEAF_NAME, b"{}\n", create_only=False)
        finally:
            os.close(state_fd)
        self.assert_no_scratch()
        self.assertFalse(owned_path(self.home).exists())

    def test_scratch_is_removed_when_link_fails(self):
        state_fd = self.open_state()
        try:
            with (
                mock.patch.object(
                    writer.os, "link", side_effect=OSError(errno.EACCES, "injected link")
                ),
                self.assertRaises(OSError),
            ):
                writer._write_state_file_at(state_fd, JOURNAL_LEAF_NAME, b"{}\n", create_only=True)
        finally:
            os.close(state_fd)
        self.assert_no_scratch()
        self.assertFalse(journal_path(self.home).exists())

    def test_unexpected_open_error_fails_closed(self):
        home_fd = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with (
                mock.patch.object(
                    writer.os, "open", side_effect=OSError(errno.EACCES, "injected open")
                ),
                self.assertRaises(OSError),
            ):
                writer._open_child_dir(home_fd, "child", create=True, check_owner=False)
        finally:
            os.close(home_fd)
        self.assert_no_scratch()


class StateTrustTest(WriterTestCase):
    def test_first_use_without_a_state_directory_is_refused(self):
        bare = self.root / "bare"
        bare.mkdir()
        transaction = writer.plan_create_transaction({}, self.plans(home=bare))
        with self.assertRaises(WriterError) as caught:
            writer.apply_transaction(bare, self.boundary, transaction)
        self.assertIn("missing directory component", str(caught.exception))

    def test_group_writable_state_directory_is_refused(self):
        os.chmod(self.home / ".config" / "tinjis", 0o777)
        with self.assertRaises(WriterError) as caught:
            writer.apply_transaction(self.home, self.boundary, self.create_transaction())
        self.assertIn("group/other writable", str(caught.exception))

    def test_symlinked_state_directory_is_refused(self):
        state = self.home / ".config" / "tinjis"
        shutil.rmtree(state)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        state.symlink_to(elsewhere)
        with self.assertRaises(WriterError):
            writer.apply_transaction(self.home, self.boundary, self.create_transaction())

    def test_symlinked_home_is_refused(self):
        link = self.root / "link-home"
        link.symlink_to(self.home)
        with self.assertRaises(WriterError), writer._locked_state(link):
            self.fail("symlinked HOME must not open")

    def test_recovery_refuses_a_symlinked_state_directory(self):
        state = self.home / ".config" / "tinjis"
        shutil.rmtree(state)
        elsewhere = self.root / "elsewhere-recover"
        elsewhere.mkdir()
        state.symlink_to(elsewhere)
        with self.assertRaises(WriterError):
            writer.recover(self.home, self.boundary)

    def test_recovery_refuses_a_symlinked_home(self):
        link = self.root / "link-home-recover"
        link.symlink_to(self.home)
        with self.assertRaises(WriterError):
            writer.recover(link, self.boundary)

    def test_lock_contention_fails_closed(self):
        home_fd = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            state_fd = writer._open_dir_chain(
                home_fd, writer.STATE_RELATIVE, create=False, check_owner=True
            )
            try:
                first = writer._open_lock_fd(state_fd)
                try:
                    with self.assertRaises(WriterError) as caught:
                        writer._open_lock_fd(state_fd)
                    self.assertIn("lock", str(caught.exception))
                finally:
                    os.close(first)
            finally:
                os.close(state_fd)
        finally:
            os.close(home_fd)

    def test_fifo_journal_is_refused_without_blocking(self):
        fifo = journal_path(self.home)
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(WriterError) as caught:
            writer.recover(self.home, self.boundary)
        self.assertIn("not a regular file", str(caught.exception))

    def test_fifo_ownership_record_is_refused_without_blocking(self):
        self.leave_journal(self.create_transaction())
        fifo = owned_path(self.home)
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(WriterError) as caught:
            writer.recover(self.home, self.boundary)
        self.assertIn("not a regular file", str(caught.exception))

    def test_recovery_is_serialized_by_the_same_lock(self):
        home_fd = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            state_fd = writer._open_dir_chain(
                home_fd, writer.STATE_RELATIVE, create=False, check_owner=True
            )
            try:
                held = writer._open_lock_fd(state_fd)
                try:
                    with self.assertRaises(WriterError):
                        writer.recover(self.home, self.boundary)
                finally:
                    os.close(held)
            finally:
                os.close(state_fd)
        finally:
            os.close(home_fd)


class CrashRecoveryTest(WriterTestCase):
    def test_crash_after_journal_before_creation(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        self.assertTrue(journal_path(self.home).exists())
        self.assertFalse(owned_path(self.home).exists())
        for entry in transaction.entries:
            self.assertFalse(Path(entry.dest).is_symlink())
        writer.recover(self.home, self.boundary)
        for entry in transaction.entries:
            self.assertTrue(Path(entry.dest).is_symlink())
        self.assertEqual(self.owned_now(), transaction.after)
        self.assertFalse(journal_path(self.home).exists())
        self.assert_no_scratch()

    def test_crash_after_creation_before_ownership_write(self):
        transaction = self.create_transaction()
        real = writer._write_state_file_at

        def flaky(state_fd, name, data, *, create_only):
            if name == OWNED_LEAF_NAME:
                raise WriterError("injected crash")
            return real(state_fd, name, data, create_only=create_only)

        with (
            mock.patch.object(writer, "_write_state_file_at", side_effect=flaky),
            self.assertRaises(WriterError),
        ):
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertTrue(journal_path(self.home).exists())
        self.assertFalse(owned_path(self.home).exists())
        for entry in transaction.entries:
            self.assertTrue(Path(entry.dest).is_symlink())
        writer.recover(self.home, self.boundary)
        self.assertEqual(self.owned_now(), transaction.after)
        self.assertFalse(journal_path(self.home).exists())

    def test_crash_after_ownership_before_journal_clear(self):
        transaction = self.create_transaction()
        with (
            mock.patch.object(
                writer, "_remove_state_file_at", side_effect=WriterError("injected crash")
            ),
            self.assertRaises(WriterError),
        ):
            writer.apply_transaction(self.home, self.boundary, transaction)
        self.assertTrue(journal_path(self.home).exists())
        self.assertEqual(self.owned_now(), transaction.after)
        writer.recover(self.home, self.boundary)
        self.assertFalse(journal_path(self.home).exists())

    def test_recovery_is_idempotent(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        writer.recover(self.home, self.boundary)
        self.assertIsNone(writer.recover(self.home, self.boundary))
        self.assertEqual(self.owned_now(), transaction.after)

    def test_recovery_returns_none_without_a_state_directory(self):
        bare = self.root / "bare"
        bare.mkdir()
        self.assertIsNone(writer.recover(bare, self.boundary))

    def test_recovery_refuses_an_unrelated_ownership_record(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        first = transaction.entries[0].dest
        owned_path(self.home).write_text(
            json.dumps({"owner": "tinjis", "schema": 1, "owned": {first: "/something-else"}}),
            encoding="utf-8",
        )
        with self.assertRaises(JournalError) as caught:
            writer.recover(self.home, self.boundary)
        self.assertIn("does not match", str(caught.exception))
        self.assertTrue(journal_path(self.home).exists())

    def test_recovery_refuses_to_clear_a_changed_journal(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        real = writer._read_state_file_at
        reads = {"journal": 0}

        def fake(state_fd, name):
            data = real(state_fd, name)
            if name == JOURNAL_LEAF_NAME:
                reads["journal"] += 1
                if reads["journal"] >= 2:
                    return (data or b"") + b" "
            return data

        with (
            mock.patch.object(writer, "_read_state_file_at", side_effect=fake),
            self.assertRaises(JournalError) as caught,
        ):
            writer.recover(self.home, self.boundary)
        self.assertIn("changed during recovery", str(caught.exception))
        self.assertTrue(journal_path(self.home).exists())

    def test_recovery_refuses_a_conflicting_existing_destination(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        first = transaction.entries[0].dest
        Path(first).parent.mkdir(parents=True, exist_ok=True)
        Path(first).write_text("user data", encoding="utf-8")
        with self.assertRaises(RevalidationError):
            writer.recover(self.home, self.boundary)
        self.assertEqual(Path(first).read_text(encoding="utf-8"), "user data")
        self.assertTrue(journal_path(self.home).exists())

    def test_recovery_adopts_an_exact_target_link_because_the_journal_is_trusted(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        for entry in transaction.entries:
            Path(entry.dest).parent.mkdir(parents=True, exist_ok=True)
            Path(entry.dest).symlink_to(entry.target)
        writer.recover(self.home, self.boundary)
        self.assertEqual(self.owned_now(), transaction.after)
        self.assertFalse(journal_path(self.home).exists())


class JournalReadTest(WriterTestCase):
    def test_read_journal_returns_the_written_transaction(self):
        transaction = self.create_transaction()
        self.leave_journal(transaction)
        reread = read_journal(self.home, self.boundary)
        self.assertEqual(reread, transaction)


if __name__ == "__main__":
    unittest.main()
