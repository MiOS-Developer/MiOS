#!/bin/bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Node builder script to pull the zen-browser surfer repository, download the upstream Firefox codebase, apply structural thre...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh" 2>/dev/null || true
source "${SCRIPT_DIR}/lib/packages.sh"
install_packages "ai"

PIN_REF="${MIOS_BUILD_BAKE_REFS_SURFER:-latest}"
[ "$PIN_REF" != latest ] || PIN_REF="$(/usr/libexec/mios/mios-bake-plan latest-git "${MIOS_URL_SURFER:-https://github.com/zen-browser/surfer.git}")" || { echo "surfer: newest ref could not be resolved" >&2; exit 1; }
mios_log "Surfer pin ref: ${PIN_REF}"

git config --global user.email "build@mios.local"  2>/dev/null || true
git config --global user.name  "MiOS Build"        2>/dev/null || true
git config --global init.defaultBranch main        2>/dev/null || true
git config --global advice.detachedHead false      2>/dev/null || true

SURFER_BUILD_DIR="/tmp/surfer-build"
SURFER_OK=""

for attempt in 1 2 3; do
    mios_log "Compilation attempt $attempt/3"
    cd /tmp
    rm -rf "$SURFER_BUILD_DIR"

    if ! git clone "${MIOS_URL_SURFER:-https://github.com/zen-browser/surfer.git}" "$SURFER_BUILD_DIR"; then
        mios_warn "Git clone failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    cd "$SURFER_BUILD_DIR"
    if ! git checkout "$PIN_REF"; then
        mios_warn "Git checkout to $PIN_REF failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    if ! npm install --legacy-peer-deps; then
        mios_warn "Npm install failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    mios_log "Firefox version + surfer.json config"
    export MIOS_SURFER_PRODUCT="${MIOS_SURFER_PRODUCT:-firefox}"
    python3 -c '
import json, os, urllib.request
ff_ver = "153.0"
try:
    req = urllib.request.urlopen("https://product-details.mozilla.org/1.0/firefox_versions.json", timeout=10)
    vdata = json.loads(req.read().decode("utf-8"))
    ff_ver = vdata.get("LATEST_FIREFOX_VERSION") or ff_ver
except Exception:
    pass

p = "surfer.json"
data = {}
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
data["name"] = data.get("name") or os.environ.get("MIOS_SURFER_NAME", "MiOS Webshell")
data["vendor"] = data.get("vendor") or os.environ.get("MIOS_SURFER_VENDOR", "mios")
data["appId"] = data.get("appId") or os.environ.get("MIOS_SURFER_APPID", "os.mios.webshell")
data["binaryName"] = data.get("binaryName") or os.environ.get("MIOS_SURFER_BINARY", "mios-webshell")
_ver = data.get("version")
if not isinstance(_ver, dict):
    _ver = {}
_ver["product"] = os.environ.get("MIOS_SURFER_PRODUCT", "firefox")
_ver["version"] = ff_ver
data["version"] = _ver
for _k in ("buildOptions", "addons", "brands"):
    if not isinstance(data.get(_k), dict):
        data[_k] = {}
if not isinstance(data.get("license"), (dict, str)):
    data["license"] = {}
data["firefoxVersion"] = ff_ver
with open(p, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
'

    mios_log "Fetch upstream Mozilla codebase"
    FF_VER="$(python3 -c 'import json; print(json.load(open("surfer.json")).get("firefoxVersion", "153.0"))' 2>/dev/null || echo '153.0')"
    if ! npx surfer download 2>&1 && \
       ! npx surfer download "$FF_VER" 2>&1; then
        mios_warn "Surfer download failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    mios_log "Browser.xhtml layout patches"
    : "${MIOS_COLORS_BG:=#282262}"
    : "${MIOS_COLORS_ACCENT:=#1A407F}"
    : "${MIOS_COLORS_SUBTLE:=#B7C9D7}"
    # A wrong port baked into browser chrome stays invisible until someone
    # opens the sidebar, so an unresolved SSOT value fails the bake.
    for _v in MIOS_PORTS_AGENT_PIPE MIOS_PORTS_HERMES MIOS_BROWSER_AI_PROVIDER_URL; do
        [ -n "${!_v:-}" ] || { mios_err "${_v} unresolved -- cannot bake browser chrome"; exit 1; }
    done
    cat << EOF > /tmp/browser_xhtml_patch.xml
<!-- Add sidebar panels for navigation cockpit and AI interaction to browser.xhtml -->
<hbox flex="1" id="mios-three-pane-container">
  <vbox id="mios-custom-sidebar" width="220" style="background-color: ${MIOS_COLORS_BG}; border-right: 1px solid ${MIOS_COLORS_SUBTLE};">
    <vbox id="mios-panel-cockpit" flex="1">
      <!-- Nav cockpit & System controls -->
      <button label="Local Dashboard" oncommand="loadURI('http://localhost:${MIOS_PORTS_AGENT_PIPE}/')" />
      <button label="Container Status" oncommand="loadURI('http://localhost:${MIOS_PORTS_HERMES}/v1/cluster/health')" />
    </vbox>
    <hbox id="mios-action-area" style="padding: 10px; border-top: 1px solid ${MIOS_COLORS_SUBTLE};">
      <button id="mios-terminal-trigger" label="Launch Terminal" oncommand="launchTerminalAsync()" style="flex: 1;" />
    </hbox>
  </vbox>
  <splitter id="mios-sidebar-splitter" resizebefore="grow" resizeafter="shrink" class="chromeclass-extrachrome" />
  <vbox id="appcontent" flex="1" />
  <splitter id="mios-ai-splitter" resizebefore="grow" resizeafter="shrink" class="chromeclass-extrachrome" />
  <vbox id="mios-ai-sidebar" width="300" style="background-color: ${MIOS_COLORS_ACCENT};">
    <!-- Local AI agent panel -->
    <browser id="mios-ai-frame" src="${MIOS_BROWSER_AI_PROVIDER_URL}" flex="1" />
  </vbox>
</hbox>
EOF

    if ! npx surfer import /tmp/browser_xhtml_patch.xml; then
        mios_warn "Surfer import patch failed on attempt $attempt"
        sleep $((attempt * 8))
        continue
    fi

    mios_log "Native compilation"
    if npm run build; then
        mkdir -p /usr/lib/mios/webshell
        cp -r dist/* /usr/lib/mios/webshell/ 2>/dev/null || true
        ln -sf /usr/lib/mios/webshell/firefox /usr/bin/mios-webshell 2>/dev/null || true
        if [[ -x /usr/bin/mios-webshell ]]; then
            SURFER_OK=1
            break
        fi
        mios_warn "Surfer CLI built but no browser binary in the bake"
        break
    fi

    mios_warn "Build failed on attempt $attempt"
    sleep $((attempt * 8))
done

if [[ -z "$SURFER_OK" ]]; then
    mios_warn "Mios-webshell not built in the bake"
    exit 0
fi

record_version surfer "$PIN_REF" "https://github.com/zen-browser/surfer/tree/${PIN_REF}"
mios_ok "Installed /usr/lib/mios/webshell/ + symlinked /usr/bin/mios-webshell"
