# AI-hint: REFINE intent-classifier extracted verbatim from server.py (refactor R5/mios_refine wave).
# AI-doc: usr/share/doc/mios/manual/routing.md

import asyncio
import json
import inspect
import os
import re
import time
from typing import Optional, Callable, Any

import httpx

import mios_tokenize
from mios_jsonsalvage import loads_lenient as _loads_lenient
from mios_grounding import _env_grounding, _env_grounding_static, _env_grounding_dynamic
from mios_routing import _deterministic_action_route
from mios_dci import dci_critic_pass, DCI_ENABLED, DCI_FLOW_TRIGGER_CONF

log = None
_AGENT_REGISTRY: dict = {}
_VERB_CATALOG: dict = {}
_routed_domain_var = None
_over_global_ceiling = None
_resolve_verb_key = None
_route_domain = None
_db_fire = None
_db_post = None
_db_create = None
REFINE_ENABLED = False
REFINE_MODEL = ""
REFINE_ENDPOINT = ""
REFINE_MAX_TOKENS = 700
REFINE_TIMEOUT_S = 30
REFINE_ATTEMPTS = 2
resolve_preferences = None
_OS_CONTROL_VERBS_RENDERED = ""
_BROWSER_ACTION_ALT = ""
_WEB_SEARCH_TRIGGERS: list = []
_WEB_SEARCH_CONTEXTS: list = []
_REMEMBER_TRIGGERS: list = []
_FASTPATH_VERBS = frozenset()
_ROUTING_ENABLE = False
_ROUTING_DOMAINS: dict = {}
REFINE_CHAT_CHARS = 40              # prompt cue: chat is for very short conversational input
REFINE_DISPATCH_CHARS = 60         # prompt cue: dispatch is for short verb invocations
REFINE_PROMOTE_CHARS = 100         # >this -> promote a shallow chat/dispatch to agent (also a prompt cue)
REFINE_DISPATCH_ARG_MAX_WORDS = 3  # a dispatch arg with more words is a semantic phrase -> agent
_emit_session_event = None
CRITIC_REFINE_ENABLED = True
CRITIC_REFINE_MAX = 1
CRITIC_REFINE_MIN_CHARS = 500

def configure(*, logger=None, agent_registry=None, verb_catalog=None,
              routed_domain_var=None, over_global_ceiling=None,
              resolve_verb_key=None, route_domain=None,
              db_fire=None, db_post=None, db_create=None,
              refine_enabled=None, refine_model=None, refine_endpoint=None,
              refine_max_tokens=None, refine_timeout_s=None, refine_attempts=None,
              os_control_verbs_rendered=None, browser_action_alt=None,
              web_search_triggers=None, web_search_contexts=None,
              remember_triggers=None, fastpath_verbs=None,
              routing_enable=None, routing_domains=None,
              promote_chars=None, dispatch_arg_max_words=None,
              chat_chars=None, dispatch_chars=None,
              emit_session_event=None, critic_refine_enabled=None,
              critic_refine_max=None, critic_refine_min_chars=None,
              resolve_preferences_inject=None) -> None:
    global log, _AGENT_REGISTRY, _VERB_CATALOG, _routed_domain_var
    global _over_global_ceiling, _resolve_verb_key, _route_domain
    global _db_fire, _db_post, _db_create
    global REFINE_ENABLED, REFINE_MODEL, REFINE_ENDPOINT, REFINE_MAX_TOKENS
    global REFINE_TIMEOUT_S, REFINE_ATTEMPTS, resolve_preferences
    global _OS_CONTROL_VERBS_RENDERED, _BROWSER_ACTION_ALT
    global _WEB_SEARCH_TRIGGERS, _WEB_SEARCH_CONTEXTS, _REMEMBER_TRIGGERS, _FASTPATH_VERBS
    global _ROUTING_ENABLE, _ROUTING_DOMAINS
    global REFINE_CHAT_CHARS, REFINE_DISPATCH_CHARS, REFINE_PROMOTE_CHARS
    global REFINE_DISPATCH_ARG_MAX_WORDS, _REFINE_SYSTEM
    global _emit_session_event, CRITIC_REFINE_ENABLED, CRITIC_REFINE_MAX, CRITIC_REFINE_MIN_CHARS

    if resolve_preferences_inject is not None:
        resolve_preferences = resolve_preferences_inject
    if logger is not None:
        log = logger
    if agent_registry is not None:
        _AGENT_REGISTRY = agent_registry
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if routed_domain_var is not None:
        _routed_domain_var = routed_domain_var
    if over_global_ceiling is not None:
        _over_global_ceiling = over_global_ceiling
    if resolve_verb_key is not None:
        _resolve_verb_key = resolve_verb_key
    if route_domain is not None:
        _route_domain = route_domain
    if db_fire is not None:
        _db_fire = db_fire
    if db_post is not None:
        _db_post = db_post
    if db_create is not None:
        _db_create = db_create
    if refine_enabled is not None:
        REFINE_ENABLED = refine_enabled
    if refine_model is not None:
        REFINE_MODEL = refine_model
    if refine_endpoint is not None:
        REFINE_ENDPOINT = refine_endpoint
    if refine_max_tokens is not None:
        REFINE_MAX_TOKENS = refine_max_tokens
    if refine_timeout_s is not None:
        REFINE_TIMEOUT_S = refine_timeout_s
    if refine_attempts is not None:
        REFINE_ATTEMPTS = refine_attempts
    if os_control_verbs_rendered is not None:
        _OS_CONTROL_VERBS_RENDERED = os_control_verbs_rendered
    if browser_action_alt is not None:
        _BROWSER_ACTION_ALT = browser_action_alt
    if web_search_triggers is not None:
        _WEB_SEARCH_TRIGGERS = web_search_triggers
    if web_search_contexts is not None:
        _WEB_SEARCH_CONTEXTS = web_search_contexts
    if remember_triggers is not None:
        _REMEMBER_TRIGGERS = remember_triggers
    if fastpath_verbs is not None:
        _FASTPATH_VERBS = fastpath_verbs
    if routing_enable is not None:
        _ROUTING_ENABLE = routing_enable
    if routing_domains is not None:
        _ROUTING_DOMAINS = routing_domains
    if emit_session_event is not None:
        _emit_session_event = emit_session_event
    if critic_refine_enabled is not None:
        CRITIC_REFINE_ENABLED = critic_refine_enabled
    if critic_refine_max is not None:
        CRITIC_REFINE_MAX = critic_refine_max
    if critic_refine_min_chars is not None:
        CRITIC_REFINE_MIN_CHARS = critic_refine_min_chars
    _cuts_changed = False
    if chat_chars is not None:
        REFINE_CHAT_CHARS = int(chat_chars)
        _cuts_changed = True
    if dispatch_chars is not None:
        REFINE_DISPATCH_CHARS = int(dispatch_chars)
        _cuts_changed = True
    if promote_chars is not None:
        REFINE_PROMOTE_CHARS = int(promote_chars)
        _cuts_changed = True
    if dispatch_arg_max_words is not None:
        REFINE_DISPATCH_ARG_MAX_WORDS = int(dispatch_arg_max_words)
    if _cuts_changed:
        _REFINE_SYSTEM = _build_refine_system()

