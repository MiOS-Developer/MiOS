<!-- AI-hint: Manual pages distilled from the source comments of deploy, sanitized, each passage anchored to the comment it came from. -->

# deploy

### MiOS Bare-Metal Direct Installer & Hardware Discovery...

MiOS Bare-Metal Direct Installer & Hardware Discovery Engine.

Orchestrates rapid bare-metal installation of MiOS to physical NVMe/SATA storage in under 3 minutes:
- Auto-discovers and ranks candidate block devices (prioritizing NVMe SSDs > SATA SSDs > HDDs).
- Asserts UEFI boot firmware support (/sys/firmware/efi).
- Enforces strict safety gates preventing accidental destruction of live/booted drives.
- Invokes 'bootc install to-disk' with container rootfs materialization and systemd-boot setup.

<!-- mios-src:1d8cdf6e791c from usr/libexec/mios/deploy/baremetal_install.py:5-13 -->

### Scoring algorithm

Scoring algorithm:
NVMe SSD: +1000
SATA SSD: +700
SATA HDD: +300
Size bonus: +1 per 10GB up to +100
Boot device penalty: score = 0, ineligible
< 32GB penalty: ineligible

<!-- mios-src:bd6ce2ef7258 from usr/libexec/mios/deploy/baremetal_install.py:166-172 -->

### WS-DIFFCYCLE (task_411): Boot Cycle Diff Accrual & Risk...

WS-DIFFCYCLE (task_411): Boot Cycle Diff Accrual & Risk Classifier.
Ingests historical diff snapshots across power cycles, deduplicates entries,
classifies file mutations into Safe vs High-Risk vs Review tiers,
and atomically exports structured audit ledgers for operator inspection and image baking.

<!-- mios-src:b4f7a62b9844 from usr/libexec/mios/deploy/diff_accrual.py:5-10 -->

### WS-DIFFCYCLE (task_410): Pre-Poweroff Diff Snapshot Hook....

WS-DIFFCYCLE (task_410): Pre-Poweroff Diff Snapshot Hook.
Captures all filesystem and configuration modifications across system root (.git == /)
before shutdown, reboot, or kexec events with a strict sub-3s timeout SLA,
redacting all sensitive tokens and writing immutable JSON records.

<!-- mios-src:0ba1d7f4b109 from usr/libexec/mios/deploy/diff_snapshot.py:5-10 -->

### WS-DIFFCYCLE (task_413): Autonomous Background OCI Image...

WS-DIFFCYCLE (task_413): Autonomous Background OCI Image Synthesis Service.
Ingests operator-approved diffs from staged manifests, verifies against the quarantine ledger,
creates structured git commits at repo root (.git == /), builds updated container layers
inside podman-MiOS-DEV with low CPU/IO priority, and stages images via bootc switch --staged.

<!-- mios-src:a92d2540f8bc from usr/libexec/mios/deploy/image_bake.py:5-10 -->

### MiOS Bootable Live Hybrid ISO Generator. Produces hybrid...

MiOS Bootable Live Hybrid ISO Generator.

Produces hybrid ISO images supporting dual UEFI (x86_64) and Legacy BIOS firmware boot,
featuring mandatory serial console redirection (console=ttyS0,115200n8) for headless
rackmount servers, BMC/IPMI SoL (Serial-over-LAN), and edge micro-nodes.

Constructs El Torito multi-boot headers and executes/simulates xorriso image mastering.

<!-- mios-src:23def7029b7f from usr/libexec/mios/deploy/iso_generate.py:5-13 -->

### MiOS OCI Image Layer Streaming Extractor. Extracts...

MiOS OCI Image Layer Streaming Extractor.

Extracts multi-gigabyte OCI container image layers directly into the destination
rootfs without 2x disk space overhead. Implements full OCI Image Specification v1
whiteout handling (.wh.<filename> deletions and .wh..wh..opq opaque directory masking),
extended attributes, hardlinks, symlinks, and permission retention.

<!-- mios-src:5b4e0c9a7209 from usr/libexec/mios/deploy/oci_extractor.py:5-12 -->

### MiOS Non-Destructive Dual-Boot Partition & Resize...

MiOS Non-Destructive Dual-Boot Partition & Resize Orchestrator.

Safely audits Windows NTFS volume health (dirty bit check), calculates safe shrink boundaries,
provisions dedicated MiOS XBOOTLDR (if existing ESP < 512MB) and Root partitions (Btrfs/XFS),
and generates systemd-boot loader entries chainloading Windows Boot Manager.

<!-- mios-src:bec987e028f7 from usr/libexec/mios/deploy/partition_dualboot.py:5-11 -->

### quadlet_prewarm.py — task_693 WS-BUILD Build-time Quadlet...

quadlet_prewarm.py — task_693 WS-BUILD
Build-time Quadlet container image pre-warmer and zstd chunked layer optimizer.

Pulls and unpacks enabled Quadlet containers directly into /var/lib/containers/storage
during build time, ensuring sub-100ms offline Day-0 container startup.

<!-- mios-src:4ec661685fc6 from usr/libexec/mios/deploy/quadlet_prewarm.py:4-10 -->

### self_replicate.py — task_910 WS-HCI Autonomous...

self_replicate.py — task_910 WS-HCI
Autonomous self-replication daemon and podman-MiOS-DEV build pipeline trigger.

Inspects local git tree changes, triggers containerized bootc-image-builder (BIB)
compilation in podman-MiOS-DEV, verifies cryptographic image digest signatures,
and stages hot-swapped OCI artifacts for atomic bootc switch.

<!-- mios-src:0c9d52dd3663 from usr/libexec/mios/deploy/self_replicate.py:5-12 -->

