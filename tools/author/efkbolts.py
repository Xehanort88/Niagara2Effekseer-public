"""Lightning from procedural bolt strands: build a bolt KIT effect once, then copy its parts into any effect with `edit`.

The rules these helpers keep are in README section "Procedural lightning bolts" (all found the hard way):
  - a bolt is a Ribbon strand whose kinks come from few vertex rows plus noise that changes fast UP the bolt and
    slowly ACROSS it (no plank-shaped segments if the sideways noise stays below the row spacing);
  - the user's tiling bolt texture rides the strand, its drawn line centred with the UV scroll Start;
  - strikes overlap and fade (life 6, fading over 5) and the texture flows down the strand: no hard 3-frame cuts;
  - never a bolt without an impact under it: each strike's flash and sparks spawn ON the strand's bottom-row
    vertices (GenerationLocation Model, Type Vertex), so they follow the foot wherever the noise put it, and the
    flash outlives its bolt by a frame;
  - forks are short and point 20-50 degrees below horizontal, so one that reaches the floor lands in the foot flash;
  - free-floating arcs start from a spark and (near the floor) point up or sideways;
  - colour a coloured texture by REMAPPING its brightness, not by tinting it; colour ranges vary ONE channel;
  - draw additively (a dark outline in a texture vanishes; normal blending showed it as an ugly black edge).

Standard library + Pillow. Typical use (a pack effect with its own impact, bolts striking onto it):

    import sys; sys.path.insert(0, 'tools/author')
    import efkbolts as eb
    kit = eb.make_kit('out/edits/<task>/kit', ['boltA.png', 'boltB.png', 'boltA.png'], ['boltB.png', 'boltA.png'])
    ops = eb.strikes(kit, first_root=5, first=8)            # the target effect already has root nodes 0..4
    spec = {'source': 'Pack.efkefc', 'name': 'X', 'edits': hide_pack_bolt + ops}

Test: python tools/author/test_efkbolts.py --work out/tests/bolts
"""
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import efkauthor as ea  # noqa: E402

HEIGHT = 9.0           # a bolt strand's model height; the kit's bolt nodes scale it by SCALE
SCALE = 1.75           # 9 x 1.75: from the target to the top of the battle frame
TILE = (128, 256)      # the tiling bolt textures this was built for (width, height in pixels)
WHITE = [255, 255, 255, 255]


# ------------------------------------------------------------------ textures

def line_centre(path, edge_rows=4):
    """Where a tiling vertical bolt crosses the tile's top and bottom edges (where one tile joins the next): the mean
    column (0..1) of the brightest pixels in the first and last `edge_rows` rows (brightness = max channel x alpha).
    Not the average over the whole tile: a drawn bolt wanders (the user's bolt B leaves both edges at u 0.26 but bulges
    to u 0.7 in the middle, averaging 0.57)."""
    im = Image.open(path).convert('RGBA')
    w, h = im.size
    px = im.load()
    total = weight = 0.0
    for y in list(range(edge_rows)) + list(range(h - edge_rows, h)):
        row = [max(px[x, y][:3]) * px[x, y][3] / 255 for x in range(w)]
        top = max(row)
        if top <= 0:
            continue
        for x, v in enumerate(row):
            if v >= 0.6 * top:
                total += (x + 0.5) * v
                weight += v
    return total / weight / w if weight else 0.5


def centred(path, out_dir=None):
    """A copy of a tiling bolt texture with its drawn line in the middle, padded on one side with empty pixels, and
    the width factor (new width / old width). Returns (path, factor).

    Not a UV scroll shift: shifting wraps the texture round, so branches near the far side came back at the strip's
    opposite edge and were cut by the strip's edge (user, Levin Barrage 2026-10-06: arcs "end or start in nothing
    mid-arc"; bolt B, line at u 0.26, branches out to u 0.88). Padding keeps every branch inside the tile. The strip
    must then be `factor` times wider so the drawn line keeps its width on screen (make_kit does that). Content-named
    copy in <out_dir or the texture's folder>/centred/."""
    path = Path(path)
    im = Image.open(path).convert('RGBA')
    w, h = im.size
    c = line_centre(path) * w
    half = math.ceil(max(c, w - c)) + 2
    nw = 2 * half
    opaque = im.getextrema()[3][0] == 255
    out = Image.new('RGBA', (nw, h), (0, 0, 0, 255 if opaque else 0))
    out.paste(im, (int(round(half - c)), 0))
    digest = hashlib.sha1(out.tobytes()).hexdigest()[:8]
    d = Path(out_dir) if out_dir else path.parent / 'centred'
    d.mkdir(parents=True, exist_ok=True)
    named = d / ('%s_c%s.png' % (path.stem, digest))
    if not named.exists():
        out.save(named)
    return named, nw / w


def remap(src, dst, ramp):
    """Recolour by brightness: each pixel's max channel (0..1) goes through `ramp` [(t, (r, g, b)), ...] from t 0 to
    1; alpha is kept. Black -> hue -> white keeps a white-hot core while the glow takes the hue (tinting a coloured
    texture by node colour multiplies: yellow x violet = mud)."""
    im = Image.open(src).convert('RGBA')
    out = Image.new('RGBA', im.size)
    pi, po = im.load(), out.load()

    def at(v):
        for (a, ca), (b, cb) in zip(ramp, ramp[1:]):
            if v <= b:
                t = (v - a) / (b - a) if b > a else 0
                return tuple(int(round(x + (y - x) * t)) for x, y in zip(ca, cb))
        return tuple(ramp[-1][1])

    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = pi[x, y]
            po[x, y] = at(max(r, g, b) / 255) + (a,)
    out.save(dst)
    return Path(dst)


def haze_cut(src, dst, floor=60):
    """A copy with everything dimmer than `floor` (0..255, per channel) set to black and the rest stretched back to
    full range. A texture drawn additively adds ALL of its light: the user's Lightningrep_C1 has no true black (median
    16) and 92% of its light was the grey cloudy background, which covered a sphere in grey glass; cut at 40 only the
    crackle remains (60 also removed most of its lines, which are 60-133 bright; lightningrep-study, 2026-10-06). Alpha is kept; a pointwise change keeps a tile seamless."""
    im = Image.open(src).convert('RGBA')
    r, g, b, a = im.split()
    lut = [0 if v <= floor else int(round((v - floor) * 255 / (255 - floor))) for v in range(256)]
    Image.merge('RGBA', [c.point(lut) for c in (r, g, b)] + [a]).save(dst)
    return Path(dst)


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def seamless_y(src, dst, seams=(0,), band=24, shift=None):
    """A copy whose joins between rows are healed: within `band` rows of each row in `seams` (0 = where the tile's
    bottom meets the next tile's top) it crossfades into the same texture shifted down by `shift` rows (default a
    quarter of its height), whose rows run on unbroken there. The shifted copy's own joins (each seam + shift) must
    lie outside every band. For the user's Lightningrep_A: the column pinched and most lines ended at the tile join
    (22.5 between the last and first rows against 12.2 between neighbouring rows), and the drawing had been offset
    once already, leaving a worse join across its middle (rows 127/128: 27.9); heal with seams (0, 128). Rows away
    from the seams are untouched. Keep `band` narrow: 48 rows on A smeared a double-exposed band, 24 did not. Find
    joins by eye in the texture tiled with itself, then confirm with the brightness jump between those rows against
    the typical jump (an automatic threshold also flagged every horizontal stroke)."""
    im = Image.open(src).convert('RGBA')
    w, h = im.size
    shift = h // 4 if shift is None else shift % h

    def dist(y, r):           # rows between row boundary r (between r-1 and r) and row y's centre, round the wrap
        d = abs(y + 0.5 - r) % h
        return min(d, h - d)
    for r in seams:
        if dist(r + shift - 0.5, r) < 1:
            raise ValueError('shift %d moves seam %d onto itself' % (shift, r))
        for q in seams:
            if dist((q + shift) % h - 0.5, r) < band:
                raise ValueError("the shifted copy's join at row %d falls inside the band round seam %d" % ((q + shift) % h, r))
    rolled = Image.new('RGBA', (w, h))
    rolled.paste(im.crop((0, h - shift, w, h)), (0, 0))
    rolled.paste(im.crop((0, 0, w, h - shift)), (0, shift))
    mask = Image.new('L', (w, h))
    for y in range(h):
        near = min(dist(y, r) for r in seams)
        mask.paste(int(round(255 * (1 - _smooth(near / band)))), (0, y, w, y + 1))
    Image.composite(rolled, im, mask).save(dst)
    return Path(dst)


