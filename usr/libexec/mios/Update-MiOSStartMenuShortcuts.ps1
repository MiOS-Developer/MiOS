# AI-hint: Powershell script that generates native Windows .lnk shortcuts for all .desktop files (including Flatpaks) in the MiOS-DEV WSL distro to ens...
# AI-doc: usr/share/doc/mios/manual/mios.md

[CmdletBinding()]
param(
    [string]$Distro    = 'podman-MiOS-DEV',
    [string]$LinuxUser = 'mios',
    # Folder default = "MiOS" -- NOT the distro name. Earlier versions
    # wrote to %APPDATA%\...\Programs\<distro>\ to match Microsoft's
    # native WSL2 Start Menu sync, but Microsoft's wslservice TREATS
    # that folder AS ITS OWN: every WSL distro restart it re-enumerates
    # apps from the distro side using a much more restrictive filter
    # (NoDisplay + Terminal filtering plus its own ad-hoc rules), then
    # DELETES every .lnk in the folder that doesn't match. Result:
    # operator goes from 46 properly-iconed apps to 3 after `wsl
    # -shutdown`. Operator-flagged "no apps on windows
    # again!!!" -- the second time the WSL stomp wiped the shortcuts.
    #
    # Writing to a distinct folder lets MS manage its 3-app distro
    # folder and lets MiOS own its 46-app folder side-by-side. They
    # appear next to each other in Start Menu.
    [string]$Folder    = 'MiOS Apps'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$modulePath = Join-Path $PSScriptRoot 'MiOSShortcutUtils.psm1'
if (Test-Path $modulePath) { Import-Module $modulePath -ErrorAction SilentlyContinue }

$wslgExe = 'C:\Program Files\WSL\wslg.exe'
if (-not (Test-Path $wslgExe)) {
    # Fallback: bare wsl.exe (console-mode -- will flash a window).
    # Better than nothing; warn so operator knows.
    $wslgExe = Join-Path $env:WINDIR 'System32\wsl.exe'
    Write-Host "  [!] wslg.exe not found; falling back to wsl.exe (apps will flash a console window)." -ForegroundColor Yellow
}

# WSL2's icon staging directory -- match native layout so an operator
# uninstall script that targets WSLDVCPlugin\* sweeps our icons too.
$iconStageDir = Join-Path $env:LOCALAPPDATA "Temp\WSLDVCPlugin\$Distro"
if (-not (Test-Path $iconStageDir)) {
    New-Item -ItemType Directory -Path $iconStageDir -Force | Out-Null
}

$defaultIcoPath = Join-Path $iconStageDir '_mios-default.ico'
$defaultSrcPng = $null
foreach ($cand in @(
    'C:\MiOS\usr\share\mios\branding\icon.png',
    'C:\MiOS\usr\share\mios\branding\_mios-default.ico',
    'M:\MiOS\branding\icon.png',
    'M:\MiOS\branding\mios.png'
)) {
    if (Test-Path $cand) { $defaultSrcPng = $cand; break }
}

$startMenuRoot = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$outDir = Join-Path $startMenuRoot $Folder
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

# Locate the enumerator + rasterize helpers. Both are shipped at
# /usr/libexec/mios/ in the deployed tree (C:\MiOS\... in the dev
# working copy).
$enumSh = $null
$rasterSh = $null
foreach ($cand in @(
    'C:\MiOS\usr\libexec\mios\enumerate-mios-desktops.sh',
    "$env:TEMP\enumerate-desktops.sh"
)) {
    if (Test-Path $cand) { $enumSh = $cand; break }
}
foreach ($cand in @(
    'C:\MiOS\usr\libexec\mios\rasterize-mios-icons.sh',
    "$env:TEMP\rasterize-mios-icons.sh"
)) {
    if (Test-Path $cand) { $rasterSh = $cand; break }
}
if (-not $enumSh) {
    Write-Host "  [!] enumerate-mios-desktops.sh not found -- aborting" -ForegroundColor Yellow
    return
}

$wslEnumPath = ($enumSh -replace '\\', '/' -replace '^([A-Za-z]):', '/mnt/$1').ToLower()
$lines = & wsl.exe -d $Distro --user $LinuxUser -- bash $wslEnumPath 2>$null
if (-not $lines -or $lines.Count -eq 0) {
    Write-Host "  [!] No .desktop entries returned from $Distro" -ForegroundColor Yellow
    return
}

# Resolve + rasterize every needed icon via the staged rasterizer
# script (PS-generated heredoc bash has BOM/CRLF issues that break
# bash; using a checked-in .sh under /usr/libexec/mios/ avoids that).
$iconNames = $lines | ForEach-Object {
    $p = $_ -split '\|', 7
    if ($p.Count -ge 3 -and $p[2]) { $p[2].Trim() }
} | Where-Object { $_ } | Sort-Object -Unique

# DEPRECATED: keep the inline script below as fallback for hosts that
# don't have the staged rasterize-mios-icons.sh yet.
$iconBatchScript = @'
TARGET_DIR=/tmp/mios-icon-stage
mkdir -p "$TARGET_DIR"
rm -f "$TARGET_DIR"/*.png 2>/dev/null

resolve_icon() {
    local name="$1"
    # Absolute path
    if [[ "$name" == /* ]] && [ -e "$name" ]; then echo "$name"; return; fi
    # Walk hicolor sizes (prefer larger), then pixmaps.
    for sz in 512x512 256x256 128x128 64x64 48x48 scalable 32x32 24x24 16x16; do
        for base in /usr/share/icons/hicolor /var/lib/flatpak/exports/share/icons/hicolor; do
            for ext in png svg xpm; do
                f="$base/$sz/apps/$name.$ext"
                [ -e "$f" ] && { echo "$f"; return; }
            done
        done
    done
    for f in "/usr/share/pixmaps/$name.png" "/usr/share/pixmaps/$name.svg" "/usr/share/pixmaps/$name"; do
        [ -e "$f" ] && { echo "$f"; return; }
    done
}

while IFS= read -r name; do
    [ -z "$name" ] && continue
    src=$(resolve_icon "$name")
    if [ -z "$src" ]; then
        printf '%s|MISSING\n' "$name"
        continue
    fi
    out="$TARGET_DIR/$name.png"
    # Detect ACTUAL file format via `file` -- flatpak apps sometimes
    # ship a PNG under a .svg name (e.g. Chrome's
    # com.google.ChromeDev.svg is binary PNG data). Dispatch on the
    # real magic, not the extension.
    mime=$(file -Lb --mime-type "$src" 2>/dev/null)
    case "$mime" in
        image/svg+xml)
            if command -v rsvg-convert >/dev/null 2>&1; then
                rsvg-convert -w 256 -h 256 -o "$out" "$src" 2>/dev/null || cp -f "$src" "$out"
            elif command -v magick >/dev/null 2>&1; then
                magick -background none -density 256 "$src" -resize 256x256 "$out" 2>/dev/null || cp -f "$src" "$out"
            elif command -v convert >/dev/null 2>&1; then
                convert -background none -density 256 "$src" -resize 256x256 "$out" 2>/dev/null || cp -f "$src" "$out"
            else
                cp -f "$src" "$out"
            fi
            ;;
        image/png|image/jpeg|image/x-icon|image/vnd.microsoft.icon)
            cp -f "$src" "$out"
            ;;
        image/x-xpixmap|image/x-pixmap)
            command -v convert >/dev/null 2>&1 && convert "$src" "$out" 2>/dev/null || cp -f "$src" "$out"
            ;;
        *)
            # Unknown -- still copy; .lnk IconLocation tolerates PNG even
            # at non-canonical extension. .NET's PNG->ICO converter
            # below will reject if truly garbage; we degrade gracefully.
            cp -f "$src" "$out" 2>/dev/null
            ;;
    esac
    if [ -f "$out" ]; then
        printf '%s|%s\n' "$name" "$out"
    else
        printf '%s|FAILED\n' "$name"
    fi
done
'@

# Prefer the staged rasterizer script (no BOM/CRLF issues) over the
# PS-generated heredoc.
if ($rasterSh) {
    $wslBatchPath = ($rasterSh -replace '\\', '/' -replace '^([A-Za-z]):', '/mnt/$1').ToLower()
} else {
    $iconBatchPath = Join-Path $env:TEMP 'mios-icon-batch.sh'
    [System.IO.File]::WriteAllText($iconBatchPath, $iconBatchScript.Replace("`r`n","`n"), (New-Object System.Text.UTF8Encoding($false)))
    $wslBatchPath = ($iconBatchPath -replace '\\', '/' -replace '^([A-Za-z]):', '/mnt/$1').ToLower()
}

# Stream icon names via stdin to the rasterizer.
$iconMap = @{}
if ($iconNames) {
    $stagedLines = $iconNames -join "`n" | & wsl.exe -d $Distro --user $LinuxUser -- bash $wslBatchPath 2>$null
    foreach ($r in $stagedLines) {
        if ([string]::IsNullOrWhiteSpace($r)) { continue }
        $pp = $r -split '\|', 2
        if ($pp.Count -ge 2 -and $pp[1] -notin @('MISSING','FAILED')) {
            $iconMap[$pp[0]] = $pp[1]
        }
    }
}

# PNG -> ICO converter that EMBEDS the PNG bytes inside an ICO
# container. This is the Vista+ "PNG-encoded ICO" format Windows
# Start Menu renders cleanly at every size from 16x16 to 256x256.
#
# Why NOT .NET's Icon.Save() / Bitmap.GetHicon: those produce
# single-image .ico files in 32-bit BMP format with a 32x32 source,
# which Windows upscales badly at 256x256. The resulting Start Menu
# tile shows either a generic icon or a blurry mess. Operator-flagged
# twice: "no icons match", "NEVER saw native icons -- NOT
# even ONCE". A PNG-embedded ICO matches what flatpak / Microsoft
# Store / WSL's own sync produce.
#
# Format (Vista+ PNG ICO):
#   ICONDIR     (6 bytes): 00 00 | 01 00 | 01 00
#   ICONDIRENTRY (16 bytes per image):
#     bWidth(1)   bHeight(1)  bColorCount(1)  bReserved(1)
#     wPlanes(2)  wBitCount(2)  dwBytesInRes(4)  dwImageOffset(4)
#   Image data: raw PNG bytes (NOT XOR/AND masks like classic ICO)
function Convert-PngToIco {
    param([string]$PngPath, [string]$IcoPath)
    try {
        $png = [IO.File]::ReadAllBytes($PngPath)
        if ($png.Length -lt 24) { return $false }
        # Read width/height from PNG IHDR chunk. IHDR starts at byte 16
        # (after the 8-byte signature + 4-byte chunk-length + 4-byte
        # "IHDR" type). Width = bytes 16..19 BE; height = 20..23 BE.
        $w = ([int]$png[16] -shl 24) -bor ([int]$png[17] -shl 16) -bor ([int]$png[18] -shl 8) -bor [int]$png[19]
        $h = ([int]$png[20] -shl 24) -bor ([int]$png[21] -shl 16) -bor ([int]$png[22] -shl 8) -bor [int]$png[23]
        # In ICO, width/height of 0 means 256.
        $icoW = if ($w -ge 256) { 0 } else { [byte]$w }
        $icoH = if ($h -ge 256) { 0 } else { [byte]$h }

        $ms = New-Object System.IO.MemoryStream
        $bw = New-Object System.IO.BinaryWriter($ms)
        # ICONDIR
        $bw.Write([uint16]0)         # idReserved
        $bw.Write([uint16]1)         # idType (1 = icon)
        $bw.Write([uint16]1)         # idCount
        # ICONDIRENTRY
        $bw.Write([byte]$icoW)       # bWidth
        $bw.Write([byte]$icoH)       # bHeight
        $bw.Write([byte]0)           # bColorCount (0 for 256+ colors)
        $bw.Write([byte]0)           # bReserved
        $bw.Write([uint16]1)         # wPlanes
        $bw.Write([uint16]32)        # wBitCount
        $bw.Write([uint32]$png.Length)  # dwBytesInRes
        $bw.Write([uint32]22)        # dwImageOffset (6 + 16)
        # Image data
        $bw.Write($png)
        $bw.Flush()
        [IO.File]::WriteAllBytes($IcoPath, $ms.ToArray())
        $bw.Close()
        return $true
    } catch {
        return $false
    }
}

# Stage the default fallback icon now that the converter is defined.
if ($defaultSrcPng -and (-not (Test-Path $defaultIcoPath))) {
    Convert-PngToIco -PngPath $defaultSrcPng -IcoPath $defaultIcoPath | Out-Null
}

function Clear-WindowsIconCache {
    # Force the Shell to re-read .lnk IconLocation values WITHOUT
    # nuking the icon cache database -- previous version called
    # `ie4uinit.exe -ClearIconCache` which dropped every Start Menu
    # icon to blank until Explorer was manually restarted. Operator-
    # flagged "the icons disappeared now!!!".
    #
    # Lighter approach: touch every .lnk mtime + the SHChangeNotify
    # broadcast. The Shell watches .lnk mtimes for change; touching
    # invalidates the per-shortcut icon cache entry without affecting
    # other Start Menu items.
    $now = Get-Date
    foreach ($lnk in (Get-ChildItem -LiteralPath $outDir -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
        try { $lnk.LastWriteTime = $now } catch {}
    }
    # SHChangeNotify(SHCNE_ASSOCCHANGED) tells the Shell to flush
    # association + per-item icon caches. Non-destructive; instant.
    try {
        Add-Type -Namespace MiosWin -Name Shell32 -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int eventId, int flags, System.IntPtr item1, System.IntPtr item2);
'@ -ErrorAction SilentlyContinue
        # SHCNE_ASSOCCHANGED = 0x08000000; SHCNF_IDLIST = 0
        [MiosWin.Shell32]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
    } catch {}
}

# Sweep ALL .lnk files in our managed folder so renamed / removed apps
# don't leave orphan shortcuts. Also clear the legacy `<distro>` folder
# (where shortcuts USED to land before Microsoft's wslservice started
# stomping it on every distro restart) -- if it still has our
# leftover .lnks, they'll appear in Start Menu as duplicates next to
# the new MiOS Apps folder.
$legacyFolder = Join-Path $startMenuRoot $Distro
if (Test-Path $legacyFolder) {
    Get-ChildItem -LiteralPath $legacyFolder -Filter '*.lnk' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
Get-ChildItem -LiteralPath $outDir -Filter '*.lnk' -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

$wshShell = New-Object -ComObject WScript.Shell
$created = 0
$skipped = 0
foreach ($line in $lines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split '\|', 7
    if ($parts.Count -lt 7) { continue }
    $name    = $parts[0].Trim()
    $execRaw = $parts[1].Trim()
    $iconRaw = $parts[2].Trim()
    $desktopId = [IO.Path]::GetFileNameWithoutExtension($parts[6].Trim())
    if (-not $name -or -not $execRaw -or $desktopId -notmatch '^[A-Za-z0-9_.-]+$') { $skipped++; continue }

    # Strip .desktop %f/%F/%u/%U/etc field-codes.
    $execClean = "/usr/libexec/mios/mios-gui $desktopId"

    # Icon resolution: copy the rasterized PNG out to WSLDVCPlugin\
    # then convert PNG -> ICO so Start Menu renders crisply.
    $iconLocation = $null
    if ($iconRaw -and $iconMap.ContainsKey($iconRaw)) {
        $wslPngPath = "\\wsl.localhost\$Distro$($iconMap[$iconRaw])" -replace '/', '\'
        $localPng = Join-Path $iconStageDir "$iconRaw.png"
        $localIco = Join-Path $iconStageDir "$iconRaw.ico"
        try {
            Copy-Item -LiteralPath $wslPngPath -Destination $localPng -Force -ErrorAction Stop
            if (Convert-PngToIco -PngPath $localPng -IcoPath $localIco) {
                $iconLocation = "$localIco,0"
            } else {
                $iconLocation = "$localPng,0"
            }
        } catch {}
    }
    # Fallback: every shortcut MUST have an IconLocation set or Windows
    # renders the generic .lnk icon. If icon resolution failed (no
    # Icon= field, broken icon path, missing theme), point at the
    # shared MiOS distro icon staged below.
    # "I want all the Linux apps icons visible in windows".
    if (-not $iconLocation -and (Test-Path $defaultIcoPath)) {
        $iconLocation = "$defaultIcoPath,0"
    }

    # NATIVE WSL filename pattern: "<App Display Name> (<distro>).lnk".
    # Matches the exact convention WSL's built-in sync uses, so the
    # operator sees one consistent set of shortcuts (ours + WSL's
    # native sync share the same filenames -> de-dup at write).
    $sanitizedName = ($name -replace '[\\/:*?"<>|]', '_').Trim()
    $lnkFileName   = "$sanitizedName ($Distro).lnk"
    $lnkPath = Join-Path $outDir $lnkFileName
    $shortcut = $wshShell.CreateShortcut($lnkPath)
    # NATIVE Microsoft WSL pattern (reverse-engineered from a WSL-
    # generated shortcut on Win11 26H1):
    #   TargetPath       = C:\Program Files\WSL\wslg.exe  (GUI, no console)
    #   Arguments        = -d <distro> --cd "~" -- <exec line>
    #   WorkingDirectory = C:\WINDOWS\system32
    #   WindowStyle      = 7   (Minimized -- no flash; wslg handles UI)
    #   IconLocation     = <path-to-ico>,0
    $shortcut.TargetPath       = $wslgExe
    $shortcut.Arguments        = "-d $Distro --cd `"~`" -- $execClean"
    $shortcut.WorkingDirectory = Join-Path $env:WINDIR 'system32'
    $shortcut.Description      = "$name (in $Distro)"
    $shortcut.WindowStyle      = 7
    if ($iconLocation) { $shortcut.IconLocation = $iconLocation }
    $shortcut.Save()
    $created++
}

Write-Host "  [+] $created Start Menu shortcuts written to $outDir (native Microsoft WSL pattern)" -ForegroundColor Green

# ─── "MiOS Full Desktop" Enhanced Session shortcut ───────────────────
# Alternate launch path that opens the full GNOME desktop via mstsc.exe
# connecting to the xrdp service in the dev VM. Set up by automation/
# 35-xrdp-enhanced-session.sh at install time. Lives alongside the
# per-window app shortcuts in the same MiOS Apps folder so the operator
# can pick per session (per-window for native-Windows-window feel,
# Full Desktop for libadwaita-uniform rendering + Bibata cursor).
# Operator directive "Full Enhanced Session is an alternate
# launch option installed at irm|iex invoke and installation".
$enhSessPort = 3389
$enhSessTomlPaths = @(
    'M:\usr\share\mios\mios.toml',
    'C:\MiOS\usr\share\mios\mios.toml',
    "$env:USERPROFILE\.config\mios\mios.toml"
)
foreach ($tomlPath in $enhSessTomlPaths) {
    if (-not (Test-Path -LiteralPath $tomlPath)) { continue }
    $tomlText = [IO.File]::ReadAllText($tomlPath, [Text.UTF8Encoding]::new($false))
    $match = [regex]::Match($tomlText, '(?ms)^\[enhanced_session\].*?(?:^port\s*=\s*(\d+))')
    if ($match.Success -and $match.Groups[1].Success) {
        $enhSessPort = [int]$match.Groups[1].Value
        break
    }
}
# Nested GNOME approach: WSLg launches /usr/bin/mios-full-desktop in
# the distro, which exec's gnome-session inside `gnome-shell --nested`.
# Mutter runs as a Wayland CLIENT of WSLg's Weston, hosting the entire
# GNOME desktop in one window. All cursor + theme + decoration rendering
# happens INSIDE that nested compositor -- bypasses every WSLg-per-window
# rendering limit at once (Bibata + rounded corners + libadwaita-uniform
# everything just work because Mutter draws final pixels itself).
$enhSessLnkPath = Join-Path $outDir "MiOS Full Desktop ($Distro).lnk"
$enhSessShortcut = $wshShell.CreateShortcut($enhSessLnkPath)
$enhSessShortcut.TargetPath       = $wslgExe
$enhSessShortcut.Arguments        = "-d $Distro --cd `"~`" -- /usr/bin/mios-full-desktop"
$enhSessShortcut.WorkingDirectory = Join-Path $env:WINDIR 'System32'
$enhSessShortcut.Description      = "Full GNOME desktop in $Distro via gnome-shell --nested inside WSLg -- libadwaita-uniform rendering, Bibata cursor, rounded corners"
$enhSessShortcut.WindowStyle      = 7
if (Test-Path $defaultIcoPath) { $enhSessShortcut.IconLocation = "$defaultIcoPath,0" }
$enhSessShortcut.Save()
Write-Host "  [+] MiOS Full Desktop shortcut -> wslg.exe ... mios-full-desktop (nested GNOME)" -ForegroundColor Green

# Flush the icon cache so the freshly-embedded PNG-ICO files render
# in the Start Menu without a sign-out / reboot.
Clear-WindowsIconCache
if ($skipped -gt 0) {
    Write-Host "  [-] $skipped entries skipped (NoDisplay / Terminal / missing Exec)" -ForegroundColor DarkGray
}
