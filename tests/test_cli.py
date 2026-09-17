"""CLI behavior tests: help, validate, and the read-only check.

Every test runs in-process against a temporary HOME. No test writes to the real
HOME, installs a package, touches the network, or asks the package to mutate
anything -- the package has no mutation path, and a test asserts that too.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

CHECKOUT = Path(__file__).resolve().parent.parent
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fixture_topology import example_payload

from tinjis import cli
from tinjis.manifest import MANIFEST_NAME
from tinjis.ownership import owned_path
from tinjis.topology import LOCKED_EXAMPLE_TOPOLOGY


def tree_digest(root: Path) -> str:
    """A content, mode, and symlink-target digest of a whole tree."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            digest.update(f"link {relative} -> {os.readlink(path)}\n".encode())
        elif path.is_dir():
            digest.update(f"dir {relative} {stat.S_IMODE(path.stat().st_mode):o}\n".encode())
        else:
            digest.update(f"file {relative} {stat.S_IMODE(path.stat().st_mode):o} ".encode())
            digest.update(path.read_bytes())
            digest.update(b"\n")
    return digest.hexdigest()


def run(argv, *, home=None):
    """Run the CLI in-process and return (code, stdout, stderr).

    ``argparse`` exits through ``SystemExit`` for ``--help``, ``--version``, and
    usage errors; those codes are returned like any other result.
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    patches = []
    if home is not None:
        patches.append(mock.patch.object(Path, "home", return_value=home))
    try:
        for patch in patches:
            patch.start()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        for patch in reversed(patches):
            patch.stop()
    return code, stdout.getvalue(), stderr.getvalue()


@contextlib.contextmanager
def patched_checkout(checkout: Path):
    """Point both the checkout root and the default manifest at a temp tree."""
    original_checkout = cli.CHECKOUT
    original_example = cli.EXAMPLE_MANIFEST
    cli.CHECKOUT = checkout
    cli.EXAMPLE_MANIFEST = checkout / MANIFEST_NAME
    try:
        yield checkout
    finally:
        cli.CHECKOUT = original_checkout
        cli.EXAMPLE_MANIFEST = original_example


class ParserTest(unittest.TestCase):
    def test_help_lists_every_read_only_command(self):
        code, out, _err = run(["--help"])
        self.assertEqual(code, 0)
        for command in ("validate", "check", "example"):
            self.assertIn(command, out)

    def test_help_does_not_offer_an_apply_command(self):
        _code, out, _err = run(["--help"])
        self.assertNotIn("apply", out)

    def test_apply_is_not_a_command(self):
        code, out, err = run(["apply", "--home", "/tmp"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command", err)
        self.assertEqual(out, "")

    def test_version_is_reported(self):
        code, out, _err = run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("tinjis", out)

    def test_unknown_command_is_a_usage_error(self):
        code, _out, err = run(["frobnicate"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command", err)

    def test_no_arguments_defaults_to_check(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve() / "home"
            home.mkdir()
            code, out, _err = run([], home=home)
            self.assertEqual(code, 1)  # fresh home is out of sync
            self.assertIn("out of sync", out)
            self.assertIn(str(home), out)

    def test_subcommand_help_is_available(self):
        for command in ("validate", "check", "example"):
            code, out, _err = run([command, "--help"])
            self.assertEqual(code, 0)
            self.assertIn("usage: tinjis", out)

    def test_help_does_not_promise_writing(self):
        _code, out, _err = run(["--help"])
        self.assertIn("Read-only", out)


class NoMutationRouteTest(unittest.TestCase):
    """There must be no callable that mutates the filesystem."""

    def test_cli_exposes_no_apply_command_or_function(self):
        self.assertNotIn("apply", cli.COMMANDS)
        self.assertFalse(hasattr(cli, "command_apply"))

    def test_package_has_no_writer_entry_points(self):
        import tinjis.ownership as ownership
        import tinjis.plan as plan

        for module, name in (
            (plan, "apply_projections"),
            (plan, "atomic_link"),
            (plan, "leaf_race_error"),
            (ownership, "write_owned"),
        ):
            with self.subTest(module=module.__name__, name=name):
                self.assertFalse(hasattr(module, name))

    def test_no_module_defines_a_write_shaped_function_name(self):
        banned = ("write_", "apply_", "atomic_", "install", "mutate", "unlink")
        for path in sorted((CHECKOUT / "tinjis").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for name in banned:
                with self.subTest(path=path.name, name=name):
                    self.assertNotIn(f"def {name}", source)


class ValidateTest(unittest.TestCase):
    def test_bundled_example_is_valid_and_locked(self):
        code, out, _err = run(["validate"])
        self.assertEqual(code, 0)
        self.assertIn("lock     matches the compiled bundled-example topology", out)
        self.assertIn("nothing was written and nothing can be", out)

    def test_explicit_manifest_path_is_accepted(self):
        manifest = str(CHECKOUT / "examples" / "tinjis.json")
        code, out, _err = run(["validate", "--manifest", manifest])
        self.assertEqual(code, 0)
        self.assertIn("schema   1", out)

    def test_explicit_symlinked_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            link = Path(temp) / "linked.json"
            link.symlink_to(CHECKOUT / "examples" / "tinjis.json")
            code, _out, err = run(["validate", "--manifest", str(link)])
        self.assertEqual(code, 1)
        self.assertIn("must be a regular file, not a symlink", err)

    def test_valid_but_non_executable_topology_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            checkout.mkdir()
            shutil.copytree(CHECKOUT / "examples", checkout / "examples")
            payload = example_payload()
            payload["runtime"]["root"] = ".config/other-example"
            (checkout / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
            with patched_checkout(checkout):
                code, out, _err = run(["validate", "--manifest", str(checkout / MANIFEST_NAME)])
            self.assertEqual(code, 0)
            self.assertIn("not the bundled example", out)
            self.assertIn("read-only either way", out)

    def test_validate_does_not_require_the_sources_to_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            (checkout / "examples").mkdir(parents=True)
            (checkout / MANIFEST_NAME).write_text(json.dumps(example_payload()), encoding="utf-8")
            with patched_checkout(checkout):
                code, _out, err = run(["validate"])
            self.assertEqual(code, 0, err)

    def test_invalid_manifest_exits_one(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            checkout.mkdir()
            (checkout / MANIFEST_NAME).write_text('{"schema": 1,}', encoding="utf-8")
            with patched_checkout(checkout):
                code, _out, err = run(["validate"])
            self.assertEqual(code, 1)
            self.assertIn("error:", err)

    def test_missing_manifest_exits_one(self):
        with tempfile.TemporaryDirectory() as temp:
            with patched_checkout(Path(temp).resolve()):
                code, _out, err = run(["validate"])
            self.assertEqual(code, 1)
            self.assertIn("missing", err)

    def test_missing_explicit_manifest_exits_one(self):
        code, _out, err = run(["validate", "--manifest", "/nonexistent/tinjis.json"])
        self.assertEqual(code, 1)
        self.assertIn("missing", err)

    def test_case_only_collision_exits_one(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            checkout.mkdir()
            shutil.copytree(CHECKOUT / "examples", checkout / "examples")
            payload = example_payload()
            payload["files"].append(
                {
                    "label": "shout",
                    "source": "examples/project/herdr/status.sh",
                    "destination": ".config/tinjis-example/pi/agents/Example-Agent",
                }
            )
            (checkout / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
            with patched_checkout(checkout):
                code, _out, err = run(["validate"])
            self.assertEqual(code, 1)
            self.assertIn("folding", err)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.home.mkdir()

    def test_check_is_read_only_and_reports_out_of_sync(self):
        before = tree_digest(self.home)
        code, out, _err = run(["check", "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("out of sync", out)
        self.assertEqual(tree_digest(self.home), before)
        self.assertFalse(owned_path(self.home).exists())
        self.assertFalse((self.home / ".config").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_malformed_ownership_record_is_refused_without_mutation(self):
        record = owned_path(self.home)
        record.parent.mkdir(parents=True)
        record.write_text('{"owner":', encoding="utf-8")
        before = tree_digest(self.home)
        code, _out, err = run(["check", "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("ownership record", err)
        self.assertEqual(tree_digest(self.home), before)

    def test_retirement_report_path_is_read_only(self):
        orphan = self.home / ".local/share/tinjis-example/shared/retired"
        orphan.parent.mkdir(parents=True)
        target = str(CHECKOUT / "examples/project/shared/example-report")
        orphan.symlink_to(target)
        record = owned_path(self.home)
        record.parent.mkdir(parents=True)
        record.write_text(
            json.dumps({"owner": "tinjis", "schema": 1, "owned": {str(orphan): target}}),
            encoding="utf-8",
        )
        before = tree_digest(self.home)
        code, out, err = run(["check", "--home", str(self.home)])
        self.assertEqual(code, 1, err)
        self.assertIn("retire", out)
        self.assertEqual(tree_digest(self.home), before)

    def test_check_says_plainly_that_it_cannot_apply(self):
        code, out, _err = run(["check", "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("Tinjis v0 is read-only; there is no command that applies this plan", out)

    def test_check_prints_the_ownership_boundary(self):
        code, out, _err = run(["check", "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("boundary file      .config/tinjis-example/herdr/status.sh", out)
        self.assertIn("boundary selection .local/share/tinjis-example/shared/<one-leaf>", out)

    def test_check_declares_consumers_without_configuring_them(self):
        _code, out, _err = run(["check", "--home", str(self.home)])
        for name in ("pi", "hermes", "herdr"):
            self.assertIn(f"declared {name}:", out)
        self.assertIn("Tinjis does not configure this consumer", out)

    def test_check_prints_reviewed_actions_and_never_runs_them(self):
        _code, out, _err = run(["check", "--home", str(self.home)])
        self.assertIn("reviewed actions (printed, never run automatically)", out)
        self.assertIn("[pi] example-package-manager install pi", out)
        self.assertIn("[global] herdr integration install pi", out)

    def test_check_errors_when_home_is_missing(self):
        code, _out, err = run(["check", "--home", str(self.home / "absent")])
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)

    def test_check_errors_when_home_is_a_symlink(self):
        link = self.home.parent / "linked-home"
        link.symlink_to(self.home)
        code, _out, err = run(["check", "--home", str(link)])
        self.assertEqual(code, 1)
        self.assertIn("must not be a symlink", err)

    def test_check_refuses_when_the_authored_example_drifts_from_the_lock(self):
        drifted = replace(
            LOCKED_EXAMPLE_TOPOLOGY,
            runtime=replace(LOCKED_EXAMPLE_TOPOLOGY.runtime, root=".config/drifted"),
        )
        with mock.patch.object(cli, "LOCKED_EXAMPLE_TOPOLOGY", drifted):
            code, _out, err = run(["check", "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("disagrees with the compiled topology lock", err)

    def test_check_detects_a_missing_settings_template(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            checkout.mkdir()
            shutil.copytree(CHECKOUT / "examples", checkout / "examples")
            (checkout / MANIFEST_NAME).write_text(json.dumps(example_payload()), encoding="utf-8")
            (checkout / "examples" / "project" / "pi" / "settings.json").unlink()
            with patched_checkout(checkout):
                code, _out, err = run(["check", "--home", str(self.home)])
            self.assertEqual(code, 1)
            self.assertIn("consumers[pi].settings is missing", err)

    def test_check_detects_a_symlinked_consumer_link_source(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            checkout.mkdir()
            shutil.copytree(CHECKOUT / "examples", checkout / "examples")
            (checkout / MANIFEST_NAME).write_text(json.dumps(example_payload()), encoding="utf-8")
            leaf = checkout / "examples" / "project" / "pi" / "agents" / "example-agent"
            shutil.rmtree(leaf)
            leaf.symlink_to(checkout / "examples" / "project" / "herdr")
            with patched_checkout(checkout):
                code, _out, err = run(["check", "--home", str(self.home)])
            self.assertEqual(code, 1)
            self.assertIn("must not be a symlink", err)

    def test_check_detects_a_missing_consumer_link_source(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve() / "checkout"
            checkout.mkdir()
            shutil.copytree(CHECKOUT / "examples", checkout / "examples")
            (checkout / MANIFEST_NAME).write_text(json.dumps(example_payload()), encoding="utf-8")
            shutil.rmtree(checkout / "examples" / "project" / "hermes" / "skills")
            with patched_checkout(checkout):
                code, _out, err = run(["check", "--home", str(self.home)])
            self.assertEqual(code, 1)
            self.assertIn("is missing", err)


class ExampleTest(unittest.TestCase):
    def test_example_prints_the_authored_manifest(self):
        code, out, _err = run(["example"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.split("\n# lock:")[0]), example_payload())

    def test_example_raw_matches_the_file(self):
        code, out, _err = run(["example", "--raw"])
        self.assertEqual(code, 0)
        self.assertEqual(out, (CHECKOUT / "examples" / "tinjis.json").read_text(encoding="utf-8"))

    def test_example_reports_the_lock_status(self):
        _code, out, _err = run(["example"])
        self.assertIn("# lock: matches compiled bundled-example topology", out)

    def test_example_reports_a_drifted_file(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve()
            (checkout / "examples").mkdir()
            drifted = example_payload()
            drifted["runtime"]["root"] = ".config/drifted-example"
            (checkout / "examples" / MANIFEST_NAME).write_text(
                json.dumps(drifted), encoding="utf-8"
            )
            original = cli.EXAMPLE_MANIFEST
            cli.EXAMPLE_MANIFEST = checkout / "examples" / MANIFEST_NAME
            try:
                code, out, _err = run(["example"])
            finally:
                cli.EXAMPLE_MANIFEST = original
            self.assertEqual(code, 0)
            self.assertIn("# lock: the runtime root", out)

    def test_example_reports_a_missing_file(self):
        original = cli.EXAMPLE_MANIFEST
        cli.EXAMPLE_MANIFEST = Path("/nonexistent/tinjis.json")
        try:
            code, _out, err = run(["example"])
        finally:
            cli.EXAMPLE_MANIFEST = original
        self.assertEqual(code, 1)
        self.assertIn("missing", err)


class CheckoutIsolationTest(unittest.TestCase):
    """A read-only command must not write anywhere, including the checkout.

    The snapshot covers file contents (hashed), permission bits, and symlink
    targets, so a change that preserves a size or a timestamp still fails.
    """

    def setUp(self):
        # The test process itself imports the package, which may have left
        # bytecode caches behind. Remove them so a fresh cache written by the
        # command under test cannot hide.
        for cache in CHECKOUT.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)

    def snapshot(self) -> str:
        return tree_digest(CHECKOUT)

    def launcher(self, args):
        import subprocess

        env = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
        return subprocess.run(
            [sys.executable, str(CHECKOUT / "bin" / "tinjis"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def module_run(self, args):
        import subprocess

        env = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
        return subprocess.run(
            [sys.executable, "-B", "-m", "tinjis", *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(CHECKOUT),
            env=env,
        )

    def assert_checkout_unchanged(self, before: str) -> None:
        self.assertEqual(self.snapshot(), before)

    def test_launcher_check_writes_nothing_at_all(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve() / "home"
            home.mkdir()
            before = self.snapshot()
            result = self.launcher(["check", "--home", str(home)])
            self.assertEqual(result.returncode, 1, result.stderr)  # fresh home is out of sync
            self.assertIn("out of sync", result.stdout)
            self.assert_checkout_unchanged(before)
            self.assertEqual(sorted(home.rglob("*")), [])

    def test_launcher_refuses_malformed_record_without_mutating_populated_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve() / "home"
            record = owned_path(home)
            record.parent.mkdir(parents=True)
            record.write_text('{"owner":', encoding="utf-8")
            checkout_before = self.snapshot()
            home_before = tree_digest(home)
            result = self.launcher(["check", "--home", str(home)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("ownership record", result.stderr)
            self.assert_checkout_unchanged(checkout_before)
            self.assertEqual(tree_digest(home), home_before)

    def test_launcher_reports_retirement_without_mutating_populated_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve() / "home"
            orphan = home / ".local/share/tinjis-example/shared/retired"
            orphan.parent.mkdir(parents=True)
            target = str(CHECKOUT / "examples/project/shared/example-report")
            orphan.symlink_to(target)
            record = owned_path(home)
            record.parent.mkdir(parents=True)
            record.write_text(
                json.dumps({"owner": "tinjis", "schema": 1, "owned": {str(orphan): target}}),
                encoding="utf-8",
            )
            checkout_before = self.snapshot()
            home_before = tree_digest(home)
            result = self.launcher(["check", "--home", str(home)])
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("retire", result.stdout)
            self.assert_checkout_unchanged(checkout_before)
            self.assertEqual(tree_digest(home), home_before)

    def test_launcher_validate_writes_nothing_at_all(self):
        before = self.snapshot()
        result = self.launcher(["validate"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_checkout_unchanged(before)

    def test_launcher_example_writes_nothing_at_all(self):
        before = self.snapshot()
        result = self.launcher(["example"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_checkout_unchanged(before)

    def test_module_entry_point_write_free_with_dash_b(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve() / "home"
            home.mkdir()
            before = self.snapshot()
            result = self.module_run(["check", "--home", str(home)])
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assert_checkout_unchanged(before)
            self.assertEqual(sorted(home.rglob("*")), [])


class LauncherTest(unittest.TestCase):
    def test_launcher_exists_and_is_executable(self):
        launcher = CHECKOUT / "bin" / "tinjis"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK), "bin/tinjis must be executable")

    def test_module_entry_point_is_present(self):
        self.assertTrue((CHECKOUT / "tinjis" / "__main__.py").is_file())


if __name__ == "__main__":
    unittest.main()
