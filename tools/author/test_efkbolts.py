#!/usr/bin/env python3
"""Tests for efkbolts.py: the pure helpers on generated textures, then a real kit build and an effect that copies
its parts, both read back with `dump`.

    python tools/author/test_efkbolts.py --work out/tests/bolts [--tool <Niagara2Efk.dll>]
"""
import argparse, json, math, shutil, subprocess, sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import efkauthor as ea  # noqa: E402
import efkbolts as eb  # noqa: E402

passed = failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print('PASS ' + name)
    else:
        failed += 1
        print('FAIL %s %s' % (name, detail))


def bolt_texture(path, x, colour=(255, 220, 120)):
    """A 128x256 tiling 'bolt' on black (opaque, like the user's textures): a bright zigzag that crosses the top and
    bottom edges at column x and wanders up to 30 px right of it in between, as a drawn bolt does."""
    im = Image.new('RGBA', (128, 256), (0, 0, 0, 255))
    d = ImageDraw.Draw(im)
    pts = [(x + dx, i * 32) for i, dx in enumerate((0, 10, 25, 30, 18, 30, 20, 8, 0))]
    d.line(pts, fill=colour + (255,), width=5)
    im.save(path)
    return path


def pure(work):
    a = bolt_texture(work / 'A.png', 83)
    b = bolt_texture(work / 'B.png', 32)
    check('line_centre: where the line crosses the tile edges, not its wandering middle', abs(eb.line_centre(a) - 83.5 / 128) < 0.03, eb.line_centre(a))
    for name, t in (('right of centre', a), ('left of centre', b)):
        ca, f = eb.centred(t, work / 'centred')
        check('centred (%s): the line is in the middle of the copy' % name, abs(eb.line_centre(ca) - 0.5) < 0.02, eb.line_centre(ca))
        src = np.asarray(Image.open(t).convert('L'), float)
        dst = np.asarray(Image.open(ca).convert('L'), float)
        check('centred (%s): every lit pixel kept (padding, no wrap or crop)' % name, abs(src.sum() - dst.sum()) < 1, (src.sum(), dst.sum()))
        check('centred (%s): factor = new width / old width' % name, abs(f - Image.open(ca).size[0] / 128) < 1e-9 and f > 1, f)
    out = eb.remap(a, work / 'A_violet.png', [(0, (0, 0, 0)), (0.5, (120, 70, 230)), (1, (255, 255, 255))])
    im = Image.open(out).convert('RGBA')
    check('remap: black stays black, alpha kept', im.getpixel((2, 2)) == (0, 0, 0, 255), im.getpixel((2, 2)))
    lit = max((im.getpixel((x, 128)) for x in range(128)), key=lambda p: sum(p[:3]))
    check('remap: the brightest pixel goes to the top of the ramp', lit[:3] == (255, 255, 255), lit)
    try:
        eb.rng([255, 100, 50, 255], [255, 200, 90, 255])
        check('rng refuses a two-channel range', False)
    except ValueError:
        check('rng refuses a two-channel range', True)
    check('rng accepts one channel', eb.rng([255, 100, 50, 255], [255, 200, 50, 255])['max'][1] == 200)
    cleaning(work)
    m = eb.bolt_model(2)
    check('bolt_model: Plane strand, two tiles, shifted per seed',
          m['Ribbon/CrossSection'] == 'Plane' and m['UV/UVSize'] == [1, 2] and abs(m['UV/UVPosition'][1] - 0.37) < 1e-9, m)
    check('bolt_model: noise slow across, fast up', m['ShapeNoise/CurlNoiseFrequency'][0] < m['ShapeNoise/CurlNoiseFrequency'][1])
    solid = eb.bolt_model(2, fade_top=False)
    check('bolt_model: the top fades out by default, fade_top=False keeps it solid',
          m.get('VertexColor/ColorUpperCenter') == [255, 255, 255, 0] and 'VertexColor/ColorUpperCenter' not in solid
          and {k: v for k, v in m.items() if k != 'VertexColor/ColorUpperCenter'} == solid,
          {k: v for k, v in solid.items() if k.startswith('VertexColor')})
    kit_path = str(work / 'nokit' / 'kit.efkefc')   # no kit there: default factors; generated files stay in the work dir
    ops = eb.strikes(kit_path, first_root=5, first=8, flash=[1, 2, 3, 4])
    adds = [o for o in ops if 'add' in o]
    check('strikes: three bolts added at the root, staggered by 3',
          len(adds) == 3 and [o['set']['CommonValues/GenerationTimeOffset'] for o in adds] == [8, 11, 14], adds)
    check('strikes: flash colour addresses child 2 of each bolt',
          [o['node'] for o in ops if 'add' not in o] == ['5/2', '6/2', '7/2'], [o['node'] for o in ops])
    sub = eb.strikes(kit_path, first_root=2, parent='3', flash=[1, 2, 3, 4])
    check('strikes under a spawner: added to it, children addressed below it',
          [o['node'] for o in sub if 'add' in o] == ['3'] * 3 and [o['node'] for o in sub if 'add' not in o] == ['3/2/2', '3/3/2', '3/4/2'],
          [o['node'] for o in sub])
    tr = eb.strikes(kit_path, first_root=5, first=9, travel=3)
    bolt0 = [o for o in tr if 'add' in o][0]
    ys = {k['curve']: k['points'] for k in bolt0.get('keys', [])}
    rv = 'AdvancedRendererCommonValuesValues/AlphaTextureParam/'
    check('strikes travel: full size from the first frame (no squash), revealed top-down by an alpha flipbook held at its end',
          'ScalingValues/Type' not in bolt0['set'] and bolt0['set'].get('RendererCommonValues/FadeInType') == 'None'
          and bolt0['set'].get(rv + 'UV') == 'Animation' and bolt0['set'].get(rv + 'UVAnimation/FrameCountX') == 4
          and bolt0['set'].get(rv + 'UVAnimation/LoopType') == 'Once', bolt0['set'])
    cells = np.asarray(Image.open(bolt0['set'][rv + 'Texture']))[..., 3]
    check('reveal_texture: cell k lit down to (k+1)/4 of the strand, the last cell all of it',
          cells[30, 0] == 255 and cells[100, 0] == 0 and cells[100, 4] == 255 and cells[250, 12] == 255 and cells[250, 8] == 0, cells[::32, ::4])
    fl = [o for o in tr if o.get('node') == '5/2']
    check('strikes travel: the flash keeps its own size (NotBind scale, x the strike scale)',
          fl and fl[-1]['set'].get('CommonValues/ScaleEffectType') == 'NotBind', fl)
    hd = [o for o in tr if o.get('node') == '5' and o.get('add', {}).get('name') == 'Strike Head']
    hf = [o for o in tr if o.get('node') == '5' and o.get('add', {}).get('name') == 'Strike Head Faint']
    check('strikes travel: a faint tip glow down the first segments, a bright spark on the last, the full flash on landing',
          fl and fl[-1]['set'].get('CommonValues/GenerationTimeOffset') == 3 and len(hd) == 1 and len(hf) == 1
          and hd[0]['set']['CommonValues/GenerationTimeOffset'] == 2 and hd[0]['set']['CommonValues/Life'] == 2
          and hf[0]['set']['CommonValues/Life'] == 2 and hf[0]['set']['DrawingValues/ColorAll/Fixed'][3] == eb.HEAD_FAINT
          and hd[0]['set']['DrawingValues/ColorAll/Fixed'][3] == 255, (fl, hd, hf))
    hk = {k['curve'].rsplit('/', 1)[1]: k['points'] for k in hd[0].get('keys', [])} if hd else {}
    fk = {k['curve'].rsplit('/', 1)[1]: k['points'] for k in hf[0].get('keys', [])} if hf else {}
    ys = [v for _, v in fk.get('Y', [])] + [v for _, v in hk.get('Y', [])]
    check('strikes travel: the tip glow rides the reveal down the strand (faint, then bright), ending on the drawn foot',
          len(ys) == 4 and ys[0] > ys[1] > ys[2] > ys[3] and [hk[a][-1][1] for a in 'XYZ'] == eb.foot_point(0, eb.SCALE, eb.HEAD_LIFT), (fk, hk))
    tf = eb.strikes(kit_path, first_root=5, first=9, travel=3, flash_scale=3.0)
    sized = [o for o in tf if o.get('node') == '5/2' and 'ScalingValues/Fixed/Scale' in o['set']]
    check('strikes travel + flash_scale: the flash keeps the strike scale (no later unscaled override)',
          sized and sized[-1]['set']['ScalingValues/Fixed/Scale'][0] == round(3.0 * eb.kit_factors(kit_path)['bolts'][0] * eb.SCALE, 4), sized)
    check('strikes travel: forks wait for the landing', any(o.get('node') == '5/0' and o['set'].get('CommonValues/GenerationTimeOffset') == 3 for o in tr))
    check('strikes without travel: no curves', not any('keys' in o for o in eb.strikes(kit_path, first_root=5)))
    # the shape-noise port: Perlin is 0 on lattice points; bolt 0's foot 1.56 units right of its origin (in battle
    # the unpinned ThunderFall calm foot stood 13-25 px right of the explosion, pinned 0-2 px, 2026-10-07)
    check('shape noise: Perlin is 0 on lattice points', all(abs(eb._CURL.noise(x, y, z)) < 1e-12 for x, y, z in ((0, 0, 0), (3, 1, 7), (-2, 5, 1))))
    f0 = eb.bolt_foot(0, lean=0)
    check('bolt_foot: bolt 0 lands 1.56 units right of its origin (the noise moves the foot)',
          abs(f0[0] - 1.56) < 0.01 and abs(f0[1]) < 1e-9 and abs(f0[2] + 0.152) < 0.01, f0)
    fl11 = eb.bolt_foot(0, lean=-11)
    check('bolt_foot: a lean turns the offset (same length)', abs(math.hypot(*fl11) - math.hypot(*f0)) < 1e-9 and fl11[1] < 0, fl11)
    pt = eb.strikes(kit_path, first_root=5, first=9, travel=3, lean=(-11, -11), pin_feet=True)
    pb = [o for o in pt if 'add' in o][0]['set']
    check('strikes pin_feet + travel: the bolt stands moved by minus its foot offset (the reveal does the travelling)',
          pb['LocationValues/Fixed/Location'] == [round(-c, 4) for c in fl11], pb.get('LocationValues/Fixed/Location'))
    pn = [o for o in eb.strikes(kit_path, first_root=5, lean=None, pin_feet=True) if 'add' in o]
    check('strikes pin_feet without travel: a fixed location per bolt',
          [o['set']['LocationValues/Fixed/Location'] for o in pn] == [[round(-c, 4) for c in eb.bolt_foot(i)] for i in range(3)], pn)
    check('strikes pin_feet: no random Y turn (it swings the foot off the pin)',
          all(o['set']['RotationValues/PVA/Rotation/Y'] == 0 for o in pn), pn)
    wk = work / 'widekit' / 'BoltKit.efkefc'
    wk.parent.mkdir(parents=True, exist_ok=True)
    wk.with_suffix('.factors.json').write_text(json.dumps({'bolts': [2.5] * 3, 'forks': [2.5] * 2, 'flash': [2.25] * 3, 'burst': True}))
    wide = eb.strikes(wk, first_root=5, travel=3, pin_feet=True)
    loc = {o['node']: o['set'] for o in wide if 'add' not in o and ('LocationValues/Fixed/Location' in o['set'] or 'LocationValues/PVA/Location/X' in o['set'])}
    fu = eb.foot_point(0, 1.0)
    check('strikes: the burst card sits on the drawn foot (bolt units), lifted as before',
          loc.get('5/4', {}).get('LocationValues/Fixed/Location') == [fu[0], eb.BURST_LIFT, fu[2]], loc.get('5/4'))
    check('strikes, widened kit: its single flash and the sparks on the foot, not the bolt origin',
          loc.get('5/2', {}).get('LocationValues/Fixed/Location') == eb.foot_point(0, eb.SCALE)
          and loc.get('5/3', {}).get('LocationValues/PVA/Location/X') == [round(fu[0] - 0.5, 4), round(fu[0] + 0.5, 4)], (loc.get('5/2'), loc.get('5/3')))
    # foot-stable strands, held strike, soft foot, slower strikes (user review, 2026-10-07)
    check('FOOT_SEEDS: every strand lands within 0.15 units of its origin, so a random lean keeps the foot put',
          all(max(abs(c) for c in eb.bolt_foot(i, seeds=eb.FOOT_SEEDS)) < 0.15 for i in range(3)),
          [eb.bolt_foot(i, seeds=eb.FOOT_SEEDS) for i in range(3)])
    sk = work / 'seedkit' / 'BoltKit.efkefc'
    sk.parent.mkdir(parents=True, exist_ok=True)
    sk.with_suffix('.factors.json').write_text(json.dumps({'bolts': [1.3] * 3, 'forks': [1.0] * 2, 'flash': [1.3] * 3,
                                                           'burst': False, 'seeds': list(eb.FOOT_SEEDS)}))
    held = eb.held_strike(sk, 5, 9, life=42, fade=14, restrikes=(15, 12, 2), restrike_from=1)
    main = [o for o in held if o.get('add') and o['node'] == 'root']
    check('held_strike: one bolt, its random lean and turn kept (foot-stable strand), the drawing still',
          len(main) == 1 and main[0]['set']['RotationValues/PVA/Rotation/Z'] == [-16, -6]
          and main[0]['set']['RotationValues/PVA/Rotation/Y'] == [-20, 20] and main[0]['set']['RendererCommonValues/UVScroll/Speed/Y'] == 0
          and main[0]['set']['CommonValues/Life'] == 42, main)
    rs = [o for o in held if o.get('add', {}).get('name') == 'Re-strike']
    check('held_strike: re-strikes are the bolt\'s child on ITS strand (overlay at any lean), drawing from strand 1',
          len(rs) == 1 and rs[0]['node'] == '5' and rs[0]['add']['from'] == '1' and rs[0]['set']['DrawingValues/Model/Reference'] == 'Bolt0'
          and rs[0]['set']['CommonValues/GenerationTimeOffset'] == 15, rs)
    fl = [o for o in held if o['node'] == '5/2' and 'CommonValues/Life' in o['set']]
    check('held_strike: the foot flash outlasts the bolt', fl and fl[-1]['set']['CommonValues/Life'] == 42 - 3 + 6, fl)
    fp = [o for o in held if o.get('add', {}).get('name') == 'Foot Point']
    check('held_strike: a foot point on the drawn foot', fp and fp[0]['set']['LocationValues/Fixed/Location'] == eb.foot_point(0, eb.SCALE, 0.4, eb.FOOT_SEEDS), fp)
    ft = eb.foot_fade_texture(work / 'FootFade.png', 0.04)
    alpha = np.asarray(Image.open(ft))[..., 3][:, 0]
    check('foot_fade_texture: opaque, then fading to 0 over the last 4%', alpha[:240].min() == 255 and alpha[-1] < 10 and 0 < alpha[250] < 255, alpha[-12:])
    sf = eb.soft_foot(ft)
    check('soft_foot: the ramp once along the strand (UV 0..2 -> 0..1), clamped',
          sf['AdvancedRendererCommonValuesValues/AlphaTextureParam/Wrap'] == 'Clamp'
          and sf['AdvancedRendererCommonValuesValues/AlphaTextureParam/UVFixed/Size'] == [4, 128], sf)
    slow = [o for o in eb.strikes(kit_path, first_root=5, life=10, flow=-12) if o.get('add')]
    sflash = [o for o in eb.strikes(kit_path, first_root=5, life=10, flow=-12) if o['node'] == '5/2' and 'CommonValues/Life' in o['set']]
    check('strikes life/flow: longer, slower strikes; the flash still outlasts each by 3 frames',
          all(o['set']['CommonValues/Life'] == 10 and o['set']['RendererCommonValues/UVScroll/Speed/Y'] == -12 for o in slow)
          and sflash and sflash[0]['set']['CommonValues/Life'] == 13, (slow, sflash))
    arc = eb.arcs(kit_path, '3/0', 'Arc', 1, 0, 6, up=True)[0]
    check('arcs: up keeps them out of the floor', arc['set']['RotationValues/PVA/Rotation/Z'] == [-80, 80], arc)
    return a, b


