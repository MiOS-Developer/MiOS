<!-- AI-hint: Per-boot-profile GPU display vs render routing: which GPU lights outputs (the dGPU session leaves the iGPU headless), which render node nested sessions use (dGPU boots -> the headless iGPU), where the login greeter and the kernel console appear, the boot display report, verification after a reboot and how to revert. -->
<!-- AI-related: usr/share/mios/mios.toml [desktop.hyprland.hardware_profiles], [desktop.nested].render_node, [desktop.greeter.display_host], [units."mios-boot-display-report.service"], tools/native/mios-gen/src/{gpu_topology,wl_device_proxy,greeter,nested_session,render}.rs, usr/libexec/mios/mios-greeter-session, tests/test-gpu-display-routing.py -->

# Desktop GPU routing: display vs render per boot profile

MiOS boots one of two desktop hardware profiles, chosen in the GRUB menu (`mios.desktop.gpu=`):

| | iGPU boot (`MiOS iGPU`) | dGPU boot (`MiOS dGPU`) |
|---|---|---|
| dGPU driver | `vfio-pci` (reserved for VMs) | `nvidia` (`nvidia-drm.modeset=1 nvidia-drm.fbdev=1`) |
| Compositor lights | the iGPU's outputs | **only the dGPU's outputs**. The iGPU is headless. |
| Compositor renders on | iGPU | dGPU (`DRI_PRIME` = dGPU) |
| Nested sessions render on | iGPU (`nested_render = "igpu"`) | **the headless iGPU** (`nested_render = "igpu"`) |
| Login greeter | tuigreet on VT 1 (kernel console) | **tuigreet hosted on the dGPU's display** (cage + terminal on the dGPU card). The VT greeter is the fallback. |
| Kernel console / boot text | iGPU (boot VGA) | **dGPU** (`fbcon=map:10`) |
| GRUB / firmware screens | firmware primary display | firmware primary display (see the last section) |

All of it comes from the SSOT tables `[desktop.hyprland.hardware_profiles.igpu]` and `.dgpu`, plus
`[desktop.nested].render_node` and `[desktop.greeter.display_host]`. Nothing is hard-coded per machine. The GPU of
each profile is its PCI identity (`pci`, or the host file `pci_file` = `/etc/mios/hyprland/<profile>-pci`), so a
renumbered `/dev/dri/cardN` does not matter.

## Display vs render (the compositor)

`mios-gen hyprland-session` sets `AQ_DRM_DEVICES` / `WLR_DRM_DEVICES` to the cards the profile **displays** on:

* `include_other_devices = false` (both profiles, the default): only the profile's own card. In a dGPU boot,
  Hyprland never opens the iGPU's KMS card, so it lights none of the iGPU's connectors. The iGPU's **render node**
  (`renderD128`) is still there for nested sessions and for any client that asks for it with `DRI_PRIME`.
* `include_other_devices = true` is the explicit opt-in to light the other GPU's outputs too. The dGPU renders, and
  Aquamarine copies frames to the iGPU's connectors. This was the dGPU default before this change.

`mios-gen hyprland-session --hardware-profile dgpu --dry-run` prints `display_devices`, `headless_devices` and the
`render_node`.

## Nested sessions: the render node

`[desktop.nested].render_node = "auto"` takes the running profile's `nested_render`. The running profile is the
parent session's `MIOS_DESKTOP_HARDWARE_PROFILE`, else `mios.desktop.gpu=`. The explicit overrides are:

* `"inherit"`: whatever device the parent announces. This was the behaviour before this change; in a dGPU boot it
  meant the dGPU.
