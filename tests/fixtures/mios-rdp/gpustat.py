#!/usr/bin/python3 -I
"""GPU busy % (sysfs, sampled) + per-process gfx engine share from DRM fdinfo over a window."""
import os, sys, time, json, glob
CARD = "/sys/class/drm/card1/device/gpu_busy_percent"
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0

def fdinfo():
    res = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fdinfo")
        except OSError:
            continue
        seen = {}
        for fd in fds:
            try:
                with open(f"/proc/{pid}/fdinfo/{fd}") as f:
                    txt = f.read()
            except OSError:
                continue
            if "drm-engine-gfx" not in txt:
                continue
            d = dict(l.split(":", 1) for l in txt.splitlines() if ":" in l)
            cid = d.get("drm-client-id", "").strip()
            if cid in seen:
                continue
            ns = 0
            for k in ("drm-engine-gfx", "drm-engine-compute"):
                v = d.get(k, "").strip().split()
                if v:
                    ns += int(v[0])
            seen[cid] = ns
        if seen:
            try:
                comm = open(f"/proc/{pid}/comm").read().strip()
            except OSError:
                comm = "?"
            res[pid] = (comm, sum(seen.values()))
    return res

a = fdinfo(); t0 = time.monotonic()
busy = []
while time.monotonic() - t0 < secs:
    try:
        busy.append(int(open(CARD).read()))
    except OSError:
        pass
    time.sleep(0.05)
b = fdinfo(); dt = time.monotonic() - t0
share = []
for pid, (comm, ns) in b.items():
    d = ns - a.get(pid, (comm, ns))[1]
    if d > 0:
        share.append((round(100 * d / (dt * 1e9), 1), pid, comm))
share.sort(reverse=True)
print(json.dumps({"busy_avg": round(sum(busy) / max(1, len(busy)), 1), "busy_min": min(busy or [0]),
                  "busy_max": max(busy or [0]), "gfx_pct_by_proc": share[:8]}))
