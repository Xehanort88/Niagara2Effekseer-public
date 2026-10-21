"""Check that Effekseer file materials (.efkmat) compile as DirectX HLSL, the way the Effekseer editor builds them.

The editor (Windows) compiles a material's code as DirectX 11 HLSL; RPGReactor compiles the same code as WebGL
GLSL. Effekseer's generator only swaps the $F1$..$F4$, $TIME$ and $TEX_P/S$ tokens and defines MOD, FRAC and LERP,
so GLSL-only code (mix, fract, inversesqrt, lessThan, a one-value constructor such as float4(0.0)) works in the
game but fails in the editor, which then draws the node's plain texture on a quad. This tool wraps each
material's code the way the HLSL generator does (built-in inputs, custom data, uniforms, textures) and compiles
it with Windows' d3dcompiler_47.dll: the pixel shader (ps_4_0) and the sprite and model vertex shaders (vs_4_0),
since the editor builds the code into all of them and drops the material if any fails. The vertex shaders
declare their own locals, so code that declares one of them fails there only: the model vertex shader's
'float4 uv' (redefinition of 'uv') turned every OT0 port into a plain quad in the editor (2026-10-11).
Every shader declares the background and depth textures (efk_background_texture/_sampler, efk_depth_texture/
_sampler) after the material's own, as the generator does. A material flagged as refraction (PRM_ hasRefraction)
is drawn twice, so its pixel shader is also compiled a second time with the generator's refraction ending (pass 0:
cameraMat declared, the background read at the refracted screen position) besides the normal one (pass 1).
Windows only; nothing to install.

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
    info = {'version': version, 'cd1': 0, 'cd2': 0, 'textures': [], 'uniforms': [], 'required': [], 'code': None,
            'refraction': False}
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
            _shading, _normal, refraction, info['cd1'], info['cd2'] = struct.unpack_from('<5i', data, p)
            info['refraction'] = refraction != 0
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


# The end of the pixel shader that draws a refraction material's pass 0, as Effekseer 1.80.7's HLSL generator
# (DirectX 11) writes it: HLSLGenerator/ShaderGenerator.cpp GetMaterialPS_Suf2_Refraction.
REFRACTION_SUFFIX = """
  float airRefraction = 1.0;
  float3 dir = mul((float3x3)cameraMat, pixelNormalDir);
  dir.y = -dir.y;
  float2 distortUV = dir.xy * (refraction - airRefraction);
  distortUV += screenUV;
  distortUV = GetUVBack(distortUV);
  float4 bg = efk_background_texture.Sample(efk_background_sampler, distortUV);
  float4 Output = bg;
  if(opacityMask <= 0.0) discard;
  if(opacity <= 0.0) discard;
  return Output;"""


def builtin_textures(slot):
    """The background and depth textures every generated shader declares after the material's own textures
    (ShaderGenerator::GenerateShader, 'background' / 'depth')."""
    return ['Texture2D efk_background_texture : register(t%d);' % slot,
            'SamplerState efk_background_sampler : register(s%d);' % slot,
            'Texture2D efk_depth_texture : register(t%d);' % (slot + 1),
            'SamplerState efk_depth_sampler : register(s%d);' % (slot + 1)]


def to_hlsl(info, refraction=False):
    """The material code inside a pixel shader declared like the HLSL generator's (sprite, unlit or lit). With
    refraction, the pass-0 shader of a refraction material: cameraMat declared and the generator's refraction ending."""
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
    decl += builtin_textures(len(info['textures']))
    if refraction:
        decl.append('float4x4 cameraMat : register(c5);')
    for k, u in enumerate(info['uniforms']):
        decl.append('float4 %s : register(c%d);' % (u, 9 + k))
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
        REFRACTION_SUFFIX if refraction else
        '  return float4(emissive + baseColor, opacity * opacityMask + refraction * 0.0 + metallic * 0.0'
        ' + roughness * 0.0 + ambientOcclusion * 0.0 + worldPositionOffset.x * 0.0);',
        '}'])


