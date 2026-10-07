# Lane 3 review and editor configuration pre-flight

Reviewed on 2026-10-07 against base `0624ddc1` and the live Phase 3.20 draft in `/mnt/c/MiOS`. The implementation lane was still editing the shared checkout. This report is a review of that snapshot, not certification of a later commit.

## Findings requiring Phase 3.20 owner action

| Priority | Location | Evidence and required correction |
| --- | --- | --- |
| P1 | `tools/native/mios-gen/src/fastfetch.rs:258` | The default target is never selected: `out.map(PathBuf::from)` leaves it absent. A corrupted default config followed by `render-fastfetch --root FIXTURE --check` exits 0 and announces a match. Default rendering also exits 0 without replacing the corrupted file. Resolve the default target under the supplied root and require a real comparison for every check. |
| P1 | `automation/98-drift-checks.sh:5116` | `check_fastfetch` invokes `--mock` without `--check` or a target comparison. The gate exits 0 on an isolated tree containing a deliberately corrupted deployed config. The negative control at `tests/drift-gate-negatives.sh:5349` only corrupts a color; it does not prove configuration drift detection. Add an artifact-corruption negative control whose diagnostic names the target drift. |
| P2 | `tools/native/mios-gen/src/fastfetch.rs:242` | Missing vendor TOML becomes `None`, then compiled defaults. `render-fastfetch --root ABSENT --mock` exits 0. ADR-0021 failure policy requires missing required inputs to fail explicitly; distinguish an intentional mock fixture from an absent subject. Vendor-only parsing also bypasses the existing layered resolver. |
| P2 | `tests/golden/fastfetch/cases/positive_mock.trycmd:3` | Fixture expects 26 lines/1076 bytes; the compiled command returns 106 lines/1759 bytes. No executable trycmd runner or trycmd dependency was found in the searched test/tool/workflow sources. A fixture file alone is not an executed parity gate. |
| P2 | `tools/native/mios-gen/tests/fastfetch.rs:95` | Tests use the live repository TOML and a fixed output filename inside the checkout. Use per-test temporary roots with explicit fixture input; otherwise concurrent tests/agents collide and operator edits affect the result. |
| P2 | `tools/native/mios-gen/src/fastfetch.rs:120` | Host `/etc/os-release` takes precedence over the supplied root. The same fixture can render differently on different runners. Define runtime metadata versus reproducible build projection explicitly. |

Other observations: `tools/sync-generated.sh` has no fastfetch dispatch in the reviewed draft. The source carries inherited fixed model/port display text rather than resolved SSOT facts. It makes no network request, so the strings alone are not evidence of a vendor-cloud request. The inspected `/usr/bin/mios-gen` has a glibc program interpreter; it is a development build, not proof of release static linkage. A release artifact still needs the actual static-linkage gate.

## Reproduction receipts

Diagnostics ran inside `podman-MiOS-DEV`, using disposable `/tmp` fixtures. Shared TOML and deployed configuration were not modified.

Inspected debug binary SHA-256: `eccae3834c2f4fcf0d67d7e80d5a9cbe415eee4ada7867d1a9a8bb1f4a49616b`.

| Control | Result |
| --- | --- |
| Explicit `--out` render | Exit 0; generated JSON parses and contains 21 modules. |
| Explicit `--out --check` on matching artifact | Exit 0. |
| Explicit `--out --check` on planted corruption | Exit 1, diagnostic names fastfetch configuration drift. |
| Default `--check` on the same planted corruption | Exit 0: defect reproduced. |
| Default render on corrupted default artifact | Exit 0, artifact unchanged: defect reproduced. |
| `check_fastfetch` on corrupted fixture artifact | Exit 0: defect reproduced. |
| Missing root with `--mock` | Exit 0: missing subject accepted. |
| Editor checker: one approved route plus an external route | Original checker says compliant. |
| Editor checker: local endpoint and synthetic `sk-local` key | Original checker says non-compliant. |

Legacy mock JSON is 106 lines/1766 UTF-8 bytes; native mock JSON reports 106 lines/1759 bytes. Unicode escaping and trailing-newline behavior differ. Current stdout prefixes differ too. Preserve the ADR byte-parity contract or document an explicit contract change; do not call semantic similarity byte parity.

## Editor checker correction prepared

Branch `peer/editor-compliance`, worktree `/mnt/c/worktrees/peer-editor-compliance` (Windows `C:/worktrees/peer-editor-compliance`). Changed only the existing checker and its existing test class. No new generator, service or build system was introduced.

