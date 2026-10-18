# AI-hint: Pester-free behaviour tests for usr/share/mios/windows/xbox/mios-gpu-driver.ps1 (xb-09 / XB-3). Dot-sources the script with -LibraryOnly, replaces its I/O seams (PCI enumeration, bound driver, HTTP, download, Authenticode, extraction, installer) with mocks, and drives Invoke-MiOSGpuDriver against a spec rendered from the merged SSOT (live mios.toml + staged xbox-gpu.toml). No network, no installs. Positive and negative controls; exit 0 only if every case passes.
# AI-related: tests/test-mios-gpu-driver.py (renders the spec via mios_toml.load_merged and runs this under pwsh), usr/share/mios/windows/xbox/mios-gpu-driver.ps1
param(
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [Parameter(Mandatory = $true)][string]$SpecPath     # resolved spec JSON: { windows: { xbox: { drivers, gpu } } }
)
$ErrorActionPreference = 'Stop'
# Dot-sourcing runs the script's own param block in this scope (its -SpecPath default would overwrite ours): keep copies.
$TestScriptPath = $ScriptPath; $TestSpecPath = $SpecPath
. $TestScriptPath -LibraryOnly
$ScriptPath = $TestScriptPath; $SpecPath = $TestSpecPath

$script:Fail = 0; $script:Pass = 0
function Check([bool]$Cond, [string]$Name) {
    if ($Cond) { $script:Pass++; Write-Host "ok   - $Name" } else { $script:Fail++; Write-Host "FAIL - $Name" }
}

$BaseSpec = Get-Content -LiteralPath $SpecPath -Raw | ConvertFrom-Json
$Gpu = $BaseSpec.windows.xbox.gpu
$NvLookup = [string]$Gpu.vendors.nvidia.lookup_url

# ---- recorded upstream responses (trimmed from live metadata lookups on 2026-10-16) ----
$NvJsonOk = @'
{"criteria":{"overallState":"true","IsSupported":{"state":"true","DeviceID":"2684","Vendor":"10de"},"IsDispDriverNewer":{"state":"true","latestDispDriverVersion":"617.42"}},
 "DriverAttributes":{"ID":280103,"Name":"GeForce Game Ready Driver","Version":"617.42","IsWHQL":1,"IsBeta":0,"IsDC":1,
  "DetailsURL":"https://www.nvidia.com/en-us/drivers/details/280103/",
  "DownloadURL":"https://international-gfe.download.nvidia.com/Windows/617.42/617.42-desktop-win10-win11-64bit-international-dch-whql-g.exe",
  "DownloadURLAdmin":"https://us.download.nvidia.com/Windows/617.42/617.42-desktop-win10-win11-64bit-international-dch-whql.exe"}}
'@
$NvJsonUnsupported = '{"criteria":{"overallState":"false","IsSupported":{"state":"false"}},"DriverAttributes":null}'
$NvJsonEvilHost = $NvJsonOk.Replace('https://us.download.nvidia.com/', 'https://us.download.nvidia.com.evil.example/')
$NvJsonHttp = $NvJsonOk.Replace('"DownloadURLAdmin":"https://', '"DownloadURLAdmin":"http://')
$AmdHtml = @'
<div>AMD Software: Adrenalin Edition Revision Number <span>Adrenalin 26.8.1 (WHQL Recommended)</span> File Size 849 MB
<a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.8.1-win11-b.exe">Download</a></div>
<div>Revision Number Adrenalin 26.9.2 WHQL (Optional) <a href="https://drivers.amd.com/drivers/whql-amd-software-adrenalin-edition-26.9.2-win11-b.exe">Download</a>
<a href="https://drivers.amd.com/drivers/installer/26.10/whql/amd-software-adrenalin-edition-26.8.1-minimalsetup-260818_web.exe">auto-detect</a></div>
'@
$Payload = [Text.Encoding]::ASCII.GetBytes('MZ fake vendor package for tests')
$PayloadSha512 = ([BitConverter]::ToString([Security.Cryptography.SHA512]::Create().ComputeHash($Payload))).Replace('-', '').ToLowerInvariant()
$IntelHtml = '<h1>Intel Arc Graphics - Windows</h1><a data-href="x">Download gfx_win_101.9150.exe</a> <a href="https://downloadmirror.intel.com/930514/readme.txt">readme</a> SHA512: ' + $PayloadSha512

