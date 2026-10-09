#!/usr/bin/env python3
# AI-hint: Near-runtime agent-pipe import, exact re-export identity and dependency-injection gate with substitution negative controls.
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md
"""Stub-and-import gate: prove server.py imports cleanly with all DI wired (refactor R0+)."""

import os
import importlib
import functools
import sys
import types
from unittest import mock

_fails = 0

def check(name, cond, detail=""):
    global _fails
    if not cond:
        _fails += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def _resolve_toml():
    """Point MIOS_TOML at the repo's vendor mios.toml if present (repo root = 4
    levels up from this file: usr/lib/mios/agent-pipe/), so the import exercises
    the REAL config parse on any host. Harmless if absent (readers degrade)."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    toml = os.path.join(repo, "usr", "share", "mios", "mios.toml")
    if "MIOS_TOML" not in os.environ and os.path.isfile(toml):
        os.environ["MIOS_TOML"] = toml

def _install_stubs():
    for name in ("websockets", "uvicorn"):
        sys.modules.setdefault(name, mock.MagicMock(name=name))

    try:
        import httpx
    except ImportError:
        MockHTTPError = type("MockHTTPError", (Exception,), {})
        httpx_mock = mock.MagicMock(name="httpx")
        httpx_mock.HTTPError = MockHTTPError
        sys.modules["httpx"] = httpx_mock

    fastapi = types.ModuleType("fastapi")

    class _App:
        def __getattr__(self, _attr):
            def _decorator_factory(*_a, **_k):
                def _wrap(fn=None):
                    return fn if fn is not None else (lambda f: f)
                return _wrap
            return _decorator_factory

    fastapi.FastAPI = lambda *a, **k: _App()
    fastapi.APIRouter = lambda *a, **k: _App()
    fastapi.Request = object
    fastapi.WebSocket = object
    fastapi.BackgroundTasks = object
    responses = types.ModuleType("fastapi.responses")
    for _c in ("HTMLResponse", "JSONResponse", "RedirectResponse",
               "Response", "StreamingResponse"):
        setattr(responses, _c, type(_c, (), {"__init__": lambda self, *a, **k: None}))
    fastapi.responses = responses
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", responses)

_EXTRACTED = {
    "mios_pipe.kernel.config": ["_toml_section", "_cfg_num", "PORT", "_STACK_MODEL"],
    "mios_pipe.routing.provider_translate": ["_scrub_schema", "_oai_msgs_to_anthropic"],
    "mios_pipe.routing.sse": ["_sse_chunk", "_sse_status", "_stream_answer", "_iter_answer_chunks"],
    "mios_pipe.routing.dci": ["run_dci_flow", "critic_then_maybe_flow"],
    "mios_endpoints": ["_endpoint_is_llamacpp"],
    "mios_pipe.context.grounding": ["_env_grounding", "_env_block", "_host_timezone", "_current_year"],
    "mios_pipe.lifecycle.verity": ["polish_response", "_strip_ungrounded_figures", "_clarify_question"],
    "mios_skills": ["execute_skill", "_make_schema_strict", "_skill_render_args",
                    "_skill_invocation_open", "_skill_invocation_close",
                    "_skill_attribute_tool_call", "_SKILL_INV_META",
                    "_slug_for_skill", "_render_skill_md", "_write_skill_md_fire"],
    "mios_pipe.routing.fanout": ["_pick_fanout_agents"],
    "mios_pipe.routing.routing": ["_load_routing_domains", "_load_routing_phrases",
                     "_load_launch_fillers", "_deterministic_action_route"],
    "mios_pipe.routing.agentreg": ["_load_agent_registry", "_load_node_pool", "_build_agent_engines",
                      "_agent_lane", "_render_agent_catalog", "_role_system",
                      "_dedup_pool_by_target"],
    "mios_pipe.memory.knowledge": ["_store_knowledge", "_recall_knowledge", "_recency_mult",
                       "_rls_owner", "_recall_agent_memory", "kg_lookup"],
    "mios_pipe.routing.refine": ["refine_intent", "_salvage_refine_dispatch", "_REFINE_SYSTEM",
                    "_critic_refine_agent"],
    "mios_pipe.routing.planner": ["decompose_intent", "_topological_order", "_dag_levels", "_PLANNER_SYSTEM"],
    "mios_pipe.routing.toolexec": ["_exec_tool_calls", "_rescue_tool_calls", "_cap_verb_result",
                      "_format_tool_error", "_record_mcp_tool_call"],
    "mios_pipe.routing.agent_call": ["_call_agent_complete", "_call_agent_complete_inner",
                        "_call_agent_stream_inner",
                        "_kv_base", "_kv_filename", "_kv_lock", "_kv_slot_action",
                        "_kv_paging", "_kv_fork", "_rr_eligible", "_rr_slice",
                        "_rr_run",
                        "_trip_breaker", "_num_predict_cap_for"],
    "mios_pipe.routing.secondary_loop": ["_v1_secondary_tool_loop",
                            "_daemon_diagnose", "_TOOL_NUDGE", "_REPLAN_NUDGE",
                            "_tool_call_sig", "_looks_like_disclaimer",
                            "_tmsgs_indicate_failure", "_DISCLAIM_MARKERS"],
    "mios_pipe.routing.web_research": ["_web_research_enrich", "_is_port_open",
                          "_url_has_path", "_clean_web_text",
                          "_anchor_tokens", "_shares_anchor",
                          "_src_turn_key", "_src_turn_init", "_src_record",
                          "_src_collected", "_sources_markdown", "_sources_metadata",
                          "_sources_annotations", "_filter_relevant_sources",
                          "_src_record_from_text", "_harvest_sub_sources"],
    "mios_pipe.access.policy": ["_perm_rank", "_effective_perm", "_agent_rbac_filter",
                    "_dispatch_pdp_reason"],
    "mios_pipe.access.firewall": ["_is_external_url", "_classify_verb_taint",
                      "_session_is_tainted"],
    "mios_pipe.access.hitlflow": ["_action_hash", "_pending_hash", "_hitl_gate",
                      "_classify_approval_reply", "hitl_approve_logic"],
    "mios_dispatch": ["dispatch_mios_verb", "_build_dispatch_cmd",
                      "_template_to_cmd", "_emit_dispatch_dedup_event",
                      "_arg_with_synonyms", "_validate_enum_args",
                      "_dispatch_sandbox_profile", "_sandbox_wrap_cmd"],
    "mios_pipe.routing.dag_exec": ["execute_dag", "_execute_dag_node",
                      "_execute_dag_bounded", "_execute_dag_emitting"],
    "mios_pipe.routing.swarm": ["_agent_dag_from_tasks", "_respond_agent_dag",
                   "_plan_swarm", "_expand_facets"],
    "mios_pipe.routing.native_loop": ["_respond_native_loop_direct", "_respond_local_state",
                         "_format_local_state", "_formulate_web_query",
                         "_formulate_compute_snippet"],
    "mios_pipe.routing.oscontrol": ["_respond_os_control", "_verify_os_action", "_window_diff",
                       "_render_os_control_verbs"],
    "mios_pipe.routing.vision": ["_vision_complete", "_has_client_tools", "_client_tools_complete"],
    "mios_pipe.memory.worker_tools": [],  # populated below if the module exposes stable names
    "mios_pipe.routing.toolsearch": ["_tool_embedding", "_ensure_verb_embeddings",
                        "_refresh_app_inventory", "_cosine", "_verb_embed_text",
                        "_verb_embed_fingerprint"],
    "mios_pipe.kernel.daemons": ["_gossip_loop", "_membership_watch_loop", "_selfimprove_report",
                     "_selfimprove_loop", "_kv_gc_sweep_once", "_kv_gc_loop",
                     "selfimprove_proposals_ep"],
    "mios_pipe.routing.portal": ["_portal_authed", "_portal_token_ok", "_discover_portal_services",
                    "portal_stats_logic", "portal_service_detail_logic",
                    "portal_swarm_logic", "portal_term_ws_logic",
                    "portal_login_page_logic", "portal_login_logic",
                    "portal_page_logic"],
    "mios_pipe.federation.a2a": ["_build_agent_card", "_a2a_jsonrpc_dispatch", "_a2a_verify_principal",
                 "a2a_jsonrpc_logic", "a2a_skills_list_logic", "a2a_dispatch_logic",
                 "passport_verify_logic", "passport_public_key_logic",
                 "a2a_skill_directory", "a2a_context_get", "a2a_jsonrpc",
                 "a2a_jsonrpc_alias", "a2a_peers_reload",
                 "caller_key_revoke",
                 "a2a_agent_card", "a2a_agent_card_legacy", "agent_passport",
                 "agntcy_manifest_wellknown", "a2a_peers_list", "a2a_skills_list",
                 "a2a_dispatch", "passport_verify", "passport_public_key"],
    "mios_pipe.federation.a2a_client": ["_a2a_load_peers", "_a2a_send_message_to_peer",
                        "_a2a_extract_text", "_a2a_self_peer_url",
                        "_a2a_fetch_card", "_a2a_tailnet_candidates"],
    "mios_pipe.identity.principal": ["_passport_canonical_json", "_passport_op_hash",
                           "_passport_load_priv", "_passport_kid",
                           "_passport_load_public", "_passport_sign",
                           "_passport_verify", "_passport_pub_cache",
                           "_passport_load_attempted"],
    "mios_pipe.federation.mcp": ["_mcp_call_tool", "_mcp_render_headers", "_McpStdioClient",
                 "mcp_clients", "mcp_tools_list", "mcp_dispatch"],
    "mios_pipe.federation.http_caps": ["_skill_to_mcp_resource", "_recipe_to_mcp_resource",
                       "_verb_to_mcp_resource",
                       "v1_peers", "list_resources", "read_resource"],
    "mios_pipe.routing.chat": ["chat_completions_logic", "responses_api_logic",
                  "_budget_num", "_budget_bucket", "_budget_window_total",
                  "_budget_debit", "_budget_prune_inflight", "_budget_admit",
                  "_budget_release_inflight",
                  "_BUDGET_TOML", "_BUDGET_LEDGER", "_BUDGET_LEDGER_MAX",
                  "_BUDGET_AUTO_INFLIGHT", "BUDGET_CONV_TOKEN_CEIL",
                  "BUDGET_AUTO_TOKEN_CEIL", "BUDGET_AUTO_MAX_INFLIGHT",
                  "BUDGET_WINDOW_S", "BUDGET_ENABLE", "BUDGET_PER_TURN_ESTIMATE",
                  "BUDGET_INFLIGHT_TTL_S"],
    "mios_pipe.routing.cua": ["v1_computer_use_logic", "_cua_extract_png", "_cua_screenshot_uri",
                 "_cua_vlm_json", "_cua_loop"],
    "mios_pipe.kernel.clusterhealth": ["cluster_health_logic", "scheduler_state_logic",
                           "health_logic", "_probe_one_endpoint",
                           "_lane_sched_stats", "_kernel_managers_detail"],
    "mios_pipe.routing.verbcatalog": ["_load_verb_catalog", "_resolve_verb_key",
                         "_verb_to_openai_tool"],
    "mios_pipe.routing.turn": ["_extract_last_user_text", "_pick_agent", "_live_agent_names"],
    "mios_pipe.routing.lanes_resolver": ["_pick_tool_backend", "_lane_resolver", "_heavy_lane_up"],
    "mios_pipe.scheduler.sched": ["PriorityGate", "_lane_tool_cap", "_agent_offload_engine",
                   "_resolve_autonomous_priority", "_sched_priority", "_lane_sem_key"],
    "mios_pipe.context.promptfmt": ["_council_role_lens", "_format_satisfaction_block", "_format_tool_history",
                       "_build_agent_hint", "_multi_task_preamble"],
    "mios_pipe.context.tokenize": ["_usage_estimate"],
    "mios_pipe.routing.classify": ["classify_intent", "_route_domain"],
    "mios_pipe.routing.reflect": ["_inline_satisfaction_check", "reflect_on_step_failure",
                     "_recent_satisfaction_verdicts", "_recent_tool_history",
                     "_judge_answer_satisfied"],
}

def _ssot_flag(env: str, key: str, default: str, server) -> str:
    """The [dispatch] value as the SSOT cascade resolves it: env wins, then the TOML."""
    return str(os.environ.get(env) or server._DISPATCH_TOML.get(key, default)).strip().lower()

_EXPORT_ALIASES = {
    ("mios_pipe.routing.provider_translate", "_scrub_schema"): "scrub_schema",
    ("mios_pipe.routing.provider_translate", "_oai_msgs_to_anthropic"): "oai_msgs_to_anthropic",
}
_TRACED_EXPORTS = {
    ("mios_pipe.routing.refine", "refine_intent"),
    ("mios_pipe.routing.swarm", "_plan_swarm"),
}

def _same_export(server, module, name: str, *, source_name=None, wrapped=False) -> bool:
    """Aliases and compatibility shims must expose the exact declared object.

    __module__ describes where an object was defined, not the module re-exporting
    it; matching that string also accepts a different object with a copied label.
    """
    expected = getattr(module, source_name or name, None)
    actual = getattr(server, name, None)
    if wrapped:
        actual = getattr(actual, "__wrapped__", None)
    return expected is not None and actual is expected

def _check_export_identity_controls():
    canonical = lambda: None
    spoof = lambda: None
    spoof.__module__ = canonical.__module__
    source = types.SimpleNamespace(export=canonical)
    check("export identity accepts an exact re-export", _same_export(types.SimpleNamespace(export=canonical), source, "export"))
    check("export identity rejects a copied origin label", not _same_export(types.SimpleNamespace(export=spoof), source, "export"))
    check("export identity rejects a missing server binding", not _same_export(types.SimpleNamespace(), source, "export"))
    check("export identity rejects a missing declared export", not _same_export(source, types.SimpleNamespace(), "export"))
    check("export identity rejects None placeholders", not _same_export(types.SimpleNamespace(export=None), types.SimpleNamespace(export=None), "export"))
    value = []
    check("export identity accepts shared data", _same_export(types.SimpleNamespace(export=value), types.SimpleNamespace(export=value), "export"))
    check("export identity rejects equal but separate data", not _same_export(types.SimpleNamespace(export=[]), types.SimpleNamespace(export=[]), "export"))
    @functools.wraps(canonical)
    def traced():
        return canonical()
    alias = types.SimpleNamespace(alias=canonical)
    check("export identity accepts an explicit source alias", _same_export(source, alias, "export", source_name="alias"))
    check("export identity rejects a wrong source alias", not _same_export(source, alias, "export", source_name="missing"))
    check("export identity accepts an explicitly traced binding", _same_export(types.SimpleNamespace(export=traced), source, "export", wrapped=True))
    check("export identity rejects an undeclared wrapper", not _same_export(types.SimpleNamespace(export=traced), source, "export"))
    check("export identity rejects a wrong wrapped target", not _same_export(types.SimpleNamespace(export=traced), types.SimpleNamespace(export=spoof), "export", wrapped=True))

def _check_scheduler_wiring(server):
    """The [dispatch] scheduler knobs reach their consumers after server's DI runs."""
    import asyncio
    from mios_pipe import vram_scheduler as vs
    from mios_pipe.scheduler import admission

    want_reclaim = _ssot_flag("MIOS_VRAM_RECLAIM_IDLE", "vram_reclaim_idle", "true",
                              server) not in {"0", "false", "no", "off"}
    check("[dispatch] vram_reclaim_idle reaches the admit path",
          vs.VRAM_RECLAIM_IDLE is want_reclaim,
          f"want {want_reclaim}, got {vs.VRAM_RECLAIM_IDLE}")

    want_research = _ssot_flag("MIOS_NODES_RESEARCH_ONLY", "nodes_research_only", "false",
                               server) in {"1", "true", "yes"}
    got_research = sys.modules["mios_agentreg"].NODES_RESEARCH_ONLY
    check("[dispatch] nodes_research_only reaches the agent registry",
          got_research is want_research, f"want {want_research}, got {got_research}")

    lane_map = vs._parse_lane_priority(
        os.environ.get("MIOS_LANE_PRIORITY") or server._DISPATCH_TOML.get("lane_priority", ""))
    for lane in ("gpu", "cpu", "igpu"):
        want = lane_map.get(lane, lane_map["_default"])
        got = server._dispatch_priority({"lane": lane})
        check(f"[dispatch] lane_priority sets the {lane} dispatch priority",
              got == want, f"want {want}, got {got}")

    gate = server._GLOBAL_PRIORITY_GATE
    cap = gate.stats()["cap"]
    peak = {"now": 0, "max": 0}

    async def _hold():
        async with server._priority_gate(5.0):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.02)
            peak["now"] -= 1

    async def _burst():
        await asyncio.gather(*(_hold() for _ in range(cap + 2)))

    asyncio.run(_burst())
    enabled = server.PRIORITY_QUEUE_ENABLE
    want_peak = cap if enabled else cap + 2
    check("[dispatch] priority_queue_enable engages the global priority gate",
          admission._GLOBAL_PRIORITY_GATE is (gate if enabled else None)
          and peak["max"] == want_peak,
          f"enable={enabled} cap={cap} peak={peak['max']} want={want_peak}")
    check("priority gate released every permit", gate.stats()["in_flight"] == 0,
          str(gate.stats()))

    # task_3484: vram_scheduler dependency injection checks
    vram_deps = [
        ("_over_blade_ceiling", True),
        ("_over_global_ceiling", True),
        ("_is_warm", True),
        ("_blade_vram_budget", True),
        ("VRAM_BUDGET_MB", False),
        ("_resident_cached", True),
        ("_norm_model_tag", True),
        ("VRAM_COLOAD_EST_MB", False),
        ("VRAM_COLOAD_ENABLE", False),
        ("VRAM_COLOAD_RESERVE_MB", False),
        ("_reclaim_idle_vram", True),
        ("_dispatch_num", True),
    ]
    for dep_name, must_be_callable in vram_deps:
        val = getattr(vs, dep_name, None)
        ok = val is not None and (not must_be_callable or callable(val))
        check(f"vram_scheduler.{dep_name} injected and {'callable' if must_be_callable else 'non-None'}",
              ok, f"got {val!r}")

    # task_3484: scheduler.vram configuration checks
    from mios_pipe.scheduler import vram as sv
    check("scheduler.vram received host_stats via configure", sv._host_stats is server._host_stats_cached)
    check("scheduler.vram received endpoint_key via configure", sv._endpoint_key is server._endpoint_key)
    check("scheduler.vram received blade_pool via configure", sv._BLADE_POOL is server._BLADE_POOL)
    check("scheduler.vram received endpoint_blade via configure", sv._ENDPOINT_BLADE is server._ENDPOINT_BLADE)
    check("scheduler.vram received local_blade via configure", sv._LOCAL_BLADE == server._LOCAL_BLADE)
    check("scheduler.vram received vram_budget_mb via configure", sv.VRAM_BUDGET_MB == server.VRAM_BUDGET_MB)
    check("scheduler.vram received admit_load_ceil via configure", sv.ADMIT_LOAD_CEIL == server.ADMIT_LOAD_CEIL)
    check("scheduler.vram received admit_mem_pct via configure", sv.ADMIT_MEM_PCT == server.ADMIT_MEM_PCT)
    check("scheduler.vram received multiblade_enable via configure", sv.MULTIBLADE_ENABLE == server.MULTIBLADE_ENABLE)
    check("scheduler.vram received vram_reclaim_idle via configure", sv.VRAM_RECLAIM_IDLE == vs.VRAM_RECLAIM_IDLE)

    # task_3484: vram_scheduler.configure fail-loud checks on missing/None dependencies
    try:
        vs.configure(_over_global_ceiling=None)
        check("vram_scheduler.configure fails loudly on None callable", False, "no TypeError raised")
    except TypeError:
        check("vram_scheduler.configure fails loudly on None callable", True)

    try:
        vs.configure(VRAM_BUDGET_MB=None)
        check("vram_scheduler.configure fails loudly on None value", False, "no TypeError raised")
    except TypeError:
        check("vram_scheduler.configure fails loudly on None value", True)

    # task_3484: test admission ceiling backpressure with admit_enable=True
    import time
    orig_admit_enable = vs.ADMIT_ENABLE
    orig_admit_max_wait = vs.ADMIT_MAX_WAIT
    orig_over_ceil = vs._over_global_ceiling
    try:
        vs.ADMIT_ENABLE = True
        vs.ADMIT_MAX_WAIT = 0.05
        vs._over_global_ceiling = lambda: True
        t0 = time.monotonic()
        asyncio.run(vs._admit("http://localhost:8500", "test-model", "gpu"))
        elapsed = time.monotonic() - t0
        check("admit observes backpressure when over ceiling with admit_enable=True",
              elapsed >= 0.04, f"elapsed={elapsed:.4f}s want >= 0.04s")
    finally:
        vs.ADMIT_ENABLE = orig_admit_enable
        vs.ADMIT_MAX_WAIT = orig_admit_max_wait
        vs._over_global_ceiling = orig_over_ceil

