#!/usr/bin/env python3
# AI-hint: Representative X11 game client for placement/resize tests: Wine/SDL-style size hints, self-positioning, root-size remaps, fullscreen requests; logs every ConfigureNotify as JSON.
# AI-related: tests/test-game-placement.py, src/mios-rs/miosd/src/placement.rs, usr/share/mios/mios.toml [desktop.hyprland.placement.games]
# AI-functions: main, Probe
"""An X11 client that behaves the way real game windows do under XWayland.

Recorded evidence (miosd placement journal, Forza Horizon 6 / Proton, class steam_app_2483190):
the window mapped at 1920x1080, then reconfigured itself to 3840x1080 -- the XWayland root, i.e. the union of
both outputs -- at the root origin. Wine marks a non-resizable (WS_POPUP) window fixed-size with
WM_NORMAL_HINTS min == max and re-issues those hints whenever its size changes, and positions itself with
ConfigureRequests in X root coordinates. This probe reproduces those requests on demand:

  --fixed            min == max size hints (Wine non-resizable / SDL_WINDOW_RESIZABLE off)
  --wine-hints       after every ConfigureNotify re-pin min == max to the new size (Wine semantics)
  --pos X,Y          USPosition request at map time (SDL/Wine CW_USEDEFAULT-less windows)
  --root-size        map at the root size at 0,0 (borderless "fullscreen" over every output)
  --remap-root MS    MS ms after mapping, ConfigureRequest to 0,0 at the root size (the Forza remap)
  --remap-to X,Y,WxH with --remap-root: request this box instead of the root (an oversize remap that is not the
                     root, or a game moving itself partly off-screen)
  --change-size WxH  --change-at MS after mapping: re-pin the hints to WxH (when fixed) and send a size-only
                     ConfigureRequest (a windowed game applying a new resolution from its settings menu)
  --transient        map a resizable parent window of the same class first and make this window
                     WM_TRANSIENT_FOR it (a Wine application's installer/file/settings dialog)
  --fullscreen       _NET_WM_STATE_FULLSCREEN at map time
  --borderless       _MOTIF_WM_HINTS decorations = 0
  --no-adapt         keep painting only the original size and set no window background (a GL/Vulkan game that
                     never recreates its swapchain): any area the compositor grows the window by shows black

It repaints its whole believed client area with --color on every Expose/ConfigureNotify, so a region that
stays black on screen is the compositor's, not the probe's. Each ConfigureNotify (real or synthetic) is a
JSON line on --log: {"t", "event", "x", "y", "w", "h", "send_event"}.
"""
import argparse
import json
import sys
import time

from Xlib import X, Xatom, display
from Xlib.protocol import event as xevent


def parse_pair(s, sep):
    a, b = s.split(sep)
    return int(a), int(b)


def open_display():
    """DISPLAY may be libxcb's socket-path form (unix:/run/.../x11/X1, as nested sessions publish); python-xlib
    only parses [host]:N, so connect that socket directly."""
    name = __import__('os').environ.get('DISPLAY', '')
    if not name.startswith('unix:/'):
        return display.Display()
    import socket
    from Xlib.support import unix_connect
    path = name[len('unix:'):]
    dno = path.rsplit('X', 1)[-1]

    def get_socket(dname, protocol, host, dno_):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        return s
    unix_connect.get_socket = get_socket
    unix_connect.get_auth = lambda sock, dname, protocol, host, dno_: ('', b'')
    return display.Display(f':{dno}')


