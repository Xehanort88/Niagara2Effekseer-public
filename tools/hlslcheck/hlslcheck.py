"""Check that Effekseer file materials (.efkmat) compile as DirectX HLSL, the way the Effekseer editor builds them.

The editor (Windows) compiles a material's code as DirectX 11 HLSL; RPGReactor compiles the same code as WebGL
GLSL. Effekseer's generator only swaps the $F1$..$F4$, $TIME$ and $TEX_P/S$ tokens and defines MOD, FRAC and LERP,
so GLSL-only code (mix, fract, inversesqrt, lessThan, a one-value constructor such as float4(0.0)) works in the
game but fails in the editor, which then draws the node's plain texture on a quad. This tool wraps each
material's code the way the HLSL generator does (built-in inputs, custom data, uniforms, textures) and compiles
it with Windows' d3dcompiler_47.dll (ps_4_0). Windows only; nothing to install.

Usage: python tools/hlslcheck/hlslcheck.py <file.efkmat | folder> ...   (folders are searched recursively)
Exit code 1 if any material fails. A material that uses Effekseer's built-in gradient/noise/HSV functions is
reported as SKIP (those function bodies are not reproduced here).
"""
import argparse
import ctypes
import re
import struct
import sys
from pathlib import Path

TYPES = {1: 'float', 2: 'float2', 3: 'float3', 4: 'float4'}


def parse_efkmat(data):
    """Version, custom data sizes, texture slots, uniform names, required built-ins and code of an .efkmat."""
    if data[:4] != b'EFKM':
        raise ValueError('not an efkmat file')
    version = struct.unpack_from('<i', data, 4)[0]
    off = 16
    info = {'version': version, 'cd1': 0, 'cd2': 0, 'textures': [], 'uniforms': [], 'required': [], 'code': None}
    while off + 8 <= len(data):
        tag = data[off:off + 4]
        size = struct.unpack_from('<i', data, off + 4)[0]
        body = off + 8
        if tag == b'PRM_':
            p = body
            i32 = lambda: struct.unpack_from('<i', data, p)[0]

            def string():
                nonlocal p
                n = i32()
                p += 4
                s = data[p:p + n].split(b'\0', 1)[0].decode('utf-8', 'replace')
                p += n
                return s
            _shading, _normal, _refraction, info['cd1'], info['cd2'] = struct.unpack_from('<5i', data, p)
            p += 20
            if version >= 1703:
                n = i32()
                p += 4
                info['required'] = list(struct.unpack_from('<%di' % n, data, p))
                p += 4 * n
            n = i32()
            p += 4
            for _ in range(n):
                name = string()
                if version >= 3:
                    name = string()
                string()                                   # default path
                index = i32()
                p += 4 * 5                                 # index, priority, param, colour type, sampler
                info['textures'].append({'name': name, 'index': index})
            n = i32()
            p += 4
            for _ in range(n):
                name = string()
                if version >= 3:
                    name = string()
                p += 4 * 3 + 4 * 4                         # offset, priority, type, default values
                info['uniforms'].append(name)
        elif tag == b'GENE':
            n = struct.unpack_from('<i', data, body)[0]
            info['code'] = data[body + 4:body + 4 + n].split(b'\0', 1)[0].decode('utf-8', 'replace')
        off = body + size
    if info['code'] is None:
        raise ValueError('no GENE (code) chunk')
    return info


