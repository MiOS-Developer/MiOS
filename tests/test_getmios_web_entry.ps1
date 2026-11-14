# AI-hint: Parse the exact fetched PowerShell bytes without executing installer, resume, elevation, or provisioning code.
# AI-related: Get-MiOS.ps1, bootstrap.ps1, install.ps1, build-mios.ps1
[CmdletBinding()]
param([Parameter(Mandatory)][string]$BootstrapRoot, [string]$EntryPath)
$ErrorActionPreference = 'Stop'
if (-not $EntryPath) { $EntryPath = Join-Path $BootstrapRoot 'Get-MiOS.ps1' }
$script:checks = 0
function Check([bool]$Condition, [string]$Name) {
    $script:checks++
    if (-not $Condition) { throw ('ASSERT: ' + $Name) }
    Write-Host ('  PASS ' + $Name)
}
function Parse-Text([string]$Text) {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    return [pscustomobject]@{ Ast = $ast; Errors = @($errors) }
}
function Definition($Ast, [string]$Name) {
    $found = @($Ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $Name
    }, $true))
    if ($found.Count -ne 1) { throw ('Missing or duplicate source function: ' + $Name) }
    return $found[0]
}
$targets = @(
    @{ Name = 'Get-MiOS.ps1'; Path = $EntryPath; ParameterBlock = $true },
    @{ Name = 'bootstrap.ps1'; Path = (Join-Path $BootstrapRoot 'bootstrap.ps1'); ParameterBlock = $true },
    @{ Name = 'install.ps1'; Path = (Join-Path $BootstrapRoot 'install.ps1'); ParameterBlock = $true },
    @{ Name = 'field/MiOS-Field.ps1'; Path = (Join-Path $BootstrapRoot 'field/MiOS-Field.ps1'); ParameterBlock = $false },
    @{ Name = 'build-mios.ps1'; Path = (Join-Path $BootstrapRoot 'build-mios.ps1'); ParameterBlock = $true },
    @{ Name = 'field/autounattend/Invoke-MiOSProvision.ps1'; Path = (Join-Path $BootstrapRoot 'field/autounattend/Invoke-MiOSProvision.ps1'); ParameterBlock = $true }
)
$sources = @{}
$entryAst = $null
foreach ($target in $targets) {
    $raw = [IO.File]::ReadAllBytes($target.Path)
    # UTF8.GetString deliberately preserves U+FEFF, unlike ParseFile/ReadAllText.
    # This models the string delivered by the web entry, with no TrimStart workaround.
    $text = [Text.Encoding]::UTF8.GetString($raw)
    Check (@($raw | Where-Object { $_ -gt 127 }).Count -eq 0) ($target.Name + ' is ASCII with no BOM')
    $parsed = Parse-Text $text
    Check ($parsed.Errors.Count -eq 0 -and (($null -ne $parsed.Ast.ParamBlock) -eq $target.ParameterBlock)) ($target.Name + ' raw web string parses with its declared parameter block')
    $compiled = [scriptblock]::Create($text)
    Check ((($null -ne $compiled.Ast.ParamBlock) -eq $target.ParameterBlock)) ($target.Name + ' ScriptBlock.Create preserves its parameter block without executing it')
    $sources[$target.Name] = $text
    if ($target.Name -eq 'Get-MiOS.ps1') { $entryAst = $parsed.Ast }
}
$entryText = $sources['Get-MiOS.ps1']
$mirror = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes((Join-Path $BootstrapRoot 'Get-MiOS.ps1')))
Check ($entryText.Replace("`r`n", "`n") -ceq $mirror.Replace("`r`n", "`n")) 'the system and Bootstrap entry sources agree apart from checkout line endings'

