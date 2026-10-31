# AI-hint: MiOS-Xbox guest post-setup GPU driver step (XB-3). No GPU vendor driver is ever baked into a MiOS image: at first boot (and every boot until it succeeds) this script enumerates the PCI display-class devices present, maps PCI vendor/device IDs to the vendor (NVIDIA 10DE, AMD 1002, Intel 8086), resolves that vendor's latest driver from its upstream, downloads it over HTTPS from an SSOT-allowlisted host, verifies size + published hash (when the vendor publishes one) + Authenticode (status Valid AND signer organization = the vendor), installs it silently with the vendor's documented switches (NVIDIA: Display.Driver only, no NVIDIA App/telemetry), verifies the device now runs that driver and records vendor/device/version/sha256/source URL in C:\ProgramData\MiOS\gpu-driver.json. Idempotent (an already-current device is skipped) and fails loudly (non-zero exit, status "failed" in the record) on any resolver, integrity or install error.
# AI-related: usr/libexec/mios/win/xbox/render_spec.py, tests/test-mios-gpu-driver.ps1, tests/test-mios-gpu-driver.py, usr/share/mios/mios.toml
# Compatible with Windows PowerShell 5.1 and PowerShell 7 (no ??, ?., ternary, -AsHashtable, ForEach-Object -Parallel).
[CmdletBinding()]
param(
    # Resolved SSOT projection (xb-02 render_spec.py output). The GPU table is read from
    # .windows.xbox.gpu (full spec) or .gpu (a gpu-only projection); .windows.xbox.drivers.gpu / .drivers.gpu must be "detect".
    [string]$SpecPath = 'C:\ProgramData\MiOS\xbox-spec.json',
    # Register the scheduled task named by [windows.xbox.gpu] task_name (SetupComplete calls this once) instead of running.
    [switch]$Register,
    # Define the functions only (tests dot-source the script with this and replace the I/O seams).
    [switch]$LibraryOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:MiOSGpuLog = New-Object System.Collections.Generic.List[string]

function Write-MiOSGpuLog([string]$Message) {
    $line = '[mios-gpu-driver] ' + $Message
    $script:MiOSGpuLog.Add($line) | Out-Null
    Write-Host $line   # host stream, never the pipeline: functions here return values
}

# ---------------------------------------------------------------- I/O seams (replaced by tests)

# All PCI devices whose compatible IDs carry PCI base class 03 (display controller). Win32_PnPEntity is used
# rather than Get-PnpDevice -Class Display because a GPU with no driver at all has no Display class yet.
function Get-MiOSPciDisplayDevices {
    $out = @()
    foreach ($d in @(Get-CimInstance -ClassName Win32_PnPEntity -Filter "PNPDeviceID LIKE 'PCI\\%'")) {
        $cc = @($d.CompatibleID) | Where-Object { $_ -match '^PCI\\CC_03' }
        if (-not $cc) { continue }
        $out += [pscustomobject]@{ InstanceId = [string]$d.PNPDeviceID; Name = [string]$d.Name }
    }
    return $out
}

# Driver currently bound to a device: version + provider (Win32_PnPSignedDriver), or $null when none.
function Get-MiOSDeviceDriver([string]$InstanceId) {
    $esc = $InstanceId.Replace('\', '\\').Replace("'", "''")
    $drv = Get-CimInstance -ClassName Win32_PnPSignedDriver -Filter "DeviceID = '$esc'" | Select-Object -First 1
    if (-not $drv) { return $null }
    return [pscustomobject]@{ DriverVersion = [string]$drv.DriverVersion; Provider = [string]$drv.DriverProviderName; InfName = [string]$drv.InfName }
}

function Get-MiOSOsBuild { return [string][Environment]::OSVersion.Version.Build }

function Invoke-MiOSHttpGet([string]$Uri, [hashtable]$Headers, [string]$UserAgent) {
    if ($Uri -notmatch '^https://') { throw "refusing non-HTTPS request: $Uri" }
    $p = @{ Uri = $Uri; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($Headers) { $p.Headers = $Headers }
    if ($UserAgent) { $p.UserAgent = $UserAgent }
    return [string](Invoke-WebRequest @p).Content
}

# Content-Length of the package (HEAD), or -1 when the server does not send one.
function Get-MiOSRemoteLength([string]$Uri, [hashtable]$Headers, [string]$UserAgent) {
    $p = @{ Uri = $Uri; Method = 'Head'; UseBasicParsing = $true; TimeoutSec = 60 }
    if ($Headers) { $p.Headers = $Headers }
    if ($UserAgent) { $p.UserAgent = $UserAgent }
    $r = Invoke-WebRequest @p
    $v = $r.Headers['Content-Length']
    if ($v -is [array]) { $v = $v[0] }
    if ($v) { return [long]$v }
    return [long]-1
}

function Save-MiOSDownload([string]$Uri, [string]$OutFile, [hashtable]$Headers, [string]$UserAgent) {
    if ($Uri -notmatch '^https://') { throw "refusing non-HTTPS download: $Uri" }
    $p = @{ Uri = $Uri; OutFile = $OutFile; UseBasicParsing = $true; TimeoutSec = 3600; MaximumRedirection = 0 }
    if ($Headers) { $p.Headers = $Headers }
    if ($UserAgent) { $p.UserAgent = $UserAgent }
    Invoke-WebRequest @p | Out-Null
}

function Get-MiOSSignature([string]$Path) {
    $s = Get-AuthenticodeSignature -LiteralPath $Path
    $subj = ''; $thumb = ''
    if ($s.SignerCertificate) { $subj = [string]$s.SignerCertificate.Subject; $thumb = [string]$s.SignerCertificate.Thumbprint }
    return [pscustomobject]@{ Status = [string]$s.Status; Subject = $subj; Thumbprint = $thumb }
}

function Expand-MiOSPackage([string]$Path, [string]$Destination) {
    # NVIDIA/AMD/Intel packages are 7-Zip self-extractors; Windows' tar.exe (libarchive) reads them.
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & "$env:SystemRoot\System32\tar.exe" -xf $Path -C $Destination
    if ($LASTEXITCODE -ne 0) { throw "extracting $Path failed (tar exit $LASTEXITCODE)" }
}

function Invoke-MiOSInstaller([string]$FilePath, [string[]]$Arguments) {
    $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru -NoNewWindow
    return [int]$p.ExitCode
}

function Get-MiOSFileSha([string]$Path, [string]$Algorithm) {
    return (Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash.ToLowerInvariant()
}

# --------------------------------------------------------------- pure helpers

function Get-MiOSProp($Object, [string]$Name, [switch]$Required) {
    if ($null -ne $Object) {
        $p = $Object.PSObject.Properties[$Name]
        if ($p) { return $p.Value }
    }
    if ($Required) { throw "SSOT value missing: $Name" }
    return $null
}

function Read-MiOSGpuSpec([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "resolved SSOT spec not found: $Path" }
    $spec = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $gpu = $null
    $drivers = $null

    $specProp = Get-MiOSProp $spec 'spec'
    if ($specProp) {
        $gpu = Get-MiOSProp $specProp 'gpu'
        $drivers = Get-MiOSProp $specProp 'drivers'
    }
    if (-not $gpu) {
        $win = Get-MiOSProp $spec 'windows'
        if ($win) {
            $xb = Get-MiOSProp $win 'xbox'
            if ($xb) {
                $gpu = Get-MiOSProp $xb 'gpu'
                $drivers = Get-MiOSProp $xb 'drivers'
            }
        }
    }
    if (-not $gpu) {
        $gpu = Get-MiOSProp $spec 'gpu'
        $drivers = Get-MiOSProp $spec 'drivers'
    }
    if (-not $gpu) { throw "SSOT table windows.xbox.gpu missing in $Path" }
    $mode = if ($drivers) { [string](Get-MiOSProp $drivers 'gpu') } else { '' }
    if (-not $mode) { throw "SSOT key windows.xbox.drivers.gpu missing in $Path" }
    foreach ($k in 'record_path', 'work_dir', 'task_name', 'skip_vendor_ids', 'vendors') { [void](Get-MiOSProp $gpu $k -Required) }
    return [pscustomobject]@{ Mode = $mode; Gpu = $gpu }
}

# PCI\VEN_10DE&DEV_2684&SUBSYS_...  ->  vendor/device (upper-case hex)
function ConvertFrom-MiOSPciId([string]$InstanceId) {
    if ($InstanceId -match '^PCI\\VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})') {
        return [pscustomobject]@{ VendorId = $Matches[1].ToUpperInvariant(); DeviceId = $Matches[2].ToUpperInvariant() }
    }
    return $null
}

# Windows DriverVersion -> NVIDIA marketing version: 32.0.16.1742 -> 617.42 (last five digits of parts 3+4).
function ConvertTo-MiOSNvidiaVersion([string]$DriverVersion) {
    $parts = $DriverVersion.Split('.')
    if ($parts.Count -ne 4) { return $null }
    $d = ($parts[2] + $parts[3].PadLeft(4, '0'))
    if ($d.Length -lt 5) { return $null }
    $d = $d.Substring($d.Length - 5)
    return ('{0}.{1}' -f [int]$d.Substring(0, 3), $d.Substring(3, 2))
}

# Windows DriverVersion 32.0.101.9150 -> Intel package version 101.9150.
function ConvertTo-MiOSIntelVersion([string]$DriverVersion) {
    $parts = $DriverVersion.Split('.')
    if ($parts.Count -ne 4) { return $null }
    return ($parts[2] + '.' + $parts[3])
}

function Compare-MiOSVersion([string]$A, [string]$B) {
    $pa = @($A.Split('.') | ForEach-Object { [int]$_ }); $pb = @($B.Split('.') | ForEach-Object { [int]$_ })
    $n = [Math]::Max($pa.Count, $pb.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $x = 0; $y = 0
        if ($i -lt $pa.Count) { $x = $pa[$i] }
        if ($i -lt $pb.Count) { $y = $pb[$i] }
        if ($x -ne $y) { return [Math]::Sign($x - $y) }
    }
    return 0
}

function Test-MiOSAllowedUrl([string]$Url, $Hosts) {
    if ($Url -notmatch '^https://([^/:?#]+)(/|$)') { return $false }
    $h = $Matches[1].ToLowerInvariant()
    foreach ($a in @($Hosts)) { if ($h -eq ([string]$a).ToLowerInvariant()) { return $true } }
    return $false
}

function Get-MiOSVendorForId($Gpu, [string]$VendorId) {
    foreach ($p in $Gpu.vendors.PSObject.Properties) {
        if (([string](Get-MiOSProp $p.Value 'vendor_id')).ToUpperInvariant() -eq $VendorId) {
            return [pscustomobject]@{ Key = $p.Name; Cfg = $p.Value }
        }
    }
    return $null
}

function ConvertTo-MiOSHeaders($Cfg) {
    $h = @{}
    $ref = Get-MiOSProp $Cfg 'referer'
    if ($ref) { $h['Referer'] = [string]$ref }
    return $h
}

# --------------------------------------------------------------- resolvers (vendor upstream, latest)

# NVIDIA: the device-ID lookup the NVIDIA App / GeForce Experience client uses (unofficial, undocumented).
function Resolve-MiOSNvidiaDriver($Cfg, $Dev) {
    $params = @{}
    foreach ($p in (Get-MiOSProp $Cfg 'lookup_params' -Required).PSObject.Properties) { $params[$p.Name] = [string]$p.Value }
    $params['dIDa'] = @(('{0}_{1}' -f $Dev.DeviceId, $Dev.VendorId).ToLowerInvariant())
    $params['osB'] = Get-MiOSOsBuild
    $json = ($params | ConvertTo-Json -Compress)
    $url = [string](Get-MiOSProp $Cfg 'lookup_url' -Required) + [Uri]::EscapeDataString($json)
    $raw = Invoke-MiOSHttpGet $url (ConvertTo-MiOSHeaders $Cfg) ([string](Get-MiOSProp $Cfg 'user_agent'))
    $r = $raw | ConvertFrom-Json
    $sup = [string]$r.criteria.IsSupported.state
    if ($sup -ne 'true') { throw ("NVIDIA lookup: device {0}:{1} not supported (IsSupported={2})" -f $Dev.VendorId, $Dev.DeviceId, $sup) }
    $a = $r.DriverAttributes
    if (-not $a) { throw 'NVIDIA lookup: no DriverAttributes in response' }
    $ver = [string]$a.Version
    if ($ver -notmatch '^\d{3}\.\d{2}$') { throw "NVIDIA lookup: unexpected version '$ver'" }
    if ([string]$a.IsWHQL -ne '1' -and (Get-MiOSProp $Cfg 'require_whql')) { throw "NVIDIA lookup: $ver is not WHQL" }
    $dl = [string](Get-MiOSProp $a ([string](Get-MiOSProp $Cfg 'download_field' -Required)))
    if (-not $dl) { throw 'NVIDIA lookup: no download URL in response' }
    if ($dl -notmatch [regex]::Escape($ver)) { throw "NVIDIA lookup: download URL does not carry version $ver" }
    return [pscustomobject]@{ Version = $ver; Url = $dl; Sha256 = $null; Sha512 = $null; Size = [long]-1; Resolver = 'nvidia-devid-lookup'; ResolverUrl = [string](Get-MiOSProp $Cfg 'lookup_url'); Details = [string]$a.DetailsURL }
}

# AMD: AMD's official per-series driver pages list the full Adrenalin packages (no API, no published hash).
function Resolve-MiOSAmdDriver($Cfg, $Dev) {
    $rx = [string](Get-MiOSProp $Cfg 'package_regex' -Required)
    $channel = [string](Get-MiOSProp $Cfg 'channel' -Required)
    $found = @{}; $recommended = @{}
    foreach ($page in @(Get-MiOSProp $Cfg 'index_pages' -Required)) {
        $html = Invoke-MiOSHttpGet ([string]$page) (ConvertTo-MiOSHeaders $Cfg) ([string](Get-MiOSProp $Cfg 'user_agent'))
        foreach ($m in [regex]::Matches($html, $rx)) { $found[$m.Groups['version'].Value] = $m.Value }
        $text = [regex]::Replace([regex]::Replace($html, '<[^>]+>', ' '), '[\s\u200B]+', ' ')
        foreach ($m in [regex]::Matches($text, [string](Get-MiOSProp $Cfg 'recommended_regex' -Required))) { $recommended[$m.Groups['version'].Value] = $true }
    }
    if ($found.Count -eq 0) { throw 'AMD resolver: no Adrenalin package link found on the configured AMD driver pages' }
    $versions = @($found.Keys)
    if ($channel -eq 'recommended') { $versions = @($versions | Where-Object { $recommended.ContainsKey($_) }) }
    elseif ($channel -ne 'latest') { throw "AMD resolver: unknown channel '$channel'" }
    if ($versions.Count -eq 0) { throw "AMD resolver: no package for channel '$channel'" }
    $best = $versions[0]
    foreach ($v in $versions) { if ((Compare-MiOSVersion $v $best) -gt 0) { $best = $v } }
    return [pscustomobject]@{ Version = $best; Url = $found[$best]; Sha256 = $null; Sha512 = $null; Size = [long]-1; Resolver = 'amd-driver-pages'; ResolverUrl = (@(Get-MiOSProp $Cfg 'index_pages') -join ' '); Details = '' }
}

# Intel: Intel's official download-center page (version, mirror link and published SHA512 are on the page).
function Resolve-MiOSIntelDriver($Cfg, $Dev) {
    $rx = [string](Get-MiOSProp $Cfg 'package_regex' -Required)
    $best = $null
    foreach ($page in @(Get-MiOSProp $Cfg 'index_pages' -Required)) {
        $html = Invoke-MiOSHttpGet ([string]$page) (ConvertTo-MiOSHeaders $Cfg) ([string](Get-MiOSProp $Cfg 'user_agent'))
        $m = [regex]::Match($html, $rx)
        $url = $null; $ver = $null
        if ($m.Success) { $url = $m.Value; $ver = $m.Groups['version'].Value }
        else {
            # The download-center page may render the link from data attributes: rebuild it from the file label and
            # the page's own downloadmirror folder (the same folder that serves its readme/release notes).
            $lbl = [regex]::Match($html, [string](Get-MiOSProp $Cfg 'label_regex' -Required))
            $dir = [regex]::Match($html, [string](Get-MiOSProp $Cfg 'mirror_dir_regex' -Required))
            if ($lbl.Success -and $dir.Success) { $ver = $lbl.Groups['version'].Value; $url = $dir.Value + $lbl.Value }
        }
        if (-not $url) { continue }
        $sha = [regex]::Match($html, [string](Get-MiOSProp $Cfg 'hash_regex' -Required))
        $cand = [pscustomobject]@{ Version = $ver; Url = $url; Sha256 = $null; Sha512 = $null; Size = [long]-1; Resolver = 'intel-download-center'; ResolverUrl = [string]$page; Details = [string]$page }
        if ($sha.Success) { $cand.Sha512 = $sha.Groups['hash'].Value.ToLowerInvariant() }
        if (-not $best -or (Compare-MiOSVersion $cand.Version $best.Version) -gt 0) { $best = $cand }
    }
    if (-not $best) { throw 'Intel resolver: no gfx_win package link found on the configured Intel download pages' }
    if ((Get-MiOSProp $Cfg 'require_published_hash') -and -not $best.Sha512) { throw "Intel resolver: no published SHA512 for $($best.Version)" }
    return $best
}

function Resolve-MiOSVendorDriver([string]$Resolver, $Cfg, $Dev) {
    switch ($Resolver) {
        'nvidia-devid-lookup' { return Resolve-MiOSNvidiaDriver $Cfg $Dev }
        'amd-driver-pages' { return Resolve-MiOSAmdDriver $Cfg $Dev }
        'intel-download-center' { return Resolve-MiOSIntelDriver $Cfg $Dev }
        default { throw "unknown resolver '$Resolver' in SSOT" }
    }
}

# Is the device's bound driver already the resolved version?
function Test-MiOSDriverCurrent([string]$Resolver, $Current, $Latest, $Record, $Dev) {
    if (-not $Current -or -not $Current.DriverVersion) { return $false }
    switch ($Resolver) {
        'nvidia-devid-lookup' { return ((ConvertTo-MiOSNvidiaVersion $Current.DriverVersion) -eq $Latest.Version) }
        'intel-download-center' { return ((ConvertTo-MiOSIntelVersion $Current.DriverVersion) -eq $Latest.Version) }
        default {
            # AMD's package version (26.9.2) is not derivable from the Windows driver version: trust our own record
            # only while the device still runs the exact driver that install left behind.
            foreach ($e in @(Get-MiOSProp $Record 'devices')) {
                if ($e -and $e.vendor_id -eq $Dev.VendorId -and $e.device_id -eq $Dev.DeviceId -and $e.version -eq $Latest.Version -and $e.status -eq 'installed' -and $e.installed_driver_version -eq $Current.DriverVersion) { return $true }
            }
            return $false
        }
    }
}

function Protect-MiOSDir([string]$Path, [bool]$UsersRead = $false, [bool]$Quarantine = $true) {
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
    if (-not ($env:OS -eq 'Windows_NT' -or $IsWindows)) { return }
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
                cmd /c rmdir "$Path" | Out-Null
            } else {
                takeown /F "$Path" /A | Out-Null
                icacls "$Path" /grant '*S-1-5-32-544:F' /Q | Out-Null
                if ($Quarantine) {
                    $q = "$Path.untrusted-$([DateTime]::UtcNow.Ticks)"
                    Rename-Item -LiteralPath $Path -NewName (Split-Path -Leaf $q)
                    cmd /c rd /s /q "$q" 2>$null | Out-Null
                }
            }
            Write-MiOSGpuLog "$Path was not restricted to SYSTEM/Administrators; secured"
        }
    }
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
    try {
        $n = New-Object Security.AccessControl.DirectorySecurity
        $n.SetAccessRuleProtection($true, $false)
        $grants = @($adm | ForEach-Object { , @($_, 'FullControl') })
        if ($UsersRead) { $grants += , @($usr, 'ReadAndExecute') }
        foreach ($g in $grants) {
            $n.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier $g[0]), $g[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
        }
        [IO.Directory]::SetAccessControl($Path, $n)
    } catch {
        Write-MiOSGpuLog "warning: could not set DACL on $Path : $($_.Exception.Message)"
    }
}

function Resolve-MiOSRedirectUrl([string]$Url, $Hosts, [hashtable]$Headers, [string]$UserAgent) {
    if (-not (Test-MiOSAllowedUrl $Url $Hosts)) {
        throw "initial download URL $Url is not an allowed host"
    }
    $current = $Url
    $hops = 0
    while ($hops -lt 10) {
        $req = $null
        $loc = $null
        $isRedirect = $false
        try {
            $req = [System.Net.HttpWebRequest]::Create($current)
            $req.AllowAutoRedirect = $false
            $req.Method = 'HEAD'
            $req.Timeout = 15000
            if ($UserAgent) { $req.UserAgent = $UserAgent }
            if ($Headers) { foreach ($k in $Headers.Keys) { $req.Headers.Add($k, $Headers[$k]) } }
            $resp = $req.GetResponse()
            $code = [int]$resp.StatusCode
            if ($code -ge 300 -and $code -lt 400) {
                $loc = $resp.Headers['Location']
                $isRedirect = $true
            }
            $resp.Close()
        } catch [System.Net.WebException] {
            if ($_.Response) {
                $code = [int]$_.Response.StatusCode
                if ($code -ge 300 -and $code -lt 400) {
                    $loc = $_.Response.Headers['Location']
                    $isRedirect = $true
                }
                $_.Response.Close()
            }
            if (-not $isRedirect) { break }
        } catch {
            break
        }

        if ($isRedirect) {
            if (-not $loc) { break }
            $next = (New-Object Uri ([Uri]$current), $loc).AbsoluteUri
            if (-not (Test-MiOSAllowedUrl $next $Hosts)) {
                throw "redirect to $next is not on an allowed host"
            }
            $current = $next
            $hops++
            continue
        }
        break
    }
    if (-not (Test-MiOSAllowedUrl $current $Hosts)) {
        throw "resolved redirect URL $current is not an allowed host"
    }
    return $current
}

function Test-MiOSSigner($Sig, [string]$OrgRegex) {
    if ($Sig.Status -ne 'Valid') { return $false }
    # Organization (O=) of the signer certificate subject; a quoted value may itself contain commas.
    $m = [regex]::Match([string]$Sig.Subject, '(?:^|,\s*)O=(?:"(?<o>[^"]+)"|(?<o>[^,]+))')
    if (-not $m.Success) { return $false }
    $o = $m.Groups['o'].Value.Trim()
    if ($o -cmatch $OrgRegex) { return $true }
    if ($o -cmatch ($OrgRegex -replace 'Inc', '(?:Inc|INC)')) { return $true }
    return $false
}

function Expand-MiOSArgs($Template, [hashtable]$Vars) {
    $out = @()
    foreach ($t in @($Template)) {
        $s = [string]$t
        if ($s -eq '{components}') { $out += @($Vars['components']); continue }
        foreach ($k in $Vars.Keys) { if ($Vars[$k] -is [string]) { $s = $s.Replace('{' + $k + '}', $Vars[$k]) } }
        $out += $s
    }
    return , $out
}

function Write-MiOSJsonAtomic([string]$Path, $Object) {
    $dir = Split-Path -Parent $Path
    Protect-MiOSDir $dir $true
    $tmp = Join-Path $dir ('.' + [IO.Path]::GetFileName($Path) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $text = ($Object | ConvertTo-Json -Depth 8)
    $fs = [IO.File]::Open($tmp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $b = (New-Object Text.UTF8Encoding($false)).GetBytes($text)
        $fs.Write($b, 0, $b.Length); $fs.Flush($true)
    } finally { $fs.Dispose() }
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($tmp, $Path, [NullString]::Value) } else { [IO.File]::Move($tmp, $Path) }
}

function Read-MiOSRecord([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) } catch { Write-MiOSGpuLog "ignoring unreadable record $Path : $($_.Exception.Message)" }
    }
    return $null
}

