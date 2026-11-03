# AI-hint: Validates installer and exporter PowerShell AST structure and syntax.
# Adversarial Challenge: AST Syntax and Parsing Ambiguity Stress Test
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $env:MIOS_BOOTSTRAP_ROOT -or -not (Test-Path $env:MIOS_BOOTSTRAP_ROOT)) {
    $env:MIOS_BOOTSTRAP_ROOT = 'C:\mios-bootstrap'
}

Describe "Adversarial Challenge: AST Syntax Validation" {
    $targetFiles = @(
        @{ File = (Join-Path $env:MIOS_BOOTSTRAP_ROOT 'build-mios.ps1'); Name = (Join-Path $env:MIOS_BOOTSTRAP_ROOT 'build-mios.ps1') },
        @{ File = (Join-Path $PSScriptRoot '../../build-mios.ps1'); Name = (Join-Path $PSScriptRoot '../../build-mios.ps1') },
        @{ File = (Join-Path $PSScriptRoot '../../usr/libexec/mios/MiOS.Export.psm1'); Name = (Join-Path $PSScriptRoot '../../usr/libexec/mios/MiOS.Export.psm1') }
    )

    Context "ParseFile on <Name>" -ForEach $targetFiles {
        BeforeAll {
            $script:currentFile = $File
            $script:tokens = $null
            $script:errors = $null
            $script:ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $script:currentFile,
                [ref]$script:tokens,
                [ref]$script:errors
            )
        }

        It "Parses with zero syntax errors" {
            $script:errors.Count | Should -Be 0
        }

        It "Has valid Root ScriptBlockAst without null body" {
            $script:ast | Should -Not -BeNullOrEmpty
            $script:ast.EndBlock | Should -Not -BeNullOrEmpty
        }

        It "Contains no unclosed string interpolation or subexpression parsing errors" {
            $parseErrors = $script:ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.ErrorStatementAst] -or
                $args[0] -is [System.Management.Automation.Language.ErrorExpressionAst]
            }, $true)
            $parseErrors.Count | Should -Be 0
        }
    }
}
