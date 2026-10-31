<!-- AI-hint: Manual pages distilled from the source comments of ventulusd.service.d, sanitized, each passage anchored to the comment it came from. -->

# ventulusd.service.d

### Replay any hand-back record a previous instance left...

Replay any hand-back record a previous instance left un-replayed (its
ExecStopPost was killed or never ran) BEFORE the daemon starts. Without this
the new instance reads the header still in manual mode, records "manual
<duty>" as the original, and every later stop hands back a fixed duty
instead of the firmware curve. No record (the normal case) is a no-op.

<!-- mios-src:ea9b7db89155 from usr/lib/systemd/system/ventulusd.service.d/10-mios.conf:18-22 -->

### Upstream leaves the socket 0666 in a 0755 directory with no...

Upstream leaves the socket 0666 in a 0755 directory with no authentication,
so any local account could activate profiles or take overrides. Close the
directory to others and grant wheel traversal with an ACL. systemd re-chowns
and re-chmods RuntimeDirectory on every exec, but chmod 0750 only resets the
ACL mask to r-x, so the wheel entry stays effective. (A socket-proxyd
front-end was tried: SELinux confines systemd_socket_proxyd_t away from
var_run_t sockets.)

<!-- mios-src:42c3a2aa9016 from usr/lib/systemd/system/ventulusd.service.d/10-mios.conf:24-30 -->
