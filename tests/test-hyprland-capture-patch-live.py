#!/usr/bin/env python3
# AI-hint: Live reproduction of the Hyprland 0.56.2 toplevel-capture crashes, run ONLY against a nested Hyprland the caller
#          owns (mios-gen nested-session --seat <seat>; never the operator's compositor). Two scenarios, each judged by
#          whether the nested compositor and the capturing client survive:
#            orphan  -- a floating window pinned on a headless output; the output is removed (the pinned window keeps an
#                       expired m_monitor: `hyprctl clients` monitor -1); a NEW capture client captures it.
#                       Stock 0.56.2: SEGV in Screenshare::CScreenshareSession::init (cvtss2sd 0x94 = nullptr->m_scale).
#            closed  -- windows closed while a client streams them (hyprland-toplevel-export with a stale handle).
#                       Stock 0.56.2: the client is disconnected ("invalid object": the frame id it allocated is never
#                       created), i.e. the whole shell when the shell was previewing.
#          The capture client is a Quickshell harness that ScreencopyViews every foreign toplevel and toggles the source
#          every few ms (what the dock preview does, harder). With hyprland-screenshare-no-monitor.patch the compositor
#          answers those captures with `failed` and both survive. --expect crash|survive turns the result into pass/fail, so the
#          same scenario is the negative control (the stock compositor) and the positive control (the patched build).
# AI-related: usr/share/mios/hyprland/patches/hyprland-screenshare-no-monitor.patch, usr/share/mios/mios.toml
#             [desktop.hyprland.compositor] [desktop.nested].compositor, tools/native/mios-gen/src/hyprland_build.rs,
#             tools/native/mios-gen/src/nested_session.rs
import argparse, json, os, signal, subprocess, sys, tempfile, time

HARNESS = r'''
import QtQuick
import Quickshell
import Quickshell.Wayland
ShellRoot {
    PanelWindow {
        anchors { top: true; left: true }
        implicitWidth: 400; implicitHeight: 60
        color: "transparent"; exclusiveZone: 0
        Row { Repeater { model: ToplevelManager.toplevels
            delegate: ScreencopyView { id: v; required property var modelData; property bool on: true
                width: 60; height: 40; captureSource: on ? modelData : null; live: true
                Timer { interval: 7; repeat: true; running: true; onTriggered: v.on = !v.on } } } }
    }
}
'''


def session(seat):
    out = subprocess.run(["mios-gen", "nested-session", "--list-sessions"], capture_output=True, text=True, check=True).stdout
    for s in json.loads(out):
        if s.get("seat") == seat and s.get("state") == "running":
            return s
    sys.exit(f"no running nested session with seat {seat!r}")


class Nest:
    def __init__(self, s):
        self.s = s
        self.env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "TMUX", "TMUX_PANE", "MIOS_TOML")}
        self.env.update(XDG_RUNTIME_DIR=s["runtime"], HYPRLAND_INSTANCE_SIGNATURE=s["instance"],
                        WAYLAND_DISPLAY=s["wayland_display"], QT_QPA_PLATFORM="wayland")
        self.pid = s["compositor_pid"]
        self.children = []

    def alive(self):
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def hc(self, *a):
        return subprocess.run(["hyprctl", *a], env=self.env, capture_output=True, text=True, timeout=5).stdout

    def clients(self):
        try:
            return json.loads(self.hc("-j", "clients"))
        except Exception:
            return []

    def eval(self, code):
        return self.hc("dispatch", code).strip()

    def spawn(self, argv, log=subprocess.DEVNULL):
        p = subprocess.Popen(argv, env=self.env, stdout=log, stderr=log, start_new_session=True)
        self.children.append(p)
        return p

    def stop(self, p):
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)

    def cleanup(self):
        for p in self.children:
            try:
                self.stop(p)
            except OSError:
                pass


def wait_for(fn, t=5.0):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None


def compositor(n):
    try:
        return os.readlink(f"/proc/{n.pid}/exe")
    except OSError:
        return None


