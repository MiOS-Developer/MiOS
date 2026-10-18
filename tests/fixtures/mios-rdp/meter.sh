#!/bin/bash
# meter.sh <secs> <outputs csv> <label> -> median/min presented FPS per output under continuous damage (+ GPU busy)
H=$(dirname "$(readlink -f "$0")")
secs=${1:-8}; outs=${2:-HDMI-A-1}; label=${3:-m}
python3 -I $H/gpustat.py $((secs - 2)) > $H/gpu-$label.json &
GP=$!
WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland QT_FORCE_STDERR_LOGGING=1 timeout -s INT $secs /usr/lib64/qt6/bin/qml $H/framemeter.qml -- "$outs" > $H/fps-$label.txt 2>&1
wait $GP
python3 -I - $H/fps-$label.txt $H/gpu-$label.json <<'PY'
import json, sys
rows = [json.loads(l.split("FPS ", 1)[1]) for l in open(sys.argv[1]) if "FPS {" in l][1:]
out = {}
for name in sorted({k for r in rows for k in r}):
    v = sorted(r.get(name, 0) for r in rows)
    out[name] = {"median": v[len(v) // 2], "min": v[0], "max": v[-1], "n": len(v)}
g = json.load(open(sys.argv[2]))
print(json.dumps({"fps": out, "gpu_busy": g["busy_avg"], "gfx": g["gfx_pct_by_proc"][:6]}))
PY
