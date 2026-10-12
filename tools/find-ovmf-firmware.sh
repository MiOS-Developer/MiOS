#!/bin/bash
# AI-hint: Shared OVMF firmware library + discovery UI: finds OVMF CODE/VARS pairs under /usr/share, proves Secure Boot capability and enrollment from content (qemu descriptors, bounded EDK2 varstore parser) and rejects incompatible pairs.
# AI-functions: find_vars_for_code, ovmf_share_root, ovmf_fwdesc_dir, ovmf_file_format, ovmf_descriptor_pairs, ovmf_vars_enrollment, ovmf_sb_capability, ovmf_pair_status, ovmf_enroll_with_virt_fw_vars, ovmf_install_verified_vars, ovmf_repair_menu
#
# Incompatible = a cross-build, size or format mismatch. Sourced by tools/get-secureboot-ovmf.sh,
# tools/check-ovmf-enrollment.sh and tools/fix-ovmf-enrollment.sh; run directly for the discovery report.
# Upstream contracts: QEMU docs/interop/firmware.json and EDK2
# MdeModulePkg/Include/Guid/VariableFormat.h, MdePkg/Include/Guid/ImageAuthentication.h.
#   - Fedora ships /usr/share/edk2/ovmf/{OVMF_CODE[.secboot].fd, OVMF_VARS[.secboot].fd, *_4M.qcow2}
#     Enrollment varies by package and template; every candidate is content-checked. /usr/share/OVMF/* are compat symlinks. Gerd Hoffmann's RPMs
#     use /usr/share/edk2/x64 with .4m. names and ship NO enrolled VARS.
#   - /usr/share/qemu/firmware/*.json descriptors are the authoritative capability+pairing
#     source: each descriptor pairs exactly one CODE with one VARS template and lists
#     features ("secure-boot", "enrolled-keys"). libvirt firmware autoselection reads these.
#   - libvirt NEVER enrolls keys itself; the enrolled-keys feature selects a pre-enrolled
#     template. Enrollment is created by virt-firmware: virt-fw-vars --enroll-redhat --secure-boot.
#   - A descriptor identifies compatible CODE/VARS and each image format. Both image
#     structures are validated; filenames and directories do not prove compatible builds.
#
# Testability (scoped test seams, not MiOS settings): OVMF_SHARE_ROOT overrides the /usr/share root (default /usr/share) and
# OVMF_FWDESC_DIR overrides the descriptor directory. No script in this family ever
# writes to /var/lib/libvirt/qemu/nvram or overwrites an existing firmware file.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Library (sourced by get-secureboot-ovmf.sh / check-ovmf-enrollment.sh /
# fix-ovmf-enrollment.sh). Everything below is pure until ovmf_main runs.
# ---------------------------------------------------------------------------

ovmf_share_root() {
    echo "${OVMF_SHARE_ROOT:-/usr/share}"
}

ovmf_fwdesc_dir() {
    echo "${OVMF_FWDESC_DIR:-$(ovmf_share_root)/qemu/firmware}"
}

ovmf_human_size() {
    local bytes=$1
    if command -v numfmt &>/dev/null; then
        numfmt --to=iec-i --suffix=B "$bytes"
    else
        echo "${bytes}B"
    fi
}

ovmf_file_size() {
    stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0
}

# Print "raw", "qcow2" or "unknown" for a firmware file (magic sniff, no names).
ovmf_file_format() {
    local f=$1 magic
    [ -f "$f" ] || { echo unknown; return; }
    magic=$(head -c 4 -- "$f" 2>/dev/null | od -An -tx1 | tr -d ' \n')
    case "$magic" in
        514649fb) echo qcow2 ;;   # QFI\xfb
        *)        echo raw ;;
    esac
}

# Enumerate split-flash firmware descriptors (qemu/libvirt firmware autoselection DB).
# Output TSV per descriptor: json_path \t code \t vars \t features \t format \t desc
ovmf_descriptor_pairs() {
    local desc_dir
    desc_dir=$(ovmf_fwdesc_dir)
    [ -d "$desc_dir" ] || return 0
    python3 - "$desc_dir" <<'PYEOF' 2>/dev/null || true
import glob, json, sys
for p in sorted(glob.glob(sys.argv[1] + '/*.json')):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception:
        continue
    if not isinstance(d, dict):
        continue
    m = d.get('mapping', {})
    if not isinstance(m, dict):
        continue
    if m.get('device') != 'flash' or m.get('mode', 'split') != 'split':
        continue
    exe = m.get('executable', {}) or {}
    nv = m.get('nvram-template', {}) or {}
    if not isinstance(exe, dict) or not isinstance(nv, dict):
        continue
    code = exe.get('filename', '')
    vars_ = nv.get('filename', '')
    if not code or not vars_:
        continue
    formats = (exe.get('format'), nv.get('format'))
    features = d.get('features', [])
    if not all(fmt in ('raw', 'qcow2') for fmt in formats) or not isinstance(features, list) or not all(isinstance(item, str) and item and all(c.isalnum() or c in '-_' for c in item) for item in features):
        continue
    fields = [p, code, vars_, d.get('description', '')]
    if not all(isinstance(item, str) and not any(c in item for c in '\t\r\n') for item in fields):
        continue
    feats = ','.join(sorted(features)) or '-'
    print('\t'.join([p, code, vars_, feats, ':'.join(formats), d.get('description', '') or '-']))
PYEOF
}

