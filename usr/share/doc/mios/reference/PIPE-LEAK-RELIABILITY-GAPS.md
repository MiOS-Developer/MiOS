<!-- AI-hint: MiOS architectural documentation: Pipe Leak & Reliability Gaps — root-cause + fix design.
     AI-related: mios-llm-light, mios-reasoning-ok, mios-web-search, mios-apps, mios-win-scan, localhost:8450 -->

# Pipe Leak & Reliability Gaps — root-cause + fix design

> Research/design doc only — no code changed. Companion to
> [`ANTIFAB-FABRICATION-GAPS.md`](ANTIFAB-FABRICATION-GAPS.md) (owned by a
> separate lane; this doc explicitly does NOT touch `chat.py`/`native_loop.py`
> fabrication-guard logic). Scope: four **non-fabrication** failures from a live
> `@` (agent-pipe) session — scaffold/tool-syntax leaking into the visible
> answer, and three reliability gaps (query-expansion parse failure, an opaque
> generic tool-error wrapper, and an incomplete app inventory). Every claim
> below is grounded in a real `file:line` read during this pass.

---

## LEAK-1 (CQ2 / tracked as task_112) — literal `<tool_call>`/```json``` text leaks in the final answer

### Root cause

The **secondary tool-loop** (`_v1_secondary_tool_loop`, called at
`usr/lib/mios/agent-pipe/mios_pipe/routing/native_loop.py:773-775`) correctly
binds `tools` and, on a narrated-instead-of-called response, **rescues and
strips** the narration:

```
secondary_loop.py:334-344   tcs = msg.get("tool_calls") or []
                             if not tcs:
                                 _rescued = _rescue_tool_calls(msg.get("content") or "", tools)
                                 ...
                                 _c = re.sub(r"<tool_call>.*?</tool_call>", "", _c, ...)
                                 _c = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", _c, ...)
                                 _c = re.sub(r"<function=.*?</function>", "", _c, ...)
```//
(identical rescue+strip block repeated at `secondary_loop.py:503-515` for the
ollama variant.)

But the loop's return value (`_m2`) is then fed into a **second, separate**
completion request — the answer-shaping / final-synthesis call — built at
`native_loop.py:780-782`:

```python
780   _pb = {"model": BACKEND_MODEL, "messages": _m2, "stream": False,
781          "parallel_tool_calls": _endpoint_supports_parallel_tools(BACKEND),
782          "chat_template_kwargs": {"enable_thinking": False}}
```

This payload has **no `tools` key, no `tool_choice`, and no `stop` sequences**.
It is streamed/POSTed at `native_loop.py:787-821` and its raw text becomes
`_raw` → `_ans` with only a `<think>` strip applied (`native_loop.py:847-854`)
— **no** `<tool_call>`/```json``` fence removal exists anywhere on this path.
So when the model (still primed by the same system/tool context) emits
residual tool intent, it has nowhere to put it but literal text in
`delta.content` (`native_loop.py:805-810`), and nothing strips it before it
reaches the user. `toolexec.py:184-190` shows the *only* place the
`<tool_call>{json}</tool_call>` regex is compiled
(`_RESCUE_TOOL_CALL_RE`) — it is a **rescue-into-execution** primitive for the
tools-bound loop, not a sanitizer for a tools-unbound completion, so it's
structurally the wrong tool for this call site even if it were wired in.

This is already tracked verbatim in `TASKS.md:2611-2623` (task_112/CQ2), which
independently names the same `native_loop.py:780-782` line and the same
"tools-on-final" + "constrained decoding" fix shape — corroborating this
root-cause read.

### Is constrained decoding / grammar available on this lane?

Yes. `BACKEND`/`BACKEND_MODEL` for the native loop is the same llama.cpp-family
OpenAI-compat lane (`mios-llm-light` / heavy lanes) that **already** uses
`response_format: {"type": "json_schema", ...}` + `chat_template_kwargs:
{"enable_thinking": false}` successfully elsewhere in this same codebase:

