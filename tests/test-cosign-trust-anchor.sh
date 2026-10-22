#!/usr/bin/env bash
# AI-hint: Hermetic two-sided test of the image-signature trust model: runs the real phase 49 as an image build in a private namespace, then pulls signed, unsigned and foreign-signed images via skopeo from a loopback registry.
# AI-related: automation/49-cosign-policy.sh, tools/native/mios-resolver/src/sigstore.rs, usr/share/mios/mios.toml, usr/lib/containers/registries.d, tests/fixtures/oci-mini-registry.py
set -euo pipefail
ROOT="${MIOS_TEST_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if [[ "${MIOS_TA_TEST_NS:-}" != 1 ]]; then
    exec env MIOS_TA_TEST_NS=1 unshare --user --map-root-user --net --mount bash "${BASH_SOURCE[0]}" "$@"
fi

pass() { echo "PASS $*"; }
fail() { echo "FAIL $*" >&2; exit 1; }

GEN="${MIOS_GEN_BIN:-}"
[[ -n "$GEN" ]] || for c in "$ROOT/tools/native/target/release/mios-gen" /usr/bin/mios-gen; do
    [[ -x "$c" ]] && { GEN="$c"; break; }
done
MIOSD="${MIOS_MIOSD_BIN:-/usr/bin/miosd}"
[[ -x "$GEN" && -x "$MIOSD" ]] || fail "mios-gen ($GEN) and miosd ($MIOSD) must be built"
command -v skopeo >/dev/null || fail "skopeo is required"