# Determine the enrollment state of a VARS file from its CONTENT.
# Prints one line: "ENROLLED <vars...>" | "BLANK" | "UNKNOWN <reason>"
# Uses the bounded EDK2 parser to validate live state, namespace, payload,
# and Secure Boot enable intent. NEVER infers enrollment from the filename.
ovmf_vars_enrollment() {
    local f=$1 tmp="" parsed
    if [ ! -f "$f" ]; then
        echo "UNKNOWN not a file: $f"
        return 1
    fi
    # qcow2 varstores must be validated and converted read-only before parsing.
    if [ "$(ovmf_file_format "$f")" = "qcow2" ]; then
        ovmf_validate_image "$f" qcow2 >/dev/null || { echo "UNKNOWN qcow2 image is unverified"; return 1; }
        if command -v qemu-img &>/dev/null; then
            tmp=$(mktemp /tmp/ovmf-vars-XXXXXX.raw) || { echo "UNKNOWN mktemp failed"; return 1; }
            if ! qemu-img convert -f qcow2 -O raw "$f" "$tmp" 2>/dev/null; then
                rm -f "$tmp"; echo "UNKNOWN qcow2 convert failed: $f"; return 1
            fi
            parsed=$(ovmf_vars_enrollment_raw "$tmp")
            rm -f "$tmp"
            echo "$parsed"
            case "$parsed" in ENROLLED*|BLANK*) return 0 ;; *) return 1 ;; esac
        else
            echo "UNKNOWN qcow2 varstore and qemu-img not installed: $f"
            return 1
        fi
    fi
    parsed=$(ovmf_vars_enrollment_raw "$f")
    echo "$parsed"
    case "$parsed" in ENROLLED*|BLANK*) return 0 ;; *) return 1 ;; esac
}

ovmf_vars_enrollment_raw() {
    # Human-readable virt-fw-vars output lists names and cannot prove that a
    # variable is live, correctly namespaced, nonempty, and structurally valid.
    # One parser owns this decision; a tool's successful print is no fallback.
    ovmf_parse_varstore_python "$1"
}

