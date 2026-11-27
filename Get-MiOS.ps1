# UTF-8 source for Windows PowerShell.
# AI-hint: Primary entry point for MiOS installation; handles admin elevation, environment validation, and fresh-clone of the bootstrap repo to initiate the preflight, VM setup, and OCI build pipeline.
# AI-doc: usr/share/doc/mios/manual/root.md
<#
.SYNOPSIS
    'MiOS' bootstrap -- canonical Windows one-liner entry point.

.DESCRIPTION
    Designed for: irm https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1 | iex

    Thin entrypoint that:
      1. Keeps operator setup and MiOS-DEV in the original user token;
         elevates only fixed machine preparation through UAC.
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
    Bootstrap checkout (CurrentUser: Local AppData/MiOS/repo/mios-bootstrap;
    Administrator: system-drive mios-bootstrap). Existing local changes are preserved. Clean checkouts
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
    [string]$RepoDir   = "",
    [switch]$FullBuild,
    [switch]$Unattended,
    [string]$Workflow  = ""
)

function New-MiosGetChildArguments {
    param([string]$EntryPath, [string]$Action, [string]$RepoUrl, [string]$Branch, [string]$RepoDir, [string]$Workflow,
          [switch]$FullBuild, [switch]$Unattended)
    $arguments = @('-NoProfile', '-File', $EntryPath, '-Action', $Action, '-RepoUrl', $RepoUrl, '-Branch', $Branch)
    if (-not [string]::IsNullOrWhiteSpace($RepoDir)) { $arguments += @('-RepoDir', $RepoDir) }
    if ($Workflow) { $arguments += @('-Workflow', $Workflow) }
    if ($FullBuild) { $arguments += '-FullBuild' }
    if ($Unattended) { $arguments += '-Unattended' }
    return $arguments
}

