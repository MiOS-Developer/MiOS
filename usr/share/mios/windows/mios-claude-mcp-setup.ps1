# AI-hint: Configures Claude Desktop and Claude Code to connect to the MiOS MCP server by dynamically resolving WSL distro names and ports to enable remo...
# AI-doc: usr/share/doc/mios/manual/windows.md
<#
  mios-claude-mcp-setup.ps1 -- wire the MiOS MCP server (remote control +
  dispatch: the full MiOS verb catalog -> agent-pipe /v1/dispatch) into BOTH
  Anthropic desktop clients so EVERY chat has it on:

    * Claude Code   -> ~/.claude.json  (top-level mcpServers = user/global
                       scope = every project + chat) via native WSL stdio.
    * Claude Desktop -> %APPDATA%\Claude\claude_desktop_config.json via a
                       version-independent stdio bridge (wsl.exe spawns the
                       MiOS MCP server inside the distro on demand).

  Operator binding 2026-05-29: "make sure Claude Code and Claude Desktop have
  remote control and dispatch on for every chat!!"

  The WSL distro, unprivileged account and SDK path resolve at runtime.
  Port is retained for compatibility; native clients use stdio. Re-running
  refreshes the entry without disturbing other servers. Run it again any time a
  client config gets reset.
#>
param(
  [string]$Distro = $null,
  [int]$Port = 0,
  [string]$ServerName = 'mios-control'
)
$ErrorActionPreference = 'Stop'

# ---- resolve the WSL distro generatively from the registry -------------------
# (wsl.exe -l emits UTF-16 that mangles under the default console encoding ->
# "p" instead of "podman-MiOS-DEV"; the Lxss registry is clean + null-free.)
# Prefer a distro whose name carries the MiOS product (that's where the MiOS
# MCP server lives). A non-MiOS default distro cannot serve the native component.
if (-not $Distro) {
  $lxss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
  $all = @(Get-ChildItem $lxss -ErrorAction SilentlyContinue |
           ForEach-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DistributionName } |
           Where-Object { $_ })
  $Distro = ($all | Where-Object { $_ -match 'MiOS' } | Select-Object -First 1)
}
if (-not $Distro) { throw "could not resolve a WSL distro from the Lxss registry" }

# ---- resolve SDK and account inside the native MiOS runtime ------------------
$registered = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object { (Get-ItemProperty $_.PSPath).DistributionName })
if ($Distro -notin $registered) {
  if ("podman-$Distro" -in $registered) { $Distro = "podman-$Distro" }
  elseif ($Distro.StartsWith('podman-') -and $Distro.Substring(7) -in $registered) { $Distro = $Distro.Substring(7) }
  else { throw 'The requested MiOS distro is not registered' }
}
$LinuxUser = (& wsl.exe -d $Distro -u root -- python3 -c 'import pwd;print(pwd.getpwuid(1000).pw_name)').Trim()
if ($LASTEXITCODE -ne 0 -or -not $LinuxUser) { throw 'Could not resolve the MiOS UID 1000 account' }
$resolve = 'import sys;sys.path.insert(0,"/usr/lib/mios");import mios_toml;print(mios_toml.load_merged()["mcp"]["python"])'
$python = (& wsl.exe -d $Distro -u $LinuxUser -- python3 -c $resolve).Trim()
if ($LASTEXITCODE -ne 0 -or -not $python.StartsWith('/')) { throw 'Could not resolve the native MCP SDK path' }
$nativeArgs = @('-d', $Distro, '-u', $LinuxUser, '--', $python, '/usr/libexec/mios/mios-mcp-server')
Write-Host "MiOS MCP setup: distro=$Distro user=$LinuxUser native stdio server='$ServerName'"

# ---- helper: load JSON file into an ordered hashtable (preserve unknown keys) -
function Read-JsonObj($path) {
  if (Test-Path $path) {
    $raw = Get-Content -Raw -Path $path
    if ($raw.Trim()) { return ($raw | ConvertFrom-Json) }
  }
  return [pscustomobject]@{}
}
function Ensure-Prop($obj, $name, $value) {
  if ($obj.PSObject.Properties.Name -contains $name) { $obj.$name = $value }
  else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

# ================= Claude Code  (~/.claude.json, stdio transport) ============
$ccPath = Join-Path $env:USERPROFILE '.claude.json'
$cc = Read-JsonObj $ccPath
if (-not ($cc.PSObject.Properties.Name -contains 'mcpServers') -or $null -eq $cc.mcpServers) {
  Ensure-Prop $cc 'mcpServers' ([pscustomobject]@{})
}
$ccEntry = [pscustomobject]@{ type = 'stdio'; command = 'wsl.exe'; args = $nativeArgs }
Ensure-Prop $cc.mcpServers $ServerName $ccEntry
if (Test-Path $ccPath) { Copy-Item $ccPath "$ccPath.mios.bak" -Force }
($cc | ConvertTo-Json -Depth 100) | Set-Content -Path $ccPath -Encoding UTF8
Write-Host "  [Claude Code]    +$ServerName (native stdio) -> $ccPath"

# ============ Claude Desktop  (claude_desktop_config.json, stdio) ============
$cdDir  = Join-Path $env:APPDATA 'Claude'
$cdPath = Join-Path $cdDir 'claude_desktop_config.json'
if (-not (Test-Path $cdDir)) { New-Item -ItemType Directory -Force -Path $cdDir | Out-Null }
$cd = Read-JsonObj $cdPath
if (-not ($cd.PSObject.Properties.Name -contains 'mcpServers') -or $null -eq $cd.mcpServers) {
  Ensure-Prop $cd 'mcpServers' ([pscustomobject]@{})
}
$cdEntry = [pscustomobject]@{
  command = 'wsl.exe'
  args    = $nativeArgs
}
Ensure-Prop $cd.mcpServers $ServerName $cdEntry
if (Test-Path $cdPath) { Copy-Item $cdPath "$cdPath.mios.bak" -Force }
($cd | ConvertTo-Json -Depth 100) | Set-Content -Path $cdPath -Encoding UTF8
Write-Host "  [Claude Desktop] +$ServerName (stdio via wsl.exe) -> $cdPath"

Write-Host "Done. Restart Claude Desktop to pick up the new server; Claude Code picks it up on the next session."
