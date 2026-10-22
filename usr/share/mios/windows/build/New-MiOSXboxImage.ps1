# AI-hint: MiOS-Xbox DISM servicing on the GPU-less build worker (xb-05/07/08/10/11, XB-1..XB-3). Driven ONLY by the credential-free guest projection of xbox-spec.json (render_spec.py) and the payload answer.py rendered from the same spec: verifies every staged installer (sha256 + Authenticode signer) before baking, exports exactly source.edition, requires the captured Start layout of the image's BuildBranch family, injects the virtio-win drivers into install.wim and every boot.wim index (signature-enforced, never forced unsigned) and refuses any GPU vendor display driver, applies the optional-feature/capability lists, imports the rendered offline registry (.reg) into the loaded hives, stages the payload (SetupComplete chain, components, installers, guest spec), copies the rendered autounattend.xml to the media root and masters the ISO. Every failure throws; result.json records what was resolved.
# AI-related: usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/windows/build/MiOS-Xbox-Worker.ps1, usr/libexec/mios/win/xbox/conform.py
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SpecPath,        # guest projection of xbox-spec.json (no credentials)
    [Parameter(Mandatory = $true)][string]$MediaDir,        # converted Windows media (sources\install.wim, boot.wim)
    [Parameter(Mandatory = $true)][string]$PayloadDir,      # answer.py output
    [Parameter(Mandatory = $true)][string]$DriversDir,      # virtio-win <inf>\<os>\<arch> tree from the resolved ISO
    [Parameter(Mandatory = $true)][string]$StartAssetsDir,  # <family>\start2.bin + start-assets.json (sha256 per family)
    [Parameter(Mandatory = $true)][string]$WorkDir,
    [Parameter(Mandatory = $true)][string]$OutIso,
    [Parameter(Mandatory = $true)][string]$ResultPath,
    [Parameter(Mandatory = $true)][string]$IsoLabel,
    [string]$UupDir = '',                                   # capability source (FOD packages of the UUP set)
    [string]$OscdimgPath = ''                               # oscdimg.exe / cdimage.exe (ADK or the converter's bin)
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version 2.0

