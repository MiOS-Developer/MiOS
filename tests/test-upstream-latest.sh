#!/usr/bin/env bash
# AI-hint: Hermetic two-sided tests for build-time latest resolution (automation/lib/upstream-latest.sh) and its consumers 25-gpu-cdi-toolkits, 49-cosign-policy, 56-fonts, 59-tools (code-server) and 64-bake-fan-control: mocked forge (fake curl + file:// git remotes) inside a user+net+mount namespace, so no network is reachable; proves the newest release is resolved and recorded as build output, and that a failed integrity check aborts without installing anything.
# AI-related: automation/lib/upstream-latest.sh, automation/25-gpu-cdi-toolkits.sh, automation/49-cosign-policy.sh, automation/56-fonts.sh, automation/59-tools.sh, automation/64-bake-fan-control.sh, tools/native/mios-bake-plan/src/latest.rs
set -euo pipefail
ROOT="${MIOS_TEST_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SSOT="${MIOS_TEST_SSOT:-$ROOT/usr/share/mios/mios.toml}"

# Private namespaces: no network at all, and install targets can be overmounted.
if [[ "${MIOS_UL_TEST_NS:-}" != 1 ]]; then
    exec env MIOS_UL_TEST_NS=1 unshare --user --map-root-user --net --mount bash "${BASH_SOURCE[0]}" "$@"
fi

PLAN=""
for c in "${MIOS_BAKE_PLAN:-}" "$ROOT/tools/native/target/release/mios-bake-plan" \
         "$ROOT/tools/native/target/debug/mios-bake-plan" /usr/bin/mios-bake-plan; do
    [[ -n "$c" && -x "$c" ]] && { PLAN="$c"; break; }
done
[[ -n "$PLAN" ]] || { echo "FAIL mios-bake-plan not built (needed for latest-git)" >&2; exit 1; }

T="$(mktemp -d /var/tmp/mios-ul-test.XXXXXX)"
trap 'rm -rf "$T"' EXIT
mount -t tmpfs tmpfs /tmp
mkdir -p "$T"/{bin,http,git,root/usr/share/mios,sbom,work,libexec,fonts,vendored}
mount --bind "$T/libexec" /usr/libexec/mios
mount --bind "$T/fonts" /usr/share/fonts
mount --bind "$T/vendored" /usr/share/mios/vendored

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
export GIT_CONFIG_NOSYSTEM=1 HOME="$T/home"; mkdir -p "$HOME"
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="url.file://$T/git/.insteadOf" GIT_CONFIG_VALUE_0="https://github.com/"
export MIOS_ROOT="$T/root" MIOS_BAKE_PLAN="$PLAN" MIOS_SBOM_DIR="$T/sbom" MIOS_VERSION_MANIFEST="$T/versions.tsv"
export MIOS_UL_RETRY_DELAY=0

# Temp SSOT: the live [build.float] shapes plus this lane's staged fragment.
python3 - "$SSOT" "$T/root/usr/share/mios/mios.toml" <<'PY'
import sys, tomllib
live = tomllib.load(open(sys.argv[1], "rb"))
f = live["build"]["float"]
q = lambda xs: "[" + ", ".join("'" + x + "'" for x in xs) + "]"
open(sys.argv[2], "w").write(
    "[build.float]\nimage_shapes = " + q(f["image_shapes"]) + "\ngit_shapes = " + q(f["git_shapes"]) + "\n"
    "[build.bake_refs]\nventulusd = \"latest\"\n"
    "[security.sigstore]\ncosign_major = 2\ncosign_release_url = \"https://github.com/sigstore/cosign/releases/download\"\n"
    "[image.sidecars]\ncode_server = \"ghcr.io/coder/code-server:latest\"\n")
PY

# Fake forge over HTTPS: URL -> $T/http/<host>/<path>, '?' kept as '__Q__'.
cat > "$T/bin/curl" <<EOF
#!/usr/bin/env bash
out=""; url=""; prev=""
for a in "\$@"; do
    case "\$prev" in -o|--output) out="\$a" ;; esac
    case "\$a" in https://*|http://*) url="\$a" ;; --output=*) out="\${a#--output=}" ;; esac
    prev="\$a"
