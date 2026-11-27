# AI-hint: Re-applies MiOS-DEV's Windows-host LAN exposure on demand by running build-mios.ps1 Phase 4's own entry point, Invoke-MiosLanExposure, from the build-mios.ps1 beside it: run as the operator, it measures drift and asks UAC only for Phase 4's worker, then removes what older releases left on retired ports.
# AI-doc: usr/share/doc/mios/manual/mios.md
# AI-related: build-mios.ps1, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-lan-status

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Run as the operator, not elevated: Phase 4 reads the operator's mios.toml layers and WSL VM address with this token
# and hands them to its own UAC worker. An elevated token sees neither (another account's layers, no WSL distro).
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# One implementation: build-mios.ps1 Phase 4, read once from this tree's root (the repo root, which IS the system root).
# Its top-level functions are defined, which runs nothing; of its statements only the layer cache and
# [bootstrap].dev_distro run, then Phase 4's own call. Without that file nothing changes.
$canonical = Join-Path $PSScriptRoot '..\..\..\build-mios.ps1'
if (-not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
    throw "build-mios.ps1 is not at $canonical; nothing was changed. The Windows-host exposure is build-mios.ps1 Phase 4's (mios.toml [firewall.windows]): run this helper from a MiOS tree that carries it, or re-run the bootstrap."
}
$canonical = (Resolve-Path -LiteralPath $canonical).ProviderPath
$source = [IO.File]::ReadAllText($canonical)
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "$canonical does not parse; nothing was changed." }
$top = @($ast.EndBlock.Statements)
$lifted = @($top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] } | ForEach-Object { $_.Extent.Text })
foreach ($name in @('Invoke-MiosLanExposure', 'Invoke-MiosElevatedLanExposure', 'Get-MiosLanExposureWorkerScript')) {
    if (-not @($top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $name }).Count) {
        throw "$canonical has no top-level function $name; nothing was changed."
    }
}
foreach ($var in @('$script:_MiosTomlCache', '$DevDistro')) {
    $st = @($top | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq $var })
    if ($st.Count -ne 1) { throw "$canonical has no single top-level $var assignment; nothing was changed." }
    $lifted += $st[0].Extent.Text
}
# build-mios.ps1 runs without StrictMode, so its statements and steps do here too. Its worker is compiled from the
# same text that was parsed here, and this token's elevation is the one Phase 4 branches on.
Set-StrictMode -Off
. ([scriptblock]::Create($lifted -join "`n`n"))
$script:MiosBuildSource = $source
$script:IsAdmin = $isAdmin
Set-StrictMode -Version Latest

# Console loggers in place of build-mios.ps1's log-file and dashboard ones; the lifted steps call them by these names.
function Write-Log([string]$M, [string]$L = 'INFO') { Write-Host "  [.] $M" -ForegroundColor DarkGray }
function Log-Ok([string]$T)   { Write-Host "  [+] $T" -ForegroundColor Green }
function Log-Info([string]$T) { Write-Host "  [*] $T" -ForegroundColor Cyan }
function Log-Warn([string]$T) { Write-Host "  [!] $T" -ForegroundColor Yellow }
function Log-Fail([string]$T) { Write-Host "  [x] $T" -ForegroundColor Red }
function Set-Step([string]$T) { }

# build-mios.ps1 Phase 4's exposure call, as it runs there (without StrictMode). It asks UAC only when the Defender
# rules, forwards or refresh task differ from mios.toml [firewall.windows], and sweeps what the plan no longer names.
& {
    Set-StrictMode -Off
    try { Invoke-MiosLanExposure } catch { Log-Warn "Windows host exposure: $($_.Exception.Message)" }
}

# Retired-port cleanup: forwards and rules releases before the [ports] renumbering left on ports MiOS no longer uses.
# These literals are retired ports, kept only to delete what they opened (mios.toml [docs].retired_code_exemptions).
$retiredPorts = @(
    @{ Port = 9090;  Name = 'cockpit' }
    @{ Port = 9119;  Name = 'dash-ai' }
    @{ Port = 8443;  Name = 'ceph-dash' }
    @{ Port = 3389;  Name = 'rdp' }
    @{ Port = 3000;  Name = 'forge' }
    @{ Port = 3030;  Name = 'hermes_workspace' }
    @{ Port = 3033;  Name = 'open-webui' }
)
# Both read back without elevation; only leftovers are deleted, elevated in place or through one UAC prompt.
$forwards = @(& netsh interface portproxy show v4tov4 2>$null | ForEach-Object {
    if ([string]$_ -match '^\s*0\.0\.0\.0\s+([0-9]+)\s') { [int]$Matches[1] } })
$cleanup = foreach ($rp in $retiredPorts) {
    $rule = "MiOS - $($rp.Name) ($([int]$rp.Port)/tcp)"
    if ($forwards -contains [int]$rp.Port) { "netsh.exe interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$([int]$rp.Port) | Out-Null" }
    if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) { "Remove-NetFirewallRule -DisplayName '$rule' -ErrorAction SilentlyContinue" }
}
if ($cleanup) {
    $cleanupText = @($cleanup) -join "`n"
    if ($isAdmin) {
        & ([scriptblock]::Create($cleanupText))
        Log-Ok "retired-port cleanup: removed $(@($cleanup).Count) leftover forward(s) and rule(s)"
    } else {
        $engine = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cleanupText))
        Write-Host '  [*] Approve UAC to delete the forwards and rules older MiOS releases left on retired ports.' -ForegroundColor Cyan
        try {
            $p = Start-Process -FilePath $engine -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ErrorAction Stop `
                -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded)
            if ($p.ExitCode -eq 0) { Log-Ok "retired-port cleanup: removed $(@($cleanup).Count) leftover forward(s) and rule(s)" }
            else { Log-Warn "retired-port cleanup: the elevated cleanup exited $($p.ExitCode)" }
        } catch { Log-Warn "retired-port cleanup: UAC did not start it ($($_.Exception.Message)); the leftovers remain" }
    }
}

# Show the resulting state and where the exposed services answer.
Write-Host ''
Write-Host '--- netsh portproxy table ---' -ForegroundColor Cyan
& netsh interface portproxy show all

Write-Host ''
Write-Host '--- mios.toml [firewall.windows] exposure ---' -ForegroundColor Cyan
$plan = & { Set-StrictMode -Off; Get-MiosLanExposurePlan }
if (-not $plan.Ports.Count -or -not $plan.RemoteAddresses.Count -or -not $plan.Profiles.Count) {
    Write-Host '  nothing is exposed: the plan resolved to no port, no remote address or no profile (see the warnings above)' -ForegroundColor Yellow
} else {
    $lan = $null
    if (Get-Command Get-NetIPAddress -ErrorAction SilentlyContinue) {
        $lanObj = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -match '^192\.168\.' -or $_.IPAddress -match '^10\.' -or $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.' } |
            Where-Object { $_.InterfaceAlias -notmatch 'Loopback|vEthernet|WSL' } |
            Select-Object -First 1
        if ($lanObj) { $lan = $lanObj.IPAddress }
    }
    if (-not $lan) { $lan = '<your-windows-ip>' }
    foreach ($svc in $plan.Ports.Keys) { Write-Host ("  {0}:{1}   ({2})" -f $lan, $plan.Ports[$svc], $svc) }
    Write-Host ("  from {0}, on the {1} profiles" -f ($plan.RemoteAddresses -join ', '), ($plan.Profiles -join ', '))
}

Write-Host ''
Write-Host '  Done. Press any key to close this window.' -ForegroundColor Green
if (-not [Console]::IsInputRedirected) {
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
}
