# AI-hint: MiOS-Tmux (tmux-os) behaviour on PRIVATE tmux servers only: generated tables and idempotent reloads, explicit run-shell panes, keyboard/controller/console menus, float shell toggle/lift/dock/reap, hidden sessions, the SSOT Apps and TUIs launcher, outside-tmux app entry, float-safe workspace layout and the narrowed pane-shell reload.
# AI-related: usr/share/mios/tmux/mios-keys.tmux.conf, usr/share/mios/tmux/mios-theme.tmux.conf, usr/libexec/mios/mios-terminal, tools/native/mios-gen/src/tmux_os.rs, tools/native/mios-unit-gen/src/tmux_os.rs, etc/profile.d/50-mios.sh, usr/libexec/mios/mios-mcp-server
# AI-functions: Harness, TestTmuxOs

"""Every tmux server here is `tmux -L mios-tmuxos-test-<pid>-<tag>-{i,o}` under a fresh 0700 TMUX_TMPDIR in /tmp,
killed (and the directory removed) in cleanup. The SSOT is the worktree vendor file plus a private host layer;
the user layer is pointed at a file that does not exist, so no personal configuration is read. Every tmux CLI call
has a timeout (display-popup from a CLI blocks until the popup closes)."""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import secrets
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
SSOT = tomllib.loads(VENDOR.read_text())
OPERATOR_SOCKET = SSOT["keybindings"]["socket_name"]
PREFIX = "mios-tmuxos-test-"

BASE_HOST = """
[mcp.tmux.workspace]
global_sessions = false
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

    def __init__(self, tag, host_extra=""):
        self.dir = Path(tempfile.mkdtemp(prefix="mtt.", dir="/tmp"))  # short path: AF_UNIX sun_path limit
        os.chmod(self.dir, 0o700)
        self.inner = f"{PREFIX}{os.getpid()}-{tag}-i"
        self.outer = f"{PREFIX}{os.getpid()}-{tag}-o"
        for name in (self.inner, self.outer):
            assert name.startswith(PREFIX) and name != OPERATOR_SOCKET and not name.startswith(("tmux-os", "mios-workspace-"))
        (self.dir / "bin").mkdir()
        (self.dir / "run").mkdir(mode=0o700)
        self.log = self.dir / "tui.log"
        stub = self.dir / "bin/fake-tui"
        stub.write_text(STUB.replace("{log}", str(self.log)))
        stub.chmod(0o755)
        self.host = self.dir / "host.toml"
        self.host.write_text(BASE_HOST + host_extra)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TMUX", "MIOS_", "SSH_"))}
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
        conf.write_text(text.replace("/usr/libexec/mios/mios-terminal", str(TERMINAL)))
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
        cmd = f"env -u TMUX TERM={term} tmux -L {self.inner} attach -t ={session}"
        if self.O("has-session", "-t", "=v", check=False) == "" and subprocess.run(
                ["tmux", "-L", self.outer, "has-session", "-t", "=v"], env=self.env, capture_output=True, timeout=5).returncode == 0:
            self.O("new-window", "-t", "=v:", "-n", name, cmd)
        else:
            self.O("new-session", "-d", "-s", "v", "-n", name, "-x", str(w), "-y", str(h), cmd)
        self.O("set", "-g", "escape-time", "0")
        before = set(self.clients())
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
        h = self.harness("t3")
        h.start(80, 20)
        c = h.view("view", w=80, h=20)
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


if __name__ == "__main__":
    unittest.main()
