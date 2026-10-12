#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs the unified Hermes-Agent and opencode components into the MiOS agent plane, configuring the shared Python venv, sy...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null  # Resolve the repository or installed FHS logging library.
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh" 2>/dev/null || {
    mios_warn "Lib/common.sh unavailable"
    exit 0
}
source "$(dirname "$0")/lib/packages.sh"

HERMES_REPO="${MIOS_HERMES_AGENT_REPO:-https://github.com/NousResearch/hermes-agent.git}"
HERMES_REF="${MIOS_HERMES_AGENT_REF:-main}"
AGENTS_ROOT=/usr/lib/mios/agents
VENV_ROOT="${MIOS_HERMES_DIR:-${AGENTS_ROOT}/hermes-agent}"   # Hermes vendor code (FHS /usr/lib)
VENV_DIR="${MIOS_HERMES_VENV:-${AGENTS_ROOT}/.venv}"          # shared interpreter (sibling)
BIN_DIR="${VENV_ROOT}/bin"

CONSTRAINTS_FILE="${MIOS_HERMES_CONSTRAINTS:-/usr/share/mios/agents/constraints.txt}"
PIP_CONSTRAINTS_ARG=""
if [[ -f "$CONSTRAINTS_FILE" ]]; then
    PIP_CONSTRAINTS_ARG="-c ${CONSTRAINTS_FILE}"
else
    mios_warn "Pip constraints ${CONSTRAINTS_FILE} absent"
fi

mios_log "Direct install: repo=${HERMES_REPO} ref=${HERMES_REF} constraints=${PIP_CONSTRAINTS_ARG:-none}"

_missing=""
for tool in python3 git; do
    command -v "$tool" >/dev/null 2>&1 || _missing="${_missing} ${tool}"
done
if [[ -n "$_missing" ]]; then
    mios_warn "Missing build tools:${_missing}"
    exit 0
fi

install -d -m 0755 "${AGENTS_ROOT}" "${VENV_ROOT}" "${BIN_DIR}" || { mios_warn "Mkdir ${VENV_ROOT} failed"; exit 0; }

VENV_PY=python3
for _py in python3.13 python3.12 python3.11; do
    command -v "${_py}" >/dev/null 2>&1 && { VENV_PY="${_py}"; break; }
done
mios_log "Venv interpreter: ${VENV_PY})"
if ! "${VENV_PY}" -m venv "${VENV_DIR}" 2>/dev/null; then
    mios_warn "${VENV_PY} -m venv failed"
    exit 0
fi
if [ -x "${VENV_DIR}/bin/${VENV_PY}" ]; then
    ln -sf "${VENV_PY}" "${VENV_DIR}/bin/python3" 2>/dev/null || true
    ln -sf "${VENV_PY}" "${VENV_DIR}/bin/python" 2>/dev/null || true
fi

PIP_OFFLINE_ARGS=""
LOCAL_SOURCE=""
if [ -d "/usr/share/mios/vendored/hermes-agent" ]; then
    mios_log "Found offline vendored hermes-agent directory"
    LOCAL_SOURCE="/usr/share/mios/vendored/hermes-agent"
elif [ -f "/usr/share/mios/vendored/hermes-agent.zip" ]; then
    mios_log "Found offline vendored hermes-agent.zip, extracting"
    unzip -o -q /usr/share/mios/vendored/hermes-agent.zip -d /tmp/hermes-agent-src 2>/dev/null || true
    LOCAL_SOURCE="/tmp/hermes-agent-src/hermes-agent"
elif [ -f "/usr/share/mios/vendored/hermes_agent.whl" ]; then
    mios_log "Found offline vendored hermes_agent.whl"
    LOCAL_SOURCE="/usr/share/mios/vendored/hermes_agent.whl"
