"""Independent, test-only copy of the bundled example topology.

This fixture is deliberately separate from ``tinjis/topology.py`` (the
compiled executable lock) and from ``examples/tinjis.json`` (the authored
example). It is the independent third description used to prove that the
authored example and the compiled lock still agree: a drift in either one
fails a test instead of silently redefining what Tinjis may write.

It is not a runtime manifest and production code never reads it.
"""

from __future__ import annotations

from typing import Any


def example_payload() -> dict:
    """A fresh, mutable dict of the expected bundled example topology."""
    return {
        "schema": 1,
        "runtime": {"root": ".config/tinjis-example"},
        "consumers": [
            {
                "name": "pi",
                "root": "pi",
                "settings": "examples/project/pi/settings.json",
                "links": [
                    {
                        "destination": "agents/example-agent",
                        "source": "examples/project/pi/agents/example-agent",
                    },
                    {
                        "destination": "extensions/example-tool",
                        "source": "examples/project/pi/extensions/example-tool",
                    },
                ],
            },
            {
                "name": "hermes",
                "root": "hermes",
                "settings": "examples/project/hermes/config.overlay.yaml",
                "links": [
                    {
                        "destination": "skills/example-notes",
                        "source": "examples/project/hermes/skills/example-notes",
                    }
                ],
            },
            {
                "name": "herdr",
                "root": "herdr",
                "settings": "examples/project/herdr/config.toml",
                "links": [],
            },
        ],
        "selection": {
            "inventory": "examples/project/inventory.json",
            "source_root": "examples/project/shared",
            "destination": ".local/share/tinjis-example/shared",
        },
        "files": [
            {
                "label": "herdr/status.sh",
                "source": "examples/project/herdr/status.sh",
                "destination": ".config/tinjis-example/herdr/status.sh",
            }
        ],
        "legacy_paths": ["legacy-example-link"],
        "reviewed_actions": [
            {"consumer": "pi", "command": "example-package-manager install pi"},
            {"consumer": None, "command": "herdr integration install pi"},
        ],
    }


def write_example_tree(root: Any) -> None:
    """Create the minimal source tree a bundled-example-shaped manifest needs.

    Used by tests that parse the fixture payload against a synthetic checkout
    rather than the real one.
    """
    from pathlib import Path

    root = Path(root)
    files = {
        "examples/project/pi/settings.json": "{}\n",
        "examples/project/pi/agents/example-agent/AGENT.md": "agent\n",
        "examples/project/pi/extensions/example-tool/README.md": "tool\n",
        "examples/project/hermes/config.overlay.yaml": "example: {}\n",
        "examples/project/hermes/skills/example-notes/SKILL.md": "notes\n",
        "examples/project/herdr/config.toml": "[example]\n",
        "examples/project/herdr/status.sh": "#!/bin/sh\n",
        "examples/project/inventory.json": (
            '{"skills": ["example-checklist", "example-report"]}\n'
        ),
        "examples/project/shared/example-checklist/SKILL.md": "checklist\n",
        "examples/project/shared/example-report/SKILL.md": "report\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
