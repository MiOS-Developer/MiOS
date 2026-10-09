<!-- AI-hint: MiOS Installer Script Family Disambiguation (task_1207). This document defines the roles and scope of each `install.sh` script in the MiOS repository to prevent dangerous confusion between disk-wiping bare-metal installers and non-destructive FHS overlay scripts.
     AI-related: usr/share/mios/mios.toml, docs/adr/, AGY-TASKS.md -->
# MiOS Installer Script Family Disambiguation (task_1207)

This document defines the roles and scope of each `install.sh` script in the MiOS repository to prevent dangerous confusion between disk-wiping bare-metal installers and non-destructive FHS overlay scripts.

## Installer Scripts & Roles

| Script Path | `# MIOS_INSTALLER_ROLE` Marker | Description & Target Environment | Destructive? |
|---|---|---|---|
| `install.sh` | `root-overlay-redirector` | Root redirector forwarding execution to `build-mios.sh` for backward compatibility. | No |
| `tools/install.sh` | `bootc-baremetal-disk-installer` | Offline bare-metal disk installer. Executes `bootc install to-disk --transport oci-archive`. **Note (task_2457):** Disconnected from automated USB/Ventoy kickstarts; requires manual invocation from a live shell. | **YES (Wipes Target Disk)** |
| `automation/install.sh` | `container-build-installer` | Container build installer applying FHS tree during OCI image build. | No |
| `automation/install-fhs.sh` | `fhs-overlay-installer` | FHS overlay installer applying `/usr`, `/etc`, `/var` onto non-bootc Fedora hosts. | No |

## Invariant enforcement

> [!WARNING]
> **Deployment Status Realities (task_2457)**:
> `tools/install.sh` provides the `bootc install to-disk` mechanism, but automated bare-metal deployment from Ventoy/USB media currently defaults to standard Fedora kickstart installation rather than executing `tools/install.sh`. Automatic bootc image deployment remains disconnected and unbuilt.

The drift check `check_installer_family_roles` in `automation/98-drift-checks.sh` enforces that:
1. Every `*install*.sh` script declares a valid `# MIOS_INSTALLER_ROLE=<role>` marker.
2. All declared roles across the repo are unique (zero collisions).
