# AI-hint: MiOS configuration and runtime asset for profile.ps1.
# AI-related: /usr/libexec/mios/mios-dashboard, /usr/libexec/mios/mios-ssh-dev-cmd, mios-dashboard, mios-ssh-dev-cmd, mios-dev, mios-bootstrap, mios-build, mios-pull, mios-update, mios-config


if ($Global:MiosProfileLoaded) { return }
$Global:MiosProfileLoaded = $true

# Ensure WinGet machine links directory is in the Path for the current session.
# This prevents PATH drift warnings or command-not-found errors if a user installs
# a global portable package (like claude) and expects it to work in the active shell.
if ($env:Path -notlike "*C:\Program Files\WinGet\Links*") {
    $env:Path += ";C:\Program Files\WinGet\Links"
}

try { & chcp.com 65001 *> $null } catch {}
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}
try { [Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false) } catch {}
try { $OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch {}

if ($env:MIOS_APP_CONTEXT) {
    try {
        $_curW = [Console]::WindowWidth
        if ($_curW -gt 80) {
            [Console]::SetWindowSize(80, 20)
            [Console]::SetBufferSize(80, 9000)
        } else {
            [Console]::SetBufferSize(80, 9000)
            [Console]::SetWindowSize(80, 20)
        }
    } catch {}
}
if ($env:MIOS_APP_CONTEXT) {
    try {
        Add-Type -Namespace MiosWin -Name N -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool MoveWindow(System.IntPtr hWnd, int x, int y, int w, int h, bool repaint);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out System.Drawing.Rectangle rect);
'@ -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $_hwnd = [MiosWin.N]::GetConsoleWindow()
        $_r = New-Object System.Drawing.Rectangle
        [MiosWin.N]::GetWindowRect($_hwnd, [ref]$_r) | Out-Null
        $_w = $_r.Width  - $_r.X
        $_h = $_r.Height - $_r.Y
        # Center on the ACTIVE display (where the cursor currently is),
        # NOT PrimaryScreen. On multi-monitor hosts the operator launches
        # mios.bat from whichever monitor they're working on; the window
        # should land THERE.
        $_cur = [System.Windows.Forms.Cursor]::Position
        $_s   = [System.Windows.Forms.Screen]::FromPoint($_cur).WorkingArea
        $_x = $_s.X + [int](([math]::Max(0, $_s.Width  - $_w)) / 2)
        $_y = $_s.Y + [int](([math]::Max(0, $_s.Height - $_h)) / 2)
        [MiosWin.N]::MoveWindow($_hwnd, $_x, $_y, $_w, $_h, $true) | Out-Null
    } catch {}
}