def scenario_orphan(n, qml, logdir):
    before = {c["address"] for c in n.clients()}
    for _ in range(2):
        n.spawn(["weston-terminal"])
    new = wait_for(lambda: [c["address"] for c in n.clients() if c["address"] not in before][:2] if len([c for c in n.clients() if c["address"] not in before]) >= 2 else None)
    if not new:
        return {"error": "test windows did not map"}
    a, b = new
    n.hc("output", "create", "headless", "CAPGUARD-H1")
    mon = wait_for(lambda: next((m for m in json.loads(n.hc("-j", "monitors") or "[]") if m["name"] == "CAPGUARD-H1"), None))
    if not mon:
        return {"error": "headless output not created"}
    ws = mon["activeWorkspace"]["name"]
    h1 = n.spawn(["quickshell", "--path", qml], open(os.path.join(logdir, "harness-orphan-1.log"), "w"))
    for addr in (a, b):
        n.eval(f"hl.dsp.window.move({{workspace = '{ws}', window = 'address:{addr}', follow = false}})")
    n.eval(f"hl.dsp.window.float({{action = 'enable', window = 'address:{b}'}})")
    n.eval(f"hl.dsp.window.pin({{action = 'enable', window = 'address:{b}'}})")
    time.sleep(1)
    n.hc("output", "remove", "CAPGUARD-H1")
    time.sleep(1.5)
    orphan = next((c for c in n.clients() if c["address"] == b), None)
    n.stop(h1)
    h2 = n.spawn(["quickshell", "--path", qml], open(os.path.join(logdir, "harness-orphan-2.log"), "w"))
    time.sleep(3)
    res = {"orphan_monitor": orphan["monitor"] if orphan else None, "compositor_alive": n.alive(),
           "client_alive": h2.poll() is None}
    n.stop(h2)
    return res


def scenario_closed(n, qml, logdir):
    h = n.spawn(["quickshell", "--path", qml], open(os.path.join(logdir, "harness-closed.log"), "w"))
    time.sleep(1.5)
    for _ in range(6):
        before = {c["address"] for c in n.clients()}
        n.spawn(["weston-terminal"])
        addr = wait_for(lambda: next((c["address"] for c in n.clients() if c["address"] not in before), None))
        time.sleep(0.4)
        if addr:
            n.eval(f"hl.dsp.window.close({{window = 'address:{addr}'}})")
        time.sleep(0.3)
        if not n.alive() or h.poll() is not None:
            break
    time.sleep(1)
    res = {"compositor_alive": n.alive(), "client_alive": h.poll() is None}
    n.stop(h)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seat", required=True)
    ap.add_argument("--scenario", choices=["orphan", "closed", "both"], default="both")
    ap.add_argument("--expect", choices=["crash", "survive", "report"], default="report")
    ap.add_argument("--logdir", default=tempfile.mkdtemp(prefix="capguard-"))
    a = ap.parse_args()
    n = Nest(session(a.seat))
    os.makedirs(a.logdir, exist_ok=True)
    qml = os.path.join(a.logdir, "harness.qml")
    with open(qml, "w") as f:
        f.write(HARNESS)
    out = {"seat": a.seat, "instance": n.s["instance"], "compositor": compositor(n), "scenarios": {}}
    try:
        if a.scenario in ("closed", "both"):
            out["scenarios"]["closed"] = scenario_closed(n, qml, a.logdir)
        if a.scenario in ("orphan", "both") and n.alive():
            out["scenarios"]["orphan"] = scenario_orphan(n, qml, a.logdir)
    finally:
        n.cleanup()
    survived = all(s.get("compositor_alive") and s.get("client_alive") for s in out["scenarios"].values())
    out["survived"] = survived
    print(json.dumps(out, indent=1))
    if a.expect == "survive":
        sys.exit(0 if survived else 1)
    if a.expect == "crash":
        sys.exit(0 if not survived else 1)


if __name__ == "__main__":
    # The unit tier runs every registered suite with no arguments and no compositor. This reproduction needs a nested
    # Hyprland the caller owns (never the operator's), named by --seat, so like every other live suite it is opt-in:
    # MIOS_LIVE_NESTED=1 or an explicit argument runs it (a missing --seat then fails), otherwise it reports the skip.
    if len(sys.argv) == 1 and os.environ.get("MIOS_LIVE_NESTED") != "1":
        print("skip: live nested capture reproduction (set MIOS_LIVE_NESTED=1 and pass --seat <owned nested seat>)")
        sys.exit(0)
    main()
