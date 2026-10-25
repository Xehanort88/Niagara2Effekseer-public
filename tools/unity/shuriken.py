"""Shuriken (Unity ParticleSystem) values resolved from their serialized form.

Units follow what the Unity inspector shows, converted from what is stored (UnityCsReference ParticleSystemEditor:
`InitialModuleUI`/`RotationModuleUI` remap rotations by Rad2Deg, `UVModuleUI` remaps frame positions by the frame
count). Everything else is stored in display units already (seconds, Unity units, degrees for shape angles).

MinMaxCurve (ParticleSystemCurveMode, `minMaxState`): 0 Constant = `scalar`; 1 Curve = maxCurve(t) x `scalar`;
2 TwoCurves = random between minCurve(t) and maxCurve(t), x `scalar`; 3 TwoConstants = random in
`minScalar`..`scalar` (SerializedMinMaxCurve: maxConstant is `scalar`, minConstant `minScalar`, the curve editor's
y axis `scalar`).
MinMaxGradient (ParticleSystemGradientMode): 0 Color, 1 Gradient, 2 TwoColors, 3 TwoGradients, 4 RandomColor.
A Gradient holds up to 8 keys: colour key i is `key<i>.rgb` at `ctime<i>`/65535, alpha key i is `key<i>.a` at
`atime<i>`/65535 (`m_NumColorKeys`, `m_NumAlphaKeys`); `m_Mode` 0 Blend, 1 Fixed, 2 PerceptualBlend (Oklab).
"""
import math

from unityasset import flag, num, vec

RAD2DEG = 180.0 / math.pi
SAMPLES = 11            # curve samples written per MinMaxCurve (t = 0, 0.1, ... 1)

CURVE_MODES = ['constant', 'curve', 'two_curves', 'two_constants']
GRADIENT_MODES = ['color', 'gradient', 'two_colors', 'two_gradients', 'random_color']
BLEND_MODES = ['blend', 'fixed', 'perceptual_blend']

# Enums from UnityCsReference Modules/ParticleSystem/Managed/ParticleSystemEnums.cs, keyed by (module, field);
# module None means the ParticleSystem's own top-level fields.
_SHAPES = {0: 'Sphere', 1: 'SphereShell', 2: 'Hemisphere', 3: 'HemisphereShell', 4: 'Cone', 5: 'Box', 6: 'Mesh',
           7: 'ConeShell', 8: 'ConeVolume', 9: 'ConeVolumeShell', 10: 'Circle', 11: 'CircleEdge',
           12: 'SingleSidedEdge', 13: 'MeshRenderer', 14: 'SkinnedMeshRenderer', 15: 'BoxShell', 16: 'BoxEdge',
           17: 'Donut', 18: 'Rectangle', 19: 'Sprite', 20: 'SpriteRenderer'}
