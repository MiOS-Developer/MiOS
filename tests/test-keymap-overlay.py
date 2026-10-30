#!/usr/bin/env python3
# AI-hint: Real-binary test of the Super+Escape overlay (mios-sync-theme -> native `mios-gen keymap-overlay` -> theme.json keymap) generated from the global tables (G1 C-25): global remaps, bypass chords, bypass_mod chords, toggle_chord labels, system layer and nav rows; negative controls: main's script (legacy tables) and a mios-gen without keymap-overlay both fail the same checks.
# AI-related: usr/libexec/mios/mios-sync-theme, tools/native/mios-gen/src/global_bindings.rs, usr/share/mios/quickshell/Keymap.qml, usr/share/mios/mios.toml [keybindings.global], docs/design/mios-global-bindings-tests.md
# Runs the worktree's mios-sync-theme against scratch layer files (never /etc/mios/theme, never the operator's tiers).
# MIOS_GEN = the candidate mios-gen (required); MIOS_GEN_OLD = a mios-gen without keymap-overlay (default
# /usr/bin/mios-gen). MIOS_SYNC_THEME overrides the script under test; MIOS_SYNC_THEME_OLD names the negative-control
# script (default: the script at git main, which must FAIL the remap and toggle_chord checks).
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


GEN = os.environ.get('MIOS_GEN', '')
GEN_OLD = os.environ.get('MIOS_GEN_OLD', '/usr/bin/mios-gen')


def keymap(script, vendor_text, tmp, tag, gen=None):
    d = Path(tmp) / tag
    d.mkdir()
    (d / 'vendor.toml').write_text(vendor_text)
    (d / 'user.toml').write_text('')
    env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_VENDOR_TOML_D', 'MIOS_USER_TOML_D')}
    env.update(MIOS_VENDOR_TOML=str(d / 'vendor.toml'), MIOS_HOST_TOML=str(d / 'none.toml'),
               MIOS_USER_TOML=str(d / 'user.toml'), MIOS_THEME_OUT_DIR=str(d / 'out'),
               MIOS_LIB_DIR=str(ROOT / 'usr/lib/mios'), MIOS_GEN=gen or GEN)
    (d / 'out').mkdir()
    r = subprocess.run(['bash', str(script), '--bridge-only'], env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'{script}: rc={r.returncode} {r.stderr[-400:]}')
    return json.loads((d / 'out/theme.json').read_text())['keymap']


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
    nav = text + '\n[keybindings.global.controller.nav]\nenabled = true\n[keybindings.global.controller.nav.contexts]\ndesktop-2d = "desktop"\n[keybindings.global.controller.nav.desktop]\nBTN_SOUTH = ["key:ENTER", "key:SHIFT+ENTER"]\n'
    km4 = keymap(script, nav, tmp, f'{tag}-nav', gen)
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
            r = subprocess.run(['git', '-C', str(ROOT), 'show', 'main:usr/libexec/mios/mios-sync-theme'], capture_output=True, text=True)
            if r.returncode == 0:
                old = str(Path(tmp) / 'old-sync-theme')
                Path(old).write_text(r.stdout)
        if old:
            neg = {n: ok for n, ok, _ in suite(old, tmp, 'old')}
            # Negative control: the pre-G1 overlay was built from legacy tables, so these must fail there.
            for n in ['remap keymap -> SUPER+F1: overlay shows SUPER+F1 and never the stale SUPER+Escape',
                      'toggle_chord -> [BTN_TL, BTN_TR]: the release label follows it (no hard-coded L3 + R3)',
                      'vendor: every bypass chord is listed and says it works over captured apps']:
                check(f'negative control (main script fails): {n}', neg.get(n) is False)
        else:
            check('negative-control script available', False, 'no git main and no MIOS_SYNC_THEME_OLD')
        # Negative control: the overlay comes from the native generator. A mios-gen without keymap-overlay leaves it
        # empty (warning on stderr), so the script holds no binding logic of its own.
        if GEN_OLD and os.access(GEN_OLD, os.X_OK) and Path(GEN_OLD).resolve() != Path(GEN).resolve():
            km = keymap(SCRIPT, VENDOR.read_text(), tmp, 'oldgen', GEN_OLD)
            check('negative control (mios-gen without keymap-overlay): the overlay is empty', km == [], len(km))
    print(f'{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