if ($env:WT_SESSION) {

    foreach ($mod in @('Terminal-Icons','posh-git','CompletionPredictor','Microsoft.WinGet.CommandNotFound')) {
        if (Get-Module -ListAvailable -Name $mod -ErrorAction SilentlyContinue) {
            try { Import-Module $mod -ErrorAction SilentlyContinue } catch {}
        }
    }

    try {
        $latestPSRL = Get-Module -ListAvailable -Name PSReadLine |
                       Sort-Object Version -Descending | Select-Object -First 1
        if ($latestPSRL -and $latestPSRL.Version -ge [version]'2.3.5') {
            Import-Module PSReadLine -RequiredVersion $latestPSRL.Version -Force -ErrorAction SilentlyContinue
        }
    } catch {}

    $miosArtifactRoot = 'M:\MiOS'
    if (-not (Test-Path -LiteralPath $miosArtifactRoot)) {
        Write-Host "  [!] M:\MiOS not found -- re-run the irm|iex bootstrap to provision M:\." -ForegroundColor Yellow
    }
    function _MiosSelfHeal {
        param([string]$RelDir, [string]$FileName, [string]$Blob)
        $dir = Join-Path $miosArtifactRoot $RelDir
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $path = Join-Path $dir $FileName
        if (-not (Test-Path -LiteralPath $path)) {
            try { [System.IO.File]::WriteAllBytes($path, [Convert]::FromBase64String($Blob)) } catch { return $null }
        }
        return $path
    }

    # oh-my-posh config -- probe canonical paths, self-heal if missing.
    $miosOmp = $null
    $ompCands = @()
    if ($env:MIOS_OMP_JSON) { $ompCands += $env:MIOS_OMP_JSON }
    $ompCands += @(
        'M:\MiOS\themes\mios.omp.json',
        'M:\usr\share\mios\oh-my-posh\mios.omp.json'
    )
    # C:\* deliberately excluded -- M:\-everywhere invariant
    # (operator: EVERYTHING to M:\, no LOCALAPPDATA / C:\MiOS leaks).
    foreach ($c in $ompCands) {
        if ($c -and (Test-Path -LiteralPath $c)) { $miosOmp = $c; break }
    }
    if (-not $miosOmp) {
        $miosOmp = _MiosSelfHeal 'themes' 'mios.omp.json' 'ewogICIkc2NoZW1hIjogImh0dHBzOi8vcmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbS9KYW5EZURvYmJlbGVlci9vaC1teS1wb3NoL21haW4vdGhlbWVzL3NjaGVtYS5qc29uIiwKICAidmVyc2lvbiI6IDQsCiAgImZpbmFsX3NwYWNlIjogZmFsc2UsCiAgIi8vIjogWwogICAgIk1pT1MgT2gtTXktUG9zaCB0aGVtZS4iLAogICAgIkRFVEVSTUlOSVNUSUMgVFdPLUxJTkUgTEFZT1VUICgyMDI2LTA1LTEwIHJlZGVzaWduKTogdGhlIHByaW9yIiwKICAgICJsZWZ0K3JpZ2h0IGFsaWdubWVudCBzY2hlbWUgb3ZlcmZsb3dlZCB0byBhIDNyZCBsaW5lIG9uIDgweDIwIiwKICAgICJ0ZXJtaW5hbHMgYmVjYXVzZSBvaC1teS1wb3NoJ3MgcmlnaHQtYmxvY2sgYWxpZ25tZW50IG1hdGggc2F3IiwKICAgICJ0aGUgdHJhaWxpbmcgNi1OQlNQIGJ1ZmZlciArIHJpZ2h0X21hcmdpbiBhcyBwYXJ0IG9mIHRoZSByaWdodCIsCiAgICAiYmxvY2sncyByZW5kZXIgd2lkdGggYW5kIHRyaWdnZXJlZCBhbiBlYXJseSB3cmFwLiBPcGVyYXRvci0iLAogICAgInJlcG9ydGVkIGFzICdwb3dlcmxpbmVzIHdyYXAgaW5jb3JyZWN0bHknIHdpdGggc2NyZWVuc2hvdC4iLAogICAgIiIsCiAgICAiTmV3IGxheW91dDoiLAogICAgIiAgTGluZSAxIChibG9jayAxKTogbGVhZGVyIGNhcCArIHNoZWxsICsgcGF0aCArIGdpdCArIGV4ZWN0aW1lIiwKICAgICIgICAgICAgICAgICAgICAgICAgICsgV1NML09TIGluZGljYXRvciArIEhIOk1NIHRpbWUsIGFsbCBwYWNrZWQiLAogICAgIiAgICAgICAgICAgICAgICAgICAgTEVGVC1hbGlnbmVkIC0tIG5vIHJpZ2h0IGFsaWdubWVudCwgbm8iLAogICAgIiAgICAgICAgICAgICAgICAgICAgY3Jvc3MtYmxvY2sgbWF0aCwgbm8gd3JhcCByaXNrLiIsCiAgICAiICBMaW5lIDIgKGJsb2NrIDIpOiBjbG9zZXIgY2FwICsgY2hldnJvbiBwcm9tcHQgYXJyb3cuIiwKICAgICIiLAogICAgIkFsbCBzZWdtZW50cyBzdGlsbCBwdWxsIGdseXBocyBmcm9tIG1pb3MudG9tbCBbdGhlbWUucHJvbXB0XSIsCiAgICAidmlhIHRoZSBwb3N0LWluc3RhbGwgdGVtcGxhdGUgc3Vic3RpdHV0aW9uIGluIiwKICAgICJJbnN0YWxsLU1pT1NPaE15UG9zaFRoZW1lLiBQYWxldHRlOiBtaW9zLnRvbWwgW2NvbG9yc10gKEhva3VzYWkgKyIsCiAgICAib3BlcmF0b3IgbmV1dHJhbHMpLiBMYW5ndWFnZSBzZWdtZW50cyAobm9kZS9weXRob24vZ28vcnVzdC9kb3RuZXQpIiwKICAgICJhbmQgY29uZGl0aW9uYWwgaW5mcmFzdHJ1Y3R1cmUgc2VnbWVudHMgKGt1YmVjdGwvYXdzL2JhdHRlcnkpIiwKICAgICJyZW1vdmVkIC0tIHRoZXkgcG9sbHV0ZSB0aGUgbGluZSBhbmQgcmFyZWx5IHJlbmRlciBtZWFuaW5nZnVsbHkiLAogICAgIm9uIHRoZSBkZXYgVk0uIE9wZXJhdG9ycyB3aG8gbmVlZCB0aGVtIGNhbiBhZGQgdmlhIG1pb3MuaHRtbC4iLAogICAgIiIsCiAgICAiUG93ZXJsaW5lIGNhcCBnbHlwaDogVStFMEI0IChyb3VuZGVkIHJpZ2h0IGhhbGYtY2lyY2xlKS4gR2Vpc3QiLAogICAgIk1vbm8gTmVyZCBGb250IGhhcyBmdWxsIFBvd2VybGluZSBjb3ZlcmFnZTsgdGhlIHByZXZpb3VzIGRlc2lnbiIsCiAgICAidXNlZCBcIlwiIChlbXB0eSkgd2hpY2ggcHJvZHVjZWQgYWJ1dHRpbmcgc3F1YXJlIGNvbG9yIGJsb2NrcyAtLSIsCiAgICAib3BlcmF0b3ItZmxhZ2dlZCAyMDI2LTA1LTEwICdwaXBlbGluIGlzbid0IHJvdW5kZWQgYW55bW9yZScuIgogIF0sCiAgImJsb2NrcyI6IFsKICAgIHsKICAgICAgInR5cGUiOiAicHJvbXB0IiwKICAgICAgImFsaWdubWVudCI6ICJsZWZ0IiwKICAgICAgInNlZ21lbnRzIjogWwogICAgICAgIHsKICAgICAgICAgICIvLyI6ICJMZWFkaW5nIHByb21wdCBjYXAuIEJveC1kcmF3aW5nIGNoYXJzIChVKzI1NkQgLyBVKzI1MDApIGFyZSBndWFyYW50ZWVkIGluIGFueSBtb25vc3BhY2UgZm9udDsgdGhlIFUrRTBCNiByb3VuZGVkLWNhcCBQVUEgYXBwcm9hY2ggd2UgdHJpZWQgaW4gdjEgd2FzIHVucmVsaWFibGUgb24gb3BlcmF0b3IncyBHZWlzdE1vbm8gTmVyZCBGb250IE1vbm8gYnVpbGQgKGNhcCBnbHlwaCBuZXZlciBzdXJmYWNlZCwgbGVhdmluZyBjb2xzIDAtMiBibGFuaykuIiwKICAgICAgICAgICJ0eXBlIjogInRleHQiLAogICAgICAgICAgInN0eWxlIjogInBsYWluIiwKICAgICAgICAgICJmb3JlZ3JvdW5kIjogIiNCN0M5RDciLAogICAgICAgICAgInRlbXBsYXRlIjogIuKVreKUgCIKICAgICAgICB9LAogICAgICAgIHsKICAgICAgICAgICJ0eXBlIjogInNoZWxsIiwKICAgICAgICAgICJzdHlsZSI6ICJwb3dlcmxpbmUiLAogICAgICAgICAgInBvd2VybGluZV9zeW1ib2wiOiAi7oK0IiwKICAgICAgICAgICJiYWNrZ3JvdW5kIjogIiMxQTQwN0YiLAogICAgICAgICAgImZvcmVncm91bmQiOiAiI0U3REZEMyIsCiAgICAgICAgICAidGVtcGxhdGUiOiAiICB7eyAuTmFtZSB9fSAiCiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAidHlwZSI6ICJyb290IiwKICAgICAgICAgICJzdHlsZSI6ICJwb3dlcmxpbmUiLAogICAgICAgICAgInBvd2VybGluZV9zeW1ib2wiOiAi7oK0IiwKICAgICAgICAgICJiYWNrZ3JvdW5kIjogIiNEQzI3MUIiLAogICAgICAgICAgImZvcmVncm91bmQiOiAiI0YzNUMxNSIsCiAgICAgICAgICAidGVtcGxhdGUiOiAiICAiCiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAidHlwZSI6ICJwYXRoIiwKICAgICAgICAgICJzdHlsZSI6ICJwb3dlcmxpbmUiLAogICAgICAgICAgInBvd2VybGluZV9zeW1ib2wiOiAi7oK0IiwKICAgICAgICAgICJiYWNrZ3JvdW5kIjogIiNGMzVDMTUiLAogICAgICAgICAgImZvcmVncm91bmQiOiAiIzI4MjI2MiIsCiAgICAgICAgICAicHJvcGVydGllcyI6IHsKICAgICAgICAgICAgImZvbGRlcl9pY29uIjogIiAgIiwKICAgICAgICAgICAgImhvbWVfaWNvbiI6ICIiLAogICAgICAgICAgICAic3R5bGUiOiAiYWdub3N0ZXJfc2hvcnQiLAogICAgICAgICAgICAibWF4X2RlcHRoIjogMwogICAgICAgICAgfSwKICAgICAgICAgICJ0ZW1wbGF0ZSI6ICIgIHt7IC5QYXRoIH19ICIKICAgICAgICB9LAogICAgICAgIHsKICAgICAgICAgICJ0eXBlIjogImdpdCIsCiAgICAgICAgICAic3R5bGUiOiAicG93ZXJsaW5lIiwKICAgICAgICAgICJwb3dlcmxpbmVfc3ltYm9sIjogIu6CtCIsCiAgICAgICAgICAiYmFja2dyb3VuZCI6ICIjM0U3NzY1IiwKICAgICAgICAgICJiYWNrZ3JvdW5kX3RlbXBsYXRlcyI6IFsKICAgICAgICAgICAgInt7IGlmIG9yICguV29ya2luZy5DaGFuZ2VkKSAoLlN0YWdpbmcuQ2hhbmdlZCkgfX0jRjM1QzE1e3sgZW5kIH19IiwKICAgICAgICAgICAgInt7IGlmIGFuZCAoZ3QgLkFoZWFkIDApIChndCAuQmVoaW5kIDApIH19I0RDMjcxQnt7IGVuZCB9fSIsCiAgICAgICAgICAgICJ7eyBpZiBndCAuQWhlYWQgMCB9fSMxQTQwN0Z7eyBlbmQgfX0iLAogICAgICAgICAgICAie3sgaWYgZ3QgLkJlaGluZCAwIH19IzczNEYzOXt7IGVuZCB9fSIKICAgICAgICAgIF0sCiAgICAgICAgICAiZm9yZWdyb3VuZCI6ICIjMjgyMjYyIiwKICAgICAgICAgICJwcm9wZXJ0aWVzIjogewogICAgICAgICAgICAiYnJhbmNoX2ljb24iOiAiICIsCiAgICAgICAgICAgICJmZXRjaF9zdGF0dXMiOiB0cnVlLAogICAgICAgICAgICAiZmV0Y2hfdXBzdHJlYW1faWNvbiI6IHRydWUKICAgICAgICAgIH0sCiAgICAgICAgICAidGVtcGxhdGUiOiAiICB7eyAuVXBzdHJlYW1JY29uIH19e3sgLkhFQUQgfX17eyBpZiAuQnJhbmNoU3RhdHVzIH19IHt7IC5CcmFuY2hTdGF0dXMgfX17eyBlbmQgfX17eyBpZiAuV29ya2luZy5DaGFuZ2VkIH19IOKcjnt7IC5Xb3JraW5nLlN0cmluZyB9fXt7IGVuZCB9fXt7IGlmIGFuZCAoLldvcmtpbmcuQ2hhbmdlZCkgKC5TdGFnaW5nLkNoYW5nZWQpIH19IHx7eyBlbmQgfX17eyBpZiAuU3RhZ2luZy5DaGFuZ2VkIH19PCNEQzI3MUI+ICt7eyAuU3RhZ2luZy5TdHJpbmcgfX08Lz57eyBlbmQgfX0gIgogICAgICAgIH0sCiAgICAgICAgewogICAgICAgICAgInR5cGUiOiAiZXhlY3V0aW9udGltZSIsCiAgICAgICAgICAic3R5bGUiOiAicG93ZXJsaW5lIiwKICAgICAgICAgICJwb3dlcmxpbmVfc3ltYm9sIjogIu6CtCIsCiAgICAgICAgICAiYmFja2dyb3VuZCI6ICIjOTQ4RThFIiwKICAgICAgICAgICJmb3JlZ3JvdW5kIjogIiMyODIyNjIiLAogICAgICAgICAgInByb3BlcnRpZXMiOiB7CiAgICAgICAgICAgICJzdHlsZSI6ICJyb3VuZHJvY2siLAogICAgICAgICAgICAidGhyZXNob2xkIjogMAogICAgICAgICAgfSwKICAgICAgICAgICJ0ZW1wbGF0ZSI6ICIgIHt7IC5Gb3JtYXR0ZWRNcyB9fSAiCiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAiLy8iOiAiQ29tcGFjdCBPUyBpbmRpY2F0b3IgLS0ganVzdCB0aGUgcGxhdGZvcm0gZ2x5cGgsIG5vICdXU0wgYXQnIHByb3NlLiBTYXZlcyBjZWxsczsgdGhlIGZyYW1lZCBkYXNoYm9hcmQgYWxyZWFkeSBuYW1lcyB0aGUgaG9zdCBraW5kIGV4cGxpY2l0bHkuIiwKICAgICAgICAgICJ0eXBlIjogIm9zIiwKICAgICAgICAgICJzdHlsZSI6ICJwb3dlcmxpbmUiLAogICAgICAgICAgInBvd2VybGluZV9zeW1ib2wiOiAi7oK0IiwKICAgICAgICAgICJiYWNrZ3JvdW5kIjogIiNCN0M5RDciLAogICAgICAgICAgImZvcmVncm91bmQiOiAiIzI4MjI2MiIsCiAgICAgICAgICAicHJvcGVydGllcyI6IHsKICAgICAgICAgICAgImxpbnV4IjogIiIsCiAgICAgICAgICAgICJtYWNvcyI6ICIiLAogICAgICAgICAgICAid2luZG93cyI6ICIiCiAgICAgICAgICB9LAogICAgICAgICAgInRlbXBsYXRlIjogIiB7eyAuSWNvbiB9fSAiCiAgICAgICAgfSwKICAgICAgICB7CiAgICAgICAgICAidHlwZSI6ICJ0aW1lIiwKICAgICAgICAgICJzdHlsZSI6ICJwb3dlcmxpbmUiLAogICAgICAgICAgInBvd2VybGluZV9zeW1ib2wiOiAi7oK0IiwKICAgICAgICAgICJiYWNrZ3JvdW5kIjogIiMxQTQwN0YiLAogICAgICAgICAgImZvcmVncm91bmQiOiAiI0U3REZEMyIsCiAgICAgICAgICAicHJvcGVydGllcyI6IHsKICAgICAgICAgICAgInRpbWVfZm9ybWF0IjogIjE1OjA0IgogICAgICAgICAgfSwKICAgICAgICAgICJ0ZW1wbGF0ZSI6ICIge3sgLkN1cnJlbnREYXRlIHwgZGF0ZSAuRm9ybWF0IH19ICIKICAgICAgICB9CiAgICAgIF0KICAgIH0sCiAgICB7CiAgICAgICJ0eXBlIjogInByb21wdCIsCiAgICAgICJhbGlnbm1lbnQiOiAibGVmdCIsCiAgICAgICJuZXdsaW5lIjogdHJ1ZSwKICAgICAgInNlZ21lbnRzIjogWwogICAgICAgIHsKICAgICAgICAgICIvLyI6ICJDbG9zZXIgY2FwICsgcHJvbXB0IGFycm93LiBTdGF0dXMtdGludGVkOiB0aW50cyB0aGUgd2hvbGUgYOKVsOKUgCDina9gIHJlZCBvbiBub24temVybyBleGl0IGNvZGUgKC5Db2RlID4gMCksIHBhbGUtYmx1ZSBvdGhlcndpc2UgKG1hdGNoZXMgdGhlIGxlYWRpbmcgYOKVreKUgGAgY2FwIGZvciB2aXN1YWwgc3ltbWV0cnkpLiIsCiAgICAgICAgICAidHlwZSI6ICJzdGF0dXMiLAogICAgICAgICAgInN0eWxlIjogInBsYWluIiwKICAgICAgICAgICJmb3JlZ3JvdW5kIjogIiNCN0M5RDciLAogICAgICAgICAgImZvcmVncm91bmRfdGVtcGxhdGVzIjogWwogICAgICAgICAgICAie3sgaWYgZ3QgLkNvZGUgMCB9fSNEQzI3MUJ7eyBlbmQgfX0iCiAgICAgICAgICBdLAogICAgICAgICAgInByb3BlcnRpZXMiOiB7CiAgICAgICAgICAgICJhbHdheXNfZW5hYmxlZCI6IHRydWUKICAgICAgICAgIH0sCiAgICAgICAgICAidGVtcGxhdGUiOiAi4pWw4pSAIOKdryAiCiAgICAgICAgfQogICAgICBdCiAgICB9CiAgXQp9Cg=='
    }

    # -- Framed MiOS dashboard (mirrors mios-dashboard from mios.git) -
    # 80-col fixed frame, centered ASCII logo, framed fastfetch info.
    # Gated on WT_SESSION since the +-+ box-drawing only renders
    # properly in WT (conhost / VS Code embedded shell mangles it).
    function Show-MiosDashboard {
        param([string]$ConfigPath, [string]$LogoPath, [switch]$Full)
        $_widthA = 0; $_widthB = 0
        for ($_i = 0; $_i -lt 5; $_i++) {
            $_widthB = $_widthA
            $_winC = try { [Console]::WindowWidth } catch { 0 }
            $_winR = try { $Host.UI.RawUI.WindowSize.Width } catch { 0 }
            $_widthA = if ($_winC -gt 0 -and $_winR -gt 0) { [math]::Min($_winC, $_winR) }
                        elseif ($_winC -gt 0) { $_winC }
                        elseif ($_winR -gt 0) { $_winR }
                        else { 0 }
            if ($_widthA -gt 0 -and $_widthA -eq $_widthB) { break }
            if ($_i -lt 4) { Start-Sleep -Milliseconds 50 }
        }
        $_winWNow = if ($_widthA -gt 0) { $_widthA } else { 80 }
        $WIDTH = $_winWNow - 0
        if (80 -gt 0 -and $WIDTH -gt 80) {
            $WIDTH = 80
        }
        if ($WIDTH -lt 20) { $WIDTH = [math]::Max(20, $_winWNow) }
        $INNER = $WIDTH - 4
        # $H is a STRING (not [char]): the frame borders build with `$H * n`,
        # and PowerShell's `*` is undefined for [char] (it threw "[Char] *
        # [Int32] is not defined" -> Show-MiosDashboard never rendered, so the
        # framed Windows dashboard silently fell back to nothing and only the
        # fastfetch `mios dash` view showed). String repeat gives the border.
        $TL=[char]0x256d; $TR=[char]0x256e; $BL=[char]0x2570; $BR=[char]0x256f; $LT=[char]0x251c; $RT=[char]0x2524; $V=[char]0x2502; $H=[string][char]0x2500

        $_esc      = [char]27
        $_FrameC   = "$_esc[34m"
        $_FrameR   = "$_esc[0m"

        function _Strip { param($s) $s -replace '\x1b\[[0-9;]*m','' }
        function _Frame {
            param([string]$Line)
            $visible = _Strip $Line
            if ($visible.Length -gt $INNER) {
                # Truncate with ellipsis preserving ANSI prefix.
                $Line = $Line.Substring(0, [math]::Min($Line.Length, $INNER + ($Line.Length - $visible.Length) - 1)) + [char]0x2026
                $visible = _Strip $Line
            }
            $pad = ' ' * [math]::Max(0, $INNER - $visible.Length)
            "$_FrameC$V$_FrameR $Line$pad$_FrameC $V$_FrameR"
        }
        function _Center {
            param([string]$Line)
            $visible = _Strip $Line
            $totalPad = [math]::Max(0, $INNER - $visible.Length)
            $lpad = ' ' * [math]::Floor($totalPad / 2)
            $rpad = ' ' * ($totalPad - [math]::Floor($totalPad / 2))
            "$_FrameC$V$_FrameR $lpad$Line$rpad$_FrameC $V$_FrameR"
        }

        $_winH = try { [Console]::WindowHeight } catch { 0 }
        if ($_winH -le 0) { $_winH = try { $Host.UI.RawUI.WindowSize.Height } catch { 0 } }
        $_compact = if ($Full) { $false } elseif ($_winH -gt 0) { $_winH -lt 25 } else { $true }
        # Reserve rows for top + divider + divider + hints + bottom.
        # Compact hints = 1 row; full hints = 7 rows.
        $_hintsRows  = if ($_compact) { 1 } else { 7 }
        $_overhead   = 1 + 1 + 1 + $_hintsRows + 1   # top + 2 dividers + hints + bottom
        # Logo + fastfetch share whatever's left.
        $_contentBudget = [math]::Max(2, 19 - $_overhead)
        # In compact mode skip the multi-line ASCII logo entirely; in
        # full mode allocate up to half the content budget to the logo.
        $_logoBudget = if ($_compact) { 1 } else { [math]::Min(11, [math]::Floor($_contentBudget / 2)) }
        $_ffBudget   = $_contentBudget - $_logoBudget

        # Read mios.toml ONCE up-front so [dashboard].title (here),
        # [dashboard].rows + [theme.font] (further down) all read from
        # the same in-memory copy.  No fallback to other paths -- the
        # canonical layout is M:\etc\mios (host overlay) > M:\usr\share
        # (vendor on M:\).
        $_dashTomlText = $null
        foreach ($_tc in @('M:\etc\mios\mios.toml','M:\usr\share\mios\mios.toml')) {
            if (Test-Path -LiteralPath $_tc) {
                try { $_dashTomlText = [IO.File]::ReadAllText($_tc, (New-Object System.Text.UTF8Encoding($false))); break } catch {}
            }
        }

        # Top frame.
        Write-Host ($TL + ($H * ($WIDTH - 2)) + $TR) -ForegroundColor Blue
        if ($_compact) {
            $title = 'MiOS  --  My Personal Operating System'
            if ($_dashTomlText) {
                $_titleM = [regex]::Match($_dashTomlText, '(?ms)^\[dashboard\]\s*\r?\n.*?^\s*title\s*=\s*"([^"]+)"')
                if ($_titleM.Success) { $title = $_titleM.Groups[1].Value }
            }
            Write-Host (_Center $title) -ForegroundColor Blue
        }
        elseif ($LogoPath -and (Test-Path -LiteralPath $LogoPath)) {
            $logoLines = @(Get-Content -LiteralPath $LogoPath) | Where-Object { $_ -ne $null -and $_ -notmatch '^\s*#' }
            # The full view (mios dash) shows the whole banner to match the
            # Linux dash; only the compact window path caps to the frame_height.
            if (-not $Full -and $logoLines.Count -gt $_logoBudget) {
                $logoLines = $logoLines[0..([math]::Max(0, $_logoBudget - 1))]
            }
            $maxLen = 0
            foreach ($ll in $logoLines) {
                $len = (_Strip $ll).Length
                if ($len -gt $maxLen) { $maxLen = $len }
            }
            $blockLPad = ' ' * [math]::Max(0, [math]::Floor(($INNER - $maxLen) / 2))
            foreach ($ll in $logoLines) {
                $stripped = _Strip $ll
                $rPad = ' ' * [math]::Max(0, $maxLen - $stripped.Length)
                Write-Host (_Frame ($blockLPad + $ll + $rPad)) -ForegroundColor Blue
            }
        }
        # Divider.
        Write-Host ($LT + ($H * ($WIDTH - 2)) + $RT) -ForegroundColor Blue

        $_dashCache = @{}
        $_DashGetField = {
            param([string]$_k, [string]$_fontFam, [int]$_fontSz)
            switch ($_k) {
                'host_os' {
                    if (-not $_dashCache.ContainsKey('_os')) {
                        $_dashCache['_os'] = try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { $null }
                    }
                    $_o = $_dashCache['_os']
                    $_cap = if ($_o -and $_o.Caption) { (((((($_o.Caption -replace 'Microsoft\s*','') -replace '\s+for\s+Workstations','') -replace '\s+Insider\s+Preview','') -replace '\s*\(64-?bit\)','') -replace '\s*N\s+Edition','')).Trim() } else { 'Windows' }
                    return "$env:USERNAME@$env:COMPUTERNAME -- $_cap".Trim()
                }
                'cpu' {
                    if (-not $_dashCache.ContainsKey('_cpu')) {
                        $_dashCache['_cpu'] = try { Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1 } catch { $null }
                    }
                    $_c = $_dashCache['_cpu']
                    if (-not $_c) { return 'CPU --' }
                    $_n = ($_c.Name -replace '\s+@.*','' -replace '\s+Processor','' -replace '\(R\)','' -replace '\(TM\)','').Trim()
                    $_clk = if ($_c.MaxClockSpeed) { [math]::Round($_c.MaxClockSpeed / 1000.0, 2) } else { 0 }
                    $_co  = $_c.NumberOfLogicalProcessors
                    return "CPU $_n ${_clk}GHz (${_co}c)"
                }
                {$_ -in 'gpu_discrete','gpu_integrated'} {
                    if (-not $_dashCache.ContainsKey('_gpus')) {
                        $_dashCache['_gpus'] = try { @(Get-CimInstance Win32_VideoController -ErrorAction Stop) } catch { @() }
                    }
                    $_gs = $_dashCache['_gpus']
                    if (-not $_gs -or $_gs.Count -eq 0) { return 'GPU --' }
                    if ($_k -eq 'gpu_discrete') {
                        $_g = $_gs | Where-Object { $_.Name -match 'NVIDIA|GeForce|RTX|GTX|Quadro|Radeon RX|Radeon Pro' } | Select-Object -First 1
                        if (-not $_g) { $_g = $_gs | Sort-Object @{e={$_.AdapterRAM};Descending=$true} | Select-Object -First 1 }
                    } else {
                        $_g = $_gs | Where-Object { $_.Name -match 'Radeon\(TM\) Graphics|Intel.*Graphics|UHD Graphics' } | Select-Object -First 1
                        if (-not $_g) { return '' }
                    }
                    if (-not $_g) { return 'GPU --' }
                    $_n = ($_g.Name -replace 'NVIDIA GeForce ','' -replace 'NVIDIA ','' -replace '\(R\)','' -replace '\(TM\)','').Trim()
                    $_vr = if ($_g.AdapterRAM) { [math]::Round(([uint32]$_g.AdapterRAM) / 1GB, 1) } else { 0 }
                    if ($_vr -le 0) { return "GPU $_n" }
                    return "GPU $_n ${_vr}GiB"
                }
                'ram' {
                    if (-not $_dashCache.ContainsKey('_os')) {
                        $_dashCache['_os'] = try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { $null }
                    }
                    $_o = $_dashCache['_os']
                    if (-not $_o) { return 'RAM --' }
                    $_tot = [math]::Round(([int64]$_o.TotalVisibleMemorySize) / 1MB, 1)
                    $_use = [math]::Round((([int64]$_o.TotalVisibleMemorySize - [int64]$_o.FreePhysicalMemory)) / 1MB, 1)
                    $_pct = if ($_o.TotalVisibleMemorySize -gt 0) { [math]::Round((($_use / $_tot) * 100), 0) } else { 0 }
                    return "RAM ${_use} / ${_tot}GiB (${_pct}%)"
                }
                'swap' {
                    if (-not $_dashCache.ContainsKey('_pf')) {
                        $_dashCache['_pf'] = try { Get-CimInstance Win32_PageFileUsage -ErrorAction Stop } catch { $null }
                    }
                    $_p = @($_dashCache['_pf'])
                    if (-not $_p -or $_p.Count -eq 0 -or -not $_p[0]) { return 'Swap --' }
                    $_tot = [math]::Round(($_p | Measure-Object AllocatedBaseSize -Sum).Sum / 1024.0, 1)
                    $_use = [math]::Round(($_p | Measure-Object CurrentUsage -Sum).Sum / 1024.0, 1)
                    $_pct = if ($_tot -gt 0) { [math]::Round((($_use / $_tot) * 100), 0) } else { 0 }
                    return "Swap ${_use} / ${_tot}GiB (${_pct}%)"
                }
                {$_ -match '^disk_([a-zA-Z])$'} {
                    $_dl = $_.Substring(5,1).ToUpper()
                    $_v  = try { Get-Volume -DriveLetter $_dl -ErrorAction Stop } catch { $null }
                    if (-not $_v) { return "${_dl}: --" }
                    $_tot = [math]::Round($_v.Size / 1GB, 1)
                    $_use = [math]::Round(($_v.Size - $_v.SizeRemaining) / 1GB, 1)
                    $_pct = if ($_v.Size -gt 0) { [math]::Round(((($_v.Size - $_v.SizeRemaining) / $_v.Size) * 100), 0) } else { 0 }
                    return "${_dl}: ${_use} / ${_tot}GiB (${_pct}%)"
                }
                'kernel' {
                    return 'Kernel ' + [System.Environment]::OSVersion.Version.ToString()
                }
                'shell' {
                    return 'Shell pwsh ' + $PSVersionTable.PSVersion.ToString()
                }
                'font' {
                    return "Font $_fontFam ${_fontSz}pt"
                }
                'uptime' {
                    if (-not $_dashCache.ContainsKey('_os')) {
                        $_dashCache['_os'] = try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop } catch { $null }
                    }
                    $_o = $_dashCache['_os']
                    if (-not $_o -or -not $_o.LastBootUpTime) { return 'Up --' }
                    $_up = (Get-Date) - $_o.LastBootUpTime
                    $_upd = [math]::Floor($_up.TotalDays)
                    return "Up ${_upd}d $($_up.Hours)h $($_up.Minutes)m"
                }
                default { return '' }
            }
        }

        # Parse [dashboard].rows + [theme.font] from the mios.toml text
        # we already loaded above for [dashboard].title.  Vendor defaults
        # baked in below if parsing fails (cold first-run before M:\
        # overlay is staged).
        $_dashRows  = $null
        $_dashFontF = 'GeistMono Nerd Font Mono'
        $_dashFontS = 12
        if ($_dashTomlText) {
            $_dashSec = [regex]::Match($_dashTomlText, '(?ms)^\[dashboard\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
            if ($_dashSec.Success) {
                $_rowsM = [regex]::Match($_dashSec.Groups['body'].Value, '(?ms)^\s*rows\s*=\s*\[(?<arr>.*?)^\]')
                if ($_rowsM.Success) {
                    $_rowsBody = $_rowsM.Groups['arr'].Value
                    $_rowMatches = [regex]::Matches($_rowsBody, '\[(?<r>[^\]]*)\]')
                    $_dashRows = @()
                    foreach ($_rm in $_rowMatches) {
                        $_fields = @($_rm.Groups['r'].Value -split ',' | ForEach-Object { $_.Trim().Trim('"',"'",' ',"`t","`r","`n") } | Where-Object { $_ })
                        if ($_fields.Count -gt 0) { $_dashRows += ,$_fields }
                    }
                    if ($_dashRows.Count -eq 0) { $_dashRows = $null }
                }
            }
            # [theme.font] -- pick up runtime font overrides for the font field.
            $_fontSec = [regex]::Match($_dashTomlText, '(?ms)^\[theme\.font\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
            if ($_fontSec.Success) {
                $_fb = $_fontSec.Groups['body'].Value
                $_fm = [regex]::Match($_fb, '(?m)^\s*family\s*=\s*"([^"]+)"')
                if ($_fm.Success) { $_dashFontF = $_fm.Groups[1].Value }
                $_sm = [regex]::Match($_fb, '(?m)^\s*size\s*=\s*(\d+)')
                if ($_sm.Success) { $_dashFontS = [int]$_sm.Groups[1].Value }
            }
        }
        if (-not $_dashRows) {
            $_dashRows = @(@('host_os'),@('cpu','gpu_discrete'),@('ram','swap'),@('disk_c','disk_m'),@('kernel','shell','font'))
        }

        foreach ($_row in $_dashRows) {
            $_n = @($_row).Count
            if ($_n -le 0) { continue }
            # Equal-width columns within the framed inner area.
            $_colW = [math]::Floor(($INNER - ($_n - 1) * 2) / $_n)
            if ($_colW -lt 8) { $_colW = 8 }
            $_cells = @()
            foreach ($_fk in $_row) {
                $_val = ''
                try {
                    $_val = & $_DashGetField $_fk $_dashFontF $_dashFontS
                } catch {
                    $_val = "$_fk : err"
                }
                if (-not $_val) { $_val = '' }
                if ($_val.Length -gt $_colW) {
                    $_val = $_val.Substring(0, [math]::Max(1, $_colW - 1)) + [char]0x2026
                }
                $_cells += $_val.PadRight($_colW)
            }
            try {
                Write-Host (_Frame (($_cells -join '  ').TrimEnd()))
            } catch {
                # Frame helper failed (rare -- ANSI strip or PadRight
                # overflow); print a placeholder so the render flow
                # continues and the divider/hints/bottom frame land.
                Write-Host (_Frame "  [dashboard row render failed]")
            }
        }
        $_devDistro = $null
        if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
            try {
                $env:WSL_UTF8 = '1'
                $_running = @(& wsl.exe --list --running --quiet 2>$null) |
                            ForEach-Object { ($_ -replace "`0", '').Trim() } |
                            Where-Object { $_ }
                foreach ($_c in @('podman-MiOS-DEV','MiOS-DEV')) {
                    if ($_running -contains $_c) { $_devDistro = $_c; break }
                }
            } catch {}
        }

        Write-Host ($LT + ($H * ($WIDTH - 2)) + $RT) -ForegroundColor Blue
        $_svcMode = if ($Full) { '--table-only' } else { '--endpoints-only' }
        $_svcLines = $null
        if ($_devDistro) {
            try {
                $_o = & wsl.exe -d $_devDistro --user mios -- bash -lc "COLUMNS=$WIDTH INNER=$INNER python3 /usr/libexec/mios/mios-dashboard $_svcMode --no-color" 2>$null
                if ($LASTEXITCODE -eq 0 -and $_o) { $_svcLines = @($_o) }
            } catch {}
        }
        if ($_svcLines) {
            foreach ($_sl in $_svcLines) { Write-Host (_Frame $_sl) -ForegroundColor Blue }
        } else {
            Write-Host (_Frame "  $_esc[90m[services: MiOS-DEV distro not running -- start with: mios dev]$_esc[0m") -ForegroundColor Blue
        }

        $_verbDefs = @(
            @{ name='build';  desc='open mios.html, save, then build the OCI image' },
            @{ name='config'; desc='edit mios.toml in the HTML configurator (no build)' },
            @{ name='dash';   desc='show this dashboard (framed banner + fastfetch info)' },
            @{ name='dev';    desc='enter the MiOS-DEV podman machine' },
            @{ name='pull';   desc='sync M:\ overlay to origin/main' },
            @{ name='update'; desc='re-run the bootstrap (cache-busted)' },
            @{ name='help';   desc='list every verb' }
        )
        try {
            $_tomlCands = @(
                (Join-Path $env:USERPROFILE '.config\mios\mios.toml'),
                'M:\etc\mios\mios.toml',
                'M:\usr\share\mios\mios.toml'
            )
            foreach ($_tc in $_tomlCands) {
                if ($_tc -and (Test-Path -LiteralPath $_tc)) {
                    $_tt = Get-Content -LiteralPath $_tc -Raw -ErrorAction SilentlyContinue
                    if (-not $_tt) { continue }
                    $_vb = [regex]::Match($_tt, '(?ms)^\[verbs\]\s*\r?\n(.*?)(?=^\[|\z)')
                    if ($_vb.Success) {
                        $_parsed = @()
                        foreach ($_ln in ($_vb.Groups[1].Value -split "`n")) {
                            $_pm = [regex]::Match($_ln, '^\s*([a-z][a-z0-9_-]*)\s*=\s*\{[^}]*description\s*=\s*"([^"]+)"')
                            if ($_pm.Success) { $_parsed += @{ name=$_pm.Groups[1].Value; desc=$_pm.Groups[2].Value } }
                        }
                        if ($_parsed.Count -gt 0) { $_verbDefs = $_parsed; break }
                    }
                }
            }
        } catch {}
        Write-Host ($LT + ($H * ($WIDTH - 2)) + $RT) -ForegroundColor Blue
        if ($_compact) {
            $_hint1 = (($_verbDefs | ForEach-Object { $_.name }) -join '  ')
            Write-Host (_Center $_hint1) -ForegroundColor DarkCyan
        } else {
            $_maxName = (($_verbDefs | ForEach-Object { $_.name.Length }) | Measure-Object -Maximum).Maximum
            foreach ($_v in $_verbDefs) {
                $_pad = ' ' * ($_maxName - $_v.name.Length + 2)
                Write-Host (_Frame ('  mios ' + $_v.name + $_pad + '-- ' + $_v.desc)) -ForegroundColor DarkCyan
            }
        }

        # Bottom frame.
        Write-Host ($BL + ($H * ($WIDTH - 2)) + $BR) -ForegroundColor Blue

        try {
            $_devCmd = $null
            if ($_devDistro) {
                $_out = & wsl.exe -d $_devDistro --user mios -- bash -lc 'bash /usr/libexec/mios/mios-ssh-dev-cmd' 2>$null
                if ($LASTEXITCODE -eq 0 -and $_out) { $_devCmd = ("$($_out | Select-Object -First 1)").Trim() }
            }
            if ($_devCmd) {
                Write-Host ("$_esc[1m$_esc[36mdev shell:$_esc[0m $_devCmd")
            }
        } catch {}
    }

    if (Get-Command oh-my-posh -ErrorAction SilentlyContinue) {
        # Shell-aware: oh-my-posh init pwsh emits PS 7+ syntax that
        # FAILS silently in Windows PowerShell 5.1, leaving the
        # operator's pre-existing broken init showing "CONFIG NOT
        # FOUND". Detect PS edition and use the matching arg
        # (powershell for 5.1 / Desktop, pwsh for 7+ / Core).
        $_ompShell = if ($PSVersionTable.PSEdition -eq 'Desktop') { 'powershell' } else { 'pwsh' }
        $ompInit = if ($miosOmp -and (Test-Path -LiteralPath $miosOmp)) {
            (oh-my-posh init $_ompShell --config $miosOmp --print) -join "`n"
        } else {
            (oh-my-posh init $_ompShell --print) -join "`n"
        }
        if ($ompInit) {
            $ompInit = [regex]::Replace($ompInit, 'Get-PSReadLineKeyHandler\s+(?!-)([A-Za-z][\w+]*)', 'Get-PSReadLineKeyHandler -Chord ''$1''')
            try { Invoke-Expression $ompInit } catch {}
        }
    }
}

