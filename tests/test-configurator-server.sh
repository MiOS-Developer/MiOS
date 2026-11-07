#!/usr/bin/env bash
# AI-hint: Contract suite for miosd's config server (mios.html & /portal/config): login-gated GET, lossless {"toml"} saves, redaction markers never written, delta-only user tier.
# AI-related: src/mios-rs/miosd/src/lib.rs, usr/share/mios/configurator/mios.html, usr/share/mios/mios.toml
#
# Tiers. The CANDIDATE tier alone decides the exit status. Its binary is
# MIOS_MIOSD_BIN (a MiOS-DEV tree build of this checkout), else this checkout's own
# cargo build of miosd -- CI's `cargo build -p miosd` step leaves it at
# src/mios-rs/target/debug/miosd -- when that build's dep-info shows it was compiled
# from these sources and none of them is newer. With neither the candidate is NOT
# RUN and the suite exits 77, which tests/run-suites.sh counts as a failure (it also
# scrubs MIOS_MIOSD_BIN): the installed binary never certifies the candidate. The
# INSTALLED-COMPAT tier
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

# tree_build: the freshest cargo build of miosd under this checkout whose dep-info
# lists this tree's miosd/src/lib.rs and no source newer than the binary.
tree_build() {
    python3 - "$REPO_ROOT" <<'PY'
import glob, os, re, sys

root = os.path.realpath(sys.argv[1])
lib = os.path.join(root, "src", "mios-rs", "miosd", "src", "lib.rs")
manifests = [os.path.join(root, "src", "mios-rs", *rel) for rel in
             (("Cargo.lock",), ("Cargo.toml",), ("miosd", "Cargo.toml"))]
fresh, why = [], []
for pattern in ("src/mios-rs/target/*/miosd", "src/mios-rs/target/*/*/miosd",
                "tools/native/target/*/miosd", "tools/native/target/*/*/miosd"):
    for binary in sorted(glob.glob(os.path.join(root, pattern))):
        if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
            continue
        try:
            with open(binary + ".d", encoding="utf-8") as fh:
                rule = fh.readline()
        except OSError:
            why.append(f"{binary}: no cargo dep-info beside it")
            continue
        sources = {os.path.realpath(s.replace("\\ ", " "))
                   for s in re.split(r"(?<!\\) ", rule.partition(": ")[2].strip()) if s}
        if lib not in sources:
            why.append(f"{binary}: compiled from another tree")
            continue
        built = os.stat(binary).st_mtime
        code = [s for s in sources if s.startswith(root + os.sep) and s.endswith(".rs")] + manifests
        newer = [s for s in code if not os.path.exists(s) or os.stat(s).st_mtime > built]
        if newer:
            why.append(f"{binary}: older than {len(newer)} of its sources, e.g. {newer[0]}")
            continue
        fresh.append((built, binary))
if fresh:
    print(max(fresh)[1])
else:
    print("; ".join(why) or "no cargo build of miosd under this checkout", file=sys.stderr)
    sys.exit(1)
PY
}

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
    python3 - "$label" "$port" "$token" "$d" "$REPO_ROOT" <<'PY' || rc=$?
import http.client, json, os, subprocess, sys, time, tomllib

label, port, token, d, repo = sys.argv[1:]
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

def edit(text, section, key, value):
    lines = text.split("\n")
    start = lines.index(f"[{section}]")
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("[")), len(lines))
    lines[next(j for j in range(start + 1, end) if lines[j].startswith(f"{key} = "))] = f"{key} = {value}"
    return "\n".join(lines)

def kept(tier):
    return (tier.get("portal", {}).get("password") == "user-tier-portal-pw"
            and tier.get("ai", {}).get("api_key") == "user-tier-api-key"
            and tier.get("git", {}).get("github_token") == "user-tier-gh-token")

# C3: user-tier secrets survive a save that echoed their redaction. github_token is
# redacted through [security.secret_keys], not by its table and key name.
with open(user, "w", encoding="utf-8") as fh:
    fh.write('[portal]\npassword = "user-tier-portal-pw"\n\n[ai]\napi_key = "user-tier-api-key"\n\n'
             '[git]\nuser_name = "user-tier-git-user"\ngithub_token = "user-tier-gh-token"\n')
