# AI-hint: Configures a local Podman machine as a MiOS AI node by installing NVIDIA container toolkits, deploying mios-llm-light Quadlets on the resolved SSOT port, opening Tailscale firewall rules, and pulling model weights into VM storage.
# AI-doc: usr/share/doc/mios/manual/windows.md
<#
  mios-ai-node.ps1 -- MiOS Windows AI Node setup

  Turns the local Podman machine into an AI node that mirrors the MiOS AI
  inference stack and registers itself on the Tailscale swarm so MiOS-DEV's
  Hermes can fan out inference tasks to it.

  What it does
  ------------
  1.  Resizes the Podman machine RAM to the requested amount (default 8 GB).
      (WSL machines do not support --cpus; only --memory is changed.)
  2.  Installs nvidia-container-toolkit inside the Fedora machine so the RTX
      GPU is accessible to containers via CDI (same mechanism as MiOS-DEV).
  3.  Generates the CDI spec (nvidia.com/gpu=all device class).
  4.  Creates the mios.network bridge and /var/lib/mios/llamacpp* volume directories.
  5.  Deploys mios-llm-light (port :8500 / SSOT llm_light) as a systemd Quadlet inside the machine.
  6.  Pulls the GGUF weights directly from Hugging Face via curl.
  7.  Opens Tailscale-scoped Windows Firewall rules for the resolved llm_light port.
  8.  Prints the /etc/mios/mios.toml snippet to paste on MiOS-DEV so Hermes
      registers this node as a swarm agent.

  Usage
  -----
    pwsh -File mios-ai-node.ps1                                 # full setup
    pwsh -File mios-ai-node.ps1 -SkipResize                     # skip RAM resize
    pwsh -File mios-ai-node.ps1 -SkipGpu                        # skip NVIDIA CTK
    pwsh -File mios-ai-node.ps1 -SkipModels                     # skip model pulls
    pwsh -File mios-ai-node.ps1 -Models "granite4.1:8b,nomic-embed-text"
    pwsh -File mios-ai-node.ps1 -Uninstall                      # remove Quadlets + fw rules

  First run needs internet for: nvidia-container-toolkit RPM, llama-swap image,
  and model weights. Re-runs are fully offline (idempotent).
#>
[CmdletBinding()]
param(
    [string] $MachineName  = 'podman-machine-default',
    [int]    $MemoryMB     = 8192,
    # Default model set:
    # granite4.1:8b    = primary brain (~5.5 GB)
    # lfm2:700m        = micro CPU/GPU brain (~0.7 GB)
    # nomic-embed-text = embeddings (~0.5 GB)
    [string] $Models       = 'granite4.1:8b,lfm2:700m,nomic-embed-text',
    [switch] $SkipResize,
    [switch] $SkipGpu,
    [switch] $SkipModels,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

$compiledExe = Join-Path $PSScriptRoot '..\..\..\src\mios-ainode\mios-ainode.exe'
if (-not (Test-Path $compiledExe)) {
    $compiledExe = 'C:\MiOS\src\mios-ainode\mios-ainode.exe'
}

$useCompiled = $env:MIOS_MIGRATION_USE_COMPILED_AINODE
if ($null -eq $useCompiled -or $useCompiled -eq '') {
    $useCompiled = 'true'
}

if (($useCompiled -eq 'true' -or $useCompiled -eq '1') -and (Test-Path $compiledExe)) {
    Write-Host "[mios-ai-node] launching compiled binary: $compiledExe"
    & $compiledExe
    exit $LASTEXITCODE
}
$QuadletSrc = Join-Path $PSScriptRoot 'quadlets'

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

$llmLightPort = Get-PortFromSsot 'MIOS_PORTS_LLM_LIGHT' 'llm_light' 8500

function Info($m) { Write-Host "  [*] $m" -ForegroundColor Cyan    }
function Ok($m)   { Write-Host "  [+] $m" -ForegroundColor Green   }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow  }
function Fail($m) { Write-Host "  [X] $m" -ForegroundColor Red; throw $m }

