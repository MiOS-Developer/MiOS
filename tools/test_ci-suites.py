#!/usr/bin/env python3
# AI-hint: Sibling test for tools/ci-suites.py; proves the registry reader fails on the shapes it exists to catch.
# AI-related: tools/ci-suites.py, usr/share/mios/mios.toml
"""Each case is a mutation the checker must reject.

A checker that passes on a deliberately broken registry is the defect this
whole registry exists to prevent, so every assertion here is a red, not a green.
"""
import contextlib
import copy
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

def workflow_script(name):
    workflow = Path(_ROOT, ".github/workflows/mios-ci.yml").read_text()
    block = workflow.split(f"      - name: {name}\n", 1)[1].split("        run: |\n", 1)[1]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line[10:])
    if not any(line.strip() for line in lines):
        raise AssertionError(f"Workflow step has no executable body: {name}")
    return "\n".join(lines)

def _load():
    spec = importlib.util.spec_from_file_location(
        "ci_suites", os.path.join(_HERE, "ci-suites.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

MOD = _load()

class TestRegistryReader(unittest.TestCase):
    def _ci(self, **over):
        base = {
            "max_exempt_suites": 1,
            "runners": ["tests/run-suites.sh"],
            "tiers": {"lint": ["automation/lint-json.sh"]},
            "globs": {},
            "exempt": {"tests/bake-smoke.sh": "takes an image reference"},
            "python": {"packages": ["pyflakes"]},
        }
        base.update(over)
        return base

    def test_a_ceiling_below_the_count_fails(self):
        ci = self._ci(exempt={"a": "r", "b": "r"}, max_exempt_suites=1)
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(0, MOD.cmd_check(d, ci))

    def test_an_absent_ceiling_fails(self):
        ci = self._ci()
        del ci["max_exempt_suites"]
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(0, MOD.cmd_check(d, ci))

    def test_an_exemption_without_a_reason_fails(self):
        ci = self._ci(exempt={"tests/x.sh": "   "})
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(0, MOD.cmd_check(d, ci))

    def test_a_skip_must_name_an_existing_glob_member(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "t").mkdir()
            Path(d, "t", "test_live.py").write_text("")
            for skip, stale in ((["test_live.py"], False), (["test_gone.py"], True)):
                ci = self._ci(globs={"g": {"dir": "t", "glob": "test_*.py", "tier": "lint",
                                           "skip": skip, "skip_reason": "needs a database"}})
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    MOD.cmd_check(d, ci)
                self.assertEqual(stale, "stale skip" in out.getvalue(), out.getvalue())

    def test_a_suite_in_two_tiers_fails(self):
        ci = self._ci(tiers={"lint": ["automation/lint-json.sh"],
                             "gate": ["automation/lint-json.sh"]})
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(0, MOD.cmd_check(d, ci))

    def test_a_tool_skip_needs_a_registered_suite_a_reason_and_room_under_the_ceiling(self):
        ok = {"automation/lint-json.sh": "mktool: no package"}
        for over in ({"tool_skips": {"tests/unregistered.sh": "mktool: no package"}, "max_tool_skips": 1},
                     {"tool_skips": {"automation/lint-json.sh": "  "}, "max_tool_skips": 1},
                     {"tool_skips": ok}):
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertNotEqual(0, MOD.cmd_check(d, self._ci(**over)), over)
            self.assertIn("tool_skip", out.getvalue().replace("tool-skip", "tool_skip"), over)

    def test_an_unknown_tier_is_not_silently_empty(self):
        self.assertEqual(2, MOD.cmd_list(_ROOT, self._ci(), "no-such-tier"))

    def test_the_shipped_registry_passes(self):
        ci = MOD._load(_ROOT)
        self.assertEqual(0, MOD.cmd_check(_ROOT, ci))

    def test_pip_arguments_carry_the_requirements_file(self):
        ci = MOD._load(_ROOT)
        reqs = (ci.get("python") or {}).get("requirements") or []
        self.assertTrue(reqs, "[ci.python].requirements is what stopped the "
                              "hand-written package list drifting from the code")
        for r in reqs:
            self.assertTrue(os.path.isfile(os.path.join(_ROOT, r)), r)

    @unittest.skipIf(os.name == "nt", "the shim is a POSIX shell script")
    def test_a_refusing_git_is_not_a_fully_registered_tree(self):
        """The corpus used to come back empty and the unregistered-suite
        direction retired itself, reporting the same success as a clean run."""
        shim = tempfile.mkdtemp(prefix="gitshim-")
        self.addCleanup(shutil.rmtree, shim, True)
        exe = os.path.join(shim, "git")
        with open(exe, "w") as fh:
            fh.write('#!/bin/sh\necho "fatal: detected dubious ownership" >&2\n'
                     'exit 128\n')
        os.chmod(exe, 0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = shim + os.pathsep + old
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = MOD.cmd_check(_ROOT, MOD._load(_ROOT))
        finally:
            os.environ["PATH"] = old
        self.assertNotEqual(0, rc)
        self.assertIn("cannot enumerate tracked suites", buf.getvalue())

    def test_every_registered_path_exists(self):
        ci = MOD._load(_ROOT)
        for path, tier in MOD._registered(_ROOT, ci).items():
            self.assertTrue(os.path.isfile(os.path.join(_ROOT, path)),
                            "%s (tier %s)" % (path, tier))

class TestFedoraProvisioning(unittest.TestCase):
    def _args(self, option, fedora=None, packages=None):
        fedora = fedora if fedora is not None else {
            "image": "registry.example/fedora:test", "repos": [],
            "package_sets": ["dev"], "packages": ["extra", "shared"]}
        packages = packages if packages is not None else {
            "dev": {"pkgs": ["dev-tool", "shared"], "requires_sections": ["build"]},
            "build": {"pkgs": ["compiler", "shared"], "enable": True}}
        with mock.patch.object(MOD, "_load_packages", return_value=packages):
            return MOD.fedora_arguments(_ROOT, {"fedora": fedora}, option)

    def test_package_closure_is_dependency_first_and_deduplicated(self):
        self.assertEqual(["compiler", "shared", "dev-tool", "extra"],
                         self._args("--dnf-packages"))

    def test_all_exporter_entrypoints_work_with_the_shipped_ssot(self):
        for option in ("--dnf-repos", "--dnf-packages", "--fedora-image"):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(0, MOD.main([option]), option)
            self.assertTrue(out.getvalue().strip(), option)
        pkgs = MOD.fedora_arguments(_ROOT, MOD._load(_ROOT), "--dnf-packages")
        self.assertEqual(len(pkgs), len(set(pkgs)))
        closure = MOD._load_packages(_ROOT)
        for section in ("devcontainer", "self-build", "build-toolchain"):
            self.assertTrue(set(closure[section]["pkgs"]).issubset(pkgs), section)

    def test_missing_disabled_and_cyclic_sections_fail(self):
        bad = [
            {},
            {"dev": {"pkgs": ["x"], "enable": False}},
            {"dev": {"pkgs": ["x"], "requires_sections": ["missing"]}},
            {"dev": {"pkgs": ["x"], "requires_sections": ["other"]},
             "other": {"pkgs": ["y"], "requires_sections": ["dev"]}},
            {"dev": {"pkgs": []}},
        ]
        for packages in bad:
            with self.assertRaises(ValueError):
                self._args("--dnf-packages", packages=packages)

    def test_invalid_tokens_and_table_types_fail(self):
        for token in ("", "two packages", "line\nbreak", "--nogpgcheck", 42):
            with self.assertRaises(ValueError):
                self._args("--dnf-packages", packages={"dev": {"pkgs": [token]}})
        for fedora in ({}, {"fedora": "wrong type"}):
            with self.assertRaises(ValueError):
                MOD.fedora_arguments(_ROOT, fedora, "--dnf-repos")

    def test_empty_repo_list_is_allowed_but_empty_packages_fail(self):
        self.assertEqual([], self._args("--dnf-repos"))
        with self.assertRaises(ValueError):
            self._args("--dnf-packages", fedora={"repos": [], "package_sets": [], "packages": []})

    def test_invalid_export_has_no_partial_stdout(self):
        for option in ("--dnf-repos", "--dnf-packages", "--fedora-image"):
            with mock.patch.object(MOD, "_load", return_value={"fedora": {}}), \
                    contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertNotEqual(0, MOD.main([option]))
            self.assertEqual("", out.getvalue())
            self.assertIn(option, err.getvalue())

    def test_fedora_image_drift_is_rejected(self):
        ci = MOD._load(_ROOT)
        ci["fedora"]["image"] = "registry.example/fedora:wrong"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertNotEqual(0, MOD.cmd_check(_ROOT, ci))
        self.assertIn("drift-gate container differs", out.getvalue())
        # The dev container is the MiOS image, not the CI harness: [ci.fedora]
        # no longer names its base, so a harness change says nothing about it.
        self.assertNotIn("devcontainer", out.getvalue())

    @unittest.skipIf(os.name == "nt", "POSIX provisioning shell control")
    def test_empty_repos_skip_repo_install_and_packages_still_install(self):
        script = workflow_script("Provision the analysis toolchain")
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "dnf-arguments")
            py = Path(d, "python3")
            py.write_text("#!/bin/sh\n"
                          'case "$*" in\n'
                          '*--dnf-repos) exit 0;;\n'
                          '*--dnf-packages) echo compiler;;\n'
                          '*--python-packages) echo pyflakes;;\n'
                          '*"-m pip install"*) exit 0;;\n'
                          '*) exit 2;;\nesac\n')
            dnf = Path(d, "dnf")
            dnf.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$PROVISION_MARKER"\n')
            py.chmod(0o755)
            dnf.chmod(0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"],
                       PROVISION_MARKER=marker)
            result = subprocess.run(["bash", "-c", script], env=env,
                                    capture_output=True, text=True, cwd=_ROOT)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["install -y --setopt=install_weak_deps=False compiler"],
                             Path(marker).read_text().splitlines())

    @unittest.skipIf(os.name == "nt", "POSIX provisioning shell control")
    def test_failed_export_stops_before_dnf(self):
        script = workflow_script("Provision the analysis toolchain")
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "dnf-was-called")
            for name, body in (("python3", 'echo "exporter refused" >&2; exit 2'),
                               ("dnf", 'touch "$PROVISION_MARKER"; exit 0')):
                p = Path(d, name)
                p.write_text("#!/bin/sh\n" + body + "\n")
                p.chmod(0o755)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"],
                       PROVISION_MARKER=marker)
            result = subprocess.run(["bash", "-c", script], env=env,
                                    capture_output=True, text=True, cwd=_ROOT)
            self.assertEqual(2, result.returncode)
            self.assertIn("exporter refused", result.stderr)
            self.assertFalse(os.path.exists(marker))