def soft_mask(src, dst, inner=0.45):
    """A copy faded out toward a circle's edge (full inside `inner` of the radius, smoothly to 0 at the edge): a
    texture whose lines run off every side of the tile (Lightningrep_B1, C1) is cut along a hard rectangle on a flat
    card. Turn the card to vary it (RotatedBillboard), do not scroll it (the mask would move with the UV). Opaque
    textures fade their colour (drawn additively), transparent ones their alpha."""
    im = Image.open(src).convert('RGBA')
    w, h = im.size
    mask = Image.new('L', (w, h))
    px = mask.load()
    for y in range(h):
        for x in range(w):
            d = math.hypot((x + 0.5) / w - 0.5, (y + 0.5) / h - 0.5) * 2
            px[x, y] = int(round(255 * _smooth((1 - d) / (1 - inner))))
    r, g, b, a = im.split()
    if a.getextrema()[0] == 255:
        rgb = Image.composite(im, Image.new('RGBA', (w, h), (0, 0, 0, 255)), mask)
        rgb.putalpha(a)
        rgb.save(dst)
    else:
        im.putalpha(Image.composite(a, Image.new('L', (w, h)), mask))
        im.save(dst)
    return Path(dst)


def glow_texture(path, n=128, hot=0.0):
    """A soft round glow, white, alpha falling off from the centre (`hot` > 0 adds a brighter core). Generated, so the
    kit needs no pack texture. Saved as <stem>_<content hash>.png and that path is returned: every build copies its
    textures beside the effect (and installs into a game folder's shared Texture/), and a changed texture under an
    old name would be hash-renamed there and fail the read-back; a content-named file never collides."""
    im = Image.new('RGBA', (n, n))
    px = im.load()
    for y in range(n):
        for x in range(n):
            d = math.hypot((x + 0.5) / n - 0.5, (y + 0.5) / n - 0.5) * 2
            # (1 - d)^2 fell off so fast that a strike's foot flash read as a few faint dots in battle (Levin Barrage
            # audit, 2026-10-06); a broader falloff reads like the pack's soft circle glows
            a = max(0.0, 1 - d) ** 1.2 * (1 - 0.35 * d) + hot * math.exp(-(d / 0.15) ** 2)
            px[x, y] = (255, 255, 255, int(255 * min(1.0, a)))
    path = Path(path)
    digest = hashlib.sha1(im.tobytes()).hexdigest()[:8]
    named = path.with_name('%s_%s%s' % (path.stem, digest, path.suffix))
    if not named.exists():
        named.parent.mkdir(parents=True, exist_ok=True)
        im.save(named)
    return named


def rng(lo, hi):
    """A colour range for a ColorWithRandom slot. Effekseer draws each channel at random on its own, so a range on
    several channels gives off-hue streaks: only ONE channel may differ."""
    if sum(a != b for a, b in zip(lo[:3], hi[:3])) > 1:
        raise ValueError('colour range varies more than one RGB channel: %s .. %s' % (lo, hi))
    return {'min': list(lo), 'max': list(hi)}


# ------------------------------------------------------------------ models

def side_fade():
    """Fade the strip to transparent at its two side edges (vertex alpha). A Plane strip has three vertices across it
    (u 0, 0.5, 1), so the fade is linear from the centre line to each edge (ColorCenterArea cannot widen it).
    The user's bolt drawings carry a soft haze across most of the tile, which a strip showed as a hard-edged band
    (user, Levin Barrage 2026-10-06), and a branch reaching the strip's side was cut there: now both fade out."""
    return ea.soft_edges()


def bolt_model(seed, width=2.2, tip=0.8, vertices=10, wander=0.45, zig=0.35, zig_freq=1.2, tile=(1, 2), uv_shift=None,
               fade_top=True):
    """A vertical bolt strand from the ground (y 0) to HEIGHT, for a tiling bolt texture (defaults: the 372 bolt,
    two tiles up the strand). `seed` moves the noise: a different bolt. Plane cross-section: at sweep angle 0 its
    width runs along X, facing the battle camera. `fade_top`: the strand fades out over its upper half (a bolt from the
    sky melting into it); False keeps it solid to its top end, for a beam whose top is a lit source."""
    return ea.merge(ea.spline4((0.05, 0), (0.05, HEIGHT / 3), (0.05, 2 * HEIGHT / 3), (0.05, HEIGHT)),
                    ea.ribbon(1, 0, width * tip, width, vertices=vertices, cross=False),
                    {'ShapeNoise/WaveNoisePower': [wander, 0, wander * 0.5], 'ShapeNoise/WaveNoiseFrequency': [0.45, 1, 0.45],
                     'ShapeNoise/WaveNoiseOffset': [seed * 1.7, 0, seed * 0.9],
                     'ShapeNoise/CurlNoisePower': [zig, 0, zig * 0.6], 'ShapeNoise/CurlNoiseFrequency': [0.25, zig_freq, 0.25],
                     'ShapeNoise/CurlNoiseOffset': [seed * 3.1, seed * 5.3, seed * 2.3]},
                    ea.fade_rows('Upper') if fade_top else {}, side_fade(),
                    # uv_shift 0: the foot on a tile edge, where a centred() texture's line crosses the strip's middle
                    ea.uv(list(tile), [0, (seed - 1) * 0.37 if uv_shift is None else uv_shift]))


class _Perlin:
    """Effekseer's PerlinNoise (Noise/Effekseer.PerlinNoise.h, 1.80.3), its own LCG shuffle included."""

    def __init__(self, seed):
        s = seed & 0xffffffff

        def rand():
            nonlocal s
            s = (s * 1103515245 + 12345) & 0xffffffff & 2147483647
            return (s % 0x7fff) / (0x7fff - 1)
        p = list(range(256))
        for i in range(256):
            t = int(rand() * 255)
            p[i], p[t] = p[t], p[i]
        self.p = p + p

    @staticmethod
    def _grad(h, x, y, z):
        h &= 15
        u = x if h < 8 else y
        v = y if h < 4 else (x if h in (12, 14) else z)
        return (-u if h & 1 else u) + (-v if h & 2 else v)

    def noise(self, x, y, z):
        p, g = self.p, self._grad
        fx, fy, fz = math.floor(x), math.floor(y), math.floor(z)
        xi, yi, zi = int(fx) & 0xff, int(fy) & 0xff, int(fz) & 0xff
        x, y, z = x - fx, y - fy, z - fz
        u, v, w = (t * t * t * (t * (t * 6 - 15) + 10) for t in (x, y, z))
        a0 = p[xi] + yi; a1 = p[a0] + zi; a2 = p[a0 + 1] + zi
        b0 = p[xi + 1] + yi; b1 = p[b0] + zi; b2 = p[b0 + 1] + zi
        lerp = lambda t, a, b: a + t * (b - a)  # noqa: E731
        c = [lerp(u, g(p[a], x, y - dy, z - dz), g(p[b], x - 1, y - dy, z - dz))
             for a, b, dy, dz in ((a1, b1, 0, 0), (a2, b2, 1, 0), (a1 + 1, b1 + 1, 0, 1), (a2 + 1, b2 + 1, 1, 1))]
        return lerp(w, lerp(v, c[0], c[1]), lerp(v, c[2], c[3]))

    def octave(self, n, x, y, z):
        total, amp = 0.0, 1.0
        for _ in range(n):
            total += self.noise(x, y, z) * amp
            x, y, z, amp = 2 * x, 2 * y, 2 * z, amp * 0.5
        return total * 0.5 + 0.5


