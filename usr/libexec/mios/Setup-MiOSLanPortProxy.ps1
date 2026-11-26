# AI-hint: Re-applies MiOS-DEV's Windows-host LAN exposure on demand, exactly as build-mios.ps1 Phase 4 does: it lifts that script's own [firewall.windows] exposure plan, Defender-rule and portproxy steps out of the build-mios.ps1 beside it and runs them, after removing what older releases left on retired ports.
# AI-doc: usr/share/doc/mios/manual/mios.md
# AI-related: build-mios.ps1, /usr/share/mios/mios.toml, /usr/libexec/mios/mios-lan-status

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Self-elevate if not admin.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '  [*] Not elevated. Re-launching via UAC...' -ForegroundColor Yellow
    $psArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
    Start-Process -FilePath 'pwsh.exe' -ArgumentList $psArgs -Verb RunAs
    return
}

# The Windows-host exposure has one implementation, build-mios.ps1 Phase 4. Its exposure plan (mios.toml
# [firewall.windows] expose, profiles and remote_addresses, bounded by the system SSOT), rule names, scope and portproxy
# target are lifted from the build-mios.ps1 at this tree's root (the repo root, which IS the system root) and run as
# they are; nothing here restates them. Without that file, or with any of them missing, nothing is changed.
$canonical = Join-Path $PSScriptRoot '..\..\..\build-mios.ps1'
if (-not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
    throw "build-mios.ps1 is not at $canonical; nothing was changed. The Windows-host exposure is build-mios.ps1 Phase 4's (mios.toml [firewall.windows]): run this helper from a MiOS tree that carries it, or re-run the bootstrap."
}
$canonical = (Resolve-Path -LiteralPath $canonical).ProviderPath
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($canonical, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "$canonical does not parse; nothing was changed." }
$top = @($ast.EndBlock.Statements)
$liftedNames = @('Resolve-MiosTomlLayers', 'Get-MiosTomlValue', 'Get-MiosSsotPort', 'Get-MiosVendorTomlValue',
    'ConvertTo-MiosNetworkPrefix', 'Test-MiosPrefixWithin', 'Resolve-MiosWslNetworkingMode', 'Resolve-MiosBuilderDistribution',
    'Resolve-MiosWslVmAddress', 'Get-MiosVendorPortSet',
    'Get-MiosLanExposurePlan', 'Set-MiosLanFirewallRules', 'Set-MiosLanPortProxy')
$lifted = foreach ($name in $liftedNames) {
    $def = @($top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $name })
    if ($def.Count -ne 1) { throw "$canonical has no single top-level function $name; nothing was changed." }
    $def[0].Extent.Text
}
# The layer cache those readers share and [bootstrap].dev_distro (Set-MiosLanPortProxy reads $DevDistro), by
# build-mios.ps1's own statements.
foreach ($var in @('$script:_MiosTomlCache', '$DevDistro')) {
    $st = @($top | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq $var })
    if ($st.Count -ne 1) { throw "$canonical has no single top-level $var assignment; nothing was changed." }
    $lifted += $st[0].Extent.Text
}
# build-mios.ps1 runs without StrictMode, so its statements and steps do here too.
Set-StrictMode -Off
. ([scriptblock]::Create($lifted -join "`n`n"))
Set-StrictMode -Version Latest

# Console loggers in place of build-mios.ps1's log-file and dashboard ones; the lifted steps call them by these names.
function Write-Log([string]$M, [string]$L = 'INFO') { Write-Host "  [.] $M" -ForegroundColor DarkGray }
function Log-Ok([string]$T)   { Write-Host "  [+] $T" -ForegroundColor Green }
function Log-Info([string]$T) { Write-Host "  [*] $T" -ForegroundColor Cyan }
function Log-Warn([string]$T) { Write-Host "  [!] $T" -ForegroundColor Yellow }

# Retired-port cleanup: the forwards and rules releases before the [ports] renumbering left on ports MiOS no longer
# uses. These literals are retired ports, kept only to remove what they opened (mios.toml [docs].retired_code_exemptions).
$retiredPorts = @(
    @{ Port = 9090;  Name = 'cockpit' }
    @{ Port = 9119;  Name = 'dash-ai' }
    @{ Port = 8443;  Name = 'ceph-dash' }
    @{ Port = 3389;  Name = 'rdp' }
    @{ Port = 3000;  Name = 'forge' }
    @{ Port = 3030;  Name = 'hermes_workspace' }
    @{ Port = 3033;  Name = 'open-webui' }
)
foreach ($rp in $retiredPorts) {
    & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$($rp.Port) 2>$null | Out-Null
    Remove-NetFirewallRule -DisplayName "MiOS - $($rp.Name) ($($rp.Port)/tcp)" -ErrorAction SilentlyContinue | Out-Null
}

# build-mios.ps1 Phase 4, step for step and as it runs there (without StrictMode): the Defender rules, then the
# portproxy. Each sweeps the "MiOS - *" rules and the 0.0.0.0 forwards of MiOS [ports] values that
# [firewall.windows].expose no longer names, so a rule or forward an earlier release of this helper opened outside the
# plan is removed here too.
& {
    Set-StrictMode -Off
    try { Set-MiosLanFirewallRules } catch { Log-Warn "Set-MiosLanFirewallRules: $($_.Exception.Message)" }
    try { Set-MiosLanPortProxy }     catch { Log-Warn "Set-MiosLanPortProxy: $($_.Exception.Message)" }
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
