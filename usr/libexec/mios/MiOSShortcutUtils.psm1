# AI-hint: Shared PowerShell module providing icon rasterization, PNG-to-ICO conversion, icon cache clearing, and WSLg native shortcut creation.
# AI-related: Update-MiOSStartMenuShortcuts.ps1, tools/refresh-flatpak-shortcuts.ps1

function Convert-PngToIco {
    param([string]$PngPath, [string]$IcoPath)
    try {
        if (-not (Test-Path -LiteralPath $PngPath)) { return $false }
        $png = [IO.File]::ReadAllBytes($PngPath)
        if ($png.Length -lt 24) { return $false }
        $w = ([int]$png[16] -shl 24) -bor ([int]$png[17] -shl 16) -bor ([int]$png[18] -shl 8) -bor [int]$png[19]
        $h = ([int]$png[20] -shl 24) -bor ([int]$png[21] -shl 16) -bor ([int]$png[22] -shl 8) -bor [int]$png[23]
        $icoW = if ($w -ge 256) { 0 } else { [byte]$w }
        $icoH = if ($h -ge 256) { 0 } else { [byte]$h }

        $ms = New-Object System.IO.MemoryStream
        $bw = New-Object System.IO.BinaryWriter($ms)
        $bw.Write([uint16]0)
        $bw.Write([uint16]1)
        $bw.Write([uint16]1)
        $bw.Write([byte]$icoW)
        $bw.Write([byte]$icoH)
        $bw.Write([byte]0)
        $bw.Write([byte]0)
        $bw.Write([uint16]1)
        $bw.Write([uint16]32)
        $bw.Write([uint32]$png.Length)
        $bw.Write([uint32]22)
        $bw.Write($png)
        $bw.Flush()
        $icoDir = Split-Path -Parent $IcoPath
        if ($icoDir -and -not (Test-Path $icoDir)) { New-Item -ItemType Directory -Path $icoDir -Force | Out-Null }
        [IO.File]::WriteAllBytes($IcoPath, $ms.ToArray())
        $bw.Close()
        return $true
    } catch {
        return $false
    }
}

function Clear-WindowsIconCache {
    param([string]$LnkDir = '')
    if ($LnkDir -and (Test-Path -LiteralPath $LnkDir)) {
        $now = Get-Date
        foreach ($lnk in (Get-ChildItem -LiteralPath $LnkDir -Filter '*.lnk' -ErrorAction SilentlyContinue)) {
            try { $lnk.LastWriteTime = $now } catch {}
        }
    }
    try {
        Add-Type -Namespace MiosWin -Name Shell32 -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int eventId, int flags, System.IntPtr item1, System.IntPtr item2);
'@ -ErrorAction SilentlyContinue
        [MiosWin.Shell32]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
    } catch {}
}

function New-MiosWslShortcut {
    param(
        [Parameter(Mandatory=$true)][string]$LnkPath,
        [Parameter(Mandatory=$true)][string]$Distro,
        [Parameter(Mandatory=$true)][string]$ExecCmd,
        [string]$IconLocation = '',
        [string]$Description = ''
    )
    $wslgExe = 'C:\Program Files\WSL\wslg.exe'
    if (-not (Test-Path $wslgExe)) {
        $wslgExe = Join-Path $env:WINDIR 'System32\wsl.exe'
    }
    
    $parent = Split-Path -Parent $LnkPath
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }

    $wshShell = New-Object -ComObject WScript.Shell
    $shortcut = $wshShell.CreateShortcut($LnkPath)
    $shortcut.TargetPath = $wslgExe
    $shortcut.Arguments = "-d $Distro --cd `"`~`" -- $ExecCmd"
    $shortcut.WindowStyle = 7
    if ($IconLocation) {
        $shortcut.IconLocation = "$IconLocation,0"
    } else {
        $shortcut.IconLocation = "imageres.dll,15"
    }
    if ($Description) {
        $shortcut.Description = $Description
    }
    $shortcut.Save()
}

Export-ModuleMember -Function Convert-PngToIco, Clear-WindowsIconCache, New-MiosWslShortcut
