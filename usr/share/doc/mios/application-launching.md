<!-- AI-hint: Shared desktop application launch policy, native sandbox and portal recovery, startup failures and opt-in verification. -->
<!-- AI-related: tools/native/mios-gen/src/app_preflight.rs, usr/share/mios/quickshell/AppLaunch.qml, usr/share/mios/mios.toml [desktop.launch], tests/test-app-launch-health.py, tests/test-app-launch-qml.py -->

# Application launching

The MiOS dock, application drawer and application search use `AppLaunch.qml` and the native `mios-gen app-preflight` command. The same path applies to installed desktop entries, including native applications, Flatpaks and application entries that launch other runtimes. Window activation and application-menu actions remain the application's own operations.

Before launching a desktop entry, the guard validates its identity, launch metadata, optional executable requirement and terminal configuration. Flatpak entries also run a bounded sandbox probe. Project-owned Flatpak wrappers retain their `X-Flatpak` identity so they receive the same checks as exported Flatpak entries.

The document portal is healthy only when its actual `fuse.portal` mount and `by-app` directory exist. An active systemd service alone is insufficient. If the mount is missing, one launch recovers the document portal under a per-user lock; concurrent launches reuse the recovered service. This does not restart applications or the desktop. A native application can proceed if this optional sandbox infrastructure cannot recover.

GIO activates desktop entries using their field codes and D-Bus activation metadata. Terminal entries open through the configured terminal argv and working directory. Flatpak activation removes inherited `MIOS_*` environment variables to prevent the large projected session environment from exceeding sandbox argument limits. Duplicate clicks share one pending check. Failed checks, sandbox timeouts and failed GIO activation requests produce notifications and release their pending state. A successful check establishes that the launch infrastructure works; it cannot guarantee that an application will finish its own initialization or remain running.

## Configuration

`[desktop.launch]` in layered `mios.toml` defines the overall and individual probe timeouts, portal recovery policy and service name, recovery lock, application directories, launcher executables and terminal argv. The configurator exposes these settings under **Application startup**. The default check deadline is 15 seconds and each sandbox probe is bounded to 8 seconds. A `{name}` argument in `terminal_command` expands to the application's displayed name.

## Diagnostics

Run these commands as the desktop operator with its normal runtime and session bus:

```sh
mios-gen app-preflight --desktop com.spotify.Client
mios-gen app-preflight --flatpak com.discordapp.Discord
mios-gen app-preflight --all
mios-gen app-preflight --portal-only
```

Each command emits JSON and exits nonzero on failure. `--all` checks every installed Flatpak sandbox and reports a nonzero checked count; it does not open all applications. A sandbox probe uses `--command=true`, so app-specific GPU, network, update and initialization failures still require the application's logs and an actual window check. Direct third-party launchers that bypass the MiOS shell retain their own startup behavior.

## Verification

The Rust tests exercise actual subprocess failures, bounded timeouts, process-group cleanup, large diagnostic output, desktop-entry parsing and mount detection. `tests/test-app-launch-health.py` adds compiled-binary controls for missing runtime dependencies, disabled recovery, invalid IDs, concurrent recovery and a stuck sandbox. Its activating test buses are born with private homes and runtime directories, so they cannot unmount the operator's document portal.

`tests/test-app-launch-qml.py` runs the actual shared launcher offscreen and records dispatches, duplicate clicks, terminal argv, environment cleanup, failures and timeouts. Opt in as the desktop operator with `MIOS_APP_LAUNCH_LIVE=1`; the tests launch synthetic subprocesses. `MIOS_APP_LAUNCH_BIN` can select an isolated candidate for the native tests. Live application checks additionally verify mapped windows rather than treating successful sandbox probes as proof that a GUI opened.
