#!/usr/bin/env python3
# AI-hint: Real-binary test of M1 (C-33): in the miosd session selector the controller's West button (Tab, nav grammar) opens a new session like `n`; the TUI runs in a private tmux server with an isolated filesystem and fake mios-terminal and tailscale, so nothing reaches the operator's tmux-os. Negative control (MIOSD_OLD, a pre-C-33 miosd) ignores Tab; `n` proves the harness on it.
# AI-related: src/mios-rs/miosd/src/sessions.rs, usr/share/mios/mios.toml [terminal.sessions], usr/share/doc/mios/keybinds.md, docs/design/mios-global-bindings-final.md
# Env: MIOSD = the candidate miosd (explicit, otherwise built/installed native catalog); MIOSD_OLD = a pre-C-33 miosd for the negative control (optional: the
# deployed miosd already carries C-33 from main, so it is no control any more).
import json, os, re, shlex, shutil, socket, subprocess, sys, tempfile, time
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


def global_menu_fixture(root, fixture, uid, gid, scenario):
    """Native catalogue and owned UNIX compositor fixture; every exec stays in this root."""
    gen = os.environ.get('MIOS_GEN')
    if not gen or not os.access(gen, os.X_OK):
        raise RuntimeError('MIOS_GEN must select the source-current native catalogue producer')
    copy_executable(gen, root, '/fixture/bin/mios-gen')
    apps = fixture / 'apps'
    apps.mkdir()
    for desktop_id, name, metadata in (
        ('org.fixture.Native', 'Catalogue Fixture Native', ''),
        ('org.fixture.Flatpak', 'Catalogue Fixture Flatpak', 'X-Flatpak=org.fixture.Flatpak\n'),
        ('org.fixture.Console', 'Catalogue Fixture Console', 'Terminal=true\n'),
        ('org.fixture.Hidden', 'Catalogue Fixture Hidden', 'Hidden=true\n'),
        ('org.fixture.Private', 'Catalogue Fixture Private', 'NoDisplay=true\n'),
        ('org.fixture.Missing', 'Catalogue Fixture Missing', 'TryExec=/fixture/absent\n'),
    ):
        (apps / (desktop_id + '.desktop')).write_text(
            f'[Desktop Entry]\nType=Application\nName={name}\nExec=/fixture/bin/fixture-app %U\n{metadata}')
    if scenario == 'narrow':
        for index in range(40):
            (apps / f'org.fixture.Scroll{index}.desktop').write_text(
                f'[Desktop Entry]\nType=Application\nName=Scrollable fixture {index:02}\nExec=/fixture/bin/fixture-app\n')
    scripts = {
        'getent': f'#!/bin/sh\nprintf "fixture:x:{uid}:{gid}:fixture:/fixture:/bin/sh\\n"\n',
        'fixture-app': '#!/bin/sh\nprintf unsafe > /fixture/desktop-exec-was-run\n',
        'mios-mons': '#!/bin/sh\nexit 0\n',
        'global-launch': '#!/bin/sh\nprintf "%s\\n" "$MIOS_EXECUTION_SESSION" "$@" > /fixture/argv\n',
        'tmux': '#!/bin/sh\nprintf "mios\\t2\\t1\\ntmux-os\\t1\\t0\\n"\n',
        'ssh': '#!/bin/sh\nprintf "remote-work\\t1\\t0\\n"\n',
        'tailscale': '#!/bin/sh\nprintf \'%s\\n\' \'{"Peer":{"live":{"OS":"linux","HostName":"fixture-live","DNSName":"fixture-live.test.","Online":true},"off":{"OS":"linux","HostName":"fixture-offline","DNSName":"fixture-offline.test.","Online":false}}}\'\n',
    }
    receipt = {'execution_target': {'id': 'fixture-windows-session', 'platform': 'windows',
               'session_id': 'MiOS-Xbox fixture', 'backend': 'os-control'}, 'windows': []}
    scripts['session-probe'] = (
        '#!/bin/sh\n[ "$MIOS_EXECUTION_SESSION" = "os_control.nodes.fixture-windows" ] || exit 1\n'
        + ('exit 1\n' if scenario == 'negative' else "printf '%s\\n' '" + json.dumps(receipt) + "'\n"))
    for name, content in scripts.items():
        file = fixture / 'bin' / name
        file.write_text(content)
        file.chmod(0o755)
    child = None
    if scenario != 'headless':
        runtime = root / f'run/user/{uid}'
        hypr = runtime / 'hypr/mios-fixture'
        hypr.mkdir(parents=True)
        for directory in (runtime, runtime / 'hypr', hypr):
            os.chown(directory, uid, gid)
        sock = hypr / '.socket.sock'
        child = os.fork()
        if child == 0:
            try:
                os.chroot(root)
                os.chdir('/')
                os.setgroups([])
                os.setgid(gid)
                os.setuid(uid)
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(f'/run/user/{uid}/hypr/mios-fixture/.socket.sock')
                listener.listen()
                while True:
                    connection, _ = listener.accept()
                    with connection:
                        try:
                            connection.recv(1024)
                            connection.sendall(b'{"tag":"v0.56.2"}')
                        except OSError:
                            pass
            finally:
                os._exit(0)
        if not wait(sock.exists, 3):
            raise RuntimeError('private uid-owned compositor fixture failed to start')
    def close():
        if child is not None:
            try:
                os.kill(child, 15)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)
    config = (
        '[desktop.launch]\napplication_dirs=["/fixture/apps"]\n'
        '[terminal.sessions]\nssh_timeout_s=1\n'
        '[terminal.sessions.menu]\ninventory_program="/fixture/bin/mios-gen"\n'
        'launch_program="/fixture/bin/global-launch"\nprobe_program="/fixture/bin/session-probe"\n'
        '[os_control.nodes.fixture-windows]\nendpoint="http://127.0.0.1:1"\n'
        '[[terminal.tuis]]\nid="fixture-registry-app"\nlabel="MiOS-Mons"\nsection="mios"\n'
        'mode="window"\nvt_ok=true\nargv=["/fixture/bin/mios-mons"]\n')
    return close, config


