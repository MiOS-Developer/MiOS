# AI-hint: Keep MiOS terminal and agent verbs in the invoking PowerShell terminal while retaining the existing Windows management dispatcher.
# AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, Get-MiOS.ps1
param([string]$BinDirectory = $PSScriptRoot)

$entry = Join-Path $BinDirectory 'mios-native-entry.ps1'
if (-not (Test-Path -LiteralPath $entry)) { return }
$current = Get-Command mios -CommandType Function -ErrorAction SilentlyContinue
if ($current -and $current.Definition -notlike '*MiOS native in-terminal dispatch*') {
    $global:MiosLegacyDispatcher = $current.ScriptBlock
}
$global:MiosNativeEntry = $entry
function global:Invoke-MiosNativeDashboard {
    [CmdletBinding()]
    param([string[]]$Arguments = @())
    $bin = Split-Path $global:MiosNativeEntry
    $binding = Get-Content -Raw -LiteralPath (Join-Path $bin 'native-binding.json') | ConvertFrom-Json
    $resolved = & wsl.exe -d $binding.distro -u $binding.linuxUser -- /usr/bin/mios-resolver --emit=json
    if ($LASTEXITCODE -ne 0) { throw 'Live layered SSOT resolution failed; refusing a cached dashboard.' }
    $width = $Host.UI.RawUI.WindowSize.Width
    $resolved -join "`n" | & (Join-Path $bin 'mios-gen.exe') dashboard --resolved-stdin --width $width @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Native dashboard failed (exit $LASTEXITCODE)" }
}
function global:Show-MiosDashboard {
    param([string]$ConfigPath, [string]$LogoPath)
    Invoke-MiosNativeDashboard
}
function global:mios {
    # MiOS native in-terminal dispatch
    [CmdletBinding()]
    param([Parameter(Position=0)][string]$Verb,
          [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    if ($null -eq $Arguments) { $Arguments = @() }
    if (-not $Verb) { $Verb = 'terminal' }
    if ($Verb.ToLowerInvariant() -in @('terminal','ai','ai-terminal','agent','agents','mcp','ssh','mon','monitor','mini','dash','btop','repair')) {
        & $global:MiosNativeEntry $Verb @Arguments
    } elseif ($global:MiosLegacyDispatcher) {
        & $global:MiosLegacyDispatcher $Verb @Arguments
    } else {
        & $global:MiosNativeEntry $Verb @Arguments
    }
}
function global:btop {
    # Use the installed binding's unprivileged user, including from Xbox Mode.
    & $global:MiosNativeEntry btop @args
}
function global:tmux {
    # Rust resolves the backend and binding from SSOT, then verifies the socket.
    & (Join-Path (Split-Path $global:MiosNativeEntry) 'mios-launch.exe') --tmux @args
}
