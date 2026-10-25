"""Tests for tools/rdcdigest (no RenderDoc needed): DDS parsing and decoding, EXR round trip, cbuffer rows; v2:
changed box and crop, RenderDoc format names, contribution and dynamic-snapshot conversion, post-VS screen maths.
usage: python tools/rdcdigest/test_rdcdigest.py --work <folder>"""
import argparse
import os
import shutil
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ddsio  # noqa: E402
import rawdiff  # noqa: E402
import rdcdigest  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    print('%s %s %s' % ('ok  ' if cond else 'FAIL', name, detail))
    if not cond:
        FAILS.append(name)


def dds_dx10(fmt, w, h, mips, payload, arr=1, cube=False):
    hdr = bytearray(148)
    hdr[:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if mips > 1 else 0), h, w, 0, 0, mips)
    struct.pack_into('<II4s', hdr, 76, 32, 0x4, b'DX10')
    struct.pack_into('<I', hdr, 112, 0x200 | 0xFC00 if cube else 0)
    struct.pack_into('<5I', hdr, 128, fmt, 3, 0x4 if cube else 0, arr, 0)
    return bytes(hdr) + payload


def dds_fourcc(cc, w, h, mips, payload):
    hdr = bytearray(128)
    hdr[:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000, h, w, 0, 0, mips)
    struct.pack_into('<II4s', hdr, 76, 32, 0x4, cc)
    return bytes(hdr) + payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    work = ap.parse_args().work
    os.makedirs(work, exist_ok=True)
    # Execute the real snapshot method without importing RenderDoc. MSAA is
    # explicitly recorded as unstored, and has no raw-subresource hash.
    import ast
    import time
    from types import SimpleNamespace
    from pathlib import Path
    tree = ast.parse(Path(__file__).with_name('rd_digest.py').read_text())
    snapshot = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
                    for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'snapshot')
    namespace = {'time': time, 'rid': str}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[snapshot], type_ignores=[])),
                 'rd_digest.snapshot', 'exec'), namespace)
    fake = SimpleNamespace(last_write=lambda *a: 5, snaps={}, dynamic={}, phase={'dynamic_s': 0.0})
    row = {}
    namespace['snapshot'](fake, 7, SimpleNamespace(msSamp=4), row, 10, True)
    check('MSAA snapshot explicitly unstored', row == {'written_at': 5, 'snapshot': 'msaa: not stored'})
    namespace['snapshot'](fake, 7, SimpleNamespace(msSamp=4), row, 11, True)
    check('MSAA snapshot cache read', len(fake.snaps) == 1 and 'raw_md5' not in row)
    for sub in ('cap', 'store'):                     # this test's own folders, from an earlier run
        shutil.rmtree(os.path.join(work, sub), ignore_errors=True)

    # half RGBA, 2 mips: layout and exact decode
    m0 = np.arange(4 * 4 * 4, dtype=np.float16).reshape(4, 4, 4) / 7
    m1 = -np.arange(2 * 2 * 4, dtype=np.float16).reshape(2, 2, 4)
    d = ddsio.read_dds(dds_dx10(10, 4, 4, 2, m0.tobytes() + m1.tobytes()))
    check('dx10 header', (d.format, d.width, d.height, d.mips, d.slices) == ('R16G16B16A16_FLOAT', 4, 4, 2, 1))
    ch = ddsio.decode_raw(d, 1)
    check('half mip 1 exact', all(np.array_equal(ch[c], m1[..., i]) for i, c in enumerate('RGBA')))

    # cube: 6 slices x 1 mip
    faces = b''.join(bytes([i] * 16) for i in range(6))
    d = ddsio.read_dds(dds_dx10(87, 2, 2, 1, faces, cube=True))
    check('cube slices', d.slices == 6 and d.surface(0, 5) == bytes([5] * 16))
    check('bgra order', ddsio.decode_raw(d, 0, 2)['R'][0, 0] == 2 / 255.0)

    # legacy BC4U, 8x8 with 4 mips (blocks: 4, 1, 1, 1): decodes through Pillow
    blocks = [bytes([200, 100]) + b'\0' * 6] * 4 + [bytes([50, 50]) + b'\0' * 6] * 3
    d = ddsio.read_dds(dds_fourcc(b'BC4U', 8, 8, 4, b''.join(blocks)))
    check('bc4 legacy', d.format == 'BC4_UNORM' and d.mip_bytes(0) == 32 and d.mip_bytes(3) == 8)
    im = ddsio.to_image(d, 0)
    check('bc4 pillow', np.asarray(im.convert('L'))[0, 0] == 200, str(np.asarray(im.convert('L'))[0, 0]))

    # R11G11B10: 1.0 in every channel (exponent 15, mantissa 0)
    v = (15 << 6) | ((15 << 6) << 11) | ((15 << 5) << 22)
    d = ddsio.read_dds(dds_dx10(26, 1, 1, 1, struct.pack('<I', v)))
    ch = ddsio.decode_raw(d)
    check('r11g11b10', [float(ch[c][0, 0]) for c in 'RGB'] == [1.0, 1.0, 1.0])

    # R9G9B9E5 (Unity GI irradiance): shared exponent 16 -> mantissa / 256
    v = 256 | (128 << 9) | (511 << 18) | (16 << 27)
    d = ddsio.read_dds(dds_dx10(67, 1, 1, 1, struct.pack('<I', v)))
    ch = ddsio.decode_raw(d)
    check('r9g9b9e5', d.format == 'R9G9B9E5_SHAREDEXP' and
          [float(ch[c][0, 0]) for c in 'RGB'] == [1.0, 0.5, 511 / 256.0], [float(ch[c][0, 0]) for c in 'RGB'])
    check('r9g9b9e5 png', ddsio.to_image(d).getpixel((0, 0)) == (255, 128, 255, 255))

    # depth + stencil
    d = ddsio.read_dds(dds_dx10(20, 1, 1, 1, struct.pack('<fI', 0.25, 0x7F)))
    ch = ddsio.decode_raw(d)
    check('d32s8', float(ch['Z'][0, 0]) == 0.25 and int(ch['S'][0, 0]) == 0x7F)

    # EXR round trip: half, float, uint, a size that is not a multiple of 16 lines
    p = os.path.join(work, 'rt.exr')
    a = {'R': (np.random.rand(37, 53) * 9).astype(np.float16), 'Z': np.random.rand(37, 53).astype(np.float32),
         'S': np.arange(37 * 53, dtype=np.uint32).reshape(37, 53)}
    ddsio.write_exr(p, a)
    b = ddsio.read_exr(p)
    check('exr round trip', all(np.array_equal(a[k], b[k]) for k in a))

    # cbuffer rows: floats, and ints shown as ints
    rows = rdcdigest.floats_rows(struct.pack('<4f4I', 1.5, 0, -2, 3, 1, 2, 0, 7).hex())
    check('cb float row', rows[0][1] == (1.5, 0.0, -2.0, 3.0) and '(uint)' not in rdcdigest.fmt_row(*rows[0]))
    check('cb uint row', '(uint)' in rdcdigest.fmt_row(*rows[1]))

    # ---- v2 ------------------------------------------------------------------------------------------------
    # changed box (pure Python, runs inside qrenderdoc) against numpy, and crop
    rng = np.random.default_rng(7)
    a = rng.integers(0, 255, (37, 53, 8), dtype=np.uint8)
    check('box equal', rawdiff.changed_box(a.tobytes(), a.tobytes(), 53, 37, 8) is None)
    ok = True
    for _ in range(40):
        b = a.copy()
        for _ in range(rng.integers(1, 4)):
            b[rng.integers(0, 37), rng.integers(0, 53), rng.integers(0, 8)] ^= 1 << int(rng.integers(0, 8))
        ys, xs = np.nonzero((a != b).any(axis=2))
        want = (xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
        got = rawdiff.changed_box(a.tobytes(), b.tobytes(), 53, 37, 8)
        ok = ok and tuple(int(v) for v in want) == got
        x, y, w, h = got
        ok = ok and rawdiff.crop(b.tobytes(), 53, 8, got) == b[y:y + h, x:x + w].tobytes()
    check('box matches numpy + crop', ok)

    # RenderDoc format names
    d32s8 = np.zeros((2, 3, 2), np.uint32)
    d32s8[..., 0] = np.float32(0.75).view(np.uint32)
    d32s8[..., 1] = 0x81
    ch = ddsio.decode_pixels('D32S8_TYPELESS', d32s8.tobytes(), 3, 2)
    check('D32S8 alias', float(ch['Z'][1, 2]) == 0.75 and int(ch['S'][0, 0]) == 0x81)
    ch = ddsio.decode_pixels('B8G8R8A8_TYPELESS', bytes([10, 20, 30, 40]), 1, 1)
    check('BGRA typeless alias', [round(float(ch[c][0, 0]) * 255) for c in 'RGBA'] == [30, 20, 10, 40])
    check('texel values', rdcdigest.texel_values('R32G32B32A32_FLOAT', struct.pack('<4f', 1.5, 2, 3, 4).hex()) ==
          [[1.5, 2.0, 3.0, 4.0]])

    # contributions: a half target, before/after crops -> difference EXR (half), stats, thumbnail, threshold
    cap = os.path.join(work, 'cap')
    os.makedirs(os.path.join(cap, 'contrib'), exist_ok=True)
    before = np.zeros((4, 5, 4), np.float16)
    after = before.copy()
    after[1, 2] = (0.5, 0.25, 0.0, 1.0)
    after[3, 4] = (0.0, 0.0, 0.001, 0.0)                       # a tiny change at the box corner
    open(os.path.join(cap, 'contrib', '7_0.raw'), 'wb').write(before.tobytes() + after.tobytes())
    open(os.path.join(cap, 'contrib', '8_0.raw'), 'wb').write(before.tobytes() + after.tobytes())
    man = {'draws': [{'eid': 7, 'contrib': [{'slot': 0, 'format': 'R16G16B16A16_FLOAT', 'box': [10, 20, 5, 4],
                                             'raw': '7_0.raw'}]},
                     {'eid': 8, 'contrib': [{'slot': 0, 'format': 'R16G16B16A16_FLOAT', 'box': [10, 20, 5, 4],
                                             'raw': '8_0.raw'}]}]}
    n, _ = rdcdigest.convert_contribs(cap, {'draws': man['draws'][:1]})
    c = man['draws'][0]['contrib'][0]
    e = ddsio.read_exr(os.path.join(cap, 'contrib', c['exr']))
    check('contrib exr', n == 1 and e['R'].dtype == np.float16 and float(e['R'][1, 2]) == 0.5 and
          float(e['B'][3, 4]) == float(np.float16(0.001)) and c['pixels'] == 2 and abs(c['sum']['G'] - 0.25) < 1e-9)
    check('contrib thumbnail', os.path.exists(os.path.join(cap, 'contrib', c['png'])) and
          not os.path.exists(os.path.join(cap, 'contrib', '7_0.raw')))
    rdcdigest.convert_contribs(cap, {'draws': man['draws'][1:]}, 'float', 0.01)
    c = man['draws'][1]['contrib'][0]
    e = ddsio.read_exr(os.path.join(cap, 'contrib', c['exr']))
    check('contrib threshold box', c['box'] == [12, 21, 1, 1] and e['R'].dtype == np.float32 and e['R'].shape == (1, 1))

    # dynamic snapshots: float -> EXR (exact), 8-bit -> PNG with the stored bytes
    store = os.path.join(work, 'store')
    os.makedirs(os.path.join(store, 'blobs', 'dynamic'), exist_ok=True)
    hf = np.array([[1.5, -2.0], [65504.0, 0.0]], np.float16)
    rgba = np.stack([hf] * 4, -1)
    open(os.path.join(store, 'blobs', 'dynamic', 'aa.dds'), 'wb').write(dds_dx10(10, 2, 2, 1, rgba.tobytes()))
    bgra = np.array([[[1, 2, 3, 4], [5, 6, 7, 8]]], np.uint8)
    open(os.path.join(store, 'blobs', 'dynamic', 'bb.dds'), 'wb').write(dds_dx10(87, 2, 1, 1, bgra.tobytes()))
    man = {'blobs': {'dynamic': ['aa', 'bb']}, 'blob_stats': {'bytes_new': {'dynamic': 1000}}}
    n = rdcdigest.convert_dynamic(store, man)
    e = ddsio.read_exr(os.path.join(store, 'blobs', 'dynamic', 'aa.exr'))
    from PIL import Image
    im = np.asarray(Image.open(os.path.join(store, 'blobs', 'dynamic', 'bb.png')))
    check('dynamic exr/png exact', n == 2 and np.array_equal(e['G'], hf) and im[0, 1].tolist() == [7, 6, 5, 8] and
          not os.path.exists(os.path.join(store, 'blobs', 'dynamic', 'aa.dds')))

    # post-VS: clip -> pixels, and instance grouping of a stored blob
    clip = np.array([[0.0, 0.0, 0.5, 1.0], [-2.0, 2.0, 0.0, 2.0]], np.float32)
    px, py, z = rdcdigest.screen_of(clip, [10, 20, 100, 50, 0, 1])
    check('screen_of', px.tolist() == [60.0, 10.0] and py.tolist() == [45.0, 20.0] and z.tolist() == [0.5, 0.0])
    os.makedirs(os.path.join(store, 'blobs', 'postvs'), exist_ok=True)
    verts = np.arange(3 * 4 * 6, dtype=np.float32).reshape(3, 4, 6)       # 3 instances x 4 vertices x 24 bytes
    open(os.path.join(store, 'blobs', 'postvs', 'cc.bin'), 'wb').write(verts.tobytes())
    m = {'tables': {'postvs_layout': {'L': [{'name': 'SV_Position', 'index': 0, 'offset': 0, 'comps': 4},
                                            {'name': 'TEXCOORD', 'index': 0, 'offset': 16, 'comps': 2}]}}}
    r = {'postvs': {'vs': {'vertex': 'cc', 'stride': 24, 'vertices': 4, 'inst_stride': 96, 'layout': 'L'}}}
    _, lay, f = rdcdigest.postvs_data(store, m, r)
    check('postvs grouping', f.shape == (3, 4, 6) and f[2, 3, 5] == 71 and lay[1]['offset'] == 16)

    # --dedup: a capture file equal to a blob becomes a hard link; rewriting it later (write_exr) never touches the blob
    ds = os.path.join(work, 'dedup')
    if os.path.exists(ds):
        shutil.rmtree(ds)
    os.makedirs(os.path.join(ds, 'blobs', 'dynamic'))
    os.makedirs(os.path.join(ds, 'captures', 'c1', 'rt'))
    big = {'R': np.arange(512 * 600, dtype=np.float32).reshape(512, 600)}
    blob = os.path.join(ds, 'blobs', 'dynamic', 'dd.exr')
    rt = os.path.join(ds, 'captures', 'c1', 'rt', '1_after.exr')
    other = os.path.join(ds, 'captures', 'c1', 'rt', '2_after.exr')
    ddsio.write_exr(blob, big)
    ddsio.write_exr(rt, big)
    ddsio.write_exr(other, {'R': big['R'] + 1})
    rdcdigest.dedup_store(ds, min_bytes=1)
    check('dedup links equal files only', os.path.samefile(blob, rt) and not os.path.samefile(blob, other))
    ddsio.write_exr(rt, {'R': big['R'] * 2})
    check('rewrite breaks the link, blob unchanged', not os.path.samefile(blob, rt) and
          np.array_equal(ddsio.read_exr(blob)['R'], big['R']) and np.array_equal(ddsio.read_exr(rt)['R'], big['R'] * 2))

    import index_tests
    index_tests.run(work, check, dds_dx10)

    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
