"""Character coverage and unchanged-output checks for make_atlases.py.

python tools/fontatlas/test_make_atlases.py --work out/tests/fontatlas [--font C:/Windows/Fonts/arial.ttf]

Fixture fonts are subsets of --font made with fontTools (required for the
fixtures, not for make_atlases itself): one lacking Z and !, one mapping Z to
an empty glyph. The full font must produce sheets byte-identical to the
committed (git HEAD) make_atlases.py, so the coverage check changes nothing
else. All files go under --work, in a fresh run-* folder.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.dont_write_bytecode = True
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import make_atlases as m  # noqa: E402

WORK = None
FONT = None


def subset(name, drop='', blank='', remap=None):
    from fontTools import subset as fs
    from fontTools.ttLib import TTFont
    out = WORK / 'fonts' / name
    out.parent.mkdir(parents=True, exist_ok=True)
    opts = fs.Options()
    opts.notdef_outline = True
    opts.notdef_glyph = True
    font = TTFont(str(FONT))
    s = fs.Subsetter(opts)
    s.populate(text=''.join(c for c in map(chr, range(0x20, 0x7f)) if c not in drop))
    s.subset(font)
    # remap: character -> glyph of another character, or '.notdef'.
    targets = {c: 'space' for c in blank}
    for c, to in (remap or {}).items():
        targets[c] = to if to == '.notdef' else font.getBestCmap()[ord(to)]
    for c, glyph in targets.items():
        for table in font['cmap'].tables:
            if table.isUnicode():
                table.cmap[ord(c)] = glyph
    font.save(str(out))
    return out


def script(path, *args):
    return subprocess.run([sys.executable, '-B', str(path), *map(str, args)], capture_output=True, text=True)


def sheets(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((folder / 'Texture').glob('*.png'))}


class Coverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.missing = subset('missing-Z-bang.ttf', drop='Z!')
        cls.blank = subset('blank-Z.ttf', blank='Z')
        cls.notdef = subset('notdef-Z.ttf', remap={'Z': '.notdef'})
        cls.shared = subset('shared-ZX.ttf', remap={'Z': 'X', 'Q': 'O'})

    def test_missing_found_by_cmap(self):
        self.assertEqual(m.missing_characters(self.missing, list('!AEZ'), 'cmap'), (['!', 'Z'], [], 'cmap'))

    def test_missing_found_by_render(self):
        # The fallback when fontTools is absent must agree with the cmap.
        self.assertEqual(m.missing_characters(self.missing, list('!AEZ'), 'render'), (['!', 'Z'], [], 'render'))

    def test_full_font_covered_both_ways(self):
        chars = sorted({c for cells in m.LAYOUTS.values() for c in cells if c})
        for method in ('cmap', 'render'):
            self.assertEqual(m.missing_characters(FONT, chars, method)[:2], ([], []), method)

    def test_blank_glyph_reported(self):
        self.assertEqual(m.missing_characters(self.blank, list('AZ'), 'cmap'), ([], ['Z'], 'cmap'))

    def test_mapped_to_notdef_is_missing(self):
        # A cmap entry that points at glyph 0 still draws the .notdef box.
        for method in ('cmap', 'render'):
            self.assertEqual(m.missing_characters(self.notdef, list('AZ'), method)[0], ['Z'], method)

    def test_shared_glyph_groups(self):
        self.assertEqual(sorted(map(sorted, m.shared_glyphs(self.shared, list('AOQXZ')))), [['O', 'Q'], ['X', 'Z']])
        # X is not on the sheets but still exposes Z; a group of others alone is not reported.
        self.assertEqual(m.shared_glyphs(self.shared, list('AZ'), list('X')), [['Z', 'X']])
        self.assertEqual(m.shared_glyphs(self.shared, list('A'), list('XZ')), [])
        self.assertEqual(m.shared_glyphs(FONT, list('AOQXZ')), [])

    def test_cli_refuses_shared(self):
        out = WORK / 'cli-shared'
        r = script(HERE / 'make_atlases.py', '--font', self.shared, '--out', out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("'Z' U+005A same glyph as X: DCP_Mask_SuccessFailure cell 12", r.stderr)
        self.assertIn("'Q' U+0051 same glyph as O: DCP_Mask_UnlockAcceptComplete cell 0, DCP_Mask_AbandonDismiss cell 0", r.stderr)
        self.assertFalse(out.exists())

    def test_cli_refuses_missing_and_writes_nothing(self):
        out = WORK / 'cli-missing'
        r = script(HERE / 'make_atlases.py', '--font', self.missing, '--out', out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('lacks 2 of the 24 characters', r.stderr)
        self.assertIn("'!' U+0021 not in font: DCP_Mask_AbandonDismiss cell 15, DCP_Mask_VictoryDefeat cell 15, DCP_Mask_SuccessFailure cell 14", r.stderr)
        self.assertIn("'Z' U+005A not in font: DCP_Mask_SuccessFailure cell 12", r.stderr)
        self.assertFalse(out.exists(), 'refused run created its output folder')

    def test_cli_refuses_blank(self):
        out = WORK / 'cli-blank'
        r = script(HERE / 'make_atlases.py', '--font', self.blank, '--out', out)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("'Z' U+005A draws nothing: DCP_Mask_SuccessFailure cell 12", r.stderr)
        self.assertFalse(out.exists())


class Unchanged(unittest.TestCase):
    def test_sheets_identical_to_head(self):
        head = WORK / 'make_atlases_head.py'
        shown = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:tools/fontatlas/make_atlases.py'], capture_output=True)
        if shown.returncode != 0:
            self.skipTest('git HEAD copy unavailable')
        head.write_bytes(shown.stdout)
        for fit in ('pack', 'cell'):
            old, new = WORK / f'head-{fit}', WORK / f'new-{fit}'
            for path, out in ((head, old), (HERE / 'make_atlases.py', new)):
                r = script(path, '--font', FONT, '--out', out, '--fit', fit)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(len(sheets(new)), 8)
            self.assertEqual(sheets(old), sheets(new), fit)
            a = json.loads((old / 'manifest.json').read_text(encoding='utf8'))
            b = json.loads((new / 'manifest.json').read_text(encoding='utf8'))
            self.assertEqual(b.pop('characterCoverage')['method'], 'cmap')
            self.assertEqual(a, b, fit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--font', default='C:/Windows/Fonts/arial.ttf', type=Path)
    args, rest = parser.parse_known_args()
    root = args.work.resolve()
    if root == ROOT or any(root == ROOT / p or ROOT / p in root.parents for p in ('src', 'external', 'tests', 'tools')):
        sys.exit('--work must be outside repository source folders')
    root.mkdir(parents=True, exist_ok=True)
    WORK = Path(tempfile.mkdtemp(prefix='run-', dir=root))
    FONT = args.font.resolve()
    unittest.main(argv=[sys.argv[0]] + rest, verbosity=2)