def main():
    _resolve_toml()
    _install_stubs()
    _check_export_identity_controls()
    try:
        import server  # noqa: E402 -- executes ALL module-level code incl. configure() DI
    except Exception as e:  # noqa: BLE001
        import traceback
        check("import server (no NameError from DI ordering)", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        print(f"\n{_fails} FAILED")
        return 1
    check("import server (no NameError from DI ordering)", True)
    check("declared export census is nonempty", sum(map(len, _EXTRACTED.values())) > 0)
    for module, names in _EXTRACTED.items():
        declared = importlib.import_module(module)
        for n in names:
            obj = getattr(server, n, None)
            origin = getattr(obj, "__module__", None)
            key = (module, n)
            ok = _same_export(server, declared, n, source_name=_EXPORT_ALIASES.get(key),
                              wrapped=key in _TRACED_EXPORTS)
            check(f"{n} provided by server (-> {module})", ok,
                  "" if ok else f"missing or different declared object (definition origin: {origin!r})")

    bh = getattr(server, "_bind_host", None)
    check("_bind_host present + callable", callable(bh))
    if callable(bh):
        check("_bind_host auth-off -> loopback", bh(False) == "127.0.0.1", bh(False))
        check("_bind_host auth-on -> all-interfaces", bh(True) == "0.0.0.0", bh(True))
        check("_bind_host explicit override wins",
              bh(False, "10.1.2.3") == "10.1.2.3", bh(False, "10.1.2.3"))

    _check_scheduler_wiring(server)

    print(f"\n{'ok' if _fails == 0 else str(_fails) + ' FAILED'}")
    return 1 if _fails else 0

if __name__ == "__main__":
    sys.exit(main())
