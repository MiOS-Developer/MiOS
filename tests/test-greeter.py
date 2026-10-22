#!/usr/bin/env python3
# AI-hint: End-to-end tests for the MiOS glass greeter WITHOUT touching the real greetd: a mock greetd socket
# (tests/greeter/mock_greetd.py), a private headless cage on its own XDG_RUNTIME_DIR/WAYLAND_DISPLAY, real
# key events through tests/greeter/vkbd.py, and a stub in place of tuigreet so every fallback path is
# observable (the stub records its PID: same PID as the launcher == the launcher exec'd it).
# Kills only the process groups it started. MIOS_GREETER_PREVIEW=<png> saves a screenshot of the greeter.
# Run inside a memory-capped scope:
#   systemd-run --user --scope -p MemoryMax=8G -p MemorySwapMax=0 python3 -I tests/test-greeter.py
# AI-related: usr/share/mios/greeter/, usr/libexec/mios/mios-greeter-session, tools/native/mios-gen/src/greeter.rs
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MIOS_GREETER_ROOT", os.path.dirname(HERE))
GREETER = os.path.join(ROOT, "usr/share/mios/greeter")
LAUNCHER = os.path.join(ROOT, "usr/libexec/mios/mios-greeter-session")
MOCK = os.path.join(HERE, "greeter/mock_greetd.py")
VKBD = os.path.join(HERE, "greeter/vkbd.py")
SESSIONS = os.environ.get("MIOS_GREETER_REAL_SESSIONS", "/usr/share/wayland-sessions")
TUIGREET_ARGS = ["--time", "--asterisks", "--remember", "--remember-user-session",
                 "--sessions", "/usr/share/wayland-sessions:/usr/share/xsessions",
                 "--cmd", "/usr/local/bin/start-hyprland-igpu"]
HAVE_GUI = all(shutil.which(b) for b in ("cage", "qs", "grim"))
PASSWD = "mios:x:1000:1000:MiOS:/home/mios:/bin/bash\ndemo:x:1001:1001:Demo User:/home/demo:/bin/bash\n" \
         "svc:x:990:990:service:/:/usr/sbin/nologin\nnobody2:x:1002:1002::/:/sbin/nologin\n"


