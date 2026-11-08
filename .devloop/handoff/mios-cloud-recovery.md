<!-- AI-hint: Integration publication receipt for portable source recovery, Codespace startup repairs and remaining access/build blockers. -->
<!-- AI-related: handoff.md, .devloop/handoff/mios-recovery-manifest.json, .devcontainer/boot-mios-systems.sh -->
# Cloud recovery integration milestone

Integration branch: `mios-cloud-recovery`. Publication destination: PR #65's
`claude/code-session-lryqdr`, with the same committed tree on
`mios-cloud-recovery`. Original main is
`4bf38f52b7d5103c5b6ba6fada6a440e55475062`. Find the commit carrying this
receipt with `git log -1 --format=%H -- .devloop/handoff/mios-cloud-recovery.md`;
verify its remote head before resuming. No main merge or live install is claimed.

## Published worker milestones

Each worker's remote branch was pushed and confirmed before integration.
Cherry-picks preserve each milestone as a separate integration commit.

| Lane | Published source commits | Integrated commits |
| --- | --- | --- |
| Recovery inventory | c9d39b500d333334397e6088bd34ebaf355d6a00 | df807dac8 |
| Startup readiness | dfde1a837ef72018445415a040ebc5d3d587a6e8 | e5bb1853f |
| Startup artifact paths | 7fb65eb282ab055cc872f3dad0df795ec0c9c272 | 03748168f |
| Locked workspace builds | 54fd9ee15 | 8553f7852 |
| CI registration | 0f8d44e1b86d5a573ab05f37bfa651695d4ccd73 | 0a18c098f |
| Audit and lane handoff | Written by audit worker; published by integration owner | 6fefad425 |

The startup now uses the shipped gateway readiness probe rather than an absent
`harness/verification_gates.py`. Installation follows Cargo's reported executable
paths instead of silently skipping configured target directories. Both builds
use `--locked`; missing required artifact reports and build/probe failures stop
startup. The SearXNG suite is registered in the canonical unit CI tier.

## Integration verification

- `python3 tests/test-code-server-bake.py`: 71 tests OK on the combined tree,
  including alternate target architecture, target directory spaces, missing
  executable receipts and build/readiness failure propagation.
- `python3 tools/test_ci-suites.py`: 81 tests OK.
- `python3 tests/test-searxng-endpoint-ssot.py`: 69 tests OK, 33 skipped for
  unavailable PowerShell/native components. Skips are not runtime passes.
- `python3 tools/ci-suites.py --check`: 573 suites registered in three tiers;
  six of six exempt; PASS after correcting the pre-existing PR registration gap.
- `bash -n .devcontainer/boot-mios-systems.sh` and `git diff --check`: PASS.
- Clean-clone recovery: bundle verification, import of all seven exact histories
  and commit resolution PASS. All eleven original portable payload hashes and
  byte counts match. An altered bundle fails the recorded digest comparison.
- Integrated recovery manifest: bundle/WIP manifest hashes, seven refs, and all
  four compressed/decompressed patch hashes match. Three recorded-base patch
  application checks and both merged-lane ancestry checks passed in the worker.
- Worker negative controls caught the missing-harness command, old installation
  glob, corrupt bundle digest and removal of the CI registration. See lane receipts.

## Explicit blockers

`bash tools/sync-generated.sh` was attempted and returned:
`[mios-gen sync] required native tool mios-gen is missing; build the SSOT catalog`.
The native standing-gate binaries and the MiOS-DEV builder are unavailable in
this executor. No full projection sync, native standing gates, combined MiOS
CI, image build/publication, release, physical/visual acceptance or live install
is certified. Regenerate/check derived SSOT projections in MiOS-DEV before main
publication; do not hand-edit generated projections or loosen ratchets.

The user reports authentication for Codespace
`fluffy-trout-p7rwx5jj9r55f67qr`. After that report, both `gh codespace view` and
an ordinary `curl --include` request to its API were retried. Curl fails at
CONNECT: HTTP 403 from the cloud proxy before GitHub authentication. The enforced
allowlist omits `api.github.com`; this session exposes no network-policy mutation
or Codespaces-capable tool. GitHub repository writes remain functional. Earlier
CLI authentication diagnostics do not establish credential validity under this
network denial. Do not record a successful rebuild or assume unpublished
Codespace files were recovered.

## Next owner actions

1. Fetch the latest PR branch into a collision-checked worktree and read
   `handoff.md` and each lane receipt. All seven original histories remain
   recoverable from the bundle already on main; HUD/camera/gesture/R4/menu/root
   WIP are preserved, not silently merged.
2. Enable `api.github.com` in the cloud environment's allowed domains using
   environment settings or a session exposing the policy-edit capability.
   Retry fresh Codespaces access, then verify actual credential/scopes if the
   request reaches GitHub. Never paste tokens into handoff files or chat.
3. Inventory the Codespace and preserve its unpublished source and required
   state before switching to the repaired source and rebuilding. Only its
   actual logs can establish the first live failure and final successful load.
4. In authorized MiOS-DEV, run projection sync and standing gates, review the
   complete PR, and perform the required live installation after main publication.
   Preserve adopted sessions, pet saves, models and other participants' worktrees.
