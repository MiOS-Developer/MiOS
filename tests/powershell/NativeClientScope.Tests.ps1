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
