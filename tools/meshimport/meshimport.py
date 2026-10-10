#!/usr/bin/env python3
"""GLB triangle meshes to native Effekseer v3 models and 1710 lit materials.

Python standard library + Pillow. No numpy, editor, or package installation.
Map arguments are paths or templates containing {material}, relative to --maps.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

from PIL import Image, ImageOps


class ImportErrorDetail(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ImportErrorDetail(message)


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def norm(v):
    length = math.sqrt(sum(x*x for x in v))
    return [x/length for x in v] if length > 1e-12 else [1., 0., 0.]


def bounds(points):
    require(bool(points), 'Empty geometry or fit selection')
    lo = [min(p[k] for p in points) for k in range(3)]
    hi = [max(p[k] for p in points) for k in range(3)]
    return {'min': lo, 'max': hi, 'dimensions': [hi[k]-lo[k] for k in range(3)]}


class GLB:
    def __init__(self, path):
        self.path = Path(path)
        data = self.path.read_bytes()
        require(len(data) >= 20, 'Truncated GLB')
        magic, version, length = struct.unpack_from('<4sII', data)
        require(magic == b'glTF' and version == 2 and length == len(data), 'Expected valid GLB version 2')
        chunks, offset = {}, 12
        while offset < length:
            require(offset+8 <= length, 'Truncated GLB chunk header')
            size, kind = struct.unpack_from('<II', data, offset)
            offset += 8
            require(offset+size <= length and size % 4 == 0, 'Invalid GLB chunk length')
            require(kind not in chunks, 'Duplicate GLB chunk')
            chunks[kind] = data[offset:offset+size]
            offset += size
        require(0x4e4f534a in chunks and 0x004e4942 in chunks, 'GLB needs JSON and BIN chunks')
        self.j = json.loads(chunks[0x4e4f534a])
        self.bin = chunks[0x004e4942]
        require(len(self.j.get('buffers', [])) == 1 and 'uri' not in self.j['buffers'][0], 'Only embedded single-buffer GLB supported')
        self.buffer_length = self.j['buffers'][0]['byteLength']
        require(0 <= self.buffer_length <= len(self.bin) <= self.buffer_length+3, 'Invalid embedded buffer length')
        require(not self.j.get('extensionsRequired'), 'Required glTF extensions are unsupported')

    def accessor(self, index):
        a = self.j['accessors'][index]
        widths = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}
        types = {5120: 'b', 5121: 'B', 5122: 'h', 5123: 'H', 5125: 'I', 5126: 'f'}
        require(a['type'] in widths and a['componentType'] in types, 'Unsupported accessor type')
        fmt = '<'+types[a['componentType']]*widths[a['type']]
        size = struct.calcsize(fmt)
        require(a['count'] >= 0, 'Invalid accessor count')
        def read_view(view_index, byte_offset, count, row_fmt, strided=False):
            v = self.j['bufferViews'][view_index]
            require(v.get('buffer', 0) == 0, 'External buffer unsupported')
            row_size = struct.calcsize(row_fmt)
            stride = v.get('byteStride', row_size) if strided else row_size
            begin = v.get('byteOffset', 0)+byte_offset
            require(stride >= row_size, 'Invalid accessor stride')
            end = begin+(count-1)*stride+row_size if count else begin
            require(byte_offset >= 0 and v.get('byteOffset',0) >= 0 and v['byteLength'] >= 0 and
                    end <= v.get('byteOffset',0)+v['byteLength'] <= self.buffer_length, 'Accessor overruns bufferView')
            return [list(struct.unpack_from(row_fmt,self.bin,begin+k*stride)) for k in range(count)]
        rows = read_view(a['bufferView'],a.get('byteOffset',0),a['count'],fmt,True) if 'bufferView' in a else [[0]*widths[a['type']] for _ in range(a['count'])]
        if 'sparse' in a:
            sparse = a['sparse']; idx = sparse['indices']; values = sparse['values']
            require(0 <= sparse['count'] <= a['count'] and idx['componentType'] in [5121,5123,5125], 'Invalid sparse accessor')
            ids = [row[0] for row in read_view(idx['bufferView'],idx.get('byteOffset',0),sparse['count'],'<'+types[idx['componentType']])]
            require(all(0 <= x < a['count'] for x in ids) and all(x < y for x,y in zip(ids,ids[1:])), 'Sparse indices must be increasing and in range')
            overrides = read_view(values['bufferView'],values.get('byteOffset',0),sparse['count'],fmt)
            for index,row in zip(ids,overrides): rows[index] = row
        if a.get('normalized') and a['componentType'] != 5126:
            denominator = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}.get(a['componentType'])
            require(denominator is not None, 'Unsupported normalized accessor')
            rows = [[max(-1, x/denominator) for x in row] for row in rows]
        require(all(math.isfinite(x) for row in rows for x in row), 'Non-finite accessor values')
        return rows

    def primitives(self):
        require(not self.j.get('animations'), 'Animated GLB requires --bake (phase 2 is not implemented)')
        meshes, bind = [], False
        identity = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

        def visit(index, ancestors, stack):
            nonlocal bind
            require(index not in stack, 'Cycle in scene nodes')
            n = self.j['nodes'][index]
            chain = ancestors+[n]
            if 'mesh' in n:
                # Bone transforms describe the skin; only mesh scene ancestry
                # transforms affect the mesh-space bind positions being read.
                for parent in chain:
                    require(parent.get('matrix', identity) == identity and
                            parent.get('translation', [0,0,0]) == [0,0,0] and
                            parent.get('scale', [1,1,1]) == [1,1,1] and
                            parent.get('rotation', [0,0,0,1]) in ([0,0,0,1], [0,0,0,-1]),
                            'Non-identity mesh scene-node transform: '+parent.get('name', str(index)))
                meshes.append(n['mesh'])
                bind |= 'skin' in n
            for child in n.get('children', []):
                visit(child, chain, stack+[index])

        if self.j.get('scenes'):
            for node in self.j['scenes'][self.j.get('scene', 0)].get('nodes', []):
                visit(node, [], [])
        else:
            meshes = list(range(len(self.j.get('meshes', []))))
        result = []
        for mesh in meshes:
            for p in self.j['meshes'][mesh]['primitives']:
                require(p.get('mode', 4) == 4, 'Only triangle primitives (mode 4) supported')
                require(not p.get('targets'), 'Morph targets are outside scope')
                a = p['attributes']
                require('POSITION' in a and 'TEXCOORD_0' in a, 'POSITION and TEXCOORD_0 required')
                pos, uv = self.accessor(a['POSITION']), self.accessor(a['TEXCOORD_0'])
                require(bool(pos) and all(len(x) == 3 for x in pos) and len(uv) == len(pos) and all(len(x) == 2 for x in uv), 'Invalid POSITION/UV shapes')
                ids = [x[0] for x in self.accessor(p['indices'])] if 'indices' in p else list(range(len(pos)))
                require(len(ids) % 3 == 0 and all(isinstance(x, int) and 0 <= x < len(pos) for x in ids), 'Invalid triangle indices')
                faces = [ids[k:k+3] for k in range(0, len(ids), 3)]
                normal = self.accessor(a['NORMAL']) if 'NORMAL' in a else None
                tangent = self.accessor(a['TANGENT']) if 'TANGENT' in a else None
                for attr, width in [(normal, 3), (tangent, 4)]:
                    require(attr is None or (len(attr) == len(pos) and all(len(x) == width for x in attr)), 'Invalid NORMAL/TANGENT shapes')
                material = self.j.get('materials', [])[p['material']].get('name', 'material'+str(p['material'])) if 'material' in p else 'default'
                result.append({'material': material, 'pos': pos, 'normal': normal, 'tangent': tangent, 'uv': uv, 'faces': faces})
        require(bool(result), 'No mesh triangle primitives in selected scene')
        return result, bind


def weld(p):
    """Stored-identical source attributes, first occurrence; before tangents."""
    keys, keep, remap = {}, [], []
    for k, pos in enumerate(p['pos']):
        row = pos+(p['normal'][k] if p['normal'] is not None else [])+p['uv'][k]+(p['tangent'][k] if p['tangent'] is not None else [])
        key = struct.pack('<'+'f'*len(row), *row)
        if key not in keys:
            keys[key] = len(keep)
            keep.append(k)
        remap.append(keys[key])
    out = dict(p)
    for field in ['pos', 'normal', 'tangent', 'uv']:
        if p[field] is not None:
            out[field] = [p[field][k] for k in keep]
    out['faces'] = [[remap[x] for x in f] for f in p['faces']]
    return out


def vertices(p, center, scale, tile=None):
    nrm = p['normal']
    if nrm is None:
        nrm = [[0.,0.,0.] for _ in p['pos']]
        for face in p['faces']:
            a,b,c = [p['pos'][i] for i in face]
            n = cross([b[k]-a[k] for k in range(3)], [c[k]-a[k] for k in range(3)])
            for i in face:
                nrm[i] = [nrm[i][k]+n[k] for k in range(3)]
    tan, bit = [[0.,0.,0.] for _ in nrm], [[0.,0.,0.] for _ in nrm]
    degenerate = 0
    for ids in p['faces']:
        a,b,c = [p['pos'][i] for i in ids]
        u,v,w = [p['uv'][i] for i in ids]
        e1,e2 = [b[k]-a[k] for k in range(3)], [c[k]-a[k] for k in range(3)]
        s1,s2,t1,t2 = v[0]-u[0],w[0]-u[0],v[1]-u[1],w[1]-u[1]
        det = s1*t2-s2*t1
        if abs(det) < 1e-12:
            degenerate += 1
            continue
        if p['tangent'] is None:
            tr,bi = [(t2*e1[k]-t1*e2[k])/det for k in range(3)], [(s1*e2[k]-s2*e1[k])/det for k in range(3)]
            for i in ids:
                for k in range(3):
                    tan[i][k] += tr[k]
                    bit[i][k] += bi[k]
    result = []
    for i,pos in enumerate(p['pos']):
        n = norm(nrm[i])
        if p['tangent'] is not None:
            t = norm(p['tangent'][i][:3])
            b = [x*p['tangent'][i][3] for x in cross(n, t)]
        else:
            dot = sum(n[k]*tan[i][k] for k in range(3))
            t = [tan[i][k]-n[k]*dot for k in range(3)]
            if sum(x*x for x in t) < 1e-20:
                t = cross([0,1,0] if abs(n[1]) < .9 else [1,0,0], n)
            t = norm(t)
            b = cross(n,t)
            hand = -1 if sum(b[k]*bit[i][k] for k in range(3)) < 0 else 1
            b = [x*hand for x in b]
        uv = p['uv'][i]
        if tile is not None:
            uv = [((tile%2)*1024+2+uv[0]*1020)/2048, ((tile//2)*1024+2+uv[1]*1020)/2048]
        result.append([(pos[k]-center[k])*scale for k in range(3)]+n+b+t+uv)
    return result, degenerate


def rotate(rows, degrees):
    # Round the input basis to storage precision before each requested rotation.
    for axis, angle in enumerate(degrees):
        if angle == 0:
            continue
        c,s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        for row in rows:
            row[:] = [f32(x) for x in row]
            a,b = [(1,2),(2,0),(0,1)][axis]
            for base in [0,3,6,9]:
                x,y = row[base+a],row[base+b]
                row[base+a],row[base+b] = c*x-s*y,s*x+c*y
    return rows


def spherical(rows):
    out = [r[:] for r in rows]
    for r in out:
        x,y,z = [f32(v) for v in r[:3]]
        radius = math.sqrt(x*x+y*y+z*z)
        r[12] = (math.atan2(z,x)/(2*math.pi)+1)%1
        r[13] = math.acos(max(-1,min(1,y/radius)))/math.pi if radius else .5
    return out


def model_bytes(rows, faces):
    require(all(len(r) == 14 and all(math.isfinite(x) for x in r) for r in rows), 'Invalid model vertices')
    data = (struct.pack('<3i',3,1,len(rows))+b''.join(struct.pack('<14f4B',*r,255,255,255,255) for r in rows)+
            struct.pack('<i',len(faces))+b''.join(struct.pack('<3i',*f) for f in faces)+struct.pack('<f',1))
    require(len(data) == 20+60*len(rows)+12*len(faces), 'Model byte length mismatch')
    return data


def parse_model(data):
    require(len(data) >= 20 and struct.unpack_from('<2i',data) == (3,1), 'Expected v3 single-frame model')
    count = struct.unpack_from('<i',data,8)[0]
    require(count > 0 and 16+60*count <= len(data), 'Invalid vertex count')
    rows = [list(struct.unpack_from('<14f',data,12+60*i)) for i in range(count)]
    require(all(data[68+60*i:72+60*i] == b'\xff'*4 for i in range(count)), 'Invalid vertex colors')
    faces_count = struct.unpack_from('<i',data,12+60*count)[0]
    require(faces_count >= 0 and len(data) == 20+60*count+12*faces_count, 'Unparsed/truncated model bytes')
    faces = [list(struct.unpack_from('<3i',data,16+60*count+12*i)) for i in range(faces_count)]
    require(all(0 <= x < count for f in faces for x in f) and all(math.isfinite(x) for r in rows for x in r), 'Invalid model values')
    require(struct.unpack_from('<f',data,len(data)-4)[0] == 1, 'Invalid trailing scale')
    return rows,faces


def save_model(path, rows, faces):
    data = model_bytes(rows,faces)
    path.write_bytes(data)
    stored,indices = parse_model(path.read_bytes())
    require(indices == faces and stored == [[f32(x) for x in r] for r in rows], 'Model read-back mismatch')
    return {'bytes': len(data), 'vertices': len(rows), 'triangles': len(faces), 'strict_readback': True, 'sha256': hashlib.sha256(data).hexdigest()}


def load_image(path, size):
    with Image.open(path) as source:
        if source.mode.startswith('I;16') or source.mode == 'I':
            pixels = source.get_flattened_data() if hasattr(source,'get_flattened_data') else source.getdata()
            values = [max(0,min(255,int(x/257))) for x in pixels]
            im = Image.new('L',source.size)
            im.putdata(values)
            im = im.convert('RGB')
        else:
            # Resize RGBA before dropping alpha, matching the original scripts.
            im = source.convert('RGBA')
        if im.size != (size,size):
            im = im.resize((size,size),Image.Resampling.LANCZOS)
        return im.convert('RGB')


def save_image(path, image, source=None):
    image.save(path,optimize=True,compress_level=9)
    reused = False
    if source is not None and Path(source).stat().st_size < path.stat().st_size:
        with Image.open(source) as original:
            if original.mode == 'RGB' and original.size == image.size and original.tobytes() == image.tobytes():
                path.write_bytes(Path(source).read_bytes())
                reused = True
    with Image.open(path) as stored:
        require(stored.mode == 'RGB' and stored.size == image.size and stored.tobytes() == image.tobytes(), 'Texture read-back mismatch')
    return {'bytes':path.stat().st_size,'size':image.size,'mode':'RGB','strict_readback':True,'kept_source_encoding':reused}


def maps(args, materials, out):
    roles = ['color','normal','emission','roughness','metallic','ao']
    active = {role:getattr(args,role) for role in roles if getattr(args,role)}
    resolved = {role:[Path(args.maps)/pattern.format(material=m) for m in materials] for role,pattern in active.items()}
    for paths in resolved.values():
        for p in paths:
            require(p.is_file(),'Missing explicitly requested map: '+str(p))
    atlas = len(materials) > 1
    require(not atlas or args.size >= 1024 and args.size % 1024 == 0, '2x2 atlas size must be a positive multiple of 1024 (integer exact gutters)')
    content = args.size*1020//2048 if atlas else args.size
    keys = [r.title() for r in ['color','normal','emission'] if r in active]
    if any(r in active for r in ['roughness','metallic','ao']):
        keys.append('Surface')
    report = {}
    for key in keys:
        image = Image.new('RGB',(args.size,args.size))
        for tile,material in enumerate(materials):
            if key == 'Surface':
                # Missing roles are declared constants, never invented files.
                channels = [load_image(resolved[r][tile],content).getchannel('R') if r in active else Image.new('L',(content,content),v)
                            for r,v in [('roughness',255),('metallic',0),('ao',255)]]
                im = Image.merge('RGB',tuple(channels))
            else:
                im = load_image(resolved[key.lower()][tile],content)
                if key == 'Normal' and args.flip_normal_green:
                    r,g,b = im.split()
                    im = Image.merge('RGB',(r,ImageOps.invert(g),b))
            if atlas:
                edge,gutter = args.size//2,args.size//1024
                pad = Image.new('RGB',(edge,edge))
                pad.paste(im,(gutter,gutter))
                pad.paste(im.crop((0,0,1,content)).resize((gutter,content)),(0,gutter))
                pad.paste(im.crop((content-1,0,content,content)).resize((gutter,content)),(edge-gutter,gutter))
                pad.paste(pad.crop((0,gutter,edge,gutter+1)).resize((edge,gutter)),(0,0))
                pad.paste(pad.crop((0,edge-gutter-1,edge,edge-gutter)).resize((edge,gutter)),(0,edge-gutter))
                image.paste(pad,((tile%2)*edge,(tile//2)*edge))
            else:
                image = im
        source = resolved.get(key.lower(),[None])[0] if not atlas else None
        path = out/(args.name+'-'+key+'.png')
        report[key] = save_image(path,image,source)
        source_roles = [r for r in ['roughness','metallic','ao'] if r in resolved] if key == 'Surface' else [key.lower()]
        report[key]['sources'] = [str(p.resolve()) for r in source_roles for p in resolved[r]]
    if args.dissolve_mask:
        path = Path(args.dissolve_mask)
        report['DissolveMask'] = save_image(out/(args.name+'-DissolveMask.png'),load_image(path,args.size),path)
        report['DissolveMask']['sources'] = [str(path.resolve())]
    return report, sorted(active)


def material_bytes(name, keys, roles):
    def ints(*xs): return struct.pack('<'+'i'*len(xs),*xs)
    def string(s):
        data = s.encode('utf-8')+b'\0'
        return ints(len(data))+data
    def chunk(tag,b): return tag.encode()+ints(len(b))+b
    code = []
    for index,key in enumerate(keys):
        code.append(f'$F4$ mesh{key} = $TEX_P{index}$uv1$TEX_S{index}$;')
    if 'Normal' in keys:
        code += ['$F3$ meshNormalDir = meshNormal.xyz * 2.0 - $F3$(1.0,1.0,1.0);',
                 'pixelNormalDir = normalize(meshNormalDir.x * worldTangent + meshNormalDir.y * worldBinormal + meshNormalDir.z * worldNormal);']
    code += ['$F3$ worldPositionOffset = $F3$(0.0,0.0,0.0);',
             '$F3$ baseColor = '+('meshColor.xyz * vcolor.xyz;' if 'Color' in keys else 'vcolor.xyz;'),
             '$F3$ emissive = '+('meshEmission.xyz * vcolor.xyz;' if 'Emission' in keys else '$F3$(0.0,0.0,0.0);'),
             '$F1$ metallic = '+('meshSurface.y;' if 'metallic' in roles else '0.0;'),
             '$F1$ roughness = '+('max(meshSurface.x,0.04);' if 'roughness' in roles else '1.0;'),
             '$F1$ ambientOcclusion = '+('meshSurface.z;' if 'ao' in roles else '1.0;'),
             '$F1$ opacity = vcolor.w;',
             '$F1$ opacityMask = '+('meshDissolveMask.x - (1.0 - vcolor.w);' if 'DissolveMask' in keys else '1.0;'),
             '$F1$ refraction = 1.0;']
    shader = '\n'.join(code)+'\n'
    prm = ints(0,1,0,0,0,0,len(keys))
    for index,key in enumerate(keys):
        prm += string(key)+string('mesh'+key+'Tex')+string('../Texture/'+name+'-'+key+'.png')+ints(index,index,1,0 if key in ['Color','Emission'] else 1,1)
    prm += ints(0,0,0)
    guid = hashlib.sha256((name+shader).encode()).digest()[:8]
    return b'EFKM'+ints(1710)+guid+chunk('PRM_',prm)+chunk('GENE',string(shader)),shader


def parse_material(data):
    """Strictly parse the generated native subset, including every byte."""
    require(len(data) >= 16 and data[:4] == b'EFKM' and struct.unpack_from('<i',data,4)[0] == 1710, 'Invalid 1710 material header')
    offset,parts = 16,{}
    while offset < len(data):
        require(offset+8 <= len(data), 'Truncated material chunk')
        tag,length = struct.unpack_from('<4si',data,offset)
        offset += 8
        require(length >= 0 and offset+length <= len(data) and tag not in parts, 'Invalid material chunk')
        parts[tag] = data[offset:offset+length]
        offset += length
    require(set(parts) == {b'PRM_',b'GENE'}, 'Unexpected material chunks')
    prm,pos = parts[b'PRM_'],0
    def integer():
        nonlocal pos
        require(pos+4 <= len(prm), 'Truncated PRM_ integer')
        value = struct.unpack_from('<i',prm,pos)[0]; pos += 4
        return value
    def string():
        nonlocal pos
        length = integer()
        require(length > 0 and pos+length <= len(prm) and prm[pos+length-1] == 0, 'Invalid PRM_ string')
        value = prm[pos:pos+length-1].decode('utf-8'); pos += length
        return value
    require([integer() for _ in range(6)] == [0,1,0,0,0,0], 'Unexpected lit material settings')
    count = integer()
    require(0 <= count <= 5, 'Invalid texture count')
    textures = []
    for index in range(count):
        name,uniform,path = string(),string(),string()
        values = [integer() for _ in range(5)]
        require(values[:3] == [index,index,1] and values[3] in [0,1] and values[4] == 1, 'Invalid texture parameters')
        textures.append({'name':name,'uniform':uniform,'path':path})
    require([integer() for _ in range(3)] == [0,0,0] and pos == len(prm), 'Unparsed PRM_ bytes')
    gene = parts[b'GENE']
    require(len(gene) >= 5 and struct.unpack_from('<i',gene)[0] == len(gene)-4 and gene[-1] == 0, 'Invalid GENE shader string')
    return {'textures':textures,'shader':gene[4:-1].decode('utf-8'),'strict_readback':True}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--name')
    p.add_argument('--lod',type=Path)
    p.add_argument('--diameter',type=float,default=2)
    p.add_argument('--scale',type=float,help='multiply source units by this instead of fitting --diameter '
                   '(e.g. 2.5 for metres to effect units at 0.025 units per cm)')
    p.add_argument('--keep-origin',action='store_true',
                   help='keep the source pivot instead of centring on the bounds (a column that grows from its base)')
    p.add_argument('--fit-sections',default='')
    p.add_argument('--material-order',help='Comma-separated atlas tile order; defaults to first primitive occurrence')
    for axis in 'xyz': p.add_argument('--rotate-'+axis,type=float,default=0)
    p.add_argument('--overlay-uv',choices=['spherical','source'],
                   help='also write <name>-overlay.efkmodel: same vertices with spherical UVs (small meteor) '
                        'or the source UVs before atlasing (big meteor)')
    p.add_argument('--no-weld',action='store_true',
                   help='keep duplicate vertices; only for reproducing models built before welding '
                        '(generated tangents are then per triangle, which can show facets)')
    p.add_argument('--maps',type=Path,default=Path('.'))
    for role in ['color','normal','emission','roughness','metallic','ao']: p.add_argument('--'+role)
    p.add_argument('--flip-normal-green',action='store_true')
    p.add_argument('--size',type=int,default=2048)
    p.add_argument('--dissolve-mask',type=Path)
    p.add_argument('--bake',type=int,help='Phase 2; unavailable until static verification passes')
    return p


def run(args):
    require(args.bake is None, '--bake is unavailable: animated-model phase 2 has not passed its prerequisite gate')
    args.name = args.name or args.input.stem
    require(args.name not in ['.','..'] and all(c not in args.name for c in '/\\:') and bool(args.name), 'Invalid output name')
    require(math.isfinite(args.diameter) and args.diameter > 0 and args.size > 0, 'Diameter and size must be positive')
    angles = [args.rotate_x,args.rotate_y,args.rotate_z]
    require(all(math.isfinite(x) for x in angles), 'Non-finite rotation')
    main,main_bind = GLB(args.input).primitives()
    source,bind = GLB(args.lod).primitives() if args.lod else (main,main_bind)
    materials = list(dict.fromkeys(p['material'] for p in main))
    if args.material_order:
        ordered = args.material_order.split(',')
        require(len(ordered) == len(materials) and set(ordered) == set(materials), 'Material order must list every material once')
        materials = ordered
    require(len(materials) <= 4, '2x2 atlas supports at most four material sections')
    require(set(p['material'] for p in source) <= set(materials), 'LOD materials differ from main GLB')
    fit = args.fit_sections.split(',') if args.fit_sections else materials
    require(set(fit) <= set(materials), 'Unknown fit section')
    fit_bounds = bounds([x for p in main if p['material'] in fit for x in p['pos']])
    center = [0.0,0.0,0.0] if args.keep_origin else [(fit_bounds['min'][k]+fit_bounds['max'][k])/2 for k in range(3)]
    require(max(fit_bounds['dimensions']) > 0, 'Zero-sized fit geometry')
    require(args.scale is None or (math.isfinite(args.scale) and args.scale > 0), 'Scale must be positive')
    scale = args.scale if args.scale is not None else args.diameter/max(fit_bounds['dimensions'])
    rows,faces,sections,core_indices,source_uv = [],[],[],[],[]
    for p in source:
        merged = p if args.no_weld else weld(p)
        source_uv.extend(merged['uv'])
        tile = materials.index(p['material']) if len(materials) > 1 else None
        if tile is not None:
            require(all(0 <= x <= 1 for uv in p['uv'] for x in uv), 'Atlas UV outside 0..1')
        vs,degenerate = vertices(merged,center,scale,tile)
        if p['material'] in fit:
            core_indices.extend(range(len(rows),len(rows)+len(vs)))
        faces.extend([[x+len(rows) for x in f] for f in merged['faces']])
        rows.extend(vs)
        sections.append({'material':p['material'],'source_vertices':len(p['pos']),'vertices':len(vs),'triangles':len(p['faces']),
                         'degenerate_uv_triangles':degenerate,'tangents':'provided' if p['tangent'] is not None else ('generated, no weld' if args.no_weld else 'generated after weld'),
                         'normals':'provided' if p['normal'] is not None else 'generated'})
    args.out.mkdir(parents=True,exist_ok=True)
    for folder in ['Model','Texture','Materials']: (args.out/folder).mkdir(exist_ok=True)
    rotate(rows,angles)
    models = {args.name:save_model(args.out/'Model'/(args.name+'.efkmodel'),rows,faces)}
    if args.overlay_uv:
        overlay = spherical(rows) if args.overlay_uv == 'spherical' else [r[:12]+list(source_uv[i]) for i,r in enumerate(rows)]
        models[args.name+'-overlay'] = save_model(args.out/'Model'/(args.name+'-overlay.efkmodel'),overlay,faces)
    textures,roles = maps(args,materials,args.out/'Texture')
    data,shader = material_bytes(args.name,list(textures),roles)
    matpath = args.out/'Materials'/(args.name+'.efkmat')
    matpath.write_bytes(data)
    require(matpath.read_bytes() == data, 'Material read-back mismatch')
    parsed_material = parse_material(matpath.read_bytes())
    require(parsed_material['shader'] == shader, 'Material shader read-back mismatch')
    report = {'input':str(args.input.resolve()),'lod':str(args.lod.resolve()) if args.lod else None,
              'weld':'off (--no-weld)' if args.no_weld else 'source float32-identical attributes, first occurrence order',
              'overlay_uv':args.overlay_uv,
              'static_bind_pose':bind,'main_static_bind_pose':main_bind,'skin_handling':'mesh-space bind positions; joint transforms not applied',
              'scene_transform_check':'mesh nodes and their ancestors; skeleton joint transforms are bind-pose metadata',
              'fit_sections':fit,'material_order':materials,'center':center,'scale':scale,'diameter':args.diameter if args.scale is None else None,'keep_origin':args.keep_origin,'rotate_degrees_xyz':angles,
              'main_whole_bounds':bounds([x for p in main for x in p['pos']]),'main_core_bounds':fit_bounds,
              'geometry_whole_bounds':bounds([x for p in source for x in p['pos']]),
              'geometry_core_bounds':bounds([x for p in source if p['material'] in fit for x in p['pos']]),
              'saved_bounds':bounds([r[:3] for r in parse_model((args.out/'Model'/(args.name+'.efkmodel')).read_bytes())[0]]),
              'saved_core_bounds':bounds([[f32(x) for x in rows[i][:3]] for i in core_indices]),
              'sections':sections,'models':models,'textures':textures,'map_roles':roles,
              'surface_missing_roles':[r for r in ['roughness','metallic','ao'] if r not in roles],
              'normal_green_flipped':args.flip_normal_green,'material':{'version':1710,'bytes':len(data),'editable_graph':False,**parsed_material},
              'unsupported':['height/displacement','skeletal runtime','morph targets','FBX/OBJ','animated bake (phase 2 gated)']}
    (args.out/(args.name+'.import.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    try:
        result = run(parser().parse_args())
        print(json.dumps({'models':result['models'],'textures':result['textures'],'static_bind_pose':result['static_bind_pose'],'material':result['material']},indent=2))
    except (ValueError, OSError, KeyError, IndexError, TypeError, OverflowError, struct.error) as exc:
        print('meshimport: '+str(exc),file=sys.stderr)
        sys.exit(1)
