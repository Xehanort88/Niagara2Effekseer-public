"""Focused format/geometry/map checks. All scratch files go under --work."""
import argparse
import copy
import json
import math
from pathlib import Path
import struct
import sys
sys.dont_write_bytecode = True
import unittest
from PIL import Image
import meshimport as m

WORK = None


def write_glb(path, transform=None, animation=False, mode=4, skin=False, colours=None):
    attributes = {'POSITION':[[0,0,0],[1,0,0],[0,1,0]], 'NORMAL':[[0,0,1]]*3,'TEXCOORD_0':[[0,0],[1,0],[0,1]]}
    if colours: attributes['COLOR_0'] = colours
    binary,views,accessors,indices = bytearray(),[],[],{}
    for name,rows in attributes.items():
        offset = len(binary)
        binary.extend(b''.join(struct.pack('<'+'f'*len(r),*r) for r in rows))
        views.append({'buffer':0,'byteOffset':offset,'byteLength':len(binary)-offset})
        indices[name] = len(accessors)
        accessors.append({'bufferView':len(views)-1,'componentType':5126,'count':len(rows),'type':'VEC'+str(len(rows[0]))})
    nodes = [{'mesh':0}]
    if transform: nodes[0].update(transform)
    if skin:
        nodes[0]['skin'] = 0
        nodes.append({'translation':[2,3,4]})
    j = {'asset':{'version':'2.0'},'buffers':[{'byteLength':len(binary)}],'bufferViews':views,'accessors':accessors,
         'meshes':[{'primitives':[{'attributes':indices,'mode':mode}]}],'nodes':nodes,'scenes':[{'nodes':list(range(len(nodes)))}]}
    if animation: j['animations'] = [{'name':'test'}]
    if skin: j['skins'] = [{'joints':[1]}]
    data = json.dumps(j).encode(); data += b' '*((-len(data))%4)
    binary += b'\0'*((-len(binary))%4)
    raw = struct.pack('<4sII',b'glTF',2,28+len(data)+len(binary))+struct.pack('<I4s',len(data),b'JSON')+data+struct.pack('<I4s',len(binary),b'BIN\0')+binary
    path.write_bytes(raw)
    return path