# Model HuggingFace Registry and Aliases
$ModelRegistry = @{
    'granite4.1:8b'    = @{ File = 'granite-4.1-8b.gguf';                  Repo = 'unsloth/granite-4.1-8b-GGUF';                  SrcFile = 'granite-4.1-8b-Q4_K_M.gguf' }
    'lfm2:700m'        = @{ File = 'lfm2-700m.gguf';                      Repo = 'LiquidAI/LFM2-700M-GGUF';                      SrcFile = 'LFM2-700M-Q4_K_M.gguf' }
    'nomic-embed-text' = @{ File = 'embeddinggemma-300m-qat-q8_0.gguf';    Repo = 'ggml-org/embeddinggemma-300m-qat-q8_0-GGUF';    SrcFile = 'embeddinggemma-300m-qat-Q8_0.gguf' }
}

function Resolve-ModelTag($tag) {
    $t = $tag.Trim().ToLower()
    if ($t -eq 'granite4.1:3b' -or $t -eq 'granite4.1:8b' -or $t -eq 'mios-agent' -or $t -eq 'hermes-agent' -or $t -eq 'gemma4:12b') {
        return 'granite4.1:8b'
    }
    if ($t -eq 'lfm2:700m' -or $t -eq 'qwen3:1.7b') {
        return 'lfm2:700m'
    }
    if ($t -eq 'nomic-embed-text') {
        return 'nomic-embed-text'
    }
    return $tag
}

# -- helpers -------------------------------------------------------------------

function Get-TailscaleIP {
    try {
        $ip = (& tailscale ip --4 2>$null).Trim()
        if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { return $ip }
    } catch {}
    $ip = (Get-NetAdapter -ErrorAction SilentlyContinue |
           Where-Object { $_.InterfaceDescription -match 'Tailscale' } |
           Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Select-Object -First 1).IPAddress
    if ($ip) { return $ip }
    $ip = (Get-NetIPAddress -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -match '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.' } |
           Select-Object -First 1).IPAddress
    return $ip
}

# Run a bash script on the machine.
function Invoke-Machine([string]$script) {
    $lf  = $script -replace "`r`n", "`n"
    $enc = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($lf))
    podman machine ssh $MachineName "echo $enc | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) { Fail "Remote script failed (see output above)" }
}

# Write a file into the machine using base64 to avoid CRLF/quoting issues.
function Write-MachineFile([string]$remotePath, [string]$content) {
    $lf  = $content -replace "`r`n", "`n"
    $enc = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($lf))
    podman machine ssh $MachineName "echo $enc | base64 -d > $remotePath"
    if ($LASTEXITCODE -ne 0) { Fail "Failed to write $remotePath on machine" }
}

function Test-MachineCmd([string]$cmd) {
    $lf  = $cmd -replace "`r`n", "`n"
    $enc = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($lf))
    $null = podman machine ssh $MachineName "echo $enc | base64 -d | bash" 2>$null
    return ($LASTEXITCODE -eq 0)
}

# -- uninstall -----------------------------------------------------------------

if ($Uninstall) {
    Info 'Stopping and removing MiOS AI Node Quadlets...'
    Invoke-Machine @'
set +e
systemctl stop   mios-llm-light.service  2>/dev/null || true
systemctl disable mios-llm-light.service 2>/dev/null || true
systemctl stop   mios-ollama.service     2>/dev/null || true
systemctl disable mios-ollama.service    2>/dev/null || true
systemctl stop   mios-ollama-cpu.service 2>/dev/null || true
systemctl disable mios-ollama-cpu.service 2>/dev/null || true
rm -f /etc/containers/systemd/mios-llm-light.container
rm -f /etc/containers/systemd/mios-ollama.container
rm -f /etc/containers/systemd/mios-ollama-cpu.container
rm -f /etc/containers/systemd/mios.network
systemctl daemon-reload
echo "[OK] Quadlets removed"
'@
    foreach ($port in @(11434, 11435, $llmLightPort, 11450)) {
        $name = if ($port -eq $llmLightPort -or $port -eq 11450) { "MiOS AI Node - mios-llm-light ($port/tcp)" } else { "MiOS AI Node - mios-ollama ($port/tcp)" }
        Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Out-Null
        netsh interface portproxy delete v4tov6 listenaddress=0.0.0.0 listenport=$port 2>$null | Out-Null
        Ok "removed firewall rule and portproxy: $name"
    }
    Ok 'Uninstall complete.'
    return
}

# -- preflight -----------------------------------------------------------------

Info 'MiOS AI Node setup starting...'
Info "Target machine: $MachineName"

