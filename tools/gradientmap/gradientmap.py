"""Gradient-map recolour for Effekseer: greyscale in, colour ramp out.

  material  write one unlit 1710 .efkmat that looks a texture's tone up on a ramp
  plan      read an effect with `Niagara2Efk dump`, sort its drawn nodes into
            convert / keep (black) / refused, write the materials and an edit spec

The material (README § Gradient-map recolour):
  tone    = luminance(texture) x texture alpha x brightest channel of the node colour
  colour  = ramp(tone)            (optionally ramp(sqrt(tone)), "lift")
  opacity = texture alpha x node alpha
so soft edges and dim or fading particles take the ramp's dark end, solid bright
cores its light end, and per-particle brightness ranges survive as ramp shimmer.
The ramp is any N x 1 (or N x M, row 0.5 is read) PNG, sampled with Clamp.

A file material replaces the built-in shader, so a node's alpha texture, UV
distortion, blend texture, alpha cutoff, falloff and soft particle settings do
not draw with it (EffekseerRenderer.CommonUtils.h, RendererShaderType::Material);
`plan` refuses such nodes. Material texture slots cannot be set per node by
`edit` yet, so `plan` writes one material per base texture, whose default path
names it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOOL = ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll'
ADVANCED = ['AlphaTextureParam', 'UVDistortionTextureParam', 'BlendTextureParams', 'AlphaCutoffParam',
            'FalloffParam', 'SoftParticleParams']
WRAP = {'repeat': 0, 'clamp': 1}


class GradientMapError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise GradientMapError(message)


def shader(lift):
    lines = ['$F4$ gmBase = $TEX_P0$uv1$TEX_S0$;',
             '$F1$ gmLight = max(vcolor.x, max(vcolor.y, vcolor.z));',
             '$F1$ gmTone = clamp(dot(gmBase.xyz, $F3$(0.299,0.587,0.114)) * gmBase.w * gmLight, 0.0, 1.0);']
    if lift:
        lines.append('gmTone = sqrt(gmTone);')
    # Keep the lookup inside the first and last texel centres of a 256-wide ramp.
    lines += ['$F4$ gmRamp = $TEX_P1$$F2$(gmTone * 0.99609375 + 0.001953125, 0.5)$TEX_S1$;',
              '$F3$ worldPositionOffset = $F3$(0.0,0.0,0.0);',
              '$F3$ baseColor = $F3$(0.0,0.0,0.0);',
              '$F3$ emissive = gmRamp.xyz;',
              '$F1$ metallic = 0.0;',
              '$F1$ roughness = 1.0;',
              '$F1$ ambientOcclusion = 1.0;',
              '$F1$ opacity = gmBase.w * vcolor.w;',
              '$F1$ opacityMask = 1.0;',
              '$F1$ refraction = 1.0;']
    return '\n'.join(lines) + '\n'


def material_bytes(name, base_path, ramp_path, lift=False, wrap='repeat'):
    """PRM_ + GENE, as tools/meshimport writes them, with shading model Unlit."""
    def ints(*xs): return struct.pack('<' + 'i' * len(xs), *xs)
    def string(s):
        data = s.encode('utf-8') + b'\0'
        return ints(len(data)) + data
    def chunk(tag, b): return tag.encode() + ints(len(b)) + b
    code = shader(lift)
    # shading Unlit, no normal, no refraction, no custom data, no required methods, two textures
    prm = ints(1, 0, 0, 0, 0, 0, 2)
    # name, uniform, default path, index, priority, param, colour type (1 value, 0 colour), wrap
    prm += string('Base') + string('gmBaseTex') + string(base_path) + ints(0, 0, 1, 1, WRAP[wrap])
    prm += string('Ramp') + string('gmRampTex') + string(ramp_path) + ints(1, 1, 1, 0, WRAP['clamp'])
    prm += ints(0, 0, 0)  # uniforms, gradients, fixed gradients
    guid = hashlib.sha256((name + code + base_path + ramp_path).encode()).digest()[:8]
    return b'EFKM' + ints(1710) + guid + chunk('PRM_', prm) + chunk('GENE', string(code))


def parse_material(data):
    """Strictly parse the subset material_bytes writes, every byte accounted for."""
    require(len(data) >= 16 and data[:4] == b'EFKM' and struct.unpack_from('<i', data, 4)[0] == 1710,
            'Invalid 1710 material header')
    offset, parts = 16, {}
    while offset < len(data):
        require(offset + 8 <= len(data), 'Truncated material chunk')
        tag, length = struct.unpack_from('<4si', data, offset)
        offset += 8
        require(length >= 0 and offset + length <= len(data) and tag not in parts, 'Invalid material chunk')
        parts[tag] = data[offset:offset + length]
        offset += length
    require(set(parts) == {b'PRM_', b'GENE'}, 'Unexpected material chunks')
    prm, pos = parts[b'PRM_'], 0

    def integer():
        nonlocal pos
        require(pos + 4 <= len(prm), 'Truncated PRM_ integer')
        value = struct.unpack_from('<i', prm, pos)[0]
        pos += 4
        return value

    def string():
        nonlocal pos
        length = integer()
        require(length > 0 and pos + length <= len(prm) and prm[pos + length - 1] == 0, 'Invalid PRM_ string')
        value = prm[pos:pos + length - 1].decode('utf-8')
        pos += length
        return value

    require([integer() for _ in range(6)] == [1, 0, 0, 0, 0, 0], 'Unexpected unlit material settings')
    require(integer() == 2, 'Expected two textures')
    textures = []
    for index in range(2):
        name, uniform, path = string(), string(), string()
        values = [integer() for _ in range(5)]
        require(values[:3] == [index, index, 1] and values[3] in (0, 1) and values[4] in (0, 1),
                'Invalid texture parameters')
        textures.append({'name': name, 'uniform': uniform, 'path': path,
                         'colorType': 'value' if values[3] else 'color', 'wrap': 'clamp' if values[4] else 'repeat'})
    require([t['name'] for t in textures] == ['Base', 'Ramp'], 'Unexpected texture names')
    require(textures[1]['wrap'] == 'clamp', 'Ramp must clamp')
    require([integer() for _ in range(3)] == [0, 0, 0] and pos == len(prm), 'Unparsed PRM_ bytes')
    gene = parts[b'GENE']
    require(len(gene) >= 5 and struct.unpack_from('<i', gene)[0] == len(gene) - 4 and gene[-1] == 0,
            'Invalid GENE shader string')
    code = gene[4:-1].decode('utf-8')
    return {'textures': textures, 'shader': code, 'lift': 'sqrt(gmTone)' in code}


def write_material(out, name, base_file, ramp_file, lift=False, wrap='repeat'):
    """Write out/Materials/<name>.efkmat naming ../Texture/<file> and read it back."""
    data = material_bytes(name, '../Texture/' + base_file, '../Texture/' + ramp_file, lift, wrap)
    path = Path(out) / 'Materials' / (name + '.efkmat')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    back = parse_material(path.read_bytes())
    require([t['path'] for t in back['textures']] == ['../Texture/' + base_file, '../Texture/' + ramp_file]
            and back['lift'] == lift and back['textures'][0]['wrap'] == wrap and back['shader'] == shader(lift),
            'Material read-back differs from what was written: ' + str(path))
    return path


def copy_texture(src, out):
    """Copy beside the materials; refuse a same-named file with different bytes."""
    src = Path(src)
    require(src.is_file(), 'Missing texture: ' + str(src))
    dst = Path(out) / 'Texture' / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.resolve() != src.resolve():
        require(dst.read_bytes() == src.read_bytes(), 'Texture name clash: %s differs from %s' % (src, dst))
    elif not dst.exists():
        shutil.copyfile(src, dst)
    return dst.name


def white_texture(out):
    from PIL import Image
    path = Path(out) / 'Texture' / 'GM-White.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        Image.new('RGBA', (4, 4), (255, 255, 255, 255)).save(path)
    return path.name


# ---- plan -----------------------------------------------------------------

RGBA = re.compile(r'rgba\((.*)\)')


def colour_max(value):
    """Brightest RGB channel of a dumped colour, taking the top of a random range."""
    m = RGBA.match(value.strip())
    if not m:
        return None
    channels = [c.strip() for c in m.group(1).split(',')]
    tops = [float(c[1:-1].split('..')[1]) if c.startswith('[') else float(c) for c in channels[:3]]
    return max(tops)


def parse_dump(text):
    nodes = []
    for block in re.split(r'^### ', text, flags=re.M)[1:]:
        head, _, body = block.partition('\n')
        path, _, name = head.strip().partition(' ')
        kv = {}
        for line in body.split('\n'):
            k, sep, v = line.partition(' = ')
            if sep:
                kv[k.strip()] = v.strip()
        nodes.append({'path': path, 'name': name, 'kv': kv})
    return nodes


def slot_values(kv, prefix, type_key, layout):
    """Active values of one colour parameter, or None when its type can't be read (Gradient, FCurve)."""
    kind = kv.get(type_key)
    keys = {'Fixed': layout['Fixed'], 'Random': layout['Random'], 'Easing': layout['Easing']}.get(kind)
    if keys is None:
        return None
    values = [colour_max(kv.get(prefix + k, '')) for k in keys]
    return None if None in values else values


