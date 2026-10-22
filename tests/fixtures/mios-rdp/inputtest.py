#!/usr/bin/python3 -I
"""K1/K2/M1 on the live session, guarded: keyboard (scancodes, modifiers), Unicode text, mouse buttons
and wheel from FreeRDP (XTEST in a private Xvfb) through the native server's virtual pointer (bound to
HEADLESS-MIOS-SCALETEST) and virtual keyboards into a GTK logger window on the scaletest workspace.
Aborts unless the operator's cursor is idle; keys are sent only while the logger window is focused; the
cursor is handed back at detach (display service) and checked."""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e  # noqa: E402

H = e2e.HERE

def cursor():
    try:
        a, b = e2e.hc("cursorpos").split(",")
        return int(a), int(b)
    except ValueError:
        return None

def active_class():
    try:
        return json.loads(e2e.hc("-j", "activewindow") or "{}").get("class")
    except ValueError:
        return None

def xin(disp, *cmds):
    subprocess.run([sys.executable, "-I", f"{H}/xinput.py", disp, *cmds], check=True)

def events(path):
    try:
        return [json.loads(l) for l in open(path)]
    except (OSError, ValueError):
        return []

def main():
    # operator idle: the shared cursor has not moved for 3 s
    c0 = cursor()
    for _ in range(3):
        time.sleep(1)
        if cursor() != c0:
            print(json.dumps({"aborted": "operator cursor moving; input test postponed"}))
            return
    saved_active = active_class()
    d = os.path.join(H, "..", "t-input")
    e2e.ensure_cert(d)
    e2e.write_config(d, input=True, redirect={"clipboard": "off", "touch": False, "pen": False})
    before = e2e.guards()
    s = e2e.Session(d, "input")
    logp = f"{s.out}/logger.jsonl"
    if os.path.exists(logp):
        os.unlink(logp)
    res = {"cursor_before": c0, "active_before": saved_active}
    inside = []
    try:
        s.start_server()
        s.start_xvfb()
        s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "-clipboard", "/kbd:unicode:on"])
        res["attached"] = bool(s.wait_attached())
        s.wait_window_rule()
        env = dict(e2e.ENV)
        env.update(MIOS_LOGGER_OUT=logp, DBUS_SESSION_BUS_ADDRESS="disabled:", GDK_DEBUG="no-portals", GTK_USE_PORTAL="0")
        s.window = subprocess.Popen(["python3", "-I", f"{H}/win-logger.py"], env=env, stdout=subprocess.DEVNULL, stderr=open(f"{s.out}/logger.err", "w"))
        win = None
        for _ in range(50):
            time.sleep(0.2)
            cl = [c for c in json.loads(e2e.hc("-j", "clients") or "[]") if c.get("class") == "mios-scaletest"]
            if cl:
                win = cl[0]
                break
        mon = next(m for m in e2e.mons() if m["name"] == e2e.OUT_NAME)
        res["window"] = {"at": win and win["at"], "size": win and win["size"], "monitor": mon["name"], "scale": mon["scale"]}
        sc = mon["scale"]
        cx = int((win["at"][0] - mon["x"] + win["size"][0] / 2) * sc)
        cy = int((win["at"][1] - mon["y"] + win["size"][1] / 2) * sc)
        # M1: pointer onto the logger window (virtual pointer bound to SCALETEST)
        xin(s.disp, f"move:{cx},{cy}", "sleep:300", f"move:{cx + 20},{cy + 30}", "sleep:300")
        inside.append(cursor())
        res["active_after_move"] = active_class()
        if res["active_after_move"] != "mios-scaletest":
            res["keyboard"] = "skipped: logger not focused (focus guard)"
        else:
            # K1: ASCII + modifiers + editing keys (scancode path: unicode off for keys FreeRDP maps)
            xin(s.disp, "type:Hello, MiOS!", "key:Return", "key:BackSpace", "key:Left", "sleep:200")
            # K2: Unicode keysyms bound to spare Xvfb keycodes, sent by FreeRDP as Unicode events
            xin(s.disp, "bind:200:e9", "bind:201:1004f60", "bind:202:101f600", "sleep:200",
                "code:200", "code:201", "code:202", "sleep:400")
            inside.append(cursor())
        # buttons and wheel
        xin(s.disp, "click:1", "sleep:150", "click:3", "sleep:150", "click:4", "click:4", "click:5", "sleep:300")
        inside.append(cursor())
        time.sleep(0.5)
        res["stats_input"] = s.stats().get("input")
    finally:
        s.stop()
        time.sleep(1.5)
        after = e2e.guards()
        res["cursor_after"] = cursor()
        res["active_after"] = active_class()
        ev = events(logp)
        keys = [e for e in ev if e["kind"] == "key" and e["pressed"]]
        res["typed"] = "".join(e["unicode"] for e in keys if e["unicode"])
        res["key_names"] = [e["name"] for e in keys]
        res["buttons"] = [(e["button"], e["pressed"]) for e in ev if e["kind"] == "button"]
        res["scroll"] = [(e["dx"], e["dy"]) for e in ev if e["kind"] == "scroll"]
        res["motion_events"] = sum(1 for e in ev if e["kind"] == "motion")
        b = (mon["x"], mon["y"], mon["x"] + mon["width"] / mon["scale"], mon["y"] + mon["height"] / mon["scale"]) if 'mon' in dir() else None
        res["cursor_inside_scaletest"] = [bool(b and p and b[0] <= p[0] < b[2] and b[1] <= p[1] < b[3]) for p in inside]
        res["cursor_handed_back"] = res["cursor_after"] == c0
        res["guards_ok"] = before["portal"] == after["portal"] and after["watchdog"] == "inactive" \
            and before["local"] == after["local"] and not after["scaletest"]
        json.dump(res, open(f"{s.out}/input.json", "w"), indent=1)
        print(json.dumps(res, indent=1))

main()
