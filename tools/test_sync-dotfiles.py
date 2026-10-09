#!/usr/bin/env python3
# AI-hint: Hermetic fixtures for sync-dotfiles.py: ADR-0024 prune and --check both ways, surface mode kept, empty partition fails loud, forwardPorts/containerEnv projected from [ports] keys and resolved MIOS_* names, a stale stylesheet copy refused.
# AI-related: tools/sync-dotfiles.py, usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh
# AI-functions: main, test_rewrite_keeps_surface_mode, test_new_surface_gets_umask_mode, test_forward_ports_projection, test_container_env_projection, test_stale_stylesheet_copy_refused, test_edge_settings_projected
"""What the client-portable projection must not get wrong.

A browser client throws on the first API-written key it never registered, so
the merge that used to be additive must PRUNE, and its --check must go red in
both directions: a desktop-only key back on a surface, and a key that left the
SSOT list while the surfaces still lack it (a scan of the surfaces for listed
keys passes that one -- the Check-Without-Diff this file plants).
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "sync-dotfiles.py")

FAILED: list[str] = []
PASSED = 0

DESKTOP_ONLY = ["window.customTitleBarVisibility", "window.titleBarStyle"]
UNREGISTERED = ["vscode_custom_css.policy"]
SSOT = {
    "workbench.colorTheme": "MiOS-Dev",
    "window.titleBarStyle": "custom",
    "window.customTitleBarVisibility": "never",
    "editor.fontSize": 15,
    "window.density.layout": "compact",
    "workbench.experimental.modernUI": True,
}
EDGE = '[theme.edge]\ncode_server_density = "compact"\ncode_server_modern_ui = true\n'


def check(name, got, want):
    global PASSED
    if got == want:
        PASSED += 1
    else:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")


FWD_KEYS = '["web", "api"]'
PORTS = "[ports]\nstack_id = 0\nweb = 9100\napi = 9200\n[ai]\nendpoint = \"http://localhost:${MIOS_PORT_API}/v1\"\n"
ENV_KEYS = '["MIOS_AI_ENDPOINT"]'
WORKSPACE = (
    "[workspace]\n"
    'root = "/workspaces"\n'
    'primary = "MiOS"\n'
    'devcontainer = ".devcontainer/devcontainer.json"\n'
    'repos = [{ name = "MiOS", url = "https://github.com/mios-dev/MiOS.git" }]\n'
)
HOST = '{ cpus = 8, memory = "32gb", storage = "64gb" }'
IDENTITY = '[identity]\nusername = "fx-user"\n'


def _toml(desktop_only, fwd_keys=FWD_KEYS, ports=PORTS, env_keys=ENV_KEYS, edge=EDGE, workspace=WORKSPACE,
          host=HOST, identity=IDENTITY):
    keys = "".join(f'    "{k}",\n' for k in desktop_only)
    unreg = "".join(f'    "{k}",\n' for k in UNREGISTERED)
    return ("[dotfiles.vscode]\n"
            f"desktop_only_keys = [\n{keys}]\n"
            "user_only_keys = []\n"
            f"unregistered_keys = [\n{unreg}]\n"
            'client_portable_surfaces = [".devcontainer/devcontainer.json", "x.code-workspace"]\n'
            'bootstrap_client_portable_surfaces = [".devcontainer/devcontainer.json"]\n'
            + "[dotfiles.devcontainer]\n"
            + (f"forward_port_keys = {fwd_keys}\n" if fwd_keys is not None else "")
            + (f"container_env_keys = {env_keys}\n" if env_keys is not None else "")
            + (f"host_requirements = {host}\n" if host is not None else "")
            + ports + edge + workspace + (identity or ""))


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _fixture(root, desktop_only=DESKTOP_ONLY):
    """A minimal MiOS root + a bootstrap sibling, each surface still carrying
    the whole profile plus a surface-only key that must survive."""
    _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(desktop_only))
    _write(os.path.join(root, ".dotfiles/vscode/settings.json"), json.dumps(SSOT, indent=2) + "\n")
    _write(os.path.join(root, ".dotfiles/code-server/settings.json"), json.dumps(SSOT, indent=2) + "\n")
    _write(os.path.join(root, "usr/share/mios/keybindings/vscode-settings.json"), "{}\n")
    stale = dict(SSOT, **{"vscode_custom_css.policy": True, "zenMode.showTabs": "none"})
    dev = {"name": "fx", "containerEnv": {"MIOS_AI_ENDPOINT": "http://127.0.0.1:8080/v1", "MIOS_AI_ROLE": "builder"},
           "customizations": {"vscode": {"settings": stale}}, "forwardPorts": [8080, 11450]}
    _write(os.path.join(root, ".devcontainer/devcontainer.json"), json.dumps(dev, indent=2) + "\n")
    _write(os.path.join(root, "x.code-workspace"), json.dumps({"folders": [], "settings": stale}, indent=2) + "\n")
    boot = os.path.join(root, "..", "mios-bootstrap")
    _write(os.path.join(boot, ".devcontainer/devcontainer.json"), json.dumps(dev, indent=2) + "\n")
    return boot


def _run(root, boot, *args):
    env = dict(os.environ, MIOS_ROOT=root, MIOS_BOOTSTRAP_ROOT=boot, HOME=os.path.join(root, "home"))
    os.makedirs(env["HOME"], exist_ok=True)
    res = subprocess.run([sys.executable, TOOL, *args], env=env, capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


def _settings(path, key_path):
    d = json.load(open(path, encoding="utf-8"))
    for k in key_path:
        d = d[k]
    return d


def _mode(path):
    return oct(stat.S_IMODE(os.stat(path).st_mode))


def _load_tool():
    """The tool as a module (its name carries a hyphen), for the one helper a
    fixture run cannot reach: a surface written where none existed before."""
    spec = importlib.util.spec_from_file_location("sync_dotfiles", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prune_then_check_both_ways():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("stale surfaces are drift", rc, 1)
        check("--check names the planted file and key",
              "DRIFT MiOS/.devcontainer/devcontainer.json: desktop-only key window.customTitleBarVisibility" in out, True)
        check("--check names the unregistered key", "unregistered key vscode_custom_css.policy" in out, True)
        check("--check names the bootstrap surface", "DRIFT mios-bootstrap/.devcontainer/devcontainer.json" in out, True)

        rc, out = _run(root, boot)
        check("projection succeeds", rc, 0)
        dev = _settings(os.path.join(root, ".devcontainer/devcontainer.json"), ("customizations", "vscode", "settings"))
        ws = _settings(os.path.join(root, "x.code-workspace"), ("settings",))
        bdev = _settings(os.path.join(boot, ".devcontainer/devcontainer.json"), ("customizations", "vscode", "settings"))
        for label, s in (("devcontainer", dev), ("workspace", ws), ("bootstrap devcontainer", bdev)):
            check(f"{label}: desktop-only keys pruned", [k for k in DESKTOP_ONLY if k in s], [])
            check(f"{label}: unregistered key pruned", "vscode_custom_css.policy" in s, False)
            check(f"{label}: portable keys present", (s.get("workbench.colorTheme"), s.get("editor.fontSize")), ("MiOS-Dev", 15))
            check(f"{label}: surface-only key survives", s.get("zenMode.showTabs"), "none")
        skel = json.load(open(os.path.join(root, "etc/skel/.config/Code/User/settings.json"), encoding="utf-8"))
        check("settings FILE copy keeps the desktop profile", skel.get("window.customTitleBarVisibility"), "never")

        rc, out = _run(root, boot, "--check")
        check("green after projection", rc, 0)

        # (a) a desktop-only key put back on one surface
        p = os.path.join(root, ".devcontainer/devcontainer.json")
        d = json.load(open(p, encoding="utf-8"))
        d["customizations"]["vscode"]["settings"]["window.customTitleBarVisibility"] = "never"
        _write(p, json.dumps(d, indent=2) + "\n")
        rc, out = _run(root, boot, "--check")
        check("planted key is red", rc, 1)
        check("planted key is named with its file",
              "DRIFT MiOS/.devcontainer/devcontainer.json: desktop-only key window.customTitleBarVisibility" in out, True)
        _run(root, boot)  # repair

        # (b) the key leaves the SSOT list while the surfaces still lack it
        _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(["window.titleBarStyle"]))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("de-listed key is red (no Check-Without-Diff)", rc, 1)
        check("de-listed key is named", "SSOT key window.customTitleBarVisibility is missing from the surface" in out, True)


def test_unregistered_key_refused_at_the_source():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        p = os.path.join(root, ".dotfiles/vscode/settings.json")
        _write(p, json.dumps(dict(SSOT, **{"vscode_custom_css.policy": True}), indent=2) + "\n")
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("SSOT carrying an unregistered key is red", rc, 1)
        check("the source file is named", "DRIFT .dotfiles/vscode/settings.json: unregistered key vscode_custom_css.policy" in out, True)
        rc, out = _run(root, boot)
        check("write mode refuses it too", rc, 1)


def test_rewrite_keeps_surface_mode():
    """mkstemp creates 0600 and os.replace carries the temp file's mode, so a
    rewritten surface silently came back 0600: the tracked 100755
    devcontainer.json showed as a mode change. A rewrite keeps the target's mode."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        dev = os.path.join(root, ".devcontainer/devcontainer.json")
        ws = os.path.join(root, "x.code-workspace")
        os.chmod(dev, 0o755)
        os.chmod(ws, 0o644)
        rc, out = _run(root, boot, "--client-surfaces")
        check("mode: projection succeeds", rc, 0)
        # the surfaces carried stale keys, so both were really rewritten
        check("mode: the 0o755 surface was rewritten", "window.titleBarStyle" in _settings(dev, ("customizations", "vscode", "settings")), False)
        check("mode: the 0o644 surface was rewritten", "window.titleBarStyle" in _settings(ws, ("settings",)), False)
        check("mode: a 0o755 surface keeps its mode after a rewrite", _mode(dev), oct(0o755))
        check("mode: a 0o644 surface keeps its mode after a rewrite", _mode(ws), oct(0o644))
        check("mode: no temp file is left beside the surface",
              [f for f in os.listdir(os.path.dirname(dev)) if f.startswith(".devcontainer.json.")], [])


