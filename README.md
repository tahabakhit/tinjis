# Tinjis

A small, stdlib-only, **read-only CLI** for declarative configuration
manifests.

Tinjis parses one authored JSON manifest, refuses anything malformed or
ambiguous, and reports the *plan* a future writer would have to carry out. No
command carries it out: there is no `apply` command and the CLI has no
filesystem-mutation path. A tested, CLI-unreachable writer foundation exists
under `tinjis/writer.py` for a later phase; see
[`docs/STATUS.md`](docs/STATUS.md) for exactly what it does and why `apply` is
still withheld.

The tools a manifest can name — Pi, Hermes, Herdr, or anything else — are
**not configured by Tinjis**. A manifest can declare a consumer, its settings
template, and its resource links; Tinjis validates and reports those
declarations and configures nothing. All external tools remain user-managed
prerequisites.

## Status

Early extraction, read-only. In short:

* **Operational** — strict manifest parsing, NFC path grammar, folded
  (case-insensitive and Unicode-normalised) collision rejection,
  selection-destination containment, checkout source containment, declared-input
  existence/type/symlink validation, selection inventories, ownership-record
  reading, and read-only planning.
* **Read-only by construction** — no `apply`, and no command imports the
  writer. A create-only, journaled writer foundation exists but is deliberately
  CLI-unreachable; see `docs/STATUS.md`.
* **Scaffolded** — declared consumers and their settings/links are validated and
  reported, never applied.

## Requirements

* Python 3.11 or newer. The CI matrix is configured to test 3.11, 3.12, 3.13,
  and 3.14; no green run is claimed until one is recorded.
* A checkout. There is nothing to install: no package manager, no installer,
  no lockfile, no runtime dependency.

## Running from a clone

```sh
python3 bin/tinjis --help
python3 bin/tinjis validate
```

Or, from the checkout root:

```sh
python3 -B -m tinjis --help
```

`bin/tinjis` works from any directory and is the write-free path: it puts the
checkout root on `sys.path` and disables bytecode writing before importing
anything. `python3 -m tinjis` without `-B` lets the interpreter cache the
package's own `__init__` module before Tinjis can disable caching, so pass `-B`
if the checkout must stay byte-for-byte unchanged.

## Commands

| Command | Writes | What it does |
|---|---|---|
| `validate [--manifest PATH]` | nothing | Strict JSON, structural, NFC and folded-collision, and source-containment checks on any schema-v1 manifest. Reports whether the manifest is the bundled example. Does not require the authored sources to exist, so a manifest can be inspected on a machine that does not have the tree. |
| `check [--home DIR]` | nothing | The full read-only preflight of the bundled example: declared-input existence, type, and symlink policy; resolved selection leaves; the ownership record; and the plan. Exits non-zero when anything is out of sync. This is the default command. |
| `example [--raw]` | nothing | Prints the bundled example manifest and whether it still matches the compiled topology lock. |

There is no command that applies a plan. An internal writer foundation exists in
`tinjis/writer.py` (create-only, with a journal and idempotent recovery), but the
CLI does not import it, so no command can reach it.

### A read-only walkthrough

```sh
tmp=$(mktemp -d)/home && mkdir -p "$tmp"
python3 bin/tinjis validate            # the declaration is well formed
python3 bin/tinjis check --home "$tmp" # exits 1: not reflected there; writes nothing
find "$tmp"                            # still empty
```

`check` reports what a writer would do for file and selected-resource
projections and refuses to pretend it did it. Against a separately populated,
matching tree it reports that scoped projection set as `in sync`; consumer
settings and links are declarations only and are not runtime-compared.

## Manifest in one screen

```json
{
  "schema": 1,
  "runtime": { "root": ".config/tinjis-example" },
  "consumers": [
    { "name": "pi", "root": "pi",
      "settings": "examples/project/pi/settings.json",
      "links": [{ "destination": "agents/example-agent",
                  "source": "examples/project/pi/agents/example-agent" }] }
  ],
  "selection": {
    "inventory": "examples/project/inventory.json",
    "source_root": "examples/project/shared",
    "destination": ".local/share/tinjis-example/shared"
  },
  "files": [
    { "label": "herdr/status.sh", "source": "examples/project/herdr/status.sh",
      "destination": ".config/tinjis-example/herdr/status.sh" }
  ],
  "legacy_paths": ["legacy-example-link"],
  "reviewed_actions": [{ "consumer": "pi", "command": "example-package-manager install pi" }]
}
```

Every `source` is relative to the checkout; every `destination` is relative to
a runtime root. The full reference is in [`docs/MANIFEST.md`](docs/MANIFEST.md).

## Safety model

1. **Read before report.** Every destination is planned read-only. A conflict
   anywhere is reported and nothing changes, because nothing can.
2. **Ownership is recorded, never inferred.** An existing symlink that already
   points at the right place is still a conflict unless the ownership record
   names its exact previous target. The record is treated as untrusted input: it
   is read strictly and validated against a narrow boundary.
3. **Narrow boundary.** The declarations Tinjis reasons about are the file
   projections, one direct child per selected resource, and the consumer
   leaves — nothing else.
4. **Fail closed.** Missing, malformed, duplicated, unknown, traversing,
   escaping, colliding, case-colliding, non-NFC, mistyped, or symlinked input is
   an error. There is no fallback manifest.
5. **Nothing runs.** Reviewed actions are printed, never executed. The CLI
   imports no writer and the package has no network, package-install, or
   subprocess path; the one mutation module (`tinjis/writer.py`) is not
   reachable from any command. Recursive bounded AST checks guard
   straightforward regressions; they are not a general proof of Python
   behavior.

Details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`SECURITY.md`](SECURITY.md).

## Development

The whole suite runs on a bare Python installation:

```sh
python3 -m unittest discover --start-directory tests --verbose
```

Optional, not required by CI: `.ruff.toml` describes the lint and format rules
used while authoring this checkout, if you happen to have `ruff` installed.
There is no runtime dependency, no packaging manifest, and no build step.

Three descriptions of the bundled topology must stay in sync — the authored
`examples/tinjis.json`, the compiled `LOCKED_EXAMPLE_TOPOLOGY` in
`tinjis/topology.py`, and the independent `tests/fixture_topology.py`. A drift in
any one of them fails a test rather than silently redefining what Tinjis
accepts. See [`AGENTS.md`](AGENTS.md) before changing any of them.

## Platform support

* **macOS** — primary; the suite runs here on Python 3.12 and 3.14, and CI is
  configured to test 3.11 through 3.14.
* **Linux (POSIX)** — **not verified.** The CI matrix includes Linux, but no
  green Linux run has been recorded, so no support is claimed.
* **Windows** — deferred. Nothing is claimed.

## Examples

`examples/tinjis.json` declares three synthetic consumers named after Pi,
Hermes, and Herdr, plus two shared resources and one file projection. The
resources are newly authored placeholders, not real tool configuration, and
declaring them does not configure anything. See
[`examples/README.md`](examples/README.md).

## Releases, security, and contributing

* Release status and contents: [`CHANGELOG.md`](CHANGELOG.md).
* Release procedure and gates: [`docs/RELEASING.md`](docs/RELEASING.md).
* Security model and private reporting: [`SECURITY.md`](SECURITY.md).
* Development, tests, and review rules: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
Provenance of the adapted code, including what Atlas is and is not licensed
under, is recorded in [`docs/PROVENANCE.md`](docs/PROVENANCE.md).
