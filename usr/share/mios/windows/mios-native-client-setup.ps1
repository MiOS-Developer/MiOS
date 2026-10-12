# AI-hint: Native Windows/CMD and MCP entrypoints into the unprivileged MiOS WSL runtime; shared mobile shortcuts and SSOT fonts, preserving other client settings.
# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-terminal, usr/libexec/mios/mios-mcp-server, build-mios.ps1, usr/share/doc/mios/guides/mobile-keybindings.md

[CmdletBinding()]
param(
    [string]$Distro,
    [string]$LinuxUser,
    [string]$BinDirectory = (Join-Path $env:ProgramData 'MiOS\bin'),
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..\..\..\..'),
    [switch]$SkipClients,
    [switch]$SkipAgentInstall,
    [switch]$RuntimeOnly,
    [switch]$RepairRuntime,
    [switch]$EmitConfig
)
$ErrorActionPreference = 'Stop'
if ($RepairRuntime) { $RuntimeOnly = $true }
function Test-MiosGuestDefaultRoute([string[]]$Json) { return @(($Json -join "`n") | ConvertFrom-Json).Count -gt 0 }
function Set-MiosTerminalTransparency([Collections.IDictionary]$Appearance, [Collections.IDictionary]$Theme) {
    foreach ($key in @('opacity','unfocused_opacity')) {
        if (($Theme[$key] -isnot [int] -and $Theme[$key] -isnot [long]) -or $Theme[$key] -lt 0 -or $Theme[$key] -gt 100) { throw "[theme].$key must be an integer from 0 to 100" }
    }
    $Appearance['opacity'] = $Theme['opacity']
    $Appearance['useAcrylic'] = $Theme['acrylic']
    if ($Theme['system_backdrop']) { $Appearance['systemBackdrop'] = $Theme['system_backdrop'] }
    if ($Appearance['unfocusedAppearance'] -isnot [Collections.IDictionary]) { $Appearance['unfocusedAppearance'] = @{} }
    $Appearance['unfocusedAppearance']['opacity'] = $Theme['unfocused_opacity']
    $Appearance['unfocusedAppearance']['useAcrylic'] = $Theme['unfocused_acrylic']
}
function Set-MiosTerminalStartup([Collections.IDictionary]$Terminal, [Collections.IDictionary]$Config) {
    $center = $Config['theme']['terminal']['center_on_launch']
    if ($center -isnot [bool]) { throw '[theme.terminal].center_on_launch must be a boolean' }
    $Terminal['centerOnLaunch'] = $center
    $Terminal['launchMode'] = $Config['theme']['launch_mode']
    $Terminal['initialCols'] = $Config['terminal']['cols']
    $Terminal['initialRows'] = $Config['terminal']['rows']
    if ($center) { $Terminal.Remove('initialPosition') }
}
function Set-MiosNativeShortcut($Shell, [string]$Path, [string]$Target, [string]$Profile, [string]$Directory) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    $link = $Shell.CreateShortcut($Path)
    if ($link.TargetPath -eq $Target -and $link.Arguments -eq $Profile -and $link.WorkingDirectory -eq $Directory -and $link.WindowStyle -eq 1) { return }
    if (Test-Path -LiteralPath $Path) { Copy-Item -LiteralPath $Path -Destination "$Path.mios-backup-$stamp" }
    $link.TargetPath = $Target
    $link.Arguments = $Profile
    $link.WorkingDirectory = $Directory
    $link.WindowStyle = 1
    $link.Description = 'MiOS terminal -- projected from runtime SSOT'
    $link.Save()
}
function Set-MiosUnifiedShortcuts($Shell, [Collections.IDictionary]$Config, [string]$Directory, [string[]]$DesktopRoots, [string[]]$ProgramsRoots) {
    # Only named MiOS entrypoints are retired. WSLg applications and unrelated
    # shortcuts keep their own entries. Backups have no .lnk extension.
    $name = $Config['apps']['hub_shortcut_name'] + '.lnk'
    $canonical = @((Join-Path $DesktopRoots[0] $name), (Join-Path $ProgramsRoots[0] $name))
    $retired = @('MiOS-WIN.lnk', 'MiOS-DEV.lnk', 'MiOS Terminal.lnk', 'MiOS AI.lnk', 'MiOS Agents.lnk', 'MiOS System Monitor.lnk', 'MiOS Help.lnk', 'Uninstall MiOS.lnk', $name)
    foreach ($row in $Config['apps']['shortcuts'].Values) { $retired += ($row['name'] + '.lnk') }
    foreach ($row in $Config['keybindings']['actions']) { $retired += ($row['label'] + '.lnk') }
    $folders = @($DesktopRoots) + @($ProgramsRoots)
    foreach ($root in $ProgramsRoots) { $folders += Join-Path $root $Config['apps']['start_menu_folder'] }
    foreach ($folder in ($folders | Select-Object -Unique)) {
        foreach ($entry in ($retired | Select-Object -Unique)) {
            $path = Join-Path $folder $entry
            if ($path -in $canonical -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
            $link = $Shell.CreateShortcut($path)
            if ($link.TargetPath -notmatch '(?i)mios[^\\/]*\.(exe|ps1)$' -and $link.Arguments -notmatch '(?i)mios') { continue }
            Copy-Item -LiteralPath $path -Destination "$path.mios-backup-$stamp" -Force
            Remove-Item -LiteralPath $path -Force
        }
    }
    foreach ($path in $canonical) {
        Set-MiosNativeShortcut $Shell $path (Join-Path $Directory 'mios-launch.exe') $Config['theme']['terminal']['hub_target_profile'] $Directory
    }
}
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'MiOS native client setup requires PowerShell 7' }
$installedBinding = Join-Path $BinDirectory 'native-binding.json'
if ($RuntimeOnly -and (Test-Path -LiteralPath $installedBinding)) {
    $binding = Get-Content -Raw -LiteralPath $installedBinding | ConvertFrom-Json
    if (-not $Distro) { $Distro = $binding.distro }
    if (-not $LinuxUser) { $LinuxUser = $binding.linuxUser }
}
if (-not $RuntimeOnly) { $SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path }
if (-not $Distro) {
    $Distro = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
        ForEach-Object { (Get-ItemProperty $_.PSPath).DistributionName } |
        Where-Object { $_ -match 'MiOS' } | Select-Object -First 1
}
if (-not $Distro) { throw 'No MiOS WSL distribution found; pass -Distro explicitly' }
$registered = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object { (Get-ItemProperty $_.PSPath).DistributionName })
if ($Distro -notin $registered) {
    if ("podman-$Distro" -in $registered) { $Distro = "podman-$Distro" }
    elseif ($Distro.StartsWith('podman-') -and $Distro.Substring(7) -in $registered) { $Distro = $Distro.Substring(7) }
    else { throw "MiOS image '$Distro' is not registered in WSL" }
}
# Windows owns mirrored NIC addresses. NetworkManager in a long-lived WSL
# guest can clear them when a new host adapter appears (for example tethering).
# Reconcile only the active host adapter, only when the guest has no default route.
$wslConfig = Join-Path $env:USERPROFILE '.wslconfig'
if ((Test-Path -LiteralPath $wslConfig) -and (Get-Content -Raw -LiteralPath $wslConfig) -match '(?im)^networkingMode\s*=\s*mirrored\s*$') {
    $guestRoute = & wsl.exe -d $Distro -u root -- ip -j route show default
    if ($LASTEXITCODE -eq 0 -and -not (Test-MiosGuestDefaultRoute $guestRoute)) {
        $hostRoute = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1
        if ($hostRoute) {
            $hostAdapter = Get-NetAdapter -InterfaceIndex $hostRoute.InterfaceIndex
            $hostAddress = Get-NetIPAddress -InterfaceIndex $hostRoute.InterfaceIndex -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
            $guestLinks = (& wsl.exe -d $Distro -u root -- ip -j link show) | ConvertFrom-Json
            $guestAdapter = $guestLinks | Where-Object { $_.address -eq $hostAdapter.MacAddress.Replace('-', ':').ToLowerInvariant() } | Select-Object -First 1
            if ($guestAdapter -and $hostAddress) {
                & wsl.exe -d $Distro -u root -- nmcli device set $guestAdapter.ifname managed no | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Could not release the mirrored adapter from NetworkManager' }
                & wsl.exe -d $Distro -u root -- ip addr replace "$($hostAddress.IPAddress)/$($hostAddress.PrefixLength)" dev $guestAdapter.ifname
                if ($LASTEXITCODE -ne 0) { throw 'Could not reconcile the mirrored host address' }
                & wsl.exe -d $Distro -u root -- ip route replace default via $hostRoute.NextHop dev $guestAdapter.ifname
                if ($LASTEXITCODE -ne 0) { throw 'Could not reconcile the mirrored host route' }
            }
        }
    }
}
if (-not $LinuxUser) {
    $LinuxUser = (& wsl.exe -d $Distro -u root -- python3 -c 'import pwd; print(pwd.getpwuid(1000).pw_name)').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $LinuxUser) { throw 'No UID 1000 MiOS user; pass an unprivileged -LinuxUser explicitly' }
}
$resolve = 'import sys,json; sys.path.insert(0,"/usr/lib/mios"); import mios_toml; d=mios_toml.load_merged(); print(json.dumps({"apps":d["apps"],"font":d["theme"]["font"],"theme":d["theme"],"terminal":d["terminal"],"colors":mios_toml.colors(d),"keybindings":d["keybindings"],"mcp":d["mcp"],"agent_cli":d["agent_cli"],"ports":d["ports"],"os_control":d["os_control"],"nativeWindows":d["build"]["native"]["windows"],"nativeToolchain":d["build"]["toolchain"],"windowsCrossPackages":d["packages"]["windows-cross-build"]["pkgs"],"clinkPackage":d["bootstrap"]["prereqs"]["clink_pkg"],"runtimePackages":d["packages"]["mcp"]["pkgs"],"runtimeProbes":d["packages"]["mcp"]["verify_probes"]}))'
$configJson = & wsl.exe -d $Distro -u $LinuxUser -- python3 -c $resolve
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve native MiOS theme SSOT' }
$config = $configJson | ConvertFrom-Json -AsHashtable
if (-not $config['runtimePackages'].Count -or -not $config['runtimeProbes'].Count) { throw 'SSOT [packages.mcp] runtime packages/probes must not be empty' }
$runtimeProbe = 'import shutil,sys; missing=[c for c in sys.argv[1:] if not shutil.which(c)]; print("\n".join(missing)); sys.exit(bool(missing))'
$missingRuntime = @(& wsl.exe -d $Distro -u $LinuxUser -- python3 -c $runtimeProbe @($config['runtimeProbes']))
if ($LASTEXITCODE -ne 0) {
    if ($RuntimeOnly -and -not $RepairRuntime) { throw "Missing SSOT runtime commands: $($missingRuntime -join ', '). Run mios repair to install the declared package set." }
    foreach ($package in $config['runtimePackages']) { if ($package -notmatch '^[A-Za-z0-9][A-Za-z0-9+_.-]*$') { throw 'Invalid SSOT runtime package name' } }
    & wsl.exe -d $Distro -u root -- dnf -y install @($config['runtimePackages'])
    if ($LASTEXITCODE -ne 0) { throw 'SSOT runtime package installation failed' }
    & wsl.exe -d $Distro -u $LinuxUser -- python3 -c $runtimeProbe @($config['runtimeProbes'])
    if ($LASTEXITCODE -ne 0) { throw 'SSOT runtime commands still missing after package installation' }
}
$runtimeUid = (& wsl.exe -d $Distro -u $LinuxUser -- id -u).Trim()
$runtimeGid = (& wsl.exe -d $Distro -u $LinuxUser -- id -g).Trim()
if ($runtimeUid -notmatch '^[1-9][0-9]*$' -or $runtimeGid -notmatch '^[0-9]+$') { throw 'Could not resolve unprivileged terminal identity' }
& wsl.exe -d $Distro -u $LinuxUser -- /usr/bin/miosd terminal-runtime-check --root / | Out-Null
if ($LASTEXITCODE -ne 0) {
    if ($RuntimeOnly -and -not $RepairRuntime) { throw 'Unsafe MiOS terminal runtime; run mios repair. Existing sessions preserved.' }
    & wsl.exe -d $Distro -u root -- /usr/bin/miosd terminal-runtime-check --root / --uid $runtimeUid --gid $runtimeGid --repair
    if ($LASTEXITCODE -ne 0) { throw 'MiOS terminal runtime repair rejected; existing sessions preserved' }
}
if ($config['terminal']['start_directory'] -isnot [string] -or -not $config['terminal']['start_directory'].StartsWith('/')) { throw '[terminal].start_directory must be an absolute MiOS path' }
$mcpPython = $config['mcp']['python']
$check = & wsl.exe -d $Distro -u $LinuxUser -- $mcpPython -c 'import os; from mcp import Client; assert os.getuid()!=0; assert os.access("/usr/libexec/mios/tmux-mcp",os.X_OK); print("native-ready")'
if ($LASTEXITCODE -ne 0 -or $check -notcontains 'native-ready') { throw 'Install native MiOS-MCP in this WSL distribution first' }
foreach ($value in $config['colors'].Values) {
    if ($value -notmatch '^#[0-9a-fA-F]{6}$') { throw 'Invalid SSOT terminal color; existing projections preserved' }
}
$renderPrompt = 'import sys,json;sys.path.insert(0,"/usr/lib/mios");sys.path.insert(0,"/usr/libexec/mios/ux");import mios_toml,theme_sync;d=mios_toml.load_merged();print(json.dumps({"local":theme_sync.render_prompt(d),"remote":theme_sync.render_prompt(d,remote=True)}))'
$promptBundle = ((& wsl.exe -d $Distro -u $LinuxUser -- python3 -c $renderPrompt) -join "`n") | ConvertFrom-Json
$promptJson = $promptBundle.local
if ($LASTEXITCODE -ne 0) { throw 'Could not project the native Oh My Posh theme' }
$null = $promptJson | ConvertFrom-Json
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$changed = [Collections.Generic.List[string]]::new()
function Write-MiosFile([string]$Path, [string]$Text) {
    if ((Test-Path -LiteralPath $Path) -and [IO.File]::ReadAllText($Path) -ceq $Text) { return }
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    if (Test-Path -LiteralPath $Path) { Copy-Item -LiteralPath $Path -Destination "$Path.mios-backup-$stamp" }
    $pending = "$Path.mios-pending-$([guid]::NewGuid().ToString('N'))"
    [IO.File]::WriteAllText($pending, $Text, [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($pending, $Path, $true)
    $changed.Add($Path)
}
function Read-MiosJson([string]$Path) {
    if (Test-Path -LiteralPath $Path) { return (Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -AsHashtable) }
    return @{}
}
function Save-MiosJson([string]$Path, $Value) {
    Write-MiosFile $Path (((Convert-MiosOrdered $Value) | ConvertTo-Json -Depth 100) + "`n")
}
function Convert-MiosOrdered($Value) {
    if ($Value -is [Collections.IDictionary]) {
        $ordered = [ordered]@{}
        foreach ($key in @($Value.Keys | Sort-Object -CaseSensitive)) { $ordered[$key] = Convert-MiosOrdered $Value[$key] }
        return $ordered
    }
    if ($Value -is [Collections.IList]) { return ,@($Value | ForEach-Object { Convert-MiosOrdered $_ }) }
    return $Value
}
function Set-MiosNativeProfile([string]$Path, [string]$Directory) {
    $text = if (Test-Path -LiteralPath $Path) { [IO.File]::ReadAllText($Path) } else { '' }
    $original = $text
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw "Refusing to migrate invalid PowerShell profile: $Path" }
    $functions = @($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Show-MiosDashboard'}, $true))
    foreach ($function in ($functions | Sort-Object { $_.Extent.StartOffset } -Descending)) {
        if ($function.Extent.Text -notmatch '_dashTomlText|_ServiceCell|_Frame') { continue }
        $replacement = @'
function Show-MiosDashboard {
    param([string]$ConfigPath, [string]$LogoPath)
    . (Join-Path '__BIN__' 'mios-native-shell.ps1') -BinDirectory '__BIN__'
    Invoke-MiosNativeDashboard
}
'@
        $replacement = $replacement.Replace('__BIN__', $Directory.Replace("'", "''"))
        $text = $text.Remove($function.Extent.StartOffset, $function.Extent.EndOffset - $function.Extent.StartOffset).Insert($function.Extent.StartOffset, $replacement)
    }
    $text = [regex]::Replace($text, '(?ms)^# >>> MiOS native SSOT runtime >>>.*?^# <<< MiOS native SSOT runtime <<<\r?\n?', '').TrimEnd()
    $hook = @'
# >>> MiOS native SSOT runtime >>>
$_miosNativeBin = '__BIN__'
. (Join-Path $_miosNativeBin 'mios-native-shell.ps1') -BinDirectory $_miosNativeBin
if (-not $env:MIOS_SKIP_MOTD -and -not $env:MIOS_HOST_TMUX -and -not $env:TMUX -and -not $env:SSH_CONNECTION -and -not [Console]::IsInputRedirected) {
    & (Join-Path $_miosNativeBin 'mios-launch.exe') --host-terminal
    if ($LASTEXITCODE -ne 0) { throw "Native MiOS host terminal failed (exit $LASTEXITCODE)" }
    return
}
# <<< MiOS native SSOT runtime <<<
'@
    $hook = $hook.Replace('__BIN__', $Directory.Replace("'", "''"))
    # Only the known generated profile may be prefixed; arbitrary operator
    # profiles can have param/using/#requires statements that must stay first.
    $next = if ($text -match '\$Global:MiosProfileLoaded') {
        $ast = [Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count -or -not $ast.EndBlock.Statements.Count) { throw "Generated profile has no valid startup boundary: $Path" }
        # Keep using/param/#requires and operator comments before executable
        # startup. They remain valid even when added to a generated profile.
        $offset = $ast.EndBlock.Statements[0].Extent.StartOffset
        $text.Insert($offset, $hook + "`n") + "`n"
    } else { $text + "`n`n" + $hook + "`n" }
    $ast = [Management.Automation.Language.Parser]::ParseInput($next, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw "Native profile migration produced invalid PowerShell: $Path" }
    if ($next -cne $original) { Write-MiosFile $Path $next }
}
$shellSource = Join-Path $SourceRoot 'usr\share\mios\windows\mios-native-shell.ps1'
if (-not (Test-Path -LiteralPath $shellSource)) { $shellSource = Join-Path $BinDirectory 'mios-native-shell.ps1' }
if (Test-Path -LiteralPath $shellSource) {
    Write-MiosFile (Join-Path $BinDirectory 'mios-native-shell.ps1') ([IO.File]::ReadAllText($shellSource))
}
$legacyHub = if ($binding) { $binding.windowsHub } else { 'M:\MiOS\bin\mios.ps1' }
$aiSource = Join-Path $SourceRoot 'usr\share\mios\windows\mios-ai.ps1'
if (-not (Test-Path -LiteralPath $aiSource)) { $aiSource = Join-Path $BinDirectory 'mios-ai.ps1' }
if (Test-Path -LiteralPath $aiSource) { Write-MiosFile (Join-Path $BinDirectory 'mios-ai.ps1') ([IO.File]::ReadAllText($aiSource)) }
if ($legacyHub -and (Test-Path -LiteralPath $legacyHub) -and (Test-Path -LiteralPath $aiSource)) {
    Write-MiosFile (Join-Path (Split-Path -Parent $legacyHub) 'mios-ai.ps1') ([IO.File]::ReadAllText($aiSource))
}
# Upgrade the owned block in already installed profiles, including RuntimeOnly.
$existingProfile = if (Test-Path -LiteralPath 'M:\MiOS\powershell\profile.ps1') { 'M:\MiOS\powershell\profile.ps1' } else { [string]$PROFILE.CurrentUserAllHosts }
if (Test-Path -LiteralPath $existingProfile) {
    Set-MiosNativeProfile $existingProfile $BinDirectory
    $existingText = [IO.File]::ReadAllText($existingProfile)
    # Retired shell dashboard calls survive in already-installed profile bodies.
    # Keep the body and operator edits, migrating only this MiOS entrypoint.
    $migratedText = $existingText.Replace('bash /usr/libexec/mios/mios-dashboard.sh', 'python3 /usr/libexec/mios/mios-dashboard')
    $migratedText = $migratedText.Replace('-- /usr/libexec/mios/mios-dashboard.sh --mini', '-- python3 /usr/libexec/mios/mios-dashboard --mini --once')
    if ($migratedText -cne $existingText) {
        Write-MiosFile $existingProfile $migratedText
        $existingText = $migratedText
    }
    if ($existingText -match '# >>> MiOS native SSOT runtime >>>' -and $existingText -notmatch 'mios-native-shell.ps1') {
        $shellHook = ". (Join-Path `$_miosNativeBin 'mios-native-shell.ps1') -BinDirectory `$_miosNativeBin"
        Write-MiosFile $existingProfile ($existingText.Replace('# <<< MiOS native SSOT runtime <<<', "$shellHook`n# <<< MiOS native SSOT runtime <<<"))
    }
}
Save-MiosJson (Join-Path $env:LOCALAPPDATA 'MiOS\themes\ssot.json') $config
if ($RuntimeOnly -and $binding) {
    $binding.distro = $Distro
    $binding.linuxUser = $LinuxUser
    $binding | Add-Member -NotePropertyName mcpPython -NotePropertyValue $mcpPython -Force
    $binding | Add-Member -NotePropertyName terminalDirectory -NotePropertyValue $config['terminal']['start_directory'] -Force
    Save-MiosJson (Join-Path $env:LOCALAPPDATA 'MiOS\native-binding.json') ($binding | ConvertTo-Json -Depth 20 | ConvertFrom-Json -AsHashtable)
    Save-MiosJson $installedBinding ($binding | ConvertTo-Json -Depth 20 | ConvertFrom-Json -AsHashtable)
}
foreach ($path in @(
    (Join-Path $env:LOCALAPPDATA 'MiOS\themes\mios.omp.json'),
    'M:\MiOS\themes\mios.omp.json'
)) {
    if ($path -like 'M:*' -and -not (Test-Path -LiteralPath 'M:\MiOS')) { continue }
    Write-MiosFile $path ($promptJson + "`n")
}
Write-MiosFile (Join-Path $env:LOCALAPPDATA 'MiOS\themes\mios-remote.omp.json') ($promptJson + "`n")
# Refresh the cache used by sessions opened before the remote-theme migration.
$legacyRemote = Join-Path $env:LOCALAPPDATA 'MiOS\themes\mios-ascii.omp.json'
if (Test-Path -LiteralPath $legacyRemote) { Write-MiosFile $legacyRemote $promptBundle.remote }

# Persist MiOS palette, font, and VT settings to Windows Console registry targets
# Ensures standard cmd.exe, OpenSSH (ConPTY), and PowerShell sessions default to MiOS SSOT
$consoleTargets = @(
    'HKCU:\Console',
    'HKCU:\Console\%SystemRoot%_System32_cmd.exe',
    'HKCU:\Console\MiOS',
    'HKCU:\Console\%SystemRoot%_System32_WindowsPowerShell_v1.0_powershell.exe',
    'HKCU:\Console\%SystemRoot%_SysWOW64_WindowsPowerShell_v1.0_powershell.exe',
    'Registry::HKEY_USERS\.DEFAULT\Console',
    'Registry::HKEY_USERS\.DEFAULT\Console\%SystemRoot%_System32_cmd.exe'
)
$ansiConsoleKeys = @(
    'ansi_0_black', 'ansi_4_blue', 'ansi_2_green', 'ansi_6_cyan',
    'ansi_1_red', 'ansi_5_magenta', 'ansi_3_yellow', 'ansi_7_white',
    'ansi_8_bright_black', 'ansi_12_bright_blue', 'ansi_10_bright_green', 'ansi_14_bright_cyan',
    'ansi_9_bright_red', 'ansi_13_bright_magenta', 'ansi_11_bright_yellow', 'ansi_15_bright_white'
)
$fontSize = [int]$config['font']['size']
foreach ($cPath in $consoleTargets) {
    if (-not (Test-Path -LiteralPath $cPath)) { New-Item -Path $cPath -Force | Out-Null }
    for ($i = 0; $i -lt $ansiConsoleKeys.Count; $i++) {
        $hex = $config['colors'][$ansiConsoleKeys[$i]].TrimStart('#')
        $dword = ([Convert]::ToInt32($hex.Substring(4, 2), 16) -shl 16) -bor ([Convert]::ToInt32($hex.Substring(2, 2), 16) -shl 8) -bor [Convert]::ToInt32($hex.Substring(0, 2), 16)
        Set-ItemProperty -Path $cPath -Name ('ColorTable{0:D2}' -f $i) -Value $dword -Type DWord
    }
    foreach ($entry in @(@('bg','DefaultBackground'),@('fg','DefaultForeground'),@('cursor','CursorColor'))) {
        $h = $config['colors'][$entry[0]].TrimStart('#')
        $dw = ([Convert]::ToInt32($h.Substring(4, 2), 16) -shl 16) -bor ([Convert]::ToInt32($h.Substring(2, 2), 16) -shl 8) -bor [Convert]::ToInt32($h.Substring(0, 2), 16)
        Set-ItemProperty -Path $cPath -Name $entry[1] -Value $dw -Type DWord
    }
    Set-ItemProperty -Path $cPath -Name 'ScreenColors' -Value 0x07 -Type DWord
    Set-ItemProperty -Path $cPath -Name 'PopupColors' -Value 0xF5 -Type DWord
    Set-ItemProperty -Path $cPath -Name 'VirtualTerminalLevel' -Value 1 -Type DWord
    Set-ItemProperty -Path $cPath -Name 'FaceName' -Value $config['font']['family'] -Type String
    Set-ItemProperty -Path $cPath -Name 'FontFamily' -Value 0x36 -Type DWord
    Set-ItemProperty -Path $cPath -Name 'FontSize' -Value ($fontSize -shl 16) -Type DWord
}
foreach ($cpPath in @('HKCU:\Software\Microsoft\Command Processor', 'HKLM:\Software\Microsoft\Command Processor')) {
    if (Test-Path -LiteralPath $cpPath) { Set-ItemProperty -Path $cpPath -Name 'DefaultColor' -Value 0x07 -Type DWord -ErrorAction SilentlyContinue }
}

# A real .cmd on machine PATH works from cmd.exe, SSH's default CMD shell, and
# scripts, without a PowerShell alias or a Command Processor AutoRun hook.
[IO.Directory]::CreateDirectory($BinDirectory) | Out-Null
$engine = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
if (-not (Test-Path -LiteralPath $engine)) {
    $pwshPackage = Get-AppxPackage Microsoft.PowerShell | Select-Object -First 1
    if ($pwshPackage) { $engine = Join-Path $pwshPackage.InstallLocation 'pwsh.exe' }
}
if (-not (Test-Path -LiteralPath $engine)) { $engine = (Get-Command pwsh.exe -ErrorAction Stop).Source }
$hub = @((Join-Path $BinDirectory 'mios.ps1'), 'M:\MiOS\bin\mios.ps1', 'C:\MiOS\bin\mios.ps1') |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$entry = @'
# AI-hint: CMD-accessible native MiOS dispatcher, retaining the installed Windows verb hub.
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$ErrorActionPreference = 'Stop'
$binding = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'native-binding.json') | ConvertFrom-Json
$verb = if ($Arguments.Count) { $Arguments[0] } else { 'terminal' }
[string[]]$rest = @()
if ($Arguments.Count -gt 1) { $rest = $Arguments[1..($Arguments.Count-1)] }
if ($verb -eq 'repair') { & (Join-Path $PSScriptRoot 'mios-native-client-setup.ps1') -RepairRuntime -BinDirectory $PSScriptRoot; exit 0 }
if ($verb -notin @('mcp','ssh')) { & (Join-Path $PSScriptRoot 'mios-native-client-setup.ps1') -RuntimeOnly -BinDirectory $PSScriptRoot }
$userBinding = Join-Path $env:LOCALAPPDATA 'MiOS\native-binding.json'
if (Test-Path -LiteralPath $userBinding) { $binding = Get-Content -Raw -LiteralPath $userBinding | ConvertFrom-Json }
$remote = @()
$directory = (Get-Location).ProviderPath
# Shortcut processes start in the shim folder. WSL must enter the deployed
# root there; an explicit operator project directory remains the working cwd.
if (-not $directory -or $directory.TrimEnd('\') -eq $PSScriptRoot.TrimEnd('\') -or $directory.TrimEnd('\') -eq (Join-Path $env:WINDIR 'System32')) {
    $directory = $binding.terminalDirectory
}
$remote = @('/usr/bin/env','MIOS_TERMINAL_DIRECTORY=.')
if ($env:SSH_CONNECTION -or $env:SSH_CLIENT -or $env:SSH_TTY) { $remote += 'MIOS_REMOTE_TERMINAL=1' }
switch ($verb) {
    'project' { exit 0 }
    'btop' { & wsl.exe -d $binding.distro -u $binding.linuxUser -- btop @rest; exit $LASTEXITCODE }
    'mini' { . (Join-Path $PSScriptRoot 'mios-native-shell.ps1'); Invoke-MiosNativeDashboard -Arguments $rest; exit $LASTEXITCODE }
    'dash' { . (Join-Path $PSScriptRoot 'mios-native-shell.ps1'); Invoke-MiosNativeDashboard -Arguments $rest; exit $LASTEXITCODE }
    'mon' { & wsl.exe -d $binding.distro -u $binding.linuxUser -- /usr/bin/mios mon @rest; exit $LASTEXITCODE }
    'monitor' { & wsl.exe -d $binding.distro -u $binding.linuxUser -- /usr/bin/mios mon @rest; exit $LASTEXITCODE }
    'terminal' { & wsl.exe -d $binding.distro -u $binding.linuxUser --cd $directory -- @remote /usr/libexec/mios/mios-terminal @rest; exit $LASTEXITCODE }
    'ai-terminal' { & wsl.exe -d $binding.distro -u $binding.linuxUser --cd $directory -- @remote /usr/libexec/mios/mios-ai-terminal @rest; exit $LASTEXITCODE }
    'ai' { & wsl.exe -d $binding.distro -u $binding.linuxUser --cd $directory -- @remote /usr/bin/mios ai @rest; exit $LASTEXITCODE }
    'agent' { & wsl.exe -d $binding.distro -u $binding.linuxUser --cd $directory -- @remote /usr/bin/mios agent @rest; exit $LASTEXITCODE }
    'agents' { & wsl.exe -d $binding.distro -u $binding.linuxUser -- /usr/bin/mios agents @rest; exit $LASTEXITCODE }
    'mcp' { & wsl.exe -d $binding.distro -u $binding.linuxUser -- $binding.mcpPython /usr/libexec/mios/mios-mcp-server @rest; exit $LASTEXITCODE }
    'ssh' {
        if (-not $rest.Count) { Write-Error 'Usage: mios ssh [OpenSSH options] user@host'; exit 64 }
        & ssh.exe -t @rest mios terminal
        exit $LASTEXITCODE
    }
    default {
        if ($binding.windowsHub -and (Test-Path -LiteralPath $binding.windowsHub)) { & $binding.windowsHub @Arguments }
        else { & wsl.exe -d $binding.distro -u $binding.linuxUser -- /usr/bin/mios @Arguments }
        if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }
    }
}
'@
Write-MiosFile (Join-Path $BinDirectory 'mios-native-entry.ps1') ($entry + "`n")
$launcher = @"
@echo off
setlocal DisableDelayedExpansion
"%~dp0mios-launch.exe" --dispatch %*
exit /b %ERRORLEVEL%
"@.Replace("`n","`r`n")
Write-MiosFile (Join-Path $BinDirectory 'mios.cmd') $launcher
$cmdProcessorPath = 'HKCU:\Software\Microsoft\Command Processor'
if (-not (Test-Path -LiteralPath $cmdProcessorPath)) {
    New-Item -Path $cmdProcessorPath -Force | Out-Null
}
Set-ItemProperty -Path $cmdProcessorPath -Name 'DisableUNCCheck' -Value 1 -Type DWord -Force
if (-not $RuntimeOnly) {
Write-MiosFile (Join-Path $BinDirectory 'mios-native-shell.ps1') ([IO.File]::ReadAllText((Join-Path $SourceRoot 'usr\share\mios\windows\mios-native-shell.ps1')))
Write-MiosFile (Join-Path $BinDirectory 'mios-native-client-setup.ps1') ([IO.File]::ReadAllText($PSCommandPath))
foreach ($helper in @('mios-pc-control.ps1','mios-window-foreground.ps1','mios-uia-dump.ps1','mios-oscontrol-server.ps1','run-hidden.vbs')) {
    $srcHelper = Join-Path $SourceRoot "usr\share\mios\windows\$helper"
    if (Test-Path -LiteralPath $srcHelper) {
        Write-MiosFile (Join-Path $BinDirectory $helper) ([IO.File]::ReadAllText($srcHelper))
    }
}
$svcToolSrc = Join-Path $SourceRoot "usr\share\mios\windows\MiosServiceTool.exe"
if (Test-Path -LiteralPath $svcToolSrc) {
    Copy-Item -LiteralPath $svcToolSrc -Destination (Join-Path $BinDirectory 'MiosServiceTool.exe') -Force
}
$windowsBuild = $config['nativeWindows']
$nativeDirectory = Join-Path $SourceRoot "tools\native\target\$($windowsBuild['target'])\release"
# Cargo verifies the source fingerprint even when a prior artifact exists.
& {
    $builderName = $config['theme']['terminal']['dev_profile_name']
    $builder = @("podman-$builderName",$builderName) | Where-Object { $_ -in $registered } | Select-Object -First 1
    if (-not $builder) { throw 'MiOS-DEV is required to build the native Windows catalog' }
    # WSL's argument bridge consumes unquoted backslashes in Windows paths.
    $sourceLinux = (& wsl.exe -d $builder -u root -- wslpath -a -u $SourceRoot.Replace('\','/')) -join ''
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the system source in MiOS-DEV' }
    $crossPackages = @($config['windowsCrossPackages'])
    if (-not $crossPackages.Count) { throw 'SSOT Windows cross-build package closure is empty' }
    foreach ($package in $crossPackages) { if ($package -notmatch '^[A-Za-z0-9][A-Za-z0-9+_.-]*$') { throw 'Invalid SSOT cross-build package name' } }
    & wsl.exe -d $builder -u root -- dnf -y install @crossPackages
    if ($LASTEXITCODE -ne 0) { throw 'SSOT Windows cross-build packages could not be installed' }
    $channel = $config['nativeToolchain']['channel']
    if (-not $channel -or -not $config['nativeToolchain']['components'].Count) { throw 'SSOT toolchain channel/components are required' }
    $toolchainEnv = @('CARGO_HOME=/usr/local/cargo','RUSTUP_HOME=/usr/local/rustup','PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    & wsl.exe -d $builder -u root -- test -x /usr/local/cargo/bin/rustup
    if ($LASTEXITCODE -ne 0) {
        & wsl.exe -d $builder -u root -- env @toolchainEnv rustup-init -y --no-modify-path --profile minimal --default-toolchain $channel
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the SSOT Rust toolchain' }
    }
    & wsl.exe -d $builder -u root -- env @toolchainEnv rustup toolchain install $channel --profile minimal
    if ($LASTEXITCODE -ne 0) { throw 'SSOT Rust channel installation failed' }
    & wsl.exe -d $builder -u root -- env @toolchainEnv rustup component add --toolchain $channel @($config['nativeToolchain']['components'])
    if ($LASTEXITCODE -ne 0) { throw 'SSOT Rust lint/format components are unavailable' }
    & wsl.exe -d $builder -u root -- env @toolchainEnv rustup target add --toolchain $channel $windowsBuild['target']
    if ($LASTEXITCODE -ne 0) { throw 'SSOT Windows Rust target is unavailable' }
    & wsl.exe -d $builder -u root -- env @toolchainEnv "RUSTUP_TOOLCHAIN=$channel" /usr/libexec/mios/miosd native-windows-build --root $sourceLinux --target-dir /var/tmp/mios-native-build
    if ($LASTEXITCODE -ne 0) { throw 'Native Windows catalog lint, build or PE verification failed inside MiOS-DEV' }
    # Run the management binary from staging so replacement never locks its own
    # installed executable. Rust validates every staged/copied member and rolls
    # back replacements if a running application prevents installation.
    $nativeManager = Join-Path $nativeDirectory 'miosd.exe'
    if (-not (Test-Path -LiteralPath $nativeManager -PathType Leaf)) { throw 'Verified Windows management binary was not staged' }
    & $nativeManager native-windows-install --root $SourceRoot --source $nativeDirectory --bin-dir $BinDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Native Windows catalog installation failed; see the preserved transaction receipt' }
    & $nativeManager native-runtime-check --root $SourceRoot --platform windows --bin-dir $BinDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Installed Windows native catalog failed SSOT artifact verification' }
}
Save-MiosJson (Join-Path $BinDirectory 'native-binding.json') @{distro=$Distro; linuxUser=$LinuxUser; windowsHub=$hub; engine=$engine; mcpPython=$mcpPython;terminalDirectory=$config['terminal']['start_directory']}
& (Join-Path $BinDirectory 'mios-oscontrol-server.ps1') -Install
$devEntry = @'
# AI-hint: Resolve the installed MiOS image through its native CMD dispatcher.
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
if (-not $Arguments.Count) { & '__CMD__' terminal; exit $LASTEXITCODE }
$binding = Get-Content -Raw -LiteralPath '__BINDING__' | ConvertFrom-Json
& wsl.exe -d $binding.distro @Arguments
exit $LASTEXITCODE
'@
if ($hub) {
    Write-MiosFile (Join-Path (Split-Path -Parent $hub) 'mios-dev.ps1') ($devEntry.Replace('__CMD__',(Join-Path $BinDirectory 'mios.cmd').Replace("'","''")).Replace('__BINDING__',$installedBinding.Replace("'","''")) + "`n")
}
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
if (-not (($machinePath -split ';') -contains $BinDirectory)) {
    [Environment]::SetEnvironmentVariable('Path', "$machinePath;$BinDirectory", 'Machine')
}
if (-not (($env:Path -split ';') -contains $BinDirectory)) { $env:Path += ";$BinDirectory" }
[Environment]::SetEnvironmentVariable('MIOS_NATIVE_BIN', $BinDirectory, 'Machine')
$env:MIOS_NATIVE_BIN = $BinDirectory
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    Add-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' | Out-Null
}
$clink = @((Join-Path ${env:ProgramFiles(x86)} 'clink\clink_x64.exe'),(Join-Path $env:ProgramFiles 'clink\clink_x64.exe')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $clink) {
    & winget.exe install --id $config['clinkPackage'] --exact --scope machine --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw 'Native MiOS CMD dependency installation failed' }
    $clink = @((Join-Path ${env:ProgramFiles(x86)} 'clink\clink_x64.exe'),(Join-Path $env:ProgramFiles 'clink\clink_x64.exe')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
$clinkDirectory = Split-Path -Parent $clink
$cmdPrompt = @'
-- AI-hint: MiOS CMD startup resolves the native layered SSOT before loading the prompt.
-- Services already running can retain PATH from before MiOS installation.
local bin = __BIN__
os.setenv("PATH", __AGENTPATH__ .. ";" .. bin .. ";" .. (os.getenv("PATH") or ""))
os.setenv("MIOS_NATIVE_BIN", bin)
local autorun = os.getenv("CLINK_NOAUTORUN")
os.setenv("CLINK_NOAUTORUN", "1")
local encoding = io.popen("chcp __CODEPAGE__ >nul")
if encoding then encoding:read("*a"); encoding:close() end
local p = io.popen(__COMMAND__)
if p then p:read("*a"); p:close() end
os.setenv("CLINK_NOAUTORUN", autorun)
if not os.getenv("MIOS_COLORS_APPLIED") then
    io.write(__OSC_COLORS__)
    io.flush()
    os.execute("color 07")
    os.setenv("MIOS_COLORS_APPLIED", "1")
end
-- Native host tmux is distinct from the MiOS guest entered by `mios`.
if not os.getenv("MIOS_HOST_TMUX") and not autorun and not os.getenv("SSH_CONNECTION") and not os.getenv("SSH_CLIENT") and not os.getenv("SSH_TTY") then
    local launcher = 'call "' .. bin .. '\\mios-launch.exe" --host-terminal'
    os.execute(launcher)
end
settings.set("clink.customprompt", __PROMPT__)
'@
$cmdPrompt = $cmdPrompt.Replace('__BIN__', ($BinDirectory | ConvertTo-Json -Compress)).Replace('__COMMAND__',(('call "' + (Join-Path $BinDirectory 'mios.cmd') + '" project') | ConvertTo-Json -Compress)).Replace('__PROMPT__',((Join-Path $clinkDirectory 'themes\mios-ssot.clinkprompt') | ConvertTo-Json -Compress)).Replace('__CODEPAGE__', [string][int]$config['theme']['terminal']['windows_codepage'])
$agentRoot = Join-Path $env:ProgramData $config['agent_cli']['windows_directory']
$agentPaths = @((Join-Path $env:ProgramFiles 'nodejs'),(Join-Path $agentRoot 'npm'),(Join-Path $agentRoot 'native'),(Join-Path $agentRoot 'bin')) -join ';'
$cmdPrompt = $cmdPrompt.Replace('__AGENTPATH__', ($agentPaths | ConvertTo-Json -Compress))
$palette = $config['colors']
$oscColors = [Text.StringBuilder]::new()
[void]$oscColors.Append("\x1b]10;$($palette['fg'])\x07")
[void]$oscColors.Append("\x1b]11;$($palette['bg'])\x07")
[void]$oscColors.Append("\x1b]12;$($palette['cursor'])\x07")
[void]$oscColors.Append("\x1b]17;$($palette['accent'])\x07")
$ansiNames = @('black','red','green','yellow','blue','magenta','cyan','white')
for ($i = 0; $i -lt 8; $i++) {
    $name = $ansiNames[$i]
    [void]$oscColors.Append("\x1b]4;$i;$($palette["ansi_${i}_$name"])\x07")
    [void]$oscColors.Append("\x1b]4;$($i+8);$($palette["ansi_$($i+8)_bright_$name"])\x07")
}
[void]$oscColors.Append("\x1b[0m")
$oscLiteral = '"' + $oscColors.ToString() + '"'
$cmdPrompt = $cmdPrompt.Replace('__OSC_COLORS__', $oscLiteral)
Write-MiosFile (Join-Path $clinkDirectory 'mios-ssot.lua') ($cmdPrompt + "`n")
$cmdTheme = @'
-- AI-hint: Native MiOS prompt loads the caller's runtime SSOT projection.
-- Oh My Posh owns its Lua filters; Clink owns prompt activation.
if not os.getenv("MIOS_COLORS_APPLIED") then
    io.write(__OSC_COLORS__)
    io.flush()
    os.execute("color 07")
    os.setenv("MIOS_COLORS_APPLIED", "1")
end
local theme = os.getenv("LOCALAPPDATA") .. "\\MiOS\\themes\\mios.omp.json"
local p = assert(io.popen('oh-my-posh init cmd --config "' .. theme .. '"'))
local script = p:read("*a")
p:close()
assert(load(script, "MiOS SSOT prompt"))()
return {}
'@
$cmdTheme = $cmdTheme.Replace('__OSC_COLORS__', $oscLiteral)
Write-MiosFile (Join-Path $clinkDirectory 'themes\mios-ssot.clinkprompt') ($cmdTheme + "`n")
& $clink autorun install --allusers
if ($LASTEXITCODE -ne 0) { throw 'Could not enable native CMD startup' }
& $clink config prompt use mios-ssot | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not enable native MiOS CMD prompt' }

# Upgrade only the owned dashboard and startup hook, retaining other functions and edits.
$profilePath = if (Test-Path -LiteralPath 'M:\MiOS\powershell\profile.ps1') { 'M:\MiOS\powershell\profile.ps1' } else { [string]$PROFILE.CurrentUserAllHosts }
Set-MiosNativeProfile $profilePath $BinDirectory
Write-MiosFile (Join-Path $BinDirectory 'mios-agent-cli-setup.ps1') ([IO.File]::ReadAllText((Join-Path $SourceRoot 'usr\share\mios\windows\mios-agent-cli-setup.ps1')))
if (-not $SkipAgentInstall) { & (Join-Path $BinDirectory 'mios-agent-cli-setup.ps1') -Distro $Distro -LinuxUser $LinuxUser }
}