st, _, served = call("GET", "/portal/config", headers=auth)
check(st == 200 and not any(s in served for s in ("user-tier-portal-pw", "user-tier-api-key", "user-tier-gh-token")),
      "GET redacts the planted user-tier secrets")

def marks(value, at=()):
    if value == "<redacted>":
        return {at}
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    return set().union(set(), *(marks(v, at + (k,)) for k, v in items))

# Parity: agent-pipe's GET redaction over the same tiers marks exactly the paths miosd marks.
pipe_env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
pipe_env.update(MIOS_VENDOR_TOML=os.path.join(repo, "usr/share/mios/mios.toml"),
                MIOS_VENDOR_TOML_D=os.path.join(repo, "usr/lib/mios/mios.d"),
                MIOS_HOST_TOML=os.path.join(d, "host.toml"), MIOS_HOST_TOML_D=os.path.join(d, "host.d"),
                MIOS_USER_TOML=user, MIOS_USER_TOML_D=os.path.join(d, "user.d"),
                MIOS_RESOLVER_NATIVE="0", MIOS_DB_AUTHORITATIVE="0")
pipe = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path[:0] = sys.argv[1:3]; import mios_toml; "
                       "from mios_pipe.kernel.config import redact_secrets, to_toml; "
                       "print(to_toml(redact_secrets(mios_toml.load_merged())))",
                       os.path.join(repo, "usr/lib/mios/agent-pipe"), os.path.join(repo, "usr/lib/mios")],
                      env=pipe_env, capture_output=True, text=True, timeout=60)
native_marks = marks(tomllib.loads(served))
pipe_marks = marks(tomllib.loads(pipe.stdout)) if pipe.returncode == 0 else {"<agent-pipe failed>"}
check(native_marks == pipe_marks and {("portal", "password"), ("git", "github_token")} <= native_marks,
      f"agent-pipe redacts the same {len(native_marks)} paths as miosd "
      f"(only miosd: {sorted(native_marks - pipe_marks)[:4]}, only agent-pipe: {sorted(pipe_marks - native_marks)[:4]}, "
      f"{pipe.stderr.strip()[-160:]!r})")
edited = edit(served, "identity", "username", '"test-operator"')
st, _, body = save(edited)
text = tier_text() or ""
tier = tomllib.loads(text)
check(st == 200 and tier.get("identity", {}).get("username") == "test-operator",
      f"an edited echo saved as {{\"toml\": ...}} persists the edit (HTTP {st}: {body[:160]!r})")
check(kept(tier), f"user-tier portal.password, ai.api_key and git.github_token keep their real values ({text[:240]!r})")
check("<redacted>" not in text and "host-tier-cookie-secret" not in text,
      "neither the marker nor a lower-tier secret reaches the user tier")
check("ports" not in tier, "unchanged [ports] stay in the lower tiers")

# C3, same table: an edit beside each user-tier secret sends the whole [portal]
# and [git] tables in the delta, so a dropped or lower-tier marker loses the secret.
st, _, served = call("GET", "/portal/config", headers=auth)
ttl = tomllib.loads(served)["portal"]["session_ttl"] + 1
edited = edit(edit(served, "portal", "session_ttl", str(ttl)), "git", "user_name", '"edited-git-user"')
st, _, body = save(edited)
text = tier_text() or ""
tier = tomllib.loads(text)
check(st == 200 and tier.get("portal", {}).get("session_ttl") == ttl
      and tier.get("git", {}).get("user_name") == "edited-git-user",
      f"an echo editing [portal].session_ttl and [git].user_name persists both (HTTP {st}: {body[:160]!r})")
check(kept(tier), f"the secret beside each edited key keeps its real value ({text[:240]!r})")
check("<redacted>" not in text and "host-tier-cookie-secret" not in text,
      "the same-table echo writes neither the marker nor a lower-tier secret")

# Same layer: user.d outranks the user tier and the GET serves its secrets, but the save
# writes the user tier, which keeps its own secret and never copies user.d's.
own = os.path.join(d, "user.d", "20-own.toml")
os.makedirs(os.path.dirname(own), exist_ok=True)
with open(own, "w", encoding="utf-8") as fh:
    fh.write('[portal]\npassword = "user-d-portal-pw"\nsecret = "user-d-cookie-secret"\n')
