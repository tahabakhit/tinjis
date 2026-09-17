"""Create-only, journaled, lock-serialized mutation foundation (not CLI-reachable).

This is the package's single sanctioned mutation site. No command imports it,
the topology lock is unchanged, and retirement is refused outright. It exists so
the journal and mutation invariants can be reviewed and tested before any route
can act on a real HOME.

Threat model
------------
The writer assumes an operator-controlled, same-UID environment and cooperating
Tinjis processes. Cooperating processes are serialized by one advisory lock.
The writer does **not** claim protection from arbitrary same-UID state tampering:
a process that can rewrite the ownership record, the journal, or HOME components
is outside this model. Anchored, no-follow descriptors and fail-if-present
creation narrow the race windows for cooperating callers; they are not a
defense against a hostile same-UID actor.

What the foundation does
------------------------
* **Refuses retirement before any mutation.** A transaction containing ``retire``
  (or a plan for a retire, update, or conflict) is rejected before the journal is
  written. A safe retire would have to journal every quarantine phase and prove a
  no-clobber restore/delete; that design is not implemented.
* **Locks first.** ``apply_transaction`` and ``recover`` acquire one stable,
  never-unlinked ``flock`` advisory lock on ``~/.config/tinjis/lock`` before any
  authoritative read, and hold it through cleanup. A competing process fails
  closed rather than interleaving.
* **Anchors traversal.** HOME is opened ``O_DIRECTORY|O_NOFOLLOW``; the state
  directory and every destination parent are walked component-by-component with
  descriptor-relative, no-follow opens. Missing destination directories are
  created with ``mkdirat`` and fsynced. A symlinked HOME, ancestor, or state
  directory is refused.
* **Creates without clobbering.** A leaf is created with descriptor-relative
  ``os.symlink``, which fails atomically with ``EEXIST``. A normal apply never
  adopts a pre-existing link, even one that already points at the exact target;
  only recovery, reading a trusted locked journal, may treat such a link as the
  recorded create's result.
* **Journals first, owns last.** Fsynced intent is written before the
  first link; the ownership record is rewritten last; the journal is cleared
  after re-checking it is unchanged.
* **Refuses first use.** The state directory must already exist as a real,
  operator-owned, non-group/other-writable directory. The writer never creates
  it, so there is no newly-created-directory durability ordering to get wrong.

Durability
----------
Recovery is deterministic and idempotent: a reader replays the journal against
observed state. The writer fsyncs the journal and ownership files and the
directories that hold them, and treats an unexpected ``fsync`` or ``open``
failure as fatal (fail closed). It does **not** claim full power-loss
durability: device caches are not forced. Directory ``fsync`` errors fail the
operation closed; a platform that refuses directory fsync is unsupported by
this foundation.
"""

from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
from contextlib import contextmanager, suppress
from pathlib import Path

from .errors import JournalError, RevalidationError, WriterError
from .journal import (
    ABSENT,
    CREATE,
    LINK,
    OTHER,
    JournalEntry,
    JournalTransaction,
    dumps_journal,
    parse_journal_text,
    recovery_action,
    validate_transaction,
)
from .ownership import (
    BOOKKEEPING_RELATIVE,
    JOURNAL_LEAF_NAME,
    LOCK_LEAF_NAME,
    OWNED_LEAF_NAME,
    Boundary,
    owned_document,
    parse_owned,
)
from .plan import ProjectionPlan
from .strictjson import dumps_stable

#: The fixed state directory; must be pre-initialized by the operator.
STATE_RELATIVE = BOOKKEEPING_RELATIVE

#: Scratch names are namespaced so a leftover can be recognised.
_TMP_INFIX = ".tinjis-tmp-"

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


# --------------------------------------------------------------------------
# State-directory trust and locking
# --------------------------------------------------------------------------


def _check_owned_dir(fd: int, what: str) -> None:
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise WriterError(f"{what} is not a directory")
    if st.st_uid != os.geteuid():
        raise WriterError(f"{what} is not owned by the current user")
    if st.st_mode & 0o022:
        raise WriterError(f"{what} is group/other writable")


def _check_owned_file(fd: int, what: str) -> None:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise WriterError(f"{what} is not a regular file")
    if st.st_uid != os.geteuid():
        raise WriterError(f"{what} is not owned by the current user")
    if st.st_mode & 0o077:
        raise WriterError(f"{what} is group/other accessible")


