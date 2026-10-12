<!-- AI-hint: MiOS architectural documentation: Anti-Fabrication Gaps — Root-Cause + Fix-Design (LANE A).
     AI-related:  -->

# Anti-Fabrication Gaps — Root-Cause + Fix-Design (LANE A)

> Research/design deliverable. **No `.py` changed** — this doc specifies the
> fixes with real `file:line` anchors so LANE B can implement them. Two live-
> falsified P0 fabrications (task_114 / FAB-01 and task_115 / FAB-02) escape the
> current native-loop guards. Both guards are pattern-of-omission checks; both
> real attacks are patterns-of-**commission** the checks don't model.

---

## 0. The two live falsifications (ground truth)

| ID | Turn | Real tool state | What shipped in the FINAL answer | Why the guard missed |
|----|------|-----------------|----------------------------------|----------------------|
| **FAB-01** | task_114 "what games are installed?" | `apps` verb **fired**, returned Linux flatpaks (ptyxis, flatseal, DOSBox…) | A *second* `🤝 apps output (truncated for brevity):` block with **invented** games (FakeGame 5, Sea of Thieves, Cyberpunk 2077, Minecraft, Zelda TotK) each `"version":"1.0.0"` | Guard strips a `🤝 <verb> output` block only when `<verb> ∉ _fired`. Here `apps` **was** fired, so the fabricated *duplicate* survived. |
| **FAB-02** | task_115 "2024 games" | Real Wikipedia/Polygon fetched | Real data **+** a fabricated "Major 2025 Announcements" section (Starfield DLC, FIFA 26, GoW Ragnarök expansion, Minecraft 1.21), an admission "*widely reported but not captured in the excerpt*", and an IGN citation never fetched | Guard fires only when **zero** sources were fetched **and** a markdown table exists. Here sources *were* fetched (so `_real_norm` non-empty) and the invented section was prose/bullets citing an outlet **by name** (no off-list `http://` URL) → both fire-conditions false. |

The shared root cause: **both guards key on the wrong axis.** FAB-01's guard
keys on *verb membership* (`fired` vs not) when the real signal is *content
provenance* (did the executor produce this block, or did the model author it?).
FAB-02's guard keys on *source count == 0* when the real signal is *per-claim
grounding* (is each named entity present in fetched text?).

---

## 1. End-to-end trace of a `@` (agent-pipe) non-chat turn

Path for a non-chat (`decision.mode == "agent"`) turn that lands in the native
loop. All anchors are current `file:line`.

1. **Refine + route.** `chat.py` runs refine; a short non-web turn can
   short-circuit to a chat reply (`chat.py:1795-1836`), guarded by the *sibling*
   anti-fab predicate `_contains_tool_result_block` (`chat.py:1810`, predicate
   `chat.py:1848-1860`, flag `chat.py:1845`). A games/research turn does **not**
   short-circuit → `decision.mode = "agent"` (`chat.py:1835`) → dispatcher →
   native loop.

2. **`_respond_native_loop_direct`** (`native_loop.py:183`). Deterministic
   fast-paths for remember (`:198-227`) and identity (`:234-260`) are skipped for
   a games/research ask. System prompt assembled (`:261-347`); recall injected
   (`:357-424`); tool surface built (`:425-488`).

3. **Streaming shell** (`:517-595`) re-invokes the same function
   `streaming=False, emit=_q.put_nowait` (`:526-530`) so the loop body runs
   **once**; the queue drains reasoning + live answer tokens to the wire.

4. **Prefetch grounding** inside the `httpx` client (`:598-772`): local-state
   (`:628-637`), **web_search** (`:645-690`, captures real source URLs into the
   turn collector via `_src_record` at `:686`), compute (`:700-739`), file-search
   (`:748-772`). The web results text is `_wtext` (`:659`), injected as a
   system message (`:664-672`).