def cleaning(work):
    """haze_cut, seamless_y, soft_mask on generated textures."""
    rs = np.random.RandomState(1)
    fog = np.full((64, 64, 3), 30.0) + rs.randint(0, 20, (64, 64, 1))
    fog[30:34, :] = 250                                                     # a bright line across a grey fog
    Image.fromarray(fog.astype(np.uint8)).save(work / 'fog.png')
    cut = np.asarray(Image.open(eb.haze_cut(work / 'fog.png', work / 'fog_cut.png', 60)).convert('RGBA'), float)
    check('haze_cut: the fog under the floor goes black', cut[:30, :, :3].max() == 0, cut[:30, :, :3].max())
    check('haze_cut: the line stays bright, alpha kept', cut[31, 5, 0] > 240 and cut[..., 3].min() == 255, cut[31, 5])

    # two ramps stacked: smooth everywhere except a jump at the wrap (row 0) and one across the middle (row 64),
    # like Lightningrep_A (tile join + an older join in its middle)
    ramp = np.repeat((np.concatenate([np.linspace(40, 200, 64), np.linspace(40, 200, 64)]))[:, None], 32, 1)
    Image.fromarray(ramp.astype(np.uint8)).save(work / 'joins.png')

    def jump(p, r):
        a = np.asarray(Image.open(p).convert('L'), float)
        return np.abs(a[r] - a[r - 1]).mean()
    healed = eb.seamless_y(work / 'joins.png', work / 'joins_ok.png', seams=(0, 64), band=12, shift=32)
    before = (jump(work / 'joins.png', 0), jump(work / 'joins.png', 64))
    after = (jump(healed, 0), jump(healed, 64))
    check('seamless_y: both joins healed', max(after) < 0.25 * min(before), (before, after))
    src = np.asarray(Image.open(work / 'joins.png').convert('L'), float)
    out = np.asarray(Image.open(healed).convert('L'), float)
    check('seamless_y: rows away from the joins untouched', np.abs(src[20:44] - out[20:44]).max() == 0)
    try:
        eb.seamless_y(work / 'joins.png', work / 'bad.png', seams=(0, 64), band=12, shift=64)
        check('seamless_y refuses a shift that lands one join on the other', False)
    except ValueError:
        check('seamless_y refuses a shift that lands one join on the other', True)

    Image.new('RGBA', (64, 64), (200, 200, 200, 255)).save(work / 'flat.png')
    m = np.asarray(Image.open(eb.soft_mask(work / 'flat.png', work / 'flat_card.png')).convert('RGBA'), float)
    check('soft_mask (opaque): corners black, centre kept, alpha untouched',
          m[0, 0, :3].max() == 0 and m[32, 32, 0] == 200 and m[..., 3].min() == 255, (m[0, 0], m[32, 32]))
    t = Image.new('RGBA', (64, 64), (200, 200, 200, 128))
    t.save(work / 'soft.png')
    m = np.asarray(Image.open(eb.soft_mask(work / 'soft.png', work / 'soft_card.png')).convert('RGBA'), float)
    check('soft_mask (transparent): alpha faded, colour kept', m[0, 0, 3] == 0 and m[32, 32, 3] == 128 and m[0, 0, 0] == 200, (m[0, 0], m[32, 32]))


