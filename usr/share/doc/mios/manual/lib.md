<!-- AI-hint: Manual pages distilled from the source comments of lib, sanitized, each passage anchored to the comment it came from. -->

# lib

### MiOS is cross-platform, and this module runs under pwsh on...

MiOS is cross-platform, and this module runs under pwsh on Linux too (CI
lints and Pester-tests it there). Every entry must therefore be built
defensively: on Linux $env:USERPROFILE is null, and `Join-Path $null ...`
throws ParameterBindingValidationException under StrictMode +
ErrorActionPreference='Stop', which took out the whole Pester suite.

Order mirrors the layered resolver contract used everywhere else in MiOS:
explicit env override, then user tier, then host tier, then vendor.

<!-- mios-src:59b5c506e354 from automation/lib/MiOS.Toml.psm1:14-21 -->

### A trailing '_' means the regex stopped on a non-name...

A trailing '_' means the regex stopped on a
non-name character, so this is a FRAGMENT, not a
variable: an f-string prefix (f"MIOS_A2A_{name}"),
a build-time template placeholder
(__MIOS_COCKPIT_PORT__), or a doc wildcard
(MIOS_COLOR_*). No emitted name ends in '_', so
these can never be satisfied and would wedge the
gate permanently.

<!-- mios-src:257291d28a99 from automation/lib/mios_var_closure.py:71-78 -->

### AI-hint

AI-hint: PowerShell script that displays and enforces legal/policy acknowledgments for interactive users, supporting bypass via MIOS_AGREEMENT_* environment variables to control entry-point access.
AI-related: mios-bootstrap
AI-functions: Get-MiOSAgreementSummary, Test-MiOSInteractiveHost, Show-MiOSAgreementScrollable, Invoke-MiOSAgreementBanner
automation/lib/agreements-banner.ps1 -- PowerShell sibling of
agreements-banner.sh. Dot-sourced by every PowerShell entry point in
'MiOS' (mios.git) and 'mios-bootstrap' (mios-bootstrap.git).

Behavior summary:
  * Default for an interactive operator: print a scrollable summary
    of the project's licenses, research-project framing, third-party
    agreements, and data/network posture, then require an explicit
    "Acknowledged" or "No thanks" choice.
  * Default for non-interactive runs (CI, no console host, irm|iex
    redirected through a non-RawUI host): print a one-line note and
    continue. There is no way to accept-by-prompt without a host UI.
  * Escape hatches (any one of these skips the prompt):
        $env:MIOS_AGREEMENT_BANNER = 'quiet' | 'silent' | 'off' | '0' | 'false'
        $env:MIOS_AGREEMENT_ACK = 'accepted'                # explicit accept
        $env:MIOS_REQUIRE_AGREEMENT_ACK = '0'                # explicit waive
  * CI users that need the prompt skipped should set
    `$env:MIOS_AGREEMENT_ACK = 'accepted'` -- declaring acknowledgment
    by external policy is more honest than silently bypassing.

Exit code 78 (EX_CONFIG) on decline, matching the bash sibling.

<!-- mios-src:8aef9c3bb4a6 from automation/lib/agreements-banner.ps1:1-24 -->

### !/usr/bin/env bash AI-hint: Provides the canonical...

!/usr/bin/env bash
AI-hint: Provides the canonical legal/policy acknowledgment gate; agents use it to determine if the system requires a manual "Acknowledged" prompt or can proceed automatically based on MIOS_AGREEMENT_ACK environment variables.
AI-related: mios-bootstrap
AI-functions: mios_agreement_summary, _mios_agreement_render, mios_print_agreement_banner

<!-- mios-src:44f9919d6249 from automation/lib/agreements-banner.sh:1-4 -->

### !/usr/bin/env bash AI-hint: Provides idempotent shared...

!/usr/bin/env bash
AI-hint: Provides idempotent shared helper functions, logging utilities, and environment resolution logic (masking, paths, globals) for MiOS build scripts and automation tools.
AI-related: globals.sh, build.sh, /usr/share/mios/tools/lib/userenv.sh, /usr/lib/mios/logs/, mios-k3s, mios-build-versions
AI-functions: _mios_locate_userenv, log_ts, log, warn, die, diag, record_version

