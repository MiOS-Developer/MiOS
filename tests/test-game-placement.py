#!/usr/bin/env python3
# AI-hint: Game window placement: games open centred at the window_opening size on the focused output, fixed-size games are never resized (no black regrown area), self-placement is refused; SSOT/configurator wiring, real mios-gen/miosd binaries, and an opt-in live nested PiP session (MIOS_LIVE_NESTED=1) with XWayland, Wine and Vulkan clients.
# AI-related: src/mios-rs/miosd/src/placement.rs, tools/native/mios-resolver/src/lib.rs (window_opening::Games), tools/native/mios-gen/src/render.rs, tools/native/mios-gen/src/nested_session.rs, tests/fixtures/game-clients/x11-game-probe.py, usr/share/mios/mios.toml [desktop.hyprland.placement.games]
# AI-functions: TestGamePlacementWiring, TestGamePlacementBinaries, TestGamePlacementLive
"""Game window placement.

Unit (always): the SSOT table, its configurator inputs and the committed Hyprland projection agree.
Real binaries (MIOS_GEN / MIOSD, default the installed ones): render-hyprland projects the game rules from
an SSOT copy and drops them when the table is disabled; invalid values fail the render; miosd's rule size is
the size the game rule carries.
Live (MIOS_LIVE_NESTED=1): an owned nested PiP session (`MIOS_GEN nested-session --layout pip`) with the
placement rules of MIOS_HYPRLAND_LUA installed and `MIOSD placement serve` running inside it; X11 game probes,
Wine (winemine = fixed size, notepad = resizable; MIOS_LIVE_WINEPREFIX) and vkcube (xcb + wayland) are
placed, toggled and resized; screenshots measure black regions. Old-code controls: MIOS_GEN=/usr/bin/mios-gen
(no nested XWayland), MIOSD=/usr/bin/miosd with MIOS_HYPRLAND_LUA=<main's projection> (games left offscreen).
The primary compositor is never addressed; the nested Hyprland is SIGTERMed at the end.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = Path(os.environ.get('MIOS_VENDOR_TOML') or ROOT / 'usr/share/mios/mios.toml')
LUA = Path(os.environ.get('MIOS_HYPRLAND_LUA') or ROOT / 'usr/share/mios/hyprland/hyprland.lua')
MIOS_GEN = os.environ.get('MIOS_GEN', '/usr/bin/mios-gen')
MIOSD = os.environ.get('MIOSD', '/usr/bin/miosd')
PROBE = ROOT / 'tests/fixtures/game-clients/x11-game-probe.py'
RULE = re.compile(r'^hl\.window_rule\(.*\["name"\] = "(mios-placement[^"]*)".*\)$')
GAME_KEYS = ['enabled', 'class', 'extra_class', 'content_game', 'suppress_client_geometry', 'oversize', 'tag', 'fixed_tag']


def clean_env(**kw):
    env = {k: v for k, v in os.environ.items() if k not in ('MIOS_TOML', 'MIOS_VENDOR_TOML')}
    env.update(kw)
    return env


def placement_rules(lua_text):
    return {m.group(1): line for line in lua_text.splitlines() if (m := RULE.match(line))}


class TestGamePlacementWiring(unittest.TestCase):
    def setUp(self):
        self.doc = tomllib.loads(SSOT.read_text())
        self.games = self.doc['desktop']['hyprland']['placement']['games']

    def test_ssot_table_is_complete_and_shares_the_game_class_list(self):
        self.assertEqual(sorted(self.games), sorted(GAME_KEYS))
        node = self.doc
        for part in self.games['class'].split('.'):
            node = node[part]
        self.assertIn('steam_app_.*', node, 'Proton games (WM_CLASS steam_app_<id>) are in the shared list')
        self.assertIn(self.games['oversize'], ('fit', 'keep'))
        self.assertNotEqual(self.games['tag'], self.games['fixed_tag'])
        self.assertIs(self.doc['desktop']['hyprland']['config']['xwayland']['force_zero_scaling'], False)

    def test_configurator_exposes_every_key(self):
        html = (ROOT / 'usr/share/mios/configurator/mios.html').read_text()
        for k in GAME_KEYS:
            self.assertIn(f'data-key="desktop.hyprland.placement.games.{k}"', html, k)
        self.assertIn('data-key="desktop.hyprland.config.xwayland.force_zero_scaling"', html)

    def test_projection_carries_resizable_and_fixed_rules(self):
        rules = placement_rules((ROOT / 'usr/share/mios/hyprland/hyprland.lua').read_text())
        for name in ('mios-placement', 'mios-placement-games', 'mios-placement-games-fixed',
                     'mios-placement-games-content', 'mios-placement-games-content-fixed'):
            self.assertIn(name, rules)
        size = re.search(r'\["size"\] = ("[^"]+")', rules['mios-placement']).group(1)
        self.assertIn(f'["size"] = {size}', rules['mios-placement-games'], 'one opening size for every window')
        for fixed in ('mios-placement-games-fixed', 'mios-placement-games-content-fixed'):
            self.assertNotIn('["size"]', rules[fixed], 'a fixed-size game is never sized by the compositor')
            self.assertIn('["float"] = true}', rules[fixed], 'matches only windows Hyprland floated itself')
        for name, line in rules.items():
            if name.startswith('mios-placement-games'):
                self.assertIn('["suppress_event"] = "x11configurerequest"', line)

    def test_probe_fixture_compiles(self):
        subprocess.run([sys.executable, '-m', 'py_compile', str(PROBE)], check=True)


@unittest.skipUnless(Path(MIOS_GEN).is_file(), 'mios-gen binary missing')
class TestGamePlacementBinaries(unittest.TestCase):
    def render(self, mutate=None):
        d = Path(tempfile.mkdtemp(prefix='mios-game-render-'))
        self.addCleanup(shutil.rmtree, d, True)
        text = SSOT.read_text()
        if mutate:
            text = mutate(text)
        (d / 'usr/share/mios').mkdir(parents=True)
        (d / 'usr/share/mios/mios.toml').write_text(text)
        r = subprocess.run([MIOS_GEN, 'render-hyprland', '--root', str(d)], capture_output=True, text=True,
                           env=clean_env(), timeout=120)
        out = d / 'usr/share/mios/hyprland/hyprland.lua'
        return r, (out.read_text() if out.is_file() else '')

    def test_render_projects_game_rules(self):
        r, lua = self.render()
        self.assertEqual(r.returncode, 0, r.stderr)
        rules = placement_rules(lua)
        self.assertIn('mios-placement-games-fixed', rules)
        self.assertEqual(rules, placement_rules((ROOT / 'usr/share/mios/hyprland/hyprland.lua').read_text()),
                         'committed projection is current')

    def test_disabled_table_projects_no_game_rules(self):
        r, lua = self.render(lambda t: t.replace('[desktop.hyprland.placement.games]\nenabled = true',
                                                 '[desktop.hyprland.placement.games]\nenabled = false'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(placement_rules(lua)), ['mios-placement'])

    def test_invalid_oversize_fails_the_render(self):
        r, _ = self.render(lambda t: t.replace('oversize = "fit"', 'oversize = "stretch"'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('oversize', r.stdout + r.stderr)

    @unittest.skipUnless(Path(MIOSD).is_file(), 'miosd binary missing')
    def test_miosd_rule_size_is_the_game_rule_size(self):
        r = subprocess.run([MIOSD, 'placement', 'rule-size'], capture_output=True, text=True,
                           env=clean_env(MIOS_VENDOR_TOML=str(SSOT)), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        rules = placement_rules((ROOT / 'usr/share/mios/hyprland/hyprland.lua').read_text())
        self.assertIn(f'["size"] = "{r.stdout.strip()}"', rules['mios-placement-games'])


# ------------------------------------------------------------------------------------------------ live

def black_fraction(png, box, scale):
    from PIL import Image
    im = Image.open(png).convert('RGB')
    x, y, w, h = [int(round(v * scale)) for v in box]
    x0, y0, x1, y1 = max(x + 2, 0), max(y + 2, 0), min(x + w - 2, im.width), min(y + h - 2, im.height)
    if x1 <= x0 or y1 <= y0:
        return None
    px = list(im.crop((x0, y0, x1, y1)).getdata())
    return round(sum(1 for p in px if max(p) < 12) / len(px), 3)


@unittest.skipUnless(os.environ.get('MIOS_LIVE_NESTED') == '1', 'live nested session test (MIOS_LIVE_NESTED=1)')
class TestGamePlacementLive(unittest.TestCase):
    """One owned nested PiP session for every scenario (setUpClass), SIGTERMed in tearDownClass."""

    @classmethod
    def setUpClass(cls):
        cls.out = Path(os.environ.get('MIOS_LIVE_OUT') or tempfile.mkdtemp(prefix='mios-game-live-'))
        cls.out.mkdir(parents=True, exist_ok=True)
        cls.procs = []
        cls.launcher = subprocess.Popen([MIOS_GEN, 'nested-session', '--layout', 'pip'],
                                        stdout=open(cls.out / 'launcher.log', 'w'), stderr=subprocess.STDOUT,
                                        env=clean_env(MIOS_VENDOR_TOML=str(SSOT)))
        d = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-nested-desktop-{cls.launcher.pid}'
        cls.session = None
        for _ in range(300):
            if (d / 'session.json').is_file():
                cls.session = json.loads((d / 'session.json').read_text())
                break
            time.sleep(0.2)
        if cls.session is None:
            cls.tearDownClass()
            raise RuntimeError(f'nested session did not start; see {cls.out}/launcher.log')
        inst = cls.session['instance']
        cls.env = clean_env(XDG_RUNTIME_DIR=str(d), HYPRLAND_INSTANCE_SIGNATURE=inst['instance'],
                            WAYLAND_DISPLAY=inst['wl_socket'], MIOS_VENDOR_TOML=str(SSOT))
        cls.env.pop('DISPLAY', None)
        cls.x11 = cls.session.get('x11_display')
        if cls.x11:
            cls.env['DISPLAY'] = cls.x11
        cls.rules = placement_rules(LUA.read_text())
        cls.rule_results = {n: cls.ctl('eval', line).strip() for n, line in cls.rules.items()}
        cls.daemon = subprocess.Popen([MIOSD, 'placement', 'serve'], env=cls.env,
                                      stdout=open(cls.out / 'placement.log', 'w'), stderr=subprocess.STDOUT)
        time.sleep(1.0)
        geo = subprocess.run([MIOSD, 'placement', 'geometry'], env=cls.env, capture_output=True, text=True, timeout=30)
        cls.geometry = json.loads(geo.stdout)[0]
        mon = json.loads(cls.ctl('-j', 'monitors'))[0]
        cls.scale = mon['scale']
        cls.output = (mon['x'], mon['y'], mon['width'] / mon['scale'], mon['height'] / mon['scale'])
        (cls.out / 'setup.json').write_text(json.dumps({'session': cls.session, 'rules': cls.rule_results,
                                                        'geometry': cls.geometry, 'output': cls.output}, indent=1))

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, 'procs', []) + [getattr(cls, 'daemon', None)]:
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        prefix = os.environ.get('MIOS_LIVE_WINEPREFIX')
        if prefix:
            subprocess.run(['wineserver', '-k'], env=dict(os.environ, WINEPREFIX=prefix), capture_output=True)
        s = getattr(cls, 'session', None)
        if s:
            try:
                os.kill(s['instance']['pid'], signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            cls.launcher.wait(20)
        except subprocess.TimeoutExpired:
            cls.launcher.terminate()

    @classmethod
    def ctl(cls, *a):
        return subprocess.run(['hyprctl', *a], env=cls.env, capture_output=True, text=True, timeout=20).stdout

    def clients(self):
        try:
            return json.loads(self.ctl('-j', 'clients'))
        except ValueError:
            return []

    def wait_for(self, pred, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for c in self.clients():
                if pred(c) and c['size'][0] > 1:
                    return c
            time.sleep(0.1)
        return None

    def spawn(self, argv, env=None, log=None):
        p = subprocess.Popen(argv, env=env or self.env, stdout=open(log, 'w') if log else subprocess.DEVNULL,
                             stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p

    def stop(self, p):
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()

    def probe(self, cls_name, *args, seconds=12):
        log = self.out / f'{cls_name}.jsonl'
        p = self.spawn([sys.executable, str(PROBE), '--class', cls_name, '--seconds', str(seconds), '--log', str(log), *args])
        c = self.wait_for(lambda c: c['class'] == cls_name)
        self.assertIsNotNone(c, f'{cls_name} never mapped')
        return p, log

    def settled(self, pred, secs=1.6):
        time.sleep(secs)
        return self.wait_for(pred, 3)

    def policy(self):
        g = self.geometry['geometry']
        return (g['x'], g['y'], g['w'], g['h'])

    def assert_centred(self, c, tol=3):
        px, py, pw, ph = self.policy()
        cx, cy = c['at'][0] + c['size'][0] / 2, c['at'][1] + c['size'][1] / 2
        self.assertLessEqual(abs(cx - (px + pw / 2)), tol, f'{c["class"]} at {c["at"]} {c["size"]}, policy {self.policy()}')
        self.assertLessEqual(abs(cy - (py + ph / 2)), tol, f'{c["class"]} at {c["at"]} {c["size"]}, policy {self.policy()}')

    def assert_inside_output(self, c):
        ox, oy, ow, oh = self.output
        x, y = c['at']
        w, h = c['size']
        self.assertTrue(x >= ox - 1 and y >= oy - 1 and x + w <= ox + ow + 1 and y + h <= oy + oh + 1,
                        f'{c["class"]} {c["at"]} {c["size"]} leaves the output {self.output}')

    def shot(self, name):
        p = self.out / f'{name}.png'
        subprocess.run(['grim', str(p)], env=self.env, capture_output=True, timeout=20)
        return p

    # -------------------------------------------------------------------------------- scenarios

    def test_00_nested_session_has_xwayland_and_rules(self):
        self.assertTrue(self.x11, 'nested session publishes no x11_display: X11 games cannot run in it')
        self.assertTrue(self.rules, f'no placement rules in {LUA}')
        for n, r in self.rule_results.items():
            self.assertEqual(r, 'ok', f'{n}: {r}')

    def require_x11(self):
        if not self.x11:
            self.fail('no nested XWayland')

    def test_10_fixed_size_game_offscreen_request_is_centred_at_its_own_size(self):
        self.require_x11()
        ox, oy, ow, oh = self.output
        p, _ = self.probe('steam_app_4101', '--fixed', '--size', '480x320', '--pos', f'{int(ow) - 100},{int(oh) - 80}')
        c = self.settled(lambda c: c['class'] == 'steam_app_4101')
        self.assertEqual(c['size'], [480, 320], 'fixed-size game must keep its size')
        self.assert_inside_output(c)
        self.assert_centred(c)
        self.stop(p)

    def test_11_fixed_size_game_larger_than_the_output_is_fit(self):
        self.require_x11()
        ox, oy, ow, oh = self.output
        p, log = self.probe('steam_app_4102', '--fixed', '--wine-hints', '--size', f'{int(ow) + 600}x{int(oh) + 200}', '--pos', '0,0')
        c = self.settled(lambda c: c['class'] == 'steam_app_4102')
        px, py, pw, ph = self.policy()
        self.assertEqual((c['size'][0], c['size'][1]), (pw, ph), 'oversize = fit: the opening size')
        self.assert_centred(c)
        time.sleep(0.5)
        conf = [json.loads(l) for l in open(log) if '"configure"' in l]
        self.assertEqual((conf[-1]['w'], conf[-1]['h']), tuple(c['size']), 'client acknowledged the compositor size')
        self.stop(p)

    def test_12_wine_style_root_remap_is_refused_and_acknowledged(self):
        self.require_x11()
        p, log = self.probe('steam_app_4103', '--wine-hints', '--borderless', '--size', '480x320', '--remap-root', '900')
        time.sleep(2.6)
        c = self.wait_for(lambda c: c['class'] == 'steam_app_4103', 3)
        events = [json.loads(l) for l in open(log)]
        self.assertTrue(any(e['event'] == 'request' for e in events), 'probe issued its root-size request')
        self.assertEqual(c['size'], [480, 320], f'remap to the X root was honoured: {c["at"]} {c["size"]}')
        self.assert_centred(c)
        last = [e for e in events if e['event'] == 'configure'][-1]
        self.assertEqual((last['w'], last['h']), (480, 320), 'refusal answered with the real geometry')
        self.stop(p)

    def test_13_resizable_game_takes_the_opening_size(self):
        self.require_x11()
        p, _ = self.probe('steam_app_4104', '--size', '1600x1000', '--pos', '0,0')
        c = self.settled(lambda c: c['class'] == 'steam_app_4104')
        self.assertEqual(tuple(c['at'] + c['size']), self.policy())
        self.stop(p)

    def test_14_non_game_fixed_window_is_left_alone(self):
        # Scope control: only game classes are touched (dialogs and ordinary fixed windows keep their request).
        self.require_x11()
        ox, oy, ow, oh = self.output
        pos = (int(ow) - 300, int(oh) - 200)
        p, _ = self.probe('mios-not-a-game', '--fixed', '--size', '480x320', '--pos', f'{pos[0]},{pos[1]}')
        c = self.settled(lambda c: c['class'] == 'mios-not-a-game')
        self.assertEqual(tuple(c['at']), pos)
        self.stop(p)

    def test_20_toggle_never_resizes_a_fixed_game(self):
        # Super+V (miosd placement toggle) on a fixed-size game that never re-renders at another size.
        self.require_x11()
        p, _ = self.probe('steam_app_4105', '--fixed', '--no-adapt', '--size', '480x320', '--color', 'e04040')
        c = self.settled(lambda c: c['class'] == 'steam_app_4105')
        before = black_fraction(self.shot('toggle-before'), c['at'] + c['size'], self.scale)
        t = subprocess.run([MIOSD, 'placement', 'toggle', '--window', c['address']], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(t.returncode, 0, t.stderr)
        c = self.settled(lambda c: c['class'] == 'steam_app_4105')
        after = black_fraction(self.shot('toggle-after'), c['at'] + c['size'], self.scale)
        (self.out / 'toggle.json').write_text(json.dumps({'before': before, 'after': after, 'client': c}))
        self.assertEqual(c['size'], [480, 320], f'toggle resized a fixed-size game to {c["size"]}')
        self.assertLess(after, 0.05, f'black area after toggle: {after}')
        self.stop(p)

    def wine(self, exe, cls_name):
        prefix = os.environ.get('MIOS_LIVE_WINEPREFIX')
        if not (prefix and shutil.which('wine') and self.x11):
            self.skipTest('MIOS_LIVE_WINEPREFIX / wine / nested XWayland unavailable')
        env = dict(self.env, WINEPREFIX=prefix, WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=')
        self.spawn(['wine', exe], env=env, log=self.out / f'wine-{exe}.log')
        c = self.wait_for(lambda c: c['class'].lower() == cls_name, 40)
        self.assertIsNotNone(c, f'{exe} never mapped')
        return self.settled(lambda c: c['class'].lower() == cls_name, 2.5)

    def test_30_wine_fixed_size_window_is_centred_at_its_own_size(self):
        c = self.wine('winemine', 'winemine.exe')
        self.assertIn('mios-place-game-fixed', [t.rstrip('*') for t in c['tags']], c['tags'])
        self.assertNotIn('mios-place-game', [t.rstrip('*') for t in c['tags']], 'winemine declares a fixed size')
        self.assert_inside_output(c)
        self.assert_centred(c)

    def test_31_wine_resizable_window_takes_the_opening_size(self):
        c = self.wine('notepad', 'notepad.exe')
        self.assertIn('mios-place-game', [t.rstrip('*') for t in c['tags']], c['tags'])
        self.assertEqual(tuple(c['at'] + c['size']), self.policy())

    def vk_resize(self, wsi):
        if wsi == 'xcb':
            self.require_x11()
        if not shutil.which('vkcube'):
            self.skipTest('vkcube missing')
        p = self.spawn(['vkcube', '--wsi', wsi], log=self.out / f'vkcube-{wsi}.log')
        c = self.wait_for(lambda c: c['title'].lower().startswith('vkcube'), 20)
        self.assertIsNotNone(c, f'vkcube {wsi} never mapped')
        time.sleep(1.0)
        rows = []
        for i, (w, h) in enumerate([(520, 360), (760, 520), (430, 300)]):
            self.ctl('eval', f"local w='address:{c['address']}'; hl.dispatch(hl.dsp.window.float({{action='enable', window=w}})); hl.dispatch(hl.dsp.window.resize({{x={w}, y={h}, window=w}}))")
            time.sleep(1.2)
            c2 = self.wait_for(lambda x: x['address'] == c['address'], 3)
            frac = black_fraction(self.shot(f'vkcube-{wsi}-{i}'), c2['at'] + c2['size'], self.scale)
            rows.append({'requested': [w, h], 'size': c2['size'], 'black': frac})
        (self.out / f'vkcube-{wsi}.json').write_text(json.dumps(rows))
        self.stop(p)
        for r in rows:
            self.assertEqual(r['size'], r['requested'], f'{wsi}: configure not applied {rows}')
            self.assertLess(r['black'], 0.05, f'{wsi}: black after resize {rows}')

    def test_40_vulkan_xcb_swapchain_follows_resizes(self):
        self.vk_resize('xcb')

    def test_41_vulkan_wayland_swapchain_follows_resizes(self):
        self.vk_resize('wayland')


if __name__ == '__main__':
    unittest.main(verbosity=2)
