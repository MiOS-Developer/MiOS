<!-- AI-hint: Manual pages distilled from the source comments of display, sanitized, each passage anchored to the comment it came from. -->

# display

### MiOS Looking Glass B6 Client Configuration & Direct Input...

MiOS Looking Glass B6 Client Configuration & Direct Input Manager.

Manages Looking Glass B6 client.ini generation/parsing, direct SPICE UNIX socket
input configuration (/var/run/libvirt/qemu/<vm>-spice.sock), Hyprland windowrulev2
rules, GNOME custom keybinding integration, and client execution argument synthesis.

<!-- mios-src:5f3978004e21 from usr/libexec/mios/display/looking_glass.py:4-10 -->

### MiOS Multi-Monitor Looking Glass Display Geometry & Cursor...

MiOS Multi-Monitor Looking Glass Display Geometry & Cursor Synchronizer.

Calculates power-of-2 IVSHMEM buffer sizing across display resolutions (1080p, 1440p,
4K, Ultrawide, 8K), parses Wayland / Hyprland monitor topologies, computes cross-monitor
cursor warp transitions, and generates multi-head libvirt XML blocks, Hyprland window rules,
and synchronized multi-instance client launchers.

<!-- mios-src:7bf5a3aeea35 from usr/libexec/mios/display/multimonitor_sync.py:4-11 -->

### Computes cursor coordinate transformation and boundary...

Computes cursor coordinate transformation and boundary crossing between monitors.

        Given coordinates (x, y) relative to source_head's top-left origin:
        - Detects if cursor crosses right/left/top/bottom boundaries.
        - Identifies matching adjacent head in the global topology.
        - Returns transformed target head and target (x, y) coordinates.

<!-- mios-src:4e3aaffe24be from usr/libexec/mios/display/multimonitor_sync.py:191-198 -->
### Builds Looking Glass client launch command argument list....

Builds Looking Glass client launch command argument list.

        resolve_fallback=True is for an immediate launch on this host: it applies
        the /dev/shm fallback when /dev/kvmfr0 is missing. Leave it False when
        the arguments are stored (scripts, rules) for a later boot.

<!-- mios-src:daa1ab6794de from usr/libexec/mios/display/looking_glass.py:274-279 -->
