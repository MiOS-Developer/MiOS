<!-- AI-hint: Lead-agent handoff for the MiOS desktop/RDP/roadmap lanes: live state of main, every lane's branch and gate status, the integration procedure, safety rules learned from incidents, and the operator's standing decisions.
     AI-related: docs/design/mios-desktop-continuation-handoff.md, usr/share/mios/mios.toml, tools/native/mios-gen/src/mem_admit.rs -->
# MiOS lead handoff

Written by the Claude lead (`mios-desktop-repair-session` on the agent relay) for whoever takes over coordination.
Read it before touching anything live. The older RDP/Codex handoff is
[`mios-desktop-continuation-handoff.md`](mios-desktop-continuation-handoff.md).

## 1. Live state

- **`main`** is `fb5fb4183`, pushed. `/` is both the repo root and the live system root.
- **Primary desktop**
  - Hyprland 0.56.2, started by greetd with `--config /usr/share/mios/hyprland/session.lua`.
  - Find it by that command line, never by PID or `pgrep -o`. `~/mios-desktop-work-1/integrate-desktop/primary.sh` exports `PRIMARY` and the live `HYPRLAND_INSTANCE_SIGNATURE`.
  - Quickshell is Fedora 0.2.1. The 0.3.2 candidate was rolled back; its journal is in `/var/lib/mios-build/rollback/quickshell-candidate/`.
- **Memory guards** (live since `11ce45d48`)
  - Desktop floors in `memory.low`: `user-.slice` 10G, `session-.scope` 4G, `user@.service` 6G, `mios-shell.service` 2G.
  - `mios-gen mem-admit [--wait]` refuses below `[system.memory_admission].min_available_mb`. Every nested-session launch runs the same check.
- **Document portal**
  - The drop-in `xdg-document-portal.service.d/10-mios-restart.conf` restarts it on failure.
  - Root cause of the deaths: a private `dbus-daemon --session` that inherited the operator's `XDG_RUNTIME_DIR`. Guarded by `tests/test-document-portal-isolation.py`.
- **Open crash**
  - Hyprland 0.56.2 segfaults in `CScreenshareSession::init` when any toplevel-export client captures a window: dock previews, OBS/xdph capture, screen share.
  - The operator chose to patch Hyprland. The fix is in the dock lane (branch `mios-hyprland-capture-guard`) and applies at an operator-chosen compositor restart.
  - Until then, avoid window capture on the primary.

## 2. Lanes

Each lane has a worktree `/.worktrees/<branch>`; reports are under `~/mios-desktop-work-1/`. A lane goes live only after an independent verifier says ready and the lead integrates it.

- **`mios-input-platform`:** round 4 committed at `a1582d9ec` (fixes the 5 gate findings and the stale binaries). It needs a fresh verifier.
- **`mios-design-unify`:** done; verifier running, verdict to `mios-pickup/design-unify-verify.md`. Applying it needs a mios-shell restart.
- **`mios-dock-hud` + `mios-hyprland-capture-guard`:**
  - the Hyprland capture-crash patch comes first;
  - then the dock: grows upward, OSK toggle, minimized previews, full app names, flatter look, window portability.
- **`mios-appmenu-global`:** app-menu pills for every app with menus. In progress.
- **`mios-gpu-display-routing`:**
  - dGPU boots: the compositor uses only the 4090's outputs;
  - nested sessions render on the headless iGPU (renderD128);
  - console and greeter go to the 4090's display;
  - the 4090 can be hot-handed to VMs;
  - the boot-level parts need an operator reboot to confirm.
- **RDP pickup (`mios-rdp-audio-out`, `-camera-v4l2`, `-usb-forwarding`)**, workflow `mios-codex-pickup.js`. Upstream IronRDP `ed4c279` changed `GfxServerFactory` to take a `GfxContext`; main's `pipeline.rs` needs that fix.
- **`mios-tmuxos-launcher-ux` and `mios-controls-visual`**, same workflow:
  - CTRL+SUPER tap opens the launcher;
  - controller navigation everywhere;
  - nested sessions land in the TUI selector;
  - the Controls TUI draws keyboard, mouse and pad with rebind.
