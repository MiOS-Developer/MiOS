#!/usr/bin/env bash
# AI-hint: bash layered mios.toml dotfile. AI-related: /etc/mios/install.env, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/lib/mios/userenv.sh, ...
# AI-doc: usr/share/doc/mios/manual/mios.md
set -euo pipefail

_CALLER_ROOT="${MIOS_ROOT:-}"
_read_security_array() {
    local _sec="$1" _toml="${_CALLER_ROOT}/usr/share/mios/mios.toml"
    [[ -r "$_toml" ]] || _toml="${MIOS_TOML_VENDOR:-/usr/share/mios/mios.toml}"
    [[ -r "$_toml" ]] || _toml="$(dirname "${BASH_SOURCE[0]}")/../../usr/share/mios/mios.toml"
    [[ -r "$_toml" ]] || return 0
    awk -v sec="$_sec" '/^\[/ { _in = (index($0, "[security." sec "]") == 1); next }
        _in && match($0, /"MIOS_[A-Z0-9_]*"/) { print substr($0, RSTART + 1, RLENGTH - 2) }' "$_toml"
}

DRY_RUN=0
SHOW_SOURCE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --show-source) SHOW_SOURCE=1 ;;
        --secret-keys) _read_security_array "secret_keys"; exit 0 ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed -n '/^# /p' | sed 's/^# //'
            exit 0
            ;;
        *) printf 'mios-sync-env: unknown arg %q (try --help)\n' "$arg" >&2; exit 2 ;;
    esac
done

OUT=/etc/mios/install.env

RESOLVER="${_CALLER_ROOT}/usr/lib/mios/userenv.sh"
if [[ ! -r "$RESOLVER" ]]; then
    echo "Mios-sync-env: resolver $RESOLVER not found" >&2
    exit 1
fi
# shellcheck disable=SC1090  # resolver path is a variable by design (vendor/host/user cascade)
. "$RESOLVER"

_ENV_UNSAFE='[[:space:]"'"'"'$`#]'

# Law 10 declares install.env bare KEY=value. Keys that legitimately cannot be bare
# are declared in SSOT [security.non_bare_env]; anything else is fatal (T-1060).
_NON_BARE_OK=",$(_read_security_array "non_bare_env" | tr '\n' ',')"
emit() {
    local _k="$1" _v="$2"
    if [[ "$_v" =~ $_ENV_UNSAFE ]]; then
        if [[ "$_NON_BARE_OK" == *",${_k},"* ]]; then
            printf 'mios-sync-env: skip %s (declared in [security.non_bare_env])\n' "$_k" >&2
            return 0
        fi
        printf 'mios-sync-env: ERROR %s is unsafe for a bare env file and is NOT declared in [security.non_bare_env] -- declare it with a reason, or make the value bare\n' "$_k" >&2
        return 1
    fi
    printf '%s=%s\n' "$_k" "$_v"
}