- `classify.py:105, 175` — `_route_domain` / label classifier, enum-constrained.
- `chat.py:600` — a yes/no constrained completion.
- `planner.py:400`, `dci.py:329,663`, `reflect.py:321` — `json_object`/`json_schema`.
- `refine.py:749-786` — the refine envelope itself (non-streaming path).

Comments at `classify.py:99-100,160-161` and `fanout.py:273-274` document a
known llama.cpp caveat (**llama.cpp #20345: the grammar is silently dropped
when `enable_thinking` is on** — so `enable_thinking:false` must accompany any
`response_format`). The final-shaping call already sets
`enable_thinking:false` (`native_loop.py:782`) but never adds
`response_format`/`tools` — so grammar-backed decoding is proven available on
this exact lane, just not applied to this call site.

### Design fix (flag-gated, degrade-open)

**(a) Bind stop-sequences + tools on the final completion.**
Add `"tools": _tools` (already in scope at `native_loop.py:773`) and
`"tool_choice": "none"` (this is an *answer-shaping* pass — tools should not
fire again, but their schema being present measurably suppresses a model's
urge to hand-roll tool syntax as prose) to `_pb` at `native_loop.py:780-782`.
Additionally add a `"stop"` array covering the literal markers
(`"<tool_call>"`, `` "```json" ``, `"<function="`) — cheap, engine-native, and
already proven safe: llama.cpp/vLLM/SGLang all support `stop` on `/v1/chat/completions`;
gate behind `MIOS_FINAL_STOP_MARKERS` (default on) so a future model whose
*correct* answer legitimately starts a code fence isn't truncated — degrade to
"no stop list" if the SSOT capability probe says the backend doesn't honor
`stop`.

**(b) Post-strip as a second, independent layer (never rely on (a) alone).**
Immediately after the `<think>` strip at `native_loop.py:851-854`, reuse the
*exact* three regexes already proven in `secondary_loop.py:341-343` /
`512-514` (do not re-derive new ones — same behavior, same false-positive
profile) against `_raw`/`_ans`:

```python
_ans = re.sub(r"<tool_call>.*?</tool_call>", "", _ans, flags=re.DOTALL | re.IGNORECASE)
_ans = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", _ans, flags=re.DOTALL | re.IGNORECASE)
_ans = re.sub(r"<function=.*?</function>", "", _ans, flags=re.DOTALL | re.IGNORECASE)
```

Flag `MIOS_FINAL_TOOLSYNTAX_STRIP` (default on); degrade-open (regex miss ⇒
unchanged text, never an exception that blanks the answer). Move the shared
three-regex block into a small helper (e.g. `toolexec.py` or `secondary_loop.py`)
so `native_loop.py` imports it instead of duplicating — one definition, three
call sites (both secondary-loop variants + the final-shaping pass).

**(c) Grammar-constrained final answer (stronger, optional Wave-2).**
Because `response_format: json_schema` is proven live on this lane, a
longer-term fix is a *structured* final envelope
(`{"answer": "...", ...}`) so tool intent has no free-text channel at all —
but that changes the OpenAI streaming contract (content must still be prose,
not JSON) so it's out of scope for a flag-gated degrade-open patch; (a)+(b)
are the load-bearing fix, (c) is the durable follow-on already scoped by
task_112/task_120 (native typed-arg richness).

---

## LEAK-2 (CQ1 / tracked as task_116, extends task_110) — refine scaffold leaks to `content` + duplicate "Refining intent" status

### Root cause 1 — the refine pass drops `response_format:json_schema` specifically when streaming

