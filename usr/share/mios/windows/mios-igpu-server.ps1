# AI-hint: Powershell script that hosts a llama.cpp Vulkan-backend inference server (or rpc-server) on Windows to provide a persistent, low-latency micro-LLM for the MiOS daemon and agent pipeline.
# AI-doc: usr/share/doc/mios/manual/windows.md
<#
  mios-igpu-server.ps1  --  MiOS iGPU inference server (Windows host)

  WHY THIS EXISTS
  ---------------
  The MiOS dev VM is WSL2. WSL2 only exposes the GPU via /dev/dxg (the
  DirectX paravirt node). NVIDIA bridges dxg -> CUDA via its WSL driver, so
  the RTX 4090 works *inside* the VM's ollama container. AMD/Intel have no
  such dxg->compute bridge in WSL2: there is no /dev/kfd (ROCm) and no
  /dev/dri (Vulkan) in the VM, so the in-VM "iGPU" ollama (ollama:rocm at
  :11435) silently runs on CPU ("offloaded 0/29 layers to GPU").

  The only way to actually use the AMD iGPU is to run the inference server
  NATIVELY on Windows, where the iGPU has a real driver + a Vulkan ICD, and
  expose it over localhost (127.0.0.1) under WSL2 mirrored networking per
  Architectural Law 5 (MIOS_AI_ENDPOINT). This script is that server: llama.cpp's
  OpenAI-compatible `llama-server` (or `rpc-server`) on the VULKAN backend
  (Vulkan supports AMD + Intel iGPUs; ROCm-on-Windows usually does NOT support
  integrated Radeon).

  The MiOS swarm node local-igpu is pointed at:
  http://127.0.0.1:8540/v1 (see mios.toml [nodes.local-igpu]).

  USAGE
  -----
    pwsh -File mios-igpu-server.ps1                 # run in foreground on 127.0.0.1:8540
    pwsh -File mios-igpu-server.ps1 -Mode Rpc       # run rpc-server for cross-lane sharding
    pwsh -File mios-igpu-server.ps1 -Install        # register a Windows service (persistent, hidden)
    pwsh -File mios-igpu-server.ps1 -Uninstall      # remove the service
    pwsh -File mios-igpu-server.ps1 -Model C:\path\to\model.gguf

  First run needs internet ONCE if binaries need fetching; local GGUF models in
  the MiOS WSL distro (\\wsl$\<distro>\var\lib\mios\llamacpp\models\, the
  distro resolved from the Lxss registry or passed as -Distro) are detected and
  used automatically without downloading.
