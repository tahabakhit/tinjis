# AGENTS.md — Tinjis agent instructions

Short placement and update guardrails. Do not restate the architecture.

## The CLI is read-only; the writer is one internal create-only module

No command has a filesystem-mutation path: there is no `apply` command, and
`tinjis/cli.py` must never import `tinjis/writer.py`. The internal foundation in
`tinjis/journal.py` and `tinjis/writer.py` is a deliberate, bounded exception:
it is **create-only** (it refuses `retire`, `update`, and `conflict` before any
mutation), it journals intent before the first link, and recovery is
deterministic. Normal apply never adopts a pre-existing leaf; recovery may
accept an exact-target leaf only under a trusted journal published after an
absence preflight. It is not an operational writer and makes no
hostile-same-UID, full power-loss, or general race-proof guarantee. Do not expose
it through the CLI, add another mutating module, or
widen its primitives without first satisfying the preconditions recorded in
`docs/STATUS.md`.

The safety scan in `tests/test_hygiene.py` bans filesystem calls in every module
except `writer.py`, bans process and network imports and `getattr`/`setattr`
indirection everywhere, and pins the exact filesystem calls `writer.py` may use.
A separate test fails if any other module starts calling a mutating primitive.
Keep those tests passing rather than weakening them; widening the writer's call
set is a reviewable, deliberate act.

## Placement

| Item | Location |
|---|---|
| Declarative schema-v1 manifest | `examples/tinjis.json`; the bundled example |
| Compiled topology lock | `tinjis/topology.py` (`LOCKED_EXAMPLE_TOPOLOGY`) |
| Independent test copy of that topology | `tests/fixture_topology.py`; test-only, never read by production code |
| Python package | `tinjis/` (repo root, so `python3 -B -m tinjis` works from a clone) |
| Clone-run launcher | `bin/tinjis` |
| Synthetic example resources | `examples/project/` |
| Tests | `tests/` |
| Design, schema, provenance, and status documents | `docs/` |

Keep a module's responsibility whole. `paths.py` owns the path grammar, NFC
rule, and folded keys; `selection.py` owns the inventory; `manifest.py` owns the
declarative model, containment, and declared-input shapes; `ownership.py` owns
the ownership record; `plan.py` owns read-only planning; `cli.py` owns argument
parsing and printing. Do not move a boundary check into a caller.

## Rules to preserve

* **Ownership is recorded, never inferred from a matching target alone.** An
  existing symlink with the right target is a normal-planning conflict unless
  the record names it. Create recovery may accept that state only when a trusted
  locked journal proves intent was published after an absence preflight.
* **Comparison of declared paths is folded.** Case and Unicode normalisation
  differences are collisions, because a default macOS filesystem collapses them.
  Destinations must be NFC. Reserved leaf names are matched folded.
* **`check` and `validate` must not create anything**: no bytecode, no directory,
  no lock, no state file, no cache, no output file. `tests/test_cli.py` enforces
  this against a checkout digest covering contents, modes, and symlink targets.
* **Validation layers stay separate.** Structure/grammar/collisions,
  containment, and declared-input shape each prove one thing. `validate` must not
  require the tree to exist; `check` must.
* **Schema-v1 topology is locked.** Any change to `examples/tinjis.json` must be
  made in `tinjis/topology.py` **and** `tests/fixture_topology.py` in the same
  change, or the tests fail by design.
* **No fallback manifest.** A missing or malformed manifest is always an error.
* **Reviewed actions are printed, never run.**

## Update guardrails

- Standard library only. No dependency, package manager, installer, lockfile, or
  build step may be introduced without an explicit decision recorded in
  `docs/STATUS.md`.
- Never commit credentials, sessions, caches, generated integrations, or other
  runtime state. The ownership record lives in the operator's HOME, never in the
  checkout.
- Do not copy, adapt, or relicense a resource whose authorship is uncertain. Add
  a license only when the source states one. `docs/PROVENANCE.md` must name every
  Atlas file consulted, its git state versus `HEAD`, and its recomputed SHA-256,
  and must state that no license of Atlas as a whole is claimed (Atlas has no root license file).
- Do not claim a platform is supported without a recorded acceptance run on that
  platform. Keep macOS primary and Linux unverified until then.
- Do not reference an operator's personal paths, models, package pins, private
  resources, or credentials in examples, tests, or documentation. Hygiene tests
  enforce this.
- Do not write documentation that promises more than the tests establish. If a
  claim cannot be tested, scope it or drop it.