if (-not (Get-Command podman -ErrorAction SilentlyContinue)) {
    Fail 'podman not found. Install Podman Desktop first: https://podman-desktop.io'
}
$machines = podman machine list --format '{{.Name}}' 2>$null
if ($machines -notcontains $MachineName) {
    Fail "Podman machine '$MachineName' not found."
}

$tsIp = Get-TailscaleIP
if (-not $tsIp) {
    Warn 'Tailscale IP not detected -- using placeholder in summary.'
    $tsIp = '<mios-win-tailscale-ip>'
} else {
    Ok "Tailscale IP: $tsIp"
}

# -- resize machine -------------------------------------------------------------

if (-not $SkipResize) {
    $info   = podman machine inspect $MachineName 2>$null | ConvertFrom-Json
    $curMem = [int]($info.Memory)   # already in MiB
    if ($curMem -lt $MemoryMB) {
        Warn "Machine RAM is ${curMem} MB. Requesting resize to ${MemoryMB} MB..."
        Warn '(WSL2 machines do not support podman machine set --memory; editing .wslconfig instead)'
        $wslCfg = "$env:USERPROFILE\.wslconfig"
        $existing = if (Test-Path $wslCfg) { Get-Content $wslCfg -Raw } else { '' }
        $memGb = [math]::Ceiling($MemoryMB / 1024)
        if ($existing -match '\[wsl2\]') {
            if ($existing -match 'memory\s*=') {
                $existing = $existing -replace 'memory\s*=\s*[^\r\n]+', "memory=${memGb}GB"
            } else {
                $existing = $existing -replace '(\[wsl2\])', "`$1`nmemory=${memGb}GB"
            }
        } else {
            $existing += "`n[wsl2]`nmemory=${memGb}GB`n"
        }
        Set-Content $wslCfg $existing
        podman machine stop $MachineName
        podman machine start $MachineName
        Ok ".wslconfig: memory=${memGb}GB -- machine restarted."
    } else {
        Ok "Machine RAM OK: ${curMem} MB."
    }
} else {
    Info 'Skipping machine resize (-SkipResize).'
}

$state = (podman machine inspect $MachineName 2>$null | ConvertFrom-Json).State
if ($state -ne 'running') {
    Info 'Starting Podman machine...'
    podman machine start $MachineName
}

# -- remove retired containers --------------------------------------------------

Info 'Removing retired Ollama/LLM Light containers if any...'
Invoke-Machine @'
set +e
for cname in mios-ollama mios-ollama-cpu; do
    podman stop "$cname" 2>/dev/null || true
    podman rm   "$cname" 2>/dev/null || true
done
for cname in $(podman ps -a --filter 'ancestor=docker.io/ollama/ollama:latest' --format '{{.Names}}' 2>/dev/null); do
    podman stop "$cname" 2>/dev/null || true
    podman rm   "$cname" 2>/dev/null || true
done
echo "[OK] old container cleanup done"
'@

# -- NVIDIA Container Toolkit --------------------------------------------------

if (-not $SkipGpu) {
    Info 'Checking NVIDIA Container Toolkit...'
    if (-not (Test-MachineCmd 'rpm -q nvidia-container-toolkit')) {
        Info 'Installing nvidia-container-toolkit (this may take a minute)...'
        Invoke-Machine @'
set -euo pipefail
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
    -o /etc/yum.repos.d/nvidia-container-toolkit.repo
dnf install -y nvidia-container-toolkit
echo "[OK] nvidia-container-toolkit installed"
'@
        Ok 'nvidia-container-toolkit installed.'
    } else {
        Ok 'nvidia-container-toolkit already present.'
    }

    Info 'Generating CDI spec...'
    Invoke-Machine @'
set -euo pipefail
mkdir -p /etc/cdi
nvidia-ctk cdi generate \
    --output=/etc/cdi/nvidia.yaml \
    --device-name-strategy=index 2>&1 \
|| {
    echo "[WARN] nvidia-ctk generate failed -- writing fallback WSL2 CDI spec"
    cat > /etc/cdi/nvidia.yaml << 'CDIYAML'
cdiVersion: "0.5.0"
kind: "nvidia.com/gpu"
devices:
  - name: "0"
    containerEdits:
      deviceNodes:
        - path: /dev/dxg
          type: c
      mounts:
        - hostPath: /usr/lib/wsl
          containerPath: /usr/lib/wsl
          options: [ro, bind]
  - name: "all"
    containerEdits:
      deviceNodes:
        - path: /dev/dxg
          type: c
      mounts:
        - hostPath: /usr/lib/wsl
          containerPath: /usr/lib/wsl
          options: [ro, bind]
CDIYAML
}
nvidia-ctk cdi list 2>/dev/null || true
echo "[OK] CDI spec ready"
'@
    Ok 'CDI spec ready.'
} else {
    Info 'Skipping GPU setup (-SkipGpu).'
}