def _build_refine_system() -> str:
    return (
    "You are MiOS-Agent's refine pass. Read the user's message and\n"
    "the recent chat history. Emit a single JSON object describing\n"
    "what the user wants AND how to achieve it. Be terse -- output\n"
    "is consumed by another agent, NOT shown to the user.\n"
    "\n"
    "Schema:\n"
    '  {\n'
    '    "intent": "<one of: chat | dispatch | agent | dag | multi_task>",\n'
    '    "refined_text": "<rewritten user query in clear, actionable form>",\n'
    '    "intended_outcome": "<one short line: what the user expects back>",\n'
    '    "target_agent": "<one of the registered sub-agents -- pick by role>",\n'
    '    "hint_tools":  ["<verb-name-1>", "<verb-name-2>", ...],\n'
    '    "hint_skills": ["<skill-name-1>", ...],\n'
    '    "reply": "<for intent=chat: your reply directly; omit otherwise>",\n'
    '    "tasks": [   // ONLY for intent=multi_task. One entry per\n'
    '                 //   discrete goal the user crammed into one prompt.\n'
    '      {\n'
    '        "title":            "<short imperative -- one line>",\n'
    '        "refined_text":     "<rewritten subtask, agent-ready>",\n'
    '        "intended_outcome": "<what success looks like for THIS task>",\n'
    '        "target_agent":     "<role-matched sub-agent>",\n'
    '        "hint_tools":       ["..."],\n'
    '        "hint_skills":      ["..."],\n'
    '        "priority":         1,  // lower runs first; 1..N\n'
    '        "depends_on":       []  // task indices this one waits for;\n'
    '                                //   empty = runs first / in parallel\n'
    '      }, ...\n'
    '    ],\n'
    '    "tool_cards": [   // OPTIONAL but PREFERRED for intent in\n'
    '                      //   {agent, dag, multi_task}. Per-step\n'
    '                      //   guidance carried INTO the sub-agent\n'
    '                      //   dispatch so it knows WHY each tool is\n'
    '                      //   hinted + what success looks like. Lifts\n'
    '                      //   the planning burden off the worker.\n'
    '      {\n'
    '        "tool":              "<verb-name or skill-name>",\n'
    '        "args_hint":         {"key": "value", ...},\n'
    '        "why":               "<one line: why THIS tool for THIS step>",\n'
    '        "success_predicate": "<short check: how to know it worked>",\n'
    '        "output_used_by":    [<idx-of-step-that-consumes-this>]\n'
    '      }, ...\n'
    '    ]\n'
    '  }\n'
    "\n"
    "REASON -> PLAN -> DELEGATE meta-rule:\n"
    "  An 'open / find / install / launch / use / run / start / show /\n"
    "  reveal X' intent NEVER routes to `chat`. NEITHER does any request\n"
    "  for CURRENT or EXTERNAL information: 'search the web for', 'look\n"
    "  up', 'latest', 'today', 'news', 'recent', \"what's trending\",\n"
    "  prices, weather, scores, or ANY fact not answerable from THIS\n"
    "  conversation alone. Those need the agent's web_search / web_extract\n"
    "  tools -- pick `agent` (or `dag`). Decide local-vs-web by intent: a\n"
    "  file/app on THIS computer -> agent with directory_lookup/\n"
    "  everything_search/fs_search; current world info -> agent with\n"
    "  web_search/web_extract. The downstream agent must fan out across\n"
    "  discovery/search surfaces before deciding -- never refuse or\n"
    "  chat-reply without trying. Refine-time `chat` is RESERVED for\n"
    "  greetings / thanks / single-turn conversational text with NO action\n"
    "  verb AND no external-info need.\n"
    "\n"
    "Intent classification:\n"
    "  chat        -- greeting, thanks, single-turn conversation; no system\n"
    "                 effect needed; emit `reply` and no agent is called.\n"
    "                 NOT for any 'open / find / launch / install / show /\n"
    "                 reveal / run / start <X>' intent -- those need\n"
    "                 tools and must route to `agent` or `dag`.\n"
    "  dispatch    -- maps to ONE MiOS verb; tool + args populated by the\n"
    "                 existing router. Refine just rewrites refined_text.\n"
    "  agent       -- needs a sub-agent for ONE coherent goal. Pick\n"
    "                 target_agent by role:\n"
    "                 * general    (Hermes)        -- broad reasoning + tools\n"
    "                 * coding     (OpenCode)      -- file edits / refactor / git\n"
    "                 * telemetry  (mios-daemon-agent) -- 'what just happened?',\n"
    "                              log/journal tail, recent system activity\n"
    "                              follow-ups. Pinned to 2 cores; always-on.\n"
    "  dag         -- ONE goal broken into multiple dependent steps; the\n"
    "                 planner will decompose. target_agent can be empty.\n"
    "  multi_task  -- the user crammed SEVERAL INDEPENDENT goals into one\n"
    "                 prompt (e.g. 'open chrome AND install vscode AND\n"
    "                 summarize my journal'). Emit a `tasks` array with one\n"
    "                 entry per discrete goal, ordered by priority. The\n"
    "                 dispatcher runs task #1 immediately, queues the rest\n"
    "                 in kanban for sequential execution.\n"
    "\n"
    "RULES:\n"
    "- ALWAYS emit valid JSON. No prose around it.\n"
    "- `hint_tools` lists MiOS verb names you think the agent will need\n"
    "  (open_app, focus_window, text_view, winget_search, ...).\n"
    "- For 'find <X>' / 'where is <X>' / 'show me the <X> file' queries,\n"
    "  ALWAYS hint `directory_lookup` -- sub-100ms DB query against the\n"
    "  mios-daemon cache (~19k indexed entries). Falls back to\n"
    "  `everything_search` (Windows-side live search) or `fs_search`\n"
    "  (Linux-side deep walk) only when the cache misses.\n"
    "- DURABLE MEMORY + KNOWLEDGE actions map to verbs -- do NOT just\n"
    "  acknowledge them in `chat`. When the user asks you to KEEP/REMEMBER/\n"
    "  SAVE/NOTE a durable fact -> intent=dispatch tool=`remember`. To READ\n"
    "  back what was saved -> `recall`. To CONDENSE a doc/text into tiers ->\n"
    "  `summarize`. To pull local files/notes into the knowledge vault ->\n"
    "  `ingest`. To NAVIGATE/SEARCH the stored second brain -> `viking_ls`/\n"
    "  `viking_find`/`viking_cat`. To run a code snippet SAFELY in a sandbox\n"
    "  -> `coderun`. These are real effects; a bare conversational 'noted'\n"
    "  with no verb is WRONG when the user asked you to remember/save it.\n"
    "- `hint_skills` lists C.2 skill names from the catalog\n"
    "  (open-and-focus, install-flatpak-app, window-tile-side-by-side).\n"
    "- For conversational input (greetings, small talk, single-turn\n"
    "  questions like 'how are you', acknowledgements, thanks):\n"
    "  pick intent=chat AND populate `reply` with a brief, natural\n"
    "  response. Do NOT delegate to a sub-agent. Examples that should\n"
    "  ALWAYS be chat: 'hey', 'hi', 'hello', 'thanks', 'thank you',\n"
    "  'how's it going', 'how are you', 'good morning', 'bye'.\n"
    "  When in doubt about conversational vs. agent: if the user is\n"
    "  not asking for a system action / file / data / code, chat.\n"
    "- multi_task vs dag: dag = ONE goal, dependent steps (e.g. 'install\n"
    "  vscode and open it'). multi_task = SEVERAL goals, independent\n"
    "  (e.g. 'install vscode AND THEN ALSO summarize my journal AND\n"
    "  THEN ALSO post a status to discord'). Three+ unrelated\n"
    "  imperatives joined by `and`/`also`/`then` is the multi_task tell.\n"
    "- multi_task MUST emit `tasks` with >= 2 entries. If you only\n"
    "  find one goal, use intent=agent or intent=dag instead.\n"
    "- RESEARCH-AND-REPORT: when the goal is to GATHER information on one\n"
    "  or more topics and report the findings back IN THE ANSWER (rather\n"
    "  than putting something on the operator's screen), it is research,\n"
    "  not launching. Decompose into one INDEPENDENT research task per\n"
    "  topic so they dispatch CONCURRENTLY (depends_on empty), each\n"
    "  delegated to a web_search-capable sub-agent that fetches + reads\n"
    "  page content via Hermes's native Chrome browsing; finish with a\n"
    "  synthesis step that combines the findings into one report. NEVER\n"
    "  map a 'check / look up / find out <topic>' goal to open_url or to\n"
    "  opening a visible browser window per topic -- open_url only SHOWS a\n"
    "  page the operator explicitly asked to see.\n"
    "- EXPLICIT-TARGET LAUNCH (decisive, OVERRIDES research): when the user\n"
    "  names a browser/app to open something IN or WITH it ('open <X> in\n"
    "  epiphany', 'show <url> in GNOME Web', 'pull <page> up in chrome',\n"
    "  'open epiphany to <url>'), the operator wants a WINDOW ON SCREEN, not\n"
    "  a report. This is ALWAYS intent=dispatch, tool=open_url, args=\n"
    "  {\"url\": <resolved real URL>, \"browser\": <the named app>}. NEVER\n"
    "  route a named-browser launch to research / web_search / agent. The\n"
    "  named app target (a browser the operator points at) is the decisive\n"
    "  tell. Resolve a page description to its real URL ('the Wikipedia\n"
    "  main page' -> https://en.wikipedia.org/wiki/Main_Page).\n"
    "- BREADTH = FACETS: a BROAD or COMPREHENSIVE ask about a SINGLE topic\n"
    "  (the user wants 'everything', the 'full picture', 'all the latest', a\n"
    "  wide/deep overview) is multi_task too -- split the ONE topic into 2-4\n"
    "  INDEPENDENT FACETS of it (distinct angles / sub-topics / regions /\n"
    "  sectors) and emit one research task per facet so they dispatch\n"
    "  CONCURRENTLY, then synthesise. A wide ask deserves a real swarm, not\n"
    "  one shallow pass. (A narrow single-fact question stays intent=agent.)\n"
    "- `tool_cards` rationale (ReWOO + MCP-style annotations): the\n"
    "  worker agent (Hermes / OpenCode / daemon-agent) sees ONLY what\n"
    "  you emit. If you list tools in hint_tools but the worker has\n"
    "  no idea WHY each one was hinted, it'll re-derive the plan\n"
    "  itself (slow + error-prone). Per-step `tool_cards` carry the\n"
    "  WHY + the success predicate, so the worker just executes. For\n"
    "  multi-step goals (3+ tool calls), emit tool_cards even when\n"
    "  intent stays `agent` -- they're additive guidance, not a new\n"
    "  intent class. Skip tool_cards for intent=chat or single-step\n"
    "  dispatch (no value vs. cost).\n"
    "- For dag: tool_cards' `output_used_by` lets the worker chain\n"
    "  step outputs (e.g. step 0 lists games -> step 1 web_search\n"
    "  ratings -> step 2 launches winner). Worker substitutes #E0,\n"
    "  #E1 placeholders into args at execute time -- you don't have\n"
    "  to know the runtime values.\n"
    "\n"
    "Length cue (CRITICAL): intent=chat is for SHORT conversational\n"
    "inputs (~ <" + str(REFINE_CHAT_CHARS) + " chars: 'hi', 'how are you', 'thanks'). intent=\n"
    "dispatch is for SHORT verb invocations (~ <" + str(REFINE_DISPATCH_CHARS) + " chars: 'open\n"
    "chrome', 'launch steam', 'screenshot'). If the user_text is\n"
    "LONG (>" + str(REFINE_PROMOTE_CHARS) + " chars) it almost certainly describes a multi-step\n"
    "goal -- pick intent=dag (or multi_task for unrelated parallel\n"
    "goals) and decompose. A long-text intent=dispatch is almost\n"
    "always wrong -- the args would have to carry a semantic\n"
    "descriptor (e.g. 'the highest reviewed game I have installed')\n"
    "which the launcher can't resolve to a real app.\n"
    "\n"
    "Arg-concreteness rule: when emitting intent=dispatch, every\n"
    "args value MUST be a concrete identifier (app name, file\n"
    "path, URL, fully-qualified id). NEVER a semantic phrase\n"
    "('highest', 'best', 'the one with X', 'whichever is fastest').\n"
    "Do NOT invent or guess command-line arguments (e.g. '--big-picture' or\n"
    "'-bigpicture') for open_app/launch_app. If the requested mode or target\n"
    "has a native URI protocol (like steam://open/bigpicture for Steam Big Picture\n"
    "mode), use that native URI directly as the app target name/URL.\n"
    "If the right value can't be known without first running other\n"
    "tools, pick intent=dag with the lookup as step 0 and the\n"
    "dispatch as a downstream node using #E0 substitution.\n"
    "\n"
    "Strict version grounding rule: Do NOT guess, assume, or append specific version numbers,\n"
    "release versions, or hardware specifications (e.g. '4', '5', '6', '2026') unless they\n"
    "are explicitly requested by the user or present in the chat history/context. Keep generic brand\n"
    "or product names (e.g. 'spotify' or 'photoshop') EXACTLY as requested in the refined query\n"
    "so that downstream search/resolver tools can check the actual installed system inventory.\n"
    )

