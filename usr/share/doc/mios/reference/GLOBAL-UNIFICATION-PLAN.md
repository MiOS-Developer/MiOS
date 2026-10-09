<!-- AI-hint: The staged, lossless-diff-gated plan to collapse the duplicated/proliferated MIOS_* SSOT keys (measured 2523 keys, 79 version/image pairs, dead+alias dupes, one value copy-pasted across ~35 places) into one-value-declared-once-derived-everywhere. 6 phases; the invariant is a byte-identical resolved-env diff (mios-env-snapshot). task_1530..task_1582 execute it. -->
<!-- AI-related: usr/lib/mios/mios_toml.py, tools/lib/userenv.sh, usr/share/mios/mios.toml, automation/98-drift-checks.sh, tasks.jsonl -->

# MiOS Global Unification Plan (GUP)

> One value, declared once, derived everywhere. Killing the duplicated/proliferated
> SSOT keys so "move one thing" never breaks ten. Every step is provably **lossless**.
> Author: Claude, 2026-07-31, after the day-0 publish exposed the k3s-version-in-35-places bug.

## 1. The problem (measured, not asserted)

Resolved `env | grep ^MIOS_ | sort` on the current tree:

| Symptom | Evidence |
|---|---|
| Namespace bloat | **2,523** `MIOS_*` keys resolved |
| Version duplicated per image | **79** `_version`/`_image` pairs — the version lives in `X_version` AND embedded in the `X` ref |
| Dead keys | `MIOS_EMB_VERSION=`, `MIOS_AGENT_PASSPORT_VERSION=`, `MIOS_AGNTCY_OASF_VERSION=` (empty) |
| Literal alias-dupes | `MIOS_CONVERGE_IMAGE_RECHUNK_FORMAT_VERSION` == `MIOS_CONV_IMAGE_RECHUNK_FORMAT_VERSION` (same value, two names); `WEBTOOLS_*`/`CRAWL4AI_*` |
| Same value copy-pasted across surfaces | k3s `v1.32.1-k3s1` appeared in `[image.sidecars]` (version + ref), `[build.bake].core`, `mios-k3s.container`, `03-extra.list`, `k3s-cockpit.md`, `variables.md`, `mios.html` (configurator), `knowledge-graph.json`, `bound-images.tsv` (SBOM) — **~35 places**. Bumping it required editing all of them. |
| Two hand-maintained resolver twins | `usr/lib/mios/mios_toml.py` + `tools/lib/userenv.sh` (~200 tuples each), must stay equivalent (check-45) |

This is the **WS-NAME "unified key library"** debt (memory north-star; task_1514). It is a real NO-HARDCODE / SSOT law violation.

## 2. The invariant — LOSSLESS = byte-identical resolved env

The only safe way to refactor a load-bearing resolver is to **prove** each change changes nothing it shouldn't:

1. `mios-env-snapshot` = `env | grep ^MIOS_ | sort` → the ground truth. **Baseline captured** (`env-baseline.txt`, 2523 lines).
2. After every change: re-snapshot + `diff baseline new`. **Empty diff (or ONLY the exact keys I intend to drop) = ship. Anything else = revert.**
3. A permanent drift-gate `check_resolved_env_lossless` runs the snapshot-diff in CI, so no future change can silently move a value.

No phase below commits until its diff is clean.

## 3. Target unified state

- **One source of truth per fact.** A version/ref/port/path is declared ONCE; every other use DERIVES it (resolver composition, or a generator). Never a second literal.
- `[image.sidecars]`: ONE `X` full ref (`repo:tag`) per image. Resolver emits `MIOS_X_IMAGE = X` and `MIOS_X_VERSION = tag(X)` (derived). **No `X_version` keys.**
- `[build.bake].core`, the Quadlets, `03-extra.list`: **generated** from `[image.sidecars]` (+ an explicit local-builds list) — no re-listed literals.
- Docs / configurator (`mios.html`) / `knowledge-graph.json` / SBOM: **generated** from `mios.toml` — no hardcoded versions.
- **Always-latest**: refs carry `:latest`/family-channel intent; the resolved tag+digest is recorded to SBOM at build (ADR-0003).
- Minimal key namespace: dead keys removed; alias-dupes collapsed to one canonical name.

## 4. Phases (each ends with an EMPTY resolved-env diff + green drift-gate)

