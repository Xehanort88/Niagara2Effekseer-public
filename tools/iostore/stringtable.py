"""Key/value pairs of a cooked UE StringTable package read straight from an IoStore container, without FModel.

A heuristic scan: every FString (ANSI or UTF-16) whose text looks like a key (letters/underscores then a digit,
e.g. SkillName000002) followed by another FString is a pair. Written for a UE 5.6 study game's skill names
(ST_SkillName is not in the FModel export; 1,160 pairs), 2026-11-01; restored into tools/ after the 2026-11-07 sweep.

    python tools/iostore/stringtable.py <container.utoc> ST_SkillName.uasset [regex on key or value]
    e.g. ... MyGame-Windows.utoc ST_SkillName.uasset "スキルベータ|スキルアルファ"   ->  SkillName000001 = スキルベータ, ...

Print Japanese with PYTHONIOENCODING=utf-8 on Windows consoles.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from iostore import Toc  # noqa: E402

KEY = re.compile(r'^[A-Za-z_]+\d')


def fstr(b, o):
    """(text, offset after it) for an FString at o, or (None, o)."""
    if o + 4 > len(b):
        return None, o
    n = struct.unpack_from('<i', b, o)[0]
    if 0 < n < 512 and o + 4 + n <= len(b) and b[o + 3 + n] == 0:
        s = b[o + 4:o + 3 + n]
        if all(32 <= c < 127 for c in s):
            return s.decode(), o + 4 + n
    if -512 < n < 0 and o + 4 - 2 * n <= len(b) and b[o + 2 - 2 * n:o + 4 - 2 * n] == b'\0\0':
        try:
            return b[o + 4:o + 2 - 2 * n].decode('utf-16-le'), o + 4 - 2 * n
        except UnicodeDecodeError:
            pass
    return None, o


def pairs(b):
    """[(key, value)] found in a package's bytes."""
    o, out = 0, []
    while o < len(b) - 8:
        k, o2 = fstr(b, o)
        if k and KEY.match(k):
            v, o3 = fstr(b, o2)
            if v is not None:
                out.append((k, v))
                o = o3
                continue
        o += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('utoc')
    ap.add_argument('package', help='substring of the package path, e.g. ST_SkillName.uasset')
    ap.add_argument('filter', nargs='?', help='regex on the key or the value')
    a = ap.parse_args(argv)
    t = Toc(a.utoc)
    found = [(f, i) for f, i in t.files.items() if a.package in f]
    if not found:
        print('no package matching %r in %s' % (a.package, a.utoc))
        return 1
    for f, i in found:
        ps = pairs(t.read(i))
        print(f.split('/')[-1], len(ps), 'pairs')
        for k, v in ps:
            if a.filter is None or re.search(a.filter, v) or re.search(a.filter, k):
                print('  ', k, '=', v)
    return 0


if __name__ == '__main__':
    sys.exit(main())
