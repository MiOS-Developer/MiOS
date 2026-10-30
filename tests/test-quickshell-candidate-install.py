#!/usr/bin/env python3
# AI-hint: Offline safety contract of src/quickshell/install-candidate.sh in private scratch roots (never the live
# root): existing directory modes and owners are never changed (the pre-fix `tar | tar -C /` pipeline reset /usr,
# /usr/bin and /usr/lib64 to 0700 -- reproduced here as the negative control), the binary is stripped, every replaced
# file is backed up and verified, a failed or interrupted install is undone, rollback restores the tree byte for byte
# and removes the files only the candidate shipped (the pre-fix rollback left them -- negative control), unsafe stages
# are refused, and the script starts, stops or signals nothing.
# AI-related: src/quickshell/install-candidate.sh, usr/share/doc/mios/quickshell-candidate.md,
# tests/test-quickshell-candidate-live.py
# AI-functions: TestCandidateInstall, make_root, make_stage, snapshot, run
"""Usage: python3 tests/test-quickshell-candidate-install.py
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


def run(*args, env_extra=None):
    # a minimal environment: the script must not depend on the caller's
    env = {'PATH': '/usr/bin:/bin', 'HOME': os.environ.get('HOME', '/tmp'), 'LC_ALL': 'C'}
    env.update(env_extra or {})
    return subprocess.run([str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env, timeout=300)


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
