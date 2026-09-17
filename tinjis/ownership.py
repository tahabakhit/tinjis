"""Narrow ownership records, read only.

Tinjis v0 does not mutate the filesystem. There is no writer in this package at
all -- see ``docs/STATUS.md`` -- so this module does exactly one thing: read the
ownership record that a *previous* (not yet designed) writer would have left,
and refuse anything that is not inside the narrow boundary the current manifest
declares.

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
  caller named, and inside the boundary of the manifest being checked;
* every recorded target must be a plausible symlink target.

``OWNED_RELATIVE`` is a constant rather than a manifest field on purpose: a
corrupted or hostile manifest must not be able to point Tinjis at a different
bookkeeping file.

``OWNED_STATE_DIRNAME`` is reserved as a name so no declared link can ever
claim it. Nothing creates, reads, or writes that directory in this phase, and
no journal is claimed to exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .errors import OwnershipError
from .paths import fold_key, has_unsafe_ancestor
from .strictjson import loads

OWNED_RELATIVE = (".config", "tinjis", "owned.json")
OWNED_LEAF_NAME = "owned.json"
OWNED_STATE_DIRNAME = ".tinjis-state"

OWNER = "tinjis"
OWNERSHIP_SCHEMA = 1

OWNERSHIP_KEYS = frozenset({"owner", "schema", "owned"})


class Boundary(NamedTuple):
    """The exact destinations and the one-leaf prefix a manifest may own."""

    exact: tuple
    prefix: tuple


def owned_path(home: Path) -> Path:
    """The fixed, Tinjis-owned ownership record path."""
    return home.joinpath(*OWNED_RELATIVE)


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
    """A recorded destination must be absolute, normalised, inside HOME, owned."""
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
    payload = loads(text, where=f"ownership record {record}", error=OwnershipError)
    if not isinstance(payload, dict):
        raise OwnershipError(f"ownership record is not a JSON object: {record}")
    unknown = sorted(set(payload) - OWNERSHIP_KEYS)
    if unknown:
        raise OwnershipError(f"ownership record has unknown top-level keys {unknown}: {record}")
    schema = payload.get("schema")
    if payload.get("owner") != OWNER:
        raise OwnershipError(f"unrecognized ownership record owner: {record}")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != OWNERSHIP_SCHEMA:
        raise OwnershipError(f"unrecognized ownership record schema: {record}")
    owned = payload.get("owned")
    if not isinstance(owned, dict):
        raise OwnershipError(f"ownership record has no owned map: {record}")
    folded: dict[tuple, str] = {}
    for key, value in owned.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise OwnershipError(f"ownership record has a non-string entry: {record}")
        if home is not None and boundary is not None:
            validate_owned_destination(key, home, boundary)
        validate_owned_target(value)
        alias = tuple(fold_key(part) for part in Path(key).parts)
        if alias in folded:
            raise OwnershipError(
                "ownership record has case/Unicode aliases for one filesystem "
                f"destination: {folded[alias]!r} and {key!r}"
            )
        folded[alias] = key
    return dict(owned)


def ancestor_conflict(dest: Path, home: Path) -> str | None:
    """Refuse a symlinked or non-directory ancestor below HOME."""
    return has_unsafe_ancestor(dest, home)
