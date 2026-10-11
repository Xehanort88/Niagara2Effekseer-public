"""Tests for hlslcheck: hand-written .efkmat files, portable and GLSL-only. Windows only (d3dcompiler_47)."""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hlslcheck as H  # noqa: E402

TAIL = ('$F3$ worldPositionOffset = $F3$(0.0,0.0,0.0);\n$F3$ baseColor = $F3$(0.0,0.0,0.0);\n'
        '$F1$ metallic = 0.0;\n$F1$ roughness = 1.0;\n$F1$ ambientOcclusion = 1.0;\n$F1$ opacityMask = 1.0;\n'
        '$F1$ refraction = 1.0;\n')


def efkmat(code, textures=('tex0',), uniforms=('Tint',), cd=(2, 2), required=()):
    """A minimal version-1710 .efkmat: PRM_ (custom data, required built-ins, textures, uniforms) + GENE."""
    def i32(*v):
        return struct.pack('<%di' % len(v), *v)

    def s(x):
        b = x.encode() + b'\0'
        return i32(len(b)) + b
    prm = i32(1, 0, 0, cd[0], cd[1], len(required), *required, len(textures))
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
    }
    for name, (data, want, word) in cases.items():
        p = work / (name.replace(' ', '_') + '.efkmat')
        p.write_bytes(data)
        got, msg = H.check(p)
        check(name + ' -> ' + want, got == want and word in msg, (got, msg))
    info = H.parse_efkmat(cases['portable'][0])
    check('parse: textures, uniforms, custom data', info['textures'][0]['name'] == 'tex0'
          and info['uniforms'] == ['Tint'] and (info['cd1'], info['cd2']) == (2, 2), info)
    rc = H.main([str(work)])
    check('exit code 1 when a material fails', rc == 1, rc)
    print('%d failure(s)' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