if (-not $SkipClients) {
    $wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
    $argsMcp = @('-d', $Distro, '-u', $LinuxUser, '--', $mcpPython, '/usr/libexec/mios/mios-mcp-server')
    $mcpEntry = @{command=$wsl; args=$argsMcp}
    foreach ($path in @(
        (Join-Path $env:USERPROFILE '.claude.json'),
        (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'),
        (Join-Path $env:USERPROFILE '.gemini\settings.json'),
        (Join-Path $env:USERPROFILE '.gemini\config\mcp_config.json'),
        (Join-Path $env:USERPROFILE '.gemini\antigravity-cli\mcp_config.json'),
        (Join-Path $env:USERPROFILE '.cursor\mcp.json')
    )) {
        $object = Read-MiosJson $path
        if (-not $object.Contains('mcpServers')) { $object['mcpServers'] = @{} }
        $object['mcpServers']['mios-control'] = $mcpEntry
        Save-MiosJson $path $object
    }
    $codexPath = Join-Path $env:USERPROFILE '.codex\config.toml'
    $codex = if (Test-Path -LiteralPath $codexPath) { Get-Content -Raw -LiteralPath $codexPath } else { '' }
    # Replace only the owned server and its subtables; preserve every other section.
    $codex = [regex]::Replace($codex, '(?ms)^\[mcp_servers\.mios-control(?:\.[^\]]+)?\]\r?\n.*?(?=^\[|\z)', '')
    $codex = $codex.TrimEnd() + "`n`n[mcp_servers.mios-control]`ncommand = " + ($wsl | ConvertTo-Json -Compress) + "`nargs = " + ($argsMcp | ConvertTo-Json -Compress) + "`nenabled = true`n"
    Write-MiosFile $codexPath $codex

    $copilot = Join-Path $env:USERPROFILE '.copilot\mcp-config.json'
    $object = Read-MiosJson $copilot
    if (-not $object.Contains('mcpServers')) { $object['mcpServers'] = @{} }
    $object['mcpServers']['mios-control'] = @{type='local';command=$wsl;args=$argsMcp;tools=@('*')}
    Save-MiosJson $copilot $object
    $opencode = Join-Path $env:USERPROFILE '.config\opencode\opencode.json'
    $object = Read-MiosJson $opencode
    if (-not $object.Contains('mcp')) { $object['mcp'] = @{} }
    $ocCommand = Join-Path (Join-Path $env:ProgramData $config['agent_cli']['windows_directory']) 'npm\opencode.cmd'
    $v2 = (Test-Path -LiteralPath $ocCommand) -and ((& $ocCommand --version) -match '^2\.')
    if ($v2) {
        if (-not $object['mcp'].Contains('servers')) { $object['mcp']['servers'] = @{} }
        $object['mcp'].Remove('mios-control')
        $object['mcp']['servers']['mios-control'] = @{type='local';command=@($wsl)+$argsMcp}
    } else { $object['mcp']['mios-control'] = @{type='local';command=@($wsl)+$argsMcp;enabled=$true} }
    # Resolve through the same guest SSOT and Rust provider projector as Linux.
    $projection = ($object | ConvertTo-Json -Depth 40 -Compress) | & $wsl -d $Distro -u $LinuxUser -- /usr/bin/mios-ai-config --root / --opencode-stdin
    if ($LASTEXITCODE -ne 0) { throw 'Native local OpenCode projection failed' }
    $object = ($projection -join "`n") | ConvertFrom-Json -AsHashtable
    Save-MiosJson $opencode $object

    if (-not $RuntimeOnly) {
    $profileRoot = Join-Path $SourceRoot 'usr\share\mios\keybindings'
    $bindings = @(Get-Content -Raw (Join-Path $profileRoot 'vscode-keybindings.json') | ConvertFrom-Json -AsHashtable)
    foreach ($directory in @((Join-Path $env:APPDATA 'Code\User'), (Join-Path $env:APPDATA 'Code - Insiders\User'))) {
        if ($directory -like '*Insiders*' -and -not (Test-Path -LiteralPath $directory)) { continue }
        $keyPath = Join-Path $directory 'keybindings.json'
        $prior = if (Test-Path -LiteralPath $keyPath) { @(Get-Content -Raw -LiteralPath $keyPath | ConvertFrom-Json -AsHashtable) } else { @() }
        $keys = @($bindings | ForEach-Object { $_['key'] })
        $merged = @($prior | Where-Object { $_['key'] -notin $keys }) + $bindings
        # Editor shortcuts launch Windows shims; terminal input still passes through to tmux.
        foreach ($row in $merged) {
            if ($row['key'] -in $keys -and $row['command'] -eq 'runCommands') {
                foreach ($action in $row['args']['commands']) {
                    if ($action -is [Collections.IDictionary] -and $action['command'] -eq 'workbench.action.terminal.sendSequence') {
                        $text = $action['args']['text']
                        $action['args']['text'] = $text.Replace('/usr/libexec/mios/mios-ai-terminal', 'mios.cmd ai-terminal').Replace('mios ai', 'mios.cmd ai').Replace('mios mon', 'mios.cmd mon').Replace('mios agents --watch', 'mios.cmd agents --watch')
                    }
                }
            }
        }
        Save-MiosJson $keyPath $merged
        $settingsPath = Join-Path $directory 'settings.json'
        $settings = Read-MiosJson $settingsPath
        $settings['terminal.integrated.allowChords'] = $config['keybindings']['vscode_allow_chords']
        $settings['terminal.integrated.allowMnemonics'] = $config['keybindings']['vscode_allow_mnemonics']
        $settings['terminal.integrated.commandsToSkipShell'] = @($config['keybindings']['vscode_passthrough_commands'] | ForEach-Object { "-$_" })
        $settings['terminal.integrated.fontFamily'] = $config['font']['family']
        $settings['terminal.integrated.fontSize'] = $config['font']['size']
        if (-not $settings.Contains('terminal.integrated.env.windows')) { $settings['terminal.integrated.env.windows'] = @{} }
        $settings['terminal.integrated.env.windows']['PATH'] = '${env:PATH};' + $BinDirectory
        Save-MiosJson $settingsPath $settings
    }
}
}

# A CMD profile uses the same terminal font and named palette as the existing
# SSOT projection, rather than assigning a separate console theme.
$terminalPaths = @(
    (Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'),
    (Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\settings.json')
)
foreach ($path in $terminalPaths) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $terminal = Read-MiosJson $path
    Set-MiosTerminalStartup $terminal $config
    if (-not $terminal.Contains('compatibility')) { $terminal['compatibility'] = @{} }
    $terminal['compatibility']['enableUnfocusedAcrylic'] = $true
    $palette = $config['colors']
    $scheme = @{name=$config['theme']['terminal']['scheme_name']; background=$palette['bg']; foreground=$palette['fg']; cursorColor=$palette['cursor']; selectionBackground=$palette['muted']}
    $colors = @('black','red','green','yellow','blue','magenta','cyan','white')
    for ($index = 0; $index -lt $colors.Count; $index++) {
        $color = $colors[$index]
        $field = if ($color -eq 'magenta') { 'purple' } else { $color }
        $scheme[$field] = $palette["ansi_${index}_$color"]
        $scheme['bright' + (Get-Culture).TextInfo.ToTitleCase($field)] = $palette["ansi_$($index+8)_bright_$color"]
    }
    foreach ($key in $scheme.Keys) { if ($key -ne 'name' -and $scheme[$key] -notmatch '^#[0-9a-fA-F]{6}$') { throw "SSOT terminal palette color $key is invalid" } }
    $terminal['schemes'] = @($terminal['schemes'] | Where-Object { $_['name'] -ne $scheme['name'] }) + @($scheme)
    $terminal['actions'] = @($terminal['actions'] | Where-Object { -not ($_['command'] -is [Collections.IDictionary] -and $_['command']['action'] -eq 'globalSummon' -and $_['command']['name'] -like 'MiOS*') }) + @(@{keys=$config['theme']['terminal']['summon_keys'];command=@{action='globalSummon';name=$config['theme']['terminal']['summon_window_name'];dropdownDuration=0}})
    if (-not $terminal.Contains('profiles')) { $terminal['profiles'] = @{list=@()} }
    $profile = @{name='MiOS-CMD'; commandline='cmd.exe'; font=@{face=$config['font']['family']; size=$config['font']['size']}; colorScheme=$config['theme']['terminal']['scheme_name']; padding=$config['theme']['padding']; scrollbarState=$config['theme']['scrollbar_state']}
    $terminal['profiles']['list'] = @($terminal['profiles']['list'] | Where-Object { $_['name'] -ne 'MiOS-CMD' }) + @($profile)
    if (-not $terminal['profiles'].Contains('defaults')) { $terminal['profiles']['defaults'] = @{} }
    $terminal['profiles']['defaults']['colorScheme'] = $scheme['name']
    $terminal['profiles']['defaults']['font'] = @{face=$config['font']['family'];size=$config['font']['size']}
    $terminal['profiles']['defaults']['padding'] = $config['theme']['padding']
    $terminal['profiles']['defaults']['scrollbarState'] = $config['theme']['scrollbar_state']
    Set-MiosTerminalTransparency $terminal['profiles']['defaults'] $config['theme']
    foreach ($item in $terminal['profiles']['list']) {
        $item['font'] = @{face=$config['font']['family']; size=$config['font']['size']}
        $item['colorScheme'] = $config['theme']['terminal']['scheme_name']
        $item['padding'] = $config['theme']['padding']
        $item['scrollbarState'] = $config['theme']['scrollbar_state']
        $item['cursorShape'] = $config['theme']['cursor_shape']
        Set-MiosTerminalTransparency $item $config['theme']
        $item['suppressApplicationTitle'] = $config['theme']['suppress_app_title']
        if ($item['name'] -eq $config['theme']['terminal']['dev_profile_name']) {
            $enginePath = if ($RuntimeOnly) { $binding.engine } else { $engine }
            $item['commandline'] = "`"$enginePath`" -NoLogo -NoProfile -File `"$(Join-Path $BinDirectory 'mios-native-entry.ps1')`" terminal"
            $item['startingDirectory'] = '%USERPROFILE%'
        }
        if ($item['name'] -in @($config['theme']['terminal']['profile_name'], 'MiOS-CMD')) {
            $item['commandline'] = "`"$(Join-Path $BinDirectory 'mios-launch.exe')`" --host-terminal"
            $item['startingDirectory'] = '%USERPROFILE%'
        }
        if ($item['source'] -eq 'Windows.Terminal.Wsl' -and $item['name'] -notin $registered) { $item['hidden'] = $true }
    }
    $default = @($terminal['profiles']['list'] | Where-Object { $_['name'] -eq $config['theme']['terminal']['hub_target_profile'] })[0]
    if ($default) { $terminal['defaultProfile'] = if ($default['guid']) { $default['guid'] } else { $default['name'] } }
    Save-MiosJson $path $terminal
}
# Installation, build-time staging and each runtime projection share the Rust renderer.
& (Join-Path $BinDirectory 'mios-launch.exe') --stage-host-terminal
if ($LASTEXITCODE -ne 0) { throw "Native host tmux projection failed (exit $LASTEXITCODE)" }
# Reconcile the same entrypoints on install and each native runtime projection.
# Preserve the icon and hotkey while replacing the retired hub launcher route.
$shell = New-Object -ComObject WScript.Shell
$desktopRoots = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'))
$programsRoots = @((Join-Path ([Environment]::GetFolderPath('CommonStartMenu')) 'Programs'), (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
Set-MiosUnifiedShortcuts $shell $config $BinDirectory $desktopRoots $programsRoots
if (-not $RuntimeOnly) {
    Write-Host "Installed CMD entrypoint: $(Join-Path $BinDirectory 'mios.cmd')"
    Write-Host "Native runtime: $Distro / $LinuxUser; $($changed.Count) changed files with backups."
}
if ($EmitConfig) { $config | ConvertTo-Json -Depth 100 -Compress }
