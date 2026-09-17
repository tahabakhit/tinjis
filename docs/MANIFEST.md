# Manifest reference (schema-v1)

One JSON object. Every key is required unless marked optional. Unknown keys are
refused at every level, duplicate keys are refused, and a non-integer or
unsupported `schema` is refused. All paths are POSIX-style, use `/` as the only
separator, and may not be absolute, may not contain `.` or `..` components, and
may not contain a backslash, NUL, newline, or carriage return. A **destination**
component must additionally be Unicode-normalised (NFC).

Nothing in this schema is applied. `tinjis check` plans the bundled example and
`tinjis validate` inspects any manifest; neither writes.

```
┌ schema          1
├ runtime         { root }
├ consumers[]     { name, root, settings, links[] }
├ selection       { inventory, source_root, destination }
├ files[]         { label, source, destination }
├ legacy_paths[]  string
└ reviewed_actions[] { consumer, command }
```

## `schema`

Integer `1`. `true` is refused even though Python treats it as `1`.

## `runtime`

| Key | Meaning |
|---|---|
| `root` | The generic runtime root, relative to HOME. Parent directory of every consumer root. Example: `.config/tinjis-example`. |

## `consumers[]`

At least one entry. A consumer is a named tool configuration Tinjis knows how
to describe.

| Key | Meaning |
|---|---|
| `name` | Safe profile name: `[a-z][a-z0-9_-]*`, unique across the manifest. Also the default directory component under `runtime.root`. |
| `root` | Runtime-relative path, relative to `runtime.root`, where this consumer's files live. |
| `settings` | Checkout-relative path to the authored settings template. Must resolve inside the checkout and must not be a symlink that leaves it. |
| `links` | Zero or more projected leaves. |

### `consumers[].links[]`

| Key | Meaning |
|---|---|
| `destination` | Path relative to the consumer root (`runtime.root/root`). |
| `source` | Checkout-relative path to the resource directory. |

A link `destination` is refused when any component is a reserved name. The
comparison is case-folded, so `Settings.json` is refused for the same reason
`settings.json` is:

* `settings.json` — the consumer settings file;
* `owned.json` — the Tinjis ownership record leaf name;
* `.tinjis-state` — the reserved future transaction state directory name.

Within one consumer, `destination` values must be unique and no destination may
be an ancestor of another. Both rules are folded, and both are re-applied across
every namespace by `validate_semantic_collisions`.

## `selection`

The shared resources a runtime sees, one symlink per selected resource in a
future writer's plan.

| Key | Meaning |
|---|---|
| `inventory` | Checkout-relative path to the authored inventory file. Must be a regular, non-symlink file. |
| `source_root` | Checkout-relative path to the directory the inventory selects from. Must be a real, non-symlink directory. |
| `destination` | Runtime-relative directory. Each selected resource is one direct child here. |

The inventory file is:

```json
{ "skills": ["alpha", "beta"] }
```

Exactly one key, `skills`. Entries are unique safe path components
(`[A-Za-z0-9][A-Za-z0-9._-]*`), returned sorted, and each must name a real,
non-symlink directory under `source_root` containing a regular, non-symlink
`SKILL.md`. Duplicate entries are refused case-folded, so `Report` and `report`
are one selection. The name grammar is ASCII-only, which also means a
normalisation duplicate is unreachable here. `SKILL.md` is the cross-tool Agent
Skills marker, not a Tinjis invention. A missing inventory is an error, never an
empty selection.

## `files[]`

Standalone file projections for tool configuration that is not a resource leaf.

| Key | Meaning |
|---|---|
| `label` | Unique human label, used in plans and errors. |
| `source` | Checkout-relative path to the authored file. |
| `destination` | Runtime-relative path to the symlink Tinjis owns. |

## `legacy_paths[]`

Unique safe path components, each interpreted as one direct child of
`runtime.root`. If a symlink is found there, `check` reports it as a retired
whole-root link for manual removal. Tinjis never deletes it.

