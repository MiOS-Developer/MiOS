#!/usr/bin/env python3
# AI-hint: LIVE opt-in (MIOS_LIVE_NESTED=1): in an OWNED nested MiOS PiP session (`mios-gen nested-session --layout pip`,
#          private /tmp) -- the frame-rate tiers through the candidate miosd on private fake D-Bus services (power profile,
#          UPower, GameMode): mios-pacing cap and presented fps, Hyprglass glass rate, the wallpaper's held frame
#          callbacks, Hypr3D's room/glass/dome rates; the extended-vblank modeset with its 15 s confirm-or-revert guard;
#          then the Hypr3D Retina spawn numbers at the nested output's scale, landscape and portrait
#          (tests/test-hypr3d-retina-spawn.py tiers). Positive and negative controls throughout.
# AI-related: src/mios-rs/miosd/src/framerate.rs, src/mios-rs/miosd/src/framerate/plan.rs, tools/native/mios-resolver/src/edid_timing.rs,
# AI-related: usr/share/mios/hyprland/plugins/mios-pacing, usr/share/mios/mios.toml [desktop.framerate] [desktop.hyprland.hypr3d.spawn],
# AI-related: tests/test-framerate.py, tests/test-hypr3d-retina-spawn.py, tools/native/mios-gen/src/nested_session.rs
# AI-functions: Nest, Bus, Daemon, synth_edid, tier_checks, room_checks, modeset_checks, retina_checks
"""Live frame-rate + Retina checks in an owned nested PiP session.

Primary compositor: only the launcher's own PiP window appears there; nothing is dispatched to it. The nested
compositor gets the candidate plugins; the daemon runs against the nested instance with a private runtime, state
home, user tier and private buses (nothing activatable). The nested Hyprland is SIGTERMed at the end.

Env: MIOS_LIVE_NESTED=1; MIOS_GEN (candidate launcher; needs extended-vblank), MIOS_FR_MIOSD, MIOS_FR_WALLPAPERD,
MIOS_FR_PACING_SO, MIOS_FR_HYPR3D_SO (default: installed); MIOS_LIVE_OUT (outside /tmp: the nest's /tmp is private).
The nested window's frame callbacks follow the PRIMARY's own rate (its cap), so the tiers here are scaled below it
(20/24/30/40 Hz; divisors are the vendor ones); the vendor table itself is unit-tested in tests/test-framerate.py.
"""
import json, os, re, signal, subprocess, sys, time
from pathlib import Path

if os.environ.get("MIOS_LIVE_NESTED") != "1":
    print("skip: live nested frame-rate/Retina test (set MIOS_LIVE_NESTED=1 inside a MiOS desktop session)")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
WT_SSOT = ROOT / "usr/share/mios/mios.toml"
OUT = Path(os.environ.get("MIOS_LIVE_OUT") or Path.home() / ".cache/mios-live" / f"framerate-retina-{int(time.time())}")
GEN = os.environ.get("MIOS_GEN", "/usr/bin/mios-gen")
MIOSD = os.environ.get("MIOS_FR_MIOSD", "/usr/bin/miosd")
WALLPAPERD = os.environ.get("MIOS_FR_WALLPAPERD", "/usr/bin/mios-wallpaperd")
PACING_SO = os.environ.get("MIOS_FR_PACING_SO", "/usr/lib64/mios/mios-pacing.so")
H3D_SO = os.environ.get("MIOS_FR_HYPR3D_SO", "/usr/lib64/mios/hypr3d.so")
QML = "/usr/lib64/qt6/bin/qml"
TIERS = {"power-saver": 20, "balanced": 20, "battery": 24, "performance": 30, "gaming": 40}   # scaled (see above)
results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:600]})
    print(f"{'PASS' if ok else 'FAIL'} {name} {str(detail)[:300]}", flush=True)
    return ok


