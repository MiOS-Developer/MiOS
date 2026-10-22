# AI-hint: MiOS-Xbox Start layout capture on the GPU-less build worker (xb-11, XB-1). Three phases on the throwaway overlay guest, driven only by job\capture.json (capture.py renders it from xbox-spec.json): prepare (admin over WinRM) refuses unless the worker's BuildBranch is the image's build family, moves legacy Start-layout sources aside, applies exactly the spec's Start policy values (the start.folders / start.pins registry items the image gets), creates a local capture account whose random password is generated here and never leaves the guest (the overlay is deleted afterwards), and arms autologon plus a logon task; user (the capture account's own interactive session) waits until StartMenuExperienceHost has written start2.bin and it is stable, exports the Start layout and reads VisiblePlaces; collect (admin over WinRM) verifies the session owner, the hashes and the policy still in effect, and writes out\start2.bin + out\capture.json for the host's read-only copy-out. Every failure throws.
# AI-related: usr/libexec/mios/win/xbox/capture.py, usr/libexec/mios/win/xbox/worker.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/mios.toml ([windows.xbox.start], [windows.xbox.start.capture])
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('prepare', 'user', 'collect')][string]$Phase,
    [string]$Root = ''   # <exchange volume>:\mios-build (prepare, collect)
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version 2.0

$Pub = Join-Path $env:PUBLIC 'mios-capture'
$StartRel = 'AppData\Local\Packages\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\LocalState\start2.bin'
$CvKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$TaskPath = '\MiOS\'
$TaskName = 'MiOS-Xbox-Capture-User'

