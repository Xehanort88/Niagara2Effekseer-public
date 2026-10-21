"""Index/API fixtures, invoked by test_rdcdigest.py; all writes stay in --work."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import ddsio
import digestlib


def run(work, check, dds_dx10):
    base = (Path(work) / 'index-tests').resolve()
    base.mkdir(exist_ok=True)
    check('mesh BGRA semantic component order', np.allclose(
        digestlib._vertex_array(bytes([10, 20, 30, 255]), 4, 0, 'B8G8R8A8_UNORM'),
        [[30 / 255, 20 / 255, 10 / 255, 1]]))
    check('mesh signed normal minimum', digestlib._vertex_array(bytes([128, 127, 0, 0]), 4, 0,
                                                              'R8G8B8A8_SNORM').tolist() == [[-1, 1, 0, 0]])
    seed = base / 'seed'
    store = base / 'Exports/OT2/capture/rdcdigest'
    for path in (seed, store):
        if path.exists():
            if not path.resolve().is_relative_to(base):
                raise ValueError('fixture cleanup escaped work folder: %s' % path)
            shutil.rmtree(path)
    cap = seed / 'captures/example'
    cap.mkdir(parents=True)
    for k in ('textures', 'meshes', 'postvs', 'dynamic', 'buffers', 'shaders'):
        (seed / 'blobs' / k).mkdir(parents=True)
    tex = bytes([17, 33, 99, 255]) * 4
    (seed / 'blobs/textures/aa.dds').write_bytes(dds_dx10(28, 2, 2, 1, tex))
    # first stored vertex corresponds to source element 7; draw indices 2,3 + baseVertex 5.
    (seed / 'blobs/meshes/bb.bin').write_bytes(np.array([[10, 20], [30, 40]], '<f4').tobytes())
    (seed / 'blobs/meshes/cc.bin').write_bytes(np.array([2, 3], '<u2').tobytes())
    (seed / 'blobs/meshes/dd.bin').write_bytes(np.array([[50], [60]], '<f4').tobytes())
    post = np.array([[[0, 0, .5, 1], [1, 1, .5, 1]]], '<f4')
    (seed / 'blobs/postvs/ee.bin').write_bytes(post.tobytes())
    (cap / 'contrib').mkdir()
    ddsio.write_exr(str(cap / 'contrib/7_0.exr'), {'R': np.ones((1, 2), np.float32)})
    cbraw = np.array([1, 2, 0, 7], '<u4').tobytes()
    m = dict(version=2, api='D3D11', capture='example.rdc', capture_bytes=100,
             counts=dict(draws=1, dispatches=0, kept=1), timing=dict(total_s=1), options={},
             blobs={'textures': ['aa'], 'meshes': ['bb', 'cc', 'dd'], 'postvs': ['ee']},
             shaders={'ff': {}}, tables=dict(
                 cbdata={'ab': cbraw.hex()}, cbvars={'ab-vars': {'value': 7}},
                 layout={'L': [dict(name='POSITION', slot=0, offset=0, format='R32G32_FLOAT',
                                    perInstance=False, used=True),
                               dict(name='INSTANCE', slot=1, offset=0, format='R32_FLOAT',
                                    perInstance=True, rate=1, used=True)]},
                 postvs_layout={'P': [dict(name='SV_Position', index=0, offset=0, comps=4)]}),
             draws=[dict(eid=7, kind='draw', name='example', path='Particles', keep=['blend'],
                         indices=2, instances=2, indexed=True, base_vertex=5, vertex_offset=0,
                         instance_offset=4, index_offset=0, layout='L',
                         viewport=[0, 0, 100, 50, 0, 1], targets=[],
                         blend_summary=[dict(kind='additive')],
                         shaders={'vs': dict(checksum='ff', md5='f0', srvs=[], cbuffers=[]),
                                  'ps': dict(checksum='fa', md5='f1',
                                             srvs=[dict(kind='texture', register=0, hash='aa',
                                                        width=2, height=2, depth=1, view_format='RGBA8')],
                                             cbuffers=[dict(register=0, data='ab', size=16, vars='ab-vars')])},
                         ib=dict(hash='cc', format='R16_UINT'),
                         vb=[dict(slot=0, hash='bb', stride=8, bound_stride=8, first_element=7,
                                  count=2, per_instance=False),
                             dict(slot=1, hash='dd', stride=4, bound_stride=4, first_element=4,
                                  count=2, per_instance=True)],
                         postvs={'vs': dict(vertex='ee', stride=16, vertices=2, inst_stride=0, layout='P')},
                         contrib=[dict(slot=0, changed=True, exr='7_0.exr', box=[3, 4, 2, 1])])])
    m.update(dynamic={}, tiny={}, gpu={'counter': 'fixture'})
    m['draws'][0]['gpu_us'] = 1.25
    m['draws'][0]['contrib'][0].update(id='1', format='R32_FLOAT', size=[100, 50], pixels=2, before_eid=6)
    m['tables']['postvs_layout']['P'][0].update(type='Float', system='Position')
    (cap / 'manifest.json').write_text(json.dumps(m))
    # Exercise discovery and indexing on a copied store, never a live store.
    shutil.copytree(seed, store)
    db = base / 'index.sqlite'
    result = digestlib.build_index([store.parent.parent], db, rebuild=True)
    check('index copied store build', result['added'] == 1)
    check('index incremental skip', digestlib.build_index([store], db)['skipped'] == 1)
    with digestlib.open(db) as d:
        check('indexed vertex range count', d.con.execute('SELECT vertices FROM draws').fetchone()[0] == 2)
        check('API captures game', d.captures(game='OT2')[0]['id'] == 'example')
        check('API draw filters', len(d.draws('example', kept=True, ps='fa', shader='ff', texture='aa',
                                             marker='Particle', blend='additive')) == 1 and not d.draws(ps='00'))
        check('API manifest row', d.draw('example', 7) == m['draws'][0])
        cb = d.cbuffer('example', 7, 'ps', 0)
        check('API cbuffer bytes uint decoded', cb['raw'] == cbraw and cb['uint_rows'][0] == (1, 2, 0, 7)
              and cb['decoded'] == {'value': 7})
        texture = d.texture('aa')
        check('API texture pixels format size path', texture['array'][0, 0].tolist() == [17, 33, 99, 255]
              and texture['size'] == (2, 2) and Path(d.texture_path('aa')).is_file())
        mesh = d.mesh('example', 7)
        check('API mesh base vertex and first element', mesh['indices'].tolist() == [2, 3]
              and mesh['element_indices']['POSITION'].tolist() == [0, 1]
              and mesh['attributes']['POSITION'].tolist() == [[10, 20], [30, 40]])
        check('API mesh instance offset', mesh['element_indices']['INSTANCE'].tolist() == [0, 1]
              and mesh['attributes']['INSTANCE'].tolist() == [[50], [60]])
        pv = d.postvs('example', 7)
        check('API postvs screen arrays', np.array_equal(pv['array'], post)
              and pv['screen'][0].tolist() == [[50, 100]])
        contrib = d.contrib('example', 7, 0)
        check('API contrib float array box', contrib['box'] == [3, 4, 2, 1]
              and contrib['array'].dtype == np.float32 and contrib['array'].tolist() == [[[1], [1]]])
        check('where shader texture mesh postvs', all(d.where(h) for h in ('ff', 'aa', 'bb', 'cc', 'ee')))
    cli = str(Path(__file__).with_name('rdcdigest.py'))
    for command in (['captures'], ['draws'], ['draws', '--kept', '--shader', 'fa'],
                    ['draw', 'example', '7', '--json'], ['cbuffer', 'example', 'ab'], ['texture', 'aa'],
                    ['shader', 'ff'], ['contrib', 'example', '7'], ['postvs', 'example', '7'],
                    ['dynamic', 'example'], ['exposure', 'example'], ['gpu', 'example'], ['stats']):
        a = subprocess.run([sys.executable, cli, '--store', str(store), *command], capture_output=True, text=True)
        b = subprocess.run([sys.executable, cli, '--db', str(db), *command], capture_output=True, text=True)
        check('CLI db/store parity ' + ' '.join(command), a.returncode == b.returncode == 0 and a.stdout == b.stdout,
              a.stderr + b.stderr if a.returncode or b.returncode else '')
    # Rewrite copied manifest, then remove it; both are detected in one incremental database.
    path = store / 'captures/example/manifest.json'
    m['options']['new'] = True
    path.write_text(json.dumps(m))
    check('index incremental update', digestlib.build_index([store], db)['updated'] == 1)
    duplicate = base / 'duplicate/rdcdigest'
    if duplicate.exists():
        if not duplicate.resolve().is_relative_to(base):
            raise ValueError('fixture cleanup escaped work folder: %s' % duplicate)
        shutil.rmtree(duplicate)
    shutil.copytree(store, duplicate)
    digestlib.build_index([duplicate], db)
    with digestlib.open(db) as d:
        try:
            d.captures()
            check('duplicate capture refusal', False)
        except ValueError as ex:
            check('duplicate capture refusal with both paths', str(store.resolve()) in str(ex)
                  and str(duplicate.resolve()) in str(ex))
    result = subprocess.run([sys.executable, cli, '--db', str(db), 'draw', 'example', '7'],
                            capture_output=True, text=True)
    check('CLI duplicate refusal', result.returncode != 0 and str(duplicate.resolve()) in result.stderr
          and str(store.resolve()) in result.stderr)
    path.unlink()
    check('index incremental removal', digestlib.build_index([store], db)['removed'] == 1)
    with digestlib.open(db) as d:
        check('removal cascades children', d.con.execute('SELECT count(*) FROM draws').fetchone()[0] == 1)
    digestlib.build_index([store], db, rebuild=True)
    with digestlib.open(db) as d:
        check('rebuild drops old stores', not d.captures())
    path.write_text(json.dumps(m))
    (duplicate / 'captures/example').rename(duplicate / 'captures/second')
    digestlib.build_index([store, duplicate], db, rebuild=True)
    with digestlib.open(db) as d:
        check('multi-store capture and draw listing', len(d.captures()) == 2 and len(d.draws()) == 2)
        check('multi-store where and per-file blob inventory', len(d.where('aa')) == 2 and
              d.con.execute("SELECT count(*) FROM blobs WHERE hash='aa'").fetchone()[0] == 2)
        check('shared hash resolves deterministically', Path(d.texture_path('aa')).is_file())
    output = subprocess.run([sys.executable, cli, 'draws', '--db', str(db), '--kept'],
                            capture_output=True, text=True)
    check('multi-store CLI listing and trailing db', output.returncode == 0 and '2 draws' in output.stdout)
