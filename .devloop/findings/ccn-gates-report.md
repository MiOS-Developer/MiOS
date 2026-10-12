<!-- AI-hint: MiOS -- dev-loop finding: the ccn-gates lane's audit of which drift
     gates can be driven red, with per-gate evidence and verdicts.
     AI-related: automation/98-drift-checks.sh, tests/drift-gate-negatives.sh -->
# ccn-gates — "Checks That Cannot Fail" audit of `automation/98-drift-checks.sh`

Lane: `ccn-gates` (nested manager). Target: 220 check functions in
`automation/98-drift-checks.sh` (4904 lines), its Python backend
`tools/drift-checks.py` (4736 lines), and the negative suite
`tests/drift-gate-negatives.sh` (5100 lines).

Method: six read-only subagents over disjoint line ranges, plus four mechanical
manager-level sweeps over the whole file. **The drift checks were never
executed** — they mutate the tracked tree — so every finding below is traced
statically through code that was read.

## Harness semantics (established first; every finding depends on these)

- `main()` (line 3687) wraps its dispatch list in `set +e`, so a check returning
  1 does **not** abort the run. The **only** failure signal is the global
  counter `VIOLATIONS`, incremented **exclusively** by `_violation` (line 48).
  `main()` exits 1 iff `VIOLATIONS > 0`.
- Corollary, and the criterion for sweep B below: **a check whose body never
  reaches `_violation` (directly or via `_violations_from` / `_run_py_check`)
  cannot fail, no matter what it prints.**
- `_need_python` (line 86): when `python3` is absent it prints only a
  **WARNING** and returns 1 — it violates *only* under
  `MIOS_DRIFT_REQUIRE_TOOLS=1`.
- `_violations_from` (line 98) is **not** vacuous on empty input: it violates
  with "the backing tool failed and produced no parsable output". Correct by
  construction; not reported as a defect anywhere below.
- `MIOS_DRIFT_CHECK_SOFT=1` makes `main()` exit 0 despite violations (line 3941).

---

## Manager sweep A — dispatch closure: no unwired checks (range read: whole file)

**Result: clean.** A check defined but never dispatched cannot fail regardless
of its quality, so this was the first hypothesis tested.

Extracting definitions (`^check_*()`) and `main()`'s dispatch block
(lines 3687–3947) independently:

```
defined:    220
dispatched: 220
DEFINED BUT NEVER DISPATCHED: (none)
DISPATCHED BUT NOT DEFINED:   (none)
```

Perfect bijection. Note that ~30 checks are *defined after* `main()` is defined
(e.g. `check_no_inert_ssot_tables` at 4793, `check_header_integrity` at 4890)
but before `main "$@"` is called at line 4904 — which is valid bash, and all of
them are dispatched. **No unwired-check finding. Phantom dismissed.**

---

## Manager sweep B — `check_unit_security` (line 4449) cannot fail: CONFIRMED

Defect classes: **Swallowed Failure + Skip-as-Pass + Measuring the Wrong
Property + Over-claiming PASS line.** Severity: highest in the file.

Mechanical criterion: of all 220 check bodies, exactly **one** never reaches
`_violation` by any path. (19 others flagged by the raw sweep are
`_run_py_check` one-liners at 4773–4790 and 4856, and two — `check_globals_ports`
at 902, `check_firstboot_degrade_open` at 1181 — are pure delegators; all 21 are
false positives of the crude test and were dismissed by reading them.)

`check_unit_security` contains **no `_violation` call whatsoever**, so
`VIOLATIONS` can never increment. Four independent mechanisms:

1. **Skip-as-Pass, unrescuable.** It bypasses `_need_python` and rolls its own
   guard, so `MIOS_DRIFT_REQUIRE_TOOLS=1` does **not** save it — this check
   skips silently even in CI:
   ```bash
   if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
       echo "[98-drift-checks]   WARNING: python missing" >&2
       return 0
   fi
   ```
   Related dead code: `local py_bin="python3"` / `py_bin="python"` is computed
   and then **never used** — the call hardcodes `python3`, so on a `python`-only
   host the guard passes and the call fails into mechanism 2.
2. **Swallowed Failure.** A crashed backing tool is a pass:
   ```bash
   if ! out="$(MIOS_DRIFT_ROOT="$ROOT" python3 tools/drift-checks.py unit-security "$ROOT")"; then
       echo "[98-drift-checks]   WARNING: systemd unit security check flagged unconfined services" >&2
       return 0
   fi
   ```
3. **The core defect — the backing tool always exits 0.**
   `tools/drift-checks.py:check_unit_security` (line 4257) ends:
   ```python
   if viol:
       print('\n'.join(viol))
   return 0
   ```
   and the dispatcher is `raise SystemExit(SUBCOMMANDS[sys.argv[1]]() or 0)`
   (line 4736) → `0 or 0` → **exit 0**. So branch 2 is unreachable, violations
   arrive on **stdout**, `[[ -n "$out" ]]` is true, and the wrapper prints the
   *reassuring*
   `NOTE: legacy unconfined units pending migration (roster active)`
   before falling off the end with status 0. The violation text is rendered as
   a success line.
   Same function, two further defects: `except Exception: pass` (Swallowed
   Failure — an unreadable unit is skipped) and `if os.path.isdir(systemd_dir)`
   with no `else` (Empty-Set Pass — a missing unit directory yields an empty
   violation set).
4. **Measuring the Wrong Property.** The Python tests `if directive not in
   content` — a bare substring match on the whole file, so the *name* of a
   directive counts as hardening regardless of its **value**, and a mention in
   a comment counts too.

**Blast radius if repaired** (§7 requires stating this — the gate is currently
passing real violations, so arming it makes CI redder). Measured read-only
against the tree:

- `[security.privileged_units].unconfined` roster: **2** entries.
- `.service` files in `usr/lib/systemd/system`: **106**.
- Non-roster services that **fail** the baseline the check claims to enforce:
  **84** (e.g. `k3s.service`, `mios-agents.service` missing `ProtectHome`,
  `hermes-worker-firstboot.service` missing all four).
- Services with a required directive **present but disabled**, which the
  substring test passes today — proof defect 4 is live, not hypothetical:
  **3** — `hermes-worker.service` (`NoNewPrivileges=false`, `ProtectHome=false`),
  `mios-hermes-browser-worker.service` (`PrivateTmp=false`),
  `mios-hermes-browser.service` (`PrivateTmp=false`).

So the PASS line claims a hardening baseline across 106 units while 84 of them
breach it and the "roster active" NOTE implies a curated 2-entry migration list
covers them. Recommend narrowing the claim *and* arming the gate, staged behind
the roster, per §7 ("narrow the claim, not widen the check") and §8 (retire by
removing the cause, not by growing the roster to 84).

---

## Manager sweep C — skip-path blast radius: 83 checks (Skip-as-Pass, conditional)

Defect class: **Skip-as-Pass**, whole-file scale. Status: **CONFIRMED
mechanism, conditional trigger.**

`_need_python || return 0` appears **83 times** (lines 262, 273, 308 … 4898).
Because `_need_python` only *warns* unless `MIOS_DRIFT_REQUIRE_TOOLS=1`, all 83
checks — 38% of the suite — return success on a host with no `python3`.

Whether that is live depends entirely on the invocation path, which I traced:

- `.github/workflows/mios-ci.yml:21` sets `MIOS_DRIFT_REQUIRE_TOOLS: "1"` →
  **in CI the mitigation is active** and these 83 fail loudly. Good.
