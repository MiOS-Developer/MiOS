#!/bin/bash
H=$(dirname "$(readlink -f "$0")")
B=/var/lib/mios-build/rdp-native/target/fast/mios-rdp-server
label=$1; shift
$H/meter.sh 12 HDMI-A-1,HEADLESS-MIOS-SCALETEST $label > $H/meter-$label.json &
M=$!
sleep 1
"$@" 2>/dev/null > $H/cap-$label.json
wait $M
python3 -I -c 'import json,sys; m=json.load(open(sys.argv[1])); c=json.load(open(sys.argv[2])) if open(sys.argv[2]).read().strip() else {}; print(json.dumps({"meter": m, "capture": {k: c.get(k) for k in ("fps","convert_ms_p50","convert_ms_p95","capture_wait_ms_p50","encode_ms_p50","encode_ms_p95","cpu_cores")}}))' $H/meter-$label.json $H/cap-$label.json