class Probe:
    def __init__(self, a):
        self.a = a
        self.d = open_display()
        self.s = self.d.screen()
        root = self.s.root.get_geometry()
        self.root = (root.width, root.height)
        if a.root_size:
            self.w, self.h = self.root
            self.x, self.y = 0, 0
        else:
            self.w, self.h = parse_pair(a.size, 'x')
            self.x, self.y = parse_pair(a.pos, ',') if a.pos else (0, 0)
        cmap = self.s.default_colormap
        self.pixel = cmap.alloc_color(*(int(a.color[i:i + 2], 16) * 257 for i in (0, 2, 4))).pixel
        bg = {} if a.no_adapt else {'background_pixel': self.pixel}
        self.paint_size = (self.w, self.h)
        self.win = self.s.root.create_window(
            self.x, self.y, self.w, self.h, 0, self.s.root_depth, X.InputOutput, X.CopyFromParent,
            event_mask=X.ExposureMask | X.StructureNotifyMask | X.KeyPressMask, **bg)
        self.gc = self.win.create_gc(foreground=self.pixel)
        self.win.set_wm_name(a.title)
        self.win.set_wm_class(a.cls, a.cls)
        self.win.change_property(self.d.intern_atom('_NET_WM_PID'), Xatom.CARDINAL, 32, [__import__('os').getpid()])
        self.hints(self.w, self.h)
        if a.borderless:
            motif = self.d.intern_atom('_MOTIF_WM_HINTS')
            self.win.change_property(motif, motif, 32, [2, 0, 0, 0, 0])
        if a.fullscreen:
            self.win.change_property(self.d.intern_atom('_NET_WM_STATE'), Xatom.ATOM, 32,
                                     [self.d.intern_atom('_NET_WM_STATE_FULLSCREEN')])
        self.log = open(a.log, 'a', buffering=1) if a.log else sys.stdout
        self.parent = None
        if a.transient:
            self.parent = self.s.root.create_window(0, 0, 900, 600, 0, self.s.root_depth, X.InputOutput, X.CopyFromParent,
                                                    event_mask=X.StructureNotifyMask, background_pixel=self.s.white_pixel)
            self.parent.set_wm_name(a.title + ' main')
            self.parent.set_wm_class(a.cls, a.cls)
            self.parent.change_property(self.d.intern_atom('_NET_WM_PID'), Xatom.CARDINAL, 32, [__import__('os').getpid()])
            self.win.change_property(self.d.intern_atom('WM_TRANSIENT_FOR'), Xatom.WINDOW, 32, [self.parent.id])

    def hints(self, w, h):
        flags = 0
        h_ = {}
        if self.a.pos or self.a.root_size:
            flags |= 1 | 4  # USPosition | PPosition
            h_.update(x=self.x, y=self.y)
        if self.a.fixed or self.a.wine_hints:
            flags |= 16 | 32  # PMinSize | PMaxSize
            h_.update(min_width=w, min_height=h, max_width=w, max_height=h)
        self.win.set_wm_normal_hints(flags=flags, **h_)

    def emit(self, **kw):
        kw['t'] = round(time.monotonic(), 3)
        self.log.write(json.dumps(kw) + '\n')

    def paint(self):
        w, h = self.paint_size if self.a.no_adapt else (self.w, self.h)
        self.win.fill_rectangle(self.gc, 0, 0, w, h)
        self.d.flush()

    def run(self):
        self.emit(event='start', root_w=self.root[0], root_h=self.root[1], x=self.x, y=self.y, w=self.w, h=self.h)
        if self.parent:
            self.parent.map()
            self.d.flush()
            time.sleep(1.5)
        self.win.map()
        self.d.flush()
        remap_at = time.monotonic() + self.a.remap_root / 1000.0 if self.a.remap_root >= 0 else None
        change_at = time.monotonic() + self.a.change_at / 1000.0 if self.a.change_size else None
        end = time.monotonic() + self.a.seconds
        while time.monotonic() < end:
            if remap_at is not None and time.monotonic() >= remap_at:
                remap_at = None
                rx, ry, (rw, rh) = 0, 0, self.root
                if self.a.remap_to:
                    xs, ys, size = self.a.remap_to.split(',')
                    rx, ry, (rw, rh) = int(xs), int(ys), parse_pair(size, 'x')
                if self.a.wine_hints or self.a.fixed:
                    self.hints(rw, rh)
                self.win.configure(x=rx, y=ry, width=rw, height=rh)
                self.emit(event='request', x=rx, y=ry, w=rw, h=rh)
                self.d.flush()
            if change_at is not None and time.monotonic() >= change_at:
                change_at = None
                cw, ch = parse_pair(self.a.change_size, 'x')
                if self.a.wine_hints or self.a.fixed:
                    self.hints(cw, ch)
                self.paint_size = (cw, ch)
                self.win.configure(width=cw, height=ch)
                self.emit(event='request', w=cw, h=ch)
                self.d.flush()
            while self.d.pending_events():
                e = self.d.next_event()
                if e.type == X.ConfigureNotify and e.window == self.win:
                    changed = (e.width, e.height) != (self.w, self.h)
                    self.w, self.h = e.width, e.height
                    self.emit(event='configure', x=e.x, y=e.y, w=e.width, h=e.height, send_event=bool(e.send_event))
                    if changed and self.a.wine_hints:
                        self.hints(self.w, self.h)
                    self.paint()
                elif e.type == X.Expose:
                    self.paint()
                elif e.type == X.MapNotify:
                    self.emit(event='mapped')
                    self.paint()
            time.sleep(0.01)
        self.emit(event='exit', w=self.w, h=self.h)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--class', dest='cls', default='mios-game-probe')
    p.add_argument('--title', default='MiOS game probe')
    p.add_argument('--size', default='1280x720')
    p.add_argument('--pos')
    p.add_argument('--fixed', action='store_true')
    p.add_argument('--wine-hints', action='store_true')
    p.add_argument('--root-size', action='store_true')
    p.add_argument('--remap-root', type=int, default=-1, metavar='MS')
    p.add_argument('--remap-to', metavar='X,Y,WxH')
    p.add_argument('--change-size', metavar='WxH')
    p.add_argument('--change-at', type=int, default=3000, metavar='MS')
    p.add_argument('--transient', action='store_true')
    p.add_argument('--fullscreen', action='store_true')
    p.add_argument('--borderless', action='store_true')
    p.add_argument('--no-adapt', action='store_true')
    p.add_argument('--color', default='20c060')
    p.add_argument('--seconds', type=float, default=60.0)
    p.add_argument('--log')
    Probe(p.parse_args()).run()


if __name__ == '__main__':
    main()
