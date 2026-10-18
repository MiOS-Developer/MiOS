<!-- AI-hint: MiOS-Xbox build plan: gap analysis, operator questions, conformance gate design, resources and risk (2026-10-16). -->
## MiOS-Xbox build plan

The goal file is `/.devloop/GOALS-mios-xbox.md`. Its XB-3 condition settles one earlier open question: no NVIDIA, Intel or AMD driver goes into the image. A post-setup step in the guest detects the GPU and installs the latest driver from the vendor.

**Paths used below**
- **BS** = `/var/lib/mios-build/repos/mios-bootstrap-build`
- **AU** = `BS/field/autounattend`
- **VS** = `/usr/share/mios/mios.toml`

**Main-repo facts I checked for this plan**
- The layered SSOT loader is `usr/lib/mios/mios_toml.py` (`load_merged`).
- Existing Windows tooling is in `usr/libexec/mios/win/`: `unattend_gen.py`, `unattend_validate.py`, `driver_slipstream.py`, `ps_policy_config.py` and `wt_profile_inject.py`.
- An example of a pin test is `tests/test-everything-no-pin.sh`.
- The Windows components are in `usr/share/mios/windows/` (26 entries).
- The main repo has no UUP or DISM code. That pipeline exists only in BS.

## 1) Gap analysis

### XB-1: one spec

| Spec item | Current state | Required change |
|---|---|---|
| Where the spec lives | The authoritative text is `AU/docs/MiOS-Xbox-provisioning-spec.md`. The `[autounattend*]` tables exist only in `BS/mios.toml:1329-1557`. VS has none of them, only `[editions.mios-xbox]` (VS:11891-11900). | Add a `[windows.xbox]` spec tree to VS: preferences, start, xbox, features, drivers, guest_tools, components, settings, users (by reference only) and vm. The configurator expects `autounattend.*` (`configurator/mios.html:2328-2362`), so either bridge to that name or migrate the configurator. |
| Taskbar alignment: centered, `TaskbarAl=1` (spec:34) | Three places produce left alignment: `AU/MiOS-Provision.lib.ps1:278` defaults to `'0'`; `AU/New-MiOSISO.ps1:371` (UserOnce) and `:389` (DefaultUser) hard-code `0`; `AU/MiOS-Xbox.xml:1261` and `:1366` set `/d 0`. The spec's "default flipped" claim is false. | Put the value in the SSOT and remove all three sources. Code must fail when the key is missing, not fall back to a default. |
| Taskbar search hidden, `SearchboxTaskbarMode=0` | The code uses `1` at `lib.ps1:283` and `New-MiOSISO.ps1:375` and `:390`. | Move to SSOT; remove the literals. |
| Start folder row: exactly Personal, Network, File Explorer, Settings | The code also turns on Downloads and Documents (`lib.ps1:293-294`). | Store the list in SSOT and emit it as a `VisiblePlaces` binary. The binary needs a one-time capture (blueprint:62-65). |
| Empty Start grid; no taskbar pins | Uses `ConfigureStartPins []` plus legacy layout XML (`New-MiOSISO.ps1:301`, `:349-358`). There is no `start2.bin`. | Move to SSOT. Add a captured `start2.bin` asset stored per build family. Retire the generator boilerplate. |
| Emoji / touch keyboard | Hard-coded at `New-MiOSISO.ps1:378` and `:393`. | Move to SSOT (`TipbandDesiredVisibility`). |
| `TaskbarDa`, `TaskbarMn`, `ShowTaskViewButton` | Hard-coded at `lib.ps1:280-282`. | Move to SSOT (see Q2). |
| Desktop: no icons, Recycle Bin hidden, no Edge shortcut | Implemented, but driven by code defaults (`lib.ps1:279`, `:303-311`). | Move to SSOT. |
| Living wallpaper, no static image | `New-MiOSISO.ps1:443` and `:650` still use `mios-wallpaper.jpg`. | Use the SSOT value. Bake `mios-wallpaperd.exe` (see XB-3). |
| Legacy generators | `New-MiOSISO.ps1:293-395` (`Set-MiOSCustomDefaults`, called at `:1504`) is Schneegans generator output. `MiOS-Xbox.xml` and `MiOS-Xbox-Merged.xml` are NTLite presets. `unattend_gen.py` duplicates the generator. | Delete or retire all of these. Leave one renderer that reads only the SSOT. |
| Conformance gate | None. The only related check is `tests/test-audit-image-provisioning.py:60`, which asserts that the preference key is absent. | New gate (section 4). |
| Layered SSOT | The pipeline reads one file (`New-MiOSAutounattend.ps1:481-505`). There is no vendor → host → user merge. | Render through `mios_toml.load_merged` into a resolved `xbox-spec.json`. The PowerShell steps read only that file. |

