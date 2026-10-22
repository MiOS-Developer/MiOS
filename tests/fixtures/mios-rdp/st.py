#!/usr/bin/python3 -I
"""SCALETEST harness: keep HEADLESS-MIOS-SCALETEST alive (display service) while a command runs.
usage: st.py [--window motion|text|none] [--seconds N] -- cmd args...   (cmd gets $ST_OUTPUT)"""
import json, os, subprocess, sys, time, glob, signal

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.environ.get("ST_BRIDGE", os.path.join(HERE, "..", "mios-rdp-rfb-bridge"))
RT = "/run/user/1000"
OUT = "HEADLESS-MIOS-SCALETEST"

def live_env():
    best, bt = None, -1
    for lock in glob.glob(f"{RT}/hypr/*/hyprland.lock"):
        try:
            pid, wl = open(lock).read().split("\n")[:2]
            pid = int(pid); os.kill(pid, 0)
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
    return e

ENV = live_env()

def hc(*a):
    return subprocess.run(["hyprctl", *a], capture_output=True, text=True, env=ENV, timeout=5).stdout.strip()

def mons():
    return json.loads(hc("-j", "monitors", "all") or "[]")

def snapshot():
    return [(m["name"], m["width"], m["height"], round(m["refreshRate"]), m["scale"], m["x"], m["y"]) for m in mons()
            if not m["name"].startswith("HEADLESS-MIOS-SCALETEST")]

def main():
    args = sys.argv[1:]
    window = "none"
    if "--window" in args:
        i = args.index("--window"); window = args[i + 1]; del args[i:i + 2]
    w, h = 1100, 2080
    if "--size" in args:
        i = args.index("--size"); w, h = map(int, args[i + 1].split("x")); del args[i:i + 2]
    keep = "--keep" in args
    if keep:
        args.remove("--keep")
    cmd = args[args.index("--") + 1:] if "--" in args else []
    before = snapshot()
    cfgp = os.path.join(HERE, "ds.json")
    json.dump({"mode": "virtual", "virtual_output": OUT, "virtual_attach": "20000x20000",
               "virtual_workspace": "scaletest", "virtual_initial_mode": f"{w}x{h}", "keep_virtual_output": False,
               "linger_seconds": 0, "target_hz": 90, "min_hz": 30, "refresh_pacing": "fixed", "output_scale": True,
               "virtual_scale": "auto", "runtime_dir": RT, "hints_dir": HERE, "primary_output": "HDMI-A-1"}, open(cfgp, "w"))
    ds = subprocess.Popen(["python3", "-I", BRIDGE, "--display-service", "--config", cfgp], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, env=ENV)
    def req(op, **kw):
        kw.update(op=op, id=int(time.time() * 1000))
        ds.stdin.write(json.dumps(kw) + "\n"); ds.stdin.flush()
        while True:
            l = ds.stdout.readline()
            if not l:
                raise SystemExit("display service died")
            d = json.loads(l)
            if d.get("id") == kw["id"]:
                return d
    win = None
    rc = 1
    try:
        a = req("attach", width=w, height=h)
        print("attach:", a, file=sys.stderr)
        hc("eval", 'hl.window_rule({name = "mios-scaletest-ws", match = {class = "^(mios-scaletest)$"}, workspace = "name:scaletest silent"})')
        if window != "none":
            script = os.path.join(HERE, f"win-{window}.py")
            win = subprocess.Popen(["alacritty", "--class", "mios-scaletest", "-e", "python3", "-I", script], env=ENV,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
        e = dict(ENV); e["ST_OUTPUT"] = OUT
        rc = subprocess.call(cmd, env=e) if cmd else 0
    finally:
        if win:
            win.terminate()
            try: win.wait(5)
            except subprocess.TimeoutExpired: win.kill()
        for c in json.loads(hc("-j", "clients") or "[]"):
            if c.get("class") == "mios-scaletest":
                os.kill(c["pid"], signal.SIGTERM)
        if not keep:
            try:
                req("detach"); req("quit")
            except Exception as ex:
                print("cleanup:", ex, file=sys.stderr)
        try: ds.wait(10)
        except subprocess.TimeoutExpired: ds.kill()
        after = snapshot()
        left = [m["name"] for m in mons() if m["name"] == OUT]
        print(json.dumps({"local_outputs_unchanged": before == after, "before": before, "after": after,
                          "scaletest_left": bool(left),
                          "watchdog": open("/sys/class/watchdog/watchdog0/state").read().strip()}), file=sys.stderr)
    sys.exit(rc)

main()