5. **Secondary tool loop** `_v1_secondary_tool_loop(...)` (`:773-775`) →
   `secondary_loop.py:239`. It calls `_exec_tool_calls` (`secondary_loop.py:392`),
   `msgs.extend(_tmsgs)` (`:393`), and **returns `msgs`** (`secondary_loop.py:458`).
   That return value is `_m2` (`native_loop.py:773`). **`_m2` therefore contains
   one `role:"tool"` message per executed verb**, each carrying
   `{"name": vname, "content": <REAL capped output>}` (built in
   `toolexec.py:413-417` + `:732`). **This is the in-scope ground truth for the
   real captured `apps` output.**

6. **Final synthesis completion** (`:780-821`): posts `_m2` back to the heavy
   backend; `_raw` = the model's synthesized answer (streamed via `emit`,
   `:807-811`). Heavy-empty → light-lane failover (`:829-841`).

7. **`_fired` construction** (`:842-846`): iterate `_m2`, collect every
   `tool_calls[].function.name` actually dispatched. `_fired` is a *name list*,
   with **no linkage to the real per-verb output** — this is why the FAB-01 guard
   can only ask "was the name fired?", never "does this block match what the verb
   returned?".

8. **`<think>` strip + empty-recovery** (`:851-854`); `_ans = _raw` (`:855`).

9. **Polish** (`:858-867`) → `_ans = _p`.

10. **Relay ladder** (`:868-917`) when `_ans` empty:
    - (1) raw synthesis (`:873-875`) — model text.
    - (2) **raw tool evidence** (`:877-885`): `_ans = "\n\n".join(_snips)` where
      `_snips` = `role:"tool"` message **content** (`:879-882`). **This is the only
      path where real executor evidence legitimately *becomes* `_ans`.** Note the
      content is `tmsg["content"]` — the raw JSON, **without** the `🤝` sentinel
      (the sentinel is only added by `push`, see §2).
    - (3b) injected-recall surface (`:898-914`) — memory text.

11. **ANTI-FABRICATED-EXECUTION guard** (`:918-937`) — the **FAB-01 gap**
    (detailed §3).

12. **ANTI-FABRICATED-CITATION guard** (`:938-966`) — the **FAB-02 gap**
    (detailed §4).

13. **Sources** append (`:967-990`) + `_store_knowledge` (`:991-995`) + final
    emission (`:998-1019`).

### 1a. Where the `🤝 <verb> output:` sentinel actually comes from

The sentinel is emitted **only** by the executor's `push(...)` callback:
`toolexec.py:454, 493, 523, 566, 651, 733` (e.g. `push(f"\n\n🤝 {vname}
output:\n{tmsg['content']}\n")` at `:733`). In the native loop `push == _push`,
which routes to the **reasoning / emit stream**, never into `_ans`:

- `native_loop.py:608` (debug) / `:610` (normal): `_push = lambda s: emit({"reasoning": str(s)})`.
- The dag path is identical — `dag_exec.py:1377` wraps its `🤝` block in
  `_sse_reasoning(...)`, i.e. the think stream, sanitized.

**Consequence (the load-bearing fact for the fix):** on *every* legitimate
native-loop path the `🤝 <verb> output:` sentinel goes to the reasoning
dropdown; it is **never** concatenated into the model-synthesized answer, and the
raw-evidence fallback (`:877-885`) surfaces tool **content only** (no sentinel).
Therefore **any `🤝 <verb> output:` substring appearing in `_ans` is
model-authored** — it is the model *reprinting the executor's evidence format*,
which is exactly FAB-01. This is what makes "strip ALL sentinel blocks from a
synthesized answer" safe rather than lossy.

The **one** caveat is the raw success-JSON form `{"success":true,"tool":"…"}`:
that *can* appear legitimately in `_ans`, but **only** via the raw-evidence
fallback (`:877-885`) where a real tool result is surfaced verbatim. So the JSON
form — unlike the `🤝` sentinel — needs a provenance guard, not a blanket strip.

---

## 2. FAILURE 1 fix — structural, provenance-based (not verb-membership)

### 2.1 Current guard (the gap)

`native_loop.py:923-937`:

```
_fired_set = {str(_f) for _f in (_fired or [])}
def _drop_unfired(_m):
    _blk = _m.group(0)
    _mv = re.search(r'🤝\s+(\S+)\s+output|"tool"\s*:\s*"([^"]+)"', _blk)
    _vn = ((_mv.group(1) or _mv.group(2)) if _mv else "") or ""
    return "" if (_vn and _vn not in _fired_set) else _blk       # <-- keeps FIRED-verb blocks
_san = re.sub(r'🤝[^\n]*output\s*:.*?(?=\n\n|\Z)', _drop_unfired, _ans, flags=re.DOTALL)
_san = re.sub(r'\{[^{}]*"success"\s*:\s*true[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', _drop_unfired, _san, flags=re.DOTALL)
```

The `return "" if (_vn and _vn not in _fired_set) else _blk` line is the bug: a
fabricated block for `apps` (which **was** fired) hits the `else _blk` branch and
survives. Verb-membership is the wrong axis.

### 2.2 Options evaluated

**(a) Strip ALL executor-sentinel blocks from a model-*synthesized* answer,
unconditionally.** Rationale: the model's job is to synthesize **prose** from
tool results; it must **never** reprint the executor's `🤝 <verb> output:`
evidence format — the real evidence is already streamed separately to the
reasoning pane (§1a). Per §1a the sentinel never legitimately reaches a
synthesized `_ans`, so stripping every sentinel block is *lossless* for real
answers and *total* against FAB-01 (fired-or-not is irrelevant — a duplicate
fabricated `apps` block is stripped because it's a sentinel block in synthesized
prose). **This also subsumes the low-severity skill/recipe false-positive the
safety pass found**: with no verb-name matching, `🤝 skill:foo output:` /
`🤝 recipe:bar output:` are treated identically — a synthesized answer should
carry none of them.
 - *Distinguishing "synthesized" vs "raw-evidence surfaced":* introduce a
   boolean `_surfaced_raw_evidence`, set `True` at the exact point `_ans` is
   assigned from `_snips` (`native_loop.py:885`). When that flag is set, `_ans`
   **is** the executor's evidence and must be preserved verbatim; the strip is
   skipped. On every other path `_ans` is model-authored → strip applies. (The
   `🤝` sentinel is actually absent even from `_snips`, so the flag matters
   strictly for the success-JSON form — see (b) — but tracking it makes the
   provenance explicit and keeps the guard correct if the evidence format ever
   changes.)

**(b) Cross-check each claimed block against the REAL captured output.** The pipe
holds the real per-verb output in `_m2` (§1 step 5). Build
`_real_out = {str(_mm.get("name")): str(_mm.get("content") or "") for _mm in _m2
if isinstance(_mm, dict) and _mm.get("role") == "tool"}`. For any `🤝 <verb>
output:` block the model emitted, compare its payload to `_real_out[verb]`;
mismatch (the model's "output" is not a substring/normalized-match of the real
one) ⇒ fabricated ⇒ strip + honest note. This catches FAB-01 directly (invented
games ≠ real flatpak list) and is the *only* way to police the success-JSON form
inside the raw-evidence path. Cost: payload-normalization fuzz (truncation
markers like "(truncated for brevity)", whitespace, ordering) → more code, some
false-negative risk if the model paraphrases lightly.

**(c) Combination (RECOMMENDED).** Minimal robust fix = **(a) as the primary
gate** for the `🤝` sentinel form (it is total and lossless), **plus (b) as
defense-in-depth for the success-JSON form** in the preserved raw-evidence path.
Concretely:
  1. `🤝 <verb> output:` blocks → **strip-all** when `not
     _surfaced_raw_evidence` (option a).
  2. `{"success":true,"tool":…}` JSON → strip when `not _surfaced_raw_evidence`;
     when raw-evidence *is* surfaced, keep only if it byte-matches an entry in
     `_real_out` (option b), else strip.

### 2.3 Exact change

**File:** `usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py`

