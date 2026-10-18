#!/usr/bin/python3 -I
"""P1: native server on SCALETEST (1100x2080@90, scale 2.5) with FreeRDP (AVC420) in Xvfb.
usage: perf.py LABEL WINDOW [meter_secs] [key=value native overrides...]
WINDOW: none (wallpaper + shell) | text (scrolling alacritty: full-window motion) | static
Prints delivered/captured/encoded FPS, bitrate, encoder and server CPU, latencies, and the HDMI-A-1 /
SCALETEST presented FPS (framemeter) + GPU busy measured in the same window."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

def med(v):
    v = sorted(x for x in v if x is not None)
    return v[len(v) // 2] if v else None

def main():
    label, window = sys.argv[1], sys.argv[2]
    msecs = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    over = {}
    for kv in sys.argv[4:]:
        k, v = kv.split("=", 1)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        over[k] = v
    d = os.path.join(e2e.HERE, "..", "t-perf")
    e2e.ensure_cert(d)
    nat = {"frames_in_flight": 3}
    nat.update(over)
    e2e.write_config(d, native=nat)
    before = e2e.guards()
    s = e2e.Session(d, label)
    res = {"label": label, "window": window, "native": nat}
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard", "-sound", "-microphone"])
        res["attached"] = bool(s.wait_attached())
        if window != "none":
            s.spawn_window(window)
        time.sleep(4)
        fc = subprocess.run(["pgrep", "-f", f"xfreerdp /v:127.0.0.1:{s.port}"], capture_output=True, text=True).stdout.split()
        cpu = subprocess.Popen(["python3", "-I", f"{e2e.HERE}/cpustat.py", str(msecs - 2), str(s.server.pid), "5329",
                                *(fc[:1] or ["1"])], stdout=subprocess.PIPE, text=True)
        meter = subprocess.Popen([f"{e2e.HERE}/meter.sh", str(msecs), os.environ.get("PERF_METER_OUTS", "HDMI-A-1"), label],
                                 stdout=subprocess.PIPE, text=True, env=e2e.ENV)
        samples = []
        t0 = time.time()
        while time.time() - t0 < msecs:
            time.sleep(2.0)
            samples.append(s.stats())
        res["meter"] = json.loads(meter.communicate()[0] or "{}")
        res["cpu"] = json.loads(cpu.communicate()[0] or "{}")
        res["samples"] = samples
        keys = ("fps", "captured_fps", "encoded_fps", "static_skipped_fps", "bitrate_kbps", "target_bitrate_kbps",
                "encode_ms_p50", "encode_ms_p95", "convert_ms_p50", "capture_to_send_ms_p50", "capture_to_send_ms_p95",
                "ack_ms_p50", "ack_ms_p95", "capture_to_ack_ms_p50", "cpu_pct", "rss_mb", "output_hz", "frames_in_flight",
                "damage_pct_p50")
        res["median"] = {k: med([x.get(k) for x in samples[1:]]) for k in keys}
        res["last"] = samples[-1] if samples else {}
    finally:
        s.stop()
        after = e2e.guards()
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and before["local"] == after["local"] and not after["scaletest"] and before["audio"] == after["audio"]
        json.dump(res, open(f"{s.out}/perf.json", "w"), indent=1)
        m = res.get("meter", {}).get("fps", {})
        print(json.dumps({
            "label": label, "window": window, "guards_ok": res["guards_ok"], **(res.get("median") or {}),
            "hdmi_fps": m.get("HDMI-A-1", {}).get("median"), "scaletest_fps": m.get("HEADLESS-MIOS-SCALETEST", {}).get("median"),
            "gpu_busy": res.get("meter", {}).get("gpu_busy"),
            "server_cpu": next((v["cpu_pct"] for k, v in res.get("cpu", {}).items() if k.startswith("mios-rdp")), None),
            "hyprland_cpu": next((v["cpu_pct"] for k, v in res.get("cpu", {}).items() if k.startswith("Hyprland")), None),
        }))

main()
