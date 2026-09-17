"""Narrow ownership records, read-only, plus map and reservation validation.

This module never mutates the filesystem. The package's one mutation site is
:mod:`tinjis.writer`, which no command imports; the actual, fsynced ownership
write happens there. This module:

* reads and strictly parses the ownership record a previous writer left;
* validates a complete ownership map before :mod:`tinjis.writer` writes it;
* owns the fixed bookkeeping namespace constants and the predicate that keeps
  every manifest destination out of that namespace.

That matters even in a read-only tool. The planner uses the record to tell
"a symlink Tinjis itself created" apart from "a symlink that was already
there", so the record is an untrusted input and is validated as strictly as the
manifest:

* a missing record means "nothing is owned yet", not an error;
* the record must be a regular, non-symlink file at the fixed Tinjis-owned
  location, mode is not trusted and not modified;
* the payload must be exactly ``owner``/``schema``/``owned`` with no unknown
  keys and no duplicate JSON keys;
* every recorded destination must be absolute, normalised, inside the HOME the
  caller named, inside the boundary of the manifest being checked, and outside
  the reserved bookkeeping namespace;
* every recorded target must be a plausible symlink target.

``OWNED_RELATIVE`` is a constant rather than a manifest field on purpose: a
corrupted or hostile manifest must not be able to point Tinjis at a different
bookkeeping file. :data:`BOOKKEEPING_RELATIVE` (``.config/tinjis``) is reserved
as a whole so no declared projection or selected inventory leaf can ever claim
the ownership record, the journal, the lock, or a scratch name.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .errors import OwnershipError
from .paths import fold_key, has_unsafe_ancestor, is_nfc
from .strictjson import loads

# The whole Tinjis bookkeeping namespace, HOME-relative. Reserved against every
# manifest projection category and every selected inventory leaf.
BOOKKEEPING_RELATIVE = (".config", "tinjis")
BOOKKEEPING_LEAF_NAME = "tinjis"

# Fixed names inside the namespace.
OWNED_LEAF_NAME = "owned.json"
JOURNAL_LEAF_NAME = "journal.json"
LOCK_LEAF_NAME = "lock"

OWNED_RELATIVE = (*BOOKKEEPING_RELATIVE, OWNED_LEAF_NAME)
JOURNAL_RELATIVE = (*BOOKKEEPING_RELATIVE, JOURNAL_LEAF_NAME)
LOCK_RELATIVE = (*BOOKKEEPING_RELATIVE, LOCK_LEAF_NAME)

# A consumer-root leaf name that is reserved so no declared link can claim it.
OWNED_STATE_DIRNAME = ".tinjis-state"

OWNER = "tinjis"
OWNERSHIP_SCHEMA = 1

OWNERSHIP_KEYS = frozenset({"owner", "schema", "owned"})

_BOOKKEEPING_FOLDED = tuple(fold_key(part) for part in BOOKKEEPING_RELATIVE)


class Boundary(NamedTuple):
    """The exact destinations and the one-leaf prefix a manifest may own."""

    exact: tuple
    prefix: tuple


def owned_path(home: Path) -> Path:
    """The fixed, Tinjis-owned ownership record path."""
    return home.joinpath(*OWNED_RELATIVE)


def owned_document(owned: dict) -> dict:
    """The exact document shape the record writer serialises.

    Pure: it builds the mapping only. The actual, fsynced write happens in
    :mod:`tinjis.writer`, which is the package's one sanctioned mutation site.
    """
    return {"owner": OWNER, "schema": OWNERSHIP_SCHEMA, "owned": dict(owned)}


def bookkeeping_overlap(parts: tuple) -> bool:
    """Whether HOME-relative ``parts`` equals, sits inside, or contains the
    reserved bookkeeping directory ``.config/tinjis``.

    Comparison is folded component-wise, so a case or Unicode spelling that a
    default macOS filesystem would collapse onto the namespace is refused too.
    An empty path is not a bookkeeping path.
    """
    if not parts:
        return False
    folded = tuple(fold_key(part) for part in parts)
    prefix = _BOOKKEEPING_FOLDED
    inside = folded[: len(prefix)] == prefix
    ancestor = prefix[: len(folded)] == folded
    return inside or ancestor


def validate_owned_map(owned: object, home: Path, boundary: Boundary) -> None:
    """Validate a complete ownership map as it would be written or read.

    Every destination must be absolute, normalised, inside HOME, inside the
    declared boundary, and outside the reserved bookkeeping namespace; every
    target must be a plausible symlink target; and two destinations that a
    case-insensitive filesystem would collapse are refused as aliases of one
    name.
    """
    if not isinstance(owned, dict):
        raise OwnershipError("ownership map must be a mapping")
    folded: dict = {}
    for key, value in owned.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise OwnershipError("ownership map has a non-string entry")
        validate_owned_destination(key, home, boundary)
        validate_owned_target(value)
        alias = tuple(fold_key(part) for part in Path(key).parts)
        if alias in folded:
            raise OwnershipError(
                "ownership map has case/Unicode aliases for one filesystem "
                f"destination: {folded[alias]!r} and {key!r}"
            )
        folded[alias] = key


def within_boundary(parts: tuple, boundary: Boundary) -> bool:
    """Whether a HOME-relative destination is inside the declared boundary.

    Exact file destinations use exact comparison. Selection records may also
    name one direct child of the selection prefix because a removed selection
    leaf must remain reportable. ``read_owned`` rejects folded aliases within
    the record, and the planner rejects a recorded spelling that aliases a
    currently selected leaf, preventing contradictory plans on a
    case-insensitive or normalization-insensitive filesystem.
    """
    if parts in boundary.exact:
        return True
    # Selection leaves are one direct child of the declared selection
    # destination. No deeper or arbitrary path is inferred.
    return (
        len(parts) == len(boundary.prefix) + 1
        and parts[: len(boundary.prefix)] == boundary.prefix
        and parts[-1] not in ("", ".", "..")
    )


def validate_owned_destination(raw: object, home: Path, boundary: Boundary) -> None:
    """A recorded destination must be absolute, normalised, inside HOME, owned.

    The reserved bookkeeping namespace is refused independently of the boundary,
    so a hand-written transaction can never claim ``.config/tinjis`` even if a
    manifest bug were to declare it.
    """
    if not isinstance(raw, str) or not raw:
        raise OwnershipError("ownership record destination must be a non-empty string")
    path = Path(raw)
    if not path.is_absolute():
        raise OwnershipError(f"ownership record destination is not absolute: {raw!r}")
    if any(part in ("", ".", "..") for part in raw.split("/")[1:]):
        raise OwnershipError(f"ownership record destination is not normalized: {raw!r}")
    try:
        relative = path.relative_to(home)
    except ValueError:
        raise OwnershipError(f"ownership record destination escapes HOME: {raw!r}") from None
    if any(not is_nfc(part) for part in relative.parts):
        raise OwnershipError(
            f"ownership record destination is not Unicode-normalised (NFC): {raw!r}"
        )
    if bookkeeping_overlap(relative.parts):
        raise OwnershipError(
            f"ownership record destination is inside the reserved Tinjis "
            f"bookkeeping namespace: {raw!r}"
        )
    if not within_boundary(relative.parts, boundary):
        raise OwnershipError(
            f"ownership record destination is outside the declared boundary: {raw!r}"
        )


def validate_owned_target(raw: object) -> None:
    """A recorded target must be a plausible absolute or relative symlink target."""
    if not isinstance(raw, str) or not raw:
        raise OwnershipError("ownership record target must be a non-empty string")
    if "\x00" in raw or "\n" in raw:
        raise OwnershipError("ownership record target contains control characters")
    if Path(raw).is_absolute() and any(part in ("", ".", "..") for part in raw.split("/")[1:]):
        raise OwnershipError(f"ownership record target is not normalized: {raw!r}")


def parse_owned(
    text: str,
    *,
    where: str,
    error: type = OwnershipError,
    home: Path | None = None,
    boundary: Boundary | None = None,
) -> dict:
    """Strictly parse an ownership document that has already been read.

    Split out from :func:`read_owned` so the writer can read the record through
    an anchored, no-follow descriptor and still share one parser.
    """
    payload = loads(text, where=where, error=error)
    if not isinstance(payload, dict):
        raise error(f"ownership record is not a JSON object: {where}")
    unknown = sorted(set(payload) - OWNERSHIP_KEYS)
    if unknown:
        raise error(f"ownership record has unknown top-level keys {unknown}: {where}")
    schema = payload.get("schema")
    if payload.get("owner") != OWNER:
        raise error(f"unrecognized ownership record owner: {where}")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != OWNERSHIP_SCHEMA:
        raise error(f"unrecognized ownership record schema: {where}")
    owned = payload.get("owned")
    if not isinstance(owned, dict):
        raise error(f"ownership record has no owned map: {where}")
    if home is not None and boundary is not None:
        validate_owned_map(owned, home, boundary)
    else:
        for key, value in owned.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise error(f"ownership record has a non-string entry: {where}")
            validate_owned_target(value)
    return dict(owned)


def read_owned(record: Path, home: Path | None = None, boundary: Boundary | None = None) -> dict:
    """Read the narrow ownership record strictly.

    Missing means no owned leaves yet. Every recorded destination must lie
    inside HOME and inside the declared boundary, so a hand-edited or corrupt
    record can never claim an arbitrary path. A symlinked ancestor of the
    record, or a symlinked record, is refused before anything is read.
    """
    if home is not None and boundary is not None:
        ancestor = ancestor_conflict(record, home)
        if ancestor:
            raise OwnershipError(ancestor)
    if record.is_symlink():
        raise OwnershipError(f"ownership record must be a regular file: {record}")
    if not record.exists():
        return {}
    if not record.is_file():
        raise OwnershipError(f"ownership record must be a regular file: {record}")
    try:
        text = record.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OwnershipError(f"cannot read ownership record {record}: {exc}") from exc
    return parse_owned(text, where=f"ownership record {record}", home=home, boundary=boundary)


def ancestor_conflict(dest: Path, home: Path) -> str | None:
    """Refuse a symlinked or non-directory ancestor below HOME."""
    return has_unsafe_ancestor(dest, home)
