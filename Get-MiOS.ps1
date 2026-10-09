# AI-hint: Primary entry point for MiOS installation; handles admin elevation, environment validation, and fresh-clone of the bootstrap repo to initiate the preflight, VM setup, and OCI build pipeline.
# AI-doc: usr/share/doc/mios/manual/root.md
<#
.SYNOPSIS
    'MiOS' bootstrap -- canonical Windows one-liner entry point.

.DESCRIPTION
    Designed for: irm https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1 | iex

    Thin entrypoint that:
      1. Elevates to Administrator (re-launches a NEW window so the
         operator sees a clean, properly-sized terminal).
      2. Resizes the host window to ~100x40 so the build dashboard
         frame (80 cols + breathing room) fits without wrapping.
      3. Verifies Git + Podman are present.
      4. Updates a clean bootstrap checkout while preserving local edits,
         existing WSL distributions and persistent installation state.
      5. Hands off to bootstrap.ps1 using the layered SSOT selection
         [bootstrap].windows_install_mode (default: full), including the
         OCI build and deployment phases.

    Pre-v0.2.4 this script wrapped the run in Start-Transcript --
    that captured the dashboard's cursor escapes and broke the
    in-place repaint. Removed: build-mios.ps1 writes its own unified
    log directly via [IO.File]::AppendAllText (no transcript needed).

    Pass -FullBuild to chain the OCI image build immediately
    regardless of the SSOT bootstrap-only selection.

.PARAMETER RepoUrl
    git URL for mios-bootstrap (default: GitHub upstream).

.PARAMETER Branch
    Branch to clone (default: main).

.PARAMETER RepoDir
    Bootstrap checkout. Existing local changes are preserved. Clean checkouts
    update to the selected branch; installation never deletes the checkout.

.PARAMETER FullBuild
    Run the full pipeline in one shot (preflight + dev VM + Windows
    install + OCI build + deploy). Equivalent to passing -FullBuild
    through to bootstrap.ps1.

.PARAMETER Unattended
    Take all defaults; skip interactive prompts.

.PARAMETER Workflow
    Optional preset workflow name (legacy parameter; passed through
    via $env:MIOS_WORKFLOW for any consumer that reads it).
#>
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet('Default', 'BuildXboxISO', 'FlashUSB', 'OfflineSync', 'Install', 'Configure')]
    [string]$Action = 'Default',
    [string]$RepoUrl   = "https://github.com/mios-dev/mios-bootstrap.git",
    [string]$Branch    = "main",
    [string]$RepoDir   = "M:\MiOS\repo\mios-bootstrap",
    [switch]$FullBuild,
    [switch]$Unattended,
    [string]$Workflow  = ""
)

$ErrorActionPreference = "Stop"

# Invoke-Expression runs this body inside its caller's PowerShell process.
# This entrypoint uses `exit` for explicit pipeline exit codes, so executing it
# directly with `irm ... | iex` would close the operator's terminal. Re-run the
# fetched entrypoint as a script file in a child process; exit then returns to
# the caller's prompt. The environment guard prevents the child's cache-busted
# in-process refresh from spawning another child.
if (-not $PSCommandPath -and -not $env:MIOS_GETMIOS_FILE_RELAUNCHED) {
    $entryUrl = 'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1'
    $entryPath = Join-Path $env:TEMP ('mios-entry-' + [guid]::NewGuid().ToString('N') + '.ps1')
    try {
        $entryResponse = Invoke-WebRequest -Uri $entryUrl `
            -Headers @{ 'Cache-Control' = 'no-cache, no-store, max-age=0'; 'Pragma' = 'no-cache' } `
            -UseBasicParsing -ErrorAction Stop
        if (-not $entryResponse.Content -or $entryResponse.Content.Length -lt 1000) {
            throw 'The fetched Get-MiOS.ps1 response was empty or incomplete.'
        }
        [IO.File]::WriteAllText($entryPath, [string]$entryResponse.Content, (New-Object System.Text.UTF8Encoding($true)))
        $engine = Join-Path $PSHOME $(if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' })
        if (-not (Test-Path -LiteralPath $engine -PathType Leaf)) { $engine = 'powershell.exe' }
        $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $entryPath,
            '-Action', $Action, '-RepoUrl', $RepoUrl, '-Branch', $Branch, '-RepoDir', $RepoDir)
        if ($Workflow) { $childArgs += @('-Workflow', $Workflow) }
        if ($FullBuild) { $childArgs += '-FullBuild' }
        if ($Unattended) { $childArgs += '-Unattended' }

        $oldRelaunchFlag = $env:MIOS_GETMIOS_FILE_RELAUNCHED
        $env:MIOS_GETMIOS_FILE_RELAUNCHED = '1'
        try {
            & $engine @childArgs
            $childExit = $LASTEXITCODE
        } finally {
            if ($null -eq $oldRelaunchFlag) { Remove-Item Env:MIOS_GETMIOS_FILE_RELAUNCHED -ErrorAction SilentlyContinue }
            else { $env:MIOS_GETMIOS_FILE_RELAUNCHED = $oldRelaunchFlag }
        }
    } catch {
        Write-Host ("[!] Could not start the MiOS installer in a child PowerShell process: " + $_.Exception.Message) -ForegroundColor Red
        $childExit = 1
    } finally {
        Remove-Item -LiteralPath $entryPath -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $childExit) { $global:LASTEXITCODE = [int]$childExit }
    return
}
if ($PSCommandPath) { $global:MIOS_GETMIOS_FILE_RELAUNCHED = $true }

# Set TLS 1.2 explicitly for down-level/.NET-old hosts
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {}

# Disable console QuickEdit immediately so an accidental click/select in the
# (elevated) window can't freeze the installer on its next write -- Windows
# "mark" mode blocks the process until Enter/Esc is pressed (a click
# during the long install read as a dead hang). build-mios.ps1 re-applies this;
# the type guard makes the second call a no-op. Best-effort; never fatal.
function Disable-ConsoleQuickEdit {
    try {
        if (-not ('MiosConsole.Win32' -as [type])) {
            Add-Type -Namespace MiosConsole -Name Win32 -MemberDefinition '[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern System.IntPtr GetStdHandle(int nStdHandle); [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleMode(System.IntPtr hConsoleHandle, out uint lpMode); [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetConsoleMode(System.IntPtr hConsoleHandle, uint dwMode);' -ErrorAction Stop
        }
        $h = [MiosConsole.Win32]::GetStdHandle(-10)   # STD_INPUT_HANDLE
        [uint32]$mode = 0
        if ([MiosConsole.Win32]::GetConsoleMode($h, [ref]$mode)) {
            $mode = ($mode -band (-bnot [uint32]0x40)) -bor [uint32]0x80   # -QUICK_EDIT +EXTENDED_FLAGS
            [void][MiosConsole.Win32]::SetConsoleMode($h, $mode)
        }
    } catch {}
}
Disable-ConsoleQuickEdit

function Center-MiosBootstrapWindow {
    try {
        if (-not ([System.Management.Automation.PSTypeName]'MiosBootstrapWindow').Type) {
            Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MiosBootstrapWindow {
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct MonitorInfo { public int cbSize; public Rect monitor, work; public uint flags; }
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder name, int count);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out Rect rect);
    [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern bool GetMonitorInfo(IntPtr h, ref MonitorInfo info);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int width, int height, uint flags);
    public static bool CenterForegroundTerminal() {
        IntPtr h = GetForegroundWindow();
        if (h == IntPtr.Zero) return false;
        StringBuilder cls = new StringBuilder(128);
        GetClassName(h, cls, cls.Capacity);
        string name = cls.ToString();
        if (name != "CASCADIA_HOSTING_WINDOW_CLASS" && name != "ConsoleWindowClass") return false;
        IntPtr previous = IntPtr.Zero;
        try { previous = SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) {}
        try {
            Rect rect;
            MonitorInfo info = new MonitorInfo();
            info.cbSize = Marshal.SizeOf(typeof(MonitorInfo));
            IntPtr monitor = MonitorFromWindow(h, 2);
            if (monitor == IntPtr.Zero || !GetMonitorInfo(monitor, ref info) || !GetWindowRect(h, out rect)) return false;
            int width = Math.Min(rect.Right - rect.Left, info.work.Right - info.work.Left);
            int height = Math.Min(rect.Bottom - rect.Top, info.work.Bottom - info.work.Top);
            if (width <= 0 || height <= 0) return false;
            int x = info.work.Left + (info.work.Right - info.work.Left - width) / 2;
            int y = info.work.Top + (info.work.Bottom - info.work.Top - height) / 2;
            return SetWindowPos(h, IntPtr.Zero, x, y, width, height, 0x14);
        } finally { if (previous != IntPtr.Zero) SetThreadDpiAwarenessContext(previous); }
    }
}
"@ -ErrorAction Stop
        }
        [void][MiosBootstrapWindow]::CenterForegroundTerminal()
    } catch {}
}
Center-MiosBootstrapWindow

function Start-MiosBuildMonitor {
    if ($env:MIOS_NO_MONITOR -in @('1','true','yes','on') -or
        $env:MIOS_HEADLESS -in @('1','true','yes','on')) { return }

    $monitorCols = [int](Get-MiosTomlValue -Section 'terminal.monitor' -Key 'cols' -Default 80)
    $monitorRows = [int](Get-MiosTomlValue -Section 'terminal.monitor' -Key 'rows' -Default 20)
    $cellWidth = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'cell_w_px' -Default 10)
    $cellHeight = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'cell_h_px' -Default 20)
    $chromeWidth = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_w_px' -Default 20)
    $chromeHeight = [int](Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_h_px' -Default 12)
    $monitorWidthPx = ($monitorCols * $cellWidth) + $chromeWidth
    $monitorHeightPx = ($monitorRows * $cellHeight) + $chromeHeight

    $monitorScript = @(
        $env:MIOS_MONITOR_SCRIPT,
        'C:\MiOS\usr\libexec\mios\mios-mon.py',
        'M:\usr\libexec\mios\mios-mon.py'
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $monitorScript) { return }

    try {
        if (-not ([System.Management.Automation.PSTypeName]'MiosDeskLauncher').Type) {
            $typeDef = @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MiosDeskLauncher {
    public delegate bool EnumDesktopWindowsProc(IntPtr hWnd, IntPtr lParam);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO {
        public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
        public int dwX; public int dwY; public int dwXSize; public int dwYSize;
        public int dwXCountChars; public int dwYCountChars; public int dwFillAttribute;
        public int dwFlags; public short wShowWindow; public short cbReserved2;
        public IntPtr lpReserved2; public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId;
    }
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CreateProcess(
        string lpApp, string lpCmd, IntPtr pAttr, IntPtr tAttr, bool bInherit,
        uint flags, IntPtr env, string dir, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);
    [DllImport("user32.dll")] public static extern bool EnumDesktopWindows(IntPtr hDesktop, EnumDesktopWindowsProc lpfn, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr hDesktop);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder strText, int maxCount);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int width, int height, bool repaint);
    [DllImport("user32.dll")] public static extern IntPtr MonitorFromWindow(IntPtr hWnd, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern bool GetMonitorInfo(IntPtr monitor, ref MONITORINFO info);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hWnd);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct MONITORINFO { public int cbSize; public RECT monitor; public RECT work; public uint flags; }

    public static bool HasVisibleMonitorWindow() {
        IntPtr hDesk = OpenDesktop("Default", 0, false, 0x01FF);
        if (hDesk == IntPtr.Zero) return false;
        bool found = false;
        EnumDesktopWindows(hDesk, (hWnd, lParam) => {
            if (IsWindowVisible(hWnd)) {
                StringBuilder sb = new StringBuilder(256);
                if (GetWindowText(hWnd, sb, 256) > 0) {
                    if (sb.ToString().IndexOf("MiOS Build Monitor", StringComparison.OrdinalIgnoreCase) >= 0) {
                        found = true;
                        ShowWindow(hWnd, 9);
                        BringWindowToTop(hWnd);
                        SetForegroundWindow(hWnd);
                        return false;
                    }
                }
            }
            return true;
        }, IntPtr.Zero);
        CloseDesktop(hDesk);
        return found;
    }

    public static bool CenterVisibleMonitorWindow(int wantedWidth, int wantedHeight) {
        IntPtr hDesk = OpenDesktop("Default", 0, false, 0x01FF);
        if (hDesk == IntPtr.Zero) return false;
        bool found = false;
        EnumDesktopWindows(hDesk, (hWnd, lParam) => {
            if (IsWindowVisible(hWnd)) {
                StringBuilder sb = new StringBuilder(256);
                if (GetWindowText(hWnd, sb, 256) > 0 &&
                    sb.ToString().IndexOf("MiOS Build Monitor", StringComparison.OrdinalIgnoreCase) >= 0) {
                    ShowWindow(hWnd, 1);
                    IntPtr oldDpi = IntPtr.Zero;
                    try { oldDpi = SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) {}
                    try {
                        RECT rect;
                        MONITORINFO info = new MONITORINFO();
                        info.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
                        IntPtr monitor = MonitorFromWindow(hWnd, 2);
                        if (GetWindowRect(hWnd, out rect) && monitor != IntPtr.Zero && GetMonitorInfo(monitor, ref info)) {
                            uint dpi = 96;
                            try { dpi = GetDpiForWindow(hWnd); } catch (EntryPointNotFoundException) {}
                            int requestedWidth = (int)Math.Round(wantedWidth * dpi / 96.0);
                            int requestedHeight = (int)Math.Round(wantedHeight * dpi / 96.0);
                            int width = Math.Min(requestedWidth, info.work.Right - info.work.Left);
                            int height = Math.Min(requestedHeight, info.work.Bottom - info.work.Top);
                            if (width > 0 && height > 0) {
                                int x = info.work.Left + ((info.work.Right - info.work.Left - width) / 2);
                                int y = info.work.Top + ((info.work.Bottom - info.work.Top - height) / 2);
                                BringWindowToTop(hWnd);
                                SetForegroundWindow(hWnd);
                                MoveWindow(hWnd, x, y, width, height, true);
                            }
                        }
                    } finally { if (oldDpi != IntPtr.Zero) SetThreadDpiAwarenessContext(oldDpi); }
                    found = true;
                    return false;
                }
            }
            return true;
        }, IntPtr.Zero);
        CloseDesktop(hDesk);
        return found;
    }

    public static int Launch(string cmd, string title) {
        STARTUPINFO si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(si);
        si.lpDesktop = @"winsta0\default";
        si.lpTitle = title;
        PROCESS_INFORMATION pi;
        if (CreateProcess(null, cmd, IntPtr.Zero, IntPtr.Zero, false, 0x00000010, IntPtr.Zero, null, ref si, out pi)) {
            CloseHandle(pi.hProcess);
            CloseHandle(pi.hThread);
            return pi.dwProcessId;
        }
        return -1;
    }
}
"@
            Add-Type -TypeDefinition $typeDef -ErrorAction SilentlyContinue
        }

        if (([System.Management.Automation.PSTypeName]'MiosDeskLauncher').Type) {
            if ([MiosDeskLauncher]::CenterVisibleMonitorWindow($monitorWidthPx, $monitorHeightPx)) { return }
        } else {
            $runningProcs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'mios-mon\.py' })
            $hasActive = $false
            foreach ($rp in $runningProcs) {
                $p = Get-Process -Id $rp.ProcessId -ErrorAction SilentlyContinue
                if ($p -and -not $p.HasExited) { $hasActive = $true; break }
            }
            if ($hasActive) { return }
        }

        $python = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
        if (-not $python) {
            foreach ($c in @(
                'C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe',
                'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe',
                'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe',
                'C:\Python314\python.exe',
                'C:\Python312\python.exe'
            )) { if (Test-Path -LiteralPath $c) { $python = $c; break } }
        }
        if (-not $python) { return }

        $wtExe = Get-Command wt.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
        if (-not $wtExe) {
            foreach ($candidate in @(
                "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe",
                "$env:ProgramFiles\WindowsApps\Microsoft.WindowsTerminal*\wt.exe"
            )) {
                $found = Get-Item $candidate -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($found) { $wtExe = $found.FullName; break }
            }
        }
        # Windows Terminal is required by the bootstrap. If it is not ready
        # yet, wait for the post-profile-setup invocation below instead of
        # opening a plain conhost window that can mask the SSOT terminal.
        if (-not $wtExe -or -not (Test-Path -LiteralPath $wtExe)) { return }

        $spawnedPid = -1
        if ($wtExe -and (Test-Path -LiteralPath $wtExe)) {
            $monitorProfile = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'profile_name' -Default 'MiOS-WIN')
            if ([string]::IsNullOrWhiteSpace($monitorProfile)) { $monitorProfile = 'MiOS-WIN' }
            $monitorScheme = [string](Get-MiosTomlValue -Section 'theme.terminal' -Key 'scheme_name' -Default 'MiOS')
            if ([string]::IsNullOrWhiteSpace($monitorScheme)) { $monitorScheme = 'MiOS' }
            $monitorLaunchMode = [string](Get-MiosTomlValue -Section 'theme' -Key 'launch_mode' -Default 'focus')
            $monitorX = 0; $monitorY = 0
            $displayCols = $monitorCols; $displayRows = $monitorRows
            try {
                Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
                $cursor = [System.Windows.Forms.Cursor]::Position
                $workArea = [System.Windows.Forms.Screen]::FromPoint($cursor).WorkingArea
                $displayCols = [math]::Min($monitorCols, [math]::Max(40, [math]::Floor(($workArea.Width - $chromeWidth - 28) / $cellWidth)))
                $displayRows = [math]::Min($monitorRows, [math]::Max(12, [math]::Floor(($workArea.Height - $chromeHeight - 32) / $cellHeight)))
                $monitorWidthPx = ($displayCols * $cellWidth) + $chromeWidth
                $monitorHeightPx = ($displayRows * $cellHeight) + $chromeHeight
                $monitorX = [int]($workArea.X + (($workArea.Width - $monitorWidthPx) / 2))
                $monitorY = [int]($workArea.Y + (($workArea.Height - $monitorHeightPx) / 2))
                if ($monitorX -lt $workArea.X) { $monitorX = $workArea.X }
                if ($monitorY -lt $workArea.Y) { $monitorY = $workArea.Y }
            } catch {}
            $wtWindowArgs = switch ($monitorLaunchMode) {
                'focus'          { @('--focus') }
                'maximized'      { @('--maximized') }
                'maximizedFocus' { @('--maximized','--focus') }
                'fullscreen'     { @('--fullscreen') }
                'focusFullscreen'{ @('--fullscreen','--focus') }
                default          { @() }
            }
            $profileReady = $false
            foreach ($settingsPath in @(
                (Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'),
                (Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe\LocalState\settings.json')
            )) {
                if (-not (Test-Path -LiteralPath $settingsPath)) { continue }
                try {
                    $settingsText = [IO.File]::ReadAllText($settingsPath)
                    $settingsText = [regex]::Replace($settingsText, '(?ms)/\*.*?\*/', '')
                    $settingsText = [regex]::Replace($settingsText, '(?m)^\s*//.*$', '')
                    $settingsText = [regex]::Replace($settingsText, ',(\s*[\}\]])', '$1')
                    $settings = $settingsText | ConvertFrom-Json -ErrorAction Stop
                    if (@($settings.profiles.list | Where-Object { $_.name -eq $monitorProfile }).Count) {
                        $profileReady = $true
                        break
                    }
                } catch {}
            }
            # On a first install, wait until Install-MiOSTerminalProfile has
            # created the SSOT profile; the caller invokes this launcher again
            # immediately after that setup step. This avoids a plain-profile
            # monitor that masks a failed themed launch.
            if (-not $profileReady) { return }
            $wtWindowArgsText = $wtWindowArgs -join ' '
            $monitorCommand = "while (`$true) { & '$($python.Replace("'", "''"))' '$($monitorScript.Replace("'", "''"))' --pipeline; if (`$LASTEXITCODE -eq 0) { break }; Write-Host 'MiOS monitor exited unexpectedly; restarting in 2 seconds' -ForegroundColor Yellow; Start-Sleep -Seconds 2 }"
            $monitorEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($monitorCommand))
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

            $tmuxExe = Get-Command tmux.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
            if (-not $tmuxExe) {
                foreach ($tc in @(
                    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\tmux.exe",
                    "$env:ProgramFiles\WinGet\Links\tmux.exe"
                )) { if (Test-Path -LiteralPath $tc) { $tmuxExe = $tc; break } }
            }

            if ($tmuxExe) {
                & $tmuxExe kill-session -t mios-mon 2>$null
                $monRunner = "`"$python`" `"$monitorScript`" --pipeline"
                if ($monitorLaunchMode -in @('fullscreen', 'focusFullscreen')) {
                    # 1 + 4 Expanded View: Head (smaller ~38%) on LEFT, Monitor + 3 Workers (2x2 grid ~62%) on RIGHT
                    & $tmuxExe new-session -d -s mios-mon -n "MiOS" -x $displayCols -y $displayRows $interactiveShell
                    & $tmuxExe set-option -w -t mios-mon:0 automatic-rename off
                    & $tmuxExe rename-window -t mios-mon:0 "MiOS"
                    & $tmuxExe split-window -h -p 62 -t mios-mon:0.0 $monRunner
                    & $tmuxExe split-window -v -t mios-mon:0.1 $interactiveShell
                    & $tmuxExe split-window -h -t mios-mon:0.1 $interactiveShell
                    & $tmuxExe split-window -h -t mios-mon:0.2 $interactiveShell
                    & $tmuxExe select-pane -t mios-mon:0.1
                } elseif ($displayCols -ge $displayRows) {
                    # Landscape Golden Ratio: Head (smaller 38.2%) on LEFT, Monitor (bigger 61.8%) on RIGHT
                    & $tmuxExe new-session -d -s mios-mon -n "MiOS" -x $displayCols -y $displayRows $interactiveShell
                    & $tmuxExe set-option -w -t mios-mon:0 automatic-rename off
                    & $tmuxExe rename-window -t mios-mon:0 "MiOS"
                    & $tmuxExe split-window -h -p 62 -t mios-mon:0.0 $monRunner
                    & $tmuxExe select-pane -t mios-mon:0.1
                } else {
                    # Portrait & Mobile Golden Ratio: Monitor (bigger 61.8%) on TOP, Head (smaller 38.2%) on BOTTOM
                    & $tmuxExe new-session -d -s mios-mon -n "MiOS" -x $displayCols -y $displayRows $monRunner
                    & $tmuxExe set-option -w -t mios-mon:0 automatic-rename off
                    & $tmuxExe rename-window -t mios-mon:0 "MiOS"
                    & $tmuxExe split-window -v -p 38 -t mios-mon:0.0 $interactiveShell
                    & $tmuxExe select-pane -t mios-mon:0.0
                }
                $wtInnerCmd = "`"$tmuxExe`" attach-session -t mios-mon"
            } else {
                $wtInnerCmd = "`"$monitorShell`" -NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand $monitorEncoded"
            }

            $wtArgsString = "$wtWindowArgsText --pos `"$monitorX,$monitorY`" --size `"$displayCols,$displayRows`" -w `"MiOS-Monitor`" new-tab --profile `"$monitorProfile`" --colorScheme `"$monitorScheme`" --title `"MiOS Build Monitor`" $wtInnerCmd"
            $cmdLine = "`"$wtExe`" $wtArgsString"
            if (([System.Management.Automation.PSTypeName]'MiosDeskLauncher').Type) {
                $spawnedPid = [MiosDeskLauncher]::Launch($cmdLine, 'MiOS Build Monitor')
            }
            if ($spawnedPid -le 0) {
                $monitorProcess = Start-Process -FilePath $wtExe `
                    -ArgumentList $wtArgsString `
                    -WindowStyle Normal -PassThru -ErrorAction SilentlyContinue
                if ($monitorProcess) { $spawnedPid = $monitorProcess.Id }
            }
            if ($spawnedPid -gt 0 -and ([System.Management.Automation.PSTypeName]'MiosDeskLauncher').Type) {
                $centerDeadline = (Get-Date).AddSeconds(6)
                while ((Get-Date) -lt $centerDeadline) {
                    [void][MiosDeskLauncher]::CenterVisibleMonitorWindow($monitorWidthPx, $monitorHeightPx)
                    Start-Sleep -Milliseconds 250
                }
            }
        }

        if ($spawnedPid -le 0) {
            Write-Host '  [!] MiOS monitor could not launch in the SSOT Windows Terminal profile.' -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [!] MiOS monitor launch failed: $($_.Exception.Message)" -ForegroundColor Yellow
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
        # The clone URL lives in [urls].bootstrap_repo -- NOT [bootstrap].mios_repo,
        # which is the LOCAL MiOS checkout path (C:/MiOS). Reading mios_repo here
        # would hand a filesystem path to `git clone <url>`.
        $cfgRepo = Get-MiosTomlValue -Key 'urls.bootstrap_repo' -Default $RepoUrl
        if ($cfgRepo) { $RepoUrl = $cfgRepo }
        $cfgDir = Get-MiosTomlValue -Key 'bootstrap.bootstrap_repo' -Default $TargetDir
        if ($cfgDir) { $TargetDir = $cfgDir }
    }

    $sentinelPath = Join-Path $TargetDir $SentinelFile
    if (Test-Path $sentinelPath) { return $TargetDir }

    Write-Host "  [*] mios-bootstrap repo missing -- fetching to $TargetDir ..." -ForegroundColor Cyan

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
            Write-Host "  [!] Could not fetch mios-bootstrap: $($_.Exception.Message)" -ForegroundColor Yellow
        } finally {
            Remove-Item $zip,$tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-Path $sentinelPath)) {
        Write-Error "Failed to fetch mios-bootstrap repository to $TargetDir (sentinel $SentinelFile missing)"
        exit 1
    }

    return $TargetDir
}

# Consolidated Action Router
if ($Action -ne 'Default') {
    if ($Action -eq 'BuildXboxISO') {
        Write-Host "[*] Action: BuildXboxISO. Invoking Build-MiOSXboxISO..." -ForegroundColor Cyan
        $repoRoot = Ensure-MiosBootstrapRepo
        $buildScript = Join-Path $repoRoot "field\autounattend\Build-MiOSXboxISO.ps1"
        if (-not (Test-Path $buildScript)) {
            Write-Error "Build-MiOSXboxISO.ps1 not found after fetch -- check network / GitHub access."
            exit 1
        }
        # Run using a dynamic free-space work directory
        $v = (Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.SizeRemaining -gt 15GB } | Sort-Object SizeRemaining -Descending | Select-Object -First 1)
        $work = if ($v) { "$($v.DriveLetter):\MiOS\isobuild_live" } else { "C:\MiOS\isobuild_live" }
        Write-Host "    Using WorkDir: $work" -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $buildScript -WorkDir $work -SkipWsl -SkipPrereqs
        exit $LASTEXITCODE
    }

    if ($Action -eq 'FlashUSB') {
        Write-Host "[*] Action: FlashUSB. Staging and launching interactive MiOS-Field installer..." -ForegroundColor Cyan
        # 1. Locate source folder
        $sourceRoot = Ensure-MiosBootstrapRepo
        $srcDir = Join-Path $sourceRoot "field"
        if (-not (Test-Path $srcDir)) {
            Write-Error "MiOS-Field (field) folder not found after fetch -- check network / GitHub access."
            exit 1
        }
        # 2. Resolve staging directory
        $v = Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.SizeRemaining -gt 25GB } | Sort-Object SizeRemaining -Descending | Select-Object -First 1
        $stageDir = if ($v) { Join-Path "$($v.DriveLetter):\" "MiOS\field_stage" } else { Join-Path $env:TEMP "field_stage" }
        $targetDir = Join-Path $stageDir "field"
        Write-Host "    Staging directory: $targetDir" -ForegroundColor Cyan

        # Preserve the repository layout required by the shared field backend.
        # Stage only runtime source paths; never copy Git metadata or scratch files.
        New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
        foreach ($relativePath in @('field', 'installation', 'automation', 'src', 'etc', 'usr', 'var',
                'Get-MiOS.ps1', 'bootstrap.ps1', 'bootstrap.sh', 'build-mios.ps1', 'build-mios.sh',
                'install.ps1', 'install.sh', 'seed-merge.ps1', 'seed-merge.sh', 'mios.toml',
                'VERSION', 'system-prompt.md')) {
            $sourcePath = Join-Path $sourceRoot $relativePath
            if (Test-Path -LiteralPath $sourcePath) {
                Copy-Item -LiteralPath $sourcePath -Destination $stageDir -Recurse -Force -ErrorAction Stop
            }
        }

        $fieldScript = Join-Path $targetDir "MiOS-Field.bat"
        foreach ($requiredPath in @($fieldScript, (Join-Path $stageDir 'installation\mios-common.ps1'),
                (Join-Path $stageDir 'installation\MiOS-Field.bat'))) {
            if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
                throw "MiOS-Field runtime file was not staged: $requiredPath"
            }
        }
        Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList "/c start `"MiOS-Field`" cmd.exe /k `"$fieldScript`" flash"
        Write-Host "[+] Interactive MiOS-Field launcher spawned from staged directory." -ForegroundColor Green
        exit 0
    }

    if ($Action -eq 'OfflineSync') {
        Write-Host "[*] Action: OfflineSync. Staging repositories to USB..." -ForegroundColor Cyan
        $drive = 'D'
        $toml = 'C:\mios-bootstrap\mios.toml'
        if (-not (Test-Path $toml)) { $toml = 'C:\MiOS\usr\share\mios\mios.toml' }
        if (-not (Test-Path $toml)) { $toml = 'C:\MiOS\mios.toml' }
        if (Test-Path $toml) {
            $drive = (Get-Content $toml | Select-String -Pattern '^\s*drivepath\s*=\s*\"(.*)\"' | ForEach-Object { $_.Matches.Groups[1].Value })
            if (-not $drive) { $drive = 'D' }
        }
        Write-Host "[*] Target USB Drive: $($drive):" -ForegroundColor Cyan
        New-Item -ItemType Directory -Force -Path "$($drive):\repos\mios-bootstrap" | Out-Null
        New-Item -ItemType Directory -Force -Path "$($drive):\repos\MiOS" | Out-Null
        robocopy "C:\mios-bootstrap" "$($drive):\repos\mios-bootstrap" /E /XD .git .npm node_modules build cache isobuild isobuild2 /R:2 /W:2 | Out-Null
        robocopy "C:\MiOS" "$($drive):\repos\MiOS" /E /XD .git .npm node_modules build cache isobuild isobuild2 /R:2 /W:2 | Out-Null
        Write-Host "[+] Sync completed successfully." -ForegroundColor Green
        exit 0
    }

    if ($Action -eq 'Install') {
        # The web door hands into the ONE guided surface: fetch the repo, then run
        # installation\mios-install.ps1 (the SSOT-themed menu that explains every
        # target and dispatches to the right local entrypoint). This replaces
        # "re-implement each launch here" with "hand into the single installer".
        Write-Host "[*] Action: Install. Fetching the repo and opening the guided MiOS installer..." -ForegroundColor Cyan
        $installer = Join-Path (Ensure-MiosBootstrapRepo) "installation\mios-install.ps1"
        if (-not (Test-Path $installer)) {
            Write-Error "installation\mios-install.ps1 not found after fetch -- check network / GitHub access."
            exit 1
        }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        exit $LASTEXITCODE
    }

    if ($Action -eq 'Configure') {
        # Same door, straight to the one SSOT editor: mios-install's `configure`
        # target opens the MiOS Portal / configurator (:8640/configure, else the
        # MiOS-DEV launcher, else the offline HTML).
        Write-Host "[*] Action: Configure. Fetching the repo and opening the MiOS configurator (Portal)..." -ForegroundColor Cyan
        $installer = Join-Path (Ensure-MiosBootstrapRepo) "installation\mios-install.ps1"
        if (-not (Test-Path $installer)) {
            Write-Error "installation\mios-install.ps1 not found after fetch -- check network / GitHub access."
            exit 1
        }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer configure
        exit $LASTEXITCODE
    }
}

try {
    $_curW = [Console]::WindowWidth
    if ($_curW -gt 80) {
        # Shrink window first, then buffer.
        [Console]::SetWindowSize(80, 40)
        [Console]::SetBufferSize(80, 9000)
    } else {
        # Enlarge buffer first, then window.
        [Console]::SetBufferSize(80, 9000)
        [Console]::SetWindowSize(80, 40)
    }
} catch {}
# Center on cursor's active monitor via Win32 MoveWindow. Wrap each
# Add-Type separately so a "type already defined" exception on a
# re-entry doesn't skip the MoveWindow call.
try { Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue } catch {}
if (-not ('MiOSWinR.N' -as [type])) {
    try {
        Add-Type -Namespace MiOSWinR -Name N -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool MoveWindow(System.IntPtr hWnd, int x, int y, int w, int h, bool repaint);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@ -ErrorAction Stop
    } catch {}
}
try { [MiOSWinR.N]::SetProcessDPIAware() | Out-Null } catch {}
try {
    # Geist Mono 12pt @ 100% DPI: cell 10x20 px, chrome 20x12 px.
    # 80x40 cells -> 820 x 812 px outer rect.
    $_winWPx = 820
    $_winHPx = 812
    $_cur    = [System.Windows.Forms.Cursor]::Position
    $_work   = [System.Windows.Forms.Screen]::FromPoint($_cur).WorkingArea
    $_x      = $_work.X + [int](([math]::Max(0, $_work.Width  - $_winWPx)) / 2)
    $_y      = $_work.Y + [int](([math]::Max(0, $_work.Height - $_winHPx)) / 2)
    $_hwnd   = [MiOSWinR.N]::GetConsoleWindow()
    if ($_hwnd -ne [IntPtr]::Zero) {
        [MiOSWinR.N]::MoveWindow($_hwnd, $_x, $_y, $_winWPx, $_winHPx, $true) | Out-Null
    }
} catch {}

try {
    $_legacyProfile = Join-Path $env:USERPROFILE 'MiOS-bootstrap'
    if (Test-Path -LiteralPath $_legacyProfile) {
        Remove-Item -LiteralPath $_legacyProfile -Recurse -Force -ErrorAction SilentlyContinue
        # Also rewrite the redirector to point at the M:\ canonical
        # location so the NEXT pwsh launch loads a clean profile (or
        # no-ops via the redirector's `if (Test-Path)` guard if Pass-1
        # hasn't yet staged the M:\ copy on this run).
        $_profilePath = $PROFILE.CurrentUserAllHosts
        if (-not $_profilePath) { $_profilePath = $PROFILE }
        if ($_profilePath -and (Test-Path -LiteralPath $_profilePath)) {
            try {
                $_existing = Get-Content -LiteralPath $_profilePath -Raw -ErrorAction SilentlyContinue
                $_marker  = '# >>> MiOS oh-my-posh init >>>'
                $_endMark = '# <<< MiOS oh-my-posh init <<<'
                if ($_existing -match [regex]::Escape($_marker)) {
                    $_pattern = "(?s)$([regex]::Escape($_marker)).*?$([regex]::Escape($_endMark))"
                    $_cleaned = [regex]::Replace($_existing, $_pattern, '').TrimEnd()
                    Set-Content -LiteralPath $_profilePath -Value $_cleaned -Encoding UTF8 -NoNewline
                }
            } catch {}
        }
    }
} catch {}

if (-not $env:MIOS_CACHE_BUSTED -and -not $env:MIOS_GETMIOS_RELAUNCHED) {
    $env:MIOS_CACHE_BUSTED = '1'
    try {
        $cb = [int][double]::Parse((Get-Date -UFormat %s))
        $bustedUrl = "https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1?cb=$cb"
        $noCacheHdr = @{ 'Cache-Control' = 'no-cache, no-store, max-age=0'; 'Pragma' = 'no-cache' }
        $freshSrc = Invoke-RestMethod -Uri $bustedUrl -Headers $noCacheHdr -ErrorAction Stop
        if ($freshSrc -and $freshSrc.Length -gt 1000) {
            # Got a real script back -- relaunch with the fresh copy.
            & ([scriptblock]::Create($freshSrc)) -RepoUrl "$RepoUrl" -Branch "$Branch" -RepoDir "$RepoDir" -Workflow "$Workflow" $(if ($FullBuild) { '-FullBuild' }) $(if ($Unattended) { '-Unattended' })
            return
        }
        # Empty / suspiciously small response -- fall through to the
        # cached copy we already have running.
    } catch {
        # Network blip / DNS / Fastly outage -- fall through to the
        # cached copy. Better to run something stale than nothing at all.
    }
}

