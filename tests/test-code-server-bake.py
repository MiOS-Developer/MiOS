# AI-hint: Hermetic two-sided tests for the code-server workbench bake (mios-vscode-custom-css patch/verify), the OS-pipeline phases that bake it and the toolchain, the dev container that is the MiOS image, and the mios-agents image and builders.
# AI-related: /usr/libexec/mios/mios-vscode-custom-css, /.devcontainer/Containerfile, /automation/59-tools.sh, /automation/55-native-build.sh, /tools/ci-suites.py, /.devcontainer/setup-devcontainer.sh, /.devcontainer/boot-mios-systems.sh, /usr/share/mios/agents/Containerfile, /usr/libexec/mios/mios-agents-firstboot.sh, /src/mios-rs/miosd/src/main.rs
# AI-functions: TestCodeServerBake, TestDevcontainerLifecycle, TestDevImageWiring, TestOsImageProvides, TestAgentsContainerfile, TestBuilders

import hashlib
import importlib.machinery
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOL = os.path.join(ROOT, "usr/libexec/mios/mios-vscode-custom-css")
CSS = os.path.join(ROOT, "usr/share/mios/themes/code-server-terminal.css")
CF = os.path.join(ROOT, "usr/share/mios/agents/Containerfile")
FIRSTBOOT = os.path.join(ROOT, "usr/libexec/mios/mios-agents-firstboot.sh")
MAIN_RS = os.path.join(ROOT, "src/mios-rs/miosd/src/main.rs")
TOML = os.path.join(ROOT, "usr/share/mios/mios.toml")
WB = "/usr/lib/code-server/lib/vscode/out/vs/code/browser/workbench/workbench.html"
IMG_TOOL = "/usr/libexec/mios/mios-vscode-custom-css"
IMG_CSS = "/usr/share/mios/themes/code-server-terminal.css"
ARGS = {"MIOS_CODE_SERVER_VERSION": ("image.sidecars", "code_server"),
        "CODE_SERVER_SCROLLBAR_PX": ("theme.edge", "code_server_scrollbar_px"),
        "CODE_SERVER_PERIMETER_PX": ("theme.edge", "code_server_perimeter_px")}

# Measured once each in code-server 4.139.1 lib/vscode/out/vs/code/browser/workbench/workbench.js.
JS_SCROLLBAR = 'get scrollbarWidth(){return this._configurationService.getValue("workbench.experimental.modernUI")===!0?10:14}'
JS_PERIMETER = ("var L0e=4,fGn=0,vGn=4,Wre=0;function Hre(s){return s.isModernUICompact()?fGn:L0e}"
                "function P0e(s){return s.isModernUICompact()?vGn:L0e}")
HTML = ('<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8" />\n<link rel="stylesheet" href="{{WORKBENCH_WEB_BASE_URL}}'
        '/out/vs/code/browser/workbench/workbench.css">\n</head>\n<body aria-label="">\n</body>\n</html>\n')
START = "<!-- !! VSCODE-CUSTOM-CSS-START !! -->"


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _bash_directory(path):
    """Let Bash name the directory: Windows may dispatch Bash through WSL."""
    return subprocess.run(["bash", "-c", "pwd"], cwd=path, check=True,
                          capture_output=True, text=True).stdout.strip()


