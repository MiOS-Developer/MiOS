# AI-hint: Shared library for the mios-install unified dispatcher and its wrapped entrypoints. Contains: 1. Enable-MiosVt / Get-MiosPalette / C / B / Rule / Write-MiosLine /...
# AI-doc: usr/share/doc/mios/manual/installation.md

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
if (-not (Get-Variable -Name Root -Scope Script -ErrorAction SilentlyContinue)) {
    $script:Root = Split-Path -Parent $PSScriptRoot
}

function Enable-MiosVt {
    if ($env:MIOS_NO_COLOR -or $env:NO_COLOR) { return $false }
    if ($env:OS -notmatch 'Windows') { return $true }
    try {
        $sig = '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n);' +
               '[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out int m);' +
               '[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, int m);'
        $type = Add-Type -MemberDefinition $sig -Name ('MiosCommonVt_' + [Guid]::NewGuid().ToString('N')) -Namespace 'MiosVt' -PassThru -ErrorAction Stop
        $h = $type::GetStdHandle(-11); $m = 0
        if ($type::GetConsoleMode($h, [ref]$m)) {
            return [bool]$type::SetConsoleMode($h, ($m -bor 0x0004 -bor 0x0001))
        }
    } catch {}
    return $false
}

function Get-MiosSsotValue {
    param(
        [Parameter(Mandatory=$true)][string]$Section,
        [Parameter(Mandatory=$true)][string]$Key,
        [string]$Default = '',
        [string]$TomlPath = ''
    )
    $userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $candidatePaths = if ($TomlPath) { @($TomlPath) } else {
        @(
            (Join-Path $userHome '.config\mios\mios.toml'),
            'C:\ProgramData\MiOS\mios.toml',
            'C:\MiOS\usr\share\mios\mios.toml',
            'C:\Windows\Web\MiOS\mios.toml',
            'M:\etc\mios\mios.toml',
            'M:\usr\share\mios\mios.toml',
            (Join-Path (Split-Path -Parent $PSScriptRoot) 'mios.toml'),
            (Join-Path (Split-Path -Parent $PSScriptRoot) 'usr\share\mios\mios.toml'),
            '/usr/share/mios/mios.toml'
        )
    }
    foreach ($p in $candidatePaths) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $raw = Get-Content -Raw -LiteralPath $p -ErrorAction Stop
            $secMatch = [regex]::Match($raw, "(?ms)^\s*\[" + [regex]::Escape($Section) + "\]\s*(.*?)(?=^\s*\[|\z)")
            if ($secMatch.Success) {
                $keyMatch = [regex]::Match($secMatch.Groups[1].Value, "(?m)^\s*" + [regex]::Escape($Key) + "\s*=\s*(?:`"([^`"]*)`"|'([^']*)'|(\S+))")
                if ($keyMatch.Success) {
                    for ($g = 1; $g -le 3; $g++) {
                        if ($keyMatch.Groups[$g].Success) { return $keyMatch.Groups[$g].Value }
                    }
                }
            }
        } catch {}
    }
    return $Default
}

function Convert-MiosHexToRgb {
    param([string]$Hex, [int[]]$DefaultRgb)
    if ($Hex -and $Hex -match '^#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$') {
        try {
            return @(
                [convert]::ToInt32($Matches[1], 16),
                [convert]::ToInt32($Matches[2], 16),
                [convert]::ToInt32($Matches[3], 16)
            )
        } catch {}
    }
    return $DefaultRgb
}

function Get-MiosPalette {
    param([string]$TomlPath = '')
    $fallback = @{
        bg      = @(40,34,98)
        fg      = @(231,223,211)
        accent  = @(26,64,127)
        cursor  = @(243,92,21)
        success = @(62,119,101)
        warning = @(243,92,21)
        error   = @(220,39,27)
        muted   = @(148,142,142)
        subtle  = @(183,201,215)
    }
    $pal = @{}
    foreach ($k in $fallback.Keys) {
        $defHex = '#{0:X2}{1:X2}{2:X2}' -f $fallback[$k][0], $fallback[$k][1], $fallback[$k][2]
        $hex = Get-MiosSsotValue -Section 'colors' -Key $k -Default $defHex -TomlPath $TomlPath
        $pal[$k] = Convert-MiosHexToRgb $hex $fallback[$k]
    }
    return $pal
}

$script:MiosVtOk = Enable-MiosVt
$script:Pal      = Get-MiosPalette
$script:TomlPath = @(
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'mios.toml'),
    'C:\MiOS\usr\share\mios\mios.toml'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

function C {
    param([int[]]$rgb, [string]$t)
    if (-not $script:MiosVtOk -or $env:MIOS_NO_COLOR -or $env:NO_COLOR) { return $t }
    $e = [char]27
    return "$e[38;2;$($rgb[0]);$($rgb[1]);$($rgb[2])m$t$e[0m"
}

function B {
    param([string]$t)
    if (-not $script:MiosVtOk -or $env:MIOS_NO_COLOR -or $env:NO_COLOR) { return $t }
    $e = [char]27
    return "$e[1m$t$e[0m"
}

function Rule {
    param([int]$Width = 64)
    return (C $script:Pal.muted ("=" * $Width))
}

function Write-MiosLine {
    param([string]$Level, [string]$Message)
    $tag = switch ($Level.ToLower()) {
        'info'  { C $script:Pal.accent  (B '[INFO]') }
        'warn'  { C $script:Pal.warning (B '[WARN]') }
        'err'   { C $script:Pal.error   (B '[FAIL]') }
        'pass'  { C $script:Pal.success (B '[PASS]') }
        default { C $script:Pal.muted   (B "[$Level]") }
    }
    Write-Host "  $tag $Message"
}

function Write-MiosKV {
    param([string]$Key, [string]$Value)
    $k = (C $script:Pal.subtle ("{0,-14}" -f $Key))
    $v = (C $script:Pal.fg $Value)
    Write-Host "  $k : $v"
}

