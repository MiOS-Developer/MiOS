#!/usr/bin/env python3
# AI-hint: Unit test for mios update verb routing and backend declaration.
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Unit test verifying mios update verb configuration and routing."""

from __future__ import annotations
import os
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))

def test_update_verb_ssot():
    toml_path = os.path.join(_ROOT, "usr/share/mios/mios.toml")
    with open(toml_path, "rb") as f:
        cfg = tomllib.load(f)
    update_cfg = (cfg.get("verbs") or {}).get("update")
    assert update_cfg is not None, "[verbs.update] missing in mios.toml"
    assert update_cfg.get("cmd") == "/usr/bin/mios-update", f"Unexpected cmd for [verbs.update]: {update_cfg.get('cmd')}"

def test_known_verbs_update():
    mios_bin = os.path.join(_ROOT, "usr/bin/mios")
    with open(mios_bin, "r", encoding="utf-8") as f:
        content = f.read()
    assert '"update": ["/usr/bin/mios-update"]' in content or "'update': ['/usr/bin/mios-update']" in content, (
        "update missing from KNOWN_VERBS in usr/bin/mios"
    )

def test_profile_verbs_update():
    # The profile.d consolidation (ADR-0030) folded mios-verbs.sh into 50-mios.sh.
    profile_script = os.path.join(_ROOT, "etc/profile.d/50-mios.sh")
    with open(profile_script, "r", encoding="utf-8") as f:
        content = f.read()
    assert "mios-update" in content, "mios-update missing in etc/profile.d/50-mios.sh"

def main() -> int:
    print("[test-mios-update-verb] Running mios update verb verification...")
    test_update_verb_ssot()
    test_known_verbs_update()
    test_profile_verbs_update()
    print("[test-mios-update-verb] PASS: Verified mios update verb routing.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
