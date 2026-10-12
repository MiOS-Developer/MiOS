#!/usr/bin/env python3
# AI-hint: Unit test verifying ISO credential guard in Justfile.
# AI-doc: usr/share/doc/mios/manual/tests.md
"""The iso, qcow2 and vhdx recipes reach the credential guard in `miosd
artifact-build` (src/mios-rs/mios-build/src/artifacts.rs), and no recipe
sed-substitutes a placeholder."""

from __future__ import annotations
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_JUSTFILE = os.path.join(_ROOT, "Justfile")

_GUARDED = ("iso", "qcow2", "vhdx")


def _recipes(content: str) -> dict[str, str]:
    blocks = {}
    for m in re.finditer(r"^([a-z0-9][a-z0-9_-]*):[^\n]*\n((?:[ \t]+[^\n]*\n|\n)*)", content, re.M):
        blocks[m.group(1)] = m.group(2)
    return blocks


def test_iso_credential_guard():
    with open(_JUSTFILE, "r", encoding="utf-8") as f:
        content = f.read()
    recipes = _recipes(content)

    for name in _GUARDED:
        assert name in recipes, f"{name} recipe missing in Justfile"
        body = recipes[name]
        assert f"artifact-build {name}" in body, \
            f"{name} recipe does not build through `miosd artifact-build {name}`, where the credential guard is"
        assert "config/artifacts/" not in body, \
            f"{name} recipe mounts a recipe file itself instead of the rendered config"
    for name, body in recipes.items():
        assert not re.search(r"sed\b[^\n]*REPLACE", body), \
            f"{name} recipe sed-substitutes a credential placeholder"
        assert "MIOS_USER_PASSWORD_HASH:-}|g" not in body, \
            f"{name} recipe substitutes an empty password hash"


def main() -> int:
    print("[test-iso-credential-guard] Running disk credential guard verification...")
    test_iso_credential_guard()
    print("[test-iso-credential-guard] PASS: iso, qcow2 and vhdx build through miosd artifact-build.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
