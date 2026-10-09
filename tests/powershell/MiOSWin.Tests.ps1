# AI-hint: Pester characterization tests for MiOS.Win.psm1 Windows helper module.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$winModulePath = Join-Path $scriptDir '../../automation/lib/MiOS.Win.psm1'

Describe "MiOS.Win.psm1 Module" {
    BeforeAll {
        # Pester 5 runs BeforeAll in a different scope from the
        # discovery-phase top level, so recompute paths here.
        $winModulePath = Join-Path $PSScriptRoot '../../automation/lib/MiOS.Win.psm1'
        Import-Module $winModulePath -Force -Global
    }

    $isWin = if (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue) { $IsWindows } else { $true }
    # Resolving a Windows interpreter is meaningless on Linux, where CI runs
    # pwsh -- there is no pwsh.exe and no %WINDIR%. Assert the real contract on
    # Windows; on Linux only assert the function does not throw.
    It "Should resolve concrete interpreter path avoiding WindowsApps alias" -Skip:(-not $isWin) {
        $exe = Get-MiosPowerShellExe
        $exe | Should -Not -BeNullOrEmpty
        ($exe -like '*\WindowsApps\*') | Should -Be $false
    }

    It "Should not throw when no Windows interpreter exists" {
        { Get-MiosPowerShellExe } | Should -Not -Throw
    }
}

Describe 'MiOS mirrored-network route detection' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-client-setup.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
        $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-MiosGuestDefaultRoute' }, $true)
        . ([scriptblock]::Create($functionAst.Extent.Text))
    }
    It 'detects an empty JSON array as no route' {
        Test-MiosGuestDefaultRoute '[]' | Should -Be $false
    }
    It 'detects a real route' {
        Test-MiosGuestDefaultRoute '[{"dst":"default","gateway":"192.0.2.1","dev":"eth0"}]' | Should -Be $true
    }
    It 'rejects malformed route output' {
        { Test-MiosGuestDefaultRoute 'DEVLOOP-PLANTED-INVALID-JSON' } | Should -Throw
    }
}

Describe 'MiOS global Terminal transparency projection' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-client-setup.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
        $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Set-MiosTerminalTransparency' }, $true)
        . ([scriptblock]::Create($functionAst.Extent.Text))
    }
    It 'replaces opaque profile overrides and retains unrelated appearance fields' {
        $appearance = @{ opacity=100; useAcrylic=$false; commandline='cmd.exe'; unfocusedAppearance=@{opacity=100;cursorShape='bar'} }
        $theme = '{"opacity":43,"acrylic":true,"unfocused_opacity":37,"unfocused_acrylic":false}' | ConvertFrom-Json -AsHashtable
        Set-MiosTerminalTransparency $appearance $theme
        $appearance.opacity | Should -Be 43
        $appearance.useAcrylic | Should -BeTrue
        $appearance.unfocusedAppearance.opacity | Should -Be 37
        $appearance.unfocusedAppearance.useAcrylic | Should -BeFalse
        $appearance.unfocusedAppearance.cursorShape | Should -Be 'bar'
        $appearance.commandline | Should -Be 'cmd.exe'
    }
    It 'fails on an invalid SSOT opacity before changing the profile' {
        $appearance = @{opacity=43}
        $theme = @{opacity=101;acrylic=$true;unfocused_opacity=37;unfocused_acrylic=$false}
        { Set-MiosTerminalTransparency $appearance $theme } | Should -Throw
        $appearance.opacity | Should -Be 43
    }
}

Describe 'MiOS OS-control SSOT port projection' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-oscontrol-server.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
        $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Read-MiosOscontrolPort' }, $true)
        . ([scriptblock]::Create($functionAst.Extent.Text))
    }
    It 'reads a changed port from the runtime projection' {
        $path = Join-Path $TestDrive 'ssot.json'
        '{"ports":{"oscontrol":23123}}' | Set-Content -LiteralPath $path
        Read-MiosOscontrolPort $path | Should -Be 23123
    }
    It 'rejects missing or invalid SSOT without a port literal fallback' {
        $path = Join-Path $TestDrive 'bad.json'
        foreach ($payload in @('{"ports":{}}','{"ports":{"oscontrol":"23123"}}','{"ports":{"oscontrol":0}}','{"ports":{"oscontrol":65536}}','{"ports":{"oscontrol":23123.5}}')) {
            $payload | Set-Content -LiteralPath $path
            { Read-MiosOscontrolPort $path } | Should -Throw
        }
    }
}