- `Justfile`'s `drift-gate` target does **not** set it (it references the
  variable only at lines 108–109, for the `cargo` golden-master step). So a
  local `just drift-gate` runs with the default `0`, and on a host without
  `python3` those 83 checks pass while testing nothing.

The top-of-file Windows shim (lines 7–28) copies `python` → `python3` when only
the former exists, which narrows the trigger further: it requires a host with
*neither*. Honest characterisation: a real but narrow Skip-as-Pass, contained in
CI, exposed for operator-local runs. `check_no_silent_tool_skips` (line 3562) is
the meta-gate nominally policing this class — see the subagent section covering
lines 3361–4270.

Separately, `_require_python3` (line 197) records **nothing at all** when
`python3` is missing and `MIOS_DRIFT_REQUIRE_TOOLS != 1`; its caller at line
2246 (`check_resolver_ssot_refs`) uses `_require_python3 || return 0`, and five
callers (2625, 2820, 2925, 2937, 2989) use `if ! _require_python3; then` —
whether those branches violate is covered in the subagent sections for those
ranges.

---

## Manager sweep D — negative-test coverage gap: 2 law enforcers unprotected

Defect class: **enabling condition for every class above.** A check with no
negative test has nothing proving it *can* fail, which is how a gate silently
becomes vacuous. §6 makes the negative control the load-bearing half.

Of 220 checks, **39 are never mentioned anywhere in
`tests/drift-gate-negatives.sh`**. Because my match is substring-permissive (a
mention in a comment counts), **39 is a lower bound** on the truly unprotected
set. The list includes `check_negative_test_coverage` itself.

Cross-referencing against the law registry in `usr/share/mios/mios.toml [laws]`
(lines 2190–2205), which names a specific enforcing check per law, **two law
enforcers have zero negative coverage in any form** (hyphenated subcommand
spellings checked too):

| Law | Slug | Enforcer | Mentions in negative suite |
|---|---|---|---|
| 13 | NATIVE-DROPINS | `check_resolver_twin_parity` | **0** |
| 16 | ONE-TEMPLATE-PER-TYPE | `check_template_conformance` | **0** |

The other eight law enforcers are covered (3–10 mentions each:
`check_usr_over_etc` 4, `check_no_mkdir_in_var` 3, `check_projection_registry` 4,
`check_var_closure` 10, `check_target_languages` 3, `check_quadlet_privilege` 3,
`check_no_hardcode` 7, `check_lint_is_final` 3). Also uncovered and notable:
`check_unit_security` (sweep B — it has no negative test *because* it cannot
fail), `check_native_lint`, `check_python_lint`, `check_ps_signatures`,
`check_resolver_shell_equivalence`, `check_resolver_ps_equivalence`,
`check_comment_lex_equivalence`.

Recommend a negative test for Law 13 and Law 16 enforcers first — a law whose
named enforcer has never been proven to fail is the highest-leverage gap here.

## Subagent 1 — lines 215–1010 (39 checks read, all reachable)

Most severe first. All CONFIRMED by reading source; nothing was executed.

1. **`check_hummingbird` (715) — Skip-as-Pass + Over-claiming.** Gated on
   `local distroless_enable="${MIOS_CONV_IMAGE_DISTROLESS_ENABLE:-false}"` and
   the `rechunk_enable` twin. **Nothing in the drift-gate path ever sets
   either**: the only producer,
   `usr/libexec/mios/system-sync-env.sh:128-129`, re-emits them *only if already
   set*, and `Justfile:82` never exports them. The real SSOT values sit at
   `mios.toml:8554-8556` and this check never opens the TOML for them. Both
   flags are therefore permanently `false`, making all four gated assertions
   unreachable dead code — flipping `distroless_enable = true` in the SSOT buys
   **zero** coverage. This is verbatim the bug `check_converge_ssot`'s own
   comment (639-650) documents as fixed; the sibling was missed in that sweep.
   Also the only check in the range using **cwd-relative** subject paths instead
   of `$ROOT`, so it grades the wrong tree under a redirected root.
2. **`check_package_registry` (337) — Swallowed Failure → Skip-as-Pass.** The
   inline `python3 -c` reading `[ai].package_registry` runs *before*
   `_need_python` and ends `' 2>/dev/null || echo "false")`. Any failure — no
   python3, missing `tomli`, a TOML syntax error, unreadable file — collapses to
   `_en_val="false"` and routes to the dormant branch, printing
   `"package registry dormant"` and `return 0`. An **enabled** registry whose
   generated `registry.json` was deleted reads as a clean pass and the
   `mios-registry verify` leg that does the real SSOT comparison never runs.
3. **`check_blade_dropins` (542) — Empty-Set Pass / one-directional diff.**
   `for f in "$generated_dir"/*; do [[ -e "$f" ]] || continue` iterates **only
   generated files**, so the generated set is never asserted non-empty and
   committed files with no generated counterpart are never examined.
   `bare-metal-only.conf`, `mios-virt-gate.conf`, `mios-wsl2.conf` and
   `virt-gate.conf` ship in `usr/share/mios/dropins/` but are never generated,
   so they are hand-editable without failing a gate whose PASS line claims the
   directory is "in sync with mios.toml [blade.requires]" (Law 8). Removing a
   capability from `[blade.requires]` also orphans its `blade-<cap>.conf`
   unflagged.
4. **`check_names_registry` (927) — Skip-as-Pass.** The `git ls-files --deleted`
   guard (and the "not a git work tree" guard above it) only `_violation` under
   `MIOS_DRIFT_REQUIRE_TOOLS=1`, then
   `echo "… names registry NOT verified" >&2; return 0`. On any local run a
   single tracked-but-deleted file *anywhere* in the tree disables the whole
   projection check — precisely the failure the in-function comment claims to
   have closed. **The fix only landed for the CI tier.**
5. **`check_raw_toml_readers` (459) — Measuring the Wrong Property.** The regex
   `os\.environ(\.get)?\(["']MIOS_TOML["']\)` demands a literal `)` after the
   closing quote, so both idiomatic spellings evade: `os.environ["MIOS_TOML"]`
   (subscript) and `os.environ.get("MIOS_TOML", "/usr/share/mios/mios.toml")`
   (a `,` where the regex wants `)`) — the latter being exactly the form a real
   offender takes, since it needs a fallback. The companion
   `grep -E "open\(" | grep -q -E "mios\.toml"` needs both tokens on one
   physical line. Scope compounds it: `find -maxdepth 2` sees 332 of 452 `.py`
   files; all 120 at depth ≥3 (`mios_pipe/routing/*`, `memory/*`, `identity/*`)
   are invisible. No live evader today — latent.
6. **`check_module_test_coverage` (413) — Skip-as-Pass + Self-Certifying.**
   `if [[ -f "$baseline_file" ]]; then` has **no `else`**, so deleting or
   renaming `usr/share/mios/reference/python-untested-baseline.txt` silently
   removes the entire coverage ratchet with no violation and no warning, while
   the first leg's PASS line has already printed. Both legs are Self-Certifying:
   they assert only that a *file named* `test_<module>.py` exists, so a
   zero-byte file satisfies "has a sibling unit test".
7. **`check_unwired_modules` (615) — Skip-as-Pass in the backing tool.** Wrapper
   is sound, but `tools/drift-checks.py:518-523` does `sys.exit(0)  # nothing to
   check on a bare checkout` when `usr/lib/mios/agent-pipe` is not a directory
   (unless `MIOS_DRIFT_REQUIRE_TOOLS=1`). A missing or renamed tracked directory
   becomes a pass printing "no imported-but-dead agent-pipe module", where
   siblings (`check_module_boundary`, `check_cli_sql_safety`) correctly violate.
