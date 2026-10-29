"""unity_rdc: extract and analyse a Unity effect (Unity game A, Unity game B) from its RenderDoc
captures: the capture side of tools/unity, beside the bundle side (unity_dump.py, shuriken_summary.py).

  python tools/unity/unity_rdc.py "<capture folder>" [--work out/edits/<game>-<folder>-<yyyymmdd>] [--overwrite]
         [--quick] [--skip-video] [--skip-digest] [--renderdoc DIR] [--ffmpeg EXE] [--marker-re RE] [--top N]

Three steps; the first two skip what is already there, so a rerun only redoes the analysis:
1. footage: every video in the folder -> <folder>/frames-1fps-<video>/<second>.png, and a contact sheet labelled
   with second and frame in <work>/footage/ (AGENTS.md: watch footage as a sequence before judging any still).
2. extract: tools/rdcdigest digest of every .rdc in the folder into <folder>/rdcdigest/ (README § Digesting RenderDoc
   captures; the exact digest by default, --quick for the smaller default one). Captures are only read.
3. analyse: <work>/REPORT.md, layers.json and view.html, with each layer's textures, contribution, particle CSV and
   shader disassembly. A layer is the kept scene draws sharing shaders, textures and blend; layers are ranked by the
   light they add to the scene colour target, so the effect's layers come first even in a busy battle frame. Layers
   drawing into other targets (masks, distortion, glow buffers) are listed apart with the passes that read them, and
   UI draws (UI textures: --ui-re) are left out of the ranking. A billboard, instance or mesh copy is one particle.

<game> is wotr or rt from the capture folder's path (Unity game A, Unity game B), else unity. Bundled (tools/unity/pack.py:
the Unity tools, run_rdc.bat and the rdcdigest modules in rdc_tools/, unzipped anywhere), it runs without the repo and
the default work folder is <the capture folder's parent>/_analysis/<game>-<folder>-<yyyymmdd>.
"""
import argparse
import csv
import datetime
import glob
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUNDLED = (HERE / 'rdc_tools' / 'rdcdigest.py').exists()      # unzipped from pack.py, away from the repo
RDC = HERE / 'rdc_tools' if BUNDLED else REPO / 'tools' / 'rdcdigest'
sys.path.insert(0, str(RDC))
import ddsio  # noqa: E402
import digestlib  # noqa: E402
import rdcdigest  # noqa: E402

VIDEO_EXT = ('.mp4', '.mkv', '.mov', '.avi', '.webm')
# the exact digest (README § Digesting RenderDoc captures); --compress is NTFS compact, Windows only
EXACT = ['--all-resources', '--contrib-dtype', 'float', '--dynamic-cap', '256', '--buffer-cap', '256', '--dedup']
# Unity globals every shader's cbuffers carry; what is left is the material's own properties
BUILTIN = re.compile(r'^(unity_|glstate_|_Time$|_SinTime$|_CosTime$|_DeltaTime$|_WorldSpaceCameraPos$|'
                     r'_ProjectionParams$|_ScreenParams$|_ZBufferParams$|_LightColor0$|_WorldSpaceLightPos0$)|'
                     r'(?i:matrix|objectto|worldto)')
# textures only Unity UI reads (uGUI sprite atlases, TextMeshPro font and sprite atlases, portraits, the white default
# sprite): a scene draw reading one, or sharing its shader pair with one that does, is UI (WotR draws its HUD into the
# scene colour target with a depth target bound, so blending and depth alone let 171 UI draws in)
UI_TEX = r'^sactx-|Sprite[ _]?Atlas|SDF Atlas|Font Texture|_Portrait$|^UnityWhite$|^UI_'
CHANNELS = 'RGBA'


def fmt(v):
    """Numbers at 5 significant digits, lists kept as lists, for the report and layers.json."""
    if isinstance(v, (list, tuple)):
        return [fmt(x) for x in v]
    if isinstance(v, float):
        return float('%.5g' % v)
    return v


def pct(a, qs=(10, 50, 90)):
    return [float('%.4g' % np.percentile(a, q)) for q in qs] if len(a) else []


def spread(v):
    """Per-component min, p10, p50, p90 and max of an (n, components) array."""
    return {k: fmt(np.percentile(v, q, axis=0).tolist()) for k, q in
            (('min', 0), ('p10', 10), ('p50', 50), ('p90', 90), ('max', 100))}


def game_of(path):
    """wotr or rt from an install path (the captures live under the game's folder), else unity."""
    low = str(path).lower()
    return 'rt' if 'unity game b' in low else 'wotr' if 'unity game a' in low else 'unity'


# ---- 1. footage -------------------------------------------------------------------------------------------------
def find_ffmpeg(given):
    if given:
        return given
    bundled = sorted(glob.glob(str(REPO / 'out/edits/*/scratch/pylib/imageio_ffmpeg/binaries/ffmpeg*')))
    if shutil.which('ffmpeg') or bundled:
        return shutil.which('ffmpeg') or bundled[0]
    try:                                     # pip install imageio-ffmpeg brings its own ffmpeg.exe
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:                        # noqa: BLE001  (not installed, or its binary missing)
        return None


def contact_sheet(pngs, fps, out, cols=6, width=240):
    from PIL import Image, ImageDraw
    if not pngs:
        return
    first = Image.open(pngs[0])
    h = round(first.height * width / first.width)
    sheet = Image.new('RGB', (cols * width, -(-len(pngs) // cols) * (h + 18)), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for i, p in enumerate(pngs):
        x, y = i % cols * width, i // cols * (h + 18)
        sheet.paste(Image.open(p).convert('RGB').resize((width, h)), (x, y + 18))
        sec = int(p.stem)
        draw.text((x + 4, y + 3), '%d s' % sec + (' / f%d' % round(sec * fps) if fps else ''), fill=(230, 230, 230))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def footage(folder, ffmpeg, work):
    out = []
    for v in sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT):
        frames = folder / ('frames-1fps-' + v.stem)
        info = {'video': v.name, 'frames': str(frames)}
        out.append(info)
        if not ffmpeg:
            info['error'] = 'no ffmpeg found (--ffmpeg <exe>)'
            continue
        probe = subprocess.run([ffmpeg, '-hide_banner', '-i', str(v)], capture_output=True, text=True).stderr
        m = re.search(r'([\d.]+) fps', probe)
        d = re.search(r'Duration: (\d+):(\d+):([\d.]+)', probe)
        info['fps'] = float(m[1]) if m else None
        info['seconds'] = int(d[1]) * 3600 + int(d[2]) * 60 + float(d[3]) if d else None
        if not any(frames.glob('*.png')):
            frames.mkdir(exist_ok=True)
            r = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(v), '-vf', 'fps=1',
                                '-start_number', '0', str(frames / '%04d.png')])
            if r.returncode:
                info['error'] = 'ffmpeg exited %d' % r.returncode
                continue
        pngs = sorted(frames.glob('*.png'))
        info['count'] = len(pngs)
        sheet = work / 'footage' / ('sheet-%s.png' % v.stem)
        contact_sheet(pngs, info['fps'], sheet)
        info['sheet'] = sheet.relative_to(work).as_posix()
        print('footage: %s  %s fps, %s s, %d frames at 1 fps -> %s' % (v.name, info['fps'], info['seconds'],
                                                                      len(pngs), frames))
    return out