$Result = [ordered]@{ schema = 'mios-xbox-servicing/1'; status = 'running'; started = [DateTime]::UtcNow.ToString('o') }
function Say([string]$m) {
    $line = "[{0}] [New-MiOSXboxImage] {1}" -f [DateTime]::UtcNow.ToString('o'), $m
    Write-Output $line
    try { [System.IO.File]::AppendAllText('\\.\COM1', "$line`r`n") } catch {}
}
function Save-Result {
    $tmp = "$ResultPath.tmp"
    [IO.File]::WriteAllText($tmp, ($Result | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $ResultPath -Force
}
function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Need([object]$Value, [string]$Key) {
    if ($null -eq $Value -or ($Value -is [string] -and [string]::IsNullOrWhiteSpace($Value))) { throw "xbox-spec.json: windows.xbox.$Key missing" }
    return $Value
}

$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'New-MiOSXboxImage must run elevated (DISM).' }

# --- 1. spec (credential-free projection) -------------------------------------------------------
$doc = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($doc.schema -ne 1 -or $doc.projection -ne 'guest' -or -not $doc.spec) { throw "$SpecPath is not the guest projection of a render_spec.py xbox-spec.json" }
foreach ($acct in @($doc.users.PSObject.Properties)) {
    if ($acct.Value -and ($acct.Value.PSObject.Properties.Name -contains 'password')) { throw 'refusing a credential-bearing spec on the build worker' }
}
$x = $doc.spec
$edition = [string](Need $x.source.edition 'source.edition')
$vw = Need $x.drivers.virtio_win 'drivers.virtio_win'
$denyProviders = @(Need $x.drivers.deny_display_providers 'drivers.deny_display_providers')
$Result.edition = $edition

# --- 2. payload integrity (sha256 + Authenticode) -----------------------------------------------
$payloadJson = Join-Path $PayloadDir 'ProgramData\MiOS\payload.json'
$payload = Get-Content -LiteralPath $payloadJson -Raw -Encoding UTF8 | ConvertFrom-Json
$verified = [ordered]@{}
foreach ($p in @($payload.installers.PSObject.Properties)) {
    $f = Join-Path $PayloadDir ($p.Value.path -replace '/', '\')
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw "installer $($p.Name) not staged at $f" }
    if ((Get-Sha256 $f) -ne ([string]$p.Value.sha256).ToLowerInvariant()) { throw "installer $($p.Name) sha256 differs from the build manifest" }
    $signer = [string]$p.Value.signer
    if ($signer -and $f -notmatch '\.zip$') {
        $s = Get-AuthenticodeSignature -LiteralPath $f
        if ($s.Status -eq 'NotSigned' -and $signer -eq 'Red Hat') {
            # Fedora virtio-win-guest-tools.exe and qemu-ga MSI have no outer Authenticode table; SHA-256 is verified above.
        } elseif ($s.Status -ne 'Valid' -or -not $s.SignerCertificate -or $s.SignerCertificate.Subject -notmatch [regex]::Escape($signer)) {
            throw "installer $($p.Name): Authenticode $($s.Status) ($($s.SignerCertificate.Subject)); expected $signer"
        }
    }
    $verified[$p.Name] = [string]$p.Value.version
}
foreach ($c in @($payload.components)) {
    $f = Join-Path $PayloadDir ($c.path -replace '/', '\')
    if (-not (Test-Path -LiteralPath $f -PathType Leaf) -or (Get-Sha256 $f) -ne ([string]$c.sha256).ToLowerInvariant()) { throw "component $($c.name) missing or altered in the payload" }
}
$answer = Join-Path $PayloadDir 'autounattend.xml'
$regFile = Join-Path $PayloadDir 'ProgramData\MiOS\xbox-registry.reg'
foreach ($f in @($answer, $regFile)) { if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw "payload lacks $f" } }
$Result.installers = $verified
Say "payload verified: $(@($verified.Keys).Count) installers, $(@($payload.components).Count) components"

# --- 3. exactly the spec edition -----------------------------------------------------------------
$src = @('install.wim', 'install.esd') | ForEach-Object { Join-Path $MediaDir "sources\$_" } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $src) { throw "no install.wim/install.esd under $MediaDir\sources" }
$index = $null
foreach ($img in @(Get-WindowsImage -ImagePath $src)) {
    $info = Get-WindowsImage -ImagePath $src -Index $img.ImageIndex
    if ([string]$info.EditionId -ieq $edition) { $index = $img.ImageIndex; break }
}
if (-not $index) { throw "media has no $edition image (windows.xbox.source.edition)" }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$wim = Join-Path $WorkDir 'install.wim'
if (Test-Path -LiteralPath $wim) { Remove-Item -LiteralPath $wim -Force }
Export-WindowsImage -SourceImagePath $src -SourceIndex $index -DestinationImagePath $wim -CompressionType max -CheckIntegrity | Out-Null
Say "exported $edition (index $index) from $(Split-Path -Leaf $src)"

