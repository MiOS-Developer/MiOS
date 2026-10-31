<!-- AI-hint: Per-boot-profile GPU display vs render routing: which GPU lights outputs (the dGPU session leaves the iGPU headless), which render node nested sessions use (dGPU boots -> the headless iGPU), where the login greeter and the kernel console appear, the boot display report, verification after a reboot and how to revert. -->
<!-- AI-related: usr/share/mios/mios.toml, tools/native/mios-gen/src/gpu_topology.rs, tools/native/mios-gen/src/wl_device_proxy.rs, tools/native/mios-gen/src/greeter.rs, tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/render.rs, usr/libexec/mios/mios-greeter-session, tests/test-gpu-display-routing.py -->

# Desktop GPU routing: display vs render per boot profile

MiOS boots one of two desktop hardware profiles, chosen in the GRUB menu (`mios.desktop.gpu=`):

| | iGPU boot (`MiOS iGPU`) | dGPU boot (`MiOS dGPU`) |
|---|---|---|
| dGPU driver | `vfio-pci` (reserved for VMs) | `nvidia` (`nvidia-drm.modeset=1 nvidia-drm.fbdev=1`) |
| Compositor lights | the iGPU's outputs | **only the dGPU's outputs**. The iGPU is headless. |
| Compositor renders on | iGPU | dGPU (`DRI_PRIME` = dGPU) |
| Nested sessions render on | iGPU (`nested_render = "igpu"`) | rule: the headless iGPU (`nested_render = "igpu"`); by default degraded to the dGPU until the NVIDIA parent can show iGPU buffers (`cross_vendor_present`, below) |
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

**Measured limit: an NVIDIA parent cannot show iGPU buffers.** In a dGPU boot the nested compositor does render on
`renderD128` (the amdgpu fdinfo shows `drm-pdev: 0000:7a:00.0`, and no process of the session opens `renderD129`).
The parent Hyprland on the RTX 4090 does not show those buffers, though, for two reasons:

* Its linux-dmabuf table lists no LINEAR modifier, only NVIDIA block-linear, INVALID, and AMD-tiled entries from the
  iGPU scanout tranche.
* AMD-tiled buffers fail `eglCreateImageKHR` ("requested buffer attributes are not supported"), and LINEAR buffers
  offered through the proxy are not displayed.

So `[desktop.nested].cross_vendor_present = false` (the default) makes `"auto"` keep a nested session on a GPU of
the parent's driver family. The session stays visible, and `session.json` `render.degraded` says why.
`cross_vendor_present = true`, or an explicit `render_node = "igpu"`, forces the iGPU anyway: the pipeline is in
place, but the PiP shows black. A visible iGPU nested session on an NVIDIA parent needs a copy path (CPU or GPU)
in the proxy, which is follow-up work. During a GPU hand-off (below) the parent itself runs on the iGPU, so `"auto"`
puts nested sessions on the iGPU and they are visible.

`session.json` records the choice:
`"render": {"policy", "profile", "rule", "mode", "node", "pci", "driver", "degraded", "parent_proxy"}`. To see the
node that the compositor actually opened:

```sh
pid=$(jq .instance.pid "$XDG_RUNTIME_DIR"/mios-nested-desktop-*/session.json)
for f in /proc/$pid/fd/*; do case $(readlink $f) in /dev/dri/*) echo "$(readlink $f) $(grep -h drm-pdev /proc/$pid/fdinfo/${f##*/})";; esac; done
```

**iGPU boots.** The iGPU profile uses `"igpu"`. `"software"` is offered as an explicit override, but it is not the
default: Hyprland 0.56 needs a GBM/EGL device on the main device its Wayland backend opens, and Mesa's software
paths need a KMS node with dumb buffers, which a render node lacks. It has not been measured live; see the lane
report.

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

## Nested sessions and GPU memory (GTT)

The iGPU (Radeon 610M) has a 1 GiB VRAM carve-out and a GTT window of 23.4 GB of system RAM
(`mem_info_gtt_total` = 24524595200). Pages in GTT come out of `MemAvailable`, and the kernel does not charge them
to the session's memory cgroup.

Measured on 2026-10-31: one idle nested PiP session for 25 s, sampled every second with `/proc/<pid>/fdinfo` and
`mem_info_gtt_used`.

| render node | session processes on the node | `drm-memory-gtt` (sum) | iGPU GTT used | MemAvailable drop |
|---|---|---|---|---|
| `renderD128` (iGPU, amdgpu) | Hyprland, Xwayland, alacritty, quickshell | 65 MiB (Hyprland 15, Xwayland 10, alacritty 20, quickshell 20); VRAM 161 MiB | 21 → 75 MiB (peak 89) | ~1.1 GB |
| `renderD129` (dGPU, nvidia) | the same, plus 2 tmux processes that inherited a dGPU fd | not reported (NVIDIA fdinfo has no `drm-memory-*` keys) | unchanged (21 MiB) | ~0.8 GB |