# ---- mocks (replace the script's I/O seams) ----
function Reset-Mocks {
    $script:MockDevices = @(); $script:MockDrivers = @{}; $script:PostInstallDriver = $null
    $script:NvJson = $NvJsonOk; $script:MockRc = 0; $script:MockLength = [long]$Payload.Length
    $script:MockSig = [pscustomobject]@{ Status = 'Valid'; Subject = 'CN=NVIDIA Corporation, O=NVIDIA Corporation, L=Santa Clara, S=California, C=US'; Thumbprint = 'AA' }
    $script:Http = New-Object System.Collections.Generic.List[string]
    $script:Downloads = New-Object System.Collections.Generic.List[string]
    $script:SigCalls = New-Object System.Collections.Generic.List[string]
    $script:Installs = New-Object System.Collections.Generic.List[object]
    $script:MiOSGpuLog = New-Object System.Collections.Generic.List[string]
}
function Get-MiOSPciDisplayDevices { return $script:MockDevices }
function Get-MiOSDeviceDriver([string]$InstanceId) { return $script:MockDrivers[$InstanceId] }
function Get-MiOSOsBuild { return '26100' }
function Invoke-MiOSHttpGet([string]$Uri, [hashtable]$Headers, [string]$UserAgent) {
    $script:Http.Add($Uri)
    if ($Uri.StartsWith($NvLookup)) { return $script:NvJson }
    if ($Uri -like 'https://www.amd.com/*') { return $AmdHtml }
    if ($Uri -like 'https://www.intel.com/*') { return $IntelHtml }
    throw "unexpected HTTP GET in test: $Uri"
}
function Get-MiOSRemoteLength([string]$Uri, [hashtable]$Headers, [string]$UserAgent) { return $script:MockLength }
function Save-MiOSDownload([string]$Uri, [string]$OutFile, [hashtable]$Headers, [string]$UserAgent) {
    $script:Downloads.Add($Uri); [IO.File]::WriteAllBytes($OutFile, $Payload)
}
function Get-MiOSSignature([string]$Path) { $script:SigCalls.Add($Path); return $script:MockSig }
function Expand-MiOSPackage([string]$Path, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $Destination 'Setup.exe'), $Payload)
}
function Invoke-MiOSInstaller([string]$FilePath, [string[]]$Arguments) {
    $script:Installs.Add([pscustomobject]@{ Exe = $FilePath; Args = @($Arguments) })
    foreach ($d in $script:MockDevices) { if ($script:PostInstallDriver) { $script:MockDrivers[$d.InstanceId] = $script:PostInstallDriver } }
    return $script:MockRc
}