### XB-2: builds new, latest

| Spec item | Current state | Required change |
|---|---|---|
| Image source | The UUP fetch (`AU/mios-uup-fetch.ps1:50-140`) works, but its output (`stock/windows11-uup.iso`, build 29683.1000) was never serviced. Both VMs came from a field ISO of unknown origin. | Port the fetch, conversion and servicing into MiOS code. Resolve the latest Dev build at build time. |
| Full build chain | `Invoke-MiOSImageServicing` (`New-MiOSISO.ps1:1181`) has never run end to end. It needs Windows DISM. | Run DISM inside an SSOT-defined Windows build worker (see the operator questions), then build the ISO with `oscdimg` or `xorriso`. |
| No version pins | Pins exist: the FSE feature IDs (`BS/mios.toml:1417`, `New-MiOSISO.ps1:181`); `stable-virtio` (`New-MiOSISO.ps1:970`, `:1009`; `automation/21-virt.sh:70`); the Looking Glass IDD matched to the client commit (`setup-looking-glass.py:669-687`). | Resolve the latest versions (`latest-virtio/`, `latest-qemu-ga/`, Looking Glass host stable, WinFsp from GitHub) and record them only in `build-manifest.json`. Extend the pins gate to `[windows.xbox]`. |
| Reproducible inputs | The BS tree has 10 to 12 uncommitted files. | Build only from committed MiOS code at HEAD. |

### XB-3: everything in the image; GPU drivers by detection

| Spec item | Current state | Required change |
|---|---|---|
| virtio-win drivers | The code exists (`New-MiOSISO.ps1:963-1045`) but has never run. | Inject offline into `install.wim` and `boot.wim` from latest. |
| QEMU guest agent and SPICE tools | Not baked. `VirtIoGuestTools.ps1` (`New-MiOSISO.ps1:339`) is written but never invoked. | Stage `virtio-win-guest-tools.exe` and the qemu-ga MSI into the image. Install them at specialize or SetupComplete; the probe confirms they are present. |
| Looking Glass IDD and host app | Installed after boot over WinRM (`setup-looking-glass.py:737-803`). | Stage `looking-glass-host` (latest stable) plus the IDD offline and install at SetupComplete. Reuse `mios-looking-glass-idd.ps1` as the installer. |
| GPU driver | Unhooked (`gpu_drivers_dir`, `New-MiOSISO.ps1:1062`, uses `-ForceUnsigned`). | Remove it. Add a new guest step, `mios-gpu-driver.ps1`, that maps PCI vendor and device IDs to the vendor's latest driver, verifies the Authenticode signature, installs, and records the version. Nothing is installed for a vendor that is absent. |
| MiOS components | Only `Set-MiOSWallpaper.ps1` is staged (`New-MiOSISO.ps1:487-497`). The native exes are built (`windows-target/.../release`) but never baked. | Add an SSOT list of components and stage them from the build output into `C:\Program Files\MiOS` plus scheduled tasks. |
| Xbox features | FSE override and DeviceForm (`New-MiOSISO.ps1:176-216`); `StartupToGamingHome` (`New-MiOSAutounattend.ps1:338-347`). Game Mode, Game Bar and Auto HDR have no emitter. The `posture` and `debloat_profile` keys have no consumer. | Drive all of it from SSOT, including the missing ones. |
| Windows optional features | No SSOT list. NTLite toggles in `MiOS-Xbox-Merged.xml:383` and `:392`. | `[windows.xbox.features] enable/disable`, applied with `Enable-WindowsOptionalFeature /Image`. |

### XB-4: users from SSOT