T="$(mktemp -d /var/tmp/mios-ta-test.XXXXXX)"
REG_PID=""
trap '[[ -n "$REG_PID" ]] && kill "$REG_PID" 2>/dev/null; rm -rf "$T"' EXIT
ip link set lo up
# Snapshot the tree's inputs before /usr/lib/containers is overmounted.
mkdir -p "$T/src/registries.d"
cp "$ROOT/usr/share/mios/mios.toml" "$T/src/mios.toml"
cp "$ROOT"/usr/lib/containers/registries.d/*.yaml "$T/src/registries.d/"
for d in /etc/containers /usr/lib/containers /usr/share/pki; do mount -t tmpfs tmpfs "$d"; done
mkdir -p /usr/share/pki/containers /etc/containers/registries.d "$T"/{bin,home,run,keys}
export HOME="$T/home" XDG_RUNTIME_DIR="$T/run" TMPDIR="$T"
unset CONTAINER_HOST CONTAINER_CONNECTION DOCKER_HOST CONTAINERS_POLICY_JSON

# The phase's cosign download is not under test; a present cosign skips it.
printf '#!/bin/sh\necho cosign-test-stub\n' > "$T/bin/cosign"; chmod +x "$T/bin/cosign"
export PATH="$T/bin:$PATH" MIOS_GEN_BIN="$GEN" MIOS_MIOSD_BIN="$MIOSD"

# Signing keys: A and B are the MiOS primary and backup, C is anyone else's.
printf 'test-passphrase\n' > "$T/keys/pass"
for k in a b c; do
    skopeo generate-sigstore-key --output-prefix "$T/keys/$k" --passphrase-file "$T/keys/pass" >/dev/null
done

# A source tree: the live SSOT and registries.d, plus whichever anchors a case ships.
tree() {  # tree DIR [ANCHORS...]
    local d="$1"; shift
    mkdir -p "$d/usr/share/mios" "$d/usr/lib/containers/registries.d" "$d/usr/share/pki/containers"
    cp "$T/src/mios.toml" "$d/usr/share/mios/mios.toml"
    cp "$T"/src/registries.d/*.yaml "$d/usr/lib/containers/registries.d/"
    for a in "$@"; do cp "$T/keys/${a%%:*}.pub" "$d/usr/share/pki/containers/${a#*:}"; done
}
phase() {  # phase TREE -> runs the real phase 49 against it; output in $T/phase.log
    MIOS_ROOT="$1" CTX="$1" bash "$ROOT/automation/49-cosign-policy.sh" >"$T/phase.log" 2>&1
}

# --- negative: no MiOS anchor in the tree -> the phase dies, nothing activated -----
tree "$T/noanchor"
set +e; container=mios-test phase "$T/noanchor"; rc=$?; set -e
[[ $rc -ne 0 ]] && grep -q "MiOS trust anchor /usr/share/pki/containers/mios-cosign" "$T/phase.log" \
    && [[ ! -e /etc/containers/policy.json ]] \
    || fail "missing anchor was not refused (rc=$rc): $(tail -3 "$T/phase.log")"
pass "phase 49: a policy naming an anchor the tree does not ship fails, nothing activated"

# --- negative: an invalid requirement type -> the phase dies -----------------------
tree "$T/badtype" a:mios-cosign.pub b:mios-cosign-backup.pub
sed -i 's/^policy_mode = "insecureAcceptAnything"/policy_mode = "insecureAcceptEverything"/' "$T/badtype/usr/share/mios/mios.toml"
grep -q '^policy_mode = "insecureAcceptEverything"' "$T/badtype/usr/share/mios/mios.toml" || fail "could not plant the invalid type"
set +e; container=mios-test phase "$T/badtype"; rc=$?; set -e
[[ $rc -ne 0 && ! -e /etc/containers/policy.json ]] && grep -q "insecureAcceptEverything" "$T/phase.log" \
    || fail "invalid policy type was not refused (rc=$rc): $(tail -3 "$T/phase.log")"
pass "phase 49: policy_mode insecureAcceptEverything fails, nothing activated"

# --- host mode: no container -> /etc/containers is left alone ----------------------
tree "$T/host" a:mios-cosign.pub b:mios-cosign-backup.pub
printf '{"default":[{"type":"insecureAcceptAnything"}]}\n' > /etc/containers/policy.json
before="$(sha256sum /etc/containers/policy.json)"
[[ ! -e /run/.containerenv ]] || fail "this test needs a non-container host for the host case"
env -u container bash -c "$(declare -f phase); T='$T' ROOT='$ROOT' phase '$T/host'" || fail "host-mode phase failed: $(tail -3 "$T/phase.log")"
[[ "$(sha256sum /etc/containers/policy.json)" == "$before" && ! -e /etc/containers/registries.d/ghcr.io-mios-dev.yaml ]] \
    || fail "host-mode phase wrote /etc/containers"
grep -q "Not an image build" "$T/phase.log" || fail "host-mode phase did not say why it left /etc alone"
rm -f /etc/containers/policy.json /usr/share/pki/containers/*
pass "phase 49: on a running host it validates and installs anchors but leaves /etc/containers alone"

# --- positive: image build with both anchors -> activated ---------------------------
tree "$T/image" a:mios-cosign.pub b:mios-cosign-backup.pub
container=mios-test phase "$T/image" || fail "image-build phase failed: $(tail -5 "$T/phase.log")"
cmp -s "$T/image/usr/lib/containers/policy.json" /etc/containers/policy.json || fail "activated policy differs from the projection"
cmp -s "$T/keys/a.pub" /usr/share/pki/containers/mios-cosign.pub || fail "primary anchor not installed from the tree"
cmp -s "$T/keys/b.pub" /usr/share/pki/containers/mios-cosign-backup.pub || fail "backup anchor not installed from the tree"
[[ -f /etc/containers/registries.d/ghcr.io-mios-dev.yaml ]] || fail "sigstore attachment config not activated"
pass "phase 49: image build activates /etc/containers/policy.json, registries.d and both anchors from the tree"

# --- the consumer: pull through the activated policy from a loopback registry ------
PORT=5055
python3 -I "$ROOT/tests/fixtures/oci-mini-registry.py" "$PORT" "$T/ready" & REG_PID=$!
for _ in $(seq 50); do [[ -f "$T/ready" ]] && break; sleep 0.1; done
[[ -f "$T/ready" ]] || fail "test registry did not start"
# /etc/containers is this namespace's tmpfs: the phase never writes registries.conf,
# so the loopback remap stands in for the base image's file (skopeo has no flag for it).
cat > /etc/containers/registries.conf <<EOF
[[registry]]
prefix = "ghcr.io/mios-dev"
location = "127.0.0.1:${PORT}/mios-dev"
insecure = true
[[registry]]
prefix = "ghcr.io/other"
location = "127.0.0.1:${PORT}/other"
insecure = true
EOF
# Push side only: the loopback name also carries attachments.
printf 'docker:\n  127.0.0.1:%s:\n    use-sigstore-attachments: true\n' "$PORT" > /etc/containers/registries.d/zz-test-loopback.yaml

# One-layer OCI images, one per case: sigstore attachments are keyed by manifest
# digest, so two cases sharing an image would share each other's signatures.
mkimg() {  # mkimg NAME -> oci:$T/img-NAME:fixture
    python3 -I - "$T/img-$1" "$1" <<'PY'
import hashlib, io, json, os, sys, tarfile, gzip
d, name = sys.argv[1], sys.argv[2]
os.makedirs(f"{d}/blobs/sha256", exist_ok=True)
def blob(b):
    h = hashlib.sha256(b).hexdigest()
    open(f"{d}/blobs/sha256/{h}", "wb").write(b)
    return {"digest": "sha256:" + h, "size": len(b)}
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w") as t:
    data = f"mios trust-anchor fixture {name}\n".encode()
    ti = tarfile.TarInfo("fixture.txt"); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
layer = gzip.compress(raw.getvalue(), mtime=0)
diff = "sha256:" + hashlib.sha256(raw.getvalue()).hexdigest()
cfg = json.dumps({"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": [diff]}}).encode()
man = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                  "config": {"mediaType": "application/vnd.oci.image.config.v1+json", **blob(cfg)},
                  "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", **blob(layer)}]}).encode()
m = blob(man)
json.dump({"schemaVersion": 2, "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json", **m,
           "annotations": {"org.opencontainers.image.ref.name": "fixture"}}]}, open(f"{d}/index.json", "w"))
open(f"{d}/oci-layout", "w").write('{"imageLayoutVersion": "1.0.0"}')
PY
}

push() {  # push REPO:TAG [KEY]
    local sign=() img="${1//[:\/]/-}"
    mkimg "$img"
    [[ -n "${2:-}" ]] && sign=(--sign-by-sigstore-private-key "$T/keys/$2.private" --sign-passphrase-file "$T/keys/pass" --sign-identity "ghcr.io/$1")
    skopeo --insecure-policy copy -q "${sign[@]}" --dest-tls-verify=false "oci:$T/img-$img:fixture" "docker://127.0.0.1:${PORT}/$1" \
        || fail "could not push $1${2:+ signed by $2}"
}
pull() {  # pull REPO:TAG -> rc, output in $T/pull.log; uses /etc/containers/policy.json
    rm -rf "$T/out"
    skopeo copy -q "docker://ghcr.io/$1" "dir:$T/out" >"$T/pull.log" 2>&1
}
push mios-dev/mios:signed-a a
push mios-dev/mios:signed-b b
push mios-dev/mios:signed-c c
push mios-dev/mios-micro:unsigned
push other/thing:unsigned

pull mios-dev/mios:signed-a || fail "MiOS image signed by the primary key was refused: $(cat "$T/pull.log")"
pass "consumer: a MiOS image signed by the primary anchor pulls"
pull mios-dev/mios:signed-b || fail "MiOS image signed by the backup key was refused: $(cat "$T/pull.log")"
pass "consumer: a MiOS image signed by the backup anchor pulls"
if pull mios-dev/mios-micro:unsigned; then fail "an unsigned MiOS image pulled"; fi
grep -qi "signature" "$T/pull.log" || fail "unsigned refusal did not name the signature: $(cat "$T/pull.log")"
pass "consumer: an unsigned image in the MiOS namespace is refused ($(grep -oi 'a signature was required[^"]*' "$T/pull.log" | head -1))"
if pull mios-dev/mios:signed-c; then fail "a MiOS image signed by a foreign key pulled"; fi
grep -qi "signature" "$T/pull.log" || fail "foreign-key refusal did not name the signature: $(cat "$T/pull.log")"
pass "consumer: a MiOS image signed by a key MiOS does not ship is refused"
pull other/thing:unsigned || fail "an unsigned third-party image was refused: $(cat "$T/pull.log")"
pass "consumer: an unsigned image outside the MiOS namespace still pulls (policy_mode default)"

# Control on the control: sigstore attachments are only read where registries.d
# enables them (containers/image keys that on the registry it actually pulls
# from; here the loopback stands in for ghcr.io). Without that config even the
# primary-signed image is refused, so the registries.d activation is load-bearing.
mkdir -p "$T/regd-off"
mv /etc/containers/registries.d/*.yaml "$T/regd-off/"
if pull mios-dev/mios:signed-a; then fail "a signed MiOS image pulled with sigstore attachments disabled"; fi
grep -qi "signature" "$T/pull.log" || fail "attachments-off refusal did not name the signature: $(cat "$T/pull.log")"
mv "$T"/regd-off/*.yaml /etc/containers/registries.d/
pull mios-dev/mios:signed-a || fail "restored attachment config did not verify again"
pass "consumer: with registries.d attachments disabled even a signed MiOS image is refused (restored: pulls again)"
echo "PASS test-cosign-trust-anchor: the shipped policy trusts only MiOS anchors from the tree, for the MiOS namespace only"
