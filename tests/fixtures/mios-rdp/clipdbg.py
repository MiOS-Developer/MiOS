#!/usr/bin/python3 -I
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e
from nest import Nest
from cliptest import xclip, xclip_bg, make_png
d = os.path.join(e2e.HERE, "..", "t-clip")
n = Nest(); n.start(os.path.join(d, "nest.log"))
try:
    e2e.write_config(d, hypr_instance=n.sig, virtual_attach="0x2000", primary_output="WAYLAND-1", redirect={"clipboard": "both", "touch": False, "pen": False})
    s = e2e.Session(d, "clipdbg", server_env={"WAYLAND_DISPLAY": n.wl, "HYPRLAND_INSTANCE_SIGNATURE": n.sig})
    ne = n.env()
    s.start_server(); s.start_xvfb()
    s.start_client(["/sec:tls", "/gfx:AVC420", "/size:1100x2080", "+clipboard", "/log-level:INFO", "/log-filters:com.freerdp.channels.cliprdr.client:TRACE,com.freerdp.client.x11:DEBUG"])
    print("attached", bool(s.wait_attached())); time.sleep(1)
    subprocess.run(["wl-copy", "-t", "text/html", "<b>MiOS</b> html"], env=ne); time.sleep(1.5)
    print("targets after html:", xclip(s, "targets").stdout.strip())
    print("get-html:", xclip(s, "get-html").stdout.strip())
    make_png(f"{s.out}/c.png", 41, 19, 11)
    p = xclip_bg(s, "set-png", f"{s.out}/c.png", "6"); time.sleep(2.5)
    print("nested types after X png:", subprocess.run(["wl-paste", "-l"], capture_output=True, text=True, env=ne).stdout.split())
    p.wait(10)
    s.stop()
finally:
    n.stop()
