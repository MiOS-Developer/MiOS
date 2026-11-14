# AI-hint: Exercise the real bootstrap failure trap with synthetic early failures; never execute installer or machine-preparation code.
# AI-related: Get-MiOS.ps1, tests/test_getmios_web_entry.ps1
[CmdletBinding()]
param([string]$SourcePath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'Get-MiOS.ps1'))
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText($SourcePath)
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('Invalid fixture source: ' + $errors[0].Message) }
$initializers = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$_trapFmtFailed'
}, $true))
$traps = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.TrapStatementAst] -and
    $node.Extent.Text.Contains('Installation failed; existing distributions, persistent state and source checkouts are preserved.')
}, $true))
$preparations = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Invoke-MiosMachinePreparation'
}, $true))
$vendorReads = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$_freshVendorToml'
}, $true))
if ($initializers.Count -ne 1 -or $traps.Count -ne 1 -or $preparations.Count -ne 1 -or $vendorReads.Count -ne 1) {
    throw 'Missing or duplicate failure-diagnostic source anchors.'
}
$initializer = $initializers[0]; $trap = $traps[0]; $preparation = $preparations[0]
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('mios-failure-diagnostics-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $scratch
$engine = (Get-Process -Id $PID).Path
$script:checks = 0
$script:failures = New-Object 'System.Collections.Generic.List[string]'
function Check([bool]$Condition, [string]$Name) {
    $script:checks++
    if (-not $Condition) { $script:failures.Add($Name); Write-Host ('FAIL ' + $Name) }
}
function Invoke-DiagnosticCase([string]$Mode, [string]$Reason, [switch]$LateFormatter, [string]$TrapText = $trap.Extent.Text) {
    # Only these extracted source statements execute. The throwing statement is a
    # synthetic replacement for machine preparation; every real installer call is absent.
    $throwOffset = if ($Mode -eq 'uninitialized') { -1 } else { $preparation.Extent.StartOffset }
    $initializerOffset = if ($LateFormatter) { $trap.Extent.StartOffset - 1 } else { $initializer.Extent.StartOffset }
    $throwText = "throw '" + $Reason.Replace("'", "''") + "'"
    $statements = @(
        [pscustomobject]@{ Offset = $initializerOffset; Text = $initializer.Extent.Text },
        [pscustomobject]@{ Offset = $throwOffset; Text = $throwText },
        [pscustomobject]@{ Offset = $trap.Extent.StartOffset; Text = $TrapText }
    ) | Sort-Object Offset
    $stub = @'
param([string]$FixtureMode)
$ErrorActionPreference = 'Stop'
function Get-MiosTomlValue {
    param($Section, $Key, $Default)
    if ($Section -ne 'messages.failure_trap' -or $Key -ne 'install_failed_template') {
        throw 'Unexpected diagnostic configuration lookup.'
    }
    switch ($FixtureMode) {
        'localized' { return 'LOCALIZED-FAILURE: {0}' }
        'invalid' { return '{9}' }
        'empty' { return '   ' }
        'constant' { return 'LOCALIZED-WITHOUT-A-REASON' }
        default { return $Default }
    }
}
'@
    $program = $stub + "`n" + (($statements | ForEach-Object { $_.Text }) -join "`n") + "`nWrite-Host 'UNREACHABLE-FIXTURE-CONTINUATION'`n"
    $childTokens = $null; $childErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseInput($program, [ref]$childTokens, [ref]$childErrors)
    if ($childErrors.Count) { throw ('Invalid extracted diagnostic program: ' + $childErrors[0].Message) }
    $path = Join-Path $scratch ($Mode + '-' + [guid]::NewGuid().ToString('N') + '.ps1')
    [IO.File]::WriteAllText($path, $program, (New-Object Text.UTF8Encoding($false)))
    # The expected exit 1 is data, including when a caller enables the PS 7 native-error preference.
    $PSNativeCommandUseErrorActionPreference = $false
    $output = (& $engine -NoLogo -NoProfile -NonInteractive -File $path -FixtureMode $Mode 2>&1 | Out-String)
    $code = $LASTEXITCODE
    return [pscustomobject]@{ ExitCode = $code; Output = $output }
}
try {
    Check ($initializer.Extent.StartOffset -lt $vendorReads[0].Extent.StartOffset -and
        $initializer.Extent.StartOffset -lt $preparation.Extent.StartOffset) 'the SSOT failure format is initialized before phase-zero work'
    $reason = 'Machine preparation stopped: FIXTURE-BOUNDARY-REFUSAL'
    foreach ($mode in @('default', 'localized', 'uninitialized', 'invalid', 'empty', 'constant', 'braces')) {
        $caseReason = if ($mode -eq 'braces') { $reason + ' {literal-reason}' } else { $reason }
        $result = Invoke-DiagnosticCase -Mode $mode -Reason $caseReason
        Check ($result.ExitCode -eq 1) ($mode + ' keeps the failure exit code')
        Check ($result.Output.Contains($caseReason)) ($mode + ' prints the original reason')
        Check ($result.Output.Contains('existing distributions, persistent state and source checkouts are preserved.')) ($mode + ' retains the preservation notice')
        Check (-not $result.Output.Contains('UNREACHABLE-FIXTURE-CONTINUATION')) ($mode + ' never continues after failure')
        if ($mode -eq 'default') { Check ($result.Output.Contains('[!!] Install failed: ' + $caseReason)) 'the declared default format is preserved' }
        if ($mode -eq 'localized') { Check ($result.Output.Contains('LOCALIZED-FAILURE: ' + $caseReason)) 'the configured localization is preserved' }
    }
    # Restore the real former display expression and late initialization together.
    # This plant must reproduce the blank reason from the operator's original failure.
    $display = "Write-Host ('  ' + `$_trapDisplay) -ForegroundColor Red"
    if ([regex]::Matches($trap.Extent.Text, [regex]::Escape($display)).Count -ne 1) { throw 'The diagnostic plant display anchor is not unique.' }
    $plantedTrap = $trap.Extent.Text.Replace($display, "Write-Host ('  ' + (`$_trapFmtFailed -f `$_.Exception.Message)) -ForegroundColor Red")
    $baseline = Invoke-DiagnosticCase -Mode 'default' -Reason $reason -LateFormatter -TrapText $plantedTrap
    $plantCaught = $baseline.ExitCode -eq 1 -and -not $baseline.Output.Contains($reason) -and
        $baseline.Output.Contains('existing distributions, persistent state and source checkouts are preserved.')
    Check $plantCaught 'the former late-initialization and blank-display defect is observed by this fixture'
    Write-Host ('FAILURE_DIAGNOSTIC_ASSERTIONS ' + $script:checks)
    if ($script:failures.Count) { throw ('Diagnostic fixture failures: ' + ($script:failures -join '; ')) }
    Write-Host 'FAILURE_DIAGNOSTIC_PLANTS PASS (1/1)'
    Write-Host 'RESULT: PASS (failure trap source fixtures; no installer, network, UAC, provisioning or machine writes)'
} finally {
    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
}
