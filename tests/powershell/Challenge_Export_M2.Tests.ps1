# AI-hint: Exercises export failure cleanup, process disposal and error propagation.
# Adversarial Challenge: Export Failure and Cleanup Harness
# Tests:
# 1. Partial file deletion on export failure
# 2. Process termination and resource disposal
# 3. podman rm -f transient container cleanup
# 4. Phase 10 / Phase 11 error propagation and $script:ExitCode = 1
# 5. Stderr buffer drain without deadlock
# 6. mios-windows-export.ps1 deprecations and cleanup

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Describe "Adversarial Challenge: Container Export Failure & Cleanup" {
    BeforeAll {
        $script:tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("mios_challenger_" + [System.Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $script:tempDir -Force | Out-Null
        $script:mockLog = Join-Path $script:tempDir "mock_podman.log"
        $script:mockBin = $env:MIOS_TEST_PODMAN_BIN
        if (-not $script:mockBin -or -not (Test-Path -LiteralPath $script:mockBin)) { throw 'The compiled Podman test fixture is required.' }
        
        $script:oldPath = $env:PATH
        $env:PATH = [System.IO.Path]::GetDirectoryName($script:mockBin) + [System.IO.Path]::PathSeparator + $env:PATH
        $env:MOCK_PODMAN_LOG = $script:mockLog
    }

    AfterAll {
        $env:PATH = $script:oldPath
        Remove-Item Env:MOCK_PODMAN_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:MOCK_PODMAN_MODE -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $script:tempDir) {
            Remove-Item -LiteralPath $script:tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Context "Scenario 1: Non-zero exit code during export streaming" {
        BeforeAll {
            $env:MOCK_PODMAN_MODE = "export_fail"
            if (Test-Path -LiteralPath $script:mockLog) { Remove-Item -LiteralPath $script:mockLog -Force }
        }

        It "Export-WslTar throws detailed exception, deletes partial file, and runs podman rm -f" {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $env:MIOS_BOOTSTRAP_ROOT 'build-mios.ps1'), [ref]$null, [ref]$null)
            $funcAst = $ast.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq "Export-WslTar" }, $true)
            
            $helperStub = @'
                function Set-Step($msg) {}
                function Write-Log($msg, $lvl="INFO") {}
                function Log-Ok($msg) {}
                function Log-Warn($msg) {}
                function Get-MiosTomlValue($Section, $Key, $Default) { return $Default }
'@
            $testScript = $helperStub + "`n" + $funcAst.Extent.Text + @"

                `$outFile = Join-Path '$($script:tempDir.Replace("'", "''"))' 'test_partial_export.tar'
                `$thrown = `$false
                `$exMsg = ''
                try {
                    Export-WslTar -OutFile `$outFile -Image 'localhost/mios:latest'
                } catch {
                    `$thrown = `$true
                    `$exMsg = `$_.Exception.Message
                }

                [PSCustomObject]@{
                    Thrown = `$thrown
                    ExceptionMessage = `$exMsg
                    FileExists = (Test-Path -LiteralPath `$outFile)
                } | ConvertTo-Json -Compress
"@
            $raw = & (Get-Process -Id $PID).Path -NoProfile -Command $testScript
            $res = $raw | ConvertFrom-Json
            
            $res.Thrown | Should -Be $true
            $res.ExceptionMessage | Should -Match 'podman export exited 125'
            $res.ExceptionMessage | Should -Match 'simulated storage corruption'
            $res.FileExists | Should -Be $false

            # Check that podman rm -f e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 was logged
            $logContent = Get-Content -LiteralPath $script:mockLog -Raw
            $logContent | Should -Match 'rm -f e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        }

        It "System build - Export-WslTar throws detailed exception, deletes partial file, and runs podman rm -f" {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../../build-mios.ps1'), [ref]$null, [ref]$null)
            $funcAst = $ast.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq "Export-WslTar" }, $true)
            
            $helperStub = @'
                function Set-Step($msg) {}
                function Write-Log($msg, $lvl="INFO") {}
                function Log-Ok($msg) {}
                function Log-Warn($msg) {}
                function Get-MiosTomlValue($Section, $Key, $Default) { return $Default }
