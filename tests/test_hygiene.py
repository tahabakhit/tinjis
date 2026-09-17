"""Repository hygiene: no mutation, no network, no operator paths, no secrets.

Scope, stated precisely so the claims below are exactly what the tests prove:

* The scans walk every regular file in the working tree, excluding only the
  directories in :data:`IGNORED_DIRS` (`.git` and local caches). There is no
  suffix allowlist, so a new file type cannot escape by being unrecognised; a
  test proves the scan set covers every file in the tree and that every scanned
  file decodes as UTF-8.
* The needle scans (`/Users/`, the operator handle, the copyright name) skip
  this module, because it necessarily contains the strings it searches for.
  Nothing else is skipped. The credential scan is pattern-based rather than
  needle-based, so it does not need that exclusion and still covers this file.
* Every Python module recursively below ``tinjis/`` is parsed, not imported,
  for bounded AST regression checks, so an import-time side effect cannot
  influence the result. The checks cover straightforward mutation, process,
  and network calls; they are not a general proof of Python behavior.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
PACKAGE = CHECKOUT / "tinjis"
for _path in (CHECKOUT, CHECKOUT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# The exact copyright identity the project records.
ALLOWED_NAME = "Taha Bakhit"
NAME_ALLOWED_FILES = {"LICENSE", "NOTICE", "README.md", "AGENTS.md", "PROVENANCE.md"}

# Directories excluded from the scans. Every one is a version control or local
# cache directory, and a test asserts each is gitignored.
IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "node_modules",
}


def working_tree_files() -> list:
    """Every regular, non-symlink file in the working tree under Tinjis control."""
    files = []
    for path in sorted(CHECKOUT.rglob("*")):
        relative = path.relative_to(CHECKOUT)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        files.append(path)
    return files


def needle_scanned_files() -> list:
    """Files the literal-needle scans read: everything except this module."""
    return [path for path in working_tree_files() if path.resolve() != SELF]


class ScanScopeTest(unittest.TestCase):
    def test_the_scan_set_covers_every_file_in_the_working_tree(self):
        scanned = {path.resolve() for path in working_tree_files()}
        for path in CHECKOUT.rglob("*"):
            relative = path.relative_to(CHECKOUT)
            if any(part in IGNORED_DIRS for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            with self.subTest(path=str(relative)):
                self.assertIn(path.resolve(), scanned)

    def test_every_scanned_file_is_text(self):
        for path in working_tree_files():
            with self.subTest(path=str(path.relative_to(CHECKOUT))):
                path.read_text(encoding="utf-8")

    def test_files_without_a_python_suffix_are_still_scanned(self):
        names = {path.name for path in working_tree_files()}
        for expected in ("tinjis", "LICENSE", "NOTICE", ".gitignore", ".ruff.toml"):
            self.assertIn(expected, names)

    def test_every_ignored_directory_is_gitignored(self):
        text = (CHECKOUT / ".gitignore").read_text(encoding="utf-8")
        for name in sorted(IGNORED_DIRS - {".git"}):
            with self.subTest(name=name):
                self.assertIn(f"{name}/", text, f"{name} is skipped by the scans but not ignored")


class StdlibOnlyTest(unittest.TestCase):
    def test_every_package_module_imports_only_the_standard_library(self):
        allowed = set(sys.stdlib_module_names) | {"tinjis"}
        problems = []
        for path in sorted(PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        if root not in allowed:
                            problems.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    root = (node.module or "").split(".")[0]
                    if root not in allowed:
                        problems.append(f"{path.name}: from {node.module} import ...")
        self.assertEqual(problems, [])

    def test_every_python_file_compiles(self):
        for path in sorted(CHECKOUT.rglob("*.py")):
            relative = path.relative_to(CHECKOUT)
            if any(part in IGNORED_DIRS for part in relative.parts):
                continue
            with self.subTest(path=str(relative)):
                # compile() rather than py_compile: compiling must not write a
                # bytecode cache into the checkout.
                compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_no_package_manager_or_installer_manifest_is_required(self):
        for name in ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "poetry.lock"):
            self.assertFalse((CHECKOUT / name).exists(), name)

    def test_clone_run_launcher_and_module_entry_point_exist(self):
        self.assertTrue((CHECKOUT / "bin" / "tinjis").is_file())
        self.assertTrue((PACKAGE / "__main__.py").is_file())


class NoMutationOrNetworkTest(unittest.TestCase):
    """Bounded AST regression guards for straightforward unsafe capabilities.

    These checks recursively inspect direct calls and imports in ``tinjis/``.
    They are intentionally not described as a proof of arbitrary Python
    behavior. Indirection through ``getattr`` could evade a call-name check, so
    a separate guard rejects ``getattr`` and ``setattr`` too.
    """

    BANNED_IMPORTS = frozenset(
        {
            "asyncio",
            "ctypes",
            "ftplib",
            "http",
            "multiprocessing",
            "os.path",  # not a module root; kept explicit for future refactors
            "pickle",
            "popen2",
            "shutil",
            "smtplib",
            "socket",
            "ssl",
            "subprocess",
            "tempfile",
            "telnetlib",
            "urllib",
            "webbrowser",
            "xmlrpc",
        }
    )

    BANNED_BARE = frozenset(
        {
            # builtins that escape the surface the AST check can reason about
            "__import__",
            "breakpoint",
            "compile",
            "eval",
            "exec",
            "exit",
            "input",
            "open",
            "quit",
        }
    )

    # Process and interpreter control. Banned in every module including the
    # sanctioned writer: a mutation site still has no business spawning a
    # process, changing directory, or sending a signal.
    PROCESS_ATTRS = frozenset(
        {
            "system",
            "popen",
            "fork",
            "forkpty",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "posix_spawn",
            "posix_spawnp",
            "execv",
            "execve",
            "execvp",
            "execvpe",
            "execl",
            "execle",
            "execlp",
            "execlpe",
            "kill",
            "killpg",
            "chdir",
            "chroot",
        }
    )

    # Filesystem writes. Banned everywhere except ``writer.py``, the package's
    # one sanctioned mutation site. The CLI remains unreachable to it.
    FS_ATTRS = frozenset(
        {
            "write_text",
            "write_bytes",
            "mkdir",
            "makedirs",
            "touch",
            "unlink",
            "remove",
            "removedirs",
            "rmdir",
            "rename",
            "renames",
            "replace",
            "open",
            "link",
            "symlink",
            "symlink_to",
            "hardlink_to",
            "chmod",
            "lchmod",
            "chown",
            "lchown",
            "truncate",
            "ftruncate",
            "fsync",
            "fdatasync",
            "utime",
            "mknod",
            "mkfifo",
            "copy",
            "copy2",
            "copyfile",
            "copytree",
            "move",
            "rmtree",
            "atomicwrites",
        }
    )

    BANNED_ATTRS = PROCESS_ATTRS | FS_ATTRS

    #: The only module allowed to call a filesystem mutation. A test proves this
    #: set is exactly the set of modules that actually contain such a call, so a
    #: second mutation site cannot appear unnoticed.
    MUTATION_MODULES = frozenset({"writer.py"})

    #: The filesystem calls ``writer.py`` is expected to use, and nothing more.
    #: Widening this set is a deliberate, reviewable act.
    WRITER_FS_ATTRS = frozenset({"open", "fsync", "link", "mkdir", "rename", "symlink", "unlink"})

    def package_trees(self):
        for path in sorted(PACKAGE.rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def called_names(self, tree) -> tuple:
        """(bare builtin call names, attribute call names) in one module."""
        bare: set = set()
        attrs: set = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                bare.add(func.id)
            elif isinstance(func, ast.Attribute):
                attrs.add(func.attr)
        return bare, attrs

    def test_no_mutating_or_process_call_exists_outside_the_writer(self):
        for path, tree in self.package_trees():
            if path.name in self.MUTATION_MODULES:
                continue
            bare, attrs = self.called_names(tree)
            with self.subTest(path=path.name, kind="builtin"):
                self.assertEqual(sorted(bare & self.BANNED_BARE), [], path.name)
            with self.subTest(path=path.name, kind="attribute"):
                self.assertEqual(sorted(attrs & self.BANNED_ATTRS), [], path.name)

    def test_writer_is_the_only_module_that_mutates(self):
        offenders = set()
        for path, tree in self.package_trees():
            _bare, attrs = self.called_names(tree)
            if attrs & self.FS_ATTRS:
                offenders.add(path.name)
        self.assertEqual(offenders, set(self.MUTATION_MODULES))

    def test_writer_uses_no_process_call_and_no_unlisted_filesystem_call(self):
        path = PACKAGE / "writer.py"
        bare, attrs = self.called_names(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
        self.assertEqual(sorted(bare & self.BANNED_BARE), [], "writer.py")
        self.assertEqual(sorted(attrs & self.PROCESS_ATTRS), [], "writer.py")
        self.assertEqual(sorted((attrs & self.FS_ATTRS) - self.WRITER_FS_ATTRS), [], "writer.py")

    def test_no_network_or_process_module_is_imported(self):
        for path, tree in self.package_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    roots = {(node.module or "").split(".")[0]}
                else:
                    continue
                found = sorted(roots & self.BANNED_IMPORTS)
                with self.subTest(path=path.name):
                    self.assertEqual(found, [], f"{path.name} imports {found}")

    def test_package_does_not_use_getattr_or_indirection(self):
        for path, tree in self.package_trees():
            bare, attrs = self.called_names(tree)
            with self.subTest(path=path.name):
                self.assertNotIn("getattr", bare | attrs)
                self.assertNotIn("setattr", bare | attrs)

    def test_straightforward_bypasses_are_in_the_guard_sets(self):
        tree = ast.parse("os.replace(a, b)\nos.link(a, b)\nPath('x').open('w')\nopen('x', 'w')\n")
        bare, attrs = self.called_names(tree)
        self.assertIn("open", bare & self.BANNED_BARE)
        self.assertEqual({"replace", "link", "open"}, attrs & self.BANNED_ATTRS)

    def test_package_tree_discovery_is_recursive(self):
        discovered = {path for path, _tree in self.package_trees()}
        expected = set(PACKAGE.rglob("*.py"))
        self.assertEqual(discovered, expected)


class PersonalPathTest(unittest.TestCase):
    def test_no_absolute_operator_paths(self):
        for path in needle_scanned_files():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(CHECKOUT)):
                self.assertNotIn("/Users/", text)
                self.assertNotIn("/home/", text)

    def test_no_lowercase_operator_handle(self):
        handle = "taha" + "bakhit"
        for path in needle_scanned_files():
            lowered = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.relative_to(CHECKOUT)):
                self.assertNotIn(handle, lowered)

    def test_the_copyright_identity_appears_only_in_expected_files(self):
        for path in needle_scanned_files():
            text = path.read_text(encoding="utf-8")
            if ALLOWED_NAME in text:
                self.assertIn(
                    path.name,
                    NAME_ALLOWED_FILES,
                    f"unexpected copyright identity in {path.relative_to(CHECKOUT)}",
                )


class SecretTest(unittest.TestCase):
    """Pattern-based: a needle list would match this file's own needle list."""

    PATTERNS = (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|secret|token|password|passwd|passphrase"
            r"|private[_-]?key|access[_-]?key|client[_-]?secret|authorization)\b"
            r"\s*[:=]\s*[\"'][^\"'\s]{6,}[\"']"
        ),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
        re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    )

    def test_no_credential_shaped_content(self):
        for path in working_tree_files():
            text = path.read_text(encoding="utf-8")
            for pattern in self.PATTERNS:
                match = pattern.search(text)
                with self.subTest(path=path.relative_to(CHECKOUT), pattern=pattern.pattern):
                    self.assertIsNone(
                        match, f"{path.relative_to(CHECKOUT)}: {match.group(0) if match else ''}"
                    )

    def test_no_env_file_or_runtime_state_is_present(self):
        for name in (".env", "auth.json", "credentials.json", "state.db", "config.yaml"):
            self.assertFalse((CHECKOUT / name).exists(), name)


