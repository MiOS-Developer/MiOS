#!/bin/bash
# e2e.sh <config dir> <seconds> <label> [xfreerdp args...] : private native server + xfreerdp in Xvfb
T=$1; SECS=$2; L=$3; shift 3
B=${MIOS_RDP_BIN:-/var/lib/mios-build/rdp-native/target/fast/mios-rdp-server}
H=$(dirname "$(readlink -f "$0")")
OUT=$T/run-$L; mkdir -p $OUT
eval "$(python3 -I $H/liveenv.py)"
pre_portal=$(grep -c fuse.portal /proc/self/mountinfo)
MIOS_RDP_LOG=${MIOS_RDP_LOG:-info} $B --config $T/native.json > $OUT/server.log 2>&1 &
SP=$!
for i in $(seq 50); do ss -ltn | grep -q "127.0.0.1:18301" && break; sleep 0.1; done
DISP=:$((90 + RANDOM % 9))
Xvfb $DISP -screen 0 2200x2200x24 -nolisten tcp > $OUT/xvfb.log 2>&1 &
XP=$!
sleep 1
DISPLAY=$DISP timeout -s INT $SECS xfreerdp /v:127.0.0.1:18301 /u:mios-rdp-test /p:"$(cat $T/pw)" /cert:ignore "$@" > $OUT/client.log 2>&1
echo "client rc=$?" >> $OUT/client.log
sleep 1
cp $T/stats.json $OUT/stats-last.json 2>/dev/null
kill -TERM $SP; wait $SP; echo "server rc=$?" >> $OUT/server.log
kill $XP 2>/dev/null; wait $XP 2>/dev/null
post_portal=$(grep -c fuse.portal /proc/self/mountinfo)
left=$(hyprctl monitors -j | python3 -c 'import json,sys; print(sum(1 for m in json.load(sys.stdin) if m["name"].startswith("HEADLESS-MIOS-SCALETEST")))')
echo "{\"portal_before\": $pre_portal, \"portal_after\": $post_portal, \"scaletest_left\": $left, \"watchdog\": \"$(cat /sys/class/watchdog/watchdog0/state)\"}" > $OUT/guards.json
cat $OUT/guards.json