$installModuleDir = Join-Path $PSScriptRoot 'automation\lib\MiOS.Install'
if (Test-Path $installModuleDir) {
    Get-ChildItem -Path $installModuleDir -Filter '*.psm1' -ErrorAction SilentlyContinue | ForEach-Object {
        Import-Module $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

$script:_MiosTomlCache = @{}

function Resolve-MiosTomlText {
    if ($script:_MiosTomlCache.ContainsKey('_text')) {
        return $script:_MiosTomlCache['_text']
    }
    # Only a saved operator file is a host layer. The bootstrap checkout's
    # mios.toml is a template and must never shadow the full system SSOT.
    $path = Join-Path $env:USERPROFILE '.config\mios\mios.toml'
    if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
        try {
            $script:_MiosTomlCache['_text'] = [IO.File]::ReadAllText($path, (New-Object System.Text.UTF8Encoding($false)))
            $script:_MiosTomlCache['_source'] = $path
            return $script:_MiosTomlCache['_text']
        } catch {}
    }
    $script:_MiosTomlCache['_text'] = ''
    $script:_MiosTomlCache['_source'] = '(no operator override)'
    return ''
}

function Resolve-MiosVendorTomlText {
    if ($script:_MiosTomlCache.ContainsKey('_vendor_text')) { return $script:_MiosTomlCache['_vendor_text'] }
    # Phase 0 discards local install state; fetch the complete system SSOT
    # from the current repository ref used by this fresh bootstrap run.
    try {
        $cb = [int][double]::Parse((Get-Date -UFormat %s))
        $rawBase = if ($Script:MiosRawBase) { $Script:MiosRawBase } else { 'https://raw.githubusercontent.com/mios-dev/MiOS/main' }
        $url = "$rawBase/usr/share/mios/mios.toml?cb=$cb"
        $resp = Invoke-WebRequest -Uri $url `
            -Headers @{ 'Cache-Control'='no-cache, no-store, max-age=0'; 'Pragma'='no-cache' } `
            -UseBasicParsing -ErrorAction Stop
        if ($resp.Content -is [byte[]]) { $script:_MiosTomlCache['_vendor_text'] = [System.Text.Encoding]::UTF8.GetString($resp.Content) }
        else { $script:_MiosTomlCache['_vendor_text'] = [string]$resp.Content }
        return $script:_MiosTomlCache['_vendor_text']
    } catch {
        $script:_MiosTomlCache['_vendor_text'] = ''
        return ''
    }
}

function Get-MiosTomlValue {
    param(
        [Parameter(Mandatory)] [string]$Section,   # e.g. "terminal" or "bootstrap.host_storage"
        [Parameter(Mandatory)] [string]$Key,       # e.g. "cols"
        [Parameter(Mandatory)] [AllowEmptyString()] $Default  # returned if not found / unparseable
    )
    foreach ($txt in @((Resolve-MiosTomlText), (Resolve-MiosVendorTomlText))) {
    if (-not $txt) { continue }
    # Slice the section body: from `[Section]` (line-anchored) to the next
    # `[other.section]` header or EOF.
    $rxSec = '(?ms)^\[' + [regex]::Escape($Section) + '\][ \t]*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)'
    $mSec  = [regex]::Match($txt, $rxSec)
    if (-not $mSec.Success) { continue }
    $body  = $mSec.Groups['body'].Value
    # Within the body, find `key = value` (TOML allows leading whitespace).
    $rxKey = '(?m)^[ \t]*' + [regex]::Escape($Key) + '[ \t]*=[ \t]*(?<val>.+?)[ \t]*(?:#.*)?$'
    $mKey  = [regex]::Match($body, $rxKey)
    if (-not $mKey.Success) { continue }
    $raw   = $mKey.Groups['val'].Value.Trim()
    # Coerce by Default's type. Strings get unquoted; arrays get split.
    if ($Default -is [int]) {
        $n = 0
        if ([int]::TryParse(($raw -replace '_',''), [ref]$n)) { return $n }
        continue
    }
    if ($Default -is [bool]) {
        if ($raw -match '^(?i)true$')  { return $true }
        if ($raw -match '^(?i)false$') { return $false }
        continue
    }
    if ($Default -is [double] -or $Default -is [single]) {
        $d = 0.0
        if ([double]::TryParse($raw, [ref]$d)) { return $d }
        continue
    }
    if ($Default -is [array]) {
        if ($raw -match '^\[(.*)\]$') {
            $inner = $Matches[1]
            $items = @(
                $inner -split ',' |
                ForEach-Object {
                    $s = $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n")
                    if ($s) { $s }
                }
            )
            # If Default is an int[] try to coerce each item.
            if ($Default.Length -gt 0 -and $Default[0] -is [int]) {
                $coerced = @()
                foreach ($it in $items) {
                    $n = 0
                    if ([int]::TryParse($it, [ref]$n)) { $coerced += $n } else { $coerced = $null; break }
                }
                if ($null -ne $coerced) { return $coerced }
                continue
            }
            return $items
        }
        continue
    }
    if ($raw.Length -ge 2) {
        $first = $raw[0]; $last = $raw[$raw.Length - 1]
        if ($first -eq '"' -and $last -eq '"') {
            $_bs = [string][char]0x01 + 'BS' + [string][char]0x01
            $inner = $raw.Substring(1, $raw.Length - 2)
            $inner = $inner -replace '\\\\', $_bs   # placeholder for literal backslash
            $inner = $inner -replace '\\"', '"'
            $inner = $inner -replace '\\n', "`n"
            $inner = $inner -replace '\\t', "`t"
            $inner = $inner -replace '\\r', "`r"
            $inner = $inner -replace [regex]::Escape($_bs), '\'
            if (-not [string]::IsNullOrWhiteSpace($inner)) { return $inner }
            continue
        }
        if ($first -eq "'" -and $last -eq "'") {
            # Literal string: strip; no unescaping (TOML literal-string semantics).
            $inner = $raw.Substring(1, $raw.Length - 2)
            if (-not [string]::IsNullOrWhiteSpace($inner)) { return $inner }
            continue
        }
    }
    # Bare value, no surrounding quotes -- return as-is.
    if (-not [string]::IsNullOrWhiteSpace($raw)) { return $raw }
    }
    return $Default
}

# The monitor must use the profile named by the operator SSOT. Delay launch
# until the TOML resolver exists so the WT profile is not inferred from a
# stale global default.
Start-MiosBuildMonitor

function ConvertTo-MiosRawBase {
    param([Parameter(Mandatory)][string]$GitUrl, [Parameter(Mandatory)][string]$Ref)
    if ($GitUrl -match '^[A-Za-z]:') {
        return $GitUrl
    }
    $base = $GitUrl -replace '^https://github\.com/', 'https://raw.githubusercontent.com/' -replace '\.git$', ''
    return "$base/$Ref"
}
$Script:MiosRepoUrl      = Get-MiosTomlValue -Section 'bootstrap' -Key 'mios_repo'      -Default 'https://github.com/mios-dev/MiOS.git'
$Script:MiosBootstrapUrl = Get-MiosTomlValue -Section 'bootstrap' -Key 'bootstrap_repo' -Default 'https://github.com/mios-dev/mios-bootstrap.git'
$Script:MiosRef          = Get-MiosTomlValue -Section 'bootstrap' -Key 'mios_ref'       -Default 'main'
$Script:MiosBootstrapRef = Get-MiosTomlValue -Section 'bootstrap' -Key 'bootstrap_ref'  -Default 'main'
$Script:MiosRawBase      = ConvertTo-MiosRawBase $Script:MiosRepoUrl      $Script:MiosRef          # vendor mios.git raw tree base
$Script:MiosBootstrapRaw = ConvertTo-MiosRawBase $Script:MiosBootstrapUrl $Script:MiosBootstrapRef  # bootstrap repo raw tree base
# Release number: VERSION in $LocalRoot (a checkout), else $MiosRawBase/VERSION (irm | iex
# has no local copy), else [meta].mios_version, else 'unknown'. Never a literal.
function Get-MiosReleaseVersion {
    param([string]$LocalRoot = '')
    $v = ''
    foreach ($root in @($LocalRoot, $Script:MiosRawBase)) {
        if ($v -or -not $root) { continue }
        try { $v = if ($root -match '^[A-Za-z]:|^[\\/]') { [IO.File]::ReadAllText((Join-Path $root 'VERSION')) } else { [string](Invoke-WebRequest -Uri "$root/VERSION" -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop).Content } } catch { $v = '' }
        $v = ([string]$v).Trim(); if ($v -notmatch '^v?\d+(\.\d+)+') { $v = '' }
    }
    if (-not $v) { $v = [string](Get-MiosTomlValue -Section 'meta' -Key 'mios_version' -Default '') }
    if (-not $v) { $v = 'unknown' }
    return ($v -replace '^v', '')
}

function Show-MiOSBanner {
    param([string]$Subtitle = '')
    $art = @(
        '      ___                       ___           ___',
        '     /\__\          ___        /\  \         /\  \',
        '    /::|  |        /\  \      /::\  \       /::\  \',
        '   /:|:|  |        \:\  \    /:/\:\  \     /:/\ \  \',
        '  /:/|:|__|__      /::\__\  /:/  \:\  \   _\:\~\ \  \',
        ' /:/ |::::\__\  __/:/\/__/ /:/__/ \:\__\ /\ \:\ \ \__\',
        ' \/__/~~/:/  / /\/:/  /    \:\  \ /:/  / \:\ \:\ \/__/',
        '       /:/  /  \::/__/      \:\  /:/  /   \:\ \:\__\',
        '      /:/  /    \:\__\       \:\/:/  /     \:\/:/  /',
        '     /:/  /      \/__/        \::/  /       \::/  /',
        '     \/__/                     \/__/         \/__/'
    )
    $sub = if ($Subtitle) { $Subtitle } else { Get-MiosTomlValue -Section 'branding' -Key 'tagline_app' -Default (Get-MiosTomlValue -Section 'branding' -Key 'tagline' -Default 'My Personal Operating System') }
    $_bCols      = Get-MiosTomlValue -Section 'terminal.install' -Key 'cols'         -Default (Get-MiosTomlValue -Section 'terminal' -Key 'cols' -Default 80)
    $_bRightMgn  = Get-MiosTomlValue -Section 'terminal'         -Key 'right_margin' -Default 0
    $inner = [math]::Max(20, $_bCols - $_bRightMgn - 2)
    # Block-center: pad every art line by the SAME left-pad so internal
    # diagonal alignment is preserved.
    $maxArt = ($art | Measure-Object -Property Length -Maximum).Maximum
    $blockL = ' ' * [math]::Max(0, [math]::Floor(($inner - $maxArt) / 2))
    # Subtitle centered on its own (different width than the art block).
    $subPad = [math]::Max(0, $inner - $sub.Length)
    $subL = ' ' * [math]::Floor($subPad / 2)
    $subR = ' ' * ($subPad - [math]::Floor($subPad / 2))
    $_hbar  = ([char]0x2500).ToString() * $inner
    $top    = [char]0x256d + $_hbar + [char]0x256e
    $bottom = [char]0x2570 + $_hbar + [char]0x256f
    $rows = @($top)
    foreach ($a in $art) {
        $line = $blockL + $a
        # Right-pad to fill inner width.
        $line = $line + (' ' * [math]::Max(0, $inner - $line.Length))
        $rows += [char]0x2502 + $line + [char]0x2502
    }
    $rows += [char]0x2502 + $subL + $sub + $subR + [char]0x2502
    $rows += $bottom
    $rows -join "`n"
}

function Show-MiOSAgreement {
    $banner = Show-MiOSBanner -Subtitle 'Project Acknowledgement'
    @"
$banner
The full document lives at AGREEMENTS.md (in the mios-bootstrap repo,
fetched in step 5 below). The summary you are reading is the abridged
operator-facing extract -- it is enough to make an informed accept-or-
decline decision before any code runs.

--------------------------------------------------------------------------------
1. WHAT MiOS IS
--------------------------------------------------------------------------------

MiOS (pronounced "MyOS") is a research-grade, single-user-oriented
Linux operating system delivered as an OCI bootc image. It is NOT a
commercial product, NOT a hardened distribution backed by a vendor
SLA, and NOT an audited reference platform. Treat every script,
postcheck, and architectural claim as an artifact under ongoing
review -- correct in the cases that have been exercised, likely to
need adjustment in cases that have not.

--------------------------------------------------------------------------------
2. LICENSING
--------------------------------------------------------------------------------

* MiOS-owned source is Apache-2.0 (LICENSE)
* Bundled vendor components retain their upstream licenses (LICENSES.md)
* Attribution to every upstream project is recorded in usr/share/doc/mios/reference/credits.md

--------------------------------------------------------------------------------
3. THIRD-PARTY AGREEMENTS THAT APPLY IMPLICITLY
--------------------------------------------------------------------------------

  * NVIDIA proprietary GPU drivers + CUDA -- NVIDIA Software License
  * Steam (Flatpak) -- Steam Subscriber Agreement on first launch
  * Microsoft Windows VM guests (libvirt/QEMU) -- bring your own license
  * Flathub apps installed via mios.toml [desktop].flatpaks -- each carries
    its own license
  * Sigstore-signed images (opt-in via bootc switch --enforce-container-
    sigpolicy) -- accept the transparency-log + Fulcio identity model

These are NOT MiOS-specific terms. They are the upstream vendor terms
MiOS surfaces at install time.

--------------------------------------------------------------------------------
4. DATA AND NETWORK POSTURE
--------------------------------------------------------------------------------

* No telemetry. There is no built-in telemetry channel in the image.
* Outbound network calls from a default deployment are limited to:
    - Fedora / RPMFusion / Flathub mirrors during build / bootc upgrade
    - GitHub Container Registry (ghcr.io) during image fetch
    - User-chosen Quadlet workloads (Forgejo, LocalAI, Ollama, Guacamole,...)
    - The local AI runtime at MIOS_AI_ENDPOINT (default localhost)
* Operators can audit by inspecting /etc/containers/systemd/,
  /usr/lib/systemd/system/, and the active firewalld policy.
* MiOS does not exfiltrate any user data to a vendor cloud.

--------------------------------------------------------------------------------
5. NO WARRANTY
--------------------------------------------------------------------------------

Apache-2.0 'AS IS' clause governs MiOS-owned source. CI covers the
build pipeline, image lint, and postcheck invariants -- NOT full
hardware matrix testing, multi-host upgrade drills, long-running
stability, or production failure modes.

--------------------------------------------------------------------------------
6. TRADEMARKS
--------------------------------------------------------------------------------

Third-party trademarks (Fedora, Universal Blue, NVIDIA, OpenAI,
Anthropic, Google, GitHub, Microsoft, Cline, Cursor, ...) belong to
their respective owners. MiOS references them solely to identify the
upstream component or specification each is part of.

--------------------------------------------------------------------------------
7. YOUR CHOICE
--------------------------------------------------------------------------------

Acknowledged  -- proceed. Get-MiOS.ps1 will elevate, clone the
                 mios-bootstrap repo, and hand off to bootstrap.ps1.
No thanks     -- exit 78 (EX_CONFIG). Nothing modified, nothing pulled.

For unattended / CI invocation, set
  `$env:MIOS_AGREEMENT_ACK = 'accepted'`
in the host environment to bypass this prompt as declared policy.
"@
}

function Invoke-MiOSAgreementGate {
    if ($Unattended) { return $true }
    # Skip-paths in priority order.
    $quietValues   = @('quiet','silent','off','0','false','FALSE')
    $acceptValues  = @('accepted','ACCEPTED','yes','YES','y','1','true','TRUE')
    if ($env:MIOS_AGREEMENT_BANNER -and $quietValues -contains $env:MIOS_AGREEMENT_BANNER) { return $true }
    if ($env:MIOS_AGREEMENT_ACK -and $acceptValues -contains $env:MIOS_AGREEMENT_ACK) {
        [Console]::Error.WriteLine('[mios] AGREEMENTS.md acknowledged via MIOS_AGREEMENT_ACK; proceeding.')
        return $true
    }
    if ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive) {
        [Console]::Error.WriteLine('[mios] Non-interactive or redirected input detected; auto-acknowledging AGREEMENTS.md.')
        $env:MIOS_AGREEMENT_ACK = 'accepted'
        return $true
    }
    try { & chcp.com 65001 *> $null } catch {}
    try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
    try {
        $_curW = [Console]::WindowWidth
        if ($_curW -gt 80) {
            [Console]::SetWindowSize(80, 40)
            [Console]::SetBufferSize(80, 9000)
        } else {
            [Console]::SetBufferSize(80, 9000)
            [Console]::SetWindowSize(80, 40)
        }
    } catch {}

    if (-not ('MiOSGate.W' -as [type])) {
        try {
            Add-Type -Namespace MiOSGate -Name W -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool MoveWindow(System.IntPtr hWnd, int x, int y, int w, int h, bool repaint);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out System.Drawing.Rectangle rect);
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(System.IntPtr hWnd, System.IntPtr hWndAfter, int X, int Y, int cx, int cy, uint uFlags);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetAncestor(System.IntPtr hWnd, uint flags);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr value);
'@ -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
        } catch {}
    }
    try { Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue } catch {}
    # Per-monitor v2 DPI awareness so SetWindowPos coords match Screen.
    # WorkingArea on multi-monitor + high-DPI setups.
    try { [void][MiOSGate.W]::SetProcessDpiAwarenessContext([IntPtr]::new(-4)) } catch {}
    $_gateScreen   = $null
    $_gateTargetX  = $null
    $_gateTargetY  = $null
    $_gateTargetW  = $null
    $_gateTargetH  = $null
    $_gateTargetHwnd = [IntPtr]::Zero
    try {
        # Let conhost settle after the 80x40 SetWindowSize above.
        Start-Sleep -Milliseconds 100
        if ('MiOSGate.W' -as [type]) {
            $_consoleHwnd = [MiOSGate.W]::GetConsoleWindow()
            # GA_ROOT = 2 -- topmost ancestor.
            $_gateTargetHwnd = if ($_consoleHwnd -ne [IntPtr]::Zero) {
                $_root = [MiOSGate.W]::GetAncestor($_consoleHwnd, 2)
                if ($_root -ne [IntPtr]::Zero) { $_root } else { $_consoleHwnd }
            } else { [IntPtr]::Zero }
            if ($_gateTargetHwnd -ne [IntPtr]::Zero) {
                $_gr = New-Object System.Drawing.Rectangle
                [void][MiOSGate.W]::GetWindowRect($_gateTargetHwnd, [ref]$_gr)
                $_gateTargetW = $_gr.Width  - $_gr.X
                $_gateTargetH = $_gr.Height - $_gr.Y
                # Anchor to the window's OWN monitor (where Pass-2 placed
                # it), NOT Cursor.Position. Cursor drift between mouse
                # moves was making multi-monitor recenters jump displays.
                $_gateCenter = New-Object System.Drawing.Point ($_gr.X + [int]($_gateTargetW / 2)), ($_gr.Y + [int]($_gateTargetH / 2))
                $_gateScreen = [System.Windows.Forms.Screen]::FromPoint($_gateCenter).WorkingArea
                $_gateTargetX = $_gateScreen.X + [int](([math]::Max(0, $_gateScreen.Width  - $_gateTargetW)) / 2)
                $_gateTargetY = $_gateScreen.Y + [int](([math]::Max(0, $_gateScreen.Height - $_gateTargetH)) / 2)
            }
        }
    } catch {}
    function _Center-MiOSGateConsole {
        if (-not ('MiOSGate.W' -as [type])) { return }
        if ($null -eq $_gateTargetX -or $_gateTargetHwnd -eq [IntPtr]::Zero) { return }
        try {
            # SWP_NOZORDER (0x4) + SWP_NOACTIVATE (0x10) = 0x14
            [void][MiOSGate.W]::SetWindowPos($_gateTargetHwnd, [IntPtr]::Zero, $_gateTargetX, $_gateTargetY, $_gateTargetW, $_gateTargetH, 0x14)
        } catch {}
    }

    $banner    = Show-MiOSBanner -Subtitle 'Project Acknowledgement'
    $bannerRows = ($banner -split "`n").Count
    # Strip the leading framed banner from Show-MiOSAgreement output --
    # we'll prepend our own per-page so each page starts with it.
    $body = Show-MiOSAgreement
    $bodyLines = $body -split "`r?`n"
    # Drop the banner block at the top (lines until the closing +...+).
    $strip = 0
    for ($i = 0; $i -lt $bodyLines.Count; $i++) {
        if ($bodyLines[$i].StartsWith([char]0x2570)) { $strip = $i + 1; break }
    }
    $contentLines = @($bodyLines | Select-Object -Skip $strip)
    # Trim trailing empty lines so the last page doesn't waste rows.
    while ($contentLines.Count -gt 0 -and $contentLines[-1] -match '^\s*$') {
        $contentLines = $contentLines[0..($contentLines.Count - 2)]
    }

    $winRows    = try { [Console]::WindowHeight } catch { 40 }
    if ($winRows -lt 30) { $winRows = 30 }   # safety floor
    $promptRows = 3                          # blank + 2-line prompt
    $perPage    = [math]::Max(8, $winRows - $bannerRows - $promptRows)

    # Slice content into page-sized chunks, breaking at section dividers
    # when possible so a section's title doesn't get orphaned at the
    # bottom of one page with its body on the next.
    $pages = @()
    $start = 0
    while ($start -lt $contentLines.Count) {
        $end = [math]::Min($start + $perPage - 1, $contentLines.Count - 1)
        # If we're not at the end of the content, prefer a divider line
        # (^-{8,}$) as the cut point so a section header isn't orphaned.
        if ($end -lt ($contentLines.Count - 1)) {
            for ($k = $end; $k -ge $start + [math]::Max(8, $perPage - 12); $k--) {
                if ($contentLines[$k] -match '^-{8,}$') {
                    # Cut just BEFORE the divider so the next page starts
                    # with the divider+title+divider block intact.
                    $end = $k - 1
                    break
                }
            }
        }
        $pages += ,($contentLines[$start..$end])
        $start = $end + 1
    }

    for ($p = 0; $p -lt $pages.Count; $p++) {
        $isLast = ($p -eq $pages.Count - 1)
        $pageNum = $p + 1
        $subt = "Project Acknowledgement (page $pageNum of $($pages.Count))"
        try { Clear-Host } catch {}
        _Center-MiOSGateConsole
        Write-Host (Show-MiOSBanner -Subtitle $subt)
        Write-Host (($pages[$p]) -join "`n")
        Write-Host ''
        if (-not $isLast) {
            if (-not ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive)) { Read-Host '[mios] Press Enter for page 1 of 0' | Out-Null }
        }
    }

    # Prompt loop.
    while ($true) {
        $reply = Read-Host -Prompt "`n[mios] Type 'Acknowledged' to proceed, or 'No thanks' to abort"
        if ($null -eq $reply -or [string]::IsNullOrWhiteSpace($reply)) {
            [Console]::Error.WriteLine('[mios] Non-interactive input detected; auto-acknowledging AGREEMENTS.md.')
            $env:MIOS_AGREEMENT_ACK = 'accepted'
            return $true
        }
        switch -Regex ($reply) {
            '^(Acknowledged|acknowledged|ACKNOWLEDGED|accept|ACCEPT|y|Y|yes|YES)$' {
                [Console]::Error.WriteLine("[mios] AGREEMENTS.md acknowledged; proceeding.")
                $env:MIOS_AGREEMENT_ACK = 'accepted'
                return $true
            }
            '^(No\s+thanks|no\s+thanks|NO\s+THANKS|n|N|no|NO|decline|DECLINE|q|Q|quit|QUIT)$' {
                [Console]::Error.WriteLine('[mios] not acknowledged; aborting (no system changes made).')
                exit 78
            }
            default {
                [Console]::Error.WriteLine("[mios] Please type exactly 'Acknowledged' or 'No thanks'.")
            }
        }
    }
}
$_isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $_isAdmin -and -not $env:MIOS_GETMIOS_RELAUNCHED) {
    Write-Host ''
    $_eAdmin = Get-MiosTomlValue -Section 'messages.elevation' -Key 'admin_required'   -Default '  [*] MiOS bootstrap requires admin (M:\ partition + Podman + dev VM).'
    $_eUac1  = Get-MiosTomlValue -Section 'messages.elevation' -Key 'uac_trigger_line' -Default '  [*] Triggering UAC -- accept to continue. The install will then run'
    $_eUac2  = Get-MiosTomlValue -Section 'messages.elevation' -Key 'uac_trigger_hint' -Default '      in the elevated window, single prompt only.'
    Write-Host $_eAdmin -ForegroundColor Cyan
    Write-Host $_eUac1  -ForegroundColor Cyan
    Write-Host $_eUac2  -ForegroundColor DarkGray
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $_cursorPre = try { [System.Windows.Forms.Cursor]::Position } catch { New-Object System.Drawing.Point 100,100 }
    $_curX = $_cursorPre.X
    $_curY = $_cursorPre.Y
    $_elevCols = Get-MiosTomlValue -Section 'terminal.install' -Key 'cols'            -Default 80
    $_elevRows = Get-MiosTomlValue -Section 'terminal.install' -Key 'rows'            -Default 40
    $_elevScr  = Get-MiosTomlValue -Section 'terminal'         -Key 'scrollback_rows' -Default 9000
    $_cellW    = Get-MiosTomlValue -Section 'theme.font'       -Key 'cell_w_px'       -Default 10
    $_cellH    = Get-MiosTomlValue -Section 'theme.font'       -Key 'cell_h_px'       -Default 20
    $_chromeW  = Get-MiosTomlValue -Section 'theme.font'       -Key 'chrome_w_px'     -Default 20
    $_chromeH  = Get-MiosTomlValue -Section 'theme.font'       -Key 'chrome_h_px'     -Default 12
    # Pixel target for the BOOTSTRAP window (80x40 cells).
    $_winWPx   = ($_elevCols * $_cellW) + $_chromeW
    $_winHPx   = ($_elevRows * $_cellH) + $_chromeH
    $_appCols  = Get-MiosTomlValue -Section 'terminal'         -Key 'cols'            -Default 80
    $_appRows  = Get-MiosTomlValue -Section 'terminal'         -Key 'rows'            -Default 20
    $_appWPx   = ($_appCols * $_cellW) + $_chromeW
    $_appHPx   = ($_appRows * $_cellH) + $_chromeH
    $_rawUrl = "$($Script:MiosBootstrapRaw)/Get-MiOS.ps1?cb=$([int][double]::Parse((Get-Date -UFormat %s)))"
    $_p2ExitedPrefix     = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'exited_code_prefix'   -Default '  [!] Bootstrap exited with code ') -replace "'", "''"
    $_p2FailureDtl       = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'failure_detail'       -Default '      Output above is the failure detail (Pass-1 has no separate log).') -replace "'", "''"
    $_p2BuildLogHnt      = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'build_log_hint'       -Default "      build-mios.ps1's own log at M:\MiOS\logs\mios-install-*.log only kicks in on Pass-2 success.") -replace "'", "''"
    $_p2FetchFailed      = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'fetch_run_failed'     -Default '  [!] Bootstrap fetch/run failed: ') -replace "'", "''"
    $_p2PressEnter       = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'press_enter_close'    -Default '  Press Enter to close this elevated bootstrap window...') -replace "'", "''"
    $_p2SuccessTrans     = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'success_transition'   -Default '  [+] Bootstrap complete. Loading MiOS terminal in this window...') -replace "'", "''"
    $_p2ProfileFailed    = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'profile_load_failed'  -Default '  [!] MiOS profile load failed: ') -replace "'", "''"
    $_p2ProfileFailedHnt = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'profile_load_hint'    -Default '      Open a fresh MiOS shortcut to retry.') -replace "'", "''"
    $_p2ProfileMissing   = (Get-MiosTomlValue -Section 'messages.pass2_exit' -Key 'profile_missing'      -Default '  [!] M:\MiOS\powershell\profile.ps1 not found -- open the MiOS shortcut to launch the app window.') -replace "'", "''"
    $_innerCmd = @"
`$env:MIOS_GETMIOS_RELAUNCHED='1'
`$env:MIOS_CACHE_BUSTED='1'
`$env:TERM_PROGRAM='mios'
try { & chcp.com 65001 *> `$null } catch {}
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(`$false) } catch {}
try { [Console]::InputEncoding  = [System.Text.UTF8Encoding]::new(`$false) } catch {}
try { `$OutputEncoding = [System.Text.UTF8Encoding]::new(`$false) } catch {}
try {
    `$_p2LogDir = if (Test-Path 'M:\') { 'M:\MiOS\logs' } else { Join-Path `$env:TEMP 'mios-logs' }
    if (-not (Test-Path `$_p2LogDir)) { New-Item -ItemType Directory -Force -Path `$_p2LogDir | Out-Null }
    `$_p2Log = Join-Path `$_p2LogDir ('mios-pass2-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Start-Transcript -LiteralPath `$_p2Log -Force *> `$null
    Write-Host ('      Pass-2 log: ' + `$_p2Log) -ForegroundColor DarkGray
} catch {}
`$_curXPre = $_curX
`$_curYPre = $_curY
try {
    Add-Type -Namespace MEW -Name N -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool MoveWindow(System.IntPtr hWnd, int x, int y, int w, int h, bool repaint);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out System.Drawing.Rectangle rect);
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(System.IntPtr hWnd, System.IntPtr hWndAfter, int X, int Y, int cx, int cy, uint uFlags);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetAncestor(System.IntPtr hWnd, uint flags);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr value);
'@ -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    try { [void][MEW.N]::SetProcessDpiAwarenessContext([IntPtr]::new(-4)) } catch {}
    `$_curW = [Console]::WindowWidth
    if (`$_curW -gt $_elevCols) {
        try { [Console]::SetWindowSize($_elevCols, $_elevRows) } catch {}
        try { [Console]::SetBufferSize($_elevCols, $_elevScr) } catch {}
    } else {
        try { [Console]::SetBufferSize($_elevCols, $_elevScr) } catch {}
        try { [Console]::SetWindowSize($_elevCols, $_elevRows) } catch {}
    }
    `$_dbg = if (Test-Path 'M:\MiOS\logs') { Join-Path 'M:\MiOS\logs' 'mios-center-debug.log' } else { Join-Path `$env:TEMP 'mios-center-debug.log' }
    `$_dbgLines = New-Object System.Collections.Generic.List[string]
    `$_dbgLines.Add("[`$([DateTime]::Now.ToString('HH:mm:ss.fff'))] Pass-2 inner cmd center start")
    for (`$_attempt = 0; `$_attempt -lt 8; `$_attempt++) {
        Start-Sleep -Milliseconds 250
        `$_consoleHwnd = [MEW.N]::GetConsoleWindow()
        `$_h = if (`$_consoleHwnd -ne [IntPtr]::Zero) {
            `$_root = [MEW.N]::GetAncestor(`$_consoleHwnd, 2)
            if (`$_root -ne [IntPtr]::Zero) { `$_root } else { `$_consoleHwnd }
        } else { [IntPtr]::Zero }
        if (`$_h -eq [IntPtr]::Zero) { `$_dbgLines.Add(('attempt {0}: no hwnd' -f `$_attempt)); continue }
        `$_rect = New-Object System.Drawing.Rectangle
        `$_grcOk = [MEW.N]::GetWindowRect(`$_h, [ref]`$_rect)
        `$_actualW = `$_rect.Width  - `$_rect.X
        `$_actualH = `$_rect.Height - `$_rect.Y
        if (`$_actualW -le 0 -or `$_actualH -le 0) { `$_dbgLines.Add(('attempt {0}: zero dims grc={1}' -f `$_attempt, `$_grcOk)); continue }
        `$_pt = New-Object System.Drawing.Point `$_curXPre, `$_curYPre
        `$_s  = [System.Windows.Forms.Screen]::FromPoint(`$_pt).WorkingArea
        `$_x = `$_s.X + [int](([math]::Max(0, `$_s.Width  - `$_actualW)) / 2)
        `$_y = `$_s.Y + [int](([math]::Max(0, `$_s.Height - `$_actualH)) / 2)
        # SWP_NOZORDER (0x4) + SWP_NOACTIVATE (0x10) = 0x14
        `$_swp = [MEW.N]::SetWindowPos(`$_h, [IntPtr]::Zero, `$_x, `$_y, `$_actualW, `$_actualH, 0x14)
        `$_dbgLines.Add(('attempt {0}: hwnd=0x{1:X} console=0x{2:X} rect={3},{4} dims={5}x{6} screen={7},{8} {9}x{10} target={11},{12} SWPok={13}' -f `$_attempt, ([int64]`$_h), ([int64]`$_consoleHwnd), `$_rect.X, `$_rect.Y, `$_actualW, `$_actualH, `$_s.X, `$_s.Y, `$_s.Width, `$_s.Height, `$_x, `$_y, `$_swp))
    }
    try { Set-Content -LiteralPath `$_dbg -Value (`$_dbgLines -join [Environment]::NewLine) -Encoding UTF8 } catch {}
} catch {
    try {
        `$_dbgFail = if (Test-Path 'M:\MiOS\logs') { Join-Path 'M:\MiOS\logs' 'mios-center-debug.log' } else { Join-Path `$env:TEMP 'mios-center-debug.log' }
        Add-Content -LiteralPath `$_dbgFail -Value "Pass-2 inner cmd center FAILED: `$(`$_.Exception.Message)"
    } catch {}
}
Write-Host ''
Write-Host '  [*] MiOS Bootstrap (elevated)' -ForegroundColor Cyan
`$_cb     = [int][double]::Parse((Get-Date -UFormat %s))
`$_rawUri = 'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1?cb=' + `$_cb
Write-Host ('      Cache-busted Get-MiOS.ps1 fetch: ' + `$_rawUri) -ForegroundColor DarkGray
Write-Host ''
try {
    # install-robustness retry the fetch 3x with backoff +
    # body validation. A single transient blip here otherwise killed the
    # whole elevated install with a bare Invoke-RestMethod exception.
    `$src = `$null
    for (`$_fa = 1; `$_fa -le 3; `$_fa++) {
        try {
            `$src = Invoke-RestMethod -Uri `$_rawUri -Headers @{ 'Cache-Control' = 'no-cache, no-store, max-age=0'; 'Pragma' = 'no-cache' } -TimeoutSec 60 -ErrorAction Stop
            if (`$src -and `$src.Length -gt 200) { break }
            `$src = `$null
        } catch {
            Write-Host ('  [!] Get-MiOS.ps1 fetch attempt ' + `$_fa + ' failed: ' + `$_.Exception.Message) -ForegroundColor Yellow
        }
        if (`$_fa -lt 3) { Start-Sleep -Seconds (@(2,5,10)[`$_fa-1]) }
    }
    if (-not `$src) { throw 'Could not fetch Get-MiOS.ps1 after 3 attempts (network/TLS?).' }
    `$_rc = 0
    try {
        & ([scriptblock]::Create(`$src)) -RepoUrl "$RepoUrl" -Branch "$Branch" -RepoDir "$RepoDir" -Workflow "$Workflow" $(if ($FullBuild) { '-FullBuild' }) $(if ($Unattended) { '-Unattended' })
    } catch {
        Write-Host ''
        Write-Host ('  [!] In-process bootstrap throw: ' + `$_.Exception.Message) -ForegroundColor Red
        `$_rc = 1
    }
    if (`$_rc -ne 0) {
        Write-Host ''
        # Strings baked at Pass-1 install time from mios.toml
        # [messages.pass2_exit] (SSOT).
        Write-Host ('$_p2ExitedPrefix' + `$_rc) -ForegroundColor Red
        Write-Host '$_p2FailureDtl' -ForegroundColor DarkGray
        Write-Host '$_p2BuildLogHnt' -ForegroundColor DarkGray
        Write-Host ''
    } else {
        Write-Host ''
        Write-Host '$_p2SuccessTrans' -ForegroundColor Green
        Write-Host ''
        Start-Sleep -Milliseconds 800
        try { Clear-Host } catch {}
        `$_miosProfile = 'M:\MiOS\powershell\profile.ps1'
        if (Test-Path -LiteralPath `$_miosProfile) {
            try { . `$_miosProfile } catch {
                Write-Host ('$_p2ProfileFailed' + `$_.Exception.Message) -ForegroundColor Yellow
                Write-Host '$_p2ProfileFailedHnt' -ForegroundColor DarkGray
            }
        } else {
            Write-Host '$_p2ProfileMissing' -ForegroundColor Yellow
        }
        return
    }
} catch {
    Write-Host ''
    Write-Host ('$_p2FetchFailed' + `$_.Exception.Message) -ForegroundColor Red
    Write-Host ''
}
# FAILURE path falls through to the press-Enter close so the operator
# has time to read the error before the elevated window closes.
Write-Host ''
Write-Host '$_p2PressEnter' -ForegroundColor DarkGray -NoNewline
try { Stop-Transcript *> `$null } catch {}
`$null = Read-Host
"@
    $_innerScript = Join-Path $env:TEMP ('mios-elev-' + [guid]::NewGuid().Guid.Substring(0,8) + '.ps1')
    # UTF-8 with BOM so pwsh / powershell.exe both parse Unicode glyphs
    # in the dashboard banner correctly.
    $_utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [IO.File]::WriteAllText($_innerScript, $_innerCmd, $_utf8Bom)
    $_shell = $null
    foreach ($_c in @("$env:ProgramFiles\PowerShell\7\pwsh.exe","$env:ProgramW6432\PowerShell\7\pwsh.exe")) {
        if ($_c -and (Test-Path -LiteralPath $_c -PathType Leaf)) { $_shell = $_c; break }
    }
    if (-not $_shell) {
        $_w51 = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
        if (Test-Path -LiteralPath $_w51 -PathType Leaf) { $_shell = $_w51 }
    }
    if (-not $_shell) { $_shell = 'powershell.exe' }
    try {
        Start-Process -FilePath $_shell `
            -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File', $_innerScript) `
            -Verb RunAs -WorkingDirectory $env:WINDIR -ErrorAction Stop
        return
    } catch {
        Write-Host ''
        Write-Host "  [!] Self-elevation failed: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host '      If you saw a UAC prompt, accept it and re-paste the one-liner.' -ForegroundColor DarkGray
        Write-Host '      Or open an elevated PowerShell manually and re-run:' -ForegroundColor DarkGray
        Write-Host "        irm $_rawUrl | iex" -ForegroundColor DarkGray
        Write-Host ''
        Write-Host '  Pass-1 elevation FAILED. Read the error above, then press Enter to close.' -ForegroundColor Yellow -NoNewline
        $null = Read-Host
        return
    }
}

Invoke-MiOSAgreementGate | Out-Null

function Get-MiosPalette {
    function _hex {
        param([string]$Section, [string]$Key, [string]$Fallback)
        $v = (Get-MiosTomlValue -Section $Section -Key $Key -Default $Fallback)
        if (-not $v -or [string]::IsNullOrWhiteSpace($v)) { return $Fallback }
        $v = $v.Trim()
        if ($v -notmatch '^#[0-9A-Fa-f]{3,8}$') { return $Fallback }
        return $v
    }
    @{
        bg                = (_hex 'colors' 'bg'                       '#282262')
        fg                = (_hex 'colors' 'fg'                       '#E7DFD3')
        accent            = (_hex 'colors' 'accent'                   '#1A407F')
        cursor            = (_hex 'colors' 'cursor'                   '#F35C15')
        ansi_0_black      = (_hex 'colors' 'ansi_0_black'             '#282262')
        ansi_1_red        = (_hex 'colors' 'ansi_1_red'               '#DC271B')
        ansi_2_green      = (_hex 'colors' 'ansi_2_green'             '#3E7765')
        ansi_3_yellow     = (_hex 'colors' 'ansi_3_yellow'            '#F35C15')
        ansi_4_blue       = (_hex 'colors' 'ansi_4_blue'              '#1A407F')
        ansi_5_magenta    = (_hex 'colors' 'ansi_5_magenta'           '#734F39')
        ansi_6_cyan       = (_hex 'colors' 'ansi_6_cyan'              '#B7C9D7')
        ansi_7_white      = (_hex 'colors' 'ansi_7_white'             '#E7DFD3')
        ansi_8_brblack    = (_hex 'colors' 'ansi_8_bright_black'      '#948E8E')
        ansi_9_brred      = (_hex 'colors' 'ansi_9_bright_red'        '#FF6B5C')
        ansi_10_brgreen   = (_hex 'colors' 'ansi_10_bright_green'     '#5FAA8E')
        ansi_11_bryellow  = (_hex 'colors' 'ansi_11_bright_yellow'    '#FF8540')
        ansi_12_brblue    = (_hex 'colors' 'ansi_12_bright_blue'      '#3D6BA8')
        ansi_13_brmagenta = (_hex 'colors' 'ansi_13_bright_magenta'   '#9D7660')
        ansi_14_brcyan    = (_hex 'colors' 'ansi_14_bright_cyan'      '#E0E0E0')
        ansi_15_brwhite   = (_hex 'colors' 'ansi_15_bright_white'     '#FFFFFF')
    }
}
$Script:MiosPalette = Get-MiosPalette