def wait_for(pred, timeout, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


class Rig:
    """One mock greetd + one launcher run on a private runtime dir."""

    def __init__(self, tc, mock_args=("--user", "mios=hunter2"), launcher_args=(), env=None, sock=True):
        self.tc = tc
        self.tmp = tempfile.mkdtemp(prefix="mios-greeter-test-")
        # Wayland/IPC socket paths must stay under 108 bytes: keep the runtime dir short.
        base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        self.rt = tempfile.mkdtemp(prefix="mgt-", dir=base)
        os.chmod(self.rt, 0o700)
        self.transcript = os.path.join(self.tmp, "transcript.jsonl")
        self.stub_out = os.path.join(self.tmp, "stub.json")
        self.stub = os.path.join(self.tmp, "tuigreet-stub")
        with open(self.stub, "w") as f:
            f.write("#!/bin/sh\nprintf '{\"pid\": %s, \"argv\": \"%s\", \"sock\": \"%s\"}\\n' \"$$\" \"$*\" "
                    "\"${GREETD_SOCK:-}\" > " + self.stub_out + "\nexit 0\n")
        os.chmod(self.stub, 0o755)
        with open(os.path.join(self.tmp, "passwd"), "w") as f:
            f.write(PASSWD)
        self.sock = os.path.join(self.rt, "greetd.sock")
        self.mock = None
        if sock:
            self.mock = subprocess.Popen([sys.executable, "-I", MOCK, self.sock, self.transcript, *mock_args],
                                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tc.assertTrue(wait_for(lambda: os.path.exists(self.sock), 5), "mock greetd did not start")
        e = {k: v for k, v in os.environ.items()
             if k not in ("WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "GREETD_SOCK")}
        e.update(XDG_RUNTIME_DIR=self.rt, WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                 MIOS_GREETER_DRY_RUN="1", MIOS_GREETER_STATE=os.path.join(self.tmp, "state.json"),
                 MIOS_GREETER_PASSWD=os.path.join(self.tmp, "passwd"),
                 MIOS_GREETER_TUIGREET_CACHE=os.path.join(self.tmp, "no-tuigreet-cache"))
        if sock:
            e["GREETD_SOCK"] = self.sock
        e.update(env or {})
        args = list(launcher_args) or ["--greeter-dir", GREETER, "--sessions", SESSIONS,
                                       "--default-session", "hyprland", "--start-timeout", "20",
                                       "--heartbeat-timeout", "30"]
        self.log = open(os.path.join(self.tmp, "launcher.log"), "w+")
        self.proc = subprocess.Popen(["/bin/sh", LAUNCHER, *args, "--", self.stub, *TUIGREET_ARGS],
                                     env=e, start_new_session=True, stdout=self.log, stderr=subprocess.STDOUT)

    def status(self):
        for p in glob.glob(os.path.join(self.rt, "mios-greeter.*/status")):
            try:
                with open(p) as f:
                    return f.read().strip()
            except OSError:
                pass
        return ""

    def wait_ready(self):
        self.tc.assertTrue(wait_for(lambda: self.status() == "ready", 25), "greeter never became ready:\n" + self.output())
        time.sleep(0.6)  # first frame + initial focus

    def keys(self, *steps, allow_close=False):
        r = subprocess.run([sys.executable, "-I", VKBD, *(["--allow-close"] if allow_close else []), *steps], timeout=60, capture_output=True, text=True,
                           env={**os.environ, "XDG_RUNTIME_DIR": self.rt, "WAYLAND_DISPLAY": "wayland-0"})
        self.tc.assertEqual(r.returncode, 0, r.stderr)
        time.sleep(0.4)

    def shot(self, path):
        r = subprocess.run(["grim", path], timeout=20, capture_output=True, text=True,
                           env={**os.environ, "XDG_RUNTIME_DIR": self.rt, "WAYLAND_DISPLAY": "wayland-0"})
        self.tc.assertEqual(r.returncode, 0, r.stderr)

    def transcript_msgs(self, direction="in"):
        if not os.path.exists(self.transcript):
            return []
        with open(self.transcript) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [row["msg"] for row in rows if row["dir"] == direction]

    def stub_result(self):
        try:
            with open(self.stub_out) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def output(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def wait_exit(self, timeout):
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def close(self):
        for p in (self.proc, self.mock):
            if p is None:
                continue
            try:
                os.killpg(p.pid, signal.SIGKILL)  # only the groups this rig started
            except ProcessLookupError:
                pass
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self.log.close()
        shutil.rmtree(self.rt, ignore_errors=True)
        if not os.environ.get("MIOS_GREETER_KEEP"):
            shutil.rmtree(self.tmp, ignore_errors=True)


class Lint(unittest.TestCase):
    LINT = shutil.which("qmllint") or shutil.which("qmllint-qt6") or "/usr/lib64/qt6/bin/qmllint"

    def test_qmllint_greeter(self):
        if not os.access(self.LINT, os.X_OK):
            self.skipTest("qmllint missing")
        files = sorted(f for f in os.listdir(GREETER) if f.endswith(".qml") and f != "Theme.qml")
        self.assertIn("shell.qml", files)
        out = subprocess.run([self.LINT, "-I", "/usr/lib64/qt6/qml", *files], cwd=GREETER,
                             capture_output=True, text=True, timeout=120)
        text = out.stdout + out.stderr
        self.assertNotIn("[syntax]", text)
        self.assertNotIn("Error:", text)
        # The only accepted warning: qmllint cannot follow the Theme.qml symlink to ../quickshell.
        warnings = [l for l in text.splitlines() if l.startswith("Warning:")]
        self.assertTrue(all("Theme" in w for w in warnings), "\n".join(warnings))

    def test_negative_qmllint_catches_broken_greeter(self):
        if not os.access(self.LINT, os.X_OK):
            self.skipTest("qmllint missing")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(GREETER, "shell.qml")) as f:
                src = f.read()
            with open(os.path.join(d, "shell.qml"), "w") as f:
                f.write(src.replace("function submit() {", "function submit( {", 1))
            out = subprocess.run([self.LINT, "shell.qml"], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertIn("[syntax]", out.stdout + out.stderr)

    def test_theme_is_the_shared_projection(self):
        self.assertEqual(os.readlink(os.path.join(GREETER, "Theme.qml")), "../quickshell/Theme.qml")

    def test_launcher_is_posix_sh(self):
        self.assertEqual(subprocess.run(["sh", "-n", LAUNCHER]).returncode, 0)
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", "-s", "sh", LAUNCHER], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


@unittest.skipUnless(HAVE_GUI, "cage/qs/grim missing")
class Glass(unittest.TestCase):
    def rig(self, **kw):
        r = Rig(self, **kw)
        self.addCleanup(r.close)
        return r

    def test_boot_gpu_overrides_stale_glass_session_memory(self):
        for profile, selected, stale in (
            ("igpu", "hyprland", "mios-desktop-dgpu"),
            ("dgpu", "mios-desktop-dgpu", "hyprland"),
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(prefix="mios-gpu-greeter-") as work:
                for name in ("hyprland", "mios-desktop-dgpu"):
                    shutil.copy(os.path.join(ROOT, "usr/share/wayland-sessions", name + ".desktop"), work)
                state = os.path.join(work, "state.json")
                with open(state, "w") as stream:
                    json.dump({"session": stale}, stream)
                r = self.rig(env={"MIOS_GREETER_STATE": state, "MIOS_GREETER_BOOT_SESSION": selected},
                             launcher_args=("--greeter-dir", GREETER, "--sessions", work,
                                            "--default-session", selected))
                r.wait_ready()
                r.keys("key:Down", "key:Tab", "text:hunter2", "key:Enter", allow_close=True)
                self.assertEqual(r.wait_exit(20), 0, r.output())
                start = [m for m in r.transcript_msgs() if m.get("type") == "start_session"]
                self.assertEqual(len(start), 1)
                self.assertEqual(start[0]["cmd"], ["/usr/bin/mios-gen", "hyprland-session",
                                                   "--hardware-profile", profile])

    def test_login_wrong_then_right_password_launches_hyprland_without_fallback(self):
        r = self.rig()
        r.wait_ready()
        preview = os.environ.get("MIOS_GREETER_PREVIEW")
        r.keys("key:Down", "key:Tab")          # Up/Down picks a user from passwd (mios first; svc/nologin hidden)
        if preview:
            r.keys("text:hunter")               # show a half-typed password in the preview
            r.shot(preview)
            r.keys(*["key:BackSpace"] * 6)
        r.keys("text:wrongpw", "key:Enter")
        self.assertTrue(wait_for(lambda: any(m.get("type") == "cancel_session" for m in r.transcript_msgs()), 10))
        r.shot(os.path.join(r.tmp, "error.png"))
        msgs = r.transcript_msgs()
        self.assertEqual(msgs[0], {"type": "create_session", "username": "mios"})
        self.assertEqual(msgs[1]["response"], "wrongpw")
        self.assertEqual(r.status(), "ready", "auth failure must not end the greeter")
        if preview:
            shutil.copy(os.path.join(r.tmp, "error.png"), preview.replace(".png", "-error.png"))
        # Focus returned to the cleared password field: just type and press Enter.
        r.keys("text:hunter2", "key:Enter", allow_close=True)
        self.assertEqual(r.wait_exit(20), 0, r.output())
        start = [m for m in r.transcript_msgs() if m.get("type") == "start_session"]
        self.assertEqual(len(start), 1)
        self.assertEqual(start[0]["cmd"], ["/usr/bin/mios-gen", "hyprland-session"])
        self.assertIn("XDG_SESSION_TYPE=wayland", start[0]["env"])
        self.assertIn("XDG_CURRENT_DESKTOP=Hyprland", start[0]["env"])
        self.assertIsNone(r.stub_result(), "fallback must NOT run after a successful login")
        with open(os.path.join(r.tmp, "state.json")) as f:
            self.assertEqual(json.load(f), {"user": "mios", "session": "hyprland"})

    def test_session_choice_terminal_console_and_extra_pam_prompt(self):
        sess = tempfile.mkdtemp(prefix="mios-greeter-sessions-")
        self.addCleanup(shutil.rmtree, sess, True)
        for f in ("hyprland.desktop", "tmux-console.desktop"):
            shutil.copy(os.path.join(SESSIONS, f), sess)
        r = self.rig(mock_args=("--user", "demo=pw1", "--otp", "demo=424242", "--info", "demo=Welcome back"),
                     launcher_args=("--greeter-dir", GREETER, "--sessions", sess, "--default-session", "hyprland"))
        r.wait_ready()
        r.keys("key:Down", "key:Down", "key:Tab", "text:pw1", "key:F2", "key:Enter")
        self.assertTrue(wait_for(lambda: len(r.transcript_msgs()) >= 2, 10), r.transcript_msgs())
        r.keys("sleep:300", "text:424242", "key:Enter", allow_close=True)
        self.assertEqual(r.wait_exit(20), 0, r.output())
        start = [m for m in r.transcript_msgs() if m.get("type") == "start_session"]
        self.assertEqual(start[0]["cmd"], ["/usr/libexec/mios/mios-terminal"], start)
        self.assertIsNone(r.stub_result())

    def test_power_actions_need_confirmation(self):
        marks = tempfile.mkdtemp(prefix="mios-greeter-power-")
        self.addCleanup(shutil.rmtree, marks, True)
        r = self.rig(env={"MIOS_GREETER_POWEROFF_CMD": "touch " + os.path.join(marks, "poweroff"),
                          "MIOS_GREETER_REBOOT_CMD": "touch " + os.path.join(marks, "reboot")})
        r.wait_ready()
        r.keys("key:F12")
        time.sleep(0.5)
        self.assertFalse(os.path.exists(os.path.join(marks, "poweroff")), "one F12 must only ask")
        r.keys("key:F12")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(marks, "poweroff")), 5), r.output())
        r.keys("key:F11", "key:Escape", "key:F11")
        time.sleep(0.5)
        self.assertFalse(os.path.exists(os.path.join(marks, "reboot")), "Esc must cancel a pending reboot")
        r.keys("key:F11")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(marks, "reboot")), 5))

    def test_negative_test_mode_never_runs_default_power_commands(self):
        r = self.rig()  # dry run, no explicit commands: F12 twice must only print
        r.wait_ready()
        r.keys("key:F12", "key:F12")
        time.sleep(0.5)
        self.assertIsNone(r.proc.poll(), "greeter still up, host untouched")

    # ---- fallback paths: the launcher must exec tuigreet (same PID) ----------------------------
    def assert_fell_back(self, r, why, timeout=30):
        self.assertEqual(r.wait_exit(timeout), 0, r.output())
        res = r.stub_result()
        self.assertIsNotNone(res, "tuigreet stub never ran:\n" + r.output())
        self.assertEqual(res["pid"], r.proc.pid, "launcher must exec the fallback, not fork it")
        self.assertEqual(res["argv"], " ".join(TUIGREET_ARGS))
        self.assertIn(why, r.output())

    def test_fallback_operator_text_login_f10(self):
        r = self.rig()
        r.wait_ready()
        r.keys("key:F10", allow_close=True)
        self.assert_fell_back(r, "operator chose text login")
        self.assertEqual(r.stub_result()["sock"], r.sock, "tuigreet must inherit GREETD_SOCK")

    def test_fallback_when_greeter_crashes_at_start(self):
        r = self.rig(env={"MIOS_GREETER_QS": "/bin/false"})
        self.assert_fell_back(r, "without launching a session")

    def test_fallback_when_greeter_killed_after_ready(self):
        r = self.rig()
        r.wait_ready()
        out = subprocess.run(["pgrep", "-g", str(r.proc.pid), "-x", "qs|quickshell"], capture_output=True, text=True)
        pids = [int(p) for p in out.stdout.split()]
        self.assertTrue(pids, "qs not found in the rig's process group")
        for p in pids:
            os.kill(p, signal.SIGKILL)
        self.assert_fell_back(r, "without launching a session")

    def test_fallback_when_greeter_never_ready(self):
        hang = os.path.join(tempfile.mkdtemp(prefix="mios-greeter-hang-"), "qs")
        self.addCleanup(shutil.rmtree, os.path.dirname(hang), True)
        with open(hang, "w") as f:
            f.write("#!/bin/sh\nexec sleep 600\n")
        os.chmod(hang, 0o755)
        r = self.rig(env={"MIOS_GREETER_QS": hang},
                     launcher_args=("--greeter-dir", GREETER, "--sessions", SESSIONS, "--start-timeout", "3"))
        t0 = time.monotonic()
        self.assert_fell_back(r, "not ready within 3s")
        self.assertLess(time.monotonic() - t0, 15)

    def test_fallback_when_greeter_hangs_after_ready(self):
        r = self.rig(launcher_args=("--greeter-dir", GREETER, "--sessions", SESSIONS, "--heartbeat-timeout", "7"))
        r.wait_ready()
        out = subprocess.run(["pgrep", "-g", str(r.proc.pid), "-x", "qs|quickshell"], capture_output=True, text=True)
        stopped = [int(p) for p in out.stdout.split()]
        self.assertTrue(stopped)
        for p in stopped:
            os.kill(p, signal.SIGSTOP)
        self.assert_fell_back(r, "no heartbeat", timeout=30)
        time.sleep(0.5)
        self.assertFalse([p for p in stopped if os.path.exists(f"/proc/{p}") and
                          open(f"/proc/{p}/stat").read().split()[2] != "Z"],
                         "the hung greeter must be killed, not left behind")

    def test_fallback_without_greetd_socket(self):
        r = self.rig(sock=False)
        self.assert_fell_back(r, "GREETD_SOCK not a socket")

    def test_fallback_when_compositor_missing(self):
        r = self.rig(env={"MIOS_GREETER_CAGE": "/nonexistent/cage"})
        self.assert_fell_back(r, "compositor /nonexistent/cage missing")

    def test_negative_control_healthy_greeter_does_not_fall_back(self):
        r = self.rig()
        r.wait_ready()
        time.sleep(8)  # longer than one heartbeat period
        self.assertIsNone(r.proc.poll(), r.output())
        self.assertIsNone(r.stub_result())


if __name__ == "__main__":
    unittest.main(verbosity=2)