function Test-MiosAdmin {
    if ($env:OS -notmatch 'Windows') { return $true }
    try {
        $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $pr = New-Object System.Security.Principal.WindowsPrincipal($id)
        return $pr.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Invoke-MiosSelfElevate {
    param(
        [string]$ScriptPath = $MyInvocation.PSCommandPath,
        [string[]]$ArgList = @()
    )
    if (Test-MiosAdmin) { return $true }
    if ($env:NONINTERACTIVE -eq '1') {
        if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
            Write-MiosLine 'err' 'Administrator privileges required for non-interactive execution.'
        } else {
            Write-Error 'Administrator privileges required for non-interactive execution.'
        }
        exit 1
    }
    if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
        Write-MiosLine 'warn' 'Requesting administrator privileges (UAC prompt)...'
    } else {
        Write-Host '  [*] Requesting administrator privileges (UAC prompt)...' -ForegroundColor Yellow
    }
    if (-not $ScriptPath) {
        $ScriptPath = $MyInvocation.ScriptName
    }
    $psBin = if (Get-Command pwsh.exe -ErrorAction SilentlyContinue) { 'pwsh.exe' } else { 'powershell.exe' }
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + $ArgList
    try {
        $proc = Start-Process $psBin -ArgumentList $psArgs -Verb RunAs -PassThru
        $proc.WaitForExit()
        exit $proc.ExitCode
    } catch {
        if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
            Write-MiosLine 'err' 'Elevation declined or failed.'
        } else {
            Write-Error 'Elevation declined or failed.'
        }
        exit 1
    }
}

function Ensure-MiosBootstrapRepo {
    param(
        [string]$TargetDir = 'C:\mios-bootstrap',
        [string]$RepoUrl = 'https://github.com/mios-dev/mios-bootstrap.git',
        [string]$ZipUrl = 'https://codeload.github.com/mios-dev/mios-bootstrap/zip/refs/heads/main',
        [string]$SentinelFile = 'field\autounattend\Build-MiOSXboxISO.ps1'
    )
    if (Get-Command Get-MiosTomlValue -ErrorAction SilentlyContinue) {
        $cfgRepo = Get-MiosTomlValue -Key 'bootstrap.mios_repo' -Default $RepoUrl
        if ($cfgRepo) { $RepoUrl = $cfgRepo }
        $cfgDir = Get-MiosTomlValue -Key 'bootstrap.bootstrap_repo' -Default $TargetDir
        if ($cfgDir) { $TargetDir = $cfgDir }
    }

    $sentinelPath = Join-Path $TargetDir $SentinelFile
    if (Test-Path $sentinelPath) { return $TargetDir }

    if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
        Write-MiosLine 'info' "mios-bootstrap repo missing -- fetching to $TargetDir ..."
    } else {
        Write-Host "  [*] mios-bootstrap repo missing -- fetching to $TargetDir ..." -ForegroundColor Cyan
    }

    if (Get-Command git -ErrorAction SilentlyContinue) {
        try { & git clone --depth 1 $RepoUrl $TargetDir 2>&1 | Out-Null } catch {}
    }

    if (-not (Test-Path $sentinelPath)) {
        $zip = Join-Path $env:TEMP 'mios-bootstrap.zip'
        $tmp = Join-Path $env:TEMP ('mios-bs-' + [System.Guid]::NewGuid().ToString('N').Substring(0,8))
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing -ErrorAction Stop
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
            if ($inner) {
                New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
                Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $TargetDir -Recurse -Force
            }
        } catch {
            if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
                Write-MiosLine 'warn' "Fetch failed: $($_.Exception.Message)"
            } else {
                Write-Host "  [!] Fetch failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        } finally {
            Remove-Item $zip,$tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-Path $sentinelPath)) {
        if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
            Write-MiosLine 'err' "Failed to fetch mios-bootstrap repository to $TargetDir (sentinel $SentinelFile missing)"
        } else {
            Write-Error "Failed to fetch mios-bootstrap repository to $TargetDir (sentinel $SentinelFile missing)"
        }
        exit 1
    }

    return $TargetDir
}

function Ensure-MiosRepo {
    param([string]$TargetDir = 'C:\mios-bootstrap')
    return (Ensure-MiosBootstrapRepo -TargetDir $TargetDir)
}

function Resolve-MiosMonitorScript {
    @((Join-Path (Split-Path $script:Root -Parent) 'MiOS\usr\libexec\mios\mios-mon.py'),
      'C:\MiOS\usr\libexec\mios\mios-mon.py', 'M:\usr\libexec\mios\mios-mon.py') |
        Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

function Center-MiosMonitorWindow {
    param([int]$WantedWidth = 0, [int]$WantedHeight = 0)
    if (-not ([System.Management.Automation.PSTypeName]'MiosMonitorCenter').Type) {
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MiosMonitorCenter {
    public delegate bool EnumProc(IntPtr hwnd, IntPtr param);
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct MonitorInfo { public int cbSize; public Rect monitor, work; public uint flags; }
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr param);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int length);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
    [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    public static bool Center(int wantedWidth = 0, int wantedHeight = 0) {
        bool centered = false;
        EnumWindows((hwnd, param) => {
            if (!IsWindowVisible(hwnd)) return true;
            StringBuilder title = new StringBuilder(256);
            if (GetWindowText(hwnd, title, title.Capacity) <= 0 ||
                title.ToString().IndexOf("MiOS Build Monitor", StringComparison.OrdinalIgnoreCase) < 0) return true;
            IntPtr previous = IntPtr.Zero;
            try { previous = SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) {}
            try {
                ShowWindow(hwnd, 9);
                Rect rect;
                MonitorInfo info = new MonitorInfo();
                info.cbSize = Marshal.SizeOf(typeof(MonitorInfo));
                IntPtr monitor = MonitorFromWindow(hwnd, 2);
                if (monitor != IntPtr.Zero && GetMonitorInfo(monitor, ref info) && GetWindowRect(hwnd, out rect)) {
                    int width = (wantedWidth > 0) ? Math.Min(wantedWidth, info.work.Right - info.work.Left) : Math.Min(rect.Right - rect.Left, info.work.Right - info.work.Left);
                    int height = (wantedHeight > 0) ? Math.Min(wantedHeight, info.work.Bottom - info.work.Top) : Math.Min(rect.Bottom - rect.Top, info.work.Bottom - info.work.Top);
                    if (width > 0 && height > 0) {
                        int x = info.work.Left + (info.work.Right - info.work.Left - width) / 2;
                        int y = info.work.Top + (info.work.Bottom - info.work.Top - height) / 2;
                        centered = SetWindowPos(hwnd, IntPtr.Zero, x, y, width, height, 0x14);
                        SetWindowPos(hwnd, new IntPtr(-1), 0, 0, 0, 0, 0x03);
                        SetWindowPos(hwnd, new IntPtr(-2), 0, 0, 0, 0, 0x03);
                        SetForegroundWindow(hwnd);
                    }
                }
            } finally { if (previous != IntPtr.Zero) SetThreadDpiAwarenessContext(previous); }
            return false;
        }, IntPtr.Zero);
        return centered;
    }
}
'@ -ErrorAction Stop
    }
    return [MiosMonitorCenter]::Center($WantedWidth, $WantedHeight)
}

