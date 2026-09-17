# Security policy

## Reporting

Use the repository's **Security → Report a vulnerability** flow for private
reports. Before publication, the maintainer must enable GitHub private
vulnerability reporting. Do not open a public issue for a vulnerability that
could expose data or credentials. Include a minimal reproduction, the commit
or content hash tested, and the affected command.

## What Tinjis v0 can and cannot do

Tinjis v0 is **read-only**. It reads an authored manifest, an authored
selection inventory, a set of declared authored resources, and an ownership
record, and it prints a report. It has no filesystem-mutation path: no create,
replace, remove, chmod, or mkdir, and no subprocess or network call. Straightforward regressions are guarded by bounded AST checks in
`tests/test_hygiene.py`, in addition to source review.

Because there is no writer:

* there is no destructive-write time-of-check/time-of-use window — pathname
  checks and reads are still separate, so every report is only a snapshot;
* there is no partial-failure mode — nothing is written, so nothing can be
  half-written;
* there is no path through this tool that can damage a live configuration.

The security surface is therefore **information**: what Tinjis reads, what it
could be tricked into reading, and what it prints.

## Inputs treated as untrusted

* **The declarative manifest.** Strict JSON, exact key sets, NFC destination
  grammar, folded collision rejection, and containment. A refused manifest
  produces a message and stops.
* **The selection inventory.** Strict JSON, one known key, safe-component
  names, folded duplicate rejection, and a mandatory `SKILL.md` marker on every
  selected resource.
* **The ownership record.** A regular, non-symlink file at the fixed Tinjis
  location (`~/.config/tinjis/owned.json`). Read strictly, with every recorded
  destination required to be absolute, normalised, inside HOME, and inside the
  exact-file or one-selection-child boundary. Case/Unicode aliases within the
  record or against a currently selected leaf are refused. A symlinked ancestor
  of the record is refused before anything is read.
* **The checkout tree.** Every declared source must resolve inside the
  canonical checkout, including through a symlinked ancestor and through a
  dangling symlink. In the full preflight, every declared authored input must
  additionally exist, have the declared file/directory type, and not itself be
  a symlink.

## What Tinjis does not do

* It does not write, create, replace, or remove anything outside the process.
* It does not install packages or run a package manager.
* It does not fetch anything from the network.
* It does not execute reviewed actions; they are opaque strings that are
  printed only.
* It does not spawn a subprocess.
* It does not store credentials, sessions, or caches, and it does not read
  them.
* It does not edit a settings file, a model configuration, or any file it does
  not exclusively own.
* It does not manage the lifecycle of another tool. Pi, Hermes, and Herdr
  remain user-managed prerequisites, and a manifest that names them configures
  none of them.

## Things that look like a vulnerability but are refusals

* A symlinked HOME is refused rather than resolved, and a symlinked ancestor
  inside HOME is refused rather than followed. Pass the resolved path.
* A declared source that is a symlink is refused even when it resolves inside
  the checkout.
* Two declarations that differ only by letter case, or only by Unicode
  normalisation, are refused as a collision, because a default macOS filesystem
  would collapse them into one name.
* A destination that is not NFC-normalised is refused.
* A selected resource name that is not ASCII is refused.

## Known limitations

* The trust boundary assumes the operator's HOME and checkout are under the
  operator's control and are not writable by another user.
* The bounded AST guards reject listed calls and imports; they do not prove the
  absence of every possible escape. `getattr` indirection would defeat them, so
  a separate test rejects `getattr` and `setattr` in the package too.
* Read-only means read-only for this tool. A different program can still modify
  the same tree, so a `check` result is a snapshot, not a guarantee.
* Linux behaviour is not verified; see `docs/STATUS.md`.

## Reintroducing a writer

Any future change that adds filesystem mutation reopens the whole topic and
must bring, at minimum, a durable journal with recovery and a mutation
primitive whose check and write cannot be separated by another process. The
preconditions are listed in `docs/STATUS.md`. Until then, no such route exists
on purpose.
