<!-- AI-hint: Build specification for MiOS's generative documentation system -- extends mios-ai-tag as the comment-corpus SSOT, adds the mios-manual CLI and a content-hash landing ledger so a comment can only be deleted after its knowledge is provably in a doc.
     AI-related: usr/libexec/mios/mios-ai-tag, usr/libexec/mios/mios-ai-hint-coverage, tools/generate-manual.py, automation/98-drift-checks.sh, usr/share/mios/mios.toml -->
# BUILD SPECIFICATION -- MiOS generative documentation system

Status: implementable as written. No further design work required.
Date: 2026-08-09. Written against `C:\MiOS` HEAD `c25b17ac`.
Inputs: `survey-comments.md`, `survey-generators.md`, `survey-docs.md` (same directory).

---

## 0. The thesis, in one paragraph

MiOS already has the hard half built. `usr/libexec/mios/mios-ai-tag` is a correct,
idempotent, comment-syntax-aware, BOM/CRLF/shebang/front-matter-safe header engine that
has tagged 1,922 files and is already imported as the taggability SSOT by two consumers.
What is missing is (a) a **lexer + classifier** that can tell a line-scoped `why` comment
from a 90-line design essay, (b) a **generator** that assembles a manual from
`[SSOT tables] + [AI-hint corpus] + [authored prose]` with regeneration that cannot
destroy hand-writing, and (c) a **ledger** that makes "this comment's knowledge has landed
in a doc" a machine-checkable fact so deletion can be gated instead of trusted.

Everything below builds exactly those three things and nothing else. Four existing
surfaces are retired. Two rival definitions of "which files count" are collapsed into
mios-ai-tag. There is **one** new CLI (`mios-manual`) with six subcommands, not six tools.

**The governing invariant, stated once:**

> A comment block may be removed from source **only** when the corpus ledger records a
> doc passage annotated with that block's own content hash, and the passage retains at
> least 90% of the block's words. Deletion is mechanically gated by `check_comment_landing`
> and is always the last step of a three-commit sequence (harvest -> verify -> prune).

---

## 1. Components

Language column is Law 14 (`mios.toml [laws]` id 14 TARGET-LANGUAGES). **Law 14 note:**
the whole corpus tier is Python, and this is compliant, not an exemption. Law 14 assigns
Python to the AI plane; the AI-hint corpus *is* the AI plane (hints are LLM-authored,
LLM-consumed, and the teacher lane is an OpenAI `/v1` call). More decisively, taggability
is defined once, in Python, in `mios-ai-tag`, and is already imported by
`mios-ai-hint-coverage` and `check-template-conformance`. A Rust rival would create a
second definition of "which files get documented" and desync four tools -- the exact
failure `survey-generators.md` §3.1 warns against. `check_target_languages` only fails on
new `.cs/.bat/.cmd/.go/.cpp`, so nothing here is gate-relevant. The Rust port target is
recorded as task_2235 and is a *behind-the-same-CLI* replacement of the lexer hot path
only, once the classifier's fixtures are frozen.

### 1.1 EXTEND -- `usr/libexec/mios/mios-ai-tag` (Python, existing 354 ln)

Responsibility (unchanged in kind, extended in scope): **the corpus SSOT** -- which files
are taggable, what a header block looks like, and how it is safely read and written.

Seven changes, all additive:

| # | Change | Detail |
|---|---|---|
| T1 | **Fix multi-line hint orphaning** (BLOCKER) | Today `AI_LINE_RE` matches only marker lines, so a wrapped continuation survives the strip and the new block is inserted mid-sentence; `existing_hint()` then reads one line and drops the rest. Replace the line filter in `retag()` with a **region strip**: find the first line matching `AI_LINE_RE`, then consume forward every subsequent line that is a comment line in the same style and does NOT start a non-`AI-` sentence -- concretely, consume while the line matches `^<marker>\s{2,}\S` (continuation indent) or `AI_LINE_RE`. `existing_hint()` gains the mirror: after matching `AI-hint:`, join following continuation lines with a single space before `_clean()`. |
| T2 | **`SKIP_DIR` gap** | Add `\.rustup`, `\.tmp\.driveupload`, `tr_clone`, `tr_remote`, `shellcheck-v[0-9.]+`. Without this a run from the drive root walks 65,133 vendored Rust-toolchain files and issues ~65k teacher calls. |
| T3 | **`EXT` orphans** | Add `.rs .psm1 .qml .snap .nft .ks .template .defaults`. 98 files already carry hand-written headers their own tagger cannot refresh. `comment_style()` gains `.rs -> "slash"`. |
| T4 | **New optional header row `AI-doc:`** | Fourth row, after `AI-functions`. Value: `<repo-relative doc path>#<anchor>`. Written only by `mios-manual prune`. This is the pointer that replaces migrated prose and the thing `check_comment_landing` resolves. `AI_LINE_RE` extended to `AI-(?:hint\|related\|functions\|doc):`. |
| T5 | **Cap from SSOT, not a literal** | `_clean(s, n=None)` where `n` defaults to `[ai_tag].hint_max_chars` via the shared `mios_toml` resolver (Law 7). Ship `hint_max_chars = 3000` at M0 so the next tagger run cannot truncate the 363 over-cap hints (max 2,837 chars, ~100k chars of prose at risk); ratchet it back to 260 in M5 **after** those hints are harvested and pruned. |
| T6 | **Teacher defaults from SSOT** | `--teacher-endpoint` and `--teacher-model` resolve via `mios_toml` exactly as `mios-ai-hint-coverage` already does. Today: endpoint fallback `8450`, `mios.toml` says `8500`, the tool's own header says `11450`, and model `gemma4:12b` appears nowhere in SSOT -- three values and a phantom. |
| T7 | **`--selftest`** | Round-trips the fixture set in `tests/fixtures/ai-tag/` (shebang, BOM, CRLF, md front-matter, wrapped hint, `.rs`) and asserts byte-identical second pass. Called by `test_mios_manual.py` so it runs inside `just drift-gate`. |

Interface (public, consumed by `mios_comments.py`, `mios-manual`, `mios-ai-hint-coverage`,
`mios-codebase-index`, `check-template-conformance`) -- **frozen, do not narrow**:

```python
EXT, BASENAMES, SKIP_DIR, SKIP_SUFFIX, JSON_EXT     # taggability data
indexable(path) -> bool ;  is_text(path) -> bool ;  walk(roots) -> Iterator[str]
comment_style(path) -> "md"|"slashstar"|"slash"|"dashdash"|"hash"
existing_hint(head) -> str                          # continuation-joined (T1)
existing_doc_anchor(head) -> str                    # NEW (T4)
make_block(style, hint, related, funcs, doc="") -> list[str]
retag(raw, style, block) -> bytes
extract_related(content, path) -> str ; extract_functions(content, path) -> str
```

**Not** rebuilt, per `survey-generators.md` §3: taggability classification, comment-syntax
dispatch, safe insertion, idempotent retag, the hint-provenance cascade, the offline regex
fallback, the related/functions extractors, `--manifest` resume, degrade-open teacher.

### 1.2 NEW -- `usr/lib/mios/mios_comments.py` (Python, ~450 ln)

Responsibility: **the comment lexer and the classifier**. Pure library: reads files, holds
no policy constants of its own (all thresholds arrive as a `Policy` dataclass built from
`[docs]`), writes nothing. Lives beside `mios_toml.py` -- the established home for shared
Python libraries -- so both `mios-manual` and any future consumer import one definition.

Why a library and not more of `mios-ai-tag`: `mios-ai-tag` is imported by four consumers
and must stay small and auditable; the lexer needs `tokenize`/`ast` and language tables
that those consumers do not want. It loads `mios-ai-tag` through the same
`SourceFileLoader` shim `mios-ai-hint-coverage` already uses, so taggability and comment
style still have exactly one definition.

```python
@dataclass(frozen=True)
class Block:
    path: str; start_line: int; end_line: int      # 1-indexed, inclusive
    kind: str          # "line" | "docstring" | "inline" | "blockcomment"
    style: str         # from mios_ai_tag.comment_style()
    text: str          # markers stripped, original newlines kept
    norm: str          # text.lower(), whitespace collapsed -- hashing input
    sha12: str         # sha256(norm.encode()).hexdigest()[:12]
    lines: int; words: int
    attach: str        # "file-header" | "pre-code" | "inline" | "orphan"
    anchor_code: str   # first following non-blank non-comment line, or ""
    in_header_block: bool

@dataclass(frozen=True)
class Verdict:
    cls: str      # "STAY" | "MIGRATE" | "DROP" | "READONLY" | "MIGRATE_HEADER"
    reason: str   # the rule id that fired -- exactly one
    stale: bool
    as_: str      # "" | "note" | "heading-fact" | "adr-candidate"

lex(path: str, raw: bytes|None = None) -> list[Block]
classify(block: Block, policy: Policy, refindex: RefIndex) -> Verdict
class Policy:  # built by Policy.from_toml(merged_mios_toml)
class RefIndex: # built once per run: every path/unit/mios-* name/MIOS_* var in the tree
```

`lex()` dispatch, by `comment_style()` plus three overrides. **Python is lexed with
`tokenize` for COMMENT tokens and `ast` for docstrings -- never regex** (the survey proved
regex miscounts multi-line data strings as prose). `.ps1` adds `<# ... #>`. `.rs` adds
`///` and `//!` doc comments. A block is a maximal run of consecutive full-line comments;
a blank line, a code line, or a style change ends it. A comment token that is not the
first token on its physical line is `kind="inline"`.

### 1.3 NEW -- `usr/libexec/mios/mios-manual` (Python, ~700 ln)

Responsibility: **the one documentation CLI.** Six subcommands. Nothing else in the
system generates or checks a document.

```
mios-manual render   [--root R] [--check] [--out DIR]
mios-manual audit    [--root R] [--json] [--deletions] [--stale]
mios-manual coverage [--root R] [--json]
mios-manual harvest  [--root R] --class C [--file F]... [--limit N] [--apply]
mios-manual prune    [--root R] --class C [--only-landed] [--apply]
mios-manual ledger   [--root R] [--check]
```

