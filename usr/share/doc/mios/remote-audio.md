<!-- AI-hint: Native RDP speaker (MS-RDPEA/RDPSND) and microphone (MS-RDPEAI) forwarding through session-owned PipeWire-Pulse nodes: format negotiation, latency gate, silence idle, cleanup, audio-server restart recovery and reproducible private-lab verification. -->
<!-- AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_audio.rs, src/mios-rdp-server/crates/mios-rdp-server/src/redirect.rs, usr/share/mios/mios.toml, usr/share/mios/configurator/mios.html, tests/fixtures/mios-rdp/speaker.py, tests/fixtures/mios-rdp/microphone.py -->

# Remote audio

The native MiOS RDP server forwards audio in both directions. Each direction
uses session-owned PipeWire-Pulse nodes that belong to the RDP connection. The
server never changes the default sink or source and never opens a physical audio
device.

## Speaker (server to client)

When the client enables remote audio playback (Windows App: **Device & audio
redirection → Play sound on this device**; FreeRDP: `/sound`), the RDPSND channel
negotiates PCM. The server offers 16-bit PCM at 48 kHz stereo, 44.1 kHz stereo,
48 kHz mono, 44.1 kHz mono and 22.05 kHz stereo, in that order. It streams the
first of these formats that the client also accepts.

For each connection, the server then creates a sink named
`mios-remote-speaker-<server-pid>-<generation>`, described as
`MiOS-Remote-Speaker`. The sink uses the negotiated rate and channel count.
Applications choose it like any other output device. PipeWire resamples into
it. The server reads `speaker_packet_ms` packets from the sink's private FIFO
and sends each packet as a wave.

* **Bounded queue and latency.** The sink FIFO holds no more than two packets'
  worth of stale audio (16 KiB minimum). Older samples are discarded when the
  sink opens. Every Wave Confirm from the client advances the stream clock. New
  waves are withheld (`latency_dropped`) while more than
  `speaker_max_latency_ms` of sent audio is unconfirmed. Audio therefore skips
  ahead after a network stall instead of staying late. Clients that never
  confirm, or stop confirming for a long time, are never starved: the gate
  reopens.
* **Silence and underruns.** After `speaker_idle_ms` of exact digital silence,
  the server stops sending (`idle_packets`). It resumes with the first audible
  packet. With `0`, it always streams. A poll period with no samples from the
  sink counts as a source gap (`source_gaps`). Partial packets are discarded,
  so the client restarts playback from fresh audio.
* **Cleanup.** When the client closes the channel or disconnects, or the
  server receives SIGTERM, the sink worker stops. It unloads only the module
  it verifiably owns, matched by module ID, module type and its exact FIFO.
  Then it removes the FIFO and its directory. Shutdown waits for these workers.

The `speaker` object in the session statistics JSON reports readiness, the sink,
the format, waves/bytes sent, latency drops, idle packets, source gaps, the
unconfirmed backlog, recoveries and errors. The
`device_forwarding.audio_output` capability is `implemented: true`.

## Microphone (client to server)

MS-RDPEAI microphone forwarding is unchanged. It accepts 48 kHz 16-bit PCM,
mono or stereo, and downmixes stereo. It writes to a per-connection
`mios-remote-microphone-<pid>-<generation>` pipe source through a bounded queue.

## Audio-server restart recovery

PipeWire-Pulse removes every pipe module when it restarts or crashes. The
speaker worker detects this as its FIFO's writer hanging up. The microphone
worker detects it as `POLLERR` on its write end, polled even while no packets
arrive. Each worker then recreates its sink or source under the same name, on
a new FIFO. The client connection stays up. Applications reconnect to the same
device name.

Recovery tries `audio_recovery_attempts` times, `audio_recovery_interval_ms`
apart, for each outage. A successful reopen resets the budget. `0` disables
recovery. `microphone.recoveries` and `speaker.recoveries` count successful
recreations. In normal sessions, PipeWire-Pulse is socket-activated, so the
first control call after a crash starts it again.

## Configuration

| SSOT key | Purpose |
| --- | --- |
| `remote.rdp.redirect.audio_output` | Permit speaker forwarding (default `true`). The client still decides. |
| `remote.rdp.redirect.microphone` | Permit microphone forwarding. |
| `remote.rdp.native.audio_control_program` | PipeWire-Pulse control executable (`pactl`). |
| `remote.rdp.native.audio_server` | `""` = the operator's PipeWire-Pulse socket; `unix:/<socket>` = an isolated audio server. |
| `remote.rdp.native.audio_recovery_attempts`, `audio_recovery_interval_ms` | Restart recovery budget and interval. |
| `remote.rdp.native.speaker_packet_ms` | Wave size, 10–100 ms. |
| `remote.rdp.native.speaker_max_latency_ms` | Unconfirmed-audio ceiling before waves are withheld. |
| `remote.rdp.native.speaker_idle_ms` | Silence before streaming pauses; `0` = always stream. |
| `remote.rdp.native.microphone_queue_packets` | Bounded 10 ms microphone queue. |
| `remote.rdp.naming.speaker_sink_prefix`, `microphone_source_prefix` | Per-connection node name prefixes. |

Every key appears in the configurator under **Native server**. A changed value
takes effect after the native RDP listener restarts. The desktop does not need
a restart.

## Verification

`tests/fixtures/mios-rdp/speaker.py` is opt-in and runs as the desktop operator
with `MIOS_RDP_NATIVE_LIVE=1`. Set `MIOS_RDP_BIN` to a private test-auth build
and pass `--nested-descriptor <owned session.json>` and `--report <path>`. The
fixture starts two private PipeWire stacks. Lab A is the server's audio server
and lab B is FreeRDP's. Each stack has its own runtime directory, home and
state, no session bus, and no hardware monitors. The operator's PipeWire-Pulse
is only read, to confirm its defaults are unchanged.

| Case | Expectation |
| --- | --- |
| `speaker` | A 440 Hz tone played into the session sink reaches FreeRDP's playback sink (RMS > 1000). Silence before the tone, sink cleanup and attached-server SIGTERM are checked. |
| `speaker-disabled` | Negative control: with `audio_output = false`, no sink is created and the client receives silence. |
| `restart` | Microphone and speaker work, lab A's `pipewire-pulse` is restarted, both nodes come back under the same names, and the tone and microphone signal pass again. |
| `restart-norecovery` | Negative control: with `audio_recovery_attempts = 0`, neither node returns. The code before recovery behaved the same way. |

`remote_audio.rs` also has an ignored unit test,
`speaker_sink_tone_reaches_rdpsnd_waves_in_private_lab`. It takes
`MIOS_REMOTE_AUDIO_LAB=unix:/…/pulse/native` and checks the sink-to-wave path
without a network session.

Protocol references: [MS-RDPEA](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpea/),
[MS-RDPEAI](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpeai/).
Native nodes: [PipeWire pipe-sink/pipe-source](https://docs.pipewire.org/page_pulse_module_pipe_sink.html).