1. **Flag the raw-evidence provenance.** At the raw-evidence assignment
   (`:885`, `_ans = "\n\n".join(_snips).strip()`), also set
   `_surfaced_raw_evidence = True`. Initialize `_surfaced_raw_evidence = False`
   next to `_ans = _raw` (`:855`).

2. **Replace the guard body** (`:923-937`) with strip-all + provenance:

```python
if _ans and _ANTIFAB_ENABLE and not _surfaced_raw_evidence:   # synthesized answer only
    try:
        # (a) A synthesized answer must be PROSE, never a reprint of the
        # executor's '🤝 <verb> output:' evidence block — the real evidence is
        # streamed to the reasoning pane. Any such block here is model-authored
        # (FAB-01: a duplicate fabricated 'apps' block for an already-fired verb).
        _san = re.sub(r'🤝[^\n]*output\s*:.*?(?=\n\n|\Z)', "", _ans, flags=re.DOTALL)
        # success-claim JSON asserting a tool ran (fabricated pid/handle/rows)
        _san = re.sub(r'\{[^{}]*"success"\s*:\s*true[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', "", _san, flags=re.DOTALL)
        if _san != _ans:
            log.warning("native-loop: stripped executor-evidence block(s) from synthesized answer (anti-fab)")
            _ans = _san.strip()
    except Exception:  # noqa: BLE001 -- degrade-open
        pass
# (b) raw-evidence path: keep a success-JSON block ONLY if it matches real output
elif _ans and _ANTIFAB_ENABLE and _surfaced_raw_evidence:
    try:
        _real_out = {str(_mm.get("name")): str(_mm.get("content") or "")
                     for _mm in _m2 if isinstance(_mm, dict) and _mm.get("role") == "tool"}
        def _keep_if_real(_m):
            _blk = _m.group(0)
            _mv = re.search(r'"tool"\s*:\s*"([^"]+)"', _blk)
            _vn = _mv.group(1) if _mv else ""
            return _blk if (_vn and _blk.strip() in _real_out.get(_vn, "")) else ""
        _san = re.sub(r'\{[^{}]*"success"\s*:\s*true[^{}]*"tool"\s*:\s*"[^"]+"[^{}]*\}', _keep_if_real, _ans, flags=re.DOTALL)
        if _san != _ans:
            log.warning("native-loop: dropped success-JSON block not matching captured tool output (anti-fab)")
            _ans = _san.strip()
    except Exception:  # noqa: BLE001 -- degrade-open
        pass
```

If the strip empties `_ans`, fall through to the existing honest-note / sources
surface (append a one-line "*(some tool output could not be verified and was
omitted)*" before the Sources block, or reuse the citation guard's honest note
style at `:962-964`). Degrade-open everywhere.

### 2.4 Flag-gating

The native-loop guard is currently hard-coded "always-on" (`:923` comment) and
does **not** read the SSOT flag, unlike the chat sibling. Wire it to the same
gate: `_ANTIFAB_ENABLE`, sourced from `MIOS_VERITY_ANTIFAB_ENABLE` (bridged from
`[verity].antifab_enable`, `mios.toml:2459`; env plumbing
`system-sync-env.sh:166-170`). Inject `_ANTIFAB_ENABLE` into `native_loop` via
`configure()` (add to `_INJECTED`, `:145-167`) or read the env at module load
exactly as `chat.py:1845` does. Default **true** → degrade-open (unset/false =
pre-guard passthrough).

---

## 3. FAILURE 2 fix — per-entity/claim grounding (not source-count)

### 3.1 Current guard (the gap)

`native_loop.py:947-966`:

```python
if _routed_domain_var.get(None) == "web":
    _real_norm = {re.sub(r"[/\s.]+$", "", str(_s.get("url") or "")) for _s in (_src_collected() or []) ...}
    _ans_urls = re.findall(r"https?://[^\s)\]\"'<>]+", _ans or "")
    _fab = [_u for _u in _ans_urls if re.sub(r"[/\s.]+$", "", _u) not in _real_norm]
    _has_report_table = bool(re.search(r"(?m)^\s*\|.*\|.*\|", _ans or ""))
    if (_fab and _real_norm) or (not _real_norm and _has_report_table):
        _ans = "I couldn't extract a specific, verified story ..."
```

