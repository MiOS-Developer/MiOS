# AI-hint: Shared PowerShell module providing OCI image resolution, GHCR layer fetching, path conversion, and logging helpers for Windows image export scripts.
# AI-related: mios-cloud-build.ps1, mios-windows-export.ps1, tools/windows/Build-MiOS.ps1

function Write-Step([string]$Msg) { Write-Host ('▶ ' + $Msg) -ForegroundColor Cyan }
function Write-Ok([string]$Msg)   { Write-Host ('  [+] ' + $Msg) -ForegroundColor Green }
function Write-Warn([string]$Msg) { Write-Host ('  [!] ' + $Msg) -ForegroundColor Yellow }
function Write-Bad([string]$Msg)  { Write-Host ('  [X] ' + $Msg) -ForegroundColor Red }

function ConvertTo-WslPath([string]$WinPath) {
    if (-not $WinPath) { return '' }
    $drive = $WinPath.Substring(0,1).ToLower()
    $rest  = $WinPath.Substring(2) -replace '\\','/'
    return "/mnt/$drive$rest"
}

function Resolve-OutputBase([string]$CustomOutputDir = '') {
    if ($CustomOutputDir) { return $CustomOutputDir }
    if (Test-Path -LiteralPath 'M:\') { return 'M:\MiOS\build' }
    return Join-Path $env:USERPROFILE 'MiOS-Build'
}

function Get-GhcrToken([string]$Repo) {
    $tokUrl = "https://ghcr.io/token?scope=repository:$Repo`:pull&service=ghcr.io"
    $tok = Invoke-RestMethod -Uri $tokUrl -ErrorAction Stop
    if (-not $tok.token) { throw "GHCR /token returned no .token" }
    return $tok.token
}

function Resolve-ImageRef([string]$ImageRef) {
    if ($ImageRef -notmatch '^([^/]+)/(.+?)(?::([^:/@]+)|@(sha256:[a-f0-9]+))?$') {
        throw "Cannot parse image reference: $ImageRef"
    }
    $registry = $Matches[1]
    $repo     = $Matches[2]
    $tagOrDig = if ($Matches[3]) { $Matches[3] } elseif ($Matches[4]) { $Matches[4] } else { 'latest' }
    if ($registry -notin @('ghcr.io','registry.ghcr.io')) {
        throw "This path only supports ghcr.io. Got: $registry."
    }
    return @{ Registry = $registry; Repo = $repo; Ref = $tagOrDig }
}

function Get-ImageManifest([hashtable]$Ref, [string]$Token) {
    $headers = @{
        'Authorization' = "Bearer $Token"
        'Accept'        = 'application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'
    }
    $url = "https://$($Ref.Registry)/v2/$($Ref.Repo)/manifests/$($Ref.Ref)"
    $resp = Invoke-RestMethod -Uri $url -Headers $headers -ErrorAction Stop
    if ($resp.manifests) {
        $picked = $resp.manifests | Where-Object {
            $_.platform.architecture -eq 'amd64' -and $_.platform.os -eq 'linux'
        } | Select-Object -First 1
        if (-not $picked) {
            throw "No linux/amd64 manifest in the index for $($Ref.Repo):$($Ref.Ref)"
        }
        $headers['Accept'] = 'application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'
        $url = "https://$($Ref.Registry)/v2/$($Ref.Repo)/manifests/$($picked.digest)"
        $resp = Invoke-RestMethod -Uri $url -Headers $headers -ErrorAction Stop
    }
    return $resp
}

Export-ModuleMember -Function Write-Step, Write-Ok, Write-Warn, Write-Bad, ConvertTo-WslPath, Resolve-OutputBase, Get-GhcrToken, Resolve-ImageRef, Get-ImageManifest
