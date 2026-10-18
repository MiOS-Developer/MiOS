#!/usr/bin/env bash
# AI-hint: Pins gate for the MiOS-Xbox Windows pipeline (xb-04, XB-2): rejects version/build/commit/URL pins in the merged [windows.xbox] SSOT and in the pipeline code (stable-virtio, archive-virtio/virtio-win-0.1.N, fixed UUP build numbers, Looking Glass release/commit pins, versioned release-download URLs, literal *version = "x.y.z"). Two-sided: a clean fixture passes, every planted pin fails with its named finding, then the live tree (vendor mios.toml + staged xbox-*.toml fragments through mios_toml.load_merged, plus the pipeline files) is scanned.
# AI-related: usr/lib/mios/mios_toml.py, usr/libexec/mios/win/xbox/, usr/share/mios/windows/build/, usr/share/mios/windows/xbox/, automation/21-virt.sh, tests/test-everything-no-pin.sh
# Usage: tests/test-mios-xbox-no-pin.sh            controls + live scan (exit 1 on any pin)
#        tests/test-mios-xbox-no-pin.sh --controls only the positive/negative controls
#        tests/test-mios-xbox-no-pin.sh --scan [--layer TOML]... [FILE]...   scan the given inputs only
# A line that carries the marker  pin-gate:allow  is skipped (deny-list patterns in the gates themselves).
set -euo pipefail
T=""
ROOT="${MIOS_TEST_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ROOT="${ROOT%/}"
STAGED="${MIOS_XBOX_STAGED_DIR:-/home/mios/mios-desktop-work-1/staged-ssot}"

scan() {  # scan [--layer TOML]... [FILE]...  -> prints FINDING lines, exit 1 when any
    MIOS_PINGATE_LIB="$ROOT/usr/lib/mios" python3 -I - "$@" <<'PY'
import os, re, sys
sys.path.insert(0, os.environ["MIOS_PINGATE_LIB"])
import mios_toml

CHANNELS = {"", "latest", "stable", "evergreen", "dev", "beta", "releasepreview", "retail", "canary", "current", "detect"}
BUILD_KEY = re.compile(r"(?i)(?:^|_)(?:uup_?)?build$")
PIN_KEY = re.compile(r"(?:^|_)(version|build|commit|rev|revision|tag|sha256|sha|checksum|digest)$", re.I)
CODE_RULES = [
    ("pin.stable-virtio", re.compile(r"stable-virtio", re.I)),
    ("pin.virtio-version", re.compile(r"archive-virtio/|virtio-win-0\.1\.\d+|qemu-ga-win-\d+\.\d+", re.I)),
    ("pin.uup-build", re.compile(r"(?<![\d.])2\d{4}\.\d{1,5}(?![\d.])")),
    ("pin.uup-build", re.compile(r"(?i)(?:\b(?:uup_?build|UupBuild|BuildNumber)\b|-Build\b)\s*[:=]?\s*['\"]?\d{5}\b")),
    ("pin.looking-glass", re.compile(r"(?i)looking-glass\.io/artifact/(?!stable\b|bleeding\b)[\w.\-]+|looking-glass-(host|idd)-?(setup)?-?B\d+")),
    ("pin.commit", re.compile(r"(?i)\b(commit|checkout|rev|revision|ref)\b\W{0,6}[0-9a-f]{40}\b")),
    ("pin.release-url", re.compile(r"/releases/download/v?\d[\w.\-]*/")),
    ("pin.version-literal", re.compile(r"(?i)\b[\w\-]*version\b['\"]?\s*[:=]\s*['\"]v?\d+(\.\d+){1,3}['\"]")),
]

args = sys.argv[1:]
layers, files = [], []
while args:
    a = args.pop(0)
    if a == "--layer":
        layers.append(args.pop(0))
    else:
        files.append(a)
findings = []
if layers:
    merged = mios_toml.load_merged(layers=layers)
    xb = mios_toml.section(merged, "windows.xbox")
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            key = re.sub(r"\[\d+\]$", "", path.rsplit(".", 1)[-1])
            sval = str(node)
            if PIN_KEY.search(key) and not isinstance(node, bool) and sval.strip().lower() not in CHANNELS:
                findings.append(("pin.ssot", f"windows.xbox{path} = {sval!r} is a pinned value (use a channel; resolved versions go to build-manifest.json)"))
            if isinstance(node, str):
                for fid, rx in CODE_RULES:
                    if fid == "pin.version-literal":
                        continue
                    # a build number is a pin only where it selects the build (build/uup_build keys);
                    # descriptive "available from build N+" annotations (e.g. *.builds) are not
                    if fid == "pin.uup-build" and not BUILD_KEY.search(key):
                        continue
                    if rx.search(node):
                        findings.append((fid.replace("pin.", "pin.ssot-"), f"windows.xbox{path} = {sval!r}"))
    walk(xb, "")
for f in files:
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        findings.append(("pin.unreadable", f"{f}: {e.strerror}"))
        continue
    for n, line in enumerate(lines, 1):
        if "pin-gate:allow" in line:
            continue
        for fid, rx in CODE_RULES:
            if rx.search(line):
                findings.append((fid, f"{f}:{n}: {line.strip()[:160]}"))
for fid, d in findings:
    print(f"FINDING {fid}: {d}")
sys.exit(1 if findings else 0)
PY
}

