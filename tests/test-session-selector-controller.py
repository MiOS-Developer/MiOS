#!/usr/bin/env python3
# AI-hint: Real-binary test of M1 (C-33): in the miosd session selector the controller's West button (Tab, nav grammar) opens a new session like `n`; the TUI runs in a private tmux server under bwrap with a fake mios-terminal and tailscale, so nothing reaches the operator's tmux-os. Negative control (MIOSD_OLD, a pre-C-33 miosd) ignores Tab; `n` proves the harness on it.
# AI-related: src/mios-rs/miosd/src/sessions.rs, usr/share/mios/mios.toml [terminal.sessions] [keybindings], usr/share/doc/mios/keybinds.md, docs/design/mios-global-bindings-final.md
# Env: MIOSD = the candidate miosd (required); MIOSD_OLD = a pre-C-33 miosd for the negative control (optional: the
# deployed miosd already carries C-33 from main, so it is no control any more).
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAND = os.environ.get('MIOSD', '')
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


def selector(miosd, tmp, tag, keys):
    """Open the selector of `miosd` in a private tmux pane; press `keys` one by one. Returns the argv the selector
    exec'd mios-terminal with ('' when it did not)."""
    d = Path(tmp) / tag
    (d / 'bin').mkdir(parents=True)
    argv = d / 'argv'
    fake = d / 'mios-terminal'
    fake.write_text(f'#!/bin/sh\nprintf "%s " "$@" > {argv}\nexec sleep 30\n')
    tail = d / 'bin/tailscale'
    tail.write_text('#!/bin/sh\nexit 1\n')  # no tailnet: the selector lists this node only
    for f in (fake, tail):
        f.chmod(0o700)
    user = d / 'user.toml'
    # A private tmux namespace for the selector's own discovery (never the operator's tmux-os server).
    user.write_text(f'[keybindings]\nsocket_name = "mios-m1-{os.getpid()}"\n[terminal]\nsocket_root = "{d}"\n')
    env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'TMUX', 'TMUX_PANE')}
    env.update(MIOS_VENDOR_TOML=str(ROOT / 'usr/share/mios/mios.toml'), MIOS_USER_TOML=str(user),
               MIOS_USER_TOML_D=str(d / 'none.d'), TMUX_TMPDIR=str(d), PATH=f"{d / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}")
    cmd = ['bwrap', '--dev-bind', '/', '/', '--ro-bind', str(fake), TERMINAL, '--die-with-parent', miosd, 'sessions', '--root', '/']
    sock = f'mios-m1-host-{os.getpid()}-{tag}'
    T = lambda *a: subprocess.run(['tmux', '-L', sock, *a], env=env, capture_output=True, text=True, timeout=10)
    try:
        r = T('new-session', '-d', '-x', '100', '-y', '30', '-s', 'm1', subprocess.list2cmdline(cmd) + '; sleep 30')
        if r.returncode:
            raise RuntimeError(r.stderr)
        shown = wait(lambda: any(h in T('capture-pane', '-p', '-t', 'm1').stdout for h in ('new here', 'n new')), 15)
        check(f'[{tag}] selector rendered in the private pane', shown, T('capture-pane', '-p', '-t', 'm1').stdout.strip()[-200:])
        out = ''
        for k in keys:
            T('send-keys', '-t', 'm1', k)
            out = wait(lambda: argv.is_file() and argv.read_text().strip(), 3) or ''
            if out:
                break
        return out
    finally:
        T('kill-server')


def main():
    if not CAND or not os.access(CAND, os.X_OK) or not shutil.which('bwrap'):
        check('candidate MIOSD and bwrap available', False, CAND)
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