function Start-MiosMonitor {
    param([string]$LogPath, [string]$MarkerPath, [string]$Title = 'MiOS-Field', [switch]$InProcess)
    if ("$($env:MIOS_NO_MONITOR)".ToLower() -in @('1','true','yes','on') -or
        "$($env:MIOS_HEADLESS)".ToLower() -in @('1','true','yes','on')) { return $null }

    $mon = Resolve-MiosMonitorScript
    if (-not $mon) { return $null }

    $cols = [int](Get-MiosSsotValue -Section 'terminal.monitor' -Key 'cols' -Default '80')
    $rows = [int](Get-MiosSsotValue -Section 'terminal.monitor' -Key 'rows' -Default '20')
    $cellW = [int](Get-MiosSsotValue -Section 'theme.font' -Key 'cell_w_px' -Default '10')
    $cellH = [int](Get-MiosSsotValue -Section 'theme.font' -Key 'cell_h_px' -Default '20')
    $chromeW = [int](Get-MiosSsotValue -Section 'theme.font' -Key 'chrome_w_px' -Default '20')
    $chromeH = [int](Get-MiosSsotValue -Section 'theme.font' -Key 'chrome_h_px' -Default '12')
    $wantedW = ($cols * $cellW) + $chromeW
    $wantedH = ($rows * $cellH) + $chromeH

    $escapedPath = [regex]::Escape($mon)
    $alreadyRunning = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match $escapedPath -and ($_.CommandLine -match '--pipeline' -or $_.CommandLine -match 'mios-mon') } |
        Select-Object -First 1
    if ($alreadyRunning -and (Center-MiosMonitorWindow -WantedWidth $wantedW -WantedHeight $wantedH)) { return $alreadyRunning }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $python) { return $null }

    if ($InProcess) {
        $env:MIOS_MONITOR_RUNNING = '1'
        & $python $mon --pipeline
        return $null
    }

    $wtExe = Get-Command wt.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $wtExe) { return $null }
    $profile = Get-MiosSsotValue -Section 'theme.terminal' -Key 'profile_name' -Default 'MiOS-WIN'
    $scheme = Get-MiosSsotValue -Section 'theme.terminal' -Key 'scheme_name' -Default 'MiOS'
    $mode = Get-MiosSsotValue -Section 'theme' -Key 'launch_mode' -Default 'focus'
    Add-Type -AssemblyName System.Windows.Forms
    $work = [System.Windows.Forms.Screen]::FromPoint([System.Windows.Forms.Cursor]::Position).WorkingArea
    $x = [int]($work.X + [Math]::Max(0, $work.Width - $wantedW) / 2)
    $y = [int]($work.Y + [Math]::Max(0, $work.Height - $wantedH) / 2)
    $modeArgs = switch ($mode) {
        'focus' { @('--focus') }
        'maximized' { @('--maximized') }
        'maximizedFocus' { @('--maximized', '--focus') }
        'fullscreen' { @('--fullscreen') }
        'focusFullscreen' { @('--fullscreen', '--focus') }
        default { @() }
    }
    $modeText = @($modeArgs) -join ' '

    $tmuxExe = Get-Command tmux.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if (-not $tmuxExe) {
        foreach ($tc in @(
            "$env:LOCALAPPDATA\Microsoft\WinGet\Links\tmux.exe",
            "$env:ProgramFiles\WinGet\Links\tmux.exe"
        )) { if (Test-Path -LiteralPath $tc) { $tmuxExe = $tc; break } }
    }
    if ($tmuxExe) {
        & $tmuxExe kill-session -t mios-mon 2>$null
        $pwsh = (Get-Command pwsh.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
        if (-not $pwsh) {
            foreach ($cand in @(
                "$env:LOCALAPPDATA\Microsoft\WindowsApps\pwsh.exe",
                "$env:ProgramFiles\PowerShell\7\pwsh.exe",
                "$env:ProgramFiles\PowerShell\7-preview\pwsh.exe"
            )) {
                if (Test-Path -LiteralPath $cand) { $pwsh = $cand; break }
            }
        }
        if (-not $pwsh) { $pwsh = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }
        $monitorShell = $pwsh

        $interactiveProfile = if (Test-Path -LiteralPath 'M:\MiOS\powershell\profile.ps1') {
            'M:\MiOS\powershell\profile.ps1'
        } elseif (Test-Path -LiteralPath "$env:USERPROFILE\Documents\PowerShell\Microsoft.PowerShell_profile.ps1") {
            "$env:USERPROFILE\Documents\PowerShell\Microsoft.PowerShell_profile.ps1"
        } else { '' }

        $interactiveShell = if ($interactiveProfile) {
            "$pwsh -NoLogo -NoExit -Command `"`$env:MIOS_APP_CONTEXT='1'; . '$interactiveProfile'`""
        } else {
            "$pwsh -NoLogo -NoExit"
        }

        $monRunner = "`"$python`" `"$mon`" --pipeline"
        if ($mode -in @('fullscreen', 'focusFullscreen')) {
            # 1 + 4 Expanded View: Head (smaller ~38%) on LEFT, Monitor + 3 Workers (2x2 grid ~62%) on RIGHT
            & $tmuxExe new-session -d -s mios-mon -n "MiOS" -x $cols -y $rows $interactiveShell
            & $tmuxExe set-option -w -t mios-mon:0 automatic-rename off
            & $tmuxExe rename-window -t mios-mon:0 "MiOS"
            & $tmuxExe split-window -h -p 62 -t mios-mon:0.0 $monRunner
            & $tmuxExe split-window -v -t mios-mon:0.1 $interactiveShell
            & $tmuxExe split-window -h -t mios-mon:0.1 $interactiveShell
            & $tmuxExe split-window -h -t mios-mon:0.2 $interactiveShell
            & $tmuxExe select-pane -t mios-mon:0.1
        } elseif ($cols -ge $rows) {
            # Landscape Golden Ratio: Head (smaller 38.2%) on LEFT, Monitor (bigger 61.8%) on RIGHT
            & $tmuxExe new-session -d -s mios-mon -n "MiOS" -x $cols -y $rows $interactiveShell
            & $tmuxExe set-option -w -t mios-mon:0 automatic-rename off
            & $tmuxExe rename-window -t mios-mon:0 "MiOS"
            & $tmuxExe split-window -h -p 62 -t mios-mon:0.0 $monRunner
            & $tmuxExe select-pane -t mios-mon:0.1
        } else {
            # Portrait & Mobile Golden Ratio: Monitor (bigger 61.8%) on TOP, Head (smaller 38.2%) on BOTTOM
            & $tmuxExe new-session -d -s mios-mon -n "MiOS" -x $cols -y $rows $monRunner
            & $tmuxExe set-option -w -t mios-mon:0 automatic-rename off
            & $tmuxExe rename-window -t mios-mon:0 "MiOS"
            & $tmuxExe split-window -v -p 38 -t mios-mon:0.0 $interactiveShell
            & $tmuxExe select-pane -t mios-mon:0.0
        }
        $wtInner = "`"$tmuxExe`" attach-session -t mios-mon"
    } else {
        $wtInner = "`"$python`" `"$mon`" --pipeline"
    }
    $wtArgs = "$modeText --pos `"$x,$y`" --size `"$cols,$rows`" -w new new-tab --profile `"$profile`" --colorScheme `"$scheme`" --title `"MiOS Build Monitor`" $wtInner"
    try {
        $p = Start-Process -FilePath $wtExe -ArgumentList $wtArgs -WindowStyle Normal -PassThru -ErrorAction Stop
        $deadline = (Get-Date).AddSeconds(6)
        while ((Get-Date) -lt $deadline) {
            [void](Center-MiosMonitorWindow -WantedWidth $wantedW -WantedHeight $wantedH)
            Start-Sleep -Milliseconds 250
        }
        return $p
    } catch { return $null }
}