Two blind spots: (1) fabrication is only caught if it carries an **off-list
`http://` URL** — FAB-02 cited IGN **by name**, no URL → `_fab` empty; (2) the
table branch only arms when `not _real_norm` (zero sources) — FAB-02 fetched
real sources, so the branch is dead. Net: partial fabrication (real sources +
invented rows/entities) is invisible. It also nukes the **whole** answer when it
does fire, discarding the real half.

### 3.2 Design — structural entity grounding (unicode/lang-neutral, Law 7)

Ground each *section* of the answer against the *actually-fetched text*, at the
entity granularity, with **no hardcoded English keyword list**.

**Build the fetched corpus** (ground truth for this turn), degrade-open to `""`:
- the injected web results text `_wtext` (`:659`) — hoist it to a
  function-scope `_fetched_corpus` initialized `""` at `:621` and appended when
  the web prefetch runs (`:662`);
- every `role:"tool"` message content in `_m2` for a web-enrich verb
  (`toolexec.py:60` `_WEB_ENRICH_VERBS = {"web_search","web_extract","crawl"}`);
- the real source titles/URLs from `_src_collected()` (`:979` / `web_research.py:1172`).

**Split the answer into sections** structurally: on blank lines and markdown
headings/table-row boundaries (`re.split(r"\n\s*\n|(?m)^\s*#{1,6}\s")`). No
language assumptions.

**Extract candidate entities per section** — structural, unicode-aware:
- titlecase/uppercase runs: tokens where `tok[:1].isupper()` (unicode `Lu`) or
  `tok.istitle()`, optionally joined multi-word (`Sea of Thieves`);
- digit-bearing tokens / 4-digit years / dates (`\d`);
- URLs and bare registrable domains (`\b[\w-]+\.[a-z]{2,}\b`).
This is unicode property-based, **not** an English word list. For a script with
no case distinction (CJK) the titlecase test yields nothing → the section has
too few entity tokens → **degrade-open** (skip; never strip).

**Ground each section:** `grounded = { e for e in entities if
_norm(e) in _norm(_fetched_corpus) }` (casefold + strip punctuation; substring
match against the corpus). A section is **fabricated** when it has
`>= MIN_ENTITIES` entity tokens (e.g. 3) **and** its grounded fraction is below
`GROUND_MIN` (e.g. `< 0.34` — nearly all its named entities are absent from
*every* fetched source). Strip **only that section**, not the whole answer, and
append an honest note that some content couldn't be verified. Keep the grounded
sections + the real Sources block (`:984-990`).

