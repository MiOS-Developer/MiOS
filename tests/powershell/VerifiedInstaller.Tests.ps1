# AI-hint: Proves Windows agent installer downloads reject corrupted bytes and missing SSOT hashes before execution.
Describe 'MiOS verified agent installer' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-agent-cli-setup.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
        foreach ($name in @('Test-SHA256Integrity', 'Save-MiosVerifiedInstaller')) {
            $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
            . ([scriptblock]::Create($functionAst.Extent.Text))
        }
        $script:payload = [Text.Encoding]::UTF8.GetBytes('verified installer fixture')
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $script:expected = ([BitConverter]::ToString($sha.ComputeHash($script:payload))).Replace('-', '') } finally { $sha.Dispose() }
    }
    BeforeEach {
        Mock Invoke-WebRequest { param($Uri, $OutFile) [IO.File]::WriteAllBytes($OutFile, $script:payload) }
    }
    It 'accepts bytes matching the SSOT checksum' {
        { Save-MiosVerifiedInstaller 'https://example.invalid/installer' (Join-Path $TestDrive 'good.ps1') $script:expected } | Should -Not -Throw
    }
    It 'rejects a different checksum' {
        { Save-MiosVerifiedInstaller 'https://example.invalid/installer' (Join-Path $TestDrive 'bad.ps1') ('0' * 64) } | Should -Throw '*SHA-256 mismatch*'
    }
    It 'rejects missing checksum without downloading' {
        { Save-MiosVerifiedInstaller 'https://example.invalid/installer' (Join-Path $TestDrive 'missing.ps1') '' } | Should -Throw '*missing or invalid*'
        Should -Invoke Invoke-WebRequest -Times 0 -Exactly
    }
}

