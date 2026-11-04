#!/usr/bin/env python3
# AI-hint: Real-binary test of M1 (C-33): in the miosd session selector the controller's West button (Tab, nav grammar) opens a new session like `n`; the TUI runs in a private tmux server with an isolated filesystem and fake mios-terminal and tailscale, so nothing reaches the operator's tmux-os. Negative control (MIOSD_OLD, a pre-C-33 miosd) ignores Tab; `n` proves the harness on it.
# AI-related: src/mios-rs/miosd/src/sessions.rs, usr/share/mios/mios.toml [terminal.sessions], usr/share/doc/mios/keybinds.md, docs/design/mios-global-bindings-final.md
# Env: MIOSD = the candidate miosd (explicit, otherwise built/installed native catalog); MIOSD_OLD = a pre-C-33 miosd for the negative control (optional: the
# deployed miosd already carries C-33 from main, so it is no control any more).
import os, re, shlex, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def candidate():
    explicit = os.environ.get('MIOSD')
    if explicit:
        return explicit  # An explicit missing candidate fails; never silently run another binary.
    for path in (ROOT / 'usr/libexec/mios/miosd',
                 ROOT / 'src/mios-rs/target/release/miosd',
                 ROOT / 'tools/native/target/x86_64-unknown-linux-musl/release/miosd',
                 Path('/usr/libexec/mios/miosd')):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return ''


CAND = candidate()
OLD = os.environ.get('MIOSD_OLD', '')
TERMINAL = '/usr/libexec/mios/mios-terminal'
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    return ok


