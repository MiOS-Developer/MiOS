<!-- AI-hint: Manual pages distilled from the source comments of user, sanitized, each passage anchored to the comment it came from. -->

# user

### usr/lib/systemd/user/mios-computer-use-server.service MiOS...

/usr/lib/systemd/user/mios-computer-use-server.service

MiOS computer-use server -- the dual MCP + A2A + HTTP-executor surface for
THIS desktop, so the central agent-pipe CONSUMES this machine's Wayland
session as a federated capability (full MCP AND A2A).

WHY A USER SERVICE: driving the local desktop needs the user's GRAPHICAL
SESSION -- the session D-Bus (DBUS_SESSION_BUS_ADDRESS), WAYLAND_DISPLAY, the
RemoteDesktop/Screenshot portal, and the AT-SPI a11y bus. Those live in
user@$UID.service, not the system manager. The agent-pipe (a SYSTEM service,
user mios-ai) reaches this over loopback/tailnet HTTP -- that hop IS the
system<->session bridge (no /dev/uinput or Wayland socket has to cross into a
container). On bare-metal MiOS, gnome-session activates graphical-session.target;
on WSLg, mios-wsl-graphical-session.service does -- either way this starts with
the desktop and stays inert on a headless node.

SECURITY: binds 127.0.0.1 by default (loopback-only). Expose to the tailnet by
setting [computer_use].bind_address in your /etc/mios overlay AND firewalling
the port to the tailnet (mios_tailscale pattern). There is no auth on this
port yet -> never bind a public interface; require A2A passport signing at the
pipe for cross-host delegation. Every write-class op (click/type/key) runs
through mios-computer-use, which honours the DoD/approval gate.

uinput backend (compositor-agnostic fallback) needs the user in the `input`
group + the uinput uaccess udev rule; the default `portal` backend on
GNOME/KDE needs neither. Off-switch: `systemctl --user disable --now
mios-computer-use-server` or set [computer_use].bind_address = "".

<!-- mios-src:adb750389465 from usr/lib/systemd/user/mios-computer-use-server.service:4-30 -->

### Run on the SHARED agent venv interpreter...

Run on the SHARED agent venv interpreter (fastapi/uvicorn/httpx already
installed there -- the SAME interpreter the agent-pipe uses). The cu-server
only needs fastapi+uvicorn; the actual desktop ops shell out to
mios-computer-use (system python3 with gi/evdev), so the venv stays minimal.
Falls back to a dnf python3-fastapi/python3-uvicorn + /usr/bin/python3 deploy
by editing this line.

<!-- mios-src:37f3f3d02cf6 from usr/lib/systemd/user/mios-computer-use-server.service:38-43 -->

### Tunables (SSOT: mios.toml [computer_use]; override here via...

Tunables (SSOT: mios.toml [computer_use]; override here via drop-in):
  Environment=MIOS_CU_SERVER_PORT=11438
  Environment=MIOS_CU_BIND_ADDRESS=127.0.0.1
  Environment=MIOS_CU_INPUT_BACKEND=auto       # auto | portal | uinput
  Environment=MIOS_CU_CAPTURE_BACKEND=auto
Hardening: needs session D-Bus + portal + (optional) /dev/uinput, so kept
light. Tighten per-deployment once the backend is pinned.

<!-- mios-src:c432b58dda41 from usr/lib/systemd/user/mios-computer-use-server.service:48-54 -->

### Run BEFORE any app would try to access secrets....

Run BEFORE any app would try to access secrets. graphical-session
fires the moment WSLg's gnome-session-equivalent starts; we hook
pre-graphical-session so the daemon is up + unlocked the instant
the operator can launch anything.

<!-- mios-src:a4c63679eb14 from usr/lib/systemd/user/mios-keyring-autounlock.service:6-9 -->

### Resolve [identity].default_password from the layered...

Resolve [identity].default_password from the layered mios.toml
overlay (~/.config -> /etc -> /usr/share). Pipe it as stdin to
gnome-keyring-daemon --login --daemonize. Daemon unlocks (or
creates) the login keyring with that password, registers on the
session bus, and exits to background. Apps that call libsecret
afterwards talk to it via xdg-dbus-proxy / host dbus.