| Spec item | Current state | Required change |
|---|---|---|
| Accounts, passwords, autologon | Hard-coded fallback credentials at `New-MiOSAutounattend.ps1:140-141`, `:161`, `:185`; `lib.ps1:246-252`, `:425`; `New-MiOSISO.ps1:1079`, `:1130`, `:1489`; `MiOS-Provision.ps1:81`; `unattend_gen.py:35-36`, `:68-69`. An empty value does not fail the build. | Read everything from the merged SSOT. An empty value fails the build. **This is the scope of the running mios-ssot-credentials workflow, so wait for it and rebase on its result.** |
| Desktop account | `[[autounattend.accounts]] user` conflicts with blueprint:20. | See Q1. |

### XB-5: launches on the MiOS host

| Spec item | Current state | Required change |
|---|---|---|
| VM definition | `mios-xbox-current` has 8 GiB / 8 vCPU, SATA, rtl8139, no TPM, Secure Boot off, no guest-agent channel, SPICE on port 5900. Templates have `video none` (`Xbox-AutoEnroll.xml:209-211`) and use a pool volume that does not exist (`:87`, `xbox-repair.sh:193`). | Generate a new domain `mios-xbox` from `[vm.win11]` and `[vm.looking_glass]`: 24 GiB / 12 vCPU, q35, Secure Boot OVMF plus swtpm TPM 2.0, Hyper-V enlightenments, virtio-blk or scsi and virtio-net, a qemu-ga channel, SPICE 5930 plus VNC, virtio video, the dGPU at 01:00.0/.1, and 128M IVSHMEM. Stage the ISO into the pool. |
| Launch order | `[vm.looking_glass].domains = ["mios-xbox-current","Xbox"]` (VS:8960) starts the legacy VM first. | Put the new domain first. |
| Console viewers | `virt-viewer` and `remote-viewer` are missing. | Add them to `[packages.virt]` (VS:7232). |

### XB-6: in-guest conformance

There is no in-guest probe today. Add `mios-xbox-probe`, which runs over WinRM using SSOT credentials. It must report green on the new VM and red on `mios-xbox-current`.

### XB-7: in the codebase, gated, merged

The pipeline lives in the separate BS repo, uncommitted. Port it into `/` under `usr/libexec/mios/win/xbox/` and `usr/share/mios/windows/build/`, add the tests, run the gates, then open and merge the PR to main.

## 2) Operator questions (recommended default in brackets)

1. **Q1 Desktop account:** a separate `user` admin account with autologon, or only the renamed RID-500 `mios-sudo`? How is RID-500 re-secured after setup? [Separate desktop account, name from SSOT, with autologon. RID-500 is disabled at `mios-setup-finalize` once autologon is no longer needed.]
2. **Q2 Taskbar extras the spec does not cover:** [`TaskbarDa=0`, `TaskbarMn=0`, `ShowTaskViewButton=1`, all held in SSOT. Emoji/touch through `TipbandDesiredVisibility=1` only; skip `EnableExpressiveInputShellHotkey`.]
3. **Q3 Xbox feature IDs:** only the FSE pair (58989070, 59765208), or the 109 IDs in `mios-xbox-features.txt` too? [Only the FSE pair, held in SSOT as named toggles. Feature IDs are switches, not versions. The 109 IDs stay experimental and are not shipped.]
4. **Q4 Game Mode, Game Bar / GameDVR, Auto HDR:** [Game Mode on, Game Bar on with background capture off, Auto HDR on.]
5. **Q5 Gaming Services and WebView2** are delivered by the Store only. [Allowed as a MiOS-owned first-logon hydrate step that the SSOT declares and the probe verifies. This is a documented exception to XB-3. WebView2 is baked from the latest evergreen offline installer.]
6. **Q6 Optional features:** [Hyper-V off. WSL and VirtualMachinePlatform on only if the domain exposes nested virtualization (svm). NetFx3 off. VirtIO-FS plus WinFsp on.]
7. **Q7 Meaning of posture B/C and `debloat_profile`:** [Drop `posture`. `debloat_profile=gaming` selects `mios-debloat.json` content, which moves into `[windows.xbox.debloat]`.]
8. **Q8 Which `usr/share/mios/windows` components ship:** [`Set-MiOSWallpaper` with `mios-wallpaperd.exe`, `mios-looking-glass-idd`, `mios-ssh-setup`, `mios-oscontrol-server`, `MiosServiceTool`, `MiOS-FhsSync.cfg`, `mios-agent-relay`, miosd. Not shipped: Hyper-V deploy, Tailscale, WSL keepalive and AI-node scripts.]
9. **Q9 DISM worker:** a new GPU-less domain `mios-xbox-worker` on a qcow2 overlay of `mios-xbox-current/disk.qcow2` (the original stays untouched), or Linux-native wimlib plus hivex servicing? [The GPU-less overlay worker for DISM. The Linux-native tools are used only by the offline inspection gate.]
10. **Q10 Name and size of the new VM:** [Name `mios-xbox`, 128G thin qcow2 in the `mios-build` pool. Keep a virtio video device so VNC has a framebuffer. `idd_exclusive=false`, mirroring on.]
11. **Q11 `start2.bin` and `VisiblePlaces` capture:** both must be captured once on a reference build of the target family. [Capture them inside the worker during xb-11 and store them as SSOT-referenced assets under `usr/share/mios/windows/xbox/` with a build-family key. The gate checks that the family matches.]