_CURL = _Perlin(0)   # CurlNoise(0, 1, 2) in ProceduralModelGenerator: seed 0 for all three axes


def _curl(x, y, z):
    """Effekseer's CurlNoise::Get (seed 0, scale 1, 2 octaves)."""
    e, n = 1.0 / 1024.0, lambda *q: _CURL.octave(2, *q)  # noqa: E731
    dyz = n(x, y, z + e) - n(x, y, z - e)    # d/dz of the y and z noises (the same table: seed 0 for all)
    dzy = n(x, y + e, z) - n(x, y - e, z)
    dxz = n(x + e, y, z) - n(x - e, y, z)
    # x = d(zn)/dy - d(yn)/dz, y = d(xn)/dz - d(zn)/dx, z = d(yn)/dx - d(xn)/dy; xn = yn = zn
    return ((dzy - dyz) / (2 * e), (dyz - dxz) / (2 * e), (dxz - dzy) / (2 * e))


def shape_noise(model, point):
    """Where a procedural model's ShapeNoise moves `point` (x, y, z): a port of the noise in Effekseer's
    ProceduralModelGenerator (tilt noise is not ported: a model with TiltNoisePower raises)."""
    if any(model.get('ShapeNoise/TiltNoisePower', [0, 0])):
        raise ValueError('tilt noise is not ported')
    x, y, z = point
    wp, wf, wo = (model.get('ShapeNoise/WaveNoise' + k, [0, 0, 0]) for k in ('Power', 'Frequency', 'Offset'))
    v = [c + math.sin(y * wf[a] + wo[a]) * wp[a] for a, c in enumerate((x, y, z))]
    cp, cf, co = (model.get('ShapeNoise/CurlNoise' + k, [0, 0, 0]) for k in ('Power', 'Frequency', 'Offset'))
    c = _curl(*(v[a] * cf[a] + co[a] for a in range(3)))
    return tuple(v[a] + c[a] * cp[a] for a in range(3))


# Noise seeds whose foot lands on the bolt's origin (|foot| under 0.02 bolt units; searched with shape_noise over seeds
# 1-40, 2026-10-07). With these a strike can lean and turn at random and its foot still stays put; the kit's old seeds
# 1, 2, 3 put the foot up to 1.56 units off (pinning them only holds at one fixed lean).
FOOT_SEEDS = (16.78, 36.48, 27.61)
# units a held strike stands above the ground (0: on it; a soft_foot kit hides the strip's bottom cut instead: raised
# 0.25, the cut floated visibly above the ground once the foot glow faded, in the editor's view)
HELD_LIFT = 0.0
SOFT_FOOT = 0.04      # make_kit(soft_foot): the strand's last 4% (0.6 units at the kit's scale) fades into its foot


def bolt_foot(i, scale=SCALE, lean=0, seeds=None):
    """Where kit bolt `i`'s foot (the strip's bottom-centre vertex, where its drawn line meets the ground) ends up,
    from the bolt node's origin, in the parent's units: the shape noise moves it about a bolt unit sideways (user,
    2026-10-07: ThunderFall calm's bolt landed ~1 unit beside the pack's explosion). `lean`: the node's Z turn.
    strikes(pin_feet=True) moves each bolt by minus this, so its foot lands on its node's origin."""
    fx, fy, fz = shape_noise(bolt_model((seeds or (1, 2, 3))[i]), (0.0, 0.0, 0.05))   # a Spline4 at radius 0.05, at angle 0
    a = math.radians(lean)
    return (scale * (fx * math.cos(a) - fy * math.sin(a)), scale * (fx * math.sin(a) + fy * math.cos(a)), scale * fz)


def foot_point(i, scale=SCALE, lift=0.0, seeds=None):
    """A child's Fixed location that puts it on kit bolt `i`'s drawn foot, `lift` units above it (the bolt's own
    frame, so not leaned; for a NotBind-scale child, whose location the bolt's scale does not multiply)."""
    fx, _, fz = bolt_foot(i, scale, 0, seeds)
    return [round(fx, 4), lift, round(fz, 4)]


def fork_model(widen=1.0):
    """A short strand, 3 units, gently bent, fading at its far end: forks and free arcs (`widen`: a centred
    texture's factor). The jaggedness comes from the drawn texture: with 6 rows and strong noise the strand folded
    into one sharp V and read as a glossy blade (user, 2026-10-06)."""
    return ea.merge(ea.spline4((0.05, 0), (0.05, 1), (0.05, 2), (0.05, 3)),
                    ea.ribbon(1, 0, 0.6 * widen, 0.25 * widen, vertices=10, cross=False),
                    {'ShapeNoise/CurlNoisePower': [0.18, 0, 0.12], 'ShapeNoise/CurlNoiseFrequency': [0.25, 1.2, 0.25],
                     'ShapeNoise/CurlNoiseOffset': [7, 2, 5]},
                    ea.fade_rows('Upper'), side_fade())


# ------------------------------------------------------------------ node settings

def flowing(texture, speed=-24, tiles=1.0):
    """The texture scrolling down the strand (every frame moves). Pass a `centred()` copy: no sideways shift, so
    nothing wraps round the strip's edges. `tiles`: how much of the texture's height spans the strand's length (see
    arc_tiles); 1 = the whole tile."""
    size = Image.open(texture).size
    return {'RendererCommonValues/ColorTexture': str(texture), 'RendererCommonValues/UV': 'Scroll',
            'RendererCommonValues/UVScroll/Size/X': size[0], 'RendererCommonValues/UVScroll/Size/Y': round(size[1] * tiles, 2),
            'RendererCommonValues/UVScroll/Start/X': 0, 'RendererCommonValues/UVScroll/Start/Y': 0,
            'RendererCommonValues/UVScroll/Speed/X': 0, 'RendererCommonValues/UVScroll/Speed/Y': speed}


def arc_tiles(length, width):
    """Texture tiles along a fork/arc strand scaled [width, length, length], so the drawing keeps its own proportions.
    The strand is 3 units long and 0.6 x widen wide; a centred tile is widen x 128 by 256, i.e. its height is
    width / (0.5 widen) units, so tiles = 3 length / (0.6 widen width / (0.5 widen)) = 2.5 length / width. With the
    whole tile on every strand (tiles 1) a short fork squashed the drawing 2.5x and its zigzag collapsed into smooth
    streaks (user, 2026-10-06)."""
    return 2.5 * length / width


def fading(life, fade):
    return {'CommonValues/Life': life, 'RendererCommonValues/FadeInType': 'None',
            'RendererCommonValues/FadeOutType': 'WithinLifetime', 'RendererCommonValues/FadeOut/Frame': fade}


