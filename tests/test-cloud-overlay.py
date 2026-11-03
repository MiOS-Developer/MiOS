#!/usr/bin/env python3
# AI-hint: Two-sided tests for the cloud/container overlay: bootstrap.sh renders [deployment.cloud.overlay] as a host drop-in, the resolver merges it, and the first-boot model pullers and flatpak installer then do nothing; without it they run as before.
# AI-related: .devcontainer/cloud-shell/bootstrap.sh, usr/share/mios/mios.toml, usr/libexec/mios/mios-ai-firstboot, usr/libexec/mios/mios-models-firstboot, usr/libexec/mios/mios-bound-images-firstboot, usr/libexec/mios-flatpak-install
# AI-functions: TestOverlayRender, TestFirstbootPulls, TestFlatpakInstall, TestCloudScripts
"""Each consumer runs from a copy whose absolute state paths point into a temp
dir, with network and package tools replaced by stubs that log their calls. The
SSOT values come from the real resolver (usr/libexec/mios/mios-toml-get) reading
the checkout's vendor mios.toml plus a host drop-in dir, so the chain under test
is the one a cloud deployment runs: render -> drop-in -> merge -> gate."""

import os
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP = os.path.join(ROOT, ".devcontainer/cloud-shell/bootstrap.sh")
TOML_GET = os.path.join(ROOT, "usr/libexec/mios/mios-toml-get")
SSOT = os.path.join(ROOT, "usr/share/mios/mios.toml")

STUB = '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$STUB_LOG"\n'

MODEL_REPO = "example/repo"
MODEL_FILE = "t.gguf"
MODEL_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FAKE_CURL = r"""#!/usr/bin/env python3
import os, shutil, shlex, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write("curl " + shlex.join(args) + "\n")
url = next(arg for arg in args if arg.startswith(("https://", "http://")))
source = os.path.join(os.environ["FIXTURE_HTTP"], url.split("://", 1)[1].replace("?", "__Q__"))
if not os.path.isfile(source):
    sys.exit(22)
if "-o" in args:
    shutil.copyfile(source, args[args.index("-o") + 1])
else:
    sys.stdout.write(open(source).read())
"""



def _write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.chmod(path, mode)


def render_overlay():
    r = subprocess.run(["bash", BOOTSTRAP, "overlay"], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"bootstrap.sh overlay failed: {r.stderr}")
    return r.stdout