function Read-MiosSecret {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$false)][string]$Prompt = "Secret:",
        [switch]$AsSecureString,
        [switch]$PersistDpapi,
        [string]$PersistPath = ""
    )
    if ($Prompt) {
        Write-Host "  $Prompt " -NoNewline -ForegroundColor White
    }

    $secure = Read-Host -MaskInput -AsSecureString
    if ($AsSecureString) {
        return $secure
    }

    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $plain = ""
    try {
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }

    if ($PersistDpapi) {
        if (-not $PersistPath) {
            $MiosAppDir = Join-Path $env:LOCALAPPDATA "MiOS"
            if (-not (Test-Path $MiosAppDir)) { New-Item -ItemType Directory -Path $MiosAppDir -Force | Out-Null }
            $PersistPath = Join-Path $MiosAppDir ".mios-secrets.dpapi"
        }
        $dir = Split-Path $PersistPath -Parent
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

        $encrypted = ConvertFrom-SecureString -SecureString $secure
        Set-Content -Path $PersistPath -Value $encrypted -Encoding UTF8

        try {
            $acl = Get-Acl $PersistPath
            $acl.SetAccessRuleProtection($true, $false)
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
                "FullControl", "Allow"
            )
            $acl.AddAccessRule($rule)
            Set-Acl $PersistPath $acl
        } catch {}
    }

    return $plain
}

function Get-MiosSecretDpapi {
    param([string]$PersistPath = "")
    if (-not $PersistPath) {
        $PersistPath = Join-Path $env:LOCALAPPDATA "MiOS\.mios-secrets.dpapi"
    }
    if (-not (Test-Path $PersistPath)) { return $null }
    try {
        $encrypted = Get-Content -Raw -Path $PersistPath
        return ConvertTo-SecureString $encrypted.Trim()
    } catch {
        return $null
    }
}

function Resolve-MiosDistro {
    param([string]$Default = 'podman-MiOS-DEV')
    if ($env:MIOS_WSL_DISTRO) { return $env:MIOS_WSL_DISTRO }
    try {
        $lxss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
        if (Test-Path $lxss) {
            $all = @(Get-ChildItem $lxss -ErrorAction SilentlyContinue |
                     ForEach-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DistributionName } |
                     Where-Object { $_ })
            $resolved = ($all | Where-Object { $_ -match 'MiOS' } | Select-Object -First 1)
            if ($resolved) { return $resolved }
            $defGuid = (Get-ItemProperty $lxss -Name DefaultDistribution -ErrorAction SilentlyContinue).DefaultDistribution
            if ($defGuid) {
                $defName = (Get-ItemProperty (Join-Path $lxss $defGuid) -ErrorAction SilentlyContinue).DistributionName
                if ($defName) { return $defName }
            }
            if ($all.Count -gt 0) { return $all[0] }
        }
    } catch {}
    return $Default
}

# ============================================================================
#  MiOS-Data & OCI Bulk Staging (task_239 / task_3411)
# ============================================================================