# Bounded EDK2 parser for authenticated and standard GUID variable stores.
# Enrollment and enable intent are offline evidence; runtime enforcement is not measured.
ovmf_parse_varstore_python() {
    if ! command -v python3 &>/dev/null; then
        echo "UNKNOWN python3 not available for varstore parsing"
        return 1
    fi
    python3 - "$1" <<'PYEOF'
import struct, sys, uuid

def report(state, detail):
    print(f"{state} {detail}")
    sys.exit(0 if state in ("ENROLLED", "BLANK") else 1)

def guid(value):
    return uuid.UUID(value).bytes_le

try:
    with open(sys.argv[1], 'rb') as stream:
        d = stream.read(64 * 1024 * 1024 + 1)
except OSError as error:
    report('UNKNOWN', f'unreadable: {error}')
if len(d) < 72 or len(d) > 64 * 1024 * 1024 or d[40:44] != b'_FVH':
    report('UNKNOWN', 'invalid or unsupported firmware-volume image')
fvlen, = struct.unpack_from('<Q', d, 32)
hlen, = struct.unpack_from('<H', d, 48)
if hlen < 72 or hlen % 2 or not hlen + 28 <= fvlen <= len(d):
    report('UNKNOWN', 'truncated firmware volume or invalid header length')
if d[54:56] != b'\x00\x02' or sum(struct.unpack_from('<' + 'H' * (hlen // 2), d)) & 0xffff:
    report('UNKNOWN', 'invalid firmware-volume revision/checksum')
total = 0
for block_at in range(56, hlen-7, 8):
    count, block_size = struct.unpack_from('<II', d, block_at)
    if count == block_size == 0:
        break
    if not count or not block_size:
        report('UNKNOWN', 'invalid firmware block map')
    total += count * block_size
else:
    report('UNKNOWN', 'unterminated firmware block map')
if total != fvlen:
    report('UNKNOWN', 'firmware block map disagrees with volume length')
size, = struct.unpack_from('<I', d, hlen + 16)
end = hlen + size
if size < 28 or end > fvlen or d[hlen+20:hlen+22] != b'\x5a\xfe':
    report('UNKNOWN', 'truncated, unformatted, or unhealthy variable store')
store_guid = d[hlen:hlen+16]
if store_guid == guid('aaf32c78-947b-439a-a180-2e144ec37792'):
    hdr, sizes_at, vendor_at = 60, 36, 44
elif store_guid == guid('ddcf3616-3275-4164-98b6-fe85707ffe7d'):
    hdr, sizes_at, vendor_at = 32, 8, 16
else:
    report('UNKNOWN', 'unsupported variable-store signature')
p = (hlen + 28 + 3) & ~3
active = {}
while p < end:
    if all(byte == 0xff for byte in d[p:end]):
        break
    if p + hdr > end or struct.unpack_from('<H', d, p)[0] != 0x55aa:
        report('UNKNOWN', f'truncated or invalid variable header at {p:#x}')
    state = d[p+2]
    ns, ds = struct.unpack_from('<II', d, p + sizes_at)
    data_at = p + hdr + ns
    next_p = (data_at + ds + 3) & ~3
    if ns < 2 or ns % 2 or ns > 4096 or ds > 262144 or data_at + ds > end:
        report('UNKNOWN', f'invalid or truncated variable sizes at {p:#x}')
    name_bytes = d[p+hdr:data_at]
    try:
        if name_bytes[-2:] != b'\x00\x00':
            raise ValueError('missing terminator')
        name = name_bytes[:-2].decode('utf-16-le')
        if '\x00' in name:
            raise ValueError('embedded terminator')
    except (UnicodeDecodeError, ValueError):
        report('UNKNOWN', f'invalid variable name at {p:#x}')
    if state == 0x3f:  # VAR_ADDED; obsolete/header-only records are not keys.
        key = (name, d[p+vendor_at:p+vendor_at+16])
        if key in active:
            report('UNKNOWN', f'ambiguous duplicate live variable {name}')
        active[key] = (d[data_at:data_at+ds], struct.unpack_from('<I', d, p+4)[0])
    elif state not in (0x3c, 0x3d, 0x3e, 0x7f):
        report('UNKNOWN', f'unsupported variable state {state:#x}')
    p = next_p

global_guid = guid('8be4df61-93ca-11d2-aa0d-00e098032b8c')
db_guid = guid('d719b2cb-3d3a-4596-a3bc-dad00e67656f')
enable_guid = guid('f0a30bc7-af08-4556-99c4-001009c93a44')
required = [('PK', global_guid), ('KEK', global_guid), ('db', db_guid)]
seen = [name for name, vendor in required if (name, vendor) in active]
if not seen:
    report('BLANK', 'no live PK/KEK/db in their UEFI namespaces')
if len(seen) != len(required):
    report('UNKNOWN', 'incomplete live PK/KEK/db enrollment: ' + ','.join(seen))

# EFI_SIGNATURE_LIST: strict bounds, complete nonempty entries, supported
# signature types. Certificate parsing proves DER content, not trust policy.
def validate_signatures(name, data):
    offset, entries = 0, 0
    rsa = guid('3c5766e8-269c-4e34-aa14-ed776e85b3b6')
    x509 = guid('a5c059a1-94e4-4aa7-87b5-ab155c2bf072')
    sha256 = guid('c1c41626-504c-4092-aca9-41f936934328')
    while offset < len(data):
        if offset + 28 > len(data):
            report('UNKNOWN', f'{name} signature list header truncated')
        kind = data[offset:offset+16]
        size, header, signature = struct.unpack_from('<III', data, offset+16)
        count_bytes = size - 28 - header
        if size < 28 or header != 0 or signature <= 16 or count_bytes <= 0 or count_bytes % signature or offset + size > len(data):
            report('UNKNOWN', f'{name} signature list size invalid')
        if kind not in (rsa, x509, sha256) or (name == 'PK' and kind not in (rsa, x509)):
            report('UNKNOWN', f'{name} unsupported signature type')
        for entry in range(offset+28, offset+size, signature):
            payload = data[entry+16:entry+signature]
            if kind == rsa and (len(payload) != 256 or not any(payload) or not payload[-1] & 1):
                report('UNKNOWN', f'{name} invalid RSA2048 entry')
            if kind == sha256 and len(payload) != 32:
                report('UNKNOWN', f'{name} invalid SHA256 entry')
            if kind == x509:
                try:
                    from cryptography.x509 import load_der_x509_certificate
                    load_der_x509_certificate(payload)
                except (ImportError, ValueError):
                    report('UNKNOWN', f'{name} certificate invalid or cryptography unavailable')
            entries += 1
        offset += size
    if not entries or (name == 'PK' and entries != 1):
        report('UNKNOWN', f'{name} must contain valid signature entries')

for name, vendor in required:
    data, attrs = active[(name, vendor)]
    if not data or attrs & 0x27 != 0x27:
        report('UNKNOWN', f'{name} empty or missing authenticated NV/BS/RT attributes')
    validate_signatures(name, data)
if ('dbx', db_guid) in active:
    data, attrs = active[('dbx', db_guid)]
    if data:
        validate_signatures('dbx', data)
if active.get(('SetupMode', global_guid), (b'\x00', 0))[0] != b'\x00':
    report('UNKNOWN', 'SetupMode contradicts enrollment')
enabled = active.get(('SecureBootEnable', enable_guid), active.get(('SecureBoot', global_guid), (None, 0)))[0]
if enabled != b'\x01':
    report('UNKNOWN', 'Secure Boot enable intent absent or disabled')
report('ENROLLED', 'live authenticated PK+KEK+db signature lists and enable intent verified; runtime enforcement is not measured')
PYEOF
}

# Secure Boot CAPABILITY of a CODE image (distinct from enrollment!).
# Prints: "yes <evidence>" | "no <evidence>" | "unknown <reason>"
# Capability requires a package descriptor plus a validated firmware image.
# A name or firmware-volume signature alone cannot identify compiled features.
ovmf_validate_image() {
    local image=$1 expected=$2 raw=$1 work="" info
    command -v python3 &>/dev/null || { echo "REJECT PYTHON_MISSING"; return 1; }
    [ -f "$image" ] || { echo "REJECT IMAGE_MISSING"; return 1; }
    [ "$(ovmf_file_format "$image")" = "$expected" ] || { echo "REJECT FORMAT_MISMATCH"; return 1; }
    if [ "$expected" = qcow2 ]; then
        command -v qemu-img &>/dev/null || { echo "REJECT QCOW2_UNVERIFIED qemu-img missing"; return 1; }
        info=$(qemu-img info --output=json "$image" 2>/dev/null) || { echo "REJECT QCOW2_INVALID"; return 1; }
        if ! python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("format")=="qcow2" and 0<d.get("virtual-size",0)<=64*1024*1024 and not d.get("backing-filename") else 1)' <<< "$info"; then
            echo "REJECT QCOW2_BACKING_OR_FORMAT"; return 1
        fi
        qemu-img check -f qcow2 "$image" >/dev/null 2>&1 || { echo "REJECT QCOW2_METADATA"; return 1; }
        work=$(mktemp /tmp/ovmf-image-XXXXXX.raw) || return 1
        qemu-img convert -f qcow2 -O raw "$image" "$work" >/dev/null 2>&1 || { rm -f -- "$work"; echo "REJECT QCOW2_CONVERT"; return 1; }
        raw=$work
    fi
    local result rc
    result=$(python3 - "$raw" <<'PYEOF'
import struct, sys
try:
    with open(sys.argv[1], 'rb') as stream:
        d=stream.read(64 * 1024 * 1024 + 1)
    if not 72 <= len(d) <= 64 * 1024 * 1024 or d[40:44] != b'_FVH':
        raise ValueError('invalid firmware-volume signature/size')
    length,=struct.unpack_from('<Q',d,32)
    header,=struct.unpack_from('<H',d,48)
    if not 72 <= header <= length <= len(d) or header % 2 or d[54:56] != b'\x00\x02':
        raise ValueError('invalid firmware-volume bounds/revision')
    if sum(struct.unpack_from('<'+'H'*(header//2),d)) & 0xffff:
        raise ValueError('invalid firmware-volume checksum')
    total=0
    for p in range(56,header-7,8):
        count,size=struct.unpack_from('<II',d,p)
        if count==size==0:
            break
        if not count or not size:
            raise ValueError('invalid block map')
        total += count * size
    else:
        raise ValueError('unterminated block map')
    if total != length:
        raise ValueError('block map disagrees with volume length')
    print('OK verified firmware-volume header and block map')
except (OSError, ValueError, struct.error) as error:
    print(f'REJECT INVALID_FIRMWARE {error}')
    sys.exit(1)
PYEOF
)
    rc=$?
    [ -z "$work" ] || rm -f -- "$work"
    echo "${result:-REJECT IMAGE_UNVERIFIABLE}"
    return "$rc"
}

ovmf_sb_capability() {
    local code=$1 json c vars feats fmt desc proof
    [ -f "$code" ] || { echo "unknown file missing: $code"; return 1; }
    while IFS=$'\t' read -r json c vars feats fmt desc; do
        [ "$c" = "$code" ] || continue
        proof=$(ovmf_validate_image "$code" "${fmt%%:*}") || { echo "unknown $proof"; return 1; }
        case ",$feats," in
            *,secure-boot,*) echo "yes validated image and descriptor $(basename "$json") lists secure-boot"; return 0 ;;
            *) echo "no validated descriptor $(basename "$json") lists no secure-boot feature"; return 0 ;;
        esac
    done < <(ovmf_descriptor_pairs)
    echo "unknown no validated firmware descriptor proves Secure Boot capability"
    return 1
}

# Compatibility of a CODE/VARS pair. Prints "OK <evidence>" or "REJECT <named reason>".
# A validated descriptor identifies the exact compatible pair and the format
# of each pflash image; CODE and VARS may legitimately use different formats.
# Both image headers and the VARS content must pass verification. A directory
# or a size/name class does not prove a common build.
ovmf_pair_status() {
    local code=$1 vars=$2 json c v feats fmt desc proof state
    [ -f "$code" ] || { echo "REJECT CODE_MISSING $code"; return 1; }
    [ -f "$vars" ] || { echo "REJECT VARS_MISSING $vars"; return 1; }
    while IFS=$'\t' read -r json c v feats fmt desc; do
        [ "$c" = "$code" ] && [ "$v" = "$vars" ] || continue
        proof=$(ovmf_validate_image "$code" "${fmt%%:*}") || { echo "$proof CODE"; return 1; }
        proof=$(ovmf_validate_image "$vars" "${fmt##*:}") || { echo "$proof VARS"; return 1; }
        state=$(ovmf_vars_enrollment "$vars") || { echo "REJECT VARSTORE_UNVERIFIED $state"; return 1; }
        case ",$feats," in
            *,enrolled-keys,*) case "$state" in ENROLLED*) ;; *) echo "REJECT DESCRIPTOR_ENROLLMENT_MISMATCH $state"; return 1 ;; esac ;;
        esac
        echo "OK validated descriptor $(basename "$json") pairs content-verified $fmt CODE+VARS"
        return 0
    done < <(ovmf_descriptor_pairs)
    echo "REJECT UNPROVEN_PAIR no validated descriptor pairs this CODE+VARS; directory and filename do not prove one build"
    return 1
}

