# AI-hint: Apply layered MiOS SSOT colors using the native terminal projector.
# AI-related: tools/native/mios-gen/src/terminal.rs, usr/share/mios/mios.toml
# Palette applied (rendered from SSOT [colors]): bg #282262 fg #E7DFD3 accent #1A407F
[ -t 1 ] || return 0
/usr/bin/mios-gen terminal-colors --root / --term "$TERM"