$Script:MiosBootstrapRaw = 'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main'

function mios-build {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]$Args)
    $skipConfig = $Args -contains '-SkipConfig'
    $skipPull   = $Args -contains '-SkipPull'
    $forwardArgs = @($Args | Where-Object { $_ -notin @('-SkipConfig','-SkipPull') })

    # -- Step 1 + 2: configurator pass ------------------------------
    if (-not $skipConfig) {
        $cfgHtml = $null
        foreach ($c in @(
            'M:\usr\share\mios\configurator\mios.html',
            'M:\MiOS\usr\share\mios\configurator\mios.html'
        )) { if (Test-Path -LiteralPath $c) { $cfgHtml = $c; break } }
        if ($cfgHtml) {
            # Capture mtime BEFORE opening so we can tell if the operator
            # actually saved a new copy (the browser saves to Downloads
            # because file:// URLs can't write back to source). Used by
            # the promote step below.
            $cfgMtimeBefore = (Get-Item -LiteralPath $cfgHtml).LastWriteTimeUtc
            Write-Host ''
            Write-Host '  [1/4] Opening MiOS configurator in your browser...' -ForegroundColor Cyan
            Write-Host ('         '+$cfgHtml) -ForegroundColor DarkGray
            Write-Host '         Edit values, click Save -> the browser writes mios.toml' -ForegroundColor DarkGray
            Write-Host '         to your Downloads folder (file:// URLs cannot write back).' -ForegroundColor DarkGray
            try { Start-Process $cfgHtml | Out-Null } catch {}
            Write-Host ''
            Write-Host '  Press Enter when you''ve saved the configurator (or to skip the edit pass)...' -ForegroundColor Yellow -NoNewline
            $null = Read-Host
        } else {
            Write-Host '  [!] Configurator HTML not found on M:\ -- skipping edit pass.' -ForegroundColor Yellow
            Write-Host '      Run mios pull first to seed the overlay.' -ForegroundColor DarkGray
        }

        Write-Host ''
        Write-Host '  [2/4] Scanning Downloads for edited config files...' -ForegroundColor Cyan
        $dlDir = Join-Path $env:USERPROFILE 'Downloads'
        if (Test-Path -LiteralPath $dlDir) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            # mios.toml -> M:\etc\mios\mios.toml (+ /usr/share copy for
            # the dev VM via /mnt/m/etc/mios)
            $tomlSrc = Get-ChildItem -LiteralPath $dlDir -Filter 'mios*.toml' -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if ($tomlSrc) {
                $tomlDst = 'M:\etc\mios\mios.toml'
                $tomlPar = Split-Path -Parent $tomlDst
                if (-not (Test-Path -LiteralPath $tomlPar)) {
                    New-Item -ItemType Directory -Path $tomlPar -Force | Out-Null
                }
                Copy-Item -LiteralPath $tomlSrc.FullName -Destination $tomlDst -Force
                Write-Host ('         [+] '+$tomlSrc.Name+' -> '+$tomlDst) -ForegroundColor Green
                # Also copy to M:\usr\share\mios so the layered overlay
                # picks it up even before mios-pull runs.
                $tomlDst2 = 'M:\usr\share\mios\mios.toml'
                if (Test-Path -LiteralPath (Split-Path -Parent $tomlDst2)) {
                    Copy-Item -LiteralPath $tomlSrc.FullName -Destination $tomlDst2 -Force
                    Write-Host ('         [+] '+$tomlSrc.Name+' -> '+$tomlDst2) -ForegroundColor Green
                }
                # Archive the source so a re-run of mios build doesn't
                # re-promote the same file. Keep it (don't delete) so
                # the operator can recover if something went wrong.
                $archive = Join-Path $dlDir ($tomlSrc.BaseName+'.imported-'+$stamp+'.toml')
                Move-Item -LiteralPath $tomlSrc.FullName -Destination $archive -Force
            } else {
                Write-Host '         [-] no mios*.toml in Downloads -- using existing overlay' -ForegroundColor DarkGray
            }
            # Also pick up an edited HTML configurator (rare; the
            # configurator emits TOML by default but operators may save
            # a hand-edited HTML).
            $htmlSrc = Get-ChildItem -LiteralPath $dlDir -Filter '*mios*.html' -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notmatch '\.imported-' } |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if ($htmlSrc) {
                $htmlDst = 'M:\usr\share\mios\configurator\mios.html'
                $htmlPar = Split-Path -Parent $htmlDst
                if (-not (Test-Path -LiteralPath $htmlPar)) {
                    New-Item -ItemType Directory -Path $htmlPar -Force | Out-Null
                }
                Copy-Item -LiteralPath $htmlSrc.FullName -Destination $htmlDst -Force
                Write-Host ('         [+] '+$htmlSrc.Name+' -> '+$htmlDst) -ForegroundColor Green
                $archive = Join-Path $dlDir ($htmlSrc.BaseName+'.imported-'+$stamp+'.html')
                Move-Item -LiteralPath $htmlSrc.FullName -Destination $archive -Force
            }
        } else {
            Write-Host '         [-] '$dlDir' does not exist -- skipping promote' -ForegroundColor DarkGray
        }
    }

    # -- Step 3: sync overlay so the build sees the latest mios.toml -
    # Note: this runs AFTER the Downloads-promote step so mios-pull
    # sees the just-promoted files in M:\etc\mios. mios-pull's git
    # reset --hard would otherwise blow away the operator's changes
    # if they lived in the tracked tree.
    if (-not $skipPull) {
        Write-Host ''
        Write-Host '  [3/4] Syncing M:\ overlay (mios.git + mios-bootstrap)...' -ForegroundColor Cyan
        try { mios-pull } catch { Write-Host "  [!] mios-pull failed: $($_.Exception.Message)" -ForegroundColor Yellow }
    }

    # -- Step 4: ignite the build -----------------------------------
    Write-Host ''
    Write-Host '  [4/4] Running build pipeline (build-mios.ps1)...' -ForegroundColor Cyan
    $env:MIOS_DASHBOARD_MODE = 'log'
    $cb = [int][double]::Parse((Get-Date -UFormat %s))
    $src = Invoke-RestMethod -Uri "$Script:MiosBootstrapRaw/build-mios.ps1?cb=$cb" -Headers @{ 'Cache-Control' = 'no-cache' }
    & ([scriptblock]::Create($src)) @forwardArgs
}