_REFINE_SYSTEM = _build_refine_system()

_REFINE_SYSTEM_LITE = (
    "You are MiOS-Agent's refine pass. Read the user's message + recent\n"
    "history and output ONE compact JSON object (no prose).\n"
    "\n"
    "Fields:\n"
    '  "intent": chat | dispatch | agent | multi_task   (coarse -- the\n'
    "    planner decides single-step vs multi-step downstream)\n"
    '  "refined_text": the request rewritten as a clear, ACTIONABLE query.\n'
    "    For follow-up / contextual requests (e.g. 'research further', 'tell me more',\n"
    "    'explain the second one', 'why?', 'show links'), you MUST resolve all relative\n"
    "    references and details from the chat history into a fully detailed and explicit\n"
    "    rewritten query (e.g. 'detailed background and additional news on Volkswagen cost-cutting\n"
    "    production cuts in China July 2026') rather than repeating the generic query.\n"
    "    For current / recent / live info (news, events, trends, prices,\n"
    "    scores), make it a CONCRETE search query anchored to NOW (use the\n"
    "    current date or 'today' / 'latest') and DISAMBIGUATE any vague word a\n"
    "    search engine would mis-match to a brand / product / unrelated term\n"
    "    (e.g. a bare 'current' or 'trending' that hits an app or a\n"
    "    dictionary). This is the string the web search actually runs.\n"
    "    Do NOT guess, assume, or append specific version numbers, release\n"
    "    versions, or hardware specifications (e.g. '4', '5', '6', '2026') unless\n"
    "    explicitly requested or present in the context. Keep generic brand or\n"
    "    product names (e.g. 'spotify' or 'photoshop') EXACTLY as requested\n"
    "    so downstream resolver/search tools can match local system inventory.\n"
    "    Do NOT invent/guess CLI arguments (like '--big-picture'). If a mode has\n"
    "    a native URI scheme (e.g. steam://open/bigpicture), use it as the app target.\n"
    '  "news": recency-anchored / current-events / "latest" asks (a NEWS index\n'
    "    beats a general web search).\n"
    '  "web": ANY external-knowledge gap -- a fact about the outside world you are\n'
    "    not certain of. When unsure, prefer web; NEVER fabricate facts or citations.\n"
    "  Classify by what the ask NEEDS, never by a keyword.\n"
    '  "needs_location": true when answering REQUIRES the user\'s OWN physical\n'
    "    location -- weather, 'near me' / nearby / local services, directions,\n"
    "    what's on locally, distance-from-here. The pipeline resolves it from the\n"
    "    forwarded client location; if NONE was forwarded it ASKS the user for\n"
    "    their city rather than guessing one. NEVER put a 'my current location' /\n"
    "    '[location]' placeholder in refined_text -- if a real city was forwarded\n"
    "    use it, otherwise leave the place OUT and set this flag. Classify by what\n"
    "    the ask NEEDS. Omit/false otherwise.\n"
    '  "browser_action": true ONLY when the user wants the agent to PERFORM an\n'
    "    INTERACTIVE action ON a website or app -- sign up, log in, set up an\n"
    "    account or price alert, book, fill in + SUBMIT a form, post, or change\n"
    "    settings on a site -- i.e. DO something, not just LOOK UP / find out\n"
    "    information. Keep intent=agent; the browser-capable agent carries the\n"
    "    action out with its live browser. Omit/false for pure research/lookup.\n"
    '  "local_state": true when the answer comes from inspecting THIS computer\'s\n'
    "    OWN live state -- system/hardware (CPU/GPU/memory/disk), running services\n"
    "    or processes, containers, INSTALLED apps/games, recent logs/activity, or\n"
    "    MiOS's own status -- NOT from the web. The pipeline runs LOCAL read tools\n"
    "    (system_status, mios_apps, process_list, ...) and will NOT web-search (a\n"
    "    web search for local machine state returns irrelevant junk -- random\n"
    "    files, dictionaries, brand names). Keep intent=agent.\n"
    "    HYBRID -- local_state and web are NOT mutually exclusive: set BOTH\n"
    "    local_state:true AND web:true when the question names something ON this\n"
    "    machine but ALSO needs knowledge that exists only OFF it -- the\n"
    "    theoretical specs / benchmarks / ratings / latest version / capabilities\n"
    "    of a component you must first IDENTIFY locally (e.g. 'the theoretical AI\n"
    "    performance of MY GPU', 'is my installed X the latest version', 'how does\n"
    "    my CPU compare to ...'). The pipeline then grounds on BOTH the local read\n"
    "    tools AND web_search and combines them -- judge by MEANING, not keywords;\n"
    "    do NOT drop the web half just because the question says 'this/my system'.\n"
    "    Otherwise omit/false for anything that needs EXTERNAL / web information. A\n"
    "    technology or product\n"
    "    COMPARISON or general research question ('compare X vs Y vs Z', 'best\n"
    "    tool for ...', 'which database for ...') is NOT local_state even if it\n"
    "    mentions caches, databases, or systems -- it needs external knowledge.\n"
    '  "inventory_filter": ONLY with local_state -- when the question targets a\n'
    "    SPECIFIC category/kind of installed thing ('what GAMES do I have',\n"
    "    'list my browsers', 'show installed editors'), the short substring to\n"
    "    filter the app inventory by (e.g. 'games', 'browser', 'editor'). Lets\n"
    "    the pipeline pull a SMALL focused list instead of the whole inventory.\n"
    "    OMIT for a general 'what's installed / list all apps'. Your choice of\n"
    "    word, not a fixed list.\n"
    '  "state_scope": ONLY with local_state. "live" = what is OPEN / RUNNING NOW\n'
    "    (open windows, running apps/processes, active containers, current\n"
    '    CPU/GPU/mem/disk use); "inventory" = what is INSTALLED on disk\n'
    '    (apps/games); omit or "both" = a general system overview. Routes which\n'
    "    local read tools fire -- e.g. 'what's open' -> live -> the OPEN WINDOWS,\n"
    "    not the whole installed-app catalogue. Classify the question's MEANING,\n"
    "    not by keywords.\n"
    '  "domain_type": "internal" | "external" | "both" -- the FUNDAMENTAL domain\n'
    "    of the request. internal = answered or done ENTIRELY on THIS machine (a\n"
    "    local_state read OR a local action/dispatch); external = answered ENTIRELY\n"
    "    from the web / outside world (research, news, lookups, comparisons of\n"
    "    EXTERNAL products); both = genuinely needs a LOCAL part AND an EXTERNAL\n"
    "    part together (e.g. 'compare MY installed GPUs to the latest online\n"
    "    benchmarks', 'check my running services then look up each one's newest\n"
    "    version', 'what games do I have and which got the best reviews this\n"
    "    year'). When both: ALSO set intent=multi_task and put the LOCAL facet(s)\n"
    "    and the EXTERNAL facet(s) as SEPARATE tasks -- mark each LOCAL facet with\n"
    '    "local_state": true and each EXTERNAL facet with "web": true -- so they\n'
    "    run CONCURRENTLY and a synthesis combines them. Classify by what the\n"
    "    request NEEDS, never by keywords.\n"
    '  "intended_outcome": one line -- what the user expects back\n'
    '  "target_agent": a registered sub-agent chosen by role\n'
    '  "hint_tools": [verb names the agent will need -- ONLY names that appear\n'
    "    in the action-verb catalog injected below. If no listed verb clearly\n"
    "    fits, OMIT this field. NEVER invent a verb name (no guessing plausible\n"
    "    names like 'flight_search' / 'journalctl_tail') -- an unlisted name\n"
    "    fails downstream; omitting is always safer than inventing.]\n"
    '  "tool": ONLY for intent=dispatch -- the exact verb name (one of the\n'
    '    verbs listed in the action-verb catalog below)\n'
    '  "args": ONLY for intent=dispatch -- that verb\'s arguments as a JSON\n'
    "    object, using the concrete target the user named\n"
    '  "reply": ONLY when intent=chat -- your short direct reply\n'
    '  "tasks": ONLY when intent=multi_task -- one entry per goal\n'
    "\n"
    "Classify by what the request fundamentally NEEDS, never by keywords:\n"
    "  chat = the user only wants conversation; the answer is already\n"
    "    fully contained in ordinary dialogue -- nothing must be looked\n"
    "    up, fetched, computed, or done on the machine. Emit reply.\n"
    "  dispatch = ONE single, concrete machine ACTION that maps to exactly one\n"
    "    of the verbs listed below: an OS-control action on a NAMED target\n"
    "    (launch / open / close / focus / move / resize a SPECIFIC app or window;\n"
    "    open a SPECIFIC URL) -- EXACTLY ONE target. If the request names MORE THAN\n"
    "    ONE distinct app/action to act on, it is NOT dispatch -- use intent=agent\n"
    "    so the agent performs EACH in turn (dispatch fires only one and drops the\n"
    "    rest). OR a STANDING/RECURRING request -- 'do X every N\n"
    "    minutes/hours', 'each day', 'keep me updated on X', 'check X regularly'\n"
    "    -> the `schedule` verb (args: prompt=the task, every=the interval). A\n"
    "    request that says to REPEAT on an interval is `schedule`, NOT one-shot\n"
    "    research, even if X itself is a research topic. Emit `tool` (that verb's\n"
    "    name) and `args` (its\n"
    "    arguments). For the target, use ONLY the bare app / window / URL NAME\n"
    "    the user named -- STRIP conversational filler ('for me', 'on my pc',\n"
    "    'please', 'now', 'real quick'): 'focus Spotify for me on my pc'\n"
    "    -> tool=focus_window args={title:'Spotify'}. Use dispatch ONLY\n"
    "    when the target is a concrete identifier that can be passed straight\n"
    "    to the verb -- if the target is vague ('the best browser', 'highest-\n"
    "    rated game') or the request needs lookup / research / several steps,\n"
    "    use agent instead. For a plain launch/open prefer launch_app or\n"
    "    open_app -- the fast-path itself confirms the action by diffing a\n"
    "    before/after window enumeration, so no separate verify verb is needed.\n"
    "  agent = the user wants something DONE on this computer, or KNOWN\n"
    "    from information not already present in this conversation. The\n"
    "    agent owns the tools (system control, local file search, web\n"
    "    search/extract) and must USE them rather than guess or refuse.\n"
    "  multi_task = the request needs SEVERAL INDEPENDENT pieces of work that\n"
    "    can each run on their own with NO shared result. Use it when EITHER:\n"
    "    (a) the user lists several distinct goals in one message ('open chrome\n"
    "    AND list my games AND remind me at 3pm'), OR (b) a SINGLE topic spans\n"
    "    clearly SEPARABLE facets that benefit from concurrent research AND a\n"
    "    plain 'agent' single loop would have to serialise them (e.g. compare\n"
    "    several named items; cover distinct regions/angles the user named). In\n"
    "    case (b) split into 2-4 facets, one tasks entry each, so they research\n"
    "    CONCURRENTLY and a synthesis combines them. Do NOT split a single\n"
    "    coherent question that one agent loop answers well ('tell me about X',\n"
    "    'what is Y') -- that is intent=agent, not multi_task. A narrow\n"
    "    single-fact ask is NEVER multi_task. Emit a tasks array (>=2 entries).\n"
    "    CRITICAL -- the BOTH case (domain_type=both): when ONE request mixes a\n"
    "    LOCAL / this-machine part AND an EXTERNAL / web part, you MUST emit\n"
    "    intent=multi_task with the two as SEPARATE tasks so they run\n"
    "    concurrently -- one task for the LOCAL facet (set that task's\n"
    "    \"local_state\": true) and one for the EXTERNAL/web facet (set that\n"
    "    task's \"web\": true). NEVER collapse it to a single intent=agent that\n"
    "    only does the local half and INVENTS the web half. Example: 'what GPU is\n"
    "    in this machine and what are the newest GPUs released this year' ->\n"
    "    intent=multi_task, domain_type=both, tasks=[\n"
    "      {\"title\":\"this machine's GPU\",\"local_state\":true,\"refined_text\":\n"
    "       \"report the installed GPU model from local system state\"},\n"
    "      {\"title\":\"newest GPUs 2026\",\"web\":true,\"refined_text\":\n"
    "       \"newest GPUs released in 2026\"}].\n"
    "    multi_task is for INDEPENDENT work only -- goals or facets that could\n"
    "    each run on their own, none needing another's RESULT (run as parallel\n"
    "    tool calls). When a single goal's later step instead CONSUMES an\n"
    "    earlier step's output, that is NOT multi_task: it is one agent running\n"
    "    the standard tool-calling loop, issuing tool calls in order so the\n"
    "    final action uses the RESOLVED value, not a description of it. Classify\n"
    "    that agent and let the loop sequence it.\n"
    "  Default to agent whenever the request is not purely conversation. When\n"
    "  in doubt between chat, dispatch, and agent, choose agent -- over-\n"
    "  delegating is safer than under-delegating. Use dispatch ONLY when the\n"
    "  target is a concrete identifier; if any arg would be vague, use agent.\n"
    "\n"
    "GROUNDING (no fabrication): when answering needs information not already\n"
    "in this conversation -- anything external or current the agent would look\n"
    "up rather than already know -- classify it agent so the agent FETCHES it\n"
    "with the matching tool. Never invent facts, figures, or sources in this\n"
    "JSON. For intent=chat, emit a brief natural reply; if you cannot produce\n"
    "one, still emit intent=chat and the pipeline will generate the reply.\n"
    "The agent chooses the tool by purpose, not by keyword. Never address the\n"
    "operator by a personal name they did not give; use no name rather than a\n"
    "guessed one.\n"
    "\n"
    "LANGUAGE: write refined_text, intended_outcome, and reply in ENGLISH\n"
    "by default. Use another language ONLY when the operator's own message\n"
    "is clearly written in that language -- then keep every human-readable\n"
    "value in that ONE language. Never drift to a language the operator did\n"
    "not use. JSON keys + verb/tool names stay as-is (identifiers).\n"
)

