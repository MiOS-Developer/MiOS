#!/usr/bin/env python3
# AI-hint: Two-sided test for tools/audit-image-provisioning.py -- passes on the shipped mios.toml, fails naming the item for each planted breakage.
# AI-related: tools/audit-image-provisioning.py, usr/share/mios/mios.toml, Justfile
"""Each planted mios.toml breakage must fail the audit and name its item."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SCRIPT = os.path.join(_ROOT, "tools", "audit-image-provisioning.py")
_TOML = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")

# Scopes a plant to the [branding] table: [windows.xbox.settings.display] has
# its own living_wallpaper key, which an unscoped pattern also matched.
_BRANDING = r'(^\[branding\]\n(?:(?!\[).*\n)*?)'

# (case name, pattern, replacement, item the failure must name)
PLANTS = [
    ("version-malformed",
     r'^(mios_version\s*=\s*)"[^"]*"', r'\1"not-a-version"',
     "[meta].mios_version"),
    ("wallpaper-not-bool",
     _BRANDING + r'^(living_wallpaper\s*=\s*)true\b', r'\1\2"yes"',
     "[branding].living_wallpaper"),
    ("wallpaper-absent",
     _BRANDING + r'^living_wallpaper\s*=.*\n', r'\1',
     "[branding].living_wallpaper"),
    ("budget-non-positive",
     r'^(runner_disk_budget_gb\s*=\s*)\d+', r'\g<1>0',
     "[build.bake].runner_disk_budget_gb"),
]


def _run(toml_text: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "tools"))
        os.makedirs(os.path.join(tmp, "usr", "share", "mios"))
        shutil.copy2(_SCRIPT, os.path.join(tmp, "tools", "audit-image-provisioning.py"))
        with open(os.path.join(tmp, "usr", "share", "mios", "mios.toml"), "w",
                  encoding="utf-8") as fh:
            fh.write(toml_text)
        proc = subprocess.run(
            [sys.executable, os.path.join(tmp, "tools", "audit-image-provisioning.py")],
            capture_output=True, text=True, timeout=60)
        return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    with open(_TOML, encoding="utf-8") as fh:
        real = fh.read()
    failures = []

    rc, out = _run(real)
    if rc != 0:
        failures.append(f"positive: real mios.toml must pass, got rc={rc}\n{out}")
    if "taskbar" in out.lower():
        failures.append("positive: audit still reports [branding].taskbar_align, "
                        "a key nothing declares or consumes")

    for name, pattern, repl, item in PLANTS:
        planted, n = re.subn(pattern, repl, real, count=0, flags=re.M)
        if n != 1:
            failures.append(f"{name}: plant matched {n} times (expected exactly 1); "
                            "the fixture no longer matches mios.toml")
            continue
        rc, out = _run(planted)
        if rc == 0:
            failures.append(f"{name}: audit exited 0 on a planted breakage of {item}")
        elif item not in out:
            failures.append(f"{name}: audit failed but did not name {item}:\n{out}")
        else:
            print(f"  ok {name}: rc={rc}, names {item}")

    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("test-audit-image-provisioning: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
