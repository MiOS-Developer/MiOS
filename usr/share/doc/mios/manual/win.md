<!-- AI-hint: Manual pages distilled from the source comments of win, sanitized, each passage anchored to the comment it came from. -->

# win

### MiOS Windows Driver Slipstream & WIM Servicing Engine....

MiOS Windows Driver Slipstream & WIM Servicing Engine.

Injects essential offline connectivity and storage drivers into Windows PE (boot.wim)
and Windows 11 runtime (install.wim):
- Wi-Fi 6E / Wi-Fi 7: Intel AX210/BE200, MediaTek MT7922/RZ616, Realtek RTL8852BE.
- 2.5GbE / 10GbE NICs: Intel I225-V / I226-V, Realtek RTL8125.
- Virtualization: Red Hat VirtIO SCSI, NetKVM, VIOStor, VIORNG, VIOGPU.

Parses .INF driver manifests, enforces digital signature/syntax integrity, and orchestrates
DISM mount / injection / commit lifecycles.

<!-- mios-src:f458307f841c from usr/libexec/mios/win/driver_slipstream.py:5-16 -->

### MiOS Windows PowerShell Execution Policy & Developer Mode...

MiOS Windows PowerShell Execution Policy & Developer Mode Registry Configurator.

Configures:
1. PowerShell ExecutionPolicy: RemoteSigned (enforcing security balance without machine-wide Bypass).
2. Windows Developer Mode: AllowDevelopmentWithoutDevLicense = 1.
3. Win32 Long Paths: LongPathsEnabled = 1.
4. PowerShell $PROFILE setup: UTF-8 encoding, MiOS AI endpoint variables, and WSL proxy functions.

Supports emitting standalone .reg files, .ps1 deployment scripts, and live winreg application.

<!-- mios-src:094c5678eac0 from usr/libexec/mios/win/ps_policy_config.py:5-15 -->

### MiOS Windows 11 Unattended Answer File (autounattend.xml)...

MiOS Windows 11 Unattended Answer File (autounattend.xml) Generator.

Synthesizes complete, schema-compliant autounattend.xml answer files for Windows 11:
- windowsPE: TPM 2.0, SecureBoot, RAM & CPU hardware check bypasses, display settings.
- offlineServicing: Driver search path injection for Wi-Fi and storage controllers.
- specialize: Telemetry disabling, OEM branding, Developer Mode, and Long Paths enablement.
- oobeSystem: Passwordless/auto-logon 'mios' local admin account, OOBE screen bypass,
  and FirstLogonCommands for WSL2/Hyper-V platform initialization.

<!-- mios-src:eb752ef925ca from usr/libexec/mios/win/unattend_gen.py:5-14 -->

### MiOS Windows Unattended Answer File (autounattend.xml)...

MiOS Windows Unattended Answer File (autounattend.xml) Schema Validator.

Validates Windows 10/11 autounattend.xml answer files against official Microsoft SIM
(System Image Manager) XML schema rules and best practices without external C dependencies.
Ensures zero-defect unattended installations, valid pass ordering, strict datatype verification,
and Windows 11 hardware check bypass compliance.

<!-- mios-src:d81e85c6bfcd from usr/libexec/mios/win/unattend_validate.py:5-12 -->

### MiOS Windows Terminal Profile & Color Scheme Injector....

MiOS Windows Terminal Profile & Color Scheme Injector.

Non-destructively updates Windows Terminal settings.json with MiOS development profiles
(WSL2 Dev container, Host SSH loopback, Serial Console) and canonical 'MiOS Dark'
color schemes extracted from mios.toml [colors].

Preserves existing user profiles, custom keybindings, and global terminal preferences.

<!-- mios-src:31d37bab18ca from usr/libexec/mios/win/wt_profile_inject.py:5-13 -->
### MiOS Windows 11 Unattended Answer File (autounattend.xml)...

MiOS Windows 11 Unattended Answer File (autounattend.xml) Generator.

Synthesizes complete, schema-compliant autounattend.xml answer files for Windows 11:
- windowsPE: TPM 2.0, SecureBoot, RAM & CPU hardware check bypasses, display settings.
- offlineServicing: Driver search path injection for Wi-Fi and storage controllers.
- specialize: Telemetry disabling, OEM branding, Developer Mode, and Long Paths enablement.
- oobeSystem: local accounts + persistent AutoLogon resolved from the LAYERED SSOT
  (usr/lib/mios/mios_credentials.py -- fails closed, no literal account/password),
  OOBE screen bypass, and FirstLogonCommands for WSL2/Hyper-V platform initialization.

<!-- mios-src:857643408b79 from usr/libexec/mios/win/unattend_gen.py:5-15 -->
