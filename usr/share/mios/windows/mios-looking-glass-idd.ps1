# AI-hint: MiOS-Xbox guest side of Looking Glass: installs the upstream Looking Glass IDD (Indirect Display Driver) build that matches the host client's commit, after verifying the download's SHA-256 and the installer's Authenticode signature, and retires the legacy B-series host service. Never enables test-signing or disables driver signature enforcement.
# AI-related: usr/libexec/mios/vfio/setup-looking-glass.py (--provision-guest resolves Url/Sha256/Version for the client's commit and runs this over WinRM), automation/69-bake-lookingglass-client.sh, usr/share/mios/mios.toml [vm.looking_glass]
param(
    [Parameter(Mandatory = $true)][string]$Url,        # https://looking-glass.io/artifact/<version>/idd (a zip)
    [Parameter(Mandatory = $true)][string]$Sha256,     # SHA-256 of that zip, published by looking-glass.io
    [Parameter(Mandatory = $true)][string]$Version,    # upstream artifact id, e.g. B7-829-cdba29f2
    [string]$Signer = 'CN=HostFission',                # Authenticode subject of upstream's code-signing certificate
    [string]$Package = '',                             # offline bake: local copy of the zip (verified against Sha256 exactly like a download)
    [int]$ExclusiveMonitor = 0,                        # 0 = keep the emulated console display (VNC: virt-manager/Cockpit) and other outputs; [vm.looking_glass] idd_exclusive
    [int]$MirrorDisplays = 1,                          # 1 = Windows Duplicate (clone) topology at every sign-in: Looking Glass (IDD), the emulated VNC/SPICE console and the dGPU output show the same picture; [vm.looking_glass] mirror_displays
    [switch]$Force
)
# Operator decision 2026-10-16: trust exactly this upstream publisher certificate (CN=HostFission, Sectigo-issued,
# valid to 2027-08-23) in LocalMachine\TrustedPublisher so the IDD's Authenticode-signed (not Microsoft-attested)
# driver catalogs install silently -- the same as clicking "Always trust" once. A different thumbprint (upstream
# certificate rotation) stops provisioning and needs a new operator decision. Root store, test-signing and driver
# signature enforcement are never touched.
$TrustedPublisherThumbprint = '57476AACDE410C05A63E9FEC8B97938605B9D8F5'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Say([string]$m) { Write-Output "[mios-lg-idd] $m" }

# BEGIN Protect-MiOSDir (setup-looking-glass.py also sends this function ahead of the staged .b64 chunks)
# A directory only SYSTEM and Administrators can write (UsersRead adds BUILTIN\Users read/execute). %ProgramData% lets a
# standard user pre-create folders and keep owner rights in them, so an existing directory that is a reparse point,
# not owned by SYSTEM/Administrators, or grants anyone else access is moved aside and recreated (Quarantine) or taken over in place.
function Protect-MiOSDir([string]$Path, [bool]$UsersRead = $false, [bool]$Quarantine = $true) {
    $adm = @('S-1-5-18', 'S-1-5-32-544'); $usr = 'S-1-5-32-545'
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($item) {
        $ok = $false
        try {
            if ($item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                $a = $item.GetAccessControl()
                $bad = @($a.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) | Where-Object {
                    $id = $_.IdentityReference.Value
                    -not ($id -in $adm -or ($UsersRead -and $id -eq $usr -and $_.AccessControlType -eq 'Allow' -and ([long]$_.FileSystemRights -band 0x500D0156) -eq 0))
                })
                $ok = ($a.GetOwner([Security.Principal.SecurityIdentifier]).Value -in $adm) -and $bad.Count -eq 0
            }
        } catch { $ok = $false }
        if (-not $ok) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                if (-not $Quarantine) { throw "$Path is a reparse point (junction/symlink); refusing" }
                cmd /c rmdir "$Path" | Out-Null   # removes the link itself, never its target
            } else {
                takeown /F "$Path" /A | Out-Null
                icacls "$Path" /grant '*S-1-5-32-544:F' /Q | Out-Null
                if ($Quarantine) {
                    $q = "$Path.untrusted-$([DateTime]::UtcNow.Ticks)"
                    Rename-Item -LiteralPath $Path -NewName (Split-Path -Leaf $q)
                    cmd /c rd /s /q "$q" 2>$null | Out-Null   # rd /s does not follow junctions
                }
            }
            Write-Output "[mios-lg-idd] $Path was not restricted to SYSTEM/Administrators; secured"
        }
    }
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }
    $n = New-Object Security.AccessControl.DirectorySecurity
    $n.SetAccessRuleProtection($true, $false)
    $grants = @($adm | ForEach-Object { , @($_, 'FullControl') })
    if ($UsersRead) { $grants += , @($usr, 'ReadAndExecute') }
    foreach ($g in $grants) {
        $n.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier $g[0]), $g[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    [IO.Directory]::SetAccessControl($Path, $n)
}
# END Protect-MiOSDir

