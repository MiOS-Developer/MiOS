# AI-hint: Execute the client setup's -Scope User|Machine split: User writes only its own variables and profile, never requests elevation, and defers every all-users action; Machine writes only changed values.
# AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, tests/powershell/NativeProfile.Tests.ps1
BeforeAll {
    $source = if ($env:MIOS_TEST_CLIENT_SETUP) { $env:MIOS_TEST_CLIENT_SETUP } else { Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-client-setup.ps1' }
    $script:setupText = [IO.File]::ReadAllText((Resolve-Path $source)) -replace "`r`n", "`n"
    $tokens = $null; $errors = $null
    $script:setupAst = [Management.Automation.Language.Parser]::ParseInput($script:setupText, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Invalid production client setup source' }
    foreach ($name in @('Assert-MiosScopeArguments', 'Test-MiosMachineAction', 'Resolve-MiosNativeProfilePath', 'Set-MiosNativeEnvironment')) {
        $function = $script:setupAst.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }.GetNewClosure(), $true)
        if (-not $function) { throw "Production function missing: $name" }
        . ([scriptblock]::Create($function.Extent.Text))
    }
    # An in-memory registry stands in for the User and Machine environment targets.
    $script:store = @{}; $script:writes = New-Object System.Collections.Generic.List[string]
    function Get-MiosScopedVariable([string]$Name, [string]$Target) { return $script:store["$Target/$Name"] }
    function Set-MiosScopedVariable([string]$Name, [string]$Value, [string]$Target) { $script:store["$Target/$Name"] = $Value; $script:writes.Add("$Target/$Name") }
    # For each matching command, the conditions of every if-clause that encloses it, joined; '' when it runs unguarded.
    function Get-MiosGuards([string]$Pattern) {
        $commands = @($script:setupAst.FindAll({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.Extent.Text -match $Pattern }.GetNewClosure(), $true))
        foreach ($command in $commands) {
            $conditions = @()
            $node = $command
            while ($node.Parent) {
                $parent = $node.Parent
                if ($parent -is [Management.Automation.Language.IfStatementAst]) {
                    foreach ($clause in $parent.Clauses) { if ($clause.Item2.Extent.StartOffset -le $node.Extent.StartOffset -and $node.Extent.EndOffset -le $clause.Item2.Extent.EndOffset) { $conditions += $clause.Item1.Extent.Text } }
                }
                $node = $parent
            }
            ($conditions -join ' | ')
        }
    }
}

