<!-- AI-hint: AGY Work-Audit follow-ups (11 workstreams, 70 findings, verdicts {'needs-refinement': 11}). 1. Fix task_1121: replace globals.sh:101-113 _has_registry_creds if/else with non-clobbering `: "${MIOS_IMAGE_NAME:=ghcr.io/mios-dev/mios}"`; verify a no-creds common.sh load keeps MIOS_IMAGE_NAME=ghcr and a consistent NAME/REF pair.
     AI-related: usr/share/mios/mios.toml, docs/adr/, AGY-TASKS.md -->
# AGY Work-Audit follow-ups (11 workstreams, 70 findings, verdicts {'needs-refinement': 11})

1. Fix task_1121: replace globals.sh:101-113 _has_registry_creds if/else with non-clobbering `: "${MIOS_IMAGE_NAME:=ghcr.io/mios-dev/mios}"`; verify a no-creds common.sh load keeps MIOS_IMAGE_NAME=ghcr and a consistent NAME/REF pair.

2. Harden the CI/forgejo publish steps against empty MIOS_IMAGE_NAME: set MIOS_VENDOR_TOML to the checked-out repo mios.toml before sourcing userenv, or add `: "${MIOS_IMAGE_NAME:=${REGISTRY}/${IMAGE_NAME}}"` fallback; test on a runner without /usr/share/mios installed.

3. Align the globals.sh/globals.ps1 twins on identical override semantics for MIOS_IMAGE_NAME and add MIOS_IMAGE_NAME/MIOS_IMAGE_REF to a twin-parity assertion so the divergence is drift-gated.

4. Add the negative test task_1121 specifies (no-creds common.sh must not downgrade MIOS_IMAGE_NAME to localhost) and a CI-context test asserting sourced userenv yields a non-empty image name.

5. Reconcile the mios-ci.yml PUBLISH:'true' value with its own comment and the release-topology memory (intended capacity-gate) -- decide and document the actual GitHub publish state, since it governs whether the empty-image-name regression is live.

6. Remove dead REGISTRY/IMAGE_NAME env from mios-ci.yml (or repurpose as the fallback) to eliminate the dual source of truth.

7. Re-open task_230 in TASKS.md until the selection logic is wired to a real pull/switch consumer or explicitly de-scoped to CI-publish only.

8. Make generate-names-registry.py write referenced_names.txt with newline='\n' and have drift check 30 compare against a temp regeneration instead of overwriting the tracked file, eliminating the CRLF/dirty-tree side effect.

9. Single-source the duplicated walk/EXCLUDED_SECTIONS/WALK_MOSTLY_DEAD/WALK_EMIT_KEEP/env-table/pgvector logic shared between check-resolver-twin.py and userenv.sh (extract or move into mios_toml.py) to remove the false-RED maintenance hazard.

10. Replace the brittle regex function-scraping in check-resolver-twin.py with either an importable shared module or explicit BEGIN/END sentinel comments so userenv.sh refactors cannot silently break extraction.

11. Add a synthetic multi-layer fixture to the equivalence gate (empty-string override + vendor<host<user precedence across representative non-AI sections) so merge-rule regressions are caught independent of committed data.

12. Reconcile the generator's allow-list (TARGET_SECTIONS, 20 sections, no aliases) with the resolver's deny-list + full alias map so names.generated.txt is a complete registry, and add a gate asserting TARGET_SECTIONS covers every non-EXCLUDED mios.toml section.

13. Verify these gates on Linux/CI (bootc bake), since the checker special-cases Windows Git-bash and the default bash on the dev box is a broken WSL — confirm both check 30 and check 45 run green in the actual build environment.

14. Have 38-ssot-lint.sh derive allowed dynamic MIOS_PORT_* names from the registry rather than the hand-maintained _ssot_lint_ports_dummy array duplicated in both userenv.sh copies.