def _open_child_dir(parent_fd: int, name: str, *, create: bool, check_owner: bool) -> int:
    """Open one real subdirectory of ``parent_fd``, refusing symlinks."""
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise WriterError(f"missing directory component: {name}") from None
        with suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        try:
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise WriterError(f"refusing symlinked or non-directory component: {name}") from exc
        raise
    if check_owner:
        try:
            _check_owned_dir(fd, f"directory {name}")
        except BaseException:
            os.close(fd)
            raise
    return fd


def _open_dir_chain(base_fd: int, parts: tuple, *, create: bool, check_owner: bool = False) -> int:
    """Walk ``parts`` from ``base_fd``; return the final directory descriptor."""
    current = os.dup(base_fd)
    for part in parts:
        try:
            nxt = _open_child_dir(current, part, create=create, check_owner=check_owner)
        except BaseException:
            os.close(current)
            raise
        os.close(current)
        current = nxt
    return current


def _open_lock_fd(state_fd: int) -> int:
    """Open and exclusively lock the stable lock file, or fail closed."""
    lock_fd = os.open(
        LOCK_LEAF_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=state_fd
    )
    try:
        _check_owned_file(lock_fd, "lock file")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WriterError(
                "another Tinjis process holds the writer lock; retry after it finishes"
            ) from exc
    except BaseException:
        os.close(lock_fd)
        raise
    return lock_fd


@contextmanager
def _locked_state(home: Path):
    """Open HOME and the state directory and hold the advisory lock.

    Yields ``(home_fd, state_fd, home)``. The lock is acquired before any
    authoritative read and released only after the caller's block finishes.
    """
    try:
        home_fd = os.open(home, _DIR_FLAGS)
    except OSError as exc:
        raise WriterError(f"HOME must be a real directory, not a symlink: {home}") from exc
    try:
        _check_owned_dir(home_fd, f"HOME {home}")
        state_fd = _open_dir_chain(home_fd, STATE_RELATIVE, create=False, check_owner=True)
        try:
            lock_fd = _open_lock_fd(state_fd)
            try:
                yield home_fd, state_fd, home
            finally:
                os.close(lock_fd)
        finally:
            os.close(state_fd)
    finally:
        os.close(home_fd)