**Gate** on the same signal as the web prefetch, so news turns that don't set
`domain==web` are also covered: `refined.get("web") or refined.get("news") or
_routed_domain_var.get(None) == "web"`. Requires a non-empty `_fetched_corpus`
(else degrade-open — can't ground, keep answer). Flag-gated on `_ANTIFAB_ENABLE`
(§2.4). Thresholds `MIN_ENTITIES` / `GROUND_MIN` from `[verity]` SSOT, **not**
literals (Law 7).

**Secondary tell (weak, do not gate on it):** an admission like "*widely
reported but not captured in the excerpt*" is a self-declaration that a claim is
**not** from the fetched sources. It is a useful log signal, but it is an English
phrase → per Law 7 it must **not** gate the decision. Rely on structural
grounding; optionally *raise* confidence (lower the strip threshold for that one
section) when the structural check *already* flagged it, never introduce it as an
independent keyword trigger.

### 3.3 Exact change

**File:** `usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py`

- Hoist `_fetched_corpus = ""` beside `_refs` (`:621`); append `_wtext` when the
  web prefetch injects it (`:662`).
- **Extend** the existing URL check (`:947-966`) rather than replace: after the
  URL-based `_fab` block, add the section-level entity-grounding pass described
  in §3.2. Because it strips *sections* (not the whole answer) it composes with
  the existing whole-answer replacement (which stays for the zero-source case).
- Put the entity extractor + `_norm` helpers as module-level functions
  (`_entity_tokens(text)`, `_norm(s)`) near the guard, unicode-aware, so they're
  unit-testable in isolation.

The generalized URL sub-check (name-only citation) is handled implicitly: an
outlet named "IGN" with no `http` URL becomes an entity token in its section; if
"IGN" (and the invented titles around it) are absent from `_fetched_corpus`, the
section is stripped.

---

## 4. Tests to add

Reuse the offline-stub recipe from `test_mios_antifab.py` (no network/DB/image).

1. **`test_mios_native_antifab_synth.py` (FAB-01).** Feed a synthesized `_ans`
   containing a real `🤝 apps output:` (flatpaks) **followed by** a fabricated
   duplicate `🤝 apps output (truncated for brevity):` with invented games, with
   `_fired = ["apps"]` and `_surfaced_raw_evidence = False`. Assert the strip
   removes **both** sentinel blocks from the synthesized answer (prose survives).
   Second case: `_surfaced_raw_evidence = True` with a `{"success":true,"tool":
   "open_app",…}` block that **matches** a `_m2` tool message → preserved; a
   non-matching one → stripped. Third case: skill/recipe sentinel
   (`🤝 skill:foo output:`) in synthesized prose → stripped (subsumes the
   false-positive).

2. **`test_mios_native_antifab_grounding.py` (FAB-02).** `_fetched_corpus` =
   Wikipedia/Polygon 2024 text. `_ans` = real 2024 section + fabricated
   "Major 2025 Announcements" (Starfield DLC, FIFA 26, …) citing IGN by name.
   Assert the fabricated **section** is stripped and the real section + Sources
   survive. Negative cases: an all-grounded answer is untouched; a CJK / caseless
   answer degrades-open (untouched); empty `_fetched_corpus` degrades-open.

3. Extend `test_mios_antifab.py` to assert the native-loop guard honors
   `MIOS_VERITY_ANTIFAB_ENABLE=false` (passthrough).

---

## 5. Summary of recommended fixes (anchors)

| # | Fix | File:line | Gate / degrade |
|---|-----|-----------|----------------|
| F1 | Replace verb-membership strip with **strip-all executor-sentinel blocks from a *synthesized* answer** + provenance flag; success-JSON kept in the raw-evidence path only if it matches real `_m2` output | `native_loop.py:855` (add `_surfaced_raw_evidence=False`), `:885` (set `True`), `:923-937` (rewrite) | `_ANTIFAB_ENABLE`; degrade-open |
| F1-gate | Wire native-loop guard to SSOT flag (currently hard "always-on") | inject/read `_ANTIFAB_ENABLE` (`native_loop.py:145-167` / like `chat.py:1845`); SSOT `mios.toml:2459`, bridge `system-sync-env.sh:166-170` | default true |
| F2 | Add **per-section entity/claim grounding** against `_fetched_corpus`; strip only the fabricated section (keep real half + Sources) | `native_loop.py:621` (`_fetched_corpus`), `:662` (append `_wtext`), `:947-966` (extend guard) | same gate; degrade-open when corpus empty / caseless / too few entities |
| Ground truth used | real per-verb output = `role:"tool"` msgs in `_m2` (`secondary_loop.py:458`, built `toolexec.py:413-417,:732`); real sources = `_src_collected()` (`web_research.py:1172`) | — | — |

**Key structural insight (justifies F1 being lossless):** the
`🤝 <verb> output:` sentinel is emitted *only* by the executor's `push` into the
**reasoning stream** (`toolexec.py:733`, routed at `native_loop.py:610`); it is
never concatenated into a synthesized `_ans`, and the raw-evidence fallback
(`native_loop.py:877-885`) surfaces tool **content without** the sentinel.
Therefore any sentinel block in a synthesized answer is, by construction,
model-authored fabrication — safe to strip in full, which retires the
verb-membership whack-a-mole and the skill/recipe false-positive at once.
