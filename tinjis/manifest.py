"""Schema-v1 declarative Tinjis configuration.

This module is the whole authored interface. One JSON document at a checkout
root describes:

* the generic **runtime root** (``runtime.root``), relative to HOME;
* zero or more **consumers** (``consumers``): a named tool configuration with
  its own root, an authored settings template, and projected resource leaves;
* one shared-resource **selection** (``selection``): an authored inventory, the
  resource source root it selects from, and the runtime destination the
  selected leaves are projected into;
* standalone **file projections** (``files``): one authored file to one runtime
  destination, for tool configuration that is not a resource leaf;
* **legacy paths** (``legacy_paths``): retired runtime entries reported as
  cutover leftovers, never deleted automatically;
* **reviewed actions** (``reviewed_actions``): opaque commands Tinjis prints and
  never runs.

The manifest is authored source. It records only paths, names, labels, and
opaque command strings. It never records credentials, tokens, home
directories, live runtime contents, or any other secret: every runtime
destination is expressed *relative to a runtime root*, so the same manifest
describes any operator's machine.

Strictness
----------
The file is one JSON object (schema ``1``) and is validated before any caller
sees it:

* duplicate JSON object keys are refused (never last-wins), as are the
  non-standard ``NaN``/``Infinity`` literals;
* unknown top-level, nested, and per-entry keys are refused;
* an unsupported or missing ``schema`` is refused;
* every source path must be a normalised repository-relative path (no absolute
  paths, no ``..``) that resolves inside the canonical checkout; a source
  symlink is allowed only when its canonical resolution stays inside;
* every destination path must be a normalised, NFC-normalised
  runtime-relative path;
* consumer names and legacy entry names must be single safe path components;
* duplicate consumer names, duplicate link destinations, reserved leaf names,
  ancestor/descendant leaf collisions, and duplicate projection
  destinations/labels are refused;
* every collision check compares *folded* keys (case-folded and
  NFC-normalised), so two declarations that a case-insensitive filesystem
  would collapse into one name are refused rather than reported as two paths.

The strict helpers raise :class:`tinjis.errors.ManifestError`, a ``ValueError``
subclass, so a caller can report a refused input clearly instead of proceeding
with a partial configuration.

Validation is split into three layers, so each caller states exactly what it
proves:

1. :func:`parse_manifest` -- structure, grammar, and declared collisions. No
   filesystem access, so it works on a manifest whose tree is absent.
2. :func:`validate_source_containment` -- every declared source resolves inside
   the canonical checkout. Still does not require existence.
3. :func:`validate_declared_inputs` -- every declared authored input exists,
   has the expected type, is not a symlink, and is contained. Requires the tree
   to be present, so only ``tinjis check`` runs it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import ContainmentError, InputError, ManifestError
from .ownership import OWNED_LEAF_NAME, OWNED_STATE_DIRNAME
from .paths import (
    escapes_checkout,
    fold_key,
    fold_path,
    profile_name,
    read_only_real_directory,
    read_only_regular_file,
    repo_relative,
    runtime_relative,
    safe_component,
)
from .strictjson import loads

SCHEMA = 1

# The authored manifest lives at the checkout root. It is required: a missing
# or malformed manifest is a hard error, never a silent fallback.
MANIFEST_NAME = "tinjis.json"

TOP_KEYS = frozenset(
    {
        "schema",
        "runtime",
        "consumers",
        "selection",
        "files",
        "legacy_paths",
        "reviewed_actions",
    }
)
RUNTIME_KEYS = frozenset({"root"})
CONSUMER_KEYS = frozenset({"name", "root", "settings", "links"})
LINK_KEYS = frozenset({"destination", "source"})
SELECTION_KEYS = frozenset({"inventory", "source_root", "destination"})
FILE_KEYS = frozenset({"label", "source", "destination"})
ACTION_KEYS = frozenset({"consumer", "command"})

# The settings file is written by the consumer subsystem, and the ownership
# record and its transaction state directory are Tinjis's own bookkeeping. A
# declared link must never collide with any of them, compared case-insensitively
# so `Settings.json` cannot slip past on a case-insensitive filesystem.
SETTINGS_LEAF_NAME = "settings.json"
RESERVED_LEAF_NAMES = frozenset({SETTINGS_LEAF_NAME, OWNED_LEAF_NAME, OWNED_STATE_DIRNAME})
RESERVED_LEAF_KEYS = frozenset(fold_key(name) for name in RESERVED_LEAF_NAMES)

# The two shapes an authored input may declare.
INPUT_FILE = "file"
INPUT_DIRECTORY = "directory"


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkSpec:
    """One consumer-root-relative resource leaf, projected as a symlink."""

    destination: str
    source: str


@dataclass(frozen=True)
class ConsumerConfig:
    """One declared consumer: its own root, settings template, and leaves."""

    name: str
    root: str
    settings: str
    links: tuple[LinkSpec, ...]


@dataclass(frozen=True)
class RuntimeConfig:
    """The generic runtime root, expressed relative to HOME."""

    root: str


@dataclass(frozen=True)
class SelectionProjection:
    """The shared-resource selection and its runtime destination."""

    inventory: str
    source_root: str
    destination: str


@dataclass(frozen=True)
class FileProjection:
    """One authored file projected to one runtime destination."""

    label: str
    source: str
    destination: str


@dataclass(frozen=True)
class ReviewedAction:
    """One reviewed command Tinjis prints but never runs.

    ``consumer`` is ``None`` for a command that is not consumer-scoped, or the
    name of a declared consumer so a caller can scope the environment it
    documents.
    """

    consumer: str | None
    command: str


@dataclass(frozen=True)
class Manifest:
    schema: int
    runtime: RuntimeConfig
    consumers: tuple[ConsumerConfig, ...]
    selection: SelectionProjection
    files: tuple[FileProjection, ...]
    legacy_paths: tuple[str, ...]
    reviewed_actions: tuple[ReviewedAction, ...]

    def consumer(self, name: str) -> ConsumerConfig | None:
        for entry in self.consumers:
            if entry.name == name:
                return entry
        return None


# --------------------------------------------------------------------------
# Strict readers and validators
# --------------------------------------------------------------------------


def _object(value: object, *, where: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be a JSON object")
    return value


def _array(value: object, *, where: str) -> list:
    if not isinstance(value, list):
        raise ManifestError(f"{where} must be a JSON array")
    return value


def _string(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{where} must be a non-empty string")
    return value


def _exact_keys(value: dict, allowed, *, where: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ManifestError(f"{where} has unknown keys: {', '.join(unknown)}")
    missing = sorted(set(allowed) - set(value))
    if missing:
        raise ManifestError(f"{where} is missing keys: {', '.join(missing)}")


def _leaf_path(value: object, *, where: str) -> str:
    """A consumer-root-relative leaf path, refusing reserved names.

    The reserved-name check is folded, so ``Settings.json`` is refused for the
    same reason ``settings.json`` is: a case-insensitive filesystem treats them
    as one path.
    """
    text = runtime_relative(value, where=where, error=ManifestError)
    for part in text.split("/"):
        if fold_key(part) in RESERVED_LEAF_KEYS:
            raise ManifestError(f"{where} uses a reserved leaf name: {text!r}")
    return text


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _parse_runtime(value: object, *, where: str) -> RuntimeConfig:
    obj = _object(value, where=where)
    _exact_keys(obj, RUNTIME_KEYS, where=where)
    root = runtime_relative(obj["root"], where=f"{where}.root", error=ManifestError)
    return RuntimeConfig(root)


def _parse_link(value: object, *, where: str) -> LinkSpec:
    obj = _object(value, where=where)
    _exact_keys(obj, LINK_KEYS, where=where)
    return LinkSpec(
        destination=_leaf_path(obj["destination"], where=f"{where}.destination"),
        source=repo_relative(obj["source"], where=f"{where}.source", error=ManifestError),
    )


def _assert_no_leaf_collisions(leaves: list, *, where: str) -> None:
    """Refuse duplicate leaves and a leaf that is an ancestor of another leaf.

    A leaf that is also an ancestor would make every path below it follow the
    declared name, so the two can never coexist in one manifest. ``leaves`` is
    an ordered sequence of ``(path, label)`` pairs; a duplicate is refused
    rather than last-wins, because a silent overwrite here would hide exactly
    the cross-namespace collision this check exists to catch.

    Comparison uses :func:`tinjis.paths.fold_path`, so ``Alpha``/``alpha`` and
    an NFC/NFD pair are collisions. On a default macOS filesystem they are one
    name, and reporting two would mean planning two paths that cannot both
    exist.
    """
    seen: dict = {}
    for leaf, label in leaves:
        key = fold_path(leaf)
        if key in seen:
            other, other_label = seen[key]
            raise ManifestError(
                f"{where} declares the same leaf path twice: {leaf!r} ({label}) "
                f"collides with {other!r} ({other_label}) after case and Unicode "
                "normalization folding"
            )
        seen[key] = (leaf, label)
    for key, (leaf, label) in seen.items():
        for index in range(1, len(key)):
            ancestor = key[:index]
            if ancestor in seen:
                ancestor_leaf, ancestor_label = seen[ancestor]
                raise ManifestError(
                    f"{where} declares an ancestor/descendant collision: "
                    f"{ancestor_leaf!r} ({ancestor_label}) is an ancestor of "
                    f"{leaf!r} ({label})"
                )


def _parse_consumers(value: object, *, where: str) -> tuple:
    entries = _array(value, where=where)
    if not entries:
        raise ManifestError(f"{where} must declare at least one consumer")
    consumers: list = []
    names: dict = {}
    for index, entry in enumerate(entries):
        item = f"{where}[{index}]"
        obj = _object(entry, where=item)
        _exact_keys(obj, CONSUMER_KEYS, where=item)
        name = profile_name(obj["name"], where=f"{item}.name", error=ManifestError)
        key = fold_key(name)
        if key in names:
            raise ManifestError(
                f"{where} declares a duplicate consumer name: {name!r} collides with {names[key]!r}"
            )
        names[key] = name
        root = runtime_relative(obj["root"], where=f"{item}.root", error=ManifestError)
        settings = repo_relative(obj["settings"], where=f"{item}.settings", error=ManifestError)
        links_value = _array(obj["links"], where=f"{item}.links")
        links: list = []
        destinations: list = []
        for link_index, link in enumerate(links_value):
            link_where = f"{item}.links[{link_index}]"
            parsed = _parse_link(link, where=link_where)
            destinations.append((parsed.destination, link_where))
            links.append(parsed)
        _assert_no_leaf_collisions(destinations, where=item)
        consumers.append(ConsumerConfig(name, root, settings, tuple(links)))
    return tuple(consumers)


def _parse_selection(value: object, *, where: str) -> SelectionProjection:
    obj = _object(value, where=where)
    _exact_keys(obj, SELECTION_KEYS, where=where)
    return SelectionProjection(
        inventory=repo_relative(obj["inventory"], where=f"{where}.inventory", error=ManifestError),
        source_root=repo_relative(
            obj["source_root"], where=f"{where}.source_root", error=ManifestError
        ),
        destination=runtime_relative(
            obj["destination"], where=f"{where}.destination", error=ManifestError
        ),
    )


def _parse_files(value: object, *, where: str) -> tuple:
    entries = _array(value, where=where)
    files: list = []
    labels: dict = {}
    destinations: dict = {}
    for index, entry in enumerate(entries):
        item = f"{where}[{index}]"
        obj = _object(entry, where=item)
        _exact_keys(obj, FILE_KEYS, where=item)
        label = _string(obj["label"], where=f"{item}.label")
        label_key = fold_key(label)
        if label_key in labels:
            raise ManifestError(
                f"{where} declares a duplicate projection label: {label!r} "
                f"collides with {labels[label_key]!r}"
            )
        labels[label_key] = label
        destination = runtime_relative(
            obj["destination"], where=f"{item}.destination", error=ManifestError
        )
        destination_key = fold_path(destination)
        if destination_key in destinations:
            raise ManifestError(
                f"{where} declares a duplicate projection destination: "
                f"{destination!r} collides with {destinations[destination_key]!r}"
            )
        destinations[destination_key] = destination
        files.append(
            FileProjection(
                label=label,
                source=repo_relative(obj["source"], where=f"{item}.source", error=ManifestError),
                destination=destination,
            )
        )
    return tuple(files)


def _parse_legacy_paths(value: object, *, where: str) -> tuple:
    entries = _array(value, where=where)
    paths: list = []
    seen: dict = {}
    for index, entry in enumerate(entries):
        item = f"{where}[{index}]"
        name = safe_component(entry, where=item, error=ManifestError)
        key = fold_key(name)
        if key in seen:
            raise ManifestError(
                f"{where} declares a duplicate legacy path: {name!r} collides with {seen[key]!r}"
            )
        seen[key] = name
        paths.append(name)
    return tuple(paths)


def _parse_actions(value: object, *, where: str, consumer_names: set) -> tuple:
    entries = _array(value, where=where)
    actions: list = []
    for index, entry in enumerate(entries):
        item = f"{where}[{index}]"
        obj = _object(entry, where=item)
        _exact_keys(obj, ACTION_KEYS, where=item)
        consumer = obj["consumer"]
        if consumer is not None:
            consumer = profile_name(consumer, where=f"{item}.consumer", error=ManifestError)
            if consumer not in consumer_names:
                raise ManifestError(f"{item}.consumer names an undeclared consumer: {consumer!r}")
        command = _string(obj["command"], where=f"{item}.command")
        if not command.strip():
            raise ManifestError(f"{item}.command must contain a non-whitespace character")
        actions.append(ReviewedAction(consumer=consumer, command=command))
    return tuple(actions)


def _declared_leaves(manifest: Manifest) -> list:
    """Every concrete runtime leaf the manifest declares, as ``(path, label)``."""
    runtime_root = manifest.runtime.root
    leaves: list = []
    for entry in manifest.files:
        leaves.append((entry.destination, f"files[{entry.label}]"))
    for consumer in manifest.consumers:
        base = f"{runtime_root}/{consumer.root}"
        leaves.append((f"{base}/{SETTINGS_LEAF_NAME}", f"consumers[{consumer.name}].settings"))
        for link in consumer.links:
            leaves.append(
                (
                    f"{base}/{link.destination}",
                    f"consumers[{consumer.name}].links[{link.destination}]",
                )
            )
    return leaves


def validate_semantic_collisions(manifest: Manifest) -> None:
    """Refuse any two declared leaves that share or nest a path.

    This spans namespaces on purpose: a file projection, a consumer settings
    file, and a consumer link name paths the same way, so a collision between
    them is just as unsafe as one inside a single namespace. The shared
    selection destination is a directory of direct children, so no concrete
    leaf may equal it, sit under it, or contain it.

    Every comparison is folded, so a case-only or NFC/NFD difference is a
    collision rather than a second distinct path that the filesystem would
    collapse.
    """
    leaves = _declared_leaves(manifest)
    _assert_no_leaf_collisions(leaves, where=MANIFEST_NAME)
    destination_text = manifest.selection.destination
    destination = fold_path(destination_text)
    for leaf, label in leaves:
        key = fold_path(leaf)
        if key == destination:
            raise ManifestError(
                f"the selection destination {destination_text!r} is also declared as "
                f"a leaf by {label}"
            )
        if key[: len(destination)] == destination:
            raise ManifestError(
                f"{label} declares {leaf!r}, which is inside the selection "
                f"destination {destination_text!r}"
            )
        if destination[: len(key)] == key:
            raise ManifestError(
                f"{label} declares {leaf!r}, which contains the selection "
                f"destination {destination_text!r}"
            )


def parse_manifest(payload: object, *, where: str = "declarative manifest") -> Manifest:
    """Validate one already-decoded payload into the typed model.

    Structural validation and semantic collision validation only. The
    filesystem-level source-containment check lives in :func:`load_manifest`,
    which has the checkout root.
    """
    obj = _object(payload, where=where)
    _exact_keys(obj, TOP_KEYS, where=where)
    schema = obj["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise ManifestError(f"{where}.schema must be an integer")
    if schema != SCHEMA:
        raise ManifestError(
            f"unsupported declarative manifest schema {schema!r}; expected {SCHEMA}"
        )
    consumers = _parse_consumers(obj["consumers"], where=f"{where}.consumers")
    consumer_names = {entry.name for entry in consumers}
    manifest = Manifest(
        schema=schema,
        runtime=_parse_runtime(obj["runtime"], where=f"{where}.runtime"),
        consumers=consumers,
        selection=_parse_selection(obj["selection"], where=f"{where}.selection"),
        files=_parse_files(obj["files"], where=f"{where}.files"),
        legacy_paths=_parse_legacy_paths(obj["legacy_paths"], where=f"{where}.legacy_paths"),
        reviewed_actions=_parse_actions(
            obj["reviewed_actions"],
            where=f"{where}.reviewed_actions",
            consumer_names=consumer_names,
        ),
    )
    validate_semantic_collisions(manifest)
    return manifest


# --------------------------------------------------------------------------
# Checkout source containment and declared-input shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSpec:
    """One authored input the manifest declares, with its expected shape."""

    where: str
    relative: str
    kind: str


def declared_inputs(manifest: Manifest) -> tuple:
    """Every authored input the manifest declares, in declaration order.

    This is the single source of truth for which repository paths the manifest
    claims to read. Containment and shape validation both walk it, so a new
    source field cannot be added to one check and forgotten in the other.
    """
    inputs: list = []
    for consumer in manifest.consumers:
        inputs.append(
            SourceSpec(f"consumers[{consumer.name}].settings", consumer.settings, INPUT_FILE)
        )
        for link in consumer.links:
            inputs.append(
                SourceSpec(
                    f"consumers[{consumer.name}].links[{link.destination}].source",
                    link.source,
                    INPUT_DIRECTORY,
                )
            )
    inputs.append(SourceSpec("selection.inventory", manifest.selection.inventory, INPUT_FILE))
    inputs.append(
        SourceSpec("selection.source_root", manifest.selection.source_root, INPUT_DIRECTORY)
    )
    for entry in manifest.files:
        inputs.append(SourceSpec(f"files[{entry.label}].source", entry.source, INPUT_FILE))
    return tuple(inputs)


def repo_sources(manifest: Manifest):
    """Every repository-relative source the manifest declares, with a label."""
    for spec in declared_inputs(manifest):
        yield spec.where, spec.relative


def validate_source_containment(checkout: Path, manifest: Manifest) -> None:
    """Require every declared source to resolve inside the canonical checkout.

    ``Path.resolve`` follows symlinks, so a symlinked leaf or a symlinked
    ancestor that points outside the checkout is refused. A source that does
    not exist yet is still checked lexically after resolution, which lets a
    caller report a missing source later without opening an escape: a dangling
    symlink to an outside path is refused here.

    Existence is deliberately *not* required, so ``tinjis validate`` can
    inspect a manifest whose tree is not present on this machine. Use
    :func:`validate_declared_inputs` when the tree must be there.
    """
    for where, relative in repo_sources(manifest):
        escaped = escapes_checkout(checkout, relative)
        if escaped is not None:
            raise ContainmentError(
                f"{where} resolves outside the Tinjis checkout: {relative!r} -> {escaped}"
            )


def validate_declared_inputs(checkout: Path, manifest: Manifest) -> None:
    """Require every declared authored input to exist with its expected shape.

    Structural validation and containment answer "is this declaration safe?".
    This answers "is the artefact actually there, of the type the manifest
    says, and not a symlink?", which only a present checkout can answer. It is
    therefore a separate layer that ``tinjis check`` runs and ``tinjis
    validate`` does not.

    A declared input must be a real, non-symlink file or directory. A symlink
    is refused even when it resolves inside the checkout, because the reviewed
    name must name the artefact itself rather than something that currently
    points at it.
    """
    validate_source_containment(checkout, manifest)
    for spec in declared_inputs(manifest):
        path = checkout / spec.relative
        if spec.kind == INPUT_FILE:
            read_only_regular_file(path, where=spec.where, error=InputError)
        else:
            read_only_real_directory(path, where=spec.where, error=InputError)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def read_manifest_file(path: Path) -> Manifest:
    """Read and strictly validate one authored manifest file (no containment)."""
    if path.is_symlink():
        raise ManifestError(f"declarative manifest must be a regular file, not a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ManifestError(f"declarative manifest is not a regular file: {path}")
    if not path.is_file():
        raise ManifestError(f"declarative manifest is missing: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"cannot read declarative manifest {path}: {exc}") from exc
    return parse_manifest(loads(text, where=str(path), error=ManifestError), where=path.name)


def load_manifest(checkout: Path, path: Path | None = None) -> Manifest:
    """Read and strictly validate the required authored manifest file.

    Missing, malformed, unsupported, or escaping-source manifests are all hard
    errors; there is no built-in fallback.
    """
    manifest = path if path is not None else checkout / MANIFEST_NAME
    parsed = read_manifest_file(manifest)
    validate_source_containment(checkout, parsed)
    return parsed


def manifest_to_payload(manifest: Manifest) -> dict:
    """Re-serialise a parsed manifest into its authored JSON shape.

    Used by the CLI to print the bundled example and by tests to compare two
    manifests structurally instead of by dataclass identity.
    """
    return {
        "schema": manifest.schema,
        "runtime": {"root": manifest.runtime.root},
        "consumers": [
            {
                "name": consumer.name,
                "root": consumer.root,
                "settings": consumer.settings,
                "links": [
                    {"destination": link.destination, "source": link.source}
                    for link in consumer.links
                ],
            }
            for consumer in manifest.consumers
        ],
        "selection": {
            "inventory": manifest.selection.inventory,
            "source_root": manifest.selection.source_root,
            "destination": manifest.selection.destination,
        },
        "files": [
            {
                "label": entry.label,
                "source": entry.source,
                "destination": entry.destination,
            }
            for entry in manifest.files
        ],
        "legacy_paths": list(manifest.legacy_paths),
        "reviewed_actions": [
            {"consumer": entry.consumer, "command": entry.command}
            for entry in manifest.reviewed_actions
        ],
    }


def dumps_manifest(manifest: Manifest) -> str:
    return json.dumps(manifest_to_payload(manifest), indent=2) + "\n"
