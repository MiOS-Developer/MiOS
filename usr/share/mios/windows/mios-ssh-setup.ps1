# AI-hint: Install and configure the Windows SSH service from resolved MiOS SSOT settings. Default mode: the OpenSSH release floats -- [ssh.windows].release_api names upstream's latest release, the asset is proven by the sha256 GitHub publishes for it plus the Microsoft Authenticode check, and the resolved tag/sha256 are written to <install>\mios-openssh-provenance.json (never the SSOT). -InboxOpenSsh (MiOS-Xbox, F4): configure the inbox OpenSSH.Server capability the image baked offline -- no download, no network; the service ImagePath points at the SSOT sshd_config.
# AI-related: usr/share/mios/mios.toml [ssh.windows], [ports].ssh, [windows.xbox.ssh], usr/libexec/mios/win/xbox/answer.py
# AI-functions: Install-MiOSWindowsSsh
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SettingsPath,
    [Parameter(Mandatory)][string]$AuthorizedKeysPath,
    # Resolve, download and prove the release, write the provenance record here, then stop
    # before anything is installed (pre-flight and hermetic tests).
    [string]$StageOnlyProvenancePath,
    # MiOS-Xbox: use the inbox OpenSSH.Server Feature on Demand (baked offline into the image) instead of
    # downloading a release; nothing in this mode touches the network.
    [switch]$InboxOpenSsh
)
$ErrorActionPreference='Stop'
function Test-SHA256Integrity {
    param([string]$Path, [string]$Sha256)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ieq $Sha256
}
function Resolve-MiOSOpenSshRelease {
    # Latest release from the forge; the digest is the one GitHub publishes for the asset.
    param($Cfg)
    if($Cfg.release_api -notmatch '^https://'){throw 'SSOT [ssh.windows].release_api is missing'}
    if([string]::IsNullOrWhiteSpace($Cfg.asset)){throw 'SSOT [ssh.windows].asset is missing'}
    $release=Invoke-RestMethod -UseBasicParsing -Uri $Cfg.release_api -Headers @{Accept='application/vnd.github+json';'User-Agent'='MiOS'}
    $asset=@($release.assets | Where-Object { $_.name -eq $Cfg.asset })
    if($asset.Count -ne 1){throw "Upstream release $($release.tag_name) has no single asset $($Cfg.asset)"}
    $digest=[regex]::Match([string]$asset[0].digest,'^sha256:([a-fA-F0-9]{64})$')
    if(-not $digest.Success){throw "Upstream publishes no sha256 for $($Cfg.asset) $($release.tag_name)"}
    if([string]$asset[0].browser_download_url -notmatch '^https://'){throw 'Upstream asset URL is not https'}
    [pscustomobject]@{tag=[string]$release.tag_name; url=[string]$asset[0].browser_download_url; sha256=$digest.Groups[1].Value.ToLowerInvariant()}
}
$settings=Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
$cfg=$settings.windows
if([int]$settings.port -lt 1 -or [int]$settings.port -gt 65535){throw 'Resolved SSOT SSH port is invalid'}
if(-not (Test-Path -LiteralPath $AuthorizedKeysPath -PathType Leaf)){throw 'Operator SSH public keys are required'}
$keys=Get-Content -LiteralPath $AuthorizedKeysPath
if(-not @($keys | Where-Object {$_ -match '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[^ ]+) [A-Za-z0-9+/=]+'}).Count){throw 'No supported operator SSH public key found'}
$root=[IO.Path]::GetFullPath($cfg.root)
$install=if($InboxOpenSsh){Join-Path $env:SystemRoot 'System32\OpenSSH'}else{Join-Path $root $cfg.install_relative}
$configDir=Join-Path $root $cfg.config_relative
$sshConfig=Join-Path $configDir 'sshd_config'
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
if(-not $InboxOpenSsh){New-Item -ItemType Directory -Path $install -Force | Out-Null}
$staging=Join-Path ([IO.Path]::GetTempPath()) ('mios-ssh-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
  if($InboxOpenSsh){
    if($StageOnlyProvenancePath){throw '-StageOnlyProvenancePath resolves a download; -InboxOpenSsh never downloads'}
    $exe=Join-Path $install 'sshd.exe'
    if(-not (Test-Path -LiteralPath $exe -PathType Leaf)){throw "The inbox OpenSSH server is not installed ($exe): the image must bake the OpenSSH.Server capability"}
    foreach($bin in 'sshd.exe','ssh-keygen.exe'){
        $signature=Get-AuthenticodeSignature -LiteralPath (Join-Path $install $bin)
        if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)'){throw "Invalid inbox Microsoft SSH executable: $bin"}
    }
    $service=Get-Service -Name sshd -ErrorAction SilentlyContinue
    if(-not $service){throw 'The OpenSSH.Server capability registered no sshd service'}
    if($service.Status -eq 'Running'){Stop-Service -Name sshd}
    $provenance=([ordered]@{component='OpenSSH.Server (inbox Feature on Demand)'; path=$exe; version=[string](Get-Item -LiteralPath $exe).VersionInfo.FileVersion; recorded_at=(Get-Date).ToString('o')} | ConvertTo-Json)
    [IO.File]::WriteAllText((Join-Path $configDir 'mios-openssh-provenance.json'),$provenance,(New-Object Text.UTF8Encoding($false)))
  } else {
    $archive=Join-Path $staging 'openssh.zip'
    $release=Resolve-MiOSOpenSshRelease $cfg
    Write-Host "[*] OpenSSH latest release: $($release.tag) ($($release.url))"
    Invoke-WebRequest -UseBasicParsing -Uri $release.url -OutFile $archive
    if(-not (Test-SHA256Integrity $archive $release.sha256)){throw "SSH archive checksum mismatch for $($release.tag)"}
    $provenance=([ordered]@{component='Win32-OpenSSH'; tag=$release.tag; url=$release.url; sha256=$release.sha256; recorded_at=(Get-Date).ToString('o')} | ConvertTo-Json)
    if($StageOnlyProvenancePath){
        [IO.File]::WriteAllText($StageOnlyProvenancePath,$provenance,(New-Object Text.UTF8Encoding($false)))
        Write-Host "[+] OpenSSH $($release.tag) resolved and verified (stage only)."
        return
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $staging
    $payload=@(Get-ChildItem -LiteralPath $staging -Recurse -Filter sshd.exe)
    if($payload.Count -ne 1){throw 'SSH archive must contain exactly one server executable'}
    foreach($exe in Get-ChildItem -LiteralPath $payload[0].DirectoryName -Filter '*.exe'){
        $signature=Get-AuthenticodeSignature -LiteralPath $exe.FullName
        if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)'){throw "Invalid Microsoft SSH executable: $($exe.Name)"}
    }
    $service=Get-Service -Name sshd -ErrorAction SilentlyContinue
    if($service -and $service.Status -eq 'Running'){Stop-Service -Name sshd}
    Copy-Item -Path (Join-Path $payload[0].DirectoryName '*') -Destination $install -Recurse -Force
    [IO.File]::WriteAllText((Join-Path $install 'mios-openssh-provenance.json'),$provenance,(New-Object Text.UTF8Encoding($false)))
  }
    $keyFile=Join-Path $configDir 'administrators_authorized_keys'
    Copy-Item -LiteralPath $AuthorizedKeysPath -Destination $keyFile -Force
    & icacls.exe $keyFile /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Cannot restrict operator SSH key file permissions'}
    & icacls.exe $keyFile /setowner '*S-1-5-32-544' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Cannot set operator SSH key file owner'}
    $exe=Join-Path $install 'sshd.exe'
    $keyPath=$keyFile.Replace('\','/')
    $hostKey=Join-Path $configDir 'ssh_host_ed25519_key'
    if(-not (Test-Path -LiteralPath $hostKey)){
        & (Join-Path $install 'ssh-keygen.exe') -t ed25519 -N '""' -f $hostKey | Out-Null
        if($LASTEXITCODE -ne 0){throw 'Cannot generate SSH host key'}
    }
    & icacls.exe $hostKey /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Cannot restrict SSH host key permissions'}
    & icacls.exe $hostKey /setowner '*S-1-5-32-544' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Cannot set SSH host key owner'}
    $lines=@(
        "Port $($settings.port)", "ListenAddress $($cfg.listen_address)",
        "HostKey $($hostKey.Replace('\','/'))", 'PubkeyAuthentication yes',
        'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
        "AuthorizedKeysFile $keyPath", 'LogLevel VERBOSE',
        "Subsystem sftp $((Join-Path $install 'sftp-server.exe').Replace('\','/'))"
    )
    [IO.File]::WriteAllLines($sshConfig,$lines,(New-Object Text.UTF8Encoding($false)))
    & $exe -t -f $sshConfig
    if($LASTEXITCODE -ne 0){throw 'SSH rejected the SSOT configuration'}
    $binary='"{0}" -f "{1}"' -f $exe,$sshConfig
    if($service){$service.Close()}
    if($InboxOpenSsh){
        # the capability registered the service; point it at the SSOT config (no sc.exe argument quoting involved)
        Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\sshd' -Name 'ImagePath' -Value $binary -Type ExpandString
        Set-Service -Name sshd -StartupType Automatic
        Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    } else {
    $serviceInstaller=Join-Path $install 'install-sshd.ps1'
    $signature=Get-AuthenticodeSignature -LiteralPath $serviceInstaller
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)'){throw 'Invalid Microsoft SSH service installer'}
    & $serviceInstaller -Confirm:$false
    if(-not (Get-Service -Name sshd -ErrorAction SilentlyContinue)){throw 'Microsoft SSH service registration failed'}
    & sc.exe config sshd binPath= $binary start= auto | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Cannot apply the SSOT SSH service configuration'}
    }
    $rule=Get-NetFirewallRule -Name 'MiOS-SSH' -ErrorAction SilentlyContinue
    if($rule){Remove-NetFirewallRule -Name 'MiOS-SSH'}
    New-NetFirewallRule -Name 'MiOS-SSH' -DisplayName 'MiOS SSH' -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort ([int]$settings.port) -RemoteAddress @($cfg.remote_addresses) -Program $exe | Out-Null
    Start-Service -Name sshd
    if((Get-Service -Name sshd).Status -ne 'Running'){throw 'SSH service did not start'}
    # sshd binds a moment after the SCM reports Running: wait (bounded) for the listener instead of racing it
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    while(-not (Get-NetTCPConnection -State Listen -LocalPort ([int]$settings.port) -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Seconds 1}
    if(-not (Get-NetTCPConnection -State Listen -LocalPort ([int]$settings.port) -ErrorAction SilentlyContinue)){throw 'SSH did not bind the configured port'}
    Write-Host "[+] MiOS SSH is listening on SSOT port $($settings.port)."
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