---TASKS---
{"id":"xb-01","title":"Add [windows.xbox] spec tree to vendor SSOT (preferences/start/xbox/features/drivers/guest_tools/components/settings/users-by-ref/vm/debloat); centered taskbar, search hidden, Start row exactly Personal,Network,Explorer,Settings; bridge configurator autounattend.* keys","stopping_condition":"XB-1","files":["usr/share/mios/mios.toml","usr/share/mios/configurator/mios.html","usr/share/doc/mios/manual/mios-xbox.md"],"depends_on":[],"acceptance":{"positive":"mios-toml-get windows.xbox.preferences.taskbar_align = 1 through load_merged; mios-ssot-lint green","negative":"a user-layer override taskbar_align=0 is reported as off-spec by xb-03"},"est_hours":4,"risk":"medium; operator answers Q1-Q8 change values; touches the vendor SSOT (write atomically)"}
{"id":"xb-02","title":"Renderer: load_merged SSOT -> resolved xbox-spec.json (single input to every Windows step); missing key or empty credential = hard fail","stopping_condition":"XB-1,XB-4","files":["usr/libexec/mios/win/xbox/render_spec.py","usr/lib/mios/mios_toml.py"],"depends_on":["xb-01"],"acceptance":{"positive":"rendered json equals the vendor->host->user merge for all keys","negative":"deleting taskbar_align or blanking a password makes the render exit non-zero"},"est_hours":4,"risk":"low"}
{"id":"xb-03","title":"Offline spec-conformance gate (rendered inputs + offline image inspection), with legacy-literal deny-list","stopping_condition":"XB-1,XB-3","files":["usr/libexec/mios/win/xbox/conform.py","tests/test-mios-xbox-conform.py"],"depends_on":["xb-02"],"acceptance":{"positive":"gate green on fixtures rendered from the SSOT","negative":"planted TaskbarAl=0, SearchboxTaskbarMode=1, Start_ShowDownloads=1, an nvidia .inf in the driver set, or the literal 'mios' password each fail with a named finding"},"est_hours":6,"risk":"low"}
{"id":"xb-04","title":"Pins gate for the Windows pipeline: reject version/build/URL pins in [windows.xbox] and the pipeline code (stable-virtio, fixed UUP build, LG commit match)","stopping_condition":"XB-2","files":["tests/test-mios-xbox-no-pin.sh","usr/libexec/mios/mios-ssot-lint"],"depends_on":["xb-01"],"acceptance":{"positive":"green on HEAD after xb-06","negative":"planting virtio_url=.../stable-virtio/ or uup_build=29683.1000 fails"},"est_hours":2,"risk":"low"}
{"id":"xb-05","title":"Port the Windows pipeline into MiOS code (UUP fetch/convert, DISM servicing, answer-file emit, ISO) reading only xbox-spec.json; delete Set-MiOSCustomDefaults, the NTLite presets, the duplicate unattend_gen paths and every TaskbarAl/Searchbox/Start literal","stopping_condition":"XB-1,XB-7","files":["usr/libexec/mios/win/xbox/","usr/share/mios/windows/build/New-MiOSXboxImage.ps1","usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1","usr/libexec/mios/win/unattend_gen.py"],"depends_on":["xb-02","xb-03"],"acceptance":{"positive":"xb-03 green on the rendered answer file and SetupComplete payload","negative":"grep gate finds no TaskbarAl literal outside the renderer; reinserting the legacy UserOnce block fails xb-03"},"est_hours":12,"risk":"high; large port; must rebase on the mios-ssot-credentials changes"}
{"id":"xb-06","title":"Latest-resolvers: UUP Dump newest build for the edition's channel, virtio-win latest-virtio, qemu-ga latest, Looking Glass host stable, WinFsp, WebView2; write build-manifest.json only","stopping_condition":"XB-2","files":["usr/libexec/mios/win/xbox/resolve_latest.py","automation/21-virt.sh"],"depends_on":["xb-04"],"acceptance":{"positive":"manifest lists resolved versions + sha256; nothing written back to the SSOT","negative":"resolver failure aborts the build (no cached fallback to a pinned version)"},"est_hours":5,"risk":"medium; upstream endpoints change"}
{"id":"xb-07","title":"Xbox features + Windows features + debloat from SSOT: FSE overrides, DeviceForm, GamingHomeApp/StartupToGamingHome, Game Mode/Game Bar/Auto HDR, HAGS, optional-feature enable/disable list; consume debloat_profile, drop posture","stopping_condition":"XB-1,XB-3","files":["usr/share/mios/windows/build/New-MiOSXboxImage.ps1","usr/share/mios/mios.toml"],"depends_on":["xb-05"],"acceptance":{"positive":"offline hive inspection shows each SSOT value","negative":"an SSOT feature removed from the image is reported missing by xb-03"},"est_hours":5,"risk":"medium; feature IDs vary by build family"}
{"id":"xb-08","title":"Bake drivers and guest tools: virtio-win (install.wim + boot.wim), virtio-win-guest-tools (qemu-ga, SPICE vdagent), VirtIO-FS + WinFsp, Looking Glass host + IDD (installed at SetupComplete); remove gpu_drivers_dir/-ForceUnsigned","stopping_condition":"XB-3","files":["usr/share/mios/windows/build/New-MiOSXboxImage.ps1","usr/share/mios/windows/mios-looking-glass-idd.ps1"],"depends_on":["xb-05","xb-06"],"acceptance":{"positive":"wimlib driver-store listing contains viostor/netkvm/vioscsi/viofs/balloon; payload has qemu-ga, vdagent, LG host+IDD","negative":"an NVIDIA/AMD/Intel display .inf in the payload fails xb-03"},"est_hours":6,"risk":"medium"}
{"id":"xb-09","title":"Guest GPU post-setup step: detect PCI vendor/device IDs -> vendor upstream latest driver, Authenticode verify, install, record version; no-op for an absent vendor","stopping_condition":"XB-3","files":["usr/share/mios/windows/mios-gpu-driver.ps1","tests/test-mios-gpu-driver.ps1"],"depends_on":["xb-05"],"acceptance":{"positive":"mocked 10de:2684 resolves the latest NVIDIA package and verifies its signature","negative":"no supported GPU -> installs nothing; a tampered signature aborts"},"est_hours":6,"risk":"high; NVIDIA resolver API is unofficial"}
{"id":"xb-10","title":"Stage MiOS componentry from build output (native exes incl. mios-wallpaperd, miosd, agent-relay, MiosServiceTool, FhsSync, ssh/oscontrol/LG scripts per [windows.xbox.components]); replace legacy C# wallpaper blobs and jpg","stopping_condition":"XB-3","files":["usr/share/mios/windows/build/New-MiOSXboxImage.ps1","usr/share/mios/mios.toml"],"depends_on":["xb-05"],"acceptance":{"positive":"offline image has every listed component with the expected hash plus its scheduled task/service","negative":"removing one component from the payload fails xb-03"},"est_hours":5,"risk":"medium; native Windows build must be current"}
{"id":"xb-11","title":"Capture start2.bin and VisiblePlaces for the target build family on the worker; store them as SSOT-referenced assets with a build-family key","stopping_condition":"XB-1","files":["usr/share/mios/windows/xbox/start2.bin","usr/share/mios/windows/xbox/visibleplaces.json"],"depends_on":["xb-14"],"acceptance":{"positive":"applied blob gives an empty grid and the 4 folders in the probe","negative":"family mismatch between asset and image fails xb-03"},"est_hours":3,"risk":"medium; blobs are opaque and per build"}
{"id":"xb-12","title":"Users and credentials from merged SSOT in answer file and provision; remove every hard-coded fallback; RID-500 re-secure at finalize","stopping_condition":"XB-4","files":["usr/libexec/mios/win/xbox/render_spec.py","usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1"],"depends_on":["xb-05","WAIT:mios-ssot-credentials"],"acceptance":{"positive":"rendered answer-file accounts/autologon equal the SSOT (compared as hashes, never printed)","negative":"empty SSOT password aborts the render; planted literal fallback fails xb-03"},"est_hours":3,"risk":"medium; overlaps the credentials workflow, must wait"}
{"id":"xb-13","title":"VM domain generator from [vm.win11]/[vm.looking_glass]: q35, secboot OVMF + swtpm, Hyper-V enlightenments, virtio disk/net/video, qemu-ga channel, SPICE 5930 + VNC, dGPU 01:00.0/.1, IVSHMEM 128M; domains list puts mios-xbox first; stage the ISO into a pool; add virt-viewer to [packages.virt]","stopping_condition":"XB-5","files":["usr/libexec/mios/vfio/setup-looking-glass.py","usr/libexec/mios/Xbox-AutoEnroll.xml","usr/libexec/mios/Xbox-Final-NoAutoSelect.xml","usr/libexec/mios/xbox-repair.sh","usr/share/mios/mios.toml","tests/test-looking-glass.py"],"depends_on":["xb-01","WAIT:mios-ssot-credentials"],"acceptance":{"positive":"generated XML validates with virt-xml-validate and matches the SSOT fields","negative":"rtl8139/SATA/no-TPM/port-5900 planted in the SSOT fails the test"},"est_hours":5,"risk":"medium; same files as the credentials workflow"}
{"id":"xb-14","title":"Define the GPU-less build worker mios-xbox-worker on a qcow2 overlay of the mios-xbox-current disk (original domain/disk untouched); attach worker-tools and virtio ISOs","stopping_condition":"XB-2","files":["usr/libexec/mios/win/xbox/worker.py"],"depends_on":["xb-05","operator:Q9"],"acceptance":{"positive":"worker boots without a hostdev, WinRM reachable, DISM/oscdimg present","negative":"worker XML containing a hostdev is refused"},"est_hours":3,"risk":"medium; must never hold the 4090; RAM ~8G"}
{"id":"xb-15","title":"BUILD: resolve latest -> UUP download+convert -> DISM servicing (xb-07,08,10,11,12) -> answer file -> ISO; write build-manifest.json and log","stopping_condition":"XB-2,XB-3","files":["/var/lib/mios-build/artifacts/mios-xbox/build-<ts>/"],"depends_on":["xb-06","xb-07","xb-08","xb-10","xb-11","xb-12","xb-14"],"acceptance":{"positive":"ISO + manifest exist; xb-16 green","negative":"a forced resolver failure aborts with no ISO"},"est_hours":6,"risk":"high; ~6 GB download, 40-60 GB scratch, 2-4 h"}
{"id":"xb-16","title":"Run the offline gate on the built ISO (wimlib/hivex on Linux) and keep the report in the build output","stopping_condition":"XB-1,XB-3","files":["usr/libexec/mios/win/xbox/conform.py"],"depends_on":["xb-15","xb-03"],"acceptance":{"positive":"all spec items present, no GPU vendor driver","negative":"same gate run on the legacy field ISO / answerfile-fixed ISO reports TaskbarAl=0 and missing components"},"est_hours":2,"risk":"low"}
{"id":"xb-17","title":"Define and launch the new mios-xbox VM (worker and legacy VMs off; cold GPU), unattended install to desktop","stopping_condition":"XB-5","files":["usr/libexec/mios/vfio/setup-looking-glass.py"],"depends_on":["xb-13","xb-16"],"acceptance":{"positive":"virsh domstate running; guest-ping via qemu-ga OK; WinRM OK; SPICE 5930 + VNC show the desktop; Looking Glass client shows the desktop; mirror on","negative":"launch refuses while another domain holds 0000:01:00.0"},"est_hours":3,"risk":"high; 4090 reset; 24G RAM leaves ~7G headroom"}
{"id":"xb-18","title":"In-guest WinRM conformance probe from the same xbox-spec.json (taskbar, Start, Xbox, features, drivers, guest tools, components, users, GPU step result)","stopping_condition":"XB-6,XB-3","files":["usr/libexec/mios/win/xbox/probe.py","usr/share/mios/windows/mios-xbox-probe.ps1","tests/test-mios-xbox-probe.py"],"depends_on":["xb-02"],"acceptance":{"positive":"unit tests with recorded guest output pass","negative":"recorded legacy output (TaskbarAl=0, no qemu-ga) fails"},"est_hours":5,"risk":"low"}
{"id":"xb-19","title":"Run the probe on mios-xbox (green) and, after cold-stopping it, on mios-xbox-current (non-conforming)","stopping_condition":"XB-6","files":[],"depends_on":["xb-17","xb-18"],"acceptance":{"positive":"new VM all green incl. the NVIDIA driver installed by detection with version recorded","negative":"legacy VM reports left taskbar and missing components"},"est_hours":2,"risk":"medium; GPU handoff between VMs"}
{"id":"xb-20","title":"Retire the legacy scripts in /var/lib/mios-build (remaster-answerfile, define-current-xbox, start-xbox-baseline) and the BS field/autounattend legacy generators from any pipeline path","stopping_condition":"XB-1","files":["/var/lib/mios-build/mios-remaster-xbox-answerfile.sh","/var/lib/mios-build/mios-define-current-xbox.py","BS/field/autounattend/MiOS-Xbox.xml"],"depends_on":["xb-19"],"acceptance":{"positive":"no pipeline entry references them","negative":"xb-03 deny-list catches a reintroduction"},"est_hours":1,"risk":"low; old VMs are kept until the operator approves"}
{"id":"xb-21","title":"Gates, docs, commit, PR, merge to main","stopping_condition":"XB-7","files":["automation/manifest.json","usr/share/doc/mios/manual/mios-xbox.md","ROADMAP.md"],"depends_on":["xb-16","xb-19","xb-20"],"acceptance":{"positive":"xb-03/xb-04/test-looking-glass/probe tests green; PR merged","negative":"CI fails on a planted TaskbarAl=0"},"est_hours":2,"risk":"low"}
---END---

