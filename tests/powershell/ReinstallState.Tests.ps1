# AI-hint: Pester tests: a failed reinstall preserves state without unregistering guests or deleting files.
Describe 'MiOS reinstall state preservation' {
    It 'preserves a failed install without unregistering guests or deleting files' {
        $source=Join-Path $PSScriptRoot '..\..\Get-MiOS.ps1'
        $ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$null,[ref]$null)
        $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-MiOSFullReap'},$false)
        . ([scriptblock]::Create($fn.Extent.Text))
        Mock Remove-Item { throw 'Persistent state must survive reinstall' }
        function wsl.exe { throw 'A reinstall must not unregister WSL' }
        function podman { throw 'A reinstall must not remove machines' }
        Invoke-MiOSFullReap -Quiet
        Should -Invoke Remove-Item -Times 0 -Exactly
    }
}

Describe 'MiOS CPU allocation floor' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '../../build-mios.ps1'
        $ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
        foreach ($name in @('Get-MiosCpuMinimum', 'Resolve-MiosCpuAllocation')) {
            $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $false)
            . ([scriptblock]::Create($fn.Extent.Text))
        }
        function Get-MiosTomlValue { param($Section, $Key, $Default) return $script:MiosTestMinimum }
    }
    BeforeEach { $script:MiosTestMinimum = 4 }
    It 'admits a four CPU host' { Get-MiosCpuMinimum -AvailableCpus 4 | Should -Be 4 }
    It 'rejects an undersized host' { { Get-MiosCpuMinimum -AvailableCpus 3 } | Should -Throw }
    It 'rejects an undersized configured floor' { $script:MiosTestMinimum = 3; { Get-MiosCpuMinimum -AvailableCpus 16 } | Should -Throw }
    It 'rejects a malformed configured floor' { $script:MiosTestMinimum = 'max'; { Get-MiosCpuMinimum -AvailableCpus 16 } | Should -Throw }
    It 'supports a larger configured floor' { $script:MiosTestMinimum = 8; Get-MiosCpuMinimum -AvailableCpus 16 | Should -Be 8 }
    It 'keeps computed guests at four despite host reservation' { Resolve-MiosCpuAllocation -AvailableCpus 4 -ReservedCpus 2 | Should -Be 4 }
    It 'retains maximal allocation above the floor' { Resolve-MiosCpuAllocation -AvailableCpus 32 -ReservedCpus 4 | Should -Be 28 }
    It 'rejects an explicit two CPU guest or builder' { { Resolve-MiosCpuAllocation -AvailableCpus 16 -RequestedCpus 2 } | Should -Throw }
    It 'rejects a negative explicit allocation' { { Resolve-MiosCpuAllocation -AvailableCpus 16 -RequestedCpus -2 } | Should -Throw }
    It 'retains a valid explicit allocation' { Resolve-MiosCpuAllocation -AvailableCpus 16 -RequestedCpus 8 | Should -Be 8 }
}
