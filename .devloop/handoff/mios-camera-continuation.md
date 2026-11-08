<!-- AI-hint: Recoverable camera-continuation milestone with exact source ancestry, known defects and unrun native verification. -->
<!-- AI-related: handoff.md, tools/native/mios-tamagotchi/src/ui.rs, tools/native/mios-tamagotchi/src/config.rs, task_3774 -->
# Camera continuation

Base: `69fa06efe04f6422bbaec8b4ea0b953c24df2dd5`.
Branch: `mios-camera-continuation`. Owner: camera continuation lane.

## Milestone 1: recover the finite camera delta

Only the three camera commits after `fb49657e` were transplanted. The full
historical lane was not merged; its original ref remains preserved.

| Original source | Recovered commit |
| --- | --- |
| 19b53c6c2 | ff591ce5f |
| 7d43a2ffe | 50eb020ce |
| 0e7c693b8 | 11d620656 |

These commits merge without conflicts but remain unverified source. Independent
review found realtime camera output overwritten by `animate`, nonterminating
default reset, unscaled stop thresholds, and stale follower pan on subject
changes. This milestone preserves the input before repairing those defects.

Owned files are camera regions of `ui.rs`, `config.rs`, the configurator and
its existing security/form tests, vendor `mios.toml`, and this receipt. HUD
implementation, canonical task registry, root handoff and whitelist updates
remain owned by the integration coordinator.

No native compilation was attempted outside MiOS-DEV. Native checks, compiled
negative controls, static artifact validation and live acceptance remain
pending. A source push does not certify any of those results.

## Resume

```sh
git fetch origin refs/heads/mios-camera-continuation:refs/remotes/origin/mios-camera-continuation
git worktree add --detach ../mios-camera-review refs/remotes/origin/mios-camera-continuation
git -C ../mios-camera-review log -5 --oneline
```

Use a collision-free directory and retain the exact fetched SHA. The
coordinator will prepare a draft continuation PR for remote compilation before
main integration. No live deployment is claimed.