# Display-only size class of a firmware filename: "4m" or "2m" (kraxel ".4m." lowercase,
# Fedora "_4M." uppercase). This hint is never accepted as pairing proof.
ovmf_size_class() {
    local b
    b=$(basename "$1")
    case "$b" in
        *4[mM].fd|*4[mM].qcow2|*[._]4[mM][._]*) echo 4m ;;
        *)                                     echo 2m ;;
    esac
}

# Public pairing helper (kept for callers/metadata): print the best compatible
# VARS for a CODE file, or nothing. Never falls back to an arbitrary VARS file:
# only name-family candidates that PASS ovmf_pair_status qualify.
find_vars_for_code() {
    local code_path=$1 dir filename candidate vars_path status
    [ -f "$code_path" ] || return 1
    dir=$(dirname "$code_path")
    filename=$(basename "$code_path")
    local json c vars feats fmt desc
    while IFS=$'\t' read -r json c vars feats fmt desc; do
        [ "$c" = "$code_path" ] || continue
        ovmf_pair_status "$code_path" "$vars" >/dev/null || continue
        echo "$vars"; return 0
    done < <(ovmf_descriptor_pairs)
    # Name-family candidates, most-specific first: direct CODE->VARS rename
    # (keeps .secboot/_4M/.4m/.qcow2 markers), then the blank-VARS family of
    # the same build (secboot CODE boots fine on the blank same-build VARS;
    # enrollment is a separate, content-verified question).
    local -a candidates=(
        "${filename/OVMF_CODE/OVMF_VARS}"
    )
    case "$filename" in
        *secboot*) candidates+=("${filename//.secboot/}") ;;
    esac
    for candidate in "${candidates[@]}"; do
        [ -n "$candidate" ] || continue
        [ "$candidate" = "$filename" ] && continue
        vars_path="$dir/$candidate"
        [ -f "$vars_path" ] || continue
        status=$(ovmf_pair_status "$code_path" "$vars_path")
        case "$status" in
            OK*) echo "$vars_path"; return 0 ;;
        esac
    done
    return 1
}