elif [ -f "/usr/share/mios/vendored/wheels/hermes_agent.tar.gz" ]; then
    mios_log "Found offline vendored hermes_agent.tar.gz, extracting"
    rm -rf /tmp/hermes-agent-src
    mkdir -p /tmp/hermes-agent-src
    # tar's exit status is not the test: it returns non-zero for benign
    # per-entry warnings while still unpacking the tree. Judge on the result.
    tar -xzf /usr/share/mios/vendored/wheels/hermes_agent.tar.gz \
        -C /tmp/hermes-agent-src 2>/dev/null || true
    # Take whatever single top-level directory the archive unpacked to, rather
    # than hardcoding the upstream's branch-derived name (hermes-agent-main).
    for _cand in /tmp/hermes-agent-src/*/; do
        [ -f "${_cand}pyproject.toml" ] || [ -f "${_cand}setup.py" ] || continue
        LOCAL_SOURCE="${_cand%/}"
        break
    done
    [ -n "$LOCAL_SOURCE" ] \
        || mios_warn "hermes_agent.tar.gz unusable: no python project root after extract"
fi

if [ -n "$LOCAL_SOURCE" ]; then
    PIP_OFFLINE_ARGS="--no-index --find-links=/usr/share/mios/vendored/wheels/"
    INSTALL_TARGET="-e $LOCAL_SOURCE"
else
    # No usable vendored source. That is fine for an online build, but on an
    # offline/air-gapped build the clone below cannot succeed, and failing
    # quietly is what let the broken vendored asset go unnoticed.
    if [[ "${MIOS_ONLINE_BUILD:-0}" != "1" ]]; then
        mios_warn "OFFLINE build found no usable vendored hermes-agent source" \
                  "(checked vendored/hermes-agent{,.zip}, hermes_agent.whl," \
                  "wheels/hermes_agent.tar.gz) -- falling back to a network clone"
    fi
    if [ ! -d "${VENV_ROOT}/.git" ]; then
        mios_log "Cloning ${HERMES_REPO} to ${VENV_ROOT}"
        rm -rf "${VENV_ROOT}"
        git clone --depth 1 --branch "${HERMES_REF}" "${HERMES_REPO}" "${VENV_ROOT}" 2>/dev/null || \
            git clone "${HERMES_REPO}" "${VENV_ROOT}" 2>/dev/null || true
    fi
    INSTALL_TARGET="-e ${VENV_ROOT}"
    if [ -d "/usr/share/mios/vendored/wheels" ]; then
        PIP_OFFLINE_ARGS="--find-links=/usr/share/mios/vendored/wheels/"
        if [[ "${MIOS_ONLINE_BUILD:-0}" != "1" ]]; then
            PIP_OFFLINE_ARGS="--no-index --find-links=/usr/share/mios/vendored/wheels/"
        fi
    fi
fi

"${VENV_DIR}/bin/pip" install --no-input --disable-pip-version-check setuptools wheel 2>/dev/null || true

_REQ_ARGS=()
_REQ_FILES="$(get_package_list_setting ai python_requirements)"
[[ -n "${_REQ_FILES// }" ]] || { mios_err "[packages.ai].python_requirements is empty"; exit 1; }
for _req in ${_REQ_FILES}; do
    _req="/${_req#/}"
    [[ -f "$_req" ]] || { mios_err "Missing agent runtime requirements: $_req"; exit 1; }
    _REQ_ARGS+=(-r "$_req")
done
_venv_pip_ok=""
_PIP_ARGS_ONLINE="${PIP_OFFLINE_ARGS//--no-index/}"
for _venv_attempt in 1 2 3; do
    _pa="${PIP_OFFLINE_ARGS}"
    [ "${_venv_attempt}" -ge 2 ] && _pa="${_PIP_ARGS_ONLINE}"
    if "${VENV_DIR}/bin/pip" install --no-input --disable-pip-version-check --ignore-requires-python --no-build-isolation ${_pa} ${PIP_CONSTRAINTS_ARG} \
            ${INSTALL_TARGET} "${_REQ_ARGS[@]}" \
            aiohttp websockets "discord.py>=2.4,<3" "psycopg[binary]" "firecrawl-py" \
            "smolagents>=1.0.0" "litellm>=1.0.0" "mcp>=2.1.1,<3" 2>&1 | tail -8; then
        _venv_pip_ok=1; break
    fi
    if [ "${_venv_attempt}" -eq 1 ]; then
        mios_warn "Agent-venv offline install incomplete"
    else
        mios_warn "Agent-venv pip install attempt ${_venv_attempt}/3 failed"
    fi
    sleep $((_venv_attempt*8))
done
if [ -z "${_venv_pip_ok}" ]; then
    mios_err "Agent-venv pip install failed after 3 attempts"
    rm -rf "${VENV_DIR}"
    exit 1
fi

if [[ -x "${VENV_DIR}/bin/hermes" ]]; then
    install -d -m 0755 "${BIN_DIR}"
    ln -sf "${VENV_DIR}/bin/hermes" "${BIN_DIR}/hermes"
    mios_ok "Installed ${VENV_DIR}/bin/hermes -> ${BIN_DIR}/hermes"
    record_version "hermes-agent" "${HERMES_REF}" "$(${VENV_DIR}/bin/hermes --version 2>&1 | head -1)" 2>/dev/null || true
else
    mios_err "Pip succeeded but no 'hermes' entry-point in venv"
    exit 1
fi

shopt -s nullglob
for site_packages in "${VENV_DIR}/lib/"python*/site-packages; do
    searxng_dir="${site_packages}/plugins/web/searxng"
    if [[ -d "$searxng_dir" && ! -f "${searxng_dir}/plugin.yaml" ]]; then
        cat > "${searxng_dir}/plugin.yaml" <<YAML