function Read-MiosTarEntry {
    param([string]$ArchivePath, [string]$Entry, [switch]$KeepBytes)
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = (Get-Command tar.exe -ErrorAction Stop).Source
    $start.Arguments = '-xOf "' + $ArchivePath + '" "' + $Entry + '"'
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($start)
    $sha = [Security.Cryptography.SHA256]::Create()
    $memory = if ($KeepBytes) { New-Object IO.MemoryStream } else { $null }
    $buffer = New-Object byte[] (1024 * 1024)
    [long]$size = 0
    try {
        while (($count = $process.StandardOutput.BaseStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $null = $sha.TransformBlock($buffer, 0, $count, $buffer, 0)
            if ($memory) { $memory.Write($buffer, 0, $count) }
            $size += $count
        }
        $null = $sha.TransformFinalBlock([byte[]]@(), 0, 0)
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Cannot read OCI tar entry $Entry" }
        return [pscustomobject]@{
            Size = $size
            Sha256 = (-join ($sha.Hash | ForEach-Object { $_.ToString('x2') }))
            Bytes = if ($memory) { $memory.ToArray() } else { $null }
        }
    } finally {
        if ($memory) { $memory.Dispose() }
        $sha.Dispose()
        $process.Dispose()
    }
}

function Assert-MiosOCIBlob {
    param([string]$ArchivePath, [hashtable]$Entries, $Descriptor, [switch]$KeepBytes)
    if ($Descriptor.digest -notmatch '^sha256:([a-fA-F0-9]{64})$') { throw 'Invalid OCI descriptor digest' }
    $expected = $Matches[1]
    $key = "blobs/sha256/$expected"
    if (-not $Entries.ContainsKey($key)) { throw "Missing OCI blob $key" }
    $blob = Read-MiosTarEntry -ArchivePath $ArchivePath -Entry $Entries[$key] -KeepBytes:$KeepBytes
    if ($blob.Size -ne [long]$Descriptor.size -or $blob.Sha256 -ine $expected) { throw "OCI blob mismatch: $key" }
    return $blob
}

function Test-MiosOCIArchive {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$ArchiveFilePath)
    if (-not (Test-Path -LiteralPath $ArchiveFilePath -PathType Leaf) -or
        (Get-Item -LiteralPath $ArchiveFilePath).Length -eq 0 -or
        -not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        $entries = @{}
        foreach ($entry in @(& tar.exe -tf $ArchiveFilePath 2>$null)) {
            if ($entry -match '(^/|^[A-Za-z]:|(^|/|\\)\.\.(/|\\|$))') { return $false }
            $key = $entry -replace '^\./', ''
            if ($entries.ContainsKey($key)) { return $false }
            $entries[$key] = $entry
        }
        if ($LASTEXITCODE -ne 0 -or -not $entries.ContainsKey('oci-layout') -or -not $entries.ContainsKey('index.json')) { return $false }
        $layoutBytes = (Read-MiosTarEntry -ArchivePath $ArchiveFilePath -Entry $entries['oci-layout'] -KeepBytes).Bytes
        $indexBytes = (Read-MiosTarEntry -ArchivePath $ArchiveFilePath -Entry $entries['index.json'] -KeepBytes).Bytes
        $layout = [Text.Encoding]::UTF8.GetString($layoutBytes) | ConvertFrom-Json
        $index = [Text.Encoding]::UTF8.GetString($indexBytes) | ConvertFrom-Json
        if ($layout.imageLayoutVersion -ne '1.0.0' -or $index.schemaVersion -ne 2 -or @($index.manifests).Count -eq 0) { return $false }
        foreach ($descriptor in @($index.manifests)) {
            if ($descriptor.mediaType -ne 'application/vnd.oci.image.manifest.v1+json') { return $false }
            $manifestBlob = Assert-MiosOCIBlob -ArchivePath $ArchiveFilePath -Entries $entries -Descriptor $descriptor -KeepBytes
            $manifest = [Text.Encoding]::UTF8.GetString($manifestBlob.Bytes) | ConvertFrom-Json
            if ($manifest.schemaVersion -ne 2 -or @($manifest.layers).Count -eq 0) { return $false }
            $configBlob = Assert-MiosOCIBlob -ArchivePath $ArchiveFilePath -Entries $entries -Descriptor $manifest.config -KeepBytes
            $config = [Text.Encoding]::UTF8.GetString($configBlob.Bytes) | ConvertFrom-Json
            if ($config.os -ne 'linux' -or @($config.rootfs.diff_ids).Count -ne @($manifest.layers).Count) { return $false }
            foreach ($layer in @($manifest.layers)) {
                if ([long]$layer.size -le 0) { return $false }
                $null = Assert-MiosOCIBlob -ArchivePath $ArchiveFilePath -Entries $entries -Descriptor $layer
            }
        }
        return $true
    } catch { return $false }
}

function New-MiosOCIArchive {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$DestinationTarPath,
          [Parameter(Mandatory)][string]$SourceArchivePath)
    if (-not (Test-MiosOCIArchive -ArchiveFilePath $SourceArchivePath)) { throw "No valid bootable MiOS OCI archive at $SourceArchivePath" }
    Copy-Item -LiteralPath $SourceArchivePath -Destination $DestinationTarPath -Force
}

function Expand-MiosOCIImage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ArchiveFilePath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )
    if (-not (Test-Path -LiteralPath $ArchiveFilePath)) {
        $global:LASTEXITCODE = 1
        return $false
    }
    if (-not (Test-MiosOCIArchive -ArchiveFilePath $ArchiveFilePath)) { $global:LASTEXITCODE = 1; return $false }
    $null = New-Item -ItemType Directory -Force -Path $DestinationPath
    try {
        if (Get-Command tar.exe -ErrorAction SilentlyContinue) {
            & tar.exe -xf $ArchiveFilePath -C $DestinationPath 2>&1 | Out-Null
        }
        $hasLayout = Test-Path -LiteralPath (Join-Path $DestinationPath "oci-layout")
        $hasIndex = Test-Path -LiteralPath (Join-Path $DestinationPath "index.json")
        $hasBlobs = Test-Path -LiteralPath (Join-Path $DestinationPath "blobs")
        if ($hasLayout -and $hasIndex -and $hasBlobs) {
            $global:LASTEXITCODE = 0
            return $true
        } else {
            $global:LASTEXITCODE = 1
            return $false
        }
    } catch {
        $global:LASTEXITCODE = 1
        return $false
    }
}

function Test-MiosMediaLayout {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath
    )
    if (-not (Test-Path -LiteralPath $TargetPath)) {
        return $false
    }
    $repoDir = Join-Path $TargetPath "MiOS-Repo"
    if (-not (Test-Path -LiteralPath $repoDir)) {
        return $false
    }
    $dataDir = Join-Path $TargetPath "MiOS-Data"
    if (Test-Path -LiteralPath $dataDir) {
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir "images"))) { return $false }
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir "models"))) { return $false }
    }
    return $true
}

