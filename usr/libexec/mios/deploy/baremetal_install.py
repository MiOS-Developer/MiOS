#!/usr/bin/env python3
# AI-hint: Compatibility shim -- forwards the bare-metal install CLI to the native mios-install binary.
# AI-related: tests/test-baremetal-install.py, tools/native/mios-install/src/main.rs, usr/share/mios/mios.toml
# AI-functions: native_bin, translate, main
"""Bare-metal install entry point, kept for its path and flags.

Discovery, safety gates and the install itself live in the native
mios-install (tools/native/mios-install), which runs the image's own bootc.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional


def native_bin() -> Optional[str]:
    """mios-install on PATH."""
    return shutil.which("mios-install")


def translate(argv: List[str]) -> List[str]:
    """This CLI's flags as `mios-install disk` flags.

    --force used to confirm the erase as well; it still does (--yes), and
    otherwise only skips the UEFI check. A boot disk is always refused.
    """
    out = ["disk"]
    for arg in argv:
        if arg == "--force":
            out += ["--yes", "--force"]
        else:
            out.append(arg)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = translate(sys.argv[1:] if argv is None else argv)
    exe = native_bin()
    if not exe:
        print("[baremetal_install] ERROR: mios-install is not installed; build it with "
              "automation/55-native-build.sh", file=sys.stderr)
        return 127
    os.execv(exe, [exe, *args])
    return 0


if __name__ == "__main__":
    sys.exit(main())