class TestDevcontainerIsTheImage(unittest.TestCase):
    """.devcontainer/Containerfile is [image].ref plus wiring; each mutant must be named."""

    REF = "registry.example/mios:latest"
    GOOD = ("# comment: dnf install in a comment is fine\n"
            "ARG MIOS_IMAGE_REF=registry.example/mios:latest\n"
            "FROM ${MIOS_IMAGE_REF}\n"
            "RUN set -eu; \\\n    systemd-tmpfiles --create --prefix=/var/home; \\\n"
            "    install -d -m 0755 /workspaces\n"
            "ENV MIOS_DEVCONTAINER=1\n")

    def v(self, text):
        return MOD.devcontainer_violations(text, self.REF)

    def test_wiring_only_is_clean(self):
        self.assertEqual([], self.v(self.GOOD))

    def test_repo_containerfile_is_clean(self):
        self.assertEqual([], MOD.devcontainer_check(_ROOT))

    def test_hardcoded_from_is_named(self):
        for frm in ("FROM registry.example/mios:latest", "FROM ghcr.io/mios-dev/machine-os:6.1",
                    "FROM $MIOS_IMAGE_REF"):
            with self.subTest(frm):
                out = self.v(self.GOOD.replace("FROM ${MIOS_IMAGE_REF}", frm))
                self.assertTrue(any("is not FROM ${MIOS_IMAGE_REF}" in e for e in out), out)

    def test_arg_default_must_be_the_ssot_ref(self):
        out = self.v(self.GOOD.replace("MIOS_IMAGE_REF=registry.example/mios:latest",
                                       "MIOS_IMAGE_REF=registry.example/mios:stale"))
        self.assertTrue(any("differs from [image].ref" in e for e in out), out)
        out = self.v(self.GOOD.replace("ARG MIOS_IMAGE_REF=registry.example/mios:latest\n", ""))
        self.assertTrue(any("ARG MIOS_IMAGE_REF default None" in e for e in out), out)

    def test_second_stage_is_named(self):
        out = self.v("FROM registry.example/builder AS b\nRUN true\n" + self.GOOD)
        self.assertTrue(any("2 FROM lines" in e for e in out), out)

    def test_each_install_is_named(self):
        for run in ("dnf install -y git", "dnf5 -y install ripgrep", "rpm -Uvh x.rpm",
                    "python3 -m pip install fastapi", "pip3 install x", "npm install -g x",
                    "rustup-init -y", "cargo install just", "curl -fsSL https://x -o x",
                    "git clone https://x", "flatpak install -y x",
                    "bash automation/55-native-build.sh",
                    "/usr/lib/mios/mcp/.venv/bin/python3 /usr/libexec/mios/mios-mcp-server --agent-cli --install"):
            with self.subTest(run):
                out = self.v(self.GOOD.replace("install -d -m 0755 /workspaces", run))
                self.assertTrue(any("RUN installs" in e for e in out), (run, out))

    def test_copy_from_the_context_is_named(self):
        out = self.v(self.GOOD + "COPY usr/ /usr/\n")
        self.assertTrue(any("brings files into the image" in e for e in out), out)

    def test_local_build_must_layer_on_the_os_tag(self):
        src = Path(_ROOT, "usr/share/mios/mios.toml").read_text(encoding="utf-8")
        line = 'build_args = { MIOS_IMAGE_REF = "build.images.os" }'
        self.assertEqual(1, src.count(line))
        with tempfile.TemporaryDirectory() as d:
            for rel in ("usr/share/mios", ".devcontainer"):
                os.makedirs(os.path.join(d, rel))
            shutil.copyfile(os.path.join(_ROOT, MOD.DEV_CONTAINERFILE), os.path.join(d, MOD.DEV_CONTAINERFILE))
            toml = os.path.join(d, "usr/share/mios/mios.toml")
            Path(toml).write_text(src, encoding="utf-8")
            self.assertEqual([], MOD.devcontainer_check(d))
            # A registry ref, or a copy of the os tag_key instead of the os target.
            for wrong in ("image.ref", "image.local_tag"):
                Path(toml).write_text(src.replace(line, 'build_args = { MIOS_IMAGE_REF = "%s" }' % wrong), encoding="utf-8")
                self.assertTrue(any("would not layer on the image it built" in e for e in MOD.devcontainer_check(d)), wrong)

    def test_check_reports_a_planted_devcontainer_install(self):
        """End to end through cmd_check: the gate the CI job runs goes red."""
        with tempfile.TemporaryDirectory() as d:
            for rel in ("usr/share/mios", ".devcontainer"):
                os.makedirs(os.path.join(d, rel))
            shutil.copyfile(os.path.join(_ROOT, "usr/share/mios/mios.toml"), os.path.join(d, "usr/share/mios/mios.toml"))
            cf = Path(_ROOT, MOD.DEV_CONTAINERFILE).read_text(encoding="utf-8")
            Path(d, MOD.DEV_CONTAINERFILE).write_text(cf + "RUN dnf install -y htop\n", encoding="utf-8")
            self.assertTrue(any("RUN installs" in e for e in MOD.devcontainer_check(d)))

