# AI-hint: Pester unit tests for usr/libexec/mios/Setup-MiOSLanPortProxy.ps1: verifies AST-lifted Phase 4 SSOT alignment, operator-layer confinement, retired-port cleanup, convergence, unelevated UAC relaunch, and planted-defect detection with all Windows/WSL side effects stubbed in TestDrive.
# AI-doc: usr/share/doc/mios/manual/tests.md

BeforeAll {
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).ProviderPath
    $script:DefaultSetupScript = Join-Path $script:RepoRoot 'usr\libexec\mios\Setup-MiOSLanPortProxy.ps1'
    $script:CanonicalBuildScript = Join-Path $script:RepoRoot 'build-mios.ps1'
    $script:CanonicalVendorToml = Join-Path $script:RepoRoot 'usr\share\mios\mios.toml'

    $script:TargetSetupScript = if ($env:MIOS_LAN_PORTPROXY_SCRIPT) {
        (Resolve-Path -LiteralPath $env:MIOS_LAN_PORTPROXY_SCRIPT).ProviderPath
    } else {
        $script:DefaultSetupScript
    }

    # Extract retired ports dynamically from the script's own $retiredPorts AST (zero port literals in test code).
    function script:Get-ScriptRetiredPorts([string]$ScriptPath) {
        $tokens = $null; $errs = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$tokens, [ref]$errs)
        $assign = $ast.Find({
            param($n)
            $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
            $n.Left.Extent.Text -eq '$retiredPorts'
        }, $true)
        if (-not $assign) { return @() }
        $sb = [scriptblock]::Create($assign.Right.Extent.Text)
        $items = @(& $sb)
        return @($items | ForEach-Object {
            [pscustomobject]@{ Port = [int]$_.Port; Name = [string]$_.Name }
        })
    }

    # Extract expected SSOT exposure plan from build-mios.ps1 using the same AST-lifted functions in a scratch tree.
    function script:New-FakeRepoTree {
        param(
            [string]$Name,
            [string]$SetupContent = '',
            [string]$SetupSourcePath = $script:TargetSetupScript,
            [switch]$OmitBuildScript,
            [string]$UserToml = '',
            [string]$AppDataToml = '',
            [string]$VendorTomlOverride = ''
        )
        $root = Join-Path $TestDrive $Name
        $tree = Join-Path $root 'tree'
        $homeDir = Join-Path $root 'home'
        $appData = Join-Path $root 'appdata'
        $null = New-Item -ItemType Directory -Path (Join-Path $tree 'usr\libexec\mios') -Force
        $null = New-Item -ItemType Directory -Path (Join-Path $tree 'usr\share\mios') -Force
        $null = New-Item -ItemType Directory -Path (Join-Path $homeDir '.config\mios') -Force
        $null = New-Item -ItemType Directory -Path (Join-Path $appData 'MiOS') -Force

        $setupDst = Join-Path $tree 'usr\libexec\mios\Setup-MiOSLanPortProxy.ps1'
        if ($SetupContent) {
            [IO.File]::WriteAllText($setupDst, $SetupContent, [System.Text.UTF8Encoding]::new($false))
        } else {
            Copy-Item -LiteralPath $SetupSourcePath -Destination $setupDst -Force
        }

        if (-not $OmitBuildScript) {
            Copy-Item -LiteralPath $script:CanonicalBuildScript -Destination (Join-Path $tree 'build-mios.ps1') -Force
        }

        $vendorDst = Join-Path $tree 'usr\share\mios\mios.toml'
        if ($VendorTomlOverride) {
            [IO.File]::WriteAllText($vendorDst, $VendorTomlOverride, [System.Text.UTF8Encoding]::new($false))
        } else {
            Copy-Item -LiteralPath $script:CanonicalVendorToml -Destination $vendorDst -Force
        }

        if ($UserToml) {
            [IO.File]::WriteAllText((Join-Path $homeDir '.config\mios\mios.toml'), $UserToml, [System.Text.UTF8Encoding]::new($false))
        }
        if ($AppDataToml) {
            [IO.File]::WriteAllText((Join-Path $appData 'MiOS\mios.toml'), $AppDataToml, [System.Text.UTF8Encoding]::new($false))
        }

        return [pscustomobject]@{
            Root        = $root
            Tree        = $tree
            SetupPath   = $setupDst
            BuildPath   = (Join-Path $tree 'build-mios.ps1')
            HomeDir     = $homeDir
            AppDataDir  = $appData
            RunnerPath  = (Join-Path $root 'runner.ps1')
        }
    }

    $script:HarnessTemplate = @'