@unittest.skipIf(os.name == "nt", "the consumers are POSIX shell and Linux paths")
class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cloud-overlay-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "calls.log")
        self.dropins = os.path.join(self.tmp, "etc/mios/mios.d")
        self.fixture_root = os.path.join(self.tmp, "root")
        self.state = os.path.join(self.tmp, "var")
        self.models = os.path.join(self.tmp, "models")
        os.makedirs(self.state)
        resolver = os.path.join(self.fixture_root, "usr/lib/mios/mios_toml.py")
        os.makedirs(os.path.dirname(resolver), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, "usr/lib/mios/mios_toml.py"), resolver)
        self.http = os.path.join(self.tmp, "http")
        self.payload = b"GGUF" + bytes(range(64))
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.serve_model()

        os.makedirs(self.dropins)
        open(self.log, "w").close()
        for name in ("flatpak", "systemctl", "loginctl", "logger", "sudo", "dnf", "git"):
            self.stub(name)
        # Replace only the transport. The real provisioner hashes these bytes.
        _write(os.path.join(self.bin, "curl"), FAKE_CURL, 0o755)
        self.stub("podman", 'case "$*" in *"image exists"*) exit 1 ;; esac\n')
        self.stub("systemd-detect-virt", 'echo none\n')
        self.stub("id", 'exit 1\n')
        # The real resolver, confined to the checkout's vendor SSOT and this test's host drop-ins.
        self.toml_get = os.path.join(self.tmp, "mios-toml-get")
        _write(self.toml_get, f'#!/bin/sh\nexec python3 {TOML_GET} "$@"\n', 0o755)

    def serve_model(self, payload=None, published_digest=None):
        payload = self.payload if payload is None else payload
        digest = self.digest if published_digest is None else published_digest
        api = os.path.join(self.http, f"huggingface.co/api/models/{MODEL_REPO}/revision/main__Q__blobs=true")
        _write(api, json.dumps({"sha": MODEL_COMMIT, "siblings": [
            {"rfilename": MODEL_FILE, "lfs": {"sha256": digest, "size": len(payload)}}]}))
        blob = os.path.join(self.http, f"huggingface.co/{MODEL_REPO}/resolve/{MODEL_COMMIT}/{MODEL_FILE}")
        os.makedirs(os.path.dirname(blob), exist_ok=True)
        with open(blob, "wb") as f:
            f.write(payload)

    def configure_models(self):
        _write(os.path.join(self.dropins, "10-fixture-models.toml"),
               "[llamacpp]\nmodels_dir = " + json.dumps(self.models) + "\n"
               + f'bake_models = "{MODEL_FILE}={MODEL_REPO}:{MODEL_FILE}"\n'
               + '[ai]\nfirstboot_models = [\n'
               + f'  {{name = "{MODEL_FILE}", source = "https://huggingface.co/{MODEL_REPO}/resolve/main/{MODEL_FILE}"}}\n'
               + ']\n')

    def assert_verified_model(self):
        path = os.path.join(self.models, MODEL_FILE)
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), self.digest)
        with open(os.path.join(self.models, "models.tsv")) as f:
            row = f.read().splitlines()[1].split("\t")
        self.assertEqual(row[:4], [MODEL_FILE,
            f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_COMMIT}/{MODEL_FILE}",
            MODEL_COMMIT, self.digest])
        self.assertTrue(os.path.isfile(os.path.join(self.models, ".ready")))
        self.assertTrue(os.path.isfile(os.path.join(self.state, ".models-firstboot-done")))

    def stub(self, name, body=""):
        _write(os.path.join(self.bin, name), STUB + body, 0o755)

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
        e.update(PATH=self.bin + os.pathsep + os.environ["PATH"], STUB_LOG=self.log,
                 FIXTURE_HTTP=self.http, MIOS_HF_ENDPOINT="https://huggingface.co",
                 MIOS_TOML_GET=self.toml_get, MIOS_VENDOR_TOML=extra.pop("vendor", SSOT),
                 MIOS_TOML_ROOT=self.fixture_root, MIOS_VENDOR_TOML_D=os.path.join(self.tmp, "none.d"),
                 MIOS_HOST_TOML=os.path.join(self.tmp, "etc/mios/mios.toml"), MIOS_HOST_TOML_D=self.dropins,
                 MIOS_USER_TOML=os.path.join(self.tmp, "user/mios.toml"),
                 MIOS_USER_TOML_D=os.path.join(self.tmp, "user/mios.d"))
        e.update(extra)
        return e

    def apply_overlay(self):
        _write(os.path.join(self.dropins, "50-cloud.toml"), render_overlay())

    def calls(self):
        with open(self.log, encoding="utf-8") as fh:
            return fh.read()

    def copy(self, rel, rewrites):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            text = fh.read()
        for old, new in rewrites:
            self.assertIn(old, text, f"{rel} no longer holds {old}; update this fixture")
            text = text.replace(old, new)
        dst = os.path.join(self.fixture_root, rel)
        _write(dst, text, 0o755)
        return dst

    def get(self, *key):
        r = subprocess.run([self.toml_get, *key], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()


class TestOverlayRender(Harness):
    def test_overlay_is_the_ssot_table(self):
        with open(SSOT, "rb") as fh:
            want = tomllib.load(fh)["deployment"]["cloud"]["overlay"]
        self.assertEqual(want, tomllib.loads(render_overlay()))
        for table in ("ai", "desktop", "blade"):
            self.assertIn(table, want, f"[deployment.cloud.overlay.{table}] is gone")

    def test_resolver_merges_the_dropin_both_ways(self):
        self.assertEqual("true", self.get("ai", "firstboot_pulls", "x"))
        self.assertEqual("true", self.get("desktop", "runtime_installs", "x"))
        self.assertEqual("hybrid", self.get("blade", "type", "x"))
        self.apply_overlay()
        self.assertEqual("false", self.get("ai", "firstboot_pulls", "x"))
        self.assertEqual("false", self.get("desktop", "runtime_installs", "x"))
        self.assertEqual("headless", self.get("blade", "type", "x"))


class TestFirstbootPulls(Harness):
    def ai_firstboot(self):
        self.configure_models()
        fetcher = self.copy("usr/libexec/mios/mios-models-firstboot",
                            [("/var/lib/mios", self.state)])
        venv = os.path.join(self.tmp, "venv")
        _write(os.path.join(venv, "bin/hermes"), "#!/bin/sh\n", 0o755)
        _write(os.path.join(venv, "bin/python3"), STUB.replace("$(basename \"$0\")", "venv-python3"), 0o755)
        fb = os.path.join(self.tmp, "firstboot.list")
        _write(fb, "docker.io/example/inference:latest\nlocalhost/mios-fixture:latest\n")
        script = self.copy("usr/libexec/mios/mios-ai-firstboot", [
            ("/etc/mios/install.env", os.path.join(self.tmp, "none.env")),
            ("/usr/libexec/mios/mios-models-firstboot", fetcher),
            ("/usr/lib/mios/agents/.venv", venv),
            ("/usr/share/mios/llamacpp/models", os.path.join(self.tmp, "models")),
            ("/usr/share/mios/vllm/model", os.path.join(self.tmp, "vllm")),
            ("/usr/lib/mios/bake/plan.d/firstboot.list", fb),
            ("/usr/libexec/mios/seed-db-config.py", os.path.join(self.tmp, "none.py")),
            ("/var/lib/mios", os.path.join(self.tmp, "var")),
        ])
        env = self.env(MIOS_VLLM_BAKE_MODEL="example/model", MIOS_PG_WAIT_RETRIES="0")
        return subprocess.run(["bash", script], capture_output=True, text=True, env=env, timeout=120)

    def test_without_overlay_ai_firstboot_pulls(self):
        r = self.ai_firstboot()
        calls = self.calls()
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn(f"/api/models/{MODEL_REPO}/revision/main", calls, r.stdout + r.stderr)
        self.assertIn(f"/resolve/{MODEL_COMMIT}/{MODEL_FILE}", calls)
        self.assert_verified_model()
        self.assertIn("venv-python3 - example/model", calls)
        self.assertIn("pull docker.io/example/inference:latest", calls)
        self.assertNotIn("pull localhost/", calls)
        self.assertNotIn("No module named", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.state, ".ai-firstboot-done")),
                         "the stubbed unavailable database must not retire firstboot")

    def test_with_overlay_ai_firstboot_pulls_nothing(self):
        self.apply_overlay()
        r = self.ai_firstboot()
        calls = self.calls()
        self.assertIn("[ai].firstboot_pulls is false", r.stdout)
        self.assertEqual(0, r.returncode, r.stderr)
        for pulled in ("curl ", "venv-python3", "pull docker.io"):
            self.assertNotIn(pulled, calls)
        for name in (".ai-firstboot-done", ".models-firstboot-done"):
            self.assertFalse(os.path.exists(os.path.join(self.state, name)),
                             "a skipped run must not retire the unit")
        self.assertFalse(os.path.exists(os.path.join(self.models, ".ready")))

    def python_firstboot(self, rel):
        self.configure_models()
        vendor = os.path.join(self.tmp, "vendor.toml")
        with open(SSOT, encoding="utf-8") as fh:
            text = fh.read()
        _write(vendor, text + f'\n[[ai.firstboot_models]]\nname = "{MODEL_FILE}"\n'
               + f'source = "https://huggingface.co/{MODEL_REPO}/resolve/main/{MODEL_FILE}"\n'
               + '\n[[ai.firstboot_bound_images]]\nimage = "docker.io/example/bound:latest"\n')
        script = self.copy(rel, [("/var/lib/mios", self.state)])
        return subprocess.run([sys.executable, script], capture_output=True, text=True,
                              env=self.env(vendor=vendor, MIOS_TOML=vendor), timeout=60)

    def test_models_and_bound_images_both_ways(self):
        for rel, pulled, marker in (
            ("usr/libexec/mios/mios-models-firstboot", f"/resolve/{MODEL_COMMIT}/{MODEL_FILE}", ".models-firstboot-done"),
            ("usr/libexec/mios/mios-bound-images-firstboot", "podman pull docker.io/example/bound", ".bound-images-firstboot-done"),
        ):
            with self.subTest(rel=rel):
                open(self.log, "w").close()
                for f in os.listdir(self.dropins):
                    os.unlink(os.path.join(self.dropins, f))
                shutil.rmtree(self.state, True)
                r = self.python_firstboot(rel)
                self.assertEqual(0, r.returncode, r.stdout + r.stderr)
                self.assertIn(pulled, self.calls(), r.stdout + r.stderr)
                self.assertTrue(os.path.isfile(os.path.join(self.state, marker)), r.stdout)
                if rel.endswith("mios-models-firstboot"):
                    self.assert_verified_model()
                open(self.log, "w").close()
                shutil.rmtree(self.state, True)
                self.apply_overlay()
                r = self.python_firstboot(rel)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertIn("[ai].firstboot_pulls is false", r.stdout)
                self.assertNotIn(pulled, self.calls())
                self.assertNotIn("curl ", self.calls())
                self.assertNotIn("podman pull", self.calls())
                self.assertFalse(os.path.exists(os.path.join(self.state, marker)))

    def test_corrupted_model_is_not_published_or_retired(self):
        self.serve_model(payload=b"corrupted transport payload")
        r = self.python_firstboot("usr/libexec/mios/mios-models-firstboot")
        self.assertEqual(0, r.returncode, r.stderr)  # boot degrades open; readiness does not
        self.assertIn("sha256 MISMATCH", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.models, MODEL_FILE)))
        self.assertFalse(os.path.exists(os.path.join(self.models, MODEL_FILE + ".part")))
        self.assertFalse(os.path.exists(os.path.join(self.models, ".ready")))
        self.assertFalse(os.path.exists(os.path.join(self.state, ".models-firstboot-done")))

    def test_unpublished_model_digest_refuses_blob_download(self):
        self.serve_model(published_digest="")
        r = self.python_firstboot("usr/libexec/mios/mios-models-firstboot")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("upstream digest unavailable", r.stdout)
        self.assertNotIn(f"/resolve/{MODEL_COMMIT}/", self.calls())
        self.assertFalse(os.path.exists(os.path.join(self.models, ".ready")))
        self.assertFalse(os.path.exists(os.path.join(self.state, ".models-firstboot-done")))

    def test_ai_orchestrator_preserves_failed_model_readiness(self):
        self.serve_model(payload=b"corrupted transport payload")
        r = self.ai_firstboot()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("sha256 MISMATCH", r.stdout)
        self.assertIn("ggufs=missing", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.models, ".ready")))
        self.assertFalse(os.path.exists(os.path.join(self.state, ".ai-firstboot-done")))