function mios-update {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]$Args)
    $cb = [int][double]::Parse((Get-Date -UFormat %s))
    $src = Invoke-RestMethod -Uri "$Script:MiosBootstrapRaw/Get-MiOS.ps1?cb=$cb" -Headers @{ 'Cache-Control' = 'no-cache' }
    & ([scriptblock]::Create($src)) @Args
}

function mios-pull {
    if (-not (Test-Path 'M:\.git')) {
        Write-Host '  [!] M:\ is not a git working tree -- run mios-build first.' -ForegroundColor Yellow
        return
    }
    Push-Location 'M:\'
    try {
        git fetch --depth=1 origin main
        if ($LASTEXITCODE -eq 0) {
            git reset --hard FETCH_HEAD
            Write-Host '  [+] M:\ overlay synced to origin/main.' -ForegroundColor Green
        } else {
            Write-Host '  [!] git fetch failed -- check network.' -ForegroundColor Yellow
        }
    } finally { Pop-Location }
}

function mios-config {
    $cfg = if (Test-Path 'M:\usr\share\mios\configurator\mios.html') { 'M:\usr\share\mios\configurator\mios.html' }
           else { $null }
    if ($cfg) {
        Start-Process $cfg
        Write-Host "  [+] Opened $cfg" -ForegroundColor DarkGray
    } else {
        Write-Host '  [!] configurator not found -- run mios-build to deploy it.' -ForegroundColor Yellow
    }
}

