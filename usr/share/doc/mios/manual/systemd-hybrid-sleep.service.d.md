<!-- AI-hint: Manual pages distilled from the source comments of systemd-hybrid-sleep.service.d, sanitized, each passage anchored to the comment it came from. -->

# systemd-hybrid-sleep.service.d

### Systemd does not interact well with Xorg during system...

Systemd does not interact well with Xorg during system suspend when a kernel
driver requests a VT switch because systemd freezes all userspace processes,
including Xorg, before triggering kernel suspend. This deadlocks with the Xorg
server, which can't respond to the request to switch VTs because it was frozen
by systemd.

For now, disable freeze on suspend.

<!-- mios-src:82b8ec366137 from usr/lib/systemd/system/systemd-hybrid-sleep.service.d/nvidia-suspend-nofreeze.conf:1-7 -->