| Subcommand | Contract |
|---|---|
| `render` | Assembles `usr/share/doc/mios/manual.md`, the five derived reference docs, and `usr/share/doc/mios/README.md`. `--check` renders to a temp dir and byte-diffs against committed, printing per-file unified diff (max 40 lines each) -- the `generate-ai-manifest.py --check` diagnostic pattern. Exit 1 on any diff. **`--out` is honoured absolutely: `render` never deletes a path it was not asked to write** (explicitly closing `generate-manual.py`'s unconditional `shutil.rmtree` of `usr/share/doc/mios/manual/`, which would now destroy the authored tree). |
| `audit` | Read-only. Lexes + classifies the whole corpus, emits/refreshes the ledger, reports counts. `--deletions` evaluates the landing predicate for every pruned block. `--stale` lists dangling refs. |
| `coverage` | The ratchet measurement, `--json` shaped exactly like `mios-ai-hint-coverage --json` so the gate code is a copy. |
| `harvest` | Writes doc passages under `usr/share/doc/mios/manual/_harvest/` and fills the ledger's `landed_*` columns. **Never touches source files.** |
| `prune` | Replaces landed source blocks with a one-line pointer. **Refuses any block whose landing predicate does not hold**, even with `--apply`. |
| `ledger` | Regenerates `manual-corpus.tsv`; `--check` diffs it. |

Determinism rules, both non-negotiable and both lifted verbatim from tools that already
learned them the hard way:

1. **`git ls-files` scoping.** The corpus is `set(mios_ai_tag.walk(roots)) & set(git ls-files)`.
   `generate-ai-manifest.py` documents why: a filesystem walk makes the artifact a function
   of the developer's working directory, and a dev-generated artifact can then never match
   a clean CI regeneration. Degrade-open on "not a repo" -> fall back to `walk()` alone and
   print a WARNING to stderr (same shape as `_gitignored()` in `mios-ai-hint-coverage`).
2. **Stable ordering.** Every emitted list is sorted by `(path, start_line)`; every path is
   normalised to `/` separators and made repo-relative. No timestamps, no hostnames, no
   absolute paths in any generated artifact.

Config: everything from `[docs]` via `mios_toml.load_merged(mios_toml.layer_paths())`.
Zero literals (Law 7). Ports/paths that must appear in generated docs are read from SSOT
at render time, never typed.

### 1.4 NEW -- `usr/libexec/mios/test_mios_manual.py` (Python)

Sibling unit test, matching the `test_mios_docgen.py` precedent (already wired into
`just drift-gate` at Justfile:81). Contains:

* the **30 classifier fixtures** of §2.5, each asserting `(cls, reason)` exactly;
* `mios-ai-tag --selftest` invocation (T7);
* a ledger round-trip: lex -> classify -> ledger -> re-lex -> identical `sha12` set;
* a harvest/prune round-trip on a temp tree asserting the landing predicate is
  **false** before harvest, **true** after, and that `prune` refuses when false.

Add `test_mios_manual.py` to the `just drift-gate` block next to `test_mios_docgen.py`.

### 1.5 NEW -- `usr/share/doc/mios/manual/` -- where AUTHORED prose lives

```
usr/share/doc/mios/manual/
  00-index.md            authored
  01-what-mios-is.md     authored   <- the ONE copy of the 69-times-duplicated boilerplate
  ...
  _harvest/<slug>.md     machine-written ONCE by `harvest`, hand-owned thereafter
```

Each chapter file carries YAML front matter and is **never rewritten by the generator**:

```yaml
---
chapter: 04
part: "II -- Architecture"
title: The AI plane
status: authored              # authored | harvested | stub
sources: [ports:ai, units:mios-agent-pipe.service]
harvest: ["usr/lib/mios/agent-pipe/mios_pipe/routing/dag_exec.py:254"]
---
```

Body prose is free-form. The generator touches a chapter file only **inside** marker pairs:

```markdown
<!-- MIOS-GEN:ports:ai -->
...generated table, overwritten every render...
<!-- /MIOS-GEN -->
```

`render --check` compares only (a) the assembled `manual.md`, (b) the derived reference
docs, and (c) the contents *between* markers. Prose outside markers is invisible to the
gate and therefore uneditable by it. This is the direct fix for the failure
`survey-generators.md` §1 measured: regenerating today's manual **deletes the document's
H1 title** because the generator and its output diverged in both directions.

### 1.6 Generated outputs (all machine-owned, all gated)

| Path | Generator | Content |
|---|---|---|
| `usr/share/doc/mios/manual.md` | `mios-manual render` | assembled book: authored chapters in `chapter` order, with derived sections spliced at markers |
| `usr/share/doc/mios/README.md` | `mios-manual render` | **the missing entry point** -- 130 baked doc files currently have none on a booted host |
| `usr/share/doc/mios/reference/ports.md` | idem | from `[ports.categories]` + `[ports]` -- the single generated answer that makes the 1,155 stale port refs mechanically fixable |
| `usr/share/doc/mios/reference/laws.md` | idem | from `[laws]` (16 rows) + `[laws.root_exceptions]` (12 units) |
| `usr/share/doc/mios/reference/pipeline.md` | idem | from `[build.pipeline]`; kills the 72 phantom `automation/NN-*.sh` |
| `usr/share/doc/mios/reference/cli.md` | idem | from `[verbs.*]` (113) + `usr/libexec/mios/` backends |
| `usr/share/doc/mios/reference/units.md` | idem | from the unit files themselves (see H-1 blocklist) |
| `usr/share/mios/reference/manual-corpus.tsv` | `mios-manual ledger` | the corpus ledger -- §3 |
| `usr/share/mios/reference/doc-ratchet-floor.tsv` | `mios-manual coverage --write-floor` | lowest ceiling ever observed, per ratchet axis |

Link policy: `[docs].link_base` in `{repo, host}`, default `repo`. Emitted links are
repo-relative (`../adr/0006-...md`) or install-absolute (`/usr/share/doc/mios/...`).
`file://` and `C:\`/`C:/` are forbidden in generated output and fail gate 152 -- today's
manual has 50 `file:///C:/MiOS/...` links inside the documentation of a Linux OS.

### 1.7 SSOT additions -- `usr/share/mios/mios.toml`

```toml
[docs]
# ---- classifier thresholds (see BUILD-SPEC §2). Lowering migrate_min_* is how the
# ratchet advances; every value here is read by mios_comments.Policy.from_toml().
stay_max_lines        = 2
stay_max_words        = 25
migrate_min_lines     = 6      # ratchet: 6 -> 4 -> 3 as the mid-size band clears
migrate_min_words     = 60
landing_min_word_ratio = 0.90

# ---- ratchet ceilings. MONOTONE DECREASING -- check_doc_ratchet_monotone forbids
# raising any of these. Set from the first `mios-manual coverage --json` measurement.
max_unmigrated_narrative = 0   # set at M1 from measurement (survey estimate ~2127)
max_stale_refs           = 0   # set at M1 (survey verified ~70)
max_undocumented_components = 0  # set at M2

# ---- provenance blocklists (survey hazards H-1/H-2/H-4)
blocklist_globs = [            # generated artifacts: never a migration SOURCE
  "automation/lib/globals.sh", "automation/lib/globals.ps1",
  "tools/native/mios-unit-gen/tests/golden/**",
  "**/*.generated.*", "usr/share/mios/names.generated.txt",
]
llm_payload_globs = [          # READONLY: extract, never edit -- editing changes model behaviour
  "usr/share/mios/owui/**", "usr/share/mios/hermes/**", "usr/share/mios/prompts/**",
  "usr/share/mios/ai/**", "etc/mios/system-prompts/**", "usr/share/mios/agents/**",
  "usr/share/mios/cookbooks/**", "etc/skel/.config/mios/**",
]
ref_allowlist = [              # legitimately absent from the source tree (runtime-created)
  "/etc/ceph/ceph.conf", "/etc/cdi/nvidia.yaml", "/var/run/cdi/nvidia.yaml",
  "/etc/containers/policy.json", "/etc/mios/manifest.json", "/var/**",
  "@@MIOS_*@@",
]
link_base = "repo"

[docs.signals]
# Anchored regexes. Kept in SSOT so a rule change is an operator edit, not a code edit.
why       = "\\b(because|so that|otherwise|avoid|prevents?|must not|never|do not|don't|fail-open|fail-closed|deliberately|intentional(ly)?|noqa|workaround|upstream bug|race|deadlock)\\b"
narrative = "\\b(operator|used to|no longer|previously|regression|root cause|incident|reverted|scrapped|rejected|instead of|alternative|rationale|invariant|degrade|ADR-[0-9]+|Law [0-9]+|AGY-[0-9]+|WS-[A-Z]+|commit [0-9a-f]{7,}|[0-9]{4}-[0-9]{2}-[0-9]{2})\\b"
fact      = "\\b(broken|deprecated|removed|disabled|not supported|only on|requires|since|as of|F[0-9]{2}\\+|WS-[A-Z]+|AGY-[0-9]+|ADR-[0-9]+|Law [0-9]+)\\b"
code      = "^\\s*(if|for|while|def|class|function|export|set|return|elif|else|fi|done|esac|end|\\}|\\{)\\b|[;{]\\s*$|^\\s*[\\w.]+\\s*=[^=]"

[ai_tag]
hint_max_chars    = 3000   # M0: raised from the hardcoded 260 so a re-tag cannot truncate
                           # the 363 over-cap hand-written hints. Ratcheted to 260 at M5.
max_overlong_hints = 0     # set at M1 from measurement (survey: 363)
teacher_port_key  = "llm_light"
teacher_model     = "granite4.1:3b"   # must exist in [ai].available_models

[laws.projection_registry]
surfaces = [
  # ... existing three rows ...
  { generator = "usr/libexec/mios/mios-manual", check = "check_manual_generated",  output = "usr/share/doc/mios/manual.md + reference/*.md + README.md" },
  { generator = "usr/libexec/mios/mios-manual", check = "check_comment_landing",   output = "usr/share/mios/reference/manual-corpus.tsv" },
]
```

### 1.8 MODIFY -- existing files

| File | Change |
|---|---|
| `automation/98-drift-checks.sh` | +6 `check_*` functions, appended to `main()` after `check_globals_generated` -> ordinals 152..157. Each uses `_require_python3` (never a bare `command -v python3 \|\| return 0`, which `check_no_silent_tool_skips` flags). |
| `tests/drift-gate-negatives.sh` | +6 `test_*` functions, each registered in `main()`. Each must contain both a `die` and a `98-drift-checks.sh` invocation or `check_negatives_are_effective` (gate 147) rejects it. |
| `usr/share/mios/reference/drift-gate-index.tsv` | regenerated by `tools/generate-gate-index.py` -- do not hand-edit (gate 155/`check_gate_index`). |
| `tools/sync-generated.sh` | new step `7/7 manual -- mios-manual render + ledger`, **after** the AI manifests (it reads their outputs). Renumber the step labels. |
| `Justfile` | delete the `manual:` target (it invoked the retired generator and was in neither `sync` nor `drift-gate`); add `test_mios_manual.py` beside `test_mios_docgen.py` in `drift-gate`. |
| `usr/libexec/mios/mios-codebase-index` | **consolidation**: delete its private `EXT`/`BASENAMES`/`SKIP_DIR`/`SKIP_SUFFIX` (a second, already-diverged definition of the corpus -- it lacks `.ps1`-era additions and includes `.json`, which mios-ai-tag explicitly skips) and import `mios-ai-tag` via the `_load_tagger` shim. Net -60 lines, one definition. |
| `tools/render-globals.py` | stop projecting `[units.*].comment` into `MIOS_UNITS_*_COMMENT`. 129 AI-hint blocks x2 resolvers = 1.19 MB of generated shell/PowerShell that is mostly prose, and a fourth home for every stale unit sentence. Gate 157 enforces. |
| `usr/share/mios/mios.toml` | `[docs]`, `[docs.signals]`, `[ai_tag]` additions above; register the two projection-registry rows. |

### 1.9 RETIRE

| File | Reason | Harvest first? |
|---|---|---|
| `tools/generate-manual.py` | Not a generator: imports only `os/argparse/shutil`, zero repo reads, 1311 lines of hardcoded literal, 21/43 links broken, chapter 01 contradicts `[laws]`, and it `rmtree`s a repo directory ignoring `--output`. | **Yes.** Its 154 `{title, desc, content, citations}` records are genuine hand-written narrative no extractor can synthesise. Convert to authored chapter files in M2 (task_2225). Discard: the `credits.md#L39` line-number citations, the `file:///C:/MiOS/` URIs, the per-page boilerplate, the hardcoded "Seven Architectural Laws" list. |
| `tools/generate-unified-knowledge.py` | Whole-repo content dump; `os.walk(".")` not `--root`; no CLI, no `--check`, no gate; category taxonomy keys off `specs/core`, `specs/memory`, `evals/` -- directories that do not exist, so almost everything lands in `"other"` and never enters the semantic index; inverted extension filter sweeps in every dotfile; three-regex "redaction" misses `sk-proj-*`, `ghp_*`, PEM, JWT. | No. If RAG seeding is still wanted it is `mios-manual audit --json` -- the same corpus, ~100x smaller, deterministic, gated. |
| `tools/lib/extract_comments.py` + `usr/share/doc/mios/knowledge/recovered_comments.md` | 4.13 MB / 60,019 lines / 801 sections, 42% of the entire markdown corpus, built from git diffs by harvesting **only deleted comment lines** -- a graveyard, stale by construction, and its first entry is already contradicted by `globals.sh:170`. | No -- but see the safety note below. |
| `Justfile:161 manual:` target | invoked the retired generator | -- |

**Safety note on `recovered_comments.md`:** it is the only surviving copy of comments
already deleted from source, so it is not covered by the landing invariant (nothing left
to protect). Delete it only in M7, after `mios-manual audit --json` has been run once
across it in `--corpus-extra` mode and any block classified `MIGRATE` with a resolvable
target has been harvested. That is a one-shot rescue pass, not an ongoing input.

---

## 2. Comment classifier

Deterministic, ordered, first-match-wins. `classify()` returns exactly one `reason`, which
is why the rule set is unit-testable: every fixture asserts a `(cls, reason)` pair.

### 2.1 Preliminaries

For a block `B`: `L` = full comment lines, `W` = words after stripping markers and
punctuation-only tokens, `WHY` = `[docs.signals].why` matches `B.text`,
`NARR` = `[docs.signals].narrative` matches, `FACT` = `[docs.signals].fact` matches.

`RefIndex` is built once per run over the whole tree: the set of every repo-relative path,
every `/usr/{lib,libexec,share}/mios/**` and `/etc/mios/**` path, every `mios-*` name,
every `*.{service,container,timer,socket,target,mount,path}` unit name, and every `MIOS_*`
identifier that appears **on a code line** anywhere. A reference is dangling only if it is
absent from `RefIndex`, absent from the tree, and not matched by `[docs].ref_allowlist`.
This is the false-positive filter the survey used to get from 353 raw hits to ~70 verified.

### 2.2 The rules

```
R0  GENERATED-SOURCE
    path matches [docs].blocklist_globs
    -> DROP, reason="generated-artifact"
    Rationale: globals.{sh,ps1} embed 129 whole unit-comment bodies as string
    literals, and tests/golden/ holds 160 byte-copies of shipped units. Extracting
    from the artifact triple-counts. Extract from the unit file.

R1  READONLY-PAYLOAD
    B.kind == "docstring" AND path matches [docs].llm_payload_globs
    -> READONLY, reason="llm-payload"
    May be COPIED into docs. MUST NEVER be edited, reformatted, or pruned --
    it is the tool-description string sent to the model.

R2  HEADER
    B.in_header_block  (starts at the tagger's own insertion index and contains an AI- marker)
    len(hint) <= [ai_tag].hint_max_chars  -> STAY,           reason="ai-header"
    len(hint) >  [ai_tag].hint_max_chars  -> MIGRATE_HEADER, reason="overlong-hint"
    Also tag quality="low" (not a class) when hint in {"stub","TOML Configuration", ...}
    or when the hint text is shared verbatim by >=3 files.

R3  COMMENTED-OUT CODE
    >= 50% of stripped lines match [docs.signals].code  AND  W/L < 4
    -> DROP, reason="commented-out-code"

R4  BANNER
    every stripped line matches ^[\s\-=*_~+.#]*$
      OR (<= 8 words AND no sentence-final punctuation AND not WHY)
    FACT -> MIGRATE, as="heading-fact", reason="banner-fact"
    else -> DROP,                       reason="banner"

R5  SIZE + SIGNAL  (the core split)
    a) L <= stay_max_lines AND W <= stay_max_words
         -> STAY, reason="local-scoped"
    b) L <= stay_max_lines AND W >  stay_max_words AND NARR
         -> MIGRATE, as="note", reason="fat-inline-narrative"
    c) L >= migrate_min_lines OR W >= migrate_min_words
         -> MIGRATE, reason = "narrative-history" if NARR else "narrative-rationale"
         as="adr-candidate" when NARR and W >= 250
    d) otherwise  (the 3-5 line / <60 word mid-size band)
         NARR -> MIGRATE, reason="midsize-narrative"
         else -> STAY,    reason="midsize-why"     (WHY or not; default is to keep)

R6  INLINE
    B.kind == "inline"  -> STAY, reason="inline-scoped"
    UNLESS R5(b) already fired, in which case MIGRATE-COPY: the prose is copied to
    docs and the source line is REPLACED by a <=25-word summary + doc anchor.
    A trailing comment is never simply deleted.

R7  STALE  (an axis, not a class -- evaluated for every block regardless of R0-R6)
    any dangling reference (per RefIndex) -> Verdict.stale = True
    A stale block is BLOCKED from harvest: `harvest` refuses it and prints the
    dangling token. Fix the reference in place first. This is what stops the 9
    renumbered automation stage refs -- three of which now resolve to a DIFFERENT
    script -- from being canonised into the manual.
```

### 2.3 What each class means operationally

| Class | Doc action | Source action | Ever deleted? |
|---|---|---|---|
| `STAY` | none | none | never |
| `MIGRATE` | write passage under `_harvest/` | replace with `# see: <doc>#<anchor>` | yes, after landing proof |
| `MIGRATE_HEADER` | write passage; leave `AI-hint` <= cap summary | rewrite header, add `AI-doc:` row | the overflow only |
| `READONLY` | copy passage | none, ever | never |
| `DROP` | none | `prune --class=commented-out-code` may delete | yes -- but **only** `reason in {commented-out-code, banner}`; `generated-artifact` is never touched because the artifact is regenerated from its own source |

### 2.4 Ordering guarantee

R0 before R1 before R2 ... R6. R7 runs on the result. Two properties this buys:
a generated artifact can never be a migration source (R0 first), and an LLM payload can
never be classified `MIGRATE` and pruned (R1 before everything that migrates).

### 2.5 Fixture table (the unit tests -- all drawn from the survey)

| # | Fixture | Expected `(cls, reason)` |
|---|---|---|
| 1 | `automation/lib/globals.sh` any `MIOS_UNITS_*_COMMENT` body | `(DROP, generated-artifact)` |
| 2 | `tools/native/mios-unit-gen/tests/golden/mios-agent-pipe.service:82` 93-line block | `(DROP, generated-artifact)` |
| 3 | `usr/lib/systemd/system/mios-agent-pipe.service:82` -- the same 93 lines, real source | `(MIGRATE, narrative-rationale)` |
| 4 | `usr/share/mios/owui/tools/openui.py:207` 96-line docstring, "NEVER output openui-lang" | `(READONLY, llm-payload)` |
| 5 | `.gitignore:1` AI-hint, 1 line, hash style, no shebang | `(STAY, ai-header)` |
| 6 | `mios_pipe/routing/agent_call.py` 2,837-char AI-hint | `(MIGRATE_HEADER, overlong-hint)` |
| 7 | a file whose hint is literally `stub` | `(STAY, ai-header)` + `quality="low"` |
| 8 | 6 lines of `# if [ -f x ]; then` / `# fi` | `(DROP, commented-out-code)` |
| 9 | `# ----------------------------------------------------------------------------` | `(DROP, banner)` |
| 10 | `usr/lib/systemd/system-preset/90-mios.preset:236` `# --- Broken with composefs on F42+ ---` | `(MIGRATE, banner-fact)` + `as=heading-fact` |
| 11 | `usr/share/mios/postgres/schema-init.sql:664` `-- ===== WS-VECTOR V2 ... (task_1032) =====` | `(MIGRATE, banner-fact)` |
| 12 | `memguard.py` `except Exception:  # noqa: BLE001 -- fail-open: a guard bug never blocks a store` | `(STAY, inline-scoped)` |
| 13 | `preempt.py` `# noqa: BLE001 -- a flaky queue read must never preempt` | `(STAY, inline-scoped)` |
| 14 | `build-mios.ps1:109` `# C:\MiOS deliberately excluded -- dev working tree, not a consumer install path` | `(STAY, local-scoped)` |
| 15 | `mios-tailscale-serve.ps1:67` 2 lines, ~28 words, names its own gate, no NARR | `(STAY, midsize-why)` -- **the boundary case**: L=2 but W>25 and NARR is false, so R5(b) misses and R5(d) catches it. Asserting this pair is what keeps the thresholds honest. |
| 16 | `mios.toml` `ssh = 8100 # host admin sshd. "mios-dev ssh should be port 2222" -- hardened off :22, off the prior :49955...` | `(MIGRATE, fat-inline-narrative)` + `as=note` |
| 17 | `mios.toml` `lane_concurrency_gpu = 2   # P7 swarm-safety ("finish! I authorize you"): reverted 4->2...` | `(MIGRATE, fat-inline-narrative)` |
| 18 | `.github/workflows/mios-ci.yml:282` 42 lines / 408 words, rootful-podman GID incident | `(MIGRATE, narrative-history)` + `as=adr-candidate` |
| 19 | `usr/share/mios/mios.toml:5457` 90 lines / 821 words, crawl4ai container scrapped, cites Law 5 | `(MIGRATE, narrative-history)` + `as=adr-candidate` |
| 20 | `usr/share/mios/mios.toml:3125` 53 lines, canonical worker pool, rejected alternative | `(MIGRATE, narrative-rationale)` |
| 21 | `Get-MiOS.ps1:1244` 45 lines, operator quote + commit `1e3484f` | `(MIGRATE, narrative-history)` |
| 22 | `usr/lib/systemd/user/mios-computer-use-server.service:4` 27-line prologue, "WHY A USER SERVICE" | `(MIGRATE, narrative-rationale)` |
| 23 | `mios-coderun-sandbox@.container:4` 41-line prologue | `(MIGRATE, narrative-rationale)` |
| 24 | `# Set the timeout` (1 line, 3 words) above `TIMEOUT=30` | `(STAY, local-scoped)` |
| 25 | 4 lines / 40 words of pure mechanism, no NARR, WHY present | `(STAY, midsize-why)` |
| 26 | 4 lines / 40 words containing `ADR-0006` | `(MIGRATE, midsize-narrative)` |
| 27 | any block citing `automation/38-hermes-agent.sh` (now `72-`) | `stale=True`; `harvest` refuses |
| 28 | any block citing `/usr/lib/mios/mios_accounts.py` (planned, absent) | `stale=True` |
| 29 | a block citing `/etc/ceph/ceph.conf` (runtime-created, allowlisted) | `stale=False` |
| 30 | `usr/lib/nftables/mios-egress.nft` `@@MIOS_EGRESS_MODE@@` template token | `stale=False` (allowlisted `@@MIOS_*@@`) |

Also assert the aggregate on the real tree, as a regression tripwire (tolerance +/-3%):
`STAY` ~= 5,000 blocks, `MIGRATE` ~= 1,900, `DROP` ~= 400, `READONLY` ~= 42.

---

## 3. Doc model

### 3.1 The three layers, and which one wins

| Layer | Home | Written by | Regeneration risk |
|---|---|---|---|
| **AUTHORED** | `usr/share/doc/mios/manual/NN-*.md` outside markers; every existing `adr/`, `upstream/`, `guides/`, `concepts/` file | humans | **zero** -- the generator has no code path that writes outside a marker pair or outside its declared output list |
| **HARVESTED** | `usr/share/doc/mios/manual/_harvest/<slug>.md` | `harvest` once, humans thereafter | zero after first write; `harvest` refuses to overwrite an existing passage unless `--force` |
| **DERIVED** | inside `<!-- MIOS-GEN:id -->` markers, plus the five `reference/*.md`, plus `manual.md`, plus `usr/share/doc/mios/README.md` | `render`, every time | total, by design |

### 3.2 Assembly

`render` executes in five phases:

1. **Collect.** Read every `manual/NN-*.md` front matter. Sort by `(part, chapter, filename)`.
   Duplicate `chapter` numbers are a hard error (this is the ADR-numbering-collision failure
   mode, pre-empted).
2. **Derive.** Build each generator id's content:
   * `ports:<category>` from `[ports.categories]` + `[ports]`
   * `laws` / `laws:root-exceptions` from `[laws]`
   * `pipeline` from `[build.pipeline]`
   * `verbs:<section>` from `[verbs.*]`
   * `units:<name>` from the unit file's own comment block (**not** `mios.toml [units.*]`,
     **not** `globals.*` -- H-1)
   * `index:<glob>` -- one line per file: `path -- AI-hint`, from the corpus
   * `related:<path>` -- the "See also" graph from `AI-related`
   * `api:<path>` -- from `AI-functions`
   * `boilerplate:what-mios-is` -- the single canonical copy, replacing 69 drifting ones