15. Broaden lint-shell.sh to recursively cover automation/lib, automation/firstboot, automation/support, automation/build, tools/lib, and all shebang-shell files under usr/libexec (not just usr/libexec/mios/mios-*), then run it to burn down the newly-surfaced findings — the core build libraries (common.sh, packages.sh, globals.sh) are currently unlinted.

16. Unify the missing-shellcheck degrade-open semantics: make `just lint-shell`, `just drift-gate` (Justfile:66,77) and the CI step tolerate exit 2, or decide it is always fatal — the current mix means `just drift-gate` aborts on any shellcheck-less host.

17. Fix or remove the dead `20-mios-paths-env.conf` cp in automation/support/heal-all-services.sh and run the recovery script end-to-end; strip the trailing whitespace introduced by the rename.

18. Add negative-test coverage for the two behaviors that currently have none: the Containerfile.minimal ARG branch of check_version_ssot, and the shellcheck-absent (exit 2) degrade-open path of check_shellcheck (the current test only exercises the exit-1 fail path).

19. Scope the version-literal allowlist to (file,value) pairs and skip comment-only lines, so allowlisting 0.2.4 for one historical comment does not blind the gate to a real re-hardcode of the previous MiOS version.

20. Extend check_cli_eval_safety (or add a sibling) to the resolver twins usr/lib/mios/userenv.sh and tools/lib/userenv.sh (`eval "$exports"`) and tools/system-profiler.sh so the 'eval injection surface' claim matches the enforced surface.

21. Root-anchor the Containerfile scan in check_version_ssot (`git -C "$ROOT" ls-files`) to match the python half and survive subdir invocation.

22. Regenerate automation/manifest.json — it still embeds `'MiOS' v0.2.4` script headers from before the 391a5b77 de-hardcode, so the manifest is stale relative to the live .sh headers (it escapes the version gate only because .json is excluded from the scan).

23. Restore substrate condition-gating (virt-gate/bare-metal-only/mios-virt-gate/mios-wsl2): re-add the fanout GATES loop or migrate them to an SSOT table + committed static .d files, and add a drift-check that fails on orphaned committed dropins with no generator source.

24. Fix 48-mios-dropin-fanout.sh to write into the absolute image /usr/lib/systemd/system (or run pre-overlay), and add a post-build assertion that fanned drop-ins actually exist in the final image.

25. Harden the from-source mios-sys/mios-cuda build: pin all git clones to tags/digests and downloads to fixed versions, add retry/backoff, or revert to pulling pre-built bound-images; benchmark build time and image size regression.

26. Correct or remove After=mios-webtools-firstboot on sys/cuda-backed quadlets; if a shared-base firstboot gate is desired, create a dedicated unit.

27. Run each repointed service end-to-end (verify skill): searxng module path, adguard /opt config resolution, open-webui `serve`, llm-light /app/config.yaml presence, llm-heavy vllm entrypoint — none were exercised, only static-checked.

28. Wire or remove the dangling 'controller' capability in [blade.archetypes]/[blade.requires].

29. Update docs left describing the old fanout (ADR-0001, reference/build-scripts.md, reference/tree.md, automation/manifest.json full_content) to match the new blade-cap-only behavior.

30. Add a memoization/caching layer for is_db_authoritative() and load_db_config() (once-per-process or short TTL) so the shadow-compare and import-time constant resolution don't re-walk all TOML layers + re-connect to PG on every call.

31. Unify the db_authoritative merge semantics across mios_toml.load_merged/get/colors (recommend DB-overlays-files deep_merge) and add a drift-check/unit test that asserts the same key resolves identically via get() and via section(load_merged()) under a partial-DB authoritative fixture.

32. Add a test that exercises config.py/agent-pipe import with a mock psycopg whose connect() blocks/refuses, asserting import completes within a bounded time (guards the systemd startup-stall regression).

33. Fix the stray `-> Any` return annotation in mios_db_config.get() (line 256) -- Any is never imported; harmless only because `from __future__ import annotations` defers evaluation, but it breaks get_type_hints introspection.

34. Reconcile the MIOS_TOML vs MIOS_VENDOR_TOML env-key schism now that agentreg reads via the resolver, so all agent-pipe config consumers key off the same override.

