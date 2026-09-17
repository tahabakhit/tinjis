"""Path grammar, containment, and collision-folding helpers.

Tinjis records two kinds of path in authored input, and they are validated
differently because they mean different things:

* a **source** path is relative to the checkout root and is read by Tinjis;
* a **destination** path is relative to a runtime root (the operator's HOME,
  the generic runtime root, or one consumer root) and names a path Tinjis may
  later be asked to manage.

Neither kind may be absolute, may traverse (``..``), may contain a backslash,
NUL, newline, or carriage return, or may be unnormalised (``.`` or empty
components). A destination component must additionally be Unicode-normalised
(NFC), so one path cannot be written two ways that a normalising filesystem
would later treat as the same name.

Collision checks never compare raw strings. :func:`fold_key` maps a component
to a case-folded, NFC-normalised key, and :func:`fold_path` does the same for a
whole relative path. Two declarations that differ only by letter case or by
Unicode normalisation therefore collide, which is what a default macOS
(APFS/HFS+, case-insensitive and normalisation-insensitive) filesystem would
do to them. Refusing is the safe direction: Tinjis would otherwise report a
plan for two distinct paths that the filesystem would collapse into one.

This module is deliberately free of any knowledge about which tool owns which
destination, and it never opens a file for writing. Ownership is decided in
:mod:`tinjis.ownership`.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# One safe path component, used for consumer names, inventory entries, legacy
# entry names, and marker file names. ASCII-only so the grammar cannot smuggle
# a separator, control character, or Unicode look-alike past the check, and so
# the component is already NFC-normalised.
SAFE_COMPONENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# A consumer/profile name becomes one directory under the runtime root.
PROFILE_NAME = re.compile(r"\A[a-z][a-z0-9_-]*\Z")

_FORBIDDEN = ("\\", "\x00", "\n", "\r")


def fold_key(value: str) -> str:
    """A case- and normalisation-insensitive key for one path component.

    ``casefold`` handles the Unicode case-mapping rules (including the
    non-ASCII ones ``lower`` would miss), and NFC collapses the composed and
    decomposed spellings of a combining sequence. The result is a comparison
    key only: it is never used as a path and never written anywhere.
    """
    return unicodedata.normalize("NFC", value).casefold()


def fold_path(value: str) -> tuple:
    """A folded key for a whole ``/``-separated relative path."""
    return tuple(fold_key(part) for part in value.split("/"))


def is_nfc(value: str) -> bool:
    """Whether a string is already in Unicode Normalization Form C."""
    return unicodedata.normalize("NFC", value) == value


def normalize_relative(
    value: object, *, where: str, kind: str, error: type, require_nfc: bool = False
) -> str:
    """Return a normalised relative path, or raise ``error``.

    ``kind`` is the human-readable path kind used in the message (for example
    ``"repository-relative"`` or ``"runtime-relative"``). ``require_nfc`` adds
    the destination-only rule that every component is already NFC-normalised.
    """
    if not isinstance(value, str) or not value:
        raise error(f"{where} must be a non-empty string")
    for bad in _FORBIDDEN:
        if bad in value:
            raise error(f"{where} contains an unsupported character: {value!r}")
    if value.startswith("/"):
        raise error(f"{where} must be {kind}, not absolute: {value!r}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise error(f"{where} is not a normalized {kind} path: {value!r}")
    if require_nfc:
        for part in parts:
            if not is_nfc(part):
                raise error(f"{where} is not Unicode-normalised (NFC): {part!r} in {value!r}")
    return value


def repo_relative(value: object, *, where: str, error: type) -> str:
    """A normalised repository-relative source path."""
    return normalize_relative(value, where=where, kind="repository-relative", error=error)


def runtime_relative(value: object, *, where: str, error: type) -> str:
    """A normalised destination path relative to a runtime root.

    Destinations must be NFC-normalised, unlike sources: a source is read by
    exact name, while a destination names a path that a normalising filesystem
    would silently rewrite.
    """
    return normalize_relative(
        value, where=where, kind="runtime-relative", error=error, require_nfc=True
    )


def safe_component(value: object, *, where: str, error: type) -> str:
    """One safe single path component (no separators, no leading dot)."""
    if not isinstance(value, str) or not value:
        raise error(f"{where} must be a non-empty string")
    if not SAFE_COMPONENT.match(value):
        raise error(f"{where} is not a safe path component: {value!r}")
    return value


def profile_name(value: object, *, where: str, error: type) -> str:
    """One safe consumer/profile name, which also becomes a directory name."""
    if not isinstance(value, str) or not value:
        raise error(f"{where} must be a non-empty string")
    if not PROFILE_NAME.match(value):
        raise error(f"{where} is not a safe profile name: {value!r}")
    return value


def is_within(path: Path, root: Path) -> bool:
    """Whether ``path`` equals ``root`` or is a descendant of it (lexically)."""
    return path == root or root in path.parents


def resolves_within(root: Path, relative: str) -> Path:
    """Resolve ``root / relative`` and fail closed if it leaves ``root``.

    ``Path.resolve`` follows symlinks, so a symlinked leaf *or* a symlinked
    ancestor that points outside the root is refused. A path that does not
    exist yet is still resolved lexically, which means a dangling symlink to an
    outside location is refused here rather than surviving until later.

    The caller must check containment separately; this helper returns the
    resolved path so the caller can report the escape with both paths.
    """
    canonical = root.resolve()
    return (canonical / relative).resolve(strict=False)


def escapes_checkout(root: Path, relative: str) -> Path | None:
    """Return the escaping resolved path, or ``None`` when it stays inside."""
    resolved = resolves_within(root, relative)
    canonical = root.resolve()
    if is_within(resolved, canonical):
        return None
    return resolved


def has_unsafe_ancestor(dest: Path, home: Path) -> str | None:
    """Refuse a symlinked or non-directory ancestor of ``dest`` below ``home``.

    If any ancestor between HOME and the destination is a symlink, or exists as
    a non-directory, then the ``Path`` object no longer names what the
    ownership record says it names, so the plan is refused instead of followed.
    """
    try:
        relative = dest.relative_to(home)
    except ValueError:
        return f"destination escapes HOME: {dest}"
    current = home
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return f"refusing symlinked ancestor: {current}"
        if current.exists() and not current.is_dir():
            return f"ancestor is not a directory: {current}"
    return None


def read_only_regular_file(path: Path, *, where: str, error: type) -> None:
    """Require an existing regular, non-symlink file.

    A declared authored input must be exactly the artefact the manifest named:
    a symlink would let the reviewed name resolve to something else, so the
    symlink is refused rather than followed even when its target is inside the
    checkout.
    """
    if path.is_symlink():
        raise error(f"{where} must not be a symlink: {path}")
    if not path.exists():
        raise error(f"{where} is missing: {path}")
    if not path.is_file():
        raise error(f"{where} is not a regular file: {path}")


def read_only_real_directory(path: Path, *, where: str, error: type) -> None:
    """Require an existing real, non-symlink directory."""
    if path.is_symlink():
        raise error(f"{where} must not be a symlink: {path}")
    if not path.exists():
        raise error(f"{where} is missing: {path}")
    if not path.is_dir():
        raise error(f"{where} is not a directory: {path}")
