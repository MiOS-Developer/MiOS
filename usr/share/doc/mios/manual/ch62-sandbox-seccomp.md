<!-- AI-hint: Chapter 62: Sandbox Seccomp. Records what the risk-tier dispatch sandbox actually did before task_210 -- bwrap really was exec'd and really did jail the filesystem and the network, while the confined process reported `Seccomp: 0`, leaving mount, ptrace, keyctl and bpf reachable from inside the jail. Covers the classic-BPF filter built without libseccomp, why the syscall NUMBER table is cross-checked against the host's own kernel headers rather than trusted, why an architecture with no verified table refuses to produce a filter, why an arch mismatch in the program must fall through to deny, and why a filter that denies nothing is worse than no filter at all. -->

# <a name="62_sandbox_seccomp"></a>Chapter 62: Sandbox Seccomp

> Part V: Security & Identity of the [MiOS manual](../manual.md).

> Path Reference: `/usr/share/doc/mios/manual.md#62_sandbox_seccomp`

#### Overview

The roadmap recorded the risk-tier sandbox as computed-and-discarded: an argv
assembled and never `exec`'d. Running it settled the question differently.

Under `mios-sandbox-exec --level enforce`, a confined verb really does run
under bubblewrap — `/proc/1/comm` reads `bwrap`, a write outside the bind set
fails read-only, and the network is gone. What the same process also reported
was `Seccomp: 0`. The filesystem and the network were jailed; the **syscall
surface was not**. `mount`, `ptrace`, `keyctl`, `bpf`, `init_module` and
`perf_event_open` were all reachable from inside a jail that read as strict.

#### <a name="62_no_libseccomp"></a>62.No libseccomp: A Filter in 31 Instructions

`bwrap --seccomp FD` wants a compiled classic-BPF program, which normally means
linking libseccomp. It does not have to. The program the sandbox needs is small
and entirely regular:

```
ld  [4]                  ; seccomp_data.arch
jeq AUDIT_ARCH_X86_64  ->  next  else  DENY
ld  [0]                  ; seccomp_data.nr
jeq <denied nr>        ->  DENY  (once per denied syscall)
...
ret ALLOW
ret ERRNO(EPERM)
```

That assembles in pure `struct.pack`, so the filter has no build dependency and
unit-tests as data. The default action is `EPERM` rather than a kill: the
Done-When asks for a denied syscall to be *denied instead of succeeding*, and
an errno is both what that means and far easier to diagnose than a process that
vanished.

#### <a name="62_abi_not_trust"></a>62.The Number Table Is ABI, Not Trust

The one genuinely dangerous part is the syscall-number table. A wrong number
does not fail loudly — it denies **the wrong syscall**, quietly, forever. So
the committed table is not trusted: the sibling test re-derives it from the
host's own `asm/unistd_64.h` whenever those headers exist and fails on any
mismatch. A transcription error cannot ship past a glibc host.

That is also why only x86_64 appears. Numbers for architectures that could not
be verified here are absent rather than approximated, and an architecture with
no verified table **refuses** to produce a filter.

#### <a name="62_nothing_is_worse"></a>62.A Filter That Denies Nothing Is Worse Than None

Three refusals follow from one idea — an empty or malformed filter still shows
up as `Seccomp: 2` and still reads as protection:

* an unsupported architecture raises rather than emitting a program;
* a denylist that resolves to nothing raises rather than emitting a
  deny-nothing filter;
* a chain longer than the 255-slot jump range raises rather than truncating to
  a filter whose tail syscalls fall through to ALLOW.

The wrapper takes the same stance one level up. At `--level enforce`, if the
filter cannot be built the run is **refused with exit 126** — exactly what the
script already did for a missing `bwrap`. Confining a verb with no filter is
not a degraded success; it is a silent downgrade of a security control.

The program's own arch check follows the same rule: a mismatch jumps to DENY,
never to ALLOW. Letting a mismatched personality through is the classic
32-on-64 seccomp bypass, and the test asserts the jump target directly rather
than trusting the shape.

#### <a name="62_environment_vs_artifact"></a>62.Environment vs Artifact: What a Test May Skip On

`tests/test-sandbox-seccomp.sh` runs in two tiers because only one of them can
run everywhere. The generator and refusal tiers are **artifact** checks -- they
prove the filter builds, names this host's audit arch, has exactly one compare
per denied syscall, refuses an unsupported architecture, and that the wrapper
exits 126 rather than running unfiltered. Those need nothing but Python and
always run.

The live tier needs a kernel willing to let bubblewrap build a sandbox at all.
A hardened CI runner may refuse outright (`bwrap: setting up uid map:
Permission denied`), or allow the sandbox but refuse a fresh net namespace
(`loopback: Failed RTM_NEWADDR`). Both are facts about the **runner**, not
defects in the sandbox, so the tier skips loudly -- even under
`MIOS_DRIFT_REQUIRE_TOOLS=1`, which otherwise makes a missing tool fatal.

The rule that decides this is the same one that closed the unimportable-server
gate: **a gate may skip on the environment, never on the artifact.** The
discriminator is bubblewrap's own minimal capability -- if `bwrap /bin/true`
cannot run, nothing in the tier can -- rather than matching error text, and the
netns fallback is a second probe of the same kind. What is left running on a
restricted runner still fails when the refusals are removed, which is the
property that makes the skip safe rather than convenient.

#### <a name="62_two_argv_builders"></a>62.Two Argv Builders, One Executor

`mios_sandbox.build_bwrap_argv` predates the wrapper and describes a *different*
confinement from the one that runs: `--unshare-all` where the wrapper unshares
pid/ipc/uts, and no `--cap-drop ALL` or `--seccomp`. It is a reference shape,
not the argv anything executes, and it is now labelled as such at its
definition. Reconciling the two is recorded as task_253 rather than done here —
quietly changing the flag set of a security wrapper that cannot be VM-verified
from this environment would be a worse outcome than a documented divergence.