Describe 'Native client setup scopes' {
    BeforeEach { $script:store = @{}; $script:writes = New-Object System.Collections.Generic.List[string]; $env:MIOS_NATIVE_BIN = $null }

    It 'User scope requires its own bin directory and profile body, and Machine keeps its defaults' {
        { Assert-MiosScopeArguments 'User' $false 'C:\Users\op\AppData\Local\MiOS\powershell\profile.ps1' } | Should -Throw '*requires -BinDirectory*'
        { Assert-MiosScopeArguments 'User' $true '' } | Should -Throw '*requires -ProfilePath*'
        { Assert-MiosScopeArguments 'User' $true 'C:\Users\op\AppData\Local\MiOS\powershell\profile.ps1' } | Should -Not -Throw
        { Assert-MiosScopeArguments 'Machine' $false '' } | Should -Not -Throw
    }

    It 'User scope writes only User PATH and MIOS_NATIVE_BIN, each only when it changes' {
        $bin = 'C:\Users\op\AppData\Local\MiOS\bin'
        $script:store['User/Path'] = 'C:\tools'
        Set-MiosNativeEnvironment 'User' $bin
        $script:store['User/Path'] | Should -BeExactly "C:\tools;$bin"
        $script:store['User/MIOS_NATIVE_BIN'] | Should -BeExactly $bin
        @($script:writes | Where-Object { $_ -like 'Machine/*' }).Count | Should -Be 0
        $script:writes.Clear()
        Set-MiosNativeEnvironment 'User' $bin
        $script:writes.Count | Should -Be 0
        $env:MIOS_NATIVE_BIN | Should -BeExactly $bin
    }

    It 'Machine scope writes Machine PATH and MIOS_NATIVE_BIN only when they change' {
        $bin = 'C:\ProgramData\MiOS\bin'
        $script:store['Machine/Path'] = 'C:\Windows\system32'
        Set-MiosNativeEnvironment 'Machine' $bin
        ($script:writes -join ',') | Should -BeExactly 'Machine/Path,Machine/MIOS_NATIVE_BIN'
        $script:store['Machine/Path'] | Should -BeExactly "C:\Windows\system32;$bin"
        $script:writes.Clear()
        Set-MiosNativeEnvironment 'Machine' $bin
        $script:writes.Count | Should -Be 0
    }

    It 'User scope defers each machine-only action with one line, and stays quiet during runtime projections' {
        $RuntimeOnly = $false
        Test-MiosMachineAction 'Machine' 'x' | Should -BeTrue
        $output = @(& { Test-MiosMachineAction 'User' 'OpenSSH.Client Windows capability' } 6>&1)
        (@($output | Where-Object { $_ -is [bool] }) -join ',') | Should -BeExactly 'False'
        (@($output | Where-Object { $_ -isnot [bool] } | ForEach-Object { "$_" }) -join '|') | Should -BeExactly 'deferred: needs an elevated machine stage: OpenSSH.Client Windows capability'
        $RuntimeOnly = $true
        $quiet = & { Test-MiosMachineAction 'User' 'x' } 6>&1
        @($quiet | Where-Object { $_ -isnot [bool] }).Count | Should -Be 0
    }

    It 'User scope hooks the layout''s profile body, never the running engine''s profile' {
        $body = Join-Path $TestDrive 'MiOS\powershell\profile.ps1'
        Resolve-MiosNativeProfilePath 'User' $body | Should -BeExactly $body
        Resolve-MiosNativeProfilePath 'Machine' $body | Should -Not -BeExactly $body
    }

    It 'every machine-only action is guarded by the scope, and nothing requests elevation' {
        foreach ($pattern in @('^Add-WindowsCapability', '--scope machine', 'autorun install --allusers', "mios-oscontrol-server\.ps1'\) -Install", "mios-agent-cli-setup\.ps1'\) -Distro")) {
            $guards = @(Get-MiosGuards $pattern)
            $guards.Count | Should -BeGreaterThan 0
            foreach ($guard in $guards) { $guard | Should -Match 'Test-MiosMachineAction \$Scope' }
        }
        $script:setupText | Should -Match "(?s)if \(Test-MiosMachineAction \`$Scope '[^']*HKEY_USERS[^']*'\) \{\n\s*\`$consoleTargets \+= @\('Registry::HKEY_USERS"
        $script:setupText | Should -Match "(?s)if \(-not \(Test-MiosMachineAction \`$Scope '[^']*shortcuts'\)\) \{\n\s*\`$desktopRoots = @\(\[Environment\]::GetFolderPath\('Desktop'\)\)"
        $script:setupText | Should -Not -Match '-Verb\s+RunAs'
        @([regex]::Matches($script:setupText, '\[Environment\]::SetEnvironmentVariable')).Count | Should -Be 1
        @([regex]::Matches($script:setupText, '\$PROFILE\.CurrentUserAllHosts')).Count | Should -Be 1
        @([regex]::Matches($script:setupText, 'Set-MiosNativeProfile \$(existingProfile|profilePath) \$BinDirectory')).Count | Should -Be 2
        @([regex]::Matches($script:setupText, '= Resolve-MiosNativeProfilePath \$Scope \$ProfilePath')).Count | Should -Be 2
    }

    It 'the install records its scope, and every runtime projection passes it back' {
        $script:setupText | Should -Match 'scope=\$Scope; profilePath=\$ProfilePath\}'
        $script:setupText | Should -Match "if \(\`$binding\.scope -eq 'User'\) \{ \`$scopeArgs = @\{ Scope = 'User'; ProfilePath = \[string\]\`$binding\.profilePath \} \}"
        @([regex]::Matches($script:setupText, "mios-native-client-setup\.ps1'\) -(RepairRuntime|RuntimeOnly) -BinDirectory \`$PSScriptRoot @scopeArgs")).Count | Should -Be 2
    }
}
Describe 'Native terminal projection process status' {
    BeforeAll {
# Only extracted staging code runs. Files and processes are in-memory fixtures;
# a legacy direct invocation targets a nonexistent file beneath the scratch root.
function Invoke-MiosTerminalProjectionProbe {
    param([string]$Body, [string]$FixtureBin, [int]$NativeCode, [int]$StaleCode,
          [bool]$FunctionMode = $true, [bool]$Present = $true)
    $calls = New-Object System.Collections.Generic.List[object]
    function Get-MiosInstalledLayout { [pscustomobject]@{ NativeBin = $FixtureBin } }
    function Test-Path {
        param([string]$LiteralPath, [string]$PathType)
        return $Present -and ($LiteralPath -in @((Join-Path $FixtureBin 'mios-launch.exe'), (Join-Path $FixtureBin 'native-binding.json')))
    }
    function Start-Process {
        param([string]$FilePath, [string[]]$ArgumentList, [switch]$NoNewWindow, [switch]$Wait, [switch]$PassThru)
        $calls.Add([pscustomobject]@{ FilePath=$FilePath; Arguments=@($ArgumentList); Wait=[bool]$Wait; PassThru=[bool]$PassThru; NoNewWindow=[bool]$NoNewWindow })
        if ($PassThru) { [pscustomobject]@{ ExitCode = $NativeCode } }
    }
    $savedBin = $env:MIOS_NATIVE_BIN
    # The machine and per-user install roots are fixture paths too, so the probe
    # never depends on the host's (or a non-Windows engine's absent) profile roots.
    $savedProgramData = $env:ProgramData; $savedLocalAppData = $env:LOCALAPPDATA
    $fixtureRoot = Split-Path -Parent $FixtureBin
    $savedExit = Get-Variable LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
    $hadExit = $null -ne $savedExit; $savedExitValue = if ($hadExit) { $savedExit.Value } else { $null }
    $env:MIOS_NATIVE_BIN = $FixtureBin; $global:LASTEXITCODE = $StaleCode
    $env:ProgramData = Join-Path $fixtureRoot 'fixture-programdata'; $env:LOCALAPPDATA = Join-Path $fixtureRoot 'fixture-localappdata'
    $BinDirectory = $FixtureBin; $accepted = $false; $message = $null; $value = $null
    try {
        if ($FunctionMode) { . ([scriptblock]::Create($Body)); $value = Install-MiOSTmuxProfile -Required }
        else { $value = . ([scriptblock]::Create($Body)) }
        $accepted = $true
    } catch { $message = $_.Exception.Message }
    finally {
        $env:MIOS_NATIVE_BIN = $savedBin; $env:ProgramData = $savedProgramData; $env:LOCALAPPDATA = $savedLocalAppData
        if ($hadExit) { $global:LASTEXITCODE = $savedExitValue } else { Remove-Variable LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue }
    }
    [pscustomobject]@{ Accepted=$accepted; Message=$message; Calls=@($calls.ToArray()); Value=$value }
}
        function Get-MiosTerminalProjectionCode([string]$Surface) {
            if ($Surface -eq 'client setup') {
                $nodes = @($script:setupAst.EndBlock.Statements)
                $sites = @($nodes | Where-Object { $_ -isnot [Management.Automation.Language.FunctionDefinitionAst] -and $_.Extent.Text.Contains('--stage-host-terminal') })
                if ($sites.Count -ne 1) { throw 'Expected exactly one production client staging site' }
                $index = [array]::IndexOf($nodes, $sites[0])
                if ($nodes[$index + 1] -isnot [Management.Automation.Language.IfStatementAst]) { throw 'Production staging exit guard missing' }
                return ($sites[0].Extent.Text + "`n" + $nodes[$index + 1].Extent.Text)
            }
            $path = Join-Path (Join-Path $PSScriptRoot '../..') $Surface
            $tokens = $null; $errors = $null
            $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
            if ($errors.Count) { throw 'Invalid production installer source' }
            $fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Install-MiOSTmuxProfile' }, $true)
            if (-not $fn) { throw 'Production tmux staging function missing' }
            return $fn.Extent.Text
        }
    }
    It '<Surface> waits for its own GUI process and ignores a stale native failure' -ForEach @(
        @{ Surface='Get-MiOS.ps1'; FunctionMode=$true }, @{ Surface='build-mios.ps1'; FunctionMode=$true }, @{ Surface='client setup'; FunctionMode=$false }
    ) {
        $bin = Join-Path $TestDrive 'projection-bin'
        $r = Invoke-MiosTerminalProjectionProbe -Body (Get-MiosTerminalProjectionCode $Surface) -FixtureBin $bin -NativeCode 0 -StaleCode -1978335189 -FunctionMode $FunctionMode
        $r.Accepted | Should -BeTrue
        $r.Calls.Count | Should -Be 1
        $r.Calls[0].FilePath | Should -BeExactly (Join-Path $bin 'mios-launch.exe')
        ($r.Calls[0].Arguments -join '|') | Should -BeExactly '--stage-host-terminal'
        $r.Calls[0].Wait | Should -BeTrue
        $r.Calls[0].PassThru | Should -BeTrue
        $r.Calls[0].NoNewWindow | Should -BeTrue
    }
    It '<Surface> propagates a real GUI failure even when LASTEXITCODE is zero' -ForEach @(
        @{ Surface='Get-MiOS.ps1'; FunctionMode=$true }, @{ Surface='build-mios.ps1'; FunctionMode=$true }, @{ Surface='client setup'; FunctionMode=$false }
    ) {
        $r = Invoke-MiosTerminalProjectionProbe -Body (Get-MiosTerminalProjectionCode $Surface) -FixtureBin (Join-Path $TestDrive 'projection-bin') -NativeCode 7 -StaleCode 0 -FunctionMode $FunctionMode
        $r.Accepted | Should -BeFalse
        $r.Message | Should -Match '\(exit 7\)'
        $r.Calls.Count | Should -Be 1
        $r.Calls[0].Wait | Should -BeTrue
    }
    It '<Surface> still refuses a missing required native renderer without starting a process' -ForEach @(
        @{ Surface='Get-MiOS.ps1' }, @{ Surface='build-mios.ps1' }
    ) {
        $r = Invoke-MiosTerminalProjectionProbe -Body (Get-MiosTerminalProjectionCode $Surface) -FixtureBin (Join-Path $TestDrive 'projection-bin') -NativeCode 0 -StaleCode 0 -Present $false
        $r.Accepted | Should -BeFalse
        $r.Message | Should -Match 'missing.*incomplete'
        $r.Calls.Count | Should -Be 0
    }
}

