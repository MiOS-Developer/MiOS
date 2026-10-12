<!-- AI-hint: Chapter 37: GPU Capability Detection and Passthrough Shims. Covers spec updates triggered when hardware states change. Details device locking and lockouts during state transitions. Explains dynamic module load decisions during bootstrap. -->

# Chapter 37: GPU Capability Detection and Passthrough Shims

> Part VII: Build, Test & Upstream Maintenance of the [MiOS manual](../manual.md).

This chapter covers the documentation for **GPU Capability Detection and Passthrough Shims** under MiOS.

### <a name="37_cdi_refresh_mechanisms"></a>37.CDI Refresh Mechanisms: CDI Refresh Mechanisms

> Path Reference: `/usr/share/doc/mios/manual.md#37_cdi_refresh_mechanisms`

#### Overview

Refreshes CDI specs automatically when graphics adapters change.

## Setup
- **Checks**: Scans physical devices on boot using [20-hardware.sh](automation/20-hardware.sh).
- **Utility**: Invokes [26-nvidia-cdi-refresh.sh](automation/26-nvidia-cdi-refresh.sh).
- **Execution**: Updates container CDI files in `/var/run/cdi/`.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="37_runtime_gpu_gating"></a>37.Runtime GPU Gating: Runtime GPU Gating

> Path Reference: `/usr/share/doc/mios/manual.md#37_runtime_gpu_gating`

#### Overview

Gating mechanisms control GPU resource allocations between containers and hypervisors.

## Gating
- **Detection**: The [GPU detection service](../../../../lib/systemd/system/mios-gpu-pv-detect.service) invokes [gpu-pv-detect](../../../../libexec/mios/gpu-pv-detect) for the runtime capability check.
- **Locking**: Locks device files to prevent parallel utilization conflicts.
- **Policies**: Shunts GPU compute priorities to virtual guests.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="37_dynamic_driver_loading"></a>37.Dynamic Driver Loading: Dynamic Driver Loading

> Path Reference: `/usr/share/doc/mios/manual.md#37_dynamic_driver_loading`

#### Overview

Loads host display drivers based on profile settings.

## Flow
- **Checks**: Verifies system variables at boot.
- **Action**: Loads target GPU drivers or binds cards to VFIO.
- **Integrity**: Enforces signed drivers validation.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.
