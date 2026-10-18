"""Tests for tools/effectjob (manifest, preflight, runner, ledger) on the hand-written export tree of
tools/cascade/test_build_effekseer.py (no game content).

    python tools/effectjob/test_effectjob.py --work out/tests/effectjob

Needs the built 1.70e Niagara2Efk DLL (the end-to-end part builds three small effects and combines them). A missing
tool is a failure here, not a skip.
"""
import argparse
import copy
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'tools' / 'cascade'))
import efkinfo  # noqa: E402
import ledger as L  # noqa: E402
import manifest as MF  # noqa: E402
import preflight as PF  # noqa: E402
import test_build_effekseer as TB  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(detail)[:600]))
    if not cond:
        FAILS.append(name)


def info_chunk(version, items):
    """An .efkefc with only an INFO chunk: items = [(file type, path)] (1.6+) or 7 lists (<= 1600)."""
    s = lambda p: struct.pack('<i', len(p) + 1) + p.encode('utf-16-le') + b'\0\0'
    body = struct.pack('<i', version)
    if version <= 1600:
        for lst in items:
            body += struct.pack('<i', len(lst)) + b''.join(s(p) for p in lst)
    else:
        body += struct.pack('<i', len(items)) + b''.join(struct.pack('<ii', t, 0) + s(p) for t, p in items)
    return b'EFKE' + struct.pack('<i', 1) + b'INFO' + struct.pack('<i', len(body)) + body


def effectjob(*args):
    p = subprocess.run([sys.executable, '-B', str(HERE / 'effectjob.py')] + [str(a) for a in args], cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return p.returncode, p.stdout + p.stderr


def base_manifest(exports, dump, hold, meshes, seq=None):
    names = [e.name for e in __import__('cascade').Dump(dump, exports).emitters()]
    hold_names = [e.name for e in __import__('cascade').Dump(hold, exports).emitters()]
    keep = {'Dot', 'Burst', 'ScrollMesh'}
    m = {
        'schema': 'effectjob/1', 'effect': 'TestFx', 'game': 'Test game', 'skill': 'Test skill',
        'runtime': {'efk18': False}, 'exports': str(exports),
        'inputs': {'meshes': str(meshes)},
        'camera': {'view_pitch': -12, 'root_y': 0},
        'shared_args': ['--view-pitch', '-12', '--root-y', '0', '--window', '60'],
        'systems': [
            {'id': 'A', 'builder': 'cascade', 'source': str(dump), 'offset_s': 0.0, 'offset_frames': 0,
             'offset_reason': 'test: no sequence',
             'builds': [{'name': 'A_Sprites', 'only': ['Dot', 'Burst'], 'args': ['--bloom', '0']},
                        {'name': 'A_Mesh', 'only': ['ScrollMesh']}],
             'excluded': {n: 'not part of this test' for n in names if n not in keep}},
            {'id': 'B', 'builder': 'cascade', 'source': str(hold), 'offset_s': 0.5, 'offset_frames': 30,
             'offset_reason': 'test: no sequence',
             'builds': [{'name': 'B_Mortal', 'only': ['Mortal']}],
             'excluded': {n: 'not part of this test' for n in hold_names if n != 'Mortal'}},
        ],
        'variants': {'main': {}, 'big': {'builds': {'A_Sprites': {'args': ['--layer-scale', 'Dot=2']}}}},
        'combine': {'name': 'TestFx-{variant}', 'end_frame': 90},
    }
    if seq:
        m['sequence'] = {'name': 'Seq_Test', 'file': str(seq)}
    return m


def write(path, m):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(m, indent=1), encoding='utf-8')
    return path


