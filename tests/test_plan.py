"""Read-only planning, folded destination collisions, and boundary tests.

Every test here runs against a temporary HOME and a temporary checkout. No test
performs a mutation on behalf of the package, because the package has no
mutation path: the only writes in this module create the fixtures the planner is
supposed to observe.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fixture_topology import example_payload, write_example_tree

from tinjis import plan as pl
from tinjis.errors import ManifestError, OwnershipError, SelectionError
from tinjis.manifest import load_manifest
from tinjis.ownership import owned_path


class PlanTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.checkout = self.root / "checkout"
        self.home = self.root / "home"
        self.checkout.mkdir()
        self.home.mkdir()
        write_example_tree(self.checkout)
        (self.checkout / "tinjis.json").write_text(
            json.dumps(example_payload(), indent=2) + "\n", encoding="utf-8"
        )
        self.manifest = load_manifest(self.checkout)
        self.record = owned_path(self.home)
        self.boundary = pl.projection_boundary(self.manifest)

    # -- helpers -----------------------------------------------------------

    @property
    def pairs(self):
        return pl.projection_pairs(self.home, self.checkout, self.manifest)

    def plan(self, owned=None):
        return pl.plan_projections(self.home, self.checkout, owned or {}, self.manifest)

    def by_dest(self, plans):
        return {str(plan.dest): plan for plan in plans}

    def write_record(self, owned: dict) -> None:
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(
            json.dumps({"owner": "tinjis", "schema": 1, "owned": owned}),
            encoding="utf-8",
        )

    def rewrite_payload(self, mutate) -> None:
        payload = example_payload()
        mutate(payload)
        (self.checkout / "tinjis.json").write_text(json.dumps(payload), encoding="utf-8")


class BoundaryAndPairTest(PlanTestCase):
    def test_boundary_uses_the_selection_destination_as_its_prefix(self):
        self.assertEqual(self.boundary.prefix, (".local", "share", "tinjis-example", "shared"))
        self.assertEqual(
            self.boundary.exact, ((".config", "tinjis-example", "herdr", "status.sh"),)
        )

    def test_pair_set_covers_the_selection_leaves_and_the_file_projection(self):
        labels = sorted(label for label, _dest, _source in self.pairs)
        self.assertEqual(
            labels,
            ["herdr/status.sh", "selection/example-checklist", "selection/example-report"],
        )

    def test_consumer_links_and_settings_are_never_planned_as_projection_pairs(self):
        destinations = {str(dest) for _label, dest, _source in self.pairs}
        self.assertNotIn(str(self.home / ".config/tinjis-example/pi/settings.json"), destinations)
        self.assertNotIn(
            str(self.home / ".config/tinjis-example/pi/agents/example-agent"), destinations
        )

    def test_plan_covers_only_selection_leaves_and_file_projections(self):
        """Consumer settings and links are declared and validated, but this phase
        does not plan them, so they must not appear as planned destinations."""
        destinations = {str(dest) for _label, dest, _source in self.pairs}
        self.assertIn(
            str(self.home / ".local/share/tinjis-example/shared/example-report"), destinations
        )
        self.assertIn(str(self.home / ".config/tinjis-example/herdr/status.sh"), destinations)
        self.assertEqual(len(destinations), 3)


class PlanTest(PlanTestCase):
    def test_fresh_home_plans_only_creates(self):
        plans = self.plan()
        self.assertEqual({plan.action for plan in plans}, {"create"})
        self.assertEqual(len(plans), 3)
        self.assertTrue(all(plan.would_change for plan in plans))

    def test_planning_writes_nothing(self):
        before = sorted(str(path) for path in self.home.rglob("*"))
        self.plan()
        self.assertEqual(sorted(str(path) for path in self.home.rglob("*")), before)
        self.assertFalse(self.record.exists())
        self.assertFalse((self.home / ".config").exists())

    def test_no_module_exposes_an_apply_entry_point(self):
        self.assertFalse(hasattr(pl, "apply_projections"))
        self.assertFalse(hasattr(pl, "atomic_link"))
        self.assertFalse(hasattr(pl, "leaf_race_error"))

    def test_unowned_existing_symlink_is_a_conflict(self):
        _label, dest, _source = self.pairs[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(self.checkout / "examples" / "project" / "herdr" / "config.toml")
        plans = self.by_dest(self.plan())
        self.assertEqual(plans[str(dest)].action, "conflict")
        self.assertIn("not Tinjis-owned", plans[str(dest)].detail)

    def test_owned_symlink_with_a_drifted_target_is_a_conflict(self):
        _label, dest, _source = self.pairs[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to("/somewhere/else")
        plans = self.by_dest(self.plan({str(dest): "/expected/target"}))
        self.assertEqual(plans[str(dest)].action, "conflict")
        self.assertIn("drift", plans[str(dest)].detail)

    def test_owned_symlink_with_the_recorded_target_but_a_new_source_is_an_update(self):
        _label, dest, source = self.pairs[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to("/older/target")
        plans = self.by_dest(self.plan({str(dest): "/older/target"}))
        self.assertEqual(plans[str(dest)].action, "update")
        self.assertEqual(plans[str(dest)].source, source)

    def test_conflict_when_destination_is_a_real_file(self):
        _label, dest, _source = self.pairs[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("not a link\n", encoding="utf-8")
        plans = self.by_dest(self.plan())
        self.assertEqual(plans[str(dest)].action, "conflict")
        self.assertIn("not a symlink", plans[str(dest)].detail)

    def test_conflict_when_the_source_is_missing(self):
        (self.checkout / "examples" / "project" / "herdr" / "status.sh").unlink()
        detail = " ".join(plan.detail for plan in self.plan())
        self.assertIn("missing source", detail)

    def test_conflict_on_a_symlinked_ancestor(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.home / ".local").symlink_to(elsewhere)
        plans = self.by_dest(self.plan())
        details = " ".join(plan.detail for plan in plans.values())
        self.assertIn("symlinked ancestor", details)
        selection = [
            plan for plan in plans.values() if str(plan.dest).startswith(str(self.home / ".local"))
        ]
        self.assertEqual({plan.action for plan in selection}, {"conflict"})

    def test_retired_leaf_still_matching_the_record_is_planned_for_retirement(self):
        orphan = self.home / ".local" / "share" / "tinjis-example" / "shared" / "gone"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.symlink_to(self.checkout / "examples" / "project" / "shared" / "example-report")
        plans = self.by_dest(
            self.plan({str(orphan): str(self.checkout / "examples/project/shared/example-report")})
        )
        self.assertEqual(plans[str(orphan)].action, "retire")

    def test_retired_leaf_that_drifted_is_a_conflict(self):
        orphan = self.home / ".local" / "share" / "tinjis-example" / "shared" / "gone"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.symlink_to("/somewhere/else")
        plans = self.by_dest(self.plan({str(orphan): "/recorded"}))
        self.assertEqual(plans[str(orphan)].action, "conflict")
        self.assertIn("drift", plans[str(orphan)].detail)

    def test_retired_leaf_replaced_by_a_real_file_is_a_conflict(self):
        orphan = self.home / ".local" / "share" / "tinjis-example" / "shared" / "gone"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("x\n", encoding="utf-8")
        plans = self.by_dest(self.plan({str(orphan): "/recorded"}))
        self.assertEqual(plans[str(orphan)].action, "conflict")

    def test_retired_leaf_that_is_already_absent_is_retirable(self):
        orphan = self.home / ".local" / "share" / "tinjis-example" / "shared" / "gone"
        plans = self.by_dest(self.plan({str(orphan): "/recorded"}))
        self.assertEqual(plans[str(orphan)].action, "retire")
        self.assertEqual(plans[str(orphan)].detail, "already absent")

    def test_record_entry_outside_the_boundary_is_refused(self):
        with self.assertRaises(OwnershipError):
            pl.plan_projections(
                self.home,
                self.checkout,
                {str(self.home / ".ssh" / "authorized_keys"): "/x"},
                self.manifest,
            )

    def test_record_entry_differing_only_by_case_is_outside_the_boundary(self):
        declared = str(self.home / ".config" / "tinjis-example" / "herdr" / "status.sh")
        shouted = declared.replace("tinjis-example", "TINJIS-EXAMPLE")
        with self.assertRaises(OwnershipError):
            pl.plan_projections(self.home, self.checkout, {shouted: "/x"}, self.manifest)

    def test_case_alias_of_selected_leaf_is_refused_before_planning(self):
        declared = next(
            str(dest) for _label, dest, _source in self.pairs if dest.name == "example-report"
        )
        alias = str(Path(declared).with_name("Example-Report"))
        with self.assertRaises(OwnershipError) as caught:
            self.plan({alias: "/recorded"})
        self.assertIn("aliases a declared destination", str(caught.exception))

    def test_selected_resource_removed_from_the_inventory_is_planned_for_retirement(self):
        inventory = self.checkout / "examples" / "project" / "inventory.json"
        inventory.write_text('{"skills": ["example-report"]}\n', encoding="utf-8")
        manifest = load_manifest(self.checkout)
        plans = pl.plan_projections(self.home, self.checkout, {}, manifest)
        actions = {plan.dest.name: plan.action for plan in plans}
        self.assertEqual(actions["example-report"], "create")
        self.assertNotIn("example-checklist", actions)


class FoldedDestinationCollisionTest(PlanTestCase):
    """A default macOS filesystem is case- and normalization-insensitive, so two
    declarations that differ only by letter case or Unicode form are one name.
    They are refused at parse time, and a test proves the invariant that makes
    the parse-time check sufficient for resolved selection leaves too.
    """

    def test_case_only_cross_namespace_collision_is_refused(self):
        """A file projection and a consumer link naming one path only by case."""
        self.rewrite_payload(
            lambda p: p["files"].append(
                {
                    "label": "shout",
                    "source": "examples/project/herdr/status.sh",
                    # Consumer pi's link is agents/example-agent.
                    "destination": ".config/tinjis-example/pi/agents/Example-Agent",
                }
            )
        )
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("folding", str(caught.exception))

    def test_case_only_consumer_link_collision_is_refused(self):
        self.rewrite_payload(
            lambda p: p["consumers"][0]["links"].append(
                {
                    "destination": "agents/Example-Agent",
                    "source": "examples/project/pi/agents/example-agent",
                }
            )
        )
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("folding", str(caught.exception))

    def test_nfd_consumer_link_collision_is_refused(self):
        composed = "agents/example\u00e9"
        decomposed = unicodedata.normalize("NFD", composed)
        self.rewrite_payload(
            lambda p: p["consumers"][0]["links"].extend(
                [
                    {
                        "destination": composed,
                        "source": "examples/project/pi/agents/example-agent",
                    },
                    {
                        "destination": decomposed,
                        "source": "examples/project/pi/agents/example-agent",
                    },
                ]
            )
        )
        with self.assertRaises(ManifestError):
            load_manifest(self.checkout)

    def test_non_nfc_destination_is_refused_by_the_grammar(self):
        decomposed = unicodedata.normalize("NFD", "caf\u00e9")
        self.rewrite_payload(
            lambda p: p["files"].append(
                {
                    "label": "decomposed",
                    "source": "examples/project/herdr/status.sh",
                    "destination": f".config/tinjis-example/{decomposed}.sh",
                }
            )
        )
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("NFC", str(caught.exception))

    def test_non_nfc_runtime_root_is_refused(self):
        decomposed = unicodedata.normalize("NFD", "caf\u00e9")
        self.rewrite_payload(lambda p: p["runtime"].update(root=decomposed))
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("NFC", str(caught.exception))

    def test_case_only_reserved_leaf_name_is_refused(self):
        self.rewrite_payload(
            lambda p: p["consumers"][2]["links"].append(
                {"destination": "Settings.json", "source": "examples/project/herdr/config.toml"}
            )
        )
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("reserved", str(caught.exception))

    def test_case_only_legacy_path_duplicate_is_refused(self):
        self.rewrite_payload(
            lambda p: p.update(legacy_paths=["legacy-example-link", "Legacy-Example-Link"])
        )
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("duplicate", str(caught.exception))

    def test_case_only_projection_label_duplicate_is_refused(self):
        self.rewrite_payload(
            lambda p: p["files"].append(
                {
                    "label": "HERDR/STATUS.SH",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config/tinjis-example/herdr/other.sh",
                }
            )
        )
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("duplicate projection label", str(caught.exception))

    def test_clean_example_manifest_parses_with_no_collision(self):
        self.assertEqual(load_manifest(self.checkout), self.manifest)

    def test_selection_inventory_case_duplicate_is_refused(self):
        inventory = self.checkout / "examples" / "project" / "inventory.json"
        inventory.write_text(
            json.dumps({"skills": ["example-report", "Example-Report"]}), encoding="utf-8"
        )
        with self.assertRaises(SelectionError) as caught:
            pl.selected_leaves(self.checkout, self.manifest)
        self.assertIn("duplicate", str(caught.exception))

    def test_declared_leaf_inside_the_selection_destination_is_refused(self):
        """The invariant that makes the parse-time check sufficient.

        No declared leaf may equal, sit inside, or contain the selection
        destination. A selection leaf is by construction one direct child of
        that destination, so once this holds no resolved selection leaf can
        collide with a declared leaf, or be an ancestor of one. That is why the
        planner does not repeat the check on resolved paths.
        """

        def move_selection_over_the_herdr_root(payload):
            payload["selection"]["destination"] = ".config/tinjis-example/herdr"
            payload["consumers"][2]["links"].append(
                {"destination": "nested", "source": "examples/project/herdr/config.toml"}
            )

        self.rewrite_payload(move_selection_over_the_herdr_root)
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("inside the selection destination", str(caught.exception))

    def test_selection_destination_over_a_consumer_root_is_refused_case_insensitively(self):
        def shout_the_root(payload):
            payload["selection"]["destination"] = ".config/TINJIS-EXAMPLE/herdr"
            payload["consumers"][2]["links"].append(
                {"destination": "nested", "source": "examples/project/herdr/config.toml"}
            )

        self.rewrite_payload(shout_the_root)
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.checkout)
        self.assertIn("inside the selection destination", str(caught.exception))


class TempHomeIsolationTest(PlanTestCase):
    """The planner never names a destination outside the HOME it was given."""

    def test_no_planned_destination_escapes_home(self):
        for _label, dest, _source in self.pairs:
            self.assertTrue(str(dest).startswith(str(self.home)), dest)
        for plan in self.plan():
            self.assertTrue(str(plan.dest).startswith(str(self.home)), plan.dest)

    def test_recorded_destinations_stay_inside_home(self):
        declared = str(self.home / ".config" / "tinjis-example" / "herdr" / "status.sh")
        self.write_record({declared: "/x"})
        for raw in pl.plan_projections(self.home, self.checkout, {declared: "/x"}, self.manifest):
            self.assertTrue(str(raw.dest).startswith(str(self.home)), raw.dest)

    def test_engine_ignores_the_process_home(self):
        for plan in self.plan():
            self.assertTrue(str(plan.dest).startswith(str(self.home)), plan.dest)

    def test_planner_does_not_read_the_ownership_record_by_itself(self):
        # The record is only consulted through the caller-supplied mapping, so a
        # missing record cannot silently become "in sync".
        self.assertFalse(self.record.exists())
        plans = self.plan()
        self.assertEqual({plan.action for plan in plans}, {"create"})
        self.assertFalse(self.record.exists())


class NoWriteTest(PlanTestCase):
    def test_planning_does_not_create_any_path(self):
        before = sorted(os.walk(self.home))
        self.plan()
        self.assertEqual(sorted(os.walk(self.home)), before)

    def test_planning_does_not_change_an_existing_symlink(self):
        _label, dest, source = self.pairs[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to("/somewhere/else")
        self.plan()
        self.assertEqual(os.readlink(dest), "/somewhere/else")
        self.assertFalse(dest.resolve() == source)


if __name__ == "__main__":
    unittest.main()