function mios-dev {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]$Args)
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-Host '  [!] wsl.exe not on PATH -- WSL2 may not be installed.' -ForegroundColor Yellow
        return
    }
    # Probe for the actual on-disk WSL distro name. With the default
    # rename-skipped behavior (MIOS_RENAME_DISTRO unset), the distro is
    # 'podman-MiOS-DEV' (preserved from podman machine init so Podman
    # Desktop can see it). With opt-in rename, it's 'MiOS-DEV'. Either
    # works -- we resolve at call time so the helper survives both modes.
    $_devDistro = $null
    try {
        $_wsl = (& wsl.exe -l -q 2>$null) -split "
?
" |
            ForEach-Object { ($_ -replace [char]0,'').Trim() } |
            Where-Object { $_ }
        foreach ($_cand in @('podman-MiOS-DEV','MiOS-DEV','podman-MiOS-BUILDER','MiOS-BUILDER')) {
            if ($_wsl -contains $_cand) { $_devDistro = $_cand; break }
        }
    } catch {}
    if (-not $_devDistro) {
        Write-Host '  [!] No MiOS-DEV / podman-MiOS-DEV WSL distro registered. Run irm|iex one-liner to provision.' -ForegroundColor Yellow
        return
    }
    & wsl.exe -d $_devDistro --cd / --user mios @Args
}