def to_hlsl_vs(info, model=False):
    """The material code inside the sprite (or model) vertex shader as Effekseer 1.80.3's HLSL generator writes it
    (HLSLGenerator/ShaderGenerator.cpp: material_sprite_vs_suf1 / model_vs_suf1, 40 model instances): textures
    read with SampleLevel, custom data from the vertex (sprite) or a per-instance array (model), and the locals
    those wrappers declare before the code (the model one has 'float4 uv', 'matModel', 'modelColor', ...)."""
    code = info['code']
    for a, b in (('$F1$', 'float'), ('$F2$', 'float2'), ('$F3$', 'float3'), ('$F4$', 'float4'),
                 ('$TIME$', 'predefined_uniform.x'), ('$EFFECTSCALE$', 'predefined_uniform.y'),
                 ('$LOCALTIME$', 'predefined_uniform.w'), ('$UV$', 'uv'), ('$MOD', 'fmod'),
                 ('$PARTICLE_TIME_NORMALIZED$', 'particleTime.x'), ('$PARTICLE_TIME_SECONDS$', 'particleTime.y')):
        code = code.replace(a, b)
    decl = []
    for slot, t in enumerate(info['textures']):
        name = t['name']
        code = code.replace('$TEX_P%d$' % t['index'], '%s_texture.SampleLevel(%s_sampler,GetUV(' % (name, name))
        code = code.replace('$TEX_S%d$' % t['index'], '),0)')
        decl.append('Texture2D %s_texture : register(t%d);\nSamplerState %s_sampler : register(s%d);' % (name, slot, name, slot))
    decl += builtin_textures(len(info['textures']))
    reg = (7 + 40 * 7) if model else 11
    for n, c in ((1, info['cd1']), (2, info['cd2'])):
        if c and model:
            decl.append('float4 customData%d_[40] : register(c%d);' % (n, reg))
            reg += 40
    for k, u in enumerate(info['uniforms']):
        decl.append('float4 %s : register(c%d);' % (u, reg + k))
    cd_in, cd_out, cd = '', '', ''
    for n, c in ((1, info['cd1']), (2, info['cd2'])):
        if c:
            cd_in += '  %s CustomData%d : TEXCOORD%d;' % (TYPES[c], n, 2 + n)
            cd_out += '  %s CustomData%d : TEXCOORD%d;' % (TYPES[c], n, 7 + n)
            src = 'customData%d_[Input.Index]%s' % (n, ['', '.x', '.xy', '.xyz', ''][c]) if model else 'Input.CustomData%d' % n
            cd += '  %s customData%d = %s; Output.CustomData%d = customData%d;\n' % (TYPES[c], n, src, n, n)
    out = ['struct VS_Output { float4 Position : SV_POSITION; float4 VColor : COLOR; float2 UV1 : TEXCOORD0;',
           '  float2 UV2 : TEXCOORD1; float3 WorldP : TEXCOORD2; float3 WorldN : TEXCOORD3; float3 WorldT : TEXCOORD4;',
           '  float3 WorldB : TEXCOORD5; float4 PosP : TEXCOORD6; float2 ParticleTime : TEXCOORD7;' + cd_out + ' };']
    if model:
        head = ['struct VS_Input { float3 Pos : POSITION0; float3 Normal : NORMAL0; float3 Binormal : NORMAL1;',
                '  float3 Tangent : NORMAL2; float2 UV1 : TEXCOORD0; float2 UV2 : TEXCOORD1; float4 Color : NORMAL3;',
                '  uint Index : SV_InstanceID; };'] + out + [
                'float4x4 mCameraProj : register(c0);', 'float4x4 mModel[40] : register(c4);',
                'float4 fUV[40] : register(c164);', 'float4 fModelColor[40] : register(c204);',
                'float4 fModelParticleTime[40] : register(c244);', 'float4 mUVInversed : register(c284);',
                'float4 predefined_uniform : register(c285);', 'float4 cameraPosition : register(c286);']
        body = ['  float4x4 matModel = mModel[Input.Index]; float4 uv = fUV[Input.Index];',
                '  float4 modelColor = fModelColor[Input.Index] * Input.Color;',
                '  float2 particleTime = fModelParticleTime[Input.Index].xy; VS_Output Output = (VS_Output)0;',
                '  float4 localPosition = { Input.Pos.x, Input.Pos.y, Input.Pos.z, 1.0 };',
                '  float3x3 matRotModel = (float3x3)matModel; float3 worldPos = mul(matModel, localPosition).xyz;',
                '  float3 worldNormal = normalize(mul(matRotModel, Input.Normal));',
                '  float3 worldBinormal = normalize(mul(matRotModel, Input.Binormal));',
                '  float3 worldTangent = normalize(mul(matRotModel, Input.Tangent)); float3 objectScale = float3(1.0, 1.0, 1.0);',
                '  float2 uv1 = Input.UV1 * uv.zw + uv.xy; float2 uv2 = Input.UV2 * uv.zw + uv.xy;',
                '  float3 pixelNormalDir = worldNormal; float4 vcolor = modelColor;']
        tail = '  worldPos = worldPos + worldPositionOffset; Output.Position = mul(mCameraProj, float4(worldPos, 1.0));'
    else:
        head = ['struct VS_Input { float3 Pos : POSITION0; float4 Color : NORMAL0; float4 Normal : NORMAL1;',
                '  float4 Tangent : NORMAL2; float2 UV1 : TEXCOORD0; float2 UV2 : TEXCOORD1; float2 ParticleTime : TEXCOORD2;'
                + cd_in + ' };'] + out + [
                'float4x4 mCamera : register(c0);', 'float4x4 mProj : register(c4);', 'float4 mUVInversed : register(c8);',
                'float4 predefined_uniform : register(c9);', 'float4 cameraPosition : register(c10);']
        body = ['  VS_Output Output = (VS_Output)0; float3 worldPos = Input.Pos;',
                '  float3 worldNormal = (Input.Normal - float3(0.5, 0.5, 0.5)) * 2.0;',
                '  float3 worldTangent = (Input.Tangent - float3(0.5, 0.5, 0.5)) * 2.0;',
                '  float3 worldBinormal = cross(worldNormal, worldTangent); float3 objectScale = float3(1.0, 1.0, 1.0);',
                '  float2 uv1 = Input.UV1; float2 uv2 = Input.UV2; float3 pixelNormalDir = worldNormal;',
                '  float4 vcolor = Input.Color; Output.ParticleTime = Input.ParticleTime;',
                '  float2 particleTime = Input.ParticleTime.xy;']
        tail = ('  worldPos = worldPos + worldPositionOffset; float4 cameraPos = mul(mCamera, float4(worldPos, 1.0));'
                ' Output.Position = mul(mProj, cameraPos / cameraPos.w);')
    return '\n'.join(['#define MOD fmod', '#define FRAC frac', '#define LERP lerp'] + head + decl + [
        'float2 GetUV(float2 uv) { uv.y = mUVInversed.x + mUVInversed.y * uv.y; return uv; }',
        'float2 GetUVBack(float2 uv) { uv.y = mUVInversed.z + mUVInversed.w * uv.y; return uv; }',
        'float CalcDepthFade(float2 screenUV, float meshZ, float softParticleParam) { return 1.0f; }',
        'VS_Output main(const VS_Input Input) {'] + body + [
        '  bool isFrontFace = false; float2 screenUV = float2(0.0, 0.0); float meshZ = 0.0f;',
        cd + code, tail, '  Output.VColor = vcolor + float4(baseColor + emissive, opacity + opacityMask + refraction'
        ' + metallic + roughness + ambientOcclusion) * 0.0; Output.UV1 = uv1; Output.UV2 = uv2;',
        '  return Output;', '}'])