def _primary_verb_param(tool: str) -> str:
    """Derive primary argument name dynamically from SSOT verb catalog schema."""
    canonical = _resolve_verb_key(tool) if _resolve_verb_key else tool
    vcfg = _VERB_CATALOG.get(canonical) or _VERB_CATALOG.get(tool) or {}
    params = vcfg.get("params")
    if isinstance(params, dict) and params:
        return next(iter(params.keys()))
    sig = vcfg.get("sig")
    if isinstance(sig, str) and sig.strip():
        first = sig.split(",")[0].split("=")[0].strip().rstrip("?").strip()
        if first:
            return first
    return "name"

def _salvage_refine_dispatch(content: str) -> dict | None:
    if not content:
        return None
    m = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if m:
        try:
            obj = _loads_lenient(m.group(0))
            if isinstance(obj, dict) and obj.get("intent"):
                return obj
        except Exception:
            pass
    verbs = sorted(_FASTPATH_VERBS, key=len, reverse=True)
    if not verbs:
        return None
    alt = "|".join(re.escape(v) for v in verbs)
    call = re.search(r"(?<![A-Za-z0-9_])(" + alt + r")\s*\(\s*([^)]*)\)", content)
    if not call:
        return None
    tool = call.group(1)
    inner = (call.group(2) or "").strip()
    args: dict = {}
    for km in re.finditer(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^,]+)", inner):
        k = km.group(1).strip()
        v = km.group(2).strip().strip("\"'").strip()
        if k and v:
            args[k] = v
    if not args and inner:
        val = inner.strip().strip("\"'").strip()
        if val:
            args[_primary_verb_param(tool)] = val
    if not args:
        return None
    return {"intent": "dispatch", "tool": tool, "args": args, "_salvaged": True}

