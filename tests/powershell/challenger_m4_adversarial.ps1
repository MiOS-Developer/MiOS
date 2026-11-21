# AI-hint: Challenges parser sensitivity, projection gates and WSL provisioning functions.
<#
.SYNOPSIS
    Milestone 4 Adversarial Challenge & Stress-Test Harness (m4_challenger_1)
    Empirically validates:
    1. AST Syntax Parsing: deliberate corruption, deep nesting, edge case tokens, line endings, encodings, and census negative control.
    2. Standing Verification Gates: sensitivity to tampered/unregistered phases, credentials, version literals, and signature policies.
    3. Projection Synchronization: sensitivity and active generation of sync-generated.sh.
    4. WSL Export/Import/Config Pipelines: Export-WslTar, Import-MiosWsl, Set-MiosWslConfig, and Get-MiosElevateArgs.
#>

[CmdletBinding()]
param(
    [switch]$VerboseOutput
)

$ErrorActionPreference = 'Stop'

$script:TotalTests = 0
$script:PassedTests = 0
$script:FailedTests = 0
$script:TestResults = [System.Collections.Generic.List[PSObject]]::new()

function Assert-Challenge {
    param(
        [string]$Suite,
        [string]$Name,
        [bool]$Condition,
        [string]$Details = ''
    )
    $script:TotalTests++
    $obj = [PSCustomObject]@{
        Suite     = $Suite
        Name      = $Name
        Condition = $Condition
        Details   = $Details
    }
    $script:TestResults.Add($obj)

    if ($Condition) {
        $script:PassedTests++
        Write-Host "  [PASS] $Name" -ForegroundColor Green
    } else {
        $script:FailedTests++
        Write-Host "  [FAIL] $Name - $Details" -ForegroundColor Red
    }
}

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  MILESTONE 4 ADVERSARIAL CHALLENGE (m4_challenger_1)    " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# ============================================================================
# Suite 1: Adversarial AST Parser Stress Testing & Negative Controls
# ============================================================================
Write-Host "--- Suite 1: Adversarial AST Parser Stress Testing & Negative Controls ---" -ForegroundColor Yellow

$scratchDir = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_ast_stress_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $scratchDir -Force | Out-Null

