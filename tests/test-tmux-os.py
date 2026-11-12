# AI-hint: MiOS-Tmux (tmux-os) behaviour on PRIVATE tmux servers only: generated tables and idempotent reloads, explicit run-shell panes, keyboard/controller/console menus, float shell toggle/lift/dock/reap, hidden sessions, the SSOT Apps and TUIs launcher, outside-tmux app entry, float-safe workspace layout and the narrowed pane-shell reload.
# AI-related: usr/share/mios/tmux/mios-keys.tmux.conf, usr/share/mios/tmux/mios-theme.tmux.conf, usr/libexec/mios/mios-terminal, tools/native/mios-gen/src/tmux_os.rs, tools/native/mios-unit-gen/src/tmux_os.rs, etc/profile.d/50-mios.sh, usr/libexec/mios/mios-mcp-server
# AI-functions: Harness, TestTmuxOs

"""Every tmux server here is `tmux -L mios-tmuxos-test-<pid>-<tag>-{i,o}` under a fresh 0700 TMUX_TMPDIR in /tmp,
killed (and the directory removed) in cleanup. The SSOT is the worktree vendor file plus a private host layer;
the user layer is pointed at a file that does not exist, so no personal configuration is read. Every tmux CLI call
has a timeout (display-popup from a CLI blocks until the popup closes)."""

import hashlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "usr/share/mios/mios.toml"
TERMINAL = ROOT / "usr/libexec/mios/mios-terminal"
BIN = Path(os.environ.get("MIOS_TMUX_OS_BIN", "/var/lib/mios-build/tmp/mios-tmux-os/target/x86_64-unknown-linux-musl/release"))
GEN, UNITGEN, RELAY = BIN / "mios-gen", BIN / "mios-unit-gen", BIN / "mios-agent-relay"
# miosd (the tmux-os launcher, `miosd launcher`) lives in the src/mios-rs workspace, so it has its own build dir.
MIOSD = Path(os.environ.get("MIOS_TMUX_OS_MIOSD", str(BIN / "miosd")))
DESKTOP_TMUX = Path(os.environ["MIOS_TMUX_OS_TMUX"]) if os.environ.get("MIOS_TMUX_OS_TMUX") else None
DESKTOP_TMUX_SHA256 = os.environ.get("MIOS_TMUX_OS_TMUX_SHA256", "")
SSOT = tomllib.loads(VENDOR.read_text())
OPERATOR_SOCKET = SSOT["keybindings"]["socket_name"]
PREFIX = "mios-tmuxos-test-"

LAUNCHER_OFF = """
[keybindings.tmux.launcher]
enabled = false
"""

BASE_HOST = """
[mcp.tmux.workspace]
global_sessions = false

# Existing cases retain their explicit legacy layout contract. Global popup
# placement has its own actual-terminal case below.
[terminal.popups]
enabled = false
"""

STUB = """#!/bin/sh
# test TUI: records its argv, then waits
printf '%s\\n' "$*" >> "{log}"
read -r cg < /proc/$$/cgroup
printf 'CG %s %s\\n' "$1" "$cg" >> "{log}.cgroup"
echo "FAKE-TUI $*"
exec sleep 300
"""

CATALOGUE = """
[terminal.tui_launcher]
popup_scope = {scope}

[[terminal.tuis]]
id = "ok"
label = "Fake OK"
section = "system"
mode = "window"
argv = ["fake-tui", "window"]

[[terminal.tuis]]
id = "missing"
label = "Fake Missing"
section = "system"
mode = "window"
argv = ["definitely-missing-tui-{tag}"]

[[terminal.tuis]]
id = "pop"
label = "Fake Popup"
section = "mios"
mode = "popup"
argv = ["fake-tui", "popup"]

[[terminal.tuis]]
id = "novt"
label = "Fake NoVT"
section = "files"
mode = "window"
vt_ok = false
argv = ["fake-tui", "novt"]

[[terminal.tuis]]
id = "vtrow"
label = "Fake VT"
section = "edit"
mode = "window"
argv = ["fake-tui", "gui"]
argv_vt = ["fake-tui", "vt"]
"""