async def refine_intent(user_text: str,
                        history: list = None,
                        on_token: Optional[Callable[[str, bool], Any]] = None) -> Optional[dict]:
    if not REFINE_ENABLED or not user_text or not user_text.strip():
        return None
    agents_summary = "\n".join(
        f"  - {n}: role={c.get('role','?')} "
        f"strengths={','.join(c.get('strengths') or [])[:80]}"
        for n, c in _AGENT_REGISTRY.items()
    )
    static_parts = [
        _REFINE_SYSTEM_LITE,
        _env_grounding_static(),
        f"Registered sub-agents:\n{agents_summary}"
    ]
    if _OS_CONTROL_VERBS_RENDERED:
        static_parts.append(
            "Action-verb catalog (for intent=dispatch -- map a single\n"
            "concrete app / window / URL action, OR a recurring 'every N' /\n"
            "'each day' standing request, to exactly ONE of these):\n"
            + _OS_CONTROL_VERBS_RENDERED
        )
    if _VERB_CATALOG:
        static_parts.append(
            "VALID verb names -- for `hint_tools` (and `tool`) use ONLY "
            "these EXACT names; NEVER invent a plausible-sounding name (no "
            "'journalctl_tail', 'flight_search', 'system_service_status'). "
            "If none fits, leave hint_tools empty:\n"
            + ", ".join(sorted(_VERB_CATALOG.keys()))
        )

    if resolve_preferences:
        pref = await resolve_preferences(user_text)
        if pref and "app" in pref:
            app_info = pref["app"]
            static_parts.append(
                f"Personalized User Preference matched for '{user_text}':\n"
                f"Target App: {app_info.get('short_name', '')} (id: {app_info.get('app_id', '')})\n"
                "Prefer this app when fulfilling the user's intent if applicable."
            )

    system = "\n\n".join(static_parts) + "\n\n" + _env_grounding_dynamic()
    msgs = [{"role": "system", "content": system}]
    if history:
        for h in history[-2:]:
            if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
                msgs.append({"role": h["role"],
                             "content": mios_tokenize.truncate_to_tokens(
                                 str(h.get("content", "")), 250)})  # WS-A5 seam (was [:200])
    _refine_structured = os.environ.get(
        "MIOS_REFINE_STRUCTURED", "true").strip().lower() not in {"0", "false", "no", "off"}
    _refine_stream_structured = os.environ.get(
        "MIOS_REFINE_STREAM_STRUCTURED", "true").strip().lower() not in {"0", "false", "no", "off"}
    if on_token and not _refine_stream_structured:
        _refine_structured = False

    _u_content = user_text[-1500:]
    if not _refine_structured and not on_token:
        _u_content += " /no_think"
    msgs.append({"role": "user", "content": _u_content})

    payload = {
        "model": REFINE_MODEL,
        "messages": msgs,
        "temperature": 0.0,
        "max_tokens": REFINE_MAX_TOKENS,
        "stream": bool(on_token),
    }
    if _refine_structured:
        _rv = sorted(_VERB_CATALOG.keys())
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "mios_refine", "strict": True, "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "intent": {"type": "string",
                               "enum": ["chat", "dispatch", "agent", "multi_task"]},
                    "refined_text": {"type": "string"},
                    "news": {"type": "boolean"},
                    "web": {"type": "boolean"},
                    "local_state": {"type": "boolean"},
                    "needs_location": {"type": "boolean"},
                    "browser_action": {"type": "boolean"},
                    "domain_type": {"type": ["string", "null"]},
                    "state_scope": {"type": ["string", "null"]},
                    "inventory_filter": {"type": ["string", "null"]},
                    "intended_outcome": {"type": ["string", "null"]},
                    "target_agent": {"type": ["string", "null"]},
                    "hint_tools": {"type": "array",
                                   "items": {"type": "string", "enum": _rv}},
                    "tool": {"type": ["string", "null"], "enum": _rv + [None]},
                    "args": {"type": ["object", "null"], "additionalProperties": True},
                    "reply": {"type": ["string", "null"]},
                    "tasks": {"type": ["array", "null"], "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "title": {"type": "string"},
                            "refined_text": {"type": "string"},
                            "web": {"type": "boolean"},
                            "local_state": {"type": "boolean"}},
                        "required": ["title", "refined_text", "web", "local_state"]}}},
                "required": ["intent", "refined_text", "news", "web", "local_state",
                             "needs_location", "browser_action", "domain_type",
                             "state_scope", "inventory_filter", "intended_outcome",
                             "target_agent", "hint_tools", "tool", "args", "reply",
                             "tasks"]}}}
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    url = f"{REFINE_ENDPOINT}/v1/chat/completions"
    t0 = time.time()
    async def _call_on_token(token_val: str, is_re: bool):
        if on_token:
            try:
                if inspect.iscoroutinefunction(on_token):
                    await on_token(token_val, is_re)
                else:
                    on_token(token_val, is_re)
            except Exception as _cb_err:
                log.warning("Error in on_token: %s", _cb_err)

    body = None
    for _attempt in range(REFINE_ATTEMPTS):
        try:
            if on_token:
                async with httpx.AsyncClient(timeout=REFINE_TIMEOUT_S) as s:
                    async with s.stream("POST", url, json=payload, headers={"Content-Type": "application/json"}) as r:
                        if r.status_code != 200:
                            err_txt = await r.aread()
                            log.warning("refine stream: backend %s in %.1fs: %s", r.status_code, time.time() - t0, err_txt[:200])
                            return None
                        content_chunks = []
                        in_think = False
                        buffer = ""
                        async for chunk in r.aiter_text():
                            buffer += chunk
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()
                                if not line:
                                    continue
                                if line.startswith("data:"):
                                    data_str = line[5:].strip()
                                    if data_str == "[DONE]":
                                        continue
                                    try:
                                        data = json.loads(data_str)
                                        choices = data.get("choices") or []
                                        if not choices:
                                            continue
                                        delta = choices[0].get("delta") or {}

                                        r_val = delta.get("reasoning_content") or delta.get("reasoning")
                                        if r_val:
                                            await _call_on_token(r_val, True)
                                            continue

                                        c_val = delta.get("content") or ""
                                        if c_val:
                                            temp = c_val
                                            if "<think>" in temp:
                                                in_think = True
                                                parts = temp.split("<think>", 1)
                                                if parts[0]:
                                                    await _call_on_token(parts[0], False)
                                                if parts[1]:
                                                    await _call_on_token(parts[1], True)
                                                continue
                                            if "</think>" in temp:
                                                in_think = False
                                                parts = temp.split("</think>", 1)
                                                if parts[0]:
                                                    await _call_on_token(parts[0], True)
                                                if parts[1]:
                                                    content_chunks.append(parts[1])
                                                    await _call_on_token(parts[1], False)
                                                continue

                                            if in_think:
                                                await _call_on_token(c_val, True)
                                            else:
                                                content_chunks.append(c_val)
                                                await _call_on_token(c_val, False)
                                    except Exception as e:
                                        pass
                        full_content = "".join(content_chunks)
                        body = {
                            "choices": [{
                                "message": {
                                    "role": "assistant",
                                    "content": full_content
                                }
                            }]
                        }
                        break
            else:
                async with httpx.AsyncClient(timeout=REFINE_TIMEOUT_S) as s:
                    r = await s.post(url, json=payload,
                                     headers={"Content-Type": "application/json"})
                    if r.status_code != 200:
                        log.warning("refine: backend %s in %.1fs: %s", r.status_code, time.time() - t0, r.text[:200])
                        return None
                    body = r.json()
                    break
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            log.warning("refine: timeout/http error after %.1fs (attempt %d/%d): %s",
                        time.time() - t0, _attempt + 1, REFINE_ATTEMPTS, e)
            if _attempt + 1 >= REFINE_ATTEMPTS:
                return None
            if _over_global_ceiling():
                log.warning("refine: host over ceiling -> skip retry, degrade-open")
                return None
            continue
        except Exception as e:
            log.warning("refine unexpected error: %s", e)
            return None
    if body is None:
        return None
    elapsed = time.time() - t0
    choices = body.get("choices") or []
    msg = (choices[0].get("message") if choices else {}) or {}
    content = (msg.get("content") or "").strip()
    if not content:
        log.warning("refine: %.1fs empty_content", elapsed)
        return None
    content = re.sub(r"<think>.*?</think>\s*", "", content,
                     flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r"^\s*```(?:json)?\s*\n?", "", content)
    content = re.sub(r"\n?```\s*$", "", content)
    try:
        parsed = _loads_lenient(content)
    except json.JSONDecodeError as e:
        parsed = _loads_lenient(content)
        if isinstance(parsed, dict) and parsed.get("intent"):
            log.warning("refine: %.1fs parse_fail REPAIRED (%s) -> intent=%s",
                        elapsed, e.msg, parsed.get("intent"))
        else:
            parsed = _salvage_refine_dispatch(content)
            if parsed is not None:
                log.warning(
                    "refine: %.1fs parse_fail SALVAGED prose -> dispatch %s args=%s",
                    elapsed, parsed.get("tool"), parsed.get("args"))
            else:
                log.warning("refine: %.1fs parse_fail: %s; preview=%r",
                            elapsed, e, content[:200])
                return None
    if not isinstance(parsed, dict):
        log.warning("refine: %.1fs not_dict type=%s",
                    elapsed, type(parsed).__name__)
        return None
    log.info("refine: %.1fs [%s] intent=%s domain=%s target=%s",
             elapsed, REFINE_MODEL, parsed.get("intent"),
             parsed.get("domain_type"), parsed.get("target_agent"))
    parsed["_elapsed_s"] = round(elapsed, 1)
    parsed["_model"] = REFINE_MODEL
    parsed["_endpoint"] = REFINE_ENDPOINT
    _news = parsed.get("news")
    parsed["news"] = (_news is True) or (
        isinstance(_news, str) and _news.strip().lower() in {"true", "1", "yes"})
    _nl = parsed.get("needs_location")
    parsed["needs_location"] = (_nl is True) or (
        isinstance(_nl, str) and _nl.strip().lower() in {"true", "1", "yes"})
    _ba = parsed.get("browser_action")
    parsed["browser_action"] = (_ba is True) or (
        isinstance(_ba, str) and _ba.strip().lower() in {"true", "1", "yes"})
    _ht = parsed.get("hint_tools")
    if isinstance(_ht, list) and _ht and _VERB_CATALOG:
        _folded_keys = {v.replace("-", "_").rstrip("s") for v in _VERB_CATALOG}
        _kept, _dropped = [], []
        for _h in _ht:
            if not isinstance(_h, str) or not _h.strip():
                continue
            _hs = _h.strip()
            _fold = _hs.lower().replace("-", "_").rstrip("s")
            if (_resolve_verb_key(_hs) in _VERB_CATALOG
                    or _fold in _VERB_CATALOG or _fold in _folded_keys):
                _kept.append(_hs)
            else:
                _dropped.append(_hs)
        parsed["hint_tools"] = _kept
        if _dropped:
            log.info("refine: dropped %d hallucinated hint_tool(s): %s",
                     len(_dropped), _dropped)
    if not parsed["browser_action"] and _BROWSER_ACTION_ALT:
        try:
            import re as _re_brd
            _utb = user_text or ""
            if _re_brd.search(r'https?://', _utb) and _re_brd.search(
                    r'\b(?:' + _BROWSER_ACTION_ALT + r')\b', _utb, _re_brd.I):
                parsed["browser_action"] = True
                if parsed.get("intent") in ("dispatch", "chat"):
                    parsed["intent"] = "agent"
        except Exception:
            pass

    _ls = parsed.get("local_state")
    parsed["local_state"] = (_ls is True) or (
        isinstance(_ls, str) and _ls.strip().lower() in {"true", "1", "yes"})
    _dt = parsed.get("domain_type")
    _dt = _dt.strip().lower() if isinstance(_dt, str) else ""
    if _dt not in ("internal", "external", "both"):
        _dt = "internal" if parsed.get("local_state") else "external"
    if parsed.get("local_state") and (parsed.get("web") or parsed.get("news")):
        _dt = "both"
    parsed["domain_type"] = _dt
    try:
        import re as _re_vr
        _utl = user_text or ""
        if _WEB_SEARCH_TRIGGERS and _WEB_SEARCH_CONTEXTS:
            _wt = "|".join(_re_vr.escape(p) for p in _WEB_SEARCH_TRIGGERS)
            _wc = "|".join(_re_vr.escape(p) for p in _WEB_SEARCH_CONTEXTS)
            if _re_vr.search(rf'\b(?:{_wt})\b.{{0,40}}\b(?:{_wc})\b', _utl, _re_vr.I):
                parsed["web"] = True
                parsed["local_state"] = False
                if parsed.get("intent") == "chat":
                    parsed["intent"] = "agent"
        if _REMEMBER_TRIGGERS and "remember" in (_VERB_CATALOG or {}):
            _rt = "|".join(_re_vr.escape(p) for p in _REMEMBER_TRIGGERS)
            _rm = _re_vr.match(rf'\s*(?:please\s+)?(?:{_rt})(?:\s+that)?\s+(.+)',
                               _utl, _re_vr.I)
            if _rm:
                _fact = _re_vr.split(r',?\s*\b(?:then|and then)\b', _rm.group(1),
                                     maxsplit=1)[0].strip().rstrip('.')
                if _fact:
                    parsed["intent"] = "dispatch"
                    parsed["tool"] = "remember"
                    parsed["args"] = {"fact": _fact}
    except Exception:
        pass
    if parsed.get("intent") == "chat":
        _ut = (user_text or "").strip()
        _looks_actionable = (
            _ut.startswith(("mios-", "/", "./", "sudo ", "systemctl ",
                            "podman ", "docker ", "git ", "curl ",
                            "wsl.exe", "powershell.exe", "cmd.exe"))
            or "://" in _ut
        )
        if _looks_actionable:
            log.info(
                "refine: chat promoted to dispatch "
                "(text starts with verb/URL token)")
            parsed["intent"] = "dispatch"
            parsed.pop("reply", None)
    if parsed.get("intent") == "multi_task":
        tasks = parsed.get("tasks") or []
        if not isinstance(tasks, list) or len(tasks) < 2:
            log.info(
                "refine: multi_task degraded to agent (tasks=%s)",
                len(tasks) if isinstance(tasks, list) else "non-list",
            )
            parsed["intent"] = "agent"
            parsed["_multi_step"] = True
            parsed.pop("tasks", None)
    _ut = (user_text or "").strip()
    _os_dispatch = (parsed.get("intent") == "dispatch"
                    and str(parsed.get("tool") or "").strip()
                    in _FASTPATH_VERBS)
    if (parsed.get("intent") in ("chat", "dispatch")
            and len(_ut) > REFINE_PROMOTE_CHARS
            and not _os_dispatch):
        log.info(
            "refine: %s promoted to agent (user_text=%d chars > %d)",
            parsed["intent"], len(_ut), REFINE_PROMOTE_CHARS)
        parsed["intent"] = "agent"
        parsed.pop("reply", None)
    if parsed.get("intent") == "dispatch":
        _is_os = str(parsed.get("tool") or "").strip() in _FASTPATH_VERBS
        _args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        _wordy = False
        for v in _args.values():
            if isinstance(v, str) and len(v.strip().split()) > REFINE_DISPATCH_ARG_MAX_WORDS:
                _wordy = True
                break
        if _wordy and not _is_os:
            log.info(
                "refine: dispatch promoted to agent "
                "(arg value contained a multi-word semantic phrase)")
            parsed["intent"] = "agent"
    _det = _deterministic_action_route(user_text)
    if _det is not None and parsed.get("intent") != "dispatch":
        log.info("refine: deterministic OS-action override %s args=%s (was intent=%s)",
                 _det["tool"], _det["args"], parsed.get("intent"))
        parsed = _det
    if (parsed.get("intent") == "dispatch" and not parsed.get("_deterministic")
            and _ROUTING_ENABLE and _ROUTING_DOMAINS):
        _gdom = _routed_domain_var.get(None)
        if _gdom is None:
            try:
                _gdom = await _route_domain(user_text)
            except Exception:  # noqa: BLE001
                _gdom = None
        _gtool = str(parsed.get("tool") or "").strip()
        _gverbs = (set((_ROUTING_DOMAINS.get(_gdom) or {}).get("verbs") or [])
                   if _gdom else set())
        if _gdom and _gverbs and _gtool and _gtool not in _gverbs:
            log.info("refine: cross-domain mis-dispatch (tool=%s NOT in routed domain "
                     "%s) -> agent (anti-fabrication)", _gtool, _gdom)
            parsed["intent"] = "agent"
            parsed.pop("tool", None)
            parsed.pop("args", None)
            parsed.pop("reply", None)
            parsed["refined_text"] = user_text
            parsed.pop("hint_tools", None)
            parsed["web"] = False
            parsed["news"] = False
    _db_fire(_db_post(_db_create("event", {
        "source": "mios-agent-pipe",
        "kind": "refine",
        "severity": "info",
        "summary": str(parsed.get("intent", "?"))[:120],
        "payload": parsed,
    }, now_fields=("ts",))))
    return parsed

