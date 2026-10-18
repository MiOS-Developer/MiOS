import glob, os
RT = "/run/user/1000"
best, bt = None, -1
for lock in glob.glob(f"{RT}/hypr/*/hyprland.lock"):
    try:
        pid, wl = open(lock).read().split("\n")[:2]
        pid = int(pid); os.kill(pid, 0)
        env = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
        if any(e.startswith((b"WAYLAND_DISPLAY=", b"DISPLAY=")) and len(e.split(b"=", 1)[1]) for e in env):
            continue
        t = os.stat(lock).st_mtime
        if t > bt and os.path.exists(f"{RT}/{wl}"):
            best, bt = (os.path.basename(os.path.dirname(lock)), wl), t
    except (OSError, ValueError):
        continue
print(f"export HYPRLAND_INSTANCE_SIGNATURE={best[0]} WAYLAND_DISPLAY={best[1]} XDG_RUNTIME_DIR={RT}")
