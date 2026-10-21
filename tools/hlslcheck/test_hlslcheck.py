"""Tests for hlslcheck: hand-written .efkmat files, portable and GLSL-only. Windows only (d3dcompiler_47)."""
import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hlslcheck as H  # noqa: E402

TAIL = ('$F3$ worldPositionOffset = $F3$(0.0,0.0,0.0);\n$F3$ baseColor = $F3$(0.0,0.0,0.0);\n'
        '$F1$ metallic = 0.0;\n$F1$ roughness = 1.0;\n$F1$ ambientOcclusion = 1.0;\n$F1$ opacityMask = 1.0;\n'
        '$F1$ refraction = 1.0;\n')


def efkmat(code, textures=('tex0',), uniforms=('Tint',), cd=(2, 2), required=(), refraction=0):
    """A minimal version-1710 .efkmat: PRM_ (custom data, required built-ins, textures, uniforms) + GENE."""
    def i32(*v):
        return struct.pack('<%di' % len(v), *v)

    def s(x):
        b = x.encode() + b'\0'
        return i32(len(b)) + b
    prm = i32(1, 0, refraction, cd[0], cd[1], len(required), *required, len(textures))
    for k, t in enumerate(textures):
        prm += s(t) + s(t) + s('') + i32(k, k, 1, 1, 0)
    prm += i32(len(uniforms))
    for k, u in enumerate(uniforms):
        prm += s(u) + s(u) + i32(k * 4, k, 4) + struct.pack('<4f', 0, 0, 0, 0)
    prm += i32(0, 0)                                        # gradients, fixed gradients
    gene = s(code + TAIL)
    return (b'EFKM' + i32(1710) + b'\0' * 8 + b'PRM_' + i32(len(prm)) + prm + b'GENE' + i32(len(gene)) + gene)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    if sys.platform != 'win32':
        print('skipped: needs Windows')
        return 0
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    fails = 0

    def check(name, ok, detail=''):
        nonlocal fails
        fails += not ok
        print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else '  ' + str(detail)))

    portable = ('$F4$ c = $TEX_P0$uv1$TEX_S0$;\n'
                '$F3$ k = LERP($F3$(0.0,0.0,0.0), c.xyz * vcolor.xyz, step($F3$(0.5,0.5,0.5), c.xyz));\n'
                '$F1$ f = FRAC(customData1.x + $TIME$) * customData2.y + Tint.x;\n'
                '$F3$ emissive = k * f;\n$F1$ opacity = c.w * vcolor.w;\n')
    background = ('$F4$ b = efk_background_texture.Sample(efk_background_sampler, screenUV);\n'
                  '$F3$ emissive = b.xyz * Tint.xyz;\n$F1$ opacity = vcolor.w;\n')
    redirect = ('$F4$ bkOut = efk_background_texture.SampleLevel(efk_background_sampler, screenUV, 0.0);\n'
                'bkOut.xyz = 1.0 - bkOut.xyz;\n'
                '#define Sample(s, u) SampleLevel(s, u, 0.0) * 0.0 + bkOut\n'
                '$F3$ emissive = $F3$(0.0,0.0,0.0) * Tint.xyz;\n$F1$ opacity = vcolor.w;\n')
    cases = {   # name: (file, expected status, a word the compiler error must contain)
        'portable': (efkmat(portable), 'ok', ''),
        'mix': (efkmat(portable.replace('LERP(', 'mix(')), 'FAIL', 'mix'),
        'fract': (efkmat(portable.replace('FRAC(', 'fract(')), 'FAIL', 'fract'),
        'one-value constructor': (efkmat(portable.replace('$F3$(0.0,0.0,0.0), c.xyz', '$F3$(0.0), c.xyz')), 'FAIL',
                                  'constructor'),
        'two-argument atan': (efkmat(portable.replace('FRAC(customData1.x + $TIME$)', 'atan(uv1.y, uv1.x)')), 'FAIL',
                              'atan'),
        'greaterThanEqual': (efkmat(portable.replace('step($F3$(0.5,0.5,0.5), c.xyz)',
                                                     '$F3$(greaterThanEqual(c.xyz, $F3$(0.5,0.5,0.5)))')), 'FAIL',
                             'greaterThanEqual'),
        'custom data 3 is a float3': (efkmat(portable.replace('customData1.x', 'customData1.z'), cd=(3, 2)), 'ok', ''),
        'custom data 2 has no .z': (efkmat(portable.replace('customData1.x', 'customData1.z'), cd=(2, 2)), 'FAIL',
                                    'subscript'),
        'undeclared uniform': (efkmat(portable, uniforms=()), 'FAIL', 'Tint'),
        'built-in gradient is skipped': (efkmat(portable, required=(1,)), 'SKIP', ''),
        # the editor's model vertex shader declares 'float4 uv': a local of that name fails there only
        'local uv (model vertex shader)': (efkmat('$F2$ uv = uv1;\n' + portable.replace('$TEX_P0$uv1', '$TEX_P0$uv')),
                                           'FAIL', 'model vertex shader'),
        'local ptuv is fine': (efkmat('$F2$ ptuv = uv1;\n' + portable.replace('$TEX_P0$uv1', '$TEX_P0$ptuv')), 'ok', ''),
        'local matModel (model vertex shader)': (efkmat('$F1$ matModel = 1.0;\n' + portable), 'FAIL', 'model vertex shader'),
        # every generated shader declares the background and depth textures, refraction or not
        'reads efk_background': (efkmat(background), 'ok', ''),
        'reads efk_depth': (efkmat(background.replace('efk_background', 'efk_depth')), 'ok', ''),
        'refraction flag, reads efk_background': (efkmat(background, refraction=1), 'ok', ''),
        # a refraction material's pass 0 ends with the generator's refraction code, its locals and cameraMat
        'refraction: local dir (refraction pixel shader)': (efkmat('$F3$ dir = $F3$(0.0,0.0,1.0);\n' + background,
                                                                   refraction=1), 'FAIL', 'refraction pixel shader'),
        'no refraction: local dir is fine': (efkmat('$F3$ dir = $F3$(0.0,0.0,1.0);\n' + background), 'ok', ''),
        'cameraMat without the refraction flag': (efkmat(background.replace('vcolor.w', 'cameraMat[0].x')), 'FAIL',
                                                  'cameraMat'),
        # pass 1 of a refraction material is the normal shader: no cameraMat there either
        'refraction: cameraMat in pass 1': (efkmat(background.replace('vcolor.w', 'cameraMat[0].x'), refraction=1),
                                            'FAIL', 'pixel shader'),
        # the backdrop-read prototype (out/edits/bd2-backdrop-read-20261018, scratch/backmat.py): pass 0 sends its
        # own result through the ending's background fetch with a preprocessor redirect
        'refraction: redirected background fetch': (efkmat(redirect, refraction=1), 'ok', ''),
    }
    for name, (data, want, word) in cases.items():
        p = work / (re.sub(r'[^A-Za-z0-9.()-]+', '_', name) + '.efkmat')
        p.write_bytes(data)
        got, msg = H.check(p)
        check(name + ' -> ' + want, got == want and word in msg, (got, msg))
    info = H.parse_efkmat(cases['portable'][0])
    check('parse: textures, uniforms, custom data', info['textures'][0]['name'] == 'tex0'
          and info['uniforms'] == ['Tint'] and (info['cd1'], info['cd2']) == (2, 2) and not info['refraction'], info)
    refr = H.parse_efkmat(cases['refraction flag, reads efk_background'][0])
    check('parse: refraction flag', refr['refraction'], refr)
    src = H.to_hlsl(refr, refraction=True)
    check('refraction pixel shader ends with the background read',
          'efk_background_texture.Sample(efk_background_sampler, distortUV)' in src and 'cameraMat' in src, src[-400:])
    rc = H.main([str(work)])
    check('exit code 1 when a material fails', rc == 1, rc)
    print('%d failure(s)' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
