<!-- AI-hint: Manual pages distilled from the source comments of mios-rdp, sanitized, each passage anchored to the comment it came from. -->

# mios-rdp

### C1/C2 clipboard, tier 1: the native server attached to a...

C1/C2 clipboard, tier 1: the native server attached to a private nested Hyprland (its own seat and
clipboard), FreeRDP +clipboard in Xvfb; X side via xclip.py (GTK4/X11), Wayland side via wl-copy/wl-paste
in the nested session. The operator's live clipboard is only fingerprinted (types + SHA-256) before/after.

<!-- mios-src:0a2ef67b67ee from tests/fixtures/mios-rdp/cliptest.py:2-4 -->

### Private native-server session for tests

Private native-server session for tests: mios-rdp-server on 127.0.0.1:<port> (test-auth build,
test certificate) attached to HEADLESS-MIOS-SCALETEST of the live Hyprland, a FreeRDP client in a
private Xvfb, an optional test window on the scaletest workspace. Guards before/after: fuse.portal
count, watchdog0, HDMI-A-1 mode/position, no SCALETEST left, default sink/source unchanged.
Library + CLI: e2e.py --label L --seconds N [--window bars|text|static|none] [-- extra xfreerdp args]

<!-- mios-src:9e529c4de280 from tests/fixtures/mios-rdp/e2e.py:2-6 -->

### K1/K2/M1 tier 1

K1/K2/M1 tier 1: keyboard (scancodes, modifiers, editing keys), Unicode text (é 你 😀 via FreeRDP
/kbd:unicode:on), mouse buttons and wheel from FreeRDP (XTEST in a private Xvfb) through the native server's
virtual pointer (bound to the remote output) and virtual keyboards into a GTK logger window, all inside a
private nested Hyprland (own seat: the operator's cursor and focus are never touched).

<!-- mios-src:a5f84194e6ac from tests/fixtures/mios-rdp/inputnest.py:2-5 -->

### K1/K2/M1 on the live session, guarded: keyboard (scancodes...

K1/K2/M1 on the live session, guarded: keyboard (scancodes, modifiers), Unicode text, mouse buttons
and wheel from FreeRDP (XTEST in a private Xvfb) through the native server's virtual pointer (bound to
HEADLESS-MIOS-SCALETEST) and virtual keyboards into a GTK logger window on the scaletest workspace.
Aborts unless the operator's cursor is idle; keys are sent only while the logger window is focused; the
cursor is handed back at detach (display service) and checked.

<!-- mios-src:cd1efbe76813 from tests/fixtures/mios-rdp/inputtest.py:2-6 -->

### Private nested Hyprland for tier-1 tests (clipboard...

Private nested Hyprland for tier-1 tests (clipboard, keyboard, mouse): its aquamarine window is parked on
the live session's hidden special workspace special:mios-rdp-nest by a class-scoped rule that is enabled only
while it opens; no D-Bus at all (DBUS_SESSION_BUS_ADDRESS=disabled:), no plugins, no exec, no portals; its own
seat, so its clipboard and keyboard focus are not the operator's. Library: Nest().start() / .stop().

<!-- mios-src:2ae6acab1333 from tests/fixtures/mios-rdp/nest.py:2-5 -->

### T1/T2 (live session, guarded): synthetic MS-RDPEI touch and...

T1/T2 (live session, guarded): synthetic MS-RDPEI touch and pen frames (test-auth FIFO, delivered exactly as
the RDPEI handler delivers them) -> uinput "MiOS Remote Touch test" / "MiOS Remote Pen test", mapped to
HEADLESS-MIOS-SCALETEST by the display service BEFORE the devices exist -> Hyprland libinput -> wl_touch /
tablet-v2 in the GTK logger on the scaletest workspace. Runs only while the operator's cursor is idle;
the previously active window is re-focused afterwards. Negatives: touch=false creates no device; an
unmappable device name creates no device.

<!-- mios-src:cf7ba92cd032 from tests/fixtures/mios-rdp/touchtest.py:2-7 -->