35. Add an explicit standalone unit assertion that _load_agent_registry deep-merges nested [agents.<n>.auth]/[.engines] sub-tables across layers (the shallow->deep behavior change), so the new semantics are pinned.

36. Verify [accounts].db_backed (SSOT=true) is actually auto-derived to MIOS_ACCOUNTS_DB_BACKED by tools/lib/userenv.sh at build time -- no explicit bridge was found; if it is not exported, 17-accounts-db.sh always disables the service (default false) and the whole workstream ships inert.

37. Fix the account/aliases UNION duplication (single-source the principal) and add a regression test that seeds the real account+alias pair AND the vendor 'user' + identity operator, asserting: one sync per OS user, wheel preserved, no uid-1000 collision, service accounts get /sbin/nologin.

38. Redesign the disable-sweep to be degrade-open: lock only on an explicit DB signal (enabled=false / os_targets excludes linux), never on mere absence, never the live operator, and add a dry-run mode.

39. Populate account.shell/home_dir in the seeder (or read them from the canonical_users join) so service/AI accounts are not created as /bin/bash login users.

40. Decide LISTEN/NOTIFY vs poll: either make the daemon event-driven on the 'account_sync' channel or drop the notify triggers; update the service Description accordingly.

41. Land or explicitly defer the Windows DB->SAM twin; wire NTLM write-back or document it as Windows-path-only. Add a twin-parity note so the schema claims match shipped code.

42. Remove the hardcoded 'user'/'user' admin account + NTLM/SID literals from mios-ai-firstboot, or gate behind a documented dev-only SSOT flag with forced password reset; align with the [accounts] NO-HARDCODE contract.

43. Add a drift-check that the account-sync .service is installed and (when db_backed) enabled, and that mios-account-sync passes py_compile, so this plane cannot silently rot.

44. Add a to_toml round-trip test that loads the real usr/share/mios/mios.toml, serializes, reparses, and asserts equality -- this is the single highest-value guard for the GET/POST feature and would have surfaced the datetime-drop gap; keep it in the CI test loop.

45. Make test_mios_config_write.py actually execute in both CI gates: import only the FastAPI app/route module (not full server.py) so the websockets/uvicorn absence in the fast gate no longer triggers the sys.exit(0) no-op that reports a false [OK].

46. Decide and document the layer-write semantics: delta-vs-vendor (preferred, using mios_toml.load_vendor()) or explicit authoritative snapshot with an upgrade-reabsorption path; add a drift-check that the user-layer file is not a full copy of vendor if delta is chosen.

47. Harden validate_config: it guards only [identity]/[ports] drops + mios_user + port ranges; consider validating that the posted config still parses under load_merged layering and that no [ports] value collides, since a bad save reseeds the DB (run_db_reseed_bg, check=True) and can brick services.

48. The advertised broader 'TD-5 server.py god-module extraction (feed task_1094..task_1099)' is NOT present as committed code beyond to_toml/validate_config/write_user_config (into config.py) and _arg_with_synonyms/_validate_enum_args (into mios_argval); reconcile the roadmap claim with what actually shipped or file the remaining extraction waves explicitly.

49. Fix the quadlet (and systemd-unit) [templates.*].match patterns to point at the real estate dirs (usr/share/containers/systemd, usr/lib/systemd/system) and re-run check-template-conformance to confirm green; grandfather any genuine non-conformers uncovered.

50. Rework check-template-conformance to enforce ALL matching templates (or most-specific-first) so drift-check `check_`, roadmap-ws, and quadlet markers are actually enforced; add a unit test proving every declared marker set is reachable.

51. Reconcile mios-new destinations with the SSOT match patterns (bash-verb mios- prefix + .sh, quadlet -> containers/systemd, systemd-unit -> matched dir); expand mios-new render/get_dest_path from 8 to all 19 registered types and update the Usage string.

52. Implement real SSOT-driven field filling in mios-new (next ADR number, next automation/NN ordinal, canonical ports from mios.toml) or remove the SSOT claim from its header + ROADMAP.