$ErrorActionPreference = "Stop"
$Script:MiosEntrySource = if ($PSCommandPath) { [IO.File]::ReadAllText($PSCommandPath) } else { $MyInvocation.MyCommand.Definition }

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
        $childArgs = New-MiosGetChildArguments -EntryPath $entryPath -Action $Action -RepoUrl $RepoUrl -Branch $Branch `
            -RepoDir $RepoDir -Workflow $Workflow -FullBuild:$FullBuild -Unattended:$Unattended

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

function Test-MiosElevated {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-MiosWindowsInstallRoot {
    param([bool]$Elevated = (Test-MiosElevated),
          [string]$LocalAppData = ([Environment]::GetFolderPath('LocalApplicationData')),
          [string]$SystemDrive = $env:SystemDrive)
    if ($Elevated) { return Join-Path $SystemDrive 'MiOS' }
    if (-not $LocalAppData) { throw 'The operator Local AppData folder is unavailable.' }
    return Join-Path $LocalAppData 'MiOS'
}

# The installed Windows layout, the one source for every native-bin default and both PowerShell profile redirectors.
# build-mios.ps1 carries the same function; tests/test_install_native_layout.ps1 holds the two to one answer.
# Unelevated: %LOCALAPPDATA%\MiOS, User scope. Elevated: %SystemDrive%\MiOS, Machine scope. MIOS_NATIVE_BIN overrides
# only the native bin.
function Get-MiosInstalledLayout {
    param([bool]$Elevated = (Test-MiosElevated))
    $root = Get-MiosWindowsInstallRoot -Elevated $Elevated
    $bin = Join-Path $root 'bin'
    return [pscustomobject]@{
        Root        = $root
        Bin         = $bin
        NativeBin   = if ($env:MIOS_NATIVE_BIN) { $env:MIOS_NATIVE_BIN } else { $bin }
        ProfileBody = Join-Path $root 'powershell\profile.ps1'
        Scope       = if ($Elevated) { 'Machine' } else { 'User' }
    }
}

function Start-MiosBuildMonitor {
    if ($env:MIOS_NO_MONITOR -in @('1','true','yes','on') -or
        $env:MIOS_HEADLESS -in @('1','true','yes','on')) { return }
    $launcher = Join-Path (Get-MiosInstalledLayout).NativeBin 'mios-launch.exe'
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

# Host projections contain no credential fields. This conservative lexical reader accepts bare/quoted dotted keys,
# tables, arrays, inline tables and multiline strings; unsupported key escapes/syntax fail before a public write.
# Include the existing SSOT aliases (ttyd.auth_pass, pgvector.pass, auth.github_pat and bcrypt hashes).
$Script:MiosCredentialKeyRe = '(?i)(^|[._-])(password|passwd|pass|passphrase|secret|token|pat|otp|api_key|private_key)(_(hash|bcrypt))?$'

function Get-MiosTomlAssignments {
    param([AllowEmptyString()][string]$Text)
    $state = @{ Text = $Text; Pos = 0; Length = $Text.Length; Names = $null }
    function Stop-MiosTomlRead {
        $line = 1 + ([regex]::Matches($state.Text.Substring(0, $state.Pos), "`n")).Count
        throw "Unsupported or invalid TOML at line $line; no host projection was written."
    }
    function Skip-MiosTomlSpace([bool]$Newlines) {
        while ($state.Pos -lt $state.Length) {
            $c = $state.Text[$state.Pos]
            if ($c -eq ' ' -or $c -eq "`t" -or ($Newlines -and ($c -eq "`r" -or $c -eq "`n"))) { $state.Pos++; continue }
            if ($Newlines -and $c -eq '#') {
                while ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -ne "`n") { $state.Pos++ }
                continue
            }
            break
        }
    }
    function Read-MiosTomlString([bool]$Key) {
        $quote = $state.Text[$state.Pos]; $state.Pos++
        $triple = -not $Key -and $state.Pos + 1 -lt $state.Length -and
                  $state.Text[$state.Pos] -eq $quote -and $state.Text[$state.Pos + 1] -eq $quote
        if ($triple) { $state.Pos += 2 }
        $start = $state.Pos
        while ($state.Pos -lt $state.Length) {
            $c = $state.Text[$state.Pos]
            if (-not $triple -and ($c -eq "`r" -or $c -eq "`n")) { Stop-MiosTomlRead }
            if ($c -eq '\' -and $quote -eq '"') {
                # Quoted-key escapes are deliberately unsupported, rather than misclassified as non-secret keys.
                if ($Key) { Stop-MiosTomlRead }
                $state.Pos++
                if ($state.Pos -ge $state.Length) { Stop-MiosTomlRead }
                $escape = $state.Text[$state.Pos]
                if ($triple -and [char]::IsWhiteSpace($escape)) {
                    while ($state.Pos -lt $state.Length -and [char]::IsWhiteSpace($state.Text[$state.Pos])) { $state.Pos++ }
                    continue
                }
                if ('"\btnfr'.Contains([string]$escape)) { $state.Pos++; continue }
                if ($escape -eq 'u' -or $escape -eq 'U') {
                    $digits = if ($escape -eq 'u') { 4 } else { 8 }
                    $state.Pos++
                    if ($state.Pos + $digits -gt $state.Length -or $state.Text.Substring($state.Pos, $digits) -notmatch '^[0-9A-Fa-f]+$') { Stop-MiosTomlRead }
                    $state.Pos += $digits; continue
                }
                Stop-MiosTomlRead
            }
            if ($c -eq $quote) {
                $end = $state.Pos; $count = 0
                while ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq $quote) { $state.Pos++; $count++ }
                if (-not $triple) {
                    # Empty strings are legal; a following quote starts an unsupported adjacent value.
                    $state.Pos = $end + 1
                    return $state.Text.Substring($start, $end - $start)
                }
                if ($count -ge 3) {
                    if ($count -gt 5) { Stop-MiosTomlRead }
                    return ''
                }
                continue
            }
            $state.Pos++
        }
        Stop-MiosTomlRead
    }
    function Read-MiosTomlKey {
        $parts = New-Object System.Collections.Generic.List[string]
        while ($true) {
            Skip-MiosTomlSpace $false
            if ($state.Pos -ge $state.Length) { Stop-MiosTomlRead }
            $c = $state.Text[$state.Pos]
            if ($c -eq '"' -or $c -eq "'") { $part = Read-MiosTomlString $true }
            else {
                $start = $state.Pos
                while ($state.Pos -lt $state.Length -and [string]$state.Text[$state.Pos] -match '^[A-Za-z0-9_-]$') { $state.Pos++ }
                if ($start -eq $state.Pos) { Stop-MiosTomlRead }
                $part = $state.Text.Substring($start, $state.Pos - $start)
            }
            $parts.Add($part)
            Skip-MiosTomlSpace $false
            if ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq '.') { $state.Pos++; continue }
            return ,$parts.ToArray()
        }
    }
    function Read-MiosTomlValue([string]$Prefix, [int]$Depth) {
        if ($Depth -gt 64 -or $state.Pos -ge $state.Length) { Stop-MiosTomlRead }
        $c = $state.Text[$state.Pos]
        if ($c -eq '"' -or $c -eq "'") { $null = Read-MiosTomlString $false; return }
        if ($c -eq '[') {
            $state.Pos++; Skip-MiosTomlSpace $true
            if ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq ']') { $state.Pos++; return }
            while ($true) {
                Read-MiosTomlValue ($Prefix + '[]') ($Depth + 1)
                Skip-MiosTomlSpace $true
                if ($state.Pos -ge $state.Length) { Stop-MiosTomlRead }
                $c = $state.Text[$state.Pos]; $state.Pos++
                if ($c -eq ']') { return }
                if ($c -ne ',') { Stop-MiosTomlRead }
                Skip-MiosTomlSpace $true
                if ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq ']') { $state.Pos++; return }
            }
        }
        if ($c -eq '{') {
            $state.Pos++; Skip-MiosTomlSpace $false
            if ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq '}') { $state.Pos++; return }
            while ($true) {
                $key = Read-MiosTomlKey
                $name = $Prefix + '.' + ($key -join '.')
                if (@($key | Where-Object { $_ -match $Script:MiosCredentialKeyRe }).Count) { $state.Names.Add($name) }
                if ($state.Pos -ge $state.Length -or $state.Text[$state.Pos] -ne '=') { Stop-MiosTomlRead }
                $state.Pos++; Skip-MiosTomlSpace $false
                Read-MiosTomlValue $name ($Depth + 1)
                Skip-MiosTomlSpace $false
                if ($state.Pos -ge $state.Length) { Stop-MiosTomlRead }
                $c = $state.Text[$state.Pos]; $state.Pos++
                if ($c -eq '}') { return }
                if ($c -ne ',') { Stop-MiosTomlRead }
            }
        }
        $start = $state.Pos
        while ($state.Pos -lt $state.Length -and -not (',]}#'.Contains([string]$state.Text[$state.Pos])) -and $state.Text[$state.Pos] -ne "`r" -and $state.Text[$state.Pos] -ne "`n") { $state.Pos++ }
        $scalar = $state.Text.Substring($start, $state.Pos - $start).Trim()
        if ($scalar -notmatch '^(true|false|[+-]?(inf|nan)|[+-]?[0-9][0-9A-Fa-f_xob.:+TZtz -]*([eE][+-]?[0-9_]+)?)$') { Stop-MiosTomlRead }
    }
    $rows = New-Object System.Collections.Generic.List[object]
    $section = @()
    while ($true) {
        Skip-MiosTomlSpace $true
        if ($state.Pos -ge $state.Length) { break }
        $start = $state.Pos
        if ($state.Text[$state.Pos] -eq '[') {
            $state.Pos++; $array = $state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq '['
            if ($array) { $state.Pos++ }
            $section = Read-MiosTomlKey
            if (@($section | Where-Object { $_ -match $Script:MiosCredentialKeyRe }).Count) {
                throw 'A credential field cannot be projected as a TOML table; no public write was made.'
            }
            if ($state.Pos -ge $state.Length -or $state.Text[$state.Pos] -ne ']') { Stop-MiosTomlRead }
            $state.Pos++
            if ($array) {
                if ($state.Pos -ge $state.Length -or $state.Text[$state.Pos] -ne ']') { Stop-MiosTomlRead }
                $state.Pos++
            }
            $row = $null
        } else {
            $key = Read-MiosTomlKey
            $name = (@($section) + @($key)) -join '.'
            $state.Names = New-Object System.Collections.Generic.List[string]
            if (@($key | Where-Object { $_ -match $Script:MiosCredentialKeyRe }).Count) { $state.Names.Add($name) }
            if ($state.Pos -ge $state.Length -or $state.Text[$state.Pos] -ne '=') { Stop-MiosTomlRead }
            $state.Pos++; Skip-MiosTomlSpace $false
            $valueStart = $state.Pos
            Read-MiosTomlValue $name 0
            $row = @{ Start = $start; End = $state.Pos; Names = $state.Names.ToArray();
                      Empty = $state.Text.Substring($valueStart, $state.Pos - $valueStart).Trim() -match '^(""|''''|\[\s*\]|\{\s*\})$' }
        }
        Skip-MiosTomlSpace $false
        if ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -eq '#') {
            while ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -ne "`n") { $state.Pos++ }
        }
        if ($state.Pos -lt $state.Length -and $state.Text[$state.Pos] -ne "`r" -and $state.Text[$state.Pos] -ne "`n") { Stop-MiosTomlRead }
        if ($row) { $row.End = $state.Pos; $rows.Add($row) }
    }
    return $rows.ToArray()
}

function Split-MiosTomlKeyLine {
    # Small scalar-row reader for the SSOT rule tables; unsupported syntax fails closed.
    param([string]$Line)
    $t = $Line.Trim()
    if (-not $t -or $t.StartsWith('#')) { return $null }
    $m = [regex]::Match($t, '^(?<key>[A-Za-z0-9_-]+)\s*=\s*(?<val>.*)$')
    if (-not $m.Success) { throw 'Unsupported SSOT rule table key syntax.' }
    return ,@($m.Groups['key'].Value, $m.Groups['val'].Value)
}

function Get-MiosTomlCredentialKeys {
    param([AllowEmptyString()][string]$Text)
    foreach ($row in @(Get-MiosTomlAssignments $Text)) {
        if (-not $row.Empty) { foreach ($name in $row.Names) { $name } }
    }
}

function ConvertTo-MiosCredentialFreeToml {
    param([AllowEmptyString()][string]$Text)
    $rows = @(Get-MiosTomlAssignments $Text)
    $out = New-Object System.Text.StringBuilder
    $start = 0
    foreach ($row in $rows) {
        if (-not $row.Names.Count) { continue }
        $null = $out.Append($Text.Substring($start, $row.Start - $start))
        $start = $row.End
    }
    $null = $out.Append($Text.Substring($start))
    return $out.ToString()
}

function Assert-MiosNoReparsePath {
    param([Parameter(Mandatory)][string]$Path)
    $p = [IO.Path]::GetFullPath($Path)
    while ($p) {
        $item = $null
        try { $item = Get-Item -LiteralPath $p -Force -ErrorAction Stop }
        catch { if ($_.CategoryInfo.Category -ne [Management.Automation.ErrorCategory]::ObjectNotFound) { throw } }
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'A TOML path passes through a reparse point; refusing to write.' }
        $next = Split-Path -Parent $p
        if ($next -eq $p) { break }
        $p = $next
    }
}

function Set-MiosHostTierAcl {
    param([Parameter(Mandatory)][string]$Path, [switch]$Private)
    Assert-MiosNoReparsePath $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    $isDir = $item.PSIsContainer
    $acl = if ($isDir) { New-Object System.Security.AccessControl.DirectorySecurity } else { New-Object System.Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $groups = @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'))
    if (-not $Private) { $groups += ,@('S-1-5-32-545', 'ReadAndExecute') }
    foreach ($g in $groups) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier $g[0]
        $rule = if ($isDir) {
            New-Object System.Security.AccessControl.FileSystemAccessRule($sid, $g[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        } else { New-Object System.Security.AccessControl.FileSystemAccessRule($sid, $g[1], 'Allow') }
        $acl.AddAccessRule($rule)
    }
    $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier 'S-1-5-32-544'))
    # Persist only the modified owner/DACL. Set-Acl can request SACL privilege after File.Replace.
    $info = if ($isDir) { New-Object IO.DirectoryInfo $Path } else { New-Object IO.FileInfo $Path }
    try {
        if ($PSVersionTable.PSEdition -eq 'Desktop') { $info.SetAccessControl($acl) }
        else { [IO.FileSystemAclExtensions]::SetAccessControl($info, $acl) }
    } catch { throw }
}

function Initialize-MiosHostTierDirectory {
    param([Parameter(Mandatory)][string]$Path)
    $dir = Split-Path -Parent ([IO.Path]::GetFullPath($Path)); $etc = Split-Path -Parent $dir
    if ((Split-Path -Leaf $Path) -ine 'mios.toml' -or (Split-Path -Leaf $dir) -ine 'mios' -or (Split-Path -Leaf $etc) -ine 'etc') {
        throw 'A host TOML destination must be etc/mios/mios.toml.'
    }
    Assert-MiosNoReparsePath $Path
    # Both levels, vendor-style: an existing directory must already pass admission; an absent one is created atomically protected.
    foreach ($d in @($etc, $dir)) {
        if (Test-Path -LiteralPath $d) { Assert-MiosMachineWriteBoundary $d } else { New-MiosProtectedMachineDirectory $d }
    }
    Assert-MiosNoReparsePath $Path
}

function Write-MiosHostToml {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    # Restrict an old raw file before parsing: unsupported TOML or ACL failure must not leave readable credentials.
    Assert-MiosNoReparsePath $Path
    if (Test-Path -LiteralPath $Path) { Set-MiosHostTierAcl -Path $Path -Private }
    Initialize-MiosHostTierDirectory $Path
    if (Test-Path -LiteralPath $Path) { Set-MiosHostTierAcl -Path $Path -Private }
    $proj = ConvertTo-MiosCredentialFreeToml $Text
    if (@(Get-MiosTomlCredentialKeys $proj).Count) { throw 'The host projection still contains credential fields; refusing to write.' }
    $pending = Join-Path (Split-Path -Parent $Path) ('.mios-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        New-Item -ItemType File -Path $pending -ErrorAction Stop | Out-Null
        Set-MiosHostTierAcl -Path $pending -Private
        [IO.File]::WriteAllText($pending, $proj, (New-Object System.Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($pending, $Path, [System.Management.Automation.Language.NullString]::Value) } else { [IO.File]::Move($pending, $Path) }
        Set-MiosHostTierAcl $Path
    } finally {
        if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Force -ErrorAction Stop }
    }
}

function New-MiosMachineCreationAcl {
    param([switch]$Directory, [switch]$Private)
    $acl = if ($Directory) { New-Object Security.AccessControl.DirectorySecurity } else { New-Object Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $groups = @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'))
    if (-not $Private) { $groups += ,@('S-1-5-32-545', 'ReadAndExecute') }
    foreach ($group in $groups) {
        $sid = New-Object Security.Principal.SecurityIdentifier $group[0]
        $rule = if ($Directory) { New-Object Security.AccessControl.FileSystemAccessRule($sid, $group[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow') }
                else { New-Object Security.AccessControl.FileSystemAccessRule($sid, $group[1], 'Allow') }
        $acl.AddAccessRule($rule)
    }
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'))
    return $acl
}

function New-MiosProtectedMachineDirectory {
    param([Parameter(Mandatory)][string]$Path)
    Assert-MiosNoReparsePath $Path
    $acl = New-MiosMachineCreationAcl -Directory
    if ($PSVersionTable.PSEdition -eq 'Desktop') { $null = [IO.Directory]::CreateDirectory($Path, $acl) }
    else {
        # Core exposes ACL creation as an extension; support both documented directory overload shapes.
        $methods = @([IO.FileSystemAclExtensions].GetMethods())
        $method = @($methods | Where-Object { $_.Name -eq 'CreateDirectory' -and $_.GetParameters().Count -eq 2 -and
            $_.GetParameters()[0].ParameterType -eq [Security.AccessControl.DirectorySecurity] -and $_.GetParameters()[1].ParameterType -eq [string] }) | Select-Object -First 1
        # Reflection needs the raw .NET objects, not the PSObject wrappers function output carries.
        if ($method) { $null = $method.Invoke($null, [object[]]@($acl.psobject.BaseObject, $Path)) }
        else {
            $method = @($methods | Where-Object { $_.Name -eq 'Create' -and $_.GetParameters().Count -eq 2 -and
                $_.GetParameters()[0].ParameterType -eq [IO.DirectoryInfo] -and $_.GetParameters()[1].ParameterType -eq [Security.AccessControl.DirectorySecurity] }) | Select-Object -First 1
            if (-not $method) { throw 'Atomic directory creation with a protected ACL is unavailable.' }
            $null = $method.Invoke($null, [object[]]@((New-Object IO.DirectoryInfo $Path).psobject.BaseObject, $acl.psobject.BaseObject))
        }
    }
    # A concurrent pre-existing directory must pass the same boundary checks; never take over its ACL or owner.
    Assert-MiosMachineWriteBoundary $Path
}

function Write-MiosPrivateMachineFile {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][byte[]]$Bytes)
    Assert-MiosNoReparsePath $Path
    $acl = New-MiosMachineCreationAcl -Private
    if ($PSVersionTable.PSEdition -eq 'Desktop') {
        $stream = New-Object IO.FileStream($Path, [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::Write,
            [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $acl)
    } else {
        $method = @([IO.FileSystemAclExtensions].GetMethods() | Where-Object { $_.Name -eq 'Create' -and $_.GetParameters().Count -eq 7 -and
            $_.GetParameters()[0].ParameterType -eq [IO.FileInfo] -and $_.GetParameters()[6].ParameterType -eq [Security.AccessControl.FileSecurity] }) | Select-Object -First 1
        if (-not $method) { throw 'Atomic file creation with a protected ACL is unavailable.' }
        $stream = $method.Invoke($null, [object[]]@((New-Object IO.FileInfo $Path).psobject.BaseObject, [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::Write,
            [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $acl.psobject.BaseObject))
    }
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush() } finally { $stream.Dispose() }
}

function Assert-MiosMachineWriteBoundary {
    param([Parameter(Mandatory)][string]$Path)
    Assert-MiosNoReparsePath $Path
    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $trusted) { throw 'An existing machine TOML path has an untrusted owner.' }
    $write = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        # InheritOnly has no rights on this existing object. Every new child is created atomically with a protected DACL.
        if ($rule.AccessControlType -ne 'Allow' -or ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)) { continue }
        if (($rule.FileSystemRights -band $write) -and $rule.IdentityReference.Value -notin $trusted) {
            throw 'An existing machine TOML path allows an untrusted account to modify or replace it.'
        }
    }
}

function Initialize-MiosMachineBoundaryApi {
    if (-not ('MiosMachineBoundaryNative' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public sealed class MiosBoundaryInfo {
    public uint Attributes; public uint Links; public uint Volume; public ulong Index; public long Created; public long Written; public ulong Size;
}
public static class MiosMachineBoundaryNative {
    [StructLayout(LayoutKind.Sequential)] struct FT { public uint Low; public uint High; }
    [StructLayout(LayoutKind.Sequential)] struct BHFI { public uint Attributes; public FT Created; public FT Accessed; public FT Written;
        public uint Volume; public uint SizeHigh; public uint SizeLow; public uint Links; public uint IndexHigh; public uint IndexLow; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetFileInformationByHandle(SafeFileHandle handle, out BHFI info);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
    [DllImport("advapi32.dll")]
    static extern uint GetSecurityInfo(SafeFileHandle handle, int type, uint info, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll")] static extern uint SetSecurityInfo(SafeFileHandle handle, int type, uint info, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(IntPtr descriptor, uint revision, uint info, out IntPtr text, out uint length);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint revision, out IntPtr descriptor, out uint length);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool GetSecurityDescriptorOwner(IntPtr descriptor, out IntPtr owner, out bool defaulted);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool GetSecurityDescriptorDacl(IntPtr descriptor, out bool present, out IntPtr dacl, out bool defaulted);
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool GetSecurityDescriptorControl(IntPtr descriptor, out ushort control, out uint revision);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
    [StructLayout(LayoutKind.Sequential)] struct LUID { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] struct TOKEN_PRIVILEGES { public uint Count; public LUID Luid; public uint Attributes; }
    [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool LookupPrivilegeValueW(string system, string name, out LUID luid);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll, ref TOKEN_PRIVILEGES state, uint length, IntPtr previous, IntPtr returnLength);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DuplicateHandle(IntPtr sourceProcess, SafeFileHandle source, IntPtr targetProcess, out SafeFileHandle target,
        uint access, bool inherit, uint options);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct FINDSTREAM { public long Size; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 296)] public string Name; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr FindFirstStreamW(string path, int level, out FINDSTREAM data, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool FindNextStreamW(IntPtr find, out FINDSTREAM data);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool FindClose(IntPtr find);
    const uint OwnerInfo = 0x1, DaclInfo = 0x4, ProtectedDacl = 0x80000000, UnprotectedDacl = 0x20000000;
    // Every data stream of a file, the unnamed "::$DATA" included; ERROR_HANDLE_EOF (38) ends the list.
    public static string[] Streams(string path) {
        System.Collections.Generic.List<string> names = new System.Collections.Generic.List<string>();
        FINDSTREAM data;
        IntPtr find = FindFirstStreamW(path, 0, out data, 0u);
        if (find == new IntPtr(-1)) {
            int error = Marshal.GetLastWin32Error();
            if (error == 38) { return names.ToArray(); }
            throw new Win32Exception(error, "Cannot list the streams of " + path);
        }
        try {
            do { names.Add(data.Name); } while (FindNextStreamW(find, out data));
            int last = Marshal.GetLastWin32Error();
            if (last != 38) { throw new Win32Exception(last, "Cannot list the streams of " + path); }
        } finally { FindClose(find); }
        return names.ToArray();
    }
    // Restoring another account as owner needs SeRestorePrivilege; an elevated token holds it disabled.
    public static void EnablePrivilege(string name) {
        IntPtr token;
        if (!OpenProcessToken(GetCurrentProcess(), 0x20u | 0x8u, out token)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try {
            LUID luid;
            if (!LookupPrivilegeValueW(null, name, out luid)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
            TOKEN_PRIVILEGES state = new TOKEN_PRIVILEGES(); state.Count = 1; state.Luid = luid; state.Attributes = 0x2;
            if (!AdjustTokenPrivileges(token, false, ref state, 0u, IntPtr.Zero, IntPtr.Zero)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
            int error = Marshal.GetLastWin32Error();
            if (error == 1300) { throw new Win32Exception(error, name + " is not held by this token."); }
        } finally { CloseHandle(token); }
    }
    // DACL only: unprotected makes Windows recompute this object's inherited ACEs from its parent.
    public static void SetDacl(SafeFileHandle handle, string sddl, bool isProtected) {
        IntPtr descriptor; uint length;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1u, out descriptor, out length)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try {
            IntPtr dacl; bool present, defaulted;
            if (!GetSecurityDescriptorDacl(descriptor, out present, out dacl, out defaulted) || !present || dacl == IntPtr.Zero) {
                throw new InvalidOperationException("The descriptor has no DACL.");
            }
            uint result = SetSecurityInfo(handle, 1, DaclInfo | (isProtected ? ProtectedDacl : UnprotectedDacl), IntPtr.Zero, IntPtr.Zero, dacl, IntPtr.Zero);
            if (result != 0) { throw new Win32Exception((int)result); }
        } finally { LocalFree(descriptor); }
    }
    // READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_READ_DATA (+ WRITE_DAC | WRITE_OWNER); OPEN_EXISTING; BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
    // A write handle shares read only, so no one else may write or delete the object while it is held. Windows enforces
    // share modes only for handles with data access, so the write handle carries FILE_READ_DATA (FILE_LIST_DIRECTORY).
    public static SafeFileHandle Open(string path, bool write) {
        uint access = 0x20000u | 0x80u | (write ? (0x1u | 0x40000u | 0x80000u) : 0x1u);
        SafeFileHandle handle = CreateFileW(path, access, write ? 1u : 7u, IntPtr.Zero, 3u, 0x02000000u | 0x00200000u, IntPtr.Zero);
        if (handle.IsInvalid) { throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot open " + path); }
        return handle;
    }
    // Duplicate the already-validated data handle; never reopen a file by path. Disposing the stream closes only the duplicate,
    // so the caller retains the original share-read-only handle through validation and SetSecurityInfo.
    static System.IO.FileStream DataStream(SafeFileHandle handle) {
        SafeFileHandle copy;
        IntPtr process = GetCurrentProcess();
        if (!DuplicateHandle(process, handle, process, out copy, 0u, false, 0x2u)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        System.IO.FileStream stream = null;
        try {
            stream = new System.IO.FileStream(copy, System.IO.FileAccess.Read);
            stream.Position = 0;
            return stream;
        } catch { if (stream != null) { stream.Dispose(); } else { copy.Dispose(); } throw; }
    }
    public static byte[] ReadBytes(SafeFileHandle handle) {
        using (System.IO.FileStream stream = DataStream(handle)) {
            using (System.IO.MemoryStream bytes = new System.IO.MemoryStream()) { stream.CopyTo(bytes); return bytes.ToArray(); }
        }
    }
    public static string Sha256(SafeFileHandle handle) {
        using (System.IO.FileStream stream = DataStream(handle)) {
            using (System.Security.Cryptography.SHA256 hash = System.Security.Cryptography.SHA256.Create()) {
                return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
            }
        }
    }
    // An ancestor pin (FILE_LIST_DIRECTORY, so its share mode is enforced) shares read only: while it is held no one
    // else may open the directory to write, rename or remove it.
    public static SafeFileHandle Pin(string path) {
        SafeFileHandle handle = CreateFileW(path, 0x1u | 0x20000u | 0x80u, 1u, IntPtr.Zero, 3u, 0x02000000u | 0x00200000u, IntPtr.Zero);
        if (handle.IsInvalid) { throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot pin " + path); }
        return handle;
    }
    public static MiosBoundaryInfo Info(SafeFileHandle handle) {
        BHFI raw;
        if (!GetFileInformationByHandle(handle, out raw)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        MiosBoundaryInfo info = new MiosBoundaryInfo();
        info.Attributes = raw.Attributes; info.Links = raw.Links; info.Volume = raw.Volume;
        info.Index = ((ulong)raw.IndexHigh << 32) | raw.IndexLow; info.Size = ((ulong)raw.SizeHigh << 32) | raw.SizeLow;
        info.Created = (long)(((ulong)raw.Created.High << 32) | raw.Created.Low);
        info.Written = (long)(((ulong)raw.Written.High << 32) | raw.Written.Low);
        return info;
    }
    public static string FinalPath(SafeFileHandle handle) {
        StringBuilder buffer = new StringBuilder(4096);
        uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0u);
        if (length == 0 || length >= buffer.Capacity) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        string path = buffer.ToString();
        if (path.StartsWith(@"\\?\UNC\", StringComparison.Ordinal)) { return @"\\" + path.Substring(8); }
        if (path.StartsWith(@"\\?\", StringComparison.Ordinal)) { return path.Substring(4); }
        return path;
    }
    public static string GetSddl(SafeFileHandle handle) {
        IntPtr owner, group, dacl, sacl, descriptor;
        uint result = GetSecurityInfo(handle, 1, OwnerInfo | DaclInfo, out owner, out group, out dacl, out sacl, out descriptor);
        if (result != 0) { throw new Win32Exception((int)result); }
        try {
            IntPtr text; uint length;
            if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1u, OwnerInfo | DaclInfo, out text, out length)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            try { return Marshal.PtrToStringUni(text); } finally { LocalFree(text); }
        } finally { LocalFree(descriptor); }
    }
    public static void SetSddl(SafeFileHandle handle, string sddl) {
        IntPtr descriptor; uint length;
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1u, out descriptor, out length)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try {
            IntPtr owner, dacl; bool defaulted, present; ushort control; uint revision;
            if (!GetSecurityDescriptorOwner(descriptor, out owner, out defaulted) || owner == IntPtr.Zero) { throw new InvalidOperationException("The descriptor has no owner."); }
            if (!GetSecurityDescriptorDacl(descriptor, out present, out dacl, out defaulted) || !present || dacl == IntPtr.Zero) {
                throw new InvalidOperationException("The descriptor has no DACL.");
            }
            if (!GetSecurityDescriptorControl(descriptor, out control, out revision)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
            uint info = OwnerInfo | DaclInfo | ((control & 0x1000) != 0 ? ProtectedDacl : UnprotectedDacl);
            uint result = SetSecurityInfo(handle, 1, info, owner, IntPtr.Zero, dacl, IntPtr.Zero);
            if (result != 0) { throw new Win32Exception((int)result); }
        } finally { LocalFree(descriptor); }
    }
}
'@ -ErrorAction Stop
    }
}

function Get-MiosMachineBoundaryGap {
    param([Parameter(Mandatory)][string]$Path)
    try { Assert-MiosMachineWriteBoundary $Path; return '' } catch { return $_.Exception.Message }
}

function Read-MiosBoundaryObject {
    # One object read by handle. Strict mode refuses reparse points, other final paths and hard-linked files; -Lenient only records them.
    param([Parameter(Mandatory)][string]$Path, [switch]$WithBytes, [switch]$Lenient)
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $handle = [MiosMachineBoundaryNative]::Open($full, $false)
    $sha = ''; $bytes = $null
    try {
        $info = [MiosMachineBoundaryNative]::Info($handle)
        $isReparse = [bool]($info.Attributes -band 0x400)
        $isDirectory = [bool]($info.Attributes -band 0x10)
        if (-not $Lenient) {
            if ($isReparse) { throw "Boundary recovery refuses a reparse point: $full" }
            $final = [MiosMachineBoundaryNative]::FinalPath($handle).TrimEnd('\')
            if ($final -ine $full) { throw "Boundary recovery refuses a path that resolves elsewhere: $full" }
            if (-not $isDirectory -and $info.Links -ne 1) { throw "Boundary recovery refuses a hard-linked file: $full" }
        }
        $sddl = [MiosMachineBoundaryNative]::GetSddl($handle)
        if (-not $isDirectory -and -not $isReparse) {
            $content = [MiosMachineBoundaryNative]::ReadBytes($handle)
            $hasher = [Security.Cryptography.SHA256]::Create()
            try { $sha = [BitConverter]::ToString($hasher.ComputeHash($content)).Replace('-', '').ToLowerInvariant() } finally { $hasher.Dispose() }
            if ($WithBytes) { $bytes = [Convert]::ToBase64String($content) }
            $afterRead = [MiosMachineBoundaryNative]::Info($handle)
            foreach ($field in 'Attributes', 'Links', 'Volume', 'Index', 'Created', 'Written', 'Size') {
                if ($afterRead.$field -ne $info.$field) { throw "Boundary recovery object changed while its bytes were read: $full" }
            }
            if ([MiosMachineBoundaryNative]::GetSddl($handle) -cne $sddl) { throw "Boundary recovery descriptor changed while its bytes were read: $full" }
        }
    } finally { $handle.Dispose() }
    $record = [ordered]@{
        Path = $full; Kind = $(if ($isDirectory) { 'directory' } else { 'file' })
        Owner = (New-Object Security.AccessControl.RawSecurityDescriptor $sddl).Owner.Value; Sddl = $sddl
        Attributes = [string]$info.Attributes; Links = [string]$info.Links; Volume = [string]$info.Volume; Index = ('{0:x16}' -f $info.Index)
        Created = [string]$info.Created; Written = [string]$info.Written; Size = [string]$info.Size; Sha256 = $sha
    }
    if ($WithBytes) { $record.Bytes = $bytes }
    return [pscustomobject]$record
}

function Get-MiosBoundaryDescendants {
    # Bounded, breadth-first, parents before children; a reparse point anywhere below is refused.
    param([Parameter(Mandatory)][string]$Path, [int]$Limit = 512)
    $found = New-Object System.Collections.Generic.List[string]
    $queue = New-Object System.Collections.Generic.Queue[string]
    $queue.Enqueue([IO.Path]::GetFullPath($Path))
    while ($queue.Count) {
        foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($queue.Dequeue())) {
            if ($found.Count -ge $Limit) { throw "Boundary recovery refuses more than $Limit descendants below $Path" }
            $attributes = [IO.File]::GetAttributes($entry)
            if ($attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Boundary recovery refuses a reparse point below ${Path}: $entry" }
            $found.Add($entry)
            if ($attributes -band [IO.FileAttributes]::Directory) { $queue.Enqueue($entry) }
        }
    }
    return $found.ToArray()
}

function Get-MiosExplicitAces {
    param([Parameter(Mandatory)][string]$Sddl)
    $acl = (New-Object Security.AccessControl.RawSecurityDescriptor $Sddl).DiscretionaryAcl
    return @(@($acl) | Where-Object { -not ([int]$_.AceFlags -band 0x10) } |   # 0x10 INHERITED_ACE
        ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.AceType, $_.AceFlags, $_.AccessMask, $_.SecurityIdentifier.Value })
}

function Test-MiosSameExplicitAces {
    param([Parameter(Mandatory)][string]$Before, [Parameter(Mandatory)][string]$After)
    return [bool](((Get-MiosExplicitAces $Before) -join ';') -ceq ((Get-MiosExplicitAces $After) -join ';'))
}

function Get-MiosInheritedWriteGrants {
    # Inherited allow ACEs that still give a non-trusted account write, delete or ownership rights on this object.
    param([Parameter(Mandatory)][string]$Sddl)
    $raw = New-Object Security.AccessControl.RawSecurityDescriptor $Sddl
    if ([int]$raw.ControlFlags -band 0x1000) { return @() }
    $write = 0x116 -bor 0x10000 -bor 0x40 -bor 0x40000 -bor 0x80000 -bor 0x40000000 -bor 0x10000000
    return @(@($raw.DiscretionaryAcl) | Where-Object { ([int]$_.AceFlags -band 0x10) -and -not ([int]$_.AceFlags -band 0x08) -and
        [string]$_.AceQualifier -eq 'AccessAllowed' -and ($_.AccessMask -band $write) -and $_.SecurityIdentifier.Value -notin @('S-1-5-18', 'S-1-5-32-544') } |
        ForEach-Object { $_.SecurityIdentifier.Value })
}

function Get-MiosBoundaryOwnerAction {
    # The owner after recovery: SYSTEM and Administrators stay; only the validated operator is replaced; anyone else is refused.
    param([Parameter(Mandatory)][string]$Owner, [Parameter(Mandatory)][string]$OriginSid, [Parameter(Mandatory)][string]$Path)
    if ($Owner -in @('S-1-5-18', 'S-1-5-32-544')) { return $Owner }
    if ($Owner -eq $OriginSid) { return 'S-1-5-32-544' }
    throw "Boundary recovery refuses $Path owned by ${Owner}: only SYSTEM, Administrators or the validated operator can be recovered"
}

function Assert-MiosBoundaryAcePolicy {
    # Only plain allow ACEs are reviewed policy; a deny, object, callback or NULL DACL stops recovery unchanged.
    param([Parameter(Mandatory)][string]$Sddl, [Parameter(Mandatory)][string]$Path)
    $raw = New-Object Security.AccessControl.RawSecurityDescriptor $Sddl
    if ($null -eq $raw.DiscretionaryAcl) { throw "Boundary recovery refuses $Path with a NULL DACL" }
    foreach ($ace in @($raw.DiscretionaryAcl)) {
        if ($ace -isnot [Security.AccessControl.CommonAce] -or [string]$ace.AceQualifier -ne 'AccessAllowed' -or $ace.IsCallback) {
            throw "Boundary recovery refuses $Path with an unreviewed ACE ($($ace.AceType))"
        }
    }
}

function New-MiosSharedPrefixSddl {
    # Shared FHS prefix: trusted access on the directory itself only; every inheritable ACE it passes to children stays, as InheritOnly.
    param([Parameter(Mandatory)][string]$Sddl, [Parameter(Mandatory)][string]$Owner)
    $current = New-Object Security.AccessControl.RawSecurityDescriptor $Sddl
    $acl = New-Object Security.AccessControl.RawAcl -ArgumentList ([byte]2), 0
    foreach ($self in @(@('S-1-5-18', 0x1F01FF), @('S-1-5-32-544', 0x1F01FF), @('S-1-5-32-545', 0x1200A9))) {
        $sid = New-Object Security.Principal.SecurityIdentifier $self[0]
        $acl.InsertAce($acl.Count, (New-Object Security.AccessControl.CommonAce -ArgumentList ([Security.AccessControl.AceFlags]::None),
            ([Security.AccessControl.AceQualifier]::AccessAllowed), ([int]$self[1]), $sid, $false, $null))
    }
    foreach ($ace in @($current.DiscretionaryAcl)) {
        $flags = [int]$ace.AceFlags
        if (-not ($flags -band 0x03)) { continue }   # OBJECT_INHERIT | CONTAINER_INHERIT
        $template = $ace.Copy()
        $template.AceFlags = [Security.AccessControl.AceFlags](($flags -bor 0x08) -band (-bnot 0x10))   # + INHERIT_ONLY, - INHERITED
        $acl.InsertAce($acl.Count, $template)
    }
    $control = [Security.AccessControl.ControlFlags]'DiscretionaryAclPresent, DiscretionaryAclProtected, SelfRelative'
    $result = New-Object Security.AccessControl.RawSecurityDescriptor -ArgumentList $control, (New-Object Security.Principal.SecurityIdentifier $Owner), $null, $null, $acl
    return $result.GetSddlForm([Security.AccessControl.AccessControlSections]'Owner, Access')
}

function New-MiosLeafSddl {
    # MiOS-namespaced leaf: exactly the descriptor fresh creation uses (-Private: SYSTEM/Administrators only), with the decided owner.
    param([Parameter(Mandatory)][bool]$Directory, [Parameter(Mandatory)][string]$Owner, [switch]$Private)
    $raw = New-Object Security.AccessControl.RawSecurityDescriptor ((New-MiosMachineCreationAcl -Directory:$Directory -Private:$Private).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]'Owner, Access'))
    $raw.Owner = New-Object Security.Principal.SecurityIdentifier $Owner
    return $raw.GetSddlForm([Security.AccessControl.AccessControlSections]'Owner, Access')
}

function Open-MiosBoundaryAncestors {
    # Pins every ancestor against rename or delete for one repair; handles opened earlier by other processes are not revoked.
    param([Parameter(Mandatory)][string]$Path)
    $chain = New-Object System.Collections.Generic.List[string]
    $dir = Split-Path -Parent ([IO.Path]::GetFullPath($Path))
    while ($dir) {
        $chain.Insert(0, $dir)
        $up = Split-Path -Parent $dir
        if (-not $up -or $up -eq $dir) { break }
        $dir = $up
    }
    $pins = New-Object System.Collections.Generic.List[object]
    try {
        foreach ($p in $chain) {
            $handle = [MiosMachineBoundaryNative]::Pin($p)
            $info = [MiosMachineBoundaryNative]::Info($handle)
            $pins.Add([pscustomobject]@{ Path = $p; Handle = $handle; Volume = [string]$info.Volume; Index = ('{0:x16}' -f $info.Index) })
            if (-not ($info.Attributes -band 0x10) -or ($info.Attributes -band 0x400) -or
                [MiosMachineBoundaryNative]::FinalPath($handle).TrimEnd('\') -ine $p.TrimEnd('\')) { throw "Boundary recovery refuses an unstable ancestor: $p" }
        }
    } catch { foreach ($pin in $pins) { $pin.Handle.Dispose() }; throw }
    return $pins.ToArray()
}

function Update-MiosBoundaryInheritance {
    # Recompute one descendant's inherited ACEs from its repaired parent, by handle; its owner and explicit ACEs stay.
    param([Parameter(Mandatory)]$Object, [Parameter(Mandatory)][string]$Backup)
    $handle = [MiosMachineBoundaryNative]::Open([string]$Object.Path, $true)
    try {
        $now = [MiosMachineBoundaryNative]::Info($handle)
        if ([string]$now.Volume -ne [string]$Object.Volume -or ('{0:x16}' -f $now.Index) -ne [string]$Object.Index -or ($now.Attributes -band 0x400) -or
            (-not ($now.Attributes -band 0x10) -and $now.Links -ne 1) -or [MiosMachineBoundaryNative]::FinalPath($handle).TrimEnd('\') -ine [string]$Object.Path) {
            throw "Boundary recovery descendant changed before its inheritance update: $($Object.Path); backup $Backup"
        }
        $raw = New-Object Security.AccessControl.RawSecurityDescriptor ([MiosMachineBoundaryNative]::GetSddl($handle))
        if ([int]$raw.ControlFlags -band 0x1000) { return }   # SE_DACL_PROTECTED: inherits nothing
        $explicit = New-Object Security.AccessControl.RawAcl -ArgumentList ([byte]2), 0
        foreach ($ace in @($raw.DiscretionaryAcl)) { if (-not ([int]$ace.AceFlags -band 0x10)) { $explicit.InsertAce($explicit.Count, $ace.Copy()) } }
        $only = New-Object Security.AccessControl.RawSecurityDescriptor -ArgumentList ([Security.AccessControl.ControlFlags]'DiscretionaryAclPresent, SelfRelative'), $null, $null, $null, $explicit
        [MiosMachineBoundaryNative]::SetDacl($handle, $only.GetSddlForm([Security.AccessControl.AccessControlSections]::Access), $false)
    } finally { $handle.Dispose() }
}

function Get-MiosNamedStreams {
    # Alternate data streams of a file: every stream except the unnamed ::$DATA.
    param([Parameter(Mandatory)][string]$Path)
    return @([MiosMachineBoundaryNative]::Streams([IO.Path]::GetFullPath($Path)) | Where-Object { $_ -ne '::$DATA' })
}

function Get-MiosNewReaders {
    # Accounts outside SYSTEM/Administrators that $After lets read an object although $Before did not. An earlier
    # Authenticated Users read covers BUILTIN\Users (its members are authenticated); an earlier Everyone read covers anyone.
    param([Parameter(Mandatory)][string]$Before, [Parameter(Mandatory)][string]$After)
    $readers = {
        param([string]$Sddl)
        $raw = New-Object Security.AccessControl.RawSecurityDescriptor $Sddl
        if ($null -eq $raw.DiscretionaryAcl) { return @('S-1-1-0') }
        # FILE_READ_DATA / FILE_LIST_DIRECTORY, GENERIC_READ, GENERIC_ALL on the object itself (not InheritOnly).
        return @(@($raw.DiscretionaryAcl) | Where-Object { -not ([int]$_.AceFlags -band 0x08) -and [string]$_.AceQualifier -eq 'AccessAllowed' -and
            ($_.AccessMask -band (0x1 -bor 0x80000000 -bor 0x10000000)) } | ForEach-Object { $_.SecurityIdentifier.Value })
    }
    $old = @(& $readers $Before)
    if ($old -contains 'S-1-1-0') { return @() }
    return @(@(& $readers $After) | Where-Object { $_ -notin @('S-1-5-18', 'S-1-5-32-544') -and $_ -notin $old -and
        -not ($_ -eq 'S-1-5-32-545' -and $old -contains 'S-1-5-11') } | Sort-Object -Unique)
}

function Initialize-MiosBoundaryBackupDirectory {
    param([Parameter(Mandatory)][string]$Directory)
    foreach ($level in @((Split-Path -Parent $Directory), $Directory)) {
        if (Test-Path -LiteralPath $level) { Assert-MiosMachineWriteBoundary $level } else { New-MiosProtectedMachineDirectory $level }
    }
}

function Assert-MiosPrivateBackupLeaf {
    # A backup leaf is a plain, single-link file owned by and accessible only to SYSTEM and Administrators, under a protected DACL.
    param([Parameter(Mandatory)][string]$Path, [switch]$WithBytes)
    $record = Read-MiosBoundaryObject $Path -WithBytes:$WithBytes
    $raw = New-Object Security.AccessControl.RawSecurityDescriptor $record.Sddl
    if ($record.Kind -ne 'file' -or $record.Owner -notin @('S-1-5-18', 'S-1-5-32-544') -or $null -eq $raw.DiscretionaryAcl -or
        -not ([int]$raw.ControlFlags -band 0x1000) -or @(@($raw.DiscretionaryAcl) | Where-Object { $_.SecurityIdentifier.Value -notin @('S-1-5-18', 'S-1-5-32-544') }).Count) {
        throw "Not a private MiOS boundary backup: $Path"
    }
    return $record
}

function Save-MiosTomlBackup {
    # Exact original TOML bytes as one private, content-addressed leaf, before any TOML writer runs, even when every path passes
    # admission. An existing leaf is verified, never replaced. A TOML with alternate data streams is refused: they cannot be preserved.
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][ValidateSet('vendor', 'host')][string]$Role, [Parameter(Mandatory)][string]$Directory)
    $record = Read-MiosBoundaryObject $Path -WithBytes
    if ($record.Kind -ne 'file') { throw "Boundary recovery refuses a TOML path that is not a file: $Path" }
    $streams = @(Get-MiosNamedStreams $Path)
    if ($streams.Count) { throw "Boundary recovery refuses ${Path}: it has $($streams.Count) alternate data stream(s) that the TOML backup cannot preserve" }
    Initialize-MiosBoundaryBackupDirectory $Directory
    $leaf = Join-Path $Directory ('{0}-{1}.toml' -f $Role, $record.Sha256)
    if (Test-Path -LiteralPath $leaf) {
        if ((Assert-MiosPrivateBackupLeaf $leaf).Sha256 -ne $record.Sha256) { throw "A MiOS TOML backup does not hold the bytes its name records: $leaf" }
    } else { Write-MiosPrivateMachineFile -Path $leaf -Bytes ([Convert]::FromBase64String([string]$record.Bytes)) }
    $now = Read-MiosBoundaryObject $Path
    foreach ($field in 'Owner', 'Sddl', 'Volume', 'Index', 'Sha256', 'Size', 'Written') {
        if ($now.$field -cne $record.$field) { throw "Boundary recovery TOML changed while it was backed up: $Path" }
    }
    # Per-run descriptor provenance is independent of deduplicated content leaves: a compliant file can still be rewritten next.
    return [pscustomobject][ordered]@{
        Path = $record.Path; Role = $Role; Kind = $record.Kind; Owner = $record.Owner; Sddl = $record.Sddl
        Attributes = $record.Attributes; Links = $record.Links; Volume = $record.Volume; Index = $record.Index
        Created = $record.Created; Written = $record.Written; Sha256 = $record.Sha256; Size = $record.Size; Backup = $leaf
    }
}

function Save-MiosBoundaryBackup {
    # One new private file per recovery run (CreateNew, SYSTEM/Administrators only), never overwriting an earlier backup.
    param([Parameter(Mandatory)][object[]]$Objects, [object[]]$Tomls = @(), [Parameter(Mandatory)][string]$Reason, [string]$Directory)
    if (-not $Directory) { $Directory = Join-Path $env:ProgramData 'MiOS\boundary-recovery' }
    Initialize-MiosBoundaryBackupDirectory $Directory
    $file = Join-Path $Directory ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-' + [guid]::NewGuid().ToString('N') + '.json')
    $document = [ordered]@{ schema = 'mios-boundary-recovery/2'; created = (Get-Date).ToUniversalTime().ToString('o'); reason = $Reason
        objects = @($Objects); tomls = @($Tomls) }
    Write-MiosPrivateMachineFile -Path $file -Bytes ((New-Object Text.UTF8Encoding($false)).GetBytes(($document | ConvertTo-Json -Depth 4 -Compress)))
    return $file
}

function Invoke-MiosMachineBoundaryRecovery {
    # Explicit recovery after origin validation and before any TOML write. Production orchestration supplies only the system root
    # and an explicitly selected existing host-storage root; direct use defaults to SystemDrive. There is no drive discovery.
    # Both TOML files' bytes and original descriptors are preserved privately on every run, even when admission already passes.
    # Admission (Assert-MiosMachineWriteBoundary) is unchanged and decides afterwards; no descriptor changes when every path passes.
    param([Parameter(Mandatory)][string]$OriginSid, [string]$Root, [string]$BackupDirectory)
    if (-not $Root) { $Root = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' } }
    $Root = [IO.Path]::GetFullPath($Root.TrimEnd('\') + '\')
    if (-not $BackupDirectory) { $BackupDirectory = Join-Path $env:ProgramData 'MiOS\boundary-recovery' }
    if ($OriginSid -notmatch '^S-1-[0-9]+(-[0-9]+)+$') { throw 'Boundary recovery needs the validated origin SID.' }
    Initialize-MiosMachineBoundaryApi
    # Each known TOML file comes before its namespace directory, so it is protected before that directory's inheritance changes.
    $items = @(
        @{ Rel = 'usr'; Policy = 'shared' }, @{ Rel = 'usr\share'; Policy = 'shared' },
        @{ Rel = 'usr\share\mios\mios.toml'; Policy = 'vendor-toml'; Parent = 'usr\share\mios' }, @{ Rel = 'usr\share\mios'; Policy = 'leaf' },
        @{ Rel = 'etc'; Policy = 'shared' },
        @{ Rel = 'etc\mios\mios.toml'; Policy = 'host-toml'; Parent = 'etc\mios' }, @{ Rel = 'etc\mios'; Policy = 'leaf' })
    $gaps = @{}
    foreach ($item in $items) {
        $path = (Join-Path $Root $item.Rel).TrimEnd('\')
        if (-not (Test-Path -LiteralPath $path)) { continue }
        Assert-MiosNoReparsePath $path
        $gaps[$item.Rel] = Get-MiosMachineBoundaryGap $path
    }
    $plan = New-Object System.Collections.Generic.List[object]
    foreach ($item in $items) {
        if (-not $gaps.ContainsKey($item.Rel)) { continue }
        $path = (Join-Path $Root $item.Rel).TrimEnd('\')
        $gap = $gaps[$item.Rel]
        if (-not $gap -and $item.Parent -and $gaps[$item.Parent] -and
            -not ([int](New-Object Security.AccessControl.RawSecurityDescriptor (Read-MiosBoundaryObject $path).Sddl).ControlFlags -band 0x1000)) {
            $gap = 'it inherits from a namespace directory that is being recovered'
        }
        if ($gap) { $plan.Add([pscustomobject]@{ Path = $path; Policy = $item.Policy; Gap = $gap }) }
    }
    $tomlItems = @(@(@('usr\share\mios\mios.toml', 'vendor'), @('etc\mios\mios.toml', 'host')) | Where-Object { Test-Path -LiteralPath (Join-Path $Root $_[0]) -PathType Leaf })
    if (-not $plan.Count -and -not $tomlItems.Count) { return $null }
    $why = if ($plan.Count) { "$($plan[0].Path): $($plan[0].Gap)" } else { 'TOML backup' }
    if (-not (Test-MiosElevated)) { throw "Boundary recovery requires an elevated token ($why)" }
    # Plan and validate everything before anything is written.
    $planned = @($plan | ForEach-Object { $_.Path })
    $objects = New-Object System.Collections.Generic.List[object]
    foreach ($step in $plan) {
        $record = Read-MiosBoundaryObject $step.Path
        Assert-MiosBoundaryAcePolicy -Sddl $record.Sddl -Path $step.Path
        $owner = Get-MiosBoundaryOwnerAction -Owner $record.Owner -OriginSid $OriginSid -Path $step.Path
        $isDirectory = $record.Kind -eq 'directory'
        $target = switch ($step.Policy) {
            'shared' { New-MiosSharedPrefixSddl -Sddl $record.Sddl -Owner $owner }
            'host-toml' { New-MiosLeafSddl -Directory $false -Owner $owner -Private }
            default { New-MiosLeafSddl -Directory $isDirectory -Owner $owner }
        }
        foreach ($sid in @(Get-MiosNewReaders -Before $record.Sddl -After $target)) { throw "Boundary recovery refuses $($step.Path): its recovered descriptor would let $sid read it" }
        $children = @(); $siblings = @()
        if ($step.Policy -eq 'leaf' -and $isDirectory) {
            # A planned target below a leaf (a known TOML file) is its own step, so it is not also a descendant to keep unchanged.
            $children = @(Get-MiosBoundaryDescendants $step.Path | Where-Object { $_ -notin $planned } | ForEach-Object { Read-MiosBoundaryObject $_ })
            # Re-inheriting from the hardened leaf must not let anyone read a descendant that cannot read it now.
            $byPath = @{}; foreach ($c in $children) { $byPath[$c.Path] = $c }
            $leafAces = @((New-Object Security.AccessControl.RawSecurityDescriptor $target).DiscretionaryAcl)
            foreach ($c in $children) {
                $raw = New-Object Security.AccessControl.RawSecurityDescriptor $c.Sddl
                $inherits = -not ([int]$raw.ControlFlags -band 0x1000)
                for ($p = Split-Path -Parent $c.Path; $inherits -and $p -and $p -ine $step.Path; $p = Split-Path -Parent $p) {
                    if ($byPath.ContainsKey($p) -and ([int](New-Object Security.AccessControl.RawSecurityDescriptor $byPath[$p].Sddl).ControlFlags -band 0x1000)) { $inherits = $false }
                }
                if (-not $inherits) { continue }
                $future = New-Object Security.AccessControl.RawAcl -ArgumentList ([byte]2), 0
                foreach ($ace in @($raw.DiscretionaryAcl)) { if (-not ([int]$ace.AceFlags -band 0x10)) { $future.InsertAce($future.Count, $ace.Copy()) } }
                $applies = if ($c.Kind -eq 'directory') { 0x02 } else { 0x01 }   # CONTAINER_INHERIT reaches directories, OBJECT_INHERIT files
                foreach ($ace in $leafAces) {
                    if ([int]$ace.AceFlags -band $applies) { $copy = $ace.Copy(); $copy.AceFlags = [Security.AccessControl.AceFlags]::Inherited; $future.InsertAce($future.Count, $copy) }
                }
                $after = (New-Object Security.AccessControl.RawSecurityDescriptor -ArgumentList ([Security.AccessControl.ControlFlags]'DiscretionaryAclPresent, SelfRelative'),
                    $null, $null, $null, $future).GetSddlForm([Security.AccessControl.AccessControlSections]::Access)
                foreach ($sid in @(Get-MiosNewReaders -Before $c.Sddl -After $after)) { throw "Boundary recovery refuses $($c.Path): re-inheriting from the recovered $($step.Path) would let $sid read it" }
            }
        }
        if ($step.Policy -eq 'shared') {
            $siblings = @([IO.Directory]::EnumerateFileSystemEntries($step.Path) | Where-Object { $_ -notin $planned } | ForEach-Object { Read-MiosBoundaryObject $_ -Lenient })
        }
        $chain = @(Open-MiosBoundaryAncestors $step.Path)
        try { $chainIds = @($chain | ForEach-Object { "$($_.Path)|$($_.Volume)|$($_.Index)" }) } finally { foreach ($pin in $chain) { $pin.Handle.Dispose() } }
        $step | Add-Member -NotePropertyName Owner -NotePropertyValue $owner
        $step | Add-Member -NotePropertyName Record -NotePropertyValue $record
        $step | Add-Member -NotePropertyName Target -NotePropertyValue $target
        $step | Add-Member -NotePropertyName Children -NotePropertyValue $children
        $step | Add-Member -NotePropertyName Siblings -NotePropertyValue $siblings
        $step | Add-Member -NotePropertyName Chain -NotePropertyValue $chainIds
        foreach ($o in @($record) + $children) { if (-not @($objects | Where-Object { $_.Path -eq $o.Path }).Count) { $objects.Add($o) } }
    }
    $tomls = @(foreach ($t in $tomlItems) { Save-MiosTomlBackup -Path (Join-Path $Root $t[0]) -Role $t[1] -Directory $BackupDirectory })
    foreach ($toml in $tomls) {
        $existing = @($objects | Where-Object { $_.Path -eq $toml.Path })
        if ($existing.Count) {
            foreach ($field in 'Owner', 'Sddl', 'Volume', 'Index', 'Sha256') {
                if ($existing[0].$field -cne $toml.$field) { throw "Boundary recovery TOML changed between planning and its backup: $($toml.Path)" }
            }
        } else { $objects.Add($toml) }
    }
    $reason = if ($plan.Count) { ($plan | ForEach-Object { "$($_.Path) [$($_.Policy)]: $($_.Gap)" }) -join '; ' } else { 'Preserve original TOMLs before publication; no ACL recovery required' }
    $backup = Save-MiosBoundaryBackup -Objects $objects.ToArray() -Tomls $tomls -Directory $BackupDirectory `
        -Reason $reason
    foreach ($step in $plan) {
        $pins = @(Open-MiosBoundaryAncestors $step.Path)
        try {
            if ((@($pins | ForEach-Object { "$($_.Path)|$($_.Volume)|$($_.Index)" }) -join ';') -cne ($step.Chain -join ';')) {
                throw "Boundary recovery ancestor chain changed after planning: $($step.Path); backup $backup"
            }
            $handle = [MiosMachineBoundaryNative]::Open($step.Path, $true)
            try {
                $now = [MiosMachineBoundaryNative]::Info($handle)
                $sddlNow = [MiosMachineBoundaryNative]::GetSddl($handle)
                if ([string]$now.Volume -ne $step.Record.Volume -or ('{0:x16}' -f $now.Index) -ne $step.Record.Index -or ($now.Attributes -band 0x400) -or
                    (-not ($now.Attributes -band 0x10) -and $now.Links -ne 1) -or [MiosMachineBoundaryNative]::FinalPath($handle).TrimEnd('\') -ine $step.Path -or
                    (New-Object Security.AccessControl.RawSecurityDescriptor $sddlNow).Owner.Value -ne $step.Record.Owner -or
                    -not (Test-MiosSameExplicitAces -Before $step.Record.Sddl -After $sddlNow)) { throw "Boundary recovery target changed after its backup: $($step.Path); backup $backup" }
                $target = if ($step.Policy -eq 'shared') { New-MiosSharedPrefixSddl -Sddl $sddlNow -Owner $step.Owner } else { $step.Target }
                # An elevated administrator may assign as owner only itself or Administrators; assigning SYSTEM needs SeRestorePrivilege,
                # which recovery never enables. A kept owner (SYSTEM on C:\etc) is therefore not rewritten: only the protected DACL changes.
                # A replaced owner is always Administrators (Get-MiosBoundaryOwnerAction).
                if ($step.Owner -eq $step.Record.Owner) { [MiosMachineBoundaryNative]::SetDacl($handle, $target, $true) }
                else { [MiosMachineBoundaryNative]::SetSddl($handle, $target) }
            } finally { $handle.Dispose() }
            foreach ($child in $step.Children) { Update-MiosBoundaryInheritance -Object $child -Backup $backup }
            foreach ($pin in $pins) {
                $info = [MiosMachineBoundaryNative]::Info($pin.Handle)
                if ([string]$info.Volume -ne $pin.Volume -or ('{0:x16}' -f $info.Index) -ne $pin.Index) { throw "Boundary recovery ancestor changed: $($pin.Path); backup $backup" }
            }
        } finally { foreach ($pin in $pins) { $pin.Handle.Dispose() } }
    }
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($step in $plan) {
        $after = Read-MiosBoundaryObject $step.Path
        foreach ($field in 'Volume', 'Index', 'Created', 'Written', 'Size', 'Sha256') { if ($after.$field -ne $step.Record.$field) { $problems.Add("$($step.Path) $field changed") } }
        $gap = Get-MiosMachineBoundaryGap $step.Path
        if ($gap) { $problems.Add($gap) }
        foreach ($old in $step.Children) {
            $new = Read-MiosBoundaryObject $old.Path
            foreach ($field in 'Volume', 'Index', 'Created', 'Written', 'Size', 'Sha256', 'Owner') { if ($new.$field -ne $old.$field) { $problems.Add("$($old.Path) $field changed") } }
            if (-not (Test-MiosSameExplicitAces -Before $old.Sddl -After $new.Sddl)) { $problems.Add("$($old.Path) explicit ACEs changed") }
            foreach ($sid in (Get-MiosInheritedWriteGrants $new.Sddl)) { $problems.Add("$($old.Path) still inherits write access for $sid") }
            foreach ($sid in @(Get-MiosNewReaders -Before $old.Sddl -After $new.Sddl)) { $problems.Add("$($old.Path) became readable by $sid") }
        }
        foreach ($old in $step.Siblings) {
            $new = Read-MiosBoundaryObject $old.Path -Lenient
            foreach ($field in 'Sddl', 'Owner', 'Volume', 'Index', 'Written', 'Size', 'Sha256') { if ($new.$field -cne $old.$field) { $problems.Add("sibling $($old.Path) $field changed") } }
        }
    }
    if ($problems.Count) { throw "Boundary recovery could not be verified ($($problems -join '; ')); original descriptors are in $backup and TOML bytes in $BackupDirectory" }
    if ($plan.Count) { Write-Host "  [+] Recovered the MiOS machine boundary ($($plan.Count) path(s)); originals: $backup" -ForegroundColor DarkGray }
    else { Write-Host "  [+] Preserved the original MiOS TOMLs before publication; originals: $backup" -ForegroundColor DarkGray }
    return $backup
}

function Restore-MiosMachineBoundary {
    # Rollback of one recovery run: re-applies its recorded descriptors by handle, top-down as recorded, only after the backup leaf
    # and every object validate. It refuses rather than put an old descriptor on content that changed since (a rewritten or scrubbed
    # TOML). TOML bytes stay in their private backup leaves for manual use.
    param([Parameter(Mandatory)][string]$BackupPath, [string]$BackupDirectory)
    if (-not (Test-MiosElevated)) { throw 'Boundary restore requires an elevated token.' }
    Initialize-MiosMachineBoundaryApi
    if (-not $BackupDirectory) { $BackupDirectory = Join-Path $env:ProgramData 'MiOS\boundary-recovery' }
    $full = [IO.Path]::GetFullPath($BackupPath)
    if ((Split-Path -Parent $full) -ine [IO.Path]::GetFullPath($BackupDirectory).TrimEnd('\') -or $full -notmatch '\.json$') {
        throw "Boundary restore accepts only a recovery backup in ${BackupDirectory}: $BackupPath"
    }
    Assert-MiosMachineWriteBoundary $BackupDirectory
    $leaf = Assert-MiosPrivateBackupLeaf $full -WithBytes
    $document = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$leaf.Bytes)) | ConvertFrom-Json
    if ($document.schema -cne 'mios-boundary-recovery/2') { throw "Not a MiOS boundary backup: $BackupPath" }
    $objects = @($document.objects)
    foreach ($object in $objects) {
        $now = Read-MiosBoundaryObject ([string]$object.Path)
        if ($now.Volume -ne [string]$object.Volume -or $now.Index -ne [string]$object.Index -or $now.Kind -ne [string]$object.Kind) {
            throw "Boundary restore target is not the recorded object: $($object.Path)"
        }
        if ($now.Kind -eq 'file' -and $now.Sha256 -ne [string]$object.Sha256) {
            throw "Boundary restore refuses $($object.Path): its content changed since the backup, so its old descriptor is not re-applied"
        }
    }
    [MiosMachineBoundaryNative]::EnablePrivilege('SeRestorePrivilege')
    # Retain every ancestor and target before restoring any parent ACL. Target handles have data access and share read only:
    # no writer may enter between the content check and SetSddl, including while old writable parent descriptors are restored.
    $pins = New-Object System.Collections.Generic.List[object]
    $targets = New-Object System.Collections.Generic.List[object]
    try {
        foreach ($object in $objects) {
            foreach ($pin in @(Open-MiosBoundaryAncestors ([string]$object.Path))) { $pins.Add($pin) }
            $handle = [MiosMachineBoundaryNative]::Open([string]$object.Path, $true)
            $targets.Add([pscustomobject]@{ Object = $object; Handle = $handle })
            $now = [MiosMachineBoundaryNative]::Info($handle)
            $kind = if ($now.Attributes -band 0x10) { 'directory' } else { 'file' }
            if ([string]$now.Volume -ne [string]$object.Volume -or ('{0:x16}' -f $now.Index) -ne [string]$object.Index -or
                $kind -cne [string]$object.Kind -or ($now.Attributes -band 0x400) -or ($kind -eq 'file' -and $now.Links -ne 1) -or
                [MiosMachineBoundaryNative]::FinalPath($handle).TrimEnd('\') -ine [string]$object.Path) { throw "Boundary restore target is not the recorded object: $($object.Path)" }
            if ($kind -eq 'file' -and [MiosMachineBoundaryNative]::Sha256($handle) -cne [string]$object.Sha256) {
                throw "Boundary restore refuses $($object.Path): its content changed since the backup, so its old descriptor is not re-applied"
            }
        }
        foreach ($pin in $pins) {
            $now = [MiosMachineBoundaryNative]::Info($pin.Handle)
            if ([string]$now.Volume -ne $pin.Volume -or ('{0:x16}' -f $now.Index) -ne $pin.Index -or
                -not ($now.Attributes -band 0x10) -or ($now.Attributes -band 0x400) -or
                [MiosMachineBoundaryNative]::FinalPath($pin.Handle).TrimEnd('\') -ine $pin.Path.TrimEnd('\')) { throw "Boundary restore ancestor changed: $($pin.Path)" }
        }
        # Parents before children (stable by depth); every handle and its ancestor pins stay held until all writes finish.
        $ordered = @(for ($i = 0; $i -lt $targets.Count; $i++) {
            [pscustomobject]@{ Depth = ([string]$targets[$i].Object.Path).Split('\').Count; Order = $i; Target = $targets[$i] }
        }) | Sort-Object Depth, Order | ForEach-Object { $_.Target }
        foreach ($target in $ordered) {
            $object = $target.Object; $handle = $target.Handle
            $now = [MiosMachineBoundaryNative]::Info($handle)
            if ([string]$now.Volume -ne [string]$object.Volume -or ('{0:x16}' -f $now.Index) -ne [string]$object.Index -or
                ($now.Attributes -band 0x400) -or ($object.Kind -eq 'file' -and $now.Links -ne 1) -or
                [MiosMachineBoundaryNative]::FinalPath($handle).TrimEnd('\') -ine [string]$object.Path) { throw "Boundary restore target changed before apply: $($object.Path)" }
            if ($object.Kind -eq 'file' -and [MiosMachineBoundaryNative]::Sha256($handle) -cne [string]$object.Sha256) {
                throw "Boundary restore refuses $($object.Path): its content changed since the backup, so its old descriptor is not re-applied"
            }
            [MiosMachineBoundaryNative]::SetSddl($handle, [string]$object.Sddl)
        }
    } finally {
        foreach ($target in $targets) { $target.Handle.Dispose() }
        foreach ($pin in $pins) { $pin.Handle.Dispose() }
    }
}

function Initialize-MiosVendorTierDirectory {
    param([Parameter(Mandatory)][string]$Path)
    $mios = Split-Path -Parent ([IO.Path]::GetFullPath($Path)); $share = Split-Path -Parent $mios; $usr = Split-Path -Parent $share
    if ((Split-Path -Leaf $Path) -ine 'mios.toml' -or (Split-Path -Leaf $mios) -ine 'mios' -or
        (Split-Path -Leaf $share) -ine 'share' -or (Split-Path -Leaf $usr) -ine 'usr') { throw 'A vendor TOML destination must be usr/share/mios/mios.toml.' }
    Assert-MiosNoReparsePath $Path
    foreach ($directory in @($usr, $share, $mios)) {
        if (Test-Path -LiteralPath $directory) { Assert-MiosMachineWriteBoundary $directory }
        else {
            New-MiosProtectedMachineDirectory $directory
        }
    }
    if (Test-Path -LiteralPath $Path) { Assert-MiosMachineWriteBoundary $Path }
}

function Write-MiosVendorToml {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Text)
    Initialize-MiosVendorTierDirectory $Path
    $pending = Join-Path (Split-Path -Parent $Path) ('.vendor-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Write-MiosPrivateMachineFile -Path $pending -Bytes ((New-Object Text.UTF8Encoding($false)).GetBytes($Text))
        Assert-MiosNoReparsePath $Path
        if (Test-Path -LiteralPath $Path) {
            Assert-MiosMachineWriteBoundary $Path
            [IO.File]::Replace($pending, $Path, [Management.Automation.Language.NullString]::Value)
        } else { [IO.File]::Move($pending, $Path) }
        Set-MiosHostTierAcl $Path
    } finally { if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Force -ErrorAction Stop } }
}

function Repair-MiosHostTierCredentials {
    param([string[]]$Paths = @())
    foreach ($p in $Paths) {
        if (-not (Test-Path -LiteralPath $p -PathType Leaf -ErrorAction SilentlyContinue)) { continue }
        Set-MiosHostTierAcl -Path $p -Private
        Initialize-MiosHostTierDirectory $p
        Set-MiosHostTierAcl -Path $p -Private
        # Always rewrite, including files whose credential alias/syntax older scanners missed.
        $raw = [IO.File]::ReadAllText($p)
        Write-MiosHostToml -Path $p -Text $raw
        $p
    }
}

function Set-MiosPrivateUserTomlAcl {
    param([Parameter(Mandatory)][string]$Path)
    Assert-MiosNoReparsePath $Path
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($owner, (New-Object Security.Principal.SecurityIdentifier 'S-1-5-18'), (New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'))) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')))
    }
    $acl.SetOwner($owner)
    # A fresh FileSecurity modifies owner/access only; do not ask Set-Acl to persist an audit section.
    $info = New-Object IO.FileInfo $Path
    try {
        if ($PSVersionTable.PSEdition -eq 'Desktop') { $info.SetAccessControl($acl) }
        else { [IO.FileSystemAclExtensions]::SetAccessControl($info, $acl) }
    } catch { throw }
}

function Import-MiosUserToml {
    param([Parameter(Mandatory)][string]$SourcePath,
          [string]$DestinationPath = (Join-Path $env:USERPROFILE '.config\mios\mios.toml'))
    Assert-MiosNoReparsePath $SourcePath
    Assert-MiosNoReparsePath $DestinationPath
    $dir = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null }
    if (Test-Path -LiteralPath $DestinationPath) { Set-MiosPrivateUserTomlAcl $DestinationPath }
    $pending = Join-Path $dir ('.mios-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        New-Item -ItemType File -Path $pending -ErrorAction Stop | Out-Null
        Set-MiosPrivateUserTomlAcl $pending
        [IO.File]::WriteAllBytes($pending, [IO.File]::ReadAllBytes($SourcePath))
        if (Test-Path -LiteralPath $DestinationPath) { [IO.File]::Replace($pending, $DestinationPath, [System.Management.Automation.Language.NullString]::Value) } else { [IO.File]::Move($pending, $DestinationPath) }
        Set-MiosPrivateUserTomlAcl $DestinationPath
        return $DestinationPath
    } finally {
        if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Force -ErrorAction Stop }
    }
}

function ConvertTo-MiosNativeArgument {
    param([AllowEmptyString()][string]$Value)
    # CommandLineToArgvW quoting for Windows PowerShell 5.1 / .NET Framework (no ArgumentList property).
    $out = New-Object Text.StringBuilder
    $null = $out.Append('"'); $slashes = 0
    foreach ($c in $Value.ToCharArray()) {
        if ($c -eq '\') { $slashes++; continue }
        if ($c -eq '"') { $null = $out.Append(('\' * (2 * $slashes + 1))); $null = $out.Append('"') }
        else { $null = $out.Append(('\' * $slashes)); $null = $out.Append($c) }
        $slashes = 0
    }
    $null = $out.Append(('\' * (2 * $slashes))); $null = $out.Append('"')
    return $out.ToString()
}

# The MiOS login: the vendor/host [identity].username through MiOS's identity guard (the text mios.git's
# usr/share/mios/windows/*.ps1 carries; build-mios.ps1 holds the installer's copy). The profile verbs resolve it at
# run time, once per distro per session. The guard prints its refusal on stderr and the verb then stops: never a
# fallback name. Single-quoted Python, and no 2>$null (Windows PowerShell turns wsl.exe's stderr into errors).
function Get-MiosLinuxLogin {
    param([Parameter(Mandatory)][string]$Distribution)
    if (-not $Global:MiosLinuxLogins) { $Global:MiosLinuxLogins = @{} }
    if ($Global:MiosLinuxLogins.ContainsKey($Distribution)) { return $Global:MiosLinuxLogins[$Distribution] }
    $identityProbe = 'import sys,pwd; sys.path.insert(0,''/usr/lib/mios''); import mios_toml as m; n=str((m.base_view().get(''identity'') or {}).get(''username'') or '''').strip(); p={e.pw_name: e for e in pwd.getpwall()}.get(n); print(n) if p and p.pw_uid != 0 and p.pw_uid >= m._uid_min() and not p.pw_shell.endswith((''nologin'', ''false'')) else sys.exit(''MiOS identity: the vendor/host [identity].username %r is not a login account here (missing, root, a system uid below UID_MIN, or no login shell)'' % n)'
    $login = ((& wsl.exe -d $Distribution -u root --exec python3 -c $identityProbe) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $login -or $login -eq 'root') { throw "The MiOS login (vendor/host [identity].username) is not a usable account in $Distribution; set [identity].username and re-run the installer" }
    if ($login -cnotmatch '^[a-z_][a-z0-9_-]*$') { throw "The MiOS login in $Distribution is not a plain login name; set [identity].username and re-run the installer" }
    $Global:MiosLinuxLogins[$Distribution] = $login
    return $login
}
function Sync-MiosUserTomlToBuilder {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Distro, [Parameter(Mandatory)][string]$User)
    if ($Distro -notmatch '^[A-Za-z0-9_.-]+$' -or $User -notmatch '^[A-Za-z_][A-Za-z0-9_-]*$') { throw 'Invalid builder distribution or user name.' }
    Set-MiosPrivateUserTomlAcl $Path
    function Get-MiosUserTomlReceiverScript {
        param([Parameter(Mandatory)][long]$ExpectedBytes)
        if ($ExpectedBytes -lt 0) { throw 'Invalid private TOML byte count.' }
        # A closed client pipe can look like successful EOF. Verify the nonsecret byte count before publishing.
        $program = 'set -eu; umask 077; test ! -L "$HOME/.config"; test ! -L "$HOME/.config/mios"; mkdir -p "$HOME/.config/mios"; chmod 700 "$HOME/.config/mios"; pending=$(mktemp "$HOME/.config/mios/.mios.XXXXXXXX"); trap ''rm -f -- "$pending"'' EXIT HUP INT TERM; cat > "$pending"; test "$(wc -c < "$pending")" -eq __MIOS_EXPECTED_BYTES__; chmod 600 "$pending"; mv -f -- "$pending" "$HOME/.config/mios/mios.toml"'
        return $program.Replace('__MIOS_EXPECTED_BYTES__', $ExpectedBytes.ToString([Globalization.CultureInfo]::InvariantCulture))
    }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $put = Get-MiosUserTomlReceiverScript -ExpectedBytes $bytes.LongLength
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = 'wsl.exe'
    $psi.Arguments = (@('-d', $Distro, '--user', $User, '--exec', 'sh', '-c', $put) | ForEach-Object { ConvertTo-MiosNativeArgument $_ }) -join ' '
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $proc = New-Object Diagnostics.Process
    $proc.StartInfo = $psi
    $savedInputEncoding = $null; $changedInputEncoding = $false
    try {
        # .NET Framework's redirected StreamWriter can flush Console.InputEncoding's BOM on Close,
        # even when all payload bytes went through BaseStream. Bind a BOM-less writer before Start.
        if ($PSVersionTable.PSEdition -eq 'Desktop') {
            $savedInputEncoding = [Console]::InputEncoding; $changedInputEncoding = $true
            [Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
        } elseif ($psi.PSObject.Properties['StandardInputEncoding']) {
            $psi.StandardInputEncoding = New-Object Text.UTF8Encoding($false)
        }
        if (-not $proc.Start()) { throw 'Could not start the private builder handoff.' }
        $stdout = $proc.StandardOutput.ReadToEndAsync(); $stderr = $proc.StandardError.ReadToEndAsync()
        $write = $proc.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
        if (-not $write.Wait(60000)) { throw 'Private builder handoff stdin timed out; the saved file was retained.' }
        $null = $write.GetAwaiter().GetResult()
        $proc.StandardInput.Close()
        if (-not $proc.WaitForExit(60000)) { $proc.Kill(); throw 'Private builder handoff timed out.' }
        if ($proc.ExitCode -ne 0) { throw "Private builder handoff failed (exit $($proc.ExitCode)); the saved file was not archived." }
        $null = $stdout.GetAwaiter().GetResult(); $null = $stderr.GetAwaiter().GetResult()
    } finally {
        # An input error/timeout can happen before WaitForExit. Do not leave a blocked writer child behind.
        try { if (-not $proc.HasExited) { $proc.Kill() } } catch {}
        try { $proc.Dispose() }
        finally { if ($changedInputEncoding) { [Console]::InputEncoding = $savedInputEncoding } }
    }
}

function Update-MiosWindowsBuildCheckout {
    param([Parameter(Mandatory = $true)][string]$Path,
          [Parameter(Mandatory = $true)][string]$Ref)
    # Keep local operator work and commits. Only a clean checkout can advance.
    # Native stderr is not a terminating PowerShell error on Windows PowerShell 5.1.
    $ErrorActionPreference = 'Continue'
    $changes = @(& git -C $Path status --porcelain=v1 --untracked-files=normal 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the Windows MiOS checkout; no source was changed.' }
    if (@($changes | Where-Object { $_ }).Count) {
        Write-Host '  [pull] Windows checkout has local changes; preserving them and building the current tree.' -ForegroundColor Yellow
        return
    }
    & git check-ref-format --branch $Ref *> $null
    if ($LASTEXITCODE -ne 0) { throw 'The configured MiOS source ref is invalid; no source was changed.' }
    & git -C $Path fetch origin $Ref *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  [pull] Windows source fetch failed; preserving and building the current tree. Retry when the remote is available.' -ForegroundColor Yellow
        return
    }
    # A local editor may have saved while fetch was running.
    $changes = @(& git -C $Path status --porcelain=v1 --untracked-files=normal 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the Windows MiOS checkout after fetch; no local source was changed.' }
    if (@($changes | Where-Object { $_ }).Count) {
        Write-Host '  [pull] Windows checkout changed during fetch; preserving it and building the current tree.' -ForegroundColor Yellow
        return
    }
    & git -C $Path merge-base --is-ancestor HEAD FETCH_HEAD *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  [pull] Windows checkout has local commits that differ from the remote; preserving and building the current tree. Reconcile the branches before updating.' -ForegroundColor Yellow
        return
    }
    & git -C $Path merge --ff-only --no-edit --no-autostash --no-overwrite-ignore FETCH_HEAD *> $null
    if ($LASTEXITCODE -ne 0) { throw 'The Windows MiOS checkout could not fast-forward safely; local work was preserved. Reconcile it before retrying.' }
    Write-Host '  [pull] Windows checkout is current after a clean fast-forward.' -ForegroundColor Green
}

function Get-MiosLosslessPullShell {
    # One shell implementation for generated native pull companions; no installer side effects.
    return @'
set -eu
checkout=${1:?A checkout path is required}
source_ref=${2:?A source ref is required}
case "${3:-}" in
    '') git_command() { git "$@"; } ;;
    --sudo) git_command() { sudo -n git "$@"; } ;;
    *) echo '[mios-pull] Invalid checkout execution mode; no source was changed.' >&2; exit 1 ;;