# Bind each tested payload to the actual fetch consumer; inspect source only.
Check ($entryText.Contains('Get-MiOS.ps1?cb=$cb') -and $entryText.Contains('[scriptblock]::Create($freshSrc)')) 'cache-bust relaunch parses the same canonical entry payload'
$resumeDefinition = Definition $entryAst 'Register-MiosBootstrapResume'
$resume = $resumeDefinition.Extent.Text
Check ($resume.Contains('irm ''$_resumeUrl'' | iex')) 'post-reboot resume fetches the validated canonical entry payload'
$urlGuards = @($resumeDefinition.FindAll({ param($node)
    $node -is [Management.Automation.Language.BinaryExpressionAst] -and $node.Operator.ToString() -eq 'Inotmatch' -and
    $node.Left.Extent.Text -eq '$_resumeUrl' -and $node.Right -is [Management.Automation.Language.StringConstantExpressionAst]
}, $true))
if ($urlGuards.Count -ne 1) { throw 'Missing or duplicate literal resume URL guard.' }
$resumePattern = $urlGuards[0].Right.Value
foreach ($validUrl in @(
    'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1',
    'https://raw.githubusercontent.com/MiOS-Developer/mios-bootstrap/feature/bootstrap/Get-MiOS.ps1'
)) {
    Check ($validUrl -match $resumePattern) 'the actual resume URL guard accepts a canonical HTTPS source'
}
foreach ($invalidUrl in @(
    'http://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1',
    'https://rawXgithubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1',
    'https://raw.githubusercontentXcom/mios-dev/mios-bootstrap/main/Get-MiOS.ps1',
    'https://raw.githubusercontent.com.evil.invalid/mios-dev/mios-bootstrap/main/Get-MiOS.ps1',
    'https://user@raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1',
    'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOSXps1',
    'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1?command=other',
    'https://raw.githubusercontent.com/mios-dev/mios-bootstrap/main/Get-MiOS.ps1;Write-Host injected'
)) {
    Check ($invalidUrl -notmatch $resumePattern) 'the actual resume URL guard refuses a malformed or noncanonical source'
}
Check ($sources['bootstrap.ps1'].Contains('/Get-MiOS.ps1') -and $sources['bootstrap.ps1'].Contains('[scriptblock]::Create($src)')) 'Bootstrap web forwarding parses the canonical entry payload'
Check ($sources['install.ps1'].Contains('/field/MiOS-Field.ps1') -and $sources['install.ps1'].Contains('Invoke-RestMethod $url | Invoke-Expression')) 'legacy install forwarding consumes the tested Field launcher'
Check ($sources['field/autounattend/Invoke-MiOSProvision.ps1'].Contains('/Get-MiOS.ps1') -and $sources['field/autounattend/Invoke-MiOSProvision.ps1'].Contains('[scriptblock]::Create($src)')) 'provisioning consumes the tested canonical entry payload'
Check ($entryText.Contains('/build-mios.ps1?cb=') -and $entryText.Contains('[scriptblock]::Create(`$src)')) 'the generated build command consumes the tested build payload'

# This plant reproduces the published failure even though the on-disk parser accepts BOMs.
$bom = [Text.Encoding]::UTF8.GetPreamble()
[byte[]]$plantedBytes = $bom + [Text.Encoding]::UTF8.GetBytes($entryText)
$plantedText = [Text.Encoding]::UTF8.GetString($plantedBytes)
$planted = Parse-Text $plantedText
Check ($planted.Errors.Count -gt 0 -and $null -eq $planted.Ast.ParamBlock) 'a prepended BOM is caught before the web entry can lose its parameter binding'
$createRefused = $false
try { $null = [scriptblock]::Create($plantedText) } catch [Management.Automation.ParseException] { $createRefused = $true }
Check $createRefused 'ScriptBlock.Create also refuses the BOM-planted web entry'

# Invoke only the pure source-to-source worker extractor. Never invoke its returned program.
$workerDefinition = (Definition $entryAst 'Get-MiosMachineWorkerScript').Extent.Text
$worker = & {
    param($Definition, $Source)
    . ([scriptblock]::Create($Definition))
    Get-MiosMachineWorkerScript -Source $Source
} $workerDefinition $entryText
$preparation = Definition $entryAst 'Invoke-MiosMachinePreparation'
$launchAssignments = @($preparation.FindAll({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$launch'
}, $true))
if ($launchAssignments.Count -ne 1) { throw 'Missing or duplicate UAC launch source.' }
# Expand the real launcher string using placeholders, then evaluate only its UTF-8 decode assignment.
$quotedPath = 'C:\fixture\worker.ps1'; $digest = 'fixture-digest'; $pipeName = 'fixture-pipe'
. ([scriptblock]::Create($launchAssignments[0].Extent.Text))
$launchAst = (Parse-Text $launch).Ast
$decodeAssignments = @($launchAst.FindAll({ param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$source'
}, $true))
if ($decodeAssignments.Count -ne 1) { throw 'Missing or duplicate UAC byte decoder.' }
[byte[]]$bytes = $bom + [Text.Encoding]::UTF8.GetBytes($worker)
. ([scriptblock]::Create($decodeAssignments[0].Extent.Text))
Check ($source -ceq $worker) 'the real UAC decoder strips the file BOM while preserving the hashed worker text'
$workerAst = Parse-Text $source
Check ($workerAst.Errors.Count -eq 0 -and $null -ne $workerAst.Ast.ParamBlock) 'the generated machine worker parses after its actual file-byte decoder'
Check ($null -ne ([scriptblock]::Create($source)).Ast.ParamBlock) 'the generated machine worker compiles without invoking machine preparation'

Write-Host ('WEB_ENTRY_ASSERTIONS ' + $script:checks)
Write-Host 'WEB_ENTRY_BOM_PLANTS PASS (1/1)'
Write-Host 'RESULT: PASS (raw web bytes and source-only consumers; no installer, network, elevation, resume, or provisioning execution)'