## 4) Conformance gate design

There is one source of truth: `xbox-spec.json`, rendered from the merged SSOT by `render_spec.py`. Each spec item records its expected value plus an evidence locator: a registry path in the hive, an appx package name, a driver `.inf` class/provider, a file and its hash, a scheduled task name, or an optional feature. Both gates iterate over the same item list, so neither gate can contain a value the SSOT does not have.

### Offline gate (`conform.py`, Linux)

1. **Rendered inputs.** Decode the base64 FirstLogon and Specialize scripts in `autounattend.xml`, then parse the SetupComplete payload and the `*.reg` files. Each item must be set exactly once to the expected value. A deny-list of legacy literals fails the gate on any occurrence: `TaskbarAl` other than the spec value, `SearchboxTaskbarMode=1`, `Start_ShowDownloads`/`Start_ShowDocuments`, `LockedStartLayout`, a jpg wallpaper, the literal `'mios'` password, `stable-virtio`, `ForceUnsigned`. Credentials are compared as hashes and never printed.
2. **Image inspection.** Run `wimlib-imagex extract` on `install.wim` index 1, covering `Windows/System32/config/SOFTWARE`, `Users/Default/NTUSER.DAT`, `DriverStore/FileRepository` and `Program Files/MiOS`. Read the hives with `hivexregedit` and check each item. The checks are:
   - Every required virtio, guest-tool, Looking Glass and MiOS component is present.
   - No GPU vendor display driver is present: no `Display` class `.inf` from provider NVIDIA, AMD/ATI or Intel.
   - The provisioned appx set equals the keep-set.
   - `build-manifest.json` exists, and the SSOT has no pins (via xb-04).
