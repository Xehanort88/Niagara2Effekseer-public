"""FBX and asset inspection checks on small synthetic fixtures. All files go under --work.

python tools/assetinspect/test_assetinspect.py --work out/tests/assetinspect

The binary FBX fixtures are written by the encoder below (both record layouts,
raw and zlib arrays); no third-party asset is used. Pillow is required.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
sys.dont_write_bytecode = True
import unittest
import zlib
from contextlib import redirect_stdout, redirect_stderr
from PIL import Image
import asset_sheet as a
import fbx_inspect as f

WORK = None
TICKS = f.KTIME_PER_SECOND


# --- binary FBX encoder -------------------------------------------------------

def enc_prop(p, compress):
    t, v = p
    if t == 'raw':                     # pre-encoded property bytes, for corrupt fixtures
        return v
    if t in f.SCALAR:
        return t.encode() + struct.pack(f.SCALAR[t], v)
    if t == 'S':
        b = v.encode('utf-8')
        return b'S' + struct.pack('<I', len(b)) + b
    if t == 'R':
        return b'R' + struct.pack('<I', len(v)) + v
    fmt, _ = f.ARRAY[t]
    raw = struct.pack('<%d%s' % (len(v), fmt[1]), *v)
    if compress:
        data = zlib.compress(raw)
        return t.encode() + struct.pack('<III', len(v), 1, len(data)) + data
    return t.encode() + struct.pack('<III', len(v), 0, len(raw)) + raw


def enc_node(n, pos, wide, compress):
    """n = (name, [props], [children]). Returns bytes placed at absolute offset pos."""
    name, props, children = n
    rec = 25 if wide else 13
    pdata = b''.join(enc_prop(p, compress) for p in props)
    body = name.encode() + pdata
    cpos = pos + rec + len(body)
    kids = b''
    for c in children:
        kids += enc_node(c, cpos + len(kids), wide, compress)
    if children:
        kids += b'\0' * rec
    end = pos + rec + len(body) + len(kids)
    hdr = struct.pack('<QQQ' if wide else '<III', end, len(props), len(pdata)) + bytes([len(name.encode())])
    return hdr + body + kids


def build_fbx(nodes, version=7400, compress=False):
    wide = version >= 7500
    out = f.MAGIC + b'\0' + struct.pack('<I', version)
    for n in nodes:
        out += enc_node(n, len(out), wide, compress)
    return out + b'\0' * (25 if wide else 13) + b'\0' * 16


def S(s):
    return ('S', s)


def L(i):
    return ('L', i)


def P(name, typ, *values):
    props = [S(name), S(typ), S(''), S('A')]
    for v in values:
        props.append(('D', v) if isinstance(v, float) else ('I', v) if isinstance(v, int) and abs(v) < 2 ** 31 else
                     ('L', v) if isinstance(v, int) else S(v))
    return ('P', props, [])


def obj(record, oid, name, cls, sub, children=()):
    return (record, [L(oid), S(name + '\x00\x01' + cls), S(sub)], list(children))


def C(typ, src, dst, prop=None):
    return ('C', [S(typ), L(src), L(dst)] + ([S(prop)] if prop else []), [])


def scene(dangling=True, objects=True, connections=True, curve_times=True):
    times = [0, TICKS // 2, TICKS]
    curve = [('Default', [('D', 1.0)], []), ('KeyVer', [('I', 4009)], [])]
    if curve_times:
        curve.append(('KeyTime', [('l', times)], []))
    curve += [('KeyValueFloat', [('f', [1.0, 2.0, 0.5])], []),
              ('KeyAttrFlags', [('i', [0x8 | 0x100, 0x2])], []),
              ('KeyAttrDataFloat', [('f', [0.0] * 8)], []),
              ('KeyAttrRefCount', [('i', [2, 1])], [])]
    objs = [
        obj('Model', 10, 'Root', 'Model', 'Null', [('Properties70', [], [P('Lcl Translation', 'Lcl Translation', 1.0, 2.0, 3.0)])]),
        obj('Model', 11, 'Child', 'Model', 'Mesh'),
        obj('Geometry', 20, 'ChildGeo', 'Geometry', 'Mesh', [
            ('Vertices', [('d', [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0])], []),
            ('PolygonVertexIndex', [('i', [0, 1, -3, 1, 3, -3])], []),
            ('LayerElementUV', [('I', 0)], [('Name', [S('map1')], []), ('MappingInformationType', [S('ByPolygonVertex')], []),
                                            ('ReferenceInformationType', [S('IndexToDirect')], []),
                                            ('UV', [('d', [0.0] * 8)], []), ('UVIndex', [('i', [0, 1, 2, 1, 3, 2])], [])])]),
        obj('Material', 30, 'Mat', 'Material', '', [('ShadingModel', [S('phong')], []),
                                                     ('Properties70', [], [P('DiffuseColor', 'Color', 1.0, 0.5, 0.25)])]),
        obj('Texture', 40, 'Tex', 'Texture', '', [('FileName', [S('C:/tex/a.png')], []), ('RelativeFilename', [S('a.png')], [])]),
        obj('AnimationStack', 50, 'Take', 'AnimStack', '', [('Properties70', [], [P('LocalStart', 'KTime', 0),
                                                                                  P('LocalStop', 'KTime', TICKS)])]),
        obj('AnimationLayer', 51, 'Base', 'AnimLayer', ''),
        obj('AnimationCurveNode', 52, 'S', 'AnimCurveNode', '', [('Properties70', [], [P('d|X', 'Number', 1.0)])]),
        ('AnimationCurve', [L(53), S('\x00\x01AnimCurve'), S('')], curve),
    ]
    conns = [C('OO', 10, 0), C('OO', 11, 10), C('OO', 20, 11), C('OO', 30, 11), C('OP', 40, 30, 'DiffuseColor'),
             C('OO', 51, 50), C('OO', 52, 51), C('OP', 52, 11, 'Lcl Scaling'), C('OP', 53, 52, 'd|X')]
    if dangling:
        conns.append(C('OP', 999, 52, 'd|Y'))
    nodes = [('FBXHeaderExtension', [], [('FBXVersion', [('I', 7400)], [])]),
             ('GlobalSettings', [], [('Properties70', [], [P('TimeMode', 'enum', 6), P('UnitScaleFactor', 'double', 1.0)])]),
             ('Definitions', [], [('ObjectType', [S('Model')], [('Count', [('I', 2)], [])]),
                                  ('ObjectType', [S('Geometry')], [('Count', [('I', 1)], [])])])]
    if objects:
        nodes.append(('Objects', [], objs))
    if connections:
        nodes.append(('Connections', [], conns))
    return nodes


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def run_cli(module, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.run([str(x) for x in argv])
    return code, out.getvalue(), err.getvalue()


def fresh(name):
    d = WORK / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


# --- FBX ----------------------------------------------------------------------

class FbxParse(unittest.TestCase):
    def analyse(self, data):
        parsed = f.parse_fbx(data)
        res, diags, complete = f.analyse(parsed)
        return parsed, res, diags, complete

    def test_layouts_and_compression_agree(self):
        results = []
        for version in (7400, 7500):
            for compress in (False, True):
                parsed, res, diags, complete = self.analyse(build_fbx(scene(), version, compress))
                self.assertEqual(parsed['version'], version)
                self.assertIn('64-bit' if version >= 7500 else '32-bit', parsed['layout'])
                self.assertTrue(complete)
                res = json.loads(json.dumps(res, default=f.jsonable))
                results.append(res)
        for r in results[1:]:
            self.assertEqual(r['object_counts'], results[0]['object_counts'])
            self.assertEqual(r['animation'], results[0]['animation'])
            self.assertEqual(r['geometry'], results[0]['geometry'])

    def test_inventory_content(self):
        _, res, diags, _ = self.analyse(build_fbx(scene()))
        self.assertEqual(res['object_counts']['Model'], 2)
        self.assertEqual(res['presence']['Geometry'], {'status': 'present', 'count': 1})
        self.assertEqual(res['presence']['Video'], {'status': 'absent', 'count': 0})
        root = res['hierarchy']['roots'][0]
        self.assertEqual((root['name'], root['children'][0]['name']), ('Root', 'Child'))
        self.assertEqual(root['stored_transform']['Lcl Translation'], [1.0, 2.0, 3.0])
        self.assertEqual({x['record'] for x in root['children'][0]['attached']}, {'Geometry', 'Material'})
        g = res['geometry'][0]
        self.assertEqual((g['control_points'], g['polygons']), (4, 2))
        self.assertEqual(g['layer_elements'][0]['arrays'], {'UV': 8, 'UVIndex': 6})
        m = res['materials'][0]
        self.assertEqual(m['shading_model'], 'phong')
        self.assertEqual(m['textures'][0]['name'], 'Tex')
        self.assertEqual(m['textures'][0]['property'], 'DiffuseColor')
        curve = res['animation']['curves'][0]
        self.assertEqual(curve['times_seconds'], [0.0, 0.5, 1.0])
        self.assertEqual(curve['times_frames'], [0.0, 15.0, 30.0])   # TimeMode 6 = 30 fps
        self.assertEqual(curve['values'], [1.0, 2.0, 0.5])
        self.assertEqual(curve['interpolation'], ['cubic', 'cubic', 'constant'])
        cn = curve['curve_nodes'][0]
        self.assertEqual((cn['name'], cn['channel'], cn['targets'][0]['name'], cn['targets'][0]['property']),
                         ('S', 'd|X', 'Child', 'Lcl Scaling'))
        self.assertEqual(cn['stacks'], ['Take'])
        self.assertEqual(res['animation']['stacks'][0]['LocalStop']['seconds'], 1.0)
        self.assertEqual(len(res['dangling_connections']), 1)
        d = res['dangling_connections'][0]
        self.assertEqual((d['missing'], d['property'], d['destination']['name']), (['source'], 'd|Y', 'S'))

    def test_absent_vs_not_inspected(self):
        _, res, diags, complete = self.analyse(build_fbx(scene(objects=False)))
        self.assertFalse(complete)
        self.assertEqual(res['presence']['Material']['status'], 'not_inspected')
        self.assertIsNone(res['object_counts'])
        self.assertTrue(any('no Objects record' in d for d in diags))
        empty = [n if n[0] != 'Objects' else ('Objects', [], []) for n in scene()]
        _, res, _, _ = self.analyse(build_fbx(empty))
        self.assertEqual(res['presence']['Material'], {'status': 'absent', 'count': 0})

    def test_missing_connections_and_keys(self):
        _, res, diags, complete = self.analyse(build_fbx(scene(connections=False)))
        self.assertFalse(complete)
        self.assertIsNone(res['hierarchy'])
        self.assertIsNone(res['dangling_connections'])
        _, res, diags, complete = self.analyse(build_fbx(scene(curve_times=False)))
        self.assertFalse(complete)
        self.assertIn('no KeyTime array', res['animation']['curves'][0]['problems'])

    def test_no_frame_rate_without_global_settings(self):
        nodes = [n for n in scene() if n[0] != 'GlobalSettings']
        _, res, diags, _ = self.analyse(build_fbx(nodes))
        self.assertIsNone(res['animation']['curves'][0]['times_frames'])
        self.assertTrue(any('frame rate unknown' in d for d in diags))

    def assertFbxError(self, data, kind, pattern):
        with self.assertRaises(f.FbxError) as cm:
            f.parse_fbx(data)
        self.assertEqual(cm.exception.kind, kind)
        self.assertRegex(str(cm.exception), pattern)

    def test_ascii_reported_unsupported(self):
        self.assertFbxError(b'; FBX 7.4.0 project file\n; ----\nFBXHeaderExtension:  {\n}\n', 'unsupported', 'ASCII FBX')

    def test_header_checks(self):
        self.assertFbxError(b'not an fbx at all, just some bytes here', 'malformed', 'magic')
        self.assertFbxError(f.MAGIC[:10], 'malformed', 'shorter than')
        self.assertFbxError(f.MAGIC + b'\0\x40', 'malformed', 'truncated binary FBX header')
        self.assertFbxError(f.MAGIC + b'\x01' + struct.pack('<I', 7400) + b'\0' * 13, 'unsupported', 'big-endian')
        self.assertFbxError(f.MAGIC + b'\0' + struct.pack('<I', 7400) + b'\0' * 13, 'malformed', 'no records')

    def test_truncation_and_bounds(self):
        data = build_fbx(scene())
        self.assertFbxError(data[:len(data) // 2], 'malformed', 'record at byte|needs|truncated')
        bad = bytearray(build_fbx([('Top', [('I', 1)], [])]))
        struct.pack_into('<I', bad, 27, len(bad) + 100)        # end offset past end of file
        self.assertFbxError(bytes(bad), 'malformed', 'claims to end')
        bad = bytearray(build_fbx([('Top', [S('abc')], [])]))
        struct.pack_into('<I', bad, 27 + 13 + 3 + 1, 1000)   # string length past the property list
        self.assertFbxError(bytes(bad), 'malformed', 'string data')

    def test_array_checks(self):
        good = build_fbx([('A', [('i', [1, 2, 3])], [])], compress=True)
        self.assertEqual(f.parse_fbx(good)['nodes'][0]['props'][0], ('i', [1, 2, 3]))
        hdr = 27 + 13 + 1 + 1                                   # record header, name 'A', type byte
        bad = bytearray(good)
        struct.pack_into('<I', bad, hdr + 4, 2)                  # unknown encoding
        self.assertFbxError(bytes(bad), 'malformed', 'unknown array encoding 2')
        bad = bytearray(good)
        struct.pack_into('<I', bad, hdr, 4)                      # count says 4, data decodes to 3
        self.assertFbxError(bytes(bad), 'malformed', 'decodes to 12 bytes, 16 expected')
        bad = bytearray(good)
        struct.pack_into('<I', bad, hdr, 2)                      # data decodes to more than declared
        self.assertFbxError(bytes(bad), 'malformed', 'more than 8')
        bad = bytearray(good)
        bad[hdr + 12 + 2] ^= 0xFF                                # corrupt the deflate stream
        self.assertFbxError(bytes(bad), 'malformed', 'zlib|decodes')
        z = zlib.compress(struct.pack('<3i', 1, 2, 3))[:-6]     # deflate stream cut short
        cut = build_fbx([('A', [('raw', b'i' + struct.pack('<III', 3, 1, len(z)) + z)], [])])
        self.assertFbxError(cut, 'malformed', 'compressed stream is truncated')
        raw = bytearray(build_fbx([('A', [('i', [1, 2, 3])], [])]))
        struct.pack_into('<I', raw, hdr, 2)
        self.assertFbxError(bytes(raw), 'malformed', '2 elements need 8 bytes, record stores 12')

    def test_old_object_layout_unsupported(self):
        parsed = f.parse_fbx(build_fbx(scene(), version=6100))
        with self.assertRaisesRegex(f.FbxError, '6.x object layout'):
            f.analyse(parsed)


class FbxCli(unittest.TestCase):
    def test_run_report_and_exit_codes(self):
        d = fresh('fbx-cli')
        src = d / 'src'
        good = write(src / 'a' / 'scene.fbx', build_fbx(scene(), 7500, True))
        write(src / 'b' / 'scene.fbx', build_fbx(scene(), 7400))            # same file name, other folder
        before = hashlib.sha256(good.read_bytes()).hexdigest()
        code, out, err = run_cli(f, src, '--out', d / 'out', '--trees')
        self.assertEqual(code, 0, out + err)
        self.assertEqual(hashlib.sha256(good.read_bytes()).hexdigest(), before)
        trees = sorted(p.name for p in (d / 'out' / 'trees').iterdir())
        self.assertEqual(len(trees), 2)
        self.assertEqual(len(set(t.lower() for t in trees)), 2)
        inv = json.loads((d / 'out' / 'inventory.json').read_text(encoding='utf-8'))
        self.assertEqual([r['status'] for r in inv['files']], ['ok', 'ok'])
        report = (d / 'out' / 'REPORT.md').read_text(encoding='utf-8')
        self.assertIn('Model "Child" / Lcl Scaling / curve node "S" / d|X: 3 key(s)', report)
        self.assertIn("1 x OP connection, missing source, property 'd|Y'", report)
        self.assertIn('Files whose animation curves are identical', report)
        self.assertIn('scene.fbx, scene.fbx', report)

        # Refuses to write into a non-empty folder, and inside a source folder.
        code, _, err = run_cli(f, src, '--out', d / 'out')
        self.assertEqual(code, 2)
        self.assertIn('not empty', err)
        code, _, err = run_cli(f, src, '--out', src / 'report')
        self.assertEqual(code, 2)
        self.assertIn('inside the source folder', err)
        self.assertFalse((src / 'report').exists())

    def test_bad_inputs(self):
        d = fresh('fbx-bad')
        (d / 'empty').mkdir()
        code, _, err = run_cli(f, d / 'empty', '--out', d / 'o1')
        self.assertEqual(code, 2)
        self.assertIn('nothing to inspect', err)
        self.assertFalse((d / 'o1').exists())
        code, _, err = run_cli(f, d / 'missing', '--out', d / 'o1')
        self.assertEqual(code, 2)
        src = d / 'src'
        write(src / 'ok.fbx', build_fbx(scene()))
        write(src / 'ascii.fbx', b'; FBX 7.4.0 project file\nFBXHeaderExtension: {\n}\n')
        write(src / 'trunc.fbx', build_fbx(scene())[:300])
        write(src / 'zero.fbx', b'')
        noobj = write(d / 'noobj.fbx', build_fbx(scene(objects=False)))
        other = write(d / 'notes.txt', b'hello')
        code, out, err = run_cli(f, src, noobj, other, '--out', d / 'o2')
        self.assertEqual(code, 1, out + err)
        inv = json.loads((d / 'o2' / 'inventory.json').read_text(encoding='utf-8'))
        status = {r['file']: r['status'] for r in inv['files']}
        self.assertEqual(status, {'ok.fbx': 'ok', 'ascii.fbx': 'unsupported', 'trunc.fbx': 'malformed',
                                  'zero.fbx': 'malformed', 'noobj.fbx': 'incomplete', 'notes.txt': 'unsupported'})
        report = (d / 'o2' / 'REPORT.md').read_text(encoding='utf-8')
        self.assertIn('ASCII FBX is not supported', report)
        self.assertIn('| noobj.fbx | incomplete | 7400 | not inspected |', report)
        self.assertIn('- Geometry: not inspected (no Objects record)', report)

    def test_nan_stays_strict_json(self):
        d = fresh('fbx-nan')
        nodes = scene()
        objs = next(n for n in nodes if n[0] == 'Objects')[2]
        curve = objs[-1][2]
        curve[[c[0] for c in curve].index('KeyValueFloat')] = ('KeyValueFloat', [('f', [1.0, float('nan'), 0.5])], [])
        src = write(d / 'src' / 'nan.fbx', build_fbx(nodes))
        code, out, err = run_cli(f, src, '--out', d / 'out')
        self.assertEqual(code, 0, out + err)
        text = (d / 'out' / 'inventory.json').read_text(encoding='utf-8')
        self.assertNotIn('NaN,', text.replace('"NaN"', ''))
        self.assertEqual(json.loads(text)['files'][0]['animation']['curves'][0]['values'][1], 'NaN')


# --- OBJ ----------------------------------------------------------------------

class Obj(unittest.TestCase):
    def parse(self, text):
        return a.parse_obj(text.encode('utf-8'))

    def test_negative_indices_are_relative(self):
        m = self.parse('v 0 0 0\nv 1 0 0\nv 0 1 0\nf -3 -2 -1\nv 5 0 0\nv 6 0 0\nv 5 1 0\nf -3 -2 -1\nf 1 2 -1\n')
        self.assertEqual([[c[0] for c in fc] for fc in m['faces']], [[0, 1, 2], [3, 4, 5], [0, 1, 5]])

    def test_index_errors_name_the_line(self):
        for text, pattern in (('v 0 0 0\nf 1 2 3\n', 'line 2: vertex index 2 but the file defines only 1'),
                              ('v 0 0 0\nv 1 0 0\nv 0 1 0\nf -4 1 2\n', 'line 4: vertex index -4 reaches before'),
                              ('v 0 0 0\nf 0 1 1\n', 'line 2: vertex index 0 is invalid'),
                              ('v 0 0 0\nv 0 0 0\nv 0 0 0\nf 1/5 2 3\n', 'texture coordinate index 5'),
                              ('v 0 x 0\n', 'line 1: vertex has a non-numeric value'),
                              ('v 0 0\n', 'needs at least 3 numbers'),
                              ('v 0 0 0\nf 1 1\n', 'face with 2 vertices')):
            with self.assertRaisesRegex(a.ObjError, pattern):
                self.parse(text)

    def test_forward_reference_and_continuation(self):
        m = self.parse('f 1 2 \\\n 3\nv 0 0 0\nv 1 0 0\nv 0 1 0\n')
        self.assertEqual([c[0] for c in m['faces'][0]], [0, 1, 2])

    def test_attribute_coverage(self):
        base = 'v 0 0 0\nv 1 0 0\nv 0 1 0\n'
        s = a.obj_stats(self.parse(base + 'vn 0 0 1\nf 1//1 2//1 3//1\n'))
        self.assertEqual(s['uv']['status'], 'absent')
        self.assertEqual(s['normal']['status'], 'all faces')
        s = a.obj_stats(self.parse(base + 'vt 0 0\nf 1/1 2/1 3/1\nf 1 2 3\n'))
        self.assertEqual(s['uv']['status'], 'partial')
        self.assertEqual((s['uv']['faces_all_corners'], s['uv']['faces_no_corners']), (1, 1))
        s = a.obj_stats(self.parse(base + 'vt 0 0\nvt 1 1\nf 1 2 3\n'))
        self.assertEqual(s['uv']['status'], 'records present, no face references them')
        self.assertEqual(s['uv_range'], [[0.0, 1.0], [0.0, 1.0]])
        s = a.obj_stats(self.parse(base + 'v 9 9 9\nf 1 2 3 3\n'))
        self.assertEqual(s['unreferenced_vertices'], 1)
        self.assertEqual(s['faces_with_repeated_vertex'], 1)
        self.assertEqual(s['faces_by_corner_count'], {'4': 1})
        self.assertEqual(s['triangles_after_fan'], 2)

    def test_empty_and_unsupported_statements(self):
        s = a.obj_stats(self.parse('# nothing\n'))
        self.assertEqual((s['geometry'], s['vertices'], s['bounds']), ('absent', 0, None))
        m = self.parse('v 0 0 0\ncurv 0 1 1\nvp 0.5\n')
        self.assertEqual(m['unsupported'], {'curv': 1, 'vp': 1})


# --- textures and CLI -----------------------------------------------------------

def save_image(path, im, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, **kw)
    return path


class Textures(unittest.TestCase):
    def test_stats(self):
        d = fresh('tex')
        im = Image.new('RGBA', (2, 2), (10, 20, 30, 0))
        im.putpixel((1, 1), (200, 20, 30, 255))
        rec, disp = a.inspect_texture(save_image(d / 'rgba.png', im))
        self.assertEqual(rec['status'], 'ok')
        self.assertEqual(rec['channels']['R'], {'min': 10, 'max': 200, 'mean': 57.5, 'stddev': 82.272})
        self.assertEqual(rec['alpha'], {'status': 'present', 'transparent_fraction': 0.75, 'opaque_fraction': 0.25})
        self.assertFalse(rec['rgb_identical'])
        rec, _ = a.inspect_texture(save_image(d / 'grey.png', Image.new('RGB', (4, 4), (90, 90, 90))))
        self.assertTrue(rec['rgb_identical'])
        self.assertEqual(rec['alpha'], {'status': 'absent'})
        rec, _ = a.inspect_texture(save_image(d / 'l.png', Image.new('L', (4, 4), 7)))
        self.assertEqual((rec['mode'], rec['rgb_identical'], rec['alpha']['status']), ('L', None, 'absent'))
        p = Image.new('P', (2, 1))
        p.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
        p.putpixel((1, 0), 1)
        rec, _ = a.inspect_texture(save_image(d / 'pal.png', p, transparency=0))
        self.assertEqual(rec['stats_mode'], 'RGBA (converted from palette)')
        self.assertEqual(rec['alpha']['transparent_fraction'], 0.5)
        rec, disp = a.inspect_texture(save_image(d / 'deep.png', Image.new('I;16', (3, 3), 40000)))
        self.assertEqual(rec['status'], 'ok')
        self.assertEqual(rec['channels']['I']['max'], 40000)
        self.assertEqual(disp.mode, 'RGBA')

    def test_bad_images(self):
        d = fresh('tex-bad')
        rec, _ = a.inspect_texture(write(d / 'zero.png', b''))
        self.assertEqual((rec['status'], rec['error']), ('malformed', 'empty file (0 bytes)'))
        rec, _ = a.inspect_texture(write(d / 'junk.png', b'\x89PNG\r\n\x1a\n' + b'garbage' * 10))
        self.assertEqual(rec['status'], 'malformed')
        self.assertIn('Pillow could not decode', rec['error'])

    def test_bitmap_font_draws_any_name(self):
        im = Image.new('RGB', (200, 20))
        from PIL import ImageDraw, ImageFont
        a.draw_text(ImageDraw.Draw(im), (0, 0), 'r\u00fcne-\u706b.png', ImageFont.load_default_imagefont(), 'white')

    def test_font_fallback(self):
        regular, small, desc = a.load_fonts(candidates=('no-such-font-anywhere.ttf',))
        self.assertTrue(desc.startswith('Pillow built-in'))
        self.assertGreater(a.text_width(regular, 'abc'), 0)
        with self.assertRaises(a.UsageError):
            a.load_fonts(explicit=str(WORK / 'missing.ttf'))


class AssetCli(unittest.TestCase):
    def test_run(self):
        d = fresh('asset-cli')
        src = d / 'src'
        for i in range(5):
            save_image(src / 'tex' / ('t%d.png' % i), Image.new('RGBA', (8, 8), (i * 40, 0, 0, 255)))
        save_image(src / 'tex' / 'ramp.png', Image.new('RGB', (256, 1), (0, 0, 255)))
        write(src / 'mesh' / 'tri.obj', b'v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvn 0 0 1\nf -3/1/1 -2/1/1 -1/1/1\n')
        write(src / 'mesh' / 'empty.obj', b'')
        write(src / 'mesh' / 'broken.obj', b'v 0 0 0\nf 1 2 3\n')
        write(src / 'anim.fbx', b'skipped')
        tri_hash = hashlib.sha256((src / 'mesh' / 'tri.obj').read_bytes()).hexdigest()
        code, out, err = run_cli(a, src, '--out', d / 'out', '--per-page', '4', '--view', 'front')
        self.assertEqual(code, 1, out + err)                        # broken.obj could not be inspected
        self.assertEqual(hashlib.sha256((src / 'mesh' / 'tri.obj').read_bytes()).hexdigest(), tri_hash)
        names = sorted(p.name for p in (d / 'out').iterdir())
        self.assertEqual(names, ['REPORT.md', 'assets.json', 'meshes-01.png', 'textures-01.png', 'textures-02.png'])
        inv = json.loads((d / 'out' / 'assets.json').read_text(encoding='utf-8'))
        meshes = {m['file']: m for m in inv['meshes']}
        self.assertEqual(meshes['broken.obj']['status'], 'malformed')
        self.assertIn('line 2', meshes['broken.obj']['error'])
        self.assertEqual(meshes['empty.obj']['geometry'], 'absent')
        self.assertEqual(meshes['tri.obj']['uv']['status'], 'all faces')
        self.assertEqual(len(inv['skipped']), 1)
        ramp = next(t for t in inv['textures'] if t['file'] == 'ramp.png')
        self.assertIn('stretched', ramp['display_note'])
        report = (d / 'out' / 'REPORT.md').read_text(encoding='utf-8')
        self.assertIn('.fbx x1', report)
        self.assertIn('| broken.obj | malformed: line 2:', report)

        code, _, err = run_cli(a, src, '--out', d / 'out')
        self.assertEqual(code, 2)
        self.assertIn('not empty', err)
        code, _, err = run_cli(a, src, '--out', src / 'mesh' / 'sheets')
        self.assertEqual(code, 2)
        (d / 'none').mkdir()
        write(d / 'none' / 'x.txt', b'')
        code, _, err = run_cli(a, d / 'none', '--out', d / 'o3')
        self.assertEqual(code, 2)
        self.assertIn('nothing to inspect', err)
        code, _, err = run_cli(a, src / 'anim.fbx', '--out', d / 'o4')
        self.assertEqual(code, 1)
        inv = json.loads((d / 'o4' / 'assets.json').read_text(encoding='utf-8'))
        self.assertEqual(inv['unsupported'][0]['status'], 'unsupported')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--work', type=Path, required=True)
    args = p.parse_args()
    WORK = args.work.resolve()
    WORK.mkdir(parents=True, exist_ok=True)
    suite = unittest.TestSuite()
    for case in (FbxParse, FbxCli, Obj, Textures, AssetCli):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'skipped': len(result.skipped), 'passed': result.wasSuccessful()}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
