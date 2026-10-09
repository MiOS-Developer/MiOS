<!-- AI-hint: Prose harvested out of source comments by `mios-manual harvest`; each passage carries the mios-src anchor that proves which comment it came from. -->

# Harvested notes

### AI-hint

AI-hint: Overrides the ConditionUser=!@system check for localsearch-3 to allow the service to run for the mios system user (UID 992), preventing GNOME file manager crashes caused by missing Tracker3.Miner.Files DBus responses.
AI-related: mios-bootstrap, localsearch-3.service

MiOS dev VM creates the `mios` user with UID 992 (system class
range, 0-999). Upstream localsearch-3 has ConditionUser=!@system
so it refuses to start for system users. Without this drop-in,
nautilus + gnome-files dbus-activate Tracker3.Miner.Files,
localsearch-3 fails the condition check, dbus returns
ServiceUnknown, the GTK app null-derefs in its bookmark loader.

Future: ideally the mios user gets UID 1000+ at install time
(mios-bootstrap useradd -u 1000 per task_908 / task_3141) and this drop-in becomes
unnecessary. Until then this is the live workaround.

<!-- mios-src:16889981906b from usr/lib/systemd/user/localsearch-3.service.d/10-mios-allow-system-uid.conf:1-13 -->
