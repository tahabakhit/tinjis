"""The executable topology lock.

Schema-v1 does not implement ownership migration: there is no protocol for
moving a runtime root, retiring a consumer, relocating a destination, or
rewriting a previously recorded target in another place. Tinjis therefore has
no writer, and only the bundled example is eligible for full read-only
preflight.

Parsing is generic and the *checkable* topology is locked:

* ``tinjis validate`` accepts any well-formed schema-v1 manifest and reports
  its parsed topology. It writes nothing.
* ``tinjis check`` accepts only the bundled example topology in this checkout,
  byte-for-byte at the semantic level.

The lock is a validation allowlist, never a fallback: a missing, malformed, or
topology-changing manifest is always an error. It is also compared against the
authored ``examples/tinjis.json`` on every executable run, so editing the
example without updating this constant fails closed instead of silently
redefining what Tinjis is allowed to check.
"""

from __future__ import annotations

from .errors import TopologyError
from .manifest import (
    SCHEMA,
    ConsumerConfig,
    FileProjection,
    LinkSpec,
    Manifest,
    ReviewedAction,
    RuntimeConfig,
    SelectionProjection,
)

# The bundled, executable example. Everything here is synthetic: generic
# placeholder consumers named after the tools Tinjis is meant to help
# configure, and freshly authored fixture resources under ``examples/project``.
LOCKED_EXAMPLE_TOPOLOGY = Manifest(
    schema=SCHEMA,
    runtime=RuntimeConfig(root=".config/tinjis-example"),
    consumers=(
        ConsumerConfig(
            name="pi",
            root="pi",
            settings="examples/project/pi/settings.json",
            links=(
                LinkSpec(
                    "agents/example-agent",
                    "examples/project/pi/agents/example-agent",
                ),
                LinkSpec(
                    "extensions/example-tool",
                    "examples/project/pi/extensions/example-tool",
                ),
            ),
        ),
        ConsumerConfig(
            name="hermes",
            root="hermes",
            settings="examples/project/hermes/config.overlay.yaml",
            links=(
                LinkSpec(
                    "skills/example-notes",
                    "examples/project/hermes/skills/example-notes",
                ),
            ),
        ),
        ConsumerConfig(
            name="herdr",
            root="herdr",
            settings="examples/project/herdr/config.toml",
            links=(),
        ),
    ),
    selection=SelectionProjection(
        inventory="examples/project/inventory.json",
        source_root="examples/project/shared",
        destination=".local/share/tinjis-example/shared",
    ),
    files=(
        FileProjection(
            label="herdr/status.sh",
            source="examples/project/herdr/status.sh",
            destination=".config/tinjis-example/herdr/status.sh",
        ),
    ),
    legacy_paths=("legacy-example-link",),
    reviewed_actions=(
        ReviewedAction("pi", "example-package-manager install pi"),
        ReviewedAction(None, "herdr integration install pi"),
    ),
)


def topology_mismatch(manifest: Manifest, reference: Manifest) -> str | None:
    """Name the first field that leaves the locked topology, or ``None``."""
    if manifest.runtime != reference.runtime:
        return "the runtime root"
    if tuple(entry.name for entry in manifest.consumers) != tuple(
        entry.name for entry in reference.consumers
    ):
        return "the consumer set/names"
    if manifest.consumers != reference.consumers:
        return "the consumer roots/settings/leaves"
    if manifest.selection != reference.selection:
        return "the shared selection topology"
    if manifest.files != reference.files:
        return "the file projections/destinations"
    if manifest.legacy_paths != reference.legacy_paths:
        return "the legacy path set"
    if manifest.reviewed_actions != reference.reviewed_actions:
        return "the reviewed actions"
    return None


def validate_executable_topology(
    manifest: Manifest, reference: Manifest = LOCKED_EXAMPLE_TOPOLOGY
) -> Manifest:
    """Refuse any topology that is not the locked bundled example topology."""
    field = topology_mismatch(manifest, reference)
    if field is not None:
        raise TopologyError(
            f"schema-v1 is topology-locked: {field} differs from the bundled "
            "example. Changing it requires an ownership-migration protocol "
            "that does not exist in this phase; run `tinjis validate` to "
            "inspect a candidate without checking it against runtime state."
        )
    return manifest