8. **`check_converge_ssot` (636) — Skip-as-Pass.** The function's own comment
   names this "the one assertion that inspects a real systemd unit", then leaves
   it behind `if command -v systemctl …` with no else. In any container or bake
   context without `systemctl`, `retire_heavy_alt=true` plus a still-enabled
   `mios-llm-heavy-alt.service` records nothing, and line 711 prints
   `"[converge] SSOT values validated (… retire_alt=true)"` — **naming the value
   it did not verify.** Rest of the check is fail-closed.
9. **`check_dead_lane` (215) / `check_retired_models` (238) — Measuring the
   Wrong Property + Empty-Set Pass.** Law 5's registry is `[docs].retired_ports`,
   thirteen ports at `mios.toml:10898`; `check_dead_lane` hardcodes
   `local pattern=':11434'` — one of them — so adding a port to the registry
   adds zero coverage, and the gate itself contains a Law-7 hardcode.
   `check_retired_models` is worse: `gemma4|qwen3:1\.7b` has **no SSOT registry
   behind it at all** (no `retired_models` key exists). Both also Empty-Set:
   `[[ -d "$d" ]] || continue` over five paths plus `find … 2>/dev/null`, so a
   wrong `$ROOT` scans zero files and prints the PASS line.
10. **`check_module_boundary` (285) — scope gap (latent).**
    `find "$dir" -maxdepth 1 -type f -name 'mios_*.py'` excludes all 129 `.py`
    files under `mios_pipe/`, so `import server` in
    `mios_pipe/routing/dispatcher.py` is invisible — though
    `check_module_test_coverage` 70 lines below *does* walk `mios_pipe/`
    recursively, so the gate knows those files exist and still exempts them.
11. **`check_cli_sql_safety` (386) — Over-claiming PASS line.** The sentence
    `"libexec CLIs SQL-safe"` rests on a six-alternative grep for *retired named
    idioms* (`_pgesc\(|_pgq\(|post_sql\(|def _sql\(|/sql"|surreal-ns`). A
    hand-rolled f-string or `%`-formatted query — the actual injection class —
    matches none of the six and prints PASS. Empty-Set too if
    `find -maxdepth 1 -type f` yields nothing.
12. **`check_pod_quadlets` (507) — Empty-Set Pass in the backing tool.**
    `tools/generate-pod-quadlets.py:282-284` returns 0 with
    `"[pod-gen] no Quadlets in SSOT -- nothing to do"` when all five tables are
    empty, and the wrapper then prints "Quadlet units in sync with mios.toml
    SSOT" having compared nothing. A rename of `[pods]`/`[containers]`/
    `[networks]`/`[volumes]`/`[images]` triggers it. Otherwise `--check` is
    genuinely bidirectional.
13. **`check_no_mkdir_in_var` (1009) — Empty-Set Pass + evadable pattern
    (latent).** `for f in "$ROOT"/automation/[0-9]*.sh "$ROOT"/Containerfile*` —
    both globs stay literal when unmatched, `-f` rejects them, zero files are
    scanned and PASS prints, so a wrong `$ROOT` silently retires **Law 2**. The
    pattern `mkdir[^;&|#]*['\''"[:space:]]/var/` requires `/var/` preceded by a
    quote or whitespace, so `mkdir -p "${DESTDIR}/var/lib/x"` evades, and
    `install -d /var/...` — the same imperative-creation defect — is entirely
    outside it. Neither evasion is present today.

Cleared as sound: `check_userenv_parity` (872, a model fix — absent twin
violates rather than skips), `check_egress_firewall` (523, renders to a real tmp
location via `MIOS_EGRESS_OUT`, so not a self-comparison), and ~20 wrapper
checks. `check_hint_coverage` (271) is a Count-Only Ratchet with a raisable
ceiling (`[ai_tag].max_untagged = 42`) but its PASS line says "within ratchet
ceiling", so it does not over-claim. Backing-subcheck bodies beyond the two
traced are **UNVERIFIED**.

Hygiene note: `check_package_registry`, `check_no_hardcode`,
`check_no_hardcode_version` and `check_dotfiles_projection` write `.pkgreg.err`,
`.nohc.err`, `.nohc_ver.err`, `.dotfiles.err` into the **tracked worktree root**;
removed on both branches, but a crash between write and `rm` litters the repo.

## Subagent 3 — lines 1786–2570 (34 checks read, all reachable)

1. **`check_negative_test_coverage` (2541; backing `tools/drift-checks.py:1447`)
   — Count-Only Ratchet + Self-Certifying Predicate.** The meta-gate meant to
   prove the other gates can fail. It asserts (a) a bare **count** of `test_*()`
   functions in `tests/drift-gate-negatives.sh` against a count of 63 hardcoded
   names **with no mapping between them**, and (b) that each required name
   appears *anywhere in the file as a substring* — a comment, an `echo`, or the
   `required_checks` list itself satisfies it. So every negative test can be
   gutted to `test_foo() { :; }` and the gate still certifies "negative test
   coverage ratchet verified". The floor is the hardcoded list in the same repo,
   so deleting a name deletes the requirement.
2. **`check_ratchet_direction` (1959; backing
   `src/mios-rs/mios-gate/src/ratchet.rs`) — Self-Comparison / Raisable
   Ratchet.** `resolve_baseline` falls back to **`HEAD`**, and `MIOS_RATCHET_BASE`
   is set nowhere in the repo. `.forgejo/workflows/build-mios.yml` checks out
   with `fetch-depth: 1`, so either no `origin/main` ref exists or
   `merge-base HEAD origin/main == HEAD`; either way
   `git show <base>:usr/share/mios/mios.toml` is byte-identical to the worktree
   file, `findings` is empty and the binary exits 0. The tool admits this in
   `describe_baseline` ("a committed raise passes; see task_983") but that text
   lands only in a summary string while the wrapper branches solely on exit
   status. **All ~78 shrink-only ceilings in `mios.toml` are freely raisable in
   CI.**
3. **`check_bake_budget` (2301) — Raisable Threshold + Measuring the Wrong
   Property.** Both sides of the inequality are same-commit editable and neither
   is measured. The numerator reads the `size_gb` column of the tracked
   `bound-images.tsv`, whose generator never measures anything —
   `tools/native/mios-bake-plan/src/main.rs:496` does
   `existing_sizes.get(img).cloned().unwrap_or_else(|| "1.0".to_string())`,
   copying forward whatever the file already said. The denominator
   `build.bake.runner_disk_budget_gb = 40` is **not** a ceiling under
   `is_ceiling_key` (no `max_` prefix, no `_ceiling` suffix, section `build`
   absent from `RATCHET_SECTIONS`), so finding 2 lets it rise too. PASS line
   claims "projected baked image size within the SSOT disk budget".
4. **`check_sbom_metadata` (2119) — Empty-Set Pass / Skip-as-Pass, live today.**
   `usr/share/mios/artifacts/sbom/` currently holds only `bound-images.tsv`;
   `models.tsv` and `binaries.tsv` **do not exist and are not tracked**, so two
   of three loops never execute and the check prints "SBOM metadata manifests
   are structurally valid" right now while validating one file. Deleting the
   whole directory also passes (outer `-d` guard, `bad` stays empty).
5. **`check_shellcheck` (2107) — Skip-as-Pass.** `automation/lint-shell.sh` exits
   2 when shellcheck is missing and unprovisionable; the `elif [[ $rc -eq 2 ]]`
   branch warns and — **unlike every other soft skip in the file** — never
   consults `MIOS_DRIFT_REQUIRE_TOOLS`, so even CI's `"1"` cannot make it loud.
   Also `lint-shell.sh` exits 0 on "No shell scripts found to lint", and its
   modified-files pass is guarded by `[ -d ".git" ]`, **false in a worktree**
   (`.git` is a file), so that half silently does not run here.
