# AI-hint: MiOS-Xbox in-guest stages driven by C:\ProgramData\MiOS\xbox-spec.json (the credential-free guest projection staged by answer.py): -Stage Hydrate installs the [windows.xbox.store_hydrate] Store-only packages (winget msstore) and removes its own logon task once every package is provisioned; -Stage Verify records, read-only, the guest-tool services, Store packages, GPU step record, Looking Glass display and RID-500 state into provision-state.json for the host probe. Every name comes from the spec; nothing is hardcoded.
# AI-related: usr/libexec/mios/win/xbox/answer.py (renders the first-logon script that registers the hydrate task and calls Verify), usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/xbox/mios-gpu-driver.ps1
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Hydrate', 'Verify')][string]$Stage = 'Verify',
    [string]$SpecPath = 'C:\ProgramData\MiOS\xbox-spec.json',
    [string]$StateDir = 'C:\ProgramData\MiOS'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$log = Join-Path $StateDir 'provision.log'
function Log([string]$Message, [string]$Level = 'INFO') {
    $line = "[{0}] [{1}] [{2}] {3}" -f [DateTime]::UtcNow.ToString('o'), $Stage, $Level, $Message
    Write-Output $line
    try { Add-Content -LiteralPath $log -Value $line -Encoding UTF8 } catch {}
}
function Save-Json([string]$Path, $Data) {
    $tmp = "$Path.tmp"
    [IO.File]::WriteAllText($tmp, ($Data | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

if (-not (Test-Path -LiteralPath $SpecPath)) { throw "spec $SpecPath missing (the image stages it)" }
$doc = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($doc.projection -ne 'guest' -or -not $doc.spec) { throw "$SpecPath is not the guest projection of xbox-spec.json" }
$x = $doc.spec
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

if ($Stage -eq 'Hydrate') {
    $pending = @()
    foreach ($pkg in @($x.store_hydrate.packages)) {
        if (Get-AppxPackage -Name ([string]$pkg.name) -ErrorAction SilentlyContinue) { Log "$($pkg.name) provisioned"; continue }
        $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $wg) { Log 'winget not available yet; retry at next logon' 'WARN'; $pending += $pkg.name; continue }
        & $wg.Source install --id ([string]$pkg.store_id) --source msstore --silent --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        if (Get-AppxPackage -Name ([string]$pkg.name) -ErrorAction SilentlyContinue) { Log "$($pkg.name) installed" } else { Log "$($pkg.name) not installed yet (winget exit $LASTEXITCODE)" 'WARN'; $pending += $pkg.name }
    }
    Save-Json (Join-Path $StateDir 'hydrate-state.json') ([ordered]@{ at = [DateTime]::UtcNow.ToString('o'); pending = $pending })
    if (-not $pending.Count) {
        Unregister-ScheduledTask -TaskPath '\MiOS\' -TaskName 'MiOS-StoreHydrate' -Confirm:$false -ErrorAction SilentlyContinue
        Log 'every Store package is provisioned; hydrate task removed'
    }
    return
}

# Verify (read-only)
$services = [ordered]@{}
foreach ($g in @($x.guest_tools.PSObject.Properties)) {
    $name = [string]($g.Value.PSObject.Properties | Where-Object { $_.Name -eq 'service' } | ForEach-Object { $_.Value })
    if (-not $name) { continue }
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    $services[$name] = if ($svc) { [ordered]@{ present = $true; status = [string]$svc.Status; start = [string]$svc.StartType } } else { [ordered]@{ present = $false } }
}
$store = [ordered]@{}
foreach ($pkg in @($x.store_hydrate.packages)) {
    $a = Get-AppxPackage -Name ([string]$pkg.name) -AllUsers -ErrorAction SilentlyContinue | Select-Object -First 1
    $store[[string]$pkg.name] = if ($a) { [ordered]@{ installed = $true; version = [string]$a.Version } } else { [ordered]@{ installed = $false } }
}
$gpu = [ordered]@{ present = $false }
$rec = [string]$x.gpu.record_path
if ($rec -and (Test-Path -LiteralPath $rec)) {
    $g = Get-Content -LiteralPath $rec -Raw -Encoding UTF8 | ConvertFrom-Json
    $gpu = [ordered]@{ present = $true; status = [string]$g.status }
}
$idd = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*Looking Glass*' })
$rid = Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } | Select-Object -First 1
$setup = Join-Path $StateDir 'setup-state.json'
Save-Json (Join-Path $StateDir 'provision-state.json') ([ordered]@{
    schema = 'mios-xbox-provision-state/1'; at = [DateTime]::UtcNow.ToString('o')
    services = $services; store_packages = $store; gpu_driver = $gpu
    looking_glass_idd = ($idd.Count -gt 0); rid500_enabled = $(if ($rid) { [bool]$rid.Enabled } else { $false })
    setup = $(if (Test-Path -LiteralPath $setup) { Get-Content -LiteralPath $setup -Raw | ConvertFrom-Json } else { $null })
})
Log 'provision-state.json written'
