---
name: parallel-fanout
description: |
  Use delegate_subtask_to_peer_agent to fan out independent work across child
  agents in parallel. Apply this whenever a request decomposes into 2+ subtasks
  that share no data -- multi-file audits, multi-source investigations,
  multi-target verifications, multi-search greps, multi-host queries. The single
  tasks=[...] form is the parallel form; calling a tool in a serial loop instead
  is the wrong shape and burns the multi-agent capability.
metadata:
  hermes:
    requires_tools:
      - delegate_subtask_to_peer_agent
---
<!-- AI-hint: Defines the parallel-fanout skill for Hermes to replace serial tool-call loops with `delegate_subtask_to_peer_agent(tasks=[...])` when processing independent subtasks like multi-file audits or multi-host queries to optimize execution via child agents.
     AI-related: /usr/share/mios/hermes/skills/parallel-fanout/SKILL.md., mios-hermes-firstboot, mios-llm-light, hermes-agent.service, MIOS_AI_ENDPOINT -->

# Parallel fan-out via `delegate_subtask_to_peer_agent`

> _MiOS-managed: seeded into $HERMES_HOME/skills/parallel-fanout/SKILL.md
> and ~/.hermes/skills/parallel-fanout/SKILL.md by mios-hermes-firstboot
> from /usr/share/mios/hermes/skills/parallel-fanout/SKILL.md. To take
> ownership of this skill, delete the "MiOS-managed" marker in this
> comment — firstboot will then leave the file alone forever._

You are an orchestrator over a pool of cheap, fast CPU-side child agents
(`lfm2:700m`, ~6 concurrent, depth 2). The `delegate_subtask_to_peer_agent`
tool is how you turn serial tool-call loops into parallel dispatch.

## Decision rule (HARD)

If you would make 2+ inspection/gathering tool calls **in the same turn** with
no data dependency between them, you have ALREADY LOST. The right shape is
ONE `delegate_subtask_to_peer_agent(tasks=[...])` call instead.

Sequential inspection calls are the most common antipattern. Examples:

| Antipattern (slow + serial) | Correct (parallel + cheap) |
|---|---|
| run "ls /etc/foo" then "ls /etc/bar" as two calls | `delegate_subtask_to_peer_agent(tasks=[{goal:"list /etc/foo"},{goal:"list /etc/bar"}])` |
| 3 sequential greps in different dirs | 1 delegate_subtask_to_peer_agent with 3 tasks |
| read 4 config files one at a time | 1 delegate_subtask_to_peer_agent with 4 tasks |
| Check 5 service states one by one | 1 delegate_subtask_to_peer_agent with 5 tasks |

**Cost of delegation is small** (~50-200 ms to spawn child + the child's own
runtime, which is faster than your serial loop because it's parallelised
across CPU cores). Don't think of it as overhead — think of it as the
default for *any* multi-step gathering work. Reserve direct single-shot tool
calls for: single commands, sequential pipelines where step B needs step A,
or interactive workflows.

If you fire two inspection tool calls in a row with no data dependency, you
should have used `delegate_subtask_to_peer_agent` once. Even for "small" jobs.
Especially for "small" jobs, because the parallelism savings stack across
child concurrency (up to 6 simultaneous). The rule is not "use delegation for
big work" — it is "use delegation whenever the structure of the work is
parallel."

## The right call shape (parallel form)

A *single* `delegate_subtask_to_peer_agent` call with a `tasks` array. **Not**
three separate `delegate_subtask_to_peer_agent` calls. **Not** a `for` loop of
inspection commands.

```
delegate_subtask_to_peer_agent(tasks=[
  {"goal": "Read /etc/containers/systemd/*.container, extract every Image= line, return a markdown table."},
  {"goal": "Find the 5 most recently modified files under /var/log via `find -printf`. Return a table of path/mtime/size."},
  {"goal": "List every file under /etc/mios with size and first non-comment line via `cat`+`head`. Return a table."}
])
```

Children are leaves by default — they cannot delegate further. To allow a
child to spawn its own grandchildren, pass `role="orchestrator"` on that
task entry (max depth is 2 for this user).

## When to use

- **Audits across multiple files / directories / hosts** — "compare three configs", "verify N services are running", "summarize the contents of these N files"
- **Multi-source investigation** — gather facts from `journalctl`, `systemctl`, `podman ps`, log files, all at once
- **Multi-search greps** — search the same pattern across N subtrees
- **Verification fan-out** — confirm a fact via 3 independent methods in parallel
- **Independent transforms** — process N items where each item's processing is self-contained
- **Research synthesis** — research topic A, B, C concurrently, compose at the end

## When NOT to use

- **Single command** — just call the tool directly. Delegation overhead beats the saving.
- **Sequential pipeline** — step B needs step A's output. Run them in your own loop.
- **Reasoning-heavy synthesis** — children run on `lfm2:700m`, which is great for
  grep/inspect/report and not for multi-step reasoning or code synthesis. Save
  that for your own GPU model.
- **Operations with shared external side-effects** — children may race; serialize
  writes/publishes/HTTP-POSTs in your own loop where you control ordering.

## Per-task `context` is mandatory

Children have **no memory of your conversation**. Every fact a child needs
must be in its `context` field — file paths, error messages, constraints,
output language, expected return format. The richer the context, the
better the child performs and the smaller the back-and-forth.

```
delegate_subtask_to_peer_agent(tasks=[
  {
    "goal": "Confirm whether the granite4.1:8b model is served and ready for inference.",
    "context": "The mios-llm-light lane serves on the `llm_light` port (OpenAI-compat). `curl $MIOS_AI_ENDPOINT/models` lists the served models. Return YES/NO + which model ids are present."
  },
  {
    "goal": "Verify the hermes-agent service is healthy.",
    "context": "Use `systemctl is-active hermes-agent.service`. Return the literal output."
  }
])
```

## Verify, don't trust

Subagent summaries are **self-reports**, not verified facts. A child that
says "uploaded successfully" or "config updated" may be wrong. For
operations with external side-effects, require the child to return a
verifiable handle (URL, ID, absolute path, HTTP status, file contents
post-write) and verify it yourself before reporting success to the
operator.

## What you get back

Results return as an array, one entry per task, in the order you submitted
them. Each entry is a *summary* generated by the child after it finished —
the intermediate tool output never enters your context window. That's the
point: you preserve your context window for synthesis while children burn
their own contexts on the dirty work.