def unit_tests(work, exports, dump, hold, fields_dump, meshes):
    # ---- efkinfo
    f = work / 'info_new.efkefc'
    f.write_bytes(info_chunk(1810, [(1, 'Texture/a.png'), (3, 'Model/m.efkmodel'), (4, 'Materials/x.efkmat')]))
    (work / 'Texture').mkdir(exist_ok=True)
    (work / 'Texture' / 'a.png').write_bytes(b'x')
    check('efkinfo reads a 1.6+ dependency list (type and relative path)',
          efkinfo.resources(f) == [('texture', 'Texture/a.png'), ('model', 'Model/m.efkmodel'), ('material', 'Materials/x.efkmat')],
          efkinfo.resources(f))
    check('efkinfo.missing lists the resources not beside the file',
          efkinfo.missing(f) == [('model', 'Model/m.efkmodel'), ('material', 'Materials/x.efkmat')], efkinfo.missing(f))
    f2 = work / 'info_old.efkefc'
    f2.write_bytes(info_chunk(1500, [['t.png'], [], [], ['m.efkmodel'], [], [], []]))
    check('efkinfo reads the <= 1600 seven-list layout', efkinfo.resources(f2) == [('texture', 't.png'), ('model', 'm.efkmodel')],
          efkinfo.resources(f2))

    # ---- manifest
    m = base_manifest(exports, dump, hold, meshes)
    check('a good manifest has no problems', MF.problems(m) == [], MF.problems(m))
    check('flag_values reads --f v, --f=v and bare switches',
          MF.flag_values(['--a', '1', '--b=2', '--c', '--a', '3'], '--a') == ['1', '3']
          and MF.flag_values(['--b=2'], '--b') == ['2'] and MF.flag_values(['--c'], '--c') == [True])
    bl = MF.builds(m, 'big')
    argv = dict((b['name'], a) for s, b, a in bl)
    a_spr = argv['A_Sprites']
    check('builds(): work placeholder, source, shared + build + variant args, manifest-owned flags injected',
          a_spr[0] is None and a_spr[1] == str(dump) and a_spr[2:8] == ['--view-pitch', '-12', '--root-y', '0', '--window', '60']
          and MF.flag_values(a_spr, '--name') == ['A_Sprites'] and MF.flag_values(a_spr, '--only') == ['Dot,Burst']
          and MF.flag_values(a_spr, '--layer-scale') == ['Dot=2'] and MF.flag_values(a_spr, '--exports') == [str(exports)]
          and MF.flag_values(a_spr, '--meshes') == [str(meshes)] and MF.flag_values(a_spr, '--time-offset') == ['0.0']
          and not MF.flag_values(a_spr, '--efk18'), a_spr)
    check('builds(): the B system gets its own --time-offset (0.5) and no variant args of A',
          MF.flag_values(argv['B_Mortal'], '--time-offset') == ['0.5'] and not MF.flag_values(argv['B_Mortal'], '--layer-scale'),
          argv['B_Mortal'])
    check('runtime.efk18 injects --efk18', MF.flag_values(MF.builds(dict(m, runtime={'efk18': True}), 'main')[0][2], '--efk18') == [True])

    def probs_after(fn):
        mm = copy.deepcopy(m)
        fn(mm)
        return MF.problems(mm)
    cases = [
        ('offset_frames that is not offset_s x 60', lambda mm: mm['systems'][1].update(offset_frames=29), 'offset_frames 29'),
        ('a sequence offset that differs without offset_reason',
         lambda mm: mm['systems'][1].update(sequence_offset_s=0.7, offset_reason=''), 'offset_reason is empty'),
        ('an emitter in two builds', lambda mm: mm['systems'][0]['builds'][1]['only'].append('Dot'), 'it would draw twice'),
        ('a --time-offset in args that differs from offset_s',
         lambda mm: mm['systems'][1].update(args=['--time-offset', '0.4']), '--time-offset'),
        ('a camera.view_pitch the args contradict', lambda mm: mm['camera'].update(view_pitch=-10), 'camera.view_pitch'),
        ('a camera.turn the build leaves at its default', lambda mm: mm['camera'].update(turn=90), 'camera.turn 90'),
        ('several builds without combine.name', lambda mm: mm.pop('combine'), 'combine.name missing'),
        ('--name given in args', lambda mm: mm['shared_args'].extend(['--name', 'X']), 'the manifest owns them'),
        ('a missing --exposure-curve file', lambda mm: mm['shared_args'].extend(['--exposure-curve', str(work / 'nope.csv')]),
         '--exposure-curve'),
        ('an excluded emitter without a reason', lambda mm: mm['systems'][0]['excluded'].update(Sheet=''), 'without a reason'),
        ('a variant naming an unknown build', lambda mm: mm['variants']['big']['builds'].update(Nope={}), 'build Nope'),
        ('a missing inputs.meshes folder', lambda mm: mm['inputs'].update(meshes=str(work / 'gone')), 'inputs.meshes'),
        ('a vector field root that is not a folder', lambda mm: mm['inputs'].update(vector_fields=[str(work / 'gone')]),
         'inputs.vector_fields'),
    ]
    for label, fn, want in cases:
        pr = probs_after(fn)
        check('manifest problem: %s' % label, any(want in p for p in pr), pr)

    # ---- preflight on the builder test's own systems
    res = PF.run(m)
    st = {(r['system'], r['name']): r['status'] for r in res['emitters']}
    check('preflight passes the test manifest', res['ok'], res['problems'])
    check('preflight: built emitters ok, the rest excluded with their reason, Off switched off',
          st[('A', 'Dot')] == st[('A', 'ScrollMesh')] == st[('B', 'Mortal')] == 'ok' and st[('A', 'Sheet')] == 'excluded'
          and st[('A', 'Off')] == 'off', st)
    mm = copy.deepcopy(m)
    mm['systems'][0]['excluded'] = {}
    mm['systems'][0]['builds'][0]['only'] = ['Dot', 'Burst', 'Bare', 'Fielded']
    mm['systems'][0]['excluded'] = {n: 'test' for n in m['systems'][0]['excluded'] if n not in ('Bare', 'Fielded', 'Sheet')}
    res = PF.run(mm)
    st = {r['name']: r for r in res['emitters'] if r['system'] == 'A'}
    check('preflight: a texture without a png stops the job (Bare: T_Missing)',
          st['Bare']['status'] == 'MISSING' and 'T_Missing' in st['Bare']['reason'] and not res['ok'], st['Bare'])
    check('preflight: a vector field without its voxels stops the job (Fielded: VF_Missing)',
          st['Fielded']['status'] == 'MISSING' and 'VF_Missing' in st['Fielded']['reason'], st['Fielded'])
    check('preflight: an enabled emitter in no build and not excluded stops the job (Sheet)',
          st['Sheet']['status'] == 'MISSING' and 'in no build' in st['Sheet']['reason'], st['Sheet'])
    check('preflight problems name each emitter', all(any(n in p for p in res['problems']) for n in ('Bare', 'Fielded', 'Sheet')),
          res['problems'])
    mm['systems'][0]['accept'] = {'Bare': 'test: drawn without its texture is fine here'}
    res = PF.run(mm)
    st = {r['name']: r for r in res['emitters'] if r['system'] == 'A'}
    check('preflight: accept with a reason turns a missing input into an accepted gap',
          st['Bare']['status'] == 'accepted' and 'drawn without' in st['Bare']['reason'], st['Bare'])
    mm = copy.deepcopy(m)
    mm['inputs']['meshes'] = str(work / 'no-meshes-here')
    res = PF.run(mm)
    st = {r['name']: r for r in res['emitters'] if r['system'] == 'A'}
    check('preflight: a removed mesh folder fails the folder and lists the mesh emitter it breaks',
          not res['ok'] and any('inputs.meshes' in p for p in res['problems'])
          and st['ScrollMesh']['status'] == 'MISSING' and 'M_Quad not imported' in st['ScrollMesh']['reason'], res['problems'])
    # vector field with voxels (P_Fields + <work>/fields), and without NIAGARA_FIELDS
    mf = {'schema': 'effectjob/1', 'effect': 'F', 'game': 'g', 'skill': 's', 'runtime': {'efk18': False},
          'exports': str(exports), 'shared_args': [],
          'systems': [{'id': 'F', 'builder': 'cascade', 'source': str(fields_dump), 'offset_s': 0, 'offset_frames': 0,
                       'builds': [{'name': 'F_All'}]}]}
    res_without = PF.run(mf)
    res_with = PF.run(dict(mf, inputs={'vector_fields': [str(exports.parent / 'fields')]}))
    sw = {r['name']: r for r in res_with['emitters']}
    so = {r['name']: r for r in res_without['emitters']}
    check('preflight: FJet finds VF_Push under inputs.vector_fields', sw['FJet']['status'] == 'ok'
          and 'VF_Push' in str(sw['FJet']['inputs'].get('vector field')), sw['FJet'])
    check('preflight: without inputs.vector_fields the same emitter is missing its voxels',
          so['FJet']['status'] == 'MISSING' and 'not pulled' in so['FJet']['reason'], so['FJet'])
    # a Niagara system: mesh renderers checked against inputs.meshes, materials said to be unchecked
    summ = work / 'summaries' / 'N_Test' / 'summary.json'
    summ.parent.mkdir(parents=True)
    summ.write_text(json.dumps({'system': 'N_Test', 'emitters': [
        {'handle': 'Slash', 'enabled': True, 'renderers': [{'type': 'Mesh', 'Meshes': [{'Mesh': 'M_Quad'}]}]},
        {'handle': 'Cut', 'enabled': True, 'renderers': [{'type': 'Mesh', 'Meshes': [{'Mesh': 'M_Gone'}]}]},
        {'handle': 'Dots', 'enabled': True, 'renderers': [{'type': 'Sprite'}]},
        {'handle': 'Old', 'enabled': False, 'renderers': [{'type': 'Sprite'}]}]}))
    mn = {'schema': 'effectjob/1', 'effect': 'N', 'game': 'g', 'skill': 's', 'runtime': {'efk18': True},
          'exports': str(exports), 'inputs': {'meshes': str(meshes)}, 'shared_args': [],
          'systems': [{'id': 'N', 'builder': 'niagara', 'source': str(summ), 'offset_s': 0, 'offset_frames': 0,
                       'builds': [{'name': 'N_All'}]}]}
    res = PF.run(mn)
    sn = {r['name']: r for r in res['emitters']}
    check('preflight (Niagara): imported mesh ok, a missing mesh stops the job, a switched-off emitter is off',
          sn['Slash']['status'] == 'ok' and sn['Cut']['status'] == 'MISSING' and 'M_Gone' in sn['Cut']['reason']
          and sn['Dots']['status'] == 'ok' and sn['Old']['status'] == 'off' and not res['ok'], sn)
    check('preflight (Niagara): the record says materials/textures are not checked',
          'not checked' in sn['Dots']['inputs'].get('materials/textures', ''), sn['Dots'])
    bad = copy.deepcopy(mn)
    bad['systems'][0]['builds'][0]['only'] = ['Slash']
    check('manifest problem: --only on a Niagara build', any('no --only' in p for p in MF.problems(bad)), MF.problems(bad))
    nargv = MF.builds(mn, 'main')[0][2]
    check('builds(): a Niagara build gets --efk18 and --exports but no --meshes/--time-offset (build_niagara has neither)',
          MF.flag_values(nargv, '--efk18') == [True] and not MF.flag_values(nargv, '--meshes')
          and not MF.flag_values(nargv, '--time-offset'), nargv)

    # a material whose master has no recipe, and an emitter without a material (a helper)
    d = json.loads(Path(dump).read_text())
    for e in d:
        if e.get('Name') == 'Req_Dot':
            e['Properties'].pop('Material')
        if e.get('Name') == 'Req_Burst':
            e['Properties']['Material'] = TB.mref('Mi_Nope')
    (exports / 'Game/Content/Mat/FxMM_Nope.json').write_text(json.dumps([{'Type': 'Material', 'Name': 'FxMM_Nope', 'Properties': {}}]))
    (exports / 'Game/Content/Mat/Mi_Nope.json').write_text(json.dumps([{'Type': 'MaterialInstanceConstant', 'Name': 'Mi_Nope',
        'Properties': {'Parent': {'ObjectName': "Material'FxMM_Nope'", 'ObjectPath': 'Game/Content/Mat/FxMM_Nope.0'}}}]))
    mod = Path(dump).with_name('P_Mod.json')
    mod.write_text(json.dumps(d))
    mm = copy.deepcopy(m)
    mm['systems'][0]['source'] = str(mod)
    res = PF.run(mm)
    st = {r['name']: r for r in res['emitters'] if r['system'] == 'A'}
    check('preflight: a master without a recipe stops the job', st['Burst']['status'] == 'MISSING'
          and 'no recipe for material master FxMM_Nope' in st['Burst']['reason'], st['Burst'])
    check('preflight: an emitter without a material is the builder\'s own helper skip, not a gap',
          st['Dot']['status'] == 'builder-skip' and 'helper' in st['Dot']['reason'], st['Dot'])


