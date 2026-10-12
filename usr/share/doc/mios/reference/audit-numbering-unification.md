<!-- AI-hint: Honest census + corrected unification design for MiOS's many build/system numbering schemes. Splits the SPARSE banded STAGE IDENTITY (automation/NN-name.sh prefix == [NN-name] log label, the unifiable coordinate) from the DENSE PROGRESS ORDINAL (build.sh SCRIPT_COUNT 1..70) — they are two legitimately different numbers and must NOT be collapsed. Resolves the real split: the 121 drift-checks carry TWO disagreeing numbers (SSOT TSV ordinal 1..121 vs hand-written colliding (NN) echo labels). Design = SSOT [pipeline] band table + single log.sh reporter projecting [NN-name:CC] + one drift-gate; folds all critic corrections (band table redrawn, no fabricated dup precondition, run_step marked future, gate asserts only what it verifies). External tool counters (buildah STEP i/N, dnf5 [i/N], cargo) are irreducible. -->
<!-- AI-related: automation/build.sh, automation/98-drift-checks.sh, automation/99-postcheck.sh, usr/lib/mios/log.sh, usr/share/mios/reference/drift-gate-index.tsv, usr/share/mios/reference/pipeline-index.tsv, tools/native/mios-gen/src/indexes.rs, usr/share/mios/mios.toml, docs/design/doc-unified-pipeline.md -->

# Numbering Unification — Audit & Architecture

**Status:** Reference & Design · **Measured from `C:\MiOS`**

MiOS build and system numbering separates sparse stage identity from dense progress tracking:
1. **Stage Identity (Family A):** Sparse, banded coordinate (`automation/NN-name.sh`, `[NN-name]` log tags via `usr/lib/mios/log.sh`).
2. **Progress Ordinal (Family B):** Dense running execution count (`build.sh SCRIPT_COUNT 1..70`).
3. **Drift Gate SSOT Index (Family C):** Canonical `usr/share/mios/reference/drift-gate-index.tsv` ordinal dispatched via `tools/generate-gate-index.py`.
