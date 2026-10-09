<!-- AI-hint: Manual pages distilled from the source comments of sec, sanitized, each passage anchored to the comment it came from. -->

# sec

### MiOS Interactive Human-In-The-Loop (HITL) Permission...

MiOS Interactive Human-In-The-Loop (HITL) Permission Escalation and Approval Engine.

Intercepts high-risk or destructive tool execution requests, issues cryptographically
signed escalation tokens upon operator approval, and enforces strict TTL-based expiration.

<!-- mios-src:f0e04a8353d5 from usr/libexec/mios/sec/approval.py:5-10 -->
### MiOS Portable Drive LUKS2 FIDO2 / CTAP2 Token Enrollment...

MiOS Portable Drive LUKS2 FIDO2 / CTAP2 Token Enrollment Engine.

Binds hardware security keys (YubiKey 5, SoloKeys, Nitrokey) to portable LUKS2 encrypted
partitions using systemd-cryptenroll. Enables secure, mobile LUKS2 volume unlocking across
heterogeneous host machines without relying on host-bound TPM2 PCR policies (ADR-0016 D15).

<!-- mios-src:43e53dee5dc0 from usr/libexec/mios/sec/fido2_enroll.py:5-11 -->

### MiOS FIDO2 / WebAuthn Hardware Security Key Manager &...

MiOS FIDO2 / WebAuthn Hardware Security Key Manager & Sandbox Engine.

Provides unified hardware authentication provisioning:
1. CTAP2 Device Discovery: Scans HID raw devices for FIDO2 / U2F authenticators.
2. Declarative PAM Enrollment: Generates `u2f_keys` mapping for passwordless/2FA system authentication.
3. Resident SSH Security Keys: Configures hardware-backed `ssh-ed25519-sk` resident keypairs.
4. User Presence & PIN Verification: Validates touch challenges and client PIN enforcement.

<!-- mios-src:942552473328 from usr/libexec/mios/sec/fido2_manager.py:4-12 -->

### WS-DIFFCYCLE (task_414): Greenboot Post-Bake Health Gate &...

WS-DIFFCYCLE (task_414): Greenboot Post-Bake Health Gate & Automated Fallback.
Invoked during early system boot by Greenboot required checks.
Verifies that newly baked image layers initialize critical AI and system services cleanly,
triggering automated bootc rollback and quarantining offending diffs if regressions occur.

<!-- mios-src:9d2f054b71db from usr/libexec/mios/sec/greenboot_gate.py:5-10 -->

### WS-SEC (task_489): MOK-signed kpatch livepatching manager and...

WS-SEC (task_489): MOK-signed kpatch livepatching manager and late CPU microcode reload daemon.
Enforces MOK module signature verification for runtime livepatch kernel modules (.ko),
orchestrates atomic livepatch load/unload via kpatch, triggers late processor microcode reloads,
and stages UKI image updates for subsequent boot cycles.

<!-- mios-src:d81ecf158d6b from usr/libexec/mios/sec/livepatch_mgr.py:5-10 -->

### Execute atomic zero-downtime key rotation

Execute atomic zero-downtime key rotation:
        1. Verify current passphrase validity.
        2. Identify active and free keyslots.
        3. Backup header.
        4. Add new passphrase to free keyslot.
        5. Verify new passphrase unlocks device.
        6. Retire old keyslot.
        7. Verify post-rotation state.

<!-- mios-src:f9b19e9c7b26 from usr/libexec/mios/sec/mios-luks-rotate:219-228 -->

### Secure in-memory secret enclave runtime for MiOS. Allocates...

Secure in-memory secret enclave runtime for MiOS.

Allocates memory-locked, non-dumpable, wipe-on-fork pages to isolate decrypted tokens,
private keys, and agent credentials. Enforces deterministic compiler-barrier zeroization
(explicit_bzero) on scope completion to prevent credential harvesting from swap files
and post-crash core dump forensics.

<!-- mios-src:7e3c387463cc from usr/libexec/mios/sec/secret_mem.py:4-10 -->

### WS-SEC (task_511): SPIFFE/SPIRE Workload Identity Agent &...

WS-SEC (task_511): SPIFFE/SPIRE Workload Identity Agent & Ephemeral 24h mTLS Certificate Rotator.
Manages dynamic X.509 SPIFFE Verifiable Identity Documents (SVIDs) for local and mesh agent workloads.
Enforces trust domain verification (spiffe://mios.cluster/node/{node_id}/workload/{workload_name}),
handles automatic in-memory rotation prior to 24h expiration, and validates mTLS peer identities.

<!-- mios-src:f846624c2152 from usr/libexec/mios/sec/spiffe_identity.py:5-10 -->

### systemd_homed.py — task_689 WS-SEC Declarative systemd-homed...

systemd_homed.py — task_689 WS-SEC
Declarative systemd-homed LUKS2 user enclave configurator and TPM2/FIDO2 key manager.

Provisions /home/mios as a systemd-homed LUKS2 Btrfs container bound to TPM 2.0
and FIDO2 PIN, with sub-200ms unlock and automatic RAM key zeroization on logout.

<!-- mios-src:20b24fecc565 from usr/libexec/mios/sec/systemd_homed.py:4-10 -->
