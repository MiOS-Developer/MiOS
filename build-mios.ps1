# AI-hint: PowerShell entry point for MiOS installation that configures the MiOS-DEV podman-machine, handles initial licensing, and manages the SSH handoff to the Linux-side build driver ...
# AI-doc: usr/share/doc/mios/manual/root.md

param(
    [switch]$BootstrapOnly,
    [switch]$BuildOnly,
    [string]$SourceRoot = '',
    [string]$BuildDistro = '',
    [switch]$FullBuild,
    [switch]$DeployPipeline,

    # -Unattended: take all defaults; no interactive prompts.
    [switch]$Unattended,

    # -ImportWsl: fast-path WSL import without full bootstrap build.
    [switch]$ImportWsl,
    [string]$WslArchive = '',
    [string]$WslInstallDir = '',
    [parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Passthrough = @()
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

function Resolve-MiosBuilderDistribution([string]$Machine) {
    $registered = @(& wsl.exe --list --quiet 2>$null) |
        ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ }
    foreach ($candidate in @($Machine, "podman-$Machine")) {
        if ($registered -contains $candidate) { return $candidate }
    }
    throw "No registered WSL distribution for MiOS builder '$Machine'; existing machines are preserved."
}

function Install-MiosNativeCatalog([string]$Root, [string]$Machine) {
    $Root = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path
    foreach ($required in @('usr\share\mios\mios.toml','Containerfile','.devcontainer\Containerfile','automation\55-native-build.sh')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $required) -PathType Leaf)) {
            throw "Current-source build input missing: $required"
        }
    }
    $distribution = Resolve-MiosBuilderDistribution $Machine
    $linuxRoot = (& wsl.exe -d $distribution -u root -- wslpath -a -u $Root.Replace('\','/')) -join ''
    if ($LASTEXITCODE -ne 0 -or -not $linuxRoot) { throw 'Current source is not accessible in the MiOS builder' }
    # Bootstrap only the native management executable, then let its SSOT engine
    # build/lint/install the catalog. Use persistent builder output, not the source.
    & wsl.exe -d $distribution -u root -- env CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin CARGO_TARGET_DIR=/var/tmp/mios-native-build MIOS_NATIVE_INSTALL_ROOT=/ bash "$linuxRoot/automation/55-native-build.sh" | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Native SSOT build/install failed (exit $LASTEXITCODE)" }
    # Publish the vendor layer atomically; /etc and user SSOT layers persist.
    $pendingVendor = '/usr/share/mios/mios.toml.pending-' + [guid]::NewGuid().ToString('N')
    & wsl.exe -d $distribution -u root -- install -D -m 0644 "$linuxRoot/usr/share/mios/mios.toml" $pendingVendor
    if ($LASTEXITCODE -ne 0) { throw 'Current-source vendor SSOT staging failed' }
    & wsl.exe -d $distribution -u root -- mv -f -- $pendingVendor /usr/share/mios/mios.toml
    if ($LASTEXITCODE -ne 0) { throw 'Current-source vendor SSOT publication failed' }
    return @{ Distribution = $distribution; LinuxRoot = $linuxRoot }
}

function Invoke-MiosNativeImageBuild([string]$Root, [string]$Machine) {
    $catalog = Install-MiosNativeCatalog $Root $Machine
    & wsl.exe -d $catalog.Distribution -u root -- /usr/bin/miosd image-build --root $catalog.LinuxRoot --target all | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}

function Install-MiosNativeWindowsArtifact([string]$Root, [string]$Machine, [string]$Binary, [string]$Destination, [string]$ServiceName = '') {
    $distribution = Resolve-MiosBuilderDistribution $Machine
    $linuxRoot = (& wsl.exe -d $distribution -u root -- wslpath -a -u $Root.Replace('\','/')) -join ''
    if ($LASTEXITCODE -ne 0 -or -not $linuxRoot) { throw 'Windows artifact source is not accessible in MiOS-DEV' }
    $policyJson = & wsl.exe -d $distribution -u root -- env "MIOS_TOML_ROOT=$linuxRoot" /usr/bin/mios-toml-get --section build.native.windows
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve SSOT Windows artifact policy' }
    $policy = ($policyJson -join "`n") | ConvertFrom-Json -AsHashtable
    if ($policy.target -notin @('x86_64-pc-windows-gnu','x86_64-pc-windows-msvc')) { throw 'Invalid SSOT Windows artifact target' }
    $buildEnv = @('CARGO_HOME=/usr/local/cargo','RUSTUP_HOME=/usr/local/rustup','PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    & wsl.exe -d $distribution -u root -- env @buildEnv /usr/bin/miosd native-windows-build --root $linuxRoot --binary $Binary --target-dir /var/tmp/mios-native-build | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "SSOT Windows lint/build/static-artifact verification failed: $Binary" }
    $built = Join-Path $Root "tools\native\target\$($policy.target)\release\$Binary.exe"
    if (-not (Test-Path -LiteralPath $built -PathType Leaf)) { throw "Verified Windows artifact was not staged: $Binary" }
    $pending = $Destination + '.pending-' + [guid]::NewGuid().ToString('N')
    Copy-Item -LiteralPath $built -Destination $pending -ErrorAction Stop
    $linuxPending = (& wsl.exe -d $distribution -u root -- wslpath -a -u $pending.Replace('\','/')) -join ''
    if ($LASTEXITCODE -ne 0 -or -not $linuxPending) { throw 'Windows staged artifact is not accessible in MiOS-DEV' }
    & wsl.exe -d $distribution -u root -- /usr/bin/miosd native-artifact-check $linuxPending --platform windows --root $linuxRoot | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "Staged Windows artifact failed the SSOT dependency gate: $Binary" }
    $restart = $false
    if ($ServiceName) {
        $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($service -and $service.Status -eq 'Running') { Stop-Service -Name $ServiceName -ErrorAction Stop; $restart = $true }
    }
    try { [IO.File]::Move($pending, $Destination, $true) }
    finally { if ($restart) { Start-Service -Name $ServiceName -ErrorAction Stop } }
    return $Destination
}

function Ensure-MiosBuilder([string]$Machine, [hashtable]$Hardware) {
    $registered = @(& wsl.exe --list --quiet 2>$null) |
        ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ }
    foreach ($candidate in @($Machine, "podman-$Machine")) {
        if ($registered -contains $candidate) {
            & wsl.exe -d $candidate -u root -- /bin/true | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Existing builder '$candidate' is not responsive; its persistent state is preserved." }
            return $candidate
        }
    }
    $machines = @(& podman machine list --format '{{.Name}}' 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Podman machines; refusing to recreate an unknown builder.' }
    if ($machines -contains $Machine) {
        & podman machine start $Machine | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "Cannot start existing machine '$Machine'; its persistent state is preserved." }
    } else {
        New-BuilderDistro -HW $Hardware
    }
    return Resolve-MiosBuilderDistribution $Machine
}

# Build the current checkout without entering the provisioning/reset phases.
# The native engine reads layered SSOT, builds both declared images and verifies
# their runtime commands. It never fetches, resets, overlays or reaps the source.
if ($BuildOnly) {
    if (-not $SourceRoot) { $SourceRoot = $PSScriptRoot }
    $SourceRoot = (Resolve-Path -LiteralPath $SourceRoot -ErrorAction Stop).Path
    foreach ($required in @('usr\share\mios\mios.toml','Containerfile','.devcontainer\Containerfile')) {
        if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot $required) -PathType Leaf)) { throw "Current-source build input missing: $required" }
    }
    if (-not $BuildDistro) {
        $binding = Get-Content -Raw -LiteralPath (Join-Path $env:ProgramData 'MiOS\bin\native-binding.json') -ErrorAction Stop | ConvertFrom-Json
        $BuildDistro = $binding.distro
    }
    if (-not $BuildDistro) { throw 'A provisioned MiOS builder distribution is required' }
    $nativeExit = Invoke-MiosNativeImageBuild -Root $SourceRoot -Machine $BuildDistro
    exit $nativeExit
}

function Disable-ConsoleQuickEdit {
    try {
        if (-not ('MiosConsole.Win32' -as [type])) {
            Add-Type -Namespace MiosConsole -Name Win32 -MemberDefinition '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern System.IntPtr GetStdHandle(int nStdHandle); [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleMode(System.IntPtr hConsoleHandle, out uint lpMode); [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleMode(System.IntPtr hConsoleHandle, uint dwMode);' -ErrorAction Stop
        }
        $h = [MiosConsole.Win32]::GetStdHandle(-10)   # STD_INPUT_HANDLE
        [uint32]$mode = 0
        if ([MiosConsole.Win32]::GetConsoleMode($h, [ref]$mode)) {
            $mode = ($mode -band (-bnot [uint32]0x40)) -bor [uint32]0x80
            [void][MiosConsole.Win32]::SetConsoleMode($h, $mode)
        }
    } catch {}
}
Disable-ConsoleQuickEdit

# Import MiOS.Build sub-modules
$buildModuleDir = Join-Path $PSScriptRoot 'automation\lib\MiOS.Build'
if (Test-Path $buildModuleDir) {
    Get-ChildItem -Path $buildModuleDir -Filter '*.psm1' -ErrorAction SilentlyContinue | ForEach-Object {
        Import-Module $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

$script:_MiosTomlCache = @{}
function Resolve-MiosTomlLayers {
    if ($script:_MiosTomlCache.ContainsKey('_layers')) { return $script:_MiosTomlCache['_layers'] }
    $layers = @()
    foreach ($p in @(
        (Join-Path $env:USERPROFILE '.config\mios\mios.toml'),
        (Join-Path $env:APPDATA 'MiOS\mios.toml'),
        'M:\etc\mios\mios.toml',
        'M:\usr\share\mios\mios.toml'
        # C:\MiOS deliberately excluded -- dev working tree, not a consumer install path
    )) {
        if ($p -and (Test-Path -LiteralPath $p)) {
            try {
                $layers += [pscustomobject]@{ Path = $p; Text = [IO.File]::ReadAllText($p, (New-Object System.Text.UTF8Encoding($false))) }
            } catch {
                try {
                    $layers += [pscustomobject]@{ Path = $p; Text = Get-Content -LiteralPath $p -Raw -Encoding UTF8 -ErrorAction Stop }
                } catch {}
            }
        }
    }
    if (-not (Test-Path -LiteralPath 'M:\usr\share\mios\mios.toml')) {
        try {
            $cb  = [int][double]::Parse((Get-Date -UFormat %s))
            $ref = if ($null -ne $MiosRef) { $MiosRef } else { 'main' }
            $url = "https://raw.githubusercontent.com/mios-dev/MiOS/$ref/usr/share/mios/mios.toml?cb=$cb"
            $layers += [pscustomobject]@{ Path = $url; Text = (Invoke-RestMethod -Uri $url `
                -Headers @{ 'Cache-Control'='no-cache, no-store, max-age=0'; 'Pragma'='no-cache' } `
                -ErrorAction Stop) }
        } catch {}
    }
    $script:_MiosTomlCache['_layers'] = $layers
    return $layers
}
function Resolve-MiosTomlText {
    $layers = @(Resolve-MiosTomlLayers)
    if ($layers.Count -gt 0) { return $layers[0].Text }
    return ''
}
function Get-MiosTomlValue {
    param([Parameter(Mandatory)][string]$Section, [Parameter(Mandatory)][string]$Key, [Parameter(Mandatory)][AllowEmptyString()]$Default,
          [string]$SourcePath = '')
    foreach ($layer in @(Resolve-MiosTomlLayers)) {
        if ($SourcePath -and $layer.Path -ne $SourcePath) { continue }
        $txt = $layer.Text
        if (-not $txt) { continue }
        $rxSec = '(?ms)^\[' + [regex]::Escape($Section) + '\][ \t]*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)'
        $mSec  = [regex]::Match($txt, $rxSec)
        if (-not $mSec.Success) { continue }
        $rxKey = '(?m)^[ \t]*' + [regex]::Escape($Key) + '[ \t]*=[ \t]*(?<val>.+?)[ \t]*(?:#.*)?$'
        $mKey  = [regex]::Match($mSec.Groups['body'].Value, $rxKey)
        if (-not $mKey.Success) { continue }
        $raw = $mKey.Groups['val'].Value.Trim()
    if ($Default -is [int]) {
        $n = 0; if ([int]::TryParse(($raw -replace '_',''), [ref]$n)) { return $n }
        continue
    }
    if ($Default -is [bool]) {
        if ($raw -match '^(?i)true$')  { return $true }
        if ($raw -match '^(?i)false$') { return $false }
        continue
    }
    if ($Default -is [array]) {
        if ($raw -match '^\[(.*)\]$') {
            $items = @($Matches[1] -split ',' | ForEach-Object {
                $s = $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n")
                if ($s) { $s }
            })
            if ($Default.Length -gt 0 -and $Default[0] -is [int]) {
                $coerced = @()
                $valid = $true
                foreach ($it in $items) {
                    $n = 0
                    if ([int]::TryParse($it, [ref]$n)) { $coerced += $n } else { $valid = $false; break }
                }
                if ($valid) { return $coerced }
                continue
            }
            return $items
        }
        continue
    }
    if ($raw.Length -ge 2) {
        $first = $raw[0]; $last = $raw[$raw.Length - 1]
        if ($first -eq '"' -and $last -eq '"') {
            # PS 5.1-safe sentinel ([char]0x01); `` `u{0001} `` is PS 7-only.
            $_bs = [string][char]0x01 + 'BS' + [string][char]0x01
            $inner = $raw.Substring(1, $raw.Length - 2)
            $inner = $inner -replace '\\\\', $_bs
            $inner = $inner -replace '\\"', '"'
            $inner = $inner -replace '\\n', "`n"
            $inner = $inner -replace '\\t', "`t"
            $inner = $inner -replace '\\r', "`r"
            $inner = $inner -replace [regex]::Escape($_bs), '\'
            if (-not [string]::IsNullOrWhiteSpace($inner)) { return $inner }
            continue
        }
        if ($first -eq "'" -and $last -eq "'") {
            $inner = $raw.Substring(1, $raw.Length - 2)
            if (-not [string]::IsNullOrWhiteSpace($inner)) { return $inner }
            continue
        }
    }
        if (-not [string]::IsNullOrWhiteSpace($raw)) { return $raw }
    }
    return $Default
}

function Resolve-MiosLoginCredential {
    # Evaluate related auth keys within each layer before moving to the next.
    # A host/user password must outrank a vendor password even when the keys differ.
    foreach ($layer in @(Resolve-MiosTomlLayers)) {
        $hash = [string](Get-MiosTomlValue -Section 'auth' -Key 'password_hash' -Default '' -SourcePath $layer.Path)
        if ($hash) { return @{ Hash = $hash; Plain = '' } }
        $plain = [string](Get-MiosTomlValue -Section 'auth' -Key 'password' -Default '' -SourcePath $layer.Path)
        if ($plain) { return @{ Hash = ''; Plain = $plain } }
        $plain = [string](Get-MiosTomlValue -Section 'identity' -Key 'default_password' -Default '' -SourcePath $layer.Path)
        if ($plain) { return @{ Hash = ''; Plain = $plain } }
    }
    return @{ Hash = ''; Plain = 'mios' }
}

$script:MiosInstCols = Get-MiosTomlValue -Section 'terminal.install' -Key 'cols' -Default 80
$script:MiosInstRows = Get-MiosTomlValue -Section 'terminal.install' -Key 'rows' -Default 40
$script:MiosCols    = $script:MiosInstCols
$script:MiosRows    = $script:MiosInstRows
$script:MiosScroll  = Get-MiosTomlValue -Section 'terminal' -Key 'scrollback_rows' -Default 9000
$script:MiosAppCols = Get-MiosTomlValue -Section 'terminal' -Key 'cols' -Default 80
$script:MiosAppRows = Get-MiosTomlValue -Section 'terminal' -Key 'rows' -Default 20

$_resizeBefore = try { "$([Console]::WindowWidth)x$([Console]::WindowHeight) buf=$([Console]::BufferWidth)x$([Console]::BufferHeight)" } catch { 'unknown' }
$_resizeAfter  = 'unchanged'
$_resizeErr    = $null
try {
    $_curW = [Console]::WindowWidth
    if ($_curW -gt $script:MiosCols) {
        # Shrink window first (buffer can't be < window), then buffer.
        [Console]::SetWindowSize($script:MiosCols, $script:MiosRows)
        [Console]::SetBufferSize($script:MiosCols, $script:MiosScroll)
    } else {
        # Enlarge buffer first (window can't be > buffer), then window.
        [Console]::SetBufferSize($script:MiosCols, $script:MiosScroll)
        [Console]::SetWindowSize($script:MiosCols, $script:MiosRows)
    }
    $_resizeAfter = "$([Console]::WindowWidth)x$([Console]::WindowHeight) buf=$([Console]::BufferWidth)x$([Console]::BufferHeight)"
} catch {
    $_resizeErr = $_.Exception.Message
}
# Log to a deferred-flush variable; written to the unified log once the log
# file path is known (Write-Log isn't defined this early in load).
$script:_PendingResizeLog = "console resize: before=$_resizeBefore after=$_resizeAfter err=$_resizeErr"

$script:_PendingResizeLog += " center-skip=amsi-bait-removed"

# $BootstrapOnly is driven by the script parameter [switch]$BootstrapOnly (default $false).
# Explicit switches (-FullBuild, -DeployPipeline, -BuildOnly) guarantee $BootstrapOnly is $false.
if ($FullBuild -or $DeployPipeline -or $BuildOnly) {
    $BootstrapOnly = $false
}
$script:BootstrapOnly = [bool]$BootstrapOnly

# Acknowledgment banner. Inlined (script is irm-piped). Respects
# $env:MIOS_AGREEMENT_BANNER=quiet for unattended runs.
if ($env:MIOS_AGREEMENT_BANNER -notin @('quiet','silent','off','0','false','FALSE')) {
    [Console]::Error.WriteLine(@"
[mios] By invoking build-mios.ps1 you acknowledge AGREEMENTS.md
       (Apache-2.0 main + bundled-component licenses in LICENSES.md +
        attribution in usr/share/doc/mios/reference/credits.md). 'MiOS' is a research project
       (pronounced 'MyOS'; generative, seed-script-derived).
"@)
}

$script:IsAdmin = $false
try {
    $script:IsAdmin = ([Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { $script:IsAdmin = $false }

$MiosScope = if ($script:IsAdmin) { "AllUsers" } else { "CurrentUser" }

$MiosRepoUrl    = Get-MiosTomlValue -Section 'bootstrap' -Key 'mios_repo'       -Default 'https://github.com/mios-dev/MiOS.git'
$MiosBootstrapUrl = Get-MiosTomlValue -Section 'bootstrap' -Key 'bootstrap_repo' -Default 'https://github.com/mios-dev/mios-bootstrap.git'
$MiosRef          = Get-MiosTomlValue -Section 'bootstrap' -Key 'mios_ref'       -Default 'main'
$MiosBootstrapRef = Get-MiosTomlValue -Section 'bootstrap' -Key 'bootstrap_ref' -Default 'main'
# Raw-content tree bases + registry owner DERIVED from the repo URLs above so
# every download/login site sources the owner/name from one SSOT place
# (github.com host -> raw.githubusercontent.com, drop .git, append the ref).
$MiosRawBase      = (($MiosRepoUrl      -replace '^https://github\.com/', 'https://raw.githubusercontent.com/' -replace '\.git$', '') + "/$MiosRef")
$MiosBootstrapRaw = (($MiosBootstrapUrl -replace '^https://github\.com/', 'https://raw.githubusercontent.com/' -replace '\.git$', '') + "/$MiosBootstrapRef")
$MiosRepoOwner    = (($MiosRepoUrl -replace '^https://github\.com/', '') -split '/')[0]   # ghcr.io / GitHub owner namespace
# Release number: VERSION beside this script, else $MiosRawBase/VERSION (irm | iex
# has no PSScriptRoot), else [meta].mios_version, else 'unknown'. Never a literal.
$_v = ''
foreach ($_vroot in @($PSScriptRoot, $MiosRawBase)) {
    if ($_v -or -not $_vroot) { continue }
    try { $_v = if ($_vroot -match '^[A-Za-z]:|^[\\/]') { [IO.File]::ReadAllText((Join-Path $_vroot 'VERSION')) } else { [string](Invoke-WebRequest -Uri "$_vroot/VERSION" -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop).Content } } catch { $_v = '' }
    $_v = ([string]$_v).Trim(); if ($_v -notmatch '^v?\d+(\.\d+)+') { $_v = '' }
}
if (-not $_v) { $_v = Get-MiosTomlValue -Section 'meta' -Key 'mios_version' -Default '' }
if (-not $_v) { $_v = 'unknown' }
$MiosVersion    = if ($_v -match '^v') { $_v } else { "v$_v" }
# Podman machine name. Backed by WSL distro `podman-MiOS-DEV` once `podman
# machine init` runs. Locked per memory feedback_mios_distro_name_locked.md
# (renaming breaks podman's distro discovery), so the TOML key carries
# vendor default 'MiOS-DEV' and operators rarely override.
$DevDistro      = Get-MiosTomlValue -Section 'bootstrap' -Key 'dev_distro'     -Default 'MiOS-DEV'
$BuilderDistro    = $DevDistro
$LegacyDevName    = "MiOS-BUILDER"
$MiosWslDistro    = "MiOS"
$LegacyDistro     = "podman-machine-default"
$MachineImage = $env:MIOS_MACHINE_IMAGE
if ($MachineImage -and $MachineImage -notmatch '^(docker|https?|file)://' -and $MachineImage -match '^[a-z0-9.-]+\.[a-z]{2,}/') {
    # Operator passed a bare OCI ref via env -- auto-prefix `docker://`.
    $MachineImage = "docker://$MachineImage"
}

if ($script:IsAdmin) {
    # AllUsers (machine-wide native Windows app layout). Top-level
    # C:\MiOS as requested -- treats MiOS as a first-class Windows
    # application rather than a hidden Program Files entry.
    $MiosInstallDir   = Join-Path ${env:SystemDrive} "MiOS"             # C:\MiOS
    $MiosProgramData  = Join-Path ${env:ProgramData}  "MiOS"            # C:\ProgramData\MiOS
    $MiosRepoDir      = Join-Path $MiosInstallDir   "repo"              # boot-time default; Update-MiosInstallPaths swaps to M:\ on data disk
    $MiosBootstrapShadow = Join-Path $MiosRepoDir 'mios-bootstrap'      # boot-time default; data-disk variant goes to M:\MiOS\bootstrap-shadow
    $MiosBinDir       = Join-Path $MiosInstallDir   "bin"               # entry-point scripts + oh-my-posh
    $MiosShareDir     = Join-Path $MiosInstallDir   "share"             # mios-bootstrap etc/usr trees
    $MiosIconsDir     = Join-Path $MiosInstallDir   "icons"             # per-verb .ico files
    $MiosThemesDir    = Join-Path $MiosInstallDir   "themes"            # mios.omp.json + future themes
    $MiosFontsDir     = Join-Path $MiosInstallDir   "fonts"             # local copy of installed fonts
    $MiosDistroDir    = Join-Path $MiosProgramData  "distros"           # multi-GB WSL2 artifacts
    $MiosImagesDir    = Join-Path $MiosProgramData  "images"            # qcow2 / vhdx / iso outputs
    $MiosMachineCfg   = Join-Path $MiosProgramData  "config"            # global non-secret install.env
    $StartMenuDir     = Join-Path ${env:ProgramData} "Microsoft\Windows\Start Menu\Programs\MiOS"
    $UninstallRegKey  = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MiOS"
} else {
    # CurrentUser fallback (no write access to C:\). Mirrors the admin
    # layout under %LOCALAPPDATA%\MiOS so paths inside the install
    # root stay relative-stable (bin/, icons/, themes/, ...).
    $MiosInstallDir   = Join-Path ${env:LOCALAPPDATA} "MiOS"
    $MiosProgramData  = Join-Path $MiosInstallDir    "machine-state"
    $MiosRepoDir      = Join-Path $MiosInstallDir    "repo"
    $MiosBootstrapShadow = Join-Path $MiosRepoDir    'mios-bootstrap'
    $MiosBinDir       = Join-Path $MiosInstallDir    "bin"
    $MiosShareDir     = Join-Path $MiosInstallDir    "share"
    $MiosIconsDir     = Join-Path $MiosInstallDir    "icons"
    $MiosThemesDir    = Join-Path $MiosInstallDir    "themes"
    $MiosFontsDir     = Join-Path $MiosInstallDir    "fonts"
    $MiosDistroDir    = Join-Path $MiosInstallDir    "distros"
    $MiosImagesDir    = Join-Path $MiosInstallDir    "images"
    $MiosMachineCfg   = Join-Path $MiosInstallDir    "config"
    $StartMenuDir     = Join-Path ${env:APPDATA}     "Microsoft\Windows\Start Menu\Programs\MiOS"
    $UninstallRegKey  = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MiOS"
}

$script:MiosInstallDir     = $MiosInstallDir
$script:MiosProgramData    = $MiosProgramData
$script:MiosRepoDir        = $MiosRepoDir
$script:MiosBootstrapShadow = $MiosBootstrapShadow
$script:MiosBinDir         = $MiosBinDir
$script:MiosShareDir       = $MiosShareDir
$script:MiosIconsDir       = $MiosIconsDir
$script:MiosThemesDir      = $MiosThemesDir
$script:MiosFontsDir       = $MiosFontsDir
$script:MiosDistroDir      = $MiosDistroDir
$script:MiosImagesDir      = $MiosImagesDir
$script:MiosMachineCfg     = $MiosMachineCfg

$_miosDataLetter = if ($env:MIOS_DATA_DISK_LETTER) { $env:MIOS_DATA_DISK_LETTER } else { 'M' }
try {
    $_miosVol = Get-Volume -DriveLetter $_miosDataLetter -ErrorAction SilentlyContinue
    if ($_miosVol -and $_miosVol.FileSystemLabel -eq 'MIOS-DEV') {
        $_miosNewRoot = Join-Path "${_miosDataLetter}:\" 'MiOS'
        if (-not (Test-Path $_miosNewRoot)) { New-Item -ItemType Directory -Path $_miosNewRoot -Force | Out-Null }
        # Update-MiosInstallPaths is defined below; do an inline
        # equivalent here so the redirect lands BEFORE Phase 0 runs.
        $script:MiosInstallDir      = $_miosNewRoot
        $script:MiosBinDir          = Join-Path $_miosNewRoot 'bin'
        $script:MiosShareDir        = Join-Path $_miosNewRoot 'share'
        $script:MiosIconsDir        = Join-Path $_miosNewRoot 'icons'
        $script:MiosThemesDir       = Join-Path $_miosNewRoot 'themes'
        $script:MiosFontsDir        = Join-Path $_miosNewRoot 'fonts'
        $script:MiosProgramData     = Join-Path $_miosNewRoot 'machine-state'
        $script:MiosDistroDir       = Join-Path $script:MiosProgramData 'distros'
        $script:MiosImagesDir       = Join-Path $script:MiosProgramData 'images'
        $script:MiosMachineCfg      = Join-Path $script:MiosProgramData 'config'
        # M:\ root IS the mios.git working tree per the
        # directive ("M:\ IS git"). Repo lives at the drive root,
        # mios-bootstrap shadow at M:\MiOS\bootstrap-shadow.
        $script:MiosRepoDir         = "${_miosDataLetter}:\"
        $script:MiosBootstrapShadow = Join-Path $_miosNewRoot 'bootstrap-shadow'
        # Mirror the locals so any later code that still reads $MiosInstallDir
        # (without $script: prefix) gets the same redirect.
        $MiosInstallDir      = $script:MiosInstallDir
        $MiosBinDir          = $script:MiosBinDir
        $MiosShareDir        = $script:MiosShareDir
        $MiosIconsDir        = $script:MiosIconsDir
        $MiosThemesDir       = $script:MiosThemesDir
        $MiosFontsDir        = $script:MiosFontsDir
        $MiosProgramData     = $script:MiosProgramData
        $MiosDistroDir       = $script:MiosDistroDir
        $MiosImagesDir       = $script:MiosImagesDir
        $MiosMachineCfg      = $script:MiosMachineCfg
        $MiosRepoDir         = $script:MiosRepoDir
        $MiosBootstrapShadow = $script:MiosBootstrapShadow
        Write-Host "  [+] M:\ MIOS-DEV partition detected -- ALL install paths redirected to M:\" -ForegroundColor Green
    }
} catch {}

$MiosDataDir      = if ($script:MiosInstallDir) { $script:MiosInstallDir } else { 'M:\MiOS' }
$MiosLogDir       = Join-Path $MiosDataDir 'logs'
$MiosConfigDir    = Join-Path $MiosDataDir 'config'   # was %APPDATA%\MiOS
# Make the log dir bulletproof: ensure it exists the moment it's derived
# (well before $LogFile is first written by [IO.File]::AppendAllText), so a
# non-admin run never trips over a missing M:\ root.
$null = New-Item -ItemType Directory -Path $MiosLogDir -Force -ErrorAction SilentlyContinue

function Resolve-MiosInstallRoot {
    param([string]$Default = $script:MiosInstallDir)
    $letter = if ($env:MIOS_DATA_DISK_LETTER) { $env:MIOS_DATA_DISK_LETTER } else { 'M' }
    $vol = Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue
    if ($vol -and $vol.FileSystemLabel -eq 'MIOS-DEV') {
        return Join-Path "${letter}:\" 'MiOS'
    }
    return $Default
}

function Update-MiosInstallPaths {
    param([Parameter(Mandatory)] [string] $NewRoot)
    $script:MiosInstallDir  = $NewRoot
    $script:MiosBinDir      = Join-Path $NewRoot 'bin'
    $script:MiosShareDir    = Join-Path $NewRoot 'share'
    $script:MiosIconsDir    = Join-Path $NewRoot 'icons'
    $script:MiosThemesDir   = Join-Path $NewRoot 'themes'
    $script:MiosFontsDir    = Join-Path $NewRoot 'fonts'
    # State + artifacts also move onto the data disk.
    $script:MiosProgramData = Join-Path $NewRoot 'machine-state'
    # MiosRepoDir = data-disk root (M:\) when we're running on the
    # MIOS-DEV partition; legacy NewRoot\repo otherwise. Both repos
    # overlay to this path; mios.git's .git lives here, mios-bootstrap.git's
    # .git lives in $MiosBootstrapShadow.
    $_qualifier = (Split-Path $NewRoot -Qualifier)         # 'M:'
    $_drive     = if ($_qualifier) { "$_qualifier\" } else { $null }   # 'M:\'
    $_onDataDisk = $false
    if ($_drive) {
        try {
            $_vol = Get-Volume -DriveLetter $_qualifier.TrimEnd(':') -ErrorAction SilentlyContinue
            if ($_vol -and $_vol.FileSystemLabel -eq 'MIOS-DEV') {
                $_onDataDisk = $true
            }
        } catch {}
    }
    if ($_onDataDisk) {
        $script:MiosRepoDir         = $_drive                                  # 'M:\'
        $script:MiosBootstrapShadow = Join-Path $NewRoot 'bootstrap-shadow'    # 'M:\MiOS\bootstrap-shadow'
    } else {
        $script:MiosRepoDir         = Join-Path $NewRoot 'repo'                # legacy fallback
        $script:MiosBootstrapShadow = Join-Path $script:MiosRepoDir 'mios-bootstrap'
    }
    $script:MiosDistroDir   = Join-Path $NewRoot 'distros'
    $script:MiosImagesDir   = Join-Path $NewRoot 'images'
    $script:MiosMachineCfg  = Join-Path $NewRoot 'config'
    $script:MiosLogDir      = Join-Path $NewRoot 'logs'
    # NOTE: $LogFile (the unified install log opened at script init)
    # stays on its boot-time path because file handles are already
    # open. Long-term logs from CLI verbs (mios-pull, mios-update,
    # etc.) write to the redirected $MiosLogDir.
}

function Invoke-MigrateLegacyInstallRoot {
    param([string]$LegacyRoot)
    if (-not $LegacyRoot) { return }
    if ($LegacyRoot -ieq $script:MiosInstallDir) { return }
    if (-not (Test-Path $LegacyRoot)) { return }

    # Default no-op. The MIOS_SKIP_LEGACY_MIGRATE alias remains
    # recognized for backward compat; it's now redundant.
    if ($env:MIOS_FORCE_LEGACY_MIGRATE -notin @('1','true','TRUE','yes')) {
        Log-Ok "Legacy migration is no-op by default. C:\\MiOS (dev working tree) and M:\\MiOS\\ (bootstrap install root) coexist; neither overwrites the other. Set MIOS_FORCE_LEGACY_MIGRATE=1 only for explicit cleanup of stale plain-dir leftovers."
        return
    }

    # -- Force path: explicit operator opt-in for cleanup of stale dirs --
    # Refuses to operate on git working trees -- those are sacrosanct.
    if (Test-Path (Join-Path $LegacyRoot '.git')) {
        Log-Warn "$LegacyRoot is a git working tree. Migration refuses to /MOVE git working trees (use a manual git remote workflow instead). Aborting even with MIOS_FORCE_LEGACY_MIGRATE=1."
        return
    }

    Log-Warn "MIOS_FORCE_LEGACY_MIGRATE=1 -- proceeding with destructive robocopy /MOVE from $LegacyRoot to $($script:MiosInstallDir) (non-git leftover dirs only)"
    Set-Step "Migrating legacy install $LegacyRoot -> $($script:MiosInstallDir) ..."
    $InstallDir = $script:MiosInstallDir
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    $rcArgs = @(
        $LegacyRoot, $InstallDir,
        '/MOVE',           # delete source files after copy
        '/E',              # include all subdirs incl. empty
        '/XO', '/XN', '/XC', # skip if dest exists (older / newer / same-size-different)
        '/NFL', '/NDL', '/NJH', '/NJS',  # quiet output
        '/R:1', '/W:1'     # 1 retry, 1s wait
    )
    & robocopy.exe @rcArgs 2>&1 | ForEach-Object { Write-Log "migrate: $_" }
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        Log-Warn "robocopy returned $rc on legacy migration (>= 8 = real error). Some files may remain at $LegacyRoot."
    }

    if (Test-Path $LegacyRoot) {
        $remaining = @(Get-ChildItem -Path $LegacyRoot -Recurse -Force -File -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            try {
                Remove-Item $LegacyRoot -Recurse -Force -ErrorAction SilentlyContinue
                Log-Ok "Migrated and removed legacy install root: $LegacyRoot"
            } catch {
                Log-Warn "Could not remove now-empty $LegacyRoot : $_"
            }
        } else {
            Log-Warn "Migration kept $($remaining.Count) file(s) at $LegacyRoot (already-present at destination); review manually."
        }
    } else {
        Log-Ok "Legacy install root $LegacyRoot fully migrated"
    }
}


function Test-DashboardCanRedraw {
    try {
        if ([Console]::IsOutputRedirected) { return $false }
        $origTop  = [Console]::CursorTop
        $origLeft = [Console]::CursorLeft
        # Move to col 0 of the SAME row -- a no-op if positioning works,
        # detectable as a failure if it doesn't.
        [Console]::SetCursorPosition(0, $origTop)
        $afterLeft = [Console]::CursorLeft
        # Restore.
        [Console]::SetCursorPosition($origLeft, $origTop)
        return ($afterLeft -eq 0)
    } catch { return $false }
}

function Try-ResizeConsole {
    # Best-effort: set the host window to ~100x40 (slightly larger than
    # the 80-col dashboard frame so there's breathing room for log
    # spillover). Silently skipped if the host doesn't allow resize
    # (e.g. embedded terminals, SSH sessions, fixed-size kiosks).
    param([int]$Cols = 100, [int]$Rows = 40)
    try {
        $sz  = New-Object Management.Automation.Host.Size $Cols, $Rows
        $buf = New-Object Management.Automation.Host.Size $Cols, 3000
        # BufferSize must be >= WindowSize on both axes; set buf first.
        $Host.UI.RawUI.BufferSize = $buf
        $Host.UI.RawUI.WindowSize = $sz
    } catch {
        # Some hosts throw "WindowSize cannot exceed BufferSize" if
        # buffer wasn't accepted. Try the inverse order as a fallback.
        try {
            $Host.UI.RawUI.WindowSize = New-Object Management.Automation.Host.Size $Cols, $Rows
        } catch {}
    }
}

$null = New-Item -ItemType Directory -Path $MiosLogDir -Force -ErrorAction SilentlyContinue
$LogStamp       = [datetime]::Now.ToString("yyyyMMdd-HHmmss")
$LogFile        = Join-Path $MiosLogDir "mios-install-$LogStamp.log"
$BuildDetailLog = Join-Path $MiosLogDir "mios-build-$LogStamp.log"
[Environment]::SetEnvironmentVariable("MIOS_UNIFIED_LOG", $LogFile)
[Environment]::SetEnvironmentVariable("MIOS_BUILD_LOG",   $BuildDetailLog)

# Initialize the unified log with a session header so post-mortem readers
# can identify the run boundary the same way Start-Transcript used to.
try {
    $scriptCommit = "(unknown)"
    try {
        $scriptDir = if ($PSCommandPath) { Split-Path $PSCommandPath -Parent }
                     elseif ($MyInvocation.MyCommand.Path) { Split-Path $MyInvocation.MyCommand.Path -Parent }
                     else { $null }
        if ($scriptDir -and (Test-Path (Join-Path $scriptDir '.git'))) {
            $sha = & git -C $scriptDir rev-parse --short HEAD 2>$null
            if ($LASTEXITCODE -eq 0 -and $sha) { $scriptCommit = "$sha" }
        }
    } catch {}
    $script:BuildMiosCommit = $scriptCommit
    [System.IO.File]::AppendAllText(
        $LogFile,
        ("=" * 78 + "`n" +
         "MiOS install session  start=$LogStamp  pid=$PID  user=$env:USERNAME  host=$env:COMPUTERNAME`n" +
         "                     build-mios.ps1 commit=$scriptCommit  version=$MiosVersion`n" +
         "=" * 78 + "`n"),
        [Text.Encoding]::UTF8)
    # Flush the deferred console-resize diagnostic captured at line ~70
    # (before $LogFile was known, before Write-Log existed). This makes
    # it visible in the unified log so we can tell post-mortem whether
    # the SetWindowSize(80,30) call actually took.
    if ($script:_PendingResizeLog) {
        [System.IO.File]::AppendAllText(
            $LogFile,
            ("[" + (Get-Date -Format 'HH:mm:ss.fff') + "][INFO] " + $script:_PendingResizeLog + "`n"),
            [Text.Encoding]::UTF8)
    }
} catch {}

# Dashboard mode is set after $script:DashRow is captured below in MAIN
# (initial render + Test-DashboardCanRedraw probe). Default to 'log'
# so any pre-MAIN Write-Log calls don't try to render-over a frame
# that doesn't exist yet.
$script:DashboardMode = 'log'

function Write-Log {
    param([string]$M, [string]$L = "INFO")
    $ts = [datetime]::Now.ToString("HH:mm:ss.fff")
    $line = "[$ts][$L] $M"
    # Append to the unified log directly. No transcript -> dashboard
    # frames cannot leak in. This is THE single canonical counting
    # system for the run; every event flows through here.
    try { [System.IO.File]::AppendAllText($LogFile, ($line + "`n"), [Text.Encoding]::UTF8) } catch {}
    if ($L -in @('WARN','ERROR')) {
        $color = if ($L -eq 'ERROR') { 'Red' } else { 'Yellow' }
        Write-Host $line -ForegroundColor $color
    }
    if ($L -eq "ERROR") { $script:ErrCount++ }
    if ($L -eq "WARN")  { $script:WarnCount++ }
}

function Start-MiosBuildMonitor {
    if ($env:MIOS_NO_MONITOR -in @('1','true','yes','on') -or
        $env:MIOS_HEADLESS -in @('1','true','yes','on')) { return }
    $launcher = Join-Path $env:ProgramData 'MiOS\bin\mios-launch.exe'
    $monitorScript = @($env:MIOS_MONITOR_SCRIPT,
        (Join-Path $PSScriptRoot 'usr\libexec\mios\mios-mon.py'),
        'C:\MiOS\usr\libexec\mios\mios-mon.py') |
        Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf) -or -not $monitorScript -or -not $python) {
        Write-Warning 'Native SSOT monitor requires the installed Rust launcher, Python and monitor asset.'
        return
    }
    # Rust resolves layered SSOT and renders/spawns WT with structured arguments.
    & $launcher --build-monitor --python $python --monitor-script $monitorScript
    if ($LASTEXITCODE -ne 0) { Write-Warning "Native SSOT monitor failed (exit $LASTEXITCODE)" }
}


Start-MiosBuildMonitor

function Initialize-MiosGlobals {
    $script:MiosFrameW     = [int](Get-MiosTomlValue -Section 'terminal' -Key 'frame_width'     -Default 80)
    $script:MiosFrameH     = [int](Get-MiosTomlValue -Section 'terminal' -Key 'frame_height'    -Default 19)
    $script:MiosRightMgn   = [int](Get-MiosTomlValue -Section 'terminal' -Key 'right_margin'    -Default 0)
    if ($script:MiosFrameW   -lt 20) { $script:MiosFrameW   = 79 }
    if ($script:MiosFrameH   -lt 5)  { $script:MiosFrameH   = 19 }
    if ($script:MiosRightMgn -lt 0)  { $script:MiosRightMgn = 1  }
    # -- [theme.font] -- font + cell metrics ------------------
    $script:MiosFontFamily = [string](Get-MiosTomlValue -Section 'theme.font' -Key 'family'      -Default 'GeistMono Nerd Font Mono')
    $script:MiosFontSize   = [int]   (Get-MiosTomlValue -Section 'theme.font' -Key 'size'        -Default 12)
    $script:MiosFontWeight = [string](Get-MiosTomlValue -Section 'theme.font' -Key 'weight'      -Default 'normal')
    $script:MiosCellW      = [int]   (Get-MiosTomlValue -Section 'theme.font' -Key 'cell_w_px'   -Default 10)
    $script:MiosCellH      = [int]   (Get-MiosTomlValue -Section 'theme.font' -Key 'cell_h_px'   -Default 20)
    $script:MiosChromeW    = [int]   (Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_w_px' -Default 20)
    $script:MiosChromeH    = [int]   (Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_h_px' -Default 12)
    # -- [theme.terminal] -- WT profile names -----------------
    $script:MiosSchemeName     = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'scheme_name'         -Default 'MiOS')
    $script:MiosProfileName    = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'profile_name'        -Default 'MiOS-WIN')
    $script:MiosDevProfileName = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'dev_profile_name'    -Default 'MiOS-DEV')
    $script:MiosHubTargetProf  = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'hub_target_profile'  -Default 'MiOS-DEV')
    $script:MiosSummonKeys     = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'summon_keys'         -Default 'win+space')
    $script:MiosSummonWindow   = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'summon_window_name'  -Default 'MiOS-DEV')
    # -- [apps] -- shortcut / AumID names ---------------------
    $script:MiosAumid          = [string](Get-MiosTomlValue -Section 'apps' -Key 'aumid'             -Default 'MiOS.Workstation')
    $script:MiosStartMenuFold  = [string](Get-MiosTomlValue -Section 'apps' -Key 'start_menu_folder' -Default 'MiOS')
    $script:MiosHubLnkName     = [string](Get-MiosTomlValue -Section 'apps' -Key 'hub_shortcut_name' -Default 'MiOS')
    # -- [branding] -- taglines + dashboard frame chars -------
    $script:MiosTagline        = [string](Get-MiosTomlValue -Section 'branding' -Key 'tagline'      -Default 'My Personal Operating System')
    $script:MiosTaglineLong    = [string](Get-MiosTomlValue -Section 'branding' -Key 'tagline_long' -Default 'My Personal Operating System  --  Immutable Fedora AI Workstation')
    $script:MiosTaglineApp     = [string](Get-MiosTomlValue -Section 'branding' -Key 'tagline_app'  -Default $script:MiosTagline)
    $script:MiosFrameChars     = [string](Get-MiosTomlValue -Section 'branding.dashboard' -Key 'frame_chars' -Default "$([char]0x256D)$([char]0x2500)$([char]0x256E)$([char]0x2502)$([char]0x2570)$([char]0x256F)")
    if ($script:MiosFrameChars.Length -lt 6) { $script:MiosFrameChars = "$([char]0x256D)$([char]0x2500)$([char]0x256E)$([char]0x2502)$([char]0x2570)$([char]0x256F)" }
}
Initialize-MiosGlobals

# UNIFIED width formula -- ONE function used by every framed surface
# in build-mios.ps1 (load-time + post-resize Show-Dashboard +
# install-complete banner) AND Show-MiosDashboard (Get-MiOS.ps1) AND
# mios-dashboard.sh (Linux).  WIDTH = min(WindowWidth - right_margin,
# frame_width) sourced from the [terminal] section loaded above.
function Get-MiosFrameWidth {
    $width = 80
    try {
        if ([Console]::WindowWidth -gt 0) {
            $width = [Console]::WindowWidth
        }
    } catch {}
    [math]::Max(60, [math]::Min(($width - $script:MiosRightMgn), $script:MiosFrameW))
}
$script:DW = Get-MiosFrameWidth
$_phaseFallbackBootstrap = @(
    "Hardware + Prerequisites",
    "Detecting environment",
    "Directories and repos",
    "MiOS-DEV distro",
    "WSL2 configuration",
    "App registration"
)
$_phaseFallbackFull = @(
    "Hardware + Prerequisites",
    "Detecting environment",
    "Directories and repos",
    "MiOS-DEV distro",
    "WSL2 configuration",
    "Verifying build context",
    "Identity",
    "Writing identity",
    "App registration",
    "Building OCI image",
    "Exporting WSL2 image",
    "Registering 'MiOS' WSL2",
    "Building disk images",
    "Deploying Hyper-V VM"
)
$_phaseSection = if ($BootstrapOnly) { 'install_phases.bootstrap' } else { 'install_phases.full' }
$_phaseFallback = if ($BootstrapOnly) { $_phaseFallbackBootstrap } else { $_phaseFallbackFull }
$script:PhaseNames = @(Get-MiosTomlValue -Section $_phaseSection -Key 'names' -Default $_phaseFallback)
if (-not $script:PhaseNames -or $script:PhaseNames.Count -eq 0) { $script:PhaseNames = $_phaseFallback }
# AppRegPhaseId is the 0-based index of "App registration" within the
# active PhaseNames array. Resolved by name search so reordering doesn't
# break the post-phase-N callers.
$script:AppRegPhaseId = [array]::IndexOf([string[]]$script:PhaseNames, 'App registration')
if ($script:AppRegPhaseId -lt 0) {
    $script:AppRegPhaseId = if ($BootstrapOnly) { 5 } else { 8 }
}
$script:TotalPhases = $script:PhaseNames.Count
# PhStat size tracks the active PhaseNames so the dashboard's status
# row never indexes past the array. 0=pending, 1=running, 2=ok,
# 3=warn, 4=fail (see Set-Step / End-Phase).
$script:PhStat = @(0) * $script:TotalPhases
$script:PhStart       = @{}
$script:PhEnd         = @{}
$script:CurPhase      = -1
$script:CurStep       = "Starting..."
$script:ErrCount      = 0
$script:WarnCount     = 0
$script:ScriptStart   = [datetime]::Now
$script:DashRow       = 0
$script:DashHeight    = 0
$script:DashLastHeight = 0
$script:DashLastWidth = 0
$script:FinalRc       = 0
$script:BuildSubTotal = if ($BootstrapOnly) { 0 } else { 48 }
$script:BuildSubDone  = 0
$script:BuildSubStep  = ""
$script:GhcrToken     = ""
# Live build tracking -- updated each loop tick; shown in debug row
$script:DebugLine     = ""
$script:LineCount     = 0
$script:HWInfo        = ""   # set after Get-Hardware; shown in dashboard info row
$script:IdentInfo     = ""   # set after phase 6 identity; User/Host/Base/Model row
# Shared state between main thread and background spinner runspace.
# SpinnerRow = -1 means unknown (spinner write suppressed until first render).
$script:DashSync = [hashtable]::Synchronized(@{
    Running    = $true
    Rendering  = $false   # set by the main thread around Show-Dashboard's
                          # buffer writes so the background heartbeat skips
                          # its spinner stamp during render -- prevents the
                          # spinner from bleeding into separator rows when
                          # the row count changes between renders.
    SpinnerRow = -1
    SpinnerCol = 2        # "| X" -- spinner is the first char inside the row body
})
$script:BgPs = $null
$script:BgRs = $null

# -- Dashboard functions -------------------------------------------------------
function fmtSpan([timespan]$s) {
    if ($s.TotalHours -ge 1) { return "{0}:{1:D2}:{2:D2}" -f [int]$s.TotalHours,$s.Minutes,$s.Seconds }
    return "{0:D2}:{1:D2}" -f [int]$s.TotalMinutes,$s.Seconds
}

function Update-BuildSubPhase([string]$line) {
    # Strip BuildKit "#N 0.123 " prefix
    $stripped = ($line -replace '^\s*#\d+\s+[\d.]+\s+', '').TrimStart()
    $script:LineCount++

    if ($stripped -match '\+-\s*STEP\s+(\d+)/(\d+)\s*:\s*(\S+)') {
        # Step start marker: "+- STEP NN/TT : scriptname.sh"
        $script:BuildSubTotal = [int]$Matches[2]
        $script:BuildSubStep  = $Matches[3] -replace '\.sh$', ''
        $script:BuildSubDone  = [math]::Max(0, [int]$Matches[1] - 1)
        $script:CurStep       = "Step $($Matches[1])/$($Matches[2]) -- $($script:BuildSubStep)"
        $script:DebugLine     = $stripped
    } elseif ($stripped -match '\+--\s+\[') {
        # Step end marker
        $script:BuildSubDone = [math]::Min($script:BuildSubDone + 1, $script:BuildSubTotal)
        $script:DebugLine    = $stripped
    } elseif (-not [string]::IsNullOrWhiteSpace($stripped)) {
        $c = ($stripped -replace '\s+', ' ').Trim()
        if ($c.Length -gt 120) { $c = $c.Substring(0, 117) + '...' }
        $script:CurStep   = $c
        $script:DebugLine = $c
    }
}

function Show-Dashboard {
    param([switch]$Force)
    # Linear-log mode: SetCursorPosition is a no-op or the host doesn't
    # support repaint -- attempting to render the framed dashboard just
    # stacks frames downward forever (one per Set-Step / phase tick).
    # Bail entirely; Start-Phase / End-Phase / Set-Step emit their own
    # one-line log messages in this mode (see those functions below).
    if ($script:DashboardMode -eq 'log') { return }

    if (-not $Force) {
        $nowMs = [Environment]::TickCount
        if ($script:DashLastRenderMs -and ($nowMs - $script:DashLastRenderMs) -lt 100) {
            return
        }
        $script:DashLastRenderMs = $nowMs
    } else {
        $script:DashLastRenderMs = [Environment]::TickCount
    }
    try {
    $winW = try { [Console]::WindowWidth  } catch { 80 }
    $bufW = try { [Console]::BufferWidth  } catch { $winW }
    $bufH = try { [Console]::BufferHeight } catch { 9999 }
    $winW = [math]::Min($winW, [math]::Min($bufW, 80))
    $w    = [math]::Max(40, [math]::Min(80, $winW - 1))
    $in = $w - 4   # inner content width: "| " + content + " |"
    # Box-drawing frame chars to match the MiOS terminal's
    # Show-MiosDashboard styling (oh-my-posh framing). $sepTop and
    # $sepBot are the rounded top/bottom corners; $sepD is the
    # divider between sections; sides use thin |.
    $sepTop = ([char]0x256D + (([char]0x2500).ToString() * ($w - 2)) + [char]0x256E).PadRight($winW)
    $sepBot = ([char]0x2570 + (([char]0x2500).ToString() * ($w - 2)) + [char]0x256F).PadRight($winW)
    $sepD   = ([char]0x251C + (([char]0x2500).ToString() * ($w - 2)) + [char]0x2524).PadRight($winW)
    $sepE   = $sepTop   # legacy alias -- header uses top corner the first time

    # -- Row helper -- script block closes over $in/$winW from caller scope -----
    $mkRow = {
        param([string]$c)
        ([char]0x2502 + " " + $c.PadRight($in) + " " + [char]0x2502).PadRight($winW)
    }

    # -- State -----------------------------------------------------------------
    $phDone = [int]($script:PhStat | Where-Object { $_ -ge 2 } | Measure-Object).Count
    $phFail = [int]($script:PhStat | Where-Object { $_ -eq 3 } | Measure-Object).Count
    $elapsed   = [datetime]::Now - $script:ScriptStart
    $elStr     = fmtSpan $elapsed
    $statusStr = if ($phFail -gt 0) { "FAILED" } `
                 elseif ($script:CurPhase -ge 0 -and $script:CurPhase -lt $script:PhStat.Count -and $script:PhStat[$script:CurPhase] -eq 1) { "RUNNING" } `
                 else { "IDLE" }
    $curName   = if ($script:CurPhase -ge 0 -and $script:CurPhase -lt $script:PhaseNames.Count) { [string]$script:PhaseNames[$script:CurPhase] } else { "Initializing" }

    # Spinner -- 500ms tick; visible on slow/remote consoles, animates even when
    # build output is silent.
    $spinChar = @('|','/','-',[char]92)[[int]($elapsed.TotalMilliseconds / 500) % 4]

    $step = (([string]$script:CurStep) -replace '\s+', ' ').Trim()
    $stepMax = [math]::Max(3, $in - 8)
    if ($step.Length -gt $stepMax) { $step = $step.Substring(0, $stepMax - 3) + "..." }

    # -- Single unified progress bar (phases + build steps = one global count) -
    $stDone  = [math]::Max(0, $script:BuildSubDone)
    $stTotal = [math]::Max(1, $script:BuildSubTotal)
    $glDone  = $phDone + $stDone
    $glTotal = $script:TotalPhases + $stTotal
    $barW    = [math]::Max(4, $in - 24)
    $glPct = 0; if ($glTotal -gt 0) { $glPct = [int](($glDone / $glTotal) * 100) }
    $glFRaw = 0; if ($glTotal -gt 0) { $glFRaw = [int](($glDone / $glTotal) * $barW) }
    $glF     = [math]::Max(0, $glFRaw)
    if ($glF -gt 0) { $glFill = ("=" * ($glF - 1)) + ">" } else { $glFill = "" }
    $glFill  = $glFill.PadRight($barW)
    $glBarL  = "[{0}] {1,3}%  {2}/{3}" -f $glFill,$glPct,$glDone,$glTotal

    $nameW = [math]::Max(8, $in - 16)
    $tableFmt = "{0,2} {1,-6} {2,-${nameW}} {3,5}"

    # -- Assemble rows ---------------------------------------------------------
    $rows = [System.Collections.Generic.List[string]]::new()

    # Header -- gap computed so total row width = $w, then padded to $winW
    $rows.Add($sepE)
    $commitTag = if ($script:BuildMiosCommit -and $script:BuildMiosCommit -ne '(unknown)') {
        " (commit $($script:BuildMiosCommit))"
    } else { '' }
    $title = " 'MiOS' $MiosVersion$commitTag  --  Build Dashboard"
    $right = "[ $elStr ] "
    $gap   = [math]::Max(0, $in - $title.Length - $right.Length)
    $hdr   = [char]0x2502 + " $title" + (" " * $gap) + "$right " + [char]0x2502
    $rows.Add($hdr.PadRight($winW))
    $rows.Add($sepD)

    # Hardware info row (populated after Get-Hardware; blank during early phases)
    if ($script:HWInfo) {
        $hw = ([string]$script:HWInfo)
        if ($hw.Length -gt $in) { $hw = $hw.Substring(0,$in-3)+"..." }
        $rows.Add((& $mkRow $hw))
    }

    # Identity row (populated after phase 6; blank before)
    if ($script:IdentInfo) {
        $id = ([string]$script:IdentInfo)
        if ($id.Length -gt $in) { $id = $id.Substring(0,$in-3)+"..." }
        $rows.Add((& $mkRow $id))
    }

    if ($script:HWInfo -or $script:IdentInfo) { $rows.Add($sepD) }

    $phIdx  = [math]::Min([math]::Max(0, $script:CurPhase), $script:PhStat.Count - 1)
    $phTag = switch ([int]$script:PhStat[$phIdx]) {
        1 { "[>>]" } 2 { "[OK]" } 3 { "[XX]" } 4 { "[!!]" } default { "[ ]" }
    }
    # Now-line: phase name + live operation stream + spinner. No
    # numeric counters here -- those live in the bar below.
    $opRowIdx = $rows.Count
    $nowLine  = "$spinChar  $phTag $curName -- $step"
    if ($nowLine.Length -gt $in) { $nowLine = $nowLine.Substring(0, $in - 3) + "..." }
    $rows.Add((& $mkRow $nowLine))
    $rows.Add($sepD)

    # The single global counter -- bar + percent + N/M of the unified
    # phase+substep total. This is THE counter; nothing else displays
    # progress numerically.
    $rows.Add((& $mkRow $glBarL))
    $rows.Add($sepD)

    # Side notes (not counters): error/warning tally + status. Errors
    # are not progress, so they get their own one-line row separate from
    # the counter row above. "Lines" was meaningless to operators and
    # was contributing to the visual noise -- dropped.
    $rows.Add((& $mkRow "Errors:$($script:ErrCount)  Warnings:$($script:WarnCount)  Status:$statusStr"))
    $rows.Add($sepD)

    # Phase table -- header, divider, and data rows ALL go through the
    # single $tableFmt printf template so the right border lands at
    # the same column on every row. Status tags are padded to 6 chars
    # to align under the "[Stat]" header column.
    $rows.Add((& $mkRow ($tableFmt -f " #", "[Stat]", "Phase Name", " Time")))
    $rows.Add((& $mkRow ($tableFmt -f "--", "------", ("-" * $nameW), "-----")))
    for ($i = 0; $i -lt $script:TotalPhases; $i++) {
        $st = switch ([int]$script:PhStat[$i]) {
            0 { "[ ]"  } 1 { "[>>]" } 2 { "[OK]" } 3 { "[XX]" } 4 { "[!!]" } default { "[??]" }
        }
        $nm = [string]$script:PhaseNames[$i]
        if ($nm.Length -gt $nameW) { $nm = $nm.Substring(0,$nameW-3)+"..." }
        $t = ""
        if ($null -ne $script:PhStart[$i]) {
            try {
                $ps = [datetime]$script:PhStart[$i]
                $pe = if ($null -ne $script:PhEnd[$i]) { [datetime]$script:PhEnd[$i] } else { [datetime]::Now }
                $t  = fmtSpan ($pe - $ps)
            } catch { $t = "--:--" }
        }
        $rows.Add((& $mkRow ($tableFmt -f $i, $st, $nm, $t)))
    }
    $rows.Add($sepD)

    # Log footer -- unified log only ($BuildDetailLog is merged in at exit)
    $logLeaf = try { Split-Path $LogFile -Leaf } catch { "?" }
    $rows.Add((& $mkRow "Log: $logLeaf"))
    $rows.Add($sepBot)

    # -- Render at fixed position; full-width overwrite eliminates bleed --------
    $dashStart = [math]::Min($script:DashRow, [math]::Max(0, $bufH - $rows.Count - 2))
    # Lock out the background heartbeat for the duration of the buffer
    # writes so the spinner can't stamp a "/" or "-" into a separator
    # row mid-render. The heartbeat sees Rendering=$true on its next
    # 120 ms tick and skips its [Console]::Write.
    $script:DashSync.Rendering = $true
    try {
        $script:DashSync.SpinnerRow = $dashStart + $opRowIdx
        for ($i = 0; $i -lt $rows.Count; $i++) {
            $tgtRow = $dashStart + $i
            if ($tgtRow -lt 0 -or $tgtRow -ge $bufH) { continue }
            [Console]::SetCursorPosition(0, $tgtRow)
            [Console]::Write($rows[$i])
        }
        if ($script:DashLastHeight -gt $rows.Count) {
            $blank = (' ' * $winW)
            $extra = $script:DashLastHeight - $rows.Count
            for ($k = 0; $k -lt $extra; $k++) {
                $blankRow = $dashStart + $rows.Count + $k
                if ($blankRow -lt 0 -or $blankRow -ge $bufH) { continue }
                [Console]::SetCursorPosition(0, $blankRow)
                [Console]::Write($blank)
            }
        }
        $script:DashHeight     = $rows.Count
        $script:DashLastHeight = $rows.Count
        $script:DashLastWidth  = $winW
        [Console]::SetCursorPosition(0, [math]::Min($dashStart + $script:DashHeight, $bufH - 1))
    } finally {
        $script:DashSync.Rendering = $false
    }

    } catch {
        Write-Host "[$([datetime]::Now.ToString('HH:mm:ss.fff'))][WARN] dashboard render error: $_ -- falling back to linear log mode"
        $script:DashboardMode = 'log'
    }
}

function Start-Phase([int]$i) {
    $script:CurPhase   = $i
    $script:PhStat[$i] = 1
    $script:PhStart[$i] = [datetime]::Now
    $script:CurStep    = $script:PhaseNames[$i]
    Write-Log "START phase $i : $($script:PhaseNames[$i])"
    if ($script:DashboardMode -eq 'log') {
        $ts = [datetime]::Now.ToString("HH:mm:ss")
        Write-Host ""
        # In bootstrap-only mode, phases 6-13 never run; report just the
        # phase number + name without a misleading X/13 ratio.
        $phaseTag = if ($BootstrapOnly) { "Phase $i" } else { "Phase $i/$($script:TotalPhases - 1)" }
        Write-Host "[$ts] >> $phaseTag -- $($script:PhaseNames[$i])" -ForegroundColor Cyan
    } else {
        Show-Dashboard -Force
    }
}

function End-Phase([int]$i, [switch]$Fail, [switch]$Warn) {
    $script:PhStat[$i] = if ($Fail) { 3 } elseif ($Warn) { 4 } else { 2 }
    $script:PhEnd[$i]  = [datetime]::Now
    $spanStr = try {
        if ($null -ne $script:PhStart[$i]) { fmtSpan ([datetime]$script:PhEnd[$i] - [datetime]$script:PhStart[$i]) } else { "--:--" }
    } catch { "--:--" }
    $tag     = if ($Fail) { "FAIL" } elseif ($Warn) { "WARN" } else { "OK  " }
    $lvl     = if ($Fail) { "ERROR" } else { "INFO" }
    Write-Log "$tag  phase $i : $($script:PhaseNames[$i]) ($spanStr)" $lvl
    if ($script:DashboardMode -eq 'log') {
        $ts = [datetime]::Now.ToString("HH:mm:ss")
        $color = if ($Fail) { 'Red' } elseif ($Warn) { 'Yellow' } else { 'Green' }
        $mark  = if ($Fail) { 'XX' } elseif ($Warn) { '!!' } else { 'OK' }
        Write-Host "[$ts] [$mark] Phase $i ($spanStr)  $($script:PhaseNames[$i])" -ForegroundColor $color
        # Inline progress bar after every phase transition. Operator
        # sees how far into the build they are without having to count.
        Show-MiosProgressBar
    } else {
        Show-Dashboard -Force
    }
}

function Show-MiosProgressBar {
    if (-not $script:PhStat) { return }
    $done = [int]($script:PhStat | Where-Object { $_ -ge 2 } | Measure-Object).Count
    $total = [int]$script:TotalPhases
    if ($total -le 0) { return }
    $pct = [int](($done / $total) * 100)
    $barW = 50
    $filled = [int](($done / $total) * $barW)
    if ($filled -lt 0) { $filled = 0 }
    if ($filled -gt $barW) { $filled = $barW }
    $bar = ((([char]0x2588).ToString()) * $filled) + ((([char]0x2591).ToString()) * ($barW - $filled))
    Write-Host "  [$bar] $done/$total ($pct%)" -ForegroundColor Cyan
}

# Throttle Set-Step prints in log mode -- the build pipeline calls
# Set-Step on every line of native output, which would flood the log.
# Print at most once per 2 seconds OR on a substantially-changed step.
$script:LastStepLogTime = [datetime]::MinValue
$script:LastStepLogText = ""
function _TruncToWidth {
    # Shorten a string to fit within $maxW visible chars. Long Windows
    # paths like "C:\Users\Administrator\AppData\Local\MiOS\repo\..."
    # get middle-elided to keep both ends visible:
    #   "C:\...\MiOS\repo\subdir\file.ext"
    # Falls back to simple tail truncation with "..." for non-paths.
    param([string]$S, [int]$MaxW = 78)
    if ($S.Length -le $MaxW) { return $S }
    # Path-aware: middle-elide if the string contains backslashes.
    if ($S -match '\\' -and $S.Length -gt 30) {
        $left  = $S.Substring(0, [int]($MaxW * 0.4))
        $right = $S.Substring($S.Length - [int]($MaxW * 0.5))
        $cand  = "$left$([char]0x2026)$right"
        if ($cand.Length -le $MaxW) { return $cand }
    }
    return $S.Substring(0, $MaxW - 1) + [char]0x2026
}

function Set-Step([string]$T) {
    $script:CurStep = $T
    Write-Log "step: $T"
    if ($script:DashboardMode -eq 'log') {
        # Skip console echo for WARN:/FAIL: -- Write-Log already
        # mirrored those.
        if ($T -match '^(WARN|FAIL):') { return }
        $now = [datetime]::Now
        $clean = ($T -replace '\s+', ' ').Trim()
        $secsSince = ($now - $script:LastStepLogTime).TotalSeconds
        $isFirst   = ($script:LastStepLogTime -eq [datetime]::MinValue)
        if ($isFirst -or $secsSince -ge 2 -or $clean -ne $script:LastStepLogText) {
            $ts = $now.ToString("HH:mm:ss")
            # Truncate long paths so the line fits in the 80-col window
            # without wrapping. "  [HH:MM:SS]  " prefix is 14 chars,
            # leaving 66 chars for content in a 80-col terminal.
            $maxContent = $script:DW - 14
            $clean = _TruncToWidth -S $clean -MaxW $maxContent
            Write-Host "  [$ts]  $clean" -ForegroundColor DarkGray
            $script:LastStepLogTime = $now
            $script:LastStepLogText = $clean
        }
    } else {
        Show-Dashboard
    }
}

function Log-Ok([string]$T)   { Write-Log $T;          Set-Step $T }
function Log-Info([string]$T) { Write-Log $T;          Set-Step $T }
function Log-Warn([string]$T) { Write-Log $T "WARN";  Set-Step "WARN: $T" }
function Log-Fail([string]$T) { Write-Log $T "ERROR"; Set-Step "FAIL: $T" }

# -- Utility helpers -----------------------------------------------------------
function ConvertTo-WslPath([string]$P) {
    $P = $P -replace '\\','/'
    if ($P -match '^([A-Za-z]):(.*)') { return "/mnt/$($Matches[1].ToLower())$($Matches[2])" }
    return $P
}

function Move-BelowDash {
    try {
        $targetRow = [math]::Min($script:DashRow + $script:DashHeight, [Console]::BufferHeight - 1)
        [Console]::SetCursorPosition(0, $targetRow)
    } catch {}
}

function Repair-WslConfig {
    $wslCfg = Join-Path $env:USERPROFILE ".wslconfig"
    $bootSectionKeys = @('systemd', 'command', 'enabled', 'appendWindowsPath',
                         'default', 'options', 'mountFsTab',
                         'generateHosts', 'generateResolvConf', 'hostname')
    $winBuild = [Environment]::OSVersion.Version.Build
    $targetNetMode = if ($winBuild -ge 22621) { 'mirrored' } else { 'NAT' }

    if (-not (Test-Path $wslCfg)) {
        $initLines = @(
            "[wsl2]",
            "networkingMode=$targetNetMode"
        )
        [System.IO.File]::WriteAllLines($wslCfg, $initLines, (New-Object System.Text.UTF8Encoding($false)))
        Log-Ok ".wslconfig: initialized with [wsl2] networkingMode=$targetNetMode (BOM-free UTF-8)"
        return
    }

    $lines = Get-Content $wslCfg
    $inWsl2 = $false
    $hasWsl2 = $false
    $hasNetMode = $false
    $newLines = [System.Collections.Generic.List[string]]::new()
    $scrubbed = 0
    $modified = 0

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^\s*\[wsl2\]\s*$') {
            $inWsl2 = $true
            $hasWsl2 = $true
            $newLines.Add($line)
            continue
        }
        if ($line -match '^\s*\[') {
            if ($inWsl2 -and -not $hasNetMode) {
                $newLines.Add("networkingMode=$targetNetMode")
                $hasNetMode = $true
                $modified++
            }
            $inWsl2 = $false
            $newLines.Add($line)
            continue
        }
        if ($inWsl2) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
                $key = $Matches[1]
                $val = $Matches[2].Trim()
                if ($bootSectionKeys -contains $key) {
                    Write-Log "wslconfig-repair: dropped misplaced '$key=' line from [wsl2] (belongs in /etc/wsl.conf, not .wslconfig)" "WARN"
                    $scrubbed++
                    continue
                }
                if ($key -ieq 'networkingMode') {
                    $hasNetMode = $true
                    if ($winBuild -lt 22621 -and $val -ieq 'mirrored') {
                        Write-Log "wslconfig-repair: rewriting networkingMode=mirrored to NAT for Windows 10 (Build $winBuild)" "INFO"
                        $newLines.Add("networkingMode=NAT")
                        $modified++
                        continue
                    } elseif ($winBuild -ge 22621 -and $val -ieq 'NAT') {
                        Write-Log "wslconfig-repair: upgrading networkingMode=NAT to mirrored for Windows 11 22H2+ (Build $winBuild)" "INFO"
                        $newLines.Add("networkingMode=mirrored")
                        $modified++
                        continue
                    }
                }
                # Preserve user-configured memory, processors, swap, etc.
            }
        }
        $newLines.Add($line)
    }

    if ($hasWsl2 -and -not $hasNetMode) {
        $newLines.Add("networkingMode=$targetNetMode")
        $modified++
    } elseif (-not $hasWsl2) {
        $newLines.Add("[wsl2]")
        $newLines.Add("networkingMode=$targetNetMode")
        $modified++
    }

    [System.IO.File]::WriteAllLines($wslCfg, $newLines, (New-Object System.Text.UTF8Encoding($false)))
    if ($scrubbed -gt 0 -or $modified -gt 0) {
        Log-Ok ".wslconfig: repaired ($scrubbed misplaced keys dropped, $modified updates, BOM-free UTF-8)"
    }
}

function Invoke-NativeQuiet {
    param([scriptblock]$Cmd)
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & $Cmd 2>&1 | Out-Null
        $LASTEXITCODE
    }
}


function Read-Line([string]$Prompt, [string]$Default = "") {
    Move-BelowDash
    Write-Host "  $Prompt" -NoNewline -ForegroundColor White
    if ($Default) { Write-Host " [$Default]" -NoNewline -ForegroundColor DarkGray }
    Write-Host ": " -NoNewline
    if ($Unattended) { Write-Host $Default -ForegroundColor DarkGray; return $Default }
    $v = Read-Host
    if ([string]::IsNullOrWhiteSpace($v)) { return $Default } else { return $v }
}


function Resolve-MiosTomlAiDefaults([string]$RepoDir) {
    return @{
        Model              = [string](Get-MiosTomlValue -Section 'ai' -Key 'model' -Default 'qwen3.5:2b')
        EmbedModel         = [string](Get-MiosTomlValue -Section 'ai' -Key 'embed_model' -Default 'nomic-embed-text')
        BakeModels         = [string](Get-MiosTomlValue -Section 'ai' -Key 'bake_models' -Default 'qwen3.5:2b,nomic-embed-text')
        LlamacppBakeModels = [string](Get-MiosTomlValue -Section 'llamacpp' -Key 'bake_models' -Default 'granite-4.1-8b.gguf=unsloth/granite-4.1-8b-GGUF:granite-4.1-8b-Q4_K_M.gguf,lfm2-700m.gguf=LiquidAI/LFM2-700M-GGUF:LFM2-700M-Q4_K_M.gguf,embeddinggemma-300m-qat-q8_0.gguf=ggml-org/embeddinggemma-300m-qat-q8_0-GGUF:embeddinggemma-300m-qat-Q8_0.gguf')
        VllmBakeModel      = [string](Get-MiosTomlValue -Section 'ai.vllm' -Key 'bake_model' -Default 'Qwen/Qwen2.5-0.5B-Instruct')
    }
}

function New-SeededConfiguratorHtml {
    param([string]$HtmlPath, [string]$TomlPath)
    if (-not (Test-Path -LiteralPath $HtmlPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $TomlPath -PathType Leaf)) { return $HtmlPath }
    try {
        $htmlText = [IO.File]::ReadAllText($HtmlPath)
        $tomlText = [IO.File]::ReadAllText($TomlPath, (New-Object System.Text.UTF8Encoding($false)))
        $json = ConvertTo-Json -InputObject $tomlText -Compress
        $json = $json.Replace('</', '<\/')
        $seedTag = "<script>window.MIOS_BOOTSTRAP_TOML = $json;</script>`n"
        $headEnd = $htmlText.LastIndexOf('</head>', [StringComparison]::OrdinalIgnoreCase)
        if ($headEnd -lt 0) { return $HtmlPath }
        $target = Join-Path $env:TEMP ('mios-configurator-seeded-' + [guid]::NewGuid().ToString('N') + '.html')
        [IO.File]::WriteAllText($target, $htmlText.Insert($headEnd, $seedTag), (New-Object System.Text.UTF8Encoding($false)))
        return $target
    } catch {
        Log-Warn "Could not stage actual mios.toml into standalone configurator: $($_.Exception.Message)"
        return $HtmlPath
    }
}

function Open-Configurator([string]$RepoDir) {
    if ($Unattended) { return }
    if ($env:MIOS_NO_CONFIGURATOR -eq "1") { return }

    $resp = Read-Line "Open MiOS configurator (Epiphany on MiOS-DEV via WSLg)?" "y"
    if ($resp -notmatch '^(y|yes|true|1)$') { return }

    $candidates = @(
        (Join-Path $RepoDir "mios\usr\share\mios\configurator\mios.html"),
        (Join-Path $MiosShareDir "system\usr\share\mios\configurator\mios.html"),
        (Join-Path $MiosShareDir "bootstrap\usr\share\mios\configurator\mios.html")
    )
    $html = $null
    foreach ($c in $candidates) { if (Test-Path $c) { $html = $c; break } }
    if (-not $html) {
        Write-Log "Configurator HTML not found locally -- skipping GUI step" "WARN"
        return
    }

    if (Open-ConfiguratorInDev -RepoDir $RepoDir -Html $html) { return }
    Log-Warn "MiOS-DEV / Epiphany unavailable -- falling back to Windows default browser"
    Open-ConfiguratorOnWindows -RepoDir $RepoDir -Html $html
}

function Open-ConfiguratorInDev([string]$RepoDir, [string]$Html) {
    # Probe MiOS-DEV (canonical name then legacy fallback)
    $wslDistro = $null
    foreach ($candidate in @("podman-$DevDistro", $DevDistro, "podman-$LegacyDevName")) {
        $probe = (& wsl.exe -d $candidate --exec bash -c "echo ok" 2>$null) -join ""
        if ($probe.Trim() -eq "ok") { $wslDistro = $candidate; break }
    }
    if (-not $wslDistro) { return $false }

    # Find the regular user (uid 1000) inside the dev VM. Podman machines
    # default to "user"; we honor whatever's actually there.
    $devUser = ((& wsl.exe -d $wslDistro --exec bash -c "getent passwd 1000 | cut -d: -f1" 2>$null) -join "").Trim()
    if (-not $devUser) { $devUser = "user" }

    # Convert C:\path\mios.html -> /mnt/c/path/mios.html
    $drive    = $Html.Substring(0,1).ToLower()
    $htmlWsl  = "/mnt/$drive" + ($Html.Substring(2) -replace '\\','/')

    # Resolve the seed mios.toml the configurator should pre-load. Pick
    # the highest-precedence existing layer; the bash side will copy it
    # into the dev VM's ~/Downloads/mios.toml as the working file.
    $sources = @(
        (Join-Path $env:USERPROFILE '.config\mios\mios.toml'),
        (Join-Path $env:APPDATA "MiOS\mios.toml"),
        'M:\usr\share\mios\mios.toml',
        (Join-Path $RepoDir "usr\share\mios\mios.toml"),
        'C:\MiOS\usr\share\mios\mios.toml',
        (Join-Path $RepoDir "mios\usr\share\mios\mios.toml")
    )
    $seedToml = $null
    foreach ($s in $sources) { if (Test-Path $s) { $seedToml = $s; break } }
    $seedTomlWsl = ""
    if ($seedToml) {
        $sd = $seedToml.Substring(0,1).ToLower()
        $seedTomlWsl = "/mnt/$sd" + ($seedToml.Substring(2) -replace '\\','/')
    }
    $html = New-SeededConfiguratorHtml -HtmlPath $html -TomlPath $seedToml
    $htmlDrive = $html.Substring(0,1).ToLower()
    $htmlWsl = "/mnt/$htmlDrive" + ($html.Substring(2) -replace '\\','/')

    Write-Host ""
    Write-Host "  Launching Epiphany on $wslDistro (user: $devUser) ..." -ForegroundColor Cyan
    Write-Host "  Configurator URL:    file://~/Downloads/mios-configurator.html" -ForegroundColor Gray
    Write-Host "  Working mios.toml:   /home/$devUser/Downloads/mios.toml" -ForegroundColor Gray
    Write-Host "  WSLg routes the Epiphany window to the Windows desktop." -ForegroundColor Gray
    Write-Host ""

    $bashScript = @'
#!/usr/bin/env bash
# Generated by build-mios.ps1 / Open-ConfiguratorInDev.
set -euo pipefail
SRC_HTML="${1:?html path required}"
SEED_TOML="${2:-}"
USER_NAME="${3:-user}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
DL_DIR="$USER_HOME/Downloads"

sudo -u "$USER_NAME" install -d -m 0755 "$DL_DIR"

if [[ -n "$SEED_TOML" && -r "$SEED_TOML" ]]; then
    sudo -u "$USER_NAME" install -m 0644 "$SEED_TOML" "$DL_DIR/mios.toml"
elif [[ ! -f "$DL_DIR/mios.toml" ]]; then
    sudo -u "$USER_NAME" touch "$DL_DIR/mios.toml"
fi

# Copy the HTML configurator into ~/Downloads where Epiphany's flatpak
# sandbox can read it via the home-portal default exposure.
sudo -u "$USER_NAME" install -m 0644 "$SRC_HTML" "$DL_DIR/mios-configurator.html"

# Ensure flathub remote + Epiphany flatpak are present (system-wide install).
flatpak remote-add --system --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
if ! flatpak list --system --app --columns=application 2>/dev/null | grep -qx org.gnome.Epiphany; then
    echo "[configurator] installing org.gnome.Epiphany flatpak (one-time, ~250 MB)..."
    flatpak install --system --noninteractive --assumeyes --or-update flathub org.gnome.Epiphany \
        2>&1 | grep -E '^(Installing|Updating|Already|Error|Warning)' || true
fi

# Resolve the WSLg display sockets for the regular user. WSLg sets
# WAYLAND_DISPLAY=wayland-0 + DISPLAY=:0 in $HOME/.profile, but a
# sudo invocation strips those -- pull them from /run/user/1000.
RT="/run/user/$(id -u "$USER_NAME")"
[[ -d "$RT" ]] || RT="/tmp/runtime-$USER_NAME"
sudo -u "$USER_NAME" mkdir -p "$RT"

# Launch Epiphany detached. Browsers refuse to run as root, so we drop
# to the regular user. The flatpak run wrapper picks up the seat's
# Wayland socket via XDG_RUNTIME_DIR.
sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="$RT" \
    DISPLAY=":0" \
    WAYLAND_DISPLAY="wayland-0" \
    PULSE_SERVER="unix:$RT/pulse/native" \
    flatpak run org.gnome.Epiphany \
        "file://$DL_DIR/mios-configurator.html" >/dev/null 2>&1 &
disown
echo "[configurator] Epiphany launched -- window should appear on the Windows desktop"
echo "[configurator] save target: $DL_DIR/mios.toml"
'@

    # PowerShell @'...'@ here-strings produce CRLF line endings on
    # Windows. The bash shebang then becomes "#!/usr/bin/env bash\r"
    # and `env` errors with "bash\r: No such file or directory".
    # Strip CR before base64-encoding so the script lands clean inside
    # the WSL distro.
    $bashScript = $bashScript -replace "`r`n", "`n" -replace "`r", "`n"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bashScript))
    $stage = "set -e; echo '$b64' | base64 -d > /tmp/launch-config.sh && chmod +x /tmp/launch-config.sh; " +
             "/tmp/launch-config.sh '$htmlWsl' '$seedTomlWsl' '$devUser'"
    & wsl.exe -d $wslDistro --exec bash -c $stage 2>&1 | ForEach-Object { Write-Log "configurator: $_" }
    if ($LASTEXITCODE -ne 0) { Log-Warn "Epiphany launch returned rc=$LASTEXITCODE -- falling back"; return $false }

    Write-Host ""
    Write-Host "  In Epiphany on the Windows desktop:" -ForegroundColor Cyan
    Write-Host "    1. Click 'Pick file' (or 'Open (fallback)') -> ~/Downloads/mios.toml" -ForegroundColor Gray
    Write-Host "    2. Edit identity / AI / desktop / flatpaks / quadlets" -ForegroundColor Gray
    Write-Host "    3. Click 'Save' -- the file overwrites ~/Downloads/mios.toml" -ForegroundColor Gray
    Write-Host ""
    $null = Read-Host "  Press Enter when finished editing in Epiphany"

    $tomlContent = (& wsl.exe -d $wslDistro --user $devUser --exec cat "/home/$devUser/Downloads/mios.toml" 2>$null) -join "`n"
    if ([string]::IsNullOrWhiteSpace($tomlContent)) {
        Log-Warn "No saved mios.toml found at /home/$devUser/Downloads/ -- continuing with vendor default"
        return $true
    }

    $userLayer = Join-Path $env:USERPROFILE '.config\mios\mios.toml'
    $userDir   = Split-Path -Parent $userLayer
    if (-not (Test-Path $userDir)) { New-Item -ItemType Directory -Path $userDir -Force | Out-Null }
    [System.IO.File]::WriteAllText($userLayer, $tomlContent, [Text.UTF8Encoding]::new($false))

    Log-Ok "Saved mios.toml -> $userLayer"
    return $true
}

function Open-ConfiguratorOnWindows([string]$RepoDir, [string]$Html) {
    $stagingDir = Join-Path $env:TEMP "mios-configurator"
    if (-not (Test-Path $stagingDir)) { New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null }
    $stamp   = [datetime]::Now.ToString("yyyyMMdd-HHmmss")
    $staging = Join-Path $stagingDir "mios-$stamp.toml"
    $sources = @(
        (Join-Path $env:USERPROFILE '.config\mios\mios.toml'),
        (Join-Path $env:APPDATA "MiOS\mios.toml"),
        'M:\usr\share\mios\mios.toml',
        (Join-Path $RepoDir "usr\share\mios\mios.toml"),
        'C:\MiOS\usr\share\mios\mios.toml',
        (Join-Path $RepoDir "mios\usr\share\mios\mios.toml")
    )
    $src = $null
    foreach ($s in $sources) { if (Test-Path $s) { $src = $s; break } }
    if ($src) { Copy-Item -Path $src -Destination $staging -Force }
    else      { New-Item -ItemType File -Path $staging -Force | Out-Null }

    $seededHtml = New-SeededConfiguratorHtml -HtmlPath $Html -TomlPath $staging
    $stagingForUrl = ($staging -replace '\\', '/' -replace ' ', '%20')
    $url = "file:///$($seededHtml -replace '\\', '/' -replace ' ', '%20')?suggested_path=$stagingForUrl"
    Write-Host ""
    Write-Host "  Opening configurator: $url" -ForegroundColor Cyan
    Write-Host "  Staging file:         $staging" -ForegroundColor Cyan
    Write-Host ""
    try { Start-Process $url -ErrorAction Stop }
    catch { Log-Warn "Browser launch failed: $($_.Exception.Message). Open manually: $url" }
    $null = Read-Host "  Press Enter when finished editing in the browser"

    if ((Test-Path $staging) -and ((Get-Item $staging).Length -gt 0)) {
        $userLayer = Join-Path $env:USERPROFILE '.config\mios\mios.toml'
        $userDir   = Split-Path -Parent $userLayer
        if (-not (Test-Path $userDir)) { New-Item -ItemType Directory -Path $userDir -Force | Out-Null }
        Copy-Item -Path $staging -Destination $userLayer -Force
        Log-Ok "Staged $staging -> $userLayer"
    }
}


function Get-PasswordHash([string]$Plain) {
    if ($Plain -eq "mios" -or [string]::IsNullOrWhiteSpace($Plain)) {
        return '$6$miosmios0$ShHuf/TnPoEmEX//L9mrNNuP7kZ6l9aj/qV9WFj5LnjL3lunhKEwnJfY6tvlJbRiWkLTtPmdwCgWeOQB9eXuW.'
    }
    $salt = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    foreach ($d in @($BuilderDistro, $LegacyDistro)) {
        try {
            $h = (& wsl.exe -d $d --exec openssl passwd -6 -salt $salt $Plain 2>$null) -join ""
            if ($LASTEXITCODE -eq 0 -and $h -match '^\$6\$') { return $h.Trim() }
        } catch {}
    }
    # Dev-distro shell (works pre- AND post-rename via Invoke-DistroSh
    # auto-detect): wsl-direct on MiOS-DEV, podman-machine-ssh on
    # podman-MiOS-DEV. -NoSudo because openssl needs no privilege.
    try {
        $h = (Invoke-DistroSh -Bash "openssl passwd -6 -salt '$salt' '$Plain'" -MachineName $BuilderDistro -NoSudo 2>$null) -join ""
        if ($LASTEXITCODE -eq 0 -and $h -match '^\$6\$') { return $h.Trim() }
    } catch {}
    try {
        $h = (& podman run --rm docker.io/library/alpine:latest sh -c "apk add -q openssl && openssl passwd -6 -salt '$salt' '$Plain'" 2>$null) -join ""
        if ($LASTEXITCODE -eq 0 -and $h -match '^\$6\$') { return $h.Trim() }
    } catch {}
    throw "Cannot generate sha512crypt hash -- install openssl or run from a distro."
}

function Get-Hardware {
    $hostRamGB = try { [math]::Round((Get-CimInstance Win32_PhysicalMemory|Measure-Object Capacity -Sum).Sum/1GB) } catch { 16 }
    # OS-reported RAM (bytes) -- this is what podman validates against; may be less than nominal GB count
    $osTotalRamMB = try { [math]::Floor((Get-CimInstance Win32_ComputerSystem -EA Stop).TotalPhysicalMemory / 1MB) } catch { $hostRamGB * 1024 }
    $hostCpus = [Environment]::ProcessorCount

    # GPU surface: enumerate every non-Microsoft-Basic display adapter.
    # The dev VM (WSL2) automatically gets host-GPU access via /dev/dxg
    # (WSLg) for compute; this enumeration drives base-image selection
    # and is reflected in the dispatched manifest.
    $allGpus = try {
        Get-CimInstance Win32_VideoController -EA Stop |
            Where-Object { $_.Name -notmatch "Microsoft Basic|Microsoft Hyper-V Video|Remote Display" }
    } catch { @() }
    $gpu       = $allGpus | Select-Object -First 1
    $gpuName   = if ($gpu) { $gpu.Name } else { "Unknown" }
    $gpuNames  = ($allGpus | ForEach-Object { $_.Name }) -join ', '
    $hasNvidia = $gpuNames -match "NVIDIA|GeForce|Quadro|RTX|GTX|Tesla"
    $hasAmd    = $gpuNames -match "AMD|Radeon|RX |R[5-9] |Vega|Navi"
    $hasIntel  = $gpuNames -match "Intel|Iris|UHD Graphics|HD Graphics"
    # Base image variants resolve through mios.toml [image].base_nvidia /
    # base_no_nvidia (SSOT). Operators can swap upstreams (ucore-minimal,
    # fedora-bootc, etc.) via mios.html without touching code.
    $_baseNvidia   = Get-MiosTomlValue -Section 'image' -Key 'base_nvidia'    -Default 'ghcr.io/ublue-os/ucore-hci:stable-nvidia'
    $_baseNoNvidia = Get-MiosTomlValue -Section 'image' -Key 'base_no_nvidia' -Default 'ghcr.io/ublue-os/ucore-hci:stable'
    $baseImage     = if ($hasNvidia) { $_baseNvidia } else { $_baseNoNvidia }
    # AI model auto-pick by host RAM. Thresholds + model IDs from mios.toml
    # [ai.host_thresholds] (NEW). Operators tune the cutoffs or swap to a
    # different family (mistral / llama / etc.) via mios.html.
    $_aiBig    = Get-MiosTomlValue -Section 'ai.host_thresholds' -Key 'big_ram_gb'        -Default 32
    $_aiMid    = Get-MiosTomlValue -Section 'ai.host_thresholds' -Key 'mid_ram_gb'        -Default 12
    $_aiBigM   = Get-MiosTomlValue -Section 'ai.host_thresholds' -Key 'big_ram_model'     -Default 'qwen3.5:14b'
    $_aiMidM   = Get-MiosTomlValue -Section 'ai.host_thresholds' -Key 'mid_ram_model'     -Default 'qwen3.5:2b'
    $_aiSmallM = Get-MiosTomlValue -Section 'ai.host_thresholds' -Key 'small_ram_model'   -Default 'phi4-mini:3.8b-q4_K_M'
    $aiModel   = if ($hostRamGB -ge $_aiBig) { $_aiBigM } elseif ($hostRamGB -ge $_aiMid) { $_aiMidM } else { $_aiSmallM }

    # Free space on the data disk (M:\ if provisioned, else C:\). The
    # dev VM's VHDX lives on M:\ when Initialize-MiosDataDisk has run.
    $diskLetter = if ($env:MIOS_DATA_DISK_LETTER) { $env:MIOS_DATA_DISK_LETTER } else { 'M' }
    $diskFreeGB = try { [math]::Floor((Get-PSDrive $diskLetter -EA Stop).Free/1GB) } catch {
        try { [math]::Floor((Get-PSDrive C -EA Stop).Free/1GB) } catch { 200 }
    }

    # Read host_reserve policy from env (synthesized from
    # mios.toml by tools/lib/userenv.sh); fall back to sane defaults.
    $cpuReservePct = if ($env:MIOS_DEV_VM_CPU_RESERVE_PCT)    { [int]$env:MIOS_DEV_VM_CPU_RESERVE_PCT }    else { 15 }
    $cpuReserveMin = if ($env:MIOS_DEV_VM_CPU_RESERVE_MIN)    { [int]$env:MIOS_DEV_VM_CPU_RESERVE_MIN }    else { 2 }
    $memReservePct = if ($env:MIOS_DEV_VM_MEMORY_RESERVE_PCT) { [int]$env:MIOS_DEV_VM_MEMORY_RESERVE_PCT } else { Get-MiosTomlValue -Section 'bootstrap.dev_vm.host_reserve' -Key 'memory_pct' -Default 50 }
    $memReserveGB  = if ($env:MIOS_DEV_VM_MEMORY_RESERVE_GB)  { [int]$env:MIOS_DEV_VM_MEMORY_RESERVE_GB }  else { Get-MiosTomlValue -Section 'bootstrap.dev_vm.host_reserve' -Key 'memory_gb'  -Default 8 }
    $memReservePct = [math]::Min(95, [math]::Max(0, $memReservePct))
    $memReserveGB  = [math]::Max(0, $memReserveGB)
    $diskReserveGB = if ($env:MIOS_DEV_VM_DISK_RESERVE_GB)    { [int]$env:MIOS_DEV_VM_DISK_RESERVE_GB }    else { 32 }

    # Compute maximalist dev-VM allocation = host - reserve.
    $reservedCpus = [math]::Max($cpuReserveMin, [math]::Floor($hostCpus * $cpuReservePct / 100))
    $devCpus = [math]::Max(1, $hostCpus - $reservedCpus)
    $reservedRamGB = [math]::Max($memReserveGB, [math]::Floor($hostRamGB * $memReservePct / 100))
    $devRamGB = [math]::Max(4, $hostRamGB - $reservedRamGB)
    $devDiskGB = [math]::Max(80, $diskFreeGB - $diskReserveGB)

    # Apply explicit pin overrides from mios.toml [bootstrap.dev_vm].*
    # (set to "max" or empty/unset to use the computed maximalist value).
    if ($env:MIOS_DEV_VM_CPUS      -and $env:MIOS_DEV_VM_CPUS      -notmatch '^(max|0|)$') { $devCpus   = [int]$env:MIOS_DEV_VM_CPUS      }
    if ($env:MIOS_DEV_VM_MEMORY_MB -and $env:MIOS_DEV_VM_MEMORY_MB -notmatch '^(max|0|)$') { $devRamGB  = [math]::Max(4, [math]::Floor([int]$env:MIOS_DEV_VM_MEMORY_MB / 1024)) }
    if ($env:MIOS_DEV_VM_DISK_GB   -and $env:MIOS_DEV_VM_DISK_GB   -notmatch '^(max|0|)$') { $devDiskGB = [int]$env:MIOS_DEV_VM_DISK_GB   }

    Write-Log "Get-Hardware: host=${hostCpus}c/${hostRamGB}GB/${diskFreeGB}GB  reserve=${reservedCpus}c/${reservedRamGB}GB/${diskReserveGB}GB  dev-vm=${devCpus}c/${devRamGB}GB/${devDiskGB}GB  gpu=[$gpuNames]"

    return @{
        # Host-detected (informational)
        HostRamGB    = $hostRamGB
        HostCpus     = $hostCpus
        OsTotalRamMB = $osTotalRamMB
        AllGpus      = $allGpus
        GpuNames     = $gpuNames
        GpuName      = $gpuName
        HasNvidia    = $hasNvidia
        HasAmd       = $hasAmd
        HasIntel     = $hasIntel
        # Dispatched dev-VM allocation (maximalist - host_reserve)
        Cpus         = $devCpus
        RamGB        = $devRamGB
        DiskGB       = $devDiskGB
        # Image / model selection
        BaseImage    = $baseImage
        AiModel      = $aiModel
    }
}

function Find-ActiveDistro {
    # Check legacy WSL distros ('MiOS' already applied via bootc switch, has /Justfile)
    foreach ($d in @($BuilderDistro, $LegacyDistro)) {
        try {
            $r = (& wsl.exe -d $d --exec bash -c "test -f /Justfile && echo ready" 2>$null) -join ""
            if ($r.Trim() -eq "ready") { return $d }
        } catch {}
    }
    # Check if BuilderDistro is a running Podman machine (machine-os: no /Justfile but can still build)
    try {
        $ml = (& podman machine ls --format "{{.Name}} {{.Running}}" 2>$null) |
              Where-Object { $_ -match "(?i)^$([regex]::Escape($BuilderDistro))\s+true" }
        if ($ml) { return $BuilderDistro }
    } catch {}
    return $null
}


function Initialize-MiosDataDisk {
    <#
    .SYNOPSIS
        Shrink C: by exactly $ShrinkMB and create a dedicated MiOS-DEV partition
        in the freed space. Redirect podman-machine storage onto that partition
        so MiOS-DEV's VHDX (which internally hosts the ext4 root) lives on the
        new drive end-to-end.

    .NOTES
        WSL2 STORES DISTROS AS VHDX FILES. The VHDX format requires a Windows-
        accessible host filesystem (NTFS or ReFS) -- a raw ext4 host partition
        cannot host a VHDX. The new partition is therefore formatted NTFS, and
        MiOS-DEV's Linux root inside the VHDX *is* ext4 (mkfs'd by WSL2 at first
        boot). Result: the operator's "ext partition for MiOS-DEV" requirement
        is satisfied at the WSL/Linux layer, with the host wrapper as the thin
        NTFS shell that WSL2 strictly requires.

        Idempotent: a partition labeled $VolumeLabel on $DriveLetter is treated
        as already-initialized and the function returns without shrinking again.
    #>
    param(
        [int]$ShrinkMB     = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'shrink_mb' -Default 262656),
        [string]$DriveLetter = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default 'M'),
        [string]$VolumeLabel = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'volume_label' -Default 'MIOS-DEV')
    )

    # Step description from mios.toml [messages.steps].disk_sizing_template
    # (SSOT). {placeholder} substitution at render time.
    Set-Step ((Get-MiosTomlValue -Section 'messages.steps' -Key 'disk_sizing_template' -Default 'Sizing MiOS data disk ({mb} MB on {drive}:)...') -replace '\{mb\}', $ShrinkMB -replace '\{drive\}', $DriveLetter)

    # 0. Already-initialized? Skip.
    $existing = Get-Volume -DriveLetter $DriveLetter -ErrorAction SilentlyContinue
    if ($existing -and $existing.FileSystemLabel -eq $VolumeLabel) {
        $_sgb = [math]::Round($existing.Size/1GB,1)
        Log-Ok ((Get-MiosTomlValue -Section 'messages.steps' -Key 'disk_already_template' -Default 'MiOS data disk already on {drive}: ({size_gb} GB, NTFS)') -replace '\{drive\}', $DriveLetter -replace '\{size_gb\}', $_sgb)
        return "${DriveLetter}:\"
    }
    if ($existing) {
        throw "Drive ${DriveLetter}: already exists with label '$($existing.FileSystemLabel)' -- pass a different -DriveLetter or remove the volume manually"
    }

    # 1. Locate C: partition + its disk
    $sysLetter = ([Environment]::GetEnvironmentVariable('SystemDrive')).TrimEnd(':')
    $cPart = Get-Partition -DriveLetter $sysLetter
    $supported = Get-PartitionSupportedSize -DriveLetter $sysLetter
    $shrinkBytes = [int64]$ShrinkMB * 1MB
    $newCSize = $cPart.Size - $shrinkBytes

    if ($shrinkBytes -gt ($cPart.Size - $supported.SizeMin)) {
        throw "Cannot shrink ${sysLetter}: by $ShrinkMB MB. Min partition size is $([math]::Round($supported.SizeMin/1GB,1)) GB; current $([math]::Round($cPart.Size/1GB,1)) GB; max shrinkable $([math]::Round(($cPart.Size-$supported.SizeMin)/1GB,1)) GB. Free space on ${sysLetter}: or move pagefile/hibernation file to allow more shrink."
    }

    # 2. Free space on disk after shrink (for new partition placement)
    $disk = Get-Disk -Number $cPart.DiskNumber
    if ($disk.PartitionStyle -ne 'GPT' -and $disk.PartitionStyle -ne 'MBR') {
        throw "Disk $($disk.Number) has unsupported partition style '$($disk.PartitionStyle)'"
    }

    # 3. Shrink C:
    Set-Step "Shrinking ${sysLetter}: $([math]::Round($cPart.Size/1GB,1))GB -> $([math]::Round($newCSize/1GB,1))GB ..."
    Resize-Partition -DriveLetter $sysLetter -Size $newCSize -ErrorAction Stop
    Log-Ok "${sysLetter}: shrunk by $ShrinkMB MB"

    # 4. Create new partition in freed space, exact size match
    Set-Step "Creating $VolumeLabel partition (${ShrinkMB}MB) on disk $($disk.Number)..."
    $newPart = New-Partition -DiskNumber $disk.Number -Size $shrinkBytes -DriveLetter $DriveLetter -ErrorAction Stop

    # 5. Format NTFS (host wrapper -- VHDX inside carries ext4)
    Format-Volume -DriveLetter $DriveLetter -FileSystem NTFS -NewFileSystemLabel $VolumeLabel `
        -AllocationUnitSize 4096 -Confirm:$false -Force | Out-Null
    Log-Ok "${DriveLetter}: created (${ShrinkMB}MB NTFS, label=$VolumeLabel) -- VHDX inside hosts ext4"

    return "${DriveLetter}:\"
}

function Set-PodmanMachineStorageOn {
    <#
    .SYNOPSIS
        Symlink ALL candidate podman-machine storage paths to
        $DataRoot\podman\machine BEFORE `podman machine init` runs,
        so MiOS-DEV's VHDX is created on the data disk from the start
        (no post-hoc move dance, no risk of leaving 100s of GBs of
        machine state on C:\).

    .NOTES
        Symlinks (mklink /D), NOT junctions (mklink /J). Verified
        empirically against podman 5.8.2 + WSL provider:

            /J -> `podman machine ls` FAILS with
                  "mkdir <path>: Cannot create a file when that file
                   already exists" (Go's os.Mkdir doesn't fall through
                  on EEXIST when the path is a junction)
            /D -> `podman machine ls` works, `init` works, files land
                  on the symlink target

        Idempotent: an existing correct symlink is left alone; an
        existing legacy junction is replaced.

        Covers all three default machineDir locations podman has used
        across versions:
          * %LOCALAPPDATA%\containers\podman\machine  (Windows-style)
          * %USERPROFILE%\.local\share\containers\podman\machine
            (Linux/XDG-style -- this is what podman 5.8.2 ACTUALLY
            uses on Windows; the reason the old single-path
            %LOCALAPPDATA% link did nothing useful)
          * %PROGRAMDATA%\containers\podman\machine  (machine-wide
            install fallback)

        Get-MiOS.ps1's Set-PodmanMachineStorageOnM does the same work
        before this function runs; this is a defensive idempotent
        re-run in case the operator launched build-mios.ps1
        directly.
    #>
    param([Parameter(Mandatory)][string]$DataRoot)

    $targetDir = Join-Path $DataRoot 'podman\machine'
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'containers\podman\machine'),
        (Join-Path $env:USERPROFILE  '.local\share\containers\podman\machine'),
        (Join-Path $env:PROGRAMDATA  'containers\podman\machine')
    )

    foreach ($defaultDir in $candidates) {
        if (-not $defaultDir) { continue }
        if (Test-Path $defaultDir) {
            $item = Get-Item $defaultDir -Force -ErrorAction SilentlyContinue
            if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                $current   = ($item.Target -join '').TrimStart('\??\')
                $isSymlink = $item.LinkType -eq 'SymbolicLink'
                if ($current -ieq $targetDir -and $isSymlink) {
                    Log-Ok "podman-machine storage already symlinked -> $targetDir ($defaultDir)"
                    continue
                }
                # Wrong target OR right target wrong link type (legacy
                # junction). Remove + relink as symlink below.
                if ($current -ieq $targetDir -and -not $isSymlink) {
                    Log-Warn "$defaultDir is a JUNCTION (legacy) -- recreating as symlink so podman 5.8.2 stops failing on os.Mkdir"
                }
                cmd /c "rmdir `"$defaultDir`"" 2>$null | Out-Null
            } else {
                # Real directory -- move children to target then remove.
                Set-Step "Migrating existing podman-machine state to $targetDir ..."
                Get-ChildItem $defaultDir -Force -ErrorAction SilentlyContinue |
                    Move-Item -Destination $targetDir -Force -ErrorAction SilentlyContinue
                Remove-Item $defaultDir -Force -Recurse -ErrorAction SilentlyContinue
            }
        } else {
            $parent = Split-Path $defaultDir -Parent
            if (-not (Test-Path $parent)) {
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
            }
        }

        # Create the symlink (mklink /D, NOT /J -- see .NOTES above).
        $rc = (cmd /c "mklink /D `"$defaultDir`" `"$targetDir`"" 2>&1)
        if ($LASTEXITCODE -eq 0) {
            Log-Ok "podman-machine storage symlinked $defaultDir -> $targetDir"
        } else {
            Log-Warn "mklink /D $defaultDir -> $targetDir failed: $rc"
        }
    }
}

function Get-PodmanMachineOsImage {
    [CmdletBinding()]
    param(
        # Default Repo + Tag resolve through mios.toml [image].machine_os_repo
        # / .machine_os_tag (SSOT). Hardcoded fallbacks below are vendor
        # defaults only -- operators bump the tag (6.0 -> 6.1) via mios.html.
        [string]$Repo = (Get-MiosTomlValue -Section 'image' -Key 'machine_os_repo' -Default 'quay.io/podman/machine-os'),
        [string]$Tag  = (Get-MiosTomlValue -Section 'image' -Key 'machine_os_tag'  -Default '6.1'),
        [string]$Architecture = 'x86_64',
        [string]$DiskType = 'wsl',
        [Parameter(Mandatory)] [string]$CacheDir
    )

    if (-not (Test-Path $CacheDir)) {
        New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    }

    $slash    = $Repo.IndexOf('/')
    $registry = $Repo.Substring(0, $slash)
    $name     = $Repo.Substring($slash + 1)
    $base     = "https://$registry/v2/$name"

    Set-Step "Resolving $Repo`:$Tag (OCI index)"
    $idxResp = Invoke-WebRequest -UseBasicParsing -Uri "$base/manifests/$Tag" `
        -Headers @{ 'Accept' = 'application/vnd.oci.image.index.v1+json' } `
        -ErrorAction Stop
    $idxJson = if ($idxResp.Content -is [byte[]]) {
        [System.Text.Encoding]::UTF8.GetString($idxResp.Content)
    } else { [string]$idxResp.Content }
    $index = $idxJson | ConvertFrom-Json
    if ($index.mediaType -notlike '*image.index*' -and $index.mediaType -notlike '*manifest.list*') {
        throw "Expected OCI image index at $Repo`:$Tag, got mediaType=$($index.mediaType)"
    }

    # -- Step 2: pick the platform manifest ----------------------------
    $pm = $index.manifests | Where-Object {
        $_.platform.architecture -eq $Architecture -and
        $_.annotations.disktype -eq $DiskType
    } | Select-Object -First 1
    if (-not $pm) {
        $available = ($index.manifests |
            ForEach-Object { "$($_.platform.architecture)/$($_.annotations.disktype)" }) -join ', '
        throw "No platform manifest for $Architecture/$DiskType in $Repo`:$Tag (available: $available)"
    }

    # -- Step 3: platform manifest -> single layer ---------------------
    # Same byte[]-vs-string trap as Step 1 -- decode explicitly.
    $pmResp = Invoke-WebRequest -UseBasicParsing -Uri "$base/manifests/$($pm.digest)" `
        -Headers @{ 'Accept' = 'application/vnd.oci.image.manifest.v1+json' } `
        -ErrorAction Stop
    $pmJson = if ($pmResp.Content -is [byte[]]) {
        [System.Text.Encoding]::UTF8.GetString($pmResp.Content)
    } else { [string]$pmResp.Content }
    $manifest = $pmJson | ConvertFrom-Json
    $layer = $manifest.layers | Select-Object -First 1
    if (-not $layer) {
        throw "Platform manifest $($pm.digest) has no layers"
    }

    $title = $layer.annotations.'org.opencontainers.image.title'
    if (-not $title) { $title = "$Architecture-$DiskType-$Tag.tar.zst" }
    $localPath      = Join-Path $CacheDir $title
    $expectedDigest = ($layer.digest -replace '^sha256:', '').ToLower()

    # -- Step 4: cache-hit short-circuit -------------------------------
    if (Test-Path $localPath) {
        $existingHash = (Get-FileHash -Path $localPath -Algorithm SHA256).Hash.ToLower()
        if ($existingHash -eq $expectedDigest) {
            Log-Ok "Reusing cached machine-os layer: $localPath"
            return $localPath
        }
        Log-Warn "Cached machine-os layer hash mismatch -- re-downloading"
        Remove-Item $localPath -Force -ErrorAction SilentlyContinue
    }

    # -- Step 5: streamed download via System.Net.Http (no RAM buffer) -
    $sizeMB  = [math]::Round($layer.size / 1MB, 1)
    Log-Ok "Downloading machine-os layer ($sizeMB MB) -> $localPath"
    $blobUrl = "$base/blobs/$($layer.digest)"
    $tmpPath = "$localPath.tmp"

    Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [System.TimeSpan]::FromMinutes(30)
    try {
        $req  = [System.Net.Http.HttpRequestMessage]::new('Get', $blobUrl)
        $resp = $client.SendAsync(
            $req,
            [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
        ).GetAwaiter().GetResult()
        if (-not $resp.IsSuccessStatusCode) {
            throw "HTTP $([int]$resp.StatusCode) fetching $blobUrl"
        }
        $stream = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $file   = [System.IO.File]::Create($tmpPath)
        try {
            $buf       = [byte[]]::new(1048576)  # 1 MiB chunks
            $total     = 0L
            $lastTickMB = -16L
            while (($n = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
                $file.Write($buf, 0, $n)
                $total += $n
                $totalMB = [int]($total / 1MB)
                if ($totalMB - $lastTickMB -ge 16) {
                    Set-Step "Downloading machine-os layer: $totalMB / $sizeMB MB"
                    Show-Dashboard
                    $lastTickMB = $totalMB
                }
            }
        } finally {
            $file.Dispose()
            $stream.Dispose()
        }
    } finally {
        $client.Dispose()
    }

    # -- Step 6: SHA256 verify -----------------------------------------
    Set-Step "Verifying machine-os layer SHA256"
    $actualHash = (Get-FileHash -Path $tmpPath -Algorithm SHA256).Hash.ToLower()
    if ($actualHash -ne $expectedDigest) {
        Remove-Item $tmpPath -Force -ErrorAction SilentlyContinue
        throw "machine-os layer SHA256 mismatch: expected $expectedDigest, got $actualHash"
    }

    Move-Item -Path $tmpPath -Destination $localPath -Force
    Log-Ok "machine-os layer staged: $localPath"
    return $localPath
}

function Move-PodmanWslDistroToM {
    param(
        [Parameter(Mandatory)] [string] $DistroName,
        # Default uses $script:MiosDistroDir which already centralizes
        # the distro-storage root across admin/user/data-disk modes
        # (set in build-mios.ps1's "Paths & constants" block + updated
        # by Update-MiosInstallPaths when M:\ comes online).
        [string] $TargetRoot = $(if ($script:MiosDistroDir) { $script:MiosDistroDir } else { 'M:\MiOS\distros' })
    )
    # podman prefixes its WSL distros with `podman-`. Resolve the actual
    # registered name (callers pass either form -- `MiOS-DEV` or
    # `podman-MiOS-DEV`). WSL distro names are case-sensitive in the
    # registry; iterate Lxss/ subkeys and match.
    $candidates = @($DistroName, "podman-$DistroName")
    $lxssRoot   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    if (-not (Test-Path $lxssRoot)) {
        Log-Warn "Move-PodmanWslDistroToM: WSL Lxss registry key missing -- skipping migration"
        return
    }
    $matched = $null
    foreach ($sub in (Get-ChildItem $lxssRoot -ErrorAction SilentlyContinue)) {
        $props = Get-ItemProperty $sub.PSPath -ErrorAction SilentlyContinue
        if (-not $props) { continue }
        $dn = $props.DistributionName
        if (-not $dn) { continue }
        if ($candidates -contains $dn) {
            $matched = [pscustomobject]@{
                DistributionName = $dn
                BasePath         = $props.BasePath
                RegPath          = $sub.PSPath
            }
            break
        }
    }
    if (-not $matched) {
        Log-Warn "Move-PodmanWslDistroToM: distro $DistroName not registered -- nothing to migrate"
        return
    }
    $current = ($matched.BasePath -replace '^\\\\\?\\','').TrimEnd('\')
    if ($current -match '^[Mm]:\\') {
        Log-Ok "podman-WSL distro $($matched.DistributionName) already on M:\ ($current) -- no migration needed"
        return
    }
    # Migrate.
    Set-Step "Migrating $($matched.DistributionName) WSL distro from $current onto M:\..."
    if (-not (Test-Path $TargetRoot)) {
        New-Item -ItemType Directory -Path $TargetRoot -Force -ErrorAction Stop | Out-Null
    }
    $newPath = Join-Path $TargetRoot $matched.DistributionName
    if (Test-Path $newPath) {
        # Stale dir from a previous failed migration -- safe to wipe
        # because the registered distro still points at $current.
        Log-Warn "Removing stale $newPath before re-import"
        Remove-Item -LiteralPath $newPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    & wsl.exe --shutdown 2>&1 | ForEach-Object { Write-Log "wsl-shutdown: $_" }
    $tmpTar = Join-Path $env:TEMP "mios-podman-migrate-$([guid]::NewGuid().ToString('N').Substring(0,8)).tar"
    try {
        Log-Ok "Exporting $($matched.DistributionName) -> $tmpTar"
        & wsl.exe --export $matched.DistributionName $tmpTar 2>&1 | ForEach-Object { Write-Log "wsl-export: $_" }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmpTar)) {
            Log-Warn "wsl --export $($matched.DistributionName) failed -- aborting M:\ migration"
            return
        }
        & wsl.exe --unregister $matched.DistributionName 2>&1 | ForEach-Object { Write-Log "wsl-unregister: $_" }
        Log-Ok "Re-importing $($matched.DistributionName) at $newPath"
        & wsl.exe --import $matched.DistributionName $newPath $tmpTar --version 2 2>&1 | ForEach-Object { Write-Log "wsl-import-M: $_" }
        if ($LASTEXITCODE -eq 0) {
            Log-Ok "podman-WSL distro $($matched.DistributionName) is now on M:\ ($newPath)"
        } else {
            Log-Warn "wsl --import to M:\ failed; falling back to original location"
            & wsl.exe --import $matched.DistributionName $current $tmpTar --version 2 2>&1 | ForEach-Object { Write-Log "wsl-import-fallback: $_" }
        }
    } finally {
        if (Test-Path $tmpTar) { Remove-Item $tmpTar -Force -ErrorAction SilentlyContinue }
    }
}

function New-BuilderDistro([hashtable]$HW) {
    Set-Step "Initializing $DevDistro ($($HW.Cpus) CPUs / $($HW.RamGB)GB / $($HW.DiskGB)GB disk)"
    if ((Test-Path 'M:\') -and -not $env:XDG_DATA_HOME) {
        $miosPodmanRoot = 'M:\podman'
        if (-not (Test-Path $miosPodmanRoot)) {
            New-Item -ItemType Directory -Path $miosPodmanRoot -Force | Out-Null
        }
        $env:XDG_DATA_HOME = $miosPodmanRoot
        Log-Ok "podman-machine state redirected to M:\podman (XDG_DATA_HOME)"
    }
    $ramMB = [math]::Max(4096, [math]::Min($HW.OsTotalRamMB - 512, $HW.RamGB * 1024))

    # Data disk + podman storage redirection happened earlier
    # (between Phase 1 and Phase 2). By the
    # time we reach Phase 3 the partition is provisioned and
    # CONTAINERS_STORAGE_CONF / podman.connections already point at
    # the data disk. $HW.DiskGB has also been clamped there.
    $diskGB = $HW.DiskGB

    $_tomlBase   = Get-MiosTomlValue -Section 'bootstrap.dev_vm' -Key 'base_image' -Default 'quay.io/podman/machine-os:6.1'
    if ($_tomlBase -match '^(.+):([^:]+)$') {
        $_tomlRepo = $Matches[1]; $_tomlTag = $Matches[2]
    } else {
        $_tomlRepo = $_tomlBase;  $_tomlTag = '6.1'
    }
    $machineTag = if ($env:MIOS_MACHINE_TAG) { $env:MIOS_MACHINE_TAG } else { $_tomlTag }
    $machineRepo = $_tomlRepo
    if ($MachineImage -match '^docker://(.+)$') {
        $ref = $matches[1]
        if ($ref -match '^(.+):([^:]+)$') {
            $machineRepo = $matches[1]
            $machineTag  = $matches[2]
        } elseif ($ref -match '^[^/]+/[^/]+/[^/]+$') {
            $machineRepo = $ref
        }
        $MachineImage = $null  # force re-resolution below
    }
    if ($machineRepo -eq 'quay.io/podman/machine-os' -and $machineTag -eq 'latest') {
        $podmanVersion = (& podman --version 2>$null) -join ' '
        if ($podmanVersion -match '(\d+)\.(\d+)') {
            $machineTag = "$($Matches[1]).$($Matches[2])"
            Log-Warn "machine-os has no latest tag; using installed Podman version tag $machineTag"
        } else {
            throw "machine-os:latest is unavailable and the installed Podman version could not be resolved"
        }
    }
    if (-not $MachineImage) {
        $machineCacheDir = Join-Path $script:MiosInstallDir 'machine-os'
        $MachineImage = $null
        $lastErr      = $null
        $delays       = @(Get-MiosTomlValue -Section 'network.retry' -Key 'delays_seconds' -Default @(0, 5, 15, 30))
        for ($i = 0; $i -lt $delays.Count; $i++) {
            if ($delays[$i] -gt 0) {
                Set-Step "Retry $i/$($delays.Count - 1) for $machineRepo`:$machineTag in $($delays[$i])s..."
                Start-Sleep -Seconds $delays[$i]
            }
            try {
                $MachineImage = Get-PodmanMachineOsImage `
                    -Repo $machineRepo `
                    -Tag  $machineTag `
                    -CacheDir $machineCacheDir
                break  # success
            } catch {
                $lastErr = $_
                $msg     = "$_"
                # 502/503/504/timeout = retryable. Anything else (404,
                # 401, parse error) = permanent, break out.
                if ($msg -notmatch '\b(50[234]|timed out|timeout|connection reset|connection refused|RemoteIO|temporarily)\b') {
                    Log-Warn "Pre-stage of $machineRepo`:$machineTag hit non-retryable error: $msg"
                    break
                }
                Log-Warn "Pre-stage attempt $($i+1) failed (retryable): $msg"
            }
        }
        if (-not $MachineImage) {
            Log-Warn "Pre-stage of $machineRepo`:$machineTag failed after retries: $lastErr"
            Log-Warn "Will let podman attempt its own pull (likely fails on this client if quay.io is still down)."
        }
    }

    $initSw = [System.Diagnostics.Stopwatch]::StartNew()
    $initOut = [System.Collections.Generic.List[string]]::new()
    if ($MachineImage) {
        Log-Ok "Provisioning MiOS-DEV from machine image: $MachineImage"
    } else {
        Log-Ok "Provisioning MiOS-DEV using podman's bundled default machine image"
    }
    $initArgs = @(
        'machine', 'init', $BuilderDistro,
        '--cpus',      $HW.Cpus,
        '--memory',    $ramMB,
        '--disk-size', $diskGB,
        '--rootful',
        '--update-connection',
        '--now'
    )
    if ($MachineImage) {
        $initArgs += @('--image', $MachineImage)
    }
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & podman @initArgs 2>&1 | ForEach-Object {
            Write-Log "podman-init: $_"
            $initOut.Add([string]$_) | Out-Null
            if ($initSw.ElapsedMilliseconds -ge 150) {
                $clean = ($_ -replace '\x1b\[[0-9;]*[mGKHFJ]','').Trim()
                if ($clean) { $script:CurStep = $clean.Substring(0,[math]::Min($clean.Length,80)) }
                Show-Dashboard
                $initSw.Restart()
            }
        }
    }
    $initRc      = $LASTEXITCODE
    $initJoined  = ($initOut -join " ")

    if ($initRc -ne 0 -and $MachineImage `
            -and $initJoined -match '(?i)failed to pull|cannot find the path specified') {
        Log-Warn "podman machine init failed to pull $MachineImage on this client."
        Log-Warn "Falling back to podman's bundled default machine-os image."
        Log-Warn "To get the pinned image, upgrade your podman client: winget upgrade Podman.Podman"

        # Strip --image from the arg list and retry.
        $fallbackArgs = @($initArgs | Where-Object { $_ -ne '--image' -and $_ -ne $MachineImage })
        $fallbackOut = [System.Collections.Generic.List[string]]::new()
        & {
            $ErrorActionPreference = 'Continue'
            if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                $PSNativeCommandUseErrorActionPreference = $false
            }
            & podman @fallbackArgs 2>&1 | ForEach-Object {
                Write-Log "podman-init-fallback: $_"
                $fallbackOut.Add([string]$_) | Out-Null
                $clean = ($_ -replace '\x1b\[[0-9;]*[mGKHFJ]','').Trim()
                if ($clean) { $script:CurStep = $clean.Substring(0,[math]::Min($clean.Length,80)) }
                Show-Dashboard
            }
        }
        $initRc = $LASTEXITCODE
        $initJoined = (($initOut + $fallbackOut) -join " ")
        if ($initRc -eq 0) {
            Log-Ok "$BuilderDistro initialized via bundled-default fallback"
        }
    }

    if ($initRc -ne 0) {
        if ($initJoined -match '(?i)already exists|vm.*already exists') {
            Log-Warn "podman machine init: $BuilderDistro already exists -- starting instead"
            $startOut = [System.Collections.Generic.List[string]]::new()
            & {
                $ErrorActionPreference = 'Continue'
                if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                    $PSNativeCommandUseErrorActionPreference = $false
                }
                & podman machine start $BuilderDistro 2>&1 | ForEach-Object {
                    Write-Log "podman-recover-start: $_"
                    $startOut.Add([string]$_) | Out-Null
                }
            }
            $startJoined = ($startOut -join " ")
            if ($startJoined -match '(?i)already running') {
                Log-Ok "$BuilderDistro is already running"
            } elseif ($LASTEXITCODE -eq 0) {
                Log-Ok "$BuilderDistro started"
            } else { throw "Existing builder start failed (exit $LASTEXITCODE); preserving machine registration, distribution and storage. $startJoined" }
        } else {
            throw "podman machine init failed (exit $initRc): $(($initOut | Select-Object -Last 3) -join ' / ')"
        }
    }
    $null = Invoke-NativeQuiet { podman machine set --default $BuilderDistro }
    Log-Ok "$DevDistro ready as default Podman machine"

    if ((Test-Path 'M:\') -and ($env:MIOS_FORCE_VHDX_MIGRATE -in @('1','true','TRUE','yes'))) {
        try {
            Move-PodmanWslDistroToM -DistroName $BuilderDistro
        } catch {
            Log-Warn "podman-WSL distro M:\ migration: $_"
        }
    } else {
        Log-Ok "podman-WSL distro $BuilderDistro left in-place (junction redirect handles M:\ placement; set MIOS_FORCE_VHDX_MIGRATE=1 to force export-unregister-import)"
    }

    # Rootful machine-os distros are not accessible via wsl.exe or podman machine ssh.
    # Build runs from the Windows Podman client via the machine's API -- no exec needed.
    # Just verify the API is up (it should be immediately after --now).
    Set-Step "Verifying $DevDistro Podman API..."
    $deadline = (Get-Date).AddSeconds(90)
    $apiOk = $false
    $lastState = ''
    while ((Get-Date) -lt $deadline) {
        try {
            $stateOut = & podman machine inspect $BuilderDistro --format '{{.State}}' 2>$null
            $lastState = ($stateOut | Select-Object -First 1) -as [string]
            if ($lastState) { $lastState = $lastState.Trim() }
        } catch { $lastState = '' }
        if ($lastState -eq 'running') { $apiOk = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $apiOk) {
        throw "$BuilderDistro not in running state after 90 s (last seen: '$lastState') -- check: podman machine ls"
    }
    Log-Ok "$DevDistro Podman API ready"
    # Overlay seed is invoked once at end of Phase 3 (covers both the
    # newly-created path and the already-running path); see the call
    # site directly above End-Phase 3 in the main flow.
}

function Invoke-MiosOverlaySeed {
    if ($env:MIOS_FORCE_LEGACY_PACKAGES_MD -ne '1') {
        return
    }
    Log-Warn "MIOS_FORCE_LEGACY_PACKAGES_MD=1 -- running deprecated mios.toml overlay seed (you are off the canonical path)"
    Set-Step "Seeding MiOS package overlay onto $DevDistro (LEGACY)..."
    # Updated path: check mios.toml instead of PACKAGES.md.
    $tomlPath = Join-Path $MiosRepoDir "mios.toml"
    if (-not (Test-Path $tomlPath)) {
        $tomlPath = Join-Path $MiosRepoDir "usr\share\mios\mios.toml"
    }
    if (-not (Test-Path $tomlPath)) {
        Log-Warn "mios.toml not found in either canonical location -- legacy overlay seed skipped"
        return
    }
    $wslDistro = "podman-$DevDistro"

    # Confirm the distro is reachable via wsl.exe (rootful machines on
    # newer Podman builds register as podman-<Name>; older builds may
    # register without prefix -- try both).
    $sshOk = $false
    foreach ($candidate in @($wslDistro, $DevDistro)) {
        $probe = (& wsl.exe -d $candidate --exec bash -c "echo ok" 2>$null) -join ""
        if ($probe.Trim() -eq "ok") { $wslDistro = $candidate; $sshOk = $true; break }
    }
    if (-not $sshOk) {
        Log-Warn "Cannot wsl.exe into $DevDistro -- overlay seed deferred to first manual run"
        return
    }

    # Stage the highest-precedence mios.toml + the overlay installer inside
    # the distro's /tmp. Using `wsl --exec cp` from the Windows path avoids
    # podman-machine-cp's rootful permission quirks.
    # The bash overlay reads [packages.dev_overlay].sections from mios.toml.
    $drive = $tomlPath.Substring(0,1).ToLower()
    $tomlWslPath = "/mnt/$drive" + ($tomlPath.Substring(2) -replace '\\','/')

    $overlayScript = @'
#!/usr/bin/env bash
# mios-overlay.sh -- live system overlay seeder for MiOS-DEV.
# Generated by build-mios.ps1 / Invoke-MiosOverlaySeed.
set -uo pipefail

SENTINEL="/var/lib/mios/.overlay-seeded"
SRC_TOML="${SRC_TOML:-/tmp/mios.toml}"
LOG_DIR="/tmp/mios-overlay-logs"
mkdir -p "$LOG_DIR" && chmod 0777 "$LOG_DIR"

# Skip if already seeded and mios.toml is older than the sentinel.
if [[ -f "$SENTINEL" && "$SENTINEL" -nt "$SRC_TOML" ]]; then
    echo "[mios-overlay] sentinel newer than mios.toml -> skip"
    exit 0
fi

# Normalize CRLF (OneDrive-synced source).
TOML_LF="/tmp/mios.lf.toml"
tr -d '\r' < "$SRC_TOML" > "$TOML_LF"

DEFAULT_SECTIONS=(
    base security utils build-toolchain containers
    cockpit storage virt
    gpu-mesa gpu-nvidia gpu-amd-compute gpu-intel-compute
    gnome-flatpak-runtime
    ai sbom-tools self-build network-discovery updater
    cockpit-plugins-build k3s-selinux-build uki
)

# Naive TOML scrape: pull the array under [packages.dev_overlay].sections
# (or [packages].dev_overlay.sections inline form). Tolerates the
# single-line + multi-line array shapes the configurator emits.
parse_sections_from_toml() {
    [[ -r "$TOML_LF" ]] || return 1
    awk '
        /^\[packages\.dev_overlay\][[:space:]]*$/ { in_block=1; next }
        in_block && /^\[/                        { in_block=0; next }
        in_block && /^[[:space:]]*sections[[:space:]]*=/ {
            sub(/^[^=]*=[[:space:]]*/, "", $0); collecting=1
        }
        collecting {
            print
            if ($0 ~ /\]/) { collecting=0 }
        }
    ' "$TOML_LF" \
        | tr -d '[]\n' \
        | tr ',' '\n' \
        | sed -E 's/^[[:space:]]*"?([^"#]*)"?[[:space:]]*$/\1/' \
        | sed '/^$/d'
}

mapfile -t SECTIONS < <(parse_sections_from_toml || true)
SECTIONS_SOURCE="mios.toml [packages.dev_overlay]"
if (( ${#SECTIONS[@]} == 0 )); then
    SECTIONS=("${DEFAULT_SECTIONS[@]}")
    SECTIONS_SOURCE="hardcoded minimal default"
fi
echo "[mios-overlay] sections (${#SECTIONS[@]}, from ${SECTIONS_SOURCE}): ${SECTIONS[*]}"

get_pkgs() {
    local category="$1"
    awk -v section="packages.${category}" '
        /^\[/ {
            in_section = 0
            collecting = 0
            line = $0
            sub(/^\[/, "", line); sub(/\][[:space:]]*$/, "", line)
            gsub(/[[:space:]]/, "", line)
            if (line == section) in_section = 1
            next
        }
        in_section && /^[[:space:]]*pkgs[[:space:]]*=/ {
            sub(/^[^=]*=[[:space:]]*/, "", $0)
            collecting = 1
        }
        collecting {
            print
            if ($0 ~ /\][[:space:]]*$/) { collecting = 0 }
        }
    ' "$TOML_LF" \
        | tr -d '[]' \
        | tr ',' '\n' \
        | sed -E "s/[[:space:]]*\"([^\"]*)\"[[:space:]]*\$/\\1/" \
        | sed '/^[[:space:]]*$/d' \
        | sed -E 's/[[:space:]]*#.*$//'
}

# Add Fedora-version-pinned RPMFusion (free + nonfree).
fedver=$(rpm -E %fedora 2>/dev/null || echo 43)
sudo env SYSTEMD_OFFLINE=1 dnf5 install -y --skip-unavailable \
    "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${fedver}.noarch.rpm" \
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${fedver}.noarch.rpm" \
    >"$LOG_DIR/00-rpmfusion.log" 2>&1 || true

ALWAYS_SKIP_RE='^(kernel|boot|moby|bloat|critical)$'

install_section() {
    local sec="$1"
    [[ "$sec" =~ $ALWAYS_SKIP_RE ]] && { echo "[mios-overlay] SKIP $sec (always-skipped)"; return; }
    local pkgs
    pkgs=$(get_pkgs "$sec" | tr '\n' ' ')
    [[ -z "${pkgs// }" ]] && { echo "[mios-overlay] EMPTY $sec"; return; }
    echo "[mios-overlay] INSTALL $sec"
    # shellcheck disable=SC2086
    sudo env SYSTEMD_OFFLINE=1 dnf5 install -y --skip-unavailable --skip-broken --allowerasing \
        $pkgs >"$LOG_DIR/$sec.log" 2>&1
    # RPM scriptlet presets must operate on unit files without contacting the
    # WSL machine's transitional systemd bus (SYSTEMD_OFFLINE survives sudo).
}

# Foundation (repos must be first), then user-selected sections.
install_section repos
for sec in "${SECTIONS[@]}"; do
    [[ "$sec" == "repos" ]] && continue
    install_section "$sec"
done

# Critical safe-subset (skip kernel-core/gdm/libvirt on WSL).
echo "[mios-overlay] INSTALL critical (WSL-safe subset)"
sudo env SYSTEMD_OFFLINE=1 dnf5 install -y --skip-unavailable --skip-broken --allowerasing \
    bootc chrony cockpit firewalld NetworkManager pipewire tuned \
    >"$LOG_DIR/critical.log" 2>&1 || true

sudo install -d -m 0755 /var/lib/mios
sudo touch "$SENTINEL"

sudo install -d -m 0755 /usr/local/bin
sudo install -m 0755 /tmp/mios-overlay.sh /usr/local/bin/mios-dev-seed

# Drop a profile.d hint so `wsl -d podman-MiOS-DEV` greets the operator
# with the dev-VM context. Quiet for non-interactive shells.
sudo tee /etc/profile.d/mios-dev-motd.sh >/dev/null <<'PROFILE'
# MiOS-DEV operator hint -- only on interactive shells.
if [[ -n "${PS1-}" && -t 1 ]]; then
    pkgs=$(rpm -qa | wc -l 2>/dev/null || echo ?)
    echo "MiOS-DEV (Podman-WSL2 dev VM, $pkgs pkgs)  --  refresh: sudo mios-dev-seed"
fi
PROFILE
sudo chmod 0644 /etc/profile.d/mios-dev-motd.sh

echo "[mios-overlay] done -- $(rpm -qa | wc -l) packages installed"
echo "[mios-overlay] manual refresh: sudo mios-dev-seed"
'@

    $overlayScript = $overlayScript -replace "`r`n", "`n" -replace "`r", "`n"
    $b64Script = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($overlayScript))
    $stage = "set -e; sudo install -d -m 0777 /tmp; " +
             "echo '$b64Script' | base64 -d > /tmp/mios-overlay.sh && chmod +x /tmp/mios-overlay.sh; " +
             "cp '$tomlWslPath' /tmp/mios.toml; " +
             "/tmp/mios-overlay.sh"
    & wsl.exe -d $wslDistro --exec bash -c $stage 2>&1 | ForEach-Object { Write-Log "overlay-seed: $_" }
    if ($LASTEXITCODE -ne 0) {
        Log-Warn "overlay seed exited rc=$LASTEXITCODE -- partial install possible (packages may still be present; rerun safe)"
    } else {
        Log-Ok "MiOS package overlay seeded into $DevDistro"
    }
}

function Invoke-MiosQuadletOverlay {
    if ($env:MIOS_SKIP_DEV_QUADLETS -in @('1','true','TRUE','yes')) {
        Log-Warn "MIOS_SKIP_DEV_QUADLETS set -- Quadlet overlay skipped"
        return
    }

    Set-Step "Overlaying MiOS Quadlets + systemd units onto $DevDistro..."

    # Per the directive "M:\ IS git", mios.git is overlaid AT
    # $MiosRepoDir root, not at $MiosRepoDir\mios subdir.
    $miosRoot = $MiosRepoDir
    if (-not (Test-Path (Join-Path $miosRoot "Containerfile"))) {
        Log-Warn "mios.git overlay missing at $miosRoot (no Containerfile) -- Quadlet overlay skipped"
        return
    }
    function _ProbeWslAlive {
        param([string]$Distro, [int]$TimeoutMs = 8000)
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName  = 'wsl.exe'
        $psi.Arguments = "-d $Distro --exec bash -c `"echo ok`""
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow  = $true
        try {
            $proc = [System.Diagnostics.Process]::Start($psi)
        } catch { return $false }
        if (-not $proc.WaitForExit($TimeoutMs)) {
            try { $proc.Kill() } catch {}
            return $false
        }
        $stdout = $proc.StandardOutput.ReadToEnd().Trim()
        return ($stdout -eq 'ok')
    }
    $wslDistro = "podman-$DevDistro"
    $sshOk = $false
    foreach ($candidate in @($wslDistro, $DevDistro)) {
        if (_ProbeWslAlive -Distro $candidate -TimeoutMs 8000) {
            $wslDistro = $candidate; $sshOk = $true; break
        }
    }
    if (-not $sshOk) {
        Log-Warn "wsl.exe probe into $DevDistro timed out at 8s -- install-time Quadlet overlay skipped."
        Log-Warn "  The mios-build-driver / bootc switch path still delivers the SAME Quadlets via the OCI image,"
        Log-Warn "  so MiOS-DEV will reach full-parity (MiOS-DEV == MiOS) after the build phase regardless."
        return
    }

    # Convert C:\path\to\mios -> /mnt/c/path/to/mios for the WSL side.
    # Trim trailing backslash so M:\ -> /mnt/m (no trailing slash, which
    # would produce /mnt/m/ and break sentinel comparisons in the seed
    # script that compare against $1).
    $miosRootTrimmed = $miosRoot.TrimEnd('\')
    $drive = $miosRootTrimmed.Substring(0,1).ToLower()
    $miosRootWsl = if ($miosRootTrimmed.Length -le 2) {
        "/mnt/$drive"     # bare drive root e.g. M:\ -> /mnt/m
    } else {
        "/mnt/$drive" + ($miosRootTrimmed.Substring(2) -replace '\\','/')
    }

    $enableAi     = if ($env:MIOS_DEV_ENABLE_AI     -in @('1','true','TRUE','yes')) { '1' } else { '0' }
    $enableRunner = if ($env:MIOS_DEV_ENABLE_RUNNER -in @('1','true','TRUE','yes')) { '1' } else { '0' }

    $overlayScript = @'
#!/usr/bin/env bash
# mios-quadlet-overlay.sh -- mirror MiOS FHS overlay into MiOS-DEV.
# Generated by build-mios.ps1 / Invoke-MiosQuadletOverlay.
set -uo pipefail

SRC="${1:?source mios.git path required}"
SENTINEL="/var/lib/mios/.quadlet-overlay-seeded"

# Skip if sentinel is newer than the source mios.git's Containerfile
# (cheap proxy for "has the source tree changed since last overlay").
if [[ -f "$SENTINEL" && "$SENTINEL" -nt "$SRC/Containerfile" ]]; then
    echo "[quadlet-overlay] sentinel newer than mios.git -> skip"
    exit 0
fi

echo "[quadlet-overlay] making / a git working tree of mios.git ($SRC) ..."

ORIGIN_URL="${MIOS_GIT_ORIGIN:-https://github.com/mios-dev/MiOS.git}"
ORIGIN_BRANCH="${MIOS_GIT_BRANCH:-main}"
CACHE_DIR="${MIOS_GIT_CACHE:-/var/lib/mios/git/mios.git}"

sudo mkdir -p "$(dirname "$CACHE_DIR")"

# Mark `/` AND the cache as safe git directories -- root-owned `.git`
# triggers "dubious ownership" rejection when non-root users later
# inspect state (`git -C / log`, dashboard's git panel, etc.).
sudo git config --system --add safe.directory / 2>/dev/null || \
    sudo git config --global --add safe.directory /
sudo git config --system --add safe.directory "$CACHE_DIR" 2>/dev/null || \
    sudo git config --global --add safe.directory "$CACHE_DIR"

# -- Phase A: ensure native bare-clone cache exists + is fresh ----------------
cache_state=missing
if [[ -d "$CACHE_DIR/objects" ]]; then
    cache_state=present
fi

if [[ "$cache_state" = present ]]; then
    echo "[overlay] refreshing native cache: $CACHE_DIR (origin=$ORIGIN_URL)"
    if ! timeout 60 sudo git -C "$CACHE_DIR" fetch --depth=1 origin "$ORIGIN_BRANCH" 2>&1 | tail -3; then
        echo "[overlay] WARN: cache fetch failed (or timed out) -- proceeding with stale cache"
    fi
else
    # Cold cache. Try direct origin clone first (network-only path; pure
    # ext4 destination, no DrvFs round-trips). Both probe + clone are
    # bounded by `timeout` so a hung DNS / unreachable proxy can't stall
    # the whole bootstrap; the /mnt/m fallback below is the offline path.
    cache_populated=0
    if timeout 10 git ls-remote --exit-code --heads "$ORIGIN_URL" "$ORIGIN_BRANCH" >/dev/null 2>&1; then
        echo "[overlay] populating native cache via direct clone of $ORIGIN_URL"
        if timeout 120 sudo git clone --bare --depth=1 --branch="$ORIGIN_BRANCH" "$ORIGIN_URL" "$CACHE_DIR" 2>&1 | tail -3; then
            cache_populated=1
        else
            echo "[overlay] WARN: direct clone failed (or timed out at 2 min); falling back to $SRC bootstrap"
        fi
    else
        echo "[overlay] origin $ORIGIN_URL unreachable (probe timed out); falling back to $SRC bootstrap"
    fi

    # Fallback: bootstrap from the operator-side mios.git checkout (one-shot
    # DrvFs read; cache then operates on native ext4 forever after).
    if [[ $cache_populated -eq 0 ]] && [[ -d "$SRC/.git" ]]; then
        echo "[overlay] bootstrap-cloning native cache from $SRC (one-shot)"
        if sudo git clone --bare --depth=1 --branch="$ORIGIN_BRANCH" "$SRC" "$CACHE_DIR" 2>&1 | tail -3; then
            sudo git -C "$CACHE_DIR" remote set-url origin "$ORIGIN_URL"
            cache_populated=1
        fi
    fi

    if [[ $cache_populated -eq 0 ]]; then
        echo "[overlay] FATAL: no source for mios.git cache (origin unreachable AND $SRC/.git missing)"
        exit 1
    fi
fi

# Preserve locally edited deployed files. The verified image pipeline performs
# the whole-system update; this optional live overlay must never discard them.
if sudo git -C / rev-parse --verify HEAD >/dev/null 2>&1; then
    if ! root_changes=$(sudo git -C / status --porcelain --untracked-files=no 2>&1); then
        echo "[overlay] ERROR: cannot inspect existing root checkout" >&2
        exit 1
    fi
    if [[ -n "$root_changes" ]]; then
        echo "[overlay] preserving modified root overlay; full image build remains required"
        exit 0
    fi
fi

# -- Phase B: ensure / is a git working tree pointing at the native cache -----
echo "[overlay] making / a git working tree of mios.git ($CACHE_DIR)"
sudo git -C / init -b "$ORIGIN_BRANCH" 2>&1 | head -1 || true
sudo git -C / config --bool core.fileMode false
sudo git -C / config --bool core.autocrlf false
sudo git -C / config --bool core.symlinks true
sudo git -C / remote remove origin 2>/dev/null || true
sudo git -C / remote add origin "$CACHE_DIR"

# -- Phase C: fast-forward an existing clean root; preserve all local edits -----
echo "[overlay] git -C / fetch origin $ORIGIN_BRANCH (from native cache) ..."
fetch_out=$(sudo git -C / fetch --depth=1 origin "$ORIGIN_BRANCH" 2>&1)
fetch_rc=$?
echo "$fetch_out" | tail -3
if [[ $fetch_rc -ne 0 ]]; then
    echo "[overlay] ERROR: git fetch failed (rc=$fetch_rc)"
fi
if [[ $fetch_rc -ne 0 ]]; then exit "$fetch_rc"; fi
if sudo git -C / rev-parse --verify HEAD >/dev/null 2>&1; then
    sudo git -C / merge --ff-only FETCH_HEAD || exit 1
else
    sudo git -C / checkout --detach FETCH_HEAD || exit 1
fi

count=$(sudo git -C / ls-tree -r --name-only HEAD 2>/dev/null | wc -l)
echo "[quadlet-overlay] / now contains $count tracked mios.git files"
echo "[quadlet-overlay] / HEAD: $(sudo git -C / rev-parse --short HEAD 2>/dev/null)"

# Restore the executable bit on MiOS scripts. mios.git is authored on Windows
# where git core.filemode is off, so the checkout to / lands libexec/bin scripts
# as 0644 -- systemd ExecStart then 203/EXECs "Permission denied" (
# hermes-agent + cdi-detect + every firstboot failed this way). chmod +x the
# script trees; data files (py/json/yaml/md) stay untouched.
echo "[quadlet-overlay] restoring +x on MiOS scripts (Windows git checkout drops it)"
sudo chmod -R +x /usr/libexec/mios/ 2>/dev/null || true
sudo find /usr/lib/mios -type f \( -name "*.sh" -o -name "mios-*" \) ! -name "*.py" ! -name "*.json" ! -name "*.yaml" ! -name "*.md" -exec chmod +x {} + 2>/dev/null || true
sudo find /usr/bin /usr/local/bin -maxdepth 1 -name "mios-*" -type f -exec chmod +x {} + 2>/dev/null || true

sudo install -d -m 0755 /usr/lib/systemd/system/multi-user.target.wants 2>/dev/null || true
sudo ln -sf ../mios-ai-firstboot.service /usr/lib/systemd/system/multi-user.target.wants/mios-ai-firstboot.service 2>/dev/null \
    && echo "[quadlet-overlay] mios-ai-firstboot enabled via .wants symlink (runs on first boot)" \
    || echo "[quadlet-overlay] WARN: could not symlink mios-ai-firstboot.service"

sudo install -d -m 0755 /etc/systemd/user/default.target.wants 2>/dev/null || true
sudo ln -sf /usr/lib/systemd/user/mios-launcher.service /etc/systemd/user/default.target.wants/mios-launcher.service 2>/dev/null \
    && echo "[quadlet-overlay] mios-launcher (OS-control broker) enabled via global user .wants symlink" \
    || echo "[quadlet-overlay] WARN: could not symlink mios-launcher.service"

# Sanity: the smoke test expects /usr/share/mios. If git reset
# succeeded but the dir isn't there, surface that loudly so we
# don't silently ship a half-applied overlay.
if [[ ! -d /usr/share/mios ]]; then
    echo "[quadlet-overlay] ERROR: /usr/share/mios still missing after git reset"
    echo "[quadlet-overlay]   tracked usr/share/mios entries in HEAD:"
    sudo git -C / ls-tree -r --name-only HEAD 2>/dev/null | grep '^usr/share/mios/' | head -5 || true
    echo "[quadlet-overlay]   filesystem state of /usr/share:"
    ls -ld /usr/share/mios 2>&1 || true
    ls -la /usr/share/ 2>&1 | head -10 || true
fi

sudo ln -sf usr/share/mios/mios.toml             /mios.toml             2>/dev/null || true
sudo ln -sf usr/share/mios/configurator/mios.html /configurator.html  2>/dev/null || true
echo "[quadlet-overlay] root symlinks: /mios.toml, /configurator.html"

if [[ -r /tools/lib/userenv.sh ]]; then
    sudo install -D -m 0755 /tools/lib/userenv.sh /usr/lib/mios/userenv.sh \
        && echo "[quadlet-overlay] deployed env-bridge resolver -> /usr/lib/mios/userenv.sh"
fi
if [[ -x /usr/libexec/mios/system-sync-env.sh ]]; then
    echo "[quadlet-overlay] generating /etc/mios/install.env via mios-sync-env"
    sudo /usr/libexec/mios/system-sync-env.sh 2>&1 | sed 's/^/[quadlet-overlay]   /' || \
        echo "[quadlet-overlay] WARN: mios-sync-env exited non-zero (install.env may be stale)"
fi

# Render failure is fatal (exit 3, thrown by the caller): a unit started with an unrendered ${MIOS_*} reads it literally (T-1133).
[[ -x /automation/34-render-quadlets.sh ]] || { echo "[quadlet-overlay] ERROR: /automation/34-render-quadlets.sh not found"; exit 3; }
echo "[quadlet-overlay] rendering Quadlet \${MIOS_*} placeholders via automation/34-render-quadlets.sh"
sudo /automation/34-render-quadlets.sh 2>&1 | sed 's/^/[quadlet-overlay]   /' || \
    { echo "[quadlet-overlay] ERROR: 34-render-quadlets.sh failed; no unit was started"; exit 3; }

SYSTEMD_PID=$(pidof systemd 2>/dev/null | tr ' ' '\n' | head -1)
if [[ -n "$SYSTEMD_PID" ]]; then
    NS="sudo nsenter -t $SYSTEMD_PID -a"
    echo "[quadlet-overlay] entering systemd ns (PID $SYSTEMD_PID) for systemctl calls"
else
    NS="sudo"
    echo "[quadlet-overlay] WARN: systemd PID not found -- systemctl calls may fail"
fi

echo "[quadlet-overlay] realizing sysusers / tmpfiles / daemon-reload ..."
$NS systemd-sysusers 2>&1 | tail -3 || true
$NS systemd-tmpfiles --create 2>&1 | tail -3 || true
$NS systemctl daemon-reload 2>&1 | tail -3 || true

echo "[quadlet-overlay] setting wsl.conf [boot].systemd=true + [user].default=mios"
if ! grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then
    printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
    echo "[quadlet-overlay] /etc/wsl.conf: appended [boot] systemd=true"
elif ! grep -qE '^[[:space:]]*systemd[[:space:]]*=[[:space:]]*true[[:space:]]*$' /etc/wsl.conf 2>/dev/null; then
    if grep -qE '^[[:space:]]*systemd[[:space:]]*=' /etc/wsl.conf 2>/dev/null; then
        sudo sed -i 's|^[[:space:]]*systemd[[:space:]]*=.*|systemd=true|' /etc/wsl.conf
        echo "[quadlet-overlay] /etc/wsl.conf: rewrote systemd=<other> to systemd=true under [boot]"
    else
        sudo sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf
        echo "[quadlet-overlay] /etc/wsl.conf: inserted systemd=true under existing [boot]"
    fi
else
    echo "[quadlet-overlay] /etc/wsl.conf: [boot] systemd=true already set"
fi
if id mios >/dev/null 2>&1; then
    if ! grep -q '^\[user\]' /etc/wsl.conf 2>/dev/null; then
        printf '\n[user]\ndefault=mios\n' | sudo tee -a /etc/wsl.conf >/dev/null
        echo "[quadlet-overlay] /etc/wsl.conf: appended [user] default=mios"
    elif ! grep -qE '^[[:space:]]*default[[:space:]]*=' /etc/wsl.conf 2>/dev/null; then
        sudo sed -i '/^\[user\]/a default=mios' /etc/wsl.conf
        echo "[quadlet-overlay] /etc/wsl.conf: inserted default=mios under existing [user]"
    elif ! grep -qE '^[[:space:]]*default[[:space:]]*=[[:space:]]*mios[[:space:]]*$' /etc/wsl.conf 2>/dev/null; then
        sudo sed -i 's|^[[:space:]]*default[[:space:]]*=.*|default=mios|' /etc/wsl.conf
        echo "[quadlet-overlay] /etc/wsl.conf: rewrote default=<other> to default=mios"
    else
        echo "[quadlet-overlay] /etc/wsl.conf: default=mios already set"
    fi
else
    echo "[quadlet-overlay] WARN: mios user not found after sysusers; skipping wsl.conf default-user write"
fi

echo "[quadlet-overlay] container-host prerequisites for mios user ..."
if id mios >/dev/null 2>&1; then
    if ! grep -q '^mios:' /etc/subuid 2>/dev/null; then
        echo 'mios:524288:65536' | sudo tee -a /etc/subuid >/dev/null
        echo "[quadlet-overlay]   /etc/subuid: mios:524288:65536"
    fi
    if ! grep -q '^mios:' /etc/subgid 2>/dev/null; then
        echo 'mios:524288:65536' | sudo tee -a /etc/subgid >/dev/null
        echo "[quadlet-overlay]   /etc/subgid: mios:524288:65536"
    fi
    if command -v loginctl >/dev/null 2>&1; then
        sudo loginctl enable-linger mios 2>/dev/null || true
        echo "[quadlet-overlay]   loginctl enable-linger mios"
    fi
    sudo install -d -m 0755 /var/home 2>/dev/null || true
    sudo install -d -m 0755 -o mios -g mios /var/home/mios 2>/dev/null || \
        sudo install -d -m 0755 /var/home/mios
    if [[ -d /etc/skel ]]; then
        sudo cp -an /etc/skel/. /var/home/mios/ 2>/dev/null || true
        sudo chown -R mios:mios /var/home/mios 2>/dev/null || true
        echo "[quadlet-overlay]   /var/home/mios reconciled against /etc/skel (cp -an, idempotent)"
    fi
fi

if [[ -d /var/lib/flatpak/exports/share/applications ]]; then
    for _df in /var/lib/flatpak/exports/share/applications/*.desktop; do
        [[ -f "$_df" ]] || continue
        _base=$(basename "$_df")
        if [[ ! -e "/usr/share/applications/$_base" ]]; then
            sudo ln -sf "$_df" "/usr/share/applications/$_base" 2>/dev/null
            echo "[quadlet-overlay]   linked flatpak desktop: $_base"
        fi
    done
    sudo update-desktop-database /usr/share/applications/ 2>/dev/null || true
fi

NATIVE_SET=(cockpit.socket mios-cdi-detect.service nvidia-cdi-refresh.path)

QUADLET_AUTOSTART=( __MIOS_QUADLET_AUTOSTART__ )
QUADLET_OPTIN=( __MIOS_QUADLET_OPTIN__ )

# Daemon-reload so the Quadlet generator regenerates units from the
# latest .container files in /etc/containers/systemd/ +
# /usr/share/containers/systemd/ -- the bootc-deployed root may
# carry newer ones than the live systemd state.
$NS systemctl daemon-reload 2>&1 | grep -vE 'created symlink' || true

# Belt-and-suspenders reload, then enable each unit DIRECTLY. The previous
# `list-unit-files | grep` gate gave a FALSE "not present" for units checked out
# in this same overlay pass (mios-ai-firstboot.service was on disk +
# /usr/lib/systemd/system writable, but the gate skipped it so the AI never
# auto-provisioned). enable is the authoritative existence check.
$NS systemctl daemon-reload 2>/dev/null || true
for svc in "${NATIVE_SET[@]}"; do
    # timeout 30: belt-and-suspenders so a single unit that blocks on a
    # transitional bus can never wedge the whole install (long oneshots like
    # mios-ai-firstboot must NOT be in NATIVE_SET -- see note above). A unit that
    # exceeds 30s is treated as a non-fatal skip; its Quadlet Restart= / the
    # first-boot .wants symlink handle it later.
    if $NS timeout 30 systemctl enable --now "$svc" >/dev/null 2>&1; then
        echo "[quadlet-overlay] enabled $svc"
    else
        echo "[quadlet-overlay] skip $svc (enable failed/timed out -- unit absent or start error; non-fatal)"
    fi
done

# Start the autostart set + any opt-in extras. `--no-block` so the
# overlay returns immediately; each Quadlet pulls/starts in parallel
# via systemd's job queue. Restart=on-failure (set per-Quadlet) covers
# the retry on transient image-pull failures.
for svc in "${QUADLET_AUTOSTART[@]}" "${QUADLET_OPTIN[@]}"; do
    if $NS systemctl cat "$svc" >/dev/null 2>&1; then
        echo "[quadlet-overlay] start --no-block $svc (Quadlet-generated)"
        $NS systemctl start --no-block "$svc" 2>&1 | grep -vE 'created symlink' || true
    else
        echo "[quadlet-overlay] skip $svc (Quadlet not yet rendered or pruned)"
    fi
done

echo "[quadlet-overlay] running canonical fetchers (fonts + oh-my-posh + xrdp Enhanced Session)..."
for script in /automation/56-fonts.sh \
              /automation/35-xrdp-enhanced-session.sh \
              /automation/62-oh-my-posh.sh; do
    if [[ -x "$script" ]]; then
        echo "[quadlet-overlay] => $script"
        # Stream live (line-buffered), drop only bash -x trace lines -- no `tail`
        # so long fetchers show continuous progress instead of a silent gap.
        sudo stdbuf -oL bash "$script" 2>&1 | grep --line-buffered -vE '^\+ |^\+\+' || true
    fi
done

install_gnome_flatpaks() {
echo "[quadlet-overlay] installing GNOME Flatpaks for WSLg portal (one-time, ~600MB)..."
sudo install -d -m 0700 -o root -g root /run/user/0 2>/dev/null || true
export XDG_RUNTIME_DIR=/run/user/0
unset DBUS_SESSION_BUS_ADDRESS 2>/dev/null || true
sudo install -d -m 0755 /var/lib/flatpak
sudo flatpak remote-add --system --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
sudo flatpak remote-add --system --if-not-exists flathub-beta \
    https://flathub.org/beta-repo/flathub-beta.flatpakrepo 2>/dev/null || true
sudo flatpak remote-add --system --if-not-exists fedora \
    oci+https://registry.fedoraproject.org 2>/dev/null || true
# gnome-nightly: where the modern Nautilus lives (org.gnome.Nautilus.Devel).
# Flathub's org.gnome.Nautilus is EOL on GNOME 3.28; Fedora flatpak
# registry doesn't carry Nautilus at all. The Devel build tracks
# current GNOME with modern libadwaita CSS / decorations.
sudo flatpak remote-add --system --if-not-exists gnome-nightly \
    https://nightly.gnome.org/gnome-nightly.flatpakrepo 2>/dev/null || true
# "enable all beta/preview/testing repositories
# for all fedora sources". Enable updates-testing dnf repo so we
# always get the freshest Fedora packages (fixes lag in Mesa /
# libadwaita / gnome-* / etc. landing on stable).
sudo dnf config-manager setopt updates-testing.enabled=1 2>/dev/null || true
# Refresh the appstream index so the install loop below can resolve
# the app IDs. Without this step `flatpak install` errors with
# "Nothing matches <ref> in remote <remote>" on a fresh remote.
for _remote in flathub flathub-beta fedora gnome-nightly; do
    echo "[quadlet-overlay] appstream update: ${_remote}"
    sudo env FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 \
        flatpak update --system --appstream "$_remote" 2>&1 || \
        echo "[quadlet-overlay] WARN: appstream update failed: ${_remote}"
done
declare -A FLATPAK_SHORT=(
    [app.devsuite.Ptyxis]=ptyxis
    [gnome-nightly:org.gnome.Nautilus.Devel]=nautilus
    [com.github.tchx84.Flatseal]=flatseal
    [fedora:org.gnome.Epiphany]=epiphany
    [com.vscodium.codium]=codium
    [com.mattjakeman.ExtensionManager]=extension-manager
)
# Defensive cleanup: if the prior install left a gnome-software flatpak
# wrapper at /usr/local/bin/gnome-software, remove it so the dnf-installed
# /usr/bin/gnome-software (from [packages.gnome-core-apps]) takes
# precedence on PATH.
if [[ -f /usr/local/bin/gnome-software ]] && grep -q 'flatpak.*org.gnome.Software\|flatpak-launch.*org.gnome.Software' /usr/local/bin/gnome-software 2>/dev/null; then
    sudo rm -f /usr/local/bin/gnome-software
    echo "[quadlet-overlay] removed legacy /usr/local/bin/gnome-software flatpak wrapper (now installed via dnf)"
fi
# Also clean up the OLD flathub Nautilus / Epiphany if a prior install
# pulled the EOL versions -- they conflict with the fedora-remote
# versions on the same app id.
for _eol in org.gnome.Nautilus org.gnome.Epiphany; do
    if flatpak info --system "$_eol" >/dev/null 2>&1; then
        _origin=$(flatpak info --system "$_eol" 2>/dev/null | awk -F': *' '/^Origin:/ {print $2; exit}')
        if [[ "$_origin" == "flathub" ]]; then
            sudo flatpak uninstall --system --noninteractive --assumeyes "$_eol" 2>&1 | tail -2 || true
            echo "[quadlet-overlay] uninstalled EOL flathub $_eol (will reinstall from fedora remote)"
        fi
    fi
done
for keyref in "${!FLATPAK_SHORT[@]}"; do
    # Split "remote:appid" form; default to flathub when no prefix.
    if [[ "$keyref" == *:* ]]; then
        remote="${keyref%%:*}"
        ref="${keyref#*:}"
    else
        remote="flathub"
        ref="$keyref"
    fi
    if ! flatpak list --system --app --columns=application 2>/dev/null | grep -qx "$ref"; then
        # sudo prefix bypasses polkit's "Deploy not allowed for user"
        # gate on a fresh dev VM where polkit auth hasn't been
        # established yet. The sudoers drop-in below grants
        # passwordless sudo for the dev user, so this is silent.
        sudo flatpak install --system --noninteractive --assumeyes --or-update "$remote" "$ref" \
            2>&1 | grep -E '^(Installing|Updating|Already|Error|Warning)' || true
    fi
    short="${FLATPAK_SHORT[$keyref]}"
    if [[ -n "$short" ]] \
       && { [[ ! -e "/usr/local/bin/$short" ]] \
            || ! grep -q '/usr/libexec/mios/flatpak-launch' "/usr/local/bin/$short" 2>/dev/null; }
    then
        sudo tee "/usr/local/bin/$short" >/dev/null <<WRAPPER
#!/bin/sh
# /usr/local/bin/$short -- MiOS shim for the $ref flatpak.
# Generated by mios-bootstrap/build-mios.ps1 :: Invoke-MiosQuadletOverlay.
if [ -x /usr/libexec/mios/flatpak-launch ]; then
    exec /usr/libexec/mios/flatpak-launch $ref "\$@"
fi
exec flatpak run --system $ref "\$@"
WRAPPER
        sudo chmod 0755 "/usr/local/bin/$short"
    fi
done
}

DEV_USER=$(getent passwd 1000 | cut -d: -f1)
[[ -z "$DEV_USER" ]] && DEV_USER=user
if [[ ! -f /etc/sudoers.d/00-mios-dev ]]; then
    sudo tee /etc/sudoers.d/00-mios-dev >/dev/null <<SUDO
# MiOS-DEV passwordless sudo. Generated by Invoke-MiosQuadletOverlay.
# The dev VM is single-tenant on Windows; the operator already has
# host-level admin to reach the VM, so passwordless sudo here is no
# weaker than the surrounding trust boundary.
$DEV_USER ALL=(ALL) NOPASSWD: ALL
mios     ALL=(ALL) NOPASSWD: ALL
SUDO
    sudo chmod 0440 /etc/sudoers.d/00-mios-dev
    sudo visudo -c -f /etc/sudoers.d/00-mios-dev >/dev/null \
        && echo "[quadlet-overlay] sudoers drop-in installed for $DEV_USER + mios" \
        || { echo "[quadlet-overlay] WARN: sudoers drop-in failed visudo check; removing"; sudo rm -f /etc/sudoers.d/00-mios-dev; }
fi

_mios_pw='__MIOS_LOGIN_PASSWORD__'
_mios_pw_hash='__MIOS_LOGIN_PASSWORD_HASH__'
for account in "$DEV_USER" mios; do
    if [[ -n "$_mios_pw_hash" ]]; then
        printf '%s:%s\n' "$account" "$_mios_pw_hash" | sudo chpasswd -e 2>&1 \
            && echo "[quadlet-overlay] $account password hash applied" \
            || echo "[quadlet-overlay] WARN: chpasswd -e for $account failed"
    else
        printf '%s:%s\n' "$account" "$_mios_pw" | sudo chpasswd 2>&1 \
            && echo "[quadlet-overlay] $account password set" \
            || echo "[quadlet-overlay] WARN: chpasswd for $account failed"
    fi
done

if [[ -n "$_mios_pw" ]] && command -v python3 >/dev/null 2>&1; then
    if python3 - "${_mios_pw}" <<'PYVERIFY' 2>&1; then
import pty, os, sys, select, time
pw = sys.argv[1]
pid, fd = pty.fork()
if pid == 0:
    os.execvp("su", ["su", "-", "mios", "-c", "id -un"])
buf = b""
end = time.time() + 6
sent = False
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.5)
    if r:
        try: data = os.read(fd, 4096)
        except OSError: break
        if not data: break
        buf += data
        if not sent and b"assword" in buf:
            os.write(fd, pw.encode() + b"\n"); sent = True
        if b"mios" in buf and b"su:" not in buf:
            print("[quadlet-overlay] password verify OK")
            sys.exit(0)
        if b"Authentication failure" in buf or b"incorrect password" in buf:
            print("[quadlet-overlay] password verify FAILED:", buf.decode("ascii", "ignore")[:200])
            sys.exit(1)
print("[quadlet-overlay] password verify INCONCLUSIVE:", buf.decode("ascii", "ignore")[:200])
sys.exit(2)
PYVERIFY
        :
    fi
fi

TOML_FILE="/usr/share/mios/mios.toml"
# Tool inventory: log explicitly which package manager is available so
# the operator can see why a given fallback was chosen on this host.
echo "[quadlet-overlay] package manager inventory:"
echo "[quadlet-overlay]   rpm-ostree: $(command -v rpm-ostree 2>/dev/null || echo MISSING)"
echo "[quadlet-overlay]   dnf:        $(command -v dnf 2>/dev/null || echo MISSING)"
echo "[quadlet-overlay]   dnf5:       $(command -v dnf5 2>/dev/null || echo MISSING)"
echo "[quadlet-overlay]   python3:    $(command -v python3 2>/dev/null || echo MISSING)"
echo "[quadlet-overlay]   awk:        $(command -v awk 2>/dev/null || echo MISSING)"
echo "[quadlet-overlay]   toml file:  $TOML_FILE ($([[ -f "$TOML_FILE" ]] && echo present || echo MISSING))"

if [[ -f "$TOML_FILE" ]] && command -v awk >/dev/null 2>&1; then
    parse_pkgs() {
        local toml="$1"
        # Stage 1: extract sections array
        local sections
        sections=$(awk '
            $0 == "[packages]" { in_master=1; line=""; next }
            /^\[/ && in_master { in_master=0 }
            in_master && /^[[:space:]]*sections[[:space:]]*=/ { collecting=1; line=$0 }
            in_master && collecting && NR > 1 {
                if (line != $0) line = line "\n" $0
                if ($0 ~ /\]/) {
                    sub(/^[^[]*\[/, "", line)
                    sub(/\].*$/, "", line)
                    gsub(/[[:space:]]/, "", line)
                    gsub(/,/, " ", line)
                    gsub(/"/, "", line)
                    print line
                    exit
                }
            }
        ' "$toml")
        # Stage 2: for each section, extract pkgs[] when enable != false
        local sec
        for sec in $sections; do
            awk -v target="[packages.$sec]" '
                $0 == target { in_sect=1; enable=1; collecting=0; next }
                /^\[/ && in_sect { exit }
                in_sect && /^[[:space:]]*enable[[:space:]]*=[[:space:]]*false/ { enable=0 }
                in_sect && /^[[:space:]]*pkgs[[:space:]]*=/ { collecting=1; line=$0 }
                in_sect && collecting {
                    if (line != $0) line = line "\n" $0
                    if ($0 ~ /\]/) {
                        if (enable) {
                            sub(/^[^[]*\[/, "", line)
                            sub(/\].*$/, "", line)
                            n = split(line, arr, /[,\n]/)
                            for (i=1; i<=n; i++) {
                                p = arr[i]
                                gsub(/[[:space:]]/, "", p)
                                gsub(/"/, "", p)
                                gsub(/#.*/, "", p)
                                if (p != "") print p
                            }
                        }
                        exit
                    }
                }
            ' "$toml"
        done | awk '!seen[$0]++' | tr '\n' ' '
    }

    PKG_LIST=$(parse_pkgs "$TOML_FILE")
    PKG_COUNT=$(echo "$PKG_LIST" | wc -w)
    echo "[quadlet-overlay] resolved $PKG_COUNT packages from mios.toml [packages].sections"

    if [[ $PKG_COUNT -gt 0 ]]; then
        # Try package managers in order of preference:
        # 1. rpm-ostree install (canonical on FCOS / machine-os; layered + apply-live)
        # 2. rpm-ostree usroverlay + dnf install (reset-on-deployment-switch but
        #    immediate effect; survives wsl --terminate within same boot)
        # 3. dnf install standalone (mutable-fs base or already-overlayed)
        installed_via=""
        if command -v rpm-ostree >/dev/null 2>&1; then
            echo "[quadlet-overlay] rpm-ostree install: $PKG_COUNT packages (first run 10-15 min; cached after)"
            # shellcheck disable=SC2086
            if sudo rpm-ostree install --idempotent --allow-inactive $PKG_LIST 2>&1 | tail -40; then
                installed_via="rpm-ostree"
                echo "[quadlet-overlay] rpm-ostree apply-live (best-effort, layered packages active immediately where possible)..."
                sudo rpm-ostree apply-live --allow-replacement 2>&1 | tail -10 || true
            else
                echo "[quadlet-overlay] WARN: rpm-ostree install returned non-zero, falling back to dnf"
            fi
        fi
        if [[ -z "$installed_via" ]] && command -v dnf >/dev/null 2>&1; then
            echo "[quadlet-overlay] dnf install fallback (rpm-ostree usroverlay -> dnf)..."
            echo "[quadlet-overlay] installing $PKG_COUNT packages via dnf (streaming live)..."
            sudo rpm-ostree usroverlay 2>&1 | tail -3 || true
            # Stream live (stdbuf -oL) -- do NOT pipe to `tail`: tail buffers all
            # output until the (20-40 min) transaction finishes AND masks dnf's
            # exit code (the `if` would see tail's 0 and mark success on failure).
            # shellcheck disable=SC2086
            if sudo env SYSTEMD_OFFLINE=1 stdbuf -oL -eL dnf install -y --skip-unavailable $PKG_LIST 2>&1; then
                installed_via="dnf"
            fi
        fi
        if [[ -z "$installed_via" ]] && command -v dnf5 >/dev/null 2>&1; then
            echo "[quadlet-overlay] dnf5 install fallback..."
            echo "[quadlet-overlay] installing $PKG_COUNT packages via dnf5 (streaming live)..."
            sudo rpm-ostree usroverlay 2>&1 | tail -3 || true
            # Stream live; no `tail` (see dnf note above -- buffers + masks exit).
            # shellcheck disable=SC2086
            if sudo env SYSTEMD_OFFLINE=1 stdbuf -oL -eL dnf5 install -y --skip-unavailable $PKG_LIST 2>&1; then
                installed_via="dnf5"
            fi
        fi
        if [[ -n "$installed_via" ]]; then
            echo "[quadlet-overlay] package install: SUCCESS via $installed_via"
        else
            echo "[quadlet-overlay] ERROR: all package managers failed (rpm-ostree / dnf / dnf5)"
            echo "[quadlet-overlay]        machine-os may be locked-down past what live-install supports;"
            echo "[quadlet-overlay]        full set will land via mios-build-driver -> bootc switch"
        fi
    else
        echo "[quadlet-overlay] WARN: parser yielded EMPTY package list -- check awk parse logic vs $TOML_FILE"
    fi
else
    echo "[quadlet-overlay] WARN: $TOML_FILE absent or awk missing; cannot resolve package list"
fi

if command -v flatpak >/dev/null 2>&1; then
    install_gnome_flatpaks
else
    echo "[quadlet-overlay] Flatpak is not installed after package provisioning; deferring desktop apps to the later installer pass"
fi

sudo install -d -m 0755 /var/lib/mios
sudo touch "$SENTINEL"

SYSTEMD_PID=$(pidof systemd 2>/dev/null | tr ' ' '\n' | head -1)
if [[ -n "$SYSTEMD_PID" ]]; then
    NS="sudo nsenter -t $SYSTEMD_PID -a"
    echo "[quadlet-overlay] post-pkg: re-resolved systemd ns (PID $SYSTEMD_PID)"
else
    NS="sudo"
    echo "[quadlet-overlay] post-pkg: WARN systemd PID not found; falling back to bare sudo"
fi

echo "[quadlet-overlay] writing /etc/mios/hermes/config.local.yaml (host-network URL overrides)"
sudo install -d -m 0755 /etc/mios/hermes
sudo tee /etc/mios/hermes/config.local.yaml >/dev/null <<'CFGLOCAL'
# /etc/mios/hermes/config.local.yaml
# Dev VM overrides for host-network mode. Generated by
# mios-bootstrap build-mios.ps1 :: Invoke-MiosQuadletOverlay.
# Operator-edits on top of THIS file persist across re-bootstraps
# only if the file is preserved -- the overlay step regenerates it
# every run, so for permanent customization edit
# /etc/mios/hermes/config.yaml (the vendor file) or move the
# override to /var/lib/mios/hermes/operator.yaml + adjust the
# include path.
backend:
  base_url: http://localhost:${MIOS_PORTS_LLM_LIGHT:-8450}
auxiliary:
  # LLM Light's OpenAI-compatible surface for compression / summarization /
  # memory flush. Port 8080 was a legacy inference bind -- after the
  # retired-lane purge, 8080 is code-server, so the previous default 8080/v1
  # made Hermes 401 against code-server then fall through to its
  # openrouter auto-detect (which also 401'd without an API key).
  base_url: http://localhost:${MIOS_PORTS_LLM_LIGHT:-8450}/v1
tools:
  web_search:
    base_url: http://localhost:${MIOS_PORTS_SEARXNG:-8899}

# model / custom_providers / agent are intentionally NOT defined here.
# mios-hermes-firstboot seeds /var/lib/mios/hermes/config.yaml (=
# /opt/data/config.yaml inside the container) with values resolved
# from mios.toml [ai].model + [[ai.catalog]] + [ai.host_thresholds]
# auto-pick. That seeded file is what Hermes loads as $HERMES_HOME/
# config.yaml -- it pins model.provider=custom:local-llm-light and
# model.default=<resolved>. Duplicating those keys here would
# overwrite the SSOT-derived value with whatever build-time guess
# build-mios.ps1 has hardcoded -- exactly the regression
# operator hit ("MiOS-Hermes agent isn't trying hard
# enough and is not capable"). Keep config.local.yaml limited to
# host-network URL overrides only.
CFGLOCAL

echo "[quadlet-overlay] applying Network=host drop-ins (dev VM port-forward workaround)"
for svc_pair in \
    "mios-forge:Environment=FORGEJO__server__HTTP_ADDR=0.0.0.0|Environment=GITEA__server__HTTP_ADDR=0.0.0.0" \
    "mios-searxng:Environment=GRANIAN_HOST=0.0.0.0|Environment=GRANIAN_PORT=8888|Environment=SEARXNG_BIND_ADDRESS=0.0.0.0:8888|Environment=BIND_ADDRESS=0.0.0.0:8888" \
    "mios-open-webui:Environment=PORT=3030" \
    "mios-code-server:" \
    "mios-cockpit-link:" \
    "mios-llm-light:Environment=HOME=/var/lib/mios/llamacpp|Environment=LD_LIBRARY_PATH=/usr/lib/wsl/lib:/usr/local/cuda/lib64" \
    "mios-forgejo-runner:" \
; do
    svc="${svc_pair%%:*}"
    extra="${svc_pair#*:}"
    [ "$extra" = "$svc" ] && extra=""
    sudo install -d -m 0755 "/etc/containers/systemd/${svc}.container.d"
    {
        echo "[Container]"
        echo "Network="
        echo "Network=host"
        # `extra` may carry multiple Environment= lines separated by
        # `|` (the heredoc loop above can't hold newlines).
        IFS='|' read -ra extras <<< "$extra"
        for e in "${extras[@]}"; do
            [ -n "$e" ] && echo "$e"
        done
    } | sudo tee "/etc/containers/systemd/${svc}.container.d/10-mios-dev-host-network.conf" >/dev/null
done

if systemctl is-active --quiet firewalld 2>/dev/null; then
    echo "[quadlet-overlay] opening MiOS service ports in dev VM firewalld"
    for _p in __MIOS_FIREWALL_PORTS__; do
        sudo firewall-cmd --permanent --add-port="${_p}/tcp" >/dev/null 2>&1
    done
    sudo firewall-cmd --reload >/dev/null 2>&1
    echo "[quadlet-overlay]   firewalld ports: $(sudo firewall-cmd --list-ports 2>/dev/null)"
else
    echo "[quadlet-overlay] firewalld inactive in dev VM -- no ports to open"
fi

$NS systemctl daemon-reload

if [[ -r /usr/lib/systemd/system-preset/90-mios.preset ]]; then
    echo "[quadlet-overlay] applying 90-mios.preset (cockpit + pcp + firstboot services)"
    grep -E '^enable ' /usr/lib/systemd/system-preset/90-mios.preset 2>/dev/null \
      | awk '{print $2}' \
      | while read -r _unit; do
            [[ -z "$_unit" ]] && continue
            if $NS systemctl cat "$_unit" >/dev/null 2>&1; then
                $NS systemctl preset "$_unit" 2>&1 | sed 's/^/[quadlet-overlay]   /'
            fi
        done
fi

for _hostile in audit-rules.service auditd.service fapolicyd.service usbguard.service \
                bootloader-update.service greenboot-healthcheck.service \
                mios-aichat-build.service mios-wslg-permissions-fix.service \
                mios-wsl-init.service; do
    if $NS systemctl cat "$_hostile" >/dev/null 2>&1; then
        $NS systemctl stop "$_hostile" 2>/dev/null || true
        $NS systemctl mask "$_hostile" 2>&1 | sed 's/^/[quadlet-overlay]   /'
    fi
done

active=$($NS systemctl --no-legend list-units 'mios-*' 2>/dev/null | wc -l)
echo "[quadlet-overlay] done -- $active mios-* units active"
echo "[quadlet-overlay] Cockpit:        https://localhost:__MIOS_COCKPIT_PORT__/  (host LAN reachable via mirrored networking)"
echo "[quadlet-overlay] Podman Desktop: containers under MiOS-DEV machine carry openInBrowser labels"
echo "[quadlet-overlay] Terminal:       Ptyxis flatpak ready -- launch via WSLg, default tab is host shell"
echo "[quadlet-overlay] Ollama:         set MIOS_DEV_ENABLE_AI=1 then re-run for the local Ollama Quadlet"
'@

    $_quadletAutostartDefault = @(
        'mios-cockpit-link','mios-forge','mios-searxng',
        'mios-hermes','mios-hermes-workspace','ollama'
    )
    $_quadletAutostart = @(Get-MiosTomlValue -Section 'containers.quadlets' -Key 'autostart' -Default $_quadletAutostartDefault)
    $_quadletOptin     = @(Get-MiosTomlValue -Section 'containers.quadlets' -Key 'optin'     -Default @())
    # Convert ["mios-cockpit-link","mios-forge",...] to bash array
    # entries: `"mios-cockpit-link.service" "mios-forge.service" ...`
    # (one literal token per quadlet, .service suffix appended).
    $_autostartBash = (@($_quadletAutostart) | ForEach-Object { '"' + $_ + '.service"' }) -join ' '
    $_optinBash     = (@($_quadletOptin)     | ForEach-Object { '"' + $_ + '.service"' }) -join ' '
    if ($null -eq $_autostartBash) { $_autostartBash = '' }
    if ($null -eq $_optinBash)     { $_optinBash     = '' }
    $overlayScript = $overlayScript -replace '__MIOS_QUADLET_AUTOSTART__', $_autostartBash
    $overlayScript = $overlayScript -replace '__MIOS_QUADLET_OPTIN__',     $_optinBash

    $_fwServicePorts = [ordered]@{
        forge_http       = 8300
        open_webui       = 8033
        code_server      = 8800
        cockpit          = 8090
        llm_light        = 8450
        searxng          = 8899
        hermes           = 8642
        hermes_dashboard = 8119
        guacamole_web    = 8080
        ceph_dashboard   = 8444
        rdp              = 8389
        ssh              = 8222
        forge_ssh        = 8301
        cpu_node         = 8458
        agent_pipe       = 8640
        ttyd_bash        = 8681
        ttyd_powershell  = 8682
        adguard_dns      = 53
        adguard_ui       = 8053
        crawl4ai         = 8235
        firecrawl        = 8302
        opencode_gateway = 8633
        llm_heavy        = 8520
        prefilter        = 8641
        arbiter          = 8650
        daemon_agent     = 8644
        model_router     = 8645
        oscontrol        = 8453
        mcp              = 8460
    }
    $_fwPortList = [System.Collections.Generic.List[int]]::new()
    [void]$_fwPortList.Add(22)
    foreach ($_k in $_fwServicePorts.Keys) {
        [void]$_fwPortList.Add([int](Get-MiosTomlValue -Section 'ports' -Key $_k -Default $_fwServicePorts[$_k]))
    }
    $_fwPortsStr = (($_fwPortList | Sort-Object -Unique) -join ' ')
    $overlayScript = $overlayScript -replace '__MIOS_FIREWALL_PORTS__', $_fwPortsStr
    $cockpitPort = [int](Get-MiosTomlValue -Section 'ports' -Key 'cockpit' -Default 8090)
    $overlayScript = $overlayScript -replace '__MIOS_COCKPIT_PORT__', $cockpitPort

    $_loginCredential = Resolve-MiosLoginCredential
    $_miosLoginPassword = [string]$_loginCredential.Plain
    $_miosLoginHash = [string]$_loginCredential.Hash
    # Escape single-quote so the bash literal stays sound even if the
    # operator picks a password containing a quote character.
    $_miosLoginPasswordEsc = $_miosLoginPassword -replace "'", "'\''"
    $_miosLoginHashEsc = $_miosLoginHash -replace "'", "'\''"
    $overlayScript = $overlayScript.Replace('__MIOS_LOGIN_PASSWORD__', $_miosLoginPasswordEsc)
    $overlayScript = $overlayScript.Replace('__MIOS_LOGIN_PASSWORD_HASH__', $_miosLoginHashEsc)
    $_loginCredential = $null

    # CRLF -> LF: bash on Linux is allergic to \r in shebang lines /
    # heredoc terminators. The PowerShell here-string ships CRLF on
    # Windows; normalize before the script ever leaves the host.
    $overlayScript = $overlayScript -replace "`r`n", "`n" -replace "`r", "`n"

    $stagingDir  = Join-Path $MiosBootstrapShadow '.tmp'
    if (-not (Test-Path $stagingDir)) {
        New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
    }
    $stagedPath  = Join-Path $stagingDir 'quadlet-overlay-seed.sh'
    [System.IO.File]::WriteAllText($stagedPath, $overlayScript, [System.Text.UTF8Encoding]::new($false))

    # Convert Windows path -> WSL /mnt/<drive>/... path so the seed
    # script can be invoked from the WSL side without re-mounting.
    $stagedDrive = $stagedPath.Substring(0,1).ToLower()
    $stagedWsl   = "/mnt/$stagedDrive" + ($stagedPath.Substring(2) -replace '\\','/')

    $stage = "set -e; export MIOS_DEV_ENABLE_AI='$enableAi' MIOS_DEV_ENABLE_RUNNER='$enableRunner'; " +
             "bash '$stagedWsl' '$miosRootWsl'"
    & wsl.exe -d $wslDistro --exec bash -c $stage 2>&1 | ForEach-Object { Write-Log "quadlet-overlay: $_" }
    if ($LASTEXITCODE -eq 3) { Log-Fail "Quadlet placeholders did not render; no unit was started"; throw "Quadlet overlay failed: 34-render-quadlets.sh" }
    if ($LASTEXITCODE -ne 0) {
        Log-Warn "Quadlet overlay rc=$LASTEXITCODE -- partial overlay possible (units may still be present; rerun safe)"
    } else {
        Log-Ok "MiOS Quadlet overlay applied to $DevDistro"
    }
}

function Invoke-GhcrLogin([string]$Token) {
    if ([string]::IsNullOrWhiteSpace($Token)) {
        Write-Log "ghcr-login: no token (set MIOS_GITHUB_TOKEN or provide one in phase 6)"
        return
    }
    Set-Step "Authenticating podman to ghcr.io..."
    $Token | & podman login ghcr.io --username "$MiosRepoOwner" --password-stdin 2>&1 |
        ForEach-Object { Write-Log "ghcr-login: $_" }
    if ($LASTEXITCODE -eq 0) { Log-Ok "Authenticated to ghcr.io" }
    else { Log-Warn "ghcr.io login failed -- build may fail pulling base image" }
}

function Invoke-WindowsPodmanBuild([string]$BaseImage, [string]$MiosUser, [string]$MiosHostname,
                                   [string]$AiModel = '', [string]$EmbedModel = '', [string]$BakeModels = '') {
    # Preserve the public call signature; image settings come from layered SSOT.
    return Invoke-MiosNativeImageBuild -Root $MiosRepoDir -Machine $BuilderDistro
}

function Invoke-WslBuild([string]$Distro, [string]$BaseImage, [string]$AiModel,
                          [string]$MiosUser = '', [string]$MiosHostname = '',
                          [string]$EmbedModel = '', [string]$BakeModels = '') {
    return Invoke-MiosNativeImageBuild -Root $MiosRepoDir -Machine $Distro
}

function Export-WslTar([string]$OutFile, [string]$Image = '') {
    # Dynamically resolve image tag from mios.toml (localhost/mios:latest as fallback)
    if ([string]::IsNullOrWhiteSpace($Image)) {
        try {
            $Image = Get-MiosTomlValue -Section 'image' -Key 'local_tag' -Default 'localhost/mios:latest'
        } catch {
            $Image = 'localhost/mios:latest'
        }
    }
    if ([string]::IsNullOrWhiteSpace($Image)) { $Image = 'localhost/mios:latest' }

    Set-Step "Creating container snapshot of $Image..."
    Write-Log "Creating export container snapshot from $Image..."

    # Start podman create without entrypoint arguments (entrypoint command override removed to avoid conflicts)
    $createPsi = New-Object System.Diagnostics.ProcessStartInfo
    $createPsi.FileName               = "podman"
    $createPsi.Arguments              = "create $Image"
    $createPsi.RedirectStandardOutput = $true
    $createPsi.RedirectStandardError  = $true
    $createPsi.UseShellExecute        = $false
    $createPsi.CreateNoWindow         = $true

    $createProc = [System.Diagnostics.Process]::Start($createPsi)
    # Asynchronously capture standard error to prevent OS pipe buffer deadlock
    $createStderrTask = $createProc.StandardError.ReadToEndAsync()
    $createOut  = $createProc.StandardOutput.ReadToEnd()
    $createProc.WaitForExit()
    $createErr  = if ($createStderrTask) {
        try { $createStderrTask.Result } catch { "" }
    } else { "" }

    if ($createProc.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($createOut)) {
        $errDetail = if (-not [string]::IsNullOrWhiteSpace($createErr)) { $createErr.Trim() } else { "exit code $($createProc.ExitCode)" }
        Write-Log "podman create failed for ${Image}: $errDetail" "ERROR"
        throw "podman create failed for ${Image}: $errDetail"
    }

    $contLines = $createOut -split "`r?`n"
    $contId = ($contLines | Where-Object { $_ -match '^[0-9a-f]{12,64}$' } | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace($contId)) {
        $contId = ($contLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)
    }
    if ([string]::IsNullOrWhiteSpace($contId)) { throw "podman create returned no container ID (stdout: $createOut)" }
    $contId = $contId.Trim()
    Write-Log "export container ID: $contId"

    $proc = $null
    try {
        Set-Step "Streaming container filesystem -> $([System.IO.Path]::GetFileName($OutFile))..."
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName               = "podman"
        $psi.Arguments              = "export $contId"
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.UseShellExecute        = $false
        $psi.CreateNoWindow         = $true

        $proc = [System.Diagnostics.Process]::Start($psi)

        # Asynchronously capture standard error to prevent OS pipe buffer deadlock
        $stderrTask = $proc.StandardError.ReadToEndAsync()

        # Ensure parent directory exists
        $outParent = [System.IO.Path]::GetDirectoryName($OutFile)
        if ($outParent -and -not (Test-Path -LiteralPath $outParent)) {
            New-Item -ItemType Directory -Path $outParent -Force | Out-Null
        }

        $fs = [System.IO.File]::Create($OutFile)
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $buf    = New-Object byte[] 65536
            $stream = $proc.StandardOutput.BaseStream
            while ($true) {
                $n = $stream.Read($buf, 0, $buf.Length)
                if ($n -le 0) { break }
                $fs.Write($buf, 0, $n)
                if ($sw.ElapsedMilliseconds -ge 2000) {
                    $mb = [math]::Round($fs.Length / 1MB)
                    Set-Step "Exporting WSL2 tar... ${mb} MB"
                    $sw.Restart()
                }
            }
        } finally {
            $fs.Close()
        }

        $proc.WaitForExit()
        $exportStderr = if ($stderrTask) {
            try { $stderrTask.Result.Trim() } catch { "" }
        } else { "" }

        if ($proc.ExitCode -ne 0) {
            # Stderr captured, logged, and diagnosed
            Write-Log "podman export failed with exit code $($proc.ExitCode): $exportStderr" "ERROR"
            $exportMsg = "podman export exited $($proc.ExitCode)"
            if ($exportStderr) { $exportMsg += ": $exportStderr" }
            throw $exportMsg
        }

        # Verify output file exists and is not empty
        if (-not (Test-Path -LiteralPath $OutFile) -or (Get-Item -LiteralPath $OutFile).Length -eq 0) {
            throw "podman export produced an empty or missing output file: $OutFile"
        }

        return $true
    } catch {
        # Streaming failed: kill and dispose process to prevent hangs
        if ($proc -and -not $proc.HasExited) {
            try { $proc.Kill() } catch {}
        }
        # Delete partial archive to prevent corrupt artifacts
        if (Test-Path -LiteralPath $OutFile) {
            try { Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue } catch {}
        }
        throw
    } finally {
        if ($proc) {
            try {
                if (-not $proc.HasExited) {
                    $proc.Kill()
                }
            } catch {}
            try { $proc.Dispose() } catch {}
        }
        # Guaranteed cleanup of transient export container with -f
        if ($contId) {
            try { & podman rm -f $contId 2>$null | Out-Null } catch {}
        }
    }
}

function Import-MiosWsl {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0, Mandatory = $true)]
        [Alias('TarFile', 'Path', 'File', 'ArchiveFile')]
        [string]$Archive,

        [Parameter(Position = 1)]
        [string]$InstallDir = '',

        [Parameter()]
        [Alias('Distro')]
        [string]$DistroName = '',

        [Parameter()]
        [switch]$Force,

        [Parameter()]
        [switch]$Vhd
    )

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        throw "WSL is not installed or wsl.exe was not found in PATH."
    }

    if (-not (Test-Path $Archive)) { throw "WSL2 archive not found: $Archive" }

    $targetDistro = if ($DistroName) { $DistroName } elseif ($MiosWslDistro) { $MiosWslDistro } else { 'MiOS' }

    if (-not $InstallDir) {
        $InstallDir = if ($script:MiosDistroDir) {
            Join-Path $script:MiosDistroDir $targetDistro
        } else {
            Join-Path $env:LOCALAPPDATA "MiOS"
        }
    }

    # Query WSL distros safely with null-byte and whitespace trimming
    $wslRaw = & wsl.exe -l -v 2>$null
    $distros = @{}
    if ($wslRaw) {
        $distroLines = $wslRaw | ForEach-Object { ($_ -replace "\x00", "").Trim() }
        foreach ($line in $distroLines) {
            if (-not $line -or $line -match '^\s*NAME\s+STATE\s+VERSION') { continue }
            $cleanLine = $line -replace '^\*\s*', ''
            if ($cleanLine -match '^(\S+)\s+(\S+)\s+(\d+)') {
                $distros[$Matches[1]] = @{ State = $Matches[2]; Version = $Matches[3] }
            }
        }
    }

    $distroExists = $distros.ContainsKey($targetDistro)
    if ($distroExists) {
        throw "WSL runtime '$targetDistro' already exists; preserve /var and upgrade through bootc instead of unregistering it."
    }

    # Ensure target InstallDir exists
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }

    # Avoid WSL error 0x80070050: handle pre-existing ext4.vhdx in target InstallDir
    $existingVhdx = Join-Path $InstallDir "ext4.vhdx"
    if (Test-Path -LiteralPath $existingVhdx) {
        $resolvedSource = try { (Resolve-Path -LiteralPath $Archive).Path } catch { $Archive }
        $resolvedExisting = try { (Resolve-Path -LiteralPath $existingVhdx).Path } catch { $existingVhdx }
        if ($resolvedSource -and $resolvedExisting -and ($resolvedSource -ieq $resolvedExisting)) {
            $stagedSource = Join-Path $InstallDir "source_$((Get-Date).ToString('yyyyMMdd_HHmmss')).vhdx"
            Write-Log "Archive is located at target ext4.vhdx. Renaming source to $stagedSource..." "INFO"
            Move-Item -LiteralPath $Archive -Destination $stagedSource -Force
            $Archive = $stagedSource
        } else {
            $timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
            $backupVhdx = Join-Path $InstallDir "ext4.vhdx.bak"
            $backupVhdxTimestamped = Join-Path $InstallDir "ext4.vhdx.bak_${timestamp}"
            Write-Log "Target InstallDir already contains ext4.vhdx. Backing up to $backupVhdx to avoid 0x80070050..." "INFO"
            try {
                Copy-Item -LiteralPath $existingVhdx -Destination $backupVhdx -Force
                Copy-Item -LiteralPath $existingVhdx -Destination $backupVhdxTimestamped -Force
                Remove-Item -LiteralPath $existingVhdx -Force -ErrorAction Stop
            } catch {
                Write-Log "Remove-Item failed: $($_.Exception.Message). Moving file..." "WARN"
                Move-Item -LiteralPath $existingVhdx -Destination $backupVhdxTimestamped -Force
            }
        }
    }

    # Import distro: detect .vhdx vs .tar
    Set-Step "wsl --import $targetDistro ..."
    $isVhd = ($Vhd.IsPresent -or ($Archive -match '\.vhdx?$'))
    if ($isVhd) {
        Write-Log "Importing VHDX archive '$Archive' via --vhd into '$InstallDir'..." "INFO"
        & wsl.exe --import $targetDistro $InstallDir $Archive --vhd 2>&1 |
            ForEach-Object { Write-Log "wsl-import: $_" }
    } else {
        Write-Log "Importing tar archive '$Archive' via --version 2 into '$InstallDir'..." "INFO"
        & wsl.exe --import $targetDistro $InstallDir $Archive --version 2 2>&1 |
            ForEach-Object { Write-Log "wsl-import: $_" }
    }
    if ($LASTEXITCODE -ne 0) { throw "wsl --import exited $LASTEXITCODE" }

    # Configure /etc/wsl.conf and /usr/lib/wsl.conf without duplicate sections
    try {
        $sanitizePy = @'
import os, re, shutil

def sanitize_conf(filepath, has_user_mios):
    if not os.path.exists(filepath):
        content = "[boot]\nsystemd=true\n"
        if has_user_mios:
            content += "\n[user]\ndefault=mios\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    seen_sections = set()
    current_sec = None
    sec_keys = {}
    new_lines = []

    for line in lines:
        sm = re.match(r"^\s*\[([a-zA-Z0-9_-]+)\]\s*$", line)
        if sm:
            sec = sm.group(1).lower()
            if sec in seen_sections:
                current_sec = sec
                continue
            seen_sections.add(sec)
            current_sec = sec
            sec_keys[sec] = set()
            new_lines.append(f"[{sec}]")
            continue

        km = re.match(r"^\s*([a-zA-Z0-9_.-]+)\s*=\s*(.*)$", line)
        if km and current_sec:
            k = km.group(1).lower()
            v = km.group(2).strip()
            if k in sec_keys[current_sec]:
                continue
            sec_keys[current_sec].add(k)
            if current_sec == "boot" and k == "systemd":
                new_lines.append("systemd=true")
                continue
            if current_sec == "user" and k == "default":
                new_lines.append("default=mios" if has_user_mios else f"default={v}")
                continue
            new_lines.append(line)
            continue

        new_lines.append(line)

    if "boot" not in seen_sections:
        new_lines.append("[boot]")
        new_lines.append("systemd=true")
    elif "systemd" not in sec_keys.get("boot", set()):
        idx = new_lines.index("[boot]") + 1
        new_lines.insert(idx, "systemd=true")

    if has_user_mios:
        if "user" not in seen_sections:
            new_lines.append("[user]")
            new_lines.append("default=mios")
        elif "default" not in sec_keys.get("user", set()):
            idx = new_lines.index("[user]") + 1
            new_lines.insert(idx, "default=mios")

    output = "\n".join(new_lines).strip() + "\n"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(output)

has_mios = os.system("id mios >/dev/null 2>&1") == 0
if os.path.exists("/usr/lib/wsl.conf"):
    sanitize_conf("/usr/lib/wsl.conf", has_mios)
    shutil.copyfile("/usr/lib/wsl.conf", "/etc/wsl.conf")
    os.chmod("/etc/wsl.conf", 0o644)
else:
    sanitize_conf("/etc/wsl.conf", has_mios)
'@
        $pyB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($sanitizePy))
        $bashCmd = "if command -v python3 >/dev/null 2>&1; then echo '$pyB64' | base64 -d | python3; else " +
                   "if [ -f /usr/lib/wsl.conf ]; then " +
                   "if ! grep -q '^\[boot\]' /usr/lib/wsl.conf 2>/dev/null; then printf '[boot]\nsystemd=true\n\n' >> /usr/lib/wsl.conf; fi; " +
                   "if id mios >/dev/null 2>&1 && ! grep -q '^\[user\]' /usr/lib/wsl.conf 2>/dev/null; then printf '[user]\ndefault=mios\n\n' >> /usr/lib/wsl.conf; fi; " +
                   "cp -f /usr/lib/wsl.conf /etc/wsl.conf; chmod 644 /etc/wsl.conf; " +
                   "else " +
                   "if ! grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then printf '[boot]\nsystemd=true\n\n' >> /etc/wsl.conf; " +
                   "elif ! grep -qE '^[[:space:]]*systemd[[:space:]]*=' /etc/wsl.conf 2>/dev/null; then sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf; fi; " +
                   "if id mios >/dev/null 2>&1; then if ! grep -q '^\[user\]' /etc/wsl.conf 2>/dev/null; then printf '[user]\ndefault=mios\n\n' >> /etc/wsl.conf; " +
                   "elif ! grep -qE '^[[:space:]]*default[[:space:]]*=' /etc/wsl.conf 2>/dev/null; then sed -i '/^\[user\]/a default=mios' /etc/wsl.conf; fi; fi; fi; fi"
        & wsl.exe -d $targetDistro --user root --exec bash -c $bashCmd 2>&1 | ForEach-Object { Write-Log "wsl-conf: $_" }
    } catch {
        Write-Log "WARN: /etc/wsl.conf configuration: $($_.Exception.Message)" "WARN"
    }

    # Reconcile mios user home directory, permissions, and system directory access
    try {
        $reconcileCmd = "if id mios >/dev/null 2>&1; then " +
                        "mkdir -p /var/home/mios/.cache/oh-my-posh /var/home/mios/.config 2>/dev/null; " +
                        "chown -R mios:mios /var/home/mios 2>/dev/null || true; " +
                        "chmod 700 /var/home/mios/.cache 2>/dev/null || true; " +
                        "if [ -d /home/mios ]; then chown -R mios:mios /home/mios 2>/dev/null || true; fi; " +
                        "fi; " +
                        "chmod 755 /etc/sudoers.d 2>/dev/null || true; " +
                        "chmod 444 /etc/sudoers.d/* 2>/dev/null || true; " +
                        "chmod 755 /etc/fapolicyd 2>/dev/null || true; " +
                        "chmod 644 /etc/fapolicyd/fapolicyd.rules 2>/dev/null || true"
        & wsl.exe -d $targetDistro --user root --exec bash -c $reconcileCmd 2>&1 | ForEach-Object { Write-Log "wsl-reconcile: $_" }
    } catch {
        Write-Log "WARN: mios home directory reconciliation: $($_.Exception.Message)" "WARN"
    }

    # Terminate distro so systemd boots as PID 1 on next launch with clean wsl.conf
    Set-Step "Terminating WSL distro '$targetDistro' so systemd boots as PID 1..."
    & wsl.exe --terminate $targetDistro 2>$null | Out-Null
    Start-Sleep -Milliseconds 500

    return $true
}

if ($ImportWsl) {
    Repair-WslConfig
    $archive = $WslArchive
    if (-not $archive) {
        if ($Passthrough -and $Passthrough.Count -gt 0) {
            foreach ($p in $Passthrough) {
                if ($p -and (Test-Path $p)) { $archive = $p; break }
            }
        }
    }
    if (-not $archive) {
        $candidates = @(
            (Join-Path $script:MiosDistroDir "artifacts\mios-wsl2.tar"),
            (Join-Path $PSScriptRoot "artifacts\mios-wsl2.tar"),
            (Join-Path $PSScriptRoot "output\disk.wsl2"),
            (Join-Path $PSScriptRoot "output\disk.vhdx"),
            (Join-Path $PSScriptRoot "build\artifacts\mios-wsl2.tar"),
            "M:\MiOS-images\mios-wsl2.tar"
        )
        foreach ($cand in $candidates) {
            if ($cand -and (Test-Path $cand)) { $archive = $cand; break }
        }
    }
    if (-not $archive -or -not (Test-Path $archive)) {
        if ($Unattended) { throw "No pre-built WSL2 rootfs/vhdx archive found for unattended import." }
        $archive = Read-Host "Path to pre-built MiOS WSL2 archive (.tar or .vhdx)"
    }
    if (-not (Test-Path $archive)) { throw "WSL2 archive not found: '$archive'" }
    $destDir = if ($WslInstallDir) { $WslInstallDir } elseif ($script:MiosDistroDir) { Join-Path $script:MiosDistroDir $MiosWslDistro } else { Join-Path $env:LOCALAPPDATA "MiOS\distro" }
    $null = Import-MiosWsl -Archive $archive -InstallDir $destDir
    Log-Ok "WSL2 distro '$MiosWslDistro' successfully imported from '$archive' to '$destDir'."
    exit 0
}

function Invoke-BibBuild([string[]]$Types, [string]$MachineOutDir, [int]$TimeoutMin = 60) {
    # Run bootc-image-builder inside the machine via Windows podman API (-> machine socket)
    # Types: 'qcow2', 'raw', 'anaconda-iso', 'vmdk'
    $typeArgs = ($Types | ForEach-Object { "--type $_" }) -join " "
    Set-Step "BIB: $($Types -join '+')..."
    Write-Log "BIB start: types=$($Types -join ',')  out=$MachineOutDir"

    Set-Step "BIB: creating output dir on dev distro..."
    $machineName = if ($env:MIOS_BUILDER_MACHINE) { $env:MIOS_BUILDER_MACHINE } else { $DevDistro }
    Invoke-DistroSh -Bash "mkdir -p '$MachineOutDir' && chmod 0755 '$MachineOutDir'" -MachineName $machineName 2>&1 |
        ForEach-Object { Write-Log "bib-mkdir: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "WARN: BIB output-dir mkdir returned $LASTEXITCODE -- BIB will likely fail with statfs ENOENT"
    }

    $bibImage = Get-MiosTomlValue -Section 'image' -Key 'bib' -Default 'quay.io/centos-bootc/bootc-image-builder:latest'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = "cmd.exe"
    $psi.Arguments = ("/c podman run --rm --privileged --pull=newer " +
        "--security-opt label=type:unconfined_t " +
        "-v /var/lib/containers/storage:/var/lib/containers/storage " +
        "-v ${MachineOutDir}:/output:z " +
        "$bibImage " +
        "$typeArgs --local localhost/mios:latest 2>&1")
    $psi.RedirectStandardOutput = $true
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $sw   = [System.Diagnostics.Stopwatch]::StartNew()
    $done = $false
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not $proc.StandardOutput.EndOfStream) {
        $line = $proc.StandardOutput.ReadLine()
        if ($null -eq $line) { break }
        Write-Log "bib: $line"
        if ($sw.ElapsedMilliseconds -ge 2000) {
            $elapsed = [math]::Floor($timer.Elapsed.TotalMinutes)
            Set-Step "BIB ${elapsed}min: $($line.Substring(0,[math]::Min($line.Length,60)))"
            $sw.Restart()
        }
        if ($timer.Elapsed.TotalMinutes -ge $TimeoutMin) {
            Write-Log "WARN: BIB timeout after ${TimeoutMin}min -- killing"
            $proc.Kill()
            break
        }
    }
    $proc.WaitForExit()
    Write-Log "BIB end: exit=$($proc.ExitCode)"
    return $proc.ExitCode -eq 0
}

function Copy-FromMachine([string]$MachinePath, [string]$WinDest) {
    # podman machine cp MiOS-DEV:/path/in/machine C:\windows\path
    Set-Step "Copying $([System.IO.Path]::GetFileName($MachinePath)) from machine..."
    & podman machine cp "${BuilderDistro}:${MachinePath}" $WinDest 2>&1 |
        ForEach-Object { Write-Log "machine-cp: $_" }
    return ($LASTEXITCODE -eq 0)
}

function New-MiosHyperVVm([string]$RawPath, [int]$RamGB = 8) {
    if (-not (Get-Command New-VM -EA SilentlyContinue)) {
        Write-Log "Hyper-V module not available -- skipping VM creation"
        return $false
    }
    # Convert raw -> vhdx if Convert-VHD is available
    $vhdxPath = [System.IO.Path]::ChangeExtension($RawPath, ".vhdx")
    if (Get-Command Convert-VHD -EA SilentlyContinue) {
        Set-Step "Converting raw -> vhdx..."
        try {
            Convert-VHD -Path $RawPath -DestinationPath $vhdxPath -VHDType Dynamic -EA Stop
        } catch {
            Write-Log "Convert-VHD failed: $_ -- trying raw rename"
            $vhdxPath = [System.IO.Path]::ChangeExtension($RawPath, ".vhd")
            Copy-Item $RawPath $vhdxPath -Force
        }
    } else {
        # Raw can be used as a fixed VHD by Hyper-V if renamed .vhd
        $vhdxPath = [System.IO.Path]::ChangeExtension($RawPath, ".vhd")
        Copy-Item $RawPath $vhdxPath -Force
    }
    if (-not (Test-Path $vhdxPath)) { throw "VHDX/VHD not found after conversion" }

    # Remove existing VM if present
    $vmName = $MiosWslDistro
    try { Remove-VM -Name $vmName -Force -EA SilentlyContinue } catch {}

    Set-Step "Creating Hyper-V VM: $vmName..."
    $vm = New-VM -Name $vmName -MemoryStartupBytes ($RamGB * 1GB) `
                 -VHDPath $vhdxPath -Generation 2 -EA Stop
    Set-VMFirmware  -VMName $vmName -EnableSecureBoot Off
    Set-VMProcessor -VMName $vmName -Count ([math]::Max(2, [int]([Environment]::ProcessorCount / 2)))
    Set-VMMemory    -VMName $vmName -DynamicMemoryEnabled $true `
                    -MinimumBytes 2GB -MaximumBytes ($RamGB * 1GB)
    Log-Ok "Hyper-V VM '$vmName' created from $([System.IO.Path]::GetFileName($vhdxPath))"
    return $true
}

function Invoke-DeployPipeline([hashtable]$HW) {
    $artifactDir = Join-Path $MiosDistroDir "artifacts"
    $wslFsDir    = Join-Path $MiosDistroDir "MiOS"
    if (-not (Test-Path $artifactDir)) { New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null }
    if (-not (Test-Path $wslFsDir))    { New-Item -ItemType Directory -Path $wslFsDir    -Force | Out-Null }

    # -- Phase 10: Export WSL2 tar ----------------------------------------------
    Start-Phase 10
    $wslTar = Join-Path $artifactDir "mios-wsl2.tar"
    $wslOk  = $false
    try {
        $wslOk = Export-WslTar -OutFile $wslTar
        $sizeMB = [math]::Round((Get-Item $wslTar).Length / 1MB)
        Log-Ok "WSL2 tar: ${sizeMB}MB -> $wslTar"
        End-Phase 10
    } catch {
        Log-Warn "WSL2 export: $_"
        End-Phase 10 -Fail
        $script:ExitCode = 1
        $ExitCode = 1
    }

    # -- Phase 11: Register WSL2 distro ----------------------------------------
    Start-Phase 11
    if ($wslOk) {
        try {
            $null = Import-MiosWsl -TarFile $wslTar -InstallDir $wslFsDir
            Log-Ok "WSL2 distro '$MiosWslDistro' registered at $wslFsDir"
            End-Phase 11
        } catch {
            Log-Warn "WSL2 import: $_"
            End-Phase 11 -Fail
            $script:ExitCode = 1
            $ExitCode = 1
        }
    } else {
        Log-Warn "Skipped (no WSL2 tar due to export failure)"
        End-Phase 11 -Fail
        $script:ExitCode = 1
        $ExitCode = 1
    }

    # -- Phase 12: BIB disk images (qcow2 + raw) -------------------------------
    Start-Phase 12
    $bibMachineDir = "/tmp/mios-bib-output"
    $bibOk = $false
    try {
        $bibOk = Invoke-BibBuild -Types @('qcow2','raw') -MachineOutDir $bibMachineDir
        if ($bibOk) {
            # Copy artifacts from machine to Windows
            $cpOk = @{}
            foreach ($pair in @(
                @{ src="$bibMachineDir/qcow2/disk.qcow2"; dst=Join-Path $artifactDir "mios.qcow2" },
                @{ src="$bibMachineDir/image/disk.raw";   dst=Join-Path $artifactDir "mios.raw"   }
            )) {
                try {
                    $cpOk[$pair.dst] = Copy-FromMachine $pair.src $pair.dst
                    if ($cpOk[$pair.dst]) {
                        $sz = [math]::Round((Get-Item $pair.dst).Length / 1GB, 1)
                        Log-Ok "$([System.IO.Path]::GetFileName($pair.dst)): ${sz}GB"
                    }
                } catch { Write-Log "WARN: copy $($pair.src): $_" }
            }
            End-Phase 12
        } else {
            Log-Warn "BIB build failed (non-fatal -- OCI image still available in $BuilderDistro)"
            End-Phase 12 -Warn
        }
    } catch {
        Log-Warn "BIB phase: $_"
        End-Phase 12 -Warn
    }

    # -- Phase 13: Hyper-V VM from raw disk ------------------------------------
    Start-Phase 13
    $rawPath = Join-Path $artifactDir "mios.raw"
    if ($bibOk -and (Test-Path $rawPath)) {
        try {
            $vmOk = New-MiosHyperVVm -RawPath $rawPath -RamGB ([math]::Max(4, [math]::Min($HW.RamGB / 2, 16)))
            if ($vmOk) { End-Phase 13 } else { Log-Warn "Hyper-V not available"; End-Phase 13 -Warn }
        } catch {
            Log-Warn "Hyper-V VM: $_"
            End-Phase 13 -Warn
        }
    } else {
        Log-Warn "Skipped (no raw disk image)"
        End-Phase 13 -Warn
    }
}

function Test-MiosDevDistroHealthy {
    Set-Step ((Get-MiosTomlValue -Section 'messages.steps' -Key 'smoke_header_template' -Default "Smoke-testing {distro} before rename...") -replace '\{distro\}', $DevDistro)

    # The pre-rename distro is "podman-$DevDistro"; post-rename it's
    # just "$DevDistro". This function is called pre-rename so we
    # check both for safety.
    $wslList = @()
    try { $wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" | ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ } } catch {}
    $candidates = @("podman-$DevDistro", $DevDistro)
    $name = $wslList | Where-Object { $candidates -contains $_ } | Select-Object -First 1
    if (-not $name) {
        Log-Warn "smoke: neither podman-$DevDistro nor $DevDistro is registered"
        return $false
    }

    $echoOut = ""
    $echoAttempts = [int](Get-MiosTomlValue -Section 'smoke_tests' -Key 'echo_attempts'    -Default 15)
    $echoIntSec   = [int](Get-MiosTomlValue -Section 'smoke_tests' -Key 'interval_seconds' -Default 2)
    for ($i = 1; $i -le $echoAttempts; $i++) {
        try { $echoOut = (& wsl.exe -d $name -- /bin/sh -c 'echo ready' 2>&1) -join "" } catch {}
        if ($echoOut.Trim() -eq 'ready') { break }
        if ($i -lt $echoAttempts) { Start-Sleep -Seconds $echoIntSec }
    }
    if ($echoOut.Trim() -ne 'ready') {
        Log-Warn "smoke: $name did not respond to 'echo ready' (got: '$echoOut') after $echoAttempts attempts"
        return $false
    }
    Log-Ok ((Get-MiosTomlValue -Section 'messages.steps' -Key 'smoke_responsive_template' -Default "smoke 1/4: {name} is responsive") -replace '\{name\}', $name)

    # 2. systemd up. Retried with backoff: Phase 3's wsl --terminate
    # restarts the distro right before this smoke check runs, so systemd
    # is warming up. Without retry, `systemctl is-system-running` returns
    # 'offline' before pid1 has finished switch-root.
    # SSOT: attempts + interval resolve through mios.toml [smoke_tests].
    $sysOut = ""
    $sysAttempts  = [int](Get-MiosTomlValue -Section 'smoke_tests' -Key 'systemd_attempts'   -Default 15)
    $smokeIntSec  = [int](Get-MiosTomlValue -Section 'smoke_tests' -Key 'interval_seconds'   -Default 2)
    for ($i = 1; $i -le $sysAttempts; $i++) {
        try { $sysOut = (& wsl.exe -d $name --user root -- /bin/sh -c 'systemctl is-system-running 2>&1 || true' 2>&1) -join "" } catch {}
        if ($sysOut.Trim() -notmatch '^(offline|unknown)\s*$' -and -not [string]::IsNullOrWhiteSpace($sysOut)) { break }
        if ($i -lt $sysAttempts) { Start-Sleep -Seconds $smokeIntSec }
    }
    if ($sysOut.Trim() -match '^(offline|unknown)\s*$' -or [string]::IsNullOrWhiteSpace($sysOut)) {
        Log-Warn "smoke: systemd not reachable in $name after $sysAttempts attempts (state: '$sysOut')"
        # Non-fatal -- some build flows skip systemd. Continue.
    } else {
        Log-Ok ((Get-MiosTomlValue -Section 'messages.steps' -Key 'smoke_systemd_template' -Default "smoke 2/4: systemd state '{state}' in {name}") -replace '\{state\}', $sysOut.Trim() -replace '\{name\}', $name)
    }

    # 3. MiOS overlay present.
    $overlayOut = ""
    try { $overlayOut = (& wsl.exe -d $name --user root -- /bin/sh -c 'test -d /usr/share/mios && echo present || echo missing' 2>&1) -join "" } catch {}
    if ($overlayOut.Trim() -ne 'present') {
        Log-Warn "smoke: /usr/share/mios overlay missing in $name (got: '$overlayOut')"
        # Non-fatal -- the overlay is applied at build time, not
        # bootstrap. The dev distro's Fedora rootfs is the only thing
        # we need pre-build.
    } else {
        Log-Ok ((Get-MiosTomlValue -Section 'messages.steps' -Key 'smoke_overlay_template' -Default "smoke 3/4: /usr/share/mios overlay present in {name}") -replace '\{name\}', $name)
    }

    if ($name -eq "podman-$DevDistro") {
        $podOut = ""
        $okFmt = '^[0-9]+\.[0-9]+'
        $attempts     = [int](Get-MiosTomlValue -Section 'smoke_tests' -Key 'podman_api_attempts' -Default 15)
        $smokeIntSec2 = [int](Get-MiosTomlValue -Section 'smoke_tests' -Key 'interval_seconds'    -Default 2)
        for ($i = 1; $i -le $attempts; $i++) {
            try { $podOut = (& podman --connection "${DevDistro}-root" version --format '{{.Server.Version}}' 2>&1) -join "" } catch { $podOut = "$_" }
            if ($podOut -match $okFmt) { break }
            if ($i -lt $attempts) { Start-Sleep -Seconds $smokeIntSec2 }
        }
        if ($podOut -match $okFmt) {
            Log-Ok ((Get-MiosTomlValue -Section 'messages.steps' -Key 'smoke_podman_api_template' -Default "smoke 4/4: podman API server v{version}") -replace '\{version\}', $podOut.Trim())
        } else {
            Log-Warn "smoke: podman API not responding after $attempts attempts (got: '$podOut')"
            # Non-fatal -- machine may still be warming up; first
            # `podman machine inspect` call after this will succeed.
        }
    }

    return $true
}

function Invoke-DistroSh {
    param(
        [Parameter(Mandatory)] [string] $Bash,
        [string] $MachineName = $script:DevDistro,
        [switch] $NoSudo
    )
    $Bash = $Bash -replace "`r`n", "`n" -replace "`r", "`n"
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Bash))
    # --user root makes sudo redundant on the wsl path; pre-rename
    # podman-machine-ssh runs as `core` so sudo is needed unless the
    # script is itself root-safe. Default = sudo on the ssh path,
    # bare bash on the wsl path.
    $sudoPrefix = if ($NoSudo) { '' } else { 'sudo ' }

    $wslList = @()
    try { $wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" | ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ } } catch {}

    if ($wslList -contains $MachineName) {
        # Post-rename: wsl --user root, no sudo (already root).
        $inner = "echo $encoded | base64 -d | bash"
        & wsl.exe -d $MachineName --user root -- /bin/sh -c $inner
        return
    }
    if ($wslList -contains "podman-$MachineName") {
        # Pre-rename: podman machine ssh, sudo unless caller opts out.
        $inner = "echo $encoded | base64 -d | ${sudoPrefix}bash"
        & podman machine ssh $MachineName -- /bin/sh -c $inner
        return
    }
    Write-Log "Invoke-DistroSh: neither '$MachineName' nor 'podman-$MachineName' is registered" "ERROR"
    # Synthesize a non-zero exit code so callers' $LASTEXITCODE check fires.
    cmd /c "exit 127" | Out-Null
}

function Set-MiosWslConfig {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)]
        [int]$RamGB = 0,

        [Parameter(Position = 1)]
        [int]$Cpus = 0,

        [Parameter()]
        [switch]$Force,

        [Parameter()]
        [switch]$NoShutdown
    )

    if ($RamGB -le 0) {
        $RamGB = if ($script:HW -and $script:HW.RamGB) { $script:HW.RamGB } else { 8 }
    }
    if ($Cpus -le 0) {
        $Cpus = if ($script:HW -and $script:HW.Cpus) { $script:HW.Cpus } else { [Math]::Max(2, [Environment]::ProcessorCount) }
    }

    $wslCfg = Join-Path $env:USERPROFILE ".wslconfig"
    $winBuild = [Environment]::OSVersion.Version.Build
    $isWin11_22H2 = ($winBuild -ge 22621)
    $targetNetMode = if ($isWin11_22H2) { "mirrored" } else { "NAT" }

    $defaultKeys = [ordered]@{
        memory              = "${RamGB}GB"
        processors          = "$Cpus"
        swap                = "4GB"
        networkingMode      = $targetNetMode
        localhostForwarding = "true"
        dnsTunneling        = "true"
        autoProxy           = "true"
        guiApplications     = "true"
    }

    $cfgRaw = if (Test-Path $wslCfg) { Get-Content $wslCfg -Raw } else { "" }

    if ($cfgRaw -notmatch "\[wsl2\]") {
        $block = "`n[wsl2]`n# MiOS-managed -- host resources for MiOS-DEV`n"
        foreach ($kv in $defaultKeys.GetEnumerator()) { $block += "$($kv.Key)=$($kv.Value)`n" }
        $finalContent = if ($cfgRaw.Trim()) { $cfgRaw.TrimEnd() + "`n`n" + $block.TrimStart() } else { $block }
        [System.IO.File]::WriteAllText($wslCfg, $finalContent, (New-Object System.Text.UTF8Encoding($false)))
        Log-Ok ".wslconfig: wrote [wsl2] -- ${RamGB}GB RAM, $Cpus CPUs, $targetNetMode"
        if (-not $NoShutdown) {
            try { & wsl.exe --shutdown 2>$null | Out-Null } catch {}
        }
        return
    }

    $deprecatedKeys = @('firewall')
    $bootSectionKeys = @('systemd', 'command', 'enabled', 'appendWindowsPath',
                         'default', 'options', 'mountFsTab',
                         'generateHosts', 'generateResolvConf', 'hostname')
    $lines    = (Get-Content $wslCfg)
    $inWsl2   = $false
    $patched  = [System.Collections.Generic.List[string]]::new()
    $inserted = [System.Collections.Generic.HashSet[string]]::new()
    $effectiveRam = "${RamGB}GB"
    $effectiveCpus = "$Cpus"
    $effectiveNetMode = $targetNetMode

    foreach ($line in $lines) {
        if ($line -match "^\s*\[wsl2\]\s*$") { $inWsl2 = $true; $patched.Add($line); continue }
        elseif ($line -match "^\s*\[")        { $inWsl2 = $false; $patched.Add($line); continue }
        if ($inWsl2 -and $line -match "^\s*([A-Za-z0-9_.-]+)\s*=\s*(.*)$") {
            $key = $Matches[1]
            $val = $Matches[2].Trim()
            if ($deprecatedKeys -contains $key -or $bootSectionKeys -contains $key) { continue }
            if ($defaultKeys.Contains($key)) {
                # Preserve existing memory and processors limits unless -Force is specified
                if (-not $Force -and ($key -ieq 'memory' -or $key -ieq 'processors') -and $val) {
                    $patched.Add("$key=$val")
                    $null = $inserted.Add($key)
                    if ($key -ieq 'memory') { $effectiveRam = $val }
                    if ($key -ieq 'processors') { $effectiveCpus = $val }
                    continue
                }
                # Reconcile networkingMode based on Windows build
                if ($key -ieq 'networkingMode') {
                    $patched.Add("networkingMode=$targetNetMode")
                    $null = $inserted.Add($key)
                    $effectiveNetMode = $targetNetMode
                    continue
                }
                $patched.Add("$key=$($defaultKeys[$key])")
                $null = $inserted.Add($key)
                continue
            }
        }
        $patched.Add($line)
    }

    $missing = $defaultKeys.Keys | Where-Object { -not $inserted.Contains($_) }
    if ($missing) {
        $wsl2Match = ($patched | Select-String -Pattern "^\s*\[wsl2\]\s*$" | Select-Object -First 1)
        $insertIdx = if ($wsl2Match) { $wsl2Match.LineNumber } else { 0 }
        $offset = 0
        foreach ($key in $missing) {
            $patched.Insert($insertIdx + $offset, "$key=$($defaultKeys[$key])")
            $offset++
        }
    }
    # BOM-free UTF-8: a UTF-8 BOM makes WSL ignore [wsl2].
    [System.IO.File]::WriteAllLines($wslCfg, $patched, (New-Object System.Text.UTF8Encoding($false)))
    Log-Ok ".wslconfig: merged [wsl2] -- $effectiveRam RAM, $effectiveCpus CPUs, $effectiveNetMode"
    if (-not $NoShutdown) {
        try { & wsl.exe --shutdown 2>$null | Out-Null } catch {}
    }
}

function Set-MiosLanFirewallRules {

    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Log-Warn "Firewall: New-NetFirewallRule cmdlet not available; skipping LAN inbound rules"
        return
    }

    $_defaultPorts = [ordered]@{
        forge_http       = 8300
        open_webui       = 8033
        code_server      = 8800
        cockpit          = 8090
        llm_light        = 8450
        searxng          = 8899
        hermes           = 8642
        hermes_dashboard = 8119
        guacamole_web    = 8080
        ceph_dashboard   = 8444
        rdp              = 8389
    }

    # Resolve per-service ports from [ports].<key>, falling back to vendor.
    $_ports = [ordered]@{}
    foreach ($k in $_defaultPorts.Keys) {
        $_ports[$k] = [int](Get-MiosTomlValue -Section 'ports' -Key $k -Default $_defaultPorts[$k])
    }

    # Which profiles + which services to expose (operator-editable).
    $_profiles = @(Get-MiosTomlValue -Section 'ports.lan_firewall' -Key 'profiles' -Default @('Private','Domain'))
    $_expose   = @(Get-MiosTomlValue -Section 'ports.lan_firewall' -Key 'expose'   -Default @($_defaultPorts.Keys))
    if ($_profiles.Count -eq 0) { $_profiles = @('Private','Domain') }
    if ($_expose.Count   -eq 0) { $_expose   = @($_defaultPorts.Keys) }

    $applied = New-Object System.Collections.Generic.List[string]
    foreach ($svc in $_expose) {
        if (-not $_ports.Contains($svc)) {
            Write-Log "firewall: skip '$svc' -- not in [ports] section"
            continue
        }
        $port = [int]$_ports[$svc]
        if ($port -lt 1 -or $port -gt 65535) { continue }
        # Rule name carries the "MiOS - " prefix so Invoke-MiOSFullReap
        # can sweep them on uninstall without touching other rules.
        $name = "MiOS - $svc ($port/tcp)"
        try {
            $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
            if ($existing) {
                # Idempotent re-run: refresh action / profile / port.
                Set-NetFirewallRule -DisplayName $name `
                    -Enabled True -Action Allow -Direction Inbound `
                    -Profile ($_profiles -join ',') -ErrorAction SilentlyContinue
                $existing | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue |
                    Set-NetFirewallPortFilter -Protocol TCP -LocalPort $port -ErrorAction SilentlyContinue
                Write-Log "firewall: refreshed '$name' on profiles $($_profiles -join ',')"
            } else {
                New-NetFirewallRule -DisplayName $name `
                    -Description "MiOS LAN inbound (auto-generated by mios-bootstrap)" `
                    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port `
                    -Profile ($_profiles -join ',') -ErrorAction Stop | Out-Null
                Write-Log "firewall: created '$name' (TCP $port) on profiles $($_profiles -join ',')"
            }
            $applied.Add("$svc/$port")
        } catch {
            Log-Warn ("firewall: could not add '$name': " + $_.Exception.Message)
        }
    }
    if ($applied.Count -gt 0) {
        Log-Ok ("Windows Firewall: LAN inbound rules on $($_profiles -join '+') for " + ($applied -join ', '))
    }
}

function Set-MiosLanPortProxy {
    # Skip entirely under WSL2 mirrored networking: there is no distinct VM
    # eth0 to resolve and Windows already exposes container ports on the host,
    # so netsh portproxy is both impossible and unnecessary (the
    # "could not resolve a clean WSL VM IP" warning came from mirrored mode --
    # the .wslconfig the installer writes now uses networkingMode=mirrored).
    try {
        $_wslCfg = Join-Path $env:USERPROFILE '.wslconfig'
        if ((Test-Path $_wslCfg) -and ((Get-Content -LiteralPath $_wslCfg -Raw -ErrorAction SilentlyContinue) -match '(?im)^\s*networkingMode\s*=\s*mirrored\b')) {
            Log-Ok "portproxy: skipped (networkingMode=mirrored exposes container ports on the host directly)"
            return
        }
    } catch {}
    $_defaultPorts = [ordered]@{
        forge_http       = 8300
        open_webui       = 8033
        code_server      = 8800
        cockpit          = 8090
        llm_light        = 8450
        searxng          = 8899
        hermes           = 8642
        hermes_dashboard = 8119
        guacamole_web    = 8080
        ceph_dashboard   = 8444
        rdp              = 8389
    }
    $_ports = [ordered]@{}
    foreach ($k in $_defaultPorts.Keys) {
        $_ports[$k] = [int](Get-MiosTomlValue -Section 'ports' -Key $k -Default $_defaultPorts[$k])
    }
    $_expose = @(Get-MiosTomlValue -Section 'ports.lan_firewall' -Key 'expose' -Default @($_defaultPorts.Keys))
    if ($_expose.Count -eq 0) { $_expose = @($_defaultPorts.Keys) }

    $_wslIp = $null
    try {
        $_prevWslUtf8 = $env:WSL_UTF8
        $env:WSL_UTF8 = '1'
        $_raw = (& wsl.exe -d $DevDistro --user root -- sh -c "ip -4 -o addr show eth0" 2>$null) -join "`n"
        $env:WSL_UTF8 = $_prevWslUtf8
        $_m = [regex]::Match($_raw, '\binet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b')
        if ($_m.Success) { $_wslIp = $_m.Groups[1].Value }
    } catch {}
    if (-not $_wslIp -or $_wslIp -notmatch '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') {
        Log-Warn "portproxy: could not resolve a clean WSL VM IP (got: '$_wslIp') -- skipping forwarding"
        return
    }
    Write-Log "portproxy: WSL VM ip = $_wslIp"

    foreach ($svc in $_expose) {
        if (-not $_ports.Contains($svc)) { continue }
        $port = [int]$_ports[$svc]
        if ($port -lt 1 -or $port -gt 65535) { continue }
        # Drop any prior rule (idempotent re-run).
        & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port 2>&1 | Out-Null
        $r = & netsh interface portproxy add v4tov4 `
                  listenaddress=0.0.0.0 listenport=$port `
                  connectaddress=$_wslIp connectport=$port 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "portproxy: 0.0.0.0:$port -> ${_wslIp}:$port ($svc)"
        } else {
            Log-Warn "portproxy add for $svc :$port failed: $($r -join ' ')"
        }
    }

    # Ensure the ipv4-listen helper service is up (netsh portproxy
    # depends on IPHelper running -- a fresh Server SKU sometimes
    # ships it Disabled).
    try {
        $svc = Get-Service -Name iphlpsvc -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -ne 'Running') {
            Set-Service -Name iphlpsvc -StartupType Automatic -ErrorAction SilentlyContinue
            Start-Service -Name iphlpsvc -ErrorAction SilentlyContinue
        }
    } catch {}
}

function Restore-PodmanPrefix {
    if ($env:MIOS_SKIP_PODMAN_RESTORE -in @('1','true','TRUE','yes')) {
        return
    }
    $wslList = @()
    try { $wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" | ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ } } catch {}
    $renamed  = $wslList -contains $DevDistro
    $prefixed = $wslList -contains "podman-$DevDistro"
    if ($prefixed) { return }                # already correct
    if (-not $renamed) { return }            # nothing to restore from

    Set-Step "Restoring podman- prefix on $DevDistro (recovery)..."
    & wsl.exe --shutdown 2>$null | Out-Null
    $tmpTar = Join-Path $env:TEMP "mios-podman-restore-$([guid]::NewGuid().ToString('N').Substring(0,8)).tar.gz"
    try {
        Log-Ok "Exporting $DevDistro -> $tmpTar"
        & wsl.exe --export $DevDistro $tmpTar 2>&1 | ForEach-Object { Write-Log "wsl-export: $_" }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmpTar)) {
            Log-Warn "wsl --export $DevDistro failed; cannot restore podman prefix"
            return
        }
        & wsl.exe --unregister $DevDistro 2>&1 | ForEach-Object { Write-Log "wsl-unregister: $_" }
        if (-not (Test-Path $script:MiosDistroDir)) { New-Item -ItemType Directory -Path $script:MiosDistroDir -Force | Out-Null }
        $newPath = Join-Path $script:MiosDistroDir "podman-$DevDistro"
        Log-Ok "Re-importing as podman-$DevDistro at $newPath"
        & wsl.exe --import "podman-$DevDistro" $newPath $tmpTar --version 2 2>&1 | ForEach-Object { Write-Log "wsl-import: $_" }
        if ($LASTEXITCODE -eq 0) {
            Log-Ok "Recovery complete: $DevDistro restored as podman-$DevDistro"
            Log-Warn "podman machine commands work again. User-facing labels still show '$DevDistro'."
        } else {
            Log-Warn "wsl --import podman-$DevDistro failed; restoring original $DevDistro"
            & wsl.exe --import $DevDistro (Join-Path $script:MiosDistroDir $DevDistro) $tmpTar --version 2 2>&1 | ForEach-Object { Write-Log "wsl-import-fallback: $_" }
        }
    } finally {
        if (Test-Path $tmpTar) { Remove-Item $tmpTar -Force -ErrorAction SilentlyContinue }
    }
}

function Rename-PodmanDevDistro {
    $_renameDefault   = [string](Get-MiosTomlValue -Section 'bootstrap.dev_vm' -Key 'rename_distro' -Default 'false')
    $_renameRequested = if ($env:MIOS_RENAME_DISTRO -in @('1','true','TRUE','yes','True')) { $true }
                       elseif ($env:MIOS_RENAME_DISTRO -in @('0','false','FALSE','no','False')) { $false }
                       else { ($_renameDefault -ieq 'true') }
    if (-not $_renameRequested) {
        Log-Ok "WSL distro rename skipped (mios.toml [bootstrap.dev_vm].rename_distro=$_renameDefault) -- preserves Podman Desktop visibility for podman-$DevDistro. Edit in mios.html or set `$env:MIOS_RENAME_DISTRO=1 to opt in."
        return
    }
    Set-Step "Renaming podman-$DevDistro -> $DevDistro (drops podman- prefix)..."

    $oldName = "podman-$DevDistro"
    $newName = $DevDistro

    # Snapshot current registrations.
    $wslList = @()
    try { $wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" | ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ } } catch {}

    if ($wslList -contains $newName -and -not ($wslList -contains $oldName)) {
        Log-Ok "$newName already registered and $oldName absent -- nothing to rename"
        return
    }
    if (-not ($wslList -contains $oldName)) {
        Log-Warn "$oldName not registered -- nothing to rename (skipping)"
        return
    }
    if ($wslList -contains $newName) {
        Log-Warn "$newName already exists alongside $oldName -- skipping rename to avoid clobbering an existing distro. Run 'wsl --unregister $newName' manually if you want to redo this."
        return
    }

    # Stop the machine so the WSL VM has no active mounts when we
    # export. Errors here are non-fatal -- if podman wasn't running we
    # just proceed straight to wsl --shutdown.
    try { & podman machine stop $DevDistro 2>$null | Out-Null } catch {}
    & wsl.exe --shutdown 2>$null | Out-Null

    # Pick the new home path -- prefer the dedicated MiOS data disk if
    # present (already redirected by Update-MiosInstallPaths during
    # Install-WindowsBranding), else fall back to the standard distros
    # dir under %ProgramData%/%LOCALAPPDATA%.
    $newDistroDir = Join-Path $MiosDistroDir $newName
    if (-not (Test-Path $MiosDistroDir)) { New-Item -ItemType Directory -Path $MiosDistroDir -Force | Out-Null }

    # Export to a temp tarball, unregister the old, import with the
    # new name. wsl --export uses gzip-compressed tar by default since
    # Win11; we keep .tar.gz suffix explicit so the format is obvious.
    $tmpTar = Join-Path $env:TEMP "mios-distro-rename-$([guid]::NewGuid().ToString('N').Substring(0,8)).tar.gz"
    try {
        Log-Ok "Exporting $oldName -> $tmpTar"
        & wsl.exe --export $oldName $tmpTar 2>&1 | ForEach-Object { Write-Log "wsl-export: $_" }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmpTar)) {
            throw "wsl --export $oldName failed (exit $LASTEXITCODE)"
        }
        Log-Ok "Unregistering $oldName"
        & wsl.exe --unregister $oldName 2>&1 | ForEach-Object { Write-Log "wsl-unregister: $_" }
        if ($LASTEXITCODE -ne 0) {
            throw "wsl --unregister $oldName failed (exit $LASTEXITCODE)"
        }
        Log-Ok "Importing as $newName at $newDistroDir"
        & wsl.exe --import $newName $newDistroDir $tmpTar --version 2 2>&1 | ForEach-Object { Write-Log "wsl-import: $_" }
        if ($LASTEXITCODE -ne 0) {
            # Recovery: re-import as the old name so the operator isn't
            # left with NO dev distro at all.
            Log-Warn "wsl --import $newName failed -- restoring $oldName from tarball"
            & wsl.exe --import $oldName (Join-Path $MiosDistroDir $oldName) $tmpTar --version 2 2>&1 | ForEach-Object { Write-Log "wsl-import-recovery: $_" }
            throw "wsl --import $newName failed (exit $LASTEXITCODE) -- $oldName restored"
        }

        # Boot the new distro once so subsequent podman commands hit a
        # running VM. `wsl -d <name> -- echo` is the lightest possible
        # warm-start that doesn't depend on the distro's default user
        # being configured.
        & wsl.exe -d $newName -- /bin/sh -c 'echo ready' 2>&1 | ForEach-Object { Write-Log "wsl-warm: $_" }

        Log-Ok "Renamed: $oldName -> $newName ($newDistroDir)"
        Log-Warn "Note: 'podman machine start/ssh $newName' will fail (podman hardcodes the 'podman-' prefix). Use 'wsl -d $newName' or the 'mios-dev' shortcut instead. The Windows-side podman client (podman build/cp/commit) still works via the existing SSH connection."
    } catch {
        Log-Warn "Distro rename aborted: $_"
    } finally {
        if (Test-Path $tmpTar) {
            try { Remove-Item $tmpTar -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

function New-Shortcut([string]$Path,[string]$Target,[string]$ArgList="",[string]$Desc="",[string]$Dir="") {
    $ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut($Path)
    $sc.TargetPath = $Target
    if ($ArgList) { $sc.Arguments = $ArgList }
    if ($Desc)    { $sc.Description = $Desc }
    if ($Dir)     { $sc.WorkingDirectory = $Dir }
    $sc.Save()
}

function Install-MiosWindowsTools {
    $_hostSrc = $null
    foreach ($_c in @(
        (Join-Path $MiosRepoDir 'src\install-host-tools.ps1'),
        (Join-Path $MiosBootstrapShadow 'src\install-host-tools.ps1')
    )) {
        if (Test-Path -LiteralPath $_c) { $_hostSrc = $_c; break }
    }
    if (-not $_hostSrc) {
        Log-Fail "src/install-host-tools.ps1 not found in repo. Re-run irm | iex to refresh."
        return
    }
    # Dot-source REDEFINES Install-MiosWindowsTools with the on-disk body
    # then re-invokes it. The redefinition is idempotent.
    . $_hostSrc
    Install-MiosWindowsTools
}

function Install-MiOSTmuxProfile {
    param([switch]$Required)
    # Bootstrap may run before the native catalog is installed. Final installation
    # and runtime must use the native renderer; a deferred phase is never success.
    $nativeRoots = @($env:MIOS_NATIVE_BIN, (Join-Path $env:ProgramData 'MiOS\bin'), (Join-Path $env:LOCALAPPDATA 'MiOS\bin'))
    foreach ($nativeRoot in $nativeRoots) {
        if (-not $nativeRoot) { continue }
        $launcher = Join-Path $nativeRoot 'mios-launch.exe'
        if ((Test-Path -LiteralPath $launcher -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $nativeRoot 'native-binding.json') -PathType Leaf)) {
            & $launcher --stage-host-terminal
            if ($LASTEXITCODE -ne 0) { throw "Native layered SSOT tmux projection failed (exit $LASTEXITCODE)" }
            return (Join-Path $env:LOCALAPPDATA 'MiOS\terminal\mios-host.tmux.conf')
        }
    }
    if ($Required) { throw 'Native tmux renderer or runtime binding is missing; installation is incomplete.' }
    Write-Host '  [tmux] Projection deferred until the native catalog and runtime binding are installed.' -ForegroundColor DarkGray
    return $null
}

function Install-WindowsBranding {
    if ($env:MIOS_SKIP_WINDOWS_BRANDING -in @('1','true','TRUE','yes')) {
        Log-Warn "MIOS_SKIP_WINDOWS_BRANDING set -- Windows branding install skipped"
        return
    }

    $resolvedRoot = Resolve-MiosInstallRoot
    if ($resolvedRoot -ne $script:MiosInstallDir) {
        $legacyRoot = $script:MiosInstallDir
        Log-Ok "MiOS data disk detected -- redirecting install root: $legacyRoot -> $resolvedRoot"
        Update-MiosInstallPaths -NewRoot $resolvedRoot
        Invoke-MigrateLegacyInstallRoot -LegacyRoot $legacyRoot
    }
    Set-Step "Installing oh-my-posh + Geist + Nerd fonts under $($script:MiosInstallDir)..."

    $_fontVercelRepo = [string](Get-MiosTomlValue -Section 'theme.font' -Key 'vercel_repo'   -Default 'https://github.com/vercel/geist-font.git')
    $_fontNerdMono   = [string](Get-MiosTomlValue -Section 'theme.font' -Key 'url'           -Default 'https://github.com/ryanoasis/nerd-fonts/releases/latest/download/GeistMono.zip')
    $_fontSymbols    = [string](Get-MiosTomlValue -Section 'theme.font' -Key 'symbols_url'   -Default 'https://github.com/ryanoasis/nerd-fonts/releases/latest/download/NerdFontsSymbolsOnly.zip')
    $_fontScope      = [string](Get-MiosTomlValue -Section 'theme.font' -Key 'install_scope' -Default 'auto')

    $_isAdmin = (New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

    if ($_fontScope -ieq 'system' -or ($_fontScope -ieq 'auto' -and $_isAdmin)) {
        $fontDir   = "$env:WINDIR\Fonts"
        $regKey    = 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        $scopeTag  = 'system-wide'
        if (-not $_isAdmin) {
            Log-Warn "[theme.font].install_scope=system but not running elevated -- falling back to per-user"
            $fontDir  = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Fonts'
            $regKey   = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
            $scopeTag = 'per-user (fallback)'
        }
    } else {
        $fontDir  = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Fonts'
        $regKey   = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        $scopeTag = 'per-user'
    }
    if (-not (Test-Path $fontDir)) { New-Item -ItemType Directory -Path $fontDir -Force | Out-Null }
    if (-not (Test-Path $regKey))  { New-Item -Path $regKey -Force | Out-Null }

    function Install-FontFile([string]$Source) {
        try {
            $name = [System.IO.Path]::GetFileName($Source)
            $dest = Join-Path $fontDir $name
            if (Test-Path $dest) { return $false }
            Copy-Item -Path $Source -Destination $dest -Force
            $ext  = [System.IO.Path]::GetExtension($name).ToLower()
            $face = [System.IO.Path]::GetFileNameWithoutExtension($name)
            $regName = if ($ext -eq '.otf') { "$face (OpenType)" } else { "$face (TrueType)" }
            New-ItemProperty -Path $regKey -Name $regName -Value $dest -PropertyType String -Force | Out-Null
            return $true
        } catch { Write-Log "font-install: $name : $($_.Exception.Message)" "WARN"; return $false }
    }

    # Geist (Vercel) -- shallow clone the upstream repo, copy *.otf + *.ttf
    $geistTmp = Join-Path $env:TEMP "mios-geist-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    try {
        $null = Invoke-NativeQuiet { git clone --depth=1 --quiet $_fontVercelRepo $geistTmp }
        if (Test-Path $geistTmp) {
            $count = 0
            Get-ChildItem -Path $geistTmp -Recurse -Include '*.otf','*.ttf' | ForEach-Object {
                if (Install-FontFile -Source $_.FullName) { $count++ }
            }
            Log-Ok "Geist (Vercel) installed ($scopeTag, $count new)"
        } else { Log-Warn "Geist clone failed -- skipping Vercel font install" }
    } finally {
        if (Test-Path $geistTmp) { Remove-Item $geistTmp -Recurse -Force -ErrorAction SilentlyContinue }
    }

    # GeistMono Nerd Font -- the canonical MiOS terminal/UI face
    $geistMonoTmp = Join-Path $env:TEMP "mios-geistmono-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    New-Item -ItemType Directory -Path $geistMonoTmp -Force | Out-Null
    try {
        $geistMonoZip = Join-Path $geistMonoTmp 'GeistMono.zip'
        Invoke-WebRequest -Uri $_fontNerdMono -OutFile $geistMonoZip -UseBasicParsing -ErrorAction Stop
        Expand-Archive -Path $geistMonoZip -DestinationPath $geistMonoTmp -Force
        $count = 0
        Get-ChildItem -Path $geistMonoTmp -Recurse -Include '*.otf','*.ttf' | ForEach-Object {
            if (Install-FontFile -Source $_.FullName) { $count++ }
        }
        Log-Ok "GeistMono Nerd Font installed ($scopeTag, $count new)"
    } catch { Log-Warn "GeistMono Nerd Font fetch failed: $($_.Exception.Message)" }
    finally { if (Test-Path $geistMonoTmp) { Remove-Item $geistMonoTmp -Recurse -Force -ErrorAction SilentlyContinue } }

    # Symbols-Only Nerd Font (Powerline + Devicon glyphs the omp theme uses)
    $nerdTmp = Join-Path $env:TEMP "mios-nerd-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    New-Item -ItemType Directory -Path $nerdTmp -Force | Out-Null
    try {
        $nerdZip = Join-Path $nerdTmp 'NerdFontsSymbolsOnly.zip'
        Invoke-WebRequest -Uri $_fontSymbols -OutFile $nerdZip -UseBasicParsing -ErrorAction Stop
        Expand-Archive -Path $nerdZip -DestinationPath $nerdTmp -Force
        $count = 0
        Get-ChildItem -Path $nerdTmp -Recurse -Include '*.otf','*.ttf' | ForEach-Object {
            if (Install-FontFile -Source $_.FullName) { $count++ }
        }
        Log-Ok "Symbols-Only Nerd Font installed ($scopeTag, $count new)"
    } catch { Log-Warn "Nerd Font fetch failed: $($_.Exception.Message)" }
    finally { if (Test-Path $nerdTmp) { Remove-Item $nerdTmp -Recurse -Force -ErrorAction SilentlyContinue } }

    # System-scope installs need a GDI WM_FONTCHANGE broadcast so apps see
    # the new fonts without logoff. Per-user installs are picked up
    # automatically by the user's session.
    if ($regKey -like 'HKLM:*') {
        try {
            Add-Type -Namespace MiosFontX -Name Native -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Auto)] public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, System.IntPtr lParam, uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);' -ErrorAction SilentlyContinue
            $_fcRes = [System.UIntPtr]::Zero
            [void][MiosFontX.Native]::SendMessageTimeout([IntPtr]0xFFFF, 0x001D, [IntPtr]::Zero, [IntPtr]::Zero, [uint32]0x0002, [uint32]1000, [ref]$_fcRes)
        } catch {}
    }

    # -- 2. oh-my-posh.exe (installed into $MiosBinDir) ---------------
    # Single canonical install location: $MiosInstallDir\bin (= C:\MiOS\bin
    # for admin installs, %LOCALAPPDATA%\MiOS\bin otherwise) so all MiOS
    # tooling lives under one root and a single PATH entry covers them.
    New-Item -ItemType Directory -Path $MiosBinDir -Force | Out-Null
    $ompExe  = Join-Path $MiosBinDir 'oh-my-posh.exe'
    if (-not (Test-Path $ompExe)) {
        try {
            $arch = if ([Environment]::Is64BitOperatingSystem) {
                if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
            } else { '386' }
            $url = "https://github.com/JanDeDobbeleer/oh-my-posh/releases/latest/download/posh-windows-$arch.exe"
            Invoke-WebRequest -Uri $url -OutFile $ompExe -UseBasicParsing -ErrorAction Stop
            Log-Ok "oh-my-posh.exe installed at $ompExe"
        } catch { Log-Warn "oh-my-posh download failed: $($_.Exception.Message)"; return }
    }

    # Add $MiosBinDir to PATH (machine-wide for admin installs, user
    # otherwise) so `oh-my-posh`, `mios-dash`, `mios-dev`, etc. all
    # resolve from any new shell.
    $pathScope = if ($script:IsAdmin) { 'Machine' } else { 'User' }
    $envPath = [Environment]::GetEnvironmentVariable('Path', $pathScope)
    if (-not ($envPath -split ';' | Where-Object { $_ -ieq $MiosBinDir })) {
        [Environment]::SetEnvironmentVariable('Path', "$envPath;$MiosBinDir", $pathScope)
        Log-Ok "Added $MiosBinDir to $pathScope PATH"
    }

    # -- 3. PowerShell profile + theme --------------------------------
    # mios.git overlay puts the theme at $MiosRepoDir\usr\share\mios\...
    # (per "M:\ IS git" directive). The mios-bootstrap shadow
    # is checked as a defensive fallback.
    $miosThemeSrc = Join-Path $MiosRepoDir 'usr\share\mios\oh-my-posh\mios.omp.json'
    if (-not (Test-Path $miosThemeSrc)) {
        $miosThemeSrc = Join-Path $MiosBootstrapShadow 'usr\share\mios\oh-my-posh\mios.omp.json'
    }
    if (Test-Path $miosThemeSrc) {
        New-Item -ItemType Directory -Path $MiosThemesDir -Force | Out-Null
        $themeDst = Join-Path $MiosThemesDir 'mios.omp.json'
        Copy-Item -Path $miosThemeSrc -Destination $themeDst -Force
        try {
            $_pwRight = Get-MiosTomlValue -Section 'theme.prompt' -Key 'powerline_right'  -Default ''
            $_pwLeft  = Get-MiosTomlValue -Section 'theme.prompt' -Key 'powerline_left'   -Default ''
            $_ldDia   = Get-MiosTomlValue -Section 'theme.prompt' -Key 'leading_diamond'  -Default ''
            $_trDia   = Get-MiosTomlValue -Section 'theme.prompt' -Key 'trailing_diamond' -Default ''
            $_omp = Get-Content -LiteralPath $themeDst -Raw -Encoding UTF8
            # Map each TOML glyph to its JSON-escaped \uXXXX equivalent
            # so the on-disk file remains ASCII-safe (the source omp.json
            # explicitly notes "Nerd Font private-use-area glyphs are
            # encoded as \uXXXX so the file roundtrips through any editor
            # without losing the U+E000..F8FF code points").
            function _Esc([string]$s) {
                if (-not $s) { return $null }
                $cp = [int][char]$s[0]
                return ('\u{0:x4}' -f $cp)
            }
            $_eR = _Esc $_pwRight; $_eL = _Esc $_pwLeft; $_eLD = _Esc $_ldDia; $_eTD = _Esc $_trDia
            # Replace every powerline_symbol occurrence by VALUE -- the
            # current vendor default is [U+E0B4] (right) or [U+E0B6] (left);
            # we don't know which segments use which without parsing,
            # so we substitute by current literal in two passes.
            if ($_eR -and $_eR -ne "$([char]0xE0B4)") { $_omp = $_omp -replace '\\ue0b4', $_eR }
            if ($_eL -and $_eL -ne "$([char]0xE0B6)") { $_omp = $_omp -replace '\\ue0b6', $_eL }
            # leading_diamond / trailing_diamond appear only on diamond-
            # style segments (the leading text + trailing time caps).
            # Patch by JSON key: "leading_diamond": "[U+E0B6]" -> the new
            # value. Same for trailing_diamond.
            if ($_eLD -and $_eLD -ne "$([char]0xE0B6)") {
                $_omp = $_omp -replace '("leading_diamond"\s*:\s*")\\u[0-9a-fA-F]{4}', ('${1}' + $_eLD)
            }
            if ($_eTD -and $_eTD -ne "$([char]0xE0B4)") {
                $_omp = $_omp -replace '("trailing_diamond"\s*:\s*")\\u[0-9a-fA-F]{4}', ('${1}' + $_eTD)
            }
            $_palette = @(
                @{ Token='accent';  VendorHex='#1A407F' }
                @{ Token='fg';      VendorHex='#E7DFD3' }
                @{ Token='bg';      VendorHex='#282262' }
                @{ Token='cursor';  VendorHex='#F35C15' }
                @{ Token='success'; VendorHex='#3E7765' }
                @{ Token='error';   VendorHex='#DC271B' }
                @{ Token='muted';   VendorHex='#948E8E' }
                @{ Token='subtle';  VendorHex='#B7C9D7' }
                @{ Token='earth';   VendorHex='#734F39' }
            )
            foreach ($_pe in $_palette) {
                $_resolved = Get-MiosTomlValue -Section 'colors' -Key $_pe.Token -Default $_pe.VendorHex
                if ($_resolved -and $_resolved -ne $_pe.VendorHex -and $_resolved -match '^#[0-9A-Fa-f]{3,8}$') {
                    $_omp = [regex]::Replace($_omp, [regex]::Escape($_pe.VendorHex), $_resolved, 'IgnoreCase')
                }
            }
            Set-Content -LiteralPath $themeDst -Value $_omp -Encoding UTF8 -NoNewline
            Log-Ok "omp.json glyphs + palette synced from mios.toml [theme.prompt] + [colors]"
        } catch {
            Log-Warn "omp.json [theme.prompt] substitution failed: $($_.Exception.Message) -- shipped defaults retained"
        }
        Log-Ok "MiOS oh-my-posh theme staged at $themeDst"

        $profilePath = $PROFILE.CurrentUserAllHosts
        if (-not $profilePath) { $profilePath = $PROFILE }
        $profileDir  = Split-Path $profilePath -Parent
        if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }
        $existing = if (Test-Path $profilePath) { Get-Content $profilePath -Raw } else { '' }
        $marker   = '# >>> MiOS oh-my-posh init >>>'
        $endMark  = '# <<< MiOS oh-my-posh init <<<'
        $miosProfilePath = if (Test-Path 'M:\') { 'M:\MiOS\powershell\profile.ps1' }
                            else { Join-Path $env:USERPROFILE 'MiOS-bootstrap\powershell\profile.ps1' }
        $block = @"
$marker
# Auto-generated redirector. The MiOS profile body (PSReadLine reload +
# oh-my-posh init + fastfetch MOTD + dashboard) lives at M:\ as the
# SSOT; this block is replaced on every re-run between the markers.
`$_miosProfile = '$miosProfilePath'
if (Test-Path `$_miosProfile) { . `$_miosProfile }
$endMark
"@
        # Marker-delimited block replace (idempotent across re-runs).
        # The replacement string is fed to .NET Regex.Replace which
        # treats $0/$1/$& specially -- escape any literal $ inside the
        # block content so a `$miosTheme` template substring doesn't
        # accidentally turn into a backreference.
        if ($existing -match [regex]::Escape($marker)) {
            $pattern  = "(?s)$([regex]::Escape($marker)).*?$([regex]::Escape($endMark))"
            $safeRepl = $block -replace '\$', '$$$$'
            $existing = [regex]::Replace($existing, $pattern, $safeRepl)
        } else {
            $existing = ($existing.TrimEnd() + "`n`n" + $block + "`n").TrimStart()
        }
        Set-Content -Path $profilePath -Value $existing -Encoding UTF8 -NoNewline
        Log-Ok "PowerShell profile updated: $profilePath"
    } else {
        Log-Warn "MiOS oh-my-posh theme not found in cloned repos -- profile not updated"
    }

    Log-Ok "Windows-side branding installed (open a NEW pwsh window to see the MiOS prompt)"
}

function New-MiosIcon {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [ValidateSet('plain','dev','pull','dash','build','update','config','help')] [string] $Badge = 'plain'
    )
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
    $sizes = @(16, 32, 48, 64, 256)
    $bitmaps = @()
    foreach ($s in $sizes) {
        $bmp = New-Object System.Drawing.Bitmap $s, $s
        $g   = [System.Drawing.Graphics]::FromImage($bmp)
        $g.SmoothingMode      = 'AntiAlias'
        $g.TextRenderingHint  = 'AntiAlias'
        $g.InterpolationMode  = 'HighQualityBicubic'
        $g.PixelOffsetMode    = 'HighQuality'

        $bg     = [System.Drawing.Color]::FromArgb(40, 34, 98)
        $fg     = [System.Drawing.Color]::FromArgb(231, 223, 211)
        $accent = [System.Drawing.Color]::FromArgb(243, 92, 21)
        $shade  = [System.Drawing.Color]::FromArgb(20, 17, 49)
        $green  = [System.Drawing.Color]::FromArgb(62, 119, 101)
        $g.Clear($bg)

        # -- Iso cube vertices ----------------------------------------
        # Six visible vertices of an isometric cube silhouette, plus
        # the front (vMid) corner. The cube is centered at (cx, cy)
        # with extent $r. All face polygons share these vertices so
        # edges line up exactly.
        $cx = $s / 2.0
        $cy = $s / 2.0
        $r  = $s * 0.36
        $cos30 = 0.866
        $hH = $r * 0.55          # half-height (vertical)
        $hW = $r * $cos30        # half-width (horizontal)
        $vTop  = [System.Drawing.PointF]::new($cx,        $cy - $hH * 1.10)
        $vTopR = [System.Drawing.PointF]::new($cx + $hW,  $cy - $hH * 0.55)
        $vBotR = [System.Drawing.PointF]::new($cx + $hW,  $cy + $hH * 0.55)
        $vBot  = [System.Drawing.PointF]::new($cx,        $cy + $hH * 1.10)
        $vBotL = [System.Drawing.PointF]::new($cx - $hW,  $cy + $hH * 0.55)
        $vTopL = [System.Drawing.PointF]::new($cx - $hW,  $cy - $hH * 0.55)
        $vMid  = [System.Drawing.PointF]::new($cx,        $cy)

        # Cast to PointF[] explicitly: PowerShell's overload resolver
        # otherwise picks DrawPolygon(Pen, Point[]) (the int variant)
        # and tries to coerce PointF -> Point, which throws.
        [System.Drawing.PointF[]] $topPts   = @($vTop,  $vTopR, $vMid,  $vTopL)
        [System.Drawing.PointF[]] $leftPts  = @($vTopL, $vMid,  $vBot,  $vBotL)
        [System.Drawing.PointF[]] $rightPts = @($vTopR, $vBotR, $vBot,  $vMid)

        # Fill the three faces.
        $brushTop   = New-Object System.Drawing.SolidBrush($accent)
        $brushLeft  = New-Object System.Drawing.SolidBrush($fg)
        $brushRight = New-Object System.Drawing.SolidBrush($shade)
        $g.FillPolygon($brushTop,   $topPts)
        $g.FillPolygon($brushLeft,  $leftPts)
        $g.FillPolygon($brushRight, $rightPts)
        $brushTop.Dispose(); $brushLeft.Dispose(); $brushRight.Dispose()

        # -- Hatch marks (`/:\` echoes of the ASCII art) --------------
        # Skip at 16 px -- the lines turn to mush. At 32+ each face
        # gets two parallel diagonal strokes to mimic the wireframe
        # `/:\` cross-hatching of the dashboard letters.
        if ($s -ge 32) {
            $hatchPen = New-Object System.Drawing.Pen($bg, [math]::Max(1, $s / 64))
            # Left face: lines parallel to the top-left -> bottom edge.
            for ($i = 1; $i -le 2; $i++) {
                $t = $i / 3.0
                $a = [System.Drawing.PointF]::new(
                    $vTopL.X + ($vMid.X  - $vTopL.X) * $t,
                    $vTopL.Y + ($vMid.Y  - $vTopL.Y) * $t)
                $b = [System.Drawing.PointF]::new(
                    $vBotL.X + ($vBot.X  - $vBotL.X) * $t,
                    $vBotL.Y + ($vBot.Y  - $vBotL.Y) * $t)
                $g.DrawLine($hatchPen, $a, $b)
            }
            # Right face: lines parallel to the top-right -> bottom edge.
            for ($i = 1; $i -le 2; $i++) {
                $t = $i / 3.0
                $a = [System.Drawing.PointF]::new(
                    $vTopR.X + ($vMid.X  - $vTopR.X) * $t,
                    $vTopR.Y + ($vMid.Y  - $vTopR.Y) * $t)
                $b = [System.Drawing.PointF]::new(
                    $vBotR.X + ($vBot.X  - $vBotR.X) * $t,
                    $vBotR.Y + ($vBot.Y  - $vBotR.Y) * $t)
                $g.DrawLine($hatchPen, $a, $b)
            }
            # Top face: a single cross-stroke from top-left corner
            # to mid (just a hint -- two lines clutter the small face).
            $tA = [System.Drawing.PointF]::new(
                $vTopL.X + ($vTop.X - $vTopL.X) * 0.5,
                $vTopL.Y + ($vTop.Y - $vTopL.Y) * 0.5)
            $tB = [System.Drawing.PointF]::new(
                $vTopR.X + ($vMid.X - $vTopR.X) * 0.5,
                $vTopR.Y + ($vMid.Y - $vTopR.Y) * 0.5)
            $g.DrawLine($hatchPen, $tA, $tB)
            $hatchPen.Dispose()
        }

        # -- Edge strokes (cube outline) ------------------------------
        $edgePen = New-Object System.Drawing.Pen($bg, [math]::Max(1, $s / 36))
        $g.DrawPolygon($edgePen, $topPts)
        $g.DrawPolygon($edgePen, $leftPts)
        $g.DrawPolygon($edgePen, $rightPts)
        # Inner spine (top-vertex -> mid) for the iso "Y" silhouette.
        $g.DrawLine($edgePen, $vTop, $vMid)
        $edgePen.Dispose()

        # -- Badge (verb-specific glyph in bottom-right) --------------
        if ($Badge -ne 'plain' -and $s -ge 32) {
            $bSize = [int]($s * 0.36)
            $bX    = $s - $bSize - 1
            $bY    = $s - $bSize - 1
            # Green for read-only verbs (dev shell, dashboard, config-edit);
            # orange for state-mutating verbs (build, pull, update).
            $badgeFill = if ($Badge -in @('dev','dash','config')) { $green } else { $accent }
            $badgeBrush = New-Object System.Drawing.SolidBrush($badgeFill)
            $g.FillEllipse($badgeBrush, $bX, $bY, $bSize, $bSize)
            $badgeBrush.Dispose()
            $glyphFont = New-Object System.Drawing.Font("Segoe UI Symbol", [int]($bSize * 0.65), [System.Drawing.FontStyle]::Bold)
            $glyphChar = switch ($Badge) {
                'dev'    { [char]0x276F }   # > chevron right
                'pull'   { [char]0x2193 }   # v down arrow
                'dash'   { [char]0x25A6 }   # # grid
                'build'  { [char]0x2699 }   # * gear
                'update' { [char]0x21BB }   # @ clockwise
                'config' { [char]0x2699 }   # * gear
                'help'   { [char]0x003F }   # ? question mark
            }
            $sf = New-Object System.Drawing.StringFormat
            $sf.Alignment = 'Center'; $sf.LineAlignment = 'Center'
            $glyphBrush = New-Object System.Drawing.SolidBrush($fg)
            $g.DrawString([string]$glyphChar, $glyphFont, $glyphBrush,
                [System.Drawing.RectangleF]::FromLTRB($bX, $bY, $bX + $bSize, $bY + $bSize), $sf)
            $glyphFont.Dispose(); $glyphBrush.Dispose()
        }
        $g.Dispose()
        $bitmaps += ,$bmp
    }
    # Multi-image .ico writer (ICONDIR header + ICONDIRENTRY[] + per-image PNG blocks).
    $fs = [System.IO.File]::Create($Path)
    $bw = New-Object System.IO.BinaryWriter($fs)
    $bw.Write([UInt16]0)                    # reserved
    $bw.Write([UInt16]1)                    # type = icon
    $bw.Write([UInt16]$bitmaps.Count)
    $icoBlocks = @()
    foreach ($bmp in $bitmaps) {
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $icoBlocks += ,$ms.ToArray()
    }
    $offset = 6 + (16 * $bitmaps.Count)
    for ($i = 0; $i -lt $bitmaps.Count; $i++) {
        $b = $bitmaps[$i]; $p = $icoBlocks[$i]
        $bw.Write([byte]($(if ($b.Width  -ge 256) { 0 } else { $b.Width  })))
        $bw.Write([byte]($(if ($b.Height -ge 256) { 0 } else { $b.Height })))
        $bw.Write([byte]0)              # palette
        $bw.Write([byte]0)              # reserved
        $bw.Write([UInt16]1)            # color planes
        $bw.Write([UInt16]32)           # bpp
        $bw.Write([UInt32]$p.Length)
        $bw.Write([UInt32]$offset)
        $offset += $p.Length
    }
    foreach ($p in $icoBlocks) { $bw.Write($p) }
    $bw.Flush(); $bw.Close(); $fs.Close()
    foreach ($bmp in $bitmaps) { $bmp.Dispose() }
}

function Install-MiosLauncher {
    if ($env:MIOS_SKIP_LAUNCHER -in @('1','true','TRUE','yes')) {
        Log-Warn "MIOS_SKIP_LAUNCHER set -- launcher install skipped"
        return
    }
    Set-Step "Installing MiOS desktop launcher under $MiosInstallDir..."

    foreach ($d in @($MiosInstallDir, $MiosBinDir, $MiosIconsDir, $MiosThemesDir, $StartMenuDir)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }

    # -- 1. Generate the icon family (one .ico per verb) ---------------
    $iconMap = @{
        'mios'         = 'plain'
        'mios-dev'     = 'dev'
        'mios-pull'    = 'pull'
        'mios-dash'    = 'dash'
        'mios-build'   = 'build'
        'mios-update'  = 'update'
        'mios-config'  = 'config'
        'mios-help'    = 'help'
    }
    $icoPaths = @{}
    foreach ($name in $iconMap.Keys) {
        $p = Join-Path $MiosIconsDir "$name.ico"
        try {
            New-MiosIcon -Path $p -Badge $iconMap[$name]
            $icoPaths[$name] = $p
        } catch {
            Log-Warn "icon $name : $($_.Exception.Message)"
        }
    }
    $icoPath = $icoPaths['mios']
    if ($icoPath) { Log-Ok "Generated $($iconMap.Count) MiOS icons under $MiosIconsDir" }
    else          { Log-Warn "icon generation failed -- shortcuts will use default WT icon"; $icoPath = "" }

    $dashPath = Join-Path $MiosBinDir 'mios-dash.ps1'
    $dashScript = @'
$ErrorActionPreference = 'SilentlyContinue'

$Global:MiosProfileMotdRendered = $true

$_miosProfile = 'M:\MiOS\powershell\profile.ps1'
if (Test-Path -LiteralPath $_miosProfile) {
    . $_miosProfile
    if (Get-Command Show-MiosDashboard -ErrorAction SilentlyContinue) {
        Show-MiosDashboard
        return
    }
}
Write-Host "  [!] M:\MiOS\powershell\profile.ps1 missing or Show-MiosDashboard not defined." -ForegroundColor Yellow
Write-Host "      Re-run irm | iex Get-MiOS.ps1 to refresh the profile." -ForegroundColor DarkGray
return
'@
    Set-Content -Path $dashPath -Value $dashScript -Encoding UTF8
    Log-Ok "Windows mios-dash staged at $dashPath (delegates to profile Show-MiosDashboard for unified compact rendering)"

    $devResolveBlock = @"
`$Global:MiosDevCandidates = @('$DevDistro', 'podman-$DevDistro', '$LegacyDevName', 'podman-$LegacyDevName')
function Resolve-MiosDevDistro {
    `$wslList = @()
    try { `$wslList = (& wsl.exe -l -q 2>`$null) -split "``r?``n" | ForEach-Object { (`$_ -replace [char]0, '').Trim() } | Where-Object { `$_ } } catch {}
    `$match = `$Global:MiosDevCandidates | Where-Object { `$wslList -contains `$_ } | Select-Object -First 1
    if (-not `$match) { `$match = '$DevDistro' }
    return `$match
}
"@
    $devPath = Join-Path $MiosBinDir 'mios-dev.ps1'
    Set-Content -Path $devPath -Value @"
$devResolveBlock
`$distro = Resolve-MiosDevDistro
if (`$args.Count -eq 0) {
    & (Join-Path `$PSScriptRoot 'mios.cmd') terminal
} else {
    wsl.exe -d `$distro @args
}
"@ -Encoding UTF8

    $pullPath = Join-Path $MiosBinDir 'mios-pull.ps1'
    Set-Content -Path $pullPath -Value @"
$devResolveBlock
`$ErrorActionPreference = 'Continue'

# Step 1: Windows-side M:\ refresh.
`$miosRoot = 'M:\'
if ((Test-Path (Join-Path `$miosRoot '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host '  [mios-pull] Windows-side: git fetch + reset --hard origin/main on M:\...' -ForegroundColor Cyan
    try {
        & git -C `$miosRoot fetch --depth=1 origin main 2>&1 | ForEach-Object { Write-Host ('    ' + `$_) -ForegroundColor DarkGray }
        if (`$LASTEXITCODE -eq 0) {
            & git -C `$miosRoot reset --hard origin/main 2>&1 | ForEach-Object { Write-Host ('    ' + `$_) -ForegroundColor DarkGray }
            if (`$LASTEXITCODE -eq 0) {
                `$_head = (& git -C `$miosRoot rev-parse --short HEAD 2>`$null)
                Write-Host ('  [mios-pull] M:\ now at origin/main HEAD = ' + `$_head) -ForegroundColor Green
            } else {
                Write-Host '  [mios-pull] M:\ git reset --hard failed' -ForegroundColor Yellow
            }
        } else {
            Write-Host '  [mios-pull] M:\ git fetch failed (offline?)' -ForegroundColor Yellow
        }
    } catch {
        Write-Host ('  [mios-pull] M:\ git refresh threw: ' + `$_.Exception.Message) -ForegroundColor Yellow
    }
} else {
    Write-Host '  [mios-pull] M:\ is not a git working tree -- skipping Windows-side refresh' -ForegroundColor Yellow
}

Write-Host '  [mios-pull] dev VM: syncing / overlay to origin/main...' -ForegroundColor Cyan
`$inlinePull = @'
set -uo pipefail
if [ -x /usr/bin/mios-pull ]; then
    # post-bootc-switch path: canonical script is present, defer to it
    sudo /usr/bin/mios-pull "`$@"
    exit `$?
fi
# pre-bootc-switch path: do the same work inline
if [ ! -d /.git ]; then
    echo "[mios-pull-inline] /.git missing -- dev VM root is not yet a mios.git working tree"
    echo "[mios-pull-inline]   (this is normal pre-build; bootstrap's mios-build-driver"
    echo "[mios-pull-inline]    will git-init / and overlay mios.git on the next build)"
    exit 0
fi
echo "[mios-pull-inline] git -C / fetch --depth=1 origin main ..."
sudo git -C / fetch --depth=1 origin main 2>&1 | sed 's/^/    /'
echo "[mios-pull-inline] git -C / reset --hard FETCH_HEAD ..."
sudo git -C / reset --hard FETCH_HEAD 2>&1 | sed 's/^/    /'
_head=`$(sudo git -C / rev-parse --short HEAD 2>/dev/null || true)
echo "[mios-pull-inline] / now at origin/main HEAD = `${_head}"
'@
`$inlinePullLf = `$inlinePull.Replace("``r``n", "``n")
`$inlinePullLf | wsl.exe -d (Resolve-MiosDevDistro) --user mios -- bash -s -- @args
"@ -Encoding UTF8

    $bootstrapBuild = Join-Path $MiosRepoDir 'mios-bootstrap\build-mios.ps1'
    $updatePath = Join-Path $MiosBinDir 'mios-update.ps1'
    $updateScript = @"
# <MiOSRoot>\bin\mios-update.ps1 -- self-updating bootstrap re-runner.
# Fetches latest mios-bootstrap from origin/main, re-overlays it onto
# M:\, then re-runs build-mios.ps1 with whatever args were passed.
`$ErrorActionPreference = 'SilentlyContinue'
`$shadow      = "$MiosBootstrapShadow"
`$repoDir     = "$MiosRepoDir"
`$bootstrapBs = "$bootstrapBuild"

if (Test-Path (Join-Path `$shadow '.git')) {
    Write-Host '  [mios update] Fetching latest mios-bootstrap from origin/main...' -ForegroundColor Cyan
    Push-Location `$shadow
    try {
        & git remote set-url origin '$MiosBootstrapUrl' 2>&1 | Out-Null
        & git fetch --depth=1 origin main 2>&1 | Out-Null
        if (`$LASTEXITCODE -eq 0) {
            & git reset --hard FETCH_HEAD 2>&1 | Out-Null
            if (`$LASTEXITCODE -eq 0) {
                Write-Host '  [mios update] mios-bootstrap shadow updated to origin/main HEAD.' -ForegroundColor Green
            } else {
                Write-Host '  [mios update] git reset failed; running with possibly-stale shadow.' -ForegroundColor Yellow
            }
        } else {
            Write-Host '  [mios update] git fetch failed (offline?); running with possibly-stale shadow.' -ForegroundColor Yellow
        }
    } finally { Pop-Location }

    # 2. Re-overlay shadow onto M:\ so the build-mios.ps1 we run is
    #    the fresh one. /XD .git keeps mios.git's .git intact.
    Write-Host '  [mios update] Re-overlaying mios-bootstrap files onto M:\...' -ForegroundColor Cyan
    & robocopy `$shadow `$repoDir /E /XD .git /XF mios.toml /NJH /NJS /NFL /NDL /NP 2>&1 | Out-Null
} else {
    Write-Host "  [mios update] No mios-bootstrap shadow at `$shadow -- running local build-mios.ps1 as-is." -ForegroundColor Yellow
}

# 3. Re-run build-mios.ps1 (now refreshed) with all forwarded args.
if (Test-Path `$bootstrapBs) {
    & pwsh.exe -NoProfile -File `$bootstrapBs @args
} else {
    Write-Host "  [mios update] build-mios.ps1 not found at `$bootstrapBs" -ForegroundColor Red
    Write-Host "  [mios update] Re-paste the canonical irm|iex one-liner to recover:" -ForegroundColor Yellow
    Write-Host '    powershell -ExecutionPolicy Bypass -Command "irm $($MiosBootstrapRaw)/Get-MiOS.ps1 | iex"' -ForegroundColor DarkGray
}
"@
    Set-Content -Path $updatePath -Value $updateScript -Encoding UTF8

    $cfgPath = Join-Path $MiosBinDir 'mios-config.ps1'
    $_shadowCfg = (Join-Path $MiosBootstrapShadow 'usr\share\mios\configurator\mios.html') -replace '\\','\\'
    $_legacyCfg = (Join-Path $MiosShareDir 'mios\usr\share\mios\configurator\mios.html') -replace '\\','\\'
    $cfgScript = @"
`$_candidates = @(
    'M:\usr\share\mios\configurator\mios.html',
    "$_shadowCfg",
    "$_legacyCfg"
)
`$_html = `$null
foreach (`$_c in `$_candidates) { if (`$_c -and (Test-Path -LiteralPath `$_c)) { `$_html = `$_c; break } }
if (`$_html) { Start-Process `$_html }
else {
    Write-Host "MiOS configurator HTML not found. Tried:" -ForegroundColor Yellow
    foreach (`$_c in `$_candidates) { Write-Host "  `$_c" -ForegroundColor DarkGray }
    Write-Host "Run 'mios update' to refresh the M:\ overlay." -ForegroundColor DarkGray
}
"@
    Set-Content -Path $cfgPath -Value $cfgScript -Encoding UTF8

    # mios-help.ps1 -- comprehensive help / verb listing. Standalone
    # script (not just the M:\ profile's mios-help function) so the
    # `MiOS Help.lnk` Start Menu shortcut can target it. The script
    # uses the same MiOS palette as the rest of the surface.
    $helpPath   = Join-Path $MiosBinDir 'mios-help.ps1'
    $helpScript = @'
# <MiOSRoot>\bin\mios-help.ps1 -- the `mios help` verb.
# Comprehensive verb + functionality listing. Run from any MiOS
# terminal (`mios help` or click the MiOS Help Start Menu shortcut).
$ErrorActionPreference = 'SilentlyContinue'

# Color palette -- mirrors mios.toml [colors]. Hardcoded fallbacks
# so this script works even when mios.toml isn't yet on disk.
$accent = 'Cyan'      # operator blue (#1A407F)
$muted  = 'DarkGray'  # silver
$ok     = 'Green'     # wave green
$warn   = 'Yellow'    # sunset orange

function Header {
    param([string]$T, [string]$Sub = '')
    Write-Host ''
    Write-Host "  $T" -ForegroundColor $accent
    Write-Host ('  ' + ((([char]0x2500).ToString()) * [math]::Min(76, $T.Length + 4))) -ForegroundColor $muted
    if ($Sub) { Write-Host "  $Sub" -ForegroundColor $muted; Write-Host '' }
}

function Verb {
    param([string]$V, [string]$D)
    Write-Host ('  {0,-12} {1}' -f $V, $D) -ForegroundColor White
}

function Note {
    param([string]$T)
    Write-Host "  $T" -ForegroundColor $muted
}

try { Clear-Host } catch {}
Write-Host ''
Write-Host ("  $([char]0x256D)" + ("$([char]0x2500)" * 74) + "$([char]0x256E)") -ForegroundColor $accent
Write-Host "  $([char]0x2502)                   MiOS  --  Help / Verb Reference                        $([char]0x2502)" -ForegroundColor $accent
# Tagline resolves through mios.toml [branding].tagline_long at runtime
# (SSOT). No hardcoding -- per operator: "no hardcoding ANYWHERE".
$_helpTagline = 'Immutable Fedora AI Workstation  --  Self-replicating bootc OS'
foreach ($_tcand in @("$env:USERPROFILE\.config\mios\mios.toml",'M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
    # C:\MiOS deliberately excluded -- dev working tree, not consumer path
    if (Test-Path -LiteralPath $_tcand) {
        try {
            $_tt = [IO.File]::ReadAllText($_tcand, (New-Object System.Text.UTF8Encoding($false)))
            $_m = [regex]::Match($_tt, '(?ms)^\[branding\].*?^\s*tagline_long\s*=\s*"([^"]+)"')
            if ($_m.Success) { $_helpTagline = $_m.Groups[1].Value; break }
        } catch {}
    }
}
$_helpTagPad = $_helpTagline.PadRight(72).Substring(0, [math]::Min(72, $_helpTagline.Length))
Write-Host ("  $([char]0x2502)   " + $_helpTagPad.PadRight(72) + "   $([char]0x2502)") -ForegroundColor $accent
Write-Host ("  $([char]0x2570)" + ("$([char]0x2500)" * 74) + "$([char]0x256F)") -ForegroundColor $accent

Header 'Core verbs' 'Type any of these in a MiOS terminal, OR click the matching Start Menu shortcut.'
Verb 'mios'         '(no arg) -- open this help; runs `mios help` by default'
Verb 'mios build'   'Promote Downloads edits, sync the overlay, SSH into MiOS-DEV,'
Note '               ignite mios-build-driver -- the full OCI build pipeline.'
Verb 'mios code'    'Open code-server (VS Code in a browser) at http://localhost:8800/.'
Note '               Login: mios. Terminal pre-rooted at your home; `git clone'
Note '               http://mios-forge:3000/<user>/<repo>.git` works in-browser.'
Verb 'mios config'  'Open the HTML configurator (mios.toml editor) in your browser.'
Note '               Edit identity, AI, packages, ports, services, theme, etc.'
Verb 'mios dash'    'Render the framed MiOS dashboard (banner + fastfetch + verbs).'
Verb 'mios dev'     'Drop into the MiOS-DEV podman machine as user `mios` at /.'
Verb 'mios pull'    'git fetch + hard reset M:\ overlay to origin/main (no rebuild).'
Verb 'mios update'  'Re-run the bootstrap (cache-busted) -- refresh terminal + dev VM.'
Verb 'mios help'    'This list.'
Note ''
Verb 'mios <Q>'     'Free-form chat with Hermes-Agent. Anything that is not a known'
Note '               verb is sent to http://localhost:8642/v1/chat/completions and'
Note '               the response streamed to the terminal.'
Note '               Example: mios what kargs do I need for VFIO passthrough'

Header 'Native Windows apps' 'The five-app MiOS surface (Start Menu + Desktop).'
Verb 'MiOS'         'The MiOS terminal. Themed Windows Terminal MiOS profile, 80x20,'
Note '                acrylic 50%%, MiOS color palette, oh-my-posh + dashboard on'
Note '                every launch. Right-click -> Pin to Start / Pin to Taskbar.'
Verb 'MiOS-DEV'     'Drops you into the MiOS-DEV podman machine immediately.'
Verb 'MiOS Config'  'Opens mios.html (the configurator) in the default browser.'
Note '                Edit identity, AI, packages, ports, services, theme. Save'
Note '                writes mios.toml to %USERPROFILE%\Downloads; `mios build`'
Note '                step 2 promotes it into the M:\ overlay automatically.'
Verb 'MiOS Help'    'This help screen, as a clickable app.'
Verb 'Uninstall MiOS' 'Remove MiOS (preserves per-user config).'

Header 'How MiOS is laid out' 'Every operator-tunable value lives in mios.toml.'
Note '   M:\                            Data partition (256 GB NTFS, label MIOS-DEV)'
Note '   M:\MiOS\bin\                   Verb scripts (mios-build, mios-dev, mios-help, ...)'
Note '   M:\MiOS\repo\mios              The mios.git working tree (`.git IS /` on deploy)'
Note '   M:\MiOS\repo\mios-bootstrap    The mios-bootstrap.git working tree'
Note '   M:\etc\mios\mios.toml          Host overlay (operator overrides)'
Note '   M:\usr\share\mios\mios.toml    Vendor SSOT (default values)'
Note '   M:\MiOS\themes\mios.omp.json   oh-my-posh theme (MiOS palette)'
Note '   M:\MiOS\powershell\profile.ps1 PowerShell profile (dashboard + oh-my-posh init)'
Note '   M:\MiOS\fastfetch\config.jsonc fastfetch theme (banner + system info)'

Header 'The Day-0 -> Day-N self-replication flow'
Note '   Day-0 (Windows host): irm | iex Get-MiOS.ps1'
Note '          -> ack + M:\ provision + Podman Desktop + MiOS-DEV machine'
Note '          -> install Windows Terminal + pwsh 7 + Geist Mono + oh-my-posh'
Note '          -> register MiOS as a native Windows app (Start Menu + Desktop)'
Note '          -> STOP. Operator opens the MiOS app and types `mios build`.'
Note ''
Note '   `mios build` (operator-typed) -- triggers the full build pipeline:'
Note '          -> promote Downloads/mios.toml edits to M:\etc\mios\mios.toml'
Note '          -> mios-pull (sync M:\ to origin/main)'
Note '          -> SSH into MiOS-DEV -> /usr/libexec/mios/mios-build-driver'
Note '          -> overlay -> account -> install -> smoketest -> build -> deploy'
Note '          -> bootc switch + reboot -> MiOS-DEV IS MiOS (full parity)'
Note ''
Note '   Day-N (inside MiOS-DEV / any Fedora bootc host):'
Note '          -> dev environment runs INSIDE MiOS-DEV (Epiphany via WSLg)'
Note '          -> dual-push: local Forgejo + GitHub -> CI/CD -> bootc switch'
Note '          -> test deployments: Hyper-V, WSL2/g, QEMU, OCI, ISO, USB, RAW'

Header 'Architectural laws (every contribution obeys these)'
Note '   1. USR-OVER-ETC          static config in /usr/lib, /etc is admin-override only'
Note '   2. NO /VAR WRITES AT BUILD   tmpfiles.d realises /var at first boot'
Note '   3. GIT-MANAGED ROOT      `.git` IS `/` on every deployed host'
Note '   4. BOOTC-CONTAINER-LINT  every build ends with `bootc container lint`'
Note '   5. UNIFIED-AI-REDIRECTS  every OpenAI-API client targets MIOS_AI_ENDPOINT'
Note '                            (default http://localhost:8642/v1 -- Hermes-Agent)'
Note '   6. UNPRIVILEGED-QUADLETS every Quadlet declares User=, Group=, Delegate=yes'

Header 'Where to dig deeper'
Note '   mios.html    /usr/share/mios/configurator/mios.html   (HTML editor for mios.toml)'
Note '   AGENTS.md    M:\MiOS\repo\mios\AGENTS.md              (canonical agents.md doc)'
Note '   README.md    M:\MiOS\repo\mios\README.md              (project overview)'
Note "   GitHub       $($MiosRepoUrl -replace '\.git$','')"
Note ''
Note '   Press any key to close...'
[void]([System.Console]::ReadKey($true))
'@
    Set-Content -Path $helpPath -Value $helpScript -Encoding UTF8
    Log-Ok "mios-help.ps1 (full verb + functionality reference) staged at $helpPath"

    $buildPath  = Join-Path $MiosBinDir 'mios-build.ps1'
    $miosEtcDir = Join-Path $MiosRepoDir 'etc\mios'
    $miosShareDirInRepo = Join-Path $MiosRepoDir 'mios\usr\share\mios'
    $buildScript = @"
$devResolveBlock
`$ErrorActionPreference = 'Continue'
`$downloads = Join-Path `$env:USERPROFILE 'Downloads'
`$promoteTargets = @(
    @{ Pattern = 'mios*.toml';  TargetDir = "$miosEtcDir";       Filename = 'mios.toml' }
    @{ Pattern = '*mios*.html'; TargetDir = "$miosShareDirInRepo\configurator"; Filename = 'mios.html' }
)
`$promoted = `$false
foreach (`$pt in `$promoteTargets) {
    `$candidates = Get-ChildItem -Path `$downloads -Filter `$pt.Pattern -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
    if (-not `$candidates) { continue }
    `$src = `$candidates[0]
    if (-not (Test-Path `$pt.TargetDir)) {
        New-Item -ItemType Directory -Path `$pt.TargetDir -Force | Out-Null
    }
    `$dst = Join-Path `$pt.TargetDir `$pt.Filename
    Copy-Item -Path `$src.FullName -Destination `$dst -Force
    `$ts  = Get-Date -Format 'yyyyMMdd-HHmmss'
    `$archive = Join-Path `$src.DirectoryName ("{0}.imported-{1}" -f `$src.Name, `$ts)
    Move-Item -Path `$src.FullName -Destination `$archive -Force
    Write-Host ("  [promote] {0} -> {1}" -f `$src.Name, `$dst) -ForegroundColor Green
    Write-Host ("            archived as {0}" -f (Split-Path `$archive -Leaf)) -ForegroundColor DarkGray
    `$promoted = `$true
}
if (-not `$promoted) {
    Write-Host '  [promote] no mios*.toml / *mios*.html in Downloads -- proceeding with current overlay' -ForegroundColor DarkGray
}

`$miosRoot = 'M:\'
if ((Test-Path (Join-Path `$miosRoot '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host '  [pull] Windows-side: git fetch + reset --hard origin/main on M:\...' -ForegroundColor Cyan
    try {
        & git -C `$miosRoot fetch --depth=1 origin main 2>&1 | ForEach-Object { Write-Host ('    ' + `$_) -ForegroundColor DarkGray }
        if (`$LASTEXITCODE -eq 0) {
            & git -C `$miosRoot reset --hard origin/main 2>&1 | ForEach-Object { Write-Host ('    ' + `$_) -ForegroundColor DarkGray }
            if (`$LASTEXITCODE -eq 0) {
                `$_head = (& git -C `$miosRoot rev-parse --short HEAD 2>`$null)
                Write-Host ('  [pull] M:\ now at origin/main HEAD = ' + `$_head) -ForegroundColor Green
            } else {
                Write-Host '  [pull] M:\ git reset --hard failed -- build will run against possibly-stale overlay' -ForegroundColor Yellow
            }
        } else {
            Write-Host '  [pull] M:\ git fetch failed (offline?) -- build will run against possibly-stale overlay' -ForegroundColor Yellow
        }
    } catch {
        Write-Host ('  [pull] M:\ git refresh threw: ' + `$_.Exception.Message) -ForegroundColor Yellow
    }
} else {
    Write-Host '  [pull] M:\ is not a git working tree OR git is missing -- skipping Windows-side refresh' -ForegroundColor Yellow
}

# Now refresh the dev VM root (/) via mios-pull.ps1.
`$pull = Join-Path `$PSScriptRoot 'mios-pull.ps1'
if (Test-Path `$pull) {
    Write-Host '  [pull] dev VM: syncing / overlay to origin/main...' -ForegroundColor Cyan
    & pwsh.exe -NoProfile -File `$pull
} else {
    Write-Host '  [pull] mios-pull.ps1 not found -- skipping dev VM pull, build will run against staged dev tree' -ForegroundColor Yellow
}

`$distro = Resolve-MiosDevDistro
`$podmanMachine = `$distro -replace '^podman-', ''
Write-Host ''
Write-Host ('  [build] pre-warming WSL distro {0} ...' -f `$distro) -ForegroundColor DarkGray
try {
    & wsl.exe -d `$distro --user mios -- true 2>&1 | Out-Null
} catch {}

Write-Host ('  [build] starting WSL-Podman machine: {0} ...' -f `$podmanMachine) -ForegroundColor Cyan
try {
    & podman machine start `$podmanMachine 2>&1 | ForEach-Object {
        `$line = `$_.ToString()
        # Filter noise. "already running" is happy-path; "machine did
        # not transition into running state" is a known false-positive
        # on WSL-backed machines when the api-proxy pipe is slow to
        # surface -- the distro is up, the daemon is reachable,
        # podman's own state cache just hasn't caught up yet.
        if (`$line -match 'is already running') {
            Write-Host '    (machine already running)' -ForegroundColor DarkGray
        } elseif (`$line -match 'machine did not transition into running state' -or
                  `$line -match 'could not start api proxy since expected pipe is not available' -or
                  `$line -match 'API forwarding for Docker API clients is not available') {
            Write-Host ('    (non-fatal: ' + `$line + ')') -ForegroundColor DarkGray
        } else {
            Write-Host ('    ' + `$line) -ForegroundColor DarkGray
        }
    }
} catch {
    Write-Host ('  [build] podman machine start threw: ' + `$_.Exception.Message) -ForegroundColor Yellow
    Write-Host '  [build] continuing -- wsl.exe -d already has the distro live; podman daemon should be reachable inside it.' -ForegroundColor Yellow
}

# Brief settling pause so podman API socket is reachable before the
# build driver's first `podman ...` invocation.
Start-Sleep -Milliseconds 800

`$driverSrc = '/mnt/m/usr/libexec/mios/mios-build-driver'
Write-Host ('  [build] staging mios-build-driver into {0}:/usr/libexec/mios/' -f `$distro) -ForegroundColor DarkGray
& wsl.exe -d `$distro --user root -- bash -c "mkdir -p /usr/libexec/mios && if [ -r '`$driverSrc' ]; then cp '`$driverSrc' /usr/libexec/mios/mios-build-driver && chmod +x /usr/libexec/mios/mios-build-driver && echo '[stage] driver staged from `$driverSrc'; else echo '[stage] WARN: `$driverSrc not readable from inside `$distro -- falling back to curl'; curl -fsSL -o /usr/libexec/mios/mios-build-driver '$MiosRawBase/usr/libexec/mios/mios-build-driver' && chmod +x /usr/libexec/mios/mios-build-driver; fi"

Write-Host ''
Write-Host ('  [build] handing off to {0}:/usr/libexec/mios/mios-build-driver' -f `$distro) -ForegroundColor Cyan
Write-Host '  [build] (this builds the OCI image inside MiOS-DEV; first run takes 10-30 min)' -ForegroundColor DarkGray
Write-Host ''
& wsl.exe -d `$distro --user mios --cd / -- bash -lc '/usr/libexec/mios/mios-build-driver'
`$_drc = `$LASTEXITCODE
if (`$_drc -ne 0) {
    Write-Host ('  [build] OCI image build FAILED inside {0} (exit {1}) -- MiOS AI will NOT be operational until this build succeeds; see the log above.' -f `$distro, `$_drc) -ForegroundColor Red
    exit `$_drc
}
Write-Host '  [build] OCI image build completed OK.' -ForegroundColor Green
# Install-robustness run the post-bootstrap acceptance smoke now that
# the image built -- it was authored to run "at the end of mios build" but was
# never wired in, so the AI-plane "is it operational?" check never executed. The
# smoke is best-effort here (non-fatal): a still-warming AI plane warns rather
# than failing the build verb; `mios smoke` re-runs it on demand.
`$_smoke = '/mnt/m/tests/post-bootstrap-smoke.sh'
if ((& wsl.exe -d `$distro --user root -- bash -lc "test -f `$_smoke && echo y" 2>`$null) -eq 'y') {
    Write-Host '  [build] running post-bootstrap acceptance smoke (AI-plane + parity)...' -ForegroundColor Cyan
    & wsl.exe -d `$distro --user root -- bash `$_smoke
    if (`$LASTEXITCODE -ne 0) { Write-Host '  [build] smoke reported issues (see above) -- re-run with: mios smoke' -ForegroundColor Yellow }
} else {
    Write-Host '  [build] smoke script not found at M:\tests -- skipping (run: mios smoke)' -ForegroundColor DarkGray
}
"@
    Set-Content -Path $buildPath -Value $buildScript -Encoding UTF8
    Log-Ok "mios-build.ps1 (the `mios build` verb) staged at $buildPath"

    $hubPath   = Join-Path $MiosBinDir 'mios.ps1'
    $hubScript = @'
$ErrorActionPreference = 'SilentlyContinue'
$Script:MiOSBin  = $PSScriptRoot
$Script:MiOSRoot = Split-Path -Parent $Script:MiOSBin

# Canonical profile body (Show-MiosDashboard + mios <verb> dispatcher).
$_miosProfile = Join-Path $Script:MiOSRoot 'powershell\profile.ps1'
if (Test-Path -LiteralPath $_miosProfile) {
    try { . $_miosProfile } catch {
        Write-Host "  [!] Failed to load $_miosProfile : $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

if ($args.Count -gt 0) {
    $verb = $args[0]
    $rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }
    if (Get-Command mios -ErrorAction SilentlyContinue) {
        & mios $verb @rest
    } else {
        # Fallback when the profile didn't load: invoke the per-verb
        # bin script directly.
        $vScript = Join-Path $Script:MiOSBin "mios-$verb.ps1"
        if (Test-Path -LiteralPath $vScript) {
            & $vScript @rest
        } else {
            Write-Host "  [!] mios verb '$verb' not found ($vScript)." -ForegroundColor Yellow
        }
    }
}
'@
    Set-Content -Path $hubPath -Value $hubScript -Encoding UTF8
    Log-Ok "MiOS app staged at $hubPath"
    # CMD is also the default Windows OpenSSH shell. A machine-PATH .cmd must
    # exist independently of the user's PowerShell profile, including Xbox.
    $_nativeSetup = Join-Path $MiosRepoDir 'usr\share\mios\windows\mios-native-client-setup.ps1'
    if (-not (Test-Path -LiteralPath $_nativeSetup)) { throw "Native MiOS CMD setup missing: $_nativeSetup" }
    $null = Install-MiosNativeCatalog -Root $MiosRepoDir -Machine $DevDistro
    & $_nativeSetup -Distro $DevDistro -BinDirectory $MiosBinDir -SourceRoot $MiosRepoDir
    if (-not $?) { throw 'Native MiOS CMD/client setup failed' }

    # mios-code.ps1 -- `mios code` verb. Opens code-server in the
    # operator's default browser.
    $codePath = Join-Path $MiosBinDir 'mios-code.ps1'
    $codeScript = @'
# <MiOSRoot>\bin\mios-code.ps1 -- the `mios code` verb.
# Opens code-server (VS Code in a browser) in the default browser.
# Resolves the URL via mios.toml [ports].code_server (default 8800).
param([Parameter(ValueFromRemainingArguments)] $Args)
$ErrorActionPreference = 'SilentlyContinue'
$port = 8800
foreach ($_t in @("$env:USERPROFILE\.config\mios\mios.toml",'M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
    if (Test-Path -LiteralPath $_t) {
        try {
            $_txt = [IO.File]::ReadAllText($_t, (New-Object System.Text.UTF8Encoding($false)))
            $_m = [regex]::Match($_txt, '(?ms)^\[ports\].*?^\s*code_server\s*=\s*(\d+)')
            if ($_m.Success) { $port = [int]$_m.Groups[1].Value; break }
        } catch {}
    }
}
$url = "http://localhost:$port/"
Write-Host "  Opening $url (login: mios)" -ForegroundColor DarkGray
Start-Process $url | Out-Null
'@
    Set-Content -Path $codePath -Value $codeScript -Encoding UTF8

    # mios-ai.ps1 -- enter the native workspace in this terminal.
    $aiPath = Join-Path $MiosBinDir 'mios-ai.ps1'
    $aiScript = @'
# AI-hint: Legacy Windows mios-ai command enters the native AI workspace in the invoking terminal.
# AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, build-mios.ps1
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$nativeBin = if ($env:MIOS_NATIVE_BIN) { $env:MIOS_NATIVE_BIN } else { Join-Path $env:ProgramData 'MiOS\bin' }
$entry = Join-Path $nativeBin 'mios-native-entry.ps1'
if (-not (Test-Path -LiteralPath $entry)) { throw 'MiOS native terminal entrypoint is missing; run the MiOS native client setup.' }
& $entry ai @Arguments
'@
    Set-Content -Path $aiPath -Value $aiScript -Encoding UTF8

    # mios-mon.ps1 -- the `mios mon` verb.
    $monPath = Join-Path $MiosBinDir 'mios-mon.ps1'
    $monScript = @'
# <MiOSRoot>\bin\mios-mon.ps1 -- the `mios mon` verb.
param([Parameter(ValueFromRemainingArguments)] $Args)
$py = if (Get-Command python.exe -ErrorAction SilentlyContinue) { 'python.exe' } else { 'python3' }
$mon = @('M:\usr\libexec\mios\mios-mon.py','C:\MiOS\usr\libexec\mios\mios-mon.py','M:\MiOS\repo\mios\usr\libexec\mios\mios-mon.py') | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($mon) {
    if ($Args.Count -eq 0) {
        & $py $mon --monitor
    } else {
        & $py $mon @Args
    }
} else {
    Write-Host "  [!] mios mon: mios-mon.py not found on M:\ or C:\" -ForegroundColor Yellow
}
'@
    Set-Content -Path $monPath -Value $monScript -Encoding UTF8

    $monitorPath = Join-Path $MiosBinDir 'mios-monitor.ps1'
    $monitorScript = @'
# <MiOSRoot>\bin\mios-monitor.ps1 -- alias for `mios mon` verb.
param([Parameter(ValueFromRemainingArguments)] $Args)
& (Join-Path $PSScriptRoot 'mios-mon.ps1') @Args
'@
    Set-Content -Path $monitorPath -Value $monitorScript -Encoding UTF8

    # System verbs -- forward to the dev VM via wsl.exe.
    $systemVerbs = @('xbox','virt','vfio','tune','summary','profile','assess','iommu','theme','user')
    foreach ($v in $systemVerbs) {
        $vPath = Join-Path $MiosBinDir "mios-$v.ps1"
        $vScript = @"
# <MiOSRoot>\bin\mios-$v.ps1 -- the `mios $v` verb.
# Forwards the command to the MiOS-DEV WSL distro.
param([Parameter(ValueFromRemainingArguments)] `$Args)
`$_distro = 'podman-MiOS-DEV'
try {
    `$_wsl = (& wsl.exe -l -q 2>`$null) -split "``r?`n" | ForEach-Object { (`$_ -replace [char]0,'').Trim() } | Where-Object { `$_ }
    foreach (`$_cand in @('podman-MiOS-DEV','MiOS-DEV')) {
        if (`$_wsl -contains `$_cand) { `$_distro = `$_cand; break }
    }
} catch {}
& wsl.exe -d `$_distro --user mios -- mios $v @Args
"@
        Set-Content -Path $vPath -Value $vScript -Encoding UTF8
    }

    # mios-ask.ps1 -- free-form Hermes-Agent chat from the Windows
    # PowerShell terminal. Invoked by the `mios` dispatcher whenever
    # the first arg isn't a known verb. POSTs to MIOS_AI_ENDPOINT
    # (default http://localhost:8642/v1) and streams the assistant
    # content to the console.
    $askPath = Join-Path $MiosBinDir 'mios-ask.ps1'
    $askScript = @'
# <MiOSRoot>\bin\mios-ask.ps1 -- `mios <query>` chat against Hermes-Agent.
param([Parameter(ValueFromRemainingArguments)] [string[]] $Q)
$ErrorActionPreference = 'SilentlyContinue'
if (-not $Q -or $Q.Count -eq 0) {
    Write-Host "  Usage: mios <question or instruction>" -ForegroundColor Yellow
    Write-Host "  Example: mios how do I bootc switch to a staged image" -ForegroundColor DarkGray
    return
}
$query = ($Q -join ' ').Trim()
if (-not $query) { return }

# Resolution is per field. Model: MIOS_AI_MODEL env, else the layered
# mios.toml [ai].model (the SSOT default chat model), else a vendor fallback
# -- so an unset env never pins a stale model id. Key: MIOS_AI_KEY env, else
# install.env. Endpoint: MIOS_AI_ENDPOINT env, else resolved from mios.toml [ports].hermes.
$hermesPort = 8642
foreach ($_t in @("$env:USERPROFILE\.config\mios\mios.toml",'M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
    if (Test-Path -LiteralPath $_t) {
        try {
            $_txt = [IO.File]::ReadAllText($_t, (New-Object System.Text.UTF8Encoding($false)))
            $_m = [regex]::Match($_txt, '(?ms)^\[ports\].*?^\s*hermes\s*=\s*(\d+)')
            if ($_m.Success) { $hermesPort = [int]$_m.Groups[1].Value; break }
        } catch {}
    }
}
$endpoint = if ($env:MIOS_AI_ENDPOINT) { $env:MIOS_AI_ENDPOINT } else { "http://localhost:$hermesPort/v1" }
$model    = if ($env:MIOS_AI_MODEL)    { $env:MIOS_AI_MODEL }    else { '' }
$apiKey   = if ($env:MIOS_AI_KEY)      { $env:MIOS_AI_KEY }      else { '' }

# If no env model, resolve [ai].model from the layered mios.toml (SSOT for the
# default chat model). Mirrors the open_webui port scrape in the mios app verb;
# the literal is only the bottom-of-stack fallback if every layer is unreadable.
if (-not $model) {
    foreach ($_t in @("$env:USERPROFILE\.config\mios\mios.toml",'M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
        if (Test-Path -LiteralPath $_t) {
            try {
                $_txt = [IO.File]::ReadAllText($_t, (New-Object System.Text.UTF8Encoding($false)))
                $_m = [regex]::Match($_txt, '(?ms)^\[ai\].*?^\s*model\s*=\s*"?([^"\r\n]+)"?')
                if ($_m.Success) { $model = $_m.Groups[1].Value.Trim(); break }
            } catch {}
        }
    }
}
if (-not $model) { $model = 'qwen3.5:2b' }

# If no env key, scrape /etc/mios/install.env on M:\ for the key.
if (-not $apiKey) {
    foreach ($_e in @('M:\etc\mios\install.env','M:\etc\mios\hermes\api.env')) {
        if (Test-Path -LiteralPath $_e) {
            try {
                $_txt = [IO.File]::ReadAllText($_e, (New-Object System.Text.UTF8Encoding($false)))
                $_m = [regex]::Match($_txt, '(?m)^(?:API_SERVER_KEY|MIOS_AI_KEY|OPENAI_API_KEY)\s*=\s*"?([^"\r\n]+)"?')
                if ($_m.Success) { $apiKey = $_m.Groups[1].Value.Trim(); break }
            } catch {}
        }
    }
}

$headers = @{ 'Content-Type' = 'application/json' }
if ($apiKey) { $headers['Authorization'] = "Bearer $apiKey" }

$body = @{
    model    = $model
    messages = @(
        @{ role = 'user'; content = $query }
    )
    stream   = $false
} | ConvertTo-Json -Depth 8 -Compress

try {
    $resp = Invoke-RestMethod -Method Post -Uri "$endpoint/chat/completions" -Headers $headers -Body $body -TimeoutSec 120 -ErrorAction Stop
    $content = $resp.choices[0].message.content
    if ($content) {
        Write-Host ''
        Write-Host $content
        Write-Host ''
    } else {
        Write-Host "  [!] Hermes returned an empty response." -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [!] mios ask: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  Hermes-Agent endpoint: $endpoint" -ForegroundColor DarkGray
    Write-Host "  Is mios-hermes.service running? Check with: mios dash" -ForegroundColor DarkGray
}
'@
    Set-Content -Path $askPath -Value $askScript -Encoding UTF8

    Log-Ok "Bin scripts staged: mios (app), mios-dash, mios-dev, mios-pull, mios-update, mios-config, mios-code, mios-ask"

    # Also drop a VERSION file so mios-dash can render the current ver.
    Set-Content -Path (Join-Path $MiosInstallDir 'VERSION') -Value $MiosVersion.TrimStart('v') -Encoding UTF8

    # -- 3. PowerShell profile: mios-* functions (idempotent block) ----
    $profilePath = $PROFILE.CurrentUserAllHosts
    if (-not $profilePath) { $profilePath = $PROFILE }
    $profileDir  = Split-Path $profilePath -Parent
    if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }
    $existing = if (Test-Path $profilePath) { Get-Content $profilePath -Raw } else { '' }
    $marker   = '# >>> MiOS dash function >>>'
    $endMark  = '# <<< MiOS dash function <<<'
    $miosBinForProfile = $MiosBinDir -replace '\\', '\\'
    $dashFn = @"
$marker
`$Global:MiosBin = "$miosBinForProfile"
function mios-dev     { & (Join-Path `$Global:MiosBin 'mios-dev.ps1')     @args }
function mios-pull    { & (Join-Path `$Global:MiosBin 'mios-pull.ps1')    @args }
function mios-update  { & (Join-Path `$Global:MiosBin 'mios-update.ps1')  @args }
function mios-config  { & (Join-Path `$Global:MiosBin 'mios-config.ps1')  @args }
function mios-code    { & (Join-Path `$Global:MiosBin 'mios-code.ps1')    @args }
function mios-mon     { & (Join-Path `$Global:MiosBin 'mios-mon.ps1')     @args }
function mios-monitor { & (Join-Path `$Global:MiosBin 'mios-monitor.ps1') @args }
function mios-ask     { & (Join-Path `$Global:MiosBin 'mios-ask.ps1')     @args }

function Set-MiosWindow {
    [CmdletBinding()]
    param([ValidateSet('portal','reading')][string]`$Mode = 'portal')
    `$_section = if (`$Mode -eq 'reading') { 'terminal.reading' } else { 'terminal' }
    # Read dims from mios.toml (host overlay > vendor SSOT > hardcoded).
    `$_cols = 80; `$_rows = 20
    if (`$Mode -eq 'reading') { `$_cols = 100; `$_rows = 50 }
    foreach (`$_t in @('M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
        if (-not (Test-Path -LiteralPath `$_t)) { continue }
        try {
            `$_txt  = [IO.File]::ReadAllText(`$_t, (New-Object System.Text.UTF8Encoding(`$false)))
            `$_secRx = '(?ms)^\[' + [regex]::Escape(`$_section) + '\][ \t]*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)'
            `$_m = [regex]::Match(`$_txt, `$_secRx)
            if (-not `$_m.Success) { continue }
            `$_body = `$_m.Groups['body'].Value
            `$_mc = [regex]::Match(`$_body, '(?m)^[ \t]*cols[ \t]*=[ \t]*(\d+)')
            `$_mr = [regex]::Match(`$_body, '(?m)^[ \t]*rows[ \t]*=[ \t]*(\d+)')
            if (`$_mc.Success) { `$_cols = [int]`$_mc.Groups[1].Value }
            if (`$_mr.Success) { `$_rows = [int]`$_mr.Groups[1].Value }
            break
        } catch {}
    }
    # Cell + chrome metrics from mios.toml [theme.font] (defaulted to
    # the Geist Mono Nerd Font 12pt baseline if the toml is unreadable
    # at this point).
    `$_cellW = 10; `$_cellH = 20; `$_chromeW = 20; `$_chromeH = 12
    foreach (`$_t in @('M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
        if (-not (Test-Path -LiteralPath `$_t)) { continue }
        try {
            `$_txt = [IO.File]::ReadAllText(`$_t, (New-Object System.Text.UTF8Encoding(`$false)))
            `$_m = [regex]::Match(`$_txt, '(?ms)^\[theme\.font\][ \t]*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
            if (-not `$_m.Success) { continue }
            `$_b = `$_m.Groups['body'].Value
            foreach (`$_kv in @(@('cell_w_px','_cellW'),@('cell_h_px','_cellH'),@('chrome_w_px','_chromeW'),@('chrome_h_px','_chromeH'))) {
                # Build the regex from string concat. Earlier attempt
                # used dollar-paren interpolation but the outer build-
                # mios.ps1 heredoc evaluated it at install time -- it
                # tried to look up _kv at build-time scope and crashed
                # the bootstrap. Concat avoids any subexpression form.
                `$_pat = '(?m)^[ \t]*' + `$_kv[0] + '[ \t]*=[ \t]*(\d+)'
                `$_x   = [regex]::Match(`$_b, `$_pat)
                if (`$_x.Success) { Set-Variable -Name `$_kv[1] -Value ([int]`$_x.Groups[1].Value) }
            }
            break
        } catch {}
    }
    `$_winW = `$_cols * `$_cellW + `$_chromeW
    `$_winH = `$_rows * `$_cellH + `$_chromeH

    # Win32 helpers + Cursor.Position + Screen.FromPoint for centering
    # on the monitor that currently hosts the cursor.
    try {
        Add-Type -Namespace 'MiOSResize' -Name 'W' -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetWindowPos(System.IntPtr hWnd, System.IntPtr h, int x, int y, int cx, int cy, uint flags);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool IsWindowVisible(System.IntPtr hWnd);
'@ -ErrorAction SilentlyContinue
    } catch {}
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    `$_cur  = [System.Windows.Forms.Cursor]::Position
    `$_work = [System.Windows.Forms.Screen]::FromPoint(`$_cur).WorkingArea
    `$_x = [int](`$_work.X + (`$_work.Width  - `$_winW) / 2); if (`$_x -lt `$_work.X) { `$_x = `$_work.X }
    `$_y = [int](`$_work.Y + (`$_work.Height - `$_winH) / 2); if (`$_y -lt `$_work.Y) { `$_y = `$_work.Y }

    # Resolve the WT process hosting THIS pwsh. Walk up the parent
    # chain via WMI since pwsh runs as a CHILD of WindowsTerminal.exe;
    # Get-Process -Name WindowsTerminal could return any WT window,
    # not necessarily ours.
    `$_hwnd = [IntPtr]::Zero
    try {
        `$_pid = `$PID
        for (`$_i = 0; `$_i -lt 6; `$_i++) {
            `$_proc = Get-CimInstance Win32_Process -Filter "ProcessId=`$_pid" -ErrorAction SilentlyContinue
            if (-not `$_proc) { break }
            if (`$_proc.Name -match '^WindowsTerminal') {
                `$_p = Get-Process -Id `$_proc.ProcessId -ErrorAction SilentlyContinue
                if (`$_p -and `$_p.MainWindowHandle -ne [IntPtr]::Zero) { `$_hwnd = `$_p.MainWindowHandle; break }
            }
            `$_pid = `$_proc.ParentProcessId
        }
    } catch {}
    if (`$_hwnd -eq [IntPtr]::Zero) {
        # Fallback: newest visible WT window. Acceptable for the
        # common case (one MiOS window open).
        `$_p = Get-Process -Name 'WindowsTerminal' -ErrorAction SilentlyContinue |
               Where-Object { `$_.MainWindowHandle -ne [IntPtr]::Zero -and [MiOSResize.W]::IsWindowVisible(`$_.MainWindowHandle) } |
               Sort-Object StartTime -Descending | Select-Object -First 1
        if (`$_p) { `$_hwnd = `$_p.MainWindowHandle }
    }
    if (`$_hwnd -ne [IntPtr]::Zero) {
        # 0x40 = SWP_SHOWWINDOW. No NOZORDER so window comes to front.
        [void][MiOSResize.W]::SetWindowPos(`$_hwnd, [IntPtr]::Zero, `$_x, `$_y, `$_winW, `$_winH, 0x40)
        Write-Host ("  [+] MiOS window: {0} mode ({1}x{2})" -f `$Mode, `$_cols, `$_rows) -ForegroundColor DarkGray
    } else {
        Write-Host '  [!] Could not resolve current WT window handle; resize skipped.' -ForegroundColor Yellow
    }
}

# Verb shorthands for Set-MiosWindow.
function mios-portal  { Set-MiosWindow -Mode portal }
function mios-reading { Set-MiosWindow -Mode reading }

# btop on Windows -> prefer native btop.exe (btop4win) if available;
# otherwise resize window to reading mode and run the dev VM's Linux btop via WSL (UNIFIED).
function btop {
    `$_native = (Get-Command btop.exe -CommandType Application -ErrorAction SilentlyContinue)
    if (`$_native) {
        if (-not `$env:BTOP_CONFIG_DIR) {
            foreach (`$_bDir in @('M:\MiOS\btop', (Join-Path `$env:LOCALAPPDATA 'btop'))) {
                if (Test-Path -LiteralPath `$_bDir) { `$env:BTOP_CONFIG_DIR = `$_bDir; break }
            }
        }
        & `$_native.Source @args
        return
    }
    `$_devCandidates = @('podman-MiOS-DEV','MiOS-DEV','podman-MiOS-BUILDER','MiOS-BUILDER')
    `$_wslList = @()
    try { `$_wslList = (& wsl.exe -l -q 2>`$null) -split "``r?``n" | ForEach-Object { (`$_ -replace [char]0,'').Trim() } | Where-Object { `$_ } } catch {}
    `$_dev = `$null
    foreach (`$_c in `$_devCandidates) {
        if (`$_wslList -contains `$_c) { `$_dev = `$_c; break }
    }
    if (-not `$_dev) {
        Write-Host '  [!] No native btop.exe or MiOS-DEV WSL distro found -- cannot run btop.' -ForegroundColor Yellow
        return
    }
    Set-MiosWindow -Mode reading
    Start-Sleep -Milliseconds 200   # let WT settle the new dims
    try {
        & wsl.exe -d `$_dev --user mios -- btop @args
    } finally {
        Set-MiosWindow -Mode portal
    }
}

# Windows tmux dispatch is rendered and enforced by the native Rust SSOT launcher.
function tmux {
    & (Join-Path `$env:ProgramData 'MiOS\bin\mios-launch.exe') --tmux @args
}
$endMark
"@
    if ($existing -match [regex]::Escape($marker)) {
        $pattern  = "(?s)$([regex]::Escape($marker)).*?$([regex]::Escape($endMark))"
        $safeRepl = $dashFn -replace '\$', '$$$$'
        $existing = [regex]::Replace($existing, $pattern, $safeRepl)
    } else {
        $existing = ($existing.TrimEnd() + "`n`n" + $dashFn + "`n").TrimStart()
    }
    Set-Content -Path $profilePath -Value $existing -Encoding UTF8 -NoNewline
    Log-Ok "PowerShell profile updated with mios-* functions"

    $wtSettings = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
    if (-not (Test-Path $wtSettings)) {
        $wtSettings = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json'
    }
    $hubPathForJson = $hubPath -replace '\\', '\\'
    if (Test-Path $wtSettings) {
        try {
            $wtRaw = Get-Content $wtSettings -Raw
            $wtRaw = [regex]::Replace($wtRaw, '(?ms)/\*.*?\*/', '')
            $wtRaw = [regex]::Replace($wtRaw, '(?m)^\s*//.*$', '')
            $wtRaw = [regex]::Replace($wtRaw, ',(\s*[}\]])', '$1')
            $wtJson = $wtRaw | ConvertFrom-Json

            $miosGuid    = '{a8b5c2d3-e4f5-6789-abcd-ef0123456789}'
            $miosDevGuid = '{a8b5c2d3-e4f5-6789-abcd-ef0123456790}'
            if ($wtJson.profiles -and $wtJson.profiles.list) {
                foreach ($p in $wtJson.profiles.list) {
                    if ($p.guid -eq $miosGuid -or $p.guid -eq $miosDevGuid) {
                        # ICON ONLY -- commandline + startingDirectory
                        # owned by Get-MiOS.ps1's Pass-1 patcher.
                        if ($icoPath -and (-not $p.PSObject.Properties['icon'])) {
                            $p | Add-Member -NotePropertyName icon -NotePropertyValue $icoPath -Force
                        } elseif ($icoPath) {
                            $p.icon = $icoPath
                        }
                    }
                }
                $wtJson | ConvertTo-Json -Depth 32 | Set-Content -Path $wtSettings -Encoding UTF8
                Log-Ok "Windows Terminal MiOS + MiOS-DEV profile icons refreshed (commandline left untouched)"
            } else {
                Log-Warn "Windows Terminal MiOS profile not found (Get-MiOS.ps1 entry didn't run?) -- skipping rebind"
            }
        } catch {
            Log-Warn "Windows Terminal settings.json rebind failed: $($_.Exception.Message)"
        }
    } else {
        Log-Warn "Windows Terminal not installed (no settings.json found) -- launcher will fall back to bare pwsh"
    }

    # -- 5. Desktop primary launcher + Start Menu MiOS folder ----------
    $desktopDir = [Environment]::GetFolderPath('Desktop')
    $shell      = New-Object -ComObject WScript.Shell

    # Resolve toolchain paths once.
    $wtExe   = (Get-Command wt.exe   -ErrorAction SilentlyContinue).Source
    $pwshExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
    if (-not $pwshExe) { $pwshExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source }
    if (-not $pwshExe) {
        Log-Warn "pwsh.exe / powershell.exe not found -- launcher shortcuts skipped"
        return
    }


    $_stagingDrive = if ($env:MIOS_DATA_DISK_LETTER) { $env:MIOS_DATA_DISK_LETTER } else { Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default 'M' }

    $hubResizePrelude = "try { `$H=Get-Host; `$H.UI.RawUI.WindowSize=(New-Object Management.Automation.Host.Size 80,30) } catch {}"
    $miosLauncher = Join-Path $MiosBinDir 'mios-launch.ps1'
    $_psSrcCandidates = @(
        (Join-Path $MiosRepoDir 'src\mios-launch.ps1'),
        (Join-Path $MiosBootstrapShadow 'src\mios-launch.ps1')
    )
    $_psSrc = $null
    foreach ($_c in $_psSrcCandidates) {
        if (Test-Path -LiteralPath $_c) { $_psSrc = $_c; break }
    }
    $launcherSrc = $null
    if ($_psSrc) {
        try { $launcherSrc = [IO.File]::ReadAllText($_psSrc, (New-Object System.Text.UTF8Encoding($false))) } catch {
            Log-Warn "mios-launch.ps1 read failed at ${_psSrc}: $($_.Exception.Message)"
        }
    } else {
        Log-Warn "mios-launch.ps1 source not found in repo (probed: $($_psSrcCandidates -join ', ')) -- launcher will not be staged"
    }
    if ($launcherSrc) {
        # Substitute __MIOS_COLS__ / __MIOS_ROWS__ placeholders from mios.toml
        # [terminal].cols /.rows (SSOT) -- "Toml is the
        # total reference for all functions and calls".
        $_lnchCols = [int](Get-MiosTomlValue -Section 'terminal' -Key 'cols' -Default 80)
        $_lnchRows = [int](Get-MiosTomlValue -Section 'terminal' -Key 'rows' -Default 20)
        $_lnchCellW = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'cell_w_px' -Default 10)
        $_lnchCellH = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'cell_h_px' -Default 20)
        $_lnchChromeW = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_w_px' -Default 20)
        $_lnchChromeH = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_h_px' -Default 12)
        $_lnchScheme = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'scheme_name' -Default 'MiOS')
        $launcherSrc = $launcherSrc -replace '__MIOS_COLS__', [string]$_lnchCols
        $launcherSrc = $launcherSrc -replace '__MIOS_ROWS__', [string]$_lnchRows
        $launcherSrc = $launcherSrc -replace '__MIOS_CELL_W__', [string]$_lnchCellW
        $launcherSrc = $launcherSrc -replace '__MIOS_CELL_H__', [string]$_lnchCellH
        $launcherSrc = $launcherSrc -replace '__MIOS_CHROME_W__', [string]$_lnchChromeW
        $launcherSrc = $launcherSrc -replace '__MIOS_CHROME_H__', [string]$_lnchChromeH
        $launcherSrc = $launcherSrc -replace '__MIOS_SCHEME__', $_lnchScheme
        $launcherSrc = $launcherSrc -replace '__MIOS_DRIVE__', $_stagingDrive
        if (-not (Test-Path $MiosBinDir)) { New-Item -ItemType Directory -Path $MiosBinDir -Force | Out-Null }
        Set-Content -Path $miosLauncher -Value $launcherSrc -Encoding UTF8
        Log-Ok "MiOS native launcher staged: $miosLauncher (cols=$_lnchCols rows=$_lnchRows from mios.toml [terminal])"
    }
    # -- mios-wallpaperd (Rust native living wallpaper + gui-watch daemon, T-1132) --
    $wallpaperd_exe = Join-Path $MiosBinDir 'mios-wallpaperd.exe'
    $svcName = 'MiOS-Wallpaper-Service'
    $null = Install-MiosNativeWindowsArtifact -Root $MiosRepoDir -Machine $DevDistro -Binary 'mios-wallpaperd' -Destination $wallpaperd_exe -ServiceName $svcName
    Log-Ok "SSOT Windows wallpaper release verified and installed: $wallpaperd_exe"
    if (-not (Get-Service -Name $svcName -ErrorAction SilentlyContinue)) {
        & sc.exe create $svcName binPath= "`"$wallpaperd_exe`"" start= auto displayname= 'MiOS Wallpaper Service' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not register the verified MiOS wallpaper service' }
    }
    Start-Service -Name $svcName -ErrorAction Stop

            # Register MiOS-Autostart (AtLogon trigger, RunLevel Highest, hidden).
            # NOTE: aa5f216e replaced the enclosing mios-gui-watch `if ($_gwSrc) { try {`
            # (which defined $_runKey/$_pwsh) with the mios-wallpaperd block above but
            # left this block orphaned -> re-establish those vars + a standalone try/catch.
            $_runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
            if (-not (Test-Path $_runKey)) { New-Item -Path $_runKey -Force | Out-Null }
            $_pwsh = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
            if (-not $_pwsh) { $_pwsh = "$env:ProgramFiles\PowerShell\7\pwsh.exe" }
            try {
            $_autostartEnabled = Get-MiosTomlValue -Section 'bootstrap.autostart' -Key 'enable' -Default $true
            if ($_autostartEnabled -eq 'true') { $_autostartEnabled = $true }
            elseif ($_autostartEnabled -eq 'false') { $_autostartEnabled = $false }
            if ($_autostartEnabled -isnot [bool]) { $_autostartEnabled = $true }

            if ($_autostartEnabled) {
                # Ensure local ProgramData\MiOS directory exists
                $hostProgData = Join-Path $env:ProgramData 'MiOS'
                if (-not (Test-Path $hostProgData)) { New-Item -ItemType Directory -Path $hostProgData -Force | Out-Null }
                $autostartScript = Join-Path $hostProgData 'mios-autostart.ps1'
                $autostartBody = @"
`$logPath = "C:\ProgramData\MiOS\logs\autostart.log"
`$null = New-Item -ItemType Directory -Path (Split-Path `$logPath) -Force -ErrorAction SilentlyContinue
`$null = New-Item -ItemType File -Path `$logPath -Force -ErrorAction SilentlyContinue
function Log {
    param(`$msg)
    "`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - `$msg" | Out-File -FilePath `$logPath -Append -Encoding UTF8
}
Log "MiOS Autostart triggered."
`$env:PATH = [Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH','User')
if (Get-Command podman -ErrorAction SilentlyContinue) {
    try {
        `$state = & podman machine list --format "{{.Running}}" --filter "name=$BuilderDistro" 2>&1
        Log "Current state of machine '$BuilderDistro': `$state"
        if (`$state -notmatch "true") {
            Log "Starting machine '$BuilderDistro'..."
            `$startOut = & podman machine start $BuilderDistro 2>&1
            Log "Start output: `$startOut"
        } else {
            Log "Machine '$BuilderDistro' is already running."
        }
    } catch {
        Log "Error starting machine: `$(`$_.Exception.Message)"
    }
} else {
    Log "Error: podman command not found on PATH."
}
"@
                Set-Content -Path $autostartScript -Value $autostartBody -Encoding UTF8
                Log-Ok "Staged autostart script: $autostartScript"

                $registered = $false
                $runHiddenVbs = Join-Path $hostProgData 'bin\run-hidden.vbs'
                if (-not (Test-Path -LiteralPath $runHiddenVbs)) { $runHiddenVbs = Join-Path $hostProgData 'run-hidden.vbs' }
                if (-not (Test-Path -LiteralPath $runHiddenVbs)) {
                    $vbsSrc = Join-Path $PSScriptRoot 'usr\share\mios\windows\run-hidden.vbs'
                    if (Test-Path -LiteralPath $vbsSrc) { Copy-Item -LiteralPath $vbsSrc -Destination $runHiddenVbs -Force }
                }
                if (-not (Test-Path -LiteralPath $runHiddenVbs)) {
                    $vbsDir = Split-Path $runHiddenVbs -Parent
                    if (-not (Test-Path -LiteralPath $vbsDir)) { New-Item -ItemType Directory -Path $vbsDir -Force | Out-Null }
                    @'
' MiOS Run-Hidden launcher: executes processes in hidden window mode without spawning console frames or Windows Terminal popups
Option Explicit
Dim WshShell, args, cmd, i, arg
Set WshShell = CreateObject("WScript.Shell")
Set args = WScript.Arguments
If args.Count > 0 Then
    cmd = ""
    For i = 0 To args.Count - 1
        arg = args(i)
        If InStr(arg, " ") > 0 And Left(arg, 1) <> """" Then
            arg = """" & arg & """"
        End If
        If cmd = "" Then
            cmd = arg
        Else
            cmd = cmd & " " & arg
        End If
    Next
    WshShell.Run cmd, 0, False
End If
'@ | Set-Content -Path $runHiddenVbs -Encoding ASCII -Force
                }
                $wscriptExe = Join-Path $env:SystemRoot 'System32\wscript.exe'
                $serviceTool = Join-Path $hostProgData 'bin\MiosServiceTool.exe'
                if (-not (Test-Path -LiteralPath $serviceTool)) {
                    $svcSrc = Join-Path $PSScriptRoot 'usr\share\mios\windows\MiosServiceTool.exe'
                    if (Test-Path -LiteralPath $svcSrc) { Copy-Item -LiteralPath $svcSrc -Destination $serviceTool -Force }
                }
                $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                if (-not (Test-Path -LiteralPath $psExe)) {
                    $psExe = if ($_pwsh -and (Test-Path -LiteralPath $_pwsh)) { $_pwsh } else { 'powershell.exe' }
                }

                if (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue) {
                    try {
                        if (Test-Path -LiteralPath $runHiddenVbs) {
                            $action = New-ScheduledTaskAction -Execute $wscriptExe -Argument "//B //Nologo `"$runHiddenVbs`" `"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$autostartScript`""
                        } elseif (Test-Path -LiteralPath $serviceTool) {
                            $action = New-ScheduledTaskAction -Execute $serviceTool -Argument "-Run `"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$autostartScript`""
                        } else {
                            throw "Neither run-hidden.vbs (wscript.exe) nor MiosServiceTool.exe is available to prevent console window flashing."
                        }
                        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
                        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
                        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
                        $null = Register-ScheduledTask -TaskName 'MiOS-Autostart' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -ErrorAction Stop
                        $registered = $true
                        Log-Ok "MiOS-Autostart Scheduled Task registered (AtLogon, RunLevel Highest, hidden)."
                    } catch {
                        Log-Warn "Failed to register scheduled task: $($_.Exception.Message). Falling back to HKCU\Run..."
                    }
                } else {
                    Log-Warn "Register-ScheduledTask cmdlet not available. Falling back to HKCU\Run..."
                }

                if (-not $registered) {
                    if (Test-Path -LiteralPath $runHiddenVbs) {
                        $_runValAutostart = '"{0}" //B //Nologo "{1}" "{2}" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "{3}"' -f $wscriptExe, $runHiddenVbs, $psExe, $autostartScript
                    } elseif (Test-Path -LiteralPath $serviceTool) {
                        $_runValAutostart = '"{0}" -Run "{1}" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "{2}"' -f $serviceTool, $psExe, $autostartScript
                    } else {
                        Log-Warn "Skipping HKCU\Run registration: no Subsystem 2 GUI runner available to prevent console window flash."
                    }
                    if ($_runValAutostart) {
                        Set-ItemProperty -Path $_runKey -Name 'MiOS-Autostart' -Value $_runValAutostart -Type String -Force
                        Log-Ok "MiOS-Autostart registered in HKCU\Run (fallback)."
                    }
                }
            }
        } catch {
            Log-Warn "MiOS-Autostart staging failed: $($_.Exception.Message)"
        }

    # Native client setup builds the Rust Windows-subsystem launcher inside
    # MiOS-DEV before creating shortcuts. Its startup renders the runtime SSOT.
    $miosLauncherExe = Join-Path $MiosBinDir 'mios-launch.exe'
    if (-not (Test-Path -LiteralPath $miosLauncherExe)) {
        throw 'The native Rust Windows launcher was not staged by MiOS-DEV'
    }

    if ($miosLauncherExe -and (Test-Path -LiteralPath $miosLauncherExe)) {
        # Native .exe launcher with subsystem:Windows -- ZERO console
        # flash + post-launch SetWindowPos centering. Best of both worlds.
        $hubTarget = $miosLauncherExe
        $hubArgs   = "`"$($script:MiosProfileName)`" $($script:MiosAppCols) $($script:MiosAppRows) `"$($script:MiosSchemeName)`""
    } elseif ($wtExe) {
        # Fallback: wt.exe direct (no flash but no centering).
        $hubTarget = $wtExe
        $hubArgs   = "-w MiOS --size $($script:MiosAppCols),$($script:MiosAppRows) --focus new-tab --profile `"$($script:MiosProfileName)`" --colorScheme `"$($script:MiosSchemeName)`" --title MiOS"
    } else {
        $hubTarget = $pwshExe
        $hubArgs   = "-NoExit -ExecutionPolicy Bypass -Command `"& { $hubResizePrelude; & '$hubPath' }`""
    }

    $smLnk = Join-Path $StartMenuDir 'MiOS.lnk'

    $verbShortcuts = @()
    $miosLaunchPs1 = Join-Path $MiosBinDir 'mios-launch.ps1'

    $staleLnks = @(
        # Redundant-with-MiOS.lnk + typed-verb apps:
        'MiOS-DEV.lnk', 'MiOS Config.lnk',
        'MiOS Build.lnk', 'MiOS Configurator.lnk', 'MiOS Dashboard.lnk',
        'MiOS Update.lnk', 'MiOS Pull.lnk',
        # Legacy names from older revisions:
        'Build MiOS.lnk', 'MiOS Dev VM.lnk', 'MiOS Rebuild.lnk',
        'MiOS Setup.lnk', 'MiOS Terminal.lnk', 'MiOS Dev Shell.lnk',
        'MiOS Podman Shell.lnk',
        # Per-action shortcuts consolidated into canonical MiOS.lnk:
        'MiOS Agents.lnk', 'MiOS AI.lnk', 'MiOS System Monitor.lnk'
    )
    foreach ($legacy in $staleLnks) {
        foreach ($dir in @($StartMenuDir, $desktopDir)) {
            if (-not $dir) { continue }
            $stale = Join-Path $dir $legacy
            if (Test-Path $stale) {
                try { Remove-Item $stale -Force -ErrorAction SilentlyContinue; Log-Ok "Removed stale shortcut: $stale" } catch {}
            }
        }
    }

    try {
        $linuxAppsDir = Join-Path $StartMenuDir 'Linux Apps'
        if (-not (Test-Path -LiteralPath $linuxAppsDir)) {
            New-Item -ItemType Directory -Path $linuxAppsDir -Force | Out-Null
        }

        # Resolve WSL distro that actually exists (podman-prefixed or bare).
        $linuxDistro = $null
        try {
            $_wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" |
                ForEach-Object { ($_ -replace [char]0, '').Trim() } |
                Where-Object { $_ }
            foreach ($_cand in @("podman-$DevDistro", $DevDistro, "podman-$LegacyDevName", $LegacyDevName)) {
                if ($_wslList -contains $_cand) { $linuxDistro = $_cand; break }
            }
        } catch {}
        if (-not $linuxDistro) { $linuxDistro = "podman-$DevDistro" }

        $wslExe = $null
        foreach ($_c in @(
            "$env:ProgramFiles\WSL\wslg.exe",
            "$env:WINDIR\System32\wslg.exe"
        )) {
            if (Test-Path -LiteralPath $_c) { $wslExe = $_c; break }
        }
        if (-not $wslExe) {
            $wslExe = (Get-Command wsl.exe -ErrorAction SilentlyContinue).Source
            if (-not $wslExe) { $wslExe = "$env:WINDIR\System32\wsl.exe" }
        }

        $linuxAppMap = @{
            'org.gnome.Nautilus.Devel'         = 'Files'
            'org.gnome.Nautilus'               = 'Files'
            'org.gnome.Epiphany'               = 'Web'
            'app.devsuite.Ptyxis'              = 'Ptyxis'
            'com.github.tchx84.Flatseal'       = 'Flatseal'
            'com.mattjakeman.ExtensionManager' = 'Extension Manager'
            'com.vscodium.codium'              = 'VSCodium'
            'org.gnome.Software'               = 'Software'
        }

        $_fpList = @()
        try {
            $_tomlTextForFp = Resolve-MiosTomlText
            if ($_tomlTextForFp) {
                $_fpMatch = [regex]::Match($_tomlTextForFp,
                    '(?ms)^\[desktop\]\s*$.*?^\s*flatpaks\s*=\s*\[(?<arr>.*?)\]\s*$')
                if ($_fpMatch.Success) {
                    $_fpArrBody = ($_fpMatch.Groups['arr'].Value -split "`n" |
                        ForEach-Object { ($_ -replace '#.*$', '').Trim() }) -join ' '
                    $_fpList = @($_fpArrBody -split ',' |
                        ForEach-Object { $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n") } |
                        Where-Object { $_ })
                }
            }
        } catch {
            Log-Warn "Linux Apps: mios.toml [desktop].flatpaks parse failed: $($_.Exception.Message)"
        }

        $linuxShortcutsCreated = 0
        foreach ($_ref in $_fpList) {
            $_r = $_ref.Trim().Trim('"').Trim("'")
            if (-not $_r) { continue }
            if ($_r -like '#*') { continue }
            # Strip <remote>: prefix if present.
            $_appId = if ($_r -match ':') { $_r -split ':', 2 | Select-Object -Last 1 } else { $_r }
            # Skip GTK theme extensions (not launchable apps).
            if ($_appId -like 'org.gtk.Gtk*theme*') { continue }
            $_friendly = $linuxAppMap[$_appId]
            if (-not $_friendly) {
                # Fallback: last dotted segment, capitalized.
                $_last = ($_appId -split '\.')[-1]
                $_friendly = (Get-Culture).TextInfo.ToTitleCase($_last)
            }
            $_lnk = Join-Path $linuxAppsDir ("{0}.lnk" -f $_friendly)
            $_isWslg = $wslExe -match 'wslg\.exe$'
            # mios-gui dispatches through flatpak-launch, which arms the
            # SSOT Windows window-centering observer before mapping WSLg UI.
            $_args   = if ($_isWslg) {
                ("-d {0} --user mios --cd `"~`" -- /usr/libexec/mios/mios-gui {1}" -f $linuxDistro, $_appId)
            } else {
                ("-d {0} --user mios -- /usr/libexec/mios/mios-gui {1}" -f $linuxDistro, $_appId)
            }
            try {
                New-Shortcut -Path $_lnk `
                    -Target $wslExe `
                    -ArgList $_args `
                    -Desc ("MiOS Linux app: {0} ({1}) on {2}" -f $_friendly, $_appId, $linuxDistro) `
                    -Dir ([Environment]::GetFolderPath('Desktop'))
                $linuxShortcutsCreated++
            } catch {
                Log-Warn "Linux app shortcut failed for ${_appId}: $($_.Exception.Message)"
            }
        }

        # System apps that aren't flatpaks but should still appear in
        # the Linux Apps folder (control center, system monitor).
        $sysApps = @(
            @{ Name = 'Settings'; Cmd = 'gnome-control-center' }
        )
        foreach ($_sa in $sysApps) {
            $_lnk = Join-Path $linuxAppsDir ("{0}.lnk" -f $_sa.Name)
            $_isWslg = $wslExe -match 'wslg\.exe$'
            $_sysArgs = if ($_isWslg) {
                ("-d {0} --user mios --cd `"~`" -- /usr/libexec/mios/mios-gui {1}" -f $linuxDistro, $_sa.Cmd)
            } else {
                ("-d {0} --user mios -- /usr/libexec/mios/mios-gui {1}" -f $linuxDistro, $_sa.Cmd)
            }
            try {
                New-Shortcut -Path $_lnk `
                    -Target $wslExe `
                    -ArgList $_sysArgs `
                    -Desc ("MiOS Linux app: {0} ({1}) on {2}" -f $_sa.Name, $_sa.Cmd, $linuxDistro) `
                    -Dir ([Environment]::GetFolderPath('Desktop'))
                $linuxShortcutsCreated++
            } catch {}
        }

        # btop is a TUI. Run its existing Windows-side MiOS verb in the
        # centered, SSOT-themed Windows Terminal profile instead of WSLg.
        $_btopLnk = Join-Path $linuxAppsDir 'System Monitor.lnk'
        $_btopProfile = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'profile_name' -Default 'MiOS-WIN')
        $_btopArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$miosLauncher`" -Profile `"$_btopProfile`" -Verb btop"
        New-Shortcut -Path $_btopLnk -Target $pwshExe -ArgList $_btopArgs -Desc 'MiOS System Monitor (btop)' -Dir ([Environment]::GetFolderPath('Desktop'))
        $linuxShortcutsCreated++

        Log-Ok ("MiOS Linux Apps: {0} Start Menu shortcuts -> {1}" -f $linuxShortcutsCreated, $linuxAppsDir)
    } catch {
        Log-Warn "MiOS Linux Apps Start Menu seeding failed: $($_.Exception.Message)"
    }

    try {
        $servicesDir = Join-Path $StartMenuDir 'Services'
        if (-not (Test-Path -LiteralPath $servicesDir)) {
            New-Item -ItemType Directory -Path $servicesDir -Force | Out-Null
        }
        $_defaultPorts = [ordered]@{
            forge_http       = 8300
            open_webui       = 8033
            code_server      = 8800
            hermes           = 8642
            guacamole_web    = 8080
            ceph_dashboard   = 8444
            searxng          = 8899
            cockpit          = 8090
            llm_light        = 8450
        }
        # HTTPS for Cockpit + Ceph (self-signed; click through once). All
        # logins default to the global MiOS password (mios.toml [identity].
        # default_password = "mios"). Open WebUI is the default
        # chat front-end; code-server pairs with mios-forge for an
        # in-browser dev workflow.
        $_webLinks = @(
            @{ Key='open_webui';       Name='MiOS Chat (Open WebUI)';       Scheme='http';  Path='/' }
            @{ Key='code_server';      Name='MiOS Code (code-server)';      Scheme='http';  Path='/' }
            @{ Key='cockpit';          Name='MiOS Cockpit';                 Scheme='https'; Path='/' }
            @{ Key='forge_http';       Name='MiOS Forge';                   Scheme='http';  Path='/' }
            @{ Key='searxng';          Name='MiOS Search (SearXNG)';        Scheme='http';  Path='/' }
            @{ Key='hermes';           Name='MiOS Hermes API';              Scheme='http';  Path='/v1/models' }
            @{ Key='llm_light';        Name='MiOS LLM Light API';           Scheme='http';  Path='/' }
            @{ Key='guacamole_web';    Name='MiOS Guacamole';               Scheme='http';  Path='/guacamole/' }
            @{ Key='ceph_dashboard';   Name='MiOS Ceph Dashboard';          Scheme='https'; Path='/' }
        )
        $_svcCreated = 0
        foreach ($_w in $_webLinks) {
            $_port = [int](Get-MiosTomlValue -Section 'ports' -Key $_w.Key -Default $_defaultPorts[$_w.Key])
            if ($_port -lt 1 -or $_port -gt 65535) { continue }
            $_url = "{0}://localhost:{1}{2}" -f $_w.Scheme, $_port, $_w.Path
            $_urlFile = Join-Path $servicesDir ("{0}.url" -f $_w.Name)
            try {
                $_lines = @(
                    '[InternetShortcut]'
                    "URL=$_url"
                    '[{000214A0-0000-0000-C000-000000000046}]'
                    'Prop3=19,2'
                )
                Set-Content -Path $_urlFile -Value $_lines -Encoding ASCII -Force
                $_svcCreated++
            } catch {
                Log-Warn "MiOS Services: $($_w.Name) shortcut failed: $($_.Exception.Message)"
            }
        }
        Log-Ok ("MiOS Services: {0} Start Menu .url shortcuts -> {1}" -f $_svcCreated, $servicesDir)
    } catch {
        Log-Warn "MiOS Services Start Menu seeding failed: $($_.Exception.Message)"
    }

    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null

    # -- 6. Verify the dev distro is registered (or warn) --------------
    # Phase 3 ("MiOS-DEV distro") provisions the dev distro as
    # "podman-$DevDistro" (= "podman-MiOS-DEV"); the post-Phase-13
    # Rename-PodmanDevDistro pass drops that prefix to plain
    # "$DevDistro" (= "MiOS-DEV"). Probe canonical-first.
    $wslList = @()
    try { $wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" | ForEach-Object { ($_ -replace [char]0, '').Trim() } | Where-Object { $_ } } catch {}
    $devCandidates = @($DevDistro, "podman-$DevDistro", $LegacyDevName, "podman-$LegacyDevName")
    $matched = $wslList | Where-Object { $devCandidates -contains $_ } | Select-Object -First 1
    if ($matched) {
        Log-Ok "$matched distro registered -- launcher ready"
    } else {
        Log-Warn "$DevDistro distro not registered yet (Phase 3 should have provisioned it). The launcher's mios-dash will show 'not registered'; rerun this script or `podman machine init` to create it."
    }

    Log-Ok "MiOS launcher binaries staged at $MiosBinDir (mios-launch.ps1 + mios-launch.exe). Shortcut creation deferred to FINAL STEP of Get-MiOS.ps1 after bootstrap completes successfully."

    $_getMiosCandidates = @(
        Join-Path $MiosRepoDir 'Get-MiOS.ps1'
        Join-Path $MiosBootstrapShadow 'Get-MiOS.ps1'
        'M:\Get-MiOS.ps1'
    )
    $_getMios = $_getMiosCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($_getMios) {
        try {
            $env:MIOS_GETMIOS_FUNCTIONS_ONLY = '1'
            $_gmSrc = [System.IO.File]::ReadAllText($_getMios, [System.Text.UTF8Encoding]::new($false))
            $_gmBlock = [scriptblock]::Create($_gmSrc)
            . $_gmBlock
            Remove-Item env:\MIOS_GETMIOS_FUNCTIONS_ONLY -ErrorAction SilentlyContinue
            if (Get-Command Install-MiOSPowerShellProfile -ErrorAction SilentlyContinue) {
                Install-MiOSPowerShellProfile | Out-Null
                Log-Ok "Get-MiOS Install-MiOSPowerShellProfile re-substituted (M:\MiOS\powershell\profile.ps1 from current mios.toml)"
            } else {
                Log-Warn "Install-MiOSPowerShellProfile not defined after Get-MiOS.ps1 dot-source -- gate may have triggered too early"
            }
            if (Get-Command Install-MiOSTerminalProfile -ErrorAction SilentlyContinue) {
                Install-MiOSTerminalProfile | Out-Null
                Log-Ok "Get-MiOS Install-MiOSTerminalProfile re-substituted (WT settings.json from current mios.toml)"
            }
            if (Get-Command Install-MiOSTmuxProfile -ErrorAction SilentlyContinue) {
                Install-MiOSTmuxProfile | Out-Null
                Log-Ok "Get-MiOS Install-MiOSTmuxProfile re-substituted (`$env:USERPROFILE\.tmux.conf from current mios.toml)"
            }
        } catch {
            Remove-Item env:\MIOS_GETMIOS_FUNCTIONS_ONLY -ErrorAction SilentlyContinue
            Log-Warn "Get-MiOS.ps1 functions-only dot-source failed: $($_.Exception.Message). Dashboard + WT settings.json may be stale -- run 'irm $($MiosBootstrapRaw)/Get-MiOS.ps1 | iex' to refresh."
        }
    } else {
        Log-Warn "Get-MiOS.ps1 not found in any candidate path ($($_getMiosCandidates -join ', ')) -- dashboard + WT settings.json patches skipped. Run 'irm $($MiosBootstrapRaw)/Get-MiOS.ps1 | iex' to refresh."
    }
}

# =============================================================================
# MAIN -- wrapped so the window NEVER closes on error
# =============================================================================
$ExitCode = 0
try {

Try-ResizeConsole -Cols 80 -Rows 30
$script:DashboardMode = if ($env:MIOS_DASHBOARD_MODE -eq 'interactive' -and (Test-DashboardCanRedraw)) {
    'interactive'
} else {
    'log'
}

# -- Banner -------------------------------------------------------------------
try { Clear-Host } catch {}
$bTop = [char]0x256D + (([char]0x2500).ToString() * ($script:DW - 2)) + [char]0x256E
$bBot = [char]0x2570 + (([char]0x2500).ToString() * ($script:DW - 2)) + [char]0x256F

function _BoxRow {
    param([string]$Inner)
    $maxInner = $script:DW - 4
    if ($Inner.Length -gt $maxInner) {
        $Inner = $Inner.Substring(0, $maxInner)
    }
    [char]0x2502 + " " + $Inner.PadRight($maxInner) + " " + [char]0x2502
}

$_bannerTitle    = Get-MiosTomlValue -Section 'messages.installer_banner' -Key 'title'    -Default "'MiOS' {version}  --  Unified Windows Installer"
$_bannerTaglines = @(Get-MiosTomlValue -Section 'messages.installer_banner' -Key 'taglines' -Default @(
    'Immutable Fedora AI Workstation',
    "WSL2 + Podman  $([char]0x2502)  Offline Build Pipeline"
))
$_bannerTitle = $_bannerTitle -replace '\{version\}', $MiosVersion
Write-Host $bTop -ForegroundColor Cyan
Write-Host (_BoxRow $_bannerTitle) -ForegroundColor Cyan
foreach ($_tg in $_bannerTaglines) {
    Write-Host (_BoxRow ($_tg -replace '\{version\}', $MiosVersion)) -ForegroundColor Cyan
}
Write-Host $bBot -ForegroundColor Cyan
Write-Host ""

if ($script:DashboardMode -eq 'log') {
    # Resolve linear-log mode header lines from mios.toml
    # [messages.build_pipeline] (SSOT).  Vendor fallback below
    # preserves the existing wording when no toml is reachable.
    $_llNote = Get-MiosTomlValue -Section 'messages.build_pipeline' -Key 'linear_log_note' -Default "Note: console doesn't support in-place repaint -- running in linear log mode."
    $_llHint = Get-MiosTomlValue -Section 'messages.build_pipeline' -Key 'linear_log_hint' -Default "      Phase transitions + throttled step updates print sequentially below."
    Write-Host $_llNote -ForegroundColor Yellow
    Write-Host $_llHint -ForegroundColor DarkYellow
    Write-Host ""
}

# Capture the row where the dashboard will be drawn (right after banner)
$script:DashRow = try { [Console]::CursorTop } catch { 0 }

# -- Background heartbeat (interactive mode only) -----------------------------
# Runs on a dedicated runspace so the spinner animates even when the
# main render loop is blocked on a long sub-process. Skipped in log
# mode -- without working SetCursorPosition the heartbeat would just
# stamp characters at the bottom of the buffer forever.
if ($script:DashboardMode -eq 'interactive') {
    $script:BgRs = [runspacefactory]::CreateRunspace()
    $script:BgRs.Open()
    $script:BgRs.SessionStateProxy.SetVariable('dashSync', $script:DashSync)
    $script:BgPs = [powershell]::Create()
    $script:BgPs.Runspace = $script:BgRs
    $null = $script:BgPs.AddScript({
        $chars = @('|', '/', '-', [char]92)
        $i = 0
        while ($dashSync.Running) {
            [System.Threading.Thread]::Sleep(120)
            if ($dashSync.Rendering) { continue }
            $row = $dashSync.SpinnerRow
            $col = $dashSync.SpinnerCol
            if ($row -ge 0) {
                try {
                    $prevTop = [Console]::CursorTop
                    $prevLeft = [Console]::CursorLeft
                    [Console]::SetCursorPosition($col, $row)
                    [Console]::Write($chars[$i % 4])
                    [Console]::SetCursorPosition($prevLeft, $prevTop)
                } catch {}
                $i++
            }
        }
    })
    $script:BgHandle = $script:BgPs.BeginInvoke()
}

# Re-set console size again right before the first Show-Dashboard render.
# The earlier resize (~line 70) is the LOAD-TIME resize that fixes the $DW
# computation. This second resize is defensive: if some other code in the
# load path between line 70 and here changed the window size, this restores
# it. Idempotent. Dims source from mios.toml [terminal] (script:Mios* vars).
try {
    [Console]::SetWindowSize($script:MiosCols, $script:MiosRows)
    [Console]::SetBufferSize($script:MiosCols, $script:MiosScroll)
} catch {}

# Re-call the SAME Get-MiosFrameWidth helper so post-resize $DW is
# computed by ONE function (operator: "I said Unified!!!").
$script:DW = Get-MiosFrameWidth

Show-Dashboard -Force   # draw initial (all phases pending)

# -- Phase 0 -- Hardware + Prerequisites --------------------------------------
Start-Phase 0
$HW = Get-Hardware
Write-Log "hw: CPU=$($HW.Cpus)  RAM=$($HW.RamGB)GB  Disk=$($HW.DiskGB)GB  GPU=$($HW.GpuName)"
Write-Log "hw: Base=$($HW.BaseImage)  Model=$($HW.AiModel)"
$gpuShort = $HW.GpuName -replace 'NVIDIA GeForce ','RTX ' -replace 'NVIDIA Quadro ','Quadro '
$script:HWInfo    = "Host:$($env:COMPUTERNAME)  RAM:$($HW.RamGB)GB  CPU:$($HW.Cpus)c  GPU:$gpuShort  Base:$($HW.BaseImage -replace 'ghcr.io/ublue-os/ucore-hci:','')"
$script:IdentInfo = "Base:$($HW.BaseImage -replace 'ghcr.io/ublue-os/ucore-hci:','')  Model:$($HW.AiModel)"
Show-Dashboard -Force

$preOk = $true

function Install-MiosPrereqDirect {
    param([string]$Cmd, [string]$Label)
    $_root = Join-Path $env:LOCALAPPDATA 'MiOS'
    try {
        switch ($Cmd) {
            'git' {
                # PortableGit self-extracting 7-Zip archive from git-for-windows.
                $rel = Invoke-RestMethod 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{'User-Agent'='mios-bootstrap'} -ErrorAction Stop
                $asset = $rel.assets | Where-Object { $_.name -match '^PortableGit-.*-64-bit\.7z\.exe$' } | Select-Object -First 1
                if (-not $asset) { Log-Warn 'git: no PortableGit 64-bit asset in latest git-for-windows release'; return $false }
                $sfx = Join-Path $env:TEMP "PortableGit-$(Get-Random).7z.exe"
                Invoke-WebRequest $asset.browser_download_url -OutFile $sfx -UseBasicParsing -ErrorAction Stop
                $gitDir = Join-Path $_root 'PortableGit'
                if (Test-Path $gitDir) { Remove-Item $gitDir -Recurse -Force -ErrorAction SilentlyContinue }
                New-Item -ItemType Directory -Path $gitDir -Force | Out-Null
                & $sfx "-o$gitDir" -y | Out-Null   # 7-Zip SFX: silent extract to -o<dir>
                Remove-Item $sfx -Force -ErrorAction SilentlyContinue
                $gitCmd = Join-Path $gitDir 'cmd'
                if (Test-Path (Join-Path $gitCmd 'git.exe')) {
                    $_u = [Environment]::GetEnvironmentVariable('Path','User')
                    if (-not (($_u -split ';') | Where-Object { $_ -ieq $gitCmd })) {
                        [Environment]::SetEnvironmentVariable('Path', "$_u;$gitCmd", 'User')
                    }
                    $env:PATH = "$env:PATH;$gitCmd"
                    return $true
                }
                return $false
            }
            'wsl' {
                # `wsl --install` ships with Windows 10 2004+/11 -- no download
                # needed; needs admin + likely a reboot before wsl.exe surfaces.
                & wsl.exe --install --no-distribution 2>&1 | ForEach-Object { Write-Log "wsl-install: $_" }
                return ($LASTEXITCODE -eq 0)
            }
            'podman' {
                # Podman for Windows installer from containers/podman releases.
                $rel = Invoke-RestMethod 'https://api.github.com/repos/containers/podman/releases/latest' -Headers @{'User-Agent'='mios-bootstrap'} -ErrorAction Stop
                $asset = $rel.assets | Where-Object { $_.name -match '^podman-.*-setup\.exe$' } | Select-Object -First 1
                if (-not $asset) { Log-Warn 'podman: no setup.exe asset in latest containers/podman release'; return $false }
                $exe = Join-Path $env:TEMP "podman-setup-$(Get-Random).exe"
                Invoke-WebRequest $asset.browser_download_url -OutFile $exe -UseBasicParsing -ErrorAction Stop
                Start-Process -FilePath $exe -ArgumentList '/install','/quiet','/norestart' -Wait -ErrorAction Stop  # WiX burn silent
                Remove-Item $exe -Force -ErrorAction SilentlyContinue
                $_m = [Environment]::GetEnvironmentVariable('PATH','Machine'); $_u = [Environment]::GetEnvironmentVariable('PATH','User')
                $env:PATH = (@($_m,$_u) | Where-Object {$_}) -join ';'
                return ([bool](Get-Command podman -ErrorAction SilentlyContinue))
            }
        }
    } catch { Log-Warn ("{0} direct-install failed: {1}" -f $Label, $_.Exception.Message) }
    return $false
}

$_prereqs = @(
    @{ Cmd = 'git';    Pkg = (Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'git_pkg'    -Default 'Git.Git');                 Label = 'Git';    Required = $true  }
    @{ Cmd = 'wsl';    Pkg = (Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'wsl_pkg'    -Default 'Microsoft.WSL');           Label = 'WSL2';   Required = $true  }
    @{ Cmd = 'podman'; Pkg = (Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_pkg' -Default 'RedHat.Podman-Desktop');   Label = 'Podman'; Required = $true  }
)
foreach ($_pq in $_prereqs) {
    if (Get-Command $_pq.Cmd -EA SilentlyContinue) {
        $_ver = ''
        try {
            switch ($_pq.Cmd) {
                'git'    { $_ver = ((& git --version 2>&1) -replace 'git version ','') }
                'wsl'    { $_ver = 'available' }
                'podman' { $_ver = ((& podman --version 2>&1) -replace 'podman version ','') }
            }
        } catch {}
        Log-Ok ("{0} {1}" -f $_pq.Label, $_ver)
        continue
    }
    $_done = $false
    # 1) winget -- OPTIONAL accelerator, only if present.
    if (Get-Command winget -EA SilentlyContinue) {
        Log-Ok ("{0} not found -- winget installing {1}..." -f $_pq.Label, $_pq.Pkg)
        & winget install --id $_pq.Pkg --silent --accept-package-agreements --accept-source-agreements --source winget 2>&1 |
            ForEach-Object { Write-Log ("winget[{0}]: {1}" -f $_pq.Cmd, $_) }
        $_rc = $LASTEXITCODE
        try {
            $_machPath = [System.Environment]::GetEnvironmentVariable('PATH','Machine')
            $_userPath = [System.Environment]::GetEnvironmentVariable('PATH','User')
            $env:PATH  = (@($_machPath, $_userPath) | Where-Object { $_ }) -join ';'
        } catch {}
        if (Get-Command $_pq.Cmd -EA SilentlyContinue) {
            Log-Ok ("{0} installed via winget" -f $_pq.Label); $_done = $true
        } elseif ($_pq.Cmd -eq 'wsl' -and $_rc -eq 0) {
            Log-Warn ("{0} installed via winget -- a reboot may be required for wsl.exe to surface" -f $_pq.Label); $_done = $true
            $script:WslJustInstalled = $true   # install-robustness: WSL2 substrate not live until reboot
        }
    }
    # 2) NO-LOCAL-DEPS direct install -- winget absent OR failed.
    if (-not $_done) {
        Log-Ok ("{0}: installing via direct download (no winget dependency)..." -f $_pq.Label)
        if (Install-MiosPrereqDirect -Cmd $_pq.Cmd -Label $_pq.Label) {
            if ($_pq.Cmd -eq 'wsl') {
                Log-Warn ("{0} installed direct -- a reboot may be required for wsl.exe to surface" -f $_pq.Label)
                $script:WslJustInstalled = $true   # install-robustness: reboot before WSL2 substrate is live
            } else {
                Log-Ok ("{0} installed direct" -f $_pq.Label)
            }
            $_done = $true
        }
    }
    if (-not $_done) {
        Log-Fail ("{0} could not be installed (winget + direct both unavailable/failed) -- bootstrap needs {1}" -f $_pq.Label, $_pq.Cmd)
        if ($_pq.Required) { $preOk = $false }
    }
}

# Install-robustness (B3): if WSL2 was JUST installed this run, the
# WSL2 substrate (and thus `podman machine init` in Phase 3) is NOT live until
# Windows reboots. Falling through to Phase 1/3 here dies with a cryptic podman
# error. HALT cleanly with an actionable, idempotent-re-run banner instead.
if ($script:WslJustInstalled) {
    End-Phase 0 -Fail
    Log-Fail "WSL2 was just installed -- Windows MUST reboot before the WSL2 substrate (podman machine) is live."
    Log-Fail "  -> Reboot Windows, then re-run the MiOS bootstrap (it is idempotent and resumes from here)."
    throw "Reboot required after WSL2 install -- reboot Windows, then re-run the bootstrap."
}

# Install-robustness (B2): hardware-virtualization preflight. WSL2 +
# `podman machine init` cannot start without VT-x/AMD-V (SVM) enabled in BIOS/
# UEFI; without this check Phase 3 dies with a cryptic HCS 0x80370102 / "not in
# running state after 90s". Probe firmware + hypervisor presence and fail CLEANLY
# with remediation. (Best-effort: a CIM query failure must not block a capable box.)
try {
    $_virtFw = $true; $_hyperv = $true
    try { $_virtFw = [bool](Get-CimInstance Win32_Processor -EA Stop | Select-Object -First 1 -Expand VirtualizationFirmwareEnabled) } catch {}
    try { $_hyperv = [bool](Get-CimInstance Win32_ComputerSystem -EA Stop).HypervisorPresent } catch {}
    if (-not $_virtFw -and -not $_hyperv) {
        Log-Fail "Hardware virtualization is DISABLED -- WSL2 + podman machine cannot start."
        Log-Fail "  -> Enable Intel VT-x / AMD-V (SVM) in BIOS/UEFI, then re-run the bootstrap."
        $preOk = $false
    }
} catch {}

if (-not $preOk) { End-Phase 0 -Fail; throw "Prerequisites missing -- see log: $LogFile" }

# Pre-flight: scrub misplaced /etc/wsl.conf keys from .wslconfig's [wsl2]
# section BEFORE Phase 3 (podman machine init) talks to wsl.exe. A stale
# `systemd=true` here would otherwise crash Phase 3 with the FATAL
# "wsl: Unknown key 'wsl2.systemd' in <path>" surfaced as the last
# captured stderr line of the podman pipeline.
Repair-WslConfig

End-Phase 0

function Invoke-GitFetchWithRetry {
    param(
        [string]$RepoPath,
        [string]$Ref
    )
    $exitCode = 1
    Push-Location $RepoPath
    try {
        for ($retry = 1; $retry -le 3; $retry++) {
            $exitCode = Invoke-NativeQuiet { git fetch --depth=1 origin $Ref }
            if ($exitCode -eq 0) { return 0 }
            # Fallback to full fetch if depth=1 fails (e.g. on commit SHAs or tags)
            $exitCode = Invoke-NativeQuiet { git fetch origin $Ref }
            if ($exitCode -eq 0) { return 0 }

            if ($retry -lt 3) {
                Log-Warn "git fetch failed for ref $Ref (exit $exitCode). Retrying in 5 seconds ($retry/3)..."
                Start-Sleep -Seconds 5
            }
        }
    } finally {
        Pop-Location
    }
    return $exitCode
}

function Update-MiosCheckout([string]$RepoPath, [string]$Ref) {
    $changes = @(& git -C $RepoPath status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect checkout $RepoPath; refusing to update it." }
    if ($changes.Count) {
        Log-Warn "Preserving local contributions at $RepoPath; remote update deferred."
        return
    }
    $fetchExit = Invoke-GitFetchWithRetry -RepoPath $RepoPath -Ref $Ref
    if ($fetchExit -ne 0) { throw "Cannot fetch $Ref at $RepoPath (exit $fetchExit)" }
    & git -C $RepoPath merge --ff-only FETCH_HEAD | ForEach-Object { Write-Log "git-update: $_" }
    if ($LASTEXITCODE -ne 0) { throw "Cannot fast-forward $RepoPath to $Ref; local commits are preserved." }
}

# -- Phase 1 -- Detecting existing build environment --------------------------
Start-Phase 1
Start-MiosBuildMonitor
$activeDistro = Find-ActiveDistro

if ($activeDistro) {
    Log-Ok "MiOS repo found in $activeDistro"
}

# mios.git is overlaid AT $MiosRepoDir root (M:\). Per.
if ($SourceRoot) {
    $script:MiosRepoDir = (Resolve-Path -LiteralPath $SourceRoot -ErrorAction Stop).Path
    $MiosRepoDir = $script:MiosRepoDir
}
$miosRepo = $MiosRepoDir
    if (Test-Path (Join-Path $MiosRepoDir ".git")) {
        if (-not $SourceRoot) { Update-MiosCheckout -RepoPath $MiosRepoDir -Ref $MiosRef }
    } else {
        Set-Step (Get-MiosTomlValue -Section 'messages.steps' -Key 'mios_git_init' -Default "Initializing mios.git as the $MiosRepoDir working tree")
        & git config --global --add safe.directory '*' 2>&1 | ForEach-Object { Write-Log "git-safe-dir: $_" }
        & git config --global --add safe.directory $MiosRepoDir 2>&1 | ForEach-Object { Write-Log "git-safe-dir: $_" }
        Push-Location $MiosRepoDir
        try {
            $null = Invoke-NativeQuiet { git init -q }
            $null = Invoke-NativeQuiet { git config --unset core.worktree }
            $null = Invoke-NativeQuiet { git remote add origin $MiosRepoUrl }

            $fetchExit = Invoke-GitFetchWithRetry -RepoPath $MiosRepoDir -Ref $MiosRef
            if ($fetchExit -ne 0) {
                throw "mios.git: git fetch from $MiosRepoUrl failed (exit $fetchExit) at $MiosRepoDir"
            }
            $null = Invoke-NativeQuiet { git checkout --detach FETCH_HEAD }
            if ($MiosRef -match '^[0-9a-fA-F]{7,40}$') {
                $null = Invoke-NativeQuiet { git checkout -q FETCH_HEAD }
            } else {
                $null = Invoke-NativeQuiet { git branch $MiosRef FETCH_HEAD }
                $null = Invoke-NativeQuiet { git checkout -q $MiosRef }
            }
        } finally { Pop-Location }
    }
    Push-Location $MiosRepoDir
    try {
        $existingWt = & git config --get core.worktree 2>$null
        if ($existingWt -and ($existingWt -match '^[A-Za-z]:[\/]')) {
            Log-Warn "Scrubbing stale Windows-shaped core.worktree '$existingWt' from $MiosRepoDir\.git\config"
            $null = Invoke-NativeQuiet { git config --unset core.worktree }
        }
    } finally { Pop-Location }
    Log-Ok (Get-MiosTomlValue -Section 'messages.steps' -Key 'mios_git_overlaid' -Default "mios.git overlaid at $MiosRepoDir")

    # -- Step 2: mios-bootstrap.git in shadow checkout, files overlaid ------
    if (Test-Path (Join-Path $MiosBootstrapShadow ".git")) {
        Update-MiosCheckout -RepoPath $MiosBootstrapShadow -Ref $MiosBootstrapRef
    } else {
        if (-not (Test-Path $MiosBootstrapShadow)) {
            New-Item -ItemType Directory -Path $MiosBootstrapShadow -Force | Out-Null
        }
        Set-Step (Get-MiosTomlValue -Section 'messages.steps' -Key 'mios_bootstrap_clone' -Default "Cloning mios-bootstrap.git -> shadow $MiosBootstrapShadow")
        Push-Location $MiosBootstrapShadow
        try {
            $null = Invoke-NativeQuiet { git init -q }
            $null = Invoke-NativeQuiet { git remote add origin $MiosBootstrapUrl }
            $fetchExit = Invoke-GitFetchWithRetry -RepoPath $MiosBootstrapShadow -Ref $MiosBootstrapRef
            if ($fetchExit -ne 0) {
                throw "mios-bootstrap.git: git fetch from $MiosBootstrapUrl failed (exit $fetchExit) at $MiosBootstrapShadow"
            }
            $null = Invoke-NativeQuiet { git checkout --detach FETCH_HEAD }
            if ($MiosBootstrapRef -match '^[0-9a-fA-F]{7,40}$') {
                $null = Invoke-NativeQuiet { git checkout -q FETCH_HEAD }
            } else {
                $null = Invoke-NativeQuiet { git branch $MiosBootstrapRef FETCH_HEAD }
                $null = Invoke-NativeQuiet { git checkout -q $MiosBootstrapRef }
            }
        } finally { Pop-Location }
    }

    Set-Step (Get-MiosTomlValue -Section 'messages.steps' -Key 'mios_bootstrap_overlay' -Default "Overlaying mios-bootstrap files onto $MiosRepoDir")
    $robocopyExit = Invoke-NativeQuiet {
        robocopy $MiosBootstrapShadow $MiosRepoDir `
            /E /XC /XN /XO /XD .git /XF mios.toml /NJH /NJS /NFL /NDL /NP
    }
    if ($robocopyExit -ge 8) {
        throw "mios-bootstrap overlay failed: robocopy exit $robocopyExit"
    }
    Log-Ok "mios-bootstrap files overlaid at $MiosRepoDir (shadow at $MiosBootstrapShadow)"

    if (-not (Test-Path $MiosInstallDir)) { New-Item -ItemType Directory -Path $MiosInstallDir -Force | Out-Null }
    if (-not (Test-Path $MiosBinDir)) { New-Item -ItemType Directory -Path $MiosBinDir -Force | Out-Null }

    # Drop a VERSION marker at the Windows install dir so external tools
    # (and the uninstaller) can identify the installed release without
    # a git query.
    Set-Content -Path (Join-Path $MiosInstallDir "VERSION") -Value $MiosVersion -Encoding ASCII -Force

    # Stage entry-point scripts under $MiosBinDir for Start Menu shortcuts /
    # PATH integration that target a stable non-git location. Files come
    # from M:\ (overlay) since both repos' contents are now there.
    foreach ($script in @("Get-MiOS.ps1","build-mios.ps1","build-mios.sh","bootstrap.ps1","bootstrap.sh")) {
        $srcFile = Join-Path $MiosRepoDir $script
        if (Test-Path $srcFile) {
            Copy-Item -Path $srcFile -Destination (Join-Path $MiosBinDir $script) -Force
        }
    }
    Log-Ok (Get-MiosTomlValue -Section 'messages.steps' -Key 'entry_scripts_staged' -Default "Entry scripts staged at $MiosBinDir")
    End-Phase 2

    # -- Phase 3 -- MiOS-DEV distro (formerly MiOS-BUILDER) -------------------
    Start-Phase 3

    try { Set-MiosWslConfig -RamGB $HW.RamGB -Cpus $HW.Cpus -Force } catch { Log-Warn "Set-MiosWslConfig (pre-Phase-3): $($_.Exception.Message)" }
    $script:ResolvedBuilderDistribution = Ensure-MiosBuilder -Machine $BuilderDistro -Hardware $HW
    Log-Ok "Using preserved MiOS builder $script:ResolvedBuilderDistribution"

    Invoke-MiosQuadletOverlay

    $_wslDistroForTerm = $script:ResolvedBuilderDistribution
    Set-Step "Layering MiOS build essentials onto $_wslDistroForTerm..."
    $devVmTomlCands = @(
        'M:\etc\mios\mios.toml',
        (Join-Path $script:MiosRepoDir 'usr\share\mios\mios.toml'),
        'M:\usr\share\mios\mios.toml'
    )
    $miosEssentials  = ''
    $essentialsSource = ''
    foreach ($p in $devVmTomlCands) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $tomlText = [IO.File]::ReadAllText($p, (New-Object System.Text.UTF8Encoding($false)))
            $rx = '(?ms)^\[packages\.dev_vm_essentials\]\s*$.*?^\s*pkgs\s*=\s*\[(?<list>.*?)\]\s*$'
            $m  = [regex]::Match($tomlText, $rx)
            if ($m.Success) {
                # Strip TOML inline comments per line FIRST, then split.
                # PS regex without (?m) makes `$` match end-of-string, which
                # would let `# comment` text bleed across newlines into the
                # next package entry.
                $stripped = ($m.Groups['list'].Value -split "`n" |
                             ForEach-Object { ($_ -replace '#.*$', '').Trim() }) -join ' '
                $pkgs = @(
                    $stripped -split ',' |
                    ForEach-Object {
                        $s = $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n")
                        if ($s) { $s }
                    }
                )
                if ($pkgs.Count -gt 0) {
                    $miosEssentials  = ($pkgs -join ' ')
                    $essentialsSource = $p
                    Log-Ok "Sourced $($pkgs.Count) dev-VM essentials from $p [packages.dev_vm_essentials]"
                    break
                }
            }
        } catch {
            Log-Warn "Failed to parse $p for [packages.dev_vm_essentials]: $($_.Exception.Message)"
        }
    }
    if (-not $miosEssentials) {
        $miosEssentials = 'mkpasswd whois openssl python3-passlib bootc git iptables nftables fastfetch oh-my-posh python3-rich python3-textual python3-psutil bash-completion'
        Log-Warn "Using fallback dev-VM essentials list (mios.toml [packages.dev_vm_essentials] not found / unparseable)"
    }
    $essentialsRc = -1
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "dnf install -y --quiet $miosEssentials" 2>&1 |
            ForEach-Object { Write-Log "mios-essentials: $_" }
        $script:_essentialsRc = $LASTEXITCODE
    }
    $checkPkgs = ($miosEssentials -split ' ' | Where-Object { $_ } | ForEach-Object { $_ })
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $rpmCmd = "rpm -q --whatprovides $($checkPkgs -join ' ') 2>&1; echo '---'; rpm -q $($checkPkgs -join ' ') 2>&1"
        $script:_rpmCheck = (& wsl.exe -d $_wslDistroForTerm --user root -- bash -c $rpmCmd 2>&1) -join "`n"
    }
    # Count how many of our requested packages have a verified provider on the
    # system. `rpm -q --whatprovides foo` prints lines like "foo-1.2-3.fc44.x86_64"
    # for installed providers, "no package provides foo" for missing.
    $missing = @()
    foreach ($p in $checkPkgs) {
        if ($script:_rpmCheck -notmatch [regex]::Escape("provides $p")) {
            # whatprovides returned a real package; only flag if BOTH queries
            # come up empty.
            if ($script:_rpmCheck -match "package $([regex]::Escape($p)) is not installed" -and
                $script:_rpmCheck -match "no package provides $([regex]::Escape($p))") {
                $missing += $p
            }
        }
    }
    if ($missing.Count -eq 0) {
        Log-Ok "MiOS build essentials layered onto $_wslDistroForTerm ($($checkPkgs.Count) packages verified)"
    } else {
        Log-Warn "MiOS build essentials partial: missing [$($missing -join ', ')] -- driver may fail when it tries to use those"
    }

    $_doFull = $true
    if ($_devOverlayTomlText -or ($miosEssentials -and (Test-Path -LiteralPath ($devVmTomlCands | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)))) {
        try {
            $_topPath = $devVmTomlCands | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if ($_topPath) {
                $_topToml = [IO.File]::ReadAllText($_topPath, (New-Object System.Text.UTF8Encoding($false)))
                $_bsM = [regex]::Match($_topToml, '(?ms)^\[bootstrap\]\s*$.*?^\s*dev_overlay_full\s*=\s*(?<v>true|false)\s*$')
                if ($_bsM.Success -and $_bsM.Groups['v'].Value -eq 'false') { $_doFull = $false }
            }
        } catch {}
    }
    if (-not $_doFull) {
        Log-Ok "[packages.dev_overlay] full layer SKIPPED ([bootstrap].dev_overlay_full=false)"
    } else {
        $_devOverlayTomlText2 = $null
        foreach ($p in $devVmTomlCands) {
            if (-not (Test-Path -LiteralPath $p)) { continue }
            try { $_devOverlayTomlText2 = [IO.File]::ReadAllText($p, (New-Object System.Text.UTF8Encoding($false))); break } catch {}
        }
        if ($_devOverlayTomlText2) {
            # Pull section list from [packages.dev_overlay].sections
            $_doSec = [regex]::Match($_devOverlayTomlText2, '(?ms)^\[packages\.dev_overlay\]\s*$.*?^\s*sections\s*=\s*\[(?<arr>.*?)\]\s*$')
            $_allOverlayPkgs = @()
            $_secList = @()
            if ($_doSec.Success) {
                $_secStripped = ($_doSec.Groups['arr'].Value -split "`n" |
                                ForEach-Object { ($_ -replace '#.*$', '').Trim() }) -join ' '
                $_secList = @($_secStripped -split ',' | ForEach-Object { $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n") } | Where-Object { $_ })
                Log-Ok "[packages.dev_overlay].sections -> $($_secList -join ', ')"
                # Enable repos FIRST so packages from rpmfusion / fedora-workstation
                # resolve. Insert the repos section if it's not already first.
                if ($_secList -notcontains 'repos') { $_secList = @('repos') + $_secList }
                foreach ($_sec in $_secList) {
                    $_rxSec = '(?ms)^\[packages\.' + [regex]::Escape($_sec) + '\]\s*$.*?^\s*pkgs\s*=\s*\[(?<list>.*?)\]\s*$'
                    $_secM  = [regex]::Match($_devOverlayTomlText2, $_rxSec)
                    if (-not $_secM.Success) { continue }
                    $_stripped = ($_secM.Groups['list'].Value -split "`n" |
                                  ForEach-Object { ($_ -replace '#.*$', '').Trim() }) -join ' '
                    $_secPkgs = @($_stripped -split ',' | ForEach-Object { $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n") } | Where-Object { $_ })
                    if ($_secPkgs.Count -gt 0) {
                        $_allOverlayPkgs += $_secPkgs
                    }
                }
            }
            $_allOverlayPkgs = @($_allOverlayPkgs | Select-Object -Unique)
            if ($_allOverlayPkgs.Count -gt 0) {
                Log-Ok "Layering $($_allOverlayPkgs.Count) packages from [packages.dev_overlay].sections (full MiOS OCI parity, est. 20-40 min)..."
                # Chunk to keep wsl.exe argv under Windows' command-line cap.
                $_chunkSize = 60
                $_chunkN = 0
                $_chunkTotal = [math]::Ceiling($_allOverlayPkgs.Count / $_chunkSize)
                for ($i = 0; $i -lt $_allOverlayPkgs.Count; $i += $_chunkSize) {
                    $_chunkN++
                    $_endIdx = [math]::Min($i + $_chunkSize - 1, $_allOverlayPkgs.Count - 1)
                    $_chunk = $_allOverlayPkgs[$i..$_endIdx]
                    Set-Step ("[overlay] dnf chunk {0}/{1} ({2} pkgs)..." -f $_chunkN, $_chunkTotal, $_chunk.Count)
                    & {
                        $ErrorActionPreference = 'Continue'
                        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                            $PSNativeCommandUseErrorActionPreference = $false
                        }
                        & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "dnf install -y --skip-unavailable --skip-broken --quiet $($_chunk -join ' ')" 2>&1 |
                            ForEach-Object { Write-Log "mios-overlay: $_" }
                    }
                }
                Log-Ok "[packages.dev_overlay] full layer complete ($($_allOverlayPkgs.Count) requested)"
            }
            # Flatpaks from [desktop].flatpaks
            $_fpSec = [regex]::Match($_devOverlayTomlText2, '(?ms)^\[desktop\]\s*$.*?^\s*flatpaks\s*=\s*\[(?<arr>.*?)\]\s*$')
            if ($_fpSec.Success) {
                $_fpStripped = ($_fpSec.Groups['arr'].Value -split "`n" |
                                ForEach-Object { ($_ -replace '#.*$', '').Trim() }) -join ' '
                $_flatpaks = @($_fpStripped -split ',' | ForEach-Object { $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n") } | Where-Object { $_ })
                if ($_flatpaks.Count -gt 0) {
                    Log-Ok "Installing $($_flatpaks.Count) flatpak refs from [desktop].flatpaks..."
                    # Add flathub remote first (idempotent).
                    & {
                        $ErrorActionPreference = 'Continue'
                        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                            $PSNativeCommandUseErrorActionPreference = $false
                        }
                        & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "command -v flatpak >/dev/null 2>&1 && flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo 2>&1 || echo 'flatpak not installed -- skipping flathub remote'" 2>&1 |
                            ForEach-Object { Write-Log "mios-flatpak: $_" }
                    }
                    $_flatpakInstalled = $false
                    & {
                        $ErrorActionPreference = 'Continue'
                        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                            $PSNativeCommandUseErrorActionPreference = $false
                        }
                        $_probe = & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "command -v flatpak >/dev/null 2>&1 && echo OK || echo MISS" 2>&1
                        if ($_probe -match 'OK') { $script:_flatpakInstalledRef = $true }
                    }
                    if (-not $script:_flatpakInstalledRef) {
                        Log-Warn "flatpak binary not present in $_wslDistroForTerm -- all $($_flatpaks.Count) [desktop].flatpaks deferred to bootc-switch (full MiOS OCI image has flatpak baked in)"
                    } else {
                        & {
                            $ErrorActionPreference = 'Continue'
                            if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                                $PSNativeCommandUseErrorActionPreference = $false
                            }
                            & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "command -v dbus-launch >/dev/null 2>&1 || dnf install -y --quiet dbus-x11 xorg-x11-xauth 2>&1 | tail -5" 2>&1 |
                                ForEach-Object { Write-Log "mios-flatpak-dbus-prereq: $_" }
                        }
                        & {
                            $ErrorActionPreference = 'Continue'
                            if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                                $PSNativeCommandUseErrorActionPreference = $false
                            }
                            '' | & wsl.exe -d $_wslDistroForTerm --user root --exec env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 dbus-run-session -- flatpak update --system --appstream flathub 2>&1 |
                                ForEach-Object { Write-Log "mios-flatpak-runtime: $_" }
                        }
                        # Ensure ALL configured remotes are added before the
                        # install loop runs (flathub is added separately
                        # elsewhere; fedora + gnome-nightly land here so
                        # entries with "fedora:" / "gnome-nightly:" prefixes
                        # in [desktop].flatpaks can install).
                        & {
                            $ErrorActionPreference = 'Continue'
                            if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                                $PSNativeCommandUseErrorActionPreference = $false
                            }
                            # build-mios.ps1 is a CRLF file, so a multi-line `bash -c "..."` string
                            # carries \r on every line -> wsl bash chokes "$'\r': command not found".
                            # Strip CR before handing it to bash.
                            $_remotesScript = ("
                                sudo flatpak remote-add --system --if-not-exists flathub-beta https://flathub.org/beta-repo/flathub-beta.flatpakrepo 2>/dev/null || true
                                sudo flatpak remote-add --system --if-not-exists fedora oci+https://registry.fedoraproject.org 2>/dev/null || true
                                sudo flatpak remote-add --system --if-not-exists gnome-nightly https://nightly.gnome.org/gnome-nightly.flatpakrepo 2>/dev/null || true
                                sudo dnf config-manager setopt updates-testing.enabled=1 2>/dev/null || true
                                echo '[appstream] updating flathub-beta'
                                sudo env FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 flatpak update --system --appstream flathub-beta 2>&1 || echo '[appstream] WARN: flathub-beta update failed'
                                echo '[appstream] updating fedora'
                                sudo env FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 flatpak update --system --appstream fedora 2>&1 || echo '[appstream] WARN: fedora update failed'
                                echo '[appstream] updating gnome-nightly'
                                sudo env FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 flatpak update --system --appstream gnome-nightly 2>&1 || echo '[appstream] WARN: gnome-nightly update failed'
                            ") -replace "`r", ""
                            & wsl.exe -d $_wslDistroForTerm --user root -- bash -c $_remotesScript 2>&1 | ForEach-Object { Write-Log "mios-flatpak-remotes: $_" }
                        }
                        $_fpOk = 0; $_fpFail = 0
                        foreach ($_fpEntry in $_flatpaks) {
                            if ($_fpEntry -match '^([a-zA-Z0-9_-]+):(.+)$') {
                                $_fpRemote = $matches[1]
                                $_fp       = $matches[2]
                            } else {
                                $_fpRemote = 'flathub'
                                $_fp       = $_fpEntry
                            }
                            Set-Step ("[overlay] flatpak install {0}:{1}..." -f $_fpRemote, $_fp)
                            & {
                                $ErrorActionPreference = 'Continue'
                                if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                                    $PSNativeCommandUseErrorActionPreference = $false
                                }
                                # --noninteractive selects Flatpak's quiet transaction and
                                # suppresses progress. --assumeyes plus closed stdin keeps
                                # automation unattended; plain output emits progress lines.
                                # Root must not inherit WSLg's uid-1000 runtime/session bus.
                                '' | & wsl.exe -d $_wslDistroForTerm --user root --exec env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 dbus-run-session -- flatpak install --system --assumeyes --or-update $_fpRemote $_fp 2>&1 |
                                    ForEach-Object { Write-Log "mios-flatpak: $_" }
                                $script:_fpLastRc = $LASTEXITCODE
                            }
                            if ($script:_fpLastRc -eq 0) {
                                Log-Ok "[overlay] flatpak install OK: $_fpRemote/$_fp"
                                $_fpOk++
                            } else {
                                $_fpRetryLog = New-Object System.Collections.Generic.List[string]
                                Log-Warn "[overlay] flatpak install attempt 1 failed (exit $($script:_fpLastRc)): $_fpRemote/$_fp -- retrying with --arch=x86_64 -v"
                                & {
                                    $ErrorActionPreference = 'Continue'
                                    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                                        $PSNativeCommandUseErrorActionPreference = $false
                                    }
                                    '' | & wsl.exe -d $_wslDistroForTerm --user root --exec env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS FLATPAK_FANCY_OUTPUT=0 FLATPAK_TTY_PROGRESS=0 dbus-run-session -- flatpak install --assumeyes --or-update --system --arch=x86_64 -v $_fpRemote $_fp 2>&1 |
                                        ForEach-Object { Write-Log "mios-flatpak-retry: $_"; [void]$_fpRetryLog.Add($_) }
                                    $script:_fpRetryRc = $LASTEXITCODE
                                }
                                if ($script:_fpRetryRc -eq 0) {
                                    Log-Ok "[overlay] flatpak install OK on retry: $_fp"
                                    $_fpOk++
                                } else {
                                    $_fpTail = ($_fpRetryLog | Select-Object -Last 5) -join ' | '
                                    Log-Warn "[overlay] flatpak install FAILED both attempts (last exit $($script:_fpRetryRc)): $_fp"
                                    Log-Warn "  diagnostic tail: $_fpTail"
                                    Log-Warn "  full output is in the unified log: $LogFile"
                                    Log-Warn "  OCI image build (mios build -> automation/61-flatpak-bake.sh) retries at bake time; first-boot service mios-flatpak-install also retries on every host boot."
                                    $_fpFail++
                                }
                            }
                        }
                        $_fpSummary = "[desktop].flatpaks install pass: $_fpOk OK / $_fpFail failed (of $($_flatpaks.Count) total)"
                        if ($_fpFail -gt 0) { Log-Warn $_fpSummary } else { Log-Ok $_fpSummary }
                    }
                }
            }
        }
    }

    Set-Step "Installing NVIDIA WSL userland in $_wslDistroForTerm (Vulkan ICD + GLX/EGL libs)..."
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $_nvOut = & wsl.exe -d $_wslDistroForTerm --user root -- bash /usr/libexec/mios/install-nvidia-wsl-userland.sh 2>&1
        $_nvExit = $LASTEXITCODE
        if ($_nvExit -eq 0) {
            $_nvSummary = ($_nvOut | Where-Object { $_ -match '^\s*\[(ok|skip|warn)\]' } | Select-Object -Last 3) -join ' / '
            if (-not $_nvSummary) { $_nvSummary = '(silent - see install log if needed)' }
            Log-Ok "NVIDIA WSL userland: $_nvSummary"
        } else {
            Log-Warn "NVIDIA WSL userland install exit=$_nvExit; GUI apps may fall back to dzn-only Vulkan path"
        }
    }

    Set-Step "Configuring podman netavark for WSL2 (firewall_driver=none)..."
    $netavarkConf = @'
[network]
firewall_driver = "none"
'@
    $confDropIn = "/etc/containers/containers.conf.d/mios-wsl2.conf"
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        # Use a here-doc piped through wsl so we don't have to escape
        # the [section] brackets through bash -c.
        $netavarkConf | & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "mkdir -p /etc/containers/containers.conf.d && cat > $confDropIn" 2>&1 |
            ForEach-Object { Write-Log "netavark-conf: $_" }
        $script:_netavarkRc = $LASTEXITCODE
    }
    if ($script:_netavarkRc -eq 0) {
        Log-Ok "netavark configured for WSL2 (firewall_driver=none in $confDropIn)"
    } else {
        Log-Warn "Failed to write netavark drop-in (exit $($script:_netavarkRc)) -- podman build may fail at first network step"
    }

    Set-Step "Seeding MiOS terminal experience inside $_wslDistroForTerm..."
    $miosSeedScript = @'
set -e
# Symlink the canonical MiOS dirs to the M:\ overlay so existing
# profile.d scripts (mios-prompt.sh, zz-mios-motd.sh) resolve their
# dependencies. -e check skips re-symlinking if the path is already
# real (post-bootc-switch state).
if [ -d /mnt/m/usr/libexec/mios ] && [ ! -e /usr/libexec/mios ]; then
    ln -snf /mnt/m/usr/libexec/mios /usr/libexec/mios
fi
if [ -d /mnt/m/usr/share/mios ] && [ ! -e /usr/share/mios ]; then
    ln -snf /mnt/m/usr/share/mios /usr/share/mios
fi
if ! id mios >/dev/null 2>&1; then
    set +e
    useradd -m -s /bin/bash -G wheel mios 2>/dev/null || \
        useradd -m -s /bin/bash mios 2>/dev/null
    _useradd_rc=$?
    set -e
    if id mios >/dev/null 2>&1; then
        echo 'mios:mios' | chpasswd 2>/dev/null || true
        # Passwordless sudo for mios so build-mios.ps1's later steps
        # (smoke test, container-host setup) don't prompt.
        if [ -d /etc/sudoers.d ]; then
            printf 'mios ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/10-mios-nopasswd
            chmod 0440 /etc/sudoers.d/10-mios-nopasswd
        fi
        echo "[mios-seed] mios user created (uid=$(id -u mios), groups=$(id -Gn mios))"
    else
        echo "[mios-seed] WARN: useradd mios failed (rc=$_useradd_rc) -- wsl.conf default=mios will fail until the user exists"
    fi
fi
if [ ! -f /etc/wsl.conf ]; then
    printf '[boot]\nsystemd=true\n' > /etc/wsl.conf
    echo "[mios-seed] /etc/wsl.conf created with [boot] systemd=true"
elif ! grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then
    printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf
    echo "[mios-seed] /etc/wsl.conf: appended [boot] systemd=true"
elif ! grep -qE '^[[:space:]]*systemd[[:space:]]*=[[:space:]]*true[[:space:]]*$' /etc/wsl.conf 2>/dev/null; then
    if grep -qE '^[[:space:]]*systemd[[:space:]]*=' /etc/wsl.conf 2>/dev/null; then
        sed -i 's|^[[:space:]]*systemd[[:space:]]*=.*|systemd=true|' /etc/wsl.conf
        echo "[mios-seed] /etc/wsl.conf: rewrote systemd=<other> to systemd=true"
    else
        sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf
        echo "[mios-seed] /etc/wsl.conf: inserted systemd=true under existing [boot]"
    fi
else
    echo "[mios-seed] /etc/wsl.conf: [boot] systemd=true already set"
fi
if id mios >/dev/null 2>&1; then
    if ! grep -q '^\[user\]' /etc/wsl.conf 2>/dev/null; then
        printf '\n[user]\ndefault=mios\n' >> /etc/wsl.conf
        echo "[mios-seed] /etc/wsl.conf: appended [user].default=mios"
    elif ! grep -qE '^[[:space:]]*default[[:space:]]*=' /etc/wsl.conf 2>/dev/null; then
        sed -i '/^\[user\]/a default=mios' /etc/wsl.conf
        echo "[mios-seed] /etc/wsl.conf: inserted default=mios under existing [user]"
    elif ! grep -qE '^[[:space:]]*default[[:space:]]*=[[:space:]]*mios[[:space:]]*$' /etc/wsl.conf 2>/dev/null; then
        sed -i 's|^[[:space:]]*default[[:space:]]*=.*|default=mios|' /etc/wsl.conf
        echo "[mios-seed] /etc/wsl.conf: rewrote default=<other> to default=mios"
    else
        echo "[mios-seed] /etc/wsl.conf: default=mios already set"
    fi
fi
# Pre-bootc bridge: source mios.git profile.d scripts from /mnt/m/ on
# every interactive bash login, IF the canonical /etc/profile.d/mios-*
# scripts aren't installed yet (pre-bootc-switch). After bootc switch,
# the canonical scripts exist and this bridge skips silently.
mkdir -p /etc/profile.d
cat > /etc/profile.d/00-mios-pre-bootc.sh <<'EOPROFILE'
# /etc/profile.d/00-mios-pre-bootc.sh
# Pre-bootc-switch MiOS terminal-experience bridge.
# Sources mios.git's profile.d scripts from /mnt/m/ until the OCI
# image's bootc-switch lands them at the canonical /etc/profile.d/.
# Auto-disables once /etc/profile.d/mios-prompt.sh exists at root.
if [ ! -e /etc/profile.d/mios-prompt.sh ] && [ -d /mnt/m/etc/profile.d ]; then
    for _miosf in /mnt/m/etc/profile.d/mios-*.sh /mnt/m/etc/profile.d/zz-mios-*.sh; do
        [ -r "$_miosf" ] && . "$_miosf"
    done
    unset _miosf
fi
EOPROFILE
chmod 0644 /etc/profile.d/00-mios-pre-bootc.sh
echo "[mios-seed] symlinks + pre-bootc bridge installed"
if [ -d /mnt/m/MiOS/btop ]; then
    mkdir -p /etc/btop/themes
    if [ -f /mnt/m/MiOS/btop/btop.conf ]; then
        cp -f /mnt/m/MiOS/btop/btop.conf /etc/btop/btop.conf
        chmod 0644 /etc/btop/btop.conf
    fi
    if [ -f /mnt/m/MiOS/btop/themes/mios.theme ]; then
        cp -f /mnt/m/MiOS/btop/themes/mios.theme /etc/btop/themes/mios.theme
        chmod 0644 /etc/btop/themes/mios.theme
    fi
    echo "[mios-seed] btop system-wide config staged at /etc/btop/"

    # Per-user copies (kept for operators who customize per-user).
    for _u in mios root; do
        if id "$_u" >/dev/null 2>&1; then
            _uhome=$(getent passwd "$_u" | cut -d: -f6)
            if [ -n "$_uhome" ] && [ -d "$_uhome" ]; then
                mkdir -p "$_uhome/.config/btop/themes"
                if [ -f /mnt/m/MiOS/btop/btop.conf ]; then
                    cp -f /mnt/m/MiOS/btop/btop.conf "$_uhome/.config/btop/btop.conf"
                fi
                if [ -f /mnt/m/MiOS/btop/themes/mios.theme ]; then
                    cp -f /mnt/m/MiOS/btop/themes/mios.theme "$_uhome/.config/btop/themes/mios.theme"
                fi
                chown -R "$_u":"$_u" "$_uhome/.config/btop" 2>/dev/null || true
                echo "[mios-seed] btop config + mios.theme staged for $_u at $_uhome/.config/btop/"
            fi
        fi
    done
fi
if [ -d /var/lib/flatpak/exports/bin ]; then
    mkdir -p /usr/local/bin
    # short-name -> full-app-id pairs (mirrors mios.toml [desktop].flatpaks)
    while IFS='|' read -r _short _appid; do
        _wrapper="/var/lib/flatpak/exports/bin/$_appid"
        _link="/usr/local/bin/$_short"
        if [ -x "$_wrapper" ] && [ ! -e "$_link" ]; then
            ln -snf "$_wrapper" "$_link"
            echo "[mios-seed] flatpak symlink: $_short -> $_appid"
        fi
    done <<EOFLATPAK
epiphany|org.gnome.Epiphany
nautilus|org.gnome.Nautilus
flatseal|com.github.tchx84.Flatseal
gnome-software|org.gnome.Software
extension-manager|com.mattjakeman.ExtensionManager
codium|com.vscodium.codium
code|com.vscodium.codium
EOFLATPAK
fi
'@
    $seedTmpWin = Join-Path $env:TEMP 'mios-seed.sh'
    $seedTmpWsl = '/mnt/m/MiOS/.tmp-seed.sh'
    # Write LF-only via [System.IO.File]::WriteAllText with no-BOM UTF-8;
    # also drop a copy at /mnt/m/MiOS/.tmp-seed.sh so bash inside the
    # dev VM has a known automounted path.
    $utf8NoBom    = New-Object System.Text.UTF8Encoding($false)
    $miosSeedLF   = $miosSeedScript -replace "`r`n", "`n"
    $miosTmpDir   = 'M:\MiOS'
    if (-not (Test-Path -LiteralPath $miosTmpDir)) { New-Item -ItemType Directory -Path $miosTmpDir -Force | Out-Null }
    [System.IO.File]::WriteAllText('M:\MiOS\.tmp-seed.sh', $miosSeedLF, $utf8NoBom)
    [System.IO.File]::WriteAllText($seedTmpWin,            $miosSeedLF, $utf8NoBom)
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & wsl.exe -d $_wslDistroForTerm --user root -- bash $seedTmpWsl 2>&1 |
            ForEach-Object { Write-Log "mios-seed: $_" }
        $script:_seedRc = $LASTEXITCODE
    }
    Remove-Item -LiteralPath 'M:\MiOS\.tmp-seed.sh' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $seedTmpWin -Force -ErrorAction SilentlyContinue
    if ($script:_seedRc -eq 0) {
        Log-Ok "MiOS terminal experience seeded onto $_wslDistroForTerm"
    } else {
        Log-Warn "MiOS terminal experience seed failed (exit $($script:_seedRc)) -- bare bash login until bootc switch"
    }

    Set-Step "Compiling MiOS dconf system-db in $_wslDistroForTerm..."
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & wsl.exe -d $_wslDistroForTerm --user root -- bash -c 'command -v dconf >/dev/null 2>&1 && dconf update 2>&1 || echo dconf-binary-missing-skipped; ls /etc/dconf/db/local 2>&1 | head -1' 2>&1 |
            ForEach-Object { Write-Log "mios-dconf: $_" }
    }
    Log-Ok "MiOS dconf system-db compiled (adw-gtk3-dark + prefer-dark active for all user-bus sessions)"

    Set-Step "Installing Bibata-Modern-Classic cursor in $_wslDistroForTerm..."
    # Up to 3 attempts -- the first wsl.exe call right after a fresh
    # dev-VM provision occasionally returns 127 (transient distro-ready
    # race; the next call succeeds). Use $script: scope so the exit
    # code propagates out of the & { ... } block.
    $script:_bibataExit = 1
    $script:_bibataOutput = @()
    for ($_try = 1; $_try -le 3 -and $script:_bibataExit -ne 0; $_try++) {
        if ($_try -gt 1) {
            Write-Log "mios-bibata: attempt $_try after exit=$($script:_bibataExit)"
            Start-Sleep -Seconds 5
        }
        & {
            $ErrorActionPreference = 'Continue'
            if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
                $PSNativeCommandUseErrorActionPreference = $false
            }
            $_bibataScript = @'
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
set -e
if [ -d /usr/share/icons/Bibata-Modern-Classic ] && [ -n "$(ls -A /usr/share/icons/Bibata-Modern-Classic/cursors 2>/dev/null)" ]; then
    echo "Bibata already installed -- skipping"
    exit 0
fi
command -v curl >/dev/null || { echo "curl missing in dev VM"; exit 127; }
command -v tar  >/dev/null || { echo "tar missing in dev VM";  exit 127; }
VER=$(curl -sSL -H "Accept: application/vnd.github+json" https://api.github.com/repos/ful1e5/Bibata_Cursor/releases/latest 2>/dev/null | grep -oE "\"tag_name\":\\s*\"v[0-9.]+\"" | head -1 | grep -oE "v[0-9.]+" | sed "s/^v//") || VER=""
[ -z "$VER" ] && VER="2.0.7"
URL="https://github.com/ful1e5/Bibata_Cursor/releases/download/v${VER}/Bibata-Modern-Classic.tar.xz"
echo "Bibata v${VER}: ${URL}"
TARBALL=$(mktemp --suffix=.tar.xz)
curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 -o "$TARBALL" "$URL" || { echo "Bibata download failed"; rm -f "$TARBALL"; exit 1; }
SIZE=$(stat -c %s "$TARBALL" 2>/dev/null || echo 0)
echo "Bibata tarball downloaded: ${SIZE} bytes"
if [ "${SIZE:-0}" -lt 100000 ]; then
    echo "Bibata tarball too small: ${SIZE} bytes - aborting" >&2
    rm -f "$TARBALL"
    exit 1
fi
mkdir -p /usr/share/icons
tar -xJf "$TARBALL" -C /usr/share/icons/ || { echo "Bibata tar extract failed"; rm -f "$TARBALL"; exit 1; }
rm -f "$TARBALL"
if [ ! -d /usr/share/icons/Bibata-Modern-Classic/cursors ] || [ -z "$(ls -A /usr/share/icons/Bibata-Modern-Classic/cursors 2>/dev/null)" ]; then
    echo "Bibata extraction failed -- cursors dir empty" >&2
    exit 1
fi
CURSORN=$(ls /usr/share/icons/Bibata-Modern-Classic/cursors | wc -l)
echo "Bibata installed: ${CURSORN} cursors"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache /usr/share/icons/Bibata-Modern-Classic 2>&1 || true
exit 0
'@
            $_bibataB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($_bibataScript -replace "`r`n","`n")))
            $script:_bibataOutput = & wsl.exe -d $_wslDistroForTerm --user root -- bash -c "echo $_bibataB64 | base64 -d | bash" 2>&1
            $script:_bibataExit = $LASTEXITCODE
        }
        foreach ($_line in $script:_bibataOutput) { Write-Log "mios-bibata: $_line" }
    }
    if ($script:_bibataExit -eq 0) {
        Log-Ok "MiOS Bibata cursor theme staged"
    } else {
        Log-Warn ("Bibata cursor install failed after 3 attempts (exit=$($script:_bibataExit)); dconf points at Bibata-Modern-Classic but theme dir is missing -- run ``mios update`` or install manually from https://github.com/ful1e5/Bibata_Cursor/releases")
    }

    Set-Step "Installing MiOS AI CLIs (Claude Code + Gemini CLI) in $_wslDistroForTerm..."
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $_aiOut = & wsl.exe -d $_wslDistroForTerm --user root -- bash /usr/libexec/mios/install-ai-clis.sh 2>&1
        $_aiOut | ForEach-Object { Write-Log "mios-ai-cli: $_" }
    }
    Log-Ok "MiOS AI CLIs installed (claude + gemini available on PATH)"

    Set-Step "Reconciling mios home ownership + locale in $_wslDistroForTerm..."
    & {
        $ErrorActionPreference = 'Continue'
        if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
            $PSNativeCommandUseErrorActionPreference = $false
        }
        # Plain words only in the bash -c (no double-quotes / parens -- PowerShell
        # native-arg quoting mangles them passing to wsl.exe; see the dconf note).
        & wsl.exe -d $_wslDistroForTerm --user root -- bash -c 'chown -R mios:mios /var/home/mios 2>/dev/null || true; echo LANG=C.utf8 > /etc/locale.conf; echo reconcile-done home=mios:mios locale=C.utf8' 2>&1 |
            ForEach-Object { Write-Log "mios-reconcile: $_" }
    }
    Log-Ok "mios home reconciled to mios:mios + dev-VM locale pinned to C.utf8"

    Set-Step "wsl --shutdown so .wslconfig + /etc/wsl.conf take effect on next entry..."
    & wsl.exe --shutdown 2>&1 |
        ForEach-Object { Write-Log "wsl-shutdown-end-phase3: $_" }
    Log-Ok "WSL2 utility VM shutdown -- next entry uses mirrored networking + mios as default user"

    End-Phase 3

    Start-Phase 4
    try { Set-MiosWslConfig -RamGB $HW.RamGB -Cpus $HW.Cpus -Force } catch { Log-Warn "Set-MiosWslConfig (Phase-4 recheck): $($_.Exception.Message)" }

    Set-Step "Adding Windows Firewall LAN inbound rules for MiOS service ports..."
    try { Set-MiosLanFirewallRules } catch { Log-Warn "Set-MiosLanFirewallRules: $($_.Exception.Message)" }
    try { Set-MiosLanPortProxy }     catch { Log-Warn "Set-MiosLanPortProxy: $($_.Exception.Message)" }

    End-Phase 4

    # -- Phase 5 -- Verify Windows build context ------------------------------
    # Build runs via 'podman build' from the Windows clone -- no machine exec needed.
    Start-Phase 5
    # mios.git is overlaid AT $MiosRepoDir root, per.
    $repoPath = $MiosRepoDir
    if (Test-Path (Join-Path $repoPath "Containerfile")) {
        Log-Ok "Build context ready at $repoPath"
    } else {
        throw "mios.git Containerfile missing at $repoPath -- re-run without -BuildOnly to reclone"
    }
    End-Phase 5

    Restore-PodmanPrefix   # auto-recover from any previous rename
    Install-MiosWindowsTools   # winget install [packages.windows] (fastfetch, btop, pwsh, ...)
    Install-WindowsBranding
    Install-MiOSTmuxProfile    # stage .tmux.conf and reload running tmux servers

    $devHealthy = Test-MiosDevDistroHealthy
    if ($devHealthy -and ($env:MIOS_RENAME_DISTRO -in @('1','true','TRUE','yes'))) {
        Rename-PodmanDevDistro
    }

    Install-MiosLauncher

    if ($BootstrapOnly) {
        Log-Ok "-BootstrapOnly mode: dev VM provisioned, Windows install complete."
        $env:MIOS_NO_AUTO_CHAIN = '1'
        $_dispGb = 256
        try { $v = Get-Volume -DriveLetter M -ErrorAction SilentlyContinue; if ($v) { $_dispGb = [math]::Round($v.Size/1GB,0) } } catch {}
        $_completeTitle = Get-MiosTomlValue -Section 'messages.install_complete' -Key 'title' -Default 'MiOS Windows-side install complete'
        $_completeBullets = @(Get-MiosTomlValue -Section 'messages.install_complete' -Key 'bullets' -Default @(
            ('M:\ partition ({0} GB NTFS, label MIOS-DEV)' -f $_dispGb),
            'Podman Desktop + podman-MiOS-DEV machine',
            'mios.git + mios-bootstrap overlaid at M:\',
            'MiOS terminal essentials layered into MiOS-DEV',
            'Native Windows app: Start Menu + Desktop + per-verb shortcuts',
            'MiOS PowerShell profile (oh-my-posh, dashboard, mios <verb>)'
        ))
        # Substitute {disk_gb} placeholder if the operator templated it
        # in a custom mios.toml entry.
        $_completeBullets = @($_completeBullets | ForEach-Object { $_ -replace '\{disk_gb\}', $_dispGb })
        $_fc = Get-MiosTomlValue -Section 'branding.dashboard' -Key 'frame_chars' -Default "$([char]0x256D)$([char]0x2500)$([char]0x256E)$([char]0x2502)$([char]0x2570)$([char]0x256F)"
        if (-not $_fc -or $_fc.Length -lt 6) { $_fc = "$([char]0x256D)$([char]0x2500)$([char]0x256E)$([char]0x2502)$([char]0x2570)$([char]0x256F)" }
        $_TL = $_fc[0]; $_TH = $_fc[1]; $_TR = $_fc[2]
        $_TV = $_fc[3]; $_BL = $_fc[4]; $_BR = $_fc[5]
        # Frame width comes from the SAME Get-MiosFrameWidth helper that
        # drives every other framed surface in this script -- one
        # formula, one source.  Subtract 2 for the 2-cell left-indent
        # the install-complete banner uses ('  +...+').
        $_inner = (Get-MiosFrameWidth) - 2
        if ($_inner -lt 40) { $_inner = 40 }
        $_titlePadded = '  ' + $_TV + ' ' + $_completeTitle.PadRight($_inner - 1) + ' ' + $_TV
        Write-Host ''
        Write-Host ('  ' + $_TL + ([string]$_TH * $_inner) + $_TR) -ForegroundColor DarkCyan
        Write-Host $_titlePadded -ForegroundColor Cyan
        Write-Host ('  ' + $_BL + ([string]$_TH * $_inner) + $_BR) -ForegroundColor DarkCyan
        Write-Host ''
        # Section labels resolve through mios.toml [messages.install_complete]
        # (SSOT). Operators rebrand the installer's end-of-flow narrative via
        # mios.html without touching code.
        $_lblInstalled = Get-MiosTomlValue -Section 'messages.install_complete' -Key 'installed_lead' -Default '    Installed ...............................................................'
        $_lblNextSteps = Get-MiosTomlValue -Section 'messages.install_complete' -Key 'next_steps'     -Default "    What's next? Type any of these in the MiOS terminal:"
        Write-Host $_lblInstalled -ForegroundColor DarkGray
        foreach ($_b in $_completeBullets) {
            Write-Host ('      [+] ' + $_b) -ForegroundColor Green
        }
        Write-Host ''
        Write-Host $_lblNextSteps -ForegroundColor White
        $_verbHints = @(
            @{ name = 'build';  desc = 'open mios.html, save, then build the OCI image' },
            @{ name = 'config'; desc = 'edit mios.toml in the HTML configurator (no build)' },
            @{ name = 'dash';   desc = 'show the MiOS dashboard (framed banner + fastfetch)' },
            @{ name = 'dev';    desc = 'enter the MiOS-DEV podman machine' },
            @{ name = 'pull';   desc = 'sync M:\ overlay to origin/main' },
            @{ name = 'update'; desc = 're-run the bootstrap (cache-busted)' },
            @{ name = 'help';   desc = 'list every verb' }
        )
        $_tomlText = $null
        foreach ($_cand in @('M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml',(Join-Path $MiosBootstrapShadow 'mios.toml'))) {
            if (Test-Path -LiteralPath $_cand) { try { $_tomlText = [IO.File]::ReadAllText($_cand, (New-Object System.Text.UTF8Encoding($false))); break } catch {} }
        }
        if ($_tomlText) {
            $_verbsBlock = [regex]::Match($_tomlText, '(?ms)^\[verbs\]\s*\r?\n(.*?)(?=^\[|\z)')
            if ($_verbsBlock.Success) {
                $_resolved = @()
                foreach ($_ln in ($_verbsBlock.Groups[1].Value -split "`n")) {
                    $_m = [regex]::Match($_ln, '^\s*([a-z][a-z0-9_-]*)\s*=\s*\{[^}]*description\s*=\s*"([^"]+)"')
                    if ($_m.Success) {
                        $_resolved += @{ name = $_m.Groups[1].Value; desc = $_m.Groups[2].Value }
                    }
                }
                if ($_resolved.Count -gt 0) { $_verbHints = $_resolved }
            }
        }
        $_maxName = ($_verbHints | ForEach-Object { $_.name.Length } | Measure-Object -Maximum).Maximum
        foreach ($_v in $_verbHints) {
            $_pad = ' ' * ($_maxName - $_v.name.Length + 2)
            Write-Host ("      mios {0}{1}-- {2}" -f $_v.name, $_pad, $_v.desc) -ForegroundColor Cyan
        }
        Write-Host ''
        $_lblHubHint = Get-MiosTomlValue -Section 'messages.install_complete' -Key 'hub_hint' -Default '    The MiOS hub shortcut is in your Start Menu / Desktop / Win+Search.'
        Write-Host $_lblHubHint -ForegroundColor DarkGray
        Write-Host ''
        try { [Console]::Out.Flush() } catch {}
        return
    }

    Open-Configurator -RepoDir $MiosRepoDir
    $script:_MiosTomlCache.Clear()  # configurator may have promoted a new layer

    # -- Phase 6 -- Identity ---------------------------------------------------
    Start-Phase 6
    $script:CurStep = "Resolving identity from mios.toml..."
    Show-Dashboard -Force
    $MiosUser     = [string](Get-MiosTomlValue -Section 'identity' -Key 'username' -Default 'mios')
    $MiosHostname = [string](Get-MiosTomlValue -Section 'identity' -Key 'hostname' -Default 'mios')
    $loginCredential = Resolve-MiosLoginCredential
    $MiosHash = $loginCredential.Hash
    if (-not $MiosHash) { $MiosHash = Get-PasswordHash $loginCredential.Plain }
    $loginCredential = $null
    # Credentials come from the operator TOML, with environment override for automation.
    $script:GhcrToken = if ($env:MIOS_GITHUB_TOKEN) { $env:MIOS_GITHUB_TOKEN }
                        elseif ($env:GITHUB_TOKEN)   { $env:GITHUB_TOKEN }
                        else { [string](Get-MiosTomlValue -Section 'auth' -Key 'github_pat' -Default '') }
    $tokStatus = if ($script:GhcrToken) { "provided (masked)" } else { "none -- anonymous pull (may fail)" }

    # The layered TOML fixes the model and bake set before installation.
    $aiDefaults = Resolve-MiosTomlAiDefaults -RepoDir $MiosRepoDir
    $defaultModel = if ($aiDefaults.Model) { $aiDefaults.Model } else { $HW.AiModel }
    $MiosAiModel       = $defaultModel
    $MiosAiEmbedModel  = $aiDefaults.EmbedModel
    $MiosBakeModels = if ($aiDefaults.BakeModels) { $aiDefaults.BakeModels } else { "$defaultModel,$($aiDefaults.EmbedModel)" }
    $_bakeList = @($MiosBakeModels -split ',' | ForEach-Object { $_.Trim() })
    Write-Log "SSOT identity/model selected from layered mios.toml (user=$MiosUser host=$MiosHostname model=$MiosAiModel bake_count=$($_bakeList.Count))"

    Log-Ok "Identity: user=$MiosUser  host=$MiosHostname  password=(hashed)  ghcr=$tokStatus  ai=$MiosAiModel"
    $script:IdentInfo = "User:$MiosUser  Host:$MiosHostname  Base:$($HW.BaseImage -replace 'ghcr.io/ublue-os/ucore-hci:','')  Model:$MiosAiModel"
    End-Phase 6

    # -- Phase 7 -- Write identity ---------------------------------------------
    Start-Phase 7
    $MiosLlamacppBakeModels = $aiDefaults.LlamacppBakeModels
    $MiosVllmBakeModel       = $aiDefaults.VllmBakeModel
    $identBash = @"
mkdir -p /etc/mios
touch /etc/mios/install.env
if [ -n '$MiosHash' ]; then printf "MIOS_USER_PASSWORD_HASH='%s'\n" '$MiosHash' > /etc/mios/secrets.env; chmod 0600 /etc/mios/secrets.env; fi
for p in "MIOS_IDENTITY_USERNAME=$MiosUser" "MIOS_IDENTITY_HOSTNAME=$MiosHostname" "MIOS_AI_MODEL=$MiosAiModel" "MIOS_AI_EMBED_MODEL=$MiosAiEmbedModel" "MIOS_LLAMACPP_BAKE_MODELS=$MiosLlamacppBakeModels" "MIOS_VLLM_BAKE_MODEL=$MiosVllmBakeModel"; do
    k="`${p%%=*}"; v="`${p#*=}"
    grep -q "^`${k}=" /etc/mios/install.env && sed -i "s|^`${k}=.*|`${k}=`${v}|" /etc/mios/install.env || printf '%s=%s\n' "`$k" "`$v" >> /etc/mios/install.env
done
chmod 0640 /etc/mios/install.env
"@
    $identB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($identBash))
    $runB64 = "printf '%s' '$identB64' | base64 -d | bash"
    $written = $false

    # Try wsl.exe (works when machine runs 'MiOS' after bootc switch).
    & wsl.exe -d $BuilderDistro --user root --exec bash -c $runB64 *>$null
    if ($LASTEXITCODE -eq 0) { $written = $true }

    # Try the dev-distro shell via Invoke-DistroSh.
    if (-not $written) {
        Invoke-DistroSh -Bash $runB64 -MachineName $BuilderDistro *>$null
        if ($LASTEXITCODE -eq 0) { $written = $true }
    }

    # Fallback: write via privileged container that mounts the machine's host filesystem.
    if (-not $written) {
        Set-Step "Writing identity via privileged container..."
        $identHost = $identBash -replace '/etc/mios','/host/etc/mios'
        $hostB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($identHost))
        & podman run --rm -i --privileged --security-opt label=disable `
            -v /:/host:z `
            docker.io/library/alpine:latest `
            sh -c "printf '%s' '$hostB64' | base64 -d | sh" `
            *>$null
        if ($LASTEXITCODE -eq 0) { $written = $true }
    }

    if ($written) { Log-Ok "/etc/mios/install.env updated and secrets.env secured" } `
    else { Log-Warn "install.env write failed (non-fatal -- firstboot will use default identity; set MIOS_* vars manually)" }
    End-Phase 7

    # -- App registration + Start Menu -----------------------------------------
    # Phase index varies by mode -- 5 in BootstrapOnly (the trimmed
    # 6-phase Windows-side layout) and 8 in -FullBuild / -BuildOnly
    # (the full 14-phase legacy layout).
    Start-Phase $script:AppRegPhaseId
    $pwsh      = if (Get-Command pwsh -EA SilentlyContinue) { (Get-Command pwsh).Source } else { "powershell.exe" }
    # Entry-point scripts live under $MiosBinDir (materialized in Phase 2).
    # Prefer build-mios.ps1 (current canonical entry); fall back to the
    # legacy install.ps1 redirector if an old install is being re-run.
    $selfSc    = if (Test-Path (Join-Path $MiosBinDir "build-mios.ps1")) {
                     Join-Path $MiosBinDir "build-mios.ps1"
                 } elseif (Test-Path (Join-Path $MiosRepoDir "build-mios.ps1")) {
                     # mios-bootstrap is overlaid at $MiosRepoDir root.
                     Join-Path $MiosRepoDir "build-mios.ps1"
                 } else {
                     Join-Path $MiosBootstrapShadow "install.ps1"
                 }
    $uninstSc  = Join-Path $MiosBinDir "uninstall.ps1"
    $uninstCmd = "$pwsh -ExecutionPolicy Bypass -File `"$uninstSc`""

    if (-not (Test-Path $UninstallRegKey)) { New-Item -Path $UninstallRegKey -Force | Out-Null }
    $_arDisplayTagline = Get-MiosTomlValue -Section 'branding' -Key 'tagline_app' -Default (Get-MiosTomlValue -Section 'branding' -Key 'tagline' -Default 'My Personal Operating System')
    $_arPublisher      = Get-MiosTomlValue -Section 'branding' -Key 'publisher' -Default 'MiOS-DEV'
    $_arAboutUrl       = Get-MiosTomlValue -Section 'branding' -Key 'about_url' -Default 'https://github.com/mios-dev/mios'
    @{
        DisplayName="MiOS - $_arDisplayTagline"; DisplayVersion=$MiosVersion
        Publisher=$_arPublisher; InstallLocation=$MiosInstallDir
        UninstallString=$uninstCmd; QuietUninstallString="$uninstCmd -Quiet"
        URLInfoAbout=$_arAboutUrl
        InstallScope=$MiosScope
        NoModify=[int]1; NoRepair=[int]1
    }.GetEnumerator() | ForEach-Object {
        $regType = if ($_.Value -is [int]) { "DWord" } else { "String" }
        Set-ItemProperty -Path $UninstallRegKey -Name $_.Key -Value $_.Value -Type $regType
    }

    if (-not (Test-Path $StartMenuDir)) { New-Item -ItemType Directory -Path $StartMenuDir -Force | Out-Null }

    $cfgScript = Join-Path $MiosInstallDir 'mios-configurator.ps1'
    @"
#Requires -Version 5.1
# Generated by build-mios.ps1. Launches the MiOS HTML configurator
# inside MiOS-DEV via WSLg. Saved mios.toml lands in the dev VM's
# `~/Downloads/mios.toml` and is auto-promoted as the next build's
# source on the next `irm | iex`.
`$ErrorActionPreference = 'SilentlyContinue'
`$d = '$DevDistro'
# Probe canonical name first (post-rename), then podman- prefix
# (pre-rename), then legacy MiOS-BUILDER fallbacks. First responder wins.
foreach (`$cand in @(`$d, "podman-`$d", '$LegacyDevName', "podman-$LegacyDevName")) {
    `$probe = (& wsl.exe -d `$cand --exec bash -c 'echo ok' 2>`$null) -join ''
    if (`$probe.Trim() -eq 'ok') {
        & wsl.exe -d `$cand --exec /usr/libexec/mios/mios-configurator-launch
        exit `$LASTEXITCODE
    }
}
Write-Host '  MiOS-DEV not reachable -- run bootstrap.ps1 first to provision the dev VM' -ForegroundColor Yellow
exit 1
"@ | Set-Content -Path $cfgScript -Encoding UTF8 -Force

    $vmconnect = Join-Path $env:WINDIR 'System32\vmconnect.exe'
    @(
        @{ F="Uninstall MiOS.lnk";       T=$pwsh;      A="-ExecutionPolicy Bypass -File `"$uninstSc`"";  D="Remove MiOS (preserves per-user config)" }
        @{ F="MiOS Enhanced Session.lnk"; T=$vmconnect; A="";                                            D="Connect to a MiOS Hyper-V VM with Enhanced Session (clipboard, dynamic resolution, audio, USB)" }
    ) | ForEach-Object {
        # Only ship Enhanced Session shortcut if vmconnect.exe exists
        # (Hyper-V client tools installed). On Windows Home, Hyper-V
        # client tools aren't present; skip silently rather than
        # creating a broken shortcut.
        if ($_.F -eq 'MiOS Enhanced Session.lnk' -and -not (Test-Path -LiteralPath $vmconnect)) { return }
        New-Shortcut (Join-Path $StartMenuDir $_.F) $_.T $_.A $_.D $MiosInstallDir
    }

    $desktopDir = [Environment]::GetFolderPath('Desktop')
    foreach ($legacy in @('MiOS Setup.lnk','Build MiOS.lnk','MiOS Configurator.lnk','MiOS Terminal.lnk','MiOS Dev Shell.lnk','MiOS Podman Shell.lnk','MiOS Build.lnk','MiOS Dashboard.lnk','MiOS Update.lnk','MiOS Pull.lnk','MiOS Agents.lnk','MiOS AI.lnk','MiOS System Monitor.lnk')) {
        foreach ($dir in @($StartMenuDir, $desktopDir)) {
            if (-not $dir) { continue }
            $stale = Join-Path $dir $legacy
            if (Test-Path $stale) {
                try { Remove-Item $stale -Force -ErrorAction SilentlyContinue; Log-Ok "Removed stale shortcut: $stale" } catch {}
            }
        }
    }
    Log-Ok "Add/Remove Programs + Start Menu created (5+ native apps: MiOS, MiOS-DEV, MiOS Config, MiOS Help, Uninstall MiOS, MiOS Enhanced Session*)"

    $B = $BuilderDistro
    @"
param([switch]`$Quiet, [switch]`$Purge)
`$ErrorActionPreference = 'SilentlyContinue'
`$I='$($MiosInstallDir-replace"'","''")'
`$P='$($MiosProgramData-replace"'","''")'
`$D='$($MiosDataDir-replace"'","''")'
`$C='$($MiosConfigDir-replace"'","''")'
`$S='$($StartMenuDir-replace"'","''")'
`$K='$($UninstallRegKey-replace"'","''")'
`$B='$B'
`$M='$MiosWslDistro'
`$BIN='$($MiosBinDir-replace"'","''")'
`$DESK = [Environment]::GetFolderPath('Desktop')
`$WT='$($env:LOCALAPPDATA-replace"'","''")\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
`$WT_PREVIEW='$($env:LOCALAPPDATA-replace"'","''")\Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json'
`$FONTDIR='$($env:LOCALAPPDATA-replace"'","''")\Microsoft\Windows\Fonts'
`$FONTREG='HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'

if (-not `$Quiet) {
    Write-Host ''; Write-Host '  ''MiOS'' Uninstaller' -ForegroundColor Red; Write-Host ''
    Write-Host "  Removes:"
    Write-Host "    - Podman machines (MiOS-DEV, MiOS-BUILDER, podman-MiOS-*) + podman system reset"
    Write-Host "    - WSL distros (MiOS, MiOS-DEV, podman-MiOS-*, MiOS-BUILDER, podman-MiOS-BUILDER)"
    Write-Host "    - Hyper-V VMs matching MiOS-*"
    Write-Host "    - M:\\MiOS install dir + overlay files + ProgramData (`$I, `$P, `$D)"
    Write-Host "    - Start Menu folder + Desktop shortcuts"
    Write-Host "    - WT settings.json: launchMode, profiles.defaults, MiOS scheme + profiles"
    Write-Host "    - PowerShell profile redirector blocks (pwsh 7 + WindowsPowerShell 5.1)"
    Write-Host "    - Geist + Symbols-Only Nerd Font files + registry entries"
    Write-Host "    - HKCU/HKLM Path entries pointing into MiOS bin"
    Write-Host "    - HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MiOS"
    Write-Host "    - podman-machine state symlinks"
    Write-Host "    - MIOS_* environment variables"
    if (`$Purge) {
        Write-Host "    - Per-user config at `$C (PURGE mode)" -ForegroundColor Yellow
    } else {
        Write-Host "  Preserves: `$C (per-user config -- pass -Purge to also remove)"
    }
    Write-Host ''
    if ((Read-Host "  Type 'yes' to confirm") -ne 'yes') { Write-Host '  Aborted.'; exit 0 }
}

# 1. Podman machine (every variant + global system reset)
Write-Host '  [1/13] Stopping + removing podman machines...' -ForegroundColor Cyan
foreach (`$mch in @(`$B, 'MiOS-DEV','MiOS-BUILDER','podman-MiOS-DEV','podman-MiOS-BUILDER')) {
    if ([string]::IsNullOrWhiteSpace(`$mch)) { continue }
    try { & podman machine stop `$mch 2>`$null } catch {}
    try { & podman machine rm -f `$mch 2>`$null } catch {}
}
try { & podman system reset --force 2>`$null } catch {}

# 2. WSL distros (every variant the install has used across revisions)
Write-Host '  [2/13] Unregistering WSL distros...' -ForegroundColor Cyan
foreach (`$d in @(`$B, `$M, 'MiOS', 'MiOS-DEV', 'podman-MiOS-DEV', 'MiOS-BUILDER', 'podman-MiOS-BUILDER')) {
    if ([string]::IsNullOrWhiteSpace(`$d)) { continue }
    try { & wsl.exe --unregister `$d 2>`$null | Out-Null } catch {}
}
try { & wsl.exe --shutdown 2>`$null | Out-Null } catch {}

# 2b. Hyper-V VMs matching MiOS-* (per feedback_mios_entry_full_reset memory)
Write-Host '  [3/13] Removing Hyper-V VMs (MiOS-*)...' -ForegroundColor Cyan
try {
    if (Get-Command Get-VM -ErrorAction SilentlyContinue) {
        Get-VM -Name 'MiOS-*' -ErrorAction SilentlyContinue | ForEach-Object {
            try { Stop-VM -Name `$_.Name -TurnOff -Force -ErrorAction SilentlyContinue } catch {}
            try { Remove-VM -Name `$_.Name -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
} catch {}

# 3. Install dirs (preserve `$C unless -Purge)
Write-Host '  [4/13] Removing install dirs (M:\\MiOS + overlay)...' -ForegroundColor Cyan
`$dirsToRemove = @(`$I, `$P, `$D, `$S)
if (`$Purge) { `$dirsToRemove += `$C }
foreach (`$p in `$dirsToRemove) {
    if ([string]::IsNullOrWhiteSpace(`$p)) { continue }
    if (Test-Path -LiteralPath `$p) { Remove-Item -LiteralPath `$p -Recurse -Force -ErrorAction SilentlyContinue }
}
# M:\ root overlay files (only the ones MiOS overlaid; never wipe drive root structure)
if (Test-Path -LiteralPath 'M:\') {
    foreach (`$mRoot in @('M:\etc','M:\usr','M:\var','M:\automation','M:\config','M:\tools','M:\v1','M:\winget','M:\.devcontainer','M:\.forgejo','M:\.github','M:\.git','M:\Get-MiOS.ps1','M:\install.ps1','M:\bootstrap.ps1','M:\bootstrap.sh','M:\build-mios.ps1','M:\build-mios.sh','M:\install.sh','M:\install-mios-agents.sh','M:\seed-merge.ps1','M:\seed-merge.sh','M:\push-to-github.ps1','M:\preflight.ps1','M:\mios-pipeline.ps1','M:\mios-build-local.ps1','M:\Justfile','M:\Containerfile','M:\Containerfile.minimal','M:\manifest.json','M:\image-versions.yml','M:\renovate.json','M:\system-prompt.md','M:\identity.env.example','M:\mios.toml','M:\AGENTS.md','M:\AGREEMENTS.md','M:\CLAUDE.md','M:\GEMINI.md','M:\CONTRIBUTING.md','M:\SECURITY.md','M:\README.md','M:\LICENSE','M:\VERSION','M:\MiOS-SBOM.csv','M:\llms.txt','M:\llms-full.txt','M:\.clinerules','M:\.cursorrules','M:\.editorconfig','M:\.env.mios','M:\.gitattributes','M:\.gitignore','M:\podman')) {
        if (Test-Path -LiteralPath `$mRoot) { Remove-Item -LiteralPath `$mRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

# 4. WT settings.json -- remove only MiOS-set keys, preserve everything else
Write-Host '  [5/13] Cleaning Windows Terminal settings.json...' -ForegroundColor Cyan
foreach (`$wtPath in @(`$WT, `$WT_PREVIEW)) {
    if (-not (Test-Path -LiteralPath `$wtPath)) { continue }
    try {
        `$raw = Get-Content -LiteralPath `$wtPath -Raw
        `$stripped = [regex]::Replace(`$raw, '(?ms)/\*.*?\*/', '')
        `$stripped = [regex]::Replace(`$stripped, '(?m)^\s*//.*$', '')
        `$stripped = [regex]::Replace(`$stripped, ',(\s*[\}\]])', '`$1')
        `$j = `$stripped | ConvertFrom-Json -ErrorAction Stop
        `$changed = `$false
        # Root-level launchMode (only remove if = 'focus' or 'maximizedFocus' -- our values)
        if (`$j.PSObject.Properties['launchMode'] -and `$j.launchMode -in @('focus','maximizedFocus','focusFullscreen')) {
            `$j.PSObject.Properties.Remove('launchMode'); `$changed = `$true
        }
        # profiles.defaults: only the keys MiOS writes
        if (`$j.profiles -and `$j.profiles.defaults) {
            foreach (`$k in @('scrollbarState','padding','useAcrylic','opacity','unfocusedAppearance','systemBackdrop','suppressApplicationTitle','disableAnimations','useAtlasEngine','experimental.detectURLs','experimental.input.forceVT','experimental.rendering.forceFullRepaint')) {
                if (`$j.profiles.defaults.PSObject.Properties[`$k]) {
                    `$j.profiles.defaults.PSObject.Properties.Remove(`$k); `$changed = `$true
                }
            }
        }
        # MiOS scheme
        if (`$j.schemes) {
            `$keepSchemes = @(`$j.schemes | Where-Object { `$_.name -ne 'MiOS' })
            if (`$keepSchemes.Count -ne `$j.schemes.Count) { `$j.schemes = [object[]]`$keepSchemes; `$changed = `$true }
        }
        # MiOS / MiOS-WIN / MiOS-DEV / podman-MiOS-* profiles
        if (`$j.profiles -and `$j.profiles.list) {
            `$keepProfiles = @(`$j.profiles.list | Where-Object {
                `$_.name -ne 'MiOS' -and `$_.name -ne 'MiOS-WIN' -and `$_.name -ne 'MiOS-DEV' -and `$_.name -ne 'MiOS-Bootstrap' -and `$_.name -notmatch '^podman-MiOS-' -and `$_.guid -ne '{a8b5c2d3-e4f5-6789-abcd-ef0123456789}' -and `$_.guid -ne '{a8b5c2d3-e4f5-6789-abcd-ef0123456790}'
            })
            if (`$keepProfiles.Count -ne `$j.profiles.list.Count) { `$j.profiles.list = [object[]]`$keepProfiles; `$changed = `$true }
        }
        if (`$changed) {
            (`$j | ConvertTo-Json -Depth 32) | Set-Content -LiteralPath `$wtPath -Encoding UTF8
        }
    } catch {}
}

# 5. PowerShell profile redirector blocks (both pwsh 7 + WindowsPowerShell 5.1)
Write-Host '  [6/13] Removing PowerShell profile redirector blocks...' -ForegroundColor Cyan
function Remove-MarkerBlock {
    param([string]`$Text, [string]`$StartMarker, [string]`$EndMarker)
    while (`$true) {
        `$si = `$Text.IndexOf(`$StartMarker)
        if (`$si -lt 0) { return `$Text }
        `$ei = `$Text.IndexOf(`$EndMarker, `$si)
        if (`$ei -lt 0) { return `$Text }
        `$endPos = `$ei + `$EndMarker.Length
        # Trim a trailing newline if present so the removal is clean.
        if (`$endPos -lt `$Text.Length -and `$Text[`$endPos] -eq "``r") { `$endPos++ }
        if (`$endPos -lt `$Text.Length -and `$Text[`$endPos] -eq "``n") { `$endPos++ }
        `$Text = `$Text.Substring(0, `$si) + `$Text.Substring(`$endPos)
    }
}
`$pwshProfileCandidates = @(
    `$PROFILE.CurrentUserAllHosts,
    `$PROFILE.CurrentUserCurrentHost,
    (Join-Path `$env:USERPROFILE 'Documents\PowerShell\profile.ps1'),
    (Join-Path `$env:USERPROFILE 'Documents\PowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path `$env:USERPROFILE 'Documents\WindowsPowerShell\profile.ps1'),
    (Join-Path `$env:USERPROFILE 'Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path `$env:USERPROFILE 'OneDrive\Documents\PowerShell\profile.ps1'),
    (Join-Path `$env:USERPROFILE 'OneDrive\Documents\PowerShell\Microsoft.PowerShell_profile.ps1'),
    (Join-Path `$env:USERPROFILE 'OneDrive\Documents\WindowsPowerShell\profile.ps1'),
    (Join-Path `$env:USERPROFILE 'OneDrive\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1')
) | Where-Object { `$_ } | Sort-Object -Unique
foreach (`$pp in `$pwshProfileCandidates) {
    if (-not (Test-Path -LiteralPath `$pp)) { continue }
    try {
        `$body = Get-Content -LiteralPath `$pp -Raw
        `$body = Remove-MarkerBlock -Text `$body -StartMarker '# >>> MiOS oh-my-posh init >>>' -EndMarker '# <<< MiOS oh-my-posh init <<<'
        `$body = Remove-MarkerBlock -Text `$body -StartMarker '# >>> MiOS dash function >>>' -EndMarker '# <<< MiOS dash function <<<'
        `$body = `$body.Trim()
        if ([string]::IsNullOrWhiteSpace(`$body)) {
            Remove-Item -LiteralPath `$pp -Force -ErrorAction SilentlyContinue
        } else {
            Set-Content -LiteralPath `$pp -Value `$body -Encoding UTF8 -NoNewline
        }
    } catch {}
}

# 6. Fonts (Geist + Symbols-Only Nerd Font)
Write-Host '  [7/13] Removing MiOS fonts...' -ForegroundColor Cyan
if (Test-Path -LiteralPath `$FONTDIR) {
    Get-ChildItem -LiteralPath `$FONTDIR -File -ErrorAction SilentlyContinue |
        Where-Object { `$_.Name -match '^(Geist|.*NerdFontMono|.*NerdFontPropo|.*NerdFont|SymbolsOnly|.*Symbols.*)' } |
        ForEach-Object {
            `$fname = `$_.Name
            try { Remove-Item -LiteralPath `$_.FullName -Force -ErrorAction SilentlyContinue } catch {}
            # Matching reg entries (TrueType / OpenType suffixes)
            if (Test-Path -LiteralPath `$FONTREG) {
                `$face = [System.IO.Path]::GetFileNameWithoutExtension(`$fname)
                foreach (`$suffix in @(' (TrueType)',' (OpenType)')) {
                    `$regName = "`$face`$suffix"
                    try { Remove-ItemProperty -LiteralPath `$FONTREG -Name `$regName -ErrorAction SilentlyContinue } catch {}
                }
            }
        }
}

# 7. PATH env (HKCU + HKLM if admin)
Write-Host '  [8/13] Removing PATH env entries...' -ForegroundColor Cyan
foreach (`$scope in @('User','Machine')) {
    try {
        `$cur = [Environment]::GetEnvironmentVariable('Path', `$scope)
        if (-not `$cur) { continue }
        `$parts = `$cur -split ';' | Where-Object { `$_ -and `$_ -notmatch '[Mm]:\\\\?MiOS\\\\bin' -and `$_ -notmatch [regex]::Escape(`$BIN) }
        `$new = (`$parts -join ';')
        if (`$new -ne `$cur) {
            [Environment]::SetEnvironmentVariable('Path', `$new, `$scope)
        }
    } catch {}
}

# 8. HKCU uninstall reg key
Write-Host '  [9/13] Removing HKCU uninstall reg key...' -ForegroundColor Cyan
if (Test-Path -LiteralPath `$K) { Remove-Item -LiteralPath `$K -Recurse -Force -ErrorAction SilentlyContinue }

# 9. Start Menu folder + Desktop .lnk shortcuts
Write-Host '  [10/13] Removing Start Menu + Desktop shortcuts...' -ForegroundColor Cyan
`$lnkNames = @(
    'MiOS.lnk','MiOS-DEV.lnk','MiOS Config.lnk','MiOS Help.lnk','Uninstall MiOS.lnk','MiOS Enhanced Session.lnk',
    # Legacy names from prior install revisions
    'MiOS Setup.lnk','Build MiOS.lnk','MiOS Configurator.lnk','MiOS Terminal.lnk',
    'MiOS Dev Shell.lnk','MiOS Podman Shell.lnk','MiOS Build.lnk','MiOS Dashboard.lnk',
    'MiOS Update.lnk','MiOS Pull.lnk','MiOS Agents.lnk','MiOS AI.lnk','MiOS System Monitor.lnk'
)
`$shortcutDirs = @(`$DESK, `$S,
    'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\MiOS',
    (Join-Path `$env:APPDATA 'Microsoft\Windows\Start Menu\Programs\MiOS'),
    (Join-Path `$env:USERPROFILE 'OneDrive\Desktop')
) | Where-Object { `$_ -and (Test-Path -LiteralPath `$_) } | Sort-Object -Unique
foreach (`$dir in `$shortcutDirs) {
    foreach (`$ln in `$lnkNames) {
        `$lp = Join-Path `$dir `$ln
        if (Test-Path -LiteralPath `$lp) {
            try { Remove-Item -LiteralPath `$lp -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    if (`$dir -match 'Start Menu\\Programs\\MiOS$') {
        `$linuxAppsSub = Join-Path `$dir 'Linux Apps'
        if (Test-Path -LiteralPath `$linuxAppsSub) {
            try { Remove-Item -LiteralPath `$linuxAppsSub -Recurse -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    # If dir is the MiOS Start Menu folder and now empty, remove it
    if (`$dir -match 'Start Menu\\Programs\\MiOS$') {
        if ((Get-ChildItem -LiteralPath `$dir -Force -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
            try { Remove-Item -LiteralPath `$dir -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

# 10. AppUserModelID HKCU registrations
Write-Host '  [11/13] Removing AppUserModelID registrations...' -ForegroundColor Cyan
foreach (`$aumKey in @('HKCU:\Software\Classes\AppUserModelId\MiOS.Workstation',
                       'HKLM:\Software\Classes\AppUserModelId\MiOS.Workstation')) {
    if (Test-Path -LiteralPath `$aumKey) {
        try { Remove-Item -LiteralPath `$aumKey -Recurse -Force -ErrorAction SilentlyContinue } catch {}
    }
}

# 11. podman-machine state symlinks
Write-Host '  [12/13] Removing podman-machine state symlinks...' -ForegroundColor Cyan
foreach (`$pmLink in @(
    (Join-Path `$env:LOCALAPPDATA 'containers\podman\machine'),
    (Join-Path `$env:USERPROFILE  '.local\share\containers\podman\machine'),
    'C:\ProgramData\containers\podman\machine'
)) {
    if (Test-Path -LiteralPath `$pmLink) {
        try {
            `$item = Get-Item -LiteralPath `$pmLink -Force -ErrorAction SilentlyContinue
            if (`$item.LinkType -eq 'SymbolicLink' -or `$item.LinkType -eq 'Junction' -or `$item.Target) {
                Remove-Item -LiteralPath `$pmLink -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
}

# 12. MIOS_* + BTOP_CONFIG_DIR environment variables
Write-Host '  [13/17] Removing MIOS_* + BTOP_CONFIG_DIR environment variables...' -ForegroundColor Cyan
foreach (`$scope in @('User','Machine')) {
    try {
        `$envKey = if (`$scope -eq 'User') { 'HKCU:\Environment' }
                   else { 'HKLM:\System\CurrentControlSet\Control\Session Manager\Environment' }
        if (Test-Path -LiteralPath `$envKey) {
            (Get-Item -LiteralPath `$envKey).Property | Where-Object { `$_ -match '^(MIOS_|MiOS_|BTOP_CONFIG_DIR$)' } |
                ForEach-Object { try { Remove-ItemProperty -LiteralPath `$envKey -Name `$_ -ErrorAction SilentlyContinue } catch {} }
        }
    } catch {}
}

# 13. HKCU\Run autostart entries (MiOS-GuiWatch background daemon) + scheduled tasks
Write-Host '  [14/17] Removing HKCU\Run autostart entries + scheduled tasks...' -ForegroundColor Cyan
foreach (`$runVal in @('MiOS-GuiWatch','MiOS','MiOSGuiWatch','MiOS-Autostart')) {
    try { Remove-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name `$runVal -ErrorAction SilentlyContinue } catch {}
}
# Kill any running mios-gui-watch.ps1 pwsh process (it auto-resizes WSLg
# windows; without this it'd survive uninstall and keep polling).
try {
    Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { `$_.CommandLine -match 'mios-gui-watch' } |
        ForEach-Object { try { Stop-Process -Id `$_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
} catch {}
try {
    if (Get-Command Unregister-ScheduledTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName 'MiOS-Autostart' -Confirm:`$false -ErrorAction SilentlyContinue
    }
} catch {}
try {
    `$stagedAutostart = Join-Path `$env:ProgramData 'MiOS\mios-autostart.ps1'
    if (Test-Path `$stagedAutostart) {
        Remove-Item -Path `$stagedAutostart -Force -ErrorAction SilentlyContinue
    }
} catch {}

# 14. Windows Defender exclusions (added by Add-MiosDefenderExclusions)
Write-Host '  [15/17] Removing Windows Defender exclusions...' -ForegroundColor Cyan
try {
    if (Get-Command Remove-MpPreference -ErrorAction SilentlyContinue) {
        foreach (`$excPath in @('M:\','M:\MiOS','M:\MiOS\bin','M:\MiOS\repo',(Join-Path `$env:LOCALAPPDATA 'Microsoft\WinGet'),`$env:TEMP)) {
            try { Remove-MpPreference -ExclusionPath `$excPath -ErrorAction SilentlyContinue } catch {}
        }
        foreach (`$excProc in @('pwsh.exe','wsl.exe','wslservice.exe','podman.exe','msrdc.exe')) {
            try { Remove-MpPreference -ExclusionProcess `$excProc -ErrorAction SilentlyContinue } catch {}
        }
    }
} catch {}

# 15. /etc/skel and Add/Remove Programs final cleanup (any stragglers)
# (covered by step 4 + 9; explicit re-pass here in case a partial install left both states)
Write-Host '  [16/17] Final HKCU\Uninstall\MiOS sweep + stale icon dir...' -ForegroundColor Cyan
try { Remove-Item -LiteralPath `$K -Recurse -Force -ErrorAction SilentlyContinue } catch {}
try { Remove-Item -LiteralPath (Join-Path `$I 'icons') -Recurse -Force -ErrorAction SilentlyContinue } catch {}

Write-Host '  [17/17] Reformatting M:\ partition (MIOS-DEV label)...' -ForegroundColor Cyan
try {
    `$mVol = Get-Volume -DriveLetter M -ErrorAction SilentlyContinue
    if (`$mVol -and `$mVol.FileSystemLabel -match '^MIOS') {
        # Stop any process holding handles into M:\ first
        try {
            Get-Process | Where-Object {
                try { `$_.Path -and `$_.Path -like 'M:\*' } catch { `$false }
            } | ForEach-Object { try { Stop-Process -Id `$_.Id -Force -ErrorAction SilentlyContinue } catch {} }
        } catch {}
        if (-not `$Quiet) {
            `$ans = Read-Host "  M: drive will be FULLY FORMATTED (label MIOS-DEV). Type 'format' to confirm"
            if (`$ans -eq 'format') {
                Format-Volume -DriveLetter M -FileSystem NTFS -NewFileSystemLabel 'MIOS-DEV' -Force -Confirm:`$false -ErrorAction Stop | Out-Null
                Write-Host '  [+] M:\ reformatted (NTFS, label MIOS-DEV, empty).' -ForegroundColor Green
            } else {
                Write-Host '  M:\ format SKIPPED (operator did not confirm).' -ForegroundColor Yellow
            }
        } else {
            # -Quiet mode: format without prompt (called from auto-reap)
            Format-Volume -DriveLetter M -FileSystem NTFS -NewFileSystemLabel 'MIOS-DEV' -Force -Confirm:`$false -ErrorAction Stop | Out-Null
            Write-Host '  [+] M:\ reformatted (NTFS, label MIOS-DEV, empty) [-Quiet].' -ForegroundColor Green
        }
    } else {
        Write-Host '  M:\ not present or label != MIOS-DEV; skipping format (safety guard).' -ForegroundColor DarkGray
    }
} catch {
    Write-Host "  [!] M:\ format failed: `$(`$_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ''
if (`$Purge) {
    Write-Host "  'MiOS' fully removed (zero-state). Per-user config at `$C also purged." -ForegroundColor Green
} else {
    Write-Host "  'MiOS' removed. Per-user config at `$C preserved." -ForegroundColor Green
    Write-Host "  Run with -Purge to also remove per-user config." -ForegroundColor DarkGray
}
"@ | Set-Content $uninstSc -Encoding UTF8
    Log-Ok "uninstall.ps1 written (13-category cleanup, mirrors Get-MiOS.ps1 Invoke-MiOSFullReap)"
    End-Phase $script:AppRegPhaseId

    Start-Phase 9
    if ($DeployPipeline) {
        Log-Ok "Skipping Phase 9 container build (-DeployPipeline requested)"
        End-Phase 9
        Invoke-DeployPipeline -HW $HW
    } else {
        $rc = Invoke-WslBuild -Distro $BuilderDistro -BaseImage $HW.BaseImage `
                              -AiModel $MiosAiModel -EmbedModel $MiosAiEmbedModel `
                              -BakeModels $MiosBakeModels `
                              -MiosUser $MiosUser -MiosHostname $MiosHostname
        if ($rc -eq 0) {
            End-Phase 9
            Invoke-DeployPipeline -HW $HW
        } else { End-Phase 9 -Fail; $ExitCode = $rc }
    }

# end full-install branch

} catch {
    $ExitCode = 1   # set FIRST -- must be reached even if Show-Dashboard below also fails
    $errMsg = "$_"
    Write-Log "FATAL: $errMsg" "ERROR"
    $script:CurStep = "FATAL: $($errMsg.Substring(0,[math]::Min($errMsg.Length,120)))"
    if ($script:CurPhase -ge 0 -and $script:CurPhase -lt $script:PhStat.Count -and $script:PhStat[$script:CurPhase] -eq 1) {
        try { End-Phase $script:CurPhase -Fail } catch {}
    }
    Show-Dashboard -Force
} finally {
    # Drain stdout + Install-MiosLauncher's still-flushing log lines
    # before the final summary writes -- avoids the success-box-rows-
    # racing-with-launcher-tails rendering issue.
    try { [Console]::Out.Flush() } catch {}
    Start-Sleep -Milliseconds 500

    $totalTime = fmtSpan ([datetime]::Now - $script:ScriptStart)
    Write-Host ""
    if ($ExitCode -eq 0) {
        if (-not $BootstrapOnly) {
            # Plain-text summary (no box drawing). The previous boxed
            # success summary was racing with Install-MiosLauncher's tail
            # log lines and producing fragmented output. Plain Write-Host
            # lines can't be partially overwritten by stragglers.
            Write-Host "  MiOS bootstrap complete." -ForegroundColor Green
            Write-Host "    Total time:   $totalTime"   -ForegroundColor DarkGray
            Write-Host "    Dev distro:   $BuilderDistro" -ForegroundColor DarkGray
            Write-Host "    Logs:         $MiosLogDir" -ForegroundColor DarkGray
            Write-Host ""
            Write-Host "  Next steps (run in any MiOS terminal):" -ForegroundColor Cyan
            Write-Host "    mios-build    full OCI image build inside MiOS-DEV" -ForegroundColor White
            Write-Host "    mios-config   open mios.toml configurator"          -ForegroundColor White
            Write-Host "    mios-dev      enter the dev distro shell"           -ForegroundColor White
            Write-Host "    mios-help     full command list"                    -ForegroundColor White
        }
    } else {
        Write-Host "  MiOS bootstrap FAILED (exit $ExitCode)" -ForegroundColor Red
        Write-Host "    Errors: $($script:ErrCount)" -ForegroundColor Yellow
        Write-Host "    Log:    $LogFile" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Log "auto-chain gate: ExitCode=$ExitCode Unattended=$Unattended MIOS_NO_AUTO_CHAIN='$($env:MIOS_NO_AUTO_CHAIN)'"
    if ($ExitCode -eq 0 -and -not $Unattended -and -not $env:MIOS_NO_AUTO_CHAIN) {
        Write-Log "auto-chain: gate open; resolving dev distro"
        $devDistro = $null
        try {
            $wslList = (& wsl.exe -l -q 2>$null) -split "`r?`n" |
                       ForEach-Object { ($_ -replace [char]0,'').Trim() } |
                       Where-Object { $_ }
            foreach ($c in @('MiOS-DEV','podman-MiOS-DEV','MiOS-BUILDER','podman-MiOS-BUILDER')) {
                if ($wslList -contains $c) { $devDistro = $c; break }
            }
        } catch {}
        Write-Log "auto-chain: resolved dev distro = '$devDistro'"
        if ($devDistro) {
            $resolvedUser = 'root'
            try {
                $passwd = (& wsl.exe -d $devDistro --user root -- cat /etc/passwd 2>$null) -join "`n"
                if ($passwd -match '(?m)^mios:') { $resolvedUser = 'mios' }
                elseif ($passwd -match '(?m)^core:') { $resolvedUser = 'core' }
            } catch {}
            Write-Log "auto-chain: resolved user = '$resolvedUser'"
            Write-Host "  -> Launching $devDistro (--user $resolvedUser) to run mios-build-driver..." -ForegroundColor Cyan
            Write-Host "     Output streams below; the OCI build runs inside MiOS-DEV." -ForegroundColor DarkGray
            Write-Host ""

            $localDriver = Join-Path $script:MiosRepoDir 'usr\libexec\mios\mios-build-driver'
            Write-Log "auto-chain: localDriver = '$localDriver' exists=$(Test-Path -LiteralPath $localDriver)"
            if (-not (Test-Path -LiteralPath $localDriver)) {
                Write-Log "auto-chain: ABORT -- mios-build-driver not found" "WARN"
                Write-Host "  [!] mios-build-driver not found at $localDriver" -ForegroundColor Yellow
                Write-Host "      Re-run the bootstrap; Phase 2 should have cloned mios.git to M:\." -ForegroundColor DarkGray
            } else {
                # Convert M:\path\to\file -> /mnt/m/path/to/file (WSL automount).
                $wslDriver = '/mnt/' + $localDriver.Substring(0, 1).ToLower() + ($localDriver.Substring(2) -replace '\\','/')
                $automountOk = $false
                try {
                    & wsl.exe -d $devDistro --user root -- test -r $wslDriver 2>$null
                    if ($LASTEXITCODE -eq 0) { $automountOk = $true }
                } catch {}
                Write-Log "auto-chain: wslDriver = '$wslDriver' automountOk=$automountOk"
                if (-not $automountOk) {
                    Write-Log "auto-chain: ABORT -- /mnt/m/ not readable inside $devDistro" "WARN"
                    Write-Host "  [!] /mnt/m/ not readable inside $devDistro (automount disabled?)" -ForegroundColor Yellow
                    Write-Host "      Manually run inside $devDistro :  bash $wslDriver" -ForegroundColor DarkGray
                } else {
                    # Exec the driver. As root, no sudo needed (avoids
                    # PAM/sudoers edge cases inside rootful machine-os).
                    Write-Log "auto-chain: EXEC bash $wslDriver inside $devDistro as $resolvedUser"
                    if ($resolvedUser -eq 'root') {
                        & wsl.exe -d $devDistro --user root -- bash -lc "exec bash $wslDriver"
                    } else {
                        & wsl.exe -d $devDistro --user $resolvedUser -- bash -lc "exec sudo bash $wslDriver"
                    }
                    Write-Log "auto-chain: driver exited with code $LASTEXITCODE"
                }
            }
        } else {
            Write-Log "auto-chain: ABORT -- no dev distro found in WSL list" "WARN"
        }
    } else {
        Write-Log "auto-chain: SKIPPED (gate closed)"
    }
    # Stop the background heartbeat runspace cleanly before exit. There is
    # no transcript to close (the unified log is written directly via
    # [IO.File]::AppendAllText), so dashboard frames never reach the log.
    try {
        $script:DashSync.Running = $false
        [System.Threading.Thread]::Sleep(200)   # let background loop exit its Sleep(120)
        if ($script:BgPs)  { try { $script:BgPs.Stop() }    catch {}; try { $script:BgPs.Dispose() }  catch {} }
        if ($script:BgRs)  { try { $script:BgRs.Close() }   catch {} }
    } catch {}
    # Merge raw build output (BuildDetailLog) into the unified log so a
    # post-mortem reader has a single file with the full picture.
    if (Test-Path $BuildDetailLog) {
        try {
            [System.IO.File]::AppendAllText($LogFile, "`n`n---- BUILD OUTPUT ----`n", [Text.Encoding]::UTF8)
            $detail = [System.IO.File]::ReadAllText($BuildDetailLog, [Text.Encoding]::UTF8)
            [System.IO.File]::AppendAllText($LogFile, $detail, [Text.Encoding]::UTF8)
            Remove-Item $BuildDetailLog -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    # Inject unified log into OCI image at /usr/share/mios/build-log.txt
    if ($ExitCode -eq 0) {
        try {
            $cid = (& podman create localhost/mios:latest 2>$null) -join ""
            if ($LASTEXITCODE -eq 0 -and $cid.Trim()) {
                $cid = $cid.Trim()
                & podman cp $LogFile "${cid}:/usr/share/mios/build-log.txt" 2>$null
                & podman commit --quiet $cid localhost/mios:latest 2>$null | Out-Null
                & podman rm -f $cid 2>$null | Out-Null
            }
        } catch {}
    }
    exit $ExitCode
}
