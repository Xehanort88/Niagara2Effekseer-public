"""Helpers for writing `edit` specs that build new effects out of procedural models.

Standard library only. Every helper returns plain dicts of `set` paths (or whole ops), so a spec stays readable JSON.
The traps these helpers exist for are in README section "Building an effect from scratch":

    import sys; sys.path.insert(0, 'tools/author')
    from efkauthor import *
    spec = Spec(base='out/edits/<task>/blank.efkefc', name='MyEffect', end_frame=120)
    spec.model('Spike', merge(spline4([0.4, 0], [0.5, 0.4], [0.2, 2.6], [0, 3.1]), divisions(5, 4), fade_rows('Lower')))
    spec.node0('Spikes', merge(model_node('Spike', 'Blend', [200, 235, 255, 255]), once(90, 4, 25), ring_of(10, 3.2, 30)))
    spec.fresh('Glow', merge(sprite('glow.png', 'Add', [120, 200, 255, 200]), once(20, 2, 10), grow(1, 5)))
    spec.build('out/edits/<task>/MyEffect.efkefc')

Conventions: colours are [r, g, b, a] bytes; lengths are effect units (a battle target is about 4-6 tall); frames
are game frames. A part made with `Spec.fresh` is a copy of the blank base's node, so it inherits nothing from other
parts; `Spec.copy` copies an existing node of this effect (and everything set on it so far).
"""
import json
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOOL = ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll'
ROWS = ('Upper', 'Middle', 'Lower')
COLUMNS = ('Left', 'Center', 'Right')
FLOOR_TILT = 65          # degrees on X: the battle camera looks straight ahead, flat floor parts are edge-on


def merge(*parts):
    """Later parts win, like layered settings."""
    out = {}
    for p in parts:
        out.update(p)
    return out


# ------------------------------------------------------------------ node basics

def neutral():
    """Reset what a copied node may carry: still at the origin, upright, scale 1, no gravity, no fades."""
    s = {'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': [0, 0, 0],
         'RotationValues/Type': 'Fixed', 'RotationValues/Fixed/Rotation': [0, 0, 0],
         'ScalingValues/Type': 'Fixed', 'ScalingValues/Fixed/Scale': [1, 1, 1],
         'RendererCommonValues/FadeInType': 'None', 'RendererCommonValues/FadeOutType': 'None',
         'RendererCommonValues/UV': 'Default'}
    s.update(no_gravity())
    return s


def no_gravity():
    return {'LocationValues/PVA/Acceleration/X': 0, 'LocationValues/PVA/Acceleration/Y': 0, 'LocationValues/PVA/Acceleration/Z': 0}


def once(life, fade_in=0, fade_out=0):
    """One particle at its start frame, living `life` frames, with optional fades (frames)."""
    s = {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1, 'CommonValues/Life': life}
    s['RendererCommonValues/FadeInType'] = 'Use' if fade_in else 'None'
    if fade_in:
        s['RendererCommonValues/FadeIn/Frame'] = fade_in
    s['RendererCommonValues/FadeOutType'] = 'WithinLifetime' if fade_out else 'None'
    if fade_out:
        s['RendererCommonValues/FadeOut/Frame'] = fade_out
    return s


def emit(count, every, life, fade_in=3, fade_out=8):
    """`count` particles, one every `every` frames ([lo, hi] for a random interval). Stop early enough that the
    last ones die with the rest of the effect: count * every + life is when the node is done."""
    return {'CommonValues/MaxGeneration': count, 'CommonValues/GenerationTime': every, 'CommonValues/Life': life,
            'RendererCommonValues/FadeInType': 'Use', 'RendererCommonValues/FadeIn/Frame': fade_in,
            'RendererCommonValues/FadeOutType': 'WithinLifetime', 'RendererCommonValues/FadeOut/Frame': fade_out}


def delayed(frames):
    return {'CommonValues/GenerationTimeOffset': frames}


def at(x, y, z=0):
    return {'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': [x, y, z]}


def rotated(x=0, y=0, z=0):
    return {'RotationValues/Type': 'Fixed', 'RotationValues/Fixed/Rotation': [x, y, z]}