def on_vertices(model, count):
    """Spawn `count` children on the first `count` vertices of procedural model `model`, in order. On a Plane strand
    vertices 0, 1, 2 are the left edge, centre and right edge of its bottom row (the foot), 3-5 the next row."""
    return {'GenerationLocationValues/Type': 'Model', 'GenerationLocationValues/Model/ModelReference': 'ProceduralModel',
            'GenerationLocationValues/Model/Reference': model, 'GenerationLocationValues/Model/Type': 'Vertex',
            'CommonValues/MaxGeneration': count, 'CommonValues/GenerationTime': 0.1, 'CommonValues/GenerationTimeOffset': 0}
    # GenerationTime 0.1: ten per frame, so all `count` appear in the strike's first frame. With 0 they came one per
    # frame and a strike's first frame had only the left-edge glow (lightning_audit, ThunderFall f9, 2026-10-06).


FLASH = [255, 225, 140, 160]
FLASH_SIZE = 2.3
FLASH_LIFE, FLASH_FADE = 10, 6   # outlasts the bolt (BOLT_LIFE 7) by 3 frames: a bolt fades out before its light, never
                                 # with it (user rule 2026-10-07; at 7/4 Levin Barrage's fading bolts stood unlit, f110-117)
SPARK = [255, 240, 180, 255]
ARC = [255, 255, 255, 220]
ARC_SPARK = [255, 245, 215, 255]
ARC_SPARK_SIZE = 1.4   # units; 0.5 of a soft glow was too faint to read as a lit point on a normal backdrop
FORK = [255, 255, 255, 200]
FORK_SCALE = [2.5, 0.4, 0.4]   # 2.5x wide so a drawn line shows at fork width; 0.4 long: 3 x 0.4 x 1.75 = 2.1 units
FORK_TURN = {'left': [100, 125], 'right': [-125, -100]}   # degrees from up: 10-35 below horizontal (20-50: a fork from low on
                                                          # the bolt still reached the floor beside the flash, 307 f20, 402 f50)
WIDE_FLASH = 1.8      # a widened kit's single foot flash, x the plain padding factor (see make_kit); at 1.4 a low fork reached the floor outside it (-12_3, f29)
BOLT_LIFE = 7         # a kit bolt's life in frames (fades in over 1, out over 5)
HEAD_SIZE = 1.8       # units: the white-hot spark on a travelling strike's tip (as ARC_SPARK_SIZE, the audit's lit point)
HEAD_LIFT = 1.0       # units above the strip's foot (NotBind: not scaled by the falling bolt)
HEAD_FAINT = 60       # alpha of the tip glow down the first segments (the bright spark takes the last one)
BURST = [255, 255, 255, 230]
BURST_SIZE = 3.2       # bolt units (x SCALE 1.75: ~5.6 units across)
BURST_LIFT = 1.4       # bolt units: the card's masked circle (radius ~1.6) rests on the ground


def foot_fade_texture(path, fade):
    """A 4x256 alpha ramp: opaque, then fading to 0 over the last `fade` of its height (smoothly). Mapped once along
    a bolt strand by soft_foot(), clamped: the strand's foot fades out instead of ending in a straight cut."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new('RGBA', (4, 256))
    px = im.load()
    for y in range(256):
        t = (y + 0.5) / 256
        a = 255 if t < 1 - fade else int(round(255 * (1 - _smooth((t - (1 - fade)) / fade))))
        for x in range(4):
            px[x, y] = (a, a, a, a)
    named = path.with_name('%s_%d.png' % (path.stem, round(fade * 1000)))
    im.save(named)
    return named


def reveal_texture(path, travel, soft=None, edge=0.08):
    """An alpha flipbook of travel+1 cells (4x256 each, side by side): cell k shows the strand down to (k+1)/(travel+1)
    of its length with a soft leading edge `edge` long; the last cell the whole strand, its foot fading over `soft`
    (None: fully opaque). Played once by reveal(), it holds the last cell."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = travel + 1
    im = Image.new('RGBA', (4 * n, 256))
    px = im.load()
    for cell in range(n):
        b = (cell + 1) / n
        for y in range(256):
            t = (y + 0.5) / 256
            if cell < n - 1:
                a = 1.0 if t <= b - edge else (0.0 if t >= b else 1 - _smooth((t - (b - edge)) / edge))
            else:
                a = 1.0 if not soft or t < 1 - soft else 1 - _smooth((t - (1 - soft)) / soft)
            v = int(round(255 * a))
            for x in range(4):
                px[4 * cell + x, y] = (v, v, v, v)
    named = path.with_name('%s_t%d_s%d.png' % (path.stem, travel, round((soft or 0) * 1000)))
    im.save(named)
    return named


def reveal(texture, travel):
    """Node settings: `texture` (reveal_texture) as the alpha flipbook, one cell a frame, once (holds the last)."""
    A = 'AdvancedRendererCommonValuesValues/AlphaTextureParam/'
    return {A + 'Enabled': True, A + 'Texture': str(texture), A + 'Filter': 'Linear', A + 'Wrap': 'Clamp', A + 'UV': 'Animation',
            A + 'UVAnimation/Start': [0, 0], A + 'UVAnimation/Size': [4, 128], A + 'UVAnimation/FrameLength': 1,
            A + 'UVAnimation/FrameCountX': travel + 1, A + 'UVAnimation/FrameCountY': 1, A + 'UVAnimation/LoopType': 'Once',
            A + 'UVAnimation/StartSheet': 0}


def strand_point(i, scale, frac, seeds=None):
    """Where kit strand `i`'s centre line is `frac` of its length down from its top (bolt frame, plain units)."""
    p = shape_noise(bolt_model((seeds or (1, 2, 3))[i]), (0.0, HEIGHT * (1 - frac), 0.05))
    return [round(c * scale, 4) for c in p]


def head_keys(i, scale, travel, seeds=None):
    """The tip spark's location keys: on the reveal's leading edge each frame, then on the foot (lifted HEAD_LIFT)."""
    n = travel + 1
    pts = [strand_point(i, scale, (k + 1) / n - 0.04, seeds) for k in range(travel)] + [foot_point(i, scale, HEAD_LIFT, seeds)]
    return [{'curve': 'LocationValues/LocationFCurve/FCurve/' + a, 'points': [[k, p[j]] for k, p in enumerate(pts)]}
            for j, a in enumerate('XYZ')]


def soft_foot(texture):
    """Node settings: the foot fade texture as the ALPHA texture, once along the whole strand (the strand's own UV
    runs 0..2 along it: two tiles of the drawing), clamped so nothing repeats. The bottom cut of a leaning strip is
    straight and tilted: half of it stood under the ground beside the foot in the editor's view (user, 2026-10-07)."""
    A = 'AdvancedRendererCommonValuesValues/AlphaTextureParam/'
    return {A + 'Enabled': True, A + 'Texture': str(texture), A + 'Filter': 'Linear', A + 'Wrap': 'Clamp',
            A + 'UV': 'Fixed', A + 'UVFixed/Start': [0, 0], A + 'UVFixed/Size': [4, 128]}