53. Wire tools/compile-templates.py into the drift-gate/CI so template round-trip + [templates.*] registration is enforced, not orphaned.

54. Regenerate conformance-grandfathered.list to contain only files that match a template pattern AND currently fail it; remove the two conforming first-party ADRs and the ~184 inert extensionless/.container entries so the ratchet count reflects real template debt.

55. Add a documented ceiling knob (max_unconforming) under [ai_tag] or a [templates] policy block, and scope the broad ts/ps1/toml/yaml header patterns to authored dirs to remove the CI-workflow/vendored-file footgun.

56. Reconcile the TD-4 contradiction: either move 53/55/56 out of build.sh NON_FATAL_SCRIPTS (true fail-closed) or amend ROADMAP.md:289 to state that degrade-open (Law 12) intentionally keeps them warn-gated and the delivered fix is retry+pin only.

57. Fix 53-bake-lookingglass: restore auto-latest B* resolution when the SSOT ref is unset or the pinned branch 404s, OR delete the 'tracks :latest' comment to match the hard B7 pin; move record_version after the success guard.

58. Resolve the orphan MIOS_BUILD_BAKE_REFS_HYPRLAND: wire it into a git-based hyprland baker with a real tag/sha, or delete the key from mios.toml and both userenv.sh twins (preserving check-27 parity).

59. Replace moving bake refs (hyprland='main', lookingglass='B7') with immutable tags/shas to actually deliver TD-4 reproducibility.

60. De-scope the WS-CAT/WS-DOTFILES stub sections from TD-4: populate or remove the empty [editor]/[git] tables and ensure roadmap ssot_keys reference consumed sections, not empty gate-satisfying stubs.

61. Add a `bash -n` (and error-level shellcheck) pre-merge gate on build.sh to catch runtime-fatal shapes like the `local`-outside-function regression that shipped in 5964a890.

62. Rewrite tests/test-a2o-fallback.sh to exercise the real mios-a2o codegen (generate/run $RUND/$name.sh with stubbed AGY_BIN/CLAUDE_BIN) instead of a hand-copied replica; add a fallback-FAILURE case and a $PROMPT-with-quotes/dollar-signs escaping case.

63. Fix the reason-clobber in mios-a2o _agy_post so a failed claude fallback reports 'fallback engine failed rc=N' instead of being overwritten by the generic 'agy: quota exhausted' reason.

64. Update $STATUS/$name.engine (or add a fallback marker) on successful reactive fallback so mios-a2o status shows the engine that actually ran.

65. Decide the fate of tools/native/mios-version-check: either wire it into drift-check #42 as the canonical implementation (and delete the shell duplicate) or clearly mark it a WS-LANG proof-of-concept; harden its TOML/ARG parsing (comment + quote handling) to match check #42.

66. Verify the baked Hyprland version and migrate decoration shadow keys to the `shadow {}` subcategory if >=0.45; project blur/shadow colors from mios.toml [colors] per the liquid-glass SSOT plan.

67. Launch and namespace-verify the notification daemon before keeping `layerrule = blur, notifications`, or remove the dead rule.

68. Add a CI/drift step that actually compiles tools/native (cargo build) so the Rust proof cannot silently rot; today nothing builds it.

69. Verify task_1122/task_1123 completion the real way: after AGY runs them, confirm automation/*.sh log-string edits actually landed and the 4 concepts/*.md docs exist and pass check-46 (template conformance) with the drift-gate still green.

70. Audit the rest of AGY-TASKS.md for other feed entries whose commit subjects imply delivered work but only queue tasks, so git-log legibility is consistent across the backlog.

71. Decide a policy: task files consumed by a second agent must reference only repo-tracked or explicitly-shared paths; add a lint or review step catching Temp\claude\scratchpad references in AGY-TASKS.md.

72. Confirm AGY-TASKS.md is excluded from the built image (root-level, not COPY'd in Containerfile) so backlog churn never bloats or leaks into the bootc image.

