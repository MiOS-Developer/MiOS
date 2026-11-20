# AI-hint: Challenges WSL configuration, elevation arguments and installer target selection.
<#
.SYNOPSIS
    Milestone 3 Adversarial Challenge & Stress-Test Harness (m3_challenger_2)
    Validates host .wslconfig reconciliation, CLI self-elevation, target catalog,
    and array splatting across c:\MiOS and c:\mios-bootstrap.
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
Write-Host "  MILESTONE 3 ADVERSARIAL CHALLENGE (m3_challenger_2)    " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# ============================================================================
# Suite 1: AST Syntax Census
# ============================================================================
Write-Host "--- Suite 1: AST Syntax Census Across All Touched Files ---" -ForegroundColor Yellow

$filesToParse = @(
    "c:\mios-bootstrap\build-mios.ps1",
    "c:\MiOS\build-mios.ps1",
    "c:\mios-bootstrap\installation\mios-install.ps1",
    "c:\mios-bootstrap\field\MiOS-Cat.ps1",
    "c:\mios-bootstrap\field\lib\MiOS-Cat.psm1"
)

foreach ($filePath in $filesToParse) {
    $exists = Test-Path -LiteralPath $filePath
    Assert-Challenge -Suite "Suite 1" -Name "File Exists: $(Split-Path $filePath -Leaf)" -Condition $exists -Details "File not found at $filePath"
    if ($exists) {
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($filePath, [ref]$tokens, [ref]$errors)
        $errCount = if ($errors) { $errors.Count } else { 0 }
        $details = if ($errCount -gt 0) { ($errors | ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }) -join "; " } else { "" }
        Assert-Challenge -Suite "Suite 1" -Name "AST Parse Clean (0 errors): $(Split-Path $filePath -Leaf)" -Condition ($errCount -eq 0) -Details $details
    }
}

# ============================================================================
# Suite 2: Adversarial Stress Testing of Set-MiosWslConfig
# ============================================================================
Write-Host "`n--- Suite 2: Adversarial Stress Testing of Set-MiosWslConfig ---" -ForegroundColor Yellow

$tempBase = Join-Path ([System.IO.Path]::GetTempPath()) "m3_challenger2_$([System.Guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $tempBase -Force

