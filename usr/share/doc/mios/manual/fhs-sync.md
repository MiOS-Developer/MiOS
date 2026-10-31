<!-- AI-hint: Runtime FHS Root Overlay and host-guest topology synchronization engine manual: 18 required FHS directories, Windows host namespace exclusions, inotify/ReadDirectoryChangesW event loops, and mios-fhs-sync.service. -->
<!-- AI-related: usr/share/mios/mios.toml, usr/lib/systemd/system/mios-fhs-sync.service, src/mios-rs/miosd/src/fhs_sync.rs, tests/test-fhs-sync-e2e.py, /run/mios/fhs-sync.json -->

# FHS Root Overlay & Topology Synchronization (`mios-fhs-sync`)

## 1. Core Purpose and Architectural Invariants

MiOS operates under the fundamental premise that `.git` IS `/` (Law 3): the repository root overlay mirrors the deployed Linux Filesystem Hierarchy Standard (Law 1). On dual-boot, hybrid WSL2, or sovereign VHDX topologies, the root filesystem must coexist with host filesystems without collision, namespace leakage, or permission corruption.

`mios-fhs-sync` (implemented in `src/mios-rs/miosd/src/fhs_sync.rs` and managed by `mios-fhs-sync.service`) is the continuous background synchronization daemon that enforces this invariant.

## 2. The 18 Required FHS Directories

The daemon audits and verifies the presence and permissions of the 18 standard FHS root directories:
```
/bin, /dev, /etc, /home, /lib, /lib64, /media, /mnt,
/opt, /proc, /root, /run, /sbin, /srv, /sys, /tmp, /usr, /var
```
On ostree/bootc substrates, `/var` is verified as persistent writable storage, while `/usr` remains read-only and immutable.

## 3. Windows Host Namespace Exclusion Boundary

When operating across shared storage, NTFS volumes, or WSL2 mount boundaries, `is_host_namespace` strictly filters out 14 host-specific paths to protect repository integrity:
- `Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`, `Users`
- `$Recycle.Bin`, `Recovery`, `System Volume Information`, `PerfLogs`
- `Riot Games`, `XboxGames`, `TEMP`, `inetpub`, `Documents and Settings`

Any file modification originating within these namespaces is excluded from overlay synchronization and git index tracking.

## 4. Kernel Event Notification & Daemon Loop

- **Linux Substrate**: Uses kernel `inotify` watchers on root directories, filtering noise and coalescing rapid burst modifications.
- **Windows Substrate**: Employs `ReadDirectoryChangesW` with a >= 64KB kernel notification buffer (`KERNEL_NOTIFICATION_BUFFER_SIZE = 65536`) to prevent event buffer overflow during large builds.
- **State Publishing**: Every sync cycle writes structured telemetry to `/run/mios/fhs-sync.json`, recording:
  - Status (`healthy`, `syncing`, `degraded`)
  - Last sync timestamp and scan duration
  - Total files scanned, added, updated, or skipped
  - Namespace violation count

## 5. Systemd Service Integration

The daemon runs under `usr/lib/systemd/system/mios-fhs-sync.service`:
```ini
[Unit]
Description='MiOS' FHS root overlay & host-guest topology runtime sync daemon
Documentation=man:miosd(8)
After=local-fs.target
Before=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/miosd fhs-sync --daemon --interval 15 --root /
Restart=always
RestartSec=5s
LimitNOFILE=65536
```
