#!/usr/bin/env python3
# AI-hint: Installs piper-tts[http] into the mios-piper venv: "latest" resolves to PyPI's current release, the piper-tts wheel is accepted only against the sha256 PyPI publishes for that file (dependencies by pip's own index-hash check), and the resolved version + sha256 are written to /usr/share/piper/MIOS-RESOLVED as build output.
# AI-related: usr/share/mios/piper/Containerfile, usr/libexec/mios/57-mios-sys-build.sh, tests/test-piper-latest.py
"""Usage: install-piper.py <version|latest> [resolved-record-path]"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT = "piper-tts"
JSON_API = os.environ.get("MIOS_PYPI_JSON", "https://pypi.org/pypi")


def _release(version):
    url = f"{JSON_API}/{PROJECT}/json" if version == "latest" else f"{JSON_API}/{PROJECT}/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as response:
        meta = json.load(response)
    files = {f["filename"]: f["digests"]["sha256"].lower() for f in meta.get("urls", [])
             if f.get("digests", {}).get("sha256")}
    if not files:
        raise SystemExit(f"install-piper: PyPI publishes no sha256 for {PROJECT} {meta['info']['version']}")
    return meta["info"]["version"], files


def main(argv):
    if len(argv) < 2 or not argv[1]:
        raise SystemExit(__doc__)
    record = Path(argv[2] if len(argv) > 2 else "/usr/share/piper/MIOS-RESOLVED")
    version, published = _release(argv[1])
    pip = [sys.executable, "-m", "pip"] if not os.environ.get("MIOS_PIP") else os.environ["MIOS_PIP"].split()
    with tempfile.TemporaryDirectory() as stage:
        subprocess.run([*pip, "download", "--no-deps", "--only-binary=:all:", "--dest", stage,
                        f"{PROJECT}=={version}"], check=True)
        wheels = [p for p in Path(stage).iterdir() if p.is_file()]
        if len(wheels) != 1:
            raise SystemExit(f"install-piper: expected one {PROJECT} wheel, got {[p.name for p in wheels]}")
        wheel = wheels[0]
        want = published.get(wheel.name)
        if not want:
            raise SystemExit(f"install-piper: PyPI publishes no sha256 for {wheel.name}")
        got = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if got != want:
            print(f"install-piper: INTEGRITY: {wheel.name} sha256 {got} != PyPI {want}", file=sys.stderr)
            return 3
        subprocess.run([*pip, "install", "--no-cache-dir", f"{wheel}[http]"], check=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"component": PROJECT, "requested": argv[1], "version": version,
                                  "file": wheel.name, "sha256": got}) + "\n")
    print(f"install-piper: {PROJECT} {version} ({wheel.name}, sha256 {got[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