# Split a registry UninstallString into the executable and its arguments: '"C:\x y\u.exe" /S _?=C:\x y',
# 'C:\x y\u.exe /S' and a bare path are all valid forms.
function Split-CommandLine([string]$Cmd) {
    $Cmd = $Cmd.Trim()
    if ($Cmd.StartsWith('"')) {
        $end = $Cmd.IndexOf('"', 1)
        if ($end -lt 1) { throw "unbalanced quote in command line: $Cmd" }
        return , @($Cmd.Substring(1, $end - 1), $Cmd.Substring($end + 1).Trim())
    }
    $m = [regex]::Match($Cmd, '^(.+?\.exe)(?:\s+(.*))?$', 'IgnoreCase')
    if ($m.Success) { return , @($m.Groups[1].Value, $m.Groups[2].Value.Trim()) }
    $sp = $Cmd.IndexOf(' ')
    if ($sp -lt 0) { return , @($Cmd, '') }
    return , @($Cmd.Substring(0, $sp), $Cmd.Substring($sp + 1).Trim())
}

# Preferences are applied on every run, installed or not (idempotent; the IDD reads them when its device starts).
function Set-IddPreferences([int]$Exclusive) {
    $key = 'HKLM:\SOFTWARE\LookingGlass\IDD'
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    $cur = (Get-ItemProperty -Path $key -Name 'ExclusiveMonitor' -ErrorAction SilentlyContinue).ExclusiveMonitor
    if ($cur -ne $Exclusive) {
        New-ItemProperty -Path $key -Name 'ExclusiveMonitor' -PropertyType DWord -Value $Exclusive -Force | Out-Null
        Say "IDD ExclusiveMonitor = $Exclusive (was $(if ($null -eq $cur) { 'unset' } else { $cur }); takes effect when the IDD device restarts)"
    } else { Say "IDD ExclusiveMonitor = $Exclusive (unchanged)" }
}

# Driver signature enforcement must stay on: report, never change.
$bcd = (bcdedit /enum '{current}') -join "`n"
if ($bcd -match '(?im)^\s*(testsigning|nointegritychecks)\s+Yes') { throw "test-signing/nointegritychecks is enabled in the BCD; refusing (MiOS never relies on it)" }

