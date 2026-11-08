# AI-hint: Hermetic two-sided tests for the code-server workbench bake (mios-vscode-custom-css patch/verify), the OS-pipeline phases that bake it and the toolchain, the dev container that is the MiOS image, and the mios-agents image and builders.
# AI-related: /usr/libexec/mios/mios-vscode-custom-css, /.devcontainer/Containerfile, /automation/59-tools.sh, /automation/55-native-build.sh, /tools/ci-suites.py, /.devcontainer/setup-devcontainer.sh, /.devcontainer/boot-mios-systems.sh, /usr/share/mios/agents/Containerfile, /usr/libexec/mios/mios-agents-firstboot.sh, /src/mios-rs/miosd/src/main.rs
# AI-functions: TestCodeServerBake, TestDevcontainerLifecycle, TestDevImageWiring, TestOsImageProvides, TestAgentsContainerfile, TestBuilders

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
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
        self.install_dir = self.root + "/installed tools"
        self.log = os.path.join(self.tmp, "calls.log")
        for directory in ("bin", "src/mios-rs", "tools/native", "runtime", ".devcontainer"):
            os.makedirs(os.path.join(self.tmp, directory), exist_ok=True)
        with open(os.path.join(self.tmp, "src/mios-rs/Cargo.toml"), "w") as fh:
            fh.write("# fixture\n")
        for name in ("cargo", "install", "sudo", "miosd"):
            self.shim("bin/" + name,
                      'printf "%s %s\\n" "' + name + '" "$*" >> "$CORE_LOG"\n'
                      'if [[ "' + name + '" == cargo && "${FAIL_BUILD:-}" == 1 ]]; then exit 71; fi\n')
        self.control_binary = self.root + "/custom target/release/miosd"
        self.native_binary = self.root + "/custom target/aarch64-unknown-linux-musl/release/mios-gen"
        for path in (self.control_binary, self.native_binary):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            Path(path).write_text("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        def artifact(path):
            return json.dumps({"reason": "compiler-artifact", "target": {
                "name": os.path.basename(path), "kind": ["bin"]}, "executable": path})
        self.shim("bin/cargo",
                  'printf "cargo %s\\n" "$*" >> "$CORE_LOG"\n'
                  '[[ "${FAIL_BUILD:-}" != 1 ]] || exit 71\n'
                  'case "$PWD" in */src/mios-rs) printf "%s\\n" '
                  + shlex.quote(artifact(self.control_binary)) + ' ;;\n'
                  '*/tools/native) [[ "${FAIL_BUILD:-}" != native ]] || exit 75\n'
                  'printf "%s\\n" ' + shlex.quote(artifact(self.native_binary)) + ' ;; esac\n'
                  'printf \'{"reason":"build-finished","success":true}\\n\'\n')
        self.shim("runtime/python", 'printf "runtime import\\n" >> "$CORE_LOG"\n')

    def shim(self, relative, body):
        path = os.path.join(self.tmp, relative)
        with open(path, "w", newline="\n") as fh:
            fh.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o755)

    def run_core(self, fail=False, after=""):
        if not shutil.which("bash"):
            self.skipTest("bash is required for the lifecycle hook")
        source = _read(os.path.join(ROOT, ".devcontainer/boot-mios-systems.sh"))
        source = source.split('case "${1:-start}" in', 1)[0]
        source = "set -euo pipefail\n" + source[source.index("mios_platform() {"):]
        source = source.replace("local root=/workspaces/MiOS", f'local root="{self.root}"')
        source = source.replace("local install_dir=/opt/mios/bin", f'local install_dir="{self.install_dir}"')
        source = source.replace("local agent_pipe_python=/usr/lib/mios/agents/.venv/bin/python",
                                f'local agent_pipe_python="{self.root}/runtime/python"')
        prefix = (f'export PATH="{self.root}/bin:$PATH"\n'
                  f'export CORE_LOG="{self.root}/calls.log"\n'
                  f'export CORE_INSTALL_DIR="{self.install_dir}"\n'
                  f'export FAIL_BUILD={"native" if fail == "native" else 1 if fail else 0}\n')
        # Send bytes: Windows text-mode pipes turn LF into CRLF, which Bash
        # treats as part of option names (including pipefail).
        result = subprocess.run(["bash", "-s"], input=(prefix + source + "\nmios_core\n" + after).encode(),
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
        self.assertEqual(["cargo build --locked --release --message-format=json-render-diagnostics",
                          "cargo build --locked --release --workspace --exclude mios-wallpaperd --message-format=json-render-diagnostics"], builds)
        self.assertLess(calls.index(builds[-1]), next(i for i, line in enumerate(calls) if line.startswith("sudo ")))
        self.assertIn("sudo install -m 0755 " + self.control_binary + " " + self.install_dir + "/miosd", calls)
        self.assertIn("sudo install -m 0755 " + self.native_binary + " " + self.install_dir + "/mios-gen", calls)
        self.assertEqual("miosd --help", calls[-1])

    def test_core_uses_fresh_tools_before_image_tools(self):
        """Install into scratch paths, then execute fresh tools over stale PATH entries."""
        for path in (self.control_binary, self.native_binary):
            Path(path).write_text('#!/usr/bin/env bash\nprintf "fresh %s %s\\n" '
                                  '"${0##*/}" "$*" >> "$CORE_LOG"\n')
        self.shim("bin/mios-gen", 'echo "stale image tool invoked" >&2; exit 78\n')
        self.shim("bin/sudo",
                  'printf "sudo %s\\n" "$*" >> "$CORE_LOG"\n'
                  'case "$1" in\n'
                  'install)\n'
                  '  dest="${!#}"\n'
                  '  [[ "$dest" == "$CORE_INSTALL_DIR" || "$dest" == "$CORE_INSTALL_DIR/"* ]] || exit 76\n'
                  '  if [[ "$2" == -d ]]; then mkdir -p "$dest"; else cp "$4" "$5"; chmod 0755 "$5"; fi ;;\n'
                  'ln) : ;;\n'
                  '*) exit 77 ;; esac\n')
        result, calls = self.run_core(after='mios-gen --help\nprintf "native-bin %s\\n" "${MIOS_NATIVE_BIN_DIR:-}"\n')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("fresh miosd --help", calls)
        self.assertEqual("fresh mios-gen --help", calls[-1])
        self.assertIn("native-bin " + self.install_dir, result.stdout)

    def test_remote_profiles_select_fresh_native_catalog(self):
        for relative in (".devcontainer/devcontainer.json", ".devcontainer/artifact-builder/devcontainer.json"):
            with self.subTest(profile=relative):
                doc = json.loads(_read(os.path.join(ROOT, relative)))
                self.assertEqual({"PATH": "/opt/mios/bin:${containerEnv:PATH}",
                                  "MIOS_NATIVE_BIN_DIR": "/opt/mios/bin"}, doc["remoteEnv"])

    def test_build_failure_stops_before_install(self):
        result, calls = self.run_core(fail=True)
        self.assertEqual(71, result.returncode, result.stderr)
        self.assertFalse(any(line.startswith("sudo ") for line in calls), calls)
        self.assertEqual(1, sum(line.startswith("cargo ") for line in calls))

    def test_native_build_failure_stops_before_install(self):
        result, calls = self.run_core(fail="native")
        self.assertEqual(75, result.returncode, result.stderr)
        self.assertFalse(any(line.startswith("sudo ") for line in calls), calls)
        self.assertEqual(2, sum(line.startswith("cargo ") for line in calls))

    def test_missing_artifact_receipts_stop_before_install(self):
        self.shim("bin/cargo", 'printf \'{"reason":"build-finished","success":true}\\n\'\n')
        result, calls = self.run_core()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Cargo did not report the required", result.stderr)
        self.assertFalse(any(line.startswith("sudo ") for line in calls), calls)

    def test_missing_runtime_fails_before_build(self):
        os.unlink(os.path.join(self.tmp, "runtime/python"))
        result, calls = self.run_core()
        self.assertEqual(1, result.returncode)
        self.assertIn("Missing agent-pipe runtime", result.stderr)
        self.assertEqual([], calls)

    def run_post_start(self, gateway_status=0):
        """Run the real post-start body without installs or background servers."""
        source = _read(os.path.join(ROOT, ".devcontainer/boot-mios-systems.sh"))
        source = source.split('case "${1:-start}" in', 1)[0]
        source = "set -euo pipefail\n" + source[source.index("mios_platform() {"):]
        source = source.replace("/workspaces/MiOS", self.root)
        source = source.replace("/workspaces/-dev-loop", self.root + "/missing-dev-loop")
        self.shim("bin/agy", "exit 0\n")
        # A listening socket takes the existing-server branch without launching
        # a process. No HTTP/application readiness is inferred from this socket.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.shim("bin/python3",
                      'printf "python3 %s\\n" "$*" >> "$CORE_LOG"\n'
                      'case "$1" in */mios-toml-get) printf "%s\\n" "' + str(port) + '" ;;\n'
                      '*) echo "unexpected Python entrypoint: $1" >&2; exit 72 ;; esac\n')
            self.shim(".devcontainer/mios-agent-pipe-dev",
                      'printf "gateway %s\\n" "$*" >> "$CORE_LOG"\n'
                      '[[ "$1" == check ]] || exit 73\n'
                      'exit "${GATEWAY_STATUS}"\n')
            env = dict(os.environ, PATH=self.root + "/bin:" + os.environ["PATH"],
                       CORE_LOG=self.root + "/calls.log", GATEWAY_STATUS=str(gateway_status))
            result = subprocess.run(["bash", "-s"],
                                    input=(source + "\nmios_post_start\n").encode(),
                                    capture_output=True, env=env)
        result.stdout = result.stdout.decode()
        result.stderr = result.stderr.decode()
        calls = _read(self.log).splitlines() if os.path.isfile(self.log) else []
        return result, calls

    def test_post_start_checks_shipped_gateway_without_missing_harness(self):
        result, calls = self.run_post_start()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("gateway check", calls[-1])
        self.assertIn("Ready for agent dispatch", result.stdout)

    def test_post_start_gateway_failure_is_fatal(self):
        result, calls = self.run_post_start(gateway_status=74)
        self.assertEqual(74, result.returncode, result.stderr)
        self.assertEqual("gateway check", calls[-1])
        self.assertNotIn("Ready for agent dispatch", result.stdout)


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
            data = tomllib.load(f)
            self.ref = data["image"]["ref"]
            self.ci_ref = data["ci"]["fedora"]["image"]
        self.ci = _load_ci_suites()

    def test_dev_containerfile_is_the_image(self):
        self.assertEqual(self.ci.devcontainer_violations(_read(DEV_CF), self.ref), [])

    def test_dev_containerfile_mutants_fail(self):
        cf = _read(DEV_CF)
        mutants = {
            "an install": cf + "RUN dnf install -y htop\n",
            "a hand-built code-server": cf + "RUN rpm -Uvh https://example.invalid/code-server-4.0.0-amd64.rpm\n",
            "a toolchain": cf + "RUN rustup-init -y\n",
            "a hardcoded image": cf.replace("FROM ${MIOS_IMAGE_REF}", "FROM " + self.ref),
            "the CI harness": cf.replace("FROM ${MIOS_IMAGE_REF}", "FROM " + self.ci_ref),
            "a stale default": cf.replace("MIOS_IMAGE_REF=" + self.ref, "MIOS_IMAGE_REF=ghcr.io/mios-dev/mios:0.2.4"),
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
            "a version literal": sh.replace('"${_cs_dl}/code-server-${_cs_version}-',
                                            '"${_cs_dl}/code-server-4.139.1-'),
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
        """The tracked etc/profile.d/50-mios.sh consolidation: RUSTUP_HOME and the proxies' PATH come from the SSOT
        homes only when that toolchain exists; otherwise the environment is untouched."""
        if not shutil.which("sh"):
            self.skipTest("POSIX sh is required")
        src = _read(os.path.join(ROOT, "etc/profile.d/50-mios.sh"))
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
    lines_no_comments = "\n".join(l for l in containerfile.splitlines() if not l.strip().startswith("#"))
    call = lines_no_comments.find("55-native-build.sh --toolchain")
    packages = lines_no_comments.find("install_packages_strict self-build;")
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


class TestDevcontainerPublicationSmoke(unittest.TestCase):
    """Exercise the actual publication gate across a private container boundary."""

    def _run(self, fault="", revision="", collision=False, minimum=4, dirty=""):
        with tempfile.TemporaryDirectory(prefix="mios-devcontainer-publication-") as tmp:
            root = Path(tmp)
            (root / "usr/share/mios").mkdir(parents=True)
            (root / ".devcontainer").mkdir()
            (root / "usr/share/mios/mios.toml").write_text(
                "[identity]\nusername='developer'\n[bootstrap.dev_vm]\nminimum_cpus="
                + str(minimum) + "\n[ci]\nsuite_timeout_s=30\n")
            doc = {"remoteUser": "developer", "build": {"args": {"MIOS_IDENTITY_USERNAME": "developer"}}}
            (root / ".devcontainer/devcontainer.json").write_text(json.dumps(doc))
            (root / ".devcontainer/Containerfile").write_text(_read(DEV_CF))
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=MiOS",
                            "-c", "user.email=mios@example.invalid", "commit", "-qm", "fixture"], check=True)
            expected = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            if dirty:
                tracked = root / "usr/share/mios/mios.toml"
                tracked.write_text(tracked.read_text() + "# tracked fixture change\n")
                if dirty == "staged":
                    subprocess.run(["git", "-C", str(root), "add", str(tracked)], check=True)
            tools = root / "bin"
            tools.mkdir()
            calls = root / "calls.json"
            calls.write_text("[]")
            sudo = tools / "sudo"
            sudo.write_text("#!" + sys.executable + "\n" + r'''
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
assert args[0]=="podman"
args=args[1:]
log=Path(os.environ["MIOS_SMOKE_CALLS"])
calls=json.loads(log.read_text());calls.append(args);log.write_text(json.dumps(calls))
fault=os.environ["MIOS_SMOKE_FAULT"]
if args[0]=="create":
    if os.environ["MIOS_SMOKE_COLLISION"]=="1": raise SystemExit(125)
    print("mios-fixture-container")
elif args[0]=="start":
    assert args==["start","mios-fixture-container"]
    if fault=="start": raise SystemExit(125)
elif args[0]=="exec":
    if "/usr/share/mios/source-revision" in args:
        if fault=="missing-revision": raise SystemExit(1)
        print(os.environ["MIOS_SMOKE_REVISION"])
    elif "/usr/share/mios/source-state" in args:
        if fault=="missing-state": raise SystemExit(1)
        print("dirty" if fault=="dirty-state" else "clean")
    elif "/run/mios-devcontainer-smoke/account-wiring.sh" in args:
        if fault=="account": raise SystemExit(7)
    elif "--user" in args:
        assert args[args.index("--user")+1]=="developer"
        if fault=="attach": raise SystemExit(7)
    else: raise AssertionError(args)
elif args[0]=="rm":
    assert args==["rm","--force","mios-fixture-container"]
else: raise AssertionError(args)
''')
            sudo.chmod(0o755)
            state = root / "mios-account-smoke"
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       MIOS_SMOKE_CALLS=str(calls), MIOS_SMOKE_FAULT=fault,
                       MIOS_SMOKE_COLLISION="1" if collision else "0",
                       MIOS_SMOKE_REVISION=revision or expected)
            script = 'source "$1"; mios_ci_require_cpus() { test "$2" -ge 4; }; mios_ci_verify_devcontainer_account "$2" localhost/mios:test "$3"'
            result = subprocess.run(["bash", "-c", script, "mios-publication",
                                     os.path.join(ROOT, "tools/lib/ci-runtime.sh"), str(root), str(state)],
                                    env=env, text=True, capture_output=True)
            wired = (state / "account-wiring.sh").read_text() if (state / "account-wiring.sh").exists() else None
            receipt = (state / "verified-account.txt").read_text() if (state / "verified-account.txt").exists() else None
            return result, json.loads(calls.read_text()), wired, receipt, expected

    def test_current_source_and_actual_wrapper_run_reach_nonroot_attach(self):
        result, calls, wired, receipt, expected = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        first = calls[0]
        self.assertEqual("create", first[0])
        self.assertIn("--pull=never", first)
        self.assertEqual("none", first[first.index("--network") + 1])
        self.assertEqual("4", first[first.index("--cpus") + 1])
        self.assertEqual("0:0", first[first.index("--user") + 1])
        actual = re.search(r"(?ms)^RUN (.*?)(?=^ENV )", _read(DEV_CF)).group(1).replace("\\\n", " ")
        self.assertEqual(actual, wired)
        self.assertTrue(any("--user" in call and "developer" in call for call in calls[1:]))
        self.assertEqual(["rm", "--force", "mios-fixture-container"], calls[-1])
        self.assertIn("source_revision=" + expected, receipt)
        self.assertIn("remote_user=developer\nuid=1000\n", receipt)

    def test_missing_or_stale_source_proof_stops_before_account_mutation(self):
        for kwargs in ({"fault": "missing-revision"}, {"revision": "0" * 40},
                       {"fault": "missing-state"}, {"fault": "dirty-state"}):
            with self.subTest(kwargs=kwargs):
                result, calls, _, receipt, _ = self._run(**kwargs)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(any("/run/mios-devcontainer-smoke/account-wiring.sh" in call for call in calls))
                self.assertEqual("rm", calls[-1][0])
                self.assertIsNone(receipt)

    def test_failed_account_or_nonroot_attach_never_publishes_success_receipt(self):
        for fault in ("account", "attach"):
            with self.subTest(fault=fault):
                result, calls, _, receipt, _ = self._run(fault=fault)
                self.assertNotEqual(0, result.returncode)
                self.assertIsNone(receipt)
                self.assertEqual("rm", calls[-1][0])
                if fault == "account":
                    self.assertFalse(any("--user" in call for call in calls[1:]))

    def test_collision_is_preserved_and_invalid_cpu_floor_never_starts(self):
        result, calls, _, receipt, _ = self._run(collision=True)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(["create"], [call[0] for call in calls])
        self.assertIsNone(receipt)
        result, calls, _, _, _ = self._run(minimum=2)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], calls)

    def test_failed_start_cleans_only_the_successfully_created_id(self):
        result, calls, _, receipt, _ = self._run(fault="start")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(["create", "start", "rm"], [call[0] for call in calls])
        self.assertEqual(["rm", "--force", "mios-fixture-container"], calls[-1])
        self.assertIsNone(receipt)

    def test_tracked_checkout_changes_never_start_a_publication_probe(self):
        for dirty in ("unstaged", "staged"):
            with self.subTest(dirty=dirty):
                result, calls, _, receipt, _ = self._run(dirty=dirty)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("Tracked checkout changes", result.stderr)
                self.assertEqual([], calls)
                self.assertIsNone(receipt)

    def test_source_proof_uses_context_git_and_refuses_an_unresolvable_worktree(self):
        producer = next(line.strip().removesuffix("\\").strip().removesuffix(";")
                        for line in _read(OS_CF).splitlines()
                        if "git -c safe.directory=/ctx -C /ctx rev-parse --verify" in line)
        with tempfile.TemporaryDirectory(prefix="mios-source-proof-") as tmp:
            root = Path(tmp) / "checkout"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=MiOS",
                            "-c", "user.email=mios@example.invalid", "commit", "--allow-empty",
                            "-qm", "fixture"], check=True)
            expected = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            proof = Path(tmp) / "source-revision"
            command = producer.replace("/ctx", shlex.quote(str(root))).replace(
                "/usr/share/mios/source-revision", shlex.quote(str(proof)))
            result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(expected, proof.read_text().strip())
            broken = Path(tmp) / "unresolvable-worktree"
            broken.mkdir()
            (broken / ".git").write_text("gitdir: /nonexistent/mios-source-control\n")
            command = producer.replace("/ctx", shlex.quote(str(broken))).replace(
                "/usr/share/mios/source-revision", shlex.quote(str(proof)))
            result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertNotEqual(expected, proof.read_text().strip())

    def test_partial_context_records_dirty_copied_inputs_without_rejecting_local_builds(self):
        source = _read(OS_CF)
        start = source.index("    git -c safe.directory=/ctx -C /ctx rev-parse --verify")
        producer = source[start:source.index("    dnf clean all;", start)].replace("\\\n", " ")
        with tempfile.TemporaryDirectory(prefix="mios-context-source-state-") as tmp:
            root = Path(tmp) / "context"
            (root / "usr").mkdir(parents=True)
            tracked = root / "usr/probe"
            tracked.write_text("clean\n")
            omitted = root / "README.md"
            omitted.write_text("intentionally absent from the ctx COPY stage\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=MiOS",
                            "-c", "user.email=mios@example.invalid", "commit", "-qm", "fixture"], check=True)
            # Omitted tracked paths must not make the partial readonly context dirty.
            omitted.unlink()
            proof, state = Path(tmp) / "revision", Path(tmp) / "state"
            command = producer.replace("/ctx", shlex.quote(str(root))).replace(
                "/usr/share/mios/source-revision", shlex.quote(str(proof))).replace(
                "/usr/share/mios/source-state", shlex.quote(str(state)))
            result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("clean", state.read_text().strip())
            for staged in (False, True):
                tracked.write_text("dirty\n")
                if staged:
                    subprocess.run(["git", "-C", str(root), "add", str(tracked)], check=True)
                result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("dirty", state.read_text().strip())
            tracked.unlink()
            result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("dirty", state.read_text().strip())

    def test_context_provenance_covers_every_copied_tracked_input(self):
        source = _read(OS_CF)
        ctx = source.split("FROM scratch AS ctx", 1)[1].split("\nFROM ", 1)[0]
        copied = {}
        for line in ctx.splitlines():
            if line.startswith("COPY "):
                _, path, destination = line.split()
                if path not in (".git", ".staged-portal/"):
                    copied[path] = destination
        for path, destination in copied.items():
            self.assertEqual("/ctx/" + path, destination)
        check = re.search(r"if git -c safe.directory=/ctx -C /ctx diff --quiet --no-ext-diff HEAD -- (.*?); then",
                          source.replace("\\\n", " "), re.S)
        self.assertIsNotNone(check)
        self.assertEqual(set(copied), set(check.group(1).split()))
        self.assertIn("Containerfile", copied)

    def test_both_publishers_gate_the_actual_image_before_external_actions(self):
        for relative, external in (
            (".github/workflows/mios-ci.yml", "      - name: Push the image"),
            (".forgejo/workflows/build-mios.yml", "      - name: Record the tag"),
        ):
            source = _read(os.path.join(ROOT, relative))
            gate = source.index("mios_ci_verify_devcontainer_account . localhost/mios:latest")
            self.assertLess(gate, source.index(external))
        source = _read(OS_CF)
        proof = source.index("> /usr/share/mios/source-revision")
        self.assertLess(source.index("git -c safe.directory=/ctx -C /ctx rev-parse --verify 'HEAD^{commit}'"), proof)
        self.assertLess(proof, source.index("rm -rf /tmp/build"))