def make_kit(out_dir, bolt_textures, fork_textures, glow=None, burst=None, widen=1.0, tool=ea.DEFAULT_TOOL, seeds=None, haze=None,
             soft=None, fade_top=True):
    """Build `<out_dir>/BoltKit.efkefc` from a blank base:
         root 0, 1, 2  Bolt 0..2: a strike strand each (bolt_textures[i]), with children
                       0 Fork left, 1 Fork right (fork_textures[0], [1]), 2 Strike Flash (3 on the foot), 3 Strike Sparks
         root 3        Arc: a free arc strand (fork_textures[0]) with child 0 Base Spark
       `burst`: a branching-discharge card texture (a soft_mask copy, lines fading to a circle) adds child
       4 Strike Burst to every bolt: one card at the foot per strike, at a random turn, so each strike crackles out
       differently (the user's Lightningrep_B1, 2026-10-06).
       `widen`: every strand (bolts, forks, arc) this many times wider, its foot flash too. The kit fits 128 texture
       pixels across a 2.2-unit strand (the user's 128x256 bolts); a 256-wide drawing (the user's "-12_3" sheet,
       2026-10-06) came out squeezed to half its width, its thin bolt nearly invisible: widen=2 keeps its proportions.
       `seeds`: the three strands' noise seeds (FOOT_SEEDS: shapes whose foot stays on the origin at any lean or turn;
       their UV starts at a tile edge, where the centred drawing crosses the strip's middle). Default 1, 2, 3.
       `haze`: cut the bolt drawings' faint haze below this brightness (haze_cut) first. A strip draws ALL of its
       texture additively, and the user's drawings carry a faint haze across the whole tile: zoomed in, the strip's
       straight bottom edge showed beside the foot, tilted with the lean and half under the ground (user, editor
       screenshot of ThunderFall calm, 2026-10-07).
       `soft`: the strands' last `soft` of their length fade out into the foot (SOFT_FOOT; see soft_foot).
       `fade_top` False: the strands stay solid to their top (bolt_model): a beam fired out of a lit source, its top
       on the source (Sparkforge Blast, 2026-10-07: the faded upper half left the beams ~6 units short of it).
       Copy them into an effect with `strikes()` and `arcs()`. Returns the kit's path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # generated textures are content-named (see glow_texture); the kit's own copies of an earlier build are cleared
    shutil.rmtree(out_dir / 'Texture', ignore_errors=True)
    gen = out_dir / 'generated'
    gen.mkdir(exist_ok=True)
    glow = Path(glow) if glow else glow_texture(gen / 'BoltGlow.png', hot=0.3)
    blank = ea.make_blank(out_dir / 'blank.efkefc', 40, tool)
    spec = ea.Spec(blank, 'BoltKit', end_frame=40, comment='Bolt kit (tools/author/efkbolts.py make_kit).')
    if haze:
        (gen / 'haze').mkdir(exist_ok=True)
        bolt_textures = [haze_cut(t, gen / 'haze' / ('%s_h%d.png' % (Path(t).stem, haze)), haze) for t in bolt_textures]
    bolts0 = [centred(t, gen / 'centred') for t in bolt_textures]
    bolts = [(t, f * widen) for t, f in bolts0]
    # flash size per bolt (bolt units / FLASH_SIZE): three merged edge glows follow the strip's width; on a widened
    # strip they stood apart as spots beside the bolt, so there it is ONE larger glow at the foot (WIDE_FLASH x the
    # plain padding factor)
    flashes = [f for _, f in bolts] if widen == 1 else [WIDE_FLASH * f for _, f in bolts0]
    forks = [(t, f * widen) for t, f in (centred(t, gen / 'centred') for t in fork_textures)]
    for i in range(3):
        spec.model('Bolt%d' % i, bolt_model(seeds[i] if seeds else i + 1, width=2.2 * bolts[i][1],   # wider by the padding
                                            uv_shift=0 if seeds else None, fade_top=fade_top))
    spec.model('ForkL', fork_model(forks[0][1]))
    spec.model('ForkR', fork_model(forks[1][1]))
    for i, (tex, _) in enumerate(bolts):
        # fade in over 1 frame: a child (the foot flash) first draws one update after its parent, so without it every
        # strike's first frame showed the bolt with no impact under it (lightning_audit, 2026-10-06); life 7 keeps
        # the strike on screen as long as before
        bolt = ea.merge(ea.model_node('Bolt%d' % i, 'Add', WHITE), flowing(tex), fading(BOLT_LIFE, 5),
                        {'RendererCommonValues/FadeInType': 'Use', 'RendererCommonValues/FadeIn/Frame': 1},
                        {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                         'RotationValues/Type': 'PVA', 'RotationValues/PVA/Rotation/X': 0, 'RotationValues/PVA/Rotation/Y': [-20, 20],
                         'RotationValues/PVA/Rotation/Z': 0, 'RotationValues/PVA/Velocity/Y': 0,
                         'ScalingValues/Fixed/Scale': [SCALE] * 3,
                         'DrawingValues/ColorAll/Type': 'Random', 'DrawingValues/ColorAll/Random': {'min': [255, 255, 255, 170], 'max': WHITE}},
                        soft_foot(foot_fade_texture(gen / 'FootFade.png', soft)) if soft else {})
        if i == 0:
            spec.node0('Bolt 0', bolt)
        else:
            spec.fresh('Bolt %d' % i, bolt)
    for i in range(3):
        parent = str(i)
        for side, model, (tex, _) in (('left', 'ForkL', forks[0]), ('right', 'ForkR', forks[1])):
            spec.fresh('Fork ' + side, ea.merge(ea.model_node(model, 'Add', FORK), flowing(tex, tiles=arc_tiles(FORK_SCALE[1], FORK_SCALE[0])), fading(5, 4),
                                                {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                                                 'GenerationLocationValues/Type': 'Model', 'GenerationLocationValues/Model/ModelReference': 'ProceduralModel',
                                                 'GenerationLocationValues/Model/Reference': 'Bolt%d' % i, 'GenerationLocationValues/Model/Type': 'Random',
                                                 'RotationValues/Type': 'PVA', 'RotationValues/PVA/Rotation/X': 0, 'RotationValues/PVA/Rotation/Y': 0,
                                                 'RotationValues/PVA/Rotation/Z': FORK_TURN[side], 'RotationValues/PVA/Velocity/Z': 0,
                                                 'ScalingValues/Fixed/Scale': FORK_SCALE}), parent=parent)
        # a child takes the bolt's scale 1.75: sizes below are in the bolt's units
        # three glows on the foot's left edge, centre and right edge must merge into one: their size follows the
        # strip's width (a padded texture widens it; at 2.3 regardless they separated into three dim spots)
        # widened strips (widen != 1): the foot's edge vertices lie far from the drawn line, so flash and sparks start
        # at the bolt's origin instead (one bigger flash covers the foot's noise offset, about 1 bolt unit)
        at_foot = on_vertices('Bolt%d' % i, 3) if widen == 1 else {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1}
        spark_at = on_vertices('Bolt%d' % i, 6) if widen == 1 else {'CommonValues/MaxGeneration': 6, 'CommonValues/GenerationTime': 0.1}
        spark_x = 0 if widen == 1 else [-0.5, 0.5]
        spec.fresh('Strike Flash', ea.merge(ea.sprite(glow, 'Add', FLASH), fading(FLASH_LIFE, FLASH_FADE), at_foot,
                                            {'ScalingValues/Fixed/Scale': [FLASH_SIZE * flashes[i]] * 3}), parent=parent)
        spec.fresh('Strike Sparks', ea.merge(ea.sprite(glow, 'Add', SPARK), fading(10, 5), spark_at,
                                             {'ScalingValues/Fixed/Scale': [0.2] * 3, 'LocationValues/Type': 'PVA',
                                              'LocationValues/PVA/Location/X': spark_x, 'LocationValues/PVA/Location/Y': 0, 'LocationValues/PVA/Location/Z': 0,
                                              'LocationValues/PVA/Velocity/X': [-0.16, 0.16], 'LocationValues/PVA/Velocity/Y': [0.07, 0.18],
                                              'LocationValues/PVA/Velocity/Z': [-0.06, 0.06], 'LocationValues/PVA/Acceleration/Y': -0.017}), parent=parent)
        if burst:
            # a card, so RotatedBillboard (under Billboard a Z turn is ignored); no UV change, the mask must stay put
            # one frame late: the bolt fades in over its first frame, and a burst then lit the ground before any bolt
            # was there (ThunderFall f9, 2026-10-06)
            spec.fresh('Strike Burst', ea.merge(ea.sprite(burst, 'Add', BURST), fading(5, 4),
                                                {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                                                 'CommonValues/GenerationTimeOffset': 1,
                                                 'DrawingValues/Sprite/Billboard': 'RotatedBillboard',
                                                 # on the ground, not through it: centred at 0.3 the lower half of the
                                                 # card hung below the floor over the status window (audit, Shoot f66)
                                                 'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': [0, BURST_LIFT, 0],
                                                 'RotationValues/Type': 'PVA', 'RotationValues/PVA/Rotation/X': 0,
                                                 'RotationValues/PVA/Rotation/Y': 0, 'RotationValues/PVA/Rotation/Z': [0, 360],
                                                 'RotationValues/PVA/Velocity/Z': 0,
                                                 'ScalingValues/Fixed/Scale': [BURST_SIZE] * 3}), parent=parent)
    spec.fresh('Arc', ea.merge(ea.model_node('ForkL', 'Add', ARC), flowing(forks[0][0], tiles=arc_tiles(1.2, 3.0)), fading(6, 5),
                               {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                                'RotationValues/Type': 'PVA', 'RotationValues/PVA/Rotation/X': 0, 'RotationValues/PVA/Rotation/Y': [-30, 30],
                                'RotationValues/PVA/Rotation/Z': [0, 360], 'RotationValues/PVA/Velocity/Z': 0,
                                'ScalingValues/Fixed/Scale': [3.0, 1.2, 1.2]}))
    spark_tex = glow_texture(gen / 'SparkGlow.png', hot=0.6)
    spec.fresh('Base Spark', ea.merge(ea.sprite(spark_tex, 'Add', ARC_SPARK), fading(7, 6),
                                      {'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                                       'ScalingValues/Fixed/Scale': [ARC_SPARK_SIZE / 3.0, ARC_SPARK_SIZE / 1.2, ARC_SPARK_SIZE / 1.2]}), parent='3')
    out = out_dir / 'BoltKit.efkefc'
    spec.build(out, tool=tool)
    # strikes() sizes flashes by each strip's width factor
    out.with_suffix('.factors.json').write_text(json.dumps({'bolts': [f for _, f in bolts], 'forks': [f for _, f in forks], 'flash': flashes,
                                                            'burst': bool(burst),
                                                            'fork_h': [Image.open(t).size[1] for t, _ in forks],
                                                            'seeds': list(seeds or (1, 2, 3)), 'soft': soft}))
    return out


def kit_factors(kit):
    p = Path(kit).with_suffix('.factors.json')
    return json.loads(p.read_text()) if p.exists() else {'bolts': [1, 1, 1], 'forks': [1, 1]}


# ------------------------------------------------------------------ ops that copy kit parts into an effect

def strikes(kit, first_root, first=8, every=3, cycle=9, count=3, lean=(-16, -6), flash=None, sparks=None, fork=None,
            scale=SCALE, parent='root', flash_scale=None, fork_scale=None, travel=0, pin_feet=False, life=BOLT_LIFE,
            flow=None, shapes=(0, 1, 2)):
    """Edit ops adding the kit's three bolts under `parent` (default the ROOT) of an effect whose `parent` already
    has `first_root` children (a spawner parent gives every spawned site its own strikes): the
    three shapes take turns, the first at frame `first`, one every `every` frames, each shape `count` times `cycle`
    frames apart, leaning `lean` degrees on Z (None for upright). Optional colours for the flash / sparks / forks, the
    flash's size `flash_scale` (for a strip of the plain width; the kit's is FLASH_SIZE, and both are multiplied by
    the strip's width factor), the forks' `fork_scale` (the kit's FORK_SCALE widens a
    drawn bolt 2.5x; a soft core texture wants [1, 0.4, 0.4]). Other textures (a recolour): build a kit with them,
    since each strand's width is fitted to its texture's padding.

    `travel` = N frames: each strike draws itself DOWN from the sky over N frames (its top stays put, its height grows
    from nothing while its base slides to the ground; scale and location FCurves on frame time); its sparks and burst
    start one frame before the tip lands, its forks as it lands. A strike must not land before its
    impact; in the original ThunderFall the explosion starts while the bolt is still coming down (user rule,
    2026-10-07: the kit's instant strikes stood on the ground three frames before the explosion). The foot flash rides
    the tip down from the first frame (a glowing head). Time `first` so the effect's own impact starts during the
    travel. 0 = instant, as before.

    `pin_feet`: move each bolt so its drawn foot lands exactly on its node's origin (see bolt_foot; at the middle of
    `lean`, so a lean range leaves |foot| x half the range in radians: 0.14 units for (-16, -6); the random Y turn is
    dropped, it swung the foot up to 0.5 units). Without it the shape noise puts the foot up to ~1.5 units beside the
    origin (user, 2026-10-07: "why should the lightning be separate from the explosion").
    Either way the strike's own parts that do not spawn on the foot vertices are placed ON the foot: the burst card,
    the tip spark, and a widened kit's flash and sparks (they sat at the bolt's origin). A kit made with FOOT_SEEDS
    needs no pin and keeps its random turn.

    `life`: each strike's frames on screen (kit: BOLT_LIFE 7); its foot flash still outlasts it by 3. `flow`: the
    drawing's scroll speed down the strand (kit -24 px a frame; slower reads calmer, 0 holds the drawing still).
    `shapes`: which of the kit's three strands to use (one node each, numbered from `first_root` in this order)."""
    ops = []
    k = kit_factors(kit)
    seeds = k.get('seeds')
    for n, i in enumerate(shapes):
        node = ('' if parent == 'root' else parent + '/') + str(first_root + n)
        s = {'CommonValues/MaxGeneration': count, 'CommonValues/GenerationTime': cycle, 'CommonValues/GenerationTimeOffset': first + n * every,
             'RotationValues/PVA/Rotation/Z': list(lean) if lean else 0, 'ScalingValues/Fixed/Scale': [scale] * 3}
        if life != BOLT_LIFE:
            s.update({'CommonValues/Life': life, 'RendererCommonValues/FadeOut/Frame': max(2, life - 2)})
        if flow is not None:
            s['RendererCommonValues/UVScroll/Speed/Y'] = flow
        op = {'node': parent, 'add': {'file': str(kit), 'from': str(i), 'name': 'Bolt %d' % i}, 'set': s}
        off = bolt_foot(i, scale, sum(lean) / 2 if lean else 0, seeds)
        pin = [-c for c in off] if pin_feet else [0, 0, 0]
        if pin_feet and max(abs(c) for c in off) > 0.15:     # an off-centre foot: a turn would swing it off the pin
            s['RotationValues/PVA/Rotation/Y'] = 0
        if pin_feet and not travel:
            s.update({'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': [round(c, 4) for c in pin]})
        # the foot in the bolt's own units (a child bound to its scale) and in plain units (a NotBind child)
        fu = foot_point(i, 1.0, 0.0, seeds)
        k = kit_factors(kit)
        widened = 'flash' in k and k['flash'] != k['bolts']
        if travel:
            # the strike draws itself DOWN without squashing: the strand stands at full size where it lands from its
            # first frame, and an alpha flipbook reveals it from the top, one step a frame, holding its last cell
            # (full length, with the soft foot if the kit has one). Growing it by scale from 2% (then 15%) squashed
            # the whole drawing into a stub of flat horizontal streaks (user, 2026-10-07: "for most lightning
            # effects they start like in the screenshot and it looks a bit ugly")
            s.update({'RendererCommonValues/FadeInType': 'None', 'LocationValues/Type': 'Fixed',
                      'LocationValues/Fixed/Location': [round(c, 4) for c in pin]})
            s.update(reveal(reveal_texture(Path(kit).parent / 'generated' / 'Reveal.png', travel, k.get('soft')), travel))
        ops.append(op)
        if travel:
            # the flash rides the bolt's foot vertices, so from the first frame it is a glowing head travelling down
            # with the tip (started a frame before landing instead, the tip hung unlit for two frames: lightning_audit,
            # 'lone line', 2026-10-07); sparks and burst come a frame before landing
            late = {'CommonValues/GenerationTimeOffset': travel - 1}
            for child in ('3',) + (('4',) if kit_factors(kit).get('burst') else ()):
                ops.append({'node': node + '/' + child, 'set': dict(late)})
            # the flash must not take the bolt's height scale (near 0 while it draws down): it showed as a flat
            # horizontal disc at the tip. NotBind scale = its own size in plain units, so x the strike's scale here
            # (NotBind works in the 1.80.3 web runtime; NotBind_Root does not).
            # The full flash opens only when the tip touches the ground (frame `travel`). Riding the tip down from
            # the first frame at full size it read as the explosion falling with the bolt (user, 2026-10-07: "is the
            # explosion travelling with the bolt?"). While the bolt comes down the tip carries a small white-hot
            # spark instead (Strike Head, the kit's arc spark texture): a lit point, so the tip is never an unlit
            # line (a dim small flash was still judged 'lone line' by lightning_audit).
            k = kit_factors(kit)
            f = k.get('flash', k['bolts'])[i]
            ops.append({'node': node + '/2', 'set': {'CommonValues/ScaleEffectType': 'NotBind', 'CommonValues/GenerationTimeOffset': travel,
                                                     # from the landing to 3 frames after the bolt (as before the kit's
                                                     # flash life went from 7 to 10)
                                                     'CommonValues/Life': life - travel + 3, 'RendererCommonValues/FadeOut/Frame': 4,
                                                     'ScalingValues/Fixed/Scale': [round((flash_scale or FLASH_SIZE) * f * scale, 4)] * 3}})
            head = glow_texture(Path(kit).parent / 'generated' / 'SparkGlow.png', hot=0.6)
            # the tip glow: FAINT down the first segments, BRIGHT on the last one into the landing (user, 2026-10-07: "I
            # approve the glow following the lightning until detonation, even more so if it can be transparent until
            # the last segment"): two sparks on the same path, the bright one taking over a frame before landing
            keys = head_keys(i, scale, travel, seeds)
            colour = list(sparks or ARC_SPARK)
            for name, offset, frames, alpha in (('Strike Head', travel - 1, (travel - 1, travel), colour[3]),
                                                ('Strike Head Faint', 0, tuple(range(travel - 1)), HEAD_FAINT)):
                if not frames:
                    continue
                part = [{'curve': kk['curve'], 'points': [[f - offset, v] for f, v in kk['points'] if f in frames]} for kk in keys]
                ops.append({'node': node, 'add': {'file': str(kit), 'from': '%d/2' % i, 'name': name},
                            # ONE spark at the foot: copied from the flash it also took its spawn on the 3 foot vertices,
                            # and a falling tip showed three beads across the strip (378 f35)
                            'set': {'CommonValues/ScaleEffectType': 'NotBind', 'CommonValues/GenerationTimeOffset': offset,
                                    'GenerationLocationValues/Type': 'Point', 'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                                    'CommonValues/Life': len(frames), 'RendererCommonValues/FadeInType': 'None',
                                    'RendererCommonValues/FadeOutType': 'WithinLifetime', 'RendererCommonValues/FadeOut/Frame': 1,
                                    'RendererCommonValues/ColorTexture': str(head), 'DrawingValues/ColorAll/Fixed': colour[:3] + [alpha],
                                    # it rides the reveal's leading edge down the strand's own (noisy) path, a point a
                                    # frame (strand_point), and ends on the foot, lifted onto the drawn line
                                    'LocationValues/Type': 'LocationFCurve', 'LocationValues/LocationFCurve/FCurve/Timeline': 'Time',
                                    'ScalingValues/Fixed/Scale': [HEAD_SIZE, HEAD_SIZE * 2, HEAD_SIZE]},
                            'keys': part})
            for child in ('0', '1'):
                ops.append({'node': node + '/' + child, 'set': {'CommonValues/GenerationTimeOffset': travel}})
        if k.get('burst'):
            ops.append({'node': node + '/4', 'set': {'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': [fu[0], BURST_LIFT, fu[2]]}})
        if widened:     # one flash and the sparks at the bolt's origin (make_kit): move them onto the foot
            ops.append({'node': node + '/2', 'set': {'LocationValues/Type': 'Fixed',
                                                     'LocationValues/Fixed/Location': foot_point(i, scale, 0.0, seeds) if travel else fu}})
            ops.append({'node': node + '/3', 'set': {'LocationValues/PVA/Location/X': [round(fu[0] - 0.5, 4), round(fu[0] + 0.5, 4)],
                                                     'LocationValues/PVA/Location/Z': fu[2]}})
        for child, colour in (('2', flash), ('3', sparks)):
            if colour:
                ops.append({'node': node + '/' + child, 'set': {'DrawingValues/ColorAll/Fixed': list(colour)}})
        if life != BOLT_LIFE and not travel:    # the flash outlasts the bolt by 3 frames, as the kit's own
            ops.append({'node': node + '/2', 'set': {'CommonValues/Life': life + 3, 'RendererCommonValues/FadeOut/Frame': FLASH_FADE}})
        if flash_scale and not travel:          # with travel the flash was sized above (x the strike scale)
            k = kit_factors(kit)
            f = k.get('flash', k['bolts'])[i]
            ops.append({'node': node + '/2', 'set': {'ScalingValues/Fixed/Scale': [flash_scale * f] * 3}})
        for k, child in enumerate(('0', '1')):
            s = {}
            if fork:
                s['DrawingValues/ColorAll/Fixed'] = list(fork)
            if fork_scale:
                s['ScalingValues/Fixed/Scale'] = list(fork_scale)
                h = kit_factors(kit).get('fork_h', [256, 256])[k]
                s['RendererCommonValues/UVScroll/Size/Y'] = round(h * arc_tiles(fork_scale[1], fork_scale[0]), 2)
            if s:
                ops.append({'node': node + '/' + child, 'set': s})
    return ops