Why a wrapper instead of inline: gnome-keyring-daemon expects the
password on stdin; systemd ExecStart can't easily provide a
password via pipe without a wrapper script.

<!-- mios-src:2b61314289d1 from usr/lib/systemd/user/mios-keyring-autounlock.service:19-28 -->

### usr/lib/systemd/user/mios-launcher.service Operator-side...

/usr/lib/systemd/user/mios-launcher.service

Operator-side launcher broker. Runs in user@<uid>.service so it
inherits the operator's WSLg env (WAYLAND_DISPLAY,
WSL2_GUI_APPS_ENABLED=1, WSL_INTEROP, DBUS_SESSION_BUS_ADDRESS,
the live wayland socket on /run/user/<uid>/wayland-0). Listens on
a unix socket the agent (mios-hermes) can write launch requests
to, dispatching them as the operator with the right environment.

Why this exists: mios-hermes (uid 820) cannot exec
/mnt/c/Windows/System32/*.exe (mode 0544 owned by mios:mios via
WSL metadata; even root chmod fails because Windows ACL blocks
the xattr write). The broker is the operator's standing service
that DOES have exec access; the agent posts launch requests to
its socket from any context.

Operator directive 2026-05-15: "MiOS-Agents should be able to do
this all again!! FIX!!! JUST CREATE universal Launching tools/
skills for launching applications anywhere in any environment(s)
(cross-platform)".

<!-- mios-src:cc8cbc40817f from usr/lib/systemd/user/mios-launcher.service:4-23 -->

### Run ONLY in the OPERATOR's user manager. The broker binds...

Run ONLY in the OPERATOR's user manager. The broker binds the shared
/run/mios-launcher/launcher.sock; if root's (uid 0) or a second user's
(uid 1000) user manager ALSO starts it they contend for that socket, and
whichever wins launches GUI apps in the WRONG session (root -> invisible,
then the window dies). ConditionUser gates the broker to the operator so
exactly ONE instance owns the socket. ('mios' = MIOS_IDENTITY_USERNAME, the operator;
render from MIOS_IDENTITY_USERNAME if that default ever changes.) Operator-hit
2026-06-06: multi-broker invisible launches recurring after WSL restarts.

<!-- mios-src:917db232f01b from usr/lib/systemd/user/mios-launcher.service:26-34 -->

### mios-launch + the verbs it shells to (flatpak-launch...

mios-launch + the verbs it shells to (flatpak-launch, mios-gui, mios-os-control)
live in /usr/libexec/mios, which is NOT on the default systemd-user PATH -- so the
broker got "mios-launch: command not found" (exit 127) and EVERY app launch failed
(operator 2026-06-05 "open epiphany"). Put libexec + the mios shim dir on PATH.

<!-- mios-src:09b634164cff from usr/lib/systemd/user/mios-launcher.service:39-42 -->

### WSLg-only. xdg-desktop-portal-gnome (and most...

WSLg-only. xdg-desktop-portal-gnome (and most graphical-session.target
children) require XDG_CURRENT_DESKTOP / WAYLAND_DISPLAY / DISPLAY /
XDG_SESSION_TYPE to be present in the systemd user-bus environment.
WSLg sets them in the operator's login shell via /etc/profile.d/
wslg.sh + WSLENV, but they NEVER reach systemd-user automatically
(different env-passing path).

Per Arch wiki XDG Desktop Portal + GNOME Discourse "Start a systemd
user service with graphical session in GNOME 40", the canonical
remediation is a oneshot that runs:

  systemctl --user import-environment <vars...>
  dbus-update-activation-environment --systemd <vars...>

at user-bus startup. After this, every dbus-activated service that
d-bus launches (including the portal frontends + backends) sees the
WSLg env.

<!-- mios-src:a8939d83f49c from usr/lib/systemd/user/mios-wsl-env-import.service:8-24 -->

### XDG_SESSION_CLASS=user is required by localsearch-3 (the...

XDG_SESSION_CLASS=user is required by localsearch-3 (the renamed
tracker3 indexer) and several other GNOME services that gate on
being a real "user" session. WSLg has no display manager to set
it; without it, dbus activation of org.freedesktop.Tracker3.Miner.Files
fails with "unit failed", nautilus then crashes on the SPARQL
backend ServiceUnknown error -> GLib G_IS_OBJECT assertion.
Operator-confirmed 2026-05-10 via journalctl --user -u
localsearch-3.service: "skipped, unmet condition check
ConditionEnvironment=XDG_SESSION_CLASS=user".  Force-setting it
here unblocks the whole chain.

<!-- mios-src:e4db02f2582c from usr/lib/systemd/user/mios-wsl-env-import.service:31-40 -->

### XDG_DATA_DIRS must include flatpak's exports/share dirs so...

XDG_DATA_DIRS must include flatpak's exports/share dirs so WSLg's
app-list-monitor sees the flatpak .desktop files and registers them
as native Windows Start Menu entries via the RDP-RAIL bridge.
Without this, only system /usr/share/applications/*.desktop are
discovered -- every installed flatpak (Epiphany, Nautilus, gnome-
software, etc.) is invisible to Windows. Operator-flagged
2026-05-10 "flatpak apps no longer showing in windows as native apps."
Order: ~/.local first (per-user installs), then /var/lib (system-wide
installs), then standard /usr/local/share + /usr/share.

<!-- mios-src:8fbb62638b8d from usr/lib/systemd/user/mios-wsl-env-import.service:42-50 -->

### WSLg-only. On bare-metal MiOS the GNOME session manager...

WSLg-only. On bare-metal MiOS the GNOME session manager
(gnome-session.service) is the canonical activator of
graphical-session.target -- we don't want to fight that here.
On WSLg there is NO gnome-session (WSLg's compositor is the
Wayland server, NOT a GNOME shell), so graphical-session.target
never activates and every BindsTo=graphical-session.target unit
fails -- including xdg-desktop-portal-gnome -> a "Dependency
failed" cascade to xdg-desktop-portal -> nautilus / epiphany /
gnome-software / every flatpak that touches a portal break.

Canonical fix per upstream conventions (Sway/Hyprland model,
Arch waybar wiki, systemd.io/DESKTOP_ENVIRONMENTS): a oneshot
user unit with Wants=graphical-session.target and
WantedBy=default.target. Dependency-driven activation pulls in
graphical-session.target legitimately -- RefuseManualStart=yes
only blocks `systemctl start graphical-session.target`, not
Wants= dependency activation.

Env-import precondition: xdg-desktop-portal-gnome refuses to
operate without XDG_CURRENT_DESKTOP / WAYLAND_DISPLAY /
DISPLAY / XDG_SESSION_TYPE in the systemd-user environment.
WSLg sets them in the login shell only, so we run
mios-wsl-env-import.service first to push them into systemd
user-bus + dbus activation environment.

<!-- mios-src:1eb091e7863b from usr/lib/systemd/user/mios-wsl-graphical-session.service:8-31 -->

### usr/lib/systemd/user/mios-wslg-env.service Closes...

/usr/lib/systemd/user/mios-wslg-env.service

Closes microsoft/WSL#12436 on MiOS: the `systemd --user` manager has no
DISPLAY/WAYLAND_DISPLAY (WSLg injects those only into login shells via
/etc/profile.d), so --user services and flatpak's transient --user scopes launch
GUIs with no display and die "Gtk-WARNING: Failed to open display". This oneshot
pushes the WSLg display env into the user manager + the dbus activation env at
session start, the same thing an interactive GNOME/KDE session does after the
compositor comes up. Required for the launcher broker (mios-launcher.service) to
surface visible windows -- without it, `flatpak run epiphany` from the broker
never appears even though the compositor + sockets are healthy.

<!-- mios-src:6baee29ec169 from usr/lib/systemd/user/mios-wslg-env.service:4-14 -->
### usr/lib/systemd/user/mios-wakeword.service MiOS Three-Stage...

/usr/lib/systemd/user/mios-wakeword.service

MiOS Three-Stage Acoustic Wake-Word service -- manages continuous low-overhead
hands-free voice trigger detection on the user's audio input device.

ARCHITECTURE:
Audio frames pass through RNNoise (denoiser) -> Silero VAD (speech detection) ->
OpenWakeWord (phrase matcher). When the user speaks the wake phrase ("Hey MiOS"),
it activates the downstream streaming STT session without high idle CPU or GPU load.

RUNTIME:
Runs as an unprivileged user service within user@$UID.service with access to
PipeWire / PulseAudio session endpoints.

<!-- mios-src:c3f267aca45a from usr/lib/systemd/user/mios-wakeword.service:3-15 -->

### usr/lib/systemd/user/mios-wallpaper.service Living...

/usr/lib/systemd/user/mios-wallpaper.service

Living wallpaper background rendering daemon with Wayland layer-shell occlusion
awareness and low-priority Vulkan compute queue scheduling. Suspends rendering
to 0 FPS (0% GPU load) when desktop is occluded by open windows to preserve 98%+
GPU capacity for local AI inference lanes.

<!-- mios-src:30931545ab6a from usr/lib/systemd/user/mios-wallpaper.service:4-9 -->

### WS-USER (task_503): Network-Wide Roaming Multi-Seat Session...

WS-USER (task_503): Network-Wide Roaming Multi-Seat Session Orchestrator & GPU Assignment Manager.

Orchestrates multi-seat hardware assignment dynamically for roaming users across cluster blades:
- Authenticates users against PostgreSQL users_registry or local encrypted credential store.
- Dynamically assigns available physical GPU outputs (seat0, seat1) or virtual display heads.
- Mounts and maps encrypted user CephFS home volumes (/var/home/<username>).
- Balances GPU allocations across concurrent local physical and remote streaming (Sunshine/Moonlight) users.
- Enforces strict hardware de-allocation and seat cleanup on logout.

<!-- mios-src:8c7e38d46262 from usr/libexec/mios/user/roaming_seat.py:5-14 -->

### WS-USER (task_504): Dynamic Cross-Node Wayland Session...

WS-USER (task_504): Dynamic Cross-Node Wayland Session Checkpoint and Migration Protocol.

Migrates active Wayland desktop sessions and application states seamlessly across cluster blades:
- Checkpoints running user container/Wayland state via headless compositor bridge (hyprland/sway/gnome).
- Preserves user application processes in detached background cgroups during display handoff.
- Synchronizes window geometry, workspace layout, and session state to CephFS (/var/lib/mios/sessions/).
- Restores and re-attaches desktop session to a new physical seat or remote streaming bridge on the target node.

<!-- mios-src:1b762bfeede4 from usr/libexec/mios/user/session_migrate.py:5-13 -->

### MiOS Standard Non-System UID 1000 Enforcement & Migration...

MiOS Standard Non-System UID 1000 Enforcement & Migration Engine.
Implements task_908 / task_3141: Validates and enforces that primary user 'mios'
has UID 1000 and GID 1000, aligning with systemd user session requirements.

<!-- mios-src:b17b89060039 from usr/libexec/mios/user/uid_enforce.py:5-9 -->
### Run ONLY in the OPERATOR's user manager. The broker binds...

Run ONLY in the OPERATOR's user manager. The broker binds the shared
/run/mios-launcher/launcher.sock; if root's (uid 0) or a second user's
(uid 1000) user manager ALSO starts it they contend for that socket, and
whichever wins launches GUI apps in the WRONG session (root -> invisible,
then the window dies). ConditionUser gates the broker to the operator so
exactly ONE instance owns the socket. ('mios' = MIOS_IDENTITY_USERNAME, the operator;
render from MIOS_IDENTITY_USERNAME if that default ever changes.) Operator-hit
2026-06-06: multi-broker invisible launches recurring after WSL restarts.

<!-- mios-src:bdd0df1dee46 from usr/lib/systemd/user/mios-launcher.service:26-34 -->