class TestDevcontainerAccountProvisioning(unittest.TestCase):
    """Run the real Containerfile account flow with a private passwd/tool boundary."""

    def _script(self):
        text = _read(DEV_CF)
        return re.search(r"(?ms)^RUN (.*?)(?=^ENV )", text).group(1).replace("\\\n", " ")

    def _run(self, requested="user", image_user="user", users=None, declared=True, fail="", missing=""):
        with tempfile.TemporaryDirectory(prefix="mios-devcontainer-account-") as tmp:
            root = Path(tmp)
            tools = root / "bin"
            tools.mkdir()
            sudoers = root / "sudoers.d"
            sudoers.mkdir()
            state = root / "accounts.json"
            state.write_text(json.dumps({"users": users or {}, "calls": []}))
            dispatcher = tools / "account-tool"
            dispatcher.write_text("#!" + sys.executable + "\n" + r'''
import json,os,shlex,sys
from pathlib import Path
p=Path(os.environ["MIOS_ACCOUNT_STATE"])
s=json.loads(p.read_text()); command=Path(sys.argv[0]).name; args=sys.argv[1:]
s["calls"].append([command,*args])
def save(): p.write_text(json.dumps(s))
def add(user): s["users"][user]={"uid":1000,"group":user,"home":"/var/home/"+user}
if command==os.environ.get("MIOS_ACCOUNT_FAIL"): save();raise SystemExit(7)
if command=="mios-subuid-alloc":
    if args[0]=="--user": print(args[1]+":100000:65536")
elif command=="mios-toml-get":
    print(os.environ["MIOS_IMAGE_USER"] if args[-1]=="username" else "/bin/bash")
elif command=="getent":
    kind,name=args
    found=next(((u,v) for u,v in s["users"].items() if (v["group"] if kind=="group" else u)==name or str(v["uid"])==name),None)
    if not found: save();raise SystemExit(2)
    u,v=found
    print(f'{u}:x:{v["uid"]}:1000:fixture:{v["home"]}:/bin/bash' if kind=="passwd" else f'{v["group"]}:x:1000:')
elif command=="id":
    u=args[-1]
    if u not in s["users"]: save();raise SystemExit(1)
    print(s["users"][u]["group"] if args[0]=="-gn" else s["users"][u]["uid"])
elif command=="systemd-sysusers":
    if args==["-"]:
        add(shlex.split(sys.stdin.read())[1])
    elif os.environ["MIOS_DECLARED"]=="1":
        add(os.environ["MIOS_IMAGE_USER"])
elif command=="usermod":
    user=args[args.index("--login")+1];old=args[-1]
    s["users"][user]=s["users"].pop(old);s["users"][user]["home"]=args[args.index("--home")+1]
elif command=="groupmod":
    old=args[-1];new=args[args.index("--new-name")+1]
    for v in s["users"].values():
        if v["group"]==old:v["group"]=new
elif command=="setpriv":
    user=args[args.index("--reuid")+1]
    assert s["users"][user]["uid"]==1000
    assert (Path(os.environ["MIOS_ACCOUNT_SUDOERS"])/"90-mios-devcontainer").read_text().startswith(user+" ALL=")
save()
''')
            dispatcher.chmod(0o755)
            for name in ("mios-toml-get", "getent", "id", "systemd-sysusers", "systemd-tmpfiles",
                         "usermod", "groupmod", "chage", "install", "cp", "chown", "visudo", "setpriv", "sudo", "mios-subuid-alloc"):
                if name != missing:
                    (tools / name).symlink_to(dispatcher.name)
            script = self._script().replace("/usr/libexec/mios/mios-toml-get", str(tools / "mios-toml-get"))
            script = script.replace("/usr/libexec/mios/mios-subuid-alloc", str(tools / "mios-subuid-alloc"))
            script = script.replace("/etc/sudoers.d", str(sudoers))
            script = script.replace("/etc/subuid", str(root / "subuid")).replace("/etc/subgid", str(root / "subgid"))
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                       MIOS_IDENTITY_USERNAME=requested, MIOS_IMAGE_USER=image_user,
                       MIOS_ACCOUNT_STATE=str(state), MIOS_ACCOUNT_SUDOERS=str(sudoers),
                       MIOS_DECLARED="1" if declared else "0", MIOS_ACCOUNT_FAIL=fail)
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            return result, json.loads(state.read_text())

    def test_missing_base_account_helpers_fail_before_provisioning(self):
        for helper in ("mios-toml-get", "mios-subuid-alloc"):
            with self.subTest(helper=helper):
                result, state = self._run(missing=helper)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("base image lacks required account helper", result.stderr)
                self.assertIn("publish the current full MiOS image", result.stderr)
                self.assertEqual([], state["calls"])
                self.assertEqual({}, state["users"])

    def test_missing_account_is_materialized_before_nonroot_selection(self):
        result, state = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1000, state["users"]["user"]["uid"])
        names = [c[0] for c in state["calls"]]
        self.assertLess(names.index("systemd-sysusers"), max(i for i, name in enumerate(names) if name == "getent"))
        self.assertEqual("setpriv", names[-1])
        self.assertIn("USER 1000", _read(DEV_CF))

    def test_source_identity_replaces_only_the_known_stale_image_identity(self):
        result, state = self._run(requested="developer", image_user="image-user")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("image-user", state["users"])
        self.assertEqual(1000, state["users"]["developer"]["uid"])
        self.assertEqual("developer", state["users"]["developer"]["group"])

    def test_missing_sysusers_declaration_still_provisions_canonical_identity(self):
        result, state = self._run(requested="developer", declared=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1000, state["users"]["developer"]["uid"])

    def test_existing_canonical_account_is_preserved_without_replaying_sysusers(self):
        users = {"developer": {"uid": 1000, "group": "custom-group", "home": "/home/developer"}}
        result, state = self._run(requested="developer", image_user="image-user", users=users, declared=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(users, state["users"])
        self.assertNotIn("usermod", [c[0] for c in state["calls"]])
        self.assertNotIn("systemd-sysusers", [c[0] for c in state["calls"]])

    def test_invalid_or_root_identity_is_rejected_before_account_changes(self):
        for name in ("root", "bad user", "0", "-option", "UPPER"):
            with self.subTest(name=name):
                result, state = self._run(requested=name)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(["mios-toml-get"], [c[0] for c in state["calls"]])

    def test_unrelated_uid_owner_is_not_renamed(self):
        users = {"someone-else": {"uid": 1000, "group": "someone-else", "home": "/home/someone-else"}}
        result, state = self._run(users=users, declared=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("unrelated account", result.stderr)
        self.assertNotIn("usermod", [c[0] for c in state["calls"]])

    def test_wrong_uid_and_failed_account_or_sudo_setup_fail(self):
        users = {"user": {"uid": 2000, "group": "user", "home": "/home/user"}}
        result, _ = self._run(users=users, declared=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("must own UID 1000", result.stderr)
        for command in ("systemd-sysusers", "visudo", "setpriv", "sudo", "mios-subuid-alloc"):
            with self.subTest(command=command):
                result, _ = self._run(fail=command)
                self.assertNotEqual(0, result.returncode)

    def test_both_profiles_pass_the_same_user_selected_for_attach(self):
        for rel in (".devcontainer/devcontainer.json", ".devcontainer/artifact-builder/devcontainer.json"):
            doc = json.loads(_read(os.path.join(ROOT, rel)))
            self.assertEqual(doc["remoteUser"], doc["build"]["args"]["MIOS_IDENTITY_USERNAME"])
            self.assertNotEqual("root", doc["remoteUser"])


class TestCloudContainerAccountReuse(unittest.TestCase):
    """A stale container or missing canonical account cannot reach lifecycle hooks."""

    def _run(self, container_image="sha256:current", account=True, uid="1000", configured="user"):
        source = _read(os.path.join(ROOT, ".devcontainer/cloud-shell/bootstrap.sh"))
        functions = "\n".join(re.search(rf"(?ms)^{name}\(\) \{{.*?^\}}", source).group(0)
                              for name in ("container_account", "up"))
        with tempfile.TemporaryDirectory(prefix="mios-cloud-account-") as tmp:
            env = dict(os.environ, TROOT=tmp, TSTATE=os.path.join(tmp, "state"),
                       CALLS=os.path.join(tmp, "calls"), HOOKS=os.path.join(tmp, "hooks"),
                       CONTAINER_IMAGE=container_image, ACCOUNT="1" if account else "0",
                       ACCOUNT_UID=uid, CONFIGURED_USER=configured)
            harness = r'''
set -euo pipefail
ROOT="$TROOT"; STATE="$TSTATE"; CONFIG=fixture; RT=mock_runtime
log() { :; }
die() { echo "$*" >&2; exit 1; }
require_root_checkout() { :; }
select_runtime() { :; }
select_config() { :; }
preflight() { :; }
pull_image() { :; }
container_name() { echo mios-private-test; }
ssot() { [[ "$*" == "--vendor identity username" ]] && echo user; }
need() {
    case "$*" in
        "identity username") echo user ;;
        "image ref") echo registry.invalid/mios:latest ;;
        "image dev_local_tag") echo localhost/mios-devcontainer:test ;;
        "workspace root") echo /workspaces ;;
        "workspace primary") echo MiOS ;;
        *) return 2 ;;
    esac
}
dc_json() {
    case "$1" in
        remoteUser) echo "$CONFIGURED_USER" ;;
        workspaceFolder) echo /workspaces ;;
        *) return 2 ;;
    esac
}
mock_runtime() {
    printf '%s\n' "$*" >> "$CALLS"
    case "$1:$2" in
        build:*) return 0 ;;
        container:inspect) [[ "$3" != --format ]] || echo "$CONTAINER_IMAGE"; return 0 ;;
        image:inspect) echo sha256:current ;;
        start:*) return 0 ;;
        exec:*)
            [[ "$ACCOUNT" == 1 ]] || { echo "no matching entries in passwd file" >&2; return 126; }
            echo "$ACCOUNT_UID" ;;
        *) return 2 ;;
    esac
}
lifecycle() { echo "$1" >> "$HOOKS"; }
status() { :; }
'''
            result = subprocess.run(["bash", "-c", harness + functions + "\nup\n"], env=env,
                                    capture_output=True, text=True)
            calls = Path(env["CALLS"]).read_text() if Path(env["CALLS"]).exists() else ""
            hooks = Path(env["HOOKS"]).read_text().splitlines() if Path(env["HOOKS"]).exists() else []
            created = Path(env["TSTATE"], "lifecycle.created").exists()
            return result, calls, hooks, created

    def test_current_image_and_account_reach_both_lifecycle_hooks(self):
        result, calls, hooks, created = self._run()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["postCreateCommand", "postStartCommand"], hooks)
        self.assertTrue(created)
        self.assertIn("--build-arg MIOS_IDENTITY_USERNAME=user", calls)
        self.assertIn("--build-arg MIOS_IMAGE_REF=registry.invalid/mios:latest", calls)
        self.assertIn("exec -u user mios-private-test id -u", calls)
        self.assertNotIn("exec -u root", calls)

    def test_stale_image_is_preserved_and_rejected_before_start(self):
        result, calls, hooks, created = self._run(container_image="sha256:older")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("older image", result.stderr)
        self.assertNotIn("start ", calls)
        self.assertFalse(hooks or created)
        self.assertFalse(any(calls.startswith(action) for action in ("rm ", "stop ", "rename ")))

    def test_missing_account_reports_rebuild_without_lifecycle_success(self):
        result, _, hooks, created = self._run(account=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cannot select canonical user", result.stderr)
        self.assertFalse(hooks or created)

    def test_root_or_wrong_uid_never_reaches_lifecycle(self):
        for kwargs in (dict(configured="root"), dict(configured="stale-user"), dict(uid="0"), dict(uid="2000")):
            with self.subTest(kwargs=kwargs):
                result, calls, hooks, created = self._run(**kwargs)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(hooks or created)
                self.assertNotIn("exec -u root", calls)


def main():
    """Unit tests, then the repo agents Containerfile as a gate: exit 1 naming each violation."""
    ok = unittest.main(exit=False, argv=[sys.argv[0]]).result.wasSuccessful()
    for e in containerfile_violations(_read(CF)):
        print(f"VIOLATION: {e}", file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
