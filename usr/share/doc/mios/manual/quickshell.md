<!-- AI-hint: Manual pages distilled from the source comments of quickshell, sanitized, each passage anchored to the comment it came from. -->

# quickshell

### Open the staged file "$1" (relative) on fd 3 and prove that...

Open the staged file "$1" (relative) on fd 3 and prove that the open file is that very stage file: the kernel's
own path of the open file (/proc/self/fd/3) must be "$STAGE/$1", it must be a regular file, and it must belong to
the stage's owner. The copy then reads only through fd 3, so a stage entry or ancestor swapped for a symlink (or a
hard link to a root-owned file) between validate_stage and the copy can never make root read anything else.

<!-- mios-src:6051bc651cc5 from src/quickshell/install-candidate.sh:136-139 -->

### Every directory strictly below the root on the way to...

Every directory strictly below the root on the way to "$R/$1", deepest first. ${d%/*} always shortens d, so the
walk ends for every root: for --root / (R="") it stops after /usr instead of looping on "dirname / = /", which
matched the old "$R"/* test forever.

<!-- mios-src:bc352b75f627 from src/quickshell/install-candidate.sh:161-163 -->
