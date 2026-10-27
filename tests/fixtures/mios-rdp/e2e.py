#!/usr/bin/python3 -I
"""Private native-server session for tests: mios-rdp-server on 127.0.0.1:<port> (test-auth build,
test certificate) attached to HEADLESS-MIOS-SCALETEST of the live Hyprland, a FreeRDP client in a
private Xvfb, an optional test window on the scaletest workspace. Guards before/after: fuse.portal
count, watchdog0, HDMI-A-1 mode/position, no SCALETEST left, default sink/source unchanged.
Library + CLI: e2e.py --label L --seconds N [--window bars|text|static|none] [-- extra xfreerdp args]"""
import glob, json, os, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
RT = "/run/user/1000"
OUT_NAME = "HEADLESS-MIOS-SCALETEST"
BIN = os.environ.get("MIOS_RDP_BIN", "/var/lib/mios-build/rdp-native/target/fast/mios-rdp-server")
BRIDGE = os.environ.get("ST_BRIDGE", os.path.join(HERE, "..", "mios-rdp-rfb-bridge"))


def live_env():
    best, bt = None, -1
    for lock in glob.glob(f"{RT}/hypr/*/hyprland.lock"):
        try:
            pid, wl = open(lock).read().split("\n")[:2]
            pid = int(pid)
            os.kill(pid, 0)
            env = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
            if any(e.startswith((b"WAYLAND_DISPLAY=", b"DISPLAY=")) and len(e.split(b"=", 1)[1]) for e in env):
                continue
            t = os.stat(lock).st_mtime
            if t > bt and os.path.exists(f"{RT}/{wl}"):
                best, bt = (os.path.basename(os.path.dirname(lock)), wl), t
        except (OSError, ValueError):
            continue
    e = dict(os.environ)
    e["HYPRLAND_INSTANCE_SIGNATURE"], e["WAYLAND_DISPLAY"] = best
    e["XDG_RUNTIME_DIR"] = RT
    e.pop("DISPLAY", None)
    return e


ENV = live_env()


def hc(*a):
    return subprocess.run(["hyprctl", *a], capture_output=True, text=True, env=ENV, timeout=5).stdout.strip()


def mons():
    try:
        return json.loads(hc("-j", "monitors", "all") or "[]")
    except ValueError:
        return []


def local_snapshot():
    return [(m["name"], m["width"], m["height"], round(m["refreshRate"]), m["scale"], m["x"], m["y"], m.get("transform"))
            for m in mons() if not m["name"].startswith("HEADLESS-MIOS-SCALETEST")]


def default_audio():
    out = {}
    for k in ("@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"):
        try:
            r = subprocess.run(["wpctl", "inspect", k], capture_output=True, text=True, timeout=5, env=ENV).stdout
            out[k] = next((l.split(",")[0].strip() for l in r.splitlines() if l.startswith("id ")), "?")
        except (OSError, subprocess.SubprocessError):
            out[k] = "?"
    return out


def guards():
    return {
        "portal": sum(1 for l in open("/proc/self/mountinfo") if "fuse.portal" in l),
        "watchdog": open("/sys/class/watchdog/watchdog0/state").read().strip(),
        "local": local_snapshot(),
        "audio": default_audio(),
        "scaletest": [m["name"] for m in mons() if m["name"].startswith(OUT_NAME)],
        "drive_mounts": sum(1 for l in open("/proc/self/mountinfo") if "fuse.mios-rdp-drive" in l),
    }


def write_config(d, port=18301, **over):
    cfg = {
        "listen": f"127.0.0.1:{port}", "operator_uid": 1000, "runtime_dir": RT,
        "stats_path": f"{d}/stats.json", "allow_loopback": True, "tailscale_cidrs": [], "allow_lan": False,
        "tls_source": "files", "cert_file": f"{d}/cert.pem", "key_file": f"{d}/key.pem",
        "tls_protocols": "TLSv1.2, TLSv1.3", "auth": "test", "test_user": "mios-rdp-test",
        "test_password_file": f"{d}/pw", "mode": "virtual", "virtual_output": OUT_NAME,
        "virtual_attach": "20000x20000", "virtual_workspace": "scaletest", "virtual_initial_mode": "1100x2080",
        "keep_virtual_output": False, "linger_seconds": 0, "target_hz": 90, "min_hz": 30,
        "refresh_pacing": "adaptive", "output_scale": True, "virtual_scale": "auto", "hints_dir": d,
        "primary_output": "HDMI-A-1", "keyboard_layout": "us",
        "display_service": ["python3", "-I", BRIDGE, "--display-service"],
        "input": False,
        "native": {"frames_in_flight": 3, "max_width": 4096, "max_height": 4096},
        "redirect": {"clipboard": "off", "touch": False, "pen": False},
        "naming": {"touch_device": "MiOS Remote Touch test", "pen_device": "MiOS Remote Pen test"},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    json.dump(cfg, open(f"{d}/native.json", "w"), indent=1)
    return cfg


def ensure_cert(d):
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(f"{d}/key.pem"):
        old = os.umask(0o077)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "3",
                        "-subj", "/CN=mios-rdp-test", "-keyout", f"{d}/key.pem", "-out", f"{d}/cert.pem"],
                       check=True, capture_output=True)
        with open(f"{d}/pw", "w") as f:
            f.write(os.urandom(18).hex())
        os.umask(old)


