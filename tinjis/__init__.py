"""Tinjis: a small, stdlib-only, read-only configuration declaration checker.

The package owns one narrow job: parse an authored, declarative JSON manifest,
refuse anything malformed or ambiguous, and report the *plan* a future writer
would have to carry out. It does not carry it out. There is no filesystem
mutation path in this package at all -- no symlink, directory, file, or
permission write, and no subprocess or network call. ``docs/STATUS.md`` records
why, and what a future writer must bring with it first.

Every external tool a manifest can name -- Pi, Hermes, Herdr, or anything else
-- remains a user-managed prerequisite. Tinjis does not install packages, does
not fetch from the network, and does not edit a live configuration file.

Importing this package disables bytecode writing for the process, so a
read-only run does not litter the checkout with ``__pycache__``. The one
exception is the package's own ``__init__`` module when the interpreter is
invoked as ``python3 -m tinjis``: the interpreter decides to cache that module
before its body runs, so nothing inside the package can prevent it. Use
``bin/tinjis`` (which sets the flag before importing anything) or pass ``-B``
for a run that is guaranteed to write nothing at all.
"""

from __future__ import annotations

import sys

# Suppress bytecode writing for this package and everything it imports. The
# interpreter may still cache this one module when invoked as ``python -m
# tinjis``, because that decision is made before the module body runs.
sys.dont_write_bytecode = True

__all__ = ["SCHEMA", "__version__"]

__version__ = "0.1.0"

#: The only declarative manifest schema this package understands.
SCHEMA = 1