def floor_tilt(angle=FLOOR_TILT):
    """Lean a flat floor part (disc, ring) towards the battle camera."""
    return rotated(angle, 0, 0)


def spin(y_degrees_per_frame, start_y=0):
    return {'RotationValues/Type': 'PVA', 'RotationValues/PVA/Rotation/Y': start_y, 'RotationValues/PVA/Velocity/Y': y_degrees_per_frame}


def grow(start, end, start_speed='StartRapidly2', end_speed='EndSlowly2'):
    """Scale from `start` to `end` over the particle's whole life (numbers or [x, y, z])."""
    start = start if isinstance(start, list) else [start] * 3
    end = end if isinstance(end, list) else [end] * 3
    s = {'ScalingValues/Type': 'Easing', 'ScalingValues/Easing/StartSpeed': start_speed, 'ScalingValues/Easing/EndSpeed': end_speed}
    for i, c in enumerate('XYZ'):
        s['ScalingValues/Easing/Start/' + c] = start[i]
        s['ScalingValues/Easing/End/' + c] = end[i]
    return s


def rising(speed_y, y_range=(0, 0), spread=0):
    """Drift up from a random height (PVA, gravity cancelled)."""
    s = {'LocationValues/Type': 'PVA', 'LocationValues/PVA/Location/X': [-spread, spread] if spread else 0,
         'LocationValues/PVA/Location/Y': list(y_range) if y_range[0] != y_range[1] else y_range[0],
         'LocationValues/PVA/Location/Z': [-spread, spread] if spread else 0,
         'LocationValues/PVA/Velocity/X': 0, 'LocationValues/PVA/Velocity/Y': speed_y, 'LocationValues/PVA/Velocity/Z': 0}
    s.update(no_gravity())
    return s


def on_circle(radius, axis='YAxis', ordered_count=None, face_out=False):
    """Spawn on a circle (YAxis = flat round the target). With `ordered_count`, evenly spaced."""
    s = {'GenerationLocationValues/Type': 'Circle', 'GenerationLocationValues/Circle/AxisDirection': axis,
         'GenerationLocationValues/Circle/Radius': radius, 'GenerationLocationValues/EffectsRotation': face_out}
    if ordered_count:
        s.update({'GenerationLocationValues/Circle/Division': ordered_count, 'GenerationLocationValues/Circle/Type': 'Order'})
    else:
        s['GenerationLocationValues/Circle/Type'] = 'Random'
    return s


def ring_of(n, radius, lean=0):
    """n copies at once on a circle, each turned to face outward and leaned by `lean` degrees (negative leans in)."""
    return merge({'CommonValues/MaxGeneration': n, 'CommonValues/GenerationTime': 0},
                 on_circle(radius, ordered_count=n, face_out=True), rotated(0, 0, -lean))


# ------------------------------------------------------------------ renderers

def model_node(model, blend, colour, texture=None, culling='Double'):
    """Draw procedural model `model` (name or index). Culling: Front (outside), Back (inside), Double (both)."""
    return {'DrawingValues/Type': 'Model', 'DrawingValues/Model/ModelReference': 'ProceduralModel',
            'DrawingValues/Model/Reference': model, 'DrawingValues/Model/Culling': culling,
            'RendererCommonValues/AlphaBlend': blend, 'RendererCommonValues/ColorTexture': str(texture) if texture else '',
            'DrawingValues/ColorAll/Type': 'Fixed', 'DrawingValues/ColorAll/Fixed': colour}


def sprite(texture, blend, colour, stand_on_point=False):
    """A camera-facing sprite. `stand_on_point` puts the quad's bottom edge on the spawn point (flames on a ring)."""
    s = {'DrawingValues/Type': 'Sprite', 'RendererCommonValues/AlphaBlend': blend,
         'RendererCommonValues/ColorTexture': str(texture) if texture else '',
         'DrawingValues/ColorAll/Type': 'Fixed', 'DrawingValues/ColorAll/Fixed': colour,
         'DrawingValues/Sprite/Billboard': 'Billboard', 'DrawingValues/Sprite/Position': 'Fixed' if stand_on_point else 'Default'}
    if stand_on_point:
        s.update({'DrawingValues/Sprite/Position_Fixed_LL': [-0.5, 0], 'DrawingValues/Sprite/Position_Fixed_LR': [0.5, 0],
                  'DrawingValues/Sprite/Position_Fixed_UL': [-0.5, 1], 'DrawingValues/Sprite/Position_Fixed_UR': [0.5, 1]})
    return s