function mios-metal {
    $py = if (Get-Command python.exe -ErrorAction SilentlyContinue) { 'python.exe' } else { 'python3' }
    $mon = @('C:\MiOS\usr\libexec\mios\mios-mon.py','C:\mios-bootstrap\installation\mios-mon.py','C:\mios-bootstrap\installation\MiOS-Mon.py','C:\MiOS\usr\libexec\mios\MiOS-Mon.py') | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($mon) {
        & $py $mon --mini
    } elseif (Get-Command Show-MiosDashboard -ErrorAction SilentlyContinue) {
        $cfg  = if (Test-Path 'M:\MiOS\fastfetch\config.jsonc') { 'M:\MiOS\fastfetch\config.jsonc' } else { '' }
        $logo = if (Test-Path 'M:\MiOS\fastfetch\mios.txt')      { 'M:\MiOS\fastfetch\mios.txt' }      else { '' }
        Show-MiosDashboard -ConfigPath $cfg -LogoPath $logo
    } else {
        Write-Host '  [!] mios mini: Show-MiosDashboard not loaded.' -ForegroundColor Yellow
    }
}

function mios-dash {
    $py = if (Get-Command python.exe -ErrorAction SilentlyContinue) { 'python.exe' } else { 'python3' }
    $mon = @('C:\MiOS\usr\libexec\mios\mios-mon.py','C:\mios-bootstrap\installation\mios-mon.py','C:\mios-bootstrap\installation\MiOS-Mon.py','C:\MiOS\usr\libexec\mios\MiOS-Mon.py') | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($mon) {
        & $py $mon --dash
    } elseif (Get-Command Show-MiosDashboard -ErrorAction SilentlyContinue) {
        $cfg  = if (Test-Path 'M:\MiOS\fastfetch\config.jsonc') { 'M:\MiOS\fastfetch\config.jsonc' } else { '' }
        $logo = if (Test-Path 'M:\MiOS\fastfetch\mios.txt') { 'M:\MiOS\fastfetch\mios.txt' }
                elseif (Test-Path 'M:\usr\share\mios\branding\mios.txt') { 'M:\usr\share\mios\branding\mios.txt' }
                else { '' }
        Show-MiosDashboard -ConfigPath $cfg -LogoPath $logo -Full
    } else {
        Write-Host '  [!] mios dash: Show-MiosDashboard not loaded.' -ForegroundColor Yellow
    }
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
    Write-Host '  mios mon      interactive full-screen TUI monitor (Textual / dashboard)' -ForegroundColor White
    Write-Host '  mios dash     FULL dashboard: ASCII banner + services + extended sys specs' -ForegroundColor White
    Write-Host '  mios xbox     Xbox VM Secure Boot / XML repair' -ForegroundColor White
    Write-Host '  mios virt     apply optimized VM config + CPU pinning' -ForegroundColor White
    Write-Host '  mios vfio     configure GPU/USB passthrough (Isolation)' -ForegroundColor White
    Write-Host '  mios help     this list' -ForegroundColor White
    Write-Host ''
}