# --------------------------------------------------------------- per-device install

function Install-MiOSDeviceDriver($Gpu, $Vendor, $Dev, $Record) {
    $cfg = $Vendor.Cfg
    $resolver = [string](Get-MiOSProp $cfg 'resolver' -Required)
    $orgRx = [string](Get-MiOSProp $cfg 'signer_org_regex' -Required)
    $hosts = Get-MiOSProp $cfg 'download_hosts' -Required
    $ua = [string](Get-MiOSProp $cfg 'user_agent')
    $headers = ConvertTo-MiOSHeaders $cfg
    $entry = [ordered]@{ vendor = $Vendor.Key; vendor_id = $Dev.VendorId; device_id = $Dev.DeviceId; instance_id = $Dev.InstanceId; name = $Dev.Name }

    $latest = Resolve-MiOSVendorDriver $resolver $cfg $Dev
    $entry.version = $latest.Version; $entry.source_url = $latest.Url; $entry.resolver = $latest.Resolver; $entry.resolver_url = $latest.ResolverUrl
    Write-MiOSGpuLog ("{0} {1}:{2} latest {3} <- {4}" -f $Vendor.Key, $Dev.VendorId, $Dev.DeviceId, $latest.Version, $latest.Url)

    $current = Get-MiOSDeviceDriver $Dev.InstanceId
    if (Test-MiOSDriverCurrent $resolver $current $latest $Record $Dev) {
        Write-MiOSGpuLog ("{0} {1}:{2} already runs {3} ({4}); nothing to do" -f $Vendor.Key, $Dev.VendorId, $Dev.DeviceId, $latest.Version, $current.DriverVersion)
        foreach ($e in @(Get-MiOSProp $Record 'devices')) {
            if ($e -and $e.vendor_id -eq $Dev.VendorId -and $e.device_id -eq $Dev.DeviceId -and $e.version -eq $latest.Version) {
                foreach ($k in 'sha256', 'size', 'signer', 'signer_thumbprint', 'installed_at') { $entry[$k] = Get-MiOSProp $e $k }
            }
        }
        $entry.installed_driver_version = $current.DriverVersion; $entry.driver_provider = $current.Provider; $entry.status = 'current'
        return [pscustomobject]$entry
    }

    if (-not (Test-MiOSAllowedUrl $latest.Url $hosts)) { throw ("{0}: download URL {1} is not HTTPS on an allowed host ({2})" -f $Vendor.Key, $latest.Url, (@($hosts) -join ', ')) }
    $resolvedUrl = Resolve-MiOSRedirectUrl $latest.Url $hosts $headers $ua

    Protect-MiOSDir ([string]$Gpu.work_dir)
    $work = Join-Path ([string]$Gpu.work_dir) ($Vendor.Key + '-' + $latest.Version)
    Protect-MiOSDir $work
    $file = Join-Path $work ([IO.Path]::GetFileName(([Uri]$resolvedUrl).AbsolutePath))
    $expected = Get-MiOSRemoteLength $resolvedUrl $headers $ua
    Write-MiOSGpuLog "downloading $($resolvedUrl) ($expected bytes) -> $file"
    Save-MiOSDownload $resolvedUrl $file $headers $ua
    $size = (Get-Item -LiteralPath $file).Length
    if ($expected -gt 0 -and $size -ne $expected) { throw "$($Vendor.Key): downloaded $size bytes, server announced $expected" }
    $sha256 = Get-MiOSFileSha $file 'SHA256'
    $entry.sha256 = $sha256; $entry.size = $size
    if ($latest.Sha512) {
        $got = Get-MiOSFileSha $file 'SHA512'
        if ($got -ne $latest.Sha512) { throw "$($Vendor.Key): SHA512 mismatch for $file (published $($latest.Sha512), got $got)" }
        $entry.sha512 = $got; $entry.published_hash_verified = $true
    } else { $entry.published_hash_verified = $false }

    $sig = Get-MiOSSignature $file
    if (-not (Test-MiOSSigner $sig $orgRx)) { throw ("{0}: Authenticode check failed for {1}: status={2} signer='{3}' (expected Valid, O~/{4}/)" -f $Vendor.Key, $file, $sig.Status, $sig.Subject, $orgRx) }
    $entry.signer = $sig.Subject; $entry.signer_thumbprint = $sig.Thumbprint
    Write-MiOSGpuLog "signature Valid: $($sig.Subject)"

    $logDir = Join-Path $work 'log'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $exe = $file
    $inst = Get-MiOSProp $cfg 'installer'
    if ((Get-MiOSProp $cfg 'extract')) {
        $x = Join-Path $work 'x'
        Expand-MiOSPackage $file $x
        $exe = Join-Path $x ([string](Get-MiOSProp $cfg 'installer' -Required))
        if (-not (Test-Path -LiteralPath $exe)) { throw "$($Vendor.Key): $inst not found in the extracted package" }
        $s2 = Get-MiOSSignature $exe
        if (-not (Test-MiOSSigner $s2 $orgRx)) { throw ("{0}: extracted installer {1} signature check failed: status={2} signer='{3}'" -f $Vendor.Key, $exe, $s2.Status, $s2.Subject) }
    }
    $vars = @{ logdir = $logDir; logfile = (Join-Path $logDir 'install.log'); components = @(Get-MiOSProp $cfg 'components') }
    $argv = Expand-MiOSArgs (Get-MiOSProp $cfg 'install_args' -Required) $vars
    Write-MiOSGpuLog "installing: $exe $($argv -join ' ')"
    $rc = Invoke-MiOSInstaller $exe $argv
    $entry.exit_code = $rc
    $ok = @(Get-MiOSProp $cfg 'success_exit_codes' -Required | ForEach-Object { [int]$_ })
    $rb = @(Get-MiOSProp $cfg 'reboot_exit_codes' | ForEach-Object { [int]$_ })
    if ($ok -notcontains $rc) { throw "$($Vendor.Key): installer exit code $rc (success codes: $($ok -join ','); logs in $logDir)" }
    $entry.installed_at = (Get-Date).ToUniversalTime().ToString('o')

    $after = Get-MiOSDeviceDriver $Dev.InstanceId
    $provRx = [string](Get-MiOSProp $cfg 'provider_regex' -Required)
    if ($rb -contains $rc) {
        $entry.status = 'reboot-pending'
        Write-MiOSGpuLog "$($Vendor.Key) $($latest.Version) installed, reboot required (exit $rc); the next run verifies the bound driver"
    } else {
        if (-not $after -or $after.Provider -notmatch $provRx) { throw ("{0}: after install the device is bound to '{1}' not a /{2}/ driver" -f $Vendor.Key, $(if ($after) { $after.Provider } else { '<none>' }), $provRx) }
        if ($resolver -ne 'amd-driver-pages') {
            $tmpRec = [pscustomobject]@{ devices = @() }
            if (-not (Test-MiOSDriverCurrent $resolver $after $latest $tmpRec $Dev)) { throw ("{0}: after install the device runs {1}, expected {2}" -f $Vendor.Key, $after.DriverVersion, $latest.Version) }
        }
        $entry.status = 'installed'
    }
    if ($after) { $entry.installed_driver_version = $after.DriverVersion; $entry.driver_provider = $after.Provider }
    Write-MiOSGpuLog "$($Vendor.Key) $($latest.Version) -> status $($entry.status)"
    return [pscustomobject]$entry
}

