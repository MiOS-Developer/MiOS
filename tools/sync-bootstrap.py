#!/usr/bin/env python3
# AI-hint: Compatibility CLI for native mios-gen bootstrap-sync; no Python mirror or silent fallback.
# AI-doc: usr/share/doc/mios/manual/tools.md
# AI-related: tools/native/mios-gen/src/bootstrap_sync.rs, usr/share/mios/mios.toml
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    binary = os.environ.get("MIOS_GEN_BIN")
    native_dir = os.environ.get("MIOS_NATIVE_BIN_DIR")
    if native_dir:
        binary = str(Path(native_dir) / ("mios-gen.exe" if os.name == "nt" else "mios-gen"))
    elif not binary:
        binary = shutil.which("mios-gen")
    if not binary or not Path(binary).is_file():
        print("[sync-bootstrap] required native mios-gen is missing; install the SSOT release catalog", file=sys.stderr)
        return 1
    if not any(arg == "--root" or arg.startswith("--root=") for arg in args):
        args[:0] = ["--root", str(Path(__file__).resolve().parents[1])]
    try:
        return subprocess.run([binary, "bootstrap-sync", *args], check=False).returncode
    except OSError as error:
        print(f"[sync-bootstrap] native mios-gen could not run: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