3. **Splice.** For each chapter file, replace marker interiors in place. Byte-identical
   interiors are not rewritten (keeps `git status` clean).
4. **Assemble.** Concatenate chapters into `manual.md` with a generated TOC and the header
   `<!-- GENERATED by mios-manual render -- edit usr/share/doc/mios/manual/, not this file -->`.
5. **Emit** the five `reference/*.md` and `usr/share/doc/mios/README.md`.

Every derived section ends with a provenance line so a reader can audit the claim:
`<!-- derived from usr/share/mios/mios.toml [ports.categories] -->`.

### 3.3 Harvest passage format

A harvested passage is self-describing, and the annotation is what the landing predicate
reads:

```markdown
### Rootful podman in the bake step

<!-- mios-src: .github/workflows/mios-ci.yml:282-323 sha=8f21c0a4d3b7 -->

sudo: rootful podman avoids the user-namespace UID exhaustion that breaks the
bound-images bake step. ...

<!-- /mios-src -->
```

`sha` is the block's `sha12` -- the hash of the **source** text. A stub cannot satisfy the
predicate, because the predicate also checks word count against the ledger row.

### 3.4 The corpus ledger -- `usr/share/mios/reference/manual-corpus.tsv`

Tab-separated, header row `#`-prefixed, sorted by `(path, start_line)`:

```
path  start_line  end_line  lines  words  sha12  class  reason  as  stale  landed_doc  landed_anchor  landed_words  pruned
```

* `landed_*` empty until `harvest` fills them.
* `pruned` is `0`/`1`; set by `prune`.
* The file is regenerated by `ledger`, but `landed_*` and `pruned` are **carried forward by
  `sha12`**, not recomputed -- so moving a block within a file, or reformatting the code
  around it, does not lose its landing record.
* Rows whose `sha12` no longer appears in the tree and whose `pruned=1` are retained as
  tombstones. That retention is what makes `check_comment_landing` able to prove, after the
  fact, that a deleted comment landed somewhere.

**The landing predicate**, implemented in one function and used by `prune`, `audit
--deletions` and gate 154:

```python
def landed(row, root) -> bool:
    if not row.landed_doc: return False
    p = os.path.join(root, row.landed_doc)
    if not os.path.isfile(p): return False
    passage = extract_passage(p, row.sha12)          # by the mios-src sha annotation
    if passage is None: return False
    return count_words(passage) >= policy.landing_min_word_ratio * row.words
```

---

## 4. Gates

Six new checks, appended to `main()` in `automation/98-drift-checks.sh` after
`check_globals_generated`, taking ordinals **152..157**
(`usr/share/mios/reference/drift-gate-index.tsv` currently ends at 151 -- regenerate it
with `tools/generate-gate-index.py`, never by hand).

Mandatory conformance for all six, or existing meta-gates reject them:

* use `_require_python3`, not `command -v python3 || return 0` -- else gate 146
  `check_no_silent_tool_skips` flags it;
* each needs a `test_*` in `tests/drift-gate-negatives.sh` registered in its `main()` --
  else gate 132 `check_negative_coverage` fails;
* each `test_*` must contain both a `die` and a `98-drift-checks.sh` invocation -- else
  gate 147 `check_negatives_are_effective` calls it ineffective;
* **none may be added to `[testing].negative_coverage_exempt`.** All six are exercisable
  with a pure text edit on a Linux runner; there is no "the binary isn't installed"
  excuse available, and claiming one would be the repo's documented failure mode.

### Gate 152 -- `check_manual_generated`

**Assertion.** `mios-manual render --check --root "$ROOT"` exits 0: the committed
`manual.md`, the five `reference/*.md`, `usr/share/doc/mios/README.md`, and every
`<!-- MIOS-GEN -->` marker interior are byte-identical to a fresh render. Additionally the
render output contains zero `file://` and zero `C:[\\/]` occurrences.

**Exact edit that makes it RED** (three independent ones, all one line):
1. Append `\nDRIFT\n` to `usr/share/doc/mios/manual.md`.
2. Change `[ports].agent_pipe` in `usr/share/mios/mios.toml` from `8700` to `8701` without
   re-rendering -- `reference/ports.md` and the spliced port tables drift.
3. Insert a line **inside** a `<!-- MIOS-GEN:laws -->` block in a chapter file.
   (Inserting a line *outside* the markers must **not** turn it red -- assert that too;
   it is the property that protects authored prose.)

**Negative test** `test_manual_generated`: back up `manual.md`; append `DRIFT`; assert the
check fails; restore; assert it passes. Then a second phase: append a paragraph to
`usr/share/doc/mios/manual/01-what-mios-is.md` **outside** any marker; assert the check
still **passes**; restore. Without that second phase the gate could be satisfied by a
generator that owns the whole file, which is precisely the failure being fixed.

**Failure diagnostics.** Print per-file `diff -u` head (40 lines) plus
`-> run: mios-manual render` and `-> or: bash tools/sync-generated.sh`.

### Gate 153 -- `check_doc_refs_resolve`

**Assertion.** `mios-manual audit --stale --json`: the count of dangling references, over
(a) every `AI-hint:`/`AI-related:`/`AI-doc:` header line in the corpus and (b) every
generated doc, is `<= [docs].max_stale_refs`. Dangling = absent from `RefIndex`, absent
from the tree, not matched by `[docs].ref_allowlist`.

**Exact edit that makes it RED.** In any `AI-related:` line, change
`automation/01-system-files-overlay.sh` to `automation/08-system-files-overlay.sh`
(the real historical drift: 18 doc refs point at the old number). Any of the nine
renumbered stage refs works identically. Equivalently, add
`# AI-related: usr/lib/mios/does-not-exist.py` to any tracked file.

**Negative test** `test_doc_refs_resolve`: create `usr/share/mios/mios-test-temp-docref.conf`
containing `# AI-hint: temp\n# AI-related: automation/99-nonexistent.sh\n`; assert the check
fails; `rm -f` it; assert it passes. (Uses the create-then-delete scratch-file pattern
already used by `test_eval_safety` with `mios-test-temp-eval`.)

**Why the ceiling starts non-zero.** ~70 verified stale refs exist today. Setting the
ceiling to the measured value at M1 and ratcheting it to 0 in M3 is the only way to land
the gate green; gate 156 makes the ceiling one-way.

### Gate 154 -- `check_comment_landing`