# ---- 2. extract -------------------------------------------------------------------------------------------------
def extract(folder, store, args):
    if not list(folder.glob('*.rdc')):
        sys.exit('no .rdc in %s' % folder)
    if not (Path(args.renderdoc) / 'qrenderdoc.exe').exists():
        sys.exit('no qrenderdoc.exe in %s: pass --renderdoc <RenderDoc 1.46 folder>' % args.renderdoc)
    cmd = [sys.executable, str(RDC / 'rdcdigest.py'), '--store', str(store), 'digest', str(folder),
           '--renderdoc', args.renderdoc]
    if not args.quick:
        cmd += EXACT + (['--compress'] if os.name == 'nt' else [])
    if args.marker_re:
        cmd += ['--marker-re', args.marker_re]
    print('extract: ' + subprocess.list2cmdline(cmd))
    if subprocess.run(cmd).returncode:
        sys.exit('rdcdigest digest failed (see its log.txt under %s/captures/<id>/)' % store)
    return cmd


# ---- 3. analyse ---------------------------------------------------------------------------------------------------
def effect_draws(m):
    """Kept scene draws: blended, marked or listed draws with a depth target, i.e. not UI or post."""
    return [r for r in m['draws'] if r['kind'] == 'draw' and r.get('keep') and r.get('depth_target')]


def srvs(r):
    return [s for st in ('vs', 'ps') for s in r.get('shaders', {}).get(st, {}).get('srvs', [])
            if s.get('kind') == 'texture']


def layer_key(r):
    sh, bs = r.get('shaders', {}), (r.get('blend_summary') or [{}])[0]
    return (sh.get('vs', {}).get('checksum'), sh.get('ps', {}).get('checksum'),
            tuple(s.get('hash') or 'dynamic:' + s.get('name', '') for s in srvs(r)), bs.get('kind'), bs.get('color'))


def shader_pair(r):
    sh = r.get('shaders', {})
    return sh.get('vs', {}).get('checksum'), sh.get('ps', {}).get('checksum')


def ui_draws(rs, pattern):
    """{eid: why} for the UI draws among the scene draws: they read a UI texture (UI_TEX) or share the shader pair of
    a draw that does."""
    rx = re.compile(pattern, re.I)
    named = {}
    for r in rs:
        hit = next((s.get('resource') for s in srvs(r) if rx.search(s.get('resource') or '')), None)
        if hit:
            named[r['eid']] = hit
    pairs = {shader_pair(r): named[r['eid']] for r in rs if r['eid'] in named}
    return {r['eid']: ('reads %s' % named[r['eid']] if r['eid'] in named else
                       'shares its shaders with a draw reading %s' % pairs[shader_pair(r)])
            for r in rs if r['eid'] in named or shader_pair(r) in pairs}


def target_id(r, c):
    """The resource a contribution entry was measured on (older digests name only the slot)."""
    return c.get('id') or next((t['id'] for t in r.get('targets', []) if t.get('slot', 0) == c.get('slot', 0)), None)


def main_target(rs, ui):
    """The scene colour target: the one most non-UI scene draws write at slot 0."""
    n = {}
    for r in rs:
        if r['eid'] not in ui and r.get('targets'):
            n[r['targets'][0]['id']] = n.get(r['targets'][0]['id'], 0) + 1
    return max(n, key=n.get) if n else None


def light(r, target=None):
    """Sum of |change| the draw adds to the RGB of target (every target when None): what ranks layers. A draw into a
    side target (a refraction or blur mask, a glow buffer) adds no light to the frame by itself, so ranking by every
    target put WotR's mask writes (B = 10, the mask's "on" flag) above the effect's real light."""
    return sum(abs(v) for c in r.get('contrib', []) if target is None or target_id(r, c) == target
               for k, v in (c.get('sum') or {}).items() if k in 'RGB')


def flatten(vars_, prefix=''):
    for k, v in vars_.items():
        if isinstance(v, dict):
            yield from flatten(v, prefix + k + '.')
        elif k != '_error':
            yield prefix + k, v


def material(m, r):
    """Named cbuffer values of the draw's shaders, Unity globals and matrices left out; None without reflection."""
    out, named = {}, False
    for st in ('vs', 'ps'):
        for cb in r.get('shaders', {}).get(st, {}).get('cbuffers', []):
            vars_ = m['tables']['cbvars'].get(cb.get('vars') or '')
            if not vars_:
                continue
            named = True
            for k, v in flatten(vars_):
                if not BUILTIN.search(k.split('.')[-1]):
                    out.setdefault(k, fmt(v))
    return out if named else None


def flipped(m, r):
    """(upside down?, why) for a draw's targets. Unity on D3D flips its projection while it draws into a render
    texture, so the image lies bottom-up in memory, and says so in _ProjectionParams.x (-1). Without the name a draw
    into a render texture (every scene draw: only the final blit reaches the screen) is taken as flipped."""
    for st in ('vs', 'ps'):
        for cb in r.get('shaders', {}).get(st, {}).get('cbuffers', []):
            for k, v in flatten(m['tables']['cbvars'].get(cb.get('vars') or '') or {}):
                if k.split('.')[-1] == '_ProjectionParams' and isinstance(v, list) and v:
                    return v[0] < 0, '_ProjectionParams.x = %g' % v[0]
    return True, 'assumed: no _ProjectionParams'