ENUMS = {
    (None, 'moveWithTransform'): ['Local', 'World', 'Custom'],
    (None, 'scalingMode'): ['Hierarchy', 'Local', 'Shape'],
    (None, 'stopAction'): ['None', 'Disable', 'Destroy', 'Callback'],
    (None, 'cullingMode'): ['Automatic', 'PauseAndCatchup', 'Pause', 'AlwaysSimulate'],
    (None, 'ringBufferMode'): ['Disabled', 'PauseUntilReplaced', 'LoopUntilReplaced'],
    (None, 'emitterVelocityMode'): ['Transform', 'Rigidbody', 'Custom'],
    ('InitialModule', 'gravitySource'): ['Physics3D', 'Physics2D'],
    ('ShapeModule', 'type'): _SHAPES,
    ('ShapeModule', 'placementMode'): ['Vertex', 'Edge', 'Triangle'],
    ('ShapeModule', 'm_TextureClipChannel'): ['Red', 'Green', 'Blue', 'Alpha'],
    ('*', 'mode@MultiModeParameter'): ['Random', 'Loop', 'PingPong', 'BurstSpread'],
    ('UVModule', 'mode'): ['Grid', 'Sprites'],
    ('UVModule', 'timeMode'): ['Lifetime', 'Speed', 'FPS'],
    ('UVModule', 'animationType'): ['WholeSheet', 'SingleRow'],
    ('UVModule', 'rowMode'): ['Custom', 'Random', 'MeshIndex'],
    ('InheritVelocityModule', 'm_Mode'): ['Initial', 'Current'],
    ('NoiseModule', 'quality'): ['Low', 'Medium', 'High'],
    ('SubModule', 'type'): ['Birth', 'Collision', 'Death', 'Trigger', 'Manual'],
    ('TrailModule', 'mode'): ['PerParticle', 'Ribbon'],
    ('TrailModule', 'textureMode'): ['Stretch', 'Tile', 'DistributePerSegment', 'RepeatPerSegment', 'Static'],
    ('CollisionModule', 'type'): ['Planes', 'World'],
    ('CollisionModule', 'collisionMode'): ['3D', '2D'],
    ('Renderer', 'm_RenderMode'): ['Billboard', 'Stretch', 'HorizontalBillboard', 'VerticalBillboard', 'Mesh', 'None'],
    ('Renderer', 'm_RenderAlignment'): ['View', 'World', 'Local', 'Facing', 'Velocity'],
    ('Renderer', 'm_SortMode'): ['None', 'Distance', 'OldestInFront', 'YoungestInFront', 'Depth', 'DistanceReverse',
                                 'DepthReverse'],
    ('Renderer', 'm_MeshDistribution'): ['UniformRandom', 'NonUniformRandom'],
}
SUB_EMITTER_INHERIT = ['Color', 'Size', 'Rotation', 'Lifetime', 'Duration']

# Units per (module, field) as the inspector shows them; 'deg' and 'deg/s' fields are stored in radians.
_RAD = {('InitialModule', 'startRotation'), ('InitialModule', 'startRotationX'), ('InitialModule', 'startRotationY'),
        ('RotationModule', 'x'), ('RotationModule', 'y'), ('RotationModule', 'curve'),
        ('RotationBySpeedModule', 'x'), ('RotationBySpeedModule', 'y'), ('RotationBySpeedModule', 'curve')}
UNITS = {
    (None, 'lengthInSec'): 's', (None, 'startDelay'): 's',
    ('InitialModule', 'startLifetime'): 's', ('InitialModule', 'startSpeed'): 'u/s',
    ('InitialModule', 'startSize'): 'u', ('InitialModule', 'startSizeY'): 'u', ('InitialModule', 'startSizeZ'): 'u',
    ('InitialModule', 'startRotation'): 'deg', ('InitialModule', 'startRotationX'): 'deg',
    ('InitialModule', 'startRotationY'): 'deg', ('InitialModule', 'gravityModifier'): 'x gravity',
    ('ShapeModule', 'angle'): 'deg', ('ShapeModule', 'arc'): 'deg', ('ShapeModule', 'm_Rotation'): 'deg',
    ('EmissionModule', 'rateOverTime'): '/s', ('EmissionModule', 'rateOverDistance'): '/u',
    ('EmissionModule', 'time'): 's', ('EmissionModule', 'repeatInterval'): 's',
    ('RotationModule', 'x'): 'deg/s', ('RotationModule', 'y'): 'deg/s', ('RotationModule', 'curve'): 'deg/s',
    ('RotationBySpeedModule', 'x'): 'deg/s', ('RotationBySpeedModule', 'y'): 'deg/s',
    ('RotationBySpeedModule', 'curve'): 'deg/s',
    ('VelocityModule', 'x'): 'u/s', ('VelocityModule', 'y'): 'u/s', ('VelocityModule', 'z'): 'u/s',
    ('VelocityModule', 'radial'): 'u/s', ('VelocityModule', 'orbitalX'): 'rad/s',
    ('VelocityModule', 'orbitalY'): 'rad/s', ('VelocityModule', 'orbitalZ'): 'rad/s',
    ('ForceModule', 'x'): 'u/s2', ('ForceModule', 'y'): 'u/s2', ('ForceModule', 'z'): 'u/s2',
    ('ClampVelocityModule', 'magnitude'): 'u/s', ('UVModule', 'frameOverTime'): 'frame',
    ('UVModule', 'startFrame'): 'frame', ('TrailModule', 'lifetime'): 'x particle life',
}
# What a module's curves run over (their x axis). InitialModule curves run over the emitter's duration.
AXIS = {'InitialModule': 'duration', 'EmissionModule': 'duration', 'SizeBySpeedModule': 'speed',
        'RotationBySpeedModule': 'speed', 'ColorBySpeedModule': 'speed', 'LifetimeByEmitterSpeedModule': 'speed',
        'LightsModule': 'life', 'ShapeModule': 'duration'}