def wait(pred, timeout=6.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    return pred()


def copy_executable(source, root, destination):
    """Copy a fixture executable plus its ELF loader/libraries into a private root."""
    target = root / destination.lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    result = subprocess.run(['ldd', str(source)], capture_output=True, text=True, timeout=10)
    for library in re.findall(r'(?:=>\s*)?(/[a-zA-Z0-9_./+-]+)', result.stdout):
        path = Path(library)
        if path.is_file():
            dest = root / library.lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def selector(miosd, tmp, tag, keys):
    """The TUI runs on a private tmux pty and can exec only a fixture launcher."""
    d = Path(tmp) / tag
    d.mkdir()
    uid, gid = (1000, 1000) if os.geteuid() == 0 else (os.getuid(), os.getgid())
    if os.geteuid() == 0:
        root = d / 'root'
        fixture = root / 'fixture'
        fixture.mkdir(parents=True)
        fixture.chmod(0o700)
        os.chown(fixture, uid, gid)
        for directory in ('dev', 'run', 'etc', 'usr/share/mios', 'fixture/bin'):
            (root / directory).mkdir(parents=True, exist_ok=True)
        # Standard fds already refer to the private tmux pty. Leave /dev empty;
        # the TUI uses its tty stdin, with no device-node creation or passthrough.
        copy_executable(miosd, root, '/bin/miosd')
        copy_executable(shutil.which('sh'), root, '/bin/sh')
        shutil.copy2(ROOT / 'usr/share/mios/mios.toml', root / 'usr/share/mios/mios.toml')
        (root / 'etc/passwd').write_text(f'fixture:x:{uid}:{gid}:fixture:/fixture:/bin/sh\n')
        argv = fixture / 'argv'
        fake = root / TERMINAL.lstrip('/')
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text('#!/bin/sh\nprintf "%s " "$@" > /fixture/argv\n')
        user = fixture / 'user.toml'
        paths = dict(vendor='/usr/share/mios/mios.toml', user='/fixture/user.toml',
                     tmux='/fixture', bin='/fixture/bin')
        cmd = ['chroot', f'--userspec={uid}:{gid}', str(root), '/bin/miosd', 'sessions', '--root', '/']
    else:
        fixture = d
        (fixture / 'bin').mkdir()
        argv = fixture / 'argv'
        fake = fixture / 'mios-terminal'
        fake.write_text(f'#!/bin/sh\nprintf "%s " "$@" > {shlex.quote(str(argv))}\n')
        user = fixture / 'user.toml'
        paths = dict(vendor=str(ROOT / 'usr/share/mios/mios.toml'), user=str(user),
                     tmux=str(fixture), bin=str(fixture / 'bin'))
        cmd = ['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--tmpfs', '/run', '--tmpfs', '/tmp',
               '--bind', str(fixture), str(fixture), '--ro-bind', str(fake), TERMINAL,
               '--die-with-parent', miosd, 'sessions', '--root', '/']
    fake.chmod(0o755)
    tail = fixture / 'bin/tailscale'
    tail.write_text('#!/bin/sh\nexit 1\n')
    tail.chmod(0o755)  # No network lookup; selector lists the fixture node only.
    user.write_text(f'[keybindings]\nsocket_name = "mios-m1-{os.getpid()}"\n[terminal]\nsocket_root = "{paths["tmux"]}"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('MIOS_') and k not in ('TMUX', 'TMUX_PANE')}
    env.update(MIOS_VENDOR_TOML=paths['vendor'], MIOS_USER_TOML=paths['user'],
               MIOS_USER_TOML_D=paths['tmux'] + '/none.d', HOME=paths['tmux'],
               TMUX_TMPDIR=str(d), TERM='xterm-256color')
    # The outer server has a host-private socket. Child receives only the inner private paths.
    # Resolve the isolator before replacing PATH with the fixture-only commands.
    cmd[0] = shutil.which(cmd[0])
    cmd = ['env', 'TMUX_TMPDIR=' + paths['tmux'], 'PATH=' + paths['bin'], *cmd]
    sock = f'mios-m1-host-{os.getpid()}-{tag}'
    T = lambda *a: subprocess.run(['tmux', '-L', sock, *a], env=env, capture_output=True, text=True, timeout=10)
    try:
        result = T('new-session', '-d', '-x', '100', '-y', '30', '-s', 'm1', shlex.join(cmd) + '; sleep 30')
        if result.returncode:
            raise RuntimeError(result.stderr)
        shown = wait(lambda: any(h in T('capture-pane', '-p', '-t', 'm1').stdout for h in ('new here', 'n new')), 15)
        check(f'[{tag}] selector rendered in the private pane', shown,
              T('capture-pane', '-p', '-t', 'm1').stdout.strip()[-300:])
        out = ''
        for key in keys:
            T('send-keys', '-t', 'm1', key)
            out = wait(lambda: argv.is_file() and argv.read_text().strip(), 3) or ''
            if out:
                break
        return out
    finally:
        T('kill-server')


def main():
    if not CAND or not os.access(CAND, os.X_OK) or not shutil.which('chroot' if os.geteuid() == 0 else 'bwrap'):
        check('candidate MIOSD and isolated launcher available', False, CAND)
        return 1
    with tempfile.TemporaryDirectory(prefix='mios-m1-') as tmp:
        os.chmod(tmp, 0o700)
        got = selector(CAND, tmp, 'cand-tab', ['Tab'])
        check('candidate: Tab (controller West) opens a new session (mios-terminal --new)', got == '--new', repr(got))
        got = selector(CAND, tmp, 'cand-btab', ['BTab'])
        check('candidate: BTab opens no session (only Tab and n do)', got == '', repr(got))
        if not OLD:
            print('info: MIOSD_OLD unset; the pre-C-33 negative control is skipped', flush=True)
        elif os.access(OLD, os.X_OK) and Path(OLD).resolve() != Path(CAND).resolve():
            got = selector(OLD, tmp, 'old-tab', ['Tab'])
            check('negative control (pre-C-33 miosd): Tab does nothing', got == '', repr(got))
            got = selector(OLD, tmp, 'old-n', ['n'])
            check('harness control (pre-C-33 miosd): n opens a new session', got == '--new', repr(got))
        else:
            check('negative-control miosd differs from the candidate', False, OLD)
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