generate_env() {
    local _ai_backend=""
    cat <<EOF
# secrets are NOT here -- see [security.secret_keys]
EOF
    for layer in \
        "${HOME}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/usr/share/mios/mios.toml"
    do
        if [[ -r "$layer" ]]; then
            echo "#   * $layer"
        fi
    done
    echo ""

    emit MIOS_IDENTITY_USERNAME "${MIOS_IDENTITY_USERNAME:-mios}"
    emit MIOS_IDENTITY_HOSTNAME "${MIOS_IDENTITY_HOSTNAME:-mios}"
    [[ -n "${MIOS_USER_FULLNAME:-}" ]] && emit MIOS_USER_FULLNAME "${MIOS_USER_FULLNAME}"
    [[ -n "${MIOS_IDENTITY_GROUPS:-}" ]]   && emit MIOS_IDENTITY_GROUPS "${MIOS_IDENTITY_GROUPS}"
    emit MIOS_IDENTITY_DEFAULT_PASSWORD "${MIOS_IDENTITY_DEFAULT_PASSWORD:-mios}"

    emit MIOS_AI_ENDPOINT "${MIOS_AI_ENDPOINT:-http://localhost:${MIOS_PORTS_AGENT_PIPE:-8700}/v1}"
    emit MIOS_AI_MODEL "${MIOS_AI_MODEL:-granite4.1:8b}"
    emit MIOS_AI_EMBED_MODEL "${MIOS_AI_EMBED_MODEL:-nomic-embed-text}"
    _ai_backend="${MIOS_AI_BACKEND:-${MIOS_HERMES_BACKEND_URL:-}}"
    _ai_backend="${_ai_backend%/v1}"
    [[ -n "$_ai_backend" ]] && emit MIOS_AI_BACKEND "${_ai_backend}"

    [[ -n "${MIOS_LLAMACPP_ENABLE:-}" ]]      && emit MIOS_LLAMACPP_ENABLE "${MIOS_LLAMACPP_ENABLE}"
    [[ -n "${MIOS_LLAMACPP_SLOT_DIR:-}" ]]    && emit MIOS_LLAMACPP_SLOT_DIR "${MIOS_LLAMACPP_SLOT_DIR}"
    [[ -n "${MIOS_LLAMACPP_MODELS_DIR:-}" ]]  && emit MIOS_LLAMACPP_MODELS_DIR "${MIOS_LLAMACPP_MODELS_DIR}"
    [[ -n "${MIOS_LLAMACPP_CONFIG:-}" ]]      && emit MIOS_LLAMACPP_CONFIG "${MIOS_LLAMACPP_CONFIG}"
    [[ -n "${MIOS_LLAMACPP_BAKE_MODELS:-}" ]] && emit MIOS_LLAMACPP_BAKE_MODELS "${MIOS_LLAMACPP_BAKE_MODELS}"
    [[ -n "${MIOS_VLLM_BAKE_MODEL:-}" ]]      && emit MIOS_VLLM_BAKE_MODEL "${MIOS_VLLM_BAKE_MODEL}"

    [[ -n "${MIOS_CONVERGE_GATEWAY_MODE:-}" ]]                  && emit MIOS_CONVERGE_GATEWAY_MODE "${MIOS_CONVERGE_GATEWAY_MODE}"
    [[ -n "${MIOS_CONVERGE_GATEWAY_QUEUE_MAXSIZE:-}" ]]         && emit MIOS_CONVERGE_GATEWAY_QUEUE_MAXSIZE "${MIOS_CONVERGE_GATEWAY_QUEUE_MAXSIZE}"
    [[ -n "${MIOS_CONVERGE_GATEWAY_WORKER_CONCURRENCY:-}" ]]    && emit MIOS_CONVERGE_GATEWAY_WORKER_CONCURRENCY "${MIOS_CONVERGE_GATEWAY_WORKER_CONCURRENCY}"
    [[ -n "${MIOS_CONVERGE_INFERENCE_VLLM_LORA_ADAPTERS_DIR:-}" ]] && emit MIOS_CONVERGE_INFERENCE_VLLM_LORA_ADAPTERS_DIR "${MIOS_CONVERGE_INFERENCE_VLLM_LORA_ADAPTERS_DIR}"
    [[ -n "${MIOS_CONVERGE_INFERENCE_VLLM_ALLOW_RUNTIME_LORA:-}" ]] && emit MIOS_CONVERGE_INFERENCE_VLLM_ALLOW_RUNTIME_LORA "${MIOS_CONVERGE_INFERENCE_VLLM_ALLOW_RUNTIME_LORA}"
    [[ -n "${MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS:-}" ]] && emit MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS "${MIOS_CONVERGE_INFERENCE_LLAMA_CACHE_REUSE_TOKENS}"
    [[ -n "${MIOS_CONVERGE_INFERENCE_LLAMA_PARALLEL_SLOTS:-}" ]] && emit MIOS_CONVERGE_INFERENCE_LLAMA_PARALLEL_SLOTS "${MIOS_CONVERGE_INFERENCE_LLAMA_PARALLEL_SLOTS}"
    [[ -n "${MIOS_CONVERGE_MEMORY_SQLITE_VEC_ENABLE:-}" ]]      && emit MIOS_CONVERGE_MEMORY_SQLITE_VEC_ENABLE "${MIOS_CONVERGE_MEMORY_SQLITE_VEC_ENABLE}"
    [[ -n "${MIOS_CONVERGE_MEMORY_SCRATCHPAD_DIR:-}" ]]         && emit MIOS_CONVERGE_MEMORY_SCRATCHPAD_DIR "${MIOS_CONVERGE_MEMORY_SCRATCHPAD_DIR}"
    [[ -n "${MIOS_CONVERGE_MEMORY_COLD_EVICT_ENABLE:-}" ]]      && emit MIOS_CONVERGE_MEMORY_COLD_EVICT_ENABLE "${MIOS_CONVERGE_MEMORY_COLD_EVICT_ENABLE}"
    [[ -n "${MIOS_CONVERGE_MEMORY_COLD_STORAGE_DIR:-}" ]]       && emit MIOS_CONVERGE_MEMORY_COLD_STORAGE_DIR "${MIOS_CONVERGE_MEMORY_COLD_STORAGE_DIR}"
    [[ -n "${MIOS_CONVERGE_MEMORY_COLD_RETENTION_DAYS:-}" ]]    && emit MIOS_CONVERGE_MEMORY_COLD_RETENTION_DAYS "${MIOS_CONVERGE_MEMORY_COLD_RETENTION_DAYS}"
    [[ -n "${MIOS_CONVERGE_MEMORY_COLD_ZSTD_LEVEL:-}" ]]        && emit MIOS_CONVERGE_MEMORY_COLD_ZSTD_LEVEL "${MIOS_CONVERGE_MEMORY_COLD_ZSTD_LEVEL}"
    [[ -n "${MIOS_CONVERGE_IMAGE_DISTROLESS_ENABLE:-}" ]]       && emit MIOS_CONVERGE_IMAGE_DISTROLESS_ENABLE "${MIOS_CONVERGE_IMAGE_DISTROLESS_ENABLE}"
    [[ -n "${MIOS_CONVERGE_IMAGE_RECHUNK_ENABLE:-}" ]]          && emit MIOS_CONVERGE_IMAGE_RECHUNK_ENABLE "${MIOS_CONVERGE_IMAGE_RECHUNK_ENABLE}"
    [[ -n "${MIOS_CONVERGE_IMAGE_MCP_POOL_ENABLE:-}" ]]         && emit MIOS_CONVERGE_IMAGE_MCP_POOL_ENABLE "${MIOS_CONVERGE_IMAGE_MCP_POOL_ENABLE}"

    [[ -n "${MIOS_VERITY_ANTIFAB_ENABLE:-}" ]] && emit MIOS_VERITY_ANTIFAB_ENABLE "${MIOS_VERITY_ANTIFAB_ENABLE}"
    [[ -n "${MIOS_VERITY_ANTIFAB_MIN_ENTITIES:-}" ]] && emit MIOS_VERITY_ANTIFAB_MIN_ENTITIES "${MIOS_VERITY_ANTIFAB_MIN_ENTITIES}"
    [[ -n "${MIOS_VERITY_ANTIFAB_GROUND_MIN:-}" ]] && emit MIOS_VERITY_ANTIFAB_GROUND_MIN "${MIOS_VERITY_ANTIFAB_GROUND_MIN}"

    for _fk in MIOS_FRONTIER_ORCH_ENGINE MIOS_FRONTIER_ORCH_MODEL MIOS_FRONTIER_ORCH_EFFORT \
               MIOS_FRONTIER_LANE_A_ENGINE MIOS_FRONTIER_LANE_A_MODEL MIOS_FRONTIER_LANE_A_EFFORT MIOS_FRONTIER_LANE_A_ROLE \
               MIOS_FRONTIER_LANE_B_ENGINE MIOS_FRONTIER_LANE_B_MODEL MIOS_FRONTIER_LANE_B_EFFORT MIOS_FRONTIER_LANE_B_ROLE \
               MIOS_FRONTIER_LANE_B_FALLBACK_ENGINE MIOS_FRONTIER_LANE_B_FALLBACK_MODEL MIOS_FRONTIER_LANE_B_FALLBACK_EFFORT MIOS_FRONTIER_LANE_B_PREFER_FALLBACK \
               MIOS_FRONTIER_CLAUDE_EFFORT_FLAG MIOS_A2O_AGY_EFFORT_FLAG MIOS_A2O_GEMINI_EFFORT_FLAG \
               MIOS_FRONTIER_STREAM_TO_REASONING MIOS_FRONTIER_STREAM_PATH; do
        _fv="${!_fk:-}"
        if [[ -n "$_fv" ]]; then emit ${_fk} "${_fv}"; fi
    done

    [[ -n "${MIOS_SGLANG_SERVED_NAME:-}" ]] && emit MIOS_SGLANG_SERVED_NAME "${MIOS_SGLANG_SERVED_NAME}"
    [[ -n "${MIOS_VLLM_SERVED_NAME:-}" ]]   && emit MIOS_VLLM_SERVED_NAME "${MIOS_VLLM_SERVED_NAME}"
    # ONE heavy lane: either engine ([ai].heavy_engine) serves it on llm_heavy.
    [[ -n "${MIOS_PORTS_LLM_HEAVY:-}" ]] && emit MIOS_AI_HEAVY_ENDPOINT "http://localhost:${MIOS_PORTS_LLM_HEAVY}/v1"

    # Every MIOS_PORT_* the resolver produced, enumerated rather than re-typed.
    # The hand-typed list here named 20 of 46; stage 35 writes all of them and
    # this script replaces that whole file at deploy, so 26 vanished -- one the
    # port hermes-dashboard.service reads bare (T-1067). The three legacy
    # *_PORT aliases carry no prefix, so they stay named.
    _port_names="$( { compgen -v MIOS_PORTS_ || true; compgen -v MIOS_PORT_ || true; } | sort -u )"
    for _pk in ${_port_names} MIOS_PORTS_FORGE_HTTP MIOS_PORTS_FORGE_SSH MIOS_PORTS_SEARXNG; do
        _pv="${!_pk:-}"
        # The canonical namespace also carries category documentation and
        # member lists. Only numeric port bindings belong in this projection.
        [[ "$_pk" == MIOS_PORTS_* && ! "$_pv" =~ ^[0-9]+$ ]] && continue
        if [[ -n "$_pv" ]]; then emit "${_pk}" "${_pv}"; fi
    done

    for _tk in MIOS_TTYD_BASH_SHELL MIOS_TTYD_POWERSHELL_SHELL MIOS_TTYD_BIND MIOS_TTYD_REQUIRE_AUTH MIOS_TTYD_AUTH_USER MIOS_TTYD_AUTH_PASS MIOS_TTYD_SSL_CERT MIOS_TTYD_SSL_KEY MIOS_TTYD_WRITABLE MIOS_TTYD_MAX_CLIENTS MIOS_TTYD_FONT_SIZE; do
        _tv="${!_tk:-}"
        if [[ -n "$_tv" ]]; then emit ${_tk} "${_tv}"; fi
    done

    [[ -n "${MIOS_IMAGE_NAME:-}" ]]   && emit MIOS_IMAGE_NAME "${MIOS_IMAGE_NAME}"
    [[ -n "${MIOS_IMAGE_REF:-}" ]]    && emit MIOS_IMAGE_REF "${MIOS_IMAGE_REF}"
    [[ -n "${MIOS_BRANCH:-}" ]]       && emit MIOS_BRANCH "${MIOS_BRANCH}"
    [[ -n "${MIOS_BASE_IMAGE:-}" ]]   && emit MIOS_BASE_IMAGE "${MIOS_BASE_IMAGE}"

    [[ -n "${MIOS_FORGE_ADMIN_USER:-}" ]]  && emit MIOS_FORGE_ADMIN_USER "${MIOS_FORGE_ADMIN_USER}"
    [[ -n "${MIOS_FORGE_ADMIN_EMAIL:-}" ]] && emit MIOS_FORGE_ADMIN_EMAIL "${MIOS_FORGE_ADMIN_EMAIL}"

    # MIOS_FORGE_ADMIN_PASSWORD and MIOS_GITHUB_TOKEN live only in
    return 0
}

