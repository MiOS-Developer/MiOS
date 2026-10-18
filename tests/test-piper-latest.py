#!/usr/bin/env python3
# AI-hint: Hermetic two-sided tests for usr/share/mios/piper/install-piper.py: with a mocked PyPI JSON API (file://) and a fake pip (no network), "latest" resolves to PyPI's current release, the wheel is checked against PyPI's published sha256 before install and the resolved version is recorded; a wheel whose sha256 differs aborts with nothing installed. Also gates the Containerfile on the installer.
# AI-related: usr/share/mios/piper/install-piper.py, usr/share/mios/piper/Containerfile
import json
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(os.environ.get("MIOS_TEST_ROOT") or pathlib.Path(__file__).resolve().parents[1])
INSTALLER = ROOT / "usr/share/mios/piper/install-piper.py"
CONTAINERFILE = ROOT / "usr/share/mios/piper/Containerfile"
WHEEL = "piper_tts-1.9.2-cp39-abi3-manylinux_2_17_x86_64.whl"

FAKE_PIP = r"""#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
with open(os.environ["PIP_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
if args[0] == "download":
    dest = args[args.index("--dest") + 1]
    shutil.copyfile(os.environ["PIP_WHEEL"], os.path.join(dest, os.environ["PIP_WHEEL_NAME"]))
"""


class PiperLatest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.t = pathlib.Path(self.tmp.name)
        self.wheel = t / "payload.whl"
        self.wheel.write_bytes(b"PK-wheel-" + os.urandom(32))
        self.pip = t / "pip"
        self.pip.write_text(FAKE_PIP)
        self.pip.chmod(0o755)
        self.api = t / "pypi"
        self._publish(hashlib.sha256(self.wheel.read_bytes()).hexdigest())
        self.log = t / "pip.log"
        self.record = t / "MIOS-RESOLVED"

    def tearDown(self):
        self.tmp.cleanup()

    def _publish(self, digest):
        (self.api / "piper-tts").mkdir(parents=True, exist_ok=True)
        (self.api / "piper-tts/json").write_text(json.dumps({
            "info": {"version": "1.9.2"},
            "urls": [{"filename": WHEEL, "digests": {"sha256": digest}},
                     {"filename": "piper_tts-1.9.2.tar.gz", "digests": {"sha256": "0" * 64}}]}))

    def _run(self, spec="latest"):
        env = {**os.environ, "MIOS_PYPI_JSON": self.api.as_uri(), "MIOS_PIP": f"{sys.executable} {self.pip}",
               "PIP_LOG": str(self.log), "PIP_WHEEL": str(self.wheel), "PIP_WHEEL_NAME": WHEEL}
        return subprocess.run([sys.executable, str(INSTALLER), spec, str(self.record)],
                              env=env, capture_output=True, text=True, timeout=60)

    def test_latest_resolves_verifies_installs_and_records(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.log.read_text().splitlines()
        self.assertTrue(calls[0].startswith("download --no-deps --only-binary=:all:"), calls)
        self.assertTrue(calls[0].endswith("piper-tts==1.9.2"), calls)
        self.assertTrue(calls[1].startswith("install ") and calls[1].endswith(f"{WHEEL}[http]"), calls)
        rec = json.loads(self.record.read_text())
        self.assertEqual((rec["version"], rec["requested"], rec["file"]), ("1.9.2", "latest", WHEEL))
        self.assertEqual(rec["sha256"], hashlib.sha256(self.wheel.read_bytes()).hexdigest())

    def test_wheel_not_matching_pypi_digest_aborts(self):
        self._publish("1" * 64)
        r = self._run()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("INTEGRITY", r.stderr)
        self.assertNotIn("install ", self.log.read_text())
        self.assertFalse(self.record.exists())

    def test_containerfile_uses_the_installer_not_a_bare_pip_pin(self):
        text = CONTAINERFILE.read_text()
        self.assertIn('install-piper.py "${MIOS_SERVICES_PIPER_VERSION}"', text)
        self.assertNotIn("pip install \"piper-tts[http]==", text)


if __name__ == "__main__":
    unittest.main()
