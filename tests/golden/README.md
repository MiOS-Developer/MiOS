<!-- AI-hint: Golden-master CLI cases for the native mios-gen verbs (task_1707 / ADR-0021); every console block below is executed by tools/native/mios-gen/tests/golden.rs.
     AI-related: usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md, docs/design/doc-rust-static-port.md, tools/native/mios-gen/tests/golden.rs, tests/golden/fastfetch/mock.jsonc -->
# Golden-Master CLI Testing Standards (task_1707 / ADR-0021)

This file is the **golden-master case registry** for the MiOS Script→Rust Static Binary consolidation (ADR-0021 / `doc-rust-static-port.md`). It is executable: `cargo test -p mios-gen --test golden` (part of `cargo test --workspace` in `tools/native`) runs every `console` block in section 5 against the freshly built `mios-gen`, from the repository root.

## 1. Core Principle: Two-Sided Verification Before Code

Per the dev-loop Definition of Done and task_1707, **no Rust code is written until a golden-master fixture set exists for the script surface being replaced**.

Every golden-master suite MUST be two-sided:

1. **Positive Controls**:
   - Valid inputs executed against the real repository/system tree.
   - Asserts exit code `0` (`EXIT_CLEAN`).
   - Asserts the combined stdout and stderr line for line.
2. **Negative Controls**:
   - Explicitly planted defects (missing required SSOT keys, malformed arguments, nonexistent file paths, invalid flags).
   - Asserts exit code `1` (`EXIT_VIOLATIONS`) or `2` (`EXIT_CANNOT_RUN`).
   - Asserts that the tool fails **strictly for the planted reason**, matching the expected diagnostic error message. A test that passes unconditionally or cannot fail is a phantom test and is forbidden.

## 2. Case Layout

One `### <surface>` section per ported surface in section 5, each holding one `console` block (trycmd syntax):

- `$ mios-gen <verb> <args>` starts a case. It runs with the repository root as its working directory and with every inherited `MIOS_*` variable removed, so a case reads only what it names.
- `? <code>` on the next line is the expected exit code; without it the case expects `0`.
- The lines that follow, up to the next `$` or the end of the block, are the expected combined stdout and stderr.
- `[CWD]` stands for the repository root, in commands and in output. `[..]` matches any text within one line; counts that move with the tree (ADRs, Quadlets, ports, pages) are written as `[..]`.

Fixtures a case reads live beside this file (`fastfetch/mock.jsonc`, which `check_fastfetch` also compares). A case must never write to the tree: positive controls use `--check`. Surfaces with their own crate keep their golden in that crate's integration test (`mios-template-compile`: `tools/native/mios-template-compile/tests/compile_templates.rs`).

## 3. Parity Execution Contract

A golden case must hold for **both** the legacy script and the compiled replacement binary while the port is in flight, so the same block can be replayed with trycmd against either:

```bash
# Native Rust binary against the committed cases
cd tools/native && cargo test -p mios-gen --test golden
```

### Exit Code Invariants
- `0`: Clean execution, zero violations.
- `1`: Violations found (lints, schema mismatch, contract failures).
- `2`: Could not run (missing arguments, unreadable files, corrupt TOML). Never emit exit `0` on unread or unparseable input.

### Output Formatting
- Default output format is human-readable text.
- `--format json` emits OpenAI-format structured JSON:
  ```json
  {
    "check": "<surface>",
    "status": "clean" | "violations" | "could_not_run",
    "summary": "...",
    "findings": []
  }
  ```

## 4. Single-Commit Migration Protocol

1. Author the surface's golden cases in section 5, capturing current script behavior.
2. Scaffold crate via `mios new rust-crate <name>`.
3. Implement Rust logic reading from `mios-resolver` without carrying hardcoded literals.
4. Prove 100% byte-parity across positive and negative controls on the real tree.
5. Register the binary in `[build.native.categories]` and `[rust.categories]`.
6. Delete the legacy script in the **same commit** that proves parity. `git revert` of this commit serves as the atomic rollback.
7. Lower `[legibility]` line count ceilings using `mios-size-ceiling` and ensure all standing gates pass.

## 5. Cases

### adr-index

```console
$ mios-gen adr-index --root [CWD]/nonexistent --check
? 1
VIOLATION: no ADR front-matter collected under usr/share/doc/mios/adr/ -- ADR.md cannot be verified

$ mios-gen adr-index --check
ADR.md matches the [..] baked ADR(s) and SSOT consistency checks pass
```

### ai-manifest

```console
$ mios-gen ai-manifest --root [CWD]/nonexistent --check
? 1
Error: generate-ai-manifest: Repository root not found: [CWD]/nonexistent

$ mios-gen ai-manifest --check
[OK] AI repository and tool manifests are in sync
```

### bib-configs

```console
$ mios-gen bib-configs --root [CWD]/nonexistent --check
? 1
Error: generate-bib-configs: ERROR: [CWD]/nonexistent/usr/share/mios/mios.toml not found

$ mios-gen bib-configs --check
PASS: BIB artifact configs in sync with mios.toml SSOT.
```

### btop-theme

```console
$ mios-gen render-btop-theme --root [CWD]/nonexistent --check
? 1
Error: generate-render-btop-theme: Missing [dotfiles.registry.btop] in layered mios.toml

$ mios-gen render-btop-theme --check
[btop-theme] btop theme matches SSOT
```

### cargo-manifests

```console
$ mios-gen cargo-manifests --root [CWD]/nonexistent --check
? 1
[generate-cargo-manifests] FAIL: no crate directory under [CWD]/nonexistent/tools/native, so the projection would empty the workspace

$ mios-gen cargo-manifests --check
PASS: tools/native/Cargo.toml matches its generator projection.
```