done
echo "\$url" >> "$T/curl.log"
f="$T/http/\${url#*://}"; f="\${f//\\?/__Q__}"
[[ -f "\$f" ]] || exit 22
if [[ -n "\$out" ]]; then cp "\$f" "\$out"; else cat "\$f"; fi
EOF
printf '#!/bin/sh\nexit 1\n' > "$T/bin/go"
printf '#!/bin/sh\necho "dnf5 $*" >> "%s/dnf.log"\n' "$T" > "$T/bin/dnf5"
cp "$T/bin/dnf5" "$T/bin/dnf"
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"

pass() { echo "PASS $*"; }
fail() { echo "FAIL $*" >&2; exit 1; }
sha() { sha256sum "$1" | awk '{print $1}'; }

# repo OWNER/NAME TAG... : a file:// upstream with lightweight tags, one commit per tag.
repo() {
    local name="$1"; shift
    local w="$T/work/${name//\//_}"
    git init -q "$w"; git -C "$w" commit -q --allow-empty -m init
    git init -q --bare "$T/git/${name}.git"
    for t in "$@"; do git -C "$w" commit -q --allow-empty -m "$t"; git -C "$w" tag -a -m "$t" "$t"; done
    git -C "$w" push -q "$T/git/${name}.git" HEAD:refs/heads/main --tags
}
# asset OWNER/NAME TAG FILE CONTENT_SRC [DIGEST_OVERRIDE]: release asset + release JSON with digest.
asset() {
    local name="$1" tag="$2" file="$3" src="$4" digest="${5:-}"
    local d="$T/http/github.com/${name}/releases/download/${tag}"
    mkdir -p "$d" "$T/http/api.github.com/repos/${name}/releases/tags"
    cp "$src" "$d/$file"
    [[ -n "$digest" ]] || digest="sha256:$(sha "$src")"
    local j="$T/http/api.github.com/repos/${name}/releases/tags/${tag}"
    if [[ -f "$j" ]]; then
        jq --arg n "$file" --arg d "$digest" '.assets += [{name:$n, digest:$d}]' "$j" > "$j.n" && mv "$j.n" "$j"
    else
        jq -n --arg t "$tag" --arg n "$file" --arg d "$digest" '{tag_name:$t, assets:[{name:$n, digest:$d}]}' > "$j"
    fi
}
latest_release() {  # OWNER/NAME TAG: the forge's releases/latest answer
    mkdir -p "$T/http/api.github.com/repos/$1/releases"
    jq -n --arg t "$2" '{tag_name:$t}' > "$T/http/api.github.com/repos/$1/releases/latest"
}

# shellcheck source=automation/lib/upstream-latest.sh
source "$ROOT/automation/lib/upstream-latest.sh"

# --- network really is unreachable ------------------------------------------
if command -v getent >/dev/null && getent hosts github.com >/dev/null 2>&1 && \
   timeout 3 bash -c 'exec 3<>/dev/tcp/140.82.112.3/443' 2>/dev/null; then
    fail "test namespace can reach the network"
fi
pass "test namespace has no network"

# --- lib: highest tag of a shape, pre-releases never win ----------------------
repo acme/tool v1.2.0 v1.10.0 v2.0.0-rc1
[[ "$(mios_latest_git_tag_matching https://github.com/acme/tool.git '^v[0-9]+(\.[0-9]+)*$')" == v1.10.0 ]] \
    || fail "tag shape resolution"
[[ "$(mios_latest_git_ref https://github.com/acme/tool.git 2>/dev/null)" == v1.10.0 ]] || fail "latest-git fallback"
pass "lib resolves the newest release tag (v1.10.0, rc ignored)"

