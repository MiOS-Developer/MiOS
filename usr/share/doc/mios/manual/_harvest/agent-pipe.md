<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### !/usr/bin/env python3 AI-hint: Semantic KV-cache context...

!/usr/bin/env python3
AI-hint: Semantic KV-cache context compaction engine and episodic summary generator for agent-pipe.
AI-related: usr/lib/mios/agent-pipe/mios_compact.py, tests/test-sec.py
AI-functions: KVCompactEngine, estimate_tokens, summarize_turns, main

<!-- mios-src:d9321fd812da from usr/lib/mios/agent-pipe/mios_kv_compact.py:1-4 -->

### AI-hint

AI-hint: task_284 task_3218 Turn-boundary preemption & snapshot-suspend-resume via llama.cpp KV-cache slot save/restore API (/slots endpoint). Suspended conversations are checkpointed to /var/lib/mios/llamacpp/slots/ and their task row updated to suspended
AI-related: mios-llm-light, mios_kv_compact, test_mios_kvfork
AI-functions: slot_path, __init__, suspend, resume, erase, _llama_slot_action, list_suspended, class KVSlot, class KVForkManager

<!-- mios-src:2e65d181492e from usr/lib/mios/agent-pipe/mios_kvfork.py:1-3 -->

### AI-hint

AI-hint: MiOS system and orchestration module providing mios priority sched capabilities.
AI-related: usr/lib/mios/agent-pipe/test_mios_priority_sched.py, tests/test-priority-sched.py
AI-functions: age_s, inject_headers, to_request_body_extra, _priority_name, __init__, wrap, augment_headers, sorted_queue, drain, classify_turn, PriorityRequest, PriorityGate

<!-- mios-src:dae5f7fbb88d from usr/lib/mios/agent-pipe/mios_priority_sched.py:1-3 -->
