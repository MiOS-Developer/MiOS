<!-- AI-hint: W10 live-chat design session artifacts that are text, not files to ship: edit snippets, superseded alternates and grounding excerpts, folded from docs/agy/w10-live-boot/. -->
<!-- AI-related: docs/design/w10-live-boot/_landing.md, docs/design/w10-live-boot/_design.md, docs/agy/w10-live-boot/live-iso.sh -->
# W10 live boot: snippets, alternates and grounding excerpts

The design session behind `_design.md` and `_landing.md` left its working material in `docs/agy/w10-live-boot/` as one file per edit target, with the target path mangled into the file name. The canonical landing set stays there as real files (`live-iso.sh`, `live-chat.toml`, `ventoy_grub.cfg` and the `overlay/` tree, whose getty masks are symlinks). Everything else was text describing an edit, so it lives here, verbatim apart from CRLF line endings. Each heading is the target path; the line under it names the file it was folded from.

## Edit snippets for the canonical landing

Text `_landing.md` applies by hand to files outside this directory: the `Justfile` recipe, the `MiOS-Cat.bat` hunks and the `[cat.live_chat]` block for `mios.toml`.

### `Justfile` (recipe)

Folded from `Justfile.snippet.txt`.

```text
EDIT TARGET: C:\MiOS\Justfile

Insert a new `live-chat-iso:` recipe as a sibling of `wsl2:` (same "BIB has
no type for this, repack it ourselves" shape) and fold it into `all:`.

--------------------------------- OLD --------------------------------------
# Generate WSL2 tar.gz for wsl --import
wsl2: build
    @mkdir -p output/wsl2
    @echo "[wsl2] BIB has no --type wsl2 -> exporting {{LOCAL}} rootfs for wsl --import"
    -sudo podman rm -f mios-wsl2-export 2>/dev/null
    sudo podman create --name mios-wsl2-export {{LOCAL}}
    sudo podman export mios-wsl2-export | gzip -c > output/wsl2/mios-rootfs.tar.gz
    sudo podman rm -f mios-wsl2-export
    @echo "[OK] WSL2 rootfs -> output/wsl2/mios-rootfs.tar.gz"
    @echo "     import: wsl --import MiOS <target-dir> output/wsl2/mios-rootfs.tar.gz"

# Build EVERY MiOS deployable artifact in one shot.
[...]
all: build raw iso usb-installer qcow2 vhdx wsl2
    @echo ""
    @echo "[OK] All MiOS deployable artifacts built. Output:"
    @ls -lah output/ 2>/dev/null || true
    @echo ""
    @echo "[NEXT] Run 'just verify-images' to confirm artifact integrity."
--------------------------------- NEW --------------------------------------
# Generate WSL2 tar.gz for wsl --import
wsl2: build
    @mkdir -p output/wsl2
    @echo "[wsl2] BIB has no --type wsl2 -> exporting {{LOCAL}} rootfs for wsl --import"
    -sudo podman rm -f mios-wsl2-export 2>/dev/null
    sudo podman create --name mios-wsl2-export {{LOCAL}}
    sudo podman export mios-wsl2-export | gzip -c > output/wsl2/mios-rootfs.tar.gz
    sudo podman rm -f mios-wsl2-export
    @echo "[OK] WSL2 rootfs -> output/wsl2/mios-rootfs.tar.gz"
    @echo "     import: wsl --import MiOS <target-dir> output/wsl2/mios-rootfs.tar.gz"

# Build MiOS-Live-Chat.iso -- W10, RAM-resident dmsquash-live boot of the
# SAME {{LOCAL}} image + bundled llama.cpp chat lane. BIB has no "live"
# --type either (same wsl2 gap) -- automation/build/live-iso.sh does the
# whole podman-export/overlay/dracut/mksquashfs/xorriso pipeline itself,
# and never mutates {{LOCAL}} (throwaway staged tag, deleted on exit).
live-chat-iso: build
    ./automation/build/live-iso.sh output/live-chat
    @echo "[OK] MiOS-Live-Chat.iso in output/live-chat/"

# Build EVERY MiOS deployable artifact in one shot.
[...]
all: build raw iso usb-installer qcow2 vhdx wsl2 live-chat-iso
    @echo ""
    @echo "[OK] All MiOS deployable artifacts built. Output:"
    @ls -lah output/ 2>/dev/null || true
    @echo ""
    @echo "[NEXT] Run 'just verify-images' to confirm artifact integrity."
------------------------------------------------------------------------------
```

### `C:\mios-bootstrap\cat\MiOS-Cat.bat` (hunks)

Folded from `MiOS-Cat.bat.snippet.txt`.