def hidden():
    """A non-drawing controller (e.g. a tilted parent so children's circle lies on a tilted floor ring)."""
    return {'DrawingValues/Type': 'None'}


def scroll(speed_x, speed_y, size_x, size_y):
    """Texture scroll in texture pixels per frame; size = how many texture pixels span the model's UV 0..1."""
    return {'RendererCommonValues/UV': 'Scroll', 'RendererCommonValues/UVScroll/Speed/X': speed_x,
            'RendererCommonValues/UVScroll/Speed/Y': speed_y, 'RendererCommonValues/UVScroll/Size/X': size_x,
            'RendererCommonValues/UVScroll/Size/Y': size_y}


def flipbook(cell, cols, rows, frame_length, loop=False, start_sheet=None):
    """A sheet of cols x rows cells of `cell` pixels. `start_sheet` offsets a second layer (e.g. half a cycle)."""
    p = 'RendererCommonValues/UVAnimation/AnimationParams/'
    s = {'RendererCommonValues/UV': 'Animation', p + 'Start': [0, 0], p + 'Size': [cell, cell], p + 'FrameCountX': cols,
         p + 'FrameCountY': rows, p + 'FrameLength': frame_length, p + 'LoopType': 'Loop' if loop else 'Once'}
    if start_sheet is not None:
        s[p + 'StartSheet'] = start_sheet
    return s


# ------------------------------------------------------------------ procedural model shapes

def divisions(around, along):
    return {'Mesh/Divisions/X': around, 'Mesh/Divisions/Y': along}


def spline4(p1, p2, p3, p4):
    """A profile (radius, height) spun round Y. The runtime curve passes THROUGH all four points (natural cubic);
    set all four, or the unset ones keep their defaults and bend the shape."""
    return {'Shape/PrimitiveType': 'Spline4', 'Shape/Point1': list(p1), 'Shape/Point2': list(p2),
            'Shape/Point3': list(p3), 'Shape/Point4': list(p4)}


def sphere(radius, depth_min=None, depth_max=None):
    s = {'Shape/PrimitiveType': 'Sphere', 'Shape/Radius': radius}
    s['Shape/DepthMin'] = -radius if depth_min is None else depth_min
    s['Shape/DepthMax'] = radius if depth_max is None else depth_max
    return s


def cylinder(radius_bottom, radius_top, height):
    return {'Shape/PrimitiveType': 'Cylinder', 'Shape/Radius': radius_bottom, 'Shape/Radius2': radius_top, 'Shape/Depth': height}


def flat_disc(segments=32):
    """A flat disc of radius 1 (a cylinder of radius 0 and depth 0). Scale it, and floor_tilt() it."""
    return merge(cylinder(0, 1, 0), divisions(segments, 4))


def ribbon(count, turns, width_start, width_end=None, vertices=48, cross=True, twist=(0, 0), jitter=(0, 0)):
    """Ribbon strands following the shape's profile: a strip each, U across, V along (like a trail).
    `cross` = two crossed strips, so strands radial to the camera do not vanish edge-on."""
    return {'Type': 'Ribbon', 'Ribbon/CrossSection': 'Cross' if cross else 'Plane', 'Ribbon/Count': count,
            'Ribbon/Vertices': vertices, 'Ribbon/Rotate': turns,
            'Ribbon/RibbonScales': [width_start, width_start if width_end is None else width_end],
            'Ribbon/RibbonAngles': list(twist), 'Ribbon/RibbonNoises': list(jitter)}


def noise(curl=None, wave=None, tilt=None, curl_freq=1.0, wave_freq=1.0, tilt_freq=0.5):
    """Shape noise, baked once into the model. curl/wave: [x, y, z] powers; tilt: [x, y] powers (bends along height)."""
    s = {}
    if curl:
        s.update({'ShapeNoise/CurlNoisePower': list(curl), 'ShapeNoise/CurlNoiseFrequency': [curl_freq] * 3})
    if wave:
        s.update({'ShapeNoise/WaveNoisePower': list(wave), 'ShapeNoise/WaveNoiseFrequency': [wave_freq] * 3})
    if tilt:
        s.update({'ShapeNoise/TiltNoisePower': list(tilt), 'ShapeNoise/TiltNoiseFrequency': [tilt_freq] * 2})
    return s


