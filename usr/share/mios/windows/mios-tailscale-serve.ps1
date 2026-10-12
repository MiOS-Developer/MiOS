# AI-hint: Configures Tailscale's `serve` functionality on Windows to expose internal WSL2 MiOS services via tailnet-only HTTPS/TCP URLs by mapping local ...
# AI-doc: usr/share/doc/mios/manual/windows.md
<#
  mios-tailscale-serve.ps1  --  stand up tailnet-only pretty-HTTPS URLs for the
  LIVE user-facing MiOS services, idempotently.

  WHY THIS LIVES ON WINDOWS
  -------------------------
  Tailscale runs on the Windows HOST (there is no tailscaled in the WSL2 VM).
  The MiOS services run IN the VM but are reachable here at 127.0.0.1:<port> via
  WSL2 localhost-forwarding. So `tailscale serve` on the host can proxy them onto
  the tailnet. The script serves ONLY ports that are actually LISTENING, so a
  service that isn't running is skipped (no dead mappings).

  METHOD (-Method)
  ----------------
    tcp   (default) -- `--tls-terminated-tcp=<port> tcp://127.0.0.1:<port>`.
                       TLS terminates at Tailscale; raw TCP forwards to the local
                       service. Works on ANY port (the operator's prior, verified
                       approach -- `--https` arbitrary-port support has been flaky
                       across versions).
    https           -- `--https=<port> http://127.0.0.1:<port>`. HTTP-aware proxy.
                       Use if you prefer it / if your version allows any HTTPS port.

  PREREQS (operator does these; this script does NOT bring Tailscale up)
  ---------------------------------------------------------------------
    1. Tailscale UP with the SAFE flags (these prevent the host-wide DNS freeze):
         tailscale up --accept-dns=false --accept-routes=false
    2. In the admin console enable HTTPS certificates + MagicDNS:
         https://login.tailscale.com/admin/dns
    3. Clients reaching the URLs need MagicDNS on (same tailnet).

  USAGE
    pwsh -File mios-tailscale-serve.ps1               # reset + (re)serve all live services
    pwsh -File mios-tailscale-serve.ps1 -DryRun       # show what it WOULD do, change nothing
    pwsh -File mios-tailscale-serve.ps1 -Method https # use --https instead of --tls-terminated-tcp
    pwsh -File mios-tailscale-serve.ps1 -Off          # tear down (tailscale serve reset)
#>
[CmdletBinding()]
param(
    [ValidateSet('tcp','https')] [string] $Method = 'tcp',
    [switch] $DryRun,
    [switch] $Off
)

$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) { $ts = (Get-Command tailscale.exe -ErrorAction SilentlyContinue).Source }
if (-not $ts) { throw "tailscale.exe not found (install Tailscale for Windows)" }

# Dot-source mios-common.ps1 if Get-MiosSsotValue isn't available yet
if (-not (Get-Command Get-MiosSsotValue -ErrorAction SilentlyContinue)) {
    $commonScript = Join-Path $PSScriptRoot '..\..\..\installation\mios-common.ps1'
    if (Test-Path $commonScript) { . $commonScript }
}

function Get-PortFromSsot([string]$envVar, [string]$key, [int]$default) {
    $envVal = [System.Environment]::GetEnvironmentVariable($envVar)
    if ($envVal -and $envVal -match '^\d+$') { return [int]$envVal }
    if (Get-Command Get-MiosSsotValue -ErrorAction SilentlyContinue) {
        $ssotVal = Get-MiosSsotValue -Section 'ports' -Key $key -Default $default
        if ($ssotVal -and $ssotVal -match '^\d+$') { return [int]$ssotVal }
    }
    return $default
}

