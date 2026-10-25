"""Unity / Shuriken / Studio tool checks on synthetic fixtures. All files go under --work.

python tools/unity/test_unity.py --work out/tests/unity

One prefab is written twice: as Unity YAML (with an assets folder of .meta/.mat/.shader/.cs files) and as fake
UnityPy object readers passed through unity_dump's Dumper; both must summarise to the same systems. No game data
is used. PyYAML is required.
"""
import argparse
import csv
import io
import json
import math
import shutil
import sys
import tarfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx_reference_index as OX  # noqa: E402
import shuriken as S  # noqa: E402
import shuriken_summary as SS  # noqa: E402
import unity_dump as UD  # noqa: E402
import unityasset as U  # noqa: E402
import yaml  # noqa: E402

WORK = None
INF = float('inf')
MAT_GUID, TEX_GUID, SHADER_GUID, SCRIPT_GUID = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32
MISSING_GUID = 'e' * 32


# --- fixture values ---------------------------------------------------------------------------------------------

class R:
    """A reference to an object of the same file (by fixture key)."""

    def __init__(self, key):
        self.key = key


class A:
    """A reference into another asset: YAML (guid, fileID), dump (external file, path id)."""

    def __init__(self, guid, file_id, ext, path_id):
        self.guid, self.file_id, self.ext, self.path_id = guid, file_id, ext, path_id


NULL = R(None)


def key(t, v, i=None, o=None, wm=0, iw=1 / 3, ow=1 / 3):
    return {'serializedVersion': 3, 'time': t, 'value': v, 'inSlope': 0.0 if i is None else i,
            'outSlope': 0.0 if o is None else o, 'tangentMode': 0, 'weightedMode': wm, 'inWeight': iw, 'outWeight': ow}


def curve(keys=()):
    return {'serializedVersion': 2, 'm_Curve': list(keys), 'm_PreInfinity': 2, 'm_PostInfinity': 2,
            'm_RotationOrder': 4}


def mm(state=0, scalar=0.0, min_scalar=0.0, max_keys=(), min_keys=()):
    return {'serializedVersion': 2, 'minMaxState': state, 'scalar': scalar, 'minScalar': min_scalar,
            'maxCurve': curve(max_keys), 'minCurve': curve(min_keys)}


def color(r, g, b, a=1.0):
    return {'r': r, 'g': g, 'b': b, 'a': a}


def gradient(ckeys, akeys, mode=0):
    g = {'serializedVersion': 2}
    for i in range(8):
        rgb = ckeys[i][1] if i < len(ckeys) else (0, 0, 0)
        a = akeys[i][1] if i < len(akeys) else 0
        g['key%d' % i] = color(*rgb, a)
    for i in range(8):
        g['ctime%d' % i] = round(ckeys[i][0] * 65535) if i < len(ckeys) else 0
    for i in range(8):
        g['atime%d' % i] = round(akeys[i][0] * 65535) if i < len(akeys) else 0
    g.update(m_Mode=mode, m_NumColorKeys=len(ckeys), m_NumAlphaKeys=len(akeys))
    return g


def mmg(state, max_color=(1, 1, 1, 1), min_color=(1, 1, 1, 1), max_gradient=None, min_gradient=None):
    white = gradient([(0, (1, 1, 1)), (1, (1, 1, 1))], [(0, 1), (1, 1)])
    return {'serializedVersion': 2, 'minMaxState': state, 'minColor': color(*min_color),
            'maxColor': color(*max_color), 'maxGradient': max_gradient or white, 'minGradient': min_gradient or white}


def module(enabled, **fields):
    return {'enabled': enabled, **fields}


def particle_system(go, *, duration, looping, life, rate, bursts=(), sub=None, rotation=None, uv=None, color_mod=None,
                    size_mod=None):
    d = {'m_ObjectHideFlags': 0, 'm_GameObject': R(go), 'serializedVersion': 8, 'lengthInSec': duration,
         'simulationSpeed': 1.0, 'stopAction': 2, 'cullingMode': 0, 'ringBufferMode': 0,
         'ringBufferLoopRange': {'x': 0, 'y': 1}, 'emitterVelocityMode': 0, 'looping': looping, 'prewarm': False,
         'playOnAwake': True, 'useUnscaledTime': False, 'autoRandomSeed': True, 'startDelay': mm(0, 0.0),
         'moveWithTransform': 1, 'moveWithCustomTransform': NULL, 'scalingMode': 1, 'randomSeed': 0}
    d['InitialModule'] = module(
        True, startLifetime=life, startSpeed=mm(0, 5.0), startColor=mmg(0, max_color=(1, 0.5, 0.25, 1)),
        startSize=mm(3, 0.5, 0.25), startSizeY=mm(0, 1.0), startSizeZ=mm(0, 1.0), startRotationX=mm(0, 0.0),
        startRotationY=mm(0, 0.0), startRotation=rotation or mm(3, math.pi, 0.0), randomizeRotationDirection=0.0,
        gravitySource=0, maxNumParticles=200, customEmitterVelocity={'x': 0, 'y': 0, 'z': 0}, size3D=False,
        rotation3D=False, gravityModifier=mm(0, 0.5))
    d['ShapeModule'] = module(True, type=4, angle=25.0, length=5.0, boxThickness={'x': 0, 'y': 0, 'z': 0},
                              radiusThickness=1.0, donutRadius=0.2, m_Position={'x': 0, 'y': 0, 'z': 0},
                              m_Rotation={'x': -90, 'y': 0, 'z': 0}, m_Scale={'x': 1, 'y': 1, 'z': 1},
                              placementMode=0, m_Mesh=NULL,
                              radius={'value': 0.5, 'mode': 0, 'spread': 0.0, 'speed': mm(0, 1.0)},
                              arc={'value': 360.0, 'mode': 1, 'spread': 0.0, 'speed': mm(0, 1.0)})
    d['EmissionModule'] = module(True, serializedVersion=4, rateOverTime=rate, rateOverDistance=mm(0, 0.0),
                                 m_BurstCount=len(bursts), m_Bursts=list(bursts))
    d['SizeModule'] = size_mod or module(False, curve=mm(1, 1.0, 0, [key(0, 1, 0, 0), key(1, 1, 0, 0)]),
                                         y=mm(0, 1.0), z=mm(0, 1.0), separateAxes=False)
    d['RotationModule'] = module(False, x=mm(0, 0.0), y=mm(0, 0.0), curve=mm(0, math.pi / 4), separateAxes=False)
    d['ColorModule'] = color_mod or module(False, gradient=mmg(1))
    d['UVModule'] = uv or module(False, mode=0, timeMode=0, fps=30.0, frameOverTime=mm(1, 0.9999, 0, [
        key(0, 0, 1, 1), key(1, 1, 1, 1)]), startFrame=mm(0, 0.0), speedRange={'x': 0, 'y': 1}, tilesX=1, tilesY=1,
        animationType=0, rowIndex=0, cycles=1.0, uvChannelMask=-1, rowMode=1, sprites=[], flipU=0.0, flipV=0.0)
    d['VelocityModule'] = module(False, x=mm(0, 0.0), y=mm(0, 0.0), z=mm(0, 0.0), inWorldSpace=False)
    d['ForceModule'] = module(False, x=mm(0, 0.0), y=mm(0, -1.0), z=mm(0, 0.0), inWorldSpace=True,
                              randomizePerFrame=False)
    d['NoiseModule'] = module(False, strength=mm(0, 1.0), frequency=0.5, quality=2)
    d['SubModule'] = sub or module(False, subEmitters=[])
    d['TrailModule'] = module(False, mode=0, ratio=1.0, lifetime=mm(0, 1.0))
    return d