#>
[CmdletBinding()]
param(
    [ValidateSet('Server', 'Rpc')]
    [string] $Mode        = 'Server',
    [int]    $Port        = 8540,
    [string] $Model       = '',
    # The iGPU's ROLE is the ALWAYS-ON LIGHT-COMPUTE BRAIN (
    # "iGPU SHOULD BE THE MICRO LLM ... AND the always-on MiOS daemon background
    # agent"): it hosts the micro-LLM (router/refine/judge/web-expand, hit every
    # turn) + the mios-daemon-agent, so it is NEVER cold and the dGPU/CPU are
    # freed. It is NOT a heavy reasoning agent (it is ~7 tok/s -- too slow for big
    # facets). So serve a SMALL fast instruct GGUF, not the old 3B. Override with
    # -Model / -ModelUrl for a different micro/daemon brain (e.g. a Qwen3-1.7B
    # GGUF to match the daemon model exactly).
    [string] $ModelUrl    = 'https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf',
    # 64K ctx (iGPU is now ALSO the Hermes-desktop FRONT DOOR
    # via mios-model-router's mios-orchestrator lane). The front door must hold the
    # full ~17K-token MCP tool surface (113 tools) that Hermes sends EVERY turn --
    # at the old 8192 the tool defs were TRUNCATED, the model never saw open_app,
    # and it answered in prose (the recurring "FAILURE"). 65536 also matches the
    # router-advertised ctx + Hermes's 64K floor. KV for a 1.5B at 64K is ~1.9 GB
    # on the iGPU's shared system RAM -- cheap. (ctx-size only sizes the KV pool;
    # it does NOT slow prefill -- prefill cost scales with the ACTUAL prompt len.)
    [int]    $ContextSize = 65536,
    # Single inference slot (KV-paging). llama-server defaults
    # to 4 parallel slots, which (a) splits ctx-size 4 ways (16384 each) and (b)
    # makes the OpenAI /v1 endpoint land a request on ANY slot, so the agent-pipe's
    # per-slot KV save/restore (_kv_paging, slot 0) can't deterministically bracket
    # it. ONE slot = the full 65536 ctx + every request lands on slot 0, so demand-
    # paging the conversation's KV to/from disk is reliable. The iGPU front door
    # processes one user turn at a time anyway; delegated children that round-robin
    # back onto the iGPU simply queue, which is fine.
    [int]    $Parallel    = 1,
    [int]    $GpuLayers   = 99,            # 99 = offload all layers to the iGPU
    # Pin to a SINGLE Vulkan device so llama.cpp does NOT layer-split onto the
    # RTX 4090 (Vulkan also enumerates the 4090, and GPU-PV shares it with the
    # WSL VM where hermes runs -- spilling onto it would steal hermes's VRAM).
    # Vulkan device ENUMERATION ORDER IS NOT STABLE across processes (operator
    # the task-managed server got Vulkan0=RTX 4090 and ran the "iGPU"
    # model on the dGPU at 138 tok/s, stealing hermes's VRAM; standalone
    # --list-devices on the same host showed Vulkan0=AMD). So a fixed index is
    # unreliable. 'auto' (default) resolves the AMD/Radeon device by NAME at
    # launch (see below). Pass an explicit VulkanN to override.
    [string] $Device      = 'auto',
    [switch] $ShowDevices,
    [string] $LlamaTag    = 'latest',      # llama.cpp release tag, or 'latest'
    # MiOS WSL distro whose /var/lib/mios/llamacpp/models is the local GGUF
    # fallback. Empty = resolved below, never assumed by name.
    [string] $Distro      = '',
    [switch] $Install,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

if (-not $PSBoundParameters.ContainsKey('Port') -and $env:MIOS_PORTS_LLM_IGPU) {
    $Port = [int]$env:MIOS_PORTS_LLM_IGPU
}

[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
$root      = Join-Path $env:ProgramData 'mios\igpu'
$binDir    = Join-Path $root 'bin'
$modelsDir = Join-Path $root 'models'
$logDir    = Join-Path $root 'logs'
# KV-cache paging store ("VRAM can compress or write to disk
# ... clean state when agents/models load/unload"): --slot-save-path below makes
# llama-server expose POST /slots/{id}?action=save|restore, which writes a
# conversation's KV cache to a .bin in THIS dir and restores it near-instantly.
# The in-VM agent-pipe demand-pages per conversation against it (_kv_paging).
$slotDir   = Join-Path $root 'slots'
$exe       = Join-Path $binDir 'llama-server.exe'
$rpcExe    = Join-Path $binDir 'rpc-server.exe'
if (-not (Test-Path $rpcExe) -and (Test-Path (Join-Path $binDir 'ggml-rpc-server.exe'))) {
    $rpcExe = Join-Path $binDir 'ggml-rpc-server.exe'
}
$taskName  = 'MiOS-iGPU-Server'

function Info($m){ Write-Host "  [*] $m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "  [+] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }

# ---- resolve the MiOS WSL distro generatively ---------------------------------
# The shared Resolve-MiosDistro when the MiOS globals are loaded; otherwise the
# Lxss registry walk it was lifted from (wsl.exe -l emits UTF-16 that mangles
# under the default console encoding), preferring a distro that carries the
# MiOS product. Resolved here, in the operator's session, so -Install can pin it
# for the service, whose own HKCU is not the operator's.
if (-not $Distro) {
    if (Get-Command Resolve-MiosDistro -ErrorAction SilentlyContinue) {
        $Distro = Resolve-MiosDistro
    } else {
        $Distro = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue |
            ForEach-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DistributionName } |
            Where-Object { $_ -match 'MiOS' }) | Select-Object -First 1
    }
}

