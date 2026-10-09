<!-- AI-hint: Verified, source-cited research on building a DISM-native custom Windows 11 ISO that carries MiOS (the Windows-side layer + a WSL2 podman machine). Two synthesized 5-angle research passes: (A) per-MiOS-component -> DISM offline mechanism table + the must-be-first-logon list; (B) reference build patterns (tiny11/winutil DISM sequences), the definitive WSL2-offline verdict, the oscdimg dual-boot recipe, headless GitHub-Actions CI, and LabConfig hardware-bypass. Feeds ROADMAP Part 12 WS-WISO (task_138/task_139) + the mios-bootstrap/field/autounattend suite. -->
<!-- AI-related: https://github.com/mios-dev/mios-bootstrap/tree/main/field/autounattend, ROADMAP.md Part 12, tasks.jsonl -->

# DISM-Native Custom Windows 11 ISO for MiOS — verified research (2026-07-04)

Feeds **ROADMAP Part 12 / WS-WISO** (task_138 UUP-fetch, task_139 DISM+oscdimg+CI) and the
`mios-bootstrap/field/autounattend/` suite. Two source-cited research passes; every claim is ranked
**VERIFIED / Likely / Needs-testing** against Microsoft Learn (and reputable deployment sources where
MS is silent). Servicing frame for every command below:
```
Dism /Mount-Image /ImageFile:C:\media\sources\install.wim /Index:1 /MountDir:C:\mnt
::  ...service C:\mnt (features, drivers, appx, offline hives, files)...
Dism /Unmount-Image /MountDir:C:\mnt /Commit          (PS: Dismount-WindowsImage -Path C:\mnt -Save)
```

## The one governing fact (VERIFIED)
**DISM offline servicing applies ONLY the `offlineServicing` pass** — it services files/drivers/
features/packages/registry inside a volume; it never runs the OS. Two consequences:
1. Features enabled offline land as **"Enable Pending"** — *"you must boot the image to enable the
   feature entirely."* Feature-bit-set ≠ component-functional.
2. Anything needing live OS code (SAM/LSASS, a loaded user hive, the WSL VM, Task Scheduler service,
   DPAPI, SID generation, activation) is **first-boot-only** and cannot be baked.

## Headline verdicts
1. **WSL2 CAN be fully baked offline** (myth: "always needs `wsl --update` online"). Kernel is bundled
   in the WSL app package since 1.0.0 GA; use the **GitHub WSL MSI** + a **distro rootfs `.tar`** +
   a **podman machine-os artifact** (`podman machine init --image <local>`). No piece is *irreducibly*
   online; residual first-logon steps are local (one reboot + tarball import) — IF artifacts pre-staged.
2. **tiny11 standard maker is the cleanest DISM template:** mount → `/Remove-ProvisionedAppxPackage`
   loop → offline-hive `reg add` → `/Cleanup-Image /StartComponentCleanup /ResetBase` → `/Export-Image
   /Compress:recovery` → `oscdimg`. (Avoid tiny11 **Core** — it kills serviceability; MiOS must ADD
   components.)
3. **The pipeline runs headless on GitHub Actions `windows-2025`** (≥3 public repos). Only real
   handling: install oscdimg (not preinstalled) + the ~14 GB disk budget.
