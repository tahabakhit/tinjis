"""Strict parsing, semantic collision, and source-containment tests.

No test reads a live configuration, installs a package, touches the network,
calls a model, or inspects credentials.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unicodedata
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fixture_topology import example_payload, write_example_tree

from tinjis import manifest as mf
from tinjis.errors import ContainmentError, InputError, ManifestError
from tinjis.strictjson import loads as strict_loads


def payload() -> dict:
    return example_payload()


class ParseTest(unittest.TestCase):
    def test_authored_example_matches_independent_fixture(self):
        authored = mf.load_manifest(CHECKOUT, CHECKOUT / "examples" / "tinjis.json")
        self.assertEqual(authored, mf.parse_manifest(payload()))
        self.assertEqual(mf.manifest_to_payload(authored), payload())

    def test_example_manifest_is_not_a_symlink_and_is_a_regular_file(self):
        path = CHECKOUT / "examples" / "tinjis.json"
        self.assertFalse(path.is_symlink())
        self.assertTrue(path.is_file())

    # -- strict JSON -------------------------------------------------------

    def test_duplicate_top_level_key_is_refused(self):
        text = json.dumps(payload())
        duplicated = text.replace('{"schema": 1', '{"schema": 1, "schema": 1', 1)
        with self.assertRaises(ManifestError) as caught:
            strict_loads(duplicated, where="test", error=ManifestError)
        self.assertIn("duplicate JSON key", str(caught.exception))

    def test_duplicate_keys_are_refused_by_the_loader_not_last_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve()
            path = checkout / mf.MANIFEST_NAME
            path.write_text(
                '{"schema": 1, "schema": 1, "runtime": {"root": ".a"},'
                ' "consumers": [], "selection": {}, "files": [],'
                ' "legacy_paths": [], "reviewed_actions": []}',
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError) as caught:
                mf.load_manifest(checkout)
            self.assertIn("duplicate JSON key", str(caught.exception))

    def test_non_finite_literal_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve()
            path = checkout / mf.MANIFEST_NAME
            path.write_text('{"schema": NaN}', encoding="utf-8")
            with self.assertRaises(ManifestError):
                mf.load_manifest(checkout)

    def test_malformed_json_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve()
            (checkout / mf.MANIFEST_NAME).write_text('{"schema": 1,}', encoding="utf-8")
            with self.assertRaises(ManifestError):
                mf.load_manifest(checkout)

    def test_missing_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ManifestError) as caught:
                mf.load_manifest(Path(temp).resolve())
            self.assertIn("missing", str(caught.exception))

    def test_symlinked_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve()
            real = checkout / "real.json"
            real.write_text(json.dumps(payload()), encoding="utf-8")
            (checkout / mf.MANIFEST_NAME).symlink_to(real)
            with self.assertRaises(ManifestError):
                mf.load_manifest(checkout)

    # -- schema ------------------------------------------------------------

    def test_unsupported_schema_is_refused(self):
        broken = payload()
        broken["schema"] = 2
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_missing_schema_is_refused(self):
        broken = payload()
        del broken["schema"]
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_boolean_schema_is_refused(self):
        broken = payload()
        broken["schema"] = True
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_unknown_top_level_key_is_refused(self):
        broken = payload()
        broken["extra"] = True
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("unknown keys", str(caught.exception))

    def test_unknown_nested_key_is_refused(self):
        broken = payload()
        broken["runtime"]["extra"] = ".cache"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_missing_nested_key_is_refused(self):
        broken = payload()
        del broken["selection"]["inventory"]
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("missing keys", str(caught.exception))

    def test_consumer_must_be_an_object(self):
        broken = payload()
        broken["consumers"] = [[]]
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_empty_consumers_is_refused(self):
        broken = payload()
        broken["consumers"] = []
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    # -- path grammar ------------------------------------------------------

    def test_absolute_source_is_refused(self):
        broken = payload()
        broken["consumers"][0]["links"][0]["source"] = "/etc/passwd"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_traversal_source_is_refused(self):
        broken = payload()
        broken["consumers"][0]["settings"] = "../outside.json"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_unnormalized_source_is_refused(self):
        broken = payload()
        broken["consumers"][0]["settings"] = "examples/./project/pi/settings.json"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_absolute_destination_is_refused(self):
        broken = payload()
        broken["files"][0]["destination"] = "/etc/example.toml"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_traversal_destination_is_refused(self):
        broken = payload()
        broken["selection"]["destination"] = "../share"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_control_character_path_is_refused(self):
        broken = payload()
        broken["runtime"]["root"] = ".config\n.tinjis"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_backslash_path_is_refused(self):
        broken = payload()
        broken["runtime"]["root"] = "..\\escape"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    # -- names and duplicates ---------------------------------------------

    def test_duplicate_consumer_name_is_refused(self):
        broken = payload()
        broken["consumers"][1]["name"] = broken["consumers"][0]["name"]
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_unsafe_consumer_name_is_refused(self):
        broken = payload()
        broken["consumers"][0]["name"] = "../escape"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_duplicate_link_destination_is_refused(self):
        broken = payload()
        broken["consumers"][0]["links"].append(dict(broken["consumers"][0]["links"][0]))
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_duplicate_file_destination_is_refused(self):
        broken = payload()
        broken["files"].append(dict(broken["files"][0], label="other"))
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_duplicate_file_label_is_refused(self):
        broken = payload()
        broken["files"].append(dict(broken["files"][0], destination=".config/other"))
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_duplicate_legacy_path_is_refused(self):
        broken = payload()
        broken["legacy_paths"] = ["same", "same"]
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_unsafe_legacy_path_is_refused(self):
        broken = payload()
        broken["legacy_paths"] = ["../escape"]
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_reserved_settings_leaf_is_refused(self):
        broken = payload()
        broken["consumers"][0]["links"].append(
            {"destination": "settings.json", "source": "examples/project/pi/settings.json"}
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("reserved", str(caught.exception))

    def test_reserved_ownership_leaf_component_is_refused(self):
        broken = payload()
        broken["consumers"][0]["links"].append(
            {"destination": "nested/owned.json", "source": "examples/project/pi/settings.json"}
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("reserved", str(caught.exception))

    def test_ancestor_descendant_link_collision_is_refused(self):
        broken = payload()
        broken["consumers"][0]["links"].append(
            {"destination": "agents", "source": "examples/project/pi/settings.json"}
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("ancestor/descendant", str(caught.exception))

    # -- folded (case and Unicode) collisions -----------------------------

    def test_case_only_link_collision_is_refused(self):
        broken = payload()
        broken["consumers"][0]["links"].append(
            {
                "destination": "agents/Example-Agent",
                "source": "examples/project/pi/agents/example-agent",
            }
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("folding", str(caught.exception))

    def test_case_only_cross_namespace_collision_is_refused(self):
        broken = payload()
        broken["files"].append(
            {
                "label": "shout",
                "source": "examples/project/herdr/status.sh",
                "destination": ".config/tinjis-example/pi/agents/Example-Agent",
            }
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("folding", str(caught.exception))

    def test_nfd_file_destination_is_refused_by_the_grammar(self):
        decomposed = unicodedata.normalize("NFD", "caf\u00e9")
        broken = payload()
        broken["files"].append(
            {
                "label": "decomposed",
                "source": "examples/project/herdr/status.sh",
                "destination": f".config/tinjis-example/{decomposed}.sh",
            }
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("NFC", str(caught.exception))

    def test_nfd_link_destination_is_refused_by_the_grammar(self):
        broken = payload()
        broken["consumers"][2]["links"].append(
            {
                "destination": unicodedata.normalize("NFD", "caf\u00e9"),
                "source": "examples/project/herdr/config.toml",
            }
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("NFC", str(caught.exception))

    def test_nfd_runtime_root_is_refused_by_the_grammar(self):
        broken = payload()
        broken["runtime"]["root"] = unicodedata.normalize("NFD", "caf\u00e9")
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("NFC", str(caught.exception))

    def test_composed_destination_is_accepted(self):
        broken = payload()
        broken["consumers"][2]["links"].append(
            {"destination": "caf\u00e9", "source": "examples/project/herdr/config.toml"}
        )
        parsed = mf.parse_manifest(broken)
        self.assertEqual(parsed.consumers[2].links[0].destination, "caf\u00e9")

    def test_uppercase_consumer_name_is_refused_by_the_name_grammar(self):
        broken = payload()
        broken["consumers"][1]["name"] = "PI"
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("safe profile name", str(caught.exception))

    def test_case_only_legacy_path_duplicate_is_refused(self):
        broken = payload()
        broken["legacy_paths"] = ["legacy-example-link", "Legacy-Example-Link"]
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("duplicate", str(caught.exception))

    def test_case_only_label_duplicate_is_refused(self):
        broken = payload()
        broken["files"].append(
            {
                "label": "HERDR/STATUS.SH",
                "source": "examples/project/herdr/status.sh",
                "destination": ".config/tinjis-example/herdr/other.sh",
            }
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("duplicate projection label", str(caught.exception))

    def test_case_only_projection_destination_is_refused(self):
        broken = payload()
        broken["files"].append(
            {
                "label": "other",
                "source": "examples/project/herdr/status.sh",
                "destination": ".config/tinjis-example/herdr/STATUS.SH",
            }
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("duplicate projection destination", str(caught.exception))

    def test_reserved_leaf_name_is_case_insensitive(self):
        broken = payload()
        broken["consumers"][2]["links"].append(
            {"destination": "Settings.JSON", "source": "examples/project/herdr/config.toml"}
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("reserved", str(caught.exception))

    def test_nested_reserved_leaf_name_is_case_insensitive(self):
        broken = payload()
        broken["consumers"][2]["links"].append(
            {"destination": "nested/OWNED.JSON", "source": "examples/project/herdr/config.toml"}
        )
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("reserved", str(caught.exception))

    def test_case_only_selection_destination_overlap_is_refused(self):
        broken = payload()
        broken["selection"]["destination"] = ".config/TINJIS-EXAMPLE/herdr/status.sh"
        with self.assertRaises(ManifestError) as caught:
            mf.parse_manifest(broken)
        self.assertIn("selection destination", str(caught.exception))

    def test_cross_namespace_leaf_collision_is_refused(self):
        broken = payload()
        broken["files"].append(
            {
                "label": "collide",
                "source": "examples/project/pi/settings.json",
                "destination": ".config/tinjis-example/pi/agents/example-agent",
            }
        )
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_file_destination_inside_selection_destination_is_refused(self):
        broken = payload()
        broken["files"].append(
            {
                "label": "collide",
                "source": "examples/project/pi/settings.json",
                "destination": ".local/share/tinjis-example/shared/leaf",
            }
        )
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_selection_destination_under_a_declared_leaf_is_refused(self):
        broken = payload()
        broken["selection"]["destination"] = ".config/tinjis-example/pi/agents/example-agent/nested"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_leaf_equal_to_the_selection_destination_is_refused(self):
        broken = payload()
        broken["selection"]["destination"] = ".config/tinjis-example/herdr/status.sh"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    # -- reviewed actions --------------------------------------------------

    def test_action_naming_an_undeclared_consumer_is_refused(self):
        broken = payload()
        broken["reviewed_actions"][0]["consumer"] = "missing"
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_action_global_scope_is_allowed(self):
        parsed = mf.parse_manifest(payload())
        self.assertIsNone(parsed.reviewed_actions[1].consumer)

    def test_non_string_command_is_refused(self):
        broken = payload()
        broken["reviewed_actions"][0]["command"] = 1
        with self.assertRaises(ManifestError):
            mf.parse_manifest(broken)

    def test_empty_or_whitespace_only_command_is_refused(self):
        for command in ("", "   \t"):
            with self.subTest(command=command):
                broken = payload()
                broken["reviewed_actions"][0]["command"] = command
                with self.assertRaises(ManifestError):
                    mf.parse_manifest(broken)


class ContainmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.checkout = self.root / "checkout"
        self.outside = self.root / "outside"
        self.checkout.mkdir()
        self.outside.mkdir()
        (self.outside / "SKILL.md").write_text("outside\n", encoding="utf-8")
        write_example_tree(self.checkout)
        (self.checkout / mf.MANIFEST_NAME).write_text(json.dumps(payload()), encoding="utf-8")

    def test_valid_tree_with_real_sources_loads(self):
        parsed = mf.load_manifest(self.checkout)
        self.assertEqual(parsed, mf.parse_manifest(payload()))

    def test_symlinked_leaf_escape_is_refused(self):
        target = self.checkout / "examples" / "project" / "pi" / "agents" / "example-agent"
        shutil.rmtree(target)
        target.symlink_to(self.outside)
        with self.assertRaises(ContainmentError) as caught:
            mf.load_manifest(self.checkout)
        self.assertIn("outside the Tinjis checkout", str(caught.exception))

    def test_symlinked_ancestor_escape_is_refused(self):
        examples = self.checkout / "examples"
        shutil.rmtree(examples)
        examples.symlink_to(self.outside)
        with self.assertRaises(ContainmentError):
            mf.load_manifest(self.checkout)

    def test_dangling_symlink_to_outside_is_refused(self):
        leaf = self.checkout / "examples" / "project" / "herdr" / "status.sh"
        leaf.unlink()
        leaf.symlink_to(self.root / "does-not-exist")
        with self.assertRaises(ContainmentError):
            mf.load_manifest(self.checkout)

    def test_internal_symlink_is_allowed(self):
        leaf = self.checkout / "examples" / "project" / "herdr" / "status.sh"
        internal = self.checkout / "internal"
        internal.mkdir()
        (internal / "real.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        leaf.unlink()
        leaf.symlink_to(internal / "real.sh")
        self.assertEqual(mf.load_manifest(self.checkout), mf.parse_manifest(payload()))

    def test_containment_checks_every_declared_source_kind(self):
        parsed = mf.parse_manifest(payload())
        labelled = [label for label, _ in mf.repo_sources(parsed)]
        self.assertIn("selection.inventory", labelled)
        self.assertIn("selection.source_root", labelled)
        self.assertIn("files[herdr/status.sh].source", labelled)
        self.assertIn("consumers[pi].settings", labelled)
        self.assertIn("consumers[pi].links[agents/example-agent].source", labelled)

    def test_containment_does_not_require_existence(self):
        # `validate` must be able to inspect a manifest whose tree is absent.
        absent = self.root / "absent-checkout"
        absent.mkdir()
        (absent / mf.MANIFEST_NAME).write_text(json.dumps(payload()), encoding="utf-8")
        self.assertEqual(mf.load_manifest(absent), mf.parse_manifest(payload()))


class DeclaredInputTest(unittest.TestCase):
    """`check` validates existence, type, and symlink policy for every input."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        write_example_tree(self.checkout)
        (self.checkout / mf.MANIFEST_NAME).write_text(json.dumps(payload()), encoding="utf-8")
        self.manifest = mf.load_manifest(self.checkout)

    def test_a_complete_tree_passes(self):
        self.assertIsNone(mf.validate_declared_inputs(self.checkout, self.manifest))

    def test_declared_inputs_cover_every_source_kind(self):
        kinds = {spec.where: spec.kind for spec in mf.declared_inputs(self.manifest)}
        self.assertEqual(kinds["consumers[pi].settings"], mf.INPUT_FILE)
        self.assertEqual(
            kinds["consumers[pi].links[agents/example-agent].source"], mf.INPUT_DIRECTORY
        )
        self.assertEqual(kinds["selection.inventory"], mf.INPUT_FILE)
        self.assertEqual(kinds["selection.source_root"], mf.INPUT_DIRECTORY)
        self.assertEqual(kinds["files[herdr/status.sh].source"], mf.INPUT_FILE)

    def test_missing_settings_template_is_refused(self):
        (self.checkout / "examples" / "project" / "pi" / "settings.json").unlink()
        with self.assertRaises(InputError) as caught:
            mf.validate_declared_inputs(self.checkout, self.manifest)
        self.assertIn("consumers[pi].settings is missing", str(caught.exception))

    def test_settings_template_of_the_wrong_type_is_refused(self):
        settings = self.checkout / "examples" / "project" / "pi" / "settings.json"
        settings.unlink()
        settings.mkdir()
        with self.assertRaises(InputError) as caught:
            mf.validate_declared_inputs(self.checkout, self.manifest)
        self.assertIn("not a regular file", str(caught.exception))

    def test_missing_consumer_link_source_is_refused(self):
        shutil.rmtree(self.checkout / "examples" / "project" / "pi" / "agents" / "example-agent")
        with self.assertRaises(InputError) as caught:
            mf.validate_declared_inputs(self.checkout, self.manifest)
        self.assertIn("is missing", str(caught.exception))

    def test_consumer_link_source_of_the_wrong_type_is_refused(self):
        leaf = self.checkout / "examples" / "project" / "pi" / "agents" / "example-agent"
        shutil.rmtree(leaf)
        leaf.write_text("not a directory\n", encoding="utf-8")
        with self.assertRaises(InputError) as caught:
            mf.validate_declared_inputs(self.checkout, self.manifest)
        self.assertIn("is not a directory", str(caught.exception))

    def test_symlinked_settings_template_is_refused_even_when_contained(self):
        settings = self.checkout / "examples" / "project" / "pi" / "settings.json"
        other = self.checkout / "examples" / "project" / "pi" / "other.json"
        other.write_text("{}\n", encoding="utf-8")
        settings.unlink()
        settings.symlink_to(other)
        with self.assertRaises(InputError) as caught:
            mf.validate_declared_inputs(self.checkout, self.manifest)
        self.assertIn("must not be a symlink", str(caught.exception))

    def test_symlinked_file_projection_source_is_refused(self):
        leaf = self.checkout / "examples" / "project" / "herdr" / "status.sh"
        other = self.checkout / "examples" / "project" / "herdr" / "config.toml"
        leaf.unlink()
        leaf.symlink_to(other)
        with self.assertRaises(InputError):
            mf.validate_declared_inputs(self.checkout, self.manifest)

    def test_symlinked_selection_source_root_is_refused(self):
        source_root = self.checkout / "examples" / "project" / "shared"
        real = self.checkout / "examples" / "project" / "shared-real"
        source_root.rename(real)
        source_root.symlink_to(real)
        with self.assertRaises(InputError):
            mf.validate_declared_inputs(self.checkout, self.manifest)

    def test_escaping_source_is_refused_before_the_shape_check(self):
        outside = self.root / "outside"
        outside.mkdir()
        settings = self.checkout / "examples" / "project" / "pi" / "settings.json"
        settings.unlink()
        settings.symlink_to(outside / "settings.json")
        with self.assertRaises(ContainmentError):
            mf.validate_declared_inputs(self.checkout, self.manifest)


