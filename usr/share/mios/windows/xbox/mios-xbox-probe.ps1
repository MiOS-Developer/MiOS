# AI-hint: MiOS-Xbox in-guest conformance probe (xb-18, XB-6): READ-ONLY. Given the item list derived from the resolved xbox-spec.json (passed as base64 JSON by usr/libexec/mios/win/xbox/probe.py over WinRM), reports what the running guest actually has -- desktop-user hive values (taskbar, Start, Xbox, settings), machine registry, optional features, capabilities, third-party drivers, display adapters, guest-tool services, MiOS component files/tasks/services, Appx packages, local users/autologon/RID-500, computer name, the GPU post-setup record -- as one JSON document. It never changes the guest and never returns a credential (Winlogon DefaultPassword is reported only as a sha256 digest).
# AI-related: usr/libexec/mios/win/xbox/probe.py, usr/libexec/mios/win/xbox/spec_items.py, tests/test-mios-xbox-probe.py, usr/share/mios/windows/mios-gpu-driver.ps1
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RequestB64   # base64(UTF-8 JSON {schema, desktop_user, items[]})
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$req = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestB64)) | ConvertFrom-Json
$errors = New-Object System.Collections.ArrayList
$obsMap = [ordered]@{}

function Get-Sha256Text([string]$s) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($s)) | ForEach-Object { $_.ToString('x2') }) -join '') }
    finally { $sha.Dispose() }
}

function Convert-RegValue($key, [string]$name) {
    if ($null -eq $key) { return [ordered]@{ present = $false } }
    $kind = $null
    try { $kind = $key.GetValueKind($name) } catch { return [ordered]@{ present = $false } }
    $v = $key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    switch ($kind.ToString()) {
        'DWord'        { return [ordered]@{ present = $true; type = 'dword'; value = [int64]([uint32]([int32]$v -band 0xFFFFFFFF)) } }
        'QWord'        { return [ordered]@{ present = $true; type = 'qword'; value = [int64]$v } }
        'Binary'       { return [ordered]@{ present = $true; type = 'binary'; value = (($v | ForEach-Object { $_.ToString('x2') }) -join '') } }
        'MultiString'  { return [ordered]@{ present = $true; type = 'multi_sz'; value = @($v) } }
        'ExpandString' { return [ordered]@{ present = $true; type = 'expand_sz'; value = [string]$v } }
        default        { return [ordered]@{ present = $true; type = 'sz'; value = [string]$v } }
    }
}

function Open-RegKey([Microsoft.Win32.RegistryHive]$Hive, [string]$Path) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($Hive, [Microsoft.Win32.RegistryView]::Registry64)
    return $base.OpenSubKey($Path, $false)
}

# --- desktop user's hive: HKU\<SID> when signed in, else a read-only temporary load of NTUSER.DAT ---
$desktopUser = [string]$req.desktop_user
$userSid = $null; $userRoot = $null; $loadedMount = $null; $profilePath = $null
try {
    $userSid = (New-Object System.Security.Principal.NTAccount($desktopUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $pl = Open-RegKey LocalMachine "SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$userSid"
    if ($pl) { $profilePath = [string]$pl.GetValue('ProfileImagePath'); $pl.Close() }
    $hku = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::Users, [Microsoft.Win32.RegistryView]::Registry64)
    if ($hku.OpenSubKey($userSid, $false)) {
        $userRoot = $userSid
    } elseif ($profilePath -and (Test-Path -LiteralPath (Join-Path $profilePath 'NTUSER.DAT'))) {
        $loadedMount = 'MiOSProbe_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
        & reg.exe load "HKU\$loadedMount" (Join-Path $profilePath 'NTUSER.DAT') 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $userRoot = $loadedMount } else { $loadedMount = $null; [void]$errors.Add("cannot load the hive of $desktopUser") }
    } else { [void]$errors.Add("no profile for $desktopUser") }
} catch { [void]$errors.Add("desktop user $desktopUser : $($_.Exception.Message)") }

