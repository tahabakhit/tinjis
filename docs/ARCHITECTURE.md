# Architecture

Tinjis has authored inputs (a manifest, an
inventory, declared resources), one piece of untrusted runtime state (the
ownership record), and a read-only CLI. A separate, tested, **CLI-unreachable
writer foundation** (`journal.py` + `writer.py`) journals intent, recovers
deterministically, and provides one atomic `create` primitive a future `apply`
command would need. It is not an operational writer: `retire`, `update`, and
`conflict` are refused. No command imports it.

## Module map

```
tinjis/
  __init__.py     package identity; disables bytecode writing before imports
  __main__.py     `python -m tinjis`
  errors.py       TinjisError(ValueError) and one subclass per layer
  strictjson.py   duplicate-key-refusing JSON decode + stable encoder
  paths.py        path grammar, NFC rule, folded keys, containment, ancestor safety
  selection.py    selection inventories (which resources a manifest names)
  journal.py      fsynced intent journal: model, strict parse, recovery decision
  manifest.py     the schema-v1 model, parser, containment, declared-input shapes
  topology.py     the compiled bundled-example topology lock
  ownership.py    the ownership record model, read-only, plus map validation
  plan.py         read-only planning
  writer.py       the one mutation site: atomic writes, link primitives, recovery
  cli.py          argument parsing, printing, exit codes (no writer import)
```

Dependency direction is one-way and shallow:

```
errors, strictjson, paths        (no Tinjis dependencies)
        |
   selection, ownership          (paths + errors)
        |
     journal                     (ownership, paths, strictjson)
        |
     manifest                    (paths, ownership, journal, strictjson)
        |
     topology, plan              (manifest; plan also ownership, selection)
        |
       writer                    (plan, journal, ownership)  -- unreachable from cli
        |
       cli                       (everything except writer, for printing)
```

No module imports a module above it, keeping boundary checks in the lower
layers. `cli` deliberately never imports `writer`, so the read-only commands
have no route to a mutation. Recursive bounded AST checks in
`tests/test_hygiene.py` allow filesystem calls in `writer.py` only, ban process
and network imports everywhere (including `writer.py`), and reject
`getattr`/`setattr` indirection; they are regression guards, not an exhaustive
proof of Python behavior.

## Data flow

```
manifest file ──parse──▶ Manifest ──validate──▶ folded collisions, containment
                            │
             selection inventory ──▶ selected resource leaves
                            │
                            ▼
                    projection_pairs(home, checkout, manifest)
                            │
              ownership record ──▶ plan_projections(...)  (pure, no writes)
                            │
                            ▼
                    report: create | update | unchanged | retire | conflict
                            │
                            └── report only; the CLI stops here. The unexposed
                                writer foundation can consume a plan from there.
```

`plan_projections` is pure with respect to the filesystem: it only reads. The
`writer` foundation consumes a plan but is not reachable from the CLI.

```
plan_projections(...) ──▶ writer.plan_create_transaction(owned, plans)
                              │  (create only; retire/update/conflict refused)
                              ▼
                     writer.apply_transaction(home, boundary, transaction)
                       journal (fsynced, exclusive) ──▶ links ──▶ owned.json ──▶ clear
                              │
                     writer.recover(home, boundary)   (deterministic roll-forward)
```

## Three validation layers

Each caller states exactly what it proves, and no layer silently assumes the
next:

| Layer | Function | Requires the tree? | Run by |
|---|---|---|---|
| Structure, grammar, declared collisions | `manifest.parse_manifest` | no | `validate`, `check` |
| Canonical containment | `manifest.validate_source_containment` | no (resolves lexically) | `validate`, `check` |
| Declared-input shape | `manifest.validate_declared_inputs` | yes | `check` |

`validate_source_containment` deliberately does not require existence, so
`tinjis validate` can inspect a manifest on a machine that does not have the
tree. `check` adds the shape layer, so a missing or mistyped settings template,
link source, inventory, source root, or file projection is a hard error rather
than something the planner reports later.

## Two namespaces, one leaf rule

Declared paths come in two flavours:

* **Sources** are checkout-relative and are read. They must be normalised,
  non-absolute, traversal-free, and must *resolve* inside the canonical
  checkout. A symlinked leaf or ancestor that leaves the checkout is refused,
  dangling symlinks included. In the `check` layer they must additionally be
  real, non-symlink files or directories.
* **Destinations** are runtime-relative and name paths Tinjis reasons about.
  They must be normalised, non-absolute, traversal-free, and NFC-normalised.

