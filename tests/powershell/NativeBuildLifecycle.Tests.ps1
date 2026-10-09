# AI-hint: Exercise actual full-install builder preservation and native SSOT image dispatch.
Describe 'MiOS full install build lifecycle' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '..\..\build-mios.ps1'
        $ast = [Management.Automation.Language.Parser]::ParseFile($source,[ref]$null,[ref]$null)
        foreach ($name in @('Resolve-MiosBuilderDistribution','Ensure-MiosBuilder','Invoke-MiosNativeImageBuild','Update-MiosCheckout','Invoke-WslBuild')) {
            $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false)
            if (-not $fn) { throw "Production function missing: $name" }
            . ([scriptblock]::Create($fn.Extent.Text))
        }
        function New-BuilderDistro { param($HW) }
        function Invoke-GitFetchWithRetry { param($RepoPath,$Ref) }
        function Write-Log { param($Message) }
        function Log-Warn { param($Message) }
    }
    BeforeEach {
        $global:LASTEXITCODE = 0
        $script:NativeInvocations = [Collections.Generic.List[object]]::new()
        Mock New-BuilderDistro { throw 'Existing builder must survive' }
        Mock podman { throw 'Existing WSL builder must be reused directly' }
    }
    It 'reuses the prefixed WSL builder without touching Podman metadata' {
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            $script:NativeInvocations.Add(@($Arguments))
            $global:LASTEXITCODE=0
            if ($Arguments[0] -eq '--list') { return "p`0o`0d`0m`0a`0n`0-`0M`0i`0O`0S`0-`0D`0E`0V`0" }
        }
        Ensure-MiosBuilder 'MiOS-DEV' @{} | Should -Be 'podman-MiOS-DEV'
        Should -Invoke New-BuilderDistro -Times 0 -Exactly
        Should -Invoke podman -Times 0 -Exactly
    }
    It 'fails an unresponsive existing builder without recreating it' {
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            if ($Arguments[0] -eq '--list') { $global:LASTEXITCODE=0; return 'podman-MiOS-DEV' }
            $global:LASTEXITCODE=17
        }
        { Ensure-MiosBuilder 'MiOS-DEV' @{} } | Should -Throw '*persistent state is preserved*'
        Should -Invoke New-BuilderDistro -Times 0 -Exactly
        Should -Invoke podman -Times 0 -Exactly
    }
    It 'preserves a registered machine when its start fails' {
        Mock wsl.exe { $global:LASTEXITCODE=0 }
        Mock podman { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            if ($Arguments[1] -eq 'list') { $global:LASTEXITCODE=0; return 'MiOS-DEV' }
            $global:LASTEXITCODE=19
        }
        { Ensure-MiosBuilder 'MiOS-DEV' @{} } | Should -Throw '*persistent state is preserved*'
        Should -Invoke New-BuilderDistro -Times 0 -Exactly
        Should -Invoke podman -Times 2 -Exactly
    }
    It 'does not fetch or overwrite a dirty checkout' {
        Mock git { $global:LASTEXITCODE=0; ' M usr/share/mios/mios.toml' }
        Mock Invoke-GitFetchWithRetry { throw 'Dirty checkout must survive' }
        Update-MiosCheckout $TestDrive 'main'
        Should -Invoke Invoke-GitFetchWithRetry -Times 0 -Exactly
        Should -Invoke git -Times 1 -Exactly
    }
    It 'refuses a divergent clean checkout rather than resetting it' {
        Mock git { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            if ($Arguments -contains 'merge') { $global:LASTEXITCODE=1 } else { $global:LASTEXITCODE=0 }
        }
        Mock Invoke-GitFetchWithRetry { return 0 }
        { Update-MiosCheckout $TestDrive 'main' } | Should -Throw '*local commits are preserved*'
    }
    It 'routes the full build through the same native engine' {
        $MiosRepoDir = $TestDrive
        Mock Invoke-MiosNativeImageBuild { return 23 }
        Invoke-WslBuild -Distro 'MiOS-DEV' -BaseImage 'ignored' -AiModel 'ignored' | Should -Be 23
        Should -Invoke Invoke-MiosNativeImageBuild -Times 1 -Exactly -ParameterFilter { $Root -eq $TestDrive -and $Machine -eq 'MiOS-DEV' }
    }
    It 'returns the actual native image exit code without mixing in build output' {
        foreach ($file in @('usr\share\mios\mios.toml','Containerfile','.devcontainer\Containerfile','automation\55-native-build.sh')) {
            $path=Join-Path $TestDrive $file
            New-Item -ItemType Directory -Path (Split-Path $path) -Force | Out-Null
            Set-Content $path 'fixture'
        }
        Mock Resolve-MiosBuilderDistribution { return 'podman-MiOS-DEV' }
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            $global:LASTEXITCODE=0
            $script:NativeInvocations.Add(@($Arguments))
            if ($Arguments -contains 'wslpath') { return '/tmp/operator-source' }
            if ($Arguments -contains 'image-build') { $global:LASTEXITCODE=23 }
            return 'Actual build output'
        }
        $result = Invoke-MiosNativeImageBuild $TestDrive 'MiOS-DEV'
        $result | Should -BeOfType ([int])
        $result | Should -Be 23
        $script:NativeInvocations.Count | Should -Be 3
        $script:NativeInvocations[2] | Should -Contain 'image-build'
        $script:NativeInvocations[2] | Should -Contain 'all'
    }
    It 'stops before image building when native installation fails' {
        foreach ($file in @('usr\share\mios\mios.toml','Containerfile','.devcontainer\Containerfile','automation\55-native-build.sh')) {
            $path=Join-Path $TestDrive $file
            New-Item -ItemType Directory -Path (Split-Path $path) -Force | Out-Null
            Set-Content $path 'fixture'
        }
        Mock Resolve-MiosBuilderDistribution { return 'podman-MiOS-DEV' }
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            $script:NativeInvocations.Add(@($Arguments))
            if ($Arguments -contains 'wslpath') { $global:LASTEXITCODE=0; return '/tmp/operator-source' }
            $global:LASTEXITCODE=17
        }
        { Invoke-MiosNativeImageBuild $TestDrive 'MiOS-DEV' } | Should -Throw '*Native SSOT build/install failed*'
        $script:NativeInvocations.Count | Should -Be 2
        $script:NativeInvocations[1] | Should -Contain 'bash'
        $script:NativeInvocations[1] | Should -Not -Contain 'image-build'
    }
}
