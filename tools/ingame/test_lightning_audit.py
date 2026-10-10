#!/usr/bin/env python3
"""Tests for lightning_audit.py on synthetic frames (no game needed): a zigzag 'bolt' reaching the ground with and
without an impact, a floating 'arc' with and without a spark at its base, short straight streaks, an edge line.

    python tools/ingame/test_lightning_audit.py
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lightning_audit as la  # noqa: E402

passed = failed = 0
GROUND = 200


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print('PASS ' + name)
    else:
        failed += 1
        print('FAIL %s %s' % (name, detail))


def scene(draw_fn, n=12, start=4):
    """n frames of a dim, slightly flickering backdrop; from frame `start` draw_fn(draw) adds the effect."""
    rng = np.random.default_rng(1)
    base = np.full((la.H, la.W, 3), 30, np.uint8)
    frames = []
    for i in range(n):
        f = np.clip(base.astype(int) + rng.integers(-4, 5, base.shape), 0, 255).astype(np.uint8)
        if i >= start:
            im = Image.fromarray(f)
            draw_fn(ImageDraw.Draw(im))
            f = np.array(im)
        frames.append(f)
    return frames


def bolt(d, x=300, top=10):
    pts = [(x + (14 if k % 2 else -14) * (k % 3 != 0), top + k * (GROUND - top) // 8) for k in range(9)]
    d.line(pts, fill=(255, 250, 230), width=3)


def flash(d, x=300, y=GROUND, r=34):
    for k in range(r, 0, -2):
        v = int(255 * (1 - k / r) ** 0.5)
        d.ellipse((x - k, y - k // 2, x + k, y + k // 2), fill=(v, v, int(v * 0.8)))


def arc(d, x=150, y=90):
    d.line([(x, y), (x + 12, y - 15), (x + 4, y - 30), (x + 20, y - 46)], fill=(255, 250, 230), width=3)


def spark(d, x=150, y=90, r=6):
    d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 210))


def kinds(frames, ground=GROUND):
    _, v, _ = la.audit(frames, ground)
    return {k: sum(1 for x in v if x['kind'] == k) for k in ('bare-ground', 'lone-line')}


k = kinds(scene(lambda d: bolt(d)))
check('a bolt reaching the ground with no impact is bare-ground', k['bare-ground'] > 0, k)
k = kinds(scene(lambda d: (bolt(d), flash(d))))
check('the same bolt landing in a flash passes', k['bare-ground'] == 0 and k['lone-line'] == 0, k)
k = kinds(scene(lambda d: arc(d)))
check('a floating arc with no spark is lone-line', k['lone-line'] > 0, k)
k = kinds(scene(lambda d: (arc(d), spark(d))))
check('the same arc starting from a spark passes', k['lone-line'] == 0, k)
k = kinds(scene(lambda d: d.line([(100, 60), (112, 72)], fill=(255, 255, 255), width=2)))
check('a short straight streak is not lightning', k == {'bare-ground': 0, 'lone-line': 0}, k)
k = kinds(scene(lambda d: d.line([(0, 80), (20, 60), (5, 40), (30, 20)], fill=(255, 250, 230), width=3)))
check('an arc running off the edge of the view is not judged lone', k['lone-line'] == 0, k)
bg, start = la.background(scene(lambda d: bolt(d), start=4))
check('the effect is found to start at frame 4', start == 4, start)

# --isolate: lines come from the bolts-only play, light from the full play
full = scene(lambda d: (bolt(d), flash(d)))
mine = scene(lambda d: bolt(d))
_, v, al = la.audit(full, GROUND, iso=mine)
check('isolate: our bolt landing in the flash of the full play passes', not v and al is not None and al[0] > 0.9, (v, al))
_, v, _ = la.audit(full, GROUND, iso=scene(lambda d: None))
check('isolate: a pack line in the full play is not judged when our copy has no lines', not v, v)
_, v, _ = la.audit(scene(lambda d: bolt(d)), GROUND, iso=mine)
check('isolate: our bolt with no light under it in the full play is still bare-ground', any(x['kind'] == 'bare-ground' for x in v), v)


def moving(n=12, start=4):
    """An arc that moves 40 px a frame: a play shifted by frames lands elsewhere."""
    frames = []
    for i in range(n):
        im = Image.new('RGB', (la.W, la.H), (20, 22, 30))
        if i >= start:
            x = 60 + 40 * (i - start)
            ImageDraw.Draw(im).line([(x, 60), (x + 20, 80), (x + 5, 100), (x + 25, 120)], fill=(255, 250, 230), width=3)
        frames.append(np.asarray(im))
    return frames


fm = moving()
bgi, s0 = la.background(fm)
lines = [la.lines_and_glow(f, bgi, (0, 0, la.W, 270))[0] if k >= s0 else [] for k, f in enumerate(fm)]
al = la.alignment(fm, lines)
check('alignment: the same play lines up, and beats itself shifted 3 frames by the margin', al and al[0] - al[1] >= la.ALIGN_MARGIN, al)


def fading(foot_light, n=14, start=4, bright_until=8):
    """A bolt, bright from `start`, then dim (lum ~60) after `bright_until`, over a foot glow of `foot_light` (0..255)
    that stays the same throughout; the full play and the isolated one (bolt only)."""
    full, iso = [], []
    for i in range(n):
        level = 255 if i < bright_until else 70
        def draw(d, with_light):
            pts = [(300 + (14 if k % 2 else -14) * (k % 3 != 0), 10 + k * (GROUND - 10) // 8) for k in range(9)]
            if with_light and foot_light:
                for k in range(34, 0, -2):
                    v = int(foot_light * (1 - k / 34) ** 0.5)
                    d.ellipse((300 - k, GROUND - k // 2, 300 + k, GROUND + k // 2), fill=(v, v, int(v * 0.8)))
            d.line(pts, fill=(level, level, int(level * 0.9)), width=3)
        for out, lit in ((full, True), (iso, False)):
            im = Image.fromarray(np.full((la.H, la.W, 3), 30, np.uint8))
            if i >= start:
                draw(ImageDraw.Draw(im), lit)
            out.append(np.asarray(im))
    return full, iso


f, iso = fading(70)
_, v, _ = la.audit(f, GROUND, iso=iso)
check('fading: a dim bolt left on a dimmer glow fails (faint bare-ground)', any(x.get('faint') and x['kind'] == 'bare-ground' for x in v), v)
f, iso = fading(255)
_, v, _ = la.audit(f, GROUND, iso=iso)
check('fading: the same dim bolt over a bright foot light passes', not [x for x in v if x.get('faint')], v)
f, iso = fading(70, bright_until=4)
_, v, _ = la.audit(f, GROUND, iso=iso)
check('fading: a line that was never bright is not judged as fading', not [x for x in v if x.get('faint')], v)
import tempfile
with tempfile.TemporaryDirectory() as d:
    for n in ('f000.rgba', 'f001.rgba', 'run.json'):
        (Path(d) / n).write_bytes(b'x' * 10)
    freed = la.drop_frames(d)
    check('drop_frames: deletes the raw frames only, reports the bytes', freed == 20 and sorted(q.name for q in Path(d).iterdir()) == ['run.json'], freed)
print('%d passed, %d failed' % (passed, failed))
sys.exit(1 if failed else 0)