def e2e(work, exports, dump, hold, meshes):
    seq = work / 'seq.json'
    seq.write_text(json.dumps({'name': 'Seq_Test', 'bindings': [
        {'binding': 'Fx A', 'type': 'Emitter', 'template': 'Game/Content/FX/P_Build',
         'tracks': [{'track': 'Particle', 'keys': [[0.0, 'trigger']]}]},
        {'binding': 'Fx B', 'type': 'Emitter', 'template': 'Game/Content/FX/P_Hold',
         'tracks': [{'track': 'Particle', 'keys': [[0.5, 'trigger']]}]},
        {'binding': 'Fx C', 'type': 'Emitter', 'template': 'Game/Content/FX/P_Fields',
         'tracks': [{'track': 'Particle', 'keys': [[1.0, 'trigger']]}]},
        {'binding': 'Light', 'type': 'PointLight', 'tracks': []}]}))
    m = base_manifest(exports, dump, hold, meshes, seq)
    task = work / 'job'
    mf = write(task / 'effect.json', m)
    code, out = effectjob('run', mf)
    (work / 'run1.log').write_text(out, encoding='utf-8')
    check('run: builds both variants (exit 1 only for the coverage gap below)', code == 1 and 'BUILD PASS main' in out
          and 'BUILD PASS big' in out, out[-1500:])
    for v in ('main', 'big'):
        for b in ('A_Sprites', 'A_Mesh', 'B_Mortal'):
            rec = json.loads((task / v / 'build' / (b + '.argv.json')).read_text()) if (task / v / 'build' / (b + '.argv.json')).is_file() else {}
            check('run %s: %s.argv.json holds the exact argv, its exit 0 and the builder argv' % (v, b),
                  rec.get('exit') == 0 and str(task / v) in rec.get('argv', []) and rec['builder_argv'][1] == str(task / v)
                  and '--time-offset' in rec['builder_argv'], rec)
    big = json.loads((task / 'big' / 'build' / 'A_Sprites.argv.json').read_text())['argv']
    check('run big: the variant\'s --layer-scale reached its build only', 'Dot=2' in big
          and 'Dot=2' not in json.loads((task / 'main' / 'build' / 'A_Sprites.argv.json').read_text())['argv'])
    spec = json.loads((task / 'main' / 'build' / 'TestFx-main.edit.json').read_text())
    offs = [e['set']['CommonValues/GenerationTimeOffset'] for e in spec['edits'] if 'add' in e and e['node'] == 'root']
    check('combine: one host per build, delayed by its system\'s offset_frames (0, 0, 30)', offs == [0, 0, 30], offs)
    lj = json.loads((task / 'ledger.json').read_text())
    rows = lj['variants']['main']['rows']
    by = {r['key']: r for r in rows}
    check('ledger: the sequence system missing from the manifest is a row with a gap (P_Fields)',
          by.get('P_Fields/*', {}).get('status') == 'GAP' and 'P_Fields' in by['P_Fields/*']['cells']['in sequence']['text'], by.get('P_Fields/*'))
    check('ledger: coverage INCOMPLETE because of it', 'COVERAGE INCOMPLETE main' in out, out[-800:])
    dot = next(r for r in rows if r['system'] == 'A' and r['emitter'] == 'Dot')
    check('ledger: Dot built, saved under host 1, sequence trigger matched',
          dot['status'] == 'ok' and dot['cells']['built']['text'].startswith('A_Sprites: Dot')
          and '(host 1)' in dot['cells']['saved']['text'] and dot['cells']['in sequence']['state'] == 'ok', dot)
    mesh_row = next(r for r in rows if r['emitter'] == 'ScrollMesh')
    check('ledger: ScrollMesh saved under host 2', '(host 2)' in mesh_row['cells']['saved']['text'], mesh_row)
    off = next(r for r in rows if r['emitter'] == 'Off')
    check('ledger: a switched-off emitter is explained, not missing', off['status'] == 'ok'
          and off['cells']['preflight']['state'] == 'explained', off)
    check('ledger: every emitter of both systems has a row (P_Build + P_Hold)',
          len([r for r in rows if r['system'] == 'A' and r['emitter'] != '*']) == len(__import__('cascade').Dump(dump, exports).emitters())
          and len([r for r in rows if r['system'] == 'B' and r['emitter'] != '*']) == len(__import__('cascade').Dump(hold, exports).emitters()))
    srow = by.get('B/*', {})
    check('ledger: each manifest system has a row with its sequence trigger and offset',
          srow.get('kind') == 'system' and 'sequence trigger 0.5 s = plays at 0.5 s (30 f)' in srow['cells']['in sequence']['text'], srow)
    md = (task / 'ledger.md').read_text(encoding='utf-8')
    check('ledger.md has the result lines and both variants', '## Result lines' in md and '## Variant main' in md
          and '## Variant big' in md and 'VISUAL: not judged' in md, md[:600])

    # the sequence's third system skipped with a reason, an oracle report, a capture, an install target
    m['sequence']['skip'] = {'P_Fields': 'test: not rebuilt'}
    (task / 'oracle').mkdir(exist_ok=True)
    (task / 'oracle' / 'report.json').write_text(json.dumps({'layers': {'Dot': {'drawn px': 120}, 'Burst': {'drawn px': 0}},
                                                            'not drawn': {'ScrollMesh': 'mesh M_Quad not exported'}}))
    m['oracle'] = {'systems': {'A': {'report': str(task / 'oracle' / 'report.json'), 'frames': '0:60'}}}
    (task / 'labels.json').write_text(json.dumps([{'eid': 10, 'label': 'dots'}, {'eid': 11, 'label': 'scene'}]))
    (task / 'fake.rdc').write_bytes(b'')
    m['renderdoc'] = [{'rdc': str(task / 'fake.rdc'), 'event_ids': [10, 99], 'labels': str(task / 'labels.json'),
                       'label_map': {'dots': ['A/Dot']}}]
    proj = work / 'project'
    (proj / 'data').mkdir(parents=True)
    (proj / 'data' / 'Animations.json').write_text('[null]')
    m['install'] = {'project': str(proj), 'dest': 'Test/TestFx-{variant}', 'name': 'Test {variant}'}
    write(mf, m)
    code, out = effectjob('ledger', mf)
    (work / 'ledger2.log').write_text(out, encoding='utf-8')
    lj = json.loads((task / 'ledger.json').read_text())
    rows = lj['variants']['main']['rows']
    g = lambda n, k: next(r for r in rows if r['system'] == 'A' and r['emitter'] == n)['cells'][k]
    check('ledger: the BUILD lines of the run come back from disk in a later ledger call',
          'BUILD PASS main' in out and 'BUILD PASS big' in out, out[-800:])
    check('ledger: a skipped sequence system is explained', next(r for r in rows if r['key'] == 'P_Fields/*')['status'] == 'ok')
    check('ledger: oracle column = drawn px / 0 px / oracle-gap from report.json',
          g('Dot', 'oracle')['text'] == 'drawn 120 px' and g('Burst', 'oracle')['state'] == 'explained'
          and '0 px' in g('Burst', 'oracle')['text'] and g('ScrollMesh', 'oracle')['text'].startswith('oracle-gap: mesh'),
          [g(n, 'oracle') for n in ('Dot', 'Burst', 'ScrollMesh')])
    check('ledger: an oracle-less system leaves the column n/a',
          next(r for r in rows if r['system'] == 'B' and r['emitter'] == 'Mortal')['cells']['oracle']['state'] == 'na')
    check('ledger: capture column matches labelled draws; others say no draw',
          g('Dot', 'capture')['state'] == 'ok' and '"dots": fake.rdc'[:8] in g('Dot', 'capture')['text']
          and 'x1' in g('Dot', 'capture')['text']
          and g('Burst', 'capture')['state'] == 'explained', [g('Dot', 'capture'), g('Burst', 'capture')])
    check('ledger: a listed event id missing from labels.json is a job note',
          any('event 99' in n for n in lj['variants']['main']['notes']), lj['variants']['main']['notes'])
    check('ledger: not installed is an explicit state', g('Dot', 'installed')['state'] == 'explained'
          and 'not installed' in g('Dot', 'installed')['text'])
    check('ledger: complete once the gap is explained (exit 0, EFFECT JOB COMPLETE)', code == 0 and 'EFFECT JOB COMPLETE' in out,
          out[-1200:])
    # "installed": the final file copied without its resources, then with them
    final = task / 'main' / 'build' / 'TestFx-main.efkefc'
    dest = proj / 'effects' / 'Test' / 'TestFx-main.efkefc'
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final, dest)
    code, out = effectjob('ledger', mf, '--variants', 'main')
    rows = json.loads((task / 'ledger.json').read_text())['variants']['main']['rows']
    check('ledger: an installed file whose resources are not in the project is a gap',
          g('Dot', 'installed')['state'] == 'gap' and 'resource' in g('Dot', 'installed')['text'] and code == 1,
          g('Dot', 'installed'))
    for _, rel in efkinfo.resources(final):
        (dest.parent / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final.parent / rel, dest.parent / rel)
    code, out = effectjob('ledger', mf, '--variants', 'main')
    rows = json.loads((task / 'ledger.json').read_text())['variants']['main']['rows']
    check('ledger: with its resources the installed file resolves', g('Dot', 'installed')['state'] == 'ok' and code == 0
          and 'byte-identical' in (task / 'ledger.md').read_text(encoding='utf-8'), (g('Dot', 'installed'), out[-600:]))
    # notes of one build gone: no status for its rows
    notes = task / 'main' / 'build' / 'B_Mortal.notes.txt'
    keep = notes.read_bytes()
    notes.unlink()
    code, out = effectjob('ledger', mf, '--variants', 'main')
    rows = json.loads((task / 'ledger.json').read_text())['variants']['main']['rows']
    mortal = next(r for r in rows if r['emitter'] == 'Mortal')
    check('ledger: a build with no notes on disk leaves its rows without status: INCOMPLETE',
          mortal['status'] == 'INCOMPLETE' and code == 1 and 'without status' in out, (mortal, out[-500:]))
    notes.write_bytes(keep)
    # rerun without --replace refuses; a removed mesh folder stops a fresh job before any build
    code, out = effectjob('run', mf)
    check('run: refuses to write into an existing variant folder without --replace', code == 2 and 'exists' in out, out[-300:])
    m2 = copy.deepcopy(m)
    m2['inputs']['meshes'] = str(work / 'meshes-removed')
    shutil.copytree(meshes, work / 'meshes-removed')
    mf2 = write(work / 'job2' / 'effect.json', m2)
    code, out = effectjob('preflight', mf2)
    check('preflight with the mesh folder present passes', code == 0 and 'PREFLIGHT OK' in out, out[-500:])
    shutil.rmtree(work / 'meshes-removed')
    code, out = effectjob('run', mf2)
    check('run with the mesh folder removed: PREFLIGHT FAIL naming ScrollMesh, exit 2, no variant folder built',
          code == 2 and 'PREFLIGHT FAIL' in out and 'ScrollMesh' in out and 'M_Quad not imported' in out
          and not (work / 'job2' / 'main').exists() and 'VERIFY OK' not in out, out[-800:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    work = Path(a.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    exports = work / 'exports'
    dump = TB.write_tree(exports)
    hold = dump.with_name('P_Hold.json')
    fields_dump = dump.with_name('P_Fields.json')
    sys.path.insert(0, str(ROOT / 'tools' / 'meshimport'))
    import meshimport as MI
    meshes = work / 'mesh' / 'Model'
    meshes.mkdir(parents=True)
    quad = [[x, 0.0, z, 0, 1, 0, 0, 0, 1, 1, 0, 0, (x + 1) / 2, (z + 1) / 2] for x, z in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    (meshes / 'M_Quad.efkmodel').write_bytes(MI.model_bytes(quad, [[0, 2, 1], [0, 3, 2]]))
    unit_tests(work, exports, dump, hold, fields_dump, meshes)
    if not MF.TOOL_170.is_file():
        check('the 1.70e tool is built (%s)' % MF.TOOL_170, False)
    else:
        e2e(work, exports, dump, hold, meshes)
    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