### cosign-policy

```console
$ mios-gen cosign-policy --root [CWD]/nonexistent --check
? 1
Error: generate-cosign-policy: [CWD]/nonexistent/usr/share/mios/mios.toml not found

$ mios-gen cosign-policy --check
[OK] usr/lib/containers/policy.json is in sync with SSOT
```

### egress-firewall

```console
$ mios-gen egress-firewall --root [CWD]/nonexistent --check
? 1
Error: generate-egress-firewall: Failed to read [CWD]/nonexistent/usr/share/mios/mios.toml: [..]

$ mios-gen egress-firewall --check
[egress-fw] ./usr/share/mios/security/egress.nft matches SSOT
```

### fastfetch

```console
$ mios-gen render-fastfetch --check
? 1
fastfetch --check requires --out <artifact> to verify; nothing was compared

$ mios-gen render-fastfetch --root [CWD]/nonexistent --mock
? 1
Error: generate-render-fastfetch: Missing SSOT toml file: [CWD]/nonexistent/usr/share/mios/mios.toml

$ mios-gen render-fastfetch --mock --check --out tests/golden/fastfetch/mock.jsonc
[fastfetch] fastfetch configuration matches projection
```

### gate-index

```console
$ mios-gen gate-index --root [CWD]/nonexistent --check
? 1
Error: generate-gate-index: [CWD]/nonexistent/automation/98-drift-checks.sh not found

$ mios-gen gate-index --check
PASS: drift-gate-index.tsv is in sync.
```

### metal-vs-hosted

```console
$ mios-gen metal-vs-hosted --root [CWD]/nonexistent --check
? 1
generate-metal-vs-hosted: cannot read the SSOT: [..]

$ mios-gen metal-vs-hosted --check
[generate-metal-vs-hosted] usr/share/doc/mios/reference/metal-vs-hosted.md matches the SSOT
```

### pipe-boundaries

```console
$ mios-gen pipe-boundaries --root [CWD]/nonexistent --check
? 1
[gen-pipe-boundary-manifest] agent-pipe directory not found: "[CWD]/nonexistent/usr/lib/mios/agent-pipe/mios_pipe"

$ mios-gen pipe-boundaries --check
[gen-pipe-boundary-manifest] usr/share/mios/pipe-boundaries.manifest.json matches the tree ([..] modules).
```

### pipeline-index

```console
$ mios-gen pipeline-index --root [CWD]/nonexistent --check
? 1
Error: generate-pipeline-index: ERROR: [CWD]/nonexistent/automation not found

$ mios-gen pipeline-index --check
PASS: pipeline-index.tsv is in sync.
```

### pod-quadlets

```console
$ mios-gen pod-quadlets --root [CWD]/nonexistent --check
? 1
Error: generate-pod-quadlets: cannot read [CWD]/nonexistent/usr/share/mios/mios.toml: vendor SSOT is missing

$ mios-gen pod-quadlets --check
[pod-gen] all [..] Quadlet unit(s) match SSOT
```

### render-desktop

```console
$ mios-gen render-desktop --root [CWD]/nonexistent --check
? 1
Error: generate-render-desktop: render-desktop: [CWD]/nonexistent/usr/share/mios/mios.toml could not be read: [..]

$ mios-gen render-desktop --check
[render-desktop] All .desktop launchers match SSOT
```

### render-globals

```console
$ mios-gen render-globals --root [CWD]/nonexistent --check
? 1
Error: generate-render-globals: render-globals: [CWD]/nonexistent/usr/share/mios/mios.toml does not exist

$ mios-gen render-globals --check
[render-globals] both resolvers match SSOT ([..] constants)
```

### render-manpages

```console
$ mios-gen render-manpages --root [CWD]/nonexistent --check
? 1
Error: generate-render-manpages: render-manpages: [CWD]/nonexistent/usr/share/mios/mios.toml could not be read: [..]

$ mios-gen render-manpages --check --validate
[render-manpages] [..] page(s) validated
[render-manpages] [..] page(s) verified
```

### render-ports

```console
$ mios-gen render-ports --root [CWD]/nonexistent --check
? 1
Error: generate-render-ports: render-ports: [CWD]/nonexistent/usr/share/mios/mios.toml could not be read: [..]

$ mios-gen render-ports --check
[render-ports] [..] ports derive cleanly from [..] categories
```

### roadmap-index

```console
$ mios-gen roadmap-index --root [CWD]/nonexistent --check
? 1
Error: generate-roadmap-index: ERROR: ROADMAP.md not found at [CWD]/nonexistent/ROADMAP.md

$ mios-gen roadmap-index --root [CWD] --check
[roadmap-index] ROADMAP.md index is in sync
```

### standardize-docs

```console
$ mios-gen standardize-docs --check
[standardize-docs] specs/ markdown documentation is standardized ([..] files).
```

### sync-wiki

```console
$ mios-gen sync-wiki --root [CWD]/nonexistent --check
? 1
Error: generate-sync-wiki: Root directory does not exist: [CWD]/nonexistent

$ mios-gen sync-wiki --check
[sync-wiki] documentation embeds are in sync ([..] files scanned).
```

### tmux-theme

```console
$ mios-gen render-tmux-theme --root [CWD]/nonexistent --check
? 1
Error: generate-render-tmux-theme: Missing SSOT toml file: [CWD]/nonexistent/usr/share/mios/mios.toml

$ mios-gen render-tmux-theme --check
[tmux-theme] tmux theme matches SSOT (rounded)
```