# Last-resort defaults MUST equal mios.toml [ports]; check_ps_port_fallback_ssot
# in 98-drift-checks.sh fails the gate if any literal here drifts from the SSOT.
$openWebuiPort = Get-PortFromSsot 'MIOS_PORTS_OPEN_WEBUI' 'open_webui' 8200
$hermesPort    = Get-PortFromSsot 'MIOS_PORTS_HERMES' 'hermes' 8720
$agentPipePort = Get-PortFromSsot 'MIOS_PORTS_AGENT_PIPE' 'agent_pipe' 8700
$hermesDashPort= Get-PortFromSsot 'MIOS_PORTS_HERMES_DASHBOARD' 'hermes_dashboard' 8210
$forgePort     = Get-PortFromSsot 'MIOS_PORTS_FORGE_HTTP' 'forge_http' 8400
$cockpitPort   = Get-PortFromSsot 'MIOS_PORTS_COCKPIT' 'cockpit' 8110
$adguardPort   = Get-PortFromSsot 'MIOS_PORTS_ADGUARD_UI' 'adguard_ui' 8050
$guacPort      = Get-PortFromSsot 'MIOS_PORT_GUACAMOLE_WEB' 'guacamole_web' 8220
$searxngPort   = Get-PortFromSsot 'MIOS_PORTS_SEARXNG' 'searxng' 8800
$codePort      = Get-PortFromSsot 'MIOS_PORTS_CODE_SERVER' 'code_server' 8900
$ttydBashPort  = Get-PortFromSsot 'MIOS_PORTS_TTYD_BASH' 'ttyd_bash' 8310
$ttydPwshPort  = Get-PortFromSsot 'MIOS_PORTS_TTYD_POWERSHELL' 'ttyd_powershell' 8320
$cephPort      = Get-PortFromSsot 'MIOS_PORTS_CEPH_DASHBOARD' 'ceph_dashboard' 8460

$SERVICES = @(
    @{ port=$openWebuiPort;  name='open-webui';  label='Open WebUI' }
    @{ port=$hermesPort;     name='hermes';      label='Hermes-Agent (/v1 + agent)' }
    @{ port=$agentPipePort;  name='agent-pipe';  label='agent-pipe (/v1 MiOS-Agent)' }
    @{ port=$hermesDashPort; name='hermes-dash'; label='Hermes Dashboard' }
    @{ port=$forgePort;      name='forgejo';     label='Forgejo (git web)' }
    @{ port=$cockpitPort;    name='cockpit';     label='Cockpit (host console)' }
    @{ port=$adguardPort;    name='adguard';     label='AdGuard Home UI' }
    @{ port=$guacPort;       name='guacamole';   label='Guacamole (browser desktop)' }
    @{ port=$searxngPort;    name='searxng';     label='SearXNG' }
    @{ port=$codePort;       name='code-server'; label='code-server (VS Code)' }
    @{ port=$ttydBashPort;   name='ttyd-bash';   label='ttyd (bash)' }
    @{ port=$ttydPwshPort;   name='ttyd-pwsh';   label='ttyd (PowerShell)' }
    @{ port=$cephPort;       name='ceph';        label='Ceph dashboard' }
)

$DISTRO = if (Get-Command Resolve-MiosDistro -ErrorAction SilentlyContinue) { Resolve-MiosDistro } else { 'podman-MiOS-DEV' }

# Liveness is checked IN THE VM, not on Windows 127.0.0.1. The services run in the
# WSL2 VM; a Windows-side TCP probe false-positives when a Windows process shadows
# a port (e.g. http.sys holds :8443, so a Windows probe "sees" it but it forwards to
# nothing real). We count only ports the VM binds on 0.0.0.0 / * / [::] (loopback-
# only VM ports aren't WSL-forwarded to Windows, so Tailscale can't reach them).
function Get-VmServedPorts {
    try {
        # No awk (its $4 mangles through PS->wsl->bash quoting) -- parse in PowerShell.
        $raw = & wsl.exe -d $DISTRO -u root -- bash -lc 'ss -ltn' 2>$null
        $set = @{}
        foreach ($line in $raw) {
            # local address column comes first; match a non-loopback bind + its port.
            if ($line -match '(?:0\.0\.0\.0|\*|\[::\]):(\d+)\b') { $set[[int]$matches[1]] = $true }
        }
        if ($set.Count -gt 0) { return $set }
    } catch {}
    return $null
}
$VmPorts = Get-VmServedPorts
function Test-WinReachable([int]$p) {
    try {
        $c = [Net.Sockets.TcpClient]::new()
        $h = $c.BeginConnect('127.0.0.1', $p, $null, $null)
        $ok = $h.AsyncWaitHandle.WaitOne(800)
        if ($ok) { $c.EndConnect($h) }
        $c.Close(); return $ok
    } catch { return $false }
}
function Test-Port([int]$p) {
    # Serve only if BOTH: a real VM service (not a Windows shadow like http.sys:8443
    # or a stale :9119) AND reachable from Windows 127.0.0.1 (where tailscale serve
    # forwards; some VM ports like AdGuard :3053 aren't WSL-forwarded). Either alone
    # serves a dead mapping. If the VM probe is unavailable, fall back to Win-only.
    if ($null -ne $VmPorts -and -not $VmPorts.ContainsKey($p)) { return $false }
    return (Test-WinReachable $p)
}