def renderer(go, materials, mode=0):
    return {'m_ObjectHideFlags': 0, 'm_GameObject': R(go), 'm_Enabled': True, 'm_Materials': list(materials),
            'm_RenderMode': mode, 'm_SortMode': 0, 'm_MinParticleSize': 0.0, 'm_MaxParticleSize': 0.5,
            'm_RenderAlignment': 0, 'm_Pivot': {'x': 0, 'y': 0, 'z': 0}, 'm_Flip': {'x': 0, 'y': 0, 'z': 0},
            'm_Mesh': NULL, 'm_Mesh1': NULL, 'm_Mesh2': NULL, 'm_Mesh3': NULL}


def gameobject(name, comps, active=True):
    return {'m_ObjectHideFlags': 0, 'serializedVersion': 6, 'm_Component': [{'component': R(c)} for c in comps],
            'm_Layer': 0, 'm_Name': name, 'm_IsActive': active}


def transform(go, father, children, pos=(0, 0, 0), rot=(0, 0, 0, 1)):
    return {'m_ObjectHideFlags': 0, 'm_GameObject': R(go), 'm_LocalRotation': dict(zip('xyzw', rot)),
            'm_LocalPosition': dict(zip('xyz', pos)), 'm_LocalScale': {'x': 1, 'y': 1, 'z': 1},
            'm_Children': [R(c) for c in children], 'm_Father': R(father)}


def material_data():
    return {'m_ObjectHideFlags': 0, 'm_Name': 'M_Spark_Add', 'm_Shader': A(SHADER_GUID, 4800000, 'shaders', 7),
            'm_ValidKeywords': ['_ALPHABLEND_ON'], 'm_CustomRenderQueue': 3000,
            'stringTagMap': [['RenderType', 'Transparent']],
            'm_SavedProperties': {'serializedVersion': 3,
                                  'm_TexEnvs': [['_MainTex', {'m_Texture': A(TEX_GUID, 2800000, 'textures', 9),
                                                              'm_Scale': {'x': 2, 'y': 1},
                                                              'm_Offset': {'x': 0, 'y': 0}}],
                                                ['_BumpMap', {'m_Texture': NULL, 'm_Scale': {'x': 1, 'y': 1},
                                                              'm_Offset': {'x': 0, 'y': 0}}]],
                                  'm_Floats': [['_SrcBlend', 5], ['_DstBlend', 1], ['_ZWrite', 0]],
                                  'm_Colors': [['_TintColor', color(1, 0.5, 0, 1)]]}}


