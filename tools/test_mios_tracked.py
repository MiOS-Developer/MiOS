#!/usr/bin/env python3
# AI-hint: Fixtures for mios_tracked.py -- proves a dead git and an empty listing both raise instead of reading as a clean, empty tree.
# AI-related: tools/mios_tracked.py
# AI-functions: main
"""Guards the helper that stops an unanswerable git from meaning "nothing"."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import json
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "mt", os.path.join(HERE, "mios_tracked.py"))
mt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mt)

FAILED: list = []
PASSED = 0


def check(name, got, want):
    global PASSED
    if got == want:
        PASSED += 1
    else:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")


def _git(root, *a):
    return subprocess.run(["git", "-C", root, *a],
                          capture_output=True, text=True, check=False)


def test_lists_tracked_paths():
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q")
        open(os.path.join(tmp, "a.txt"), "w").write("x\n")
        _git(tmp, "add", "a.txt")
        check("lists-the-file", mt.tracked(tmp), ["a.txt"])


def test_non_repo_raises():
    # git ls-files exits 128 here; the old code returned [] and read as clean.
    with tempfile.TemporaryDirectory() as tmp:
        raised = False
        try:
            mt.tracked(tmp)
        except mt.GitUnavailable:
            raised = True
        check("dead-git-raises", raised, True)


def test_empty_listing_raises():
    # A repo with nothing tracked: git succeeds, the corpus is still empty.
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q")
        raised = False
        try:
            mt.tracked(tmp)
        except mt.GitUnavailable:
            raised = True
        check("empty-listing-raises", raised, True)


def test_pathspec_is_passed_through():
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q")
        for n in ("keep.py", "skip.txt"):
            open(os.path.join(tmp, n), "w").write("x\n")
        _git(tmp, "add", "keep.py", "skip.txt")
        check("pathspec-filters", mt.tracked(tmp, "*.py"), ["keep.py"])



class RootedPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-rooted-paths-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, rel, text="data"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def link(self, rel, target):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        return path

    def test_fhs_and_checkout_absolute_targets_are_equivalent(self):
        target = self.file("usr/share/containers/systemd/core.container")
        link = "usr/lib/bootc/bound-images.d/core.container"
        for value in ("/usr/share/containers/systemd/core.container", str(target),
                      "../../../share/containers/systemd/core.container"):
            self.assertEqual(target, mt.rooted_target(str(self.root), link, value))

    def test_root_slash_and_nonroot_lexical_parity(self):
        rel = "usr/lib/bootc/bound-images.d/core.container"
        target = "/usr/share/containers/systemd/core.container"
        at_root = mt.rooted_target("/", rel, target)
        in_checkout = mt.rooted_target(str(self.root), rel, target)
        self.assertEqual(at_root.relative_to(Path(os.path.abspath("/"))).as_posix(),
                         in_checkout.relative_to(self.root).as_posix())

    @unittest.skipIf(os.name == "nt", "POSIX filename characters are not Windows separators")
    def test_posix_colon_and_backslash_are_filename_characters(self):
        target = self.file("usr/share/source:part\\literal.txt")
        self.link("alias", "/usr/share/source:part\\literal.txt")
        self.assertEqual(target, mt.rooted_resolve(str(self.root), "alias"))

    def test_root_slash_and_nonroot_filesystem_parity(self):
        self.file("usr/bin/worker")
        rel = "usr/libexec/mios/worker"
        self.link(rel, "/usr/bin/worker")
        nonroot = mt.rooted_resolve(str(self.root), rel)
        lstat, readlink = Path.lstat, os.readlink
        def virtual_lstat(path, *args, **kwargs):
            return lstat(self.root / str(path).lstrip("/"), *args, **kwargs)
        def virtual_readlink(path, *args, **kwargs):
            return readlink(self.root / str(path).lstrip("/"), *args, **kwargs)
        with mock.patch.object(Path, "lstat", virtual_lstat), mock.patch.object(
                os, "readlink", side_effect=virtual_readlink):
            rooted = mt.rooted_resolve("/", rel)
        self.assertEqual(rooted.as_posix(), "/" + nonroot.relative_to(self.root).as_posix())

    def test_host_absolute_file_cannot_supply_missing_checkout_target(self):
        self.link("alias", "/etc/passwd")
        with self.assertRaises(FileNotFoundError):
            mt.rooted_resolve(str(self.root), "alias")

    def test_directory_and_intermediate_fhs_aliases_stay_rooted(self):
        target = self.file("usr/share/mios/generated/item.yaml")
        self.link("usr/share/mios/manifests", "generated")
        self.assertEqual(target.parent, mt.rooted_resolve(str(self.root), "usr/share/mios/manifests"))
        self.link("alias", "/usr/share/mios/manifests/item.yaml")
        self.assertEqual(target, mt.rooted_resolve(str(self.root), "alias"))

    def test_escape_cycle_and_missing_component_before_dotdot_fail(self):
        for target in ("../escape", "alias", "/missing/../usr/bin/worker"):
            path = self.root / "alias"
            path.symlink_to(target)
            with self.subTest(target=target), self.assertRaises((ValueError, FileNotFoundError)):
                mt.rooted_resolve(str(self.root), "alias")
            path.unlink()

    def test_exact_declared_output_only(self):
        self.link("alias", "/usr/bin/mios-worker")
        declared = {"/usr/bin/mios-worker": "mios-worker"}
        self.assertEqual(self.root / "usr/bin/mios-worker",
                         mt.rooted_resolve(str(self.root), "alias", declared))
        with self.assertRaises(FileNotFoundError):
            mt.rooted_resolve(str(self.root), "alias", {"/usr/bin/other": "other"})


class NativeInstallMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mios-native-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = self.root / "usr/share/mios/mios.toml"
        self.policy.parent.mkdir(parents=True)
        self.policy.write_text("""[build.native]