# --- 49-cosign-policy: newest v2 release, proven by cosign_checksums.txt ------
grep -q 'mios_cosign_fetch_latest' "$ROOT/automation/49-cosign-policy.sh" || fail "49 does not use the resolver"
! grep -qE 'COSIGN_VERSION:\?|v2\.6\.4' "$ROOT/automation/49-cosign-policy.sh" || fail "49 still reads a pinned version"
repo sigstore/cosign v2.4.0 v2.6.9 v2.7.0-rc1 v3.0.1
printf 'cosign-binary-v2.6.9\n' > "$T/cos"
asset sigstore/cosign v2.6.9 cosign-linux-amd64 "$T/cos"
printf '%s  cosign-linux-amd64\n%s  cosign-linux-arm64\n' "$(sha "$T/cos")" "$(printf 0%.0s {1..64})" > "$T/sums"
asset sigstore/cosign v2.6.9 cosign_checksums.txt "$T/sums"
out="$(mios_cosign_fetch_latest https://github.com/sigstore/cosign/releases/download 2 "$T/out/cosign" amd64)"
read -r tag got <<< "$out"
[[ "$tag" == v2.6.9 && "$got" == "$(sha "$T/cos")" && -x "$T/out/cosign" ]] || fail "cosign latest-in-major: $out"
mios_record_resolved cosign "$tag" "https://github.com/sigstore/cosign/releases/tag/$tag" "$got"
grep -qP "^cosign\tv2\.6\.9\t$got$" "$T/sbom/binaries.tsv" || fail "cosign not recorded in SBOM"
grep -qP "^cosign\tv2\.6\.9\t" "$T/versions.tsv" || fail "cosign not recorded in version manifest"
pass "cosign: newest v2 (v2.6.9, not v3/rc) resolved, verified, recorded"
printf 'tampered\n' > "$T/http/github.com/sigstore/cosign/releases/download/v2.6.9/cosign-linux-amd64"
set +e; ( mios_cosign_fetch_latest https://github.com/sigstore/cosign/releases/download 2 "$T/out/cosign-bad" amd64 ) 2>"$T/err"; rc=$?; set -e
[[ $rc -eq 3 && ! -e "$T/out/cosign-bad" ]] && grep -q INTEGRITY "$T/err" || fail "cosign tamper not aborted (rc=$rc)"
pass "cosign: checksum mismatch aborts (exit 3) and installs nothing"

# --- 64-bake-fan-control: latest release, clone proven against the advertised commit
grep -q 'mios_git_bake_checkout' "$ROOT/automation/64-bake-fan-control.sh" || fail "64 does not use the resolver"
! grep -q 'echo v5\.1\.0' "$ROOT/automation/64-bake-fan-control.sh" || fail "64 keeps the v5.1.0 fallback"
repo Plan-B-Development/ventulusd v5.1.0 v5.2.0
latest_release Plan-B-Development/ventulusd v5.2.0
want="$(git --git-dir="$T/git/Plan-B-Development/ventulusd.git" rev-parse 'v5.2.0^{commit}')"
out="$(mios_git_bake_checkout https://github.com/Plan-B-Development/ventulusd.git latest "$T/out/vsrc")"
[[ "$out" == "v5.2.0 $want" && "$(git -C "$T/out/vsrc" rev-parse HEAD)" == "$want" ]] || fail "ventulusd latest: $out"
grep -q 'api.github.com/repos/Plan-B-Development/ventulusd/releases/latest' "$T/curl.log" || fail "forge latest not consulted"
pass "ventulusd: latest -> v5.2.0 at the advertised commit"
# A forged advertisement (ls-remote says another commit) must abort the bake.
cat > "$T/bin/git" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == ls-remote ]]; then /usr/bin/git "\$@" | sed -E 's/^[0-9a-f]{40}/0123456789abcdef0123456789abcdef01234567/'; exit "\${PIPESTATUS[0]}"; fi
exec /usr/bin/git "\$@"
EOF
chmod +x "$T/bin/git"; hash -r
set +e; ( mios_git_bake_checkout https://github.com/Plan-B-Development/ventulusd.git v5.2.0 "$T/out/vbad" ) >/dev/null 2>"$T/err"; rc=$?; set -e
rm -f "$T/bin/git"; hash -r
[[ $rc -eq 3 ]] && grep -q INTEGRITY "$T/err" || fail "ventulusd commit mismatch not aborted (rc=$rc)"
pass "ventulusd: commit != advertised id aborts (exit 3)"

# --- 25-gpu-cdi-toolkits: no fallback tags; assets proven by GitHub digests ---
! grep -qE 'FALLBACK_TAG|v1\.3\.0|v0\.7\.0' "$ROOT/automation/25-gpu-cdi-toolkits.sh" || fail "25 keeps fallback tags"
# shellcheck source=/dev/null  # the two consumer functions, excerpted at run time
source <(sed -n '/^AMD_REPO=/p;/^INTEL_REPO=/p;/^_install_amd_ctk() {/,/^}/p;/^_install_intel_sg() {/,/^}/p' "$ROOT/automation/25-gpu-cdi-toolkits.sh")
warn() { echo "WARN $*" >&2; }; mios_ok() { echo "OK $*" >&2; }
repo ROCm/container-toolkit v1.3.0 v1.4.0
latest_release ROCm/container-toolkit v1.4.0
printf 'rpm-payload\n' > "$T/rpm"
asset ROCm/container-toolkit v1.4.0 amd-container-toolkit-1.4.0-1.el9.x86_64.rpm "$T/rpm"
_install_amd_ctk 2>"$T/err"
grep -q 'amd-container-toolkit-1.4.0-1.el9.x86_64.rpm' "$T/dnf.log" || fail "AMD RPM not installed: $(cat "$T/err")"
grep -qP "^amd-container-toolkit\tv1\.4\.0\t$(sha "$T/rpm")$" "$T/sbom/binaries.tsv" || fail "AMD not recorded"
pass "amd-container-toolkit: latest v1.4.0 resolved, digest-verified, recorded"
repo intel/intel-resource-drivers-for-kubernetes specs-generator-v0.7.0 gpu-v0.9.0 specs-generator-v0.8.1
printf 'intel-bin\n' > "$T/ibin"
asset intel/intel-resource-drivers-for-kubernetes specs-generator-v0.8.1 intel-cdi-specs-generator-linux-amd64 "$T/ibin"
_install_intel_sg 2>"$T/err"
[[ -x /usr/libexec/mios/intel-cdi-specs-generator ]] || fail "Intel generator not installed: $(cat "$T/err")"
grep -qP "^intel-cdi-specs-generator\tspecs-generator-v0\.8\.1\t$(sha "$T/ibin")$" "$T/sbom/binaries.tsv" || fail "Intel not recorded"
pass "intel-cdi-specs-generator: latest specs-generator-v0.8.1 (not gpu-*) resolved, verified, recorded"
rm -f /usr/libexec/mios/intel-cdi-specs-generator
asset intel/intel-resource-drivers-for-kubernetes specs-generator-v0.8.1 intel-cdi-specs-generator-linux-amd64 "$T/ibin" "sha256:$(printf 1%.0s {1..64})"
jq '.assets |= [.[-1]]' "$T/http/api.github.com/repos/intel/intel-resource-drivers-for-kubernetes/releases/tags/specs-generator-v0.8.1" > "$T/j" \
    && mv "$T/j" "$T/http/api.github.com/repos/intel/intel-resource-drivers-for-kubernetes/releases/tags/specs-generator-v0.8.1"
set +e; ( _install_intel_sg ) >/dev/null 2>"$T/err"; rc=$?; set -e
[[ $rc -eq 3 && ! -e /usr/libexec/mios/intel-cdi-specs-generator ]] && grep -q INTEGRITY "$T/err" \
    || fail "Intel digest mismatch not aborted (rc=$rc)"
pass "intel-cdi-specs-generator: digest mismatch aborts (exit 3), nothing installed"
jq '.assets[0] |= del(.digest)' "$T/http/api.github.com/repos/intel/intel-resource-drivers-for-kubernetes/releases/tags/specs-generator-v0.8.1" > "$T/j" \
    && mv "$T/j" "$T/http/api.github.com/repos/intel/intel-resource-drivers-for-kubernetes/releases/tags/specs-generator-v0.8.1"
: > "$T/curl.log"
_install_intel_sg 2>"$T/err"
[[ ! -e /usr/libexec/mios/intel-cdi-specs-generator ]] || fail "Intel installed without a published digest"
! grep -q 'releases/download' "$T/curl.log" || fail "Intel asset downloaded without a digest"
pass "intel-cdi-specs-generator: no published digest -> not downloaded, not installed"

# --- 56-fonts: Symbols-Only Nerd Font, no fallback tag ------------------------
! grep -q 'NERD_FALLBACK_TAG' "$ROOT/automation/56-fonts.sh" || fail "56 keeps NERD_FALLBACK_TAG"
# shellcheck source=/dev/null
source <(sed -n '/^_install_nerd_symbols() {/,/^}/p' "$ROOT/automation/56-fonts.sh")
mios_log() { echo "LOG $*" >&2; }
repo ryanoasis/nerd-fonts v3.4.0 v3.5.0
latest_release ryanoasis/nerd-fonts v3.5.0
mkdir -p "$T/z"; printf 'ttf' > "$T/z/SymbolsNerdFont-Regular.ttf"
(cd "$T/z" && python3 -c 'import zipfile; z=zipfile.ZipFile("../nerd.zip","w"); z.write("SymbolsNerdFont-Regular.ttf")')
asset ryanoasis/nerd-fonts v3.5.0 NerdFontsSymbolsOnly.zip "$T/nerd.zip"
_install_nerd_symbols 2>"$T/err"
[[ -f /usr/share/fonts/nerd-symbols/SymbolsNerdFont-Regular.ttf ]] || fail "nerd font not installed: $(cat "$T/err")"
grep -qP "^NerdFontsSymbolsOnly\tv3\.5\.0\t$(sha "$T/nerd.zip")$" "$T/sbom/binaries.tsv" || fail "nerd not recorded"
pass "nerd-fonts: latest v3.5.0 resolved, digest-verified, recorded"
rm -rf /usr/share/fonts/nerd-symbols
printf 'evil' > "$T/http/github.com/ryanoasis/nerd-fonts/releases/download/v3.5.0/NerdFontsSymbolsOnly.zip"
set +e; ( _install_nerd_symbols ) >/dev/null 2>"$T/err"; rc=$?; set -e
[[ $rc -eq 3 && ! -e /usr/share/fonts/nerd-symbols/SymbolsNerdFont-Regular.ttf ]] || fail "nerd tamper not aborted (rc=$rc)"
pass "nerd-fonts: tampered zip aborts (exit 3), no font installed"
# F36: an asset the forge publishes no digest for is a hard failure (die, exit 1),
# never a warning; nothing is downloaded or installed.
eval "$(grep -E '^(log_ts|die)\(\)' "$ROOT/automation/lib/common.sh")"
_nj="$T/http/api.github.com/repos/ryanoasis/nerd-fonts/releases/tags/v3.5.0"
jq '.assets |= map(del(.digest))' "$_nj" > "$_nj.n" && mv "$_nj.n" "$_nj"
cp "$T/nerd.zip" "$T/http/github.com/ryanoasis/nerd-fonts/releases/download/v3.5.0/NerdFontsSymbolsOnly.zip"
set +e; ( _install_nerd_symbols ) >/dev/null 2>"$T/err"; rc=$?; set -e
[[ $rc -eq 1 && ! -e /usr/share/fonts/nerd-symbols/SymbolsNerdFont-Regular.ttf ]] \
    && grep -q 'Nerd Font not installed (no verified artefact)' "$T/err" \
    || fail "nerd no-digest did not hard-fail (rc=$rc): $(cat "$T/err")"
pass "nerd-fonts: no published digest dies (exit 1), nothing installed"

# --- 59-tools: code-server :latest -> newest release RPM, digest-verified -----
# shellcheck source=/dev/null
source <(sed -n '/^_cs_repo=/p;/^_cs_fetch_rpm() {/,/^}/p' "$ROOT/automation/59-tools.sh")
repo coder/code-server v4.139.1 v4.142.0
latest_release coder/code-server v4.142.0
arch=amd64; [[ "$(uname -m)" == aarch64 ]] && arch=arm64
printf 'cs-rpm\n' > "$T/csrpm"
asset coder/code-server v4.142.0 "code-server-4.142.0-${arch}.rpm" "$T/csrpm"
mkdir -p "$T/csdl"
_cs_fetch_rpm "ghcr.io/coder/code-server:latest" "$T/csdl" 2>"$T/err"
# shellcheck disable=SC2154  # _cs_version/_cs_sha are set by the excerpted _cs_fetch_rpm
[[ "$_cs_version" == 4.142.0 && "$_cs_sha" == "$(sha "$T/csrpm")" && -f "$T/csdl/code-server-4.142.0-${arch}.rpm" ]] \
    || fail "code-server latest: ${_cs_version:-} $(cat "$T/err")"
pass "code-server: :latest -> 4.142.0, RPM digest-verified"
printf 'evil\n' > "$T/http/github.com/coder/code-server/releases/download/v4.142.0/code-server-4.142.0-${arch}.rpm"
mkdir -p "$T/csbad"; set +e; ( _cs_fetch_rpm "ghcr.io/coder/code-server:latest" "$T/csbad" ) >/dev/null 2>"$T/err"; rc=$?; set -e
[[ $rc -eq 3 && -z "$(ls -A "$T/csbad")" ]] && grep -q INTEGRITY "$T/err" || fail "code-server tamper not aborted (rc=$rc)"
pass "code-server: tampered RPM aborts (exit 3)"

echo "PASS test-upstream-latest: all consumers resolve latest, record it, and abort on integrity failure"