<!-- mios-src:7605dc150172 from automation/lib/common.sh:1-4 -->

### !/usr/bin/env bash AI-hint: WS-A17 build-time materializer...

!/usr/bin/env bash
AI-hint: WS-A17 build-time materializer for the local package registry. Thin, flag-gated wrapper around `mios-registry generate`: when [ai].package_registry (MIOS_PACKAGE_REGISTRY) is true it projects the live SSOT catalogs into ai/v1/packages/<author>/<name>/<version>/mios-pkg.toml + registry.json; when false (the default) it is a no-op so the feature ships dormant. Sourced/called by the build (or run manually); never fails the build when the flag is off.
AI-related: /usr/libexec/mios/mios-registry, /usr/lib/mios/agent-pipe/mios_registry.py, /usr/share/mios/mios.toml, ./build.sh
AI-functions: (sourced helper -- no functions; guards on MIOS_PACKAGE_REGISTRY)

<!-- mios-src:ae74b5e69761 from automation/lib/generate-packages.sh:1-4 -->

### AI-hint

AI-hint: GENERATED IN FULL from usr/share/mios/mios.toml by tools/render-globals.py. Zero hand-written constants; DO NOT EDIT -- re-run the renderer.
AI-related: usr/share/mios/mios.toml, automation/lib/globals.sh, tools/render-globals.py
AI-functions: Resolve-MiosVersion

PowerShell sibling of automation/lib/globals.sh -- both are rendered from the
same SSOT by the same generator, so they cannot diverge. Dot-source from any
entry point:

    . (Join-Path $PSScriptRoot 'automation/lib/globals.ps1')

Override any constant with an environment variable BEFORE dot-sourcing -- e.g.
`$env:MIOS_VERSION = ' - rc1'; . globals.ps1`.

<!-- mios-src:03d54d6ab97e from automation/lib/globals.ps1:1-12 -->

### !/usr/bin/env bash AI-hint: GENERATED IN FULL from...

!/usr/bin/env bash
AI-hint: GENERATED IN FULL from usr/share/mios/mios.toml by tools/render-globals.py. Zero hand-written constants; DO NOT EDIT -- re-run the renderer.
AI-related: usr/share/mios/mios.toml, automation/lib/globals.ps1, tools/render-globals.py
AI-functions: _mios_resolve_version

Shell sibling of automation/lib/globals.ps1 -- both are rendered from the same
SSOT by the same generator, so they cannot diverge. Dot-source from any entry
point; every constant uses `:=` so an environment variable exported BEFORE
sourcing still wins.

<!-- mios-src:8a0fd193deb1 from automation/lib/globals.sh:1-9 -->

### !/usr/bin/env bash AI-hint: Provides helper functions for...

!/usr/bin/env bash
AI-hint: Provides helper functions for identifying, registering, and masking sensitive credentials (like GH_TOKEN or MIOS_PASSWORD) in logs and stdout, and provides a secure scurl wrapper for credential-aware requests.
AI-functions: add_mask, register_common_masks, mask_filter, scurl

<!-- mios-src:fdb6be2b4488 from automation/lib/masking.sh:1-3 -->

### !/usr/bin/env python3 AI-hint: SSOT var-closure fitness...

!/usr/bin/env python3
AI-hint: SSOT var-closure fitness function (drift-check 37 `check_drift_projection`). Proves R ⊆ E -- referenced MIOS_* variables are emitted by SSOT (task_2215).
AI-related: ../../usr/lib/mios/userenv.sh, ../../tools/lib/userenv.sh, ../../usr/libexec/mios/system-sync-env.sh, ../97-ssot-lint.sh
AI-functions: emitted_set, referenced_set, main

<!-- mios-src:fc97d85166bc from automation/lib/mios_var_closure.py:1-4 -->

### !/bin/bash AI-hint: Provides shell functions to parse and...

!/bin/bash
AI-hint: Provides shell functions to parse and extract package lists from mios.toml configuration files, supporting layered overrides and specific installation modes (strict/optional) for automated package management.
AI-related: automation/lib/packages.sh, /etc/mios/mios.toml, /usr/share/mios/mios.toml, /usr/share/mios/configurator/mios.html, mios-bootstrap
AI-functions: _resolve_mios_toml, get_packages_from_toml, get_packages, get_packages_strict, _is_section_enabled, install_packages, install_packages_strict, install_packages_optional

