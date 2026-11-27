# AI-hint: Pester unit tests for usr/libexec/mios/Setup-MiOSLanPortProxy.ps1: it runs build-mios.ps1 Phase 4's own Invoke-MiosLanExposure as the operator (drift first, then Phase 4's digest-checked UAC worker, never a relaunch of itself), confines the operator layer, cleans retired ports through a delete-only request, converges with Phase 4, and every planted defect is caught; all Windows/WSL side effects are stubbed in TestDrive.
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

    # A scratch tree shaped like the repo: the Setup under test, build-mios.ps1 at its root, the vendor SSOT, and
    # per-test operator layers.
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

    # The child harness. Phase 4 is modeled as build-mios.ps1 runs it: every top-level function defined, then
    # Invoke-MiosLanExposure under the token's elevation. -Phase4Admin and -Admin set the elevation Phase 4 and the
    # Setup under test see; -UacApprove runs an approved non-worker UAC request (Setup's retired-port deletion).
    $script:HarnessTemplate = @'
param(
    [string]$Mode,
    [string]$TargetScript,
    [string]$BuildScript,
    [string]$TreePath,
    [string]$HomeDir,
    [string]$AppDataDir,
    [string]$Admin,
    [string]$Phase4Admin,
    [string]$UacApprove,
    [string]$WslIp,
    [string]$SeedJson
)
$ErrorActionPreference = 'Stop'

$env:SystemDrive = $TreePath
$env:USERPROFILE = $HomeDir
$env:APPDATA = $AppDataDir
# Off Windows: one fixed stand-in, so the refresh task's command line (which names it) is the same in every tree.
if (-not $env:SystemRoot) { $env:SystemRoot = Join-Path ([IO.Path]::GetTempPath()) 'mios-lan-test-systemroot' }
if (-not $env:TEMP) { $env:TEMP = [IO.Path]::GetTempPath() }

Add-Type -TypeDefinition "
namespace MiosLanTestStub {
    public enum Role { Administrator }
    public sealed class Identity {
        public static object UserSid;
        public static Identity GetCurrent() { return new Identity(); }
        public object User { get { return UserSid; } }
    }
    public sealed class Principal {
        public static bool IsAdmin;
        public Principal(Identity identity) {}
        public bool IsInRole(Role role) { return IsAdmin; }
    }
}
"
# The token's own SID where Windows has one (pipe and file ACLs need a real one), a fixed account SID elsewhere.
[MiosLanTestStub.Identity]::UserSid = if ([Environment]::OSVersion.Platform -eq 'Win32NT') {
    [System.Security.Principal.WindowsIdentity]::GetCurrent().User
} else { [pscustomobject]@{ Value = 'S-1-5-21-1000000000-1000000000-1000000000-1001' } }
$acc = [psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators')
$acc::Add('Security.Principal.WindowsPrincipal', [MiosLanTestStub.Principal])
$acc::Add('Security.Principal.WindowsIdentity', [MiosLanTestStub.Identity])
$acc::Add('Security.Principal.WindowsBuiltInRole', [MiosLanTestStub.Role])

$global:FwRules = [ordered]@{}
$global:PortProxies = [ordered]@{}
$global:Tasks = [ordered]@{}
$global:StartProcessCalls = [System.Collections.Generic.List[object]]::new()
$global:WslCalls = [System.Collections.Generic.List[string]]::new()
$global:Events = [System.Collections.Generic.List[string]]::new()
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

# UAC: Phase 4's worker launch is recorded with the digest-checked file it would run, then declined (no elevated
# peer can answer its pipe here). Any other request runs here only when -UacApprove is set.
function global:Start-Process {
    param([string]$FilePath, [string[]]$ArgumentList, [string]$Verb, [switch]$Wait, [switch]$PassThru,
        [string]$WorkingDirectory, [string]$WindowStyle, $ErrorAction)
    $call = [ordered]@{ FilePath = $FilePath; ArgumentList = @($ArgumentList); Verb = $Verb; Decoded = ''; Worker = '' }
    $i = [array]::IndexOf(@($ArgumentList), '-EncodedCommand')
    if ($i -ge 0) { $call.Decoded = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String(@($ArgumentList)[$i + 1])) }
    $global:Events.Add("uac $FilePath")
    if ($call.Decoded -match '-OriginProcessId' -and $call.Decoded -match "ReadAllBytes\('([^']+)'\)") {
        $call.Worker = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Matches[1]))
        $global:StartProcessCalls.Add([pscustomobject]$call)
        throw 'The operation was canceled by the user.'
    }
    $global:StartProcessCalls.Add([pscustomobject]$call)
    if ($call.Decoded -and $UacApprove -eq '1') {
        & ([scriptblock]::Create($call.Decoded))
        return [pscustomobject]@{ ExitCode = 0 }
    }
    if ($call.Decoded) { throw 'The operation was canceled by the user.' }
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
    param([Parameter(ValueFromPipeline = $true)]$InputObject)
    process {
        if ($InputObject -and $global:FwRules.Contains([string]$InputObject.DisplayName)) {
            $r = $global:FwRules[[string]$InputObject.DisplayName]
            [pscustomobject]@{ RuleDisplayName = $r.DisplayName; Protocol = $r.Protocol; LocalPort = $r.LocalPort }
        }
    }
}