class Session:
    def __init__(self, d, label, port=18301, server_env=None):
        self.d, self.label, self.port = d, label, port
        self.out = f"{d}/run-{label}"
        os.makedirs(self.out, exist_ok=True)
        self.server = self.xvfb = self.client = self.window = None
        self.server_env = server_env or {}
        self.disp = None

    def start_server(self):
        e = dict(ENV)
        e.setdefault("MIOS_RDP_LOG", "info")
        e.update(self.server_env)
        self.slog = open(f"{self.out}/server.log", "w")
        self.server = subprocess.Popen([BIN, "--config", f"{self.d}/native.json"], stdout=self.slog,
                                       stderr=subprocess.STDOUT, env=e)
        for _ in range(100):
            if f"127.0.0.1:{self.port}" in subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout:
                return
            if self.server.poll() is not None:
                raise SystemExit(f"server exited: {open(self.out + '/server.log').read()[-2000:]}")
            time.sleep(0.1)
        raise SystemExit("server did not listen")

    def start_xvfb(self, size="2400x2400"):
        for n in range(90, 99):
            if not os.path.exists(f"/tmp/.X11-unix/X{n}") and not os.path.exists(f"/tmp/.X{n}-lock"):
                self.disp = f":{n}"
                break
        os.makedirs(f"{self.out}/fb", exist_ok=True)
        self.xvfb = subprocess.Popen(["Xvfb", self.disp, "-screen", "0", f"{size}x24", "-nolisten", "tcp",
                                      "-fbdir", f"{self.out}/fb"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)

    def start_client(self, args, user="mios-rdp-test", password=None, prog="xfreerdp", log_name="client.log"):
        pw = password if password is not None else open(f"{self.d}/pw").read().strip()
        e = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE")}
        e["DISPLAY"] = self.disp
        self.clog = open(f"{self.out}/{log_name}", "w")
        cmd = [prog, f"/v:127.0.0.1:{self.port}", f"/u:{user}", f"/p:{pw}", "/cert:ignore", *args]
        c = subprocess.Popen(cmd, stdout=self.clog, stderr=subprocess.STDOUT, env=e)
        if self.client is None:
            self.client = c
        return c

    def stats(self):
        try:
            return json.load(open(f"{self.d}/stats.json"))
        except (OSError, ValueError):
            return {}

    def wait_attached(self, timeout=15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.stats()
            if s.get("attached") and s.get("time", 0) > t0:
                return s
            time.sleep(0.25)
        return None

    def wait_window_rule(self):
        hc("eval", 'hl.window_rule({name = "mios-scaletest-ws", match = {class = "^(mios-scaletest)$"}, '
                   'workspace = "name:scaletest silent"})')

    def spawn_window(self, kind):
        self.wait_window_rule()
        script = os.path.join(HERE, f"win-{kind}.py")
        self.window = subprocess.Popen(["alacritty", "--class", "mios-scaletest", "-e", "python3", "-I", script],
                                       env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

    def xwd(self):
        """Xvfb framebuffer (XWD in -fbdir) as a PIL image."""
        from PIL import Image
        raw = open(f"{self.out}/fb/Xvfb_screen0", "rb").read()
        hdr = [int.from_bytes(raw[i:i + 4], "big") for i in range(0, 100, 4)]
        header_size, w, h, bpl, ncolors = hdr[0], hdr[4], hdr[5], hdr[12], hdr[19]
        off = header_size + ncolors * 12
        return Image.frombuffer("RGBX", (w, h), raw[off:off + bpl * h], "raw", "BGRX", bpl, 1).convert("RGB")

    def stop(self):
        for p in (self.client,):
            if p and p.poll() is None:
                p.send_signal(signal.SIGINT)
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        if self.window:
            self.window.terminate()
            try:
                self.window.wait(5)
            except subprocess.TimeoutExpired:
                self.window.kill()
        for c in json.loads(hc("-j", "clients") or "[]"):
            if c.get("class") == "mios-scaletest":
                try:
                    os.kill(c["pid"], signal.SIGTERM)
                except OSError:
                    pass
        time.sleep(0.5)
        if self.server and self.server.poll() is None:
            self.server.send_signal(signal.SIGTERM)
            try:
                self.server.wait(15)
            except subprocess.TimeoutExpired:
                self.server.kill()
        if self.xvfb:
            self.xvfb.terminate()
            self.xvfb.wait(5)


def main():
    a = sys.argv[1:]
    def opt(k, d=None):
        if k in a:
            i = a.index(k)
            v = a[i + 1]
            del a[i:i + 2]
            return v
        return d
    label = opt("--label", "run")
    secs = float(opt("--seconds", "15"))
    window = opt("--window", "none")
    d = opt("--dir", os.environ.get("MIOS_RDP_LIVE_DIR") or tempfile.mkdtemp(prefix="mios-rdp-e2e-"))
    extra = a[a.index("--") + 1:] if "--" in a else []
    ensure_cert(d)
    if not os.path.exists(f"{d}/native.json"):
        write_config(d)
    before = guards()
    s = Session(d, label)
    res = {}
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(extra or ["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"])
        att = s.wait_attached()
        res["attached"] = bool(att)
        if window != "none":
            s.spawn_window(window)
        samples = []
        t0 = time.time()
        while time.time() - t0 < secs:
            time.sleep(2.0)
            samples.append(s.stats())
        res["samples"] = samples
    finally:
        s.stop()
        after = guards()
        res["guards"] = {"before": before, "after": after,
                         "ok": before["portal"] == after["portal"] and after["watchdog"] == "inactive"
                         and before["local"] == after["local"] and not after["scaletest"]
                         and before["audio"] == after["audio"]}
        json.dump(res, open(f"{s.out}/result.json", "w"), indent=1)
        print(json.dumps({"label": label, "attached": res.get("attached"), "guards_ok": res["guards"]["ok"],
                          "fps": [x.get("fps") for x in res.get("samples", [])],
                          "kbps": [x.get("bitrate_kbps") for x in res.get("samples", [])]}))


if __name__ == "__main__":
    main()