# --------------------------------------------------------------- entry points

function Invoke-MiOSGpuDriver([string]$SpecPath) {
    $spec = Read-MiOSGpuSpec $SpecPath
    $gpu = $spec.Gpu
    $recordPath = [string]$gpu.record_path
    $old = Read-MiOSRecord $recordPath
    $record = [ordered]@{ schema = 1; run_at = (Get-Date).ToUniversalTime().ToString('o'); mode = $spec.Mode; status = 'ok'; devices = @(); skipped = @(); log = @() }
    if ($spec.Mode -ne 'detect') {
        Write-MiOSGpuLog "windows.xbox.drivers.gpu = '$($spec.Mode)' (not 'detect'): GPU driver step disabled"
        $record.status = 'disabled'; $record.log = @($script:MiOSGpuLog)
        Write-MiOSJsonAtomic $recordPath $record
        return 0
    }
    $skipIds = @(@($gpu.skip_vendor_ids) | ForEach-Object { ([string]$_).ToUpperInvariant() })
    $failed = $false
    $devs = @(Get-MiOSPciDisplayDevices)
    $seen = @{}
    foreach ($d in $devs) {
        $id = ConvertFrom-MiOSPciId $d.InstanceId
        if (-not $id) { $record.skipped += [pscustomobject]@{ instance_id = $d.InstanceId; reason = 'not a PCI VEN/DEV id' }; continue }
        if ($skipIds -contains $id.VendorId) {
            Write-MiOSGpuLog "skip virtual display $($id.VendorId):$($id.DeviceId) ($($d.Name))"
            $record.skipped += [pscustomobject]@{ instance_id = $d.InstanceId; vendor_id = $id.VendorId; device_id = $id.DeviceId; reason = 'virtual display (skip_vendor_ids)' }
            continue
        }
        $vendor = Get-MiOSVendorForId $gpu $id.VendorId
        if (-not $vendor) {
            Write-MiOSGpuLog "no MiOS resolver for PCI vendor $($id.VendorId) ($($d.Name)); nothing installed"
            $record.skipped += [pscustomobject]@{ instance_id = $d.InstanceId; vendor_id = $id.VendorId; device_id = $id.DeviceId; reason = 'unknown vendor (no resolver)' }
            continue
        }
        $key = $id.VendorId + ':' + $id.DeviceId
        if ($seen.ContainsKey($key)) { continue }   # one package per vendor/device even with several functions/cards
        $seen[$key] = $true
        $dev = [pscustomobject]@{ VendorId = $id.VendorId; DeviceId = $id.DeviceId; InstanceId = $d.InstanceId; Name = $d.Name }
        try {
            $record.devices += Install-MiOSDeviceDriver $gpu $vendor $dev $old
        } catch {
            $failed = $true
            Write-MiOSGpuLog ("FAILED {0} {1}: {2}" -f $vendor.Key, $key, $_.Exception.Message)
            $record.devices += [pscustomobject]@{ vendor = $vendor.Key; vendor_id = $id.VendorId; device_id = $id.DeviceId; instance_id = $d.InstanceId; status = 'failed'; error = $_.Exception.Message }
        }
    }
    if ($record.devices.Count -eq 0) {
        Write-MiOSGpuLog 'no supported GPU present: nothing installed'
        $record.status = 'no-gpu'
    }
    if ($failed) { $record.status = 'failed' }
    $record.log = @($script:MiOSGpuLog)
    Write-MiOSJsonAtomic $recordPath $record
    if ($failed) { return 1 }
    return 0
}