An idle nested session on either node used a bounded amount of memory in this run. The 28 GB → 0 runaway seen
earlier did not reproduce in 25 s. Because the window is 23 GB, though, a GTT leak on the iGPU could still take all
memory, so the proposal (not applied) is:

* **Kernel cap per boot profile.** Add an SSOT key `gtt_limit_mb` in `[desktop.hyprland.hardware_profiles.<p>]`,
  projected into that profile's `boot_append` as `ttm.pages_limit=<mb*256>` plus `amdgpu.gttsize=<mb>` (the latter
  is deprecated but still read). Suggested values: dGPU boot 4096 (the iGPU then serves only nested sessions, about
  70 MiB each); iGPU boot 12288 (the desktop runs there). TTM refuses allocations past the cap instead of draining
  RAM. It takes effect at the next boot, and the iGPU entry changes only if the operator sets it there.
* **Per-session guard.** `nested-session` reads the session's `drm-memory-gtt` from fdinfo (amdgpu reports it per
  client) at its existing poll. Past `[desktop.nested].gtt_limit_mb` (suggested 1024), it ends the session the same
  way a closed PiP window does.
* **mem-admit.** `MemAvailable` already excludes allocated GTT pages, so admission sees what is used. What it
  cannot see is the GTT headroom, so admission should also require
  `mem_info_gtt_total - mem_info_gtt_used >= min_available_mb` on the target GPU, and should log the current GTT
  in use.

## One monitor, two inputs

This machine has one physical monitor wired to both GPUs: DP-3 on the RTX 4090 and HDMI-A-1 on the iGPU. In a dGPU
boot, everything after the kernel takes over moves to DP:

* the boot text and the VT console (`fbcon=map:10`)
* the greeter (hosted on the dGPU card)
* the desktop (`AQ_DRM_DEVICES` = the dGPU card only)

The HDMI input keeps only the firmware/GRUB picture and the last fbcon frame of tty2, so the monitor can stay on DP.

## GPU hand-off to a VM (Looking Glass) and back

In a dGPU boot the RTX 4090 can be handed to a VM on demand. Hyprland cannot drop its primary GPU while it runs:
the DRM fd keeps the nvidia module busy, so `vfio-pci` cannot take the device. The hand-off is therefore a session
switch, not a live re-plug. The SSOT key is `handoff_fallback = "igpu"` in `[desktop.hyprland.hardware_profiles.dgpu]`.

**Hand-off:**

1. Log out of the dGPU session. Every process that holds `/dev/dri/card0` or `renderD129` must end, including
   nested sessions that run on the dGPU.
2. A privileged step, outside this lane and run by the VM tooling, unbinds `nvidia` and binds `vfio-pci`, then
   starts the VM.
3. fbcon loses fb1 and moves every console back to fb0 (the iGPU). The greeter's `display_plan` finds no dGPU card
   and uses the VT greeter on the iGPU (HDMI), and it makes the fallback profile the login default.
4. Log in: `hyprland-session` sees that the boot profile's GPU is unbound and that `handoff_fallback` names a bound
   GPU. It starts the `igpu` profile **in place**, with no reboot (`--dry-run` prints `"handoff": "igpu"`), and it
   lights the iGPU outputs. Choosing the dGPU session in this state also starts the fallback.

**Return:**

1. Stop the VM and log out of the fallback session.
2. A privileged step unbinds `vfio-pci` and binds `nvidia` again.
3. nvidia-drm registers its fb again, and fbcon moves tty1 back to the dGPU through the boot map. The greeter is
   again hosted on the dGPU.
4. Log in: the dGPU profile runs again.

None of this changes `vfio-pci.ids=` or the boot entries. The iGPU boot entry, which reserves the dGPU for VMs at
boot, is unchanged.

## GRUB and firmware screens

GRUB, the firmware logo and the firmware setup draw on the firmware's primary display: its EFI GOP, the boot VGA.
On this machine that is the iGPU (`boot_vga=1` on `0000:7a:00.0`), and the BIOS keeps it that way, so **GRUB stays on
HDMI**. No kernel argument or MiOS setting can move it, because it runs before Linux. Only the firmware setting can: the BIOS "Primary display" / "Initiate graphic
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
5. `mios-gen nested-session --layout pip`: `session.json` `render` shows the rule `igpu` and, with the default
   `cross_vendor_present = false`, `degraded` naming the NVIDIA parent. With `render_node = "igpu"` the fd check
   above shows `drm-pdev: 0000:7a:00.0` (amdgpu) only.

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
