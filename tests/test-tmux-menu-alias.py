#!/usr/bin/env python3
# AI-hint: Real tmux binary test of the tmux:menu route and the U1 mouse feature in a private server with two pty clients: the controller's client-exact route (send-keys -c <client> -K, key words from the generated @mios-menu-keys) opens the MiOS menu on exactly that client and acts; the bare `mios-menu` alias cannot be aimed (negative control: the tmux-os verify defect); `linux:mouse` makes tmux enable mouse reporting on a TERM=linux client and, through the generator, on a vt220 client; main's generated conf is the negative control.
# AI-related: tools/native/mios-unit-gen/src/tmux_os.rs, usr/share/mios/tmux/mios-keys.tmux.conf, usr/share/mios/mios.toml, docs/design/mios-global-bindings-final.md, usr/share/doc/mios/keybinds.md
# MIOS_TMUX_CONF overrides the conf under test (default: the worktree's generated mios-keys.tmux.conf); MIOS_TMUX_CONF_OLD
# the negative control (default: the conf at git main); MIOS_UNIT_GEN a candidate mios-unit-gen for the mouse_terms
# mechanism check. Never touches the operator's tmux servers (private -L sockets under a 0700 scratch TMUX_TMPDIR,
# killed at the end).
# --mouse-rows: app-independent raw SGR translation and painted-cell regression
# for status off/top/bottom (one, two and five lines) and single/split panes.
# MIOS_MOUSE_ROWS_RECEIPT_DIR retains client/pane byte traces and JSON evidence.
import os, pty, select, shutil, subprocess, sys, tempfile, time
import fcntl, json, re, shlex, struct, termios, tty
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = Path(os.environ.get('MIOS_TMUX_CONF', ROOT / 'usr/share/mios/tmux/mios-keys.tmux.conf'))
MENU_TEXT = b'Apps and TUIs'   # the root menu's first item ([[keybindings.tmux.menus]] id = "root")
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    return ok


class Client:
    """One pty tmux client (the first one starts the server with `conf`)."""

    def __init__(self, env, argv, size=None):
        self.out = b''
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            if size is not None:
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', size[1], size[0], 0, 0))
            os.execvpe('tmux', argv, env)
        self.drain(1.2)

    def drain(self, t=0.5):
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.out += os.read(self.fd, 65536)
                except OSError:
                    return

    def take(self, t=0.8):
        self.out = b''
        self.drain(t)
        return self.out

    def reap(self):
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass


class Server:
    def __init__(self, tmp, name, conf, term='xterm-256color', clients=1):
        self.name = name
        self.env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
        self.env.update(TMUX_TMPDIR=tmp, TERM=term)
        self.clients = [Client(self.env, ['tmux', '-L', name, '-f', str(conf), 'new-session', '-s', 'u1'])]
        for _ in range(clients - 1):
            self.clients.append(Client(self.env, ['tmux', '-L', name, 'attach', '-t', 'u1']))

    def t(self, *a):
        return subprocess.run(['tmux', '-L', self.name, *a], env=self.env, capture_output=True, text=True, timeout=10)

    def names(self):
        """client_name per Client, matched by client_pid."""
        rows = dict(l.split(' ', 1) for l in self.t('list-clients', '-F', '#{client_pid} #{client_name}').stdout.split('\n') if l)
        return [rows.get(str(c.pid), '') for c in self.clients]

    def zoomed(self):
        return self.t('display', '-p', '-t', 'u1', '#{window_zoomed_flag}').stdout.strip()

    def close(self):
        self.t('kill-server')
        for c in self.clients:
            c.reap()


