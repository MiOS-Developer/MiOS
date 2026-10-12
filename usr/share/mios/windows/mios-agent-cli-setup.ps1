# AI-hint: Install every SSOT agent CLI globally on a Windows management host, with machine PATH and native MCP client configuration.
# AI-related: /usr/share/mios/mios.toml [agent_cli], mios-native-client-setup.ps1

[CmdletBinding()]
param([Parameter(Mandatory)][string]$Distro, [Parameter(Mandatory)][string]$LinuxUser)
$ErrorActionPreference = 'Stop'
function Save-MiosVerifiedInstaller {
    param([string]$Url, [string]$Path, [string]$Sha256)
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'SSOT installer SHA-256 is missing or invalid' }
    Invoke-WebRequest -Uri $Url -OutFile $Path
    if (-not (Test-SHA256Integrity $Path $Sha256)) {
        throw "Installer SHA-256 mismatch: $Url"
    }
}
function Test-SHA256Integrity {
    param([string]$Path, [string]$Sha256)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ieq $Sha256
}
$resolve = 'import sys,json;sys.path.insert(0,"/usr/lib/mios");import mios_toml;print(json.dumps(mios_toml.load_merged()["agent_cli"]))'
$raw = & wsl.exe -d $Distro -u $LinuxUser -- python3 -c $resolve
if ($LASTEXITCODE -ne 0) { throw 'Could not read [agent_cli] from the native MiOS SSOT' }
$cfg = ($raw -join "`n") | ConvertFrom-Json
if (-not $cfg.enabled) { throw '[agent_cli].enabled is false' }
$names = @($cfg.tools | ForEach-Object { $_.name })
if (($names | Select-Object -Unique).Count -ne $names.Count -or @($names | Where-Object { $_ -notmatch '^[a-z][a-z0-9-]*$' }).Count) { throw 'Invalid or duplicate SSOT CLI names' }
$directory = Join-Path $env:ProgramData $cfg.windows_directory
[IO.Directory]::CreateDirectory($directory) | Out-Null
$nodeDir = Join-Path $env:ProgramFiles 'nodejs'
$node = Join-Path $nodeDir 'node.exe'
if (-not (Test-Path -LiteralPath $node) -or [int]((& $node --version).TrimStart('v').Split('.')[0]) -lt $cfg.node_min_major) {
    & winget.exe install --id $cfg.windows_node_package --exact --scope machine --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw 'Global Node.js installation failed' }
}
$env:PATH = "$nodeDir;$env:PATH"
$npm = Join-Path $nodeDir 'npm.cmd'
$npmRoot = Join-Path $directory 'npm'
$packages = @($cfg.tools | Where-Object { $_.kind -eq 'npm' } | ForEach-Object { $_.package })
& $npm install --global --prefix $npmRoot @packages
if ($LASTEXITCODE -ne 0) { throw 'Global agent npm installation failed' }

$native = Join-Path $directory 'native'
[IO.Directory]::CreateDirectory($native) | Out-Null
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('mios-agent-install-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temporary) | Out-Null
try {
    if (-not (Test-Path -LiteralPath (Join-Path $native 'agy.exe'))) {
        $script = Join-Path $temporary 'antigravity.ps1'
        Save-MiosVerifiedInstaller $cfg.antigravity_windows_installer $script $cfg.antigravity_windows_installer_sha256
        & $script --dir $native --skip-aliases --skip-path
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $native 'agy.exe'))) { throw 'Native Antigravity CLI installation failed' }
    }
    $uvBin = Join-Path $directory 'uv'
    $priorUvInstall = $env:UV_INSTALL_DIR
    $priorUvPath = $env:UV_NO_MODIFY_PATH
    try {
        $env:UV_INSTALL_DIR = $uvBin
        $env:UV_NO_MODIFY_PATH = '1'
        if (-not (Test-Path -LiteralPath (Join-Path $uvBin 'uv.exe'))) {
            $script = Join-Path $temporary 'uv.ps1'
            Save-MiosVerifiedInstaller $cfg.windows_uv_installer $script $cfg.windows_uv_installer_sha256
            & $script
        }
    } finally {
        $env:UV_INSTALL_DIR = $priorUvInstall
        $env:UV_NO_MODIFY_PATH = $priorUvPath
    }
    $uv = Join-Path $uvBin 'uv.exe'
    $priorUv = @{}
    foreach ($name in @('UV_TOOL_DIR','UV_TOOL_BIN_DIR','UV_PYTHON_INSTALL_DIR','UV_CACHE_DIR')) {
        $priorUv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:UV_TOOL_DIR = Join-Path $directory 'tools'
        $env:UV_TOOL_BIN_DIR = Join-Path $directory 'bin'
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $directory 'python'
        $env:UV_CACHE_DIR = Join-Path $directory 'cache'
        & $uv tool install --python $cfg.python.Replace('python','') $cfg.aider_package
        if ($LASTEXITCODE -ne 0) { throw 'Global Aider installation failed' }
    } finally {
        foreach ($name in $priorUv.Keys) { [Environment]::SetEnvironmentVariable($name, $priorUv[$name], 'Process') }
    }
} finally {
    # Only this resolved, explicitly created staging directory may be deleted.
    $resolved = [IO.Path]::GetFullPath($temporary)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected installer staging path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
$paths = @($nodeDir, $npmRoot, $native, (Join-Path $directory 'bin'))
$machine = [Environment]::GetEnvironmentVariable('PATH','Machine')
$remaining = @($machine -split ';' | Where-Object { $_ -and $_ -notin $paths })
[Environment]::SetEnvironmentVariable('PATH', (($paths + $remaining) -join ';'), 'Machine')
$env:PATH = ($paths -join ';') + ';' + $env:PATH
# DrvFS metadata may give only the mounting UID execute permission. These are
# MiOS-owned launchers; the unprivileged native tmux bridge must execute them too.
foreach ($folder in @($native, (Join-Path $directory 'bin'))) {
    foreach ($exe in Get-ChildItem -LiteralPath $folder -Filter '*.exe' -File) {
        $linuxPath = & wsl.exe -d $Distro -u root -- wslpath -a -u $exe.FullName.Replace('\','/')
        if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the global launcher path for MiOS tmux' }
        & wsl.exe -d $Distro -u root -- chmod a+rx ($linuxPath -join '')
        if ($LASTEXITCODE -ne 0) { throw 'Cannot grant execution of the MiOS-owned global launcher' }
    }
}
foreach ($row in $cfg.tools) {
    $command = Get-Command ($row.name + $(if ($row.kind -eq 'npm') { '.cmd' } else { '.exe' })) -ErrorAction Stop
    $version = & $command.Source --version
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI $($row.name) failed its version probe" }
    Write-Host "$($row.name): $version"
}
Write-Host ('Installed global Windows agent CLIs: ' + ($names -join ', '))
