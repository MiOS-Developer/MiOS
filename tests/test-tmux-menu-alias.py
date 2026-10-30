#!/usr/bin/env python3
# AI-hint: Real tmux binary test of the tmux:menu route and the U1 mouse feature in a private server with two pty clients: the controller's client-exact route (send-keys -c <client> -K, key words from the generated @mios-menu-keys) opens the MiOS menu on exactly that client and acts; the bare `mios-menu` alias cannot be aimed (negative control: the tmux-os verify defect); `linux:mouse` makes tmux enable mouse reporting on a TERM=linux client and, through the generator, on a vt220 client; main's generated conf is the negative control.
# AI-related: tools/native/mios-unit-gen/src/tmux_os.rs (render_tmux_keys), usr/share/mios/tmux/mios-keys.tmux.conf, usr/share/mios/mios.toml [keybindings.tmux], docs/design/mios-global-bindings-final.md 3.3, usr/share/doc/mios/keybinds.md
# MIOS_TMUX_CONF overrides the conf under test (default: the worktree's generated mios-keys.tmux.conf); MIOS_TMUX_CONF_OLD
# the negative control (default: the conf at git main); MIOS_UNIT_GEN a candidate mios-unit-gen for the mouse_terms
# mechanism check. Never touches the operator's tmux servers (private -L sockets under a 0700 scratch TMUX_TMPDIR,
# killed at the end).
import os, pty, select, shutil, subprocess, sys, tempfile, time
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

    def __init__(self, env, argv):
        self.out = b''
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
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


if __name__ == '__main__':
    sys.exit(main())
