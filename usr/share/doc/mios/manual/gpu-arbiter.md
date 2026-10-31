<!-- AI-hint: Dynamic GPU Arbiter manual: state machine, vLLM sleep mode coordination, sysfs driver binding, and QMP device hotplug between AI workloads and gaming guest. -->
<!-- AI-related: usr/libexec/mios/gpu_arbiter.py, usr/share/containers/systemd/mios-llm-heavy.container, usr/share/mios/mios.toml, /run/mios/gpu-state -->

# Dynamic GPU Arbiter (`gpu_arbiter.py`)

## 1. Overview & Architectural Problem

On bare-metal MiOS workstations and blades equipped with a single discrete high-performance GPU (e.g. NVIDIA RTX), the operator requires two mutually exclusive workloads:
1. **Heavy Local AI Inference**: vLLM (`mios-llm-heavy`) running large reasoning models with extensive KV-cache VRAM allocation.
2. **High-Performance Windows Gaming**: A dedicated virtual machine (`mios-xbox`) requiring direct, bare-metal GPU performance with Looking Glass frame capture.

Per Invariant 4, GPU fractioning (`mdevctl`/vGPU) cannot run on a driver-free host without proprietary out-of-tree host drivers. The Dynamic GPU Arbiter solves this by arbitrating the physical GPU functions via runtime driver rebinding and QEMU QMP hotplug, eliminating host reboots.

## 2. State Machine Transitions

The arbiter (`usr/libexec/mios/gpu_arbiter.py`) implements a strict 4-state finite state machine:

```
      ┌─────────────────────────────────────────────────────────┐
      │                                                         ▼
┌──────────────┐   request_gaming   ┌────────────────┐   qmp device_add   ┌───────────────┐
│ AI_EXCLUSIVE │ ─────────────────> │ DRAINING_SLEEP │ ─────────────────> │  VM_ATTACHED  │
└──────────────┘                    └────────────────┘                    └───────────────┘
      ▲                                                                           │
      │                                                                           │ release_gpu
      │                        ┌──────────────┐      qmp device_del               │
      └─────────────────────── │ RESTORING_AI │ <─────────────────────────────────┘
           vllm /wake_up       └──────────────┘
```

### State 1: `AI_EXCLUSIVE`
- Discrete GPU functions (`0000:01:00.0` VGA, `0000:01:00.1` Audio) are bound to the host `nvidia` kernel driver.
- vLLM container (`mios-llm-heavy`) is running and active on `http://127.0.0.1:8520`.
- State file `/run/mios/gpu-state` indicates `held_by: "ai"`.

### State 2: `DRAINING_SLEEP`
- Triggered by gaming guest activation or operator request (`gpu_arbiter.py request-gaming`).
- The arbiter sends `POST /sleep` to vLLM on port 8520 to flush and release GPU VRAM allocations.
- Fallback: If vLLM sleep is unresponsive, executes `podman pause mios-llm-heavy`.
- Unbinds device addresses from `/sys/bus/pci/drivers/nvidia/unbind`.
- Binds device addresses to `/sys/bus/pci/drivers/vfio-pci/bind`.

### State 3: `VM_ATTACHED`
- Connects to the QMP socket (`/var/run/libvirt/qemu/mios-xbox.qmp`).
- Negotiates `qmp_capabilities`.
- Executes `device_add` for `gpu_vga` (`driver=vfio-pci, host=01:00.0, bus=pcie.1`).
- Executes `device_add` for `gpu_audio` (`driver=vfio-pci, host=01:00.1, bus=pcie.1`).
- Gaming guest acquires hardware acceleration; Looking Glass stream begins.

### State 4: `RESTORING_AI`
- Triggered on gaming guest shutdown or release request (`gpu_arbiter.py release-gaming`).
- Executes QMP `device_del` for `gpu_audio` and `gpu_vga`.
- Unbinds from `vfio-pci` and rebinds to `nvidia`.
- Sends `POST /wake_up` to vLLM to reload model weights, or executes `podman unpause mios-llm-heavy`.
- Returns to `AI_EXCLUSIVE`.

## 3. Configuration & State Publishing

Configuration resolves from `[metal.gpu]` in `mios.toml`:
- `domain`: Target libvirt gaming domain (default `mios-xbox`).
- `pci_devices`: List of PCI addresses (default `["0000:01:00.0", "0000:01:00.1"]`).
- `qmp_socket`: Domain QMP control socket path.
- State output: Atomic update to `/run/mios/gpu-state` (primary) and `/run/mios-gpu-state` (secondary).