def sh(args, env=None, timeout=30, **kw):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout, **kw)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("MIOS_TOML", "MIOS_VENDOR_TOML", "MIOS_USER_TOML", "MIOS_USER_TOML_D", "MIOS_HOST_TOML",
                        "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                        "DBUS_SYSTEM_BUS_ADDRESS", "TMUX", "TMUX_PANE")}
    env.update(extra)
    return env


def primary_facts():
    pid = sh(["pgrep", "-x", "Hyprland", "-o"]).stdout.strip()
    return {"pid": pid, "portal_mounts": open("/proc/self/mountinfo").read().count("fuse.portal"),
            "clients": len(json.loads(sh(["hyprctl", "-j", "clients"]).stdout or "[]"))}


def synth_edid(w, h, base_hz=60, vmin=20, vmax=60):
    """An EDID for a VIRTUAL output (the test hook only reaches outputs without a DRM connector): one detailed timing
    at w x h, base_hz, with a 20..60 Hz range descriptor."""
    ht, vt = w + 160, h + 40
    clock10 = round(base_hz * ht * vt / 10000)          # 10 kHz units
    b = bytearray(128)
    b[0:8] = bytes([0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0])
    b[18], b[19] = 1, 4
    d = bytearray(18)
    d[0], d[1] = clock10 & 0xFF, clock10 >> 8
    d[2], d[3], d[4] = w & 0xFF, 160 & 0xFF, ((w >> 8) << 4) | (160 >> 8)
    d[5], d[6], d[7] = h & 0xFF, 40 & 0xFF, ((h >> 8) << 4) | (40 >> 8)
    d[8], d[9], d[10], d[11], d[17] = 48, 32, (3 << 4) | 5, 0, 0x1E
    b[54:72] = d
    b[72:90] = bytes([0, 0, 0, 0xFD, 0, vmin, vmax, 30, 160, 20, 1, 0x0A] + [0x20] * 6)
    b[90:108] = bytes([0, 0, 0, 0x10] + [0] * 14)
    b[108:126] = bytes([0, 0, 0, 0x10] + [0] * 14)
    b[127] = (256 - sum(b[:127]) % 256) % 256
    return bytes(b)


BUSCONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>{kind}</type><listen>unix:path={sock}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/></policy>
</busconfig>"""

FAKE = r'''
import sys, gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
SYSTEM, SESSION, LOG = sys.argv[1:4]
def conn(a):
    return Gio.DBusConnection.new_for_address_sync(a, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
def own(c, name):
    c.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName", GLib.Variant("(su)", (name, 4)), None, Gio.DBusCallFlags.NONE, -1, None)
def serve(addr, name, path, iface, props):
    c = conn(addr)
    p = "".join(f'<property name="{k}" type="{v.get_type_string()}" access="readwrite"/>' for k, v in props.items())
    info = Gio.DBusNodeInfo.new_for_xml(f'<node><interface name="{iface}">{p}</interface></node>').interfaces[0]
    def get(c_, s, pa, i, prop): return props[prop]
    def set_(c_, s, pa, i, prop, value):
        props[prop] = value
        c_.emit_signal(None, path, "org.freedesktop.DBus.Properties", "PropertiesChanged", GLib.Variant("(sa{sv}as)", (iface, {prop: value}, [])))
        open(LOG, "a").write(f"{iface}.{prop}={value.unpack()}\n")
        return True
    c.register_object(path, info, None, get, set_)
    own(c, name)
    return c
keep = [serve(SYSTEM, "net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles", {"ActiveProfile": GLib.Variant("s", "power-saver")}),
        serve(SYSTEM, "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", {"OnBattery": GLib.Variant("b", False)}),
        serve(SESSION, "com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode", {"ClientCount": GLib.Variant("i", 0)})]
open(LOG, "a").write("ready\n")
GLib.MainLoop().run()
'''

ANIM = """import QtQuick
Window { width: 320; height: 220; visible: true; title: "mios-fr-anim"; color: "#202040"
  Rectangle { width: 80; height: 80; color: "#e04020"; anchors.centerIn: parent
    NumberAnimation on rotation { from: 0; to: 360; duration: 900; loops: Animation.Infinite } } }
"""


class Bus:
    """Private system + session buses (no service directories: nothing activatable) with the fake services."""

    def __init__(self):
        self.procs = []
        self.addr = {}
        for kind in ("system", "session"):
            sock = OUT / f"bus-{kind}"
            conf = OUT / f"bus-{kind}.conf"
            conf.write_text(BUSCONF.format(kind=kind, sock=sock))
            self.procs.append(subprocess.Popen(["dbus-daemon", f"--config-file={conf}", "--nofork"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            self.addr[kind] = f"unix:path={sock}"
        for _ in range(50):
            if all((OUT / f"bus-{k}").exists() for k in ("system", "session")):
                break
            time.sleep(0.1)
        (OUT / "fake.py").write_text(FAKE)
        self.log = OUT / "fake.log"
        self.procs.append(subprocess.Popen([sys.executable, "-I", str(OUT / "fake.py"), self.addr["system"], self.addr["session"], str(self.log)]))
        for _ in range(100):
            if self.log.exists() and "ready" in self.log.read_text():
                return
            time.sleep(0.1)
        raise RuntimeError("fake services did not start")

    def set(self, kind, dest, path, iface, prop, sig, value):
        r = sh(["gdbus", "call", "--address", self.addr[kind], "--dest", dest, "--object-path", path, "--method",
                "org.freedesktop.DBus.Properties.Set", iface, prop, f"<{sig} {value}>" if sig != "s" else f"<'{value}'>"])
        if r.returncode:
            raise RuntimeError(r.stderr)

    def profile(self, p):
        self.set("system", "net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles", "ActiveProfile", "s", p)

    def battery(self, on):
        self.set("system", "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", "OnBattery", "boolean", "true" if on else "false")

    def gaming(self, n):
        self.set("session", "com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode", "ClientCount", "int32", str(n))

    def stop(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


class Nest:
    def __init__(self):
        self.proc = self.dir = self.sig = self.pid = None
        self.children = []

    def start(self):
        frag = OUT / "frag"
        frag.mkdir(exist_ok=True)
        tiers = "\n".join(f"[desktop.framerate.tiers.{t}]\ndesktop_hz = {d}" for t, d in TIERS.items())
        (frag / "60-fr-live-test.toml").write_text(
            f'[desktop.hyprland.mios-pacing]\npath = "{PACING_SO}"\n[desktop.hyprland.mios-pacing.config]\nrequire_vrr = false\n'
            f'[desktop.hyprland.hypr3d]\npath = "{H3D_SO}"\n{tiers}\n')
        (OUT / "tmp").mkdir(exist_ok=True)
        env = clean_env(MIOS_VENDOR_TOML=str(WT_SSOT), MIOS_USER_TOML_D=str(frag))
        env["XDG_RUNTIME_DIR"] = os.environ["XDG_RUNTIME_DIR"]
        env["WAYLAND_DISPLAY"] = os.environ["WAYLAND_DISPLAY"]
        env["HYPRLAND_INSTANCE_SIGNATURE"] = os.environ["HYPRLAND_INSTANCE_SIGNATURE"]
        # a private /tmp for the whole launcher tree: Hypr3D writes a fixed /tmp/hypr3d-status.txt (the primary's too)
        self.proc = subprocess.Popen(["bwrap", "--dev-bind", "/", "/", "--bind", str(OUT / "tmp"), "/tmp", "--", GEN, "nested-session", "--layout", "pip"],
                                     env=env, stdout=open(OUT / "launcher.log", "w"), stderr=subprocess.STDOUT)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError("launcher exited: " + (OUT / "launcher.log").read_text()[-800:])
            for d in Path(os.environ["XDG_RUNTIME_DIR"]).glob("mios-nested-desktop-*/session.json"):
                try:
                    j = json.loads(d.read_text())
                except ValueError:
                    continue
                # ours: the launcher runs in bwrap, so match the nested Hyprland's private /tmp instead of a pid
                hp = j["instance"]["pid"]
                try:
                    if os.readlink(f"/proc/{hp}/root") == "/" and Path(f"/proc/{hp}/root/tmp").samefile(OUT / "tmp"):
                        pass
                except OSError:
                    pass
                if (OUT / "tmp" / "mios-hypr3d-pad.bin").exists() and self._owned(hp):
                    self.dir, self.sig, self.pid = d.parent, j["instance"]["instance"], hp
                    self.wl = j["instance"]["wl_socket"]
                    self.parent_window = j["parent_window"]
                    time.sleep(3)
                    return
            time.sleep(0.3)
        raise RuntimeError("nested session did not publish session.json")

    def _owned(self, hpid):
        """The nested Hyprland descends from our launcher (bwrap -> mios-gen -> bwrap -> Hyprland)."""
        p = hpid
        for _ in range(8):
            try:
                st = open(f"/proc/{p}/stat").read()
            except OSError:
                return False
            p = int(st[st.rfind(")") + 2:].split()[1])
            if p == self.proc.pid:
                return True
        return False

    def env(self, **extra):
        return clean_env(XDG_RUNTIME_DIR=str(self.dir), HYPRLAND_INSTANCE_SIGNATURE=self.sig, WAYLAND_DISPLAY=self.wl, **extra)

    def hc(self, *a, timeout=20):
        return sh(["hyprctl", "-i", self.sig, *a], env=self.env(), timeout=timeout).stdout.strip()

    def opt(self, name):
        try:
            return json.loads(self.hc("-j", "getoption", name)).get("int")
        except ValueError:
            return None

    def monitor(self):
        return json.loads(self.hc("-j", "monitors"))[0]

    def pacing(self):
        try:
            return json.loads(self.hc("mios-pacing", "status"))
        except ValueError:
            return {}

    def spawn(self, args, name, **extra):
        p = subprocess.Popen(args, env=self.env(QT_QPA_PLATFORM="wayland", QT_NO_XDG_DESKTOP_PORTAL="1", GSETTINGS_BACKEND="memory", **extra),
                             stdout=open(OUT / f"{name}.log", "w"), stderr=subprocess.STDOUT)
        self.children.append(p)
        return p

    def stop(self):
        for p in self.children:
            p.terminate()
        for p in self.children:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.proc:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Daemon:
    def __init__(self, nest, bus):
        self.n, self.bus = nest, bus
        self.frag = OUT / "daemon.d"
        self.frag.mkdir(exist_ok=True)
        self.state_home = OUT / "state"
        self.p = None

    def env(self, **extra):
        return self.n.env(MIOS_VENDOR_TOML=str(WT_SSOT), MIOS_USER_TOML=str(OUT / "none.toml"), MIOS_USER_TOML_D=str(self.frag),
                          XDG_STATE_HOME=str(self.state_home), DBUS_SYSTEM_BUS_ADDRESS=self.bus.addr["system"],
                          DBUS_SESSION_BUS_ADDRESS=self.bus.addr["session"], **extra)

    def fragment(self, name, text):
        (self.frag / name).write_text(text)

    def start(self, **extra):
        self.log = open(OUT / "daemon.log", "a")
        self.p = subprocess.Popen([MIOSD, "framerate", "serve"], env=self.env(**extra), stdout=self.log, stderr=subprocess.STDOUT)
        time.sleep(2)

    def hup(self):
        self.p.send_signal(signal.SIGHUP)

    def cli(self, *a):
        return sh([MIOSD, "framerate", *a], env=self.env())

    def state(self):
        try:
            return json.loads((self.n.dir / "mios/framerate/state.json").read_text())
        except (OSError, ValueError):
            return {}

    def stop(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.p.kill()

    def text(self):
        return (OUT / "daemon.log").read_text(errors="replace")


def wait(pred, timeout=12.0, step=0.25):
    end = time.monotonic() + timeout
    v = pred()
    while not v and time.monotonic() < end:
        time.sleep(step)
        v = pred()
    return v


def sample(n, out_name, seconds=4.0):
    """Presented fps of the nested output and the wallpaper callbacks/s, after the plugin's averaging settles."""
    time.sleep(seconds)
    st = n.pacing()
    o = [x for x in st.get("outputs", []) if x["name"] == out_name]
    w = [x for x in st.get("wallpapers", []) if x.get("output") == out_name]
    return (o[0]["rendered_fps"] if o else None), (w[0]["callbacks_fps"] if w else None), st


def tier_checks(n, d, bus, out_name, max_hz):
    expect = {}
    for t, dhz in TIERS.items():
        gdiv, wdiv = (2, 2) if t == "gaming" else (3, 2)
        expect[t] = (dhz, dhz, dhz // gdiv, dhz // wdiv)
    expect["unlocked"] = (max_hz, 0, max_hz // 2, max_hz)
    steps = [("power-saver", lambda: bus.profile("power-saver")), ("performance", lambda: bus.profile("performance")),
             ("gaming", lambda: bus.gaming(1)), ("performance", lambda: bus.gaming(0)), ("battery", lambda: bus.battery(True)),
             ("unlocked", lambda: d.cli("set-unlocked", "on")), ("battery", lambda: d.cli("set-unlocked", "off"))]
    for want, act in steps:
        act()
        st = wait(lambda: d.state() if d.state().get("tier") == want else None, timeout=15)
        D, cap, G, W = expect[want]
        check(f"tier {want}: state", st and (st["desktop_hz"], st["cap_hz"], st["glass_fps"], st["wallpaper_fps"]) == (D, cap, G, W),
              {k: st.get(k) for k in ("tier", "desktop_hz", "cap_hz", "glass_fps", "wallpaper_fps", "wallpaper_paced")} if st else "no state")
        check(f"tier {want}: plugin cap", n.opt("plugin:mios_pacing:max_fps") == cap, n.opt("plugin:mios_pacing:max_fps"))
        g = n.opt("plugin:hyprglass:windows:live_resample_fps")
        check(f"tier {want}: glass rate", g == G and n.opt("plugin:hyprglass:layers:live_resample_fps") == G, g)
        fps, wcb, raw = sample(n, out_name)
        if cap:
            check(f"tier {want}: presented fps held at the cap", fps is not None and fps <= cap * 1.03 + 0.5 and fps >= cap * 0.8, f"{fps} for cap {cap}")
        else:   # negative control: uncapped, the nest presents at the parent's rate, well above the largest tier
            check(f"tier {want}: uncapped presents above every cap (control)", fps is not None and fps > max(TIERS.values()) * 1.15, fps)
        if wcb is not None:
            check(f"tier {want}: wallpaper callbacks at W", abs(wcb - W) <= max(1.5, 0.12 * W), f"{wcb}/s for W={W}")
        else:
            check(f"tier {want}: wallpaper surface present", False, raw.get("wallpapers"))
        (OUT / f"pacing-{want}.json").write_text(json.dumps(raw, indent=1))


def room_checks(n, d, bus):
    """Hypr3D: the running tier's D/G/W reach the room (status `pacing:` line) and follow a tier change."""
    status = OUT / "tmp" / "hypr3d-status.txt"
    out = n.hc("plugin", "load", H3D_SO)
    check("hypr3d candidate loads in the nest", "ok" in out.lower() or "loaded" in out.lower() or "Plugin hypr3d" in n.hc("plugin", "list"), out)
    d.hup()
    time.sleep(2)
    status.write_text("")
    n.hc("eval", 'local p=assert(hl.plugin.hypr3d); p.config({world={monitor="%s"}, player={flying=true}}); p.open()' % n.monitor()["name"])
    try:
        for want, act in (("performance", lambda: bus.profile("performance")), ("power-saver", lambda: bus.profile("power-saver"))):
            act()
            wait(lambda: d.state().get("tier") == want, timeout=15)
            D = TIERS[want]
            G, W = D // 3, D // 2

            def line():
                ls = [x for x in status.read_text(errors="replace").splitlines() if x.startswith("pacing:")]
                return ls[-1] if ls and f"max_fps={D} " in ls[-1] else None
            ln = wait(line, timeout=12)
            check(f"room {want}: max_fps/glass_fps/live_fps = D/G/W", ln and f"glass_fps={G} " in ln and f"live_fps={W} " in ln, ln)
            time.sleep(3)
            fl = [x for x in status.read_text(errors="replace").splitlines() if x.startswith("render:")]
            fps = float(re.search(r"fps=([0-9.]+)", fl[-1]).group(1)) if fl else None
            check(f"room {want}: room pump at most D", fps is not None and fps <= D * 1.1 + 1, f"{fps} for D={D}")
    finally:
        n.hc("eval", "if hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
        time.sleep(2)


def modeset_checks(n, d, bus):
    """mode_outputs + extended-vblank on the nested output (a virtual output: the EDID comes through the test hook).
    Hyprland takes the modeline only on DRM outputs; on this Wayland output it falls back to a custom mode at the
    modeline's size and refresh -- enough to drive the daemon's verify / 15 s confirm-or-revert / confirm / stop path."""
    m = n.monitor()
    name, base = m["name"], m["refreshRate"]
    edid = OUT / "nest.edid"
    edid.write_bytes(synth_edid(m["width"], m["height"]))
    # negative control first: modes = "advertised" never derives a modeline
    d.fragment("70-mode.toml", f'[desktop.framerate]\nmode_outputs = ["{name}"]\nmodes = "advertised"\nmodeset_hold_ms = 0\nmodeset_min_interval_s = 0\n')
    d.stop()
    d.start(MIOS_FRAMERATE_TEST_EDID=f"{name}={edid}")
    bus.profile("performance")
    time.sleep(6)
    st = d.state()
    check("advertised: no extended-vblank modeline is planned", "modeline" not in json.dumps(st), st.get("outputs"))
    # positive: extended-vblank -> the modeline at the output's size, D = 30 Hz
    d.fragment("70-mode.toml", f'[desktop.framerate]\nmode_outputs = ["{name}"]\nmodes = "extended-vblank"\nmodeset_hold_ms = 0\nmodeset_min_interval_s = 0\n')
    d.cli("confirm-mode", "--reset")
    d.hup()
    t0 = time.monotonic()
    st = wait(lambda: d.state() if "modeline" in str(d.state().get(f"mode.{name}", "")) else None, timeout=10)
    check("extended-vblank: the plan is a modeline for D", st is not None, (st or d.state()).get(f"mode.{name}"))
    log = wait(lambda: re.search(r"(verified after \d+ ms|not verified within \d+ ms)", d.text()), timeout=12)
    took = log.group(1) if log else None
    check("the daemon applied the modeline and judged it", took is not None, d.text()[-600:])
    if took and took.startswith("verified"):
        tv = time.monotonic()
        shown = n.monitor()["refreshRate"]
        check("the output shows D after the switch", abs(shown - TIERS["performance"]) < 0.5, shown)
        back = wait(lambda: abs(n.monitor()["refreshRate"] - base) < 0.5, timeout=25, step=0.2)
        dt = time.monotonic() - tv
        check("unconfirmed: the previous mode is back after mode_confirm_s (15 s)", back and 13.0 <= dt <= 19.0, f"{dt:.1f} s")
        check("unconfirmed: the daemon logs the decline", "not confirmed within the deadline" in d.text())
        st = wait(lambda: "declined" in d.cli("status").stdout or "no automatic modesets" in d.cli("status").stdout, timeout=3)
        check("unconfirmed: no more automatic modesets on the panel", st, d.cli("status").stdout[-300:])
        # confirmed path: reset, re-plan, confirm within the window; the mode stays past the deadline
        d.cli("confirm-mode", "--reset")
        d.hup()
        ok = wait(lambda: abs(n.monitor()["refreshRate"] - TIERS["performance"]) < 0.5, timeout=12)
        r = d.cli("confirm-mode")
        check("confirm-mode answers within the window", ok and r.returncode == 0, r.stdout + r.stderr)
        time.sleep(18)
        check("confirmed: the mode stays past the deadline", abs(n.monitor()["refreshRate"] - TIERS["performance"]) < 0.5, n.monitor()["refreshRate"])
        d.stop()
        back = wait(lambda: abs(n.monitor()["refreshRate"] - base) < 0.5, timeout=8)
        check("stopping the daemon restores the baseline mode", back and "restoring the mode it had before" in d.text(), n.monitor()["refreshRate"])
    elif took:
        check("not verified: reverted at once and blocklisted", "blocklisting" in d.text() and abs(n.monitor()["refreshRate"] - base) < 0.5, d.text()[-400:])
        d.stop()
    else:
        d.stop()
    (d.frag / "70-mode.toml").unlink(missing_ok=True)


def retina_checks(n):
    """Hypr3D Retina spawn at the nested output's scale: the plugin's live d1 / window scale / fit area against the
    independent oracle (tests/test-hypr3d-retina-spawn.py NestedStatus), landscape and portrait (transform 1)."""
    env = clean_env(MIOS_H3R_NEST_SIG=n.sig, MIOS_H3R_NEST_XDG=str(n.dir), MIOS_H3R_NEST_WL=n.wl,
                    MIOS_H3R_NEST_STATUS=str(OUT / "tmp" / "hypr3d-status.txt"), MIOS_H3R_NEW_SO=H3D_SO, MIOS_GEN=GEN,
                    MIOS_H3R_NEST_OUTPUT=n.monitor()["name"], MIOS_H3R_EVIDENCE=str(OUT / "retina"))
    (OUT / "retina").mkdir(exist_ok=True)
    m = n.monitor()
    for case, transform in (("landscape", 0), ("portrait", 1)):
        if int(n.monitor()["transform"]) != transform:
            n.hc("eval", "hl.monitor({output = '%s', mode = '%dx%d', position = '0x0', scale = %.6f, transform = %d})"
                 % (m["name"], m["width"], m["height"], m["scale"], transform))
            time.sleep(3)
        mon = n.monitor()
        r = sh([sys.executable, str(ROOT / "tests/test-hypr3d-retina-spawn.py"), "NestedStatus"], env=dict(env, MIOS_H3R_CASE=case), timeout=300)
        (OUT / "retina" / f"{case}-status.log").write_text(r.stdout + r.stderr)
        check(f"retina {case}: live spawn numbers match the oracle at scale {mon['scale']:.3f} ({mon['width']}x{mon['height']} t{mon['transform']})",
              r.returncode == 0, (r.stdout + r.stderr)[-400:])
    if int(n.monitor()["transform"]) != 0:
        n.hc("eval", "hl.monitor({output = '%s', mode = '%dx%d', position = '0x0', scale = %.6f, transform = 0})" % (m["name"], m["width"], m["height"], m["scale"]))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT, 0o700)
    before = primary_facts()
    n, bus, d = Nest(), None, None
    try:
        n.start()
        check("nested PiP session up (owned, private /tmp)", n.sig is not None, f"{n.dir} {n.monitor()['name']}")
        plugins = n.hc("plugin", "list")
        check("candidate mios-pacing loaded in the nest", "mios-pacing" in plugins, plugins[:200])
        out_name = n.monitor()["name"]
        max_hz = round(max([n.monitor()["refreshRate"]] + [0]))
        bus = Bus()
        d = Daemon(n, bus)
        d.start()
        n.spawn([QML, str(OUT / "anim.qml")], "anim") if (OUT / "anim.qml").write_text(ANIM) or True else None
        n.spawn([WALLPAPERD, "host"], "wallpaper")
        time.sleep(4)
        tier_checks(n, d, bus, out_name, max_hz)
        room_checks(n, d, bus)
        modeset_checks(n, d, bus)
        retina_checks(n)
    except Exception as e:  # noqa: BLE001 -- the run must always clean up and report
        check("harness", False, repr(e))
    finally:
        if d:
            d.stop()
        if bus:
            bus.stop()
        n.stop()
        after = primary_facts()
        check("primary compositor untouched (same pid, portal mounts, no lost clients)",
              after["pid"] == before["pid"] and after["portal_mounts"] == before["portal_mounts"] and after["clients"] >= before["clients"] - 0, (before, after))
        (OUT / "results.json").write_text(json.dumps({"results": results, "primary": [before, after]}, indent=1))
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(bad)}/{len(results)} passed; evidence {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