def _load_tool():
    loader = importlib.machinery.SourceFileLoader("mios_vscode_custom_css", TOOL)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestDevcontainerLifecycle(unittest.TestCase):
    """Run the merged core hook against fake build/install commands."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="core-hook-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = _bash_directory(self.tmp)
        self.log = os.path.join(self.tmp, "calls.log")
        for directory in ("bin", "src/mios-rs", "tools/native", "runtime"):
            os.makedirs(os.path.join(self.tmp, directory), exist_ok=True)
        with open(os.path.join(self.tmp, "src/mios-rs/Cargo.toml"), "w") as fh:
            fh.write("# fixture\n")
        for name in ("cargo", "install", "sudo", "miosd"):
            self.shim("bin/" + name,
                      'printf "%s %s\\n" "' + name + '" "$*" >> "$CORE_LOG"\n'
                      'if [[ "' + name + '" == cargo && "${FAIL_BUILD:-}" == 1 ]]; then exit 71; fi\n')
        self.shim("runtime/python", 'printf "runtime import\\n" >> "$CORE_LOG"\n')

    def shim(self, relative, body):
        path = os.path.join(self.tmp, relative)
        with open(path, "w", newline="\n") as fh:
            fh.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)

    def run_core(self, fail=False):
        if not shutil.which("bash"):
            self.skipTest("bash is required for the lifecycle hook")
        source = _read(os.path.join(ROOT, ".devcontainer/boot-mios-systems.sh"))
        source = source.split('case "${1:-start}" in', 1)[0]
        source = source.replace("local root=/workspaces/MiOS", f'local root="{self.root}"')
        source = source.replace("local agent_pipe_python=/usr/lib/mios/agents/.venv/bin/python",
                                f'local agent_pipe_python="{self.root}/runtime/python"')
        prefix = (f'export PATH="{self.root}/bin:$PATH"\n'
                  f'export CORE_LOG="{self.root}/calls.log"\n'
                  f'export FAIL_BUILD={1 if fail else 0}\n')
        # Send bytes: Windows text-mode pipes turn LF into CRLF, which Bash
        # treats as part of option names (including pipefail).
        result = subprocess.run(["bash", "-s"], input=(prefix + source + "\nmios_core\n").encode(),
                                capture_output=True,
                                env=dict(os.environ))
        result.stdout = result.stdout.decode()
        result.stderr = result.stderr.decode()
        calls = _read(self.log).splitlines() if os.path.isfile(self.log) else []
        return result, calls

    def test_builds_both_workspaces_before_installing(self):
        result, calls = self.run_core()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("runtime import", calls[0])
        builds = [line for line in calls if line.startswith("cargo ")]
        self.assertEqual(["cargo build --release",
                          "cargo build --release --workspace --exclude mios-wallpaperd"], builds)
        self.assertLess(calls.index(builds[-1]), next(i for i, line in enumerate(calls) if line.startswith("sudo ")))
        self.assertEqual("miosd --help", calls[-1])

    def test_build_failure_stops_before_install(self):
        result, calls = self.run_core(fail=True)
        self.assertEqual(71, result.returncode, result.stderr)
        self.assertFalse(any(line.startswith("sudo ") for line in calls), calls)
        self.assertEqual(1, sum(line.startswith("cargo ") for line in calls))

    def test_missing_runtime_fails_before_build(self):
        os.unlink(os.path.join(self.tmp, "runtime/python"))
        result, calls = self.run_core()
        self.assertEqual(1, result.returncode)
        self.assertIn("Missing agent-pipe runtime", result.stderr)
        self.assertEqual([], calls)


class TestCodeServerBake(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.html = os.path.join(self.tmp, "workbench.html")
        self.js = os.path.join(self.tmp, "workbench.js")
        self.css = os.path.join(self.tmp, "code-server-terminal.css")
        with open(self.html, "w") as f:
            f.write(HTML)
        with open(self.js, "w") as f:
            f.write("var a=1;" + JS_PERIMETER + ";class X{" + JS_SCROLLBAR + "}")
        shutil.copyfile(CSS, self.css)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_tool(self, *args, env=None):
        return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env)

    def bake(self, cmd="patch", sb="0", pm="0"):
        return self.run_tool(cmd, "--target", self.html, "--css", self.css, "--scrollbar-px", sb, "--perimeter-px", pm)

    def test_patch_then_verify(self):
        r = self.bake()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        js = _read(self.js)
        self.assertIn("===!0?0:14}", js)
        self.assertIn("vGn=0,Wre=0", js)
        html = _read(self.html)
        self.assertEqual(html.count(START), 1)
        self.assertIn(_read(CSS), html)
        self.assertIn("--modern-ui-floating-card-outer-margin:0px", html)
        self.assertNotIn("file://", html)
        r = self.bake("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_value_is_the_flag_not_a_literal(self):
        self.assertEqual(self.bake(sb="3", pm="2").returncode, 0)
        js = _read(self.js)
        self.assertIn("===!0?3:14}", js)
        self.assertIn("vGn=2,Wre=0", js)
        self.assertEqual(self.bake("verify", sb="3", pm="2").returncode, 0)

    def test_rebake_is_idempotent_and_replaces_block(self):
        self.assertEqual(self.bake().returncode, 0)
        before = (_sha(self.html), _sha(self.js))
        self.assertEqual(self.bake().returncode, 0)
        self.assertEqual((_sha(self.html), _sha(self.js)), before)
        with open(self.css, "a") as f:
            f.write("\n/* changed */\n")
        self.assertEqual(self.bake().returncode, 0)
        html = _read(self.html)
        self.assertEqual(html.count(START), 1)
        self.assertIn("/* changed */", html)
        self.assertEqual(self.bake("verify").returncode, 0)

    def test_verify_catches_edited_block(self):
        self.assertEqual(self.bake().returncode, 0)
        html = _read(self.html)
        planted = re.sub(r"padding-left:[^;]*;", "padding-left: 20px;", html, count=1)
        self.assertNotEqual(planted, html)
        with open(self.html, "w") as f:
            f.write(planted)
        r = self.bake("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("code-server workbench.html: injected block differs", r.stdout + r.stderr)

    def test_verify_catches_wrong_values(self):
        self.assertEqual(self.bake().returncode, 0)
        r = self.bake("verify", sb="5", pm="7")
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertIn("code-server workbench.js: scrollbarWidth ModernUI=0 want 5", out)
        self.assertIn("code-server workbench.js: COMPACT_FLOATING_PANEL_OUTER_MARGIN=0 want 7", out)

    def test_verify_unbaked_tree_fails(self):
        r = self.bake("verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("scrollbarWidth ModernUI=10 want 0", r.stdout + r.stderr)

    def _assert_anchor_failure(self, needle):
        before = (_sha(self.html), _sha(self.js))
        r = self.bake()
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 2, out)
        self.assertIn("anchor not found", out)
        self.assertIn(needle, out)
        self.assertEqual((_sha(self.html), _sha(self.js)), before, "a failed bake wrote a file")

    def test_real_anchor_must_be_found(self):
        # The fixture carries the real 4.139.1 anchor; a tool whose anchor drifted must fail, not return 0.
        r = self.bake()
        out = r.stdout + r.stderr
        if r.returncode != 0:
            self.fail(f"patch of the measured 4.139.1 anchor exited {r.returncode}: {out.strip()}")

    def test_moved_scrollbar_anchor_fails_loud(self):
        with open(self.js, "w") as f:
            f.write(JS_PERIMETER + JS_SCROLLBAR.replace("?10:14", "?10:15"))
        self._assert_anchor_failure("get scrollbarWidth")

    def test_duplicate_scrollbar_anchor_fails_loud(self):
        with open(self.js, "a") as f:
            f.write(JS_SCROLLBAR)
        self._assert_anchor_failure("get scrollbarWidth")

    def test_moved_perimeter_anchor_fails_loud(self):
        with open(self.js, "w") as f:
            f.write(JS_PERIMETER.replace("vGn=4", "vGn=6") + JS_SCROLLBAR)
        self._assert_anchor_failure("COMPACT_FLOATING_PANEL_OUTER_MARGIN")

    def test_missing_js_fails_loud(self):
        os.unlink(self.js)
        r = self.bake()
        self.assertEqual(r.returncode, 2)
        self.assertIn("anchor not found", r.stdout + r.stderr)

    def test_head_anchor_must_be_unique(self):
        for body in (HTML.replace("</head>", ""), HTML.replace("</head>", "</head></head>")):
            with open(self.html, "w") as f:
                f.write(body)
            self._assert_anchor_failure(": </head>")

    def test_empty_target_list_exits_2(self):
        mod = _load_tool()
        mod.locate_workbench_html_files = lambda: []
        argv = sys.argv
        sys.argv = ["mios-vscode-custom-css", "patch", "--scrollbar-px", "0", "--perimeter-px", "0"]
        try:
            self.assertEqual(mod.main(), 2)
        finally:
            sys.argv = argv

    def test_defaults_resolve_from_theme_edge(self):
        toml = os.path.join(self.tmp, "mios.toml")
        with open(toml, "w") as f:
            f.write("[theme.edge]\ncode_server_scrollbar_px = 4\ncode_server_perimeter_px = 1\n")
        env = dict(os.environ, MIOS_VENDOR_TOML=toml, MIOS_HOST_TOML=os.path.join(self.tmp, "none-host.toml"),
                   MIOS_USER_TOML=os.path.join(self.tmp, "none-user.toml"),
                   MIOS_VENDOR_TOML_D=os.path.join(self.tmp, "none.d"))
        r = self.run_tool("patch", "--target", self.html, "--css", self.css, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("===!0?4:14}", _read(self.js))
        self.assertIn("vGn=1,", _read(self.js))

    def test_unpatch_restores_upstream(self):
        before = (_sha(self.html), _sha(self.js))
        self.assertEqual(self.bake().returncode, 0)
        r = self.run_tool("unpatch", "--target", self.html)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((_sha(self.html), _sha(self.js)), before)

    def test_retired_fallbacks_are_gone(self):
        src = _read(TOOL)
        for gone in ("ALT_CSS_FILE", "patch_xterm_css", 'href="file://'):
            self.assertNotIn(gone, src)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "usr/share/mios/theme/code-server-terminal.css")))


class TestExtensionDirs(unittest.TestCase):
    """A global extensions dir is used only when it exists; it is never invented under an install root."""

    def setUp(self):
        self.tool = _load_tool()
        self.tmp = tempfile.mkdtemp(prefix="ext-dirs-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.install_root = os.path.join(self.tmp, "usr/lib/code-server")
        os.makedirs(self.install_root)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".local/share/code-server"))
        self.tool.GLOBAL_EXT_DIRS = [os.path.join(self.install_root, "extensions")]
        # The repo's own extension sources, not whatever a host overlay installed.
        self.tool.EXT_SRC_DIR = os.path.join(ROOT, "usr/share/mios/extensions/be5invis.vscode-custom-css")
        self.tool.THEME_SRC_DIR = os.path.join(ROOT, "usr/share/mios/extensions/mios-theme-mobile")

    def test_missing_global_dir_is_not_a_target(self):
        dirs = self.tool.locate_ext_dirs([self.home])
        self.assertNotIn(os.path.join(self.install_root, "extensions"), dirs)
        self.assertIn(os.path.join(self.home, ".local/share/code-server/extensions"), dirs)

    def test_existing_global_dir_is_a_target(self):
        os.makedirs(os.path.join(self.install_root, "extensions"))
        self.assertIn(os.path.join(self.install_root, "extensions"), self.tool.locate_ext_dirs([self.home]))

    def test_user_install_succeeds_beside_an_unwritable_install_root(self):
        os.chmod(self.install_root, 0o555)
        self.addCleanup(os.chmod, self.install_root, 0o755)
        dirs = self.tool.locate_ext_dirs([self.home])
        self.assertTrue(self.tool.install_extension(dirs), dirs)


def _load_ci_suites():
    loader = importlib.machinery.SourceFileLoader("ci_suites", os.path.join(ROOT, "tools/ci-suites.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


DEV_CF = os.path.join(ROOT, ".devcontainer/Containerfile")
TOOLS_PHASE = os.path.join(ROOT, "automation/59-tools.sh")
NATIVE_PHASE = os.path.join(ROOT, "automation/55-native-build.sh")
AGENT_PHASE = os.path.join(ROOT, "automation/72-hermes-agent.sh")
OS_CF = os.path.join(ROOT, "Containerfile")


class TestDevImageWiring(unittest.TestCase):
    """The dev container IS [image].ref: its Containerfile adds wiring only, so every
    component it used to install by hand must come from the OS image pipeline."""

    def setUp(self):
        with open(TOML, "rb") as f:
            self.ref = tomllib.load(f)["image"]["ref"]
        self.ci = _load_ci_suites()

    def test_dev_containerfile_is_the_image(self):
        self.assertEqual(self.ci.devcontainer_violations(_read(DEV_CF), self.ref), [])

    def test_dev_containerfile_mutants_fail(self):
        cf = _read(DEV_CF)
        mutants = {
            "an install": cf + "RUN dnf install -y htop\n",
            "a hand-built code-server": cf + "RUN rpm -Uvh https://example.invalid/code-server-4.0.0-amd64.rpm\n",
            "a toolchain": cf + "RUN rustup-init -y\n",
            "a hardcoded image": cf.replace("FROM ${MIOS_IMAGE}", "FROM " + self.ref),
            "the CI harness": cf.replace("FROM ${MIOS_IMAGE}", "FROM ghcr.io/mios-dev/machine-os:6.1"),
            "a stale default": cf.replace("MIOS_IMAGE=" + self.ref, "MIOS_IMAGE=ghcr.io/mios-dev/mios:0.2.4"),
            "files from the context": cf + "COPY usr/ /usr/\n",
        }
        for name, text in mutants.items():
            with self.subTest(name):
                self.assertNotEqual(text, cf, name)
                self.assertNotEqual(self.ci.devcontainer_violations(text, self.ref), [], name)

    def test_setup_verify_is_fatal_and_install_is_reported(self):
        sh = _read(os.path.join(ROOT, ".devcontainer/setup-devcontainer.sh"))
        verify = [ln for ln in sh.splitlines() if '"$VSCODE_CSS_TOOL" verify' in ln]
        self.assertEqual(len(verify), 1)
        self.assertNotIn("||", verify[0])
        self.assertNotIn("install --all || true", sh)
        self.assertIn("custom-css extension install failed (exit", sh)

    def test_post_start_binds_loopback_from_ports(self):
        sh = _read(os.path.join(ROOT, ".devcontainer/boot-mios-systems.sh"))
        self.assertIn("ports code_server", sh)
        self.assertIn("127.0.0.1", sh)
        self.assertIsNone(re.search(r"\b8900\b|\b8080\b", sh), "a code-server port literal in boot-mios-systems.sh")

    def test_lifecycle_uses_checkout_helpers(self):
        """The helpers live in the workspace checkout now; nothing installs them into /usr/local/bin."""
        for rel in (".devcontainer/boot-mios-systems.sh", ".devcontainer/setup-devcontainer.sh"):
            sh = _read(os.path.join(ROOT, rel))
            self.assertNotIn("/usr/local/bin/mios-root-overlay", sh, rel)
            self.assertIsNone(re.search(r"(?m)^\s*mios-agent-pipe-dev\b", sh), f"{rel} relies on an installed helper")


class TestOsImageProvides(unittest.TestCase):
    """Where the OS image pipeline now provides what the old dev Containerfile installed by hand."""

    def test_code_server_is_baked_by_the_tools_phase(self):
        self.assertEqual(code_server_violations(_read(TOOLS_PHASE)), [])

    def test_code_server_mutants_fail(self):
        sh = _read(TOOLS_PHASE)
        mutants = {
            "no verify": sh.replace('mios-vscode-custom-css" verify "${_cs_bake[@]}"', 'mios-vscode-custom-css" true'),
            "masked patch": sh.replace('patch "${_cs_bake[@]}"', 'patch "${_cs_bake[@]}" || true'),
            "a version literal": sh.replace("download/v${_cs_version}/code-server-${_cs_version}",
                                            "download/v4.139.1/code-server-4.139.1"),
            "no SSOT pin": sh.replace("_toml_get image.sidecars code_server", "echo ghcr.io/coder/code-server:latest"),
        }
        for name, text in mutants.items():
            with self.subTest(name):
                self.assertNotEqual(text, sh, name)
                self.assertNotEqual(code_server_violations(text), [], name)

    def test_tools_phase_runs_in_every_profile(self):
        with open(TOML, "rb") as f:
            data = tomllib.load(f)
        names = {p["name"]: p for p in data["build"]["phases"]["list"]}
        self.assertTrue(names["tools"]["fatal"])
        self.assertIn("tools", data["profiles"]["core"]["phases"])
        self.assertTrue(data["profiles"]["full"]["all"])

    def test_native_toolchain_is_provisioned_by_the_os_build(self):
        self.assertEqual(rust_toolchain_violations(_read(NATIVE_PHASE), _read(OS_CF)), [])

    def test_toolchain_mutants_fail(self):
        sh, cf = _read(NATIVE_PHASE), _read(OS_CF)
        call = "    bash /tmp/build/automation/55-native-build.sh --toolchain; \\\n"
        self.assertIn(call, cf)
        mutants = {
            "never called": (sh, cf.replace(call, "")),
            "called before rustup-init is installed": (sh, cf.replace(call, "").replace(
                "    install_packages_strict base; \\\n", "    install_packages_strict base; \\\n" + call)),
            "literal target": (sh.replace('"$(get build.native.linux.targets "$(uname -m)")"',
                                          '"x86_64-unknown-linux-musl"'), cf),
            "literal home": (sh.replace('"$(get build.toolchain rustup_home)"', '"/usr/lib/mios/rustup"'), cf),
            "unverified std": (sh.replace("libstd-*.rlib", "libcore-*.rlib"), cf),
            "unverified linker": (sh.replace("/bin/${linker}", "/bin/ld"), cf),
        }
        for name, (s, c) in mutants.items():
            with self.subTest(name):
                self.assertNotEqual((s, c), (sh, cf), name)
                self.assertNotEqual(rust_toolchain_violations(s, c), [], name)

    def test_login_env_puts_the_image_toolchain_first(self):
        """etc/profile.d/mios-env.sh: RUSTUP_HOME and the proxies' PATH come from the SSOT
        homes only when that toolchain exists; otherwise the environment is untouched."""
        if not shutil.which("sh"):
            self.skipTest("POSIX sh is required")
        src = _read(os.path.join(ROOT, "etc/profile.d/mios-env.sh"))
        fn = re.search(r"(?ms)^_mios_rust_env\(\) \{.*?^\}", src).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = _bash_directory(tmp)
            os.makedirs(os.path.join(tmp, "rustup/toolchains"))
            os.makedirs(os.path.join(tmp, "cargo/bin"))
            rustup = os.path.join(tmp, "cargo/bin/rustup")
            with open(rustup, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(rustup, 0o755)
            probe = fn + '\n_mios_rust_env\nprintf "%s|%s\\n" "${RUSTUP_HOME:-}" "${PATH%%:*}"\n'
            for home, want in ((f"{root}/rustup", f"{root}/rustup|{root}/cargo/bin"), (f"{root}/missing", "|/usr/bin")):
                with self.subTest(home=home):
                    env = {"PATH": "/usr/bin:/bin", "MIOS_BUILD_TOOLCHAIN_RUSTUP_HOME": home,
                           "MIOS_BUILD_TOOLCHAIN_CARGO_HOME": f"{root}/cargo"}
                    out = subprocess.run(["sh", "-c", probe], capture_output=True, text=True, env=env).stdout.strip()
                    self.assertEqual(out, want)

    def test_agent_clis_are_installed_by_the_agent_phase(self):
        sh = _read(AGENT_PHASE)
        self.assertIn("mios-mcp-server --agent-cli --install", sh)
        self.assertRegex(sh, r"--agent-cli --install \\\n\s+\|\| \{ mios_err [^}]*exit 1; \}",
                         "a failed agent CLI install must fail the build")
        self.assertIn("agent_cli enabled", sh)

    def test_package_and_mcp_sets_are_os_sections(self):
        self.assertRegex(_read(os.path.join(ROOT, "automation/05-repos.sh")),
                         r"for _build_section in [^;]*\bdevcontainer\b")
        cf = _read(OS_CF)
        self.assertIn("install_packages_strict mcp", cf)
        self.assertIn("mios-mcp-server --install-native", cf)


def code_server_violations(text):
    """Every way the tools phase fails to bake the SSOT-pinned, patched and verified code-server."""
    errs = []
    if "set -euo pipefail" not in text:
        errs.append("59-tools.sh: not set -euo pipefail")
    installs = [ln for ln in text.splitlines() if "dnf install" in ln and ".rpm" in ln]
    if len(installs) != 1:
        errs.append(f"59-tools.sh: {len(installs)} code-server RPM installs, expected 1")
    elif not re.search(r"code-server-\$\{_cs_version\}-\$\{_cs_arch\}\.rpm", installs[0]):
        errs.append("59-tools.sh: the RPM is not named from the SSOT version")
    for key in ("image.sidecars code_server", "theme.edge code_server_scrollbar_px", "theme.edge code_server_perimeter_px"):
        if key not in text:
            errs.append(f"59-tools.sh: no read of {key}")
    lines = text.splitlines()
    patch = [i for i, ln in enumerate(lines) if 'mios-vscode-custom-css" patch "${_cs_bake[@]}"' in ln]
    verify = [i for i, ln in enumerate(lines) if 'mios-vscode-custom-css" verify "${_cs_bake[@]}"' in ln]
    if len(patch) != 1 or len(verify) != 1 or patch[0] > verify[0]:
        errs.append("59-tools.sh: the workbench is not patched, then verified, once each")
    for i in patch + verify:
        if "||" in lines[i]:
            errs.append("59-tools.sh: a bake step's failure is masked")
    if re.search(r"code-server[-:/]v?\d+\.\d+\.\d+", text):
        errs.append("59-tools.sh: a code-server version literal")
    return errs


def rust_toolchain_violations(script, containerfile):
    """Every way the OS build fails to provision the SSOT Rust toolchain into the image."""
    errs = []
    m = re.search(r'(?ms)^if \[\[ "\$\{1:-\}" == --toolchain \]\]; then\n(.*?)^fi\n', script)
    if not m:
        return ["55-native-build.sh: no --toolchain mode"]
    block = m.group(1)
    for key in ("build.toolchain channel", "build.toolchain components", 'build.native.linux.targets "$(uname -m)"',
                "build.native.linux linker", "build.toolchain rustup_home", "build.toolchain cargo_home"):
        if key not in block:
            errs.append(f"55-native-build.sh --toolchain does not read {key} from the SSOT")
    if re.search(r"\b(x86_64|aarch64)-unknown-linux-\w+\b|\b1\.\d+\.\d+\b|/usr/lib/mios/(rustup|cargo)", block):
        errs.append("55-native-build.sh --toolchain names a target, version or home literal")
    if "rustup-init" not in block:
        errs.append("55-native-build.sh --toolchain never runs rustup-init")
    if "libstd-*.rlib" not in block or "/bin/${linker}" not in block:
        errs.append("55-native-build.sh --toolchain does not verify the target std and the linker")
    call = containerfile.find("55-native-build.sh --toolchain")
    packages = containerfile.find("install_packages_strict self-build;")
    if call == -1:
        errs.append("Containerfile never provisions the toolchain")
    elif packages == -1 or call < packages:
        errs.append("Containerfile provisions the toolchain before [packages.self-build] installs rustup-init")
    return errs


def containerfile_violations(text):
    """Every way the agents Containerfile fails to pin code-server or to bake and verify both patches."""
    errs = []
    lines = [ln.strip() for ln in text.replace("\\\n", " ").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    froms = [i for i, ln in enumerate(lines) if ln.startswith("FROM ")]
    cs = [i for i in froms if "ghcr.io/coder/code-server" in lines[i]]
    if not cs:
        errs.append("agents/Containerfile: no FROM ghcr.io/coder/code-server")
    for i in cs:
        if not re.fullmatch(r"FROM ghcr\.io/coder/code-server:\$\{MIOS_CODE_SERVER_VERSION\}(?: AS [A-Za-z0-9_-]+)?", lines[i]):
            errs.append(f"agents/Containerfile: {lines[i]} (unpinned)")
        if "ARG MIOS_CODE_SERVER_VERSION" not in lines[:i]:
            errs.append("agents/Containerfile: ARG MIOS_CODE_SERVER_VERSION (no default) must precede the FROM")
    for ln in lines:
        if re.match(r"ARG (MIOS_CODE_SERVER_VERSION|CODE_SERVER_\w+_PX)=", ln):
            errs.append(f"agents/Containerfile: {ln} (a default bypasses the layered loader)")
    after = lines[cs[0] + 1:] if cs else lines
    for a in ("CODE_SERVER_SCROLLBAR_PX", "CODE_SERVER_PERIMETER_PX"):
        if f"ARG {a}" not in after:
            errs.append(f"agents/Containerfile: ARG {a} missing after FROM")
    for src in (IMG_TOOL, IMG_CSS):
        if not any(re.fullmatch(rf"COPY --from=mios {re.escape(src)} \S+", ln) for ln in lines):
            errs.append(f"agents/Containerfile: COPY --from=mios {src} missing")
    runs = " ".join(ln for ln in lines if ln.startswith("RUN "))
    if f"wb={WB}" not in runs or 'test -f "$wb"' not in runs:
        errs.append(f"agents/Containerfile: test -f {WB} missing")
    for flag, arg in (("--scrollbar-px", "CODE_SERVER_SCROLLBAR_PX"), ("--perimeter-px", "CODE_SERVER_PERIMETER_PX")):
        if not re.search(rf'{flag} "\$\{{{arg}:\?\}}"', runs):
            errs.append(f"agents/Containerfile: {flag} not fed by a required ${{{arg}}}")
    for verb in ("patch", "verify"):
        if not re.search(rf'python3 {re.escape(IMG_TOOL)} {verb} "\$@"', runs):
            errs.append(f"agents/Containerfile: {IMG_TOOL} {verb} not run")
    return errs


def builder_violations(name, text):
    """A builder that does not pass every build arg from its mios-toml-get read, or the mios build-context."""
    errs = []
    for arg, (section, key) in ARGS.items():
        if arg not in text:
            errs.append(f"{name}: --build-arg {arg} missing")
        if not re.search(rf'{re.escape(section)}"?\)?[ ,]+"?{re.escape(key)}\b', text):
            errs.append(f"{name}: no mios-toml-get read of [{section}].{key}")
    if "mios=/" not in text or "--build-context" not in text:
        errs.append(f"{name}: --build-context mios=/ missing")
    if IMG_CSS not in text:
        errs.append(f"{name}: rebuild trigger ignores {IMG_CSS}")
    return errs


class TestAgentsContainerfile(unittest.TestCase):

    def test_repo_containerfile_is_clean(self):
        self.assertEqual(containerfile_violations(_read(CF)), [])

    def test_latest_is_named(self):
        text = re.sub(r"(?m)^FROM ghcr\.io/coder/code-server:.*$", "FROM ghcr.io/coder/code-server:latest", _read(CF))
        self.assertIn("agents/Containerfile: FROM ghcr.io/coder/code-server:latest (unpinned)", containerfile_violations(text))

    def test_stage_alias_does_not_hide_an_unpinned_image(self):
        text = re.sub(r"(?m)^FROM ghcr\.io/coder/code-server:.*$", "FROM ghcr.io/coder/code-server:latest AS editor", _read(CF))
        self.assertIn("agents/Containerfile: FROM ghcr.io/coder/code-server:latest AS editor (unpinned)", containerfile_violations(text))

    def test_missing_verify_is_named(self):
        text = _read(CF).replace(f'{IMG_TOOL} verify "$@"', "true")
        self.assertIn(f"agents/Containerfile: {IMG_TOOL} verify not run", containerfile_violations(text))

    def test_defaulted_arg_is_named(self):
        text = _read(CF).replace("ARG CODE_SERVER_PERIMETER_PX\n", "ARG CODE_SERVER_PERIMETER_PX=4\n")
        self.assertTrue(any("CODE_SERVER_PERIMETER_PX=4" in e for e in containerfile_violations(text)))


class TestBuilders(unittest.TestCase):

    def test_both_builders_clean(self):
        self.assertEqual(builder_violations("mios-agents-firstboot.sh", _read(FIRSTBOOT)), [])
        self.assertEqual(builder_violations("miosd main.rs", _read(MAIN_RS)), [])

    def test_dropped_arg_is_named(self):
        text = _read(MAIN_RS).replace("CODE_SERVER_PERIMETER_PX=", "X=")
        self.assertIn("miosd main.rs: --build-arg CODE_SERVER_PERIMETER_PX missing", builder_violations("miosd main.rs", text))

    def _resolve(self, values):
        """Run firstboot's resolve_build_args against a stub mios-toml-get that serves `values`."""
        body = re.search(r"(?ms)^resolve_build_args\(\) \{.*?^\}", _read(FIRSTBOOT)).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            stub = os.path.join(tmp, "mios-toml-get")
            with open(stub, "w", newline="\n") as f:
                f.write("#!/bin/sh\ncase \"$1 $2\" in\n")
                for (section, key), v in values.items():
                    f.write(f"  '{section} {key}') printf '%s\\n' '{v}' ;;\n")
                f.write("esac\n")
            os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
            shell_stub = _bash_directory(tmp) + "/mios-toml-get"
            script = (f'set -euo pipefail\nTOML_GET="{shell_stub}"\nlog() {{ echo "$*" >&2; }}\n{body}\n'
                      'resolve_build_args\nprintf "%s\\n" "${BUILD_ARGS[@]}"\n')
            result = subprocess.run(["bash", "-s"], input=script.encode(), capture_output=True)
            result.stdout = result.stdout.decode()
            result.stderr = result.stderr.decode()
            return result

    def test_firstboot_resolves_ssot_values(self):
        with open(TOML, "rb") as f:
            data = tomllib.load(f)
        img = data["image"]["sidecars"]["code_server"]
        edge = data["theme"]["edge"]
        vals = {("image.sidecars", "code_server"): img,
                ("theme.edge", "code_server_scrollbar_px"): edge["code_server_scrollbar_px"],
                ("theme.edge", "code_server_perimeter_px"): edge["code_server_perimeter_px"]}
        r = self._resolve(vals)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[:-1], [
            "--build-arg", f"MIOS_CODE_SERVER_VERSION={img.rsplit(':', 1)[1]}",
            "--build-arg", f"CODE_SERVER_SCROLLBAR_PX={edge['code_server_scrollbar_px']}",
            "--build-arg", f"CODE_SERVER_PERIMETER_PX={edge['code_server_perimeter_px']}",
            "--build-context", "mios=/"])

    def test_firstboot_refuses_unpinned(self):
        for img in ("ghcr.io/coder/code-server:latest", "ghcr.io/coder/code-server", "localhost:5000/code-server"):
            r = self._resolve({("image.sidecars", "code_server"): img,
                               ("theme.edge", "code_server_scrollbar_px"): 0,
                               ("theme.edge", "code_server_perimeter_px"): 0})
            self.assertNotEqual(r.returncode, 0, img)
            self.assertIn(f"'{img}' has no pinned tag", r.stderr)


def main():
    """Unit tests, then the repo agents Containerfile as a gate: exit 1 naming each violation."""
    ok = unittest.main(exit=False, argv=[sys.argv[0]]).result.wasSuccessful()
    for e in containerfile_violations(_read(CF)):
        print(f"VIOLATION: {e}", file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
