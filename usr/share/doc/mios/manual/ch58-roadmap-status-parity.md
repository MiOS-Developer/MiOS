<!-- AI-hint: Chapter 58: Roadmap Status Parity. Records the drift that let TASKS.md answer "what is left?" two different ways -- a summary-table cell and the task's own Status line -- and the 49 rows where they disagreed, including seven the table called done-by-code while the detail still said open and three P0 rows the table called done while the detail said planned. Covers why neither surface could be declared authoritative, how each disagreement was settled against the tree instead of the prose, and the check_tasks_status_parity gate that now compares the table cell to the head token of the detail status and rejects the '?' placeholder outright. -->

# <a name="58_roadmap_status_parity"></a>Chapter 58: Roadmap Status Parity

> Part I: Architecture & Governance of the [MiOS manual](../manual.md).

> Path Reference: `/usr/share/doc/mios/manual.md#58_roadmap_status_parity`

#### Overview

`TASKS.md` records every task twice: once as a row in the summary table at the
top, and once as a `**Status:**` line inside the task's own section. Nothing
kept the two in step, and they drifted apart in **49** of 286 rows.

#### <a name="58_the_drift"></a>58.The Drift: What Divergence Looked Like

The disagreements were not evenly harmless:

* **28 rows** carried `?` in the summary table while the task's own section
  gave a real status. The roadmap could not say whether these were open.
* **Seven rows** (task_50, task_56, task_58, task_59, task_60, task_62, task_63) said
  `done-by-code` in the table and `open` in the detail.
* **Three P0 rows** (task_174, task_175, task_177) said `done` in the table and
  `planned` in the detail — the highest-priority items in the file,
  disagreeing with themselves.

Either surface, read alone, produced a confident and different answer to "what
is left?".

#### <a name="58_neither_surface_wins"></a>58.Neither Surface Wins: Settling It Against the Tree

The obvious repair — declare one surface authoritative and project the other
from it — would have written wrong data in both directions. Spot-checking
showed the divergence had two distinct causes:

* For the older rows, the **table** had been swept and re-verified while the
  detail lines were never touched. `_host_pressure_gate()` really is in
  `mios-daemon`, the `[budget]` section really is in `mios.toml`, and
  `usr/share/mios/conductor/` really does exist behind its `conductor_enable`
  gate. Here the detail was stale.
* For rows edited recently, the **detail** carried the newer verdict and the
  table had not caught up.

So each disagreement was settled by looking at the tree, not at either piece of
prose: a status that claims completion has to name an artifact that exists.
Where the tree confirmed it, the detail line was rewritten to the table's
verdict **with the evidence recorded inline**. Where the detail was the more
recent or the more honest reading (`partial` beats `done-by-code` when parts
are missing), the table cell was rewritten instead.

#### <a name="58_the_gate"></a>58.The Gate: check_tasks_status_parity

`tools/check-tasks.py status-parity` compares the summary-table cell to the
**head token** of the detail status — everything before the first ` -- `
continuation or ` (` qualifier — so a detail line may carry paragraphs of
evidence while still being comparable to a one-word cell. It also:

* rejects the `?` placeholder wherever a section exists to answer it, which is
  how 28 rows stayed invisible;
* rejects any status word outside the known vocabulary, on either surface, so a
  typo is a violation rather than a silently new state;
* fails when a task section has no summary row at all;
* fails when the summary table parses to zero rows, rather than passing
  vacuously over an empty set.

That last clause matters more than it looks. The recurring defect this repo
keeps finding is a gate that reports success over a set that excludes the thing
it was meant to check; a parity gate that silently matched nothing would be
exactly that.

The gate prints the open count on success, so the number that answers "how much
is left?" comes from the same pass that proves the two surfaces agree.