param(
    [string]$Mode,
    [string]$TargetScript,
    [string]$BuildScript,
    [string]$TreePath,
    [string]$HomeDir,
    [string]$AppDataDir,
    [string]$Admin,
    [string]$WslIp,
    [string]$SeedJson
)
$ErrorActionPreference = 'Stop'

$env:SystemDrive = $TreePath
$env:USERPROFILE = $HomeDir
$env:APPDATA = $AppDataDir

Add-Type -TypeDefinition "
namespace MiosLanTestStub {
    public enum Role { Administrator }
    public sealed class Identity { public static Identity GetCurrent() { return new Identity(); } }
    public sealed class Principal {
        public static bool IsAdmin;
        public Principal(Identity identity) {}
        public bool IsInRole(Role role) { return IsAdmin; }
    }
}
"
[MiosLanTestStub.Principal]::IsAdmin = ($Admin -eq '1')
$acc = [psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators')
$acc::Add('Security.Principal.WindowsPrincipal', [MiosLanTestStub.Principal])
$acc::Add('Security.Principal.WindowsIdentity', [MiosLanTestStub.Identity])
$acc::Add('Security.Principal.WindowsBuiltInRole', [MiosLanTestStub.Role])

$global:FwRules = [ordered]@{}
$global:PortProxies = [ordered]@{}
$global:StartProcessCalls = [System.Collections.Generic.List[object]]::new()
$global:WslCalls = [System.Collections.Generic.List[string]]::new()
$global:Hosts = [System.Collections.Generic.List[string]]::new()

if ($SeedJson) {
    $seed = $SeedJson | ConvertFrom-Json
    if ($seed.Rules) {
        foreach ($r in $seed.Rules) {
            $global:FwRules[[string]$r.DisplayName] = [ordered]@{
                DisplayName   = [string]$r.DisplayName
                Enabled       = 'True'
                Action        = 'Allow'
                Direction     = 'Inbound'
                Protocol      = 'TCP'
                LocalPort     = [int]$r.LocalPort
                Profile       = [string]$r.Profile
                RemoteAddress = @($r.RemoteAddress | ForEach-Object { [string]$_ })
            }
        }
    }
    if ($seed.Proxies) {
        foreach ($p in $seed.Proxies) {
            $global:PortProxies[[string]$p.ListenPort] = [ordered]@{
                ListenAddress  = '0.0.0.0'
                ListenPort     = [int]$p.ListenPort
                ConnectAddress = [string]$p.ConnectAddress
                ConnectPort    = [int]$p.ConnectPort
            }
        }
    }
}

function global:Test-Path {
    param([string]$LiteralPath, [string]$Path, [string]$PathType)
    $t = if ($LiteralPath) { $LiteralPath } else { $Path }
    if ($t -and $t -like 'M:\*') { return $false }
    $fwd = @{}
    if ($PSBoundParameters.ContainsKey('LiteralPath')) { $fwd['LiteralPath'] = $LiteralPath }
    if ($PSBoundParameters.ContainsKey('Path')) { $fwd['Path'] = $Path }
    if ($PSBoundParameters.ContainsKey('PathType')) { $fwd['PathType'] = $PathType }
    Microsoft.PowerShell.Management\Test-Path @fwd
}

function global:Start-Process {
    param([string]$FilePath, [string[]]$ArgumentList, [string]$Verb)
    $global:StartProcessCalls.Add([pscustomobject]@{
        FilePath     = $FilePath
        ArgumentList = @($ArgumentList)
        Verb         = $Verb
    })
}

function global:Get-NetFirewallRule {
    param([string]$DisplayName, $ErrorAction)
    $matched = @()
    foreach ($k in @($global:FwRules.Keys)) {
        if (-not $DisplayName -or $k -like $DisplayName) {
            $r = $global:FwRules[$k]
            $matched += [pscustomobject]@{
                DisplayName   = $r.DisplayName
                Enabled       = $r.Enabled
                Action        = $r.Action
                Direction     = $r.Direction
                Protocol      = $r.Protocol
                LocalPort     = $r.LocalPort
                Profile       = $r.Profile
                RemoteAddress = $r.RemoteAddress
            }
        }
    }
    if ($matched.Count -eq 0) { return $null }
    return $matched
}

function global:New-NetFirewallRule {
    param([string]$DisplayName, [string]$Description, [string]$Direction, [string]$Action, [string]$Protocol, [int]$LocalPort, [string]$Profile, [string[]]$RemoteAddress, $ErrorAction)
    $global:FwRules[$DisplayName] = [ordered]@{
        DisplayName   = $DisplayName
        Enabled       = 'True'
        Action        = $Action
        Direction     = $Direction
        Protocol      = $Protocol
        LocalPort     = $LocalPort
        Profile       = $Profile
        RemoteAddress = @($RemoteAddress | ForEach-Object { [string]$_ })
    }
    return [pscustomobject]$global:FwRules[$DisplayName]
}

function global:Set-NetFirewallRule {
    param([string]$DisplayName, $Enabled, [string]$Action, [string]$Direction, [string]$Profile, [string[]]$RemoteAddress, $ErrorAction)
    if ($global:FwRules.Contains($DisplayName)) {
        $r = $global:FwRules[$DisplayName]
        $r.Enabled = [string]$Enabled
        $r.Action = $Action
        $r.Direction = $Direction
        if ($Profile) { $r.Profile = $Profile }
        if ($RemoteAddress) { $r.RemoteAddress = @($RemoteAddress | ForEach-Object { [string]$_ }) }
    }
}

function global:Remove-NetFirewallRule {
    param([string]$DisplayName, $ErrorAction)
    foreach ($k in @($global:FwRules.Keys)) {
        if ($k -like $DisplayName) { $global:FwRules.Remove($k) }
    }
}

function global:Get-NetFirewallPortFilter {
    param([Parameter(ValueFromPipeline = $true)]$InputObject, $ErrorAction)
    process {
        if ($InputObject) {
            [pscustomobject]@{ RuleDisplayName = $InputObject.DisplayName }
        }
    }
}

function global:Set-NetFirewallPortFilter {
    param([Parameter(ValueFromPipeline = $true)]$InputObject, [string]$Protocol, [int]$LocalPort, $ErrorAction)
    process {
        if ($InputObject -and $global:FwRules.Contains($InputObject.RuleDisplayName)) {
            $global:FwRules[$InputObject.RuleDisplayName].Protocol = $Protocol
            $global:FwRules[$InputObject.RuleDisplayName].LocalPort = $LocalPort
        }
    }
}

function global:netsh {
    $a = @($args | ForEach-Object { [string]$_ })
    $joined = $a -join ' '
    if ($joined -like 'interface portproxy delete v4tov4*') {
        $lp = ($a | Where-Object { $_ -like 'listenport=*' }) -replace '^listenport=', ''
        if ($lp -and $global:PortProxies.Contains([string]$lp)) {
            $global:PortProxies.Remove([string]$lp)
        }
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined -like 'interface portproxy add v4tov4*') {
        $la = (($a | Where-Object { $_ -like 'listenaddress=*' }) -replace '^listenaddress=', '') | Select-Object -First 1
        $lp = (($a | Where-Object { $_ -like 'listenport=*' }) -replace '^listenport=', '') | Select-Object -First 1
        $ca = (($a | Where-Object { $_ -like 'connectaddress=*' }) -replace '^connectaddress=', '') | Select-Object -First 1
        $cp = (($a | Where-Object { $_ -like 'connectport=*' }) -replace '^connectport=', '') | Select-Object -First 1
        $global:PortProxies[[string]$lp] = [ordered]@{
            ListenAddress  = $la
            ListenPort     = [int]$lp
            ConnectAddress = $ca
            ConnectPort    = [int]$cp
        }
        $global:LASTEXITCODE = 0
        return
    }
    if ($joined -like 'interface portproxy show*') {
        foreach ($k in @($global:PortProxies.Keys)) {
            $p = $global:PortProxies[$k]
            Write-Output ("{0,-15} {1,-11} {2,-15} {3}" -f $p.ListenAddress, $p.ListenPort, $p.ConnectAddress, $p.ConnectPort)
        }
        $global:LASTEXITCODE = 0
        return
    }
    $global:LASTEXITCODE = 0
}
Set-Item -Path 'function:global:netsh.exe' -Value ${function:global:netsh}

function global:wsl.exe {
    $a = @($args | ForEach-Object { [string]$_ })
    $global:WslCalls.Add(($a -join ' '))
    if ($a -contains '--list') {
        $global:LASTEXITCODE = 0
        return @('podman-MiOS-DEV')
    }
    if (($a -join ' ') -match 'ip -4 -o addr show eth0') {
        $global:LASTEXITCODE = 0
        return "2: eth0    inet $WslIp/24 brd 192.0.2.255 scope global eth0"
    }
    $global:LASTEXITCODE = 0
}
Set-Item -Path 'function:global:wsl' -Value ${function:global:wsl.exe}

function global:Get-Service { param([string]$Name, $ErrorAction) [pscustomobject]@{ Name = $Name; Status = 'Running' } }
function global:Set-Service { param([string]$Name, [string]$StartupType, $ErrorAction) }
function global:Start-Service { param([string]$Name, $ErrorAction) }
function global:Get-NetIPAddress { param($AddressFamily, $ErrorAction) [pscustomobject]@{ IPAddress = '192.168.10.50'; InterfaceAlias = 'Ethernet' } }
function global:Write-Host { param([Parameter(Position = 0)]$Object, $ForegroundColor, [switch]$NoNewline) $global:Hosts.Add([string]$Object) }

$errMsg = ''
$planOut = $null
try {
    if ($Mode -eq 'setup') {
        & $TargetScript
    } elseif ($Mode -eq 'phase4') {
        $tok = $null; $perr = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($BuildScript, [ref]$tok, [ref]$perr)
        if ($perr.Count) { throw "build-mios.ps1 parse error" }
        $top = @($ast.EndBlock.Statements)
        $names = @('Resolve-MiosTomlLayers', 'Get-MiosTomlValue', 'Get-MiosSsotPort', 'Get-MiosVendorTomlValue',
            'ConvertTo-MiosNetworkPrefix', 'Test-MiosPrefixWithin', 'Resolve-MiosWslNetworkingMode', 'Resolve-MiosBuilderDistribution',
            'Resolve-MiosWslVmAddress', 'Get-MiosVendorPortSet',
            'Get-MiosLanExposurePlan', 'Set-MiosLanFirewallRules', 'Set-MiosLanPortProxy')
        $defs = foreach ($n in $names) {
            ($top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $n })[0].Extent.Text
        }
        foreach ($v in @('$script:_MiosTomlCache', '$DevDistro')) {
            $defs += ($top | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq $v })[0].Extent.Text
        }
        Set-StrictMode -Off
        . ([scriptblock]::Create($defs -join "`n`n"))
        function Write-Log([string]$M, [string]$L = 'INFO') {}
        function Log-Ok([string]$T) {}
        function Log-Info([string]$T) {}
        function Log-Warn([string]$T) {}
        Set-MiosLanFirewallRules
        Set-MiosLanPortProxy
        $planOut = Get-MiosLanExposurePlan
    } elseif ($Mode -eq 'setup-then-phase4') {
        & $TargetScript
        # Re-run Phase 4 immediately in the same state to prove idempotent two-way convergence.
        $tok = $null; $perr = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($BuildScript, [ref]$tok, [ref]$perr)
        $top = @($ast.EndBlock.Statements)
        $names = @('Resolve-MiosTomlLayers', 'Get-MiosTomlValue', 'Get-MiosSsotPort', 'Get-MiosVendorTomlValue',
            'ConvertTo-MiosNetworkPrefix', 'Test-MiosPrefixWithin', 'Resolve-MiosWslNetworkingMode', 'Resolve-MiosBuilderDistribution',
            'Resolve-MiosWslVmAddress', 'Get-MiosVendorPortSet',
            'Get-MiosLanExposurePlan', 'Set-MiosLanFirewallRules', 'Set-MiosLanPortProxy')
        $defs = foreach ($n in $names) {
            ($top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $n })[0].Extent.Text
        }
        foreach ($v in @('$script:_MiosTomlCache', '$DevDistro')) {
            $defs += ($top | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq $v })[0].Extent.Text
        }
        Set-StrictMode -Off
        . ([scriptblock]::Create($defs -join "`n`n"))
        function Write-Log([string]$M, [string]$L = 'INFO') {}
        function Log-Ok([string]$T) {}
        function Log-Info([string]$T) {}
        function Log-Warn([string]$T) {}
        Set-MiosLanFirewallRules
        Set-MiosLanPortProxy
    } elseif ($Mode -eq 'phase4-then-setup') {
        $tok = $null; $perr = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($BuildScript, [ref]$tok, [ref]$perr)
        $top = @($ast.EndBlock.Statements)
        $names = @('Resolve-MiosTomlLayers', 'Get-MiosTomlValue', 'Get-MiosSsotPort', 'Get-MiosVendorTomlValue',
            'ConvertTo-MiosNetworkPrefix', 'Test-MiosPrefixWithin', 'Resolve-MiosWslNetworkingMode', 'Resolve-MiosBuilderDistribution',
            'Resolve-MiosWslVmAddress', 'Get-MiosVendorPortSet',
            'Get-MiosLanExposurePlan', 'Set-MiosLanFirewallRules', 'Set-MiosLanPortProxy')
        $defs = foreach ($n in $names) {
            ($top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $n })[0].Extent.Text
        }
        foreach ($v in @('$script:_MiosTomlCache', '$DevDistro')) {
            $defs += ($top | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq $v })[0].Extent.Text
        }
        Set-StrictMode -Off
        . ([scriptblock]::Create($defs -join "`n`n"))
        function Write-Log([string]$M, [string]$L = 'INFO') {}
        function Log-Ok([string]$T) {}
        function Log-Info([string]$T) {}
        function Log-Warn([string]$T) {}
        Set-MiosLanFirewallRules
        Set-MiosLanPortProxy
        Set-StrictMode -Version Latest
        & $TargetScript
    }
} catch {
    $errMsg = [string]$_.Exception.Message
}