def fixture_objects():
    """{key: (class name, class id, data)}: root FX_Test (sparks emitter + custom script) and child Embers,
    the root's Death sub-emitter; the root renderer also points at a material that exists nowhere."""
    root_ps = particle_system(
        'go_root', duration=2.0, looping=False, life=mm(3, 1.5, 0.5), rate=mm(0, 10.0),
        bursts=[{'serializedVersion': 2, 'time': 0.5, 'countCurve': mm(0, 5.0), 'cycleCount': 3,
                 'repeatInterval': 0.5, 'probability': 1.0}],
        sub=module(True, subEmitters=[{'serializedVersion': 3, 'emitter': R('ps_child'), 'type': 2,
                                       'properties': 1 | 2, 'emitProbability': 1.0}]),
        uv=module(True, mode=0, timeMode=0, fps=30.0,
                  frameOverTime=mm(1, 0.9999, 0, [key(0, 0, 1, 1), key(1, 1, 1, 1)]),
                  startFrame=mm(3, 0.5, 0.0), speedRange={'x': 0, 'y': 1}, tilesX=4, tilesY=2, animationType=0,
                  rowIndex=0, cycles=1.0, uvChannelMask=-1, rowMode=1, sprites=[], flipU=0.0, flipV=0.0),
        color_mod=module(True, gradient=mmg(1, max_gradient=gradient(
            [(0, (1, 1, 1)), (1, (1, 0, 0))], [(0, 0), (0.25, 1), (1, 0)]))))
    child_ps = particle_system('go_child', duration=1.0, looping=True, life=mm(0, 0.4), rate=mm(0, 0.0),
                               rotation=mm(1, math.pi, 0, [key(0, 0, INF, INF), key(1, 1, 0, 0)]),
                               size_mod=module(True, curve=mm(2, 2.0, 0, [key(0, 1, 0, 0), key(1, 0, 0, 0)],
                                                              [key(0, 0.5, 0, 0), key(1, 0, 0, 0)]),
                                               y=mm(0, 1.0), z=mm(0, 1.0), separateAxes=False))
    return {
        'go_root': ('GameObject', 1, gameobject('FX_Test', ['tr_root', 'ps_root', 'r_root', 'mb_root'])),
        'tr_root': ('Transform', 4, transform('go_root', None, ['tr_child'])),
        'ps_root': ('ParticleSystem', 198, root_ps),
        'r_root': ('ParticleSystemRenderer', 199, renderer('go_root', [
            A(MAT_GUID, 2100000, 'materials', 5), A(MISSING_GUID, 2100000, 'missing.bundle', 77)])),
        'mb_root': ('MonoBehaviour', 114, {'m_ObjectHideFlags': 0, 'm_GameObject': R('go_root'), 'm_Enabled': True,
                                           'm_Script': A(SCRIPT_GUID, 11500000, 'scripts', 3), 'm_Name': '',
                                           'FadeOutTime': 0.25, 'Locator': 'Locator_WeaponMuzzleFX_00'}),
        'go_child': ('GameObject', 1, gameobject('Embers', ['tr_child', 'ps_child', 'r_child'], active=False)),
        'tr_child': ('Transform', 4, transform('go_child', 'tr_root', [], pos=(0, 1, 0),
                                               rot=(0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4)))),
        'ps_child': ('ParticleSystem', 198, child_ps),
        'r_child': ('ParticleSystemRenderer', 199, renderer('go_child', [A(MAT_GUID, 2100000, 'materials', 5)],
                                                            mode=1)),
    }


FILE_IDS = {}


def file_id(k):
    return FILE_IDS.setdefault(k, 100000 + 2 * len(FILE_IDS))


def to_yaml_value(v):
    if isinstance(v, R):
        return {'fileID': 0 if v.key is None else file_id(v.key)}
    if isinstance(v, A):
        return {'fileID': v.file_id, 'guid': v.guid, 'type': 2}
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dict):
        return {k: to_yaml_value(x) for k, x in v.items()}
    if isinstance(v, list):
        if v and all(isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) for x in v):
            return [{x[0]: to_yaml_value(x[1])} for x in v]        # a map, as Unity YAML writes it
        return [to_yaml_value(x) for x in v]
    return v


def write_yaml(path, objects):
    parts = ['%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n']
    for k, (cls, cid, data) in objects.items():
        body = yaml.safe_dump({cls: to_yaml_value(data)}, default_flow_style=False, sort_keys=False)
        parts.append('--- !u!%d &%d\n%s' % (cid, file_id(k), body.replace('.inf', 'Infinity')))
    # a stripped object, as nested prefabs leave them: must load and stay out of the hierarchy
    parts.append('--- !u!4 &999 stripped\nTransform:\n  m_CorrespondingSourceObject: {fileID: 400000, '
                 'guid: %s, type: 3}\n  m_PrefabInstance: {fileID: 0}\n' % ('f' * 32))
    path.write_text(''.join(parts), encoding='utf-8')


def write_assets(root):
    root.mkdir(parents=True, exist_ok=True)
    mat = {'m_Shader': A(SHADER_GUID, 4800000, '', 0), **material_data()}
    (root / 'M_Spark_Add.mat').write_text(
        '%%YAML 1.1\n%%TAG !u! tag:unity3d.com,2011:\n--- !u!21 &2100000\n%s' % yaml.safe_dump(
            {'Material': to_yaml_value(mat)}, sort_keys=False), encoding='utf-8')
    (root / 'Spark.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    (root / 'Studio_Particles_Add.shader').write_text('// exported\nShader "Studio/Particles/Additive" {\n}\n',
                                                      encoding='utf-8')
    (root / 'FxFadeOut.cs').write_text('class FxFadeOut {}\n', encoding='utf-8')
    for name, guid in (('M_Spark_Add.mat', MAT_GUID), ('Spark.png', TEX_GUID),
                       ('Studio_Particles_Add.shader', SHADER_GUID), ('FxFadeOut.cs', SCRIPT_GUID)):
        (root / (name + '.meta')).write_text('fileFormatVersion: 2\nguid: %s\n' % guid, encoding='utf-8')


def fresh(name):
    """An empty folder under --work (a rerun starts from nothing)."""
    d = WORK / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


# --- fake UnityPy readers ---------------------------------------------------------------------------------------

class FakeType:
    def __init__(self, name):
        self.name = name


class FakeExternal:
    def __init__(self, path):
        self.path = path


class FakeFile:
    def __init__(self, name, externals=()):
        self.name, self.externals, self.objects, self.unity_version = name, [FakeExternal(e) for e in externals], \
            {}, '6000.0.64f1'


class FakeObj:
    def __init__(self, f, path_id, cls, class_id, data):
        self.assets_file, self.path_id, self.type, self.class_id, self._data = f, path_id, FakeType(cls), class_id, data
        f.objects[path_id] = self

    def parse_as_dict(self, check_read=True):
        if self._data is None:
            raise ValueError('Expected to read 40 bytes, but only read 12 bytes')
        return self._data

    def peek_name(self):
        return self._data.get('m_Name')