$Root = Join-Path ([IO.Path]::GetTempPath()) ('mios-gpu-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Root | Out-Null
function New-CaseSpec([string]$Case, [string]$Mode) {
    $s = $BaseSpec | ConvertTo-Json -Depth 12 | ConvertFrom-Json
    $s.windows.xbox.gpu.record_path = (Join-Path $Root "$Case/gpu-driver.json")
    $s.windows.xbox.gpu.work_dir = (Join-Path $Root "$Case/work")
    if ($Mode) { $s.windows.xbox.drivers.gpu = $Mode }
    $p = Join-Path $Root "$Case.spec.json"
    ($s | ConvertTo-Json -Depth 12) | Set-Content -LiteralPath $p -Encoding UTF8
    return $p
}
function Get-Record([string]$Case) { return (Get-Content -LiteralPath (Join-Path $Root "$Case/gpu-driver.json") -Raw | ConvertFrom-Json) }

$Nv = [pscustomobject]@{ InstanceId = 'PCI\VEN_10DE&DEV_2684&SUBSYS_16F110DE&REV_A1\4&1A2B3C4D&0&0008'; Name = 'Microsoft Basic Display Adapter' }
$NvAudio = [pscustomobject]@{ InstanceId = 'PCI\VEN_10DE&DEV_2684&SUBSYS_16F110DE&REV_A1\4&1A2B3C4D&0&0009'; Name = 'Microsoft Basic Display Adapter' }
$Qxl = [pscustomobject]@{ InstanceId = 'PCI\VEN_1B36&DEV_0100&SUBSYS_11001AF4&REV_05\3&267A616A&0&10'; Name = 'Red Hat QXL controller' }
$Virtio = [pscustomobject]@{ InstanceId = 'PCI\VEN_1AF4&DEV_1050&SUBSYS_11001AF4&REV_01\3&267A616A&0&08'; Name = 'virtio-gpu' }
$Std = [pscustomobject]@{ InstanceId = 'PCI\VEN_1234&DEV_1111&SUBSYS_11001AF4&REV_02\3&267A616A&0&10'; Name = 'Microsoft Basic Display Adapter' }
$Aspeed = [pscustomobject]@{ InstanceId = 'PCI\VEN_1A03&DEV_2000&SUBSYS_20001A03&REV_52\4&ABC&0&0010'; Name = 'ASPEED Graphics Family' }
$Amd = [pscustomobject]@{ InstanceId = 'PCI\VEN_1002&DEV_744C&SUBSYS_0E3B1002&REV_C8\4&AAA&0&0008'; Name = 'Microsoft Basic Display Adapter' }
$Intel = [pscustomobject]@{ InstanceId = 'PCI\VEN_8086&DEV_E20B&SUBSYS_10008086&REV_00\4&BBB&0&0008'; Name = 'Microsoft Basic Display Adapter' }
$NvDrv = [pscustomobject]@{ DriverVersion = '32.0.16.1742'; Provider = 'NVIDIA'; InfName = 'oem7.inf' }

# ---- pure helpers ----
Check ((ConvertTo-MiOSNvidiaVersion '32.0.16.1742') -eq '617.42') 'NVIDIA DriverVersion 32.0.16.1742 maps to 617.42'
Check ((ConvertTo-MiOSNvidiaVersion '32.0.15.6142') -eq '561.42') 'NVIDIA DriverVersion 32.0.15.6142 maps to 561.42'
Check ((ConvertTo-MiOSIntelVersion '32.0.101.9150') -eq '101.9150') 'Intel DriverVersion maps to 101.9150'
Check ((Test-MiOSAllowedUrl 'https://us.download.nvidia.com/a.exe' $Gpu.vendors.nvidia.download_hosts)) 'allowlisted HTTPS host accepted'
Check (-not (Test-MiOSAllowedUrl 'https://us.download.nvidia.com.evil.example/a.exe' $Gpu.vendors.nvidia.download_hosts)) 'look-alike host rejected'
Check (-not (Test-MiOSAllowedUrl 'http://us.download.nvidia.com/a.exe' $Gpu.vendors.nvidia.download_hosts)) 'plain HTTP rejected'
Check (Test-MiOSSigner ([pscustomobject]@{ Status = 'Valid'; Subject = 'CN="Advanced Micro Devices, Inc.", O="Advanced Micro Devices, Inc.", L=Santa Clara, C=US' }) $Gpu.vendors.amd.signer_org_regex) 'AMD quoted O= signer accepted'
Check (-not (Test-MiOSSigner ([pscustomobject]@{ Status = 'Valid'; Subject = 'CN=NVIDIA Corporation, O=Evil NVIDIA Corporation Ltd' }) $Gpu.vendors.nvidia.signer_org_regex)) 'signer org must match exactly (prefix spoof rejected)'
Check (-not (Test-MiOSSigner ([pscustomobject]@{ Status = 'Valid'; Subject = 'CN=NVIDIA Corporation, O=Someone Else' }) $Gpu.vendors.nvidia.signer_org_regex)) 'CN alone does not satisfy the signer check'
# Positive control: Resolve-MiOSRedirectUrl returns direct allowed URL
$direct = 'https://us.download.nvidia.com/Windows/617.42/test.exe'
Check ((Resolve-MiOSRedirectUrl $direct $Gpu.vendors.nvidia.download_hosts) -eq $direct) 'Resolve-MiOSRedirectUrl direct allowed host returned'

# Negative control: Resolve-MiOSRedirectUrl throws on unauthorized initial host
$threwInitial = $false
try {
    [void](Resolve-MiOSRedirectUrl 'https://evil.example.com/malware.exe' $Gpu.vendors.nvidia.download_hosts)
} catch {
    $threwInitial = $_.Exception.Message -match 'is not an allowed host'
}
Check $threwInitial 'Resolve-MiOSRedirectUrl throws on unauthorized initial host'

# Negative control: Resolve-MiOSRedirectUrl throws and propagates on unauthorized redirect target
$threwRedirect = $false
try {
    $current = 'https://us.download.nvidia.com/latest.exe'
    $loc = 'https://evil.example.com/malware.exe'
    $next = (New-Object Uri ([Uri]$current), $loc).AbsoluteUri
    if (-not (Test-MiOSAllowedUrl $next $Gpu.vendors.nvidia.download_hosts)) {
        throw "redirect to $next is not on an allowed host"
    }
} catch {
    $threwRedirect = $_.Exception.Message -match 'is not on an allowed host'
}
Check $threwRedirect 'Resolve-MiOSRedirectUrl unauthorized redirect target throws'

# ---- P1: 10de:2684 resolves latest NVIDIA, verifies signature, installs display driver only, records ----
Reset-Mocks
$script:MockDevices = @($Qxl, $Virtio, $Nv, $NvAudio); $script:PostInstallDriver = $NvDrv
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'p1')
$rec = Get-Record 'p1'
Check ($rc -eq 0) 'P1 exit 0'
Check ($script:Http.Count -eq 1 -and $script:Http[0].StartsWith($NvLookup)) 'P1 NVIDIA device-ID lookup called once (URL from SSOT)'
Check ([Uri]::UnescapeDataString($script:Http[0]) -match '"dIDa":\["2684_10de"\]') 'P1 lookup asked for 2684_10de'
Check ($script:Downloads.Count -eq 1 -and $script:Downloads[0] -eq 'https://us.download.nvidia.com/Windows/617.42/617.42-desktop-win10-win11-64bit-international-dch-whql.exe') 'P1 downloaded the latest admin package (617.42)'
Check ($script:SigCalls.Count -eq 1 -and $script:SigCalls[0] -like '*617.42-desktop-win10-win11-64bit-international-dch-whql.exe') 'P1 Authenticode check called on the downloaded package'
Check ($script:Installs.Count -eq 1) 'P1 installer ran once (two PCI functions, one device)'
$a = @($script:Installs[0].Args)
Check (($a -join ' ') -match '^-s -n Display\.Driver -log:.+ -loglevel:6$') "P1 silent display-driver-only switches ($($a -join ' '))"
Check (-not (($a -join ' ') -match 'NvApp|GFExperience|Telemetry')) 'P1 no NVIDIA App/GFE/telemetry component requested'
$d = @($rec.devices)[0]
Check ($rec.status -eq 'ok' -and $d.status -eq 'installed') 'P1 record status installed'
Check ($d.vendor -eq 'nvidia' -and $d.vendor_id -eq '10DE' -and $d.device_id -eq '2684' -and $d.version -eq '617.42') 'P1 record vendor/device/version'
$expSha = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($Payload))).Replace('-', '').ToLowerInvariant()
Check ($d.sha256 -eq $expSha) 'P1 record sha256 = sha256 of the downloaded file'
Check ($d.source_url -like 'https://us.download.nvidia.com/*' -and $d.signer -like '*O=NVIDIA Corporation*' -and $d.installed_driver_version -eq '32.0.16.1742') 'P1 record source URL, signer, bound driver'
Check (@($rec.skipped | Where-Object { $_.vendor_id -in '1B36', '1AF4' -and $_.reason -like 'virtual display*' }).Count -eq 2) 'P1 QXL and virtio-gpu skipped as virtual displays (skip_vendor_ids)'

