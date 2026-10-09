#!/usr/bin/env python3
# AI-hint: Post-build image-audit validator asserting provisioning status (AGY / task_3332).
# AI-related: usr/share/mios/mios.toml, tests/test-audit-image-provisioning.py, Justfile

import os
import re
import sys
import tomllib

# SemVer 2.0.0 (https://semver.org) -- a format definition, not a tunable.
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")

_ABSENT = object()


def _lookup(data, dotted):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def _check_version(v):
    if not isinstance(v, str) or not _SEMVER.match(v):
        return f"must be a SemVer string, got {v!r}"
    return None


def _check_bool(v):
    if not isinstance(v, bool):
        return f"must be a TOML boolean, got {v!r}"
    return None


def _check_positive_int(v):
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return f"must be a positive integer, got {v!r}"
    return None


# (table, key, label, validator)
ITEMS = (
    ("meta", "mios_version", "SSOT Version", _check_version),
    ("branding", "living_wallpaper", "Living Wallpaper Enabled", _check_bool),
    ("build.bake", "runner_disk_budget_gb", "Bake Runner Disk Budget (GB)", _check_positive_int),
)


def main():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    toml_path = os.path.join(root_dir, "usr/share/mios/mios.toml")

    print("[audit-image-provisioning] Starting image provisioning audit...")

    if not os.path.exists(toml_path):
        print(f"ERROR: mios.toml SSOT not found at {toml_path}", file=sys.stderr)
        return 1

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    results, failed = [], []
    for table, key, label, check in ITEMS:
        item = f"[{table}].{key}"
        value = _lookup(data, f"{table}.{key}")
        problem = "is absent" if value is _ABSENT else check(value)
        if problem:
            failed.append(item)
            results.append(f"[FAIL] {label}: {item} {problem}")
        else:
            results.append(f"[OK] {label}: {value}")

    print("\n--- Image Provisioning Audit Summary ---")
    for res in results:
        print(f"  {res}")

    if failed:
        print(f"\n[audit-image-provisioning] Audit report FAIL: {', '.join(failed)}",
              file=sys.stderr)
        return 1
    print("\n[audit-image-provisioning] Audit report PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
