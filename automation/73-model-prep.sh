#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: MiOS AI model-weight bake for BOTH local /v1 lanes -- llama.cpp GGUFs and the vLLM snapshot.
# AI-doc: usr/share/doc/mios/manual/automation.md

# AI-hint: Bakes GGUF weights into /usr/share/mios/llamacpp/models based on MIOS_LLAMACPP_BAKE_MODELS config to enable the offline mios-llm-light lane; agents use this to ens...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh" 2>/dev/null || {
    mios_warn "Lib/common.sh unavailable"
    exit 0
}

SPEC="${MIOS_LLAMACPP_BAKE_MODELS:-}"
SEED_DIR="${MIOS_LLAMACPP_MODELS_DIR:?SSOT models directory unresolved}"
install -d -m 0755 "$(dirname "$SEED_DIR")"

if [[ -z "$SPEC" ]]; then
    mios_log "MIOS_LLAMACPP_BAKE_MODELS empty"
    rm -rf "$SEED_DIR" 2>/dev/null || true
    ln -sf /var/lib/mios/llamacpp/models "$SEED_DIR"
    exit 0
fi

if [[ -L "$SEED_DIR" ]]; then
    rm -f "$SEED_DIR"
fi
install -d -m 0755 "$SEED_DIR"
rm -f "${SEED_DIR}/.ready"

baked=0
requested=0
IFS=',' read -ra _entries <<< "$SPEC"
for entry in "${_entries[@]}"; do
    entry="$(printf '%s' "$entry" | tr -d '[:space:]')"
    [[ -z "$entry" ]] && continue
    requested=$((requested + 1))
    dest="${entry%%=*}"
    rest="${entry#*=}"
    repo="${rest%%:*}"
    file="${rest#*:}"
    if [[ -z "$dest" || -z "$repo" || -z "$file" || "$dest" == "$entry" || "$repo" == "$rest" ]]; then
        mios_warn "Malformed entry '${entry}'"
        continue
    fi
    if [[ -s "${SEED_DIR}/${dest}" ]]; then
        mios_skip "${dest} already present"
        baked=$((baked + 1))
        continue
    fi
    _url="https://huggingface.co/${repo}/resolve/main/${file}"
    if [ -f "/usr/share/mios/vendored/models/${dest}" ]; then
        mios_log "Found offline vendored model ${dest}, using it"
        cp "/usr/share/mios/vendored/models/${dest}" "${SEED_DIR}/${dest}"
        baked=$((baked + 1))
    elif curl -fL -C - --retry 3 --max-time 1800 \
            -o "${SEED_DIR}/${dest}.part" "$_url" \
       && [[ -s "${SEED_DIR}/${dest}.part" ]]; then
        mv -f "${SEED_DIR}/${dest}.part" "${SEED_DIR}/${dest}"
        baked=$((baked + 1))
        mios_ok "Baked ${repo}:${file} -> ${dest}"

        sbom_dir="/usr/share/mios/artifacts/sbom"
        mkdir -p "$sbom_dir"
        sha=""
        if command -v sha256sum >/dev/null 2>&1; then
            sha="$(sha256sum "${SEED_DIR}/${dest}" | awk '{print $1}')"
        fi
        printf '%s\t%s\t%s\t%s\t%s\n' "$dest" "gguf" "$repo" "$file" "${sha:-unknown}" >> "${sbom_dir}/models.tsv"
    else
        rm -f "${SEED_DIR}/${dest}.part" 2>/dev/null || true
        mios_warn "Download failed for ${repo}:${file}"
    fi
done

if [[ "$requested" -gt 0 && "$baked" -eq "$requested" ]]; then
    : > "${SEED_DIR}/.ready"   # the quadlet's ConditionPathExists gate -> lane eligible
    seed_size="$(du -sh "$SEED_DIR" 2>/dev/null | awk '{print $1}')"
    mios_ok "Baked ${baked} GGUF -> ${SEED_DIR} (${seed_size:-?}); .ready set"
else
    mios_err "Incomplete GGUF bake: ${baked}/${requested}; required model files missing, .ready withheld"
    exit 1
fi

# AI-hint: Bakes vLLM model weights into the image at /usr/share/mios/vllm/model if MIOS_VLLM_BAKE_MODEL is set, enabling offline serving via the mios-llm-heavy Quadlet (vLLM or SGLang per [ai].heavy_engine) for air-gapped environments.
# AI-related: /usr/share/mios/vllm/model, mios-llm-heavy, mios-grounding, mios-llm-heavy.container

MODEL="${MIOS_VLLM_BAKE_MODEL:-}"
SEED_DIR="/usr/share/mios/vllm/model"
install -d -m 0755 "$(dirname "$SEED_DIR")"

if [[ -z "$MODEL" ]]; then
    mios_log "MIOS_VLLM_BAKE_MODEL empty"
    rm -rf "$SEED_DIR" 2>/dev/null || true
    ln -sf /var/lib/mios/vllm/model "$SEED_DIR"
else

if [[ -L "$SEED_DIR" ]]; then
    rm -f "$SEED_DIR"
fi
if [[ -d "$SEED_DIR" ]] && [[ -n "$(ls -A "$SEED_DIR" 2>/dev/null)" ]]; then
    mios_skip "seed already present at ${SEED_DIR}"
    exit 0
fi

install -d -m 0755 "$SEED_DIR"

if ! python3 - "$MODEL" "$SEED_DIR" <<'PY'
import sys, os
os.makedirs("/usr/local/lib", exist_ok=True)
try:
    from huggingface_hub import snapshot_download
except Exception:
    import subprocess
    os.makedirs("/tmp/hf_hub", exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", "/tmp/hf_hub",
                    "huggingface_hub"], check=False)
    sys.path.insert(0, "/tmp/hf_hub")
    import importlib
    importlib.invalidate_caches()
    from huggingface_hub import snapshot_download
model, dest = sys.argv[1], sys.argv[2]
snapshot_download(repo_id=model, local_dir=dest,
                  ignore_patterns=["*.pth", "original/*", "*.gguf"])
print(f"baked {model} -> {dest}")
PY
then
    mios_warn "Download failed"
    exit 1
fi

sbom_dir="/usr/share/mios/artifacts/sbom"
mkdir -p "$sbom_dir"
sbom_file="${sbom_dir}/models.tsv"
if [[ -d "$SEED_DIR" ]]; then
    find "$SEED_DIR" -type f | while read -r filepath; do
        relpath="${filepath#$SEED_DIR/}"
        sha=""
        if command -v sha256sum >/dev/null 2>&1; then
            sha="$(sha256sum "$filepath" | awk '{print $1}')"
        fi
        printf '%s\t%s\t%s\t%s\t%s\n' "$relpath" "safetensors" "$MODEL" "$relpath" "${sha:-unknown}" >> "$sbom_file"
    done
fi

seed_size="$(du -sh "$SEED_DIR" 2>/dev/null | awk '{print $1}')"
mios_ok "Baked ${MODEL} -> ${SEED_DIR} (${seed_size:-?})"
fi
exit 0
