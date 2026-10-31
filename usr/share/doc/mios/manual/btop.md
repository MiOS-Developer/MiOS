<!-- AI-hint: Manual pages distilled from the source comments of btop, sanitized, each passage anchored to the comment it came from. -->

# btop

### Presets accessible via `p` 0-9 inside btop. Format...

Presets accessible via `p` 0-9 inside btop. Format: <boxes>:<mode>:<theme>
where mode = 0 (full) or 1 (compact).

Slot 4 (proc only) is the MiOS default; mios-btop.sh launches
`btop -p 4` on plain invocation.

  0  cpu compact
  1  cpu full
  2  cpu+mem compact
  3  cpu+mem full
  4  proc only             <- canonical launch via `btop -p 4`
  5  cpu+mem+net+proc full

<!-- mios-src:2add1ac7df9c from etc/btop/btop.conf:23-34 -->

### Boxes shown at launch -- matches preset 4 (proc only) so...

Boxes shown at launch -- matches preset 4 (proc only) so plain
`btop` invocations (without -p) still render the operator's
canonical view.

<!-- mios-src:abbc7ef8a3a1 from etc/btop/btop.conf:53-55 -->

### Update time in milliseconds. Operator

Update time in milliseconds. Operator: "not 500ms update speed" -- the
displayed value was 2000ms. Lock to 500ms here so btop refreshes 2x/sec
on the dev VM out of the box.

<!-- mios-src:77fe03ac20f8 from etc/btop/btop.conf:58-60 -->
### Default symbols to use for graph creation, "braille"...

* Default symbols to use for graph creation, "braille", "block" or "tty".
* "braille" offers the highest resolution but might not be included in all fonts.
* "block" has half the resolution of braille but uses more common characters.
* "tty" uses only 3 different symbols but will work with most fonts and should work in a real TTY.
* Note that "tty" only has half the horizontal resolution of the other two, so will show a shorter historical view.

<!-- mios-src:1ada1c3d5b25 from etc/btop/btop.conf:41-45 -->
