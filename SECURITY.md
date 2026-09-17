# Security policy

## Reporting

Use the repository's **Security → Report a vulnerability** flow for private
reports. GitHub private vulnerability reporting is enabled. Do not open a
public issue for a vulnerability that
could expose data or credentials. Include a minimal reproduction, the commit
or content hash tested, and the affected command.

## What the Tinjis CLI can and cannot do

The Tinjis **command line** is **read-only**. It reads an authored manifest, an
authored selection inventory, a set of declared authored resources, and an
ownership record, and it prints a report. No command has a
filesystem-mutation path: no create, replace, remove, chmod, or mkdir, and no
subprocess or network call. `tinjis/cli.py` does not import the writer.

Because no command reaches a writer:

* there is no destructive-write time-of-check/time-of-use window in the CLI —
  pathname checks and reads are still separate, so every report is only a
  snapshot;
* there is no partial-failure mode in the CLI — nothing is written, so nothing
  can be half-written;
* there is no path through the CLI that can damage a live configuration.

The CLI's security surface is therefore **information**: what Tinjis reads, what
it could be tricked into reading, and what it prints.

## Internal writer foundation (tested, deliberately not exposed)

`tinjis/writer.py` is the package's one sanctioned mutation site, and no command
imports it. It exists so the journal and create primitives can be tested before
any route can act on a real HOME. Its properties:

* **Journal first, and exclusive.** `apply_transaction` writes an fsynced
  transaction to the fixed `~/.config/tinjis/journal.json` before the first
  link is touched. The journal is hard-linked into place, so a concurrent
  cooperating writer loses with `EEXIST` rather than clobbering the in-flight
  transaction.
* **Deterministic, idempotent recovery.** `recover` re-checks each destination
  and replays only the creates that are not final; it refuses when the live
  ownership record matches neither the transaction's `before` nor its `after`.
  Re-running recovery changes nothing.
* **Create only.** Normal apply preflights each leaf before publishing intent,
  then uses `os.symlink`, which fails atomically with `EEXIST`; an existing
  destination is refused, never overwritten or adopted. Recovery may accept an
  exact-target link only for a trusted journal published after that preflight,
  because creation may have completed before interruption. `retire`, `update`,
  and `conflict` plans are refused before the journal is written.
* **Narrow boundary.** Every destination is revalidated against HOME, the
  declared boundary, and the reserved `.config/tinjis` namespace immediately
  before the write; the ownership record is re-validated as a whole before it is
  written, and folded aliases are refused.
* **Fails closed.** An unexpected `fsync` or `open` error aborts the
  transaction. Handled Python exceptions attempt scratch cleanup; process death
  can leave a recognisable orphan scratch file.

`writer.py` imports no process or network module; the AST guard in
`tests/test_hygiene.py` bans those everywhere and pins the exact filesystem
calls `writer.py` may use. The foundation relies on one advisory lock for
cooperating same-UID processes. It does **not** claim protection from a hostile
same-UID actor that can rewrite state, full power-loss durability (device
caches are not forced), or that every race is eliminated. Directory `fsync`
errors fail closed; a filesystem that refuses it is unsupported.

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
* **The intent journal.** Also a regular, non-symlink file at a fixed location.
  Exact key sets, a known owner and schema, every destination inside HOME and
  the declared boundary, and a `before`/`after`/`entries` triple that must fold
  consistently. A journal whose live ownership record matches neither recorded
  map is refused rather than guessed at.
* **The checkout tree.** Every declared source must resolve inside the
  canonical checkout, including through a symlinked ancestor and through a
  dangling symlink. In the full preflight, every declared authored input must
  additionally exist, have the declared file/directory type, and not itself be
  a symlink.

## What the CLI does not do

* It does not write, create, replace, or remove anything outside the process.
* It does not import the writer foundation.
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
* A destination that is not NFC-normalised is refused by manifest and
  ownership/transaction validation. Internal writer callers remain responsible
  for deriving targets from a validated manifest and source inventory; the
  writer does not independently pin a target's future referent.
* A selected resource name that is not ASCII is refused.
* A journal or ownership record that is a symlink, or reached through a
  symlinked ancestor, is refused before it is read.

## Known limitations

* The trust boundary assumes the operator's HOME and checkout are under the
  operator's control and are not writable by another user.
* The bounded AST guards reject listed calls and imports; they do not prove the
  absence of every possible escape. `getattr` indirection would defeat them, so
  a separate test rejects `getattr` and `setattr` in the package too.
* Read-only means read-only for the CLI. A different program can still modify
  the same tree, so a `check` result is a snapshot, not a guarantee.
* The writer foundation is exercised by CI on macOS and Linux. It relies on
  atomic `symlink`/`link`/`rename`; its recovery semantics on a filesystem
  without those are not claimed.
* `create` closes the check/write window with atomic `os.symlink` `EEXIST`
  under the advisory lock. That protects cooperating same-UID processes only. A
  process that ignores the lock and can rewrite HOME state is outside the
  threat model; there is no portable stdlib compare-and-swap for a pathname.
* Windows and non-POSIX filesystem behaviour are not verified; see
  `docs/STATUS.md`.

## Reintroducing a writer

The foundation provides the journal, deterministic recovery, and an atomic
create primitive only; `retire`, `update`, and `conflict` are refused. Exposing
`apply` is still blocked on ownership migration (rewriting a recorded target), a
consumer settings applier, and a recorded decision to relax the topology lock;
those are preconditions 3–5 in `docs/STATUS.md`. Until they exist, no command
imports the writer.