3. **Negative controls (in tests).** Each fixture copies a good rendered set and plants one defect: `TaskbarAl=0` in the default hive, a planted `nvlddmkm.inf`, a removed `mios-wallpaperd.exe`, a blank SSOT password, or a `stable-virtio` URL. The test passes only if the gate fails with that item named. The gate also runs against the existing `mios-xbox-answerfile-fixed.iso` and `autounattend.xml` (read-only) and must report them non-conforming.

### In-guest probe (`probe.py` plus `mios-xbox-probe.ps1`)

1. **Transport.** WinRM, with credentials resolved from the SSOT. The same item list is checked live:
   - HKCU and the HKU of the desktop user: `TaskbarAl`, Search, Tipband, desktop icons.
   - Start: `start2.bin` hash under `LocalState` and `VisiblePlaces`.
   - Xbox: FeatureManagement overrides, DeviceForm, `GamingHomeApp`, Game Mode, Game Bar, Auto HDR, and the `Get-AppxPackage` set including Gaming Services.
   - Optional features: `Get-WindowsOptionalFeature`.
   - Drivers: `pnputil /enum-drivers` for virtio and the Looking Glass IDD. The GPU must use the vendor driver at the version recorded by `mios-gpu-driver`.
   - Services: QEMU-GA and spice-agent running.
   - MiOS components: files and scheduled tasks.
   - Accounts and autologon equal the SSOT.
