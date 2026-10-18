#!/usr/bin/python3 -I
"""K1/K2/M1 tier 1: keyboard (scancodes, modifiers, editing keys), Unicode text (é 你 😀 via FreeRDP
/kbd:unicode:on), mouse buttons and wheel from FreeRDP (XTEST in a private Xvfb) through the native server's
virtual pointer (bound to the remote output) and virtual keyboards into a GTK logger window, all inside a
private nested Hyprland (own seat: the operator's cursor and focus are never touched)."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402
from nest import Nest  # noqa: E402

H = e2e.HERE

def xin(disp, *cmds):
    subprocess.run([sys.executable, "-I", f"{H}/xinput.py", disp, *cmds], check=True)

def main():
    d = os.path.join(H, "..", "t-input")
    e2e.ensure_cert(d)
    before = e2e.guards()
    live_cursor = e2e.hc("cursorpos")
    n = Nest()
    res = {"nest": n.start(os.path.join(d, "nest.log"))}
    logp = os.path.join(d, "logger.jsonl")
    if os.path.exists(logp):
        os.unlink(logp)
    s = None
    try:
        e2e.write_config(d, hypr_instance=n.sig, virtual_attach="0x2000", primary_output="WAYLAND-1", input=True,
                         keyboard_layout="us", redirect={"clipboard": "off", "touch": False, "pen": False})
        s = e2e.Session(d, "input", server_env={"WAYLAND_DISPLAY": n.wl, "HYPRLAND_INSTANCE_SIGNATURE": n.sig})
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard", "/kbd:unicode:on"])
        res["attached"] = bool(s.wait_attached())
        lg = subprocess.Popen(["python3", "-I", f"{H}/win-logger.py"], env=n.env(MIOS_LOGGER_OUT=logp),
                              stdout=subprocess.DEVNULL, stderr=open(f"{s.out}/logger.err", "w"))
        s.window = lg
        win = None
        for _ in range(50):
            time.sleep(0.2)
            cl = [c for c in json.loads(n.hc("-j", "clients") or "[]") if c.get("class") == "mios-scaletest"]
            if cl:
                win = cl[0]
                break
        mon = next(m for m in json.loads(n.hc("-j", "monitors") or "[]") if m["name"] == e2e.OUT_NAME)
        res["window"] = {"at": win and win["at"], "size": win and win["size"], "ws": win and win["workspace"]["name"],
                         "monitor": [mon["width"], mon["height"], mon["scale"], mon["x"], mon["y"]]}
        sc = mon["scale"]
        cx = int((win["at"][0] - mon["x"] + win["size"][0] / 2) * sc)
        cy = int((win["at"][1] - mon["y"] + win["size"][1] / 2) * sc)
        res["target_px"] = [cx, cy]
        xin(s.disp, f"move:{cx},{cy}", "sleep:300", f"move:{cx + 20},{cy + 30}", "sleep:400")
        res["nested_cursor"] = n.hc("cursorpos")
        res["active"] = json.loads(n.hc("-j", "activewindow") or "{}").get("class")
        t0 = time.time()
        xin(s.disp, "type:Hello, MiOS!", "key:Return", "key:BackSpace", "key:Left", "key:Up", "sleep:200")
        xin(s.disp, "bind:200:e9", "bind:201:1004f60", "bind:202:101f600", "sleep:200",
            "code:200", "code:201", "code:202", "sleep:300")
        # K2 negative: after Unicode text, a plain scancode key still types its layout symbol
        xin(s.disp, "type:a", "sleep:200")
        xin(s.disp, "click:1", "sleep:150", "click:3", "sleep:150", "click:4", "click:4", "click:5", "sleep:400")
        res["inject_s"] = round(time.time() - t0, 2)
        res["stats_input"] = s.stats().get("input")
    finally:
        if s:
            s.stop()
        res["nest_stop"] = n.stop()
        after = e2e.guards()
        ev = [json.loads(l) for l in open(logp)] if os.path.exists(logp) else []
        keys = [e for e in ev if e["kind"] == "key" and e["pressed"]]
        res["typed"] = "".join(e["unicode"] for e in keys if e["unicode"] and len(e["unicode"]) == 1 and e["unicode"].isprintable())
        res["key_names"] = [e["name"] for e in keys]
        res["shift_states"] = [e["state"] for e in keys if e["unicode"] in ("H", "M", "O", "S", "!")]
        res["buttons"] = [(e["button"], e["pressed"]) for e in ev if e["kind"] == "button"]
        res["scroll"] = [(round(e["dx"], 2), round(e["dy"], 2)) for e in ev if e["kind"] == "scroll"]
        res["motion_events"] = sum(1 for e in ev if e["kind"] == "motion")
        res["live_cursor_unchanged"] = e2e.hc("cursorpos") == live_cursor
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and before["local"] == after["local"] and not after["scaletest"]
        json.dump(res, open(f"{d}/input.json", "w"), indent=1, ensure_ascii=False)
        print(json.dumps(res, indent=1, ensure_ascii=False))

main()
