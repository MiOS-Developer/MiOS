# Project: MiOS Notification System Unification & Glass Specular Highlights

## Architecture
MiOS unified notification pipeline provides a single, high-fidelity notification and alert transport layer across all native, containerized, Flatpak, and XWayland applications.

```
                          ┌─────────────────────────┐
                          │   Steam (XWayland)      │
                          │ notificationtoasts_*    │
                          └────────────┬────────────┘
                                       │ (Hyprland window rules suppress window)
                                       ▼
┌────────────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐
│ Quickshell Core Engine │  │ mios-steam-bridge     │  │ Desktop / Flatpak Apps  │
│ onReloadCompleted/Fail │  │ Hyprland IPC Listener │  │ org.freedesktop.Notif   │
└───────────┬────────────┘  └──────────┬────────────┘  └────────────┬────────────┘
            │                          │                            │
            ▼                          ▼                            │
┌────────────────────────────────────────────────────────┐          │
│               Config.qml (notifBus)                    │◄─────────┘
│       inhibitReloadPopup() + D-Bus NotificationServer  │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
┌───────────────────────────┐ ┌───────────────────────────┐
│     Notifications.qml     │ │      NotifModel.qml       │
│  Top-Centered Geometry    │ │ Persistent History Model  │
│  cardX = (W - w) / 2      │ │   Notification Tray       │
│  cardY = 44px (8px below) │ └───────────────────────────┘
│  Full Specular Glass      │
└───────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Keymap Focus Property Fix | Fix `Keymap.qml[28:9]` property from invalid `visible` to `active` on `HyprlandFocusGrab` | M1 | Survey §1.1 |
| 2 | Quickshell Reload Interception | Call `Quickshell.inhibitReloadPopup()` to disable raw red popup; connect `onReloadFailed` / `onReloadCompleted` to `notifBus.post()` | M1 | Survey §1.1 |
| 3 | Top-Centered Notification Placement | Position notification cards horizontally centered at `y = 44px` (8px below 28px TopBar/HUD) | M2 | Survey §1.2 |
| 4 | Contour-Following Specular Highlights | Implement concentric `GlassSurface.qml` specular highlight conforming to `Glass.radius` with zero straight-line whiskers | M2 | Survey §1.3 |
| 5 | Compositor Layer Rules & Masking | Configure `layerrule = ignorealpha 0.5`, `blur`, `xray 0` for `mios-notifications` to prevent rectangular blur artifacts | M2 | Survey §1.3 |
| 6 | Persistent Notification History & Tray | Implement `historyList` and `recordHistory` in `NotifModel.qml` so all notifications are saved and dismissible in Notification Tray | M2 | Survey §1.2 |
| 7 | Steam Toast Window Suppression | Hyprland window rules to hide and move offscreen raw Steam XWayland toast windows | M3 | Survey §1.4 |
| 8 | Steam Notification Bridge Daemon | `mios-steam-notify-bridge` monitoring Hyprland IPC to capture Steam toast events and bridge to `org.freedesktop.Notifications` | M3 | Survey §1.4 |
| 9 | Automated Two-Sided Test Suite | Comprehensive positive/negative tests in `tests/test-notifications-glass-unify.py` registered in `tools/ci-suites.py` | M4 | Survey §5 |
| 10 | Nested PiP Desktop Validation & Live Deploy | Validate in live nested MiOS session (PiP window), deploy live to `/`, restart `mios-shell.service`, verify 0 QML errors | M5 | Prompt |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Keymap Fix & Quickshell Reload Bridge | `Keymap.qml`, `Config.qml` reload inhibit & routing | none | DONE |
| M2 | Top-Centered Positioning, Glass Specular & Tray | `Notifications.qml`, `GlassSurface.qml`, `NotifModel.qml`, `NotificationCenter.qml`, `mios.toml`, `Theme.qml` | M1 | DONE |
| M3 | Steam Notification Bridge & Hyprland Rules | `hyprland.conf`, `mios-steam-notify-bridge`, systemd user unit | M2 | DONE |
| M4 | Automated Two-Sided Test Suite | `tests/test-notifications-glass-unify.py`, `tools/ci-suites.py` | M1, M2, M3 | DONE |
| M5 | Nested Desktop PiP Validation & Live Deployment | PiP session validation, CAS merge to `main`, atomic sync to `/`, reload `mios-shell.service`, Victory Audit | M4 | DONE |

## Interface Contracts
### `Quickshell.Core` ↔ `Config.qml` (`notifBus`)
- `Quickshell.inhibitReloadPopup()`: Invoked on `Component.onCompleted` to suppress default engine popup.
- `Quickshell.reloadCompleted()`: Triggers `notifBus.post({ title: "Quickshell Reloaded", body: "...", kind: "success", timeout: 3000 })`.
- `Quickshell.reloadFailed(QString errorString)`: Triggers `notifBus.post({ title: "Quickshell Reload Failed", body: errorString, kind: "error", critical: true, timeout: 0 })`.

### `notifBus` ↔ `NotifModel.qml` (Notification Tray)
- `NotifModel.recordHistory(entry)`: Receives toast payload `{ title, body, kind, appName, image, critical, time }` and stores up to 100 historical entries.
- `NotifModel.clearHistory()`: Resets history list.

### `GlassSurface.qml` ↔ `Notifications.qml` / `Search.qml`
- `property bool specular: true`: Enables outer Fresnel hairline border, concentric inner specular border (`radius: Math.max(0, r - Glass.hairline)`), and top specular gradient conforming to curved corners.

### Hyprland ↔ `mios-steam-notify-bridge`
- Hyprland IPC `openwindow>>...`: Bridge filters `class == "steam"|"Steam"` and `title =~ "^notificationtoasts_.*"`.
- Output: Dispatches `org.freedesktop.Notifications.Notify` with `app_name: "Steam"`, `app_icon: "steam"`, `summary`, `body`.

## Code Layout
- `usr/share/mios/quickshell/Keymap.qml`: Keymap overlay and focus grab.
- `usr/share/mios/quickshell/Config.qml`: Root configuration, shell reload handling, notifBus.
- `usr/share/mios/quickshell/GlassSurface.qml`: Core frosted glass component with specular highlight.
- `usr/share/mios/quickshell/Notifications.qml`: Notification HUD view and stack layout.
- `usr/share/mios/quickshell/NotifModel.qml`: Notification data model and persistent history.
- `usr/share/mios/quickshell/NotificationCenter.qml`: Notification tray card rendering.
- `usr/share/mios/hyprland/hyprland.conf`: Notification layer rules and Steam window rules.
- `usr/libexec/mios/mios-steam-notify-bridge`: Steam toast interception daemon.
- `etc/systemd/user/mios-steam-bridge.service`: User service for the Steam bridge.
- `tests/test-notifications-glass-unify.py`: Automated verification suite.