def node_brightness(kv):
    """Brightest channel any active colour of the node can reach (0..255), or None if unreadable."""
    renderer = kv.get('DrawingValues/Type')
    std = {'Fixed': ['Fixed'], 'Random': ['Random'], 'Easing': ['Easing/Start', 'Easing/End']}
    # ColorAll multiplies the renderer's own vertex colours; those (ring outer/centre/inner,
    # track left..right) are separate vertices, so the node can reach their brightest one.
    factors = [slot_values(kv, 'DrawingValues/ColorAll/', 'DrawingValues/ColorAll/Type', std)]
    parts = []
    if renderer == 'Track':
        for side in ['Left', 'LeftMiddle', 'Center', 'CenterMiddle', 'Right', 'RightMiddle']:
            p = 'DrawingValues/TrailColor%s/' % side
            parts.append(slot_values(kv, p, p + 'Type', std))
    elif renderer == 'Ring':
        ring = {'Fixed': ['_Fixed'], 'Random': ['_Random'], 'Easing': ['_Easing/Start', '_Easing/End']}
        for part in ['Outer', 'Center', 'Inner']:
            p = 'DrawingValues/Ring/%sColor' % part
            parts.append(slot_values(kv, p, p, ring))
    elif renderer == 'Sprite' and kv.get('DrawingValues/Sprite/Color', 'Default') != 'Default':
        parts.append(None)
    if parts:
        factors.append(None if None in parts else [max(max(v) for v in parts)])
    if any(f is None for f in factors):
        return None
    level = 1.0
    for f in factors:
        level *= max(f) / 255
    return level * 255


