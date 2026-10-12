# AI-hint: Pester tests: Get-MiOS.ps1 resolves the installation mode from the SSOT.
Describe 'MiOS SSOT installation mode' {
    BeforeAll {
        $source = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '..\..\Get-MiOS.ps1'))
        $start = $source.IndexOf('$installMode = Get-MiosTomlValue')
        $end = $source.IndexOf('if ($Unattended)', $start)
        if ($start -lt 0 -or $end -le $start) { throw 'Actual installer mode dispatch is missing' }
        $script:ModeDispatch = [scriptblock]::Create($source.Substring($start, $end - $start))
        function Invoke-TestInstallationMode([string]$Mode, [bool]$ForceFull = $false) {
            $forwardArgs = @()
            $FullBuild = $ForceFull
            function Get-MiosTomlValue { param($Section,$Key,$Default) return $Mode }
            . $script:ModeDispatch
            return $forwardArgs
        }
    }
    It 'runs the full pipeline when the user SSOT selects full' {
        @(Invoke-TestInstallationMode 'full') | Should -Contain '-FullBuild'
    }
    It 'respects an explicit bootstrap-only SSOT selection' {
        @(Invoke-TestInstallationMode 'bootstrap') | Should -Contain '-BootstrapOnly'
    }
    It 'lets the explicit full-build switch override bootstrap mode' {
        @(Invoke-TestInstallationMode 'bootstrap' $true) | Should -Contain '-FullBuild'
    }
    It 'rejects invalid selections instead of claiming installation' {
        { Invoke-TestInstallationMode 'unknown-mode' } | Should -Throw '*windows_install_mode*'
    }
}