def time_row(raw):
    """t of the first constant-buffer row shaped like Unity's _Time (t/20, t, 2t, 3t), else None. WotR's and Rogue
    Trader's shaders keep no names, but the row keeps its shape. Both games put a second clock of the same shape a few
    rows after it (WotR 4 rows, ~21 s ahead; RT 5 rows, ~27 s ahead): the first is _Time, as WotR's a Unity study effect
    showed (its 0.502 s between captures 3465 and 3491 fits the sigil's 10 deg/s turn; the second clock's 1.396 s
    does not), so later rows never count."""
    n = len(raw) // 16
    a = np.frombuffer(raw[:n * 16], '<f4').reshape(-1, 4).astype(float)
    for r in a:
        t = r[1]
        if not np.isfinite(r).all() or t <= 0.01:
            continue
        tol = 1e-5 * t + 1e-6
        if abs(r[0] * 20 - t) < 20 * tol and abs(r[2] - 2 * t) < 2 * tol and abs(r[3] - 3 * t) < 3 * tol:
            return float(t)
    return None


def unity_clock(m, rs):
    """The game clock the effect drew at: Unity's _Time.y in the constants of the kept draws rs, by name when the
    shaders kept reflection, else by the row's shape. The value most draws read wins and the others are listed (WotR's
    HUD draws read a stale 48.95 s; they are UI and not passed in). Capture names count rendered frames, not 60 fps
    frames, so frame gaps between captures say nothing about time without this (WotR ran ~51 fps)."""
    seen = {}
    for r in rs:
        found = None
        for st in ('vs', 'ps'):
            for cb in r.get('shaders', {}).get(st, {}).get('cbuffers', []):
                for k, v in flatten(m['tables']['cbvars'].get(cb.get('vars') or '') or {}):
                    if k.split('.')[-1] == '_Time' and isinstance(v, list) and len(v) > 1:
                        found = found or (float(v[1]), 'named')
                if not found and cb.get('data') in m['tables']['cbdata']:
                    t = time_row(bytes.fromhex(m['tables']['cbdata'][cb['data']]))
                    found = (t, 'shape') if t is not None else None
        if found:
            s = seen.setdefault(round(found[0], 5), {'draws': 0, 'eids': [r['eid'], r['eid']], 'how': found[1]})
            s['draws'] += 1
            s['eids'][1] = r['eid']
    if not seen:
        return None
    best = max(seen, key=lambda t: seen[t]['draws'])
    return dict(time=best, **seen[best], others=[dict(time=t, **v) for t, v in sorted(seen.items()) if t != best])


def frame_number(cid):
    """RenderDoc names a capture <exe>_frame<N> by the rendered frame it took: N, else None."""
    f = re.search(r'frame(\d+)$', cid)
    return int(f.group(1)) if f else None


def export_texture(d, s, work, done, flip):
    """A static texture as textures/<hash>.png (+ _alpha.png when its alpha is not flat), once per hash. Unity on D3D
    uploads imported textures bottom row first (its UV v = 0 is the image's bottom), so flip turns them upright."""
    from PIL import Image
    h = s['hash']
    if h in done:
        return done[h]
    ent = {'file': None}
    try:
        dds = ddsio.read_dds(Path(d.texture_path(h)).read_bytes())
        im = ddsio.to_image(dds, 0, 0)
        if flip:
            im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        ent.update(format=dds.format, size=[dds.width, dds.height], mips=dds.mips, flipped=flip)
        p = work / 'textures' / (h[:12] + '.png')
        p.parent.mkdir(exist_ok=True)
        im.save(p)
        ent['file'] = p.relative_to(work).as_posix()
        if im.mode == 'RGBA':
            a = np.asarray(im)[..., 3]
            if a.min() != a.max():
                pa = p.with_name(h[:12] + '_alpha.png')
                im.getchannel('A').save(pa)
                ent['alpha'] = pa.relative_to(work).as_posix()
    except Exception as ex:                  # noqa: BLE001  (one unreadable texture must not stop the report)
        ent['error'] = repr(ex)
    done[h] = ent
    return ent


def export_contrib(d, cid, r, work, flip):
    """Each changed target's |change| as contrib/<capture>_<eid>_rt<slot>.png; flip turns image and box upright."""
    from PIL import Image
    out = []
    for c in r.get('contrib', []):
        ent = {k: c.get(k) for k in ('slot', 'format', 'box', 'pixels', 'sum', 'max') if k in c}
        if flip and c.get('box') and c.get('size'):
            x, y, w, h = c['box']
            ent.update(box=[x, c['size'][1] - y - h, w, h], box_as_stored=c['box'])
        if not c.get('changed') or not c.get('exr'):
            ent['changed'] = False
            out.append(ent)
            continue
        try:
            a = np.abs(np.nan_to_num(d.contrib(cid, r['eid'], c['slot'])['array'][..., :3]))
            a = a[::-1] if flip else a
            mag = a.max(-1)
            top = float(np.percentile(mag[mag > 0], 99.5)) if (mag > 0).any() else 1.0
            img = (np.clip(a / (top or 1.0), 0, 1) * 255 + 0.5).astype(np.uint8)
            im = Image.fromarray(img[..., 0] if img.shape[-1] == 1 else img)
            if max(im.size) > 384:
                k = 384.0 / max(im.size)
                im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.BOX)
            p = work / 'contrib' / ('%s_%d_rt%d.png' % (cid, r['eid'], c['slot']))
            p.parent.mkdir(exist_ok=True)
            im.save(p)
            ent.update(file=p.relative_to(work).as_posix(), white=float('%.4g' % top))
        except Exception as ex:              # noqa: BLE001
            ent['error'] = repr(ex)
        out.append(ent)
    return out


def quad_rows(idx):
    """(particles, 6) index rows when the draw is Unity billboards (two triangles over four vertices each)."""
    if idx is None or not len(idx) or len(idx) % 6:
        return None
    g = np.asarray(idx, np.int64).reshape(-1, 6)
    return g if ((np.diff(np.sort(g, 1), axis=1) != 0).sum(1) == 3).all() else None