def build_fake_env(with_gone=False):
    """The fixture as UnityPy would read it: the prefab in CAB-fx, its material in CAB-shared (external 1),
    the shader/texture/script in CAB-shared too, the missing material in a bundle that was not loaded.
    with_gone also returns the files dict and that bundle's file (CAB-gone), for loading it later."""
    prefab = FakeFile('CAB-fx', externals=['archive:/CAB-shared/CAB-shared', 'archive:/CAB-gone/CAB-gone'])
    shared = FakeFile('CAB-shared')
    files = {'CAB-fx': prefab, 'CAB-shared': shared}
    ext_of = {'materials': 1, 'shaders': 1, 'textures': 1, 'scripts': 1, 'missing.bundle': 2}
    pids = {}

    def pid(k):
        return pids.setdefault(k, 10 + len(pids))

    def tt(v, in_shared=False):
        if isinstance(v, R):
            return {'m_FileID': 0, 'm_PathID': 0 if v.key is None else pid(v.key)}
        if isinstance(v, A):
            return {'m_FileID': 0 if in_shared else ext_of[v.ext], 'm_PathID': v.path_id}
        if isinstance(v, dict):
            return {k: tt(x, in_shared) for k, x in v.items()}
        if isinstance(v, list):
            if v and all(isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) for x in v):
                return [(x[0], tt(x[1], in_shared)) for x in v]
            return [tt(x, in_shared) for x in v]
        return v
    objs = fixture_objects()
    for k, (cls, cid, data) in objs.items():
        d = tt(data)
        if cls == 'MonoBehaviour':
            d['m_Thumbnail'] = b'\x00' * 16          # bytes must be written as a length
        FakeObj(prefab, pid(k), cls, cid, d)
    FakeObj(shared, 5, 'Material', 21, tt(material_data(), True))
    FakeObj(shared, 7, 'Shader', 48, {'m_Name': '', 'm_ParsedForm': {'m_Name': 'Studio/Particles/Additive',
                                                                     'm_SubShaders': ['big']}, 'blob': b'x' * 99})
    FakeObj(shared, 9, 'Texture2D', 28, {'m_Name': 'Spark', 'm_Width': 64, 'm_Height': 32, 'm_TextureFormat': 4,
                                         'image data': b'\xff' * 4096})
    FakeObj(shared, 3, 'MonoScript', 115, {'m_Name': 'FxFadeOut', 'm_ClassName': 'FxFadeOut',
                                           'm_Namespace': 'Studio.Visual.Particles'})

    def resolve(obj, fid, path_id):
        f = obj.assets_file
        if fid:
            name = f.externals[fid - 1].path.rsplit('/', 1)[-1]
            if name not in files:
                raise FileNotFoundError(name)
            f = files[name]
        return f.objects[path_id]
    if with_gone:
        gone = FakeFile('CAB-gone')
        FakeObj(gone, 77, 'Material', 21, {'m_Name': 'M_Extra', 'm_Shader': {'m_FileID': 0, 'm_PathID': 0}})
        return prefab.objects[pid('go_root')], resolve, files, gone
    return prefab.objects[pid('go_root')], resolve


# --- tests ------------------------------------------------------------------------------------------------------

class Curves(unittest.TestCase):
    def test_hermite_linear_flat_step(self):
        lin = S.keys_of(curve([key(0, 0, 1, 1), key(1, 1, 1, 1)]))
        for t in (0, 0.25, 0.5, 1):
            self.assertAlmostEqual(S.eval_keys(lin, t), t)
        flat = S.keys_of(curve([key(0, 0, 0, 0), key(1, 1, 0, 0)]))
        self.assertAlmostEqual(S.eval_keys(flat, 0.25), 3 * 0.25 ** 2 - 2 * 0.25 ** 3)   # smoothstep
        step = S.keys_of(curve([key(0, 2, INF, INF), key(1, 5, 0, 0)]))
        self.assertEqual(S.eval_keys(step, 0.99), 2)
        self.assertEqual(S.eval_keys(step, 1), 5)
        self.assertEqual(S.eval_keys(lin, -1), 0)          # clamped outside the keys
        self.assertEqual(S.eval_keys(lin, 2), 1)
        self.assertEqual(S.eval_keys([], 0.5), 0)

    def test_weighted(self):
        plain = S.keys_of(curve([key(0, 0, 0, 0), key(1, 1, 0, 0)]))
        third = S.keys_of(curve([key(0, 0, 0, 0, wm=3), key(1, 1, 0, 0, wm=3)]))
        for t in (0.1, 0.5, 0.8):
            self.assertAlmostEqual(S.eval_keys(plain, t), S.eval_keys(third, t), places=6)
        heavy = S.keys_of(curve([key(0, 0, 0, 0, wm=2, ow=0.9), key(1, 1, 0, 0, wm=1, iw=0.9)]))
        self.assertLess(S.eval_keys(heavy, 0.3), S.eval_keys(plain, 0.3))     # held longer at 0
        self.assertAlmostEqual(S.eval_keys(heavy, 0.5), 0.5, places=6)        # symmetric

    def test_minmax_modes(self):
        self.assertEqual(S.Curve(mm(0, 3.0)).range(0.7), (3.0, 3.0))
        self.assertEqual(S.Curve(mm(3, 4.0, 1.0)).range(0), (1.0, 4.0))
        c = S.Curve(mm(1, 2.0, 0, [key(0, 0, 1, 1), key(1, 1, 1, 1)]))
        self.assertAlmostEqual(c.range(0.5)[0], 1.0)
        two = S.Curve(mm(2, 10.0, 0, [key(0, 1, 0, 0), key(1, 1, 0, 0)], [key(0, 0, 0, 0), key(1, 0.5, 0, 0)]))
        self.assertEqual(two.range(1), (5.0, 10.0))
        self.assertEqual(two.overall(), (0.0, 10.0))
        s = S.Curve(mm(3, math.pi, 0.0), S.RAD2DEG).summary('deg')
        self.assertEqual((s['min'], s['max'], s['unit']), (0.0, 180.0, 'deg'))

    def test_uv_frames(self):
        uv = {'mode': 0, 'tilesX': 4, 'tilesY': 2, 'animationType': 0, 'frameOverTime': mm(1, 0.9999, 0, [
            key(0, 0), key(1, 1)])}
        d = S.decode(uv['frameOverTime'], 'UVModule', 'frameOverTime', None, uv)
        self.assertAlmostEqual(d['range'][1], 7.9992, places=4)
        uv['animationType'] = 1
        self.assertAlmostEqual(S.decode(uv['frameOverTime'], 'UVModule', 'frameOverTime', None, uv)['range'][1],
                               3.9996, places=4)
        uv.update(mode=1, sprites=[{}, {}, {}])
        self.assertEqual(S.frame_count(uv), 3)