# Serve needs Tailscale connected. We never bring it up ourselves.
$state = (& $ts status 2>&1) -join "`n"
if ($state -match 'Tailscale is stopped|NoState|Logged out') {
    Write-Host "Tailscale is not connected. First run (SAFE flags -- won't freeze the net):" -ForegroundColor Yellow
    Write-Host "  tailscale up --accept-dns=false --accept-routes=false"
    Write-Host "Then re-run this script." -ForegroundColor Yellow
    return
}

if ($Off) {
    Write-Host "Tearing down all serve config (tailscale serve reset)..."
    if (-not $DryRun) { & $ts serve reset }
    return
}

$dns = ''
try { $dns = ((& $ts status --json 2>$null | ConvertFrom-Json).Self.DNSName).TrimEnd('.') } catch {}
if (-not $dns) { $dns = '<your-node>.<tailnet>.ts.net' }

Write-Host "Idempotent rebuild: resetting existing serve config..." -ForegroundColor Cyan
if (-not $DryRun) { & $ts serve reset 2>&1 | Out-Null }

$served = @()
foreach ($s in $SERVICES) {
    if (-not (Test-Port $s.port)) {
        Write-Host ("  skip   {0,-14} :{1,-5} (not listening)" -f $s.name, $s.port) -ForegroundColor DarkGray
        continue
    }
    if ($Method -eq 'https') { $args = @('serve','--bg',"--https=$($s.port)","http://127.0.0.1:$($s.port)") }
    else                     { $args = @('serve','--bg',"--tls-terminated-tcp=$($s.port)","tcp://127.0.0.1:$($s.port)") }

    if ($DryRun) {
        Write-Host ("  WOULD  tailscale {0}" -f ($args -join ' ')) -ForegroundColor Yellow
        $served += $s; continue
    }
    $out = (& $ts @args 2>&1) -join ' '
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  serve  {0,-14} https://{1}:{2}" -f $s.name, $dns, $s.port) -ForegroundColor Green
        $served += $s
    } else {
        Write-Host ("  FAIL   {0,-14} :{1}  -> {2}" -f $s.name, $s.port, $out) -ForegroundColor Red
        Write-Host ("         (if it's a port-restriction error, re-run with -Method https, or vice-versa)") -ForegroundColor DarkYellow
    }
}

# Portal FRONT DOOR at the BARE address (https://<node>, no port) -> agent-pipe ($agentPipePort),
# which serves the MiOS Portal at GET / (password-gated -> /login). Uses --https (port
# 443 is allowed for --https; HTTP-aware so it forwards X-Forwarded-Proto and the
# portal's login cookie + 303->/login redirect work, unlike raw tls-terminated-tcp).
if (Test-Port $agentPipePort) {
    if ($DryRun) {
        Write-Host ("  WOULD  tailscale serve --bg --https=443 http://127.0.0.1:{0}   (Portal root)" -f $agentPipePort) -ForegroundColor Yellow
    } else {
        $o = (& $ts serve --bg --https=443 ("http://127.0.0.1:{0}" -f $agentPipePort) 2>&1) -join ' '
        if ($LASTEXITCODE -eq 0) {
            Write-Host ("  serve  {0,-14} https://{1}   (Portal front door)" -f 'portal', $dns) -ForegroundColor Green
            $served = @(@{ label='MiOS Portal (root)'; port=443 }) + $served
        } else {
            Write-Host ("  FAIL   portal :443 -> {0}" -f $o) -ForegroundColor Red
        }
    }
} else {
    Write-Host ("  skip   portal         (agent-pipe :{0} not reachable)" -f $agentPipePort) -ForegroundColor DarkGray
}

Write-Host "`n=== tailscale serve status ===" -ForegroundColor Cyan
& $ts serve status 2>&1

Write-Host "`n=== MiOS pretty URLs (tailnet-only; clients need MagicDNS on) ===" -ForegroundColor Cyan
foreach ($s in $served) {
    $u = if ($s.port -eq 443) { "https://$dns" } else { "https://${dns}:$($s.port)" }
    Write-Host ("  {0,-28} {1}" -f $s.label, $u)
}
if (-not $served) { Write-Host "  (none served)" -ForegroundColor DarkGray }
