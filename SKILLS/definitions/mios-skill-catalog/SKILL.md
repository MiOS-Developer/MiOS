---
name: mios-skill-catalog
description: |
  The MiOS skill catalog -- Phase C.2 cross-agent codified procedures.
  Promoted skills are surfaced through `tool_search`, which returns the
  promoted-skill projection of the catalog, and run through the
  agent-pipe Semantic Firewall the same way native verbs do. Load this
  skill when the operator says "what skills do I have", "run my <name>
  skill", "automate <chain>", or any phrase referencing learned / saved
  procedures.
metadata:
  hermes:
    requires_tools:
      - tool_search
---
<!-- AI-hint: Defines the MiOS skill catalog as a repository of codified, multi-agent procedure DAGs; agents use tool_search to discover, list, and execute promoted skill chains over the `agent_pipe` port.
     AI-related: /usr/share/mios/hermes/skills/mios-skill-catalog/SKILL.md._, mios-skill-catalog, mios-skills-miner, mios-skills, mios-skills-miner.timer -->

# MiOS skill catalog

> _MiOS-managed: seeded from
> /usr/share/mios/hermes/skills/mios-skill-catalog/SKILL.md._

The MiOS agent stack mines repeating tool-call sequences out of
the agent datastore and codifies them as `skill` rows. A promoted skill is a
parameterized DAG of MiOS verbs -- the same verbs you already call
directly. Every agent in the stack reads the same catalog so a
skill promoted from MiOS-OpenCode is callable from Hermes and vice
versa.

## Discovering and running skills

| Step | When to use |
| --- | --- |
| `tool_search(query="<intent>")` | Discovery. Returns the promoted-skill projection of the catalog -- names + descriptions -- so the agent can choose the right procedure at run time. The promoted-skill catalog is the source of truth. |
| Call the discovered tool | First-class tool call. Every promoted skill is surfaced as a callable tool; once `tool_search` returns its name, dispatch it with the parameters the catalog exposes. |

Both route through agent-pipe (port key `agent_pipe`) `/skills/*`, so the Semantic
Firewall + taint chain + audit rows apply identically to direct verb
dispatch.

## How a skill becomes runnable

1. Operator runs verb chain N times across sessions.
2. `mios-skills-miner.timer` fires `mios-skills mine`.
3. Miner inserts a `skill` row, `source=mined`, `status=candidate`.
4. Operator promotes via configurator HTML OR
   `mios-skills promote <name>`.
5. Hermes' next `/skills/openai-tools` refresh picks it up.

Operator-authored skills follow the same lifecycle minus step 1-2:
`mios-skills import` from a JSON template; promote.

## Examples (operator phrasing → tool call)

```
# Operator: "run my morning-routine skill"
# Resolve the catalog entry first, then call the surfaced skill tool:
tool_search(query="morning routine skill")

# Operator: "what skills do I have?"
tool_search(query="promoted skills")

# Operator: "save these steps as a skill called 'save-doc'"
# (no auto-promote -- ask for the parameter names first, then dispatch
#  the surfaced skill tool with the catalog's declared params)
tool_search(query="save-doc skill")
```

## What NOT to do

- Do NOT hardcode skill names in YOUR reasoning. The catalog is
  the source of truth -- call `tool_search` if uncertain.
- Do NOT bypass `/skills/run` by stitching verbs manually. The
  audit chain stops working and the miner can't dedup -- the
  operator's promoted procedure gets mined AGAIN as a new
  candidate.
- Do NOT ask the operator for params the skill doesn't expose;
  call `tool_search` to read the catalog's `body.params` array
  for the promoted skill.