class Gradients(unittest.TestCase):
    def test_blend_fixed_alpha(self):
        g = gradient([(0, (0, 0, 0)), (1, (1, 0.5, 0))], [(0, 1), (0.5, 0)])
        c = S.eval_gradient(g, 0.5)
        self.assertAlmostEqual(c[0], 0.5, places=3)
        self.assertAlmostEqual(c[1], 0.25, places=3)
        self.assertAlmostEqual(c[3], 0.0, places=3)
        self.assertAlmostEqual(S.eval_gradient(g, 0.25)[3], 0.5, places=3)
        fixed = gradient([(0.3, (1, 0, 0)), (0.6, (0, 1, 0)), (1, (0, 0, 1))], [(0, 1)], mode=1)
        self.assertEqual(S.eval_gradient(fixed, 0.1)[:3], [1, 0, 0])
        self.assertEqual(S.eval_gradient(fixed, 0.5)[:3], [0, 1, 0])
        self.assertEqual(S.eval_gradient(fixed, 0.7)[:3], [0, 0, 1])

    def test_perceptual(self):
        g = gradient([(0, (0, 0, 0)), (1, (1, 1, 1))], [(0, 1)], mode=2)
        r, gg, b, _ = S.eval_gradient(g, 0.5)
        self.assertTrue(0.3 < r < 0.45 and abs(r - gg) < 1e-6 and abs(r - b) < 1e-6, (r, gg, b))
        end = S.eval_gradient(g, 1.0)
        self.assertAlmostEqual(end[0], 1.0, places=6)

    def test_summary_modes(self):
        s = S.minmax_gradient_summary(mmg(2, max_color=(1, 0, 0, 1), min_color=(0, 0, 1, 0.5)))
        self.assertEqual((s['mode'], s['min_color'], s['max_color']), ('two_colors', [0, 0, 1, 0.5], [1, 0, 0, 1]))


class Assets(unittest.TestCase):
    def test_euler(self):
        def q_axis(axis, deg):
            h = math.radians(deg) / 2
            v = [0.0, 0.0, 0.0]
            v['xyz'.index(axis)] = math.sin(h)
            return v + [math.cos(h)]

        def mul(a, b):
            ax, ay, az, aw = a
            bx, by, bz, bw = b
            return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                    aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]
        for e in ((30, 45, 60), (-20, 170, 5), (0, 90, 0)):
            q = mul(mul(q_axis('y', e[1]), q_axis('x', e[0])), q_axis('z', e[2]))     # Unity: Z, then X, then Y
            for got, want in zip(U.quat_to_unity_euler(q), e):
                self.assertAlmostEqual(got, want, places=3)

    def test_material_blend(self):
        d = fresh('assets-blend')
        write_assets(d)
        idx = U.AssetIndex([d])
        m = idx.doc(MAT_GUID).objects['2100000']
        s = U.material_summary(m)
        self.assertEqual(s['blend']['kind'], 'additive (alpha-weighted)')
        self.assertEqual(s['shader'], 'Studio/Particles/Additive (Shader file Studio_Particles_Add.shader)')
        self.assertEqual(s['textures']['_MainTex']['scale'], [2.0, 1.0])
        self.assertNotIn('_BumpMap', s['textures'])
        self.assertEqual(s['tags'], {'RenderType': 'Transparent'})

    def test_numbers(self):
        self.assertEqual(U.num('Infinity'), INF)
        self.assertEqual(U.num(None, 3.0), 3.0)
        self.assertTrue(U.flag(1) and U.flag(True) and not U.flag(0) and not U.flag('0'))
        self.assertEqual(U.pairs([{'a': 1}, ['b', 2], {'first': 'c', 'second': 3}]), [('a', 1), ('b', 2), ('c', 3)])


class Timing(unittest.TestCase):
    def test_burst_fires(self):
        self.assertEqual(S.burst_fires(0.5, 3, 0.5, 2.0), 3)
        self.assertEqual(S.burst_fires(0.5, 10, 0.5, 2.0), 4)      # the duration ends first
        self.assertEqual(S.burst_fires(0.0, 0, 0.4, 1.0), 3)       # infinite cycles: 0, 0.4, 0.8
        self.assertEqual(S.burst_fires(3.0, 1, 0.0, 2.0), 0)
        self.assertEqual(S.burst_fires(0.0, 2, 0.0, 2.0), 2)


def summarise_yaml():
    d = fresh('yaml')
    write_assets(d / 'assets')
    write_yaml(d / 'FX_Test.prefab', fixture_objects())
    return SS.summarise(U.UnityDoc.load(d / 'FX_Test.prefab', U.AssetIndex([d / 'assets'])))


def summarise_dump():
    root, resolve = build_fake_env()
    dump = UD.Dumper(resolve).dump(root)
    p = fresh('dump') / 'FX_Test.unitydump.json'
    p.write_text(json.dumps(dump), encoding='utf-8')
    return dump, SS.summarise(U.UnityDoc.load(p))


