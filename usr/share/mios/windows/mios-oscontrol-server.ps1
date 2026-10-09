# AI-hint: Windows-specific HTTP listener for the MiOS OS-control system that executes Win32 commands, manages window states (move/resize/focus), and pro...
# AI-doc: usr/share/doc/mios/manual/windows.md
<#
  mios-oscontrol-server.ps1  --  MiOS OS-control executor (Windows node)

  WHY THIS EXISTS  (operator 2026-05-25, 4B)
  ------------------------------------------
  4A made the always-on mios-daemon-agent FIRE + VERIFY launches on the dev
  VM's desktop (through the launcher broker, which inherits the VM-host's WSLg
  display env). 4B extends OS-control TO A SEPARATE PHYSICAL NODE -- e.g. the
  iGPU host -- so an agent can launch + confirm an app on THAT machine's own
  desktop, reached over Tailscale.

  This is a tiny self-contained HTTP executor: a System.Net.HttpListener that
  accepts launch + verify requests and runs them with native Win32 on THIS
  node's interactive desktop. It is the OS-control sibling of
  mios-igpu-server.ps1 (which serves inference): same install/firewall
  machinery, different payload.

  The MiOS VM reaches it via [os_control.nodes.<name>].endpoint in mios.toml
  (put the real tailnet endpoint in your /etc/mios overlay, NOT the public repo).

  ROUTES
  ------
    GET  /health                 -> {ok, host, ts}
    POST /launch  {app:"<name>"}  -> resolve + Start-Process on this desktop,
                                     poll for the window, then
                                     {app, fired, launched, verdict, host, node}
    GET  /verify?app=<name>       -> window-presence check on this node ->
                                     {app, launched, verdict, host}
    GET  /windows                 -> {ok, count, windows:[{hwnd,title,pid,proc,
                                     x,y,w,h}], host} -- the grounding SNAPSHOT
                                     the agent records BEFORE+AFTER open/close to
                                     diff exactly what changed (operator 2026-05-26).
    POST /window/close  {title|hwnd}            -> graceful WM_CLOSE (NOT a kill)
    POST /window/focus  {title|hwnd}            -> raise + foreground
    POST /window/move   {title|hwnd, x, y}      -> reposition
    POST /window/resize {title|hwnd, width, height}
    POST /window/center {title|hwnd}            -> center on the window's monitor work area
    POST /window/state  {title|hwnd, state:minimize|maximize|restore}
                                  -> each returns {ok, op, count, matched:[...]}
    GET  /screen-layout           -> {ok, count, screens:[{device,primary,bounds,work}]}
    GET  /screenshot              -> {ok, format:png, width, height, image_b64}
    POST /input/mouse-move   {x, y}
    POST /input/click        {x, y, button:left|right|middle}
    POST /input/double-click {x, y}
    POST /input/type         {text}
    POST /input/key          {name}            -> Enter|Tab|Esc|F5|<char>...
    POST /input/key-combo    {combo:"Ctrl+C"}
                                  -- input/capture run on WinSta0\Default (the
                                  real desktop), unlike blind WSL-interop.

  It NEVER fabricates launched=true: launched reflects an actual top-level
  window match (title or owning-process name). Window ops report the ACTUAL
  matched windows; an empty match returns ok=false (no fabricated success).

  USAGE
  -----
    pwsh -File mios-oscontrol-server.ps1                 # run in foreground
    pwsh -File mios-oscontrol-server.ps1 -Install        # logon scheduled task (hidden, elevated)
    pwsh -File mios-oscontrol-server.ps1 -Uninstall      # remove the task
    pwsh -File mios-oscontrol-server.ps1 -ConfigPath <runtime-SSOT.json>

  5.1-compatible (the scheduled task runs Windows PowerShell 5.1 for a stable
  interpreter path -- the MSIX pwsh alias is unresolvable by Task Scheduler;
  see mios-igpu-server.ps1 -Install for that lesson). No ternary / ?? / -Parallel.
#>
[CmdletBinding()]
param(
    [int]    $Port    = 0,
    [string] $ConfigPath = (Join-Path $env:LOCALAPPDATA 'MiOS\themes\ssot.json'),
    [double] $VerifySettleSeconds   = 1.5,
    [int]    $VerifyAttempts        = 6,
    [double] $VerifyIntervalSeconds = 2.5,
    [switch] $Install,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

function Read-MiosOscontrolPort([string]$Path) {
    $projection = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    $value = $projection.ports.oscontrol
    if (($value -isnot [int] -and $value -isnot [long]) -or $value -lt 1 -or $value -gt 65535) {
        throw 'Runtime SSOT must contain an integer [ports].oscontrol from 1 to 65535'
    }
    return [int]$value
}
if (-not $Uninstall) {
    if (-not $PSBoundParameters.ContainsKey('Port')) { $Port = Read-MiosOscontrolPort $ConfigPath }
    if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid OS-control port' }
}
$taskName = 'MiOS-OSControl-Server'
$fwName   = "MiOS - oscontrol ($Port/tcp)"
$logDir   = Join-Path $env:LOCALAPPDATA 'mios\oscontrol\logs'

function Info($m){ Write-Host "  [*] $m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "  [+] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }

# ---- scheduled-task install / uninstall (mirrors mios-igpu-server.ps1) -------
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Ok "removed scheduled task '$taskName'"
    return
}
if ($Install) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Warn 'Not elevated -- re-launching via UAC to register the logon task...'
        Start-Process -FilePath 'pwsh.exe' -Verb RunAs -ArgumentList @(
            '-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath,'-Install','-Port',$Port)
        return
    }
    $argline = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -ConfigPath `"$ConfigPath`""
    if ($PSBoundParameters.ContainsKey('Port')) { $argline += " -Port $Port" }
    # Resolve a CONCRETE interpreter path: NOT the bare 'pwsh.exe' MSIX alias
    # (Task Scheduler can't resolve it -> 0x80070002). Prefer a real pwsh under
    # Program Files, else Windows PowerShell 5.1 at its fixed System32 path
    # (this script is 5.1-compatible).
    $psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $psExe)) { throw 'The desktop executor requires the installed Windows PowerShell interpreter' }
    $action  = New-ScheduledTaskAction -Execute $psExe -Argument $argline
    $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogon -User $account
    $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    # Interactive Logon: runs elevated in the logged-on user's interactive session
    # (so it has access to WinSta0\Default and can enumerate / focus GUI windows)
    $prin    = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $set -Principal $prin -Force | Out-Null
    Ok "registered logon scheduled task '$taskName' (port $Port)"
    # The executor is loopback-only. Remote clients use an authenticated SSH
    # tunnel; an old broad inbound rule must not survive reconciliation.
    Get-NetFirewallRule -DisplayName 'MiOS - oscontrol (*)' -ErrorAction SilentlyContinue |
        Disable-NetFirewallRule -ErrorAction SilentlyContinue | Out-Null
    Info 'starting it now...'
    Start-ScheduledTask -TaskName $taskName
    return
}

# The previous compiled stub acknowledged requests without performing them.
# Keep the real route implementation until a native replacement has parity.

New-Item -ItemType Directory -Force -Path $logDir | Out-Null


# ---- Win32 surface for window enumeration ------------------------------------
$Win32Sig = @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class OSCW32 {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc p, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int max);
    // InternalGetWindowText reads the title from the window's INTERNAL storage
    // WITHOUT sending WM_GETTEXT -- so enumeration never blocks on a hung /
    // non-responding window (/windows intermittently
    // wedged after launches -> GetWindowText was hanging on an unresponsive
    // app's message pump, freezing the whole listener + breaking autocenter +
    // launch-verify). Non-blocking; the robust enumeration primitive.
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int InternalGetWindowText(IntPtr h, StringBuilder s, int max);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int ht, bool repaint);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr i);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    // SystemParametersInfo(SPI_SETFOREGROUNDLOCKTIMEOUT=0) + AllowSetForegroundWindow:
    // satisfy the foreground LOCK so SetForegroundWindow from this background process
    // actually wins -- WITHOUT injecting the menubar-activating Alt-tap (operator
    //). The clean replacement for the removed keybd_event(Alt) hack.
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SystemParametersInfo(uint uiAction, uint uiParam, IntPtr pvParam, uint fWinIni);
    [DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(int dwProcessId);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
    public const uint MOUSEEVENTF_LEFTDOWN  = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP    = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP   = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN= 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP  = 0x0040;
}
"@
if (-not ([System.Management.Automation.PSTypeName]'OSCW32').Type) {
    Add-Type -TypeDefinition $Win32Sig -ErrorAction SilentlyContinue
}