**Assertion.** Three sub-assertions, all cheap and all always live:
1. Every `# see: <doc>#<anchor>` / `AI-doc: <doc>#<anchor>` pointer left in source resolves
   to an existing file containing that anchor.
2. Every ledger row with `pruned=1` satisfies the landing predicate (§3.4). A pruned block
   whose doc passage was later deleted or gutted turns this red.
3. `landed_doc` is never cleared: the committed ledger's set of `(sha12 -> landed_doc)`
   pairs must be a superset of the one in `git show HEAD:...manual-corpus.tsv`. Degrade-open
   when git or the previous revision is unavailable.

**Exact edit that makes it RED** (three, one per sub-assertion):
1. In any file carrying a pointer, change `# see: usr/share/doc/mios/manual/_harvest/ci-bake.md#rootful-podman`
   to `#nonexistent-anchor`.
2. Delete a harvested passage's body from `_harvest/<slug>.md` while its ledger row still
   says `pruned=1` (word count falls below 90%).
3. Blank the `landed_doc` cell of any ledger row and commit.

**Negative test** `test_comment_landing`: write
`usr/share/mios/mios-test-temp-landing.conf` containing
`# AI-hint: temp\n# see: usr/share/doc/mios/manual/_harvest/definitely-not-there.md#x\n`;
assert red; `rm -f`; assert green. Then a second phase driving sub-assertion 2: take the
first ledger row with `pruned=1` (skip the whole phase if none exists yet, which is the
case until M5), truncate its passage body to one word, assert red, restore, assert green.

**This is the gate the information-safety requirement rests on.** It is not a "did you
remember" check -- it recomputes the predicate from the source hash every run.

### Gate 155 -- `check_comment_ratchet`

**Assertion.** `mios-manual coverage --json` and require all four:

```
unmigrated_narrative    <= [docs].max_unmigrated_narrative
stale_refs              <= [docs].max_stale_refs
overlong_hints          <= [ai_tag].max_overlong_hints
undocumented_components <= [docs].max_undocumented_components
```

`unmigrated_narrative` = ledger rows with `class in {MIGRATE, MIGRATE_HEADER}` and empty
`landed_doc`. `undocumented_components` = corpus files that no generated doc mentions.

**Exact edit that makes it RED.** Add to any tracked `usr/lib/mios/**/*.py` a 12-line
comment block of >=60 words containing the word `operator` (fires `narrative` signal ->
R5(c) `MIGRATE`, unharvested -> count +1 > ceiling). A single new over-cap AI-hint, or a
single new dangling ref, does it too.

**Negative test** `test_comment_ratchet`: create
`usr/lib/mios/mios_test_temp_ratchet.py` containing a 12-line narrative block matching the
signal regex; assert red; `rm -f`; assert green. (Note: creating a `.py` under
`usr/lib/mios` also touches gate 4's coverage denominator -- give the fixture a valid
`# AI-hint:` header so only the ratchet fires. This is deliberate: it proves the ratchet
fires *independently* of hint coverage.)

**Ceiling provenance.** Layered exactly like `[ai_tag].max_untagged`:
`--max-* > env MIOS_DOCS_MAX_* > [docs].max_* > permissive fallback (report-only)`. The
permissive fallback exists so a bare checkout is never false-failed -- and it is exactly
why gate 156 must exist.

### Gate 156 -- `check_doc_ratchet_monotone`

**Assertion.** For each of the four ceilings, the committed value must be
`<=` the value recorded in `usr/share/mios/reference/doc-ratchet-floor.tsv`. That file is
generated by `mios-manual coverage --write-floor`, which writes `min(recorded, current)`
per axis -- so the floor can only ever go down, and a ceiling can never be raised.

**Why this gate exists.** Gate 155 alone is unfalsifiable in practice: "the ratchet went
red" has a one-character fix (increment the ceiling), and this repo has a documented
history of gates that cannot fail. 156 makes raising a ceiling a *gate violation*, so the
only legal response to a red 155 is to migrate a comment or fix a ref. If an operator
genuinely must raise a ceiling, the floor file must be edited in the same commit and the
diff is loud and reviewable -- which is the point.

**Exact edit that makes it RED.** Change `[docs].max_unmigrated_narrative = 1800` to
`1801` (or any other ceiling +1) and commit without touching the floor file.

**Negative test** `test_doc_ratchet_monotone`: `sed` the SSOT ceiling +1; assert red;
restore the original value; assert green.

### Gate 157 -- `check_no_generated_prose_in_resolvers`

**Assertion.** `automation/lib/globals.sh` and `automation/lib/globals.ps1` contain zero
occurrences of `AI-hint:` and zero `MIOS_UNITS_[A-Z0-9_]*_COMMENT=` assignments.
(Today: 129 and 129.)

**Exact edit that makes it RED.** Re-enable the `comment` key in
`tools/render-globals.py`'s projection and run `bash tools/sync-generated.sh`. Or, for a
one-line proof, append
`MIOS_UNITS_TEST_COMMENT='# AI-hint: x'` to `automation/lib/globals.sh`.

**Negative test** `test_no_generated_prose_in_resolvers`: `cp` globals.sh to `.bak`; append
the one-line assignment; assert red; restore from `.bak`; assert green.

**Note on ordering.** 157 can only land *after* the unit narrative it currently duplicates
has been harvested from the unit files (M6), or it deletes the only projected copy of
prose that no doc yet holds. It is step M7 for exactly that reason -- which is the landing
invariant applied to a generated surface.

### Registration checklist for each of the six

1. Function added to `98-drift-checks.sh` and appended to `main()`.
2. `python3 tools/generate-gate-index.py` re-run (never hand-edit the TSV).
3. `test_*` added to `tests/drift-gate-negatives.sh` **and** registered in its `main()`.
4. Row added to `[laws.projection_registry].surfaces` for 152 and 154 (gate 137
   `check_projection_registry` verifies the generator path and check function both exist).
5. `bash automation/98-drift-checks.sh <check_name>` run standalone, green.
6. The RED edit above applied, gate run, **observed red**, edit reverted. A gate that has
   never been observed red has not been tested.

---

## 5. Migration ladder

Nine steps. Each is independently landable, leaves `just drift-gate` green, and is a
separate commit (or small series). **No source comment is deleted before M5, and every
deletion after that is gated by 154.**

### M0 -- Repair the tagger. No docs yet, no deletions.

* T1 multi-line orphaning fix + repair the 22 already-damaged headers
  (`mios-sync-theme`, `mios-sync-toml`, `mios-sync-to-root`, `mios-sync-theme.service`,
  `installation/mios-install.sh`, `installation/README.md`, and `mios-ai-tag`'s own line 2,
  truncated at `...(AI-hint purpose`). Detect them by `AI-related:` lines ending in a
  dangling comma.
* T2 `SKIP_DIR`, T3 `EXT`, T4 `AI-doc:` row, T5 `hint_max_chars = 3000`, T6 SSOT teacher,
  T7 `--selftest` + fixtures.
* Green: gate 4 unchanged; `mios-ai-tag --selftest` passes; a full `--no-llm --dry-run`
  pass over the tree produces zero diffs on already-tagged files.
* **This must be first.** Generating a manual from a corpus with silent truncation bakes
  the truncation into the product, and running the tagger before T5 destroys ~100k
  characters of hand-written prose.

### M1 -- Land the lexer, classifier, ledger. Read-only.

* `usr/lib/mios/mios_comments.py` + `usr/libexec/mios/test_mios_manual.py` with all 30
  fixtures + `mios-manual audit|ledger|coverage`.
* First `manual-corpus.tsv` committed. First `coverage --json` measurement written into
  `[docs]`/`[ai_tag]` ceilings and into `doc-ratchet-floor.tsv`.
* Gate 155 and 156 land here **in report-only posture** via the permissive fallback (no
  ceiling in SSOT yet) for one commit, then the measured ceilings go in and they become
  enforcing. Both negative tests land with them.
* Green: `just drift-gate` passes with 155/156 enforcing at the measured baseline.

### M2 -- The manual skeleton and gate 152.

* Create `usr/share/doc/mios/manual/`; convert `generate-manual.py`'s 154
  `{title,desc,content}` records into authored chapter files (data, not code); drop its
  `credits_map` line-number citations, `file:///C:/MiOS/` URIs, per-page boilerplate and
  the hardcoded seven-laws list.
* `mios-manual render` + gate 152 + `test_manual_generated` (both phases).
* Retire `tools/generate-manual.py` and the `manual:` Justfile target; add
  `mios-manual render` as step 7 of `tools/sync-generated.sh`.
* Green: `render --check` clean; 21-broken-of-43 links become 0 because links are now
  derived, not typed.

### M3 -- Derived reference docs and gate 153.

* Emit `ports.md`, `laws.md`, `pipeline.md`, `cli.md`, `units.md`,
  `usr/share/doc/mios/README.md`.
* Fix the ~70 verified stale refs **in place** -- especially the nine renumbered
  `automation/NN-` refs, three of which now resolve to a different script. Ratchet
  `[docs].max_stale_refs` to 0 and lower the floor.
* Gate 153 + `test_doc_refs_resolve`.
* Green: `audit --stale` reports 0; the 1,155 stale port refs now have one generated
  answer to be rewritten against (the rewrite itself is a separate doc-prose task, not
  a blocker here).

### M4 -- Harvest wave 1: the 363 over-cap hints. Still no deletion.

* `mios-manual harvest --class=overlong-hint --apply`. 363 passages under `_harvest/`,
  ledger `landed_*` filled. Source untouched.
* Gate 154 lands here with `test_comment_landing` phase 1 (pointer resolution); phase 2
  is inert until M5, which the test detects and skips explicitly.
* Green: `audit --deletions` reports 363 landed, 0 pruned.

### M5 -- Prune wave 1. **The first deletion in the whole programme.**

* `mios-manual prune --class=overlong-hint --only-landed --apply`. Each over-cap hint
  becomes a `<= 260`-char summary plus an `AI-doc:` row. `prune` refuses any block whose
  predicate is false, so this is mechanically safe.
* Ratchet `[ai_tag].hint_max_chars` back to `260` and `max_overlong_hints` to 0; lower the
  floor. The tagger's original cap is now safe to re-impose because nothing over-cap
  remains.
* `test_comment_landing` phase 2 becomes live.
* Green: full `mios-ai-tag --no-llm` pass over the tree is byte-identical (proving the
  truncation hazard is gone).

### M6 -- Harvest wave 2: the narrative mass.

Ordered by yield, because the corpus is extremely top-heavy:

1. `usr/share/mios/mios.toml` (36,159 narrative words), `build-mios.ps1` (23,791),
   `Get-MiOS.ps1` (17,041) -- 55% of all narrative lines in three files.
