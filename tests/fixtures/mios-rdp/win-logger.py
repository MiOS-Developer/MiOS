#!/usr/bin/python3 -I
"""Test-only input logger window (GTK4, app_id "mios-scaletest", no D-Bus, no portals): every key,
pointer, scroll, touch and stylus event as one JSON line in $MIOS_LOGGER_OUT."""
import json, os, sys, time
os.environ.setdefault("GDK_DEBUG", "no-portals")
os.environ.setdefault("GTK_USE_PORTAL", "0")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

GLib.set_prgname("mios-scaletest")
OUT = open(os.environ.get("MIOS_LOGGER_OUT", "/dev/stdout"), "a", buffering=1)

def emit(kind, **kw):
    OUT.write(json.dumps({"t": time.monotonic(), "kind": kind, **kw}) + "\n")

Gtk.init()
win = Gtk.Window(title="mios-scaletest input logger")
win.set_default_size(400, 800)
area = Gtk.DrawingArea()
area.set_hexpand(True)
area.set_vexpand(True)
area.set_focusable(True)
area.set_can_focus(True)
win.set_child(area)

key = Gtk.EventControllerKey()
def on_key(c, keyval, keycode, state, pressed):
    emit("key", pressed=pressed, keyval=keyval, name=Gdk.keyval_name(keyval), unicode=chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else "",
         keycode=keycode, state=int(state))
    return False
key.connect("key-pressed", lambda c, kv, kc, st: on_key(c, kv, kc, st, True))
key.connect("key-released", lambda c, kv, kc, st: on_key(c, kv, kc, st, False))
win.add_controller(key)

mot = Gtk.EventControllerMotion()
mot.connect("motion", lambda c, x, y: emit("motion", x=x, y=y))
mot.connect("enter", lambda c, x, y: emit("enter", x=x, y=y))
area.add_controller(mot)

click = Gtk.GestureClick()
click.set_button(0)
click.connect("pressed", lambda g, n, x, y: emit("button", pressed=True, button=g.get_current_button(), x=x, y=y,
                                                   device=str(g.get_current_event_device().get_source()) if g.get_current_event_device() else ""))
click.connect("released", lambda g, n, x, y: emit("button", pressed=False, button=g.get_current_button(), x=x, y=y))
area.add_controller(click)

scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
def on_scroll(c, dx, dy):
    ev = c.get_current_event()
    emit("scroll", dx=dx, dy=dy, unit=str(c.get_unit()), direction=str(ev.get_direction()) if ev else "",
         deltas=list(ev.get_deltas()) if ev else None, stop=ev.is_stop() if ev else None)
    return False
scroll.connect("scroll", on_scroll)
area.add_controller(scroll)

def on_touch(ev_ctl, *_):
    return False

# raw touch: a legacy event controller sees every touch sequence
leg = Gtk.EventControllerLegacy()
def on_event(c, ev):
    t = ev.get_event_type()
    if t in (Gdk.EventType.TOUCH_BEGIN, Gdk.EventType.TOUCH_UPDATE, Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
        ok, x, y = ev.get_position()
        emit("touch", type=t.value_nick, seq=hash(ev.get_event_sequence()) % 100000, x=x, y=y)
    elif t in (Gdk.EventType.PROXIMITY_IN, Gdk.EventType.PROXIMITY_OUT):
        emit("proximity", type=t.value_nick, tool=str(ev.get_device_tool().get_tool_type()) if ev.get_device_tool() else "")
    elif t in (Gdk.EventType.MOTION_NOTIFY, Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
        tool = ev.get_device_tool()
        if tool is not None:
            ok, p = ev.get_axis(Gdk.AxisUse.PRESSURE)
            ok2, tx = ev.get_axis(Gdk.AxisUse.XTILT)
            ok3, ty = ev.get_axis(Gdk.AxisUse.YTILT)
            okp, x, y = ev.get_position()
            emit("stylus", type=t.value_nick, tool=str(tool.get_tool_type()), pressure=p if ok else None,
                 xtilt=tx if ok2 else None, ytilt=ty if ok3 else None, x=x, y=y)
    return False
leg.connect("event", on_event)
area.add_controller(leg)

def on_focus(*_):
    emit("focus", active=win.is_active())
win.connect("notify::is-active", on_focus)

win.present()
emit("ready", pid=os.getpid())
loop = GLib.MainLoop()
win.connect("close-request", lambda *_: (loop.quit(), False)[1])
loop.run()