def selector(miosd, tmp, tag, keys, scenario=None, width=100, height=30):
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
        for directory in ('dev', 'run', 'etc', 'tmp', 'usr/share/mios', 'fixture/bin'):
            (root / directory).mkdir(parents=True, exist_ok=True)
        # Stdio::null and bounded native probes need only an ordinary empty file
        # and a private temporary directory. Neither is a kernel device or host bind.
        (root / 'dev/null').write_bytes(b'')
        (root / 'dev/null').chmod(0o666)
        (root / 'tmp').chmod(0o1777)
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
    cleanup_menu = lambda: None
    menu_config = ""
    if scenario:
        if os.geteuid() != 0:
            raise RuntimeError("private root fixture requires the CI container uid")
        cleanup_menu, menu_config = global_menu_fixture(root, fixture, uid, gid, scenario)
    tail = fixture / 'bin/tailscale'
    if not scenario:
        tail.write_text('#!/bin/sh\nexit 1\n')
    tail.chmod(0o755)  # No network lookup; selector lists the fixture node only.
    user.write_text(f'[keybindings]\nsocket_name = "mios-m1-{os.getpid()}"\n[terminal]\nsocket_root = "{paths["tmux"]}"\n' + menu_config)
    env = {k: v for k, v in os.environ.items() if not k.startswith('MIOS_') and k not in ('TMUX', 'TMUX_PANE')}
    env.update(MIOS_VENDOR_TOML=paths['vendor'], MIOS_USER_TOML=paths['user'],
               MIOS_USER_TOML_D=paths['tmux'] + '/none.d', HOME=paths['tmux'],
               XDG_RUNTIME_DIR=f'/run/user/{uid}', TMUX_TMPDIR=str(d), TERM='xterm-256color')
    # The outer server has a host-private socket. Child receives only the inner private paths.
    # Resolve the isolator before replacing PATH with the fixture-only commands.
    cmd[0] = shutil.which(cmd[0])
    cmd = ['env', '-u', 'TMUX', '-u', 'TMUX_PANE', 'TMUX_TMPDIR=' + paths['tmux'], 'PATH=' + paths['bin'], *cmd]
    sock = f'mios-menu-{os.getpid()}'
    T = lambda *a: subprocess.run(['tmux', '-L', sock, *a], env=env, capture_output=True, text=True, timeout=10)
    try:
        result = T('new-session', '-d', '-x', str(width), '-y', str(height), '-s', 'm1', shlex.join(cmd) + '; sleep 30')
        if result.returncode:
            raise RuntimeError(result.stderr)
        capture = lambda: T('capture-pane', '-p', '-t', 'm1').stdout
        shown = wait(lambda: any(h in capture() for h in ('new here', 'n new')), 15)
        if scenario:
            shown = wait(lambda: ('44 local desktop' if scenario == 'narrow' else '4 local desktop') in capture() and 'Discovering apps' not in capture(), 10)
        check(f'[{tag}] selector rendered in the private pane', shown, capture().strip()[-300:])
        proof = os.environ.get('MIOS_SELECTOR_PROOF_DIR')
        def save(suffix):
            if proof:
                Path(proof).mkdir(parents=True, exist_ok=True)
                (Path(proof) / f'{tag}-{suffix}.txt').write_text(capture())
        save('initial')
        out = ''
        for index, action in enumerate(keys):
            key, expected = action if isinstance(action, tuple) else (action, None)
            save(f'before-{index}')
            T('send-keys', '-t', 'm1', key)
            if expected:
                check(f'[{tag}] {expected} visible', wait(lambda: expected in capture(), 6), capture())
            else:
                out = wait(lambda: argv.is_file() and argv.read_text().strip(), 1) or ''
            save(f'after-{index}')
            if width <= 60 and not out:
                screen = capture()
                check(f'[{tag}] narrow menu keeps root navigation and target visible after {key}',
                      all(label in screen for label in ('Global apps', 'Systems and sessions', 'All apps and sessions', 'Target:', 'Search')))
            if out:
                break
        check(f'[{tag}] desktop inventory never executes desktop Exec', not (fixture / 'desktop-exec-was-run').exists())
        return out
    finally:
        T('kill-server')
        cleanup_menu()


