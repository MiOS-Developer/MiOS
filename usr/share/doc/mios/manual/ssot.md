<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### blocks_boot = false is Law 12: enrolment never gates a...

blocks_boot = false is Law 12: enrolment never gates a boot.
federate = "native": peers join via each system's OWN mechanism -- k3s
server/agent join, corosync membership -- automatically, never by hand.

<!-- mios-src:46f29ed120aa from usr/share/mios/mios.toml:9397-9399 -->

### Keys the Python and Rust resolvers do not agree on....

Keys the Python and Rust resolvers do not agree on. Shrink-only.
The parity check never ran until the binary was found in target/debug,
so this is the first measurement, not a regression. task_2317 drives it
to zero by making one implementation authoritative.

<!-- mios-src:c3a6640de0d8 from usr/share/mios/mios.toml:10405-10408 -->

### Values that differ for keys both resolvers emit. The...

Values that differ for keys both resolvers emit. The remainder is
Python's repr of nested arrays/tables, which needs preserve_order on
the toml crate to match -- task_2317, with a toolchain that can link.

<!-- mios-src:2ebc31cc089b from usr/share/mios/mios.toml:10410-10412 -->

### Installed on a runner before the unit tier. A hand-written...

Installed on a runner before the unit tier.

A hand-written list was wrong twice in a row: server.py's import chain reaches
smolagents through mios_gateway_queue and mcp through the module after that,
and each missing name cost a CI round trip to discover. The agent-pipe already
declares what it needs, so the runner installs THAT and the list cannot drift
from the code again.

<!-- mios-src:351d4a832370 from usr/share/mios/mios.toml:10870-10876 -->

### The MiOS product line, named once. Each variant used to be...

The MiOS product line, named once. Each variant used to be a different shape:
an edition entry, its own table, or only a machine name. status is measured:
shipping means built and observed, partial means the machinery runs but not
the whole job, design means specified with no artifact.

<!-- mios-src:9f4d0ac73874 from usr/share/mios/mios.toml:10975-10978 -->

### What it takes for a built artifact to count as real. The...

What it takes for a built artifact to count as real. The verifier walked the
tree, matched nothing and reported success, so a build that produced no file
at all satisfied the gate that guards the push. Every format above names the
glob its own target writes; a format whose globs match nothing is a missing
artifact, and no artifact at all is the loudest failure of the set.

<!-- mios-src:12044cee5de9 from usr/share/mios/mios.toml:11153-11157 -->

### [rust] -- what the Rust layer's green actually covers....

[rust] -- what the Rust layer's green actually covers.

`cargo test --workspace` prints "test result: ok. 0 passed" for a crate with no
tests, which reads exactly like a crate whose tests all passed. Every entry
below is in that state. The ceiling is shrink-only: write a test, remove the
entry, lower the number.

<!-- mios-src:3137ebd37e29 from usr/share/mios/mios.toml:11165-11170 -->

### [fs_watcher], [ssot_consumers], and [unit_projection]

The `fs_watcher` section configures filesystem watch paths and events for miosd.
The `ssot_consumers` section lists all automation and tools that read mios.toml.
The `unit_projection` section defines how quadlets and systemd units project from SSOT definitions.