function Get-RegObs($it) {
    $path = [string]$it.path
    switch ([string]$it.scope) {
        'user' {
            if (-not $userRoot) { return [ordered]@{ present = $false; error = 'desktop user hive unavailable' } }
            $k = Open-RegKey Users ("$userRoot\$path")
        }
        'software' { $k = Open-RegKey LocalMachine ("SOFTWARE\$path") }
        'system'   { $k = Open-RegKey LocalMachine ("SYSTEM\$path") }
        default    { return [ordered]@{ present = $false; error = "unknown scope $($it.scope)" } }
    }
    try { return (Convert-RegValue $k ([string]$it.name)) } finally { if ($k) { $k.Close() } }
}

# --- cached inventories (each queried once) ---
$script:drivers = $null
function Get-DriverInventory {
    if ($null -eq $script:drivers) {
        $script:drivers = @(Get-WindowsDriver -Online -ErrorAction SilentlyContinue | ForEach-Object {
            [ordered]@{ inf = ([IO.Path]::GetFileNameWithoutExtension([string]$_.OriginalFileName)).ToLower(); class = [string]$_.ClassName
                        provider = [string]$_.ProviderName; version = [string]$_.Version; published = [string]$_.Driver } })
    }
    return $script:drivers
}

foreach ($it in @($req.items)) {
    $id = [string]$it.id
    try {
        switch ([string]$it.kind) {
            'reg' { $obsMap[$id] = Get-RegObs $it }
            'feature' {
                $f = Get-WindowsOptionalFeature -Online -FeatureName ([string]$it.name) -ErrorAction SilentlyContinue
                $obsMap[$id] = [ordered]@{ state = $(if ($f) { [string]$f.State } else { 'Absent' }) }
            }
            'capability' {
                $c = @(Get-WindowsCapability -Online -Name ("{0}*" -f $it.name) -ErrorAction SilentlyContinue)
                $obsMap[$id] = [ordered]@{ state = $(if ($c.Count) { [string]$c[0].State } else { 'Absent' }) }
            }
            'driver' {
                $hit = @(Get-DriverInventory | Where-Object { $_.inf -eq ([string]$it.inf).ToLower() })
                $obsMap[$id] = [ordered]@{ present = ($hit.Count -gt 0); version = $(if ($hit.Count) { $hit[0].version } else { $null }) }
            }
            'guest_tool' {
                $o = [ordered]@{}
                if ($it.PSObject.Properties['service'] -and $it.service) {
                    $s = Get-Service -Name ([string]$it.service) -ErrorAction SilentlyContinue
                    if (-not $s) { $s = Get-Service -DisplayName ([string]$it.service) -ErrorAction SilentlyContinue }
                    $o.service = [ordered]@{ present = [bool]$s; status = $(if ($s) { [string]$s.Status } else { $null }); start = $(if ($s) { [string]$s.StartType } else { $null }) }
                }
                $obsMap[$id] = $o
            }
            'component' {
                $p = Join-Path $env:SystemDrive ([string]$it.path -replace '/', '\')
                $o = [ordered]@{ present = (Test-Path -LiteralPath $p -PathType Leaf) }
                if ($o.present) { $o.sha256 = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }
                if ($it.PSObject.Properties['task'] -and $it.task) {
                    $parts = @(([string]$it.task).Split('\') | Where-Object { $_ })
                    $tn = $parts[-1]; $tp = '\'
                    if ($parts.Count -gt 1) { $tp = '\' + ((@($parts[0..($parts.Count - 2)]) | ForEach-Object { $_ + '\' }) -join '') }
                    $t = @(Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -eq $tp -or $tp -eq '\' })
                    $o.task = [ordered]@{ present = ($t.Count -gt 0); state = $(if ($t.Count) { [string]$t[0].State } else { $null }) }
                }
                if ($it.PSObject.Properties['service'] -and $it.service) {
                    $s = Get-Service -Name ([string]$it.service) -ErrorAction SilentlyContinue
                    $o.service = [ordered]@{ present = [bool]$s; status = $(if ($s) { [string]$s.Status } else { $null }) }
                }
                $obsMap[$id] = $o
            }
            'build_family' {
                $o = [ordered]@{ present = $false }
                if ($profilePath) {
                    $sb = Join-Path $profilePath 'AppData\Local\Packages\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\LocalState\start2.bin'
                    if (Test-Path -LiteralPath $sb) { $o.present = $true; $o.sha256 = (Get-FileHash -LiteralPath $sb -Algorithm SHA256).Hash.ToLower() }
                }
                $obsMap[$id] = $o
            }
            'user' {
                if ([string]$it.name -eq '<RID-500>') { $u = Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } }
                else { $u = Get-LocalUser -Name ([string]$it.name) -ErrorAction SilentlyContinue }
                $o = [ordered]@{ present = [bool]$u; enabled = $(if ($u) { [bool]$u.Enabled } else { $null }); groups = @() }
                if ($u -and $it.PSObject.Properties['groups']) {
                    foreach ($g in @($it.groups)) {
                        $m = @(Get-LocalGroupMember -Group ([string]$g) -ErrorAction SilentlyContinue | Where-Object { $_.SID -eq $u.SID })
                        if ($m.Count) { $o.groups += [string]$g }
                    }
                }
                $obsMap[$id] = $o
            }
            'rid500' {
                $u = Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' }
                $obsMap[$id] = [ordered]@{ present = [bool]$u; enabled = $(if ($u) { [bool]$u.Enabled } else { $null }) }
            }
            'computer_name' { $obsMap[$id] = [ordered]@{ value = [string]$env:COMPUTERNAME } }
            'appx' {
                $a = @(Get-AppxPackage -AllUsers -Name ([string]$it.name) -ErrorAction SilentlyContinue)
                $obsMap[$id] = [ordered]@{ present = ($a.Count -gt 0); version = $(if ($a.Count) { [string]$a[0].Version } else { $null }) }
            }
            'gpu_step' {
                $o = [ordered]@{ present = $false }
                $rp = [string]$it.record
                if (Test-Path -LiteralPath $rp) { $o.present = $true; $o.record = (Get-Content -LiteralPath $rp -Raw | ConvertFrom-Json) }
                $obsMap[$id] = $o
            }
            default { $obsMap[$id] = [ordered]@{ error = "unknown kind $($it.kind)" } }
        }
    } catch {
        $obsMap[$id] = [ordered]@{ error = $_.Exception.Message }
    }
}

# --- machine facts shared by several checks ---
$cv = Open-RegKey LocalMachine 'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$wl = Open-RegKey LocalMachine 'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$autologon = [ordered]@{
    auto_admin_logon = [string]$wl.GetValue('AutoAdminLogon', '')
    default_user     = [string]$wl.GetValue('DefaultUserName', '')
    logon_count      = $wl.GetValue('AutoLogonCount', $null)
    password_sha256  = $null
}
$dp = $wl.GetValue('DefaultPassword', $null)
if ($null -ne $dp) { $autologon.password_sha256 = Get-Sha256Text ([string]$dp) }
$dp = $null

$display = @(Get-CimInstance Win32_PnPSignedDriver -Filter "DeviceClass='DISPLAY'" -ErrorAction SilentlyContinue | ForEach-Object {
    [ordered]@{ device_id = [string]$_.DeviceID; name = [string]$_.DeviceName; provider = [string]$_.DriverProviderName
                version = [string]$_.DriverVersion; inf = [string]$_.InfName } })
$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue

$out = [ordered]@{
    schema       = 'mios-xbox-probe/1'
    probed_at    = (Get-Date).ToUniversalTime().ToString('o')
    auth_user    = [string]$env:USERNAME
    build        = [string]$cv.GetValue('CurrentBuild', '')
    branch       = [string]$cv.GetValue('BuildBranch', '')
    ubr          = $cv.GetValue('UBR', $null)
    desktop_user = [ordered]@{ name = $desktopUser; sid = $userSid; hive = $(if ($loadedMount) { 'loaded' } elseif ($userRoot) { 'signed-in' } else { 'unavailable' }) }
    autologon    = $autologon
    nested_virt  = $(if ($cpu) { [bool]$cpu.VirtualizationFirmwareEnabled } else { $null })
    hypervisor   = $(if ($cs) { [bool]$cs.HypervisorPresent } else { $null })
    display      = $display
    third_party_drivers = @(Get-DriverInventory)
    observations = $obsMap
    errors       = @($errors)
}
if ($loadedMount) { [gc]::Collect(); [gc]::WaitForPendingFinalizers(); & reg.exe unload "HKU\$loadedMount" 2>&1 | Out-Null }
$out | ConvertTo-Json -Depth 8 -Compress