def find_texture(effect, name, catalogue=None):
    effect = Path(effect)
    if catalogue:
        for e in json.load(open(catalogue, encoding='utf-8'))['effects']:
            if Path(e.get('path', '')).resolve() == effect.resolve():
                hits = {r['absolute'] for r in e.get('resources', []) if r.get('kind') == 'texture'
                        and Path(r.get('relative', '')).name == name and r.get('exists')}
                if len(hits) == 1:
                    return Path(hits.pop())
    folder = effect.parent
    hits = [folder / name] + sorted(folder.glob('*/' + name)) + sorted(folder.glob('*/*/' + name))
    hits = [h for h in hits if h.is_file()]
    require(hits, 'Texture %s of %s not found beside it (pass --catalogue)' % (name, effect))
    require(len({h.read_bytes() for h in hits}) == 1,
            'Texture %s is ambiguous beside %s: %s (pass --catalogue)' % (name, effect, [str(h) for h in hits]))
    return hits[0]


def classify(node, black):
    kv = node['kv']
    renderer = kv.get('DrawingValues/Type')
    if renderer in (None, 'None') or kv.get('IsRendered') != 'true':
        return 'skip', 'draws nothing'
    material = kv.get('RendererCommonValues/Material')
    if material != 'Default':
        return 'refused', 'material is ' + str(material)
    on = [a for a in ADVANCED if kv.get('AdvancedRendererCommonValuesValues/%s/Enabled' % a) == 'true']
    if on:
        return 'refused', 'uses ' + ', '.join(on) + ' (a file material does not draw it)'
    level = node_brightness(kv)
    if level is not None and level <= black:
        return 'keep', 'black in the original (brightest channel %g)' % round(level, 1)
    return 'convert', 'colour not read (gradient or curve)' if level is None else 'brightest channel %g' % round(level, 1)