2. `usr/lib/mios/agent-pipe/**` -- 14 of the top 25 files; assemble as one
   "agent-pipe architecture" chapter, which is what they already are.
3. `as=adr-candidate` blocks -> real ADRs. Start with `.github/workflows/mios-ci.yml:282`
   (42 lines: root cause, exact error text, operator confirmation -- an ADR that never got
   written) and `mios.toml:5457` (90 lines, crawl4ai container scrapped, cites Law 5).
4. systemd/Quadlet prologues -> `units.md` + per-unit chapter sections.

Then prune each wave, gate-verified, in its own commit. Ratchet
`[docs].max_unmigrated_narrative` down after each wave and lower the floor.

### M7 -- Collapse the duplication (this is where 157 lands).

* Gate 157: stop projecting `[units.*].comment` into `globals.{sh,ps1}` -- 1.19 MB of
  generated resolver that is mostly prose, and the fourth home of every stale unit sentence.
* One-shot rescue pass over `knowledge/recovered_comments.md`, then delete it (4.13 MB, 42%
  of the markdown corpus) and retire `tools/lib/extract_comments.py`.
* Retire `tools/generate-unified-knowledge.py`.
* Replace the 69 copies of the "built two ways at once" boilerplate with
  `<!-- MIOS-GEN:boilerplate:what-mios-is -->`.
* De-duplicate the three `docs/design/` <-> `concepts/` pairs (116 KB) and add
  `docs/design/README.md` marking the tree process-only.
* Collapse `mios-codebase-index`'s rival taggability constants into the mios-ai-tag import.

### M8 -- Steady state.

* Each release: lower `[docs].max_unmigrated_narrative` by the wave just harvested; when
  the `L>=6` band is empty, lower `[docs].migrate_min_lines` 6 -> 4 -> 3. Gate 156 makes
  every one of those moves one-way.
* `mios-manual coverage` becomes the doc-health number quoted in the release notes.

### The ratchet, stated as one loop

```
while max_unmigrated_narrative > 0:
    audit                       # measure; classifier is fixed, so the number is comparable
    harvest --limit N --apply   # docs gain prose; source unchanged; gate 154 green
    audit --deletions           # PROOF: landing predicate true for the batch
    prune --only-landed --apply # source loses prose; pointer left behind
    lower the ceiling; lower the floor   # gate 156 makes it irreversible
```

Every arrow in that loop is a separate commit, and the middle one is the proof-of-landing
that must precede any deletion.

---

## 6. New tasks

Repo format (`AGY-TASKS.md`). Numbered from 1580, above the current maximum 1579.
Workstream tag: **WS-DOCGEN**.

```markdown
## task_2221 -- Fix mios-ai-tag's multi-line hint orphaning and repair the 22 damaged headers  (WS-DOCGEN | P0 | M)
**Goal:** D-1 The comment corpus is lossless -- a re-tag can never truncate or split a header, so every downstream doc generator reads whole prose.
**What+How:** `AI_LINE_RE` matches only lines containing an `AI-*:` marker, so a wrapped continuation survives `retag()`'s strip and the fresh block is inserted MID-SENTENCE between the hint and its orphan; `existing_hint()` then regexes a single line and drops the remainder -- a lossy round-trip. Replace the line filter with a REGION strip (first `AI_LINE_RE` match, then consume forward while the line matches `^<marker>\s{2,}\S` or `AI_LINE_RE`), and make `existing_hint()` join continuation lines with a single space before `_clean()`. Then repair the 22 already-damaged files, detectable as `AI-related:` lines ending in a dangling comma: `usr/libexec/mios/mios-sync-theme`, `mios-sync-toml`, `mios-sync-to-root`, `usr/lib/systemd/system/mios-sync-theme.service`, `installation/mios-install.sh`, `installation/README.md`, and mios-ai-tag's OWN header (line 2 is truncated at "...(AI-hint purpose"). Add `--selftest` round-tripping fixtures for shebang / BOM / CRLF / md front-matter / wrapped hint.
**Where:** `usr/libexec/mios/mios-ai-tag, tests/fixtures/ai-tag/`
**Done When:** `mios-ai-tag --selftest` passes; a full `--no-llm --dry-run` over the tree reports zero diffs on already-tagged files; no `AI-related:` line in the tree ends in a comma.
**Why:** generating a manual from a corpus with silent truncation bakes the truncation into the product, and every later step reads this corpus.
**Dep:** none

## task_2222 -- Close mios-ai-tag's SKIP_DIR and EXT gaps (65k vendored files in, 98 orphaned tags out)  (WS-DOCGEN | P0 | S)
**Goal:** D-2 The taggable corpus is exactly the authored corpus -- no vendored tree can enter it and no authored file can be locked out of its own tagger.
**What+How:** `SKIP_DIR` excludes `.venv`/`node_modules`/`target`/`site-packages` but NOT `.rustup`, so `mios-ai-tag --root /mnt/c/MiOS` would walk 65,133 vendored Rust-toolchain files (61,792 `.html` + 3,108 `.js`), find no existing hint, and issue ~65k teacher calls before writing `AI-hint` comments into the Rust standard-library docs. Add `\.rustup`, `\.tmp\.driveupload` (Drive's hardlink dedup cache), `tr_clone`, `tr_remote`, `shellcheck-v[0-9.]+`. Conversely 98 files already carry hand-written headers whose extensions are absent from `EXT` and can therefore never be refreshed: `.rs` 52, `.snap` 19, `.psm1` 15, `.qml` 7, `.nft` 2, `.ks`/`.template`/`.defaults` 1 each. Add all eight to `EXT` and map `.rs` to the `slash` comment style.
**Where:** `usr/libexec/mios/mios-ai-tag`
**Done When:** a walk from the drive root yields 2,142 authored files and zero `.rustup` paths; `mios-ai-hint-coverage --json` denominator includes the 52 Rust files.
**Why:** one accidental root-scoped run currently rewrites 65k vendored files and burns 65k inference calls; meanwhile the whole Rust native tier is invisible to its own tagger.
**Dep:** none

## task_2223 -- Resolve mios-ai-tag's hint cap, teacher endpoint and teacher model from SSOT  (WS-DOCGEN | P0 | S)
**Goal:** E-7 Law 7 NO-HARDCODE holds in the tagger itself -- the tool that documents the codebase must not be the one carrying phantom constants.
**What+How:** `_clean()` hardcodes a 260-char cap, `--teacher-endpoint` defaults to `localhost:${MIOS_PORT_LLM_LIGHT:-8450}` while mios.toml uses 8500 and the tool's own header says 11450, and `--teacher-model` defaults to `gemma4:12b`, a string that appears nowhere in SSOT. Resolve all three through the shared `mios_toml` resolver exactly as the sibling `mios-ai-hint-coverage` already does: `[ai_tag].hint_max_chars`, `[ai_tag].teacher_port_key` -> `[ports]`, `[ai_tag].teacher_model` (validated against `[ai].available_models`). Ship `hint_max_chars = 3000` so the 363 hand-written over-cap hints (max 2,837 chars) survive the next run; task_2229 ratchets it back to 260 once they are harvested.
**Where:** `usr/libexec/mios/mios-ai-tag, usr/share/mios/mios.toml`
**Done When:** no numeric or model literal remains in the tagger; `mios-hardcode-lint` is clean on the file; a re-tag leaves the 363 long hints byte-identical.
**Why:** three different values for one port and a model that does not exist mean the teacher path is untested folklore, and the 260 cap is a live data-loss trigger worth ~100k characters of prose.
**Dep:** task_2221

## task_2224 -- Build the comment lexer + classifier library with the 30-fixture unit test  (WS-DOCGEN | P1 | L)
**Goal:** D-3 "Stay in code" vs "migrate to docs" is a mechanical, testable function -- not a judgement call made once per file by whoever is reading it.
**What+How:** New `usr/lib/mios/mios_comments.py` (beside `mios_toml.py`, the established shared-library home) exporting `lex(path) -> list[Block]` and `classify(block, policy, refindex) -> Verdict`. Lex per language via `mios_ai_tag.comment_style()`, with three overrides: Python via `tokenize` COMMENT tokens + `ast` docstrings (NEVER regex -- regex miscounts multi-line data strings as prose), PowerShell `<# #>`, Rust `///`/`//!`. Classify with the ordered first-match rule set R0..R7 in BUILD-SPEC section 2: generated-artifact -> llm-payload -> header -> commented-out-code -> banner -> size+signal -> inline -> stale axis. Every threshold and every signal regex comes from `[docs]`/`[docs.signals]`, never from code. Ship `usr/libexec/mios/test_mios_manual.py` with the 30 named fixtures, each asserting an exact `(class, reason)` pair, including the boundary case `mios-tailscale-serve.ps1:67` (2 lines, ~28 words, why-signal, no narrative-signal -> STAY via the mid-size rule).
**Where:** `usr/lib/mios/mios_comments.py, usr/libexec/mios/test_mios_manual.py, usr/share/mios/mios.toml, Justfile`
**Done When:** all 30 fixtures pass; aggregate counts over the real tree land within 3% of STAY ~5,000 / MIGRATE ~1,900 / DROP ~400 / READONLY 42; `test_mios_manual.py` runs inside `just drift-gate` beside `test_mios_docgen.py`.
**Why:** without a fixed classifier the ratchet has no comparable number and "we migrated some comments" is unmeasurable.
**Dep:** task_2222

## task_2225 -- Land `mios-manual` with the corpus ledger and retire generate-manual.py  (WS-DOCGEN | P1 | L)
**Goal:** D-4 One documentation CLI exists, it reads the repo, and its output is reproducible byte-for-byte between a developer and CI.
**What+How:** New `usr/libexec/mios/mios-manual` with six subcommands (`render`, `audit`, `coverage`, `harvest`, `prune`, `ledger`). Corpus = `mios_ai_tag.walk()` INTERSECT `git ls-files` -- adopt `generate-ai-manifest.py`'s `tracked_files()` determinism rule verbatim, since a filesystem walk makes the artifact a function of the developer's working directory and can then never match a clean CI regeneration. Emit `usr/share/mios/reference/manual-corpus.tsv` (path, span, lines, words, sha12, class, reason, as, stale, landed_doc, landed_anchor, landed_words, pruned), carrying `landed_*` forward by `sha12` so reformatting the surrounding code never loses a landing record, and retaining pruned rows as tombstones. Retire `tools/generate-manual.py`: it imports only `os/argparse/shutil`, performs zero repo reads, is 1311 lines of hardcoded literal, ships 21 of 43 links broken, and `shutil.rmtree`s `usr/share/doc/mios/manual/` unconditionally BEFORE and independent of `--output`. `mios-manual --out` must never delete a path it was not asked to write.
**Where:** `usr/libexec/mios/mios-manual, usr/share/mios/reference/manual-corpus.tsv, tools/generate-manual.py (delete), Justfile`
**Done When:** `mios-manual ledger --check` is green on a clean tree; two runs from different CWDs produce byte-identical output; `tools/generate-manual.py` and the `manual:` Justfile target are gone.
**Why:** the two surfaces in this repo that are actually documentation are the only two with no drift gate, which is exactly why the shipped manual has 21 dead links and 50 `file:///C:/MiOS/` paths inside a Linux OS's docs.
**Dep:** task_2224

