<!-- AI-hint: Recovery inventory milestone and receipt contract for portable source continuation. -->
<!-- AI-related: .devloop/handoff/mios-recovery-manifest.json, .devloop/handoff/mios-wip-manifest.json, handoff.md -->
# Source recovery inventory milestone

Lane: `mios-checkpoint-manifest`. Source: PR #65 at
`403114b99740c1701b3cb09a88497d3cf3b8aed5`, observed on 2026-11-08 UTC.
Find the commit carrying this receipt with
`git log -1 --format=%H -- .devloop/handoff/mios-checkpoint-manifest.md`.
Remote publication is serialized by the integration owner; this receipt alone
does not assert that a push succeeded.

The recovery manifest records all seven original bundle refs, all three actual
prerequisite commits, the bundle digest, the WIP manifest digest, and the
observed main and PR revisions. It distinguishes source preserved on main,
lanes merged in the PR, and lanes still awaiting integration. The PR's merged
voxel and ports lanes supersede the older handoff's aborted-merge status for
those two lanes. Main's historical snapshot status remains unchanged.

The original compressed payload bytes and existing hash records are unchanged.
The root untracked-code patch now has source paths and decompressed hashes in
`unbased_patches`. Its original capture did not record a base. It consists of
four additions, so successful application to a candidate base cannot establish
which original base was used. Its `base` remains `null`; review it separately
and do not include it in automatic patch replay.

## Verified evidence

- `sha256sum -c .devloop/handoff/mios-source-checkpoints.bundle.sha256`: OK.
- `git bundle verify .devloop/handoff/mios-source-checkpoints.bundle`: OK;
  seven refs and three prerequisites, matching the JSON inventory exactly.
- An independent Python/Git inspection verified the bundle and WIP manifest
  bytes and SHA-256, all 11 portable payloads, all four compressed and
  decompressed patch payloads, and each patch's source-path set.
- The three patches with recorded bases passed `git apply --cached --check`
  against temporary indexes populated with `git read-tree BASE`. No source
  worktree files were changed by these application checks.
- The seven imported `refs/remotes/mios-handoff/*` match their bundle heads.
  Both lanes marked merged in PR #65 pass `git merge-base --is-ancestor`
  against the recorded PR source commit.
- Negative control: replacing the bundle's expected digest with 64 zeroes
  caused the same byte-verification assertion to reject it.
- `git diff --check`: PASS.

The first source-path comparison used array order and rejected the pre-existing
menu manifest, whose two paths are in a different order from the diff. Comparing
the path sets correctly passed; no existing metadata was reordered to hide it.
No system image, native build, combined CI, live installation, visual acceptance,
or recovery of unpublished remote filesystem changes was verified here.

## Resume from a fresh clone

Fetch the branch carrying this receipt and create a separate worktree at its
verified published commit. Preserve an existing dirty checkout. From that
worktree's repository root:

```sh
git fetch origin main
sha256sum -c .devloop/handoff/mios-source-checkpoints.bundle.sha256
git bundle verify .devloop/handoff/mios-source-checkpoints.bundle
git bundle list-heads .devloop/handoff/mios-source-checkpoints.bundle
git for-each-ref refs/remotes/mios-handoff/ --format='%(objectname) %(refname)'
```

Compare the heads and prerequisites with `mios-recovery-manifest.json`.
If prerequisites are missing, obtain complete main history before importing.
If the recovery namespace is empty, import the preserved heads:

```sh
git fetch .devloop/handoff/mios-source-checkpoints.bundle 'refs/heads/*:refs/remotes/mios-handoff/*'
git show mios-handoff/mios-cloud-handoff:handoff.md
```

If the namespace already exists, compare every ref with the inventory instead
of blindly updating it. Preserve any different local refs and choose a fresh
namespace when necessary. Do not reset, rebase, or clean another lane. Review
the original handoff, this receipt, and the startup-fix receipt together before
continuing a still-pending implementation lane.

## Milestone receipts for every lane

Each lane keeps a tracked, function-named receipt under `.devloop/handoff/`
and records these fields when it reaches a reviewable checkpoint:

| Field | Required evidence |
| --- | --- |
| Lane and owner | Function-named branch, owned paths, coordinating participant |
| Source | Exact base commit and reviewed dependency commits |
| Checkpoint | Exact implementation commit, published ref, verified remote head |
| Artifacts | Repository paths, byte counts and SHA-256 for portable payloads |
| Checks | Exact command, source tested, result, failures and negative controls |
| Status | Complete, partial, or blocked; distinguish queued from acknowledged work |
| Runtime | Build context, live installation and visual acceptance verified or unverified |
| Resume | Next concrete action, dependencies, access blockers and ownership |

Use a following receipt commit to record a prior implementation commit's SHA;
do not invent a self-referential commit hash. The integration owner serializes
shared handoff/index updates and verifies remote heads after publishing. Runtime
credentials, private relay tokens, sockets, models, caches and datastores are
outside source artifacts. A future session must rediscover live targets.

Next actions: publish this milestone, reconcile the startup-fix lane, and retain
HUD/camera/gesture/WIP ownership and verification gaps until each receives its
own reviewed integration. The selected remote development environment's startup
and unpublished files remain access-dependent; the published source inventory
cannot establish either result.