name: searxng
kind: backend
version: ${MIOS_VERSION:-Unknown}
description: SearXNG web-search provider (local self-hosted instance at mios-searxng:8888).
register: plugins.web.searxng:register
YAML
        mios_ok "Seeded ${searxng_dir#${VENV_DIR}/}/plugin.yaml"
    fi
    firecrawl_dir="${site_packages}/plugins/web/firecrawl"
    if [[ -d "$firecrawl_dir" && ! -f "${firecrawl_dir}/plugin.yaml" ]]; then
        cat > "${firecrawl_dir}/plugin.yaml" <<YAML
name: firecrawl
kind: backend
version: ${MIOS_VERSION:-Unknown}
description: Firecrawl web provider (self-hosted at FIRECRAWL_API_URL; DORMANT -- SDK v2 vs container v1 mismatch, web.extract_backend defaults to miosfetch).
register: plugins.web.firecrawl:register
YAML
        mios_ok "Seeded ${firecrawl_dir#${VENV_DIR}/}/plugin.yaml"
    fi
    _mf_src="/usr/share/mios/hermes/plugins/web/miosfetch"
    _mf_dst="${site_packages}/plugins/web/miosfetch"
    if [[ -d "$_mf_src" ]]; then
        install -d "$_mf_dst"
        cp -f "$_mf_src"/provider.py "$_mf_src"/__init__.py "$_mf_src"/plugin.yaml "$_mf_dst"/ 2>/dev/null \
            && mios_ok "Deployed miosfetch plugin -> ${_mf_dst#${VENV_DIR}/}"
    fi
    _wt="${site_packages}/tools/web_tools.py"
    if [[ -f "$_wt" ]] && ! grep -q 'backend == "miosfetch"' "$_wt"; then
        sed -i 's/^    if backend == "exa":/    if backend == "miosfetch":  # MiOS offline direct-fetch provider (stdlib; always usable)\n        return True\n    if backend == "exa":/' "$_wt" \
            && mios_ok "Patched web_tools._is_backend_available for miosfetch"
    fi
done
shopt -u nullglob

