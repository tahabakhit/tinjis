# Status

What actually works, what is only validated, what is not built, and what was
removed. Keep this file honest: a scaffolded feature listed as operational is a
bug in the document, not a feature.

**The Tinjis CLI is read-only.** `validate`, `check`, and `example` still have no
filesystem-mutation path, and there is no `apply` command. Separately, this
phase adds a **tested but CLI-unreachable writer foundation** in
`tinjis/journal.py` and `tinjis/writer.py`; it is not exposed by any command, it
is **create-only**, and it refuses `retire`, `update`, and `conflict` before any
mutation. See *Writer foundation* and *Preconditions* below.

## Operational — read-only

Verified by the test suite in this checkout
(`python3 -m unittest discover --start-directory tests`), on macOS and on
Python 3.12 and 3.14.

| Capability | Notes |
|---|---|
| Strict JSON decoding | Duplicate object keys and `NaN`/`Infinity`/`-Infinity` literals are refused. |
| Schema-v1 manifest parsing | Unknown, missing, and wrongly typed keys refused; exact-key sets at every level. |
| Path grammar | Absolute, traversing, unnormalised, backslash, and control-character paths refused for both sources and destinations. |
| NFC destination grammar | Every destination component must be Unicode-normalised (NFC), so one name cannot be spelled two ways. |
| Name grammar | Consumer names, selection entries, legacy paths, and labels validated as safe components or labels. |
| Folded collision refusal | Duplicate and ancestor/descendant leaves compared case-folded and NFC-normalised across the file, consumer-settings, consumer-link, and selection namespaces. Reserved leaf names (`settings.json`, `owned.json`, `journal.json`, `.tinjis-state`) are matched case-insensitively. |
| Selection-destination containment | No declared leaf may equal, sit inside, or contain the selection destination, so a resolved selection leaf can never collide with a declared leaf or be an ancestor of one. |
| Checkout source containment | Every declared source must resolve inside the canonical checkout, including through a symlinked ancestor and through a dangling symlink. |
| Declared-input shape validation (`check`) | Every declared authored input must exist, be a real non-symlink file or directory of the declared kind, and be contained. |
| Selection inventory | Strict inventory read, sorted output, symlink refusal, marker-file requirement, folded duplicate refusal. |
| Ownership record reading | Fixed Tinjis-owned location, strict narrow-boundary validation, folded alias refusal within records and against selected leaves, symlink and ancestor refusal, mode never trusted or modified. |
| Read-only planning | `check` and `plan_projections` read only. Confirmed by a full checkout digest (contents, modes, symlink targets) and an empty-HOME assertion. |
| Retirement reporting | A recorded leaf that is no longer declared is reported as a retirement candidate, and as a conflict when it drifted. Nothing is removed by `check`. |
| Topology lock | The bundled example is compared against the compiled lock on every `check`, and reported by `validate`. |
| Reviewed actions | Printed only. Never executed. |
| Clone-run CLI | `bin/tinjis` and `python3 -B -m tinjis`, no installation step. |

## Writer foundation — internal, create-only, CLI-unreachable

`tinjis/writer.py` is the package's single sanctioned mutation site. Nothing in
`tinjis/cli.py` imports it, so no command can reach it. It exists so the
preconditions in *Reintroducing a writer* can be implemented and tested before
any route can act on a real HOME. It is a create-only foundation, **not an
operational writer**, and it makes no hostile-same-UID, full power-loss, or
general race-proof guarantee. All mutation tests run against temporary roots.

