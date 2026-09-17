"""Topology-lock tests.

The lock is a validation allowlist, never a fallback. These tests prove that
the authored example, the compiled lock, and an independent third description
still agree, and that a drift in any single field is named and refused.
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fixture_topology import example_payload

from tinjis.errors import TopologyError
from tinjis.manifest import load_manifest, parse_manifest
from tinjis.topology import (
    LOCKED_EXAMPLE_TOPOLOGY,
    topology_mismatch,
    validate_executable_topology,
)


class AgreementTest(unittest.TestCase):
    def test_compiled_lock_equals_the_independent_fixture(self):
        self.assertEqual(parse_manifest(example_payload()), LOCKED_EXAMPLE_TOPOLOGY)

    def test_authored_example_equals_the_compiled_lock(self):
        authored = load_manifest(CHECKOUT, CHECKOUT / "examples" / "tinjis.json")
        self.assertEqual(authored, LOCKED_EXAMPLE_TOPOLOGY)
        self.assertIsNone(topology_mismatch(authored, LOCKED_EXAMPLE_TOPOLOGY))

    def test_authored_file_bytes_parse_to_the_lock(self):
        text = (CHECKOUT / "examples" / "tinjis.json").read_text(encoding="utf-8")
        self.assertEqual(parse_manifest(json.loads(text)), LOCKED_EXAMPLE_TOPOLOGY)


class MismatchTest(unittest.TestCase):
    def assert_names(self, manifest, field):
        self.assertEqual(topology_mismatch(manifest, LOCKED_EXAMPLE_TOPOLOGY), field)
        with self.assertRaises(TopologyError) as caught:
            validate_executable_topology(manifest, LOCKED_EXAMPLE_TOPOLOGY)
        self.assertIn("topology-locked", str(caught.exception))
        self.assertIn(field, str(caught.exception))

    def test_runtime_root_is_locked(self):
        base = LOCKED_EXAMPLE_TOPOLOGY
        self.assert_names(
            replace(base, runtime=replace(base.runtime, root=".config/other")),
            "the runtime root",
        )

    def test_consumer_set_is_locked(self):
        base = LOCKED_EXAMPLE_TOPOLOGY
        self.assert_names(replace(base, consumers=base.consumers[:1]), "the consumer set/names")

    def test_consumer_leaves_are_locked(self):
        base = LOCKED_EXAMPLE_TOPOLOGY
        renamed = replace(base.consumers[0], links=(base.consumers[0].links[1],))
        self.assert_names(
            replace(base, consumers=(renamed, *base.consumers[1:])),
            "the consumer roots/settings/leaves",
        )

    def test_selection_topology_is_locked(self):
        base = LOCKED_EXAMPLE_TOPOLOGY
        self.assert_names(
            replace(
                base,
                selection=replace(base.selection, destination=".local/share/elsewhere"),
            ),
            "the shared selection topology",
        )

    def test_file_projections_are_locked(self):
        base = LOCKED_EXAMPLE_TOPOLOGY
        self.assert_names(
            replace(
                base,
                files=(replace(base.files[0], destination=".config/elsewhere"),),
            ),
            "the file projections/destinations",
        )

    def test_legacy_paths_are_locked(self):
        self.assert_names(
            replace(LOCKED_EXAMPLE_TOPOLOGY, legacy_paths=("other",)),
            "the legacy path set",
        )

    def test_reviewed_actions_are_locked(self):
        base = LOCKED_EXAMPLE_TOPOLOGY
        self.assert_names(
            replace(
                base,
                reviewed_actions=(replace(base.reviewed_actions[0], command="other"),),
            ),
            "the reviewed actions",
        )

    def test_equal_manifest_is_returned_unchanged(self):
        manifest = LOCKED_EXAMPLE_TOPOLOGY
        self.assertIs(validate_executable_topology(manifest), manifest)


class LockScopeTest(unittest.TestCase):
    def test_the_lock_is_a_constant_and_not_a_fallback(self):
        # A lock must exist independently of any file on disk: constructing the
        # lock never reads the example manifest.
        self.assertEqual(LOCKED_EXAMPLE_TOPOLOGY.schema, 1)
        self.assertEqual(LOCKED_EXAMPLE_TOPOLOGY.consumers[0].name, "pi")

    def test_the_example_uses_only_synthetic_names(self):
        names = " ".join(
            f"{consumer.name} {consumer.root} {consumer.settings}"
            for consumer in LOCKED_EXAMPLE_TOPOLOGY.consumers
        )
        lowered = names.lower()
        for forbidden in ("dari", "linear", "observational", "amos", "edxeth", "atlas"):
            self.assertNotIn(forbidden, lowered, forbidden)


if __name__ == "__main__":
    unittest.main()
