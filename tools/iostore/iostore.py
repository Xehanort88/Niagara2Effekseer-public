"""Minimal read-only UE 5.x IoStore (.utoc/.ucas) reader: table of contents, directory index, chunk reads.

Unencrypted containers only (the study games it was written for have no AES key). Written for a shader-library extraction
(2026-10-18) and used for a UE 5.6 study game's string tables (2026-11-01, 2026-11-07); restored into tools/
after the 2026-11-07 sweep deleted its task folder. Local study only (AGENTS.md § Scope of use).

Oodle-compressed blocks need the Oodle DLL (Windows): N2E_OODLE or the FModel copy at F:/FMODEL/oo2core_9_win64.dll.
Uncompressed blocks read anywhere.

    python tools/iostore/iostore.py list <container.utoc> [substring]     # files in the directory index
    python tools/iostore/iostore.py extract <container.utoc> <path substring> --out <folder>
"""
import argparse
import ctypes
import os
import struct
import sys
from pathlib import Path

OODLE = Path(os.environ.get('N2E_OODLE') or 'F:/FMODEL/oo2core_9_win64.dll')
_oodle = None

CHUNK_TYPES = {0: 'Invalid', 1: 'ExportBundleData', 2: 'BulkData', 3: 'OptionalBulkData', 4: 'MemoryMappedBulkData',
               5: 'ScriptObjects', 6: 'ContainerHeader', 7: 'ExternalFile', 8: 'ShaderCodeLibrary', 9: 'ShaderCode',
               10: 'PackageStoreEntry', 11: 'DerivedData', 12: 'EditorDerivedData', 13: 'PackageResource'}


