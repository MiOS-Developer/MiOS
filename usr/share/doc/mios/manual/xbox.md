<!-- AI-hint: Manual pages distilled from the source comments of xbox, sanitized, each passage anchored to the comment it came from. -->

# xbox

### Render the MiOS-Xbox image payload from xbox-spec.json....

Render the MiOS-Xbox image payload from xbox-spec.json.

  answer.py --spec build/xbox-spec.json --inputs build/inputs.json --out build/payload

`inputs.json` (written by fetch.py) names the verified local installers:
  {"installers": {"<role>": {"path": ..., "sha256": ..., "signer": ..., "version": ..., "url": ...}}}
with roles virtio_guest_tools (the virtio-win-gt MSI), spice_vdagent (carved from the verified bundle), qemu_ga,
winfsp, webview2, looking_glass_host, looking_glass_idd; plus fod_dir (the OpenSSH.Server FOD cab + metadata).

Exit 0 rendered; 2 spec/input error (named); 1 usage. Credentials are never printed.

<!-- mios-src:66793f89b005 from usr/libexec/mios/win/xbox/answer.py:5-14 -->

### Persistent AutoLogon (round-1 boot of...

Persistent AutoLogon (round-1 boot of build-20261023T224037Z): at SetupComplete Winlogon still holds OOBE's own
autologon (DefaultUserName=defaultuser0, no AutoLogonCount); CloudExperienceHostBroker transfers the answer-file
AutoLogon (DefaultUserName + LogonCount) at the FIRST desktop sign-in, after this stage. The SYSTEM Verify task
registered above (startup + desktop logon) deletes AutoLogonCount then; this step does it now only if Windows
already transferred it, and otherwise requires that task (never a deferral nobody performs).

<!-- mios-src:c2796abef55a from usr/libexec/mios/win/xbox/answer.py:889-893 -->

### Payload the SYSTEM stage needs beside the components (fix-a...

Payload the SYSTEM stage needs beside the components (fix-a M1), each hashed into payload.json so the
    worker verifies it: the SSH inputs from the SSOT (F4: no GitHub at provisioning), the FOD the capability
    fallback installs from (LimitAccess), the living-wallpaper page at the URL Set-MiOSWallpaper writes, and the
    MiosServiceTool host copy + .cfg for every service the SSOT hosts that way.

<!-- mios-src:0efd697baa6d from usr/libexec/mios/win/xbox/answer.py:1026-1029 -->

### MiOS-Xbox build. build.py plan # validate SSOT, tree...

MiOS-Xbox build.

  build.py plan                      # validate SSOT, tree, tools, VM guards; writes nothing
  sudo build.py capture [--family F] # worker: capture start2.bin + VisiblePlaces of the worker's (= image's) build family (xb-11)
  sudo build.py stage                # render -> resolve -> fetch -> family (capture when missing) -> payload -> rendered gate -> job
  sudo build.py service --build-dir D  # worker: convert + DISM + ISO, copy-out
  sudo build.py gate --build-dir D     # offline image gate on the ISO + negative controls (legacy media) + final manifest
  sudo build.py run                  # all of the above

Exit 0 ok; 1 a gate found non-conformance; 2 a stage failed (named). Credentials never printed.

<!-- mios-src:46335edd1757 from usr/libexec/mios/win/xbox/build.py:5-14 -->

### Before any stage writes or downloads

Before any stage writes or downloads: this process context must be able to drive the worker (virsh reads
    the files the build writes). A refusal costs seconds here instead of the whole stage.

<!-- mios-src:1f0834561671 from usr/libexec/mios/win/xbox/build.py:557-558 -->

### MiOS-Xbox offline conformance gate. conform.py rendered...

MiOS-Xbox offline conformance gate.

  conform.py rendered --spec xbox-spec.json --input DIR|FILE [...] [--report R.json]
  conform.py image    --spec xbox-spec.json --wim install.wim [--index N]
                      [--answer autounattend.xml] [--workdir DIR] [--report R.json]

Exit 0 = conforming, 1 = findings, 2 = usage/spec/tool error.  Findings carry a
stable id (``missing:<item>``, ``off-spec:<item>``, ``deny.<literal>``,
``users.<...>``, ``drivers.gpu-vendor``, ...).  No credential is ever printed:
answer-file and script credentials are compared as sha256 digests only.

<!-- mios-src:c27680dc6026 from usr/libexec/mios/win/xbox/conform.py:5-14 -->

