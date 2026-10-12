<!-- AI-hint: MiOS-Cat: Flash-Tonight Deliverables. All findings confirmed on disk. Synthesis below.
     AI-related: usr/share/mios/mios.toml, docs/adr/, AGY-TASKS.md -->
All findings confirmed on disk. Synthesis below.

---

# MiOS-Cat: Flash-Tonight Deliverables

> [!WARNING]
> **Deployment Plane Status & Real-World Reality (task_2457)**:
> The USB and Ventoy staging machinery generates bootable media, but automated bare-metal installation of MiOS is broken/disconnected in practice.
> - What runs under Ventoy kickstart installs standard Fedora rather than the MiOS bootc image.
> - The offline bare-metal installer (`tools/install.sh` executing `bootc install to-disk --transport oci-archive`) is disconnected from automated kickstarts and must be run manually from a live shell.
> - `MiOS-Xbox.iso` bootc installer integration remains disconnected and unbuilt in the live USB boot menu.

Grounded against `C:\mios-bootstrap\cat\MiOS-Cat.bat` (1418 lines), `resources\ventoy\ventoy_grub.cfg` (5 entries, verified), and `resources\autorun.sh`. Research claims cited inline.

---

## Deliverable 1 — MiOS-Cat.bat Fix-Entirely List (blockers first)

