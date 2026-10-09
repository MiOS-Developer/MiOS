<!-- AI-hint: Chapter 30: System Auditing and Drift Verification. Documents checks run by 99-postcheck.sh at build-time. Explains build constraints blocking hardcoded URLs or ports. Maps validation against our target zero-trust hardening profile. -->

# Chapter 30: System Auditing and Drift Verification

> Part VII: Build, Test & Upstream Maintenance of the [MiOS manual](../manual.md).

This chapter covers the documentation for **System Auditing and Drift Verification** under MiOS.

### <a name="30_automated_postcheck_suite"></a>30.Automated Postcheck Suite: Automated Postcheck Suite

> Path Reference: `/usr/share/doc/mios/manual.md#30_automated_postcheck_suite`

#### Overview

The postcheck suite validates system state compliance before image builds finish.

## Checks
- **Script**: Configured in [99-postcheck.sh](automation/99-postcheck.sh).
- **Tests**: Validates container layers, CDI specs, and FHS structures.
- **Gating**: Failing checks block OCI image output.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="30_hardcode_lint_rules"></a>30.Hardcode Lint Rules: Hardcode Lint Rules

> Path Reference: `/usr/share/doc/mios/manual.md#30_hardcode_lint_rules`

#### Overview

Build rules prohibit hardcoded keys, URLs, and settings.

## Rules
- **Linter**: Executed by [mios-hardcode-lint](usr/libexec/mios/mios-hardcode-lint) inside automation scripts.
- **Violations**: Hardcoded ports, IPs, or vendor links trigger build failures.
- **Bypasses**: Requires variables to resolve via config cascades.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

---

### <a name="30_security_policy_compliance"></a>30.Security Policy Compliance: Security Policy Compliance

> Path Reference: `/usr/share/doc/mios/manual.md#30_security_policy_compliance`

#### Overview

Verifies that active system configurations meet zero-trust security profiles.

## Auditing
- **Checks**: Scans permissions, SELinux states, and whitelists.
- **Output**: Reports are logged under `/usr/share/doc/mios/audits/`.
- **Validation**: Enforces integrity checks on core files.

#### System References

- Relevant configurations: `mios.toml`
- Runtime services: ``MIOS_AI_ENDPOINT``

#### Guidelines & Best Practices

1. Adhere to the Architectural Laws of MiOS at all times.
2. All configurations should be resolved using the three-layer override structure.
3. System state updates must be atomic and verified before reboot.

### [testing.smoke_components] is closed over the SSOT it ships...

[testing.smoke_components] is closed over the SSOT it ships in (task_3464).

    Every probe key is a kind `mios-gate image-equivalence` knows, every file
    probe exists in the source tree (commands, paths and rpm packages are build
    products, asserted against the image instead), every rpm_sections entry and
    every sections.<s> overlay names a [packages] section, every phases.<p> a
    registered phase, every profiles.<q> a declared profile, and the floor holds
    at least [testing].min_smoke_components probes. An overlay naming nothing
    can never be selected, so its probes would never run.

<!-- mios-src:f689719d6ce0 from tools/drift-checks.py:3363-3372 -->

### What every MiOS image holds, whatever its profile (task_3464)....

What every MiOS image holds, whatever its profile (task_3464). The top-level
lists are the floor. sections.<s>, phases.<p> and profiles.<q> add probes
only when the asserted profile selects that [packages] section, registered
phase or profile (its extends closure included). Probe kinds: shims and
paths exist, units, python_entries and manpages are files, commands are
executables on PATH, rpm_sections names [packages] sections whose pkgs the
rpm database must provide. Gate: mios-gate image-equivalence; manifest
closure: check_smoke_manifest.

<!-- mios-src:ad8b4b4ea0ba from usr/share/mios/mios.toml:11277-11284 -->
