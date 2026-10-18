# AI-hint: Purges stale netsh portproxy entries (0.0.0.0:N to 127.0.0.1:N) that cause Windows to intercept and blackhole local browser requests to MiOS ...
# AI-doc: usr/share/doc/mios/manual/mios.md

$ErrorActionPreference = 'Stop'

# Self-elevate.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '  [*] Not elevated. Re-launching via UAC...' -ForegroundColor Yellow
    $relaunchArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
    Start-Process -FilePath 'pwsh.exe' -ArgumentList $relaunchArgs -Verb RunAs
    return
}

# Dot-source mios-common.ps1 if Get-MiosSsotValue isn't available yet
if (-not (Get-Command Get-MiosSsotValue -ErrorAction SilentlyContinue)) {
    $commonScript = Join-Path $PSScriptRoot '..\..\installation\mios-common.ps1'
    if (Test-Path $commonScript) { . $commonScript }
}

# Last-resort defaults MUST equal mios.toml [ports]; check_ps_port_fallback_ssot
# in 98-drift-checks.sh fails the gate if any literal here drifts from the SSOT.
$portKeys = @(
    @{ Key = 'hermes'; Default = 8720 },
    @{ Key = 'cockpit'; Default = 8110 },
    @{ Key = 'forge_http'; Default = 8400 },
    @{ Key = 'code_server'; Default = 8900 },
    @{ Key = 'open_webui'; Default = 8200 },
    @{ Key = 'searxng'; Default = 8800 },
    @{ Key = 'llm_light'; Default = 8500 },
    @{ Key = 'agent_pipe'; Default = 8700 },
    @{ Key = 'hermes_dashboard'; Default = 8210 }
)

$resolvedPorts = [ordered]@{}
foreach ($pk in $portKeys) {
    $resolvedPorts[$pk.Key] = if (Get-Command Get-MiosSsotValue -ErrorAction SilentlyContinue) {
        [int](Get-MiosSsotValue -Section 'ports' -Key $pk.Key -Default $pk.Default)
    } else {
        $pk.Default
    }
}
$ports = @($resolvedPorts.Values)
# Cockpit speaks TLS; the probe below picks its scheme off this value.
$cockpitPort = $resolvedPorts['cockpit']

Write-Host '--- current portproxy table ---' -ForegroundColor Cyan
& netsh interface portproxy show all

Write-Host ''
Write-Host '--- deleting broken 0.0.0.0:N -> 127.0.0.1:N entries ---' -ForegroundColor Cyan
foreach ($p in $ports) {
    & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$p 2>&1 | Out-Null
    Write-Host ("  [-] removed portproxy 0.0.0.0:{0}" -f $p) -ForegroundColor Green
}

Write-Host ''
Write-Host '--- portproxy table after cleanup ---' -ForegroundColor Cyan
& netsh interface portproxy show all

Write-Host ''
Write-Host '--- testing WSL2 localhost forwarding ---' -ForegroundColor Cyan
foreach ($p in $ports) {
    $scheme = if ($p -eq $cockpitPort) { 'https' } else { 'http' }
    try {
        $r = Invoke-WebRequest -Uri ("${scheme}://localhost:${p}/") -UseBasicParsing -SkipCertificateCheck -TimeoutSec 3 -ErrorAction Stop
        Write-Host ("  [+] {0}://localhost:{1}/ -> {2}" -f $scheme, $p, $r.StatusCode) -ForegroundColor Green
    } catch {
        $code = 'no-response'
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        Write-Host ("  [!] {0}://localhost:{1}/ -> {2}" -f $scheme, $p, $code) -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '  Done. Press any key to close this window.' -ForegroundColor Green
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
