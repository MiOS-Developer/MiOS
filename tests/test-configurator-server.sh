#!/usr/bin/env bash
# AI-hint: Contract suite for miosd's config server (mios.html & /portal/config): login-gated GET, lossless {"toml"} saves, redaction markers never written, delta-only user tier.
# AI-related: src/mios-rs/miosd/src/lib.rs, usr/share/mios/configurator/mios.html, usr/share/mios/mios.toml
#
# Tiers. The CANDIDATE tier runs against MIOS_MIOSD_BIN, a MiOS-DEV tree build of
# this checkout, and alone decides the exit status. With MIOS_MIOSD_BIN unset it is
# NOT RUN and the suite exits 77, which tests/run-suites.sh counts as a failure:
# the installed binary never certifies the candidate. The INSTALLED-COMPAT tier
# reports how the installed /usr/libexec/mios/miosd fares against the same
# contract; it is informational and never gates. This suite never builds (no
# cargo), binds a free ephemeral port, and runs every server on temp host/user
# tiers with a per-run session token, so no live tier, token or port is touched.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HTML_PATH="${REPO_ROOT}/usr/share/mios/configurator/mios.html"
INSTALLED=/usr/libexec/mios/miosd
TMP_DIR="$(mktemp -d)"
SERVER_PID=""

stop_server() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        SERVER_PID=""
    fi
}
trap 'stop_server; rm -rf "$TMP_DIR"' EXIT

# run_tier <label> <miosd>: one hermetic config-server, the whole contract against it.
run_tier() {
    local label=$1 bin=$2 d="$TMP_DIR/$1" port token rc=0
    mkdir -p "$d/run" "$d/xdg"
    # A lower-tier secret the GET must hide and a save must never copy upward.
    printf '[portal]\nsecret = "host-tier-cookie-secret"\n' > "$d/host.toml"
    port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
    token="cfg-test-$(od -An -tx8 -N8 /dev/urandom | tr -d ' \n')"
    env -u MIOS_CONFIG_BIND_ADDR -u MIOS_TOML -u MIOS_VENDOR_TOML -u MIOS_VENDOR_TOML_D \
        -u MIOS_TOML_ROOT -u MIOS_PORTAL_TOKEN -u MIOS_ACCOUNTS_DB_BACKED \
        MIOS_ROOT="$REPO_ROOT" MIOS_CONFIGURATOR_HTML="$HTML_PATH" \
        MIOS_HOST_TOML="$d/host.toml" MIOS_HOST_TOML_D="$d/host.d" \
        MIOS_USER_TOML="$d/user.toml" MIOS_USER_TOML_D="$d/user.d" \
        XDG_CONFIG_HOME="$d/xdg" XDG_RUNTIME_DIR="$d/run" MIOS_PORTAL_SESSION_TOKEN="$token" \
        "$bin" config-server --port "$port" > "$d/server.log" 2>&1 &
    SERVER_PID=$!
    python3 - "$label" "$port" "$token" "$d" <<'PY' || rc=$?
import http.client, json, os, sys, time, tomllib

label, port, token, d = sys.argv[1:]
origin, user = f"http://127.0.0.1:{port}", os.path.join(d, "user.toml")
fails = 0

def check(ok, what):
    global fails
    fails += not ok
    print(f"  [{label}] [{'PASS' if ok else 'FAIL'}] {what}", flush=True)

def call(method, path, body=None, headers=()):
    c = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
    c.request(method, path, body=body, headers=dict(headers))
    r = c.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read().decode("utf-8", "replace")
    c.close()
    return out

def field(body, key):
    try:
        return json.loads(body).get(key)
    except (ValueError, AttributeError):
        return None

def tier_text():
    return open(user, encoding="utf-8").read() if os.path.exists(user) else None

for _ in range(50):
    try:
        st, _, body = call("GET", "/health")
        break
    except OSError:
        time.sleep(0.2)
else:
    st, body = 0, ""
check(st == 200 and '"status":"ok"' in body and '"engine":"miosd-config-server"' in body,
      f"GET /health answers on free port {port}")
if st != 200:
    print(open(os.path.join(d, "server.log"), encoding="utf-8", errors="replace").read())
    sys.exit(1)
auth = {"Authorization": f"Bearer {token}"}
post_headers = {**auth, "Origin": origin, "Content-Type": "application/json"}

def save(text, headers=post_headers):
    return call("POST", "/portal/config", json.dumps({"toml": text}).encode(), headers)

st, _, _ = call("HEAD", "/health")
check(st == 200, "HEAD /health is 200")
st, h, _ = call("GET", "/")
check(st == 302 and h.get("location") == "/configure", "GET / redirects to /configure")
st, _, _ = call("GET", "/configure")
check(st == 403, f"GET /configure without a session is 403 (HTTP {st})")
st, h, body = call("GET", f"/configure?token={token}")
check(st == 200 and "<!DOCTYPE html>" in body and "mios_portal=" in h.get("set-cookie", ""),
      "GET /configure?token= serves mios.html and sets the session cookie")

# GATING: GET and HEAD /portal/config honour [portal].require_login like /configure.
for method in ("GET", "HEAD"):
    st, _, body = call(method, "/portal/config")
    check(st == 403 and "[identity]" not in body,
          f"{method} /portal/config without a session is 403 (HTTP {st})")
st, _, served = call("GET", "/portal/config", headers=auth)
check(st == 200 and "[identity]" in served,
      f"GET /portal/config with the session serves the layered TOML ({len(served)} bytes)")
check("host-tier-cookie-secret" not in served and '"<redacted>"' in served,
      "GET redacts the planted host-tier secret with the <redacted> marker")

# DELTA-ONLY + C3: a no-edit save of the echo (markers and all) writes nothing.
st, _, body = save(served)
check(st == 200 and field(body, "bytes") == 0 and tier_text() == "",
      f"a no-edit save of the GET echo writes nothing (HTTP {st}: {body[:160]!r}, tier {tier_text()!r:.160})")

# C3: user-tier secrets survive a save that echoed their redaction.
with open(user, "w", encoding="utf-8") as fh:
    fh.write('[portal]\npassword = "user-tier-portal-pw"\n\n[ai]\napi_key = "user-tier-api-key"\n')
st, _, served = call("GET", "/portal/config", headers=auth)
check(st == 200 and "user-tier-portal-pw" not in served and "user-tier-api-key" not in served,
      "GET redacts the planted user-tier secrets")
lines = served.split("\n")
start = lines.index("[identity]")
end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("[")), len(lines))
lines[next(j for j in range(start + 1, end) if lines[j].startswith("username = "))] = 'username = "test-operator"'
st, _, body = save("\n".join(lines))
text = tier_text() or ""
tier = tomllib.loads(text)
check(st == 200 and tier.get("identity", {}).get("username") == "test-operator",
      f"an edited echo saved as {{\"toml\": ...}} persists the edit (HTTP {st}: {body[:160]!r})")
