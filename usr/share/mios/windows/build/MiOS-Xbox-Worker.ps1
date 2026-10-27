# AI-hint: MiOS-Xbox build job on the GPU-less worker (xb-15): runs as SYSTEM from the exchange volume the host prepared (job\: guest spec, payload, drivers, UUP set, capability FOD set, converter, Start assets, job.json), converts the verified UUP set with UUP Dump's Windows converter (ConvertConfig.ini rendered from the SSOT), drops the UUP set once the media exists, then New-MiOSXboxImage.ps1 services the media and masters the ISO into out\; on success the scratch (work\) is removed and the exchange volume re-TRIMmed before status done, so the thin exchange disk gives its space back before the host copies the ISO out. status.json (stage/status/error) is what the host polls over WinRM; nothing here talks to the network.
# AI-related: usr/libexec/mios/win/xbox/worker.py, usr/libexec/mios/win/xbox/build.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1
#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Root)   # <exchange volume>:\mios-build

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$job = Join-Path $Root 'job'
$out = Join-Path $Root 'out'
$work = Join-Path $Root 'work'
New-Item -ItemType Directory -Force -Path $out, $work | Out-Null
$statusPath = Join-Path $out 'status.json'
function Set-Status([string]$Stage, [string]$Status, [string]$Err = '') {
    $doc = [ordered]@{ schema = 'mios-xbox-worker-status/1'; stage = $Stage; status = $Status; error = $Err; at = [DateTime]::UtcNow.ToString('o') }
    $tmp = "$statusPath.tmp"
    [IO.File]::WriteAllText($tmp, ($doc | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination $statusPath -Force
}
Start-Transcript -LiteralPath (Join-Path $out 'worker.log') -Force | Out-Null
$stage = 'start'
try {
    $j = Get-Content -LiteralPath (Join-Path $job 'job.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($j.schema -ne 'mios-xbox-job/1') { throw 'job.json is not a mios-xbox-job/1 document' }

    $stage = 'convert'; Set-Status $stage 'running'
    $conv = Join-Path $work 'converter'
    if (Test-Path -LiteralPath $conv) { Remove-Item -LiteralPath $conv -Recurse -Force }
    & robocopy.exe (Join-Path $job 'converter') $conv /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "staging the converter failed ($LASTEXITCODE)" }
    $uups = Join-Path $conv 'UUPs'
    New-Item -ItemType Directory -Force -Path $uups | Out-Null
    & robocopy.exe (Join-Path $job 'uup') $uups /E /MOV /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "staging the UUP set failed ($LASTEXITCODE)" }
    $entry = Join-Path $conv ([string]$j.converter_entry)
    if (-not (Test-Path -LiteralPath $entry)) { throw "converter entry $($j.converter_entry) missing" }
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/d', '/c', ('"{0}"' -f $entry)) -WorkingDirectory $conv -Wait -PassThru -WindowStyle Hidden
    $media = Join-Path $conv 'ISOFOLDER'
    if (-not (Test-Path -LiteralPath (Join-Path $media 'sources\install.wim')) -and -not (Test-Path -LiteralPath (Join-Path $media 'sources\install.esd'))) {
        $cand = Get-ChildItem -Path $conv -Directory | Where-Object { (Test-Path (Join-Path $_.FullName 'sources\install.wim')) -or (Test-Path (Join-Path $_.FullName 'sources\install.esd')) } | Select-Object -First 1
        if ($cand) { $media = $cand.FullName }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $media 'sources\install.wim')) -and -not (Test-Path -LiteralPath (Join-Path $media 'sources\install.esd'))) {
        throw "UUP conversion produced no media (exit $($p.ExitCode); see the converter log in $conv)"
    }
    # Disk (build-20261023T224037Z ran /var/lib/mios-build down to 5.6 GB): the converted media is all servicing reads,
    # so the UUP set goes now and the servicing export + mount reuse its clusters on the thin exchange disk.
    if (([IO.Path]::GetFullPath($media) + '\').StartsWith([IO.Path]::GetFullPath($uups) + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "the converted media $media lies inside the UUP set $uups"
    }
    try { Remove-Item -LiteralPath $uups -Recurse -Force } catch { Write-Warning "UUP set $uups not removed: $($_.Exception.Message)" }

    $stage = 'service'; Set-Status $stage 'running'
    $tool = @((Join-Path $conv 'bin\cdimage.exe'), (Join-Path $conv 'bin\bin64\cdimage.exe')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    $svcArgs = @{
        SpecPath = (Join-Path $job 'xbox-spec.json'); MediaDir = $media; PayloadDir = (Join-Path $job 'payload')
        DriversDir = (Join-Path $job 'drivers'); StartAssetsDir = (Join-Path $job 'start-assets'); WorkDir = (Join-Path $work 'servicing')
        OutIso = (Join-Path $out ([string]$j.iso_name)); ResultPath = (Join-Path $out 'result.json'); IsoLabel = [string]$j.iso_label
        FodDir = (Join-Path $job 'fod')
    }
    if ($tool) { $svcArgs.OscdimgPath = $tool }
    & (Join-Path $job 'New-MiOSXboxImage.ps1') @svcArgs
    # out\ (ISO, result.json, logs) is all the host copies out: drop the scratch (converter, converted media, servicing
    # WIM) and re-TRIM the exchange volume before the worker shuts down, so the thin exchange disk hands that space back
    # to the build filesystem before the host copies the ISO. Scratch left behind costs disk, never the image.
    try {
        Remove-Item -LiteralPath $work -Recurse -Force
        Optimize-Volume -DriveLetter ($Root.Substring(0, 1)) -ReTrim | Out-Null
    } catch { Write-Warning "scratch cleanup of $work incomplete: $($_.Exception.Message)" }
    $stage = 'done'; Set-Status $stage 'done'
} catch {
    Set-Status $stage 'failed' ($_.Exception.Message)
} finally {
    Stop-Transcript | Out-Null
}