def rows(upper=None, middle=None, lower=None):
    """Vertex colour by row: Upper = the profile's last point (top), Lower = its first."""
    out = {}
    for row, colour in zip(ROWS, (upper, middle, lower)):
        if colour:
            out.update({'VertexColor/Color%s%s' % (row, c): list(colour) for c in COLUMNS})
    return out


def fade_rows(*which):
    """Alpha 0 on whole rows: soften a mesh's open ends ('Upper', 'Middle', 'Lower')."""
    return {'VertexColor/Color%s%s' % (r, c): [255, 255, 255, 0] for r in which for c in COLUMNS}


def soft_edges():
    """Alpha 0 on a ribbon strip's two edges (Left/Right columns): soft strands with no texture."""
    return {'VertexColor/Color%s%s' % (r, c): [255, 255, 255, 0] for r in ROWS for c in ('Left', 'Right')}


def uv(size, position=(0, 0)):
    """The model's own UV remap: uv * size + position. Repeat a texture, or crop it to its drawn part."""
    return {'UV/UVSize': list(size), 'UV/UVPosition': list(position)}


def facet_uv(around, along):
    """One texture copy per facet quad (Divisions - 1 each way): a cell texture then draws the mesh's own edges."""
    return uv([around - 1, along - 1])


def card(height, radius, crop_pos=(0, 0), crop_size=(1, 1), tile_aspect=1.0):
    """A curved card (partial sweep of a cylinder) showing one texture once at its own proportions.
    crop_* select the drawn part of the texture; tile_aspect is the texture's width/height in pixels."""
    width = height * tile_aspect * crop_size[0] / crop_size[1]
    half = math.degrees(width / radius / 2)
    return merge(cylinder(radius, radius, height), {'Mesh/AngleBeginEnd': [-half, half]}, divisions(12, 2), uv(crop_size, crop_pos))


def repeats(length, width, tile_aspect):
    """How many times a tile of width:height `tile_aspect` fits along a strip of this length and width, unstretched."""
    return length / (width / tile_aspect)


# ------------------------------------------------------------------ curves (frames)

def scale_keys(x, y=None, z=None):
    """Scale FCurves on a frame timeline: [[frame, value], ...] per axis. Use with Spec.keys()."""
    y, z = y or x, z or x
    return ({'ScalingValues/Type': 'FCurve', 'ScalingValues/FCurve/FCurve/Timeline': 'Time'},
            [{'curve': 'ScalingValues/FCurve/FCurve/' + a, 'points': p} for a, p in (('X', x), ('Y', y), ('Z', z))])


def spin_keys(y_points):
    return ({'RotationValues/Type': 'RotationFCurve', 'RotationValues/RotationFCurve/FCurve/Timeline': 'Time'},
            [{'curve': 'RotationValues/RotationFCurve/FCurve/' + a, 'points': p}
             for a, p in (('X', [[0, 0], [1, 0]]), ('Y', y_points), ('Z', [[0, 0], [1, 0]]))])


# ------------------------------------------------------------------ the runtime's Spline4, for previewing a profile