class Summary(unittest.TestCase):
    def test_yaml_prefab(self):
        s = summarise_yaml()
        self.assertEqual([h['path'] for h in s['hierarchy']], ['FX_Test', 'FX_Test/Embers'])
        self.assertEqual(s['hierarchy'][0]['components'],
                         ['Transform', 'ParticleSystem', 'ParticleSystemRenderer', 'MonoBehaviour FxFadeOut'])
        self.assertFalse(s['hierarchy'][1]['active'])
        self.assertEqual(s['hierarchy'][1]['transform']['euler_deg'], [0.0, 90.0, 0.0])
        root, child = s['systems']
        t = root['timing']
        self.assertEqual((t['particles_per_cycle'], t['last_spawn_s'], t['end_s']), (35.0, 2.0, 3.5))
        self.assertEqual(t['bursts'][0]['fires_in_duration'], 3)
        self.assertIsNone(child['timing']['end_s'])                       # looping
        m = root['modules']
        self.assertEqual(m['InitialModule']['startRotation']['max'], 180.0)
        self.assertEqual(m['InitialModule']['startLifetime'], {'mode': 'two_constants', 'unit': 's', 'min': 0.5,
                                                               'max': 1.5})
        self.assertEqual(m['ShapeModule']['type'], 'Cone')
        self.assertEqual(m['ShapeModule']['arc']['mode'], 'Loop')
        self.assertEqual(m['UVModule']['startFrame'], {'mode': 'two_constants', 'unit': 'frame', 'min': 0.0,
                                                       'max': 4.0})
        self.assertEqual(m['ColorModule']['gradient']['gradient']['alpha_keys'], [[0.0, 0.0], [0.25, 1.0], [1.0, 0.0]])
        self.assertIn('VelocityModule', root['disabled_modules'])
        self.assertEqual(root['sub_emitters'], [{'emitter': 'FX_Test/Embers', 'type': 'Death',
                                                 'inherit': ['Color', 'Size'], 'probability': 1.0}])
        self.assertEqual(child['modules']['InitialModule']['startRotation']['max_keys'][0]['out'], 'inf')
        self.assertEqual(child['modules']['SizeModule']['curve']['samples'][0], [1.0, 2.0])
        self.assertEqual(root['renderer']['RenderMode'], 'Billboard')
        self.assertEqual(child['renderer']['RenderMode'], 'Stretch')
        self.assertEqual(root['renderer']['Materials'][0], 'M_Spark_Add (Material)')
        self.assertIn('not in --assets', root['renderer']['Materials'][1])
        self.assertEqual(len(s['unresolved_materials']), 1)
        self.assertEqual(s['materials'][0]['blend']['kind'], 'additive (alpha-weighted)')
        self.assertEqual(s['other_components'][0]['class'], 'MonoBehaviour FxFadeOut')
        self.assertEqual(s['other_components'][0]['fields']['Locator'], 'Locator_WeaponMuzzleFX_00')
        md = SS.markdown(s)
        self.assertIn('ends by 3.5 s', md)
        self.assertIn('sub-emitter on Death: FX_Test/Embers', md)

    def test_dump_matches_yaml(self):
        dump, sj = summarise_dump()
        sy = summarise_yaml()
        self.assertEqual(dump['unresolved_files'], ['archive:/CAB-gone/CAB-gone'])
        by_class = {o['class']: o for o in dump['objects']}
        self.assertTrue(by_class['Texture2D']['trimmed'])
        self.assertEqual(by_class['Texture2D']['data'], {'m_Name': 'Spark', 'm_Width': 64, 'm_Height': 32,
                                                         'm_TextureFormat': 4})
        self.assertEqual(by_class['Shader']['data'], {'m_Name': '', 'm_ParsedForm': {
            'm_Name': 'Studio/Particles/Additive'}})
        self.assertNotIn('trimmed', by_class['Material'])
        self.assertEqual(by_class['MonoBehaviour']['data']['m_Thumbnail'], {'$bytes': 16})
        for a, b in zip(sy['systems'], sj['systems']):
            self.assertEqual(a['modules'], b['modules'])
            self.assertEqual(a['timing'], b['timing'])
            self.assertEqual(a['sub_emitters'], b['sub_emitters'])
            self.assertEqual(a['main'], b['main'])
        self.assertEqual([h['path'] for h in sy['hierarchy']], [h['path'] for h in sj['hierarchy']])
        self.assertEqual(sj['other_components'][0]['class'], 'MonoBehaviour Studio.Visual.Particles.FxFadeOut')
        self.assertEqual(sj['materials'][0]['shader'], 'Studio/Particles/Additive (Shader)')
        self.assertEqual(sj['materials'][0]['textures']['_MainTex']['texture'], 'Spark (Texture2D)')
        self.assertEqual(sj['materials'][0]['blend'], sy['materials'][0]['blend'])
        self.assertEqual(sj['unresolved_materials'], ['unresolved archive:/CAB-gone/CAB-gone:77'])

    def test_dump_unreadable_object(self):
        root, resolve = build_fake_env()
        mb = [o for o in root.assets_file.objects.values() if o.type.name == 'MonoBehaviour'][0]
        mb._data = None                                     # typetree that does not read at all
        dump = UD.Dumper(resolve).dump(root)
        self.assertEqual(len(dump['errors']), 1)
        self.assertNotIn('MonoBehaviour', [o['class'] for o in dump['objects']])

    def test_mesh_export_says_x_mirrored(self):
        # UnityPy's OBJ export negates x (positions and normals): the file's first line names it
        d = fresh('meshexport')

        class Mesh:
            path_id = 42

            def parse_as_object(self):
                return self

            def export(self):
                return 'g Ring\nv -1 2 3\nvn -1 0 0\n'
        rel = UD.Dumper(lambda *a: None, export_dir=d, meshes=True)._export(Mesh(), 'Mesh', 'Ring')
        text = (d / rel).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# unity_dump: UnityPy OBJ export, X negated'))
        self.assertIn('v -1 2 3', text)


