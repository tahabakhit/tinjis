"""Read-only projection planning.

There is no writer in Tinjis v0. This module computes what a future, journaled
writer *would* have to do, and refuses to guess about anything it cannot prove.
It opens no file for writing, creates no directory, and follows no symlink.

The plan answers these questions for every declared leaf:

* does the destination already exist, and if so is it a symlink Tinjis can prove
  it created (recorded in the ownership record with the exact same target)?
* is the authored source present?
* would the leaf be created, updated, left alone, or retired?
* is the leaf blocked by a conflict that a human must reconcile?

Cross-namespace collision checking deliberately lives in
:mod:`tinjis.manifest`, not here. Every declared leaf is validated at parse
time against a *folded* key (case-folded and NFC-normalised) and against the
selection destination, and no declared leaf may equal, sit inside, or contain
that destination. A selection leaf is by construction one direct child of that
destination, so no selection leaf can collide with a declared leaf or be an
ancestor of one. Re-checking the resolved paths here would duplicate a proof the
manifest already made; keeping one check is what keeps the guarantee auditable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import OwnershipError, SelectionError
from .manifest import Manifest
from .ownership import Boundary, ancestor_conflict, validate_owned_destination
from .paths import fold_key
from .selection import read_selection

ACTIONS = ("create", "update", "unchanged", "retire", "conflict")


@dataclass(frozen=True)
class ProjectionPlan:
    """One read-only decision for a manifest-owned runtime leaf.

    ``action`` is what a writer would do, never what this package has done. No
    code path in this package performs any of them.
    """

    label: str
    dest: Path
    source: Path
    action: str
    detail: str = ""

    @property
    def would_change(self) -> bool:
        """Whether a writer would have to touch the filesystem for this leaf."""
        return self.action in ("create", "update", "retire")


def projection_boundary(manifest: Manifest) -> Boundary:
    """The exact destinations and one-leaf prefix a manifest may own."""
    exact = tuple(tuple(Path(entry.destination).parts) for entry in manifest.files)
    prefix = tuple(Path(manifest.selection.destination).parts)
    return Boundary(exact=exact, prefix=prefix)


def selected_leaves(checkout: Path, manifest: Manifest) -> list:
    """The authored selected resource leaves, as ``(label, source)`` pairs.

    A missing, malformed, or unresolved inventory is refused rather than
    treated as an empty selection, so a renamed resource or a corrupted
    inventory can never be silently reported as an empty selection.
    """
    source_root = checkout / manifest.selection.source_root
    inventory = checkout / manifest.selection.inventory
    try:
        names = read_selection(inventory, source_root)
    except SelectionError:
        raise
    return [(f"selection/{name}", source_root / name) for name in names]


def projection_pairs(home: Path, checkout: Path, manifest: Manifest) -> list:
    """Every destination the manifest declares, with its authored source."""
    pairs = []
    selection_dest = home / manifest.selection.destination
    for label, source in selected_leaves(checkout, manifest):
        pairs.append((label, selection_dest / source.name, source))
    for entry in manifest.files:
        pairs.append((entry.label, home / entry.destination, checkout / entry.source))
    return pairs


def plan_projections(
    home: Path,
    checkout: Path,
    owned: dict,
    manifest: Manifest,
) -> list:
    """Plan every declared leaf without touching the filesystem.

    Ownership is never inferred: every preexisting destination must match the
    record exactly, even when it already points at the desired source. Record
    entries that are no longer declared are planned for retirement only inside
    the declared boundary, and only when the link still matches the recorded
    target; anything else is a conflict.

    ``owned`` is supplied by the caller rather than read here, so a missing or
    unreadable ownership record can never silently be treated as an empty one.
    """
    boundary = projection_boundary(manifest)
    plans: list = []
    declared: set = set()
    pairs = projection_pairs(home, checkout, manifest)
    declared_aliases = {
        tuple(fold_key(part) for part in dest.parts): str(dest) for _label, dest, _source in pairs
    }
    for raw in owned:
        alias = tuple(fold_key(part) for part in Path(raw).parts)
        declared_spelling = declared_aliases.get(alias)
        if declared_spelling is not None and raw != declared_spelling:
            raise OwnershipError(
                "ownership record destination aliases a declared destination by "
                f"case or Unicode spelling: {raw!r} aliases {declared_spelling!r}"
            )
    for label, dest, source in pairs:
        declared.add(str(dest))
        ancestor = ancestor_conflict(dest, home)
        if ancestor:
            plans.append(ProjectionPlan(label, dest, source, "conflict", ancestor))
            continue
        if not source.exists():
            plans.append(
                ProjectionPlan(label, dest, source, "conflict", f"missing source: {source}")
            )
            continue
        desired = str(source)
        recorded = owned.get(str(dest))
        if dest.is_symlink():
            current = os.readlink(dest)
            if recorded is None:
                plans.append(
                    ProjectionPlan(
                        label,
                        dest,
                        source,
                        "conflict",
                        "existing symlink is not Tinjis-owned "
                        f"(cutover/adoption required): {current}",
                    )
                )
            elif current != recorded:
                plans.append(
                    ProjectionPlan(
                        label,
                        dest,
                        source,
                        "conflict",
                        "owned symlink target changed (drift; cutover/adoption "
                        f"required): {current!r}",
                    )
                )
            elif current != desired:
                plans.append(ProjectionPlan(label, dest, source, "update", current))
            else:
                plans.append(ProjectionPlan(label, dest, source, "unchanged", current))
        elif dest.exists():
            plans.append(
                ProjectionPlan(
                    label,
                    dest,
                    source,
                    "conflict",
                    "destination exists and is not a symlink (cutover/adoption required)",
                )
            )
        else:
            plans.append(ProjectionPlan(label, dest, source, "create"))

    for raw, recorded in sorted(owned.items()):
        if raw in declared:
            continue
        dest = Path(raw)
        validate_owned_destination(raw, home, boundary)
        ancestor = ancestor_conflict(dest, home)
        if ancestor:
            plans.append(ProjectionPlan("retired", dest, dest, "conflict", ancestor))
            continue
        if dest.is_symlink():
            current = os.readlink(dest)
            if current == recorded:
                plans.append(ProjectionPlan("retired", dest, dest, "retire", current))
            else:
                plans.append(
                    ProjectionPlan(
                        "retired",
                        dest,
                        dest,
                        "conflict",
                        f"retired Tinjis link target changed (drift): {current!r}",
                    )
                )
        elif dest.exists():
            plans.append(
                ProjectionPlan(
                    "retired",
                    dest,
                    dest,
                    "conflict",
                    "retired destination exists and is not a symlink",
                )
            )
        else:
            plans.append(ProjectionPlan("retired", dest, dest, "retire", "already absent"))
    return plans


def conflicts(plans: list) -> list:
    """The plans a human must resolve before the plan means anything."""
    return [plan for plan in plans if plan.action == "conflict"]
