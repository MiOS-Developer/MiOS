#!/usr/bin/env python3
# AI-hint: Real-binary test of the Super+Escape overlay (mios-sync-theme -> native `mios-gen keymap-overlay` -> theme.json keymap) generated from the global tables (G1 C-25): global remaps, bypass chords, bypass_mod chords, toggle_chord labels, system layer and nav rows; negative controls: immutable legacy keymap logic and an unsupported generator fail the same checks.
# AI-related: usr/libexec/mios/mios-sync-theme, tools/native/mios-gen/src/global_bindings.rs, usr/share/mios/quickshell/Keymap.qml, usr/share/mios/mios.toml [keybindings.global], docs/design/mios-global-bindings-tests.md
# Runs the worktree's mios-sync-theme against scratch layer files (never /etc/mios/theme, never the operator's tiers).
# MIOS_GEN selects the candidate explicitly; otherwise use a source-built or installed native generator.
# MIOS_SYNC_THEME overrides the script under test; MIOS_SYNC_THEME_OLD may name a genuine pre-global-bindings script.
# Its keymap block alone is transplanted into the current bridge so unrelated geometry schema changes cannot fake a failure.
import json, os, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get('MIOS_SYNC_THEME', ROOT / 'usr/libexec/mios/mios-sync-theme'))
VENDOR = ROOT / 'usr/share/mios/mios.toml'
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)
    return ok


def generator():
    explicit = os.environ.get('MIOS_GEN')
    if explicit:
        return explicit  # A broken explicit candidate must never fall through to an installed binary.
    for path in (ROOT / 'tools/native/target/release/mios-gen', ROOT / 'tools/native/target/debug/mios-gen',
                 ROOT / 'usr/bin/mios-gen', Path('/usr/bin/mios-gen')):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return ''


GEN = generator()
GEN_OLD = os.environ.get('MIOS_GEN_OLD')


def keymap(script, vendor_text, tmp, tag, gen=None, user_text=""):
    d = Path(tmp) / tag
    d.mkdir()
    (d / 'vendor.toml').write_text(vendor_text)
    (d / 'user.toml').write_text(user_text)
    env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_VENDOR_TOML_D', 'MIOS_USER_TOML_D')}
    env.update(MIOS_VENDOR_TOML=str(d / 'vendor.toml'), MIOS_HOST_TOML=str(d / 'none.toml'),
               MIOS_USER_TOML=str(d / 'user.toml'), MIOS_THEME_OUT_DIR=str(d / 'out'),
               MIOS_LIB_DIR=str(ROOT / 'usr/lib/mios'), MIOS_GEN=gen or GEN)
    (d / 'out').mkdir()
    r = subprocess.run(['bash', str(script), '--bridge-only'], env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'{script}: rc={r.returncode} {r.stderr[-400:]}')
    return json.loads((d / 'out/theme.json').read_text())['keymap']


def configured_gen_runs(script, tmp, tag, as_root):
    """Observe the merged-tier executable policy with a private marker and real effective uid."""
    d = Path(tmp) / tag
    d.mkdir()
    marker = d / 'ran'
    fake = d / 'fake-gen'
    fake.write_text(f'#!/bin/sh\ntouch {marker}\necho "[]"\n')
    fake.chmod(0o755)
    (d / 'vendor.toml').write_text(VENDOR.read_text())
    (d / 'user.toml').write_text(f'[desktop.controls]\nmios_gen = "{fake}"\n')
    (d / 'out').mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_GEN', 'MIOS_VENDOR_TOML_D', 'MIOS_USER_TOML_D')}
    env.update(MIOS_VENDOR_TOML=str(d / 'vendor.toml'), MIOS_HOST_TOML=str(d / 'none.toml'), MIOS_USER_TOML=str(d / 'user.toml'),
               MIOS_THEME_OUT_DIR=str(d / 'out'), MIOS_LIB_DIR=str(ROOT / 'usr/lib/mios'))
    # A root CI container can exercise both uid boundaries without nested user namespaces.
    # The child drops its identity and writes only the private fixture marker/output.
    preexec = None
    prefix = []
    if os.geteuid() == 0 and not as_root:
        Path(tmp).chmod(0o755)
        os.chown(d, 65534, 65534)
        os.chown(d / 'out', 65534, 65534)
        def drop_identity():
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
        preexec = drop_identity
    elif as_root and os.geteuid() != 0:
        prefix = ['unshare', '-r']
    cmd = prefix + ['bash', str(script), '--bridge-only']
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, preexec_fn=preexec)
    return r.returncode == 0 and (d / 'out/theme.json').is_file(), marker.exists(), r.stderr[-300:]