function mios-mon {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]$Args)
    $py = if (Get-Command python.exe -ErrorAction SilentlyContinue) { 'python.exe' } else { 'python3' }
    $mon = @('M:\usr\libexec\mios\mios-mon.py','C:\MiOS\usr\libexec\mios\mios-mon.py','C:\mios-bootstrap\installation\mios-mon.py','M:\MiOS\repo\mios\usr\libexec\mios\mios-mon.py') | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($mon) {
        if ($Args.Count -eq 0) {
            & $py $mon --monitor
        } else {
            & $py $mon @Args
        }
    } else {
        Write-Host "  [!] mios mon: mios-mon.py not found on M:\ or C:\" -ForegroundColor Yellow
    }
}

function mios-monitor {
    [CmdletBinding()]
    param([Parameter(ValueFromRemainingArguments)]$Args)
    mios-mon @Args
}

$Script:MiosKnownVerbs = @('build','update','pull','config','ai','dev','dash','mini','mon','monitor','help','code','xbox','virt','vfio','tune','summary','profile','assess','iommu','theme','user')

function mios {
    [CmdletBinding()]
    param(
        [Parameter(Position=0)]
        [string]$Verb,
        [Parameter(ValueFromRemainingArguments)]
        $RemainingArgs
    )
    if (-not $Verb) { $Verb = 'help' }
    if ($Script:MiosKnownVerbs -contains $Verb.ToLowerInvariant()) {
        $cmd = "mios-$($Verb.ToLowerInvariant())"
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            & $cmd @RemainingArgs
        } else {
            Write-Host "  [!] mios: verb '$Verb' wrapper not found. Try: mios help" -ForegroundColor Yellow
        }
        return
    }
    # Free-form query -> Hermes-Agent /v1/chat/completions.
    $_query = (@($Verb) + @($RemainingArgs)) -join ' '
    # $Global:MiosBin may be unset (this profile is dot-sourced standalone from
    # the $PROFILE redirector). Guard it -- Join-Path throws on a null Path,
    # which would surface a raw binder error instead of the friendly hint below.
    if (-not $Global:MiosBin) {
        Write-Host "  [!] mios-ask.ps1 not staged. Try: mios help" -ForegroundColor Yellow
        return
    }
    $_ask = Join-Path $Global:MiosBin 'mios-ask.ps1'
    if (Test-Path -LiteralPath $_ask) {
        & $_ask $_query
    } else {
        Write-Host "  [!] mios-ask.ps1 not staged. Try: mios help" -ForegroundColor Yellow
    }
}