class TestSuiteTimeout(unittest.TestCase):
    """[ci].suite_timeout_s: one hung suite must fail by name, not wedge the tier."""

    def test_check_requires_a_positive_integer(self):
        base = TestRegistryReader()._ci()
        for bad in (None, 0, -5, "900", True):
            ci = dict(base)
            if bad is None:
                ci.pop("suite_timeout_s", None)
            else:
                ci["suite_timeout_s"] = bad
            with contextlib.redirect_stdout(io.StringIO()) as out:
                MOD.cmd_check(_ROOT, ci)
            self.assertIn("[ci].suite_timeout_s must be a positive integer", out.getvalue(), repr(bad))
        self.assertEqual(MOD.suite_timeout({"suite_timeout_s": 900}), 900)

    def _tree(self, d: str, runner_text: str) -> str:
        """A scratch repo: the real run-suites.sh logic over a stub registry."""
        os.makedirs(os.path.join(d, "tests"))
        os.makedirs(os.path.join(d, "tools"))
        Path(d, "tests", "run-suites.sh").write_text(runner_text)
        Path(d, "tools", "ci-suites.py").write_text(
            "import sys\n"
            "a = sys.argv[1:]\n"
            "if '--tier' in a: print('bash\\ttests/hang.sh\\nbash\\ttests/ok.sh')\n"
            "elif '--suite-timeout' in a: print(2)\n"
            "sys.exit(0)\n")
        # The orphan holds stdout -- the pipe run-suites.sh captures -- after
        # its parent is gone, which is what wedged the tier before the limit.
        Path(d, "tests", "hang.sh").write_text("sleep 300 &\nsleep 300\n")
        Path(d, "tests", "ok.sh").write_text("echo fine\n")
        return os.path.join(d, "tests", "run-suites.sh")

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_runner_keeps_enforcement_and_catalog_but_scrubs_host_values(self):
        runner = Path(_ROOT, "tests", "run-suites.sh").read_text()
        with tempfile.TemporaryDirectory() as d:
            script = self._tree(d, runner)
            Path(d, "tests", "hang.sh").write_text(
                'set -eu\n'
                'test "$MIOS_DRIFT_REQUIRE_TOOLS" = 1\n'
                'test "$MIOS_RATCHET_BASE" = fixture-base\n'
                'test "$MIOS_NATIVE_BIN_DIR" = "/fixture catalog"\n'
                'test "${MIOS_PROFILE_ROLE-unset}" = unset\n'
                'test "${MIOS_DRIFT_CHECK_SOFT-unset}" = unset\n')
            env = dict(os.environ, MIOS_DRIFT_REQUIRE_TOOLS="1",
                       MIOS_RATCHET_BASE="fixture-base", MIOS_NATIVE_BIN_DIR="/fixture catalog",
                       MIOS_PROFILE_ROLE="host-only", MIOS_DRIFT_CHECK_SOFT="1")
            result = subprocess.run(["bash", script, "unit"], env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("2 passed, 0 failed", result.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_a_hung_suite_fails_by_name_and_the_tier_finishes(self):
        runner = Path(_ROOT, "tests", "run-suites.sh").read_text()
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run(["bash", self._tree(d, runner), "unit"],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("[FAIL] tests/hang.sh (timed out after 2s", res.stdout)
        self.assertIn("[ OK ] tests/ok.sh", res.stdout)
        self.assertIn("1 passed, 1 failed", res.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_negative_without_the_limit_the_tier_wedges(self):
        runner = Path(_ROOT, "tests", "run-suites.sh").read_text()
        planted = runner.replace('timeout --kill-after=10s "${SUITE_TIMEOUT}s" ', "")
        self.assertNotEqual(planted, runner, "plant did not apply")
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.Popen(["setsid", "bash", self._tree(d, planted), "unit"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    proc.wait(timeout=8)
            finally:
                os.killpg(proc.pid, 9)
                proc.wait()


@unittest.skipIf(os.name == "nt", "POSIX native executable lookup")
class TestNativeProjectionTools(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bins = self.root / "native tools"
        self.bins.mkdir()
        self.script = str(Path(_HERE, "sync-generated.sh"))

    def run_shell(self, **overrides):
        env = os.environ.copy()
        env.pop("MIOS_NATIVE_BIN_DIR", None)
        env.pop("MIOS_GEN_BIN", None)
        env.update(MIOS_ROOT=str(self.root), **overrides)
        return subprocess.run(["bash", self.script], env=env,
                              capture_output=True, text=True, timeout=15)

    def tool(self, directory, name):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        return path

    def test_installed_native_entrypoint_receives_root_and_sync_verb(self):
        expected = self.tool(self.bins, "mios-gen")
        expected.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        result = self.run_shell(
                                PATH=str(self.bins) + os.pathsep + os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['sync', '--root', str(self.root)])

    def test_explicit_directory_is_authoritative_and_requires_executable(self):
        expected = self.tool(self.bins, "mios-gen")
        result = self.run_shell(MIOS_NATIVE_BIN_DIR=str(self.bins))
        self.assertEqual(result.returncode, 0, result.stderr)
        expected.chmod(0o644)
        result = self.run_shell(MIOS_NATIVE_BIN_DIR=str(self.bins))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_linux_does_not_select_windows_build_artifact(self):
        self.tool(self.bins, "mios-gen.exe")
        result = self.run_shell(MIOS_NATIVE_BIN_DIR=str(self.bins))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_smoke_build_requires_successful_nonempty_ssot_base(self):
        script = workflow_script("Smoke build")
        reader = self.tool(self.root / "usr/libexec/mios", "mios-toml-get")
        marker = self.root / "invoked"
        sudo = self.tool(self.bins, "sudo")
        sudo.write_text('#!/bin/sh\nprintf "%s\n" "$*" > "$BUILD_MARKER"\n')
        env = dict(os.environ, BUILD_MARKER=str(marker),
                   PATH=str(self.bins) + os.pathsep + os.environ["PATH"])
        for output in ["exit 7", "exit 0", "echo registry.example/base:stable"]:
            reader.write_text("#!/bin/sh\n" + output + "\n")
            result = subprocess.run(["bash", "-c", script], cwd=self.root,
                                    env=env, capture_output=True, text=True, timeout=15)
            if output.startswith("exit"):
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("BASE_IMAGE=registry.example/base:stable", marker.read_text())

    def test_fedora_rustup_init_bootstrap_is_required_before_native_build(self):
        fixture = self.root / "source"
        script = fixture / "automation/55-native-build.sh"
        script.parent.mkdir(parents=True)
        shutil.copyfile(Path(_ROOT, "automation/55-native-build.sh"), script)
        manifest = fixture / "src/mios-rs/Cargo.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("")
        for command in ['dirname', 'sed', 'mkdir', 'cp', 'chmod']:
            (self.bins / command).symlink_to(shutil.which(command))
        driver = self.root / "native-driver"
        driver.write_text('#!/bin/sh\ncommand -v rustup >/dev/null || exit 9\necho native-build >> "$BUILD_MARKER"\n')
        driver.chmod(0o755)
        self.tool(self.bins, "rustc").write_text('#!/bin/sh\necho "host: fixture-host"\n')
        self.tool(self.bins, "cargo").write_text(
            '#!/bin/sh\nprevious=""\nfor value in "$@"; do\n'
            'if [ "$previous" = --target-dir ]; then output="$value"; fi\nprevious="$value"\ndone\n'
            'mkdir -p "$output/fixture-host/release"\n'
            'cp "$NATIVE_DRIVER" "$output/fixture-host/release/miosd"\n')
        self.tool(self.bins, "rustup-init").write_text(
            '#!/bin/sh\n[ "$INIT_FAIL" = 1 ] && exit 7\n'
            'mkdir -p "$CARGO_HOME/bin"\nprintf "#!/bin/sh\\nexit 0\\n" > "$CARGO_HOME/bin/rustup"\n'
            'chmod +x "$CARGO_HOME/bin/rustup"\necho initialized >> "$BUILD_MARKER"\n')
        marker = self.root / "build-order"
        env = dict(os.environ, PATH=str(self.bins), CARGO_HOME=str(self.root / "cargo home"),
                   CARGO_TARGET_DIR=str(self.root / "output with spaces"), NATIVE_DRIVER=str(driver),
                   MIOS_NATIVE_INSTALL_ROOT=str(self.root / "installed"), BUILD_MARKER=str(marker))
        env.pop('MIOS_NATIVE_DEST_DIR', None)
        for failed in (True, False):
            env['INIT_FAIL'] = '1' if failed else '0'
            result = subprocess.run([shutil.which('bash'), str(script)], env=env,
                                    capture_output=True, text=True, timeout=15)
            if failed:
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertFalse(marker.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(marker.read_text().splitlines(), ['initialized','native-build'])

    def test_missing_tool_fails_before_any_projection(self):
        result = self.run_shell(MIOS_NATIVE_BIN_DIR=str(self.bins))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required native tool mios-gen is missing", result.stderr)
        self.assertNotIn("[ports.projection]", result.stdout)
        self.assertNotIn("render-ports.py", result.stderr)

    def test_native_resolver_rejection_cannot_fall_back_to_another_resolver(self):
        native = self.tool(self.bins, 'mios-resolver')
        native.write_text('#!/bin/sh\necho "planted resolver rejection" >&2\nexit 9\n')
        marker = self.root / 'fallback-ran'
        fallback = self.tool(self.bins, 'miosd')
        fallback.write_text('#!/bin/sh\ntouch "$FALLBACK_MARKER"\nexit 0\n')
        env = dict(os.environ, MIOS_ROOT=str(self.root),
                   MIOS_MIGRATION_USE_RUST_RESOLVER_SHELL='true', FALLBACK_MARKER=str(marker),
                   PATH=str(self.bins) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(['bash', '-c', 'source "$1"', 'resolver-test',
                                 str(Path(_ROOT, 'usr/lib/mios/userenv.sh'))],
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('planted resolver rejection', result.stderr)
        self.assertFalse(marker.exists())


class TestCanonicalInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('mios_names_test', Path(_ROOT, 'usr/lib/mios/mios_toml.py'))
        cls.resolver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.resolver)

    def test_old_input_propagates_to_canonical_and_all_synonyms(self):
        data = {'ports': {'agent_pipe': 8700}}
        self.resolver.overlay_inputs(data, {'MIOS_PORT_AGENT_PIPE': '9100'}.get)
        values = self.resolver.emit_exports(data)
        for name in ('MIOS_PORTS_AGENT_PIPE', 'MIOS_PORT_AGENT_PIPE', 'MIOS_AGENT_PIPE_PORT'):
            self.assertEqual(values[name], '9100')

    def test_conflicting_inputs_are_redacted_and_transactional(self):
        original = {'identity': {'username': 'mios'}, 'ports': {'agent_pipe': 8700}}
        data = copy.deepcopy(original)
        env = {'MIOS_PORT_AGENT_PIPE': '9100', 'MIOS_USER': 'secret-one', 'MIOS_DEFAULT_USER': 'secret-two'}
        with self.assertRaises(ValueError) as caught:
            self.resolver.overlay_inputs(data, env.get)
        self.assertIn('MIOS_USER', str(caught.exception))
        self.assertNotIn('secret-', str(caught.exception))
        self.assertEqual(data, original)
        env['MIOS_IDENTITY_USERNAME'] = 'canonical'
        self.resolver.overlay_inputs(data, env.get)
        self.assertEqual(self.resolver.emit_exports(data)['MIOS_IDENTITY_USERNAME'], 'canonical')

    def test_ambiguous_aliases_and_image_tags_are_not_interchangeable(self):
        data = {'identity': {'fullname': 'one'}, 'user': {'name': 'two'},
                'image': {'sidecars': {'k3s': 'registry.example/k3s:v1'}}}
        names = self.resolver.input_aliases(data)
        self.assertNotIn('MIOS_USER_FULLNAME', names['MIOS_IDENTITY_FULLNAME'])
        self.assertTrue(all('MIOS_K3S_VERSION' not in aliases for aliases in names.values()))

    def test_lexical_collisions_fail(self):
        with self.assertRaisesRegex(ValueError, 'MIOS_A_X_Y'):
            self.resolver.input_aliases({'a': {'x_y': 1, 'x': {'y': 2}}})

if __name__ == "__main__":
    unittest.main(verbosity=1)