```bat
EDIT TARGET: C:\mios-bootstrap\cat\MiOS-Cat.bat
Two separate insertions. live-iso.sh itself needs a Linux podman toolchain
(mksquashfs/dracut/xorriso) that a bare Windows install host does not have,
so MiOS-Cat.bat NEVER builds MiOS-Live-Chat.iso itself -- it only STAGES a
pre-built one, exact same "copy-if-present" idiom already used for
fedora_file (936-965), degrading open (warn + continue) rather than failing
the whole USB build, exactly like build_xbox already does when disabled.

------------------------------------------------------------------------------
INSERTION 1 -- SSOT map (extend the existing one-pass PowerShell loader)
------------------------------------------------------------------------------
--------------------------------- OLD (line ~51) ----------------------------
...$map=[ordered]@{ drivepath='drivepath'; medicatver='medicatver'; file='cache_path'; bg_color='bg'; fg_color='fg'; accent_color='accent'; cursor_color='cursor'; success_color='success'; muted_color='muted'; subtle_color='subtle' }...
--------------------------------- NEW ----------------------------------------
...$map=[ordered]@{ drivepath='drivepath'; medicatver='medicatver'; file='cache_path'; bg_color='bg'; fg_color='fg'; accent_color='accent'; cursor_color='cursor'; success_color='success'; muted_color='muted'; subtle_color='subtle'; live_chat_enabled='live_chat_enabled'; live_chat_iso_name='live_chat_iso_name'; live_chat_iso_src='live_chat_iso_src' }...
------------------------------------------------------------------------------
(NOTE: the loader's regex is section-agnostic -- it matches the FIRST
top-level `key = "..."` occurrence anywhere in mios.toml. [cat.live_chat]'s
`enabled`/`iso_name` keys are NOT globally-unique names in mios.toml, so
they must be read under distinct SSOT keys instead. Add these two lines to
mios.toml [cat.live_chat] alongside the block in
mios.toml.cat-live-chat-block.txt so the .bat loader has unique names to
grep, without duplicating the canonical `enabled`/`iso_name` fields
tools/generate-*.py and live-iso.sh already read via mios_toml.py's
dotted-section lookup (which IS section-aware and has no such collision
risk):
    [cat.live_chat]
    ...
    live_chat_enabled  = true                    # .bat-loader alias of `enabled` (dotted-key collision workaround)
    live_chat_iso_name = "MiOS-Live-Chat.iso"     # .bat-loader alias of `iso_name`
    live_chat_iso_src  = "M:\\MiOS-Live-Chat.iso" # where MiOS-Cat.bat looks for a pre-built ISO (just live-chat-iso output, staged to M:\ like cache_path/fedora_file already are)
)

------------------------------------------------------------------------------
INSERTION 2 -- stage the pre-built ISO (after the WIM-servicing block, same
position/idiom as the Xbox ISO build at line ~1268-1289, but a plain
copy-if-present since this artifact is Linux-podman-built, not
Windows-buildable in-line)
------------------------------------------------------------------------------
--------------------------------- OLD (line ~1263-1268) ---------------------
echo Done > "%serviced_marker%"

:skip_wim_servicing


:: 10. Compile the inline live build of MiOS-Xbox ISO directly to the USB drive
if "%build_xbox%" neq "Enabled" goto skip_xbox_build
--------------------------------- NEW ----------------------------------------
echo Done > "%serviced_marker%"

:skip_wim_servicing

:: 9b. Stage the W10 live-chat ISO (built off-host via 'just live-chat-iso' on
:: a Linux/podman builder -- MiOS-Cat.bat only copies the finished artifact,
:: same "M:\ pre-staged input" idiom as fedora_file, degrading open (warn +
:: continue) so a missing live-chat ISO never fails the whole USB build.
if not "%live_chat_enabled%"=="False" (
    if not exist "%live_chat_iso_src%" set "live_chat_iso_src=M:\MiOS-Live-Chat.iso"
    if not "%live_chat_iso_name%"=="" (set "live_chat_iso_name=MiOS-Live-Chat.iso") else (set "live_chat_iso_name=%live_chat_iso_name%")
    if exist "%live_chat_iso_src%" (
        echo Staging W10 live-chat ISO: %live_chat_iso_src% -^> %drivepath%:\Live_Operating_Systems\%live_chat_iso_name%
        copy "%live_chat_iso_src%" "%drivepath%:\Live_Operating_Systems\%live_chat_iso_name%" /Y >nul
        if errorlevel 1 (echo [WARN] Failed to copy the live-chat ISO -- "Chat with MiOS AI" entry will stay hidden ^(media-guarded^).) else (echo [OK] Live-chat ISO staged.)
    ) else (
        echo [WARN] No pre-built live-chat ISO at %live_chat_iso_src% -- run 'just live-chat-iso' on a Linux builder first ^(see automation/build/live-iso.sh^). Skipping; "Chat with MiOS AI" entry will stay hidden ^(media-guarded, no dead menu row^).
    )
)

:: 10. Compile the inline live build of MiOS-Xbox ISO directly to the USB drive
if "%build_xbox%" neq "Enabled" goto skip_xbox_build
------------------------------------------------------------------------------

NOTE on the "if not ... (set A) else (set B)" line above: that ternary-ish
idiom is deliberately AVOIDED elsewhere in this file for the documented
`%errorlevel%`-inside-parens staleness bug (impl-mios-cat-live-boot.md
finding #3/#7/#16) -- write it instead as two plain sequential lines to stay
consistent with that finding's fix:
    if "%live_chat_iso_name%"=="" set "live_chat_iso_name=MiOS-Live-Chat.iso"
(replacing the single ternary line above; kept here as the actually-correct
form a build-agent should apply, not the buggy-idiom illustration.)

The grub entry itself (resources\ventoy\ventoy_grub.cfg) ships via the
EXISTING `xcopy "%maindir%\resources\ventoy" "%drivepath%:\ventoy\"` step
(line 990) -- no new copy step needed there.
```

### `mios.toml` `[cat.live_chat]` block

Folded from `mios.toml.cat-live-chat-block.txt`.

```toml
EDIT TARGET: C:\MiOS\mios.toml  (also mirrored at usr\share\mios\mios.toml per
the [cat] block's own comment: "Canonical SSOT is usr/share/mios/mios.toml
[cat]; this seed mirrors it." -- apply the same insertion to both.)

Insert immediately after the existing [cat.data_partition] block (currently
ends at "min_disk_gb = 512"), before the "# [kargs]" section header comment.

--------------------------------- OLD --------------------------------------
[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# ----------------------------------------------------------------------------
# [kargs] -- Immutable kernel boot arguments, rendered into kargs.d fragments
--------------------------------- NEW --------------------------------------
[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# ----------------------------------------------------------------------------
# [cat.live_chat] -- W10: MiOS-Cat zero-install live-USB-to-AI-chat
# (docs/agy/impl-mios-cat-live-boot.md). Consumed by
# automation/build/live-iso.sh, which projects it into the live ISO's
# /etc/mios/live-chat.conf. `model`/`model_fallback` are short keys into
# [llamacpp].bake_models above -- NOT a third model pin; both must already
# exist in that CSV or the build fails loud rather than silently inventing
# one. `port` mirrors the canonical MIOS_AI_ENDPOINT (hermes = 8642, see
# [network.ports] above / memory mios-ai-endpoint-canonical) -- intentional
# simplification specific to the live/ephemeral profile: in a real install
# :8642 is Hermes fronting multiple backends, but in the live session
# nothing else is running, so llama-server binds the canonical port
# directly.
# ----------------------------------------------------------------------------
[cat.live_chat]
enabled            = true
model              = "lfm2-700m"        # ~600 MB Q4_K_M -- fits any stick, fast on unknown CPU
model_fallback     = "granite-4.1-8b"   # operator step-up (~4.9 GB); same brain as mios-cpu-node
port               = 8642
ctx_size           = 8192
threads            = 0                  # 0 = auto nproc at boot (resolved by mios-live-chat-server wrapper)
iso_name           = "MiOS-Live-Chat.iso"

# ----------------------------------------------------------------------------
# [kargs] -- Immutable kernel boot arguments, rendered into kargs.d fragments
------------------------------------------------------------------------------

Also add these 6 keys to MiOS-Cat.bat's SSOT `$map` (line ~51, inside the
one-pass PowerShell loader) -- see MiOS-Cat.bat.snippet.txt in this same
scratch dir for the exact old->new.
```

## Do-not-build alternates

The second build pass `_landing.md` supersedes: server-side staging split into `live-chat-fetch.sh` plus `config/live-profile/*`, and the Python client variant with its `getty@tty1` override. Kept as documentation only; `live-iso.sh` and the bash client are canonical.

### `automation/build/live-chat-fetch.sh`

Folded from `g1__automation__build__live-chat-fetch.sh`.

```bash
#!/usr/bin/env bash
# AI-hint: Build-time staging for the W10 live-boot AI chat lane -- fetches the
# AI-related: mios.toml [cat.live_chat], [llamacpp].bake_models, mios-cpu-node,
set -euo pipefail

ROOTFS="${1:?usage: live-chat-fetch.sh <exported-rootfs-dir>}"
[[ -d "$ROOTFS" ]] || { echo "[live-chat-fetch] ROOTFS '$ROOTFS' does not exist" >&2; exit 1; }

source "$(dirname "$0")/../lib/common.sh" 2>/dev/null || {
    log() { printf '[live-chat-fetch] %s\n' "$*"; }
    printf '[live-chat-fetch] WARN: lib/common.sh unavailable -- continuing with bare logging\n' >&2
}

BAKE_SPEC="${MIOS_LLAMACPP_BAKE_MODELS:-}"
MODEL_KEY="${MIOS_CAT_LIVE_CHAT_MODEL:-lfm2-700m}"
FALLBACK_KEY="${MIOS_CAT_LIVE_CHAT_MODEL_FALLBACK:-granite-4.1-8b}"
INCLUDE_FALLBACK="${MIOS_CAT_LIVE_CHAT_INCLUDE_FALLBACK:-false}"
PORT="${MIOS_CAT_LIVE_CHAT_PORT:-8642}"
LLAMA_SWAP_IMAGE="${MIOS_CAT_LIVE_CHAT_LLAMA_SWAP_IMAGE:-ghcr.io/mostlygeek/llama-swap:cuda}"

MODELS_DIR="${ROOTFS}/usr/share/mios/live-chat/models"
BIN_PATH="${ROOTFS}/usr/libexec/mios/llama-server"
SBOM_DIR="${ROOTFS}/usr/share/mios/artifacts/sbom"
install -d -m 0755 "$MODELS_DIR" "$(dirname "$BIN_PATH")" "$SBOM_DIR"

if [[ ! -s "$BIN_PATH" ]]; then
    _cid="$(podman create "$LLAMA_SWAP_IMAGE" true)"
    if podman cp "${_cid}:/usr/bin/llama-server" "$BIN_PATH"; then
        chmod 0755 "$BIN_PATH"
        log "[live-chat-fetch] staged llama-server binary from ${LLAMA_SWAP_IMAGE}"
    else
        podman rm -f "$_cid" >/dev/null 2>&1 || true
        echo "[live-chat-fetch] FATAL: could not extract llama-server from ${LLAMA_SWAP_IMAGE}" >&2
        exit 1
    fi
    podman rm -f "$_cid" >/dev/null 2>&1 || true
fi

_lookup() { # _lookup <key> -> prints "repo:file" on stdout, empty if absent
    local want="${1}.gguf" entry dest rest
    IFS=',' read -ra _entries <<< "$BAKE_SPEC"
    for entry in "${_entries[@]}"; do
        entry="$(printf '%s' "$entry" | tr -d '[:space:]')"
        [[ -z "$entry" ]] && continue
        dest="${entry%%=*}"
        rest="${entry#*=}"
        if [[ "$dest" == "$want" ]]; then
            printf '%s' "$rest"
            return 0
        fi
    done
    return 1
}

_fetch_one() { # _fetch_one <key> <required:true|false>
    local key="$1" required="$2" dest="${1}.gguf" spec repo file url sha
    spec="$(_lookup "$key")" || spec=""
    if [[ -z "$spec" ]]; then
        log "[live-chat-fetch] no [llamacpp].bake_models entry for '${dest}'"
        if [[ "$required" == "true" ]]; then
            echo "[live-chat-fetch] FATAL: default model key '${key}' has no [llamacpp].bake_models entry" >&2
            exit 1
        fi
        return 0
    fi
    repo="${spec%%:*}"
    file="${spec#*:}"
    if [[ -s "${MODELS_DIR}/${dest}" ]]; then
        log "[live-chat-fetch] ${dest} already staged"
        return 0
    fi
    url="https://huggingface.co/${repo}/resolve/main/${file}"
    if curl -fL -C - --retry 3 --max-time 1800 -o "${MODELS_DIR}/${dest}.part" "$url" \
       && [[ -s "${MODELS_DIR}/${dest}.part" ]]; then
        mv -f "${MODELS_DIR}/${dest}.part" "${MODELS_DIR}/${dest}"
        log "[live-chat-fetch] staged ${repo}:${file} -> ${dest}"
        sha=""
        command -v sha256sum >/dev/null 2>&1 && sha="$(sha256sum "${MODELS_DIR}/${dest}" | awk '{print $1}')"
        printf '%s\t%s\t%s\t%s\t%s\n' "$dest" "gguf-live-chat" "$repo" "$file" "${sha:-unknown}" >> "${SBOM_DIR}/models.tsv"
        return 0
    fi
    rm -f "${MODELS_DIR}/${dest}.part" 2>/dev/null || true
    if [[ "$required" == "true" ]]; then
        echo "[live-chat-fetch] FATAL: default model '${dest}' failed to download" >&2
        exit 1
    fi
    log "[live-chat-fetch] optional step-up model '${dest}' failed to download"
}

_fetch_one "$MODEL_KEY" "true"
if [[ "$INCLUDE_FALLBACK" == "true" ]]; then
    _fetch_one "$FALLBACK_KEY" "false"
fi

printf '%s.gguf\n' "$MODEL_KEY" > "${MODELS_DIR}/.selected"

_toml_host="${ROOTFS}/etc/mios/mios.toml"
if [[ -f "$_toml_host" ]]; then
    python3 - "$_toml_host" "$PORT" <<'PY'
import re, sys
path, port = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
m = re.search(r'^\[ai\]\s*$', text, re.MULTILINE)
if not m:
    sys.exit(0)  # no [ai] table to patch -- leave file untouched
start = m.end()
nxt = re.search(r'^\[', text[start:], re.MULTILINE)
end = start + nxt.start() if nxt else len(text)
block = text[start:end]
block = re.sub(r'^endpoint\s*=.*$', f'endpoint            = "http://127.0.0.1:{port}/v1"', block, flags=re.MULTILINE)
block = re.sub(r'^model\s*=.*$', 'model               = "mios-live-chat"', block, flags=re.MULTILINE)
block = re.sub(r'^api_key\s*=.*$', 'api_key             = ""', block, flags=re.MULTILINE)
text = text[:start] + block + text[end:]
open(path, "w", encoding="utf-8").write(text)
PY
    log "[live-chat-fetch] patched ${_toml_host} [ai] -> http://127.0.0.1:${PORT}/v1"
else
    echo "[live-chat-fetch] WARN: ${_toml_host} not found in rootfs" >&2
fi

log "[live-chat-fetch] done"$MODELS_DIR" 2>/dev/null | awk '{print $1}') staged in ${MODELS_DIR}"
exit 0
```

### `config/live-profile/usr/lib/systemd/system/mios-live-chat-prep.service`

Folded from `g1__config__live-profile__usr__lib__systemd__system__mios-live-chat-prep.service`.

```ini
# Live-profile-only unit -- shipped ONLY inside MiOS-Live-Chat.iso's exported
# rootfs via automation/build/live-chat-fetch.sh + the config/live-profile
# overlay copy in automation/build/live-iso.sh. A real `bootc install` never
# carries this file.
# /usr/lib/systemd/system/mios-live-chat-prep.service
[Unit]
Description=MiOS live-boot AI chat -- resolve active model selection (SSOT read)
DefaultDependencies=no
After=local-fs.target
Before=mios-live-chat-server.service
ConditionPathExists=/usr/share/mios/live-chat/models

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/libexec/mios/mios-live-chat-select-model
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### `config/live-profile/usr/lib/systemd/system/mios-live-chat-server.service`

Folded from `g1__config__live-profile__usr__lib__systemd__system__mios-live-chat-server.service`.

```ini
# Live-profile-only unit -- shipped ONLY inside MiOS-Live-Chat.iso's exported
# rootfs via automation/build/live-chat-fetch.sh + the config/live-profile
# overlay copy in automation/build/live-iso.sh. A real `bootc install` never
# carries this file (a bundled multi-hundred-MB GGUF has no business on
# every real MiOS disk -- W10 design S1 step 3).
# /usr/lib/systemd/system/mios-live-chat-server.service
[Unit]
Description=MiOS live-boot AI chat server (ephemeral, CPU-only, offline /v1)
After=local-fs.target mios-live-chat-prep.service
Requires=mios-live-chat-prep.service
ConditionPathExists=/usr/libexec/mios/llama-server

[Service]
Type=simple
ExecStart=/usr/libexec/mios/mios-live-chat-serve
Restart=on-failure
RestartSec=2s
TimeoutStartSec=120s

# Structural "offline", not policy (same "structural not policy" spirit as
# the tty1 no-shell boundary in the W10 design's mios-live-chat REPL): this
# server never needs the network at all -- the model is baked in, there is
# no telemetry -- so hardening denies it outright rather than trusting it
# not to try.
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/run
PrivateTmp=yes
IPAddressDeny=any
IPAddressAllow=127.0.0.1

[Install]
WantedBy=multi-user.target
```

### `config/live-profile/usr/libexec/mios/mios-live-chat-select-model`

Folded from `g1__config__live-profile__usr__libexec__mios__mios-live-chat-select-model`.

```text
#!/usr/bin/env bash
# AI-hint: Boot-time oneshot -- re-resolves [cat.live_chat].model against
# AI-related: mios.toml [cat.live_chat], mios-live-chat-serve,
set -euo pipefail

MODELS_DIR="/usr/share/mios/live-chat/models"
HOST_TOML="/etc/mios/mios.toml"
VENDOR_TOML="/usr/share/mios/mios.toml"

_toml_get() { # _toml_get <file> <section> <key>  (prints value, no exit-on-miss)
    [[ -r "$1" ]] || return 1
    awk -v sec="[$2]" -v key="$3" '
        $0 == sec { insec=1; next }
        /^\[/ { insec=0 }
        insec && $0 ~ ("^" key "[ \t]*=") {
            sub(/^[^=]*=[ \t]*"?/, "")
            sub(/"?[ \t]*(#.*)?$/, "")
            print
            exit
        }
    ' "$1"
}

model_key="$(_toml_get "$HOST_TOML" cat.live_chat model 2>/dev/null || true)"
[[ -z "$model_key" ]] && model_key="$(_toml_get "$VENDOR_TOML" cat.live_chat model 2>/dev/null || true)"
[[ -z "$model_key" ]] && model_key="lfm2-700m"

selected="${model_key}.gguf"
if [[ ! -s "${MODELS_DIR}/${selected}" ]]; then
    selected="$(basename "$(ls -1 "${MODELS_DIR}"/*.gguf 2>/dev/null | head -n1)" 2>/dev/null || true)"
fi

if [[ -z "$selected" || ! -s "${MODELS_DIR}/${selected}" ]]; then
    echo "Mios-live-chat-select-model: no GGUF staged under ${MODELS_DIR}" >&2
    exit 1
fi

printf '%s\n' "$selected" > "${MODELS_DIR}/.selected"
```

### `config/live-profile/usr/libexec/mios/mios-live-chat-serve`

Folded from `g1__config__live-profile__usr__libexec__mios__mios-live-chat-serve`.

```text
#!/usr/bin/env bash
# AI-hint: ExecStart for mios-live-chat-server.service -- resolves the
# MIOS_CAT_LIVE_CHAT_PORT. Mirrors mios-cpu-node's Exec line
# AI-related: mios-live-chat-select-model, mios-live-chat-prep.service,
set -euo pipefail

MODELS_DIR="/usr/share/mios/live-chat/models"
BIN="/usr/libexec/mios/llama-server"
PORT="${MIOS_CAT_LIVE_CHAT_PORT:-8642}"
CTX="${MIOS_CAT_LIVE_CHAT_CTX_SIZE:-8192}"
THREADS="${MIOS_CAT_LIVE_CHAT_THREADS:-0}"
[[ "$THREADS" == "0" ]] && THREADS="$(nproc 2>/dev/null || echo 4)"

selected="$(cat "${MODELS_DIR}/.selected" 2>/dev/null || true)"
if [[ -z "$selected" || ! -s "${MODELS_DIR}/${selected}" ]]; then
    selected="$(basename "$(ls -1 "${MODELS_DIR}"/*.gguf 2>/dev/null | head -n1)" 2>/dev/null || true)"
fi
if [[ -z "$selected" || ! -s "${MODELS_DIR}/${selected}" ]]; then
    echo "Mios-live-chat-serve: no staged GGUF found under ${MODELS_DIR}" >&2
    exit 1
fi
if [[ ! -x "$BIN" ]]; then
    echo "Mios-live-chat-serve: ${BIN} missing" >&2
    exit 1
fi

exec "$BIN" \
    --model "${MODELS_DIR}/${selected}" \
    --host 127.0.0.1 --port "${PORT}" \
    --ctx-size "${CTX}" --flash-attn on \
    --cache-type-k q4_0 --cache-type-v q4_0 \
    --parallel 1 --threads "${THREADS}" --n-gpu-layers 0 --jinja \
    --alias mios-live-chat
```

### `mios.toml`

Folded from `g1__mios.toml`.

```toml
# AI-hint: Documentation artifact for MiOS-Cat TOML config.
OLD (mios.toml lines 1526-1532):
[cat.repo_partition]
label              = "MiOS-Repo"

[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

NEW:
[cat.repo_partition]
label              = "MiOS-Repo"

[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# ----------------------------------------------------------------------------
# [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
# Reuses [llamacpp].bake_models -- NO third model pin. model/model_fallback are
# short keys into that CSV (dest filename = "<key>.gguf"). Threaded through
# MiOS-Cat.bat's SSOT-map (see docs/agy/impl-mios-cat-live-boot.md and the W10
# design doc) and consumed by automation/build/live-chat-fetch.sh +
# usr/libexec/mios/mios-live-chat-{select-model,serve}.
# ----------------------------------------------------------------------------
[cat.live_chat]
enabled            = true
model              = "lfm2-700m"        # default: ~small enough for any stick, fast on unknown CPU
model_fallback     = "granite-4.1-8b"   # operator step-up; same lineage as mios-cpu-node's brain
include_fallback   = false               # true bakes ~4.9GB extra onto the ISO -- off by default
port               = 8642                # loopback-only; live-session Law-5 MIOS_AI_ENDPOINT target
ctx_size           = 8192
threads            = 0                   # 0 = auto nproc, resolved at boot by mios-live-chat-serve
iso_name           = "MiOS-Live-Chat.iso"
llama_swap_image   = "ghcr.io/mostlygeek/llama-swap:cuda"  # source of the staged llama-server binary (same lineage as mios-cuda's llamaswap-src stage; CPU-only via --n-gpu-layers 0)
```

### `C:\MiOS\config\artifacts\live-chat-overlay\etc\systemd\system\getty@tty1.service.d\override.conf`

Folded from `g2__C____MiOS__config__artifacts__live-chat-overlay__etc__systemd__system__getty@tty1.service.d__override.conf`.

```ini
systemd instance drop-in for getty@tty1.service. Clears + replaces ExecStart with `-/usr/libexec/mios/mios-live-chat --tty %I`, Type=idle, Restart=always/RestartSec=0, and layers EnvironmentFile=-/etc/mios/install.env + EnvironmentFile=-/etc/mios/live-chat.env (both optional) so the client gets MIOS_COLOR_*/MIOS_LIVE_CHAT_* for free. All other getty@.service directives (TTYPath, StandardInput/Output, TTYReset, etc.) are inherited unchanged from the vendor unit.
```

### `C:\MiOS\config\artifacts\live-chat-overlay\usr\libexec\mios\mios-live-chat`

Folded from `g2__C____MiOS__config__artifacts__live-chat-overlay__usr__libexec__mios__mios-live-chat`.

```text
#!/usr/bin/env python3 — full contents in the message body / scratchpad file. Python3 stdlib-only tty1 chat REPL: reads MIOS_LIVE_CHAT_*/MIOS_COLOR_* env (projected from mios.toml via /etc/mios/install.env + optional /etc/mios/live-chat.env), health-waits on {ENDPOINT}/health, prompts 'User@mios> ', routes @-prefixed lines through a hardcoded verb allowlist (@help/@model/@endpoint/@theme/@reboot/@poweroff — no shell, no arbitrary exec), streams everything else via SSE POST to {ENDPOINT}/v1/chat/completions, and reprograms the Linux VT's 16-color palette via kernel OSC-P codes to mirror mios.toml [colors] ansi_*.
```

## Grounding copies

Excerpts of files in other trees as they stood during the design session (`C:\MiOS` and `C:\mios-bootstrap`), recorded as OLD/ANCHOR edit targets. They are not live copies; the files they quote are authoritative.

### `C:\MiOS\mios.toml`

Folded from `g3__C____MiOS__mios.toml`.

```toml
# AI-hint: Documentation artifact for MiOS TOML config.
ANCHOR: between [cat.data_partition] and the [kargs] section header comment (~line 1529-1533).

--- OLD ---
[cat.repo_partition]
label              = "MiOS-Repo"

[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# ----------------------------------------------------------------------------
# [kargs] -- Immutable kernel boot arguments, rendered into kargs.d fragments

--- NEW ---
[cat.repo_partition]
label              = "MiOS-Repo"

[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
# Both models are keys into [llamacpp].bake_models -- no new model pin here.
# live_chat_port intentionally mirrors [ports].hermes (8642, "canonical /v1"):
# in the live/ephemeral session nothing else is running, so llama-server binds
# the canonical AI port directly instead of going through agent_pipe/hermes.
# Keys are flat/unique (live_chat_*) so MiOS-Cat.bat's single-pass regex SSOT
# loader can pull them without colliding with the many other model=/port=
# keys elsewhere in this file.
[cat.live_chat]
enabled            = true
live_chat_model    = "lfm2-700m"
live_chat_fallback = "granite-4.1-8b"
live_chat_port     = "8642"
live_chat_ctx      = "8192"
live_chat_iso_name = "MiOS-Live-Chat.iso"

# ----------------------------------------------------------------------------
# [kargs] -- Immutable kernel boot arguments, rendered into kargs.d fragments
```

### `C:\MiOS\usr\share\mios\mios.toml`

Folded from `g3__C____MiOS__usr__share__mios__mios.toml`.

```toml
# AI-hint: Documentation artifact for usr/share/mios/mios.toml config.
ANCHOR: end of [cat.data_partition] block (~line 10877-10888, right after the 'models' cross-reference line, which is the last content line of the existing [cat] group).

--- OLD ---
[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# Path override for the Xbox builder script. Empty = auto-resolve from cat/autounattend/.
# Set to an absolute path to point MiOS-Cat at a custom builder location.
xbox_builder       = ""

# Model weights to stage into MiOS-Data/models/ (references [ai].bake_models SSOT;
# MiOS-Cat reads [ai].bake_models directly for the GGUF list and [ai.vllm].bake_model
# for the AWQ weights -- this key is a human-readable cross-reference, not parsed).
models             = "see [ai].bake_models + [ai.vllm].bake_model"

--- NEW ---
[cat.data_partition]
label              = "MiOS-Data"
min_disk_gb        = 512

# Path override for the Xbox builder script. Empty = auto-resolve from cat/autounattend/.
# Set to an absolute path to point MiOS-Cat at a custom builder location.
xbox_builder       = ""

# Model weights to stage into MiOS-Data/models/ (references [llamacpp].bake_models SSOT;
# MiOS-Cat reads [llamacpp].bake_models directly for the GGUF list and [ai.vllm].bake_model
# for the AWQ weights -- this key is a human-readable cross-reference, not parsed).
models             = "see [llamacpp].bake_models + [ai.vllm].bake_model"

# [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
# Live-boot mechanism derives directly from localhost/mios:latest (the same
# bootc OCI image `bootc install` writes to disk) via dracut dmsquash-live --
# same rootfs, same [colors]/SSOT projection, no second OS definition. Both
# models below are keys into [llamacpp].bake_models (line ~5783) -- no new
# model pin here. live_chat_port mirrors [ports].hermes (8642, "canonical
# /v1"): nothing else runs in the live session, so llama-server binds the
# canonical AI port directly instead of going through agent_pipe/hermes.
# Keys are flat/unique (live_chat_*) so MiOS-Cat.bat's single-pass regex SSOT
# loader can read them without colliding with the many other model=/port=
# keys elsewhere in this file (that loader is NOT table-scoped).
[cat.live_chat]
enabled            = true
live_chat_model    = "lfm2-700m"        # ~600MB Q4_K_M -- fits any stick, fast on unknown CPU
live_chat_fallback = "granite-4.1-8b"   # ~4.9GB Q4_K_M -- operator step-up, same lineage as mios-cpu-node
live_chat_port     = "8642"
live_chat_ctx      = "8192"
live_chat_iso_name = "MiOS-Live-Chat.iso"
```

### `C:\mios-bootstrap\cat\MiOS-Cat.bat`

Folded from `g3__C____mios-bootstrap__cat__MiOS-Cat.bat.txt`.

```bat
=====================================================================
HUNK 1 -- add live-chat defaults (anchor: end of the default-vars block, ~line 118-121)
=====================================================================
--- OLD ---
set "partition_label=MiOS-Cat"
set "force_format=Enabled"

:: ------------------------------------------------------------------
--- NEW ---
set "partition_label=MiOS-Cat"
set "force_format=Enabled"

:: W10 -- live-boot AI-chat ISO staging defaults (bootc-live-squashfs).
:: SSOT-overridable via [cat.live_chat] in mios.toml (loaded into the $map
:: below); these are the degrade-open fallbacks if that block is absent.
set "live_chat_enabled=Enabled"
set "live_chat_model=lfm2-700m"
set "live_chat_fallback=granite-4.1-8b"
set "live_chat_port=8642"
set "live_chat_iso_name=MiOS-Live-Chat.iso"

:: ------------------------------------------------------------------

=====================================================================
HUNK 2 -- extend the PowerShell SSOT $map (anchor: inside the single-line
loader command, ~line 51). Only the $map={...} literal changes; everything
else on that (very long) line is untouched.
=====================================================================
--- OLD (substring) ---
$map=[ordered]@{ drivepath='drivepath'; medicatver='medicatver'; file='cache_path'; bg_color='bg'; fg_color='fg'; accent_color='accent'; cursor_color='cursor'; success_color='success'; muted_color='muted'; subtle_color='subtle' };
--- NEW (substring) ---
$map=[ordered]@{ drivepath='drivepath'; medicatver='medicatver'; file='cache_path'; bg_color='bg'; fg_color='fg'; accent_color='accent'; cursor_color='cursor'; success_color='success'; muted_color='muted'; subtle_color='subtle'; live_chat_model='live_chat_model'; live_chat_fallback='live_chat_fallback'; live_chat_port='live_chat_port'; live_chat_iso_name='live_chat_iso_name' };

=====================================================================
HUNK 3 -- :menu Build line (anchor: ~line 151)
=====================================================================
--- OLD ---
echo     2. Build MiOS Images            (OCI . Xbox ISO . all)
--- NEW ---
echo     2. Build MiOS Images            (OCI . Xbox ISO . Live-Chat . all)

=====================================================================
HUNK 4 -- :sub_build menu + routing (anchor: ~line 340-351), renumbers
"Back to Main Menu" from 4 to 5
=====================================================================
--- OLD ---
echo   1. Build MiOS OCI image     : localhost/mios:latest
echo   2. Build MiOS-Xbox ISO      : Windows 11 gaming edition
echo   3. Build ALL artifacts      : OCI + raw/iso/qcow2/vhd/wsl2
echo   4. Back to Main Menu
echo ==========================================================
set "sub_choice="
set /p "sub_choice=Select an option (1-4): "
if "%sub_choice%"=="1" goto build_oci
if "%sub_choice%"=="2" goto build_xbox_iso
if "%sub_choice%"=="3" goto build_all
if "%sub_choice%"=="4" goto menu
goto sub_build
--- NEW ---
echo   1. Build MiOS OCI image     : localhost/mios:latest
echo   2. Build MiOS-Xbox ISO      : Windows 11 gaming edition
echo   3. Build MiOS Live-Chat ISO : bootc-live-squashfs, no-install AI chat
echo   4. Build ALL artifacts      : OCI + raw/iso/qcow2/vhd/wsl2/live-chat
echo   5. Back to Main Menu
echo ==========================================================
set "sub_choice="
set /p "sub_choice=Select an option (1-5): "
if "%sub_choice%"=="1" goto build_oci
if "%sub_choice%"=="2" goto build_xbox_iso
if "%sub_choice%"=="3" goto build_live_chat_iso
if "%sub_choice%"=="4" goto build_all
if "%sub_choice%"=="5" goto menu
goto sub_build

=====================================================================
HUNK 5 -- new :build_live_chat_iso target (anchor: insert between the end
of :build_xbox_iso and the :build_all label, ~line 417-419). Mirrors
:build_oci's env-var-gated call to build-mios.ps1 (MIOS_BUILD_LIVE_CHAT is a
new hook the live-iso.sh/build-driver leg -- design Phase 1 Sec.1/6 item 1,
not yet landed -- must recognize; same forward-declared pattern as the
existing MIOS_SKIP_BIB).
=====================================================================
--- OLD ---
if "%build_rc%"=="0" echo [OK] MiOS-Xbox ISO build finished.
if not "%build_rc%"=="0" echo [WARN] Xbox builder exit code %build_rc% - review the log above; you can re-run this item.
echo.
pause
goto sub_build

:build_all
--- NEW ---
if "%build_rc%"=="0" echo [OK] MiOS-Xbox ISO build finished.
if not "%build_rc%"=="0" echo [WARN] Xbox builder exit code %build_rc% - review the log above; you can re-run this item.
echo.
pause
goto sub_build

:build_live_chat_iso
cls
echo ==========================================================
echo               Build MiOS Live-Chat ISO
echo ==========================================================
echo   Produces : %live_chat_iso_name%  ^(bootc-live-squashfs^)
echo   Source   : localhost/mios:latest  ^(same image bootc install uses -- no drift^)
echo   Model    : %live_chat_model%  ^(operator step-up: %live_chat_fallback%^)
echo   Serves   : 127.0.0.1:%live_chat_port%  ^(bare llama-server, no Quadlet/pod^)
echo   Toolchain: WSL2 + podman + MiOS-DEV builder auto-provisioned
echo              if missing (offline-first from MiOS-Repo, else online)
echo   Time     : 10-25 min once the OCI image is already built
echo ==========================================================
set "confirm="
set /p "confirm=Start the Live-Chat ISO build now? (Y/N): "
if /i not "%confirm%"=="Y" goto sub_build
call :resolve_bootstrap_root
if "%bootstrap_root%"=="" goto build_need_online
if not exist "%bootstrap_root%\build-mios.ps1" goto build_need_online
echo.
echo [BUILD] Driver : %bootstrap_root%\build-mios.ps1
echo [BUILD] Mode   : Live-Chat ISO only (MIOS_BUILD_LIVE_CHAT=1)
echo [BUILD] Progress streams below and/or in the MiOS-DEV window.
echo.
set "MIOS_BUILD_LIVE_CHAT=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%bootstrap_root%\build-mios.ps1" -Unattended
set "build_rc=%errorlevel%"
set "MIOS_BUILD_LIVE_CHAT="
echo.
if "%build_rc%"=="0" echo [OK] Live-Chat ISO build finished. Output: /var/lib/mios/build/output/%live_chat_iso_name% in MiOS-DEV.
if not "%build_rc%"=="0" echo [WARN] Build driver exit code %build_rc% - review the log above; you can re-run this item.
echo.
pause
goto sub_build

:build_all

=====================================================================
HUNK 6 -- :build_all: mention live-chat in the artifact list + gate
MIOS_BUILD_LIVE_CHAT on live_chat_enabled (anchor: ~line 424-446)
=====================================================================
--- OLD ---
echo   Produces : localhost/mios:latest PLUS deployment artifacts
echo              raw - iso - qcow2 - vhd - wsl2 tarball
echo   Where    : /var/lib/mios/build/output inside MiOS-DEV
echo   Requires : a Linux/podman build host - auto-provisioned
echo              (WSL2 + podman + MiOS-DEV) if missing, offline-first
echo   Size/Time: ~30-40 GB, 45-90 min for the full matrix
echo ==========================================================
set "confirm="
set /p "confirm=Build the FULL artifact matrix now? (Y/N): "
if /i not "%confirm%"=="Y" goto sub_build
call :resolve_bootstrap_root
if "%bootstrap_root%"=="" goto build_need_online
if not exist "%bootstrap_root%\build-mios.ps1" goto build_need_online
echo.
echo [BUILD] Driver : %bootstrap_root%\build-mios.ps1
echo [BUILD] Mode   : full matrix (OCI + all [deployment] targets)
echo [BUILD] Progress streams below and/or in the MiOS-DEV window.
echo.
set "MIOS_SKIP_BIB="
powershell -NoProfile -ExecutionPolicy Bypass -File "%bootstrap_root%\build-mios.ps1" -Unattended
set "build_rc=%errorlevel%"
echo.
--- NEW ---
echo   Produces : localhost/mios:latest PLUS deployment artifacts
echo              raw - iso - qcow2 - vhd - wsl2 tarball - live-chat iso
echo   Where    : /var/lib/mios/build/output inside MiOS-DEV
echo   Requires : a Linux/podman build host - auto-provisioned
echo              (WSL2 + podman + MiOS-DEV) if missing, offline-first
echo   Size/Time: ~30-40 GB, 45-90 min for the full matrix
echo ==========================================================
set "confirm="
set /p "confirm=Build the FULL artifact matrix now? (Y/N): "
if /i not "%confirm%"=="Y" goto sub_build
call :resolve_bootstrap_root
if "%bootstrap_root%"=="" goto build_need_online
if not exist "%bootstrap_root%\build-mios.ps1" goto build_need_online
echo.
echo [BUILD] Driver : %bootstrap_root%\build-mios.ps1
echo [BUILD] Mode   : full matrix (OCI + all [deployment] targets)
echo [BUILD] Progress streams below and/or in the MiOS-DEV window.
echo.
set "MIOS_SKIP_BIB="
set "MIOS_BUILD_LIVE_CHAT="
if "%live_chat_enabled%"=="Enabled" set "MIOS_BUILD_LIVE_CHAT=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%bootstrap_root%\build-mios.ps1" -Unattended
set "build_rc=%errorlevel%"
set "MIOS_BUILD_LIVE_CHAT="
echo.

=====================================================================
HUNK 7 -- :manual_about mention (anchor: ~line 634-637)
=====================================================================
--- OLD ---
echo   Build       : builds MiOS images from this machine -
echo                 OCI (localhost/mios:latest), MiOS-Xbox ISO,
echo                 or the full artifact matrix. The toolchain is
echo                 self-provisioned (WSL2 + podman) if missing.
--- NEW ---
echo   Build       : builds MiOS images from this machine -
echo                 OCI (localhost/mios:latest), MiOS-Xbox ISO,
echo                 MiOS-Live-Chat ISO (zero-install AI-chat live boot),
echo                 or the full artifact matrix. The toolchain is
echo                 self-provisioned (WSL2 + podman) if missing.

=====================================================================
HUNK 8 -- :start_install summary block (anchor: ~line 826-827)
=====================================================================
--- OLD ---
echo Build MiOS-Xbox   : %build_xbox%
echo Partition Label   : %partition_label%
--- NEW ---
echo Build MiOS-Xbox   : %build_xbox%
echo Live-Chat ISO     : %live_chat_enabled% (%live_chat_model%, :%live_chat_port%)
echo Partition Label   : %partition_label%

=====================================================================
HUNK 9 -- stage the ISO (anchor: right after the Fedora ISO + kickstart
copy, before the PortableApps theming section, ~line 1067-1073). New
labels (live_chat_disabled/have_src/missing/done) are goto-driven, NOT
nested inside a shared parenthesized block that also sets the var being
tested -- avoids the parse-time-stale-%var% class of bug already present
elsewhere in this file (see disk_check at :run_preflight_checks and the
unmount retry counter).
=====================================================================
--- OLD ---
:: Copy Fedora ISO and Kickstart to Ventoy paths
echo Copying Fedora Server ISO and Kickstart template to USB...
copy "%fedora_file%" "%drivepath%:\Live_Operating_Systems\Fedora-Server.iso" /Y >nul
copy "%maindir%\resources\ventoy\mios-kickstart.cfg" "%drivepath%:\ventoy\mios-kickstart.cfg" /Y >nul


:: Brand the PortableApps Menu to match MiOS
--- NEW ---
:: Copy Fedora ISO and Kickstart to Ventoy paths
echo Copying Fedora Server ISO and Kickstart template to USB...
copy "%fedora_file%" "%drivepath%:\Live_Operating_Systems\Fedora-Server.iso" /Y >nul
copy "%maindir%\resources\ventoy\mios-kickstart.cfg" "%drivepath%:\ventoy\mios-kickstart.cfg" /Y >nul

:: 6c. Stage the W10 live-boot AI-chat ISO (bootc-live-squashfs; the model +
:: the mios-live-chat client are baked into the ISO at build time by
:: automation\build\live-iso.sh -- this step only finds/builds/copies the
:: single artifact). Copy-if-present / build-if-missing, same idiom as the
:: Fedora DVD above. Never blocks the rest of staging if it's unavailable --
:: worst case the "Chat with MiOS AI" menu entry just doesn't appear (its
:: grub entry is `search --file`-guarded, see ventoy_grub.cfg).
set "live_chat_iso_src="
if not "%live_chat_enabled%"=="Enabled" goto live_chat_disabled
echo.
echo Checking for MiOS Live-Chat ISO (%live_chat_iso_name%)...
if exist "%drivepath%:\Live_Operating_Systems\%live_chat_iso_name%" (
    echo [OK] %live_chat_iso_name% already staged on %drivepath%:.
    goto live_chat_done
)
call :resolve_live_chat_iso
if not "%live_chat_iso_src%"=="" goto live_chat_have_src
echo [INFO] %live_chat_iso_name% not found in cache or MiOS-DEV build output.
call :resolve_bootstrap_root
if "%bootstrap_root%"=="" goto live_chat_missing
if not exist "%bootstrap_root%\build-mios.ps1" goto live_chat_missing
echo Building it now via %bootstrap_root%\build-mios.ps1 (MIOS_BUILD_LIVE_CHAT=1)...
echo Model: %live_chat_model%  Port: %live_chat_port%  (operator step-up: %live_chat_fallback%)
set "MIOS_BUILD_LIVE_CHAT=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%bootstrap_root%\build-mios.ps1" -Unattended
set "MIOS_BUILD_LIVE_CHAT="
call :resolve_live_chat_iso
if "%live_chat_iso_src%"=="" goto live_chat_missing

:live_chat_have_src
echo Copying %live_chat_iso_name% to %drivepath%:\Live_Operating_Systems\...
copy "%live_chat_iso_src%" "%drivepath%:\Live_Operating_Systems\%live_chat_iso_name%" /Y >nul
if errorlevel 1 (
    echo [WARN] Copy of %live_chat_iso_name% failed -- the "Chat with MiOS AI" menu entry will not appear. >^&2
    goto live_chat_done
)
echo [OK] %live_chat_iso_name% staged.
echo Recording SBOM hash (sha256, per ADR-0003 -- never hand-pinned in mios.toml)...
powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath '%drivepath%:\Live_Operating_Systems\%live_chat_iso_name%').Hash.ToLower()" > "%drivepath%:\Live_Operating_Systems\%live_chat_iso_name%.sha256" 2>nul
goto live_chat_done

:live_chat_missing
echo [WARN] %live_chat_iso_name% could not be found or built -- the "Chat with MiOS AI" menu entry will not appear. >^&2
echo        Run "Build MiOS Live-Chat ISO" from the Build menu, or place a pre-built copy at %stage_dir%\%live_chat_iso_name%, and re-run Stage USB. >^&2
goto live_chat_done

:live_chat_disabled
echo [INFO] Live-Chat ISO staging disabled ([cat.live_chat] enabled=false / live_chat_enabled=Disabled).

:live_chat_done


:: Brand the PortableApps Menu to match MiOS

NOTE: the two ">^&2" above are the literal escaped form (caret before &) --
MiOS-Cat.bat already uses plain ">&2" unescaped elsewhere (e.g. the Fedora
DVD failure at ~line 961) because those lines sit outside any parenthesized
block; here the two >&2 lines that ARE inside `if errorlevel 1 ( ... )` /
plain sequential context should just use ">&2" too (cmd.exe only requires
the caret-escape for `&` inside a `(...)` block when it would otherwise be
misparsed as a command separator -- since each `echo ... >&2` here is a
whole line inside its own `if` body or a bare sequential line, plain "&"
works; use ">^&2" only if you keep it on a line that shares parens with
other `&`-sensitive syntax). Match whichever the surrounding hunk ends up
using after a quick manual smoke-run.

=====================================================================
HUNK 10 -- Live_Operating_Systems README (anchor: ~line 1135-1138)
=====================================================================
--- OLD ---
(
echo # MiOS-Cat Operating Systems
echo This folder contains the live WinPE recovery image ^(MiOS_PE.wim^) and SystemRescue ISO.
) > "%drivepath%:\Live_Operating_Systems\README.md"
--- NEW ---
(
echo # MiOS-Cat Operating Systems
echo This folder contains the live WinPE recovery image ^(MiOS_PE.wim^), the
echo SystemRescue ISO, and MiOS-Live-Chat.iso -- the W10 zero-install
echo live-USB-to-AI-chat ^(bootc-live-squashfs: boots the actual MiOS bootc
echo image RAM-resident with a bundled model, no changes to this machine^).
) > "%drivepath%:\Live_Operating_Systems\README.md"

=====================================================================
HUNK 11 -- new :resolve_live_chat_iso helper (anchor: insert between the
end of :resolve_xbox_builder and the :update_repo label, ~line 1419-1421)
=====================================================================
--- OLD ---
if exist "C:\mios-bootstrap\cat\autounattend\New-MiOSISO.ps1" (
    set "xbox_builder=C:\mios-bootstrap\cat\autounattend\New-MiOSISO.ps1"
    goto :eof
)
goto :eof

:update_repo
--- NEW ---
if exist "C:\mios-bootstrap\cat\autounattend\New-MiOSISO.ps1" (
    set "xbox_builder=C:\mios-bootstrap\cat\autounattend\New-MiOSISO.ps1"
    goto :eof
)
goto :eof

:resolve_live_chat_iso
:: Resolve a pre-built MiOS-Live-Chat.iso before staging falls back to
:: triggering a build. Preference order: build cache (fast re-run path,
:: mirrors the Fedora/MediCat M:\-style cache) -> MiOS-DEV build output
:: over the WSL2 UNC share -> not found (caller decides build-or-skip).
set "live_chat_iso_src="
if exist "%stage_dir%\%live_chat_iso_name%" (
    set "live_chat_iso_src=%stage_dir%\%live_chat_iso_name%"
    goto :eof
)
if exist "\\wsl.localhost\MiOS-DEV\var\lib\mios\build\output\%live_chat_iso_name%" (
    set "live_chat_iso_src=\\wsl.localhost\MiOS-DEV\var\lib\mios\build\output\%live_chat_iso_name%"
    goto :eof
)
if exist "\\wsl$\MiOS-DEV\var\lib\mios\build\output\%live_chat_iso_name%" (
    set "live_chat_iso_src=\\wsl$\MiOS-DEV\var\lib\mios\build\output\%live_chat_iso_name%"
    goto :eof
)
goto :eof

:update_repo
```

### `C:\mios-bootstrap\cat\resources\ventoy\ventoy_grub.cfg`

Folded from `g3__C____mios-bootstrap__cat__resources__ventoy__ventoy_grub.cfg`.

```text
# =====================================================================
#  MiOS-Cat  --  Ventoy GRUB2 menu (SecureBoot + UEFI/GPT)
#  Logically organized as a MiOS operator would expect:
#    [ Chat ]     -> boot the actual, ephemeral MiOS straight into a live
#                     AI chat TTY -- no install, no changes to this machine
#    [ Deploy ]   -> stand MiOS up on this machine
#    [ Recovery ] -> boot a live tool environment (no changes to host)
#    [ Advanced ] -> firmware / power
#  Every entry is MEDIA-GUARDED (`if search --file`) so it appears ONLY
#  when that image was actually staged onto the USB -- no dead menu rows.
# =====================================================================

# Load USB controller + input modules at the GRUB2 level (prevents keyboard blocking)
insmod usb
insmod uhci
insmod ohci
insmod ehci
insmod xhci
insmod usbkeyboard
insmod fat
insmod chain

# ---------------------------------------------------------------------
#  CHAT  --  boot the live, ephemeral MiOS AI chat (default; no install)
#  Same bootc OCI image (localhost/mios:latest) `bootc install` writes to
#  disk -- booted RAM-resident via dmsquash-live instead. Bundles a local
#  model serving OpenAI /v1 on 127.0.0.1:8642; tty0 lands on User@mios>
#  chat, sudo-disabled/immutable. See [cat.live_chat] in mios.toml.
# ---------------------------------------------------------------------

if search --file --set=root /Live_Operating_Systems/MiOS-Live-Chat.iso; then
menuentry "0. Chat with MiOS AI        [ live -- no install, no changes to this machine ]" --class linux {
    search --set=root --file /Live_Operating_Systems/MiOS-Live-Chat.iso
    chainloader /Live_Operating_Systems/MiOS-Live-Chat.iso
}
fi

# ---------------------------------------------------------------------
#  DEPLOY  --  install / provision MiOS onto this machine
# ---------------------------------------------------------------------

if search --file --set=root /Live_Operating_Systems/Fedora-Server.iso; then
menuentry "Deploy MiOS Linux         [ automated -- mutable Fedora server + MiOS overlay ]" --class fedora {
    search --set=root --file /Live_Operating_Systems/Fedora-Server.iso
    chainloader /Live_Operating_Systems/Fedora-Server.iso
}
fi

if search --file --set=root /Live_Operating_Systems/MiOS-Xbox.iso; then
menuentry "Deploy MiOS-Xbox          [ Windows gaming edition -- boot the built ISO ]" --class warning {
    search --set=root --file /Live_Operating_Systems/MiOS-Xbox.iso
    chainloader /Live_Operating_Systems/MiOS-Xbox.iso
}
fi

if search --file --set=root /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim; then
menuentry "Deploy MiOS-Xbox (WinPE)  [ automated wipe + install driver ]" --class warning {
    set vtoy_wimboot_val="1"
    search --set=root --file /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim
    chainloader /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim
}
fi

# ---------------------------------------------------------------------
#  RECOVERY & TOOLS  --  live environments, no changes to the host
# ---------------------------------------------------------------------

if search --file --set=root /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim; then
menuentry "MiOS-Cat Recovery         [ Mini Windows PE -- rescue / imaging tools ]" --class windows {
    set vtoy_wimboot_val="1"
    search --set=root --file /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim
    chainloader /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim
}
fi

if search --file --set=root /Live_Operating_Systems/SystemRescue/SystemRescue.iso; then
menuentry "SystemRescue              [ Linux diagnostics + disk tools ]" --class linux {
    search --set=root --file /Live_Operating_Systems/SystemRescue/SystemRescue.iso
    chainloader /Live_Operating_Systems/SystemRescue/SystemRescue.iso
}
fi

# ---------------------------------------------------------------------
#  ADVANCED  --  firmware / power
# ---------------------------------------------------------------------

if search --file --set=root /EFI/shell.efi; then
menuentry "UEFI Shell                [ advanced firmware recovery ]" --class recovery {
    terminal_output console
    chainloader /EFI/shell.efi
}
fi

menuentry "Reboot" --class restart {
    reboot
}

menuentry "Power Off" --class shutdown {
    halt
}
```

### `C:\mios-bootstrap\mios.toml`

Folded from `g3__C____mios-bootstrap__mios.toml`.

```toml
# AI-hint: Documentation artifact for mios-bootstrap mios.toml config.
ANCHOR: end of the existing [cat] block (last content line before EOF).

--- OLD ---
[cat]
xbox_builder = ""
build_driver = ""
log_path     = ""

--- NEW ---
[cat]
xbox_builder = ""
build_driver = ""
log_path     = ""

# ----------------------------------------------------------------------------
# [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
# Consumed by MiOS-Cat.bat's live_chat_* staging block + :build_live_chat_iso.
# Both models are keys into [llamacpp].bake_models -- no new model pin here.
# Keys are flat/unique (live_chat_*) so the single-pass regex SSOT loader in
# MiOS-Cat.bat can read them without colliding with the many other model=/
# port= keys elsewhere in this file (the loader is NOT table-scoped -- it
# greps the whole file for the FIRST `key = "value"` match).
# ----------------------------------------------------------------------------
[cat.live_chat]
enabled            = true
live_chat_model    = "lfm2-700m"        # key into [llamacpp].bake_models -- default, fits any stick
live_chat_fallback = "granite-4.1-8b"   # operator step-up, same bake_models CSV
live_chat_port     = "8642"             # = [ports].hermes ("canonical /v1"); llama-server binds it directly in the live/ephemeral session
live_chat_ctx      = "8192"
live_chat_iso_name = "MiOS-Live-Chat.iso"
```
