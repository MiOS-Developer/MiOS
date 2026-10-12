<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### !/bin/bash AI-hint: Non-critical greenboot check that...

!/bin/bash
AI-hint: Non-critical greenboot check that probes blade reachability per ADR-0016 §8 / task_2241. Records reachability state without triggering a rollback unless blade_reachability_critical = true.
AI-related: /usr/share/mios/mios.toml, /etc/mios/mios.toml, /usr/lib/greenboot/check/required.d/40-mios-ai-plane.sh

<!-- mios-src:f57e4ef958a1 from usr/lib/greenboot/check/wanted.d/70-blade-reachability.sh:1-3 -->

### MiOS Peripheral Hardware Health Evaluator...

==============================================================================
MiOS Peripheral Hardware Health Evaluator ("Degrade-Not-Refuse")

Architectural Invariant:
Peripheral subsystems (Network, Audio, Display/DRM) are evaluated during the
bootc boot cycle under Greenboot wanted.d. If any non-critical peripheral is
missing, misconfigured, or has failed driver initialization, this script
records the degraded state to the systemd journal, persistent log file, and
PostgreSQL hardware_events / hardware_inventory (when reachable).

Under the "degrade-not-refuse" architectural philosophy, this script MUST NEVER
exit non-zero on peripheral degradation or missing hardware. It explicitly
exits 0 so Greenboot promotes the boot deployment rather than triggering an
unwanted system rollback.
==============================================================================

<!-- mios-src:46c86e5b441b from usr/lib/greenboot/check/wanted.d/20-hardware-degrade.sh:6-20 -->