$uninst = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
# The NSIS installers are 32-bit: their uninstall entries live in the WOW6432Node view.
$uninstRoots = @($uninst, 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')
function Get-Uninstall([string]$name) {
    foreach ($r in $uninstRoots) { $e = Get-ItemProperty "$r\$name" -ErrorAction SilentlyContinue; if ($e) { return $e } }
    return $null
}
# Artifact ids look like B7-829-cdba29f2; the installer records B7-829-gcdba29f2f6 (git describe): compare the commit.
$commit = ($Version -split '-')[-1].ToLower()
# The emulated console display stays on (ExclusiveMonitor 0) so sign-in/recovery is always reachable over VNC.
Set-IddPreferences $ExclusiveMonitor
$idd = Get-Uninstall 'Looking Glass (IDD)'
if ($idd -and ($idd.DisplayVersion.ToLower() -match [regex]::Escape($commit)) -and -not $Force) {
    Say "Looking Glass IDD $Version already installed"
} else {
    Protect-MiOSDir (Join-Path $env:ProgramData 'MiOS') $true $false
    Protect-MiOSDir (Join-Path $env:ProgramData 'MiOS\looking-glass')
    $dir = Join-Path $env:ProgramData "MiOS\looking-glass\$Version"
    Protect-MiOSDir $dir
    $zip = Join-Path $dir 'looking-glass-idd.zip'
    if ($Package) {
        if (-not (Test-Path -LiteralPath $Package -PathType Leaf)) { throw "package $Package missing" }
        Say "using staged package $Package"
        Copy-Item -LiteralPath $Package -Destination $zip -Force
    } else {
        Say "downloading $Url"
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $zip
    }
    $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($got -ne $Sha256.ToLower()) { throw "SHA-256 mismatch: got $got, expected $Sha256" }
    Say "sha256 $got ok"
    Expand-Archive -Force -Path $zip -DestinationPath $dir
    $exe = Get-ChildItem -Path $dir -Recurse -Filter 'looking-glass-idd-setup.exe' | Select-Object -First 1
    if (-not $exe) { throw "looking-glass-idd-setup.exe not found in $zip" }
    $sig = Get-AuthenticodeSignature $exe.FullName
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch [regex]::Escape($Signer)) {
        throw "installer signature not trusted: $($sig.Status) $($sig.SignerCertificate.Subject)"
    }
    Say "installer signature Valid: $($sig.SignerCertificate.Subject) (issuer $($sig.SignerCertificate.Issuer))"

    $cert = $sig.SignerCertificate
    if ($cert.Thumbprint -ne $TrustedPublisherThumbprint) {
        throw "publisher certificate $($cert.Thumbprint) is not the operator-approved $TrustedPublisherThumbprint; refusing to trust it (needs a new operator decision)"
    }
    $tp = Get-ChildItem Cert:\LocalMachine\TrustedPublisher | Where-Object Thumbprint -eq $TrustedPublisherThumbprint
    if (-not $tp) {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store('TrustedPublisher', 'LocalMachine')
        $store.Open('ReadWrite'); $store.Add($cert); $store.Close()
        Say "trusted publisher added: $($cert.Subject) $($cert.Thumbprint)"
    } else { Say "publisher already trusted: $($cert.Thumbprint)" }

    # One Looking Glass server per IVSHMEM device: remove the legacy host application.
    $legacy = Get-Uninstall 'Looking Glass (host)'
    if (Get-Service -Name 'Looking Glass (host)' -ErrorAction SilentlyContinue) {
        Stop-Service -Name 'Looking Glass (host)' -Force -ErrorAction SilentlyContinue
        Set-Service -Name 'Looking Glass (host)' -StartupType Disabled
    }
    if ($legacy -and $legacy.UninstallString) {
        $u, $uargs = Split-CommandLine $legacy.UninstallString
        if ($uargs -notmatch '(^|\s)/S(\s|$)') { $uargs = "$uargs /S".Trim() }
        if (-not (Test-Path -LiteralPath $u -PathType Leaf)) { throw "legacy host uninstaller '$u' (from '$($legacy.UninstallString)') does not exist" }
        Say "uninstalling legacy host $($legacy.DisplayVersion) ($u $uargs)"
        $p = Start-Process -FilePath $u -ArgumentList $uargs -Wait -PassThru
        # NSIS uninstallers copy themselves to %TEMP% and return at once; wait for the copy to finish.
        for ($i = 0; $i -lt 60 -and (Get-Uninstall 'Looking Glass (host)'); $i++) { Start-Sleep 1 }
        Say "legacy host uninstaller exit $($p.ExitCode)"
    }

    # The bundled IVSHMEM driver is only added when no working one is bound already.
    $ivshmem = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'PCI\VEN_1AF4&DEV_1110*' }
    $setupArgs = @('/S')
    if (-not ($ivshmem | Where-Object Status -eq 'OK')) { $setupArgs += '/ivshmem'; Say "no working IVSHMEM driver bound; installing the bundled one" }
    Say "running $($exe.Name) $($setupArgs -join ' ')"
    $p = Start-Process -FilePath $exe.FullName -ArgumentList $setupArgs -Wait -PassThru
    Say "installer exit $($p.ExitCode)"
    if ($p.ExitCode -notin 0, 3010, 12) {
        $why = Get-Content "$env:windir\INF\setupapi.dev.log" -Tail 300 -ErrorAction SilentlyContinue | Select-String -Pattern '0xE0000242' | Select-Object -Last 1
        if ($why) {
            # The driver catalogs are Authenticode-signed by the upstream publisher (not Microsoft-attested); PnP asks
            # the user to trust that publisher, which a silent install cannot answer. Trusting it is the operator's call.
            throw "IDD installer exit $($p.ExitCode): Windows has not established '$($sig.SignerCertificate.Subject)' ($($sig.SignerCertificate.Thumbprint)) as a trusted driver publisher (0xE0000242). Run $($exe.FullName) interactively in the guest and accept the publisher prompt, or have the operator approve trusting this publisher, then re-run."
        }
        throw "IDD installer failed with exit code $($p.ExitCode)"
    }
    if ($p.ExitCode -ne 0) { Say "a Windows restart is required to finish the driver install" }
}