<!-- mios-src:e0f2122b5016 from automation/lib/packages.sh:1-4 -->

### !/usr/bin/env bash AI-hint: Defines and exports core MiOS...

!/usr/bin/env bash
AI-hint: Defines and exports core MiOS filesystem constants (USR, ETC, VAR, LOG, BUILD) as environment variables to standardize directory paths for automation scripts and build tools.
AI-related: mios-build, mios-build-chain, mios-build-versions

<!-- mios-src:5b12b5216113 from automation/lib/paths.sh:1-3 -->

### !/usr/bin/env bash AI-hint: Builds a verity-rooted Unified...

!/usr/bin/env bash
AI-hint: Builds a verity-rooted Unified Kernel Image (UKI) and configures fapolicyd in permissive mode based on mios.toml flags; use this to generate the hardened UKI artifact and carve-out rules for the WS-7 security profile.
AI-related: mios-ws7-permissive, mios-agent-codegen, mios-verity
AI-functions: _ws7_scalar, _ws7_is_true, ws7_install_fapolicyd_observe, ws7_build_verity_uki, main

<!-- mios-src:4f4814ea7fe4 from automation/lib/ws7-uki-fapolicyd-build.sh:1-4 -->

### Configure rootful container storage on a fresh runner. The...

Configure rootful container storage on a fresh runner.

The runner's default graphroot sits on the small root filesystem and its
overlay defaults enable metacopy, which bootc images cannot use. Both
publishers need the same layout or a bake succeeds on one and fails on the
other for reasons unrelated to the change under test.

<!-- mios-src:82d3f33d7e13 from tools/lib/ci-runtime.sh:12-17 -->

### Assert the built image is bootc-switchable (Architectural...

Assert the built image is bootc-switchable (Architectural Law 4).

The Containerfile's final layer already runs `bootc container lint`. This
re-reads the labels from storage so a lint that ran but did not take effect
is still caught.

<!-- mios-src:7539e098c573 from tools/lib/ci-runtime.sh:71-75 -->

### Comment lexer + classifier for the generative documentation...

Comment lexer + classifier for the generative documentation system.

Spec: docs/design/doc-generative-documentation.md sections 1.2 and 2.

Two jobs, kept apart on purpose:

  lex(path)      -> the comment blocks in a file, with enough context
                    (attachment, anchor code, hashes) to place and track them.
  classify(b, ..) -> exactly one verdict per block, from an ordered first-match
                    rule set, so every decision is explainable by one rule id.

The classifier holds NO thresholds of its own. Every number arrives in a
`Policy` built from mios.toml `[docs]`, because a rule change must be an
operator edit to SSOT rather than a code edit (Law 7 NO-HARDCODE, Law 8
SSOT-PROJECTION).

Taggability and comment syntax are NOT redefined here. They are loaded from
usr/libexec/mios/mios-ai-tag through the same SourceFileLoader shim
mios-ai-hint-coverage uses, so "which files carry documentation" has exactly one
definition across all consumers.

<!-- mios-src:f6c3310f2a36 from usr/lib/mios/mios_comments.py:4-24 -->

### The files the census covers

The files the census covers: GIT-TRACKED only, sorted.

    Walking the filesystem instead made the count depend on whatever untracked
    or ignored files a particular machine happened to have -- vendored trees,
    scratch dirs, staging dumps. The number then differed between a contributor
    box and CI, which silently loosened the ratchet ceiling in CI to the point
    that its negative test could not breach it. Tracked files are the same set
    everywhere.

<!-- mios-src:4f4415e5d651 from usr/lib/mios/mios_comments.py:49-57 -->

### Import usr/libexec/mios/mios-ai-tag (no .py suffix) as a...

Import usr/libexec/mios/mios-ai-tag (no .py suffix) as a module.

    Same SourceFileLoader approach mios-ai-hint-coverage already uses. Returns
    None when it cannot be found, so callers can degrade rather than crash.

<!-- mios-src:28a9e30776f6 from usr/lib/mios/mios_comments.py:85-89 -->