### gpu.post_setup offline (item 8): the GPU detection step...

gpu.post_setup offline (item 8): the GPU detection step must be able to read the guest spec the image SHIPS
    (the R3/M1 defect: 'SSOT table windows.xbox.gpu missing'). Runs the TREE's mios-gpu-driver.ps1 (never code taken
    from the image; gpu.script proves the shipped copy is byte-identical) with -LibraryOnly under pwsh and compares
    Read-MiOSGpuSpec's mode / task / record / vendor ids with the spec.

<!-- mios-src:778c527f0967 from usr/libexec/mios/win/xbox/conform.py:954-957 -->

### Fetch + verify the resolved MiOS-Xbox build inputs....

Fetch + verify the resolved MiOS-Xbox build inputs.

  fetch.py --manifest BUILD/build-manifest.json --spec BUILD/xbox-spec.json --dir WORK/inputs

Exit 0 fetched and verified; 2 an artefact could not be fetched or verified (nothing partial is left
marked as verified). Network access is the only side effect besides files under --dir.

<!-- mios-src:a36d21eb8369 from usr/libexec/mios/win/xbox/fetch.py:5-10 -->

### MiOS-Xbox in-guest conformance probe. probe.py request...

MiOS-Xbox in-guest conformance probe.

  probe.py request  [--spec xbox-spec.json | --fixture]    # the request the guest receives (no secrets)
  probe.py evaluate [--spec xbox-spec.json | --fixture] --recorded OUT.json [--host HOST.json] [--report R.json]
  probe.py run      [--spec xbox-spec.json] [--endpoint URL | --address IP | --domain NAME] [--host-from-ssot]
                    [--auth-from-answer-file XML] [--shm PATH --shm-size BYTES --port N ...] [--save OUT.json] [--report R.json]

Without --spec the layered SSOT is rendered in memory (the REAL spec; nothing is written); the recorded test
fixture is used only with --fixture. WinRM signs in as the SSOT desktop admin. Exit 0 = conforming,
1 = findings, 2 = unreachable (fail closed) / usage / spec error.

<!-- mios-src:5e3fcd99134d from usr/libexec/mios/win/xbox/probe.py:5-14 -->

### WinRM credentials from the rendered spec (render_spec.py)...

WinRM credentials from the rendered spec (render_spec.py): the separate elevation admin (users.admin) when the
    spec has one -- the desktop account is a STANDARD user (F2) and WinRM refuses it (round-1 boot:
    InvalidCredentialsError as the desktop user) -- else the desktop account of a single-account spec. `answer_file`
    is an explicit opt-in for a guest that was installed from another answer file (the legacy negative control): its
    first Administrators LocalAccount, read like the build worker reads its base. Never printed.

<!-- mios-src:3732a1bbb2ad from usr/libexec/mios/win/xbox/probe.py:270-274 -->

### Render the MiOS-Xbox image specification. Input: the merged...

Render the MiOS-Xbox image specification.

Input:  the merged SSOT ([windows.xbox] plus the keys it references by *_ref).
Output: xbox-spec.json with
  spec      the merged [windows.xbox] tree, verbatim (references left as written)
  resolved  every *_ref path -> its SSOT value
  users     accounts with credentials resolved (the file is written mode 0600)
  region    Windows timezone id, UI language, input locale
  source    UUP source with the language tag derived from the SSOT locale
  registry  every registry write the spec implies, one owner per hive/key/value
  feature_overrides, debloat_services, layers

Exit status: 0 rendered; 2 a required key is missing / empty / malformed (the key is named);
1 usage or I/O error. Credentials are never printed.

<!-- mios-src:a6c83926d51a from usr/libexec/mios/win/xbox/render_spec.py:5-19 -->

### every RDP redirection switch is written by exactly one...

every RDP redirection switch is written by exactly one Terminal Services policy entry (closed world: a stale
or renamed key in a host/user layer fails here instead of being silently ignored)

<!-- mios-src:be3fe2933f88 from usr/libexec/mios/win/xbox/render_spec.py:274-275 -->

### Users per operator decision, cross-checked with...

Users per operator decision, cross-checked with mios_credentials.resolve (the ONE credential
    resolver every MiOS image pipeline uses): a separate desktop admin carries the persistent
    AutoLogon; it is never the service account nor the RID-500 Administrator; its password is the
    resolver's. Returns the AutoLogon LogonCount. Errors name keys, never values.

<!-- mios-src:31b4410c078a from usr/libexec/mios/win/xbox/render_spec.py:454-457 -->