def dump_blocks(tool, path):
    text = subprocess.run(['dotnet', str(tool), 'dump', str(path)], capture_output=True, text=True).stdout
    blocks = {}
    for block in text.split('### ')[1:]:
        head, _, body = block.partition('\n')
        vals = {}
        for line in body.splitlines():
            if ' = ' in line:
                k, v = line.split(' = ', 1)
                vals.setdefault(k, v)
        key = ' '.join(head.split(' ')[:2]) if head.startswith('procedural') else head.split(' ')[0]
        blocks[key] = (head, vals)
    return blocks


def built(work, tool, a, b):
    kit = eb.make_kit(work / 'kit', [a, b, a], [b, a], tool=tool)
    check('kit builds', kit.exists())
    k = dump_blocks(tool, kit)
    flash = k.get('1/2', ('', {}))
    check('kit: Bolt 1 child 2 is the strike flash', 'Strike Flash' in flash[0], flash[0])
    v = flash[1]
    check('kit: the flash spawns on the bolt model\'s vertices',
          v.get('GenerationLocationValues/Type') == 'Model' and v.get('GenerationLocationValues/Model/Type') == 'Vertex'
          and 'Bolt1' in v.get('GenerationLocationValues/Model/Reference', ''), {x: y for x, y in v.items() if 'Generation' in x})
    check('kit: three flashes, outliving the bolt (life 10 vs the bolt 7: a bolt fades before its light)', v.get('CommonValues/MaxGeneration') == '3' and v.get('CommonValues/Life') == str(eb.FLASH_LIFE) and eb.FLASH_LIFE > eb.BOLT_LIFE,
          (v.get('CommonValues/MaxGeneration'), v.get('CommonValues/Life')))
    fork = k.get('0/0', ('', {}))[1]
    check('kit: forks are short', fork.get('ScalingValues/Fixed/Scale') == '(2.5, 0.4, 0.4)', fork.get('ScalingValues/Fixed/Scale'))
    bolt = k.get('1', ('', {}))[1]
    check('kit: no sideways UV shift (a shift wraps branches round the strip edges)', bolt.get('RendererCommonValues/UVScroll/Start/X') == '0',
          bolt.get('RendererCommonValues/UVScroll/Start/X'))
    widths = [k.get('procedural %d' % i, ('', {}))[1].get('Ribbon/RibbonScales') for i in range(3)]
    check('kit: each bolt strand widened by its texture padding (bolt B wider than A)', widths[1] != widths[0], widths)
    check('kit: arc has its base spark', 'Base Spark' in k.get('3/0', ('', {}))[0])
    check('kit without a burst: no child 4', '0/4' not in k, sorted(x for x in k if x.startswith('0/')))
    kw = eb.make_kit(work / 'kit-wide', [a, b, a], [b, a], widen=2.0, tool=tool)
    base, wide = eb.kit_factors(work / 'kit' / 'BoltKit.efkefc'), eb.kit_factors(kw)
    check('kit widen=2: every strand factor doubled',
          all(abs(w - 2 * f) < 1e-9 for f, w in zip(base['bolts'] + base['forks'], wide['bolts'] + wide['forks'])), (base, wide))
    check('kit: plain flash factors = strand factors; widened = one foot flash of WIDE_FLASH x the plain factor',
          base['flash'] == base['bolts'] and all(abs(w - eb.WIDE_FLASH * f) < 1e-9 for f, w in zip(base['bolts'], wide['flash'])), (base, wide))
    fw = dump_blocks(tool, kw).get('0/2', ('', {}))[1]
    check('kit widen=2: the flash is one glow at the foot, not on the strip edge vertices',
          fw.get('CommonValues/MaxGeneration') == '1' and fw.get('GenerationLocationValues/Type') != 'Model',
          (fw.get('CommonValues/MaxGeneration'), fw.get('GenerationLocationValues/Type')))
    w0 = dump_blocks(tool, kw).get('procedural 0', ('', {}))[1].get('Ribbon/RibbonScales')
    check('kit widen=2: the bolt strand is twice as wide', w0 != widths[0], (widths[0], w0))
    card = eb.soft_mask(a, work / 'burst_card.png')
    kb = dump_blocks(tool, eb.make_kit(work / 'kit-burst', [a, b, a], [b, a], burst=card, tool=tool))
    bursts = [kb.get('%d/4' % i, ('', {})) for i in range(3)]
    check('kit with a burst: child 4 of every bolt is the strike burst', all('Strike Burst' in n for n, _ in bursts), [n for n, _ in bursts])
    v = bursts[0][1]
    check('kit burst: a card that turns (RotatedBillboard, random Z), drawn additively',
          v.get('DrawingValues/Sprite/Billboard') == 'RotatedBillboard' and v.get('RendererCommonValues/AlphaBlend') == 'Add'
          and v.get('RotationValues/PVA/Rotation/Z', '').startswith('['), (v.get('DrawingValues/Sprite/Billboard'), v.get('RotationValues/PVA/Rotation/Z')))

    # an effect with two root parts of its own; the bolts strike onto it, arcs hang on its second part
    blank = ea.make_blank(work / 'target_blank.efkefc', 60, tool)
    spec = ea.Spec(blank, 'BoltTarget', end_frame=60)
    spec.node0('Impact', ea.merge(ea.sprite('', 'Add', [255, 255, 255, 255]), ea.once(40)))
    spec.fresh('Spawner', ea.merge(ea.hidden(), ea.emit(4, 5, 10)))
    spec.data['edits'] += eb.strikes(kit, first_root=2, first=6, flash=[200, 180, 255, 120])
    spec.data['edits'] += eb.arcs(kit, '1', 'Static', 1, 0, 6, length=0.8, up=True)
    spec.data['edits'].append(eb.arc_spark('1/0', 6, length=0.8))
    report = spec.build(work / 'BoltTarget.efkefc', tool=tool)
    check('target builds', any(l.startswith('VERIFY OK') for l in report), report)
    check('target: no warnings', not any(l.startswith('WARNING') for l in report), report)
    t = dump_blocks(tool, work / 'BoltTarget.efkefc')
    check('target: bolts at root 2-4 with their flash', 'Bolt 0' in t.get('2', ('', {}))[0] and 'Strike Flash' in t.get('4/2', ('', {}))[0],
          [t.get(p, ('',))[0] for p in ('2', '4/2')])
    check('target: the flash still references its own bolt model',
          'Bolt2' in t.get('4/2', ('', {}))[1].get('GenerationLocationValues/Model/Reference', ''))
    check('target: the flash colour was set', t.get('3/2', ('', {}))[1].get('DrawingValues/ColorAll/Fixed') == 'rgba(200, 180, 255, 120)',
          t.get('3/2', ('', {}))[1].get('DrawingValues/ColorAll/Fixed'))
    check('target: static arc + spark under the spawner', 'Static' in t.get('1/0', ('', {}))[0] and 'Base Spark' in t.get('1/0/0', ('', {}))[0])


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    ap.add_argument('--tool', default=str(ea.DEFAULT_TOOL))
    args = ap.parse_args()
    work = Path(args.work).resolve()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    a, b = pure(work)
    built(work, Path(args.tool), a, b)
    print('%d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)