function global:Get-NetFirewallAddressFilter {
    param([Parameter(ValueFromPipeline = $true)]$InputObject)
    process {
        if ($InputObject -and $global:FwRules.Contains([string]$InputObject.DisplayName)) {
            [pscustomobject]@{ RemoteAddress = @($global:FwRules[[string]$InputObject.DisplayName].RemoteAddress) }
        }
    }
}

function global:Set-NetFirewallPortFilter {
    param([Parameter(ValueFromPipeline = $true)]$InputObject, [string]$Protocol, [int]$LocalPort)
    process {
        if ($InputObject -and $global:FwRules.Contains($InputObject.RuleDisplayName)) {
            $global:FwRules[$InputObject.RuleDisplayName].Protocol = $Protocol
            $global:FwRules[$InputObject.RuleDisplayName].LocalPort = $LocalPort
        }
    }
}

function global:New-ScheduledTaskAction { param($Execute, $Argument) [pscustomobject]@{ Execute = [string]$Execute; Arguments = [string]$Argument } }
function global:New-ScheduledTaskTrigger { param([switch]$Once, $At, $RepetitionInterval) [pscustomobject]@{ Once = [bool]$Once } }
function global:New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel) [pscustomobject]@{ UserId = [string]$UserId } }
function global:New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries, [switch]$StartWhenAvailable, $MultipleInstances, $ExecutionTimeLimit) [pscustomobject]@{} }
function global:New-ScheduledTask {
    param($Action, $Trigger, $Principal, $Settings, $Description)
    [pscustomobject]@{ Actions = @($Action); Principal = $Principal; SecurityDescriptor = ''; Description = [string]$Description }
}
function global:Register-ScheduledTask {
    param($TaskPath, $TaskName, $InputObject, [switch]$Force, $ErrorAction)
    $global:Tasks["$TaskPath$TaskName"] = $InputObject
    $InputObject
}
function global:Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) if ($global:Tasks.Contains("$TaskPath$TaskName")) { $global:Tasks["$TaskPath$TaskName"] } }
function global:Unregister-ScheduledTask { param($TaskPath, $TaskName, $Confirm, $ErrorAction) $global:Tasks.Remove("$TaskPath$TaskName") }

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
    $global:Events.Add("wsl $($a -join ' ')")
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