def test_new_surface_gets_umask_mode():
    """A surface written where none existed gets what a plain open() gives it,
    0o666 masked by the process umask, never mkstemp's private 0o600."""
    mod = _load_tool()
    with tempfile.TemporaryDirectory() as tmp:
        old = os.umask(0o022)
        try:
            p = os.path.join(tmp, "new", "devcontainer.json")
            mod._write_atomic(p, "{}\n")
            check("mode: a new surface is 0o644 under umask 022", _mode(p), oct(0o644))
            check("mode: the new surface holds the text", open(p, encoding="utf-8").read(), "{}\n")
        finally:
            os.umask(old)


def test_empty_partition_fails_loud():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        _write(os.path.join(root, "usr/share/mios/mios.toml"), "[dotfiles.vscode]\ndesktop_only_keys = []\n")
        rc, out = _run(root, boot, "--check")
        check("an empty desktop-only list is exit 3, never a vacuous pass", rc, 3)
        check("the missing list is named", "desktop_only_keys is empty or absent" in out, True)
        _write(os.path.join(root, "usr/share/mios/mios.toml"), "[meta]\nx = 1\n")
        rc, out = _run(root, boot, "--check")
        check("an absent partition is exit 3", rc, 3)


def test_forward_ports_projection():
    """forwardPorts is owned whole on every devcontainer.json (both repos), in forward_port_keys order,
    with the stack_id offset; a workspace file never gets one; a stale literal and an unknown key are named."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("fwd: stale literals are drift", rc, 1)
        check("fwd: the stale array is named with its file",
              "DRIFT MiOS/.devcontainer/devcontainer.json: forwardPorts [8080, 11450] differs from the "
              "[dotfiles.devcontainer].forward_port_keys projection [9100, 9200]" in out, True)
        rc, out = _run(root, boot, "--client-surfaces")
        check("fwd: projection succeeds", rc, 0)
        dev = json.load(open(os.path.join(root, ".devcontainer/devcontainer.json"), encoding="utf-8"))
        bdev = json.load(open(os.path.join(boot, ".devcontainer/devcontainer.json"), encoding="utf-8"))
        ws = json.load(open(os.path.join(root, "x.code-workspace"), encoding="utf-8"))
        check("fwd: MiOS devcontainer carries the keys in order", dev.get("forwardPorts"), [9100, 9200])
        check("fwd: bootstrap devcontainer too", bdev.get("forwardPorts"), [9100, 9200])
        check("fwd: a workspace file gets none", "forwardPorts" in ws, False)
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("fwd: green after projection", rc, 0)

        _write(os.path.join(root, "usr/share/mios/mios.toml"),
               _toml(DESKTOP_ONLY, fwd_keys='["api", "web"]', ports=PORTS.replace("stack_id = 0", "stack_id = 1")))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("fwd: reordered keys + stack_id are drift", rc, 1)
        check("fwd: the new projection is named", "projection [19200, 19100]" in out, True)

        _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(DESKTOP_ONLY, fwd_keys='["web", "nope"]'))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("fwd: an unknown key is exit 3", rc, 3)
        check("fwd: the unknown key is named", "names 'nope', which is not an integer [ports] key" in out, True)

        _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(DESKTOP_ONLY, fwd_keys=None))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("fwd: an absent list is exit 3, never an unowned array", rc, 3)


def test_container_env_projection():
    """Each container_env_keys entry is set, on every devcontainer.json, to the value the resolver emits
    (ports offset and ${MIOS_*} expanded); other containerEnv keys survive; an unknown name is exit 3."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("env: a literal endpoint is drift", rc, 1)
        check("env: the stale value is named with its file",
              "DRIFT mios-bootstrap/.devcontainer/devcontainer.json: containerEnv.MIOS_AI_ENDPOINT "
              "'http://127.0.0.1:8080/v1' differs from the resolved SSOT value 'http://localhost:9200/v1'" in out, True)
        check("env: projection succeeds", _run(root, boot, "--client-surfaces")[0], 0)
        for label, repo in (("MiOS", root), ("bootstrap", boot)):
            env = json.load(open(os.path.join(repo, ".devcontainer/devcontainer.json"), encoding="utf-8"))["containerEnv"]
            check(f"env: {label} endpoint resolved", env, {"MIOS_AI_ENDPOINT": "http://localhost:9200/v1", "MIOS_AI_ROLE": "builder"})
        check("env: green after projection", _run(root, boot, "--check", "--client-surfaces")[0], 0)
        _write(os.path.join(root, "usr/share/mios/mios.toml"),
               _toml(DESKTOP_ONLY, ports=PORTS.replace("stack_id = 0", "stack_id = 1")))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("env: a stack_id change is drift", rc, 1)
        check("env: the offset value is named", "resolved SSOT value 'http://localhost:19200/v1'" in out, True)
        _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(DESKTOP_ONLY, env_keys='["MIOS_NOPE"]'))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("env: an unknown name is exit 3", rc, 3)
        check("env: the unknown name is named", "names 'MIOS_NOPE', which the resolver does not emit" in out, True)
        _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(DESKTOP_ONLY, env_keys=None))
        check("env: an absent list is exit 3", _run(root, boot, "--check", "--client-surfaces")[0], 3)