def _blob_text(ptr):
    if not ptr:
        return ''
    vt = ctypes.cast(ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    get_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])
    get_size = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])
    return ctypes.string_at(get_ptr(ptr), get_size(ptr)).decode('latin1')


def compile_hlsl(src, name='material', profile='ps_4_0'):
    """(ok, messages) from d3dcompiler_47 for a 'main' (ps_4_0, or vs_4_0 for the vertex shaders)."""
    d3d = ctypes.WinDLL('d3dcompiler_47.dll')
    fn = d3d.D3DCompile
    fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    b = src.encode('utf-8')
    out, err = ctypes.c_void_p(), ctypes.c_void_p()
    hr = fn(b, len(b), name.encode(), None, None, b'main', profile.encode(), 0, 0, ctypes.byref(out), ctypes.byref(err))
    return hr == 0, _blob_text(err.value)


def check(path):
    """('ok' | 'FAIL' | 'SKIP', first messages)."""
    info = parse_efkmat(Path(path).read_bytes())
    if info['required']:
        return 'SKIP', 'uses built-in gradient/noise/HSV functions (not reproduced here)'
    stages = [('pixel shader', to_hlsl(info), 'ps_4_0')]
    if info['refraction']:
        stages.append(('refraction pixel shader', to_hlsl(info, refraction=True), 'ps_4_0'))
    stages += [('sprite vertex shader', to_hlsl_vs(info), 'vs_4_0'),
               ('model vertex shader', to_hlsl_vs(info, model=True), 'vs_4_0')]
    for stage, src, profile in stages:
        ok, msg = compile_hlsl(src, str(path), profile)
        if not ok:
            errors = [line.split(': ', 1)[-1] for line in msg.splitlines() if 'error' in line]
            return 'FAIL', '%s: %s' % (stage, ' | '.join(errors[:3]))
    return 'ok', ''


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
