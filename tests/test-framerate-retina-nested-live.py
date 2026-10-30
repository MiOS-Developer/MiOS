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
# AI-functions: Nest, Bus, Daemon, synth_edid, tier_checks, room_checks, modeline_for, modeset_checks, apply_script_checks, retina_checks
"""Live frame-rate + Retina checks in an owned nested PiP session.

Primary compositor: only the launcher's own PiP window appears there; nothing is dispatched to it. The nested
compositor gets the candidate plugins; the daemon runs against the nested instance with a private runtime, state
home, user tier and private buses (nothing activatable). The nested Hyprland is SIGTERMed at the end.

Env: MIOS_LIVE_NESTED=1; MIOS_GEN (candidate launcher; needs extended-vblank), MIOS_FR_MIOSD, MIOS_FR_WALLPAPERD,
MIOS_FR_PACING_SO, MIOS_FR_HYPR3D_SO (default: installed); MIOS_LIVE_OUT (outside /tmp: the nest's /tmp is private);
MIOS_FR_APPLY_SCRIPT (optional: the lead's DP-3 apply script, rehearsed on the nested output).
The nested window's frame callbacks follow the PRIMARY's own rate (its cap, 60 Hz at power-saver), so the tiers here
are scaled below it (18/24/30/36 Hz; divisors are the vendor ones); the vendor table itself is unit-tested in
tests/test-framerate.py.
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
T0 = time.time()
TIERS = {"power-saver": 18, "balanced": 18, "battery": 24, "performance": 30, "gaming": 36}   # scaled (see above)
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
    fr = sh(["systemctl", "--user", "show", "-p", "MainPID", "--value", "mios-framerate.service"]).stdout.strip()
    return {"pid": pid, "framerate_pid": fr, "portal_mounts": open("/proc/self/mountinfo").read().count("fuse.portal"),
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
        (self.frag / "60-fr-live-test.toml").write_text((OUT / "frag" / "60-fr-live-test.toml").read_text())
        (OUT / "none-host.toml").write_text("")
        self.state_home = OUT / "state"
        self.p = None

    def env(self, **extra):
        # every tier private: the worktree vendor SSOT, an empty host tier, no user file, this run's fragment dir (the
        # scaled tier table + the plugin paths, the same fragment the nested session loads; set-unlocked writes here)
        return self.n.env(MIOS_VENDOR_TOML=str(WT_SSOT), MIOS_HOST_TOML=str(OUT / "none-host.toml"),
                          MIOS_USER_TOML=str(OUT / "none.toml"), MIOS_USER_TOML_D=str(self.frag),
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

    def unlocked(self, on):
        """set-unlocked into this run's fragment dir WITHOUT its reload (that signals the primary's
        mios-framerate.service through the user manager), then SIGHUP this run's own daemon."""
        r = self.cli("set-unlocked", "on" if on else "off", "--no-reload")
        if r.returncode:
            raise RuntimeError(r.stderr)
        self.hup()

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
    """Presented fps of the nested output, the wallpaper callbacks/s (after the plugin's averaging settles) and the
    wallpaper frame callbacks the plugin HELD during the window (its throttle at work)."""
    def wp(st):
        w = [x for x in st.get("wallpapers", []) if x.get("output") == out_name]
        return w[0] if w else {}
    time.sleep(seconds / 2)
    held0 = wp(n.pacing()).get("held")
    time.sleep(seconds / 2)
    st = n.pacing()
    o = [x for x in st.get("outputs", []) if x["name"] == out_name]
    w = wp(st)
    held = (w["held"] - held0) if (held0 is not None and "held" in w) else None
    return (o[0]["rendered_fps"] if o else None), w.get("callbacks_fps"), held, st