The checker now walks supported endpoint fields in nested editor settings, parses URLs, checks each route against the configured local endpoint pair, rejects userinfo/query/fragment and unexpected ports, and reports field paths rather than credential values. Unrelated schema URLs and prose cannot establish local routing. Local placeholder credentials no longer produce the original false positive. This is a bounded correction of the legacy checker; it is not a claim that the entire legacy projector satisfies the future unified-endpoint contract.

Two-sided proof: the augmented suite ran 15 tests against the original checker and reported 10 failing assertions/subtests for the reproduced conditions. After the correction, all 15 tests passed, including existing generation checks and 7 unapproved-route cases. The tests run without contacting any endpoint. `git diff --check` passed.

The broader legibility gate ran with translated Git metadata because Windows worktree `.git` pointers are not directly usable by Linux Git. It measured Python 77425/81101, within ceiling. It remains red on unaffected PowerShell 29178/27878, shell 55488/54941 and tracked files 3782/3662. The branch changes only two existing Python files, so these three failures are outside its diff. No ceilings were raised. The initial metadata error was an environment artifact, not a missing repository.

## Phase 3.21 Rust port specification

Implement the editor projector in the existing `mios-gen` category. The integration owner wires the CLI and SSOT registrations after merging the disjoint module. Preserve the target modes and output filenames until an explicit compatibility decision changes them. Read the existing layered `mios-resolver`; do not add another vendor-only TOML reader.

1. Resolve and expand `MIOS_AI_ENDPOINT` once. All generated completion, chat, autocomplete and embedding routes must use that approved OpenAI-compatible `/v1` endpoint. Do not retain an independent compiled inference-port fallback. Resolve model/embed model and credential references through the approved configuration surface, without logging secret values.
2. Resolve template references such as `${MIOS_PORT_AGENT_PIPE}` before output. The current Python projector can emit that literal placeholder; valid-looking text containing `localhost` is not a routable URL.
3. Treat model provider/transport and every endpoint-bearing field as data to validate. An unrelated approved URL must not excuse another route or a provider that ignores the configured route. Missing route fields, unknown provider transports and implicit cloud defaults must fail the compliance check.
4. Require real required input reads. Report malformed TOML/JSON, missing files and permission errors with paths and the agreed ADR exit code. Never silently fall back after a resolver error. Mock mode must not be reported as certification of an on-disk file.
5. Define generate, check, dry-run and mock behavior separately. Check reads and compares/validates real targets without writing. Dry-run reports intended targets without claiming files were written. Use deterministic JSON serialization and explicit mock fixtures.
6. Keep diagnostics secret-free. A bad key is identified by field path or category, never its value. An output artifact may contain the operator-authorized credential needed by its consumer; prevent that artifact from being accidentally committed or included in telemetry.
7. Capture actual legacy CLI golden behavior before deletion. If security corrections intentionally change legacy behavior, record that change separately from byte-parity claims. Wire the runner and run both positive and negative fixtures.
8. Delete the legacy script only in the same integrated change that wires all callers, equivalent coverage, projection, native registry and required gates. Do not convert the old suite into skips without proving replacement coverage. Sign/certify the release static artifact; a debug CLI smoke test is insufficient.

| Area | Positive control | Negative control / required evidence |
| --- | --- | --- |
| Resolution | Vendor/host/user override and environment precedence resolve an expanded endpoint. | Malformed upper layer, unresolved placeholder or missing endpoint fails for that input; no fallback. |
| Unified routing | Every route in every target equals the chosen endpoint, including autocomplete and embeddings. | Mix one approved route with an external/unapproved route; checker fails and names that field. |
| URL identity | Configured origin/path and permitted trailing slash accepted. | Hostname containing `localhost`, unexpected port, userinfo, query, fragment and malformed URL rejected. |
| Protocol | Explicit OpenAI-compatible provider uses the chosen route. | Unsupported provider and missing route rejected even when another field is local. |
| Credentials | Local placeholder and authorized secret reference accepted. | Synthetic disallowed credential rejected with no raw value in stdout/stderr/telemetry. |
| Files | Each target renders to its documented path; rerender is idempotent. | Missing/unreadable output subject in check mode fails; default check catches planted artifact drift. |
| No writes | Dry-run and mock leave per-test sentinel files unchanged. | Any write to the fixture or live repository causes the test to fail. |
| CLI/goldens | Captured stdout/stderr/status match the agreed contract. | Invalid flag/target and malformed inputs execute real failing golden cases. |
| Isolation | Tests own unique temporary directories and sanitized resolver inputs. | Run tests concurrently and verify no shared filenames or host metadata affect them. |
| Release | Actual release binary passes static-linkage for the declared target. | A known dynamic binary fails that gate for its interpreter/dependency. |