def test_user_and_host_projection():
    """The devcontainer runs the MiOS image, so remoteUser is its [identity].username and hostRequirements
    is [dotfiles.devcontainer].host_requirements, on every devcontainer.json; a stale literal is drift,
    an absent or malformed source is exit 3."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("user: an unprojected surface is drift", rc, 1)
        check("user: the missing user is named",
              "DRIFT MiOS/.devcontainer/devcontainer.json: remoteUser None differs from the "
              "[identity].username projection 'fx-user'" in out, True)
        check("host: the missing requirement is named on bootstrap too",
              "DRIFT mios-bootstrap/.devcontainer/devcontainer.json: hostRequirements None differs" in out, True)
        check("user: projection succeeds", _run(root, boot, "--client-surfaces")[0], 0)
        for label, repo in (("MiOS", root), ("bootstrap", boot)):
            dev = json.load(open(os.path.join(repo, ".devcontainer/devcontainer.json"), encoding="utf-8"))
            check(f"user: {label} remoteUser", dev.get("remoteUser"), "fx-user")
            check(f"host: {label} hostRequirements", dev.get("hostRequirements"),
                  {"cpus": 8, "memory": "32gb", "storage": "64gb"})
        ws = json.load(open(os.path.join(root, "x.code-workspace"), encoding="utf-8"))
        check("user: a workspace file gets none", "remoteUser" in ws or "hostRequirements" in ws, False)
        check("user: green after projection", _run(root, boot, "--check", "--client-surfaces")[0], 0)
        # A planted literal (the retired mios-dev user, the old 32 GB host) is drift again.
        p = os.path.join(root, ".devcontainer/devcontainer.json")
        dev = json.load(open(p, encoding="utf-8"))
        dev["remoteUser"], dev["hostRequirements"]["storage"] = "mios-dev", "32gb"
        _write(p, json.dumps(dev, indent=2) + "\n")
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("user: a planted literal user is drift", rc == 1 and "remoteUser 'mios-dev' differs" in out, True)
        check("host: a planted small disk is drift", "'storage': '32gb'} differs" in out, True)
        for bad in (dict(host=None), dict(host='{ cpus = 8, memory = "32", storage = "64gb" }'),
                    dict(host='{ cpus = 0, memory = "32gb", storage = "64gb" }'), dict(identity=None),
                    dict(identity='[identity]\nusername = "Not A User"\n')):
            _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(DESKTOP_ONLY, **bad))
            check(f"user/host: {bad} is exit 3", _run(root, boot, "--check", "--client-surfaces")[0], 3)


def test_stale_stylesheet_copy_refused():
    """The code-server stylesheet is rendered in one place; a returning byte copy is drift and write mode deletes it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        _run(root, boot)
        rel = ".dotfiles/code-server/code-server-terminal.css"
        _write(os.path.join(root, rel), "/* css */\n")
        rc, out = _run(root, boot, "--check")
        check("css: a stale copy is drift", rc, 1)
        check("css: the stale copy is named", f"DRIFT {rel}: stale copy of the rendered stylesheet" in out, True)
        rc, out = _run(root, boot)
        check("css: write mode succeeds", rc, 0)
        check("css: write mode deleted the copy", os.path.exists(os.path.join(root, rel)), False)
        check("css: the mirror did not recreate it",
              os.path.exists(os.path.join(root, "usr/share/mios/dotfiles/code-server/code-server-terminal.css")), False)


