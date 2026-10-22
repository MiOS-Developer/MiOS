#!/usr/bin/env python3
# AI-hint: Hermetic two-sided tests for mios-models-firstboot floating Hugging Face sources: with a mocked hub (fake curl on PATH, no network) a `resolve/main` entry with no SSOT sha256 is resolved to the current commit + upstream LFS sha256, fetched from that immutable commit, verified and recorded in models.tsv; a mismatching file and an entry with no provable digest are never installed.
# AI-related: usr/libexec/mios/mios-models-firstboot, usr/share/mios/mios.toml [[ai.firstboot_models]]
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(os.environ.get("MIOS_TEST_ROOT") or pathlib.Path(__file__).resolve().parents[1])
FETCHER = ROOT / "usr/libexec/mios/mios-models-firstboot"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO, FILE = "LiquidAI/LFM2-700M-GGUF", "LFM2-700M-Q4_K_M.gguf"

FAKE_CURL = r"""#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1] if "-o" in args else None
url = next(a for a in args if a.startswith(("http://", "https://")))
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(url + "\n")
path = os.path.join(os.environ["MOCK_HTTP"], url.split("://", 1)[1].replace("?", "__Q__"))
if not os.path.isfile(path):
    sys.exit(22)
if out:
    shutil.copyfile(path, out)
else:
    sys.stdout.write(open(path).read())
"""


class FirstbootLatest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(self.tmp.name)
        self.t = t
        (t / "bin").mkdir()
        curl = t / "bin/curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        self.http = t / "http"
        self.weights = b"GGUF" + os.urandom(64)
        self._serve(self.weights, hashlib.sha256(self.weights).hexdigest())
        # Temp SSOT carrying this lane's fragment: the entry floats, no sha256 pin.
        self.toml = t / "mios.toml"
        self.toml.write_text(
            "[ai]\nfirstboot_pulls = true\n[[ai.firstboot_models]]\n"
            f'name = "lfm2-700m.gguf"\nsource = "https://huggingface.co/{REPO}/resolve/main/{FILE}"\n')
        self.models = t / "models"
        self.state = t / "state"
        self.log = t / "curl.log"

    def tearDown(self):
        self.tmp.cleanup()

    def _serve(self, payload, published_sha):
        api = self.http / f"huggingface.co/api/models/{REPO}/revision/main__Q__blobs=true"
        api.parent.mkdir(parents=True, exist_ok=True)
        api.write_text(json.dumps({"sha": COMMIT, "siblings": [
            {"rfilename": "README.md"},
            {"rfilename": FILE, "lfs": {"sha256": published_sha, "size": len(payload)}}]}))
        blob = self.http / f"huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(payload)

    def _run(self):
        env = {**os.environ, "PATH": f"{self.t / 'bin'}:{os.environ['PATH']}", "MOCK_HTTP": str(self.http),
               "MOCK_LOG": str(self.log), "MIOS_TOML": str(self.toml), "MIOS_MODELS_DIR": str(self.models),
               "MIOS_MODELS_STATE_DIR": str(self.state), "MIOS_TOML_GET": str(self.t / "absent"),
               "MIOS_HF_ENDPOINT": "https://huggingface.co"}
        return subprocess.run([sys.executable, str(FETCHER)], env=env, capture_output=True, text=True, timeout=120)

    def test_floating_source_resolves_commit_and_upstream_digest(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)
        urls = self.log.read_text().split()
        self.assertIn(f"https://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}", urls, "not fetched from the immutable commit")
        rows = (self.models / "models.tsv").read_text().splitlines()
        self.assertEqual(rows[0].split("\t")[:4], ["name", "source", "commit", "sha256"])
        name, source, commit, digest, _ = rows[1].split("\t")
        self.assertEqual((name, commit, digest), ("lfm2-700m.gguf", COMMIT, hashlib.sha256(self.weights).hexdigest()))
        self.assertTrue((self.state / ".models-firstboot-done").exists())

    def test_mismatching_weight_is_never_installed(self):
        self._serve(b"poisoned", hashlib.sha256(self.weights).hexdigest())
        r = self._run()
        self.assertEqual(r.returncode, 0)  # degrade-open: never blocks boot
        self.assertIn("sha256 MISMATCH", r.stdout)
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())
        self.assertFalse((self.models / "lfm2-700m.gguf.part").exists())
        self.assertFalse((self.models / "models.tsv").exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())

    def test_no_published_digest_means_no_download(self):
        api = self.http / f"huggingface.co/api/models/{REPO}/revision/main__Q__blobs=true"
        api.write_text(json.dumps({"sha": COMMIT, "siblings": [{"rfilename": FILE}]}))
        r = self._run()
        self.assertIn("upstream digest unavailable", r.stdout)
        self.assertNotIn(f"https://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}", self.log.read_text().split())
        self.assertFalse((self.models / "lfm2-700m.gguf").exists())

    def test_non_hf_source_without_digest_is_refused(self):
        self.toml.write_text('[[ai.firstboot_models]]\nname = "x.gguf"\nsource = "https://example.invalid/x.gguf"\n')
        r = self._run()
        self.assertIn("refusing an unverified weight", r.stdout)
        self.assertFalse((self.models / "x.gguf").exists())


if __name__ == "__main__":
    unittest.main()
