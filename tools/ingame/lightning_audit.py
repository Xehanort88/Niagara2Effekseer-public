#!/usr/bin/env python3
"""Check lightning effects against the user's rules in a real battle, frame by frame.

    python tools/ingame/lightning_audit.py --project "../ASSEMBLER TEST PROJECT" --ids 375,381 --out out/edits/<task>/audit
        [--troop 4] [--sub name.efkefc=file ...] [--port 8838] [--frames <dir of an earlier capture>] [--isolate]

--isolate judges only OUR lightning (user, 2026-10-07): the pack parts an effect keeps (starburst rays, debris,
crack rings, a falling arrow, static crackle) were found as lines too, so most rebuilt effects failed on parts that
are not bolts, and every report needed a list of excused flags. With --isolate each effect is also played as a copy
in which every drawn node draws nothing except those whose texture is a drawn bolt (--keep, a regex on the texture
file name; default KEEP: the user's bolt drawings, their recolours and padded copies, the bolt kit's core strip).
Lines are found in that copy; impact and spark light is still measured in the FULL play (a pack explosion is a valid
impact). An effect plays identically twice (capture diff under 0.06% of pixels, the backdrop's own flicker), so the
two plays line up frame for frame, IF the copy draws the same random numbers: a node is therefore blanked by giving
it a fully transparent texture, not hidden. IsRendered false skips the node's render setup and its random draws,
which shifted everything after it (Levin Barrage, Static Flare and Chain Currents came out misaligned, Levin with 27
bogus 'bare ground' frames); a node drawing through a material file is switched to the default material first. The report
compares the share of our line pixels lying on a line edge in the full play with the same share 3 frames off
('aligned'; must win by ALIGN_MARGIN or reach ALIGN_HIGH, else the result is marked unproven).
In the copy, FADING lines are found too: under the normal brightness bar, where a bright line stood within FADING
frames (safe there because no pack line is left). A fading line needs light FADE_RATIO x its own brightness: a dim
bolt standing on a dimmer glow fails (the 'faint' violations).
The copy is built with `edit` from the installed effect (or its --sub replacement) into <out>/isolated/.

Raw frames (0.9 MB each, every frame of every play) are deleted once each effect is judged: one run over 23 effects
left 3.4 GB. --keep-frames keeps them (GIFs, measurements, a later --frames run); --frames input is never deleted.

It plays each animation once with tools/ingame/run.js in capture mode (every game frame, 640x360) against a troop with
ONE enemy (default troop 4: two enemies make RPG Maker play the animation twice, 12 frames apart, which looks like a
stray bolt), then for every frame:
  background  per-pixel MAXIMUM over the frames before the effect starts (the backdrop flickers and the party
              breathes; the brightest of those frames absorbs that, where a minimum over all frames called the
              pulsing backdrop light 'glow' and outlined the party sprites as 'lines');
  added       luminance the effect adds over it;
  lines       thin bright structures (bolts, forks, arcs): added light well above its 11x11 neighbourhood, bright in
              the frame itself, connected, thin, and long or kinked (short straight spark streaks are not lightning);
  impact      added light averaged over 33x33 round a line's lowest point: a flash or burst is wider than the halo
              a bolt texture carries itself (19x19 counted that halo as light: bare feet passed);
  spark       added light averaged over 9x9, brightest anywhere along a line: a spark is a small bright disc,
              brighter there than a line crossing the window (a kinked arc's base is not always one of its extreme
              points). With base sparks 95% of Static Flare's arcs read 153+, without them 23 of 59 read under 140.
A spark also counts when its core is near-white in the frame itself (over a bright backdrop the ADDED light of a
spark measures small). Lines that run off the edge of the view, or lie over a bright backdrop (mean luminance 120+,
e.g. the test backdrop's light column), are not judged as lone.
Violations (README "Procedural lightning bolts"):
  bare-ground  a line whose lowest point reaches the ground line with no glow there (a bolt or fork touching the
               floor without an impact);
  lone-line    a line with no glow at either end (lightning on its own, no lit point).
The ground line is the median lowest point of strikes from the sky (lines starting in the top 30% of the view and
spanning half of it, at least 3). In a GOOD strike the foot sits in its flash, which hides the bottom of the line, so
the ground is often not found: then the report says bare-ground was NOT checked. Pass --ground Y (in the 640x360
capture; 210 for troop 4 in the ASSEMBLER TEST PROJECT, measured on 374-style bare strikes) to check it anyway.
Writes <out>/audit.json and <out>/audit-<id>.png (every frame with a violation, circled). Exit status 1 if any
violation, 2 on a capture failure. Thresholds are on 0..255 luminance and were set on 374-style strikes (bare
late strikes, long forks) against the fixed 375; a pass is evidence, not a substitute for the user's eye.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

W, H = 640, 360
ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll'
ALIGN_MARGIN = 0.08   # aligned score must beat the one 3 frames off by this (one frame off: 375 69% vs 41%, 393 68% vs 56%)
ALIGN_HIGH = 0.85     # ... or be this high: a lingering chain (Chain Currents) matches itself a frame later too
KEEP = r'(?i)(LightningBolt|LightningBeam|Lightning_-12_3|BoltCore|BoltReference|ProcBolt)'
LINE_RIDGE, LINE_MIN, BRIGHT, MIN_PIXELS, MAX_THICK = 35, 80, 170, 30, 7.0
# --isolate only (no pack lines left to mistake): FAINT lines, under the thresholds above, are found too, and a faint
# line needs light in proportion to its own brightness: FADE_RATIO x its brightness, capped at the normal bar. User,
# 2026-10-07 (ThunderFall calm f47): a fading bolt stood dim on a dimmer glow; bolt vs foot light (33x33 mean)
# measured 89/91, 67/58, 44/40, 22/23 frame by frame there, and 90/157, 45/107 once the foot light outlasted the bolt.
FAINT_RIDGE, FAINT_MIN, FAINT_OVERLAP, FADE_RATIO, FAINT_JOIN = 10, 18, 0.3, 1.3, 6
FADING = 12          # frames: a faint line is judged only where a bright line stood within this many frames (a fading
FADING_SHARE = 0.5   # line); without this, never-bright fork fragments and dim chain pieces flooded the report
FAINT_BELOW = 80   # px: a faint line also counts light straight below its low end (a fading bolt is found only in
                   # pieces: over a brighter backdrop its lower part is too weak; ThunderFall calm f48: foot 57 px below)
RESTLESS = 6   # a pixel whose brightness varies by more than this BEFORE the effect starts (the party's idle animation,
               # backdrop flicker) is not looked at for faint lines: a party member's idle frame read as one (f56)
MIN_EXTENT, LONG, KINK = 28, 60, 0.03
IMPACT_R, IMPACT_MIN, SPARK_R, SPARK_MIN, GROUND_TOL, END_R = 16, 60, 4, 135, 16, 6
WHITE_CORE, BRIGHT_BACKDROP, SPARK_REACH = 215, 120, 18
# SPARK_REACH: a scrolling bolt texture's line wanders across its strip, so the drawn arc can start up to ~15 px from
# the spark at the strip's base (with 18 px the no-spark Static Flare still fails on all 36 arcs)   # a spark's near-white core (7x7 mean of the frame); backdrop too bright to judge   # the floor is in perspective: contact further back sits higher
# measured 2026-10-06: bare feet of 374-style strikes 34-50, every foot of the fixed 375 70 or more (33x33 mean)


def box(a, r):
    """Mean over a (2r+1)^2 square, edges clamped (summed-area table)."""
    p = np.pad(a, r + 1, mode='edge').astype(np.float64)
    s = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    out = s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]
    return (out / (k * k))[:a.shape[0], :a.shape[1]]


def components(mask):
    """8-connected regions of a boolean mask: list of (ys, xs) arrays."""
    seen = np.zeros_like(mask, bool)
    out = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        py, px = [], []
        while stack:
            y, x = stack.pop()
            py.append(y); px.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        out.append((np.array(py), np.array(px)))
    return out


def drop_frames(dirpath):
    """Delete a capture's raw frames (f*.rgba: 0.9 MB each, uncompressed) once judged; returns the bytes freed.
    The audit kept every frame of every play: a run over the 23 lightning effects left 3.4 GB, and a day's runs
    about 20 GB (2026-10-07). Anything else in the folder (run.js's own records) stays."""
    freed = 0
    for f in Path(dirpath).glob('f*.rgba'):
        freed += f.stat().st_size
        f.unlink()
    return freed


def load(dirpath):
    frames = []
    i = 0
    while (Path(dirpath) / ('f%03d.rgba' % i)).exists():
        a = np.frombuffer((Path(dirpath) / ('f%03d.rgba' % i)).read_bytes(), np.uint8).reshape(H, W, 4)[..., :3]
        frames.append(a)
        i += 1
    return frames


def lum(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def background(frames, start_px=50, start_added=100):
    """(background luminance, first effect frame): the per-pixel maximum over the frames before the effect shows."""
    l0 = lum(frames[0].astype(np.float64))
    start = len(frames)
    for i, f in enumerate(frames[1:], 1):
        if ((lum(f.astype(np.float64)) - l0) > start_added).sum() >= start_px:
            start = i
            break
    return np.max(np.stack([lum(f.astype(np.float64)) for f in frames[:max(1, start)]]), axis=0), start


def kinked(ys, xs):
    """How far a line departs from a straight one: the minor/major spread of its pixels (0 = perfectly straight)."""
    c = np.cov(np.vstack([xs, ys]).astype(np.float64))
    ev = np.sort(np.linalg.eigvalsh(c))
    return float(ev[0] / ev[1]) if ev[1] > 0 else 0.0


def lines_and_glow(frame, bg_l, region):
    fl = lum(frame.astype(np.float64))
    added = np.clip(fl - bg_l, 0, 255)
    x0, y0, x1, y1 = region
    added[:y0, :] = 0; added[y1:, :] = 0; added[:, :x0] = 0; added[:, x1:] = 0
    ridge = added - box(added, 5)
    mask = (ridge > LINE_RIDGE) & (added > LINE_MIN) & (fl > BRIGHT)
    glow = (box(added, IMPACT_R), box(added, SPARK_R), box(fl, 3), bg_l)
    lines = []
    # an arc is found in fragments (broken at sharp kinks and where a spark's bright core sits on it): join pieces
    # within 3 px into one line before judging it
    joined = box(mask.astype(np.float64), 3) > 0
    for ys, xs in components(joined):
        keep = mask[ys, xs]
        ys, xs = ys[keep], xs[keep]
        if len(ys) < MIN_PIXELS:
            continue
        extent = max(ys.max() - ys.min(), xs.max() - xs.min()) + 1
        if len(ys) / extent > MAX_THICK:          # a glow's bright core, not a line
            continue
        if extent < MIN_EXTENT or (extent < LONG and kinked(ys, xs) < KINK):   # a short straight spark streak
            continue
        lines.append((ys, xs))
    return lines, glow


def faint_lines(frame, bg_l, region, strong, restless=None, recent=None):
    """Lines under the normal thresholds (a fading bolt) that are not part of a line already found (`strong`):
    [(ys, xs, brightness)], brightness = the 95th percentile of the light the line adds."""
    fl = lum(frame.astype(np.float64))
    added = np.clip(fl - bg_l, 0, 255)
    x0, y0, x1, y1 = region
    added[:y0, :] = 0; added[y1:, :] = 0; added[:, :x0] = 0; added[:, x1:] = 0
    mask = ((added - box(added, 5)) > FAINT_RIDGE) & (added > FAINT_MIN)
    if restless is not None:
        mask &= ~restless
    near_strong = np.zeros(mask.shape, bool)
    for ys, xs in strong:
        near_strong[ys, xs] = True
    near_strong = box(near_strong.astype(np.float64), 4) > 0
    out = []
    # joined across 6 px (3 for normal lines): a fading bolt breaks into pieces where it is faintest, and the upper
    # piece's low end then hung 'unlit' in mid-air above the lit foot (ThunderFall calm f48)
    for ys, xs in components(box(mask.astype(np.float64), FAINT_JOIN) > 0):
        keep = mask[ys, xs]
        ys, xs = ys[keep], xs[keep]
        if len(ys) < MIN_PIXELS or near_strong[ys, xs].mean() > FAINT_OVERLAP:
            continue
        if recent is not None and recent[ys, xs].mean() < FADING_SHARE:
            continue    # never a bright line here: not a FADING one (drawn fork fragments, dim chain bits: other topics)
        extent = max(ys.max() - ys.min(), xs.max() - xs.min()) + 1
        if len(ys) / extent > MAX_THICK or extent < MIN_EXTENT or (extent < LONG and kinked(ys, xs) < KINK):
            continue
        out.append((ys, xs, float(np.percentile(added[ys, xs], 95))))
    return out


def alignment(frames, iso_lines, shifts=(-3, 3)):
    """How well the isolated play lines up with the full one: the share of our line pixels that lie on a line edge
    in the full frame, against the same share with the full play shifted a frame either way. Brightness cannot be
    compared (packs darken the screen with a Blend tint while they play), and steady parts (a pillar) match at any
    shift, so the shifted score is the baseline: (score, best shifted score), or None with no lines."""
    def score(shift):
        hit = tot = 0
        for i, lines in enumerate(iso_lines):
            j = i + shift
            if not lines or not 0 <= j < len(frames):
                continue
            fl = lum(frames[j].astype(np.float64))
            edge = box(((fl - box(fl, 5)) > 8).astype(np.float64), 1) > 0
            for ys, xs in lines:
                hit += int(edge[ys, xs].sum())
                tot += len(ys)
        return hit / tot if tot else None
    s0 = score(0)
    if s0 is None:
        return None
    return s0, max(score(k) or 0 for k in shifts)


def ends(ys, xs):
    """The extreme points of a line: lowest, highest, leftmost, rightmost."""
    pts = [(ys.max(), xs[ys.argmax()]), (ys.min(), xs[ys.argmin()]), (ys[xs.argmin()], xs.min()), (ys[xs.argmax()], xs.max())]
    return [(int(y), int(x)) for y, x in pts]


def near(field, y, x):
    return field[max(0, y - END_R):y + END_R + 1, max(0, x - END_R):x + END_R + 1].max()


def impact(glow, y, x, need=IMPACT_MIN):
    return near(glow[0], y, x) >= need


def spark_near(field, ys, xs, r=SPARK_REACH):
    """The highest value of `field` within r pixels of a line (a spark's bright core breaks the detected line, and a
    burst arc's base is not always one of its extreme points)."""
    y0, y1 = max(0, ys.min() - r), min(field.shape[0], ys.max() + r + 1)
    x0, x1 = max(0, xs.min() - r), min(field.shape[1], xs.max() + r + 1)
    m = np.zeros((y1 - y0, x1 - x0), bool)
    m[ys - y0, xs - x0] = True
    grown = box(m.astype(np.float64), r) > 0
    return float(field[y0:y1, x0:x1][grown].max())


def at_edge(ys, xs, region, m=3):
    x0, y0, x1, y1 = region
    return xs.min() <= x0 + m or xs.max() >= x1 - 1 - m or ys.min() <= y0 + m or ys.max() >= y1 - 1 - m


def audit(frames, ground=None, region=(0, 0, W, 270), iso=None):
    """`iso`: frames of the same effect with only our lightning drawn (--isolate): its lines are judged against the
    light measured in `frames`. Returns (ground, violations, aligned share or None)."""
    bg, start = background(frames)
    per = [lines_and_glow(f, bg, region) if i >= start else ([], None) for i, f in enumerate(frames)]
    aligned = None
    if iso is not None:
        bgi, _ = background(iso)
        iso_lines = [lines_and_glow(iso[i], bgi, region)[0] if start <= i < len(iso) else [] for i in range(len(per))]
        aligned = alignment(frames, iso_lines)
        pre = np.stack([lum(f.astype(np.float64)) for f in iso[:max(2, start)]])
        restless = box(((pre.max(0) - pre.min(0)) > RESTLESS).astype(np.float64), 3) > 0
        def recent_mask(i):
            m = np.zeros((H, W), bool)
            for j in range(max(0, i - FADING), i):
                for ys, xs in iso_lines[j]:
                    m[ys, xs] = True
            return box(m.astype(np.float64), 4) > 0
        faint = [faint_lines(iso[i], bgi, region, iso_lines[i], restless, recent_mask(i)) if start <= i < len(iso) else []
                 for i in range(len(per))]
        per = [([(ys, xs, None) for ys, xs in iso_lines[i]] + faint[i], per[i][1]) for i in range(len(per))]
    else:
        per = [([(ys, xs, None) for ys, xs in lines], glow) for lines, glow in per]
    if ground is None:
        h = region[3] - region[1]
        # strikes from the sky only: lines that start in the top 30% of the view (a bolt fades out at its top) and span half of it (joined-up static
        # arcs can be tall too, and gave Static Flare a bogus ground line at the bottom of the view)
        feet = [int(ys.max()) for lines, _ in per for ys, xs, b in lines if b is None and ys.min() <= region[1] + 0.3 * h and ys.max() - ys.min() > 0.5 * h]
        if len(feet) < 3:
            feet = []
        ground = int(np.median(feet)) if feet else None
    out = []
    for i, (lines, glow) in enumerate(per):
        for ys, xs, b in lines:
            pts = ends(ys, xs)
            low_y, low_x = pts[0]
            # a faint line (b = its brightness) needs light in proportion; a normal one the calibrated bars
            imp = IMPACT_MIN if b is None else min(IMPACT_MIN, FADE_RATIO * b)
            spk = SPARK_MIN if b is None else min(SPARK_MIN, FADE_RATIO * b)
            v = {'x': low_x, 'y': low_y, 'pixels': int(len(ys))}
            if b is not None:
                v.update({'faint': True, 'brightness': round(b), 'light_needed': round(imp)})
            if ground is not None and low_y >= ground - GROUND_TOL and not impact(glow, low_y, low_x, imp):
                out.append(dict(v, frame=i, kind='bare-ground'))
            elif b is not None and glow[0][low_y:low_y + FAINT_BELOW, max(0, low_x - 12):low_x + 13].max() >= imp:
                pass    # a faint piece of a bolt whose lit foot is below it (its fainter lower part was not found)
            elif (not at_edge(ys, xs, region) and glow[3][ys, xs].mean() < BRIGHT_BACKDROP
                  and spark_near(glow[1], ys, xs) < spk and spark_near(glow[2], ys, xs) < WHITE_CORE
                  and spark_near(glow[0], ys, xs) < imp):
                out.append(dict(v, frame=i, kind='lone-line'))
    return ground, out, aligned


def effect_file(project, anim_id, subs):
    """(the .efkefc an animation plays, or its --sub replacement; its folder under effects/, e.g. Edited/Sparkforge)"""
    anims = json.loads((Path(project) / 'data/Animations.json').read_text(encoding='utf-8'))
    name = anims[int(anim_id)]['effectName']
    path = Path(project) / 'effects' / (name + '.efkefc')
    folder = name.rsplit('/', 1)[0] if '/' in name else ''
    for sub in subs:
        k, v = sub.split('=', 1)
        if k == path.name:
            return Path(v), folder, path.name
    return path, folder, path.name


def isolated_copy(src, out_dir, keep, served_name=None):
    """Build a copy of `src` with every drawn node hidden except those whose texture matches `keep`. Returns
    (copy path, [kept node names]), or (None, []) if no node matches."""
    import re
    dump = subprocess.run(['dotnet', str(TOOL), 'dump', str(src)], capture_output=True, text=True,
                          encoding='utf-8', errors='replace').stdout
    kept, hide, hide_mat = [], [], []
    for sec in re.split(r'^### ', dump, flags=re.M)[1:]:
        head, _, body = sec.partition('\n')
        path, _, name = head.partition(' ')
        if path.startswith('procedural') or path == 'root':
            continue
        vals = dict(l.split(' = ', 1) for l in body.splitlines() if ' = ' in l)
        if vals.get('DrawingValues/Type', 'None') == 'None' or vals.get('IsRendered') == 'false':
            continue
        if re.search(keep, vals.get('RendererCommonValues/ColorTexture', '')):
            kept.append('%s %s' % (path, name))
        elif vals.get('RendererCommonValues/Material') == 'File':
            hide_mat.append(path)
        else:
            hide.append(path)
    if not kept:
        return None, []
    out_dir.mkdir(parents=True, exist_ok=True)
    # absolute paths: the spec's relative paths resolve against the spec's own folder (a relative --project or --sub
    # made every copy fail, 2026-10-07)
    src, out_dir = Path(src).resolve(), Path(out_dir).resolve()
    blank = out_dir / 'IsolateBlank.png'
    Image.new('RGBA', (4, 4), (0, 0, 0, 0)).save(blank)
    edits = [{'node': p, 'set': {'RendererCommonValues/ColorTexture': str(blank)}} for p in hide]
    # material-file nodes: switched to the default material with the blank texture (hiding them shifted the random
    # draws like any hidden node: Levin Barrage, Static Flare, Chain Currents stayed misaligned)
    edits += [{'node': p, 'set': {'RendererCommonValues/Material': 'Default', 'RendererCommonValues/ColorTexture': str(blank)}}
              for p in hide_mat]
    spec = out_dir / (src.stem + '.isolate.edit.json')
    spec.write_text(json.dumps({'//': 'lightning_audit --isolate: only the drawn bolts shown', 'source': str(src),
                                'name': src.stem + '-isolated',
                                'edits': edits}, indent=1))
    # named as the game asks for it (a --sub source may have another name; served by folder, a different name fell
    # back to the installed effect, whose textures the copy's folder lacks)
    copy = out_dir / (served_name or src.name)
    r = subprocess.run(['dotnet', str(TOOL), 'edit', str(spec), str(copy)], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if r.returncode or 'VERIFY OK' not in r.stdout:
        raise RuntimeError('isolated copy of %s failed:\n%s' % (src, r.stdout[-1500:]))
    return copy, kept


def sheet(frames, violations, path, region):
    bad = sorted({v['frame'] for v in violations})
    if not bad:
        return None
    x0, y0, x1, y1 = region
    tw, th = (x1 - x0) // 2, (y1 - y0) // 2
    cols = min(8, len(bad))
    rows = (len(bad) + cols - 1) // cols
    s = Image.new('RGB', (tw * cols, (th + 12) * rows), (0, 0, 0))
    d = ImageDraw.Draw(s)
    for k, f in enumerate(bad):
        r, c = divmod(k, cols)
        im = Image.fromarray(frames[f]).crop(region)
        di = ImageDraw.Draw(im)
        for v in violations:
            if v['frame'] == f:
                cx, cy = v['x'] - x0, v['y'] - y0
                di.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), outline=(255, 40, 40) if v['kind'] == 'bare-ground' else (255, 160, 0), width=3)
        s.paste(im.resize((tw, th)), (c * tw, r * (th + 12) + 12))
        d.text((c * tw + 3, r * (th + 12)), 'f%d' % f, fill=(255, 240, 120))
    s.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', required=True)
    ap.add_argument('--ids', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--troop', default='4')
    ap.add_argument('--sub', action='append', default=[])
    ap.add_argument('--port', default='8838')
    ap.add_argument('--frames', help='use the capture-<id> folders already in this directory instead of capturing')
    ap.add_argument('--ground', type=int)
    ap.add_argument('--region', default='0,0,640,270', help='x0,y0,x1,y1 of the battle view (default: above the status window)')
    ap.add_argument('--isolate', action='store_true', help='judge only our drawn bolts (see above); plays each effect twice')
    ap.add_argument('--keep', default=KEEP, help='--isolate: regex on the texture file names of the nodes judged as lightning')
    ap.add_argument('--keep-frames', action='store_true',
                    help='keep the raw captured frames (for GIFs, measurements or a later --frames run); by default each '
                         'effect\'s frames are deleted once it is judged. Frames read with --frames are never deleted')
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    region = tuple(int(v) for v in args.region.split(','))
    src = Path(args.frames).resolve() if args.frames else out
    iso_src, kept = out / 'isolated', {}
    report, total, freed = {}, 0, 0
    # one effect at a time: capture, judge, drop its frames (capturing everything first held every effect's frames
    # on disk at once)
    for i in args.ids.split(','):
        captured = []
        if not args.frames:
            cmd = ['node', str(ROOT / 'tools/ingame/run.js'), '--project', args.project, '--out', str(out), '--ids', i,
                   '--troop', args.troop, '--mode', 'capture', '--step', '1', '--port', args.port]
            for s in args.sub:
                cmd += ['--sub', s]
            r = subprocess.run(cmd, capture_output=True, text=True)
            print('\n'.join(line for line in r.stdout.splitlines() if line.startswith('capture-')))
            if r.returncode:
                print(r.stdout.strip(), r.stderr[-2000:])
                sys.exit(2)
            captured.append(out / ('capture-' + i))
        if args.isolate:
            efk, folder, served = effect_file(args.project, i, args.sub)
            copy, names = isolated_copy(efk, out / 'isolated' / ('effect-' + i), args.keep, served)
            kept[i] = names
            if copy and not (args.frames and (iso_src / ('capture-' + i)).exists()):
                cmd = ['node', str(ROOT / 'tools/ingame/run.js'), '--project', args.project, '--out', str(iso_src), '--ids', i,
                       '--troop', args.troop, '--mode', 'capture', '--step', '1', '--port', args.port]
                for sub in args.sub:
                    if sub.split('=', 1)[0] != copy.name:
                        cmd += ['--sub', sub]
                # the copy's folder carries every resource it uses (edit copies them), the blank texture included:
                # serve that folder in place of the effect's own (with --sub alone the blank texture was missing)
                cmd += ['--map', '%s=%s' % (folder, copy.parent)] if folder else ['--sub', '%s=%s' % (copy.name, copy)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode:
                    print(r.stdout.strip(), r.stderr[-2000:])
                    sys.exit(2)
                captured.append(iso_src / ('capture-' + i))
        try:
            total += judge(args, i, src, iso_src, kept, region, out, report)
        finally:
            if not args.keep_frames:
                freed += sum(drop_frames(d) for d in captured)
    (out / 'audit.json').write_text(json.dumps(report, indent=1))
    if freed:
        print('raw frames deleted after judging: %.0f MB (--keep-frames keeps them)' % (freed / 1e6))
    sys.exit(1 if total else 0)


def judge(args, i, src, iso_src, kept, region, out, report):
    """Judge animation `i` from its captures: adds its report entry, prints its line, returns its violation count."""
    frames = load(src / ('capture-' + i))
    if not frames:
        print('no frames for %s in %s' % (i, src))
        sys.exit(2)
    iso = None
    if args.isolate:
        if not kept.get(i):
            print('%s: no node draws a bolt texture (--keep %s): nothing judged' % (i, args.keep))
            report[i] = {'frames': len(frames), 'isolated': True, 'judged_nodes': [], 'violations': [], 'counts': {}}
            return 0
        iso = load(iso_src / ('capture-' + i))
    ground, v, aligned = audit(frames, args.ground, region, iso)
    png = sheet(frames, v, out / ('audit-%s.png' % i), region)
    kinds = {k: sum(1 for x in v if x['kind'] == k) for k in ('bare-ground', 'lone-line')}
    if args.isolate:
        kinds['of which faint'] = sum(1 for x in v if x.get('faint'))
    report[i] = {'frames': len(frames), 'ground_y': ground, 'violations': v, 'counts': kinds, 'sheet': str(png) if png else None}
    if args.isolate:
        report[i].update({'isolated': True, 'judged_nodes': kept[i], 'aligned': aligned})
    note = '' if ground is not None else ' (bare-ground NOT checked: no ground line found; pass --ground Y)'
    report[i]['ground_checked'] = ground is not None
    ok = aligned is None or aligned[0] - aligned[1] >= ALIGN_MARGIN or aligned[0] >= ALIGN_HIGH
    iso_note = '' if not args.isolate else ' [isolated: %d bolt nodes judged, aligned %s]' % (
        len(kept[i]), 'n/a' if aligned is None else '%d%% vs %d%% shifted 3 frames' % (round(100 * aligned[0]), round(100 * aligned[1])))
    verdict = 'PASS'
    if args.isolate and aligned is None:
        verdict = 'NOTHING JUDGED (no bolt line found in the isolated play: too faint or thin for the line finder)'
        report[i]['nothing_judged'] = True
    print('%s: %d frames, ground y %s, %s%s%s%s' % (i, len(frames), ground,
          verdict if not v else 'FAIL %s in frames %s' % (kinds, sorted({x['frame'] for x in v})), note, iso_note,
          '' if not png else '  -> ' + str(png)))
    if args.isolate and not ok:
        print('   WARNING: the isolated play does not line up with the full one: treat this result as unproven')
        report[i]['aligned_ok'] = False
    return len(v)


if __name__ == '__main__':
    main()