## task_2226 -- Create the authored manual tree and gate manual.md with `render --check` (the real task_1289)  (WS-DOCGEN | P1 | L)
**Goal:** D-5 Regeneration can never destroy hand-written prose, and a stale manual fails the build.
**What+How:** Create `usr/share/doc/mios/manual/NN-*.md`, each with YAML front matter (`chapter`, `part`, `title`, `status`, `sources`, `harvest`). Migrate `generate-manual.py`'s 154 `{title, desc, content, citations}` records into those files as DATA -- they are genuine hand-written narrative no extractor can synthesize -- dropping the `credits.md#L39` line-number citations, the `file:///C:/MiOS/` URI scheme, the per-page boilerplate and the hardcoded "Seven Architectural Laws" list that contradicts `[laws]` (16 rows). The generator writes ONLY inside `<!-- MIOS-GEN:id --> ... <!-- /MIOS-GEN -->` marker pairs plus its own declared output list; prose outside markers is invisible to it. Add drift check 152 `check_manual_generated` (byte-diff of manual.md, the derived reference docs and every marker interior; plus zero `file://` and zero `C:[\\/]`), its `test_manual_generated` with BOTH phases -- a marker-interior edit must go red, an outside-the-marker edit must stay green -- and add `mios-manual render` as step 7 of `tools/sync-generated.sh`. Note `task_1289` is marked `[DONE]` in AGY-TASKS.md:2423 and is NOT done: there is no `--check` flag and no `check_manual_generated` anywhere.
**Where:** `usr/share/doc/mios/manual/, usr/share/doc/mios/manual.md, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, tools/sync-generated.sh, usr/share/mios/reference/drift-gate-index.tsv`
**Done When:** `render --check` is green; appending a line to manual.md turns gate 152 red; appending a paragraph OUTSIDE a marker in a chapter file leaves it green; `just sync` regenerates the manual.
**Why:** today regenerating the manual DELETES its H1 title while simultaneously fixing 20 dead links -- the committed file and its generator diverged in both directions, the signature of an ungated generator.
**Dep:** task_2225

## task_2227 -- Generate the five derived reference docs and the missing `/usr/share/doc/mios/` entry point  (WS-DOCGEN | P1 | M)
**Goal:** E-13/Law 8 Ports, laws, pipeline, verbs and units have exactly ONE generated answer, and a reader landing on a booted host has somewhere to start.
**What+How:** `mios-manual render` emits `reference/ports.md` (from `[ports.categories]` + `[ports]`), `reference/laws.md` (from `[laws]`, 16 rows, plus the 12 root exceptions), `reference/pipeline.md` (from `[build.pipeline]`), `reference/cli.md` (113 `[verbs.*]` + the `usr/libexec/mios/` backends), `reference/units.md` (from the UNIT FILES, not `mios.toml [units.*]` and never `globals.*`), and `usr/share/doc/mios/README.md` -- 130 baked doc files currently have NO entry point on a host. Every derived section ends with a `<!-- derived from ... -->` provenance line. These are the single answers the 1,155 stale port refs across 122 of 225 files can then be rewritten against; note the SSOT's OWN inline comments are stale too (`mios.toml:9438` says Open WebUI is at `:3033`, `:9439-9440` cites a port 9119 that exists nowhere), so a prose fix that trusts mios.toml comments re-imports the errors.
**Where:** `usr/libexec/mios/mios-manual, usr/share/doc/mios/reference/{ports,laws,pipeline,cli,units}.md, usr/share/doc/mios/README.md`
**Done When:** all six files are generated, gate 152 covers them, and every port/law/stage number in them equals the SSOT value.
**Why:** a reader cannot get a straight answer to "what port is X on", "how many laws are there" or "what does build step N do"; every doc that answers directly is wrong, and one agent-facing contract file states a law that does not exist.
**Dep:** task_2226

## task_2228 -- Add `check_doc_refs_resolve` and clear the ~70 verified stale references  (WS-DOCGEN | P1 | M)
**Goal:** D-6 A path, unit or `mios-*` name printed in a header or a generated doc is guaranteed to exist.
**What+How:** Drift check 153: `mios-manual audit --stale --json` over every `AI-hint:`/`AI-related:`/`AI-doc:` line and every generated doc; dangling count must be `<= [docs].max_stale_refs`. Dangling = absent from the whole-tree `RefIndex` (a token is not stale if it appears on any CODE line anywhere), absent from disk, and unmatched by `[docs].ref_allowlist` (runtime-created `/etc/ceph/ceph.conf`, `/etc/cdi/nvidia.yaml`, `/var/**`, `@@MIOS_*@@` template tokens). Then fix the ~70 verified stale refs, starting with the nine renumbered `automation/` stage refs -- three of which now resolve to a DIFFERENT script and are therefore actively misleading: `38-hermes-agent.sh` -> `72-` (38- is now selinux), `37-selinux.sh` -> `38-` (37- is now k3s-selinux), `19-k3s-selinux.sh` -> `37-`, plus `15-render-quadlets.sh` -> `34-`, `45-coderun-sandbox-build.sh` -> `54-bake-coderun-sandbox.sh`, `41-mios-dropin-fanout.sh` -> `48-`, `13-ceph-k3s.sh` -> `36-`, `08-system-files-overlay.sh` -> `01-`, `globals.generated.ps1` -> `globals.ps1`. Do NOT "fix" the 29 `ollama` and 12 `:8080` mentions -- they are history, upstream defaults being overridden, or container-internal ports behind a host remap; allowlist them.
**Where:** `automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, usr/share/mios/mios.toml, usr/share/doc/mios/adr/*, upstream/selinux.md, Containerfile, tools/render-ports.py`
**Done When:** `max_stale_refs = 0` and the gate is green; injecting `# AI-related: automation/99-nonexistent.sh` into a scratch file turns it red.
**Why:** 24 of the ~70 stale refs sit inside AI-hint/AI-related header lines, which never self-correct because `mios-ai-tag` reuses an existing hint verbatim forever -- so without a gate they are permanent.
**Dep:** task_2227

## task_2229 -- Harvest the 363 over-cap AI-hints into docs, then prune them behind the landing gate  (WS-DOCGEN | P1 | L)
**Goal:** D-7 The ~100k characters of prose currently hiding in the `AI-hint:` field live in documentation, and the tagger's own cap becomes safe to re-impose.
**What+How:** 363 hints (18.9%) exceed the tagger's 260-char `_clean()` cap and were therefore NOT written by `mios-ai-tag` -- they are hand-written mini-documents topping out at 2,837 chars (`mios_pipe/routing/agent_call.py`), 2,642 (`swarm.py`), 2,158 (`a2a.py`), 2,128 (`web_research.py`), 1,980 (`vision.py`), 1,947 (`mios_dispatch.py`). Run `mios-manual harvest --class=overlong-hint --apply` to write each into `usr/share/doc/mios/manual/_harvest/<slug>.md` annotated `<!-- mios-src: <path>:<a>-<b> sha=<sha12> -->`, filling the ledger's `landed_*` columns; SOURCE IS NOT TOUCHED in this step. Land drift check 154 `check_comment_landing` (pointer resolution + landing predicate for pruned rows + `landed_doc` may never be cleared). ONLY THEN run `prune --class=overlong-hint --only-landed --apply`, which replaces each hint with a `<=260`-char summary plus an `AI-doc:` row and REFUSES any block whose predicate is false. Finally ratchet `[ai_tag].hint_max_chars` back to 260 and `max_overlong_hints` to 0.
**Where:** `usr/libexec/mios/mios-manual, usr/share/doc/mios/manual/_harvest/, usr/share/mios/reference/manual-corpus.tsv, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, usr/share/mios/mios.toml`
**Done When:** harvest and prune are SEPARATE commits with `audit --deletions` green between them; a full `mios-ai-tag --no-llm` pass is byte-identical afterwards; gate 154 goes red if a harvested passage is gutted.
**Why:** this is the single largest data-loss hazard in the tree -- `existing_hint()` re-applies the 260-char cap on reuse, so the very next tagger run silently truncates all 363.
**Dep:** task_2228, task_2223

## task_2230 -- Land the comment ratchet and its monotone guard  (WS-DOCGEN | P1 | M)
**Goal:** D-8 Narrative comment volume can only go down, and "raise the ceiling" is a gate violation rather than a one-character fix.
**What+How:** Drift check 155 `check_comment_ratchet` asserts `unmigrated_narrative <= [docs].max_unmigrated_narrative`, `stale_refs <= [docs].max_stale_refs`, `overlong_hints <= [ai_tag].max_overlong_hints` and `undocumented_components <= [docs].max_undocumented_components`, resolving each ceiling with the layered pattern `mios-ai-hint-coverage` already uses (CLI > env > mios.toml > permissive report-only fallback). Drift check 156 `check_doc_ratchet_monotone` then asserts every one of those four ceilings is `<=` the value in `usr/share/mios/reference/doc-ratchet-floor.tsv`, which `mios-manual coverage --write-floor` can only ever LOWER (it writes `min(recorded, current)`). Both need negative tests: a 12-line 60-word narrative block added to a scratch `usr/lib/mios/*.py` must turn 155 red, and incrementing any ceiling by 1 must turn 156 red. Neither may be added to `[testing].negative_coverage_exempt` -- both are exercisable with a pure text edit on a Linux runner.
**Where:** `automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, usr/libexec/mios/mios-manual, usr/share/mios/reference/doc-ratchet-floor.tsv, usr/share/mios/mios.toml`
**Done When:** both gates are enforcing at the measured baseline, both RED edits have been observed red and reverted, and the ratchet number appears in `just drift-gate` output.
**Why:** gate 155 alone is unfalsifiable in practice -- this repo has a documented history of gates that cannot fail, and a ratchet whose ceiling is freely raisable is exactly one of them.
**Dep:** task_2225