# ---- low-power GPU routing (DirectX UserGpuPreferences) ----------------------
function Ensure-MiosGpuPreferences {
    param(
        [string[]]$TargetExes
    )
    $hives = @('HKCU:\Software\Microsoft\DirectX\UserGpuPreferences')
    if (Test-Path 'Registry::HKEY_USERS') {
        Get-ChildItem 'Registry::HKEY_USERS' -ErrorAction SilentlyContinue | ForEach-Object {
            $hives += "Registry::$($_.Name)\Software\Microsoft\DirectX\UserGpuPreferences"
        }
    }

    foreach ($h in $hives) {
        try {
            if (-not (Test-Path $h)) {
                New-Item -Path $h -Force -ErrorAction SilentlyContinue | Out-Null
            }
            foreach ($t in $TargetExes) {
                if ($t) {
                    Set-ItemProperty -Path $h -Name $t -Value 'GpuPreference=1;' -Type String -Force -ErrorAction SilentlyContinue
                }
            }
        } catch { }
    }
}

# ---- service install / uninstall -------------------------------------
if ($Uninstall) {
    # Delete old scheduled task if it exists
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

    # Stop and remove Windows Service
    if (Get-Service -Name $taskName -ErrorAction SilentlyContinue) {
        Stop-Service -Name $taskName -Force -ErrorAction SilentlyContinue
        sc.exe delete $taskName | Out-Null
        Ok "removed Windows Service '$taskName'"
    }
    # Clean up wrapper files
    $targetExeWrapper = Join-Path $PSScriptRoot "$taskName.exe"
    $targetCfg = Join-Path $PSScriptRoot "$taskName.cfg"
    Remove-Item $targetExeWrapper -Force -ErrorAction SilentlyContinue
    Remove-Item $targetCfg -Force -ErrorAction SilentlyContinue
    # Clean up legacy firewall rules if present
    Remove-NetFirewallRule -DisplayName "MiOS - igpu-llm ($Port/tcp)" -ErrorAction SilentlyContinue | Out-Null
    return
}
if ($Install) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Warn 'Not elevated -- re-launching via UAC to register the service...'
        $elevatedArgs = @(
            '-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath,'-Install',
            '-Mode',$Mode,'-Port',$Port,'-ContextSize',$ContextSize,'-GpuLayers',$GpuLayers,'-Device',$Device)
        if ($Distro) { $elevatedArgs += @('-Distro',$Distro) }
        Start-Process -FilePath 'pwsh.exe' -Verb RunAs -ArgumentList $elevatedArgs
        return
    }

    # Delete old scheduled task
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

    # Resolve concrete interpreter path
    $psExe = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
    if (-not $psExe -or $psExe -like '*\WindowsApps\*' -or -not (Test-Path $psExe)) {
        $psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    }
    $argsStr = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Mode $Mode -Port $Port -ContextSize $ContextSize -GpuLayers $GpuLayers -Device $Device"
    if ($Model -and $Mode -ne 'Rpc') { $argsStr += " -Model `"$Model`"" }
    if ($Distro -and $Mode -ne 'Rpc') { $argsStr += " -Distro `"$Distro`"" }

    $targetExeWrapper = Join-Path $PSScriptRoot "$taskName.exe"
    $targetCfg = Join-Path $PSScriptRoot "$taskName.cfg"
    $wrapperSrc = Join-Path $PSScriptRoot "MiosServiceTool.exe"

    if (-not (Test-Path $wrapperSrc)) {
        throw "Wrapper tool source not found: $wrapperSrc"
    }

    # Copy wrapper and create configuration file
    Copy-Item $wrapperSrc $targetExeWrapper -Force
    $cfgContent = "$psExe`r`n$argsStr"
    Set-Content -Path $targetCfg -Value $cfgContent -Encoding Utf8

    # Register Windows Service
    if (Get-Service -Name $taskName -ErrorAction SilentlyContinue) {
        Stop-Service -Name $taskName -Force -ErrorAction SilentlyContinue
        sc.exe delete $taskName | Out-Null
        Start-Sleep -Seconds 1
    }

    $svcDisplayName = if ($Mode -eq 'Rpc') { "MiOS iGPU RPC Server" } else { "MiOS iGPU Server" }
    New-Service -Name $taskName -BinaryPathName "`"$targetExeWrapper`"" -DisplayName $svcDisplayName -StartupType Automatic | Out-Null

    Ok "registered Windows Service '$taskName' (mode: $Mode, port: $Port)"
    Info "starting it now..."
    Start-Service -Name $taskName
    return
}

# ---- ensure dirs ------------------------------------------------------------
foreach ($d in @($root,$binDir,$modelsDir,$logDir,$slotDir)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

function Test-SHA256Integrity {
    param([string]$FilePath, [string]$ExpectedSha256)
    if (-not (Test-Path $FilePath)) { return }
    $actualHash = (Get-FileHash -Path $FilePath -Algorithm SHA256).Hash.ToLower()
    if ($ExpectedSha256 -and $ExpectedSha256.Trim() -ne '') {
        if ($actualHash -ne $ExpectedSha256.ToLower()) {
            throw "SHA256 verification failed for $FilePath! Expected: $ExpectedSha256, Actual: $actualHash. Refusing untrusted artifact."
        }
        Ok "SHA256 verified: $FilePath ($actualHash)"
    } else {
        Info "SHA256 checksum for ${FilePath}: $actualHash"
    }
}

# ---- ensure llama.cpp Vulkan binaries ---------------------------------------
$targetBinary = if ($Mode -eq 'Rpc') { $rpcExe } else { $exe }
if (-not (Test-Path $targetBinary)) {
    Info "binary not found ($targetBinary) -- fetching llama.cpp Vulkan release..."
    $headers = @{ 'User-Agent' = 'mios-igpu-server' }
    if ($LlamaTag -eq 'latest') {
        # Query releases array to bypass empty tags like v0.6.0
        $releases = Invoke-RestMethod -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=20' -Headers $headers
        $matchedRelease = $null
        $asset = $null
        foreach ($r in $releases) {
            $candidate = $r.assets | Where-Object { $_.name -match 'win-vulkan-x64\.zip$' } | Select-Object -First 1
            if ($candidate) {
                $matchedRelease = $r
                $asset = $candidate
                break
            }
        }
        if (-not $asset) { throw "no release with win-vulkan-x64 asset found in recent llama.cpp releases" }
        $rel = $matchedRelease
    } else {
        $relUrl = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$LlamaTag"
        $rel = Invoke-RestMethod -Uri $relUrl -Headers $headers
        $asset = $rel.assets | Where-Object { $_.name -match 'win-vulkan-x64\.zip$' } | Select-Object -First 1
        if (-not $asset) { throw "no win-vulkan-x64 asset in llama.cpp release '$($rel.tag_name)'" }
    }
    $zip = Join-Path $env:TEMP $asset.name
    Info "downloading $($asset.name) ($([math]::Round($asset.size/1MB)) MB)..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -Headers $headers
    Test-SHA256Integrity -FilePath $zip -ExpectedSha256 $env:MIOS_LLAMA_VULKAN_ZIP_SHA256
    Info 'extracting...'
    Expand-Archive -Path $zip -DestinationPath $binDir -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue

    # Some release zips nest the exe in a subfolder -- flatten if needed.
    $foundServer = Get-ChildItem -Path $binDir -Recurse -Filter 'llama-server.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($foundServer -and $foundServer.DirectoryName -ne $binDir) {
        Copy-Item $foundServer.FullName $binDir -Force
        Get-ChildItem $foundServer.DirectoryName -Filter '*.dll' -ErrorAction SilentlyContinue | Copy-Item -Destination $binDir -Force
    }
    $foundRpc = Get-ChildItem -Path $binDir -Recurse -Filter '*rpc-server.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($foundRpc) {
        Copy-Item $foundRpc.FullName (Join-Path $binDir 'rpc-server.exe') -Force
        $rpcExe = Join-Path $binDir 'rpc-server.exe'
    }

    $targetBinary = if ($Mode -eq 'Rpc') { $rpcExe } else { $exe }
    if (-not (Test-Path $targetBinary)) { throw "$targetBinary not found after extraction in $binDir" }
    Ok "installed llama.cpp Vulkan binaries -> $binDir ($($rel.tag_name))"
}

# Ensure rpc-server.exe is accessible
if (-not (Test-Path $rpcExe)) {
    $foundRpc = Get-ChildItem -Path $binDir -Recurse -Filter '*rpc-server.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($foundRpc) {
        Copy-Item $foundRpc.FullName (Join-Path $binDir 'rpc-server.exe') -Force
        $rpcExe = Join-Path $binDir 'rpc-server.exe'
    }
}

# ---- list Vulkan devices and exit (to pick the right -Device) ---------------
if ($ShowDevices) {
    if ($Mode -eq 'Rpc') {
        & $rpcExe --device ?
    } else {
        & $exe --list-devices
    }
    return
}

# ---- ensure a model (Server mode only) --------------------------------------
# No MiOS distro registered: the WSL fallback is skipped, never guessed.
$wslModelsDir = if ($Distro) { "\\wsl$\$Distro\var\lib\mios\llamacpp\models" } else { $null }
if ($Mode -ne 'Rpc') {
    if ($Model -and -not (Test-Path $Model)) {
        if (Test-Path (Join-Path $modelsDir $Model)) {
            $Model = Join-Path $modelsDir $Model
        } elseif ($wslModelsDir -and (Test-Path (Join-Path $wslModelsDir $Model))) {
            $Model = Join-Path $wslModelsDir $Model
        }
    }

    if (-not $Model) {
        # 1. Local Windows models dir
        $existing = Get-ChildItem -Path $modelsDir -Filter '*.gguf' -ErrorAction SilentlyContinue |
            Where-Object { $_.Length -gt 0 } | Select-Object -First 1
        if ($existing) {
            $Model = $existing.FullName
            Ok "using existing model in models dir: $Model"
        } elseif ($wslModelsDir -and (Test-Path $wslModelsDir)) {
            # 2. Local WSL models fallback (granite-4.1-8b.gguf, lfm2-700m.gguf)
            $wslCandidates = @('lfm2-700m.gguf', 'granite-4.1-8b.gguf')
            foreach ($c in $wslCandidates) {
                $candidatePath = Join-Path $wslModelsDir $c
                if (Test-Path $candidatePath) {
                    $Model = $candidatePath
                    Ok "using local WSL model: $Model"
                    break
                }
            }
            if (-not $Model) {
                $anyWsl = Get-ChildItem -Path $wslModelsDir -Filter '*.gguf' -ErrorAction SilentlyContinue |
                    Where-Object { $_.Length -gt 0 } | Select-Object -First 1
                if ($anyWsl) {
                    $Model = $anyWsl.FullName
                    Ok "using local WSL model: $Model"
                }
            }
        }

        # 3. Remote download fallback
        if (-not $Model) {
            $Model = Join-Path $modelsDir (Split-Path $ModelUrl -Leaf)
            Info "no local GGUF present -- downloading default model ($(Split-Path $ModelUrl -Leaf))..."
            Invoke-WebRequest -Uri $ModelUrl -OutFile $Model
            Test-SHA256Integrity -FilePath $Model -ExpectedSha256 $env:MIOS_QWEN_GGUF_SHA256
            Ok "model -> $Model"
        }
    }
    if (-not (Test-Path $Model)) { throw "model not found: $Model" }
}

# ---- ensure DirectX Low-Power GPU Preference (GpuPreference=1;) -------------
Ensure-MiosGpuPreferences @($exe, $rpcExe, (Join-Path $binDir 'ggml-rpc-server.exe'))
Ok "registered DirectX low-power GPU preference (GpuPreference=1;) in UserGpuPreferences"

# ---- resolve the AMD iGPU device by NAME (enumeration order is unstable) -----
# CRITICAL: Vulkan device INDICES are not stable across
# processes, so a fixed --device Vulkan0 sometimes pinned the RTX 4090 and ran
# the "iGPU" model on the dGPU (138 tok/s, stealing hermes's VRAM). Resolve the
# index by NAME here, in the SAME process context that will launch the server
# (so the enumeration it sees matches), picking the AMD/Radeon device and NEVER
# an NVIDIA one. `--list-devices` prints e.g. "  Vulkan1: AMD Radeon(TM) Graphics
# (..)". Only runs for -Device auto; an explicit VulkanN is honoured as-is.
if ($Device -eq 'auto') {
    $devTxt = if ($Mode -eq 'Rpc') {
        (& $rpcExe --device ? 2>&1 | Out-String)
    } else {
        (& $exe --list-devices 2>&1 | Out-String)
    }
    $hit = [regex]::Matches($devTxt, '(?im)^\s*(Vulkan\d+)\s*:\s*(.+?)\s*(\(|$|\r|\n)') |
           Where-Object { $_.Groups[2].Value -match '(?i)AMD|Radeon' -and
                          $_.Groups[2].Value -notmatch '(?i)NVIDIA|GeForce|RTX' } |
           Select-Object -First 1
    if ($hit) {
        $Device = $hit.Groups[1].Value
        Ok "auto-selected iGPU by NAME: $Device = $($hit.Groups[2].Value.Trim())"
    } else {
        $Device = 'Vulkan0'
        Warn "no AMD/Radeon Vulkan device found; falling back to $Device"
        Warn "device list was:`n$devTxt"
    }
}