async def _critic_refine_agent(
    raw: str,
    user_text: str,
    refined: Optional[dict],
    session_id: Optional[str],
    *,
    client,
    target_endpoint: str,
    headers: dict,
    base_body: dict,
) -> str:
    if not (CRITIC_REFINE_ENABLED and DCI_ENABLED):
        return raw
    if not raw or len(raw) < CRITIC_REFINE_MIN_CHARS:
        return raw
    envelope = {
        "intent": (refined or {}).get("intent", "agent"),
        "answer": raw[:4000],
        "user_text": (user_text or "")[:1000],
    }
    try:
        act = await dci_critic_pass(user_text, envelope, session_id=session_id)
    except Exception as e:
        log.warning("critic-refine: critic pass failed: %s", e)
        return raw
    if not act or not (
            act.get("act") in ("challenge", "ask")
            and float(act.get("confidence", 0.0)) >= DCI_FLOW_TRIGGER_CONF):
        return raw  # critic satisfied -> answer stands (common case)
    concern = str(act.get("content") or "").strip()[:600]
    if not concern:
        return raw
    refine_body = dict(base_body)
    refine_body["stream"] = False
    refine_body["messages"] = list(refine_body.get("messages") or []) + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content":
            f"A reviewer raised this concern about your answer: {concern}\n"
            f"Revise your answer to fully address it. Be correct and "
            f"concise; do not mention this review."},
    ]
    out = raw
    for _ in range(max(1, CRITIC_REFINE_MAX)):
        try:
            r = await client.post(
                f"{target_endpoint}/chat/completions",
                content=json.dumps(refine_body).encode("utf-8"),
                headers=headers,
            )
            if r.status_code != 200:
                break
            j = r.json()
            ch = j.get("choices") or []
            new = (str((ch[0].get("message") or {}).get("content") or "")
                   if ch else "")
            if new.strip():
                out = new
                _emit_session_event({
                    "source": "mios-agent-pipe",
                    "kind": "critic_refine",
                    "severity": "info",
                    "summary": (f"refined on {act.get('act')} "
                                f"conf={act.get('confidence')}"),
                    "payload": {"concern": concern[:200]},
                }, session_id)
                break
        except Exception as e:
            log.warning("critic-refine: re-invoke failed: %s", e)
            break
    return out