# Per-user font-install registry key: the modern non-admin path. Win10
# 1809+ honors HKCU font registrations for the running user; no
# Windows\Fonts\ admin write needed. We probe both "GeistMono Nerd Font
# Mono" and "GeistMono NFM" (the two face names Nerd Fonts has shipped
# under) so a font installed by another tool is reused.
function Test-MiOSFontInstalled {
    param([string]$Family = 'GeistMono Nerd Font Mono')
    try {
        $key = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        if (Test-Path $key) {
            $names = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue |
                      Get-Member -MemberType NoteProperty |
                      Where-Object { $_.Name -notmatch '^PS' }).Name
            foreach ($n in $names) {
                if ($n -match [regex]::Escape($Family) -or $n -match 'GeistMono\s+NFM' -or $n -match 'GeistMono\s+Nerd\s+Font') {
                    return $true
                }
            }
        }
        # Also check the system-wide key (older admin installs / chocolatey).
        $sysKey = 'HKLM:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        if (Test-Path $sysKey) {
            $sysNames = (Get-ItemProperty -Path $sysKey -ErrorAction SilentlyContinue |
                          Get-Member -MemberType NoteProperty |
                          Where-Object { $_.Name -notmatch '^PS' }).Name
            foreach ($n in $sysNames) {
                if ($n -match [regex]::Escape($Family) -or $n -match 'GeistMono\s+NFM' -or $n -match 'GeistMono\s+Nerd\s+Font') {
                    return $true
                }
            }
        }
    } catch {}
    return $false
}

function Wait-MiOSWindowsTerminalReady {
    $deadline = (Get-Date).AddSeconds(90)
    $stableLocal = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState'

    while ((Get-Date) -lt $deadline) {
        $pkg = $null
        try { $pkg = Get-AppxPackage -Name 'Microsoft.WindowsTerminal' -ErrorAction SilentlyContinue } catch {}
        $localOk = Test-Path -LiteralPath $stableLocal
        $exeOk = $false
        if ($pkg -and $pkg.InstallLocation) {
            $wtExe = Join-Path $pkg.InstallLocation 'wt.exe'
            if (Test-Path -LiteralPath $wtExe) { $exeOk = $true }
        }
        if ($pkg -and $exeOk) {
            if (-not $localOk) {
                try { New-Item -ItemType Directory -Path $stableLocal -Force | Out-Null } catch {}
            }
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Ensure-MiOSWinget {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  [+] winget already on PATH." -ForegroundColor DarkGray
        return $true
    }
    try {
        $appx = Get-AppxPackage -Name 'Microsoft.DesktopAppInstaller' -ErrorAction SilentlyContinue
    } catch { $appx = $null }
    if ($appx) {
        $_machPath = [System.Environment]::GetEnvironmentVariable('PATH','Machine')
        $_userPath = [System.Environment]::GetEnvironmentVariable('PATH','User')
        $env:PATH = (@($_machPath, $_userPath) | Where-Object { $_ }) -join ';'
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "  [+] winget surfaced after PATH refresh." -ForegroundColor Green
            return $true
        }
    }
    $_url = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'appinstaller_url' -Default 'https://aka.ms/getwinget')
    Write-Host "  [*] winget missing -- downloading App Installer MSIXBUNDLE from $_url ..." -ForegroundColor Cyan
    $tmp = Join-Path $env:TEMP "mios-winget-$([guid]::NewGuid().ToString('N').Substring(0,8)).msixbundle"
    try {
        Invoke-WebRequest -Uri $_url -OutFile $tmp -UseBasicParsing -ErrorAction Stop
        Add-AppxPackage -Path $tmp -ErrorAction Stop
        $_machPath = [System.Environment]::GetEnvironmentVariable('PATH','Machine')
        $_userPath = [System.Environment]::GetEnvironmentVariable('PATH','User')
        $env:PATH = (@($_machPath, $_userPath) | Where-Object { $_ }) -join ';'
    } catch {
        Write-Host "  [!] Ensure-MiOSWinget download/install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  [+] winget bootstrapped via App Installer MSIXBUNDLE." -ForegroundColor Green
        return $true
    }
    Write-Host "  [!] winget still not on PATH after Add-AppxPackage." -ForegroundColor Yellow
    return $false
}

function Install-MiOSWindowsTerminal {
    $appx = $null
    try { $appx = Get-AppxPackage -Name 'Microsoft.WindowsTerminal' -ErrorAction SilentlyContinue } catch {}
    if ($appx) {
        Write-Host "  [+] Windows Terminal (base install) already present." -ForegroundColor DarkGray
        [void](Wait-MiOSWindowsTerminalReady)
        return $true
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  [!] winget not available; cannot auto-install Windows Terminal." -ForegroundColor Yellow
        Write-Host "      Install manually from the Microsoft Store." -ForegroundColor DarkGray
        return $false
    }
    # TOML-first per AGENTS.md section 3 -- winget ID resolves from
    # mios.toml [bootstrap.prereqs].terminal_pkg so operators can pin to
    # WindowsTerminalPreview or a different distribution channel via mios.html.
    $_wtPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'terminal_pkg' -Default 'Microsoft.WindowsTerminal')
    Write-Host "  [*] Installing Windows Terminal ($_wtPkg) via winget..." -ForegroundColor Cyan
    try {
        & winget install --id $_wtPkg --silent --accept-package-agreements --accept-source-agreements --source winget 2>&1 | Out-Null
    } catch {
        Write-Host "  [!] winget install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [!] winget exit code $LASTEXITCODE -- WT install may not have completed." -ForegroundColor Yellow
        return $false
    }
    Write-Host "  [*] winget install returned -- waiting for AppX deployment to finish..." -ForegroundColor Cyan
    if (-not (Wait-MiOSWindowsTerminalReady)) {
        Write-Host "  [!] Windows Terminal did not become ready within 90s." -ForegroundColor Yellow
        return $false
    }
    Write-Host "  [+] Windows Terminal installed and ready." -ForegroundColor Green
    return $true
}

function Install-MiOSPwsh7 {
    $existing = $null
    foreach ($c in @("$env:ProgramFiles\PowerShell\7\pwsh.exe",
                     "$env:ProgramW6432\PowerShell\7\pwsh.exe")) {
        if ($c -and (Test-Path -LiteralPath $c)) { $existing = $c; break }
    }
    if (-not $existing) {
        try {
            $appx = Get-AppxPackage -Name 'Microsoft.PowerShell' -ErrorAction SilentlyContinue
            if ($appx -and $appx.InstallLocation) {
                $cand = Join-Path $appx.InstallLocation 'pwsh.exe'
                if (Test-Path -LiteralPath $cand) { $existing = $cand }
            }
        } catch {}
    }
    if ($existing) {
        Write-Host "  [+] PowerShell 7 already installed: $existing" -ForegroundColor DarkGray
        return $true
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  [!] winget not available; cannot install PowerShell 7." -ForegroundColor Yellow
        Write-Host "      WT MiOS profile will fall back to Windows PS 5.1 (broken oh-my-posh init likely)." -ForegroundColor DarkGray
        return $false
    }
    $_pwshPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'pwsh_pkg' -Default 'Microsoft.PowerShell')
    Write-Host "  [*] Installing PowerShell 7 ($_pwshPkg) via winget..." -ForegroundColor Cyan
    try {
        & winget install --id $_pwshPkg --silent --accept-package-agreements --accept-source-agreements --source winget 2>&1 | Out-Null
    } catch {
        Write-Host "  [!] winget install $_pwshPkg failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [!] winget exit code $LASTEXITCODE -- pwsh 7 may not be installed." -ForegroundColor Yellow
        return $false
    }
    # Refresh $env:PATH so the caller's Get-Command pwsh / Get-AppxPackage
    # discovery picks up the new binary in this session.
    try {
        $_machPath = [System.Environment]::GetEnvironmentVariable('PATH','Machine')
        $_userPath = [System.Environment]::GetEnvironmentVariable('PATH','User')
        $env:PATH = (@($_machPath, $_userPath) | Where-Object { $_ }) -join ';'
    } catch {}
    Write-Host "  [+] PowerShell 7 installed; PATH refreshed for this session." -ForegroundColor Green
    return $true
}

