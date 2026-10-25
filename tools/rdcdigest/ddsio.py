"""DDS reading (the files RenderDoc's SaveTexture writes) and a small OpenEXR writer, for rdcdigest.

read_dds(bytes) -> Dds with .format (DXGI name), .width/.height/.depth/.mips/.slices and .surface(mip, slice) raw
bytes. decode(dds, mip, slice) -> float32 array (h, w, 4) for uncompressed formats, or a PIL image for BCn (through
Pillow's DDS decoder). write_exr(path, {'R': arr, ...}) writes a scanline OpenEXR with ZIP compression; float16
arrays become HALF channels, float32 FLOAT, uint32 UINT."""
import io
import os
import struct
import zlib

import numpy as np

# DXGI id -> (name, bytes per pixel or per 4x4 block, block compressed)
DXGI = {
    2: ('R32G32B32A32_FLOAT', 16, False), 10: ('R16G16B16A16_FLOAT', 8, False), 11: ('R16G16B16A16_UNORM', 8, False),
    16: ('R32G32_FLOAT', 8, False), 19: ('R32G8X24_TYPELESS', 8, False), 20: ('D32_FLOAT_S8X24_UINT', 8, False),
    23: ('R10G10B10A2_TYPELESS', 4, False), 24: ('R10G10B10A2_UNORM', 4, False), 26: ('R11G11B10_FLOAT', 4, False),
    27: ('R8G8B8A8_TYPELESS', 4, False), 28: ('R8G8B8A8_UNORM', 4, False), 29: ('R8G8B8A8_UNORM_SRGB', 4, False),
    30: ('R8G8B8A8_UINT', 4, False), 31: ('R8G8B8A8_SNORM', 4, False), 34: ('R16G16_FLOAT', 4, False),
    35: ('R16G16_UNORM', 4, False), 39: ('R32_TYPELESS', 4, False), 40: ('D32_FLOAT', 4, False),
    41: ('R32_FLOAT', 4, False), 42: ('R32_UINT', 4, False), 44: ('R24G8_TYPELESS', 4, False),
    45: ('D24_UNORM_S8_UINT', 4, False), 49: ('R8G8_UNORM', 2, False), 54: ('R16_FLOAT', 2, False),
    55: ('D16_UNORM', 2, False), 56: ('R16_UNORM', 2, False), 61: ('R8_UNORM', 1, False), 62: ('R8_UINT', 1, False),
    65: ('A8_UNORM', 1, False), 67: ('R9G9B9E5_SHAREDEXP', 4, False),
    70: ('BC1_TYPELESS', 8, True), 71: ('BC1_UNORM', 8, True), 72: ('BC1_UNORM_SRGB', 8, True),
    73: ('BC2_TYPELESS', 16, True), 74: ('BC2_UNORM', 16, True), 75: ('BC2_UNORM_SRGB', 16, True),
    76: ('BC3_TYPELESS', 16, True), 77: ('BC3_UNORM', 16, True), 78: ('BC3_UNORM_SRGB', 16, True),
    79: ('BC4_TYPELESS', 8, True), 80: ('BC4_UNORM', 8, True), 81: ('BC4_SNORM', 8, True),
    82: ('BC5_TYPELESS', 16, True), 83: ('BC5_UNORM', 16, True), 84: ('BC5_SNORM', 16, True),
    1: ('R32G32B32A32_TYPELESS', 16, False), 3: ('R32G32B32A32_UINT', 16, False),
    87: ('B8G8R8A8_UNORM', 4, False), 88: ('B8G8R8X8_UNORM', 4, False), 90: ('B8G8R8A8_TYPELESS', 4, False),
    91: ('B8G8R8A8_UNORM_SRGB', 4, False), 92: ('B8G8R8X8_TYPELESS', 4, False), 93: ('B8G8R8X8_UNORM_SRGB', 4, False),
    94: ('BC6H_TYPELESS', 16, True), 95: ('BC6H_UF16', 16, True), 96: ('BC6H_SF16', 16, True),
    97: ('BC7_TYPELESS', 16, True), 98: ('BC7_UNORM', 16, True), 99: ('BC7_UNORM_SRGB', 16, True),
}
BY_NAME = {v[0]: k for k, v in DXGI.items()}
FOURCC = {b'DXT1': 71, b'DXT2': 74, b'DXT3': 74, b'DXT4': 77, b'DXT5': 77, b'ATI1': 80, b'BC4U': 80, b'BC4S': 81,
          b'ATI2': 83, b'BC5U': 83, b'BC5S': 84}
