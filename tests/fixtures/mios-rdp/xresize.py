#!/usr/bin/python3 -I
"""Resize the (only) top-level client window on an X display through libX11 (ctypes): the FreeRDP
window in a private Xvfb, which then sends an MS-RDPEDISP layout. usage: xresize.py :N WxH"""
import ctypes, ctypes.util, sys

x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
x.XOpenDisplay.restype = ctypes.c_void_p
x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong
x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                         ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
x.XResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_uint]
x.XFlush.argtypes = [ctypes.c_void_p]
x.XCloseDisplay.argtypes = [ctypes.c_void_p]

class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("border_width", ctypes.c_int), ("depth", ctypes.c_int), ("visual", ctypes.c_void_p),
                ("root", ctypes.c_ulong), ("class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
                ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]

x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XWindowAttributes)]

def main():
    disp, size = sys.argv[1], sys.argv[2]
    w, h = map(int, size.split("x"))
    d = x.XOpenDisplay(disp.encode())
    if not d:
        sys.exit("cannot open display")
    root = x.XDefaultRootWindow(d)
    r, p = ctypes.c_ulong(), ctypes.c_ulong()
    kids = ctypes.POINTER(ctypes.c_ulong)()
    n = ctypes.c_uint()
    x.XQueryTree(d, root, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n))
    best = None
    for i in range(n.value):
        a = XWindowAttributes()
        x.XGetWindowAttributes(d, kids[i], ctypes.byref(a))
        if a.map_state == 2 and (best is None or a.width * a.height > best[1]):  # IsViewable, largest
            best = (kids[i], a.width * a.height, a.width, a.height)
    if not best:
        sys.exit("no mapped window")
    x.XResizeWindow(d, best[0], w, h)
    x.XFlush(d)
    x.XCloseDisplay(d)
    print(f"resized window {best[0]:#x} from {best[2]}x{best[3]} to {w}x{h}")

main()