function Install-MiOSGeistFont {
    if (Test-MiOSFontInstalled) {
        Write-Host "  [+] GeistMono Nerd Font already installed (HKCU/HKLM)." -ForegroundColor DarkGray
        return $true
    }
    Write-Host "  [*] Installing GeistMono Nerd Font (per-user)..." -ForegroundColor Cyan
    $zipUrl  = 'https://github.com/ryanoasis/nerd-fonts/releases/latest/download/GeistMono.zip'
    $tmpDir  = Join-Path $env:TEMP ("mios-geist-" + [guid]::NewGuid().ToString('N').Substring(0,8))
    $zipPath = Join-Path $tmpDir 'GeistMono.zip'
    try {
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing -ErrorAction Stop
        $zipSize = (Get-Item $zipPath).Length
        if ($zipSize -lt 100000) {
            # < 100 KB means we got a 404 HTML page or similar, not the
            # real ~50 MB Geist zip. Bail early with a useful message.
            Write-Host "  [!] GeistMono.zip download too small ($zipSize bytes) -- likely 404." -ForegroundColor Yellow
            Write-Host "      Source: $zipUrl" -ForegroundColor DarkGray
            return $false
        }
        Expand-Archive -Path $zipPath -DestinationPath $tmpDir -Force -ErrorAction Stop
        $extractedCount = (Get-ChildItem $tmpDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Host "  [*] GeistMono.zip: $([math]::Round($zipSize/1MB,1)) MB, $extractedCount files extracted." -ForegroundColor DarkGray

        $miosFontDir = if (Test-Path 'M:\') { 'M:\MiOS\fonts' }
                       else { Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Fonts' }
        if (-not (Test-Path $miosFontDir)) {
            New-Item -ItemType Directory -Path $miosFontDir -Force | Out-Null
        }
        $userFontDir = $miosFontDir
        $regKey = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        if (-not (Test-Path $regKey)) {
            New-Item -Path $regKey -Force | Out-Null
        }

        $allFonts = Get-ChildItem $tmpDir -Recurse -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '\.(ttf|otf)$' }
        # Prefer "Mono" variants (fixed-width, terminal-safe). Then
        # general "NerdFont". Then ANY font face as last resort.
        $preferred = $allFonts | Where-Object { $_.Name -match 'NerdFontMono' }
        if (-not $preferred) {
            $preferred = $allFonts | Where-Object { $_.Name -match 'NerdFont' }
        }
        if (-not $preferred) {
            $preferred = $allFonts
        }
        if (-not $preferred) {
            Write-Host "  [!] GeistMono.zip extracted but contains no .ttf/.otf files. (Found $($allFonts.Count))" -ForegroundColor Yellow
            return $false
        }

        $installed = 0
        foreach ($ttf in $preferred) {
            $dst = Join-Path $userFontDir $ttf.Name
            Copy-Item -LiteralPath $ttf.FullName -Destination $dst -Force
            # Face name for the registry value: derive from filename
            # ("GeistMonoNerdFontMono-Regular.ttf" -> "GeistMono Nerd Font Mono Regular (TrueType|OpenType)").
            # Windows registry keys differ for TTF vs OTF -- TrueType for
            # .ttf, OpenType for .otf -- and Windows' font loader uses the
            # suffix to dispatch to the right rasterizer.
            $face = $ttf.BaseName `
                -replace 'NerdFontMono', ' Nerd Font Mono ' `
                -replace 'NerdFont',     ' Nerd Font ' `
                -replace '-',            ' ' `
                -replace '\s+',          ' '
            $suffix = if ($ttf.Extension -ieq '.otf') { ' (OpenType)' } else { ' (TrueType)' }
            $face = $face.Trim() + $suffix
            New-ItemProperty -Path $regKey -Name $face -Value $dst -PropertyType String -Force | Out-Null
            $installed++
        }
        Write-Host "  [+] Installed $installed Geist Mono Nerd Font face(s) to $userFontDir." -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  [!] Geist Mono Nerd Font install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "      WT will fall back to Cascadia Mono -- glyphs in oh-my-posh will be missing." -ForegroundColor DarkGray
        return $false
    } finally {
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Install-MiOSBibataCursor {
    $schemeName = 'Bibata Modern Classic'
    $cursorsKey = 'HKCU:\Control Panel\Cursors'
    $current = (Get-ItemProperty -Path $cursorsKey -ErrorAction SilentlyContinue).'(default)'
    if (-not $current) { $current = (Get-ItemProperty -Path $cursorsKey -Name '(default)' -ErrorAction SilentlyContinue).'(default)' }
    $installRoot = if (Test-Path 'M:\') { 'M:\MiOS\cursors\Bibata-Modern-Classic' }
                   else { Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\Cursors\Bibata-Modern-Classic' }
    if ($current -eq $schemeName -and (Test-Path -LiteralPath (Join-Path $installRoot 'Default.cur'))) {
        Write-Host "  [+] Bibata cursor already installed + active: $installRoot" -ForegroundColor DarkGray
        return $true
    }

    Write-Host "  [*] Installing Bibata-Modern-Classic Windows cursor..." -ForegroundColor Cyan
    $tmpDir   = Join-Path $env:TEMP ("mios-bibata-" + [guid]::NewGuid().ToString('N').Substring(0,8))
    $zipPath  = Join-Path $tmpDir 'Bibata-Modern-Classic-Windows.zip'
    try {
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

        # Resolve latest release tag from GitHub's API.
        $tag = 'v2.0.7'
        try {
            $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/ful1e5/Bibata_Cursor/releases/latest' `
                       -Headers @{ 'User-Agent' = 'MiOS-Bootstrap' } -ErrorAction Stop
            if ($rel.tag_name) { $tag = $rel.tag_name }
        } catch {}
        $assetUrl = "https://github.com/ful1e5/Bibata_Cursor/releases/download/$tag/Bibata-Modern-Classic-Windows.zip"
        Write-Host "  [*] Bibata $tag : $assetUrl" -ForegroundColor DarkGray

        Invoke-WebRequest -Uri $assetUrl -OutFile $zipPath -UseBasicParsing -ErrorAction Stop
        $zipSize = (Get-Item $zipPath).Length
        if ($zipSize -lt 100000) {
            Write-Host "  [!] Bibata download too small ($zipSize bytes) -- likely 404." -ForegroundColor Yellow
            return $false
        }

        # Extract with Expand-Archive (handles zip natively). Strip
        # the top-level dir Bibata's zip uses ("Bibata-Modern-Classic")
        # so cursor files land directly under $installRoot.
        if (Test-Path -LiteralPath $installRoot) { Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
        $extractTmp = Join-Path $tmpDir 'extract'
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractTmp -Force -ErrorAction Stop
        $extractedRoot = Get-ChildItem -LiteralPath $extractTmp -Directory | Select-Object -First 1
        if ($extractedRoot) {
            Get-ChildItem -LiteralPath $extractedRoot.FullName -File | Move-Item -Destination $installRoot -Force
        } else {
            Get-ChildItem -LiteralPath $extractTmp -File | Move-Item -Destination $installRoot -Force
        }
        $curFiles = @(Get-ChildItem -LiteralPath $installRoot -Recurse -File -Include '*.cur','*.ani' -ErrorAction SilentlyContinue)
        if ($curFiles.Count -lt 10) {
            Write-Host "  [!] Bibata extraction produced only $($curFiles.Count) cursor files (expected 15+)." -ForegroundColor Yellow
            return $false
        }
        Write-Host "  [+] Extracted $($curFiles.Count) Bibata cursors to $installRoot" -ForegroundColor Green

        $cursorMap = [ordered]@{
            'Arrow'         = 'Pointer.cur'
            'Help'          = 'Help.cur'
            'AppStarting'   = 'Work.ani'
            'Wait'          = 'Busy.ani'
            'Crosshair'     = 'Cross.cur'
            'precisionhair' = 'Cross.cur'   # alias in Bibata install.inf
            'IBeam'         = 'Text.cur'
            'NWPen'         = 'Handwriting.cur'
            'No'            = 'Unavailable.cur'
            'SizeNS'        = 'Vert.cur'
            'SizeWE'        = 'Horz.cur'
            'SizeNWSE'      = 'Dgn1.cur'
            'SizeNESW'      = 'Dgn2.cur'
            'Grab'          = 'Move.cur'
            'SizeAll'       = 'Move.cur'
            'UpArrow'       = 'Alternate.cur'
            'Hand'          = 'Link.cur'
            'Pin'           = 'Pin.cur'
            'Person'        = 'Person.cur'
            'Pan'           = 'Pan.cur'
            'Grabbing'      = 'Grabbing.cur'
            'Zoom-in'       = 'Zoom-in.cur'
            'Zoom-out'      = 'Zoom-out.cur'
        }
        # Locate each file (Bibata's zip may extract files into a
        # nested cursors/ dir depending on packaging; walk to find them).
        $byName = @{}
        foreach ($f in $curFiles) { $byName[$f.Name] = $f.FullName }

        if (-not (Test-Path $cursorsKey)) { New-Item -Path $cursorsKey -Force | Out-Null }
        # Build CSV for HKCU\Control Panel\Cursors\Schemes value --
        # 21 comma-separated paths in install.inf's canonical order
        # (pointer, help, work, busy, cross, text, handwriting,
        # unavailable, vert, horz, dgn1, dgn2, move, alternate, link,
        # pin, person, pan, grabbing, zoom-in, zoom-out).
        $schemeFiles = @('Pointer.cur','Help.cur','Work.ani','Busy.ani','Cross.cur',
                         'Text.cur','Handwriting.cur','Unavailable.cur','Vert.cur',
                         'Horz.cur','Dgn1.cur','Dgn2.cur','Move.cur','Alternate.cur',
                         'Link.cur','Pin.cur','Person.cur','Pan.cur','Grabbing.cur',
                         'Zoom-in.cur','Zoom-out.cur')
        $schemePaths = foreach ($f in $schemeFiles) {
            if ($byName.ContainsKey($f)) { $byName[$f] } else { '' }
        }
        $schemeCsv = $schemePaths -join ','

        # Set individual pointer registry values.
        foreach ($k in $cursorMap.Keys) {
            $file = $cursorMap[$k]
            if ($byName.ContainsKey($file)) {
                Set-ItemProperty -Path $cursorsKey -Name $k -Value $byName[$file] -Type ExpandString -Force
            }
        }
        # Active scheme name (Windows reads `(default)` for the display
        # label in Mouse Properties).
        Set-ItemProperty -Path $cursorsKey -Name '(default)' -Value $schemeName -Force

        $_cursorSize = 24
        try { $_cursorSize = [int](Get-MiosTomlValue -Section 'theme.cursor_windows' -Key 'base_size' -Default 24) } catch {}
        if ($_cursorSize -lt 16 -or $_cursorSize -gt 256) { $_cursorSize = 24 }
        Set-ItemProperty -Path $cursorsKey -Name 'CursorBaseSize' -Value $_cursorSize -Type DWord -Force

        # Register the scheme in HKCU\...\Schemes so it appears in the
        # mouse properties dialog dropdown alongside Windows Default.
        $schemesKey = 'HKCU:\Control Panel\Cursors\Schemes'
        if (-not (Test-Path $schemesKey)) { New-Item -Path $schemesKey -Force | Out-Null }
        Set-ItemProperty -Path $schemesKey -Name $schemeName -Value $schemeCsv -Type ExpandString -Force

        # Broadcast SystemParametersInfo so the running desktop reloads
        # cursors immediately (no logoff). 0x57 = SPI_SETCURSORS.
        Add-Type -Namespace MiosWin -Name SPI -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
public static extern bool SystemParametersInfo(uint uiAction, uint uiParam, System.IntPtr pvParam, uint fWinIni);
'@ -ErrorAction SilentlyContinue
        try { [MiosWin.SPI]::SystemParametersInfo(0x0057, 0, [IntPtr]::Zero, 0x03) | Out-Null } catch {}

        Write-Host "  [+] Bibata-Modern-Classic active. Cursor scheme: '$schemeName'." -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  [!] Bibata install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    } finally {
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Get-MiOSTerminalSettingsPath {
    $stableSettings = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
    $stableLocal    = Split-Path -Parent $stableSettings
    if (Test-Path -LiteralPath $stableSettings) { return $stableSettings }
    # LocalState exists but settings.json not yet written (fresh install,
    # WT not yet first-launched). Return the path so we can create it.
    if (Test-Path -LiteralPath $stableLocal) { return $stableSettings }
    return $null
}

# Borderless / no-titlebar / focus-mode launchMode is configured in the
# settings file (root-level "launchMode": "focus") -- passing --focus on
# the wt.exe command line ALONE only hides tabs but keeps the title bar
# unless launchMode is also set in JSON. We set both for belt-and-braces.
function Install-MiOSTerminalProfile {
    # Defensive readiness wait: even if Install-MiOSWindowsTerminal
    # already waited, AppX deployment can still be propagating user-state
    # paths. Re-wait so we never write to a non-existent LocalState dir.
    [void](Wait-MiOSWindowsTerminalReady)
    $settingsPath = Get-MiOSTerminalSettingsPath
    if (-not $settingsPath) {
        Write-Host "  [!] Windows Terminal not ready (LocalState dir missing) -- skipping settings patch." -ForegroundColor Yellow
        Write-Host "      Re-run irm|iex after WT first-launch creates the dir." -ForegroundColor DarkGray
        return $null
    }
    Write-Host "  [*] Patching Windows Terminal settings: $settingsPath" -ForegroundColor Cyan

    $miosGuid = '{a8b5c2d3-e4f5-6789-abcd-ef0123456789}'

    $palette = Get-MiosPalette
    function _miosSchemeColor {
        param($Value, [string]$Fallback)
        if (-not $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $Fallback }
        $v = ([string]$Value).Trim()
        if ($v -notmatch '^#[0-9A-Fa-f]{3,8}$') { return $Fallback }
        return $v
    }
    $miosScheme = [ordered]@{
        name                = 'MiOS'
        background          = (_miosSchemeColor $palette.bg                '#282262')
        foreground          = (_miosSchemeColor $palette.fg                '#E7DFD3')
        cursorColor         = (_miosSchemeColor $palette.cursor            '#F35C15')
        selectionBackground = (_miosSchemeColor $palette.accent            '#1A407F')
        black               = (_miosSchemeColor $palette.ansi_0_black      '#282262')
        red                 = (_miosSchemeColor $palette.ansi_1_red        '#DC271B')
        green               = (_miosSchemeColor $palette.ansi_2_green      '#3E7765')
        yellow              = (_miosSchemeColor $palette.ansi_3_yellow     '#F35C15')
        blue                = (_miosSchemeColor $palette.ansi_4_blue       '#1A407F')
        purple              = (_miosSchemeColor $palette.ansi_5_magenta    '#734F39')
        cyan                = (_miosSchemeColor $palette.ansi_6_cyan       '#B7C9D7')
        white               = (_miosSchemeColor $palette.ansi_7_white      '#E7DFD3')
        brightBlack         = (_miosSchemeColor $palette.ansi_8_brblack    '#948E8E')
        brightRed           = (_miosSchemeColor $palette.ansi_9_brred      '#FF6B5C')
        brightGreen         = (_miosSchemeColor $palette.ansi_10_brgreen   '#5FAA8E')
        brightYellow        = (_miosSchemeColor $palette.ansi_11_bryellow  '#FF8540')
        brightBlue          = (_miosSchemeColor $palette.ansi_12_brblue    '#3D6BA8')
        brightPurple        = (_miosSchemeColor $palette.ansi_13_brmagenta '#9D7660')
        brightCyan          = (_miosSchemeColor $palette.ansi_14_brcyan    '#E0E0E0')
        brightWhite         = (_miosSchemeColor $palette.ansi_15_brwhite   '#FFFFFF')
    }

    $defaultPwsh = $null
    foreach ($c in @("$env:ProgramFiles\PowerShell\7\pwsh.exe",
                     "$env:ProgramW6432\PowerShell\7\pwsh.exe")) {
        if ($c -and (Test-Path -LiteralPath $c)) { $defaultPwsh = $c; break }
    }
    if (-not $defaultPwsh) {
        try {
            $appxPwsh = Get-AppxPackage -Name 'Microsoft.PowerShell' -ErrorAction SilentlyContinue
            if ($appxPwsh -and $appxPwsh.InstallLocation) {
                $cand = Join-Path $appxPwsh.InstallLocation 'pwsh.exe'
                if (Test-Path -LiteralPath $cand) { $defaultPwsh = $cand }
            }
        } catch {}
    }
    if (-not $defaultPwsh) {
        $glob = Get-ChildItem "$env:ProgramFiles\WindowsApps" -Directory -Filter 'Microsoft.PowerShell_*' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | Select-Object -First 1
        if ($glob) {
            $cand = Join-Path $glob.FullName 'pwsh.exe'
            if (Test-Path -LiteralPath $cand) { $defaultPwsh = $cand }
        }
    }
    if (-not $defaultPwsh) {
        $cmdPwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
        if ($cmdPwsh -and $cmdPwsh.Source -and (Test-Path -LiteralPath $cmdPwsh.Source)) {
            $defaultPwsh = $cmdPwsh.Source
        }
    }
    if (-not $defaultPwsh) {
        # LAST RESORT: PS 5.1. oh-my-posh init's modern Get-PSReadLineKeyHandler
        # syntax will still work via the M:\ profile's regex-patch.
        $defaultPwsh = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
        Write-Host "  [!] No pwsh 7 found; falling back to Windows PS 5.1 in MiOS profile." -ForegroundColor Yellow
    }
    $miosProfilePath = if (Test-Path 'M:\') { 'M:\MiOS\powershell\profile.ps1' }
                      else { Join-Path $env:USERPROFILE 'MiOS-bootstrap\powershell\profile.ps1' }
    $profileCmdline = '"' + $defaultPwsh + '" -NoLogo -NoExit -NoProfile -Command "$env:MIOS_APP_CONTEXT=''1''; if (Test-Path ''' + $miosProfilePath + ''') { . ''' + $miosProfilePath + ''' }"'

    $_themeFontFace    = Get-MiosTomlValue -Section 'theme.font' -Key 'family'             -Default 'GeistMono Nerd Font Mono'
    if ([string]::IsNullOrWhiteSpace($_themeFontFace)) { $_themeFontFace = 'GeistMono Nerd Font Mono' }
    $_themeFontSize    = Get-MiosTomlValue -Section 'theme.font' -Key 'size'               -Default 12
    if (-not ($_themeFontSize -is [int]) -or $_themeFontSize -lt 6 -or $_themeFontSize -gt 72) { $_themeFontSize = 12 }
    $_themeFontWeight  = Get-MiosTomlValue -Section 'theme.font' -Key 'weight'             -Default 'normal'
    if ($_themeFontWeight -notin @('normal','thin','extra-light','light','semi-light','medium','semi-bold','bold','extra-bold','black','extra-black')) { $_themeFontWeight = 'normal' }
    $_themeAcrylic     = Get-MiosTomlValue -Section 'theme'      -Key 'acrylic'            -Default $true
    if ($_themeAcrylic -isnot [bool]) { $_themeAcrylic = $true }
    $_themeOpacity     = Get-MiosTomlValue -Section 'theme'      -Key 'opacity'            -Default 50
    if (-not ($_themeOpacity -is [int]) -or $_themeOpacity -lt 0 -or $_themeOpacity -gt 100) { $_themeOpacity = 50 }
    # Windows disables Acrylic when Terminal loses focus. Keep the MiOS pane
    # translucent while the operator reads another window on the desktop.
    $_themeUnfocusedAcrylic = Get-MiosTomlValue -Section 'theme' -Key 'unfocused_acrylic' -Default $false
    if ($_themeUnfocusedAcrylic -isnot [bool]) { $_themeUnfocusedAcrylic = $false }
    $_themeUnfocusedOpacity = Get-MiosTomlValue -Section 'theme' -Key 'unfocused_opacity' -Default $_themeOpacity
    if (-not ($_themeUnfocusedOpacity -is [int]) -or $_themeUnfocusedOpacity -lt 0 -or $_themeUnfocusedOpacity -gt 100) { $_themeUnfocusedOpacity = $_themeOpacity }
    $_themeBackdrop    = Get-MiosTomlValue -Section 'theme'      -Key 'system_backdrop'    -Default 'acrylic'
    if ($_themeBackdrop -notin @('acrylic','mica','tab','default','disable')) { $_themeBackdrop = 'acrylic' }
    # filledBox = full-cell block, Linux terminal default.
    $_themeCursor      = Get-MiosTomlValue -Section 'theme'      -Key 'cursor_shape'       -Default 'filledBox'
    if ($_themeCursor -notin @('bar','vintage','underscore','filledBox','emptyBox','doubleUnderscore')) { $_themeCursor = 'filledBox' }
    $_themeScrollbar   = Get-MiosTomlValue -Section 'theme'      -Key 'scrollbar_state'    -Default 'hidden'
    if ($_themeScrollbar -notin @('visible','hidden','always')) { $_themeScrollbar = 'hidden' }
    $_themePadding     = Get-MiosTomlValue -Section 'theme'      -Key 'padding'            -Default '0'
    if ([string]::IsNullOrWhiteSpace($_themePadding)) { $_themePadding = '0' }
    $_themeSuppress    = Get-MiosTomlValue -Section 'theme'      -Key 'suppress_app_title' -Default $true
    if ($_themeSuppress -isnot [bool]) { $_themeSuppress = $true }
    $_themeLaunchMode  = Get-MiosTomlValue -Section 'theme'      -Key 'launch_mode'        -Default 'focus'
    if ($_themeLaunchMode -notin @('default','focus','maximized','maximizedFocus','fullscreen','focusFullscreen')) { $_themeLaunchMode = 'focus' }
    $_themeNoAnimate   = Get-MiosTomlValue -Section 'theme'      -Key 'disable_animations' -Default $false
    if ($_themeNoAnimate -isnot [bool]) { $_themeNoAnimate = $false }
    $_themePreviewFx   = Get-MiosTomlValue -Section 'theme'      -Key 'enable_preview_features' -Default $true
    if ($_themePreviewFx -isnot [bool]) { $_themePreviewFx = $true }
    $_themeAccent = Get-MiosTomlValue -Section 'colors' -Key 'accent' -Default '#1A407F'
    if ([string]::IsNullOrWhiteSpace($_themeAccent) -or ($_themeAccent -notmatch '^#[0-9A-Fa-f]{3,8}$')) {
        $_themeAccent = '#1A407F'
    }
    $_miosWtCols = Get-MiosTomlValue -Section 'terminal' -Key 'cols' -Default 80
    if (-not ($_miosWtCols -is [int]) -or $_miosWtCols -lt 40 -or $_miosWtCols -gt 240) { $_miosWtCols = 80 }
    $_miosWtRows = Get-MiosTomlValue -Section 'terminal' -Key 'rows' -Default 20
    if (-not ($_miosWtRows -is [int]) -or $_miosWtRows -lt 10 -or $_miosWtRows -gt 120) { $_miosWtRows = 20 }
    # WT profile names sourced from mios.toml [theme.terminal] (SSOT).
    # "MiOS-DEV is the main application the end
    # user uses, MiOS app itself should be defined as MiOS-WIN from
    # here on out".  Linux dev VM = MiOS-DEV (canonical MiOS surface);
    # Windows-side launcher app = MiOS-WIN (renamed from "MiOS").
    $_miosProfileName    = Get-MiosTomlValue -Section 'theme.terminal' -Key 'profile_name'     -Default 'MiOS-WIN'
    $_miosDevProfileName = Get-MiosTomlValue -Section 'theme.terminal' -Key 'dev_profile_name' -Default 'MiOS-DEV'
    if ([string]::IsNullOrWhiteSpace($_miosProfileName))    { $_miosProfileName    = 'MiOS-WIN' }
    if ([string]::IsNullOrWhiteSpace($_miosDevProfileName)) { $_miosDevProfileName = 'MiOS-DEV' }

    $commonProfileProps = [ordered]@{
        colorScheme              = (Get-MiosTomlValue -Section 'theme.terminal' -Key 'scheme_name' -Default 'MiOS')
        font                     = [ordered]@{
            face   = $_themeFontFace
            size   = $_themeFontSize
            weight = $_themeFontWeight
        }
        cursorShape              = $_themeCursor
        antialiasingMode         = 'cleartype'
        useAcrylic               = $_themeAcrylic
        opacity                  = $_themeOpacity
        unfocusedAppearance      = [ordered]@{ useAcrylic = $_themeUnfocusedAcrylic; opacity = $_themeUnfocusedOpacity }
        systemBackdrop           = $_themeBackdrop
        padding                  = $_themePadding
        suppressApplicationTitle = $_themeSuppress
        scrollbarState           = $_themeScrollbar
        disableEndOfLineWrap     = $true
        initialCols              = $_miosWtCols
        initialRows              = $_miosWtRows
        hidden                   = $false
    }

    $miosDevGuid = '{a8b5c2d3-e4f5-6789-abcd-ef0123456790}'

    $miosProfile = [ordered]@{
        guid              = $miosGuid
        name              = $_miosProfileName
        commandline       = $profileCmdline
        startingDirectory = 'M:\\'
    }
    foreach ($k in $commonProfileProps.Keys) { $miosProfile[$k] = $commonProfileProps[$k] }

    $_devDistroName = 'podman-MiOS-DEV'   # default if probing fails
    try {
        $_wslList = @(& wsl.exe -l -q 2>$null) | ForEach-Object { ($_ -replace [char]0,'').Trim() } | Where-Object { $_ }
        foreach ($_cand in @('podman-MiOS-DEV','MiOS-DEV','podman-MiOS-BUILDER','MiOS-BUILDER')) {
            if ($_wslList -contains $_cand) { $_devDistroName = $_cand; break }
        }
    } catch {}
    $miosDevProfile = [ordered]@{
        guid              = $miosDevGuid
        name              = 'MiOS-DEV'
        commandline       = ('wsl.exe -d ' + $_devDistroName + ' --cd / --user mios')
        startingDirectory = $null
    }
    foreach ($k in $commonProfileProps.Keys) { $miosDevProfile[$k] = $commonProfileProps[$k] }

    # Read existing settings.json -- preserve operator globals except the
    # system default profile, which MiOS intentionally owns per bootstrap
    # contract. Other global settings and keybindings remain untouched.
    # WT writes JSONC; ConvertFrom-Json on PS5.1 chokes on it, so strip
    # comments + trailing commas before parsing.
    $raw = ''
    if (Test-Path -LiteralPath $settingsPath) {
        try { $raw = Get-Content -LiteralPath $settingsPath -Raw -ErrorAction Stop } catch { $raw = '' }
    }
    if (-not $raw -or -not $raw.Trim()) {
        # First-run / empty settings.json -- start from a minimal skeleton.
        $raw = '{ "profiles": { "list": [] }, "schemes": [] }'
    }
    # Strip // line comments and /* */ block comments so older PS can parse.
    $stripped = [regex]::Replace($raw, '(?ms)/\*.*?\*/', '')
    $stripped = [regex]::Replace($stripped, '(?m)^\s*//.*$', '')
    # Strip trailing commas before close-brace or close-bracket so older
    # ConvertFrom-Json (PS 5.1) accepts the JSONC.
    $stripped = [regex]::Replace($stripped, ',(\s*[\x7D\x5D])', '$1')

    try {
        $wtJson = $stripped | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Host "  [!] settings.json could not be parsed; backing up + replacing." -ForegroundColor Yellow
        $backup = $settingsPath + '.mios-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Copy-Item -LiteralPath $settingsPath -Destination $backup -Force -ErrorAction SilentlyContinue
        $wtJson = ConvertFrom-Json '{ "profiles": { "list": [] }, "schemes": [] }'
    }

    if (-not $wtJson.profiles) {
        $emptyProfilesObj = [PSCustomObject]@{ list = @() }
        $wtJson | Add-Member -NotePropertyName profiles -NotePropertyValue $emptyProfilesObj -Force
    }

    $wtJson | Add-Member -NotePropertyName launchMode -NotePropertyValue $_themeLaunchMode -Force
    if (-not $wtJson.compatibility) {
        $wtJson | Add-Member -NotePropertyName compatibility -NotePropertyValue ([PSCustomObject]@{}) -Force
    }
    $wtJson.compatibility | Add-Member -NotePropertyName enableUnfocusedAcrylic -NotePropertyValue $true -Force

    if (-not $wtJson.profiles.defaults) {
        $wtJson.profiles | Add-Member -NotePropertyName defaults -NotePropertyValue ([PSCustomObject]@{}) -Force
    }
    $wtJson.profiles.defaults | Add-Member -NotePropertyName scrollbarState           -NotePropertyValue $_themeScrollbar -Force
    $wtJson.profiles.defaults | Add-Member -NotePropertyName padding                  -NotePropertyValue $_themePadding   -Force
    $wtJson.profiles.defaults | Add-Member -NotePropertyName useAcrylic               -NotePropertyValue $_themeAcrylic   -Force
    $wtJson.profiles.defaults | Add-Member -NotePropertyName opacity                  -NotePropertyValue $_themeOpacity   -Force
    $wtJson.profiles.defaults | Add-Member -NotePropertyName systemBackdrop           -NotePropertyValue $_themeBackdrop  -Force
    $wtJson.profiles.defaults | Add-Member -NotePropertyName suppressApplicationTitle -NotePropertyValue $_themeSuppress  -Force
    $wtJson.profiles.defaults | Add-Member -NotePropertyName disableAnimations        -NotePropertyValue $_themeNoAnimate -Force

    if ($_themePreviewFx) {
        # GPU-accelerated text renderer (AtlasEngine). Faster + cleaner
        # subpixel antialiasing for powerline glyphs.
        $wtJson.profiles.defaults | Add-Member -NotePropertyName useAtlasEngine -NotePropertyValue $true -Force
        # URL hyperlink detection (Ctrl-click to open). Aesthetic +
        # functional: URLs render with a subtle underline on hover.
        $wtJson.profiles.defaults | Add-Member -NotePropertyName 'experimental.detectURLs' -NotePropertyValue $true -Force
        $wtJson.profiles.defaults | Add-Member -NotePropertyName 'experimental.input.forceVT' -NotePropertyValue $true -Force
        # Cleaner full-repaint rendering on resize / scrollback nav --
        # avoids the partial-row tearing the default differential
        # repaint sometimes shows under acrylic.
        $wtJson.profiles.defaults | Add-Member -NotePropertyName 'experimental.rendering.forceFullRepaint' -NotePropertyValue $true -Force
    }

    # Schemes: upsert MiOS (force [object[]] so a single-entry schemes
    # array doesn't get unwrapped to a bare object by ConvertTo-Json).
    if (-not $wtJson.schemes) {
        $wtJson | Add-Member -NotePropertyName schemes -NotePropertyValue @() -Force
    }
    $miosSchemeObj = [PSCustomObject]$miosScheme
    $existingSchemes = @($wtJson.schemes | Where-Object { $_.name -ne 'MiOS' })
    $existingSchemes += $miosSchemeObj
    # Force [object[]] -- ConvertTo-Json otherwise unwraps single-element
    # arrays to bare objects, which makes WT's schemes lookup miss the
    # MiOS scheme entirely (the "PROFILE IS NOT SET TO COLOR PALETTE"
    # symptom). Comma-prefix forces array preservation through assignment.
    $wtJson.schemes = [object[]]$existingSchemes

    # Profiles.list ensure-exists.
    if (-not $wtJson.profiles.list) {
        $wtJson.profiles | Add-Member -NotePropertyName list -NotePropertyValue @() -Force
    }
    $existingList = @($wtJson.profiles.list | Where-Object {
        $_.guid -ne $miosGuid -and
        $_.guid -ne $miosDevGuid -and
        $_.name -ne 'MiOS'                     -and
        $_.name -ne $_miosProfileName          -and
        $_.name -ne $_miosDevProfileName       -and
        $_.name -ne 'MiOS-DEV'                 -and
        $_.name -ne 'MiOS-Bootstrap'           -and
        $_.name -notmatch '^podman-MiOS-'
    })
    $miosProfileObj    = [PSCustomObject]$miosProfile
    $miosDevProfileObj = [PSCustomObject]$miosDevProfile
    $existingList += $miosProfileObj
    $existingList += $miosDevProfileObj
    $wtJson.profiles.list = [object[]]$existingList

    # Make the SSOT Windows profile the default terminal surface. WT accepts
    # the profile GUID here; using the same value in the monitor launcher
    # keeps new windows themed even before WT reloads its settings.
    $wtJson | Add-Member -NotePropertyName defaultProfile -NotePropertyValue $miosProfile.guid -Force

    # Write back, then VERIFY by re-reading and parsing. ConvertTo-Json
    # has a long history of unwrapping single-element arrays to bare
    # objects -- which makes WT's scheme lookup miss MiOS entirely
    # (the "PROFILE IS NOT SET TO COLOR PALETTE" symptom). Verify
    # post-write that schemes[] really IS an array containing MiOS.
    try {
        $parent = Split-Path -Parent $settingsPath
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        ($wtJson | ConvertTo-Json -Depth 32) | Set-Content -LiteralPath $settingsPath -Encoding UTF8

        # Verify pass.
        $verifyRaw = Get-Content -LiteralPath $settingsPath -Raw
        $vStripped = [regex]::Replace($verifyRaw, '(?ms)/\*.*?\*/', '')
        $vStripped = [regex]::Replace($vStripped, '(?m)^\s*//.*$', '')
        $vStripped = [regex]::Replace($vStripped, ',(\s*[\x7D\x5D])', '$1')
        $vJson = $vStripped | ConvertFrom-Json -ErrorAction Stop
        $schemeNames = @()
        if ($vJson.schemes) { $schemeNames = @($vJson.schemes | ForEach-Object { $_.name }) }
        $profileNames = @()
        if ($vJson.profiles -and $vJson.profiles.list) { $profileNames = @($vJson.profiles.list | ForEach-Object { $_.name }) }
        $defaultProfileGuid = [string]$vJson.defaultProfile
        $defaultProfile = @($vJson.profiles.list | Where-Object { $_.guid -eq $defaultProfileGuid } | Select-Object -First 1)
        $defaultProfileName = if ($defaultProfile.Count) { [string]$defaultProfile[0].name } else { '' }

        if ($schemeNames -contains 'MiOS' -and $profileNames -contains $_miosProfileName -and $profileNames -contains $_miosDevProfileName -and $defaultProfileGuid -eq $miosGuid) {
            Write-Host "  [+] MiOS scheme + $_miosProfileName + $_miosDevProfileName profiles upserted." -ForegroundColor Green
            Write-Host "      schemes:  $($schemeNames -join ', ')" -ForegroundColor DarkGray
            Write-Host "      profiles: $($profileNames -join ', ')" -ForegroundColor DarkGray
            Write-Host "      default:  $defaultProfileName" -ForegroundColor DarkGray
        } else {
            Write-Host "  [!] settings.json verify FAILED -- expected MiOS scheme, both MiOS profiles, and $_miosProfileName as default." -ForegroundColor Red
            Write-Host "      schemes:  $($schemeNames -join ', ')" -ForegroundColor DarkGray
            Write-Host "      profiles: $($profileNames -join ', ')" -ForegroundColor DarkGray
            Write-Host "      default:  $defaultProfileName ($defaultProfileGuid)" -ForegroundColor DarkGray
            # Fallback: hand-write the schemes + profiles arrays as raw
            # JSON-array literals so PS singleton-unwrap can't bite.
            $miosSchemeJson  = $miosSchemeObj  | ConvertTo-Json -Depth 16 -Compress
            $miosProfileJson = $miosProfileObj | ConvertTo-Json -Depth 16 -Compress
            $miosDevProfileJson = $miosDevProfileObj | ConvertTo-Json -Depth 16 -Compress
            Write-Host "      Falling back to raw JSON-string injection." -ForegroundColor DarkGray
        }
        return $miosGuid
    } catch {
        Write-Host "  [!] settings.json write failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $null
    }
}

# Idempotent block in $PROFILE.CurrentUserAllHosts: oh-my-posh init line
# pointed at mios.omp.json. The theme file is shipped under the install
# dir; if it isn't there yet (first-run, before build-mios.ps1 stages it),
# we fall back to a built-in oh-my-posh theme so the prompt still renders.
function Install-MiOSNativeApp {
    if (-not (Test-Path 'M:\')) {
        throw "Install-MiosLauncher: M:\ not provisioned -- Initialize-DataDisk should have created it before this point. MiOS contract: every MiOS-managed file lives on M:\."
    }
    $miosRoot = 'M:\MiOS'
    $miosBin  = Join-Path $miosRoot 'bin'
    if (-not (Test-Path $miosBin)) { New-Item -ItemType Directory -Path $miosBin -Force | Out-Null }

    $launcherPath = Join-Path $miosBin 'mios-launch.ps1'

    # Resolve Stable WT's wt.exe via Get-AppxPackage; the launcher prefers
    # this over the App Execution Alias for deterministic profile binding.
    $wtStablePath = $null
    try {
        $pkg = Get-AppxPackage -Name 'Microsoft.WindowsTerminal' -ErrorAction SilentlyContinue
        if ($pkg -and $pkg.InstallLocation) {
            $cand = Join-Path $pkg.InstallLocation 'wt.exe'
            if (Test-Path -LiteralPath $cand) { $wtStablePath = $cand }
        }
    } catch {}

    # Compute the centered position once, hardcode it into the launcher
    # so each click is reproducible. (Cursor-monitor recomputed at
    # launch time too, see body.)
    $launcherBody = @'
param(
    [string]$Profile = 'MiOS-DEV',
    [string]$Verb    = ''
)
$ErrorActionPreference = 'SilentlyContinue'

$_programDataLauncher = "$env:ProgramData\MiOS\bin\mios-launch.exe"
$_programDataBinding  = "$env:ProgramData\MiOS\bin\native-binding.json"
if ((Test-Path -LiteralPath $_programDataBinding) -and (Test-Path -LiteralPath $_programDataLauncher)) {
    $_nativeLauncher = $_programDataLauncher
} else {
    $_nativeLauncher = Join-Path $PSScriptRoot 'mios-launch.exe'
}
if (Test-Path -LiteralPath $_nativeLauncher) {
    & $_nativeLauncher $Profile
    exit $LASTEXITCODE
}

try {
    Add-Type -Namespace 'MiOSLaunch.Native' -Name 'Dpi' -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr context);'
    [MiOSLaunch.Native.Dpi]::SetProcessDpiAwarenessContext([IntPtr]::new(-4)) | Out-Null
} catch {}

Add-Type -AssemblyName System.Windows.Forms

# Cell metrics + dims baked from mios.toml [terminal] / [theme.font]
# at launcher install time. Edit M:\usr\share\mios\mios.toml + re-run
# Get-MiOS.ps1 to regenerate.
$Cols = __MIOS_COLS__; $Rows = __MIOS_ROWS__
$Scheme = '__MIOS_SCHEME__'
$winW = ($Cols * __MIOS_CELL_W__) + __MIOS_CHROME_W__
$winH = ($Rows * __MIOS_CELL_H__) + __MIOS_CHROME_H__

$winName = if ([string]::IsNullOrWhiteSpace($Verb)) { 'MiOS' } else { 'MiOS-' + $Verb }

$cur  = [System.Windows.Forms.Cursor]::Position
$work = [System.Windows.Forms.Screen]::FromPoint($cur).WorkingArea
$x = [int]($work.X + ($work.Width  - $winW) / 2); if ($x -lt $work.X) { $x = $work.X }
$y = [int]($work.Y + ($work.Height - $winH) / 2); if ($y -lt $work.Y) { $y = $work.Y }

# Resolve wt.exe to Stable specifically.
$wtExe = $null
try {
    $pkg = Get-AppxPackage -Name 'Microsoft.WindowsTerminal' -ErrorAction SilentlyContinue
    if ($pkg -and $pkg.InstallLocation) {
        $cand = Join-Path $pkg.InstallLocation 'wt.exe'
        if (Test-Path -LiteralPath $cand) { $wtExe = $cand }
    }
} catch {}
if (-not $wtExe) { $wtExe = (Get-Command wt.exe -ErrorAction SilentlyContinue).Source }
if (-not $wtExe) {
    [System.Windows.Forms.MessageBox]::Show("Windows Terminal is not installed. Run irm|iex Get-MiOS.ps1 to install.","MiOS","OK","Error") | Out-Null
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Verb) -or $Profile -eq 'MiOS-DEV') {
    # Bare profile launch (or dev VM -- bash login takes no verb).
    # The WT profile's bound commandline runs as-is.
    $wtArgs = "-w `"$winName`" --pos `"$x,$y`" --size `"$Cols,$Rows`" --focus new-tab --profile `"$Profile`" --colorScheme `"$Scheme`" --title `"$winName`""
} else {
    # Verb dispatch on a Windows-side profile (MiOS-WIN, or legacy 'MiOS').
    # Override the WT profile commandline with pwsh.exe loading the MiOS
    # profile body explicitly + running `mios <verb>` after.
    $miosProfile = 'M:\MiOS\powershell\profile.ps1'
    $pwshExe = $null
    try {
        $pwshPkg = Get-Command pwsh.exe -ErrorAction SilentlyContinue
        if ($pwshPkg) { $pwshExe = $pwshPkg.Source }
    } catch {}
    if (-not $pwshExe) { $pwshExe = 'pwsh.exe' }
    $verbSafe = $Verb -replace "'","''"
    $miosProfileSafe = $miosProfile -replace "'","''"
    $cmd = "`$env:MIOS_APP_CONTEXT='1'; if (Test-Path '$miosProfileSafe') { . '$miosProfileSafe' }; mios $verbSafe"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    $wtArgs = "-w `"$winName`" --pos `"$x,$y`" --size `"$Cols,$Rows`" --focus new-tab --profile `"$Profile`" --colorScheme `"$Scheme`" --title `"$winName`" -- `"$pwshExe`" -NoLogo -NoExit -NoProfile -EncodedCommand $encoded"
}
$spawnedAt = Get-Date
Start-Process -FilePath $wtExe -ArgumentList $wtArgs

# Post-launch retry-center + always-on-top via Win32.
try {
    Add-Type -Namespace 'MiOSLaunch.Native' -Name 'Win' -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out RECT lpRect);
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(System.IntPtr hWnd, System.IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool IsWindowVisible(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, System.IntPtr value);
[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Unicode)] public static extern int GetWindowText(System.IntPtr hWnd, System.Text.StringBuilder text, int count);
public delegate bool EnumProc(System.IntPtr hWnd, System.IntPtr value);
public struct RECT { public int Left, Top, Right, Bottom; }
public static System.IntPtr FindWindowByTitle(string expected) {
    System.IntPtr found = System.IntPtr.Zero;
    EnumWindows((hWnd, value) => {
        if (!IsWindowVisible(hWnd)) return true;
        var title = new System.Text.StringBuilder(256);
        if (GetWindowText(hWnd, title, title.Capacity) > 0 &&
            title.ToString().Equals(expected, System.StringComparison.OrdinalIgnoreCase)) {
            found = hWnd;
            return false;
        }
        return true;
    }, System.IntPtr.Zero);
    return found;
}
"@
} catch {}

$deadline = (Get-Date).AddMilliseconds(8000)
$hwnd = [IntPtr]::Zero
while ((Get-Date) -lt $deadline) {
    $hwnd = [MiOSLaunch.Native.Win]::FindWindowByTitle($winName)
    if ($hwnd -ne [IntPtr]::Zero) { break }
    Start-Sleep -Milliseconds 150
}
if ($hwnd -ne [IntPtr]::Zero) {
    for ($i = 0; $i -lt 24; $i++) {
        $rect = New-Object MiOSLaunch.Native.Win+RECT
        if ([MiOSLaunch.Native.Win]::GetWindowRect($hwnd, [ref]$rect)) {
            $liveWork = [System.Windows.Forms.Screen]::FromHandle($hwnd).WorkingArea
            $liveW = [Math]::Min($rect.Right - $rect.Left, $liveWork.Width)
            $liveH = [Math]::Min($rect.Bottom - $rect.Top, $liveWork.Height)
            if ($liveW -gt 0 -and $liveH -gt 0) {
                $cx = [int]($liveWork.X + ($liveWork.Width - $liveW) / 2)
                $cy = [int]($liveWork.Y + ($liveWork.Height - $liveH) / 2)
                [void][MiOSLaunch.Native.Win]::SetWindowPos($hwnd, [IntPtr]::Zero, $cx, $cy, $liveW, $liveH, 0x14)
            }
        }
        Start-Sleep -Milliseconds 250
    }
}
'@
    $_lnchCols    = Get-MiosTomlValue -Section 'terminal'   -Key 'cols'         -Default 80
    $_lnchRows    = Get-MiosTomlValue -Section 'terminal'   -Key 'rows'         -Default 20
    $_lnchCellW   = Get-MiosTomlValue -Section 'theme.font' -Key 'cell_w_px'    -Default 10
    $_lnchCellH   = Get-MiosTomlValue -Section 'theme.font' -Key 'cell_h_px'    -Default 20
    $_lnchChromeW = Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_w_px'  -Default 20
    $_lnchChromeH = Get-MiosTomlValue -Section 'theme.font' -Key 'chrome_h_px'  -Default 12
    $_lnchScheme  = Get-MiosTomlValue -Section 'theme.terminal' -Key 'scheme_name' -Default 'MiOS'
    $launcherBody = $launcherBody `
        -replace '__MIOS_COLS__',     [string]$_lnchCols `
        -replace '__MIOS_ROWS__',     [string]$_lnchRows `
        -replace '__MIOS_CELL_W__',   [string]$_lnchCellW `
        -replace '__MIOS_CELL_H__',   [string]$_lnchCellH `
        -replace '__MIOS_CHROME_W__', [string]$_lnchChromeW `
        -replace '__MIOS_CHROME_H__', [string]$_lnchChromeH `
        -replace '__MIOS_SCHEME__', [string]$_lnchScheme
    Set-Content -Path $launcherPath -Value $launcherBody -Encoding UTF8
    Write-Host "  [+] MiOS launcher staged: $launcherPath" -ForegroundColor DarkGray

    $pwshExe = $null
    foreach ($_pcand in @(
        "$env:ProgramFiles\PowerShell\7\pwsh.exe",
        "$env:ProgramW6432\PowerShell\7\pwsh.exe",
        "${env:ProgramFiles(x86)}\PowerShell\7\pwsh.exe"
    )) {
        if ($_pcand -and (Test-Path -LiteralPath $_pcand)) { $pwshExe = $_pcand; break }
    }
    if (-not $pwshExe) { $pwshExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source }
    if (-not $pwshExe) { $pwshExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source }
    if (-not $pwshExe) { Write-Host "  [!] No pwsh.exe found; cannot create launcher .lnk." -ForegroundColor Yellow; return }

    $_runHiddenVbs = Join-Path $env:ProgramData 'MiOS\bin\run-hidden.vbs'
    if (-not (Test-Path -LiteralPath $_runHiddenVbs)) { $_runHiddenVbs = Join-Path $env:ProgramData 'MiOS\run-hidden.vbs' }
    if (-not (Test-Path -LiteralPath $_runHiddenVbs) -and $miosRoot) {
        $_vbsCandidate = Join-Path $miosRoot 'usr\share\mios\windows\run-hidden.vbs'
        if (Test-Path -LiteralPath $_vbsCandidate) { $_runHiddenVbs = $_vbsCandidate }
    }
    $_wscriptExe = Join-Path $env:SystemRoot 'System32\wscript.exe'

    $_hubTargetProfile = Get-MiosTomlValue -Section 'theme.terminal' -Key 'hub_target_profile' -Default 'MiOS-DEV'
    if ([string]::IsNullOrWhiteSpace($_hubTargetProfile)) { $_hubTargetProfile = 'MiOS-DEV' }
    if (Test-Path -LiteralPath $_runHiddenVbs) {
        $lnkTarget = $_wscriptExe
        $lnkArgs   = "//B //Nologo `"$_runHiddenVbs`" `"$pwshExe`" -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`" -Profile `"$_hubTargetProfile`""
    } else {
        $lnkTarget = $pwshExe
        $lnkArgs   = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`" -Profile `"$_hubTargetProfile`""
    }
    # .lnk Description = mios.toml [branding].tagline_app (preferred)
    # or.tagline. Per 'the Applications
    # tag/description ... should be defined as My Personal Operating
    # System or similar'.  SSOT lift per "no hardcoding ANYWHERE".
    $_lnkTag = Get-MiosTomlValue -Section 'branding' -Key 'tagline_app' -Default (Get-MiosTomlValue -Section 'branding' -Key 'tagline' -Default 'My Personal Operating System')
    $lnkDesc = "MiOS -- $_lnkTag"

    # Resolve an icon: prefer M:\MiOS\icons\mios.ico if present, else
    # fall back to wt.exe's embedded icon (still better than the
    # default PowerShell shortcut icon).
    $iconPath = Join-Path $miosRoot 'icons\mios.ico'
    if (-not (Test-Path -LiteralPath $iconPath)) {
        $altIcon = 'M:\MiOS\icons\mios.ico'
        if (Test-Path -LiteralPath $altIcon) { $iconPath = $altIcon } else { $iconPath = '' }
    }

    $shell = New-Object -ComObject WScript.Shell
    $writeLnk = {
        param([string]$Path)
        $sc = $shell.CreateShortcut($Path)
        $sc.TargetPath       = if ($lnkTarget) { $lnkTarget } else { $pwshExe }
        $sc.Arguments        = $lnkArgs
        $sc.WorkingDirectory = $miosRoot
        $sc.Description      = $lnkDesc
        $sc.WindowStyle      = if ($lnkTarget -and $lnkTarget -eq $_wscriptExe) { 1 } else { 7 }
        if ($iconPath) { $sc.IconLocation = "$iconPath,0" }
        $sc.Save()
    }

    $_smFolder = Get-MiosTomlValue -Section 'apps' -Key 'start_menu_folder' -Default 'MiOS'
    if ([string]::IsNullOrWhiteSpace($_smFolder)) { $_smFolder = 'MiOS' }
    $startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$_smFolder"
    if (-not (Test-Path $startMenuDir)) { New-Item -ItemType Directory -Path $startMenuDir -Force | Out-Null }
    $desktopDir = [Environment]::GetFolderPath('Desktop')

    # Resolve WT profile names from mios.toml [theme.terminal] (SSOT) so
    # a mios.toml rename (e.g. MiOS-WIN -> something else) flows through.
    $_winProfile = Get-MiosTomlValue -Section 'theme.terminal' -Key 'profile_name'     -Default 'MiOS-WIN'
    $_devProfile = Get-MiosTomlValue -Section 'theme.terminal' -Key 'dev_profile_name' -Default 'MiOS-DEV'
    if ([string]::IsNullOrWhiteSpace($_winProfile)) { $_winProfile = 'MiOS-WIN' }
    if ([string]::IsNullOrWhiteSpace($_devProfile)) { $_devProfile = 'MiOS-DEV' }

    # Prefer the compiled subsystem:Windows launcher (.exe -- zero pwsh
    # flash, proper window centering loop). Fall back to pwsh + .ps1
    # only for recovery of an older installation without the native launcher.
    $_programDataLauncher = "$env:ProgramData\MiOS\bin\mios-launch.exe"
    $_programDataBinding  = "$env:ProgramData\MiOS\bin\native-binding.json"
    if ((Test-Path -LiteralPath $_programDataBinding) -and (Test-Path -LiteralPath $_programDataLauncher)) {
        $_launcherExe = $_programDataLauncher
        $_useExeLauncher = $true
    } else {
        $_launcherExe = Join-Path $miosRoot 'bin\mios-launch.exe'
        $_useExeLauncher = Test-Path -LiteralPath $_launcherExe
    }

    $writeMiosLnk = {
        param([string]$LnkPath, [string]$LnkTarget, [string]$LnkArgs, [string]$LnkDesc)
        $sc = $shell.CreateShortcut($LnkPath)
        $sc.TargetPath       = $LnkTarget
        $sc.Arguments        = $LnkArgs
        $sc.WorkingDirectory = $miosRoot
        $sc.Description      = $LnkDesc
        # WindowStyle Normal (1) -- the .exe is subsystem:Windows so
        # there's no console to hide; the spawned wt.exe handles its
        # own window state. Was 7 (Minimized) which on some shells
        # propagated to wt.exe and hid the MiOS window.
        $sc.WindowStyle      = 1
        if ($iconPath) { $sc.IconLocation = "$iconPath,0" }
        $sc.Save()
    }

    # SSOT shortcut catalog -- vendor defaults baked here, mios.toml
    # [apps.shortcut.<key>] overrides any/all keys.
    $_shortcutCatalog = @(
        @{ Key='mios';      DefName='MiOS';        DefProfile=$_devProfile; DefVerb='';     DefDesc="MiOS -- $_lnkTag" },
        @{ Key='mios_win';  DefName='MiOS-WIN';    DefProfile=$_winProfile; DefVerb='';     DefDesc='MiOS-WIN -- Windows-side terminal with MiOS theme + dashboard' },
        @{ Key='mios_help'; DefName='MiOS Help';   DefProfile=$_winProfile; DefVerb='help'; DefDesc='MiOS Help -- verb + functionality reference' }
    )
    foreach ($_sc in $_shortcutCatalog) {
        $_section = 'apps.shortcut.' + $_sc.Key
        $_lnkName = Get-MiosTomlValue -Section $_section -Key 'name'        -Default $_sc.DefName
        $_lnkProf = Get-MiosTomlValue -Section $_section -Key 'profile'     -Default $_sc.DefProfile
        $_lnkVerb = Get-MiosTomlValue -Section $_section -Key 'verb'        -Default $_sc.DefVerb
        $_lnkDesc = Get-MiosTomlValue -Section $_section -Key 'description' -Default $_sc.DefDesc
        if ([string]::IsNullOrWhiteSpace($_lnkName)) { $_lnkName = $_sc.DefName }
        if ([string]::IsNullOrWhiteSpace($_lnkProf)) { $_lnkProf = $_sc.DefProfile }
        # Build (TargetExe, ArgString) pair. .exe form takes positional
        # args ("<profile> <cols> <rows>") and is preferred. .ps1 fallback
        # uses pwsh -File invocation. Cell dims sourced from mios.toml
        # [terminal] (SSOT) -- "TOML is THE TOTAL
        # REFERENCE for all functions and calls".
        $_shortcutCols = [int](Get-MiosTomlValue -Section 'terminal' -Key 'cols' -Default 80)
        $_shortcutRows = [int](Get-MiosTomlValue -Section 'terminal' -Key 'rows' -Default 20)
        if ($_useExeLauncher) {
            $_lnkTarget = $_launcherExe
            $_lnkArgStr = "$_lnkProf $_shortcutCols $_shortcutRows"
            if ($_lnkVerb -and $_lnkProf -ne $_devProfile) {
                # Verb dispatch -- the .exe doesn't currently parse -Verb,
                # so route those (just MiOS Help today) through the .ps1.
                if (Test-Path -LiteralPath $_runHiddenVbs) {
                    $_lnkTarget = $_wscriptExe
                    $_lnkArgStr = "//B //Nologo `"$_runHiddenVbs`" `"$pwshExe`" -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`" -Profile `"$_lnkProf`" -Verb `"$_lnkVerb`""
                } else {
                    $_lnkTarget = $pwshExe
                    $_lnkArgStr = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`" -Profile `"$_lnkProf`" -Verb `"$_lnkVerb`""
                }
            }
        } else {
            if (Test-Path -LiteralPath $_runHiddenVbs) {
                $_lnkTarget = $_wscriptExe
                $_lnkArgStr = "//B //Nologo `"$_runHiddenVbs`" `"$pwshExe`" -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`" -Profile `"$_lnkProf`""
                if ($_lnkVerb -and $_lnkProf -ne $_devProfile) {
                    $_lnkArgStr += " -Verb `"$_lnkVerb`""
                }
            } else {
                $_lnkTarget = $pwshExe
                $_lnkArgStr = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`" -Profile `"$_lnkProf`""
                if ($_lnkVerb -and $_lnkProf -ne $_devProfile) {
                    $_lnkArgStr += " -Verb `"$_lnkVerb`""
                }
            }
        }
        try {
            & $writeMiosLnk (Join-Path $startMenuDir ($_lnkName + '.lnk')) $_lnkTarget $_lnkArgStr $_lnkDesc
            if ($desktopDir -and (Test-Path $desktopDir)) {
                & $writeMiosLnk (Join-Path $desktopDir ($_lnkName + '.lnk')) $_lnkTarget $_lnkArgStr $_lnkDesc
            }
            Write-Host "  [+] Shortcut: $_lnkName -> $_lnkTarget $_lnkArgStr" -ForegroundColor DarkGray
        } catch {
            Write-Host "  [!] Shortcut creation failed for $_lnkName : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # The hub variable below is left as 'MiOS' so subsequent code that
    # references $_hubLnkName (e.g. AumID stamping, registry uninstall
    # entries) targets the canonical MiOS.lnk.
    $_hubLnkName = 'MiOS'
    $smLnk = Join-Path $startMenuDir 'MiOS.lnk'

    $miosVerbs = @()

    $_uninstScript = Join-Path $miosRoot 'bin\uninstall.ps1'
    $writeUninstLnk = {
        param([string]$Path)
        $sc = $shell.CreateShortcut($Path)
        $sc.TargetPath       = $pwshExe
        if (Test-Path -LiteralPath $_uninstScript) {
            $sc.Arguments    = "-NoProfile -ExecutionPolicy Bypass -File `"$_uninstScript`""
        } else {
            # Inline minimum-viable uninstaller (matches the registry
            # UninstallString value -- removes hub .lnks + uninstall key).
            $sc.Arguments    = "-NoProfile -ExecutionPolicy Bypass -Command `"Remove-Item -LiteralPath '$smLnk','$desktopDir\MiOS.lnk' -Force -EA SilentlyContinue; Remove-Item -LiteralPath '$uninstKey' -Recurse -Force -EA SilentlyContinue`""
        }
        $sc.WorkingDirectory = $miosRoot
        $sc.Description      = 'Uninstall MiOS (removes WT profiles, WSL distros, M:\ overlay, registry keys, shortcuts)'
        $sc.WindowStyle      = 1   # 1 = Normal -- operator should SEE the uninstall progress
        if ($iconPath) { $sc.IconLocation = "$iconPath,0" }
        $sc.Save()
    }
    $uninstSmLnk = Join-Path $startMenuDir 'Uninstall MiOS.lnk'
    & $writeUninstLnk $uninstSmLnk
    Write-Host "  [+] Start Menu: $uninstSmLnk" -ForegroundColor DarkGray
    if ($desktopDir -and (Test-Path $desktopDir)) {
        $uninstDeskLnk = Join-Path $desktopDir 'Uninstall MiOS.lnk'
        & $writeUninstLnk $uninstDeskLnk
        Write-Host "  [+] Desktop: $uninstDeskLnk" -ForegroundColor DarkGray
    }

    foreach ($legacy in @(
        # Per-verb shortcuts no longer in the canonical set (typed verbs):
        'MiOS Build.lnk','MiOS Dashboard.lnk','MiOS Configurator.lnk',
        'MiOS Update.lnk','MiOS Pull.lnk','MiOS Setup.lnk',
        'MiOS Terminal.lnk','MiOS Dev Shell.lnk','MiOS Podman Shell.lnk',
        'Build MiOS.lnk',
        # Redundant-with-MiOS.lnk + typed-verb apps:
        'MiOS-DEV.lnk','MiOS Config.lnk'
    )) {
        foreach ($dir in @($startMenuDir, $desktopDir)) {
            if (-not $dir) { continue }
            $stale = Join-Path $dir $legacy
            if (Test-Path -LiteralPath $stale) {
                try { Remove-Item -LiteralPath $stale -Force -ErrorAction SilentlyContinue; Write-Host "  [+] Removed stale shortcut: $stale" -ForegroundColor DarkGray } catch {}
            }
        }
    }

    # AppUserModelID on both shortcuts so taskbar/Start group correctly.
    if (-not ('MiOS.NativeApp.Aumid' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace MiOS.NativeApp {
    [StructLayout(LayoutKind.Sequential)] public struct PROPERTYKEY { public Guid fmtid; public uint pid; }
    [StructLayout(LayoutKind.Sequential)] public struct PROPVARIANT { public ushort vt; public ushort r1; public ushort r2; public ushort r3; public IntPtr p; public IntPtr p2; }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        [PreserveSig] int GetCount(out uint c);
        [PreserveSig] int GetAt(uint i, out PROPERTYKEY k);
        [PreserveSig] int GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
        [PreserveSig] int SetValue(ref PROPERTYKEY k, ref PROPVARIANT v);
        [PreserveSig] int Commit();
    }
    public static class Aumid {
        [DllImport("shell32.dll", CharSet=CharSet.Unicode, PreserveSig=false)]
        public static extern void SHGetPropertyStoreFromParsingName(string p, IntPtr b, int f, ref Guid g, out IPropertyStore o);
        [DllImport("ole32.dll", PreserveSig=false)]
        public static extern void PropVariantClear(ref PROPVARIANT v);
        public static void Set(string lnk, string id) {
            Guid ipsGuid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
            IPropertyStore ps;
            SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref ipsGuid, out ps);
            try {
                PROPERTYKEY pk = new PROPERTYKEY { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
                IntPtr s = Marshal.StringToCoTaskMemUni(id);
                PROPVARIANT pv = new PROPVARIANT { vt = 31, p = s };
                try { ps.SetValue(ref pk, ref pv); ps.Commit(); }
                finally { PropVariantClear(ref pv); }
            } finally { Marshal.FinalReleaseComObject(ps); }
        }
    }
}
'@ -Language CSharp -ErrorAction SilentlyContinue
    }
    if ('MiOS.NativeApp.Aumid' -as [type]) {
        try {
            $_aumid = Get-MiosTomlValue -Section 'apps' -Key 'aumid' -Default 'MiOS.Workstation'
            # Stamp AumID on every canonical shortcut (4-set: MiOS,
            # MiOS-WIN, MiOS Help, Uninstall MiOS) so all MiOS app
            # windows group under one taskbar / Start tile.
            $_allShortcuts = @()
            foreach ($lnkName in @('MiOS.lnk','MiOS-WIN.lnk','MiOS Help.lnk','Uninstall MiOS.lnk')) {
                $_smPath = Join-Path $startMenuDir $lnkName
                if (Test-Path -LiteralPath $_smPath) { $_allShortcuts += $_smPath }
                if ($desktopDir) {
                    $_dkPath = Join-Path $desktopDir $lnkName
                    if (Test-Path -LiteralPath $_dkPath) { $_allShortcuts += $_dkPath }
                }
            }
            foreach ($lnk in $_allShortcuts) {
                try { [MiOS.NativeApp.Aumid]::Set($lnk, $_aumid) } catch {}
            }
            Write-Host "  [+] AppUserModelID = $_aumid stamped on $($_allShortcuts.Count) shortcuts (hub + per-verb)." -ForegroundColor DarkGray
        } catch {
            Write-Host "  [!] AppUserModelID stamp failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # Add/Remove Programs registration -- HKCU so non-admin runs work.
    try {
        $uninstKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MiOS'
        if (-not (Test-Path $uninstKey)) { New-Item -Path $uninstKey -Force | Out-Null }
        $_arTag = Get-MiosTomlValue -Section 'branding' -Key 'tagline_app' -Default (Get-MiosTomlValue -Section 'branding' -Key 'tagline' -Default 'My Personal Operating System')
        Set-ItemProperty -Path $uninstKey -Name 'DisplayName'     -Value ('MiOS - ' + $_arTag) -Force
        Set-ItemProperty -Path $uninstKey -Name 'DisplayVersion'  -Value ('v' + (Get-MiosReleaseVersion -LocalRoot $miosRoot)) -Force
        Set-ItemProperty -Path $uninstKey -Name 'Publisher'       -Value 'mios-dev' -Force
        Set-ItemProperty -Path $uninstKey -Name 'InstallLocation' -Value $miosRoot -Force
        Set-ItemProperty -Path $uninstKey -Name 'URLInfoAbout'    -Value (Get-MiosTomlValue -Section 'branding' -Key 'about_url' -Default 'https://github.com/mios-dev/mios') -Force
        if ($iconPath) { Set-ItemProperty -Path $uninstKey -Name 'DisplayIcon' -Value $iconPath -Force }
        Set-ItemProperty -Path $uninstKey -Name 'NoModify' -Value 1 -Type DWord -Force
        Set-ItemProperty -Path $uninstKey -Name 'NoRepair' -Value 1 -Type DWord -Force
        # UninstallString: prefer the full M:\MiOS\bin\uninstall.ps1 if
        # build-mios.ps1's Install-WindowsBranding has staged it; falls
        # back to a minimum-viable inline removal of the hub + uninstall
        # entry .lnks + registry key when the full uninstaller isn't on
        # disk yet (e.g. half-bootstrapped host).
        $_fullUninst = Join-Path $miosRoot 'bin\uninstall.ps1'
        if (Test-Path -LiteralPath $_fullUninst) {
            $uninstCmd = "$pwshExe -NoProfile -ExecutionPolicy Bypass -File `"$_fullUninst`""
        } else {
            $uninstCmd = "$pwshExe -NoProfile -ExecutionPolicy Bypass -Command `"Remove-Item -LiteralPath '$smLnk','$desktopDir\MiOS.lnk','$startMenuDir\Uninstall MiOS.lnk','$desktopDir\Uninstall MiOS.lnk' -Force -EA SilentlyContinue; Remove-Item -LiteralPath '$uninstKey' -Recurse -Force -EA SilentlyContinue`""
        }
        Set-ItemProperty -Path $uninstKey -Name 'UninstallString' -Value $uninstCmd -Force
        Write-Host "  [+] Add/Remove Programs entry registered (HKCU\...\Uninstall\MiOS)." -ForegroundColor DarkGray
    } catch {
        Write-Host "  [!] Uninstall key write failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    # Best-effort Pin to Start (Win10 only; Win11 has no programmatic verb).
    try {
        $shellApp = New-Object -ComObject Shell.Application
        $folderObj = $shellApp.Namespace($startMenuDir)
        $itemObj = $folderObj.ParseName('MiOS.lnk')
        $pinVerb = $itemObj.Verbs() | Where-Object { ($_.Name -replace '&','') -match '^(Pin to Start|Pin to taskbar)$' } | Select-Object -First 1
        if ($pinVerb) {
            $pinVerb.DoIt()
            Write-Host "  [+] MiOS pinned to Start menu." -ForegroundColor Green
        } else {
            $os = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
            if ($os -match 'Windows 11') {
                Write-Host "  [i] Windows 11 removed programmatic Pin-to-Start. Right-click MiOS in Start search -> Pin to Start." -ForegroundColor DarkGray
            }
        }
    } catch {}

    Write-Host "  [+] MiOS installed as a native Windows app." -ForegroundColor Green
}

function Install-MiOSServiceShortcuts {
    $publishCSV = [string](Get-MiosTomlValue -Section 'desktop.start_menu' -Key 'publish' -Default 'forge,cockpit,code_server,hermes_workspace,searxng,hermes_dashboard,guacamole_web')
    $publish = @($publishCSV -split '[,\s\[\]"'']+' | Where-Object { $_ })
    if (-not $publish -or $publish.Count -eq 0) {
        Write-Host "  [-] [desktop.start_menu].publish empty -- no service shortcuts created." -ForegroundColor DarkGray
        return $false
    }

    $startMenuDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\podman-MiOS-DEV'
    if (-not (Test-Path $startMenuDir)) { New-Item -ItemType Directory -Path $startMenuDir -Force | Out-Null }

    $created = 0
    foreach ($key in $publish) {
        $portKey = [string](Get-MiosTomlValue -Section 'desktop.start_menu' -Key "${key}_port_key" -Default $key)
        $port    = [int](Get-MiosTomlValue -Section 'ports' -Key $portKey -Default 0)
        if ($port -lt 1) {
            Write-Host "  [-] skip '$key' -- [ports].$portKey unresolved" -ForegroundColor DarkGray
            continue
        }
        $label  = [string](Get-MiosTomlValue -Section 'desktop.start_menu' -Key "${key}_label"  -Default $key)
        $scheme = [string](Get-MiosTomlValue -Section 'desktop.start_menu' -Key "${key}_scheme" -Default 'http')
        $url    = "${scheme}://localhost:${port}/"

        # .url Internet shortcut format: plain INI body, opens in default
        # browser when launched from Start Menu. shell32.dll,14 is the
        # generic globe icon Windows uses for unbranded web shortcuts.
        $urlPath = Join-Path $startMenuDir "$label (MiOS-DEV).url"
        $body = "[InternetShortcut]`r`nURL=$url`r`nIconFile=$env:SystemRoot\System32\shell32.dll`r`nIconIndex=14`r`n"
        try {
            Set-Content -Path $urlPath -Value $body -Encoding ASCII -Force
            $created++
        } catch {
            Write-Host "  [!] $label : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Write-Host "  [+] $created MiOS service shortcuts created in $startMenuDir" -ForegroundColor Green
    return $true
}

function Get-MiosVendorContent {
    [CmdletBinding()] param(
        [Parameter(Mandatory)] [string] $RelPath
    )
    if ($Script:MiosRawBase -match '^[A-Za-z]:') {
        try {
            $localPath = Join-Path $Script:MiosRawBase "usr/share/mios/$RelPath"
            if (Test-Path $localPath) {
                return [IO.File]::ReadAllText($localPath, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {
            throw "Get-MiosVendorContent (local): cannot resolve '$RelPath' from '$localPath'. Underlying: $($_.Exception.Message)"
        }
    }
    try {
        $cb  = [int][double]::Parse((Get-Date -UFormat %s))
        $url = "$($Script:MiosRawBase)/usr/share/mios/$RelPath" + "?cb=$cb"
        $headers = @{ 'Cache-Control' = 'no-cache, no-store, max-age=0'; 'Pragma' = 'no-cache' }
        $resp = Invoke-WebRequest -Uri $url -Headers $headers -UseBasicParsing -ErrorAction Stop
        if ($resp.Content -is [byte[]]) {
            return [System.Text.Encoding]::UTF8.GetString($resp.Content)
        }
        return [string]$resp.Content
    } catch {
        throw "Get-MiosVendorContent: cannot resolve '$RelPath' from raw.githubusercontent.com mios.git origin/main. MiOS self-replication requires reachable origin -- no local fallback (per operator: 'ORIGIN = web entries/repos only'). Underlying: $($_.Exception.Message)"
    }
}

# Resolved at script-load time so downstream functions
# (Install-MiOSFastfetch, Install-MiOSPowerShellProfile, the self-heal
# base64-encoders below) see them in $Script: scope.  All three pull
# fresh from mios.git so the install ALWAYS reflects current upstream.
$Script:MiosBrandingTxt     = Get-MiosVendorContent 'branding/mios.txt'
$Script:MiosFastfetchConfig = Get-MiosVendorContent 'fastfetch/config.jsonc'
$Script:MiosOmpJson         = Get-MiosVendorContent 'oh-my-posh/mios.omp.json'
$Script:MiosTmuxTheme       = try { Get-MiosVendorContent 'tmux/mios-theme.tmux.conf' } catch { $null }
$Script:MiosTmuxKeys        = try { Get-MiosVendorContent 'tmux/mios-keys.tmux.conf' } catch { $null }

function Install-MiOSTerminalExtras {
    $isPs7 = $PSVersionTable.PSEdition -eq 'Core' -and $PSVersionTable.PSVersion.Major -ge 7
    $pwshExe = $null
    if (-not $isPs7) {
        foreach ($c in @("$env:ProgramFiles\PowerShell\7\pwsh.exe",
                         "$env:ProgramW6432\PowerShell\7\pwsh.exe")) {
            if ($c -and (Test-Path -LiteralPath $c)) { $pwshExe = $c; break }
        }
        if (-not $pwshExe) {
            $cmd = Get-Command pwsh.exe -ErrorAction SilentlyContinue
            if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) { $pwshExe = $cmd.Source }
        }
    }

    $modulesScript = @'
$ErrorActionPreference = 'Continue'
try { Import-Module PackageManagement -ErrorAction SilentlyContinue -Force } catch {}
try { Import-Module PowerShellGet     -ErrorAction SilentlyContinue -Force } catch {}
try {
    $nuget = Get-PackageProvider -Name NuGet -ListAvailable -ErrorAction SilentlyContinue |
             Sort-Object Version -Descending | Select-Object -First 1
    if (-not $nuget -or $nuget.Version -lt [Version]'2.8.5.201') {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force -ErrorAction SilentlyContinue | Out-Null
    }
} catch {}
try { Set-PSRepository -Name 'PSGallery' -InstallationPolicy Trusted -ErrorAction SilentlyContinue } catch {}
$psModules = @('Terminal-Icons', 'posh-git', 'CompletionPredictor', 'Microsoft.WinGet.CommandNotFound')
foreach ($mod in $psModules) {
    $have = Get-Module -ListAvailable -Name $mod -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($have) {
        [Console]::WriteLine("  [+] PS module already present: $mod $($have.Version)")
        continue
    }
    try {
        Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber -SkipPublisherCheck -ErrorAction Stop
        [Console]::WriteLine("  [+] Installed PS module: $mod")
    } catch {
        [Console]::WriteLine("  [!] $mod install failed: $($_.Exception.Message)")
    }
}
'@

    if ($isPs7) {
        & ([scriptblock]::Create($modulesScript))
    } elseif ($pwshExe) {
        Write-Host "  [*] PS 5.1 host detected -- trampolining module install through pwsh 7 ($pwshExe)" -ForegroundColor DarkGray
        $bytes = [Text.Encoding]::Unicode.GetBytes($modulesScript)
        $enc = [Convert]::ToBase64String($bytes)
        & $pwshExe -NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc
    } else {
        Write-Host "  [!] PS 5.1 host and no pwsh 7 found -- skipping CompletionPredictor/WinGet.CommandNotFound." -ForegroundColor Yellow
        Write-Host "      Install pwsh 7 then re-run irm|iex to pick up these modules." -ForegroundColor DarkGray
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  [!] winget not available; skipping CLI extras." -ForegroundColor Yellow
        return
    }
    $wingetTools = @()
    $tomlFetchOk = $false
    $tomlSource  = 'fresh system repository SSOT'
    $tomlText    = Resolve-MiosVendorTomlText
    try {
        if (-not $tomlText) { throw 'no toml source resolved' }
        # Regex-extract `[packages.windows] ... pkgs = [ ... ]`. Multiline
        # DOTALL across the TOML section. Stop at the next `[section]`
        # header so we don't accidentally swallow [packages.dev_vm_essentials]
        # right below.
        $rx = '(?ms)^\[packages\.windows\]\s*$.*?^\s*pkgs\s*=\s*\[(?<list>.*?)\]\s*$'
        $m  = [regex]::Match($tomlText, $rx)
        if ($m.Success) {
            # Strip TOML inline comments PER LINE first, then split by
            # comma. Doing it the other way around lets `# comment` text
            # bleed into the next entry because PS regex `$` without (?m)
            # matches end-of-string, eating across newlines.
            $stripped = ($m.Groups['list'].Value -split "`n" |
                         ForEach-Object { ($_ -replace '#.*$', '').Trim() }) -join ' '
            $wingetTools = @(
                $stripped -split ',' |
                ForEach-Object {
                    $s = $_.Trim().Trim('"', "'", ' ', "`t", "`r", "`n")
                    if ($s) { $s }
                }
            )
            if ($wingetTools.Count -gt 0) { $tomlFetchOk = $true }
        }
    } catch {
        Write-Host "  [!] Failed to fetch [packages.windows] from mios.toml: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "      Falling back to a minimal hardcoded set; re-run after fixing the network for the full SSOT list." -ForegroundColor DarkGray
    }
    if (-not $tomlFetchOk) {
        $wingetTools = @(
            'Git.Git', 'Microsoft.PowerShell', 'Microsoft.WSL',
            'Microsoft.WindowsTerminal', '7zip.7zip',
            'Microsoft.VCRedist.2015+.x64'
        )
    } else {
        Write-Host "  [+] Sourced $($wingetTools.Count) winget packages from $tomlSource [packages.windows]" -ForegroundColor DarkGray
    }
    foreach ($pkg in $wingetTools) {
        try {
            $probe = & winget list --id $pkg --exact 2>$null
            if ($LASTEXITCODE -eq 0 -and (($probe -join "`n") -match [regex]::Escape($pkg))) {
                Write-Host "  [+] winget package already present: $pkg" -ForegroundColor DarkGray
                continue
            }
            & winget install --id $pkg --silent --accept-package-agreements --accept-source-agreements --source winget 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  [+] Installed winget package: $pkg" -ForegroundColor Green
            }
        } catch {}
    }
    Install-MiOSTmuxProfile | Out-Null
}

function Install-MiOSLLVMMinGW {
    # Provision the SSOT-selected Windows interoperability toolchain only.
    # MiOS-DEV owns compilation and tests; this host step never runs cargo,
    # rustc, or a project build. Verify the linker and import-library tools
    # together so a partial package registration cannot claim readiness.
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  [!] winget not available; skipping LLVM-MinGW toolchain." -ForegroundColor Yellow
        return
    }
    $_enable = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'install_llvm_mingw' -Default 'true')
    if ($_enable -notin @('true', '1', 'yes')) {
        Write-Host "  [-] install_llvm_mingw is disabled; skipping LLVM-MinGW toolchain." -ForegroundColor DarkGray
        return
    }
    $_llvmPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'llvm_mingw_pkg' -Default 'MartinStorsjo.LLVM-MinGW.MSVCRT')
    Write-Host "  [*] Ensuring Windows LLVM-MinGW tooling ($_llvmPkg) for provisioning/handoff..." -ForegroundColor Cyan
    try {
        $_clang = $null
        foreach ($_attempt in 1..2) {
            # Registration alone is not proof: a partially-failed install
            # registers the package without shipping binaries, and winget then
            # reports "already installed, no upgrade". Verify the linker file
            # and heal with --force when it is missing.
            $_clang = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\${_llvmPkg}_*" -Recurse -Filter 'x86_64-w64-mingw32-clang.exe' -ErrorAction SilentlyContinue |
                Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'llvm-dlltool.exe') -PathType Leaf } | Select-Object -First 1
            if ($_clang -and $_clang.FullName) { break }
            & winget install --id $_llvmPkg --exact --silent --force --accept-package-agreements --accept-source-agreements --source winget 2>&1 | ForEach-Object { Write-Host "  [LLVM-MinGW] $_" }
            if ($LASTEXITCODE -ne 0) { Write-Host "  [!] LLVM-MinGW provisioning attempt $_attempt exited $LASTEXITCODE; rechecking binaries." -ForegroundColor Yellow }
        }
        if (-not ($_clang -and $_clang.FullName)) {
            $_clang = Get-ChildItem -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\${_llvmPkg}_*" -Recurse -Filter 'x86_64-w64-mingw32-clang.exe' -ErrorAction SilentlyContinue |
                Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'llvm-dlltool.exe') -PathType Leaf } | Select-Object -First 1
        }
        if ($_clang -and $_clang.FullName) {
            $_binDir = Split-Path -Parent $_clang.FullName
            if (($env:Path -split ';') -notcontains $_binDir) { $env:Path = "$_binDir;$env:Path" }
            Write-Host "  [+] LLVM-MinGW toolchain ready: $_binDir" -ForegroundColor Green
        } else {
            Write-Host "  [!] LLVM-MinGW linker/import-library tools remain incomplete after provisioning; the optional host toolchain is unavailable." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [!] LLVM-MinGW install failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Update-MiOSOhMyPosh {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  [!] winget not available; cannot install oh-my-posh." -ForegroundColor Yellow
        return $false
    }
    # TOML-first -- oh-my-posh winget ID from mios.toml [bootstrap.prereqs].ohmyposh_pkg
    $_ompPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'ohmyposh_pkg' -Default 'JanDeDobbeleer.OhMyPosh')
    Write-Host "  [*] Installing/upgrading oh-my-posh ($_ompPkg) via winget..." -ForegroundColor Cyan
    try {
        if (Get-Command oh-my-posh -ErrorAction SilentlyContinue) {
            & winget upgrade --id $_ompPkg --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        } else {
            & winget install --id $_ompPkg --silent --accept-package-agreements --accept-source-agreements --source winget 2>&1 | Out-Null
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [+] oh-my-posh installed/upgraded." -ForegroundColor Green
            return $true
        }
        Write-Host "  [!] winget exit code $LASTEXITCODE -- oh-my-posh may not be latest." -ForegroundColor Yellow
    } catch {
        Write-Host "  [!] oh-my-posh install/upgrade failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    return $false
}

function Update-MiOSPSReadLine {
    try {
        $current = Get-Module -ListAvailable -Name PSReadLine | Sort-Object Version -Descending | Select-Object -First 1
        if ($current -and $current.Version -ge [version]'2.3.5') {
            Write-Host "  [+] PSReadLine $($current.Version) already meets oh-my-posh's requirements." -ForegroundColor DarkGray
            return $true
        }
        if (-not (Get-Command Install-Module -ErrorAction SilentlyContinue)) {
            Write-Host "  [!] PowerShellGet not available; cannot bump PSReadLine. oh-my-posh init may emit warnings." -ForegroundColor Yellow
            return $false
        }
        Write-Host "  [*] Installing/updating PSReadLine to 2.3.5+..." -ForegroundColor Cyan
        # Trust PSGallery so install doesn't prompt.
        try { Set-PSRepository -Name 'PSGallery' -InstallationPolicy Trusted -ErrorAction SilentlyContinue } catch {}
        Install-Module -Name PSReadLine -MinimumVersion 2.3.5 -Scope CurrentUser -Force -AllowClobber -SkipPublisherCheck -ErrorAction Stop
        Write-Host "  [+] PSReadLine updated." -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  [!] PSReadLine update failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
}

function Install-MiOSFastfetch {
    $alreadyInstalled = $false
    if (Get-Command fastfetch -ErrorAction SilentlyContinue) { $alreadyInstalled = $true }
    if (-not $alreadyInstalled) {
        try {
            $probe = & winget list --id Fastfetch-cli.Fastfetch --exact 2>$null
            if ($LASTEXITCODE -eq 0 -and ($probe -join "`n") -match 'Fastfetch-cli\.Fastfetch') {
                $alreadyInstalled = $true
            }
        } catch {}
    }
    if (-not $alreadyInstalled) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host "  [!] winget not available; cannot auto-install fastfetch." -ForegroundColor Yellow
            Write-Host "      Install manually: https://github.com/fastfetch-cli/fastfetch/releases" -ForegroundColor DarkGray
        } else {
            # TOML-first -- fastfetch winget ID from mios.toml [bootstrap.prereqs].fastfetch_pkg
            $_ffPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'fastfetch_pkg' -Default 'Fastfetch-cli.Fastfetch')
            Write-Host "  [*] Installing fastfetch ($_ffPkg) via winget..." -ForegroundColor Cyan
            try {
                & winget install --id $_ffPkg --silent --accept-package-agreements --accept-source-agreements --source winget 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [+] fastfetch installed." -ForegroundColor Green
                } else {
                    Write-Host "  [!] winget exit code $LASTEXITCODE -- fastfetch may not be installed." -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [!] winget install fastfetch failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "  [+] fastfetch already installed." -ForegroundColor DarkGray
    }

    if (-not (Test-Path 'M:\')) {
        throw "Install-MiOSFastfetch: M:\ not provisioned -- Initialize-DataDisk should have created it before this point."
    }
    $miosRoot = 'M:\MiOS'
    $ffDir = Join-Path $miosRoot 'fastfetch'
    if (-not (Test-Path $ffDir)) { New-Item -ItemType Directory -Path $ffDir -Force | Out-Null }
    $logoPath   = Join-Path $ffDir 'mios.txt'
    $configPath = Join-Path $ffDir 'config.jsonc'

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($logoPath, $Script:MiosBrandingTxt, $utf8NoBom)

    # Bake the actual logo path into the JSONC -- escape backslashes
    # for the JSON string ("M:\\MiOS\\fastfetch\\mios.txt").
    $logoPathJson = $logoPath -replace '\\', '\\'
    $resolvedConfig = $Script:MiosFastfetchConfig -replace '__MIOS_LOGO__', $logoPathJson

    # Source-of-truth path point on Windows: the deployed config uses
    # /usr/share/mios/branding/mios.txt (the Linux path). Rewrite to
    # the Windows-friendly Logo path that we just staged.
    $resolvedConfig = $resolvedConfig -replace '/usr/share/mios/branding/mios\.txt', $logoPathJson

    $_ffPalette = @(
        @{ Token='logo_color';   VendorAnsi='blue';   JsonField='"1"' }
        @{ Token='keys_color';   VendorAnsi='yellow'; JsonField='"keys"' }
        @{ Token='title_color';  VendorAnsi='white';  JsonField='"title"' }
        @{ Token='output_color'; VendorAnsi='cyan';   JsonField='"output"' }
    )
    $_ansiNames = @('black','red','green','yellow','blue','magenta','cyan','white','default')
    foreach ($_pe in $_ffPalette) {
        $_resolved = Get-MiosTomlValue -Section 'theme.fastfetch' -Key $_pe.Token -Default $_pe.VendorAnsi
        if ($_resolved -and $_resolved -ne $_pe.VendorAnsi -and $_ansiNames -contains $_resolved.ToLower()) {
            # Replace `"1": "blue"` -> `"1": "<resolved>"` (or the
            # equivalent for keys/title/output).  Field-anchored
            # regex so we don't accidentally rewrite ANSI strings
            # elsewhere in the JSONC.
            $_rx = ($_pe.JsonField + '\s*:\s*"') + [regex]::Escape($_pe.VendorAnsi) + '"'
            $_rep = $_pe.JsonField + ': "' + $_resolved + '"'
            $resolvedConfig = [regex]::Replace($resolvedConfig, $_rx, $_rep)
        }
    }
    [System.IO.File]::WriteAllText($configPath, $resolvedConfig, $utf8NoBom)

    if ((Test-Path $configPath) -and (Test-Path $logoPath)) {
        Write-Host "  [+] fastfetch theme staged: $configPath" -ForegroundColor DarkGray
        Write-Host "  [+] MiOS branding logo:    $logoPath" -ForegroundColor DarkGray
    } else {
        Write-Host "  [!] fastfetch theme staging FAILED at $ffDir" -ForegroundColor Yellow
    }
    return $configPath
}

function Install-MiOSOhMyPoshTheme {
    # Stage mios.omp.json at M:\MiOS\themes\ -- M:\-everywhere
    # invariant; Initialize-DataDisk creates M:\ before this runs.
    if (-not (Test-Path 'M:\')) {
        throw "Install-MiOSOhMyPoshTheme: M:\ not provisioned -- Initialize-DataDisk should have created it before this point."
    }
    $miosRoot = 'M:\MiOS'
    $themesDir = Join-Path $miosRoot 'themes'
    if (-not (Test-Path $themesDir)) { New-Item -ItemType Directory -Path $themesDir -Force | Out-Null }
    $ompPath = Join-Path $themesDir 'mios.omp.json'
    Set-Content -Path $ompPath -Value $Script:MiosOmpJson -Encoding UTF8
    if (Test-Path -LiteralPath $ompPath) {
        $sz = (Get-Item $ompPath).Length
        Write-Host "  [+] mios.omp.json staged: $ompPath ($sz bytes)" -ForegroundColor DarkGray
    } else {
        Write-Host "  [!] mios.omp.json write FAILED at $ompPath" -ForegroundColor Yellow
    }
    return $ompPath
}

function Install-MiOSTmuxProfile {
    # Stages canonical MiOS tmux configuration (.tmux.conf) from SSOT theme and keys.
    # Staged to $env:USERPROFILE\.tmux.conf, M:\MiOS\tmux\tmux.conf, and %LOCALAPPDATA%\tmux\tmux.conf.
    # If a tmux server is active on Windows, reloads it dynamically with the SSOT theme.
    $themeContent = $Script:MiosTmuxTheme
    if (-not $themeContent) {
        foreach ($_tc in @(
            'M:\usr\share\mios\tmux\mios-theme.tmux.conf',
            'C:\MiOS\usr\share\mios\tmux\mios-theme.tmux.conf',
            'C:\mios-bootstrap\usr\share\mios\tmux\mios-theme.tmux.conf'
        )) {
            if ($_tc -and (Test-Path -LiteralPath $_tc)) {
                try { $themeContent = [System.IO.File]::ReadAllText($_tc, [System.Text.UTF8Encoding]::new($false)); break } catch {}
            }
        }
    }
    if (-not $themeContent) {
        $p = Get-MiosPalette
        $bg = if ($p.bg) { $p.bg } else { '#282262' }
        $fg = if ($p.fg) { $p.fg } else { '#E7DFD3' }
        $accent = if ($p.accent) { $p.accent } else { '#1A407F' }
        $cursor = if ($p.cursor) { $p.cursor } else { '#F35C15' }
        $green = if ($p.ansi_2_green) { $p.ansi_2_green } else { '#3E7765' }
        $muted = if ($p.muted) { $p.muted } else { '#948E8E' }
        $themeContent = @"
# AI-hint: Fallback MiOS Canonical Tmux Theme from palette SSOT
set -g status on
set -g status-interval 2
set -g status-position bottom
set -g status-style "bg=default,fg=$fg"
set -g window-style "bg=default,fg=$fg"
set -g window-active-style "bg=default,fg=$fg"
set -g status-justify left
set -g window-status-separator ""
set -g default-terminal "tmux-256color"
set -as terminal-features ",xterm*:RGB"
set -as terminal-overrides ",xterm*:Tc"
set -g pane-border-style "fg=$muted"
set -g pane-active-border-style "fg=$cursor"
set -g pane-border-lines heavy
set -g mode-style "bg=$accent,fg=$fg"
set -g message-style "bg=$accent,fg=$fg"
set -g message-command-style "bg=$bg,fg=$cursor"
set -g status-left-length 50
set -g status-left "#[fg=$accent,bg=default]#[fg=$fg,bg=$accent,bold]  MiOS #[fg=$accent,bg=$green]#[fg=$bg,bg=$green,bold]  #S #[fg=$green,bg=default] "
set -g window-status-format "#[fg=$muted,bg=default]  #I  #W  "
set -g window-status-current-format "#[fg=$cursor,bg=default]#[fg=$bg,bg=$cursor,bold] #I  #W #[fg=$cursor,bg=default]"
set -g status-right-length 100
set -g status-right "#[fg=$accent,bg=default]#[fg=$fg,bg=$accent]  %H:%M #[fg=$accent,bg=$green]#[fg=$bg,bg=$green,bold]  %Y-%m-%d #[fg=$green,bg=$cursor]#[fg=$bg,bg=$cursor,bold]  #H #[fg=$cursor,bg=default]"
"@
    }

    $keysContent = $Script:MiosTmuxKeys
    if (-not $keysContent) {
        foreach ($_kc in @(
            'M:\usr\share\mios\tmux\mios-keys.tmux.conf',
            'C:\MiOS\usr\share\mios\tmux\mios-keys.tmux.conf',
            'C:\mios-bootstrap\usr\share\mios\tmux\mios-keys.tmux.conf'
        )) {
            if ($_kc -and (Test-Path -LiteralPath $_kc)) {
                try { $keysContent = [System.IO.File]::ReadAllText($_kc, [System.Text.UTF8Encoding]::new($false)); break } catch {}
            }
        }
    }
    if (-not $keysContent) {
        $keysContent = @'
unbind-key -a -T prefix
set -g prefix C-b
set -g prefix2 None
bind-key C-b send-prefix
set -s escape-time 50
set -g repeat-time 500
set -g history-limit 50000
set -g mouse on
bind-key t new-window
bind-key h select-pane -L
bind-key j select-pane -D
bind-key k select-pane -U
bind-key l select-pane -R
bind-key s split-window -v
bind-key v split-window -h
bind-key n next-window
bind-key p previous-window
bind-key w choose-tree -Zw
bind-key z resize-pane -Z
bind-key y copy-mode
bind-key d detach-client
bind-key b send-prefix
'@
    }

    $winTmuxConf = @"
# AI-hint: MiOS Windows Native Tmux Configuration rendered from mios.toml SSOT
# =====================================================================
# Terminal Capabilities & Extended Keys (Windows / Blink / ConPTY / SSH)
# =====================================================================
set -g default-terminal "tmux-256color"
set -as terminal-features ",xterm*:RGB"
set -as terminal-overrides ",xterm*:Tc"
set -s extended-keys on
set -gw xterm-keys on
set -g mouse on

# =====================================================================
# MiOS Canonical Tmux Theme
# =====================================================================
$themeContent

# =====================================================================
# MiOS Canonical Tmux Keybindings
# =====================================================================
$keysContent

# =====================================================================
# Blink Mobile & iOS Touch Combos (BTab / Shift+Tab pass-through)
# =====================================================================
bind-key -n BTab send-keys Escape "[Z"
bind-key Tab send-keys Escape "[Z"
bind-key `` send-keys Escape "[Z"
bind-key -n M-BTab send-keys Escape "[Z"
"@

    $targetDirs = @(
        $env:USERPROFILE,
        (Join-Path $env:LOCALAPPDATA 'tmux'),
        (Join-Path $env:LOCALAPPDATA 'MiOS\tmux')
    )
    if (Test-Path -LiteralPath 'M:\') {
        $targetDirs += 'M:\MiOS\tmux'
    }

    $primaryConf = Join-Path $env:USERPROFILE '.tmux.conf'
    foreach ($td in $targetDirs) {
        if (-not (Test-Path -LiteralPath $td)) {
            New-Item -ItemType Directory -Path $td -Force -ErrorAction SilentlyContinue | Out-Null
        }
        $targetFile = if ($td -eq $env:USERPROFILE) { $primaryConf } else { Join-Path $td 'tmux.conf' }
        try {
            [System.IO.File]::WriteAllText($targetFile, $winTmuxConf, [System.Text.UTF8Encoding]::new($false))
            Write-Host "  [+] tmux configuration staged: $targetFile" -ForegroundColor DarkGray
        } catch {
            Write-Host "  [!] Failed to write tmux config to $targetFile : $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    if (Test-Path -LiteralPath 'M:\MiOS\tmux') {
        try {
            [System.IO.File]::WriteAllText('M:\MiOS\tmux\mios-theme.tmux.conf', $themeContent, [System.Text.UTF8Encoding]::new($false))
            [System.IO.File]::WriteAllText('M:\MiOS\tmux\mios-keys.tmux.conf', $keysContent, [System.Text.UTF8Encoding]::new($false))
        } catch {}
    }

    if (Get-Process -Name tmux -ErrorAction SilentlyContinue) {
        try {
            & tmux.exe source-file $primaryConf 2>$null
            Write-Host "  [+] Live tmux server reloaded with MiOS SSOT theme." -ForegroundColor Green
        } catch {}
    }

    return $primaryConf
}

function Install-MiOSPowerShellProfile {
    $miosPsRoot = if (Test-Path 'M:\') { 'M:\MiOS\powershell' }
                  else { Join-Path $env:USERPROFILE 'MiOS-bootstrap\powershell' }
    if (-not (Test-Path $miosPsRoot)) { New-Item -ItemType Directory -Path $miosPsRoot -Force | Out-Null }
    $miosProfileScript = Join-Path $miosPsRoot 'profile.ps1'

    # Resolve $PROFILE.CurrentUserAllHosts even if outer script blocks
    # have torn down standard host context.
    $profilePath = $PROFILE.CurrentUserAllHosts
    if (-not $profilePath) { $profilePath = $PROFILE }
    if (-not $profilePath) {
        $profilePath = Join-Path $env:USERPROFILE 'Documents\PowerShell\profile.ps1'
    }
    $profileDir = Split-Path -Parent $profilePath
    if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Path $profileDir -Force | Out-Null }

    $marker  = '# >>> MiOS oh-my-posh init >>>'
    $endMark = '# <<< MiOS oh-my-posh init <<<'
    $existing = if (Test-Path $profilePath) { Get-Content $profilePath -Raw } else { '' }

    $ompBlobBase64    = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script:MiosOmpJson))
    $ffConfigBase64   = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script:MiosFastfetchConfig))
    $ffLogoBase64     = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script:MiosBrandingTxt))
    $_miosCols    = Get-MiosTomlValue -Section 'terminal' -Key 'cols'            -Default 80
    $_miosRows    = Get-MiosTomlValue -Section 'terminal' -Key 'rows'            -Default 20
    $_miosScroll  = Get-MiosTomlValue -Section 'terminal' -Key 'scrollback_rows' -Default 9000
    $_miosFrameW  = Get-MiosTomlValue -Section 'terminal' -Key 'frame_width'     -Default $_miosCols
    $_miosFrameH  = Get-MiosTomlValue -Section 'terminal' -Key 'frame_height'    -Default ($_miosRows - 1)
    $_miosRightMargin = Get-MiosTomlValue -Section 'terminal' -Key 'right_margin' -Default 0
    # Font family + size sourced from mios.toml [theme.font] -- baked
    # as the install-time default for the dashboard's "font" field
    # (Show-MiosDashboard re-reads at runtime so configurator edits
    # also flow through; this is the cold-start fallback).
    $_themeFontFace = Get-MiosTomlValue -Section 'theme.font' -Key 'family' -Default 'GeistMono Nerd Font Mono'
    if ([string]::IsNullOrWhiteSpace($_themeFontFace)) { $_themeFontFace = 'GeistMono Nerd Font Mono' }
    $_themeFontSize = Get-MiosTomlValue -Section 'theme.font' -Key 'size' -Default 12
    if (-not ($_themeFontSize -is [int]) -or $_themeFontSize -lt 6 -or $_themeFontSize -gt 72) { $_themeFontSize = 12 }

    $_eulaTomlText = Resolve-MiosTomlText
    $_eulaLines = @(
        '',
        '  MiOS -- My Personal Operating System',
        '  Immutable Fedora AI Workstation (pronounced "MyOS")',
        '',
        '  By invoking any MiOS entry point you acknowledge:',
        '    * MiOS is provided AS IS, NO WARRANTY (MIT license).',
        '    * Build/install scripts can modify your system globally',
        '      (registry, env vars, fonts, WT settings, WSL distros, M:\ partition).',
        '    * Telemetry: NONE (no data leaves the host without explicit operator action).',
        '    * Full text: M:\AGREEMENTS.md  +  M:\LICENSE',
        '',
        '  Continued use of this terminal is treated as acknowledgment.',
        ''
    )
    $_eulaDisplayMs = 600
    if ($_eulaTomlText) {
        $_euSec = [regex]::Match($_eulaTomlText, '(?ms)^\[messages\.eula\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
        if ($_euSec.Success) {
            $_euBody = $_euSec.Groups['body'].Value
            $_msM = [regex]::Match($_euBody, '(?m)^\s*display_ms\s*=\s*(\d+)')
            if ($_msM.Success) { $_eulaDisplayMs = [int]$_msM.Groups[1].Value }
            $_lnsM = [regex]::Match($_euBody, '(?ms)^\s*lines\s*=\s*\[(?<arr>.*?)^\]')
            if ($_lnsM.Success) {
                # PS 5.1-safe sentinel ([char]0x01) for \\ -- the
                # `` `u{0001} `` form is PS 7-only and leaks the literal
                # placeholder when the bootstrap runs in PS 5.1.
                $_eulaBs = [string][char]0x01 + 'BS' + [string][char]0x01
                $_parsed = @()
                foreach ($_lm in [regex]::Matches($_lnsM.Groups['arr'].Value, '"((?:[^"\\]|\\.)*)"')) {
                    # Unescape JSON-style \" \\ \n \t in the toml string
                    $_v = $_lm.Groups[1].Value
                    $_v = $_v -replace '\\\\', $_eulaBs
                    $_v = $_v -replace '\\"',   '"'
                    $_v = $_v -replace '\\n',   "`n"
                    $_v = $_v -replace '\\t',   "`t"
                    $_v = $_v -replace [regex]::Escape($_eulaBs), '\'
                    $_parsed += $_v
                }
                if ($_parsed.Count -gt 0) { $_eulaLines = $_parsed }
            }
        }
    }
    # Convert to a PS array literal that's safe to embed in the
    # double-quoted heredoc.  Single-quote each line and escape
    # single-quotes by doubling them (PS '' inside a single-quoted
    # string = literal ').
    $_eulaArrayLiteral = '@(' + (
        ($_eulaLines | ForEach-Object {
            "'" + ($_ -replace "'", "''") + "'"
        }) -join ', '
    ) + ')'

    $miosScriptBody = @"

if (`$Global:MiosProfileLoaded) { return }
`$Global:MiosProfileLoaded = `$true

try { & chcp.com 65001 *> `$null } catch {}
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(`$false) } catch {}
try { [Console]::InputEncoding  = [System.Text.UTF8Encoding]::new(`$false) } catch {}
try { `$OutputEncoding = [System.Text.UTF8Encoding]::new(`$false) } catch {}

if (`$env:MIOS_APP_CONTEXT) {
    try {
        `$_curW = [Console]::WindowWidth
        if (`$_curW -gt $_miosCols) {
            [Console]::SetWindowSize($_miosCols, $_miosRows)
            [Console]::SetBufferSize($_miosCols, $_miosScroll)
        } else {
            [Console]::SetBufferSize($_miosCols, $_miosScroll)
            [Console]::SetWindowSize($_miosCols, $_miosRows)
        }
    } catch {}
}
if (`$env:MIOS_APP_CONTEXT) {
    try {
        Add-Type -Namespace MiosWin -Name N -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool MoveWindow(System.IntPtr hWnd, int x, int y, int w, int h, bool repaint);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out System.Drawing.Rectangle rect);
'@ -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        `$_hwnd = [MiosWin.N]::GetConsoleWindow()
        `$_r = New-Object System.Drawing.Rectangle
        [MiosWin.N]::GetWindowRect(`$_hwnd, [ref]`$_r) | Out-Null
        `$_w = `$_r.Width  - `$_r.X
        `$_h = `$_r.Height - `$_r.Y
        `$_cur = [System.Windows.Forms.Cursor]::Position
        `$_s   = [System.Windows.Forms.Screen]::FromPoint(`$_cur).WorkingArea
        `$_x = `$_s.X + [int](([math]::Max(0, `$_s.Width  - `$_w)) / 2)
        `$_y = `$_s.Y + [int](([math]::Max(0, `$_s.Height - `$_h)) / 2)
        [MiosWin.N]::MoveWindow(`$_hwnd, `$_x, `$_y, `$_w, `$_h, `$true) | Out-Null
    } catch {}
}

if (`$true) {

    foreach (`$mod in @('Terminal-Icons','posh-git','CompletionPredictor','Microsoft.WinGet.CommandNotFound')) {
        if (Get-Module -ListAvailable -Name `$mod -ErrorAction SilentlyContinue) {
            try { Import-Module `$mod -ErrorAction SilentlyContinue } catch {}
        }
    }

    try {
        `$latestPSRL = Get-Module -ListAvailable -Name PSReadLine |
                       Sort-Object Version -Descending | Select-Object -First 1
        if (`$latestPSRL -and `$latestPSRL.Version -ge [version]'2.3.5') {
            Import-Module PSReadLine -RequiredVersion `$latestPSRL.Version -Force -ErrorAction SilentlyContinue
        }
    } catch {}

    `$miosArtifactRoot = 'M:\MiOS'
    if (-not (Test-Path -LiteralPath `$miosArtifactRoot)) {
        Write-Host "  [!] M:\MiOS not found -- re-run the irm|iex bootstrap to provision M:\." -ForegroundColor Yellow
    }
    function _MiosSelfHeal {
        param([string]`$RelDir, [string]`$FileName, [string]`$Blob)
        `$dir = Join-Path `$miosArtifactRoot `$RelDir
        if (-not (Test-Path `$dir)) { New-Item -ItemType Directory -Path `$dir -Force | Out-Null }
        `$path = Join-Path `$dir `$FileName
        if (-not (Test-Path -LiteralPath `$path)) {
            try { [System.IO.File]::WriteAllBytes(`$path, [Convert]::FromBase64String(`$Blob)) } catch { return `$null }
        }
        return `$path
    }

    # oh-my-posh config -- probe canonical paths, self-heal if missing.
    `$miosOmp = `$null
    `$ompCands = @()
    if (`$env:MIOS_OMP_JSON) { `$ompCands += `$env:MIOS_OMP_JSON }
    `$ompCands += @(
        'M:\MiOS\themes\mios.omp.json',
        'M:\usr\share\mios\oh-my-posh\mios.omp.json'
    )
    # C:\* deliberately excluded -- M:\-everywhere invariant
    # (operator: EVERYTHING to M:\, no LOCALAPPDATA / C:\MiOS leaks).
    foreach (`$c in `$ompCands) {
        if (`$c -and (Test-Path -LiteralPath `$c)) { `$miosOmp = `$c; break }
    }
    if (-not `$miosOmp) {
        `$miosOmp = _MiosSelfHeal 'themes' 'mios.omp.json' '$ompBlobBase64'
    }

    # -- Framed MiOS dashboard (mirrors mios-dashboard.sh from mios.git) -
    # 80-col fixed frame, centered ASCII logo, framed fastfetch info.
    # Gated on WT_SESSION since the +-+ box-drawing only renders
    # properly in WT (conhost / VS Code embedded shell mangles it).
    function Show-MiosDashboard {
        param([string]`$ConfigPath, [string]`$LogoPath)
        `$_widthA = 0; `$_widthB = 0
        for (`$_i = 0; `$_i -lt 5; `$_i++) {
            `$_widthB = `$_widthA
            `$_winC = try { [Console]::WindowWidth } catch { 0 }
            `$_winR = try { `$Host.UI.RawUI.WindowSize.Width } catch { 0 }
            `$_widthA = if (`$_winC -gt 0 -and `$_winR -gt 0) { [math]::Min(`$_winC, `$_winR) }
                        elseif (`$_winC -gt 0) { `$_winC }
                        elseif (`$_winR -gt 0) { `$_winR }
                        else { 0 }
            if (`$_widthA -gt 0 -and `$_widthA -eq `$_widthB) { break }
            if (`$_i -lt 4) { Start-Sleep -Milliseconds 50 }
        }
        `$_winWNow = if (`$_widthA -gt 0) { `$_widthA } else { $_miosFrameW }
        `$WIDTH = `$_winWNow - $_miosRightMargin
        if ($_miosFrameW -gt 0 -and `$WIDTH -gt $_miosFrameW) {
            `$WIDTH = $_miosFrameW
        }
        if (`$WIDTH -lt 20) { `$WIDTH = [math]::Max(20, `$_winWNow) }
        `$INNER = `$WIDTH - 4
        `$TL=[char]0x256d; `$TR=[char]0x256e; `$BL=[char]0x2570; `$BR=[char]0x256f; `$LT=[char]0x251c; `$RT=[char]0x2524; `$V=[char]0x2502; `$H=[char]0x2500

        `$_esc      = [char]27
        `$_FrameC   = "`$_esc[34m"
        `$_FrameR   = "`$_esc[0m"

        function _Strip { param(`$s) `$s -replace '\x1b\[[0-9;]*m','' }
        function _Frame {
            param([string]`$Line)
            `$visible = _Strip `$Line
            if (`$visible.Length -gt `$INNER) {
                # Truncate with ellipsis preserving ANSI prefix.
                `$Line = `$Line.Substring(0, [math]::Min(`$Line.Length, `$INNER + (`$Line.Length - `$visible.Length) - 1)) + [char]0x2026
                `$visible = _Strip `$Line
            }
            `$pad = ' ' * [math]::Max(0, `$INNER - `$visible.Length)
            "`$_FrameC`$V`$_FrameR `$Line`$pad`$_FrameC `$V`$_FrameR"
        }
        function _Center {
            param([string]`$Line)
            `$visible = _Strip `$Line
            `$totalPad = [math]::Max(0, `$INNER - `$visible.Length)
            `$lpad = ' ' * [math]::Floor(`$totalPad / 2)
            `$rpad = ' ' * (`$totalPad - [math]::Floor(`$totalPad / 2))
            "`$_FrameC`$V`$_FrameR `$lpad`$Line`$rpad`$_FrameC `$V`$_FrameR"
        }

        `$_compact = $_miosFrameH -lt 25
        # Reserve rows for top + divider + divider + hints + bottom.
        # Compact hints = 1 row; full hints = 7 rows.
        `$_hintsRows  = if (`$_compact) { 1 } else { 7 }
        `$_overhead   = 1 + 1 + 1 + `$_hintsRows + 1   # top + 2 dividers + hints + bottom
        # Logo + fastfetch share whatever's left.
        `$_contentBudget = [math]::Max(2, $_miosFrameH - `$_overhead)
        # In compact mode skip the multi-line ASCII logo entirely; in
        # full mode allocate up to half the content budget to the logo.
        `$_logoBudget = if (`$_compact) { 1 } else { [math]::Min(11, [math]::Floor(`$_contentBudget / 2)) }
        `$_ffBudget   = `$_contentBudget - `$_logoBudget

        # Read mios.toml ONCE up-front so [dashboard].title (here),
        # [dashboard].rows + [theme.font] (further down) all read from
        # the same in-memory copy.  No fallback to other paths -- the
        # canonical layout is M:\etc\mios (host overlay) > M:\usr\share
        # (vendor on M:\).
        `$_dashTomlText = `$null
        foreach (`$_tc in @('M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
            if (Test-Path -LiteralPath `$_tc) {
                try { `$_dashTomlText = [IO.File]::ReadAllText(`$_tc, (New-Object System.Text.UTF8Encoding(`$false))); break } catch {}
            }
        }

        # Top frame.
        Write-Host (`$TL + (`$H * (`$WIDTH - 2)) + `$TR) -ForegroundColor Blue
        if (`$_compact) {
            `$title = 'MiOS  --  My Personal Operating System'
            if (`$_dashTomlText) {
                `$_titleM = [regex]::Match(`$_dashTomlText, '(?ms)^\[dashboard\]\s*\r?\n.*?^\s*title\s*=\s*"([^"]+)"')
                if (`$_titleM.Success) { `$title = `$_titleM.Groups[1].Value }
            }
            Write-Host (_Center `$title) -ForegroundColor Blue
        }
        elseif (Test-Path -LiteralPath `$LogoPath) {
            `$logoLines = @(Get-Content -LiteralPath `$LogoPath) | Where-Object { `$_ -ne `$null }
            # Cap to logo budget so we don't overflow on small frame_height.
            if (`$logoLines.Count -gt `$_logoBudget) {
                `$logoLines = `$logoLines[0..([math]::Max(0, `$_logoBudget - 1))]
            }
            `$maxLen = 0
            foreach (`$ll in `$logoLines) {
                `$len = (_Strip `$ll).Length
                if (`$len -gt `$maxLen) { `$maxLen = `$len }
            }
            `$blockLPad = ' ' * [math]::Max(0, [math]::Floor((`$INNER - `$maxLen) / 2))
            foreach (`$ll in `$logoLines) {
                `$stripped = _Strip `$ll
                `$rPad = ' ' * [math]::Max(0, `$maxLen - `$stripped.Length)
                Write-Host (_Frame (`$blockLPad + `$ll + `$rPad)) -ForegroundColor Blue
            }
        }
        # Divider.
        Write-Host (`$LT + (`$H * (`$WIDTH - 2)) + `$RT) -ForegroundColor Blue

        `$_dashCache = @{}
        `$_DashGetField = {
            param([string]`$_k, [string]`$_fontFam, [int]`$_fontSz)
            switch (`$_k) {
                'host_os' {
                    if (-not `$_dashCache.ContainsKey('_os')) {
                        `$_dashCache['_os'] = try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { `$null }
                    }
                    `$_o = `$_dashCache['_os']
                    `$_cap = if (`$_o -and `$_o.Caption) { ((((((`$_o.Caption -replace 'Microsoft\s*','') -replace '\s+for\s+Workstations','') -replace '\s+Insider\s+Preview','') -replace '\s*\(64-?bit\)','') -replace '\s*N\s+Edition','')).Trim() } else { 'Windows' }
                    return "`$env:USERNAME@`$env:COMPUTERNAME -- `$_cap".Trim()
                }
                'cpu' {
                    if (-not `$_dashCache.ContainsKey('_cpu')) {
                        `$_dashCache['_cpu'] = try { Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1 } catch { `$null }
                    }
                    `$_c = `$_dashCache['_cpu']
                    if (-not `$_c) { return 'CPU --' }
                    `$_n = (`$_c.Name -replace '\s+@.*','' -replace '\s+Processor','' -replace '\(R\)','' -replace '\(TM\)','').Trim()
                    `$_clk = if (`$_c.MaxClockSpeed) { [math]::Round(`$_c.MaxClockSpeed / 1000.0, 2) } else { 0 }
                    `$_co  = `$_c.NumberOfLogicalProcessors
                    return "CPU `$_n `${_clk}GHz (`${_co}c)"
                }
                {`$_ -in 'gpu_discrete','gpu_integrated'} {
                    if (-not `$_dashCache.ContainsKey('_gpus')) {
                        `$_dashCache['_gpus'] = try { @(Get-CimInstance Win32_VideoController -ErrorAction Stop) } catch { @() }
                    }
                    `$_gs = `$_dashCache['_gpus']
                    if (-not `$_gs -or `$_gs.Count -eq 0) { return 'GPU --' }
                    if (`$_k -eq 'gpu_discrete') {
                        `$_g = `$_gs | Where-Object { `$_.Name -match 'NVIDIA|GeForce|RTX|GTX|Quadro|Radeon RX|Radeon Pro' } | Select-Object -First 1
                        if (-not `$_g) { `$_g = `$_gs | Sort-Object @{e={`$_.AdapterRAM};Descending=`$true} | Select-Object -First 1 }
                    } else {
                        `$_g = `$_gs | Where-Object { `$_.Name -match 'Radeon\(TM\) Graphics|Intel.*Graphics|UHD Graphics' } | Select-Object -First 1
                        if (-not `$_g) { return '' }
                    }
                    if (-not `$_g) { return 'GPU --' }
                    `$_n = (`$_g.Name -replace 'NVIDIA GeForce ','' -replace 'NVIDIA ','' -replace '\(R\)','' -replace '\(TM\)','').Trim()
                    `$_vr = if (`$_g.AdapterRAM) { [math]::Round(([uint32]`$_g.AdapterRAM) / 1GB, 1) } else { 0 }
                    if (`$_vr -le 0) { return "GPU `$_n" }
                    return "GPU `$_n `${_vr}GiB"
                }
                'ram' {
                    if (-not `$_dashCache.ContainsKey('_os')) {
                        `$_dashCache['_os'] = try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { `$null }
                    }
                    `$_o = `$_dashCache['_os']
                    if (-not `$_o) { return 'RAM --' }
                    `$_tot = [math]::Round(([int64]`$_o.TotalVisibleMemorySize) / 1MB, 1)
                    `$_use = [math]::Round((([int64]`$_o.TotalVisibleMemorySize - [int64]`$_o.FreePhysicalMemory)) / 1MB, 1)
                    `$_pct = if (`$_o.TotalVisibleMemorySize -gt 0) { [math]::Round(((`$_use / `$_tot) * 100), 0) } else { 0 }
                    return "RAM `${_use} / `${_tot}GiB (`${_pct}%)"
                }
                'swap' {
                    if (-not `$_dashCache.ContainsKey('_pf')) {
                        `$_dashCache['_pf'] = try { Get-CimInstance Win32_PageFileUsage -ErrorAction Stop } catch { `$null }
                    }
                    `$_p = @(`$_dashCache['_pf'])
                    if (-not `$_p -or `$_p.Count -eq 0 -or -not `$_p[0]) { return 'Swap --' }
                    `$_tot = [math]::Round((`$_p | Measure-Object AllocatedBaseSize -Sum).Sum / 1024.0, 1)
                    `$_use = [math]::Round((`$_p | Measure-Object CurrentUsage -Sum).Sum / 1024.0, 1)
                    `$_pct = if (`$_tot -gt 0) { [math]::Round(((`$_use / `$_tot) * 100), 0) } else { 0 }
                    return "Swap `${_use} / `${_tot}GiB (`${_pct}%)"
                }
                {`$_ -match '^disk_([a-zA-Z])$'} {
                    `$_dl = `$_.Substring(5,1).ToUpper()
                    `$_v  = try { Get-Volume -DriveLetter `$_dl -ErrorAction Stop } catch { `$null }
                    if (-not `$_v) { return "`${_dl}: --" }
                    `$_tot = [math]::Round(`$_v.Size / 1GB, 1)
                    `$_use = [math]::Round((`$_v.Size - `$_v.SizeRemaining) / 1GB, 1)
                    `$_pct = if (`$_v.Size -gt 0) { [math]::Round((((`$_v.Size - `$_v.SizeRemaining) / `$_v.Size) * 100), 0) } else { 0 }
                    return "`${_dl}: `${_use} / `${_tot}GiB (`${_pct}%)"
                }
                'kernel' {
                    return 'Kernel ' + [System.Environment]::OSVersion.Version.ToString()
                }
                'shell' {
                    return 'Shell pwsh ' + `$PSVersionTable.PSVersion.ToString()
                }
                'font' {
                    return "Font `$_fontFam `${_fontSz}pt"
                }
                'uptime' {
                    if (-not `$_dashCache.ContainsKey('_os')) {
                        `$_dashCache['_os'] = try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { `$null }
                    }
                    `$_o = `$_dashCache['_os']
                    if (-not `$_o -or -not `$_o.LastBootUpTime) { return 'Up --' }
                    `$_up = (Get-Date) - `$_o.LastBootUpTime
                    `$_upd = [math]::Floor(`$_up.TotalDays)
                    return "Up `${_upd}d `$(`$_up.Hours)h `$(`$_up.Minutes)m"
                }
                default { return '' }
            }
        }

        # Parse [dashboard].rows + [theme.font] from the mios.toml text
        # we already loaded above for [dashboard].title.  Vendor defaults
        # baked in below if parsing fails (cold first-run before M:\
        # overlay is staged).
        `$_dashRows  = `$null
        `$_dashFontF = '$_themeFontFace'
        `$_dashFontS = $_themeFontSize
        if (`$_dashTomlText) {
            `$_dashSec = [regex]::Match(`$_dashTomlText, '(?ms)^\[dashboard\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
            if (`$_dashSec.Success) {
                `$_rowsM = [regex]::Match(`$_dashSec.Groups['body'].Value, '(?ms)^\s*rows\s*=\s*\[(?<arr>.*?)^\]')
                if (`$_rowsM.Success) {
                    `$_rowsBody = `$_rowsM.Groups['arr'].Value
                    `$_rowMatches = [regex]::Matches(`$_rowsBody, '\[(?<r>[^\]]*)\]')
                    `$_dashRows = @()
                    foreach (`$_rm in `$_rowMatches) {
                        `$_fields = @(`$_rm.Groups['r'].Value -split ',' | ForEach-Object { `$_.Trim().Trim('"',"'",' ',"``t","``r","``n") } | Where-Object { `$_ })
                        if (`$_fields.Count -gt 0) { `$_dashRows += ,`$_fields }
                    }
                    if (`$_dashRows.Count -eq 0) { `$_dashRows = `$null }
                }
            }
            # [theme.font] -- pick up runtime font overrides for the font field.
            `$_fontSec = [regex]::Match(`$_dashTomlText, '(?ms)^\[theme\.font\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
            if (`$_fontSec.Success) {
                `$_fb = `$_fontSec.Groups['body'].Value
                `$_fm = [regex]::Match(`$_fb, '(?m)^\s*family\s*=\s*"([^"]+)"')
                if (`$_fm.Success) { `$_dashFontF = `$_fm.Groups[1].Value }
                `$_sm = [regex]::Match(`$_fb, '(?m)^\s*size\s*=\s*(\d+)')
                if (`$_sm.Success) { `$_dashFontS = [int]`$_sm.Groups[1].Value }
            }
        }
        if (-not `$_dashRows) {
            `$_dashRows = @(@('host_os'),@('cpu','gpu_discrete'),@('ram','swap'),@('disk_c','disk_m'),@('kernel','shell','font'))
        }

        foreach (`$_row in `$_dashRows) {
            `$_n = @(`$_row).Count
            if (`$_n -le 0) { continue }
            # Equal-width columns within the framed inner area.
            `$_colW = [math]::Floor((`$INNER - (`$_n - 1) * 2) / `$_n)
            if (`$_colW -lt 8) { `$_colW = 8 }
            `$_cells = @()
            foreach (`$_fk in `$_row) {
                `$_val = ''
                try {
                    `$_val = & `$_DashGetField `$_fk `$_dashFontF `$_dashFontS
                } catch {
                    `$_val = "`$_fk : err"
                }
                if (-not `$_val) { `$_val = '' }
                if (`$_val.Length -gt `$_colW) {
                    `$_val = `$_val.Substring(0, [math]::Max(1, `$_colW - 1)) + [char]0x2026
                }
                `$_cells += `$_val.PadRight(`$_colW)
            }
            try {
                Write-Host (_Frame ((`$_cells -join '  ').TrimEnd()))
            } catch {
                # Frame helper failed (rare -- ANSI strip or PadRight
                # overflow); print a placeholder so the render flow
                # continues and the divider/hints/bottom frame land.
                Write-Host (_Frame "  [dashboard row render failed]")
            }
        }
        function _ProbeEp {
            param([string]`$Url, [int]`$TimeoutMs = 1500)
            try {
                `$req = [System.Net.WebRequest]::Create(`$Url)
                `$req.Timeout = `$TimeoutMs
                `$req.Method  = "GET"
                `$req.ServicePoint.Expect100Continue = `$false
                try { `$resp = `$req.GetResponse(); `$resp.Close() } catch {
                    if (`$_.Exception -is [System.Net.WebException] -and
                        `$_.Exception.Response -ne `$null) { return `$true }
                    return `$false
                }
                return `$true
            } catch { return `$false }
        }
        function _ServiceCell {
            param([string]`$Name, [int]`$Port, [string]`$Probe = "/",
                  [bool]`$Https = `$false)
            `$scheme = if (`$Https) { "https" } else { "http" }
            `$url = "`${scheme}://localhost:`${Port}`${Probe}"
            `$up  = _ProbeEp -Url `$url
            `$dot = if (`$up) { "`$_esc[32m*`$_esc[0m" } else { "`$_esc[90m-`$_esc[0m" }
            `$nm = `$Name.PadRight(11)
            "`$dot `$nm :`$(`$Port.ToString().PadRight(5))"
        }
        function _ServiceRow {
            param([string]`$L, [string]`$R)
            Write-Host (_Frame ("  `$L  `$R")) -ForegroundColor Blue
        }
        Write-Host (`$LT + (`$H * (`$WIDTH - 2)) + `$RT) -ForegroundColor Blue
        Write-Host (_Frame "  `$_esc[1m`$_esc[36mAI surface`$_esc[0m") -ForegroundColor Blue
        `$_c_agent  = _ServiceCell -Name "Agent-Pipe"  -Port $(Get-MiosTomlValue -Section 'ports' -Key 'agent_pipe' -Default 8640) -Probe "/health"
        `$_c_herm   = _ServiceCell -Name "Hermes"      -Port $(Get-MiosTomlValue -Section 'ports' -Key 'hermes' -Default 8642) -Probe "/health"
        `$_c_pg     = _ServiceCell -Name "pgvector"    -Port $(Get-MiosTomlValue -Section 'ports' -Key 'pgvector' -Default 8432)
        `$_c_dash   = _ServiceCell -Name "Dash-AI"     -Port $(Get-MiosTomlValue -Section 'ports' -Key 'hermes_dashboard' -Default 8119)
        `$_c_llm    = _ServiceCell -Name "LLM-Light"   -Port $(Get-MiosTomlValue -Section 'ports' -Key 'llm_light' -Default 8450)
        _ServiceRow `$_c_agent `$_c_herm
        _ServiceRow `$_c_pg    `$_c_dash
        _ServiceRow `$_c_llm   (' ' * 20)
        Write-Host (_Frame "  `$_esc[1m`$_esc[36mUser surface`$_esc[0m") -ForegroundColor Blue
        `$_c_webui  = _ServiceCell -Name "WebUI"       -Port $(Get-MiosTomlValue -Section 'ports' -Key 'open_webui' -Default 8033)
        `$_c_cock   = _ServiceCell -Name "Cockpit"     -Port $(Get-MiosTomlValue -Section 'ports' -Key 'cockpit' -Default 8090) -Https `$true
        `$_c_code   = _ServiceCell -Name "Code"        -Port $(Get-MiosTomlValue -Section 'ports' -Key 'code_server' -Default 8800)
        `$_c_forge  = _ServiceCell -Name "Forge"       -Port $(Get-MiosTomlValue -Section 'ports' -Key 'forge_http' -Default 8300)
        `$_c_srch   = _ServiceCell -Name "Search"      -Port $(Get-MiosTomlValue -Section 'ports' -Key 'searxng' -Default 8899)
        `$_c_ttyb   = _ServiceCell -Name "ttyd-bash"   -Port $(Get-MiosTomlValue -Section 'ports' -Key 'ttyd_bash' -Default 8681)
        `$_c_ttyp   = _ServiceCell -Name "ttyd-PS"     -Port $(Get-MiosTomlValue -Section 'ports' -Key 'ttyd_powershell' -Default 8682)
        _ServiceRow `$_c_webui `$_c_cock
        _ServiceRow `$_c_code  `$_c_forge
        _ServiceRow `$_c_srch  `$_c_ttyb
        _ServiceRow `$_c_ttyp  (' ' * 20)

        `$_verbDefs = @(
            @{ name='build';  desc='open mios.html, save, then build the OCI image' },
            @{ name='config'; desc='edit mios.toml in the HTML configurator (no build)' },
            @{ name='dash';   desc='show this dashboard (framed banner + fastfetch info)' },
            @{ name='dev';    desc='enter the MiOS-DEV podman machine' },
            @{ name='pull';   desc='sync M:\ overlay to origin/main' },
            @{ name='update'; desc='re-run the bootstrap (cache-busted)' },
            @{ name='help';   desc='list every verb' }
        )
        try {
            `$_tomlCands = @(
                (Join-Path `$env:USERPROFILE '.config\mios\mios.toml'),
                'M:\etc\mios\mios.toml',
                'M:\usr\share\mios\mios.toml'
            )
            foreach (`$_tc in `$_tomlCands) {
                if (`$_tc -and (Test-Path -LiteralPath `$_tc)) {
                    `$_tt = Get-Content -LiteralPath `$_tc -Raw -ErrorAction SilentlyContinue
                    if (-not `$_tt) { continue }
                    `$_vb = [regex]::Match(`$_tt, '(?ms)^\[verbs\]\s*\r?\n(.*?)(?=^\[|\z)')
                    if (`$_vb.Success) {
                        `$_parsed = @()
                        foreach (`$_ln in (`$_vb.Groups[1].Value -split "``n")) {
                            `$_pm = [regex]::Match(`$_ln, '^\s*([a-z][a-z0-9_-]*)\s*=\s*\{[^}]*description\s*=\s*"([^"]+)"')
                            if (`$_pm.Success) { `$_parsed += @{ name=`$_pm.Groups[1].Value; desc=`$_pm.Groups[2].Value } }
                        }
                        if (`$_parsed.Count -gt 0) { `$_verbDefs = `$_parsed; break }
                    }
                }
            }
        } catch {}
        Write-Host (`$LT + (`$H * (`$WIDTH - 2)) + `$RT) -ForegroundColor Blue
        if (`$_compact) {
            `$_hint1 = ((`$_verbDefs | ForEach-Object { `$_.name }) -join '  ')
            Write-Host (_Center `$_hint1) -ForegroundColor DarkCyan
        } else {
            `$_maxName = ((`$_verbDefs | ForEach-Object { `$_.name.Length }) | Measure-Object -Maximum).Maximum
            foreach (`$_v in `$_verbDefs) {
                `$_pad = ' ' * (`$_maxName - `$_v.name.Length + 2)
                Write-Host (_Frame ('  mios ' + `$_v.name + `$_pad + '-- ' + `$_v.desc)) -ForegroundColor DarkCyan
            }
        }

        # Bottom frame.
        Write-Host (`$BL + (`$H * (`$WIDTH - 2)) + `$BR) -ForegroundColor Blue
    }

    if (Get-Command oh-my-posh -ErrorAction SilentlyContinue) {
        `$_ompShell = if (`$PSVersionTable.PSEdition -eq 'Desktop') { 'powershell' } else { 'pwsh' }
        `$ompInit = if (`$miosOmp -and (Test-Path -LiteralPath `$miosOmp)) {
            (oh-my-posh init `$_ompShell --config `$miosOmp --print) -join "``n"
        } else {
            (oh-my-posh init `$_ompShell --print) -join "``n"
        }
        if (`$ompInit) {
            `$ompInit = [regex]::Replace(`$ompInit, 'Get-PSReadLineKeyHandler\s+(?!-)([A-Za-z][\w+]*)', 'Get-PSReadLineKeyHandler -Chord ''`$1''')
            try { Invoke-Expression `$ompInit } catch {}
        }
    }
}

`$Script:MiosBootstrapRaw = '$($Script:MiosBootstrapRaw)'

function mios-build {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]`$Args)
    `$skipConfig = `$Args -contains '-SkipConfig'
    `$skipPull   = `$Args -contains '-SkipPull'
    `$forwardArgs = @(`$Args | Where-Object { `$_ -notin @('-SkipConfig','-SkipPull') })

    # -- Step 1 + 2: configurator pass ------------------------------
    if (-not `$skipConfig) {
        `$cfgHtml = `$null
        foreach (`$c in @(
            'M:\usr\share\mios\configurator\mios.html',
            'M:\MiOS\usr\share\mios\configurator\mios.html'
        )) { if (Test-Path -LiteralPath `$c) { `$cfgHtml = `$c; break } }
        if (`$cfgHtml) {
            `$cfgMtimeBefore = (Get-Item -LiteralPath `$cfgHtml).LastWriteTimeUtc
            Write-Host ''
            Write-Host '  [1/4] Opening MiOS configurator in your browser...' -ForegroundColor Cyan
            Write-Host ('         '+`$cfgHtml) -ForegroundColor DarkGray
            Write-Host '         Edit values, click Save -> the browser writes mios.toml' -ForegroundColor DarkGray
            Write-Host '         to your Downloads folder (file:// URLs cannot write back).' -ForegroundColor DarkGray
            try { Start-Process `$cfgHtml | Out-Null } catch {}
            Write-Host ''
            Write-Host '  Press Enter when you''ve saved the configurator (or to skip the edit pass)...' -ForegroundColor Yellow -NoNewline
            `$null = Read-Host
        } else {
            Write-Host '  [!] Configurator HTML not found on M:\ -- skipping edit pass.' -ForegroundColor Yellow
            Write-Host '      Run `mios pull` first to seed the overlay.' -ForegroundColor DarkGray
        }

        Write-Host ''
        Write-Host '  [2/4] Scanning Downloads for edited config files...' -ForegroundColor Cyan
        `$dlDir = Join-Path `$env:USERPROFILE 'Downloads'
        if (Test-Path -LiteralPath `$dlDir) {
            `$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            # Operator edits are a host overlay. Keep the fetched vendor
            # mios.toml in M:\usr\share\mios intact for layered resolution.
            `$tomlSrc = Get-ChildItem -LiteralPath `$dlDir -Filter 'mios*.toml' -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if (`$tomlSrc) {
                `$tomlDst = 'M:\etc\mios\mios.toml'
                `$tomlPar = Split-Path -Parent `$tomlDst
                if (-not (Test-Path -LiteralPath `$tomlPar)) {
                    New-Item -ItemType Directory -Path `$tomlPar -Force | Out-Null
                }
                Copy-Item -LiteralPath `$tomlSrc.FullName -Destination `$tomlDst -Force
                Write-Host ('         [+] '+`$tomlSrc.Name+' -> '+`$tomlDst) -ForegroundColor Green
                `$archive = Join-Path `$dlDir (`$tomlSrc.BaseName+'.imported-'+`$stamp+'.toml')
                Move-Item -LiteralPath `$tomlSrc.FullName -Destination `$archive -Force
            } else {
                Write-Host '         [-] no mios*.toml in Downloads -- using existing overlay' -ForegroundColor DarkGray
            }
            # Also pick up an edited HTML configurator (rare; the
            # configurator emits TOML by default but operators may save
            # a hand-edited HTML).
            `$htmlSrc = Get-ChildItem -LiteralPath `$dlDir -Filter '*mios*.html' -File -ErrorAction SilentlyContinue |
                Where-Object { `$_.Name -notmatch '\.imported-' } |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if (`$htmlSrc) {
                `$htmlDst = 'M:\usr\share\mios\configurator\mios.html'
                `$htmlPar = Split-Path -Parent `$htmlDst
                if (-not (Test-Path -LiteralPath `$htmlPar)) {
                    New-Item -ItemType Directory -Path `$htmlPar -Force | Out-Null
                }
                Copy-Item -LiteralPath `$htmlSrc.FullName -Destination `$htmlDst -Force
                Write-Host ('         [+] '+`$htmlSrc.Name+' -> '+`$htmlDst) -ForegroundColor Green
                `$archive = Join-Path `$dlDir (`$htmlSrc.BaseName+'.imported-'+`$stamp+'.html')
                Move-Item -LiteralPath `$htmlSrc.FullName -Destination `$archive -Force
            }
        } else {
            Write-Host '         [-] '`$dlDir' does not exist -- skipping promote' -ForegroundColor DarkGray
        }
    }

    if (-not `$skipPull) {
        Write-Host ''
        Write-Host '  [3/4] Syncing M:\ overlay (mios.git + mios-bootstrap)...' -ForegroundColor Cyan
        try { mios-pull } catch { Write-Host "  [!] mios-pull failed: `$(`$_.Exception.Message)" -ForegroundColor Yellow }
    }

    # -- Step 4: ignite the build -----------------------------------
    Write-Host ''
    Write-Host '  [4/4] Running build pipeline (build-mios.ps1)...' -ForegroundColor Cyan
    `$env:MIOS_DASHBOARD_MODE = 'log'
    `$cb = [int][double]::Parse((Get-Date -UFormat %s))
    `$src = Invoke-RestMethod -Uri "`$Script:MiosBootstrapRaw/build-mios.ps1?cb=`$cb" -Headers @{ 'Cache-Control' = 'no-cache' }
    & ([scriptblock]::Create(`$src)) @forwardArgs
}

function mios-update {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]`$Args)
    `$cb = [int][double]::Parse((Get-Date -UFormat %s))
    `$src = Invoke-RestMethod -Uri "`$Script:MiosBootstrapRaw/Get-MiOS.ps1?cb=`$cb" -Headers @{ 'Cache-Control' = 'no-cache' }
    & ([scriptblock]::Create(`$src)) @Args
}

function mios-pull {
    if (-not (Test-Path 'M:\.git')) {
        Write-Host '  [!] M:\ is not a git working tree -- run mios-build first.' -ForegroundColor Yellow
        return
    }
    Push-Location 'M:\'
    try {
        git fetch --depth=1 origin main
        if (`$LASTEXITCODE -eq 0) {
            git reset --hard FETCH_HEAD
            Write-Host '  [+] M:\ overlay synced to origin/main.' -ForegroundColor Green
        } else {
            Write-Host '  [!] git fetch failed -- check network.' -ForegroundColor Yellow
        }
    } finally { Pop-Location }
}

function mios-config {
    `$cfg = if (Test-Path 'M:\usr\share\mios\configurator\mios.html') { 'M:\usr\share\mios\configurator\mios.html' }
           else { `$null }
    if (`$cfg) {
        Start-Process `$cfg
        Write-Host "  [+] Opened `$cfg" -ForegroundColor DarkGray
    } else {
        Write-Host '  [!] configurator not found -- run mios-build to deploy it.' -ForegroundColor Yellow
    }
}

function mios-dev {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]`$Args)
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-Host '  [!] wsl.exe not on PATH -- WSL2 may not be installed.' -ForegroundColor Yellow
        return
    }
    # Probe for the actual on-disk WSL distro name. With the default
    # rename-skipped behavior (MIOS_RENAME_DISTRO unset), the distro is
    # 'podman-MiOS-DEV' (preserved from podman machine init so Podman
    # Desktop can see it). With opt-in rename, it's 'MiOS-DEV'. Either
    # works -- we resolve at call time so the helper survives both modes.
    `$_devDistro = `$null
    try {
        `$_wsl = (& wsl.exe -l -q 2>`$null) -split "`r?`n" |
            ForEach-Object { (`$_ -replace [char]0,'').Trim() } |
            Where-Object { `$_ }
        foreach (`$_cand in @('podman-MiOS-DEV','MiOS-DEV','podman-MiOS-BUILDER','MiOS-BUILDER')) {
            if (`$_wsl -contains `$_cand) { `$_devDistro = `$_cand; break }
        }
    } catch {}
    if (-not `$_devDistro) {
        Write-Host '  [!] No MiOS-DEV / podman-MiOS-DEV WSL distro registered. Run irm|iex one-liner to provision.' -ForegroundColor Yellow
        return
    }
    if (`$Args.Count -eq 0) {
        & (Join-Path `$Global:MiosBin 'mios.cmd') terminal
    } else {
        & wsl.exe -d `$_devDistro --cd / --user mios @Args
    }
}

function mios-mini {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]`$Args)
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        `$_distro = `$null
        foreach (`$_d in @('podman-MiOS-DEV','MiOS-DEV')) {
            try {
                `$_chk = & wsl.exe -d `$_d --user mios -- echo ready 2>`$null
                if (`$LASTEXITCODE -eq 0 -and `$_chk -match 'ready') { `$_distro = `$_d; break }
            } catch {}
        }
        if (`$_distro) {
            & wsl.exe -d `$_distro --cd / --user mios -- /usr/libexec/mios/mios-dashboard.sh --mini @Args
            return
        }
    }
    if (Get-Command Show-MiosDashboard -ErrorAction SilentlyContinue) {
        `$cfg  = if (Test-Path 'M:\MiOS\fastfetch\config.jsonc') { 'M:\MiOS\fastfetch\config.jsonc' } else { '' }
        `$logo = if (Test-Path 'M:\MiOS\fastfetch\mios.txt')      { 'M:\MiOS\fastfetch\mios.txt' }      else { '' }
        Show-MiosDashboard -ConfigPath `$cfg -LogoPath `$logo
    } else {
        if (Get-Command fastfetch -ErrorAction SilentlyContinue) {
            & fastfetch --logo none
        } else {
            Write-Host '  MiOS Mini: WSL distro not running. Start with: mios dev' -ForegroundColor DarkGray
        }
    }
}

function mios-metal {
    if (Get-Command Show-MiosDashboard -ErrorAction SilentlyContinue) {
        `$cfg  = if (Test-Path 'M:\MiOS\fastfetch\config.jsonc') { 'M:\MiOS\fastfetch\config.jsonc' } else { '' }
        `$logo = if (Test-Path 'M:\MiOS\fastfetch\mios.txt')      { 'M:\MiOS\fastfetch\mios.txt' }      else { '' }
        Show-MiosDashboard -ConfigPath `$cfg -LogoPath `$logo
    } else {
        Write-Host '  [!] mios mini: Show-MiosDashboard not loaded.' -ForegroundColor Yellow
    }
}

function mios-dash {
    `$_ascii = `$null
    foreach (`$_p in @('M:\MiOS\fastfetch\mios.txt','M:\usr\share\mios\branding\mios.txt')) {
        if (Test-Path -LiteralPath `$_p) { `$_ascii = `$_p; break }
    }
    if (`$_ascii) {
        Write-Host ''
        foreach (`$_l in (Get-Content -LiteralPath `$_ascii)) {
            Write-Host `$_l -ForegroundColor Blue
        }
        Write-Host ''
    }

    Write-Host '  MiOS -- Full system view' -ForegroundColor Cyan
    Write-Host '  ------------------------' -ForegroundColor DarkCyan

    # Sys specs via fastfetch (full module list, no frame).
    `$_ffCfg = if (Test-Path 'M:\MiOS\fastfetch\config.jsonc') { 'M:\MiOS\fastfetch\config.jsonc' }
              elseif (Test-Path 'M:\usr\share\mios\fastfetch\config.jsonc') { 'M:\usr\share\mios\fastfetch\config.jsonc' }
              else { `$null }
    if (Get-Command fastfetch -ErrorAction SilentlyContinue) {
        if (`$_ffCfg) { & fastfetch -c `$_ffCfg --logo none } else { & fastfetch --logo none }
    } else {
        Write-Host '  [fastfetch unavailable]' -ForegroundColor DarkGray
    }

    # MiOS-DEV service status (Quadlets + portal + dev-VM-essentials).
    # Reads from the running podman-MiOS-DEV WSL distro via wsl.exe.
    Write-Host ''
    Write-Host '  MiOS-DEV services' -ForegroundColor Cyan
    Write-Host '  -----------------' -ForegroundColor DarkCyan
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        `$_distro = `$null
        foreach (`$_d in @('podman-MiOS-DEV','MiOS-DEV')) {
            try {
                `$_chk = & wsl.exe -d `$_d --user mios -- echo ready 2>`$null
                if (`$LASTEXITCODE -eq 0 -and `$_chk -match 'ready') { `$_distro = `$_d; break }
            } catch {}
        }
        if (`$_distro) {
            try {
                & wsl.exe -d `$_distro --user mios -- bash -lc 'systemctl --user list-units --type=service --state=active --no-legend --no-pager 2>/dev/null | head -30; echo ""; echo "graphical-session.target: `$(systemctl --user is-active graphical-session.target 2>/dev/null)"; echo "xdg-desktop-portal.service: `$(systemctl --user is-active xdg-desktop-portal.service 2>/dev/null)"; echo "podman.socket: `$(systemctl --user is-active podman.socket 2>/dev/null)"'
            } catch {
                Write-Host "  [!] failed to query MiOS-DEV services: `$_" -ForegroundColor Yellow
            }
        } else {
            Write-Host '  [MiOS-DEV distro not running -- start with: mios dev]' -ForegroundColor DarkGray
        }
    } else {
        Write-Host '  [wsl.exe not available]' -ForegroundColor DarkGray
    }

    # Podman machine state (Windows host side).
    Write-Host ''
    Write-Host '  Podman machine' -ForegroundColor Cyan
    Write-Host '  --------------' -ForegroundColor DarkCyan
    if (Get-Command podman -ErrorAction SilentlyContinue) {
        try { & podman machine list 2>&1 | Out-Host } catch {}
        try { & podman info --format '  Hostname:   {{.Host.Hostname}}
  Server OS:  {{.Host.OS}}
  CPUs:       {{.Host.CPUs}}
  Memory:     {{.Host.MemTotal}} bytes' 2>`$null } catch {}
    } else {
        Write-Host '  [podman not on PATH]' -ForegroundColor DarkGray
    }
    Write-Host ''
}

function mios-help {
    Write-Host ''
    Write-Host '  MiOS commands' -ForegroundColor Cyan
    Write-Host '  -------------' -ForegroundColor DarkCyan
    Write-Host '  mios <verb>   unified dispatcher (tab-complete supported)' -ForegroundColor White
    Write-Host '                  or use mios-<verb> directly:' -ForegroundColor DarkGray
    Write-Host '  mios build    run the full MiOS OS bootstrap (WSL2 + podman + dev VM)' -ForegroundColor White
    Write-Host '  mios update   re-run Get-MiOS.ps1 (refresh terminal install)' -ForegroundColor White
    Write-Host '  mios pull     git fetch + hard reset M:\ to origin/main' -ForegroundColor White
    Write-Host '  mios config   open the HTML configurator (mios.toml editor)' -ForegroundColor White
    Write-Host '  mios ai       open Open WebUI (rich LLM interface) in your browser' -ForegroundColor White
    Write-Host '  mios dev      wsl into the MiOS-DEV distro (root /, user mios)' -ForegroundColor White
    Write-Host '  mios dash     FULL dashboard: ASCII banner + services + extended sys specs' -ForegroundColor White
    Write-Host '  mios xbox     Xbox VM Secure Boot / XML repair' -ForegroundColor White
    Write-Host '  mios virt     apply optimized VM config + CPU pinning' -ForegroundColor White
    Write-Host '  mios vfio     configure GPU/USB passthrough (Isolation)' -ForegroundColor White
    Write-Host '  mios help     this list' -ForegroundColor White
    Write-Host ''
}

`$Script:MiosKnownVerbs = @('build','update','pull','config','ai','dev','dash','mini','help','code','xbox','virt','vfio','tune','summary','profile','assess','iommu','theme','user')

function mios {
    [CmdletBinding()]
    param(
        [Parameter(Position=0)]
        [string]`$Verb,
        [Parameter(ValueFromRemainingArguments)]
        `$Args
    )
    if (-not `$Verb) { `$Verb = 'help' }
    if (`$Script:MiosKnownVerbs -contains `$Verb.ToLowerInvariant()) {
        `$cmd = "mios-`$(`$Verb.ToLowerInvariant())"
        if (Get-Command `$cmd -ErrorAction SilentlyContinue) {
            & `$cmd @Args
        } else {
            Write-Host "  [!] mios: verb '`$Verb' wrapper not found. Try: mios help" -ForegroundColor Yellow
        }
        return
    }
    # Free-form query -> Hermes-Agent /v1/chat/completions.
    `$_query = (@(`$Verb) + @(`$Args)) -join ' '
    `$_ask = Join-Path `$Global:MiosBin 'mios-ask.ps1'
    if (Test-Path -LiteralPath `$_ask) {
        & `$_ask `$_query
    } else {
        Write-Host "  [!] mios-ask.ps1 not staged. Try: mios help" -ForegroundColor Yellow
    }
}

Register-ArgumentCompleter -CommandName mios -ParameterName Verb -ScriptBlock {
    param(`$cmdName, `$paramName, `$wordToComplete, `$cmdAst, `$fakeBoundParam)
    `$Script:MiosKnownVerbs |
        Where-Object { `$_ -like "`$wordToComplete*" } |
        ForEach-Object { [System.Management.Automation.CompletionResult]::new(`$_, `$_, 'ParameterValue', `$_) }
}

function _MiosResolveStartupVerb {
    `$_cands = @(
        (Join-Path `$env:USERPROFILE '.config\mios\mios.toml'),
        'M:\etc\mios\mios.toml',
        'M:\usr\share\mios\mios.toml'
    )
    foreach (`$_c in `$_cands) {
        if (-not (Test-Path -LiteralPath `$_c)) { continue }
        try {
            `$_t = [IO.File]::ReadAllText(`$_c, (New-Object System.Text.UTF8Encoding(`$false)))
        } catch { continue }
        `$_sec = [regex]::Match(`$_t, '(?ms)^\[terminal\.startup\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
        if (-not `$_sec.Success) { continue }
        `$_body = `$_sec.Groups['body'].Value
        # Per-platform key wins over cross-platform 'verb' key.
        `$_keys = @('windows','verb')
        foreach (`$_k in `$_keys) {
            `$_m = [regex]::Match(`$_body, ('(?m)^\s*' + [regex]::Escape(`$_k) + '\s*=\s*"([^"]*)"'))
            if (`$_m.Success) { return `$_m.Groups[1].Value.Trim() }
        }
    }
    return 'mini'
}

if (-not `$Global:MiosStartupVerbFired -and `$Host.UI.RawUI -and (-not `$env:MIOS_SKIP_MOTD)) {
    `$Global:MiosStartupVerbFired = `$true
    `$_startupVerb = _MiosResolveStartupVerb
    if (`$_startupVerb) {
        try { mios `$_startupVerb } catch {}
    }
}

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
    try { Set-MiosWindow -Mode reading } catch {}
    Start-Sleep -Milliseconds 200
    try {
        & wsl.exe -d `$_dev --user mios -- btop @args
    } finally {
        try { Set-MiosWindow -Mode portal } catch {}
    }
}

# tmux on Windows -> prefer native tmux.exe (arndawg.tmux-windows) if available;
# otherwise dispatch to the dev VM's Linux tmux via WSL.
function tmux {
    `$_native = (Get-Command tmux.exe -CommandType Application -ErrorAction SilentlyContinue)
    if (`$_native) {
        `$_conf = Join-Path `$env:USERPROFILE '.tmux.conf'
        if (-not (Test-Path -LiteralPath `$_conf) -or ((Get-Item `$_conf).Length -lt 100)) {
            foreach (`$_src in @('M:\MiOS\tmux\tmux.conf', (Join-Path `$env:LOCALAPPDATA 'tmux\tmux.conf'), (Join-Path `$env:LOCALAPPDATA 'MiOS\tmux\tmux.conf'))) {
                if (Test-Path -LiteralPath `$_src) {
                    Copy-Item -LiteralPath `$_src -Destination `$_conf -Force -ErrorAction SilentlyContinue
                    break
                }
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
    if (`$_dev) {
        & wsl.exe -d `$_dev --user mios -- tmux @args
        return
    }
    Write-Host '  [!] No native tmux.exe or MiOS-DEV WSL distro found -- cannot run tmux.' -ForegroundColor Yellow
}
"@
    $miosScriptBody += @'

# >>> MiOS native SSOT runtime >>>
$_miosNativeBin = if ($env:MIOS_NATIVE_BIN) { $env:MIOS_NATIVE_BIN } elseif (Test-Path (Join-Path $env:ProgramData 'MiOS\bin\native-binding.json')) { Join-Path $env:ProgramData 'MiOS\bin' } else { $Global:MiosBin }
if (Test-Path (Join-Path $_miosNativeBin 'mios-native-client-setup.ps1')) {
    & (Join-Path $_miosNativeBin 'mios-native-client-setup.ps1') -RuntimeOnly -BinDirectory $_miosNativeBin
    . (Join-Path $_miosNativeBin 'mios-native-shell.ps1') -BinDirectory $_miosNativeBin
    $env:MIOS_OMP_JSON = Join-Path $env:LOCALAPPDATA 'MiOS\themes\mios.omp.json'
    oh-my-posh init pwsh --config $env:MIOS_OMP_JSON | Invoke-Expression
}
# <<< MiOS native SSOT runtime <<<
'@
    $_utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($miosProfileScript, $miosScriptBody, $_utf8Bom)

    # Thin C:\ redirector -- dot-sources the M:\ script.
    $redirector = $miosProfileScript -replace '\\', '\\'
    $block = @"
$marker
# Thin redirector to the canonical MiOS PS profile on M:\.
# Auto-generated by Get-MiOS.ps1 -- regenerated on every bootstrap.
# DO NOT edit between the markers; edit M:\MiOS\powershell\profile.ps1.
if (Test-Path '$miosProfileScript') {
    . '$miosProfileScript'
}
$endMark
"@

    if ($existing -match [regex]::Escape($marker)) {
        $pattern  = "(?s)$([regex]::Escape($marker)).*?$([regex]::Escape($endMark))"
        $safeRepl = $block -replace '\$', '$$$$'
        $existing = [regex]::Replace($existing, $pattern, $safeRepl)
    } else {
        $existing = ($existing.TrimEnd() + "`n`n" + $block + "`n").TrimStart()
    }
    Set-Content -Path $profilePath -Value $existing -Encoding UTF8 -NoNewline
    Write-Host "  [+] MiOS PS profile body: $miosProfileScript" -ForegroundColor Green
    Write-Host "  [+] Redirector at $profilePath" -ForegroundColor DarkGray

    $diagBlock = @"

try {
    `$_diagDir = 'M:\MiOS\diagnostics'
    if (-not (Test-Path -LiteralPath `$_diagDir)) { New-Item -ItemType Directory -Path `$_diagDir -Force | Out-Null }
    `$_diagFile = Join-Path `$_diagDir 'window-width.txt'
    `$_ww = try { [Console]::WindowWidth } catch { '?' }
    `$_bw = try { `$Host.UI.RawUI.BufferSize.Width } catch { '?' }
    `$_wh = try { [Console]::WindowHeight } catch { '?' }
    `$_wt = if (`$env:WT_SESSION) { 'WT' } else { 'conhost-or-other' }
    `$_ts = (Get-Date).ToString('s')
    Add-Content -LiteralPath `$_diagFile -Value ("{0} WindowWidth={1} BufferWidth={2} WindowHeight={3} host={4} pwsh={5}" -f `$_ts, `$_ww, `$_bw, `$_wh, `$_wt, `$PSVersionTable.PSVersion)
} catch {}
# -- end MiOS WindowWidth diagnostic --
"@
    try {
        Add-Content -LiteralPath $miosProfileScript -Value $diagBlock -Encoding UTF8
        Write-Host "  [+] WindowWidth diagnostic appended to $miosProfileScript" -ForegroundColor DarkGray
    } catch {}
}


# -- Status helpers (used by Step-0 + Pass-2) ---------------------------------
# Defined here -- BEFORE Pass-1's Step-0 M:\ block -- so the M:\ provisioning
# code can call Write-Info/Good/Err. Pass-2 (Clear-Host onwards) reuses these.
function Write-Info { param([string]$M) Write-Host "  [*] $M" -ForegroundColor Cyan }
function Write-Good { param([string]$M) Write-Host "  [+] $M" -ForegroundColor Green }
function Write-Err  { param([string]$M) Write-Host "  [!] $M" -ForegroundColor Red }
function Require-Cmd {
    param([string]$Cmd, [string]$InstallHint)
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        Write-Err "$Cmd not found. $InstallHint"
        exit 1
    }
}

function Ensure-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $v = (& winget --version 2>&1) -join ' '
        Write-Host "  [+] winget already present ($v)" -ForegroundColor DarkGray
        return $true
    }

    Write-Host "  [*] winget not found -- bootstrapping Microsoft.DesktopAppInstaller..." -ForegroundColor Cyan

    # Path A: Add-AppxPackage from the official Microsoft delivery URL.
    # The bundle includes winget + its dependencies (UI.Xaml, VCLibs).
    # URL is the documented one Microsoft Learn points operators at.
    $appxUrl = 'https://aka.ms/getwinget'
    $tmpMsix = Join-Path $env:TEMP "mios-winget-bootstrap-$(Get-Random).msixbundle"
    try {
        Write-Host "    [.] Downloading $appxUrl -> $tmpMsix" -ForegroundColor DarkGray
        Invoke-WebRequest -Uri $appxUrl -OutFile $tmpMsix -UseBasicParsing -ErrorAction Stop
        Add-AppxPackage -Path $tmpMsix -ErrorAction Stop
        Write-Host "  [+] winget installed via Add-AppxPackage" -ForegroundColor Green
        Remove-Item -LiteralPath $tmpMsix -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "  [!] Add-AppxPackage Microsoft.DesktopAppInstaller failed: $($_.Exception.Message)" -ForegroundColor Yellow

        # Path B: PowerShell module fallback. Microsoft.WinGet.Client
        # ships a Repair-WinGetPackageManager cmdlet that mirrors the
        # MSIX bootstrap and handles dependency ordering on Server SKUs.
        try {
            Write-Host "    [.] Falling back to Microsoft.WinGet.Client module..." -ForegroundColor DarkGray
            Install-PackageProvider -Name NuGet -Force -Scope CurrentUser -ErrorAction Stop | Out-Null
            Install-Module -Name Microsoft.WinGet.Client -Force -Scope CurrentUser -AcceptLicense -ErrorAction Stop
            Import-Module Microsoft.WinGet.Client -ErrorAction Stop
            Repair-WinGetPackageManager -AllUsers -ErrorAction Stop
            Write-Host "  [+] winget installed via Microsoft.WinGet.Client" -ForegroundColor Green
        } catch {
            Write-Host "  [!] All winget bootstrap paths failed: $($_.Exception.Message)" -ForegroundColor Red
            return $false
        }
    }

    # Verify it's now on PATH (Add-AppxPackage doesn't refresh the
    # current process's PATH; load the AppX path explicitly).
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        $appxPath = (Get-AppxPackage Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue).InstallLocation
        if ($appxPath -and (Test-Path "$appxPath\winget.exe")) {
            $env:PATH = "$appxPath;$env:PATH"
            Write-Host "  [+] winget added to current-process PATH ($appxPath)" -ForegroundColor DarkGray
        }
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        return $true
    }
    Write-Host "  [!] winget still not on PATH -- next-session reboot will surface it." -ForegroundColor Yellow
    return $false
}

function Enable-MiOSWindowsFeatures {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host "  [!] Enable-MiOSWindowsFeatures needs admin -- deferring (auto-elevation will rerun this)." -ForegroundColor Yellow
        return $false
    }

    $features = [ordered]@{}
    $features.Add([string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'wsl'    -Default 'Microsoft-Windows-Subsystem-Linux'), 'Windows Subsystem for Linux')
    $features.Add([string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'vmp'    -Default 'VirtualMachinePlatform'),            'Virtual Machine Platform (WSL2 + Hyper-V hypervisor)')
    $features.Add([string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'hyperv' -Default 'Microsoft-Hyper-V'),                 'Hyper-V (manager + VMs)')

    $rebootPending = $false
    foreach ($name in $features.Keys) {
        $label = $features[$name]
        $isEnabled = $false
        try {
            $info = & dism.exe /online /get-featureinfo /featurename:$name 2>$null
            if ($LASTEXITCODE -eq 0 -and ($info -match 'State\s*:\s*Enabled')) {
                $isEnabled = $true
            }
        } catch {}

        if ($isEnabled) {
            Write-Host "  [+] $label already enabled." -ForegroundColor DarkGray
            continue
        }

        if ($name -like '*Subsystem-Linux*') {
            $_wslOk = $false
            try { & wsl.exe --version *> $null; if ($LASTEXITCODE -eq 0) { $_wslOk = $true } } catch {}
            if ($_wslOk) {
                Write-Host "  [+] $label satisfied (wsl.exe present; Store-based WSL needs no optional feature)." -ForegroundColor DarkGray
                continue
            }
        }

        Write-Host "  [*] Enabling $label..." -ForegroundColor Cyan
        try {
            $r = & dism.exe /online /enable-feature /featurename:$name /all /norestart
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  [+] $label enabled." -ForegroundColor Green
            } else {
                Write-Host "  [-] $label not available on this Windows edition -- skipping." -ForegroundColor DarkGray
            }
        } catch {
            Write-Host "  [!] dism /enable-feature $name failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        $_wslPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'wsl_pkg' -Default 'Microsoft.WSL')
        Write-Host "  [*] wsl.exe not on PATH -- installing WSL ($_wslPkg via Microsoft Store MSIX)..." -ForegroundColor Cyan
        # Path A: winget install (preferred on Win11; pulls the Store version + dependencies)
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            try {
                & winget install --id $_wslPkg --silent --accept-source-agreements --accept-package-agreements 2>&1 |
                    ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            } catch {
                Write-Host "  [!] winget install $_wslPkg : $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
        # Path B: fallback to `wsl --install --no-distribution` (works on
        # any Win10 22H2+ / Win11 with the Windows-feature already enabled).
        if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
            try {
                $sysWsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
                if (Test-Path $sysWsl) {
                    Write-Host "  [*] Falling back to '$sysWsl --install --no-distribution'..." -ForegroundColor Cyan
                    & $sysWsl --install --no-distribution --web-download 2>&1 |
                        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                }
            } catch {
                Write-Host "  [!] wsl --install fallback failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }

    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        try {
            Write-Host "  [*] Running 'wsl --update --pre-release' (preview channel)..." -ForegroundColor Cyan
            & wsl.exe --update --pre-release 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            & wsl.exe --set-default-version 2 2>&1 | Out-Null
            Write-Host "  [+] WSL: preview channel + default version 2" -ForegroundColor Green
            # Surface what we actually got
            try {
                $verOut = (& wsl.exe --version 2>&1) -join "`n"
                Write-Host "  [.] wsl --version:" -ForegroundColor DarkGray
                $verOut -split "`n" | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
            } catch {}
        } catch {
            Write-Host "  [!] wsl --update --pre-release: $($_.Exception.Message)" -ForegroundColor Yellow
            # Fallback: try without --pre-release in case the flag isn't
            # supported by the very-old WSL kernel installed.
            try { & wsl.exe --update 2>&1 | Out-Null } catch {}
            try { & wsl.exe --set-default-version 2 2>&1 | Out-Null } catch {}
        }
    } else {
        Write-Host "  [-] wsl.exe still not on PATH after install attempts -- next-session reboot may surface it." -ForegroundColor DarkGray
        $rebootPending = $true
    }

    if ($rebootPending) {
        $_haltOnReboot = ([string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'require_reboot_to_continue' -Default 'true')) -ieq 'true'
        Write-Host ''
        Write-Host '  +==============================================================+' -ForegroundColor Yellow
        Write-Host '  | REBOOT PENDING -- Windows features enabled this session need |' -ForegroundColor Yellow
        Write-Host '  | a reboot to take full effect. WSL2, the dev VM, podman       |' -ForegroundColor Yellow
        Write-Host '  | machine init, and the OCI build will fail until you reboot.  |' -ForegroundColor Yellow
        Write-Host '  +==============================================================+' -ForegroundColor Yellow
        if ($_haltOnReboot) {
            Write-Host ''
            Write-Host '  REBOOT NOW, then re-run the irm|iex one-liner. Pass-0 reaps' -ForegroundColor Cyan
            Write-Host '  prior state automatically; the next run starts clean and'  -ForegroundColor Cyan
            Write-Host '  proceeds straight through.' -ForegroundColor Cyan
            Write-Host ''
            return [pscustomobject]@{ Status = 'ok';  RebootRequired = $true; HaltRequested = $true }
        }
        Write-Host '  [bootstrap.prereqs.features].require_reboot_to_continue=false ' -ForegroundColor DarkGray
        Write-Host '  -- continuing despite reboot-pending; expect cascade failures.' -ForegroundColor DarkGray
        Write-Host ''
        return [pscustomobject]@{ Status = 'ok'; RebootRequired = $true; HaltRequested = $false }
    }
    return [pscustomobject]@{ Status = 'ok'; RebootRequired = $false; HaltRequested = $false }
}

function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Good "Git already installed ($((git --version) 2>&1))"
        return
    }

    $_gitPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'git_pkg' -Default 'Git.Git')
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Installing Git via winget ($_gitPkg) ..."
        & winget install --exact --id $_gitPkg `
            --silent --accept-source-agreements --accept-package-agreements `
            --scope machine 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) {
            Write-Info "Retrying Git winget install at user scope ..."
            & winget install --exact --id $_gitPkg `
                --silent --accept-source-agreements --accept-package-agreements 2>&1 |
                ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
    }

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Info "winget install failed or unavailable. Attempting PortableGit direct download..."
        $_gitUrl = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'git_url' -Default 'https://api.github.com/repos/git-for-windows/git/releases/latest')
        try {
            $rel = Invoke-RestMethod -Uri $_gitUrl -Headers @{'User-Agent'='mios-bootstrap'} -ErrorAction Stop
            $asset = $rel.assets | Where-Object { $_.name -match '^PortableGit-.*-64-bit\.7z\.exe$' } | Select-Object -First 1
            if (-not $asset) {
                throw "No PortableGit 64-bit asset in latest release."
            }
            $sfx = Join-Path $env:TEMP "PortableGit-$(Get-Random).7z.exe"
            Write-Info "Downloading PortableGit from $($asset.browser_download_url) ..."
            $webClient = New-Object System.Net.WebClient
            $webClient.DownloadFile($asset.browser_download_url, $sfx)

            $_root = Join-Path $env:LOCALAPPDATA 'MiOS'
            $gitDir = Join-Path $_root 'PortableGit'
            if (Test-Path $gitDir) { Remove-Item $gitDir -Recurse -Force -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Path $gitDir -Force | Out-Null

            Write-Info "Extracting PortableGit to $gitDir ..."
            & $sfx "-o$gitDir" -y | Out-Null
            Remove-Item $sfx -Force -ErrorAction SilentlyContinue

            $gitCmd = Join-Path $gitDir 'cmd'
            if (Test-Path (Join-Path $gitCmd 'git.exe')) {
                $_u = [Environment]::GetEnvironmentVariable('Path','User')
                if (-not (($_u -split ';') | Where-Object { $_ -ieq $gitCmd })) {
                    [Environment]::SetEnvironmentVariable('Path', "$_u;$gitCmd", 'User')
                }
                $env:PATH = "$env:PATH;$gitCmd"
                Write-Good "PortableGit installed successfully."
            }
        } catch {
            Write-Err "Direct PortableGit installation failed: $_"
        }
    }
}

function Ensure-PodmanDesktop {
    if (Get-Command podman -ErrorAction SilentlyContinue) {
        Write-Good "Podman already installed ($((podman --version) 2>&1))"
        return
    }
    # Check if we should install Podman Desktop
    $_installDesktop = Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'install_podman_desktop' -Default $false
    if ($_installDesktop -eq 'true') { $_installDesktop = $true }
    elseif ($_installDesktop -eq 'false') { $_installDesktop = $false }
    if ($_installDesktop -isnot [bool]) { $_installDesktop = $false }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if ($_installDesktop) {
            # Install Podman Desktop (the GUI). It bundles podman.exe inside its
            # resources tree -- but does NOT put it on PATH by default.
            # TOML-first -- Podman Desktop winget ID from mios.toml [bootstrap.prereqs].podman_pkg
            $_podmanPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_pkg' -Default 'RedHat.Podman-Desktop')
            Write-Info "Installing Podman Desktop via winget ($_podmanPkg) ..."
            & winget install --exact --id $_podmanPkg `
                --silent --accept-source-agreements --accept-package-agreements `
                --scope machine 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            if ($LASTEXITCODE -ne 0) {
                Write-Info "Retrying winget install at user scope ..."
                & winget install --exact --id $_podmanPkg `
                    --silent --accept-source-agreements --accept-package-agreements 2>&1 |
                    ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            }
        }
        $_podmanCliPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_cli_pkg' -Default 'RedHat.Podman')
        Write-Info "Installing Podman CLI via winget ($_podmanCliPkg) ..."
        & winget install --exact --id $_podmanCliPkg `
            --silent --accept-source-agreements --accept-package-agreements `
            --scope machine 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) {
            Write-Info "Retrying CLI winget install at user scope ..."
            & winget install --exact --id $_podmanCliPkg `
                --silent --accept-source-agreements --accept-package-agreements 2>&1 |
                ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
    }

    # Direct MSI download and silent installation fallback if winget failed/is missing
    if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
        Write-Info "winget install failed or unavailable. Attempting direct MSI download and install of Podman CLI..."
        $podmanVersion = "6.0.0"
        try {
            $podmanUrl = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_url' -Default 'https://api.github.com/repos/containers/podman/releases/latest')
            $latestRelease = Invoke-RestMethod -Uri $podmanUrl -UseBasicParsing -ErrorAction Stop
            if ($latestRelease.tag_name -match '^v?([0-9\.]+)$') {
                $podmanVersion = $Matches[1]
            }
        } catch {
            Write-Info "Failed to query latest version from GitHub API (offline or rate-limited). Using default fallback version v6.0.0"
        }
        $msiUrl = "https://github.com/containers/podman/releases/download/v$podmanVersion/podman-v$podmanVersion.msi"
        $msiPath = Join-Path $env:TEMP "podman-installer.msi"
        Write-Info "Downloading Podman CLI MSI from $msiUrl ..."
        try {
            $webClient = New-Object System.Net.WebClient
            $webClient.DownloadFile($msiUrl, $msiPath)
            Write-Info "Installing Podman CLI silently via msiexec..."
            $proc = Start-Process msiexec.exe -ArgumentList "/i `"$msiPath`" /qn /norestart" -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
                Write-Err "msiexec exited with non-zero code: $($proc.ExitCode)"
            }
            Remove-Item $msiPath -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Err "Direct MSI installation failed: $_"
            Write-Err "  Please download and install Podman CLI manually via the setup/MSI from the official release page:"
            Write-Err "  https://github.com/containers/podman/releases"
        }
    }

    # Refresh PATH from registry so the just-installed podman.exe is
    # visible to Get-Command in THIS pwsh session.
    $env:PATH = `
        [Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + `
        [Environment]::GetEnvironmentVariable('PATH','User')
    if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
        # Probe ALL the locations where podman.exe might live: standalone
        # CLI install dir, Podman Desktop's resources bundle, the older
        # podman-machine standalone, plus any user-scope variants.
        $pmCandidates = @(
            (Join-Path ${env:ProgramFiles}      'RedHat\Podman\bin'),
            (Join-Path ${env:ProgramFiles}      'RedHat\Podman'),
            (Join-Path ${env:ProgramFiles}      'RedHat\Podman\resources\app\binary'),
            (Join-Path ${env:ProgramFiles}      'RedHat\Podman\resources\bin'),
            (Join-Path ${env:ProgramFiles(x86)} 'RedHat\Podman\bin'),
            (Join-Path $env:LOCALAPPDATA        'Programs\RedHat\Podman\bin'),
            (Join-Path $env:LOCALAPPDATA        'Programs\Podman\bin')
        )
        foreach ($cand in $pmCandidates) {
            if ($cand -and (Test-Path -LiteralPath (Join-Path $cand 'podman.exe'))) {
                Write-Info "Found podman.exe at $cand -- prepending to PATH"
                $env:PATH = "$cand;$env:PATH"
                # Persist on machine PATH too so future shells see it.
                try {
                    $machPath = [Environment]::GetEnvironmentVariable('PATH','Machine')
                    if (-not ($machPath -split ';' -contains $cand)) {
                        [Environment]::SetEnvironmentVariable('PATH', "$cand;$machPath", 'Machine')
                    }
                } catch {}
                break
            }
        }
        # Last resort: filesystem-walk Program Files for podman.exe.
        if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
            $found = Get-ChildItem -Path "${env:ProgramFiles}\RedHat","${env:LOCALAPPDATA}\Programs" `
                                   -Filter podman.exe -Recurse -ErrorAction SilentlyContinue -Depth 6 |
                     Select-Object -First 1
            if ($found) {
                $podmanDir = Split-Path -Parent $found.FullName
                Write-Info "Discovered podman.exe via search at $podmanDir -- prepending to PATH"
                $env:PATH = "$podmanDir;$env:PATH"
                try {
                    $machPath = [Environment]::GetEnvironmentVariable('PATH','Machine')
                    if (-not ($machPath -split ';' -contains $podmanDir)) {
                        [Environment]::SetEnvironmentVariable('PATH', "$podmanDir;$machPath", 'Machine')
                    }
                } catch {}
            }
        }
    }
    if (Get-Command podman -ErrorAction SilentlyContinue) {
        Write-Good "Podman installed ($((podman --version) 2>&1))"
    } else {
        Write-Err "Podman installed but ``podman`` still not on PATH."
        Write-Err "  Probed: ${env:ProgramFiles}\RedHat\Podman\(bin|resources\app\binary|resources\bin),"
        Write-Err "          ${env:LOCALAPPDATA}\Programs\RedHat\Podman\bin"
        Write-Err "  Skipping CLI verification -- continuing with Pass-2 (build-mios.ps1 will"
        Write-Err "  resolve podman from its own probes inside the dev VM context)."
    }
}

function Invoke-MiOSFullReap {
    param([switch]$Quiet)
    # Compatibility entry: installation and error recovery preserve persistent
    # guest state and operator source. Destructive cleanup belongs to uninstall.
    if (-not $Quiet) { Write-Host '  [MiOS] Existing distributions, persistent state and source checkouts preserved.' -ForegroundColor Cyan }
}

function Initialize-DataDisk {
    param(
        [int]$ShrinkMB     = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'shrink_mb'    -Default 262656),
        [string]$DriveLetter = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default 'M'),
        [string]$VolumeLabel = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'volume_label' -Default 'MIOS-DEV')
    )
    $existing = Get-Volume -DriveLetter $DriveLetter -ErrorAction SilentlyContinue
    if ($existing -and $existing.FileSystemLabel -eq $VolumeLabel) {
        Write-Good "M:\ already provisioned ($([math]::Round($existing.Size/1GB,1)) GB, $($existing.FileSystem), label=$VolumeLabel)"
        return
    }
    if ($existing) {
        Write-Err "Drive ${DriveLetter}: exists with label '$($existing.FileSystemLabel)' (not '$VolumeLabel')."
        Write-Err "Either remove the volume manually or pass -DriveLetter <other> to Get-MiOS.ps1."
        exit 1
    }
    $_displayGb = Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'display_size_gb' -Default 256
    Write-Info "Provisioning ${DriveLetter}:\ at $ShrinkMB MB (target $_displayGb GB visible in Explorer) ..."
    $sysLetter = ([Environment]::GetEnvironmentVariable('SystemDrive')).TrimEnd(':')
    $cPart       = Get-Partition -DriveLetter $sysLetter
    $supported   = Get-PartitionSupportedSize -DriveLetter $sysLetter
    $shrinkBytes = [int64]$ShrinkMB * 1MB
    $newCSize    = $cPart.Size - $shrinkBytes
    if ($shrinkBytes -gt ($cPart.Size - $supported.SizeMin)) {
        $minShrinkMB = [int](Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'min_shrink_mb' -Default 65536)
        $availBytes  = $cPart.Size - $supported.SizeMin
        if ($availBytes -ge ([int64]$minShrinkMB * 1MB)) {
            $clampMB     = [int]([math]::Floor($availBytes / 1MB)) - 1024   # ~1 GB headroom
            Write-Info "Requested $ShrinkMB MB exceeds the $([math]::Round($availBytes/1GB,1)) GB shrinkable on ${sysLetter}:; clamping ${DriveLetter}:\ to ~$([math]::Round($clampMB/1024,1)) GB (floor $([math]::Round($minShrinkMB/1024,1)) GB)."
            $ShrinkMB    = $clampMB
            $shrinkBytes = [int64]$ShrinkMB * 1MB
            $newCSize    = $cPart.Size - $shrinkBytes
        } else {
            Write-Err "Cannot shrink ${sysLetter}: by even the $([math]::Round($minShrinkMB/1024,1)) GB minimum."
            Write-Err "  current ${sysLetter}: size: $([math]::Round($cPart.Size/1GB,1)) GB"
            Write-Err "  max shrinkable:         $([math]::Round($availBytes/1GB,1)) GB"
            Write-Err "Free up ${sysLetter}: space (move pagefile / disable hibernation / clean up large files) and retry."
            throw "Initialize-DataDisk: insufficient shrinkable space on ${sysLetter}: (need >= $minShrinkMB MB, have $([math]::Round($availBytes/1MB)) MB)"
        }
    }
    $disk = Get-Disk -Number $cPart.DiskNumber
    if ($disk.PartitionStyle -notin @('GPT','MBR')) {
        Write-Err "Disk $($disk.Number) has unsupported partition style '$($disk.PartitionStyle)'"
        exit 1
    }
    Write-Info "  shrinking ${sysLetter}: $([math]::Round($cPart.Size/1GB,1)) GB -> $([math]::Round($newCSize/1GB,1)) GB ..."
    Resize-Partition -DriveLetter $sysLetter -Size $newCSize -ErrorAction Stop
    Write-Info "  creating $VolumeLabel partition (${ShrinkMB} MB) on disk $($disk.Number) ..."
    $_fs    = Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'filesystem'      -Default 'NTFS'
    $_alloc = Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'allocation_unit' -Default 4096
    $null = New-Partition -DiskNumber $disk.Number -Size $shrinkBytes -DriveLetter $DriveLetter -ErrorAction Stop
    $null = Format-Volume -DriveLetter $DriveLetter -FileSystem $_fs -NewFileSystemLabel $VolumeLabel `
        -AllocationUnitSize $_alloc -Confirm:$false -Force
    Write-Good "${DriveLetter}:\\ created (${ShrinkMB} MB NTFS, label=$VolumeLabel)"
}

