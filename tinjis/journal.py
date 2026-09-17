"""Fsynced intent journal: pure model, strict parsing, recovery decision.

The writer foundation records *what it intends to do* in one file before it
touches a single link, so a crash cannot leave a link the ownership record does
not know about. This module owns the journal's data model, its strict reader,
its deterministic encoder, and the pure per-entry recovery decision. It performs
no mutation: the fsynced write, the link creation, and the ownership-record
update all live in :mod:`tinjis.writer`, the package's one sanctioned mutation
site, which no command imports.

Why a journal at all
--------------------
The removed prototype wrote a symlink and then updated the ownership record in
two uncoordinated steps. A crash in between left an unrecorded link, and a
concurrent process could change the destination between the check and the write.
The journal closes both gaps: intent is fsynced before the first mutation, and
recovery is a pure function of the recorded transaction and the *observed*
filesystem state, so it is deterministic and idempotent.

Create only
-----------
This phase models exactly one action, ``create``: the destination must be absent
in ``before`` and becomes a symlink. Retirement is **removed**: a transaction
containing ``retire`` is refused here, before any mutation, because a safe
retirement would have to journal every quarantine phase and prove a no-clobber
restore/delete, and that design is not implemented. Target replacement
(``update``) is ownership migration and is refused by :mod:`tinjis.writer`.

Transaction shape
-----------------
``before`` is the complete ownership map the transaction starts from; ``after``
is the complete map it must end at; ``entries`` are the ordered creates. Both
maps are stored so recovery is self-contained: the writer refuses a journal
whose live ownership record matches neither, instead of assuming. The
transaction is only consistent when folding the entries onto ``before`` yields
``after`` exactly, which this module enforces.

The reserved bookkeeping namespace
----------------------------------
Every destination is validated through
:func:`tinjis.ownership.validate_owned_destination`, which refuses the whole
``.config/tinjis`` namespace independently of the manifest boundary. This module
does not rely on the manifest having reserved it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import JournalError, OwnershipError
from .ownership import (
    JOURNAL_RELATIVE,
    Boundary,
    ancestor_conflict,
    validate_owned_destination,
    validate_owned_map,
    validate_owned_target,
)
from .paths import fold_key
from .strictjson import dumps_stable, loads

JOURNAL_OWNER = "tinjis"
JOURNAL_SCHEMA = 1

JOURNAL_KEYS = frozenset({"owner", "schema", "transaction"})
TRANSACTION_KEYS = frozenset({"before", "after", "entries"})
ENTRY_KEYS = frozenset({"dest", "action", "target"})

CREATE = "create"
RETIRE = "retire"
ACTIONS = (CREATE,)

# The observed state of a destination, as recovery sees it.
ABSENT = "absent"
LINK = "link"
OTHER = "other"


@dataclass(frozen=True)
class JournalEntry:
    """One ordered create: ``dest`` becomes a symlink pointing at ``target``."""

    dest: str
    action: str
    target: str


@dataclass(frozen=True)
class JournalTransaction:
    """A complete, self-contained intent record.

    ``before`` and ``after`` are ownership maps; ``entries`` is the ordered list
    of creates that turns the former into the latter.
    """

    before: dict
    after: dict
    entries: tuple


def journal_path(home: Path) -> Path:
    """The fixed, Tinjis-owned journal path."""
    return home.joinpath(*JOURNAL_RELATIVE)


def _object(value: object, *, where: str) -> dict:
    if not isinstance(value, dict):
        raise JournalError(f"{where} must be a JSON object")
    return value


def _exact_keys(value: dict, allowed: frozenset, *, where: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise JournalError(f"{where} has unknown keys: {', '.join(unknown)}")
    missing = sorted(set(allowed) - set(value))
    if missing:
        raise JournalError(f"{where} is missing keys: {', '.join(missing)}")


def validate_transaction(
    transaction: object, *, home: Path, boundary: Boundary, where: str = "journal transaction"
) -> None:
    """Validate an in-memory transaction exactly as a read journal would be.

    ``before`` and ``after`` must be valid ownership maps inside ``home`` and
    ``boundary`` and outside the reserved namespace. Every entry must be a
    ``create`` of a destination absent from ``before``, no two entries may name
    one destination (folded), and folding the entries onto ``before`` must
    reproduce ``after`` exactly. A ``retire`` entry is refused here.
    """
    if not isinstance(transaction, JournalTransaction):
        raise JournalError(f"{where} is not a journal transaction")
    validate_owned_map(transaction.before, home, boundary)
    validate_owned_map(transaction.after, home, boundary)
    expected = dict(transaction.before)
    seen: dict = {}
    for index, entry in enumerate(transaction.entries):
        item = f"{where}.entries[{index}]"
        if not isinstance(entry, JournalEntry):
            raise JournalError(f"{item} is not a journal entry")
        if entry.action == RETIRE:
            raise JournalError(
                f"{item} requests retirement, which is not implemented; a transaction "
                "containing `retire` is refused before any mutation"
            )
        if entry.action not in ACTIONS:
            raise JournalError(f"{item}.action is not supported: {entry.action!r}")
        validate_owned_destination(entry.dest, home, boundary)
        validate_owned_target(entry.target)
        alias = fold_key(entry.dest)
        if alias in seen:
            raise JournalError(
                f"{where} names one destination twice (case/Unicode folded): "
                f"{entry.dest!r} and {seen[alias]!r}"
            )
        seen[alias] = entry.dest
        if entry.dest in expected:
            raise JournalError(f"{item} would create a destination already owned: {entry.dest!r}")
        expected[entry.dest] = entry.target
    if expected != transaction.after:
        raise JournalError(
            f"{where} is inconsistent: folding its entries onto `before` does not produce `after`"
        )


def _parse_entry(value: object, *, index: int) -> JournalEntry:
    where = f"journal transaction.entries[{index}]"
    obj = _object(value, where=where)
    _exact_keys(obj, ENTRY_KEYS, where=where)
    dest = obj["dest"]
    action = obj["action"]
    target = obj["target"]
    if not isinstance(dest, str) or not dest:
        raise JournalError(f"{where}.dest must be a non-empty string")
    if not isinstance(action, str):
        raise JournalError(f"{where}.action must be a string")
    if not isinstance(target, str) or not target:
        raise JournalError(f"{where}.target must be a non-empty string")
    return JournalEntry(dest=dest, action=action, target=target)


def parse_journal(
    payload: object, *, home: Path, boundary: Boundary, where: str = "journal"
) -> JournalTransaction:
    """Parse one already-decoded journal payload into a validated transaction.

    The reader is as strict as the ownership reader: exact key sets at every
    level, a known owner and schema, and every destination inside HOME, inside
    the declared boundary, and outside the reserved namespace. The payload is
    treated as hostile input.
    """
    obj = _object(payload, where=where)
    _exact_keys(obj, JOURNAL_KEYS, where=where)
    if obj.get("owner") != JOURNAL_OWNER:
        raise JournalError(f"{where} has an unrecognized owner")
    schema = obj.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != JOURNAL_SCHEMA:
        raise JournalError(f"{where} has an unrecognized schema")
    body = _object(obj.get("transaction"), where=f"{where}.transaction")
    _exact_keys(body, TRANSACTION_KEYS, where=f"{where}.transaction")
    before = body.get("before")
    after = body.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise JournalError(f"{where}.transaction must carry before and after maps")
    raw_entries = body.get("entries")
    if not isinstance(raw_entries, list):
        raise JournalError(f"{where}.transaction.entries must be a JSON array")
    entries = tuple(_parse_entry(value, index=index) for index, value in enumerate(raw_entries))
    transaction = JournalTransaction(before=dict(before), after=dict(after), entries=entries)
    try:
        validate_transaction(transaction, home=home, boundary=boundary, where=where)
    except OwnershipError as exc:
        # A boundary/ownership violation inside a journal is a malformed journal
        # from the reader's point of view; report it at the journal layer.
        raise JournalError(str(exc)) from exc
    return transaction


def transaction_to_payload(transaction: JournalTransaction) -> dict:
    """The authored JSON shape of a transaction."""
    return {
        "owner": JOURNAL_OWNER,
        "schema": JOURNAL_SCHEMA,
        "transaction": {
            "before": dict(transaction.before),
            "after": dict(transaction.after),
            "entries": [
                {"dest": entry.dest, "action": entry.action, "target": entry.target}
                for entry in transaction.entries
            ],
        },
    }


def dumps_journal(transaction: JournalTransaction) -> str:
    """Deterministic journal bytes plus a trailing newline."""
    return dumps_stable(transaction_to_payload(transaction)) + "\n"


def parse_journal_text(
    text: str, *, home: Path, boundary: Boundary, where: str = "journal"
) -> JournalTransaction:
    """Parse journal text read through any descriptor."""
    return parse_journal(
        loads(text, where=where, error=JournalError), home=home, boundary=boundary, where=where
    )


def read_journal(home: Path, boundary: Boundary) -> JournalTransaction | None:
    """Read the fixed journal strictly, or ``None`` when there is no journal.

    A missing journal means "no transaction in flight". A symlinked journal, a
    symlinked or non-directory ancestor, a non-regular file, malformed JSON,
    duplicate keys, or a payload outside the boundary or inside the reserved
    namespace are all refused before a single byte is interpreted.
    """
    path = journal_path(home)
    conflict = ancestor_conflict(path, home)
    if conflict:
        raise JournalError(conflict)
    if path.is_symlink():
        raise JournalError(f"journal must be a regular file, not a symlink: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise JournalError(f"journal must be a regular file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise JournalError(f"cannot read journal {path}: {exc}") from exc
    return parse_journal_text(text, home=home, boundary=boundary, where=f"journal {path}")


def recovery_action(entry: JournalEntry, observed: str, current: str | None) -> str:
    """The deterministic recovery step for one create entry, or a refusal.

    ``observed`` is :data:`ABSENT`, :data:`LINK`, or :data:`OTHER`; ``current``
    is the symlink target when ``observed`` is :data:`LINK`. The result is
    ``"create"``, ``"ok"``, or a refusal.

    An exact-target existing link is treated as final here **only because
    recovery reads a journal that is trusted and held under the writer's lock**.
    A normal apply never adopts such a link; the caller must use this decision
    only on the recovery path. A destination that exists as anything else is
    unrecoverable: recovery must not clobber it.
    """
    if entry.action != CREATE:
        raise JournalError(f"cannot recover {entry.action!r}; only create recovery is implemented")
    if observed == ABSENT:
        return "create"
    if observed == LINK and current == entry.target:
        return "ok"
    raise JournalError(
        f"cannot recover create for {entry.dest!r}: destination is {observed}"
        + (f" pointing at {current!r}" if observed == LINK else "")
    )
