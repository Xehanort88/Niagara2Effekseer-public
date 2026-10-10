"""Texture contact sheets and OBJ wireframes with a JSON inventory and a Markdown report.

python tools/assetinspect/asset_sheet.py <file-or-folder>... --out <new folder> [--columns 4] [--per-page 24]
       [--mesh-columns 3] [--view oblique|front|side|top] [--font <ttf>]

Textures (.png .jpg .jpeg .tga .bmp .tif .tiff .webp .dds .gif, whatever the
installed Pillow can decode): stored mode, size, frame count, per-channel
min/max/mean/standard deviation, alpha coverage and whether R, G and B are
identical in every pixel. Drawn on a checkerboard so alpha shows.

Meshes (.obj): vertex/UV/normal record counts, faces by corner count,
triangles after fan triangulation, lines and points, how many faces reference
UVs and normals, bounds, UV range, normal lengths, unreferenced vertices,
repeated-index faces, object/group/material names (names only; no .mtl is
read). Drawn as an orthographic wireframe from coordinates alone.

Folders are searched recursively; other file types are listed as skipped.
Sources are only read. Sheets are PNG pages: textures-01.png, meshes-01.png...
Exit status: 0 every file inspected, 1 at least one file could not be,
2 usage error, nothing to inspect, or the output folder was refused.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
except ImportError:  # pragma: no cover - reported as a usage error
    Image = None

TEXTURE_EXT = {'.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tif', '.tiff', '.webp', '.dds', '.gif'}
MESH_EXT = {'.obj'}
FONT_CANDIDATES = ('DejaVuSans.ttf', 'arial.ttf', 'Arial.ttf', 'LiberationSans-Regular.ttf', 'Helvetica.ttc',
                   'segoeui.ttf', 'FreeSans.ttf')
BG = (28, 28, 33)
TEX_TILE = (255, 215)
TEX_CELL = (275, 290)
MESH_CELL = (400, 470)
MESH_AREA = (370, 360)
VIEWS = {
    'oblique': lambda x, y, z: (x + 0.6 * z, y - 0.3 * z),
    'front': lambda x, y, z: (x, y),
    'side': lambda x, y, z: (-z, y),
    'top': lambda x, y, z: (x, -z),
}


class UsageError(Exception):
    pass


class ObjError(Exception):
    def __init__(self, line, message):
        super().__init__('line %d: %s' % (line, message) if line else message)


# --- fonts ------------------------------------------------------------------

def load_fonts(explicit=None, candidates=FONT_CANDIDATES):
    """Return (regular, small, description). An explicit font must load; otherwise fall back portably."""
    if explicit:
        try:
            return ImageFont.truetype(explicit, 15), ImageFont.truetype(explicit, 12), explicit
        except OSError as e:
            raise UsageError('cannot load --font %s: %s' % (explicit, e))
    for name in candidates:
        try:
            return ImageFont.truetype(name, 15), ImageFont.truetype(name, 12), name
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=15), ImageFont.load_default(size=12), 'Pillow built-in (scalable)'
    except (TypeError, OSError, AttributeError):
        f = ImageFont.load_default()
        return f, f, 'Pillow built-in (bitmap)'


def text_width(font, s):
    try:
        return font.getlength(s)
    except AttributeError:
        return font.getbbox(s)[2]


def draw_text(d, xy, s, font, fill):
    """The bitmap fallback font only encodes Latin-1; replace what it cannot draw."""
    try:
        d.text(xy, s, font=font, fill=fill)
    except UnicodeEncodeError:
        d.text(xy, s.encode('latin-1', 'replace').decode('latin-1'), font=font, fill=fill)


def wrap(font, s, width, max_lines):
    lines, cur = [], ''
    for ch in s:
        if cur and text_width(font, cur + ch) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-3] + '...'
    return lines


# --- textures ---------------------------------------------------------------

def texture_stats(im):
    """Statistics on the stored pixels. Returns (record, display RGBA image)."""
    rec = {'format': im.format, 'mode': im.mode, 'size': list(im.size), 'frames': getattr(im, 'n_frames', 1)}
    work = im
    if im.mode == 'P':
        work = im.convert('RGBA')
        rec['stats_mode'] = 'RGBA (converted from palette)'
    elif im.mode == '1':
        work = im.convert('L')
        rec['stats_mode'] = 'L (converted from 1-bit)'
    elif im.mode.startswith('I;16'):
        work = im.convert('I')
        rec['stats_mode'] = 'I (converted from %s)' % im.mode
    elif im.mode in ('PA', 'La'):
        work = im.convert('RGBA' if im.mode == 'PA' else 'LA')
        rec['stats_mode'] = '%s (converted from %s)' % (work.mode, im.mode)
    else:
        rec['stats_mode'] = im.mode
    bands = work.getbands()
    if work.mode in ('I', 'F'):
        # ImageStat bins 32-bit modes into 256 buckets; compute from the stored values instead.
        px = list(work.getdata())
        mean = sum(px) / len(px)
        sd = math.sqrt(max(0.0, sum(v * v for v in px) / len(px) - mean * mean))
        rec['channels'] = {bands[0]: {'min': min(px), 'max': max(px), 'mean': round(mean, 3), 'stddev': round(sd, 3)}}
    else:
        st = ImageStat.Stat(work)
        rec['channels'] = {b: {'min': st.extrema[i][0], 'max': st.extrema[i][1], 'mean': round(st.mean[i], 3),
                               'stddev': round(st.stddev[i], 3)} for i, b in enumerate(bands)}
    if 'A' in bands:
        a = work.getchannel('A')
        hist = a.histogram()
        total = work.width * work.height
        rec['alpha'] = {'status': 'present', 'transparent_fraction': round(hist[0] / total, 6),
                        'opaque_fraction': round(hist[255] / total, 6)}
    else:
        rec['alpha'] = {'status': 'absent'}
    if all(b in bands for b in 'RGB'):
        r, g, b = (work.getchannel(c) for c in 'RGB')
        same = ImageChops.difference(r, g).getbbox() is None and ImageChops.difference(g, b).getbbox() is None
        rec['rgb_identical'] = same
    else:
        rec['rgb_identical'] = None
    disp = work
    if disp.mode == 'I' or disp.mode == 'F':
        lo, hi = disp.getextrema()
        scale = 255.0 / (hi - lo) if hi > lo else 0
        disp = disp.point(lambda v: (v - lo) * scale).convert('L')
        rec['display_note'] = 'high bit depth shown stretched %s..%s' % (lo, hi)
    return rec, disp.convert('RGBA')


def inspect_texture(path):
    rec = {'file': path.name, 'path': str(path.resolve()), 'kind': 'texture'}
    try:
        data = path.read_bytes()
    except OSError as e:
        rec.update(status='unreadable', error=str(e))
        return rec, None
    rec['bytes'] = len(data)
    rec['sha256'] = hashlib.sha256(data).hexdigest()
    if not data:
        rec.update(status='malformed', error='empty file (0 bytes)')
        return rec, None
    try:
        with Image.open(path) as im:
            im.load()
            stats, disp = texture_stats(im)
    except Exception as e:  # Pillow raises many types for undecodable images
        rec.update(status='malformed', error='Pillow could not decode it: %s: %s' % (type(e).__name__, e))
        return rec, None
    rec.update(stats)
    rec['status'] = 'ok'
    return rec, disp


def checker(size, cell=16):
    tile = Image.new('RGBA', size)
    d = ImageDraw.Draw(tile)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            c = 65 if (x // cell + y // cell) % 2 else 95
            d.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(c, c, c, 255))
    return tile


def texture_tile(disp):
    """Fit into the tile; strips thinner than 16 px are stretched to stay visible (noted)."""
    tile = checker(TEX_TILE)
    note = None
    if disp is None:
        return tile, None
    im = disp.copy()
    im.thumbnail((TEX_TILE[0] - 10, TEX_TILE[1] - 10))
    if im.height < 16 or im.width < 16:
        w = 80 if im.width < 16 else im.width
        h = 80 if im.height < 16 else im.height
        im = disp.resize((w, h), Image.Resampling.NEAREST)
        note = 'stretched'
    tile.alpha_composite(im, ((TEX_TILE[0] - im.width) // 2, (TEX_TILE[1] - im.height) // 2))
    return tile, note


def texture_sheets(items, columns, per_page, fonts):
    font, small = fonts[0], fonts[1]
    pages = []
    for start in range(0, len(items), per_page):
        chunk = items[start:start + per_page]
        rows = math.ceil(len(chunk) / columns)
        sheet = Image.new('RGB', (columns * TEX_CELL[0], rows * TEX_CELL[1]), BG)
        d = ImageDraw.Draw(sheet)
        for i, (rec, disp) in enumerate(chunk):
            x, y = (i % columns) * TEX_CELL[0] + 10, (i // columns) * TEX_CELL[1] + 5
            tile, note = texture_tile(disp)
            sheet.paste(tile.convert('RGB'), (x, y))
            if disp is None:
                draw_text(d, (x + 10, y + 90), 'could not inspect', font, (235, 120, 110))
            ty = y + TEX_TILE[1] + 4
            for line in wrap(small, rec['file'], TEX_TILE[0], 2):
                draw_text(d, (x, ty), line, small, 'white')
                ty += 15
            if rec['status'] == 'ok':
                info = '%dx%d %s' % (rec['size'][0], rec['size'][1], rec['mode'])
                if rec['alpha']['status'] == 'present':
                    a = rec['channels']['A']
                    info += '  A %s..%s' % (a['min'], a['max'])
                if note:
                    info += '  (%s)' % note
                    rec['display_note'] = 'thin image stretched with nearest-neighbour for display'
            else:
                info = rec['status']
            draw_text(d, (x, ty), info, small, (180, 180, 190))
            rec['sheet_cell'] = [len(pages) + 1, i + 1]
        pages.append(sheet)
    return pages


# --- OBJ ----------------------------------------------------------------------

def logical_lines(text):
    """Yield (line number, content) with comments removed and backslash continuations joined."""
    buf, first = '', None
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].rstrip()
        if first is None:
            first = no
        if line.endswith('\\'):
            buf += line[:-1] + ' '
            continue
        buf += line
        if buf.strip():
            yield first, buf.strip()
        buf, first = '', None
    if buf.strip():
        yield first, buf.strip()


def floats(tok, n_min, line, what):
    if len(tok) < n_min:
        raise ObjError(line, '%s needs at least %d numbers, found %d' % (what, n_min, len(tok)))
    try:
        return [float(t) for t in tok]
    except ValueError:
        raise ObjError(line, '%s has a non-numeric value: %s' % (what, ' '.join(tok)))


def parse_obj(data):
    """Parse OBJ geometry statements. Negative indices are relative to the records read so far;
    positive indices may refer forward and are checked once the whole file is read."""
    try:
        text = data.decode('utf-8')
        notes = []
    except UnicodeDecodeError:
        text = data.decode('latin-1')
        notes = ['not valid UTF-8; read as Latin-1']
    v, vt, vn = [], [], []
    faces, lines, points = [], [], 0
    names = {'o': [], 'g': [], 'usemtl': [], 'mtllib': []}
    unsupported = {}
    forward = []   # (line, kind, index) positive indices to verify at the end

    def index(tok, count, kind, line):
        try:
            i = int(tok)
        except ValueError:
            raise ObjError(line, 'bad %s index %r' % (kind, tok))
        if i == 0:
            raise ObjError(line, '%s index 0 is invalid (OBJ indices start at 1)' % kind)
        if i < 0:
            r = count + i
            if r < 0:
                raise ObjError(line, '%s index %d reaches before the first record (%d defined so far)' % (kind, i, count))
            return r
        forward.append((line, kind, i))
        return i - 1

    def corner(tok, line, allow_normal=True):
        parts = tok.split('/')
        if len(parts) > 3 or not parts[0]:
            raise ObjError(line, 'bad vertex reference %r' % tok)
        pv = index(parts[0], len(v), 'vertex', line)
        pt = index(parts[1], len(vt), 'texture coordinate', line) if len(parts) > 1 and parts[1] else None
        pn = index(parts[2], len(vn), 'normal', line) if len(parts) > 2 and parts[2] else None
        if pn is not None and not allow_normal:
            raise ObjError(line, 'line elements cannot reference normals: %r' % tok)
        return pv, pt, pn

    for line, s in logical_lines(text):
        tok = s.split()
        kw, rest = tok[0], tok[1:]
        if kw == 'v':
            xyz = floats(rest, 3, line, 'vertex')
            v.append(tuple(xyz[:3]))
        elif kw == 'vt':
            vt.append(tuple(floats(rest, 1, line, 'texture coordinate')[:2]))
        elif kw == 'vn':
            vn.append(tuple(floats(rest, 3, line, 'normal')[:3]))
        elif kw == 'f':
            if len(rest) < 3:
                raise ObjError(line, 'face with %d vertices (at least 3 needed)' % len(rest))
            faces.append([corner(t, line) for t in rest])
        elif kw == 'l':
            if len(rest) < 2:
                raise ObjError(line, 'line element with %d vertices (at least 2 needed)' % len(rest))
            lines.append([corner(t, line, allow_normal=False) for t in rest])
        elif kw == 'p':
            if not rest:
                raise ObjError(line, 'point element with no vertices')
            for t in rest:
                index(t, len(v), 'vertex', line)
            points += len(rest)
        elif kw in names:
            names[kw].append(' '.join(rest))
        elif kw == 's':
            pass
        else:
            unsupported[kw] = unsupported.get(kw, 0) + 1
    counts = {'vertex': len(v), 'texture coordinate': len(vt), 'normal': len(vn)}
    for line, kind, i in forward:
        if i > counts[kind]:
            raise ObjError(line, '%s index %d but the file defines only %d' % (kind, i, counts[kind]))
    return {'v': v, 'vt': vt, 'vn': vn, 'faces': faces, 'lines': lines, 'points': points, 'names': names,
            'unsupported': unsupported, 'notes': notes}


def attr_coverage(faces, slot, records):
    full = sum(1 for f in faces if all(c[slot] is not None for c in f))
    none = sum(1 for f in faces if all(c[slot] is None for c in f))
    mixed = len(faces) - full - none
    if records == 0 and full == 0 and mixed == 0:
        status = 'absent'
    elif faces and full == len(faces):
        status = 'all faces'
    elif full == 0 and mixed == 0:
        status = 'records present, no face references them' if faces else 'records present, no faces'
    else:
        status = 'partial'
    return {'status': status, 'records': records, 'faces_all_corners': full, 'faces_no_corners': none,
            'faces_mixed': mixed}


def obj_stats(m):
    v, faces = m['v'], m['faces']
    sizes = {}
    for f in faces:
        k = str(len(f)) if len(f) <= 4 else '5+'
        sizes[k] = sizes.get(k, 0) + 1
    used = {c[0] for f in faces for c in f} | {c[0] for l in m['lines'] for c in l}
    rec = {
        'vertices': len(v), 'texture_coordinates': len(m['vt']), 'normals': len(m['vn']),
        'faces': len(faces), 'faces_by_corner_count': dict(sorted(sizes.items())),
        'triangles_after_fan': sum(len(f) - 2 for f in faces),
        'line_elements': len(m['lines']), 'point_vertices': m['points'],
        'uv': attr_coverage(faces, 1, len(m['vt'])), 'normal': attr_coverage(faces, 2, len(m['vn'])),
        'bounds': [[min(p[j] for p in v), max(p[j] for p in v)] for j in range(3)] if v else None,
        'uv_range': [[min(t[j] for t in m['vt']), max(t[j] for t in m['vt'])] for j in range(len(m['vt'][0]))]
        if m['vt'] and all(len(t) == len(m['vt'][0]) for t in m['vt']) else None,
        'normal_length_range': [round(min(math.sqrt(sum(c * c for c in n)) for n in m['vn']), 6),
                                round(max(math.sqrt(sum(c * c for c in n)) for n in m['vn']), 6)] if m['vn'] else None,
        'unreferenced_vertices': len(v) - len(used) if (faces or m['lines']) else len(v),
        'faces_with_repeated_vertex': sum(1 for f in faces if len({c[0] for c in f}) < len(f)),
        'objects': m['names']['o'], 'groups': sorted(set(m['names']['g'])), 'materials_used': sorted(set(m['names']['usemtl'])),
        'material_libraries': m['names']['mtllib'], 'unsupported_statements': m['unsupported'],
    }
    rec['geometry'] = 'present' if v else 'absent'
    return rec


def inspect_mesh(path):
    rec = {'file': path.name, 'path': str(path.resolve()), 'kind': 'mesh'}
    try:
        data = path.read_bytes()
    except OSError as e:
        rec.update(status='unreadable', error=str(e))
        return rec, None
    rec['bytes'] = len(data)
    rec['sha256'] = hashlib.sha256(data).hexdigest()
    try:
        m = parse_obj(data)
    except ObjError as e:
        rec.update(status='malformed', error=str(e))
        return rec, None
    rec.update(obj_stats(m))
    rec['diagnostics'] = m['notes'] + ['%d unsupported "%s" statement(s) ignored (not drawn)' % (n, k)
                                       for k, n in sorted(m['unsupported'].items())]
    if rec['material_libraries']:
        rec['material_library_files'] = {n: (path.parent / n).is_file() for n in rec['material_libraries']}
    rec['status'] = 'ok'
    return rec, m


def mesh_sheets(items, columns, per_page, fonts, view):
    font, small = fonts[0], fonts[1]
    project = VIEWS[view]
    pages = []
    for start in range(0, len(items), per_page):
        chunk = items[start:start + per_page]
        rows = math.ceil(len(chunk) / columns)
        sheet = Image.new('RGB', (columns * MESH_CELL[0], rows * MESH_CELL[1]), (25, 27, 32))
        d = ImageDraw.Draw(sheet)
        for i, (rec, m) in enumerate(chunk):
            x0, y0 = (i % columns) * MESH_CELL[0], (i // columns) * MESH_CELL[1]
            d.rectangle((x0 + 10, y0 + 10, x0 + 10 + MESH_AREA[0], y0 + 10 + MESH_AREA[1]), outline=(48, 50, 58))
            if m is None or not m['v']:
                draw_text(d, (x0 + 30, y0 + 170), 'could not inspect' if m is None else 'no vertices', font,
                          (235, 120, 110))
            else:
                pts2 = [project(*p) for p in m['v']]
                lo = [min(p[j] for p in pts2) for j in range(2)]
                hi = [max(p[j] for p in pts2) for j in range(2)]
                ext = max(hi[0] - lo[0], hi[1] - lo[1])
                scale = min((MESH_AREA[0] - 20) / max(hi[0] - lo[0], 1e-9), (MESH_AREA[1] - 20) / max(hi[1] - lo[1], 1e-9)) \
                    if ext > 0 else 1.0
                cx, cy = x0 + 10 + MESH_AREA[0] / 2, y0 + 10 + MESH_AREA[1] / 2
                pts = [(cx + (p[0] - (lo[0] + hi[0]) / 2) * scale, cy - (p[1] - (lo[1] + hi[1]) / 2) * scale) for p in pts2]
                for f in m['faces']:
                    d.line([pts[c[0]] for c in f] + [pts[f[0][0]]], fill=(100, 165, 185), width=1)
                for l in m['lines']:
                    d.line([pts[c[0]] for c in l], fill=(200, 170, 90), width=1)
                if m['points'] or (not m['faces'] and not m['lines']):
                    for px, py in pts:
                        d.point((px, py), fill=(220, 220, 225))
            ty = y0 + MESH_AREA[1] + 18
            for line in wrap(font, rec['file'], MESH_CELL[0] - 30, 1):
                draw_text(d, (x0 + 15, ty), line, font, 'white')
            ty += 20
            if rec['status'] == 'ok':
                info = ['%d vertices / %d faces / %d triangles' % (rec['vertices'], rec['faces'], rec['triangles_after_fan']),
                        'UV: %s / normals: %s' % (rec['uv']['status'], rec['normal']['status']),
                        '%s view' % view]
            else:
                info = wrap(small, '%s: %s' % (rec['status'], rec.get('error', '')), MESH_CELL[0] - 30, 3)
            for line in info:
                draw_text(d, (x0 + 15, ty), line, small, (190, 190, 195))
                ty += 15
            rec['sheet_cell'] = [len(pages) + 1, i + 1]
        pages.append(sheet)
    return pages


# --- driver -------------------------------------------------------------------

def collect(paths):
    files, skipped, notes, seen = [], [], [], set()
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise UsageError('input does not exist: %s' % raw)
        if p.is_dir():
            found = sorted((f for f in p.rglob('*') if f.is_file()), key=lambda f: str(f).lower())
            hits = [f for f in found if f.suffix.lower() in TEXTURE_EXT | MESH_EXT]
            skipped += [f for f in found if f.suffix.lower() not in TEXTURE_EXT | MESH_EXT]
            if not hits:
                notes.append('no texture or .obj files under %s' % p)
            cands = hits
        else:
            cands = [p]
        for f in cands:
            key = str(f.resolve()).lower()
            if key not in seen:
                seen.add(key)
                files.append(f)
    return files, skipped, notes


def prepare_out(out, inputs):
    out = Path(out).resolve()
    for i in inputs:
        base = Path(i).resolve()
        if base.is_dir() and (out == base or base in out.parents):
            raise UsageError('output folder %s is inside the source folder %s; choose a folder outside the sources'
                             % (out, base))
    if out.exists():
        if not out.is_dir():
            raise UsageError('output path exists and is not a folder: %s' % out)
        if any(out.iterdir()):
            raise UsageError('output folder already exists and is not empty: %s (choose a new folder)' % out)
    out.mkdir(parents=True, exist_ok=True)
    return out


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float) and (o != o or o in (float('inf'), float('-inf'))):
        return str(o)
    return o


def write_new(path, text):
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        f.write(text)


def save_new(img, path):
    with open(path, 'xb') as f:
        img.save(f, format='PNG', optimize=True)


def report_markdown(inputs, textures, meshes, unsupported, skipped, notes, sheets, font_desc):
    L = ['# Asset inspection', '',
         'Generated by `tools/assetinspect/asset_sheet.py`. Every statement below is derived from the decoded files; '
         'sources were opened read-only. Font: %s.' % font_desc, '',
         'Inputs: ' + ', '.join('`%s`' % i for i in inputs), '']
    if notes:
        L += ['Notes:', ''] + ['- ' + n for n in notes] + ['']
    if sheets:
        L += ['Sheets: ' + ', '.join('`%s`' % s for s in sheets), '']
    if textures:
        L += ['## Textures (%d)' % len(textures), '',
              '| File | Status | Size | Mode | Alpha | R=G=B | Channels min..max (mean) |', '|---|---|---|---|---|---|---|']
        for r in textures:
            if r['status'] != 'ok':
                L.append('| %s | %s: %s | | | | | |' % (r['file'], r['status'], r.get('error', '')))
                continue
            ch = ', '.join('%s %s..%s (%s)' % (b, c['min'], c['max'], c['mean']) for b, c in r['channels'].items())
            a = r['alpha']
            alpha = 'absent' if a['status'] == 'absent' else '%.1f%% transparent' % (100 * a['transparent_fraction'])
            L.append('| %s | ok | %dx%d%s | %s | %s | %s | %s |' % (
                r['file'], r['size'][0], r['size'][1], ' x%d frames' % r['frames'] if r['frames'] > 1 else '',
                r['mode'] + ('' if r['stats_mode'] == r['mode'] else ' (stats on %s)' % r['stats_mode']), alpha,
                {True: 'yes', False: 'no', None: 'n/a'}[r['rgb_identical']], ch))
        L.append('')
    if meshes:
        L += ['## Meshes (%d)' % len(meshes), '',
              '| File | Status | Vertices | Faces (by corners) | Triangles | UV | Normals | Bounds |', '|---|---|---|---|---|---|---|---|']
        for r in meshes:
            if r['status'] != 'ok':
                L.append('| %s | %s: %s | | | | | | |' % (r['file'], r['status'], r.get('error', '')))
                continue
            b = ' '.join('[%.6g, %.6g]' % tuple(x) for x in r['bounds']) if r['bounds'] else 'no vertices'
            L.append('| %s | ok | %d | %d %s | %d | %s (%d vt) | %s (%d vn) | %s |' % (
                r['file'], r['vertices'], r['faces'], json.dumps(r['faces_by_corner_count']), r['triangles_after_fan'],
                r['uv']['status'], r['texture_coordinates'], r['normal']['status'], r['normals'], b))
        L.append('')
        for r in meshes:
            if r['status'] != 'ok':
                continue
            extra = []
            if r['unreferenced_vertices']:
                extra.append('%d unreferenced vertices' % r['unreferenced_vertices'])
            if r['faces_with_repeated_vertex']:
                extra.append('%d faces repeat a vertex' % r['faces_with_repeated_vertex'])
            if r['line_elements'] or r['point_vertices']:
                extra.append('%d line elements, %d point vertices' % (r['line_elements'], r['point_vertices']))
            if r['uv_range']:
                extra.append('UV range ' + ' '.join('[%.6g, %.6g]' % tuple(x) for x in r['uv_range']))
            if r['normal_length_range']:
                extra.append('normal lengths %s..%s' % tuple(r['normal_length_range']))
            if r['materials_used']:
                extra.append('usemtl names: ' + ', '.join(r['materials_used']))
            if r.get('material_library_files'):
                extra.append('mtllib: ' + ', '.join('%s (%s)' % (k, 'file present' if v else 'file not found')
                                                    for k, v in r['material_library_files'].items()))
            extra += r['diagnostics']
            if extra:
                L.append('- %s: %s' % (r['file'], '; '.join(extra)))
        L.append('')
    if unsupported:
        L += ['## Could not inspect: unsupported format', ''] + ['- `%s`' % r['path'] for r in unsupported] + ['']
    if skipped:
        by_ext = {}
        for f in skipped:
            by_ext[f.suffix.lower() or '(none)'] = by_ext.get(f.suffix.lower() or '(none)', 0) + 1
        L += ['## Skipped in folders (not a supported type)', '',
              ', '.join('%s x%d' % kv for kv in sorted(by_ext.items())), '']
        if '.fbx' in by_ext:
            L.append('FBX files are inspected by `tools/assetinspect/fbx_inspect.py`.')
            L.append('')
    return '\n'.join(L)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', help='texture/OBJ files or folders (searched recursively)')
    ap.add_argument('--out', required=True, help='new or empty folder for assets.json, REPORT.md and sheet pages')
    ap.add_argument('--columns', type=int, default=4, help='texture tiles per row (default 4)')
    ap.add_argument('--mesh-columns', type=int, default=3, help='wireframe tiles per row (default 3)')
    ap.add_argument('--per-page', type=int, default=24, help='tiles per sheet page (default 24)')
    ap.add_argument('--view', choices=sorted(VIEWS), default='oblique', help='wireframe projection (default oblique)')
    ap.add_argument('--font', help='TTF/OTF for labels (default: first system font found, then Pillow built-in)')
    args = ap.parse_args(argv)
    try:
        if Image is None:
            raise UsageError('Pillow is required: python -m pip install pillow')
        if min(args.columns, args.mesh_columns, args.per_page) < 1:
            raise UsageError('--columns, --mesh-columns and --per-page must be at least 1')
        fonts = load_fonts(args.font)
        files, skipped, notes = collect(args.inputs)
        if not files:
            raise UsageError('nothing to inspect: no texture or .obj files in ' + ', '.join(args.inputs))
        out = prepare_out(args.out, args.inputs)
    except UsageError as e:
        print('error: ' + str(e), file=sys.stderr)
        return 2
    textures, meshes, unsupported = [], [], []
    for f in files:
        ext = f.suffix.lower()
        if ext in TEXTURE_EXT:
            textures.append(inspect_texture(f))
        elif ext in MESH_EXT:
            meshes.append(inspect_mesh(f))
        else:
            unsupported.append({'file': f.name, 'path': str(f.resolve()), 'kind': None, 'status': 'unsupported',
                                'error': 'unsupported file type %r' % f.suffix})
    sheets = []
    for name, pages in (('textures', texture_sheets(textures, args.columns, args.per_page, fonts) if textures else []),
                        ('meshes', mesh_sheets(meshes, args.mesh_columns, args.per_page, fonts, args.view)
                         if meshes else [])):
        for i, page in enumerate(pages, 1):
            fn = '%s-%02d.png' % (name, i)
            save_new(page, out / fn)
            sheets.append(fn)
    trecs, mrecs = [r for r, _ in textures], [r for r, _ in meshes]
    for r in trecs:
        r['sheet'] = 'textures-%02d.png' % r.pop('sheet_cell')[0] if 'sheet_cell' in r else None
    for r in mrecs:
        r['sheet'] = 'meshes-%02d.png' % r.pop('sheet_cell')[0] if 'sheet_cell' in r else None
    inv = {'tool': 'tools/assetinspect/asset_sheet.py', 'inputs': [str(Path(p).resolve()) for p in args.inputs],
           'notes': notes, 'font': fonts[2], 'view': args.view, 'sheets': sheets, 'textures': trecs, 'meshes': mrecs,
           'unsupported': unsupported, 'skipped': [str(f.resolve()) for f in skipped]}
    write_new(out / 'assets.json', json.dumps(clean(inv), indent=1, allow_nan=False))
    write_new(out / 'REPORT.md', report_markdown(args.inputs, trecs, mrecs, unsupported, skipped, notes, sheets,
                                                 fonts[2]))
    allrecs = trecs + mrecs + unsupported
    for r in allrecs:
        print('%-12s %s%s' % (r['status'], r['path'], ('  -- ' + r['error']) if r.get('error') else ''))
    bad = [r for r in allrecs if r['status'] != 'ok']
    print('inspected %d file(s): %d ok, %d could not be inspected, %d skipped -> %s'
          % (len(allrecs), len(allrecs) - len(bad), len(bad), len(skipped), out))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(run())