# --------------------------------------------------------------------------
# Anchored state files
# --------------------------------------------------------------------------


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _unlink_at(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return


def _read_state_file_at(state_fd: int, name: str) -> bytes | None:
    """Read a state file through the state descriptor, refusing symlinks."""
    try:
        # O_NONBLOCK prevents a malformed FIFO/device from hanging recovery
        # before fstat can reject it as non-regular.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=state_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise WriterError(f"state file is a symlink or not regular: {name}") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise WriterError(f"state file is not a regular file: {name}")
        chunks: list = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_state_file_at(state_fd: int, name: str, data: bytes, *, create_only: bool) -> None:
    """Atomically write and fsync a state file through the state descriptor.

    A scratch file is written and fsynced first; it is then hard-linked (for the
    journal, so a concurrent cooperating writer loses) or renamed (for the
    ownership record) into place, and the state directory is fsynced. The
    scratch file is cleaned up after handled Python exceptions. Process death
    can leave a recognisable orphan scratch file; no automatic orphan cleanup is
    claimed. An unexpected error fails the write closed.
    """
    scratch = f".{name}{_TMP_INFIX}{secrets.token_hex(8)}"
    fd = os.open(
        scratch,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=state_fd,
    )
    try:
        _write_all(fd, data)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _unlink_at(state_fd, scratch)
        raise
    os.close(fd)
    try:
        if create_only:
            try:
                os.link(scratch, name, src_dir_fd=state_fd, dst_dir_fd=state_fd)
            except FileExistsError as exc:
                raise WriterError(f"a journal appeared concurrently at {name}") from exc
        else:
            os.rename(scratch, name, src_dir_fd=state_fd, dst_dir_fd=state_fd)
        os.fsync(state_fd)
    finally:
        _unlink_at(state_fd, scratch)


def _remove_state_file_at(state_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=state_fd)
    except FileNotFoundError:
        return
    os.fsync(state_fd)


def _owned_bytes(owned: dict) -> bytes:
    return (dumps_stable(owned_document(owned)) + "\n").encode("utf-8")


def _read_owned_at(state_fd: int, home: Path, boundary: Boundary) -> dict:
    data = _read_state_file_at(state_fd, OWNED_LEAF_NAME)
    if data is None:
        return {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WriterError(f"ownership record is not valid UTF-8: {exc}") from exc
    return parse_owned(text, where=f"ownership record in {home}", home=home, boundary=boundary)


def _read_journal_at(state_fd: int, home: Path, boundary: Boundary):
    data = _read_state_file_at(state_fd, JOURNAL_LEAF_NAME)
    if data is None:
        return None, None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JournalError(f"journal is not valid UTF-8: {exc}") from exc
    transaction = parse_journal_text(text, home=home, boundary=boundary, where=f"journal in {home}")
    return transaction, data


# --------------------------------------------------------------------------
# Anchored destination mutation (create only)
# --------------------------------------------------------------------------


def _relative_parts(dest: str, home: Path) -> tuple:
    try:
        parts = Path(dest).relative_to(home).parts
    except ValueError:
        raise WriterError(f"destination escapes HOME: {dest!r}") from None
    if not parts:
        raise WriterError(f"destination must name a leaf below HOME: {dest!r}")
    return parts


def _assert_leaf_absent_at(home_fd: int, dest_parts: tuple) -> None:
    """Refuse a pre-existing create destination before publishing intent.

    This preflight prevents a failed normal apply from leaving a trusted journal
    that recovery could later mistake for evidence of a successful create. The
    final descriptor-relative ``os.symlink`` remains the authoritative
    fail-if-present check.
    """
    current = os.dup(home_fd)
    try:
        for part in dest_parts[:-1]:
            try:
                nxt = _open_child_dir(current, part, create=False, check_owner=False)
            except WriterError as exc:
                if str(exc).startswith("missing directory component:"):
                    return
                raise
            os.close(current)
            current = nxt
        try:
            os.stat(dest_parts[-1], dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise RevalidationError(
            f"refusing to adopt an existing destination: {'/'.join(dest_parts)}"
        )
    finally:
        os.close(current)


def _create_link_at(home_fd: int, dest_parts: tuple, target: str) -> str:
    """Create a leaf symlink with descriptor-relative, fail-if-present semantics.

    The parent chain is opened anchored to HOME and no-follow; the symlink is
    created with ``symlinkat``, which fails atomically if the leaf exists. A
    pre-existing link, even one already pointing at ``target``, is refused: a
    normal apply never adopts it.
    """
    parent_fd = _open_dir_chain(home_fd, dest_parts[:-1], create=True)
    try:
        try:
            os.symlink(target, dest_parts[-1], dir_fd=parent_fd)
        except FileExistsError as exc:
            raise RevalidationError(
                f"refusing to replace an existing destination: {'/'.join(dest_parts)}"
            ) from exc
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return "created"


def _recover_create_at(home_fd: int, dest_parts: tuple, target: str) -> str:
    """Replay a create for a trusted journal read under the writer lock.

    Unlike a normal apply, recovery may treat an exact-target existing link as
    the recorded create's result, because the journal is trusted and locked. Any
    other state is refused rather than clobbered. The decision itself is the
    pure :func:`tinjis.journal.recovery_action`.
    """
    parent_fd = _open_dir_chain(home_fd, dest_parts[:-1], create=True)
    leaf = dest_parts[-1]
    try:
        try:
            current = os.readlink(leaf, dir_fd=parent_fd)
            observed = LINK
        except FileNotFoundError:
            observed, current = ABSENT, None
        except OSError:
            observed, current = OTHER, None
        entry = JournalEntry(dest="/".join(dest_parts), action=CREATE, target=target)
        try:
            step = recovery_action(entry, observed, current)
        except JournalError as exc:
            raise RevalidationError(str(exc)) from exc
        if step == "ok":
            return "unchanged"
        os.symlink(target, leaf, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return "created"
    finally:
        os.close(parent_fd)


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------


def plan_create_transaction(owned: dict, plans) -> JournalTransaction:
    """Turn a read-only plan into a validated create-only transaction.

    ``create`` and ``unchanged`` plans become entries or nothing. A ``retire``,
    ``update``, or ``conflict`` plan is refused here, before any mutation, so a
    caller can never slip a retirement past the writer.
    """
    before = dict(owned)
    after = dict(owned)
    entries: list = []
    for plan in plans:
        if not isinstance(plan, ProjectionPlan):
            raise WriterError(f"not a projection plan: {plan!r}")
        dest = str(plan.dest)
        if plan.action == "create":
            if dest in after:
                raise WriterError(f"refusing to create an already-owned destination: {dest}")
            entries.append(JournalEntry(dest=dest, action=CREATE, target=str(plan.source)))
            after[dest] = str(plan.source)
        elif plan.action == "unchanged":
            continue
        elif plan.action == "retire":
            raise WriterError(
                "retirement is not implemented; refusing the transaction before any mutation"
            )
        elif plan.action == "update":
            raise WriterError(
                "ownership migration is not implemented; refusing the transaction "
                "before any mutation"
            )
        else:
            raise WriterError(
                f"cannot apply a {plan.action!r} plan for {dest}; conflicts are never applied"
            )
    return JournalTransaction(before=before, after=after, entries=tuple(entries))


def apply_transaction(home: Path, boundary: Boundary, transaction: JournalTransaction) -> list:
    """Journal first, then create each leaf, then record ownership.

    Order is the whole point: validate, lock, refuse a drifted ownership record,
    write the fsynced journal, create each link, re-check the ownership record,
    rewrite it to the recorded final map, and clear the journal. A crash at any
    point leaves the journal for :func:`recover`.
    """
    home = Path(os.path.abspath(home))
    validate_transaction(transaction, home=home, boundary=boundary)
    if not transaction.entries:
        return []
    with _locked_state(home) as (home_fd, state_fd, locked_home):
        if _read_owned_at(state_fd, locked_home, boundary) != transaction.before:
            raise WriterError("ownership record changed since the transaction was planned")
        # Refuse every occupied create leaf before intent is published. Without
        # this gate, a normal apply that correctly failed on an existing link
        # could leave a journal that recovery later treated as creation proof.
        for entry in transaction.entries:
            _assert_leaf_absent_at(home_fd, _relative_parts(entry.dest, locked_home))
        _write_state_file_at(
            state_fd,
            JOURNAL_LEAF_NAME,
            dumps_journal(transaction).encode("utf-8"),
            create_only=True,
        )
        results: list = []
        for entry in transaction.entries:
            results.append(
                _create_link_at(home_fd, _relative_parts(entry.dest, locked_home), entry.target)
            )
        if _read_owned_at(state_fd, locked_home, boundary) != transaction.before:
            raise WriterError("ownership record changed during the transaction; refusing to commit")
        _write_state_file_at(
            state_fd, OWNED_LEAF_NAME, _owned_bytes(transaction.after), create_only=False
        )
        _remove_state_file_at(state_fd, JOURNAL_LEAF_NAME)
        return results


def recover(home: Path, boundary: Boundary) -> list | None:
    """Finish an interrupted create transaction, or return ``None``.

    Recovery refuses unless the live ownership record matches the transaction's
    recorded ``before`` (nothing committed yet) or ``after`` (ownership already
    committed). It replays each create idempotently, refuses to clear a journal
    that changed under it, rewrites the final ownership map, and clears the
    journal. Running it twice is safe.
    """
    home = Path(os.path.abspath(home))
    state_path = home.joinpath(*STATE_RELATIVE)
    # A missing state directory means there is nothing to recover. Anything
    # else -- including a symlinked state directory -- is revalidated by
    # ``_locked_state`` rather than silently skipped.
    if state_path.is_symlink():
        raise WriterError(f"state directory must not be a symlink: {state_path}")
    if not state_path.exists():
        return None
    with _locked_state(home) as (home_fd, state_fd, locked_home):
        transaction, raw = _read_journal_at(state_fd, locked_home, boundary)
        if transaction is None:
            return None
        live = _read_owned_at(state_fd, locked_home, boundary)
        if live != transaction.before and live != transaction.after:
            raise JournalError(
                "journal does not match the live ownership record; refusing to recover"
            )
        results: list = []
        for entry in transaction.entries:
            results.append(
                _recover_create_at(home_fd, _relative_parts(entry.dest, locked_home), entry.target)
            )
        if _read_state_file_at(state_fd, JOURNAL_LEAF_NAME) != raw:
            raise JournalError("journal changed during recovery; refusing to clear it")
        _write_state_file_at(
            state_fd, OWNED_LEAF_NAME, _owned_bytes(transaction.after), create_only=False
        )
        _remove_state_file_at(state_fd, JOURNAL_LEAF_NAME)
        return results
