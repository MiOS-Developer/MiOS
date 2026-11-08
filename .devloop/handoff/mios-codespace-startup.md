<!-- AI-hint: Portable source-level startup fix receipt; live Codespace recovery remains unverified. -->
# MiOS Codespace startup milestone

Base: PR #65 at `403114b99740c1701b3cb09a88497d3cf3b8aed5`.
Lane: `mios-codespace-startup`; owner: startup worker.

The post-start lifecycle invoked `harness/verification_gates.py --quick`,
but that file and its parent directory do not exist in the tracked source.
Every startup reaching that point failed. The hook now calls the existing
`.devcontainer/mios-agent-pipe-dev check` probe and preserves its failure status.

Verification in an isolated cloud worktree:

- `python3 tests/test-code-server-bake.py`: 69 tests pass, including real
  post-start function execution with hermetic commands and a loopback socket.
- Replacing the new probe with the old missing-harness command in memory:
  both new regression checks fail as expected.
- `bash -n .devcontainer/boot-mios-systems.sh` and `git diff --check`: pass.
- `python3 tools/ci-suites.py --check`: existing PR #65 failure reports
  `tests/test-searxng-endpoint-ssot.py is tracked but runs in no tier and is not exempt`.
  The integration lane owns that registry correction.

No image build or live Codespace rebuild ran. This host lacks MiOS-DEV and
Podman; the selected Codespace filesystem and logs are not accessible here.
The code fix establishes source behavior, not successful live loading.

Next: correct the independently found native target output installation path,
then integrate both startup milestones and rerun combined source checks.
To resume this lane, fetch `origin/mios-codespace-startup`, inspect its log and
this receipt, and use a separate worktree. Preserve unpublished Codespace files
before an authenticated operator rebuilds the existing container.