| # | file:line | issue | severity | fix |
|---|-----------|-------|----------|-----|
| 1 | `MiOS-Cat.bat:856, 957, 961` | **7z run from `%stage_dir%\bin\7z.exe`, which is never populated.** Binary ships only at `%maindir%\bin\7z.exe` (dl'd at 97-99, verified). All three extractions (ventoy unzip, surgical extract, full extract) resolve to a nonexistent path → blank/broken USB. | **BLOCKER** | Replace all three `"%stage_dir%\bin\7z.exe"` with `"%maindir%\bin\7z.exe"` (source already present). Alt: `xcopy "%maindir%\bin" "%stage_dir%\bin\" /E /I /Y` after the mkdir at line 31. |
| 2 | `ventoy_grub.cfg:11` (+ WIM servicing `MiOS-Cat.bat:1153-1232`) | **No live-boot-to-AI-chat entry exists.** The 5 menuentries (verified: Mini Windows recovery, SystemRescue, Fedora kickstart INSTALL, Xbox INSTALL, EFI shell) never boot an ephemeral MiOS into an AI TTY. WIM servicing only injects wallpaper/font/colors — no `startnet.cmd`/`winpeshl.ini`, no model, no chat client. AI endpoints (`:8640`/`:8642`) exist only in post-install Xbox provisioning. **The core demo is not on the media.** | **BLOCKER** | Add a first/default menuentry that boots the live MiOS chat env; inject autostart + model + chat client into the WIM during servicing. See Deliverable 2. |
| 3 | `MiOS-Cat.bat:1297` | **Storage preflight guard is dead.** `if %errorlevel% equ 1` sits inside the same parenthesized `if not exist` block, so `%errorlevel%` is expanded at parse-time (stale). Insufficient-space check never fires → staging starts on a full drive, fails mid-download. | **HIGH** | Use `if errorlevel 1` (live read), or `!errorlevel!` under `enabledelayedexpansion`, or pull the powershell+check out of the outer parens. |
| 4 | `MiOS-Cat.bat:883, 893, 901` | **Disk wipe + Ventoy install run with masked/unchecked exit codes.** Wipe (883) suffixed `>nul 2>&1`; `Ventoy2Disk.exe VTOYCLI` (893) and `format` (901) have no return-code check → script prints "INSTALLATION COMPLETED" (1262) over a non-booting USB. Degrade-open on destructive steps. | **HIGH** | Drop `>nul 2>&1` (or redirect to log) at 883; after 893 `if errorlevel 1 (echo [FAIL] Ventoy install failed & exit /b 1)`; check `format` after 901; verify `\ventoy\ventoy` dir exists before extraction. |
| 5 | `resources\autorun.sh:42` (+13) | **SystemRescue autorun can dd-zero ALL disks incl. the boot USB.** autorun resolves USB via `by-label/Medicat` (verified line 13), but the .bat labels partitions `MiOS-Cat`/`MiOS-Repo` (883/901/904). `usb_disk=""` → exclusion guard `[ -n "$usb_disk" ]` (42) is false → nothing excluded → `dd`+`sgdisk --zap-all` every disk, then poweroff. Destroys demo host + stick. | **HIGH** | Match real label (`by-label/MiOS-Cat`) or resolve live root via `findmnt`/`losetup`; **abort before any dd** if boot device unidentified: `[ -z "$usb_disk" ] && { echo FATAL; exit 1; }`. Coupled to #8. |
| 6 | `MiOS-Cat.bat:936` (+32) | **Fedora ISO path hardcoded to `M:\`.** `fedora_file=M:\Fedora-...iso` (verified) has no SSOT key; `file=M:\MediCat...` (32) is SSOT-overridable but defaults to M:. On hosts lacking M:, curl (942) and copy (1054) fail. `%stage_dir%` (largest free disk, line 28) is ignored. | **HIGH** | Add `[medicat] fedora_iso_path` SSOT key resolved via the loader; default to drive of `%stage_dir%` (`Split-Path`), not literal `M:\`. Apply to 936 + 1054. |
| 7 | `MiOS-Cat.bat:61, 72` | **Self-update `cd`-success guards always true.** `cd /d "C:\MiOS"` then `if %errorlevel% equ 0` are in the same block opened at 59 → parse-time stale (holds DNS-check 0). If dir absent, cd fails silently, `git fetch/pull` runs in wrong cwd. | **MEDIUM** | Chain `cd ... && (git ...)`, or use `pushd`/`popd`, or `!errorlevel!`. Adopt the `if not exist "%~1\.git"` guard already used by `:update_repo` (1406-1416). |
| 8 | `sysrescue_grub.cfg:25,31,37,43,49,55` + `sysrescue_syslinux.cfg:10,19,28,37,46,55` | **`ar_source=/dev/disk/by-label/Medicat` hardcoded**, but staged USB is `MiOS-Cat`/`MiOS-Repo`. autorun source silently absent (coincidentally masks #5, but latent). | **MEDIUM** | Token-replace label from SSOT `partition_label` at stage time (resources already xcopied at 975), or standardize one label across .bat format calls (883/901/904), autorun (13) and every `ar_source=`. |
| 9 | `installation/MiOS-Field.bat` step 6 (mios-bootstrap) | **RESOLVED (mios-bootstrap#21).** The 23 GB MediCat 7z download lives in step 6, whose size gate runs *before* the download. Step 6a now checks the finished archive against the upstream-published hash pinned in `mios.toml [field].medicat_sha1` (preferred) or `[field].medicat_md5`, both carried in mios-bootstrap's and this repo's `mios.toml`; on mismatch it deletes the archive and exits. The unused shipped `MedicatFiles.md5` manifest was deleted. | ~~MEDIUM~~ | Done. Update the two hashes with `medicatver`; keep both repos' values equal. |
| 10 | `ventoy_grub.cfg:27` | **Built `MiOS-Xbox.iso` has no menu entry.** .bat builds it to `\Live_Operating_Systems\MiOS-Xbox.iso` (1256) but entry 4 chainloads `Mini_Windows\MiOS_PE.wim`, not the ISO. Reachable only via Ventoy generic auto-scan. | **MEDIUM** | Add menuentry chainloading `/Live_Operating_Systems/MiOS-Xbox.iso`, guarded `search --file` so it only shows when built (build_xbox may be Disabled, 1238). |
| 11 | `MiOS-Cat.bat:855, 857` | **Ventoy 1.0.99 hardcoded in URL + extracted-dir `ren`, no error check.** 404/renamed-dir → `ren`/`Ventoy2Disk.exe` absent at 893; staging continues. Not SSOT-driven. | **MEDIUM** | Add `ventoy_ver` to `mios.toml [medicat]`, build URL + ren source from it; after extract `if not exist "%stage_dir%\Ventoy2Disk\Ventoy2Disk.exe" (echo [FAIL] & exit /b 1)`. |
| 12 | `MiOS-Cat.bat:883` (+679-685, 887) | **`partition_scheme` MBR choice ignored — disk hard-init GPT.** `:set_scheme` toggles GPT/MBR and 887 passes `/%partition_scheme%`, but 883 hardcodes `-PartitionStyle GPT`. MBR selection → GPT disk + Ventoy /MBR on top; legacy-BIOS targets silently overridden. | **MEDIUM** | Interpolate `-PartitionStyle %partition_scheme%`, or drop the redundant manual Initialize/Format at 883 and let VTOYCLI own the layout. |
| 13 | `MiOS-Cat.bat:945` | **Fedora size guard uses magic substring** `%fedora_sz:~9,1%` (accept if ≥10-digit byte count). Undocumented; rejects valid smaller media. Pre-existing file (937-938) accepted on existence with no size check. | **LOW** | Explicit numeric compare (`(Get-Item).Length -ge 2000000000`) for both fresh and pre-existing, mirroring 913; drop `~9,1`. |
| 14 | `MiOS-Cat.bat:992, 994` | **Offline repo robocopy omits `.git`, ignores exit code.** `/XD .npm node_modules build cache isobuild isobuild2` but not `.git` → full history (GBs) copied; `>nul` + unchecked code (≥8 = fail) hides partial stage the kickstart later trusts. | **LOW** | Add `.git` to `/XD` (or `git archive`); `if %errorlevel% geq 8 (echo [WARN] repo stage incomplete)`. |
| 15 | `MiOS-Cat.bat:28, 984, 1253` | **Handoff `.txt` files written to `%~dp0`** (`stage_path.txt`/`repo_path.txt`/`work_path.txt`). On a read-only MiOS-Repo USB partition (a supported design), writes fail → blank `stage_dir`/`repodrive`/`workdir_path` → `mkdir ""` breaks. SSOT handoff at 49 correctly uses `%TEMP%`. | **LOW** | Write these to `%TEMP%`, or capture via `for /f`; guard each `set /p` with an empty-value error. |
| 16 | `MiOS-Cat.bat:1215-1217` | **WIM-unmount retry off-by-one.** `set /a retry_count+=1`, `if %retry_count% lss 4`, echo `attempt %retry_count%/3` all in one block → parse-time stale pre-increment; retries 4× not 3×, prints wrong number. | **LOW** | `enabledelayedexpansion` + `!retry_count!`, or split increment/compare onto re-parsed lines. |

**Systemic pattern:** findings 3, 7, 16 are all the same cmd.exe bug — `%errorlevel%`/counter expanded at parse-time inside a parenthesized block. Grep the whole file for `%errorlevel%` inside `(...)` blocks and convert to `if errorlevel N` / `!var!`; there are likely more than the three audited.

---

## Deliverable 2 — W10 Live-USB → AI-Chat Implementation (no install)

**Design decision (tonight):** The existing pipeline already services a **WinPE image (`MiOS_PE.wim`)** and the servicing block (1153-1232) is the natural injection seam. WinPE is inherently ephemeral (boot.wim loads into a RAM `X:` ramdisk — zero-install, discard-on-reboot). This is the shortest path to "boot and chat tonight" and reuses machinery that exists. The Linux/bootc path (below) is the correct long-term convergence but requires building a new live ISO — not tonight.

### 2.1 Live-boot mechanism (tonight = WinPE)
- Ventoy loopback-boots `MiOS_PE.wim` off the exFAT partition, no extraction (Ventoy core, GPL-3.0 — confirmed). WinPE unpacks into RAM (`X:`), giving a stateless session automatically.
- WinPE runs **`X:\Windows\System32\startnet.cmd`** on boot — this is the autostart hook. Currently the servicing block injects only cosmetics; we add `startnet.cmd`.

### 2.2 Bundled offline model + size
- **Serving binary:** `llamafile.exe` (Mozilla.ai, Apache-2.0; v0.10.4 confirmed July 2026) — single portable executable, runs on Windows/WinPE with **no install**, serves the OpenAI `/v1` API. It is llama.cpp + Cosmopolitan Libc.
- **Weights:** `Llama-3.2-3B-Instruct-Q4_K_M.gguf` ≈ **2.0 GB** (~30-50 tok/s CPU) as demo default, or **Qwen2.5-3B-Instruct Q4_K_M** ≈ 2 GB (Apache-2.0, OSI-clean) if strict-FOSS redistribution matters. Fallback for weak/low-RAM demo hardware: **Qwen3-0.6B** or **Llama-3.2-1B** (~1.5 GB).
- **Critical size gotcha (confirmed):** Windows cannot execute a single-file `.exe` **> 4 GB**. So **do NOT embed the 2 GB GGUF into the llamafile** — ship **external-weights mode**: small `llamafile.exe` + separate `.gguf`, launched with `--gguf model.gguf`. This sidesteps the 4 GB APE cap and any noexec/immutable-mount friction entirely.
- Pin exact binary + GGUF version and record sha256 as SBOM/baked-manifest data (ADR-0003) — never float `:latest` into the image.

### 2.3 tty0 / console chat client + auto-start
Two options; recommend **B for tonight** (zero extra binaries):

- **Option A — `aichat.exe`** (sigoden/aichat, MIT/Apache dual, Rust single binary — confirmed): interactive REPL, speaks any `/v1` via `type: openai-compatible` + `api_base`. Config **must include the `/v1` suffix** (`http://127.0.0.1:8642/v1`) — bare host:port fails. Keep Shell-Assistant/CMD mode and `--serve` **OFF** (they break the "sudo-disabled / immutable TTY" contract and add attack surface). Config projected FROM `mios.toml` (SSOT), not hand-written.
- **Option B — 20-line PowerShell REPL** (WinPE ships PowerShell): a `while` loop reading `User@mios>` input, POSTing to `http://127.0.0.1:8642/v1/chat/completions` via `Invoke-RestMethod`, printing the reply. Zero extra dependencies, fully brandable to the `User@mios` prompt, no shell-escape path (satisfies immutable/sudo-disabled). Handles the `@`-prefix SSOT-toggle in-loop.

**Ordering requirement (confirmed gotcha):** the chat client must not connect before the server is up. `startnet.cmd` launches llamafile, then **polls `/health` (or `/v1/models`) until 200** before launching the REPL, else the first prompt errors.

### 2.4 Ventoy entry
Add to `ventoy_grub.cfg` as the **first / default** entry:
```
menuentry "0. Chat with MiOS AI (Live - No Install)" --class windows {
    search --set=root --file /Live_Operating_Systems/Mini_Windows/MiOS_PE.wim
    chainloader ...   # same WIM path entry 1 uses, or a dedicated MiOS_CHAT.wim
}
```
Use `menu_alias`/`menu_class` branding so the operator boots straight to it. Keep it guarded by `search --file` so it only appears when the WIM is staged.

### 2.5 What MiOS-Cat.bat must stage on the USB (step-by-step, tonight)
Inside the WIM servicing block (1153-1232), after mount, before unmount:
1. **Copy `llamafile.exe`** → `<mount>\MiOS\ai\llamafile.exe` (stage from `%maindir%\ai\` — ship it in the repo like 7z.exe is at `bin\`).
2. **Copy the GGUF** → `<mount>\MiOS\ai\model.gguf` (model name resolved from `mios.toml` SSOT, not hardcoded). *(If keeping the WIM small, place the GGUF on the exFAT data partition instead and reference it by absolute Ventoy path — avoids bloating the RAM ramdisk.)*
3. **Write `startnet.cmd`** → `<mount>\Windows\System32\startnet.cmd`:
   - `wpeinit`
   - `start /b X:\MiOS\ai\llamafile.exe --gguf X:\MiOS\ai\model.gguf --server --host 127.0.0.1 --port 8642 --nobrowser`
   - poll loop: `powershell -c "do{Start-Sleep 1}until(try{irm http://127.0.0.1:8642/health;$true}catch{$false})"`
   - launch the chat client (`aichat.exe` or the PS REPL script staged at step 4).
4. **Write the chat client + config** (REPL `.ps1`, or `aichat.exe` + `config.yaml` with `api_base: http://127.0.0.1:8642/v1`) → `<mount>\MiOS\ai\`. Config projected from `mios.toml` `MIOS_AI_ENDPOINT` (:8642) via the existing dotfiles/theme-render projection — no divergent hand-copy (SSOT law).
5. **Add the default grub menuentry** (2.4) into `ventoy_grub.cfg` during the resources xcopy (975).
6. **Unmount + commit** the WIM (existing 1211-1223 path, with the retry off-by-one #16 fixed).

`MIOS_AI_ENDPOINT` = `:8642` (Hermes), per memory `mios-ai-endpoint-canonical` — reuse the constants already in `MiOS-Provision.ps1:301-303` / `MiOS-Daemon.ps1:41`; do **not** re-hardcode.

### 2.6 Long-term convergence path (NOT tonight — Linux/bootc)
The topology's `User@mios` Linux TTY is properly served by feeding the MiOS **bootc/OCI image** to **Titanoboa** (ublue-os, Apache-2.0 — builds a live ISO directly from an OCI image; pin the commit, spec is v0.1.0) or emitting via `dmsquash-live` (dracut, GPL-2.0 — `rd.live.overlay.overlayfs=1 rd.live.ram=1` = RAM-only, USB removable at the prompt). The MiOS image must ship `dracut-live` + `squashfs-tools` and rebuild initramfs with the `dmsquash-live` module. Serve with a `llama-server` systemd unit bound `127.0.0.1:8642`; autostart `aichat` on tty0 via a getty override. Note: **bootc-image-builder has NO pure "live" type** (only installer + `pxe-tar-xz`) — Titanoboa is the live path, BIB is only for the mutable-install leg (`bootc install --transport oci`, offline). Bake ublue `akmods` + full `linux-firmware` for hardware breadth; enroll Ventoy's (Rocky-signed, not "Microsoft-signed") shim key + ublue kmod-signing key for Secure Boot. **Keep the live leg and the install leg un-conflated** — the live/chat demo must never be blocked on the install path.

---

## Deliverable 3 — Critical Path to Flash Tonight (ordered minimum)

Do exactly these, in order. Everything else in Deliverable 1 (medium/low) can wait.

1. **Fix #1 (7z path)** — replace `%stage_dir%\bin\7z.exe` → `%maindir%\bin\7z.exe` at lines 856, 957, 961. *Without this nothing extracts; the USB is blank.* (2 min)
2. **Fix #5 (autorun data-loss)** — add the abort guard (`[ -z "$usb_disk" ] && exit 1`) OR simply **do not stage/point at the SystemRescue kickstart entry** for tonight's demo. *A demo machine that self-wipes ends the demo.* (5 min)
3. **Fix #4 (unchecked Ventoy/format exit codes)** — at minimum add `if errorlevel 1 exit /b 1` after 893 so you learn immediately if the stick didn't take, rather than trusting the "COMPLETED" banner. (5 min)
4. **Fix #3 + #6 (preflight `if errorlevel 1`; Fedora `M:\`→`%stage_dir%` drive)** — so staging actually runs to completion on the build host. (10 min)
5. **Stage the AI payload (Deliverable 2.5 steps 1-4)** — `llamafile.exe` + `model.gguf` (external-weights, Llama-3.2-3B Q4_K_M ~2 GB) + `startnet.cmd` (launch → poll `/health` → REPL) injected into `MiOS_PE.wim` in the servicing block. *This is the demo.* (30-45 min)
6. **Add the default "Chat with MiOS AI (Live)" menuentry (2.4)** to `ventoy_grub.cfg`. (5 min)
7. **Run MiOS-Cat.bat → flash the stick.**
8. **Boot the stick on the actual demo machine** (not just a VM — Ventoy/live media has real-hardware boot-label bugs) and confirm: menu → WinPE → llamafile `:8642` up → `User@mios>` prompt answers one prompt.

**Minimum viable demo = steps 1, 4, 5, 6, 7, 8.** Steps 2 and 3 are safety/reliability and strongly recommended but a careful operator who simply never selects the SystemRescue entry can defer #5.

**Files to edit tonight:** `C:\mios-bootstrap\cat\MiOS-Cat.bat` (lines 856/957/961, 1297, 893, 936, 1153-1232), `C:\mios-bootstrap\cat\resources\autorun.sh` (line ~42), `C:\mios-bootstrap\cat\resources\ventoy\ventoy_grub.cfg` (new default entry). New assets to add to the repo: `cat\ai\llamafile.exe`, `cat\ai\model.gguf` (or on the exFAT data partition), the `startnet.cmd` template, and the chat-client REPL/config.