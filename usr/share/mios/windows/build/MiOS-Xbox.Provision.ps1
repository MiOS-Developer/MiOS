# AI-hint: MiOS-Xbox in-guest stages driven by C:\ProgramData\MiOS\xbox-spec.json (the credential-free guest projection staged by answer.py): -Stage Hydrate runs AS the desktop user (its logon task; Store installs are per user) and installs the [windows.xbox.store_hydrate] Store-only packages (winget msstore), logging under its own %LOCALAPPDATA%\MiOS (the SYSTEM state dir is read-only for users); -Stage Retry runs as SYSTEM at startup and re-runs any guest-tool MSI SetupComplete queued (MSDTC first) until its service exists, then removes its task; -Stage Verify runs as SYSTEM (the SetupComplete stage once, then the [windows.xbox.provision].verify_task at startup and desktop logon), first reconciles what Windows' first desktop sign-in changes after SetupComplete (deletes AutoLogonCount once the desktop account holds AutoLogon; re-applies the spec's DEFAULT_USER registry items to the loaded desktop hive), then records, read-only, the guest-tool services, MiOS services/tasks, Store packages, GPU step record, Looking Glass display, sshd/TermService/WinRM and whether RDP, WinRM and sshd listen on their ports, AutoLogon persistence, RID-500 state and any cached Panther answer file into provision-state.json for the host probe; once every Store package is present it removes the hydrate task (a standard user cannot delete a task SYSTEM registered). Every line is mirrored to the guest serial port the host logs. Every name comes from the spec; nothing is hardcoded.
# AI-related: usr/libexec/mios/win/xbox/answer.py (renders the SYSTEM stage that registers both tasks and calls Verify), usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/xbox/mios-gpu-driver.ps1
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Hydrate', 'Verify', 'Retry')][string]$Stage = 'Verify',
    [string]$SpecPath = 'C:\ProgramData\MiOS\xbox-spec.json',
    [string]$StateDir = 'C:\ProgramData\MiOS'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