Register-ArgumentCompleter -CommandName mios -ParameterName Verb -ScriptBlock {
    param($cmdName, $paramName, $wordToComplete, $cmdAst, $fakeBoundParam)
    $Script:MiosKnownVerbs |
        Where-Object { $_ -like "$wordToComplete*" } |
        ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
}

function _MiosResolveStartupVerb {
    $_cands = @(
        (Join-Path $env:USERPROFILE '.config\mios\mios.toml'),
        'M:\etc\mios\mios.toml',
        'M:\usr\share\mios\mios.toml'
    )
    foreach ($_c in $_cands) {
        if (-not (Test-Path -LiteralPath $_c)) { continue }
        try {
            $_t = [IO.File]::ReadAllText($_c, (New-Object System.Text.UTF8Encoding($false)))
        } catch { continue }
        $_sec = [regex]::Match($_t, '(?ms)^\[terminal\.startup\]\s*\r?\n(?<body>.*?)(?=^\[[^\]]+\]|\z)')
        if (-not $_sec.Success) { continue }
        $_body = $_sec.Groups['body'].Value
        # Per-platform key wins over cross-platform 'verb' key.
        $_keys = @('windows','verb')
        foreach ($_k in $_keys) {
            $_m = [regex]::Match($_body, ('(?m)^\s*' + [regex]::Escape($_k) + '\s*=\s*"([^"]*)"'))
            if ($_m.Success) { return $_m.Groups[1].Value.Trim() }
        }
    }
    # Vendor fallback: mini (the compact 80x20 framed banner).
    # dash is the FULL render -- ASCII banner + service status +
    # extended sys specs -- explicitly invoked by the operator,
    # not auto-fired on every shell spawn.
    return 'mini'
}

if (-not $Global:MiosStartupVerbFired -and $Host.UI.RawUI -and (-not $env:MIOS_SKIP_MOTD) -and $env:WT_SESSION) {
    $Global:MiosStartupVerbFired = $true
    $_startupVerb = _MiosResolveStartupVerb
    if ($_startupVerb) {
        try { mios $_startupVerb } catch {}
    }
}
try {
    $_diagDir = 'M:\MiOS\diagnostics'
    if (-not (Test-Path -LiteralPath $_diagDir)) { New-Item -ItemType Directory -Path $_diagDir -Force | Out-Null }
    $_diagFile = Join-Path $_diagDir 'window-width.txt'
    $_ww = try { [Console]::WindowWidth } catch { '?' }
    $_bw = try { $Host.UI.RawUI.BufferSize.Width } catch { '?' }
    $_wh = try { [Console]::WindowHeight } catch { '?' }
    $_wt = if ($env:WT_SESSION) { 'WT' } else { 'conhost-or-other' }
    $_ts = (Get-Date).ToString('s')
    Add-Content -LiteralPath $_diagFile -Value ("{0} WindowWidth={1} BufferWidth={2} WindowHeight={3} host={4} pwsh={5}" -f $_ts, $_ww, $_bw, $_wh, $_wt, $PSVersionTable.PSVersion)
} catch {}
# -- end MiOS WindowWidth diagnostic --