# Phase 4 as build-mios.ps1 holds it, for this harness's own runs and for the worker it would hand to UAC.
$phase4Source = ''; $phase4Top = @()
if (Microsoft.PowerShell.Management\Test-Path -LiteralPath $BuildScript) {
    $phase4Source = [IO.File]::ReadAllText($BuildScript)
    $tok = $null; $perr = $null
    $phase4Ast = [System.Management.Automation.Language.Parser]::ParseInput($phase4Source, [ref]$tok, [ref]$perr)
    if ($perr.Count) { throw 'build-mios.ps1 parse error' }
    $phase4Top = @($phase4Ast.EndBlock.Statements)
}
$phase4Defs = @($phase4Top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] } | ForEach-Object { $_.Extent.Text })
foreach ($v in @('$script:_MiosTomlCache', '$DevDistro')) {
    $phase4Defs += @($phase4Top | Where-Object { $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq $v } | ForEach-Object { $_.Extent.Text })
}
$runPhase4 = {
    Set-StrictMode -Off
    [MiosLanTestStub.Principal]::IsAdmin = ($Phase4Admin -ne '0')
    . ([scriptblock]::Create($phase4Defs -join "`n`n"))
    $script:MiosBuildSource = $phase4Source
    $script:IsAdmin = ($Phase4Admin -ne '0')
    function Write-Log([string]$M, [string]$L = 'INFO') {}
    function Log-Ok([string]$T) {}
    function Log-Info([string]$T) {}
    function Log-Warn([string]$T) { $global:Hosts.Add("phase4 warn: $T") }
    function Set-Step([string]$T) {}
    Invoke-MiosLanExposure
    $script:planOut = Get-MiosLanExposurePlan
}
$runSetup = {
    [MiosLanTestStub.Principal]::IsAdmin = ($Admin -eq '1')
    & $TargetScript
}

$errMsg = ''
$planOut = $null
try {
    switch ($Mode) {
        'setup'             { . $runSetup }
        'phase4'            { . $runPhase4 }
        'setup-then-phase4' { . $runSetup; . $runPhase4 }
        'phase4-then-setup' { . $runPhase4; $global:StartProcessCalls.Clear(); $global:Events.Clear(); $global:Hosts.Clear(); . $runSetup }
        default             { throw "unknown mode $Mode" }
    }
} catch {
    $errMsg = [string]$_.Exception.Message
}

# The worker Phase 4 would hand to UAC from this tree's build-mios.ps1, byte for byte (UTF-8 with BOM).
$workerBuilder = @($phase4Top | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq 'Get-MiosLanExposureWorkerScript' })
$expectedWorker = ''
if ($workerBuilder.Count -eq 1) {
    $expectedWorker = & {
        . ([scriptblock]::Create($workerBuilder[0].Extent.Text))
        $enc = New-Object Text.UTF8Encoding($true)
        [Convert]::ToBase64String([byte[]]($enc.GetPreamble() + $enc.GetBytes((Get-MiosLanExposureWorkerScript $phase4Source))))
    }
}