expect_clean() { local out; if ! out="$(scan "$@" 2>&1)"; then echo "FAIL positive control reported pins:"; echo "$out"; exit 1; fi; }
expect_finding() {  # FINDING-ID ARGS...
    local id="$1"; shift; local out rc=0
    out="$(scan "$@" 2>&1)" || rc=$?
    if [ "$rc" -ne 1 ] || ! grep -q "^FINDING $id:" <<<"$out"; then
        echo "FAIL negative control did not yield $id (rc=$rc):"; echo "$out"; exit 1
    fi
    echo "PASS planted pin -> $id"
}

controls() {
    T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
    cat > "$T/vendor.toml" <<'EOF'
[windows.xbox.source]
channel = "dev"
build = "latest"
[windows.xbox.drivers.virtio_win]
upstream = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/latest-virtio/virtio-win.iso"
channel = "latest"
[windows.xbox.guest_tools.looking_glass_host]
channel = "stable"
EOF
    cat > "$T/clean.ps1" <<'EOF'
$virtio = Resolve-MiOSLatest -Channel 'latest' -Feed 'https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/latest-virtio/'
$build = $Spec.source.build   # resolved at build time
Write-Host "host 10.0.2.15 build $($Manifest.windows.build)"
EOF
    printf '[windows.xbox.xbox.feature_overrides.x_1]\nid = 1\nbuilds = "26300.8553+"   # available-from annotation\n' > "$T/note.toml"
    expect_clean --layer "$T/vendor.toml" --layer "$T/note.toml" "$T/clean.ps1"
    echo "PASS clean [windows.xbox] (incl. an available-from builds note) + clean pipeline file carry no pin"

    printf '[windows.xbox.drivers.virtio_win]\nupstream = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"\n' > "$T/u1.toml"
    expect_finding pin.ssot-stable-virtio --layer "$T/vendor.toml" --layer "$T/u1.toml"
    printf '[windows.xbox.source]\nbuild = "29683.1000"\n' > "$T/u2.toml"
    expect_finding pin.ssot --layer "$T/vendor.toml" --layer "$T/u2.toml"
    expect_finding pin.ssot-uup-build --layer "$T/vendor.toml" --layer "$T/u2.toml"
    printf '[windows.xbox.guest_tools.qemu_ga]\nversion = "105.0.2"\n' > "$T/u3.toml"
    expect_finding pin.ssot --layer "$T/vendor.toml" --layer "$T/u3.toml"
    printf '[windows.xbox.drivers.looking_glass_idd]\ncommit = "0123456789abcdef0123456789abcdef01234567"\n' > "$T/u4.toml"
    expect_finding pin.ssot --layer "$T/vendor.toml" --layer "$T/u4.toml"

    local i=0
    while IFS='|' read -r id line; do
        i=$((i + 1)); printf '%s\n' "$line" > "$T/p$i.ps1"
        expect_finding "$id" "$T/p$i.ps1"
    done <<'EOF'
pin.stable-virtio|$u = 'https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso'
pin.virtio-version|$u = 'https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-virtio/virtio-win-0.1.285-1/virtio-win.iso'
pin.uup-build|$UupBuild = '29683.1000'
pin.uup-build|& .\mios-uup-fetch.ps1 -Build 29683
pin.looking-glass|$lg = 'https://looking-glass.io/artifact/B7/host'
pin.commit|git checkout 0123456789abcdef0123456789abcdef01234567
pin.release-url|$w = 'https://github.com/winfsp/winfsp/releases/download/v2.1/winfsp-2.1.25156.msi'
pin.version-literal|$qemuGaVersion = "105.0.2"
EOF
    printf '%s\n' "\$u = 'stable-virtio'   # pin-gate:allow (a deny-list pattern)" > "$T/allow.ps1"
    expect_clean "$T/allow.ps1"
    echo "PASS the pin-gate:allow marker exempts a deny-list pattern line"
}

live() {
    local layers=(--layer "$ROOT/usr/share/mios/mios.toml") f files=()
    if [ -d "$STAGED" ]; then
        for f in "$STAGED"/xbox-*.toml; do [ -f "$f" ] && layers+=(--layer "$f"); done
    fi
    for f in "$ROOT"/usr/libexec/mios/win/xbox/*.py "$ROOT"/usr/share/mios/windows/build/* \
             "$ROOT"/usr/share/mios/windows/xbox/*.ps1 "$ROOT"/usr/share/mios/windows/xbox/*.json \
             "$ROOT"/automation/21-virt.sh; do
        [ -f "$f" ] && files+=("$f")
    done
    echo "live scan: ${#layers[@]} layer args, ${#files[@]} pipeline files"
    if scan "${layers[@]}" "${files[@]}"; then
        echo "PASS live [windows.xbox] SSOT + Windows pipeline code carry no pin"
    else
        echo "FAIL live Windows pipeline carries pins (see FINDING lines)"; return 1
    fi
}

case "${1:-}" in
    --controls) controls ;;
    --scan) shift; scan "$@" ;;
    *) controls; live ;;
esac