`refine_intent()` (`refine.py:647-786`) supports a schema-constrained,
JSON-only envelope (`refine.py:749-786`, "WS-H1... constrain refine to a
strict json_schema"). But:

```python
refine.py:732-736
_refine_structured = os.environ.get("MIOS_REFINE_STRUCTURED", "true")...
if on_token:
    _refine_structured = False
```

Every **streaming** caller passes `on_token` (`chat.py:1420-1422`:
`refine_intent(last_user_text, messages, on_token=_on_refine_token)`) —
so on the CLI's actual live path, `_refine_structured` is forced `False` and
the `/no_think` sys-hint at `refine.py:738-739` is also skipped (only appended
`if not _refine_structured and not on_token`, and `on_token` is truthy here).
The model is therefore **unconstrained** and free to wrap its JSON in prose
like `**Refined JSON response**` / ```` ```json ```` fences — exactly what the
operator saw. That raw text is forwarded **token-by-token, verbatim**, via
`_call_on_token` (`refine.py:799-873`) → the `_on_refine_token` callback
(`chat.py:1417-1418`) → `_sse_reasoning(token_val, ..., reasoning_ok=_reasoning_ok)`
(`chat.py:1433-1435`).

### Root cause 2 — surface-routing falls back to the legacy content-inline path on an unlabeled surface

`_sse_reasoning()` (`sse.py:81-108`) routes by `reasoning_ok`:

```python
sse.py:102-108
if reasoning_ok is True:  return ...reasoning=text   # OWUI/Hermes: Thinking pane
if reasoning_ok is False: return ...content-inline    # declared content-only surface
if _DEBUG_ENABLE:         return ...content-inline    # <-- unknown surface + debug=true
return ...reasoning=text
```

`reasoning_ok` comes from the `x-mios-reasoning-ok` request header
(`chat.py:1188-1189`):

```python
_rok_hdr = (request.headers.get("x-mios-reasoning-ok") or "").strip().lower()
_reasoning_ok = None if not _rok_hdr else (_rok_hdr not in {"false", "0", "no"})
```

Nothing under `usr/share/mios/owui/`, `usr/bin/mios`, or `usr/libexec/mios/`
sets this header (grep found zero hits) — so the CLI's requests always carry
`_reasoning_ok = None`. `_DEBUG_ENABLE` (`sse.py:27,431-432`, driven by
`server.py:355-356`) **defaults to `"true"`**:

```python
server.py:355-356
_DEBUG_ENABLE = (str(os.environ.get("MIOS_DEBUG_ENABLE")
                      or _otel_toml.get("debug", "true")))
```

and `mios.toml:2932` also ships `debug = true` by design ("operator wants the
whole pipeline observable live" — `mios.toml:2927-2932`). So on the CLI, EVERY
`_sse_reasoning()` call — including the raw, unconstrained refine token stream
from Root cause 1 — lands in `delta.content`, not `reasoning_content`. This is
literally the "surface-aware `_sse_reasoning` fix... authored but undeployed"
gap `TASKS.md:2674-2679` (task_116) already names.

**A third, orphaned SSOT knob compounds this**: `mios.toml:2933` declares
`surface_default = "clean"   # clean = reasoning-channel; "inline" only via
per-surface opt-in` — but a repo-wide grep for `surface_default` shows it is
**never read anywhere in code**. The config's own stated safe-by-default
posture (reasoning-channel unless a surface opts in to inline) is dead SSOT —
Law-7-relevant: an unconsumed config key that silently diverges from actual
runtime behavior (`_DEBUG_ENABLE`-gated, not `surface_default`-gated).

### Root cause 3 — the "Refining intent…" status fires twice per turn (confirmed duplicate, not speculative)

The outer streaming handler emits the refine phase/status **once**, before it
even calls `refine_intent()`:

```python
chat.py:1411-1412
yield _sse_status_phase(chat_id=chat_id, model=model, phase="refine")
yield _sse_status(chat_id=chat_id, model=model, emoji="🧠",
                   label="Refining intent & decomposing plan...")
```

`_stream_refine_and_dispatch()` then hands off to
`_KERNEL.dispatcher.run(_kdec, **ctx_copy)` at `chat.py:1534`, which — when the
turn is short-circuited as a plain chat reply — routes into
`_kernel_chat_handler()` (`chat.py:1755`). That handler's own nested generator
**unconditionally re-announces** the same phases as if it were the first
emission of the turn:

```python
chat.py:1819-1822
if streaming:
    async def _stream_refine_chat():
        yield _sse_status_phase(chat_id=chat_id, model=model, phase="prompt")
        yield _sse_status_phase(chat_id=chat_id, model=model, phase="refine")
```

`_HUMAN_LABELS.get("refine", ...)` (`sse.py:151,163`) resolves to the exact
same emoji/label both times, so the CLI sees "🧠 Refining intent &
decomposing plan..." **twice** in one turn — confirmed at the code level (this
alone accounts for a 2x duplicate on any turn that takes the chat
short-circuit branch; the operator-observed "2-3x" is consistent with this
plus turn-shape variance across other kernel-decision branches).

### Design fix

1. **Constrain the streaming refine pass too.** Don't hard-disable
   `_refine_structured` when `on_token` is set (`refine.py:734-735`). Instead,
   keep `response_format: json_schema` bound on the request *and* still stream
   — llama.cpp/OpenAI-compatible engines support `stream:true` +
   `response_format` together (the grammar constrains sampling; SSE framing is
   orthogonal). If a lane genuinely can't do both (probe via the same
   `_endpoint_supports_parallel_tools`-style SSOT capability check), keep the
   degrade path but at minimum re-apply the `/no_think` + fence-stripping
   before any token reaches `on_token` — never forward a raw wrapped-JSON
   scaffold token-for-token. Flag: `MIOS_REFINE_STREAM_STRUCTURED` (default on).

2. **Refine trace is reasoning-channel, unconditionally, never content, on an
   unknown surface.** Change `_sse_reasoning`'s `None` branch
   (`sse.py:106-108`) from "fall back to `_DEBUG_ENABLE` inline" to "fall back
   to `surface_default` from SSOT (`mios.toml:2933`), defaulting to `reasoning`
   channel when unset" — i.e. wire the dead `surface_default` key in instead of
   leaving `_DEBUG_ENABLE` (a blunt, unrelated observability toggle) as the
   deciding factor for *routing*, not *visibility*. `debug=true` should still
   mean "stream everything, hide nothing" — but the CORRECT channel for hidden
   internals is `reasoning_content` (which OWUI/Hermes already render live in
   the Thinking pane per the docstring at `sse.py:89-93`); `debug` gates
   *whether* the trace ships, `surface_default`/`reasoning_ok` gates *which
   field it ships on*. Concretely: `_sse_reasoning` should read
   `surface_default` (injected via `configure()` like every other SSOT scalar
   in this module) and only use the legacy content-inline fallback when
   `surface_default == "inline"`.

3. **De-duplicate the refine status.** `_kernel_chat_handler`'s
   `_stream_refine_chat()` (`chat.py:1819-1822`) should not re-emit
   `phase="prompt"`/`phase="refine"` — those phases belong to the *outer*
   `_stream_refine_and_dispatch()` generator that already announced them
   before dispatching. Either (a) pass a `_phases_emitted` flag/set through
   `ctx`/`outer_ctx` (already threaded at `chat.py:1408,1527`) so any nested
   handler skips a phase already announced this turn, or (b) have
   `_kernel_chat_handler` jump straight to `phase="chat_done"`
   (`chat.py:1826`) since "refine" is definitionally already complete by the
   time this handler runs (it consumes `refined` from `ctx`). Flag:
   `MIOS_DEDUP_REFINE_STATUS` (default on); degrade-open (worst case: back to
   today's double-emit, never a missing status).

---

## RELIABILITY-1 — web_search query-expansion JSONDecodeError + opaque "verb execution failed"

### Root cause A — query expansion has no JSON-repair tier

`expand_queries()` (`usr/libexec/mios/mios-web-search:232-`) calls the
expansion micro-model at `_EXPAND_ENDPOINT`/`_EXPAND_MODEL`, where the port
default is the light lane:

```
mios-web-search:85
_EXPAND_PORT = _ssot_val("MIOS_PORTS_LLM_LIGHT", "ports", "llm_light", "8450")
```

(matches the operator log's `@ http://localhost:8450`). The non-Ollama branch
(`mios-web-search:288-298`) already sets
`"response_format": {"type": "json_object"}` + `enable_thinking:false` — the
same grammar-drop caveat noted in LEAK-1 (llama.cpp #20345) applies, and a
cold/loaded lane can still return an empty or truncated `content`. Parsing is
then a **bare stdlib call with no fallback tier**:

```python
mios-web-search:304-308
content = (...).get("content") or "{}"
obj = json.loads(content)          # <-- raises verbatim JSONDecodeError on "" / non-JSON
extra = [str(q).strip() for q in (obj.get("queries") or []) ...]
```

`content` defaults to the **string** `"{}"` only when the dict-access chain is
falsy — but an actual HTTP 200 with an **empty string body** (`content = ""`)
is truthy-as-a-key-miss-fallback only via `or`, so `"" or "{}"` *does*
correctly become `"{}"`... except the observed error
(`Expecting value: line 1 column 1 (char 0)`) is `json.loads("")` — meaning
`content` was not the Python empty string but something `or` didn't catch
(e.g. `None` slipping past because `.get("content")` returned a non-string
falsy-but-truthy edge, or the outer `_d.get(...)` chain itself raised before
reaching this line and the message is from a *different* `json.loads` call —
line 304's own `_d = json.loads(r.read()...)` at `mios-web-search:303-304`
parses the **raw HTTP response body itself**, which is the more likely
failure point: a non-200 body, an HTML error page, or a truncated stream from
a cold-loading model would make *that* `json.loads` throw
"Expecting value: line 1 column 1" (a completely empty or non-JSON HTTP body).
Either way, the pattern is the same: **no lenient/salvage parse** is attempted
anywhere in this function, unlike the rest of the pipe, which has a dedicated
tolerant parser (`mios_pipe/routing/jsonsalvage.py`, referenced pipe-wide as
`_loads_lenient`) used by `toolexec.py:708,719` and others. `mios-web-search`
is a standalone `usr/libexec` script and does not import it.

The `except Exception` at `mios-web-search:309-316` is otherwise a reasonable,
already-de-silenced degrade-open (it `print`s to stderr and falls back to
`extra = []` → single-query search, so the turn is never blocked) — the gap is
purely "no repair attempt before giving up," not "silently swallowed" (that
part was already fixed per the comment at line 310-312).

### Root cause B — the verb-error wrapper discards real detail

`_format_tool_error()` (`toolexec.py:310-333`) is applied to every plain-verb
result (`web_search`, `run_code`, `list_directory`, …) at the one dispatch
chokepoint:

```python
toolexec.py:692-698
try:
    res = await asyncio.wait_for(dispatch_mios_verb(_key, args), ...)
except Exception as e:
    res = {"error": str(e)}
...
toolexec.py:725-728
if _res_dict:
    _err = _format_tool_error(_res_dict)
    if _err:
        res = _err
```

```python
toolexec.py:310-333
def _format_tool_error(res):
    if isinstance(res, dict):
        if "error" in res and isinstance(res["error"], dict) ...: return res
        ...
        if res.get("success") is False:
            has_error = True
            err_msg = res.get("error") or res.get("stderr") or "verb execution failed"
        elif res.get("ok") is False:
            has_error = True
            err_msg = res.get("error") or res.get("stderr") or "verb execution failed"
        ...
        if has_error:
            return {"error": {"message": err_msg, "type": "invalid_request_error",
                               "code": "tool_execution_failed"}}
```

Two problems:

1. **It only looks at `error`/`stderr`.** `mios_dispatch.py` (the SSOT verb
   executor) *does* populate `stderr` on most known failure branches
   (firewall_block, hitl_blocked, broker errors — `mios_dispatch.py:1300-1521`
   all set a descriptive `stderr`), so those cases *should* surface correctly.
   But any verb result that reports `success:false`/`ok:false` with its detail
   under a **different** key (e.g. `output` holding the real error JSON/text,
   which is how several `mios-*` CLI scripts including `mios-web-search`
   report failures on stdout rather than stderr) has that detail **silently
   discarded** — `_format_tool_error` builds a brand-new dict
   (`{"error": {"message": ...}}`) and the caller does `res = _err`
   (`toolexec.py:728`), **replacing** `res` wholesale rather than merging. Any
   `output`/other field the original `res` carried is gone by the time
   `out = json.dumps(res, ...)` (`toolexec.py:730`) runs.
2. **The literal fallback string `"verb execution failed"` is itself
   information-destroying** — it fires whenever the two known keys are empty,
   which is exactly the situation where the model (and the operator reading
   the log) most needs the *actual* stdout/stderr/exit code to diagnose why.

### Design fix

1. **`mios-web-search`: add one lenient-JSON tier before failing.** Before
   `json.loads(content)` (and separately for the raw response body parse at
   line 303-304), attempt (a) direct `json.loads`, (b) a regex-extract of the
   first balanced `{...}` substring (mirrors the generic, topic-free approach
   already used by the pipe's `jsonsalvage.py` — "no field/topic knowledge",
   per its own docstring at `jsonsalvage.py:21`), (c) fall through to today's
   `extra = []` degrade-open only if both fail. This is a pure parsing
   robustness change, script-local, no behavior change on the happy path.
   Flag: `MIOS_WEBSEARCH_EXPAND_SALVAGE` (default on).
2. **Log the *actual* HTTP status + a body snippet**, not just the exception
   type/message, in the `except` at `mios-web-search:309-316` — right now
   `{type(e).__name__}: {e}` for a `JSONDecodeError` says nothing about *what*
   the model/endpoint actually returned; add `content[:200]` (or the raw body
   snippet) to the stderr line so the journal is diagnosable without a repro.
3. **`_format_tool_error`: never replace, only augment, and never fabricate a
   contentless message.** Change the call site (`toolexec.py:725-728`) to
   merge rather than replace: keep the original `res` under e.g.
   `res["_original"]` or fold `res.get("output")` into `err_msg` when present,
   so nothing already-captured is thrown away. And change the two `"verb
   execution failed"` literals (`toolexec.py:318,321`) to include whatever
   *is* available — `res.get("output")`, `res.get("exit_code")`,
   `res.get("code")` — joined into the message, falling back to the bare
   generic string only when the entire `res` dict is genuinely empty of any
   signal. This is the anti-fabrication principle applied to *errors*: an
   opaque wrapper that hides a real failure is itself a small fabrication (it
   claims "verb execution failed" when the truth might be far more specific
   and actionable). Flag: `MIOS_TOOLERR_VERBOSE` (default on); degrade-open
   (unset/false → today's exact string, zero behavior change).

---

## RELIABILITY-2 — `apps {"mode":"list"}` never surfaces Windows-installed games (root cause: truncation, not missing enumeration)

### The inventory code already does the right thing

`usr/libexec/mios/mios-apps` is a **generative, no-hardcode** multi-source
scanner (header comment `mios-apps:1-13`) that emits, in this section order:

| Header (line) | Source |
|---|---|
| `linux-flatpak` (156) | `flatpak list --system` |
| `linux-rpm-gui` (168) | `/usr/share/applications/*.desktop` |
| `windows-gui` (197) | `mios-win-scan` (native drvfs read of Steam/Epic/GOG/Store-UWP/Start-Menu, `mios-apps:198-227`), falling back to PowerShell `Get-ChildItem ...Start Menu\Programs\*.lnk` (`mios-apps:230-245`) |
| `windows-app` (255) | PowerShell `Get-StartApps` (Win32+UWP), cached at `/var/lib/mios/agent-env/windows-apps.cache` |
| `windows-browser` (325) | registry `Clients\StartMenuInternet` |
| `windows-game` (368) | Steam `libraryfolders.vdf` (every library, any drive — comment at `mios-apps:384-390` explicitly targets the "no hardcodes / found 3 games?!" bug class), Epic `Manifests/*.item` (`mios-apps:424-434`), GOG Galaxy registry (`mios-apps:439-`), Xbox/Store via `Get-AppxPackage`+`Get-StartApps` (`mios-apps:454-478`) |

So the claim "never enumerates Windows-installed games" is **not** because the
enumeration is missing — `mios-win-scan` + the PowerShell fallbacks
demonstrably read Steam/Epic/GOG/Xbox sources. The gap is downstream.

### Root cause — the one inventory verb missing a `max_result_chars` override

The verb declaration for `apps` has **no** `max_result_chars`:

```toml
mios.toml:9133-9146
[verbs.apps]
...
cmd = "if [ {mode} = list ]; then mios-apps --json || true; ..."
  [verbs.apps.params.mode]
  ...
  [verbs.apps.params.query]
  type = "string"
```

So it silently inherits the generic default:

```python
toolexec.py:47   READ_TOOL_ENRICH_CHARS = 1500
toolexec.py:50   ACI_HEAD_FRAC = 0.6
toolexec.py:282-288
def _verb_result_cap(verb):
    cap = int((_VERB_CATALOG.get(verb) or {}).get("max_result_chars") or 0)
    return cap if cap > 0 else READ_TOOL_ENRICH_CHARS
```

`_cap_verb_result` (`toolexec.py:291-307`) then applies `_aci_normalize` with
`head_frac=0.6` over a **1500-char** budget — i.e. ~900 chars of head + ~600
of tail, **middle dropped**. Given the emission order above, the ~900-char
head covers `linux-flatpak` (and maybe the start of `linux-rpm-gui`) and
**nothing else** — `windows-gui`/`windows-app`/`windows-browser`/`windows-game`
sections sit squarely in the dropped middle of a combined multi-source JSON
inventory that easily runs to several thousand characters on a real desktop.

This is not a hypothetical: **other inventory/discovery verbs already carry an
explicit fix for this exact failure mode**, with comments naming the identical
symptom class (a truncated list → model completes it from imagination):

```toml
mios.toml:4361  max_result_chars = 5000   # ...fit it whole so the model never invents missing values
mios.toml:4447  max_result_chars = 6000   # ...fit the default 20-process list whole (1500 cut it mid-process -> fabricated entries)
mios.toml:4473  max_result_chars = 4000   # ...fit the full container list whole (avoids a mid-row cut the model would complete from imagination)
```

`apps` is the one multi-source discovery verb that never received the same
treatment, so the model sees only Linux flatpaks, correctly has "no data" for
Windows games, and — per the exact anti-fabrication failure class the other
three verbs above were already patched against — invents them instead
(consistent with the sibling task_114/task_115 fabrication findings covering the
same live session).

### Design fix

1. **Give `[verbs.apps]` an explicit `max_result_chars`** sized to the full
   combined inventory (several thousand chars — measure a live
   `mios-apps --json` on a real desktop and set with headroom, following the
   exact pattern at `mios.toml:4361/4447/4473`). No code change, pure SSOT
   config — the lowest-risk fix of everything in this document.
2. **Query-aware filtering before capping, not after.** `apps.params.query`
   already exists (`mios.toml:9145-9146`) but `mode=list` ignores it
   (`mios-apps` cmd only forwards `{query}` to the `semantic`/`resolve`
   branches — `mios.toml:9141`). When `mode=list` AND a `query` is present
   (e.g. `"video game"`), filter the emitted lines by category
   (`windows-game`/`linux-flatpak` entries tagged as games, matched against
   the model-supplied `query` as a semantic/category hint, not a keyword
   literal — keep this **model-driven**: pass `query` through as an
   `inventory_filter` the same way `refine.py`'s `inventory_filter` field
   already exists in the refine schema, `refine.py:765`) so a narrow ask
   returns a short, complete, on-topic list instead of needing the full
   multi-thousand-char dump capped at all.
3. **Make scan degradation visible instead of silent.** Add a
   `"_scan_status"` field per section (e.g.
   `{"windows-game": "ok"|"degraded: mios-win-scan unavailable"|"degraded: powershell interop down"}`)
   so a genuinely broken Windows-interop host (the `|| true` at
   `mios-apps:210,238-244,273-275` already tolerates failure silently) is
   distinguishable, in the tool result itself, from "the operator has no
   Windows games." This directly serves the anti-fabrication mandate: the
   model should never have to guess whether an empty section means "verified
   absent" or "scan failed" — that ambiguity is exactly what invites it to
   fabricate a plausible-sounding answer instead of saying "I can't check
   right now."

---

## Summary table

| Failure | file:line root cause | Recommended fix | Flag |
|---|---|---|---|
| LEAK-1: literal `<tool_call>`/```json``` in final answer | `native_loop.py:780-782` (`_pb` has no `tools`/`tool_choice`/`stop`); no post-strip after `native_loop.py:851-854` (compare `secondary_loop.py:341-344,512-515` which DOES strip, but only for the tools-bound loop) | (a) add `tools`+`tool_choice:none`+`stop` markers to the final `_pb`; (b) reuse the proven 3-regex strip from `secondary_loop.py` on `_ans` before it ships; grammar (`response_format:json_schema`) proven live on this lane via `classify.py`/`chat.py:600`/`planner.py:400` | `MIOS_FINAL_STOP_MARKERS`, `MIOS_FINAL_TOOLSYNTAX_STRIP` |
| LEAK-2a: refine scaffold rides `content` on CLI | `refine.py:734-735` (`_refine_structured=False` whenever `on_token` set) + `chat.py:1188-1189`/`sse.py:102-108` (`reasoning_ok=None` on CLI → `_DEBUG_ENABLE` default `true`, `server.py:355-356`) + dead SSOT `mios.toml:2933 surface_default` (never read anywhere) | keep `response_format:json_schema` bound even while streaming; wire `surface_default` into `_sse_reasoning`'s `None` branch instead of `_DEBUG_ENABLE`, defaulting to reasoning-channel | `MIOS_REFINE_STREAM_STRUCTURED`, wire `surface_default` |
| LEAK-2b: "🧠 Refining intent…" fires 2x+/turn | `chat.py:1411-1412` (outer emit) duplicated by `chat.py:1820-1822` (`_kernel_chat_handler`'s nested `_stream_refine_chat()` re-announces `phase="prompt"`+`phase="refine"`) | thread a per-turn "phases already emitted" set through `ctx`/`outer_ctx`; nested handler skips phases the outer generator already announced | `MIOS_DEDUP_REFINE_STATUS` |
| RELIABILITY-1a: web_search query-expansion `JSONDecodeError` | `mios-web-search:303-308` (bare `json.loads` on the HTTP body / model `content`, no salvage tier; port 8450 = `mios-llm-light` per `mios-web-search:85`) | add a balanced-`{...}` regex-extract salvage tier before failing; log the actual body/status snippet, not just the exception repr | `MIOS_WEBSEARCH_EXPAND_SALVAGE` |
| RELIABILITY-1b: opaque "verb execution failed" | `toolexec.py:310-333` (`_format_tool_error`, only reads `error`/`stderr`, wholesale-replaces `res`) applied at `toolexec.py:725-728` | merge instead of replace; fold `res.get("output")`/exit code into the message; reserve the bare generic string for a truly empty `res` | `MIOS_TOOLERR_VERBOSE` |
| RELIABILITY-2: `apps` never shows real Windows games | `mios.toml:9133-9146` (`[verbs.apps]` has no `max_result_chars`, unlike `mios.toml:4361/4447/4473` which already fix the identical class of bug) → `toolexec.py:47,50,282-288` caps to 1500 chars / 0.6 head-frac, dropping the `windows-gui`/`windows-app`/`windows-browser`/`windows-game` sections (`mios-apps:197,255,325,368`) that sit after `linux-flatpak`/`linux-rpm-gui` (`mios-apps:156,168`) in the emitted JSON | set an explicit `max_result_chars` sized to the full multi-source inventory; wire `mode=list`+`query` into a real category filter instead of ignoring `query` (`mios.toml:9141`); add a per-section `_scan_status` so a degraded scan is distinguishable from "genuinely no Windows games" | none needed for the size fix (pure SSOT config); `inventory_filter` wiring is additive |
