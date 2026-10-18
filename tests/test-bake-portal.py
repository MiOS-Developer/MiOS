#!/usr/bin/env python3
# AI-hint: Hermetic positive and negative controls for MP-3 (67-bake-portal.sh): the positive fixture is a REAL stage of the current src/mios-portal (mios-portal-tree stage) packaged like a builder run; phase 67 installs it root-owned/world-readable from the artifact's own install-overlay and proves the result equals the artifact. Negatives: no stage.json, no Pillow, a drifted tree, a tampered or missing overlay, a wrong ICO hash, weak provenance (metrics), a tampered tarball, an absent tree, unsafe DEST, and a tampered install caught by verify-install.
# AI-related: automation/67-bake-portal.sh, usr/libexec/mios/mios-portal-tree, usr/libexec/mios/mios-portal-build, etc/fapolicyd/fapolicyd.rules, usr/share/mios/mios.toml
"""Every case runs in a throwaway copy of the repo files the phase needs; the repository is only read."""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("MIOS_ROOT") or os.path.normpath(os.path.join(HERE, ".."))
TREE = "src/mios-portal"
NEEDED = [
    "automation/67-bake-portal.sh",
    "usr/lib/mios/log.sh",
    "usr/lib/mios/mios_toml.py",
    "usr/share/mios/mios.toml",
    "usr/libexec/mios/mios-dotfiles-render",
    "usr/libexec/mios/mios-portal-tree",
    "usr/share/mios/portal/icon-512.png",
]
VERSION = "1.0"   # a stand-in resolved version: only the build copy ever carries one


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Fixture:
    """A repo copy, a real stage of its product tree, and a builder-shaped run directory."""

    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="mp3-test-")
        self.root = os.path.join(self.path, "repo")
        for rel in NEEDED:
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(REPO, rel), dst)
        tdir = "usr/share/mios/theme/templates"
        shutil.copytree(os.path.join(REPO, tdir), os.path.join(self.root, tdir))
        shutil.copytree(os.path.join(REPO, TREE), os.path.join(self.root, TREE))
        self.tool = os.path.join(self.root, "usr/libexec/mios/mios-portal-tree")
        self.run_dir = os.path.join(self.path, "artifacts/mios-portal/20260101T000000Z")
        self.dest = os.path.join(self.path, "sysroot/usr/lib/mios/portal")
        os.makedirs(self.run_dir)
        self.stage = self.stage_into(os.path.join(self.path, "stage"))
        shutil.copy2(os.path.join(self.stage, "mios-portal-stage.json"), os.path.join(self.run_dir, "stage.json"))
        shutil.copytree(os.path.join(self.stage, "install"), os.path.join(self.run_dir, "install-overlay"))
        self._package()

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("MIOS_TOML", "MIOS_VENDOR_TOML", "MIOS_VENDOR_TOML_D", "DEST", "CTX")
               and not k.startswith("MIOS_DESKTOP_PORTAL_")}
        env.update(MIOS_THEME_ROOT=self.root, MIOS_TOML_ROOT=self.root,
                   MIOS_VENDOR_TOML=os.path.join(self.root, "usr/share/mios/mios.toml"),
                   MIOS_HOST_TOML="/nonexistent.toml", MIOS_USER_TOML="/nonexistent.toml",
                   MIOS_DESKTOP_PORTAL_GECKO_NAME="MiOS Portal", MIOS_DESKTOP_PORTAL_GECKO_BINARY="mios-portal",
                   MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR=self.dest,
                   MIOS_DESKTOP_PORTAL_BUILDER_ARTIFACT_ROOT=os.path.dirname(self.run_dir),
                   MIOS_PORTAL_ARTIFACT_DIR=self.run_dir)
        env.update(extra)
        return env

    def stage_into(self, out):
        versions = os.path.join(self.path, "versions.env")
        with open(versions, "w", encoding="utf-8") as fh:
            fh.write(f"firefox_version={VERSION}\n")
        r = subprocess.run([sys.executable, "-I", self.tool, "stage", "--out", out, "--versions", versions],
                           capture_output=True, text=True, env=self.env(), timeout=180)
        if r.returncode:
            raise RuntimeError(f"stage failed: {r.stdout}\n{r.stderr}")
        return out

    def _package(self, extra=b""):
        """firefox/{firefox,firefox-bin,defaults/pref/channel-prefs.js} plus an 0600 icon, as mach package
        under a 077 umask left them; manifest and metrics as the builder writes them."""
        tar_path = os.path.join(self.run_dir, f"firefox-{VERSION}.en-US.linux-x86_64.tar.xz")
        with tarfile.open(tar_path, "w:xz") as tf:
            def add(name, data, mode):
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.uid, ti.gid = len(data), mode, 4242, 4242
                tf.addfile(ti, io.BytesIO(data))
            for d in ("firefox", "firefox/defaults", "firefox/defaults/pref", "firefox/browser",
                      "firefox/browser/chrome", "firefox/browser/chrome/icons", "firefox/browser/chrome/icons/default"):
                ti = tarfile.TarInfo(d)
                ti.type, ti.mode = tarfile.DIRTYPE, 0o700
                tf.addfile(ti)
            add("firefox/firefox", b"#!/bin/sh\necho 'mock portal'\n" + extra, 0o755)
            add("firefox/firefox-bin", b"\x7fELF-mock", 0o755)
            add("firefox/defaults/pref/channel-prefs.js", b'pref("app.update.channel", "mios");\n', 0o644)
            add("firefox/browser/chrome/icons/default/default16.png", b"\x89PNG-mock", 0o600)
        manifest = {"package": {"file": os.path.basename(tar_path), "sha256": sha256(tar_path),
                                "executable": "firefox/firefox"},
                    "resolved": {"firefox": VERSION}}
        with open(os.path.join(self.run_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        self.metrics({"exit": 0, "product": f"staged:/{TREE}", "smoke": "0"})
        return tar_path

    def metrics(self, data):
        p = os.path.join(self.run_dir, "metrics.json")
        if data is None:
            os.remove(p)
            return
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def phase(self, **extra):
        return subprocess.run(["bash", os.path.join(self.root, "automation/67-bake-portal.sh")],
                              env=self.env(**extra), capture_output=True, text=True, timeout=300)

    def verify_install(self):
        return subprocess.run([sys.executable, "-I", self.tool, "verify-install", "--run", self.run_dir,
                               "--dest", self.dest, "--binary", "mios-portal"],
                              env=self.env(), capture_output=True, text=True, timeout=120)

    def close(self):
        shutil.rmtree(self.path, ignore_errors=True)


class TestBakePortal(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def ok(self, r):
        self.assertEqual(r.returncode, 0, f"stdout: {r.stdout}\nstderr: {r.stderr}")

    def fails(self, r, needle):
        self.assertNotEqual(r.returncode, 0, f"expected failure ({needle}), got rc 0\n{r.stdout}\n{r.stderr}")
        self.assertIn(needle, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.f.dest, "mios-portal")), "a failed phase left an install")

    # ------------------------------------------------------------- positive controls
    def test_positive_installs_the_artifact_and_proves_it(self):
        r = self.f.phase()
        self.ok(r)
        self.assertIn("verify-stage:", r.stdout + r.stderr)
        self.assertIn("verify-install:", r.stdout + r.stderr)
        d = self.f.dest
        self.assertEqual(os.readlink(os.path.join(d, "mios-portal")), "firefox")
        self.assertTrue(os.access(os.path.join(d, "mios-portal"), os.X_OK))
        # The overlay is the artifact's (== staged install/), laid at the same relative paths.
        for rel in ("browser/defaults/preferences/mios-portal.js", "distribution/policies.json", "mios-autoconfig.cfg"):
            self.assertEqual(sha256(os.path.join(d, rel)), sha256(os.path.join(self.f.run_dir, "install-overlay", rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(d, "defaults/pref/mios-portal.js")),
                         "prefs must be app-level (browser/defaults/preferences), not GRE defaults/pref")
        # The 0600 icon and 0700 dirs of the package come out world-readable, nothing group/other-writable.
        for dp, ds, fs in os.walk(d):
            for x in ds + fs:
                st = os.lstat(os.path.join(dp, x))
                if stat.S_ISLNK(st.st_mode):
                    continue
                self.assertTrue(st.st_mode & 0o004, f"{x} not world-readable: {oct(st.st_mode)}")
                self.assertFalse(st.st_mode & 0o022, f"{x} group/other-writable: {oct(st.st_mode)}")
        self.ok(self.f.verify_install())
        self.assertEqual([n for n in os.listdir(os.path.dirname(d)) if n.startswith(".portal.")], [],
                         "staging/rollback directories were left behind")

    def test_positive_reinstall_replaces_the_previous_tree(self):
        self.ok(self.f.phase())
        with open(os.path.join(self.f.dest, "stray-hand-copied-file"), "w") as fh:
            fh.write("x")
        self.ok(self.f.phase())
        self.assertFalse(os.path.exists(os.path.join(self.f.dest, "stray-hand-copied-file")))

    def test_positive_icon_derivation_is_deterministic(self):
        again = self.f.stage_into(os.path.join(self.f.path, "stage2"))
        bdir = "configs/branding/mios"
        names = sorted(os.listdir(os.path.join(self.f.stage, bdir)))
        self.assertIn("firefox.ico", names)
        self.assertIn("firefox64.ico", names)
        for n in names:
            p1, p2 = os.path.join(self.f.stage, bdir, n), os.path.join(again, bdir, n)
            if os.path.isfile(p1):
                self.assertEqual(sha256(p1), sha256(p2), n)

    # ------------------------------------------------------------- negative controls: stage
    def test_negative_no_stage_json_fails(self):
        os.remove(os.path.join(self.f.run_dir, "stage.json"))
        self.fails(self.f.phase(), "stage.json is missing")

    def test_negative_no_pillow_fails(self):
        shim = os.path.join(self.f.path, "shim")
        os.makedirs(shim)
        with open(os.path.join(shim, "python3"), "w") as fh:
            fh.write(f"#!/bin/sh\nexec {sys.executable} -S \"$@\"\n")   # -S: no site-packages, no PIL
        os.chmod(os.path.join(shim, "python3"), 0o755)
        r = self.f.phase(PATH=shim + os.pathsep + os.environ.get("PATH", ""))
        self.fails(r, "python3-pillow is required")

    def test_negative_tree_drift_after_the_build_fails(self):
        p = os.path.join(self.f.root, TREE, "install/mios-autoconfig.cfg")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("// edited on the host after the build\n")
        r = self.f.phase()
        self.fails(r, "Stage hash check failed")
        self.assertIn("install/mios-autoconfig.cfg", r.stderr)

    def test_negative_file_added_to_the_tree_fails(self):
        with open(os.path.join(self.f.root, TREE, "install/distribution/extra.json"), "w") as fh:
            fh.write("{}")
        self.fails(self.f.phase(), "absent from the artifact's stage")

    def test_negative_wrong_ico_hash_fails(self):
        p = os.path.join(self.f.run_dir, "stage.json")
        with open(p, encoding="utf-8") as fh:
            st = json.load(fh)
        st["files"]["configs/branding/mios/firefox.ico"] = "0" * 64
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        r = self.f.phase()
        self.fails(r, "Stage hash check failed")
        self.assertIn("configs/branding/mios/firefox.ico", r.stderr)

    def test_negative_overlay_tampered_fails(self):
        with open(os.path.join(self.f.run_dir, "install-overlay/mios-autoconfig.cfg"), "a") as fh:
            fh.write("// tampered\n")
        self.fails(self.f.phase(), "is not the staged")

    def test_negative_overlay_missing_fails(self):
        shutil.rmtree(os.path.join(self.f.run_dir, "install-overlay"))
        self.fails(self.f.phase(), "install-overlay/ is missing")

    def test_negative_tree_absent_fails(self):
        shutil.rmtree(os.path.join(self.f.root, TREE))
        self.fails(self.f.phase(), "Stage hash check failed")

    # ------------------------------------------------------------- negative controls: provenance
    def test_negative_metrics_missing_fails(self):
        self.f.metrics(None)
        self.fails(self.f.phase(), "metrics.json is missing")

    def test_negative_override_product_fails(self):
        self.f.metrics({"exit": 0, "product": "override:/tmp/x", "smoke": "0"})
        self.fails(self.f.phase(), "only a staged")

    def test_negative_failed_or_skipped_smoke_fails(self):
        for smoke in ("1", "skipped"):
            self.f.metrics({"exit": 0, "product": f"staged:/{TREE}", "smoke": smoke})
            self.fails(self.f.phase(), "never passed its smoke test")

    def test_negative_failed_build_fails(self):
        self.f.metrics({"exit": 2, "product": f"staged:/{TREE}", "smoke": "0"})
        self.fails(self.f.phase(), "not 0")

    def test_negative_tampered_tarball_fails(self):
        with open(os.path.join(self.f.run_dir, f"firefox-{VERSION}.en-US.linux-x86_64.tar.xz"), "ab") as fh:
            fh.write(b"\x00corrupt")
        self.fails(self.f.phase(), "sha256 mismatch")

    def test_negative_missing_artifact_fails_fatally(self):
        empty = os.path.join(self.f.path, "empty")
        os.makedirs(empty)
        self.fails(self.f.phase(MIOS_PORTAL_ARTIFACT_DIR=empty, MIOS_DESKTOP_PORTAL_BUILDER_ARTIFACT_ROOT=empty), "fatal=true")

    # ------------------------------------------------------------- negative controls: install proof and guards
    def test_negative_verify_install_catches_a_tampered_install(self):
        self.ok(self.f.phase())
        self.ok(self.f.verify_install())
        with open(os.path.join(self.f.dest, "mios-autoconfig.cfg"), "a") as fh:
            fh.write("// hand-copied\n")
        r = self.f.verify_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("mios-autoconfig.cfg", r.stderr)
        self.ok(self.f.phase())
        with open(os.path.join(self.f.dest, "extra.js"), "w") as fh:
            fh.write("x")
        r = self.f.verify_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("extra.js: installed but not part of the artifact", r.stderr)

    def test_negative_unsafe_dest_refuses(self):
        self.assertIn("protected system directory", self.f.phase(MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR="/usr").stderr)
        self.assertIn("is not [desktop.portal.gecko].install_dir", self.f.phase(DEST=os.path.join(self.f.path, "elsewhere")).stderr)

    def test_negative_empty_dest_fails_fatally(self):
        for extra in ({"DEST": ""}, {"MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR": ""}):
            r = self.f.phase(**extra)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("DEST is empty", r.stderr)

    def test_fapolicyd_rules_retargeted(self):
        with open(os.path.join(REPO, "etc/fapolicyd/fapolicyd.rules"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("dir=/usr/lib/mios/portal/", content)
        self.assertNotIn("dir=/usr/lib/mios/webshell/", content)


if __name__ == "__main__":
    unittest.main()
