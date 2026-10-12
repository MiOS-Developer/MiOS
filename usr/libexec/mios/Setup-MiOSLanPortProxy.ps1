# AI-hint: Configures Windows Firewall and netsh portproxy rules to map physical NIC ports to local WSL containers, enabling LAN-wide access to MiOS services ...
# AI-doc: usr/share/doc/mios/manual/mios.md

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

param(
    [switch]$AiNode
)

# Self-elevate if not admin.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '  [*] Not elevated. Re-launching via UAC...' -ForegroundColor Yellow
    $psArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath)
    if ($AiNode) { $psArgs += '-AiNode' }
    Start-Process -FilePath 'pwsh.exe' -ArgumentList $psArgs -Verb RunAs
    return
}

# Resolve each forwarded port from the SSOT env (MIOS_PORT_*, exported by the resolver /
# install.env) with the current value as a behavior-preserving fallback -- no hardcoded
# port survives when the env is present. Values mirror mios.toml [ports].
function _MiosPort([string]$EnvName, [int]$Default) {
    $v = [Environment]::GetEnvironmentVariable($EnvName)
    if ($v) { [int]$v } else { $Default }
}

$portMap = @(
    @{ Port = (_MiosPort 'MIOS_PORTS_FORGE_HTTP'       8400); Name = 'forge'       }
    @{ Port = (_MiosPort 'MIOS_PORTS_OPEN_WEBUI'       8200); Name = 'open-webui'  }
    @{ Port = (_MiosPort 'MIOS_PORTS_CODE_SERVER'      8900); Name = 'code-server' }
    @{ Port = (_MiosPort 'MIOS_PORTS_COCKPIT'          8110); Name = 'cockpit'     }
    @{ Port = (_MiosPort 'MIOS_PORTS_LLM_LIGHT'        8500); Name = 'llm-light'   }
    @{ Port = (_MiosPort 'MIOS_PORTS_SEARXNG'          8800); Name = 'searxng'     }
    @{ Port = (_MiosPort 'MIOS_PORTS_HERMES'           8720); Name = 'hermes'      }
    @{ Port = (_MiosPort 'MIOS_PORTS_HERMES_DASHBOARD' 8210); Name = 'dash-ai'     }
    @{ Port = (_MiosPort 'MIOS_PORT_GUACAMOLE_WEB'    8220); Name = 'guacamole'   }
    @{ Port = (_MiosPort 'MIOS_PORTS_CEPH_DASHBOARD'   8460); Name = 'ceph-dash'   }
    @{ Port = (_MiosPort 'MIOS_PORTS_RDP'              8300); Name = 'rdp'         }
)

# Clean up retired legacy portproxy and firewall rules (retired ports, zero hardcodes)
$retiredPorts = @(
    @{ Port = 9090;  Name = 'cockpit' }
    @{ Port = 9119;  Name = 'dash-ai' }
    @{ Port = 8443;  Name = 'ceph-dash' }
    @{ Port = 3389;  Name = 'rdp' }
    @{ Port = 3000;  Name = 'forge' }
    @{ Port = 3030;  Name = 'hermes_workspace' }
    @{ Port = 3033;  Name = 'open-webui' }
)
foreach ($rp in $retiredPorts) {
    & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$rp.Port 2>$null | Out-Null
    Remove-NetFirewallRule -DisplayName "MiOS - $($rp.Name) ($($rp.Port)/tcp)" -ErrorAction SilentlyContinue | Out-Null
}

# 1. iphlpsvc must be running for netsh portproxy.
$svc = Get-Service -Name iphlpsvc -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne 'Running') {
    Write-Host '  [*] Starting IP Helper service...' -ForegroundColor Cyan
    Set-Service -Name iphlpsvc -StartupType Automatic
    Start-Service -Name iphlpsvc
}

# 2. netsh portproxy: listen on 0.0.0.0:PORT, forward to 127.0.0.1:PORT.
foreach ($p in $portMap) {
    $port = $p.Port
    & netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port | Out-Null
    & netsh interface portproxy add    v4tov4 listenaddress=0.0.0.0 listenport=$port connectaddress=127.0.0.1 connectport=$port | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  [+] portproxy 0.0.0.0:{0} -> 127.0.0.1:{0}  ({1})" -f $port, $p.Name) -ForegroundColor Green
    } else {
        Write-Host ("  [!] portproxy add for {0} failed" -f $port) -ForegroundColor Red
    }
}

# 2b. AI-Node Tailscale v4tov6 bridge if requested
if ($AiNode) {
    $llmLightPort = _MiosPort 'MIOS_PORTS_LLM_LIGHT' 8500
    $aiEntryName = "MiOS AI Node - mios-llm-light ($llmLightPort/tcp)"
    $existingAiRule = Get-NetFirewallRule -DisplayName $aiEntryName -ErrorAction SilentlyContinue
    if ($existingAiRule) {
        Set-NetFirewallRule -DisplayName $aiEntryName -Enabled True -Action Allow -ErrorAction SilentlyContinue
    } else {
        New-NetFirewallRule -DisplayName $aiEntryName `
            -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort $llmLightPort -RemoteAddress '100.64.0.0/10' `
            -Profile Any | Out-Null
    }
    & netsh interface portproxy delete v4tov6 listenaddress=0.0.0.0 listenport=$llmLightPort 2>$null | Out-Null
    & netsh interface portproxy add    v4tov6 listenaddress=0.0.0.0 listenport=$llmLightPort `
          connectaddress=::1 connectport=$llmLightPort | Out-Null
    Write-Host ("  [+] v4tov6 portproxy 0.0.0.0:{0} -> [::1]:{0}" -f $llmLightPort) -ForegroundColor Green
}

# 3. Windows Defender Firewall inbound allow rules for each port.
foreach ($p in $portMap) {
    $port = $p.Port
    $name = "MiOS - $($p.Name) ($port/tcp)"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Set-NetFirewallRule -DisplayName $name -Enabled True -Action Allow -Direction Inbound -Profile 'Private,Domain' -ErrorAction SilentlyContinue
        $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort $port -ErrorAction SilentlyContinue
        Write-Host ("  [.] firewall: refreshed '{0}'" -f $name) -ForegroundColor DarkGray
    } else {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile 'Private','Domain' | Out-Null
        Write-Host ("  [+] firewall: created '{0}'" -f $name) -ForegroundColor Green
    }
}

# 4. Show the resulting state + the LAN URLs the operator can hit.
Write-Host ''
Write-Host '--- netsh portproxy table ---' -ForegroundColor Cyan
& netsh interface portproxy show all

Write-Host ''
Write-Host '--- LAN URLs ---' -ForegroundColor Cyan
$wifi = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
         Where-Object { $_.IPAddress -match '^192\.168\.' -or $_.IPAddress -match '^10\.' -or $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.' } |
         Where-Object { $_.InterfaceAlias -notmatch 'Loopback|vEthernet|WSL' } |
         Select-Object -First 1).IPAddress
if (-not $wifi) { $wifi = '<your-windows-ip>' }
foreach ($p in $portMap) {
    $scheme = if ($p.Port -eq 9090) { 'https' } else { 'http' }
    Write-Host ("  {0}://{1}:{2}/   ({3})" -f $scheme, $wifi, $p.Port, $p.Name)
}

Write-Host ''
Write-Host '  Done. Press any key to close this window.' -ForegroundColor Green
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