# SetupComplete hook: one scheduled task, SYSTEM, at every startup once the network is up, until the record says done.
function Register-MiOSGpuDriverTask([string]$SpecPath) {
    $spec = Read-MiOSGpuSpec $SpecPath
    $name = [string]$spec.Gpu.task_name
    $folder = '\'
    if ($name.Contains('\')) { $folder = '\' + $name.Substring(0, $name.LastIndexOf('\')) + '\'; $name = $name.Substring($name.LastIndexOf('\') + 1) }
    $self = $PSCommandPath
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $act = New-ScheduledTaskAction -Execute $ps -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -SpecPath "{1}"' -f $self, $SpecPath)
    $trg = New-ScheduledTaskTrigger -AtStartup
    $set = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)
    $pri = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskPath $folder -TaskName $name -Action $act -Trigger $trg -Settings $set -Principal $pri -Force | Out-Null
    Write-MiOSGpuLog "registered scheduled task $folder$name"
    if ([string]$spec.Gpu.install_stage -eq 'setupcomplete-task') { Start-ScheduledTask -TaskPath $folder -TaskName $name }
    return 0
}

if ($LibraryOnly) { return }
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'mios-gpu-driver.ps1 runs in the Windows guest only' }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
if ($Register) { exit (Register-MiOSGpuDriverTask $SpecPath) }
exit (Invoke-MiOSGpuDriver $SpecPath)
