#!/usr/bin/python3 -I
"""G6 colour fidelity: colour bars on SCALETEST, grim (compositor truth) vs FreeRDP's decoded AVC420
frame (Xvfb framebuffer). ΔE2000 per bar patch. usage: g6.py LABEL [color_matrix]"""
import json, math, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402
from PIL import Image  # noqa: E402

def srgb_to_lab(c):
    def lin(u):
        u /= 255.0
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(x) for x in c)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)

def de2000(c1, c2):
    L1, a1, b1 = srgb_to_lab(c1)
    L2, a2, b2 = srgb_to_lab(c2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    if C1p * C2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = 1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp)) \
        + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) * math.sin(math.radians(60 * math.exp(-(((hbp - 275) / 25) ** 2))))
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))

def patch(im, cx, cy, r=12):
    px = [im.getpixel((x, y))[:3] for x in range(cx - r, cx + r) for y in range(cy - r, cy + r)]
    return tuple(sum(p[i] for p in px) / len(px) for i in range(3))

def main():
    label = sys.argv[1]
    matrix = sys.argv[2] if len(sys.argv) > 2 else "bt709-full"
    d = os.environ.get("MIOS_RDP_LIVE_DIR") or tempfile.mkdtemp(prefix="mios-rdp-g6-")
    e2e.ensure_cert(d)
    e2e.write_config(d, native={"color_matrix": matrix, "frames_in_flight": 3})
    before = e2e.guards()
    s = e2e.Session(d, label)
    res = {"matrix": matrix}
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"])
        res["attached"] = bool(s.wait_attached())
        s.spawn_window("bars")
        time.sleep(3.0)
        grim = f"{s.out}/grim.png"
        subprocess.run(["grim", "-o", e2e.OUT_NAME, grim], env=e2e.ENV, check=True)
        time.sleep(0.3)
        dec = s.xwd()
        dec.save(f"{s.out}/decoded.png")
        truth = Image.open(grim).convert("RGB")
        # locate the client area: FreeRDP's window at the Xvfb origin (no WM)
        rows = []
        for y in (700, 900, 1100):
            for k in range(8):
                x = 70 + 137 * k
                a, b = patch(truth, x, y), patch(dec, x, y)
                rows.append({"x": x, "y": y, "truth": [round(v, 1) for v in a], "decoded": [round(v, 1) for v in b],
                             "dE2000": round(de2000(a, b), 2)})
        res["patches"] = rows
        des = [r["dE2000"] for r in rows]
        res["dE_max"], res["dE_mean"] = max(des), round(sum(des) / len(des), 2)
        res["stats"] = s.stats()
    finally:
        s.stop()
        after = e2e.guards()
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and before["local"] == after["local"] and not after["scaletest"]
        json.dump(res, open(f"{s.out}/g6.json", "w"), indent=1)
        print(json.dumps({k: res.get(k) for k in ("matrix", "attached", "dE_max", "dE_mean", "guards_ok")}))

main()