def held_strike(kit, first_root, first, life=42, fade=14, travel=3, lean=(-16, -6), turn=(-20, 20), flash=None, sparks=None,
                fork=None, fork_scale=None, restrikes=(15, 12, 2), foot_extra=6, foot_fade=16, point=1.6, parent='root',
                scale=SCALE, shape=0, restrike_from=None, restrike_burst=False, foot_lift=HELD_LIFT, flash_scale=None):
    """Edit ops for ONE strike that comes down over `travel` frames from frame `first` and then HOLDS, fading over its
    last `fade` frames: the pack ThunderFall's rhythm (user, 2026-10-07: the 9 quick strikes of the kit versions read
    "far more chaotic and fast than the vanilla"). The drawing stands still. It lands on its node's origin (pinned
    feet), so a FOOT_SEEDS kit can lean (`lean`) and turn (`turn`) at random each cast and still hit the same spot.
    The foot flash and a white-hot foot point (`point` units) outlast the bolt by `foot_extra` frames: a bolt fades
    before its light. `restrikes` (first, every, count; frames after the bolt is born): the same drawing flashes
    brighter on the standing bolt, as a child, so it always overlays it, whatever the random lean. Nodes:
    `first_root` the bolt; its children 0/1 forks, 2 flash, 3 sparks (4 burst), then Strike Head, Foot Point,
    Re-strike. `restrike_from`: the kit strand whose DRAWING the re-strikes flash (on this bolt's strand, so they still
    overlay it): a kit of two drawings shows both, one per flash. `restrike_burst`: keep the burst card on re-strikes.
    `foot_lift`: units the standing bolt is raised. Its strip ends in a straight cut across the whole strip, tilted by
    the lean: pinned at ground level, half of that cut stood under the ground beside the foot, which the editor's
    camera shows from above (user, 2026-10-07). Raised by HELD_LIFT the cut stays inside the foot glow."""
    seeds = kit_factors(kit).get('seeds')
    ops = strikes(kit, first_root, first=first, count=1, lean=lean, flash=flash, sparks=sparks, fork=fork, scale=scale,
                  parent=parent, fork_scale=fork_scale, travel=travel, pin_feet=True, life=life, flow=0, shapes=(shape,),
                  flash_scale=flash_scale)
    node = ('' if parent == 'root' else parent + '/') + str(first_root)
    zero = max(abs(c) for c in bolt_foot(shape, scale, 0, seeds)) <= 0.15
    for o in ops:
        if o.get('add') and o['add']['from'] == str(shape) and o['node'] == parent:
            o['set'].update({'RendererCommonValues/FadeOutType': 'WithinLifetime', 'RendererCommonValues/FadeOut/Frame': fade,
                             'RotationValues/PVA/Rotation/Y': list(turn) if zero and turn else 0})
            for k in o.get('keys', []):
                if k['curve'] == 'LocationValues/LocationFCurve/FCurve/Y':
                    k['points'] = [[f, round(v + foot_lift, 4)] for f, v in k['points']]
        if o['node'] == node + '/2':
            o['set'].update({'CommonValues/Life': life - travel + foot_extra, 'RendererCommonValues/FadeOutType': 'WithinLifetime',
                             'RendererCommonValues/FadeOut/Frame': foot_fade})
    head = glow_texture(Path(kit).parent / 'generated' / 'SparkGlow.png', hot=0.6)
    ops.append({'node': node, 'add': {'file': str(kit), 'from': '%d/2' % shape, 'name': 'Foot Point'},
                'set': {'CommonValues/ScaleEffectType': 'NotBind', 'CommonValues/GenerationTimeOffset': travel,
                        'GenerationLocationValues/Type': 'Point', 'CommonValues/MaxGeneration': 1, 'CommonValues/GenerationTime': 1,
                        'CommonValues/Life': life - travel + foot_extra, 'RendererCommonValues/FadeInType': 'None',
                        'RendererCommonValues/FadeOutType': 'WithinLifetime', 'RendererCommonValues/FadeOut/Frame': foot_fade,
                        'RendererCommonValues/ColorTexture': str(head), 'DrawingValues/ColorAll/Fixed': list(sparks or ARC_SPARK),
                        'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': foot_point(shape, scale, 0.4, seeds),
                        'ScalingValues/Fixed/Scale': [point] * 3}})
    if restrikes and restrikes[2]:
        r_first, r_every, r_count = restrikes
        # child of the bolt: it takes the bolt's random lean and turn, its pin and its scale (1 = the same strand)
        src = shape if restrike_from is None else restrike_from
        ops.append({'node': node, 'add': {'file': str(kit), 'from': str(src), 'name': 'Re-strike'},
                    'set': {'DrawingValues/Model/Reference': 'Bolt%d' % shape,
                            'CommonValues/MaxGeneration': r_count, 'CommonValues/GenerationTime': r_every,
                            'CommonValues/GenerationTimeOffset': r_first, 'LocationValues/Type': 'Fixed',
                            'LocationValues/Fixed/Location': [0, 0, 0], 'RotationValues/PVA/Rotation/X': 0,
                            'RotationValues/PVA/Rotation/Y': 0, 'RotationValues/PVA/Rotation/Z': 0,
                            'ScalingValues/Type': 'Fixed', 'ScalingValues/Fixed/Scale': [1, 1, 1],
                            'CommonValues/Life': 6, 'RendererCommonValues/FadeOut/Frame': 5,
                            'RendererCommonValues/UVScroll/Speed/Y': 0}})
        k = kit_factors(kit)
        # after the kit's children, the tip sparks (bright, then faint when the travel has more than one segment
        # before the last) and the Foot Point
        rs = node + '/' + str((5 if k.get('burst') else 4) + (0 if not travel else 1 + (travel > 1)) + 1)
        ops += [{'node': rs + '/0', 'set': {'IsRendered': False}}, {'node': rs + '/1', 'set': {'IsRendered': False}}]
        if k.get('burst') and not restrike_burst:
            ops.append({'node': rs + '/4', 'set': {'IsRendered': False}})
        for child, colour in (('2', flash), ('3', sparks)):
            if colour:
                ops.append({'node': rs + '/' + child, 'set': {'DrawingValues/ColorAll/Fixed': list(colour)}})
        if src != shape and 'flash' in k and k['flash'] == k['bolts']:     # foot vertices of THIS strand
            for child in ('2', '3'):
                ops.append({'node': rs + '/' + child, 'set': {'GenerationLocationValues/Model/Reference': 'Bolt%d' % shape}})
    return ops