check(tier.get("portal", {}).get("password") == "user-tier-portal-pw"
      and tier.get("ai", {}).get("api_key") == "user-tier-api-key",
      f"user-tier portal.password and ai.api_key keep their real values ({text[:200]!r})")
check("<redacted>" not in text and "host-tier-cookie-secret" not in text,
      "neither the marker nor a lower-tier secret reaches the user tier")
check("ports" not in tier, "unchanged [ports] stay in the lower tiers")

# Negative controls: none of these may write.
st, _, _ = save('[broken_section\nmissing_bracket = "invalid"\n')
check(st == 422, f"malformed TOML inside the envelope is 422 (HTTP {st})")
st, _, _ = save("\n".join(lines), {**post_headers, "Origin": "http://evil.example"})
check(st == 403, f"a cross-origin save is 403 (HTTP {st})")
st, _, _ = save("\n".join(lines), {"Origin": origin, "Content-Type": "application/json"})
check(st == 403, f"a save without the session is 403 (HTTP {st})")
check(tier_text() == text, "the rejected saves left the user tier byte-identical")
print(f"  [{label}] {fails} failed", flush=True)
sys.exit(1 if fails else 0)
PY
    stop_server
    return "$rc"
}

status=0
echo "[test-configurator-server] static: configurator asset"
if [[ -f "$HTML_PATH" ]] && grep -q "<!DOCTYPE html>" "$HTML_PATH" && grep -q "mios" "$HTML_PATH"; then
    echo "  [PASS] $HTML_PATH is an HTML5 document with MiOS bindings"
else
    echo "  [FAIL] configurator asset missing or not HTML5: $HTML_PATH" >&2
    status=1
fi

if [[ -x "$INSTALLED" && "$(readlink -f "$INSTALLED")" != "$(readlink -f "${MIOS_MIOSD_BIN:-/nonexistent}")" ]]; then
    echo "[test-configurator-server] installed-compat tier: $INSTALLED (informational; never gates)"
    if run_tier installed-compat "$INSTALLED"; then
        echo "  [installed-compat] COMPATIBLE with the current contract"
    else
        echo "  [installed-compat] NOT COMPATIBLE with the current contract (an older image binary)"
    fi
fi

if [[ -z "${MIOS_MIOSD_BIN:-}" ]]; then
    echo "[test-configurator-server] candidate tier NOT RUN: set MIOS_MIOSD_BIN to a MiOS-DEV tree build of this checkout" >&2
    exit 77
fi
if [[ ! -x "$MIOS_MIOSD_BIN" ]]; then
    echo "[test-configurator-server] FAIL: MIOS_MIOSD_BIN=$MIOS_MIOSD_BIN is not an executable" >&2
    exit 1
fi
echo "[test-configurator-server] candidate tier: $MIOS_MIOSD_BIN"
run_tier candidate "$MIOS_MIOSD_BIN" || status=1
if [[ "$status" -ne 0 ]]; then
    echo "[test-configurator-server] FAIL: the candidate does not meet the contract" >&2
    exit 1
fi
echo "[test-configurator-server] SUCCESS: the candidate meets the contract"