if command -v git >/dev/null 2>&1; then
    DASH_TMP=$(mktemp -d)
    if (cd "$DASH_TMP" && git archive --remote="${HERMES_REPO}" "${HERMES_REF}" plugins 2>/dev/null | tar -x 2>/dev/null) ||
       git clone --depth 1 --branch "${HERMES_REF}" --filter=blob:none --sparse "${HERMES_REPO}" "$DASH_TMP/repo" 2>&1 | tail -3 &&
       (cd "$DASH_TMP/repo" && git sparse-checkout set plugins 2>/dev/null) ; then
        DASH_SRC="$DASH_TMP/plugins"
        [[ -d "$DASH_TMP/repo/plugins" ]] && DASH_SRC="$DASH_TMP/repo/plugins"
        shopt -s nullglob
        for site_packages in "${VENV_DIR}/lib/"python*/site-packages; do
            for plugin_name in kanban example-dashboard hermes-achievements; do
                src_dash="$DASH_SRC/$plugin_name/dashboard"
                dst_dash="$site_packages/plugins/$plugin_name/dashboard"
                if [[ -d "$src_dash" && -d "$dst_dash" ]]; then
                    [[ -f "$src_dash/manifest.json" && ! -f "$dst_dash/manifest.json" ]] && \
                        cp "$src_dash/manifest.json" "$dst_dash/manifest.json"
                    if [[ -d "$src_dash/dist" ]]; then
                        mkdir -p "$dst_dash/dist"
                        cp -n "$src_dash/dist/"*.{js,css} "$dst_dash/dist/" 2>/dev/null || true
                    fi
                fi
            done
        done
        shopt -u nullglob
        rm -rf "$DASH_TMP"
        mios_ok "Seeded plugins/{kanban,example-dashboard,hermes-achievements}/dashboard/{manifest.json,dist}"
    else
        mios_warn "Could not fetch upstream dashboard manifests; kanban tab will not appear"
        rm -rf "$DASH_TMP"
    fi
fi

if ! "${VENV_DIR}/bin/python3" -c "import fastapi, uvicorn, ptyprocess" 2>/dev/null; then
    if "${VENV_DIR}/bin/pip" install --no-input --disable-pip-version-check ${PIP_CONSTRAINTS_ARG} \
            "fastapi>=0.110" "uvicorn[standard]>=0.30" "python-multipart" "ptyprocess>=0.7" 2>&1 | tail -3; then
        mios_ok "Installed fastapi + uvicorn + ptyprocess into venv"
    else
        mios_warn "Dashboard runtime deps install failed"
    fi
fi

if ! "${VENV_DIR}/bin/python3" -c "from importlib.metadata import version; assert int(version('mcp').split('.')[0]) >= 2" 2>/dev/null; then
    if "${VENV_DIR}/bin/pip" install --no-input --disable-pip-version-check ${PIP_CONSTRAINTS_ARG} "mcp>=2.1.1,<3" 2>&1 | tail -3; then
        mios_ok "Installed mcp SDK into venv"
    else
        mios_warn "Mcp SDK install failed"
    fi
fi

TOK_BACKEND="${MIOS_TOKENIZER_BACKEND:-}"
TOK_ENCODING="${MIOS_TOKENIZER_ENCODING:-}"
TOK_CACHE_DIR="${MIOS_TOKENIZER_CACHE_DIR:-}"
if [[ "$TOK_BACKEND" == "tiktoken" ]]; then
    if ! "${VENV_DIR}/bin/python3" -c "import tiktoken" 2>/dev/null; then
        if "${VENV_DIR}/bin/pip" install --no-input --disable-pip-version-check ${PIP_CONSTRAINTS_ARG} "tiktoken>=0.7" 2>&1 | tail -3; then
            mios_ok "Installed tiktoken into the agent venv"
        else
            mios_warn "Tiktoken install failed"
        fi
    fi
    if [[ -n "$TOK_ENCODING" && -n "$TOK_CACHE_DIR" ]] && "${VENV_DIR}/bin/python3" -c "import tiktoken" 2>/dev/null; then
        mkdir -p "$TOK_CACHE_DIR" 2>/dev/null || true
        if TIKTOKEN_CACHE_DIR="$TOK_CACHE_DIR" "${VENV_DIR}/bin/python3" \
                -c "import sys, tiktoken; tiktoken.get_encoding(sys.argv[1])" "$TOK_ENCODING" 2>&1 | tail -2; then
            mios_ok "Pre-warmed tiktoken encoding ${TOK_ENCODING} into ${TOK_CACHE_DIR}"
        else
            mios_warn "Tiktoken encoding pre-warm failed"
        fi
    fi