esac
if [ ! -d "$checkout/.git" ] && [ ! -f "$checkout/.git" ]; then
    echo '[mios-pull] Checkout is not initialized; preserving the staged tree for the build.'
    exit 0
fi
if ! changes=$(git_command -C "$checkout" status --porcelain=v1 --untracked-files=normal 2>/dev/null); then
    echo '[mios-pull] Cannot inspect the checkout; no source was changed.' >&2
    exit 1
fi
if [ -n "$changes" ]; then
    echo '[mios-pull] Local changes preserved; using the current tree. Commit or reconcile them before updating.'
    exit 0
fi
if ! git_command check-ref-format --branch "$source_ref" >/dev/null 2>&1; then
    echo '[mios-pull] The configured source ref is invalid; no source was changed.' >&2
    exit 1
fi
if ! git_command -C "$checkout" fetch origin "$source_ref" >/dev/null 2>&1; then
    echo '[mios-pull] Fetch failed; the current tree was preserved. Retry when the remote is available.'
    exit 0
fi
if ! changes=$(git_command -C "$checkout" status --porcelain=v1 --untracked-files=normal 2>/dev/null); then
    echo '[mios-pull] Cannot inspect the checkout after fetch; local source was preserved.' >&2
    exit 1
fi
if [ -n "$changes" ]; then
    echo '[mios-pull] The checkout changed during fetch; preserving the current tree.'
    exit 0