# Enumerate visible top-level windows -> list of @{ title; pid; proc }.
#
# HANG-HARDENING (/windows timed out while / answered):
# the per-window `Get-Process -Id` call below was made INSIDE the EnumWindows
# callback, so a single hung/protected process (or a slow WMI-backed lookup)
# stalled the ENTIRE enumeration and the route never returned. Build a
# PID -> ProcessName snapshot ONCE up front (single Get-Process) and look up
# from the hashtable in the callback -- no blocking syscall per window, and
# far faster across a desktop full of windows.
function Get-VisibleWindows {
    $result = New-Object System.Collections.ArrayList
    # CACHE the PID->name snapshot with a short TTL (the
    # executor WEDGED under launch load). During a launch the agent verify-poll
    # + mios-autocenter hammer /windows ~30x in 30s; re-running the heavy +
    # occasionally-stalling Get-Process on EVERY request was the load-induced
    # hang. Rebuild only when the snapshot is stale (>2s); a burst reuses it, so
    # /windows stays cheap (just the non-blocking EnumWindows walk). script:
    # scope so the EnumWindows delegate callback always sees the map.
    $now = [DateTime]::UtcNow
    if ((-not $script:OSCProcMap) -or (-not $script:OSCProcMapTs) -or
        (($now - $script:OSCProcMapTs).TotalSeconds -gt 2)) {
        $m = @{}
        try {
            foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
                if ($p -and -not $m.ContainsKey([int]$p.Id)) { $m[[int]$p.Id] = $p.ProcessName }
            }
        } catch {}
        $script:OSCProcMap = $m
        $script:OSCProcMapTs = $now
    }
    $cb = [OSCW32+EnumWindowsProc]{
        param($h, $l)
        if ([OSCW32]::IsWindowVisible($h)) {
            # InternalGetWindowText (non-blocking) -- NOT GetWindowTextLength +
            # GetWindowText, BOTH of which send synchronous messages that hang on
            # an unresponsive window + freeze the whole /windows enumeration.
            $sb = New-Object System.Text.StringBuilder 512
            if ([OSCW32]::InternalGetWindowText($h, $sb, $sb.Capacity) -gt 0) {
                $procId = 0
                [void][OSCW32]::GetWindowThreadProcessId($h, [ref]$procId)
                $pname = ''
                if ($script:OSCProcMap.ContainsKey([int]$procId)) { $pname = $script:OSCProcMap[[int]$procId] }
                $rect = New-Object OSCW32+RECT
                [void][OSCW32]::GetWindowRect($h, [ref]$rect)
                [void]$result.Add(@{ hwnd = $h.ToInt64(); title = $sb.ToString();
                                     pid = [int]$procId; proc = $pname;
                                     x = [int]$rect.Left; y = [int]$rect.Top;
                                     w = [int]($rect.Right - $rect.Left);
                                     h = [int]($rect.Bottom - $rect.Top) })
            }
        }
        return $true
    }
    [void][OSCW32]::EnumWindows($cb, [IntPtr]::Zero)
    return $result
}

# Does a visible top-level window match this app name (title OR process)?
function Test-WindowPresent($name) {
    $needle = ($name -replace '\.exe$','').Trim()
    if (-not $needle) { return @{ launched = $false; summary = 'empty-name' } }
    foreach ($w in Get-VisibleWindows) {
        if (($w.title -and $w.title -like "*$needle*") -or
            ($w.proc  -and $w.proc  -like "*$needle*")) {
            return @{ launched = $true; summary = 'presented';
                      title = $w.title; pid = $w.pid; proc = $w.proc }
        }
    }
    return @{ launched = $false; summary = 'no-window' }
}

# Resolve a target to the matching visible window(s): by hwnd when given,
# else case-insensitive title/proc substring. Returns the live window hashes.
function Resolve-TargetWindows($hwnd, $title) {
    $all = Get-VisibleWindows
    if ($hwnd) {
        $hv = [int64]$hwnd
        return @($all | Where-Object { $_.hwnd -eq $hv })
    }
    $needle = ("$title" -replace '\.exe$','').Trim()
    if (-not $needle) { return @() }
    return @($all | Where-Object {
        ($_.title -and $_.title -like "*$needle*") -or
        ($_.proc  -and $_.proc  -like "*$needle*") })
}

# Perform a window op on the matching window(s). op = close|focus|move|resize|
# state. close is a GRACEFUL WM_CLOSE (operator binding: never force-kill /
# Stop-Process a window). Returns {ok, op, count, matched:[...]}.
function Test-MiosWindowReadback($Op, $Actual, $Expected) {
    if ($Op -eq 'close') { return ($Actual.exists -eq $false) }
    if ($Actual.exists -ne $true) { return $false }
    switch ($Op) {
        'focus' { return ($Actual.foreground -eq $true) }
        'state' {
            switch ($Expected.state) {
                'minimize' { return ($Actual.iconic -eq $true) }
                'maximize' { return ($Actual.zoomed -eq $true) }
                'restore' { return ($Actual.iconic -eq $false -and $Actual.zoomed -eq $false) }
                default { return $false }
            }
        }
    }
    if ($Actual.rect_valid -ne $true) { return $false }
    if ($Op -eq 'move') { return ($Actual.x -eq $Expected.x -and $Actual.y -eq $Expected.y) }
    if ($Op -eq 'resize') { return ($Actual.width -eq $Expected.width -and $Actual.height -eq $Expected.height) }
    if ($Op -in @('center','position')) {
        return ($Actual.x -eq $Expected.x -and $Actual.y -eq $Expected.y -and $Actual.width -eq $Expected.width -and $Actual.height -eq $Expected.height)
    }
    return $false
}

function Get-MiosWindowReadback([IntPtr]$Hwnd) {
    $exists = [OSCW32]::IsWindow($Hwnd)
    $rect = New-Object OSCW32+RECT
    $valid = $exists -and [OSCW32]::GetWindowRect($Hwnd, [ref]$rect)
    return @{ exists=$exists; foreground=([OSCW32]::GetForegroundWindow() -eq $Hwnd);
        iconic=[OSCW32]::IsIconic($Hwnd); zoomed=[OSCW32]::IsZoomed($Hwnd); rect_valid=$valid;
        x=$rect.Left; y=$rect.Top; width=($rect.Right-$rect.Left); height=($rect.Bottom-$rect.Top) }
}