FIELD_AXIS = {('TrailModule', 'widthOverTrail'): 'trail', ('TrailModule', 'colorOverTrail'): 'trail',
              ('TrailModule', 'lifetime'): 'duration'}
MODULES = ['InitialModule', 'ShapeModule', 'EmissionModule', 'SizeModule', 'RotationModule', 'ColorModule',
           'UVModule', 'VelocityModule', 'InheritVelocityModule', 'LifetimeByEmitterSpeedModule', 'ForceModule',
           'ExternalForcesModule', 'ClampVelocityModule', 'NoiseModule', 'SizeBySpeedModule',
           'RotationBySpeedModule', 'ColorBySpeedModule', 'CollisionModule', 'TriggerModule', 'SubModule',
           'LightsModule', 'TrailModule', 'CustomDataModule']


# --- AnimationCurve ---------------------------------------------------------------------------------------------

def keys_of(curve):
    """An AnimationCurve's keyframes as plain floats."""
    out = []
    for k in (curve or {}).get('m_Curve') or []:
        out.append({'time': num(k.get('time')), 'value': num(k.get('value')),
                    'inSlope': num(k.get('inSlope')), 'outSlope': num(k.get('outSlope')),
                    'weightedMode': int(num(k.get('weightedMode'))),
                    'inWeight': num(k.get('inWeight'), 1 / 3), 'outWeight': num(k.get('outWeight'), 1 / 3)})
    return out


def eval_keys(keys, t):
    """Unity AnimationCurve.Evaluate on keys, clamped outside the first/last key (particle curves are read
    over 0..1). Hermite between keys from the slopes; a weighted key (WeightedMode In=1/Out=2) makes that
    segment a cubic Bezier in (time, value) with the stored weights (1/3 = the unweighted Hermite); an infinite
    slope holds the left key's value (a stepped key)."""
    if not keys:
        return 0.0
    if t <= keys[0]['time']:
        return keys[0]['value']
    if t >= keys[-1]['time']:
        return keys[-1]['value']
    i = 0
    while keys[i + 1]['time'] < t:
        i += 1
    k0, k1 = keys[i], keys[i + 1]
    dt = k1['time'] - k0['time']
    if dt <= 0:
        return k1['value']
    m0, m1 = k0['outSlope'], k1['inSlope']
    if math.isinf(m0) or math.isinf(m1):
        return k0['value']
    s = (t - k0['time']) / dt
    w0 = k0['outWeight'] if k0['weightedMode'] & 2 else 1 / 3
    w1 = k1['inWeight'] if k1['weightedMode'] & 1 else 1 / 3
    v0, v1 = k0['value'], k1['value']
    if abs(w0 - 1 / 3) < 1e-7 and abs(w1 - 1 / 3) < 1e-7:
        s2, s3 = s * s, s * s * s
        return ((2 * s3 - 3 * s2 + 1) * v0 + (s3 - 2 * s2 + s) * dt * m0 + (-2 * s3 + 3 * s2) * v1
                + (s3 - s2) * dt * m1)
    # weighted: Bezier with x control points at w0 and 1 - w1 (normalised time), solved for x = s by bisection
    x1, x2 = w0, 1 - w1
    y1, y2 = v0 + w0 * dt * m0, v1 - w1 * dt * m1
    lo, hi = 0.0, 1.0
    for _ in range(60):
        u = (lo + hi) / 2
        x = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
        lo, hi = (u, hi) if x < s else (lo, u)
    u = (lo + hi) / 2
    return (1 - u) ** 3 * v0 + 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3 * v1


