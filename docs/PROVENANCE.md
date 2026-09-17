# Provenance

Tinjis is a new, independent repository with a clean history. Its engine was
written by adapting code that Taha Bakhit had written in an uncommitted working
tree of the Atlas project.

This document records the code/configuration source files, revision, content
hashes, and extraction treatment. The hashes let a reviewer with access to the
recorded Atlas working-tree contents reproduce the comparison; four untracked
source files cannot be reconstructed from the recorded Git revision alone.

## Authorship and licensing

Taha Bakhit authored the Atlas code listed below. His extracted contributions
are licensed here under the Apache License, Version 2.0 (see `LICENSE`).

**No license of Atlas as a whole is claimed.** The Atlas checkout has **no root
license file** at the revision examined: there is no `LICENSE`, `COPYING`, or
equivalent at its root. Some independent resources within Atlas carry their
own licenses, but none of those resources was extracted into Tinjis. Nothing in
this document should be read as a claim that Atlas is Apache-2.0 licensed or
that Tinjis inherited a repository-wide license from Atlas.

If a later review finds third-party material inside one of the listed files,
that file must be removed from Tinjis rather than relicensed. The list below is
deliberately exhaustive so that check is possible.

## Atlas revision read

| Field | Value |
|---|---|
| Committed base revision (`HEAD`) | `a7f7b15e6b4e6692861f37738b76245fda935edd` |
| Read from | The **uncommitted working tree**, not the committed tree |
| License of Atlas | None stated; see above |
| Atlas license file | Absent at the checkout root |

Two of the files read were **tracked and modified** relative to `HEAD`; one was
**tracked and clean**; four were **untracked** and exist in no Atlas commit. The
content hashes below are therefore the authoritative reference, not the commit
id.

## Files read, with independently recalculated SHA-256

Hashes were recalculated directly from the Atlas working tree with
`shasum -a 256`.

| Atlas path | Git state vs `HEAD` | SHA-256 |
|---|---|---|
| `bin/atlas_manifest.py` | untracked | `6fa6f1f2b1836a5bf58e95f3e17b79c41e5b117ed0621f085e185804088b78e2` |
| `bin/skill_selection.py` | tracked, unchanged | `dbf22c609a3ce573b2a7ed4fd138f802836d22e2745eccdb7ad4150675318c54` |
| `bin/bootstrap.py` | tracked, modified | `9848a19fd90bcd2f1c81a180e02a6ebbf8cfc27e50c6a2880c426a8d5ab0713b` |
| `bin/pi_bootstrap.py` | tracked, modified | `76b6d29efdfbaa368e9494ee975149e15a1aab1558fa30a1a9de4f2403b79e16` |
| `tests/test_atlas_manifest.py` | untracked | `6af340aa812c99a6f7393a443a1d75a1b0fcaa45826611933511962e5f6e34f1` |
| `tests/legacy_topology_fixture.py` | untracked | `2aed12c1c973b40fc5c1e1a9d9762677c4daaef9e60abec9cf68fb21e43a4bac` |
| `atlas.json` | untracked | `96faa0dc0212a2fa1dd32eb39681a42856d88697077a76fa8ed161decc9906a8` |

That is the complete list of Atlas files consulted as code or configuration
sources for this extraction. Repository instructions and metadata may have been
inspected to govern and verify the extraction, but were not copied or adapted.

## Extraction map

"Copied" means the logic and structure were carried across with renames and
message rewording. "Rewritten" means the behaviour was preserved but the shape
was generalized. Nothing was copied verbatim as a whole file.