[ordered]@{
    err           = $errMsg
    rules         = @($global:FwRules.Values)
    proxies       = @($global:PortProxies.Values)
    startProcess  = @($global:StartProcessCalls)
    wslCalls      = @($global:WslCalls)
    hosts         = @($global:Hosts)
    plan          = $planOut
} | ConvertTo-Json -Depth 8 -Compress
'@

    function script:Invoke-StubbedSetup {
        param(
            [Parameter(Mandatory)]$TreeObj,
            [string]$Mode = 'setup',
            [bool]$Admin = $true,
            [string]$WslIp = '172.24.10.20',
            [hashtable]$Seed = $null,
            [hashtable]$ExtraEnv = @{}
        )
        [IO.File]::WriteAllText($TreeObj.RunnerPath, $script:HarnessTemplate, [System.Text.UTF8Encoding]::new($false))
        $seedJson = if ($Seed) { $Seed | ConvertTo-Json -Depth 6 -Compress } else { '' }

        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = [System.Environment]::ProcessPath  # the running pwsh, on any OS
        $psi.UseShellExecute = $false
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        foreach ($kv in $ExtraEnv.GetEnumerator()) {
            $psi.Environment[$kv.Key] = [string]$kv.Value
        }
        $argList = @(
            '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', $TreeObj.RunnerPath,
            '-Mode', $Mode,
            '-TargetScript', $TreeObj.SetupPath,
            '-BuildScript', $TreeObj.BuildPath,
            '-TreePath', $TreeObj.Tree,
            '-HomeDir', $TreeObj.HomeDir,
            '-AppDataDir', $TreeObj.AppDataDir,
            '-Admin', $(if ($Admin) { '1' } else { '0' }),
            '-WslIp', $WslIp,
            '-SeedJson', $seedJson
        )
        foreach ($a in $argList) { $null = $psi.ArgumentList.Add($a) }

        $proc = [System.Diagnostics.Process]::Start($psi)
        $proc.StandardInput.Close()
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit(15000)) {
            try { $proc.Kill($true) } catch { $proc.Kill() }
            throw "Child pwsh timed out after 15s (e.g. blocked on ReadKey)."
        }
        $stdout = $outTask.GetAwaiter().GetResult()
        $stderr = $errTask.GetAwaiter().GetResult()
        if ($proc.ExitCode -ne 0) {
            throw "Child pwsh exited $($proc.ExitCode): $stderr`n$stdout"
        }
        $lastLine = ($stdout -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
        return ($lastLine | ConvertFrom-Json)
    }

    function script:Assert-SetupConvergedClean {
        param($Got, $ExpectedPlan, [string]$ExpectedWslIp = '172.24.10.20', [int[]]$RetiredPortNums = @())
        $Got.err | Should -Be ''

        # Expected rules: exact "MiOS - <key> (<port>/tcp)" for [firewall.windows].expose
        $expectedRuleNames = @($ExpectedPlan.Ports.PSObject.Properties | ForEach-Object { "MiOS - $($_.Name) ($($_.Value)/tcp)" })
        $actualRuleNames = @($Got.rules | ForEach-Object { $_.DisplayName })
        ($actualRuleNames | Sort-Object) | Should -Be ($expectedRuleNames | Sort-Object)

        # RemoteAddress and Profile come from SSOT
        $expectedProfiles = ($ExpectedPlan.Profiles -join ',')
        $expectedRemotes = @($ExpectedPlan.RemoteAddresses)
        foreach ($r in $Got.rules) {
            $r.Profile | Should -Be $expectedProfiles
            @($r.RemoteAddress) | Should -Be $expectedRemotes
            @($r.RemoteAddress) | Should -Not -Contain 'Any'
            @($r.RemoteAddress) | Should -Not -Contain '0.0.0.0/0'
        }

        # Forwards go to VM eth0 IP, not 127.0.0.1, for exact plan ports
        $expectedPorts = @($ExpectedPlan.Ports.PSObject.Properties | ForEach-Object { [int]$_.Value })
        $actualPorts = @($Got.proxies | ForEach-Object { [int]$_.ListenPort })
        ($actualPorts | Sort-Object) | Should -Be ($expectedPorts | Sort-Object)
        foreach ($p in $Got.proxies) {
            $p.ConnectAddress | Should -Be $ExpectedWslIp
            $p.ConnectAddress | Should -Not -Be '127.0.0.1'
            $p.ConnectPort | Should -Be $p.ListenPort
        }

        # Retired ports must not remain
        foreach ($rp in $RetiredPortNums) {
            $actualPorts | Should -Not -Contain $rp
        }
    }
}