def has(km, keys, label_re=None):
    norm = lambda s: s.replace(' ', '').upper()
    return any(norm(k['keys']) == norm(keys) and (label_re is None or re.search(label_re, k['label'])) for k in km)


def remap(text, row_id, chord):
    """Change one [[keybindings.global.bindings]] row's chord in the SSOT text (the vendor copy under test)."""
    pat = re.compile(r'(\[\[keybindings\.global\.bindings\]\]\nid = "' + re.escape(row_id) + r'"\n(?:[^\[\n][^\n]*\n)*?chord = )"[^"]*"')
    out, n = pat.subn(lambda m: m.group(1) + json.dumps(chord), text, count=1)
    assert n == 1, row_id
    return out


def suite(script, tmp, tag, gen=None):
    """The C-25 checks; returns a list of (name, ok, detail) without recording them."""
    text = VENDOR.read_text()
    out = []
    km = keymap(script, text, tmp, f'{tag}-vendor', gen)
    out.append(('vendor: every bypass chord is listed and says it works over captured apps', all(has(km, c, 'over captured') for c in ['SUPER+CTRL+Space', 'SUPER+CTRL+Escape', 'SUPER+CTRL+F11', 'SUPER+CTRL+Print']), ''))
    out.append(('vendor: session scale (bypass_mod chords) listed as reaching the host over captured apps', has(km, 'SUPER+CTRL+equal', 'over captured'), ''))
    out.append(('vendor: release chord label comes from toggle_chord', has(km, 'Pad L3 + R3', 'release capture'), ''))
    out.append(('vendor: Guide system layer rows listed', has(km, 'Pad Guide + D-pad left', 'Focus left') and has(km, 'Pad Guide + East', 'Close'), ''))
    km2 = keymap(script, remap(text, 'keymap', 'SUPER+F1'), tmp, f'{tag}-remap', gen)
    lab = [k for k in km2 if re.search(r'Keybind map', k['label']) and 'over captured' not in k['label']]
    out.append(('remap keymap -> SUPER+F1: overlay shows SUPER+F1 and never the stale SUPER+Escape', lab and all(k['keys'].replace(' ', '').upper() == 'SUPER+F1' for k in lab), lab))
    t3 = text.replace('toggle_chord = ["BTN_THUMBL", "BTN_THUMBR"]', 'toggle_chord = ["BTN_TL", "BTN_TR"]', 1)
    assert t3 != text
    km3 = keymap(script, t3, tmp, f'{tag}-chord', gen)
    out.append(('toggle_chord -> [BTN_TL, BTN_TR]: the release label follows it (no hard-coded L3 + R3)', has(km3, 'Pad LB + RB', 'release') and not any('L3 + R3' in k['keys'] for k in km3), [k for k in km3 if 'Pad' in k['keys']][:2]))
    nav = '[keybindings.global.controller.nav]\nenabled = true\n[keybindings.global.controller.nav.contexts]\ndesktop-2d = "desktop"\n[keybindings.global.controller.nav.desktop]\nBTN_SOUTH = ["key:ENTER", "key:SHIFT+ENTER"]\n'
    km4 = keymap(script, text, tmp, f'{tag}-nav', gen, user_text=nav)
    out.append(('a declared nav profile appears in the overlay (tap and hold)', has(km4, 'Pad South', 'ENTER.*hold'), ''))
    return out