### MiOS Storage Health, Alignment & Integrity Verification...

MiOS Storage Health, Alignment & Integrity Verification Engine.

Performs critical hardware and filesystem integrity checks:
1. 4K / 1MB Partition Alignment: Verifies that starting LBAs are aligned to physical
   flash erase blocks to eliminate write amplification and degradation.
2. Streaming Block Read-Back Verification: Computes streaming SHA-256 digests over
   written blocks and matches against source ISO/raw images.
3. Counterfeit / Fake Flash Detection: Tests for wrapped memory controller registers
   and ghost storage via exponential boundary marker probes.

<!-- mios-src:4d7169f97cde from usr/libexec/mios/deploy/storage_verify.py:5-15 -->

### MiOS-Cat Removable USB Media Partition Formatter....

MiOS-Cat Removable USB Media Partition Formatter.

Provisions removable USB media with a hybrid GPT/MBR partition layout:
- Partition 1 (MiOS-Repo): FAT32 filesystem, ESP GUID (c12a7328-f81f-11d2-ba4b-00a0c93ec93b),
  legacy MBR 0xEF, boot/esp flags for UEFI & BIOS firmware bootloaders.
- Partition 2 (MiOS-Data): exFAT (or ext4) filesystem, Basic Data GUID
  (ebd0a0a2-b9e5-4433-87c0-68b6b72699c7), legacy MBR 0x07, storing OCI layers,
  AI model weights, and staging payloads.

Enforces strict safety invariants preventing inadvertent wiping of internal or OS drives.

<!-- mios-src:0ff462459357 from usr/libexec/mios/deploy/usb_format.py:5-16 -->

### Live-profile-only unit -- shipped ONLY inside...

Live-profile-only unit -- shipped ONLY inside MiOS-Live-Chat.iso's exported
rootfs via automation/build/live-chat-fetch.sh + the config/live-profile
overlay copy in automation/build/live-iso.sh. A real `bootc install` never
carries this file (a bundled multi-hundred-MB GGUF has no business on
every real MiOS disk -- W10 design S1 step 3).
/usr/lib/systemd/system/mios-live-chat-server.service

<!-- mios-src:d9e5817ff290 from docs/agy/w10-live-boot/g1__config__live-profile__usr__lib__systemd__system__mios-live-chat-server.service:1-6 -->

### [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat...

----------------------------------------------------------------------------
[cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
Reuses [llamacpp].bake_models -- NO third model pin. model/model_fallback are
short keys into that CSV (dest filename = "<key>.gguf"). Threaded through
MiOS-Cat.bat's SSOT-map (see docs/agy/impl-mios-cat-live-boot.md and the W10
design doc) and consumed by automation/build/live-chat-fetch.sh +
usr/libexec/mios/mios-live-chat-{select-model,serve}.
----------------------------------------------------------------------------

<!-- mios-src:8edda225a6f8 from docs/agy/w10-live-boot/g1__mios.toml:18-25 -->

### [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat...

[cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
Both models are keys into [llamacpp].bake_models -- no new model pin here.
live_chat_port intentionally mirrors [ports].hermes (8642, "canonical /v1"):
in the live/ephemeral session nothing else is running, so llama-server binds
the canonical AI port directly instead of going through agent_pipe/hermes.
Keys are flat/unique (live_chat_*) so MiOS-Cat.bat's single-pass regex SSOT
loader can pull them without colliding with the many other model=/port=
keys elsewhere in this file.

<!-- mios-src:177447984c2b from docs/agy/w10-live-boot/g3__C____MiOS__mios.toml:23-30 -->

### [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat...

[cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
Live-boot mechanism derives directly from localhost/mios:latest (the same
bootc OCI image `bootc install` writes to disk) via dracut dmsquash-live --
same rootfs, same [colors]/SSOT projection, no second OS definition. Both
models below are keys into [llamacpp].bake_models (line ~5783) -- no new
model pin here. live_chat_port mirrors [ports].hermes (8642, "canonical
/v1"): nothing else runs in the live session, so llama-server binds the
canonical AI port directly instead of going through agent_pipe/hermes.
Keys are flat/unique (live_chat_*) so MiOS-Cat.bat's single-pass regex SSOT
loader can read them without colliding with the many other model=/port=
keys elsewhere in this file (that loader is NOT table-scoped).

<!-- mios-src:7e60af3e8309 from docs/agy/w10-live-boot/g3__C____MiOS__usr__share__mios__mios.toml:32-42 -->

### [cat.live_chat] -- W10: zero-install live-USB-to-AI-chat...

----------------------------------------------------------------------------
[cat.live_chat] -- W10: zero-install live-USB-to-AI-chat (bootc-live-squashfs).
Consumed by MiOS-Cat.bat's live_chat_* staging block + :build_live_chat_iso.
Both models are keys into [llamacpp].bake_models -- no new model pin here.
Keys are flat/unique (live_chat_*) so the single-pass regex SSOT loader in
MiOS-Cat.bat can read them without colliding with the many other model=/
port= keys elsewhere in this file (the loader is NOT table-scoped -- it
greps the whole file for the FIRST `key = "value"` match).
----------------------------------------------------------------------------

<!-- mios-src:10aee412f81d from docs/agy/w10-live-boot/g3__C____mios-bootstrap__mios.toml:16-24 -->
### This CLI's flags as `mios-install disk` flags. --force used...

This CLI's flags as `mios-install disk` flags.

    --force used to confirm the erase as well; it still does (--yes), and
    otherwise only skips the UEFI check. A boot disk is always refused.

<!-- mios-src:bd8e3dfec622 from usr/libexec/mios/deploy/baremetal_install.py:25-29 -->
