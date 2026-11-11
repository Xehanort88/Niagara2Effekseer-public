"""Windows-only shader tools behind a result cache: d3dcompiler_47's D3DCompile and D3DDisassemble, and UE's
decompress_shader.exe (Oodle) for compressed shader-archive entries.

On Windows each call runs the real DLL or program exactly as the callers did before, and also stores its result
under out/cache/wincache/<kind>/<sha256 of the input>.json (or N2E_WINCACHE). Elsewhere (a Linux cloud session) the
stored result is read back, and a missing one raises Unavailable with what to do. So a step run once on Windows runs
the same in the cloud: `tools/run_all_tests.py --trace-files` lists each stored result a suite used, and
tools/testdata/pack.py uploads them with the rest of the test data (README § Test data for a cloud session).

Each function returns what the DLL or program gave, undecoded beyond latin-1 text; callers keep their own trimming.
"""
import base64
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get('N2E_WINCACHE') or ROOT / 'out' / 'cache' / 'wincache')
ON_WINDOWS = sys.platform == 'win32'


class Unavailable(RuntimeError):
    """The Windows tool cannot run here and no stored result exists for this input."""


def _entry(kind, *parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode('utf-8'))
        h.update(b'\0')
    return CACHE / kind / (h.hexdigest() + '.json')


def _note(path):
    """Tell a --trace-files run that this result was needed, so it is packed although Windows wrote it, not read it."""
    trace = os.environ.get('N2E_TRACE_FILE')
    if trace:
        with open(trace, 'a', encoding='utf-8') as f:
            f.write('read\t%s\n' % os.path.abspath(path))


def _store(path, record):
    tmp = path.with_suffix('.%d.tmp' % os.getpid())       # whole file, then renamed: parallel runs never read half
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record), encoding='utf-8')
        os.replace(tmp, path)
    except OSError:                                       # read-only out/, or a parallel writer holds the target:
        try:                                              # only the cache is lost, not the result
            tmp.unlink()
        except OSError:
            pass
        return
    _note(path)


def _load(path, what):
    if not path.is_file():
        raise Unavailable('%s needs Windows, and no result for this input is stored (%s). Run the same step once on '
                          'Windows; tools/testdata/pack.py then uploads the stored result with the test data.'
                          % (what, path))
    return json.loads(path.read_text(encoding='utf-8'))


def _blob(ptr):
    """The bytes of an ID3DBlob (vtable: 3 GetBufferPointer, 4 GetBufferSize, 2 Release), released after."""
    if not ptr:
        return b''
    vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    data = ctypes.string_at(ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(ptr),
                            ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(ptr))
    ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(ptr)
    return data


def _d3d():
    return ctypes.WinDLL('d3dcompiler_47.dll')


def disassemble(code, flags=0x80):
    """D3DDisassemble of DXBC `code` as latin-1 text (0x80 = D3D_DISASM_PRINT_HEX_LITERALS)."""
    entry = _entry('disassemble', code, flags)
    if not ON_WINDOWS:
        return _load(entry, 'D3DDisassemble (d3dcompiler_47.dll)')['text']
    fn = _d3d().D3DDisassemble
    fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    out = ctypes.c_void_p()
    hr = fn(code, len(code), flags, None, ctypes.byref(out))
    if hr != 0:
        raise OSError('D3DDisassemble failed 0x%08x' % (hr & 0xFFFFFFFF))
    text = _blob(out.value).decode('latin1')
    _store(entry, {'text': text})
    return text


def compile_hlsl(source, profile, entry_point='main', flags=0, name=''):
    """D3DCompile of HLSL `source` (bytes) -> (ok, bytecode, messages). `name` only labels the messages, so it is not
    part of the stored key: messages read back name the file as it was on Windows."""
    entry = _entry('compile', source, entry_point, profile, flags)
    if not ON_WINDOWS:
        r = _load(entry, 'D3DCompile (d3dcompiler_47.dll)')
        return r['ok'], base64.b64decode(r['code']), r['messages']
    fn = _d3d().D3DCompile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    out, err = ctypes.c_void_p(), ctypes.c_void_p()
    hr = fn(source, len(source), name.encode(), None, None, entry_point.encode(), profile.encode(), flags, 0,
            ctypes.byref(out), ctypes.byref(err))
    ok, code, messages = hr == 0, _blob(out.value), _blob(err.value).decode('latin1')
    _store(entry, {'ok': ok, 'code': base64.b64encode(code).decode('ascii'), 'messages': messages})
    return ok, code, messages


def decompress(program, raw, size):
    """decompress_shader.exe <raw file> <size>: the bytes of the .dxbc/.dxil it writes, or None when it writes none."""
    entry = _entry('decompress', raw, size)
    if not ON_WINDOWS:
        r = _load(entry, 'decompress_shader.exe')
        return None if r['data'] is None else base64.b64decode(r['data'])
    with tempfile.TemporaryDirectory(prefix='gpulink_') as tmp:
        p = Path(tmp) / 'shader.bin'
        p.write_bytes(raw)
        subprocess.run([str(program), str(p), str(size)], cwd=tmp, capture_output=True, text=True)
        out = next((q for q in Path(tmp).iterdir() if q.suffix in ('.dxbc', '.dxil')), None)
        data = out.read_bytes() if out is not None else None
    _store(entry, {'data': None if data is None else base64.b64encode(data).decode('ascii')})
    return data
