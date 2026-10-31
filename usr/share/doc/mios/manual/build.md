<!-- AI-hint: Manual pages distilled from the source comments of build, sanitized, each passage anchored to the comment it came from. -->

# build

### Offline time zone = the answer file's specialize TimeZone...

Offline time zone = the answer file's specialize TimeZone (reconcile, baseline N-c): the stock image is
"Pacific Standard Time", and switching zones in specialize with RealTimeIsUniversal=1 moved Windows' UTC clock
by the zone offset for the whole install session (TLS, signature timestamps and logs ran 7 h slow). Microsoft:
DISM /Set-TimeZone sets the default time zone of an offline image (an international command runs on its own).

<!-- mios-src:faf3022f9376 from usr/share/mios/windows/build/New-MiOSXboxImage.ps1:173-176 -->
