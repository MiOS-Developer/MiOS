#!/usr/bin/env python3
# AI-hint: Real tmux binary test of the U1 tmux projection in a private server with pty clients: the generated `mios-menu` command alias opens the centred MiOS menu (typed key and `send-keys -c <client> -K`, the controller path) and acts; `linux:mouse` makes tmux enable mouse reporting on a TERM=linux client (VT + gpm); negative controls use main's generated conf.
# AI-related: tools/native/mios-unit-gen/src/lib.rs (render_keybindings), usr/share/mios/tmux/mios-keys.tmux.conf, usr/share/mios/mios.toml [keybindings.tmux], docs/design/mios-global-bindings-tests.md
# MIOS_TMUX_CONF overrides the conf under test (default: the worktree's generated mios-keys.tmux.conf); MIOS_TMUX_CONF_OLD
# the negative control (default: the conf at git main). Never touches the operator's tmux servers (private -L sockets
# under a scratch TMUX_TMPDIR, killed at the end).
import os, pty, select, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = Path(os.environ.get('MIOS_TMUX_CONF', ROOT / 'usr/share/mios/tmux/mios-keys.tmux.conf'))
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    return ok


class Server:
    def __init__(self, tmp, name, conf, term='xterm-256color'):
        self.name = name
        self.env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_PANE')}
        self.env.update(TMUX_TMPDIR=tmp, TERM=term)
        self.out = b''
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe('tmux', ['tmux', '-L', name, '-f', str(conf), 'new-session', '-s', 'u1', '-x', '120', '-y', '40'], self.env)
        self.drain(1.5)

    def drain(self, t=0.5):
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.out += os.read(self.fd, 65536)
                except OSError:
                    return

    def t(self, *a):
        return subprocess.run(['tmux', '-L', self.name, *a], env=self.env, capture_output=True, text=True, timeout=10)

    def zoomed(self):
        return self.t('display', '-p', '-t', 'u1', '#{window_zoomed_flag}').stdout.strip()

    def menu(self):
        """Run the alias as the controller does (command line, no prefix key). display-menu waits until it closes."""
        p = subprocess.Popen(['tmux', '-L', self.name, 'mios-menu'], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.drain(0.8)
        return p

    def close(self):
        self.t('kill-server')
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass


def main():
    if not CONF.is_file():
        check('generated tmux conf exists', False, CONF)
        return 1
    tmp = tempfile.mkdtemp(prefix='mios-u1-tmux-')
    old = os.environ.get('MIOS_TMUX_CONF_OLD')
    if not old:
        r = subprocess.run(['git', '-C', str(ROOT), 'show', 'main:usr/share/mios/tmux/mios-keys.tmux.conf'], capture_output=True, text=True)
        if r.returncode == 0:
            old = str(Path(tmp) / 'old.conf')
            Path(old).write_text(r.stdout)
    tag = f'mios-u1-{os.getpid()}'
    servers = []
    try:
        s = Server(tmp, tag, CONF)
        servers.append(s)
        alias = [l for l in s.t('show', '-s', 'command-alias').stdout.splitlines() if 'mios-menu=' in l]
        check('server has the mios-menu command alias, centred', alias and '-x C -y C' in alias[0], alias)
        conf = CONF.read_text()
        mouse_items = next(l for l in conf.splitlines() if l.startswith('bind-key -T root MouseDown3Status ')).split('-x M -y M ', 1)[1]
        check('alias items == MouseDown3 menu items (one list)', alias and alias[0].rstrip('"').split('-x C -y C ', 1)[1].replace('\\"', '"') == mouse_items)
        s.t('split-window', '-t', 'u1'); s.drain()
        z0 = s.zoomed()
        p = s.menu()
        check('mios-menu opens a menu on the attached client (the command waits for it)', p.poll() is None and z0 == '0', f'zoomed={z0}')
        os.write(s.fd, b'z'); s.drain(0.8)
        check('typing the item key acts (Zoom) and closes the menu', s.zoomed() == '1' and p.poll() == 0, f'zoomed={s.zoomed()} rc={p.poll()}')
        s.t('resize-pane', '-Z', '-t', 'u1'); s.drain()
        client = s.t('list-clients', '-F', '#{client_name}').stdout.strip().splitlines()[0]
        p = s.menu()
        r = s.t('send-keys', '-c', client, '-K', 'z'); s.drain(0.8)
        check('controller path: send-keys -c <client> -K z picks the item', r.returncode == 0 and s.zoomed() == '1' and p.poll() == 0, f'{r.stderr.strip()} zoomed={s.zoomed()}')
        feats = s.t('show', '-s', 'terminal-features').stdout
        check('terminal-features carries linux:mouse', 'linux:mouse' in feats, [l for l in feats.splitlines() if 'mouse' in l])
        s.close(); servers.remove(s)
        # VT client: TERM=linux. With linux:mouse, tmux turns mouse reporting on there (CSI ? 1000 h), so gpm reports reach it.
        v = Server(tmp, tag + '-vt', CONF, term='linux')
        servers.append(v)
        v.drain(1.0)
        check('TERM=linux client: tmux enables mouse reporting (CSI ?1000h)', b'\x1b[?1000h' in v.out, f'{len(v.out)} bytes')
        v.close(); servers.remove(v)
        if old:
            o = Server(tmp, tag + '-old', old)
            servers.append(o)
            r = o.t('mios-menu')
            check('negative control (main conf): mios-menu is an unknown command', r.returncode != 0 and 'unknown command' in r.stderr, r.stderr.strip())
            check('negative control (main conf): no linux:mouse feature', 'linux:mouse' not in o.t('show', '-s', 'terminal-features').stdout)
            o.close(); servers.remove(o)
            # Not a negative: tmux 3.7c already enables reporting on TERM=linux because the linux terminfo has kmous.
            # linux:mouse pins it; the feature mechanism itself is proven below on a terminfo WITHOUT kmous (vt220).
            ov = Server(tmp, tag + '-oldvt', old, term='linux')
            servers.append(ov)
            ov.drain(1.0)
            on = b'\x1b[?1000h' in ov.out
            print(f'info: main conf on TERM=linux also enables reporting: {on}', flush=True)
            ov.close(); servers.remove(ov)
        else:
            check('negative-control conf available', False)
        gen = os.environ.get('MIOS_UNIT_GEN')
        if gen:
            import json, tomllib
            ssot = tomllib.loads((ROOT / 'usr/share/mios/mios.toml').read_text())
            for terms, want in ((['vt220'], True), ([], False)):
                ssot['keybindings']['tmux']['mouse_terms'] = terms
                j = Path(tmp) / 'ssot.json'
                j.write_text(json.dumps(ssot))
                r = subprocess.run([gen, 'keybindings', '--from-json', str(j), '--emit-json'], capture_output=True, text=True, timeout=60)
                c = Path(tmp) / f'gen-{len(terms)}.conf'
                c.write_text(json.loads(r.stdout)['usr/share/mios/tmux/mios-keys.tmux.conf'] if r.returncode == 0 else '')
                g = Server(tmp, f'{tag}-gen{len(terms)}', c, term='vt220')
                servers.append(g)
                g.drain(1.0)
                got = b'\x1b[?1000h' in g.out
                check(f'generator mouse_terms={terms}: TERM=vt220 (no kmous) client mouse reporting = {want}', r.returncode == 0 and got == want, r.stderr.strip()[-200:])
                g.close(); servers.remove(g)
        else:
            print('info: MIOS_UNIT_GEN unset; generator mouse_terms mechanism check skipped', flush=True)
    finally:
        for s in servers:
            s.close()
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