# --- MinMaxCurve ------------------------------------------------------------------------------------------------

def is_minmax_curve(v):
    return isinstance(v, dict) and 'minMaxState' in v and 'scalar' in v


def is_minmax_gradient(v):
    return isinstance(v, dict) and 'minMaxState' in v and 'maxGradient' in v


def is_gradient(v):
    return isinstance(v, dict) and 'key0' in v and 'ctime0' in v


class Curve:
    """A MinMaxCurve in display units: range(t) -> (lo, hi), the values a particle can get at t."""

    def __init__(self, mc, scale=1.0):
        self.mode = int(num(mc.get('minMaxState')))
        self.scalar = num(mc.get('scalar')) * scale
        self.min_scalar = num(mc.get('minScalar')) * scale
        self.max_keys = keys_of(mc.get('maxCurve'))
        self.min_keys = keys_of(mc.get('minCurve'))

    def range(self, t):
        if self.mode == 0:
            return self.scalar, self.scalar
        if self.mode == 3:
            return tuple(sorted((self.min_scalar, self.scalar)))
        hi = eval_keys(self.max_keys, t) * self.scalar
        if self.mode == 1:
            return hi, hi
        lo = eval_keys(self.min_keys, t) * self.scalar
        return (lo, hi) if lo <= hi else (hi, lo)

    def mean(self, t=0.0):
        lo, hi = self.range(t)
        return (lo + hi) / 2

    def overall(self):
        if self.mode in (0, 3):
            return self.range(0)
        ts = sorted({i / 100 for i in range(101)} | {k['time'] for k in self.max_keys + self.min_keys
                                                      if 0 <= k['time'] <= 1})
        vals = [v for t in ts for v in self.range(t)]
        return min(vals), max(vals)

    def summary(self, unit=None, axis=None):
        out = {'mode': CURVE_MODES[self.mode] if 0 <= self.mode < 4 else 'unknown %d' % self.mode}
        if unit:
            out['unit'] = unit
        if self.mode == 0:
            out['value'] = _r(self.scalar)
        elif self.mode == 3:
            out['min'], out['max'] = [_r(x) for x in self.range(0)]
        else:
            out['scalar'] = _r(self.scalar)
            out['axis'] = axis or 'life'
            out['samples'] = [[_r(a), _r(b)] for a, b in (self.range(i / (SAMPLES - 1)) for i in range(SAMPLES))]
            out['range'] = [_r(x) for x in self.overall()]
            out['max_keys'] = [_key(k, self.scalar) for k in self.max_keys]
            if self.mode == 2:
                out['min_keys'] = [_key(k, self.scalar) for k in self.min_keys]
            if any(k['weightedMode'] for k in self.max_keys + self.min_keys):
                out['weighted_keys'] = True
        return out


def _key(k, scalar):
    d = {'t': _r(k['time']), 'v': _r(k['value'] * scalar)}
    for name, val in (('in', k['inSlope']), ('out', k['outSlope'])):
        d[name] = 'inf' if math.isinf(val) else _r(val * scalar)
    return d


def _r(x, n=6):
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return str(x)
    return round(x, n)


# --- Gradient ---------------------------------------------------------------------------------------------------

def gradient_keys(g):
    nc = max(1, min(8, int(num(g.get('m_NumColorKeys'), 2))))
    na = max(1, min(8, int(num(g.get('m_NumAlphaKeys'), 2))))
    ck = [(num(g.get('ctime%d' % i)) / 65535, vec(g.get('key%d' % i), 'rgb')) for i in range(nc)]
    ak = [(num(g.get('atime%d' % i)) / 65535, num((g.get('key%d' % i) or {}).get('a'), 1.0)) for i in range(na)]
    return sorted(ck, key=lambda k: k[0]), sorted(ak, key=lambda k: k[0])