# The NSIS installer can return before it writes its uninstall entry; wait for it.
for ($i = 0; $i -lt 60; $i++) {
    $idd = Get-Uninstall 'Looking Glass (IDD)'
    if ($idd -and $idd.InstallLocation) { break }
    Start-Sleep 1
}
if (-not ($idd -and $idd.InstallLocation)) { throw "Looking Glass (IDD) uninstall entry did not appear after install" }
Say "installed: $($idd.DisplayName) $($idd.DisplayVersion) at $($idd.InstallLocation)"
foreach ($cat in 'lgidd.cat', 'LGInput.cat') {
    $f = Join-Path "$($idd.InstallLocation)" $cat
    if (Test-Path $f) { $s = Get-AuthenticodeSignature $f; Say "$cat signature $($s.Status): $($s.SignerCertificate.Subject)" }
}
$drv = (pnputil /enum-drivers) -join "`n"
foreach ($block in ($drv -split "`n\s*`n")) {
    if ($block -match '(?i)Original Name:\s*(lgidd|lginput|ivshmem)\.inf') { Say (($block -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ' | ') }
}
Get-PnpDevice -PresentOnly | Where-Object { $_.FriendlyName -match 'Looking Glass|IVSHMEM' } | ForEach-Object { Say "device: $($_.Class) $($_.FriendlyName) $($_.Status)" }
Get-Service -Name 'Looking Glass (host)', 'LGIddHelper' -ErrorAction SilentlyContinue | ForEach-Object { Say "service: $($_.Name) $($_.Status) $($_.StartType)" }

# -- Mirror every guest display (Looking Glass IDD, emulated VNC/SPICE console, dGPU output) -------------
# Windows only applies a display topology inside a signed-in session and remembers it afterwards (the
# sign-in screen then uses it too), so a scheduled task applies Duplicate at every sign-in, for any user.
$mirrorTask = 'MiOS display mirror'
if ($MirrorDisplays) {
    # Every user runs this script at sign-in: only administrators may write it (users read/execute).
    Protect-MiOSDir (Join-Path $env:ProgramData 'MiOS') $true $false
    $mirrorDir = Join-Path $env:ProgramData 'MiOS\display'
    Protect-MiOSDir $mirrorDir $true
    $mirrorScript = Join-Path $mirrorDir 'mios-display-mirror.ps1'
    Set-Content -Encoding UTF8 -Path $mirrorScript -Value @'
# AI-hint: MiOS-Xbox: put every active guest display in Windows Duplicate (clone) topology via SetDisplayConfig; DisplaySwitch.exe /clone only when the console desktop is not ready (ERROR_ACCESS_DENIED).
$log = Join-Path $env:TEMP 'mios-display-mirror.log'   # the script dir is read-only for users
function Write-MiOSMirrorLog([string]$Line) { "$(Get-Date -Format s) $Line" | Out-File -Append -Encoding utf8 $log }
try {
    Add-Type -Namespace MiOS -Name Display -MemberDefinition @"
[DllImport("user32.dll")] public static extern int SetDisplayConfig(uint numPathArrayElements, System.IntPtr pathArray, uint numModeInfoArrayElements, System.IntPtr modeInfoArray, uint flags);
[DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
"@
} catch {
    Write-MiOSMirrorLog "display API unavailable: $($_.Exception.Message)"
    return
}
# SM_CMONITORS (80): monitors on this desktop; a Duplicate pair counts once. One (a GPU-less guest, the IDD not up yet)
# or a pair already duplicated leaves nothing to clone. Asking anyway made SetDisplayConfig return 87 and the
# DisplaySwitch fallback leave "Your PC can't project to another screen" open at every sign-in (round-1 debug boot).
$monitors = [MiOS.Display]::GetSystemMetrics(80)
if ($monitors -lt 2) {
    Write-MiOSMirrorLog "$monitors display(s) on the desktop: nothing to duplicate"
    return
}
# SDC_APPLY (0x80) | SDC_TOPOLOGY_CLONE (0x2) | SDC_ALLOW_CHANGES (0x400) | SDC_PATH_PERSIST_IF_REQUIRED (0x800).
# SDC_SAVE_TO_DATABASE (0x200) is only valid with SDC_USE_SUPPLIED_DISPLAY_CONFIG (ERROR_INVALID_PARAMETER here);
# a topology request is remembered by Windows on its own.
$rc = [MiOS.Display]::SetDisplayConfig(0, [System.IntPtr]::Zero, 0, [System.IntPtr]::Zero, 0xC82)
Write-MiOSMirrorLog "$monitors displays; SetDisplayConfig(clone 0xC82) = $rc"
# ERROR_ACCESS_DENIED (5): no console desktop yet -- Windows' own switcher applies the topology once it is. Any other
# failure is recorded only: DisplaySwitch.exe cannot clone what SetDisplayConfig refused and would just raise its flyout.
if ($rc -eq 5) {
    try {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\DisplaySwitch.exe') -ArgumentList '/clone' -Wait
        Write-MiOSMirrorLog "DisplaySwitch.exe /clone (fallback after rc=5)"
    } catch {
        Write-MiOSMirrorLog "DisplaySwitch.exe exception: $($_.Exception.Message)"
    }
}
'@
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$mirrorScript`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -GroupId 'S-1-5-32-545' -RunLevel Limited   # BUILTIN\Users: runs in the signing-in user's session
    Register-ScheduledTask -TaskName $mirrorTask -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
    Say "display mirror task registered ($mirrorScript, at every sign-in)"
} elseif (Get-ScheduledTask -TaskName $mirrorTask -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $mirrorTask -Confirm:$false
    Say "display mirror task removed"
}
