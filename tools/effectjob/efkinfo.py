"""Read the resource list (INFO chunk) of an .efkefc: every texture, model, sound, material and curve the runtime loads,
as the relative paths stored in the file (Effekseer EffekseerCore/IO/EfkEfc.cs GetInfoData).

    python tools/effectjob/efkinfo.py <effect.efkefc>     # one line per resource: type, path, on disk or MISSING

`Niagara2Efk dump` prints resource values as bare file names; the runtime resolves the stored relative path against the
.efkefc's folder, so an installed effect is only complete when every path here exists under that folder.
"""
import struct
import sys
from pathlib import Path

# EfkEfc.cs FileType (1.6+ dependency list)
FILE_TYPES = {0: 'other', 1: 'texture', 2: 'sound', 3: 'model', 4: 'material', 5: 'curve'}
OLD_LISTS = ('texture', 'texture', 'texture', 'model', 'sound', 'material', 'curve')   # info version <= 1600
VER_1600 = 1600


def chunks(data):
    if data[:4] != b'EFKE':
        raise ValueError('not an .efkefc (no EFKE magic)')
    out, off = {}, 8
    while off + 8 <= len(data):
        tag = data[off:off + 4].decode('ascii', 'replace')
        size = struct.unpack_from('<i', data, off + 4)[0]
        out[tag] = data[off + 8:off + 8 + size]
        off += 8 + size
    return out


def _str(buf, off):
    n = struct.unpack_from('<i', buf, off)[0]          # UTF-16 code units, the terminating 0 included
    raw = buf[off + 4:off + 4 + 2 * n]
    return raw.decode('utf-16-le').rstrip('\0'), off + 4 + 2 * n


def resources(path):
    """[(type, relative path)] in file order."""
    info = chunks(Path(path).read_bytes()).get('INFO')
    if info is None:
        raise ValueError('%s has no INFO chunk' % path)
    ver = struct.unpack_from('<i', info, 0)[0]
    off, out = 4, []
    if ver <= VER_1600:
        for kind in OLD_LISTS:
            n = struct.unpack_from('<i', info, off)[0]
            off += 4
            for _ in range(n):
                s, off = _str(info, off)
                out.append((kind, s))
        return out
    n = struct.unpack_from('<i', info, off)[0]
    off += 4
    for _ in range(n):
        ftype, _flag = struct.unpack_from('<ii', info, off)
        s, off = _str(info, off + 8)
        out.append((FILE_TYPES.get(ftype, 'other'), s))
    return out


def missing(path):
    """Resources of `path` that do not exist relative to its folder: [(type, relative path)]."""
    base = Path(path).parent
    return [(k, p) for k, p in resources(path) if p and not (base / p.replace('\\', '/')).is_file()]


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    base = Path(sys.argv[1]).parent
    bad = 0
    for kind, p in resources(sys.argv[1]):
        ok = (base / p.replace('\\', '/')).is_file()
        bad += not ok
        print('%-8s %-7s %s' % (kind, 'ok' if ok else 'MISSING', p))
    print('EFKINFO: %d resource(s), %d missing' % (len(resources(sys.argv[1])), bad))
    sys.exit(1 if bad else 0)
