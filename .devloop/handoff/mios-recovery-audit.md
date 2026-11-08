<!-- AI-hint: Independent source-recovery audit with exact Git heads, checked payloads, remote resume commands and unresolved Codespace runtime access. -->
<!-- AI-related: handoff.md, .agents/COORDINATION.md, .devloop/handoff/mios-wip-manifest.json, .devcontainer/boot-mios-systems.sh -->
# Source recovery audit

Audit date: 2026-11-08. Owner: recovery-audit lane. This receipt records checks
against PR #65 at `403114b99740c1701b3cb09a88497d3cf3b8aed5` and observed
`origin/main` at `4bf38f52b7d5103c5b6ba6fada6a440e55475062`. These observations
do not establish that a later main or PR head is identical.

## Verified source and payloads

`sha256sum -c .devloop/handoff/mios-source-checkpoints.bundle.sha256` passed.
`git bundle verify .devloop/handoff/mios-source-checkpoints.bundle` passed
in the full recovery checkout. Bundle size: 3,988,227 bytes. Bundle SHA-256:
`20afbba3d2f38f0b60d9ff0bcfd3e01dd6706040fa1f6da0fca4ead0f403534d`.

The original handoff directory contains 14 files: eleven portable payloads,
the WIP manifest, the Git bundle and its checksum file. All eleven original
`portable_payloads` entries match their recorded byte counts and SHA-256.
All three original `patches` entries also match their compressed and
decompressed byte counts and SHA-256. There are four compressed patches in
the payload list; the original manifest gives base/source-path metadata for
only three. These counts describe the original manifest before recovery-tool
or receipt additions.

The incremental bundle requires all three prerequisite commits below. Its
human handoff names only the final one; recovery must inspect the actual bundle.

- `150b5b461796d99607c2d28ceadf977ee2970fc8`
- `e1da4f0531553412ff69546d4e1d4eded708be18`
- `c1e55b7582e7335b5f504b393416573b2e34d5d6`

`git bundle list-heads` confirms these seven source heads:

| Bundled branch | Exact source | State at audited main / PR #65 |
| --- | --- | --- |
| mios-cloud-handoff | fc89aae34d836f4902c6ccd40341fa0c3db87e89 | Original exact-history checkpoint; API-published main has a different commit ID |
| mios-mons-input-completion | fb49657e85fbbe13d8620b6c8e674223e25a558e | Root snapshot already published in main |
| mios-mons-voxel-overlay | 6b20513e1f657f5159c02384552d7b2294f585bc | Main's merge aborted; PR #65 integrates this head |
| mios-ci3756-ports | 58b3752d415806f7cd9d43a6d04090757434eb3e | Main's merge aborted; PR #65 integrates this head |
| mios-mons-menu-geometry | 26eb01d492382da7a59d7b17cf5e24af6be55f27 | Preserved for separate HUD integration/review |
| mios-mons-camera-follow | 0e7c693b8242d0342049cfd90b659ab2da1301a3 | Preserved for separate camera integration/review |
| mios-inputd-focus-gesture | 3f3dac3beab2f2a6bb7efe009753c2a23daa49ee | Preserved for separate gesture/configurator verification |

PR #65 head branch is `claude/code-session-lryqdr`. Its two integration commits
retain the original source heads as merge parents:

- `6bdcbae43a807e6f3e62bb705ef706218b9830eb` merges main and voxel.
- `403114b99740c1701b3cb09a88497d3cf3b8aed5` merges that result and ports.

Both `git merge-base --is-ancestor SOURCE PR_HEAD` checks passed. The PR is
draft and was open at audit. Original `handoff.md` statements about aborted
voxel/ports merges describe main's earlier attempt; they do not describe the
PR integration. HUD, camera, gesture and the four WIP patches must not be
assumed integrated merely because PR #65 is available.

## Resume remotely

Use a fresh clone for recovery; the final directory must not already exist.
These commands preserve existing workspaces and pin the audited PR revision:

```sh
git clone https://github.com/MiOS-Developer/MiOS.git mios-source-resume
cd mios-source-resume
git fetch origin refs/pull/65/head:refs/remotes/origin/mios-pr-65
git cat-file -e 403114b99740c1701b3cb09a88497d3cf3b8aed5^{commit}
git switch --create mios-source-resume 403114b99740c1701b3cb09a88497d3cf3b8aed5
sha256sum -c .devloop/handoff/mios-source-checkpoints.bundle.sha256
git bundle verify .devloop/handoff/mios-source-checkpoints.bundle
git fetch .devloop/handoff/mios-source-checkpoints.bundle 'refs/heads/*:refs/remotes/mios-handoff/*'
git show refs/remotes/mios-handoff/mios-cloud-handoff:handoff.md
git bundle list-heads .devloop/handoff/mios-source-checkpoints.bundle
```

If the prerequisite check fails in an existing shallow clone, first run
`git fetch --unshallow origin main` there, then repeat bundle verification.
Do not reset, clean, force-update recovered refs, overwrite a worktree or
blindly apply WIP patches. Create collision-checked, function-named worktrees
for the individual recovered lanes. The bundle preserves local source history;
it does not assert that the original source branch names were pushed to origin.

For this recovery effort's later milestones, fetch the publication branch and
read its newest handoff in another collision-checked worktree:

```sh
git fetch origin refs/heads/mios-cloud-recovery:refs/remotes/origin/mios-cloud-recovery
git worktree add --detach ../mios-recovery-review refs/remotes/origin/mios-cloud-recovery
git -C ../mios-recovery-review log -5 --oneline
```

That branch command is a resume instruction for the coordinator's publication;
the audit lane itself does not certify or mutate the remote branch. Confirm
the fetch succeeds and record its exact commit before claiming publication.

## Codespace and verification boundary

Requested Codespace: `fluffy-trout-p7rwx5jj9r55f67qr`.
Its runtime, creation logs, private filesystem, newer unpushed work and live
MiOS processes have not been accessed or verified by this audit lane. The
operator reports that it fails to load MiOS. Source recovered from GitHub is
independent of whether that Codespace starts or contains newer local changes.
Preserve `/workspaces` and any required state before rebuilding its container.

Read-only startup inspection found that the audited
`.devcontainer/boot-mios-systems.sh` unconditionally invokes
`python3 harness/verification_gates.py --quick`, although that file and its
`harness/` directory are absent from the audited tracked MiOS tree. This is a
concrete post-start failure once earlier startup stages succeed. The startup
implementation lane owns its repair and verification. Missing base-image
account/helpers can fail before lifecycle commands run; the actual Codespace
creation log is still needed to establish its first failing stage.

The audited `mios_core` also copies native tools from
`tools/native/target/release`, while that workspace's `.cargo/config.toml`
sets `build.target = "x86_64-unknown-linux-musl"`. Its default release output
therefore lives under `target/x86_64-unknown-linux-musl/release`; the install
loop silently skips those rebuilt tools. Both findings were sent to the
startup implementation owner for source fixes and regression checks.

No combined MiOS CI, MiOS-DEV build, release artifact, visible Nested MiOS
Session, physical-device acceptance or live installation is certified here.
R3 rendering performance/visual gaps, D4's unrecovered raw adversarial receipt,
bootstrap parity and the remaining source lanes stay explicit follow-up work.
Per `.agents/COORDINATION.md`, a main push requires an authorized live-install
receipt; a PR-branch push does not trigger that installation rule.

Later access diagnosis by the integration owner: the user reports authenticated
access, but a fresh ordinary HTTPS probe to the Codespaces API fails at proxy
CONNECT with HTTP 403 before GitHub authentication. `api.github.com` is absent
from this environment's enforced allowlist. Git repository pushes work through
`github.com`. No environment policy mutation or Codespaces tool is exposed in
this session. CLI login diagnostics under that denial cannot establish whether
the user's GitHub credentials are valid.