function Set-PodmanMachineStorageOnM {
    param([string]$MRoot = 'M:\podman\machine')
    if (-not (Test-Path $MRoot)) {
        New-Item -ItemType Directory -Path $MRoot -Force -ErrorAction Stop | Out-Null
        Write-Host "    [+] created $MRoot" -ForegroundColor DarkGray
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA  'containers\podman\machine'),
        (Join-Path $env:USERPROFILE   '.local\share\containers\podman\machine'),
        (Join-Path $env:PROGRAMDATA   'containers\podman\machine')
    )
    foreach ($p in $candidates) {
        if (-not $p) { continue }
        $parent = Split-Path $p -Parent
        if (-not (Test-Path $parent)) { try { New-Item -ItemType Directory -Path $parent -Force | Out-Null } catch {} }
        if (Test-Path $p) {
            $item = Get-Item $p -Force -ErrorAction SilentlyContinue
            if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                $current = ($item.Target -join '').TrimStart('\??\')
                $isSymlink = $item.LinkType -eq 'SymbolicLink'
                if ($current -ieq $MRoot -and $isSymlink) {
                    Write-Host "    [=] $p -> $MRoot (already symlinked)" -ForegroundColor DarkGray
                    continue
                }
                if ($current -ieq $MRoot -and -not $isSymlink) {
                    Write-Host "    [~] $p is a JUNCTION (legacy) -- recreating as symlink" -ForegroundColor DarkYellow
                }
                cmd /c "rmdir `"$p`"" 2>$null | Out-Null
            } else {
                $kids = Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue
                if ($kids -and $kids.Count -gt 0) {
                    Write-Host "    [*] moving existing $p contents to $MRoot ..." -ForegroundColor DarkGray
                    try {
                        foreach ($k in $kids) {
                            $dst = Join-Path $MRoot $k.Name
                            if (-not (Test-Path $dst)) { Move-Item -LiteralPath $k.FullName -Destination $MRoot -Force -ErrorAction Stop }
                        }
                    } catch { Write-Host "    [!] move failed for $p : $($_.Exception.Message) -- forcing remove" -ForegroundColor Yellow }
                }
                try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop }
                catch { Write-Host "    [!] couldn't remove $p (locked) -- skipping junction for this path" -ForegroundColor Yellow; continue }
            }
        }
        $rc = (cmd /c "mklink /D `"$p`" `"$MRoot`"" 2>&1)
        if ($LASTEXITCODE -eq 0) { Write-Host "    [+] symlinked $p -> $MRoot" -ForegroundColor DarkGray }
        else                      { Write-Host "    [!] mklink /D $p -> $MRoot failed: $rc" -ForegroundColor Yellow }
    }
}