2. **Host-side checks.**
   - `virsh qemu-agent-command guest-ping`
   - SPICE on 5930 and VNC listening
   - `/dev/shm/looking-glass` sized at 128M
   - The Looking Glass client connects
3. **Negative control.** The same probe against `mios-xbox-current` must report it non-conforming (`TaskbarAl=0`, no qemu-ga channel, missing components). Unit tests replay recorded output from both guests.

## 5) Resources and risk

- **Disk.** `/var/lib/mios-build` has 218G free. A build needs about 6 GB of UUP ESD download, 30 to 60 GB of conversion and servicing scratch, a 6 to 7 GB ISO, and a 128G thin qcow2 that uses about 30 to 40G after install. The worker overlay grows by about 10 to 20G. That fits. If space gets tight, `/var/lib/mios-build/tmp` (74G of old cargo targets) can be reclaimed.
- **Time.** UUP download and conversion take about 30 to 90 minutes. DISM servicing takes 45 to 90 minutes. Unattended install plus hydrate takes 30 to 60 minutes. One full cycle is about 3 to 4 hours.
- **RTX 4090.** It is in IOMMU group 14 and bound to vfio-pci. Only one domain may hold 0000:01:00.0, and recovering it needs a cold stop and start (shut off, no reset). The launcher must refuse to start while another domain holds it. The build worker never gets a GPU. Sequence: worker off before `mios-xbox` starts; `mios-xbox` cold-stopped before the probe runs on `mios-xbox-current`.
- **Memory.** The host has 46 GiB with about 31 GiB available. The worker (8G) and the target (24G) must not run at the same time. Run commands under `MemoryMax`, as the OOM memory requires.
- **Rollback.** Do not touch `mios-xbox-current` or `mios-xbox-baseline` (domain XML or disks) until XB-5 and XB-6 pass and the operator approves retiring them. The worker uses an overlay, never the original disk. The new VM is a new name and new disk, so `virsh undefine mios-xbox` removes it. Write SSOT edits atomically and check their size, as the SSOT-atomic-writes memory requires.
- **Order dependencies.**
  - xb-05, xb-12 and xb-13 must wait for the mios-ssot-credentials workflow, because it is editing `unattend_gen.py`, `setup-looking-glass.py`, `Xbox-*.xml`, VS, and the BS `New-MiOSAutounattend.ps1` / `MiOS-Provision.lib.ps1`.
  - The 10 to 12 uncommitted BS files must not feed the build.
- **Upstream risks.**
  - **NVIDIA resolver:** the driver lookup API is unofficial and may change. Mitigation: signature check plus a hard fail.
  - **Start blobs:** the `start2.bin` and `VisiblePlaces` formats change per Dev build family. Mitigation: the family key check.
  - **UUP Dev builds** may break the Xbox feature overrides. Mitigation: the probe catches it.
  - **Gaming Services** is Store-only, which depends on Q5.
- **Privileges.** The artifacts directory and `/etc/libvirt/qemu` are root-only, so the build and gate tasks need `sudo -n`.