<!-- AI-hint: Manual pages distilled from the source comments of tier1_coverage, sanitized, each passage anchored to the comment it came from. -->

# tier1_coverage

### Tier 1

Tier 1: Feature Coverage — Credentials & Secrets Architecture Suite.

Covers:
- Feature 2: Credential Helper Mode (D1)
- Feature 3: PowerShell Layered Readers (D2/D3)
- Feature 4: AutoLogon Consistency (D4)
- Feature 5: Bootstrap SSOT Deduplication (G1)
- Feature 6: Render Globals Secret Stripping (G2)
- Feature 7: WSL Conf Default User (G3)
- Feature 8: Justfile Password Hash (G4)
- Feature 9: Install Bootstrap Credentials (G5)
- Feature 10: Kickstart SHA-512 Hash (G6)
- Feature 11: CMD Literal Escaping (G8)
- Feature 12: Xbox Conformance Deny-List
- Feature 13: Credentials Test Verification

<!-- mios-src:ec005cb554df from tests/e2e/tier1_coverage/test_t1_credentials.py:2-17 -->

### Tier 1

Tier 1: Feature Coverage — Displays, Remote RDP & Mobile Glass Suite.

Covers:
- Feature 14: RFB Bridge Follow-ups (socket2 reapply, cursor overlay, 1/120 snap, non-negative x)
- Feature 15: QuickShell Mobile UI Scale (Mobile.uiScale in QML)
- Feature 16: Rebuild mios-gen & Projections (display_scale.rs, render.rs)
- Feature 17: Mobile Glass Visual Parity (hyprbars/hyprglass matching controls)
- Feature 18: Global Rounding on Mobile (desktop.mobile window_rounding)
- Feature 19: 90 Hz Remote RDP & 144 Hz HDMI-A-1

<!-- mios-src:9fab4d790d30 from tests/e2e/tier1_coverage/test_t1_display.py:2-11 -->

### Tier 1

Tier 1: Feature Coverage — Gates, Tooling & Build Verification Suite.

Covers:
- Feature 39: Unit Tests Registration in [ci.tiers].unit
- Feature 40: Rust Static Binaries Rebuild (mios-gate, mios-gen, mios-resolver)
- Feature 41: Standing Gates Verification (97-ssot-lint.sh, 98-drift-checks.sh, ci-suites.py --check, mios-gate)

<!-- mios-src:06c8a37c5350 from tests/e2e/tier1_coverage/test_t1_gates.py:2-8 -->

### Tier 1

Tier 1: Feature Coverage — SSOT & Stage 1 Integrity Suite.

Covers:
- Feature 1: Stage 1 Merge Re-verification (mios.toml, render_spec.py, manual ch89, 97-ssot-lint)
- Feature 35: Cosign Trust Anchor in automation/49-cosign-policy.sh
- Feature 36: Nerd-Font Digest Hard Fail in automation/56-fonts.sh
- Feature 37: Greeter Tmpfiles Whitelist in .gitignore / staged tmpfiles
- Feature 38: ShellCheck SC2066 in usr/libexec/mios/forge-firstboot.sh
- Feature 41: Standing Gates scripts existence and execution interfaces

<!-- mios-src:b8f590ff2e9f from tests/e2e/tier1_coverage/test_t1_ssot.py:3-12 -->

### Feature 35

Feature 35: the shipped signature policy trusts MiOS anchors for the MiOS namespace, and containers/image loads it.

        Positive: usr/lib/containers/policy.json loads in containers/image
        (skopeo) and resolves ghcr.io/mios-dev to sigstoreSigned with keys under
        /usr/share/pki/containers. Negative: the old insecureAcceptEverything
        policy is refused by the same loader. The full signed/unsigned pull
        matrix is tests/test-cosign-trust-anchor.sh.

<!-- mios-src:04ff8d961e75 from tests/e2e/tier1_coverage/test_t1_ssot.py:118-125 -->

### Feature 36

Feature 36: the REAL _install_nerd_symbols from 56-fonts.sh dies when no verified artefact exists.

        The function is cut out of the shipped script, its absolute install paths
        are redirected into a temp root, and only the upstream side (release
        resolution and the digest-verified download) is stubbed; die and
        mios_ul_pass are the real helpers. Positive: a verified artefact installs
        and exits 0. Negative: no published digest, and an unresolvable release,
        each exit 1 with the hard-fail message and install nothing.

<!-- mios-src:2c20220f0449 from tests/e2e/tier1_coverage/test_t1_ssot.py:159-167 -->

### Feature 37

Feature 37: git tracks usr/lib/tmpfiles.d/mios-greeter.conf; the directory rule still ignores the rest.

        Asked of git itself (check-ignore), not of a substring in .gitignore.
        Positive: the greeter tmpfiles file is not ignored. Negative: a
        non-whitelisted name in the same directory is still ignored, so the
        whitelist is one file wide.

<!-- mios-src:eba86f07cd40 from tests/e2e/tier1_coverage/test_t1_ssot.py:233-239 -->

### Tier 1

Tier 1: Feature Coverage — Windows / Xbox Subsystem & Pipeline Suite.

Covers:
- Feature 20: Pipeline Script Port (xb-05)
- Feature 21: Xbox Feature CFR (xb-07: 153 toggles)
- Feature 22: Drivers & Guest Tools (xb-08)
- Feature 23: MiOS Component Staging (xb-10)
- Feature 24: Autologon & Admin (xb-12)
- Feature 25: Domain Generator (xb-13)
- Feature 26: GPU Driver Script Fixes (G1-G4)
- Feature 27: Phase-A Pins Gate Follow-ups (virtio latest, pin-gate:allow)
- Feature 28: Build Worker VM (xb-14)
- Feature 29: Start Menu Assets (xb-11)
- Feature 30: Image Servicing & Build (xb-15)
- Feature 31: Offline Conformance (xb-16)
- Feature 32: Cold GPU Domain Launch (xb-17)
- Feature 33: In-Guest Probe Verification (xb-18/19)
- Feature 34: Legacy Script Deprecation (xb-20)

<!-- mios-src:35d5ca992055 from tests/e2e/tier1_coverage/test_t1_xbox.py:2-20 -->
