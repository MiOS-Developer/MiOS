<!-- AI-hint: ADR 0023: Unified Native Resolver Architecture.
     AI-related: usr/share/mios/mios.toml, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md, usr/share/doc/mios/adr/README.md -->
---
adr: 0023
title: "Unified native resolver architecture"
status: superseded
date: 2026-08-05
deciders: [operator, ai-pair]
tags: [resolver, rust, native, ssot, toml]
laws: [7, 8, 9, 13, 14]
ssot_keys: [migration.use_rust_resolver_shell, migration.use_rust_resolver_powershell, migration.use_rust_resolver_python, migration.use_rust_resolver_install_env]
related_ws: [WS-LANG, WS-GUP]
supersedes: []
superseded_by: [0021]
---

# ADR-0023: Unified native resolver architecture

## Status

superseded — 2026-08-05. Originally recorded as `docs/adr/0005-unified-native-resolver.md`; promoted to canonical ADR namespace under task_958. Reconciled with and superseded by ADR-0021 §5 (consolidation into `tools/native/mios-resolver` library crate rather than full replacement of bootstrap readers).

## Context

Historically, MiOS maintained three parallel SSOT resolver implementations:
1. `usr/lib/mios/mios_toml.py` (Python)
2. `tools/lib/userenv.sh` and `usr/lib/mios/userenv.sh` (Bash)
3. `automation/lib/globals.ps1` (PowerShell)

These parallel implementations resulted in drift risk, duplicated logic, and maintenance overhead across Linux and Windows execution environments.

## Decision

Collapse all SSOT resolver surfaces into a single compiled Rust crate: `tools/native/mios-resolver`.
- **Framework**: `figment` for multi-layered TOML configuration loading (vendor, host, user, `.d` drop-ins).
- **Strangler-Fig Cutover Sequence**: Shell -> PowerShell -> Python -> Install Env -> Names Registry.
- **Rollback Safety**: `[migration]` SSOT toggles (`use_rust_resolver_*`) allowing instant fallback to legacy shims without build reverts.
- **Fitness Functions**:
  - `check_resolver_shell_equivalence`: Byte-identical bash snapshot checks.
  - `check_resolver_ps_equivalence`: Regeneration equivalence for `globals.ps1`.
  - Differential proptest gate: Automated equivalence verification (`crate == python == bash`).
  - `deny.toml`: Supply-chain security and license policy enforcement via `cargo-deny`.

## Rationale

Maintaining three disparate resolver implementations in Python, Bash, and PowerShell risked semantic divergence and required triple-testing. Consolidating into a single Rust crate with multi-layer loading guarantees uniform configuration semantics.

## Consequences

- Single source of truth for configuration resolution logic across Linux and Windows.
- Compiler-grade `miette` diagnostic errors identifying exact line spans on malformed TOML keys.
- Deletion of legacy heredoc fallbacks and reduction of drift-check duplication.
- Scope refined by ADR-0021 §5: rather than replacing bootstrap readers before binary availability, the resolver crate serves as the shared library for all native tools and compiler gates.
