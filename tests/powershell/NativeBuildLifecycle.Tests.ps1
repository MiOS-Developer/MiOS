# AI-hint: Exercise actual full-install builder preservation and native SSOT image dispatch.
Describe 'MiOS full install build lifecycle' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '..\..\build-mios.ps1'
        $ast = [Management.Automation.Language.Parser]::ParseFile($source,[ref]$null,[ref]$null)
        foreach ($name in @('Resolve-MiosBuilderDistribution','Ensure-MiosBuilder','Install-MiosNativeCatalog','Invoke-MiosNativeImageBuild','Install-MiosNativeWindowsArtifact','Update-MiosCheckout','Invoke-WslBuild')) {
            $fn = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$false)
            if (-not $fn) { throw "Production function missing: $name" }
            . ([scriptblock]::Create($fn.Extent.Text))
        }
        function New-BuilderDistro { param($HW) }
        # Pester can only mock a command that resolves. wsl.exe exists only on
        # Windows and podman only where it is installed, while this suite also
        # runs under pwsh on the Linux CI runner. Stand-ins make both resolvable
        # everywhere, and throw so a path a test forgot to mock fails instead of
        # reaching a real builder.
        function wsl.exe { throw 'Unmocked wsl.exe call' }
        function podman { throw 'Unmocked podman call' }
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
        $script:NativeInvocations.Count | Should -Be 5
        $script:NativeInvocations[4] | Should -Contain 'image-build'
        $script:NativeInvocations[4] | Should -Contain 'all'
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
    It 'installs only the declared Windows target after checking the staged bytes' {
        $binaryRoot = Join-Path $TestDrive 'operator source'
        $release = Join-Path $binaryRoot 'tools/native/target/x86_64-pc-windows-msvc/release/mios-wallpaperd.exe'
        New-Item -ItemType Directory -Path (Split-Path $release) -Force | Out-Null
        Set-Content $release 'verified release'
        $destination = Join-Path $TestDrive 'installed.exe'
        Set-Content $destination 'previous release'
        Mock Resolve-MiosBuilderDistribution { 'podman-MiOS-DEV' }
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            $global:LASTEXITCODE=0
            $script:NativeInvocations.Add(@($Arguments))
            if ($Arguments -contains 'wslpath') { return '/tmp/operator source' }
            if ($Arguments -contains '--section') { return '{"target":"x86_64-pc-windows-msvc"}' }
            if ($Arguments -contains 'native-artifact-check') { (Get-Content $destination -Raw).Trim() | Should -Be 'previous release' }
        }
        Install-MiosNativeWindowsArtifact $binaryRoot 'MiOS-DEV' 'mios-wallpaperd' $destination | Should -Be $destination
        (Get-Content $destination -Raw).Trim() | Should -Be 'verified release'
        $script:NativeInvocations[2] | Should -Contain 'native-windows-build'
        $script:NativeInvocations[4] | Should -Contain 'native-artifact-check'
    }
    It 'preserves the installed Windows executable when release lint or build fails' {
        $destination = Join-Path $TestDrive 'preserved.exe'
        Set-Content $destination 'operator release'
        Mock Resolve-MiosBuilderDistribution { 'podman-MiOS-DEV' }
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            $global:LASTEXITCODE=0
            if ($Arguments -contains 'wslpath') { return '/tmp/operator source' }
            if ($Arguments -contains '--section') { return '{"target":"x86_64-pc-windows-msvc"}' }
            if ($Arguments -contains 'native-windows-build') { $global:LASTEXITCODE=17 }
        }
        { Install-MiosNativeWindowsArtifact $TestDrive 'MiOS-DEV' 'mios-wallpaperd' $destination } | Should -Throw '*lint/build/static-artifact*'
        (Get-Content $destination -Raw).Trim() | Should -Be 'operator release'
    }
    It 'preserves the installed Windows executable when its copied artifact is rejected' {
        $release = Join-Path $TestDrive 'tools/native/target/x86_64-pc-windows-msvc/release/mios-wallpaperd.exe'
        New-Item -ItemType Directory -Path (Split-Path $release) -Force | Out-Null
        Set-Content $release 'rejected release'
        $destination = Join-Path $TestDrive 'preserved.exe'
        Set-Content $destination 'operator release'
        Mock Resolve-MiosBuilderDistribution { 'podman-MiOS-DEV' }
        Mock wsl.exe { param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
            $global:LASTEXITCODE=0
            if ($Arguments -contains 'wslpath') { return '/tmp/operator source' }
            if ($Arguments -contains '--section') { return '{"target":"x86_64-pc-windows-msvc"}' }
            if ($Arguments -contains 'native-artifact-check') { $global:LASTEXITCODE=23 }
        }
        { Install-MiosNativeWindowsArtifact $TestDrive 'MiOS-DEV' 'mios-wallpaperd' $destination } | Should -Throw '*dependency gate*'
        (Get-Content $destination -Raw).Trim() | Should -Be 'operator release'
    }

}