6. **`check_python_lint` (2517) — Skip-as-Pass / Swallowed Failure.**
   `bash "$lint_script" >/dev/null 2>&1 || res=$?` then only `res -eq 1`
   violates. `lint-python.sh` exits 2 when `python3` is absent (and 0 on "no
   Python files found"), so any other non-zero — 2, 126/127 — passes with **no
   message at all** beyond the header, indistinguishable from a real pass. The
   `>/dev/null 2>&1` also discards the per-file `ERROR:` lines on genuine
   failure.
7. **`check_rechunk_budget` (2484) — Measuring the Wrong Property.** Asks only
   whether the string `rechunk_max_layers` appears somewhere in
   `automation/build/rechunk.sh` — a comment satisfies it — and whether an
   `=`-joined integer is absent, which the space-separated `--max-layers 67`
   trivially evades. Nothing verifies the value resolves from SSOT. PASS line
   claims "rechunk budget & SSOT image reference verified".
8. **`check_nested_podman_caps` (2266) — Skip-as-Pass + keyword proxy.** Only the
   reference doc's absence violates; if `mios-ci.yml` or
   `usr/libexec/mios/57-mios-sys-build.sh` is renamed, its whole flag block is
   skipped and the check prints success. The greps (`grep -q -- "--cap-add"`,
   `grep -q "image exists"`) are unanchored presence tests a comment satisfies.
9. **`check_curl_retry` (2185) — Measuring the Wrong Property.** The scanner only
   inspects lines carrying a *literal* `http(s)://`, so the tree's dominant idiom
   — `curl -fsSL "$SOME_URL" -o out`, as in `automation/21-virt.sh`,
   `25-gpu-cdi-toolkits.sh`, `36-ceph-k3s.sh`, `49-cosign-policy.sh` — is never
   examined. The retry test is an unanchored whole-line substring, so a trailing
   comment containing `--retry` exempts the line.
10. **`check_target_languages` (1984) — Over-claiming + Unanchored Allowlist.**
    Law 14 bans "C#/Batch/Go/PowerShell-as-program", but **no `*.ps1` pattern is
    ever scanned** (50 `.ps1` files tracked), nor `.c`, `.h`, `.java`, `.rb`. The
    `grep -v '^tools/mios-portal-app/'` exemption is a blanket directory
    carve-out for *all* banned extensions, hardcoded in the gate rather than
    itemised in SSOT (unlike `grandfathered_cs`).
11. **`check_bake_ref_defaults` (2055) — Skip-as-Pass with a false PASS line.**
    When `python3` is absent the parity half is skipped and the `else` prints the
    unqualified claim "all baker scripts have non-empty defaults for their
    bake-refs" — not a WARNING, not a violation, and `MIOS_DRIFT_REQUIRE_TOOLS`
    is not consulted in that branch.
12. **`check_ssot_lint_equivalence` (2568) — Skip-as-Pass, ungated.** The
    binary-absent path correctly escalates, but the `.exe`-on-Linux path returns
    0 unconditionally, bypassing `MIOS_DRIFT_REQUIRE_TOOLS` entirely, so an
    unrunnable twin certifies equivalence. Preceding
    `cargo build … >/dev/null 2>&1 || true` masks the build failure.
13. **`check_gate_registry` (2507) — Self-Certifying Predicate (narrow).**
    Whether a `tools/check-*.py` must be registered is decided by the first three
    lines of its **own AI-hint text** (`if "drift check" in tc_hint`), so a real
    orphaned drift check escapes the registry requirement by rewording its own
    header. The `tc_name not in sh_text` test is an unanchored whole-file
    substring match.
14. **`check_root_toml_subset` (1786) — Unanchored Allowlist (low).** Exemptions
    are raw `startswith` prefixes with no `.` boundary, so `containers` also
    exempts `containers_extra`, `quadlets` exempts `quadlets_legacy`, and
    `ports.lan_firewall` exempts `ports.lan_firewall_v2`.
15. **`check_soft_mode_not_committed` (2551) — narrow subject set (low).** All
    four subjects exist today, but `*.yaml` workflows, `.github/actions/*`,
    `automation/build/*.sh` and the `Containerfile` are outside the subject set,
    so a committed `MIOS_DRIFT_CHECK_SOFT=1` there is never seen while the PASS
    line claims "no soft-mode override committed in CI/build pipeline".
16. **`check_hyprland_conf_heredoc` (2171) — Empty-Set Pass (PLAUSIBLE, low).**
    If the baker's heredoc is refactored away *and* the target conf is also gone,
    both temp files are empty, `diff` succeeds and the gate reports "in sync".
    Needs both sides missing, hence low.

Cleared as sound: `check_render_extension_coverage`, `check_render_quadlets`,
`check_toolchain_pin`, `check_size_ceiling`, `check_bake_plan` (all `_violation`
when their binary is unbuilt — correct), `check_greenboot`, `check_clevis_luks`
(a genuine regenerate-and-diff), `check_metal_vfio`, `check_router_parity`.

## Subagent 5 — lines 3361–3686 + 3948–4270 (29 checks read; `main()` excluded)

1. **`check_no_silent_tool_skips` (3562) — THE VACUOUS META-GATE.** Swallowed
   Failure + Unanchored Allowlist + Measuring the Wrong Property +
   Over-claiming. This is the sole Skip-as-Pass detector in the entire
   repository, and it fails in four independent layers:
   - **(a) Warn-only where it matters.** `Justfile:122` invokes the script with
     no `MIOS_DRIFT_REQUIRE_TOOLS`, so `require_tools=0` and even a *positive*
     detection takes the `else` branch — an echo, no `_violation`. In
     `just drift-gate` the check is structurally incapable of failing.
   - **(b) The detector matches nothing that exists.** The regex
     `command -v.*\|\|[[:space:]]*(return 0|exit 0)` demands both tokens on one
     physical line. Run read-only against its own scope: **0 hits**, while that
     scope contains **83** `_need_python || return 0` sites and **8** multi-line
     `if ! command -v …; then … return 0` sites — **91 real Skip-as-Pass sites,
     all invisible**, because the regex knows nothing of the
     `_need_python`/`_require_python3` wrappers that replaced the idiom it hunts
     ("Folded from 56 copies", line 89). Every `lint-*.sh` in scope uses the
     multi-line form; the detector sees none of them.
   - **(c) Unanchored Allowlist.** `grep -v 'MIOS_DRIFT_REQUIRE_TOOLS'` exempts
     any line merely *mentioning* the variable, with no requirement that it be
     enforced — and line 3570 itself contains the string, so the detector
     self-exempts.
   - **(d) Its negative test is shaped to the regex, not the property.**
     `tests/drift-gate-negatives.sh:3095-3105` plants a synthetic
     `command -v non_existent_tool || return 0` fixture. The harness certifies
     "the gate works" by proving the regex matches its own fixture while the 91
     production idioms pass untouched.

   **Blast radius: all 220 checks; 91 confirmed live Skip-as-Pass sites go
   unpoliced.** This is the root cause that makes sweep C, SA1 §14 and SA3 §17
   invisible. The gate index publishes the over-claim verbatim
   (`drift-gate-index.tsv:200`).
2. **`check_ps_repo_parity` (4062) — Swallowed Failure.** The heredoc's stderr is
   **not** captured (`2>&1` absent), so an interpreter-level failure — malformed
   `mios.toml`, `TOMLDecodeError`, an unreadable mirrored file — yields `rc=1`
   with `report=""`. The hand-rolled loop then reads one empty line,
   `[[ -n "$line" ]]` is false, **zero `_violation` calls**, function returns.
   This is exactly what `_violations_from`'s empty-blob guard exists to close,
   and this call site hand-rolls the loop **without** it.
3. **`check_unpinned_runtime_fetches` (4266) — Unanchored Allowlist, with a live
   pass-through.** Both tests are **whole-file** greps, so one verified download
   anywhere in a file exempts every other fetch in it. **Live instance:**
   `usr/share/mios/windows/mios-ai-node.ps1:248` fetches an NVIDIA repo file via
   `curl -fsSL` with no integrity check and passes solely because line 361 runs
   `sha256sum -c -` for an unrelated GGUF. The detector also misses
   `Invoke-RestMethod`, `iwr`, `irm`, `Start-BitsTransfer`,
   `WebClient`/`DownloadFile`, `wget`, `aria2c`, and scans one non-recursive
   directory while 8 other tracked `.ps1` files carry fetch verbs outside it.
4. **`check_ps_signatures` (4190) — Skip-as-Pass.** No `pwsh`/`powershell` →
   warning only, and unlike its sibling it **ignores `MIOS_DRIFT_REQUIRE_TOOLS`
   entirely**, so even CI cannot make it fail closed.
5. **`check_powershell_parse` (4050) — Skip-as-Pass by delegation.** Wrapper is
   correct, but `automation/lint-powershell.sh:21-24` exits 0 with a WARNING when
   no PowerShell binary is found (its AI-hint says "Degrades open") and `:44-47`
   exits 0 on an empty file set.
6. **`check_native_lint` (3968) — Skip-as-Pass, the most silent in the file.**
   `if ! command -v cargo …; then return 0; fi` — no violation, **no WARNING, no
   message of any kind**, no `MIOS_DRIFT_REQUIRE_TOOLS`. Without a Rust
   toolchain both workspaces — including the `src/mios-rs` tree its own comment
   calls "12k lines the gate never compiled" — go uncompiled and the run is
   clean.
7. **`check_value_aliases` (3405) — Skip-as-Pass.** A hand-rolled copy of
   `_need_python` that is *worse* than the helper: it omits even the WARNING
   echo, so the check returns 0 in total silence.
8. **`_need_python || return 0` in range:** `check_skip_list_covered` (3625),
   `check_template_self_conformance` (3949), `check_templates_bootstrap_sync`
   (3959) — each returns 0 with nothing recorded.
9. **`check_windows_exe_provenance` (4225) — Measuring the Wrong Property.** The
   entire assertion is that a same-named `.cs` file sits *beside* each `.exe`;
   nothing verifies the `.cs` compiles to that `.exe`. PASS line claims
   "verifiable source provenance" from pure filename adjacency.
10. **`check_ps_redirectors` (4167) — Measuring the Wrong Property.** The only
    assertion is a ≤50-line budget, yet the claim is "redirectors point to
    canonical implementation". A 3-line `# TODO`, or one redirecting to the
    *wrong* target, passes. The 2-element list is hardcoded, not SSOT-driven.
11. **`check_no_hardcoded_ssot_literal` (3425) — Unanchored Allowlist.** Every
    exemption is an unanchored substring (`/reference/`, `/artifacts/`,
    `/configurator/` exempt whole subtrees anywhere in a path), and the second
    filter is **line**-scoped: a line mentioning `$MIOS_` or `mios.toml`
    anywhere is exempt even when it also contains a literal `fedora-42`.
12. **`check_bash_phase_ratchet` (3443) — Count-Only Ratchet.** A raisable
    threshold from `[build.ratchet].max_phase_scripts` with a hardcoded `71`
    fallback reachable when `python3` is absent (`2>/dev/null || echo "71"`).
13. **`check_pipeline_numbering` (3361) — Skip-as-Pass ×3.** A deleted tracked
    SSOT TSV or generator **skips rather than violates** (`_subject_present`
    exists for exactly this and is not used); and `_pi_skip` disables the
    comparison whenever the **arbitrary** env var `CTX` is non-empty, which any
    caller can set.

Cleared as sound — and named as the model the others should copy:
`check_signature_policy`, `check_projection_coverage`,
`check_build_tool_dispatch`, `check_phase_registry`, `check_drift_stubs` all
`_violation` explicitly when `mios-gate` is unbuilt and fail closed on any
non-zero exit. `check_cargo_deny` (4038) and `check_resolver_ps_equivalence`
(4024) are presence-only assertions but **candidly say so** in their PASS lines
("the policy is NOT executed, so advisories/licenses/bans stay unenforced") —
hollow in substance, honest in claim, so not filed as defects.

Correction to the task framing: `check_skip_list_covered` is **not** the
meta-gate's skip list — it polices the agent-pipe skip list's SSOT ownership and
has no relation to sibling-check skipping. `check_no_silent_tool_skips` is the
sole Skip-as-Pass meta-gate.

## Subagent 6 — lines 4266–4904 (64 checks read, all dispatched)

Independently confirmed sweep B (`check_unit_security`) and sweep A (no
defined-but-never-dispatched functions in range; **no duplicate `check_*`
definitions anywhere in the file**, so nothing is shadowed by a redefinition).
New findings:

1. **`check_bootstrap_sync` (4871) — Skip-as-Pass, LIVE in this checkout.**
   `tools/sync-bootstrap.py:153-165` returns **0** with "bootstrap repo not
   present at …; skipping" when the sibling tree is absent, unless
   `MIOS_DRIFT_REQUIRE_TOOLS=1`. Its default `--bootstrap` is
   `<dirname ROOT>/mios-bootstrap`, else the literal `C:\mios-bootstrap`; none of
   those exists here and `MIOS_BOOTSTRAP_ROOT` is unset. So **today** the wrapper
   prints the affirmative header "shared files in MiOS-bootstrap match main
   repository SSOT" and echoes the skip line through the success path. **Law 15's
   only automated cross-repo gate cannot fail on any Linux runner or worktree
   lacking the sibling.**
2. **`check_os_update_timer_enabled` (4296) — Measuring the Wrong Property +
   invented verdict.** `grep -q 'uupd\.timer\|bootc-fetch-apply-updates\.timer'`
   against `automation/50-uupd-installer.sh`, which contains
   `systemctl disable bootc-fetch-apply-updates.timer` — so the grep matches
   **even on a `disable` line**, and even if both `ln -sf … "${WANTS}/…"` *enable*
   lines were deleted. Worse, the python-absent branch sets `declared=1` with the
   comment "cannot read the SSOT here; do not invent a verdict" — **the
   assignment is the invented verdict**, and the check then prints "declared in
   the SSOT and wired by a bake phase" without reading the SSOT. The four
   timer paths in the 4307-4309 fast path do not exist in the tree, so that
   branch is dead and the grep leg is the live one.
3. **`check_github_slug_casing` (4397) — Unanchored Allowlist + Swallowed
   Failure.** `grep -v "/${org}"` discards the whole **line** when the canonical
   `/mios-dev` appears anywhere on it, so a line carrying both a mis-cased and a
   correct reference escapes; `grep -v 'usr/share/doc/mios/knowledge'` exempts any
   line whose *content* mentions that path, in any file. With
   `2>/dev/null … || true`, an unreadable file or a partly-executed `xargs` batch
   yields the same empty `bad` as a clean tree. The `scanned -lt 100` guard is
   genuinely good but bounds only a *separate* pipeline's count, not the one that
   finds hits.
4. **`check_names_registry_equivalence` (4555) — Skip-as-Pass over a compile
   failure.** `cargo build … >/dev/null 2>&1 || true` discards the build failure,
   which then merges into the same "binary absent → return 0" path as "cargo is
   not installed". A Rust twin that no longer **compiles** is precisely the drift
   this gate documents itself as existing for (its own comment cites task_993: 3486
   emitted lines vs the Python's 1229). Everything past that guard is sound.
5. **`check_unit_dependency_closure` (4470) — Skip-as-Pass, unforceable.** Same
   bypass as sweep B: it sidesteps `_need_python` with its own
   `command -v` guard, so `MIOS_DRIFT_REQUIRE_TOOLS=1` cannot convert the skip
   into a violation, and the dead `py_bin` means a `python`-only host clears the
   guard then runs a literal `python3`. Its failure leg does reach
   `_violations_from`, so unlike sweep B it is not fully inert.
6. **`check_unpinned_runtime_fetches` (4266)** — corroborates SA5 §3 and adds:
   its immediate sibling `check_wsl_distro_resolution` (4336-4339, 4354-4357)
   `_violation`s on exactly the missing-dir and zero-corpus states, **so the
   correct pattern sits 50 lines away.**
7. **Fifteen `_need_python || return 0` siblings** in range: 4286
   `check_secret_handling`, 4399, 4638, 4646, 4654, 4662, 4670, 4678, 4686,
   4694, 4702, 4710, 4718, 4726, and 4898 `check_header_integrity`. Notably **30
   sibling checks in the same block omit `_need_python` and therefore correctly
   violate when python3 is missing — so the skip is a choice, not a necessity.**
8. **Minor (count fidelity, not cannot-fail):** `check_credential_literals`
   (4531) and `check_protected_refs` (4614) do
   `"$bin" … || _violation "<generic>"` without capturing output, so N tool
   findings collapse to 1 counted violation. Also `_run_py_check`'s
   `pfx="${3:-$1: }"` substitutes on **null as well as unset**, so the eleven call
   sites passing `""` intending no prefix silently get `"<name>: "` anyway.

**`_run_py_check` (4766) cleared on all three failure modes:** python3 absent →
rc 127 and the `2>&1` captures "command not found" → `_violations_from` →
violation, not a skip. A missing `tools/*.py` → exit 2 with "can't open file" on
stderr → captured → violation. The unquoted `$script` is deliberate and safe:
word-splitting is load-bearing for the three `--check` variants and no path
contains whitespace or glob metacharacters. Residual: the success line
`echo "…  $out"` reprints tool output as a pass, so a backing tool reporting
findings on stdout while exiting 0 would read green — sampled nine backing tools
and found **exactly one** instance repo-wide (sweep B), which is not routed
through this helper.

**The four `mios-gate` checks cleared:** all probe `${MIOS_GATE_BIN:-}` → release
→ debug → `/usr/libexec/mios/mios-gate` and `_violation` when none is `-x`. The
success branch cannot lie: `src/mios-rs/mios-gate/src/main.rs:38-46` returns
`EXIT_VIOLATIONS` whenever `ok` is false and `EXIT_CANNOT_RUN` (2) when
`could_not_run` is set, under the invariant "`findings` is empty exactly when
`ok` is true" (main.rs:28), and non-ok text goes to **stderr**, which the `2>&1`
captures. **One shared caveat worth escalating: `MIOS_GATE_BIN` is honored
first, so `MIOS_GATE_BIN=/bin/true` turns all four into unconditional passes** —
an env escape hatch on par with `MIOS_DRIFT_CHECK_SOFT`, undocumented at the
call sites.

## Subagent 2 — lines 1009–1790 (21 checks read, all reachable)

Returned late; this range holds four law enforcers, and **both law enforcers
that sweep D flagged as having zero negative-test coverage turn out to be
defective** — Law 13 (§8 below) and Law 16 (§14).

1. **`check_resolver_twin_equivalence` (1323) — Check-Without-Diff
   (Self-Comparison).** The backing tool compares `mios_toml.emit_exports()`
   against the env from `source usr/lib/mios/userenv.sh`. But `userenv.sh`
   resolves in three tiers — native `mios-resolver`, then `miosd`, then
   **`mios_toml.py` itself** (`userenv.sh:60-65`). Neither binary exists in this
   worktree, so **the bash leg *is* `mios_toml.py`**: a mutation to it changes
   both legs identically and they go on agreeing. This is verbatim the task_3360
   defect that the sibling `check_resolver_twin_parity` documents at length and
   guards against; this check has **no guard at all**, not even under
   `MIOS_DRIFT_REQUIRE_TOOLS=1`. The tool's advertised 3-way assertion is also
   dead: `crate_vars` is built at `check-resolver-twin.py:114-126` and never
   referenced by either mismatch loop.
2. **`check_vendor_urls` (1190, defect at 1215) — Swallowed Failure.**
   `ep_out="$(… python3 tools/drift-checks.py ai-endpoint-local)"` — exit status
   discarded entirely, stderr not captured, no `_need_python` guard, and **empty
   stdout is the PASS signal**. The backing function opens `mios.toml` with no
   try/except, so an unparseable SSOT raises to stderr leaving `ep_out` empty →
   "vendor [ai].endpoint is local". The script path is also **relative** and this
   gate never `cd`s, so invoked from any other cwd it dies with "can't open file"
   → empty stdout → PASS.
3. **`check_fluff_tokens` (1517) — Measuring the Wrong Property, live misses.**
   Every rule is nested inside `[[ "$line" =~ (echo|log|warn|die)[[:space:]] ]]`,
   but the pipeline's actual logger is `mios_ok`/`mios_err`/`mios_step`, which
   **never match**. Four live violations of the check's own "bare Done" rule are
   invisible to it: `automation/25-gpu-cdi-toolkits.sh:101`, `56-fonts.sh:95`,
   `72-hermes-agent.sh:439`, `90-generate-sbom.sh:56` (all `mios_ok "Done"`).
   `=~ successfully` is also case-sensitive, so
   `build-windows-binaries.sh:53` ("Successfully built") passes while carrying
   the exact banned token.
4. **`check_deploy_plane` (1632) — Skip-as-Pass + already vacuous today.** Both
   subjects skip with a WARNING rather than `_violation`, and the function still
   prints "deploy-plane checks passed". The version leg is **already dead**:
   `base_image = "quay.io/podman/machine-os:6.0"` makes
   `base_image_version=0`, and `grep -oE 'Fedora-Server-[0-9]+'` over the
   kickstart returns **zero lines**, so `grep -qv` over empty input exits 1 and
   no mismatch can ever be recorded.
5. **`check_kargs_projection` (1352) — Empty-Set Pass, missing fourth arm.**
   Three arms cover rendered-only / committed-only / both, but there is **no arm
   for "neither exists"**, and `miosd render-kargs` only touches the vfio file
   when it already exists (`miosd/src/main.rs:484`). Delete
   `usr/lib/bootc/kargs.d/01-mios-vfio.toml` and nothing is seeded, nothing
   rendered, `diffs` stays empty, and the check certifies the projection while
   `rd.driver.pre=vfio-pci` has vanished from the kernel command line.
6. **`check_greenboot_enablement` (1421) — Measuring the Wrong Property.** The
   first leg is a bare substring test for service names, satisfied by a comment
   or a commented-out `ln -sf`, so the *enablement* it claims to verify is never
   structurally checked. The second leg skips wholesale if `etc/greenboot` is
   absent and leaves `mode` empty when git is unavailable.
7. **`check_version_ssot` (1706) — Empty-Set Pass via the fallback.**
   `git ls-files` runs without `-C "$ROOT"` in a gate that never `cd`s; when it
   fails the `find` fallback emits **absolute** paths, so
   `[[ -f "$ROOT/$_cf" ]]` becomes `[[ -f "$ROOT//home/user/…" ]]` → false →
   `continue` for every file. Zero Containerfiles compared, PASS printed.
8. **`check_resolver_twin_parity` (1223) — Skip-as-Pass, LIVE here. [Law 13]**
   No `mios-resolver` exists in this worktree and `just drift-gate` is
   advertised as needing no built image, so the default local path returns 0 with
   the Law-13 fixture never evaluated. **8 sibling checks in this same file treat
   an unbuilt native tool as a violation** (1879, 1905, 1926, 1949, 1974, 2035,
   3196, 3469). The rest of the function is unusually well built.
9. **`check_impossible_eol_regressions` (1600) — Skip-as-Pass +
   Self-Certifying.** No `_subject_present "$toml"`: if `mios.toml` is missing,
   every `grep` exits 2, `&>/dev/null` swallows "No such file", all three package
   assertions read as "not found", and PASS prints. Its rejection test is also
   satisfied by the document's own prose containing `reject`.
10. **`check_quadlet_privilege` (1046) — Empty-Set Pass (latent). [Law 6]** If
    both scan dirs vanish or hold no `*.container`, `bad` stays empty and the
    check certifies Law 6 over zero units. Non-vacuous today (25 files); the
    allowlist matching itself is exact (`grep -qxF`) and correct.
11. **`check_template_conformance` (1335) — Skip-as-Pass + Count-Only Ratchet in
    the backing tool. [Law 16]** The bash wrapper is sound; the tool is not:
    `usr/libexec/mios/check-template-conformance:66-68` does
    `if not tagger: … return 0`, so **deleting `usr/libexec/mios/mios-ai-tag`
    makes the wrapper print "all new files conform to templates"**. Its verdict
    is also a raisable threshold (`max_unconforming`), and below the ceiling
    non-conforming files are printed but not fatal.
12. **`check_nut_projection` (1474) / `check_templates_compilation` (1582) /
    `check_coordination_hygiene` (1559) — Skip-as-Pass / Empty-Set (low).** A
    missing preset file silently drops the whole enablement assertion;
    `compile-templates.py` prints `PASS: All 0 templates compiled` on an empty
    dir; `_subject_present || continue` passes over zero lines if both task files
    stop being tracked.
13. **`check_no_mkdir_in_var` (1009) — corroborates SA1 §13** with the added
    detail that `/var` creation laundered through `automation/lib/*.sh` is
    outside the glob entirely.

Judged sound, and named the best-built check in the range:
**`check_lint_is_final` (1158)** — counts its corpus and violates on `n -eq 0`,
exact-string comparison, honestly scoped PASS line. Also sound:
`check_var_closure` (1093, the 414-name ledger plus `#!ceiling 414` anchors the
scan in both directions), `check_firstboot_degrade_open`,
`check_cargo_manifest_generated`, `check_chrony_projection` (renders into a fresh
`mktemp`, and its discarded rc fails *closed*).

**Inverse risk flagged by this subagent:** with `miosd` unbuilt (as here),
`check_chrony_projection` and `check_nut_projection` emit **false** violations
blaming "content drift" when the real cause is an absent renderer — the mirror
image of this audit's concern, and misleading to whoever reads the failure.

## Subagent 4 — lines 2568–3360 (37 checks read, all reachable)

Returned late. This range holds the Law 1 and Law 8 enforcers, **both
defective**, plus the coverage meta-gates.

1. **`check_oci_archive_path` (2636) — Self-Certifying Predicate, ACTIVE TODAY.**
   The producer `usr/libexec/mios/mios-stage-oci-archive` contains **exactly one**
   line matching `/mnt/mios-repo/…\.tar` — and it is **line 2, the AI-hint
   comment.** Its executable code never contains the literal (it builds
   `DEST_DIR="${2:-/mnt/mios-repo}"` then `DEST_TAR="${DEST_DIR}/mios-latest.tar"`).
   So "producer and consumer paths match" is currently satisfied on the producer
   side **only by a comment**; rename the real tar and the gate still passes.
   `check_offline_install_invariant` in the same range strips comments
   (`sed 's/#.*//'`) for exactly this reason; this check omits that step.
2. **`check_usr_over_etc` (3206) — Skip-as-Pass, reachable in the bake. [Law 1]**
   `_tracked` (`tools/drift-checks.py:47`) returns status **0** when
   `$ROOT/.git` is absent, so the check returns 0 and bash prints "Law 1
   USR-OVER-ETC verified clean" having enumerated zero files. Not a tool-absence
   path, so `MIOS_DRIFT_REQUIRE_TOOLS=1` does not close it. **Reachable in the
   bake:** `Containerfile:71-75` copies `.git` with
   `cp -a … 2>/dev/null && echo … || echo "[ctx] WARN: .git copy failed"`, so a
   failed copy produces precisely this silent-pass state. Secondary Unanchored
   Allowlist: `".d" in os.path.basename(f)` exempts any future `etc/foo.desktop`,
   `etc/x.dat`, `etc/y.deny`.
3. **`check_build_artifacts_output_dir` (2880) — Measuring the Wrong Property.**
   `$output_dir` is read from SSOT and then used **only inside the failure
   message**. The single assertion is a blacklist of four hardcoded lowercase
   `output` spellings; nothing asserts the Justfile uses the SSOT value
   (`output_dir = "build"`). Retarget a recipe to `dist/` and the gate prints
   "Justfile artifact recipes enforce SSOT output directory". The `|| echo
   "Build"` fallback is dead (`cut` always exits 0) and contradicts the SSOT,
   showing the extracted value is decorative.
4. **`check_chrony_ptp_dropin` (3000) — Over-claiming + Swallowed Failure.**
   Idempotency is **never measured**: the generator is run twice, both times
   `|| true`, and the second run's effect on `10-ptp.conf` is never compared to
   the first — no hash, no diff. A generator that appends duplicate `refclock`
   lines on re-run records nothing, while the PASS line claims "is idempotent".
5. **`check_negative_coverage` (3115) — Self-Certifying Predicate.** Coverage is
   satisfied by *any* textual occurrence of a check's name in
   `drift-gate-negatives.sh` — **including inside a `die` message string** (e.g.
   `die "check_ratchet_direction passed despite raised ratchet ceiling"`) or a
   comment. This independently corroborates sweep D's warning that my own
   39-check figure is a lower bound, and explains why: the repo's own coverage
   metric has the same substring weakness mine did. Its exempt list holds 54
   names with **no ceiling key**, unlike `[laws.projection_registry].max_exempt = 0`.
6. **`check_renderer_gate_coverage` (3049) — Empty-Set Pass, guard on the wrong
   collection.** `found_total` counts every renderer *including the two
   allowlisted ones*, while the assertion loop runs over `render_scripts` with
   the allowlist removed. With only the two allowlisted renderers present,
   `found_total=2` passes the non-empty guard, the loop body never executes, and
   PASS prints having mapped nothing. The mapping test is also an unanchored
   match against 98-drift-checks.sh **itself**, satisfiable by a comment or a
   `_violation` message string.
7. **`check_projection_registry` (3216) — Empty-Set Pass. [Law 8]** `surfaces`
   defaults to `[]` with no non-empty assertion, so an emptied or renamed
   registry table yields "Law 8 SSOT-PROJECTION registry verified clean".
   Per-entry, the only assertion is that a function of that name is **defined**
   — not that it is dispatched, and not that its body does anything; an entry
   with neither `generator` nor `check` asserts nothing at all. The reverse
   direction is enforced by a separate Rust gate (`mios-gate/src/projreg.rs`)
   that this Python check ignores entirely.
8. **`check_offline_install_invariant` (2715) / `check_repo_partition_label_ssot`
   (2831) / `check_kickstart_shell_syntax` (2673) — Skip-as-Pass +
   comment-satisfiable + Empty-Set.** The first silently skips a **tracked**
   kickstart with no `else`. The second's greps are bare unanchored substrings,
   and `mios-oci-install.ks` **line 1 is an AI-hint comment containing
   `MiOS-Repo`**, so the real `REPO_LABEL=` could drift to any other label and
   the comment alone keeps the gate green. The third: `config/artifacts/iso.toml`
   embeds a kickstart with **zero `%post` occurrences**, so `post_sh` is empty,
   that subject is silently skipped, and the PASS still claims "verified clean
   with bash -n" — only one of two subjects is actually linted.
9. **`check_ssot_lint_equivalence` (2568) — corroborates SA3 §12**, adding that
   the unbuilt path is the **live state here** and `cargo build … || true`
   swallows its own failure, so on a local `just drift-gate` this check is a
   silent no-op.
10. **`check_vendored_assets_non_stub` (3284) / `check_vllm_name_canonical`
    (3154) — Empty-Set / Swallowed Failure (latent).** The first excludes exactly
    the placeholder pair (`.keep`, `VERSIONS.txt`) that remains when the payload
    is not materialised, and its `-lt 100` byte test is a proxy a 120-byte
    pointer passes. The second cannot distinguish grep exit 2 (missing search
    dir) from exit 1 (no match), with `2>/dev/null` hiding the diagnostic.
11. **`check_resolved_env_lossless` (3307) / `check_no_duplicate_value_key`
    (3343) — documented override-as-pass.** `MIOS_ENV_BASELINE_BUMP=1` converts a
    real computed diff into a PASS with no requirement that the baseline was
    regenerated; the twin's `[[ -f "$baseline" || "$bump" == "1" ]]` lets
    `MIOS_VALUE_DUP_BASELINE_BUMP=1` excuse a **missing** ledger. Both are
    explicit ratchets, so the severity is in the escape hatch, not a hidden bug.

Judged sound (20 checks), notably `check_composefs_projection` (2958 — renders to
`COMPOSEFS_CONF` in a tmpdir which the renderer honors, then diffs against the
tracked file: a real diff, not a self-comparison), `check_law_enforcers` (3182 —
correctly violates when `mios-gate` is unbuilt), and
`check_installer_family_roles` (2762 — a non-empty floor plus an explicit
`_violation` on an empty corpus, "one of the soundest in the range").

## Coverage and what was NOT audited

**All six subagents returned; coverage is complete.** Every one of the 220 check
functions in `automation/98-drift-checks.sh` received a per-check read, across
the six ranges 215–1010, 1009–1790, 1786–2570, 2568–3360, 3361–3686 + 3948–4270,
and 4266–4904 (the small overlaps at range boundaries were read by both
neighbours; no gaps). All four manager sweeps (A–D) additionally ran mechanically
over all 220. Totals: **224 check-function reads** (the overlaps) yielding
**~75 confirmed cannot-fail defects**.

Two subagents returned *after* an initial version of this report was filed, which
had recorded their ranges as unaudited and predicted "15–18 further defects"
there. They returned 29 — so that estimate was low, and the prediction has been
replaced by their actual findings above. Sweep D's two flagged law enforcers both
proved defective once read, which is the strongest single validation in this
audit: an unprotected gate really was a broken gate, in both cases.

**Law enforcers found defective** (the registry at `mios.toml [laws]` names one
check per law):

| Law | Enforcer | Defect |
|---|---|---|
| 1 USR-OVER-ETC | `check_usr_over_etc` (3206) | Skip-as-Pass on absent `.git`, reachable in the bake |
| 2 NO-MKDIR-IN-VAR | `check_no_mkdir_in_var` (1009) | evadable pattern + Empty-Set |
| 6 UNPRIVILEGED-QUADLETS | `check_quadlet_privilege` (1046) | Empty-Set (latent) |
| 8 SSOT-PROJECTION | `check_projection_registry` (3216) | Empty-Set; definition-only assertion |
| 13 NATIVE-DROPINS | `check_resolver_twin_parity` (1223) | Skip-as-Pass, live here |
| 14 TARGET-LANGUAGES | `check_target_languages` (1984) | never scans `*.ps1` |
| 16 ONE-TEMPLATE-PER-TYPE | `check_template_conformance` (1335) | backing tool returns 0 when `mios-ai-tag` is absent |

Law 4's enforcer `check_lint_is_final` (1158) is, by contrast, the best-built
check in the file and the model the rest should copy: it counts its corpus,
violates on zero, compares exact strings, and scopes its PASS line honestly.

Remaining unverified: the backing tools listed by each subagent as read-wrapper /
unread-internals (~40 `tools/*.py` subcommands and Rust gate modules), and
whether every unreadable-input path inside the four `mios-gate` modules routes to
`cannot_run`.

Nothing in this report was produced by executing the drift checks. Both lane
controls were run and held (positive exit 0: 9 sections / 639 lines; negative
exit 1 matching the lane's planted sentinel, whose literal text is deliberately
NOT reproduced in this file — see the note below), and `git status --porcelain`
after the negative control showed only this report file — no fixture leak.

### One defect of this class committed by this lane, and fixed

Worth recording, because SKILL §6 warns that a harness reporting "0 problems"
because it silently did nothing is the same defect class being hunted, committed
by the auditor.

An earlier revision of this report quoted the lane's planted sentinel **literally**
as control evidence. The lane's `negative_control_cmd` asserts the plant landed
with `grep -q '<sentinel>' "$f"` against this very file — so once the token
existed in the report prose, that guard would have been satisfied by my own
documentation **even if the `printf` that plants it had failed**. The control's
`negative_expect` is matched against command output rather than file content, so
the gate itself still functioned; but the plant-landed assertion had become a
**Self-Certifying Predicate**, and SKILL §6 is explicit that a sentinel "must not
already exist anywhere in the tree".

Fixed by describing the sentinel instead of reproducing it. Detected by grepping
for the token after the control restored the file and finding **1** remaining
occurrence where **0** was expected — i.e. by asserting the harness had actually
done work, which is the same discipline §6 demands of every negative control.
The two other checks in this lane's own tooling that produced a convincing wrong
answer are recorded under phantoms dismissed (the lowercase `justfile` grep, and
the crude never-reaches-`_violation` sweep's 21 false positives).

### Harness components verified sound (phantoms dismissed)

- `_violations_from` (line 98) — explicitly violates on an empty blob, so the
  42 call sites it was folded from are not Empty-Set Passes.
- `_neg_gate` (`tests/drift-gate-negatives.sh:3350`) — its trailing
  `_NEG_GATE_OUT="$(...)"` assignment propagates the command substitution's exit
  status, so `if _neg_gate check_x` is a genuine test; it deliberately forwards
  `MIOS_DRIFT_REQUIRE_TOOLS` so the failing path is reachable.
- `tools/drift-checks.py` dispatcher (line 4732) — an unknown or renamed
  subcommand raises `SystemExit(2)`, so a stale wrapper fails loudly rather than
  passing. Only 1 of its 69 handlers can return `None`/0 unconditionally
  (`check_unit_security`, sweep B); the other 68 exit non-zero on violations.
- `_emit_projection_evidence` (line 121) is evidence-only and always returns 0 —
  correct, provided no check treats it as the verdict.