# ---- run server (pinned to the resolved AMD iGPU device, localhost only) ----
$ErrorActionPreference = 'Continue'

if ($Mode -eq 'Rpc') {
    $logFile = Join-Path $logDir ("rpc-server-{0:yyyyMMdd}.log" -f (Get-Date))
    Info "mode:     Rpc (llama.cpp rpc-server fabric)"
    Info "binding:  127.0.0.1:$Port (localhost loopback per Law 5)"
    Info "GPU:      Vulkan device $Device (AMD iGPU, coopmat disabled)"
    # T-212 / WSL2 Mesa Dozen interop: disable coopmat for Vulkan RPC
    $env:GGML_VK_DISABLE_COOPMAT = '1'

    & $rpcExe `
        --host 127.0.0.1 --port $Port `
        --device $Device `
        2>&1 | Tee-Object -FilePath $logFile
} else {
    $logFile = Join-Path $logDir ("llama-server-{0:yyyyMMdd}.log" -f (Get-Date))
    Info "mode:     Server (OpenAI-compatible /v1/chat/completions)"
    Info "model:    $Model"
    Info "binding:  127.0.0.1:$Port (localhost -> http://127.0.0.1:$Port/v1 per Law 5)"
    Info "GPU:      Vulkan device $Device (resolved by name; AMD iGPU)"
    Info "kv-paging: --slot-save-path $slotDir (agent-pipe pages conversations to/from disk)"

    # CRITICAL: -fit off disables auto-placement so explicit iGPU offload is honoured.
    & $exe `
        --host 127.0.0.1 --port $Port `
        --model $Model `
        --ctx-size $ContextSize `
        --parallel $Parallel `
        --n-gpu-layers $GpuLayers `
        --device $Device `
        -fit off `
        --alias mios-igpu `
        --slot-save-path $slotDir `
        2>&1 | Tee-Object -FilePath $logFile
}