Describe 'Native wallpaper installer scopes' {
    BeforeAll {
        # Extract the production wallpaper stage, not a duplicate implementation.
        function Get-MiosWallpaperStageCode([string]$Text) {
            $m = [regex]::Matches($Text, '(?ms)^    # -- mios-wallpaperd[^\n]*\n(?<Body>.*?)^\s*# Register MiOS-Autostart ')
            if ($m.Count -ne 1) { throw 'Missing or ambiguous production wallpaper stage' }
            return $m[0].Groups['Body'].Value
        }
        # No real service, artifact, process, WSL, registry or filesystem operations run.
        function Invoke-MiosWallpaperScopeProbe {
            param([string]$Body, [string]$FixtureRoot, [bool]$Elevated, [bool]$Exists = $true,
                  [int]$CreateExit = 0, [switch]$ArtifactFail, [switch]$StartFail)
            $state = @{ Artifact=0; ServiceArgument=''; Binary=''; Destination=''; Queries=0; Created=0; Started=0; Warnings=@(); Error=$null }
            $adminVar = Get-Variable -Name IsAdmin -Scope Script -ErrorAction SilentlyContinue
            $hadAdmin = $null -ne $adminVar; $oldAdmin = if ($hadAdmin) { $adminVar.Value } else { $null }
            $lastVar = Get-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
            $hadLast = $null -ne $lastVar; $oldLast = if ($hadLast) { $lastVar.Value } else { $null }
            $MiosBinDir = Join-Path $FixtureRoot 'User install bin'
            $MiosRepoDir = Join-Path $FixtureRoot 'system checkout'
            $DevDistro = 'fixture-MiOS-DEV'
            function Install-MiosNativeWindowsArtifact {
                param($Root, $Machine, $Binary, $Destination, $ServiceName)
                $state.Artifact++; $state.ServiceArgument=$ServiceName; $state.Binary=$Binary; $state.Destination=$Destination
                if ($ArtifactFail) { throw 'fixture native artifact failure' }
                if ($ServiceName -and -not $Elevated) { throw 'fixture machine-service stop denied' }
                return $Destination
            }
            function Get-Service {
                [CmdletBinding()] param([string]$Name)
                $state.Queries++
                if (-not $Elevated) { throw 'fixture User scope queried a machine service' }
                if ($Name -ne 'MiOS-Wallpaper-Service') { throw 'fixture wrong service queried' }
                if ($Exists) { return [pscustomobject]@{ Status='Running' } }
            }
            function Start-Service {
                [CmdletBinding()] param([string]$Name)
                $state.Started++
                if (-not $Elevated -or $Name -ne 'MiOS-Wallpaper-Service') { throw 'fixture User scope started a machine service' }
                if ($StartFail) { throw 'fixture service start failure' }
            }
            function sc.exe {
                param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
                $state.Created++
                if (-not $Elevated -or $Arguments[0] -ne 'create' -or $Arguments[1] -ne 'MiOS-Wallpaper-Service') { throw 'fixture invalid machine service registration' }
                if (($Arguments -join '|') -notlike ('*'+$state.Destination+'*')) { throw 'fixture unverified service destination' }
                $global:LASTEXITCODE=$CreateExit
            }
            function Log-Ok { param($Message) }
            function Log-Warn { param($Message) $state.Warnings += $Message }
            try { $script:IsAdmin=$Elevated; $global:LASTEXITCODE=0; & ([scriptblock]::Create($Body)) }
            catch { $state.Error=$_.Exception.Message }
            finally {
                if ($hadAdmin) { Set-Variable -Name IsAdmin -Scope Script -Value $oldAdmin } else { Remove-Variable -Name IsAdmin -Scope Script -ErrorAction SilentlyContinue }
                if ($hadLast) { Set-Variable -Name LASTEXITCODE -Scope Global -Value $oldLast } else { Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue }
            }
            return [pscustomobject]$state
        }
        $text = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '../../build-mios.ps1')).Replace("`r`n", "`n")
        $script:wallpaperStageCode = Get-MiosWallpaperStageCode $text
    }
    It 'stages the verified User binary without stopping, querying, creating or starting the machine service' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $false
        $r.Error | Should -BeNullOrEmpty
        $r.Artifact | Should -Be 1
        $r.ServiceArgument | Should -BeNullOrEmpty
        $r.Binary | Should -Be 'mios-wallpaperd'
        $r.Destination | Should -Be (Join-Path (Join-Path $TestDrive 'User install bin') 'mios-wallpaperd.exe')
        ($r.Queries + $r.Created + $r.Started) | Should -Be 0
        ($r.Warnings -join ' ') | Should -Match 'User scope:.*service setup deferred.*existing machine service unchanged'
    }
    It 'does not swallow a User artifact build or verification failure' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $false -ArtifactFail
        $r.Error | Should -Be 'fixture native artifact failure'
        ($r.Queries + $r.Created + $r.Started) | Should -Be 0
    }
    It 'retains the named Machine service handoff and starts an existing service' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $true
        $r.Error | Should -BeNullOrEmpty
        $r.ServiceArgument | Should -Be 'MiOS-Wallpaper-Service'
        $r.Queries | Should -Be 1
        $r.Created | Should -Be 0
        $r.Started | Should -Be 1
    }
    It 'registers a missing Machine service only after its artifact was verified' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $true -Exists $false
        $r.Error | Should -BeNullOrEmpty
        $r.Artifact | Should -Be 1
        $r.Created | Should -Be 1
        $r.Started | Should -Be 1
    }
    It 'propagates a Machine service registration failure without starting it' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $true -Exists $false -CreateExit 5
        $r.Error | Should -Be 'Could not register the verified MiOS wallpaper service'
        $r.Started | Should -Be 0
    }
    It 'propagates a Machine service start failure' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $true -StartFail
        $r.Error | Should -Be 'fixture service start failure'
    }
    It 'propagates a Machine artifact or stop failure without registering or starting a service' {
        $r = Invoke-MiosWallpaperScopeProbe -Body $script:wallpaperStageCode -FixtureRoot $TestDrive -Elevated $true -ArtifactFail
        $r.Error | Should -Be 'fixture native artifact failure'
        ($r.Queries + $r.Created + $r.Started) | Should -Be 0
    }
}