| Capability | Notes |
|---|---|
| Fsynced intent journal | `tinjis/journal.py` models a `create`-only transaction (`before`, `after`, ordered entries) and strictly parses it. `tinjis/writer.py` writes it, fsynced, to the fixed `~/.config/tinjis/journal.json` **before** the first link mutation. |
| Exclusive journal creation | The journal is hard-linked into place, so a second concurrent cooperating writer loses with `EEXIST` instead of clobbering the first writer's transaction. |
| Deterministic, idempotent recovery | `writer.recover` rolls a transaction forward: it re-checks each destination, replays only the non-final creates, rewrites the recorded final ownership map, and clears the journal. Running it twice is a no-op. It refuses when the live ownership record matches neither `before` nor `after`. |
| Narrow ownership enforcement | `ownership.validate_owned_map` re-validates every destination against HOME, the declared boundary, and the reserved `.config/tinjis` namespace before the record is written; folded aliases are refused. |
| Create without clobbering | `writer._create_link_at` uses `os.symlink`, which fails atomically with `EEXIST`; an existing destination, even one already pointing at the exact target, is refused rather than adopted. |
| Refused plan classes | `writer.plan_create_transaction` refuses a `retire`, `update`, or `conflict` plan, and `journal.validate_transaction` refuses a `retire` entry, both before the journal is written. |
| Stable advisory lock | `apply_transaction` and `recover` acquire one never-unlinked `flock` on `~/.config/tinjis/lock` before any authoritative read. A competing cooperating process fails closed. |
| Injected-failure recovery | Tests write an interrupted journal, fail mid-transaction, fail before the ownership write, and fail before journal cleanup, then assert recovery converges. |
| Scratch-file safety | `writer._write_state_file_at` writes to a scratch file, fsyncs it, and links or renames it into place. Handled Python exceptions attempt cleanup, including injected `fsync`, `open`, and `link` errors. Process death can leave a recognisable orphan scratch file; no automatic orphan cleanup is claimed. |

## Deliberately not exposed

| Capability | Why it stays disabled |
|---|---|
| `apply` command | The foundation implements only `create`. Rewriting a recorded target (`update`), retiring a leaf, and resolving a conflict are not implemented; exposing `apply` would leave those paths unproven. |
| Consumer settings applier | Precondition 4 below; settings templates are still validated and reported, never rendered or merged. |
| Topology-lock relaxation | Precondition 5 below; `check` and the compiled lock are unchanged, so no destination, consumer root, or consumer name is unlocked. |

## Removed in this phase

The following existed in the first extraction and was deleted, not disabled. A
test asserts each name is absent from the package. The writer foundation uses
**new** names in a new module rather than restoring these; it does not
reintroduce the old unjournaled writer.

| Removed | Why |
|---|---|
| `apply` CLI command and `command_apply` | It was the only route to a filesystem mutation, and neither journaled recovery nor an atomic create primitive existed behind it. A journal and a create-only primitive now exist as an unexposed foundation, but the command is still withheld. |
| `plan.apply_projections` | Wrote symlinks and the ownership record with no journal. Planning remains pure; orchestration lives in `tinjis/writer.py`. |
| `plan.atomic_link` | The `symlink` + `os.replace` writer. The foundation's create primitive lives in `tinjis/writer.py`. |
| `plan.leaf_race_error` | Per-leaf revalidation before a write. Revalidation now lives with the create primitive. |
| `ownership.write_owned` | The old ownership-record writer. `ownership` is still read-only; the fsynced write lives in `tinjis/writer.py`. |
| `errors.PlanError` | Only the old writer raised it. The foundation raises `WriterError`/`JournalError`. |

## Scaffolded — parsed, validated, and reported, never applied

| Capability | What is missing |
|---|---|
| Consumer settings templates | `consumers[].settings` is validated for grammar, containment, existence, type, and symlink policy, and reported by `check` as declared. Nothing renders it, merges it, or writes it. |
| Consumer link trees | `consumers[].links` are validated the same way, including folded cross-namespace collisions, and reported as declared. No consumer link is applied. |
| Consumer roots | `runtime.root` and `consumers[].root` are validated and used for collision checking and reporting only. |
| Legacy paths | `legacy_paths` entries are reported when a symlink is found. They are never removed. |
| `OWNED_STATE_DIRNAME` | Reserved as a name so no declared link can claim it. Nothing creates, reads, or writes that directory. |

## Not built

* Any CLI route to filesystem mutation: no `apply` command, and no command
  imports `tinjis.writer`.
* Ownership migration: moving a runtime root, renaming a consumer, relocating a
  destination, or rewriting a previously recorded target.
  `writer.plan_create_transaction` refuses an `update` plan for exactly this
  reason, and a `retire` or `conflict` plan is refused too.
* Settings-content schemas for any consumer; a template is treated as opaque.
* Package installation, dependency resolution, and pinned-cache inspection.
* Native reporter generation or any other integration refresh.
* Any interaction with a model, a network service, or an external process. The
  writer imports no process or network module and the AST guard enforces it.

