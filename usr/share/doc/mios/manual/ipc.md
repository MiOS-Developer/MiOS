<!-- AI-hint: Manual pages distilled from the source comments of ipc, sanitized, each passage anchored to the comment it came from. -->

# ipc

### varlink_activator.py — task_687 WS-NODE Point-to-point Varlink...

varlink_activator.py — task_687 WS-NODE
Point-to-point Varlink IPC socket activator and typed interface compiler.

Exposes typed Varlink JSON-RPC interfaces over point-to-point Unix sockets with
systemd socket activation and <1ms RPC roundtrip latency.

<!-- mios-src:eb7ffbfc49b2 from usr/lib/mios/ipc/varlink_activator.py:5-11 -->

### shm_ring.py — task_711 WS-NODE Lock-free POSIX shared memory...

shm_ring.py — task_711 WS-NODE
Lock-free POSIX shared memory circular ring IPC engine.

Allocates POSIX shared memory (/dev/shm) with atomic lock-free SPSC circular rings
and eventfd signaling for zero-copy 4K 60FPS video and audio streaming (<1us latency).

<!-- mios-src:58d445c05c43 from usr/libexec/mios/ipc/shm_ring.py:4-10 -->