class TestFlatpakInstall(Harness):
    def run_installer(self):
        userenv = os.path.join(self.tmp, "userenv.sh")
        _write(userenv, 'MIOS_DESKTOP_FLATPAKS="org.example.App"\n')
        self.stub("flatpak", 'case "$1" in remote-list) echo flathub ;; esac\n')
        script = self.copy("usr/libexec/mios-flatpak-install", [
            ("/usr/lib/mios/userenv.sh", userenv),
            ("/usr/share/mios/flatpak-list", os.path.join(self.tmp, "none.list")),
            ("/var/lib/mios", os.path.join(self.tmp, "var")),
        ])
        return subprocess.run(["bash", script], capture_output=True, text=True, env=self.env(), timeout=60)

    def test_without_overlay_flatpaks_install(self):
        r = self.run_installer()
        self.assertIn("flatpak install --system --noninteractive --or-update -y flathub org.example.App",
                      self.calls(), r.stdout + r.stderr)

    def test_with_overlay_nothing_installs(self):
        self.apply_overlay()
        r = self.run_installer()
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("[desktop].runtime_installs is false", r.stdout)
        self.assertIsNone(re.search(r"(?m)^flatpak ", self.calls()), self.calls())


@unittest.skipIf(os.name == "nt", "POSIX shell")
class TestCloudScripts(unittest.TestCase):
    SCRIPTS = [".devcontainer/cloud-shell/" + n for n in ("bootstrap.sh", "claude-code-cloud.sh", "codex-cloud.sh")]

    def test_scripts_parse(self):
        for rel in self.SCRIPTS:
            with self.subTest(rel):
                r = subprocess.run(["bash", "-n", os.path.join(ROOT, rel)], capture_output=True, text=True)
                self.assertEqual(0, r.returncode, r.stderr)

    def test_no_literal_image_ref_or_size(self):
        with open(SSOT, "rb") as fh:
            ref = tomllib.load(fh)["image"]["ref"]
        for rel in self.SCRIPTS:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(rel):
                self.assertNotIn(ref, text, "the image ref comes from [image].ref")
                self.assertNotIn("machine-os", text, "every cloud path runs the MiOS image, never the CI harness")

    def test_preflight_measures_the_store_against_the_ssot(self):
        """Free space in the runtime's store below [deployment.cloud].min_free_gb fails, naming both
        numbers; a requirement the disk meets passes. Planted values make it two-sided on any disk."""
        tmp = tempfile.mkdtemp(prefix="cloud-preflight-")
        self.addCleanup(shutil.rmtree, tmp, True)
        store = os.path.join(tmp, "store")
        os.makedirs(store)
        _write(os.path.join(tmp, "bin/podman"),
               f'#!/bin/sh\ncase "$1" in info) echo "{store}" ;; image) exit 1 ;; esac\n', 0o755)
        fx = os.path.join(tmp, "MiOS")
        for rel in (".devcontainer/cloud-shell/bootstrap.sh", "usr/libexec/mios/mios-toml-get",
                    "usr/lib/mios/mios_toml.py"):
            os.makedirs(os.path.dirname(os.path.join(fx, rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(fx, rel))
        with open(SSOT, encoding="utf-8") as fh:
            text = fh.read()
        line = "min_free_gb   = 80"
        self.assertEqual(1, text.count(line))
        env = dict(os.environ, PATH=os.path.join(tmp, "bin") + os.pathsep + os.environ["PATH"],
                   MIOS_DEPLOYMENT_CLOUD_RUNTIME="podman", MIOS_DEPLOYMENT_CLOUD_STATE_DIRECTORY=os.path.join(tmp, "state"))
        env.pop("MIOS_TOML_ROOT", None)
        for want, rc in ((999999, 1), (0, 0)):
            with self.subTest(want=want):
                _write(os.path.join(fx, "usr/share/mios/mios.toml"), text.replace(line, f"min_free_gb   = {want}"))
                r = subprocess.run(["bash", os.path.join(fx, ".devcontainer/cloud-shell/bootstrap.sh"), "preflight"],
                                   capture_output=True, text=True, env=env)
                self.assertEqual(rc, r.returncode, r.stdout + r.stderr)
                if rc:
                    self.assertIn(f"{want} GB needed", r.stderr)
                    self.assertIn(store, r.stderr)


if __name__ == "__main__":
    unittest.main()
