"""Known traps in .efkmat code that compile and verify cleanly but draw wrong in the game: each one cost a round.

    python tools/hlslcheck/traps.py <file.efkmat | folder> ...   (folders are searched recursively)

Exit 1 if any material has a trap. Pure Python (no Windows): hlslcheck.py and tools/install/install.py run it too, so a
material with a trap cannot be installed.

Traps:
  int-literal   an integer literal in float arithmetic (`vcolor.xyz * 223`, `pow(x, 2)`). WebGL's GLSL ES has no
                implicit int -> float conversion, so the game drops the material (the layer draws nothing) while the
                editor's HLSL accepts it and hlslcheck passes it. 2026-11-15: Recovery and Donate retunes wrote factors
                as integers and their layers read 0.06-0.16 of the game until redone as 223.0.
  facing-squash the port's facing test (the game's SV_IsFrontFace) on a depth-squashed worldNormal in a material that
                unsquashes its normal: along the camera's axis the far wall's inside counts as a front face and draws lit
                like the near wall. 2026-11-15: OT2 HP Recovery's funnel drew a second, wider band ("expands twice").
                The facing test must use the drawn surface's normal, worldNormal with its depth part x 1/s^2
                (tools/cascade/ot2port.py facing_normal).
"""
import argparse
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from hlslcheck import parse_efkmat  # noqa: E402

NUM = re.compile(r'(?<![\w.$])(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?(?![\w.])')
UNSQUASH = re.compile(r'normalize\(normalize\(worldNormal\) \+ [0-9.e+-]+ \* dot\(normalize\(worldNormal\), \$F3\$\(')
FACING_RAW = re.compile(r'step\(0\.0, dot\(normalize\(worldNormal\), normalize\(cameraPosition\.xyz - worldPos\)\)\)')


def int_literals(code):
    """Lines with an integer literal next to an arithmetic operator or as a function argument (comments, preprocessor
    lines, int/for declarations and array indices left out)."""
    out = []
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
    for ln in code.splitlines():
        s = ln.split('//', 1)[0].strip()
        if not s or s.startswith('#') or re.match(r'(for|int|uint|bool)\b', s):
            continue
        s2 = re.sub(r'\[[^\]]*\]', '[]', s)
        for m in NUM.finditer(s2):
            if '.' in m.group(1) or m.group(2):
                continue
            before = s2[:m.start()].rstrip()[-1:]
            after = s2[m.end():].lstrip()[:1]
            if (before and before in '*/+-(,') or (after and after in '*/+-'):
                out.append(s)
                break
    return out


def check_code(code):
    """[(trap, detail)] for one material's code."""
    found = []
    for s in int_literals(code):
        found.append(('int-literal', 'integer literal in float code (GLSL ES refuses it, the layer draws nothing): ' + s[:140]))
    if UNSQUASH.search(code) and FACING_RAW.search(code):
        found.append(('facing-squash', 'facing test on the depth-squashed worldNormal (far walls draw as front faces): '
                      'use worldNormal with its depth part x 1/s^2 (ot2port facing_normal)'))
    return found


def check_file(path):
    return check_code(parse_efkmat(Path(path).read_bytes())['code'])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('paths', nargs='+')
    a = ap.parse_args(argv)
    files = []
    for p in map(Path, a.paths):
        files += sorted(p.rglob('*.efkmat')) if p.is_dir() else [p]
    bad = 0
    for f in files:
        try:
            found = check_file(f)
        except ValueError as e:
            print('SKIP %s  %s' % (f, e))
            continue
        if found:
            bad += 1
            for trap, detail in found:
                print('TRAP %s  %s: %s' % (f, trap, detail))
    print('TRAPS: %d material(s), %d with a trap' % (len(files), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
