#!/usr/bin/env python3
# AI-hint: Offline safety contract of src/quickshell/install-candidate.sh in private scratch roots (never the live
# root): existing directory modes and owners are never changed (the pre-fix `tar | tar -C /` pipeline reset /usr,
# /usr/bin and /usr/lib64 to 0700 -- reproduced here as the negative control), the binary is stripped, every replaced
# file is backed up and verified, a failed or interrupted install is undone, rollback restores the tree byte for byte
# and removes the files only the candidate shipped (the pre-fix rollback left them -- negative control), unsafe stages
# are refused, and the script starts, stops or signals nothing. The live-root code path (--root /, so the internal
# prefix R is empty) runs in a user-namespace sandbox whose / is a tmpfs: the host /usr is bound read-only and only
# the four destination directories are scratch copies, so nothing on the host is writable. Negative controls run the
# pre-fix script (git show $MIOS_QS_INSTALL_PREFIX_REF, default a89bdff32): its dir_snapshot never ends for R="",
# it exits 1 without an ERROR line when the last ancestor is missing, its status reports "no active install" when the
# state is unreadable, its rollback overwrites files changed since the install, and its copy follows a stage symlink.
# AI-related: src/quickshell/install-candidate.sh, usr/share/doc/mios/quickshell-candidate.md,
# tests/test-quickshell-candidate-live.py
# AI-functions: TestCandidateInstall, TestEmptyPrefixRoot, TestPrefixDefects, make_root, make_stage, snapshot, run,
# prefix_script, Sandbox
"""Usage: python3 tests/test-quickshell-candidate-install.py
The empty-prefix (--root /) cases need bwrap with unprivileged user namespaces; the negative controls need git.
Optional: MIOS_QS_STAGE=<the real DESTDIR stage> also installs and rolls back the real candidate in a scratch root
seeded from the packaged files under /usr (read only)."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parents[1]
SCRIPT = TREE / 'src/quickshell/install-candidate.sh'
DOC = TREE / 'usr/share/doc/mios/quickshell-candidate.md'
QML = 'usr/lib64/qt6/qml/Quickshell'
FAKE_C = r'''
#include <stdio.h>
#include <string.h>
int main(int c, char **v) { if (c > 1 && !strcmp(v[1], "--version")) { puts("%s"); return 0; } return 0; }
'''


PREFIX_REF = os.environ.get('MIOS_QS_INSTALL_PREFIX_REF', 'a89bdff32')


def run(*args, env_extra=None, script=SCRIPT, timeout=300):
    # a minimal environment: the script must not depend on the caller's
    env = {'PATH': '/usr/bin:/bin', 'HOME': os.environ.get('HOME', '/tmp'), 'LC_ALL': 'C'}
    env.update(env_extra or {})
    return subprocess.run(['/bin/bash', str(script), *map(str, args)], capture_output=True, text=True, env=env,
                          timeout=timeout)


def prefix_script(dest):
    """The pre-fix install-candidate.sh (the rejected a89bdff32), for the negative controls; None without git."""
    if not shutil.which('git'):
        return None
    r = subprocess.run(['git', '-C', str(TREE), 'show', f'{PREFIX_REF}:src/quickshell/install-candidate.sh'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    dest.write_text(r.stdout)
    os.chmod(dest, 0o700)
    return dest


def can_sandbox():
    if not shutil.which('bwrap'):
        return False
    r = subprocess.run(['bwrap', '--unshare-all', '--uid', '0', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib64',
                        '/lib64', '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '/usr/bin/true'],
                       capture_output=True, timeout=30)
    return r.returncode == 0


def compile_fake(out, version, debug):
    src = out.with_suffix('.c')
    src.write_text(FAKE_C % version)
    subprocess.run(['gcc', '-O1', *(['-g3'] if debug else ['-s']), '-o', str(out), str(src)], check=True)
    src.unlink()


def make_root(base):
    """A scratch root shaped like the live one: 0755 directories, 0555 usr/lib64, the packaged 0.2.1 files."""
    r = base / 'root'
    for d in ('usr/bin', f'{QML}/Wayland', f'{QML}/Io', 'usr/share/applications', 'usr/share/icons/hicolor/scalable/apps'):
        (r / d).mkdir(parents=True, exist_ok=True)
    compile_fake(r / 'usr/bin/quickshell', 'Quickshell 0.2.1 (packaged)', False)
    (r / 'usr/bin/qs').symlink_to('/usr/bin/quickshell')
    for rel, text in ((f'{QML}/qmldir', 'module Quickshell 021\n'), (f'{QML}/Wayland/qmldir', 'module Quickshell.Wayland 021\n'),
                      (f'{QML}/Io/qmldir', 'module Quickshell.Io 021\n'), (f'{QML}/Io/FileView.qml', '// 0.2.1\n'),
                      ('usr/share/applications/org.quickshell.desktop', '[Desktop Entry]\nName=qs 0.2.1\n'),
                      ('usr/share/icons/hicolor/scalable/apps/org.quickshell.svg', '<svg/>\n')):
        (r / rel).write_text(text)
        os.chmod(r / rel, 0o644)
    (r / 'usr/share/unrelated.txt').write_text('not quickshell\n')
    for d in sorted({p for p in r.rglob('*') if p.is_dir() and not p.is_symlink()} | {r}):
        os.chmod(d, 0o755)
    os.chmod(r / 'usr/lib64', 0o555)
    return r


def make_stage(base, version='Quickshell 0.3.2 (revision test, distributed by MiOS staged candidate)', extra=None):
    """A DESTDIR stage like the real one: every directory 0700, an unstripped binary, an absolute qs symlink,
    and two QML modules the packaged 0.2.1 does not ship."""
    s = base / 'stage'
    for d in ('usr/bin', f'{QML}/Wayland/_BackgroundEffect', f'{QML}/WindowManager', f'{QML}/Io',
              'usr/share/applications', 'usr/share/icons/hicolor/scalable/apps'):
        (s / d).mkdir(parents=True, exist_ok=True)
    compile_fake(s / 'usr/bin/quickshell', version, True)
    (s / 'usr/bin/qs').symlink_to('/usr/bin/quickshell')
    for rel in (f'{QML}/qmldir', f'{QML}/Wayland/qmldir', f'{QML}/Wayland/_BackgroundEffect/qmldir',
                f'{QML}/WindowManager/qmldir', f'{QML}/WindowManager/quickshell-windowmanager.qmltypes',
                f'{QML}/Io/qmldir', f'{QML}/Io/FileView.qml', 'usr/share/applications/org.quickshell.desktop',
                'usr/share/icons/hicolor/scalable/apps/org.quickshell.svg'):
        (s / rel).write_text(f'0.3.2 {rel}\n')
        os.chmod(s / rel, 0o600)
    for rel, kind, payload in (extra or []):
        p = s / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'link':
            p.symlink_to(payload)
        else:
            p.write_text(payload)
    for d in [p for p in s.rglob('*') if p.is_dir() and not p.is_symlink()] + [s]:
        os.chmod(d, 0o700)
    return s


def snapshot(root):
    """type, mode, owner, link target and content hash of every path under root except the transaction state."""
    out = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root).as_posix()
        if rel == 'var' or rel.startswith('var/'):
            continue
        st = p.lstat()
        if stat.S_ISLNK(st.st_mode):
            out[rel] = ('l', os.readlink(p))
        elif stat.S_ISDIR(st.st_mode):
            out[rel] = ('d', oct(st.st_mode & 0o7777), st.st_uid, st.st_gid)
        else:
            out[rel] = ('f', oct(st.st_mode & 0o7777), st.st_uid, st.st_gid, p.read_bytes())
    return out


def has_debug(path):
    return '.debug_' in subprocess.run(['readelf', '-S', '-W', str(path)], capture_output=True, text=True).stdout


@unittest.skipUnless(shutil.which('gcc') and shutil.which('readelf') and shutil.which('strip'), 'needs gcc, strip, readelf')
class TestCandidateInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-qs-install-'))
        self.root = make_root(self.tmp)
        self.before = snapshot(self.root)

    def tearDown(self):
        for p in [self.tmp, *self.tmp.rglob('*')]:
            if p.is_dir() and not p.is_symlink():
                os.chmod(p, 0o700)
        shutil.rmtree(self.tmp)

    def state(self):
        return self.root / 'var/lib/mios-build/rollback/quickshell-candidate'

    def assert_untouched(self):
        self.assertEqual(snapshot(self.root), self.before)
        self.assertEqual([p for p in self.root.rglob('*.mios-new.*')], [])
        self.assertFalse((self.state() / 'current').exists())

    # ---- negative controls: the pre-fix install and rollback commands ------------------------------------------------
    def test_negative_control_prefix_tar_pipeline_resets_directory_modes(self):
        stage = make_stage(self.tmp)
        # install-quickshell-candidate.sh (pre-fix): tar -C "$STAGE" -cpf - usr | tar -C / --no-same-owner -xpf -
        subprocess.run(f'tar -C "{stage}" -cpf - usr | tar -C "{self.root}" --no-same-owner -xpf -', shell=True, check=True)
        modes = {d: oct(os.stat(self.root / d).st_mode & 0o777) for d in ('usr', 'usr/bin', 'usr/lib64')}
        self.assertEqual(modes, {'usr': '0o700', 'usr/bin': '0o700', 'usr/lib64': '0o700'},
                         'the pre-fix pipeline is expected to reset existing directory modes (the defect)')
        self.assertTrue(has_debug(self.root / 'usr/bin/quickshell'), 'the pre-fix install shipped the unstripped binary')

    def test_negative_control_prefix_rollback_leaves_candidate_only_files(self):
        stage = make_stage(self.tmp)
        files = [p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if not p.is_dir() or p.is_symlink()]
        backup = self.tmp / 'files.tar'
        subprocess.run(['tar', '-C', str(self.root), '--no-recursion', '-cpf', str(backup), *files], check=True)
        subprocess.run(f'tar -C "{stage}" -cpf - usr | tar -C "{self.root}" --no-same-owner -xpf -', shell=True, check=True)
        subprocess.run(['tar', '-C', str(self.root), '-xpf', str(backup)], check=True)   # the pre-fix rollback
        self.assertTrue((self.root / f'{QML}/WindowManager/qmldir').exists(),
                        'the pre-fix rollback is expected to leave the candidate-only files behind (the defect)')
        self.assertNotEqual(snapshot(self.root), self.before)

    # ---- the fixed script ------------------------------------------------------------------------------------------
    def test_install_keeps_directories_strips_and_rolls_back_exactly(self):
        stage = make_stage(self.tmp)
        plan = run('plan', '--root', self.root, '--stage', stage)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn(f'create  {self.root}/{QML}/WindowManager/qmldir', plan.stdout)
        self.assert_untouched()   # plan changes nothing

        r = run('install', '--root', self.root, '--stage', stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        after = snapshot(self.root)
        for rel, meta in self.before.items():
            if meta[0] == 'd':
                self.assertEqual(after[rel], meta, f'directory {rel} changed mode or owner')
        self.assertEqual(after['usr/lib64'][1], '0o555')
        q = self.root / 'usr/bin/quickshell'
        self.assertFalse(has_debug(q), 'installed binary still carries debug sections')
        self.assertLess(q.stat().st_size, (stage / 'usr/bin/quickshell').stat().st_size)
        self.assertEqual(subprocess.run([str(q), '--version'], capture_output=True, text=True).stdout.split()[:2],
                         ['Quickshell', '0.3.2'])
        self.assertEqual(oct(q.stat().st_mode & 0o7777), '0o755')
        self.assertEqual(os.readlink(self.root / 'usr/bin/qs'), 'quickshell')
        self.assertEqual((self.root / 'usr/bin/qs').resolve(), q.resolve())
        for rel in (f'{QML}/qmldir', f'{QML}/WindowManager/qmldir', 'usr/share/applications/org.quickshell.desktop'):
            self.assertEqual(oct((self.root / rel).stat().st_mode & 0o7777), '0o644', rel)
            self.assertEqual((self.root / rel).read_text(), f'0.3.2 {rel}\n')
        for d in (f'{QML}/WindowManager', f'{QML}/Wayland/_BackgroundEffect'):
            self.assertEqual(oct((self.root / d).stat().st_mode & 0o7777), '0o755', d)
        self.assertEqual((self.root / 'usr/share/unrelated.txt').read_text(), 'not quickshell\n')
        self.assertEqual(list(self.root.rglob('*.mios-new.*')), [])
        self.assertIn('active:', run('status', '--root', self.root).stdout)
        txn = self.state() / (self.state() / 'current').read_text().strip()
        self.assertEqual(oct(txn.stat().st_mode & 0o777), '0o700')
        self.assertEqual((txn / 'backup/usr/bin/quickshell').read_bytes(), self.before['usr/bin/quickshell'][4])

        again = run('install', '--root', self.root, '--stage', stage)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('already active', again.stderr)

        rb = run('rollback', '--root', self.root)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assert_untouched()   # byte for byte, modes, owners, links; candidate-only files and dirs gone
        self.assertEqual(os.readlink(self.root / 'usr/bin/qs'), '/usr/bin/quickshell')
        self.assertIn('no active', run('status', '--root', self.root).stdout)
        self.assertNotEqual(run('rollback', '--root', self.root).returncode, 0, 'a second rollback has nothing to do')

    def test_failure_after_partial_commit_is_undone(self):
        stage = make_stage(self.tmp)
        for n in ('1', '6'):
            r = run('install', '--root', self.root, '--stage', stage, env_extra={'MIOS_QS_INSTALL_FAIL_AFTER': n})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('partial install undone', r.stderr)
            self.assert_untouched()
            self.assertTrue(list(self.state().glob('txn-*.failed')))

    def test_failed_backup_aborts_before_anything_is_written(self):
        stage = make_stage(self.tmp)
        if os.geteuid() == 0:
            self.skipTest('root reads mode-000 files')
        locked = self.root / f'{QML}/Io/FileView.qml'
        os.chmod(locked, 0)        # cp cannot back it up: the install must stop before writing anything
        r = run('install', '--root', self.root, '--stage', stage)
        os.chmod(locked, 0o644)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('partial install undone', r.stderr)
        self.assert_untouched()

    def test_unsafe_stages_are_refused_without_writing(self):
        cases = {
            'outside the allowlist': [('usr/bin/evil', 'file', 'x')],
            'sibling of an allowed file': [('usr/share/applications/org.quickshell.desktop.bak', 'file', 'x')],
            'tab in a name': [(f'{QML}/Io/a\tb', 'file', 'x')],
        }
        for label, extra in cases.items():
            with self.subTest(label):
                stage = make_stage(self.tmp / label.replace(' ', '-'), extra=extra)
                r = run('install', '--root', self.root, '--stage', stage)
                self.assertNotEqual(r.returncode, 0, label)
                self.assert_untouched()
        stage = self.tmp / 'link-escape'
        make_stage(stage)
        (stage / 'stage/usr/bin/qs').unlink()
        (stage / 'stage/usr/bin/qs').symlink_to('/etc/shadow')
        r = run('install', '--root', self.root, '--stage', stage / 'stage')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('points outside the candidate', r.stderr)
        wrong = make_stage(self.tmp / 'wrong-version', version='Quickshell 0.2.9 (other)')
        r = run('install', '--root', self.root, '--stage', wrong)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('expected', r.stderr)
        self.assert_untouched()

    def test_symlinked_ancestor_is_refused(self):
        stage = make_stage(self.tmp)
        elsewhere = self.tmp / 'elsewhere'
        elsewhere.mkdir()
        os.chmod(self.root / 'usr/share', 0o755)
        shutil.rmtree(self.root / 'usr/share/icons')
        (self.root / 'usr/share/icons').symlink_to(elsewhere)
        self.before = snapshot(self.root)
        r = run('install', '--root', self.root, '--stage', stage)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('is a symlink', r.stderr)
        self.assert_untouched()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_root_is_explicit_and_live_root_needs_privilege(self):
        stage = make_stage(self.tmp)
        r = run('install', '--stage', stage)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--root is required', r.stderr)
        if os.geteuid() != 0:
            r = run('install', '--root', '/', '--stage', stage)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('needs root', r.stderr)
            r = run('rollback', '--root', '/')
            self.assertNotEqual(r.returncode, 0)

    def test_script_never_extracts_archives_or_touches_processes(self):
        code = [l.split('#', 1)[0] for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith('#')]
        body = '\n'.join(code)
        for word in ('tar', 'systemctl', 'pkill', 'kill', 'killall', 'hyprctl', 'loginctl', 'chmod -R', 'chown -R'):
            self.assertIsNone(re.search(rf'(^|[\s;|&(]){re.escape(word)}(\s|$)', body), f'script runs {word!r}')
        self.assertNotRegex(body, r'chmod\s+[0-7]+\s+--\s+"\$R/usr', 'script must not chmod existing directories')

    def test_doc_points_at_the_safe_script(self):
        doc = DOC.read_text()
        self.assertIn('src/quickshell/install-candidate.sh', doc)
        self.assertIn('--root /', doc)
        self.assertNotIn('install-quickshell-candidate.sh', doc)
        # the old tar pipeline may only appear as the described defect, never as an instruction
        for line in doc.splitlines():
            if re.search(r'tar\s+-C\s+/', line):
                self.assertIn('would have reset', line)


DEST_DIRS = {   # sandbox scratch copy -> the live destination directory it is bound over
    'bin': 'usr/bin', 'qml': QML, 'apps': 'usr/share/applications', 'icons': 'usr/share/icons/hicolor/scalable/apps'}


class Sandbox:
    """--root / for real, without touching the host: a user namespace (uid 0 mapped to the caller) whose / is a tmpfs,
    with the host /usr read-only (its tools, libraries and ancestor directories) and only the candidate's four
    destination directories bound from scratch copies seeded like the packaged 0.2.1. The state (/var) is scratch.
    bwrap's namespace forbids setgroups, so a shim stands in for setpriv (the staged binary still runs; only the
    privilege drop is skipped). Killing bwrap on timeout kills the whole PID namespace."""

    def __init__(self, base, stage, seed=None):
        self.base, self.stage = base, stage
        self.dirs = {}
        seed = seed or make_root(base / 'seed')   # or Path('/') to seed from the packaged host files (read only)
        for name, rel in DEST_DIRS.items():
            self.dirs[name] = base / 'sb' / name
            if name == 'bin':   # only the candidate's two names, never a copy of a whole bin directory
                self.dirs[name].mkdir(parents=True)
                for f in ('quickshell', 'qs'):
                    subprocess.run(['cp', '-a', '--', str(seed / rel / f), str(self.dirs[name])], check=True)
            else:
                shutil.copytree(seed / rel, self.dirs[name], symlinks=True)
            os.chmod(self.dirs[name], 0o755)
        self.var = base / 'sb/var'
        self.var.mkdir()
        shim = base / 'shim'
        shim.mkdir()
        (shim / 'setpriv').write_text('#!/hostbin/bash\nwhile [[ $# -gt 0 && $1 != -- ]]; do shift; done; shift; exec "$@"\n')
        os.chmod(shim / 'setpriv', 0o755)
        self.shim = shim

    def argv(self, script, inner, extra=()):
        a = ['bwrap', '--unshare-all', '--uid', '0', '--gid', '0', '--die-with-parent', '--new-session',
             '--tmpfs', '/', '--ro-bind', '/usr', '/usr', '--ro-bind', '/usr/bin', '/hostbin', '--ro-bind', '/etc', '/etc',
             '--symlink', 'usr/lib64', '/lib64', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/bin', '/bin',
             '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
        for name, rel in DEST_DIRS.items():
            a += ['--bind', str(self.dirs[name]), '/' + rel]
        a += ['--bind', str(self.var), '/var', '--ro-bind', str(self.stage), str(self.stage),
              '--ro-bind', str(script), '/scripts/install-candidate.sh', '--ro-bind', str(self.shim), '/shim', *extra,
              '--clearenv', '--setenv', 'PATH', '/shim:/hostbin', '--setenv', 'LC_ALL', 'C', '--setenv', 'HOME', '/tmp',
              '--setenv', 'SUDO_UID', '0', '--setenv', 'SUDO_GID', '0', '--setenv', 'TMPDIR', '/tmp',
              '/hostbin/bash', '-c', 'ulimit -v 2000000; ' + inner]
        return a

    def run(self, script, *args, timeout=120, extra=()):
        inner = 'exec /hostbin/bash /scripts/install-candidate.sh "$@"'
        return subprocess.run(self.argv(script, inner, extra) + ['_', *map(str, args)], capture_output=True, text=True,
                              timeout=timeout)

    def snapshot_unit(self, script, rels, timeout):
        """Source the script and call dir_snapshot with R="" (the --root / prefix) for these entries."""
        entries = ' '.join(f"$'f\\t{r}'" for r in rels)
        inner = f'source /scripts/install-candidate.sh; R=""; ENTRIES=({entries}); dir_snapshot'
        return subprocess.run(self.argv(script, inner), capture_output=True, text=True, timeout=timeout)

    def snapshot(self):
        return {n: snapshot(d) | {'.': ('d', oct(d.stat().st_mode & 0o7777))} for n, d in self.dirs.items()}


@unittest.skipUnless(shutil.which('gcc') and shutil.which('readelf') and shutil.which('strip'), 'needs gcc, strip, readelf')
@unittest.skipUnless(can_sandbox(), 'needs bwrap and unprivileged user namespaces')
class TestEmptyPrefixRoot(unittest.TestCase):
    """--root / (R="") inside the sandbox: the code path the scratch-root tests never ran."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-qs-sbx-'))
        self.stage = make_stage(self.tmp)
        self.sb = Sandbox(self.tmp, self.stage)
        self.before = self.sb.snapshot()

    def tearDown(self):
        for p in [self.tmp, *self.tmp.rglob('*')]:
            if p.is_dir() and not p.is_symlink():
                os.chmod(p, 0o700)
        shutil.rmtree(self.tmp)

    def test_negative_control_prefix_dir_snapshot_never_ends_for_the_live_root(self):
        old = prefix_script(self.tmp / 'prefix.sh')
        if old is None:
            self.skipTest(f'git show {PREFIX_REF} unavailable')
        with self.assertRaises(subprocess.TimeoutExpired, msg='pre-fix dir_snapshot is expected to loop forever on R=""'):
            self.sb.snapshot_unit(old, [f'{QML}/qmldir'], timeout=8)
        with self.assertRaises(subprocess.TimeoutExpired, msg='pre-fix install --root / is expected to hang'):
            self.sb.run(old, 'install', '--root', '/', '--stage', self.stage, timeout=20)
        self.assertEqual(self.sb.snapshot(), self.before, 'the hung pre-fix install wrote nothing')

    def test_dir_snapshot_ends_for_the_live_root(self):
        r = self.sb.snapshot_unit(SCRIPT, [f'{QML}/Wayland/_BackgroundEffect/qmldir', 'usr/bin/qs'], timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        paths = [l.split(' ', 3)[3] for l in r.stdout.splitlines()]
        self.assertEqual(paths, ['/usr', '/usr/bin', '/usr/lib64', '/usr/lib64/qt6', '/usr/lib64/qt6/qml',
                                 f'/{QML}', f'/{QML}/Wayland', '/usr/share'])   # _BackgroundEffect does not exist yet
        self.assertNotIn('/', paths)

    def test_install_status_rollback_with_root_slash(self):
        plan = self.sb.run(SCRIPT, 'plan', '--root', '/', '--stage', self.stage)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn(f'create  /{QML}/WindowManager/qmldir', plan.stdout)
        self.assertIn('replace /usr/bin/quickshell', plan.stdout)
        r = self.sb.run(SCRIPT, 'install', '--root', '/', '--stage', self.stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stderr, r'\n755 \S+ /usr\n755 \S+ /usr/bin\n555 \S+ /usr/lib64\n')
        after = self.sb.snapshot()
        for name in DEST_DIRS:
            self.assertEqual(after[name]['.'], self.before[name]['.'], f'{name} directory mode changed')
        q = self.sb.dirs['bin'] / 'quickshell'
        self.assertFalse(has_debug(q))
        self.assertEqual(oct(q.stat().st_mode & 0o7777), '0o755')
        self.assertEqual(os.readlink(self.sb.dirs['bin'] / 'qs'), 'quickshell')
        self.assertEqual((self.sb.dirs['qml'] / 'WindowManager/qmldir').read_text(), f'0.3.2 {QML}/WindowManager/qmldir\n')
        st = self.sb.run(SCRIPT, 'status', '--root', '/')
        self.assertEqual(st.returncode, 0, st.stderr)
        self.assertIn('active: /var/lib/mios-build/rollback/quickshell-candidate/txn-', st.stdout)
        self.assertRegex(st.stdout, r'\b2 dir\b')
        again = self.sb.run(SCRIPT, 'install', '--root', '/', '--stage', self.stage)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('already active', again.stderr)
        rb = self.sb.run(SCRIPT, 'rollback', '--root', '/')
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(self.sb.snapshot(), self.before, 'rollback --root / must restore the tree exactly')
        self.assertIn('no active', self.sb.run(SCRIPT, 'status', '--root', '/').stdout)

    def test_stage_file_of_another_owner_is_refused(self):
        # a root-owned host file appearing at a stage path (bind-mounted here; a hard link would look the same to a
        # path check): the kernel path of the open file is the stage path, but its owner is not the stage's owner
        rel = 'usr/share/applications/org.quickshell.desktop'
        bind = ['--ro-bind', '/etc/os-release', str(self.stage / rel)]
        # negative control: the pre-fix copy line (install -m 0644 -o root -g root -T -- "$STAGE/$rel" "$tmp"), run as
        # the sandbox root; the pre-fix script itself cannot reach it under --root / (its dir_snapshot never ends)
        cp = subprocess.run(self.sb.argv(SCRIPT, f'install -m 0644 -o root -g root -T -- "{self.stage}/{rel}" /tmp/x && cat /tmp/x',
                                         bind), capture_output=True, text=True, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout, Path('/etc/os-release').read_text(), 'pre-fix: root copies the foreign file (the defect)')
        r = self.sb.run(SCRIPT, 'install', '--root', '/', '--stage', self.stage, extra=bind)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not the stage owner', r.stderr)
        self.assertIn('partial install undone', r.stderr)
        self.assertEqual(self.sb.snapshot(), self.before)


@unittest.skipUnless(shutil.which('gcc') and shutil.which('readelf') and shutil.which('strip'), 'needs gcc, strip, readelf')
class TestPrefixDefects(unittest.TestCase):
    """The verifier's lesser findings, each with the pre-fix script as the negative control (scratch roots only)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mios-qs-defects-'))
        self.root = make_root(self.tmp)
        self.stage = make_stage(self.tmp)
        self.old = prefix_script(self.tmp / 'prefix.sh')

    def tearDown(self):
        for p in [self.tmp, *self.tmp.rglob('*')]:
            if p.is_dir() and not p.is_symlink():
                os.chmod(p, 0o700)
        shutil.rmtree(self.tmp)

    def need_old(self):
        if self.old is None:
            self.skipTest(f'git show {PREFIX_REF} unavailable')

    def state(self):
        return self.root / 'var/lib/mios-build/rollback/quickshell-candidate'

    def drop_lib64_and_share(self):
        # the last directories in sorted order (usr/lib64/..., usr/share/...) do not exist in this root
        for d in ('usr/lib64', 'usr/share'):
            os.chmod(self.root / d, 0o755)
            shutil.rmtree(self.root / d)

    def test_missing_last_ancestor_is_not_a_silent_failure(self):
        self.need_old()
        self.drop_lib64_and_share()
        before = snapshot(self.root)
        old = run('install', '--root', self.root, '--stage', self.stage, script=self.old)
        self.assertEqual(old.returncode, 1, 'pre-fix: exits 1 ...')
        self.assertNotIn('ERROR', old.stderr, '... without any ERROR line (the defect)')
        for p in self.state().glob('txn-*'):
            shutil.rmtree(p)
        r = run('install', '--root', self.root, '--stage', self.stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.root / f'{QML}/qmldir').is_file())
        self.assertEqual(oct((self.root / 'usr/lib64').stat().st_mode & 0o777), '0o755', 'created directories are 0755')
        rb = run('rollback', '--root', self.root)
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_status_with_unreadable_state_fails_loudly(self):
        self.need_old()
        if os.geteuid() == 0:
            self.skipTest('root can always read the state')
        r = run('install', '--root', self.root, '--stage', self.stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        parent = self.state().parent
        os.chmod(parent, 0o700)
        os.chmod(parent, 0o000)      # like the 0700 root-owned state seen by the operator's user
        try:
            old = run('status', '--root', self.root, script=self.old)
            self.assertEqual(old.returncode, 0)
            self.assertIn('no active candidate install', old.stdout, 'pre-fix: reports no install (the defect)')
            new = run('status', '--root', self.root)
            self.assertNotEqual(new.returncode, 0)
            self.assertIn('run status with sudo', new.stderr)
            self.assertNotIn('no active', new.stdout)
        finally:
            os.chmod(parent, 0o700)
        self.assertIn('active:', run('status', '--root', self.root).stdout)
        self.assertEqual(run('rollback', '--root', self.root).returncode, 0)
        self.assertIn('no active', run('status', '--root', self.root).stdout, 'a readable empty state is still "no active"')

    def test_rollback_refuses_files_changed_since_the_install(self):
        self.need_old()
        root2 = make_root(self.tmp / 'r2')
        for root, script in ((self.root, self.old), (root2, SCRIPT)):
            r = run('install', '--root', root, '--stage', self.stage, script=script)
            self.assertEqual(r.returncode, 0, r.stderr)
            # a later quickshell package update rewrites an installed file and adds a candidate-path file
            (root / f'{QML}/Io/FileView.qml').write_text('// 0.3.3 package update\n')
        old = run('rollback', '--root', self.root, script=self.old)
        self.assertEqual(old.returncode, 0)
        self.assertEqual((self.root / f'{QML}/Io/FileView.qml').read_text(), '// 0.2.1\n',
                         'pre-fix: rollback downgrades the newer package file to the 0.2.1 backup (the defect)')
        before = snapshot(root2)
        new = run('rollback', '--root', root2)
        self.assertNotEqual(new.returncode, 0)
        self.assertIn('changed since the install', new.stderr)
        self.assertIn(f'{os.path.realpath(root2)}/{QML}/Io/FileView.qml', new.stderr)
        self.assertEqual(snapshot(root2), before, 'a refused rollback touches nothing')
        self.assertIn('active:', run('status', '--root', root2).stdout)

    def test_stage_symlink_swapped_after_validation_is_never_followed(self):
        self.need_old()
        rel = 'usr/share/applications/org.quickshell.desktop'
        secret = self.tmp / 'secret'
        secret.write_text('not for the candidate\n')
        # the pre-fix copy command, run after validation saw a regular file and the entry became a symlink
        os.chmod(self.stage / 'usr/share/applications', 0o700)
        (self.stage / rel).unlink()
        (self.stage / rel).symlink_to(secret)
        out = self.tmp / 'copied'
        subprocess.run(['install', '-m', '0644', '-T', '--', str(self.stage / rel), str(out)], check=True)
        self.assertEqual(out.read_text(), 'not for the candidate\n', 'pre-fix: the copy follows the symlink (the defect)')
        cases = {'symlinked entry': (rel, 'resolves to')}
        # a swapped ancestor directory: the stage's applications dir becomes a symlink to a directory elsewhere
        elsewhere = self.tmp / 'elsewhere'
        elsewhere.mkdir()
        (elsewhere / 'org.quickshell.desktop').write_text('elsewhere\n')
        apps2 = self.stage / 'usr/share/icons/hicolor/scalable/apps'
        shutil.rmtree(apps2)
        apps2.symlink_to(elsewhere)
        (elsewhere / 'org.quickshell.svg').write_text('<svg>elsewhere</svg>\n')
        cases['symlinked ancestor'] = ('usr/share/icons/hicolor/scalable/apps/org.quickshell.svg', 'resolves to')
        for label, (r, msg) in cases.items():
            with self.subTest(label):
                p = subprocess.run(['/bin/bash', '-c', 'source "$1"; STAGE=$(realpath "$2"); open_stage_file "$3"; cat <&3',
                                    '_', str(SCRIPT), str(self.stage), r], capture_output=True, text=True, timeout=30)
                self.assertNotEqual(p.returncode, 0, label)
                self.assertIn(msg, p.stderr)
                self.assertNotIn('elsewhere', p.stdout)
                self.assertNotIn('not for the candidate', p.stdout)
        ok = subprocess.run(['/bin/bash', '-c', 'source "$1"; STAGE=$(realpath "$2"); open_stage_file "$3"; cat <&3',
                             '_', str(SCRIPT), str(self.stage), f'{QML}/qmldir'], capture_output=True, text=True, timeout=30)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(ok.stdout, f'0.3.2 {QML}/qmldir\n')



@unittest.skipUnless(os.environ.get('MIOS_QS_STAGE'), 'set MIOS_QS_STAGE to the real candidate stage')
class TestRealStage(unittest.TestCase):
    def test_real_stage_in_scratch_root(self):
        stage = Path(os.environ['MIOS_QS_STAGE'])
        tmp = Path(tempfile.mkdtemp(prefix='mios-qs-real-'))
        try:
            root = tmp / 'root'
            for rel in ('usr/bin/quickshell', 'usr/share/applications/org.quickshell.desktop',
                        'usr/share/icons/hicolor/scalable/apps/org.quickshell.svg'):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2('/' + rel, root / rel)
            (root / 'usr/bin/qs').symlink_to('/usr/bin/quickshell')
            shutil.copytree('/' + QML, root / QML, symlinks=True)
            for d in [p for p in root.rglob('*') if p.is_dir()] + [root]:
                os.chmod(d, 0o755)
            os.chmod(root / 'usr/lib64', 0o555)
            before = snapshot(root)
            r = run('install', '--root', root, '--stage', stage)
            self.assertEqual(r.returncode, 0, r.stderr)
            q = root / 'usr/bin/quickshell'
            self.assertFalse(has_debug(q))
            self.assertLess(q.stat().st_size, 32 << 20, 'stripped binary should be package-sized')
            self.assertIn('0.3.2', subprocess.run([str(q), '--version'], capture_output=True, text=True).stdout)
            after = snapshot(root)
            for rel, meta in before.items():
                if meta[0] == 'd':
                    self.assertEqual(after[rel], meta, rel)
            r = run('rollback', '--root', root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(snapshot(root), before)
        finally:
            for p in [tmp, *tmp.rglob('*')]:
                if p.is_dir() and not p.is_symlink():
                    os.chmod(p, 0o700)
            shutil.rmtree(tmp)

    @unittest.skipUnless(can_sandbox(), 'needs bwrap and unprivileged user namespaces')
    def test_real_stage_with_root_slash_in_sandbox(self):
        """The lead's exact step 2 (install --root /, then status and rollback) with the real stage, in the sandbox."""
        stage = Path(os.environ['MIOS_QS_STAGE']).resolve()
        tmp = Path(tempfile.mkdtemp(prefix='mios-qs-real-sbx-'))
        try:
            sb = Sandbox(tmp, stage, seed=Path('/'))
            before = sb.snapshot()
            r = sb.run(SCRIPT, 'install', '--root', '/', '--stage', stage, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(r.stderr, r'\n755 \S+ /usr\n755 \S+ /usr/bin\n555 \S+ /usr/lib64\n')
            q = sb.dirs['bin'] / 'quickshell'
            self.assertFalse(has_debug(q))
            self.assertLess(q.stat().st_size, 32 << 20)
            self.assertEqual(os.readlink(sb.dirs['bin'] / 'qs'), 'quickshell')
            st = sb.run(SCRIPT, 'status', '--root', '/')
            self.assertIn('active:', st.stdout)
            rb = sb.run(SCRIPT, 'rollback', '--root', '/', timeout=600)
            self.assertEqual(rb.returncode, 0, rb.stderr)
            self.assertEqual(sb.snapshot(), before)
            self.last_stderr = r.stderr + st.stdout + rb.stderr
            print(self.last_stderr)
        finally:
            for p in [tmp, *tmp.rglob('*')]:
                if p.is_dir() and not p.is_symlink():
                    os.chmod(p, 0o700)
            shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main(verbosity=2)