def main():
    if not SCRIPT.is_file() or not GEN or not os.access(GEN, os.X_OK):
        check('script under test and candidate MIOS_GEN exist', False, f'{SCRIPT} MIOS_GEN={GEN!r}')
        return 1
    with tempfile.TemporaryDirectory(prefix='mios-keymap-') as tmp:
        for name, ok, detail in suite(SCRIPT, tmp, 'new'):
            check(name, ok, detail)
        old = os.environ.get('MIOS_SYNC_THEME_OLD')
        if not old:
            r = subprocess.run(['git', '-C', str(ROOT), 'show', 'bcd6b3f22:usr/libexec/mios/mios-sync-theme'], capture_output=True, text=True)
            if r.returncode == 0:
                old = str(Path(tmp) / 'old-sync-theme')
                Path(old).write_text(r.stdout)
        if old:
            def keymap_span(source):
                begin = source.index('# Keybind map (Super+Escape overlay):')
                end = source.index('resolved["search_actions"]', begin)
                return begin, end
            legacy_source, candidate_source = Path(old).read_text(), SCRIPT.read_text()
            a, b = keymap_span(legacy_source)
            c, d = keymap_span(candidate_source)
            legacy = Path(tmp) / 'mios-legacy-keymap-control'
            legacy.write_text(candidate_source[:c] + legacy_source[a:b] + candidate_source[d:])
            neg = {n: ok for n, ok, _ in suite(legacy, tmp, 'old')}
            # Negative control: the pre-G1 overlay was built from legacy tables, so these must fail there.
            for n in ['remap keymap -> SUPER+F1: overlay shows SUPER+F1 and never the stale SUPER+Escape',
                      'toggle_chord -> [BTN_TL, BTN_TR]: the release label follows it (no hard-coded L3 + R3)',
                      'vendor: every bypass chord is listed and says it works over captured apps']:
                check(f'negative control (immutable pre-global script fails): {n}', neg.get(n) is False)
        else:
            check('negative-control script available', False, 'no immutable pre-global script and no MIOS_SYNC_THEME_OLD')
        # An unavailable verb is a deterministic stand-in for a pre-overlay generator.
        # Never infer missing functionality merely from a binary's installed location.
        unsupported = GEN_OLD
        if not unsupported:
            stub = Path(tmp) / 'mios-unsupported-generator'
            stub.write_text('#!/bin/sh\necho "unknown subcommand keymap-overlay" >&2\nexit 2\n')
            stub.chmod(0o755)
            unsupported = str(stub)
        km = keymap(SCRIPT, VENDOR.read_text(), tmp, 'oldgen', unsupported)
        check('negative control (generator rejects keymap-overlay): the overlay is empty', km == [], len(km))
        ok, ran, err = configured_gen_runs(SCRIPT, tmp, 'cfg-user', False)
        check('as a user: the configured mios_gen runs (the editor\'s candidate path)', ok and ran, err)
        ok, ran, err = configured_gen_runs(SCRIPT, tmp, 'cfg-root', True)
        check('as root (euid 0): a tier-configured mios_gen never runs', ok and not ran, err)
        # Plant only the old root-policy defect; keep the complete current bridge and tier resolver.
        source = SCRIPT.read_text()
        policy = 'None if os.geteuid() == 0 else merged.get'
        assert source.count(policy) == 1, 'root executable policy missing'
        prev = Path(tmp) / 'mios-tier-executable-negative'
        prev.write_text(source.replace(policy, 'merged.get', 1))
        ok, ran, err = configured_gen_runs(prev, tmp, 'cfg-root-prev', True)
        check('negative control (root guard removed): root ran the tier-configured binary', ok and ran, err)
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