fi
if ! git_command -C "$checkout" merge-base --is-ancestor HEAD FETCH_HEAD >/dev/null 2>&1; then
    echo '[mios-pull] Local commits differ from the remote; preserving the current tree. Reconcile the branches before updating.'
    exit 0
fi
if ! git_command -C "$checkout" merge --ff-only --no-edit --no-autostash --no-overwrite-ignore FETCH_HEAD >/dev/null 2>&1; then
    echo '[mios-pull] A safe fast-forward was refused; local work was preserved. Reconcile it before retrying.' >&2
    exit 1
fi
echo '[mios-pull] Checkout is current after a clean fast-forward.'
'@
}

function Get-MiosWslConfigText {
    param([AllowEmptyString()][string]$Existing, [string[]]$ManagedLines, [Parameter(Mandatory)][bool]$Firewall,
        [bool]$HostAddressLoopback = $true)
    if (-not $Firewall) { throw 'mios.toml [wsl2].firewall=false disables the required inbound security policy; enable it before continuing.' }
    $managed = New-Object System.Collections.Generic.List[string]
    foreach ($line in $ManagedLines) {
        if ($line -match '^firewall=') { continue }
        if ($line -notmatch '^(memory|networkingMode|localhostForwarding|guiApplications)=[^\r\n\x00]+$') {
            throw 'Invalid managed WSL configuration line.'
        }
        $managed.Add($line)
    }
    # Filtering is mandatory: firewall=false bypasses the Hyper-V default/rule policy altogether.
    $managed.Add('firewall=' + $Firewall.ToString().ToLowerInvariant())
    $out = New-Object System.Collections.Generic.List[string]
    $inside = $false; $seen = $false
    foreach ($line in ($Existing -split "`r?`n")) {
        if ($line -match '^\s*\[\s*wsl2\s*\]\s*(?:[#;].*)?$') {
            if ($seen) { throw 'Duplicate WSL2 configuration sections; refusing an ambiguous firewall policy.' }
            $seen = $true; $inside = $true; $out.Add($line)
            foreach ($entry in $managed) { $out.Add($entry) }
            continue
        }
        if ($line -match '^\s*\[') { $inside = $false }
        if ($inside -and $line -match '^\s*(memory|networkingMode|localhostForwarding|firewall|guiApplications)\s*=') { continue }
        $out.Add($line)
    }
    if (-not $seen) {
        $out.Add('[wsl2]')
        foreach ($entry in $managed) { $out.Add($entry) }
    }
    # [experimental] hostAddressLoopback follows the managed networkingMode: written when mirrored, removed under NAT.
    $mode = if (@($managed | Where-Object { $_ -ieq 'networkingMode=mirrored' }).Count) { 'mirrored' } else { 'NAT' }
    $lines = Get-MiosWslExperimentalLines -Lines $out.ToArray() -NetworkingMode $mode -HostAddressLoopback $HostAddressLoopback
    return ($lines -join "`r`n")
}

function Get-MiosTomlTableArray {
    # [[Name]] array-of-tables from the layered TOML text as hashtables (single-line scalar values only), first
    # layer that declares any wins. Used for SSOT lists that must carry a reason per entry.
    param([Parameter(Mandatory)][string]$Name)
    foreach ($txt in @((Resolve-MiosTomlText), (Resolve-MiosVendorTomlText))) {
        if (-not $txt) { continue }
        $rx = '(?ms)^\[\[' + [regex]::Escape($Name) + '\]\][ \t]*\r?\n(?<body>.*?)(?=^\[|\z)'
        $ms = [regex]::Matches($txt, $rx)
        if (-not $ms.Count) { continue }
        $rows = @()
        foreach ($m in $ms) {
            $row = @{}
            foreach ($line in ($m.Groups['body'].Value -split "`r?`n")) {
                $kv = Split-MiosTomlKeyLine $line
                if (-not $kv) { continue }
                $raw = $kv[1].Trim()
                if ($raw.StartsWith('"')) {
                    $value = [regex]::Match($raw, '^"(?<v>(?:[^"\\]|\\.)*)"\s*(?:#.*)?$')
                    if (-not $value.Success) { throw 'Unsupported SSOT rule string.' }
                    $v = ConvertFrom-Json -InputObject ('"' + $value.Groups['v'].Value + '"') -ErrorAction Stop
                } elseif ($raw.StartsWith("'")) {
                    $value = [regex]::Match($raw, "^'(?<v>[^']*)'\s*(?:#.*)?$")
                    if (-not $value.Success) { throw 'Unsupported SSOT rule string.' }
                    $v = $value.Groups['v'].Value
                } elseif ($raw -match '^[0-9]+\s*(?:#.*)?$') { $v = ($raw -replace '\s*#.*$', '').Trim() }
                else { throw 'SSOT rule fields must be single-line strings or port numbers.' }
                $row[$kv[0]] = $v
            }
            $rows += , $row
        }
        return $rows   # one hashtable per entry; callers wrap in @()
    }
    return @()
}

