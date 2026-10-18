#!/usr/bin/env bash
# AI-hint: Hermetic two-sided test for mios-ssh-setup.ps1 floating OpenSSH: with the forge mocked (Invoke-RestMethod / Invoke-WebRequest shadowed, no network namespace) the latest release is resolved, its asset proven by the GitHub-published sha256 and the tag/sha256 recorded; a mismatching archive or a release without a digest throws before anything is installed.
# AI-related: usr/share/mios/windows/mios-ssh-setup.ps1, usr/share/mios/mios.toml [ssh.windows]
set -euo pipefail
ROOT="${MIOS_TEST_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ "${MIOS_SSH_TEST_NS:-}" != 1 ]]; then
    exec env MIOS_SSH_TEST_NS=1 unshare --user --map-root-user --net bash "${BASH_SOURCE[0]}" "$@"
fi
command -v pwsh >/dev/null || { echo "FAIL pwsh is required" >&2; exit 1; }
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
printf 'zip-payload-%s\n' "$RANDOM" > "$T/OpenSSH-Win64.zip"
good="$(sha256sum "$T/OpenSSH-Win64.zip" | cut -d' ' -f1)"
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyOnly test@mios' > "$T/keys"
# The fragment's [ssh.windows] keys, as the settings JSON the installer receives.
cat > "$T/settings.json" <<EOF
{"port": 22, "windows": {"root": "$T/root", "install_relative": "usr/lib/openssh", "config_relative": "etc/ssh",
 "release_api": "https://api.github.com/repos/PowerShell/Win32-OpenSSH/releases/latest", "asset": "OpenSSH-Win64.zip",
 "listen_address": "0.0.0.0", "remote_addresses": ["LocalSubnet"]}}
EOF
release() {  # DIGEST
    printf '{"tag_name":"v10.1.0.0p1","assets":[{"name":"OpenSSH-Win32.zip","digest":"sha256:%s","browser_download_url":"https://github.com/PowerShell/Win32-OpenSSH/releases/download/v10.1.0.0p1/OpenSSH-Win32.zip"},{"name":"OpenSSH-Win64.zip"%s,"browser_download_url":"https://github.com/PowerShell/Win32-OpenSSH/releases/download/v10.1.0.0p1/OpenSSH-Win64.zip"}]}' \
        "$(printf 0%.0s {1..64})" "$1" > "$T/release.json"
}
run() {
    MOCK_RELEASE="$T/release.json" MOCK_ASSET="$T/OpenSSH-Win64.zip" MOCK_LOG="$T/dl.log" \
    pwsh -NoProfile -NonInteractive -Command "
        function Invoke-RestMethod { param(\$Uri,\$Headers,[switch]\$UseBasicParsing) Add-Content \$env:MOCK_LOG \"api \$Uri\"; Get-Content -Raw \$env:MOCK_RELEASE | ConvertFrom-Json }
        function Invoke-WebRequest { param(\$Uri,\$OutFile,[switch]\$UseBasicParsing) Add-Content \$env:MOCK_LOG \"get \$Uri\"; Copy-Item \$env:MOCK_ASSET \$OutFile }
        try { & '$ROOT/usr/share/mios/windows/mios-ssh-setup.ps1' -SettingsPath '$T/settings.json' -AuthorizedKeysPath '$T/keys' -StageOnlyProvenancePath '$T/provenance.json'; exit 0 }
        catch { Write-Output \"THROWN: \$_\"; exit 9 }"
}

release ",\"digest\":\"sha256:$good\""
run > "$T/out" 2>&1 || { cat "$T/out" >&2; echo "FAIL positive run" >&2; exit 1; }
jq -e --arg s "$good" '.tag == "v10.1.0.0p1" and .sha256 == $s and (.url | endswith("/v10.1.0.0p1/OpenSSH-Win64.zip"))' "$T/provenance.json" >/dev/null \
    || { cat "$T/provenance.json" >&2; echo "FAIL provenance" >&2; exit 1; }
grep -q '^get https://github.com/PowerShell/Win32-OpenSSH/releases/download/v10.1.0.0p1/OpenSSH-Win64.zip$' "$T/dl.log" \
    || { echo "FAIL not the resolved asset URL" >&2; exit 1; }
echo "PASS OpenSSH latest (v10.1.0.0p1) resolved, GitHub-digest verified, tag/sha256 recorded"

rm -f "$T/provenance.json"
release ",\"digest\":\"sha256:$(printf 1%.0s {1..64})\""
if run > "$T/out" 2>&1; then echo "FAIL mismatching archive accepted" >&2; exit 1; fi
grep -q 'checksum mismatch for v10.1.0.0p1' "$T/out" && [[ ! -e "$T/provenance.json" && ! -e "$T/root/usr/lib/openssh/sshd.exe" ]] \
    || { cat "$T/out" >&2; echo "FAIL mismatch not reported" >&2; exit 1; }
echo "PASS OpenSSH digest mismatch throws before anything is installed"

: > "$T/dl.log"
release ""
if run > "$T/out" 2>&1; then echo "FAIL release without digest accepted" >&2; exit 1; fi
grep -q 'publishes no sha256' "$T/out" && ! grep -q '^get ' "$T/dl.log" \
    || { cat "$T/out" >&2; echo "FAIL missing digest not refused" >&2; exit 1; }
echo "PASS OpenSSH release without a published digest is refused before download"
