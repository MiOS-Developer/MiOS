<!-- AI-hint: The MiOS HUD's information architecture: the dock (localhost and sessions: apps, windows, sessions, VMs, moving windows between them) and the topbar (inbound, external, notifications, systems). What each owns, what moved between them, how window portability really works per target and per app, the nested on-screen keyboard, and controller/keyboard navigation.
     AI-related: usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/DockExpanded.qml, usr/share/mios/quickshell/DockPreview.qml, usr/share/mios/quickshell/DockConfig.qml, usr/share/mios/quickshell/DockPopup.qml, usr/share/mios/quickshell/PanelWindow.qml, tools/native/mios-gen/src/dock.rs, usr/share/mios/mios.toml [desktop.dock] -->
# The MiOS HUD: dock and topbar

MiOS is an operating system for using a cluster: local displays on a Blade, nested
sessions, remote sessions, VMs and containerized apps are meant to behave the same,
as if they were all installed and running here. The HUD is how that is seen and
driven. It has two halves, and every control belongs to exactly one of them.

| | Dock (bottom) | Topbar (top) |
|---|---|---|
| Scope | **localhost and sessions** | **inbound, external, notifications, systems** |
| Owns | apps (pinned, running, installed), windows (open and minimized, with previews), sessions (this desktop's outputs, nested sessions, the host session), VMs, and moving windows between all of them; per-window options (PiP, ShaderGlass) | notifications, connectivity (network), inbound connections (a remote device attached over RDP), sound and power, system status (sysmon, frame rate), time, and management of local and remote MiOS systems |
| Question it answers | *What is running here, and where can it go?* | *What is coming in, and how are the systems doing?* |

Fullscreen hides both halves (the edges and the controller Guide button reveal
them); a maximized window keeps them. The glass is the same in every state.

## What moved

- **Remote device (phone tile) → topbar.** An attached RDP client is an *inbound*
  connection, so its indicator is now the topbar's `remote` item (shown only while
  a device is attached, with the peer's name). Clicking it opens the same device
  controls (pause or resume display and input, disconnect) that the dock tile used to.
- **App drawer → the expanded dock.** The full-pane drawer is gone: expanding the dock
  grows the dock itself upward from its bar, at the bar's width
  (`[desktop.dock].expand_*`; it never covers the whole display).
- Nothing moved from the topbar to the dock: it held no app or window management.
  The workspace indicator stays in the island as status; switching and moving
  windows between workspaces and sessions are dock actions.

## The expanded dock

Top to bottom, at the dock's own width:

1. **Search** — filters apps and windows. The keyboard button beside it toggles the
   on-screen keyboard.
2. **Targets** — where windows can go, each saying how a move really happens.
3. **Windows** — previews of every window in the session, minimized ones included,
   drawn edge to edge (no frames, borders or margins).
4. **Apps** — installed apps. Names wrap to `label_lines` lines and shrink down to
   `label_min_font_px` before anything is cut. Hovering a tile, or focusing it with
   the keyboard or a controller, shows the full name in a tooltip.
5. **Keyboard** — the Phosh on-screen keyboard (stevia), placed inside the panel above
   the dock bar while the toggle is on.

### Window previews are capture-safe

Hyprland 0.56.2 crashes (SEGV in `Screenshare::CScreenshareSession::init`) when any
client captures a window without a live monitor. A pinned window whose output was
removed, for example an RDP display that detached, keeps a dead monitor. Capturing a
window that has just closed also disconnects the capturing client. Two layers guard
against this:

- `DockPreview.qml` captures only a window that Hyprland reports mapped and on a live
  monitor (`monitor >= 0`), and only while the preview is shown. Otherwise it shows
  the app icon.
- The compositor fix is `hyprland-screenshare-no-monitor.patch` in the
  `[desktop.hyprland.compositor]` patch series (branch `mios-hyprland-capture-guard`).
  A capture of a window that cannot be shared now gets `failed` instead of crashing.

### Moving windows: the per-target, per-app truth

`mios-gen dock targets [--address <window>]` reports, and the chips show:

| Target | How | State |
|---|---|---|
| An output of this compositor (local, or the RDP remote display `HEADLESS-MIOS-RDP*`) | **moves live** (`hl.dsp.window.move{monitor}`) | the window and all its state move as they are; RDP streams it to the remote client |
| A nested session, or the host session (from inside a nested one) | **migrate** for containerized or stateless apps (Flatpak, podman/toolbox, `[desktop.dock.targets].stateless_apps`): the source closes and saves its state, then the app relaunches in the target (`flatpak run <id>` for Flatpaks) | only what the app saves itself |
| | **stream** for every other app (native, with local state) | not available yet: the move is refused with the reason, never faked |
| A VM (`[vm.looking_glass].domains`) | **streams only**: the VM's desktop is a local window (Looking Glass), which itself moves like any window | nothing moves *into* a VM |
| waypipe hosts (`[desktop.dock.targets].waypipe_hosts`) | listed only when waypipe is installed | — |

A live handoff of a running Wayland client between compositors is reported as
unsupported. A client's surfaces, buffers and protocol objects belong to one display
connection, and only toolkits that rebuild all of them on a new connection (Qt 6.6+
with `QT_WAYLAND_RECONNECT`, built for compositor restarts) survive a switch. MiOS
ships no reconnecting proxy for arbitrary apps, so it migrates or streams instead.

Every move is verified before it is reported: the window must reach the target
output, or a new window of the app must map in the target compositor.

### The on-screen keyboard

stevia is a GTK layer-shell client. On its own it maps a full-width surface on the TOP
layer and reserves an exclusive zone. It talks to Hyprland through
`zwp_input_method_v2` (preedit and completion, when the app supports
`text-input-v3`) and otherwise falls back to `zwp_virtual_keyboard_v1`.
`mios-gen dock osk` runs stevia behind a small Wayland proxy that rewrites only its
layer surface: the dock's width, `osk.height_px` tall, centred, with its bottom on the
dock bar, on the dock's layer and namespace (so the dock's glass and yield rules apply
to it), and with no exclusive zone. The keyboard is a child of the dock. Collapsing the
dock or turning off the toggle stops both. The toggle is remembered per output in
`$XDG_STATE_HOME/mios/dock-state.json` (user tier). Its default comes from `osk.show`.

### Keyboard and controller

Tab and Shift+Tab move between sections (search, targets, windows, apps), the arrow
keys move inside a section, Return activates, Menu or `m` on a selected window jumps
to the targets, and Escape closes the dock. Controllers reach the dock through
mios-inputd: the Guide tap opens and closes it (`quickshell:drawer-toggle`). The dock
also exposes `quickshell ipc call dock next|previous|osk|select <address>|move <target>|state`
for the controller navigation projection. SUPER+CTRL stays the universal
capture-release escape.