function Get-MiosTomlValue {
    param(
        [Parameter(Mandatory)] [string]$Section,   # e.g. "terminal" or "bootstrap.host_storage"
        [Parameter(Mandatory)] [string]$Key,       # e.g. "cols"
        [Parameter(Mandatory)] [AllowEmptyString()] $Default, # returned if not found / unparseable
        # System SSOT only: values an elevated step trusts (download pins) never come from the operator layer.
        [switch]$VendorOnly
    )
    $_layers = if ($VendorOnly) { @((Resolve-MiosVendorTomlText)) } else { @((Resolve-MiosTomlText), (Resolve-MiosVendorTomlText)) }
    foreach ($txt in $_layers) {
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

function Resolve-MiosWslNetworkingMode {
    # [wsl2].networking_mode from the layered SSOT, the only source of the .wslconfig networkingMode. NAT (the default)
    # or mirrored, case-insensitive; anything else is NAT. Mirrored needs Windows 11 22H2 (build 22621): below it the
    # result is NAT. NAT is never upgraded to mirrored, whatever the Windows build.
    param([int]$WindowsBuild = [Environment]::OSVersion.Version.Build)
    $raw = ([string](Get-MiosTomlValue -Section 'wsl2' -Key 'networking_mode' -Default 'NAT')).Trim()
    if ($raw -ieq 'NAT') { return 'NAT' }
    if ($raw -ine 'mirrored') {
        Write-Host "  [!] WSL-NETWORKING-MODE-INVALID: mios.toml [wsl2].networking_mode '$raw' is neither NAT nor mirrored; using NAT." -ForegroundColor Yellow
        return 'NAT'
    }
    if ($WindowsBuild -lt 22621) {
        Write-Host "  [!] WSL-MIRRORED-UNSUPPORTED: mios.toml [wsl2].networking_mode is mirrored but Windows build $WindowsBuild is below 22621 (Windows 11 22H2); using NAT." -ForegroundColor Yellow
        return 'NAT'
    }
    return 'mirrored'
}

function Get-MiosWslExperimentalLines {
    # The .wslconfig [experimental] hostAddressLoopback line, managed from SSOT: written under mirrored networking (the
    # only mode that reads it), removed under NAT. Every other line and section is kept as it was.
    param([AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines, [Parameter(Mandatory)][string]$NetworkingMode,
        [bool]$HostAddressLoopback = $true)
    $mirrored = ($NetworkingMode -ieq 'mirrored')
    $out = New-Object System.Collections.Generic.List[string]
    $inside = $false; $placed = $false
    foreach ($line in @($Lines)) {
        if ($line -match '^\s*\[\s*experimental\s*\]\s*(?:[#;].*)?$') {
            $inside = $true; $out.Add($line)
            if ($mirrored -and -not $placed) { $out.Add('hostAddressLoopback=' + $HostAddressLoopback.ToString().ToLowerInvariant()); $placed = $true }
            continue
        }
        if ($line -match '^\s*\[') { $inside = $false }
        if ($inside -and $line -match '^\s*hostAddressLoopback\s*=') { continue }
        $out.Add($line)
    }
    if ($mirrored -and -not $placed) {
        $out.Add('[experimental]')
        $out.Add('hostAddressLoopback=' + $HostAddressLoopback.ToString().ToLowerInvariant())
    }
    return , $out.ToArray()
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
function Get-MiosDesktopOwnerSids {
    # Owners of this session's desktop shell; another elevated account inside it is an over-the-shoulder elevation.
    $session = (Get-Process -Id $PID).SessionId
    foreach ($shell in @(Get-CimInstance Win32_Process -Filter "Name = 'explorer.exe' AND SessionId = $session" -ErrorAction SilentlyContinue)) {
        $owner = Invoke-CimMethod -InputObject $shell -MethodName GetOwnerSid -ErrorAction SilentlyContinue
        if ($owner -and $owner.ReturnValue -eq 0 -and $owner.Sid) { [string]$owner.Sid }
    }
}

function Get-MiosElevationContextError([string]$CurrentSid, [string]$OriginSid, [string[]]$DesktopSids) {
    # The operator's user layer and user-scope settings belong to one account; any other answer is ambiguous.
    $label = { param($sid) try { ([Security.Principal.SecurityIdentifier]$sid).Translate([Security.Principal.NTAccount]).Value } catch { $sid } }
    if ($OriginSid -and $OriginSid -notmatch '^S-1-[0-9]+(-[0-9]+)+$') { return 'the origin account marker is malformed' }
    if ($OriginSid -and $OriginSid -ne $CurrentSid) { return "elevation switched from $(& $label $OriginSid) to $(& $label $CurrentSid)" }
    $others = @($DesktopSids | Where-Object { $_ -and $_ -ne $CurrentSid } | Sort-Object -Unique)
    if ($others.Count) { return "this desktop belongs to $(@($others | ForEach-Object { & $label $_ }) -join ', '), not the elevated $(& $label $CurrentSid)" }
    return ''
}

function Get-MiosOriginUserContext {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $profile = [Environment]::GetFolderPath('UserProfile')
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not $profile -or -not $local -or $profile -ine $env:USERPROFILE) { throw 'The operator known folders could not be resolved in the original token.' }
    $context = [pscustomobject]@{ Sid = $identity.User.Value; Profile = $profile; LocalAppData = $local
        ConfigPath = (Join-Path $profile '.config\mios\mios.toml'); ProcessId = $PID; SessionId = (Get-Process -Id $PID).SessionId }
    Assert-MiosOriginContext $context
    return $context
}

function Assert-MiosOriginContext {
    param([Parameter(Mandatory)]$Context)
    $fields = @('Sid', 'Profile', 'LocalAppData', 'ConfigPath', 'ProcessId', 'SessionId')
    if (@($Context.PSObject.Properties.Name | Where-Object { $_ -notin $fields }).Count -or
        @($fields | Where-Object { $_ -notin $Context.PSObject.Properties.Name }).Count) { throw 'Invalid origin context fields.' }
    if ([string]$Context.Sid -notmatch '^S-1-[0-9]+(-[0-9]+)+$' -or [int]$Context.ProcessId -le 0) { throw 'Invalid origin SID or process.' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Context.ProcessId)" -ErrorAction Stop
    $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction Stop
    if (-not $process -or $owner.ReturnValue -ne 0 -or $owner.Sid -ne $Context.Sid -or
        [int]$process.SessionId -ne [int]$Context.SessionId -or
        [int]$Context.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'The originating process, SID or session no longer matches.' }
    $registered = [string](Get-ItemProperty -LiteralPath ("Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\" + $Context.Sid) -ErrorAction Stop).ProfileImagePath
    $registered = [Environment]::ExpandEnvironmentVariables($registered)
    if (-not $registered -or [IO.Path]::GetFullPath($registered).TrimEnd('\') -ine [IO.Path]::GetFullPath([string]$Context.Profile).TrimEnd('\')) { throw 'Origin profile does not match ProfileList.' }
    # REG_EXPAND_SZ must be read raw: the registry provider would expand it using the UAC account's environment.
    $shellFolders = Get-Item -LiteralPath ("Registry::HKEY_USERS\" + $Context.Sid + '\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') -ErrorAction Stop
    try { $folder = [string]$shellFolders.GetValue('Local AppData', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    finally { $shellFolders.Close() }
    $profileMarker = '%USERPROFILE%'
    $markerIndex = $folder.IndexOf($profileMarker, [StringComparison]::OrdinalIgnoreCase)
    if ($markerIndex -ge 0) { $folder = $folder.Substring(0, $markerIndex) + [string]$Context.Profile + $folder.Substring($markerIndex + $profileMarker.Length) }
    if (-not $folder -or $folder.Contains('%') -or [IO.Path]::GetFullPath($folder).TrimEnd('\') -ine [IO.Path]::GetFullPath([string]$Context.LocalAppData).TrimEnd('\')) { throw 'Origin Local AppData does not match the loaded user hive.' }
    if ([string]$Context.ConfigPath -ine (Join-Path $Context.Profile '.config\mios\mios.toml')) { throw 'Origin config path is not the operator user layer.' }
    Assert-MiosNoReparsePath $Context.Profile
    Assert-MiosNoReparsePath $Context.LocalAppData
    Assert-MiosNoReparsePath $Context.ConfigPath
}

function Get-MiosWindowsBootstrapRoot {
    param([bool]$Elevated = (Test-MiosElevated),
          [string]$LocalAppData = ([Environment]::GetFolderPath('LocalApplicationData')),
          [string]$SystemDrive = $env:SystemDrive)
    if ($Elevated) { return Join-Path $SystemDrive 'mios-bootstrap' }
    return Join-Path (Get-MiosWindowsInstallRoot -Elevated $false -LocalAppData $LocalAppData -SystemDrive $SystemDrive) 'repo\mios-bootstrap'
}

function Initialize-MiosPipePeerApi {
    if (-not ('MiosBootstrapPipePeer' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MiosBootstrapPipePeer {
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetNamedPipeServerProcessId(IntPtr handle, out uint pid);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetNamedPipeClientProcessId(IntPtr handle, out uint pid);
}
'@ -ErrorAction Stop
    }
}

function Read-MiosPipeLine {
    param([Parameter(Mandatory)][IO.TextReader]$Reader, [int]$Limit = 8388608)
    $line = New-Object Text.StringBuilder
    while ($true) {
        $next = $Reader.Read()
        if ($next -lt 0) { throw 'The machine preparation pipe closed before a complete message.' }
        if ($next -eq 10) { return $line.ToString().TrimEnd("`r") }
        if ($line.Length -ge $Limit) { throw 'Machine preparation message exceeds its size limit.' }
        [void]$line.Append([char]$next)
    }
}

function Complete-MiosMachinePreparation {
    param([Parameter(Mandatory)]$Result)
    switch ([string]$Result.Status) {
        'ready' { return }
        'pending' {
            Write-Host '  [*] Halting to await reboot; resume is registered for the operator.' -ForegroundColor Cyan
            exit 0
        }
        default { throw 'Machine preparation did not report ready; provisioning stopped before MiOS-DEV.' }
    }
}

function Invoke-MiosMachinePreparationRequest {
    param([Parameter(Mandatory)]$Request, [Parameter(Mandatory)][int]$OriginProcessId)
            if (@($request.PSObject.Properties.Name).Count -ne 3 -or
                @($request.PSObject.Properties.Name | Where-Object { $_ -notin @('Origin', 'OperatorToml', 'VendorToml') }).Count) { throw 'Invalid machine preparation request fields.' }
            if ([int]$request.Origin.ProcessId -ne $OriginProcessId) { throw 'Machine preparation request has the wrong originating process.' }
            Assert-MiosOriginContext $request.Origin
            if ((ConvertTo-MiosCredentialFreeToml ([string]$request.OperatorToml)) -cne [string]$request.OperatorToml) { throw 'The machine preparation request contains credential keys.' }
            if ([string]$request.VendorToml -notmatch '(?m)^\[meta\]\s*$' -or [string]$request.VendorToml -notmatch '(?m)^\[identity\]\s*$' -or [string]$request.VendorToml -notmatch '(?m)^\[packages\.windows\]\s*$') { throw 'Incomplete system TOML in machine preparation request.' }
            # In-memory projection only: no admin-profile lookup and no operator credentials on disk/CLI.
            $script:_MiosTomlCache = @{ '_text' = [string]$request.OperatorToml; '_source' = [string]$request.Origin.ConfigPath
                '_vendor_text' = [string]$request.VendorToml }
            return Invoke-MiosMachinePreparationCore -Context $request.Origin
}

function Invoke-MiosMachineWorker {
    param([Parameter(Mandatory)][string]$PipeName, [Parameter(Mandatory)][int]$OriginProcessId)
    if (-not (Test-MiosElevated)) { throw 'The machine worker requires UAC elevation.' }
    Initialize-MiosPipePeerApi
    $pipe = New-Object IO.Pipes.NamedPipeClientStream('.', $PipeName, [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::None)
    try {
        $pipe.Connect(180000)
        [uint32]$peer = 0
        if (-not [MiosBootstrapPipePeer]::GetNamedPipeServerProcessId($pipe.SafePipeHandle.DangerousGetHandle(), [ref]$peer) -or $peer -ne $OriginProcessId) { throw 'The machine worker pipe has the wrong originating process.' }
        $reader = New-Object IO.StreamReader($pipe, (New-Object Text.UTF8Encoding($false)), $false, 4096, $true)
        $writer = New-Object IO.StreamWriter($pipe, (New-Object Text.UTF8Encoding($false)), 4096, $true)
        $writer.AutoFlush = $true
        try {
            $line = Read-MiosPipeLine -Reader $reader
            if (-not $line -or $line.Length -gt 8388608) { throw 'Invalid machine preparation request size.' }
            $request = ConvertFrom-Json -InputObject $line -ErrorAction Stop
            $outcome = Invoke-MiosMachinePreparationRequest -Request $request -OriginProcessId $OriginProcessId
            $writer.WriteLine((@{ Status = [string]$outcome.Status } | ConvertTo-Json -Compress))
        } catch {
            $writer.WriteLine((@{ Status = 'error'; Detail = $_.Exception.Message } | ConvertTo-Json -Compress))
            throw
        } finally { $writer.Dispose(); $reader.Dispose() }
    } finally { $pipe.Dispose() }
}

function Get-MiosMachineWorkerScript {
    # Compile only the fixed machine preparation functions. Never dot-source or execute entrypoint top-level.
    param([Parameter(Mandatory)][string]$Source)
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Source, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Cannot prepare an invalid installer source.' }
    $names = @('Assert-MiosNoReparsePath', 'Get-MiosTomlAssignments', 'Split-MiosTomlKeyLine', 'Get-MiosTomlCredentialKeys',
        'ConvertTo-MiosCredentialFreeToml', 'Resolve-MiosTomlText', 'Resolve-MiosVendorTomlText', 'Get-MiosTomlValue', 'Get-MiosMachineTomlRoots',
        'Set-MiosHostTierAcl', 'Initialize-MiosHostTierDirectory', 'Write-MiosHostToml', 'Repair-MiosHostTierCredentials',
        'Assert-MiosMachineWriteBoundary', 'Initialize-MiosVendorTierDirectory', 'Write-MiosVendorToml',
        'New-MiosMachineCreationAcl', 'New-MiosProtectedMachineDirectory', 'Write-MiosPrivateMachineFile',
        'Initialize-MiosMachineBoundaryApi', 'Get-MiosMachineBoundaryGap', 'Read-MiosBoundaryObject', 'Get-MiosBoundaryDescendants',
        'Get-MiosExplicitAces', 'Test-MiosSameExplicitAces', 'Get-MiosInheritedWriteGrants', 'Get-MiosBoundaryOwnerAction',
        'Assert-MiosBoundaryAcePolicy', 'New-MiosSharedPrefixSddl', 'New-MiosLeafSddl', 'Open-MiosBoundaryAncestors',
        'Update-MiosBoundaryInheritance', 'Get-MiosNamedStreams', 'Get-MiosNewReaders', 'Initialize-MiosBoundaryBackupDirectory',
        'Assert-MiosPrivateBackupLeaf', 'Save-MiosTomlBackup', 'Save-MiosBoundaryBackup', 'Invoke-MiosMachineBoundaryRecovery',
        'Write-Info', 'Write-Good', 'Write-Err', 'Initialize-DataDisk',
        'Test-MiosElevated', 'Get-MiosFeatureState', 'Get-MiosDismEnableOutcome', 'Enable-MiOSWindowsFeatures',
        'Get-MiosTomlTableArray', 'ConvertTo-MiosWindowsPath', 'Test-MiosDefenderExclusionRefused', 'Get-MiosDefenderExclusionPlan',
        'Add-MiosDefenderExclusions', 'Test-MiosWslVmCreator', 'Register-MiosWslVmCreator', 'Set-MiosWslFirewallPolicy',
        'Get-MiosTomlStringArray', 'Get-MiosWslServicePortPlan', 'ConvertTo-MiosNetworkPrefix', 'Resolve-MiosWslRemoteScope',
        'Get-MiosVendorBoundedList', 'ConvertTo-MiosCanonicalPrefix', 'Test-MiosPrefixWithin', 'Resolve-MiosWslServiceScope',
        'Sync-MiosWslServicePortRules', 'Resolve-MiosWslNetworkingMode',
        'Invoke-MiosPlatformGate', 'Assert-MiosOriginContext', 'Initialize-MiosPipePeerApi',
        'Initialize-MiosMachineStorage', 'Publish-MiosMachineToml', 'Register-MiosBootstrapResume',
        'Get-MiosMachinePodmanExe', 'Get-MiosMachinePodmanVersion', 'Get-MiosPodmanVersion', 'Get-MiosRequiredPodmanVersion',
        'Get-MiosPinnedDownloadBytes', 'Invoke-MiosMsiInstall', 'Install-MiosPodmanClient', 'Read-MiosPodmanUninstallEntries',
        'Get-MiosOlderPodmanEntries', 'Get-MiosPodmanRemovalPlan', 'Invoke-MiosPodmanRemoval', 'Remove-MiosOlderPodman',
        'Get-MiosMachinePodmanPaths', 'Get-MiosMachinePath', 'Get-MiosMsiPackageInfo', 'Get-MiosMsiRelatedProducts', 'Remove-MiosPerUserPodman',
        'Read-MiosPipeLine', 'Invoke-MiosMachineWorker', 'Invoke-MiosMachinePreparationRequest', 'Invoke-MiosMachinePreparationCore')
    $functions = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $names }, $true))
    foreach ($name in $names) { if (@($functions | Where-Object Name -eq $name).Count -ne 1) { throw "Missing or duplicate machine helper: $name" } }
    $credentialPattern = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$Script:MiosCredentialKeyRe' }, $true))
    if ($credentialPattern.Count -ne 1) { throw 'Missing credential projection policy.' }
    return "param([string]`$PipeName, [int]`$OriginProcessId)`r`n`$ErrorActionPreference = 'Stop'`r`n" +
        $credentialPattern[0].Extent.Text + "`r`n" + (($functions | ForEach-Object { $_.Extent.Text }) -join "`r`n") +
        "`r`nInvoke-MiosMachineWorker -PipeName `$PipeName -OriginProcessId `$OriginProcessId`r`n"
}

function Invoke-MiosMachinePreparation {
    param([Parameter(Mandatory)]$Context)
    Assert-MiosOriginContext $Context
    if (([Security.Principal.WindowsIdentity]::GetCurrent().User.Value) -ne $Context.Sid) { throw 'Only the original operator can request machine preparation.' }
    if (Test-Path -LiteralPath $Context.ConfigPath) { Set-MiosPrivateUserTomlAcl $Context.ConfigPath }
    if (Test-MiosElevated) { return Invoke-MiosMachinePreparationCore -Context $Context }
    $vendor = Resolve-MiosVendorTomlText
    if (-not $vendor) { throw 'The full system TOML is required before machine preparation.' }
    # Only the credential-free operator projection crosses the privilege boundary.
    $operatorProjection = ConvertTo-MiosCredentialFreeToml (Resolve-MiosTomlText)
    $pipeName = 'MiOS-Prepare-' + [guid]::NewGuid().ToString('N')
    $security = New-Object IO.Pipes.PipeSecurity
    $security.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($Context.Sid, 'S-1-5-32-544')) {
        $rights = if ($sid -eq $Context.Sid) { 'FullControl' } else { 'ReadWrite' }
        $security.AddAccessRule((New-Object IO.Pipes.PipeAccessRule((New-Object Security.Principal.SecurityIdentifier $sid), $rights, 'Allow')))
    }
    $options = [IO.Pipes.PipeOptions]::Asynchronous
    if ($PSVersionTable.PSEdition -eq 'Desktop') {
        $pipe = New-Object IO.Pipes.NamedPipeServerStream($pipeName, [IO.Pipes.PipeDirection]::InOut, 1, [IO.Pipes.PipeTransmissionMode]::Byte, $options, 4096, 4096, $security)
    } else {
        $pipe = [IO.Pipes.NamedPipeServerStreamAcl]::Create($pipeName, [IO.Pipes.PipeDirection]::InOut, 1, [IO.Pipes.PipeTransmissionMode]::Byte,
            $options, 4096, 4096, $security, [IO.HandleInheritability]::None, [IO.Pipes.PipeAccessRights]0)
    }
    $workerPath = Join-Path $env:TEMP ('mios-machine-' + [guid]::NewGuid().ToString('N') + '.ps1')
    try {
        New-Item -ItemType File -Path $workerPath -ErrorAction Stop | Out-Null
        Set-MiosPrivateUserTomlAcl $workerPath
        $workerEncoding = New-Object Text.UTF8Encoding($true)
        [byte[]]$workerBytes = $workerEncoding.GetPreamble() + $workerEncoding.GetBytes((Get-MiosMachineWorkerScript $Script:MiosEntrySource))
        $workerSha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($workerSha.ComputeHash($workerBytes)).Replace('-', '') } finally { $workerSha.Dispose() }
        [IO.File]::WriteAllBytes($workerPath, $workerBytes)
        Initialize-MiosPipePeerApi
        $connected = $pipe.BeginWaitForConnection($null, $null)
        # UAC receives a verifier and a digest, never an unverified -File invocation.
        # Read once, hash those exact bytes, then parse those same bytes; later disk changes cannot affect this run.
        $quotedPath = $workerPath.Replace("'", "''")
        $launch = @"
`$ErrorActionPreference = 'Stop'
`$bytes = [IO.File]::ReadAllBytes('$quotedPath')
`$sha = [Security.Cryptography.SHA256]::Create()
try { `$actual = [BitConverter]::ToString(`$sha.ComputeHash(`$bytes)).Replace('-', '') } finally { `$sha.Dispose() }
if (`$actual -cne '$digest') { throw 'The machine worker source changed before UAC launch.' }
`$source = [Text.Encoding]::UTF8.GetString(`$bytes).TrimStart([char]0xfeff)
& ([scriptblock]::Create(`$source)) -PipeName '$pipeName' -OriginProcessId $PID
"@
        $encodedLaunch = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($launch))
        $engine = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        Write-Host '  [*] Approve UAC for machine preparation. This operator window owns user setup and MiOS-DEV.' -ForegroundColor Cyan
        $worker = Start-Process -FilePath $engine -Verb RunAs -PassThru -WorkingDirectory $env:SystemRoot -ErrorAction Stop `
            -ArgumentList @('-NoProfile', '-EncodedCommand', $encodedLaunch)
        if (-not $connected.AsyncWaitHandle.WaitOne(180000)) { throw 'The elevated machine worker did not connect.' }
        $pipe.EndWaitForConnection($connected)
        [uint32]$peer = 0
        if (-not [MiosBootstrapPipePeer]::GetNamedPipeClientProcessId($pipe.SafePipeHandle.DangerousGetHandle(), [ref]$peer) -or $peer -ne $worker.Id) { throw 'The machine preparation pipe has the wrong elevated worker.' }
        $writer = New-Object IO.StreamWriter($pipe, (New-Object Text.UTF8Encoding($false)), 4096, $true)
        $reader = New-Object IO.StreamReader($pipe, (New-Object Text.UTF8Encoding($false)), $false, 4096, $true)
        try {
            $writer.AutoFlush = $true
            $writer.WriteLine((@{ Origin = $Context; OperatorToml = $operatorProjection; VendorToml = $vendor } | ConvertTo-Json -Depth 6 -Compress))
            $reply = Read-MiosPipeLine -Reader $reader -Limit 65536
            if (-not $reply) { throw 'The elevated machine worker exited without readiness.' }
            $result = ConvertFrom-Json -InputObject $reply -ErrorAction Stop
            if ($result.Status -notin @('ready', 'pending')) { throw ("Machine preparation stopped: " + $result.Detail) }
            $worker.WaitForExit()
            if ($worker.ExitCode -ne 0) { throw 'The elevated machine worker failed after preparation.' }
            return $result
        } finally { $writer.Dispose(); $reader.Dispose() }
    } finally { $pipe.Dispose(); Remove-Item -LiteralPath $workerPath -Force -ErrorAction SilentlyContinue }
}

$_isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $_isAdmin -and $env:MIOS_GETMIOS_RELAUNCHED) {
    # The marker belongs to the elevated child; unelevated, it would skip elevation and reach the admin steps.
    throw 'MIOS_GETMIOS_RELAUNCHED is set in a non-elevated session; refusing to continue. Open a new PowerShell window and re-run the one-liner.'
}
if ($_isAdmin) {
    $_ctxError = Get-MiosElevationContextError -CurrentSid ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value) `
        -OriginSid ([string]$env:ELEVATION_ORIGIN_SID) -DesktopSids @(Get-MiosDesktopOwnerSids)
    if ($_ctxError) {
        throw "Refusing to continue: $_ctxError. MiOS reads the operator's user layer and applies user-scope settings for the account that runs it; run the one-liner from the operator's own administrator-capable account."
    }
}
# Keep this process and its real token: HKCU, known folders, Appx and WSL registrations
# must continue to belong to the operator, including when UAC uses a different admin.
$Script:MiosOriginContext = Get-MiosOriginUserContext
if ([string]::IsNullOrWhiteSpace($RepoDir)) {
    $RepoDir = Get-MiosWindowsBootstrapRoot -Elevated $_isAdmin -LocalAppData $Script:MiosOriginContext.LocalAppData
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

        $_sysDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
        $miosFontDir = Join-Path (Get-MiosWindowsInstallRoot) 'fonts'
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
    $_sysDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
    $installRoot = Join-Path (Get-MiosWindowsInstallRoot) 'cursors/Bibata-Modern-Classic'
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
function Get-MiosTerminalProfileCommand {
    param([Parameter(Mandatory)][string]$ShellPath, [Parameter(Mandatory)][string]$ProfilePath)
    $literalProfile = $ProfilePath.Replace("'", "''")
    return '"' + $ShellPath + '" -NoLogo -NoExit -NoProfile -Command "$env:MIOS_APP_CONTEXT=''1''; if (Test-Path ''' + $literalProfile + ''') { . ''' + $literalProfile + ''' }"'
}

function Install-MiOSTerminalProfile {
    param([string]$LinuxLogin = '')
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
    $_sysDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
    $miosProfilePath = (Get-MiosInstalledLayout).ProfileBody
    $profileCmdline = Get-MiosTerminalProfileCommand -ShellPath $defaultPwsh -ProfilePath $miosProfilePath

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
        startingDirectory = $(if ($env:SystemDrive) { "$($env:SystemDrive)\\" } else { 'C:\\' })
    }
    foreach ($k in $commonProfileProps.Keys) { $miosProfile[$k] = $commonProfileProps[$k] }

    $_devDistroName = 'podman-MiOS-DEV'   # default if probing fails
    # The MiOS-DEV login: the launcher's second render passes the guard's name (-LinuxLogin). This first render runs
    # before the guest exists, so the vendor [identity].username is only a placeholder that the launcher replaces.
    $_devLogin = if ($LinuxLogin) { $LinuxLogin } else { ([string](Get-MiosTomlValue -Section 'identity' -Key 'username' -Default '' -VendorOnly)).Trim() }
    $_devLoginArg = if ($_devLogin -cmatch '^[a-z_][a-z0-9_-]*$') { ' --user ' + $_devLogin } else { '' }
    try {
        $_wslList = @(& wsl.exe -l -q 2>$null) | ForEach-Object { ($_ -replace [char]0,'').Trim() } | Where-Object { $_ }
        foreach ($_cand in @('podman-MiOS-DEV','MiOS-DEV','podman-MiOS-BUILDER','MiOS-BUILDER')) {
            if ($_wslList -contains $_cand) { $_devDistroName = $_cand; break }
        }
    } catch {}
    $miosDevProfile = [ordered]@{
        guid              = $miosDevGuid
        name              = 'MiOS-DEV'
        commandline       = ('wsl.exe -d ' + $_devDistroName + ' --cd /' + $_devLoginArg)
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
    $sysLetter = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd(':') } else { 'C' }
    $sysDrive  = "${sysLetter}:"
    $miosRoot = Get-MiosWindowsInstallRoot
    $miosBin   = Join-Path $miosRoot 'bin'
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

# This script lives in the installed layout's bin, next to the native launcher; MIOS_NATIVE_BIN overrides it.
$_nativeBin = if ($env:MIOS_NATIVE_BIN) { $env:MIOS_NATIVE_BIN } else { $PSScriptRoot }
$_nativeLauncher = Join-Path $_nativeBin 'mios-launch.exe'
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
    $sysLetter = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd(':') } else { 'C' }
    $sysDrive  = "${sysLetter}:"
    $miosProfile = Join-Path (Split-Path $PSScriptRoot -Parent) 'powershell/profile.ps1'
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

    $_layout = Get-MiosInstalledLayout
    $_runHiddenVbs = Join-Path $_layout.NativeBin 'run-hidden.vbs'
    if (-not (Test-Path -LiteralPath $_runHiddenVbs)) { $_runHiddenVbs = Join-Path $_layout.Root 'run-hidden.vbs' }
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
        $altIcon = if (Test-Path 'M:\MiOS\icons\mios.ico') { 'M:\MiOS\icons\mios.ico' } else { Join-Path $miosRoot 'icons\mios.ico' }
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
    $_launcherExe = Join-Path (Get-MiosInstalledLayout).NativeBin 'mios-launch.exe'
    $_useExeLauncher = Test-Path -LiteralPath $_launcherExe

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
        $sc.Description      = 'Uninstall MiOS (removes WT profiles, WSL distros, root overlay, registry keys, shortcuts)'
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

    $sysLetter = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd(':') } else { 'C' }
    $sysDrive  = "${sysLetter}:"
    $miosRoot = Get-MiosWindowsInstallRoot
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
    $sysLetter = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd(':') } else { 'C' }
    $sysDrive  = "${sysLetter}:"
    $miosRoot = Get-MiosWindowsInstallRoot
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
    param([switch]$Required)
    # Bootstrap may run before the native catalog is installed. Final installation
    # and runtime must use the native renderer; a deferred phase is never success.
    $nativeRoots = @((Get-MiosInstalledLayout).NativeBin)
    foreach ($nativeRoot in $nativeRoots) {
        if (-not $nativeRoot) { continue }
        $launcher = Join-Path $nativeRoot 'mios-launch.exe'
        if ((Test-Path -LiteralPath $launcher -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $nativeRoot 'native-binding.json') -PathType Leaf)) {
            # GUI launchers are asynchronous under irm|iex; do not reuse a stale native exit code.
            # Wait for this projection and consume this process's own status.
            $projectionProcess = Start-Process -FilePath $launcher -ArgumentList '--stage-host-terminal' -NoNewWindow -Wait -PassThru
            if ($projectionProcess.ExitCode -ne 0) { throw "Native layered SSOT tmux projection failed (exit $($projectionProcess.ExitCode))" }
            return (Join-Path $env:LOCALAPPDATA 'MiOS\terminal\mios-host.tmux.conf')
        }
    }
    if ($Required) { throw 'Native tmux renderer or runtime binding is missing; installation is incomplete.' }
    Write-Host '  [tmux] Projection deferred until the native catalog and runtime binding are installed.' -ForegroundColor DarkGray
    return $null
}

function Install-MiOSPowerShellProfile {
    $sysLetter = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd(':') } else { 'C' }
    $sysDrive  = "${sysLetter}:"
    # The body path and its native-bin default both come from the installed layout; build-mios.ps1's redirector
    # resolves the same body through the same function.
    $_layout = Get-MiosInstalledLayout
    $miosProfileScript = $_layout.ProfileBody
    $miosPsRoot = Split-Path -Parent $miosProfileScript
    if (-not (Test-Path $miosPsRoot)) { New-Item -ItemType Directory -Path $miosPsRoot -Force | Out-Null }
    $safeNativeBin = $_layout.Bin.Replace("'", "''")
    $safeInstallRoot = (Get-MiosWindowsInstallRoot).Replace("'", "''")
    $operatorRepoRoot = if (Test-MiosElevated) { Get-MiosWindowsInstallRoot } else { Join-Path (Get-MiosWindowsInstallRoot) 'repo' }
    $safeOperatorRepoRoot = $operatorRepoRoot.Replace("'", "''")

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
        '      (registry, env vars, fonts, WT settings, WSL distros, root overlay).',
        '    * Telemetry: NONE (no data leaves the host without explicit operator action).',
        "    * Full text: $sysDrive\AGREEMENTS.md  +  $sysDrive\LICENSE",
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

    $userTomlHelperBlock = (@('Assert-MiosNoReparsePath', 'Set-MiosPrivateUserTomlAcl', 'Import-MiosUserToml',
        'ConvertTo-MiosNativeArgument', 'Get-MiosLinuxLogin', 'Sync-MiosUserTomlToBuilder') | ForEach-Object {
            'function ' + $_ + " {`r`n" + (Get-Command $_ -CommandType Function -ErrorAction Stop).Definition + "`r`n}"
        }) -join "`r`n"
    $windowsRefreshBlock = 'function Update-MiosWindowsBuildCheckout {' +
        (Get-Command Update-MiosWindowsBuildCheckout -CommandType Function -ErrorAction Stop).Definition + "`r`n}"
    $miosBuildRef = [string](Get-MiosTomlValue -Section 'bootstrap' -Key 'mios_ref' -Default 'main')
    $miosBuildRefLiteral = "'" + $miosBuildRef.Replace("'", "''") + "'"
    $_devName = [string](Get-MiosTomlValue -Section 'bootstrap' -Key 'dev_distro' -Default 'MiOS-DEV')
    if ($_devName -notmatch '^[A-Za-z0-9_.-]+$') { throw 'bootstrap.dev_distro is not a valid WSL distribution name.' }
    $miosDevDistroLiteral = "'" + $_devName + "'"

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

    `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
    `$miosArtifactRoot = '$safeInstallRoot'
    if (-not (Test-Path -LiteralPath `$miosArtifactRoot)) {
        try { New-Item -ItemType Directory -Path `$miosArtifactRoot -Force | Out-Null } catch {}
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
        (Join-Path `$_sysDrive 'MiOS\themes\mios.omp.json'),
        (Join-Path `$_sysDrive 'usr\share\mios\oh-my-posh\mios.omp.json'),
        'M:\MiOS\themes\mios.omp.json',
        'M:\usr\share\mios\oh-my-posh\mios.omp.json'
    )
    foreach (`$c in `$ompCands) {
        if (`$c -and (Test-Path -LiteralPath `$c)) { `$miosOmp = `$c; break }
    }
    if (-not `$miosOmp) {
        `$miosOmp = _MiosSelfHeal 'themes' 'mios.omp.json' '$ompBlobBase64'
    }

    # Both variants use the same native dashboard; no embedded roster or TOML scrape.
    function Show-MiosDashboard {
        param([string]`$ConfigPath, [string]`$LogoPath)
        `$_native = if (`$env:MIOS_NATIVE_BIN) { `$env:MIOS_NATIVE_BIN } else { '$safeNativeBin' }
        `$_shell = Join-Path `$_native 'mios-native-shell.ps1'
        if (-not (Test-Path -LiteralPath `$_shell)) {
            Write-Host "  [!] MiOS native shell missing at `$_shell -- finish the MiOS install, then open a new terminal." -ForegroundColor Yellow
            return
        }
        . `$_shell -BinDirectory `$_native
        Invoke-MiosNativeDashboard
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
$userTomlHelperBlock
$windowsRefreshBlock
function Resolve-MiosDevDistro {
    `$name = $miosDevDistroLiteral
    `$registered = @((& wsl.exe -l -q 2>`$null) -split "`r?`n" | ForEach-Object { (`$_ -replace [char]0, '').Trim() } | Where-Object { `$_ })
    if (`$LASTEXITCODE -ne 0) { throw 'Could not enumerate the configured MiOS builder.' }
    foreach (`$candidate in @(('podman-' + `$name), `$name)) { if (`$registered -contains `$candidate) { return `$candidate } }
    throw 'The configured MiOS builder is unavailable; the saved TOML was retained for retry.'
}

function mios-build {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]`$Args)
    `$skipConfig = `$Args -contains '-SkipConfig'
    `$skipPull   = `$Args -contains '-SkipPull'
    `$syncedUserToml = `$false
    `$forwardArgs = @(`$Args | Where-Object { `$_ -notin @('-SkipConfig','-SkipPull') })

    # -- Step 1 + 2: configurator pass ------------------------------
    if (-not `$skipConfig) {
        `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
        `$cfgHtml = `$null
        foreach (`$c in @(
            (Join-Path '$safeOperatorRepoRoot' 'usr\share\mios\configurator\mios.html'),
            (Join-Path '$safeInstallRoot' 'usr\share\mios\configurator\mios.html'),
            (Join-Path '$safeOperatorRepoRoot' 'usr\share\mios\configurator\mios.html'),
        (Join-Path '$safeInstallRoot' 'usr\share\mios\configurator\mios.html'),
        (Join-Path `$_sysDrive 'usr\share\mios\configurator\mios.html'),
            (Join-Path `$_sysDrive 'MiOS\usr\share\mios\configurator\mios.html'),
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
            Write-Host '  [!] Configurator HTML not found -- skipping edit pass.' -ForegroundColor Yellow
            Write-Host '      Run `mios pull` first to seed the overlay.' -ForegroundColor DarkGray
        }

        Write-Host ''
        Write-Host '  [2/4] Scanning Downloads for edited config files...' -ForegroundColor Cyan
        `$dlDir = Join-Path `$env:USERPROFILE 'Downloads'
        if (Test-Path -LiteralPath `$dlDir) {
            `$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            # Operator edits are a host overlay. Keep the fetched vendor
            # mios.toml in usr\share\mios intact for layered resolution.
            `$tomlSrc = Get-ChildItem -LiteralPath `$dlDir -Filter 'mios*.toml' -File -ErrorAction SilentlyContinue |
                Where-Object { `$_.Name -notmatch '\.imported-' } |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if (`$tomlSrc) {
                # mios-boot-win-03: the saved configurator file carries the D0 credential, so it is the USER tier:
                # ~\.config\mios\mios.toml here, and ~/.config/mios/mios.toml (0600, written from stdin, never a
                # command line) for the builder distro's mios user. It is never copied into C:\etc\mios or M:\etc\mios.
                `$tomlDst = Import-MiosUserToml -SourcePath `$tomlSrc.FullName
                `$_devDistro = Resolve-MiosDevDistro
                Sync-MiosUserTomlToBuilder -Path `$tomlDst -Distro `$_devDistro -User (Get-MiosLinuxLogin `$_devDistro)
                `$syncedUserToml = `$true
                Write-Host ('         [+] '+`$tomlSrc.Name+' -> private user tiers (Windows + '+`$_devDistro+')') -ForegroundColor Green
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
                `$htmlDst = Join-Path '$safeOperatorRepoRoot' 'usr\share\mios\configurator\mios.html'
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

    # Existing user tiers and -SkipConfig retries must reach the builder as well.
    `$existingUserToml = Join-Path `$env:USERPROFILE '.config\mios\mios.toml'
    if (-not `$syncedUserToml -and (Test-Path -LiteralPath `$existingUserToml -PathType Leaf)) {
        `$_devDistro = Resolve-MiosDevDistro
        Sync-MiosUserTomlToBuilder -Path `$existingUserToml -Distro `$_devDistro -User (Get-MiosLinuxLogin `$_devDistro)
    }
    # END PRIVATE USER CONFIGURATION HANDOFF

    if (-not `$skipPull) {
        Write-Host ''
        Write-Host '  [3/4] Syncing root overlay (mios.git + mios-bootstrap)...' -ForegroundColor Cyan
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
    `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
    `$_targetRoot = '$safeOperatorRepoRoot'
    if (-not (Test-Path (Join-Path `$_targetRoot '.git'))) {
        Write-Host "  [!] `$_targetRoot is not a git working tree -- run mios-build first." -ForegroundColor Yellow
        return
    }
    Update-MiosWindowsBuildCheckout -Path `$_targetRoot -Ref $miosBuildRefLiteral

}

function mios-config {
    `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
    `$cfg = `$null
    foreach (`$_cand in @(
        (Join-Path `$_sysDrive 'usr\share\mios\configurator\mios.html'),
        (Join-Path `$_sysDrive 'MiOS\usr\share\mios\configurator\mios.html'),
        'M:\usr\share\mios\configurator\mios.html',
        'M:\MiOS\usr\share\mios\configurator\mios.html'
    )) {
        if (Test-Path -LiteralPath `$_cand) { `$cfg = `$_cand; break }
    }
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
        & wsl.exe -d `$_devDistro --cd / --user (Get-MiosLinuxLogin `$_devDistro) @Args
    }
}

function mios-mini {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]`$Args)
    if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
        `$_distro = `$null
        foreach (`$_d in @('podman-MiOS-DEV','MiOS-DEV')) {
            try {
                `$_chk = & wsl.exe -d `$_d -u root --exec echo ready 2>`$null
                if (`$LASTEXITCODE -eq 0 -and `$_chk -match 'ready') { `$_distro = `$_d; break }
            } catch {}
        }
        if (`$_distro) {
            & wsl.exe -d `$_distro --cd / --user (Get-MiosLinuxLogin `$_distro) -- /usr/libexec/mios/mios-dashboard.sh --mini @Args
            return
        }
    }
    if (Get-Command Show-MiosDashboard -ErrorAction SilentlyContinue) {
        `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
        `$cfg  = ''
        foreach (`$_c in @((Join-Path '$safeInstallRoot' 'fastfetch\config.jsonc'), (Join-Path `$_sysDrive 'MiOS\fastfetch\config.jsonc'), 'M:\MiOS\fastfetch\config.jsonc')) {
            if (Test-Path -LiteralPath `$_c) { `$cfg = `$_c; break }
        }
        `$logo = ''
        foreach (`$_l in @((Join-Path '$safeInstallRoot' 'fastfetch\mios.txt'), (Join-Path `$_sysDrive 'MiOS\fastfetch\mios.txt'), 'M:\MiOS\fastfetch\mios.txt')) {
            if (Test-Path -LiteralPath `$_l) { `$logo = `$_l; break }
        }
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
        `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
        `$cfg  = ''
        foreach (`$_c in @((Join-Path '$safeInstallRoot' 'fastfetch\config.jsonc'), (Join-Path `$_sysDrive 'MiOS\fastfetch\config.jsonc'), 'M:\MiOS\fastfetch\config.jsonc')) {
            if (Test-Path -LiteralPath `$_c) { `$cfg = `$_c; break }
        }
        `$logo = ''
        foreach (`$_l in @((Join-Path '$safeInstallRoot' 'fastfetch\mios.txt'), (Join-Path `$_sysDrive 'MiOS\fastfetch\mios.txt'), 'M:\MiOS\fastfetch\mios.txt')) {
            if (Test-Path -LiteralPath `$_l) { `$logo = `$_l; break }
        }
        Show-MiosDashboard -ConfigPath `$cfg -LogoPath `$logo
    } else {
        Write-Host '  [!] mios mini: Show-MiosDashboard not loaded.' -ForegroundColor Yellow
    }
}

function mios-dash {
    `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
    `$_ascii = `$null
    foreach (`$_p in @(
        (Join-Path `$_sysDrive 'MiOS\fastfetch\mios.txt'),
        (Join-Path `$_sysDrive 'usr\share\mios\branding\mios.txt'),
        'M:\MiOS\fastfetch\mios.txt',
        'M:\usr\share\mios\branding\mios.txt'
    )) {
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
    `$_ffCfg = `$null
    foreach (`$_c in @(
        (Join-Path `$_sysDrive 'MiOS\fastfetch\config.jsonc'),
        (Join-Path `$_sysDrive 'usr\share\mios\fastfetch\config.jsonc'),
        'M:\MiOS\fastfetch\config.jsonc',
        'M:\usr\share\mios\fastfetch\config.jsonc'
    )) {
        if (Test-Path -LiteralPath `$_c) { `$_ffCfg = `$_c; break }
    }
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
                `$_chk = & wsl.exe -d `$_d -u root --exec echo ready 2>`$null
                if (`$LASTEXITCODE -eq 0 -and `$_chk -match 'ready') { `$_distro = `$_d; break }
            } catch {}
        }
        if (`$_distro) {
            try {
                & wsl.exe -d `$_distro --user (Get-MiosLinuxLogin `$_distro) -- bash -lc 'systemctl --user list-units --type=service --state=active --no-legend --no-pager 2>/dev/null | head -30; echo ""; echo "graphical-session.target: `$(systemctl --user is-active graphical-session.target 2>/dev/null)"; echo "xdg-desktop-portal.service: `$(systemctl --user is-active xdg-desktop-portal.service 2>/dev/null)"; echo "podman.socket: `$(systemctl --user is-active podman.socket 2>/dev/null)"'
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
    Write-Host '  mios pull     git fetch + hard reset root overlay to origin/main' -ForegroundColor White
    Write-Host '  mios config   open the HTML configurator (mios.toml editor)' -ForegroundColor White
    Write-Host '  mios ai       open Open WebUI (rich LLM interface) in your browser' -ForegroundColor White
    Write-Host '  mios dev      wsl into MiOS-DEV (root /) as the MiOS login, [identity].username; re-run the installer after changing it' -ForegroundColor White
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
    `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
    `$_cands = @(
        (Join-Path `$env:USERPROFILE '.config\mios\mios.toml'),
        (Join-Path `$_sysDrive 'etc\mios\mios.toml'),
        (Join-Path `$_sysDrive 'usr\share\mios\mios.toml'),
        (Join-Path `$_sysDrive 'MiOS\usr\share\mios\mios.toml'),
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
            `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
            foreach (`$_bDir in @(
                (Join-Path `$_sysDrive 'MiOS\btop'),
                'M:\MiOS\btop',
                (Join-Path `$env:LOCALAPPDATA 'btop')
            )) {
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
        & wsl.exe -d `$_dev --user (Get-MiosLinuxLogin `$_dev) -- btop @args
    } finally {
        try { Set-MiosWindow -Mode portal } catch {}
    }
}

# Windows tmux dispatch is rendered and enforced by the native Rust SSOT launcher.
function tmux {
    `$_nativeBin = if (`$env:MIOS_NATIVE_BIN) { `$env:MIOS_NATIVE_BIN } else { '$safeNativeBin' }
    & (Join-Path `$_nativeBin 'mios-launch.exe') --tmux @args
}
"@
    # The native client setup replaces this block with its own hook once it has installed the native shell.
    $nativeStartup = @'