# -- mios.network + volume directories -----------------------------------------

Info 'Creating mios network and volume directories...'
Invoke-Machine @'
set -euo pipefail
podman network exists mios 2>/dev/null || podman network create mios
mkdir -p /var/lib/mios/llamacpp/models /var/lib/mios/llamacpp/slots
chmod -R 0777 /var/lib/mios/llamacpp
echo "[OK] network + volumes ready"
'@

# -- deploy Quadlet files & config ---------------------------------------------

Info 'Deploying Quadlet files...'
Invoke-Machine 'mkdir -p /etc/containers/systemd'

foreach ($f in @('mios.network', 'mios-llm-light.container')) {
    $src = Join-Path $QuadletSrc $f
    if (-not (Test-Path $src)) { Fail "Quadlet source not found: $src" }
    $content = Get-Content $src -Raw
    Write-MachineFile "/etc/containers/systemd/$f" $content
    Ok "deployed: $f"
}

# Clean up old Quadlets
Invoke-Machine @'
rm -f /etc/containers/systemd/mios-ollama.container
rm -f /etc/containers/systemd/mios-ollama-cpu.container
systemctl daemon-reload
'@

# Deploy the model map config
Info 'Deploying mios-llm-light.yaml config...'
$yamlSrc = Resolve-Path (Join-Path $PSScriptRoot '../../llamacpp/mios-llm-light.yaml')
if (-not (Test-Path $yamlSrc)) { Fail "Model map YAML not found: $yamlSrc" }
$yamlContent = Get-Content $yamlSrc -Raw
Write-MachineFile '/var/lib/mios/llamacpp/mios-llm-light.yaml' $yamlContent
Ok 'deployed mios-llm-light.yaml'

# -- pull models ---------------------------------------------------------------