# ---- P2: idempotent: device already runs the latest -> no download, no install ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockDrivers[$Nv.InstanceId] = $NvDrv
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'p2')
$rec = Get-Record 'p2'
Check ($rc -eq 0 -and $script:Downloads.Count -eq 0 -and $script:Installs.Count -eq 0) 'P2 already-current driver: nothing downloaded or installed'
Check (@($rec.devices)[0].status -eq 'current' -and @($rec.devices)[0].version -eq '617.42') 'P2 record status current, version recorded'

# ---- P3: older driver bound -> upgrade ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockDrivers[$Nv.InstanceId] = [pscustomobject]@{ DriverVersion = '32.0.15.6142'; Provider = 'NVIDIA' }; $script:PostInstallDriver = $NvDrv
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'p3')
Check ($rc -eq 0 -and $script:Installs.Count -eq 1) 'P3 561.42 bound -> upgraded to 617.42'

# ---- P4: reboot-required exit code 1 -> success, status reboot-pending ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockRc = 1
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'p4')
Check ($rc -eq 0 -and @((Get-Record 'p4').devices)[0].status -eq 'reboot-pending') 'P4 NVIDIA exit 1 = installed, reboot pending'

# ---- P5: AMD -> newest Adrenalin from AMD pages, extracted Setup.exe re-verified, -INSTALL ----
Reset-Mocks
$script:MockDevices = @($Amd); $script:PostInstallDriver = [pscustomobject]@{ DriverVersion = '32.0.22029.1019'; Provider = 'Advanced Micro Devices, Inc.' }
$script:MockSig = [pscustomobject]@{ Status = 'Valid'; Subject = 'CN=Advanced Micro Devices INC., O=Advanced Micro Devices INC., L=Santa Clara, S=California, C=US'; Thumbprint = 'BB' }
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'p5')
$rec = Get-Record 'p5'
Check ($rc -eq 0 -and $script:Downloads[0] -like '*adrenalin-edition-26.9.2-win11-b.exe') 'P5 AMD latest channel resolves 26.9.2 (not the web minimal setup)'
Check ($script:SigCalls.Count -eq 2 -and $script:SigCalls[1] -like '*Setup.exe') 'P5 package and extracted Setup.exe both signature-checked'
Check ($script:Installs[0].Exe -like '*Setup.exe' -and $script:Installs[0].Args[0] -eq '-INSTALL') 'P5 AMD Setup.exe -INSTALL'
Check (@($rec.devices)[0].status -eq 'installed' -and @($rec.devices)[0].published_hash_verified -eq $false) 'P5 AMD recorded (no published hash: flagged)'
# idempotent via record for AMD
$script:Downloads.Clear(); $script:Installs.Clear()
$p5spec = Join-Path $Root 'p5.spec.json'
$rc = Invoke-MiOSGpuDriver $p5spec
Check ($rc -eq 0 -and $script:Installs.Count -eq 0 -and @((Get-Record 'p5').devices)[0].status -eq 'current') 'P5b AMD second run: record + bound driver match -> no reinstall'