| Atlas symbol | Tinjis location | Treatment |
|---|---|---|
| `atlas_manifest._strict_object` | `strictjson._object_pairs_hook` | rewritten; shared by every JSON document, and extended to refuse `NaN`/`Infinity` |
| `atlas_manifest._object`, `_exact_keys`, `_string`, `_array` | `manifest._object`, `_exact_keys`, `_string`, `_array` | copied; `ManifestError` replaces the Atlas-local error |
| `atlas_manifest._relative_path`, `_repo_relative`, `_runtime_relative` | `paths.normalize_relative`, `repo_relative`, `runtime_relative` | copied; parameterized by error type, and extended with the NFC rule for destinations |
| `atlas_manifest.validate_source_containment` | `manifest.validate_source_containment`, `paths.escapes_checkout` | copied; split so the path helper has no manifest knowledge |
| `atlas_manifest._RESERVED_LINK_NAMES` | `manifest.RESERVED_LEAF_NAMES`, `RESERVED_LEAF_KEYS` | rewritten; adds the Tinjis ownership names and compares case-folded |
| `atlas_manifest.LinkSpec`, `PiProfileConfig`, `HermesConfig`, `SkillsProjection`, `ProjectionFile`, `RuntimeConfig`, `ReviewedActions`, `AtlasManifest` | `manifest.LinkSpec`, `ConsumerConfig`, `SelectionProjection`, `FileProjection`, `RuntimeConfig`, `ReviewedAction`, `Manifest` | rewritten; three vendor-shaped subsystems collapsed into one provider-neutral `consumers` / `selection` / `files` model |
| `atlas_manifest._parse_*`, `parse_manifest`, `load_manifest` | `manifest._parse_*`, `parse_manifest`, `load_manifest` | rewritten for the new shape; strictness preserved field by field, with folded collision keys added |
| `atlas_manifest.LOCKED_TOPOLOGY`, `_topology_mismatch`, `validate_topology` | `topology.LOCKED_EXAMPLE_TOPOLOGY`, `topology_mismatch`, `validate_executable_topology` | rewritten; the lock is a reference manifest compared by the caller, and parsing is generic while only the bundled example is named by it |
| `pi_bootstrap._validate_leaf_path`, `_validate_leaf_paths` | `manifest._leaf_path`, `manifest._assert_no_leaf_collisions` | copied; reserved-name and ancestor-collision rules extended across every declared namespace and made case/normalization-folded |
| `bootstrap.MANIFEST_RELATIVE`, `MANIFEST_OWNER`, `MANIFEST_SCHEMA` | `ownership.OWNED_RELATIVE`, `OWNER`, `OWNERSHIP_SCHEMA` | copied; location moved to a Tinjis-owned path |
| `bootstrap._within_projection_boundary` | `ownership.within_boundary` | copied; the boundary is a `Boundary` value instead of two loose tuples |
| `bootstrap._validate_owned_destination`, `_validate_owned_target`, `read_owned`, `_ancestor_conflict` | `ownership.validate_owned_destination`, `validate_owned_target`, `read_owned`, `ancestor_conflict` | copied; messages generalized for reading only |
| `bootstrap.ProjectionPlan` | `plan.ProjectionPlan` | copied; the `writes` flag became `would_change`, since nothing writes |
| `bootstrap.projection_boundary`, `projection_pairs`, `plan_projections` | `plan.projection_boundary`, `projection_pairs`, `plan_projections` | copied; boundary and pairs derived from the generic manifest, and the inventory is read through `selection.read_selection` |
| `skill_selection.read_selection`, `_validate_skill_name`, `_validate_skill_leaf` | `selection.read_selection`, `_validate_marker` | copied; `SkillSelectionError` becomes `SelectionError`, the marker name is a constant, and duplicate detection is folded |

### Atlas code deliberately not retained

Some writer code existed briefly in the initial local extraction and was then
deleted during hardening. None of the following is retained in the current
Tinjis tree:

| Atlas symbol | Why |
|---|---|
| `bootstrap._atomic_link`, `bootstrap.apply_projections`, `bootstrap._leaf_race_error`, `bootstrap._write_manifest` | the mutation path. Tinjis v0 has no writer at all; see `STATUS.md`. |
| `pi_bootstrap.preflight`, `install`, `recover`, `adopt`, and the ownership/journal machinery | vendor-specific installer with a transaction journal. Out of scope for this phase. |
| `hermes_bootstrap` overlay editing, copied-skill projection, journaled swap | same. |
| `bootstrap` Pi/Hermes integration, dependency and pinned-cache reporting, launcher projection | vendor-specific, and depends on resources that were not extracted. |

## Deliberately not read for extraction

`resources/**` (skills, extensions, prompts, agents), `harnesses/**`,
`tools/**`, and `frameworks/**` were not copied or adapted. Several of those
trees are personal, inherited, or third-party, so no authorship or license
claim is made about them and none of their content appears here.

`atlas.json` was read only to understand the manifest shape. It names
operator-specific profiles, package pins, and resources; the bundled example was
written fresh instead.

## Newly authored for Tinjis

Written for this repository, with no Atlas counterpart:

* `tinjis/errors.py`, `strictjson.py`, `paths.py`, `selection.py`, `manifest.py`,
  `topology.py`, `ownership.py`, `plan.py`, `cli.py`, `__init__.py`,
  `__main__.py` — the content of several is derived from Atlas as mapped above;
  the module decomposition, docstrings, provider-neutral model, folded collision
  rules, declared-input shape validation, and read-only scope are new.
* `bin/tinjis`
* `examples/tinjis.json` and every file under `examples/project/`
* `tests/fixture_topology.py` and `tests/test_*.py`
* `LICENSE`, `NOTICE`, `README.md`, `SECURITY.md`, `AGENTS.md`, `.gitignore`,
  `.ruff.toml`, `docs/**`, `.github/workflows/ci.yml`

## Third-party material

None. Tinjis bundles no third-party code, tool, skill, extension, model
configuration, or package pin, and the `tinjis` package imports only the Python
standard library. `tests/test_hygiene.py` enforces the import rule and provides
recursive, bounded regression guards against straightforward filesystem
mutation and process-spawning calls in the package.

## Maintenance

When a later phase extracts more Atlas code, add a row to the hash table and a
row to the extraction map in the same change. Do not extract a file whose
authorship is uncertain, and do not relicense third-party material: author a
fixture instead.