- **Roadmap (`mios-roadmap-units`, `-installer`, `-names`)**, workflow `mios-roadmap-campaigns.js`. Priority: unit projection, Rust consolidation, fewer files. mios-bootstrap is integrated and pushed like MiOS.

Resume workflows with `Workflow({scriptPath, resumeFromRunId})`.

Antigravity (`mios-desktop-continuation-session`) has three relay tasks to run with `/teamwork`: tracker integrity, the 64/65/66 negative tests, and the docs harvest. Codex is out of quota; RDP returns to it once the pickup lanes land.

## 3. Integration

The scripts are in `~/mios-desktop-work-1/integrate-desktop/integrate-*.sh`.

1. Merge into a fresh `/.worktrees/mios-integrate-<x>`.
2. Regenerate the generated files; never hand-merge them.
3. The keybind gate must report 0 findings.
4. Build every changed binary from the merged tree: podman-MiOS-DEV, a private target dir, both heavy locks, `mem-admit --wait` first. `miosd` embeds the SSOT, so always rebuild it.
5. Run the guarded install. It checks the primary, renders and luajit-parses the runtime config, three-way merges other lanes' uncommitted files, writes atomically, moves `main` by CAS, resets only its own paths, backs up binaries to `<bin>.good-<sha8>`, and installs both the `/usr/bin` and `/usr/libexec/mios` copies.
6. Run `sudo mios-sync-theme`. Restart the shell only when the change needs it.
7. Verify live, then push with HTTP/1.1 and postBuffer 65536, after a fetch, a fast-forward check, and a secret/binary scan.

## 4. Safety rules

- **Memory is the limit.** About 15 lanes OOM-hung the 45 GB host at 18:23 on 2026-10-30, forcing a hard reset.
  - Keep concurrent lanes small.
  - Run `mem-admit --wait` before every build, test or nested session.
  - Agents work on non-build parts while waiting.
- **Isolation**
  - One nested PiP session per lane.
  - Reach the primary only with `send_key_state` to your own nested window.
  - Negative controls pin a fixed pre-lane commit, never `main:`.
- **D-Bus:** every private bus gets its own `XDG_RUNTIME_DIR`.
- **Uinput:** grab fixture pads before emitting anything.
- **Never**
  - restart Hyprland or greetd without the operator;
  - touch `/dev/watchdog` or run `miosd daemon`/`miosd test`;
  - touch the mios-xbox VMs or 4090 passthrough live;
  - write `/boot` or GRUB;
  - print secrets;
  - type into Antigravity's pane;
  - use `pkill -f`.

## 5. Operator decisions (2026-10-30)

- **Thesis**
  - Desktop polish is thesis evidence too.
  - The cluster vision and the SSOT/agent thesis are one claim.
  - Record bare-metal observations.
  - This host becomes a MiOS-Metal Blade.
- **HUD**
  - The dock covers localhost, sessions and portability; moves are per app (migrate containerized apps, stream the rest); VMs are first-class sessions.
  - The topbar covers inbound and system state: node health, notifications, a node launcher. Nodes join via Tailscale and the mesh.
- **Window states**
  - Fullscreen hides the HUD; maximized keeps it.
  - Glass applies in every state, with per-state opacity.
  - The popups are modular; the OSK is a toggle.
- **Input:** all pad families; SUPER+CTRL is the universal release; a CTRL+SUPER tap opens the launcher.
- **GPU**
  - One monitor on both GPUs.
  - The iGPU stays BIOS primary.
  - 4090 hot-handoff to VMs is allowed.
  - Patch Hyprland, then return to Quickshell 0.3.2.
- **Process**
  - Resume lanes automatically, with admission.
  - No lane self-pushes.
  - Roadmap priority: units, Rust consolidation, fewer files.