fi

if ! "${VENV_DIR}/bin/python3" -c "import smolagents, litellm" 2>/dev/null; then
    if "${VENV_DIR}/bin/pip" install --no-input --disable-pip-version-check ${PIP_CONSTRAINTS_ARG} \
            "smolagents>=1.0.0" "litellm>=1.0.0" 2>&1 | tail -3; then
        mios_ok "Installed smolagents + litellm into the agent venv"
    else
        mios_warn "Smolagents + litellm install failed"
    fi
fi

_BUILD_SPA() {
    command -v node >/dev/null 2>&1 || { mios_warn "Node not installed"; return 1; }
    command -v npm  >/dev/null 2>&1 || { mios_warn "Npm not installed"; return 1; }
    command -v git  >/dev/null 2>&1 || { mios_warn "Git not installed"; return 1; }

    local spa_tmp
    spa_tmp=$(mktemp -d)
    if ! git clone --depth 1 --branch "${HERMES_REF}" "${HERMES_REPO}" "$spa_tmp/repo" 2>&1 | tail -3 ; then
        mios_warn "SPA: clone failed"
        rm -rf "$spa_tmp"; return 1
    fi

    if ! (cd "$spa_tmp/repo/web" && npm install --ignore-scripts --no-audit --no-fund 2>&1 | tail -3); then
        mios_warn "SPA: npm install failed"
        rm -rf "$spa_tmp"; return 1
    fi

    if ! (cd "$spa_tmp/repo/web" && npm run build 2>&1 | tail -8); then
        mios_warn "SPA: npm run build failed"
        rm -rf "$spa_tmp"; return 1
    fi

    local built="$spa_tmp/repo/hermes_cli/web_dist"
    if [[ ! -f "$built/index.html" ]]; then
        mios_warn "SPA: build produced no index.html at $built"
        rm -rf "$spa_tmp"; return 1
    fi

    local strip_helper="${BASH_SOURCE[0]%/*}/support/hermes-dashboard-strip-externals.py"
    if [[ -x "$strip_helper" || -f "$strip_helper" ]]; then
        if ! python3 "$strip_helper" "$built" ; then
            mios_warn "SPA: strip-externals reported leftover external refs"
            rm -rf "$spa_tmp"; return 1
        fi
    else
        mios_warn "SPA: strip-externals helper missing at $strip_helper"
        rm -rf "$spa_tmp"; return 1
    fi

    shopt -s nullglob
    for site_packages in "${VENV_DIR}/lib/"python*/site-packages; do
        local dst="$site_packages/hermes_cli/web_dist"
        rm -rf "$dst"
        mkdir -p "$(dirname "$dst")"
        cp -r "$built" "$dst"
        mios_ok "SPA: installed $(du -sh "$dst" | cut -f1) to ${dst#${VENV_DIR}/}"
    done
    shopt -u nullglob

    rm -rf "$spa_tmp"
    return 0
}

_BUILD_SPA || mios_warn "Dashboard SPA not built; hermes-dashboard.service will fall back to the web_dist_stub"