try {
    # Extract Set-MiosWslConfig AST from c:\mios-bootstrap\build-mios.ps1
    $tokens = $null; $errors = $null
    $bAst = [System.Management.Automation.Language.Parser]::ParseFile("c:\mios-bootstrap\build-mios.ps1", [ref]$tokens, [ref]$errors)
    $funcAst = $bAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Set-MiosWslConfig' }, $true)
    
    # Mock Log-Ok
    function global:Log-Ok { param($msg) }
    function global:Get-MiosTomlValue { param($Section, $Key, $Default) if ("$Section.$Key" -eq 'wsl2.networking_mode') { return $global:SimulatedNetMode }; return $Default }
    function global:Get-MiosCpuMinimum { 4 }
    function global:Resolve-MiosCpuAllocation { param($AvailableCpus, $RequestedCpus) $RequestedCpus }

    # Helper function to invoke Set-MiosWslConfig under a simulated Windows build
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

    # Helper function to check BOM
    function Test-IsBomFreeUtf8 {
        param([string]$FilePath)
        if (-not (Test-Path -LiteralPath $FilePath)) { return $false }
        $bytes = [System.IO.File]::ReadAllBytes($FilePath)
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            return $false
        }
        # Check UTF-16 LE / BE BOM
        if ($bytes.Length -ge 2 -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) {
            return $false
        }
        return $true
    }

    # Test 2.1: Non-existent .wslconfig
    $dir21 = Join-Path $tempBase "test_21"
    $null = New-Item -ItemType Directory -Path $dir21 -Force
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir21 -RamGB 16 -Cpus 8
    $cfg21 = Join-Path $dir21 ".wslconfig"
    $exists21 = Test-Path $cfg21
    $content21 = if ($exists21) { Get-Content $cfg21 -Raw } else { "" }
    $bomFree21 = Test-IsBomFreeUtf8 $cfg21
    Assert-Challenge -Suite "Suite 2" -Name "2.1 Non-existent .wslconfig created successfully" -Condition ($exists21 -and $content21 -match '\[wsl2\]')
    Assert-Challenge -Suite "Suite 2" -Name "2.1 Memory 16GB written to new .wslconfig" -Condition ($content21 -match '(?m)^\s*memory\s*=\s*16GB\s*$')
    Assert-Challenge -Suite "Suite 2" -Name "2.1 Processors 8 written to new .wslconfig" -Condition ($content21 -match '(?m)^\s*processors\s*=\s*8\s*$')
    Assert-Challenge -Suite "Suite 2" -Name "2.1 Output is strictly BOM-free UTF-8" -Condition $bomFree21

    # Test 2.2: Existing user hardware limits preserved without -Force
    # Test 2.2a: Standard formatting
    $dir22a = Join-Path $tempBase "test_22a"
    $null = New-Item -ItemType Directory -Path $dir22a -Force
    $cfg22a = Join-Path $dir22a ".wslconfig"
    [System.IO.File]::WriteAllText($cfg22a, "[wsl2]`nmemory=32GB`nprocessors=16`nswap=8GB`n", (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir22a -RamGB 8 -Cpus 4
    $content22a = Get-Content $cfg22a -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.2a Preserves existing memory=32GB (called with RamGB=8 without -Force)" -Condition ($content22a -match '(?m)^\s*memory\s*=\s*32GB\s*$')
    Assert-Challenge -Suite "Suite 2" -Name "2.2a Preserves existing processors=16 (called with Cpus=4 without -Force)" -Condition ($content22a -match '(?m)^\s*processors\s*=\s*16\s*$')

    # Test 2.2b: Whitespace variations: 'memory = 48GB', 'processors = 24'
    $dir22b = Join-Path $tempBase "test_22b"
    $null = New-Item -ItemType Directory -Path $dir22b -Force
    $cfg22b = Join-Path $dir22b ".wslconfig"
    [System.IO.File]::WriteAllText($cfg22b, "[wsl2]`nmemory = 48GB`nprocessors = 24`n", (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir22b -RamGB 8 -Cpus 4
    $content22b = Get-Content $cfg22b -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.2b Preserves user limits with spaces around equals (memory=48GB)" -Condition ($content22b -match '(?m)^\s*memory\s*=\s*48GB\s*$')
    Assert-Challenge -Suite "Suite 2" -Name "2.2b Preserves user limits with spaces around equals (processors=24)" -Condition ($content22b -match '(?m)^\s*processors\s*=\s*24\s*$')

    # Test 2.2c: Units and case variation: 'MEMORY=64gb', 'Processors=12'
    $dir22c = Join-Path $tempBase "test_22c"
    $null = New-Item -ItemType Directory -Path $dir22c -Force
    $cfg22c = Join-Path $dir22c ".wslconfig"
    [System.IO.File]::WriteAllText($cfg22c, "[wsl2]`nMEMORY=64gb`nProcessors=12`n", (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir22c -RamGB 8 -Cpus 4
    $content22c = Get-Content $cfg22c -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.2c Preserves mixed-case MEMORY=64gb" -Condition ($content22c -match '(?mi)^\s*memory\s*=\s*64gb\s*$')
    Assert-Challenge -Suite "Suite 2" -Name "2.2c Preserves mixed-case Processors=12" -Condition ($content22c -match '(?mi)^\s*processors\s*=\s*12\s*$')

    # Test 2.3: Overriding user hardware limits with -Force
    $dir23 = Join-Path $tempBase "test_23"
    $null = New-Item -ItemType Directory -Path $dir23 -Force
    $cfg23 = Join-Path $dir23 ".wslconfig"
    [System.IO.File]::WriteAllText($cfg23, "[wsl2]`nmemory=32GB`nprocessors=16`n", (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir23 -RamGB 8 -Cpus 4 -Force
    $content23 = Get-Content $cfg23 -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.3 -Force overrides memory=32GB with requested memory=8GB" -Condition ($content23 -match '(?m)^\s*memory\s*=\s*8GB\s*$' -and $content23 -notmatch '(?m)^\s*memory\s*=\s*32GB\s*$')
    Assert-Challenge -Suite "Suite 2" -Name "2.3 -Force overrides processors=16 with requested processors=4" -Condition ($content23 -match '(?m)^\s*processors\s*=\s*4\s*$' -and $content23 -notmatch '(?m)^\s*processors\s*=\s*16\s*$')

    # Test 2.4: Windows Build Detection Simulation
    # Test 2.4a: Win11 22H2 (Build 22621) keeps SSOT NAT as NAT (never an upgrade)
    $dir24a = Join-Path $tempBase "test_24a"
    $null = New-Item -ItemType Directory -Path $dir24a -Force
    $cfg24a = Join-Path $dir24a ".wslconfig"
    [System.IO.File]::WriteAllText($cfg24a, "[wsl2]`nnetworkingMode=NAT`n", (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir24a -RamGB 8 -Cpus 4 -SimulatedBuild 22621
    $content24a = Get-Content $cfg24a -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.4a Build 22621 (Win11 22H2) keeps SSOT networkingMode=NAT" -Condition ($content24a -match '(?m)^\s*networkingMode\s*=\s*NAT\s*$')

    # Test 2.4b: Win11 24H2 (Build 26100) writes the SSOT mode: NAT by default, mirrored when SSOT says mirrored
    $dir24b = Join-Path $tempBase "test_24b"
    $null = New-Item -ItemType Directory -Path $dir24b -Force
    $cfg24b = Join-Path $dir24b ".wslconfig"
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir24b -RamGB 8 -Cpus 4 -SimulatedBuild 26100
    $content24b = Get-Content $cfg24b -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.4b Build 26100 (Win11 24H2) writes SSOT networkingMode=NAT" -Condition ($content24b -match '(?m)^\s*networkingMode\s*=\s*NAT\s*$')
    Remove-Item -LiteralPath $cfg24b -Force
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir24b -RamGB 8 -Cpus 4 -SimulatedBuild 26100 -SsotMode mirrored
    $content24b2 = Get-Content $cfg24b -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.4b2 Build 26100 with SSOT mirrored writes networkingMode=mirrored" -Condition ($content24b2 -match '(?m)^\s*networkingMode\s*=\s*mirrored\s*$')

    # Test 2.4c: Win10 22H2 (Build 19045) -> NAT
    $dir24c = Join-Path $tempBase "test_24c"
    $null = New-Item -ItemType Directory -Path $dir24c -Force
    $cfg24c = Join-Path $dir24c ".wslconfig"
    [System.IO.File]::WriteAllText($cfg24c, "[wsl2]`nnetworkingMode=mirrored`n", (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir24c -RamGB 8 -Cpus 4 -SimulatedBuild 19045 -SsotMode mirrored
    $content24c = Get-Content $cfg24c -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.4c Build 19045 (Win10) downgrades invalid mirrored to networkingMode=NAT" -Condition ($content24c -match '(?m)^\s*networkingMode\s*=\s*NAT\s*$')

    # Test 2.4d: Win11 21H2 (Build 22000) -> NAT (boundary: < 22621)
    $dir24d = Join-Path $tempBase "test_24d"
    $null = New-Item -ItemType Directory -Path $dir24d -Force
    $cfg24d = Join-Path $dir24d ".wslconfig"
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir24d -RamGB 8 -Cpus 4 -SimulatedBuild 22000
    $content24d = Get-Content $cfg24d -Raw
    Assert-Challenge -Suite "Suite 2" -Name "2.4d Build 22000 (Win11 21H2 boundary < 22621) writes networkingMode=NAT" -Condition ($content24d -match '(?m)^\s*networkingMode\s*=\s*NAT\s*$')

    # Test 2.5: Misplaced & deprecated key scrubbing
    $dir25 = Join-Path $tempBase "test_25"
    $null = New-Item -ItemType Directory -Path $dir25 -Force
    $cfg25 = Join-Path $dir25 ".wslconfig"
    $dirty25 = @"
[wsl2]
systemd=true
appendWindowsPath=false
firewall=true
command=init
default=mios
options=none
mountFsTab=true
generateHosts=true
generateResolvConf=true
hostname=mios
memory=16GB
processors=8
"@
    [System.IO.File]::WriteAllText($cfg25, $dirty25, (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir25 -RamGB 8 -Cpus 4
    $content25 = Get-Content $cfg25 -Raw
    $hasMisplaced = ($content25 -match '(?m)^\s*systemd\s*=' -or
                     $content25 -match '(?m)^\s*appendWindowsPath\s*=' -or
                     $content25 -match '(?m)^\s*firewall\s*=' -or
                     $content25 -match '(?m)^\s*command\s*=' -or
                     $content25 -match '(?m)^\s*hostname\s*=')
    Assert-Challenge -Suite "Suite 2" -Name "2.5 Misplaced/deprecated keys scrubbed from [wsl2]" -Condition (-not $hasMisplaced)
    Assert-Challenge -Suite "Suite 2" -Name "2.5 Preserved memory=16GB and processors=8 after scrubbing" -Condition ($content25 -match '(?m)^\s*memory\s*=\s*16GB\s*$' -and $content25 -match '(?m)^\s*processors\s*=\s*8\s*$')

    # Test 2.6: Idempotency stress test (multiple successive invocations)
    $dir26 = Join-Path $tempBase "test_26"
    $null = New-Item -ItemType Directory -Path $dir26 -Force
    $cfg26 = Join-Path $dir26 ".wslconfig"
    for ($i = 1; $i -le 5; $i++) {
        Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir26 -RamGB 12 -Cpus 6
    }
    $content26 = Get-Content $cfg26 -Raw
    $wsl2HeaderCount = [regex]::Matches($content26, '(?m)^\s*\[wsl2\]\s*$').Count
    $memKeyCount = [regex]::Matches($content26, '(?m)^\s*memory\s*=').Count
    $cpuKeyCount = [regex]::Matches($content26, '(?m)^\s*processors\s*=').Count
    $netKeyCount = [regex]::Matches($content26, '(?m)^\s*networkingMode\s*=').Count
    Assert-Challenge -Suite "Suite 2" -Name "2.6 Idempotency: exactly 1 [wsl2] header after 5 passes" -Condition ($wsl2HeaderCount -eq 1)
    Assert-Challenge -Suite "Suite 2" -Name "2.6 Idempotency: exactly 1 memory key after 5 passes" -Condition ($memKeyCount -eq 1)
    Assert-Challenge -Suite "Suite 2" -Name "2.6 Idempotency: exactly 1 processors key after 5 passes" -Condition ($cpuKeyCount -eq 1)
    Assert-Challenge -Suite "Suite 2" -Name "2.6 Idempotency: exactly 1 networkingMode key after 5 passes" -Condition ($netKeyCount -eq 1)

    # Test 2.7: External section preservation when [wsl2] exists
    $dir27 = Join-Path $tempBase "test_27"
    $null = New-Item -ItemType Directory -Path $dir27 -Force
    $cfg27 = Join-Path $dir27 ".wslconfig"
    $multiSection = @"
[experimental]
autoMemoryReclaim=gradual
sparseVhd=true

[wsl2]
memory=20GB
processors=10
"@
    [System.IO.File]::WriteAllText($cfg27, $multiSection, (New-Object System.Text.UTF8Encoding($false)))
    Invoke-SimulatedSetMiosWslConfig -ConfigDir $dir27 -RamGB 8 -Cpus 4
    $content27 = Get-Content $cfg27 -Raw
    $hasExp = ($content27 -match '\[experimental\]' -and $content27 -match 'autoMemoryReclaim=gradual' -and $content27 -match 'sparseVhd=true')
    Assert-Challenge -Suite "Suite 2" -Name "2.7 Preserves sibling [experimental] section when [wsl2] exists" -Condition $hasExp

    # Test 2.8: BOM-Free UTF-8 Check across all written files
    $allCfgFiles = Get-ChildItem -Path $tempBase -Filter ".wslconfig" -Recurse
    $allBomFree = $true
    foreach ($f in $allCfgFiles) {
        if (-not (Test-IsBomFreeUtf8 $f.FullName)) {
            $allBomFree = $false
            Write-Host "  [FAIL] BOM found in $($f.FullName)" -ForegroundColor Red
        }
    }
    Assert-Challenge -Suite "Suite 2" -Name "2.8 All generated .wslconfig files are strictly BOM-free UTF-8" -Condition ($allBomFree -and $allCfgFiles.Count -gt 5)

} finally {
    if (Test-Path $tempBase) {
        Remove-Item -Path $tempBase -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ============================================================================
# Suite 3: Adversarial Stress Testing of CLI Elevation & Parameter Retention
# ============================================================================
Write-Host "`n--- Suite 3: CLI Elevation & Parameter Retention Stress Tests ---" -ForegroundColor Yellow

# Helper function modeling mios-install.ps1 elevation loop
function Reconstruct-ElevateArgs {
    param([hashtable]$BoundParameters)

    $elevateArgs = @()
    foreach ($entry in $BoundParameters.GetEnumerator()) {
        $paramName = "-$($entry.Key)"
        if ($entry.Value -is [switch] -or $entry.Value -is [bool]) {
            if ($entry.Value) {
                $elevateArgs += $paramName
            }
        } elseif ($entry.Key -ieq 'Passthrough') {
            if ($entry.Value -is [System.Collections.IEnumerable] -and $entry.Value -isnot [string]) {
                foreach ($p in $entry.Value) { $elevateArgs += [string]$p }
            } else {
                $elevateArgs += [string]$entry.Value
            }
        } elseif ($entry.Value -is [System.Collections.IEnumerable] -and $entry.Value -isnot [string]) {
            $elevateArgs += $paramName
            foreach ($val in $entry.Value) {
                $elevateArgs += [string]$val
            }
        } else {
            $elevateArgs += $paramName
            $elevateArgs += [string]$entry.Value
        }
    }
    return ,$elevateArgs
}

# Test 3.1: Switch retention
$params31 = @{
    Target     = 'build'
    Type       = 'oci'
    Unattended = [switch]$true
    DryRun     = [switch]$false
}
$reconstructed31 = Reconstruct-ElevateArgs $params31
$hasUnattended31 = ($reconstructed31 -contains '-Unattended')
$hasDryRun31     = ($reconstructed31 -contains '-DryRun')
Assert-Challenge -Suite "Suite 3" -Name "3.1 True switch (-Unattended) retained as named flag" -Condition $hasUnattended31
Assert-Challenge -Suite "Suite 3" -Name "3.1 False switch (-DryRun:False) omitted from arguments" -Condition (-not $hasDryRun31)

# Test 3.2: Paths with spaces in parameter values
$testPathWithSpaces = "C:\Program Files\MiOS Deployment\rootfs archive.tar"
$params32 = @{
    Target      = 'wsl'
    Passthrough = @($testPathWithSpaces, '-CustomFlag', 'val with spaces')
}
$reconstructed32 = Reconstruct-ElevateArgs $params32
$hasTargetFlag32 = ($reconstructed32 -contains '-Target')
$hasTargetVal32  = ($reconstructed32 -contains 'wsl')
$hasSpacePath32  = ($reconstructed32 -contains $testPathWithSpaces)
$hasSpaceVal32   = ($reconstructed32 -contains 'val with spaces')
Assert-Challenge -Suite "Suite 3" -Name "3.2 Named flag -Target and value 'wsl' preserved" -Condition ($hasTargetFlag32 -and $hasTargetVal32)
Assert-Challenge -Suite "Suite 3" -Name "3.3 Path with spaces preserved intact in arguments" -Condition $hasSpacePath32
Assert-Challenge -Suite "Suite 3" -Name "3.4 Passthrough value with spaces preserved intact" -Condition $hasSpaceVal32

# Test 3.5: Negative Control - Demonstrate legacy defect
$legacyValues = $params31.Values
$legacyDroppedTarget = (-not ($legacyValues -contains '-Target'))
$legacyDroppedType   = (-not ($legacyValues -contains '-Type'))
Assert-Challenge -Suite "Suite 3" -Name "3.5 Negative Control: Legacy PSBoundParameters.Values dropped parameter names" -Condition ($legacyDroppedTarget -and $legacyDroppedType)

# Test 3.6: End-to-end child process parameter binding simulation
$simCallerScript = Join-Path ([System.IO.Path]::GetTempPath()) "sim_caller_$([System.Guid]::NewGuid().ToString('N')).ps1"
$simChildScript  = Join-Path ([System.IO.Path]::GetTempPath()) "sim_child_$([System.Guid]::NewGuid().ToString('N')).ps1"

try {
    # Script that receives elevated arguments and validates parameter binding
    $childContent = @'
[CmdletBinding()]
param(
    [string]$Target = '',
    [string]$Type = '',
    [switch]$Unattended,
    [parameter(ValueFromRemainingArguments=$true)][string[]]$Passthrough = @()
)
$result = [PSCustomObject]@{
    Target      = $Target
    Type        = $Type
    Unattended  = $Unattended.IsPresent
    Passthrough = $Passthrough
}
$result | ConvertTo-Json -Compress
'@
    [System.IO.File]::WriteAllText($simChildScript, $childContent, [System.Text.Encoding]::UTF8)

    # Invoke child process with reconstructed arguments
    $argsToPass = @(
        '-Target', 'wsl',
        '-Type', 'oci',
        '-Unattended',
        'C:\Program Files\MiOS\export.tar',
        '--flag-with-space',
        'value with spaces'
    )
    $procOut = & pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $simChildScript @argsToPass
    $childResult = $procOut | ConvertFrom-Json

    $boundTarget      = ($childResult.Target -eq 'wsl')
    $boundType        = ($childResult.Type -eq 'oci')
    $boundUnattended  = ($childResult.Unattended -eq $true)
    $boundPass0       = ($childResult.Passthrough[0] -eq 'C:\Program Files\MiOS\export.tar')
    $boundPass1       = ($childResult.Passthrough[1] -eq '--flag-with-space')
    $boundPass2       = ($childResult.Passthrough[2] -eq 'value with spaces')

    Assert-Challenge -Suite "Suite 3" -Name "3.6 Child process correctly bound -Target 'wsl'" -Condition $boundTarget
    Assert-Challenge -Suite "Suite 3" -Name "3.6 Child process correctly bound -Type 'oci'" -Condition $boundType
    Assert-Challenge -Suite "Suite 3" -Name "3.6 Child process correctly bound -Unattended switch" -Condition $boundUnattended
    Assert-Challenge -Suite "Suite 3" -Name "3.6 Child process correctly bound path with spaces in Passthrough" -Condition $boundPass0
    Assert-Challenge -Suite "Suite 3" -Name "3.6 Child process correctly bound argument with spaces in Passthrough" -Condition ($boundPass1 -and $boundPass2)

} finally {
    if (Test-Path $simChildScript) { Remove-Item -LiteralPath $simChildScript -Force -ErrorAction SilentlyContinue }
}

# ============================================================================
# Suite 4: Fast-Path Catalog & Target Resolution
# ============================================================================
Write-Host "`n--- Suite 4: Fast-Path Catalog & Target Resolution ---" -ForegroundColor Yellow

# Parse mios-install.ps1 and evaluate Get-MiosCatalog and Resolve-Target
$instAst = [System.Management.Automation.Language.Parser]::ParseFile("c:\mios-bootstrap\installation\mios-install.ps1", [ref]$null, [ref]$null)
$catAst = $instAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Get-MiosCatalog' }, $true)
$resAst = $instAst.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Resolve-Target' }, $true)

$script:Root = "c:\mios-bootstrap"
$script:BuildPs = "c:\mios-bootstrap\build-mios.ps1"
$script:AutoDir = "c:\mios-bootstrap\field\autounattend"

. ([scriptblock]::Create($catAst.Extent.Text))
. ([scriptblock]::Create($resAst.Extent.Text))

$catalog = Get-MiosCatalog

# Test 4.1: wsl and import in catalog
$hasWslInCat    = $catalog.Contains('wsl')
$hasImportInCat = $catalog.Contains('import')
$wslNeedsAdmin  = if ($hasWslInCat) { $catalog['wsl'].needsAdmin -eq $true } else { $false }
$impNeedsAdmin  = if ($hasImportInCat) { $catalog['import'].needsAdmin -eq $true } else { $false }

Assert-Challenge -Suite "Suite 4" -Name "4.1 Get-MiosCatalog contains dedicated 'wsl' target" -Condition $hasWslInCat
Assert-Challenge -Suite "Suite 4" -Name "4.1 Get-MiosCatalog contains dedicated 'import' target" -Condition $hasImportInCat
Assert-Challenge -Suite "Suite 4" -Name "4.1 'wsl' target declares needsAdmin = true" -Condition $wslNeedsAdmin
Assert-Challenge -Suite "Suite 4" -Name "4.1 'import' target declares needsAdmin = true" -Condition $impNeedsAdmin

# Test 4.2: Resolve-Target maps wsl and import to build-mios.ps1 -ImportWsl
$planWsl = Resolve-Target -Target 'wsl' -Unattended $true -Passthrough @('test.tar')
$planImp = Resolve-Target -Target 'import' -Unattended $true -Passthrough @('test.tar')

$wslTargetMapped = ($planWsl.Exe -eq $script:BuildPs -and
                    $planWsl.NeedsAdmin -eq $true -and
                    $planWsl.Args -contains '-ImportWsl' -and
                    $planWsl.Args -contains '-Unattended' -and
                    $planWsl.Args -contains 'test.tar')

$impTargetMapped = ($planImp.Exe -eq $script:BuildPs -and
                    $planImp.NeedsAdmin -eq $true -and
                    $planImp.Args -contains '-ImportWsl' -and
                    $planImp.Args -contains '-Unattended' -and
                    $planImp.Args -contains 'test.tar')

Assert-Challenge -Suite "Suite 4" -Name "4.2 Resolve-Target correctly routes 'wsl' to build-mios.ps1 -ImportWsl" -Condition $wslTargetMapped
Assert-Challenge -Suite "Suite 4" -Name "4.2 Resolve-Target correctly routes 'import' to build-mios.ps1 -ImportWsl" -Condition $impTargetMapped

# ============================================================================
# Suite 5: MiOS-Cat Verb Dispatch & Array Splatting
# ============================================================================
Write-Host "`n--- Suite 5: MiOS-Cat Verb Dispatch & Array Splatting ---" -ForegroundColor Yellow

# Test 5.1: AST check of MiOS-Cat.ps1 for absence of literal '+' token in CommandElements
$catFile = "c:\mios-bootstrap\field\MiOS-Cat.ps1"
$catAst = [System.Management.Automation.Language.Parser]::ParseFile($catFile, [ref]$null, [ref]$null)
$commands = $catAst.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)

$spuriousPlus = $false
foreach ($cmd in $commands) {
    foreach ($elem in $cmd.CommandElements) {
        if ($elem -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $elem.Value -eq '+') {
            $spuriousPlus = $true
        }
    }
}
Assert-Challenge -Suite "Suite 5" -Name "5.1 MiOS-Cat.ps1 AST contains zero literal '+' command expressions" -Condition (-not $spuriousPlus)

# Test 5.2: Verify switch logic in MiOS-Cat.ps1 handles wsl and import verbs
$switchAst = $catAst.Find({ $args[0] -is [System.Management.Automation.Language.SwitchStatementAst] }, $true)
$switchText = if ($switchAst) { $switchAst.Extent.Text } else { "" }
$hasWslVerbRegex = ($switchText -match '\^\(wsl\|import\)\$')
$usesArrayConcat = ($switchText -match '\$importArgs\s*=\s*@\(''-Target'',\s*\$Verb\)\s*\+\s*\$VerbArgs')
$usesSplatting   = ($switchText -match 'Invoke-MiOSCatInstall\s+@importArgs')

Assert-Challenge -Suite "Suite 5" -Name "5.2 MiOS-Cat.ps1 handles 'wsl' and 'import' verbs in switch regex" -Condition $hasWslVerbRegex
Assert-Challenge -Suite "Suite 5" -Name "5.2 MiOS-Cat.ps1 constructs importArgs array cleanly" -Condition $usesArrayConcat
Assert-Challenge -Suite "Suite 5" -Name "5.2 MiOS-Cat.ps1 invokes install via @importArgs array splatting" -Condition $usesSplatting

# ============================================================================
# Summary & Exit
# ============================================================================
Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  CHALLENGE TEST SUMMARY" -ForegroundColor Cyan
Write-Host "  Total Tests  : $script:TotalTests"
Write-Host "  Passed Tests : $script:PassedTests" -ForegroundColor Green
Write-Host "  Failed Tests : $script:FailedTests" -ForegroundColor $(if ($script:FailedTests -eq 0) { 'Green' } else { 'Red' })
Write-Host "========================================================`n" -ForegroundColor Cyan

if ($script:FailedTests -eq 0) {
    Write-Host "ALL MILESTONE 3 ADVERSARIAL CHALLENGES PASSED SUCCESSFULLY!`n" -ForegroundColor Green
    exit 0
} else {
    Write-Host "ADVERSARIAL CHALLENGES FAILED: $script:FailedTests failure(s).`n" -ForegroundColor Red
    exit 1
}