def copy_rows(idx):
    """(copies, indices per copy) rows when an indexed draw is one mesh drawn several times, each copy's vertices
    appended after the last (Unity's CPU-batched mesh particles: WotR's breath arcs are 3 copies of a 576-index mesh
    in one draw), else None. The shortest repeating block wins; blocks must not share vertices, so a ribbon or strip
    whose segments repeat one index pattern stays one mesh."""
    if idx is None or len(idx) < 6:
        return None
    idx = np.asarray(idx, np.int64)
    n = len(idx)
    for size in range(3, n // 2 + 1, 3):
        if n % size:
            continue
        g = idx.reshape(-1, size)
        step = int(g[1].min() - g[0].min())
        if step > g[0].max() - g[0].min() and (g - g[:1] == (np.arange(len(g)) * step)[:, None]).all():
            return g
    return None


def screen_particles(d, cid, r, flip):
    """Per particle (billboard quad, instance, or the whole draw): screen centre, size in pixels, z/w, from post-VS;
    flip measures y from the top of the upright image."""
    try:
        pv = d.postvs(cid, r['eid'])
    except Exception as ex:                  # noqa: BLE001  (one unreadable draw must not stop the report)
        return None, 'no post-VS output: %r' % ex
    ent, (px, py, z), w = pv['entry'], pv['screen'], pv['array'][..., 3]
    if flip:
        vy, vh = r['viewport'][1], r['viewport'][3]
        py = 2 * vy + vh - py
    groups, unit = None, 'draw'
    if ent.get('inst_stride'):
        groups, unit = [(i, slice(None)) for i in range(px.shape[0])], 'instance'
    elif ent.get('index'):
        raw = (Path(d.resolve(cid)['store']) / 'blobs/postvs' / (ent['index'] + '.bin')).read_bytes()
        idx = np.frombuffer(raw, '<u2' if ent.get('index_stride') == 2 else '<u4').astype(np.int64)
        rows = quad_rows(idx + ent.get('base_vertex', 0))
        copies = copy_rows(idx + ent.get('base_vertex', 0)) if rows is None else None
        if rows is not None and rows.max() < px.shape[1]:
            groups, unit = [(0, q) for q in rows], 'billboard'
        elif copies is not None and copies.max() < px.shape[1]:
            groups, unit = [(0, np.unique(q)) for q in copies], 'mesh'
    if groups is None:
        groups = [(0, slice(None))]
    out = []
    for i, sel in groups:
        ok = w[i][sel] > 0
        if not ok.any():
            out.append(None)
            continue
        xs, ys = px[i][sel][ok], py[i][sel][ok]
        out.append([float(xs.min() + xs.max()) / 2, float(ys.min() + ys.max()) / 2, float(xs.max() - xs.min()),
                    float(ys.max() - ys.min()), float(np.nanmean(z[i][sel][ok]))])
    return out, unit


def input_particles(d, cid, r):
    """Vertex inputs (Unity's particle COLOR, TEXCOORDs...): per particle at each billboard's first vertex or each mesh
    copy's per-channel maximum, and for a draw that is neither, over the vertices it uses (a mesh carries its colour
    in COLOR too)."""
    try:
        mesh = d.mesh(cid, r['eid'])
    except Exception:                        # noqa: BLE001  (no vertex blob, or a format _vertex_array lacks)
        return {}, {}
    rows = quad_rows(mesh['indices'])
    copies = copy_rows(mesh['indices']) if rows is None else None
    per_inst = {a['name'] for a in mesh['layout'] if a['perInstance']}
    out, verts = {}, {}
    for name, vals in mesh['attributes'].items():
        ids = mesh['element_indices'].get(name)
        if ids is None or name.upper().startswith(('POSITION', 'SV_')):
            continue
        ids = np.clip(ids, 0, len(vals) - 1)
        if name in per_inst:
            out[name] = np.asarray(vals, np.float64)[ids]
        elif rows is not None:
            out[name] = np.asarray(vals, np.float64)[ids.reshape(-1, 6)[:, 0]]
        elif copies is not None:              # the mesh's own vertex colours fade its edges: the brightest vertex
            out[name] = np.asarray(vals, np.float64)[ids.reshape(len(copies), -1)].max(1)   # carries the particle's
        else:
            verts[name] = np.asarray(vals, np.float64)[np.unique(ids)]
    return out, verts


def particles(d, cid, r, work, flip):
    scr, unit = screen_particles(d, cid, r, flip)
    inp, verts = input_particles(d, cid, r)
    n = max([len(scr or [])] + [len(v) for v in inp.values()])
    summary = {'count': n, 'unit': unit if scr is not None else 'unknown'}
    if scr is None:
        summary['note'] = unit
    else:
        on = np.array([s for s in scr if s], np.float64).reshape(-1, 5)
        summary.update(on_screen=len(on), size_px=pct(np.maximum(on[:, 2], on[:, 3])),
                       x_range=fmt([float(on[:, 0].min()), float(on[:, 0].max())]) if len(on) else [],
                       y_range=fmt([float(on[:, 1].min()), float(on[:, 1].max())]) if len(on) else [],
                       z_over_w=pct(on[:, 4], (50,)))
    summary['inputs'] = {k: spread(v) for k, v in inp.items() if len(v)}
    if verts:
        summary['vertex_inputs'] = {k: spread(v) for k, v in verts.items() if len(v)}
    if n > 1:
        p = work / 'particles' / ('%s_%d.csv' % (cid, r['eid']))
        p.parent.mkdir(exist_ok=True)
        with p.open('w', newline='') as fh:
            wr = csv.writer(fh)
            wr.writerow(['particle', 'centre_x', 'centre_y', 'width_px', 'height_px', 'z_over_w'] +
                        ['%s_%s' % (k, CHANNELS[c]) for k, v in inp.items() for c in range(v.shape[1])])
            for i in range(n):
                s = scr[i] if scr and i < len(scr) and scr[i] else [''] * 5
                wr.writerow([i] + [fmt(x) for x in s] +
                            [fmt(float(v[i, c])) if i < len(v) else '' for v in inp.values() for c in range(v.shape[1])])
        summary['csv'] = p.relative_to(work).as_posix()
    return summary


def copy_shader(store, sh, work):
    src = Path(store) / 'blobs/shaders' / (sh['checksum'] + '.asm')
    if not src.exists():
        return None
    dst = work / 'shaders' / src.name
    dst.parent.mkdir(exist_ok=True)
    shutil.copyfile(src, dst)
    return dst.relative_to(work).as_posix()


def readers(m, target, after, store, work):
    """Draws and dispatches after eid `after` that read `target` through a shader resource, kept or not: the passes
    that turn a side target into pixels (WotR's refraction composite is an opaque full-screen draw rdcdigest does not
    keep, so without this the mask's use appeared nowhere)."""
    out = []
    for r in m['draws']:
        if r['eid'] <= after:
            continue
        for st, sh in r.get('shaders', {}).items():
            if any(s.get('id') == target for s in sh.get('srvs', [])):
                ps = r.get('shaders', {}).get('ps') or r.get('shaders', {}).get('cs') or {}
                out.append({'eid': r['eid'], 'kind': r['kind'], 'kept': bool(r.get('keep')),
                            'reads': [s.get('resource') or s.get('name') for s in sh.get('srvs', [])],
                            'writes': [t.get('name') or t.get('id') for t in r.get('targets', [])],
                            'blend': (r.get('blend_summary') or [{}])[0].get('kind'),
                            'shader': {'checksum': ps.get('checksum'),
                                       'asm': copy_shader(store, ps, work) if ps.get('checksum') else None}})
                break
    return out


def upright_final(store, cid, m, main, flip, work):
    """(path, note) of the frame to show. A final.png that is black came from an undrawn swap buffer (rdcdigest before
    the D3D11 flip-model fix; `digest --final-only` redoes it): then the scene target after its last kept draw, turned
    upright, stands in."""
    from PIL import Image
    cap = Path(store) / 'captures' / cid
    final = cap / 'final.png'
    dst = work / cid / 'final.png'
    dst.parent.mkdir(exist_ok=True)
    if final.exists() and np.asarray(Image.open(final).convert('L')).any():
        shutil.copyfile(final, dst)
        return dst.relative_to(work).as_posix(), None
    after = ((m.get('targets') or {}).get(main) or {}).get('after') or {}
    alt = cap / 'rt' / after['png'] if after.get('png') else None
    if alt is None or not alt.exists():
        if final.exists():
            shutil.copyfile(final, dst)
            return dst.relative_to(work).as_posix(), 'final.png is black and no scene-target image was kept'
        return None, 'no final.png'
    im = Image.open(alt)
    (im.transpose(Image.Transpose.FLIP_TOP_BOTTOM) if flip else im).save(dst)
    return dst.relative_to(work).as_posix(), (
        'final.png is black (an undrawn swap buffer: redo it with `rdcdigest.py digest <folder> --final-only`); shown '
        'instead: the scene target %s after its last kept draw (eid %s), before post-processing%s' % (
            main, (m['targets'][main]).get('after_eid'), ', turned upright' if flip else ''))


def analyse_capture(d, cid, work, done, flip, ui_re=UI_TEX):
    m = d.manifest(cid)
    store = d.resolve(cid)['store']
    T = m['tables']
    scene = effect_draws(m)
    ui = ui_draws(scene, ui_re)
    main = main_target(scene, ui)
    groups = {}
    for r in scene:
        groups.setdefault(layer_key(r), []).append(r)
    layers = []
    for rs in groups.values():
        r0 = rs[0]
        bs = (r0.get('blend_summary') or [{}])[0]
        dep, ras = T['depth'].get(r0.get('depth'), {}), T['raster'].get(r0.get('raster'), {})
        tex = []
        for s in srvs(r0):
            e = {k: s.get(k) for k in ('register', 'name', 'resource', 'format', 'view_format', 'width', 'height',
                                       'mips', 'hash') if s.get(k) not in (None, '')}
            if s.get('dynamic'):
                e.update(dynamic=True, written_at=s.get('written_at'))
            elif s.get('hash'):
                e.update(export_texture(d, s, work, done, flip))
            tex.append(e)
        mats = [material(m, r) for r in rs]
        named = [x for x in mats if x is not None]
        common = {k: v for k, v in named[0].items() if all(x.get(k) == v for x in named)} if named else None
        draws = []
        for r, mat in zip(rs, mats):
            up, why = flipped(m, r)
            up = up and flip
            draws.append({'eid': r['eid'], 'marker': r.get('path', ''), 'indices': r.get('indices'),
                          'instances': r.get('instances'), 'keep': r.get('keep'), 'gpu_us': r.get('gpu_us'),
                          'flipped': up, 'flip_why': why if flip else 'off (--no-flip)',
                          'light': float('%.5g' % light(r, main)), 'change': float('%.5g' % light(r)),
                          'contrib': export_contrib(d, cid, r, work, up),
                          'particles': particles(d, cid, r, work, up),
                          'material_diff': {k: v for k, v in (mat or {}).items()
                                            if common is not None and k not in common}})
        t0 = (r0.get('targets') or [{}])[0].get('id')
        kind = 'ui' if all(r['eid'] in ui for r in rs) else 'scene' if t0 == main else 'side'
        layers.append({
            'capture': cid, 'eids': [r['eid'] for r in rs], 'kind': kind,
            'ui': ui[r0['eid']] if kind == 'ui' else None,
            'light': float('%.5g' % sum(x['light'] for x in draws)),
            'change': float('%.5g' % sum(x['change'] for x in draws)),
            'blend': {k: bs.get(k) for k in ('kind', 'color', 'alpha', 'writeMask')},
            'depth': {k: dep.get(k) for k in ('depthEnable', 'depthWrites', 'depthFunction')},
            'cull': ras.get('cullMode'),
            'targets': [{k: t.get(k) for k in ('id', 'name', 'format', 'size')} for t in r0.get('targets', [])],
            'shaders': {st: {'checksum': sh['checksum'], 'asm': copy_shader(store, sh, work)}
                        for st, sh in r0.get('shaders', {}).items() if st in ('vs', 'ps')},
            'textures': tex, 'material': common, 'draws': draws})
    # the scene colour target's layers by the light they add, then side-target layers and UI by their own change
    order = {'scene': 0, 'side': 1, 'ui': 2}
    layers.sort(key=lambda x: (order[x['kind']], -(x['light'] if x['kind'] == 'scene' else x['change'])))
    for i, layer in enumerate(layers):
        layer['rank'] = i + 1
    side = {}
    for ly in layers:
        if ly['kind'] == 'side':
            t = ly['targets'][0]
            s = side.setdefault(t['id'], dict(t, written_by=[]))
            s['written_by'] += ly['eids']
    for s in side.values():
        s['readers'] = readers(m, s['id'], min(s['written_by']), store, work)
    mt = next((t for r in scene for t in r.get('targets', [])[:1] if t['id'] == main), {})
    cap = {'id': cid, 'rdc': m.get('capture'), 'api': m.get('api'), 'counts': m.get('counts'),
           'scene_draws': sum(len(x['eids']) for x in layers if x['kind'] != 'ui'),
           'ui_draws': sum(len(x['eids']) for x in layers if x['kind'] == 'ui'),
           'main_target': {k: mt.get(k) for k in ('id', 'name', 'format', 'size')} if mt else None,
           'side_targets': list(side.values()), 'layers': layers,
           'clock': unity_clock(m, [r for r in scene if r['eid'] not in ui])}
    up = sum(dr['flipped'] for ly in layers if ly['kind'] == 'scene' for dr in ly['draws'])
    path, note = upright_final(store, cid, m, main, flip and up * 2 >= max(1, sum(
        len(ly['draws']) for ly in layers if ly['kind'] == 'scene')), work)
    if path:
        cap['final'] = path
    if note:
        cap['final_note'] = note
    return cap


# ---- report -----------------------------------------------------------------------------------------------------
def rgba(v):
    return ' '.join('%.3g' % x for x in v)


def write_report(work, src, store, cmd, videos, caps, top, flip):
    L = ['# Unity capture analysis: %s' % src.name, '',
         'Generated %s by `unity_rdc.py` (tools/unity) from `%s`; digest store `%s`.' % (
             datetime.date.today().isoformat(), src, store),
         'Every value below names its capture and event id (eid); query more with '
         '`python "%s" --store "%s" draw <capture> <eid> --cbuffers`.' % (RDC / 'rdcdigest.py', store), '',
         ('Upright: Unity on D3D keeps textures and render targets bottom-up (as RenderDoc shows them), so texture '
          'PNGs, contribution images and boxes, and particle y are turned upright here, per draw from its '
          '`_ProjectionParams.x`; y counts from the top of the frame as in `final.png`. `--no-flip` keeps them as '
          'stored.') if flip else 'Not flipped (`--no-flip`): textures, contributions and particle y are as stored.',
         '']
    if cmd:
        L += ['Digest command: `%s`' % subprocess.list2cmdline(cmd), '']
    L += ['## Footage', '']
    if not videos:
        L += ['No video in the folder. A capture is one frame: spawn timing, lifetimes and motion need footage of the '
              'effect beside the captures (rerun this script after adding it).', '']
    for v in videos:
        L.append('- `%s`: %s fps, %s s, %s frames at 1 fps in `%s`%s%s' % (
            v['video'], v.get('fps'), v.get('seconds'), v.get('count'), v['frames'],
            '; sheet ![](%s)' % v['sheet'] if v.get('sheet') else '', '; ERROR ' + v['error'] if v.get('error') else ''))
    L.append('')
    L += clock_md(caps)
    for cap in caps:
        c = cap['counts'] or {}
        mt = cap.get('main_target') or {}
        by = {k: [ly for ly in cap['layers'] if ly['kind'] == k] for k in ('scene', 'side', 'ui')}
        L += ['## Capture `%s`' % cap['id'], '',
              '`%s`, %s: %s draws, %s kept; %d kept scene draws in %d layers (%d into the scene colour target, %d into '
              'side targets), %d UI draws in %d layers left out of the ranking.' % (
                  cap['rdc'], cap['api'], c.get('draws'), c.get('kept'), cap['scene_draws'],
                  len(by['scene']) + len(by['side']), len(by['scene']), len(by['side']), cap.get('ui_draws', 0),
                  len(by['ui'])), '',
              'Scene colour target: `%s` %s %s %s. Layers are ranked by the light they add to it; layers drawing into '
              'other targets follow under "Side targets".' % (mt.get('id'), mt.get('name') or '', mt.get('format'),
                                                               mt.get('size')), '']
        if cap.get('final'):
            L += ['![final frame](%s)' % cap['final'], '']
        if cap.get('final_note'):
            L += ['**Final frame:** %s.' % cap['final_note'], '']
        L += ['| rank | eids | draws | particles | blend | depth test/write | textures | light (sum abs dRGB) |',
              '|---:|---|---:|---:|---|---|---|---:|']
        for ly in by['scene']:
            L.append('| %d | %s | %d | %s | %s | %s/%s | %s | %.4g |' % (
                ly['rank'], eids(ly['eids']), len(ly['eids']), particle_count(ly), ly['blend']['kind'],
                ly['depth']['depthEnable'], ly['depth']['depthWrites'], tex_names(ly), ly['light']))
        L.append('')
        if by['side']:
            L += ['### Side targets', '',
                  'Draws into a target other than the scene colour: masks, distortion or glow buffers. Their change is '
                  'not light; the passes that read the target (kept or not) say what it does.', '',
                  '| rank | eids | target | blend | textures | change (sum abs dRGB) |', '|---:|---|---|---|---|---:|']
            for ly in by['side']:
                t = ly['targets'][0]
                L.append('| %d | %s | `%s` %s | %s | %s | %.4g |' % (ly['rank'], eids(ly['eids']), t.get('id'),
                                                                   t.get('name') or '', ly['blend']['kind'],
                                                                   tex_names(ly), ly['change']))
            L.append('')
            for s in cap['side_targets']:
                L.append('- `%s` %s %s %s, written by eid %s; read by %s' % (
                    s['id'], s.get('name') or '', s.get('format'), s.get('size'), eids(s['written_by']),
                    '; '.join('eid %d (%s%s, %s, writes %s, reads %s%s)' % (
                        x['eid'], x['kind'], '' if x['kept'] else ', not kept', x['blend'], ', '.join(x['writes']),
                        ', '.join(map(str, x['reads'])),
                        ', shader `%s`' % x['shader']['asm'] if x['shader'].get('asm') else '')
                        for x in s['readers']) or 'nothing later in the frame'))
            L.append('')
        if by['ui']:
            L += ['UI left out of the ranking (a UI texture, or the shaders of a draw that reads one; `--ui-re`): %s.' %
                  '; '.join('eid %s (%s)' % (eids(ly['eids']), ly['ui']) for ly in by['ui'][:12]) +
                  (' ... %d more layers in `layers.json`' % (len(by['ui']) - 12) if len(by['ui']) > 12 else ''), '']
        shown = by['scene'] + by['side']
        for ly in shown[:top]:
            L += layer_md(cap['id'], ly)
        if len(shown) > top:
            L += ['%d more layers in `layers.json` (`--top`).' % (len(shown) - top), '']
    L += ['## What a capture cannot give', '',
          '- Timing: one frame shows the particles alive at one age. Spawn rate, lifetime and curves over life come '
          'from the footage (and from Unity particle-system data, if it can be exported).',
          '- Colour: `COLOR` inputs are Unity\'s per-particle vertex colour (start colour x colour over lifetime) at '
          'this frame; the material values multiply it in the shader (read the `.asm`).',
          '- Screen sizes are bounding boxes of each quad on screen, so rotated quads read larger; compare build and '
          'game with scale-free measures, since their cameras differ.', '']
    (work / 'REPORT.md').write_text('\n'.join(L), encoding='utf-8')


def clock_md(caps):
    """The Capture clock section: each capture's _Time.y, its time after the first, the 60 fps frame that is, and the
    game's frame rate between captures from their frame numbers."""
    timed = sorted((c for c in caps if c.get('clock')), key=lambda c: c['clock']['time'])
    L = ['## Capture clock', '']
    if not timed:
        return L + ['No capture holds Unity\'s `_Time` (by name or by its (t/20, t, 2t, 3t) row): place captures by '
                    'their frame numbers only with the game\'s frame rate in hand.', '']
    t0 = timed[0]['clock']['time']
    L += ['Unity\'s `_Time.y` that the kept scene draws read (by name, or as the first constant row shaped (t/20, t, 2t, 3t): '
          'WotR and Unity game B keep a second clock of that shape a few rows later). A capture\'s frame number counts rendered frames, so the gap between two captures '
          'is time only at the game\'s real frame rate: place a build\'s frames by `_Time`, '
          '`frame = (t - t0) x 60` from a capture whose build frame is known, and start a scrolling texture\'s phase '
          'at the game\'s `_Time` (rate x t).', '',
          '| capture | `_Time.y` | read by draws (eids) | after the first, s | at 60 fps, frames | game fps since the last |',
          '|---|---:|---|---:|---:|---:|']
    prev = None
    for c in timed:
        k = c['clock']
        n = frame_number(c['id'])
        fps = ''
        if prev and n is not None and prev[1] is not None and k['time'] > prev[0]:
            fps = '%.1f' % ((n - prev[1]) / (k['time'] - prev[0]))
        L.append('| `%s` | %.5f | %d (%d-%d, %s) | %.3f | %.1f | %s |' % (
            c['id'], k['time'], k['draws'], k['eids'][0], k['eids'][1], k['how'], k['time'] - t0,
            (k['time'] - t0) * 60, fps))
        prev = (k['time'], n)
    others = ['`%s`: %s' % (c['id'], ', '.join('%.5f (%d draws, eid %d-%d)' % (o['time'], o['draws'], *o['eids'])
                                              for o in c['clock']['others'])) for c in timed if c['clock']['others']]
    if others:
        L += ['', 'Other `_Time` values read by fewer kept draws (another camera, or a draw left from an earlier '
              'frame): ' + '; '.join(others) + '.']
    return L + ['']


def eids(es):
    return ', '.join(map(str, es[:6])) + (' ... (%d)' % len(es) if len(es) > 6 else '')


def tex_names(ly):
    """The layer's textures by resource name (the shader's own names are often texture0, texture1...)."""
    return ', '.join(t.get('resource') or t.get('name') or 't%s' % t.get('register') for t in ly['textures']) or '-'


def particle_count(ly):
    return sum(x['particles']['count'] for x in ly['draws'])


def colour_cell(p):
    """COLOR p50 per particle, else the colour range over the draw's vertices (meshes carry it per vertex)."""
    col = next((v for k, v in p['inputs'].items() if k.upper().startswith('COLOR')), None)
    if col:
        return rgba(col['p50'])
    col = next((v for k, v in p.get('vertex_inputs', {}).items() if k.upper().startswith('COLOR')), None)
    if col:
        return rgba(col['p50']) if col['min'] == col['max'] else 'vertices %s .. %s' % (rgba(col['min']),
                                                                                       rgba(col['max']))
    return '-'


def layer_md(cid, ly):
    b, dep = ly['blend'], ly['depth']
    L = ['### %s layer %d (eid %s)' % (cid, ly['rank'], eids(ly['eids'])), '',
         '- blend: %s (colour `%s`, alpha `%s`, write mask %s); depth test %s, write %s, func %s; cull %s' % (
             b['kind'], b['color'], b['alpha'], b['writeMask'], dep['depthEnable'], dep['depthWrites'],
             dep['depthFunction'], ly['cull']),
         '- targets: %s%s' % (', '.join('`%s` %s %s %s' % (t.get('id'), t.get('name') or '', t['format'], t['size'])
                                        for t in ly['targets']),
                             '' if ly['kind'] == 'scene' else ' (side target: change %.4g, no light ranked)' % ly['change'])]
    ups = {}
    for dr in ly['draws']:
        ups.setdefault('%s (%s)' % ('turned upright' if dr['flipped'] else 'as stored', dr['flip_why']),
                       []).append(dr['eid'])
    L.append('- orientation: ' + '; '.join('%s: eid %s' % (k, eids(v)) for k, v in ups.items()))
    for st, sh in ly['shaders'].items():
        L.append('- %s `%s`%s' % (st.upper(), sh['checksum'], ' (`%s`)' % sh['asm'] if sh['asm'] else ''))
    for t in ly['textures']:
        what = ('dynamic (scene texture written at eid %s: soft particles, distortion or a grab pass)' % t['written_at']
                if t.get('dynamic') else '%s %sx%s, %s mips, `%s`%s%s%s' % (
                    t.get('format') or t.get('view_format'), t.get('width'), t.get('height'), t.get('mips'),
                    (t.get('hash') or '')[:12], ' ![](%s)' % t['file'] if t.get('file') else '',
                    ' alpha ![](%s)' % t['alpha'] if t.get('alpha') else '',
                    ' ERROR ' + t['error'] if t.get('error') else ''))
        L.append('- t%s %s%s: %s' % (t.get('register'), t.get('name', ''),
                                     ' (%s)' % t['resource'] if t.get('resource') else '', what))
    if ly['material'] is None:
        L.append('- material: no reflection names; `draw <capture> <eid> --cbuffers` prints the raw float4 rows')
    else:
        L.append('- material: ' + ('; '.join('`%s` %s' % (k, json.dumps(v)) for k, v in ly['material'].items())
                                   or 'no material values'))
    L += ['', '| eid | marker | indices | inst | particles | size px p10/50/90 | COLOR p50 | dRGB sum | changed px, box |'
              ' contribution |', '|---:|---|---:|---:|---:|---|---|---|---|---|']
    for dr in ly['draws']:
        p = dr['particles']
        c = next((x for x in dr['contrib'] if x.get('file') or x.get('sum')), {})
        none = 'unchanged' if dr['contrib'] and not c else '-'
        L.append('| %d | %s | %s | %s | %d %s%s | %s | %s | %s | %s %s | %s |' % (
            dr['eid'], '/'.join(dr['marker'].split('/')[-2:]) or '-', dr['indices'], dr['instances'], p['count'], p['unit'],
            ' ([csv](%s))' % p['csv'] if p.get('csv') else '', '/'.join(map(str, p.get('size_px', []))) or '-',
            colour_cell(p), rgba(c['sum'].get(k, 0) for k in 'RGB') if c.get('sum') else none,
            c.get('pixels', none), c.get('box', ''), '![](%s)' % c['file'] if c.get('file') else none))
        if dr['material_diff']:
            L.append('|  | material: %s |' % '; '.join('`%s` %s' % (k, json.dumps(v))
                                                        for k, v in dr['material_diff'].items()))
    L.append('')
    return L


def write_view(work, src, videos, caps, top):
    e = html.escape
    H = ['<!doctype html><meta charset="utf-8"><title>Unity %s</title><style>'
         'body{background:#141414;color:#ddd;font:14px system-ui;margin:16px}img{max-width:100%%;image-rendering:pixelated;'
         'background:#000}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start;border-top:1px solid #333;'
         'padding:10px 0}.row>div{max-width:400px}code{color:#9cf}h2{margin-top:32px}</style>' % e(src.name),
         '<h1>%s</h1>' % e(src.name)]
    for v in videos:
        if v.get('sheet'):
            H.append('<h2>%s (%s fps)</h2><img src="%s">' % (e(v['video']), v.get('fps'), e(v['sheet'])))
    for cap in caps:
        H.append('<h2>%s</h2>' % e(cap['id']))
        if cap.get('final'):
            H.append('<img src="%s" style="max-width:960px">' % e(cap['final']))
        if cap.get('final_note'):
            H.append('<p>%s</p>' % e(cap['final_note']))
        for ly in [x for x in cap['layers'] if x['kind'] != 'ui'][:top]:
            imgs = ''.join('<div><img src="%s"><br>eid %d contribution</div>' % (e(c['file']), dr['eid'])
                           for dr in ly['draws'][:4] for c in dr['contrib'] if c.get('file'))
            texs = ''.join('<div><img src="%s" width="160"><br>t%s %s</div>' % (
                e(t['file']), t.get('register'), e(t.get('resource') or t.get('name', ''))) for t in ly['textures']
                if t.get('file'))
            what = ('light %.4g' % ly['light'] if ly['kind'] == 'scene' else
                    'side target %s, change %.4g' % (ly['targets'][0].get('id'), ly['change']))
            H.append('<div class="row"><div><b>#%d</b> eid %s<br>%s, %s<br>%d %s<br>PS <code>%s</code>'
                     '</div>%s%s</div>' % (ly['rank'], e(eids(ly['eids'])), e(str(ly['blend']['kind'])), e(what),
                                           particle_count(ly), e(ly['draws'][0]['particles']['unit']),
                                           e(ly['shaders'].get('ps', {}).get('checksum', '-')[:12]), imgs, texs))
    (work / 'view.html').write_text('\n'.join(H), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('folder', help='the effect\'s capture folder (its .rdc files and any videos)')
    ap.add_argument('--work', help='output folder (default out/edits/<game>-<folder>-<yyyymmdd>, bundled '
                                   '<capture folder\'s parent>/_analysis/<game>-<folder>-<yyyymmdd>)')
    ap.add_argument('--overwrite', action='store_true', help='write into a --work folder that already exists')
    ap.add_argument('--quick', action='store_true', help='rdcdigest\'s default digest instead of the exact one')
    ap.add_argument('--skip-video', action='store_true')
    ap.add_argument('--skip-digest', action='store_true', help='analyse the existing <folder>/rdcdigest only')
    ap.add_argument('--renderdoc', default=rdcdigest.DEFAULT_RD)
    ap.add_argument('--ffmpeg')
    ap.add_argument('--marker-re', help='rdcdigest --marker-re (default: its effect/particle/... names)')
    ap.add_argument('--top', type=int, default=40, help='layers per capture detailed in the report (default 40)')
    ap.add_argument('--no-flip', action='store_true',
                    help='keep textures, contributions and particle y as stored (Unity on D3D: bottom-up)')
    ap.add_argument('--ui-re', default=UI_TEX,
                    help='texture names that mark a UI draw (default: %(default)s); "$^" keeps UI in the ranking')
    args = ap.parse_args()
    src = Path(args.folder).resolve()
    if not src.is_dir():
        sys.exit('not a folder: %s' % src)
    work = Path(args.work or (src.parent / '_analysis' if BUNDLED else REPO / 'out' / 'edits') / ('%s-%s-%s' % (
        game_of(src), re.sub(r'[^A-Za-z0-9_-]+', '-', src.name).lower(),
        datetime.date.today().strftime('%Y%m%d')))).resolve()
    if work.exists() and not args.overwrite:
        sys.exit('%s already exists: pass --overwrite to write into it, or --work <new folder>' % work)
    if work.exists():
        print('writing into the existing %s (--overwrite)' % work)
    work.mkdir(parents=True, exist_ok=True)
    store = src / 'rdcdigest'
    videos = [] if args.skip_video else footage(src, find_ffmpeg(args.ffmpeg), work)
    cmd = None if args.skip_digest else extract(src, store, args)
    if not (store / 'captures').is_dir():
        sys.exit('no digest store at %s' % store)
    digestlib.build_index([store], work / 'index.sqlite', rebuild=True)
    caps, done = [], {}
    with digestlib.open(str(work / 'index.sqlite')) as d:
        for c in d.captures():
            caps.append(analyse_capture(d, c['id'], work, done, not args.no_flip, args.ui_re))
            print('analyse: %s: %d layers, %d scene draws, %d UI draws left out%s' % (
                c['id'], len(caps[-1]['layers']), caps[-1]['scene_draws'], caps[-1]['ui_draws'],
                '; ' + caps[-1]['final_note'] if caps[-1].get('final_note') else ''))
    (work / 'layers.json').write_text(json.dumps({'source': str(src), 'store': str(store), 'footage': videos,
                                                  'captures': caps}, indent=1), encoding='utf-8')
    write_report(work, src, store, cmd, videos, caps, args.top, not args.no_flip)
    write_view(work, src, videos, caps, args.top)
    print('report: %s\nview:   %s' % (work / 'REPORT.md', work / 'view.html'))


if __name__ == '__main__':
    main()