def make_unityfs(names, revision='2020.3.48f1', version=8, comp=0, at_end=False, data=b''):
    """A UnityFS bundle with the given directory (every node empty), laid out as UnityPy's BundleFile reads it."""
    import lzma
    import struct
    info = b'\0' * 16 + struct.pack('>i', 0) + struct.pack('>i', len(names))
    for n in names:
        info += struct.pack('>qqI', 0, 0, 4) + n.encode() + b'\0'
    if comp == 1:
        filters = [{'id': lzma.FILTER_LZMA1, 'dict_size': 1 << 16, 'lc': 3, 'lp': 0, 'pb': 2}]
        packed = struct.pack('<BI', 3 + 0 * 9 + 2 * 45, 1 << 16) + lzma.compress(info, lzma.FORMAT_RAW,
                                                                                  filters=filters)
    else:
        packed = info
    flags = comp | (0x80 if at_end else 0x40)
    head = b'UnityFS\0' + struct.pack('>I', version) + b'5.x.x\0' + revision.encode() + b'\0'
    body = struct.pack('>qIII', 0, len(packed), len(info), flags)
    out = head + body
    if version >= 7 or U_version(revision) >= (2019, 4, 15):
        out += b'\0' * (-len(out) % 16)
    return out + (data + packed if at_end else packed + data)


def U_version(revision):
    return UD._version_tuple(revision)


class Bundles(unittest.TestCase):
    def test_bundle_cabs(self):
        d = fresh('bundles')
        names = ['CAB-0123456789abcdef0123456789abcdef', 'CAB-0123456789abcdef0123456789abcdef.resS']
        cases = {'plain': dict(), 'lzma': dict(comp=1), 'end': dict(at_end=True, data=b'x' * 37),
                 'end-lzma-old': dict(comp=1, at_end=True, version=6, revision='2018.4.4f1'),
                 'v6-aligned-2019': dict(version=6, revision='2019.4.40f1')}
        for name, kw in cases.items():
            (d / name).write_bytes(make_unityfs(names, **kw))
            self.assertEqual(UD.bundle_cabs(d / name), (kw.get('revision', '2020.3.48f1'), names), name)
        (d / 'notes.json').write_text('{}', encoding='utf-8')
        self.assertIsNone(UD.bundle_cabs(d / 'notes.json'))
        (d / 'cut').write_bytes(make_unityfs(names, comp=1)[:-10])
        index, rows, bad = UD.cab_index(d)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(bad), 1)
        self.assertIn('cut', bad[0])
        self.assertEqual(index[names[0].lower()].parent, d)
        self.assertEqual(UD.cab_of('archive:/CAB-Gone/CAB-Gone'), 'cab-gone')

    def test_inputs_and_list(self):
        d = fresh('inputs')
        for n in ('FX', 'FX_Fireball', 'fx_frost.bundle', 'UI', 'FXmanifest.json'):
            (d / n).write_bytes(b'')
        paths, bad = UD.expand_inputs(['fx*', 'UI', 'nothere', 'Q*'], d)
        self.assertEqual([p.name for p in paths], ['FX', 'FX_Fireball', 'FXmanifest.json', 'fx_frost.bundle', 'UI'])
        self.assertEqual(bad, ['nothere', 'Q*'])
        paths, _ = UD.expand_inputs([str(d / 'FX_*'), str(d / 'FX')])     # a pattern with its own folder
        self.assertEqual([p.name for p in paths], ['FX_Fireball', 'fx_frost.bundle', 'FX'])
        root, resolve = build_fake_env()

        class PPtr:
            def __init__(self, obj, pid):
                self.obj, self.m_PathID = obj, pid

            def deref(self):
                if self.obj is None:
                    raise FileNotFoundError('CAB-gone not found')
                return self.obj

        class Container:
            def items(self):
                return [('assets/fx/weapons/plasma_shot.prefab', PPtr(root, root.path_id)),
                        ('assets/fx/gone.prefab', PPtr(None, 5)), ('assets/ui/icon.png', PPtr(root, 1))]

        class Env:
            container = Container()
        rows = UD.list_rows(Env(), [__import__('re').compile('fx/', 2)], 'FX')
        self.assertEqual(rows, [['FX', 'assets/fx/gone.prefab', 'unresolved: CAB-gone not found', '', 5],
                                ['FX', 'assets/fx/weapons/plasma_shot.prefab', 'GameObject', 'CAB-fx', root.path_id]])

    def test_dependency_rounds(self):
        root, resolve, files, gone = build_fake_env(with_gone=True)

        class Env:
            def load_file(self, path):
                files['CAB-gone'] = gone
        index = {'cab-gone': Path('/game/Bundles/materials_extra')}
        loaded = set()
        dumps, added = UD.dump_with_dependencies(Env(), [('FX_Test', root)], index, loaded,
                                                 lambda: UD.Dumper(resolve), 4)
        self.assertEqual(added, [Path('/game/Bundles/materials_extra')])
        dump = dumps[0][2]
        self.assertEqual(dump['unresolved_files'], [])
        self.assertIn('M_Extra', [o['data'].get('m_Name') for o in dump['objects'] if o['class'] == 'Material'])
        dumps, added = UD.dump_with_dependencies(Env(), [('FX_Test', root)], {}, set(),
                                                 lambda: UD.Dumper(resolve), 4)
        self.assertEqual(added, [])