def arcs(kit, parent, name, count, every, life, length=1.0, width=3.0, delay=0, up=False, colour=None):
    """Edit ops hanging the kit's free arc under node `parent` of an effect (best a spawner with scale 1): `count`
    arcs every `every` frames (0 = all at once), `length` x 3 units long, `width` x the strand's width, each starting
    from a spark, turned at random in the camera plane (`up`: only up or sideways, for arcs near the floor). The arc
    becomes the parent's LAST child: its path is parent/<the number of children the parent had before>."""
    s = {'CommonValues/MaxGeneration': count, 'CommonValues/GenerationTime': every, 'CommonValues/GenerationTimeOffset': delay,
         'GenerationLocationValues/Type': 'Point', 'LocationValues/Type': 'Fixed', 'LocationValues/Fixed/Location': [0, 0, 0],
         'RotationValues/PVA/Rotation/Z': [-80, 80] if up else [0, 360], 'ScalingValues/Fixed/Scale': [width, length, length],
         'RendererCommonValues/UVScroll/Size/Y': round(kit_factors(kit).get('fork_h', [256])[0] * arc_tiles(length, width), 2)}
    s.update(fading(life, life - 1))
    if colour:
        s['DrawingValues/ColorAll/Fixed'] = list(colour)
    return [{'node': parent, 'add': {'file': str(kit), 'from': '3', 'name': name}, 'set': s}]


def arc_spark(arc_node, life, length=1.0, width=3.0, colour=None):
    """Settings op for an arc's base spark (child 0 of the arc added by `arcs`): lives a frame longer than the arc and
    keeps a constant size whatever the arc's scale."""
    s = fading(life + 1, life)
    s['ScalingValues/Fixed/Scale'] = [ARC_SPARK_SIZE / width, ARC_SPARK_SIZE / length, ARC_SPARK_SIZE / length]
    if colour:
        s['DrawingValues/ColorAll/Fixed'] = list(colour)
    return {'node': arc_node + '/0', 'set': s}