def to_hlsl(info):
    """The material code inside a pixel shader declared like the HLSL generator's (sprite, unlit or lit)."""
    code = info['code']
    for a, b in (('$F1$', 'float'), ('$F2$', 'float2'), ('$F3$', 'float3'), ('$F4$', 'float4'),
                 ('$TIME$', 'predefined_uniform.x'), ('$LOCALTIME$', 'predefined_uniform.w'), ('$UV$', 'uv'),
                 ('$MOD', 'fmod'), ('$PARTICLE_TIME_NORMALIZED$', 'particleTime.x'),
                 ('$PARTICLE_TIME_SECONDS$', 'particleTime.y')):
        code = code.replace(a, b)
    decl = []
    for slot, t in enumerate(info['textures']):
        name = t['name']
        code = code.replace('$TEX_P%d$' % t['index'], '%s_texture.Sample(%s_sampler,GetUV(' % (name, name))
        code = code.replace('$TEX_S%d$' % t['index'], '))')
        decl.append('Texture2D %s_texture : register(t%d);\nSamplerState %s_sampler : register(s%d);' % (name, slot, name, slot))
    for k, u in enumerate(info['uniforms']):
        decl.append('float4 %s : register(c%d);' % (u, 8 + k))
    cd = ''
    for n, c in ((1, info['cd1']), (2, info['cd2'])):
        if c:
            cd += '  %s customData%d = Input.CD%d%s;\n' % (TYPES[c], n, n, ['', '.x', '.xy', '.xyz', ''][c])
    return '\n'.join([
        '#define MOD fmod', '#define FRAC frac', '#define LERP lerp',
        'float4 predefined_uniform : register(c0);', 'float4 cameraPosition : register(c1);',
        'float4 lightDirection : register(c2);', 'float4 lightColor : register(c3);',
        'float4 lightAmbientColor : register(c4);'] + decl + [
        'float2 GetUV(float2 uv) { return uv; }',
        'float2 GetUVBack(float2 uv) { return uv; }',
        'float CalcDepthFade(float2 screenUV, float meshZ, float softParticleParam) { return 1.0f; }',
        'struct PS_Input { float4 PosVS : SV_POSITION; float4 VColor : COLOR; float2 UV1 : TEXCOORD0;',
        '  float2 UV2 : TEXCOORD1; float3 WorldP : TEXCOORD2; float3 WorldN : TEXCOORD3; float3 WorldT : TEXCOORD4;',
        '  float3 WorldB : TEXCOORD5; float4 PosP : TEXCOORD6; float4 CD1 : TEXCOORD7; float4 CD2 : TEXCOORD8;',
        '  float2 ParticleTime : TEXCOORD9; };',
        'float4 main(const PS_Input Input, bool face : SV_IsFrontFace) : SV_Target {',
        '  float2 uv1 = Input.UV1; float2 uv2 = Input.UV2; float3 worldPos = Input.WorldP;',
        '  float3 worldNormal = Input.WorldN; float3 worldBinormal = Input.WorldB; float3 worldTangent = Input.WorldT;',
        '  float3 objectScale = float3(1.0, 1.0, 1.0); float3 pixelNormalDir = worldNormal;',
        '  float4 vcolor = Input.VColor; float2 particleTime = Input.ParticleTime; bool isFrontFace = face;',
        '  float2 screenUV = Input.PosP.xy / Input.PosP.w; float meshZ = Input.PosP.z / Input.PosP.w;',
        cd + code,
        '  return float4(emissive + baseColor, opacity * opacityMask + refraction * 0.0 + metallic * 0.0'
        ' + roughness * 0.0 + ambientOcclusion * 0.0 + worldPositionOffset.x * 0.0);',
        '}'])


def _blob_text(ptr):
    if not ptr:
        return ''
    vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    get_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])
    get_size = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])
    return ctypes.string_at(get_ptr(ptr), get_size(ptr)).decode('latin1')


def compile_hlsl(src, name='material'):
    """(ok, messages) from d3dcompiler_47 for a ps_4_0 'main'."""
    d3d = ctypes.WinDLL('d3dcompiler_47.dll')
    fn = d3d.D3DCompile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    b = src.encode('utf-8')
    out, err = ctypes.c_void_p(), ctypes.c_void_p()
    hr = fn(b, len(b), name.encode(), None, None, b'main', b'ps_4_0', 0, 0, ctypes.byref(out), ctypes.byref(err))
    return hr == 0, _blob_text(err.value)


def check(path):
    """('ok' | 'FAIL' | 'SKIP', first messages)."""
    info = parse_efkmat(Path(path).read_bytes())
    if info['required']:
        return 'SKIP', 'uses built-in gradient/noise/HSV functions (not reproduced here)'
    ok, msg = compile_hlsl(to_hlsl(info), str(path))
    errors = [line.split(': ', 1)[-1] for line in msg.splitlines() if 'error' in line]
    return ('ok' if ok else 'FAIL'), ' | '.join(errors[:3])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('paths', nargs='+')
    a = ap.parse_args(argv)
    if sys.platform != 'win32':
        print('hlslcheck needs Windows (d3dcompiler_47.dll)')
        return 2
    files = []
    for p in map(Path, a.paths):
        files += sorted(p.rglob('*.efkmat')) if p.is_dir() else [p]
    failed = 0
    for f in files:
        status, msg = check(f)
        failed += status == 'FAIL'
        print('%-4s %s%s' % (status, f, ('  ' + msg[:200]) if msg else ''))
    print('HLSLCHECK: %d material(s), %d failed' % (len(files), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