def plan(args):
    effect = Path(args.effect).resolve()
    ramp = Path(args.ramp).resolve()
    out = Path(args.out).resolve()
    require(effect.is_file(), 'Missing effect: ' + str(effect))
    require(ramp.is_file(), 'Missing ramp: ' + str(ramp))
    r = subprocess.run(['dotnet', str(args.tool), 'dump', str(effect)], capture_output=True, text=True,
                       encoding='utf-8')
    require(r.returncode == 0, 'dump failed: ' + (r.stderr or r.stdout)[-2000:])
    nodes = parse_dump(r.stdout)
    require(nodes, 'dump printed no nodes')
    only = set(args.only.split(',')) if args.only else None
    skip = set(args.skip.split(',')) if args.skip else set()
    label = args.name or effect.stem + '-' + ramp.stem + ('-lift' if args.lift else '')
    ramp_file = copy_texture(ramp, out)
    rows, edits, materials = [], [], {}
    for node in nodes:
        verdict, why = classify(node, args.black)
        if verdict == 'convert' and (node['path'] in skip or (only is not None and node['path'] not in only)):
            verdict, why = 'keep', 'excluded by --skip/--only'
        row = {'node': node['path'], 'name': node['name'], 'verdict': verdict, 'why': why,
               'renderer': node['kv'].get('DrawingValues/Type'), 'blend': node['kv'].get('RendererCommonValues/AlphaBlend')}
        if verdict == 'convert':
            tex = node['kv'].get('RendererCommonValues/ColorTexture', '(none)')
            base = white_texture(out) if tex in ('(none)', '') else copy_texture(find_texture(effect, tex, args.catalogue), out)
            wrap = node['kv'].get('RendererCommonValues/Wrap', 'Repeat').lower()
            require(wrap in WRAP, 'Unknown wrap %s on %s' % (wrap, node['path']))
            mname = 'GM-%s%s-%s%s' % (ramp.stem, '-lift' if args.lift else '', Path(base).stem,
                                      '-clamp' if wrap == 'clamp' else '')
            if mname not in materials:
                materials[mname] = write_material(out, mname, base, ramp_file, args.lift, wrap)
            row['texture'], row['material'] = base, mname + '.efkmat'
            edits.append({'node': node['path'], 'expect': node['name'], 'set': {
                'RendererCommonValues/Material': 'File',
                'RendererCommonValues/MaterialFile/Path': str(materials[mname])}})
        if verdict != 'skip':
            rows.append(row)
    require(edits, 'No node to convert')
    spec_path = out / (label + '.edit.json')
    spec_path.write_text(json.dumps({'source': str(effect), 'edits': edits}, indent=2), encoding='utf-8')
    report = {'effect': str(effect), 'ramp': str(ramp), 'lift': args.lift, 'black': args.black,
              'spec': str(spec_path), 'materials': sorted(str(p) for p in materials.values()), 'nodes': rows,
              'counts': {v: sum(1 for r in rows if r['verdict'] == v) for v in ('convert', 'keep', 'refused')}}
    (out / (label + '.plan.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    for row in rows:
        print('%-8s %-14s %-24s %s' % (row['verdict'], row['node'], row['name'][:24], row['why']))
    print('convert %(convert)d, keep %(keep)d, refused %(refused)d' % report['counts'])
    print('spec  ' + str(spec_path))
    print('next  dotnet %s edit "%s" <out.efkefc>' % (args.tool, spec_path))
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    m = sub.add_parser('material', help='write one gradient-map material')
    m.add_argument('--base', required=True, help='greyscale texture (copied to <out>/Texture)')
    m.add_argument('--ramp', required=True, help='colour ramp PNG, left = dark end')
    m.add_argument('--out', required=True, help='folder that gets Materials/ and Texture/')
    m.add_argument('--name', help='material name (default GM-<ramp>-<base>)')
    m.add_argument('--lift', action='store_true', help='look up sqrt(tone): soft edges climb the ramp sooner')
    m.add_argument('--wrap', choices=sorted(WRAP), default='repeat', help='base texture wrap (ramp always clamps)')
    q = sub.add_parser('plan', help='sort an effect\'s nodes and write materials plus an edit spec')
    q.add_argument('effect')
    q.add_argument('--ramp', required=True)
    q.add_argument('--out', required=True, help='folder for Materials/, Texture/, <name>.edit.json, <name>.plan.json')
    q.add_argument('--name', help='spec name (default <effect>-<ramp>[-lift])')
    q.add_argument('--lift', action='store_true')
    q.add_argument('--black', type=float, default=24, help='brightest channel at or below this keeps a node (default 24)')
    q.add_argument('--only', help='comma list of node paths to convert (others kept)')
    q.add_argument('--skip', help='comma list of node paths to keep as they are')
    q.add_argument('--catalogue', help='out/catalogue.json, to resolve texture paths')
    q.add_argument('--tool', default=str(DEFAULT_TOOL), help='Niagara2Efk.dll used for dump')
    return p


def run(args):
    if args.command == 'material':
        out = Path(args.out)
        base = copy_texture(args.base, out)
        ramp = copy_texture(args.ramp, out)
        name = args.name or 'GM-%s%s-%s' % (Path(ramp).stem, '-lift' if args.lift else '', Path(base).stem)
        path = write_material(out, name, base, ramp, args.lift, args.wrap)
        print('wrote ' + str(path))
        return {'material': str(path)}
    return plan(args)


if __name__ == '__main__':
    try:
        run(parser().parse_args())
    except GradientMapError as e:
        print('error: ' + str(e), file=sys.stderr)
        sys.exit(2)