function Invoke-MiosStage {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)]
        [string]$DriveLetter = "D",

        [Parameter()]
        [int]$MinDiskGB = 0,

        [Parameter()]
        [int]$SimulatedDiskSizeGB = 0,

        [Parameter()]
        [double]$SimulatedFreeSpaceGB = 0,

        [Parameter()]
        [switch]$Extract,

        [Parameter()]
        [string]$ArchivePath = ''
    )
    Write-Host "[MiOS-Install] Executing target: stage" -ForegroundColor Green

    $cleanLetter = $DriveLetter.TrimEnd(':\')
    $isPath = ($DriveLetter.Contains('\') -or $DriveLetter.Contains('/') -or (Test-Path -LiteralPath $DriveLetter))
    $drivePath = if ($isPath) { $DriveLetter } else { "${cleanLetter}:\" }

    if (-not (Test-Path -LiteralPath $drivePath)) {
        if (Get-Command 'Write-MiosLine' -ErrorAction SilentlyContinue) {
            Write-MiosLine 'err' "Drive or directory $drivePath not found!"
        } else {
            Write-Host "  [FAIL] Drive or directory $drivePath not found!" -ForegroundColor Red
        }
        $global:LASTEXITCODE = 1
        return $false
    }

    # Resolve min_disk_gb threshold from parameter, environment, SSOT, or fallback to 512
    if ($MinDiskGB -le 0) {
        if ($env:MIOS_FIELD_DATA_PARTITION_MIN_DISK_GB) {
            $parsedEnv = 0
            if ([int]::TryParse($env:MIOS_FIELD_DATA_PARTITION_MIN_DISK_GB, [ref]$parsedEnv) -and $parsedEnv -gt 0) {
                $MinDiskGB = $parsedEnv
            }
        }
    }

    if ($MinDiskGB -le 0) {
        $candidatePaths = @(
            "C:\MiOS\usr\share\mios\mios.toml",
            "C:\MiOS\mios.toml",
            (Join-Path $PSScriptRoot "..\mios.toml"),
            (Join-Path $PSScriptRoot "..\usr\share\mios\mios.toml"),
            (Join-Path $PSScriptRoot "..\..\usr\share\mios\mios.toml"),
            "M:\usr\share\mios\mios.toml",
            "M:\etc\mios\mios.toml"
        )
        foreach ($ssotPath in $candidatePaths) {
            if ($ssotPath -and (Test-Path -LiteralPath $ssotPath)) {
                try {
                    $content = [System.IO.File]::ReadAllText($ssotPath)
                    $rxSec = '(?ms)^\s*\[field\.data_partition\][ \t]*\r?\n(?<body>.*?)(?=^\s*\[|\z)'
                    $mSec = [regex]::Match($content, $rxSec)
                    if ($mSec.Success) {
                        $mKey = [regex]::Match($mSec.Groups['body'].Value, '(?m)^\s*min_disk_gb\s*=\s*(\d+)')
                        if ($mKey.Success) {
                            $MinDiskGB = [int]$mKey.Groups[1].Value
                            break
                        }
                    }
                    $mAny = [regex]::Match($content, '(?m)^\s*min_disk_gb\s*=\s*(\d+)')
                    if ($mAny.Success) {
                        $MinDiskGB = [int]$mAny.Groups[1].Value
                        break
                    }
                } catch {}
            }
        }
    }

    if ($MinDiskGB -le 0) {
        $MinDiskGB = 512
    }

    # Free space is checked after the physical disk gate is known.
    $freeSpaceGB = 0.0
    if ($SimulatedFreeSpaceGB -gt 0) {
        $freeSpaceGB = $SimulatedFreeSpaceGB
    } else {
        try {
            $root = [System.IO.Path]::GetPathRoot((Resolve-Path $drivePath).Path)
            $driveInfo = [System.IO.DriveInfo]::new($root)
            $freeSpaceGB = [math]::Round($driveInfo.AvailableFreeSpace / 1GB, 2)
        } catch {}
    }

    # Total disk size check
    $diskSizeGB = 0
    if ($SimulatedDiskSizeGB -gt 0) {
        $diskSizeGB = $SimulatedDiskSizeGB
    }

    if ($diskSizeGB -le 0) {
        try {
            if (-not $isPath -or $cleanLetter.Length -eq 1) {
                $letterToProbe = if ($cleanLetter.Length -eq 1) { $cleanLetter } else { $cleanLetter.Substring(0, 1) }
                $part = Get-Partition -DriveLetter $letterToProbe -ErrorAction SilentlyContinue
                if ($part) {
                    $disk = Get-Disk -Number $part.DiskNumber -ErrorAction SilentlyContinue
                    if ($disk -and $disk.Size) { $diskSizeGB = [math]::Round($disk.Size / 1GB) }
                }
            } else {
                $item = Get-Item -LiteralPath $drivePath -ErrorAction SilentlyContinue
                if ($item) {
                    $root = [System.IO.Path]::GetPathRoot($item.FullName)
                    $rootLetter = $root.TrimEnd(':\')
                    if ($rootLetter.Length -eq 1) {
                        $part = Get-Partition -DriveLetter $rootLetter -ErrorAction SilentlyContinue
                        if ($part) {
                            $disk = Get-Disk -Number $part.DiskNumber -ErrorAction SilentlyContinue
                            if ($disk -and $disk.Size) { $diskSizeGB = [math]::Round($disk.Size / 1GB) }
                        }
                    }
                }
            }
        } catch {}
    }

    Write-Host "Target disk: $drivePath (Total: $diskSizeGB GB, Free: $freeSpaceGB GB, min_disk_gb: $MinDiskGB GB)"
    if ($diskSizeGB -le 0) {
        Write-Host "  [FAIL] Cannot determine physical disk size for $drivePath." -ForegroundColor Red
        $global:LASTEXITCODE = 1; return $false
    }
    $minRequiredFreeGB = if ($diskSizeGB -ge $MinDiskGB) { 10 } else { 1 }
    if ($freeSpaceGB -lt $minRequiredFreeGB) {
        Write-Host "  [FAIL] Insufficient or unknown free disk space on $drivePath ($freeSpaceGB GB available, $minRequiredFreeGB GB required)." -ForegroundColor Red
        $global:LASTEXITCODE = 1; return $false
    }

    # A large disk is an offline image carrier only when a real image is available.
    # Resolve relative to this checkout, never to the caller's working directory.
    $sourceArchive = ''
    if ($diskSizeGB -ge $MinDiskGB) {
        $archiveCandidates = @()
        if ($ArchivePath) { $archiveCandidates += $ArchivePath }
        if ($env:MIOS_OCI_ARCHIVE) { $archiveCandidates += $env:MIOS_OCI_ARCHIVE }
        $repoRoot = Split-Path -Parent $PSScriptRoot
        $archiveCandidates += (Join-Path $repoRoot 'build/oci-archive/mios-latest.tar')
        $archiveCandidates += (Join-Path $repoRoot 'build/mios-latest.tar')
        foreach ($candidate in $archiveCandidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                if (-not (Test-MiosOCIArchive -ArchiveFilePath $candidate)) {
                    Write-Host "  [FAIL] Invalid OCI archive: $candidate" -ForegroundColor Red
                    $global:LASTEXITCODE = 1; return $false
                }
                $sourceArchive = (Resolve-Path -LiteralPath $candidate).Path
                break
            }
        }
        if (-not $sourceArchive) {
            Write-Host '  [FAIL] Large media requires a real MiOS OCI archive. Supply -ArchivePath or MIOS_OCI_ARCHIVE.' -ForegroundColor Red
            $global:LASTEXITCODE = 1; return $false
        }
    }

    # Always create MiOS-Repo (the lightweight config brain < 16GB)
    $repoDir = Join-Path $drivePath "MiOS-Repo"
    $reposDir = Join-Path $repoDir "repos"
    $null = New-Item -ItemType Directory -Force -Path $reposDir

    # Copy shadow config into MiOS-Repo
    $shadowToml = Join-Path $repoDir 'mios.toml'
    if (-not (Test-Path -LiteralPath $shadowToml)) {
        $tomlCandidates = @(
            (Join-Path $HOME '.config/mios/mios.toml'),
            (Join-Path $PSScriptRoot '../mios.toml'),
            "C:\MiOS\usr\share\mios\mios.toml"
        )
        foreach ($cand in $tomlCandidates) {
            if (Test-Path -LiteralPath $cand -PathType Leaf) {
                Copy-Item -LiteralPath $cand -Destination $shadowToml -ErrorAction Stop
                break
            }
        }
    }
    if (-not (Test-Path -LiteralPath $shadowToml)) { $global:LASTEXITCODE = 1; return $false }

    # Clone/copy repos into MiOS-Repo
    $miosGit = Join-Path $reposDir "MiOS"
    $bootstrapGit = Join-Path $reposDir "mios-bootstrap"
    if (-not (Test-Path -LiteralPath $miosGit)) {
        if (Test-Path "C:\MiOS\.git") {
            try { git clone --depth 1 "file:///C:/MiOS" $miosGit 2>$null } catch {}
        }
        if (-not (Test-Path -LiteralPath $miosGit) -and $env:MIOS_OFFLINE -ne "1") {
            try { git clone --depth 1 https://github.com/mios-dev/mios.git $miosGit 2>$null } catch {}
        }
    }
    if (-not (Test-Path -LiteralPath $bootstrapGit)) {
        if (Test-Path "C:\mios-bootstrap\.git") {
            try { git clone --depth 1 "file:///C:/mios-bootstrap" $bootstrapGit 2>$null } catch {}
        }
        if (-not (Test-Path -LiteralPath $bootstrapGit) -and $env:MIOS_OFFLINE -ne "1") {
            try { git clone --depth 1 https://github.com/mios-dev/mios-bootstrap.git $bootstrapGit 2>$null } catch {}
        }
    }

    # task_239: Stage separate MiOS-Data bulk store ONLY on disks meeting min_disk_gb gate
    if ($diskSizeGB -ge $MinDiskGB) {
        Write-Host "Disk >= ${MinDiskGB}GB gate met ($diskSizeGB GB). Staging separate MiOS-Data bulk store..." -ForegroundColor Cyan
        $dataDir = Join-Path $drivePath "MiOS-Data"
        $imagesDir = Join-Path $dataDir "images"
        $modelsDir = Join-Path $dataDir "models"
        $dnfDir = Join-Path $dataDir "dnf"
        $flatpakDir = Join-Path $dataDir "flatpak"
        $pipDir = Join-Path $dataDir "pip"

        $null = New-Item -ItemType Directory -Force -Path $imagesDir
        $null = New-Item -ItemType Directory -Force -Path $modelsDir
        $null = New-Item -ItemType Directory -Force -Path $dnfDir
        $null = New-Item -ItemType Directory -Force -Path $flatpakDir
        $null = New-Item -ItemType Directory -Force -Path $pipDir

        # Stage OCI archive for tools/install.sh offline path strictly into MiOS-Data/images/
        $stagedArchive = Join-Path $imagesDir "mios-latest.tar"
        Write-Host "Staging OCI archive to $stagedArchive..." -ForegroundColor Cyan

        if ([System.IO.Path]::GetFullPath($sourceArchive) -ine [System.IO.Path]::GetFullPath($stagedArchive)) {
            Copy-Item -LiteralPath $sourceArchive -Destination $stagedArchive -Force -ErrorAction Stop
        }
        if (-not (Test-MiosOCIArchive -ArchiveFilePath $stagedArchive)) { $global:LASTEXITCODE = 1; return $false }

        # Calculate sha256 of OCI archive
        $archiveSha256 = ""
        if (Test-Path -LiteralPath $stagedArchive) {
            $archiveSha256 = (Get-FileHash -LiteralPath $stagedArchive -Algorithm SHA256).Hash.ToLower()
        }

        # Extract OCI Image Layout if requested
        $extractedSuccessfully = $false
        if ($Extract) {
            $extractDir = Join-Path $imagesDir "extracted"
            Write-Host "Extracting OCI Image Layout to $extractDir..." -ForegroundColor Cyan
            $extractedSuccessfully = Expand-MiosOCIImage -ArchiveFilePath $stagedArchive -DestinationPath $extractDir
            if (-not $extractedSuccessfully) { $global:LASTEXITCODE = 1; return $false }
        }

        # Copy build artifacts if available
        if (Test-Path "M:\MiOS-images\") {
            Copy-Item "M:\MiOS-images\*" -Destination $imagesDir -Recurse -Force
        }
        $buildDiskArtifacts = Get-ChildItem -Path "build\*.vhdx", "build\*.raw", "build\*.qcow2", "build\*.iso" -ErrorAction SilentlyContinue
        if ($buildDiskArtifacts) {
            foreach ($art in $buildDiskArtifacts) {
                Copy-Item $art.FullName -Destination $imagesDir -Force
            }
        }

        # Stage model artifacts into MiOS-Data/models/
        Write-Host "Staging model artifacts to $modelsDir..." -ForegroundColor Cyan
        $modelSources = @(
            "C:\MiOS\models",
            "M:\models",
            "M:\MiOS-models",
            "build\models",
            "usr\share\mios\vllm\model",
            "usr\share\mios\models"
        )
        $stagedModelCount = 0
        foreach ($ms in $modelSources) {
            if (Test-Path -LiteralPath $ms) {
                $mFiles = Get-ChildItem -Path $ms -File -Include "*.gguf", "*.bin", "*.safetensors", "*.pt", "*.json" -Recurse -ErrorAction SilentlyContinue
                if ($mFiles) {
                    foreach ($mf in $mFiles) {
                        Copy-Item $mf.FullName -Destination $modelsDir -Force
                        $stagedModelCount++
                    }
                }
            }
        }

        # Stamped models inventory / manifest
        $modelsManifest = Join-Path $modelsDir "models.json"
        $modelsList = @(Get-ChildItem -LiteralPath $modelsDir -File | Where-Object { $_.Name -ne 'models.json' } | ForEach-Object {
            @{ name = $_.Name; size_bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() }
        })
        $modelsObj = @{
            staged_count = $stagedModelCount
            catalog = $modelsList
            updated = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
        $modelsObj | ConvertTo-Json -Depth 4 | Out-File -FilePath $modelsManifest -Encoding utf8

        # Stamp MiOS-Data manifest.json
        $manifestPath = Join-Path $dataDir "manifest.json"
        $manifestObj = [ordered]@{
            version = "1.0"
            updated = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            disk_size_gb = $diskSizeGB
            min_disk_gb = $MinDiskGB
            gate_passed = $true
            oci_archive = [ordered]@{
                path = "MiOS-Data/images/mios-latest.tar"
                sha256 = $archiveSha256
                extracted = [bool]$extractedSuccessfully
            }
            components = [ordered]@{
                images = "MiOS-Data/images"
                models = "MiOS-Data/models"
                dnf = "MiOS-Data/dnf"
                flatpak = "MiOS-Data/flatpak"
                pip = "MiOS-Data/pip"
            }
        }
        $manifestObj | ConvertTo-Json -Depth 4 | Out-File -FilePath $manifestPath -Encoding utf8
        Write-Host "MiOS-Data bulk store staged successfully ($manifestPath)." -ForegroundColor Green
    } else {
        Write-Host "[MiOS-Install] Disk size ($diskSizeGB GB) < min_disk_gb ($MinDiskGB GB) gate from [field.data_partition]." -ForegroundColor Yellow
        Write-Host "[MiOS-Install] Skipping separate MiOS-Data bulk store staging (degrade-open offline mode: small USB stick carries MiOS-Repo config brain only)." -ForegroundColor Yellow
    }

    $global:LASTEXITCODE = 0
    return $true
}

function Invoke-MiosVerify {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)]
        [string]$DriveLetter = "D",

        [Parameter()]
        [int]$MinDiskGB = 512,

        [int]$SimulatedDiskSizeGB = 0
    )
    $cleanLetter = $DriveLetter.TrimEnd(':\')
    $isPath = ($DriveLetter.Contains('\') -or $DriveLetter.Contains('/') -or (Test-Path -LiteralPath $DriveLetter))
    $drivePath = if ($isPath) { $DriveLetter } else { "${cleanLetter}:\" }

    if (-not (Test-Path -LiteralPath $drivePath)) {
        $global:LASTEXITCODE = 1
        return $false
    }

    $diskSizeGB = $SimulatedDiskSizeGB
    if ($diskSizeGB -le 0) {
        try {
            $root = [IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $drivePath).Path)
            $letter = $root.TrimEnd(':\')
            if ($letter.Length -eq 1) {
                $part = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
                if ($part) {
                    $disk = Get-Disk -Number $part.DiskNumber -ErrorAction SilentlyContinue
                    if ($disk) { $diskSizeGB = [math]::Round($disk.Size / 1GB) }
                }
            }
        } catch {}
    }
    if ($diskSizeGB -le 0) { $global:LASTEXITCODE = 1; return $false }

    $repoDir = Join-Path $drivePath "MiOS-Repo"
    if (-not (Test-Path -LiteralPath $repoDir) -or -not (Test-Path -LiteralPath (Join-Path $repoDir "mios.toml"))) {
        $global:LASTEXITCODE = 1
        return $false
    }

    $dataDir = Join-Path $drivePath "MiOS-Data"
    if ($diskSizeGB -ge $MinDiskGB -and -not (Test-Path -LiteralPath $dataDir)) {
        $global:LASTEXITCODE = 1; return $false
    }
    if ($diskSizeGB -lt $MinDiskGB -and (Test-Path -LiteralPath $dataDir)) {
        $global:LASTEXITCODE = 1; return $false
    }
    if (Test-Path -LiteralPath $dataDir) {
        $imagesDir = Join-Path $dataDir "images"
        $modelsDir = Join-Path $dataDir "models"
        $manifestPath = Join-Path $dataDir "manifest.json"
        if (-not (Test-Path -LiteralPath $imagesDir) -or
            -not (Test-Path -LiteralPath $modelsDir) -or
            -not (Test-Path -LiteralPath $manifestPath)) {
            $global:LASTEXITCODE = 1
            return $false
        }
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            if ($manifest.gate_passed -ne $true) {
                $global:LASTEXITCODE = 1
                return $false
            }
            $archive = Join-Path $imagesDir 'mios-latest.tar'
            if (-not (Test-MiosOCIArchive -ArchiveFilePath $archive) -or
                (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $manifest.oci_archive.sha256) {
                $global:LASTEXITCODE = 1
                return $false
            }
        } catch {
            $global:LASTEXITCODE = 1
            return $false
        }
    }

    $global:LASTEXITCODE = 0
    return $true
}

# ============================================================================
#  Backward Compatibility Aliases (task_3411)
# ============================================================================
function Invoke-MiOSFieldStage { Invoke-MiosStage @args }
function Invoke-MiOSFieldVerify { Invoke-MiosVerify @args }
