#!/usr/bin/python3 -I
"""T1/T2 (live session, guarded): synthetic MS-RDPEI touch and pen frames (test-auth FIFO, delivered exactly as
the RDPEI handler delivers them) -> uinput "MiOS Remote Touch test" / "MiOS Remote Pen test", mapped to
HEADLESS-MIOS-SCALETEST by the display service BEFORE the devices exist -> Hyprland libinput -> wl_touch /
tablet-v2 in the GTK logger on the scaletest workspace. Runs only while the operator's cursor is idle;
the previously active window is re-focused afterwards. Negatives: touch=false creates no device; an
unmappable device name creates no device."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

H = e2e.HERE

def cursor():
    return e2e.hc("cursorpos")

def devices():
    d = json.loads(e2e.hc("-j", "devices") or "{}")
    return {"touch": [x.get("name") for x in d.get("touch", [])], "tablets": [x.get("name") for x in d.get("tablets", [])]}

def active():
    a = json.loads(e2e.hc("-j", "activewindow") or "{}")
    return a.get("address"), a.get("class")

def idle(secs=3):
    c0 = cursor()
    for _ in range(secs):
        time.sleep(1)
        if cursor() != c0:
            return False
    return True

def run(d, label, redirect, naming, frames):
    fifo = os.path.join(d, f"input-{label}.fifo")
    if os.path.exists(fifo):
        os.unlink(fifo)
    os.mkfifo(fifo, 0o600)
    e2e.write_config(d, input=True, test_input_fifo=fifo, redirect=redirect, naming=naming)
    s = e2e.Session(d, label)
    logp = os.path.join(d, f"logger-{label}.jsonl")
    if os.path.exists(logp):
        os.unlink(logp)
    r = {}
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard"])
        r["attached"] = bool(s.wait_attached())
        s.wait_window_rule()
        env = dict(e2e.ENV, MIOS_LOGGER_OUT=logp, DBUS_SESSION_BUS_ADDRESS="disabled:", GDK_DEBUG="no-portals", GTK_USE_PORTAL="0")
        s.window = subprocess.Popen(["python3", "-I", f"{H}/win-logger.py"], env=env, stdout=subprocess.DEVNULL,
                                    stderr=open(f"{s.out}/logger.err", "w"))
        time.sleep(2.0)
        win = next((c for c in json.loads(e2e.hc("-j", "clients") or "[]") if c.get("class") == "mios-scaletest"), None)
        mon = next(m for m in e2e.mons() if m["name"] == e2e.OUT_NAME)
        sc = mon["scale"]
        cx = int((win["at"][0] - mon["x"] + win["size"][0] / 2) * sc)
        cy = int((win["at"][1] - mon["y"] + win["size"][1] / 2) * sc)
        r["window_center_px"] = [cx, cy]
        w = open(fifo, "w", buffering=1)
        t_send = []
        for f in frames(cx, cy):
            t_send.append(time.monotonic())
            w.write(json.dumps(f) + "\n")
            time.sleep(0.03)
        time.sleep(1.0)
        r["devices_during"] = devices()
        w.write(json.dumps({"rdpei_closed": True}) + "\n")
        time.sleep(0.8)
        r["devices_after_close"] = devices()
        w.close()
        r["stats_input"] = s.stats().get("input")
        log = open(f"{s.out}/server.log").read()
        r["server_lines"] = [l.split("mios_rdp_server::", 1)[-1][:160] for l in log.splitlines()
                             if "uinput" in l or "map the device" in l or "RDPEI" in l or "pointer emulation" in l]
        r["t_first_send"] = t_send[0] if t_send else None
    finally:
        s.stop()
        try:
            os.unlink(fifo)
        except OSError:
            pass
    ev = [json.loads(l) for l in open(logp)] if os.path.exists(logp) else []
    tev = [e for e in ev if e["kind"] == "touch"]
    r["touch_events"] = {"n": len(tev), "types": sorted({e["type"] for e in tev}), "sequences": len({e["seq"] for e in tev})}
    sev = [e for e in ev if e["kind"] in ("stylus", "proximity")]
    pr = [e["pressure"] for e in sev if e.get("pressure") is not None]
    r["stylus_events"] = {"n": len(sev), "tools": sorted({e.get("tool") for e in sev}), "pressure_min": min(pr) if pr else None,
                          "pressure_max": max(pr) if pr else None,
                          "tilt": sorted({(e.get("xtilt"), e.get("ytilt")) for e in sev if e.get("xtilt") is not None})[-1:]}
    if tev and r.get("t_first_send"):
        r["first_touch_latency_ms"] = round((tev[0]["t"] - r["t_first_send"]) * 1000, 2)
    return r

def touch_frames(cx, cy):
    yield {"touch": [[0, cx, cy, "down"]]}
    for i in range(1, 6):
        yield {"touch": [[0, cx + 10 * i, cy + 5 * i, "update"]]}
    yield {"touch": [[0, cx + 50, cy + 25, "up"]]}
    # five contacts at once (multi-touch)
    yield {"touch": [[k, cx - 100 + 50 * k, cy - 100, "down"] for k in range(5)]}
    for i in range(1, 4):
        yield {"touch": [[k, cx - 100 + 50 * k, cy - 100 + 20 * i, "update"] for k in range(5)]}
    yield {"touch": [[k, cx - 100 + 50 * k, cy - 40, "up"] for k in range(5)]}

def pen_frames(cx, cy):
    yield {"pen": [cx, cy, "hover", 0, 0, 0, "pen"]}
    yield {"pen": [cx, cy, "down", 10, 0, 0, "pen"]}
    for i in range(1, 11):
        yield {"pen": [cx + 5 * i, cy + 5 * i, "update", 100 * i + 24, 3 * i, -2 * i, "pen"]}
    yield {"pen": [cx + 60, cy + 60, "up", 0, 0, 0, "pen"]}
    yield {"pen": [cx, cy + 100, "hover", 0, 0, 0, "eraser"]}
    yield {"pen": [cx, cy + 100, "down", 500, 0, 0, "eraser"]}
    yield {"pen": [cx + 30, cy + 130, "update", 600, 0, 0, "eraser"]}
    yield {"pen": [cx + 30, cy + 130, "up", 0, 0, 0, "eraser"]}

def main():
    if not idle():
        print(json.dumps({"aborted": "operator cursor moving; touch/pen test postponed"}))
        return
    d = os.path.join(H, "..", "t-touch")
    e2e.ensure_cert(d)
    before = e2e.guards()
    act0 = active()
    res = {"active_before": act0[1]}
    names = {"touch_device": "MiOS Remote Touch test", "pen_device": "MiOS Remote Pen test"}
    try:
        res["T1"] = run(d, "touch", {"clipboard": "off", "touch": True, "pen": True}, names, touch_frames)
        res["T2"] = run(d, "pen", {"clipboard": "off", "touch": True, "pen": True}, names, pen_frames)
        res["T1_neg_touch_off"] = run(d, "touchoff", {"clipboard": "off", "touch": False, "pen": False}, names, touch_frames)
        res["T1_neg_unmappable"] = run(d, "badname", {"clipboard": "off", "touch": True, "pen": True},
                                       {"touch_device": "MiOS Remote Touch\"x", "pen_device": "MiOS Remote Pen test"},
                                       lambda cx, cy: iter([{"touch": [[0, cx, cy, "down"]]}, {"touch": [[0, cx, cy, "up"]]}]))
    finally:
        act1 = active()
        res["active_after"] = act1[1]
        if act0[0] and act1[0] != act0[0]:
            e2e.hc("dispatch", f'hl.dsp.focus({{window = "address:{act0[0]}"}})')
            res["refocused"] = active()[1]
        after = e2e.guards()
        res["devices_left"] = devices()
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and before["local"] == after["local"] and not after["scaletest"]
        json.dump(res, open(f"{d}/touch.json", "w"), indent=1)
        print(json.dumps(res, indent=1))

main()
