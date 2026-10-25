"""Tests for tools/unity/unity_rdc.py on a synthetic Unity-like capture folder (no RenderDoc needed): two billboard
draws of one layer, a UI draw and an opaque draw left out, a 3 s video for the footage step.
usage: python tools/unity/test_unity_rdc.py --work <folder>"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'tools' / 'rdcdigest'))
import ddsio  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print('%s %s %s' % ('ok  ' if cond else 'FAIL', name, detail))
    if not cond:
        FAILS.append(name)


def dds_rgba8(w, h, payload):
    hdr = bytearray(148)
    hdr[:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000, h, w, 0, 0, 1)
    struct.pack_into('<II4s', hdr, 76, 32, 0x4, b'DX10')
    struct.pack_into('<5I', hdr, 128, 28, 3, 0, 1, 0)
    return bytes(hdr) + payload


def quads(centres, half):
    """Clip-space corners (4 per particle, w = 1) and the Unity index pattern 0 1 2 2 3 0."""
    v = [[x + dx, y + dy, 0.5, 1.0] for x, y in centres for dx, dy in
         ((-half, -half), (half, -half), (half, half), (-half, half))]
    i = [b + k for b in range(0, 4 * len(centres), 4) for k in (0, 1, 2, 2, 3, 0)]
    return np.array(v, '<f4'), np.array(i, '<u2')


def build(folder):
    store = folder / 'rdcdigest'
    cap = store / 'captures' / 'breath1'
    for k in ('textures', 'meshes', 'postvs', 'shaders'):
        (store / 'blobs' / k).mkdir(parents=True)
    (cap / 'contrib').mkdir(parents=True)
    (folder / 'breath1.rdc').write_bytes(b'not a real capture')
    tex = bytes([200, 120, 30, 0, 200, 120, 30, 255, 10, 20, 250, 255, 10, 20, 250, 255])   # rows: orange, blue
    (store / 'blobs/textures/a0aa.dds').write_bytes(dds_rgba8(2, 2, tex))
    (store / 'blobs/shaders/ps01.asm').write_text('ps_5_0\nsample_indexable ...\n')
    post, idx = quads([(-0.4, 0.0), (0.4, 0.2)], 0.1)
    (store / 'blobs/postvs/pv1.bin').write_bytes(post.tobytes())
    (store / 'blobs/postvs/ix1.bin').write_bytes(idx.tobytes())
    # vertex input: POSITION xyz float, COLOR rgba8 (per particle), TEXCOORD0 uv; stride 20
    colours = [(255, 128, 0, 255), (255, 64, 0, 128)]
    vb = b''.join(struct.pack('<3f4B2f', 0, 0, 0, *colours[p], 0, 0)[:24] for p in range(2) for _ in range(4))
    (store / 'blobs/meshes/vb1.bin').write_bytes(vb)
    (store / 'blobs/meshes/ib1.bin').write_bytes(idx.tobytes())
    rgb = {c: np.array([[v] * 3, [v / 5] * 3], np.float32) for c, v in zip('RGB', (0.5, 0.25, 0.0))}  # bright row 0
    ddsio.write_exr(str(cap / 'contrib/10_0.exr'), rgb)
    ddsio.write_exr(str(cap / 'contrib/11_0.exr'), {c: v * 4 for c, v in rgb.items()})
    from PIL import Image
    Image.new('RGB', (8, 8), (40, 20, 10)).save(cap / 'final.png')

    def cb(tint, proj):
        return {'_TintColor': [tint, 0.5, 0.1, 1.0], '_MainTex_ST': [1.0, 1.0, 0.0, 0.0], '_InvFade': 1.0,
                'unity_ObjectToWorld': [[1, 0, 0, 0]] * 4, '_Time': [0.1, 2, 4, 6],
                '_ProjectionParams': [proj, 0.3, 1000.0, 0.001]}

    def row(eid, keep, tint, light, depth=True, opaque=False, vs='vs01', ps='ps01', target='rt1', res=None,
            extra=()):
        r = dict(eid=eid, kind='draw', name='DrawIndexed(12)', path='Camera.Render/Drawing/ParticleSystem.Draw',
                 keep=keep, indices=12, instances=1, indexed=True, base_vertex=0, vertex_offset=0, index_offset=0,
                 instance_offset=0, layout='L', depth='D', raster='R', viewport=[0, 0, 100, 100, 0, 1],
                 targets=[dict(slot=0, id=target, name='TempBuffer ' + target, format='R16G16B16A16_FLOAT',
                               size=[100, 100])],
                 depth_target=dict(id='ds1', format='D24_UNORM_S8_UINT') if depth else None,
                 blend_summary=[dict(slot=0, enabled=not opaque, kind='opaque' if opaque else 'additive',
                                     color='SrcAlpha Add One', alpha='Zero Add One', writeMask=15)],
                 shaders={'vs': dict(checksum=vs, md5='m0', srvs=[], cbuffers=[]),
                          'ps': dict(checksum=ps, md5='m1',
                                     srvs=[dict(kind='texture', register=0, name='_MainTex', hash='a0aa', width=2,
                                                height=2, depth=1, mips=1, format='R8G8B8A8_UNORM',
                                                view_format='R8G8B8A8_UNORM', **({'resource': res} if res else {})),
                                           dict(kind='texture', register=1, name='_CameraDepthTexture',
                                                dynamic=True, written_at=3, format='R32_FLOAT')] + list(extra),
                                     cbuffers=[dict(register=0, name='$Globals', data='c%d' % eid, size=16,
                                                    vars='c%d-v' % eid)])},
                 ib=dict(hash='ib1', format='R16_UINT'),
                 vb=[dict(slot=0, hash='vb1', stride=24, bound_stride=24, first_element=0, count=8,
                          per_instance=False)],
                 postvs={'vs': dict(vertex='pv1', index='ix1', index_stride=2, base_vertex=0, stride=16,
                                    vertices=8, inst_stride=0, layout='P')},
                 contrib=[dict(slot=0, changed=True, exr='%d_0.exr' % eid, box=[20, 30, 3, 2], pixels=6,
                               format='R16G16B16A16_FLOAT', size=[100, 100], before_eid=eid - 1,
                               sum={'R': light, 'G': light / 2, 'B': 0.0})] if light else [])
        tables['cbdata']['c%d' % eid] = '00' * 16
        tables['cbvars']['c%d-v' % eid] = cb(tint, -1.0 if eid == 10 else 1.0)   # eid 10 drew flipped
        return r

    tables = dict(cbdata={}, cbvars={}, depth={'D': dict(depthEnable=True, depthWrites=False,
                                                         depthFunction='LessEqual')},
                  raster={'R': dict(cullMode='NoCull')},
                  layout={'L': [dict(name='POSITION', slot=0, offset=0, format='R32G32B32_FLOAT', perInstance=False,
                                     used=True),
                                dict(name='COLOR', slot=0, offset=12, format='R8G8B8A8_UNORM', perInstance=False,
                                     used=True),
                                dict(name='TEXCOORD0', slot=0, offset=16, format='R32G32_FLOAT', perInstance=False,
                                     used=True)]},
                  postvs_layout={'P': [dict(name='SV_Position', index=0, offset=0, comps=4, type='Float',
                                            system='Position')]})
    for eid in (14, 15, 16):
        shutil.copyfile(cap / 'contrib/11_0.exr', cap / ('contrib/%d_0.exr' % eid))
    draws = [row(10, ['blend'], 1.0, 3.0), row(11, ['blend'], 2.0, 12.0),
             row(12, ['blend'], 1.0, 0, depth=False),                       # UI: no depth target
             row(13, [], 1.0, 0, opaque=True),                              # opaque scenery, not kept
             # HUD drawn into the scene target with depth bound (WotR): a sprite atlas, and a draw sharing its shaders
             row(14, ['blend'], 1.0, 50.0, vs='vsui', ps='psui', res='sactx-0-4096x4096-BC7-HUD_Sprite_Atlas-08'),
             row(15, ['blend'], 1.0, 40.0, vs='vsui', ps='psui', res='GoldenDragonBreathAbility'),
             # a refraction mask in its own target, and the unkept full-screen pass that reads it
             row(16, ['blend'], 1.0, 100.0, ps='psmask', target='rt2'),
             row(17, [], 1.0, 0, opaque=True, ps='pscomp',
                 extra=[dict(kind='texture', register=2, name='texture2', id='rt2', resource='TempBuffer rt2',
                             dynamic=True, written_at=16)])]
    m = dict(version=2, api='D3D11', capture=str(folder / 'breath1.rdc'), capture_bytes=18,
             counts=dict(draws=8, dispatches=0, kept=6), timing=dict(total_s=1), options={},
             blobs={}, tables=tables, draws=draws, dynamic={}, tiny={},
             targets={'rt1': dict(name='TempBuffer rt1', after_eid=16, after=dict(png='rt1_after.png'))})
    (cap / 'manifest.json').write_text(json.dumps(m))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    work = Path(ap.parse_args().work).resolve()
    folder, out = work / 'breatheffect', work / 'report'
    for p in (folder, out):
        if p.exists():
            shutil.rmtree(p)
    folder.mkdir(parents=True)
    build(folder)
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'testsrc=size=160x90:rate=30:duration=3', str(folder / 'footage.mp4')], check=True)
    script = str(HERE / 'unity_rdc.py')
    r = subprocess.run([sys.executable, script, str(folder), '--work', str(out), '--skip-digest'],
                       capture_output=True, text=True)
    check('runs', r.returncode == 0, r.stdout + r.stderr)
    if r.returncode:
        sys.exit(1)
    data = json.loads((out / 'layers.json').read_text())
    cap = data['captures'][0]
    kinds = {k: [x['eids'] for x in cap['layers'] if x['kind'] == k] for k in ('scene', 'side', 'ui')}
    check('one scene layer of the two scene draws (opaque and depthless UI left out)', kinds['scene'] == [[10, 11]],
          kinds)
    check('HUD draws with a depth target left out as UI, by texture and by shared shaders',
          kinds['ui'] == [[14, 15]] and 'HUD_Sprite_Atlas' in cap['layers'][-1]['ui'] and cap['ui_draws'] == 2, kinds)
    check('mask draw is a side-target layer ranked after the scene layer although it changes more',
          kinds['side'] == [[16]] and [x['rank'] for x in cap['layers']] == [1, 2, 3] and cap['layers'][1]['light'] == 0
          and cap['layers'][1]['change'] == 150.0, [(x['rank'], x['kind'], x['light']) for x in cap['layers']])
    st = cap['side_targets']
    check('side target names the unkept pass that reads it', len(st) == 1 and st[0]['id'] == 'rt2' and
          [(x['eid'], x['kept'], x['writes']) for x in st[0]['readers']] == [(17, False, ['TempBuffer rt1'])], st)
    check('main target', cap['main_target']['id'] == 'rt1', cap['main_target'])
    ly = cap['layers'][0]
    check('layer light is the summed |dRGB|', ly['light'] == 22.5, ly['light'])
    check('material keeps material values, drops Unity globals and matrices',
          set(ly['material']) == {'_MainTex_ST', '_InvFade'}, ly['material'])
    check('per-draw material difference', ly['draws'][1]['material_diff'] == {'_TintColor': [2.0, 0.5, 0.1, 1.0]},
          ly['draws'][1]['material_diff'])
    p = ly['draws'][0]['particles']
    check('two billboards', p['count'] == 2 and p['unit'] == 'billboard' and p['on_screen'] == 2, p)
    check('billboard size 10 px in a 100 px viewport', p['size_px'] == [10.0, 10.0, 10.0], p['size_px'])
    check('centres, y turned upright on the flipped draw',
          p['x_range'] == [30.0, 70.0] and p['y_range'] == [50.0, 60.0], (p['x_range'], p['y_range']))
    check('unflipped draw kept as stored', ly['draws'][1]['particles']['y_range'] == [40.0, 50.0] and
          ly['draws'][0]['flipped'] and not ly['draws'][1]['flipped'], ly['draws'][1]['flip_why'])
    c0, c1 = ly['draws'][0]['contrib'][0], ly['draws'][1]['contrib'][0]
    check('contribution box turned upright', c0['box'] == [20, 68, 3, 2] and c0['box_as_stored'] == [20, 30, 3, 2]
          and c1['box'] == [20, 30, 3, 2], (c0, c1))
    from PIL import Image
    im0 = np.asarray(Image.open(out / c0['file']))
    im1 = np.asarray(Image.open(out / c1['file']))
    check('contribution image turned upright', im0[0, 0, 0] < im0[1, 0, 0] and im1[0, 0, 0] > im1[1, 0, 0])
    col = p['inputs']['COLOR']
    check('per-particle vertex colour', col['min'][3] == round(128 / 255, 5) and col['max'][0] == 1.0, col)
    check('particle CSV', (out / p['csv']).read_text().count('\n') == 3)
    t = {x['register']: x for x in ly['textures']}
    check('texture exported with alpha', (out / t[0]['file']).exists() and (out / t[0]['alpha']).exists(), t[0])
    check('texture turned upright', np.asarray(Image.open(out / t[0]['file']))[0, 0].tolist() == [10, 20, 250, 255])
    check('dynamic texture noted', t[1].get('dynamic') and t[1]['written_at'] == 3, t[1])
    check('contribution image', all((out / c['file']).exists() for dr in ly['draws'] for c in dr['contrib']))
    check('shader disassembly copied', (out / ly['shaders']['ps']['asm']).exists() and ly['shaders']['vs']['asm']
          is None)
    check('final frame copied', (out / cap['final']).exists())
    rep = (out / 'REPORT.md').read_text()
    check('report names eids and layer', '### breath1 layer 1 (eid 10, 11)' in rep and '| 10 |' in rep)
    check('report lists side targets and their reader, UI apart', '### Side targets' in rep and 'eid 17 (draw, not kept'
          in rep and 'UI left out of the ranking' in rep and '### breath1 layer 3' not in rep)

    # mesh copies: Unity batches mesh particles as one mesh repeated with its vertices appended
    sys.path.insert(0, str(HERE))
    import unity_rdc
    tri = np.array([0, 1, 2, 2, 1, 3])
    check('three copies of a 4-vertex mesh', unity_rdc.copy_rows(np.concatenate([tri, tri + 4, tri + 8])).shape == (3, 6))
    check('one mesh stays one', unity_rdc.copy_rows(np.array([0, 1, 2, 0, 2, 3, 0, 3, 4])) is None)
    check('a strip sharing vertices is not copies', unity_rdc.copy_rows(np.concatenate([tri, tri + 2, tri + 4])) is None)

    # a black final.png (an undrawn swap buffer): the scene target after its last kept draw stands in, turned upright
    Image.new('RGB', (8, 8), (0, 0, 0)).save(folder / 'rdcdigest/captures/breath1/final.png')
    (folder / 'rdcdigest/captures/breath1/rt').mkdir()
    after = np.zeros((4, 4, 3), np.uint8)
    after[0] = 200                                                   # stored top row = the frame's bottom row
    Image.fromarray(after).save(folder / 'rdcdigest/captures/breath1/rt/rt1_after.png')
    r = subprocess.run([sys.executable, script, str(folder), '--work', str(out), '--skip-digest', '--overwrite',
                        '--skip-video'], capture_output=True, text=True)
    fc = json.loads((out / 'layers.json').read_text())['captures'][0]
    shown = np.asarray(Image.open(out / fc['final'])) if r.returncode == 0 else None
    check('black final replaced by the upright scene target', r.returncode == 0 and 'black' in fc.get('final_note', '')
          and shown[-1, 0, 0] == 200 and shown[0, 0, 0] == 0, r.stderr + str(fc.get('final_note')))
    Image.new('RGB', (8, 8), (40, 20, 10)).save(folder / 'rdcdigest/captures/breath1/final.png')
    check('view page', 'contribution' in (out / 'view.html').read_text())
    if ffmpeg:
        f = data['footage'][0]
        check('footage 1 fps frames named by second', f['fps'] == 30.0 and f['count'] == 3 and
              (folder / 'frames-1fps-footage' / '0000.png').exists() and (out / f['sheet']).exists(), f)
    else:
        print('SKIP: footage (no ffmpeg on PATH)')
    r = subprocess.run([sys.executable, script, str(folder), '--work', str(out), '--skip-digest'],
                       capture_output=True, text=True)
    check('refuses an existing work folder', r.returncode != 0 and 'already exists' in r.stderr, r.stderr)
    r = subprocess.run([sys.executable, script, str(folder), '--work', str(out), '--skip-digest', '--overwrite'],
                       capture_output=True, text=True)
    check('rerun with --overwrite', r.returncode == 0, r.stderr)
    r = subprocess.run([sys.executable, script, str(folder), '--work', str(out), '--skip-digest', '--overwrite',
                        '--no-flip', '--skip-video'], capture_output=True, text=True)
    nf = json.loads((out / 'layers.json').read_text())['captures'][0]['layers'][0]
    check('--no-flip keeps texture, box and y as stored', r.returncode == 0 and
          np.asarray(Image.open(out / nf['textures'][0]['file']))[0, 0].tolist() == [200, 120, 30, 0] and
          nf['draws'][0]['contrib'][0]['box'] == [20, 30, 3, 2] and
          nf['draws'][0]['particles']['y_range'] == [40.0, 50.0], r.stderr)

    # the bundle, unzipped anywhere, runs on its own modules and writes beside each capture folder, in _analysis
    import datetime
    import zipfile
    bundle, tools = work / 'unityfx-bundle.zip', work / 'UnityFX-tools'
    wotr, rt = work / 'Unity Game A', work / 'Unity Game B'
    for d in (tools, wotr, rt):
        if d.exists():
            shutil.rmtree(d)
    r = subprocess.run([sys.executable, str(HERE / 'pack.py'), '--out', str(bundle)], capture_output=True, text=True)
    check('pack', r.returncode == 0, r.stderr)
    zipfile.ZipFile(bundle).extractall(tools)
    check('bundle holds both sides', {'unityfx.ps1', 'fx_reference_index.py', 'unity_dump.py', 'shuriken_summary.py',
                                      'shuriken.py', 'unityasset.py', 'unity_rdc.py', 'run_rdc.bat',
                                      'README-unity.txt', 'VERSION.txt'} <= {x.name for x in tools.iterdir()})
    check('bundle holds the rdcdigest modules', sorted(x.name for x in (tools / 'rdc_tools').iterdir()) ==
          ['ddsio.py', 'digestlib.py', 'rawdiff.py', 'rd_digest.py', 'rd_texpng.py', 'rdcdigest.py'])
    check('batch file has CRLF line ends', b'\r\n:collect\r\n' in (tools / 'run_rdc.bat').read_bytes())
    day = datetime.date.today().strftime('%Y%m%d')
    # Unity game A keeps one folder per effect under Renderdoc; Unity game B keeps its .rdc files in Renderdpc itself
    for cap, analysis in ((wotr / 'Renderdoc' / 'breatheffect',
                           wotr / 'Renderdoc' / '_analysis' / ('wotr-breatheffect-' + day)),
                          (rt / 'Renderdpc', rt / '_analysis' / ('rt-renderdpc-' + day))):
        shutil.copytree(folder, cap)
        # -E, not -I: away from the repo's modules (PYTHONPATH), but numpy may live in the user site-packages
        r = subprocess.run([sys.executable, '-E', str(tools / 'unity_rdc.py'), str(cap), '--skip-digest',
                            '--skip-video'], capture_output=True, text=True, cwd=str(work))
        check('bundled run writes %s' % analysis.relative_to(work), r.returncode == 0 and
              (analysis / 'REPORT.md').exists(), r.stdout + r.stderr)
    print('%d failure(s)' % len(FAILS))
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