def main():
    if not shutil.which('tmux'):
        print('skip: tmux is not installed')
        return 0
    if not CONF.is_file():
        check('generated tmux conf exists', False, CONF)
        return 1
    tmp = tempfile.mkdtemp(prefix='mios-u1-tmux-')
    os.chmod(tmp, 0o700)
    old = os.environ.get('MIOS_TMUX_CONF_OLD')
    if not old:
        r = subprocess.run(['git', '-C', str(ROOT), 'show', 'bcd6b3f22:usr/share/mios/tmux/mios-keys.tmux.conf'], capture_output=True, text=True)
        if r.returncode == 0:
            old = str(Path(tmp) / 'old.conf')
            Path(old).write_text(r.stdout)
    tag = f'mios-u1-{os.getpid()}'
    servers = []
    try:
        s = Server(tmp, tag, CONF, clients=2)
        servers.append(s)
        a, b = s.clients
        na, nb = s.names()
        check('two clients attached', na and nb and na != nb, (na, nb))
        keys = s.t('show', '-sv', '@mios-menu-keys').stdout.split()
        check('@mios-menu-keys publishes the route key words', keys == ['C-b', 'Space'], keys)
        # The client-exact route: send-keys -c B -K <prefix> <menu_key>.
        a.take(0.3); b.take(0.3)
        r = s.t('send-keys', '-c', nb, '-K', *keys)
        ob, oa = b.take(), a.take(0.3)
        check('route: the MiOS menu opens on client B', r.returncode == 0 and MENU_TEXT in ob, r.stderr.strip())
        check('route: client A shows no menu', MENU_TEXT not in oa, f'{len(oa)} bytes')
        s.t('split-window', '-d', '-t', 'u1')   # zoom needs a second pane
        z0 = s.zoomed()
        s.t('send-keys', '-c', nb, '-K', 'P'); b.take(0.5)
        s.t('send-keys', '-c', nb, '-K', 'z'); b.take(0.5)
        check('route: B drives the menu (Pane > Zoom acts)', z0 == '0' and s.zoomed() == '1', f'{z0} -> {s.zoomed()}')
        s.t('resize-pane', '-Z', '-t', 'u1')
        # Negative control (tmux-os verify defect 1): the alias cannot be aimed at B. tmux appends the CLI
        # arguments to the alias expansion, so `-c B` never reaches display-menu.
        a.take(0.3); b.take(0.3)
        p = subprocess.Popen(['tmux', '-L', s.name, 'mios-menu', '-c', nb], env=s.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ob, oa = b.take(), a.take(0.3)
        check('negative control: `mios-menu -c B` does not open on B', MENU_TEXT not in ob, f'opened on A={MENU_TEXT in oa} rc={p.poll()}')
        for n in (na, nb):
            s.t('send-keys', '-c', n, '-K', 'Escape')
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        # ... while the bare alias works: it opens the menu on a client tmux picks (not one the caller chose).
        a.take(0.3); b.take(0.3)
        p = subprocess.Popen(['tmux', '-L', s.name, 'mios-menu'], env=s.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ob, oa = b.take(), a.take(0.3)
        check('the bare mios-menu alias opens the menu on one tmux-picked client', (MENU_TEXT in oa) != (MENU_TEXT in ob), f'A={MENU_TEXT in oa} B={MENU_TEXT in ob}')
        for n in (na, nb):
            s.t('send-keys', '-c', n, '-K', 'Escape')
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        feats = s.t('show', '-s', 'terminal-features').stdout
        check('terminal-features carries linux:mouse', 'linux:mouse' in feats, [l for l in feats.splitlines() if 'mouse' in l])
        s.close(); servers.remove(s)
        # VT client: TERM=linux. With linux:mouse, tmux turns mouse reporting on there (CSI ? 1000 h), so gpm reports reach it.
        v = Server(tmp, tag + '-vt', CONF, term='linux')
        servers.append(v)
        v.clients[0].drain(1.0)
        check('TERM=linux client: tmux enables mouse reporting (CSI ?1000h)', b'\x1b[?1000h' in v.clients[0].out, f'{len(v.clients[0].out)} bytes')
        v.close(); servers.remove(v)
        if old:
            o = Server(tmp, tag + '-old', old)
            servers.append(o)
            r = o.t('show', '-sv', '@mios-menu-keys')
            check('negative control (main conf): no published route', not r.stdout.strip(), r.stdout.strip())
            check('negative control (main conf): no linux:mouse feature', 'linux:mouse' not in o.t('show', '-s', 'terminal-features').stdout)
            o.close(); servers.remove(o)
        else:
            check('negative-control conf available', False)
        gen = os.environ.get('MIOS_UNIT_GEN')
        if gen:
            import json, tomllib
            ssot = tomllib.loads((ROOT / 'usr/share/mios/mios.toml').read_text())
            # The feature mechanism on a terminfo WITHOUT kmous (vt220): only an entry here turns reporting on.
            for terms, want in ((['vt220'], True), ([], False)):
                ssot['keybindings']['tmux']['mouse_terms'] = terms
                j = Path(tmp) / 'ssot.json'
                j.write_text(json.dumps(ssot))
                r = subprocess.run([gen, 'keybindings', '--from-json', str(j), '--emit-json'], capture_output=True, text=True, timeout=60)
                c = Path(tmp) / f'gen-{len(terms)}.conf'
                c.write_text(json.loads(r.stdout)['usr/share/mios/tmux/mios-keys.tmux.conf'] if r.returncode == 0 else '')
                g = Server(tmp, f'{tag}-gen{len(terms)}', c, term='vt220')
                servers.append(g)
                g.clients[0].drain(1.0)
                got = b'\x1b[?1000h' in g.clients[0].out
                check(f'generator mouse_terms={terms}: TERM=vt220 (no kmous) client mouse reporting = {want}', r.returncode == 0 and got == want, r.stderr.strip()[-200:])
                g.close(); servers.remove(g)
        else:
            print('info: MIOS_UNIT_GEN unset; generator mouse_terms mechanism check skipped', flush=True)
    finally:
        for s in servers:
            s.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


# --mouse-rows: app-independent client -> tmux -> pane transport regression.
# capture-pane cells and pane offsets are zero based. SGR client cells are one
# based and INCLUDE the top status rows. tmux owns the inverse translation.
def pane_cell_to_client(col, row, left, top, position, status_lines):
    if min(col, row, left, top, status_lines) < 0:
        raise ValueError('negative cell or status height')
    return col + left + 1, row + top + (status_lines if position == 'top' else 0) + 1


def mouse_probe(path):
    """Paint a stable target; record application input without parsing it."""
    saved = termios.tcgetattr(0)
    try:
        tty.setraw(0)
        os.write(1, b'\x1b[?1003h\x1b[?1006h\x1b[2J\x1b[4;5HR3747')
        Path(path + '.ready').touch()
        with open(path, 'ab', buffering=0) as log:
            while True:
                data = os.read(0, 1024)
                if not data or data == b'q':
                    break
                if data == b'd':
                    os.write(1, b'\x1b[?1003l\x1b[?1006l')
                    Path(path + '.disabled').touch()
                    continue
                log.write(data)
    finally:
        os.write(1, b'\x1b[?1003l\x1b[?1006l')
        termios.tcsetattr(0, termios.TCSANOW, saved)
    return 0


def painted_cells(data, width=80, height=24):
    """Decode ASCII target cells from the actual full tmux client redraw.

    Only cursor/erase operations are needed for this stable ASCII fixture.
    Retain the raw ANSI stream for independent terminal replay and audit.
    This decoder never consults pane offsets or status geometry.
    """
    cells = [[' '] * width for _ in range(height)]
    x = y = 0
    saved = (0, 0)
    tokens = re.findall(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][^\n]|\x1b.|[^\x1b]', data.decode('utf-8', errors='replace'))
    for token in tokens:
        if token.startswith('\x1b['):
            nums = token[2:-1]
            if any(c not in '0123456789;' for c in nums):
                continue
            a = [int(n or '0') for n in nums.split(';')]
            n, op = a[0] or 1, token[-1]
            if op in 'Hf':
                y, x = n - 1, (a[1] or 1) - 1 if len(a) > 1 else 0
            elif op == 'G': x = n - 1
            elif op == 'd': y = n - 1
            elif op == 'A': y -= n
            elif op == 'B': y += n
            elif op == 'C': x += n
            elif op == 'D': x -= n
            elif op == 'J' and a[0] == 2: cells = [[' '] * width for _ in range(height)]
            elif op == 'K':
                start, end = (0, width) if a[0] == 2 else (0, x + 1) if a[0] == 1 else (x, width)
                cells[min(y, height - 1)][max(0, start):min(width, end)] = [' '] * max(0, min(width, end) - max(0, start))
            elif op == 's': saved = (x, y)
            elif op == 'u': x, y = saved
            x, y = max(0, min(width - 1, x)), max(0, min(height - 1, y))
        elif token == '\x1b7': saved = (x, y)
        elif token == '\x1b8': x, y = saved
        elif token.startswith('\x1b'): continue
        elif token == '\r': x = 0
        elif token == '\n': y = min(height - 1, y + 1)
        elif token == '\b': x = max(0, x - 1)
        elif token == '\t': x = min(width - 1, (x // 8 + 1) * 8)
        elif token >= ' ':
            cells[y][x] = token
            x = min(width - 1, x + 1)
    return [''.join(row) for row in cells]


def mouse_rows_main():
    """Use private sockets only; retain positive and wrong-client-row traces."""
    if not shutil.which('tmux') or not CONF.is_file():
        print('FAIL mouse rows require tmux and the generated MiOS configuration')
        return 1
    receipt_dir = os.environ.get('MIOS_MOUSE_ROWS_RECEIPT_DIR')
    out = Path(receipt_dir or tempfile.mkdtemp(prefix='mios-mouse-row-receipts-'))
    out.mkdir(parents=True, exist_ok=True)
    receipt = {'tmux': subprocess.check_output(['tmux', '-V'], text=True).strip(),
               'configuration': str(CONF), 'physical_input_tested': False, 'cases': []}
    cases = [('off', 0), ('top', 1), ('bottom', 1), ('top', 2), ('bottom', 2), ('top', 5), ('bottom', 5)]
    try:
        for position, lines in cases:
            for layout in ('single', 'vertical', 'horizontal'):
                label = f'{position}-{lines}-{layout}'
                with tempfile.TemporaryDirectory(prefix='mios-mouse-rows-') as tmp:
                    env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
                    env.update(TMUX_TMPDIR=tmp, TERM='xterm-256color')
                    prefix = ['tmux', '-L', 'mios-mouse-rows']
                    def run(*args):
                        return subprocess.check_output(prefix + list(args), env=env, text=True, timeout=8).rstrip('\n')
                    client = None
                    try:
                        client = Client(env, prefix + ['-f', str(CONF), 'new-session', '-s', 'rows'], size=(80, 24))
                        run('set', '-t', 'rows', 'status', 'on' if lines == 1 else str(lines) if lines else 'off')
                        run('set', '-t', 'rows', 'status-position', position if position != 'off' else 'top')
                        for index in range(lines):
                            run('set', '-t', 'rows', f'status-format[{index}]', f'STATUS{index}')
                        if layout != 'single':
                            run('split-window', '-t', 'rows', '-v' if layout == 'vertical' else '-h', 'sleep 90')
                        pane, left, top, pw, ph = run('display', '-p', '-t', 'rows', '#{pane_id} #{pane_left} #{pane_top} #{pane_width} #{pane_height}').split()
                        left, top, pw, ph = map(int, (left, top, pw, ph))
                        raw = Path(tmp) / 'input'
                        command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--mouse-probe', str(raw)])
                        run('respawn-pane', '-k', '-t', pane, command)
                        deadline = time.monotonic() + 5
                        while not Path(str(raw) + '.ready').exists():
                            if time.monotonic() > deadline: raise RuntimeError('mouse probe did not start')
                            time.sleep(.02)
                        client.take(.1)
                        run('refresh-client', '-t', run('list-clients', '-F', '#{client_name}'))
                        ansi = client.take(.2)
                        screen = painted_cells(ansi)
                        hits = [(r.index('R3747'), y) for y, r in enumerate(screen) if 'R3747' in r]
                        expected = pane_cell_to_client(4, 3, left, top, position, lines)
                        check(label + ': painted client cell', hits == [(expected[0] - 1, expected[1] - 1)], str(hits))
                        capture = run('capture-pane', '-p', '-t', pane)
                        check(label + ': painted pane cell', capture.splitlines()[3][4:9] == 'R3747')
                        injected = bytearray()
                        def click(x, y):
                            payload = f'\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m'.encode()
                            injected.extend(payload)
                            os.write(client.fd, payload)
                            client.drain(.12)
                            return raw.read_bytes() if raw.exists() else b''
                        received = click(*expected)
                        want = b'\x1b[<0;5;4M\x1b[<0;5;4m'
                        check(label + ': native translation exactly once', received == want, received.hex())
                        wrong = None
                        if lines and position == 'top':
                            wrong = click(expected[0], expected[1] - lines)[len(received):]
                            check(label + ': pre-subtracted client row misses target', want not in wrong, wrong.hex())
                        before = raw.read_bytes()
                        if lines:
                            click(70, 1 if position == 'top' else 24)
                            check(label + ': status does not become pane input', raw.read_bytes() == before)
                        if layout != 'single':
                            click(left if layout == 'horizontal' else expected[0], top if layout == 'vertical' else expected[1])
                            check(label + ': split border does not become pane input', raw.read_bytes() == before)
                        run('send-keys', '-t', pane, '-l', 'd')
                        deadline = time.monotonic() + 3
                        while not Path(str(raw) + '.disabled').exists():
                            if time.monotonic() > deadline: raise RuntimeError('probe did not disable tracking')
                            time.sleep(.02)
                        client.drain(.1)
                        check(label + ': application tracking is disabled', run('display', '-p', '-t', pane, '#{mouse_any_flag}') == '0')
                        click(*expected)
                        check(label + ': disabled tracking blocks mouse input', raw.read_bytes() == before)
                        final = raw.read_bytes()
                        (out / (label + '.client.ansi')).write_bytes(ansi)
                        (out / (label + '.client-input.bin')).write_bytes(injected)
                        (out / (label + '.pane-input.bin')).write_bytes(final)
                        (out / (label + '.cells.txt')).write_text('\n'.join(screen) + '\n')
                        (out / (label + '.pane.txt')).write_text(capture + '\n')
                        receipt['cases'].append({'case': label, 'pane': pane, 'pane_geometry': [left, top, pw, ph],
                            'pane_cell_zero_based': [4, 3], 'client_cell_one_based': list(expected),
                            'painted_client_cells_zero_based': hits, 'received_hex': received.hex(),
                            'pre_subtracted_received_hex': None if wrong is None else wrong.hex()})
                    finally:
                        if client is not None:
                            try: run('kill-server')
                            finally:
                                client.reap()
                                os.close(client.fd)
    except Exception as error:
        check('mouse row fixture exception', False, repr(error))
        receipt['error'] = repr(error)
    receipt.update(checks=len(res), passed_checks=sum(res), passed=bool(res) and all(res))
    (out / 'mios-mouse-rows.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'{sum(res)}/{len(res)} mouse row checks passed; receipt {out}', flush=True)
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    if sys.argv[1:2] == ['--mouse-probe']:
        sys.exit(mouse_probe(sys.argv[2]))
    sys.exit(mouse_rows_main() if sys.argv[1:] == ['--mouse-rows'] else main())