# one log per stage. Hydrate runs AS the desktop user, and the SYSTEM state dir is read-only for users (Protect-MiOSDir
# in mios-looking-glass-idd.ps1: SYSTEM/Administrators write, Users read -- SYSTEM stages read their inputs there), so the
# user stage writes its log and record under its own profile (round-1 boot: every write to StateDir was access-denied)
$OutDir = if ($Stage -eq 'Hydrate') { Join-Path $env:LOCALAPPDATA 'MiOS' } else { $StateDir }
$log = Join-Path $OutDir ('provision-{0}.log' -f $Stage.ToLowerInvariant())
$script:Serial = $null
function Get-Prop($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($p) { return $p.Value }
    return $null
}
function Log([string]$Message, [string]$Level = 'INFO') {
    $line = "[{0}] [MiOS provision {1}] [{2}] {3}" -f [DateTime]::UtcNow.ToString('o'), $Stage, $Level, $Message
    Write-Output $line
    try { Add-Content -LiteralPath $log -Value $line -Encoding UTF8 } catch {}
    if ($Stage -eq 'Hydrate') { return }   # the serial port is a SYSTEM resource; the user stage logs to its file only
    try {
        if (-not $script:Serial) {
            $pv = Get-Prop (Get-Prop $script:x 'provision') 'serial_port'
            $baud = Get-Prop (Get-Prop $script:x 'provision') 'serial_baud'
            if ($pv -and $baud) {
                $p = New-Object System.IO.Ports.SerialPort -ArgumentList ([string]$pv), ([int]$baud)
                $p.WriteTimeout = 2000; $p.NewLine = "`r`n"; $p.Open(); $script:Serial = $p
            }
        }
        if ($script:Serial) { $script:Serial.WriteLine($line) }
    } catch { $script:Serial = $null }
}
function Save-Json([string]$Path, $Data) {
    $tmp = "$Path.tmp"
    [IO.File]::WriteAllText($tmp, ($Data | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Split-TaskName([string]$Name) {
    $parts = @($Name -split '[\\/]' | Where-Object { $_ })
    $path = if ($parts.Count -gt 1) { '\' + (($parts[0..($parts.Count - 2)]) -join '\') + '\' } else { '\' }
    return @($path, $parts[-1])
}
function Merge-MiOSKv([string]$Current, [string]$Field, $Value) {
    # a "k=v;" list value (registry_map kv_field): only $Field is owned; the other fields keep their values and order
    $pairs = [ordered]@{}
    foreach ($p in @($Current -split ';' | Where-Object { $_ -match '=' })) { $i = $p.IndexOf('='); $pairs[$p.Substring(0, $i)] = $p.Substring($i + 1) }
    $pairs[$Field] = [string]$Value
    return ((@($pairs.Keys | ForEach-Object { '{0}={1}' -f $_, $pairs[$_] }) -join ';') + ';')
}
function Set-MiOSUserValue($Root, $It) {
    # one DEFAULT_USER item of the spec's registry list on an open, writable user-hive root key (HKU\<SID>).
    # Returns $true when the hive changed; an unsupported item throws (recorded per item by the caller, never silent).
    $name = [string]$It.name
    if ([bool](Get-Prop $It 'delete')) {
        $k = $Root.OpenSubKey([string]$It.key, $true)
        if (-not $k) { return $false }
        try { if (@($k.GetValueNames()) -contains $name) { $k.DeleteValue($name); return $true }; return $false } finally { $k.Close() }
    }
    $k = $Root.CreateSubKey([string]$It.key)
    try {
        $has = @($k.GetValueNames()) -contains $name
        $cur = if ($has) { $k.GetValue($name, $null, 'DoNotExpandEnvironmentNames') } else { $null }
        switch ([string]$It.type) {
            'REG_DWORD' {
                $want = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$It.data), 0)
                if ($has -and [string]$k.GetValueKind($name) -eq 'DWord' -and [int]$cur -eq $want) { return $false }
                $k.SetValue($name, $want, 'DWord'); return $true
            }
            'REG_SZ' {
                $want = [string]$It.data
                $kv = [string](Get-Prop $It 'kv_field')
                if ($kv) { $want = Merge-MiOSKv $(if ($has) { [string]$cur } else { '' }) $kv $It.data }
                if ($has -and [string]$k.GetValueKind($name) -eq 'String' -and [string]$cur -eq $want) { return $false }
                $k.SetValue($name, $want, 'String'); return $true
            }
            default { throw "unsupported DEFAULT_USER value type $($It.type)" }
        }
    } finally { $k.Close() }
}

$script:x = $null
if (-not (Test-Path -LiteralPath $SpecPath)) { throw "spec $SpecPath missing (the image stages it)" }
$doc = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($doc.projection -ne 'guest' -or -not $doc.spec) { throw "$SpecPath is not the guest projection of xbox-spec.json" }
$x = $doc.spec
$script:x = $x
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$hydrateTask = Split-TaskName ([string](Get-Prop (Get-Prop $x 'provision') 'hydrate_task'))

if ($Stage -eq 'Hydrate') {
    $pending = @()
    foreach ($pkg in @($x.store_hydrate.packages)) {
        if (Get-AppxPackage -Name ([string]$pkg.name) -ErrorAction SilentlyContinue) { Log "$($pkg.name) provisioned"; continue }
        $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $wg) { Log 'winget not available yet; retry at next logon' 'WARN'; $pending += $pkg.name; continue }
        & $wg.Source install --id ([string]$pkg.store_id) --source msstore --silent --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        if (Get-AppxPackage -Name ([string]$pkg.name) -ErrorAction SilentlyContinue) { Log "$($pkg.name) installed" } else { Log "$($pkg.name) not installed yet (winget exit $LASTEXITCODE)" 'WARN'; $pending += $pkg.name }
    }
    Save-Json (Join-Path $OutDir 'hydrate-state.json') ([ordered]@{ at = [DateTime]::UtcNow.ToString('o'); account = [Security.Principal.WindowsIdentity]::GetCurrent().Name; pending = $pending })
    if (-not $pending.Count) { Log 'every Store package is provisioned; the SYSTEM Verify task removes the hydrate task' }
    return
}

if ($Stage -eq 'Retry') {
    # SYSTEM startup task: re-run every guest-tool MSI the SetupComplete stage queued (retry.json) until its service
    # exists; MSDTC first (qemu-ga's qga-vss RegisterCom needs COM+ -> MSDTC). Removes itself when the queue is empty.
    $q = Join-Path $StateDir 'retry.json'
    $retryTask = Split-TaskName ([string](Get-Prop (Get-Prop $x 'provision') 'retry_task'))
    $left = @()
    if (Test-Path -LiteralPath $q) {
        $d = Get-Service -Name 'MSDTC' -ErrorAction SilentlyContinue
        if ($d -and [string]$d.StartType -eq 'Disabled') { Set-Service -Name 'MSDTC' -StartupType Manual }
        if ($d -and (Get-Service -Name 'MSDTC').Status -ne 'Running') { try { Start-Service -Name 'MSDTC' } catch { Log "MSDTC did not start: $($_.Exception.Message)" 'WARN' } }
        $logDir = [string](Get-Prop (Get-Prop $x 'provision') 'log_dir')
        foreach ($r in @(Get-Content -LiteralPath $q -Raw -Encoding UTF8 | ConvertFrom-Json)) {
            if (Get-Service -Name ([string]$r.service) -ErrorAction SilentlyContinue) { Log "$($r.name): service $($r.service) present"; continue }
            if ((Get-FileHash -LiteralPath ([string]$r.msi) -Algorithm SHA256).Hash.ToLowerInvariant() -ne ([string]$r.sha256).ToLowerInvariant()) {
                Log "$($r.name): $($r.msi) does not match its build-manifest sha256; not run" 'ERROR'; $left += $r; continue
            }
            $mlog = Join-Path $logDir ("{0}.retry.msi.log" -f $r.name)
            $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\msiexec.exe') -Wait -PassThru -WindowStyle Hidden `
                -ArgumentList @('/i', ('"{0}"' -f $r.msi), '/qn', '/norestart', '/l*v', ('"{0}"' -f $mlog))
            if ($p.ExitCode -in 0, 1641, 3010 -and (Get-Service -Name ([string]$r.service) -ErrorAction SilentlyContinue)) {
                Set-Service -Name ([string]$r.service) -StartupType Automatic
                try { Start-Service -Name ([string]$r.service) } catch { Log "$($r.name): service $($r.service) did not start: $($_.Exception.Message)" 'WARN' }
                Log "$($r.name): installed on retry (msiexec $($p.ExitCode)); service $($r.service) $((Get-Service -Name ([string]$r.service)).Status)"
            } else { Log "$($r.name): msiexec exited $($p.ExitCode) (log $mlog); retry at next startup" 'WARN'; $left += $r }
        }
    }
    if ($left.Count) {
        Save-Json $q @($left)
    } else {
        Remove-Item -LiteralPath $q -ErrorAction SilentlyContinue
        if ($retryTask[1]) { Unregister-ScheduledTask -TaskPath $retryTask[0] -TaskName $retryTask[1] -Confirm:$false -ErrorAction SilentlyContinue }
        Log 'guest-tool retry queue empty; retry task removed'
    }
    if ($script:Serial) { try { $script:Serial.Close() } catch {} }
    return
}

# Verify (SYSTEM) -- records state read-only; its only writes besides the record: removing the finished hydrate task, and
# the first-sign-in reconcile below (AutoLogonCount deleted, the desktop hive's DEFAULT_USER values re-applied)
$services = [ordered]@{}
$names = @()
foreach ($g in @($x.guest_tools.PSObject.Properties)) { $n = [string](Get-Prop $g.Value 'service'); if ($n) { $names += $n } }
$svcTable = Get-Prop $x 'services'
if ($svcTable) { $names += @($svcTable.PSObject.Properties | ForEach-Object { $_.Name }) }
$names += 'sshd', 'TermService', 'WinRM'
foreach ($name in $names) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    $services[$name] = if ($svc) { [ordered]@{ present = $true; status = [string]$svc.Status; start = [string]$svc.StartType } } else { [ordered]@{ present = $false } }
}
$tasks = [ordered]@{}
$taskTable = Get-Prop $x 'tasks'
$taskNames = @()
if ($taskTable) { $taskNames += @($taskTable.PSObject.Properties | ForEach-Object { 'MiOS\' + $_.Name }) }
$taskNames += [string](Get-Prop (Get-Prop $x 'provision') 'verify_task'), [string](Get-Prop (Get-Prop $x 'provision') 'hydrate_task'), [string](Get-Prop (Get-Prop $x 'provision') 'retry_task'), [string]$x.gpu.task_name
foreach ($tn in @($taskNames | Where-Object { $_ })) {
    $pn = Split-TaskName $tn
    $t = Get-ScheduledTask -TaskPath $pn[0] -TaskName $pn[1] -ErrorAction SilentlyContinue
    $tasks[$tn] = if ($t) { [ordered]@{ present = $true; state = [string]$t.State; user = [string]$t.Principal.UserId } } else { [ordered]@{ present = $false } }
}
$store = [ordered]@{}
$allStore = $true
foreach ($pkg in @($x.store_hydrate.packages)) {
    $a = Get-AppxPackage -Name ([string]$pkg.name) -AllUsers -ErrorAction SilentlyContinue | Select-Object -First 1
    $store[[string]$pkg.name] = if ($a) { [ordered]@{ installed = $true; version = [string]$a.Version } } else { [ordered]@{ installed = $false } }
    if (-not $a) { $allStore = $false }
}
if ($allStore -and $hydrateTask[1] -and (Get-ScheduledTask -TaskPath $hydrateTask[0] -TaskName $hydrateTask[1] -ErrorAction SilentlyContinue)) {
    Unregister-ScheduledTask -TaskPath $hydrateTask[0] -TaskName $hydrateTask[1] -Confirm:$false
    Log 'every Store package is provisioned; hydrate task removed'
}
$gpu = [ordered]@{ present = $false }
$rec = [string]$x.gpu.record_path
if ($rec -and (Test-Path -LiteralPath $rec)) {
    $g = Get-Content -LiteralPath $rec -Raw -Encoding UTF8 | ConvertFrom-Json
    $gpu = [ordered]@{ present = $true; status = [string](Get-Prop $g 'status') }
}
$idd = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*Looking Glass*' })
$rid = Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } | Select-Object -First 1
# --- reconcile (SYSTEM): the two changes Windows' FIRST desktop sign-in makes necessary; both happen after SetupComplete
# (round-1 boot of build-20261023T224037Z), so they belong to this startup/logon task, never to the SetupComplete stage.
$desk = Get-Prop (Get-Prop $doc 'users') 'desktop'
$deskName = [string](Get-Prop $desk 'name')
$wlPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$persistedAt = $null
# 1. persistent AutoLogon: at SetupComplete Winlogon still holds OOBE's own autologon (DefaultUserName=defaultuser0, no
#    AutoLogonCount); CloudExperienceHostBroker transfers the answer-file AutoLogon (DefaultUserName + LogonCount) at the
#    first sign-in. Once the desktop account holds AutoLogon, AutoLogonCount is deleted so AutoLogon never expires.
if ([bool](Get-Prop $desk 'autologon_persistent') -and $deskName) {
    $w0 = Get-ItemProperty -Path $wlPath -ErrorAction SilentlyContinue
    if ([string](Get-Prop $w0 'AutoAdminLogon') -eq '1' -and [string](Get-Prop $w0 'DefaultUserName') -eq $deskName -and
        $w0 -and @($w0.PSObject.Properties.Name) -contains 'AutoLogonCount') {
        Remove-ItemProperty -Path $wlPath -Name 'AutoLogonCount'
        $persistedAt = [DateTime]::UtcNow.ToString('o')
        Log "AutoLogon of $deskName made persistent (AutoLogonCount deleted)"
    }
}
# 2. the desktop user's hive: the first sign-in initialises theme, wallpaper, search box and content delivery AFTER the
#    profile was copied from Default (round-1 boot: 5 baked DEFAULT_USER values replaced within 90 s of the logon), so
#    every DEFAULT_USER item of the spec is re-applied to HKU\<desktop SID> while that hive is loaded (idempotent).
$userDefaults = [ordered]@{ sid = $null; loaded = $false; applied = @(); failed = @() }
$acct = if ($deskName) { Get-LocalUser -Name $deskName -ErrorAction SilentlyContinue } else { $null }
if ($acct) {
    $userDefaults.sid = $acct.SID.Value
    $hku = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::Users, [Microsoft.Win32.RegistryView]::Registry64)
    $uroot = $hku.OpenSubKey($userDefaults.sid, $true)
    if ($uroot) {
        $userDefaults.loaded = $true
        try {
            foreach ($it in @(@(Get-Prop $doc 'registry') | Where-Object { [string](Get-Prop $_ 'hive') -eq 'DEFAULT_USER' })) {
                $id = '{0}\{1}' -f $it.key, $it.name
                try { if (Set-MiOSUserValue $uroot $it) { $userDefaults.applied += $id } } catch { $userDefaults.failed += ('{0}: {1}' -f $id, $_.Exception.Message) }
            }
        } finally { $uroot.Close() }
        if ($userDefaults.applied.Count) { Log "desktop user defaults re-applied to $($userDefaults.sid): $($userDefaults.applied -join ', ')" }
        if ($userDefaults.failed.Count) { Log "desktop user defaults NOT applied: $($userDefaults.failed -join ' | ')" 'ERROR' }
    }
}
$wl = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction SilentlyContinue
$autologon = [ordered]@{ auto_admin_logon = [string](Get-Prop $wl 'AutoAdminLogon'); user = [string](Get-Prop $wl 'DefaultUserName')
                         count_present = [bool]($wl -and @($wl.PSObject.Properties.Name) -contains 'AutoLogonCount'); persisted_at = $persistedAt }
# reachability from the host: a Running service is not a listening one (RDP, WinRM, sshd ports from the spec / registry)
$listen = [ordered]@{}
$rdpPort = [int](Get-Prop (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -ErrorAction SilentlyContinue) 'PortNumber')
foreach ($pp in @(@('rdp', $rdpPort), @('winrm', [int](Get-Prop (Get-Prop $doc 'winrm') 'port')), @('ssh', [int](Get-Prop (Get-Prop $doc 'ssh') 'port')))) {
    if ($pp[1] -gt 0) { $listen[$pp[0]] = [ordered]@{ port = $pp[1]; listening = [bool]@(Get-NetTCPConnection -State Listen -LocalPort $pp[1] -ErrorAction SilentlyContinue).Count } }
}
# the cached answer file (plain-text SSOT passwords) must be gone after SetupComplete
$answerCache = @(@('Panther\unattend.xml', 'Panther\unattend-original.xml', 'Panther\autounattend.xml', 'System32\Sysprep\unattend.xml') |
    ForEach-Object { Join-Path $env:SystemRoot $_ } | Where-Object { Test-Path -LiteralPath $_ })
$setup = Join-Path $StateDir 'setup-state.json'
Save-Json (Join-Path $StateDir 'provision-state.json') ([ordered]@{
    schema = 'mios-xbox-provision-state/2'; at = [DateTime]::UtcNow.ToString('o'); account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    services = $services; tasks = $tasks; store_packages = $store; gpu_driver = $gpu
    looking_glass_idd = ($idd.Count -gt 0); rid500_enabled = $(if ($rid) { [bool]$rid.Enabled } else { $false })
    autologon = $autologon; user_defaults = $userDefaults; listening = $listen; answer_file_cache = @($answerCache)
    setup = $(if (Test-Path -LiteralPath $setup) { Get-Content -LiteralPath $setup -Raw | ConvertFrom-Json } else { $null })
})
$bad = @($services.Keys | Where-Object { -not $services[$_].present -or $services[$_].status -ne 'Running' })
$deaf = @($listen.Keys | Where-Object { -not $listen[$_].listening })
Log ("provision-state.json written; services not running: {0}; not listening: {1}; rid500_enabled={2}; cached answer files: {3}" -f $(if ($bad.Count) { $bad -join ', ' } else { 'none' }), $(if ($deaf.Count) { $deaf -join ', ' } else { 'none' }), $(if ($rid) { [bool]$rid.Enabled } else { $false }), $answerCache.Count)
if ($script:Serial) { try { $script:Serial.Close() } catch {} }
