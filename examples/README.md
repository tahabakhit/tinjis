# Examples

`tinjis.json` is the bundled example topology. It declares three synthetic
consumers named after the tools Tinjis is meant to help configure — Pi, Hermes,
and Herdr — plus one shared selection and one file projection.

**These are placeholders, not real tool configuration.** They were written for
this repository so the validator has something concrete to check. They do not
describe Pi's, Hermes's, or Herdr's actual file formats, they are not intended to
be copied into a live setup, and declaring them does not configure anything:
Tinjis v0 is read-only and never applies a manifest.

| Path | Role |
|---|---|
| `tinjis.json` | The manifest. Locked to the compiled topology; see `docs/ARCHITECTURE.md`. |
| `project/inventory.json` | The selection inventory: which shared resources are named. |
| `project/shared/` | Selected resources, each a directory with a `SKILL.md` marker. |
| `project/pi/` | A consumer settings template, one agent leaf, one extension leaf. |
| `project/hermes/` | A consumer settings template and one consumer-private resource. |
| `project/herdr/` | A consumer settings template and a standalone declared file. |

## What `check` reports

`check` plans the bundled example against a runtime root and prints what a
writer *would* do:

```
$HOME/.local/share/tinjis-example/shared/example-checklist   create
$HOME/.local/share/tinjis-example/shared/example-report      create
$HOME/.config/tinjis-example/herdr/status.sh                create
```

Nothing is created. A fresh runtime root therefore stays out of sync, `check`
exits non-zero, and it says so:

```
out of sync: the declarations are not reflected at this runtime root
         Tinjis v0 is read-only; there is no command that applies this plan.
```

The consumer roots (`$HOME/.config/tinjis-example/pi`, `.../hermes`, `.../herdr`)
are never even planned: consumer settings and links are declared and validated
only, and `check` prints them as `declared` lines that say Tinjis does not
configure the consumer.

## Writing your own

You can `validate` any well-formed schema-v1 manifest you like:

```sh
python3 bin/tinjis validate --manifest /path/to/your/tinjis.json
```

`validate` writes nothing, does not require the authored sources to exist, and
does not require your topology to match the bundled example. It reports whether
the manifest is the bundled example, which today is the only topology `check`
will plan. See `docs/STATUS.md` and `docs/MANIFEST.md`.
