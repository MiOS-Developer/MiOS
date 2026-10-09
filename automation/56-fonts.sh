#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs Geist and Symbols-Only Nerd Fonts to ensure the MiOS dashboard, oh-my-posh prompt, and TTY surfaces render icons and mono...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

_install_geist_nerd_font() {
    local archive="$1" expected_sha="$2" destination="$3"
    [[ -f "$archive" && "$expected_sha" =~ ^[a-fA-F0-9]{64}$ ]] || {
        mios_err "Geist Nerd SSOT archive or SHA256 is missing/invalid: $archive"
        return 1
    }
    printf '%s  %s\n' "$expected_sha" "$archive" | sha256sum -c - || return 1
    mkdir -p "$destination"
    unzip -o -j -q "$archive" '*.[ot]tf' -d "$destination" || return 1
    [[ -n "$(find "$destination" -maxdepth 1 \( -name '*.ttf' -o -name '*.otf' \) -type f -print -quit)" ]] || {
        mios_err "Geist Nerd archive installed no OpenType/TrueType fonts"
        return 1
    }
}

# The shared service base copies this third family as well as Geist and Symbols.
_font_resolver="${MIOS_RESOLVER_BIN:-/usr/libexec/mios/mios-resolver}"
[[ -x "$_font_resolver" ]] || _font_resolver=/usr/bin/mios-resolver
_font_inputs="$("$_font_resolver" --emit=json | jq -er '.merged.theme.font | [.native_archive,.native_sha256] | select(all(.[]; type == "string" and length > 0)) | @tsv')"
IFS=$'\t' read -r _font_archive _font_sha <<< "$_font_inputs"
_install_geist_nerd_font "/${_font_archive#/}" "$_font_sha" /usr/share/fonts/geist-nerd

mios_log "Installing Geist font family from Vercel"
mkdir -p /usr/share/fonts/geist
if [ -f "/usr/share/mios/vendored/fonts/geist.tar.xz" ]; then
    mios_log "Found offline vendored geist.tar.xz, extracting"
    mkdir -p /tmp/geist-font
    tar -xf "/usr/share/mios/vendored/fonts/geist.tar.xz" -C /tmp/geist-font 2>/dev/null || true
elif [ -f "/usr/share/mios/vendored/geist-font.zip" ]; then
    mios_log "Found offline vendored geist-font.zip, extracting"
    mkdir -p /tmp/geist-font
    unzip -o -q /usr/share/mios/vendored/geist-font.zip -d /tmp/geist-font 2>/dev/null || true
elif [ -d "/usr/share/mios/vendored/geist-font" ]; then
    mios_log "Found offline vendored geist-font directory, copying"
    cp -a /usr/share/mios/vendored/geist-font /tmp/geist-font
else
    git clone --depth=1 --single-branch -c http.lowSpeedLimit=1 -c http.lowSpeedTime=20 \
        https://github.com/vercel/geist-font.git /tmp/geist-font 2>/dev/null || true
fi

if [ -d /tmp/geist-font ]; then
    find /tmp/geist-font \( -name "*.otf" -o -name "*.ttf" \) \
        -exec cp -t /usr/share/fonts/geist/ {} + 2>/dev/null || true
    rm -rf /tmp/geist-font
    record_version geist-font "git-main" "https://github.com/vercel/geist-font"
fi

mios_log "Installing Symbols-Only Nerd Font"
mkdir -p /usr/share/fonts/nerd-symbols
NERD_TAG=$( (scurl -s https://api.github.com/repos/ryanoasis/nerd-fonts/releases/latest \
            | grep -Po '"tag_name": "\K.*?(?=")') 2>/dev/null || true)
NERD_FALLBACK_TAG="v3.4.0"
if [ -z "$NERD_TAG" ]; then
    mios_warn "Api.github.com release-tag lookup empty"
    NERD_TAG="$NERD_FALLBACK_TAG"
fi
record_version nerd-symbols-font "$NERD_TAG" \
    "https://github.com/ryanoasis/nerd-fonts/releases/tag/${NERD_TAG}"

if command -v unzip >/dev/null 2>&1; then
    NERD_URL="https://github.com/ryanoasis/nerd-fonts/releases/download/${NERD_TAG}/NerdFontsSymbolsOnly.zip"
    download_ok=false
    if [ -f "/usr/share/mios/vendored/fonts/nerd.tar.xz" ]; then
        mios_log "Found offline vendored nerd.tar.xz, using it"
        tar -xf "/usr/share/mios/vendored/fonts/nerd.tar.xz" -C /usr/share/fonts/nerd-symbols 2>/dev/null || true
        download_ok=true
    elif [ -f "/usr/share/mios/vendored/NerdFontsSymbolsOnly.zip" ]; then
        mios_log "Found offline vendored NerdFontsSymbolsOnly.zip, using it"
        cp /usr/share/mios/vendored/NerdFontsSymbolsOnly.zip /tmp/nerd-symbols.zip
        download_ok=true
    elif [ -f "/usr/share/mios/vendored/nerd-symbols.zip" ]; then
        mios_log "Found offline vendored nerd-symbols.zip, using it"
        cp /usr/share/mios/vendored/nerd-symbols.zip /tmp/nerd-symbols.zip
        download_ok=true
    elif scurl -fsL --max-time 90 "$NERD_URL" -o /tmp/nerd-symbols.zip 2>/dev/null; then
        download_ok=true
    fi

    if [ "$download_ok" = true ]; then
        if [ -f /tmp/nerd-symbols.zip ]; then
            unzip -o -q /tmp/nerd-symbols.zip "*.ttf" "*.otf" -d /usr/share/fonts/nerd-symbols 2>/dev/null || true
        fi

        sbom_dir="/usr/share/mios/artifacts/sbom"
        mkdir -p "$sbom_dir"
        sha=""
        if command -v sha256sum >/dev/null 2>&1; then
            for _asset in /tmp/nerd-symbols.zip /usr/share/mios/vendored/fonts/nerd.tar.xz; do
                if [ -f "$_asset" ]; then
                    sha="$(sha256sum "$_asset" | awk '{print $1}')"
                    break
                fi
            done
        fi
        printf '%s\t%s\t%s\n' "NerdFontsSymbolsOnly" "${NERD_TAG}" "${sha:-unknown}" >> "${sbom_dir}/binaries.tsv"

        rm -f /tmp/nerd-symbols.zip
        mios_ok "Symbols-Only Nerd Font ${NERD_TAG} installed"
    else
        mios_warn "Symbols-Only Nerd Font download failed"
    fi
else
    mios_warn "Unzip unavailable"
fi

fc-cache -f /usr/share/fonts/geist /usr/share/fonts/nerd-symbols 2>/dev/null || true

mios_ok "Done"
