# Releasing Tinjis

This checklist prepares and publishes a Tinjis release. Nothing here runs
automatically: Tinjis has no release workflow, no tag- or release-creating
automation, and no workflow with write permissions. A maintainer runs each step
and records the result. If a gate fails or cannot be run, stop and report it
rather than skipping it.

The release candidate is identified by `__version__` in `tinjis/__init__.py`
(currently `0.1.0`). A release is an annotated Git tag plus, optionally, a
hosted release page. Neither exists until a maintainer creates it.

## 0. Confirm scope

- The CLI is **read-only**: no `apply` command, no filesystem-mutation path, and
  no command imports `tinjis/writer.py`.
- `CHANGELOG.md` describes the release contents and the read-only scope.
- No package manifest, dependency, lockfile, build step, or release automation
  is introduced by the release.

## 1. Fresh clone and clean tree

Start from a clean clone of the exact commit to release, not a long-lived
working tree:

```sh
git clone <checkout-url> tinjis-release
cd tinjis-release
git status --short          # must be empty
git rev-parse HEAD          # record this commit
```

Every later check runs against this clean clone.

## 2. Python versions

The claimed range is Python 3.11 or newer; the CI matrix is configured to test
3.11, 3.12, 3.13, and 3.14. Run the suite on each version available:

```sh
for v in 3.11 3.12 3.13 3.14; do
  "python$v" -m unittest discover --start-directory tests --verbose || exit 1
done
```

Record the interpreter versions actually exercised. Do not claim a version that
was not run; release notes name only the tested versions.

## 3. Tests, lint, and format

```sh
python3 -m unittest discover --start-directory tests --verbose
```

A test failure is a blocker. Ruff is optional and deliberately not wired into
CI, so a clone stays testable with nothing installed; run it only if installed:

```sh
ruff check .
ruff format --check .
```

A Ruff finding is a maintainer decision, not an automatic release blocker.

## 4. Secret scans: working tree and history

Scan the complete working tree and the complete Git history. A clean history is
required for publication:

```sh
gitleaks dir . --no-banner --redact
gitleaks git . --no-banner --redact
```

Both commands must exit 0. Record each command and its exit status.

## 5. CI evidence

For the exact commit, record a green CI run on every row of the matrix: macOS
and Linux, on Python 3.11, 3.12, 3.13, and 3.14. Linux support, and any version
without a recorded green run, stays unclaimed in `README.md` and
`docs/STATUS.md`. A matrix definition alone is not evidence; do not flip a
support claim from a configuration file.

## 6. Security reporting

Before publication, enable private vulnerability reporting in the repository's
security settings so the `SECURITY.md` flow works. Confirm a test private report
can be filed and closed. Do not publish with public-only reporting.

## 7. Version and tag consistency

- `tinjis/__init__.py` `__version__` equals the intended release version.
- `CHANGELOG.md` has an entry for that version; when tagged, replace
  "Unreleased" with the release date.
- `python3 bin/tinjis --version` reports the same version.
- The tag name matches the version, for example `v0.1.0`.

## 8. Tag: an owner decision

Creating and pushing the tag is the owner's decision.

- Choose an **annotated** tag (`git tag -a`) unless the owner specifically
  requires a **signed** tag (`git tag -s`). Signing needs a configured key and
  is a maintainer identity decision, not a repository requirement.
- Tag the reviewed commit, not a later one: `git tag -a v0.1.0 <commit>`.
- Push the tag only after the owner confirms. No automation may create or push
  a tag.

## 9. Hosted release: explicit confirmation required

Only after the owner explicitly confirms, create the hosted release from the
pushed annotated tag:

1. Create a release page from tag `v0.1.0`.
2. Use the matching `CHANGELOG.md` entry as the release notes.
3. Attach no build artifacts: Tinjis is clone-run and stdlib-only.
4. Publish, then verify the tag, the notes, and the recorded scans.

Do not create a release workflow, grant a workflow write permission, or let
automation tag or publish. If any gate above failed or is unrecorded, stop and
report instead of publishing.