### Home of the operator the build runs for

Home of the operator the build runs for: under sudo the invoking user's (build.build_layers does the
    same for the user SSOT tier), else the caller's.

<!-- mios-src:9124dcff1ee2 from usr/libexec/mios/win/xbox/render_spec.py:505-506 -->

### F4 inputs the image carries for mios-ssh-setup.ps1 (no...

F4 inputs the image carries for mios-ssh-setup.ps1 (no GitHub, no Windows Update at provisioning):
    the SSOT SSH port and [ssh.windows] settings, and the operator public keys -- [windows.xbox.ssh].
    authorized_keys_key (an SSOT value, "" allowed) plus authorized_keys_files (globs in the operator's home).
    Public keys only (a private key or junk line is refused); none at all fails the render, naming the keys.

<!-- mios-src:ecb7005df7ed from usr/libexec/mios/win/xbox/render_spec.py:517-520 -->

### Resolve the latest upstream inputs of a MiOS-Xbox image...

Resolve the latest upstream inputs of a MiOS-Xbox image build into build-manifest.json.

Usage:
  resolve_latest.py resolve --out BUILD_DIR/build-manifest.json [--profile mios-xbox] [--layers A.toml B.toml ...] [--replace]
  resolve_latest.py verify  --manifest BUILD_DIR/build-manifest.json --artifact NAME --file PATH [--member NAME]

Inputs come only from the merged SSOT: the [windows.xbox] spec tree (source, drivers.virtio_win,
guest_tools.qemu_ga/winfsp, baked.webview2; locale via source.language_ref; channel/arch fall back to
[editions.<profile>].autounattend.*) plus the endpoints/trust anchors in [windows.xbox.resolve]. A missing key, an unreachable endpoint, a malformed or inconsistent
published digest, or a pinned value in the SSOT all exit non-zero with nothing written.

<!-- mios-src:e970be9b243c from usr/libexec/mios/win/xbox/resolve_latest.py:15-25 -->

### One upstream call with bounded backoff on 5xx/transport...

One upstream call with bounded backoff on 5xx/transport errors (``tries`` attempts) and, apart from that, on 429
    (RATE_LIMIT_WAITS_S, or the upstream's Retry-After capped at RATE_LIMIT_MAX_WAIT_S). Exhaustion raises ResolveError.
    prefix=N: a ranged GET of the first N bytes only (206, or 200 read up to N bytes). retry: 4xx statuses this
    upstream uses for transient failures (UUP_API_RETRY), retried within ``tries`` like a 5xx.

<!-- mios-src:a6b9491affa8 from usr/libexec/mios/win/xbox/resolve_latest.py:160-163 -->

### MiOS-Xbox spec items (schema ``mios-xbox-spec/1``). Input...

MiOS-Xbox spec items (schema ``mios-xbox-spec/1``).

Input is the resolved ``xbox-spec.json`` rendered from the layered SSOT (xb-02,
render_spec.py).  Two shapes are accepted:

1. render_spec.py output (``schema`` 1: ``spec``, ``registry``, ``users``, ...).  The
   registry locators come from ``[windows.xbox.registry_map]`` via render_spec's
   ``registry`` list -- this module owns NO spec-key -> registry mapping.
2. ``{"schema": "mios-xbox-spec/1", "items": [...]}`` -- the canonical item list.

Item kinds (every item has ``id`` and ``kind``):

=============  ===============================================================
reg            scope (user|software|system), path, name, type, value
feature        name, state (enabled|disabled|conditional)
driver         inf (base name, e.g. ``viostor``) -- must be in the driver set
guest_tool     name, installer (payload path) and/or service, file
component      name, path (image-relative), sha256?, task?, service?
capability     name (Windows capability name prefix, e.g. OpenSSH.Server), proof? (image-relative file of the offline-baked FOD)
file_hash      path (image-relative), sha256
build_family   family (list of BuildBranch names with Start assets), families{}
user           name ("<RID-500>" = built-in Administrator), autologon, password_sha256
rid500         state (disabled)
computer_name  value
appx           name (package family prefix), present (bool), pfn + inbox (integrated offline: image gate + probe) -- else probe only
gpu_step       record (guest path of the post-setup JSON record)
=============  ===============================================================

Credentials only ever appear here as sha256 hex digests; nothing in this module
prints a credential.

<!-- mios-src:e45deee747d5 from usr/libexec/mios/win/xbox/spec_items.py:5-34 -->

### Map any observed registry path to (scope, canonical...

Map any observed registry path to (scope, canonical lower-case path below the hive).

    Handles HKCU, HKU\<anything> (default-profile mounts, SIDs), HKLM\SOFTWARE,
    HKLM\SYSTEM (ControlSet00N -> CurrentControlSet), and offline mounts such as
    HKLM\MIOS_SYS / HKLM\MIOS_SW / HKLM\DefUser.  PowerShell drive syntax
    (``HKCU:\``, ``Registry::HKEY_...``) is accepted.  Returns None for an
    unrecognised path.

<!-- mios-src:9a8ebb6b6d99 from usr/libexec/mios/win/xbox/spec_items.py:96-102 -->

### ADMX policy name -> (registry value name, enabledValue...

ADMX policy name -> (registry value name, enabledValue, disabledValue). Source: Microsoft Learn "Policy CSP -
ADMX_TerminalServer" / "Policy CSP - RemoteDesktopServices" (ADMX mapping tables), cross-checked against the
image's own Windows\PolicyDefinitions\TerminalServer.admx (build 29683; TS_LOCATION_REDIRECTION is only in the
ADMX). This is Microsoft's schema, not MiOS data: the SSOT registry_map names the policy, and a value name that
is not that policy's valueName (fDisableLocation, or fDisableCam used for the camera) fails the render and the gate.

<!-- mios-src:65b3ffb0cbb4 from usr/libexec/mios/win/xbox/spec_items.py:447-451 -->

### A GPU vendor display driver installed by a pipeline script...

A GPU vendor display driver installed by a pipeline script (XB-3: only the post-setup detection step installs one,
from the vendor's own installer). Any driver-install command -- PnPUtil /add-driver|-a|-i, DISM /Add-Driver,
Add-WindowsDriver, DevCon install|update|dp_add -- that names an NVIDIA/AMD/Intel display INF (the names
GPU_INF_NAME_RE knows) or a GPU-vendor driver directory is denied. A pnputil + nv*.inf line keeps its own id above.

<!-- mios-src:6c7fc87fdd82 from usr/libexec/mios/win/xbox/spec_items.py:512-515 -->

### Credentials that are known legacy literals / factory...

Credentials that are known legacy literals / factory defaults, stored as sha256 digests so no
credential literal lives in code (the legacy MiOS defaults and common factory defaults).
Policy (XB-4): the SSOT is authoritative. A value the resolved layered SSOT SELECTED is never
a finding, even when it is one of these (that is only a weak-password WARNING); the same value
HARDCODED in pipeline code/templates, or rendered without being the SSOT value, is a finding.

<!-- mios-src:77c764d0259c from usr/libexec/mios/win/xbox/spec_items.py:528-532 -->

### MiOS-Xbox build worker manager. worker.py --spec...

MiOS-Xbox build worker manager.

  worker.py --spec BUILD/xbox-spec.json xml        # print the worker domain XML (no side effects)
  worker.py --spec BUILD/xbox-spec.json preflight  # guards only
  worker.py --spec BUILD/xbox-spec.json stop|cleanup|assert-shutoff
(the full define -> run -> extract sequence is driven by build.py).

<!-- mios-src:746b96959c8d from usr/libexec/mios/win/xbox/worker.py:5-10 -->

### virsh must read files this process writes under work_dir...

virsh must read files this process writes under work_dir: ``virsh define`` reads the worker XML client-side.
    Under SELinux a virsh exec'd from a confined system domain (initrc_t, e.g. a shell script started by systemd-run)
    runs as virsh_t and gets "Failed to open file ...: Permission denied" -- which surfaced only at the worker, after
    the 35-minute stage. The probe defines a throw-away domain in libvirt's in-process test driver (test:///default):
    nothing on the host is defined or changed.

<!-- mios-src:70a6958ee99a from usr/libexec/mios/win/xbox/worker.py:296-300 -->

### WSMan faults Windows raises while it creates the shell for...

WSMan faults Windows raises while it creates the shell for a request, before the command runs, so a retry is safe.
0x800703FA ERROR_KEY_DELETED ("Illegal operation attempted on a registry key that has been marked for deletion"):
the User Profile Service unloaded the signing-in account's hive under WinRM between two shells (build
20261024T044408Z: submit_job's shell, right after quiesce). It clears once the profile is reloaded.

<!-- mios-src:64bb2d4c307b from usr/libexec/mios/win/xbox/worker.py:516-519 -->