### Phase 0 — Freeze, baseline, gate (SAFE; no value change)
- Commit AGY's verified work (vendored k3s v1.36.2 + fonts/cursor, `mios-resolve-latest`, `mios-vendor-refresh`, `mios-web`/`mios-data` Containerfiles, check-87, terra.key) + the coordinated k3s `v1.32.1→v1.36.2` bump — get to a clean, consistent base.
- Land `mios-env-snapshot` + `check_resolved_env_lossless`.
- **Done when:** tree clean; baseline gate green.

### Phase 1 — Dead + alias-dup removal (provably lossless; no resolver-logic change)
- Drop dead-empty keys after grep-confirming **zero consumers**.
- Collapse literal alias-dupes to ONE canonical name; repoint consumers.
- **Done when:** env-diff shows ONLY the intended dropped keys; gate green.

### Phase 2 — `[image.sidecars]` version single-source (resolver-derive, BOTH twins)
- **Reconcile the inconsistent pairs first** (`cuda_version=latest` vs `:cuda` tag → make version match the tag) so `derive == old`.
- Change both twins: `image.sidecars.<X>` (ref) emits `MIOS_X_IMAGE=X` **and** `MIOS_X_VERSION=tag(X)`; remove `_version`-key handling.
- Delete every `X_version` key from `[image.sidecars]`.
- **Done when:** env-diff empty (`MIOS_X_VERSION` unchanged); check-45 twin-equivalence green.

### Phase 3 — Kill cross-section ref duplication (generate, don't re-list)
- `[build.bake].core`: derive sidecar refs from `[image.sidecars]` + an explicit local-builds list; `mios-bake-group` reads the composed set.
- `03-extra.list` + Quadlets: regenerate from `[image.sidecars]` (canonical `MIOS_*` env — never de-digest).
- **Done when:** a sidecar ref appears in exactly ONE place (`[image.sidecars]`); bake-plan/quadlet gates green.

### Phase 4 — Derived surfaces project from SSOT (no hardcoded versions in docs/UI/graph/SBOM)
- Extend `mios-ssot-regen` so `k3s-cockpit.md`, `variables.md`, `mios.html`, `knowledge-graph.json`, `bound-images.tsv` PROJECT versions/refs from `mios.toml`.
- New gate `check_no_hardcoded_ssot_literal`: a version/ref literal in a non-SSOT file that matches an SSOT key = violation.
- **Done when:** bumping one value in `mios.toml` + `mios-ssot-regen` updates EVERY surface; the gate is green.

### Phase 5 — Always-latest float + SBOM pin (task_1435/390/393)
- Float sidecar refs to `:latest`/family-channel; `mios-resolve-latest` records resolved tag+digest to SBOM at build.
- Gate: no hand-pinned `:vX.Y.Z` outside SBOM/allowlist.
- **Done when:** day-0 build floats to latest; SBOM records the pin; NO-HARDCODE-VERSION gate green.

### Phase 6 — Minimal key library (WS-NAME / task_1514)
- Auto-derive the minimal global key set (collapse `userenv.sh`'s ~200 tuples); reconcile `MIOS_AI_VLLM_*` (emitted) vs short `MIOS_VLLM_*` (consumed).
- **Done when:** the manual table is generated; env-diff empty; twin-parity green.

## 5. Permanent enforcement (so it can't regress)

- `check_resolved_env_lossless` — resolved env == committed baseline unless the baseline is deliberately bumped.
- `check_no_duplicate_value_key` — no two SSOT keys carry the same single-source value (a version in two keys).
- `check_no_hardcoded_ssot_literal` — docs/UI/graph/SBOM project from SSOT; no matching literal.
- Pre-commit hook — `mios-ssot-regen` on any `mios.toml` change (task_1497).

## 6. AGY task mapping

| Phase | AGY tasks |
|---|---|
| 0 | NEW task_1530 (freeze+baseline+gate) |
| 1 | NEW task_1531 (dead/alias-dup removal) |
| 2 | task_1514 + NEW task_1532 (sidecars version-derive) |
| 3 | task_1448 (rewire double-bakes) + NEW task_1533 (compose bake.core) |
| 4 | NEW task_1534 (derived-surface projection) + task_1496 (extend ssot-regen) |
| 5 | task_1435 / task_1441 / task_1444 |
| 6 | task_1514 |

## 7. Sequencing note

Phase 0 is mandatory first — it commits the messy in-flight tree to a clean base **and** lands the lossless gate that guards Phases 1-6. Nothing touches the resolver until the baseline gate is green. The running `0.3.0` publish is unaffected (it cloned an earlier HEAD); the unified refactor ships in `0.3.1+` (task_1499 version bump).
