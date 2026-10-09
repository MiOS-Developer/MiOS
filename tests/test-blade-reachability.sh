#!/usr/bin/env bash
# AI-hint: bash Proves `mios blade status` answers the one question a MiOS-Metal seat has -- is my blade there? On a seat every offload target is REMOTE, an...
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0

log() { printf '[blade-reachability] %s\n' "$*"; }
die() { printf '[blade-reachability] ERROR: %s\n' "$*" >&2; exit 1; }
ok()  { PASS=$((PASS + 1)); log "ok: $*"; }

command -v curl >/dev/null 2>&1 || { log "SKIP: curl absent"; exit 0; }
# `hostname` is not installed everywhere (minimal container images ship without
# it), and an absent binary made this suite die at exit 127 before testing
# anything. uname -n is in coreutils and answers the same question.
HOST="$(hostname 2>/dev/null || uname -n)"
PY="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)" \
    || die "no python interpreter for the probe server"
# Keep the host name only if it RESOLVES, else fall back to loopback. getent
# answers that where it exists; without it the resolver is asked about the SAME
# name, so the name is still verified rather than swapped for an unchecked one.
if command -v getent >/dev/null 2>&1; then
    getent hosts "$HOST" >/dev/null 2>&1 || HOST="127.0.0.1"
else
    "$PY" -c 'import socket, sys; socket.gethostbyname(sys.argv[1])' "$HOST" \
        >/dev/null 2>&1 || HOST="127.0.0.1"
fi

FIXTURE="$(mktemp -d)"
export MIOS_PATHS_BLADE_ENV="${FIXTURE}/blade.env"
printf '%s\n' 'MIOS_BLADE_TYPE=WS-BLADE' 'MIOS_BLADE_CAPS=service-plane' >"$MIOS_PATHS_BLADE_ENV"
SRV_PID=""
stop_server() {
    if [ -n "$SRV_PID" ]; then
        kill "$SRV_PID" 2>/dev/null || true
        wait "$SRV_PID" 2>/dev/null || true
    fi
}
cleanup() { stop_server; rm -rf "$FIXTURE"; }
trap cleanup EXIT

# Ephemeral port (a stale fixed-port listener faked a pass); started directly so $! is
# the server cleanup kills, output to /dev/null so a $(...) caller never hangs.
"$PY" - "${FIXTURE}/port" >/dev/null 2>&1 <<'SRV' &
import http.server, socketserver, sys

class Q(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass

socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.TCPServer(("0.0.0.0", 0), Q)
with open(sys.argv[1], "w") as fh:
    fh.write(str(srv.server_address[1]))
srv.serve_forever()
SRV
SRV_PID=$!

PORT=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -s "${FIXTURE}/port" ] && PORT="$(tr -d '\r' <"${FIXTURE}/port")" && break
    sleep 0.3
done
[ -n "$PORT" ] || { log "SKIP: the probe server never reported a port"; exit 0; }

for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -sS -o /dev/null --max-time 1 "http://${HOST}:${PORT}/" 2>/dev/null && break
    sleep 0.3
done
curl -sS -o /dev/null --max-time 2 "http://${HOST}:${PORT}/" 2>/dev/null \
    || die "bound port ${PORT} but could not reach it -- the probe is not proving anything"
# A port nothing is listening on. +1 from an ephemeral port is free in
# practice; the assertion below fails loudly if it ever is not.
DEAD=$((PORT + 1))
cat > "${FIXTURE}/mios.toml" <<EOF
[ai]
endpoint = "http://${HOST}:${PORT}/v1"

[search]
endpoint = "http://${HOST}:${DEAD}/"
EOF

run_status() {
    MIOS_USR_DIR="${ROOT}/usr/lib/mios" \
    MIOS_ETC_DIR="$FIXTURE" \
    MIOS_HOST_TOML="${FIXTURE}/mios.toml" \
    MIOS_USER_TOML=/dev/null \
    MIOS_BLADE_PROBE_TIMEOUT=2 \
    MIOS_PORTS_CPU_NODE=8510 MIOS_PORTS_LLM_LIGHT=8500 \
    MIOS_PORTS_LLM_HEAVY=8520 \
        bash "${ROOT}/usr/libexec/mios/mios-blade" status 2>&1
}

OUT="$(run_status)"

grep -q '^Blade Type:   WS-BLADE$' <<<"$OUT" \
    || die "status ignored the selected runtime state fixture: $OUT"

grep -q '^Offload targets:' <<<"$OUT" \
    || die "status does not report offload targets:
$OUT"
ok "status reports where this blade's services live"

grep -qE "^  ai +REMOTE +up " <<<"$OUT" \
    || die "the overlay's AI endpoint must read REMOTE and up:
$OUT"
ok "an offloaded target that answers reads REMOTE up"

grep -qE "^  search +REMOTE +UNREACHABLE " <<<"$OUT" \
    || die "an offloaded target that does NOT answer must read UNREACHABLE:
$OUT"
ok "an offloaded target that does not answer reads REMOTE UNREACHABLE"

grep -qE "^  node:local-heavy +local " <<<"$OUT" \
    || die "a target the overlay does not name must stay local:
$OUT"
ok "targets the overlay does not name stay local"

# The seat/blade tell: with NO overlay every target is local.
OUT_LOCAL="$(MIOS_USR_DIR="${ROOT}/usr/lib/mios" MIOS_ETC_DIR="$FIXTURE" \
    MIOS_HOST_TOML=/dev/null MIOS_USER_TOML=/dev/null MIOS_BLADE_PROBE_TIMEOUT=1 \
    MIOS_PORTS_AGENT_PIPE=8700 MIOS_PORTS_SEARXNG=8800 MIOS_PORTS_CPU_NODE=8510 \
    MIOS_PORTS_LLM_LIGHT=8500 MIOS_PORTS_LLM_HEAVY=8520 \
    bash "${ROOT}/usr/libexec/mios/mios-blade" status 2>&1)"
grep -q 'REMOTE' <<<"$OUT_LOCAL" \
    && die "with no overlay nothing should be REMOTE:
$OUT_LOCAL"
ok "with no overlay every target is local -- that is the seat/blade tell"

# An unresolved placeholder must be VISIBLE, never probed as a literal URL.
OUT_RAW="$(
    for p in $(compgen -v MIOS_PORT_ 2>/dev/null || true); do unset "$p"; done
    MIOS_USR_DIR="${ROOT}/usr/lib/mios" MIOS_ETC_DIR="$FIXTURE" \
    MIOS_HOST_TOML=/dev/null MIOS_USER_TOML=/dev/null MIOS_BLADE_PROBE_TIMEOUT=1 \
    bash "${ROOT}/usr/libexec/mios/mios-blade" status 2>&1)"
grep -q 'UNRESOLVED' <<<"$OUT_RAW" \
    || die "an unexpanded \${MIOS_PORT_*} must be reported UNRESOLVED, not probed:
$OUT_RAW"
ok "an unexpanded placeholder is reported, not silently probed"

# The fixture must not outlive the suite: stop the server the way cleanup does
# and prove the port is closed. A wrapper whose pid is not the server's leaves
# an orphan listener on 0.0.0.0 that holds a capturing caller's pipe open.
stop_server
SRV_PID=""
curl -sS -o /dev/null --max-time 1 "http://${HOST}:${PORT}/" 2>/dev/null \
    && die "the probe server is still listening on ${PORT} after teardown -- it leaked"
ok "the probe server is gone after teardown (no leaked listener)"

log "PASS: ${PASS}/7 assertions"
