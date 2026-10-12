# AI-hint: Execute the native profile migrator against operator-owned content, stale generated dashboards, idempotence and malformed input.
# AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, usr/share/mios/windows/mios-native-shell.ps1
BeforeAll {
    $source = Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-client-setup.ps1'
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Invalid production client setup source' }
    $function = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Set-MiosNativeProfile' }, $true)
    if (-not $function) { throw 'Production profile migrator missing' }
    . ([scriptblock]::Create($function.Extent.Text))
    function Write-MiosFile([string]$Path, [string]$Text) { [IO.File]::WriteAllText($Path, $Text) }
}

Describe 'Native SSOT profile migration' {
    It 'preserves an operator profile and custom dashboard, including leading param and using statements' {
        $path = Join-Path $TestDrive 'operator.ps1'
        $operator = "using namespace System.Text`nparam([string]`$OperatorValue)`nfunction Show-MiosDashboard { 'operator renderer' }`n`$operatorMarker = 'keep-me'"
        [IO.File]::WriteAllText($path, $operator)
        Set-MiosNativeProfile $path "C:\MiOS\operator's bin"
        $actual = [IO.File]::ReadAllText($path)
        $actual.StartsWith($operator) | Should -BeTrue
        $actual | Should -Match 'operator renderer'
        $actual | Should -Match 'mios-launch.exe.*--host-terminal'
        $actual | Should -Match "operator''s bin"
        Set-MiosNativeProfile $path "C:\MiOS\operator's bin"
        [IO.File]::ReadAllText($path) | Should -BeExactly $actual
    }

    It 'replaces the recognized stale dashboard and enters the native host before the generated profile guard, idempotently' {
        $path = Join-Path $TestDrive 'generated.ps1'
        [IO.File]::WriteAllText($path, "if (`$Global:MiosProfileLoaded) { return }`nfunction Show-MiosDashboard { `$null = '_dashTomlText old-six-services'; 'old-body' }`nfunction OperatorFunction { 'keep-function' }`n")
        Set-MiosNativeProfile $path 'C:\MiOS\bin'
        $actual = [IO.File]::ReadAllText($path)
        $actual | Should -Not -Match 'old-six-services|old-body'
        $actual | Should -Match 'Invoke-MiosNativeDashboard'
        $actual | Should -Match 'keep-function'
        $actual.IndexOf('--host-terminal') | Should -BeLessThan $actual.IndexOf('$Global:MiosProfileLoaded')
        Set-MiosNativeProfile $path 'C:\MiOS\bin'
        [IO.File]::ReadAllText($path) | Should -BeExactly $actual
    }

    It 'refuses malformed operator input without changing any byte' {
        $path = Join-Path $TestDrive 'invalid.ps1'
        [IO.File]::WriteAllText($path, 'function unfinished {')
        $before = [IO.File]::ReadAllBytes($path)
        { Set-MiosNativeProfile $path 'C:\MiOS\bin' } | Should -Throw '*invalid PowerShell profile*'
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) | Should -BeExactly ([Convert]::ToBase64String($before))
    }

    It 'preserves operator preamble statements added to a generated profile' {
        $path = Join-Path $TestDrive 'generated-with-preamble.ps1'
        $preamble = "using namespace System.Text`nparam([string]`$OperatorValue)`n# operator startup comment`n"
        [IO.File]::WriteAllText($path, $preamble + 'if ($Global:MiosProfileLoaded) { return }')
        Set-MiosNativeProfile $path 'C:\MiOS\bin'
        $actual = [IO.File]::ReadAllText($path)
        $actual.StartsWith($preamble) | Should -BeTrue
        $actual.IndexOf('--host-terminal') | Should -BeLessThan $actual.IndexOf('$Global:MiosProfileLoaded')
        Set-MiosNativeProfile $path 'C:\MiOS\bin'
        [IO.File]::ReadAllText($path) | Should -BeExactly $actual
    }
}
