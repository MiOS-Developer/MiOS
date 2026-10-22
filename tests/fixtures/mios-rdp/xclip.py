#!/usr/bin/python3 -I
"""X11 CLIPBOARD helper for a private Xvfb (GTK4, GDK_BACKEND=x11, no D-Bus):
xclip.py set-text TEXT SECS | set-png FILE SECS | get-text | get-png OUT | get-html | targets"""
import os, sys
os.environ["GDK_BACKEND"] = "x11"
os.environ.setdefault("GDK_DEBUG", "no-portals")
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

Gtk.init()
cb = Gdk.Display.get_default().get_clipboard()
loop = GLib.MainLoop()
out = {"rc": 1}
op = sys.argv[1]

def done(v, rc=0):
    out["v"] = v
    out["rc"] = rc
    loop.quit()

if op == "set-text":
    cb.set(sys.argv[2])
    GLib.timeout_add(int(float(sys.argv[3]) * 1000), loop.quit)
    loop.run()
    sys.exit(0)
if op == "set-png":
    t = Gdk.Texture.new_from_filename(sys.argv[2])
    cb.set(t)
    GLib.timeout_add(int(float(sys.argv[3]) * 1000), loop.quit)
    loop.run()
    sys.exit(0)
GLib.timeout_add(5000, lambda: done("TIMEOUT", 2))
if op == "get-text":
    cb.read_text_async(None, lambda c, r: done(c.read_text_finish(r)))
elif op == "get-png":
    def fin(c, r):
        try:
            t = c.read_texture_finish(r)
            t.save_to_png(sys.argv[2])
            done(f"{t.get_width()}x{t.get_height()}")
        except GLib.Error as e:
            done(str(e), 3)
    cb.read_texture_async(None, fin)
elif op == "get-html":
    def fin(c, r):
        try:
            stream, mime = c.read_finish(r)
            data = b""
            while True:
                b = stream.read_bytes(65536, None).get_data()
                if not b:
                    break
                data += b
            done(data.decode(errors="replace"))
        except GLib.Error as e:
            done(str(e), 3)
    cb.read_async(["text/html"], GLib.PRIORITY_DEFAULT, None, fin)
elif op == "targets":
    GLib.timeout_add(300, lambda: done(cb.get_formats().to_string()))
loop.run()
print(out.get("v"))
sys.exit(out["rc"])