def native_tui_client_cases():
    """Real selector -> shipped terminal wrapper -> native app executor on private tmux servers."""
    import fcntl, pty, termios
    canonical = Path(TERMINAL)
    check('native app fixture uses the exact canonical terminal wrapper',
          canonical.read_bytes() == (ROOT / TERMINAL.lstrip('/')).read_bytes())
    gen = os.environ.get('MIOS_GEN', '')
    check('native app fixture selects an explicit executable native executor',
          bool(gen) and Path(gen).is_file() and os.access(gen, os.X_OK))
    for case, attached, unrelated in (('single', 1, False), ('zero', 0, False),
                                      ('ambiguous', 2, False), ('other-session', 1, True)):
        with tempfile.TemporaryDirectory(prefix='mios-selector-client-') as tmp:
            d = Path(tmp)
            (d / 'bin').mkdir()
            tail = d / 'bin/tailscale'
            tail.write_text('#!/bin/sh\nexit 1\n')
            tail.chmod(0o755)
            game = d / 'mios-fixture-game'
            marker = d / 'launched'
            game.write_text('#!/bin/sh\nprintf "%s\\n" "$$" > ' + shlex.quote(str(marker))
                            + '\nprintf "MIOS_PRIVATE_GAME_RUNNING\\n"\nexec sleep 120\n')
            game.chmod(0o755)
            user = d / 'profile.toml'
            # Discovery deliberately names another socket. Execution must use the invoking TMUX witness.
            user.write_text('[keybindings]\nsocket_name="mios-fixture-unrelated-discovery"\n'
                            '[terminal]\nsocket_root=' + json.dumps(tmp) + '\n'
                            '[[terminal.tuis]]\nid="mios-fixture-game"\nlabel="Private game fixture"\n'
                            'section="mios"\nmode="window"\nvt_ok=true\nargv=['
                            + json.dumps(str(game)) + ']\n')
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith('MIOS_') and k not in ('TMUX', 'TMUX_PANE')}
            env.update(MIOS_VENDOR_TOML=str(ROOT / 'usr/share/mios/mios.toml'),
                       MIOS_USER_TOML=str(user), MIOS_USER_TOML_D=str(d / 'none.d'),
                       PATH=str(d / 'bin') + ':' + os.environ['PATH'],
                       TERM='xterm-256color', HOME=tmp, XDG_CONFIG_HOME=str(d / 'config'),
                       MIOS_TERMINAL_TEST_SOCKET_DIR=tmp,
                       MIOS_TERMINAL_TEST_SOCKET='mios-tmuxos-test-selector-client',
                       MIOS_TERMINAL_TEST_GEN=gen)
            sock = str(d / 'native.sock')
            base = ['tmux', '-S', sock, '-f', '/dev/null']
            def run(*args):
                return subprocess.run([*base, *args], env=env, text=True,
                                      capture_output=True, timeout=10)
            clients = []
            fds = []
            def attach(session):
                master, slave = pty.openpty()
                def tty():
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                proc = subprocess.Popen([*base, 'attach-session', '-t', '=' + session],
                                        env=env, stdin=slave, stdout=slave, stderr=slave,
                                        preexec_fn=tty)
                os.close(slave)
                os.set_blocking(master, False)
                fds.append(master)
                clients.append(proc)
                return proc
            def capture():
                for master in fds:
                    try:
                        while os.read(master, 65536):
                            pass
                    except (BlockingIOError, OSError):
                        pass
                return run('capture-pane', '-p', '-t', '=owned:').stdout
            try:
                started = run('new-session', '-d', '-x', '110', '-y', '32', '-s', 'owned',
                              shlex.join([CAND, 'sessions', '--root', '/']) + '; sleep 120')
                check(f'[{case}] private native tmux server starts', started.returncode == 0, started.stderr)
                check(f'[{case}] actual selector menu rendered',
                      wait(lambda: 'All apps and sessions' in capture(), 15), capture()[-250:])
                for _ in range(attached):
                    attach('owned')
                if unrelated:
                    run('new-session', '-d', '-s', 'unrelated', 'sleep 120')
                    attach('unrelated')
                check(f'[{case}] requested private clients actually attach',
                      wait(lambda: len(run('list-clients', '-F', '#{client_name}').stdout.splitlines())
                           == attached + int(unrelated), 5))
                old_foreign = run('list-clients', '-F', '#{session_name}|#{window_id}').stdout
                run('send-keys', '-t', '=owned:', '-l', '/Private game fixture')
                check(f'[{case}] real TUI registry row selected',
                      wait(lambda: 'Private game fixture' in capture() and
                           'Search editing: Private game fixture' in capture(), 5), capture()[-250:])
                run('send-keys', '-t', '=owned:', 'Enter')
                if attached == 1:
                    check(f'[{case}] native wrapper and executor launch the real fixture process',
                          wait(lambda: marker.is_file(), 6), capture()[-250:])
                    pane = run('list-panes', '-s', '-t', '=owned:', '-F',
                               '#{pane_pid}|#{pane_current_command}|#{window_name}').stdout
                    check(f'[{case}] launched process is alive in its canonical game window',
                          marker.is_file() and Path('/proc', marker.read_text().strip()).exists()
                          and '|sleep|Private game fixture' in pane, pane)
                    if unrelated:
                        new_foreign = run('list-clients', '-F', '#{session_name}|#{window_id}').stdout
                        check('other attached session keeps its existing window',
                              [x for x in old_foreign.splitlines() if x.startswith('unrelated|')]
                              == [x for x in new_foreign.splitlines() if x.startswith('unrelated|')])
                else:
                    error = 'No attached tmux client' if attached == 0 else 'Multiple attached tmux clients'
                    check(f'[{case}] selector rejects missing or ambiguous client before execution',
                          wait(lambda: error in capture(), 5), capture()[-300:])
                    check(f'[{case}] no game executed and no additional window created',
                          not marker.exists() and run('list-windows', '-t', '=owned:',
                                                       '-F', '#{window_id}').stdout.count('\n') == 1)
                proof = os.environ.get('MIOS_SELECTOR_PROOF_DIR')
                if proof:
                    Path(proof).mkdir(parents=True, exist_ok=True)
                    (Path(proof) / f'client-{case}.txt').write_text(capture())
            finally:
                run('kill-server')
                for client in clients:
                    try:
                        client.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        client.terminate()
                        client.wait(timeout=3)
                for master in fds:
                    os.close(master)


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
    with tempfile.TemporaryDirectory(prefix='mios-global-menu-') as tmp:
        got = selector(CAND, tmp, 'global-root-apps', [('Enter', 'Catalogue Fixture Console'), 'Enter'], 'local')
        check('visible Global apps root entry opens the full catalogue and a native terminal app',
              got.endswith('\norg.fixture.Console'), repr(got))
        got = selector(CAND, tmp, 'global-narrow',
              [('Enter', 'Catalogue Fixture Console'), ('End', 'MiOS-Mons'), ('Home', 'Global apps'),
               ('/Catalogue Fixture Native', 'Catalogue Fixture Native'), 'Enter'], 'narrow', width=50, height=20)
        check('narrow menu searches and dispatches through the same target-bound generic launcher',
              got.endswith('\norg.fixture.Native'), repr(got))
        got = selector(CAND, tmp, 'global-local', [('/Catalogue Fixture Native', 'Catalogue Fixture Native'), 'Enter'], 'local')
        check('global app dispatch preserves exact desktop ID and selected native session',
              got == '/run/user/1000/hypr/mios-fixture/.socket.sock\norg.fixture.Native', repr(got))
        got = selector(CAND, tmp, 'global-flatpak', [('/Catalogue Fixture Flatpak', 'Catalogue Fixture Flatpak'), 'Enter'], 'local')
        check('Flatpak export is available through the same generic dispatcher',
              got.endswith('\norg.fixture.Flatpak'), repr(got))
        got = selector(CAND, tmp, 'global-mons', [('/MiOS-Mons', 'MiOS-Mons'), 'Enter'], 'local')
        check('ordinary registered MiOS-Mons entry opens through the existing TUI launcher',
              got == '--app fixture-registry-app --console 0', repr(got))
        got = selector(CAND, tmp, 'global-remote',
              [('/fixture-windows', 'probe required'), ('Enter', 'windows / MiOS-Xbox fixture'),
               ('/Remote App Fixture', 'Launch by name: Remote App Fixture'), 'Enter'], 'remote')
        check('registered Windows system uses the witnessed execution target and requested application name',
              got == 'fixture-windows-session\nRemote App Fixture', repr(got))
        got = selector(CAND, tmp, 'global-remote-local-missing',
              [('/fixture-windows', 'probe required'), ('Enter', 'windows / MiOS-Xbox fixture'),
               ('/Catalogue Fixture Missing', 'resolve remotely'), 'Enter'], 'remote')
        check('local TryExec absence does not block resolving an app on the selected remote system',
              got == 'fixture-windows-session\nCatalogue Fixture Missing', repr(got))
        got = selector(CAND, tmp, 'global-unavailable',
              [('/fixture-windows', 'probe required'), ('Enter', 'Session unavailable'),
               'Escape', ('/Catalogue Fixture Native', 'Catalogue Fixture Native'), 'Enter'], 'negative')
        check('failed explicit remote target never falls back to the local desktop', got == '', repr(got))
        got = selector(CAND, tmp, 'global-headless',
              [('/Catalogue Fixture Native', 'Catalogue Fixture Native'), 'Enter'], 'headless')
        check('headless app row cannot invent a graphical launch target', got == '', repr(got))
    native_tui_client_cases()
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
