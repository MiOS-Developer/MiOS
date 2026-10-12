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