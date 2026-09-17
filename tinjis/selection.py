"""Authored selection inventories.

An inventory records which resource trees a runtime *sees*. Which runtime sees
which resource is a separate, explicit decision from where the resource lives,
so a runtime can be narrowed or widened without moving source trees.

Inventory shape
---------------
The file is a JSON object with exactly one ``skills`` key whose value is an
array of unique resource directory names under the declarative source root.
Names are always returned sorted, so the on-disk order is documentation only
and a reordered inventory can never reorder a reported selection.

Every name must be one normalised path component matching
:data:`tinjis.paths.SAFE_COMPONENT`: an ASCII alphanumeric first character
followed by alphanumerics, dot, underscore, or hyphen. That grammar admits
ordinary resource names while refusing absolute paths, separators,
``.``/``..``, leading dots, whitespace, non-ASCII look-alikes, and control
characters. A selected leaf must also be a real (non-symlink) directory
carrying a regular, non-symlink ``SKILL.md`` marker. A hand-edited inventory or
a poisoned source tree is therefore refused instead of silently widening,
redirecting, or reporting a phantom selection.

Duplicate entries are compared *folded*: ``Report`` and ``report`` are the same
selection on a case-insensitive filesystem, so they are refused as a duplicate
rather than accepted as two names that a filesystem would collapse into one.

``SKILL.md`` is the cross-tool Agent Skills marker, not a Tinjis invention;
Tinjis treats it as the one advertisement file a selected resource must carry.
"""

from __future__ import annotations

from pathlib import Path

from .errors import SelectionError
from .paths import SAFE_COMPONENT, fold_key
from .strictjson import loads

SELECTION_KEYS = frozenset({"skills"})
MARKER_NAME = "SKILL.md"


def _validate_marker(source_root: Path, entry: str) -> None:
    leaf = source_root / entry
    if leaf.is_symlink():
        raise SelectionError(f"selection entry must not be a symlinked directory: {entry!r}")
    if not leaf.is_dir():
        raise SelectionError(f"selection entry has no directory under the source root: {entry!r}")
    marker = leaf / MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise SelectionError(f"selection entry has no regular non-symlink {MARKER_NAME}: {entry!r}")


def read_selection(inventory: Path, source_root: Path) -> list:
    """Read one inventory and return its entry names sorted by name.

    A missing or symlinked inventory is refused: an absent selection must be an
    explicit empty array, never a silently skipped file. Each entry is refused
    unless it is one safe path component naming a real, non-symlink directory
    under ``source_root`` that carries a regular, non-symlink marker. Duplicate
    JSON object keys, unknown keys, and duplicate array entries (compared
    case-insensitively and Unicode-normalised) are refused, so a traversal
    payload, a name with no resource directory, or a poisoned tree cannot
    silently drop, redirect, or widen a selection.
    """
    if source_root.is_symlink():
        raise SelectionError(
            f"selection source root must be a real directory, not a symlink: {source_root}"
        )
    if inventory.is_symlink():
        raise SelectionError(
            f"selection inventory must be a regular file, not a symlink: {inventory}"
        )
    if not inventory.is_file():
        raise SelectionError(f"selection inventory is missing: {inventory}")
    try:
        text = inventory.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SelectionError(f"cannot read selection inventory {inventory}: {exc}") from exc
    payload = loads(text, where=f"selection inventory {inventory}", error=SelectionError)
    if not isinstance(payload, dict):
        raise SelectionError(f"selection inventory is not a JSON object: {inventory}")
    unknown = sorted(set(payload) - SELECTION_KEYS)
    if unknown:
        raise SelectionError(f"selection inventory has unknown keys {unknown}: {inventory}")
    declared = payload.get("skills")
    if not isinstance(declared, list):
        raise SelectionError(f"selection inventory has no skills array: {inventory}")
    if not source_root.is_dir():
        raise SelectionError(f"selection source root is missing: {source_root}")
    names: list = []
    seen: dict = {}
    for index, entry in enumerate(declared):
        where = f"{inventory}.skills[{index}]"
        if not isinstance(entry, str) or not entry:
            raise SelectionError(f"{where} must be a non-empty string: {entry!r}")
        if not SAFE_COMPONENT.match(entry):
            raise SelectionError(f"{where} is not a normalized skill-name component: {entry!r}")
        key = fold_key(entry)
        if key in seen:
            raise SelectionError(
                f"{inventory} has a case/normalization-duplicate selection entry: "
                f"{entry!r} collides with {seen[key]!r}"
            )
        seen[key] = entry
        _validate_marker(source_root, entry)
        names.append(entry)
    return sorted(names)