def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c):
    c = max(0.0, c)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab(rgb):
    r, g, b = rgb
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _oklab_inv(lab):
    L, a, b = lab
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def _lerp_keys(keys, t, mode, mix):
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            if mode == 1:                 # Fixed: the exact colour of the key at or after t
                return v1
            f = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            return mix(v0, v1, f)
    return keys[-1][1]


def eval_gradient(g, t):
    """Gradient.Evaluate: [r, g, b, a] at t in 0..1. PerceptualBlend mixes in Oklab from the keys taken as sRGB."""
    ck, ak = gradient_keys(g)
    mode = int(num(g.get('m_Mode')))
    if mode == 2:
        def mix(a, b, f):
            la = _oklab([_srgb_to_linear(c) for c in a])
            lb = _oklab([_srgb_to_linear(c) for c in b])
            return [_linear_to_srgb(c) for c in _oklab_inv([x + (y - x) * f for x, y in zip(la, lb)])]
    else:
        def mix(a, b, f):
            return [x + (y - x) * f for x, y in zip(a, b)]
    rgb = _lerp_keys(ck, t, mode, mix)
    a = _lerp_keys(ak, t, mode, lambda x, y, f: x + (y - x) * f)
    return [*rgb, a]


def gradient_summary(g):
    ck, ak = gradient_keys(g)
    mode = int(num(g.get('m_Mode')))
    out = {'blend': BLEND_MODES[mode] if 0 <= mode < 3 else 'unknown %d' % mode,
           'color_keys': [[_r(t, 4), [_r(c, 4) for c in rgb]] for t, rgb in ck],
           'alpha_keys': [[_r(t, 4), _r(a, 4)] for t, a in ak]}
    if 'm_ColorSpace' in g:
        out['color_space'] = {-1: 'uninitialized', 0: 'gamma', 1: 'linear'}.get(int(num(g['m_ColorSpace'])),
                                                                               str(g['m_ColorSpace']))
    return out


def minmax_gradient_summary(mg):
    mode = int(num(mg.get('minMaxState')))
    out = {'mode': GRADIENT_MODES[mode] if 0 <= mode < 5 else 'unknown %d' % mode}
    if mode == 0:
        out['color'] = [_r(c, 4) for c in vec(mg.get('maxColor'), 'rgba')]
    elif mode == 2:
        out['min_color'] = [_r(c, 4) for c in vec(mg.get('minColor'), 'rgba')]
        out['max_color'] = [_r(c, 4) for c in vec(mg.get('maxColor'), 'rgba')]
    elif mode in (1, 4):
        out['gradient'] = gradient_summary(mg.get('maxGradient') or {})
    elif mode == 3:
        out['min_gradient'] = gradient_summary(mg.get('minGradient') or {})
        out['max_gradient'] = gradient_summary(mg.get('maxGradient') or {})
    return out


# --- whole modules ----------------------------------------------------------------------------------------------

def frame_count(uv):
    """The sheet's frame count the inspector remaps frame positions by (UVModuleUI): tilesX for a SingleRow
    animation, tilesX x tilesY for WholeSheet, the sprite count in Sprites mode."""
    if int(num(uv.get('mode'))) == 1:
        return max(1, len(uv.get('sprites') or []))
    tx, ty = int(num(uv.get('tilesX'), 1)), int(num(uv.get('tilesY'), 1))
    return max(1, tx if int(num(uv.get('animationType'))) == 1 else tx * ty)