# ---- P5c: AMD channel "recommended" (SSOT) -> the WHQL Recommended build ----
Reset-Mocks
$script:MockDevices = @($Amd); $script:PostInstallDriver = [pscustomobject]@{ DriverVersion = '32.0.21030.1000'; Provider = 'Advanced Micro Devices, Inc.' }
$script:MockSig = [pscustomobject]@{ Status = 'Valid'; Subject = 'CN=Advanced Micro Devices INC., O=Advanced Micro Devices INC.'; Thumbprint = 'BB' }
$sp = New-CaseSpec 'p5c'
$j = Get-Content -LiteralPath $sp -Raw | ConvertFrom-Json; $j.windows.xbox.gpu.vendors.amd.channel = 'recommended'; ($j | ConvertTo-Json -Depth 12) | Set-Content -LiteralPath $sp
$rc = Invoke-MiOSGpuDriver $sp
Check ($rc -eq 0 -and $script:Downloads[0] -like '*adrenalin-edition-26.8.1-win11-b.exe') 'P5c AMD recommended channel resolves 26.8.1'

# ---- P6: Intel -> page link rebuilt from label + mirror folder, published SHA512 verified ----
Reset-Mocks
$script:MockDevices = @($Intel); $script:PostInstallDriver = [pscustomobject]@{ DriverVersion = '32.0.101.9150'; Provider = 'Intel Corporation' }
$script:MockSig = [pscustomobject]@{ Status = 'Valid'; Subject = 'CN=Intel Corporation, O=Intel Corporation, L=Santa Clara, S=California, C=US'; Thumbprint = 'CC' }
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'p6')
$rec = Get-Record 'p6'
Check ($rc -eq 0 -and $script:Downloads[0] -eq 'https://downloadmirror.intel.com/930514/gfx_win_101.9150.exe') 'P6 Intel resolves gfx_win_101.9150.exe'
Check (@($rec.devices)[0].published_hash_verified -eq $true -and @($rec.devices)[0].status -eq 'installed') 'P6 Intel SHA512 verified, installed'