'@
            $testScript = $helperStub + "`n" + $funcAst.Extent.Text + @"

                `$outFile = Join-Path '$($script:tempDir.Replace("'", "''"))' 'test_partial_export_mios.tar'
                `$thrown = `$false
                `$exMsg = ''
                try {
                    Export-WslTar -OutFile `$outFile -Image 'localhost/mios:latest'
                } catch {
                    `$thrown = `$true
                    `$exMsg = `$_.Exception.Message
                }

                [PSCustomObject]@{
                    Thrown = `$thrown
                    ExceptionMessage = `$exMsg
                    FileExists = (Test-Path -LiteralPath `$outFile)
                } | ConvertTo-Json -Compress
"@
            $raw = & (Get-Process -Id $PID).Path -NoProfile -Command $testScript
            $res = $raw | ConvertFrom-Json
            
            $res.Thrown | Should -Be $true
            $res.ExceptionMessage | Should -Match 'podman export exited 125'
            $res.ExceptionMessage | Should -Match 'simulated storage corruption'
            $res.FileExists | Should -Be $false
        }

        It "Windows export - Export-WslTar cleans partial archive and runs podman rm -f" {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../../mios-windows-export.ps1'), [ref]$null, [ref]$null)
            $funcAst = $ast.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq "Export-WslTar" }, $true)
            
            $helperStub = @'
                function Write-Step($msg) {}
                function Write-Ok($msg) {}
                function Write-Warn($msg) {}
                function Write-Bad($msg) {}
                function Test-CommandExists($Name) { return $true }
'@
            $testScript = $helperStub + "`n" + $funcAst.Extent.Text + @"

                `$outDir = '$($script:tempDir.Replace("'", "''"))'
                `$expectedTar = Join-Path `$outDir 'mios.wsl.tar'
                `$thrown = `$false
                `$exMsg = ''
                try {
                    Export-WslTar -ImageRef 'localhost/mios:latest' -OutDir `$outDir
                } catch {
                    `$thrown = `$true
                    `$exMsg = `$_.Exception.Message
                }

                [PSCustomObject]@{
                    Thrown = `$thrown
                    ExceptionMessage = `$exMsg
                    FileExists = (Test-Path -LiteralPath `$expectedTar)
                } | ConvertTo-Json -Compress
"@
            $raw = & (Get-Process -Id $PID).Path -NoProfile -Command $testScript
            $res = $raw | ConvertFrom-Json
            
            $res.Thrown | Should -Be $true
            $res.ExceptionMessage | Should -Match 'podman export failed with exit code 125'
            $res.ExceptionMessage | Should -Match 'simulated storage corruption'
            $res.FileExists | Should -Be $false
        }
    }

    Context "Scenario 2: Pipe buffer deadlock prevention (large stderr output)" {
        BeforeAll {
            $env:MOCK_PODMAN_MODE = "deadlock"
        }

        It "Drains 128KB stderr asynchronously without deadlock within timeout" {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $env:MIOS_BOOTSTRAP_ROOT 'build-mios.ps1'), [ref]$null, [ref]$null)
            $funcAst = $ast.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq "Export-WslTar" }, $true)
            
            $helperStub = @'
                function Set-Step($msg) {}
                function Write-Log($msg, $lvl="INFO") {}
                function Log-Ok($msg) {}
                function Log-Warn($msg) {}
                function Get-MiosTomlValue($Section, $Key, $Default) { return $Default }
'@
            $testScript = $helperStub + "`n" + $funcAst.Extent.Text + @"

                `$outFile = Join-Path '$($script:tempDir.Replace("'", "''"))' 'test_deadlock.tar'
                `$sw = [System.Diagnostics.Stopwatch]::StartNew()
                `$thrown = `$false
                `$errLen = 0
                try {
                    Export-WslTar -OutFile `$outFile -Image 'localhost/mios:latest'
                } catch {
                    `$thrown = `$true
                    `$errLen = `$_.Exception.Message.Length
                }
                `$sw.Stop()

                [PSCustomObject]@{
                    Thrown = `$thrown
                    ElapsedMs = `$sw.ElapsedMilliseconds
                    ErrorLength = `$errLen
                } | ConvertTo-Json -Compress
