#!/usr/bin/env python3
# AI-hint: Hermetic two-sided tests for mios-models-firstboot floating Hugging Face sources: with a mocked hub (fake curl on PATH, no network) a `resolve/main` entry with no SSOT sha256 is resolved to the current commit + upstream LFS sha256, fetched from that immutable commit, verified and recorded in models.tsv; a mismatching file and an entry with no provable digest are never installed.
# AI-related: usr/libexec/mios/mios-models-firstboot, usr/share/mios/mios.toml [ai.firstboot_models]
import hashlib
import json
import os
import pathlib
import shlex
import configparser
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
               "MIOS_HF_ENDPOINT": "https://huggingface.co",
               "MIOS_HOST_TOML": str(self.t / "host.toml"), "MIOS_USER_TOML": str(self.t / "user.toml"),
               "MIOS_VENDOR_TOML_D": str(self.t / "absent"), "MIOS_HOST_TOML_D": str(self.t / "absent"),
               "MIOS_USER_TOML_D": str(self.t / "absent")}
        return subprocess.run([sys.executable, str(FETCHER)], env=env, capture_output=True, text=True, timeout=120)

    def test_host_layer_disables_pulls_without_retiring_retries(self):
        (self.t / "host.toml").write_text("[ai]\nfirstboot_pulls=false\n")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())

    def test_required_light_fleet_is_verified_before_ready(self):
        with self.toml.open("a") as stream:
            stream.write(f'\n[llamacpp]\nbake_models="lfm2-700m.gguf={REPO}:{FILE}"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.models / ".ready").exists())
        self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)  # metadata + blob, no duplicate fetch

    def test_existing_corrupt_model_is_replaced_only_with_verified_bytes(self):
        self.models.mkdir()
        (self.models / "lfm2-700m.gguf").write_bytes(b"broken")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.models / "lfm2-700m.gguf").read_bytes(), self.weights)

    def test_invalid_entry_does_not_write_sentinel_or_escape_model_directory(self):
        self.toml.write_text('[ai]\n[[ai.firstboot_models]]\nname="../escaped"\nsource="https://example.invalid/weight"\n')
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        self.assertFalse((self.t / "escaped").exists())
        self.assertFalse(self.log.exists())

    def test_required_fleet_failure_keeps_retry_eligible_and_clears_stale_readiness(self):
        with self.toml.open("a") as stream:
            stream.write('[llamacpp]\nbake_models="missing.gguf=example/missing:missing.gguf"\n')
        self.models.mkdir()
        (self.models / ".ready").touch()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.models / ".ready").exists())
        self.assertFalse((self.state / ".models-firstboot-done").exists())
        service = configparser.ConfigParser()
        service.read(ROOT / "usr/lib/systemd/system/mios-models-firstboot.service")
        self.assertFalse(service.getboolean("Service", "RemainAfterExit", fallback=False))
        timer = configparser.ConfigParser()
        timer.read(ROOT / "usr/lib/systemd/system/mios-models-firstboot.timer")
        self.assertEqual(timer["Timer"]["Unit"], "mios-models-firstboot.service")
        self.assertIn("OnUnitInactiveSec", timer["Timer"])

    def test_finalizer_applies_system_and_user_presets_offline(self):
        script = (ROOT / "automation/88-finalize.sh").read_text()
        commands = [shlex.split(line) for line in script.splitlines()
                    if line.startswith("systemctl ") and "preset-all" in line]
        self.assertEqual(len(commands), 2)
        image = self.t / "image"
        paths = [
            "usr/lib/systemd/system/mios-models-firstboot.service",
            "usr/lib/systemd/system/mios-models-firstboot.timer",
            "usr/lib/systemd/system-preset/90-mios.preset",
            "usr/lib/systemd/user/mios-launcher.service",
            "usr/lib/systemd/user-preset/90-mios.preset",
        ]
        for path in paths:
            dest = image / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((ROOT / path).read_bytes())
        for command in commands:
            # Run the exact finalizer commands against a disposable image root.
            result = subprocess.run([command[0], "--root", str(image), *command[1:]],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((image / "etc/systemd/system/timers.target.wants/mios-models-firstboot.timer").is_symlink())
        self.assertTrue((image / "etc/systemd/user/default.target.wants/mios-launcher.service").is_symlink())
        # Plant the original defect: system-only presets cannot enable a broker.
        broker = image / "etc/systemd/user/default.target.wants/mios-launcher.service"
        broker.unlink()
        system_only = next(command for command in commands if "--global" not in command)
        result = subprocess.run([system_only[0], "--root", str(image), *system_only[1:]],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(broker.exists())

    def test_heavy_weights_are_opt_in_and_use_a_repository_from_ssot(self):
        script = (ROOT / "usr/libexec/mios/mios-ai-firstboot").read_text()
        selection = script[script.index('VLLM_MODEL="${MIOS_VLLM_BAKE_MODEL:-}"'):script.index('VLLM_DIR=')]
        getter = self.t / "toml-get"
        getter.write_text("""#!/bin/sh
case "$1.$2" in
ai.heavy_engine) printf vllm ;;
ai.vllm.enable) printf '%s' "$MIOS_TEST_HEAVY_ENABLED" ;;
ai.sglang.bake_model) printf synthetic/weights ;;
*) exit 1 ;;
esac
""")
        getter.chmod(0o755)
        for enabled, explicit, expected in [
                ("false", "", ""), ("true", "", "synthetic/weights"),
                ("false", "operator/selected", "operator/selected")]:
            result = subprocess.run(["bash", "-c", selection + '\nprintf "%s" "$VLLM_MODEL"'],
                env={**os.environ, "MIOS_TOML_GET": str(getter),
                     "MIOS_VLLM_BAKE_MODEL": explicit, "MIOS_TEST_HEAVY_ENABLED": enabled},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)

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