function Invoke-WindowOp($op, $hwnd, $title, $x, $y, $w, $h, $state, $monitor = -1) {
    $WM_CLOSE = 0x0010
    $targets = Resolve-TargetWindows $hwnd $title
    if (-not $targets -or $targets.Count -eq 0) {
        return @{ ok = $false; op = $op; count = 0; matched = @();
                  error = "no visible window matches" }
    }
    $done = New-Object System.Collections.ArrayList
    $verifiedAll = $true
    foreach ($wnd in $targets) {
        $p = [IntPtr]([int64]$wnd.hwnd)
        if ($op -in @('move', 'resize', 'center', 'position')) {
            if ([OSCW32]::IsZoomed($p) -or [OSCW32]::IsIconic($p)) {
                [void][OSCW32]::ShowWindow($p, 9) # SW_RESTORE
                Start-Sleep -Milliseconds 100      # brief settle
            }
        }
        switch ($op) {
            'close'  { [void][OSCW32]::PostMessage($p, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) }
            'focus'  {
                $ptr = [System.Runtime.InteropServices.Marshal]::AllocHGlobal(4)
                $oldTimeout = 0
                if ([OSCW32]::SystemParametersInfo(0x2000, 0, $ptr, 0)) {
                    $oldTimeout = [System.Runtime.InteropServices.Marshal]::ReadInt32($ptr)
                }
                [System.Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)

                [void][OSCW32]::SystemParametersInfo(0x2001, 0, [IntPtr]::Zero, 0)  # SPI_SETFOREGROUNDLOCKTIMEOUT = 0, no persist
                [void][OSCW32]::AllowSetForegroundWindow(-1)                        # ASFW_ANY
                $fg = [OSCW32]::GetForegroundWindow()
                $fgT = 0; [void][OSCW32]::GetWindowThreadProcessId($fg, [ref]$fgT)
                $myT = [OSCW32]::GetCurrentThreadId()
                $att = $false
                if ($fgT -ne 0 -and $fgT -ne $myT) { $att = [OSCW32]::AttachThreadInput($myT, $fgT, $true) }
                [void][OSCW32]::ShowWindow($p, 9)        # SW_RESTORE
                [void][OSCW32]::BringWindowToTop($p)
                [void][OSCW32]::SetForegroundWindow($p)
                [void][OSCW32]::SetWindowPos($p, [IntPtr](-1), 0, 0, 0, 0, 0x0003)  # HWND_TOPMOST, NOMOVE|NOSIZE
                [void][OSCW32]::SetWindowPos($p, [IntPtr](-2), 0, 0, 0, 0, 0x0003)  # HWND_NOTOPMOST
                if ($att) { [void][OSCW32]::AttachThreadInput($myT, $fgT, $false) }

                [void][OSCW32]::SystemParametersInfo(0x2001, 0, [IntPtr]$oldTimeout, 0) # Restore
            }
            # move/resize/center use SetWindowPos with SWP_ASYNCWINDOWPOS instead
            # of MoveWindow (the executor stalled DURING a
            # launch -> centering hung the single-threaded listener). MoveWindow
            # (and a repaint:$true SetWindowPos) SENDS WM_WINDOWPOSCHANGING/paint
            # SYNCHRONOUSLY to the target window's message loop and BLOCKS until
            # it acks -- a freshly-launched app isn't pumping its queue yet, so
            # the call hangs for seconds and every queued /windows poll behind it
            # times out. SWP_ASYNCWINDOWPOS (0x4000) POSTS the request to the
            # target thread and returns immediately -> the listener never blocks.
            #   SWP_NOSIZE=0x1 NOMOVE=0x2 NOZORDER=0x4 NOACTIVATE=0x10 SHOWWINDOW=0x40 ASYNC=0x4000
            'move'   { [void][OSCW32]::SetWindowPos($p, [IntPtr]::Zero, [int]$x, [int]$y, 0, 0, 0x4015) }  # async|nozorder|noactivate|nosize
            'resize' { [void][OSCW32]::SetWindowPos($p, [IntPtr]::Zero, 0, 0, [int]$w, [int]$h, 0x4016) }  # async|nozorder|noactivate|nomove
            'center' {
                Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
                $previousDpi = [IntPtr]::Zero
                try { $previousDpi = [OSCW32]::SetThreadDpiAwarenessContext([IntPtr]::new(-4)) } catch {}
                try {
                    $rect = New-Object OSCW32+RECT
                    if (-not [OSCW32]::GetWindowRect($p, [ref]$rect)) { continue }
                    $sc = [System.Windows.Forms.Screen]::FromHandle($p).WorkingArea
                    $cw = [Math]::Min([int]($rect.Right - $rect.Left), $sc.Width)
                    $ch = [Math]::Min([int]($rect.Bottom - $rect.Top), $sc.Height)
                    if ($cw -le 0 -or $ch -le 0) { continue }
                    $cx = $sc.X + [int](($sc.Width - $cw) / 2)
                    $cy = $sc.Y + [int](($sc.Height - $ch) / 2)
                    # ASYNCWINDOWPOS keeps the listener responsive during app startup.
                    [void][OSCW32]::SetWindowPos($p, [IntPtr]::Zero, $cx, $cy, $cw, $ch, 0x4044)
                } finally { if ($previousDpi -ne [IntPtr]::Zero) { [void][OSCW32]::SetThreadDpiAwarenessContext($previousDpi) } }
            }
            'position' {
                Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
                $screens = [System.Windows.Forms.Screen]::AllScreens
                $screen = $null
                if ($monitor -ge 0 -and $monitor -lt $screens.Count) {
                    $screen = $screens[$monitor]
                } else {
                    $screen = [System.Windows.Forms.Screen]::FromHandle($p)
                }
                $sc = $screen.WorkingArea
                $sw = $sc.Width
                $sh = $sc.Height
                $sx = $sc.X
                $sy = $sc.Y

                $pos = $state.ToLower()

                $rect = New-Object OSCW32+RECT
                [void][OSCW32]::GetWindowRect($p, [ref]$rect)
                $cw = [int]($rect.Right - $rect.Left)
                $ch = [int]($rect.Bottom - $rect.Top)

                $nx = $sx; $ny = $sy; $nw = $cw; $nh = $ch

                switch ($pos) {
                    'default' {
                        $phi = 1.6180339887
                        $nw = [int][Math]::Round($sw / $phi)
                        $nh = [int][Math]::Round($nw * 10.0 / 16.0)
                        if ($nw -gt $sw)  { $nw = $sw }
                        if ($nh -gt $sh) { $nh = $sh }
                        $nx = $sx + [int](($sw - $nw) / 2)
                        $ny = $sy + [int](($sh - $nh) / 2)
                    }
                    'center' {
                        $nx = $sx + [int](($sw - $cw) / 2)
                        $ny = $sy + [int](($sh - $ch) / 2)
                        $nw = $cw
                        $nh = $ch
                    }
                    'left' {
                        $nx = $sx
                        $ny = $sy
                        $nw = [int]($sw / 2)
                        $nh = $sh
                    }
                    'right' {
                        $nx = $sx + [int]($sw / 2)
                        $ny = $sy
                        $nw = [int]($sw / 2)
                        $nh = $sh
                    }
                    'top' {
                        $nx = $sx
                        $ny = $sy
                        $nw = $sw
                        $nh = [int]($sh / 2)
                    }
                    'bottom' {
                        $nx = $sx
                        $ny = $sy + [int]($sh / 2)
                        $nw = $sw
                        $nh = [int]($sh / 2)
                    }
                    'top-left' {
                        $nx = $sx
                        $ny = $sy
                        $nw = [int]($sw / 2)
                        $nh = [int]($sh / 2)
                    }
                    'top-right' {
                        $nx = $sx + [int]($sw / 2)
                        $ny = $sy
                        $nw = [int]($sw / 2)
                        $nh = [int]($sh / 2)
                    }
                    'bottom-left' {
                        $nx = $sx
                        $ny = $sy + [int]($sh / 2)
                        $nw = [int]($sw / 2)
                        $nh = [int]($sh / 2)
                    }
                    'bottom-right' {
                        $nx = $sx + [int]($sw / 2)
                        $ny = $sy + [int]($sh / 2)
                        $nw = [int]($sw / 2)
                        $nh = [int]($sh / 2)
                    }
                    'maximize' {
                        [void][OSCW32]::ShowWindow($p, 3) # SW_MAXIMIZE
                        $nx = $sx; $ny = $sy; $nw = $sw; $nh = $sh
                    }
                    default {
                        # fallback to default golden ratio centered geometry
                        $phi = 1.6180339887
                        $nw = [int][Math]::Round($sw / $phi)
                        $nh = [int][Math]::Round($nw * 10.0 / 16.0)
                        if ($nw -gt $sw)  { $nw = $sw }
                        if ($nh -gt $sh) { $nh = $sh }
                        $nx = $sx + [int](($sw - $nw) / 2)
                        $ny = $sy + [int](($sh - $nh) / 2)
                    }
                }
                if ($pos -ne 'maximize') {
                    [void][OSCW32]::SetWindowPos($p, [IntPtr]::Zero, $nx, $ny, $nw, $nh, 0x4044) # async|nozorder|showwindow
                }
            }
            'state'  {
                $n = 1
                if ($state -eq 'minimize') { $n = 6 }
                elseif ($state -eq 'maximize') { $n = 3 }
                elseif ($state -eq 'restore')  { $n = 9 }
                [void][OSCW32]::ShowWindow($p, $n)
            }
        }
        $expected = @{ x=$x; y=$y; width=$w; height=$h; state=$state }
        $readbackOp = $op
        if ($op -eq 'center') { $expected = @{x=$cx;y=$cy;width=$cw;height=$ch} }
        if ($op -eq 'position') {
            if ($pos -eq 'maximize') { $readbackOp = 'state'; $expected = @{state='maximize'} }
            else { $expected = @{x=$nx;y=$ny;width=$nw;height=$nh} }
        }
        $verified = $false
        for ($attempt=0; $attempt -lt 10; $attempt++) {
            $actual = Get-MiosWindowReadback $p
            $verified = Test-MiosWindowReadback $readbackOp $actual $expected
            if ($verified) { break }
            Start-Sleep -Milliseconds 50
        }
        if (-not $verified) { $verifiedAll = $false }
        [void]$done.Add(@{ hwnd = $wnd.hwnd; title = $wnd.title; proc = $wnd.proc; verified=$verified; readback=$actual })
    }
    $verifiedAll = $verifiedAll -and $done.Count -eq $targets.Count
    return @{ ok=$verifiedAll; verified=$verifiedAll; op=$op; count=$done.Count; matched=$done;
        reason=$(if ($verifiedAll) {'readback confirmed'} else {'requested window state was not observed'}) }
}