## task_2231 -- Harvest the narrative mass: mios.toml, the two Windows entry points, and the agent-pipe tree  (WS-DOCGEN | P2 | XL)
**Goal:** D-9 The 1,764 narrative blocks / 16,481 lines / ~150k words of design prose living in comments become chapters and ADRs.
**What+How:** Harvest in yield order, one wave per commit, each followed by a gated prune. Wave A: `usr/share/mios/mios.toml` (36,159 narrative words / 4,050 lines), `build-mios.ps1` (23,791 / 2,975) and `Get-MiOS.ps1` (17,041 / 2,032) -- three files hold 55% of all narrative. Wave B: `usr/lib/mios/agent-pipe/**`, 14 of the top 25 files (`memory/pg.py`, `routing/dag_exec.py`, `mios_surface.py`, `scheduler/preempt.py`, `federation/a2a.py`, `routing/agentreg.py`, `server.py`, `mios_dispatch.py`, ...), assembled as ONE "agent-pipe architecture" chapter -- which is what those module docstrings collectively already are. Wave C: every `as=adr-candidate` block becomes a real ADR, starting with `.github/workflows/mios-ci.yml:282` (42 lines giving root cause, exact error text and operator confirmation for the rootful-podman GID-remap bake failure) and `mios.toml:5457` (90 lines, the crawl4ai container scrapped, citing Law 5). Wave D: systemd/Quadlet prologues into `reference/units.md` -- config formats are comment-DOMINATED (`.rules` 78%, `.conf` 64%, `.preset` 54%, `.service`/`.timer` 43%, `.toml` 39%) and are the real design-intent surface. Lower `[docs].max_unmigrated_narrative` and the floor after each wave.
**Where:** `usr/share/doc/mios/manual/, usr/share/doc/mios/adr/, usr/share/mios/mios.toml, build-mios.ps1, Get-MiOS.ps1, usr/lib/mios/agent-pipe/**`
**Done When:** each wave lands as harvest-commit then prune-commit with `audit --deletions` green between; `max_unmigrated_narrative` has fallen by the wave size; no source block was deleted without a ledger row proving its landing.
**Why:** this is the extraction prize -- ~150k words of decisions, rejected alternatives and incident history that today only an agent reading whole files can find.
**Dep:** task_2229, task_2230

## task_2232 -- Stop projecting AI-hint prose into globals.{sh,ps1} and gate it  (WS-DOCGEN | P2 | M)
**Goal:** Law 8 The generated resolvers carry VALUES, not documentation, and a unit's narrative has exactly one home.
**What+How:** `automation/lib/globals.sh` and `globals.ps1` each carry 129 `AI-hint:` blocks as `MIOS_UNITS_*_COMMENT` environment-variable VALUES -- 1.19 MB of generated shell/PowerShell that is mostly prose, and the fourth home of every unit sentence (unit file -> `mios.toml [units.*]`, 182 tables / 115 `comment=` keys -> globals.sh -> globals.ps1), which is why both resolvers show exactly 486 narrative lines. Teach `tools/render-globals.py` to skip the `comment` key, re-run `bash tools/sync-generated.sh`, and add drift check 157 `check_no_generated_prose_in_resolvers` asserting zero `AI-hint:` and zero `MIOS_UNITS_[A-Z0-9_]*_COMMENT=` in either file. THIS MUST LAND AFTER task_2231 wave D: the unit narrative it deletes must already be harvested from the unit files, or the projection removes the only copy some readers have.
**Where:** `tools/render-globals.py, automation/lib/globals.sh, automation/lib/globals.ps1, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh`
**Done When:** both resolvers shrink by ~1.1 MB combined, gate 157 is green, and appending one `MIOS_UNITS_TEST_COMMENT='# AI-hint: x'` line turns it red.
**Why:** one stale sentence about `hermes-agent.service` on `:8642` currently exists in four materially different file formats, and fixing the doc fixes none of the others.
**Dep:** task_2231

## task_2233 -- Retire the comment graveyard and the unified-knowledge dump  (WS-DOCGEN | P2 | M)
**Goal:** D-10 No documentation surface is built by scraping deleted comments or dumping whole files.
**What+How:** `usr/share/doc/mios/knowledge/recovered_comments.md` is 4.13 MB / 60,019 lines / 801 file sections -- 42% of the entire markdown corpus -- and `tools/lib/extract_comments.py:29-34` builds it from git diffs by harvesting ONLY DELETED comment lines: a graveyard, stale by construction, whose first entry is already contradicted by `automation/lib/globals.sh:170`. Run `mios-manual audit --corpus-extra` over it ONCE as a rescue pass, harvest anything classified MIGRATE whose target still resolves, then delete both the file and the extractor. Separately retire `tools/generate-unified-knowledge.py`: it walks CWD not `--root`, has no CLI and no `--check`, its category taxonomy keys off `specs/core`/`specs/memory`/`evals/` (only `specs/engineering` exists, so nearly everything lands in "other" and never enters the semantic index), its extension filter is inverted so any dotfile sweeps in, and its three-regex redaction misses `sk-proj-*`, `ghp_*`, PEM and JWT while the output is copied into agent-visible scratch. Replace the RAG-seeding use with `mios-manual audit --json`.
**Where:** `usr/share/doc/mios/knowledge/recovered_comments.md (delete), tools/lib/extract_comments.py (delete), tools/generate-unified-knowledge.py (delete), automation/ai-bootstrap.sh, tools/sync-wiki.py`
**Done When:** all three are gone, `ai-bootstrap.sh` and `sync-wiki.py` consume the audit JSON, and the markdown corpus drops from 9.99 MB to ~5.9 MB.
**Why:** 42% of the doc corpus by bytes is mechanically-transcribed deleted comments shipped into `/usr/share/doc/`, and one commit has already re-duplicated part of it back into manual.md.
**Dep:** task_2231

## task_2234 -- Collapse the 69-copy boilerplate, the 3 duplicate doc pairs, and mios-codebase-index's rival taggability  (WS-DOCGEN | P2 | M)
**Goal:** D-11 Every fact has one home; the codebase is quickly auditable because there is nothing to cross-check.
**What+How:** (1) The two-paragraph "MiOS is one thing built two ways at once ... `bootc upgrade` it like a `git pull` ... `bootc rollback` it like a Ctrl-Z" block appears in 69 files (~48 KB) and every copy drifts independently -- some name GNOME 50, some name ports, some name lanes. Replace all 69 with `<!-- MIOS-GEN:boilerplate:what-mios-is -->` rendered from the single authored chapter. (2) De-duplicate the three near-verbatim doc pairs (~116 KB, already diverging on facts): `docs/design/doc-mios-metal.md` <-> `concepts/mios-metal-architecture.md`, `doc-container-runtime.md` <-> `concepts/container-os-runtime.md`, `doc-foss-upstream.md` <-> `concepts/foss-upstream-map.md`; add `docs/design/README.md` marking that tree process-only, the pattern `usr/share/doc/mios/archive/` already models. (3) Delete `mios-codebase-index`'s private `EXT`/`BASENAMES`/`SKIP_DIR`/`SKIP_SUFFIX` -- a second, already-diverged definition of the corpus that includes `.json` (which mios-ai-tag explicitly skips as comment-less) -- and import `mios-ai-tag` through the same `SourceFileLoader` shim `mios-ai-hint-coverage` uses.
**Where:** `usr/share/doc/mios/**, docs/design/**, usr/libexec/mios/mios-codebase-index, usr/share/mios/**`
**Done When:** the boilerplate exists once; the three pairs are one file each with a pointer; `mios-codebase-index` defines zero taggability constants of its own and its file count matches `mios-ai-hint-coverage`'s denominator.
**Why:** "fewer, more feature-complete components" is unachievable while three tools disagree about which files exist and one paragraph has 69 independently-drifting copies.
**Dep:** task_2233

## task_2235 -- Port the comment lexer hot path to Rust behind the unchanged mios-manual CLI  (WS-DOCGEN | P3 | L)
**Goal:** Law 14 The whole-tree lexing pass runs in the native tier without creating a second definition of the corpus.
**What+How:** `lex()` over 2,142 files is the only compute-bound part of the system; classification, rendering and the ledger stay in Python because they are policy and because taggability must remain defined exactly once, in `mios-ai-tag`, which four tools import. Add a `mios-comment-lex` crate under `tools/native/` alongside the existing `mios-ssot-walk`/`mios-ssot-lint` crates, emitting the same `Block` JSON records. `mios_comments.lex()` becomes a two-branch dispatcher: call the binary when present, fall back to the Python lexer otherwise, and add a differential gate asserting the two produce identical `sha12` sets over the tree -- the same strangler-fig shape `check_resolver_shell_equivalence` already uses for `mios-resolver`. Only start after task_2224's 30 fixtures are frozen, so the Rust implementation is validated against a fixed oracle rather than a moving one.
**Where:** `tools/native/mios-comment-lex/, tools/native/Cargo.toml, usr/lib/mios/mios_comments.py, automation/98-drift-checks.sh`
**Done When:** the differential gate is green, the binary is optional (a bare checkout still works), and full-tree `mios-manual audit` runtime drops below 5s.
**Why:** Law 14 puts native tooling in Rust, but porting the classifier or the taggability rules would fork the corpus definition -- the lexer is the one piece that can move without that cost.
**Dep:** task_2224, task_2230
```

---

## 7. Auditability summary

After M8 the documentation system is five files a reviewer can read end to end:

| File | Lines (target) | Answers |
|---|---|---|
| `usr/libexec/mios/mios-ai-tag` | ~420 | which files exist, what a header is, how it is written |
| `usr/lib/mios/mios_comments.py` | ~450 | what a comment block is, and which of five classes it belongs to |
| `usr/libexec/mios/mios-manual` | ~700 | how docs are rendered, measured, harvested and pruned |
| `usr/libexec/mios/test_mios_manual.py` | ~350 | the 30 cases that pin the classifier |
| `usr/share/mios/mios.toml [docs]` | ~50 | every threshold, ceiling, blocklist and regex |

Retired in the same programme: `tools/generate-manual.py` (1,311 ln),
`tools/generate-unified-knowledge.py`, `tools/lib/extract_comments.py`,
`usr/share/doc/mios/knowledge/recovered_comments.md` (4.13 MB), the `manual:` Justfile
target, `mios-codebase-index`'s duplicate constants, and ~1.1 MB of prose embedded in
`globals.{sh,ps1}`.

Net: **-1.2 MB of generated code, -4.1 MB of dead markdown, -1,311 lines of hardcoded
"generator", +6 gates that have each been observed red.**