D3DFMT = {36: 11, 111: 54, 112: 34, 113: 10, 114: 41, 115: 16, 116: 2}
# PNG export decodes a typeless or sRGB surface as its UNORM twin (same bytes)
TYPED = {1: 2, 70: 71, 72: 71, 73: 74, 75: 74, 76: 77, 78: 77, 79: 80, 82: 83, 94: 95, 97: 98, 99: 98, 27: 28, 29: 28,
         90: 87, 91: 87, 92: 88, 93: 88, 23: 24, 39: 41, 19: 20}


class Dds:
    def __init__(self, data):
        if data[:4] != b'DDS ':
            raise ValueError('not a DDS file')
        (_, flags, h, w, _, d, mips) = struct.unpack_from('<7I', data, 4)
        pf_flags, fourcc, bits = struct.unpack_from('<I4sI', data, 80)
        masks = struct.unpack_from('<4I', data, 92)
        caps2 = struct.unpack_from('<I', data, 112)[0]
        off = 128
        self.slices, self.cube = 1, bool(caps2 & 0x200)
        self.dim3 = bool(caps2 & 0x200000)
        if pf_flags & 0x4 and fourcc == b'DX10':
            fmt, resdim, misc, arr = struct.unpack_from('<4I', data, 128)
            off = 148
            self.slices = max(1, arr)
            self.cube = self.cube or bool(misc & 0x4)
            self.dim3 = resdim == 4
        elif pf_flags & 0x4:
            n = struct.unpack('<I', fourcc)[0]
            fmt = FOURCC.get(fourcc) or D3DFMT.get(n)
            if fmt is None:
                raise ValueError('unknown DDS fourcc %r' % fourcc)
        elif bits == 32 and masks[:3] == (0xFF0000, 0xFF00, 0xFF):
            fmt = 87 if masks[3] else 88
        elif bits == 32 and masks[:3] == (0xFF, 0xFF00, 0xFF0000):
            fmt = 28
        elif bits == 8:
            fmt = 61 if masks[0] else 65
        else:
            raise ValueError('unsupported legacy DDS pixel format (%d bits, masks %r)' % (bits, masks))
        if self.cube:
            self.slices *= 6
        self.dxgi = fmt
        self.format, self.unit, self.bc = DXGI[fmt]
        self.width, self.height = w, h
        self.depth = d if (self.dim3 and d) else 1
        self.mips = max(1, mips) if flags & 0x20000 or mips else 1
        self.data = data
        self.offsets = {}
        p = off
        for s in range(self.slices):
            for m in range(self.mips):
                n = self.mip_bytes(m)
                self.offsets[(m, s)] = (p, n)
                p += n
        if p > len(data):
            raise ValueError('DDS truncated: need %d bytes, have %d' % (p, len(data)))

    def mip_size(self, m):
        return max(1, self.width >> m), max(1, self.height >> m), max(1, self.depth >> m)

    def mip_bytes(self, m):
        w, h, d = self.mip_size(m)
        if self.bc:
            return ((w + 3) // 4) * ((h + 3) // 4) * self.unit * d
        return w * h * self.unit * d

    def surface(self, mip=0, slice_=0):
        p, n = self.offsets[(mip, slice_)]
        return self.data[p:p + n]


def read_dds(data):
    return Dds(data)


def _half_from_bits(bits, mant, exp_bits=5):
    """Unsigned small floats (R11G11B10): mantissa width mant, 5-bit exponent, bias 15."""
    e = (bits >> mant) & 0x1F
    m = bits & ((1 << mant) - 1)
    f = np.where(e == 0, m / float(1 << mant) * 2.0 ** -14,
                 (1.0 + m / float(1 << mant)) * np.exp2(e.astype(np.float64) - 15))
    f = np.where(e == 31, np.inf, f)
    return f.astype(np.float32)


def decode_raw(dds, mip=0, slice_=0):
    """Uncompressed surface -> dict of channel name -> numpy array (h, w) in the format's own type, exact."""
    w, h, d = dds.mip_size(mip)
    return decode_pixels(DXGI[TYPED.get(dds.dxgi, dds.dxgi)][0], dds.surface(mip, slice_), w, h * d)


# RenderDoc's ResourceFormat.Name() for formats whose DXGI name differs, and typeless/sRGB names -> typed twin
RD_ALIAS = {'D32S8_TYPELESS': 'D32_FLOAT_S8X24_UINT', 'D32S8': 'D32_FLOAT_S8X24_UINT', 'D24S8': 'D24_UNORM_S8_UINT',
            'D24S8_TYPELESS': 'D24_UNORM_S8_UINT', 'R24G8_TYPELESS': 'D24_UNORM_S8_UINT', 'D16': 'D16_UNORM',
            'D32': 'D32_FLOAT', 'R16_TYPELESS': 'R16_UNORM', 'R32_TYPELESS': 'R32_FLOAT', 'R8_TYPELESS': 'R8_UNORM',
            'R8G8_TYPELESS': 'R8G8_UNORM', 'R16G16_TYPELESS': 'R16G16_FLOAT', 'R32G32_TYPELESS': 'R32G32_FLOAT',
            'R16G16B16A16_TYPELESS': 'R16G16B16A16_FLOAT', 'R32G32B32A32_TYPELESS': 'R32G32B32A32_FLOAT',
            'R11G11B10_FLOAT': 'R11G11B10_FLOAT', 'B8G8R8A8_SRGB': 'B8G8R8A8_UNORM', 'R8G8B8A8_SRGB': 'R8G8B8A8_UNORM',
            'B8G8R8X8_SRGB': 'B8G8R8X8_UNORM', 'R10G10B10A2_TYPELESS': 'R10G10B10A2_UNORM'}


def typed_name(fmt):
    """A DXGI or RenderDoc format name -> the typed DXGI name decode_pixels knows (typeless/sRGB -> UNORM etc.)."""
    fmt = RD_ALIAS.get(fmt, fmt)
    if fmt in BY_NAME:
        return DXGI[TYPED.get(BY_NAME[fmt], BY_NAME[fmt])][0]
    return fmt


def bytes_per_pixel(fmt):
    f = typed_name(fmt)
    return DXGI[BY_NAME[f]][1] if f in BY_NAME and not DXGI[BY_NAME[f]][2] else None


def decode_pixels(fmt, b, w, h):
    """Tightly packed rows of an uncompressed format -> dict of channel -> numpy array (h, w), exact."""
    f = typed_name(fmt)
    if f == 'R16G16B16A16_FLOAT':
        a = np.frombuffer(b, '<f2').reshape(h, w, 4)
        return dict(zip('RGBA', np.moveaxis(a, 2, 0)))
    if f == 'R32G32B32A32_FLOAT':
        a = np.frombuffer(b, '<f4').reshape(h, w, 4)
        return dict(zip('RGBA', np.moveaxis(a, 2, 0)))
    if f == 'R32G32_FLOAT':
        a = np.frombuffer(b, '<f4').reshape(h, w, 2)
        return {'R': a[..., 0], 'G': a[..., 1]}
    if f in ('R16G16_FLOAT',):
        a = np.frombuffer(b, '<f2').reshape(h, w, 2)
        return {'R': a[..., 0], 'G': a[..., 1]}
    if f in ('R16_FLOAT',):
        return {'R': np.frombuffer(b, '<f2').reshape(h, w)}
    if f in ('R32_FLOAT', 'D32_FLOAT'):
        return {'Z' if f.startswith('D') else 'R': np.frombuffer(b, '<f4').reshape(h, w)}
    if f in ('D32_FLOAT_S8X24_UINT',):
        a = np.frombuffer(b, '<u4').reshape(h, w, 2)
        return {'Z': a[..., 0].view('<f4'), 'S': (a[..., 1] & 0xFF).astype(np.uint32)}
    if f in ('D24_UNORM_S8_UINT',):
        a = np.frombuffer(b, '<u4').reshape(h, w)
        return {'Z': ((a & 0xFFFFFF) / 16777215.0).astype(np.float32), 'S': (a >> 24).astype(np.uint32)}
    if f == 'R11G11B10_FLOAT':
        a = np.frombuffer(b, '<u4').reshape(h, w).astype(np.int64)
        return {'R': _half_from_bits(a & 0x7FF, 6), 'G': _half_from_bits((a >> 11) & 0x7FF, 6),
                'B': _half_from_bits((a >> 22) & 0x3FF, 5)}
    if f == 'R9G9B9E5_SHAREDEXP':
        # three 9-bit mantissas sharing the top 5 bits as exponent (bias 15, no implicit 1): Unity's GI irradiance
        a = np.frombuffer(b, '<u4').reshape(h, w).astype(np.int64)
        k = np.exp2((a >> 27).astype(np.float64) - 24)
        return {c: (((a >> s) & 511) * k).astype(np.float32) for c, s in zip('RGB', (0, 9, 18))}
    if f == 'R10G10B10A2_UNORM':
        a = np.frombuffer(b, '<u4').reshape(h, w)
        return {'R': ((a & 1023) / 1023.0).astype(np.float32), 'G': (((a >> 10) & 1023) / 1023.0).astype(np.float32),
                'B': (((a >> 20) & 1023) / 1023.0).astype(np.float32), 'A': ((a >> 30) / 3.0).astype(np.float32)}
    if f in ('R8G8B8A8_UNORM', 'B8G8R8A8_UNORM', 'B8G8R8X8_UNORM', 'R8G8B8A8_UINT'):
        a = np.frombuffer(b, np.uint8).reshape(h, w, 4).astype(np.float32) / 255.0
        order = 'BGRA' if f.startswith('B') else 'RGBA'
        out = {c: a[..., i] for i, c in enumerate(order)}
        if f == 'B8G8R8X8_UNORM':
            out.pop('A')
        return out
    if f == 'R16G16B16A16_UNORM':
        a = np.frombuffer(b, '<u2').reshape(h, w, 4).astype(np.float32) / 65535.0
        return dict(zip('RGBA', np.moveaxis(a, 2, 0)))
    if f in ('R16G16_UNORM',):
        a = np.frombuffer(b, '<u2').reshape(h, w, 2).astype(np.float32) / 65535.0
        return {'R': a[..., 0], 'G': a[..., 1]}
    if f in ('R16_UNORM', 'D16_UNORM'):
        return {'Z' if f.startswith('D') else 'R': np.frombuffer(b, '<u2').reshape(h, w).astype(np.float32) / 65535.0}
    if f in ('R8G8_UNORM',):
        a = np.frombuffer(b, np.uint8).reshape(h, w, 2).astype(np.float32) / 255.0
        return {'R': a[..., 0], 'G': a[..., 1]}
    if f in ('R8_UNORM', 'R8_UINT', 'A8_UNORM'):
        return {'A' if f.startswith('A') else 'R': np.frombuffer(b, np.uint8).reshape(h, w).astype(np.float32) / 255.0}
    if f == 'R32_UINT':
        return {'R': np.frombuffer(b, '<u4').reshape(h, w)}
    if f == 'R32G32B32A32_UINT':
        a = np.frombuffer(b, '<u4').reshape(h, w, 4)
        return dict(zip('RGBA', np.moveaxis(a, 2, 0)))
    raise ValueError('no decoder for %s' % fmt)


# 8-bit formats a PNG holds exactly: typed name -> (PIL mode, channel order of the stored bytes)
PNG_EXACT = {'R8G8B8A8_UNORM': ('RGBA', 'RGBA'), 'B8G8R8A8_UNORM': ('RGBA', 'BGRA'), 'B8G8R8X8_UNORM': ('RGB', 'BGRX'),
             'R8_UNORM': ('L', 'R'), 'A8_UNORM': ('L', 'A'), 'R8G8B8A8_UINT': ('RGBA', 'RGBA')}


def exact_png(dds, mip=0, slice_=0):
    """One surface of an 8-bit format as a PIL image holding the stored bytes (BGRA reordered to RGBA)."""
    from PIL import Image
    mode, order = PNG_EXACT[typed_name(dds.format)]
    w, h, d = dds.mip_size(mip)
    a = np.frombuffer(dds.surface(mip, slice_), np.uint8).reshape(h * d, w, len(order))
    if order == 'BGRA':
        a = a[..., [2, 1, 0, 3]]
    elif order == 'BGRX':
        a = a[..., [2, 1, 0]]
    return Image.fromarray(np.ascontiguousarray(a if len(order) > 1 else a[..., 0]), mode)


def single_surface_dds(dds, mip=0, slice_=0):
    """A one-surface DDS (DX10 header, typed format) holding (mip, slice) of dds, for Pillow."""
    w, h, _ = dds.mip_size(mip)
    fmt = TYPED.get(dds.dxgi, dds.dxgi)
    data = dds.surface(mip, slice_)
    hdr = bytearray(148)
    hdr[:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, h, w, len(data), 0, 1)
    struct.pack_into('<II4s', hdr, 76, 32, 0x4, b'DX10')
    struct.pack_into('<I', hdr, 108, 0x1000)
    struct.pack_into('<5I', hdr, 128, fmt, 3, 0, 1, 0)
    return bytes(hdr) + data


def to_image(dds, mip=0, slice_=0, scale=1.0):
    """A PIL image of one surface: BCn through Pillow, uncompressed formats clamped to 8-bit after * scale.
    Raw values: no sRGB conversion either way, so sRGB textures look as the bytes are stored."""
    from PIL import Image
    if dds.bc:
        im = Image.open(io.BytesIO(single_surface_dds(dds, mip, slice_)))
        im.load()
        return im
    ch = decode_raw(dds, mip, slice_)
    if 'Z' in ch:
        z = ch['Z'].astype(np.float32)
        lo, hi = float(np.min(z)), float(np.max(z))
        v = (z - lo) / (hi - lo) if hi > lo else np.zeros_like(z)
        return Image.fromarray((np.clip(v, 0, 1) * 255 + 0.5).astype(np.uint8), 'L')
    names = [c for c in 'RGBA' if c in ch]
    if names == ['R']:
        return Image.fromarray((np.clip(ch['R'].astype(np.float32) * scale, 0, 1) * 255 + 0.5).astype(np.uint8), 'L')
    if names == ['A']:
        return Image.fromarray((np.clip(ch['A'].astype(np.float32), 0, 1) * 255 + 0.5).astype(np.uint8), 'L')
    h, w = ch[names[0]].shape
    out = np.zeros((h, w, 4), np.float32)
    out[..., 3] = 1.0
    for i, c in enumerate('RGBA'):
        if c in ch:
            out[..., i] = ch[c].astype(np.float32) * (scale if c != 'A' else 1.0)
    return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA')


# ---- OpenEXR (scanline, ZIP) -----------------------------------------------------------------------------------
def _attr(name, typ, data):
    return name.encode() + b'\0' + typ.encode() + b'\0' + struct.pack('<i', len(data)) + data


def _zip_block(raw):
    a = np.frombuffer(raw, np.uint8)
    re_ = np.concatenate([a[0::2], a[1::2]]).astype(np.int16)
    d = re_.copy()
    d[1:] = (re_[1:] - re_[:-1] + 384) & 0xFF
    comp = zlib.compress(d.astype(np.uint8).tobytes(), 6)
    return comp if len(comp) < len(raw) else raw


def write_exr(path, channels):
    """channels: name -> 2D array (float16 -> HALF, float32 -> FLOAT, uint32 -> UINT), all the same shape."""
    names = sorted(channels)
    h, w = channels[names[0]].shape
    arrs, chl = [], b''
    for n in names:
        a = np.ascontiguousarray(channels[n])
        if a.dtype == np.float16:
            t, a = 1, a.astype('<f2')
        elif a.dtype == np.uint32:
            t, a = 0, a.astype('<u4')
        else:
            t, a = 2, a.astype('<f4')
        arrs.append(a)
        chl += n.encode() + b'\0' + struct.pack('<iB3xii', t, 0, 1, 1)
    chl += b'\0'
    box = struct.pack('<4i', 0, 0, w - 1, h - 1)
    hdr = (b'\x76\x2f\x31\x01' + struct.pack('<I', 2) + _attr('channels', 'chlist', chl) +
           _attr('compression', 'compression', b'\x03') + _attr('dataWindow', 'box2i', box) +
           _attr('displayWindow', 'box2i', box) + _attr('lineOrder', 'lineOrder', b'\x00') +
           _attr('pixelAspectRatio', 'float', struct.pack('<f', 1.0)) +
           _attr('screenWindowCenter', 'v2f', struct.pack('<2f', 0, 0)) +
           _attr('screenWindowWidth', 'float', struct.pack('<f', 1.0)) + b'\0')
    blocks = []
    for y in range(0, h, 16):
        rows = []
        for yy in range(y, min(h, y + 16)):
            for a in arrs:
                rows.append(a[yy].tobytes())
        blocks.append((y, _zip_block(b''.join(rows))))
    table_size = 8 * len(blocks)
    off = len(hdr) + table_size
    offsets = []
    for y, data in blocks:
        offsets.append(off)
        off += 8 + len(data)
    # a new file replaces the old one: never written in place, so a hard link (rdcdigest --dedup) is never written through
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(hdr)
        f.write(struct.pack('<%dQ' % len(offsets), *offsets))
        for y, data in blocks:
            f.write(struct.pack('<ii', y, len(data)))
            f.write(data)
    os.replace(tmp, path)


def read_exr(path):
    """Reads what write_exr writes (scanline, ZIP or none): name -> 2D array. For checks and the query CLI."""
    b = open(path, 'rb').read()
    if b[:4] != b'\x76\x2f\x31\x01':
        raise ValueError('not an EXR')
    p, attrs = 8, {}
    while b[p] != 0:
        e = b.index(b'\0', p)
        name = b[p:e].decode()
        e2 = b.index(b'\0', e + 1)
        typ = b[e + 1:e2].decode()
        n = struct.unpack_from('<i', b, e2 + 1)[0]
        attrs[name] = (typ, b[e2 + 5:e2 + 5 + n])
        p = e2 + 5 + n
    p += 1
    chl, chans = attrs['channels'][1], []
    q = 0
    while chl[q] != 0:
        e = chl.index(b'\0', q)
        t = struct.unpack_from('<i', chl, e + 1)[0]
        chans.append((chl[q:e].decode(), t))
        q = e + 17
    x0, y0, x1, y1 = struct.unpack('<4i', attrs['dataWindow'][1])
    w, h = x1 - x0 + 1, y1 - y0 + 1
    comp = attrs['compression'][1][0]
    lines = 16 if comp == 3 else (1 if comp == 0 else None)
    if lines is None:
        raise ValueError('compression %d not supported' % comp)
    nblocks = (h + lines - 1) // lines
    offs = struct.unpack_from('<%dQ' % nblocks, b, p)
    dt = {0: '<u4', 1: '<f2', 2: '<f4'}
    out = {n: np.zeros((h, w), dt[t]) for n, t in chans}
    for o in offs:
        y, n = struct.unpack_from('<ii', b, o)
        data = b[o + 8:o + 8 + n]
        nl = min(lines, h - (y - y0))
        raw_size = sum(w * nl * (2 if t == 1 else 4) for _, t in chans)
        if comp == 3 and n < raw_size:
            d = np.frombuffer(zlib.decompress(data), np.uint8).astype(np.int32)
            r = np.cumsum(np.concatenate([d[:1], d[1:] - 128])) & 0xFF
            r = r.astype(np.uint8)
            half = (len(r) + 1) // 2
            raw = np.empty(len(r), np.uint8)
            raw[0::2], raw[1::2] = r[:half], r[half:]
            data = raw.tobytes()
        q = 0
        for yy in range(nl):
            for name, t in chans:
                sz = w * (2 if t == 1 else 4)
                out[name][y - y0 + yy] = np.frombuffer(data[q:q + sz], dt[t])
                q += sz
    return out