function Set-WingetStorageOnM {
    param([string]$MRoot = 'M:\winget')
    if (-not (Test-Path $MRoot)) {
        New-Item -ItemType Directory -Path $MRoot -Force -ErrorAction Stop | Out-Null
        Write-Host "    [+] created $MRoot" -ForegroundColor DarkGray
    }
    foreach ($_sub in @('Packages','Cache','PortableLinks','PortablePackagesRoot','MachinePackages')) {
        $sd = Join-Path $MRoot $_sub
        if (-not (Test-Path $sd)) { New-Item -ItemType Directory -Path $sd -Force | Out-Null }
    }
    # Resolve %ProgramFiles% defensively -- on 64-bit Windows the
    # canonical machine WinGet path is "C:\Program Files\WinGet\Packages".
    $_pf = $env:ProgramFiles
    if (-not $_pf) { $_pf = $env:ProgramW6432 }
    if (-not $_pf) { $_pf = 'C:\Program Files' }
    $candidates = @(
        @{ Src = (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages');             Dst = (Join-Path $MRoot 'Packages')             },
        @{ Src = (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Cache');                Dst = (Join-Path $MRoot 'Cache')                },
        @{ Src = (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links');                Dst = (Join-Path $MRoot 'PortableLinks')        },
        @{ Src = (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Portable\PackagesRoot'); Dst = (Join-Path $MRoot 'PortablePackagesRoot') },
        # Machine-scope winget retry (used when --scope user fails -- e.g.
        # Microsoft.PowerShell, .NET runtimes, system-wide tools) lands
        # in %ProgramFiles%\WinGet\Packages. Junction to M:\ so machine-
        # scope installs ALSO end up on M:\.
        @{ Src = (Join-Path $_pf 'WinGet\Packages');                                    Dst = (Join-Path $MRoot 'MachinePackages')      }
    )
    foreach ($c in $candidates) {
        $p = $c.Src; $tgt = $c.Dst
        if (-not $p) { continue }
        $parent = Split-Path $p -Parent
        if (-not (Test-Path $parent)) { try { New-Item -ItemType Directory -Path $parent -Force | Out-Null } catch {} }
        if (Test-Path $p) {
            $item = Get-Item $p -Force -ErrorAction SilentlyContinue
            if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                $current = ($item.Target -join '').TrimStart('\??\')
                if ($current -ieq $tgt) { Write-Host "    [=] $p -> $tgt (already linked)" -ForegroundColor DarkGray; continue }
                cmd /c "rmdir `"$p`"" 2>$null | Out-Null
            } else {
                $kids = Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue
                if ($kids -and $kids.Count -gt 0) {
                    Write-Host "    [*] moving existing $p contents to $tgt ..." -ForegroundColor DarkGray
                    try {
                        foreach ($k in $kids) {
                            $dst = Join-Path $tgt $k.Name
                            if (-not (Test-Path $dst)) { Move-Item -LiteralPath $k.FullName -Destination $tgt -Force -ErrorAction Stop }
                        }
                    } catch { Write-Host "    [!] move failed: $($_.Exception.Message) -- forcing remove" -ForegroundColor Yellow }
                }
                try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop }
                catch { Write-Host "    [!] couldn't remove $p (locked) -- skipping link for this path" -ForegroundColor Yellow; continue }
            }
        }
        $rc = (cmd /c "mklink /D `"$p`" `"$tgt`"" 2>&1)
        if ($LASTEXITCODE -eq 0) { Write-Host "    [+] symlinked $p -> $tgt" -ForegroundColor DarkGray }
        else                      { Write-Host "    [!] mklink /D $p -> $tgt failed: $rc" -ForegroundColor Yellow }
    }
}

if ($env:MIOS_GETMIOS_FUNCTIONS_ONLY) {
    # All function defs + $Script:Mios* vars (MiosBrandingTxt,
    # MiosFastfetchConfig, MiosOmpJson) above this point are now in
    # the caller's scope. Caller invokes Install-MiOSPowerShellProfile
    # / Install-MiOSTerminalProfile / etc. directly.
    return
}

function Add-MiosDefenderExclusions {
    if (-not (Get-Command Add-MpPreference -ErrorAction SilentlyContinue)) { return }
    $_defaultPaths = @(
        'M:\',
        'M:\MiOS',
        'M:\MiOS\bin',
        'M:\MiOS\repo',
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet'),
        $env:TEMP
    )
    $_defaultProcs = @(
        'M:\MiOS\bin\mios-launch.exe',
        'M:\MiOS\bin\fastfetch.exe',
        'M:\MiOS\bin\btop.exe'
    )
    $excPaths = @(Get-MiosTomlValue -Section 'security.defender_exclusions' -Key 'paths'     -Default $_defaultPaths)
    $excProcs = @(Get-MiosTomlValue -Section 'security.defender_exclusions' -Key 'processes' -Default $_defaultProcs)
    foreach ($p in $excPaths) {
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        try { Add-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue } catch {}
    }
    foreach ($p in $excProcs) {
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        try { Add-MpPreference -ExclusionProcess $p -ErrorAction SilentlyContinue } catch {}
    }
}
try { Add-MiosDefenderExclusions } catch { Write-Host "  [!] Defender exclusion add failed (non-fatal, AMSI may still block): $($_.Exception.Message)" -ForegroundColor Yellow }

$_netMode  = [string](Get-MiosTomlValue -Section 'wsl2' -Key 'networking_mode'      -Default 'NAT')
$_lhfwd    = [string](Get-MiosTomlValue -Section 'wsl2' -Key 'localhost_forwarding' -Default 'true')
$_fwall    = [string](Get-MiosTomlValue -Section 'wsl2' -Key 'firewall'             -Default 'false')
$_gui      = [string](Get-MiosTomlValue -Section 'wsl2' -Key 'gui_applications'     -Default 'true')
$_isMirror = ($_netMode -ieq 'mirrored')
$_wslHostRamGB = try { [math]::Floor((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB) } catch { 16 }
$_wslReservePct = [math]::Min(95, [math]::Max(0, [int](Get-MiosTomlValue -Section 'bootstrap.dev_vm.host_reserve' -Key 'memory_pct' -Default 50)))
$_wslReserveGB  = [math]::Max(0, [int](Get-MiosTomlValue -Section 'bootstrap.dev_vm.host_reserve' -Key 'memory_gb'  -Default 8))
$_wslRamGB = [math]::Max(4, $_wslHostRamGB - [math]::Max($_wslReserveGB, [math]::Floor($_wslHostRamGB * $_wslReservePct / 100)))

$_wslCfg = Join-Path $env:USERPROFILE ".wslconfig"
$_wslCfgRaw = if (Test-Path $_wslCfg) { Get-Content $_wslCfg -Raw } else { "" }

# Build the section body from TOML-resolved values.
$_keyLines = New-Object System.Collections.Generic.List[string]
$_keyLines.Add("memory=${_wslRamGB}GB")
$_keyLines.Add("networkingMode=$_netMode")
if ($_isMirror) {
    if ($_fwall -ieq 'true') { $_keyLines.Add('firewall=true') }
} else {
    if ($_lhfwd -ieq 'true') { $_keyLines.Add('localhostForwarding=true') }
}
if ($_gui -ieq 'true') { $_keyLines.Add('guiApplications=true') }

# Detect divergence: any required key missing or value mismatched.
$_needWrite = $false
foreach ($_kv in $_keyLines) {
    $_pat = '(?m)^' + [regex]::Escape($_kv) + '\s*$'
    if ($_wslCfgRaw -notmatch $_pat) { $_needWrite = $true; break }
}
if ($_needWrite) {
    if ($_wslCfgRaw -notmatch "\[wsl2\]") {
        $_baseline = @"

[wsl2]
# MiOS pre-Phase-0 settings, generated from mios.toml [wsl2] and
# [bootstrap.dev_vm.host_reserve] by
# Get-MiOS.ps1 on every irm|iex. Edit values in mios.html, not here --
# this block is regenerated.
$($_keyLines -join "`r`n")
"@
        [System.IO.File]::WriteAllText($_wslCfg, $_baseline, (New-Object System.Text.UTF8Encoding($false)))
    } else {
        # [wsl2] section exists -- replace its keys with the TOML-resolved
        # set. Strip ALL legacy networking keys (networkingMode,
        # localhostForwarding, firewall) so a prior mode doesn't survive.
        $_lines = Get-Content $_wslCfg
        $_in    = $false
        $_out   = [System.Collections.Generic.List[string]]::new()
        $_added = $false
        foreach ($_l in $_lines) {
            if ($_l -match '^\[wsl2\]') {
                $_in = $true; $_out.Add($_l)
                if (-not $_added) { foreach ($_kv in $_keyLines) { $_out.Add($_kv) }; $_added = $true }
                continue
            } elseif ($_l -match '^\[') { $_in = $false }
            if ($_in -and $_l -match '^(memory|networkingMode|localhostForwarding|firewall|guiApplications)\s*=') { continue }
            $_out.Add($_l)
        }
        [System.IO.File]::WriteAllLines($_wslCfg, $_out, (New-Object System.Text.UTF8Encoding($false)))
    }
    Write-Host "  [+] .wslconfig: $_netMode mode and ${_wslRamGB}GB RAM written from mios.toml (pre-Phase-0)" -ForegroundColor Green
    & wsl.exe --shutdown 2>$null | Out-Null
}

$_freshVendorToml = Resolve-MiosVendorTomlText
if (-not $_freshVendorToml -or
    $_freshVendorToml -notmatch '(?m)^\[meta\]\s*$' -or
    $_freshVendorToml -notmatch '(?m)^\[identity\]\s*$' -or
    $_freshVendorToml -notmatch '(?m)^\[packages\.windows\]\s*$') {
    Write-Host '  [!!] Fresh system mios.toml unavailable or incomplete; Phase 0 was not started.' -ForegroundColor Red
    exit 1
}
try { Invoke-MiOSFullReap } catch { Write-Host "  [!] Invoke-MiOSFullReap failed: $($_.Exception.Message)" -ForegroundColor Yellow }

$_trapFmtFailed = Get-MiosTomlValue -Section 'messages.failure_trap' -Key 'install_failed_template' -Default '[!!] Install failed: {0}'
trap {
    Write-Host ''
    Write-Host ('  ' + ($_trapFmtFailed -f $_.Exception.Message)) -ForegroundColor Red
    Write-Host '  [MiOS] Installation failed; existing distributions, persistent state and source checkouts are preserved.' -ForegroundColor Yellow
    exit 1
}
# SSOT: every Step N banner resolves through mios.toml [messages.steps].
# Per feedback_mios_messages_section_ssot: no Write-Host literals in code;
# vendor defaults via -Default arg of Get-MiosTomlValue.
$_msgStep0          = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_0_provision'      -Default '[*] Step 0: Provisioning M:\ partition + storage junctions...'
$_msgStep0Failed    = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_0_failed_template' -Default '[!] Initialize-DataDisk failed: {0}'
$_msgPodmanRedirect = Get-MiosTomlValue -Section 'messages.steps' -Key 'podman_storage_redirect' -Default 'Redirecting podman-machine storage to M:\\podman\\machine ...'
$_msgPodmanFailed   = Get-MiosTomlValue -Section 'messages.steps' -Key 'podman_storage_failed_template' -Default '[!] Set-PodmanMachineStorageOnM failed: {0}'
$_msgWingetRedirect = Get-MiosTomlValue -Section 'messages.steps' -Key 'winget_storage_redirect' -Default 'Redirecting winget package storage to M:\\winget\\* ...'
$_msgWingetFailed   = Get-MiosTomlValue -Section 'messages.steps' -Key 'winget_storage_failed_template' -Default '[!] Set-WingetStorageOnM failed: {0}'

# Install-robustness (B2): hardware-virtualization preflight. WSL2 +
# `podman machine init` cannot start without VT-x/AMD-V (SVM) enabled in BIOS/
# UEFI. Fail fast before Initialize-DataDisk (reboot/disk changes).
try {
    $_virtFw = $true; $_hyperv = $true
    try { $_virtFw = [bool](Get-CimInstance Win32_Processor -EA Stop | Select-Object -First 1 -Expand VirtualizationFirmwareEnabled) } catch {}
    try { $_hyperv = [bool](Get-CimInstance Win32_ComputerSystem -EA Stop).HypervisorPresent } catch {}
    if (-not $_virtFw -and -not $_hyperv) {
        Write-Err "Hardware virtualization is DISABLED -- WSL2 + podman machine cannot start."
        Write-Err "  -> Enable Intel VT-x / AMD-V (SVM) in BIOS/UEFI, then re-run the bootstrap."
        exit 1
    }
} catch {}

Write-Host ''
Write-Host "  $_msgStep0" -ForegroundColor Cyan
$_dataOk = $true
try { Initialize-DataDisk } catch { $_dataOk = $false; Write-Host ('  ' + ($_msgStep0Failed -f $_.Exception.Message)) -ForegroundColor Yellow }
$_dataDrive = [string](Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default 'M')
if ($_dataOk -and -not (Get-Volume -DriveLetter $_dataDrive -ErrorAction SilentlyContinue)) { $_dataOk = $false }
if (-not $_dataOk) {
    Write-Err "Could not provision the ${_dataDrive}:\ data partition -- MiOS stores its containers,"
    Write-Err "  packages, config and repos there, so the install cannot continue without it."
    Write-Err "  Most common causes on a factory laptop:"
    Write-Err "    - Under ~64 GB free on C:  (free some space / empty the Recycle Bin, then re-run)."
    Write-Err "    - C: is BitLocker-locked or non-shrinkable  (suspend it, then re-run the one-liner:"
    Write-Err "        manage-bde -protectors -disable C: -RebootCount 1)."
    try { Invoke-MiOSFullReap } catch {}
    exit 1
}
try {
    Write-Info $_msgPodmanRedirect
    Set-PodmanMachineStorageOnM
} catch { Write-Host ('  ' + ($_msgPodmanFailed -f $_.Exception.Message)) -ForegroundColor Yellow }

# Bootstrap winget on hosts that don't have it before any winget-consuming
# step runs (Set-WingetStorageOnM, Enable-MiOSWindowsFeatures' WSL Store
# install, Ensure-PodmanDesktop, Windows Terminal install, etc.).
# Fresh Win11 has it preinstalled; Server / Win10 / debloated images may not.
try { Ensure-Winget | Out-Null } catch { Write-Host "  [!] Ensure-Winget failed: $($_.Exception.Message)" -ForegroundColor Yellow }

try {
    Write-Info $_msgWingetRedirect
    Set-WingetStorageOnM
} catch { Write-Host ('  ' + ($_msgWingetFailed -f $_.Exception.Message)) -ForegroundColor Yellow }

try {
    $_miosVendorToml = Resolve-MiosVendorTomlText
    $_miosHostToml = Resolve-MiosTomlText
    if ($_miosVendorToml) {
        $_vendorDst = 'M:\usr\share\mios\mios.toml'
        $_vendorDir = Split-Path -Parent $_vendorDst
        if (-not (Test-Path -LiteralPath $_vendorDir)) { New-Item -ItemType Directory -Path $_vendorDir -Force | Out-Null }
        [IO.File]::WriteAllText($_vendorDst, $_miosVendorToml, (New-Object System.Text.UTF8Encoding($false)))
        if ($_miosHostToml) {
            $_hostDst = 'M:\etc\mios\mios.toml'
            $_hostDir = Split-Path -Parent $_hostDst
            if (-not (Test-Path -LiteralPath $_hostDir)) { New-Item -ItemType Directory -Path $_hostDir -Force | Out-Null }
            [IO.File]::WriteAllText($_hostDst, $_miosHostToml, (New-Object System.Text.UTF8Encoding($false)))
            Write-Host "  [+] Full vendor mios.toml -> M:\usr\share\mios; operator profile -> M:\etc\mios" -ForegroundColor DarkGray
        } else {
            Write-Host "  [+] Full vendor mios.toml -> M:\usr\share\mios (no host override found)" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  [!] Full vendor mios.toml could not be loaded -- M:\usr\share\mios was not overwritten" -ForegroundColor Yellow
    }
} catch {
    Write-Host ("  [!] mios.toml promotion to M:\ failed: $($_.Exception.Message)") -ForegroundColor Yellow
}
$_vendorDst = 'M:\usr\share\mios\mios.toml'
if (-not (Test-Path -LiteralPath $_vendorDst -PathType Leaf) -or
    [IO.File]::ReadAllText($_vendorDst, (New-Object System.Text.UTF8Encoding($false))) -cne $_freshVendorToml) {
    throw 'Full fetched system mios.toml was not staged exactly on M:\; refusing to continue the installer.'
}

$_msgStep06 = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_0_6_features' -Default '[*] Step 0.6: Enabling Windows features (WSL + VirtualMachinePlatform + Hyper-V)...'
Write-Host ''
Write-Host "  $_msgStep06" -ForegroundColor Cyan
try { Ensure-MiOSWinget | Out-Null } catch { Write-Host "  [!] Ensure-MiOSWinget failed: $($_.Exception.Message)" -ForegroundColor Yellow }

try {
    $_hvWslGuid = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
    if (Get-Command Set-NetFirewallHyperVVMSetting -ErrorAction SilentlyContinue) {
        Set-NetFirewallHyperVVMSetting -Name $_hvWslGuid `
            -Enabled True `
            -DefaultInboundAction Allow `
            -DefaultOutboundAction Allow `
            -LoopbackEnabled True `
            -AllowHostPolicyMerge True `
            -ErrorAction Stop
        Write-Host '  [+] Hyper-V Firewall: WSL VM creator set to Allow + LoopbackEnabled.' -ForegroundColor Green
    } else {
        Write-Host '  [!] Set-NetFirewallHyperVVMSetting cmdlet missing -- Windows < 11 22H2? Hyper-V firewall step skipped.' -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [!] Hyper-V Firewall config failed: $($_.Exception.Message)" -ForegroundColor Yellow
}
try {
    # Capture into [pscustomobject] -- if the function leaks ANY pipeline
    # output (shouldn't, post-.Add refactor), grab the LAST
    # value (the explicit return) so the structured-result check can't be
    # confused by stray strings/objects upstream of the return statement.
    $_featOut = @(Enable-MiOSWindowsFeatures)
    $_featResult = $_featOut | Where-Object { $_ -is [pscustomobject] -and $_.PSObject.Properties['HaltRequested'] } | Select-Object -Last 1
    if ($_featResult -and $_featResult.HaltRequested) {
        try {
            $_resumeUrl = [string](Get-MiosTomlValue -Section 'bootstrap' -Key 'oneliner_url' -Default 'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1')
            $_ps        = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $_resumeArg = "-NoProfile -ExecutionPolicy Bypass -Command `"irm '$_resumeUrl' | iex`""
            $_runHiddenVbs = Join-Path $env:ProgramData 'MiOS\bin\run-hidden.vbs'
            if (-not (Test-Path -LiteralPath $_runHiddenVbs)) { $_runHiddenVbs = Join-Path $env:ProgramData 'MiOS\run-hidden.vbs' }
            if (-not (Test-Path -LiteralPath $_runHiddenVbs) -and $miosRoot) {
                $_vbsCandidate = Join-Path $miosRoot 'usr\share\mios\windows\run-hidden.vbs'
                if (Test-Path -LiteralPath $_vbsCandidate) { $_runHiddenVbs = $_vbsCandidate }
            }
            if (-not (Test-Path -LiteralPath $_runHiddenVbs)) {
                $_vbsDir = Split-Path $_runHiddenVbs -Parent
                if (-not (Test-Path -LiteralPath $_vbsDir)) { New-Item -ItemType Directory -Path $_vbsDir -Force | Out-Null }
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
'@ | Set-Content -Path $_runHiddenVbs -Encoding ASCII -Force
            }
            $_wscript     = Join-Path $env:SystemRoot 'System32\wscript.exe'
            $_serviceTool = Join-Path $env:ProgramData 'MiOS\bin\MiosServiceTool.exe'
            if (Test-Path -LiteralPath $_runHiddenVbs) {
                $_act  = New-ScheduledTaskAction -Execute $_wscript -Argument "//B //Nologo `"$_runHiddenVbs`" `"$_ps`" $_resumeArg"
            } elseif (Test-Path -LiteralPath $_serviceTool) {
                $_act  = New-ScheduledTaskAction -Execute $_serviceTool -Argument "-Run `"$_ps`" $_resumeArg"
            } else {
                throw "Neither run-hidden.vbs (wscript.exe) nor MiosServiceTool.exe is available to prevent console window flashing on auto-resume."
            }
            $_trg  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
            $_prin = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
            $_set  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName 'MiOS-Resume-Bootstrap' -Action $_act -Trigger $_trg -Principal $_prin -Settings $_set -Force | Out-Null
            Write-Host '  [+] Auto-resume armed: MiOS continues automatically after you reboot and' -ForegroundColor Green
            Write-Host '      sign back in -- no need to re-paste the one-liner.' -ForegroundColor Green
        } catch {
            Write-Host "  [!] Could not arm auto-resume ($($_.Exception.Message)); after reboot re-run" -ForegroundColor Yellow
            Write-Host '      the irm|iex one-liner manually to continue.' -ForegroundColor Yellow
        }
        Write-Host '  [*] Halting to await reboot (WSL/VirtualMachinePlatform just enabled).' -ForegroundColor Cyan
        exit 0
    } else {
        # Features already present (first run had them, OR this IS the post-reboot
        # resume) -- clear any leftover auto-resume task so it fires exactly once.
        if (Get-Command Unregister-ScheduledTask -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName 'MiOS-Resume-Bootstrap' -Confirm:$false -ErrorAction SilentlyContinue
        }
    }
} catch { Write-Host "  [!] Enable-MiOSWindowsFeatures failed: $($_.Exception.Message)" -ForegroundColor Yellow }

if ($true) {
    $isAdmin = $_isAdmin
    try {
        $personalize = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
        if (-not (Test-Path $personalize)) { New-Item -Path $personalize -Force | Out-Null }
        Set-ItemProperty -Path $personalize -Name 'EnableTransparency'   -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $personalize -Name 'AppsUseLightTheme'    -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $personalize -Name 'SystemUsesLightTheme' -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $personalize -Name 'ColorPrevalence'      -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue

        # Enable LongPathsEnabled for path compatibility
        $fileSystemPath = 'HKLM:\System\CurrentControlSet\Control\FileSystem'
        if (Test-Path $fileSystemPath) {
            Set-ItemProperty -Path $fileSystemPath -Name 'LongPathsEnabled' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
        }

        $dwmKeyReg = 'HKCU\Software\Microsoft\Windows\DWM'
        $accentHex = '0xFF7F401A'
        & reg.exe add $dwmKeyReg /v 'AccentColor'           /t REG_DWORD /d $accentHex /f *>$null
        & reg.exe add $dwmKeyReg /v 'ColorizationColor'     /t REG_DWORD /d $accentHex /f *>$null
        & reg.exe add $dwmKeyReg /v 'ColorizationAfterglow' /t REG_DWORD /d $accentHex /f *>$null
        & reg.exe add $dwmKeyReg /v 'ColorPrevalence'       /t REG_DWORD /d '1'        /f *>$null
        # SSOT: theme-apply success/failure messages from [messages.theme_apply].
        $_msgThemeOk     = Get-MiosTomlValue -Section 'messages.theme_apply' -Key 'applied'          -Default '[+] Windows global theme set to MiOS palette (dark mode + #1A407F accent + transparency).'
        Write-Host "  $_msgThemeOk" -ForegroundColor DarkGray
    } catch {
        $_msgThemeFail = Get-MiosTomlValue -Section 'messages.theme_apply' -Key 'failed_template' -Default '[!] Windows theme registry write failed: {0}'
        Write-Host ('  ' + ($_msgThemeFail -f $_.Exception.Message)) -ForegroundColor Yellow
    }

    $_msgStep1     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_1_wt'           -Default '[*] Step 1/7: Installing Windows Terminal (base) via winget...'
    $_msgStep2     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_2_pwsh7'        -Default '[*] Step 2/7: Installing PowerShell 7 (pwsh) BEFORE WT profile creation...'
    $_msgStep3     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_3_wt_settings'  -Default '[*] Step 3/7: Patching WT settings.json with MiOS scheme + profiles...'
    $_msgStep4     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_4_geist_font'   -Default '[*] Step 4/7: Installing GeistMono Nerd Font (per-user, HKCU)...'
    $_msgStep5     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_5_fastfetch'    -Default '[*] Step 5/7: Installing fastfetch + staging MiOS-themed config...'
    $_msgStep6     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_6_omp'          -Default '[*] Step 6/7: oh-my-posh + PSReadLine + mios.omp.json + profile wiring...'
    $_msgStep7     = Get-MiosTomlValue -Section 'messages.steps' -Key 'step_7_extras'       -Default '[*] Step 7/7: Installing terminal completion / UX modules...'
    $_msgWtFailed  = Get-MiosTomlValue -Section 'messages.steps' -Key 'wt_failed_error'     -Default '[!] WT install failed -- bootstrap cannot continue without a themed WT to launch into.'
    $_msgWtHint    = Get-MiosTomlValue -Section 'messages.steps' -Key 'wt_failed_hint'      -Default '    Install manually and re-run: winget install Microsoft.WindowsTerminal'

    Write-Host "  $_msgStep1" -ForegroundColor Cyan
    if (-not (Install-MiOSWindowsTerminal)) {
        Write-Host "  $_msgWtFailed" -ForegroundColor Red
        Write-Host "  $_msgWtHint" -ForegroundColor DarkGray
        exit 1
    }
    Write-Host "  $_msgStep2" -ForegroundColor Cyan
    Install-MiOSPwsh7               | Out-Null
    Write-Host "  $_msgStep3" -ForegroundColor Cyan
    Install-MiOSTerminalProfile     | Out-Null
    Center-MiosBootstrapWindow
    Start-MiosBuildMonitor
    Write-Host "  $_msgStep4" -ForegroundColor Cyan
    Install-MiOSGeistFont           | Out-Null
    Install-MiOSBibataCursor        | Out-Null
    try {
        $_shortcutScript = 'C:\MiOS\usr\libexec\mios\Update-MiOSStartMenuShortcuts.ps1'
        if (Test-Path $_shortcutScript) {
            & $_shortcutScript | Out-Null
        }
    } catch { Write-Host "  [!] Update-MiOSStartMenuShortcuts failed: $($_.Exception.Message)" -ForegroundColor Yellow }
    Write-Host "  $_msgStep5" -ForegroundColor Cyan
    Install-MiOSFastfetch           | Out-Null
    Write-Host "  $_msgStep6" -ForegroundColor Cyan
    Install-MiOSLLVMMinGW           | Out-Null
    Update-MiOSOhMyPosh             | Out-Null
    Update-MiOSPSReadLine           | Out-Null
    Install-MiOSOhMyPoshTheme       | Out-Null
    Install-MiOSTmuxProfile         | Out-Null
    Install-MiOSPowerShellProfile   | Out-Null
    Write-Host "  $_msgStep7" -ForegroundColor Cyan
    Install-MiOSTerminalExtras      | Out-Null

    try {
        $_machPath = [System.Environment]::GetEnvironmentVariable('PATH','Machine')
        $_userPath = [System.Environment]::GetEnvironmentVariable('PATH','User')
        $env:PATH = (@($_machPath, $_userPath) | Where-Object { $_ }) -join ';'
    } catch {}

    # Mark this session as the MiOS terminal so the profile body's
    # WT_SESSION-or-TERM_PROGRAM=mios gate fires Show-MiosDashboard
    # (the elevated pwsh runs in conhost; WT_SESSION is unset).
    $env:TERM_PROGRAM = 'mios'
    $env:MIOS_SKIP_MOTD = '1'

    try {
        if ($PROFILE.CurrentUserAllHosts -and (Test-Path -LiteralPath $PROFILE.CurrentUserAllHosts)) {
            . $PROFILE.CurrentUserAllHosts
            Write-Host "  [+] Profile reloaded in this session (oh-my-posh + MiOS prompt active)." -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "  [!] Profile reload failed (will take effect on next pwsh launch): $($_.Exception.Message)" -ForegroundColor Yellow
    } finally {
        Remove-Item env:MIOS_SKIP_MOTD -ErrorAction SilentlyContinue
    }

    Write-Host ''
    Write-Host '+============================================================+' -ForegroundColor Cyan
    Write-Host '|  MiOS user-scope setup complete.                           |' -ForegroundColor Cyan
    Write-Host '|  Continuing with admin steps (M:\ + Podman + dev VM)...    |' -ForegroundColor Cyan
    Write-Host '+============================================================+' -ForegroundColor Cyan
    Write-Host ''
    if ($isAdmin) {
        # Already admin -- fall through to the admin-scope code below
        # (M:\ provisioning + bootstrap.ps1 hand-off). No relaunch.
    } else {
        # SHOULD BE UNREACHABLE: the auto-elevation block at script
        # entry (line ~2317) re-launches with admin token if not admin.
        # Defensive fallback only -- if we got here, the auto-elevate
        # path didn't trigger for some reason (manual MIOS_GETMIOS_
        # RELAUNCHED=1 env override, etc.). Surface a clear error.
        Write-Host '  [!] Reached admin-only code without admin token.' -ForegroundColor Red
        Write-Host '      Re-run from a fresh pwsh window so the auto-elevation prompt fires.' -ForegroundColor DarkGray
        return
        # Dead code below (kept for fallback if auto-elevation is ever
        # disabled). Reachable only if `return` above is removed.
        $rawUrl = "$($Script:MiosBootstrapRaw)/Get-MiOS.ps1?cb=$([int][double]::Parse((Get-Date -UFormat %s)))"
        $innerCmd = @"
# Resize + center BEFORE anything else paints.
try {
    `$_curW = [Console]::WindowWidth
    if (`$_curW -gt 80) {
        [Console]::SetWindowSize(80, 30)
        [Console]::SetBufferSize(80, 9000)
    } else {
        [Console]::SetBufferSize(80, 9000)
        [Console]::SetWindowSize(80, 30)
    }
} catch {}
try {
    Add-Type -Namespace MiosWin -Name N -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool MoveWindow(System.IntPtr hWnd, int x, int y, int w, int h, bool repaint);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out System.Drawing.Rectangle rect);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetDesktopWindow();
'@ -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    `$hwnd = [MiosWin.N]::GetConsoleWindow()
    `$dummy = New-Object System.Drawing.Rectangle
    [MiosWin.N]::GetWindowRect(`$hwnd, [ref]`$dummy) | Out-Null
    `$winW = `$dummy.Width  - `$dummy.X
    `$winH = `$dummy.Height - `$dummy.Y
    # Center on the ACTIVE display (cursor position), not PrimaryScreen.
    `$cur = [System.Windows.Forms.Cursor]::Position
    `$screen = [System.Windows.Forms.Screen]::FromPoint(`$cur).WorkingArea
    `$x = `$screen.X + [int](([math]::Max(0, `$screen.Width  - `$winW)) / 2)
    `$y = `$screen.Y + [int](([math]::Max(0, `$screen.Height - `$winH)) / 2)
    [MiosWin.N]::MoveWindow(`$hwnd, `$x, `$y, `$winW, `$winH, `$true) | Out-Null
} catch {}

`$env:MIOS_GETMIOS_RELAUNCHED='1'
`$env:MIOS_AGREEMENT_ACK='accepted'
try {
    `$noCacheHdr = @{ 'Cache-Control' = 'no-cache, no-store, max-age=0'; 'Pragma' = 'no-cache' }
    `$src = Invoke-RestMethod -Uri '$rawUrl' -Headers `$noCacheHdr -ErrorAction Stop
    & ([scriptblock]::Create(`$src))
} catch {
    Write-Host ''
    Write-Host ('  [!] Bootstrap failed: ' + `$_) -ForegroundColor Red
    Write-Host ''
}
Write-Host ''
Write-Host '  Press Enter to close...' -ForegroundColor DarkGray -NoNewline
`$null = Read-Host
"@
        $innerBytes   = [Text.Encoding]::Unicode.GetBytes($innerCmd)
        $innerEncoded = [Convert]::ToBase64String($innerBytes)
        # Resolve a directly-launchable pwsh (skip WindowsApps\ -- the
        # Store install's TrustedInstaller ACL blocks Start-Process
        # -Verb RunAs there).
        $shell = $null
        foreach ($c in @("$env:ProgramFiles\PowerShell\7\pwsh.exe","$env:ProgramW6432\PowerShell\7\pwsh.exe")) {
            if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) { $shell = $c; break }
        }
        if (-not $shell) {
            $w51 = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
            if (Test-Path -LiteralPath $w51 -PathType Leaf) { $shell = $w51 }
        }
        if (-not $shell) { $shell = 'powershell.exe' }
        $shellArgs = @('-NoLogo','-ExecutionPolicy','Bypass','-NoExit','-EncodedCommand', $innerEncoded)

        try {
            Start-Process -FilePath $shell -ArgumentList $shellArgs `
                -Verb RunAs -WorkingDirectory $env:WINDIR -ErrorAction Stop
            Write-Host '  [+] Elevated bootstrap window opened. Continuing the install there.' -ForegroundColor Green
        } catch {
            Write-Host "  [!] Self-elevation failed: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host '      Open an elevated PowerShell manually and re-run:' -ForegroundColor DarkGray
            Write-Host "        irm $rawUrl | iex" -ForegroundColor DarkGray
        }
        Write-Host ''
        return
    }

}

try {
    $sz  = New-Object Management.Automation.Host.Size 80, 30
    $buf = New-Object Management.Automation.Host.Size 80, 9000
    $Host.UI.RawUI.BufferSize = $buf
    $Host.UI.RawUI.WindowSize = $sz
} catch {
    try { $Host.UI.RawUI.WindowSize = New-Object Management.Automation.Host.Size 80, 30 } catch {}
}

try { Clear-Host } catch {}
Write-Host "MiOS Bootstrap (irm | iex web entry)" -ForegroundColor Cyan
Write-Host "------------------------------------" -ForegroundColor Cyan

Ensure-Git
Require-Cmd "git"    "Install Git from https://git-scm.com/download/win"
Ensure-PodmanDesktop
Write-Good "Prerequisites OK (git, podman)"

# Initialize-DataDisk + Set-PodmanMachineStorageOnM + Set-WingetStorageOnM
# are defined ABOVE (before Pass-1) so Step 0 can call them BEFORE Pass-1
# stages files. Their original definitions moved up; this header retained
# for orientation.

# Create the canonical Windows install root structure now that M:\
# is guaranteed to exist. The reset above wiped M:\MiOS, so this
# rebuilds it fresh.
$miosRepoDir = "M:\MiOS\repo"
New-Item -ItemType Directory -Path $miosRepoDir -Force -ErrorAction SilentlyContinue | Out-Null

if ((Test-Path $RepoDir) -and ($RepoDir -ne 'M:\MiOS\repo\mios-bootstrap')) {
    Write-Err "-RepoDir $RepoDir already exists. Either delete it manually, or re-run without -RepoDir to use the canonical M:\MiOS\repo\mios-bootstrap."
    exit 1
}

# Helper: run git with all streams drained via System.Diagnostics.Process
# so PowerShell's pipeline never sees stderr (no EAP=Stop trap on git's
# normal "Cloning into ..." progress banner).
function Invoke-GitProc {
    param([string[]]$ArgList, [string]$Cwd = $null)
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = 'git'
        $argumentListProperty = $psi.GetType().GetProperty('ArgumentList')
        if ($argumentListProperty) {
            $argumentList = $argumentListProperty.GetValue($psi, $null)
            foreach ($a in $ArgList) { [void]$argumentList.Add([string]$a) }
        } else {
            # Windows PowerShell 5.1 uses .NET Framework, which has no
            # ProcessStartInfo.ArgumentList. Quote according to the Windows
            # command-line rules so paths, quotes, and trailing slashes survive.
            $quotedArgs = foreach ($a in $ArgList) {
                $s = [string]$a
                if ($s.Length -gt 0 -and $s -notmatch '[\s"]') { $s; continue }
                $b = New-Object System.Text.StringBuilder
                [void]$b.Append('"')
                $slashes = 0
                foreach ($ch in $s.ToCharArray()) {
                    if ($ch -eq '\') { $slashes++; continue }
                    if ($ch -eq '"') {
                        [void]$b.Append(('\' * (2 * $slashes + 1)))
                        [void]$b.Append('"')
                        $slashes = 0
                        continue
                    }
                    if ($slashes) { [void]$b.Append(('\' * $slashes)); $slashes = 0 }
                    [void]$b.Append($ch)
                }
                if ($slashes) { [void]$b.Append(('\' * (2 * $slashes))) }
                [void]$b.Append('"')
                $b.ToString()
            }
            $psi.Arguments = $quotedArgs -join ' '
        }
        if ($Cwd) { $psi.WorkingDirectory = $Cwd }
        $psi.UseShellExecute        = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.CreateNoWindow         = $true
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        [void]$proc.Start()
        $out = $proc.StandardOutput.ReadToEnd()
        $err = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $proc.ExitCode
            Stdout   = $out
            Stderr   = $err
        }
    } catch {
        return [pscustomobject]@{ ExitCode = -1; Stdout = ''; Stderr = $_.Exception.Message }
    }
}

if (Test-Path $RepoDir) {
    if (Test-Path (Join-Path $RepoDir '.git')) {
        $sourceState = Invoke-GitProc -ArgList @('status','--porcelain','--untracked-files=normal') -Cwd $RepoDir
        if ($sourceState.ExitCode -ne 0) { throw 'Could not inspect bootstrap working tree; source update refused' }
        if ($sourceState.Stdout.Trim()) {
            Write-Info "Preserving current bootstrap checkout at $RepoDir (local changes present)."
        } else {
        Write-Info "Updating existing bootstrap clone at $RepoDir (fetch + hard reset to origin/$Branch) ..."
        $fr = Invoke-GitProc -ArgList @('fetch','--depth=1','origin',$Branch) -Cwd $RepoDir
        if ($fr.ExitCode -ne 0) {
            Write-Warning "git fetch in $RepoDir failed (exit $($fr.ExitCode)). Network may be unreachable."
            Write-Warning "Using existing staged offline clone at $RepoDir without updating."
        } else {
            $rr = Invoke-GitProc -ArgList @('reset','--hard','FETCH_HEAD') -Cwd $RepoDir
            if ($rr.ExitCode -ne 0) {
                Write-Err "git reset --hard in $RepoDir failed (exit $($rr.ExitCode))."
                Write-Err "Stderr: $($rr.Stderr.Trim())"
                exit 1
            }
            Write-Good "Bootstrap clone updated to origin/$Branch in place at $RepoDir"
        }
        }
    } elseif (@(Get-ChildItem -LiteralPath $RepoDir -Force -ErrorAction SilentlyContinue).Count -eq 0) {
        # Exists but EMPTY: a prior uninstall emptied it, yet a lingering WSL2 /
        # minifilter handle can leave the now-empty dir undeletable (rmdir ->
        # "device or resource busy", no Restart-Manager holder). git clone into
        # an existing EMPTY dir succeeds, so don't abort the whole install over
        # an empty leftover -- clone in place.
        Write-Info "$RepoDir exists but is empty (undeletable leftover) -- cloning $RepoUrl into it in place ..."
        $cr = $null
        for ($_cattempt = 1; $_cattempt -le 3; $_cattempt++) {
            $cr = Invoke-GitProc -ArgList @('clone','--branch',$Branch,'--depth','1',$RepoUrl,$RepoDir)
            if ($cr.ExitCode -eq 0) { break }
            if ($_cattempt -lt 3) { Start-Sleep -Seconds @(2,5,10)[$_cattempt-1] }
        }
        if ($cr.ExitCode -ne 0) {
            Write-Err "git clone into empty $RepoDir failed (exit $($cr.ExitCode)). Stderr: $($cr.Stderr.Trim())"
            exit 1
        }
        Write-Good "Fresh bootstrap clone into empty $RepoDir"
    } else {
        Write-Err "$RepoDir exists, is not a git repository, and is NOT empty."
        Write-Err "I won't delete it -- contents may be operator-managed. Either:"
        Write-Err "  - Move it aside:   Rename-Item `"$RepoDir`" `"$RepoDir.bak`""
        Write-Err "  - Or pass -RepoDir <other-path> to use a different target."
        exit 1
    }
} else {
    Write-Info "Cloning $RepoUrl ($Branch, depth=1) -> $RepoDir ..."
    # Ensure parent dir exists so git clone has a place to write.
    $parent = Split-Path $RepoDir -Parent
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force -ErrorAction SilentlyContinue | Out-Null
    }
    # Install-robustness retry the clone 3x with backoff. A single
    # transient network blip (raw.githubusercontent / GitHub TLS reset) otherwise
    # aborted the ENTIRE irm|iex install at the entry. Each retry wipes the partial
    # clone so it starts clean.
    $cr = $null
    for ($_cattempt = 1; $_cattempt -le 3; $_cattempt++) {
        if (Test-Path $RepoDir) { Remove-Item -Recurse -Force $RepoDir -ErrorAction SilentlyContinue }
        $cr = Invoke-GitProc -ArgList @('clone','--branch',$Branch,'--depth','1',$RepoUrl,$RepoDir)
        if ($cr.ExitCode -eq 0) { break }
        if ($_cattempt -lt 3) {
            $_cbk = @(2,5,10)[$_cattempt-1]
            Write-Info "git clone attempt $_cattempt failed (exit $($cr.ExitCode)); retrying in ${_cbk}s (transient network?)..."
            Start-Sleep -Seconds $_cbk
        }
    }
    if ($cr.ExitCode -ne 0) {
        Write-Err "git clone $RepoUrl -> $RepoDir failed after 3 attempts (exit $($cr.ExitCode))."
        Write-Err "Stderr: $($cr.Stderr.Trim())"
        Write-Err "Re-run manually to see git's diagnostic output:"
        Write-Err "  git clone --branch $Branch --depth 1 $RepoUrl `"$RepoDir`""
        exit 1
    }
    Write-Good "Fresh bootstrap clone at $RepoDir"
}

# 6. Hand off to bootstrap.ps1 (canonical split-bootstrap entry).
# The published one-liner follows the operator's SSOT installation mode.
$entry = Join-Path $RepoDir "bootstrap.ps1"
if (-not (Test-Path $entry)) {
    Write-Err "bootstrap.ps1 not found in $RepoDir (cloned with wrong branch?)"
    exit 1
}

if ($Workflow) { $env:MIOS_WORKFLOW = $Workflow }

$forwardArgs = @()
$installMode = Get-MiosTomlValue -Section 'bootstrap' -Key 'windows_install_mode' -Default 'full'
if ($installMode -notin @('full','bootstrap')) { throw 'SSOT bootstrap.windows_install_mode must be full or bootstrap' }
if ($FullBuild -or $installMode -eq 'full') { $forwardArgs += '-FullBuild' }
else { $forwardArgs += '-BootstrapOnly' }
if ($Unattended) { $forwardArgs += '-Unattended' }

Write-Info "Handing off to bootstrap.ps1 ..."
Push-Location $RepoDir
try {
    & $entry @forwardArgs
} finally { Pop-Location }
$_bootstrapExit = $LASTEXITCODE

if ($_bootstrapExit -eq 0) {
    $_msgFinalStep = Get-MiosTomlValue -Section 'messages.steps' -Key 'final_step_native_app' -Default '[*] Final step: Registering the unified MiOS Windows launcher...'
    Write-Host ''
    Write-Host "  $_msgFinalStep" -ForegroundColor Cyan
    try { Install-MiOSNativeApp | Out-Null } catch {
        Write-Host "  [!] Install-MiOSNativeApp failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    # MiOS service URLs as Windows Start Menu shortcuts (Cockpit, Code,
    # Workspace, Search, Forge, Dashboard, Guacamole). Drives the
    # mios.toml [desktop.start_menu] catalog -- WSLg's auto-publish
    # filter ignores xdg-open URL handlers, so we publish explicitly.
    try { Install-MiOSServiceShortcuts | Out-Null } catch {
        Write-Host "  [!] Install-MiOSServiceShortcuts failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    # Reconcile after the legacy app registrar so its per-action links cannot
    # reappear at the end of a fresh install. Uninstall remains in Windows Settings.
    $_nativeProjection = Join-Path $env:ProgramData 'MiOS\bin\mios-native-client-setup.ps1'
    if (Test-Path -LiteralPath $_nativeProjection) {
        & $_nativeProjection -RuntimeOnly -BinDirectory (Split-Path -Parent $_nativeProjection)
    }
}

if ($_bootstrapExit -eq 0) {
    try {
        Write-Host ''
        Write-Host '  [*] Resetting WSL/WSLg host-side state so the next launch starts clean...' -ForegroundColor Cyan
        & wsl.exe --shutdown 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        $_wslSvcRestarted = $false
        foreach ($_svc in @('WslService','LxssManager')) {
            try { Restart-Service -Name $_svc -Force -ErrorAction Stop; $_wslSvcRestarted = $true; break } catch {}
        }
        if (-not $_wslSvcRestarted) {
            Write-Host "  [!] WSL service restart skipped: neither WslService nor LxssManager present (non-fatal)." -ForegroundColor DarkGray
        }
        Write-Host '  [+] WSLg reset complete -- next MiOS terminal launch starts with fresh RDP-RAIL state.' -ForegroundColor Green
    } catch {
        Write-Host "  [!] WSLg reset step failed (non-fatal): $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

if ($_bootstrapExit -eq 0 -and -not $Unattended) {
    try {
        $_fieldSrc = Join-Path $RepoDir 'field'
        if (-not (Test-Path $_fieldSrc)) { $_fieldSrc = 'C:\mios-bootstrap\field' }
        $_fieldBat = Join-Path $_fieldSrc 'MiOS-Field.bat'
        if (Test-Path $_fieldBat) {
            Write-Host ''
            Write-Host '  MiOS is provisioned. MiOS-Field can now build a bootable USB that deploys' -ForegroundColor Cyan
            Write-Host '  MiOS (and MiOS-Xbox) onto any machine -- recovery tools, the offline Fedora' -ForegroundColor Cyan
            Write-Host '  installer, and the repo, all on one stick.' -ForegroundColor Cyan
            $_ans = Read-Host '  Launch MiOS-Field to build a deploy USB now? [y/N]'
            if ($_ans -match '^(y|yes)$') {
                Write-Host '  [*] Launching MiOS-Field (canonical .bat)...' -ForegroundColor Cyan
                # Already elevated -- launch the canonical .bat directly in a new
                # interactive console (no hardcoded-principal scheduled task).
                Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList "/c start `"MiOS-Field`" cmd.exe /k `"$_fieldBat`" flash"
            } else {
                Write-Host "  You can run it any time:  `"$_fieldBat`"" -ForegroundColor DarkGray
            }
        }
    } catch {
        Write-Host "  [!] MiOS-Field handoff prompt skipped (non-fatal): $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

exit $_bootstrapExit