# Find the best verified-enrolled VARS on the system (descriptor-backed first).
# Prints: "<path>\t<evidence>" or nothing.
ovmf_find_enrolled_vars() {
    local json code vars feats state
    while IFS=$'\t' read -r json code vars feats _fmt _desc; do
        case ",$feats," in
            *,enrolled-keys,*)
                ovmf_pair_status "$code" "$vars" >/dev/null || continue
                state=$(ovmf_vars_enrollment "$vars")
                case "$state" in
                    ENROLLED*)
                        echo -e "$vars\tdescriptor $(basename "$json") (enrolled-keys feature) and content verified"
                        return 0
                        ;;
                esac
                ;;
        esac
    done < <(ovmf_descriptor_pairs)
    local f state
    while IFS= read -r f; do
        state=$(ovmf_vars_enrollment "$f")
        case "$state" in
            ENROLLED*) echo -e "$f\t$state"; return 0 ;;
        esac
    done < <(find "$(ovmf_share_root)/edk2" "$(ovmf_share_root)/OVMF" -type f \( -name 'OVMF_VARS*.fd' -o -name 'OVMF_VARS*.qcow2' \) 2>/dev/null | sort)
    return 1
}

# Enroll a COPY of a blank VARS template with virt-firmware (vendor/MS keys).
# Never modifies the template in place; output goes to a new file.
ovmf_enroll_with_virt_fw_vars() {
    local template=$1 out=$2 work state
    command -v virt-fw-vars &>/dev/null || { echo "virt-fw-vars not installed" >&2; return 1; }
    [ -f "$template" ] || { echo "template missing: $template" >&2; return 1; }
    [ ! -e "$out" ] && [ ! -L "$out" ] || { echo "refusing to overwrite $out" >&2; return 1; }
    case "$(ovmf_vars_enrollment "$template")" in BLANK*) ;; *) echo "template must be verified blank" >&2; return 1 ;; esac
    work=$(mktemp "$(dirname "$out")/.ovmf-enroll-XXXXXX") || return 1
    if ! virt-fw-vars --input "$template" --output "$work" --enroll-redhat --secure-boot; then
        rm -f -- "$work"; echo "virt-fw-vars enrollment failed" >&2; return 1
    fi
    state=$(ovmf_vars_enrollment "$work")
    case "$state" in
        ENROLLED*) ;;
        *) rm -f -- "$work"; echo "post-enrollment verification failed: $state" >&2; return 1 ;;
    esac
    # Hard-link publication refuses an output created by a concurrent caller.
    if ! ln -- "$work" "$out"; then rm -f -- "$work"; return 1; fi
    rm -f -- "$work"
}

# Install a VARS file into a target directory after verification, atomically.
# Refuses to overwrite anything, refuses unverified (non-ENROLLED) sources,
# requires validated descriptor pairing when a target CODE is supplied.
ovmf_install_verified_vars() {
    local src=$1 dest_dir=$2 dest_name=$3 code_hint=$4 tmp state
    [ -f "$src" ] || { echo "source missing: $src"; return 1; }
    state=$(ovmf_vars_enrollment "$src")
    case "$state" in
        ENROLLED*) ;;
        *) echo "refusing to install: source varstore is not verified ENROLLED ($state)"; return 1 ;;
    esac
    if [ -n "$code_hint" ]; then
        local pair
        pair=$(ovmf_pair_status "$code_hint" "$src")
        case "$pair" in
            OK*) ;;
            *) echo "refusing to install: $pair"; return 1 ;;
        esac
    fi
    mkdir -p -- "$dest_dir" || return 1
    if [ -e "$dest_dir/$dest_name" ]; then
        echo "refusing to overwrite existing $dest_dir/$dest_name (never replace firmware or NVRAM in place)"
        return 1
    fi
    tmp=$(mktemp "$dest_dir/.ovmf-vars-XXXXXX") || return 1
    if ! cp -- "$src" "$tmp"; then rm -f "$tmp"; return 1; fi
    chmod 644 "$tmp" || { rm -f -- "$tmp"; return 1; }
    # Validate the actual copy before publication; copying is not proof that
    # its source remained unchanged. Never publish a failed candidate.
    case "$(ovmf_vars_enrollment "$tmp")" in
        ENROLLED*) ;;
        *) rm -f -- "$tmp"; echo "copied candidate failed verification"; return 1 ;;
    esac
    if ! ln -- "$tmp" "$dest_dir/$dest_name"; then rm -f -- "$tmp"; return 1; fi
    rm -f -- "$tmp"
    echo "installed verified enrolled varstore: $dest_dir/$dest_name"
    return 0
}

# Derive the conventional enrolled-VARS filename from a blank template name:
# OVMF_VARS.4m.fd -> OVMF_VARS.secboot.4m.fd, OVMF_VARS_4M.qcow2 ->
# OVMF_VARS_4M.secboot.qcow2, OVMF_VARS.fd -> OVMF_VARS.secboot.fd.
ovmf_derive_enrolled_name() {
    local b
    b=$(basename "$1")
    case "$b" in
        *secboot*) echo "$b" ;;
        OVMF_VARS.fd) echo "OVMF_VARS.secboot.fd" ;;
        *VARS*.fd) echo "${b/VARS./VARS.secboot.}" ;;
        *VARS*.qcow2) echo "${b/VARS_4M./VARS_4M.secboot.}" ;;
        *) echo "OVMF_VARS.secboot.mios.fd" ;;
    esac
}