st, _, served = call("GET", "/portal/config", headers=auth)
ttl_own = tomllib.loads(served)["portal"]["session_ttl"] + 1
st, _, body = save(edit(served, "portal", "session_ttl", str(ttl_own)))
text = tier_text() or ""
tier = tomllib.loads(text)
check(st == 200 and tier.get("portal", {}).get("session_ttl") == ttl_own and kept(tier),
      f"with a user.d secret above it, an echo keeps the user tier's own secrets (HTTP {st}: {text[:240]!r})")
check(not any(s in text for s in ("user-d-portal-pw", "user-d-cookie-secret", "host-tier-cookie-secret", "<redacted>")),
      "neither user.d's secrets, a lower-tier secret nor the marker reaches the user tier")
os.remove(own)

# Fail-closed: a user.d override that does not parse leaves the hidden values
# unknown, so the echo is refused with every byte kept; once it parses, the echo saves.
frag, broken = os.path.join(d, "user.d", "10-broken.toml"), "[portal\npassword = \n"
os.makedirs(os.path.dirname(frag), exist_ok=True)
with open(frag, "w", encoding="utf-8") as fh:
    fh.write(broken)
st, _, body = save(edited)
check(st == 500 and "nothing was written" in body and tier_text() == text
      and open(frag, encoding="utf-8").read() == broken,
      f"an echo is refused while a user.d override does not parse (HTTP {st}: {body[:160]!r})")
os.remove(frag)
st, _, body = save(edited)
text = tier_text() or ""
check(st == 200 and kept(tomllib.loads(text)),
      f"the same echo saves once the override is gone (HTTP {st}: {body[:160]!r})")

# Negative controls: none of these may write.
st, _, _ = save('[broken_section\nmissing_bracket = "invalid"\n')
check(st == 422, f"malformed TOML inside the envelope is 422 (HTTP {st})")
st, _, _ = save(edited, {**post_headers, "Origin": "http://evil.example"})
check(st == 403, f"a cross-origin save is 403 (HTTP {st})")
st, _, _ = save(edited, {"Origin": origin, "Content-Type": "application/json"})
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

CANDIDATE="${MIOS_MIOSD_BIN:-}"
if [[ -z "$CANDIDATE" ]] && ! CANDIDATE="$(tree_build 2>"$TMP_DIR/tree-build.why")"; then
    CANDIDATE=""
fi

if [[ -x "$INSTALLED" && "$(readlink -f "$INSTALLED")" != "$(readlink -f "${CANDIDATE:-/nonexistent}")" ]]; then
    echo "[test-configurator-server] installed-compat tier: $INSTALLED (informational; never gates)"
    if run_tier installed-compat "$INSTALLED"; then
        echo "  [installed-compat] COMPATIBLE with the current contract"
    else
        echo "  [installed-compat] NOT COMPATIBLE with the current contract (an older image binary)"
    fi
fi

if [[ -z "$CANDIDATE" ]]; then
    echo "[test-configurator-server] candidate tier NOT RUN: no fresh tree build ($(cat "$TMP_DIR/tree-build.why")); build miosd from this checkout or set MIOS_MIOSD_BIN to a MiOS-DEV tree build of it" >&2
    exit 77
fi
if [[ ! -x "$CANDIDATE" ]]; then
    echo "[test-configurator-server] FAIL: MIOS_MIOSD_BIN=$CANDIDATE is not an executable" >&2
    exit 1
fi
case "$(readlink -f "$CANDIDATE")" in
    /usr/*|"$(readlink -f "$INSTALLED" 2>/dev/null || echo /nonexistent)")
        echo "[test-configurator-server] FAIL: $CANDIDATE is an installed binary, not a tree build of this checkout" >&2
        exit 1 ;;
esac
echo "[test-configurator-server] candidate tier: $CANDIDATE${MIOS_MIOSD_BIN:+ (MIOS_MIOSD_BIN)}"
run_tier candidate "$CANDIDATE" || status=1
if [[ "$status" -ne 0 ]]; then
    echo "[test-configurator-server] FAIL: the candidate does not meet the contract" >&2
    exit 1
fi
echo "[test-configurator-server] SUCCESS: the candidate meets the contract"