def run(main, *args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main([str(a) for a in args])
        except SystemExit as ex:
            code = ex.code
    return code, out.getvalue(), err.getvalue()


class SummaryCli(unittest.TestCase):
    def test_cli(self):
        d = fresh('cli')
        write_assets(d / 'assets')
        (d / 'in').mkdir()
        write_yaml(d / 'in' / 'FX_Test.prefab', fixture_objects())
        (d / 'in' / 'broken.prefab').write_text('--- !u!1 &1\nGameObject: [unclosed\n', encoding='utf-8')
        code, out, _ = run(SS.main, d / 'in', '--out', d / 'out', '--assets', d / 'assets')
        self.assertEqual(code, 1, out)
        c = json.loads((d / 'out' / 'census.json').read_text(encoding='utf-8'))
        self.assertEqual((c['inputs'], c['summarised'], c['systems']), (2, 1, 2))
        self.assertEqual(c['render_mode'], {'Billboard': 1, 'Stretch': 1})
        self.assertEqual(c['components'], {'MonoBehaviour FxFadeOut': 1})
        self.assertTrue((d / 'out' / '000-FX_Test' / 'summary.md').is_file())
        self.assertIn('broken.prefab', (d / 'out' / 'census.md').read_text(encoding='utf-8'))
        code, _, err = run(SS.main, d / 'in', '--out', d / 'out')
        self.assertEqual(code, 2)
        self.assertIn('not an empty folder', err)
        (d / 'empty').mkdir()
        code, _, err = run(SS.main, d / 'empty', '--out', d / 'out2')
        self.assertEqual(code, 2)


def write_template(path, rows, extra_key=None):
    data = json.dumps({'dataList': rows} if extra_key is None else {extra_key: rows}).encode()
    with tarfile.open(path, 'w') as tar:
        for name, payload in (('WhRtModificationTemplate/Assets/readme.txt', b'hi'),
                              ('WhRtModificationTemplate' + OX.CACHE_SUFFIX, data)):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


class StudioIndex(unittest.TestCase):
    ROWS = [
        {'BlueprintName': 'PlasmaGun_Ability', 'BlueprintGuid': '1' * 32, 'FieldName': 'FxPrefab',
         'ObjectPath': 'Assets/FX/Weapons/Plasma_Shot.prefab', 'Guid': 'A' * 32, 'FileId': 100100000},
        {'BlueprintName': 'Bolter_Ability', 'BlueprintGuid': '2' * 32, 'FieldName': 'FxPrefab',
         'ObjectPath': 'Assets/FX/Weapons/Bolt.prefab', 'Guid': 'b' * 32, 'FileId': 100100000},
        {'BlueprintName': 'Some_Unit', 'BlueprintGuid': '3' * 32, 'FieldName': 'Prefab',
         'ObjectPath': 'Assets/Units/Guard.prefab', 'Guid': 'c' * 32, 'FileId': 100100000},
        'not a dict']

    def test_archive_map_query(self):
        d = fresh('studio')
        game = d / 'game'
        (game / 'Modding').mkdir(parents=True, exist_ok=True)
        write_template(game / 'Modding' / 'WhRtModificationTemplate.tar', self.ROWS)
        (d / 'map.txt').write_text('%s:fx\n%s\tfx_weapons\n# comment\nnot a guid:x\n' % ('a' * 32, 'c' * 32),
                                   encoding='utf-8')
        code, out, _ = run(OX.main, '--game', game, '--bundle-map', d / 'map.txt', '--out', d / 'fx.csv',
                           '--save-cache', d / 'cache.json')
        self.assertEqual(code, 0, out)
        rows = list(csv.DictReader((d / 'fx.csv').open(encoding='utf-8-sig')))
        self.assertEqual([r['BlueprintName'] for r in rows], ['Bolter_Ability', 'PlasmaGun_Ability'])
        self.assertEqual([r['MappingStatus'] for r in rows], ['not-in-provided-map', 'historical-map-match'])
        self.assertEqual(rows[1]['CandidateBundle'], 'fx')
        self.assertIn("'FxPrefab': 2", out)
        self.assertIn('All FxPrefab references: 2', out)
        pref = list(csv.DictReader((d / 'fx_prefabs.csv').open(encoding='utf-8-sig')))
        self.assertEqual([(r['Category'], r['Prefab'], r['Blueprints'], r['Container']) for r in pref],
                         [('FX/Weapons', 'Bolt', '1', 'bolt.prefab'),
                          ('FX/Weapons', 'Plasma_Shot', '1', 'plasma_shot.prefab')])
        code, out, _ = run(OX.main, '--json', d / 'cache.json', '--field', 'FxPrefab', '--field', 'Prefab',
                           '--query', 'guard', '--bundle-map', d / 'map.txt', '--out', d / 'q.csv')
        rows = list(csv.DictReader((d / 'q.csv').open(encoding='utf-8-sig')))
        self.assertEqual([(r['BlueprintName'], r['CandidateBundle']) for r in rows], [('Some_Unit', 'fx_weapons')])
        code, out, _ = run(OX.main, '--json', d / 'cache.json', '--out', d / 'n.csv')
        rows = list(csv.DictReader((d / 'n.csv').open(encoding='utf-8-sig')))
        self.assertEqual({r['MappingStatus'] for r in rows}, {'no-map-provided'})

    def test_errors(self):
        d = fresh('studio-bad')
        write_template(d / 'bad.tar', self.ROWS, extra_key='rows')
        with self.assertRaises(ValueError):
            run(OX.main, '--archive', d / 'bad.tar', '--out', d / 'x.csv')
        (d / 'map.txt').write_text('nothing here\n', encoding='utf-8')
        write_template(d / 'ok.tar', self.ROWS)
        with self.assertRaises(ValueError):
            run(OX.main, '--archive', d / 'ok.tar', '--bundle-map', d / 'map.txt', '--out', d / 'x.csv')
        code, _, err = run(OX.main, '--archive', d / 'none.tar', '--out', d / 'x.csv')
        self.assertEqual(code, 2)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--work', type=Path, required=True)
    args = p.parse_args()
    WORK = args.work.resolve()
    WORK.mkdir(parents=True, exist_ok=True)
    suite = unittest.TestSuite()
    for case in (Curves, Gradients, Assets, Timing, Summary, SummaryCli, Bundles, StudioIndex):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'skipped': len(result.skipped), 'passed': result.wasSuccessful()}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