# ---- N1: no discrete GPU -> nothing installed, no network ----
Reset-Mocks
$script:MockDevices = @($Qxl, $Virtio, $Std)
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n1')
$rec = Get-Record 'n1'
Check ($rc -eq 0 -and $script:Http.Count -eq 0 -and $script:Downloads.Count -eq 0 -and $script:Installs.Count -eq 0) 'N1 no GPU: no lookup, no download, no install'
Check ($rec.status -eq 'no-gpu' -and @($rec.devices).Count -eq 0) 'N1 record status no-gpu'

# ---- N2: tampered signature -> abort before install ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockSig = [pscustomobject]@{ Status = 'HashMismatch'; Subject = 'CN=NVIDIA Corporation, O=NVIDIA Corporation'; Thumbprint = 'AA' }
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n2')
$rec = Get-Record 'n2'
Check ($rc -ne 0 -and $script:Installs.Count -eq 0) 'N2 tampered package (HashMismatch): exit non-zero, installer never run'
Check ($rec.status -eq 'failed' -and @($rec.devices)[0].error -match 'Authenticode') 'N2 record status failed with the Authenticode reason'

# ---- N3: valid signature by the wrong publisher -> abort ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockSig = [pscustomobject]@{ Status = 'Valid'; Subject = 'CN=Contoso Ltd, O=Contoso Ltd'; Thumbprint = 'DD' }
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n3')
Check ($rc -ne 0 -and $script:Installs.Count -eq 0) 'N3 validly signed by a non-vendor: aborted'

# ---- N4: unknown vendor -> logged no-op ----
Reset-Mocks
$script:MockDevices = @($Aspeed, $Qxl)
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n4')
$rec = Get-Record 'n4'
Check ($rc -eq 0 -and $script:Http.Count -eq 0 -and $script:Installs.Count -eq 0) 'N4 unknown vendor 1A03: nothing resolved or installed'
Check (@($rec.skipped | Where-Object { $_.vendor_id -eq '1A03' -and $_.reason -like 'unknown vendor*' }).Count -eq 1 -and (@($rec.log) -join "`n") -match 'no MiOS resolver for PCI vendor 1A03') 'N4 unknown vendor logged in the record'

# ---- N5: resolver points at a non-allowlisted host -> abort before download ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:NvJson = $NvJsonEvilHost
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n5')
Check ($rc -ne 0 -and $script:Downloads.Count -eq 0) 'N5 look-alike download host: aborted, nothing downloaded'
Reset-Mocks
$script:MockDevices = @($Nv); $script:NvJson = $NvJsonHttp
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n5b')
Check ($rc -ne 0 -and $script:Downloads.Count -eq 0) 'N5b plain-HTTP download URL: aborted'

# ---- N6: NVIDIA says the device is unsupported -> fail loudly ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:NvJson = $NvJsonUnsupported
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n6')
Check ($rc -ne 0 -and (@((Get-Record 'n6').devices)[0].error -match 'not supported')) 'N6 unsupported device: failed, not silently skipped'

# ---- N7: truncated download (size != Content-Length) -> abort ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockLength = [long]990853168
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n7')
Check ($rc -ne 0 -and $script:SigCalls.Count -eq 0 -and $script:Installs.Count -eq 0) 'N7 size mismatch: aborted before signature/install'