* `"software"`: Mesa llvmpipe (`LIBGL_ALWAYS_SOFTWARE=1 GBM_ALWAYS_SOFTWARE=1`, Mesa's EGL vendor only).
* a profile id (`"igpu"`, `"dgpu"`), or `"/dev/dri/renderD<N>"`.

If `"auto"` names a GPU that is absent in this boot, the session degrades to `"inherit"` and records why in
`session.json` (`render.degraded`). An explicit override that cannot be met is an error.

**How the node is enforced.** A nested Hyprland runs Aquamarine's Wayland backend. That backend has no setting
for a render node: it opens the `main_device` that the parent announces in `zwp_linux_dmabuf_feedback_v1`. A
bind-mounted alias over `/dev/dri/renderD129` does not work either, because libdrm resolves the announced `dev_t` by
scanning `/dev/dri` for a node with that `st_rdev` (`drmGetDeviceFromDevId failed`, measured). So for a chosen node,
`nested-session` puts a small pass-through between the nested compositor and the parent
(`<runtime>/parent-wayland`, `wl_device_proxy.rs`). The proxy changes only linux-dmabuf feedback:

* `main_device` and `tranche_target_device` become the chosen render node.
* The format table keeps only `DRM_FORMAT_MOD_LINEAR` entries, the one layout that both GPUs can read, so the
  parent (NVIDIA) can import buffers the nested compositor rendered on the iGPU.

Every other byte and fd is forwarded unchanged. The nested compositor's own clients follow its own main device, so
they also render on the iGPU (and get `DRI_PRIME` = the iGPU).

`session.json` records the choice:
`"render": {"policy", "profile", "rule", "mode", "node", "pci", "driver", "degraded", "parent_proxy"}`. To see the
node that the compositor actually opened:

```sh
pid=$(jq .instance.pid "$XDG_RUNTIME_DIR"/mios-nested-desktop-*/session.json)
for f in /proc/$pid/fd/*; do case $(readlink $f) in /dev/dri/*) echo "$(readlink $f) $(grep -h drm-pdev /proc/$pid/fdinfo/${f##*/})";; esac; done
```

**iGPU boots.** `"software"` was measured as not viable for a nested Hyprland 0.56 compositor (see the lane report
`gpu-display-routing.md`), so the iGPU profile uses `"igpu"`.

## The login greeter on the dGPU's display

The kernel console belongs to the firmware's boot VGA (the iGPU) unless `fbcon=` says otherwise, and tuigreet on VT
1 draws through that console. So in a dGPU boot the greeter showed only on the iGPU's display. Two fixes stack:

1. **Greeter (`greeter_display = "compositor"` in the dGPU profile).** `mios-gen hyprland-greeter` (the greetd
   command) runs `mios-greeter-session --client terminal --drm-device <dGPU card>`. That is cage, restricted to the
   dGPU card (`WLR_DRM_DEVICES`), running `[desktop.greeter.display_host].terminal` (alacritty), which runs the
   **same tuigreet argv** as the VT greeter: the same flags, theme and keys. The terminal uses the SSOT console
   palette (`[colors].ansi_*`) and `[theme.font]`. The launcher falls back to the VT greeter whenever cage, the
   terminal or tuigreet fails, when the card is not a character device, and when no `ready` heartbeat arrives within
   `start_timeout_s` / `heartbeat_timeout_s`. It never leaves you at a dead end. The glass greeter kind gets the same
   `--drm-device`. The iGPU profile keeps `greeter_display = "vt"`.
2. **Kernel console (`fbcon=map:10` in the dGPU `boot_append`).** This puts the console (boot text, VT 1 and the VT
   fallback greeter) on the dGPU. The map is per console and repeats: tty1→fb1, tty2→fb0, tty3→fb1, and so on. On
   this machine amdgpu is in the initramfs and registers fb0 first, and nvidia-drm's fbdev registers fb1 later from
   the root filesystem. If fb1 never registers (nvidia fbdev off or failed), fbcon keeps every console on fb0, the
   iGPU, so the console is never blank. Hyprland is not affected.

**Boot display report.** `mios-boot-display-report.service` runs `mios-gen boot-display-report` once per boot. It
is read-only and needs only CAP_SYSLOG to read `/dev/kmsg`. journald does not store kernel messages
(`ReadKMsg=no`), so this service is the record. It logs one JSON line with:

* the DRM cards, their drivers and connectors
* the fb registration order from the kernel log
* fbcon's `map:`
* `verdict.tty1_fb`, `verdict.profile_fb`, `verdict.tty1_on_profile_gpu` and `verdict.recommended_fbcon` (the map
  that would put tty1 on the booted profile's GPU)

Read it with `journalctl -b -u mios-boot-display-report -o cat | jq .verdict`.

**Optional: a dark iGPU output.** You can add `video=HDMI-A-1:d` (the iGPU's connector) to the dGPU `boot_append`.
It forces that output off at the kernel level, so a monitor wired to both GPUs switches to the dGPU input on its
own. It is not the default, for two reasons:

* It also removes the iGPU console fallback if the NVIDIA driver fails to load.
* Connector names are global and depend on probe order: amdgpu probes first, so its connectors are DP-1, DP-2 and
  HDMI-A-1, and NVIDIA's are DP-3 to DP-5 and HDMI-A-2.

## GRUB and firmware screens

GRUB, the firmware logo and the firmware setup draw on the firmware's primary display: its EFI GOP, the boot VGA.
On this machine that is the iGPU (`boot_vga=1` on `0000:7a:00.0`). No kernel argument or MiOS setting can move
them, because they run before Linux. Only the firmware setting can: the BIOS "Primary display" / "Initiate graphic
adapter" option set to PEG/dGPU. Only the operator can change it. If the firmware primary becomes the dGPU, the
iGPU entry still works: its console then follows whichever fb is primary, and the compositor follows
`AQ_DRM_DEVICES` as before.

## Verify after a reboot into `MiOS dGPU`

1. Boot text and then the tuigreet login appear on the **DP** (dGPU) input. The tuigreet look and keys are
   unchanged.
2. `journalctl -b -u mios-boot-display-report -o cat | jq .verdict` shows `"tty1_on_profile_gpu": true` and
   `"profile_fb": 1`. If it says false, set the dGPU `boot_append` map to `verdict.recommended_fbcon` and re-render
   the entries.
3. `journalctl -b -t mios-greeter-session` shows `started terminal greeter (... DRM /dev/dri/card0)` and no
   `starting text greeter`.
4. After login: `tr '\0' '\n' </proc/$(pgrep -o -x Hyprland)/environ | grep AQ_DRM` lists only the dGPU card, and
   the HDMI (iGPU) output is not lit by Hyprland.
5. `mios-gen nested-session --layout pip`: `session.json` shows `render.node = /dev/dri/renderD128` and
   `render.pci = 0000:7a:00.0`, and the fd check above shows `drm-pdev: 0000:7a:00.0` (amdgpu).

The `MiOS iGPU` entry and `MiOS Recovery` are unchanged. Both stay available in GRUB, which is shown on the
firmware display.

## Revert

* **Light both GPUs' outputs again:** set `include_other_devices = true` in `[desktop.hyprland.hardware_profiles.dgpu]`
  (layered: `/etc/mios/mios.toml` or the user SSOT). It takes effect at the next login.
* **Nested sessions as before:** set `[desktop.nested].render_node = "inherit"`.
* **VT greeter in dGPU boots:** set `greeter_display = "vt"` in the dGPU profile. It takes effect at the next
  greeter start.
* **Console karg:** remove `"fbcon=map:10"` from the dGPU `boot_append`, re-render the managed entries
  (`render-hyprland --boot-entry`), then `render-grub --check`. A one-off alternative: press `e` in GRUB and delete
  `fbcon=map:10` for that boot.
