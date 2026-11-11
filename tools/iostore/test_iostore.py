"""Tests for tools/iostore (iostore.py, stringtable.py) on a synthetic container built here: no game data needed.
    python tools/iostore/test_iostore.py --work out/tests/iostore
Builds a one-file, uncompressed, indexed .utoc/.ucas with a string-table-like package in it, then checks the TOC,
the directory index, chunk reads across two blocks, the string-table scan, the refusal of an encrypted or foreign
file, and the list/extract commands. Ends with 'N passed, M failed'.
"""
import argparse
import io
import shutil
import struct
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import iostore as I  # noqa: E402
import stringtable as ST  # noqa: E402

RESULTS = []
NONE = 0xFFFFFFFF


def check(name, ok, detail=''):
    RESULTS.append(bool(ok))
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  ' + str(detail)))


def fstring(s):
    try:
        b = s.encode('ascii') + b'\0'
        return struct.pack('<i', len(b)) + b
    except UnicodeEncodeError:
        b = s.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(b) // 2)) + b


def package_bytes():
    """A fake StringTable package: filler, then key/value FStrings (one ANSI value, two UTF-16)."""
    body = b'\x07' * 37 + fstring('ST_SkillName') + b'\x00' * 9
    for k, v in (('SkillName000002', 'スキルアルファ'), ('SkillName000001', 'スキルベータ'), ('SkillName100010', 'Potion')):
        body += fstring(k) + fstring(v) + b'\x01\x02'
    return body


def build(folder, data, block_size=64, flags=8):
    """<folder>/T.utoc + T.ucas: one chunk (`data`) split into uncompressed blocks, indexed as
    ../../../Game/UI/ST_SkillName.uasset."""
    blocks, ucas, off = [], b'', 0
    while off < len(data):
        part = data[off:off + block_size]
        blocks.append(off.to_bytes(5, 'little') + len(part).to_bytes(3, 'little') + len(part).to_bytes(3, 'little') + b'\0')
        ucas += part + b'\0' * (block_size - len(part))
        off += block_size
    d = fstring('../../../')
    dirs = [(NONE, 1, NONE, NONE), (0, 2, NONE, NONE), (1, NONE, NONE, 0)]      # root -> Game -> UI (one file)
    d += struct.pack('<i', len(dirs)) + b''.join(struct.pack('<4I', *x) for x in dirs)
    d += struct.pack('<i', 1) + struct.pack('<3I', 2, NONE, 0)
    names = ['Game', 'UI', 'ST_SkillName.uasset']
    d += struct.pack('<i', len(names)) + b''.join(fstring(s) for s in names)
    method_len = 32
    head = b'-==--==--==--==-' + struct.pack('<BBH', 3, 0, 0)
    head += struct.pack('<9I', 144, 1, len(blocks), 12, 1, method_len, block_size, len(d), 1)
    head += struct.pack('<Q', 0x1234) + b'\0' * 16 + struct.pack('<BBH', flags, 0, 0)
    head += struct.pack('<I', 0) + struct.pack('<Q', 0) + struct.pack('<I', 0)
    head += b'\0' * (144 - len(head))
    chunk_id = b'\x11' * 11 + bytes([1])                                         # ExportBundleData
    toc = head + chunk_id + (0).to_bytes(5, 'big') + len(data).to_bytes(5, 'big') + b''.join(blocks)
    toc += b'Oodle'.ljust(method_len, b'\0') + d
    (folder / 'T.utoc').write_bytes(toc)
    (folder / 'T.ucas').write_bytes(ucas)
    return folder / 'T.utoc'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    work = Path(a.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    data = package_bytes()
    utoc = build(work, data)
    t = I.Toc(utoc)
    check('toc: one entry, %d blocks' % len(t.blocks), t.entry_count == 1 and len(t.blocks) == (len(data) + 63) // 64)
    path = '../../../Game/UI/ST_SkillName.uasset'
    check('directory index names the file under its mount', t.files == {path: 0}, t.files)
    check('chunk type ExportBundleData', I.CHUNK_TYPES[t.chunk_type(0)] == 'ExportBundleData')
    check('read joins the blocks back', t.read(0) == data)
    ps = ST.pairs(t.read(0))
    want = [('SkillName000002', 'スキルアルファ'), ('SkillName000001', 'スキルベータ'), ('SkillName100010', 'Potion')]
    check('string table pairs (UTF-16 and ANSI values)', ps == want, ps)
    check('fstr refuses a non-string', ST.fstr(b'\xff\xff\xff\x7f' + b'\0' * 8, 0) == (None, 0))
    enc = work / 'enc'
    enc.mkdir()
    try:
        I.Toc(build(enc, data, flags=8 | 2))
        check('an encrypted container is refused', False)
    except ValueError as e:
        check('an encrypted container is refused', 'encrypted' in str(e), e)
    (work / 'x.utoc').write_bytes(b'not a toc' * 4)
    try:
        I.Toc(work / 'x.utoc')
        check('a foreign file is refused', False)
    except ValueError as e:
        check('a foreign file is refused', 'not a utoc' in str(e), e)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ST.main([str(utoc), 'ST_SkillName.uasset', 'アルファ'])
    out = buf.getvalue()
    check('stringtable command filters by value', rc == 0 and 'SkillName000002' in out and 'Potion' not in out, out)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = I.main(['list', str(utoc), 'Skill'])
    check('list command', rc == 0 and path in buf.getvalue() and '1 of 1 files' in buf.getvalue(), buf.getvalue())
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = I.main(['extract', str(utoc), 'ST_Skill', '--out', str(work / 'x')])
    got = work / 'x' / 'Game/UI/ST_SkillName.uasset'
    check('extract command writes the package', rc == 0 and got.is_file() and got.read_bytes() == data, buf.getvalue())
    print('%d passed, %d failed' % (sum(RESULTS), len(RESULTS) - sum(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