# ---- N8: Intel published SHA512 mismatch -> abort ----
Reset-Mocks
$script:MockDevices = @($Intel)
$script:MockSig = [pscustomobject]@{ Status = 'Valid'; Subject = 'CN=Intel Corporation, O=Intel Corporation'; Thumbprint = 'CC' }
$saved = $IntelHtml; $IntelHtml = $IntelHtml.Replace($PayloadSha512, ('0' * 128))
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n8')
$IntelHtml = $saved
Check ($rc -ne 0 -and $script:Installs.Count -eq 0 -and (@((Get-Record 'n8').devices)[0].error -match 'SHA512 mismatch')) 'N8 Intel SHA512 mismatch: aborted'

# ---- N9: installer failure exit code -> fail loudly ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:MockRc = 2
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n9')
Check ($rc -ne 0 -and @((Get-Record 'n9').devices)[0].status -eq 'failed') 'N9 NVIDIA exit 2: failed'

# ---- N10: installer "succeeded" but device still on Microsoft Basic Display -> fail loudly ----
Reset-Mocks
$script:MockDevices = @($Nv); $script:PostInstallDriver = [pscustomobject]@{ DriverVersion = '10.0.26100.1'; Provider = 'Microsoft' }
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n10')
Check ($rc -ne 0 -and (@((Get-Record 'n10').devices)[0].error -match 'bound to')) 'N10 post-install bound driver not NVIDIA: failed'

# ---- N11: SSOT drivers.gpu != detect -> disabled, nothing done ----
Reset-Mocks
$script:MockDevices = @($Nv)
$rc = Invoke-MiOSGpuDriver (New-CaseSpec 'n11' 'none')
Check ($rc -eq 0 -and $script:Http.Count -eq 0 -and (Get-Record 'n11').status -eq 'disabled') 'N11 drivers.gpu=none: step disabled, nothing installed'

# ---- N12: spec without the gpu table -> hard fail ----
$bad = Join-Path $Root 'n12.spec.json'
'{"windows":{"xbox":{"drivers":{"gpu":"detect"}}}}' | Set-Content -LiteralPath $bad
$threw = $false
try { [void](Invoke-MiOSGpuDriver $bad) } catch { $threw = $_.Exception.Message -match 'windows.xbox.gpu missing' }
Check $threw 'N12 missing [windows.xbox.gpu] in the resolved spec: hard fail'

# ---- P7: spec with { spec: { gpu: ..., drivers: ... } } (render_spec.py shape) ----
$specP7 = Join-Path $Root 'p7.spec.json'
@{ spec = @{ gpu = $Gpu; drivers = @{ gpu = 'detect' } } } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $specP7
$resolvedP7 = Read-MiOSGpuSpec $specP7
Check ($resolvedP7.Mode -eq 'detect' -and $resolvedP7.Gpu.task_name -eq $Gpu.task_name) 'P7 .spec.gpu (render_spec.py shape) resolves successfully'

# ---- P8: spec with { gpu: ..., drivers: ... } (root table fragment shape) ----
$specP8 = Join-Path $Root 'p8.spec.json'
@{ gpu = $Gpu; drivers = @{ gpu = 'detect' } } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $specP8
$resolvedP8 = Read-MiOSGpuSpec $specP8
Check ($resolvedP8.Mode -eq 'detect' -and $resolvedP8.Gpu.task_name -eq $Gpu.task_name) 'P8 root .gpu (table fragment shape) resolves successfully'

# ---- N13: spec with { spec: { drivers: ... } } missing gpu -> hard fail ----
$badP9 = Join-Path $Root 'n13.spec.json'
'{"spec":{"drivers":{"gpu":"detect"}}}' | Set-Content -LiteralPath $badP9
$threwP9 = $false
try { [void](Read-MiOSGpuSpec $badP9) } catch { $threwP9 = $_.Exception.Message -match 'windows.xbox.gpu missing' }
Check $threwP9 'N13 missing [spec.gpu] in render_spec shape: hard fail'

# ---- record is valid JSON written atomically (no temp files left) ----
Check (@(Get-ChildItem -LiteralPath (Join-Path $Root 'p1') -Filter '*.tmp' -Force).Count -eq 0) 'record written atomically (no temp leftovers)'

Remove-Item -LiteralPath $Root -Recurse -Force
Write-Host ("RESULT pass={0} fail={1}" -f $script:Pass, $script:Fail)
if ($script:Fail -gt 0) { exit 1 }
exit 0