Describe 'Setup-MiOSLanPortProxy.ps1 SSOT alignment' {
    BeforeAll {
        $script:BaselineTree = New-FakeRepoTree -Name 'baseline'
        $script:Phase4Baseline = Invoke-StubbedSetup -TreeObj $script:BaselineTree -Mode 'phase4'
        $script:ExpectedPlan = $script:Phase4Baseline.plan
        $script:RetiredEntries = Get-ScriptRetiredPorts -ScriptPath $script:DefaultSetupScript
        $script:RetiredPortNums = @($script:RetiredEntries | ForEach-Object { [int]$_.Port })
    }

    It 'creates exact SSOT firewall rules and VM eth0 portproxies with no errors' {
        $t = New-FakeRepoTree -Name 'happy'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -WslIp '172.24.10.20'
        Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -ExpectedWslIp '172.24.10.20' -RetiredPortNums $script:RetiredPortNums
    }

    It 'converges both ways with test-lifted build-mios.ps1 Phase 4' {
        $t1 = New-FakeRepoTree -Name 'conv-setup-vs-p4'
        $gotSetup = Invoke-StubbedSetup -TreeObj $t1 -Mode 'setup'
        $gotSetup.err | Should -Be ''

        # Compare end state directly against Phase 4
        ($gotSetup.rules | ForEach-Object { $_.DisplayName } | Sort-Object) | Should -Be ($script:Phase4Baseline.rules | ForEach-Object { $_.DisplayName } | Sort-Object)
        ($gotSetup.proxies | ForEach-Object { "$($_.ListenPort)->$($_.ConnectAddress):$($_.ConnectPort)" } | Sort-Object) | Should -Be (
            $script:Phase4Baseline.proxies | ForEach-Object { "$($_.ListenPort)->$($_.ConnectAddress):$($_.ConnectPort)" } | Sort-Object
        )

        # Two-way convergence: setup -> phase4 and phase4 -> setup yield identical final state
        $t2 = New-FakeRepoTree -Name 'conv-s-then-p4'
        $sThenP4 = Invoke-StubbedSetup -TreeObj $t2 -Mode 'setup-then-phase4'
        $t3 = New-FakeRepoTree -Name 'conv-p4-then-s'
        $p4ThenS = Invoke-StubbedSetup -TreeObj $t3 -Mode 'phase4-then-setup'

        $sThenP4.err | Should -Be ''
        $p4ThenS.err | Should -Be ''
        ($sThenP4.rules | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($p4ThenS.rules | ConvertTo-Json -Depth 5 -Compress)
        ($sThenP4.proxies | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($p4ThenS.proxies | ConvertTo-Json -Depth 5 -Compress)
    }

    It 'confines the operator layer so it cannot widen expose, profiles, remote_addresses, or port numbers' {
        $operatorToml = @"
[firewall.windows]
expose = ["forge_http", "pgvector", "searxng"]
profiles = ["Private", "Public"]
remote_addresses = ["LocalSubnet", "0.0.0.0/0", "Any", "1.2.3.4/32"]

[ports]
forge_http = 19999
"@
        $t = New-FakeRepoTree -Name 'operator-confined' -UserToml $operatorToml
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
        $got.err | Should -Be ''

        # Only forge_http (at vendor port) on Private from LocalSubnet should survive
        $got.rules.Count | Should -Be 1
        $got.rules[0].DisplayName | Should -Be ("MiOS - forge_http (" + $script:ExpectedPlan.Ports.forge_http + "/tcp)")
        $got.rules[0].Profile | Should -Be 'Private'
        @($got.rules[0].RemoteAddress) | Should -Be @('LocalSubnet')
        $got.proxies.Count | Should -Be 1
        $got.proxies[0].ListenPort | Should -Be ([int]$script:ExpectedPlan.Ports.forge_http)
    }

    It 'ignores MIOS_PORT_* and MIOS_PORTS_* process environment overrides in favor of SSOT' {
        $t = New-FakeRepoTree -Name 'env-ignored'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -ExtraEnv @{
            MIOS_PORTS_FORGE_HTTP   = '19400'
            MIOS_PORT_GUACAMOLE_WEB = '19220'
            MIOS_PORTS_SEARXNG      = '19800'
        }
        Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums
    }

    It 'removes pre-existing retired portproxy forwards and firewall rules extracted from $retiredPorts AST' {
        $script:RetiredEntries.Count | Should -BeGreaterThan 0
        $seedRules = @($script:RetiredEntries | ForEach-Object {
            @{ DisplayName = "MiOS - $($_.Name) ($($_.Port)/tcp)"; LocalPort = $_.Port; Profile = 'Private'; RemoteAddress = @('LocalSubnet') }
        })
        $seedProxies = @($script:RetiredEntries | ForEach-Object {
            @{ ListenPort = $_.Port; ConnectAddress = '127.0.0.1'; ConnectPort = $_.Port }
        })
        $t = New-FakeRepoTree -Name 'retired-cleanup'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -Seed @{ Rules = $seedRules; Proxies = $seedProxies }
        Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums
        foreach ($rp in $script:RetiredEntries) {
            ($got.rules | ForEach-Object { $_.DisplayName }) | Should -Not -Contain ("MiOS - $($rp.Name) ($($rp.Port)/tcp)")
        }
    }

    It 'throws and changes nothing when build-mios.ps1 is missing from the tree' {
        $seed = @{
            Rules   = @(@{ DisplayName = 'MiOS - keep-me (9999/tcp)'; LocalPort = 9999; Profile = 'Private'; RemoteAddress = @('LocalSubnet') })
            Proxies = @(@{ ListenPort = 9999; ConnectAddress = '127.0.0.1'; ConnectPort = 9999 })
        }
        $t = New-FakeRepoTree -Name 'no-build' -OmitBuildScript
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -Seed $seed
        $got.err | Should -Not -Be ''
        $got.rules.Count | Should -Be 1
        $got.rules[0].DisplayName | Should -Be 'MiOS - keep-me (9999/tcp)'
        $got.proxies.Count | Should -Be 1
        $got.proxies[0].ListenPort | Should -Be 9999
    }

    It 'relaunches via UAC with no extra arguments when unelevated and changes nothing' {
        $t = New-FakeRepoTree -Name 'unelevated'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -Admin $false
        $got.err | Should -Be ''
        $got.startProcess.Count | Should -Be 1
        $got.startProcess[0].FilePath | Should -Be 'pwsh.exe'
        $got.startProcess[0].Verb | Should -Be 'RunAs'
        @($got.startProcess[0].ArgumentList) | Should -Be @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $t.SetupPath)
        $got.rules.Count | Should -Be 0
        $got.proxies.Count | Should -Be 0
    }

    Context 'Planted-defect controls' {
        BeforeAll {
            $script:ShippedText = [IO.File]::ReadAllText($script:TargetSetupScript)
        }

        It 'catches planted defect: unscoped own rule loop' {
            $planted = $script:ShippedText.Replace(
                "try { Set-MiosLanFirewallRules } catch { Log-Warn `"Set-MiosLanFirewallRules: `$(`$_.Exception.Message)`" }",
                "try { Set-MiosLanFirewallRules; New-NetFirewallRule -DisplayName 'MiOS - unscoped (8499/tcp)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8499 -Profile Any -RemoteAddress Any | Out-Null } catch { Log-Warn `"Set-MiosLanFirewallRules: `$(`$_.Exception.Message)`" }"
            )
            $t = New-FakeRepoTree -Name 'plant-unscoped' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: old rule names' {
            $planted = $script:ShippedText.Replace(
                "try { Set-MiosLanFirewallRules } catch { Log-Warn `"Set-MiosLanFirewallRules: `$(`$_.Exception.Message)`" }",
                "try { Set-MiosLanFirewallRules; New-NetFirewallRule -DisplayName 'MiOS - forge (8400/tcp)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8400 -Profile Private -RemoteAddress LocalSubnet | Out-Null } catch { Log-Warn `"Set-MiosLanFirewallRules: `$(`$_.Exception.Message)`" }"
            )
            $t = New-FakeRepoTree -Name 'plant-old-names' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: loopback forward loop' {
            $planted = $script:ShippedText.Replace(
                "try { Set-MiosLanPortProxy }     catch { Log-Warn `"Set-MiosLanPortProxy: `$(`$_.Exception.Message)`" }",
                "try { Set-MiosLanPortProxy; foreach (`$p in (Get-MiosLanExposurePlan).Ports.Values) { & netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=`$p connectaddress=127.0.0.1 connectport=`$p | Out-Null } } catch { Log-Warn `"Set-MiosLanPortProxy: `$(`$_.Exception.Message)`" }"
            )
            $t = New-FakeRepoTree -Name 'plant-loopback' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: extra unexposed ports 8210/8460/8300' {
            $planted = $script:ShippedText.Replace(
                "try { Set-MiosLanPortProxy }     catch { Log-Warn `"Set-MiosLanPortProxy: `$(`$_.Exception.Message)`" }",
                "try { Set-MiosLanPortProxy; foreach (`$ep in @(8210, 8460, 8300)) { & netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=`$ep connectaddress=172.24.10.20 connectport=`$ep | Out-Null } } catch { Log-Warn `"Set-MiosLanPortProxy: `$(`$_.Exception.Message)`" }"
            )
            $t = New-FakeRepoTree -Name 'plant-extra-ports' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: shadowing unbounded Get-MiosVendorTomlValue' {
            $shadow = @'
function Get-MiosVendorTomlValue {
    param([string]$Section, [string]$Key, $Default)
    return (Get-MiosTomlValue -Section $Section -Key $Key -Default $Default)
}
'@
            $planted = $script:ShippedText.Replace(
                "Set-StrictMode -Version Latest`r`n`r`n# Console loggers",
                "Set-StrictMode -Version Latest`r`n$shadow`r`n# Console loggers"
            )
            if ($planted -eq $script:ShippedText) {
                $planted = $script:ShippedText.Replace(
                    "# Console loggers",
                    "$shadow`n# Console loggers"
                )
            }
            $operatorToml = @"
[firewall.windows]
expose = ["forge_http", "pgvector"]
profiles = ["Private"]
remote_addresses = ["LocalSubnet"]
"@
            $t = New-FakeRepoTree -Name 'plant-unbounded-shadow' -SetupContent $planted -UserToml $operatorToml
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            @($got.rules | Where-Object { $_.DisplayName -like 'MiOS - pgvector (*' }).Count | Should -Be 1
        }

        It 'catches planted defect: dropped retired-port cleanup' {
            $planted = $script:ShippedText.Replace(
                "& netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=`$(`$rp.Port) 2>`$null | Out-Null",
                "# dropped retired cleanup"
            )
            if ($planted -eq $script:ShippedText) {
                $planted = $script:ShippedText -replace '(?ms)foreach \(\$rp in \$retiredPorts\) \{.*?\}', '# dropped retired cleanup'
            }
            $seedRules = @($script:RetiredEntries | ForEach-Object {
                @{ DisplayName = "MiOS - $($_.Name) ($($_.Port)/tcp)"; LocalPort = $_.Port; Profile = 'Private'; RemoteAddress = @('LocalSubnet') }
            })
            $seedProxies = @($script:RetiredEntries | ForEach-Object {
                @{ ListenPort = $_.Port; ConnectAddress = '127.0.0.1'; ConnectPort = $_.Port }
            })
            $t = New-FakeRepoTree -Name 'plant-no-retired' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -Seed @{ Rules = $seedRules; Proxies = $seedProxies }
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }
    }
}
