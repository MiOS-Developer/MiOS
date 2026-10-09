#!/usr/bin/env python3
# AI-hint: Two-sided tests for the cloud/container overlay: bootstrap.sh renders [deployment.cloud.overlay] as a host drop-in, the layered resolver merges it, and the first-boot model pullers and the runtime flatpak installer then do nothing; without it they pull and install as before.
# AI-related: .devcontainer/cloud-shell/bootstrap.sh, usr/share/mios/mios.toml, usr/libexec/mios/mios-ai-firstboot, usr/libexec/mios/mios-models-firstboot, usr/libexec/mios/mios-bound-images-firstboot, usr/libexec/mios-flatpak-install
# AI-functions: TestOverlayRender, TestFirstbootPulls, TestFlatpakInstall, TestCloudScripts
"""Each consumer runs from a copy whose absolute state paths point into a temp
dir, with network and package tools replaced by stubs that log their calls. The
SSOT values come from the real resolver (usr/libexec/mios/mios-toml-get) reading
the checkout's vendor mios.toml plus a host drop-in dir, so the chain under test
is the one a cloud deployment runs: render -> drop-in -> merge -> gate."""

import os
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
        os.makedirs(self.dropins)
        open(self.log, "w").close()
        for name in ("flatpak", "systemctl", "loginctl", "logger", "sudo", "dnf", "git"):
            self.stub(name)
        # A download lands its -o file; nothing is in a container store yet.
        self.stub("curl", 'while [ $# -gt 1 ]; do [ "$1" = -o ] && echo x > "$2"; shift; done\n')
        self.stub("podman", 'case "$*" in *"image exists"*) exit 1 ;; esac\n')
        self.stub("systemd-detect-virt", 'echo none\n')
        self.stub("id", 'exit 1\n')
        # The real resolver, confined to the checkout's vendor SSOT and this test's host drop-ins.
        self.toml_get = os.path.join(self.tmp, "mios-toml-get")
        _write(self.toml_get, f'#!/bin/sh\nexec python3 {TOML_GET} "$@"\n', 0o755)

    def stub(self, name, body=""):
        _write(os.path.join(self.bin, name), STUB + body, 0o755)

    def env(self, **extra):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], STUB_LOG=self.log,
                 MIOS_TOML_GET=self.toml_get, MIOS_VENDOR_TOML=extra.pop("vendor", SSOT),
                 MIOS_VENDOR_TOML_D=os.path.join(self.tmp, "none.d"),
                 MIOS_HOST_TOML=os.path.join(self.tmp, "etc/mios/mios.toml"), MIOS_HOST_TOML_D=self.dropins,
                 MIOS_USER_TOML=os.path.join(self.tmp, "user/mios.toml"),
                 MIOS_USER_TOML_D=os.path.join(self.tmp, "user/mios.d"))
        e.pop("MIOS_TOML_ROOT", None)
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
        dst = os.path.join(self.tmp, "x", os.path.basename(rel))
        _write(dst, text, 0o755)
        return dst

    def get(self, *key):
        r = subprocess.run([self.toml_get, *key], capture_output=True, text=True, env=self.env())
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
        venv = os.path.join(self.tmp, "venv")
        _write(os.path.join(venv, "bin/hermes"), "#!/bin/sh\n", 0o755)
        _write(os.path.join(venv, "bin/python3"), STUB.replace("$(basename \"$0\")", "venv-python3"), 0o755)
        fb = os.path.join(self.tmp, "firstboot.list")
        _write(fb, "docker.io/example/inference:latest\n")
        script = self.copy("usr/libexec/mios/mios-ai-firstboot", [
            ("/etc/mios/install.env", os.path.join(self.tmp, "none.env")),
            ("/usr/lib/mios/agents/.venv", venv),
            ("/usr/share/mios/llamacpp/models", os.path.join(self.tmp, "models")),
            ("/usr/share/mios/vllm/model", os.path.join(self.tmp, "vllm")),
            ("/usr/lib/mios/bake/plan.d/firstboot.list", fb),
            ("/usr/libexec/mios/seed-db-config.py", os.path.join(self.tmp, "none.py")),
            ("/var/lib/mios", os.path.join(self.tmp, "var")),
        ])
        env = self.env(MIOS_LLAMACPP_BAKE_MODELS="t.gguf=example/repo:t.gguf",
                       MIOS_VLLM_BAKE_MODEL="example/model", MIOS_PG_WAIT_RETRIES="0")
        return subprocess.run(["bash", script], capture_output=True, text=True, env=env, timeout=120)

    def test_without_overlay_ai_firstboot_pulls(self):
        r = self.ai_firstboot()
        calls = self.calls()
        self.assertIn("huggingface.co/example/repo", calls, r.stdout + r.stderr)
        self.assertIn("venv-python3 - example/model", calls)
        self.assertIn("pull docker.io/example/inference:latest", calls)

    def test_with_overlay_ai_firstboot_pulls_nothing(self):
        self.apply_overlay()
        r = self.ai_firstboot()
        calls = self.calls()
        self.assertIn("[ai].firstboot_pulls is false", r.stdout)
        for pulled in ("huggingface.co", "venv-python3", "pull docker.io"):
            self.assertNotIn(pulled, calls)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "var/.ai-firstboot-done")),
                         "a skipped run must not retire the unit")

    def python_firstboot(self, rel):
        vendor = os.path.join(self.tmp, "vendor.toml")
        with open(SSOT, encoding="utf-8") as fh:
            text = fh.read()
        _write(vendor, text + '\n[[ai.firstboot_models]]\nname = "m.gguf"\nsource = "https://example.invalid/m.gguf"\n'
               '\n[[ai.firstboot_bound_images]]\nimage = "docker.io/example/bound:latest"\n')
        script = self.copy(rel, [("/var/lib/mios", os.path.join(self.tmp, "var"))])
        return subprocess.run([sys.executable, script], capture_output=True, text=True,
                              env=self.env(vendor=vendor, MIOS_TOML=vendor), timeout=60)

    def test_models_and_bound_images_both_ways(self):
        for rel, pulled in (("usr/libexec/mios/mios-models-firstboot", "curl -fL"),
                            ("usr/libexec/mios/mios-bound-images-firstboot", "podman pull docker.io/example/bound")):
            with self.subTest(rel):
                open(self.log, "w").close()
                for f in os.listdir(self.dropins):
                    os.unlink(os.path.join(self.dropins, f))
                shutil.rmtree(os.path.join(self.tmp, "var"), True)
                r = self.python_firstboot(rel)
                self.assertIn(pulled, self.calls(), r.stdout + r.stderr)
                open(self.log, "w").close()
                shutil.rmtree(os.path.join(self.tmp, "var"), True)
                self.apply_overlay()
                r = self.python_firstboot(rel)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertIn("[ai].firstboot_pulls is false", r.stdout)
                self.assertNotIn(pulled, self.calls())


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
                   MIOS_CLOUD_RUNTIME="podman", MIOS_CLOUD_STATE=os.path.join(tmp, "state"))
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
