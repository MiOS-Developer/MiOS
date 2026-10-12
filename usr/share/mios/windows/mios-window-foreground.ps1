# AI-hint: PowerShell script used by mios-windows to force a specific Windows process's main window into the foreground via AppActivate after a WSL-init...
# AI-doc: usr/share/doc/mios/manual/windows.md

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProcessName,
    [Parameter()][int]$WaitMs = 200,
    [Parameter()][int]$MaxAttempts = 5
)

# Strip a trailing .exe -- Get-Process expects the bare name.
$ProcessName = $ProcessName -replace '\.exe$', ''

# Wait for the process to appear (the launch + window-creation race
# is real on slower spawn paths; ~1s budget).
for ($i = 0; $i -lt $MaxAttempts; $i++) {
    $proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 } |
            Sort-Object StartTime -Descending |
            Select-Object -First 1
    if ($proc) { break }
    Start-Sleep -Milliseconds $WaitMs
}

if (-not $proc) {
    Write-Output ("[mios-window-foreground] no '{0}' process with a window found after {1} attempts" -f $ProcessName, $MaxAttempts)
    exit 1
}

# AppActivate is the documented "raise this window" call that doesn't
# need a Win32 P/Invoke. Returns $true on success.
$shell = New-Object -ComObject WScript.Shell
$ok = $shell.AppActivate($proc.Id)

Write-Output ("[mios-window-foreground] {0} PID {1} window='{2}' foreground={3}" -f $ProcessName, $proc.Id, $proc.MainWindowTitle, $ok)
exit 0
