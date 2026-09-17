"""Clone-run command line interface. Read-only by construction.

Three commands, no writer:

``validate [--manifest PATH]``
    Parse and validate any well-formed schema-v1 manifest: strict JSON,
    structural rules, NFC destination grammar, folded collision rules, and
    checkout source containment. Writes nothing. Never applies the topology
    lock, so a candidate configuration can be reviewed without being
    executable. It does not require the authored sources to exist, so a
    manifest can be inspected on a machine that does not have the tree.

``check``
    The full read-only preflight of the *bundled example* against a runtime
    root: declared-input existence/type/symlink policy, resolved selection
    leaves, folded cross-namespace destination collisions, the ownership
    record, and the resulting plan. No write, no lock file, no cache, no
    network, no state directory. This is the default action. It exits non-zero
    when anything is out of sync, so it works as a drift check.

``example``
    Print the authored bundled example manifest and whether it still matches
    the compiled topology lock.

There is no ``apply``. The CLI has no filesystem mutation path at all, so no
exposed route can create, replace, or remove a link; the writer foundation in
:mod:`tinjis.writer` is never imported here. No partial-failure or
destructive-write time-of-check/time-of-use window exists on any command. Read
results remain snapshots. See ``docs/STATUS.md`` for what the unexposed writer
does and which preconditions still block exposing it.

The CLI never runs a reviewed action, never installs a package, and never
touches a consumer's settings file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .errors import TinjisError
from .manifest import (
    MANIFEST_NAME,
    Manifest,
    dumps_manifest,
    load_manifest,
    read_manifest_file,
    validate_declared_inputs,
)
from .ownership import owned_path, read_owned
from .plan import conflicts, plan_projections, projection_boundary
from .topology import LOCKED_EXAMPLE_TOPOLOGY, topology_mismatch

# ``tinjis/cli.py`` -> ``tinjis`` -> checkout root.
CHECKOUT = Path(__file__).resolve().parents[1]
EXAMPLE_MANIFEST = CHECKOUT / "examples" / MANIFEST_NAME

COMMANDS = ("validate", "check", "example")


def _canonical_dir(raw: str, *, what: str) -> Path:
    path = Path(raw)
    if path.is_symlink():
        raise TinjisError(f"{what} must not be a symlink: {path}")
    if not path.is_dir():
        raise TinjisError(f"{what} is not a directory: {path}")
    return path.resolve()


def _load_example() -> Manifest:
    """Load the bundled example and require it to match the compiled lock."""
    parsed = load_manifest(CHECKOUT, EXAMPLE_MANIFEST)
    mismatch = topology_mismatch(parsed, LOCKED_EXAMPLE_TOPOLOGY)
    if mismatch is not None:
        raise TinjisError(
            f"the bundled example manifest {EXAMPLE_MANIFEST} disagrees with the "
            f"compiled topology lock on {mismatch}. Refusing to plan."
        )
    return parsed


def _print_boundary(manifest: Manifest, print_fn=print) -> None:
    boundary = projection_boundary(manifest)
    for parts in boundary.exact:
        print_fn(f"boundary file      {'/'.join(parts)}")
    print_fn(f"boundary selection {'/'.join(boundary.prefix)}/<one-leaf>  (one direct child only)")


def _print_actions(manifest: Manifest, print_fn=print) -> None:
    if not manifest.reviewed_actions:
        return
    print_fn("notice   reviewed actions (printed, never run automatically):")
    for action in manifest.reviewed_actions:
        scope = action.consumer or "global"
        print_fn(f"         [{scope}] {action.command}")


def _print_legacy(manifest: Manifest, home: Path, print_fn=print) -> None:
    for name in manifest.legacy_paths:
        path = home / manifest.runtime.root / name
        if path.is_symlink():
            print_fn(f"legacy   {path} (retired whole-root link; remove it manually)")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def command_validate(args) -> int:
    # Preserve the supplied pathname so read_manifest_file can refuse a symlink;
    # resolving first would erase that evidence.
    path = Path(args.manifest).expanduser() if args.manifest else EXAMPLE_MANIFEST
    manifest = load_manifest(CHECKOUT, path)
    print(f"manifest {path}")
    print(f"checkout {CHECKOUT}")
    print(f"schema   {manifest.schema}")
    print(f"runtime  {manifest.runtime.root}")
    for consumer in manifest.consumers:
        print(
            f"consumer {consumer.name} root={consumer.root} "
            f"settings={consumer.settings} links={len(consumer.links)}"
        )
    print(
        f"selection {manifest.selection.source_root} "
        f"-> {manifest.selection.destination} "
        f"(inventory {manifest.selection.inventory})"
    )
    for entry in manifest.files:
        print(f"file     {entry.label} {entry.source} -> {entry.destination}")
    for action in manifest.reviewed_actions:
        print(f"action   [{action.consumer or 'global'}] {action.command}")
    mismatch = topology_mismatch(manifest, LOCKED_EXAMPLE_TOPOLOGY)
    if mismatch is None:
        print("lock     matches the compiled bundled-example topology")
    else:
        print(f"lock     not the bundled example: differs on {mismatch}")
        print("         (`validate` inspects any manifest; it is read-only either way)")
    print("ok       manifest is valid; nothing was written and nothing can be")
    print("         declaration scope only: no external tool is configured by this")
    return 0


def command_check(args) -> int:
    home = _canonical_dir(args.home, what="runtime root (HOME)")
    manifest = _load_example()
    boundary = projection_boundary(manifest)
    print(f"checkout {CHECKOUT}")
    print(f"home     {home}")
    print(f"manifest {EXAMPLE_MANIFEST}")
    print(f"runtime  {home / manifest.runtime.root}")
    print(f"owned    {owned_path(home)}")
    _print_boundary(manifest)
    for consumer in manifest.consumers:
        print(
            f"declared {consumer.name}: root {home / manifest.runtime.root / consumer.root} "
            "(declared only; Tinjis does not configure this consumer)"
        )
    # Authored inputs must exist, have the declared type, and not be symlinks.
    try:
        validate_declared_inputs(CHECKOUT, manifest)
    except TinjisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # The ownership record is untrusted input, so it is read through the
    # manifest's boundary before it is used to interpret any existing symlink.
    try:
        owned = read_owned(owned_path(home), home, boundary)
    except TinjisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        plans = plan_projections(home, CHECKOUT, owned, manifest)
    except TinjisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for plan in plans:
        print(f"  {plan.action:<9} {plan.dest}")
    for plan in conflicts(plans):
        print(f"conflict {plan.dest} ({plan.label}): {plan.detail}")
    _print_legacy(manifest, home)
    _print_actions(manifest)
    if conflicts(plans):
        print("\nout of sync: conflicts require explicit reconciliation; nothing was changed")
        return 1
    if any(plan.would_change for plan in plans):
        print("\nout of sync: the declarations are not reflected at this runtime root")
        print("         Tinjis v0 is read-only; there is no command that applies this plan.")
        return 1
    print("\nin sync: file and selected-resource projections only")
    return 0


def command_example(args) -> int:
    if not EXAMPLE_MANIFEST.is_file():
        print(f"error: bundled example is missing: {EXAMPLE_MANIFEST}", file=sys.stderr)
        return 1
    if args.raw:
        sys.stdout.write(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
        return 0
    try:
        parsed = read_manifest_file(EXAMPLE_MANIFEST)
    except TinjisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(dumps_manifest(parsed))
    mismatch = topology_mismatch(parsed, LOCKED_EXAMPLE_TOPOLOGY)
    status = "matches compiled bundled-example topology" if mismatch is None else mismatch
    print(f"# lock: {status}")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinjis",
        description=(
            "Read-only configuration declaration parser, validator, and planner. "
            "The tinjis CLI never writes; all external tools remain "
            "user-managed prerequisites."
        ),
    )
    parser.add_argument("--version", action="version", version=f"tinjis {__version__}")
    sub = parser.add_subparsers(dest="command")

    validate = sub.add_parser(
        "validate",
        help="strictly validate a schema-v1 manifest (writes nothing)",
        description=(
            "Strict JSON, structural, NFC and folded-collision, and "
            "source-containment checks. The topology lock is reported but not "
            "enforced: validate inspects, it never writes, and it does not "
            "require the authored sources to exist."
        ),
    )
    validate.add_argument(
        "--manifest",
        default=None,
        help=f"manifest path (default: the bundled example, {MANIFEST_NAME} under examples/)",
    )
    validate.set_defaults(func=command_validate)

    check = sub.add_parser(
        "check",
        help="run the full read-only preflight of the bundled example (default)",
        description=(
            "Read-only preflight: declared-input existence, type, and symlink "
            "policy; resolved selection leaves; folded cross-namespace "
            "destination collisions; the ownership record; and the plan. Never "
            "writes, locks, caches, or uses the network."
        ),
    )
    check.add_argument("--home", default=str(Path.home()), help="runtime root (default: HOME)")
    check.set_defaults(func=command_check)

    example = sub.add_parser(
        "example",
        help="print the bundled example manifest and its lock status",
    )
    example.add_argument("--raw", action="store_true", help="print the file verbatim")
    example.set_defaults(func=command_example)

    return parser


def main(argv: list | None = None) -> int:
    parser = build_parser()
    tokens = list(sys.argv[1:] if argv is None else argv)
    # ``check`` is the default action. Top-level ``--help``/``--version`` stay
    # top-level; any other leading option belongs to ``check``.
    if not tokens:
        tokens = ["check"]
    elif tokens[0] in ("-h", "--help", "--version"):
        pass
    elif tokens[0].startswith("-"):
        tokens = ["check", *tokens]
    elif tokens[0] not in COMMANDS:
        parser.error(f"unknown command: {tokens[0]!r}")
    args = parser.parse_args(tokens)
    try:
        return args.func(args)
    except TinjisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
