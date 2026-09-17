# Changelog

Notable changes to Tinjis. Release dates use ISO 8601 format.

Every entry describes a **read-only** command-line interface. Tinjis validates
and reports; it has no `apply` command and no filesystem-mutation, subprocess,
or network path. A tested, CLI-unreachable, create-only writer foundation exists
under `tinjis/writer.py`; see `docs/STATUS.md` for exactly what it does and why
`apply` stays withheld.

## [0.1.0] - 2026-09-17

Initial public release of the Tinjis engine.

### Added

- Strict schema-v1 manifest parsing: exact key sets, duplicate-key and
  `NaN`/`Infinity` refusal, NFC destination grammar, and folded
  (case-insensitive and Unicode-normalised) collision rejection.
- Checkout source containment, declared-input existence/type/symlink
  validation, selection inventories, and strict ownership-record reading.
- Read-only commands `validate`, `check` (the default), and `example`, which
  print what a future writer would do and change nothing.
- A tested, internal, create-only writer foundation (`tinjis/journal.py`,
  `tinjis/writer.py`): an fsynced intent journal, an exclusive lock,
  deterministic roll-forward recovery, and an atomic create primitive.
- Clone-run entry points `bin/tinjis` and `python3 -B -m tinjis`, with no
  install step, packaging manifest, dependency, or build step.
- Synthetic example topology under `examples/`, held in place by a compiled
  topology lock and an independent test fixture.
- Repository policy in `README.md`, `SECURITY.md`, `AGENTS.md`,
  `CONTRIBUTING.md`, and `docs/`.

### Read-only CLI scope

- There is no `apply` command. `tinjis/cli.py` does not import the writer.
- A missing, malformed, duplicated, traversing, escaping, colliding,
  case-colliding, non-NFC, mistyped, or symlinked input is refused; there is no
  fallback manifest.
- Reviewed actions are printed, never executed.
- A read-only run leaves no bytecode, directory, lock, state file, or cache in
  the checkout.

### Not included

- Any CLI route to mutation, including ownership migration, settings
  application, retirement, or replacement of a recorded target.
- Package installation, dependency resolution, network access, model
  interaction, or another tool's lifecycle management.
- Windows or non-POSIX platform support; the release matrix covers macOS and
  Linux only.

### Notes

- Python 3.11 through 3.14. The complete macOS and Linux CI matrix passed on
  2026-09-17. See `docs/RELEASING.md`.
- Licensed under Apache-2.0 (`LICENSE`, `NOTICE`). Adapted-code provenance,
  including that no license of Atlas as a whole is claimed, is recorded in
  `docs/PROVENANCE.md`.