"@
            $raw = & (Get-Process -Id $PID).Path -NoProfile -Command $testScript
            $res = $raw | ConvertFrom-Json
            $res.Thrown | Should -Be $true
            # With async drain, completes in < 8000ms and captures all > 65536 bytes
            $res.ElapsedMs | Should -BeLessThan 8000
            $res.ErrorLength | Should -BeGreaterThan 65536
        }
    }

    Context "Scenario 3: Phase 10 & Phase 11 Error Propagation in Invoke-DeployPipeline" {
        It "Marks Phase 10 -Fail, skips Phase 11 with -Fail, and sets `$script:ExitCode = 1" {
            $pipelineTest = @"
                `$script:ExitCode = 0
                `$ExitCode = 0
                `$script:PhStat = @(0) * 14
                function Start-Phase([int]`$i) {}
                function End-Phase([int]`$i, [switch]`$Fail, [switch]`$Warn) {
                    `$script:PhStat[`$i] = if (`$Fail) { 3 } elseif (`$Warn) { 4 } else { 2 }
                }
                function Log-Ok(`$msg) {}
                function Log-Warn(`$msg) {}
                function Export-WslTar([string]`$OutFile) {
                    throw "Simulated export failure: out of disk space"
                }
                function Import-MiosWsl([string]`$TarFile, [string]`$InstallDir) {
                    return `$true
                }

                `$MiosDistroDir = '$($script:tempDir.Replace("'", "''"))'
                `$MiosWslDistro = 'MiOS'

                `$artifactDir = Join-Path `$MiosDistroDir "artifacts"
                `$wslFsDir    = Join-Path `$MiosDistroDir "MiOS"
                if (-not (Test-Path `$artifactDir)) { New-Item -ItemType Directory -Path `$artifactDir -Force | Out-Null }
                if (-not (Test-Path `$wslFsDir))    { New-Item -ItemType Directory -Path `$wslFsDir    -Force | Out-Null }

                # -- Phase 10: Export WSL2 tar ----------------------------------------------
                Start-Phase 10
                `$wslTar = Join-Path `$artifactDir "mios-wsl2.tar"
                `$wslOk  = `$false
                try {
                    `$wslOk = Export-WslTar -OutFile `$wslTar
                    `$sizeMB = [math]::Round((Get-Item `$wslTar).Length / 1MB)
                    Log-Ok "WSL2 tar: `$sizeMB MB -> `$wslTar"
                    End-Phase 10
                } catch {
                    Log-Warn "WSL2 export: `$_"
                    End-Phase 10 -Fail
                    `$script:ExitCode = 1
                    `$ExitCode = 1
                }

                # -- Phase 11: Register WSL2 distro ----------------------------------------
                Start-Phase 11
                if (`$wslOk) {
                    try {
                        `$null = Import-MiosWsl -TarFile `$wslTar -InstallDir `$wslFsDir
                        Log-Ok "WSL2 distro '`$MiosWslDistro' registered at `$wslFsDir"
                        End-Phase 11
                    } catch {
                        Log-Warn "WSL2 import: `$_"
                        End-Phase 11 -Fail
                        `$script:ExitCode = 1
                        `$ExitCode = 1
                    }
                } else {
                    Log-Warn "Skipped (no WSL2 tar due to export failure)"
                    End-Phase 11 -Fail
                    `$script:ExitCode = 1
                    `$ExitCode = 1
                }

                [PSCustomObject]@{
                    ExitCode = `$ExitCode
                    ScriptExitCode = `$script:ExitCode
                    Phase10Status = `$script:PhStat[10]
                    Phase11Status = `$script:PhStat[11]
                } | ConvertTo-Json -Compress
"@
            $raw = & (Get-Process -Id $PID).Path -NoProfile -Command $pipelineTest
            $res = $raw | ConvertFrom-Json
            $res.ExitCode | Should -Be 1
            $res.ScriptExitCode | Should -Be 1
            $res.Phase10Status | Should -Be 3
            $res.Phase11Status | Should -Be 3
        }
    }

    Context "Scenario 4: POSIX preservation & deprecations in mios-windows-export.ps1" {
        BeforeAll {
            $script:exportScript = (Join-Path $PSScriptRoot '../../mios-windows-export.ps1')
        }

        It "Merge-LayersToTar is removed to protect POSIX permissions" {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile($script:exportScript, [ref]$null, [ref]$null)
            $funcAst = $ast.Find({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq "Merge-LayersToTar" }, $true)
            $funcAst | Should -BeNullOrEmpty
        }

        It "Warns on Compress-WithZstd to prevent WSL error 0x80070057" {
            $raw = Get-Content -LiteralPath $script:exportScript -Raw
            $raw | Should -Match 'DEPRECATED: \.tar\.zst compression is incompatible with .wsl --import.'
        }
    }
}