# Interactive repair menu shared by get-secureboot-ovmf.sh and
# fix-ovmf-enrollment.sh (root install). No hardcoded download URLs: the only
# network path is the distro package manager (dnf download), and every
# artifact passes content + pair verification before anything is written.
# Never overwrites an existing file; never touches /var/lib/libvirt/qemu/nvram.
ovmf_repair_menu() {
    local target_dir=${1:-$(ovmf_share_root)/edk2/x64}
    local blank_template choice f code_hint

    # If the target directory already has a CODE image, every candidate VARS
    # must pair with it (named rejection otherwise).
    code_hint=""
    for f in "$target_dir"/*CODE*; do
        [ -f "$f" ] && { code_hint="$f"; break; }
    done

    echo -e "${BOLD}Repair options (nothing is modified without your choice):${NC}\n"
    echo -e "  ${CYAN}1)${NC} Copy a content-verified enrolled VARS already on this system (dnf install edk2-ovmf provides one)"
    echo -e "  ${CYAN}2)${NC} Enroll a fresh copy of the local same-build blank VARS with virt-fw-vars (offline, verified)"
    echo -e "  ${CYAN}3)${NC} Fetch current edk2-ovmf via 'dnf download', extract, verify, install (repo-tracked, no stale URLs)"
    echo -e "  ${CYAN}4)${NC} Print guidance only (libvirt autoselection facts + manual steps)"
    echo
    read -r -p "Choose option (1-4): " choice
    echo
    case "$choice" in
        1)
            local picked="" ev
            # Prefer an enrolled source that PASSES the pair check with the
            # target CODE; otherwise report the named rejection.
            while IFS= read -r f; do
                [ -f "$f" ] || continue
                if [ -n "$code_hint" ]; then
                    ev=$(ovmf_pair_status "$code_hint" "$f")
                    case "$ev" in OK*) ;; *) continue ;; esac
                fi
                case "$(ovmf_vars_enrollment "$f")" in
                    ENROLLED*) picked="$f"; break ;;
                esac
            done < <(find "$(ovmf_share_root)/edk2" "$(ovmf_share_root)/OVMF" -type f \( -name '*VARS*.fd' -o -name '*VARS*.qcow2' \) 2>/dev/null | sort)
            if [ -n "$picked" ]; then
                echo -e "${GREEN}[ok]${NC} Verified+pair-compatible source: $picked"
                ovmf_install_verified_vars "$picked" "$target_dir" "$(ovmf_derive_enrolled_name "$picked")" "$code_hint"
                return $?
            fi
            echo -e "${RED}[x] No content-verified enrolled VARS that is pair-compatible with this layout.${NC}"
            echo -e "${YELLOW}Try option 2 (enrolls from the local same-build template) or: sudo dnf install edk2-ovmf${NC}"
            return 1
            ;;
        2)
            blank_template=""
            for f in "$target_dir"/OVMF_VARS*.fd "$target_dir"/OVMF_VARS*.qcow2; do
                [ -f "$f" ] || continue
                case "$(basename "$f")" in *secboot*) continue ;; esac
                blank_template="$f"; break
            done
            if [ -z "$blank_template" ]; then
                echo -e "${RED}[x] No blank VARS template in $target_dir to enroll from (install edk2-ovmf first).${NC}"
                return 1
            fi
            case "$(ovmf_vars_enrollment "$blank_template")" in BLANK*) ;; *) echo "Template not verified blank"; return 1 ;; esac
            if [ -n "$code_hint" ]; then
                ovmf_pair_status "$code_hint" "$blank_template" >/dev/null || { echo "Template is not paired with target CODE"; return 1; }
            fi
            local out_name
            out_name=$(ovmf_derive_enrolled_name "$blank_template")
            if [ -e "$target_dir/$out_name" ]; then
                case "$out_name" in
                    *.fd) out_name="${out_name%.fd}.mios.fd" ;;
                    *.qcow2) out_name="${out_name%.qcow2}.mios.qcow2" ;;
                esac
            fi
            if [ "$(ovmf_file_format "$blank_template")" = "qcow2" ]; then
                # Enroll in raw space, then convert back so the format matches.
                if ! command -v qemu-img &>/dev/null; then
                    echo -e "${RED}[x] qcow2 template needs qemu-img for enrollment${NC}"
                    return 1
                fi
                local workraw
                workraw=$(mktemp /tmp/ovmf-enroll-XXXXXX.fd) || return 1
                qemu-img convert -f qcow2 -O raw "$blank_template" "$workraw" || { rm -f "$workraw"; return 1; }
                if ovmf_enroll_with_virt_fw_vars "$workraw" "$workraw.enrolled"; then
                    local qcow_copy="${workraw}.qcow2" rc
                    if ! qemu-img convert -f raw -O qcow2 "$workraw.enrolled" "$qcow_copy"; then
                        rm -f -- "$workraw" "$workraw.enrolled" "$qcow_copy"; return 1
                    fi
                    # Pair proof came from the untouched blank template above;
                    # the new store is verified after the format round-trip.
                    ovmf_install_verified_vars "$qcow_copy" "$target_dir" "$out_name" ""
                    rc=$?
                    rm -f -- "$workraw" "$workraw.enrolled" "$qcow_copy"
                    return "$rc"
                fi
                rm -f "$workraw" "$workraw.enrolled"
                return 1
            fi
            ovmf_enroll_with_virt_fw_vars "$blank_template" "$target_dir/$out_name" \
                && echo -e "${GREEN}[ok]${NC} Enrolled copy written (content-verified): $target_dir/$out_name" \
                || return 1
            ;;
        3)
            local work found=""
            work=$(mktemp -d /tmp/ovmf-dnf-XXXXXX) || return 1
            if ! command -v dnf &>/dev/null; then
                echo -e "${RED}[x] dnf not available; use option 1 or 2${NC}"
                rm -rf "$work"; return 1
            fi
            if ! ( cd "$work" && dnf download edk2-ovmf ); then
                echo -e "${RED}[x] dnf download edk2-ovmf failed${NC}"
                rm -rf "$work"; return 1
            fi
            if ! ( cd "$work" && rpm2cpio edk2-ovmf-*.rpm | cpio -idm --quiet ); then
                echo -e "${RED}[x] RPM extraction failed (need rpm2cpio + cpio)${NC}"
                rm -rf "$work"; return 1
            fi
            while IFS= read -r f; do
                case "$(ovmf_vars_enrollment "$f")" in ENROLLED*) found="$f"; break ;; esac
            done < <(find "$work" -type f -name 'OVMF_VARS*' 2>/dev/null)
            if [ -z "$found" ]; then
                echo -e "${RED}[x] Downloaded package contains no ENROLLED varstore. Nothing installed.${NC}"
                echo -e "${YELLOW}(Current Fedora ships OVMF_VARS.secboot.fd enrolled; if your release does not, use option 2.)${NC}"
                rm -rf "$work"; return 1
            fi
            ovmf_install_verified_vars "$found" "$target_dir" "$(ovmf_derive_enrolled_name "$found")" "$code_hint"
            local rc=$?
            rm -rf "$work"
            return $rc
            ;;
        4)
            ovmf_print_guidance
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            return 1
            ;;
    esac
}

ovmf_print_guidance() {
    cat <<GUIDE
Facts (upstream-verified):
  * libvirt firmware autoselection NEVER enrolls keys. <feature name='enrolled-keys'/>
    only SELECTS firmware whose varstore template already has keys enrolled.
  * A distribution may provide an enrolled template, for example:
      /usr/share/edk2/ovmf/OVMF_VARS.secboot.fd  (raw, MS keys enrolled)
      plus descriptors /usr/share/qemu/firmware/31-edk2-ovmf-2m-raw-x64-sb-enrolled.json
  * To create an enrolled varstore offline (MiOS ships virt-firmware):
      cp /usr/share/edk2/ovmf/OVMF_VARS.fd /tmp/enrolled_VARS.fd
      virt-fw-vars --input /tmp/enrolled_VARS.fd --output /tmp/enrolled_VARS.fd \\
                   --enroll-redhat --secure-boot
  * Select a descriptor-backed CODE/VARS pair and verify both artifacts.
    A common directory or filename family does not establish compatibility.
  * Never edit /var/lib/libvirt/qemu/nvram/* while the VM is running.
GUIDE
}

# ---------------------------------------------------------------------------
# Discovery UI (direct execution only)
# ---------------------------------------------------------------------------

ovmf_main() {
    local share dir dirs code_file vars_file capability state
    share=$(ovmf_share_root)

    echo -e "${BOLD}${GREEN}════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${GREEN}     OVMF Firmware Discovery Tool${NC}"
    echo -e "${BOLD}${GREEN}════════════════════════════════════════════════════${NC}\n"
    echo -e "${BLUE}Scanning $(ovmf_share_root) for OVMF firmware files...${NC}\n"

    local code_files
    code_files=$(find "$share/edk2" "$share/OVMF" -type f \( -name 'OVMF*.fd' -o -name 'OVMF*.qcow2' \) 2>/dev/null | sort)
    if [ -z "$code_files" ]; then
        echo -e "${RED}[x] No OVMF files found under $share!${NC}\n"
        echo -e "${YELLOW}Ensure it is in PACKAGES.md: ${NC}${CYAN}edk2-ovmf${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Found OVMF files:${NC}"
    echo "$code_files" | nl -w2 -s'. '
    echo

    echo -e "\n${CYAN}════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Firmware Files by Directory:${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════${NC}\n"

    dirs=$(echo "$code_files" | xargs -r dirname | sort -u)
    for dir in $dirs; do
        echo -e "${BOLD}$dir${NC}"
        ls -lh "$dir" 2>/dev/null | awk '/OVMF/ {printf "  %s  %s\n", $9, $5}'
        echo
    done

    echo -e "${CYAN}════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Firmware descriptor pairs ($(ovmf_fwdesc_dir)):${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════${NC}\n"
    local desc_count=0
    while IFS=$'\t' read -r json code vars feats fmt desc; do
        desc_count=$((desc_count + 1))
        echo -e "  ${BOLD}$(basename "$json")${NC}"
        echo -e "    CODE: $code ($fmt)"
        echo -e "    VARS: $vars"
        echo -e "    features: ${feats}, $desc"
    done < <(ovmf_descriptor_pairs)
    [ $desc_count -eq 0 ] && echo -e "  ${YELLOW}(no split-flash descriptors found - libvirt autoselection unavailable)${NC}"
    echo

    echo -e "${CYAN}════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Verified CODE/VARS Pairs (capability + enrollment from content):${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════${NC}\n"

    local pair_count=0 rejected=0
    while IFS= read -r code_file; do
        case "$(basename "$code_file")" in *CODE*) ;; *) continue ;; esac
        vars_file=$(find_vars_for_code "$code_file")
        if [ -z "$vars_file" ]; then
            rejected=$((rejected + 1))
            echo -e "${YELLOW}[!]${NC} $(basename "$code_file"): no compatible VARS in $(dirname "$code_file") (no name-family sibling with matching layout)"
            continue
        fi
        pair_count=$((pair_count + 1))
        capability=$(ovmf_sb_capability "$code_file")
        state=$(ovmf_vars_enrollment "$vars_file")
        echo -e "${BOLD}Pair #$pair_count:${NC} $(basename "$code_file") + $(basename "$vars_file")"
        echo -e "  ${YELLOW}CODE:${NC} $code_file ($(ovmf_human_size "$(ovmf_file_size "$code_file")"), $(ovmf_file_format "$code_file"))"
        echo -e "  ${YELLOW}VARS:${NC} $vars_file ($(ovmf_human_size "$(ovmf_file_size "$vars_file")"))"
        case "$capability" in
            yes*) echo -e "  ${GREEN}[ok] Secure Boot capable: $capability${NC}" ;;
            no*)  echo -e "  ${YELLOW}[i] Not Secure Boot: $capability${NC}" ;;
            *)    echo -e "  ${RED}[?] Capability UNVERIFIED: $capability${NC}" ;;
        esac
        case "$state" in
            ENROLLED*) echo -e "  ${GREEN}[ok] Keys ENROLLED (content-verified): $state${NC}" ;;
            BLANK*)    echo -e "  ${YELLOW}[i] Varstore blank (no keys): pair is SB-capable but NOT enrolled${NC}" ;;
            *)         echo -e "  ${RED}[?] Enrollment UNVERIFIED: $state${NC}" ;;
        esac
        echo
    done <<< "$code_files"

    if [ $pair_count -eq 0 ]; then
        echo -e "${RED}[x] No compatible CODE/VARS pair could be verified!${NC}"
        echo -e "${YELLOW}This might indicate:${NC}"
        echo -e "  1. edk2-ovmf package not installed or incomplete"
        echo -e "  2. VARS/CODE images from different builds mixed in one directory"
        echo -e "  3. Package is corrupted"
        echo
        echo -e "${YELLOW}Ensure it is in PACKAGES.md: ${NC}${CYAN}edk2-ovmf${NC}"
        exit 1
    fi

    echo -e "${CYAN}════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Recommendation:${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════${NC}\n"

    local best_code="" best_vars="" best_ev=""
    # Prefer: descriptor-backed secure-boot + enrolled-keys, then secboot-capable
    # with a blank varstore, then anything verified-compatible.
    while IFS=$'\t' read -r json code vars feats fmt _desc; do
        case ",$feats," in
            *,secure-boot,*)
                [ -f "$code" ] && [ -f "$vars" ] || continue
                ovmf_pair_status "$code" "$vars" >/dev/null || continue
                case "$(ovmf_vars_enrollment "$vars")" in
                    ENROLLED*)
                        best_code=$code; best_vars=$vars
                        best_ev="descriptor-backed secure-boot with enrolled keys ($(basename "$json")) - BEST"
                        break
                        ;;
                esac
                ;;
        esac
    done < <(ovmf_descriptor_pairs)
    if [ -z "$best_code" ]; then
        while IFS= read -r code_file; do
            case "$(basename "$code_file")" in *CODE*secboot*) ;; *) continue ;; esac
            case "$(ovmf_sb_capability "$code_file")" in yes*) ;; *) continue ;; esac
            vars_file=$(find_vars_for_code "$code_file")
            [ -n "$vars_file" ] || continue
            best_code=$code_file; best_vars=$vars_file
            best_ev="Secure Boot capable CODE; varstore may need enrollment (see check-ovmf-enrollment.sh)"
            break
        done <<< "$code_files"
    fi
    if [ -n "$best_code" ]; then
        echo -e "  ${BOLD}$best_ev${NC}"
        echo -e "  ${CYAN}CODE:${NC} $best_code ($(ovmf_human_size "$(ovmf_file_size "$best_code")"))"
        echo -e "  ${CYAN}VARS:${NC} $best_vars ($(ovmf_human_size "$(ovmf_file_size "$best_vars")"))"
        local secure_attr=no
        case "$(ovmf_sb_capability "$best_code")" in yes*) secure_attr=yes ;; esac
        echo
        echo -e "${BOLD}XML Configuration Snippet:${NC}"
        echo -e "${CYAN}────────────────────────────────────────────────────${NC}"
        cat << XMLSNIPPET
  <os>
    <type arch="x86_64" machine="pc-q35-10.1">hvm</type>
    <loader readonly="yes" secure="$secure_attr" type="pflash">$best_code</loader>
    <nvram template="$best_vars">/var/lib/libvirt/qemu/nvram/Xbox_VARS.fd</nvram>
    <bootmenu enable="yes"/>
  </os>
XMLSNIPPET
        echo -e "${CYAN}────────────────────────────────────────────────────${NC}"

        cat > /tmp/ovmf-paths.txt << EOF

CODE_PATH=$best_code
VARS_PATH=$best_vars
SECURE_BOOT=$secure_attr
TYPE=$best_ev

EOF
        echo
        echo -e "${GREEN}[ok] Paths saved to: ${NC}${CYAN}/tmp/ovmf-paths.txt${NC}"
    else
        echo -e "${RED}[x] Could not find a verified usable CODE/VARS pair!${NC}"
        echo -e "${YELLOW}Ensure it is in PACKAGES.md: ${NC}${CYAN}edk2-ovmf${NC}"
        exit 1
    fi

    echo -e "\n${BOLD}${GREEN}════════════════════════════════════════════════════${NC}\n"
}

# Library guard: run the UI only when executed directly.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    ovmf_main "$@"
fi
