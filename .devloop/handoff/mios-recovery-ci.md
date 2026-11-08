<!-- AI-hint: Remote recovery milestone for CI coverage of the recovered SearXNG lane. -->
# SearXNG CI registration milestone

Source: PR #65 head `403114b99740c1701b3cb09a88497d3cf3b8aed5`.
Lane branch: `mios-recovery-ci`; owned source: `usr/share/mios/mios.toml`.
The source change is one registry entry under `[ci.tiers].unit`, beside the
existing firewall suite. Both CI publishers consume this registry. No exemption
or ratchet was raised.

Before the change, `python3 tools/ci-suites.py --check` failed because the tracked
`tests/test-searxng-endpoint-ssot.py` ran in no tier and was not exempt. The suite
is now selected by `python3 tools/ci-suites.py --tier unit`.

Verified in the cloud source executor:

- `python3 tools/ci-suites.py --check`: passes, 573 suites across 3 tiers,
  6/6 exempt.
- `python3 tools/ci-suites.py --tier unit`: emits
  `python3\ttests/test-searxng-endpoint-ssot.py`.
- `python3 tools/test_ci-suites.py`: 81 tests, OK.
- `python3 tests/test-searxng-endpoint-ssot.py`: 69 tests, OK with 33 skipped
  tests/subtests because this executor lacks PowerShell and native projection
  tools. Skips do not establish those paths work.
- Negative control: remove only the new entry from an in-memory copy of the
  real registry and call the actual `cmd_check`; it returns 1 and names the
  SearXNG suite as tracked but unregistered. No on-disk mutation is left behind.

The native MiOS-DEV builder and live Codespace runtime are not available in this
lane. No native build, native standing gates, projection synchronization, image
publication, live installation or Codespace rebuild is claimed. The orchestrator
must reconcile this SSOT edit with any projection requirements before claiming
combined release validation.

Next action: integrate this lane's commit into the recovery PR, preserve this
receipt, and run the actual unit tier on the provisioned MiOS CI environment
where the required native and PowerShell tools are present. Parent orchestrator
serializes shared integration and publication; no push to main is performed here.