_VENDOR_OPENUI="${BASH_SOURCE[0]%/*}/support/mios-vendor-openui.sh"
if [[ -x "$_VENDOR_OPENUI" || -f "$_VENDOR_OPENUI" ]]; then
    bash "$_VENDOR_OPENUI" || mios_warn "OpenUI bundle download failed; render panel will report 'bundle missing' until re-run"
else
    mios_warn "Mios-vendor-openui.sh missing at $_VENDOR_OPENUI"
fi

_PTY_PATCH="${BASH_SOURCE[0]%/*}/support/hermes-dashboard-shell-patch.py"
shopt -s nullglob
for site_packages in "${VENV_DIR}/lib/"python*/site-packages; do
    _ws="$site_packages/hermes_cli/web_server.py"
    if [[ -f "$_ws" && -f "$_PTY_PATCH" ]]; then
        if python3 "$_PTY_PATCH" "$_ws" 2>&1 | tail -2; then
            : # ok; idempotent — re-runs are no-ops once marker present
        else
            mios_warn "HERMES_PTY_SHELL patch failed on $_ws"
        fi
    fi
done
shopt -u nullglob

_BGREVIEW_PATCH="${BASH_SOURCE[0]%/*}/support/hermes-background-review-tools-patch.py"
shopt -s nullglob
for site_packages in "${VENV_DIR}/lib/"python*/site-packages; do
    _br="$site_packages/agent/background_review.py"
    if [[ -f "$_br" && -f "$_BGREVIEW_PATCH" ]]; then
        if python3 "$_BGREVIEW_PATCH" "$_br" 2>&1 | tail -2; then
            : # ok; idempotent -- re-runs are no-ops once marker present
        else
            mios_warn "Background-review tool patch failed on $_br"
        fi
    fi
done
shopt -u nullglob

OPENCODE_VERSION="${MIOS_OPENCODE_VERSION:-latest}"
OPENCODE_INSTALL_URL="${MIOS_OPENCODE_INSTALL_URL:-https://opencode.ai/install}"
OPENCODE_BIN="${MIOS_OPENCODE_BIN:-/usr/lib/mios/agents/opencode/bin/opencode}"
OPENCODE_BIN_DIR="$(dirname "${OPENCODE_BIN}")"
OPENCODE_ROOT="$(dirname "${OPENCODE_BIN_DIR}")"
OPENCODE_CONFIG="${MIOS_OPENCODE_CONFIG:-/etc/mios/opencode/opencode.json}"
OPENCODE_CONFIG_DIR="$(dirname "${OPENCODE_CONFIG}")"
OPENCODE_VENDOR_CONFIG=/usr/share/mios/opencode/opencode.json

mios_log "Opencode phase: version=${OPENCODE_VERSION} root=${OPENCODE_ROOT}"

if [[ -f "${OPENCODE_VENDOR_CONFIG}" ]]; then
    if install -d -m 0750 "${OPENCODE_CONFIG_DIR}" 2>/dev/null; then
        if [[ ! -f "${OPENCODE_CONFIG}" ]]; then
            if install -m 0640 "${OPENCODE_VENDOR_CONFIG}" "${OPENCODE_CONFIG}"; then
                if getent group mios-ai >/dev/null 2>&1; then
                    chgrp mios-ai "${OPENCODE_CONFIG}" 2>/dev/null \
                        && chmod 0640 "${OPENCODE_CONFIG}" 2>/dev/null || true
                fi
                mios_ok "Landed opencode.json -> ${OPENCODE_CONFIG}"
            else
                mios_warn "Failed to land opencode.json into ${OPENCODE_CONFIG}"
            fi
        else
            mios_skip "${OPENCODE_CONFIG} already present (admin override) -- not overwriting"
        fi
    else
        mios_warn "Could not create ${OPENCODE_CONFIG_DIR}"
    fi
else
    mios_warn "Vendored ${OPENCODE_VENDOR_CONFIG} missing"
