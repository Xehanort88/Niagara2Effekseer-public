"""Gradient-map material and plan checks. All scratch files go under --work.

python tools/gradientmap/test_gradientmap.py --work out/tests/gradientmap [--tool <Niagara2Efk.dll>]

The plan test builds its fixture with the tool's own `sprite` and `edit` commands
(no pack content) and is skipped when the DLL is not built.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
import unittest
from PIL import Image
import gradientmap as g

WORK = None
TOOL = None


def ramp(path):
    im = Image.new('RGBA', (256, 1))
    for x in range(256):
        im.putpixel((x, 0), (x, 0, 255 - x, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def grey(path, value=128):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (8, 8), (value, value, value, 255)).save(path)
    return path


def run_tool(*args):
    r = subprocess.run(['dotnet', str(TOOL)] + [str(a) for a in args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError('%s failed (%d): %s%s' % (args[0], r.returncode, r.stdout[-1500:], r.stderr[-1500:]))
    return r.stdout


class Material(unittest.TestCase):
    def test_round_trip(self):
        out = WORK / 'material'
        path = g.write_material(out, 'GM-a-b', 'b.png', 'a.png', lift=False, wrap='repeat')
        back = g.parse_material(path.read_bytes())
        self.assertEqual([t['path'] for t in back['textures']], ['../Texture/b.png', '../Texture/a.png'])
        self.assertEqual([t['colorType'] for t in back['textures']], ['value', 'color'])
        self.assertEqual([t['wrap'] for t in back['textures']], ['repeat', 'clamp'])
        self.assertFalse(back['lift'])
        self.assertNotIn('sqrt', back['shader'])

    def test_lift_and_clamp(self):
        back = g.parse_material(g.write_material(WORK / 'material', 'GM-lift', 'b.png', 'a.png', True, 'clamp').read_bytes())
        self.assertTrue(back['lift'])
        self.assertEqual(back['textures'][0]['wrap'], 'clamp')
        self.assertIn('gmTone = sqrt(gmTone);', back['shader'])

    def test_unlit_header(self):
        data = g.material_bytes('x', '../Texture/b.png', '../Texture/a.png')
        self.assertEqual(data[:4], b'EFKM')
        self.assertEqual(data[16:20], b'PRM_')
        self.assertEqual(int.from_bytes(data[24:28], 'little'), 1)  # shading model Unlit

    def test_truncated_refused(self):
        data = g.material_bytes('x', '../Texture/b.png', '../Texture/a.png')
        with self.assertRaisesRegex(g.GradientMapError, 'Truncated|Invalid'):
            g.parse_material(data[:-3])

    def test_repeat_ramp_refused(self):
        data = bytearray(g.material_bytes('x', '../Texture/b.png', '../Texture/a.png'))
        # The ramp's wrap is the last integer before the three trailing zero counts.
        prm_end = 16 + 8 + int.from_bytes(data[20:24], 'little')
        data[prm_end - 16:prm_end - 12] = (0).to_bytes(4, 'little')
        with self.assertRaisesRegex(g.GradientMapError, 'Ramp must clamp'):
            g.parse_material(bytes(data))

    def test_texture_clash_refused(self):
        out = WORK / 'clash'
        g.copy_texture(grey(WORK / 'clash-a' / 'same.png', 10), out)
        g.copy_texture(grey(WORK / 'clash-a' / 'same.png', 10), out)  # identical bytes: fine
        with self.assertRaisesRegex(g.GradientMapError, 'clash'):
            g.copy_texture(grey(WORK / 'clash-b' / 'same.png', 200), out)

    def test_cli_material(self):
        args = g.parser().parse_args(['material', '--base', str(grey(WORK / 'cli' / 'smoke.png')),
                                      '--ramp', str(ramp(WORK / 'cli' / 'Ramp-test.png')),
                                      '--out', str(WORK / 'cli-out'), '--lift'])
        path = Path(g.run(args)['material'])
        self.assertEqual(path.name, 'GM-Ramp-test-lift-smoke.efkmat')
        self.assertTrue((WORK / 'cli-out' / 'Texture' / 'smoke.png').is_file())
        self.assertTrue((WORK / 'cli-out' / 'Texture' / 'Ramp-test.png').is_file())


class Colours(unittest.TestCase):
    def kv(self, **extra):
        kv = {'DrawingValues/Type': 'Sprite', 'IsRendered': 'true', 'RendererCommonValues/Material': 'Default',
              'DrawingValues/ColorAll/Type': 'Fixed', 'DrawingValues/ColorAll/Fixed': 'rgba(255, 255, 255, 255)'}
        kv.update(extra)
        return kv

    def test_colour_max_range(self):
        self.assertEqual(g.colour_max('rgba([73..128], 0, [84..140], 255)'), 140)
        self.assertEqual(g.colour_max('rgba(0, 0, 0, 255)'), 0)
        self.assertIsNone(g.colour_max('(none)'))

    def test_black_kept(self):
        node = {'kv': self.kv(**{'DrawingValues/ColorAll/Fixed': 'rgba(0, 0, 0, 255)'})}
        self.assertEqual(g.classify(node, 24)[0], 'keep')

    def test_easing_uses_brightest_end(self):
        node = {'kv': self.kv(**{'DrawingValues/ColorAll/Type': 'Easing',
                                 'DrawingValues/ColorAll/Easing/Start': 'rgba(0, 0, 0, 255)',
                                 'DrawingValues/ColorAll/Easing/End': 'rgba(0, 90, 0, 255)'})}
        self.assertEqual(g.classify(node, 24)[0], 'convert')

    def test_ring_multiplies(self):
        kv = self.kv(**{'DrawingValues/Type': 'Ring'})
        for part in ('Outer', 'Center', 'Inner'):
            kv['DrawingValues/Ring/%sColor' % part] = 'Fixed'
            kv['DrawingValues/Ring/%sColor_Fixed' % part] = 'rgba(0, 0, 0, 255)'
        self.assertEqual(g.node_brightness(kv), 0)
        kv['DrawingValues/Ring/CenterColor_Fixed'] = 'rgba(255, 214, 196, 255)'
        self.assertEqual(g.node_brightness(kv), 255)

    def test_gradient_unread_converts(self):
        node = {'kv': self.kv(**{'DrawingValues/ColorAll/Type': 'Gradient'})}
        verdict, why = g.classify(node, 24)
        self.assertEqual(verdict, 'convert')
        self.assertIn('not read', why)

    def test_advanced_refused(self):
        node = {'kv': self.kv(**{'AdvancedRendererCommonValuesValues/AlphaTextureParam/Enabled': 'true'})}
        verdict, why = g.classify(node, 24)
        self.assertEqual(verdict, 'refused')
        self.assertIn('AlphaTextureParam', why)

    def test_file_material_refused(self):
        self.assertEqual(g.classify({'kv': self.kv(**{'RendererCommonValues/Material': 'File'})}, 24)[0], 'refused')

    def test_hidden_skipped(self):
        self.assertEqual(g.classify({'kv': self.kv(IsRendered='false')}, 24)[0], 'skip')


class Plan(unittest.TestCase):
    def setUp(self):
        if TOOL is None or not Path(TOOL).is_file():
            self.skipTest('Niagara2Efk.dll not built (pass --tool)')

    def test_plan_and_edit(self):
        d = WORK / 'plan'
        tex = grey(d / 'src' / 'Texture' / 'smoke.png')
        source = d / 'src' / 'sprite.efkefc'
        run_tool('sprite', source, tex)
        # Three siblings: white (convert), black (keep), alpha texture on (refused).
        spec = {'source': str(source), 'edits': [
            {'node': '0', 'set': {'DrawingValues/ColorAll/Type': 'Fixed', 'DrawingValues/ColorAll/Fixed': [255, 255, 255, 255]}},
            {'node': '', 'add': {'from': '0', 'name': 'Black'},
             'set': {'DrawingValues/ColorAll/Type': 'Fixed', 'DrawingValues/ColorAll/Fixed': [0, 0, 0, 255]}},
            {'node': '', 'add': {'from': '0', 'name': 'Masked'},
             'set': {'AdvancedRendererCommonValuesValues/AlphaTextureParam/Enabled': True}}]}
        spec_path = d / 'fixture.edit.json'
        spec_path.write_text(json.dumps(spec), encoding='utf-8')
        fixture = d / 'fixture' / 'fixture.efkefc'
        run_tool('edit', spec_path, fixture)

        args = g.parser().parse_args(['plan', str(fixture), '--ramp', str(ramp(d / 'Ramp-test.png')),
                                      '--out', str(d / 'out'), '--tool', str(TOOL)])
        report = g.run(args)
        verdicts = {r['name']: r['verdict'] for r in report['nodes']}
        self.assertEqual(verdicts.get('Black'), 'keep')
        self.assertEqual(verdicts.get('Masked'), 'refused')
        self.assertEqual(report['counts']['convert'], 1)
        self.assertTrue((d / 'out' / 'Texture' / 'smoke.png').is_file())

        out = d / 'result' / 'result.efkefc'
        log = run_tool('edit', report['spec'], out)
        self.assertIn('VERIFY OK', log)
        self.assertTrue((d / 'result' / 'Materials' / 'GM-Ramp-test-smoke.efkmat').is_file())
        self.assertTrue((d / 'result' / 'Texture' / 'Ramp-test.png').is_file())
        dump = run_tool('dump', out, '0')
        self.assertIn('RendererCommonValues/Material = File', dump)
        self.assertIn('RendererCommonValues/MaterialFile/Path = GM-Ramp-test-smoke.efkmat', dump)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--tool', type=Path, default=g.DEFAULT_TOOL)
    args = p.parse_args()
    WORK = args.work.resolve()
    WORK.mkdir(parents=True, exist_ok=True)
    TOOL = args.tool.resolve()
    suite = unittest.TestSuite()
    for case in (Material, Colours, Plan):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'skipped': len(result.skipped), 'passed': result.wasSuccessful()}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