function Write-Json([string]$Path, $Obj) {
    $tmp = "$Path.tmp"
    [IO.File]::WriteAllText($tmp, ($Obj | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Read-Json([string]$Path) { Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Get-Prop($Obj, [string]$Name) {
    if ($null -eq $Obj) { return $null }
    $p = $Obj.PSObject.Properties | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if ($p) { return $p.Value }
    return $null
}
function Get-RegValue([string]$Key, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Key)) { return $null }
    return Get-Prop (Get-ItemProperty -LiteralPath $Key) $Name
}
function Read-Job([string]$Path) {
    $j = Read-Json $Path
    if ((Get-Prop $j 'schema') -ne 'mios-xbox-capture-job/1') { throw "$Path is not a mios-xbox-capture-job/1 document" }
    return $j
}

function Invoke-Prepare {
    if (-not $Root) { throw '-Root (the exchange mios-build directory) is required' }
    $job = Read-Job (Join-Path $Root 'job\capture.json')
    $cv = Get-ItemProperty -LiteralPath $CvKey
    $family = [string](Get-Prop $cv ([string]$job.family_source))
    if (-not $family) { throw "the worker has no $($job.family_source) value" }
    if ($job.family -and $family -ine [string]$job.family) {
        throw "worker $($job.family_source) '$family' is not the image build family '$($job.family)': capture refused (a blob of another family is wrong)"
    }
    New-Item -ItemType Directory -Force -Path $Pub | Out-Null
    foreach ($f in @('MiOS-Xbox-Capture.ps1', 'capture.json')) { Copy-Item -LiteralPath (Join-Path $Root "job\$f") -Destination (Join-Path $Pub $f) -Force }
    foreach ($f in @('user.json', 'start2.bin', 'export.json')) { Remove-Item -LiteralPath (Join-Path $Pub $f) -Force -ErrorAction SilentlyContinue }

    # 1. legacy Start-layout sources of this (legacy-provisioned) overlay are moved aside: the blob reflects stock Windows + the spec only
    $neutralized = @()
    $defShell = Join-Path $env:SystemDrive 'Users\Default\AppData\Local\Microsoft\Windows\Shell'
    foreach ($p in @((Join-Path $defShell 'LayoutModification.xml'), (Join-Path $defShell 'LayoutModification.json'),   # pin-gate:allow (removal list: legacy sources moved aside on the throwaway worker)
                     (Join-Path $env:SystemDrive "Users\Default\$StartRel"))) {
        if (Test-Path -LiteralPath $p) { Move-Item -LiteralPath $p -Destination "$p.mios-legacy" -Force; $neutralized += $p }
    }
    $legacy = @(
        @('HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer', @('StartLayoutFile', 'LockedStartLayout')),   # pin-gate:allow (removal list)
        @('HKLM:\SOFTWARE\Microsoft\PolicyManager\current\device\Start',
          @('ConfigureStartPins', 'ConfigureStartPins_ProviderSet', 'ConfigureStartPins_WinningProvider', 'StartLayout', 'StartLayout_ProviderSet', 'StartLayout_WinningProvider')),
        @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\GamingConfiguration', @('StartupToGamingHome', 'GamingHomeApp')),   # FSE boots ShellHost instead of StartMenuExperienceHost
        @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System', @('legalnoticecaption', 'legalnoticetext'))   # a logon notice blocks autologon
    )
    foreach ($lv in $legacy) {
        foreach ($n in $lv[1]) {
            $val = Get-RegValue $lv[0] $n
            if ($null -eq $val -or ($n -like 'legalnotice*' -and -not [string]$val)) { continue }
            Remove-ItemProperty -LiteralPath $lv[0] -Name $n -Force
            $neutralized += "$($lv[0])\$n"
        }
    }
    $defNtuser = Join-Path $env:SystemDrive 'Users\Default\NTUSER.DAT'
    if (Test-Path -LiteralPath $defNtuser) {
        & reg.exe load 'HKU\MIOS_CAP_DEF' $defNtuser 2>&1 | Out-Null
        if (-not $LASTEXITCODE) {
            try {
                foreach ($sub in @('Software\Microsoft\Windows\CurrentVersion\GamingConfiguration',
                                   'Software\Microsoft\Windows\CurrentVersion\Start')) {
                    $dk = "Registry::HKEY_USERS\MIOS_CAP_DEF\$sub"
                    if (Test-Path -LiteralPath $dk) { Remove-Item -LiteralPath $dk -Recurse -Force; $neutralized += $dk }
                }
                $adv = 'Registry::HKEY_USERS\MIOS_CAP_DEF\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
                if (Test-Path -LiteralPath $adv) {
                    Set-ItemProperty -LiteralPath $adv -Name 'TaskbarAl' -Value 1 -Type DWord
                    $neutralized += "$adv\TaskbarAl=1"
                }
                $usf = 'Registry::HKEY_USERS\MIOS_CAP_DEF\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
                if (Test-Path -LiteralPath $usf) {
                    $props = Get-ItemProperty -LiteralPath $usf
                    foreach ($p in @($props.PSObject.Properties)) {
                        if ($p.Name -notlike 'PS*' -and [string]$p.Value -match '^C:\\Users\\[^\\]+\\(.+)$') {
                            [Microsoft.Win32.Registry]::SetValue("HKEY_USERS\MIOS_CAP_DEF\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders", $p.Name, "%USERPROFILE%\$($Matches[1])", [Microsoft.Win32.RegistryValueKind]::ExpandString)
                            $neutralized += "$usf\$($p.Name)"
                        }
                    }
                }
            } finally {
                [GC]::Collect(); [GC]::WaitForPendingFinalizers()
                for ($u = 0; $u -lt 5; $u++) { & reg.exe unload 'HKU\MIOS_CAP_DEF' 2>&1 | Out-Null; if (-not $LASTEXITCODE) { break }; Start-Sleep -Seconds 1 }
            }
        }
    }

    # 2. exactly the spec's Start policy values (xbox-spec.json registry items start.folders / start.pins)
    $applied = @()
    foreach ($r in @($job.registry)) {
        $k = 'HKLM:\SOFTWARE\' + [string]$r.key
        if (-not (Test-Path -LiteralPath $k)) { New-Item -Path $k -Force | Out-Null }
        $t = switch ([string]$r.type) { 'REG_DWORD' { 'DWord' } 'REG_SZ' { 'String' } default { throw "registry type $($r.type) unsupported" } }
        New-ItemProperty -LiteralPath $k -Name ([string]$r.name) -Value $r.data -PropertyType $t -Force | Out-Null
        $applied += "$($r.key)\$($r.name)"
    }

    # 3. a throwaway capture account: its random password is generated here and exists only in this overlay's Winlogon
    $name = [string]$job.user
    if (Get-LocalUser -Name $name -ErrorAction SilentlyContinue) { throw "account $name already exists on the worker (refusing to reuse it)" }
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = 'Mc-' + ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x') + '-9a'
    New-LocalUser -Name $name -Password (ConvertTo-SecureString -String $secret -AsPlainText -Force) -PasswordNeverExpires -AccountNeverExpires `
        -Description 'MiOS-Xbox Start layout capture' | Out-Null
    try { Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $name } catch { if ($_.Exception.Message -notmatch 'already') { throw } }
    $acl = & icacls.exe $Pub /grant ('{0}:(OI)(CI)M' -f $name) 2>&1
    if ($LASTEXITCODE) { throw "icacls $Pub failed: $acl" }

    # 4. autologon of the capture account on the next boot; first-logon animation and privacy experience off
    $wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty -LiteralPath $wl -Name AutoAdminLogon -Value '1' -Type String
    Set-ItemProperty -LiteralPath $wl -Name DefaultUserName -Value $name -Type String
    Set-ItemProperty -LiteralPath $wl -Name DefaultDomainName -Value $env:COMPUTERNAME -Type String
    Set-ItemProperty -LiteralPath $wl -Name DefaultPassword -Value $secret -Type String
    foreach ($n in @('AutoLogonCount', 'AutoLogonSID', 'ForceAutoLogon')) { Remove-ItemProperty -LiteralPath $wl -Name $n -ErrorAction SilentlyContinue }
    foreach ($kv in @(@('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device', 'DevicePasswordLessBuildVersion', 0),
                      @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System', 'EnableFirstLogonAnimation', 0),
                      @('HKLM:\SOFTWARE\Policies\Microsoft\Windows\OOBE', 'DisablePrivacyExperience', 1))) {
        if (-not (Test-Path -LiteralPath $kv[0])) { New-Item -Path $kv[0] -Force | Out-Null }
        Set-ItemProperty -LiteralPath $kv[0] -Name $kv[1] -Value $kv[2] -Type DWord
    }

    # 5. the user phase runs inside the capture account's interactive session at logon
    Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $who = "$env:COMPUTERNAME\$name"
    $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Phase user' -f (Join-Path $Pub 'MiOS-Xbox-Capture.ps1'))
    $t = New-ScheduledTaskTrigger -AtLogOn -User $who
    $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $a -Trigger $t -Settings $s -User $name -Password $secret -Force | Out-Null
    } catch {
        $p = New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
    }
    $secret = $null

    # RunOnce registry fallback (runs in the interactive desktop session of the autologon user)
    Set-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'MiOS-Xbox-Capture' `
        -Value ('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Phase user' -f (Join-Path $Pub 'MiOS-Xbox-Capture.ps1')) -Type String

    # Startup folder fallback (runs on interactive desktop launch)
    $startupDir = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup'
    if (Test-Path -LiteralPath $startupDir) {
        $cmdLine = ('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Phase user' -f (Join-Path $Pub 'MiOS-Xbox-Capture.ps1'))
        Set-Content -LiteralPath (Join-Path $startupDir 'mios-capture.cmd') -Value "@echo off`r`n$cmdLine`r`n" -Force
    }

    $rec = [ordered]@{
        schema = 'mios-xbox-capture-prepare/1'; family = $family; build = [string](Get-Prop $cv 'CurrentBuild'); ubr = [string](Get-Prop $cv 'UBR')
        edition = [string](Get-Prop $cv 'EditionID'); user = $name; neutralized = $neutralized; applied = $applied; at = [DateTime]::UtcNow.ToString('o')
    }
    Write-Json (Join-Path $Root 'out\prepare.json') $rec
    $rec | ConvertTo-Json -Compress -Depth 4
}

function Open-StartOnce {
    try {
        $sh = New-Object -ComObject Shell.Application
        $sh.ToggleDesktop()
        Start-Sleep -Seconds 2
    } catch {}
    if (-not ('MiOS.CaptureKbd' -as [type])) {
        Add-Type -Namespace MiOS -Name CaptureKbd -MemberDefinition '[DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);'
    }
    [MiOS.CaptureKbd]::keybd_event(0x5B, 0, 0, [UIntPtr]::Zero); [MiOS.CaptureKbd]::keybd_event(0x5B, 0, 2, [UIntPtr]::Zero)
    Start-Sleep -Seconds 10
    [MiOS.CaptureKbd]::keybd_event(0x1B, 0, 0, [UIntPtr]::Zero); [MiOS.CaptureKbd]::keybd_event(0x1B, 0, 2, [UIntPtr]::Zero)
}

function Invoke-User {
    $uj = Join-Path $Pub 'user.json'
    if (Test-Path -LiteralPath $uj) {
        $curr = Read-Json $uj
        if ((Get-Prop $curr 'status') -eq 'running' -or (Get-Prop $curr 'status') -eq 'done') { return }
    }
    $res = [ordered]@{ schema = 'mios-xbox-capture-user/1'; status = 'running'; user = $env:USERNAME; started = [DateTime]::UtcNow.ToString('o') }
    Write-Json $uj $res
    try {
        $job = Read-Job (Join-Path $Pub 'capture.json')
        $f = Join-Path $env:USERPROFILE $StartRel
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $maxSec = [int]$job.logon_timeout_s
        $last = ''; $since = 0; $opened = $false; $stable = $false
        while ($sw.Elapsed.TotalSeconds -lt $maxSec) {
            if (Test-Path -LiteralPath $f) {
                $h = Get-Sha256 $f
                if ($h -ne $last) { $last = $h; $since = $sw.Elapsed.TotalSeconds }
                elseif (($sw.Elapsed.TotalSeconds - $since) -ge [int]$job.settle_s) { $stable = $true; break }
            } elseif ($sw.Elapsed.TotalSeconds -ge [int]$job.open_start_after_s -and (-not $opened -or [int]($sw.Elapsed.TotalSeconds) % 60 -lt 6)) {
                Open-StartOnce
                $opened = $true
            }
            Start-Sleep -Seconds 5
        }
        $res.opened_start = $opened
        if (-not $stable) { throw "start2.bin was not written (or never settled) within $($job.logon_timeout_s)s" }
        $ep = Join-Path $Pub 'export.json'
        Export-StartLayout -Path $ep
        $res.export = Read-Json $ep
        $vp = Get-RegValue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Start' 'VisiblePlaces'
        if ($null -ne $vp) { $res.visible_places = ([BitConverter]::ToString([byte[]]$vp)).Replace('-', '').ToLowerInvariant() } else { $res.visible_places = $null }
        $copy = Join-Path $Pub 'start2.bin'
        Copy-Item -LiteralPath $f -Destination $copy -Force
        $res.start2_sha256 = Get-Sha256 $copy
        if ($res.start2_sha256 -ne (Get-Sha256 $f)) { throw 'start2.bin changed while it was copied' }
        $res.status = 'done'
    } catch {
        $res.status = 'failed'
        $res.error = $_.Exception.Message
    }
    $res.finished = [DateTime]::UtcNow.ToString('o')
    Write-Json $uj $res
}

function Invoke-Collect {
    if (-not $Root) { throw '-Root (the exchange mios-build directory) is required' }
    $job = Read-Job (Join-Path $Root 'job\capture.json')
    $prep = Read-Json (Join-Path $Root 'out\prepare.json')
    $uj = Join-Path $Pub 'user.json'
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $maxSec = [int]$job.logon_timeout_s + 300
    $u = $null
    while ($sw.Elapsed.TotalSeconds -lt $maxSec) {
        if (Test-Path -LiteralPath $uj) {
            $u = Read-Json $uj
            if ((Get-Prop $u 'status') -ne 'running') { break }
        }
        Start-Sleep -Seconds 10
    }
    $startupCmd = Join-Path (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Startup') 'mios-capture.cmd'
    if (Test-Path -LiteralPath $startupCmd) { Remove-Item -LiteralPath $startupCmd -Force -ErrorAction SilentlyContinue }
    Remove-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'MiOS-Xbox-Capture' -Force -ErrorAction SilentlyContinue

    $owners = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
            $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner; '{0}\{1}' -f $o.Domain, $o.User })
    if ($null -eq $u -or (Get-Prop $u 'status') -eq 'running') { throw "the capture session wrote no result within the timeout (explorer sessions: $($owners -join ', '))" }
    if ((Get-Prop $u 'status') -ne 'done') { throw "the capture session failed: $(Get-Prop $u 'error')" }
    if ([string](Get-Prop $u 'user') -ine [string]$job.user) { throw "the capture ran as $(Get-Prop $u 'user'), not $($job.user)" }
    if (-not ($owners | Where-Object { ($_ -split '\\')[-1] -ieq [string]$job.user })) { throw "no explorer.exe of $($job.user) (sessions: $($owners -join ', '))" }
    $prof = Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -and (Split-Path -Leaf $_.LocalPath) -ieq [string]$job.user } | Select-Object -First 1
    if (-not $prof) { throw "no profile of $($job.user)" }
    $copy = Join-Path $Pub 'start2.bin'
    $live = Join-Path $prof.LocalPath $StartRel
    $sha = Get-Sha256 $copy
    if ($sha -ne [string](Get-Prop $u 'start2_sha256') -or $sha -ne (Get-Sha256 $live)) { throw 'start2.bin differs between the session record, its copy and the live profile' }
    $observed = @()
    foreach ($r in @($job.registry)) {
        $observed += [ordered]@{ key = [string]$r.key; name = [string]$r.name; value = (Get-RegValue ('HKLM:\SOFTWARE\' + [string]$r.key) ([string]$r.name)) }
    }
    $out = Join-Path $Root 'out'
    $dst = Join-Path $out 'start2.bin'
    Copy-Item -LiteralPath $copy -Destination $dst -Force
    if ((Get-Sha256 $dst) -ne $sha) { throw 'start2.bin copy on the exchange volume is corrupt' }
    $rec = [ordered]@{
        schema = 'mios-xbox-start-capture/1'; status = 'done'; family = [string]$prep.family; family_source = [string]$job.family_source
        build = [string]$prep.build; ubr = [string]$prep.ubr; edition = [string]$prep.edition; user = [string]$job.user
        captured_at = [string](Get-Prop $u 'finished'); explorer_sessions = $owners; start2_sha256 = $sha; start2_size = (Get-Item -LiteralPath $dst).Length
        pinned_export = (Get-Prop $u 'export'); visible_places = (Get-Prop $u 'visible_places'); opened_start = [bool](Get-Prop $u 'opened_start')
        policy_observed = $observed; neutralized_legacy = @($prep.neutralized)
    }
    Write-Json (Join-Path $out 'capture.json') $rec
    $rec | ConvertTo-Json -Compress -Depth 8
}

switch ($Phase) {
    'prepare' { Invoke-Prepare }
    'user' { Invoke-User }
    'collect' { Invoke-Collect }
}