def decode(value, module, field, doc, ctx=None):
    """One serialized value made readable: curves and gradients resolved, enums named, references described,
    vectors as lists, nested structs recursed. `ctx` carries the module dict (UV frame count)."""
    if is_minmax_curve(value):
        scale = RAD2DEG if (module, field) in _RAD else 1.0
        if module == 'UVModule' and field in ('frameOverTime', 'startFrame'):
            scale = frame_count(ctx or {})
        return Curve(value, scale).summary(UNITS.get((module, field)),
                                           FIELD_AXIS.get((module, field), AXIS.get(module, 'life')))
    if is_minmax_gradient(value):
        out = minmax_gradient_summary(value)
        out['axis'] = FIELD_AXIS.get((module, field), AXIS.get(module, 'life'))
        return out
    if is_gradient(value):
        return gradient_summary(value)
    if isinstance(value, dict) and ('fileID' in value or '$ref' in value or 'm_PathID' in value):
        return doc.describe_ref(value)
    if isinstance(value, dict) and {'value', 'mode', 'spread', 'speed'} <= set(value):     # MultiModeParameter
        out = {'value': _r(num(value['value'])), 'mode': _enum(('*', 'mode@MultiModeParameter'), value['mode']),
               'spread': _r(num(value['spread'])), 'speed': decode(value['speed'], module, field + '.speed', doc)}
        if UNITS.get((module, field)):
            out['unit'] = UNITS[(module, field)]
        return out
    if isinstance(value, dict) and set(value) <= set('xyzw') and value:
        return [_r(num(value[k])) for k in 'xyzw' if k in value]
    if isinstance(value, dict) and set(value) <= set('rgba') and value:
        return [_r(num(value[k])) for k in 'rgba' if k in value]
    if isinstance(value, dict):
        return {k: decode(v, module, k, doc, ctx) for k, v in value.items() if k != 'serializedVersion'}
    if isinstance(value, list):
        return [decode(v, module, field, doc, ctx) for v in value]
    if isinstance(value, bool):          # UnityPy's True/False read as YAML's 1/0, so both sources summarise alike
        return int(value)
    key = (module, field)
    if key in ENUMS:
        return _enum(key, value)
    if isinstance(value, float):
        return _r(value)
    return value


def _enum(key, v):
    names = ENUMS[key]
    i = int(num(v))
    if isinstance(names, dict):
        return names.get(i, 'unknown %d' % i)
    return names[i] if 0 <= i < len(names) else 'unknown %d' % i


def module_summary(ps, name):
    m = ps.data.get(name)
    if not isinstance(m, dict):
        return None
    out = {k: decode(v, name, k, ps.doc, m) for k, v in m.items() if k not in ('serializedVersion', 'enabled')}
    if name == 'UVModule':
        out['frame_count'] = frame_count(m)
    return out


def system_summary(ps, renderer):
    """Everything a builder needs from one ParticleSystem and its renderer, in display units."""
    d, doc = ps.data, ps.doc
    top = {k: decode(v, None, k, doc) for k, v in d.items()
           if k not in MODULES and k not in ('m_GameObject', 'serializedVersion', 'm_ObjectHideFlags',
                                              'm_CorrespondingSourceObject', 'm_PrefabInstance', 'm_PrefabAsset')}
    enabled, disabled = {}, []
    for name in MODULES:
        m = d.get(name)
        if not isinstance(m, dict):
            continue
        # InitialModule is always on in Unity (its enabled flag is serialized but not shown).
        if name == 'InitialModule' or flag(m.get('enabled', 0)):
            enabled[name] = module_summary(ps, name)
        else:
            disabled.append(name)
    out = {'main': top, 'modules': enabled, 'disabled_modules': disabled,
           'renderer': renderer_summary(renderer) if renderer is not None else None,
           'timing': timing(ps)}
    return out