# ---- input (SendInput-equivalent) + capture on the interactive desktop -------
# These mirror mios-pc-control.ps1 but run IN the executor's interactive session
# so SetCursorPos / mouse_event / SendKeys hit WinSta0\Default (the operator's
# real desktop), not a blind service window station.
function Invoke-MouseMove($x, $y) {
    $point = New-Object OSCW32+POINT
    $verified = [OSCW32]::SetCursorPos([int]$x, [int]$y) -and [OSCW32]::GetCursorPos([ref]$point) -and $point.X -eq [int]$x -and $point.Y -eq [int]$y
    return @{ ok=$verified; verified=$verified; op='mouse-move'; x=[int]$x; y=[int]$y }
}

function Invoke-Click($x, $y, $button) {
    $move = Invoke-MouseMove $x $y
    if (-not $move.verified) { return @{ok=$false;error='cursor position was not observed'} }
    Start-Sleep -Milliseconds 50
    $btn = "$button"; if (-not $btn) { $btn = 'left' }
    switch ($btn) {
        'left'   { [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_LEFTDOWN,0,0,0,[IntPtr]::Zero);  [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_LEFTUP,0,0,0,[IntPtr]::Zero) }
        'right'  { [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_RIGHTDOWN,0,0,0,[IntPtr]::Zero); [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_RIGHTUP,0,0,0,[IntPtr]::Zero) }
        'middle' { [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_MIDDLEDOWN,0,0,0,[IntPtr]::Zero);[OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_MIDDLEUP,0,0,0,[IntPtr]::Zero) }
        default  { return @{ ok = $false; error = "unknown button '$btn'" } }
    }
    return @{ ok=$true; injected=$true; op='click'; button=$btn; x=[int]$x; y=[int]$y }
}

function Invoke-DoubleClick($x, $y) {
    $move = Invoke-MouseMove $x $y
    if (-not $move.verified) { return @{ok=$false;error='cursor position was not observed'} }
    Start-Sleep -Milliseconds 50
    [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_LEFTDOWN,0,0,0,[IntPtr]::Zero); [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_LEFTUP,0,0,0,[IntPtr]::Zero)
    Start-Sleep -Milliseconds 50
    [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_LEFTDOWN,0,0,0,[IntPtr]::Zero); [OSCW32]::mouse_event([OSCW32]::MOUSEEVENTF_LEFTUP,0,0,0,[IntPtr]::Zero)
    return @{ ok=$true; injected=$true; op='double-click'; x=[int]$x; y=[int]$y }
}

# ── UIA semantic element targeting (the #1 Windows gap --
# Linux has AT-SPI, Windows control was pixel-only). Find a control BY NAME via UI
# Automation, scoped to the FOREGROUND window's subtree (fast; avoids a whole-
# desktop tree walk that can hang), returning its clickable CENTER so the agent
# acts on a SEMANTIC target instead of guessed pixels. NOT arbitrary code exec --
# only enumerates + acts on the active window's accessibility tree.
$script:UIA_OK = $false
try {
    Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
    Add-Type -AssemblyName UIAutomationTypes  -ErrorAction Stop
    $script:UIA_OK = $true
} catch { $script:UIA_OK = $false }