# >>> MiOS native SSOT runtime >>>
$_miosNativeBin = if ($env:MIOS_NATIVE_BIN) { $env:MIOS_NATIVE_BIN } else { '__MIOS_NATIVE_BIN__' }
$_miosNativeShell = Join-Path $_miosNativeBin 'mios-native-shell.ps1'
if (-not (Test-Path -LiteralPath $_miosNativeShell)) {
    Write-Host "  [!] MiOS native shell missing at $_miosNativeShell -- finish the MiOS install, then open a new terminal." -ForegroundColor Yellow
} else {
    . $_miosNativeShell -BinDirectory $_miosNativeBin
    if (-not $env:MIOS_SKIP_MOTD -and -not $env:MIOS_HOST_TMUX -and -not $env:TMUX -and -not $env:SSH_CONNECTION -and -not [Console]::IsInputRedirected) {
        & (Join-Path $_miosNativeBin 'mios-launch.exe') --host-terminal
        if ($LASTEXITCODE -ne 0) { throw "Native MiOS host terminal failed (exit $LASTEXITCODE)" }
        return
    }
}
# <<< MiOS native SSOT runtime <<<
'@
    $nativeStartup = $nativeStartup.Replace('__MIOS_NATIVE_BIN__', $safeNativeBin)
    $miosScriptBody = $nativeStartup + "`n" + $miosScriptBody
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
    `$_sysDrive = if (`$env:SystemDrive) { `$env:SystemDrive } else { 'C:' }
    `$_diagDir = Join-Path '$safeInstallRoot' 'diagnostics'
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

function Get-MiosFeatureState([string[]]$Info) {
    # dism /get-featureinfo: 'Enable Pending' = enabled earlier, but the restart that activates it has not happened
    if (@($Info) -match '^\s*State\s*:\s*Enabled\s*$') { return 'enabled' }
    if (@($Info) -match '^\s*State\s*:\s*Enable Pending\s*$') { return 'pending' }
    return 'disabled'
}

function Get-MiosDismEnableOutcome([int]$ExitCode) {
    # dism /enable-feature /norestart: 0 = enabled; 3010 (ERROR_SUCCESS_REBOOT_REQUIRED) = enabled, active after a restart
    switch ($ExitCode) { 0 { return 'enabled' } 3010 { return 'restart' } default { return 'unavailable' } }
}

function Enable-MiOSWindowsFeatures {
    # Status: ready | pending (restart first) | error | deferred. Only 'ready' may reach the WSL firewall policy and MiOS-DEV.
    if (-not (Test-MiosElevated)) {
        Write-Host '  [!] Windows features need an elevated run; nothing was checked or enabled.' -ForegroundColor Red
        return [pscustomobject]@{ Status = 'deferred'; Detail = 'not elevated'; RebootRequired = $false }
    }

    $features = @(
        @{ Name = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'wsl'    -Default 'Microsoft-Windows-Subsystem-Linux')
           Label = 'Windows Subsystem for Linux'; Required = $true; StoreWsl = $true },
        @{ Name = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'vmp'    -Default 'VirtualMachinePlatform')
           Label = 'Virtual Machine Platform (WSL2 + Hyper-V hypervisor)'; Required = $true; StoreWsl = $false },
        @{ Name = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'hyperv' -Default 'Microsoft-Hyper-V')
           Label = 'Hyper-V (manager + VMs)'; Required = $false; StoreWsl = $false })

    $rebootPending = $false
    $failed = New-Object System.Collections.Generic.List[string]
    foreach ($feature in $features) {
        $name = $feature.Name; $label = $feature.Label
        $state = 'unknown'
        try {
            $info = & dism.exe /online /English /get-featureinfo /featurename:$name 2>$null   # English: the state text is parsed
            if ($LASTEXITCODE -eq 0) { $state = Get-MiosFeatureState $info }
        } catch {}

        if ($state -eq 'enabled') {
            Write-Host "  [+] $label already enabled." -ForegroundColor DarkGray
            continue
        }
        if ($state -eq 'pending') {
            Write-Host "  [+] $label enabled; a restart is still pending." -ForegroundColor Yellow
            $rebootPending = $true
            continue
        }

        if ($feature.StoreWsl) {
            $_wslOk = $false
            try { & wsl.exe --version *> $null; if ($LASTEXITCODE -eq 0) { $_wslOk = $true } } catch {}
            if ($_wslOk) {
                Write-Host "  [+] $label satisfied (wsl.exe present; Store-based WSL needs no optional feature)." -ForegroundColor DarkGray
                continue
            }
        }

        Write-Host "  [*] Enabling $label..." -ForegroundColor Cyan
        $outcome = 'unavailable'
        try {
            $null = & dism.exe /online /enable-feature /featurename:$name /all /norestart
            $outcome = Get-MiosDismEnableOutcome $LASTEXITCODE
        } catch {
            Write-Host "  [!] dism /enable-feature $name failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        if ($outcome -eq 'enabled') {
            Write-Host "  [+] $label enabled." -ForegroundColor Green
        } elseif ($outcome -eq 'restart') {
            Write-Host "  [+] $label enabled; Windows needs a restart to activate it." -ForegroundColor Green
            $rebootPending = $true
        } elseif ($feature.Required) {
            Write-Host "  [!] $label could not be enabled; MiOS-DEV requires it." -ForegroundColor Red
            $failed.Add($label)
        } else {
            Write-Host "  [-] $label not available on this Windows edition -- skipping." -ForegroundColor DarkGray
        }
    }

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        $_wslPkg = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'wsl_pkg' -Default 'Microsoft.WSL')
        Write-Host "  [*] wsl.exe not on PATH -- installing WSL ($_wslPkg via Microsoft Store MSIX)..." -ForegroundColor Cyan
        # Appx/winget registration belongs to the original operator; this helper uses the system WSL installer.
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
            Write-Host "  [+] WSL: preview channel updated" -ForegroundColor Green
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
        }
    } else {
        Write-Host "  [-] wsl.exe still not on PATH after install attempts -- next-session reboot may surface it." -ForegroundColor DarkGray
        $rebootPending = $true
    }

    if ($failed.Count) {
        return [pscustomobject]@{ Status = 'error'; Detail = ($failed -join ', '); RebootRequired = $rebootPending }
    }
    if ($rebootPending) {
        Write-Host ''
        Write-Host '  +==============================================================+' -ForegroundColor Yellow
        Write-Host '  | REBOOT PENDING -- Windows features enabled this session need |' -ForegroundColor Yellow
        Write-Host '  | a reboot to take full effect. WSL2, the dev VM, podman       |' -ForegroundColor Yellow
        Write-Host '  | machine init, and the OCI build will fail until you reboot.  |' -ForegroundColor Yellow
        Write-Host '  +==============================================================+' -ForegroundColor Yellow
        if (([string](Get-MiosTomlValue -Section 'bootstrap.prereqs.features' -Key 'require_reboot_to_continue' -Default 'true')) -ine 'true') {
            Write-Host '  [!] require_reboot_to_continue=false is refused: the WSL firewall policy and MiOS-DEV need active features.' -ForegroundColor Yellow
        }
        Write-Host ''
        Write-Host '  REBOOT NOW, then re-run the irm|iex one-liner. Pass-0 reaps' -ForegroundColor Cyan
        Write-Host '  prior state automatically; the next run starts clean and'  -ForegroundColor Cyan
        Write-Host '  proceeds straight through.' -ForegroundColor Cyan
        Write-Host ''
        return [pscustomobject]@{ Status = 'pending'; Detail = 'restart required'; RebootRequired = $true }
    }
    return [pscustomobject]@{ Status = 'ready'; Detail = ''; RebootRequired = $false }
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

function Get-MiosMachinePodmanPaths {
    # The per-machine podman.exe locations, all under %ProgramFiles% (administrator-writable): the pinned 6.x MSI's
    # INSTALLDIR first, then the 5.x setup bundle's layout. Never PATH, never a user profile.
    return @((Join-Path ${env:ProgramFiles} 'Podman\podman.exe'), (Join-Path ${env:ProgramFiles} 'RedHat\Podman\podman.exe'))
}

function Resolve-MiosPodmanExe {
    # User side only: the podman.exe this unelevated session would run, or $null. The per-machine install paths after
    # PATH, so a client the elevated stage just installed is found before a new logon picks up the machine PATH.
    $cmd = Get-Command podman -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return [string]$cmd.Source }
    foreach ($candidate in @(Get-MiosMachinePodmanPaths)) { if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate } }
    return $null
}

function Get-MiosMachinePodmanExe {
    # Elevated side: only the per-machine install paths, never PATH. Under admin-approval UAC the elevated PATH still carries
    # user-writable directories (WinGet Links, WindowsApps), so a podman.exe found through PATH could be a planted one.
    foreach ($path in @(Get-MiosMachinePodmanPaths)) { if (Test-Path -LiteralPath $path -PathType Leaf) { return $path } }
    return $null
}

function Get-MiosMachinePodmanVersion {
    # The machine client's version. It is executed elevated only after its Authenticode signature is Valid and its signer
    # is exactly the SSOT signer; an absent client is $null, a present unsigned or foreign-signed one is refused, never run.
    param([Parameter(Mandatory)][string]$Signer)
    $exe = Get-MiosMachinePodmanExe
    if (-not $exe) { return $null }
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ([string]$signature.Status -ne 'Valid') { throw "The machine Podman client $exe has a $($signature.Status) signature, not Valid; it is never run elevated. Nothing was installed." }
    $subject = if ($signature.SignerCertificate) { [string]$signature.SignerCertificate.Subject } else { '' }
    if ($subject -cne $Signer) { throw "The machine Podman client $exe is signed by '$subject', not the SSOT signer; it is never run elevated. Nothing was installed." }
    return Get-MiosPodmanVersion $exe
}

function Get-MiosPodmanVersion {
    # major.minor[.build] from `podman --version`, or $null when the client is absent or unreadable.
    param([AllowEmptyString()][string]$Exe)
    if (-not $Exe) { return $null }
    $text = ((& $Exe --version 2>$null) | Out-String)
    if ($text -match '(\d+)\.(\d+)(?:\.(\d+))?') {
        $build = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        return New-Object Version ([int]$Matches[1]), ([int]$Matches[2]), $build
    }
    return $null
}

function Get-MiosRequiredPodmanVersion {
    # The Podman client must be at least the machine-os major.minor MiOS-DEV is provisioned from ([image].machine_os_tag).
    $tag = [string](Get-MiosTomlValue -Section 'image' -Key 'machine_os_tag' -Default '')
    if ($tag -notmatch '^v?(\d+)\.(\d+)') { throw "The system SSOT [image].machine_os_tag '$tag' is not a major.minor machine-os version; the Podman client cannot be checked." }
    return New-Object Version ([int]$Matches[1]), ([int]$Matches[2])
}

function Get-MiosPinnedDownloadBytes {
    param([Parameter(Mandatory)][string]$Uri)
    $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -ErrorAction Stop
    if ($response.Content -is [byte[]]) { return , $response.Content }
    if ($response.RawContentStream) { return , $response.RawContentStream.ToArray() }
    throw "The pinned download $Uri returned no binary content."
}

function Invoke-MiosMsiInstall {
    param([Parameter(Mandatory)][string]$Path, [string[]]$Properties = @())
    $msiexec = Join-Path $env:SystemRoot 'System32\msiexec.exe'
    $proc = Start-Process -FilePath $msiexec -ArgumentList (@('/i', ('"' + $Path + '"')) + @($Properties) + @('/qn', '/norestart')) -Wait -PassThru -WindowStyle Hidden -ErrorAction Stop
    return [int]$proc.ExitCode
}

function Get-MiosMachinePath {
    # The machine-wide PATH (HKLM Environment) as stored, without this process's additions.
    return [string][Environment]::GetEnvironmentVariable('Path', 'Machine')
}

function Get-MiosMsiPackageInfo {
    # ProductCode, UpgradeCode and Manufacturer from an MSI's own Property table, opened read-only through Windows Installer
    # (msiOpenDatabaseModeReadOnly). Called only on the private copy after its sha256, signature and signer are verified.
    param([Parameter(Mandatory)][string]$Path)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $db = $installer.GetType().InvokeMember('OpenDatabase', 'InvokeMethod', $null, $installer, @($Path, [int]0))
    $info = [ordered]@{}
    try {
        foreach ($name in 'ProductCode', 'UpgradeCode', 'Manufacturer') {
            $view = $db.GetType().InvokeMember('OpenView', 'InvokeMethod', $null, $db, @("SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = '$name'"))
            try {
                [void]$view.GetType().InvokeMember('Execute', 'InvokeMethod', $null, $view, $null)
                $record = $view.GetType().InvokeMember('Fetch', 'InvokeMethod', $null, $view, $null)
                $info[$name] = if ($record) { [string]$record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, [object[]]@([int]1)) } else { '' }
            } finally {
                [void]$view.GetType().InvokeMember('Close', 'InvokeMethod', $null, $view, $null)
                [void][Runtime.InteropServices.Marshal]::ReleaseComObject($view)
            }
        }
    } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($db) }
    return [pscustomobject]$info
}

function Get-MiosMsiRelatedProducts {
    # The installed products of one UpgradeCode as this (elevated) account sees them: the per-machine ones and its own
    # per-user ones. AssignmentType is Windows Installer's own: '1' per-machine, '0' per-user.
    param([Parameter(Mandatory)][string]$UpgradeCode)
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $related = $installer.GetType().InvokeMember('RelatedProducts', 'GetProperty', $null, $installer, @($UpgradeCode))
    $count = [int]$related.GetType().InvokeMember('Count', 'GetProperty', $null, $related, $null)
    $products = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $count; $i++) {
        $code = [string]$related.GetType().InvokeMember('Item', 'GetProperty', $null, $related, [object[]]@([int]$i))
        $assignment = [string]$installer.GetType().InvokeMember('ProductInfo', 'GetProperty', $null, $installer, [object[]]@($code, 'AssignmentType'))
        $version = [string]$installer.GetType().InvokeMember('ProductInfo', 'GetProperty', $null, $installer, [object[]]@($code, 'VersionString'))
        $products.Add([pscustomobject]@{ ProductCode = $code; AssignmentType = $assignment; Version = $version })
    }
    return $products.ToArray()
}

function Remove-MiosPerUserPodman {
    # A per-user install of the pinned package family (the UpgradeCode read from the verified MSI) belongs to the account
    # that elevated, not to the machine, and the operator account never sees it. It is removed by its GUID-validated
    # ProductCode with msiexec /x in this account's context, then re-checked. Per-machine products of the family are left to
    # the MSI's own upgrade; any other state refuses before install.
    param([Parameter(Mandatory)][string]$UpgradeCode)
    $guid = '^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$'
    $related = @(Get-MiosMsiRelatedProducts -UpgradeCode $UpgradeCode)
    $unknown = @($related | Where-Object { $_.AssignmentType -notin @('0', '1') -or $_.ProductCode -notmatch $guid })
    if ($unknown.Count) { throw "A Podman product of the pinned family has an unexpected Windows Installer registration ($(@($unknown | ForEach-Object { "$($_.ProductCode) assignment '$($_.AssignmentType)'" }) -join ', ')); nothing was removed and the pinned Podman MSI was not installed." }
    $perUser = @($related | Where-Object { $_.AssignmentType -eq '0' })
    $removed = 0
    foreach ($p in $perUser) {
        Write-Info "Removing the per-user Podman $($p.Version) [$($p.ProductCode)] of this elevated account; MiOS installs Podman per-machine."
        $code = Invoke-MiosPodmanRemoval -FilePath (Join-Path $env:SystemRoot 'System32\msiexec.exe') -Arguments @('/x', $p.ProductCode, '/qn', '/norestart')
        if ($code -notin @(0, 3010)) { throw "Removing the per-user Podman $($p.Version) [$($p.ProductCode)] failed (exit $code); the pinned Podman MSI was not installed." }
        $removed++
    }
    $left = @(@(Get-MiosMsiRelatedProducts -UpgradeCode $UpgradeCode) | Where-Object { $_.AssignmentType -ne '1' })
    if ($left.Count) { throw "A per-user Podman is still installed for this elevated account after removal ($(@($left | ForEach-Object { "$($_.Version) [$($_.ProductCode)]" }) -join ', ')); the pinned Podman MSI was not installed." }
    return $removed
}

function Read-MiosPodmanUninstallEntries {
    # Machine-wide uninstall registrations (64-bit and WOW6432Node views) read as data; no registry command text is ever run.
    $entries = New-Object System.Collections.Generic.List[object]
    foreach ($root in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $p = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            if (-not $p -or [string]$p.DisplayName -notmatch 'podman') { continue }
            $entries.Add([pscustomobject]@{ Root = $root; Key = [string]$key.PSChildName; DisplayName = [string]$p.DisplayName; Publisher = [string]$p.Publisher
                DisplayVersion = [string]$p.DisplayVersion; WindowsInstaller = [string]$p.WindowsInstaller; BundleCachePath = [string]$p.BundleCachePath
                BundleProviderKey = [string]$p.BundleProviderKey; QuietUninstallString = [string]$p.QuietUninstallString })
        }
    }
    return $entries.ToArray()
}

function Get-MiosOlderPodmanEntries {
    # Only an entry named exactly 'Podman' from Red Hat whose version is older than machine-os major.minor. 'Podman Desktop',
    # other publishers and current versions never match.
    param([Parameter(Mandatory)][Version]$Required)
    $publishers = @('Red Hat', 'Red Hat Inc.', 'Red Hat, LLC')
    return @(@(Read-MiosPodmanUninstallEntries) | Where-Object {
        $_.DisplayName -ieq 'Podman' -and $_.Publisher -cin $publishers -and $_.DisplayVersion -match '^(\d+)\.(\d+)' -and
        (New-Object Version ([int]$Matches[1]), ([int]$Matches[2])) -lt $Required })
}

function Get-MiosPodmanRemovalPlan {
    # Every older entry is validated before anything is removed: a setup bundle whose cached exe lies under
    # %ProgramData%\Package Cache and carries the SSOT signer (run with fixed arguments), or a Windows Installer product
    # addressed only by its GUID key. Any other shape refuses; registry argument text is data, never executed.
    param([Parameter(Mandatory)][object[]]$Entries, [Parameter(Mandatory)][string]$Signer)
    $cacheRoot = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'Package Cache')).TrimEnd('\') + '\'
    $plan = New-Object System.Collections.Generic.List[object]
    foreach ($e in $Entries) {
        $label = "$($e.DisplayName) $($e.DisplayVersion) [$($e.Key)]"
        if ($e.BundleCachePath -or $e.BundleProviderKey) {
            $exe = [string]$e.BundleCachePath
            if (-not $exe -or -not [IO.Path]::IsPathRooted($exe) -or $exe -notmatch '\.exe$') { throw "The Podman bundle $label has no absolute cached bundle exe; nothing was removed." }
            $full = [IO.Path]::GetFullPath($exe)
            if ($full -ine $exe -or -not $full.StartsWith($cacheRoot, [StringComparison]::OrdinalIgnoreCase)) { throw "The Podman bundle $label caches its exe outside $cacheRoot; nothing was removed." }
            $quiet = ([string]$e.QuietUninstallString).Trim()
            if ($quiet -and $quiet -ine ('"' + $exe + '" /uninstall /quiet')) { throw "The Podman bundle $label has an unexpected uninstall command; nothing was removed." }
            if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "The Podman bundle $label cached exe is missing; nothing was removed." }
            $signature = Get-AuthenticodeSignature -LiteralPath $full
            if ([string]$signature.Status -ne 'Valid') { throw "The Podman bundle $label exe has a $($signature.Status) signature, not Valid; nothing was removed." }
            $subject = if ($signature.SignerCertificate) { [string]$signature.SignerCertificate.Subject } else { '' }
            if ($subject -cne $Signer) { throw "The Podman bundle $label exe is signed by '$subject', not the SSOT signer; nothing was removed." }
            $plan.Add([pscustomobject]@{ Order = 0; Kind = 'bundle'; Label = $label; Key = $e.Key; FilePath = $full; Arguments = @('/uninstall', '/quiet', '/norestart') })
        } elseif ($e.WindowsInstaller -eq '1' -and $e.Key -match '^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$') {
            $plan.Add([pscustomobject]@{ Order = 1; Kind = 'msi'; Label = $label; Key = $e.Key; FilePath = (Join-Path $env:SystemRoot 'System32\msiexec.exe'); Arguments = @('/x', $e.Key, '/qn', '/norestart') })
        } else { throw "The Podman entry $label is neither a cached setup bundle nor a Windows Installer product; nothing was removed." }
    }
    return @($plan | Sort-Object Order)
}

function Invoke-MiosPodmanRemoval {
    param([Parameter(Mandatory)][string]$FilePath, [Parameter(Mandatory)][string[]]$Arguments)
    $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden -ErrorAction Stop
    return [int]$proc.ExitCode
}

function Remove-MiosOlderPodman {
    # The pinned MSI cannot upgrade an older Podman in place (its installer refuses with 1603), so the older Red Hat
    # registrations go first: the setup bundle, then any Windows Installer product still registered. The vendor preserves
    # containers, images, machines and data on uninstall; no podman command, user directory or policy is touched here.
    param([Parameter(Mandatory)][Version]$Required, [Parameter(Mandatory)][string]$Signer)
    $older = @(Get-MiosOlderPodmanEntries -Required $Required)
    if (-not $older.Count) { return 0 }
    $plan = @(Get-MiosPodmanRemovalPlan -Entries $older -Signer $Signer)
    $removed = 0
    foreach ($step in $plan) {
        # A bundle normally removes its own Windows Installer product; only what is still registered is removed here.
        if ($step.Kind -eq 'msi' -and -not @(@(Get-MiosOlderPodmanEntries -Required $Required) | Where-Object { $_.Key -eq $step.Key }).Count) { continue }
        Write-Info "Removing $($step.Label) ($($step.Kind)); the vendor preserves containers, images, machines and data."
        $code = Invoke-MiosPodmanRemoval -FilePath $step.FilePath -Arguments $step.Arguments
        if ($code -notin @(0, 3010)) { throw "Removing $($step.Label) failed (exit $code); the pinned Podman MSI was not installed." }
        $removed++
    }
    $left = @(Get-MiosOlderPodmanEntries -Required $Required)
    if ($left.Count) { throw "An older Red Hat Podman is still registered after removal ($(@($left | ForEach-Object { "$($_.DisplayName) $($_.DisplayVersion) [$($_.Key)]" }) -join ', ')); the pinned Podman MSI was not installed." }
    return $removed
}