fi

_oc_missing=""
for tool in curl bash; do
    command -v "$tool" >/dev/null 2>&1 || _oc_missing="${_oc_missing} ${tool}"
done
if [[ -n "$_oc_missing" ]]; then
    mios_warn "Opencode phase: missing tools:${_oc_missing}"
elif [[ -x "${OPENCODE_BIN}" ]]; then
    mios_skip "opencode binary already present at ${OPENCODE_BIN} -- skipping fetch"
elif ! install -d -m 0755 "${OPENCODE_ROOT}" "${OPENCODE_BIN_DIR}" 2>/dev/null; then
    mios_warn "Mkdir ${OPENCODE_ROOT} failed"
elif ! curl -fsSL --max-time 60 "${OPENCODE_INSTALL_URL}" -o /tmp/opencode-install.sh; then
    mios_warn "Could not fetch opencode installer from ${OPENCODE_INSTALL_URL}"
else
    _oc_ver_env=""
    if [[ "${OPENCODE_VERSION}" != "latest" && -n "${OPENCODE_VERSION}" ]]; then
        _oc_ver_env="${OPENCODE_VERSION}"
    fi
    OPENCODE_INSTALL_DIR="${OPENCODE_BIN_DIR}" \
    OPENCODE_VERSION="${_oc_ver_env}" \
        bash /tmp/opencode-install.sh 2>&1 | tail -10 || \
        mios_warn "Opencode installer exited non-zero"
    rm -f /tmp/opencode-install.sh 2>/dev/null || true

    if [[ ! -x "${OPENCODE_BIN}" ]]; then
        for cand in "${OPENCODE_BIN_DIR}/opencode" "${OPENCODE_ROOT}/opencode" \
                    "${HOME:-/root}/.opencode/bin/opencode" /root/.opencode/bin/opencode \
                    /var/home/mios/.opencode/bin/opencode; do
            if [[ -x "$cand" ]]; then
                if [[ "$cand" != "${OPENCODE_BIN}" ]]; then
                    install -d -m 0755 "${OPENCODE_BIN_DIR}"
                    install -m 0755 "$cand" "${OPENCODE_BIN}" \
                        && mios_ok "Copied opencode ${cand} -> ${OPENCODE_BIN}"
                fi
                break
            fi
        done
    fi

    if [[ -x "${OPENCODE_BIN}" ]]; then
        ln -sf "${OPENCODE_BIN}" /usr/local/bin/opencode 2>/dev/null || true
        record_version "opencode" "${OPENCODE_VERSION}" "$("${OPENCODE_BIN}" --version 2>&1 | head -1 || echo unknown)" 2>/dev/null || true
        mios_ok "Installed opencode ${OPENCODE_BIN}"
    else
        mios_warn "Opencode binary not found at ${OPENCODE_BIN} or fallbacks"
    fi
fi

# The global agent CLI catalog ([agent_cli].tools) under [agent_cli].linux_prefix
# with its /usr/bin wrappers -- the payload `mios agents --install` installs. Every
# MiOS image carries it, so the devcontainer and the cloud sessions (which run this
# image) start with every catalogued CLI. A failed install fails the build.
_ac_src="$(cd "$(dirname "$0")/.." && pwd)"
_ac_enabled="$(MIOS_TOML_ROOT="$_ac_src" python3 "$_ac_src/usr/libexec/mios/mios-toml-get" --vendor agent_cli enabled)"
if [[ "$_ac_enabled" == true ]]; then
    mios_log "Install the [agent_cli].tools catalog"
    /usr/lib/mios/mcp/.venv/bin/python3 /usr/libexec/mios/mios-mcp-server --agent-cli --install \
        || { mios_err "Agent CLI catalog install failed"; exit 1; }
else
    mios_skip "[agent_cli].enabled is '${_ac_enabled}'; agent CLIs not installed"
fi

mios_ok "Done"
exit 0