### Every name a comment could legitimately reference. A...

Every name a comment could legitimately reference.

    A reference counts as dangling only when it is absent from here AND absent
    from the tree AND not allowlisted. Without this filter the staleness rule
    drowns in false positives -- the survey needed it to get from 353 raw hits
    down to ~70 real ones.

<!-- mios-src:6fc7fcbe217c from usr/lib/mios/mios_comments.py:212-218 -->

### Names the SSOT defines

Names the SSOT defines: every MIOS_* key, every unit it declares.

        A comment naming MIOS_AI_ENDPOINT is referencing a key that exists --
        in mios.toml, not as a file. Without this the staleness rule reported
        every env var in the tree as a dangling reference, which is why its
        count was noise rather than a signal.

<!-- mios-src:4ed8af1db3c0 from usr/lib/mios/mios_comments.py:323-329 -->

### Short pod names and service aliases, restricted to names...

Short pod names and service aliases, restricted to names the tree
actually carries. A hand-list is the wrong shape here -- 26 of the
original 59 named nothing that exists, including mios-ollama, which
was purged from MiOS entirely, and vector stores it never shipped.
Allowlisting a name that does not exist blinds the staleness
measurement to precisely the references it exists to find, which
lowers the count without making anything truer. Names that are
legitimate but unresolvable belong in [docs].ref_allowlist with a
recorded reason (task_2249), not hardcoded here.

<!-- mios-src:89c739e01b60 from usr/lib/mios/mios_comments.py:363-371 -->

### The allowlist holds literal reference tokens and globs --...

The allowlist holds literal reference tokens and globs -- paths like
'C:\mios-bootstrap\Get-MiOS.ps1', bare tokens like 'ollama' or
'8080', and globs like 'blade-*.conf'. It was matched with re.search,
under which the Windows paths are invalid patterns (bad escape \m)
and 'blade-*.conf' silently matches nothing it was meant to cover.
Match them as what they are written as.

<!-- mios-src:be7e0f6ee742 from usr/lib/mios/mios_comments.py:458-463 -->

### Characters of AI-hint prose, continuation lines included....

Characters of AI-hint prose, continuation lines included.

    Counting only the line that starts with `AI-hint:` would mean a hint could
    clear the cap by being wrapped across several `#` lines -- the gate would
    then be measuring line length, which nobody cares about, instead of how much
    prose sits in the header, which is the thing being ratcheted down.

<!-- mios-src:2c1e4d0345bf from usr/lib/mios/mios_comments.py:486-492 -->

### Python uses tokenize + ast, never regex. Regex miscounts...

Python uses tokenize + ast, never regex.

    Regex miscounts multi-line data strings as prose -- the survey proved it on
    the AI-plane files, where a system-prompt literal reads exactly like a
    narrative comment block.

<!-- mios-src:1fe018524038 from usr/lib/mios/mios_comments.py:578-583 -->

### hint_max_chars caps the AI-hint PROSE, not the whole header...

hint_max_chars caps the AI-hint PROSE, not the whole header block.
AI-related/AI-functions/AI-doc are machine-maintained: their length
tracks how many files a module touches and how many functions it
defines, neither of which is a writing-quality signal. Counting them
made the ceiling unreachable -- check-fleet-safety.py carries 357
characters of generated metadata, so it breached a 260 cap even with
an empty hint, and no amount of editing could clear the gate.

<!-- mios-src:91b1f37a187d from usr/lib/mios/mios_comments.py:793-799 -->

### R4 BANNER DELIBERATE NARROWING of the spec's second clause....

R4 BANNER

DELIBERATE NARROWING of the spec's second clause. As written it is
"<= 8 words AND no sentence-final punctuation AND not WHY -> DROP", which
also swallows ordinary short comments ("bump the retry count",
"guard against zero") -- and DROP is a class that `prune` may delete.
Information safety is absolute here, so a short block must additionally
LOOK like a label -- ALL-CAPS, Title Case, or a trailing colon -- before it
can be treated as a divider. Pure divider runs are unaffected.

<!-- mios-src:f3e774fcaabb from usr/lib/mios/mios_comments.py:811-819 -->
