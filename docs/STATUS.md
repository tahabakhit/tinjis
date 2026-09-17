# Status

What actually works, what is only validated, what is not built, and what was
removed. Keep this file honest: a scaffolded feature listed as operational is a
bug in the document, not a feature.

**Tinjis v0 is read-only.** There is no writer, no `apply` command, and no
filesystem-mutation path anywhere in the package. That is a scope decision, not
an unfinished feature: see *Removed* below.

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
| Folded collision refusal | Duplicate and ancestor/descendant leaves compared case-folded and NFC-normalised across the file, consumer-settings, consumer-link, and selection namespaces. Reserved leaf names (`settings.json`, `owned.json`, `.tinjis-state`) are matched case-insensitively. |
| Selection-destination containment | No declared leaf may equal, sit inside, or contain the selection destination, so a resolved selection leaf can never collide with a declared leaf or be an ancestor of one. |
| Checkout source containment | Every declared source must resolve inside the canonical checkout, including through a symlinked ancestor and through a dangling symlink. |
| Declared-input shape validation (`check`) | Every declared authored input must exist, be a real non-symlink file or directory of the declared kind, and be contained. |
| Selection inventory | Strict inventory read, sorted output, symlink refusal, marker-file requirement, folded duplicate refusal. |
| Ownership record reading | Fixed Tinjis-owned location, strict narrow-boundary validation, folded alias refusal within records and against selected leaves, symlink and ancestor refusal, mode never trusted or modified. |
| Read-only planning | `check` and `plan_projections` read only. Confirmed by a full checkout digest (contents, modes, symlink targets) and an empty-HOME assertion. |
| Retirement reporting | A recorded leaf that is no longer declared is reported as a retirement candidate, and as a conflict when it drifted. Nothing is removed. |
| Topology lock | The bundled example is compared against the compiled lock on every `check`, and reported by `validate`. |
| No process, network, or mutation surface | Current source inspection finds none. Recursive bounded AST guards reject straightforward banned imports/calls and `getattr` indirection; they are regression guards, not a proof of all Python behavior. |
| Reviewed actions | Printed only. Never executed. |
| Clone-run CLI | `bin/tinjis` and `python3 -B -m tinjis`, no installation step. |

## Removed in this phase

The following existed in the first extraction and has been deleted, not
disabled. A test asserts each name is absent from the package.

| Removed | Why |
|---|---|
| `apply` CLI command and `command_apply` | It was the only route to a filesystem mutation, and neither journaled recovery nor race-resistant mutation existed behind it. |
| `plan.apply_projections` | Wrote symlinks and the ownership record with no durable journal; a crash between a write and the record update left an unrecorded link. |
| `plan.atomic_link` | The `symlink` + `os.replace` writer. |
| `plan.leaf_race_error` | Per-leaf revalidation before a write. With no write, there is no destructive-write time-of-check/time-of-use window to narrow; read results remain snapshots. |
| `ownership.write_owned` and `strictjson.dumps_stable` | The ownership-record writer and the deterministic encoder it used. The record is now read-only input. |
| `errors.PlanError` | Only the writer raised it. |

Consequence: the two destructive-write blockers that motivated this change —
a check/write race and a non-atomic partial failure — do not exist, because
there is no code path that can change the filesystem. Read checks remain
snapshots and can race with changes made by other programs. Re-adding a writer
without a journal and a recovery design would reintroduce both write blockers.

## Scaffolded — parsed, validated, and reported, never applied

| Capability | What is missing |
|---|---|
| Consumer settings templates | `consumers[].settings` is validated for grammar, containment, existence, type, and symlink policy, and reported by `check` as declared. Nothing renders it, merges it, or writes it. |
| Consumer link trees | `consumers[].links` are validated the same way, including folded cross-namespace collisions, and reported as declared. No consumer link is applied. |
| Consumer roots | `runtime.root` and `consumers[].root` are validated and used for collision checking and reporting only. |
| Legacy paths | `legacy_paths` entries are reported when a symlink is found. They are never removed. |
| `OWNED_STATE_DIRNAME` | Reserved as a name so no declared link can claim it. Nothing creates, reads, or writes that directory, and no journal is claimed to exist. |

## Not built

* Any filesystem mutation: no create, replace, remove, chmod, or mkdir.
* A crash-recovery journal and multi-step transactions.
* Ownership migration: moving a runtime root, renaming a consumer, relocating a
  destination, or rewriting a previously recorded target.
* Settings-content schemas for any consumer; a template is treated as opaque.
* Package installation, dependency resolution, and pinned-cache inspection.
* Native reporter generation or any other integration refresh.
* Any interaction with a model, a network service, or an external process.

## Preconditions for reintroducing a writer

Do not add one until all of these exist and are tested:

1. **A journal with recovery.** A durable record of intent written before the
   first mutation, plus a recovery path that can finish or roll back an
   interrupted run, so a crash cannot leave an unrecorded link.
2. **A race-resistant mutation primitive** whose check and write cannot be
   separated by another process, and which is re-validated against the
   ownership record at the moment of the write.
3. **An ownership-migration protocol**, which is the precondition for unlocking
   a destination, a consumer root, or a consumer name.
4. **A consumer settings applier** that owns one narrow, marked region,
   refuses to touch anything outside it, and validates the region before and
   after.
5. **A decision recorded here** on why the lock can be relaxed for the specific
   topology being unlocked.

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

## Platform status

| Platform | Status |
|---|---|
| macOS | Primary. The suite passes here on Python 3.12 and 3.14. |
| Linux (POSIX) | **Not verified.** The code is stdlib-only and path handling is POSIX-shaped, and the CI matrix includes Linux, but no green Linux run has been recorded. Do not claim support until one is. |
| Windows | Deferred. Nothing is claimed. |

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
