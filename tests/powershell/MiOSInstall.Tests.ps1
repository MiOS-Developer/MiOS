# AI-hint: Pester characterization tests for MiOS.Install module suite.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$installModuleDir = Join-Path $scriptDir '../../automation/lib/MiOS.Install'

Describe "MiOS.Install Sub-modules" {
    # Import once in BeforeAll: Pester 5 isolates each It, so modules imported
    # inside one are invisible to the next.
    BeforeAll {
        # Pester 5 runs BeforeAll in a different scope from the
        # discovery-phase top level, so recompute paths here.
        $installModuleDir = Join-Path $PSScriptRoot '../../automation/lib/MiOS.Install'
        $script:installFiles = @(Get-ChildItem -Path $installModuleDir -Filter '*.psm1')
        foreach ($f in $script:installFiles) {
            Import-Module $f.FullName -Force -Global
        }
    }

    It "Should load all sub-modules" {
        $script:installFiles.Count | Should -BeGreaterThan 0
    }

    It "Should return phase name by ID" {
        $phase = Get-MiosInstallerPhaseName 0
        $phase | Should -Be 'Prerequisites'
    }

    It "Should return verb list array" {
        $verbs = Get-MiosVerbList
        ($verbs -contains 'build') | Should -Be $true
    }

    It "Should return existing target dir when sentinel exists" {
        # $scriptDir is discovery-phase and null here; $env:TEMP does not exist
        # on Linux, where CI runs pwsh; and 'field\autounattend' is not a path
        # there either.
        $common = Join-Path $PSScriptRoot '../../installation/mios-common.ps1'
        . $common
        $tempRoot = [System.IO.Path]::GetTempPath()
        $tempDir = Join-Path $tempRoot ('mios-test-sentinel-' + [Guid]::NewGuid().ToString('N'))
        $sentinelDir = Join-Path $tempDir 'field/autounattend'
        New-Item -ItemType Directory -Force -Path $sentinelDir | Out-Null
        Set-Content -Path (Join-Path $sentinelDir 'Build-MiOSXboxISO.ps1') -Value '# test sentinel'
        try {
            $res = Ensure-MiosBootstrapRepo -TargetDir $tempDir
            $res | Should -Be $tempDir
        } finally {
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}


Describe 'Monitor layout uses the resolved SSOT launch mode' {
    BeforeAll {
        $common = Join-Path $PSScriptRoot '../../installation/mios-common.ps1'
        $tokens = $null; $parseErrors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile($common, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count) { throw 'Invalid production monitor source' }
        $monitor = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Start-MiosMonitor' }, $true)
        $selection = $monitor.Find({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Right.Extent.Text -match "Get-MiosSsotValue -Section 'theme' -Key 'launch_mode'" }, $true)
        $layout = $monitor.Find({ param($node)
            $node -is [Management.Automation.Language.IfStatementAst] -and
            $node.Clauses[0].Item1.Extent.Text -match 'fullscreen' -and
            $node.Extent.Text -match 'split-window'
        }, $true)
        if (-not $selection -or -not $layout) { throw 'Production SSOT mode/layout statements missing' }
        $script:monitorSelection = [scriptblock]::Create($selection.Extent.Text)
        $script:monitorLayout = [scriptblock]::Create($layout.Extent.Text)
        function Get-MiosSsotValue { param($Section, $Key, $Default) $script:fixtureMode }
        function Invoke-MiosTmuxFixture {
            $script:tmuxCalls.Add(@($args))
        }
    }
    BeforeEach {
        $script:tmuxCalls = [Collections.Generic.List[object]]::new()
        $tmuxExe = 'Invoke-MiosTmuxFixture'
        $cols = 80; $rows = 20
        $interactiveShell = 'fixture-interactive'; $monRunner = 'fixture-monitor'
        # A stale caller variable must not override the resolved SSOT mode.
        $launchMode = 'fullscreen'
    }
    It 'creates the expanded four-split layout for both fullscreen SSOT values' {
        foreach ($selected in @('fullscreen', 'focusFullscreen')) {
            $script:fixtureMode = $selected
            $launchMode = 'focus'
            $script:tmuxCalls.Clear()
            . $script:monitorSelection
            . $script:monitorLayout
            @($script:tmuxCalls | Where-Object { $_[0] -eq 'split-window' }).Count | Should -Be 4
            ($script:tmuxCalls[-1] -join ' ') | Should -BeExactly 'select-pane -t mios-mon:0.1'
        }
    }
    It 'keeps the compact layout despite a stale fullscreen caller variable' {
        $script:fixtureMode = 'focus'
        . $script:monitorSelection
        . $script:monitorLayout
        @($script:tmuxCalls | Where-Object { $_[0] -eq 'split-window' }).Count | Should -Be 1
    }
    It 'places the compact portrait monitor above the interactive pane' {
        $script:fixtureMode = 'focus'
        $cols = 20; $rows = 80
        . $script:monitorSelection
        . $script:monitorLayout
        @($script:tmuxCalls | Where-Object { $_[0] -eq 'split-window' }).Count | Should -Be 1
        ($script:tmuxCalls[0] -join ' ') | Should -Match 'fixture-monitor$'
        ($script:tmuxCalls[-1] -join ' ') | Should -BeExactly 'select-pane -t mios-mon:0.0'
    }
}