function Find-UIElements($name, $maxN) {
    if (-not $script:UIA_OK) { return @() }
    if (-not $maxN -or $maxN -le 0) { $maxN = 15 }
    $fg = [OSCW32]::GetForegroundWindow()
    $root = $null
    try { if ($fg -ne [IntPtr]::Zero) { $root = [System.Windows.Automation.AutomationElement]::FromHandle($fg) } } catch {}
    if (-not $root) { return @() } # DO NOT fall back to RootElement with Descendants!
    $out = @()
    try {
        $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                             [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($el in $all) {
            $nm = ''
            try { $nm = [string]$el.Current.Name } catch {}
            if (-not $nm) { continue }
            if ($name -and ($nm -notlike "*$name*")) { continue }
            $rect = $null
            try { $rect = $el.Current.BoundingRectangle } catch {}
            if (-not $rect -or $rect.Width -le 0 -or $rect.Height -le 0) { continue }
            $ct = ''; $aid = ''
            try { $ct = [string]$el.Current.ControlType.ProgrammaticName } catch {}
            try { $aid = [string]$el.Current.AutomationId } catch {}
            $out += @{ name = $nm; control_type = $ct; automation_id = $aid;
                       x = [int]$rect.X; y = [int]$rect.Y; w = [int]$rect.Width; h = [int]$rect.Height;
                       cx = [int]($rect.X + $rect.Width / 2); cy = [int]($rect.Y + $rect.Height / 2) }
            if ($out.Count -ge $maxN) { break }
        }
    } catch {}
    return $out
}

function Get-UIATree {
    param(
        [System.Windows.Automation.AutomationElement]$Element,
        [int]$Depth = 0,
        [int]$MaxDepth = 15
    )
    if ($Depth -gt $MaxDepth) { return $null }
    if ($null -eq $Element) { return $null }

    $node = [ordered]@{
        Name = ''
        ControlType = ''
        AutomationId = ''
        Rect = $null
    }

    try { $node.Name = [string]$Element.Current.Name } catch {}
    try { $node.ControlType = [string]$Element.Current.ControlType.ProgrammaticName.Replace('ControlType.', '') } catch {}
    try { $node.AutomationId = [string]$Element.Current.AutomationId } catch {}
    $isOffscreen = $false
    try { $isOffscreen = [bool]$Element.Current.IsOffscreen } catch {}

    try {
        $rect = $Element.Current.BoundingRectangle
        if (-not $rect.IsEmpty) {
            $node.Rect = @{ X = [int]$rect.X; Y = [int]$rect.Y; W = [int]$rect.Width; H = [int]$rect.Height }
        }
    } catch {}

    # Skip invisible elements to reduce token bloat for the LLM
    if ($isOffscreen -and $Depth -gt 0) { return $null }

    $children = New-Object System.Collections.ArrayList
    try {
        # ControlViewWalker skips purely decorative elements and layouts
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $child = $walker.GetFirstChild($Element)
        $maxSiblings = 500
        $i = 0
        while ($null -ne $child -and $i -lt $maxSiblings) {
            $i++
            $childNode = Get-UIATree -Element $child -Depth ($Depth + 1) -MaxDepth $MaxDepth
            if ($null -ne $childNode) {
                [void]$children.Add($childNode)
            }
            $child = $walker.GetNextSibling($child)
        }
    } catch {}

    if ($children.Count -gt 0) {
        $node.Children = @($children)
    }

    $interactive = @('Button', 'Edit', 'Document', 'MenuItem', 'ListItem', 'TabItem', 'Hyperlink', 'CheckBox', 'RadioButton', 'ComboBox', 'TreeItem')
    if ($children.Count -eq 0 -and $node.ControlType -notin $interactive -and -not $node.Name) {
        return $null
    }

    return $node
}

function Invoke-UIASetValue($name, $text) {
    if (-not $script:UIA_OK) { return @{ ok = $false; error = 'UIA unavailable on this host' } }
    # Build the search roots: the foreground window FIRST (fast path), then
    # EVERY top-level window on the desktop. A just-launched app (e.g. Notepad)
    # is frequently not yet the foreground window by the time this call runs, so
    # relying on GetForegroundWindow alone returned "no foreground window" and
    # the type never landed. Searching all windows for the editable control --
    # then activating its window -- makes set-value land regardless of focus.
    $roots = New-Object System.Collections.Generic.List[object]
    $fg = [OSCW32]::GetForegroundWindow()
    if ($fg -ne [IntPtr]::Zero) {
        try { $r = [System.Windows.Automation.AutomationElement]::FromHandle($fg); if ($r) { $roots.Add($r) } } catch {}
    }
    try {
        $wins = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $wins) { $roots.Add($w) }
    } catch {}
    if ($roots.Count -eq 0) { return @{ ok = $false; error = "no windows found on desktop" } }

    $target = $null; $targetRoot = $null
    foreach ($root in $roots) {
        try {
            $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($el in $all) {
                $nm = ''; $aid = ''
                try { $nm = [string]$el.Current.Name } catch {}
                try { $aid = [string]$el.Current.AutomationId } catch {}
                if (($name -and ($nm -like "*$name*")) -or ($name -and ($aid -eq $name))) {
                    # Require an editable ValuePattern so we skip labels/read-only
                    # matches and only accept a control we can actually set.
                    $vpc = $null
                    if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vpc) `
                            -and -not $vpc.Current.IsReadOnly) {
                        $target = $el; $targetRoot = $root; break
                    }
                }
            }
        } catch {}
        if ($target) { break }
    }

    if (-not $target) {
        return @{ ok = $false; error = "no editable UIA element matching '$name'" }
    }

    # Bring the target's window to the foreground so the input lands and the
    # operator SEES it (satisfy the foreground lock via AllowSetForegroundWindow).
    try {
        $h = [IntPtr]$targetRoot.Current.NativeWindowHandle
        if ($h -ne [IntPtr]::Zero) {
            [void][OSCW32]::ShowWindow($h, 9)                 # SW_RESTORE
            [void][OSCW32]::AllowSetForegroundWindow(-1)      # ASFW_ANY
            [void][OSCW32]::SetForegroundWindow($h)
        }
    } catch {}

    $vp = $null
    if ($target.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
        if (-not $vp.Current.IsReadOnly) {
            try {
                $vp.SetValue($text)
                return @{ ok = $true; op = 'uia-set-value'; method = 'uia_setvalue'; name = $name; chars = $text.Length }
            } catch {
                return @{ ok = $false; error = "SetValue threw: $($_.Exception.Message)" }
            }
        } else {
            return @{ ok = $false; error = "element is ReadOnly" }
        }
    }
    return @{ ok = $false; error = "element does not support ValuePattern" }
}

# Read-back helpers for type verification: the FOREGROUND-window title and the
# focused control's text (UIA Value/Text pattern). Used to confirm typed text
# ACTUALLY landed, so the executor never reports a false success.
function Get-FgTitleRaw {
    $sb = New-Object System.Text.StringBuilder 512
    [void][OSCW32]::GetWindowText([OSCW32]::GetForegroundWindow(), $sb, 512)
    return $sb.ToString()
}
function Get-FocusedTextRaw {
    if (-not $script:UIA_OK) { return $null }
    try {
        $fe = [System.Windows.Automation.AutomationElement]::FocusedElement
        if ($null -eq $fe) { return $null }
        $vp = $null
        if ($fe.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
            return $vp.Current.Value
        }
        $tp = $null
        if ($fe.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) {
            return $tp.DocumentRange.GetText(4096)
        }
    } catch { }
    return $null
}

function Get-FocusedValueElement {
    # The focused element IF it exposes a WRITABLE UIA ValuePattern (so we can set
    # text directly, no keystrokes). $null when there is no focused element or it is
    # read-only / has no ValuePattern (e.g. Win11 Notepad's Pane).
    if (-not $script:UIA_OK) { return $null }
    try {
        $fe = [System.Windows.Automation.AutomationElement]::FocusedElement
        if ($null -eq $fe) { return $null }
        $vp = $null
        if ($fe.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
            if (-not $vp.Current.IsReadOnly) { return @{ el = $fe; vp = $vp } }
        }
    } catch { }
    return $null
}

function Resolve-EditElement($hwnd) {
    if (-not $script:UIA_OK) { return $null }
    try {
        $win = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
        if ($null -eq $win) { return $null }
        $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($el in $all) {
            $cur = $el.Current
            if ($cur.IsEnabled -and -not $cur.IsOffscreen) {
                if ($cur.ControlType -eq [System.Windows.Automation.ControlType]::Edit -or $cur.ControlType -eq [System.Windows.Automation.ControlType]::Document) {
                    $vp = $null
                    if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
                        if (-not $vp.Current.IsReadOnly) {
                            return @{ el = $el; vp = $vp; type = 'value' }
                        }
                    }
                    $tp = $null
                    if ($el.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) {
                        return @{ el = $el; tp = $tp; type = 'text' }
                    }
                }
            }
        }
        foreach ($el in $all) {
            $cur = $el.Current
            if ($cur.IsEnabled -and -not $cur.IsOffscreen) {
                $vp = $null
                if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
                    if (-not $vp.Current.IsReadOnly) {
                        return @{ el = $el; vp = $vp; type = 'value' }
                    }
                }
                $tp = $null
                if ($el.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) {
                    return @{ el = $el; tp = $tp; type = 'text' }
                }
            }
        }
    } catch { }
    return $null
}

function Invoke-TypeText($text, $hwnd = $null) {
    $t = "$text"
    $titleBefore = Get-FgTitleRaw
    $valBefore = Get-FocusedTextRaw
    $method = 'none'

    if ($null -ne $hwnd -and $hwnd -ne 0) {
        $target = Resolve-EditElement $hwnd
        if ($null -eq $target) {
            return @{ ok = $false; verified = $false; op = 'type'; method = 'none';
                      reason = 'no_edit_control_in_target';
                      detail = "No editable UIA control found in target window (hwnd=$hwnd)";
                      chars = $t.Length }
        }

        try {
            $h = [IntPtr]$hwnd
            [void][OSCW32]::ShowWindow($h, 9)                 # SW_RESTORE
            [void][OSCW32]::AllowSetForegroundWindow(-1)      # ASFW_ANY
            [void][OSCW32]::SetForegroundWindow($h)
            Start-Sleep -Milliseconds 120
            $target.el.SetFocus()
            Start-Sleep -Milliseconds 120
        } catch {}

        if ($target.type -eq 'value') {
            try {
                $target.vp.SetValue($t)
                $method = 'uia_setvalue'
                Start-Sleep -Milliseconds 120
            } catch {
                $method = 'none'
            }
        }

        if ($method -eq 'none') {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
            $escaped = ($t -replace '([+\^%~(){}\[\]])', '{$1}')
            Start-Sleep -Milliseconds 250
            try {
                [System.Windows.Forms.SendKeys]::SendWait($escaped)
                $method = 'keystroke'
            } catch {
                $msg = "$($_.Exception.Message)"
                $detail = if ($msg -match 'Access is denied') {
                    'input injection blocked -- the interactive desktop session is likely DISCONNECTED/locked, or an ELEVATED window holds the foreground (UIPI). Reconnect the session (or run the executor elevated) to enable typing.'
                } else { $msg }
                return @{ ok = $false; verified = $false; op = 'type'; method = 'keystroke';
                          reason = 'input_injection_blocked'; detail = $detail;
                          error = $msg; chars = $t.Length }
            }
            Start-Sleep -Milliseconds 400
        }

        $valAfter = $null
        try {
            if ($target.type -eq 'value') {
                $valAfter = $target.vp.Current.Value
            } else {
                $valAfter = $target.tp.DocumentRange.GetText(4096)
            }
        } catch {}

        $titleAfter = Get-FgTitleRaw
        $verified = $false
        $reason = 'text_not_delivered'
        if (($null -ne $valAfter) -and $valAfter.Contains($t)) {
            $verified = $true; $reason = 'uia_value_contains_text'
        } elseif (($titleAfter -ne $titleBefore) -and $titleAfter.Contains($t)) {
            $verified = $true; $reason = 'title_contains_text'
        } else {
            $reason = 'text_mismatch_partial_or_dropped'
        }

        $vc = ''
        if ($null -ne $valAfter) { $vc = $valAfter.Substring(0, [Math]::Min(160, $valAfter.Length)) }
        return @{ ok = $verified; verified = $verified; op = 'type'; method = $method;
                  reason = $reason; chars = $t.Length; title_before = $titleBefore;
                  title_after = $titleAfter; focused_text_after = $vc }
    }

    $ve = Get-FocusedValueElement
    if ($null -ne $ve) {
        try {
            $ve.vp.SetValue($t)
            $method = 'uia_setvalue'
            Start-Sleep -Milliseconds 120
        } catch { $method = 'none' }
    }
    if ($method -ne 'uia_setvalue') {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $escaped = ($t -replace '([+\^%~(){}\[\]])', '{$1}')
        Start-Sleep -Milliseconds 250
        try {
            [System.Windows.Forms.SendKeys]::SendWait($escaped)
            $method = 'keystroke'
        } catch {
            $msg = "$($_.Exception.Message)"
            $detail = if ($msg -match 'Access is denied') {
                'input injection blocked -- the interactive desktop session is likely DISCONNECTED/locked, or an ELEVATED window holds the foreground (UIPI). Reconnect the session (or run the executor elevated) to enable typing.'
            } else { $msg }
            return @{ ok = $false; verified = $false; op = 'type'; method = 'keystroke';
                      reason = 'input_injection_blocked'; detail = $detail;
                      error = $msg; chars = $t.Length }
        }
        Start-Sleep -Milliseconds 400
    }

    $titleAfter = Get-FgTitleRaw
    $valAfter = Get-FocusedTextRaw
    $verified = $false
    $reason = 'text_not_delivered'
    if (($null -ne $valAfter) -and $valAfter.Contains($t)) {
        $verified = $true; $reason = 'uia_value_contains_text'
    } elseif (($titleAfter -ne $titleBefore) -and $titleAfter.Contains($t)) {
        $verified = $true; $reason = 'title_contains_text'
    } elseif (($null -eq $valAfter) -and ($titleAfter -eq $titleBefore)) {
        $reason = 'no_verifiable_target'
    } else {
        $reason = 'text_mismatch_partial_or_dropped'
    }

    $vc = ''
    if ($null -ne $valAfter) { $vc = $valAfter.Substring(0, [Math]::Min(160, $valAfter.Length)) }
    return @{ ok = $verified; verified = $verified; op = 'type'; method = $method;
              reason = $reason; chars = $t.Length; title_before = $titleBefore;
              title_after = $titleAfter; focused_text_after = $vc }
}

function Invoke-Key($name) {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $sk = switch -Regex ("$name") {
        '^(Enter|Return)$' { '{ENTER}' }
        '^(Tab)$'          { '{TAB}' }
        '^(Esc(ape)?)$'    { '{ESC}' }
        '^(Backspace|BS)$' { '{BACKSPACE}' }
        '^(Delete|Del)$'   { '{DELETE}' }
        '^Up$'             { '{UP}' }
        '^Down$'           { '{DOWN}' }
        '^Left$'           { '{LEFT}' }
        '^Right$'          { '{RIGHT}' }
        '^Home$'           { '{HOME}' }
        '^End$'            { '{END}' }
        '^F\d+$'           { "{$name}" }
        default            { "$name" }
    }
    [System.Windows.Forms.SendKeys]::SendWait($sk)
    return @{ ok = $true; op = 'key'; key = "$name"; sent = $sk }
}

function Invoke-KeyCombo($combo) {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $parts = ("$combo" -split '\+')
    $key = $parts[-1]
    $mods = ''
    if ($parts.Count -ge 2) {
        foreach ($m in $parts[0..($parts.Count - 2)]) {
            switch ($m.ToLower()) {
                'ctrl'  { $mods += '^' }
                'alt'   { $mods += '%' }
                'shift' { $mods += '+' }
                'win'   { $mods += '#' }
            }
        }
    }
    [System.Windows.Forms.SendKeys]::SendWait($mods + $key)
    return @{ ok = $true; op = 'key-combo'; combo = "$combo" }
}

function Get-ScreenLayout {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    $screens = [System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
        @{ device = $_.DeviceName; primary = [bool]$_.Primary;
           bounds = @{ x = $_.Bounds.X; y = $_.Bounds.Y; width = $_.Bounds.Width; height = $_.Bounds.Height };
           work   = @{ x = $_.WorkingArea.X; y = $_.WorkingArea.Y; width = $_.WorkingArea.Width; height = $_.WorkingArea.Height } }
    }
    return @{ ok = $true; count = @($screens).Count; screens = @($screens) }
}

function Invoke-Screenshot {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing -ErrorAction SilentlyContinue
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.X, $b.Y, 0, 0, $b.Size)
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    $b64 = [Convert]::ToBase64String($ms.ToArray())
    $ms.Dispose()
    return @{ ok = $true; format = 'png'; width = $b.Width; height = $b.Height; image_b64 = $b64 }
}

# Resolve an app name to a launch target. Returns the thing Start-Process can
# take: a Start-Menu .lnk path if one matches, else the bare name (Start-Process
# handles exe-on-PATH, registered apps, protocols/URIs like ms-settings:).
# Cached index of Start-Menu .lnks: @(@{Name;Path;Wslg}). The recursive scan +
# per-shortcut COM target-resolution (for the WSLg-skip) is the per-launch cost
# the executor-first routing added; cache it with a TTL so repeat launches are
# instant ("streamlined, fast and perfected"). Installs are
# rare -> a 60s TTL is safe; a cold cache (first launch / post-install) pays the
# full scan once. script: scope so it survives across requests.
function Get-StartMenuIndex {
    $now = [DateTime]::UtcNow
    if ($script:OSCLnkIdx -and $script:OSCLnkTs -and
        (($now - $script:OSCLnkTs).TotalSeconds -le 60)) {
        return $script:OSCLnkIdx
    }
    $menus = @(
        (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'),
        (Join-Path $env:APPDATA      'Microsoft\Windows\Start Menu\Programs')
    )
    $wsh = New-Object -ComObject WScript.Shell
    $idx = New-Object System.Collections.ArrayList
    foreach ($m in $menus) {
        if (-not (Test-Path $m)) { continue }
        foreach ($f in (Get-ChildItem -Path $m -Recurse -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
            $t = ''
            try { $t = $wsh.CreateShortcut($f.FullName).TargetPath } catch {}
            [void]$idx.Add(@{ Name = $f.BaseName; Path = $f.FullName;
                              Wslg = (($t -like '*wslg.exe') -or ($t -like '*wsl.exe')) })
        }
    }
    $script:OSCLnkIdx = $idx
    $script:OSCLnkTs  = $now
    return $idx
}

function Resolve-LaunchTarget($name) {
    $n = $name.Trim()
    if (-not $n) { return $null }
    # Direct path / protocol / URL -> hand straight to Start-Process.
    if ($n -match '^[a-zA-Z]:\\' -or $n -match '^[a-zA-Z][a-zA-Z0-9+.\-]*:' -or (Test-Path $n -ErrorAction SilentlyContinue)) {
        return $n
    }
    # For known browsers, return bare .exe so Windows resolves it via App Paths registry,
    # which natively reuses the running instance and opens a tab instead of a new window.
    $lower = $n.ToLower()
    if ($lower -eq 'firefox') { return 'firefox.exe' }
    if ($lower -eq 'chrome') { return 'chrome.exe' }
    if ($lower -eq 'msedge' -or $lower -eq 'edge') { return 'msedge.exe' }
    if ($lower -eq 'brave') { return 'brave.exe' }
    if ($lower -eq 'opera') { return 'opera.exe' }

    # Match the cached Start-Menu index, SKIPPING WSLg-exported shortcuts
    # (target = WSL\wslg.exe -- those launch a LINUX flatpak through WSLg, which
    # on the executor is slow + bypasses the operator's trained flatpak path;
    # letting the executor MISS makes mios-launch fall back to Linux). A NATIVE
    # Windows app .lnk targets a real .exe and is kept -- "codium" picks the
    # native VSCodium over the WSLg one; "discord"/"notepad" resolve normally.
    # Prefer the SHORTEST matching BaseName.
    $cand = Get-StartMenuIndex |
            Where-Object { (-not $_.Wslg) -and ($_.Name -like "*$n*") } |
            Sort-Object { $_.Name.Length } | Select-Object -First 1
    if ($cand) { return $cand.Path }
    return $n   # let Start-Process try it as a PATH exe / registered app
}

# FIRE a launch on this node's desktop, then poll for the window.
function Invoke-LaunchAndVerify($app, $appArgs = '', $position = 'default', $verifyName = '') {
    # Window-match needle: prefer the human title hint -- a steam:// URI or a
    # shell:appsFolder string never appears in a window title, so verifying a
    # game by $app would always miss. Falls back to $app.
    $wname = if ([string]::IsNullOrWhiteSpace($verifyName)) { $app } else { $verifyName }
    $target = Resolve-LaunchTarget $app
    if ($target -like "*steam.exe" -or $target -like "*steam.lnk" -or $app -eq "steam") {
        if ($appArgs -and ($appArgs -match 'big-?picture')) {
            $target = "steam://open/bigpicture"
            $appArgs = ""
            $wname = "Steam"
        }
    }
    $fired  = $false
    $fireErr = ''
    if ($target) {
        try {
            if ($target -like 'shell:*') {
                # UWP / Store / Xbox apps: Start-Process can't grok shell: URIs
                # (treats them as filesystem paths); explorer.exe is the handler.
                Start-Process -FilePath 'explorer.exe' -ArgumentList $target -ErrorAction Stop
            } else {
                # exe paths, names, AND protocol URIs (steam:// epic:// uplay://
                # ...) -- Start-Process invokes the registered protocol handler.
                if ($appArgs) {
                    Start-Process -FilePath $target -ArgumentList $appArgs -ErrorAction Stop
                } else {
                    Start-Process -FilePath $target -ErrorAction Stop
                }
            }
            $fired = $true
        } catch { $fireErr = $_.Exception.Message }
    } else {
        $fireErr = 'could not resolve launch target'
    }
    # FAST MISS: if the launch did NOT fire (target unresolvable on Windows /
    # Start-Process threw -- e.g. a Linux-only flatpak name), return immediately
    # instead of sleeping + polling for a window that was never started. The
    # executor is now tried FIRST for every app, so a
    # non-Windows app must fall through to the Linux chain cheaply.
    if (-not $fired) {
        return @{ app = $app; node = $env:COMPUTERNAME; host = $env:COMPUTERNAME;
                  target = "$target"; fired = $false; fire_error = $fireErr;
                  launched = $false; centered = $false; positioned = $false;
                  verdict = @{ launched = $false; summary = 'not-fired' };
                  ts = [int][double]::Parse((Get-Date -UFormat %s)) }
    }
    Start-Sleep -Seconds $VerifySettleSeconds
    $verdict = @{ launched = $false; summary = 'no-window' }
    for ($i = 0; $i -lt [Math]::Max(1,$VerifyAttempts); $i++) {
        $verdict = Test-WindowPresent $wname
        if ($verdict.launched) { break }
        if ($i -lt ($VerifyAttempts - 1)) { Start-Sleep -Seconds $VerifyIntervalSeconds }
    }
    # POSITION and FOCUS the launched window (launches default
    # to focused and centered unless specified as-is / none / background).
    # Settle, position + focus, briefly sleep 900ms, and repeat to counter Electron
    # apps (Discord/Teams) that restore their saved window bounds/states a beat
    # after mapping.
    $positioned = $false
    if ($position -and $position -ne 'as-is' -and $position -ne 'none' -and $verdict.launched) {
        try {
            [void](Invoke-WindowOp 'position' $null $wname 0 0 0 0 $position)
            [void](Invoke-WindowOp 'focus' $null $wname 0 0 0 0 '')
            Start-Sleep -Milliseconds 900
            [void](Invoke-WindowOp 'position' $null $wname 0 0 0 0 $position)
            [void](Invoke-WindowOp 'focus' $null $wname 0 0 0 0 '')
            $positioned = $true
        } catch {}
    }
    return @{
        app      = $app
        node     = $env:COMPUTERNAME
        host     = $env:COMPUTERNAME
        target   = "$target"
        fired    = $fired
        fire_error = $fireErr
        launched = [bool]$verdict.launched
        centered = $positioned
        positioned = $positioned
        verdict  = $verdict
        ts       = [int][double]::Parse((Get-Date -UFormat %s))
    }
}

# ---- minimal query-string parser (System.Web not guaranteed on 5.1) ----------
function Get-QueryValue($rawUrl, $key) {
    $q = ''
    $idx = $rawUrl.IndexOf('?')
    if ($idx -ge 0) { $q = $rawUrl.Substring($idx + 1) }
    foreach ($pair in $q.Split('&')) {
        $kv = $pair.Split('=', 2)
        if ($kv.Length -eq 2 -and $kv[0] -eq $key) {
            return [Uri]::UnescapeDataString($kv[1])
        }
    }
    return ''
}

function Read-JsonBody($ctx) {
    $body = ''
    $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, $ctx.Request.ContentEncoding)
    try { $body = $reader.ReadToEnd() } finally { $reader.Close() }
    if (-not $body) { return $null }
    try { return ($body | ConvertFrom-Json) } catch { return $null }
}

function Write-JsonResponse($ctx, $code, $obj) {
    $json = $obj | ConvertTo-Json -Depth 6 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $ctx.Response.StatusCode = $code
    $ctx.Response.ContentType = 'application/json'
    $ctx.Response.ContentLength64 = $bytes.Length
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.OutputStream.Close()
}

# ---- HttpListener loop -------------------------------------------------------
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
try {
    $listener.Start()
} catch {
    Warn "HttpListener could not bind loopback port $Port (need elevation or a loopback urlacl)."
    throw
}
Ok "MiOS OS-control executor listening on http://127.0.0.1:$Port/"
$logFile = Join-Path $logDir ("oscontrol-{0:yyyyMMdd}.log" -f (Get-Date))

while ($listener.IsListening) {
    $ctx = $null
    try { $ctx = $listener.GetContext() } catch { break }
    try {
        $method = $ctx.Request.HttpMethod
        $path   = $ctx.Request.Url.AbsolutePath.TrimEnd('/')
        if ($path -eq '') { $path = '/' }

        if ($method -eq 'GET' -and $path -eq '/health') {
            Write-JsonResponse $ctx 200 @{ ok = $true; implementation = 'powershell-win32'; capabilities = @('windows','launch','window','input','screenshot','screen-layout','ui'); host = $env:COMPUTERNAME;
                                           ts = [int][double]::Parse((Get-Date -UFormat %s)) }
        }
        elseif ($method -eq 'GET' -and $path -eq '/verify') {
            $app = Get-QueryValue $ctx.Request.Url.OriginalString 'app'
            if (-not $app) { Write-JsonResponse $ctx 400 @{ error = 'missing ?app=' } }
            else {
                $v = Test-WindowPresent $app
                Write-JsonResponse $ctx 200 @{ app = $app; launched = [bool]$v.launched;
                                               verdict = $v; host = $env:COMPUTERNAME }
            }
        }
        elseif ($method -eq 'POST' -and $path -eq '/launch') {
            $body = ''
            $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, $ctx.Request.ContentEncoding)
            try { $body = $reader.ReadToEnd() } finally { $reader.Close() }
            $app = ''; $args = ''; $position = 'default'; $verify = ''
            if ($body) {
                try {
                    $j = $body | ConvertFrom-Json
                    $app = ([string]$j.app).Trim()
                    if ($j.PSObject.Properties.Name -contains 'args') {
                        $args = ([string]$j.args)
                    }
                    if ($j.PSObject.Properties.Name -contains 'position') {
                        $position = ([string]$j.position).Trim().ToLower()
                    } elseif ($j.PSObject.Properties.Name -contains 'center') {
                        if (-not [bool]$j.center) {
                            $position = 'as-is'
                        }
                    }
                    if ($j.PSObject.Properties.Name -contains 'verify') {
                        $verify = ([string]$j.verify).Trim()
                    }
                } catch { $app = ''; $args = ''; $position = 'default' }
            }
            if (-not $app) { Write-JsonResponse $ctx 400 @{ error = "missing 'app' in JSON body" } }
            else {
                $r = Invoke-LaunchAndVerify $app $args $position $verify
                ("{0}  launch app={1} fired={2} launched={3}" -f (Get-Date -Format s), $app, $r.fired, $r.launched) |
                    Out-File -FilePath $logFile -Append -Encoding utf8
                Write-JsonResponse $ctx 200 $r
            }
        }
        elseif ($method -eq 'GET' -and $path -eq '/windows') {
            # Enumerate ALL visible top-level windows (the grounding snapshot
            # the agent records before/after open+close to diff what changed).
            $wins = @(Get-VisibleWindows)
            Write-JsonResponse $ctx 200 @{ ok = $true; count = $wins.Count;
                                           windows = $wins; host = $env:COMPUTERNAME }
        }
        elseif ($method -eq 'POST' -and $path -like '/window/*') {
            $op = $path.Substring('/window/'.Length)
            $b  = Read-JsonBody $ctx
            $hwnd  = $null; $title = ''; $x = 0; $y = 0; $ww = 0; $hh = 0; $state = 'restore'; $monitor = -1
            if ($b) {
                if ($b.PSObject.Properties.Name -contains 'hwnd'  -and $b.hwnd)  { $hwnd = [int64]$b.hwnd }
                if ($b.PSObject.Properties.Name -contains 'title') { $title = "$($b.title)" }
                if ($b.PSObject.Properties.Name -contains 'x')      { $x  = [int]$b.x }
                if ($b.PSObject.Properties.Name -contains 'y')      { $y  = [int]$b.y }
                if ($b.PSObject.Properties.Name -contains 'width')  { $ww = [int]$b.width }
                if ($b.PSObject.Properties.Name -contains 'height') { $hh = [int]$b.height }
                if ($b.PSObject.Properties.Name -contains 'state')  { $state = "$($b.state)" }
                if ($b.PSObject.Properties.Name -contains 'monitor') { $monitor = [int]$b.monitor }
            }
            if ($op -notin @('close','focus','move','resize','state','center','position')) {
                Write-JsonResponse $ctx 404 @{ error = "unknown window op '$op'" }
            }
            elseif (-not $hwnd -and -not $title.Trim()) {
                Write-JsonResponse $ctx 400 @{ error = "need 'hwnd' or 'title'" }
            }
            else {
                $r = Invoke-WindowOp $op $hwnd $title $x $y $ww $hh $state $monitor
                ("{0}  window {1} title='{2}' hwnd={3} count={4}" -f (Get-Date -Format s), $op, $title, $hwnd, $r.count) |
                    Out-File -FilePath $logFile -Append -Encoding utf8
                $code = 200
                Write-JsonResponse $ctx $code $r
            }
        }
        elseif ($method -eq 'GET' -and $path -eq '/screen-layout') {
            Write-JsonResponse $ctx 200 (Get-ScreenLayout)
        }
        elseif ($method -eq 'GET' -and $path -eq '/screenshot') {
            Write-JsonResponse $ctx 200 (Invoke-Screenshot)
        }
        elseif ($method -eq 'POST' -and $path -like '/input/*') {
            $op = $path.Substring('/input/'.Length)
            $b  = Read-JsonBody $ctx
            $x = 0; $y = 0; $text = ''; $name = ''; $combo = ''; $button = 'left'; $hwnd = $null
            if ($b) {
                if ($b.PSObject.Properties.Name -contains 'x')      { $x = [int]$b.x }
                if ($b.PSObject.Properties.Name -contains 'y')      { $y = [int]$b.y }
                if ($b.PSObject.Properties.Name -contains 'text')   { $text = "$($b.text)" }
                if ($b.PSObject.Properties.Name -contains 'name')   { $name = "$($b.name)" }
                if ($b.PSObject.Properties.Name -contains 'combo')  { $combo = "$($b.combo)" }
                if ($b.PSObject.Properties.Name -contains 'button') { $button = "$($b.button)" }
                if ($b.PSObject.Properties.Name -contains 'hwnd')   { $hwnd = [int]$b.hwnd }
            }
            $r = $null
            switch ($op) {
                'mouse-move'   { $r = Invoke-MouseMove $x $y }
                'click'        { $r = Invoke-Click $x $y $button }
                'double-click' { $r = Invoke-DoubleClick $x $y }
                'type'         { $r = Invoke-TypeText $text $hwnd }
                'key'          { $r = Invoke-Key $name }
                'key-combo'    { $r = Invoke-KeyCombo $combo }
                default        { $r = @{ ok = $false; error = "unknown input op '$op'" } }
            }
            $code = 200
            ("{0}  input {1}" -f (Get-Date -Format s), $op) | Out-File -FilePath $logFile -Append -Encoding utf8
            Write-JsonResponse $ctx $code $r
        }
        elseif ($method -eq 'POST' -and ($path -eq '/ui/find' -or $path -eq '/ui/click')) {
            # UIA semantic targeting: /ui/find lists matching controls in the
            # foreground window; /ui/click finds the first match + clicks its
            # center. Element-targeting only -- no arbitrary code execution.
            $b = Read-JsonBody $ctx
            $name = ''
            if ($b -and ($b.PSObject.Properties.Name -contains 'name')) { $name = "$($b.name)" }
            if (-not $name.Trim()) {
                Write-JsonResponse $ctx 400 @{ ok = $false; error = "missing 'name'" }
            }
            elseif (-not $script:UIA_OK) {
                Write-JsonResponse $ctx 200 @{ ok = $false; error = 'UIA unavailable on this host'; host = $env:COMPUTERNAME }
            }
            elseif ($path -eq '/ui/find') {
                $els = @(Find-UIElements $name 15)
                Write-JsonResponse $ctx 200 @{ ok = ($els.Count -gt 0); count = $els.Count; elements = $els; host = $env:COMPUTERNAME }
            }
            else {
                $els = @(Find-UIElements $name 1)
                if ($els.Count -eq 0) {
                    Write-JsonResponse $ctx 404 @{ ok = $false; error = "no UIA element matching '$name' in the foreground window"; host = $env:COMPUTERNAME }
                }
                else {
                    $e = $els[0]
                    $click = Invoke-Click $e.cx $e.cy 'left'
                    ("{0}  ui-click name='{1}' -> ({2},{3})" -f (Get-Date -Format s), $name, $e.cx, $e.cy) | Out-File -FilePath $logFile -Append -Encoding utf8
                    Write-JsonResponse $ctx 200 @{ ok=($click.ok -eq $true); injected=($click.injected -eq $true); element=$e; host=$env:COMPUTERNAME }
                }
            }
        }
        elseif ($method -eq 'GET' -and $path -eq '/ui/list') {
            # SoM-first grounding: list ALL named controls in the
            # FOREGROUND window (the UIA tree AS the set-of-marks) so the agent surveys
            # them + picks one to click by name/center -- coordinate-free, no VLM needed
            # for UIA surfaces. (Vision-overlay SoM for non-UIA canvas surfaces is the
            # remaining fallback.)
            if (-not $script:UIA_OK) {
                Write-JsonResponse $ctx 200 @{ ok = $false; error = 'UIA unavailable on this host'; host = $env:COMPUTERNAME }
            }
            else {
                $els = @(Find-UIElements '' 40)
                for ($i = 0; $i -lt $els.Count; $i++) { $els[$i].mark = $i + 1 }
                Write-JsonResponse $ctx 200 @{ ok = ($els.Count -gt 0); count = $els.Count; elements = $els; host = $env:COMPUTERNAME }
            }
        }
        elseif ($method -eq 'GET' -and $path -eq '/ui/tree') {
            if (-not $script:UIA_OK) {
                Write-JsonResponse $ctx 200 @{ ok = $false; error = 'UIA unavailable on this host'; host = $env:COMPUTERNAME }
            } else {
                $fg = [OSCW32]::GetForegroundWindow()
                $root = $null
                try { if ($fg -ne [IntPtr]::Zero) { $root = [System.Windows.Automation.AutomationElement]::FromHandle($fg) } } catch {}
                if (-not $root) {
                    Write-JsonResponse $ctx 404 @{ ok = $false; error = "no foreground window"; host = $env:COMPUTERNAME }
                } else {
                    $tree = Get-UIATree -Element $root -MaxDepth 15
                    if ($null -ne $tree) {
                        Write-JsonResponse $ctx 200 @{ ok = $true; tree = $tree; host = $env:COMPUTERNAME }
                    } else {
                        Write-JsonResponse $ctx 404 @{ ok = $false; error = "failed to extract UIA tree"; host = $env:COMPUTERNAME }
                    }
                }
            }
        }
        elseif ($method -eq 'POST' -and $path -eq '/ui/set-value') {
            $b = Read-JsonBody $ctx
            $name = ''; $text = ''
            if ($b) {
                if ($b.PSObject.Properties.Name -contains 'name') { $name = "$($b.name)" }
                if ($b.PSObject.Properties.Name -contains 'text') { $text = "$($b.text)" }
            }
            if (-not $name.Trim()) {
                Write-JsonResponse $ctx 400 @{ ok = $false; error = "missing 'name'" }
            } else {
                $r = Invoke-UIASetValue $name $text
                $code = if ($r.ok) { 200 } else { 400 }
                Write-JsonResponse $ctx $code $r
            }
        }
        else {
            Write-JsonResponse $ctx 404 @{ error = 'not found'; path = $path; method = $method }
        }
    } catch {
        try { Write-JsonResponse $ctx 500 @{ error = "$($_.Exception.Message)" } } catch {}
    }
}