def test_edge_settings_projected():
    """[theme.edge] renders window.density.layout / modernUI into both .dotfiles sources and on to their copies."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "MiOS")
        boot = _fixture(root)
        _run(root, boot)
        check("edge: green at the vendor values", _run(root, boot, "--check")[0], 0)
        _write(os.path.join(root, "usr/share/mios/mios.toml"),
               _toml(DESKTOP_ONLY, edge=EDGE.replace('"compact"', '"spacious"').replace("= true", "= false")))
        rc, out = _run(root, boot, "--check", "--client-surfaces")
        check("edge: an edited key is drift", rc, 1)
        check("edge: the drift names the source and key",
              "DRIFT .dotfiles/vscode/settings.json: window.density.layout is 'compact', mios.toml "
              "[theme.edge].code_server_density renders 'spacious'" in out, True)
        check("edge: projection succeeds", _run(root, boot)[0], 0)
        for rel in (".dotfiles/vscode/settings.json", ".dotfiles/code-server/settings.json",
                    "etc/skel/.local/share/code-server/User/settings.json"):
            d = json.load(open(os.path.join(root, rel), encoding="utf-8"))
            check(f"edge: {rel} rendered", (d["window.density.layout"], d["workbench.experimental.modernUI"]),
                  ("spacious", False))
        dev = _settings(os.path.join(root, ".devcontainer/devcontainer.json"), ("customizations", "vscode", "settings"))
        check("edge: the devcontainer block follows", dev.get("window.density.layout"), "spacious")
        check("edge: green after projection", _run(root, boot, "--check")[0], 0)
        _write(os.path.join(root, "usr/share/mios/mios.toml"), _toml(DESKTOP_ONLY, edge=""))
        rc, out = _run(root, boot, "--check")
        check("edge: absent [theme.edge] keys are exit 3", rc, 3)
        check("edge: exit 3 names the key", "[theme.edge] lacks code_server_density" in out, True)


def main() -> int:
    for fn in (test_prune_then_check_both_ways, test_unregistered_key_refused_at_the_source,
               test_rewrite_keeps_surface_mode, test_new_surface_gets_umask_mode, test_empty_partition_fails_loud,
               test_forward_ports_projection, test_container_env_projection, test_user_and_host_projection,
               test_stale_stylesheet_copy_refused,
               test_edge_settings_projected):
        fn()
    for f in FAILED:
        print("FAIL " + f, file=sys.stderr)
    print(f"[test_sync-dotfiles] {PASSED} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
