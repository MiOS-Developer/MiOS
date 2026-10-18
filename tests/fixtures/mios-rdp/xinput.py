#!/usr/bin/python3 -I
"""XTEST input into a private Xvfb (where FreeRDP turns it into RDP input).
usage: xinput.py :N cmd...   cmds: move:X,Y  click:B  down:B  up:B  key:KEYSYM  type:TEXT  sleep:MS"""
import ctypes, ctypes.util, sys, time

x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
xt = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
x.XOpenDisplay.restype = ctypes.c_void_p
x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XStringToKeysym.restype = ctypes.c_ulong
x.XStringToKeysym.argtypes = [ctypes.c_char_p]
x.XKeysymToKeycode.restype = ctypes.c_ubyte
x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x.XFlush.argtypes = [ctypes.c_void_p]
x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
xt.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
xt.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
xt.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
x.XChangeKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int]

def main():
    d = x.XOpenDisplay(sys.argv[1].encode())
    if not d:
        sys.exit("cannot open display")
    shift = x.XKeysymToKeycode(d, x.XStringToKeysym(b"Shift_L"))
    for c in sys.argv[2:]:
        op, _, arg = c.partition(":")
        if op == "move":
            a, b = map(int, arg.split(","))
            xt.XTestFakeMotionEvent(d, -1, a, b, 0)
        elif op in ("click", "down", "up"):
            b = int(arg)
            if op in ("click", "down"):
                xt.XTestFakeButtonEvent(d, b, 1, 0)
            if op in ("click", "up"):
                x.XSync(d, 0)
                xt.XTestFakeButtonEvent(d, b, 0, 0)
        elif op == "key":
            kc = x.XKeysymToKeycode(d, x.XStringToKeysym(arg.encode()))
            xt.XTestFakeKeyEvent(d, kc, 1, 0)
            x.XSync(d, 0)
            xt.XTestFakeKeyEvent(d, kc, 0, 0)
        elif op == "type":
            for ch in arg:
                name = {" ": "space", ".": "period", ",": "comma", "-": "minus", "!": "exclam"}.get(ch, ch)
                kc = x.XKeysymToKeycode(d, x.XStringToKeysym(name.encode()))
                up = ch.isupper() or ch == "!"
                if up:
                    xt.XTestFakeKeyEvent(d, shift, 1, 0)
                xt.XTestFakeKeyEvent(d, kc, 1, 0)
                x.XSync(d, 0)
                xt.XTestFakeKeyEvent(d, kc, 0, 0)
                if up:
                    xt.XTestFakeKeyEvent(d, shift, 0, 0)
                x.XSync(d, 0)
                time.sleep(0.03)
        elif op == "bind":
            # bind:KEYCODE:KEYSYMHEX -> that keycode types the keysym (Unicode keysyms 0x1000000 + cp)
            kc_s, ks_s = arg.split(":")
            arr = (ctypes.c_ulong * 1)(int(ks_s, 16))
            x.XChangeKeyboardMapping(d, int(kc_s), 1, arr, 1)
            x.XSync(d, 0)
        elif op == "code":
            kc = int(arg)
            xt.XTestFakeKeyEvent(d, kc, 1, 0)
            x.XSync(d, 0)
            xt.XTestFakeKeyEvent(d, kc, 0, 0)
        elif op == "sleep":
            x.XSync(d, 0)
            time.sleep(int(arg) / 1000)
        x.XSync(d, 0)
    x.XFlush(d)

main()
