#!/usr/bin/python3 -I
"""D1/G3 display control: FreeRDP /dynamic-resolution; the client window is resized (portrait ->
landscape -> small) and the server must resize HEADLESS-MIOS-SCALETEST through the display service
(mode, [desktop.scaling] scale, fixed test position), re-create the EGFX surface and send an IDR.
HDMI-A-1 must not change."""
import json, os, re, subprocess, sys, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

def ts(line):
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z", line)
    return datetime.fromisoformat(m.group(1)).timestamp() if m else None

def mon():
    return next(({"w": m["width"], "h": m["height"], "scale": m["scale"], "x": m["x"], "y": m["y"], "hz": round(m["refreshRate"])}
                 for m in e2e.mons() if m["name"] == e2e.OUT_NAME), None)

def main():
    d = os.path.join(e2e.HERE, "..", "t-disp")
    e2e.ensure_cert(d)
    e2e.write_config(d)
    before = e2e.guards()
    s = e2e.Session(d, "disp")
    res = {"steps": []}
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "/dynamic-resolution", "-clipboard"])
        res["attached"] = bool(s.wait_attached())
        res["initial"] = mon()
        for size in ("2080x1100", "800x1280", "1100x2080", "150x150"):
            t0 = time.time()
            r = subprocess.run([sys.executable, "-I", f"{e2e.HERE}/xresize.py", s.disp, size], capture_output=True, text=True)
            time.sleep(3.0)
            log = open(f"{s.out}/server.log").read().splitlines()
            surf = [l for l in log if "EGFX surface ready" in l and (ts(l) or 0) >= t0]
            disp = [l for l in log if "DISP layout from the client" in l and (ts(l) or 0) >= t0]
            st = s.stats()
            res["steps"].append({
                "request": size, "xresize": r.stdout.strip() or r.stderr.strip(),
                "disp_layouts": [re.sub(r"^.*?DISP layout from the client ", "", l)[:160] for l in disp],
                "output": mon(), "surface": st.get("surface"), "idr": st.get("idr"),
                "surface_ready_ms": round(((ts(surf[0]) or t0) - t0) * 1000) if surf else None,
                "first_disp_to_surface_ms": round((ts(surf[0]) - ts(disp[0])) * 1000) if surf and disp else None,
            })
    finally:
        s.stop()
        after = e2e.guards()
        res["hdmi_unchanged"] = before["local"] == after["local"]
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and res["hdmi_unchanged"] and not after["scaletest"]
        json.dump(res, open(f"{s.out}/disp.json", "w"), indent=1)
        print(json.dumps(res, indent=1))

main()
