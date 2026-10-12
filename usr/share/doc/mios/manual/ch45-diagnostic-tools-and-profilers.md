<!-- AI-hint: Chapter 45: Diagnostic Tools and Profilers. Covers physical adapter checks run by system-profilers. Details checks verifying container loopback containment. Explains comparing active setups against templates. -->

# Chapter 45: Diagnostic Tools and Profilers

> Part VII: Build, Test & Upstream Maintenance of the [MiOS manual](../manual.md).

This chapter covers the documentation for **Diagnostic Tools and Profilers** under MiOS.

### <a name="45_hardware_capability_profiling"></a>45.Hardware Capability Profiling: Hardware Capability Profiling

> Path Reference: `/usr/share/doc/mios/manual.md#45_hardware_capability_profiling`

#### Overview

Profiles system capabilities using profiling scripts.

## Operations
- **Profiler**: Executed via [system-profiler.sh](tools/system-profiler.sh).
- **Run tool**: Runs [run-all-profilers.sh](tools/run-all-profilers.sh).
- **Output**: Logs system properties for review.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="45_egress_firewall_verification"></a>45.Egress Firewall Verification: Egress Firewall Verification

> Path Reference: `/usr/share/doc/mios/manual.md#45_egress_firewall_verification`

#### Overview

Validates outbound networking rules.

## Setup
- **Verify tool**: Run `mios-gen egress-firewall --check` using the [native projector](../../../../../tools/native/mios-gen/src/main.rs).
- **Checks**: Audits active rules inside firewall filters.
- **Safety**: Confines network execution blocks.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="45_profile_comparison_utilities"></a>45.Profile Comparison Utilities: Profile Comparison Utilities

> Path Reference: `/usr/share/doc/mios/manual.md#45_profile_comparison_utilities`

#### Overview

Compares configuration states against templates.

## Utilities
- **Script**: Run [profile-compare.sh](tools/profile-compare.sh).
- **Checks**: Scans active configs against reference parameters.
- **Gating**: Detects drift parameters.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.
