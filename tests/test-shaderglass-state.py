#!/usr/bin/env python3
# AI-hint: ShaderGlass profile/window-state persistence ([desktop.shaderglass.state]) in three tiers: 1. SSOT/source (always): the state table, its configurator inputs, the WineCap command channel (menu command ids match ShaderGlass' resource.h), the glue's SUPER+SHIFT+G cycle (old glue as negative control); 2. real binary (MIOS_SHADERGLASS_MIOSD, hermetic temp homes, never a compositor): status/stop/resume/cycle/save and SSOT refusals, old binary as negative control; 3. LIVE opt-in (MIOS_LIVE_NESTED=1): an owned nested MiOS PiP session with a private D-Bus + xdg-desktop-portal(-hyprland) stack, a private Wine prefix and install, real ShaderGlass 1.3.1 under Wine + DXVK + WineCap -- cycle, presentation-mode switches, detach/re-attach, a killed pane, a killed supervisor and setup round trips must all keep the chosen shader while the operator's default profile stays byte-identical; the installed miosd + WineCap is the negative control (the choice is lost).
# AI-related: src/mios-rs/miosd/src/shaderglass.rs, src/mios-rs/miosd/src/shaderglass/state.rs, usr/share/mios/shaderglass/patches/winecap-mios-portal.patch, usr/bin/mios-shaderglass, usr/share/mios/mios.toml [desktop.shaderglass.state], usr/share/mios/configurator/mios.html
# AI-functions: Source, Binary, LiveNested, Portal, Nested
"""ShaderGlass state persistence tests (unit, real binary, live nested)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("MIOS_TEST_ROOT") or Path(__file__).resolve().parents[1])
SSOT = Path(os.environ.get("MIOS_VENDOR_TOML_UNDER_TEST") or ROOT / "usr/share/mios/mios.toml")
GLUE = ROOT / "usr/bin/mios-shaderglass"
PATCH = ROOT / "usr/share/mios/shaderglass/patches/winecap-mios-portal.patch"
SG_SRC = Path(os.environ.get("MIOS_SHADERGLASS_SRC") or "/home/mios/mios-desktop-work-1/shaderglass-src")


def run(cmd, env=None, timeout=60, cwd=None):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env, timeout=timeout, cwd=cwd)


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def miosd(var: str, default: str | None) -> str | None:
    b = os.environ.get(var) or default
    if not b or not os.access(b, os.X_OK):
        return None
    return b if run([b, "shaderglass", "--help"]).returncode == 0 else None


# ---------------------------------------------------------------- 1. SSOT and source


class Source(unittest.TestCase):
    def setUp(self) -> None:
        self.d = ssot()
        self.sg = self.d["desktop"]["shaderglass"]

    def test_state_table_is_complete_and_strips_the_capture_source(self) -> None:
        st = self.sg["state"]
        self.assertIs(st["persist"], True)
        self.assertIs(st["resume"], True)
        self.assertFalse(st["directory"].startswith("/"))
        self.assertGreaterEqual(st["save_timeout_ms"], 100)
        self.assertIn(st["restart_limit"], range(0, 11))
        for k in ("CaptureWindow", "CaptureDesktop", "CaptureDevice", "CaptureFormat"):
            self.assertIn(k, st["strip_keys"])
        launch = self.sg["launch"]
        self.assertNotEqual(launch["cycle_shortcut"], launch["setup_shortcut"])
        cycle = [b for b in self.sg["bindings"] if b["args"] == "cycle"]
        self.assertEqual(len(cycle), 1, "SUPER+SHIFT+G cycles")

    def test_every_configurator_shaderglass_input_names_an_ssot_key(self) -> None:
        html = (ROOT / "usr/share/mios/configurator/mios.html").read_text(encoding="utf-8")
        keys = re.findall(r'data-key="(desktop\.shaderglass\.[^"]+)"', html)
        for need in ("desktop.shaderglass.state.persist", "desktop.shaderglass.state.restart_limit",
                     "desktop.shaderglass.state.resume", "desktop.shaderglass.state.save_timeout_ms",
                     "desktop.shaderglass.wine.driver", "desktop.shaderglass.launch.cycle_shortcut"):
            self.assertIn(need, keys)
        for k in keys:
            cur = self.d
            for part in k.split("."):
                self.assertIn(part, cur, f"configurator input {k} has no SSOT key")
                cur = cur[part]

    def test_winecap_command_channel_uses_shaderglass_menu_ids(self) -> None:
        text = PATCH.read_text(encoding="utf-8")
        for needle in ("MIOS_WINECAP_COMMAND_FILE", "mios_command_thread", "SendMessageTimeoutW", '"%s.ready"', '"%s.ack"',
                       "mios_start_command_channel();"):
            self.assertIn(needle, text)
        ids = dict(re.findall(r"#define MIOS_(ID[A-Z_]*) (\d+)", text))
        self.assertEqual(set(ids), {"ID_PROCESSING_SETASDEFAULT", "IDM_SHADER_NEXT", "IDM_TOGGLEMENU"})
        res = SG_SRC / "ShaderGlass/resource.h"
        if not res.is_file():
            self.skipTest(f"no ShaderGlass source checkout at {SG_SRC} (MIOS_SHADERGLASS_SRC)")
        real = dict(re.findall(r"#define (\w+)\s+(\d+)", res.read_text(encoding="utf-8", errors="replace")))
        for name, value in ids.items():
            self.assertEqual(real.get(name), value, f"{name}: patch {value}, ShaderGlass {real.get(name)}")
        # Negative control: a wrong id is caught by the same comparison.
        self.assertNotEqual(real.get("IDM_SHADER_NEXT"), str(int(ids["IDM_SHADER_NEXT"]) + 1))

    def test_patch_applies_to_the_installed_release_tag(self) -> None:
        if not (SG_SRC / ".git").exists():
            self.skipTest("no ShaderGlass checkout")
        installed = Path("/usr/lib/mios/shaderglass/INSTALLED.json")
        ref = json.loads(installed.read_text())["source_commit"] if installed.is_file() else "HEAD"
        with tempfile.TemporaryDirectory() as t:
            arch = subprocess.run(["git", "-C", str(SG_SRC), "archive", ref, "WineCap"], capture_output=True, check=True).stdout
            subprocess.run(["tar", "x", "-C", t], input=arch, check=True)
            r = run(["git", "apply", "--check", str(PATCH)], cwd=t)
            self.assertEqual(r.returncode, 0, r.stderr)

    def _glue(self, script: Path, *args: str, runtime: str | None = None) -> list[str]:
        """Run the glue with a recording fake miosd; return the arguments it passed."""
        with tempfile.TemporaryDirectory() as t:
            log = Path(t) / "args"
            fake = Path(t) / "miosd"
            fake.write_text(f'#!/bin/bash\n[[ "$*" == "shaderglass --help" ]] && exit 0\nprintf "%s\\n" "$*" >> {log}\n')
            fake.chmod(0o700)
            env = {k: v for k, v in os.environ.items() if k not in ("HYPRLAND_INSTANCE_SIGNATURE",)}
            env["XDG_RUNTIME_DIR"] = runtime or t
            env["HOME"] = t
            body = f'source {script}\nMIOSD={fake}\nhypr3d_is_active() {{ return 1; }}\nensure_hyprland_env() {{ :; }}\nmain "$@"\n'
            r = subprocess.run(["bash", "-c", body, "glue", *args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return log.read_text().splitlines() if log.exists() else []

    def test_super_shift_g_cycles_the_shader_in_2d(self) -> None:
        self.assertEqual(self._glue(GLUE, "cycle"), ["shaderglass cycle"])
        self.assertEqual(self._glue(GLUE, "preset"), ["shaderglass cycle"])
        self.assertEqual(self._glue(GLUE, "setup"), ["shaderglass setup"])
        self.assertEqual(self._glue(GLUE, "off"), ["shaderglass stop"])
        # Negative control: the glue before this change turned "cycle" into setup mode.
        base = os.environ.get("MIOS_SHADERGLASS_BASE_REF", "main")
        old = run(["git", "-C", ROOT, "show", f"{base}:usr/bin/mios-shaderglass"])
        if old.returncode != 0:
            self.skipTest(f"no {base} glue to compare")
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(old.stdout)
        try:
            got = self._glue(Path(f.name), "cycle")
            if got == ["shaderglass cycle"]:
                self.skipTest(f"{base} already has the fixed glue")
            self.assertEqual(got, ["shaderglass setup"], "the old glue mapped cycle to setup")
        finally:
            os.unlink(f.name)

    def test_nested_glue_never_reads_the_primary_room_status(self) -> None:
        text = GLUE.read_text()
        fn = text[text.index("hypr3d_is_active()"):text.index("ensure_hyprland_env()")]
        self.assertLess(fn.index('"/run/user/$(id -u)"'), fn.index("[[ -f /tmp/hypr3d-status.txt ]]"),
                        "the runtime-dir check comes before the primary's status file")


# ---------------------------------------------------------------- 2. real binary, hermetic


class Binary(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bin = miosd("MIOS_SHADERGLASS_MIOSD", None)
        cls.old = miosd("MIOS_SHADERGLASS_OLD_MIOSD", None)

    def setUp(self) -> None:
        if not self.bin:
            self.skipTest("set MIOS_SHADERGLASS_MIOSD to the candidate miosd")
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        for d in ("home", "run", "cfg", "data", "cache", "state"):
            (t / d).mkdir(mode=0o700)
        self.vendor = t / "mios.toml"
        shutil.copy(SSOT, self.vendor)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY", "MIOS_TOML", "DBUS_SESSION_BUS_ADDRESS")}
        self.env.update(MIOS_VENDOR_TOML=str(self.vendor), MIOS_HOST_TOML=str(t / "none"), MIOS_USER_TOML=str(t / "none"),
                        HOME=str(t / "home"), XDG_RUNTIME_DIR=str(t / "run"), XDG_CONFIG_HOME=str(t / "cfg"),
                        XDG_DATA_HOME=str(t / "data"), XDG_CACHE_HOME=str(t / "cache"), XDG_STATE_HOME=str(t / "state"),
                        DBUS_SESSION_BUS_ADDRESS=f"unix:path={t / 'nobus'}")
        self.state = t / "state/mios/shaderglass/state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sg(self, *a, binary=None):
        return run([binary or self.bin, "shaderglass", *a], env=self.env)

    def test_status_reports_the_store_and_a_session_scoped_unit(self) -> None:
        r = self.sg("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertEqual(Path(st["state"]["dir"]), self.state.parent)
        self.assertRegex(st["unit"], r"^app-mios-org\.mios\.ShaderGlass@overlay-[0-9a-f]{8}\.service$",
                         "a non-primary runtime dir gets its own supervisor unit")
        self.assertIsNone(st["supervisor"])

    def test_an_explicit_stop_forgets_what_was_attached(self) -> None:
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps({"version": 1, "classes": {}, "attached": [
            {"address": "0x1", "pid": 1, "stable_id": "", "class": "kitty", "setup": False}]}))
        r = self.sg("stop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.state.read_text())["attached"], [])
        r = self.sg("resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["resumed"], False)

    def test_pane_commands_need_a_supervisor(self) -> None:
        for verb in ("cycle", "save", "setup"):
            r = self.sg(verb)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no ShaderGlass pane is running", r.stderr)

    def test_bad_state_policy_is_refused(self) -> None:
        text = self.vendor.read_text()
        for old, new, needle in (('directory = "mios/shaderglass"', 'directory = "/var/lib/sg"', "relative path"),
                                 ("restart_limit = 2 ", "restart_limit = 50 ", "restart_limit"),
                                 ('cycle_shortcut = "n"', 'cycle_shortcut = "m"', "must differ")):
            self.assertIn(old, text)
            self.vendor.write_text(text.replace(old, new, 1))
            r = self.sg("status")
            self.assertNotEqual(r.returncode, 0, needle)
            self.assertIn(needle, r.stderr)
        self.vendor.write_text(text)
        self.assertEqual(self.sg("status").returncode, 0)

    def test_old_binary_has_no_cycle_save_or_resume(self) -> None:
        if not self.old:
            self.skipTest("set MIOS_SHADERGLASS_OLD_MIOSD (e.g. /usr/bin/miosd) for the negative control")
        for verb in ("cycle", "save", "resume"):
            r = self.sg(verb, "--help", binary=self.old)
            self.assertNotEqual(r.returncode, 0, f"old miosd already has {verb}")
            self.assertEqual(self.sg(verb, "--help").returncode, 0, f"candidate lacks {verb}")


# ---------------------------------------------------------------- 3. live nested


class Portal:
    """A private D-Bus session with xdg-permission-store, xdg-desktop-portal-hyprland (bound to the nested
    compositor, with a picker that runs the miosd under test) and xdg-desktop-portal. Nothing is activated."""

    def __init__(self, work: Path, nested_env: dict, picker_miosd: str):
        self.work = work
        self.procs = []
        bus = work / "bus"
        (bus / "services").mkdir(parents=True, mode=0o700)
        (bus / "session.conf").write_text(f"""<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>session</type><keep_umask/><listen>unix:path={bus}/bus</listen><servicedir>{bus}/services</servicedir>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/></policy></busconfig>
