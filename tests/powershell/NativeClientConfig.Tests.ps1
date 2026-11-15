# AI-hint: Execute the client setup's reads of other applications' configs: blank files read as empty, anything else unreadable is refused and left unchanged, and one refused client never stops the others.
# AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, tests/powershell/NativeClientScope.Tests.ps1
BeforeAll {
    $source = if ($env:MIOS_TEST_CLIENT_SETUP) { $env:MIOS_TEST_CLIENT_SETUP } else { Join-Path $PSScriptRoot '../../usr/share/mios/windows/mios-native-client-setup.ps1' }
    $script:setupText = [IO.File]::ReadAllText((Resolve-Path $source)) -replace "`r`n", "`n"
    $tokens = $null; $errors = $null
    $script:setupAst = [Management.Automation.Language.Parser]::ParseInput($script:setupText, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Invalid production client setup source' }
    foreach ($name in @('Read-MiosJsonValue', 'Read-MiosJson', 'Read-MiosJsonList', 'Get-MiosJsonSection', 'Invoke-MiosClientConfig',
                        'Write-MiosFile', 'Save-MiosJson', 'Convert-MiosOrdered', 'Set-MiosTerminalStartup', 'Set-MiosTerminalTransparency')) {
        $function = $script:setupAst.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }.GetNewClosure(), $true)
        if (-not $function) { throw "Production function missing: $name" }
        . ([scriptblock]::Create($function.Extent.Text))
    }
    # The production statements themselves: the MCP client loop, the Windows Terminal loop and the closing summary.
    function Get-MiosStatement([type]$Kind, [string]$Pattern) {
        $node = $script:setupAst.Find({ param($n) $n -is $Kind -and $n.Extent.Text -match $Pattern }.GetNewClosure(), $true)
        if (-not $node) { throw "Production statement missing: $Pattern" }
        return [scriptblock]::Create($node.Extent.Text)
    }
    $script:mcpLoop = Get-MiosStatement ([Management.Automation.Language.ForEachStatementAst]) "^foreach \(\`$path in @\(\s*\(Join-Path \`$env:USERPROFILE '\.claude\.json'\)"
    $script:terminalLoop = Get-MiosStatement ([Management.Automation.Language.ForEachStatementAst]) '^foreach \(\$path in \$terminalPaths\)'
    $script:summary = Get-MiosStatement ([Management.Automation.Language.IfStatementAst]) '^if \(\$skippedClients\.Count\)'

    function New-MiosFile([string]$Path, [string]$Text) {
        [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
        [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
    }
    # The SSOT keys the Windows Terminal projection reads, with valid values.
    $script:terminalConfig = @{
        colors = @{bg='#101010'; fg='#e0e0e0'; cursor='#ffffff'; muted='#404040'}
        theme = @{launch_mode='default'; padding='8'; scrollbar_state='hidden'; cursor_shape='bar'; suppress_app_title=$true
                  opacity=90; unfocused_opacity=80; acrylic=$false; unfocused_acrylic=$false; system_backdrop=''
                  terminal=@{scheme_name='MiOS'; summon_keys='win+`'; summon_window_name='MiOS'; dev_profile_name='MiOS-DEV'
                             profile_name='MiOS'; hub_target_profile='MiOS'; center_on_launch=$true}}
        terminal = @{cols=80; rows=40}
        font = @{family='Mono'; size=11}
    }
    $names = @('black','red','green','yellow','blue','magenta','cyan','white')
    for ($i = 0; $i -lt 8; $i++) {
        $script:terminalConfig['colors']["ansi_${i}_$($names[$i])"] = '#00000{0}' -f $i
        $script:terminalConfig['colors']["ansi_$($i+8)_bright_$($names[$i])"] = '#11111{0}' -f $i
    }
}

Describe 'Native client setup: reading other applications'' configs' {
    BeforeEach {
        $script:root = Join-Path ([IO.Path]::GetTempPath()) ("mios-client-config-" + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($script:root) | Out-Null
        $stamp = 'test'
        $changed = [Collections.Generic.List[string]]::new()
        $skippedClients = [Collections.Generic.List[string]]::new()
    }
    AfterEach { Remove-Item -LiteralPath $script:root -Recurse -Force -ErrorAction SilentlyContinue }

    It 'reads an absent, empty, blank or null file as an empty object' {
        $cases = [ordered]@{absent=$null; empty=''; blank=" `r`n`t "; null='null'}
        foreach ($case in $cases.Keys) {
            $path = Join-Path $script:root "$case.json"
            if ($null -ne $cases[$case]) { New-MiosFile $path $cases[$case] }
            $value = Read-MiosJson $path
            $value | Should -BeOfType ([Collections.IDictionary]) -Because $case
            $value.Count | Should -Be 0 -Because $case
        }
        $path = Join-Path $script:root 'object.json'
        New-MiosFile $path '{"mcpServers":{"other":{"command":"x"}}}'
        (Read-MiosJson $path)['mcpServers']['other']['command'] | Should -BeExactly 'x'
    }

    It 'refuses a JSON array, a scalar or malformed text, naming the file and leaving it unchanged' {
        foreach ($text in @('[{"mcpServers":{}}]', '[]', '5', '"text"', 'true', '{ "mcpServers": ', 'not json')) {
            $path = Join-Path $script:root 'client.json'
            New-MiosFile $path $text
            { Read-MiosJson $path } | Should -Throw -ExceptionType ([IO.InvalidDataException]) -ExpectedMessage "$path *left unchanged" -Because $text
            [IO.File]::ReadAllText($path) | Should -BeExactly $text
        }
    }

    It 'reads a list file as its entries and refuses an object where a list belongs' {
        # As production uses it: assigned, then enumerated, never $null (a $null piped into Where-Object would index it).
        $path = Join-Path $script:root 'keybindings.json'
        $list = Read-MiosJsonList $path
        $null -eq $list | Should -BeFalse
        @($list | Where-Object { $_['key'] }).Count | Should -Be 0
        New-MiosFile $path ''
        $list = Read-MiosJsonList $path
        $null -eq $list | Should -BeFalse
        @($list | Where-Object { $_['key'] }).Count | Should -Be 0
        New-MiosFile $path '[{"key":"ctrl+a","command":"x"}]'
        $list = Read-MiosJsonList $path
        $list.Count | Should -Be 1
        $list[0]['key'] | Should -BeExactly 'ctrl+a'
        New-MiosFile $path '{"key":"ctrl+a"}'
        { Read-MiosJsonList $path } | Should -Throw -ExceptionType ([IO.InvalidDataException]) -ExpectedMessage "$path does not hold a JSON array*"
    }

    It 'treats a null section as missing, keeps an existing one and refuses any other value' {
        $object = @{present=@{other=1}; empty=$null; list=@(1); text='x'}
        $created = Get-MiosJsonSection $object 'absent' 'cfg.json'
        $created['k'] = 1
        $object['absent']['k'] | Should -Be 1
        (Get-MiosJsonSection $object 'empty' 'cfg.json') | Should -BeOfType ([Collections.IDictionary])
        $object['empty'] | Should -BeOfType ([Collections.IDictionary])
        (Get-MiosJsonSection $object 'present' 'cfg.json')['other'] | Should -Be 1
        foreach ($key in @('list', 'text')) {
            { Get-MiosJsonSection $object $key 'cfg.json' } | Should -Throw -ExceptionType ([IO.InvalidDataException]) -ExpectedMessage "cfg.json: $key is not a JSON object*"
        }
    }

    It 'skips a refused MCP client with a warning, still writes the clients after it, and names it in the summary' {
        $savedProfile = $env:USERPROFILE
        $savedAppData = $env:APPDATA
        $env:USERPROFILE = Join-Path $script:root 'home'
        $env:APPDATA = Join-Path $script:root 'appdata'
        try {
            $home_ = $env:USERPROFILE
            New-MiosFile (Join-Path $home_ '.claude.json') '{"projects":{"a":1},"mcpServers":{"other":{"command":"keep"}}}'
            New-MiosFile (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json') '{"mcpServers":null}'
            $refused = Join-Path $home_ '.gemini\settings.json'
            New-MiosFile $refused '[]'
            $blank = Join-Path $home_ '.gemini\config\mcp_config.json'
            New-MiosFile $blank ''
            $malformed = Join-Path $home_ '.gemini\antigravity-cli\mcp_config.json'
            New-MiosFile $malformed '{"mcpServers": {'
            $mcpEntry = @{command='wsl.exe'; args=@('-d', 'podman-MiOS-DEV')}
            $warnings = $null
            . $script:mcpLoop 3>&1 | Tee-Object -Variable warnings | Out-Null
            $claude = Get-Content -Raw (Join-Path $home_ '.claude.json') | ConvertFrom-Json -AsHashtable
            $claude['mcpServers']['other']['command'] | Should -BeExactly 'keep'
            $claude['mcpServers']['mios-control']['command'] | Should -BeExactly 'wsl.exe'
            $claude['projects']['a'] | Should -Be 1
            (Get-Content -Raw (Join-Path $env:APPDATA 'Claude\claude_desktop_config.json') | ConvertFrom-Json -AsHashtable)['mcpServers']['mios-control'] | Should -Not -BeNullOrEmpty
            # The clients after the refused one are still written.
            (Get-Content -Raw $blank | ConvertFrom-Json -AsHashtable)['mcpServers']['mios-control'] | Should -Not -BeNullOrEmpty
            (Get-Content -Raw (Join-Path $home_ '.cursor\mcp.json') | ConvertFrom-Json -AsHashtable)['mcpServers']['mios-control'] | Should -Not -BeNullOrEmpty
            [IO.File]::ReadAllText($refused) | Should -BeExactly '[]'
            [IO.File]::ReadAllText($malformed) | Should -BeExactly '{"mcpServers": {'
            ($skippedClients -join '|') | Should -BeExactly "$refused|$malformed"
            @($warnings | Where-Object { "$_" -like "*skipped $refused -- $refused does not hold a JSON object*" }).Count | Should -Be 1
            @($warnings | Where-Object { "$_" -like "*skipped $malformed -- $malformed is not valid JSON*" }).Count | Should -Be 1
            $summary = @(. $script:summary 3>&1)
            $summary.Count | Should -Be 1
            "$($summary[0])" | Should -BeLike "*2 client config(s) skipped and left unchanged: $refused; $malformed"
        } finally {
            $env:USERPROFILE = $savedProfile
            $env:APPDATA = $savedAppData
        }
    }

    It 'stays silent when no client was skipped' {
        @(. $script:summary 3>&1).Count | Should -Be 0
    }

    It 'never swallows MiOS''s own failures, only a refused config' {
        { Invoke-MiosClientConfig 'opencode' { throw 'Native local OpenCode projection failed' } } | Should -Throw '*projection failed*'
        $skippedClients.Count | Should -Be 0
    }

    It 'skips a Windows Terminal settings file it cannot extend, and fills one without schemes, actions or profiles' {
        $config = $script:terminalConfig
        $BinDirectory = Join-Path $script:root 'bin'
        $registered = @(); $RuntimeOnly = $false; $engine = 'pwsh.exe'
        $legacy = Join-Path $script:root 'legacy\settings.json'
        New-MiosFile $legacy '{"profiles":[{"name":"cmd"}]}'
        $bare = Join-Path $script:root 'bare\settings.json'
        New-MiosFile $bare '{"theme":"dark"}'
        $terminalPaths = @($legacy, (Join-Path $script:root 'absent\settings.json'), $bare)
        . $script:terminalLoop 3>$null
        [IO.File]::ReadAllText($legacy) | Should -BeExactly '{"profiles":[{"name":"cmd"}]}'
        ($skippedClients -join '|') | Should -BeExactly $legacy
        $terminal = Get-Content -Raw $bare | ConvertFrom-Json -AsHashtable
        $terminal['theme'] | Should -BeExactly 'dark'
        (@($terminal['schemes'] | ForEach-Object { $_['name'] }) -join '|') | Should -BeExactly 'MiOS'
        @($terminal['actions']).Count | Should -Be 1
        (@($terminal['profiles']['list'] | ForEach-Object { $_['name'] }) -join '|') | Should -BeExactly 'MiOS-CMD'
        $terminal['profiles']['defaults']['colorScheme'] | Should -BeExactly 'MiOS'
        $terminal['compatibility']['enableUnfocusedAcrylic'] | Should -BeTrue
    }
}