A **leaf** is any concrete path the manifest declares: a consumer settings file,
a consumer link, a file projection, or one selected resource.
`manifest.validate_semantic_collisions` refuses:

* two leaves with the same *folded* path, in any namespace;
* a leaf that is a folded ancestor of another leaf;
* a leaf equal to, inside, or containing the selection destination.

"Folded" means case-folded and NFC-normalised (`paths.fold_key`), so `Alpha` and
`alpha`, or an NFC/NFD pair, are one path. A default macOS filesystem would
collapse them, so reporting two would be a lie about what can exist. Reserved
leaf names (`settings.json`, `owned.json`, `journal.json`, `lock`,
`.tinjis-state`) are matched folded too, and the whole `.config/tinjis`
namespace is reserved against every projection category. Destinations must be
NFC, so a decomposed spelling never reaches a collision check at all.

These rules are checked at parse time, so an unsafe topology cannot reach the
planner.

### Why the selection namespace needs no separate check

The selection destination is a directory whose children are the selected
resources. The parse-time rules forbid any declared leaf from equalling, sitting
inside, or containing it. A selection leaf is by construction one direct child of
it, so:

* a selection leaf cannot equal a declared leaf (that leaf would be inside the
  destination, which is refused);
* a selection leaf cannot be an ancestor of a declared leaf (the declared leaf
  would then be inside the destination, which is refused);
* two selection leaves cannot collide, because inventory entries are unique
  after folding.

The invariant therefore closes the whole namespace, and the planner does not
repeat the check on resolved paths. Keeping one check rather than two is what
makes the guarantee auditable. A test asserts the invariant on the bundled
example and on a mutated manifest that would violate it.

## The topology lock

Parsing is generic; naming a topology is not.
`topology.LOCKED_EXAMPLE_TOPOLOGY` is a compiled constant, and `check` compares
the parsed bundled example against it field by field. If the authored example
and the compiled lock disagree, the run is refused before planning.

This is a validation allowlist, never a fallback: the lock is constructed in
code and never read from disk. Since Tinjis writes nothing anyway, the lock's
role is to keep the *reviewed* topology and the *executable* topology from
drifting apart, and to keep `validate` honest about which manifest is the
bundled one.

## Ownership record

`ownership.OWNED_RELATIVE` is a fixed constant (`~/.config/tinjis/owned.json`),
not a manifest field: a corrupted manifest must not be able to point Tinjis at a
different bookkeeping file.

The record maps each destination to the exact target a previous writer recorded.
On every read, each recorded destination must:

* be an absolute, normalised path;
* lie inside the HOME the command was given;
* spell a declared destination exactly, or be one direct child of the selection
  destination.

Comparison is exact, deliberately. The record is bookkeeping a writer left, so a
variant that differs only by case or normalisation is refused as outside the
boundary rather than accepted as the same path: on a case-sensitive filesystem
it names a different path, and on a case-insensitive one accepting it would hide
drift.

The record only affects how an existing symlink is interpreted — as
Tinjis-created or as pre-existing. An existing symlink with the correct target is
still a conflict unless the record names it, because "it already points where I
want" is not evidence that this tool wrote it.

## Why the writer is not exposed

The first extraction had a writer with two real defects: it could not survive a
crash between a symlink write and the ownership-record update, and it could not
make the check-and-write step atomic against another process. Both were
deleted rather than left as a callable prototype: `plan.apply_projections`,
`plan.atomic_link`, `plan.leaf_race_error`, `ownership.write_owned`, and the
`apply` command. A test asserts each name is still absent.

This phase adds the missing machinery under new names, in `journal.py` and
`writer.py`, with the following shape:

* **Journal first.** `apply_transaction` writes an fsynced transaction to
  `~/.config/tinjis/journal.json` *before* the first link mutation, using a
  hard link so a concurrent cooperating writer loses. The transaction records
  the exact `before` and `after` ownership maps and the ordered entries, so
  recovery needs no context beyond the journal.
* **Deterministic, idempotent recovery.** `recover` re-checks each destination
  and replays only the non-final creates; it refuses when the live ownership
  record matches neither `before` nor `after`. Running it twice changes nothing.
* **Create only.** `create` relies on `os.symlink`'s atomic `EEXIST`; an
  existing destination, even one already pointing at the exact target, is
  refused rather than adopted. A target rewrite (`update`), a retirement, and a
  conflict are refused before the journal is written.

What is *not* built is the reason `apply` stays hidden: ownership migration
(precondition 3 in `docs/STATUS.md`), a consumer settings applier (precondition
4), and a recorded decision to relax the topology lock (precondition 5). Until
those exist, the CLI exposes no mutation and the lock is unchanged.
