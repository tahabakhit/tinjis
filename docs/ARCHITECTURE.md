# Architecture

Tinjis is a read-only declaration checker. It has authored inputs (a manifest, an
inventory, declared resources), one piece of untrusted runtime state (the
ownership record), and no writable subsystem at all.

## Module map

```
tinjis/
  __init__.py     package identity; disables bytecode writing before imports
  __main__.py     `python -m tinjis`
  errors.py       TinjisError(ValueError) and one subclass per layer
  strictjson.py   duplicate-key-refusing JSON decode
  paths.py        path grammar, NFC rule, folded keys, containment, ancestor safety
  selection.py    selection inventories (which resources a manifest names)
  manifest.py     the schema-v1 model, parser, containment, declared-input shapes
  topology.py     the compiled bundled-example topology lock
  ownership.py    the ownership record, read only
  plan.py         read-only planning
  cli.py          argument parsing, printing, exit codes
```

Dependency direction is one-way and shallow:

```
errors, strictjson, paths        (no Tinjis dependencies)
        |
   selection, ownership          (paths + errors)
        |
     manifest                    (paths, ownership constants, strictjson)
        |
     topology                    (manifest)
        |
       plan                      (manifest, ownership, selection)
        |
       cli                       (everything, for printing)
```

No module imports a module above it, keeping boundary checks in the lower
layers. Current source inspection finds no write, process, or network route;
recursive bounded AST checks in `tests/test_hygiene.py` guard straightforward
regressions but are not an exhaustive proof of Python behavior.

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
                            └── no writer exists. Nothing follows.
```

`plan_projections` is pure with respect to the filesystem: it only reads. There
is no function in the package that writes.

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
leaf names (`settings.json`, `owned.json`, `.tinjis-state`) are matched folded
too. Destinations must be NFC, so a decomposed spelling never reaches a
collision check at all.

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

## Why there is no writer

The first extraction had one. It was removed: it could not survive a crash
between a symlink write and the ownership-record update, and it could not make
the check-and-write step atomic against another process. Both are real defects,
and neither is fixable by tightening the code that existed — they need a journal
with recovery and a mutation primitive that cannot be split.

Rather than keep a callable prototype with known defects, the whole mutation
path was deleted: `plan.apply_projections`, `plan.atomic_link`,
`plan.leaf_race_error`, `ownership.write_owned`, and the `apply` command. A test
asserts each is absent.

What a future writer must bring is listed in `docs/STATUS.md`, together with the
requirement that adding one is an explicit, recorded decision.