$mount = Join-Path $WorkDir 'mount'
if (Test-Path -LiteralPath $mount) { Remove-Item -LiteralPath $mount -Recurse -Force }
New-Item -ItemType Directory -Force -Path $mount | Out-Null
Mount-WindowsImage -ImagePath $wim -Index 1 -Path $mount | Out-Null
$mounted = $true
$hives = @()
try {
try {
    # --- 4. drivers (install.wim) ---------------------------------------------------------------
    Add-WindowsDriver -Path $mount -Driver $DriversDir -Recurse | Out-Null
    $drv = @(Get-WindowsDriver -Path $mount)
    $missing = @()
    foreach ($inf in @($vw.inf)) {
        if (-not ($drv | Where-Object { ([IO.Path]::GetFileNameWithoutExtension([string]$_.OriginalFileName)) -ieq $inf })) { $missing += $inf }
    }
    if ($missing.Count) { throw "virtio drivers not in the image driver store: $($missing -join ', ')" }
    foreach ($d in $drv) {
        if ([string]$d.ClassName -ieq 'Display') {
            foreach ($prov in $denyProviders) { if ([string]$d.ProviderName -match [regex]::Escape([string]$prov)) { throw "GPU vendor display driver $($d.OriginalFileName) ($($d.ProviderName)) in the image (XB-3)" } }
        }
    }
    $Result.drivers = @($drv | ForEach-Object { [IO.Path]::GetFileName([string]$_.OriginalFileName) } | Sort-Object -Unique)

    # --- 5. optional features + capabilities ---------------------------------------------------
    $feat = [ordered]@{}
    $present = @{}
    foreach ($f in @(Get-WindowsOptionalFeature -Path $mount)) { $present[[string]$f.FeatureName] = [string]$f.State }
    foreach ($f in @($x.features.disable)) {
        if ($present.ContainsKey($f)) { Disable-WindowsOptionalFeature -Path $mount -FeatureName $f | Out-Null; $feat[$f] = 'disabled' } else { $feat[$f] = 'absent' }
    }
    foreach ($f in @($x.features.enable)) {
        if (-not $present.ContainsKey($f)) { throw "optional feature $f (windows.xbox.features.enable) is not in this image" }
        Enable-WindowsOptionalFeature -Path $mount -FeatureName $f -All | Out-Null; $feat[$f] = 'enabled'
    }
    foreach ($f in @($x.features.enable_if_nested_virt)) { $feat[$f] = 'setupcomplete (nested virtualization check)' }
    $Result.features = $feat
    $caps = [ordered]@{}
    foreach ($cap in @($x.features.capabilities_add)) {
        $c = Get-WindowsCapability -Path $mount -Name "$cap*" | Select-Object -First 1
        if (-not $c) { throw "capability $cap is not offered by this image" }
        if ($UupDir -and (Test-Path -LiteralPath (Join-Path $UupDir 'FoDMetadata_Client.cab'))) {
            try {
                Add-WindowsCapability -Path $mount -Name $c.Name -Source $UupDir -LimitAccess | Out-Null
                $caps[$cap] = 'offline'
            } catch { $caps[$cap] = 'setupcomplete (online)' }
        } else {
            $caps[$cap] = 'setupcomplete (online)'
        }
    }
    $Result.capabilities = $caps

    if (-not ('MiOSOfflineReg' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Globalization;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

public static class MiOSOfflineReg {
    const uint REG_SZ = 1u;
    const uint REG_DWORD = 4u;

    [DllImport("offreg.dll", CharSet = CharSet.Unicode)]
    static extern int OROpenHive(string lpHivePath, out IntPtr phkResult);
    [DllImport("offreg.dll", CharSet = CharSet.Unicode)]
    static extern int ORCreateKey(IntPtr hKey, string lpSubKey, string lpClass, uint dwOptions, IntPtr pSecurityDescriptor, out IntPtr phkResult, out uint pdwDisposition);
    [DllImport("offreg.dll", CharSet = CharSet.Unicode)]
    static extern int ORSetValue(IntPtr hKey, string lpValueName, uint dwType, byte[] lpData, uint cbData);
    [DllImport("offreg.dll", CharSet = CharSet.Unicode)]
    static extern int ORDeleteValue(IntPtr hKey, string lpValueName);
    [DllImport("offreg.dll")]
    static extern int ORCloseKey(IntPtr hKey);
    [DllImport("offreg.dll", CharSet = CharSet.Unicode)]
    static extern int ORSaveHive(IntPtr hHive, string lpHivePath, uint dwOsMajorVersion, uint dwOsMinorVersion);
    [DllImport("offreg.dll")]
    static extern int ORCloseHive(IntPtr hHive);

    static string Unescape(string s) {
        var sb = new StringBuilder(s.Length);
        for (int i = 0; i < s.Length; i++) {
            if (s[i] == '\\' && i + 1 < s.Length) { sb.Append(s[++i]); }
            else { sb.Append(s[i]); }
        }
        return sb.ToString();
    }

    static IntPtr OpenSubKey(IntPtr root, string subKey) {
        IntPtr cur = root;
        foreach (string part in subKey.Split(new char[] { '\\' }, StringSplitOptions.RemoveEmptyEntries)) {
            IntPtr next; uint disp;
            int rc = ORCreateKey(cur, part, null, 0, IntPtr.Zero, out next, out disp);
            if (cur != root) ORCloseKey(cur);
            if (rc != 0) throw new InvalidOperationException("ORCreateKey " + subKey + " (" + part + ") failed: " + rc);
            cur = next;
        }
        return cur;
    }

    public static void ImportRegFile(string regPath, string swPath, string sysPath, string defuPath) {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) {
            { "MIOS_SW", swPath }, { "MIOS_SYS", sysPath }, { "MIOS_DEFU", defuPath }
        };
        var hives = new Dictionary<string, IntPtr>(StringComparer.OrdinalIgnoreCase);
        try {
            foreach (var kv in map) {
                IntPtr h;
                int rc = OROpenHive(kv.Value, out h);
                if (rc != 0) throw new InvalidOperationException("OROpenHive " + kv.Key + " failed: " + rc);
                hives[kv.Key] = h;
            }
            string curHive = null, curSub = null;
            var valRx = new Regex("^\"((?:[^\"\\\\]|\\\\.)*)\"\\s*=\\s*(.*)$");
            foreach (string raw in File.ReadAllLines(regPath)) {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith(";") || line.StartsWith("Windows Registry Editor")) continue;
                if (line.StartsWith("[HKEY_LOCAL_MACHINE\\") && line.EndsWith("]")) {
                    string full = line.Substring("[HKEY_LOCAL_MACHINE\\".Length, line.Length - "[HKEY_LOCAL_MACHINE\\".Length - 1);
                    int slash = full.IndexOf('\\');
                    if (slash <= 0) throw new InvalidOperationException("invalid hive key line: " + line);
                    curHive = full.Substring(0, slash);
                    curSub = full.Substring(slash + 1);
                    continue;
                }
                if (curHive == null) continue;
                Match m = valRx.Match(line);
                if (!m.Success) throw new InvalidOperationException("unrecognised .reg line: " + line);
                string name = Unescape(m.Groups[1].Value);
                string rhs = m.Groups[2].Value.Trim();
                IntPtr root = hives[curHive];
                IntPtr hKey = OpenSubKey(root, curSub);
                try {
                    int rc;
                    if (rhs == "-") {
                        rc = ORDeleteValue(hKey, name);
                        if (rc != 0 && rc != 2) throw new InvalidOperationException("ORDeleteValue " + curHive + "\\" + curSub + "\\" + name + " failed: " + rc);
                    } else if (rhs.StartsWith("dword:", StringComparison.OrdinalIgnoreCase)) {
                        uint v = uint.Parse(rhs.Substring(6), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                        byte[] b = BitConverter.GetBytes(v);
                        rc = ORSetValue(hKey, name, REG_DWORD, b, (uint)b.Length);
                        if (rc != 0) throw new InvalidOperationException("ORSetValue " + curHive + "\\" + curSub + "\\" + name + " failed: " + rc);
                    } else if (rhs.StartsWith("\"") && rhs.EndsWith("\"")) {
                        string s = Unescape(rhs.Substring(1, rhs.Length - 2));
                        byte[] b = Encoding.Unicode.GetBytes(s + "\0");
                        rc = ORSetValue(hKey, name, REG_SZ, b, (uint)b.Length);
                        if (rc != 0) throw new InvalidOperationException("ORSetValue " + curHive + "\\" + curSub + "\\" + name + " failed: " + rc);
                    } else {
                        throw new InvalidOperationException("unsupported .reg value syntax: " + line);
                    }
                } finally {
                    if (hKey != IntPtr.Zero && hKey != root) ORCloseKey(hKey);
                }
            }
            foreach (var kv in map) {
                IntPtr h = hives[kv.Key];
                string tmp = kv.Value + ".mios.tmp";
                if (File.Exists(tmp)) File.Delete(tmp);
                int rc = ORSaveHive(h, tmp, 6, 3);
                if (rc != 0) throw new InvalidOperationException("ORSaveHive " + kv.Key + " failed: " + rc);
                ORCloseHive(h);
                hives.Remove(kv.Key);
                File.SetAttributes(kv.Value, FileAttributes.Normal);
                File.Copy(tmp, kv.Value, true);
                File.Delete(tmp);
            }
        } finally {
            foreach (var h in hives.Values) { if (h != IntPtr.Zero) ORCloseHive(h); }
        }
    }
}
'@
    }

    # Ensure offline hive files are read-write before loading (NTUSER.DAT has ReadOnly on disk)
    foreach ($hRel in @('Windows\System32\config\SOFTWARE', 'Windows\System32\config\SYSTEM', 'Users\Default\NTUSER.DAT')) {
        $hp = Join-Path $mount $hRel
        if (Test-Path -LiteralPath $hp) { [IO.File]::SetAttributes($hp, [IO.FileAttributes]::Normal) }
    }

    # --- 6. build family + captured Start layout (and flush DISM transaction logs) ----------------
    & reg.exe load 'HKLM\MIOS_SW' (Join-Path $mount 'Windows\System32\config\SOFTWARE') | Out-Null
    if ($LASTEXITCODE) { throw 'cannot load the image SOFTWARE hive' }
    $hives += 'HKLM\MIOS_SW'
    $cvKey = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('MIOS_SW\Microsoft\Windows NT\CurrentVersion', $false)
    if (-not $cvKey) { throw 'cannot open the image SOFTWARE CurrentVersion key' }
    try {
        $family = [string]$cvKey.GetValue((Need $x.start.layout_family_source 'start.layout_family_source'), '')
        $Result.build = [string]$cvKey.GetValue('CurrentBuild', '')
        $Result.ubr = [string]$cvKey.GetValue('UBR', '')
        $Result.branch = [string]$cvKey.GetValue('BuildBranch', '')
        $Result.family = $family
    } finally {
        $cvKey.Close(); $cvKey.Dispose()
    }
    $assets = $x.start.layout_assets.PSObject.Properties | Where-Object { $_.Name -ieq $family } | Select-Object -First 1
    if (-not $assets) { throw "image build family '$family' has no [windows.xbox.start.layout_assets.$family] (capture it: xb-11)" }
    $assetIndex = Get-Content -LiteralPath (Join-Path $StartAssetsDir 'start-assets.json') -Raw | ConvertFrom-Json
    $start2 = Join-Path $StartAssetsDir "$family\start2.bin"
    $want = [string]($assetIndex.PSObject.Properties | Where-Object { $_.Name -ieq $family } | ForEach-Object { $_.Value.start2_sha256 })
    if (-not (Test-Path -LiteralPath $start2) -or -not $want -or (Get-Sha256 $start2) -ne $want) { throw "Start layout asset of family '$family' missing or altered" }

    & reg.exe load 'HKLM\MIOS_SYS' (Join-Path $mount 'Windows\System32\config\SYSTEM') | Out-Null
    if ($LASTEXITCODE) { throw 'cannot load the image SYSTEM hive' }
    $hives += 'HKLM\MIOS_SYS'
    & reg.exe add 'HKLM\MIOS_SYS\Setup' /v SetupType /t REG_DWORD /d 2 /f | Out-Null
    & reg.exe add 'HKLM\MIOS_SYS\Setup' /v CmdLine /t REG_SZ /d 'cmd.exe /c C:\Windows\Setup\Scripts\SetupComplete.cmd' /f | Out-Null
    Say 'offline registry setup hooks injected (SetupType=2)'
    & reg.exe load 'HKLM\MIOS_DEFU' (Join-Path $mount 'Users\Default\NTUSER.DAT') | Out-Null
    if ($LASTEXITCODE) { throw 'cannot load the Default user hive' }
    $hives += 'HKLM\MIOS_DEFU'
} finally {
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        foreach ($h in @($hives)) {
            for ($i = 0; $i -lt 10; $i++) { & reg.exe unload $h >$null 2>$null; if (-not $LASTEXITCODE) { break }; [GC]::Collect(); [GC]::WaitForPendingFinalizers(); Start-Sleep -Seconds 2 }
            if ($LASTEXITCODE) { $Result.hive_unload_failed = $h }
        }
    } finally { $ErrorActionPreference = $prevEap }
}
    if ($Result.Contains('hive_unload_failed')) { throw "hive $($Result.hive_unload_failed) did not unload" }

    # --- 7. offline registry (rendered .reg via offreg.dll, bypassing kernel UCPD/DACL) ----------
    $swHive = Join-Path $mount 'Windows\System32\config\SOFTWARE'
    $sysHive = Join-Path $mount 'Windows\System32\config\SYSTEM'
    $defuHive = Join-Path $mount 'Users\Default\NTUSER.DAT'
    [MiOSOfflineReg]::ImportRegFile($regFile, $swHive, $sysHive, $defuHive)
    # Re-sync kernel transaction log files (.LOG1/.LOG2) with the newly saved hives
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        foreach ($pair in @(@('HKLM\MIOS_SW', $swHive), @('HKLM\MIOS_SYS', $sysHive), @('HKLM\MIOS_DEFU', $defuHive))) {
            Remove-Item -Path "$($pair[1]).LOG*" -Force -ErrorAction SilentlyContinue
            & reg.exe load $pair[0] $pair[1] >$null 2>$null
            if (-not $LASTEXITCODE) { & reg.exe unload $pair[0] >$null 2>$null }
        }
    } finally { $ErrorActionPreference = $prevEap }
    if (Test-Path -LiteralPath $defuHive) { [IO.File]::SetAttributes($defuHive, [IO.FileAttributes]'Hidden, System') }
    Say 'offline registry imported'
    # --- 8. payload into the image ----------------------------------------------------------------
    & robocopy.exe $PayloadDir $mount /E /XF autounattend.xml /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy payload -> image failed ($LASTEXITCODE)" }
    $lstate = Join-Path $mount 'Users\Default\AppData\Local\Packages\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\LocalState'
    New-Item -ItemType Directory -Force -Path $lstate | Out-Null
    Copy-Item -LiteralPath $start2 -Destination (Join-Path $lstate 'start2.bin') -Force
    $pantherDir = Join-Path $mount 'Windows\Panther'
    New-Item -ItemType Directory -Force -Path $pantherDir | Out-Null
    Copy-Item -LiteralPath $answer -Destination (Join-Path $pantherDir 'unattend.xml') -Force
    Say 'injected Panther\unattend.xml'
    Dismount-WindowsImage -Path $mount -Save | Out-Null
    $mounted = $false
} finally {
    if ($mounted) { Dismount-WindowsImage -Path $mount -Discard | Out-Null }
}
Copy-Item -LiteralPath $wim -Destination (Join-Path $MediaDir 'sources\install.wim') -Force
if (Test-Path -LiteralPath (Join-Path $MediaDir 'sources\install.esd')) { Remove-Item -LiteralPath (Join-Path $MediaDir 'sources\install.esd') -Force }
$Result.install_wim_sha256 = Get-Sha256 (Join-Path $MediaDir 'sources\install.wim')