def renderer_summary(r):
    d, doc = r.data, r.doc
    keep = ('m_Enabled', 'm_RenderMode', 'm_RenderAlignment', 'm_SortMode', 'm_SortingFudge', 'm_SortingOrder',
            'm_MinParticleSize', 'm_MaxParticleSize', 'm_CameraVelocityScale', 'm_VelocityScale', 'm_LengthScale',
            'm_NormalDirection', 'm_Pivot', 'm_Flip', 'm_AllowRoll', 'm_FreeformStretching',
            'm_RotateWithStretchDirection', 'm_UseCustomVertexStreams', 'm_VertexStreams', 'm_MeshDistribution',
            'm_MaskInteraction', 'm_ApplyActiveColorSpace')
    out = {k[2:]: decode(d[k], 'Renderer', k, doc) for k in keep if k in d}
    out['Materials'] = [doc.describe_ref(m) for m in d.get('m_Materials') or []]
    out['TrailMaterial'] = doc.describe_ref(d.get('m_TrailMaterial')) if 'm_TrailMaterial' in d else None
    meshes = [doc.describe_ref(d.get(k)) for k in ('m_Mesh', 'm_Mesh1', 'm_Mesh2', 'm_Mesh3') if k in d]
    out['Meshes'] = [m for m in meshes if m]
    return out


def burst_fires(t0, cycles, interval, duration):
    """How often a burst fires within one emitter cycle: never after the duration; cycleCount 0 repeats every
    interval until the duration ends; a positive cycleCount stops early if the duration ends first."""
    if t0 > duration:
        return 0
    room = int((duration - t0) / interval) + 1 if interval > 0 else None
    if cycles <= 0:
        return room if room is not None else 1
    return min(cycles, room) if room is not None else cycles


def timing(ps):
    """When this system spawns and when its last particle can die, from duration, delay, bursts and lifetime
    (seconds). A looping system has no end. Sub-emitters and simulation speed are not included."""
    d = ps.data
    init, em = d.get('InitialModule') or {}, d.get('EmissionModule') or {}
    duration = num(d.get('lengthInSec'))
    delay = Curve(d.get('startDelay') or {'minMaxState': 0, 'scalar': 0})
    life = Curve(init.get('startLifetime') or {'minMaxState': 0, 'scalar': 0})
    looping = flag(d.get('looping', 0))
    out = {'duration_s': _r(duration), 'looping': looping, 'prewarm': flag(d.get('prewarm', 0)),
           'start_delay_s': [_r(x) for x in delay.overall()], 'lifetime_s': [_r(x) for x in life.overall()],
           'simulation_speed': _r(num(d.get('simulationSpeed'), 1.0))}
    per_cycle = 0.0
    if flag(em.get('enabled', 0)):
        rate = Curve(em.get('rateOverTime') or {'minMaxState': 0, 'scalar': 0})
        per_cycle += sum(rate.mean(i / 20) for i in range(20)) / 20 * duration
        last_spawn = duration if rate.overall()[1] > 0 else 0.0
        bursts = []
        for b in em.get('m_Bursts') or []:
            cc = Curve(b.get('countCurve') or {'minMaxState': 0, 'scalar': num(b.get('maxCount'))})
            cycles = int(num(b.get('cycleCount'), 1))
            t0, interval = num(b.get('time')), num(b.get('repeatInterval'))
            prob = num(b.get('probability'), 1.0)
            fired = burst_fires(t0, cycles, interval, duration)
            per_cycle += cc.mean(t0 / duration if duration > 0 else 0) * fired * prob
            if fired:
                last_spawn = max(last_spawn, t0 + interval * (fired - 1))
            bursts.append({'time_s': _r(t0), 'count': [_r(x) for x in cc.overall()], 'cycles': cycles,
                           'interval_s': _r(interval), 'probability': _r(prob), 'fires_in_duration': fired})
        out['bursts'] = bursts
        out['last_spawn_s'] = _r(delay.overall()[1] + last_spawn)
        out['particles_per_cycle'] = _r(per_cycle, 2)
        out['end_s'] = None if looping else _r(delay.overall()[1] + last_spawn + life.overall()[1])
    else:
        out['particles_per_cycle'] = 0
        out['end_s'] = None if looping else _r(delay.overall()[1])
    out['max_particles'] = int(num(init.get('maxNumParticles'), 1000))
    return out