function Install-MiosPodmanClient {
    # Elevated machine preparation (the operator's UAC): bring the Podman client up to the machine-os version MiOS-DEV needs.
    # Only the system SSOT pin is trusted: the MSI must match its sha256, carry a Valid Authenticode signature and the pinned
    # signer, all checked before msiexec runs. Any mismatch refuses with a message and installs nothing.
    param([string]$DownloadDirectory)
    $required = Get-MiosRequiredPodmanVersion
    $url = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_cli_msi_url' -Default '' -VendorOnly)
    $sha = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_cli_msi_sha256' -Default '' -VendorOnly)
    $signer = [string](Get-MiosTomlValue -Section 'bootstrap.prereqs' -Key 'podman_cli_msi_signer' -Default '' -VendorOnly)
    # The signer also gates running the installed client elevated, so it is required before any version check.
    if (-not $signer.Trim()) {
        throw "The system SSOT has no complete pinned Podman MSI ([bootstrap.prereqs] podman_cli_msi_url, podman_cli_msi_sha256, podman_cli_msi_signer); the machine Podman client cannot be verified before it runs elevated. Nothing was installed."
    }
    $installed = Get-MiosMachinePodmanVersion -Signer $signer
    $have = if ($installed) { "Podman $installed" } else { 'no Podman client' }
    if ($installed -and (New-Object Version $installed.Major, $installed.Minor) -ge $required) {
        Write-Good "Podman client $installed satisfies machine-os $required"
        return 'current'
    }
    if ($url -notmatch '^https://[^\s"'']+\.msi$' -or $sha -cnotmatch '^[0-9a-f]{64}$') {
        throw "Found $have; MiOS-DEV needs Podman $required or newer, and the system SSOT has no complete pinned Podman MSI ([bootstrap.prereqs] podman_cli_msi_url, podman_cli_msi_sha256, podman_cli_msi_signer). Nothing was installed."
    }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw "Found $have; the pinned Podman MSI is for amd64, not $($env:PROCESSOR_ARCHITECTURE). Nothing was installed." }
    if (-not $DownloadDirectory) { $DownloadDirectory = Join-Path $env:ProgramData 'MiOS\downloads' }
    if (Test-Path -LiteralPath $DownloadDirectory) { Assert-MiosMachineWriteBoundary $DownloadDirectory } else { New-MiosProtectedMachineDirectory $DownloadDirectory }
    Write-Info "Found $have; installing the pinned Podman MSI for machine-os $required ..."
    $bytes = Get-MiosPinnedDownloadBytes -Uri $url
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $actual = ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() } finally { $hasher.Dispose() }
    if ($actual -cne $sha) { throw "The pinned Podman MSI sha256 is $actual, not the SSOT $sha. Nothing was installed." }
    $msi = Join-Path $DownloadDirectory ('podman-' + $sha.Substring(0, 16) + '-' + [guid]::NewGuid().ToString('N') + '.msi')
    Write-MiosPrivateMachineFile -Path $msi -Bytes $bytes
    try {
        $signature = Get-AuthenticodeSignature -LiteralPath $msi
        if ([string]$signature.Status -ne 'Valid') { throw "The pinned Podman MSI signature is $($signature.Status), not Valid. Nothing was installed." }
        $subject = if ($signature.SignerCertificate) { [string]$signature.SignerCertificate.Subject } else { '' }
        if ($subject -cne $signer) { throw "The pinned Podman MSI is signed by '$subject', not the SSOT signer '$signer'. Nothing was installed." }
        # The verified package's own identity; its UpgradeCode names the family whose per-user installs are removed below.
        $package = Get-MiosMsiPackageInfo -Path $msi
        $guid = '^\{[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}$'
        if ($package.ProductCode -notmatch $guid -or $package.UpgradeCode -notmatch $guid) { throw "The verified Podman MSI has no valid ProductCode/UpgradeCode ('$($package.ProductCode)', '$($package.UpgradeCode)'). Nothing was installed." }
        Write-Info "Verified Podman MSI: ProductCode $($package.ProductCode), UpgradeCode $($package.UpgradeCode), Manufacturer '$($package.Manufacturer)'."
        # Only with a verified replacement in hand: remove an older Red Hat Podman the MSI cannot upgrade, then re-check.
        $removed = Remove-MiosOlderPodman -Required $required -Signer $signer
        # Re-read the machine client after a removal: anything left at the machine path must still carry the SSOT signer.
        if ($removed) { $null = Get-MiosMachinePodmanVersion -Signer $signer }
        $removed += Remove-MiosPerUserPodman -UpgradeCode $package.UpgradeCode
        # The package is dual-purpose (ALLUSERS=2, MSIINSTALLPERUSER=1): without these it installs per-user for whichever
        # account approved the UAC prompt. ALLUSERS=1 makes it per-machine, and MSIINSTALLPERUSER=0 (not "", which Windows
        # PowerShell 5.1 would strip) selects its machine-PATH component instead of the per-user one.
        $code = Invoke-MiosMsiInstall -Path $msi -Properties @('ALLUSERS=1', 'MSIINSTALLPERUSER=0')
        if ($code -notin @(0, 3010)) {
            $state = if ($removed) { "the older Podman was already removed ($removed registration(s))" } else { "$have is unchanged" }
            throw "msiexec could not install the verified Podman MSI (exit $code); $state."
        }
    } finally { Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue }
    # Windows Installer itself must report the verified product per-machine.
    $scope = @(@(Get-MiosMsiRelatedProducts -UpgradeCode $package.UpgradeCode) | Where-Object { $_.ProductCode -eq $package.ProductCode })
    if (-not @($scope | Where-Object { $_.AssignmentType -eq '1' }).Count) {
        throw "The verified Podman MSI $($package.ProductCode) installed per-user for this elevated account, not per-machine; the operator account cannot use it."
    }
    $after = Get-MiosMachinePodmanVersion -Signer $signer
    if (-not $after) {
        throw "After installing the verified MSI, no Podman client was found at $(@(Get-MiosMachinePodmanPaths) -join ' or '); the elevated stage trusts only those per-machine paths."
    }
    if ((New-Object Version $after.Major, $after.Minor) -lt $required) {
        throw "After installing the verified MSI, Podman $after is still older than machine-os $required."
    }
    # The package's machine-PATH component puts its INSTALLDIR on the machine PATH; the user side also finds the exe directly.
    $installDir = Split-Path -Parent @(Get-MiosMachinePodmanPaths)[0]
    if (-not @(@((Get-MiosMachinePath) -split ';') | Where-Object { $_.Trim().TrimEnd('\') -ieq $installDir }).Count) {
        Write-Info "Warning: the machine PATH does not list $installDir; the operator side finds $(@(Get-MiosMachinePodmanPaths)[0]) directly."
    }
    Write-Good "Podman client $after installed per-machine from the verified MSI"
    return 'installed'
}

function Ensure-PodmanDesktop {
    # An elevated machine preparation may have just installed the pinned client; see it before deciding anything.
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User') + ';' + $env:PATH
    if (Get-Command podman -ErrorAction SilentlyContinue) {
        Write-Good "Podman already installed ($((podman --version) 2>&1))"
        return
    }
    # A per-machine client the elevated stage installed is used even if the machine PATH has not picked it up yet; its
    # directory goes first on this session's PATH so build-mios and its guard resolve the same exe. No winget run over it.
    $machineClient = Resolve-MiosPodmanExe
    if ($machineClient) {
        $env:PATH = (Split-Path -Parent $machineClient) + ';' + $env:PATH
        Write-Good "Podman already installed at $machineClient"
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

    # No unverified download: the only MSI path is the pinned, hash- and signature-checked install in elevated machine preparation.
    if (-not (Get-Command podman -ErrorAction SilentlyContinue) -and -not (Resolve-MiosPodmanExe)) {
        Write-Err "No Podman client is installed. The elevated machine preparation installs the pinned Podman MSI from the system SSOT"
        Write-Err "  ([bootstrap.prereqs] podman_cli_msi_url/sha256/signer); re-run the installer and approve its UAC prompt."
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
            (Join-Path ${env:ProgramFiles}      'Podman'),
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
        [string]$DriveLetter = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default 'C'),
        [string]$VolumeLabel = $(Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'volume_label' -Default 'MIOS-DEV')
    )
    $sysLetter = ([Environment]::GetEnvironmentVariable('SystemDrive')).TrimEnd(':')
    if (-not $sysLetter) { $sysLetter = 'C' }
    if ($DriveLetter -eq $sysLetter) {
        Write-Good "${DriveLetter}:\ matches system drive (${sysLetter}:) -- converged root layout active; skipping partition resize."
        return
    }
    $existing = Get-Volume -DriveLetter $DriveLetter -ErrorAction SilentlyContinue
    if ($existing -and $existing.FileSystemLabel -eq $VolumeLabel) {
        Write-Good "${DriveLetter}:\ already provisioned ($([math]::Round($existing.Size/1GB,1)) GB, $($existing.FileSystem), label=$VolumeLabel)"
        return
    }
    if ($existing) {
        Write-Err "Drive ${DriveLetter}: exists with label '$($existing.FileSystemLabel)' (not '$VolumeLabel')."
        Write-Err "Either remove the volume manually or pass -DriveLetter <other> to Get-MiOS.ps1."
        throw "Initialize-DataDisk: drive ${DriveLetter}: already exists with another label; no disk changes were made."
    }
    $_displayGb = Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'display_size_gb' -Default 256
    Write-Info "Provisioning ${DriveLetter}:\ at $ShrinkMB MB (target $_displayGb GB visible in Explorer) ..."
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
        throw "Initialize-DataDisk: disk $($disk.Number) has an unsupported partition style; no disk changes were made."
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
    param([string]$MRoot = $(if ($env:SystemDrive) { "$($env:SystemDrive)\podman\machine" } else { 'C:\podman\machine' }),
          [string]$UserProfile = $env:USERPROFILE, [string]$LocalAppData = $env:LOCALAPPDATA)
    if (-not (Test-Path $MRoot)) {
        New-Item -ItemType Directory -Path $MRoot -Force -ErrorAction Stop | Out-Null
        Write-Host "    [+] created $MRoot" -ForegroundColor DarkGray
    }
    $candidates = @(
        (Join-Path $LocalAppData  'containers\podman\machine'),
        (Join-Path $UserProfile   '.local\share\containers\podman\machine'),
        (Join-Path $env:PROGRAMDATA   'containers\podman\machine')
    )
    foreach ($p in $candidates) {
        if (-not $p -or $p -ieq $MRoot) { continue }
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
    param([string]$MRoot = $(if ($env:SystemDrive) { "$($env:SystemDrive)\winget" } else { 'C:\winget' }),
          [string]$LocalAppData = $env:LOCALAPPDATA)
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
        @{ Src = (Join-Path $LocalAppData 'Microsoft\WinGet\Packages');             Dst = (Join-Path $MRoot 'Packages')             },
        @{ Src = (Join-Path $LocalAppData 'Microsoft\WinGet\Cache');                Dst = (Join-Path $MRoot 'Cache')                },
        @{ Src = (Join-Path $LocalAppData 'Microsoft\WinGet\Links');                Dst = (Join-Path $MRoot 'PortableLinks')        },
        @{ Src = (Join-Path $LocalAppData 'Microsoft\WinGet\Portable\PackagesRoot'); Dst = (Join-Path $MRoot 'PortablePackagesRoot') },
        # Machine-scope winget retry (used when --scope user fails -- e.g.
        # Microsoft.PowerShell, .NET runtimes, system-wide tools) lands
        # in %ProgramFiles%\WinGet\Packages.
        @{ Src = (Join-Path $_pf 'WinGet\Packages');                                    Dst = (Join-Path $MRoot 'MachinePackages')      }
    )
    foreach ($c in $candidates) {
        $p = $c.Src; $tgt = $c.Dst
        if (-not $p -or $p -ieq $tgt) { continue }
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

# mios-boot-win-04: Defender exclusions exist only where the SSOT declares them, narrowly and with a reason:
#   [[security.defender_exclusions.path]]     path = '...'  reason = '...'
#   [[security.defender_exclusions.process]]  path = '...'  reason = '...'
# No code default. Refused even when declared: a drive root, %TEMP%/%TMP% (machine or user) and anything under
# them, a user profile root and the AppData roots -- places any local user or download can write to.
function ConvertTo-MiosWindowsPath {
    param([Parameter(Mandatory)][string]$Path)
    $p = [Environment]::ExpandEnvironmentVariables($Path.Trim()).Replace('/', '\')
    if ($p -notmatch '^[A-Za-z]:\\' -or $p -match '[*?%]' -or $p.Substring(2).Contains(':')) {
        throw 'The exclusion must be an absolute local drive path without wildcards or unresolved variables.'
    }
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($part in ($p.Substring(3) -split '\\+')) {
        if (-not $part -or $part -eq '.') { continue }
        if ($part -eq '..') {
            if ($parts.Count) { $parts.RemoveAt($parts.Count - 1) }
            continue
        }
        if ($part -match '[. ]$' -or $part -match '[\x00-\x1f<>"|]') { throw 'Ambiguous Windows path component.' }
        $parts.Add($part)
    }
    return $p.Substring(0, 1).ToUpperInvariant() + ':\' + ($parts -join '\')
}

function Test-MiosDefenderExclusionRefused {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return 'empty path' }
    try { $p = ConvertTo-MiosWindowsPath $Path } catch { return 'an invalid or ambiguous Windows path' }
    if ($p -match '^[A-Za-z]:\\$') { return 'a whole drive' }
    $wide = @($env:TEMP, $env:TMP, [Environment]::GetEnvironmentVariable('TEMP', 'Machine'),
              [Environment]::GetEnvironmentVariable('TMP', 'Machine'))
    if ($env:SystemRoot) { $wide += $env:SystemRoot + '\Temp' }
    foreach ($w in $wide) {
        if (-not $w) { continue }
        try { $root = ConvertTo-MiosWindowsPath $w } catch { continue }
        if ($p -ieq $root -or $p.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return 'a temp directory' }
    }
    foreach ($r in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $script:MiosMachineOrigin.Profile, $script:MiosMachineOrigin.LocalAppData)) {
        if (-not $r) { continue }
        try { $root = ConvertTo-MiosWindowsPath $r } catch { continue }
        if ($p -ieq $root -or $p.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return 'a profile or AppData directory' }
    }
    if ($env:USERPROFILE) {
        try {
            $profile = ConvertTo-MiosWindowsPath $env:USERPROFILE
            $users = $profile.Substring(0, $profile.LastIndexOf('\'))
            if ($p -ieq $users) { return 'the profiles directory' }
        } catch { return 'an unresolved profile boundary' }
    }
    return $null
}

function Get-MiosDefenderExclusionPlan {
    $plan = @{ paths = @(); processes = @(); refused = @() }
    foreach ($kind in 'path', 'process') {
        foreach ($row in @(Get-MiosTomlTableArray "security.defender_exclusions.$kind")) {
            $path = [string]$row['path']; $why = [string]$row['reason']
            if ([string]::IsNullOrWhiteSpace($why)) { $plan.refused += "$kind entry: no reason declared"; continue }
            $bad = Test-MiosDefenderExclusionRefused $path
            if ($bad) { $plan.refused += "$kind entry: $bad"; continue }
            $canonical = ConvertTo-MiosWindowsPath $path
            if ($env:OS -eq 'Windows_NT') { Assert-MiosNoReparsePath $canonical }
            if ($kind -eq 'path') { $plan.paths += $canonical } else { $plan.processes += $canonical }
        }
    }
    return $plan
}

function Add-MiosDefenderExclusions {
    if (-not (Get-Command Add-MpPreference -ErrorAction SilentlyContinue)) { return }
    # Remove only the exact signatures this bootstrap historically installed. Do not sweep administrator policy.
    $systemDrive = if ($env:SystemDrive) { $env:SystemDrive } else { 'C:' }
    $legacyPaths = @(($systemDrive + '\MiOS'), ($systemDrive + '\MiOS\bin'), ($systemDrive + '\usr'),
                     ($systemDrive + '\etc\mios'), 'M:\', 'M:\MiOS')
    if ($env:LOCALAPPDATA) { $legacyPaths += $env:LOCALAPPDATA + '\Microsoft\WinGet' }
    foreach ($temp in @($env:TEMP, $env:TMP)) {
        if ($temp) { $legacyPaths += ConvertTo-MiosWindowsPath $temp }
    }
    $legacyProcesses = @(($systemDrive + '\MiOS\bin\mios-launch.exe'), ($systemDrive + '\MiOS\bin\fastfetch.exe'),
                         ($systemDrive + '\MiOS\bin\btop.exe'), 'M:\MiOS\bin\mios-launch.exe')
    $preferences = Get-MpPreference -ErrorAction Stop
    foreach ($current in @($preferences.ExclusionPath)) {
        if (-not $current) { continue }
        try { $canonical = if ($current -match '^[A-Za-z]:$') { ConvertTo-MiosWindowsPath ($current + '\') } else { ConvertTo-MiosWindowsPath $current } } catch { continue }
        if ($legacyPaths -contains $canonical) { Remove-MpPreference -ExclusionPath $current -ErrorAction Stop }
    }
    foreach ($current in @($preferences.ExclusionProcess)) {
        if (-not $current) { continue }
        try { $canonical = ConvertTo-MiosWindowsPath $current } catch { continue }
        if ($legacyProcesses -contains $canonical) { Remove-MpPreference -ExclusionProcess $current -ErrorAction Stop }
    }
    $plan = Get-MiosDefenderExclusionPlan
    foreach ($r in $plan.refused) { Write-Host "  [!] Defender exclusion refused ($r)" -ForegroundColor Yellow }
    foreach ($p in $plan.paths) { Add-MpPreference -ExclusionPath $p -ErrorAction Stop }
    foreach ($p in $plan.processes) { Add-MpPreference -ExclusionProcess $p -ErrorAction Stop }
}

function Test-MiosWslVmCreator([string]$Creator) {
    foreach ($c in @(Get-NetFirewallHyperVVMCreator -ErrorAction Stop)) {
        $id = [guid]::Empty
        if ([guid]::TryParse([string]$c.VMCreatorId, [ref]$id) -and $id -eq [guid]$Creator) { return $true }
    }
    return $false
}

function Register-MiosWslVmCreator([string]$Creator) {
    # The call WSL itself makes (microsoft/WSL WslCoreFirewallSupport.cpp, FriendlyName "WSL"); no VM is created.
    $alreadyExists = [uint32]2147749913   # 0x80041019 WBEM_E_ALREADY_EXISTS
    try {
        $rv = [uint32](Invoke-CimMethod -Namespace 'root/standardcimv2' -ClassName 'MSFT_NetFirewallHyperVVMCreator' -MethodName 'RegisterHyperVVMCreator' `
            -Arguments @{ VMCreatorId = $Creator; FriendlyName = 'WSL' } -ErrorAction Stop).ReturnValue
    } catch {
        $native = if ($_.Exception.PSObject.Properties['NativeErrorCode']) { [string]$_.Exception.NativeErrorCode } else { '' }
        if ($native -ne 'AlreadyExists') { throw "Registering the WSL firewall creator failed: $($_.Exception.Message)" }
        $rv = $alreadyExists
    }
    if ($rv -ne 0 -and $rv -ne $alreadyExists) { throw "Registering the WSL firewall creator returned $rv; provisioning stopped before MiOS-DEV." }
    # Success, or a concurrent registration, counts only once the creator record is visible.
    foreach ($attempt in 1..5) {
        if (Test-MiosWslVmCreator $Creator) { return }
        Start-Sleep -Seconds 1
    }
    throw 'The WSL firewall creator did not appear after registration; provisioning stopped before MiOS-DEV.'
}

function Get-MiosTomlStringArray {
    # A TOML array of quoted strings (single- or multi-line, comments allowed) from the layered TOML. The first layer
    # that declares the key wins, an empty array included. Returns $null when no layer declares it. -VendorOnly reads the
    # system SSOT alone: the trust anchor an elevated step bounds the operator layer with.
    param([Parameter(Mandatory)][string]$Section, [Parameter(Mandatory)][string]$Key, [switch]$VendorOnly)
    $layers = if ($VendorOnly) { @((Resolve-MiosVendorTomlText)) } else { @((Resolve-MiosTomlText), (Resolve-MiosVendorTomlText)) }
    foreach ($txt in $layers) {
        if (-not $txt) { continue }
        $mSec = [regex]::Match($txt, '(?ms)^\[' + [regex]::Escape($Section) + '\][ \t]*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
        if (-not $mSec.Success) { continue }
        $body = $mSec.Groups['body'].Value
        $mKey = [regex]::Match($body, '(?m)^[ \t]*' + [regex]::Escape($Key) + '[ \t]*=[ \t]*\[')
        if (-not $mKey.Success) { continue }
        $items = New-Object System.Collections.Generic.List[string]
        $i = $mKey.Index + $mKey.Length; $closed = $false
        while ($i -lt $body.Length) {
            $c = $body[$i]
            if ($c -eq ']') { $closed = $true; break }
            if ($c -eq '#') { while ($i -lt $body.Length -and $body[$i] -ne "`n") { $i++ }; continue }
            if ($c -eq '"' -or $c -eq "'") {
                $end = $body.IndexOf($c, $i + 1)
                if ($end -lt 0) { break }
                $value = $body.Substring($i + 1, $end - $i - 1)
                if ($value.Contains('\') -or $value.Contains("`n")) { throw "Unsupported string in mios.toml [$Section].$Key." }
                $items.Add($value); $i = $end + 1; continue
            }
            if ($c -eq ',' -or [char]::IsWhiteSpace($c)) { $i++; continue }
            throw "mios.toml [$Section].$Key must be an array of quoted strings."
        }
        if (-not $closed) { throw "mios.toml [$Section].$Key is an unterminated array." }
        return , $items.ToArray()
    }
    return $null
}

function Get-MiosVendorBoundedList {
    # A [firewall.windows] list for an elevated step: the system SSOT declares the most it may open, and the operator layer
    # can only narrow it. Operator entries the system SSOT does not list are refused, never added.
    param([Parameter(Mandatory)][string]$Section, [Parameter(Mandatory)][string]$Key)
    $vendor = Get-MiosTomlStringArray -Section $Section -Key $Key -VendorOnly
    if ($null -eq $vendor) { return [pscustomobject]@{ Values = @(); Refused = @("mios.toml [$Section].$Key is not declared by the system SSOT") } }
    $effective = Get-MiosTomlStringArray -Section $Section -Key $Key
    $values = @(); $refused = @()
    foreach ($item in @($effective)) {
        if (@($vendor | Where-Object { $_ -ieq $item }).Count) { if (@($values | Where-Object { $_ -ieq $item }).Count -eq 0) { $values += $item } }
        else { $refused += "operator [$Section].$Key entry '$item' is outside the system SSOT" }
    }
    return [pscustomobject]@{ Values = $values; Refused = $refused }
}

function Get-MiosWslServicePortPlan {
    # MiOS-DEV's service ports, from SSOT only: each [firewall.windows].expose key resolved through [ports].<key>, shifted
    # by [ports].stack_id * 10000. [firewall].open_ports is the guest firewalld list (it carries pgvector) and is never read
    # here. Port numbers and stack_id come from the system SSOT alone, so the user-writable layer cannot point an exposed
    # key at another listener. A key without a valid [ports] value opens nothing and is reported as refused.
    $plan = [pscustomobject]@{ Ports = [ordered]@{}; Refused = @() }
    $ports = [ordered]@{}
    $list = Get-MiosVendorBoundedList -Section 'firewall.windows' -Key 'expose'
    $plan.Refused += $list.Refused
    $stack = Get-MiosTomlValue -Section 'ports' -Key 'stack_id' -Default 0 -VendorOnly
    if ($stack -isnot [int] -or $stack -lt 0) { throw 'mios.toml [ports].stack_id must be a nonnegative integer.' }
    $seen = @{}
    foreach ($key in $list.Values) {
        if ($key -notmatch '^[a-z][a-z0-9_]*$') { $plan.Refused += "expose entry '$key' is not a [ports] key name"; continue }
        if ($ports.Contains($key)) { continue }
        $base = Get-MiosTomlValue -Section 'ports' -Key $key -Default 0 -VendorOnly
        if ($base -isnot [int] -or $base -lt 1 -or $base -gt 65535) { $plan.Refused += "$key has no valid [ports].$key value"; continue }
        $port = $base + ($stack * 10000)
        if ($port -gt 65535) { $plan.Refused += "$key resolves to $port with stack_id $stack, above 65535"; continue }
        if ($seen.ContainsKey($port)) { $plan.Refused += "$key repeats port $port of $($seen[$port])"; continue }
        $seen[$port] = $key
        $ports[$key] = $port
    }
    $plan.Ports = $ports
    return $plan
}

function ConvertTo-MiosNetworkPrefix {
    # The network address of IP/PrefixLength in CIDR form (192.168.1.37/24 -> 192.168.1.0/24).
    param([Parameter(Mandatory)][Net.IPAddress]$Address, [Parameter(Mandatory)][int]$PrefixLength)
    $bytes = $Address.GetAddressBytes()
    if ($PrefixLength -lt 1 -or $PrefixLength -gt ($bytes.Length * 8)) { throw 'Invalid prefix length.' }
    for ($b = 0; $b -lt $bytes.Length; $b++) {
        $keep = [math]::Min(8, [math]::Max(0, $PrefixLength - ($b * 8)))
        $bytes[$b] = $bytes[$b] -band [byte]((0xFF -shl (8 - $keep)) -band 0xFF)
    }
    return ([Net.IPAddress]::new($bytes)).ToString() + '/' + $PrefixLength
}

function ConvertTo-MiosCanonicalPrefix {
    # One rule address in the form this script writes (network/length), whatever form Windows reads it back in:
    # a bare address (a host prefix), address/length, or IPv4 address/dotted-mask. Anything else is returned as written.
    param([AllowEmptyString()][string]$Text)
    $t = $Text.Trim()
    $ipText = $t; $len = $null
    if ($t -match '^([^/]+)/(.+)$') {
        $ipText = $Matches[1]; $tail = $Matches[2]
        if ($tail -match '^[0-9]{1,3}$') { $len = [int]$tail }
        else {
            $mask = $null
            if (-not [Net.IPAddress]::TryParse($tail, [ref]$mask) -or $mask.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { return $t }
            $bits = ''
            foreach ($byte in $mask.GetAddressBytes()) { $bits += [Convert]::ToString($byte, 2).PadLeft(8, '0') }
            if ($bits -notmatch '^1*0*$') { return $t }
            $len = $bits.IndexOf('0'); if ($len -lt 0) { $len = 32 }
        }
    }
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($ipText, [ref]$ip)) { return $t }
    $bitsMax = if ($ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) { 32 } else { 128 }
    if ($null -eq $len) { $len = $bitsMax }
    if ($len -lt 1 -or $len -gt $bitsMax) { return $t }
    return ConvertTo-MiosNetworkPrefix -Address $ip -PrefixLength $len
}

function Test-MiosPrefixWithin {
    # True when CIDR Inner lies wholly inside CIDR Outer (same family, at least as long a prefix, same network bits).
    param([Parameter(Mandatory)][string]$Inner, [Parameter(Mandatory)][string]$Outer)
    if ($Inner -notmatch '^(.+)/([0-9]{1,3})$') { return $false }; $iIp = [Net.IPAddress]::Parse($Matches[1]); $iLen = [int]$Matches[2]
    if ($Outer -notmatch '^(.+)/([0-9]{1,3})$') { return $false }; $oIp = [Net.IPAddress]::Parse($Matches[1]); $oLen = [int]$Matches[2]
    if ($iIp.AddressFamily -ne $oIp.AddressFamily -or $iLen -lt $oLen) { return $false }
    return ((ConvertTo-MiosNetworkPrefix -Address $iIp -PrefixLength $oLen) -eq (ConvertTo-MiosNetworkPrefix -Address $oIp -PrefixLength $oLen))
}

function Resolve-MiosWslRemoteScope {
    # SSOT remote_addresses to the explicit prefixes a Hyper-V rule accepts. Hyper-V rules take only addresses, subnets
    # and ranges (MSFT_NetFirewallHyperVRule.RemoteAddresses), not the LocalSubnet keyword host rules take, so LocalSubnet
    # becomes the subnets of this host's preferred unicast addresses, loopback and link-local excluded.
    # Anything unscoped (Any, /0, 0.0.0.0, ::) is refused, never widened.
    param([AllowEmptyCollection()][string[]]$Entries)
    $scope = New-Object System.Collections.Generic.List[string]
    $refused = @()
    foreach ($raw in @($Entries)) {
        $entry = ([string]$raw).Trim()
        if ($entry -ieq 'LocalSubnet') {
            if (-not (Get-Command Get-NetIPAddress -ErrorAction SilentlyContinue)) { $refused += 'LocalSubnet: Get-NetIPAddress is unavailable'; continue }
            $found = 0
            foreach ($a in @(Get-NetIPAddress -AddressState Preferred -ErrorAction Stop)) {
                $ip = $null
                if (-not [Net.IPAddress]::TryParse(([string]$a.IPAddress -replace '%.*$', ''), [ref]$ip)) { continue }
                if ([Net.IPAddress]::IsLoopback($ip) -or $ip.IsIPv6LinkLocal) { continue }
                if ($ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and $ip.GetAddressBytes()[0] -eq 169 -and $ip.GetAddressBytes()[1] -eq 254) { continue }
                $len = [int]$a.PrefixLength
                if ($len -lt 1) { continue }
                $cidr = ConvertTo-MiosNetworkPrefix -Address $ip -PrefixLength $len
                if (-not $scope.Contains($cidr)) { $scope.Add($cidr) }
                $found++
            }
            if (-not $found) { $refused += 'LocalSubnet: no preferred unicast address on this host' }
            continue
        }
        $ipText = $entry; $prefix = $null
        if ($entry -match '^([^/]+)/([0-9]{1,3})$') { $ipText = $Matches[1]; $prefix = [int]$Matches[2] }
        $ip = $null
        if (-not [Net.IPAddress]::TryParse($ipText, [ref]$ip)) { $refused += "'$entry' is not an address, CIDR or LocalSubnet"; continue }
        $bits = if ($ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) { 32 } else { 128 }
        if ($null -eq $prefix) { $prefix = $bits }
        if ($prefix -lt 1 -or $prefix -gt $bits -or $ip.Equals([Net.IPAddress]::Any) -or $ip.Equals([Net.IPAddress]::IPv6Any)) {
            $refused += "'$entry' is unscoped"; continue
        }
        $cidr = ConvertTo-MiosNetworkPrefix -Address $ip -PrefixLength $prefix
        if (-not $scope.Contains($cidr)) { $scope.Add($cidr) }
    }
    return [pscustomobject]@{ Addresses = @($scope | Sort-Object); Refused = $refused }
}

function Resolve-MiosWslServiceScope {
    # The [firewall.windows].remote_addresses scope for the elevated Hyper-V rules. The system SSOT bounds it: an
    # operator prefix is kept only when it lies inside a prefix the system SSOT resolves to, so the user-writable layer
    # can narrow the scope but never widen it.
    $vendor = Get-MiosTomlStringArray -Section 'firewall.windows' -Key 'remote_addresses' -VendorOnly
    if ($null -eq $vendor) { return [pscustomobject]@{ Addresses = @(); Refused = @('mios.toml [firewall.windows].remote_addresses is not declared by the system SSOT') } }
    $bound = Resolve-MiosWslRemoteScope -Entries $vendor
    $effective = Get-MiosTomlStringArray -Section 'firewall.windows' -Key 'remote_addresses'
    $scope = Resolve-MiosWslRemoteScope -Entries @($effective)
    $kept = @(); $refused = @($scope.Refused)
    foreach ($cidr in $scope.Addresses) {
        if (@($bound.Addresses | Where-Object { Test-MiosPrefixWithin -Inner $cidr -Outer $_ }).Count) { $kept += $cidr }
        else { $refused += "operator scope '$cidr' is outside the system SSOT scope" }
    }
    return [pscustomobject]@{ Addresses = @($kept | Sort-Object); Refused = $refused }
}

function Sync-MiosWslServicePortRules {
    # One inbound Hyper-V allow rule per SSOT service port for the WSL VM creator, scoped to [firewall.windows]
    # (remote_addresses: the local subnet and the tailnet; profiles: never Any). Reconciled by name: missing rules are
    # created, drifted rules corrected, and rules for ports that left SSOT removed. The default inbound action is never touched.
    param([Parameter(Mandatory)][string]$Creator)
    $prefix = 'MiOS-DEV-Port-'
    $result = [pscustomobject]@{ Status = 'ready'; Opened = @(); Removed = @(); Refused = @(); Addresses = @(); Profiles = @() }
    $cmdlets = @('Get-NetFirewallHyperVRule', 'New-NetFirewallHyperVRule', 'Set-NetFirewallHyperVRule', 'Remove-NetFirewallHyperVRule')
    $missing = @($cmdlets | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) })
    if ($missing.Count) {
        Write-Host "  [!] WSL-SERVICE-PORTS-UNSUPPORTED: $($missing -join ', ') unavailable (Hyper-V firewall needs Windows 11 22H2+ with WSL 2.0.9+); MiOS-DEV service ports stay closed to the LAN and tailnet." -ForegroundColor Yellow
        $result.Status = 'unsupported'
        return $result
    }
    $plan = Get-MiosWslServicePortPlan
    $result.Refused += $plan.Refused
    $scope = Resolve-MiosWslServiceScope
    $result.Refused += $scope.Refused
    $result.Addresses = $scope.Addresses
    $profileList = Get-MiosVendorBoundedList -Section 'firewall.windows' -Key 'profiles'
    $result.Refused += $profileList.Refused
    $profiles = @()
    foreach ($p in $profileList.Values) {
        $name = @('Domain', 'Private', 'Public') | Where-Object { $_ -ieq $p }
        if ($name) { $profiles += $name } else { $result.Refused += "profile '$p' is not Domain, Private or Public" }
    }
    $profiles = @($profiles | Sort-Object -Unique)
    $result.Profiles = $profiles
    foreach ($r in $result.Refused) { Write-Host "  [!] WSL service port refused ($r)" -ForegroundColor Yellow }
    $desired = [ordered]@{}
    if (-not $scope.Addresses.Count -or -not $profiles.Count) {
        Write-Host '  [!] WSL-SERVICE-PORTS-UNSCOPED: mios.toml [firewall.windows] resolved to no remote address or no firewall profile; no MiOS-DEV service port is opened.' -ForegroundColor Yellow
        $result.Status = 'unscoped'
    } else {
        foreach ($key in $plan.Ports.Keys) {
            $desired[$prefix + ($key -replace '[^A-Za-z0-9-]', '-')] = @{ Key = $key; Port = [string]$plan.Ports[$key] }
        }
    }
    $profileText = $profiles -join ','
    $existing = @{}
    foreach ($rule in @(Get-NetFirewallHyperVRule -ErrorAction Stop)) {
        if ([string]$rule.Name -like ($prefix + '*')) { $existing[[string]$rule.Name] = $rule }
    }
    # Remove first: a port that left SSOT, or a rule whose action/direction/creator/state cannot be corrected in place.
    foreach ($name in @($existing.Keys)) {
        $rule = $existing[$name]
        $sameCreator = $false
        $id = [guid]::Empty
        if ([guid]::TryParse([string]$rule.VMCreatorId, [ref]$id)) { $sameCreator = ($id -eq [guid]$Creator) }
        $fixed = $sameCreator -and ([string]$rule.Direction -eq 'Inbound') -and ([string]$rule.Action -eq 'Allow') -and
            ([string]$rule.Enabled -ne 'False')
        if (-not $desired.Contains($name) -or -not $fixed) {
            Remove-NetFirewallHyperVRule -Name $name -ErrorAction Stop
            $existing.Remove($name)
            if (-not $desired.Contains($name)) { $result.Removed += $name }
        }
    }
    foreach ($name in $desired.Keys) {
        $want = $desired[$name]
        $display = "MiOS-DEV $($want.Key) ($($want.Port)/tcp)"
        try {
            if ($existing.ContainsKey($name)) {
                $rule = $existing[$name]
                $ports = @($rule.LocalPorts | ForEach-Object { [string]$_ })
                # Windows may read an address back as address/mask or a bare host: compare in the form written.
                $addrs = @($rule.RemoteAddresses | ForEach-Object { ConvertTo-MiosCanonicalPrefix ([string]$_) } | Sort-Object)
                # Profiles may read back as names ('Domain, Private') or as the flag value (1 Domain, 2 Private, 4 Public).
                $ruleProfiles = @(([string]$rule.Profiles) -split '[,\s]+' | Where-Object { $_ } | ForEach-Object {
                    if ($_ -match '^[0-9]+$') { $bits = [int]$_; if ($bits -band 1) { 'Domain' }; if ($bits -band 2) { 'Private' }; if ($bits -band 4) { 'Public' } } else { $_ }
                } | Sort-Object -Unique)
                $drift = ([string]$rule.Protocol -notin @('TCP', '6')) -or ($ports.Count -ne 1 -or $ports[0] -ne $want.Port) -or
                    (($addrs -join ',') -ne ($scope.Addresses -join ',')) -or (($ruleProfiles -join ',') -ne $profileText)
                if ($drift) {
                    Set-NetFirewallHyperVRule -Name $name -NewDisplayName $display -Protocol TCP -LocalPorts $want.Port `
                        -RemoteAddresses $scope.Addresses -Profiles $profileText -ErrorAction Stop | Out-Null
                }
            } else {
                New-NetFirewallHyperVRule -Name $name -DisplayName $display -VMCreatorId $Creator -Direction Inbound -Action Allow `
                    -Protocol TCP -LocalPorts $want.Port -RemoteAddresses $scope.Addresses -Profiles $profileText -ErrorAction Stop | Out-Null
            }
            $result.Opened += "$($want.Key)/$($want.Port)"
        } catch {
            $result.Refused += "$($want.Key): $($_.Exception.Message)"
            Write-Host "  [!] WSL-SERVICE-PORT-FAILED: $($want.Key) ($($want.Port)/tcp) stays closed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    if ($result.Opened.Count) {
        Write-Host "  [+] MiOS-DEV inbound: $($result.Opened -join ', ') from $($scope.Addresses -join ', ') on $profileText" -ForegroundColor Green
    }
    $mode = Resolve-MiosWslNetworkingMode
    if ($mode -ne 'mirrored') {
        Write-Host "  [i] WSL networking_mode=$mode`: LAN and tailnet clients reach a NAT guest through the host port proxy and the Windows Defender rules build-mios.ps1 scopes to [firewall.windows]; Windows localhost through the loopback the policy allows." -ForegroundColor Cyan
    } else {
        Write-Host '  [i] WSL networking_mode=mirrored: Windows<->WSL loopback also needs the image to leave loopback0 unmanaged by NetworkManager.' -ForegroundColor Cyan
    }
    return $result
}

function Set-MiosWslFirewallPolicy {
    $creator = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
    $provider = @('Get-NetFirewallHyperVVMCreator', 'Get-NetFirewallHyperVVMSetting', 'Set-NetFirewallHyperVVMSetting', 'Get-NetFirewallHyperVRule',
        'New-NetFirewallHyperVRule', 'Remove-NetFirewallHyperVRule', 'Invoke-CimMethod')
    $missing = @($provider | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) })
    if ($missing.Count) { throw "The Hyper-V firewall provider is unavailable ($($missing -join ', ')); provisioning stopped before MiOS-DEV." }
    if (-not (Test-MiosWslVmCreator $creator)) { Register-MiosWslVmCreator $creator }
    $policy = @{ Enabled = 'True'; DefaultInboundAction = 'Block'; DefaultOutboundAction = 'Allow'; LoopbackEnabled = 'True'; AllowHostPolicyMerge = 'True' }
    Set-NetFirewallHyperVVMSetting -Name $creator @policy -ErrorAction Stop
    # ActiveStore is the effective policy (Group Policy merged in): it must read back enabled with inbound Block.
    $active = @(Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore -Name $creator -ErrorAction Stop)
    if ($active.Count -ne 1 -or [string]$active[0].Enabled -ne 'True' -or [string]$active[0].DefaultInboundAction -ne 'Block') {
        throw 'The WSL firewall policy did not read back enabled with inbound Block; provisioning stopped before MiOS-DEV.'
    }
    # Explicit allow rules survive default Block. Remove our previous rules first, including deleted/refused entries.
    foreach ($old in @(Get-NetFirewallHyperVRule -ErrorAction Stop)) {
        if ($old.Name -like 'MiOS-WSL-*' -and [string]$old.VMCreatorId -and [guid]$old.VMCreatorId -eq [guid]$creator) {
            Remove-NetFirewallHyperVRule -Name $old.Name -ErrorAction Stop
        }
    }
    $names = @{}
    foreach ($rule in @(Get-MiosTomlTableArray 'wsl2.inbound')) {
        $missing = @('name', 'protocol', 'local_port', 'remote_address', 'reason') | Where-Object { [string]::IsNullOrWhiteSpace([string]$rule[$_]) }
        if ($missing) { Write-Host '  [!] Incomplete WSL inbound entry refused.' -ForegroundColor Yellow; continue }
        $protocol = ([string]$rule['protocol']).ToUpperInvariant()
        if ($protocol -notin @('TCP', 'UDP')) { throw 'A WSL inbound rule must use TCP or UDP.' }
        foreach ($port in ([string]$rule['local_port'] -split ',')) {
            if ($port.Trim() -notmatch '^([1-9][0-9]{0,4})(?:-([1-9][0-9]{0,4}))?$') { throw 'Invalid WSL inbound local port.' }
            $first = [int]$Matches[1]; $last = if ($Matches[2]) { [int]$Matches[2] } else { $first }
            if ($first -gt 65535 -or $last -gt 65535 -or $last -lt $first) { throw 'Invalid WSL inbound port range.' }
        }
        $remote = ([string]$rule['remote_address']).Trim()
        $ipText = $remote; $prefix = $null
        if ($remote.Contains('/')) {
            if ($remote -notmatch '^([^/]+)/([0-9]{1,3})$') { throw 'Invalid WSL inbound address scope.' }
            $ipText = $Matches[1]; $prefix = [int]$Matches[2]
        }
        $ip = $null
        if (-not [Net.IPAddress]::TryParse($ipText, [ref]$ip)) { throw 'WSL inbound remote_address must be an explicit IP address or scoped CIDR.' }
        $bits = if ($ip.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) { 32 } else { 128 }
        if ($null -ne $prefix -and ($prefix -lt 1 -or $prefix -gt $bits)) { throw 'An unscoped WSL inbound address is refused.' }
        if ($null -eq $prefix -and ($ip.Equals([Net.IPAddress]::Any) -or $ip.Equals([Net.IPAddress]::IPv6Any))) { throw 'An unscoped WSL inbound address is refused.' }
        $name = 'MiOS-WSL-' + ([string]$rule['name'] -replace '[^A-Za-z0-9-]', '-')
        if ($names.ContainsKey($name)) { throw 'Duplicate WSL inbound rule name.' }
        $names[$name] = $true
        New-NetFirewallHyperVRule -Name $name -DisplayName $name -VMCreatorId $creator -Direction Inbound -Action Allow `
            -Protocol $protocol -LocalPorts ([string]$rule['local_port']) -RemoteAddresses $remote -ErrorAction Stop | Out-Null
    }
    # MiOS-DEV's SSOT service ports, reconciled after the policy reads back inbound Block.
    $null = Sync-MiosWslServicePortRules -Creator $creator
}

function Invoke-MiosPlatformGate {
    # Features first, then the WSL firewall policy; anything short of 'ready' stops before MiOS-DEV.
    $result = @(Enable-MiOSWindowsFeatures) | Where-Object { $_ -is [pscustomobject] -and $_.PSObject.Properties['Status'] } | Select-Object -Last 1
    if (-not $result) { throw 'Windows feature readiness returned no result; provisioning stopped before MiOS-DEV.' }
    switch ([string]$result.Status) {
        'ready'   { $null = Set-MiosWslFirewallPolicy; return $result }
        'pending' { return $result }
        default   { throw "Windows features are not ready ($($result.Status): $($result.Detail)); provisioning stopped before the WSL firewall policy and MiOS-DEV." }
    }
}
function Initialize-MiosMachineStorage {
    param([Parameter(Mandatory)]$Context)
    $_msgStep0 = '[*] Preparing configured host storage...'
    $_msgStep0Failed = '[!] Initialize-DataDisk failed: {0}'
    # Install-robustness (B2): hardware-virtualization preflight. WSL2 +
    # `podman machine init` cannot start without VT-x/AMD-V (SVM) enabled in BIOS/
    # UEFI. Fail fast before Initialize-DataDisk (reboot/disk changes).
    # A failure here throws its public reason back through the worker pipe to the operator. It never exits the worker silently
    # and never reaps or cleans anything: existing distributions, persistent state, sources and models stay as they are.
    $_virtFw = $true; $_hyperv = $true
    try { $_virtFw = [bool](Get-CimInstance Win32_Processor -EA Stop | Select-Object -First 1 -Expand VirtualizationFirmwareEnabled) } catch {}
    try { $_hyperv = [bool](Get-CimInstance Win32_ComputerSystem -EA Stop).HypervisorPresent } catch {}
    if (-not $_virtFw -and -not $_hyperv) {
        Write-Err "Hardware virtualization is DISABLED -- WSL2 + podman machine cannot start."
        Write-Err "  -> Enable Intel VT-x / AMD-V (SVM) in BIOS/UEFI, then re-run the bootstrap."
        throw 'Hardware virtualization is disabled: enable Intel VT-x / AMD-V (SVM) in BIOS/UEFI, then re-run. No storage changes were made.'
    }

    Write-Host ''
    Write-Host "  $_msgStep0" -ForegroundColor Cyan
    $_dataOk = $true; $_dataReason = ''
    try { Initialize-DataDisk } catch { $_dataOk = $false; $_dataReason = $_.Exception.Message; Write-Host ('  ' + ($_msgStep0Failed -f $_.Exception.Message)) -ForegroundColor Yellow }
    $_dataDrive = [string](Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default 'C')
    if ($_dataOk -and -not (Get-Volume -DriveLetter $_dataDrive -ErrorAction SilentlyContinue)) { $_dataOk = $false; $_dataReason = "configured volume ${_dataDrive}: was not returned by storage discovery" }
    if (-not $_dataOk) {
        Write-Err "Could not provision the ${_dataDrive}:\ data partition -- MiOS stores its containers,"
        Write-Err "  packages, config and repos there, so the install cannot continue without it."
        Write-Err "  Most common causes on a factory laptop:"
        Write-Err "    - Under ~64 GB free on C:  (free some space / empty the Recycle Bin, then re-run)."
        Write-Err "    - C: is BitLocker-locked or non-shrinkable  (suspend it, then re-run the one-liner:"
        Write-Err "        manage-bde -protectors -disable C: -RebootCount 1)."
        throw "Could not provision the ${_dataDrive}:\ data partition ($_dataReason); existing distributions, persistent state and source checkouts are preserved."
    }
}

function Get-MiosMachineTomlRoots {
    # The system root and an explicitly selected, already provisioned secondary drive only.
    # A drive merely being present never opts it in. An absent selected drive waits for storage provisioning.
    $_systemDrive = $(if ($env:SystemDrive) { [string]$env:SystemDrive } else { 'C:' }).Trim().TrimEnd('\').ToUpperInvariant()
    if ($_systemDrive -notmatch '^[A-Z]:$') { throw 'The Windows system drive must be one absolute drive root.' }
    $_selectedDrive = ([string](Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default $_systemDrive.Substring(0, 1))).Trim().ToUpperInvariant()
    if ($_selectedDrive -notmatch '^[A-Z]:?$') { throw 'The configured host storage drive must be one Windows drive letter.' }
    $_systemDrive
    $_dataRoot = $_selectedDrive.Substring(0, 1) + ':'
    if ($_dataRoot -cne $_systemDrive -and (Test-Path -LiteralPath ($_dataRoot + '\') -PathType Container)) { $_dataRoot }
}

function Publish-MiosMachineToml {
    param([Parameter(Mandatory)]$Context, [string[]]$AdmittedRoots)
    # Core supplies its one admitted-root snapshot. Standalone callers resolve once here.
    # Validate a supplied snapshot against the same in-memory selection without adding newly appeared drives.
    if ($PSBoundParameters.ContainsKey('AdmittedRoots')) {
        $_roots = @($AdmittedRoots)
        $_systemRoot = $(if ($env:SystemDrive) { [string]$env:SystemDrive } else { 'C:' }).Trim().TrimEnd('\').ToUpperInvariant()
        $_selectedDrive = ([string](Get-MiosTomlValue -Section 'bootstrap.host_storage' -Key 'drive_letter' -Default $_systemRoot.Substring(0, 1))).Trim().ToUpperInvariant()
        if ($_roots.Count -lt 1 -or $_roots.Count -gt 2 -or
            @($_roots | Where-Object { $_ -cnotmatch '^[A-Z]:$' }).Count -or $_roots[0] -cne $_systemRoot -or
            $_selectedDrive -notmatch '^[A-Z]:?$' -or
            ($_roots.Count -eq 2 -and ($_roots[1] -ceq $_roots[0] -or $_roots[1] -cne ($_selectedDrive.Substring(0, 1) + ':')))) {
            throw 'Machine TOML publication received an invalid admitted-root snapshot.'
        }
    } else { $_roots = @(Get-MiosMachineTomlRoots) }
    # Restrict/repair old readable credential copies only inside those admitted roots.
    $null = Repair-MiosHostTierCredentials -Paths @($_roots | ForEach-Object { Join-Path $_ 'etc\mios\mios.toml' })
    try {
        $_miosVendorToml = Resolve-MiosVendorTomlText
        $_miosHostToml = Resolve-MiosTomlText
        if ($_miosVendorToml) {
            foreach ($_root in $_roots) {
                Write-MiosVendorToml -Path (Join-Path $_root 'usr\share\mios\mios.toml') -Text $_miosVendorToml
                if ($_miosHostToml) {
                    # The operator layer stays in the user tier; the host tier gets its credential-free projection.
                    Write-MiosHostToml -Path (Join-Path $_root 'etc\mios\mios.toml') -Text $_miosHostToml
                    Write-Host "  [+] Full vendor mios.toml -> $_root\usr\share\mios; operator profile (credential-free) -> $_root\etc\mios" -ForegroundColor DarkGray
                } else {
                    Write-Host "  [+] Full vendor mios.toml -> $_root\usr\share\mios (no host override found)" -ForegroundColor DarkGray
                }
            }
        } else {
            Write-Host "  [!] Full vendor mios.toml could not be loaded -- mios.toml was not overwritten" -ForegroundColor Yellow
        }
    } catch {
        throw 'Host TOML security/promotion failed; provisioning stopped. Check host-tier ACLs before retrying.'
    }
    foreach ($_root in $_roots) {
        $_checkDst = Join-Path $_root 'usr\share\mios\mios.toml'
        if (-not (Test-Path -LiteralPath $_checkDst -PathType Leaf) -or
            [IO.File]::ReadAllText($_checkDst, (New-Object System.Text.UTF8Encoding($false))) -cne $_miosVendorToml) {
            throw "Full fetched system mios.toml was not staged exactly on $_checkDst; refusing to continue the installer."
        }
    }
}

function Register-MiosBootstrapResume {
    param([Parameter(Mandatory)]$Context)
    try {
        $_resumeUrl = [string](Get-MiosTomlValue -Section 'bootstrap' -Key 'oneliner_url' -Default 'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1')
        if ($_resumeUrl -notmatch '^https://raw\.githubusercontent\.com/[A-Za-z0-9_./%-]+/Get-MiOS\.ps1$') { throw 'Invalid bootstrap resume URL.' }
        $_ps        = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $_resumeArg = "-NoProfile -Command `"irm '$_resumeUrl' | iex`""
        $_resumeRoot = Join-Path $env:ProgramData 'MiOS'
        $_vbsDir = Join-Path $_resumeRoot 'bootstrap'
        foreach ($directory in @($_resumeRoot, $_vbsDir)) {
            Assert-MiosNoReparsePath $directory
            if (Test-Path -LiteralPath $directory) { Assert-MiosMachineWriteBoundary $directory }
            else { New-MiosProtectedMachineDirectory $directory }
        }
        $_runHiddenVbs = Join-Path $_vbsDir 'run-hidden.vbs'
        Assert-MiosNoReparsePath $_runHiddenVbs
        if (Test-Path -LiteralPath $_runHiddenVbs) { Assert-MiosMachineWriteBoundary $_runHiddenVbs }
        else {
            $_vbsText = @'
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
'@
            Write-MiosPrivateMachineFile -Path $_runHiddenVbs -Bytes ([Text.Encoding]::ASCII.GetBytes($_vbsText))
            Set-MiosHostTierAcl $_runHiddenVbs
        }
        $_wscript     = Join-Path $env:SystemRoot 'System32\wscript.exe'
        $_act = New-ScheduledTaskAction -Execute $_wscript -Argument "//B //Nologo `"$_runHiddenVbs`" `"$_ps`" $_resumeArg"
        $_trg  = New-ScheduledTaskTrigger -AtLogOn -User $Context.Sid
        $_prin = New-ScheduledTaskPrincipal -UserId $Context.Sid -LogonType Interactive -RunLevel Limited
        $_set  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName 'MiOS-Resume-Bootstrap' -Action $_act -Trigger $_trg -Principal $_prin -Settings $_set -Force | Out-Null
        Write-Host '  [+] Auto-resume armed: MiOS continues automatically after you reboot and' -ForegroundColor Green
        Write-Host '      sign back in -- no need to re-paste the one-liner.' -ForegroundColor Green
    } catch {
        Write-Host "  [!] Could not arm auto-resume ($($_.Exception.Message)); after reboot re-run" -ForegroundColor Yellow
        Write-Host '      the irm|iex one-liner manually to continue.' -ForegroundColor Yellow
    }
}

function Invoke-MiosMachinePreparationCore {
    param([Parameter(Mandatory)]$Context)
    if (-not (Test-MiosElevated)) { throw 'Machine preparation requires an elevated token.' }
    Assert-MiosOriginContext $Context
    $script:MiosMachineOrigin = $Context
    # Recover exactly the TOML-selected existing roots, after origin validation and before any publication or repair.
    $_tomlRoots = @(Get-MiosMachineTomlRoots)
    foreach ($_tomlRoot in $_tomlRoots) {
        $null = Invoke-MiosMachineBoundaryRecovery -OriginSid ([string]$Context.Sid) -Root $_tomlRoot
    }
    Publish-MiosMachineToml -Context $Context -AdmittedRoots $_tomlRoots
    Initialize-MiosMachineStorage -Context $Context
    Add-MiosDefenderExclusions
    # The readiness gate remains mandatory and outside a warning-only catch.
    $_platform = Invoke-MiosPlatformGate
    if ($_platform.Status -eq 'pending') {
        Register-MiosBootstrapResume -Context $Context
        return $_platform
    }
    if (Get-Command Unregister-ScheduledTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName 'MiOS-Resume-Bootstrap' -Confirm:$false -ErrorAction SilentlyContinue
    }
    # The Podman client must match the machine-os MiOS-DEV is provisioned from; this elevated stage is the only install path.
    $null = Install-MiosPodmanClient
    Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1 -Type DWord -Force -ErrorAction Stop
    return $_platform
}

# Resolve the localized failure format before phase-zero work can throw.
$_trapFmtFailed = Get-MiosTomlValue -Section 'messages.failure_trap' -Key 'install_failed_template' -Default '[!!] Install failed: {0}'
$_freshVendorToml = Resolve-MiosVendorTomlText
if (-not $_freshVendorToml -or
    $_freshVendorToml -notmatch '(?m)^\[meta\]\s*$' -or
    $_freshVendorToml -notmatch '(?m)^\[identity\]\s*$' -or
    $_freshVendorToml -notmatch '(?m)^\[packages\.windows\]\s*$') {
    Write-Host '  [!!] Fresh system mios.toml unavailable or incomplete; Phase 0 was not started.' -ForegroundColor Red
    exit 1
}
Ensure-Winget | Out-Null
$_platform = Invoke-MiosMachinePreparation -Context $Script:MiosOriginContext
Complete-MiosMachinePreparation -Result $_platform
& wsl.exe --set-default-version 2 2>&1 | Out-Null

$_netMode  = Resolve-MiosWslNetworkingMode
$_lhfwd    = [string](Get-MiosTomlValue -Section 'wsl2' -Key 'localhost_forwarding' -Default 'true')
$_fwall    = Get-MiosTomlValue -Section 'wsl2' -Key 'firewall' -Default $true
$_gui      = [string](Get-MiosTomlValue -Section 'wsl2' -Key 'gui_applications'     -Default 'true')
$_loopback = [bool](Get-MiosTomlValue -Section 'wsl2' -Key 'host_address_loopback' -Default $true)
$_isMirror = ($_netMode -ieq 'mirrored')
$_wslHostRamGB = try { [math]::Floor((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB) } catch { 16 }
$_wslReservePct = [math]::Min(95, [math]::Max(0, [int](Get-MiosTomlValue -Section 'bootstrap.dev_vm.host_reserve' -Key 'memory_pct' -Default 50)))
$_wslReserveGB  = [math]::Max(0, [int](Get-MiosTomlValue -Section 'bootstrap.dev_vm.host_reserve' -Key 'memory_gb'  -Default 8))
$_wslRamGB = [math]::Max(4, $_wslHostRamGB - [math]::Max($_wslReserveGB, [math]::Floor($_wslHostRamGB * $_wslReservePct / 100)))

$_wslCfg = Join-Path $env:USERPROFILE ".wslconfig"
$_wslCfgRaw = if (Test-Path -LiteralPath $_wslCfg) { [IO.File]::ReadAllText($_wslCfg) } else { '' }

# Build the section body from TOML-resolved values.
$_keyLines = New-Object System.Collections.Generic.List[string]
$_keyLines.Add("memory=${_wslRamGB}GB")
$_keyLines.Add("networkingMode=$_netMode")
if (-not $_isMirror -and $_lhfwd -ieq 'true') { $_keyLines.Add('localhostForwarding=true') }
if ($_gui -ieq 'true') { $_keyLines.Add('guiApplications=true') }

# Reconcile the entire managed key set, including a stale firewall=false even when all resource keys match.
$_desiredWslConfig = Get-MiosWslConfigText -Existing $_wslCfgRaw -ManagedLines $_keyLines.ToArray() -Firewall $_fwall -HostAddressLoopback $_loopback
if ($_desiredWslConfig -cne $_wslCfgRaw) {
    [IO.File]::WriteAllText($_wslCfg, $_desiredWslConfig, (New-Object Text.UTF8Encoding($false)))
    Write-Host "  [+] .wslconfig: $_netMode mode, ${_wslRamGB}GB RAM and SSOT firewall filtering reconciled" -ForegroundColor Green
    & wsl.exe --shutdown 2>$null | Out-Null
}

try { Invoke-MiOSFullReap } catch { Write-Host "  [!] Invoke-MiOSFullReap failed: $($_.Exception.Message)" -ForegroundColor Yellow }

trap {
    # A scope-wide trap also handles errors before the TOML formatter is initialized.
    # Keep the original reason when configuration is unavailable or cannot format it.
    $_trapReason = [string]$_.Exception.Message
    $_trapDisplay = $_trapReason
    $_trapTemplate = Get-Variable -Name '_trapFmtFailed' -ValueOnly -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrWhiteSpace([string]$_trapTemplate)) {
        try {
            $_trapFormatted = [string]($_trapTemplate -f $_trapReason)
            if (-not [string]::IsNullOrWhiteSpace($_trapFormatted) -and $_trapFormatted.Contains($_trapReason)) {
                $_trapDisplay = $_trapFormatted
            }
        } catch { }
    }
    Write-Host ''
    Write-Host ('  ' + $_trapDisplay) -ForegroundColor Red
    Write-Host '  [MiOS] Installation failed; existing distributions, persistent state and source checkouts are preserved.' -ForegroundColor Yellow
    exit 1
}
# SSOT: every Step N banner resolves through mios.toml [messages.steps].
# Per feedback_mios_messages_section_ssot: no Write-Host literals in code;
# vendor defaults via -Default arg of Get-MiosTomlValue.

if ($true) {
    try {
        $personalize = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
        if (-not (Test-Path $personalize)) { New-Item -Path $personalize -Force | Out-Null }
        Set-ItemProperty -Path $personalize -Name 'EnableTransparency'   -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $personalize -Name 'AppsUseLightTheme'    -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $personalize -Name 'SystemUsesLightTheme' -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
        Set-ItemProperty -Path $personalize -Name 'ColorPrevalence'      -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue

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
    Write-Host '|  Continuing with the operator-owned MiOS-DEV build...       |' -ForegroundColor Cyan
    Write-Host '+============================================================+' -ForegroundColor Cyan
    Write-Host ''
    # Machine preparation completed above. The original token owns the build and WSL distro.

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

# Machine preparation completed before user setup. Shared package/profile
# storage redirectors are compatibility functions and are never invoked by this entrypoint.

# Create the canonical Windows install root structure. Sub-repos are
# nested directly at the root directory as sub git trees.
$sysLetter = if ($env:SystemDrive) { $env:SystemDrive.TrimEnd(':') } else { 'C' }
$sysDrive  = "${sysLetter}:"
$miosRepoDir = Get-MiosWindowsInstallRoot
$canonicalBootstrap = Get-MiosWindowsBootstrapRoot
New-Item -ItemType Directory -Path $miosRepoDir -Force -ErrorAction SilentlyContinue | Out-Null

if ((Test-Path $RepoDir) -and ($RepoDir -ne $canonicalBootstrap) -and ($RepoDir -ne 'M:\MiOS\repo\mios-bootstrap')) {
    Write-Err "-RepoDir $RepoDir already exists. Either delete it manually, or re-run without -RepoDir to use the canonical $canonicalBootstrap."
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
    $_layout = Get-MiosInstalledLayout
    $_nativeProjection = Join-Path $_layout.NativeBin 'mios-native-client-setup.ps1'
    if (Test-Path -LiteralPath $_nativeProjection) {
        # A per-user layout projects in User scope: no Machine variable, all-users key or shortcut, and no elevation.
        $_projectionArgs = @{ RuntimeOnly = $true; BinDirectory = (Split-Path -Parent $_nativeProjection) }
        if ($_layout.Scope -eq 'User') { $_projectionArgs['Scope'] = 'User'; $_projectionArgs['ProfilePath'] = $_layout.ProfileBody }
        & $_nativeProjection @_projectionArgs
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