if (-not $SkipModels) {
    $modelList = $Models -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    Info "Pulling $($modelList.Count) model(s): $($modelList -join ', ')"

    foreach ($model in $modelList) {
        $resolved = Resolve-ModelTag $model
        if ($ModelRegistry.ContainsKey($resolved)) {
            $spec = $ModelRegistry[$resolved]
            $file = $spec.File
            $repo = $spec.Repo
            $srcfile = $spec.SrcFile

            Info "Downloading $model ($file) from Hugging Face..."
            Invoke-Machine @"
set -euo pipefail
_url=\"https://huggingface.co/${repo}/resolve/main/${srcfile}\"
if [ ! -s \"/var/lib/mios/llamacpp/models/${file}\" ]; then
    echo \"Downloading ${file}...\"
    curl -fL -C - --retry 3 --max-time 1800 -o \"/var/lib/mios/llamacpp/models/${file}.part\" \"\$_url\"
    if [ -n \"\${MIOS_MODEL_SHA256:-}\" ]; then
        echo \"Verifying SHA256 pin...\"
        echo \"\${MIOS_MODEL_SHA256}  /var/lib/mios/llamacpp/models/${file}.part\" | sha256sum -c -
    fi
    mv -f \"/var/lib/mios/llamacpp/models/${file}.part\" \"/var/lib/mios/llamacpp/models/${file}\"
    echo \"[OK] Complete\"
else
    echo \"${file} already present\"
fi
"@
            Ok "pulled: $model"
        } else {
            Warn "Unknown model tag '$model', no HF mapping in registry. Skipping."
        }
    }

    # Write sentinel file
    Invoke-Machine "touch /var/lib/mios/llamacpp/models/.ready"
    Ok "Sentinels updated."
} else {
    Info 'Skipping model downloads (-SkipModels).'
    Info 'Make sure to place weights in /var/lib/mios/llamacpp/models/ and touch .ready inside the VM.'
}

# -- start Quadlet units -------------------------------------------------------

Info 'Reloading systemd and starting Quadlet units...'
Invoke-Machine @'
set -euo pipefail
systemctl daemon-reload
systemctl start mios-llm-light.service 2>&1 | tail -3 || true
sleep 3
if systemctl is-active --quiet mios-llm-light.service; then
    echo "[OK] mios-llm-light running"
else
    echo "[WARN] mios-llm-light not yet active"
    systemctl status mios-llm-light.service --no-pager -l 2>&1 | tail -12 || true
fi
'@

# -- firewall + portproxy -------------------------------------------------------

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Info 'Configuring Windows Firewall (Tailscale CGNAT 100.64.0.0/10)...'
if ($isAdmin) {
    # Delete old firewall rules
    foreach ($port in @(11434, 11435)) {
        Remove-NetFirewallRule -DisplayName "MiOS AI Node - mios-ollama ($port/tcp)" -ErrorAction SilentlyContinue | Out-Null
        Remove-NetFirewallRule -DisplayName "MiOS AI Node - mios-ollama-cpu ($port/tcp)" -ErrorAction SilentlyContinue | Out-Null
    }

    # Create or update llm_light port firewall rule
    $entry = [pscustomobject]@{ Port = $llmLightPort; Name = "MiOS AI Node - mios-llm-light ($llmLightPort/tcp)" }
    $existing = Get-NetFirewallRule -DisplayName $entry.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Set-NetFirewallRule -DisplayName $entry.Name -Enabled True -Action Allow -ErrorAction SilentlyContinue
        Ok "firewall: refreshed '$($entry.Name)'"
    } else {
        New-NetFirewallRule -DisplayName $entry.Name `
            -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort $entry.Port -RemoteAddress '100.64.0.0/10' `
            -Profile Any -ErrorAction SilentlyContinue | Out-Null
        Ok "firewall: created '$($entry.Name)'"
    }

    # portproxy so Tailscale (Windows-side) can reach into the Podman machine.
    $svc = Get-Service iphlpsvc -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Running') {
        Set-Service iphlpsvc -StartupType Automatic; Start-Service iphlpsvc
    }

    # Delete old proxies
    foreach ($port in @(11434, 11435, 11450)) {
        netsh interface portproxy delete v4tov6 listenaddress=0.0.0.0 listenport=$port 2>$null | Out-Null
    }

    # Configure llm_light proxy
    $port = $llmLightPort
    netsh interface portproxy delete v4tov6 listenaddress=0.0.0.0 listenport=$port 2>$null | Out-Null
    netsh interface portproxy add    v4tov6 listenaddress=0.0.0.0 listenport=$port connectaddress=::1 connectport=$port | Out-Null
    Ok "portproxy 0.0.0.0:${port} -> [::1]:${port}"
} else {
    Warn 'Not elevated -- firewall/portproxy skipped. Re-run as admin or run Setup-MiOSLanPortProxy.ps1.'
}

# -- summary -------------------------------------------------------------------

Write-Host ''
Write-Host '===============================================================' -ForegroundColor Green
Write-Host '  MiOS AI Node setup complete' -ForegroundColor Green
Write-Host '===============================================================' -ForegroundColor Green
Write-Host ''
Write-Host '  Services:' -ForegroundColor Cyan
Write-Host "    mios-llm-light  GPU  :${llmLightPort}   http://${tsIp}:${llmLightPort}/v1"
Write-Host ''
Write-Host '  Add to /etc/mios/mios.toml on MiOS-DEV:' -ForegroundColor Cyan
Write-Host ''
Write-Host "[agents.mios-win]"
Write-Host "endpoint    = `"http://${tsIp}:${llmLightPort}/v1`""
Write-Host 'model       = "granite4.1:8b"'
Write-Host 'role        = "gpu"'
Write-Host 'job         = "RTX 3060 Ti GPU overflow node on MiOS-WIN."'
Write-Host 'default     = false'
Write-Host 'lane        = "gpu"'
Write-Host 'health_gate = true'
Write-Host 'fanout      = true'
Write-Host 'strengths   = ["gpu_inference", "code", "chat", "overflow_compute"]'
Write-Host ''
Write-Host '  Useful commands:' -ForegroundColor Cyan
Write-Host "  Status:  podman machine ssh $MachineName -- systemctl status mios-llm-light"
Write-Host "  Logs:    podman machine ssh $MachineName -- journalctl -u mios-llm-light -f"
Write-Host "  Models:  podman machine ssh $MachineName -- curl -s http://localhost:${llmLightPort}/v1/models"
Write-Host ''
