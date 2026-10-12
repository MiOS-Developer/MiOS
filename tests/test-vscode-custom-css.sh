#!/usr/bin/env bash
# AI-hint: Verification test suite for VS Code and code-server Custom CSS subsystem and edge-to-edge terminal.
# AI-doc: usr/share/doc/mios/manual/ch72-ide-custom-css.md
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL="${ROOT_DIR}/usr/libexec/mios/mios-vscode-custom-css"
VSIX="${ROOT_DIR}/usr/share/mios/extensions/vscode-custom-css-7.5.1.vsix"
EXT_DIR="${ROOT_DIR}/usr/share/mios/extensions/be5invis.vscode-custom-css"
CSS_FILE="${ROOT_DIR}/usr/share/mios/themes/code-server-terminal.css"
SETTINGS_TPL="${ROOT_DIR}/usr/share/mios/agents/code-server-mobile-settings.json"

TMP_DIR=""
cleanup() {
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT
TMP_DIR="$(mktemp -d)"

pass_count=0
fail_count=0

assert_pass() {
    echo "  PASS: $1"
    pass_count=$((pass_count + 1))
}

assert_fail() {
    echo "  FAIL: $1" >&2
    fail_count=$((fail_count + 1))
}

echo "[test-vscode-custom-css] === MiOS VS Code Custom CSS Test Suite ==="

# Test 1: VSIX package verification
echo "[test-vscode-custom-css] Test 1: VSIX package existence and integrity"
if [[ -f "$VSIX" ]]; then
    assert_pass "VSIX archive exists at $VSIX"
else
    assert_fail "VSIX archive missing at $VSIX"
fi

if python3 -c "import zipfile; z = zipfile.ZipFile('$VSIX'); assert 'extension/package.json' in z.namelist()" 2>/dev/null; then
    assert_pass "VSIX is a valid zip archive containing extension/package.json"
else
    assert_fail "VSIX invalid or missing extension/package.json"
fi

# Test 2: Unpacked extension verification
echo "[test-vscode-custom-css] Test 2: Unpacked extension directory structure"
if [[ -f "${EXT_DIR}/package.json" ]]; then
    assert_pass "Unpacked extension package.json exists"
else
    assert_fail "Unpacked extension package.json missing"
fi

if python3 -c "import json; data = json.load(open('${EXT_DIR}/package.json')); assert data.get('version') == '7.5.1'" 2>/dev/null; then
    assert_pass "Extension version matches 7.5.1"
else
    assert_fail "Extension version mismatch or invalid JSON"
fi

# Test 3: Stylesheet verification
echo "[test-vscode-custom-css] Test 3: Edge-to-edge terminal CSS"
if [[ -f "$CSS_FILE" ]]; then
    assert_pass "CSS stylesheet exists at $CSS_FILE"
else
    assert_fail "CSS stylesheet missing at $CSS_FILE"
fi

if grep -q "terminal-outer-container" "$CSS_FILE" && grep -q "padding: 0 !important" "$CSS_FILE"; then
    assert_pass "CSS stylesheet contains terminal-outer-container zero-padding rule"
else
    assert_fail "CSS stylesheet missing required rules"
fi

# Test 4: CLI tool execution
echo "[test-vscode-custom-css] Test 4: CLI tool validation"
if [[ -x "$TOOL" ]]; then
    assert_pass "mios-vscode-custom-css is executable"
else
    assert_fail "mios-vscode-custom-css is not executable"
fi

if "$TOOL" --help >/dev/null 2>&1; then
    assert_pass "mios-vscode-custom-css --help succeeds"
else
    assert_fail "mios-vscode-custom-css --help failed"
fi

if "$TOOL" status >/dev/null 2>&1; then
    assert_pass "mios-vscode-custom-css status succeeds"
else
    assert_fail "mios-vscode-custom-css status failed"
fi

# Test 5: Synthetic functional installation
echo "[test-vscode-custom-css] Test 5: Synthetic functional installation"
SYN_EXT="${TMP_DIR}/test-user/.vscode/extensions"
SYN_SETTINGS="${TMP_DIR}/test-user/.config/Code/User/settings.json"
mkdir -p "$SYN_EXT" "$(dirname "$SYN_SETTINGS")"
echo '{"editor.fontSize": 12}' > "$SYN_SETTINGS"

if "$TOOL" install --extensions-dir "$SYN_EXT" --settings-file "$SYN_SETTINGS" >/dev/null 2>&1; then
    assert_pass "install command succeeded with explicit targets"
else
    assert_fail "install command failed with explicit targets"
fi

if [[ -d "${SYN_EXT}/be5invis.vscode-custom-css" && -f "${SYN_EXT}/be5invis.vscode-custom-css/package.json" ]]; then
    assert_pass "Extension directory properly copied into target extensions folder"
else
    assert_fail "Extension directory missing from target extensions folder"
fi

if [[ -d "${SYN_EXT}/mios-theme-mobile" && -f "${SYN_EXT}/mios-theme-mobile/package.json" ]]; then
    assert_pass "MiOS Mobile theme extension properly copied into target extensions folder"
else
    assert_fail "MiOS Mobile theme extension missing from target extensions folder"
fi

if python3 -c "import json; s = json.load(open('$SYN_SETTINGS')); assert 'vscode_custom_css.imports' in s and s['editor.fontSize'] == 12 and s.get('workbench.colorTheme') == 'MiOS-Dev'" 2>/dev/null; then
    assert_pass "Target settings.json updated with MiOS-Dev theme and preserved existing keys"
else
    assert_fail "Target settings.json missing custom css configuration or theme"
fi

# Test 6: Synthetic workbench HTML patch and unpatch
echo "[test-vscode-custom-css] Test 6: Workbench HTML patching and unpatching"
SYN_HTML="${TMP_DIR}/workbench.html"
cat <<'EOF' > "$SYN_HTML"
<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="Content-Security-Policy" content="default-src 'self';"/>
    <title>Workbench</title>
</head>
<body>
    <div id="workbench"></div>
</body>
</html>
EOF

# The measured code-server 4.139.1 workbench.js anchors the bake rewrites.
printf '%s' 'var L0e=4,fGn=0,vGn=4,Wre=0;function Hre(s){return s.isModernUICompact()?fGn:L0e}function P0e(s){return s.isModernUICompact()?vGn:L0e}class X{get scrollbarWidth(){return this._configurationService.getValue("workbench.experimental.modernUI")===!0?10:14}}' > "${TMP_DIR}/workbench.js"
BAKE_ARGS=(--target "$SYN_HTML" --css "$CSS_FILE" --scrollbar-px 0 --perimeter-px 0)

if "$TOOL" patch "${BAKE_ARGS[@]}" >/dev/null 2>&1; then
    assert_pass "patch command succeeded"
else
    assert_fail "patch command failed"
fi

if grep -q "<!-- !! VSCODE-CUSTOM-CSS-START !!" "$SYN_HTML" && grep -q "terminal-outer-container" "$SYN_HTML" && ! grep -q "file://" "$SYN_HTML"; then
    assert_pass "workbench.html carries the inlined stylesheet and no file:// link"
else
    assert_fail "workbench.html missing the inlined stylesheet, or carries a file:// link"
fi

if "$TOOL" verify "${BAKE_ARGS[@]}" >/dev/null 2>&1; then
    assert_pass "verify accepts the baked workbench"
else
    assert_fail "verify rejected the baked workbench"
fi

if "$TOOL" unpatch --target "$SYN_HTML" >/dev/null 2>&1; then
    assert_pass "unpatch command succeeded"
else
    assert_fail "unpatch command failed"
fi

if ! grep -q "VSCODE-CUSTOM-CSS" "$SYN_HTML"; then
    assert_pass "workbench.html clean after unpatch"
else
    assert_fail "workbench.html still contains patch markers after unpatch"
fi

# Test 7: Mobile settings template check
echo "[test-vscode-custom-css] Test 7: Mobile settings template verification"
if python3 -c "import json; data = json.load(open('$SETTINGS_TPL')); assert 'vscode_custom_css.imports' in data and data.get('workbench.colorTheme') == 'MiOS-Dev'" 2>/dev/null; then
    assert_pass "Mobile settings template contains vscode_custom_css.imports and MiOS-Dev theme"
else
    assert_fail "Mobile settings template missing custom CSS properties or theme"
fi

# Test 8: Mobile theme package integrity
echo "[test-vscode-custom-css] Test 8: MiOS-Dev theme integrity"
THEME_JSON="${ROOT_DIR}/usr/share/mios/extensions/mios-theme-mobile/themes/mios-mobile-theme.json"
THEME_EXT_DIR="${ROOT_DIR}/usr/share/mios/extensions/mios-theme-mobile"
if [[ -f "$THEME_JSON" ]] && python3 -c "import json; d = json.load(open('$THEME_JSON')); assert d['name'] == 'MiOS-Dev' and 'colors' in d and 'tokenColors' in d" 2>/dev/null; then
    assert_pass "Theme JSON exists and contains valid color tokens"
else
    assert_fail "Theme JSON missing or invalid"
fi

if [[ -f "${THEME_EXT_DIR}/package.json" ]] && python3 -c "import json; d = json.load(open('${THEME_EXT_DIR}/package.json')); assert d['name'] == 'mios-theme-mobile' and 'contributes' in d" 2>/dev/null; then
    assert_pass "Theme extension manifest contributes theme properly"
else
    assert_fail "Theme extension manifest missing or invalid"
fi

# Test 9: Edge-to-edge frameless CSS checks
echo "[test-vscode-custom-css] Test 9: Frameless CSS safe area insets and rounded UI"
if grep -q "safe-area-inset-bottom" "$CSS_FILE" && grep -q "safe-area-inset-top" "$CSS_FILE"; then
    assert_pass "Custom CSS includes mobile safe-area insets"
else
    assert_fail "Custom CSS missing safe-area-inset rules"
fi

if grep -q "border-radius" "$CSS_FILE" && grep -q "terminal-outer-container" "$CSS_FILE"; then
    assert_pass "Custom CSS includes rounded floating status bar and zero-border terminal selectors"
else
    assert_fail "Custom CSS missing rounded bottom bar or terminal selectors"
fi

echo "[test-vscode-custom-css] === Test Results: ${pass_count} passed, ${fail_count} failed ==="
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
echo "[test-vscode-custom-css] SUCCESS: All ${pass_count} tests passed (100% success)."
exit 0