workspaces = ["tools/native"]
windows_only = []
[build.native.categories.daemons]
binaries = ["mios-worker"]
install_dir = "/usr/libexec/mios"
expose_bin = true
compat_dirs = []
""")
        catalog = self.root / "catalog"
        catalog.mkdir()
        executable = catalog / ("miosd.exe" if os.name == "nt" else "miosd")
        executable.write_text("fixture")
        executable.chmod(0o755)
        patch = mock.patch.dict(os.environ, MIOS_NATIVE_BIN_DIR=str(catalog))
        patch.start()
        self.addCleanup(patch.stop)
        self.row = {"workspace":"tools/native", "package":"mios-worker", "binary":"mios-worker",
                    "category":"daemons", "install_dir":"/usr/libexec/mios", "expose_bin":True,
                    "compat_dirs":[], "platform":"linux"}

    def invoke(self, payload, code=0):
        completed = subprocess.CompletedProcess([], code, json.dumps(payload), "planted planner error" if code else "")
        with mock.patch.object(mt.subprocess, "run", return_value=completed) as run:
            result = mt.native_install_paths(str(self.root))
        self.assertEqual(["native-targets", "--root", str(self.root), "--platform", "linux", "--json"],
                         run.call_args.args[0][1:])
        return result

    def test_install_and_exposed_alias_paths_follow_metadata(self):
        self.assertEqual({"/usr/libexec/mios/mios-worker":"mios-worker",
                          "/usr/bin/mios-worker":"mios-worker"}, self.invoke([self.row]))

    def test_named_aliases_require_exact_planner_and_policy_agreement(self):
        self.policy.write_text(self.policy.read_text() + """
[build.native.aliases.mios-observe]
binary = "mios-worker"
command = "build-status"
platform = "linux"
""")
        row = {**self.row, "named_aliases": ["mios-observe"]}
        paths = self.invoke([row])
        self.assertEqual("mios-worker", paths["/usr/bin/mios-observe"])
        self.assertEqual("mios-worker", paths["/usr/libexec/mios/mios-observe"])
        for names in ([], ["foreign"], ["../escape"], "mios-observe", ["mios-observe", "mios-observe"]):
            with self.subTest(names=names), self.assertRaises(mt.NativeCatalogError):
                self.invoke([{**row, "named_aliases": names}])
        valid = self.policy.read_text()
        for old, new in (('binary = "mios-worker"', 'binary = "missing"'),
                         ('platform = "linux"', 'platform = "windows"'),
                         ('command = "build-status"', 'command = ""'),
                         ('aliases.mios-observe', 'aliases.mios-worker')):
            self.policy.write_text(valid.replace(old, new))
            with self.subTest(new=new), self.assertRaises(mt.NativeCatalogError):
                self.invoke([row])

    def test_unknown_wrong_path_platform_and_partial_metadata_fail(self):
        for patch in ({"binary":"unknown"}, {"install_dir":"/tmp"}, {"category":{}},
                      {"install_dir":[]}, {"platform":"windows"}, {"workspace":"elsewhere"},
                      {"compat_dirs":["/usr/bin"]}, {"expose_bin":False}, {"package":""}):
            with self.subTest(patch=patch), self.assertRaises(mt.NativeCatalogError):
                self.invoke([{**self.row, **patch}])
        for payload in ([], {}, [self.row, self.row]):
            with self.subTest(payload=payload), self.assertRaises(mt.NativeCatalogError):
                self.invoke(payload)

    def test_failed_planner_or_missing_explicit_tool_cannot_fall_back(self):
        with self.assertRaises(mt.NativeCatalogError):
            self.invoke([self.row], code=1)
        for path in (self.root / "catalog").iterdir():
            path.unlink()
        with self.assertRaises(mt.NativeCatalogError):
            mt.native_install_paths(str(self.root))

    def test_malformed_policy_or_json_fails_closed(self):
        completed = subprocess.CompletedProcess([], 0, "not JSON", "")
        with mock.patch.object(mt.subprocess, "run", return_value=completed):
            with self.assertRaises(mt.NativeCatalogError):
                mt.native_install_paths(str(self.root))
        self.policy.write_text("[build.native")
        with self.assertRaises(mt.NativeCatalogError):
            mt.native_install_paths(str(self.root))
        self.policy.write_text('[build.native]\nworkspaces=[]\ncategories="bad"')
        with self.assertRaises(mt.NativeCatalogError):
            mt.native_install_paths(str(self.root))

def main() -> int:
    test_lists_tracked_paths()
    test_non_repo_raises()
    test_empty_listing_raises()
    test_pathspec_is_passed_through()
    print(f"[test_mios_tracked] {PASSED} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAIL {f}")
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (RootedPathTests, NativeInstallMetadataTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 1 if FAILED or not result.wasSuccessful() else 0


if __name__ == "__main__":
    sys.exit(main())
