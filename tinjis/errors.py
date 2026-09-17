"""Tinjis error hierarchy.

Every refusal in Tinjis is a ``ValueError`` subclass so a caller can catch one
base class and report a refused input clearly instead of proceeding with a
partial configuration. Subclasses name the layer that refused, which keeps the
CLI messages and the tests specific without inventing a second convention.
"""

from __future__ import annotations


class TinjisError(ValueError):
    """Base class for every refused Tinjis input or unsafe state."""


class ManifestError(TinjisError):
    """A missing, malformed, unsupported, or unsafe declarative manifest."""


class TopologyError(ManifestError):
    """A manifest that is well formed but is not the bundled example topology."""


class ContainmentError(ManifestError):
    """A declared repository source that resolves outside the checkout."""


class InputError(ManifestError):
    """A declared authored input that is missing, mistyped, or a symlink."""


class OwnershipError(TinjisError):
    """A malformed ownership record or a path outside the owned boundary."""


class SelectionError(TinjisError):
    """A missing, malformed, duplicated, or unresolved selection inventory."""


class JournalError(TinjisError):
    """A malformed, inconsistent, or unrecoverable intent journal."""


class WriterError(TinjisError):
    """A refused or failed filesystem mutation in the writer foundation."""


class RevalidationError(WriterError):
    """A destination changed between the check and the write."""