if [[ "$SHOW_SOURCE" -eq 1 ]]; then
    echo "# ===== layered TOML source ====="
    for layer in \
        "${HOME}/.config/mios/mios.toml" \
        "/etc/mios/mios.toml" \
        "/usr/share/mios/mios.toml"
    do
        if [[ -r "$layer" ]]; then
            echo "#"
            cat "$layer"
            echo ""
        fi
    done
    echo "# ===== generated install.env ====="
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    generate_env
    exit 0
fi

TMP="$(mktemp /etc/mios/install.env.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
generate_env > "$TMP"
chown root:root "$TMP" 2>/dev/null || true
chmod 0640 "$TMP"

_st=0
if grep -qE '="' "$TMP"; then
    echo "Mios-sync-env: SELFTEST double-quoted value present:" >&2
    grep -nE '="' "$TMP" >&2; _st=1
fi
_sk_re="^($(_read_security_array "secret_keys" | paste -sd'|' -))="
if [[ "$_sk_re" != "^()=" ]] && grep -qE "$_sk_re" "$TMP"; then
    echo "Mios-sync-env: SELFTEST secret present in install.env:" >&2
    grep -nE "$_sk_re" "$TMP" >&2; _st=1
fi
if ! bash -u -c ". '$TMP'" >/dev/null 2>&1; then
    echo "Mios-sync-env: SELFTEST $OUT does not source clean under 'set -u'" >&2; _st=1
fi
[[ "$_st" -ne 0 ]] && echo "Mios-sync-env: WARN self-test failed" >&2

mv -f "$TMP" "$OUT"
trap - EXIT

echo "Mios-sync-env: regenerated $OUT from layered mios.toml"
