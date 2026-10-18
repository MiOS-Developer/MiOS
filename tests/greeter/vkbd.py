#!/usr/bin/env python3
# AI-hint: Minimal pure-Python Wayland virtual-keyboard client (zwp_virtual_keyboard_v1 over the raw wire
# protocol, keymap from libxkbcommon via ctypes) so the greeter tests can drive the nested test compositor
# with real key events -- no wtype/pywayland needed. Only ever pointed at a private test WAYLAND_DISPLAY.
# AI-related: ../test-greeter.py
"""Usage: vkbd.py [--allow-close] STEP...   steps: text:<ascii>  key:<Name>  sleep:<ms>
--allow-close: the compositor exiting after the last step is success (the greeter quit on that key).
Key names: Tab ShiftTab Enter Escape Up Down Left Right Space BackSpace F1..F12."""
import ctypes
import os
import socket
import struct
import sys
import tempfile
import time

KEYS = {"Escape": 1, "BackSpace": 14, "Tab": 15, "Enter": 28, "Space": 57, "Up": 103, "Down": 108,
        "Left": 105, "Right": 106, "Shift": 42}
KEYS.update({f"F{i}": 58 + i for i in range(1, 11)})
KEYS.update({"F11": 87, "F12": 88})
ROWS = {"1234567890": 2, "qwertyuiop": 16, "asdfghjkl": 30, "zxcvbnm": 44}
CHARS = {c: base + i for row, base in ROWS.items() for i, c in enumerate(row)}
CHARS.update({" ": 57, "-": 12, "=": 13, ".": 52, ",": 51, "/": 53, ";": 39})
SHIFTED = {"_": 12, "!": 2, "@": 3}


def xkb_keymap():
    lib = ctypes.CDLL("libxkbcommon.so.0")
    lib.xkb_context_new.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    lib.xkb_keymap_get_as_string.restype = ctypes.c_void_p
    lib.xkb_keymap_get_as_string.argtypes = [ctypes.c_void_p, ctypes.c_int]
    ctx = lib.xkb_context_new(0)
    km = lib.xkb_keymap_new_from_names(ctx, None, 0)
    if not km:
        raise SystemExit("vkbd: cannot compile default xkb keymap")
    return ctypes.string_at(lib.xkb_keymap_get_as_string(km, 1)) + b"\0"


class Wl:
    def __init__(self):
        path = os.path.join(os.environ["XDG_RUNTIME_DIR"], os.environ["WAYLAND_DISPLAY"])
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.buf = b""
        self.next_id = 2

    def new_id(self):
        i = self.next_id
        self.next_id += 1
        return i

    @staticmethod
    def string(v):
        b = v.encode() + b"\0"
        return struct.pack("=I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)

    def send(self, obj, op, payload=b"", fds=()):
        msg = struct.pack("=II", obj, ((8 + len(payload)) << 16) | op) + payload
        if fds:
            self.s.sendmsg([msg], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("=%di" % len(fds), *fds))])
        else:
            self.s.sendall(msg)

    def events(self):
        while True:
            while len(self.buf) >= 8:
                obj, so = struct.unpack("=II", self.buf[:8])
                size = so >> 16
                if len(self.buf) < size:
                    break
                body, self.buf = self.buf[8:size], self.buf[size:]
                yield obj, so & 0xFFFF, body
            data = self.s.recv(65536)
            if not data:
                raise SystemExit("vkbd: compositor closed the connection")
            self.buf += data

    def roundtrip(self, on_event=None):
        cb = self.new_id()
        self.send(1, 0, struct.pack("=I", cb))
        for obj, op, body in self.events():
            if obj == cb and op == 0:
                return
            if obj == 1 and op == 0:
                oid, code = struct.unpack("=II", body[:8])
                raise SystemExit(f"vkbd: protocol error on object {oid} code {code}: {body[12:].rstrip(bytes(1)).decode(errors='replace')}")
            if on_event:
                on_event(obj, op, body)


def main(steps):
    wl = Wl()
    registry = wl.new_id()
    wl.send(1, 1, struct.pack("=I", registry))
    globs = {}

    def on_global(obj, op, body):
        if obj == registry and op == 0:
            name, ln = struct.unpack("=II", body[:8])
            iface = body[8:8 + ln - 1].decode()
            globs[iface] = name

    wl.roundtrip(on_global)
    if "zwp_virtual_keyboard_manager_v1" not in globs or "wl_seat" not in globs:
        raise SystemExit("vkbd: compositor lacks wl_seat or zwp_virtual_keyboard_manager_v1")

    def bind(iface, version):
        oid = wl.new_id()
        wl.send(registry, 0, struct.pack("=I", globs[iface]) + Wl.string(iface) + struct.pack("=II", version, oid))
        return oid

    seat = bind("wl_seat", 1)
    mgr = bind("zwp_virtual_keyboard_manager_v1", 1)
    kbd = wl.new_id()
    wl.send(mgr, 0, struct.pack("=II", seat, kbd))
    km = xkb_keymap()
    with tempfile.TemporaryFile() as f:
        f.write(km)
        f.flush()
        wl.send(kbd, 0, struct.pack("=II", 1, len(km)), fds=(f.fileno(),))
        wl.roundtrip()
    t0 = time.monotonic()

    def key(code, state):
        wl.send(kbd, 1, struct.pack("=III", int((time.monotonic() - t0) * 1000), code, state))

    def tap(code, shift=False):
        if shift:
            key(KEYS["Shift"], 1)
            wl.send(kbd, 2, struct.pack("=IIII", 1, 0, 0, 0))
        key(code, 1)
        key(code, 0)
        if shift:
            key(KEYS["Shift"], 0)
            wl.send(kbd, 2, struct.pack("=IIII", 0, 0, 0, 0))
        wl.roundtrip()
        time.sleep(0.04)

    for step in steps:
        kind, _, arg = step.partition(":")
        if kind == "text":
            for c in arg:
                if c in CHARS:
                    tap(CHARS[c])
                elif c.lower() in CHARS and c.isupper():
                    tap(CHARS[c.lower()], shift=True)
                elif c in SHIFTED:
                    tap(SHIFTED[c], shift=True)
                else:
                    raise SystemExit(f"vkbd: no key for {c!r}")
        elif kind == "key":
            if arg == "ShiftTab":
                tap(KEYS["Tab"], shift=True)
            else:
                tap(KEYS[arg])
        elif kind == "sleep":
            time.sleep(int(arg) / 1000)
        else:
            raise SystemExit(f"vkbd: bad step {step!r}")
    wl.roundtrip()


if __name__ == "__main__":
    args = sys.argv[1:]
    allow_close = bool(args) and args[0] == "--allow-close"
    try:
        main(args[1:] if allow_close else args)
    except (SystemExit, OSError) as e:
        closed = isinstance(e, OSError) or "closed the connection" in str(e)
        if not (allow_close and closed):
            raise