def spline4_rows(points, along):
    """Where the runtime puts the vertex rows of a Spline4 profile: a port of RotatorSpline3 / SplineGenerator
    (natural cubic through the points, rows spaced by distance along the control polygon). Returns [(radius, height)]."""
    a = [list(p) for p in points]
    n = len(a)
    b = [[0.0, 0.0] for _ in a]; c = [[0.0, 0.0] for _ in a]; d = [[0.0, 0.0] for _ in a]; w = [[0.0, 0.0] for _ in a]
    for i in range(1, n - 1):
        c[i] = [(a[i - 1][k] - 2 * a[i][k] + a[i + 1][k]) * 3 for k in (0, 1)]
    for i in range(1, n - 1):
        tmp = [4 - w[i - 1][k] for k in (0, 1)]
        c[i] = [(c[i][k] - c[i - 1][k]) / tmp[k] for k in (0, 1)]
        w[i] = [1 / tmp[k] for k in (0, 1)]
    for i in range(n - 2, 0, -1):
        c[i] = [c[i][k] - c[i + 1][k] * w[i][k] for k in (0, 1)]
    for i in range(n - 1):
        d[i] = [(c[i + 1][k] - c[i][k]) / 3 for k in (0, 1)]
        b[i] = [a[i + 1][k] - a[i][k] - c[i][k] - d[i][k] for k in (0, 1)]

    def value(t):
        j = max(int(math.floor(t)), 0)
        dt = t - j
        return tuple(a[j][k] + (b[j][k] + (c[j][k] + d[j][k] * dt) * dt) * dt for k in (0, 1))

    dist = [math.dist(points[i], points[i + 1]) for i in range(n - 1)]
    total = sum(dist)

    def position(v):
        left = total * min(max(v, 0.0), 1.0)
        for i, di in enumerate(dist):
            if left <= di:
                return value(i + left / di)
            left -= di
        return value(len(dist))
    return [position(r / (along - 1)) for r in range(along)]


# ------------------------------------------------------------------ the spec

class Spec:
    """An edit spec for a new effect built on a `blank` base (`Niagara2Efk blank <file>`)."""

    def __init__(self, base, name, end_frame=None, comment=None):
        self.base = str(Path(base).resolve())
        self.data = {'//': comment or 'Built with tools/author/efkauthor.py', 'source': self.base, 'name': name, 'procedural': [], 'edits': []}
        if end_frame:
            self.data['endFrame'] = end_frame

    def model(self, name, settings, copy_of=None):
        """Add a procedural model (from the defaults, or a copy of model `copy_of` by index)."""
        op = {'add': name, 'set': settings}
        if copy_of is not None:
            op['from'] = copy_of
        self.data['procedural'].append(op)
        return self

    def node0(self, name, settings):
        """Set up the base's own node (index 0)."""
        return self._op({'node': '0', 'set': merge({'Name': name}, neutral(), settings)})

    def fresh(self, name, settings, parent='root'):
        """A new part copied from the blank FILE: inherits nothing from the other parts."""
        return self._op({'node': parent, 'add': {'file': self.base, 'from': '0', 'name': name}, 'set': merge(neutral(), settings)})

    def copy(self, source, name, settings, parent='root'):
        """A copy of node `source` of THIS effect, with everything set on it so far (then `settings`)."""
        return self._op({'node': parent, 'add': {'from': source, 'name': name}, 'set': settings})

    def keys(self, *parts):
        """Attach curve parts (scale_keys / spin_keys results) to the last op."""
        op = self.data['edits'][-1]
        for settings, keys in parts:
            op['set'].update(settings)
            op.setdefault('keys', []).extend(keys)
        return self

    def _op(self, op):
        self.data['edits'].append(op)
        return self

    def write(self, path):
        Path(path).write_text(json.dumps(self.data, indent=2) + '\n', encoding='utf-8')
        return Path(path)

    def build(self, out, spec_path=None, tool=DEFAULT_TOOL):
        """Write the spec beside `out` (or at spec_path) and run `edit`. Raises on anything but VERIFY OK; returns
        the tool's report lines (VERIFY, WARNING)."""
        out = Path(out).resolve()
        spec_path = Path(spec_path) if spec_path else out.with_suffix('.edit.json')
        self.write(spec_path)
        r = subprocess.run(['dotnet', str(tool), 'edit', str(spec_path), str(out)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        report = [l for l in r.stdout.splitlines() if l.startswith(('VERIFY', 'WARNING'))]
        if r.returncode != 0 or not any(l.startswith('VERIFY OK') for l in report):
            raise RuntimeError('edit failed (%d):\n%s\n%s' % (r.returncode, r.stdout[-2000:], r.stderr[-2000:]))
        return report


def make_blank(path, life=60, tool=DEFAULT_TOOL):
    """Create the neutral base with the `blank` command."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['dotnet', str(tool), 'blank', str(path), str(life)], capture_output=True, text=True)
    if 'BLANK OK' not in r.stdout:
        raise RuntimeError('blank failed:\n' + r.stdout + r.stderr)
    return path
