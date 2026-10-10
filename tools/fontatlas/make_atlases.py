"""Letter sheets for the POLISH Quests/Results text effects, from any TTF/OTF.

Writes the pack's four DCP_Mask_* sheets and their _Blur partners under the
original file names, cell order and blank slot, so a copy of the pack's
effects folder shows the new font with no effect edits (README § Letter
sheets from a font). Never mix two fonts in one Texture/ folder: the names
match on purpose.

Matched to the pack's own sheets (out/edits/fontatlas-review-20261001):

  * --blur defaults to 3.0: the pack's _Blur sheets are a Gaussian blur of its
    sharp sheets at radius 3.0, gain 1.0 (error 0.15 grey levels).
  * --fit pack (default) puts the capital top and baseline of the flat letters
    (E, T, I, ...) where the pack's are, 62 and 193 px of a 256 px cell,
    measured from the reference sheets when present. If a font's widest
    glyph plus its blur would leave the cell, every letter shrinks together
    and the manifest records it. --fit cell is the earlier rule (widest glyph
    fills 184 px, ink centred), kept for reproducing older sheets.

Every character the sheets need must be in the font itself: one the font
does not map would draw as its .notdef box (or nothing) and pass for a letter.
The font's Unicode cmap is read with fontTools when it is installed; without
it, each glyph is compared with the glyph drawn for an unmapped code point.
Characters that draw the identical glyph (a placeholder standing in for
letters the font lacks) are refused too. Missing, blank and shared characters
are listed, with their cells, before anything is written, and the command
exits 1. A single letter drawn as some other shape cannot be detected: look
at preview.png.

Requires Pillow. Run with --font <ttf/otf> --out <new directory>.
Existing output directories are refused unless --overwrite is explicitly set.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import argparse
import hashlib
import json
import math
import os
import statistics
import sys

LAYOUTS = {
    'DCP_Mask_UnlockAcceptComplete': list('QUESTMIONLCKDPFA'),
    'DCP_Mask_AbandonDismiss': list('QUESTMIONBRADPF!'),
    'DCP_Mask_VictoryDefeat': list('VICTORYDEFAWNLS!'),
    'DCP_Mask_SuccessFailure': ['S','U','C','E','F','A','I','L','R','G','T','M','Z','N','!',''],
}
# Where the pack demos live; --reference-root overrides it. The sheets are
# read only, for the occupancy check and the --fit pack metrics.
REFERENCE_ROOT = Path(os.environ.get('EFFEKSEER_TOOL_DIR', 'Effekseer170eWin'))
REFERENCE_DIRS = {
    'DCP_Mask_UnlockAcceptComplete': 'POLISH Classical Quests MZ DEMO',
    'DCP_Mask_AbandonDismiss': 'POLISH Classical Quests MZ DEMO',
    'DCP_Mask_VictoryDefeat': 'POLISH Classical Results MZ DEMO v1.1',
    'DCP_Mask_SuccessFailure': 'POLISH Classical Results MZ DEMO v1.1',
}
# Letters that sit on the baseline and stop at the capital height (no
# overshoot or descender), used to measure both the pack and a new font.
FLAT = set('EFTILHNMKZXAVWY')
# Measured from the reference sheets (threshold 128); used if they are absent.
PACK_CAP_HEIGHT = 131
PACK_BASELINE = 193
CELL = 256
SCALE = 4


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


class CoverageError(ValueError):
    pass


# A code point no font maps: what the font draws for it is its .notdef glyph.
UNMAPPED = '\U0010FFFF'


def missing_characters(font_path, characters, method='auto'):
    """Characters the font does not map ('missing') or maps to a glyph with no
    ink ('blank'), and how mapping was decided ('cmap' or 'render')."""
    if method == 'auto':
        try:
            import fontTools.ttLib  # noqa: F401
            method = 'cmap'
        except ImportError:
            method = 'render'
    probe = ImageFont.truetype(str(font_path), 96)
    def mask(c):
        m = probe.getmask(c)
        return m.size, bytes(m)
    if method == 'cmap':
        from fontTools.ttLib import TTFont
        with TTFont(str(font_path), fontNumber=0, lazy=True) as tt:
            cmap = tt.getBestCmap() or {}
            notdef = tt.getGlyphOrder()[0]
        # Mapped to glyph 0 is the same as not mapped: it draws .notdef.
        missing = [c for c in characters if cmap.get(ord(c), notdef) == notdef]
    else:
        notdef = mask(UNMAPPED)
        missing = [c for c in characters if mask(c) == notdef]
    blank = [c for c in characters if c not in missing and probe.getmask(c).getbbox() is None]
    return missing, blank, method


def shared_glyphs(font_path, characters, others=()):
    """Groups of distinct characters that draw the identical glyph: a font's
    stand-in for letters it lacks (a placeholder or 'demo' glyph) rather than
    letters. No two capitals or ! of a real font look the same. `others` are
    compared too (the rest of A-Z), but only groups holding one of
    `characters` are returned."""
    probe = ImageFont.truetype(str(font_path), 96)
    groups = {}
    for c in list(characters) + [o for o in others if o not in characters]:
        m = probe.getmask(c)
        if m.getbbox() is not None:
            groups.setdefault((m.size, bytes(m)), []).append(c)
    return [g for g in groups.values() if len(g) > 1 and any(c in characters for c in g)]


def check_coverage(font_path, method='auto'):
    characters = sorted({c for cells in LAYOUTS.values() for c in cells if c})
    missing, blank, used = missing_characters(font_path, characters, method)
    # The capitals the sheets do not use, where the font has them.
    rest = [c for c in map(chr, range(65, 91)) if c not in characters]
    rest = [c for c in rest if c not in missing_characters(font_path, rest, used)[0]]
    groups = shared_glyphs(font_path, [c for c in characters if c not in missing], rest)
    shared = [c for g in groups for c in g if c in characters]
    if missing or blank or shared:
        def where(c):
            return ', '.join(f'{name} cell {cells.index(c)}' for name, cells in LAYOUTS.items() if c in cells)
        lines = [f'  {c!r} U+{ord(c):04X} {kind}: {where(c)}'
                 for kind, group in (('not in font', missing), ('draws nothing', blank)) for c in group]
        lines += [f'  {c!r} U+{ord(c):04X} same glyph as {"".join(x for x in g if x != c)}: {where(c)}'
                  for g in groups for c in g]
        bad = missing + blank + shared
        raise CoverageError(f'{Path(font_path).name} lacks {len(bad)} of the {len(characters)} '
                            f'characters the sheets need ({"".join(bad)}; checked by {used}):\n' +
                            '\n'.join(lines) + '\nNo sheets were written.')
    return {'method': used, 'characters': ''.join(characters)}


def reference_path(name):
    return REFERENCE_ROOT/REFERENCE_DIRS[name]/'effects'/'Texture'/(name+'.png')


def ink_box(tile):
    return tile.point(lambda v: 255 if v >= 128 else 0).getbbox()


def pack_metrics():
    """Median capital height and baseline of the flat letters in the pack."""
    tops, bottoms = [], []
    for name, cells in LAYOUTS.items():
        p = reference_path(name)
        if not p.exists():
            return PACK_CAP_HEIGHT, PACK_BASELINE, 'built-in'
        ref = Image.open(p).convert('L')
        for cell, char in enumerate(cells):
            if char in FLAT:
                x, y = (cell % 4) * CELL, (cell // 4) * CELL
                box = ink_box(ref.crop((x, y, x + CELL, y + CELL)))
                tops.append(box[1]); bottoms.append(box[3])
    base = statistics.median(bottoms)
    return base - statistics.median(tops), base, 'reference sheets'


def render_tile(font, char, baseline):
    tile = Image.new('L', (CELL * SCALE, CELL * SCALE), 0)
    if char:
        box = font.getbbox(char, anchor='ls')
        ImageDraw.Draw(tile).text((CELL * SCALE / 2 - (box[0] + box[2]) / 2, baseline * SCALE),
                                  char, font=font, fill=255, anchor='ls')
    return tile.resize((CELL, CELL), Image.Resampling.LANCZOS)


def fit_cell(font_path, characters):
    """The original rule: widest glyph and full ink height within 184 px, ink centred."""
    size = 180
    while size > 0:
        font = ImageFont.truetype(str(font_path), size * SCALE)
        boxes = [font.getbbox(c, anchor='ls') for c in characters]
        top = min(b[1] for b in boxes); bottom = max(b[3] for b in boxes)
        if max(b[2] - b[0] for b in boxes) <= 184 * SCALE and bottom - top <= 184 * SCALE:
            return font, size, (CELL * SCALE / 2 - (top + bottom) / 2) / SCALE, {}
        size -= 1
    raise ValueError('Font cannot fit the configured cells')


def fit_pack(font_path, characters, margin):
    """Match the pack's measured capital height and baseline, then make sure
    every glyph (and its blur) stays inside its cell."""
    cap_target, base_target, source = pack_metrics()
    flat = [c for c in characters if c in FLAT]
    size, baseline = 130.0, float(base_target)
    for _ in range(6):
        font = ImageFont.truetype(str(font_path), max(1, round(size * SCALE)))
        boxes = [ink_box(render_tile(font, c, baseline)) for c in flat]
        cap = statistics.median(b[3] for b in boxes) - statistics.median(b[1] for b in boxes)
        base = statistics.median(b[3] for b in boxes)
        if abs(cap - cap_target) < 0.5 and abs(base - base_target) < 0.5:
            break
        size *= cap_target / cap
        baseline += base_target - base
    shrunk = 1.0
    while True:
        font = ImageFont.truetype(str(font_path), max(1, round(size * shrunk * SCALE)))
        boxes = [ink_box(render_tile(font, c, baseline)) for c in characters]
        if all(b[0] >= margin and b[1] >= margin and b[2] <= CELL - margin and b[3] <= CELL - margin
               for b in boxes):
            break
        shrunk -= 0.01
        if shrunk <= 0.3:
            raise ValueError('Font cannot fit the cells even at 30% of the pack capital height')
    flat_boxes = [ink_box(render_tile(font, c, baseline)) for c in flat]
    achieved = {
        'metricSource': source,
        'targetCapHeight': cap_target, 'targetBaseline': base_target,
        'capHeight': statistics.median(b[3] for b in flat_boxes) - statistics.median(b[1] for b in flat_boxes),
        'baseline': statistics.median(b[3] for b in flat_boxes),
        'shrunkToFit': round(shrunk, 2),
    }
    return font, round(size * shrunk * SCALE) / SCALE, baseline, achieved


def generate(font_path, output, radius=3.0, overwrite=False, fit='pack'):
    font_path = Path(font_path).resolve()
    output = Path(output).resolve()
    if output.exists() and not overwrite:
        raise ValueError(f'Output already exists: {output}; choose a fresh folder or use --overwrite')
    for name, cells in LAYOUTS.items():
        assert len(cells) == 16
    coverage = check_coverage(font_path)
    characters = sorted({c for cells in LAYOUTS.values() for c in cells if c})
    margin = math.ceil(3 * radius) + 2
    if fit == 'pack':
        font, size, baseline, achieved = fit_pack(font_path, characters, margin)
    else:
        font, size, baseline, achieved = fit_cell(font_path, characters)
    output.mkdir(parents=True, exist_ok=True)
    textures = output/'Texture'
    textures.mkdir(exist_ok=True)
    mappings = []
    checks = []
    for name, cells in LAYOUTS.items():
        sharp = Image.new('RGBA', (1024, 1024), (0, 0, 0, 255))
        blurred = sharp.copy()
        reference = reference_path(name)
        ref = None
        if reference.exists():
            ref = Image.open(reference).convert('RGB')
            assert ref.size == (1024, 1024)
        for cell, char in enumerate(cells):
            tile = render_tile(font, char, baseline)
            glow = tile.filter(ImageFilter.GaussianBlur(radius))
            pos = ((cell % 4) * CELL, (cell // 4) * CELL)
            opaque = Image.new('L', (CELL, CELL), 255)
            sharp.paste(Image.merge('RGBA', (tile, tile, tile, opaque)), pos)
            blurred.paste(Image.merge('RGBA', (glow, glow, glow, opaque)), pos)
            assert bool(tile.getbbox()) == bool(char), f'Blank or unexpected glyph: {name} cell {cell}'
            if ref is not None:
                original = ref.crop((*pos, pos[0] + CELL, pos[1] + CELL)).convert('L')
                assert (original.getextrema()[1] > 32) == bool(char), f'Reference occupancy differs at {name} cell {cell}'
            mappings.append({'texture': name+'.png', 'blurTexture': name+'_Blur.png', 'cell': cell, 'character': char,
                             'row': cell // 4, 'column': cell % 4, 'glyphBounds': tile.getbbox()})
        sharp.save(textures/(name+'.png'))
        blurred.save(textures/(name+'_Blur.png'))
        for file in [textures/(name+'.png'), textures/(name+'_Blur.png')]:
            saved = Image.open(file)
            assert saved.size == (1024, 1024) and saved.mode == 'RGBA'
            assert saved.getchannel('A').getextrema() == (255, 255)
            assert saved.getchannel('R').tobytes() == saved.getchannel('G').tobytes() == saved.getchannel('B').tobytes()
            checks.append({'file': 'Texture/'+file.name, 'size': list(saved.size), 'mode': saved.mode, 'sha256': digest(file)})
    manifest = {'font': str(font_path), 'fontName': list(font.getname()), 'fontSha256': digest(font_path),
                'characterCoverage': coverage,
                'fit': fit, 'fontSize': size, 'baselinePixels': baseline, 'fitResult': achieved,
                'grid': [4, 4], 'cellSize': CELL, 'blurRadiusPixels': radius, 'cellMarginPixels': margin,
                'layouts': LAYOUTS, 'cells': mappings, 'readbackChecks': checks,
                'originalLayoutReferences': [{'path': str(reference_path(n)), 'sha256': digest(reference_path(n))}
                                             for n in LAYOUTS if reference_path(n).exists()],
                'effectRendered': False}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf8')
    # Preview reads the saved files, rather than rerendering the font.
    preview = Image.new('RGB', (1440, 790), (25, 27, 33))
    d = ImageDraw.Draw(preview)
    ui = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 22)
    small = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 15)
    d.text((40, 22), ' / '.join(font.getname())+' — original pack cell layouts', font=ui, fill=(235, 235, 235))
    for row, (name, cells) in enumerate(LAYOUTS.items()):
        y = 87 + row * 165
        d.text((40, y), name, font=small, fill=(155, 165, 175))
        atlas = Image.open(textures/(name+'.png')).convert('L')
        for cell, char in enumerate(cells):
            x = (cell % 4) * CELL; sy = (cell // 4) * CELL
            mask = atlas.crop((x, sy, x + CELL, sy + CELL)).resize((80, 80), Image.Resampling.LANCZOS)
            px = 65 + cell * 82
            preview.paste((245, 237, 216), (px, y + 27, px + 80, y + 107), mask)
            d.text((px + 26, y + 112), str(cell), font=small, fill=(145, 155, 165))
    d.text((40, 755), 'Matching filenames, cells and blank slots. Typography preview; effect playback has not been tested.',
           font=small, fill=(155, 165, 175))
    preview.save(output/'preview.png')
    print(json.dumps({'output': str(output), 'fontName': manifest['fontName'], 'files': len(checks),
                      'cells': len(mappings), 'fontSize': size, 'fit': achieved}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--font', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--blur', type=float, default=3.0,
                        help='Gaussian radius in pixels for the _Blur sheets (pack: 3.0)')
    parser.add_argument('--fit', choices=['pack', 'cell'], default='pack',
                        help="pack: the pack's capital height and baseline (default); "
                             'cell: the previous rule, widest glyph fills the cell')
    parser.add_argument('--reference-root', default=str(REFERENCE_ROOT),
                        help='folder holding the POLISH demo folders (reference sheets)')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    REFERENCE_ROOT = Path(args.reference_root)
    if args.blur < 0: parser.error('--blur must be nonnegative')
    try:
        generate(args.font, args.out, args.blur, args.overwrite, args.fit)
    except CoverageError as e:
        print(f'make_atlases: {e}', file=sys.stderr)
        sys.exit(1)
