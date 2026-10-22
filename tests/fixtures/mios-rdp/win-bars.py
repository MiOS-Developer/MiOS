import os, shutil, sys, time
# colour bars with explicit (opaque) cell backgrounds: 8 columns x full height
cols = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
grey = [(16 * i, 16 * i, 16 * i) for i in range(16)]
def draw():
    w, h = shutil.get_terminal_size((40, 40))
    out = ["\x1b[?25l\x1b[H"]
    for row in range(h):
        line = []
        pal = cols if row < h * 2 // 3 else grey
        for x in range(w):
            r, g, b = pal[x * len(pal) // w]
            line.append(f"\x1b[48;2;{r};{g};{b}m ")
        out.append("".join(line) + ("\x1b[0m" if row == h - 1 else "\x1b[0m\r\n"))
    sys.stdout.write("".join(out)); sys.stdout.flush()
draw()
last = shutil.get_terminal_size((40, 40))
while True:
    time.sleep(0.5)
    s = shutil.get_terminal_size((40, 40))
    if s != last:
        last = s; draw()