def tier_checks(n, d, bus, out_name, max_hz):
    expect = {}
    for t, dhz in TIERS.items():
        gdiv, wdiv = (2, 2) if t == "gaming" else (3, 2)
        expect[t] = (dhz, dhz, dhz // gdiv, dhz // wdiv)
    expect["unlocked"] = (max_hz, 0, max_hz // 2, max_hz)
    steps = [("power-saver", lambda: bus.profile("power-saver")), ("performance", lambda: bus.profile("performance")),
             ("gaming", lambda: bus.gaming(1)), ("performance", lambda: bus.gaming(0)), ("battery", lambda: bus.battery(True)),
             ("unlocked", lambda: d.unlocked(True)), ("battery", lambda: d.unlocked(False))]
    for want, act in steps:
        act()
        st = wait(lambda: d.state() if d.state().get("tier") == want else None, timeout=15)
        D, cap, G, W = expect[want]
        check(f"tier {want}: state", st and (st["desktop_hz"], st["cap_hz"], st["glass_fps"], st["wallpaper_fps"]) == (D, cap, G, W),
              {k: st.get(k) for k in ("tier", "desktop_hz", "cap_hz", "glass_fps", "wallpaper_fps", "wallpaper_paced")} if st else "no state")
        check(f"tier {want}: plugin cap", n.opt("plugin:mios_pacing:max_fps") == cap, n.opt("plugin:mios_pacing:max_fps"))
        g = n.opt("plugin:hyprglass:windows:live_resample_fps")
        check(f"tier {want}: glass rate", g == G and n.opt("plugin:hyprglass:layers:live_resample_fps") == G, g)
        fps, wcb, held, raw = sample(n, out_name)
        if cap:
            check(f"tier {want}: presented fps held at the cap", fps is not None and fps <= cap * 1.03 + 0.5 and fps >= cap * 0.8, f"{fps} for cap {cap}")
        else:   # negative control: uncapped, the nest presents at the parent's rate, above the largest tier
            check(f"tier {want}: uncapped presents above every cap (control)", fps is not None and fps > max(TIERS.values()) * 1.1, fps)
        if wcb is None:
            check(f"tier {want}: wallpaper surface present", False, raw.get("wallpapers"))
        elif cap:
            check(f"tier {want}: wallpaper callbacks at W (the plugin holds the rest)", abs(wcb - W) <= max(1.5, 0.12 * W) and (held or 0) > 0,
                  f"{wcb}/s for W={W}, held {held}")
        else:   # negative control: W = D = the output's maximum (not D/2): the wallpaper presents at the full rate, far
            # above every paced W (the plugin still trims Qt's doubled commits down to W, so `held` is not zero)
            check(f"tier {want}: wallpaper at the full rate W = D (control)", abs(wcb - W) <= max(1.5, 0.12 * W) and wcb > max(TIERS.values()) // 2 * 1.5,
                  f"{wcb}/s for W={W}, held {held}")
        (OUT / f"pacing-{want}.json").write_text(json.dumps(raw, indent=1))


def room_checks(n, d, bus):
    """Hypr3D: the running tier's D/G/W reach the room (status `pacing:` line) and follow a tier change."""
    status = OUT / "tmp" / "hypr3d-status.txt"
    out = n.hc("plugin", "load", H3D_SO)
    check("hypr3d candidate loads in the nest", "ok" in out.lower() or "loaded" in out.lower() or "Plugin hypr3d" in n.hc("plugin", "list"), out)
    d.hup()
    time.sleep(2)
    bus.battery(False)   # battery outranks the profile: back on mains, the profile tiers below decide
    wait(lambda: d.state().get("tier") == "power-saver", timeout=15)
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


def modeline_for(n, hz):
    """The independent CLI derivation (`miosd framerate modeline --edid`) of the extended-vblank timing for the nested
    output's size from the synthetic EDID: what the daemon must plan."""
    m = n.monitor()
    r = sh([MIOSD, "framerate", "modeline", m["name"], "--edid", str(OUT / "nest.edid"), "--size", f"{m['width']}x{m['height']}", "--hz", str(hz), "--json"],
           env=clean_env(MIOS_VENDOR_TOML=str(WT_SSOT), MIOS_HOST_TOML=str(OUT / "none-host.toml"), MIOS_USER_TOML=str(OUT / "none.toml"),
                         MIOS_USER_TOML_D=str(OUT / "daemon.d")))
    return json.loads(r.stdout)["modeline"] if r.returncode == 0 else None


def modeset_checks(n, d, bus):
    """mode_outputs + extended-vblank on the nested output (a virtual output: its EDID comes through the test hook).
    Hyprland takes a modeline only on a DRM output ("Tried to set custom modeline on non-DRM output") and a Wayland-
    backend output keeps the parent's 60 Hz, so here the daemon's planned modeline can never verify: the checks are the
    plan (against the CLI derivation), the switch, the unverified path (reverted at once, blocklisted, never retried)
    and the advertised negative control. The verified 15 s confirm-or-revert path needs a DRM panel (the lead's DP-3
    apply script); its logic is unit-tested in plan.rs."""
    m = n.monitor()
    name, base = m["name"], m["refreshRate"]
    edid = OUT / "nest.edid"
    edid.write_bytes(synth_edid(m["width"], m["height"]))
    D = TIERS["performance"]
    # negative control first: modes = "advertised" never derives a modeline
    d.fragment("70-mode.toml", f'[desktop.framerate]\nmode_outputs = ["{name}"]\nmodes = "advertised"\nmodeset_hold_ms = 0\nmodeset_min_interval_s = 0\n')
    d.stop()
    d.start(MIOS_FRAMERATE_TEST_EDID=f"{name}={edid}")
    bus.profile("performance")
    wait(lambda: d.state().get("tier") == "performance", timeout=15)
    time.sleep(4)
    check("advertised: no extended-vblank modeline is planned", "modeline" not in d.text() and "modeline" not in json.dumps(d.state()), d.state().get("outputs"))
    # positive: extended-vblank -> the modeline at the output's size for D
    want = modeline_for(n, D)
    check(f"the CLI derives a {D} Hz extended-vblank modeline for the nested size", want and want.startswith("modeline "), want)
    mark = len(d.text())
    d.fragment("70-mode.toml", f'[desktop.framerate]\nmode_outputs = ["{name}"]\nmodes = "extended-vblank"\nmodeset_hold_ms = 0\nmodeset_min_interval_s = 0\n')
    d.cli("confirm-mode", "--reset")
    d.hup()
    log = wait(lambda: re.search(r"(verified after \d+ ms|not verified within \d+ ms)", d.text()[mark:]), timeout=15)
    new = d.text()[mark:]
    check("extended-vblank: the daemon switched to the CLI's modeline for D", want and f"-> {want} (a modeset" in new, new[-500:])
    took = log.group(1) if log else None
    check("the daemon judged the switch within revert_timeout_ms", took is not None, new[-400:])
    if took and took.startswith("not verified"):
        back = wait(lambda: abs(n.monitor()["refreshRate"] - base) < 0.5, timeout=6)
        check("not verified (a non-DRM output): reverted at once and blocklisted", back and f"blocklisting {want}" in d.text()[mark:], d.text()[mark:][-400:])
        mark2 = len(d.text())
        d.hup()
        time.sleep(5)
        again = d.text()[mark2:]
        check("a blocklisted modeline is never retried (control)", f"-> {want}" not in again, again[-300:])
    elif took:
        check("verified on this output (a DRM-backed nest?): the output shows D", abs(n.monitor()["refreshRate"] - D) < 0.5, n.monitor()["refreshRate"])
    d.stop()
    back = wait(lambda: abs(n.monitor()["refreshRate"] - base) < 0.5, timeout=8)
    check("after the daemon stops the output shows its baseline mode", back, n.monitor()["refreshRate"])
    (d.frag / "70-mode.toml").unlink(missing_ok=True)


def apply_script_checks(n, d):
    """MIOS_FR_APPLY_SCRIPT (the lead's apply-dp3-90hz.sh) rehearsed on the NESTED output with the synthetic EDID:
    `check` is read-only and prints the CLI's modeline; `apply` arms the transient revert timer FIRST, then switches; a
    non-DRM output cannot show the modeline, so the script's own verification fails and it reverts at once (the timer
    then finds the revert done and does nothing); `confirm` after a revert is refused (the control). The verified +
    confirm path needs the real DRM panel."""
    script = os.environ.get("MIOS_FR_APPLY_SCRIPT")
    if not script:
        return
    m = n.monitor()
    name, base = m["name"], m["refreshRate"]
    hz = TIERS["performance"]
    env = d.env(MIOS_FRAMERATE_TEST_EDID=f"{name}={OUT / 'nest.edid'}")
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={os.environ['XDG_RUNTIME_DIR']}/bus"   # the user manager: the timer
    timeout = 8
    args = ["--output", name, "--hz", str(hz), "--miosd", MIOSD, "--timeout", str(timeout)]
    want = modeline_for(n, hz)
    r = sh(["bash", script, "check", *args], env=env)
    check("apply script: check is read-only and prints the CLI's modeline", r.returncode == 0 and want and want in r.stdout and abs(n.monitor()["refreshRate"] - base) < 0.5,
          (r.stdout + r.stderr)[-400:])
    adir = OUT / "apply-rehearsal"
    t0 = time.monotonic()
    r = sh(["bash", script, "apply", *args, "--dir", str(adir)], env=env, timeout=40)
    log = (adir / "log").read_text() if (adir / "log").exists() else ""
    unit = (adir / "unit").read_text().strip() if (adir / "unit").exists() else ""
    check("apply script: the revert timer is armed before the modeset", unit and log.find("revert armed") >= 0 and log.find("revert armed") < log.find("applying:"), log[-400:])
    check("apply script: unverified on a non-DRM output -> reverted at once, rc 1", r.returncode == 1 and (adir / "reverted").exists() and "NOT verified" in log
          and abs(n.monitor()["refreshRate"] - base) < 0.5, (r.stdout + r.stderr)[-300:])
    c = sh(["bash", script, "confirm", str(adir)], env=env)
    check("apply script: confirm after the revert is refused (control)", c.returncode != 0 and "too late" in c.stderr, c.stdout + c.stderr)
    wait(lambda: "already reverted" in ((adir / "log").read_text() if (adir / "log").exists() else ""), timeout=timeout + 8 - (time.monotonic() - t0))
    log = (adir / "log").read_text()
    active = sh(["systemctl", "--user", "is-active", f"{unit}.timer"], env=env).stdout.strip()
    check("apply script: the timer fired, found the revert done and left nothing behind", "already reverted" in log and active != "active", f"{active} {log[-300:]}")


# Retina in the nested PiP session: its output at the size and scale the session has. Both are the PARENT window's
# (the PiP on the primary): a mode, scale (the session zoom included) or transform requested from inside a Wayland-
# backend output is reported by hyprctl but not committed (the room keeps the parent-sized output: evidence in the lane
# report), and headless outputs get no swapchain in the nest; so a portrait/mobile-shaped viewport needs a portrait
# PiP, which only the primary's PiP gravity can give. Rotated and phone (1170x2532 @3, mobile) outputs are the
# oracle/CLI tiers of tests/test-hypr3d-retina-spawn.py.
def retina_checks(n):
    """Hypr3D Retina spawn in the nested room at the nested output's scale: NestedStatus (the plugin's live d1 / window
    scale / fit area against the independent oracle; the stock keys are its negative control) and NestedToggleStatus
    (the real `mios-gen hyprland-3d-toggle`: mios-gen's per-output plan reaches the room; criterion "fixed" = control)."""
    (OUT / "retina").mkdir(exist_ok=True)
    out = n.monitor()["name"]
    mon = n.monitor()
    env = clean_env(MIOS_H3R_NEST_SIG=n.sig, MIOS_H3R_NEST_XDG=str(n.dir), MIOS_H3R_NEST_WL=n.wl,
                    MIOS_H3R_NEST_STATUS=str(OUT / "tmp" / "hypr3d-status.txt"), MIOS_H3R_NEW_SO=H3D_SO, MIOS_GEN=GEN,
                    MIOS_H3R_NEST_OUTPUT=out, MIOS_H3R_EVIDENCE=str(OUT / "retina"), MIOS_H3R_CASE="nested-pip",
                    MIOS_H3R_SSOT=str(WT_SSOT))
    r = sh([sys.executable, str(ROOT / "tests/test-hypr3d-retina-spawn.py"), "-v", "NestedStatus", "NestedToggleStatus"], env=env, timeout=600)
    (OUT / "retina" / "nested-pip-status.log").write_text(r.stdout + r.stderr)
    ran = re.search(r"Ran (\d+) test", r.stderr)
    ev = OUT / "retina" / "nested-pip-toggle-status-mios.toml.txt"
    verdict = re.search(r"mobile=(\w+)", ev.read_text()) if ev.exists() else None
    check(f"retina: plugin + mios-gen toggle numbers match the oracle on {out} {mon['width']}x{mon['height']} @{float(mon['scale']):.3f} "
          f"(logical {mon['width'] / float(mon['scale']):.0f}x{mon['height'] / float(mon['scale']):.0f}, mobile={verdict.group(1) if verdict else '?'})",
          r.returncode == 0 and ran and int(ran.group(1)) == 4 and "skipped" not in r.stderr, (r.stdout + r.stderr)[-500:])


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
        apply_script_checks(n, d)
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
        # the primary is shared with other running work (its clients and portal mounts come and go on their own):
        # the assertion is the compositor itself; the counts are recorded as evidence only
        check("primary compositor untouched (same pid)", after["pid"] == before["pid"] and after["pid"], (before, after))
        check("primary's mios-framerate.service was not signalled by this run",
              after["framerate_pid"] == before["framerate_pid"] and "Reloading" not in sh(["journalctl", "--user", "-u", "mios-framerate.service", "--since", f"@{int(T0)}", "-q", "--no-pager"]).stdout
              and "SSOT reloaded" not in sh(["journalctl", "--user", "-u", "mios-framerate.service", "--since", f"@{int(T0)}", "-q", "--no-pager"]).stdout,
              (before["framerate_pid"], after["framerate_pid"]))
        (OUT / "results.json").write_text(json.dumps({"results": results, "primary": [before, after]}, indent=1))
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(bad)}/{len(results)} passed; evidence {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