class SecrecyTest(unittest.TestCase):
    def test_authored_example_has_no_credential_shaped_keys(self):
        text = (CHECKOUT / "examples" / "tinjis.json").read_text(encoding="utf-8")
        lowered = text.lower()
        for forbidden in (
            "api_key",
            "apikey",
            "password",
            "secret",
            "bearer",
            "private_key",
            "token",
        ):
            self.assertNotIn(forbidden, lowered, forbidden)

    def test_every_declared_path_is_relative(self):
        parsed = mf.parse_manifest(payload())
        paths = [parsed.runtime.root, parsed.selection.destination]
        for _label, relative in mf.repo_sources(parsed):
            paths.append(relative)
        for entry in parsed.files:
            paths.append(entry.destination)
        for consumer in parsed.consumers:
            paths.append(consumer.root)
            for link in consumer.links:
                paths.append(link.destination)
        for path in paths:
            self.assertFalse(Path(path).is_absolute(), path)

    def test_no_external_url_or_package_pin_in_the_example_tree(self):
        forbidden = ("http://", "https://", "git:github.com", "@sha", "node_modules")
        for path in sorted((CHECKOUT / "examples").rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8").lower()
            for needle in forbidden:
                self.assertNotIn(needle, text, f"{path}: {needle}")


class ImmutabilityTest(unittest.TestCase):
    def test_parsed_manifest_is_frozen(self):
        parsed = mf.parse_manifest(payload())
        with self.assertRaises(FrozenInstanceError):
            parsed.runtime.root = "other"  # type: ignore[misc]

    def test_replace_builds_a_different_manifest_for_mutation_tests(self):
        parsed = mf.parse_manifest(payload())
        mutated = replace(parsed, runtime=replace(parsed.runtime, root=".other"))
        self.assertNotEqual(parsed, mutated)
        self.assertEqual(mf.validate_semantic_collisions(mutated), None)


if __name__ == "__main__":
    unittest.main()