## Preconditions for reintroducing a writer

| # | Precondition | Status |
|---|---|---|
| 1 | **A journal with recovery.** An fsynced record of intent written before the first mutation, plus a recovery path that can finish or roll back an interrupted run. | Implemented and tested for `create` only (`tinjis/journal.py`, `tinjis/writer.py`); a `retire` entry is refused. Roll-forward, not rollback. |
| 2 | **A race-resistant mutation primitive** whose check and write cannot be separated by another cooperating process, re-validated at the moment of the write. | Implemented for `create` (atomic `os.symlink` `EEXIST`). `retire`, `update`, and `conflict` are refused. Exclusive journal creation serialises concurrent cooperating writers. |
| 3 | **An ownership-migration protocol**, the precondition for unlocking a destination, a consumer root, or a consumer name. | Not implemented. Target replacement is refused, so nothing is unlocked. |
| 4 | **A consumer settings applier** that owns one narrow marked region and validates it before and after. | Not implemented. |
| 5 | **A recorded decision** on why the lock can be relaxed for the specific topology being unlocked. | Not applicable yet: the lock is unchanged and no topology is unlocked. |

Exposing `apply` remains blocked on preconditions 3–5. The current foundation
narrows the claim honestly: it is a journaled, create-only foundation with a
stable advisory lock for cooperating same-UID processes. It is not an
operational writer and does not claim general race-proofness or power-loss
durability.

## Known limits of this phase

* A runtime root that is itself a symlink is refused rather than resolved.
* `python3 -m tinjis` without `-B` lets the interpreter cache the package's own
  `__init__` module before the package can disable caching. `bin/tinjis` and
  `python3 -B -m tinjis` are the write-free paths; `tests/test_cli.py` asserts
  both leave the checkout unchanged.
* `check` exits non-zero when the file and selected-resource projections are
  not reflected at the runtime root, so it is usable as a scoped drift check.
  Consumer settings and links are validated as authored inputs but are not
  compared with consumer runtime state. `validate` exits zero for any
  well-formed manifest, including one that is not the bundled example.
* `check` runs only against the bundled example topology. `validate` inspects
  any manifest but never plans one.
* A declared authored input that is a symlink is refused even when it resolves
  inside the checkout. Repositories that symlink a shared resource internally
  will need that declaration changed to a real path.
* Directory `fsync` errors fail the operation closed. A platform or filesystem
  that refuses directory fsync is unsupported by this foundation. Successful
  fsync calls still do not establish full device-level power-loss durability.
* The writer foundation is POSIX-shaped and is exercised only on macOS in this
  checkout. Its recovery semantics on a filesystem without atomic
  `symlink`/`link`/`rename` are not claimed.
* The writer foundation creates only. It does not retire, replace, or otherwise
  migrate an existing destination. Normal apply preflights every leaf before
  publishing intent and refuses pre-existing links. Recovery may accept an
  exact-target link only for a trusted transaction whose intent was published
  after that preflight and whose create may have completed before interruption.
* Direct internal callers must supply targets derived from a validated manifest
  and source inventory. The writer validates destination boundaries and NFC,
  but it does not independently prove target-source containment or inspect every
  live sibling for filesystem-specific aliases.

## Platform status

| Platform | Status |
|---|---|
| macOS | Primary. The suite passes here on Python 3.12 and 3.14. |
| Linux (POSIX) | **Not verified.** The code is stdlib-only and path handling is POSIX-shaped, and the CI matrix includes Linux, but no green Linux run has been recorded. Do not claim support until one is. |
| Windows | Deferred. Nothing is claimed; the writer's `os.link`/`os.rename` semantics differ. |

## Development tooling

None is required. The suite runs on a bare Python installation with no
installation step. `.ruff.toml` documents the lint and format rules used while
authoring this checkout; `ruff` is a local convenience and is deliberately not
wired into CI, so a clone can be tested with nothing installed. Secret scanning
is a publication check, not a runtime dependency. On 2026-09-17,
`gitleaks dir . --no-banner --redact` exited 0 against the complete uncommitted
working tree. Re-run it against Git history before publication. Adding a runtime
dependency, a packaging manifest, a lockfile, or a build step requires an
explicit decision recorded here first.
