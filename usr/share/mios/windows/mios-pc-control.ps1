# AI-hint: Windows-side execution bridge for MiOS-Agent to perform GUI interactions (click, type, move, resize) and window management via Win32 API calls trigg...
# AI-doc: usr/share/doc/mios/manual/windows.md

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true, Position=0)][string]$Action,
    [switch]$Json,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Args
)

$ErrorActionPreference = 'Stop'

# ─── Win32 P/Invoke surface (loaded once) ─────────────────────────
$Win32Sig = @"
using System;
using System.Runtime.InteropServices;
public class W32 {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr i);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int n, bool repaint);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc p, IntPtr l);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern IntPtr SendMessageTimeout(IntPtr h, uint msg, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public const uint WM_CLOSE = 0x0010;
    public const uint SMTO_ABORTIFHUNG = 0x0002;
    public const uint MOUSEEVENTF_LEFTDOWN  = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP    = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP   = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN= 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP  = 0x0040;
}
"@
if (-not ([System.Management.Automation.PSTypeName]'W32').Type) {
    Add-Type -TypeDefinition $Win32Sig -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
}

# ─── Subcommand dispatch ─────────────────────────────────────────
switch ($Action) {

    'screenshot' {
        $out = $Args[0]
        if (-not $out) { throw "screenshot: missing <out-path>" }
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        try {
            $g.CopyFromScreen($bounds.X, $bounds.Y, 0, 0, $bounds.Size)
            $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $g.Dispose(); $bmp.Dispose()
        }
        Write-Output ("[mios-pc-control] screenshot saved to {0} ({1}x{2})" -f $out, $bounds.Width, $bounds.Height)
    }

    'mouse-move' {
        $x = [int]$Args[0]; $y = [int]$Args[1]
        [W32]::SetCursorPos($x, $y) | Out-Null
        Write-Output "[mios-pc-control] mouse-move $x $y"
    }

    'click' {
        $x = [int]$Args[0]; $y = [int]$Args[1]
        $btn = if ($Args.Count -ge 3) { $Args[2] } else { 'left' }
        [W32]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 50
        switch ($btn) {
            'left'   { [W32]::mouse_event([W32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero); [W32]::mouse_event([W32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero) }
            'right'  { [W32]::mouse_event([W32]::MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, [IntPtr]::Zero); [W32]::mouse_event([W32]::MOUSEEVENTF_RIGHTUP, 0, 0, 0, [IntPtr]::Zero) }
            'middle' { [W32]::mouse_event([W32]::MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, [IntPtr]::Zero); [W32]::mouse_event([W32]::MOUSEEVENTF_MIDDLEUP, 0, 0, 0, [IntPtr]::Zero) }
            default  { throw "click: unknown button '$btn'" }
        }
        Write-Output "[mios-pc-control] click $btn at ($x,$y)"
    }

    'double-click' {
        $x = [int]$Args[0]; $y = [int]$Args[1]
        [W32]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 50
        [W32]::mouse_event([W32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
        [W32]::mouse_event([W32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 50
        [W32]::mouse_event([W32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
        [W32]::mouse_event([W32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
        Write-Output "[mios-pc-control] double-click at ($x,$y)"
    }

    'type' {
        # READ-BACK VERIFICATION (the agent claimed it typed when
        # nothing reached a window -> "LIAR"). NEVER report success unless the text
        # actually landed: read the focused control value (UI Automation) and/or the
        # foreground-window title BEFORE and AFTER SendKeys. verified ONLY if the value
        # contains/grew by the sent text or the title changed; otherwise exit 1 with a
        # real reason so the orchestrator surfaces uncertainty, never a false success.
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class MiosWin {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Auto)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
}
"@
        function Get-FocusedText {
            try {
                Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue
                Add-Type -AssemblyName UIAutomationTypes -ErrorAction SilentlyContinue
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
        function Get-FgTitle {
            $sb = New-Object System.Text.StringBuilder 512
            [void][MiosWin]::GetWindowText([MiosWin]::GetForegroundWindow(), $sb, 512)
            return $sb.ToString()
        }
        $text = $Args -join ' '
        $titleBefore = Get-FgTitle
        $valBefore = Get-FocusedText
        # SendKeys interprets {} +^%~ specially; escape them.
        $escaped = ($text -replace '([+\^%~(){}\[\]])', '{$1}')
        # Settle BEFORE the first keystroke: a freshly launched/focused window often
        # is not ready the instant after focus, dropping LEADING characters (verified:
        # "DEHARD-5566" landed as "RD-5566"). A short pre-type settle lets the window's
        # input queue attach so the whole string lands.
        Start-Sleep -Milliseconds 250
        [System.Windows.Forms.SendKeys]::SendWait($escaped)
        Start-Sleep -Milliseconds 400
        $titleAfter = Get-FgTitle
        $valAfter = Get-FocusedText
        # STRICT verification: success ONLY if the EXACT sent text
        # actually appears in the focused-control value OR the foreground title (Notepad
        # shows it as "*<text> - Notepad"). A partial / dropped-keystroke result must NOT
        # pass -- "value grew" / "title changed" alone was the RESIDUAL lie (it let
        # "RD-5566" verify for "DEHARD-5566"). If neither is readable/contains it -> NOT
        # verified (exit 1) so the orchestrator can surface uncertainty / retry.
        $verified = $false
        $reason = 'text_not_delivered'
        if (($null -ne $valAfter) -and $valAfter.Contains($text)) {
            $verified = $true; $reason = 'uia_value_contains_text'
        } elseif (($titleAfter -ne $titleBefore) -and $titleAfter.Contains($text)) {
            $verified = $true; $reason = 'title_contains_text'
        } elseif (($null -eq $valAfter) -and ($titleAfter -eq $titleBefore)) {
            $reason = 'no_verifiable_target'
        } else {
            $reason = 'text_mismatch_partial_or_dropped'
        }
        $vc = ''
        if ($null -ne $valAfter) { $vc = $valAfter.Substring(0, [Math]::Min(160, $valAfter.Length)) }
        $res = [ordered]@{
            ok = $verified; verified = $verified; reason = $reason;
            chars_sent = $text.Length; title_before = $titleBefore; title_after = $titleAfter;
            focused_text_after = $vc
        }
        $json = ($res | ConvertTo-Json -Compress)
        if ($verified) {
            Write-Output ("[mios-pc-control] type verified: " + $json)
        } else {
            Write-Error ("[mios-pc-control] type NOT verified (" + $reason + "): " + $json)
            exit 1
        }
    }

    'key' {
        Add-Type -AssemblyName System.Windows.Forms
        $name = $Args[0]
        # SendKeys uses {ENTER}, {TAB}, etc. -- pass the name through.
        $sk = switch -Regex ($name) {
            '^(Enter|Return)$'  { '{ENTER}' }
            '^(Tab)$'           { '{TAB}' }
            '^(Esc(ape)?)$'     { '{ESC}' }
            '^(Backspace|BS)$'  { '{BACKSPACE}' }
            '^(Delete|Del)$'    { '{DELETE}' }
            '^Up$'              { '{UP}' }
            '^Down$'            { '{DOWN}' }
            '^Left$'            { '{LEFT}' }
            '^Right$'           { '{RIGHT}' }
            '^Home$'            { '{HOME}' }
            '^End$'             { '{END}' }
            '^F\d+$'            { "{$name}" }
            default             { $name }
        }
        [System.Windows.Forms.SendKeys]::SendWait($sk)
        Write-Output "[mios-pc-control] key $name -> $sk"
    }

    'key-combo' {
        Add-Type -AssemblyName System.Windows.Forms
        # Translate "Ctrl+C" / "Alt+F4" / "Shift+Tab" / "Win+R" -> SendKeys
        $combo = ($Args -join ' ')
        $parts = $combo -split '\+'
        $key = $parts[-1]
        $mods = ''
        foreach ($m in $parts[0..($parts.Count-2)]) {
            switch ($m.ToLower()) {
                'ctrl'  { $mods += '^' }
                'alt'   { $mods += '%' }
                'shift' { $mods += '+' }
                'win'   { $mods += '#' }   # SendKeys doesn't support Win; will silently no-op on older .NET
            }
        }
        [System.Windows.Forms.SendKeys]::SendWait($mods + $key)
        Write-Output "[mios-pc-control] key-combo $combo"
    }

    'window-list' {
        $rows = @()
        $callback = [W32+EnumWindowsProc]{
            param($h, $l)
            if ([W32]::IsWindowVisible($h)) {
                $len = [W32]::GetWindowTextLength($h)
                if ($len -gt 0) {
                    $sb = New-Object System.Text.StringBuilder ($len + 1)
                    [W32]::GetWindowText($h, $sb, $sb.Capacity) | Out-Null
                    $rect = New-Object W32+RECT
                    [W32]::GetWindowRect($h, [ref]$rect) | Out-Null
                    $proc_pid = 0
                    [W32]::GetWindowThreadProcessId($h, [ref]$proc_pid) | Out-Null
                    $script:rows += [pscustomobject]@{
                        hwnd  = [int64]$h
                        pid   = [int]$proc_pid
                        title = $sb.ToString()
                        x     = $rect.Left
                        y     = $rect.Top
                        w     = $rect.Right - $rect.Left
                        h     = $rect.Bottom - $rect.Top
                    }
                }
            }
            return $true
        }
        $script:rows = @()
        [W32]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
        if ($Json) {
            # Machine-mode envelope so the agent reads typed fields,
            # not Format-Table prose. Operator directive
            # task #148 (shim JSON sweep).
            $env = [pscustomobject]@{
                ok      = $true
                verb    = 'window_list'
                count   = $script:rows.Count
                windows = $script:rows
            }
            $env | ConvertTo-Json -Depth 4 -Compress
        } else {
            $script:rows | Format-Table hwnd, pid, x, y, w, h, title -AutoSize
        }
    }

    'window-focus' {
        $arg = $Args[0]
        $hwnd = [IntPtr]::Zero
        if ($arg -match '^\d+$') {
            $val = [int64]$arg
            if ($val -lt 1000000) {
                try {
                    $proc = Get-Process -Id ([int]$val) -ErrorAction Stop
                    if ($proc -and $proc.MainWindowHandle -ne 0) {
                        $hwnd = $proc.MainWindowHandle
                    }
                } catch {}
            }
            if ($hwnd -eq [IntPtr]::Zero) {
                $hwnd = [IntPtr]$val
            }
        } else {
            throw "window-focus: <hwnd-or-pid> must be numeric"
        }
        $wasZoomed = [W32]::IsZoomed($hwnd)
        if ([W32]::IsIconic($hwnd)) { [W32]::ShowWindow($hwnd, 9) | Out-Null }
        $before = New-Object W32+RECT
        [void][W32]::GetWindowRect($hwnd, [ref]$before)
        [W32]::BringWindowToTop($hwnd) | Out-Null
        [W32]::SetForegroundWindow($hwnd) | Out-Null
        if (-not $wasZoomed -and $before.Right -gt $before.Left -and $before.Bottom -gt $before.Top) {
            Start-Sleep -Milliseconds 100
            $after = New-Object W32+RECT
            [void][W32]::GetWindowRect($hwnd, [ref]$after)
            if ([W32]::IsZoomed($hwnd) -or $after.Left -ne $before.Left -or
                $after.Top -ne $before.Top -or $after.Right -ne $before.Right -or
                $after.Bottom -ne $before.Bottom) {
                [void][W32]::ShowWindow($hwnd, 1)
                [void][W32]::MoveWindow($hwnd, $before.Left, $before.Top,
                    $before.Right - $before.Left, $before.Bottom - $before.Top, $true)
            }
        }
        Write-Output "[mios-pc-control] window-focus hwnd=$hwnd"
    }

    'window-move' {
        $hwnd = [IntPtr]([int64]$Args[0])
        $x = [int]$Args[1]; $y = [int]$Args[2]
        $rect = New-Object W32+RECT
        [W32]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
        $w = $rect.Right - $rect.Left
        $h = $rect.Bottom - $rect.Top
        [W32]::MoveWindow($hwnd, $x, $y, $w, $h, $true) | Out-Null
        Write-Output "[mios-pc-control] window-move hwnd=$hwnd to ($x,$y)"
    }

    'window-resize' {
        $hwnd = [IntPtr]([int64]$Args[0])
        $w = [int]$Args[1]; $h = [int]$Args[2]
        $rect = New-Object W32+RECT
        [W32]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
        [W32]::MoveWindow($hwnd, $rect.Left, $rect.Top, $w, $h, $true) | Out-Null
        Write-Output "[mios-pc-control] window-resize hwnd=$hwnd to ${w}x${h}"
    }

    'window-center' {
        # Center the window on its current monitor's work area.
        # Usage: window-center <hwnd-or-pid>
        # Operator directive "MiOS apps STILL don't center
        # launch and don't self center" -- Windows apps launched via
        # Start-Process appear at default Win32 placement (often top-
        # left or last-position). This puts them in the screen center.
        $arg = $Args[0]
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        # Try treating $arg as hwnd first; if it's a small int (PID),
        # resolve to the process's main window handle.
        $hwnd = [IntPtr]::Zero
        if ($arg -match '^\d+$') {
            $val = [int64]$arg
            if ($val -lt 1000000) {
                try {
                    $proc = Get-Process -Id ([int]$val) -ErrorAction Stop
                    if ($proc -and $proc.MainWindowHandle -ne 0) {
                        $hwnd = $proc.MainWindowHandle
                    }
                } catch {}
            }
            if ($hwnd -eq [IntPtr]::Zero) {
                $hwnd = [IntPtr]$val
            }
        }
        if ($hwnd -eq [IntPtr]::Zero) {
            throw "window-center: could not resolve '$arg' to a window handle"
        }
        $previousDpi = [IntPtr]::Zero
        try { $previousDpi = [W32]::SetThreadDpiAwarenessContext([IntPtr]::new(-4)) } catch {}
        try {
            if ([W32]::IsIconic($hwnd)) { [W32]::ShowWindow($hwnd, 9) | Out-Null }
            $rect = New-Object W32+RECT
            if (-not [W32]::GetWindowRect($hwnd, [ref]$rect)) { throw "window-center: could not read window rectangle" }
            $screen = [System.Windows.Forms.Screen]::FromHandle($hwnd).WorkingArea
            $w = [Math]::Min($rect.Right - $rect.Left, $screen.Width)
            $h = [Math]::Min($rect.Bottom - $rect.Top, $screen.Height)
            $x = $screen.X + [int](($screen.Width - $w) / 2)
            $y = $screen.Y + [int](($screen.Height - $h) / 2)
            [W32]::MoveWindow($hwnd, $x, $y, $w, $h, $true) | Out-Null
        } finally { if ($previousDpi -ne [IntPtr]::Zero) { [void][W32]::SetThreadDpiAwarenessContext($previousDpi) } }
        [W32]::SetForegroundWindow($hwnd) | Out-Null
        Write-Output "[mios-pc-control] window-center hwnd=$hwnd to ($x,$y) ${w}x${h}"
    }

    'window-close' {
        # Graceful close of a window via WM_CLOSE.
        #
        # Usage: window-close <hwnd-or-pid>
        #
        # Posts WM_CLOSE to the target window so the app's own message
        # loop handles it (same as the operator clicking the X / Alt+F4).
        # Most apps prompt to save unsaved work, then exit cleanly.
        #
        # NOT a kill: never use Stop-Process / taskkill /f for "close
        # this window" -- that loses unsaved state and may not even
        # close the right process when the target hosts multiple
        # windows (Chrome, Discord, browsers in general). Operator
        # directive chat showed agent running
        # `pkill -f hermes-agent` thinking "close the crew" meant
        # "close the agent crew" -- self-terminated. WM_CLOSE on the
        # right window is the correct verb every time.
        $arg = $Args[0]
        $hwnd = [IntPtr]::Zero
        if ($arg -match '^\d+$') {
            $val = [int64]$arg
            if ($val -lt 1000000) {
                try {
                    $proc = Get-Process -Id ([int]$val) -ErrorAction Stop
                    if ($proc -and $proc.MainWindowHandle -ne 0) {
                        $hwnd = $proc.MainWindowHandle
                    }
                } catch {}
            }
            if ($hwnd -eq [IntPtr]::Zero) {
                $hwnd = [IntPtr]$val
            }
        } else {
            throw "window-close: <hwnd-or-pid> must be numeric"
        }
        # SendMessageTimeout with SMTO_ABORTIFHUNG so a hung window
        # doesn't block this helper indefinitely. 5s timeout is plenty
        # for apps that show a "save changes?" dialog.
        $result = [IntPtr]::Zero
        $rc = [W32]::SendMessageTimeout($hwnd, [W32]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero, [W32]::SMTO_ABORTIFHUNG, 5000, [ref]$result)
        if ($rc -eq [IntPtr]::Zero) {
            # Fall back to async PostMessage so we don't block at all
            [W32]::PostMessage($hwnd, [W32]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
            Write-Output "[mios-pc-control] window-close (PostMessage) hwnd=$hwnd"
        } else {
            Write-Output "[mios-pc-control] window-close hwnd=$hwnd (graceful)"
        }
    }

    'screen-layout' {
        # Monitor geometry for the screen_layout verb (was dispatching a
        # non-existent action -> "unknown action" error). Emits each display's
        # bounds + working area + primary flag as JSON the agent reads before
        # positioning windows by literal coords.
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $screens = [System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
            @{
                device  = $_.DeviceName
                primary = [bool]$_.Primary
                bounds  = @{ x = $_.Bounds.X; y = $_.Bounds.Y; width = $_.Bounds.Width; height = $_.Bounds.Height }
                work    = @{ x = $_.WorkingArea.X; y = $_.WorkingArea.Y; width = $_.WorkingArea.Width; height = $_.WorkingArea.Height }
            }
        }
        @{ ok = $true; verb = "screen_layout"; count = @($screens).Count; screens = @($screens) } | ConvertTo-Json -Depth 5 -Compress
    }

    default {
        throw "mios-pc-control.ps1: unknown action '$Action' (try: screenshot, click, double-click, mouse-move, type, key, key-combo, window-list, window-focus, window-move, window-resize, window-center, window-close, screen-layout)"
    }
}