class Checks(unittest.TestCase):
    def triangle(self):
        return m.GLB(write_glb(WORK/'test.glb')).primitives()[0][0]

    def test_vertex_colours(self):
        # --vertex-colours: the mesh's COLOR_0 saved and read back as bytes; without it every vertex stays white
        cols = [[1,0,0,1],[0,1,0,0.5],[0,0,1,0]]
        p = m.GLB(write_glb(WORK/'colours.glb', colours=cols)).primitives()[0][0]
        rows,_ = m.vertices(m.weld(p),[0,0,0],1.0,None,True)
        info = m.save_model(WORK/'colours.efkmodel', rows, p['faces'])
        stored,_ = m.parse_model((WORK/'colours.efkmodel').read_bytes(), True)
        self.assertEqual([r[14:] for r in stored], [[1,0,0,1],[0,1,0,128/255],[0,0,1,0]])
        plain,_ = m.vertices(m.weld(p),[0,0,0],1.0)
        m.save_model(WORK/'plain.efkmodel', plain, p['faces'])
        self.assertTrue(info['strict_readback'])
        m.parse_model((WORK/'plain.efkmodel').read_bytes())          # white required without the flag

    def test_vertex_colours_cli_bounds(self):
        # The report's saved_bounds reads the saved model back with the same flag: read as a white-only model, a
        # coloured one failed the import after it was written ('Invalid vertex colors', TS Geodesic's wire mesh)
        glb = write_glb(WORK/'colours-cli.glb', colours=[[1,0,0,1],[0,1,0,0.5],[0,0,1,0]])
        report = m.run(m.parser().parse_args([str(glb),'--out',str(WORK/'colours-cli'),'--size','8','--vertex-colours']))
        self.assertEqual(report['saved_bounds']['min'],report['saved_core_bounds']['min'])
        self.assertEqual(report['saved_bounds']['max'],report['saved_core_bounds']['max'])
        stored,_ = m.parse_model((WORK/'colours-cli'/'Model'/'colours-cli.efkmodel').read_bytes(), True)
        self.assertEqual([r[14:] for r in stored], [[1,0,0,1],[0,1,0,128/255],[0,0,1,0]])

    def test_glb_read(self):
        p = self.triangle()
        self.assertEqual(p['faces'],[[0,1,2]])
        self.assertEqual(p['normal'],[[0,0,1]]*3)

    def test_identity_transforms(self):
        g = m.GLB(write_glb(WORK/'identity.glb',{'translation':[0,0,0],'rotation':[0,0,0,-1],'scale':[1,1,1]}))
        self.assertEqual(len(g.primitives()[0]),1)

    def test_ancestor_transform_refused(self):
        path = write_glb(WORK/'ancestor.glb')
        g = m.GLB(path)
        g.j['nodes'] = [{'translation':[1,0,0],'children':[1]},{'mesh':0}]
        with self.assertRaisesRegex(m.ImportErrorDetail,'Non-identity'): g.primitives()

    def test_transform_refused(self):
        for transform in [{'translation':[1,0,0]},{'scale':[2,1,1]},{'rotation':[0,1,0,0]}]:
            with self.assertRaisesRegex(m.ImportErrorDetail,'Non-identity'):
                m.GLB(write_glb(WORK/'transform.glb',transform)).primitives()

    def test_animation_refused(self):
        with self.assertRaisesRegex(m.ImportErrorDetail,'--bake'):
            m.GLB(write_glb(WORK/'animated.glb',animation=True)).primitives()

    def test_bind_pose(self):
        _,bind = m.GLB(write_glb(WORK/'skin.glb',skin=True)).primitives()
        self.assertTrue(bind)

    def test_triangle_mode_refused(self):
        with self.assertRaisesRegex(m.ImportErrorDetail,'mode 4'):
            m.GLB(write_glb(WORK/'lines.glb',mode=1)).primitives()

    def test_morph_refused(self):
        g = m.GLB(write_glb(WORK/'morph.glb'))
        g.j['meshes'][0]['primitives'][0]['targets'] = [{}]
        with self.assertRaisesRegex(m.ImportErrorDetail,'Morph'): g.primitives()

    def test_bad_glb_length(self):
        path = write_glb(WORK/'bad.glb')
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(m.ImportErrorDetail): m.GLB(path)

    def test_accessor_stride_and_bounds(self):
        g = m.GLB(write_glb(WORK/'accessor.glb'))
        g.j['bufferViews'][0]['byteStride'] = 4
        with self.assertRaisesRegex(m.ImportErrorDetail,'stride'): g.accessor(0)
        g.j['bufferViews'][0]['byteStride'] = 12
        g.j['accessors'][0]['count'] = 100
        with self.assertRaisesRegex(m.ImportErrorDetail,'overruns'): g.accessor(0)

    def test_sparse_accessor(self):
        g = m.GLB(write_glb(WORK/'sparse.glb'))
        offset = len(g.bin)
        g.bin += struct.pack('<I3f',1,4,5,6)
        g.buffer_length = len(g.bin)
        g.j['bufferViews'] += [{'buffer':0,'byteOffset':offset,'byteLength':4},{'buffer':0,'byteOffset':offset+4,'byteLength':12}]
        a = g.j['accessors'][0]
        a.pop('bufferView')
        a['sparse'] = {'count':1,'indices':{'bufferView':3,'componentType':5125},'values':{'bufferView':4}}
        self.assertEqual(g.accessor(0),[[0,0,0],[4,5,6],[0,0,0]])

    def test_sparse_index_refused(self):
        g = m.GLB(write_glb(WORK/'sparse-bad.glb'))
        offset = len(g.bin)
        g.bin += struct.pack('<I3f',3,4,5,6); g.buffer_length = len(g.bin)
        g.j['bufferViews'] += [{'buffer':0,'byteOffset':offset,'byteLength':4},{'buffer':0,'byteOffset':offset+4,'byteLength':12}]
        g.j['accessors'][0]['sparse'] = {'count':1,'indices':{'bufferView':3,'componentType':5125},'values':{'bufferView':4}}
        with self.assertRaisesRegex(m.ImportErrorDetail,'Sparse indices'): g.accessor(0)

    def test_weld_first_order(self):
        p = self.triangle()
        for field in ['pos','normal','uv']: p[field] += [p[field][0][:]]
        p['faces'] = [[3,1,2]]
        q = m.weld(p)
        self.assertEqual(q['pos'],[[0,0,0],[1,0,0],[0,1,0]])
        self.assertEqual(q['faces'],[[0,1,2]])

    def test_weld_keeps_uv_normal_tangent_seams(self):
        for field in ['normal','uv','tangent']:
            p = self.triangle(); p['tangent'] = [[1,0,0,1]]*3
            for name in ['pos','normal','uv','tangent']: p[name] += [p[name][0][:]]
            p[field][-1][0] += .25
            self.assertEqual(len(m.weld(p)['pos']),4)

    def test_generated_tangent_basis(self):
        rows,degen = m.vertices(self.triangle(),[0,0,0],1)
        self.assertEqual(degen,0)
        self.assertEqual(rows[0][3:12],[0,0,1,0,1,0,1,0,0])

    def test_degenerate_uv_count(self):
        p = self.triangle(); p['uv'] = [[0,0]]*3
        rows,degen = m.vertices(p,[0,0,0],1)
        self.assertEqual(degen,1)
        self.assertTrue(all(math.isfinite(x) for r in rows for x in r))

    def test_missing_normals(self):
        p = self.triangle(); p['normal'] = None
        rows,_ = m.vertices(p,[0,0,0],1)
        self.assertEqual(rows[0][3:6],[0,0,1])

    def test_tangent_handedness(self):
        p = self.triangle(); p['tangent'] = [[2,0,0,-1]]*3
        rows,_ = m.vertices(p,[0,0,0],1)
        self.assertEqual(rows[0][6:12],[0,-1,0,1,0,0])

    def test_rotation_complete_basis(self):
        rows,_ = m.vertices(self.triangle(),[0,0,0],1)
        rotated = m.rotate(copy.deepcopy(rows),[90,0,0])
        for row in rotated:
            for offset in [3,6,9]: self.assertAlmostEqual(sum(x*x for x in row[offset:offset+3]),1)
        self.assertAlmostEqual(rotated[0][4],-1)

    def test_spherical_uses_stored_positions(self):
        rows,_ = m.vertices(self.triangle(),[0,0,0],1)
        out = m.spherical(rows)
        self.assertEqual(out[0][12:],[0,.5])
        self.assertEqual(out[1][12:],[0,.5])
        self.assertEqual(out[2][13],0)

    def test_model_strict_parse(self):
        rows,_ = m.vertices(self.triangle(),[0,0,0],1)
        data = m.model_bytes(rows,[[0,1,2]])
        self.assertEqual(len(data),212)
        self.assertEqual(m.parse_model(data)[1],[[0,1,2]])
        for bad in [data[:-1],data+b'\0',data[:16]+b'\xff'*4+data[20:]]:
            with self.assertRaises(m.ImportErrorDetail): m.parse_model(bad)

    def test_16bit_division(self):
        im = Image.new('I;16',(8,8)); im.putdata([0,128,129,256,257,385,386,65535]*8)
        path = WORK/'16.png'; im.save(path)
        converted = m.load_image(path,8)
        self.assertEqual([converted.getpixel((x,0))[0] for x in range(8)],[0,0,0,0,1,1,1,255])

    def test_lit_native_material(self):
        data,shader = m.material_bytes('Test',['Color','Normal','Surface','DissolveMask'],['color','normal','roughness'])
        parsed = m.parse_material(data)
        self.assertEqual(parsed['shader'],shader)
        self.assertIn('meshDissolveMask.x - (1.0 - vcolor.w)',shader)
        self.assertNotIn('meshSurface.y;',shader)
        self.assertEqual(len(parsed['textures']),4)
        with self.assertRaises(m.ImportErrorDetail): m.parse_material(data+b'\0')

    def test_lod_fit_main(self):
        main,low = write_glb(WORK/'main.glb'),write_glb(WORK/'lod.glb')
        g = m.GLB(low)
        # Alter stored geometry in the LOD without changing main bounds.
        data = bytearray(low.read_bytes()); off = len(data)-len(g.bin)
        struct.pack_into('<f',data,off+12,10); low.write_bytes(data)
        report = m.run(m.parser().parse_args([str(main),'--lod',str(low),'--out',str(WORK/'lod-result'),'--size','8']))
        self.assertEqual(report['center'],[.5,.5,0])
        self.assertEqual(report['scale'],2)
        self.assertEqual(report['geometry_whole_bounds']['dimensions'],[10,1,0])

    def test_keep_origin_and_scale(self):
        # The test triangle spans (0,0)..(1,1); the pivot stays at the source origin and units scale by 2.5.
        report = m.run(m.parser().parse_args([str(write_glb(WORK/'origin.glb')),'--out',str(WORK/'origin-result'),
                                              '--size','8','--keep-origin','--scale','2.5']))
        self.assertEqual(report['center'],[0,0,0])
        self.assertEqual(report['scale'],2.5)
        self.assertIsNone(report['diameter'])
        self.assertEqual(report['saved_bounds']['min'],[0,0,0])
        self.assertEqual(report['saved_bounds']['max'],[2.5,2.5,0])
        with self.assertRaisesRegex(m.ImportErrorDetail,'Scale must be positive'):
            m.run(m.parser().parse_args([str(WORK/'origin.glb'),'--out',str(WORK/'origin-bad'),'--size','8','--scale','0']))

    def test_uv2_centre_v6(self):
        # (1,0,0) m and (0,1,0) m at --scale 2.5: Unreal x 100 cm, and glb y = Unreal z 100 cm; 40 cm per unit
        report = m.run(m.parser().parse_args([str(write_glb(WORK/'uv2.glb')),'--out',str(WORK/'uv2-result'),
                                              '--size','8','--keep-origin','--scale','2.5','--uv2-centre','40']))
        data = (WORK/'uv2-result/Model/uv2.efkmodel').read_bytes()
        rows,faces = m.parse_model_v6(data)
        self.assertEqual(report['models']['uv2']['version'],6)
        self.assertEqual(struct.unpack_from('<4i',data),(6,1,1,1))
        uv2 = {tuple(round(x,4) for x in r[:3]):(round(r[14],3),round(r[15],3)) for r in rows}
        self.assertEqual(uv2[(2.5,0,0)],(100.0,0.0))
        self.assertEqual(uv2[(0,2.5,0)],(0.0,100.0))
        self.assertEqual(uv2[(0,0,0)],(0.0,0.0))
        self.assertEqual([r[12:14] for r in rows],[r[12:14] for r in m.parse_model((WORK/'origin-result/Model/origin.efkmodel').read_bytes())[0]]
                         if (WORK/'origin-result/Model/origin.efkmodel').exists() else [r[12:14] for r in rows])
        with self.assertRaisesRegex(m.ImportErrorDetail,'must be positive'):
            m.run(m.parser().parse_args([str(WORK/'uv2.glb'),'--out',str(WORK/'uv2-bad'),'--size','8','--uv2-centre','0']))

    def test_missing_map_refused(self):
        args = m.parser().parse_args([str(write_glb(WORK/'missing.glb')),'--out',str(WORK/'missing-out'),'--color',str(WORK/'absent.png'),'--size','8'])
        with self.assertRaisesRegex(m.ImportErrorDetail,'Missing explicitly requested map'): m.run(args)

    def test_cli_bake_gate(self):
        args = m.parser().parse_args(['no.glb','--out',str(WORK/'bake'),'--bake','20'])
        with self.assertRaisesRegex(m.ImportErrorDetail,'prerequisite'): m.run(args)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--work',type=Path,required=True)
    args = p.parse_args(); WORK = args.work.resolve(); WORK.mkdir(parents=True,exist_ok=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    print(json.dumps({'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