[ordered]@{
    err            = $errMsg
    rules          = @($global:FwRules.Values)
    proxies        = @($global:PortProxies.Values)
    tasks          = @($global:Tasks.Keys | ForEach-Object { $t = $global:Tasks[$_]; [ordered]@{ Name = $_; Execute = $t.Actions[0].Execute; Arguments = $t.Actions[0].Arguments; UserId = $t.Principal.UserId } })
    startProcess   = @($global:StartProcessCalls)
    wslCalls       = @($global:WslCalls)
    events         = @($global:Events)
    hosts          = @($global:Hosts)
    expectedWorker = $expectedWorker
    plan           = $planOut
} | ConvertTo-Json -Depth 8 -Compress
'@

    function script:Invoke-StubbedSetup {
        param(
            [Parameter(Mandatory)]$TreeObj,
            [string]$Mode = 'setup',
            [bool]$Admin = $true,
            [bool]$Phase4Admin = $true,
            [bool]$UacApprove = $false,
            [string]$WslIp = '172.24.10.20',
            [hashtable]$Seed = $null,
            [hashtable]$ExtraEnv = @{}
        )
        [IO.File]::WriteAllText($TreeObj.RunnerPath, $script:HarnessTemplate, [System.Text.UTF8Encoding]::new($false))
        $seedJson = if ($Seed) { $Seed | ConvertTo-Json -Depth 6 -Compress } else { '' }

        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        # The PowerShell running these tests runs the harness: pwsh.exe on Windows, pwsh on Linux (the CI
        # drift-gate). A hard-coded 'pwsh.exe' only resolves on Windows.
        $psi.FileName = [System.Environment]::ProcessPath
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
            '-Phase4Admin', $(if ($Phase4Admin) { '1' } else { '0' }),
            '-UacApprove', $(if ($UacApprove) { '1' } else { '0' }),
            '-WslIp', $WslIp,
            '-SeedJson', $seedJson
        )
        foreach ($a in $argList) { $null = $psi.ArgumentList.Add($a) }

        $proc = [System.Diagnostics.Process]::Start($psi)
        $proc.StandardInput.Close()
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit(90000)) {
            try { $proc.Kill($true) } catch { $proc.Kill() }
            throw "Child pwsh timed out after 90s (e.g. blocked on ReadKey or a UAC pipe)."
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

    # Phase 4's elevated step also keeps the SYSTEM refresh task; the same task, byte for byte, as Phase 4 registers.
    function script:Assert-RefreshTaskAsPhase4 {
        param($Got, $Phase4)
        @($Got.tasks).Count | Should -Be 1
        ($Got.tasks | ConvertTo-Json -Depth 4 -Compress) | Should -Be ($Phase4.tasks | ConvertTo-Json -Depth 4 -Compress)
        $Got.tasks[0].UserId | Should -Be 'S-1-5-18'
    }

    # As the operator with the host already matching Phase 4: no UAC request at all, and nothing changed.
    function script:Assert-NoUacAndUnchanged {
        param($Got, $Phase4)
        $Got.err | Should -Be ''
        @($Got.startProcess).Count | Should -Be 0
        ($Got.rules | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($Phase4.rules | ConvertTo-Json -Depth 5 -Compress)
        ($Got.proxies | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($Phase4.proxies | ConvertTo-Json -Depth 5 -Compress)
        ($Got.tasks | ConvertTo-Json -Depth 4 -Compress) | Should -Be ($Phase4.tasks | ConvertTo-Json -Depth 4 -Compress)
    }
}

Describe 'Setup-MiOSLanPortProxy.ps1 runs build-mios.ps1 Phase 4' {
    BeforeAll {
        $script:BaselineTree = New-FakeRepoTree -Name 'baseline'
        $script:Phase4Baseline = Invoke-StubbedSetup -TreeObj $script:BaselineTree -Mode 'phase4'
        $script:Phase4Baseline.err | Should -Be ''
        $script:ExpectedPlan = $script:Phase4Baseline.plan
        $script:RetiredEntries = Get-ScriptRetiredPorts -ScriptPath $script:DefaultSetupScript
        $script:RetiredPortNums = @($script:RetiredEntries | ForEach-Object { [int]$_.Port })
    }

    It 'elevated, applies exactly the Phase 4 plan in place: rules, VM eth0 forwards and the refresh task' {
        $t = New-FakeRepoTree -Name 'happy'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -WslIp '172.24.10.20'
        Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -ExpectedWslIp '172.24.10.20' -RetiredPortNums $script:RetiredPortNums
        Assert-RefreshTaskAsPhase4 -Got $got -Phase4 $script:Phase4Baseline
        @($got.startProcess).Count | Should -Be 0
    }

    It 'converges both ways with build-mios.ps1 Phase 4' {
        $t1 = New-FakeRepoTree -Name 'conv-setup-vs-p4'
        $gotSetup = Invoke-StubbedSetup -TreeObj $t1 -Mode 'setup'
        $gotSetup.err | Should -Be ''
        ($gotSetup.rules | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($script:Phase4Baseline.rules | ConvertTo-Json -Depth 5 -Compress)
        ($gotSetup.proxies | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($script:Phase4Baseline.proxies | ConvertTo-Json -Depth 5 -Compress)

        $t2 = New-FakeRepoTree -Name 'conv-s-then-p4'
        $sThenP4 = Invoke-StubbedSetup -TreeObj $t2 -Mode 'setup-then-phase4'
        $t3 = New-FakeRepoTree -Name 'conv-p4-then-s'
        $p4ThenS = Invoke-StubbedSetup -TreeObj $t3 -Mode 'phase4-then-setup'
        $sThenP4.err | Should -Be ''
        $p4ThenS.err | Should -Be ''
        foreach ($k in 'rules', 'proxies', 'tasks') {
            ($sThenP4.$k | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($p4ThenS.$k | ConvertTo-Json -Depth 5 -Compress)
            ($sThenP4.$k | ConvertTo-Json -Depth 5 -Compress) | Should -Be ($script:Phase4Baseline.$k | ConvertTo-Json -Depth 5 -Compress)
        }
    }

    It 'as the operator on a host that already matches Phase 4, asks for no UAC and changes nothing' {
        $t = New-FakeRepoTree -Name 'operator-no-drift'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'phase4-then-setup' -Admin $false
        Assert-NoUacAndUnchanged -Got $got -Phase4 $script:Phase4Baseline
        @($got.hosts | Where-Object { $_ -match 'already match mios\.toml \[firewall\.windows\]; no elevation needed' }).Count | Should -Be 1
    }

    It 'as the operator, hands UAC only Phase 4''s own digest-checked worker, after reading the VM address itself' -Skip:([Environment]::OSVersion.Platform -ne 'Win32NT') {
        $t = New-FakeRepoTree -Name 'operator-handoff'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup' -Admin $false
        $got.err | Should -Be ''
        @($got.startProcess).Count | Should -Be 1
        $req = $got.startProcess[0]
        $req.FilePath | Should -BeLike '*\System32\WindowsPowerShell\v1.0\powershell.exe'
        $req.Verb | Should -Be 'RunAs'
        @($req.ArgumentList)[0..1] | Should -Be @('-NoProfile', '-EncodedCommand')
        @($req.ArgumentList) | Should -Not -Contain $t.SetupPath
        # The worker UAC would run is the one build-mios.ps1 compiles, and the launch checks exactly those bytes.
        $req.Worker | Should -Not -BeNullOrEmpty
        $req.Worker | Should -Be $got.expectedWorker
        $bytes = [Convert]::FromBase64String($req.Worker)
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '') } finally { $sha.Dispose() }
        $req.Decoded | Should -Match ([regex]::Escape("-cne '$digest'"))
        # The VM address is read with the operator's token before UAC, and nothing was written without elevation.
        $probe = [array]::IndexOf(@($got.events), @($got.events | Where-Object { $_ -match '^wsl .*ip -4 -o addr show eth0' })[0])
        $uac = [array]::IndexOf(@($got.events), @($got.events | Where-Object { $_ -like 'uac *' })[0])
        $probe | Should -BeGreaterOrEqual 0
        $probe | Should -BeLessThan $uac
        @($got.rules).Count | Should -Be 0
        @($got.proxies).Count | Should -Be 0
        @($got.tasks).Count | Should -Be 0
        @($got.hosts | Where-Object { $_ -match 'LAN-EXPOSURE-ELEVATION-DECLINED' }).Count | Should -Be 1
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

    It 'elevated, removes retired-port forwards and rules extracted from $retiredPorts AST' {
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
        @($got.startProcess).Count | Should -Be 0
    }

    It 'as the operator, deletes retired-port leftovers through one UAC request that only deletes them' {
        $seedProxies = @($script:RetiredEntries | ForEach-Object {
            @{ ListenPort = $_.Port; ConnectAddress = '127.0.0.1'; ConnectPort = $_.Port }
        })
        $t = New-FakeRepoTree -Name 'operator-retired'
        $got = Invoke-StubbedSetup -TreeObj $t -Mode 'phase4-then-setup' -Admin $false -UacApprove $true -Seed @{ Proxies = $seedProxies }
        $got.err | Should -Be ''
        @($got.startProcess).Count | Should -Be 1
        $req = $got.startProcess[0]
        $req.Verb | Should -Be 'RunAs'
        $req.FilePath | Should -BeLike '*System32?WindowsPowerShell?v1.0?powershell.exe'
        # Phase 4 already removed any retired port that is still a MiOS [ports] value; the request deletes the rest.
        $lines = @($req.Decoded -split "`r?`n" | Where-Object { $_.Trim() })
        $lines.Count | Should -BeGreaterThan 0
        foreach ($l in $lines) { $l | Should -Match '^netsh\.exe interface portproxy delete v4tov4 listenaddress=0\.0\.0\.0 listenport=\d+ \| Out-Null$' }
        $deleted = @($lines | ForEach-Object { [int]([regex]::Match($_, 'listenport=(\d+)').Groups[1].Value) })
        @($deleted | Sort-Object -Unique).Count | Should -Be $deleted.Count
        foreach ($d in $deleted) { $script:RetiredPortNums | Should -Contain $d }
        Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums
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
        @($got.startProcess).Count | Should -Be 0
    }

    Context 'Planted-defect controls' {
        BeforeAll {
            $script:ShippedText = [IO.File]::ReadAllText($script:TargetSetupScript)
            $script:Phase4Call = "try { Invoke-MiosLanExposure } catch { Log-Warn `"Windows host exposure: `$(`$_.Exception.Message)`" }"
            # One plant, applied exactly once: a plant that does not apply is an error, never a vacuous pass.
            function script:Get-Planted([string]$From, [string]$To) {
                $n = ([regex]::Matches($script:ShippedText, [regex]::Escape($From))).Count
                if ($n -ne 1) { throw "Plant anchor must match the Setup under test exactly once (matched $n): $From" }
                return $script:ShippedText.Replace($From, $To)
            }
        }

        It 'catches planted defect: unscoped own rule loop' {
            $planted = Get-Planted $script:Phase4Call ("try { Invoke-MiosLanExposure; New-NetFirewallRule -DisplayName 'MiOS - unscoped (8499/tcp)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8499 -Profile Any -RemoteAddress Any | Out-Null } catch { Log-Warn `"Windows host exposure: `$(`$_.Exception.Message)`" }")
            $t = New-FakeRepoTree -Name 'plant-unscoped' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: old rule names' {
            $planted = Get-Planted $script:Phase4Call ("try { Invoke-MiosLanExposure; New-NetFirewallRule -DisplayName 'MiOS - forge (8400/tcp)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8400 -Profile Private -RemoteAddress LocalSubnet | Out-Null } catch { Log-Warn `"Windows host exposure: `$(`$_.Exception.Message)`" }")
            $t = New-FakeRepoTree -Name 'plant-old-names' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: loopback forward loop' {
            $planted = Get-Planted $script:Phase4Call ("try { Invoke-MiosLanExposure; foreach (`$p in (Get-MiosLanExposurePlan).Ports.Values) { & netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=`$p connectaddress=127.0.0.1 connectport=`$p | Out-Null } } catch { Log-Warn `"Windows host exposure: `$(`$_.Exception.Message)`" }")
            $t = New-FakeRepoTree -Name 'plant-loopback' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: extra unexposed ports 8210/8460/8300' {
            $planted = Get-Planted $script:Phase4Call ("try { Invoke-MiosLanExposure; foreach (`$ep in @(8210, 8460, 8300)) { & netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=`$ep connectaddress=172.24.10.20 connectport=`$ep | Out-Null } } catch { Log-Warn `"Windows host exposure: `$(`$_.Exception.Message)`" }")
            $t = New-FakeRepoTree -Name 'plant-extra-ports' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-SetupConvergedClean -Got $got -ExpectedPlan $script:ExpectedPlan -RetiredPortNums $script:RetiredPortNums } | Should -Throw
        }

        It 'catches planted defect: shadowing unbounded Get-MiosVendorTomlValue' {
            $shadow = "function Get-MiosVendorTomlValue { param([string]`$Section, [string]`$Key, `$Default) return (Get-MiosTomlValue -Section `$Section -Key `$Key -Default `$Default) }`n"
            $planted = Get-Planted '# Console loggers' ($shadow + '# Console loggers')
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
            $planted = Get-Planted 'if ($cleanup) {' 'if ($false) {'
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

        It 'catches planted defect: self-elevating relaunch before Phase 4 (the operator token is lost)' {
            $from = '$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)'
            $planted = Get-Planted $from ($from + "`nif (-not `$isAdmin) { Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',`$PSCommandPath) -Verb RunAs; return }")
            $t = New-FakeRepoTree -Name 'plant-relaunch' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'phase4-then-setup' -Admin $false
            { Assert-NoUacAndUnchanged -Got $got -Phase4 $script:Phase4Baseline } | Should -Throw
        }

        It 'catches planted defect: the inner steps instead of Phase 4''s entry point (no drift check, no refresh task)' {
            $planted = Get-Planted $script:Phase4Call "try { Set-MiosLanFirewallRules; Set-MiosLanPortProxy } catch { Log-Warn `"Windows host exposure: `$(`$_.Exception.Message)`" }"
            $t = New-FakeRepoTree -Name 'plant-inner-steps' -SetupContent $planted
            $got = Invoke-StubbedSetup -TreeObj $t -Mode 'setup'
            { Assert-RefreshTaskAsPhase4 -Got $got -Phase4 $script:Phase4Baseline } | Should -Throw
        }
    }
}