def oodle_decompress(src, raw_len):
    global _oodle
    if _oodle is None:
        if sys.platform != 'win32' or not OODLE.is_file():
            raise RuntimeError('an Oodle-compressed block needs the Oodle DLL on Windows (N2E_OODLE, now %s)' % OODLE)
        _oodle = ctypes.WinDLL(str(OODLE))
        f = _oodle.OodleLZ_Decompress
        f.restype = ctypes.c_int64
        f.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_int64, ctypes.c_int]
    out = ctypes.create_string_buffer(raw_len)
    n = _oodle.OodleLZ_Decompress(src, len(src), out, raw_len, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != raw_len:
        raise ValueError('Oodle returned %d, expected %d' % (n, raw_len))
    return out.raw


class Reader:
    def __init__(self, b, pos=0):
        self.b, self.p = b, pos

    def take(self, n):
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack('<H', self.take(2))[0]
    def u32(self): return struct.unpack('<I', self.take(4))[0]
    def i32(self): return struct.unpack('<i', self.take(4))[0]
    def u64(self): return struct.unpack('<Q', self.take(8))[0]

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ''
        if n > 0:
            return self.take(n)[:-1].decode('latin-1')
        return self.take(-n * 2)[:-2].decode('utf-16-le')


class Toc:
    """A .utoc and its .ucas partitions. `files`: {mount path: chunk index} from the directory index."""

    def __init__(self, utoc):
        self.path = Path(utoc)
        b = self.path.read_bytes()
        r = Reader(b)
        if r.take(16) != b'-==--==--==--==-':
            raise ValueError('not a utoc: %s' % utoc)
        self.version = r.u8(); r.u8(); r.u16()
        header_size = r.u32()
        self.entry_count = r.u32()
        block_count = r.u32()
        block_entry_size = r.u32()
        method_count = r.u32()
        method_len = r.u32()
        self.block_size = r.u32()
        dir_size = r.u32()
        self.partition_count = r.u32()
        self.container_id = r.u64()
        r.take(16)
        self.flags = r.u8(); r.u8(); r.u16()
        seeds_count = r.u32()
        self.partition_size = r.u64()
        no_hash_count = r.u32()
        r.p = header_size
        self.chunk_ids = [r.take(12) for _ in range(self.entry_count)]
        self.offlen = []
        for _ in range(self.entry_count):
            e = r.take(10)
            self.offlen.append((int.from_bytes(e[0:5], 'big'), int.from_bytes(e[5:10], 'big')))
        r.take(4 * seeds_count)
        r.take(4 * no_hash_count)
        self.blocks = []
        for _ in range(block_count):
            e = r.take(block_entry_size)
            self.blocks.append((int.from_bytes(e[0:5], 'little'), int.from_bytes(e[5:8], 'little'),
                                int.from_bytes(e[8:11], 'little'), e[11]))
        self.methods = [r.take(method_len).split(b'\0')[0].decode() for _ in range(method_count)]
        if self.flags & 4:                                   # signed
            hs = r.i32()
            r.take(hs); r.take(hs); r.take(20 * block_count)
        if self.flags & 2:
            raise ValueError('encrypted container: %s' % utoc)
        self.files = {}
        if self.flags & 8 and dir_size:
            self._directory(Reader(r.take(dir_size)))
        self.ucas = [self.path.with_suffix('.ucas')] + [self.path.with_name('%s_s%d.ucas' % (self.path.stem, i))
                                                         for i in range(1, self.partition_count)]

    def _directory(self, r):
        mount = r.fstring()
        dirs = [(r.u32(), r.u32(), r.u32(), r.u32()) for _ in range(r.i32())]
        files = [(r.u32(), r.u32(), r.u32()) for _ in range(r.i32())]
        strings = [r.fstring() for _ in range(r.i32())]
        none = 0xFFFFFFFF

        def walk(di, prefix):
            name, child, sib, fil = dirs[di]
            here = prefix + (strings[name] + '/' if name != none else '')
            f = fil
            while f != none:
                fn, nxt, user = files[f]
                self.files[mount + here + strings[fn]] = user
                f = nxt
            c = child
            while c != none:
                walk(c, here)
                c = dirs[c][2]
        if dirs:
            walk(0, '')

    def chunk_type(self, i):
        return self.chunk_ids[i][11]

    def index_of(self, chunk_id):
        if not hasattr(self, '_by_id'):
            self._by_id = {c: i for i, c in enumerate(self.chunk_ids)}
        return self._by_id.get(bytes(chunk_id))

    def read(self, i):
        off, length = self.offlen[i]
        first, last = off // self.block_size, (off + length - 1) // self.block_size
        out = bytearray()
        handles = {}
        try:
            for bi in range(first, last + 1):
                boff, csize, usize, mi = self.blocks[bi]
                part, poff = divmod(boff, self.partition_size) if self.partition_size else (0, boff)
                h = handles.get(part) or handles.setdefault(part, open(self.ucas[part], 'rb'))
                h.seek(poff)
                data = h.read(csize)
                if mi == 0:
                    out += data[:usize]
                else:
                    method = self.methods[mi - 1]
                    if method.lower() != 'oodle':
                        raise ValueError('compression %s not supported' % method)
                    out += oodle_decompress(data, usize)
        finally:
            for h in handles.values():
                h.close()
        start = off - first * self.block_size
        return bytes(out[start:start + length])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list')
    p.add_argument('utoc')
    p.add_argument('substring', nargs='?', default='')
    p = sub.add_parser('extract')
    p.add_argument('utoc')
    p.add_argument('substring')
    p.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    t = Toc(a.utoc)
    hits = sorted(f for f in t.files if a.substring in f)
    if a.cmd == 'list':
        for f in hits:
            print('%-12s %s' % (CHUNK_TYPES.get(t.chunk_type(t.files[f]), '?'), f))
        print('%d of %d files' % (len(hits), len(t.files)))
        return 0
    out = Path(a.out)
    for f in hits:
        dst = out / f.lstrip('./').replace('../', '')
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(t.read(t.files[f]))
        print('wrote', dst)
    print('%d file(s) extracted' % len(hits))
    return 0


if __name__ == '__main__':
    sys.exit(main())