## `reviewed_actions[]`

| Key | Meaning |
|---|---|
| `consumer` | `null`, or the `name` of a declared consumer. An undeclared name is refused. |
| `command` | Non-empty, non-blank opaque string. |

Reviewed actions are printed and never executed. They exist so an operator can
see the commands a tool's own ecosystem expects without Tinjis running them.

## Collision rules

The manifest declares concrete leaves:

* each `files[].destination`;
* each `runtime.root/consumers[].root/settings.json`;
* each `runtime.root/consumers[].root/links[].destination`.

Refused:

1. two leaves with the same **folded** path;
2. a leaf that is a **folded** ancestor of another leaf;
3. a leaf equal to the selection `destination`;
4. a leaf inside the selection `destination`;
5. a leaf that contains the selection `destination`.

"Folded" means case-folded and NFC-normalised. On a default macOS filesystem
`Alpha` and `alpha`, and a composed and a decomposed spelling of one name, are a
single path, so accepting both would report a plan the filesystem cannot hold.
Destinations must be NFC, so a decomposed spelling is refused by the grammar
before any collision check runs.

Rule 1 spans namespaces on purpose. Before it was made cross-namespace, a file
projection and a consumer link could name the same path and the second silently
overwrote the first in the collision map.

Rule 4 is what makes the check sufficient for selected resources. A selection
leaf is by construction one direct child of the selection `destination`, so once
no declared leaf may sit inside that destination, no resolved selection leaf can
collide with a declared leaf or be an ancestor of one. That is why the planner
does not repeat the check on resolved paths. See `docs/ARCHITECTURE.md`.

Duplicate projection labels are compared folded too, so two labels cannot look
identical in a plan.

## Containment rules

Every checkout-relative source — `consumers[].settings`,
`consumers[].links[].source`, `selection.inventory`, `selection.source_root`,
and `files[].source` — is resolved with symlinks followed (`Path.resolve`). The
result must be the checkout root or a descendant of it. A symlinked leaf, a
symlinked ancestor, or a dangling symlink that resolves outside is refused with
`ContainmentError`.

Existence is *not* required by containment, so `tinjis validate` can inspect a
manifest whose tree is absent. `tinjis check` adds a second layer,
`validate_declared_inputs`, which requires every declared input to exist, have
the declared type, and not itself be a symlink:

| Declaration | Expected shape |
|---|---|
| `consumers[].settings` | regular, non-symlink file |
| `consumers[].links[].source` | real, non-symlink directory |
| `selection.inventory` | regular, non-symlink file |
| `selection.source_root` | real, non-symlink directory |
| `files[].source` | regular, non-symlink file |

A shape failure raises `InputError`.

## Error types

| Exception | Raised for |
|---|---|
| `ManifestError` | Malformed JSON, duplicate keys, unknown/missing keys, bad schema, bad path grammar, duplicate or colliding declarations. |
| `ContainmentError` | A declared source that resolves outside the checkout. A subclass of `ManifestError`. |
| `InputError` | A declared authored input that is missing, mistyped, or a symlink. A subclass of `ManifestError`. |
| `TopologyError` | A well-formed manifest that is not the bundled example. A subclass of `ManifestError`. |
| `SelectionError` | A missing, malformed, duplicated, or unresolved selection inventory. |
| `OwnershipError` | A malformed ownership record, or a recorded destination outside HOME or outside the declared boundary. |

All are subclasses of `TinjisError`, which is a `ValueError`. There is no
`PlanError`: nothing in this phase performs a write, so no plan can fail during
one.

## Complete example

See [`examples/tinjis.json`](../examples/tinjis.json) and
[`examples/README.md`](../examples/README.md). It is the only manifest that
`check` plans, and it is compared against the compiled lock in
`tinjis/topology.py` on every run. Nothing in this schema is ever applied.