4. **oscdimg dual BIOS+UEFI recipe confirmed** vs KB 947024.
5. **LabConfig bypass keys are offline-bakeable** (autounattend windowsPE reg-add, or offline hive
   edit of boot.wim's SYSTEM hive); they gate **Setup only**, inert post-install.

---

## PART A — Component → offline mechanism → exact command → constraint (all 10)

**1. WSL2 / VirtualMachinePlatform / Hyper-V / Containers (optional features)**
- `Dism /Image:C:\mnt /Enable-Feature /FeatureName:VirtualMachinePlatform /All` — the only feature
  strictly required by modern (Store) WSL2. "Enable Pending" → needs one **reboot**. **[Verified]**
- `.../FeatureName:Microsoft-Windows-Subsystem-Linux /All` — flips the lxss bit only (kernel+distro
  separate). **[Verified]**
- `.../FeatureName:Microsoft-Hyper-V-All /All` — `/All` required offline (parent/child). Activation
  needs target-BCD `hypervisorlaunchtype auto` + reboot + SLAT/VT firmware; **not on Home**; conflicts
  with live VMware/VBox. **[Verified enable / Likely finalize]**
- `.../FeatureName:Containers /All` — process-isolation only; Hyper-V-isolated containers also need
  Hyper-V. **[Verified/Likely]**
- **The WSL2 3-part gotcha:** (a) optional feature = bakeable bit; (b) **kernel is NOT a feature** —
  ships as `wsl_update_x64.msi` (legacy) or bundled in the Store MSIX; stage as provisioned Appx or a
  first-boot file; (c) **distro registration is per-user** (`HKCU\...\Lxss\{GUID}` + `ext4.vhdx` at
  first launch) — cannot pre-register for a not-yet-existing user.

**2. podman CLI** — binaries: file-copy into `<mnt>\Program Files\...` **[Verified]**. `podman machine
init/start` = **first-boot only** (it `wsl --import`s a Fedora rootfs into the invoking user's
HKCU\Lxss; needs WSL service + vmcompute). No all-users machine; run in the **target user's logon
context**, never SetupComplete.cmd (SYSTEM). Seed offline via `--image <baked-rootfs>`.

**3. GPU drivers** — `Dism /Image:C:\mnt /Add-Driver /Driver:C:\drivers /Recurse` (`Add-WindowsDriver
-Path C:\mnt -Driver C:\drivers -Recurse`). **INF/SYS/CAT only** — vendor `.exe`/MSI ignored; extract
to INF first. `/ForceUnsigned` for unsigned x64. Binds only if that GPU is present at first boot.
**Adversarial:** does NOT install GeForce App / Adrenalin / Arc Control / CUDA (runtime components).
**[Verified]**

**4. OS-wide branding (offline registry hives).** Load frame (top-level hive name is dropped at load):
```
reg load HKLM\OFF_SW   C:\mnt\Windows\System32\config\SOFTWARE
reg load HKLM\OFF_USER C:\mnt\Users\Default\NTUSER.DAT     :: the REAL new-user template
:: ...edits... then reg unload BOTH before /Unmount-Image /Commit (locked hive fails commit)
```
> **Myth-buster:** `HKU\.DEFAULT` is SYSTEM/logon-desktop, NOT the new-user template. The template is
> `C:\Users\Default\NTUSER.DAT`. (Editing it is the community/MDT method but technically "unsupported";
> the only MS-supported default-profile method is **CopyProfile** via unattend specialize + sysprep.)

| Item | Key (rel. to loaded hive) · value/type | Rank |
|---|---|---|
| OEM info | `OFF_SW\Microsoft\Windows\CurrentVersion\OEMInformation` — Manufacturer/Model/Support*/`Logo`=32-bit ≤120×120 .bmp | Verified |
| Dark mode | `OFF_USER\...\Themes\Personalize` — `AppsUseLightTheme`=0, `SystemUsesLightTheme`=0 | Verified |
| Accent | `OFF_USER\...\Explorer\Accent` (`AccentPalette` 32-byte, `*ColorMenu`=**ABGR**) + `...\DWM` (`AccentColor`=ABGR, `ColorizationColor`=ARGB) + `Personalize\ColorPrevalence`=1. **ABGR vs ARGB byte order = #1 red/blue-swap bug.** Raw-registry bake is unreliable → prefer unattend `<WindowColor>` (ARGB) or a `.theme` at first logon | Verified values / fragile bake |
| Wallpaper | `OFF_USER\Control Panel\Desktop` — `WallPaper`=path, `WallpaperStyle`=`10`(fill), `TileWallpaper`=0 | Verified |
| Lockscreen | `OFF_SW\...\PersonalizationCSP` — `LockScreenImageStatus`=1 + `LockScreenImagePath`/`Url`=same path (cross-SKU); kill Spotlight in `OFF_USER\...\ContentDeliveryManager` | Verified / test on Home |
| Dynamic Lighting (RGB) | `OFF_USER\Software\Microsoft\Lighting` — `AmbientLightingEnabled`, `UseSystemAccentColor`=1 (**undocumented names**); per-device color NOT seedable offline | fragile |

> **Killer caveat:** first-logon provisioning **resets a class of HKCU settings copied from Default**
> (accent, Dynamic Lighting). Durable fix: seed `OFF_USER\...\RunOnce` with a `reg import` that
> re-writes them **after** provisioning finishes.

**5. Geist font + Bibata cursor (files + registry).**
- Font: copy `Geist-*.ttf` → `<mnt>\Windows\Fonts`; register `OFF_SW\Microsoft\Windows NT\
  CurrentVersion\Fonts` `"Geist Regular (TrueType)"`=`"Geist-Regular.ttf"` (one value/face — copy alone
  is NOT enough). **[Verified mechanism; face-name strings Needs-testing]**
- Substitution: `...\FontSubstitutes` `"Segoe UI"="Geist"` — **only reskins legacy GDI/Win32**
  (Explorer classic dialogs, RegEdit). **WinUI3/XAML (Settings/Start/taskbar/toasts) hardcode `Segoe UI
  Variable` and ignore it.** **NEVER** substitute `Segoe MDL2 Assets`/`Segoe Fluent Icons` → tofu icons.
- Cursor: copy `.cur`/`.ani` → `<mnt>\Windows\Cursors\Bibata\`; catalog in machine `...\Schemes`; set
  **active** per-user in `OFF_USER\Control Panel\Cursors` (15 role values as REG_EXPAND_SZ). Active
  scheme is HKCU → goes in Default hive (or first-logon); logon-screen cursor needs `HKU\.DEFAULT` too.

**6. M:\ layout + Linux tree** — folders inside C: bake fine (`MkDir C:\mnt\...`). A **real `M:\`
drive is first-boot only** — DISM has no partition/drive-letter primitive (letters assigned at boot
from the volume GUID). Provision via windowsPE `DiskConfiguration`; `subst M: C:\MiOS` is a stopgap.

**7. mios CLI** — straight file-copy into `<mnt>\Program Files\MiOS\`; PATH via offline SYSTEM hive.
**[Verified]**

**8. MiOS-Autostart scheduled task** — **do NOT hand-inject offline.** A task lives in `<mnt>\Windows\
System32\Tasks\<name>` (XML) **AND** `HKLM\SOFTWARE\...\Schedule\TaskCache\{Tasks,Tree}` (binary +
SDDL + a `Hash` = **SHA-256 of the XML, BOM excluded**); mismatch → `SCHED_E_INVALID_TASK_HASH`
(0x80041321) and the task is dropped. **Register at first boot** via `schtasks /create /xml` /
`Register-ScheduledTask -Xml` in SetupComplete.cmd (machine) or FirstLogonCommands (per-user).

**9. Local offline accounts** — **truly impossible offline** (DISM=offlineServicing; SAM `F`/`V`
records + RID + SID + `HKU\<SID>` hive + password-DPAPI-MasterKey all built by live code at first
logon; `chntpw` only edits existing accounts). Declare in `autounattend.xml` oobeSystem
`UserAccounts\LocalAccounts\LocalAccount` (+ `OOBE\HideOnlineAccountScreens=true`). **Do NOT rely on
`BypassNRO` — being removed (~Insider 26200.5516); pin the target build.**

**10. Debloat + provisioning Appx** —
- List: `Dism /Image:C:\mnt /Get-ProvisionedAppxPackages`.
- Remove: `.../Remove-ProvisionedAppxPackage /PackageName:<full>` (new profiles only = correct on a
  fresh image). **Edge (Win32/MSI) not removable this way; removing Store breaks winget.**
- Add (WSL MSIX / App Installer / VCLibs): `.../Add-ProvisionedAppxPackage /PackagePath:app.appxbundle
  /DependencyPackagePath:VCLibs.x64.appx /DependencyPackagePath:VCLibs.x86.appx /LicensePath:License.xml`
  — deps each via `/DependencyPackagePath`; on x64 ship **both** x64+x86 deps; Store-signed needs
  `/LicensePath`. **Adversarial:** provisioning ≠ working — **winget is NOT usable at first boot**
  (async per-user registration + `msstore` agreement); fix at first logon with `Add-AppxPackage
  -Register ...AppXManifest.xml` + `winget source reset --force`.

**Cross-cutting offline mechanisms:** Updates `Dism /Add-Package /PackagePath:<LCU+SSU .msu>` (21H2+
combined msu — add directly). Capabilities `Dism /Add-Capability /Source:<FoD ISO> /LimitAccess`
(**offline REQUIRES `/Source`; silent failure — verify** with `Get-WindowsCapability`). Unattend
`/Apply-Unattend` applies **only offlineServicing** — copy the file to `<mnt>\Windows\Panther\
unattend.xml` for specialize/oobe passes to run at boot. `SetupComplete.cmd` runs as **SYSTEM** at end
of Setup — **disabled under retail/OEM keys except Enterprise/Server** (real MiOS risk — verify
edition/key), wrong context for per-user work.

## PART B — must be FIRST-LOGON (not DISM-bakeable)
Local account creation (SAM/SID/DPAPI) · WSL distro registration (per-user HKCU\Lxss) · `podman
machine init` (per-user WSL import) · WSL2 kernel install + `--set-default-version 2` · real `M:\`
drive (diskpart) · MiOS-Autostart task (hash validation) · winget usability · Store-app per-user
registration · GPU vendor runtimes (GeForce App/Adrenalin/Arc/CUDA) · accent + Dynamic-Lighting
persistence (provisioning reset) · activation/licensing · machine SID (sysprep specialize) · per-user
DPAPI secrets. Mechanisms: `autounattend.xml` oobeSystem + `FirstLogonCommands`, per-user RunOnce /
Active Setup, SetupComplete.cmd (machine/SYSTEM only).

## PART C — reference build patterns (VERIFIED from raw source)
- **tiny11 standard maker** (`ntdevlabs/tiny11builder tiny11maker.ps1`): Appx `/Remove-Provisioned
  AppxPackage` loop over a prefix list; **no `/Remove-Capability`/`/Disable-Feature`** (keeps
  serviceability); Edge/OneDrive by raw file-delete; LabConfig+MoSetup offline-hive writes to **both**
  install.wim and boot.wim idx2; `/Cleanup-Image /StartComponentCleanup /ResetBase` → `Dismount -Save`
  → `/Export-Image /Compress:recovery` → oscdimg. **Naming myth-buster:** winutil MicroWin refactored
  into `functions/private/Invoke-WinUtilISO.ps1` + `Invoke-WinUtilISOScript.ps1` (the old
  `Invoke-MicroWin.ps1`/`Remove-Features.ps1` don't exist on `main`). winutil is lighter (Appx+registry
  only) and shows the **driver-add** pattern + staging first-logon scripts into `C:\Windows\Setup\
  Scripts\` inside the WIM.
- **WSL2 offline (definitive):** enable VMP+lxss features; bake the **GitHub WSL MSI** (`msiexec /i
  wsl.2.x.x64.msi /qn` — NOT the legacy `wsl_update_x64.msi` trap); `wsl --import <Distro> <path>
  <rootfs.tar>`; `podman machine init --image <local machine-os>`. **Uncertain:** exact `--image`
  local-path form + zero-network guarantee → validate on an air-gapped VM.
- **oscdimg (dual boot, vs KB 947024):** `oscdimg -m -o -h -u2 -udfver102 -l<LABEL> -bootdata:2#p0,e,b
  <root>\boot\etfsboot.com#pEF,e,b<root>\efi\microsoft\boot\efisys.bin <root> out.iso`. `-u2`=UDF-only
  (Win11 wim >4 GB); swap `efisys_noprompt.bin` for zero-touch; add `-yo<order.txt>` if UEFI boot fails
  on real hardware (>4.5 GB images). oscdimg ships in the ADK "Deployment Tools" feature.
- **Headless CI:** GitHub Actions `windows-2025` (admin + UAC disabled → DISM mount works; DISM inbox,
  no Hyper-V needed; Defender passive). Reference: `kelexine/tiny11-automated`, `rgl/uup-dump-get-
  windows-iso`, `marcinmajsc/uup-dump-build-and-get-windows-iso`. Install oscdimg (choco/winget/
  symbol-server curl); work on `D:`; `timeout-minutes: 360`; split Release assets ~1.95 GB.
- **LabConfig bypass:** `HKLM\SYSTEM\Setup\LabConfig` REG_DWORD=1 `BypassTPMCheck`/`BypassSecureBoot
  Check`/`BypassRAMCheck`/`BypassStorageCheck`/`BypassCPUCheck` (+ `MoSetup\AllowUpgradesWith
  UnsupportedTPMOrCPU` for in-place). Offline via autounattend windowsPE `<RunSynchronous>` (Schneegans
  emits it) or offline hive edit of **boot.wim**. Gate **Setup only**; install.wim copy persists as
  dead unread data. **Uncertain:** 24H2/25H2 new Setup UI — validate on target build.

## Build blueprint (MiOS)
Fetch (UUP-dump/aria2, headless) → mount install.wim → Appx-strip (tiny11 loop, keep serviceability)
→ **bake offline:** enable VMP+lxss; stage WSL MSI + distro rootfs + podman machine-os; drop
first-logon scripts (msiexec wsl.msi, `wsl --import`, `podman machine init --image`); offline-hive
branding (OEM/dark/accent-with-RunOnce-backstop/wallpaper/lockscreen) + fonts + cursors into
`Users\Default\NTUSER.DAT`; LabConfig+MoSetup; `dism /Add-Driver /Recurse`; declare accounts +
MiOS-Autostart-registration + M:\ carve in `autounattend.xml` (oobeSystem/FirstLogonCommands, Panther)
→ `/Cleanup-Image /ResetBase` → `Dismount -Save` → `/Export-Image /Compress:recovery` → oscdimg →
CI on `windows-2025`.

## Validation gaps to close before trusting the bake
1. Does the target **edition/key** let `SetupComplete.cmd` run? (else move first-boot logic to unattend
   `FirstLogonCommands`/`RunSynchronousCommand`.)
2. Do **accent + Dynamic Lighting** survive first-logon provisioning without a RunOnce backstop; exact
   **Geist** face-name strings + **Bibata** scheme resolution after a real boot.
3. `podman machine init --image <local>` accepts the path form + makes zero network calls (air-gapped
   VM); PersonalizationCSP lockscreen on **Home**; modern Store WSL needs only VMP on the target 26xxx.
