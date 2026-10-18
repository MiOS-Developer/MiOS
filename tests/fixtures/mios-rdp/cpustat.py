#!/usr/bin/python3 -I
"""CPU% per process (and its busiest thread) over a window: cpustat.py SECS PID..."""
import os, sys, time, json
secs = float(sys.argv[1]); pids = [p for p in sys.argv[2:]]
HZ = os.sysconf("SC_CLK_TCK")
def snap(pid):
    out = {}
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            with open(f"/proc/{pid}/task/{tid}/stat") as f:
                s = f.read()
            name = s[s.index("(") + 1:s.rindex(")")]
            fl = s[s.rindex(")") + 2:].split()
            out[tid] = (name, int(fl[11]) + int(fl[12]))
    except OSError:
        pass
    return out
a = {p: snap(p) for p in pids}; t0 = time.monotonic(); time.sleep(secs); b = {p: snap(p) for p in pids}
dt = time.monotonic() - t0; res = {}
for p in pids:
    tot = 0; best = (0, "")
    for tid, (name, t) in b[p].items():
        d = t - a[p].get(tid, (name, t))[1]
        tot += d
        if d > best[0]:
            best = (d, name)
    try:
        comm = open(f"/proc/{p}/comm").read().strip()
    except OSError:
        comm = "?"
    res[f"{comm}:{p}"] = {"cpu_pct": round(100 * tot / HZ / dt, 1), "top_thread": best[1], "top_thread_pct": round(100 * best[0] / HZ / dt, 1)}
print(json.dumps(res))