def wait(pred, timeout=6.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            v = pred()
            if v:
                return v
        except subprocess.SubprocessError:
            pass
        time.sleep(step)
    return pred()


class Harness:
    """A private inner server loaded with the generated conf, viewed by real clients in a private outer server."""

    def __init__(self, tag, host_extra="", global_popups=False):
        self.dir = Path(tempfile.mkdtemp(prefix="mtt.", dir="/tmp"))  # short path: AF_UNIX sun_path limit
        os.chmod(self.dir, 0o700)
        self.inner = f"{PREFIX}{os.getpid()}-{tag}-i"
        self.outer = f"{PREFIX}{os.getpid()}-{tag}-o"
        for name in (self.inner, self.outer):
            assert name.startswith(PREFIX) and name != OPERATOR_SOCKET and not name.startswith(("tmux-os", "mios-workspace-"))
        (self.dir / "bin").mkdir()
        if DESKTOP_TMUX is not None:
            (self.dir / "bin/tmux").symlink_to(DESKTOP_TMUX)
        (self.dir / "run").mkdir(mode=0o700)
        self.log = self.dir / "tui.log"
        stub = self.dir / "bin/fake-tui"
        stub.write_text(STUB.replace("{log}", str(self.log)))
        stub.chmod(0o755)
        self.host = self.dir / "host.toml"
        base = BASE_HOST.replace("enabled = false", "enabled = true") if global_popups else BASE_HOST
        self.host.write_text(base + host_extra)
        # No desktop variables: a launcher pop-out must never open a window on the operator's desktop.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TMUX", "MIOS_", "SSH_", "WAYLAND_", "HYPRLAND_")) and k not in ("DISPLAY", "BASH_ENV")}
        env.update({
            "TMUX_TMPDIR": str(self.dir),
            "MIOS_VENDOR_TOML": str(VENDOR),
            "MIOS_HOST_TOML": str(self.host),
            "MIOS_USER_TOML": str(self.dir / "no-user.toml"),
            "MIOS_VENDOR_TOML_D": str(self.dir / "no.d"),
            "MIOS_HOST_TOML_D": str(self.dir / "no.d"),
            "MIOS_USER_TOML_D": str(self.dir / "no.d"),
            "XDG_CONFIG_HOME": str(self.dir / "cfg"),
            # XDG_RUNTIME_DIR stays the caller's: the popup scope (systemd-run --user) needs the user manager.
            "MIOS_UNIT_GEN": str(UNITGEN),
            "MIOS_TERMINAL_TEST_SOCKET_DIR": str(self.dir),
            "MIOS_TERMINAL_TEST_SOCKET": self.inner,
            "MIOS_TERMINAL_TEST_GEN": str(GEN),
            "MIOS_TERMINAL_TEST_MIOSD": str(MIOSD),
            "MIOS_TMUX_HELPER": "1",  # any workspace-ensure path returns early
            "PATH": f"{self.dir / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        })
        self.env = env
        self.conf = self.render(self.dir / "runtime")

    def render(self, out):
        r = subprocess.run([str(GEN), "render-tmux-theme", "--runtime", str(out)], env=self.env, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        text = (out / "tmux.conf").read_text()
        assert "set-hook -g client-attached" not in text, "workspace hooks must be off in tests"
        conf = out / "test.conf"
        conf.write_text(text.replace("/usr/libexec/mios/mios-terminal", str(TERMINAL)).replace("/usr/bin/miosd ", f"{MIOSD} ").replace("/usr/bin/mios-gen ", f"{GEN} "))
        return conf

    def I(self, *args, check=True, timeout=5):
        r = subprocess.run(["tmux", "-L", self.inner, *args], env=self.env, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise AssertionError(f"tmux {args}: {r.stderr}")
        return r.stdout.rstrip("\n")

    def O(self, *args, check=True):
        r = subprocess.run(["tmux", "-L", self.outer, "-f", os.devnull, *args], env=self.env, capture_output=True, text=True, timeout=5)
        if check and r.returncode != 0:
            raise AssertionError(f"outer {args}: {r.stderr}")
        return r.stdout.rstrip("\n")

    def start(self, w=100, h=30):
        self.I("-f", str(self.conf), "new-session", "-d", "-s", "main", "-c", str(self.dir), "-x", str(w), "-y", str(h), "bash --norc --noprofile")
        self.I("set-option", "-g", "default-command", "bash --norc --noprofile")
        self.I("set-environment", "-g", "MIOS_TMUX_HELPER", "1")

    def view(self, name="view", term="xterm-256color", w=100, h=30, session="main"):
        before = set(self.clients())
        cmd = f"env -u TMUX TERM={term} tmux -L {self.inner} attach -t ={session}"
        if self.O("has-session", "-t", "=v", check=False) == "" and subprocess.run(
                ["tmux", "-L", self.outer, "has-session", "-t", "=v"], env=self.env, capture_output=True, timeout=5).returncode == 0:
            self.O("new-window", "-t", "=v:", "-n", name, cmd)
        else:
            self.O("new-session", "-d", "-s", "v", "-n", name, "-x", str(w), "-y", str(h), cmd)
        self.O("set", "-g", "escape-time", "0")
        # Wait for THIS viewer's client: with another viewer attached, "any client" would answer at once.
        fresh = wait(lambda: set(self.clients()) - before)
        assert fresh, f"viewer {name} never attached"
        return wait(lambda: self.client_of(name, fresh))

    def client_of_pid(self, name):
        pid = self.O("display", "-p", "-t", f"=v:{name}", "#{pane_pid}")
        for line in self.I("list-clients", "-F", "#{client_name}\t#{client_pid}").splitlines():
            c, p = line.split("\t")
            if self.parent_of(p) == pid or p == pid:
                return c
        return None

    def clients(self):
        return [l for l in self.I("list-clients", "-F", "#{client_name}", check=False).splitlines() if l]

    def client_of(self, name, fresh=()):
        found = self.client_of_pid(name)
        if found:
            return found
        return sorted(fresh)[0] if len(fresh) == 1 else None

    @staticmethod
    def parent_of(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[1]
        except OSError:
            return ""

    def keys(self, name, *keys):
        self.O("send-keys", "-t", f"=v:{name}", *keys)

    def raw(self, name, text):
        self.O("send-keys", "-t", f"=v:{name}", "-l", text)

    def shot(self, name="view", delay=0.5):
        time.sleep(delay)
        return self.O("capture-pane", "-p", *(("-e",) if getattr(self, "esc", False) else ()), "-t", f"=v:{name}")

    def close(self):
        for sock in (self.inner, self.outer):
            subprocess.run(["tmux", "-L", sock, "kill-server"], env=self.env, capture_output=True, timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)


def prefix_keys(listing):
    keys = set()
    for line in listing.splitlines():
        m = re.match(r"bind-key\s+(?:-r\s+)?-T prefix\s+(\S+)", line)
        if m:
            keys.add(m.group(1).replace("\\", ""))
    return keys


@unittest.skipUnless(shutil.which("tmux") and GEN.is_file() and UNITGEN.is_file(), "tmux and the lane's built mios-gen/mios-unit-gen are required")
class TestTmuxOs(unittest.TestCase):
    def harness(self, tag, host_extra=""):
        h = Harness(tag, host_extra)
        self.addCleanup(h.close)
        return h

    @unittest.skipUnless(MIOSD.is_file(), "the built native launcher is required")
    def test_t11_global_popups_center_native_floats_and_keep_mouse_menus_open(self):
        extra = CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "global-popups")
        h = Harness("global-popups", extra, global_popups=True)
        self.addCleanup(h.close)
        h.start(240, 67)
        client = h.view(w=240, h=67)
        original = h.I("display", "-p", "-t", "main", "#{pane_id}")
        original_pid = h.I("display", "-p", "-t", original, "#{pane_pid}")
        root_keys=h.I("list-keys", "-T", "root")
        self.assertTrue(any("MouseUp3Pane" in line and "mios-menu-pane-mouse" in line for line in root_keys.splitlines()), "runtime projection must carry the shared popup switch")
        command = shlex.join(["env", f"TMUX_PANE={original}", str(GEN), "tmux-os", "--root", str(ROOT), "app", "ok", "--client", client])
        h.I("run-shell", "-b", "-t", original, command)
        def floating():
            return [l for l in h.I("list-panes", "-t", "main", "-F", "#{pane_id}|#{pane_floating_flag}|#{pane_width}|#{pane_height}|#{pane_left}|#{pane_top}").splitlines() if "|1|" in l]
        self.assertTrue(wait(floating), "default app window becomes a persistent centered float")
        pane, flag, width, height, left, top = floating()[0].split("|")
        width, height, left, top = map(int, [width, height, left, top])
        ww, wh = map(int, h.I("display", "-p", "-t", original, "#{window_width}|#{window_height}").split("|"))
        self.assertEqual((width, height), (94, 33))
        self.assertEqual((left, top), ((ww-width)//2, (wh-height)//2))
        self.assertEqual(h.I("display", "-p", "-t", original, "#{pane_pid}"), original_pid)
        self.assertTrue(wait(lambda: h.log.exists() and "window" in h.log.read_text()))
        h.keys("view", "C-b", "Space")
        shot = h.shot(delay=1)
        borders = [(y, line.find("╭"), line.rfind("╮")) for y,line in enumerate(shot.splitlines()) if "╭" in line and "╮" in line]
        self.assertTrue(borders, shot)
        self.assertTrue(any(r-l+1 <=94 and l>0 and y>0 for y,l,r in borders), shot)
        h.keys("view", "Escape")
        self.assertTrue(wait(lambda: "╭" not in h.shot(delay=0.05)), "launcher closes before its parent float is removed")
        h.I("kill-pane", "-t", pane)
        h.I("select-pane", "-t", original)
        h.I("copy-mode", "-t", original)
        # The opening release must never choose whatever lies beneath the
        # pointer when a global centered menu appears.
        for x,y in [(10,2),(120,32),(230,60)]:
            h.raw("view", f"\x1b[<2;{x};{y}M")
            h.raw("view", f"\x1b[<2;{x};{y}m")
            menu_shot=h.shot(delay=0.4)
            self.assertIn("MiOS pane",menu_shot)
            title_row=next(line for line in menu_shot.splitlines() if "MiOS pane" in line)
            menu_width=title_row.rfind("╮")-title_row.find("╭")+1
            self.assertGreaterEqual(menu_width,54,"native menus retain physical landscape proportions")
            self.assertLessEqual(menu_width,60,"native menus inherit the existing MiOS menu launch width")
            self.assertIn(original, h.I("list-panes", "-t", "main", "-F", "#{pane_id}"))
            h.keys("view", "Escape")
            self.assertTrue(wait(lambda: "MiOS pane" not in h.shot(delay=0.05)), "wait for dismissal before starting the next independent click")
        self.assertEqual(h.I("display", "-p", "-t", original, "#{pane_pid}"), original_pid)
        h.I("send-keys", "-t", original, "-X", "cancel")
        direct = shlex.join(["env", f"TMUX_PANE={original}", str(GEN), "tmux-os", "--root", str(ROOT), "float", "new", "--client", client])
        h.I("run-shell", "-b", "-t", original, direct)
        self.assertTrue(wait(floating), "explicit new float uses the same centered launch policy")
        direct_pane, _, dw, dh, dx, dy = floating()[0].split("|")
        self.assertEqual(tuple(map(int,[dw,dh,dx,dy])), (94,33,(ww-94)//2,(wh-33)//2))
        self.assertEqual(h.I("display", "-p", "-t", original, "#{pane_pid}"), original_pid)
        h.I("kill-pane", "-t", direct_pane)
        h.O("new-window", "-t", "=v:", "-n", "entry", f"env -u TMUX -u TMUX_PANE {TERMINAL} --app ok --console 0; sleep 30")
        base = SSOT["terminal"]["sessions"]["new_session_name"]
        def outside_float():
            return [line for line in h.I("list-panes", "-t", f"={base}:", "-F", "#{pane_floating_flag}|#{pane_width}|#{pane_height}", check=False).splitlines() if line.startswith("1|")]
        self.assertTrue(wait(outside_float, timeout=20), "outside entry waits for its attaching client and then floats the app")
        self.assertEqual(outside_float()[0], "1|94|33")
        self.assertEqual(h.I("show", "-qv", "-t", f"={base}:", "@mios-bare"), "1")
        self.assertEqual(h.I("display", "-p", "-t", original, "#{pane_pid}"), original_pid)

    # ---- T1: generated tables, idempotent and shrink-safe reloads ----
    def test_t1_prefix_and_root_tables_match_the_ssot_and_reloads_are_idempotent(self):
        h = self.harness("t1")
        h.start()
        kb, tm = SSOT["keybindings"], SSOT["keybindings"]["tmux"]
        want = {a["key"] for a in kb["actions"]} | {b["key"] for b in tm["bindings"]}
        want |= {tm["menu_key"], tm["launcher_key"], tm["float"]["new_key"], tm["float"]["toggle_key"], tm["float"]["lift_key"], kb["agent"]["accept_key"], kb["tmux_prefix"]}
        self.assertEqual(prefix_keys(h.I("list-keys", "-T", "prefix")), want)
        root = h.I("list-keys", "-T", "root")
        self.assertIn("S-Enter", root)
        self.assertRegex(root, r"MouseDown3Pane\s+select-pane -t = \\; if-shell -F")
        aliases = lambda: len(re.findall(r'^command-alias\[\d+\] "?mios-menu', h.I("show", "-s", "command-alias"), re.M))
        counts = lambda: (aliases(), h.I("show", "-s", "terminal-features").count("extkeys"), len(h.I("show-hooks", "-g").splitlines()))
        first = counts()
        # Two variants per menu (desktop, console), two more for each right-click menu (pane, root), plus mios-menu.
        self.assertEqual(first[0], 2 * len(tm["menus"]) + 2 * 2 + 1)
        self.assertEqual(first[1], len(tm["extended_key_terms"]))
        for _ in range(2):
            h.I("source-file", str(h.conf))
        self.assertEqual(counts(), first)
        self.assertEqual(h.I("show", "-sv", "extended-keys"), "on")
        # Shrink: fewer menus, one extkeys term, mouse and floats off. Nothing of the bigger conf may remain.
        h.host.write_text(BASE_HOST + """
[keybindings]
mouse = false
[keybindings.tmux]
extended_key_terms = ["xterm*"]
menus = [
  { id = "root", title = "MiOS", items = [ { label = "Keys", key = "?" } ] },
  { id = "kill-pane", title = "Kill?", start = 1, items = [ { label = "Yes", key = "y", command = "kill-pane" }, { label = "No", key = "n", command = "" } ] },
  { id = "kill-window", title = "Kill?", start = 1, items = [ { label = "Yes", key = "y", command = "kill-window" }, { label = "No", key = "n", command = "" } ] },
]
[keybindings.tmux.float]
enabled = false
""")
        small = h.render(h.dir / "runtime-b")
        h.I("source-file", str(small))
        self.assertNotIn("mios-menu-pane", h.I("show", "-s", "command-alias"))
        self.assertEqual(aliases(), 7)
        self.assertEqual(h.I("show", "-s", "terminal-features").count("extkeys"), 1)
        self.assertNotRegex(h.I("list-keys", "-T", "root"), r"MouseDown3Pane\s+select-pane -t = \\; if-shell")
        self.assertNotRegex(h.I("show-hooks", "-g"), r"(?m)^session-closed\[")
        self.assertNotIn("*", prefix_keys(h.I("list-keys", "-T", "prefix")))

    # ---- T2: run-shell names its pane (the D1 root cause) ----
    def test_t2_every_run_shell_names_its_pane_and_the_stale_default_is_wrong(self):
        h = self.harness("t2")
        text = h.conf.read_text()
        lines = [l for l in text.splitlines() if "run-shell" in l]
        self.assertGreater(len(lines), 10)
        for l in lines:
            for chunk in l.split("run-shell")[1:]:
                if chunk.startswith(" -C "):
                    continue  # a tmux command run inside the server: no shell job, nothing inherits TMUX_PANE
                self.assertIn("TMUX_PANE=#{pane_id}", chunk, l[:160])
        menu = subprocess.run([str(GEN), "tmux-menu", "apps", "--rows", "60"], env=h.env, capture_output=True, timeout=10)
        self.assertEqual(menu.returncode, 0, menu.stderr)
        for arg in menu.stdout.split(b"\0"):
            if b"run-shell" in arg:
                self.assertIn(b"TMUX_PANE=#{pane_id}", arg)
        h.start()
        h.view()
        out_ok, out_stale = h.dir / "ok.txt", h.dir / "stale.txt"
        # Single quotes: tmux would expand $TMUX_PANE itself inside a double-quoted word.
        h.I("bind-key", "Z", f"run-shell -b 'TMUX_PANE=#{{pane_id}} sh -c \"echo \\$TMUX_PANE > {out_ok}\"'")
        h.I("bind-key", "Y", f"run-shell -b 'sh -c \"echo x\\$TMUX_PANE > {out_stale}\"'")
        second = h.I("split-window", "-P", "-F", "#{pane_id}", "-t", "main")
        h.keys("view", "C-b", "Z")
        h.keys("view", "C-b", "Y")
        self.assertEqual(wait(lambda: out_ok.is_file() and out_ok.read_text().strip()), second)
        self.assertNotEqual(wait(lambda: out_stale.is_file() and out_stale.read_text().strip()), "x" + second, "negative control: the job's own TMUX_PANE is not the pane")

    # ---- T3: menus by keyboard, controller vocabulary, mouse, console, size ----
    def test_t3_menus_are_keyboard_controller_mouse_and_console_driven(self):
        # The generated display-menus themselves: with the launcher on, prefix Space opens the launcher instead (T10).
        h = self.harness("t3", LAUNCHER_OFF)
        # 24 rows: the organised pane menu (section headers) is 19 rows tall, and tmux 3.7c only shows a menu
        # whose box fits on screen at the pointer.
        h.start(80, 24)
        c = h.view("view", w=80, h=24)
        h.keys("view", "C-b", "Space")
        shot = h.shot()
        self.assertIn("MiOS", shot)
        self.assertIn("Detach", shot)
        self.assertIn("╭", shot, "desktop client: rounded menu border")
        # Controller vocabulary (send-keys -K = keys as the client's own): Down Down Enter = New floating pane.
        h.I("send-keys", "-c", c, "-K", "Down", "Down", "Enter")
        self.assertTrue(wait(lambda: "1" in h.I("list-panes", "-t", "main", "-F", "#{pane_floating_flag}").split()))
        h.I("kill-pane", "-t", [l.split()[0] for l in h.I("list-panes", "-t", "main", "-F", "#{pane_id} #{pane_floating_flag}").splitlines() if l.endswith(" 1")][0])
        # Kill confirmation starts on No: Enter keeps the pane, Up Enter kills it.
        victim = h.I("split-window", "-d", "-P", "-F", "#{pane_id}", "-t", "main")
        h.I("select-pane", "-t", victim)
        h.keys("view", "C-b", "x")
        self.assertIn("Kill pane", h.shot())
        h.I("send-keys", "-c", c, "-K", "Enter")
        time.sleep(0.4)
        self.assertIn(victim, h.I("list-panes", "-t", "main", "-F", "#{pane_id}"))
        h.keys("view", "C-b", "x")
        h.shot(delay=0.4)
        h.I("send-keys", "-c", c, "-K", "Up", "Enter")
        self.assertTrue(wait(lambda: victim not in h.I("list-panes", "-t", "main", "-F", "#{pane_id}")))
        # Right-click in copy mode keeps the MiOS pane menu (stock rule), outside an app's mouse.
        h.I("copy-mode", "-t", "main")
        h.raw("view", "\x1b[<2;10;2M")
        h.raw("view", "\x1b[<2;10;2m")
        self.assertIn("MiOS pane", h.shot(), "a quick right-click (or touch long-press) leaves the menu open")
        self.assertIn("Go to top", h.shot(delay=0.1), "copy mode adds its own items")
        h.keys("view", "Escape")
        h.I("send-keys", "-t", "main", "-X", "cancel", check=False)
        # Linux console client: single borders, named colours, ASCII status; both status lines non-empty.
        vt = h.view("vt", term="linux", w=80, h=20)
        h.keys("vt", "C-b", "Space")
        vshot = h.shot("vt")
        self.assertIn("┌", vshot)
        self.assertNotIn("╭", vshot)
        h.keys("vt", "Escape")
        self.assertTrue(h.I("display", "-p", "-c", vt, "#{T:status-left}").strip())
        self.assertTrue(h.I("display", "-p", "-c", c, "#{T:status-left}").strip())
        self.assertNotIn("#[fg=#", h.I("display", "-p", "-c", vt, "#{T:status-left}"))
        self.assertEqual(h.I("display", "-p", "-c", vt, "#{E:@mios-console-client}"), "1")
        self.assertEqual(h.I("display", "-p", "-c", c, "#{E:@mios-console-client}"), "0")
        # A client too small for the menu gets the SSOT message instead of nothing.
        h.O("resize-window", "-t", "=v:view", "-x", "80", "-y", "10")
        time.sleep(0.4)
        h.keys("view", "C-b", "Space")
        self.assertIn("too small", h.shot())

    # ---- T4: float shell toggle, lift/dock, native float, reap ----
    def test_t4_float_shell_toggles_persists_lifts_docks_and_is_reaped(self):
        h = self.harness("t4")
        h.start()
        c = h.view()
        sid = h.I("display", "-p", "-t", "main", "#{session_id}").lstrip("$")
        name = f"mios-float-{sid}"
        h.keys("view", "C-b", "`")
        self.assertTrue(wait(lambda: h.I("has-session", "-t", f"={name}", check=False) == "" and name in h.I("list-sessions", "-F", "#{session_name}")))
        self.assertIn(" float ", h.shot(delay=1.0))
        self.assertEqual(h.I("show", "-qv", "-t", f"={name}:", "@mios-bare"), "1")
        h.raw("view", "echo FLOAT$((40+2))MARK")
        h.keys("view", "Enter")
        self.assertIn("FLOAT42MARK", h.shot())
        h.keys("view", "C-b", "`")
        self.assertNotIn(" float ", h.shot(delay=1.0))
        self.assertIn(name, h.I("list-sessions", "-F", "#{session_name}"), "hiding keeps the session")
        self.assertEqual(h.I("display", "-p", "-c", c, "#{client_session}"), "main")
        h.keys("view", "C-b", "`")
        self.assertIn("FLOAT42MARK", h.shot(delay=1.0))
        h.keys("view", "C-b", "`")
        h.shot(delay=0.8)
        # Lift a tiled pane into the float shell, then dock it back.
        lifted = h.I("split-window", "-P", "-F", "#{pane_id}", "-t", "main", "sleep 300")
        window = h.I("display", "-p", "-t", lifted, "#{window_id}")
        h.keys("view", "C-b", "F")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-t", lifted, "#{session_name}") == name))
        self.assertEqual(h.I("show", "-pqv", "-t", lifted, "@mios-float-origin"), window)
        self.assertEqual(h.I("display", "-p", "-t", window, "#{window_panes}"), "1")
        h.shot(delay=1.0)
        h.keys("view", "C-b", "F")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-t", lifted, "#{window_id}") == window))
        self.assertNotIn(" float ", h.shot(delay=1.0))
        # Native floating pane keeps the directory.
        # The docked pane may be the active one now: type into (and float from) the original shell pane.
        shell = next(l.split()[0] for l in h.I("list-panes", "-t", "main", "-F", "#{pane_id} #{pane_current_command}").splitlines() if l.split()[0] != lifted)
        h.I("select-pane", "-t", shell)
        h.I("send-keys", "-t", shell, "cd /usr/share", "Enter")
        time.sleep(0.4)
        h.keys("view", "C-b", "*")
        def float_path():
            rows = [l.split("\t") for l in h.I("list-panes", "-t", "main", "-F", "#{pane_floating_flag}\t#{pane_current_path}").splitlines()]
            return [p for f, p in rows if f == "1"]
        self.assertEqual(wait(float_path), ["/usr/share"])
        # A float shell dies with its parent session.
        h.I("new-session", "-d", "-s", "second", "bash --norc --noprofile")
        sid2 = h.I("display", "-p", "-t", "=second:", "#{session_id}")
        h.I("new-session", "-d", "-s", f"mios-float-{sid2.lstrip('$')}")
        h.I("set", "-t", f"=mios-float-{sid2.lstrip('$')}:", "@mios-float-parent", sid2)
        hook = h.I("show-hooks", "-g", "session-closed")
        self.assertIn("run-shell -C", hook)
        self.assertNotRegex(hook, r"run-shell -b|mios-terminal|python", "the reaper must not spawn a process per close")
        h.I("kill-session", "-t", "=second")
        self.assertTrue(wait(lambda: f"mios-float-{sid2.lstrip('$')}" not in h.I("list-sessions", "-F", "#{session_name}")))
        # Negative control: closing a session that has no float shell leaves every other session alone.
        h.I("new-session", "-d", "-s", "third", "sleep 300")
        before = set(h.I("list-sessions", "-F", "#{session_name}").split()) - {"third"}
        h.I("kill-session", "-t", "=third")
        time.sleep(0.4)
        self.assertEqual(set(h.I("list-sessions", "-F", "#{session_name}").split()), before)
        self.assertIn(name, h.I("list-sessions", "-F", "#{session_name}"), "a live parent keeps its float shell")

    # ---- T5: hidden sessions in pickers and cycling; choose-tree with no match returns ----
    def test_t5_hidden_sessions_are_never_listed_or_cycled_to(self):
        h = self.harness("t5")
        h.start()
        c = h.view()
        for s in ("mios-workspace-9", "mios-float-77", "zz-visible"):
            h.I("new-session", "-d", "-s", s, "sleep 300")
        h.keys("view", "C-b", "w")
        shot = h.shot()
        self.assertIn("main", shot)
        self.assertIn("zz-visible", shot)
        self.assertNotIn("mios-workspace-9", shot)
        self.assertNotIn("mios-float-77", shot)
        h.keys("view", "q")
        # Visible sessions in list order: main, zz-visible; hidden ones sort between and must be skipped.
        h.keys("view", "C-b", ")")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-c", c, "#{client_session}") == "zz-visible"))
        h.keys("view", "C-b", ")")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-c", c, "#{client_session}") == "main"))
        h.keys("view", "C-b", "(")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-c", c, "#{client_session}") == "zz-visible"))
        h.I("choose-tree", "-t", "main", "-f", "#{==:1,0}", check=False)
        self.assertIn("main", h.I("list-sessions", "-F", "#{session_name}"), "server still answers")

    # ---- T6: the SSOT launcher inside tmux ----
    def test_t6_apps_menu_shows_installed_rows_and_launches_by_mode(self):
        scoped = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--collect", "true"], capture_output=True, timeout=20).returncode == 0 if shutil.which("systemd-run") else False
        scope = '["systemd-run", "--user", "--scope", "--collect", "--quiet", "-p", "OOMPolicy=continue", "--"]' if scoped else '["env", "--"]'
        h = self.harness("t6", CATALOGUE.replace("{scope}", scope).replace("{tag}", secrets.token_hex(3)))
        h.start()
        c = h.view()
        h.keys("view", "C-b", "e")
        shot = h.shot(delay=0.8)
        self.assertIn("Fake OK", shot)
        self.assertNotIn("Fake Missing", shot)
        self.assertIn("MiOS AI", shot, "actions are listed first")
        h.keys("view", "Escape")
        pane = h.I("display", "-p", "-t", "main", "#{pane_id}")
        def run(*args):
            h.I("run-shell", "-b", "-t", pane, f"TMUX_PANE={pane} {TERMINAL} --app {' '.join(args)} --client {c}")
        run("ok", "--console", "0")
        self.assertTrue(wait(lambda: "Fake OK" in h.I("list-windows", "-t", "main", "-F", "#{window_name}")))
        run("vtrow", "--console", "1")
        self.assertTrue(wait(lambda: h.log.is_file() and "vt" in h.log.read_text().split()))
        run("novt", "--console", "1")
        time.sleep(0.6)
        self.assertNotIn("novt", h.log.read_text().split(), "vt_ok = false rows never run on a console")
        run("pop", "--console", "0")
        self.assertIn("Fake Popup", h.shot(delay=1.0))
        # The stub records its own cgroup when it starts (after systemd-run moved it), so there is no race.
        cglog = Path(f"{h.log}.cgroup")
        popup_cg = wait(lambda: cglog.is_file() and next((l.split(" ", 2)[2] for l in cglog.read_text().splitlines() if l.startswith("CG popup ")), None))
        self.assertTrue(popup_cg, "the popup TUI started")
        window_cg = next(l.split(" ", 2)[2] for l in cglog.read_text().splitlines() if l.startswith("CG window "))
        if scoped:
            server = h.I("display", "-p", "#{pid}")
            server_cg = Path(f"/proc/{server}/cgroup").read_text().strip()
            self.assertNotEqual(popup_cg, server_cg, "a popup TUI never runs in the tmux server's cgroup")
            self.assertTrue(popup_cg.endswith(".scope"), popup_cg)
        else:
            # Negative control without a user manager: the same stub in a window shares no scope with a popup.
            self.assertTrue(window_cg)
        h.keys("view", "Escape")

    # ---- T7: outside tmux (selector entry) through the test socket hook; D7 ----
    def test_t7_outside_entry_uses_only_the_test_socket_and_keeps_the_current_window(self):
        h = self.harness("t7", CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "t7"))
        bad = dict(h.env, MIOS_TERMINAL_TEST_SOCKET=OPERATOR_SOCKET)
        self.assertEqual(subprocess.run([str(TERMINAL), "--app", "ok"], env=bad, capture_output=True, timeout=10).returncode, 64)
        loose = h.dir / "loose"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        bad = dict(h.env, MIOS_TERMINAL_TEST_SOCKET_DIR=str(loose))
        self.assertEqual(subprocess.run([str(TERMINAL), "--app", "ok"], env=bad, capture_output=True, timeout=10).returncode, 64)
        bad = dict(h.env, MIOS_TERMINAL_TEST_SOCKET_DIR="/tmp")
        self.assertEqual(subprocess.run([str(TERMINAL), "--app", "ok"], env=bad, capture_output=True, timeout=10).returncode, 64)
        h.O("new-session", "-d", "-s", "v", "-n", "entry", "-x", "100", "-y", "30", f"env -u TMUX -u TMUX_PANE {TERMINAL} --app ok --console 0; sleep 30")
        base = SSOT["terminal"]["sessions"]["new_session_name"]
        self.assertTrue(wait(lambda: "Fake OK" in h.I("list-windows", "-t", f"={base}", "-F", "#{window_name}", check=False), timeout=20))
        self.assertEqual(h.I("show", "-qv", "-t", f"={base}:", "@mios-bare"), "1")
        self.assertTrue(wait(lambda: h.clients()))
        sock = h.I("display", "-p", "#{socket_path}")
        self.assertTrue(sock.startswith(str(h.dir)), sock)
        # D7: a second entry attaching to an existing session leaves its current window alone.
        h.I("new-window", "-d", "-t", f"={base}:", "-n", "third", "sleep 300")
        h.I("select-window", "-t", f"={base}:third")
        h.O("new-window", "-t", "=v:", "-n", "again", f"env -u TMUX -u TMUX_PANE {TERMINAL} --session {base}; sleep 30")
        wait(lambda: len(h.clients()) >= 2, timeout=20)
        time.sleep(1.0)
        self.assertEqual(h.I("display", "-p", "-t", f"={base}:", "#{window_name}"), "third")

    # ---- T8: workspace layout never wrecks a floating pane ----
    @unittest.skipUnless(RELAY.is_file(), "the lane's built mios-agent-relay is required")
    def test_t8_workspace_resize_keeps_floating_panes_floating(self):
        h = self.harness("t8", f'\n[mcp.agents]\nbinary = "{RELAY}"\n')
        h.start()
        # Negative control: a plain layout preset turns a float into a tiled pane.
        h.I("split-window", "-d", "-t", "main", "sleep 300")
        f0 = h.I("new-pane", "-d", "-P", "-F", "#{pane_id}", "-t", "main", "-x", "30", "-y", "8", "-X", "2", "-Y", "2", "sleep 300")
        h.I("select-layout", "-t", "main", "tiled")
        self.assertEqual(h.I("display", "-p", "-t", f0, "#{pane_floating_flag}"), "0")
        saved = {k: os.environ.get(k) for k in ("MIOS_TOML", "MIOS_VENDOR_TOML", "MIOS_HOST_TOML", "MIOS_USER_TOML", "MIOS_RESOLVER_NATIVE", "TMUX", "TMUX_PANE")}
        try:
            os.environ.update({"MIOS_TOML": str(VENDOR), "MIOS_VENDOR_TOML": str(VENDOR), "MIOS_HOST_TOML": str(h.host), "MIOS_USER_TOML": h.env["MIOS_USER_TOML"], "MIOS_RESOLVER_NATIVE": "0"})
            os.environ.pop("TMUX", None)
            os.environ.pop("TMUX_PANE", None)
            loader = importlib.machinery.SourceFileLoader("mios_mcp_tmuxos", str(ROOT / "usr/libexec/mios/mios-mcp-server"))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            mcp = importlib.util.module_from_spec(spec)
            loader.exec_module(mcp)
            sock = h.I("display", "-p", "#{socket_path}")
            session = f"ws-{secrets.token_hex(3)}"
            res = mcp._workspace_call("open", socket=sock, session=session, directory="/tmp", latch=f"mios-latch-{secrets.token_hex(4)}",
                                      command="sleep 3600", observer_command="sleep 3600", adapter=f"{sys.executable} {ROOT / 'usr/libexec/mios/mios-mcp-server'}")
            head = res["head"]
            h.I("resize-window", "-t", head, "-x", "200", "-y", "60")
            fl = h.I("new-pane", "-d", "-P", "-F", "#{pane_id}", "-t", head, "-x", "40", "-y", "10", "-X", "5", "-Y", "5", "sleep 300")
            mcp._workspace_call("resize", socket=sock, session=session, head=head, adapter=f"{sys.executable} {ROOT / 'usr/libexec/mios/mios-mcp-server'}", observer_command="sleep 3600")
            self.assertEqual(h.I("display", "-p", "-t", fl, "#{pane_floating_flag}"), "1", "the relay must not lay out over a float")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # ---- T9: a pane shell refreshes only the human server, only on a digest change ----
    def test_t9_pane_shell_reload_is_limited_to_the_human_socket_and_a_changed_digest(self):
        h = self.harness("t9")
        h.start()
        text = (ROOT / "etc/profile.d/50-mios.sh").read_text()
        a = text.index("# Render on shell startup")
        b = text.index("    unset _mios_projection\nfi\n", a) + len("    unset _mios_projection\nfi\n")
        snippet = text[a:b].replace("/usr/libexec/mios/mios-gen", str(GEN)).replace("[ -x /usr/libexec/mios/mios-unit-gen ] && ", "")
        script = h.dir / "snippet.sh"
        script.write_text("_mios_profile() {\n" + snippet + "}\n_mios_profile\n")
        def run_snippet():
            # The profile renders into $XDG_RUNTIME_DIR/mios-terminal: point it at the private directory.
            env = dict(h.env, TMUX=h.I("display", "-p", "#{socket_path},#{pid},#{session_id}"), XDG_RUNTIME_DIR=str(h.dir / "run"))
            env.pop("MIOS_OMP_THEME", None)
            r = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
        canary = lambda: "mios-canary" in h.I("list-keys", "-T", "prefix")
        h.I("bind-key", "Z", "display-message mios-canary")
        run_snippet()
        self.assertTrue(canary(), "a server that is not the human socket is never re-sourced")
        # Positive control: make this server the human socket in the test SSOT; a digest change re-sources it.
        h.host.write_text(BASE_HOST + f'\n[keybindings]\nsocket_name = "{h.inner}"\n[terminal]\nsocket_root = "{h.dir}"\n')
        run_snippet()
        self.assertTrue(canary(), "the human server whose loaded digest matches this render is not re-sourced")
        h.I("set", "-g", "@mios-conf-digest", "stale")
        run_snippet()
        self.assertFalse(canary(), "the human server with a stale digest is re-sourced")
        self.assertTrue(h.I("show", "-gqv", "@mios-conf-digest"))
        h.I("bind-key", "Z", "display-message mios-canary")
        run_snippet()
        self.assertTrue(canary(), "an unchanged digest is never re-sourced")

    def test_t3b_right_click_menus_open_wherever_they_fit(self):
        # tmux 3.7c never moves a menu back on screen: a right-click menu placed with its top at the pointer
        # (-y M) is silently dropped when it would run off the bottom. The generated right-click menus open above
        # the pointer there. Negative control: the pre-fix projection (-y M) shows nothing for the low click.
        h = self.harness("t3b", LAUNCHER_OFF)
        h.start(80, 30)
        h.view("view", w=80, h=30)
        rows = {}
        for y in (2, 15, 26):
            h.raw("view", f"\x1b[<2;10;{y}M")
            shot = h.shot(delay=0.4)
            rows[y] = next((n for n, l in enumerate(shot.splitlines()) if "MiOS pane" in l), None)
            h.raw("view", f"\x1b[<2;0;{y}m")
            h.keys("view", "Escape")

            time.sleep(0.3)
        self.assertEqual(rows[2], 1, f"a high click opens the menu below the pointer: {rows}")
        self.assertIsNotNone(rows[15], f"a dead-zone click centers the menu: {rows}")
        self.assertIsNotNone(rows[26], f"a low click opens the menu above the pointer: {rows}")
        self.assertLess(rows[26], 26 - 1, "above the pointer, so the release lands outside the items")


    # ---- T10: the tmux-os launcher (miosd launcher): organised popup, keyboard/controller/mouse, instance options ----
    @unittest.skipUnless(MIOSD.is_file(), "the lane's built miosd is required")
    def test_t10_launcher_is_organised_and_driven_by_keys_controller_and_mouse(self):
        h = self.harness("t10", CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "t10"))
        h.start(120, 36)
        h.I("new-window", "-d", "-t", "main:", "-n", "editor", "sleep 300")
        h.I("split-window", "-d", "-t", "main:editor", "sleep 300")
        c = h.view(w=120, h=36)
        def opened(delay=1.0):
            h.keys("view", "C-b", "Space")
            return h.shot(delay=delay)
        def row(shot, text):
            # Rows of the popup only: the status line names the current window too.
            for y, line in enumerate(shot.splitlines()):
                x = line.find(text)
                if x >= 0 and "│" in line[:x]:
                    return x, y
            raise AssertionError(f"{text!r} not on screen:\n{shot}")
        def click(x, y, button=0):
            h.raw("view", f"\x1b[<{button};{x + 1};{y + 1}M")
            h.raw("view", f"\x1b[<{button};{x + 1};{y + 1}m")
        shot = opened()
        # Organised: grouped sections under headers, two columns at this width, a centred popup.
        for header in ("Running", "AI and agents", "Tmux-OS", "Pane", "Window", "Session"):
            self.assertRegex(shot, rf"{re.escape(header)} ─{{5,}}", f"section header {header}")
        self.assertTrue(any(re.search(r"\S ─{5,}.*\S ─{5,}", l) for l in shot.splitlines()), f"two columns of sections:\n{shot}")
        self.assertIn("Fake OK", shot, "installed apps are listed")
        self.assertNotIn("Fake Missing", shot, "missing apps are not")
        top = next(l for l in shot.splitlines() if "╭" in l)
        # capture-pane drops trailing blanks, so the right margin is measured against the client width (120).
        left, right = top.index("╭"), 120 - 1 - top.rindex("╮")
        self.assertLessEqual(abs(left - right), 2, f"centred popup: {left} vs {right}")
        # Controller vocabulary (the nav projection = keys sent as the client's own): d-pad Down, A = Enter.
        self.assertEqual(h.I("display", "-p", "-c", c, "#{window_name}"), "bash")
        # The nav projection types d-pad and stick moves on the virtual keyboard (key:DOWN), so they are typed
        # into the terminal here too: tmux 3.7c drops cursor keys that send-keys -K aims at a popup (probed:
        # Down and Up never reach the popup's tty, Enter and letters do).
        h.keys("view", "Down")
        h.I("send-keys", "-c", c, "-K", "Enter")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-c", c, "#{window_name}") == "editor"), "A on a running instance goes to it")
        self.assertNotIn("Running ─", h.shot(delay=0.3), "the popup closed")
        # B backs out: Esc closes the launcher, nothing changes.
        opened()
        h.I("send-keys", "-c", c, "-K", "Escape")
        self.assertNotIn("Running ─", h.shot(delay=0.4))
        # Right-click on an instance opens its options; Float the pane (left click on the row) floats the window's pane.
        shot = opened()
        x, y = row(shot, "editor")
        click(x, y, button=2)
        shot = h.shot(delay=0.6)
        for label in ("Options", "Pop out to its own window", "Float the pane", "Move to window...", "Move to session...", "Close..."):
            self.assertIn(label, shot, f"instance option {label}")
        x, y = row(shot, "Float the pane")
        click(x, y)
        # Float the pane is prefix F: the pane is lifted into the session's float shell (its own floating popup,
        # [keybindings.tmux.float]); it docks back from there.
        fprefix = SSOT["keybindings"]["tmux"]["float"]["session_prefix"]
        floated = lambda: any(l.startswith(fprefix) for l in h.I("list-panes", "-a", "-F", "#{session_name} #{window_name}").splitlines() if l.endswith(" sleep"))
        self.assertTrue(wait(floated), "floated: " + h.I("list-panes", "-a", "-F", "#{session_name}:#{window_name} #{pane_id}"))
        h.shot(delay=0.6)
        h.keys("view", "C-b", "`")                   # hide the float shell again (prefix `)
        self.assertTrue(wait(lambda: "─ float ─" not in h.shot(delay=0.2)), "float shell hidden")
        # Pop out (no desktop here): the instance moves into its own session pop-editor.
        opened()
        h.I("send-keys", "-c", c, "-K", "Tab")
        shot = h.shot(delay=0.5)
        x, y = row(shot, "Pop out to its own window")
        click(x, y)
        self.assertTrue(wait(lambda: "pop-" in h.I("list-sessions", "-F", "#{session_name}")), "popped out into its own session")
        # Close asks first and starts on No: A keeps it, Down A (Yes is below No) closes it.
        before = h.I("list-panes", "-a", "-F", "#{pane_id}").split()
        opened()
        h.keys("view", "Tab", "End", "Enter")
        self.assertIn("Close it?", h.shot(delay=0.5))
        h.I("send-keys", "-c", c, "-K", "Enter")
        time.sleep(0.5)
        self.assertEqual(h.I("list-panes", "-a", "-F", "#{pane_id}").split(), before, "No keeps the instance")
        # Two separate presses: one write of ESC ESC reads as a single Alt+Esc.
        for _ in range(2):
            h.I("send-keys", "-c", c, "-K", "Escape")
            time.sleep(0.3)
        self.assertTrue(wait(lambda: "Running ─" not in h.shot(delay=0.2)), "B twice backs out of options and the launcher")
        time.sleep(0.5)
        self.assertIn("Running ─", opened())
        h.keys("view", "Tab", "End", "Enter")
        self.assertIn("Close it?", h.shot(delay=0.5))
        h.keys("view", "Down", "Enter")
        self.assertTrue(wait(lambda: len(h.I("list-panes", "-a", "-F", "#{pane_id}").split()) < len(before)), "Yes closes it")

    def test_t10_negative_control_launcher_off_is_the_single_column_menu(self):
        # The pre-launcher entry: prefix Space opens the one-column display-menu, with none of the launcher's sections.
        h = self.harness("t10n", LAUNCHER_OFF)
        h.start(120, 36)
        h.view(w=120, h=36)
        h.keys("view", "C-b", "Space")
        shot = h.shot(delay=0.8)
        self.assertIn("Detach", shot)
        self.assertNotRegex(shot, r"Running ─{5,}")
        self.assertFalse(any(re.search(r"\S ─{5,}.*\S ─{5,}", l) for l in shot.splitlines()))


    @unittest.skipUnless(MIOSD.is_file(), "the candidate native launcher is required")
    def test_t12_launcher_find_keeps_typing_literal_and_opens_the_filtered_app(self):
        extra = CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "find")
        h = Harness("find", extra, global_popups=True)
        self.addCleanup(h.close)
        h.start(120, 36)
        h.view(w=120, h=36)
        h.keys("view", "C-b", "Space")
        self.assertTrue(wait(lambda: "/ or Ctrl+F" in h.shot(delay=0.1)))
        h.keys("view", "C-f")
        h.raw("view", "q 2")
        shot = h.shot()
        self.assertIn("Find: q 2", shot, "q, spaces and row shortcuts remain literal search input")
        self.assertIn("No available matches", shot)
        self.assertFalse(h.log.exists(), "typing must not launch an app")
        h.keys("view", "C-u")
        h.raw("view", "fake ok")
        self.assertTrue(wait(lambda: "Fake OK" in h.shot(delay=0.1)))
        self.assertNotIn("Fake Popup", h.shot(delay=0.1))
        h.keys("view", "Tab")
        self.assertTrue(wait(lambda: "Options: Fake OK" in h.shot(delay=0.1)))
        self.assertIn("Esc back", h.shot(delay=0.1))
        h.keys("view", "Escape")
        self.assertTrue(wait(lambda: "Options: Fake OK" not in h.shot(delay=0.1)))
        self.assertIn("Find: fake ok", h.shot(delay=0.1), "Back returns to the same search")
        h.keys("view", "Enter")
        self.assertTrue(wait(lambda: h.log.exists() and "window" in h.log.read_text().splitlines()))
        self.assertNotIn("popup", h.log.read_text(), "the original filtered app target must be used")

    @unittest.skipUnless(MIOSD.is_file(), "the candidate native launcher is required")
    def test_t13_console_find_empty_results_cancel_and_mouse_match(self):
        extra = CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "find-vt")
        h = Harness("find-vt", extra, global_popups=True)
        self.addCleanup(h.close)
        h.start(80, 30)
        h.view(term="vt220", w=80, h=30)
        h.keys("view", "C-b", "Space")
        self.assertTrue(wait(lambda: "/ or Ctrl+F" in h.shot(delay=0.1)))
        h.raw("view", "/")
        h.raw("view", "zzzz-missing")
        self.assertIn("No available matches", h.shot())
        h.keys("view", "Enter")
        self.assertIn("Find: zzzz-missing", h.shot(), "empty results stay open and cannot activate")
        h.keys("view", "Escape")
        self.assertTrue(wait(lambda: "/ or Ctrl+F" in h.shot(delay=0.1)))
        self.assertFalse(h.log.exists())
        h.raw("view", "/fake ok")
        shot = h.shot()
        self.assertIn("Fake OK", shot)
        self.assertNotIn("Fake Popup", shot)
        row = next((y, line.index("Fake OK")) for y, line in enumerate(shot.splitlines()) if "Fake OK" in line)
        x, y = row[1] + 2, row[0]
        h.raw("view", f"\x1b[<0;{x + 1};{y + 1}M")
        h.raw("view", f"\x1b[<0;{x + 1};{y + 1}m")
        self.assertTrue(wait(lambda: h.log.exists() and "window" in h.log.read_text().splitlines()), "mouse activates exactly the visible filtered row")

    @unittest.skipUnless(DESKTOP_TMUX is not None and MIOSD.is_file(), "the patched native desktop backend is required")
    def test_t14_desktop_windows_preserve_apps_across_geometry_minimize_and_mouse_controls(self):
        extra = CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "desktop")
        extra += f'\n[keybindings.tmux.desktop]\nbinary={str(DESKTOP_TMUX)!r}\n'
        h = Harness("desktop", extra, global_popups=True)
        self.addCleanup(h.close)
        h.start(200, 60)
        client = h.view(w=200, h=60)
        canvas = h.I("display", "-p", "-t", "main", "#{pane_id}")
        h.I("set-option", "-w", "-t", canvas, "@mios-desktop-enabled", "1")
        h.I("set-option", "-p", "-t", canvas, "@mios-desktop-canvas", "1")
        h.I("set-option", "-w", "-t", canvas, "pane-scrollbars", "off")
        h.I("set-option", "-w", "-t", canvas, "pane-border-status", "top")
        h.I("set-option", "-w", "-t", canvas, "pane-border-format", h.I("show-options", "-gqv", "@mios-desktop-border-format"))
        h.I("set-option", "-t", "main", "status", "2")
        h.I("set-option", "-t", "main", "status-position", "bottom")
        h.I("set-option", "-t", "main", "status-format[1]", h.I("show-options", "-gqv", "@mios-desktop-dock-format"))
        app = h.I("new-pane", "-P", "-F", "#{pane_id}", "-t", canvas, "-x", "60", "-y", "20", "-X", "20", "-Y", "10", "--", "fake-tui", "desktop")
        self.assertTrue(wait(lambda: h.log.exists()))
        socket = h.I("display", "-p", "#{socket_path}")
        def identity():
            return h.I("display", "-p", "-t", app, "#{pane_id}|#{pane_pid}|#{pane_tty}")
        witnessed = identity()
        def rect():
            return tuple(map(int, h.I("display", "-p", "-t", app, "#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}").split("|")))
        def engine(verb, *args, check=True):
            command = [str(GEN), "tmux-os", "--root", str(ROOT), "desktop", verb, "--socket", socket, "--client", client, "--pane", app, *args]
            result = subprocess.run(command, env=h.env, capture_output=True, text=True, timeout=8)
            if check:
                self.assertEqual(result.returncode, 0, result.stderr)
            return result
        background = h.I("display", "-p", "-t", canvas, "#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}")
        engine("center")
        self.assertEqual(rect()[2:], (94, 33))
        self.assertEqual(identity(), witnessed)
        engine("snap-left")
        left = rect()
        engine("snap-right")
        right = rect()
        self.assertEqual(left[0] + left[2], right[0])
        self.assertEqual(h.I("display", "-p", "-t", canvas, "#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}"), background)
        engine("maximize")
        self.assertEqual(h.I("display", "-p", "-t", app, "#{window_zoomed_flag}"), "1")
        engine("restore")
        self.assertEqual(rect(), right)
        engine("tile")
        self.assertEqual(h.I("display", "-p", "-t", app, "#{pane_floating_flag}"), "0")
        engine("float")
        self.assertEqual(h.I("display", "-p", "-t", app, "#{pane_floating_flag}"), "1")
        self.assertEqual(identity(), witnessed)
        engine("minimize")
        self.assertTrue(h.I("display", "-p", "-t", app, "#{session_name}").startswith("mios-float-"))
        dock = __import__("json").loads(engine("dock-state").stdout)
        self.assertTrue(any(row["pane"] == app and row["minimized"] for row in dock))
        engine("focus")
        self.assertEqual(h.I("display", "-p", "-t", app, "#{session_name}"), "main")
        self.assertEqual(identity(), witnessed)
        h.I("set-option", "-p", "-t", app, "@mios-mcp-owner", "private-proof")
        before = rect()
        self.assertNotEqual(engine("snap-left", check=False).returncode, 0)
        self.assertEqual(rect(), before)
        h.I("set-option", "-p", "-u", "-t", app, "@mios-mcp-owner")
        engine("center")
        x,y,w,height = rect()
        # A native border drag moves the existing window, without helpers per motion.
        h.raw("view", f"\x1b[<0;{x+20};{y}M\x1b[<32;{x+25};{y+2}M\x1b[<0;{x+25};{y+2}m")
        self.assertTrue(wait(lambda: rect()[:2] != (x,y)))
        self.assertEqual(identity(), witnessed)
        x,y,w,height = rect()
        # Left title controls start at body x+2: [x][_][+]; click the middle minimize cell.
        click_x, click_y = x+7, y
        h.raw("view", f"\x1b[<0;{click_x};{click_y}M\x1b[<0;{click_x};{click_y}m")
        self.assertTrue(wait(lambda: h.I("display", "-p", "-t", app, "#{session_name}").startswith("mios-float-")), h.shot())
        engine("focus")
        self.assertEqual(identity(), witnessed)
        # The Apps control opens the same searchable launcher as the keyboard.
        command = shlex.join([str(GEN), "tmux-os", "--root", str(ROOT), "desktop", "dock-click", "--socket", socket, "--client", client, "--item", "apps"])
        h.I("run-shell", "-b", "-t", canvas, command)
        self.assertTrue(wait(lambda: "/ or Ctrl+F" in h.shot(delay=.1)))
        h.raw("view", "/fake ok")
        self.assertTrue(wait(lambda: "Fake OK" in h.shot(delay=.1)))
        h.keys("view", "Escape", "Escape")
        self.assertEqual(identity(), witnessed)

    @unittest.skipUnless(DESKTOP_TMUX is not None, "the explicitly selected native desktop backend is required")
    def test_t20_machine_rpcs_preserve_c_locale_records_and_text_delimiters(self):
        import json
        self.assertRegex(DESKTOP_TMUX_SHA256, r"\A[a-f0-9]{64}\Z")
        self.assertEqual(hashlib.sha256(DESKTOP_TMUX.resolve(strict=True).read_bytes()).hexdigest(),
                         DESKTOP_TMUX_SHA256)
        generator_sha = os.environ.get("MIOS_TMUX_OS_GEN_SHA256", "")
        self.assertRegex(generator_sha, r"\A[a-f0-9]{64}\Z", "the actual candidate generator must be pinned")
        self.assertEqual(hashlib.sha256(GEN.read_bytes()).hexdigest(), generator_sha)
        extra = f'\n[keybindings.tmux.desktop]\nbinary={str(DESKTOP_TMUX)!r}\n'
        h = Harness("machine-c-locale", extra, global_popups=True)
        self.addCleanup(h.close)
        h.start(157, 41)
        client = h.view(w=157, h=41)
        canvas = h.I("display-message", "-p", "-t", "main", "#{pane_id}")
        h.I("set-option", "-w", "-t", canvas, "@mios-desktop-enabled", "1")
        h.I("set-option", "-p", "-t", canvas, "@mios-desktop-canvas", "1")
        app = h.I("split-window", "-d", "-P", "-F", "#{pane_id}", "-t", canvas, "sleep 30")
        title = "MiOS \u03c6 | window"
        h.I("select-pane", "-t", app, "-T", title)
        identity = h.I("display-message", "-p", "-t", app, "#{pane_id}|#{pane_pid}|#{pane_tty}")
        socket = h.I("display-message", "-p", "#{socket_path}")
        env = dict(h.env, LANG="C", LC_ALL="C", TERM="xterm-256color")
        dock = subprocess.run([str(GEN), "tmux-os", "--root", str(ROOT), "desktop", "dock-state",
                               "--socket", socket, "--client", client, "--format", "json"],
                              env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(dock.returncode, 0, dock.stderr)
        rows = json.loads(dock.stdout)
        actual = next(row for row in rows if row["pane"] == app)
        self.assertEqual(actual["title"], title)
        self.assertEqual(str(actual["pid"]), identity.split("|")[1])
        # The second native RPC constructor must parse its real session records
        # without mistaking a printable delimiter in text for another field.
        prefix = SSOT["keybindings"]["tmux"]["float"]["session_prefix"]
        live, orphan = prefix + "live|c", prefix + "orphan|c"
        parent = h.I("display-message", "-p", "-t", canvas, "#{session_id}")
        for name, origin in ((live, parent), (orphan, "$999999999")):
            h.I("new-session", "-d", "-s", name, "sleep 30")
            h.I("set-option", "-t", "=" + name + ":", "@mios-float-parent", origin)
        env["TMUX"] = f'{socket},{h.I("display-message", "-p", "#{pid}")},0'
        env["TMUX_PANE"] = canvas
        reaped = subprocess.run([str(GEN), "tmux-os", "--root", str(ROOT), "float", "reap"],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(reaped.returncode, 0, reaped.stderr)
        self.assertEqual(h.I("display-message", "-p", "-t", "=" + live + ":", "#{session_name}"), live)
        missing = subprocess.run(["tmux", "-L", h.inner, "has-session", "-t", "=" + orphan],
                                 env=h.env, capture_output=True, timeout=5)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(h.I("display-message", "-p", "-t", app, "#{pane_id}|#{pane_pid}|#{pane_tty}"),
                         identity)

    @unittest.skipUnless(DESKTOP_TMUX is not None and MIOSD.is_file(), "the native desktop opener is required")
    def test_t15_canonical_bootstrap_uses_vendor_shell_for_an_older_operator_catalogue(self):
        # Exercise the public opener itself: T14's manual pane setup cannot
        # detect startup-shell failures before the owned server is created.
        extra = CATALOGUE.replace("{scope}", '["env", "--"]').replace("{tag}", "old-catalogue")
        h = Harness("canonical-startup", extra, global_popups=True)
        self.addCleanup(h.close)
        private_root = h.dir / "root"
        vendor = private_root / "usr/share/mios/mios.toml"
        vendor.parent.mkdir(parents=True)
        vendor.write_text(VENDOR.read_text())
        h.host.write_text(h.host.read_text() + f'''\n[terminal]
socket_root = {str(h.dir)!r}
[keybindings.tmux.desktop]
binary = {str(DESKTOP_TMUX)!r}
socket_name = {h.inner!r}
session_name = "Canonical-Desktop"
[keybindings.tmux.desktop.panel]
enabled = false
[keybindings.tmux.desktop.sidebar]
enabled = false
''')
        before = h.host.read_text()
        self.assertNotIn('id = "terminal-shell"', before)
        env = dict(h.env, MIOS_VENDOR_TOML=str(vendor), XDG_RUNTIME_DIR=str(h.dir / "run"))
        command = [str(MIOSD), "desktop", "--root", str(private_root), "--no-attach"]
        def bootstrap():
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return __import__("json").loads(result.stdout)
        first = bootstrap()
        identity = h.I("display", "-p", "-t", first["canvas"], "#{pane_id}|#{pane_pid}|#{pane_tty}")
        expected = next(row["argv"] for row in SSOT["terminal"]["tuis"] if row["id"] == "terminal-shell")
        self.assertEqual(shlex.split(h.I("show-options", "-v", "-t", first["session"], "default-command")), expected)
        self.assertEqual(h.host.read_text(), before, "the operator application list must not be replaced")
        self.assertEqual(bootstrap(), first, "repeat startup reuses the witnessed server and canvas")
        self.assertEqual(h.I("display", "-p", "-t", first["canvas"], "#{pane_id}|#{pane_pid}|#{pane_tty}"), identity)
        h.host.write_text(before + '''\n[[terminal.tuis]]
id = "terminal-shell"
label = "Invalid explicit shell"
section = "system"
mode = "float"
argv = []
''')
        invalid = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(invalid.returncode, 0, "a malformed explicit entry must not fall back")
        self.assertEqual(h.I("display", "-p", "-t", first["canvas"], "#{pane_id}|#{pane_pid}|#{pane_tty}"), identity)

    @unittest.skipUnless(DESKTOP_TMUX is not None and MIOSD.is_file(), "the native desktop opener is required")
    def test_t16_canonical_top_panel_survives_resizes_with_one_collector_and_a_blank_canvas(self):
        h = Harness("canonical-panel", global_popups=True)
        self.addCleanup(h.close)
        private_root = h.dir / "root"
        vendor = private_root / "usr/share/mios/mios.toml"
        vendor.parent.mkdir(parents=True)
        vendor.write_text(VENDOR.read_text())
        h.host.write_text(h.host.read_text() + f'''\n[terminal]
socket_root = {str(h.dir)!r}
[keybindings.tmux.desktop]
binary = {str(DESKTOP_TMUX)!r}
socket_name = {h.inner!r}
session_name = "Canonical-Panel"
[keybindings.tmux.desktop.panel]
enabled = true
argv = [{str(h.dir / "bin/fake-tui")!r}, "desktop-panel"]
[keybindings.tmux.desktop.sidebar]
enabled = false
''')
        env = dict(h.env, MIOS_VENDOR_TOML=str(vendor), XDG_RUNTIME_DIR=str(h.dir / "run"))
        command = [str(MIOSD), "desktop", "--root", str(private_root), "--no-attach"]
        def bootstrap():
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return __import__("json").loads(result.stdout)
        first = bootstrap()
        panel = h.I("show-options", "-wqv", "-t", first["window"], "@mios-desktop-panel-pane")
        self.assertEqual(h.I("show-options", "-sqv", "focus-events"), "on", "the owned desktop must forward application focus loss immediately")
        self.assertRegex(panel, r"^%[0-9]+$")
        self.assertEqual(h.I("show-options", "-wqv", "-t", first["window"], "@mios-desktop-workarea-pane"), first["canvas"])
        identity_format = "#{pane_id}|#{pane_pid}|#{pane_tty}"
        identities = {pane: h.I("display", "-p", "-t", pane, identity_format) for pane in (panel, first["canvas"])}
        app = h.I("new-pane", "-d", "-P", "-F", "#{pane_id}", "-t", first["canvas"], "-x", "25", "-y", "8", "-X", "10", "-Y", "20", "sleep 300")
        geometry = "#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}|#{pane_floating_flag}"
        app_identity = h.I("display", "-p", "-t", app, geometry)
        h.view(session="Canonical-Panel", w=120, h=40)
        for columns, rows in [(120, 40), (90, 30), (80, 24), (120, 40)]:
            h.O("resize-window", "-t", "=v:", "-x", str(columns), "-y", str(rows))
            self.assertTrue(wait(lambda: h.I("display", "-p", "-t", panel, "#{pane_height}") == h.I("display", "-p", "-t", panel, "#{E:@mios-desktop-panel-height}")), "native post-layout hook must fit the current viewport")
            memo = h.I("show-options", "-wqv", "-t", first["window"], "@mios-desktop-panel-viewport")
            self.assertEqual(memo, h.I("display", "-p", "-t", panel, "#{pane_width}x#{window_height}"))
        h.I("set-option", "-s", "focus-events", "off")
        self.assertEqual(bootstrap(), first)
        self.assertEqual(h.I("show-options", "-sqv", "focus-events"), "on", "repeat open restores focus forwarding without replacing applications")
        self.assertEqual({pane: h.I("display", "-p", "-t", pane, identity_format) for pane in identities}, identities)
        self.assertEqual(h.I("display", "-p", "-t", app, geometry), app_identity)
        self.assertTrue(wait(lambda: h.log.exists()))
        self.assertEqual(h.log.read_text().splitlines().count("desktop-panel"), 1, "resizes and repeated open must reuse the panel collector")
        label = SSOT["keybindings"]["tmux"]["desktop"]["labels"]["topbar"]
        header = h.I("display", "-p", "-t", panel, "#{E:pane-border-format}")
        self.assertIn(label, header, "native topbar must retain literal menu text")
        self.assertNotIn("#{", header, "all dynamic padding formats must expand")
        self.assertTrue(wait(lambda: not h.I("capture-pane", "-p", "-t", first["canvas"]).strip()), "the lower canvas must remain blank")

    @unittest.skipUnless(DESKTOP_TMUX is not None and MIOSD.is_file(), "the native desktop opener is required")
    def test_t17_canonical_desktop_roles_rotate_without_restarting_apps_or_covering_compact_chat(self):
        h = Harness("canonical-sidebar", global_popups=True)
        self.addCleanup(h.close)
        private_root = h.dir / "root"
        vendor = private_root / "usr/share/mios/mios.toml"
        vendor.parent.mkdir(parents=True)
        vendor.write_text(VENDOR.read_text())
        stub = str(h.dir / "bin/fake-tui")
        metrics_command = shlex.join([stub, "desktop-metrics"])
        h.host.write_text(h.host.read_text().replace("[mcp.tmux.workspace]\n", f"[mcp.tmux.workspace]\nresource_command = {metrics_command!r}\n") + f'''\n[mcp]
python = {stub!r}
[terminal]
socket_root = {str(h.dir)!r}
[keybindings.tmux.desktop]
binary = {str(DESKTOP_TMUX)!r}
socket_name = {h.inner!r}
session_name = "Canonical-Sidebar"
[keybindings.tmux.desktop.panel]
enabled = true
argv = [{stub!r}, "desktop-panel"]
''')
        env = dict(h.env, MIOS_VENDOR_TOML=str(vendor), XDG_RUNTIME_DIR=str(h.dir / "run"))
        command = [str(MIOSD), "desktop", "--root", str(private_root), "--no-attach"]
        def bootstrap():
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return __import__("json").loads(result.stdout)
        first = bootstrap()
        roles = {"canvas": first["canvas"]}
        for name in ("panel", "chat", "metrics"):
            roles[name] = h.I("show-options", "-wqv", "-t", first["window"], f"@mios-desktop-{name}-pane")
            self.assertRegex(roles[name], r"^%[0-9]+$")
        self.assertEqual(len(set(roles.values())), 4)
        identity = "#{pane_id}|#{pane_pid}|#{pane_tty}"
        identities = {name: h.I("display", "-p", "-t", pane, identity) for name, pane in roles.items()}
        h.view(session="Canonical-Sidebar", w=240, h=70)
        def mode():
            return h.I("show-options", "-wqv", "-t", first["window"], "@mios-desktop-layout-mode")
        self.assertTrue(wait(lambda: mode() == "landscape-metrics"))
        app = h.I("new-pane", "-d", "-P", "-F", "#{pane_id}", "-t", roles["canvas"], "-x", "30", "-y", "8", "-X", "120", "-Y", "30", "sleep 300")
        geometry = "#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}|#{pane_floating_flag}"
        app_identity = h.I("display", "-p", "-t", app, geometry)
        def rectangle(pane):
            return h.I("display", "-p", "-t", pane, "#{window_id}|#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}").split("|")
        def layout_ready(columns, expected):
            if mode() != expected or h.I("display", "-p", "-t", roles["panel"], "#{window_width}") != str(columns):
                return False
            if expected == "compact":
                return rectangle(roles["panel"])[3] == rectangle(roles["chat"])[3] == str(columns)
            share = SSOT["mcp"]["tmux"]["workspace"]["layout"]["column_share"]
            return int(rectangle(roles["chat"])[3]) == round((columns - 1) * share)
        for columns, rows, expected in [(80, 30, "compact"), (240, 70, "landscape-metrics"), (130, 42, "compact"), (240, 70, "landscape-metrics")]:
            h.O("resize-window", "-t", "=v:", "-x", str(columns), "-y", str(rows))
            self.assertTrue(wait(lambda: layout_ready(columns, expected)))
            rectangles = {name: rectangle(pane) for name, pane in roles.items()}
            workarea = h.I("show-options", "-wqv", "-t", first["window"], "@mios-desktop-workarea-pane")
            if expected == "compact":
                self.assertEqual(workarea, "", "an agent float must not cover the compact chat input")
                self.assertEqual(rectangles["panel"][0], first["window"])
                self.assertEqual(rectangles["chat"][0], first["window"])
                self.assertLess(int(rectangles["panel"][2]), int(rectangles["chat"][2]))
                self.assertEqual(rectangles["panel"][3], rectangles["chat"][3])
                for name in ("canvas", "metrics"):
                    self.assertNotEqual(rectangles[name][0], first["window"])
                    self.assertTrue(h.I("show-options", "-wqv", "-t", roles[name], "@mios-desktop-storage-origin"))
            else:
                self.assertEqual(workarea, roles["canvas"])
                self.assertTrue(all(rect[0] == first["window"] for rect in rectangles.values()))
                self.assertEqual(rectangles["chat"][1], rectangles["metrics"][1])
                self.assertLess(int(rectangles["chat"][2]), int(rectangles["metrics"][2]))
                self.assertLess(int(rectangles["chat"][1]), int(rectangles["canvas"][1]))
                self.assertLess(int(rectangles["panel"][2]), int(rectangles["canvas"][2]))
        h.host.write_text(h.host.read_text().replace("resource_command =", "resource_min_columns = 100\nresource_command ="))
        self.assertEqual(bootstrap(), first)
        self.assertTrue(wait(lambda: mode() == "landscape"))
        self.assertNotEqual(rectangle(roles["metrics"])[0], first["window"], "unfitting metrics are parked without stopping btop")
        self.assertEqual(rectangle(roles["canvas"])[0], first["window"])
        self.assertEqual({name: h.I("display", "-p", "-t", pane, identity) for name, pane in roles.items()}, identities)
        self.assertEqual(h.I("display", "-p", "-t", app, geometry), app_identity)
        self.assertTrue(wait(lambda: h.log.exists() and len(h.log.read_text().splitlines()) == 3))
        lines = h.log.read_text().splitlines()
        self.assertEqual(lines.count("desktop-panel"), 1)
        self.assertEqual(lines.count("desktop-metrics"), 1)
        self.assertEqual(sum(line.endswith("--desktop-chat") for line in lines), 1)

    @unittest.skipUnless(DESKTOP_TMUX is not None, "the explicitly selected native desktop backend is required")
    def test_t18_native_client_focus_fixture_runs_all_seven_with_the_pinned_backend(self):
        self.assertRegex(DESKTOP_TMUX_SHA256, r"\A[a-f0-9]{64}\Z",
                         "MIOS_TMUX_OS_TMUX_SHA256 must pin the explicitly selected native backend")
        backend = DESKTOP_TMUX.resolve(strict=True)
        self.assertTrue(backend.is_file(), "the selected native backend must be a file")
        self.assertEqual(hashlib.sha256(backend.read_bytes()).hexdigest(), DESKTOP_TMUX_SHA256,
                         "the selected native backend differs from its explicit SHA-256")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tests/fixtures/tmux/client-focus.py"),
             "--binary", str(backend), "--sha256", DESKTOP_TMUX_SHA256],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        self.client_focus_output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, self.client_focus_output)
        self.assertRegex(self.client_focus_output, r"(?m)^Ran 7 tests in [0-9.]+s$",
                         "all seven native focus fixture tests must run")
        self.assertRegex(self.client_focus_output, r"(?m)^OK$",
                         "the native focus fixture must pass without skips")
        self.assertNotRegex(self.client_focus_output, r"(?i)\b(skipped|expected failures|unexpected successes)\b")
        self.client_focus_tests_run = 7


    @unittest.skipUnless(DESKTOP_TMUX is not None and MIOSD.is_file(), "the explicitly selected native desktop/backend are required")
    def test_t19_real_c_locale_desktop_bootstrap_and_reopen_keep_exact_native_role_identities(self):
        self.assertRegex(DESKTOP_TMUX_SHA256, r"\A[a-f0-9]{64}\Z", "the native backend must have an explicit SHA-256")
        self.assertEqual(hashlib.sha256(DESKTOP_TMUX.resolve(strict=True).read_bytes()).hexdigest(), DESKTOP_TMUX_SHA256)
        frontend_sha = os.environ.get("MIOS_TMUX_OS_MIOSD_SHA256", "")
        self.assertRegex(frontend_sha, r"\A[a-f0-9]{64}\Z", "the candidate native miosd must have an explicit SHA-256")
        self.assertEqual(hashlib.sha256(MIOSD.resolve(strict=True).read_bytes()).hexdigest(), frontend_sha)
        h = Harness("c-locale-records", global_popups=True)
        self.addCleanup(h.close)
        h.env.update(LANG="C", LC_ALL="C", TERM="xterm-256color")
        private_root = h.dir / "root"
        vendor = private_root / "usr/share/mios/mios.toml"
        vendor.parent.mkdir(parents=True)
        vendor.write_bytes(VENDOR.read_bytes())
        stub = str(h.dir / "bin/fake-tui")
        metrics_command = shlex.join([stub, "desktop-metrics"])
        h.host.write_text(h.host.read_text().replace("[mcp.tmux.workspace]\n", f"[mcp.tmux.workspace]\nresource_command = {metrics_command!r}\n") + f'''\n[mcp]
python = {stub!r}
[terminal]
socket_root = {str(h.dir)!r}
cols = 157
rows = 41
[keybindings.tmux.desktop]
binary = {str(DESKTOP_TMUX)!r}
socket_name = {h.inner!r}
session_name = "Printable-Identity"
[keybindings.tmux.desktop.panel]
enabled = true
argv = [{stub!r}, "desktop-panel"]
''')
        env = dict(h.env, MIOS_VENDOR_TOML=str(vendor), XDG_RUNTIME_DIR=str(h.dir / "run"))
        command = [str(MIOSD), "desktop", "--root", str(private_root), "--no-attach"]
        def bootstrap():
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return __import__("json").loads(result.stdout)
        first = bootstrap()
        self.assertRegex(first["session"], r"^\$[0-9]+$")
        self.assertRegex(first["window"], r"^@[0-9]+$")
        self.assertRegex(first["canvas"], r"^%[0-9]+$")
        runtime_receipt = h.dir / "mios-desktop-1000" / h.inner / "reservation.json"
        details = __import__("json").loads(runtime_receipt.read_text())
        self.assertIsNone(details["login"], "this is maintenance acceptance, not a fabricated login")
        roles = {"canvas": {"pane": details["canvas"], "pid": details["canvas_pid"], "start": details["canvas_start"]}}
        roles.update({role: details[role] for role in ("panel", "chat", "metrics")})
        self.assertEqual(len({role["pane"] for role in roles.values()}), 4)
        observed = {}
        for name, role in roles.items():
            self.assertGreater(role["pid"], 1)
            row = h.I("display-message", "-p", "-t", role["pane"], "#{pane_id}|#{pane_pid}")
            self.assertEqual(row, f"{role['pane']}|{role['pid']}")
            observed[name] = (role["pid"], str(role["start"]))
        self.assertEqual(h.I("display-message", "-p", "-t", first["window"], "#{window_width}|#{window_height}"), "157|41")
        self.assertEqual(bootstrap(), first, "C-locale reopen must verify/reuse its owned server and every role")
        repeated = __import__("json").loads(runtime_receipt.read_text())
        self.assertEqual((repeated["canvas_pid"], str(repeated["canvas_start"])), observed["canvas"])
        for role in ("panel", "chat", "metrics"):
            self.assertEqual((repeated[role]["pid"], str(repeated[role]["start"])), observed[role])
        self.assertTrue(wait(lambda: h.log.exists() and len(h.log.read_text().splitlines()) == 3))
        lines = h.log.read_text().splitlines()
        self.assertEqual(lines.count("desktop-panel"), 1)
        self.assertEqual(lines.count("desktop-metrics"), 1)
        self.assertEqual(sum(line.endswith("--desktop-chat") for line in lines), 1)


    @unittest.skipUnless(
        DESKTOP_TMUX is not None or DESKTOP_TMUX_SHA256 or os.environ.get("MIOS_TMUX_OS_GEN_SHA256"),
        "the explicitly selected native desktop backend and generator are required")
    def test_t21_generated_desktop_roles_preserve_apps_and_refuse_managed_agents(self):
        import json
        self.assertIsNotNone(DESKTOP_TMUX, "pinned acceptance requires an explicit native backend path")
        self.assertRegex(DESKTOP_TMUX_SHA256, r"\A[a-f0-9]{64}\Z",
                         "the selected native backend must have an explicit SHA-256")
        backend = DESKTOP_TMUX.resolve(strict=True)
        self.assertTrue(backend.is_file(), "the selected native backend must be a file")
        self.assertEqual(hashlib.sha256(backend.read_bytes()).hexdigest(), DESKTOP_TMUX_SHA256)
        generator_sha = os.environ.get("MIOS_TMUX_OS_GEN_SHA256", "")
        self.assertRegex(generator_sha, r"\A[a-f0-9]{64}\Z",
                         "the actual candidate generator must have an explicit SHA-256")
        generator = GEN.resolve(strict=True)
        self.assertTrue(generator.is_file(), "the selected generator must be a file")
        self.assertEqual(hashlib.sha256(generator.read_bytes()).hexdigest(), generator_sha)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(ROOT / "tests/fixtures/tmux/desktop-role-actions.py"),
             "--source", str(ROOT), "--mode", "candidate"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        self.desktop_role_actions_output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, self.desktop_role_actions_output)
        self.assertNotRegex(self.desktop_role_actions_output,
                            r"(?i)\b(skipped|expected failures|unexpected successes)\b")
        evidence = json.loads(completed.stdout)
        self.assertIs(evidence["ok"], True)
        self.assertEqual(evidence["mode"], "candidate", "baseline reproduction is not candidate acceptance")
        self.assertEqual(evidence["generator_sha256"], generator_sha)
        self.assertEqual(evidence["generator_resolved_path"], str(generator))
        self.assertEqual(evidence["c95_sha256"], DESKTOP_TMUX_SHA256)
        self.assertRegex(evidence["generated_managed_expression_sha256"], r"\A[a-f0-9]{64}\Z")
        self.assertIs(evidence["four_core_roles"], True)
        self.assertIs(evidence["app_identity_preserved"], True)
        self.assertIs(evidence["native_agent_float_protected"], True)
        self.assertIsInstance(evidence["native_omitted_float_observed"], bool)
        self.assertIsInstance(evidence["count_guard_runtime_exercised"], bool)
        inventory = evidence["native_float_inventory"]
        self.assertIsInstance(inventory["app_listed"], bool)
        self.assertIsInstance(inventory["agent_listed"], bool)
        self.assertEqual(evidence["native_omitted_float_observed"],
                         not (inventory["app_listed"] and inventory["agent_listed"]))
        observations = evidence["native_guard_observations"]
        self.assertEqual(len(observations), 2)
        self.assertEqual({row["action"] for row in observations}, {"center", "new"})
        for row in observations:
            self.assertEqual(row["native_pane_count"], 6)
            self.assertLessEqual(row["observed_pane_count"], row["native_pane_count"])
            self.assertIsInstance(row["agent_witnessed"], bool)
            self.assertIsInstance(row["count_guard_runtime_exercised"], bool)
            if row["count_guard_runtime_exercised"]:
                self.assertIs(row["agent_witnessed"], False)
                self.assertEqual(row["refusal_class"], "unobserved-count")
                self.assertLess(row["observed_pane_count"], row["native_pane_count"])
            else:
                self.assertIs(row["agent_witnessed"], True)
                self.assertEqual(row["refusal_class"], "managed-agent")
        # Some accepted providers list every float. Their real managed-agent
        # refusal is tested here; incomplete inventory remains a distinct Rust
        # unit gate, never credited as a runtime path that did not execute.
        self.assertEqual(evidence["count_guard_runtime_exercised"],
                         any(row["count_guard_runtime_exercised"] for row in observations))
        expected = {
            "canonical-new-from-canvas",
            "center-ordinary-app-with-four-core-roles",
            "focus-core-chat-from-app-with-four-core-roles",
            "restore-app-focus-with-four-core-roles",
            "second-canonical-app-from-existing-float",
            "center-two-ordinary-floats-and-restore-first",
            "native-agent-float-fails-closed",
            "manual-managed-marker-remains-protected",
        }
        expected.update(f"{role}-rejects-{verb}"
                        for role in ("canvas", "panel", "chat", "metrics")
                        for verb in ("close-confirm", "center", "snap-left"))
        expected.update(f"{flag}-wins-over-forged-desktop-role"
                        for flag in ("@mios-workspace-head", "@mios-mcp-owner", "@mios-workspace-worker"))
        self.assertEqual(len(evidence["checks"]), 23, "all positive and negative native cases must execute")
        self.assertEqual(set(evidence["checks"]), expected)


if __name__ == "__main__":
    unittest.main()