Describe 'MiOS in-terminal Windows dispatch' {
    BeforeAll {
        $shellSource = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-shell.ps1'
        $aiSource = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-ai.ps1'
    }
    BeforeEach {
        $script:savedDispatcher = Get-Command mios -CommandType Function -ErrorAction SilentlyContinue
        $script:savedLegacy = $global:MiosLegacyDispatcher
        $script:savedEntry = $global:MiosNativeEntry
        $script:savedBin = $env:MIOS_NATIVE_BIN
        $script:savedBtop = Get-Command btop -CommandType Function -ErrorAction SilentlyContinue
        $global:MiosDispatchReceipt = $null
        function global:mios { param($Verb, [Parameter(ValueFromRemainingArguments=$true)]$Arguments) $global:MiosDispatchReceipt = @('legacy',$Verb) + $Arguments }
        Set-Content -LiteralPath (Join-Path $TestDrive 'mios-native-entry.ps1') -Value 'param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments) $global:MiosDispatchReceipt = @("native") + $Arguments'
    }
    AfterEach {
        Remove-Item Function:\mios -ErrorAction SilentlyContinue
        if ($script:savedDispatcher) { Set-Item Function:\global:mios $script:savedDispatcher.ScriptBlock }
        $global:MiosLegacyDispatcher = $script:savedLegacy
        $global:MiosNativeEntry = $script:savedEntry
        $env:MIOS_NATIVE_BIN = $script:savedBin
        Remove-Variable MiosDispatchReceipt -Scope Global -ErrorAction SilentlyContinue
        Remove-Item Function:\btop -ErrorAction SilentlyContinue
        if ($script:savedBtop) { Set-Item Function:\global:btop $script:savedBtop.ScriptBlock }
    }
    It 'replaces the web route with the native entry while preserving literal arguments' {
        mios ai
        $global:MiosDispatchReceipt[0] | Should -Be 'legacy'
        . $shellSource -BinDirectory $TestDrive
        mios ai codex 'a quoted task; $HOME'
        ($global:MiosDispatchReceipt -join '|') | Should -Be 'native|ai|codex|a quoted task; $HOME'
    }
    It 'retains Windows management verbs after repeated profile projection' {
        . $shellSource -BinDirectory $TestDrive
        . $shellSource -BinDirectory $TestDrive
        mios config 'kept argument'
        ($global:MiosDispatchReceipt -join '|') | Should -Be 'legacy|config|kept argument'
    }
    It 'routes compact and full dashboards through the installed binding' {
        . $shellSource -BinDirectory $TestDrive
        foreach ($verb in @('mini','dash')) {
            mios $verb --no-color
            ($global:MiosDispatchReceipt -join '|') | Should -Be "native|$verb|--no-color"
        }
    }
    It 'routes btop without a Windows Terminal resize or a fixed Linux user' {
        . $shellSource -BinDirectory $TestDrive
        btop --version
        ($global:MiosDispatchReceipt -join '|') | Should -Be 'native|btop|--version'
    }
    It 'allows repair even when the normal runtime preflight would fail' {
        . $shellSource -BinDirectory $TestDrive
        mios repair
        ($global:MiosDispatchReceipt -join '|') | Should -Be 'native|repair'
    }
    It 'repairs the old per-verb wrapper without starting a separate window' {
        $env:MIOS_NATIVE_BIN = $TestDrive
        Mock Start-Process { throw 'A separate window must not be started' }
        & $aiSource codex --version
        ($global:MiosDispatchReceipt -join '|') | Should -Be 'native|ai|codex|--version'
        Should -Invoke Start-Process -Times 0 -Exactly
    }
}
Describe 'MiOS native terminal projection' {
    BeforeAll {
        $source = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-client-setup.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
        foreach ($name in @('Set-MiosTerminalStartup', 'Set-MiosNativeShortcut', 'Set-MiosUnifiedShortcuts')) {
            $functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
            . ([scriptblock]::Create($functionAst.Extent.Text))
        }
        $stamp = 'projection-proof'
    }
    It 'replaces persisted pixel placement with SSOT centering and dimensions' {
        $terminal = @{initialPosition='-1900,400'; unrelated='preserved'}
        $config = @{theme=@{terminal=@{center_on_launch=$true};launch_mode='focus'};terminal=@{cols=97;rows=31}}
        Set-MiosTerminalStartup $terminal $config
        $terminal.ContainsKey('initialPosition') | Should -BeFalse
        $terminal.centerOnLaunch | Should -BeTrue
        $terminal.initialCols | Should -Be 97
        $terminal.initialRows | Should -Be 31
        $terminal.launchMode | Should -Be 'focus'
        $terminal.unrelated | Should -Be 'preserved'
    }
    It 'rejects a malformed SSOT centering policy' {
        { Set-MiosTerminalStartup @{} @{theme=@{terminal=@{center_on_launch='yes'}}} } | Should -Throw '*must be a boolean*'
    }
    It 'repairs an existing legacy shortcut and leaves an identical projection untouched' {
        $shell = New-Object -ComObject WScript.Shell
        $path = Join-Path $TestDrive 'MiOS.lnk'
        $old = $shell.CreateShortcut($path)
        $old.TargetPath = 'C:\old-mios\mios-launch.exe'
        $old.Arguments = 'MiOS-DEV 80 20'
        $old.WindowStyle = 7
        $old.Save()
        Set-MiosNativeShortcut $shell $path 'C:\native-mios\mios-launch.exe' 'MiOS-DEV' 'C:\native-mios'
        $result = $shell.CreateShortcut($path)
        $result.TargetPath | Should -Be 'C:\native-mios\mios-launch.exe'
        $result.Arguments | Should -Be 'MiOS-DEV'
        $result.WorkingDirectory | Should -Be 'C:\native-mios'
        $result.WindowStyle | Should -Be 1
        $before = (Get-FileHash -LiteralPath $path).Hash
        $written = (Get-Item -LiteralPath $path).LastWriteTimeUtc
        Set-MiosNativeShortcut $shell $path 'C:\native-mios\mios-launch.exe' 'MiOS-DEV' 'C:\native-mios'
        (Get-FileHash -LiteralPath $path).Hash | Should -Be $before
        (Get-Item -LiteralPath $path).LastWriteTimeUtc | Should -Be $written
    }
    It 'consolidates personal and common entrypoints with backups and preserves unrelated apps' {
        $shell = New-Object -ComObject WScript.Shell
        $bundle = Join-Path $TestDrive 'consolidation'
        $desktop = Join-Path $bundle 'desktop'
        $programs = Join-Path $bundle 'common-programs'
        $personal = Join-Path $bundle 'personal-programs'
        $folder = Join-Path $personal 'MiOS'
        $config = @{apps=@{hub_shortcut_name='MiOS';start_menu_folder='MiOS';shortcuts=@{help=@{name='MiOS Help'}}};theme=@{terminal=@{hub_target_profile='MiOS-DEV'}};keybindings=@{actions=@()}}
        [IO.Directory]::CreateDirectory($folder) | Out-Null
        [IO.Directory]::CreateDirectory($desktop) | Out-Null
        foreach ($path in @((Join-Path $folder 'MiOS Help.lnk'), (Join-Path $personal 'MiOS.lnk'), (Join-Path $desktop 'MiOS-WIN.lnk'))) {
            $old = $shell.CreateShortcut($path)
            $old.TargetPath = 'C:\retired\mios-launch.exe'
            $old.Save()
        }
        $other = Join-Path $folder 'Other app.lnk'
        $link = $shell.CreateShortcut($other)
        $link.TargetPath = 'C:\Windows\notepad.exe'
        $link.Save()
        $before = (Get-FileHash -LiteralPath $other).Hash
        Set-MiosUnifiedShortcuts $shell $config 'C:\native-mios' @($desktop) @($programs,$personal)
        @(Get-ChildItem -LiteralPath $bundle -Filter 'MiOS*.lnk' -Recurse).Count | Should -Be 2
        @(Get-ChildItem -LiteralPath $bundle -Filter '*.mios-backup-*' -Recurse).Count | Should -Be 3
        (Get-FileHash -LiteralPath $other).Hash | Should -Be $before
        Set-MiosUnifiedShortcuts $shell $config 'C:\native-mios' @($desktop) @($programs,$personal)
        @(Get-ChildItem -LiteralPath $bundle -Filter 'MiOS*.lnk' -Recurse).Count | Should -Be 2
        $shell.CreateShortcut((Join-Path $programs 'MiOS.lnk')).Arguments | Should -Be 'MiOS-DEV'
    }
}