## T-1009 Unit 4 status

No native value-aliases implementation or completed path-join change was present in the searched `src/mios-rs/mios-gate` tree. Review of that future code remains pending; Linux success does not certify Windows path handling.

The current Python check passed on the live Linux tree. Isolated path-with-spaces control passed; planted divergent values failed with the `value-alias-drift` diagnostic; a snapshot exiting 7 failed explicitly instead of being treated as an empty successful corpus. Preserve all three controls in the native port. Windows path translation needs its own executed test on the actual adapter; do not change path strings speculatively or suppress the transport error.

## Committed Phase 3.19 btop correction

The existing `run_render_btop_theme` checked syntax of a present theme, or validated freshly rendered text when the target was absent. Both a syntactically valid wrong color and a missing artifact exited 0 with a match announcement. Disposable-fixture CLI probes reproduced both defects.

The peer correction requires the real target to exist, validates its syntax, compares its CRLF-normalized bytes with the rendered SSOT projection and never writes in check mode. Existing error exit behavior is preserved. Regex construction and capture access now return errors instead of using `unwrap()` in the generator path. The tests now use per-test temporary TOML/artifact roots instead of the live checkout.

The augmented Rust suite first ran on the original source: the original e2e case passed and both new regression tests failed for the expected false-success exits. After correction, all three passed. Coverage includes valid hex drift, missing default/custom target, unchanged negative subject, syntax failure, matching JSON response and CRLF-only normalization. Compilation and tests ran offline inside `podman-MiOS-DEV`, with a separate `/tmp/mios-peer-lane-target` build directory. Formatting passed using the installed host formatter; the Linux image has no rustfmt or clippy. Clippy remains unverified; no package was installed.

Broader package testing exposed pre-existing projection drift: `ai_manifest` fails on stale embedded content for unchanged `automation/98-drift-checks.sh` and `tools/sync-generated.sh`; regenerating those two isolated manifests made that test pass and exposed an existing `cargo_manifests` check failure on the committed native workspace manifest. The experiment's generated changes were restored only in this isolated worktree. These failures must not be relabeled as a green full package suite or hidden by changing ceilings. Package-wide results are recorded separately; targeted btop tests are green.

## Additional evidence from the supplied execution report

The supplied report directly edits `state.json`, appending purported sender messages without the relay. The native relay authenticates sender leases at `tools/native/mios-agent-relay/src/main.rs:479`, takes a file lock at line 584, syncs the temporary file at line 602 and persists it at line 608. A separate read/modify/write bypasses those checks and can overwrite concurrent receipts/messages or expose a partial JSON file. Stop direct state mutations; send through the native JSON action protocol with the sender's own token. An inbox entry injected by a file edit is not proof of an authenticated send.

The report also includes copying a debug build into a release directory. A path name is not release or linkage certification. Current inspection found a distinct release artifact, SHA-256 `93e3f3959fd7f6d8cf8cc2d32b2973220d933cc89736b06b2e69acd7dfded006`, matching the installed `/usr/libexec/mios/mios-gen`. Both request `/lib64/ld-linux-x86-64.so.2`. The actual `mios-gate static-linkage --root /mnt/c/MiOS --binary /mnt/c/MiOS/tools/native/target/release/mios-gen --arch x86_64` exits 1 with `static policy rejects ELF interpreter (PT_INTERP)`. The static Rust release invariant is therefore not verified by the reported eight standing gates. Use the configured image target and require the actual release artifact to pass this gate before claiming static certification.

The complete offline mios-gen package run with `--no-fail-fast` passed 63 cases and failed three targets: ai_manifest, cargo_manifests and roadmap_index. The roadmap fixture inherited GIT_DIR/GIT_WORK_TREE and reinitialized the shared Git configuration instead of an isolated repository. The induced core.worktree and identity changes were repaired from the recorded repository identity, and only the peer worktree's unintended symlink index changes were restored. Do not rerun fixture-creating tests with inherited Git repository overrides; sanitize Git environment variables inside each fixture command. Full package verification remains open.
