#!/usr/bin/python3 -I
"""C1/C2 clipboard, tier 1: the native server attached to a private nested Hyprland (its own seat and
clipboard), FreeRDP +clipboard in Xvfb; X side via xclip.py (GTK4/X11), Wayland side via wl-copy/wl-paste
in the nested session. The operator's live clipboard is only fingerprinted (types + SHA-256) before/after."""
import hashlib, json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402
from nest import Nest  # noqa: E402
from PIL import Image  # noqa: E402

H = e2e.HERE

def live_clip_fingerprint():
    t = subprocess.run(["wl-paste", "-l"], capture_output=True, text=True, env=e2e.ENV, timeout=5).stdout.split()
    h = ""
    if any(x.startswith("text/plain") for x in t):
        b = subprocess.run(["wl-paste", "-n", "-t", "text/plain"], capture_output=True, env=e2e.ENV, timeout=5).stdout
        h = hashlib.sha256(b).hexdigest()
    return {"types": sorted(t), "text_sha256": h}

def xclip(s, *args, timeout=10):
    e = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY",)}
    e["DISPLAY"] = s.disp
    return subprocess.run([sys.executable, "-I", f"{H}/xclip.py", *args], capture_output=True, text=True, env=e, timeout=timeout)

def xclip_bg(s, *args):
    e = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY",)}
    e["DISPLAY"] = s.disp
    return subprocess.Popen([sys.executable, "-I", f"{H}/xclip.py", *args], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def make_png(path, w, h, seed):
    im = Image.new("RGBA", (w, h))
    im.putdata([((x * seed) % 256, (y * 3 + seed) % 256, (x + y) % 256, 255) for y in range(h) for x in range(w)])
    im.save(path)

def same_pixels(a, b):
    A, Bi = Image.open(a).convert("RGBA"), Image.open(b).convert("RGBA")
    return A.size == Bi.size and list(A.getdata()) == list(Bi.getdata())

def run_case(nest, d, label, redirect):
    e2e.write_config(d, hypr_instance=nest.sig, virtual_attach="0x2000", primary_output="WAYLAND-1",
                     redirect=redirect)
    s = e2e.Session(d, label, server_env={"WAYLAND_DISPLAY": nest.wl, "HYPRLAND_INSTANCE_SIGNATURE": nest.sig})
    ne = nest.env()
    r = {}
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "+clipboard"])
        r["attached"] = bool(s.wait_attached())
        time.sleep(1.0)
        # host -> client: text
        subprocess.run(["wl-copy", "MiOS host text é 你"], env=ne, timeout=5)
        time.sleep(1.0)
        r["h2c_text"] = xclip(s, "get-text").stdout.strip()
        # host -> client: HTML
        subprocess.run(["wl-copy", "-t", "text/html", "<b>MiOS</b> <i>html</i>"], env=ne, timeout=5)
        time.sleep(1.0)
        r["h2c_html_targets"] = xclip(s, "targets").stdout.strip()
        r["h2c_html"] = xclip(s, "get-html").stdout.strip()
        # host -> client: PNG (-> CF_DIB/PNG -> client texture)
        make_png(f"{s.out}/h.png", 37, 23, 7)
        with open(f"{s.out}/h.png", "rb") as f:
            subprocess.run(["wl-copy", "-t", "image/png"], stdin=f, env=ne, timeout=5)
        time.sleep(1.0)
        g = xclip(s, "get-png", f"{s.out}/h-got.png")
        r["h2c_png"] = {"result": g.stdout.strip(), "pixel_equal": os.path.exists(f"{s.out}/h-got.png") and same_pixels(f"{s.out}/h.png", f"{s.out}/h-got.png")}
        # client -> host: text
        p = xclip_bg(s, "set-text", "MiOS client text ß 😀", "4")
        time.sleep(1.5)
        r["c2h_text"] = subprocess.run(["wl-paste", "-n", "-t", "text/plain;charset=utf-8"], capture_output=True, env=ne, timeout=8).stdout.decode(errors="replace")
        p.wait(10)
        # client -> host: PNG
        make_png(f"{s.out}/c.png", 41, 19, 11)
        p = xclip_bg(s, "set-png", f"{s.out}/c.png", "5")
        time.sleep(1.5)
        r["c2h_png_types"] = subprocess.run(["wl-paste", "-l"], capture_output=True, text=True, env=ne, timeout=5).stdout.split()
        b = subprocess.run(["wl-paste", "-t", "image/png"], capture_output=True, env=ne, timeout=8).stdout
        with open(f"{s.out}/c-got.png", "wb") as f:
            f.write(b)
        r["c2h_png"] = {"bytes": len(b), "pixel_equal": len(b) > 0 and same_pixels(f"{s.out}/c.png", f"{s.out}/c-got.png")}
        p.wait(10)
        r["types_after_c2h"] = subprocess.run(["wl-paste", "-l"], capture_output=True, text=True, env=ne, timeout=5).stdout.split()
        r["stats_clipboard"] = s.stats().get("clipboard")
    finally:
        s.stop()
    return r

def main():
    d = os.path.join(H, "..", "t-clip")
    e2e.ensure_cert(d)
    before = e2e.guards()
    live_before = live_clip_fingerprint()
    n = Nest()
    res = {"nest": n.start(os.path.join(d, "nest.log"))}
    try:
        res["both"] = run_case(n, d, "clip-both", {"clipboard": "both", "touch": False, "pen": False})
        res["to_client"] = run_case(n, d, "clip-toclient", {"clipboard": "to-client", "touch": False, "pen": False})
        res["size_cap"] = run_case(n, d, "clip-cap", {"clipboard": "both", "clipboard_max_mb": 0, "touch": False, "pen": False})
    finally:
        res["nest_stop"] = n.stop()
        after = e2e.guards()
        live_after = live_clip_fingerprint()
        res["live_clipboard_unchanged"] = live_before == live_after
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and before["local"] == after["local"] and not after["scaletest"]
        json.dump(res, open(f"{d}/clip.json", "w"), indent=1)
        print(json.dumps(res, indent=1, ensure_ascii=False))

main()
