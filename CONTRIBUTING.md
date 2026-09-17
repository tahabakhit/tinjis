# Contributing to Tinjis

Tinjis is a small, stdlib-only, **read-only** configuration-declaration
checker. This file covers how to develop and test it and the invariants a change
must preserve. Read `AGENTS.md` for placement rules and `SECURITY.md` for the
security model before changing behavior.

## Development model: stdlib-only, clone-run

- **Standard library only.** Do not add a runtime dependency, packaging
  manifest, lockfile, installer, virtual-environment requirement, or build
  step. A contributor must be able to run everything from a bare Python
  checkout.
- There is nothing to install. Work directly in a clone:

  ```sh
  python3 bin/tinjis --help
  python3 -B -m tinjis --help
  ```

  `bin/tinjis` puts the checkout root on `sys.path` and disables bytecode
  writing before importing anything; `-B` does the same for the module entry
  point.
- Keep `tinjis/` at the repository root and `bin/tinjis` as the launcher, so
  both clone-run entry points keep working.

## Running the tests

The whole suite runs with the standard library:

```sh
python3 -m unittest discover --start-directory tests --verbose
```

Run the full suite before proposing a change. There is no separate integration
or network test. The claimed interpreter range is Python 3.11 through 3.14; run the
suite on each version you have. Record which versions you actually ran and do
not claim a version you did not exercise.

Ruff is optional. It is a local convenience and is deliberately not wired into
CI, so a clone can be tested with nothing installed. `.ruff.toml` documents the
lint and format rules used while authoring:

```sh
ruff check .
ruff format --check .
```

## Safety invariants to preserve

The project is conservative by design. Do not weaken these to make a change
convenient.

- **The CLI is read-only.** No command may create, replace, remove, or
  otherwise mutate anything, and no command may import `tinjis/writer.py`. A
  read-only run must not leave bytecode, a directory, a lock, a state file, or
  a cache in the checkout.
- **The writer stays internal and create-only.** `tinjis/writer.py` is the one
  sanctioned mutation module and is a tested foundation, not an operational
  writer. Do not expose it through the CLI, add a second mutating module, or
  widen its primitives without first satisfying the preconditions in
  `docs/STATUS.md`.
- **Fail closed.** Unknown, missing, duplicated, traversing, escaping,
  colliding, case-colliding, non-NFC, mistyped, or symlinked input is an error.
  There is no fallback manifest.
- **Comparison is folded; destinations are NFC.** Duplicate and
  ancestor/descendant leaves are compared case-folded and Unicode-normalised
  across every declared namespace.
- **Boundaries stay separate.** Structure/grammar/collision, containment, and
  declared-input shape are distinct layers; do not move a boundary check into a
  caller.
- **Reviewed actions are printed, never executed.**
- **Keep `tests/test_hygiene.py` passing.** It scans every file, bans
  filesystem mutation outside `tinjis/writer.py`, bans process and network
  imports and `getattr`/`setattr` indirection, and rejects personal paths and
  credential-shaped content. A new mutating site is a deliberate, reviewable
  act, not a test edit.

## Provenance and licensing

- Do not copy, adapt, or relicense a resource whose authorship is uncertain.
- Add a license only when the source states one. Tinjis has one root license,
  Apache-2.0 (`LICENSE`), with its copyright identity in `NOTICE`; do not
  invent a second.
- Record every upstream file consulted in `docs/PROVENANCE.md` with its git
  state versus `HEAD` and a recomputed SHA-256. If a later review finds
  third-party material inside a recorded file, remove that file rather than
  relicense it.
- Author a fixture instead of importing someone else's resource.
- Do not claim a platform is supported without a recorded acceptance run on
  that platform.

## Documentation and claims

- Do not document a flag or behavior that the code, or the installed tool's
  own help, does not show.
- Do not write a claim the tests do not establish. If it cannot be tested,
  scope it or drop it.
- Keep the three descriptions of the bundled topology in sync: the authored
  `examples/tinjis.json`, the compiled `LOCKED_EXAMPLE_TOPOLOGY` in
  `tinjis/topology.py`, and `tests/fixture_topology.py`. A drift fails a test
  by design.

## Where things live

See the placement table in `AGENTS.md`. Tests live in `tests/`, design and
policy documents in `docs/`, and synthetic example resources under `examples/`.

## Releases

Release gates, tag choices, and the publication checklist are in
`docs/RELEASING.md`. Do not add release automation; tagging and publishing are
maintainer decisions.