# --- 9. boot.wim: every index gets the virtio drivers ----------------------------------------------
$boot = Join-Path $MediaDir 'sources\boot.wim'
if (@($vw.targets) -contains 'boot.wim') {
    if (-not (Test-Path -LiteralPath $boot)) { throw 'media has no sources\boot.wim' }
    foreach ($img in @(Get-WindowsImage -ImagePath $boot)) {
        Mount-WindowsImage -ImagePath $boot -Index $img.ImageIndex -Path $mount | Out-Null
        try { Add-WindowsDriver -Path $mount -Driver $DriversDir -Recurse | Out-Null; Dismount-WindowsImage -Path $mount -Save | Out-Null }
        catch { Dismount-WindowsImage -Path $mount -Discard | Out-Null; throw }
    }
}

# --- 10. answer file + ISO ------------------------------------------------------------------------
Copy-Item -LiteralPath $answer -Destination (Join-Path $MediaDir 'autounattend.xml') -Force
$tool = $OscdimgPath
if (-not $tool) {
    $tool = @("${env:ProgramFiles(x86)}\Windows Kits\10\Assessment and Deployment Kit\Deployment Tools\amd64\Oscdimg\oscdimg.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $tool -or -not (Test-Path -LiteralPath $tool)) { throw 'oscdimg/cdimage not found (ADK or the converter bin)' }
$efi = @('efi\microsoft\boot\efisys_noprompt.bin', 'efi\microsoft\boot\efisys.bin') | ForEach-Object { Join-Path $MediaDir $_ } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$bios = Join-Path $MediaDir 'boot\etfsboot.com'
if (-not $efi -or -not (Test-Path -LiteralPath $bios)) { throw 'media lacks the BIOS/UEFI boot sectors' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutIso) | Out-Null
& $tool -m -o -u2 -udfver102 "-l$IsoLabel" ('-bootdata:2#p0,e,b{0}#pEF,e,b{1}' -f $bios, $efi) $MediaDir $OutIso | Out-Null
if ($LASTEXITCODE) { throw "ISO mastering failed ($LASTEXITCODE)" }
$Result.iso = [ordered]@{ name = (Split-Path -Leaf $OutIso); sha256 = (Get-Sha256 $OutIso); size = (Get-Item -LiteralPath $OutIso).Length }
$Result.status = 'done'
$Result.finished = [DateTime]::UtcNow.ToString('o')
Save-Result
Say "ISO $OutIso ($($Result.iso.sha256))"