class DocumentationTest(unittest.TestCase):
    REQUIRED = (
        "LICENSE",
        "NOTICE",
        "README.md",
        "SECURITY.md",
        "AGENTS.md",
        ".gitignore",
        "docs/ARCHITECTURE.md",
        "docs/MANIFEST.md",
        "docs/PROVENANCE.md",
        "docs/STATUS.md",
    )

    def test_required_public_files_exist(self):
        for name in self.REQUIRED:
            with self.subTest(name=name):
                self.assertTrue((CHECKOUT / name).is_file(), name)

    def test_license_is_apache_two_point_zero(self):
        text = (CHECKOUT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0, January 2004", text)
        self.assertIn("www.apache.org/licenses/LICENSE-2.0", text)
        self.assertNotIn("[yyyy]", text)
        self.assertIn(f"Copyright 2026 {ALLOWED_NAME}", text)

    def test_notice_records_the_copyright_identity(self):
        text = (CHECKOUT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn(ALLOWED_NAME, text)

    def test_notice_does_not_claim_atlas_is_apache_licensed(self):
        text = (CHECKOUT / "NOTICE").read_text(encoding="utf-8").lower()
        self.assertIn("no root license file", text)
        self.assertIn("no license of atlas as a whole is claimed", text)

    def test_status_marks_operational_versus_deferred_and_the_removal(self):
        text = (CHECKOUT / "docs" / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("Operational", text)
        self.assertIn("Scaffolded", text)
        self.assertIn("Removed", text)

    def test_status_records_the_writer_foundation_and_withholds_apply(self):
        text = (CHECKOUT / "docs" / "STATUS.md").read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertIn("writer foundation", lowered)
        self.assertIn("cli-unreachable", lowered)
        self.assertIn("no `apply` command", lowered)
        self.assertIn("ownership migration", lowered)

    def test_provenance_names_every_source_with_a_hash(self):
        text = (CHECKOUT / "docs" / "PROVENANCE.md").read_text(encoding="utf-8")
        self.assertIn("Atlas", text)
        self.assertIn("Apache-2.0", text)
        self.assertIn("SHA-256", text)
        for name in (
            "bin/atlas_manifest.py",
            "bin/skill_selection.py",
            "bin/bootstrap.py",
            "bin/pi_bootstrap.py",
            "tests/legacy_topology_fixture.py",
            "tests/test_atlas_manifest.py",
            "atlas.json",
        ):
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_provenance_distinguishes_committed_from_working_tree(self):
        text = (CHECKOUT / "docs" / "PROVENANCE.md").read_text(encoding="utf-8")
        self.assertIn("untracked", text.lower())
        self.assertIn("modified", text.lower())

    def test_provenance_does_not_claim_atlas_is_apache_licensed(self):
        text = (CHECKOUT / "docs" / "PROVENANCE.md").read_text(encoding="utf-8").lower()
        self.assertIn("no license of atlas as a whole is claimed", text)
        self.assertIn("no root", text)
        self.assertIn("license file", text)

    def test_readme_does_not_claim_unverified_platform_support(self):
        text = (CHECKOUT / "README.md").read_text(encoding="utf-8")
        self.assertIn("macOS", text)
        self.assertIn("not verified", text)

    def test_readme_states_the_read_only_scope(self):
        text = (CHECKOUT / "README.md").read_text(encoding="utf-8")
        self.assertIn("read-only", text.lower())
        self.assertNotIn("tinjis apply", text)

    def test_no_document_references_an_unpublished_url(self):
        for path in working_tree_files():
            if path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(CHECKOUT)):
                self.assertNotIn("github.com/", text)


class RepositoryShapeTest(unittest.TestCase):
    """Product invariants that remain valid after the repository is committed."""

    def test_no_submodule_is_declared(self):
        self.assertFalse((CHECKOUT / ".gitmodules").exists())


if __name__ == "__main__":
    unittest.main()