""")
        self.address = f"unix:path={bus}/bus"
        picker = work / "picker.sh"
        picker.write_text(f'#!/bin/bash\necho "$(date +%T) $*" >> {work}/picker.log\nexec {picker_miosd} shaderglass pick "$@"\n')
        picker.chmod(0o700)
        hypr = Path(nested_env["XDG_CONFIG_HOME"]) / "hypr"
        hypr.mkdir(parents=True, exist_ok=True)
        (hypr / "xdph.conf").write_text(f"screencopy {{\n    allow_token_by_default = true\n    custom_picker_binary = {picker}\n}}\n")
        self.env = dict(nested_env, DBUS_SESSION_BUS_ADDRESS=self.address)
        self.start(["dbus-daemon", f"--config-file={bus}/session.conf", "--nofork"], "bus")
        for _ in range(50):
            if (bus / "bus").exists():
                break
            time.sleep(0.1)
        self.start(["/usr/libexec/xdg-permission-store"], "perm")
        time.sleep(0.5)
        self.start(["/usr/libexec/xdg-desktop-portal-hyprland"], "xdph")
        time.sleep(1.5)
        self.start(["/usr/libexec/xdg-desktop-portal", "-r"], "xdp")

    def start(self, argv, name):
        self.procs.append(subprocess.Popen(argv, env=self.env, stdout=open(self.work / f"{name}.log", "w"),
                                           stderr=subprocess.STDOUT, start_new_session=True))

    def sources(self) -> int:
        r = run(["busctl", "--user", "get-property", "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                 "org.freedesktop.portal.ScreenCast", "AvailableSourceTypes"], env=self.env, timeout=10)
        return int(r.stdout.split()[-1]) if r.returncode == 0 and r.stdout.split() else 0

    def close(self):
        for p in reversed(self.procs):
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


class Nested:
    """An owned nested MiOS PiP session (mios-gen nested-session --layout pip), or the one MIOS_NESTED_SESSION
    names (its runtime directory) when a driver reuses it."""

    def __init__(self, work: Path, vendor: Path):
        self.launcher = None
        reuse = os.environ.get("MIOS_NESTED_SESSION")
        if reuse:
            self.dir = Path(reuse)
        else:
            env = {k: v for k, v in os.environ.items() if k not in ("MIOS_TOML",)}
            env["MIOS_VENDOR_TOML"] = str(vendor)
            self.launcher = subprocess.Popen(["/usr/bin/mios-gen", "nested-session", "--layout", "pip"], env=env,
                                             stdout=open(work / "launcher.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
            self.dir = Path(os.environ["XDG_RUNTIME_DIR"]) / f"mios-nested-desktop-{self.launcher.pid}"
        for _ in range(300):
            if (self.dir / "session.json").is_file():
                break
            time.sleep(0.2)
        s = json.loads((self.dir / "session.json").read_text())
        self.pid = s["instance"]["pid"]
        self.env = {"XDG_RUNTIME_DIR": str(self.dir), "HYPRLAND_INSTANCE_SIGNATURE": s["instance"]["instance"],
                    "WAYLAND_DISPLAY": s["instance"]["wl_socket"], "PIPEWIRE_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                    "XDG_CURRENT_DESKTOP": "Hyprland"}

    def close(self):
        if self.launcher:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.launcher.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.launcher.kill()


@unittest.skipUnless(os.environ.get("MIOS_LIVE_NESTED") == "1", "live nested ShaderGlass is opt-in (MIOS_LIVE_NESTED=1)")
class LiveNested(unittest.TestCase):
    """Real ShaderGlass in an owned nested PiP session. The primary compositor and the operator's prefix, state,
    portal and user manager are never touched: every XDG home, the D-Bus session and the Wine prefix are private."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.new = miosd("MIOS_SHADERGLASS_MIOSD", None)
        cls.old = miosd("MIOS_SHADERGLASS_OLD_MIOSD", "/usr/bin/miosd")
        cls.install = os.environ.get("MIOS_SHADERGLASS_INSTALL_DIR")
        cls.old_install = os.environ.get("MIOS_SHADERGLASS_OLD_INSTALL_DIR", "/usr/lib/mios/shaderglass")
        if not cls.new or not cls.install:
            raise unittest.SkipTest("set MIOS_SHADERGLASS_MIOSD and MIOS_SHADERGLASS_INSTALL_DIR (private install with the candidate WineCap)")
        cls.work = Path(os.environ.get("MIOS_LIVE_OUT") or tempfile.mkdtemp(prefix="mios-sg-state-"))
        cls.work.mkdir(parents=True, exist_ok=True)
        cls.work.chmod(0o700)
        cls.results = []
        home = cls.work / "home"
        for d in ("data", "state", "config", "cache"):
            (home / d).mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.environ.get("MIOS_SHADERGLASS_PREFIX_FROM"):
            dst = home / "data/mios/shaderglass/prefix"
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(os.environ["MIOS_SHADERGLASS_PREFIX_FROM"], dst, symlinks=True)
        text = SSOT.read_text()
        # This nested session has no XWayland: the Wine Wayland driver.
        text = re.sub(r'(?m)^driver = "x11"', 'driver = "wayland"', text, count=1)
        cls.vendors = {}
        for label, inst in (("new", cls.install), ("old", cls.old_install)):
            v = cls.work / f"mios-{label}.toml"
            v.write_text(text.replace('install_dir = "/usr/lib/mios/shaderglass"', f'install_dir = "{inst}"', 1))
            cls.vendors[label] = v
        (cls.work / "user.toml").write_text("")
        cls.nested = Nested(cls.work, cls.vendors["new"])
        cls.base_env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "MIOS_TOML", "MIOS_VENDOR_TOML")}
        cls.base_env.update(cls.nested.env)
        cls.base_env.update(XDG_DATA_HOME=str(home / "data"), XDG_STATE_HOME=str(home / "state"),
                            XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"),
                            MIOS_USER_TOML=str(cls.work / "user.toml"), MIOS_USER_TOML_D=str(cls.work / "user.d"),
                            MIOS_HOST_TOML=str(cls.work / "no-host.toml"))
        cls.portal = Portal(cls.work, dict(cls.base_env, MIOS_VENDOR_TOML=str(cls.vendors["new"])), cls.new)
        cls.serve = None
        cls.target = None
        cls.target_proc = None

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            if cls.serve and cls.serve.poll() is None:
                cls.ctl({"cmd": "stop"})
                try:
                    cls.serve.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    cls.serve.kill()
            if cls.target:
                cls.hypr_eval(f"pcall(function() hl.dispatch(hl.dsp.window.close({{window='address:{cls.target}'}})) end)")
        finally:
            cls.portal.close()
            cls.nested.close()
            (cls.work / "results.json").write_text(json.dumps(cls.results, indent=2))

    # ---- helpers
    @classmethod
    def env_for(cls, label="new"):
        e = dict(cls.base_env, DBUS_SESSION_BUS_ADDRESS=cls.portal.address, MIOS_VENDOR_TOML=str(cls.vendors[label]))
        return e

    @classmethod
    def hypr(cls, *a):
        return run(["hyprctl", *a], env=cls.env_for(), timeout=20)

    @classmethod
    def hypr_eval(cls, lua):
        return cls.hypr("eval", lua)

    @classmethod
    def clients(cls):
        try:
            return json.loads(cls.hypr("-j", "clients").stdout or "[]")
        except ValueError:
            return []

    @classmethod
    def client(cls, address):
        return next((c for c in cls.clients() if c["address"] == address), None)

    @classmethod
    def ctl(cls, req):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(30)
        try:
            s.connect(str(cls.nested.dir / "mios/shaderglass/control.sock"))
            s.sendall((json.dumps(req) + "\n").encode())
            return json.loads(s.makefile().readline())
        except OSError as e:
            return {"ok": False, "error": str(e)}
        finally:
            s.close()

    def check(self, name, ok, detail=""):
        self.results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:600]})
        print(f"{'PASS' if ok else 'FAIL'} {name} {str(detail)[:300]}", flush=True)
        return ok

    def wait(self, pred, timeout=60.0, step=0.25):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            v = pred()
            if v:
                return v
            time.sleep(step)
        return None

    def start_serve(self, binary, label):
        log = open(self.work / f"serve-{label}-{len(self.results)}.log", "w")
        type(self).serve = subprocess.Popen([binary, "shaderglass", "serve"], env=self.env_for(label), stdout=log,
                                            stderr=subprocess.STDOUT, start_new_session=True)
        self.assertTrue(self.wait(lambda: self.ctl({"cmd": "ping"}).get("ok"), 20), f"{label} supervisor answers")

    def stop_serve(self):
        if self.serve and self.serve.poll() is None:
            self.ctl({"cmd": "stop"})
            try:
                self.serve.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.serve.kill()
                self.serve.wait()

    def pane(self):
        st = self.ctl({"cmd": "status"})
        return next((p for p in st.get("panes", []) if p.get("target") == self.target), None)

    def mapped_pane(self, timeout=90):
        def ok():
            p = self.pane()
            if p and p.get("window") and p.get("state") in ("overlay", "setup"):
                c = self.client(p["window"])
                if c and c["title"].startswith("ShaderGlass ("):
                    return p, c
            return None
        return self.wait(ok, timeout)

    def shader_in_title(self, pane_addr):
        c = self.client(pane_addr)
        m = re.match(r"^ShaderGlass \((.*)\)$", c["title"]) if c else None
        return [x.strip() for x in m.group(1).split(",")] if m else []

    def default_profile(self):
        p = Path(self.base_env["XDG_DATA_HOME"]) / "mios/shaderglass/prefix/drive_c/users"
        found = list(p.glob("*/AppData/Local/ShaderGlassDefault.sgp"))
        return found[0].read_bytes() if found else None

    def state(self):
        f = Path(self.base_env["XDG_STATE_HOME"]) / "mios/shaderglass/state.json"
        return json.loads(f.read_text()) if f.is_file() else {}

    def open_target(self):
        cls = type(self)
        name = "mios-sg-state-" + uuid.uuid4().hex[:6]
        script = self.work / "target.py"
        script.write_text("import os,signal\ndef d(*_):\n os.write(1,(chr(27)+'[48;2;30;60;220m'+chr(27)+'[2J'+chr(27)+'[H'+'MiOS ShaderGlass state target').encode())\n"
                          "signal.signal(signal.SIGWINCH,d)\nd()\nwhile True: signal.pause()\n")
        self.hypr_eval(f"hl.dispatch(hl.dsp.exec_cmd('alacritty --class {name} --title MiOS-sg-state -o window.opacity=1.0 -e {sys.executable} {script}'))")
        c = self.wait(lambda: next((c for c in self.clients() if c["class"] == name), None), 30)
        self.assertIsNotNone(c, "target mapped")
        cls.target = c["address"]
        w = f"'address:{cls.target}'"
        # Odd sizes: a pane one pixel off would show here.
        self.hypr_eval(f"hl.dispatch(hl.dsp.window.float({{action='enable',window={w}}})); hl.dispatch(hl.dsp.window.resize({{x=801,y=503,window={w}}})); hl.dispatch(hl.dsp.window.move({{x=97,y=141,window={w}}}))")
        time.sleep(1.0)

    def geometry_matches(self, pane_addr, timeout=10):
        def same():
            t, p = self.client(self.target), self.client(pane_addr)
            if t and p and t["at"] == p["at"] and t["size"] == p["size"]:
                return (t["at"], t["size"])
            return None
        got = self.wait(same, timeout, 0.2)
        t, p = self.client(self.target), self.client(pane_addr)
        return got, (t and (t["at"], t["size"]), p and (p["at"], p["size"]))

    # ---- the scenario (one ordered test: every step depends on the live state of the previous one)
    def test_profile_and_window_state_survive_every_transition(self) -> None:
        self.assertTrue(self.wait(lambda: self.portal.sources() != 0, 20), "private portal offers ScreenCast")
        self.open_target()

        # ---- negative control: the installed miosd + WineCap loses the operator's choice
        if self.old:
            env = self.env_for("old")
            r = run([self.new, "shaderglass", "prefix"], env=self.env_for("new"), timeout=400)
            self.check("[setup] private prefix with the seeded default profile", r.returncode == 0, r.stdout + r.stderr)
            self.start_serve(self.old, "old")
            self.ctl({"cmd": "attach", "target": self.target, "setup": False})
            got = self.mapped_pane()
            self.check("[old] pane mapped", got, got and got[1]["title"])
            if got:
                first = self.shader_in_title(got[0]["window"])
                self.hypr_eval(f"pcall(function() hl.send_shortcut({{mods='', key='n', window='address:{got[0]['window']}'}}) end)")
                changed = self.wait(lambda: self.shader_in_title(got[0]["window"]) != first and self.shader_in_title(got[0]["window"]), 15)
                self.check("[old] the next-shader key changes the shader", changed, f"{first} -> {changed}")
                self.ctl({"cmd": "detach", "target": self.target})
                self.wait(lambda: self.pane() is None, 30)
                self.ctl({"cmd": "attach", "target": self.target, "setup": False})
                again = self.mapped_pane()
                after = again and self.shader_in_title(again[0]["window"])
                self.check("[old] NEGATIVE CONTROL: after detach/attach the chosen shader is lost", again and changed and after != changed and after == first,
                           f"chosen {changed}, relaunched with {after}")
                self.ctl({"cmd": "detach", "target": self.target})
                self.wait(lambda: self.pane() is None, 30)
            self.stop_serve()
        else:
            r = run([self.new, "shaderglass", "prefix"], env=self.env_for("new"), timeout=400)
            self.check("[setup] private prefix", r.returncode == 0, r.stdout + r.stderr)

        default_before = self.default_profile()
        self.check("[setup] the operator default profile exists", default_before, len(default_before or b""))

        # ---- candidate
        self.start_serve(self.new, "new")
        r = run([self.new, "shaderglass", "attach", "--window", self.target], env=self.env_for("new"), timeout=60)
        self.check("[new] attach through the client", r.returncode == 0, r.stdout + r.stderr)
        got = self.mapped_pane()
        self.assertTrue(self.check("[new] pane mapped", got, got and got[1]["title"]))
        pane, pc = got
        self.check("[new] the pane floats and is pinned (rules missed it under winewayland)",
                   self.wait(lambda: (lambda c: c and c["floating"] and c["pinned"])(self.client(pane["window"])), 10), pc)
        same, geo = self.geometry_matches(pane["window"])
        self.check("[new] pane exactly over the target (no 1 px drift)", same, geo)
        self.check("[new] WineCap command channel ready", self.wait(lambda: (self.pane() or {}).get("command_channel"), 20), self.pane())
        start_shader = self.shader_in_title(pane["window"])

        r = run([self.new, "shaderglass", "cycle", "--window", self.target], env=self.env_for("new"), timeout=60)
        reply = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
        self.check("[new] cycle runs inside ShaderGlass and saves", reply.get("via") == "winecap" and reply.get("shader"), r.stdout + r.stderr)
        chosen = self.wait(lambda: self.shader_in_title(pane["window"]) != start_shader and self.shader_in_title(pane["window"]), 15)
        self.check("[new] the shader changed", chosen, f"{start_shader} -> {chosen}")
        cls_entry = (self.state().get("classes") or {}).get(self.client(self.target)["class"], {})
        saved_name = (cls_entry.get("shader") or "").split("/")[-1]
        self.check("[new] the class profile records the chosen shader", chosen and saved_name and saved_name in chosen, f"{cls_entry} vs {chosen}")
        self.check("[new] the operator default profile is byte-identical after the save", self.default_profile() == default_before)
        shader_name = saved_name

        def same_shader(addr):
            return shader_name in self.shader_in_title(addr)

        # ---- presentation modes: same process, same shader, same geometry
        pid = pane["pid"]
        w = f"'address:{self.target}'"
        modes = [
            ("maximized", f"hl.dispatch(hl.dsp.window.fullscreen({{window={w},mode='maximized',action='set'}}))",
             f"hl.dispatch(hl.dsp.window.fullscreen({{window={w},mode='maximized',action='unset'}}))"),
            ("fullscreen", f"hl.dispatch(hl.dsp.window.fullscreen({{window={w},mode='fullscreen',action='set'}}))",
             f"hl.dispatch(hl.dsp.window.fullscreen({{window={w},mode='fullscreen',action='unset'}}))"),
            ("PiP (small, pinned)", f"hl.dispatch(hl.dsp.window.pin({{window={w},action='enable'}})); hl.dispatch(hl.dsp.window.resize({{x=361,y=203,window={w}}})); hl.dispatch(hl.dsp.window.move({{x=900,y=600,window={w}}}))",
             f"hl.dispatch(hl.dsp.window.pin({{window={w},action='disable'}})); hl.dispatch(hl.dsp.window.resize({{x=801,y=503,window={w}}})); hl.dispatch(hl.dsp.window.move({{x=97,y=141,window={w}}}))"),
            ("other workspace", f"hl.dispatch(hl.dsp.window.move({{workspace='7',follow=false,window={w}}}))",
             f"hl.dispatch(hl.dsp.window.move({{workspace='1',follow=false,window={w}}}))"),
        ]
        for label, on, off in modes:
            self.hypr_eval(on)
            time.sleep(2.0)
            p_on = self.pane() or {}
            during = same_shader(pane["window"])
            geo_on = self.geometry_matches(pane["window"], 6)[0] if label != "other workspace" else True
            self.hypr_eval(off)
            time.sleep(2.0)
            p_off = self.pane() or {}
            back, geo = self.geometry_matches(pane["window"])
            self.check(f"[new] {label}: same pane process, shader kept, pane follows",
                       p_on.get("pid") == pid and p_off.get("pid") == pid and during and same_shader(pane["window"]) and geo_on and back,
                       f"pid {pid}->{p_on.get('pid')}/{p_off.get('pid')} geo {geo}")
        # Hypr3D room (when the nested session loaded the plugin): the pane hides, keeps its process and profile.
        room = run(["/usr/bin/mios-gen", "hyprland-3d-toggle"], env=self.env_for("new"), timeout=30)
        time.sleep(4.0)
        p_room = self.pane() or {}
        run(["/usr/bin/mios-gen", "hyprland-3d-toggle"], env=self.env_for("new"), timeout=30)
        time.sleep(4.0)
        p_back = self.pane() or {}
        self.check("[new] Hypr3D room round trip: same pane process and shader", p_room.get("pid") == pid and p_back.get("pid") == pid and same_shader(pane["window"]),
                   f"toggle rc={room.returncode} {room.stderr.strip()[:200]}")

        # ---- detach / re-attach: a new process starts with the class profile
        self.ctl({"cmd": "detach", "target": self.target})
        self.wait(lambda: self.pane() is None, 30)
        self.check("[new] detach saved and kept the default", self.default_profile() == default_before)
        self.ctl({"cmd": "attach", "target": self.target, "setup": False})
        got = self.mapped_pane()
        self.check("[new] re-attach: new process started with the class profile and the chosen shader",
                   got and got[0]["pid"] != pid and got[0].get("profile") and self.wait(lambda: same_shader(got[0]["window"]), 20),
                   got and (got[0], got[1]["title"]))
        pane = got[0]

        # ---- a killed pane is relaunched with its profile
        os.kill(pane["pid"], signal.SIGKILL)
        def relaunched():
            p = self.pane()
            return p if p and p.get("pid") != pane["pid"] and p.get("restarts") == 1 and p.get("window") else None
        rp = self.wait(relaunched, 90)
        self.check("[new] SIGKILLed ShaderGlass is relaunched (restarts=1) with the chosen shader",
                   rp and self.wait(lambda: same_shader(rp["window"]), 30), rp)
        pane = rp or pane

        # ---- a killed supervisor: the next one resumes the very same target
        self.serve.kill()
        self.serve.wait()
        # its ShaderGlass (own session) is still around: stop it as KillMode=mixed would.
        try:
            os.kill(pane["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.wait(lambda: not self.client(pane["window"]), 20)
        self.check("[new] the attached record survived the dead supervisor", any(a.get("address") == self.target for a in self.state().get("attached", [])), self.state().get("attached"))
        self.start_serve(self.new, "new-resumed")
        got = self.mapped_pane()
        self.check("[new] a restarted supervisor resumed the pane with the chosen shader",
                   got and self.wait(lambda: same_shader(got[0]["window"]), 30), got and got[1]["title"])
        pane = got[0] if got else pane

        # ---- setup round trip (menu shown / hidden), saved on exit
        r1 = self.ctl({"cmd": "setup", "target": self.target})
        time.sleep(2)
        r2 = self.ctl({"cmd": "setup", "target": self.target})
        self.check("[new] setup on/off round trip saved on exit", r1.get("setup") is True and r2.get("setup") is False and r2.get("saved") is True, (r1, r2))
        same, geo = self.geometry_matches(pane["window"])
        self.check("[new] after setup the pane is exactly over the target again", same, geo)

        # ---- explicit stop: nothing to resume, default untouched
        self.stop_serve()
        self.check("[new] stop clears the attached record", self.state().get("attached") == [], self.state().get("attached"))
        self.check("[new] the operator default profile is byte-identical at the end", self.default_profile() == default_before)
        failed = [r for r in self.results if not r["ok"]]
        self.assertFalse(failed, json.dumps(failed, indent=1))


if __name__ == "__main__":
    unittest.main()
