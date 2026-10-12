<!-- AI-hint: Manual pages distilled from the source comments of cockpit.socket.d, sanitized, each passage anchored to the comment it came from. -->

# cockpit.socket.d

### Explicit 0.0.0.0:<port> (IPv4) -- the bare...

Explicit 0.0.0.0:<port> (IPv4) -- the bare `ListenStream=<port>` form
resolves to [::]:<port> dual-stack on dual-stack-by-default Linux,
which appears as `*:<port>` in ss output. WSL2's NAT-mode
localhostForwarding does NOT forward dual-stack binds; only explicit
IPv4 listens reach the Windows-side 127.0.0.1:<port>.
systemd does NOT expand ${VAR} / ${VAR:-default} in ListenStream -- it
fails "Failed to parse address ... Invalid argument" and the socket
then refuses with "Unit has no Listen setting", so cockpit never binds.
The port is therefore a LITERAL mirroring mios.toml [ports].cockpit
(MIOS_PORTS_COCKPIT); keep the two in sync.

<!-- mios-src:933490c7e200 from usr/lib/systemd/system/cockpit.socket.d/listen.conf:5-14 -->

### WSL2 Windows-side port forwarder (wslhost.exe) keeps 9090...

WSL2 Windows-side port forwarder (wslhost.exe) keeps 9090 mapped for
a short window after the Linux process exits, so the next boot of
the same distro hits EADDRINUSE on cockpit.socket and the unit fails
with "Failed with result 'resources'". ReusePort lets the kernel
bind anyway -- safe because cockpit.socket is the only listener
that ever wants 9090 inside the distro.

<!-- mios-src:07a4154c9b21 from usr/lib/systemd/system/cockpit.socket.d/listen.conf:17-22 -->
### Explicit 0.0.0.0:<port> (IPv4) -- the bare...

Explicit 0.0.0.0:<port> (IPv4) -- the bare `ListenStream=<port>` form
resolves to [::]:<port> dual-stack on dual-stack-by-default Linux,
which appears as `*:<port>` in ss output. WSL2's NAT-mode
localhostForwarding does NOT forward dual-stack binds; only explicit
IPv4 listens reach the Windows-side 127.0.0.1:<port>.
systemd does NOT expand ${VAR} / ${VAR:-default} in ListenStream -- it
fails "Failed to parse address ... Invalid argument" and the socket
then refuses with "Unit has no Listen setting", so cockpit never binds.
The port is therefore a LITERAL mirroring mios.toml [ports].cockpit
(MIOS_PORTS_COCKPIT); keep the two in sync.

<!-- mios-src:56bdbcfd60d1 from usr/lib/systemd/system/cockpit.socket.d/listen.conf:5-14 -->