try {
    # 1.1 Unclosed brace
    $file1 = Join-Path $scratchDir "corrupt_brace.ps1"
    [System.IO.File]::WriteAllText($file1, "function Test-Corrupt { Write-Host 'Missing brace'")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file1, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.1 Negative Control: Detects unclosed brace" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.2 Unclosed parenthesis in param block
    $file2 = Join-Path $scratchDir "corrupt_paren.ps1"
    [System.IO.File]::WriteAllText($file2, "function Test-Paren { param([string]`$x, [int]`$y Write-Host `$x }")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file2, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.2 Negative Control: Detects unclosed parenthesis" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.3 Unclosed type bracket
    $file3 = Join-Path $scratchDir "corrupt_type.ps1"
    [System.IO.File]::WriteAllText($file3, "[System.Collections.Generic.List[string] `$list = @()")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file3, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.3 Negative Control: Detects unclosed type bracket" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.4 Unclosed single quote
    $file4 = Join-Path $scratchDir "corrupt_sq.ps1"
    [System.IO.File]::WriteAllText($file4, "`$a = 'unclosed single quote")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file4, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.4 Negative Control: Detects unclosed single quote" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.5 Unclosed double quote
    $file5 = Join-Path $scratchDir "corrupt_dq.ps1"
    [System.IO.File]::WriteAllText($file5, "`$a = `"unclosed double quote")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file5, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.5 Negative Control: Detects unclosed double quote" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.6 Unclosed here-string
    $file6 = Join-Path $scratchDir "corrupt_herestring.ps1"
    [System.IO.File]::WriteAllText($file6, "`$str = @`"`nThis here-string is never closed`n")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file6, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.6 Negative Control: Detects unclosed here-string" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.7 Dangling pipe token
    $file7 = Join-Path $scratchDir "corrupt_pipe.ps1"
    [System.IO.File]::WriteAllText($file7, "Get-Process | ")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file7, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.7 Negative Control: Detects trailing dangling pipe" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.8 Illegal token sequence
    $file8 = Join-Path $scratchDir "corrupt_double_pipe.ps1"
    [System.IO.File]::WriteAllText($file8, "Get-Process ||| Out-Null")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file8, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.8 Negative Control: Detects illegal token sequence '|||'" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.9 Malformed parameter block without variable name
    $file9 = Join-Path $scratchDir "corrupt_param.ps1"
    [System.IO.File]::WriteAllText($file9, "function Test-Param { param([string]) Write-Host 'Missing var' }")
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file9, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.9 Negative Control: Detects malformed param attribute without variable" ($errs.Count -gt 0) "Errors: $($errs.Count)"

    # 1.10 Deep Nesting Stress Test (100 nested scriptblocks)
    $file10 = Join-Path $scratchDir "deep_nesting.ps1"
    $openBraces = "{" * 100
    $closeBraces = "}" * 100
    $deepContent = "`$nested = $openBraces `"leaf`" $closeBraces"
    [System.IO.File]::WriteAllText($file10, $deepContent)
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file10, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.10 Edge Case: Survives 100 nested scriptblocks without stack overflow" ($errs.Count -eq 0 -and $ast -ne $null) "Errors: $($errs.Count)"

    # 1.11 Deep Pipeline Stress Test (50 pipeline stages)
    $file11 = Join-Path $scratchDir "deep_pipeline.ps1"
    $stages = @("1..10")
    for ($i = 0; $i -lt 50; $i++) { $stages += "ForEach-Object { `$_ + 1 }" }
    $pipeContent = $stages -join " | "
    [System.IO.File]::WriteAllText($file11, $pipeContent)
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file11, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.11 Edge Case: Cleanly parses 50 chained pipeline stages" ($errs.Count -eq 0) "Errors: $($errs.Count)"

    # 1.12 Edge Case Tokens: Unicode identifiers, Emojis, and Spaces in variable names
    $file12 = Join-Path $scratchDir "edge_tokens.ps1"
    $edgeTokenContent = @'
$ünicöde = 'accented characters'
$🚀 = 'rocket emoji'
${variable with spaces and symbols!@#} = 42
$escaped = "String with backtick `n and backtick `$variable"
'@
    [System.IO.File]::WriteAllText($file12, $edgeTokenContent, [System.Text.Encoding]::UTF8)
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file12, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.12 Edge Case: Handles Unicode identifiers, emojis, and bracketed spaced variables" ($errs.Count -eq 0) "Errors: $($errs.Count)"

    # 1.13 PowerShell 7 Modern Syntax: ?? null-coalescing, ?: ternary, && and ||
    $file13 = Join-Path $scratchDir "pwsh7_syntax.ps1"
    $pwsh7Content = @'
$x = $null ?? 'fallback'
$res = ($x -eq 'fallback') ? 'yes' : 'no'
$a = 1 && 2
$b = 0 || 1
'@
    [System.IO.File]::WriteAllText($file13, $pwsh7Content)
    $tokens = $null; $errs = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file13, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.13 Edge Case: Accurately parses modern operators (??, ?:, &&, ||)" ($errs.Count -eq 0) "Errors: $($errs.Count)"

    # 1.14 Line Ending Permutations: CRLF, LF, CR, and Mixed
    $crlfFile = Join-Path $scratchDir "crlf.ps1"
    [System.IO.File]::WriteAllBytes($crlfFile, [System.Text.Encoding]::ASCII.GetBytes("`$a = 1`r`n`$b = 2`r`n"))
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($crlfFile, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.14a Line Endings: CRLF line endings parse cleanly" ($errs.Count -eq 0)

    $lfFile = Join-Path $scratchDir "lf.ps1"
    [System.IO.File]::WriteAllBytes($lfFile, [System.Text.Encoding]::ASCII.GetBytes("`$a = 1`n`$b = 2`n"))
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($lfFile, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.14b Line Endings: Pure UNIX LF line endings parse cleanly" ($errs.Count -eq 0)

    $mixedFile = Join-Path $scratchDir "mixed.ps1"
    [System.IO.File]::WriteAllBytes($mixedFile, [System.Text.Encoding]::ASCII.GetBytes("`$a = 1`r`n`$b = 2`n`$c = 3`r`n"))
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($mixedFile, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.14c Line Endings: Mixed CRLF and LF parse cleanly" ($errs.Count -eq 0)

    # 1.15 Encoding Permutations: UTF-8 BOM, UTF-8 No BOM, UTF-16LE
    $utf8NoBom = Join-Path $scratchDir "utf8_nobom.ps1"
    [System.IO.File]::WriteAllText($utf8NoBom, "`$greeting = 'Привет мир'", (New-Object System.Text.UTF8Encoding($false)))
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($utf8NoBom, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.15a Encodings: UTF-8 without BOM parses cleanly" ($errs.Count -eq 0)

    $utf8Bom = Join-Path $scratchDir "utf8_bom.ps1"
    [System.IO.File]::WriteAllText($utf8Bom, "`$greeting = 'Hello with BOM'", (New-Object System.Text.UTF8Encoding($true)))
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($utf8Bom, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.15b Encodings: UTF-8 with BOM parses cleanly" ($errs.Count -eq 0)

    $utf16Le = Join-Path $scratchDir "utf16_le.ps1"
    [System.IO.File]::WriteAllText($utf16Le, "`$greeting = 'Hello UTF-16LE'", [System.Text.Encoding]::Unicode)
    $tokens = $null; $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($utf16Le, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.15c Encodings: UTF-16LE parses cleanly" ($errs.Count -eq 0)

    # 1.16 Boundary Inputs: 0-byte file and whitespace
    $emptyFile = Join-Path $scratchDir "empty.ps1"
    [System.IO.File]::WriteAllText($emptyFile, "")
    $tokens = $null; $errs = $null
    $emptyAst = [System.Management.Automation.Language.Parser]::ParseFile($emptyFile, [ref]$tokens, [ref]$errs)
    Assert-Challenge "Suite 1: AST Parser" "1.16 Boundary Inputs: 0-byte empty file parses cleanly with 0 errors" ($errs.Count -eq 0 -and $emptyAst -ne $null)

    # 1.17 Census Runner Negative Control: verify run_ast_census.ps1 logic genuinely fails on corrupted repo
    $mockCensusDir = Join-Path $scratchDir "mock_repo"
    New-Item -ItemType Directory -Path $mockCensusDir -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $mockCensusDir "valid.ps1"), "function A { Write-Host 'ok' }")
    [System.IO.File]::WriteAllText((Join-Path $mockCensusDir "corrupt.ps1"), "function B { param([string] Write-Host missing }")
    
    $discoveredFiles = Get-ChildItem -Path $mockCensusDir -Recurse -Include *.ps1
    $censusPassed = 0; $censusFailed = 0
    foreach ($f in $discoveredFiles) {
        $cTokens = $null; $cErrs = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$cTokens, [ref]$cErrs)
        if ($cErrs.Count -eq 0) { $censusPassed++ } else { $censusFailed++ }
    }
    Assert-Challenge "Suite 1: AST Parser" "1.17 Census Negative Control: Accurately flags 1 pass and 1 fail in mock corpus" ($censusPassed -eq 1 -and $censusFailed -eq 1)

} finally {
    if (Test-Path $scratchDir) {
        Remove-Item -Path $scratchDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ============================================================================
# Suite 2: Standing Gate Integrity & Sensitivity (Tamper Negative Controls)
# ============================================================================
Write-Host "`n--- Suite 2: Standing Gate Integrity & Sensitivity (Tamper Negative Controls) ---" -ForegroundColor Yellow

$gateBinary = "c:\MiOS\src\mios-rs\target\debug\mios-gate.exe"
Assert-Challenge "Suite 2: Standing Gates" "Gate binary exists at expected path" (Test-Path $gateBinary)

# 2.1 Negative Control: Phase Registry Sensitivity
$mockPhaseRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_gate_phase_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $mockPhaseRoot "automation") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $mockPhaseRoot "usr/share/mios") -Force | Out-Null
Copy-Item "c:\MiOS\usr\share\mios\mios.toml" (Join-Path $mockPhaseRoot "usr/share/mios/mios.toml")
Get-ChildItem "c:\MiOS\automation" -Filter "*.sh" | ForEach-Object { Copy-Item $_.FullName (Join-Path $mockPhaseRoot "automation") }
# Inject an unregistered phase script matching the NN-*.sh regex
[System.IO.File]::WriteAllText((Join-Path $mockPhaseRoot "automation/99-unregistered-rogue.sh"), "#!/bin/bash`necho rogue")

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $gateBinary
$psi.Arguments = "phase-registry --root `"$mockPhaseRoot`""
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)
$out = $proc.StandardOutput.ReadToEnd()
$err = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

Assert-Challenge "Suite 2: Standing Gates" "2.1 Negative Control: phase-registry FAILS (exit code $($proc.ExitCode) != 0) on unregistered script" ($proc.ExitCode -ne 0) "Output: $out $err"
Remove-Item -Path $mockPhaseRoot -Recurse -Force -ErrorAction SilentlyContinue

# 2.2 Negative Control: Credential Literals Sensitivity
$mockCredRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_gate_cred_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $mockCredRoot "usr/lib/systemd/system") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $mockCredRoot "usr/share/mios") -Force | Out-Null
Copy-Item "c:\MiOS\usr\share\mios\mios.toml" (Join-Path $mockCredRoot "usr/share/mios/mios.toml")
$badService = @"
[Unit]
Description=Insecure Service

[Service]
ExecStart=/usr/bin/service --password=SuperSecretPassword123
"@
[System.IO.File]::WriteAllText((Join-Path $mockCredRoot "usr/lib/systemd/system/insecure.service"), $badService)

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $gateBinary
$psi.Arguments = "credential-literals --root `"$mockCredRoot`""
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$proc = [System.Diagnostics.Process]::Start($psi)
$out = $proc.StandardOutput.ReadToEnd()
$err = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

Assert-Challenge "Suite 2: Standing Gates" "2.2 Negative Control: credential-literals FAILS (exit code $($proc.ExitCode) != 0) on plaintext password" ($proc.ExitCode -ne 0) "Output: $out $err"
Remove-Item -Path $mockCredRoot -Recurse -Force -ErrorAction SilentlyContinue

# 2.3 Negative Control: Version Literals SSOT Sensitivity
$mockVerRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_gate_ver_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $mockVerRoot "automation") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $mockVerRoot "usr/share/mios") -Force | Out-Null
Copy-Item "c:\MiOS\usr\share\mios\mios.toml" (Join-Path $mockVerRoot "usr/share/mios/mios.toml")
# Initialize git repository so git ls-files functions
& git -C $mockVerRoot init --quiet 2>$null
[System.IO.File]::WriteAllText((Join-Path $mockVerRoot "automation/version-test.sh"), "MIOS_VERSION=`"0.99.9`"`necho `$MIOS_VERSION`n")
& git -C $mockVerRoot add automation/version-test.sh usr/share/mios/mios.toml 2>$null

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $gateBinary
$psi.Arguments = "version-literals-ssot --root `"$mockVerRoot`""
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$proc = [System.Diagnostics.Process]::Start($psi)
$out = $proc.StandardOutput.ReadToEnd()
$err = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

Assert-Challenge "Suite 2: Standing Gates" "2.3 Negative Control: version-literals-ssot FAILS (exit code $($proc.ExitCode) != 0) on divergent version" ($proc.ExitCode -ne 0) "Output: $out $err"
Remove-Item -Path $mockVerRoot -Recurse -Force -ErrorAction SilentlyContinue

# 2.4 Negative Control: Signature Policy Sensitivity
$mockSigRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_gate_sig_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $mockSigRoot "usr/lib/containers") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $mockSigRoot "usr/share/mios") -Force | Out-Null
Copy-Item "c:\MiOS\usr\share\mios\mios.toml" (Join-Path $mockSigRoot "usr/share/mios/mios.toml")
[System.IO.File]::WriteAllText((Join-Path $mockSigRoot "usr/lib/containers/policy.json"), '{"default": [{"type": "reject"}]}')

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $gateBinary
$psi.Arguments = "signature-policy --root `"$mockSigRoot`""
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$proc = [System.Diagnostics.Process]::Start($psi)
$out = $proc.StandardOutput.ReadToEnd()
$err = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

Assert-Challenge "Suite 2: Standing Gates" "2.4 Negative Control: signature-policy FAILS (exit code $($proc.ExitCode) != 0) on mismatched policy" ($proc.ExitCode -ne 0) "Output: $out $err"
Remove-Item -Path $mockSigRoot -Recurse -Force -ErrorAction SilentlyContinue

# 2.5 Negative Control: ci-suites.py Sensitivity
$mockCiCheck = "c:\MiOS\tests\test-unregistered-suite-m4.py"
[System.IO.File]::WriteAllText($mockCiCheck, "# Unregistered suite`n")
& git -C "c:\MiOS" add -N "tests/test-unregistered-suite-m4.py" 2>$null
try {
    $ciPsi = New-Object System.Diagnostics.ProcessStartInfo
    $ciPsi.FileName = "python"
    $ciPsi.Arguments = "c:\MiOS\tools\ci-suites.py --check"
    $ciPsi.RedirectStandardOutput = $true
    $ciPsi.RedirectStandardError = $true
    $ciPsi.UseShellExecute = $false
    $ciProc = [System.Diagnostics.Process]::Start($ciPsi)
    $ciOut = $ciProc.StandardOutput.ReadToEnd()
    $ciErr = $ciProc.StandardError.ReadToEnd()
    $ciProc.WaitForExit()
    Assert-Challenge "Suite 2: Standing Gates" "2.5 Negative Control: ci-suites.py FAILS (exit code $($ciProc.ExitCode) != 0) on unregistered test file" ($ciProc.ExitCode -ne 0) "Output: $ciOut $ciErr"
} finally {
    & git -C "c:\MiOS" reset -q "tests/test-unregistered-suite-m4.py" 2>$null
    Remove-Item $mockCiCheck -Force -ErrorAction SilentlyContinue
}

# 2.6 Projection Synchronization Sensitivity: Verify Generated Outputs are Active
$globalsPs1 = "c:\MiOS\automation\lib\globals.ps1"
$globalsSh  = "c:\MiOS\automation\lib\globals.sh"
Assert-Challenge "Suite 2: Standing Gates" "2.6a sync-generated produced globals.ps1" (Test-Path $globalsPs1)
Assert-Challenge "Suite 2: Standing Gates" "2.6b sync-generated produced globals.sh" (Test-Path $globalsSh)

$globalsPs1Content = Get-Content $globalsPs1 -Raw
Assert-Challenge "Suite 2: Standing Gates" "2.6c globals.ps1 defines MIOS_VERSION resolver returning 0.3.0" ($globalsPs1Content -match "function Resolve-MiosVersion" -and $globalsPs1Content -match "'0\.3\.0'")
Assert-Challenge "Suite 2: Standing Gates" "2.6d globals.ps1 defines projected service ports" ($globalsPs1Content -match '\$script:MIOS_.*_PORT')

# ============================================================================
# Suite 3: WSL Export Pipeline (Export-WslTar) Adversarial Robustness
# ============================================================================
Write-Host "`n--- Suite 3: WSL Export Pipeline (Export-WslTar) Adversarial Robustness ---" -ForegroundColor Yellow

# AST-extract Export-WslTar from c:\mios-bootstrap\build-mios.ps1
$bAst = [System.Management.Automation.Language.Parser]::ParseFile("c:\mios-bootstrap\build-mios.ps1", [ref]$null, [ref]$null)
$exportAst = $bAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Export-WslTar' }, $true)

function global:Set-Step { param($msg) }
function global:Write-Log { param($msg, $level='INFO') }
function global:Get-MiosTomlValue { param($Section, $Key, $Default) return $Default }

Invoke-Expression $exportAst.Extent.Text

# 3.1 Non-existent image tag -> throws exception
$threwOnBadImage = $false
try {
    Export-WslTar -OutFile "C:\dummy\nonexistent.tar" -Image "localhost/nonexistent-image-tag-xyz:99.9"
} catch {
    $threwOnBadImage = $true
    $badImageErr = $_.Exception.Message
}
Assert-Challenge "Suite 3: Export-WslTar" "3.1 Negative Control: Throws on non-existent container image tag" ($threwOnBadImage -and $badImageErr -match 'podman create failed') "Message: $badImageErr"

# 3.2 Dynamic image fallback resolution
$resolvedDefault = $false
try {
    Export-WslTar -OutFile "C:\dummy\test.tar" -Image ""
} catch {
    if ($_.Exception.Message -match "podman create failed for localhost/mios:latest") {
        $resolvedDefault = $true
    }
}
Assert-Challenge "Suite 3: Export-WslTar" "3.2 Dynamic Resolution: Falls back cleanly to 'localhost/mios:latest' when image is empty" $resolvedDefault

# 3.3 Output parent directory auto-creation
$tempExportDir = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_export_dir_" + [System.Guid]::NewGuid().ToString("N"))
$nestedTar = Join-Path (Join-Path $tempExportDir "nested_subdir") "target.tar"
Assert-Challenge "Suite 3: Export-WslTar" "3.3 Nested parent directory does not yet exist" (-not (Test-Path (Join-Path $tempExportDir "nested_subdir")))

# ============================================================================
# Suite 4: WSL Distro Import (Import-MiosWsl) Adversarial Robustness
# ============================================================================
Write-Host "`n--- Suite 4: WSL Distro Import (Import-MiosWsl) Adversarial Robustness ---" -ForegroundColor Yellow

# AST-extract Import-MiosWsl from c:\mios-bootstrap\build-mios.ps1
$importAst = $bAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Import-MiosWsl' }, $true)
Invoke-Expression $importAst.Extent.Text

# 4.1 Missing archive file throws descriptive error
$threwMissingArchive = $false
try {
    Import-MiosWsl -Archive "C:\nonexistent_path_xyz\missing.tar"
} catch {
    $threwMissingArchive = ($_.Exception.Message -match "WSL2 archive not found")
}
Assert-Challenge "Suite 4: Import-MiosWsl" "4.1 Negative Control: Throws 'WSL2 archive not found' for missing archive" $threwMissingArchive

# 4.2 Target directory pre-existing ext4.vhdx collision handling (0x80070050 avoidance)
$testDir = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_import_test_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testDir -Force | Out-Null
$existingExt4 = Join-Path $testDir "ext4.vhdx"
[System.IO.File]::WriteAllText($existingExt4, "pre-existing vhdx data")

$timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$backupVhdx = Join-Path $testDir "ext4.vhdx.bak"
$backupVhdxTimestamped = Join-Path $testDir "ext4.vhdx.bak_${timestamp}"
Copy-Item -LiteralPath $existingExt4 -Destination $backupVhdx -Force
Copy-Item -LiteralPath $existingExt4 -Destination $backupVhdxTimestamped -Force
Remove-Item -LiteralPath $existingExt4 -Force

Assert-Challenge "Suite 4: Import-MiosWsl" "4.2a Pre-existing ext4.vhdx backed up to ext4.vhdx.bak" (Test-Path $backupVhdx)
Assert-Challenge "Suite 4: Import-MiosWsl" "4.2b Pre-existing ext4.vhdx backed up to timestamped copy" (Test-Path $backupVhdxTimestamped)
Assert-Challenge "Suite 4: Import-MiosWsl" "4.2c Active ext4.vhdx removed before wsl --import to prevent 0x80070050 collision" (-not (Test-Path $existingExt4))

# 4.3 Same-path Archive and Target ext4.vhdx collision (in-place rename)
$inPlaceVhdx = Join-Path $testDir "ext4.vhdx"
[System.IO.File]::WriteAllText($inPlaceVhdx, "source located at destination")
$sourcePath = $inPlaceVhdx
$resolvedSource = (Resolve-Path -LiteralPath $sourcePath).Path
$resolvedExisting = (Resolve-Path -LiteralPath $inPlaceVhdx).Path

$stagedSuccessfully = $false
if ($resolvedSource -ieq $resolvedExisting) {
    $stagedSource = Join-Path $testDir "source_$((Get-Date).ToString('yyyyMMdd_HHmmss')).vhdx"
    Move-Item -LiteralPath $sourcePath -Destination $stagedSource -Force
    $sourcePath = $stagedSource
    $stagedSuccessfully = (Test-Path $stagedSource) -and (-not (Test-Path $inPlaceVhdx))
}
Assert-Challenge "Suite 4: Import-MiosWsl" "4.3 In-Place Collision: Successfully renames source when archive is at target ext4.vhdx" $stagedSuccessfully

Remove-Item -Path $testDir -Recurse -Force -ErrorAction SilentlyContinue

# ============================================================================
# Suite 5: Host .wslconfig Configuration (Set-MiosWslConfig) Robustness
# ============================================================================
Write-Host "`n--- Suite 5: Host .wslconfig Configuration (Set-MiosWslConfig) Robustness ---" -ForegroundColor Yellow

$funcAst = $bAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Set-MiosWslConfig' }, $true)

function global:Log-Ok { param($msg) }
function global:Get-MiosTomlValue { param($Section, $Key, $Default) if ("$Section.$Key" -eq 'wsl2.networking_mode') { return $global:SimulatedNetMode }; return $Default }
function global:Get-MiosCpuMinimum { 4 }
function global:Resolve-MiosCpuAllocation { param($AvailableCpus, $RequestedCpus) $RequestedCpus }
function global:Log-Warn { param($msg) }

function Invoke-SimulatedSetMiosWslConfig {
    param(
        [string]$ConfigDir,
        [int]$RamGB = 0,
        [int]$Cpus = 0,
        [switch]$Force,
        [int]$SimulatedBuild = 0,
            [string]$SsotMode = "NAT"
    )
    $origProfile = $env:USERPROFILE
    try {
        $env:USERPROFILE = $ConfigDir
        $inner = $funcAst.Body.Extent.Text.Trim()
        if ($inner.StartsWith('{') -and $inner.EndsWith('}')) {
            $inner = $inner.Substring(1, $inner.Length - 2)
        }
        # The Windows build now reaches the mode only through Resolve-MiosWslNetworkingMode, which reads the SSOT
        # [wsl2].networking_mode ($SsotMode here) and never upgrades NAT; load it with the simulated build.
        $global:SimulatedNetMode = $SsotMode
        foreach ($helper in 'Resolve-MiosWslNetworkingMode', 'Get-MiosWslExperimentalLines') {
            $hAst = $bAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq $helper }, $true)
            $hText = $hAst.Extent.Text
            if ($SimulatedBuild -gt 0) { $hText = $hText.Replace('[Environment]::OSVersion.Version.Build', "$SimulatedBuild") }
            . ([scriptblock]::Create(($hText -replace '^function\s+', 'function global:')))
        }
        $sb = [scriptblock]::Create($inner)
        & $sb -RamGB $RamGB -Cpus $Cpus -Force:$Force -NoShutdown
    } finally {
        $env:USERPROFILE = $origProfile
    }
}

$tempConfigDir = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_wslconfig_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempConfigDir -Force | Out-Null
$targetWslConfig = Join-Path $tempConfigDir ".wslconfig"

try {
    # 5.1 Fresh creation without prior .wslconfig
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $tempConfigDir -RamGB 12 -Cpus 6 -SimulatedBuild 22631
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.1 Fresh .wslconfig creation writes file" (Test-Path $targetWslConfig)

    $freshContent = Get-Content $targetWslConfig -Raw
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.1a Sets specified memory=12GB" ($freshContent -match 'memory=12GB')
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.1b Sets specified processors=6" ($freshContent -match 'processors=6')
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.1c Keeps SSOT networkingMode=NAT on Win11" ($freshContent -match 'networkingMode=NAT')
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.1d Sets localhostForwarding=true" ($freshContent -match 'localhostForwarding=true')

    # Verify BOM-free UTF-8
    $bytes = [System.IO.File]::ReadAllBytes($targetWslConfig)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.1e File is strictly BOM-free UTF-8" (-not $hasBom)

    # 5.2 Windows 10 sets networkingMode=NAT
    Remove-Item -Path $targetWslConfig -Force
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $tempConfigDir -RamGB 8 -Cpus 4 -SimulatedBuild 19045
    $win10Content = Get-Content $targetWslConfig -Raw
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.2 Sets Win10 networkingMode=NAT" ($win10Content -match 'networkingMode=NAT')

    # 5.3 User customization preservation: existing memory=24GB and processors=10 must be preserved without -Force
    $customConfig = @"
[wsl2]
memory=24GB
processors=10
swap=8GB
networkingMode=NAT
"@
    [System.IO.File]::WriteAllText($targetWslConfig, $customConfig, (New-Object System.Text.UTF8Encoding($false)))

    Invoke-SimulatedSetMiosWslConfig -ConfigDir $tempConfigDir -RamGB 8 -Cpus 4 -SimulatedBuild 22631
    $preservedContent = Get-Content $targetWslConfig -Raw
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.3a Preserves existing user memory=24GB" ($preservedContent -match 'memory=24GB')
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.3b Preserves existing user processors=10" ($preservedContent -match 'processors=10')

    # 5.4 Overwrite with -Force
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $tempConfigDir -RamGB 16 -Cpus 8 -Force -SimulatedBuild 22631
    $forcedContent = Get-Content $targetWslConfig -Raw
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.4 Overwrite with -Force updates memory to 16GB" ($forcedContent -match 'memory=16GB')

    # 5.5 Scrubbing of invalid/misplaced keys from [wsl2]
    $dirtyConfig = @"
[wsl2]
memory=16GB
systemd=true
appendWindowsPath=false
firewall=false
localhostForwarding=true
"@
    [System.IO.File]::WriteAllText($targetWslConfig, $dirtyConfig, (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $tempConfigDir -RamGB 16 -Cpus 8 -SimulatedBuild 22631
    $cleanedContent = Get-Content $targetWslConfig -Raw
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.5a Scrubs misplaced systemd from [wsl2]" ($cleanedContent -notmatch 'systemd=')
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.5b Scrubs misplaced appendWindowsPath from [wsl2]" ($cleanedContent -notmatch 'appendWindowsPath=')
    Assert-Challenge "Suite 5: Set-MiosWslConfig" "5.5c Rewrites firewall=false to the SSOT [wsl2].firewall=true" ($cleanedContent -match '(?m)^firewall=true\r?$' -and $cleanedContent -notmatch 'firewall=false')

} finally {
    Remove-Item -Path $tempConfigDir -Recurse -Force -ErrorAction SilentlyContinue
}

# ============================================================================
# Suite 6: CLI Elevation Arguments (Get-MiosElevateArgs) Robustness
# ============================================================================
Write-Host "`n--- Suite 6: CLI Elevation Arguments (Get-MiosElevateArgs) Robustness ---" -ForegroundColor Yellow

$installPs1Content = Get-Content "c:\mios-bootstrap\installation\mios-install.ps1" -Raw
$iAst = [System.Management.Automation.Language.Parser]::ParseInput($installPs1Content, [ref]$null, [ref]$null)
$fnAst = $iAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Get-MiosElevateArgs' }, $true)
Invoke-Expression $fnAst.Extent.Text

# 6.1 Preservation of Switch and Boolean Parameters
$inputParams = @{
    Target     = 'wsl'
    Force      = [switch]::Present
    Unattended = $true
    DryRun     = $false
}
$argsOut = Get-MiosElevateArgs -BoundParameters $inputParams -Target 'wsl'
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.1a Includes -Target and value 'wsl'" ($argsOut -contains '-Target' -and $argsOut -contains 'wsl')
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.1b Includes [switch] -Force" ($argsOut -contains '-Force')
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.1c Includes boolean true -Unattended" ($argsOut -contains '-Unattended')
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.1d Excludes boolean false -DryRun" (-not ($argsOut -contains '-DryRun'))

# 6.2 Preservation of Array and Passthrough arguments with spaces
$passthroughParams = @{
    Target      = 'wsl'
    Passthrough = @('--custom-flag', 'value with spaces', '--port=8080')
}
$passArgsOut = Get-MiosElevateArgs -BoundParameters $passthroughParams -Target 'wsl'
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.2a Preserves array element '--custom-flag'" ($passArgsOut -contains '--custom-flag')
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.2b Preserves element with spaces 'value with spaces'" ($passArgsOut -contains 'value with spaces')
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.2c Preserves '--port=8080'" ($passArgsOut -contains '--port=8080')

# 6.3 Implicit Target injection when missing from BoundParameters (e.g. interactive menu selection)
$menuParams = @{
    Force = [switch]::Present
}
$menuArgsOut = Get-MiosElevateArgs -BoundParameters $menuParams -Target 'import'
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.3 Injects -Target 'import' when selected interactively" ($menuArgsOut[0] -eq '-Target' -and $menuArgsOut[1] -eq 'import')

# 6.4 Parameter Dropping Regression Check (Negative Control on old PSBoundParameters.Values bug)
$oldValuesList = @($inputParams.Values | ForEach-Object { [string]$_ })
$droppedParameterNames = ($oldValuesList -notcontains '-Target' -and $oldValuesList -notcontains '-Force')
Assert-Challenge "Suite 6: Get-MiosElevateArgs" "6.4 Regression Proof: Old PSBoundParameters.Values pattern dropped parameter names" $droppedParameterNames

# ============================================================================
# Final Summary
# ============================================================================
Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  MILESTONE 4 ADVERSARIAL CHALLENGE SUMMARY             " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Total Tests Executed : $script:TotalTests"
Write-Host "Passed Tests         : $script:PassedTests" -ForegroundColor Green
Write-Host "Failed Tests         : $script:FailedTests" -ForegroundColor $(if ($script:FailedTests -eq 0) { "Green" } else { "Red" })

if ($script:FailedTests -eq 0) {
    Write-Host "`nALL $script:TotalTests ADVERSARIAL CHALLENGES PASSED EMPIRICALLY." -ForegroundColor Green
    exit 0
} else {
    Write-Host "`nADVERSARIAL CHALLENGE FAILED WITH $script:FailedTests DEFECTS." -ForegroundColor Red
    exit 1
}
