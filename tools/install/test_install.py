#!/usr/bin/env python3
"""install.py against a throwaway project: new install, refused reinstall, --replace keeps the id, existing
entries and line format untouched, a stale same-named texture in the project caught, nothing left in the project
but the effect and its Texture/. Needs a built Niagara2Efk.dll (--tool).

    python tools/install/test_install.py --work out/tests/install [--tool <Niagara2Efk.dll>]
"""
import argparse, json, shutil, struct, subprocess, sys, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def png(path, value):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    px = bytes([value, value, value, 255])
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress((b'\0' + px * 2) * 2)) + chunk(b'IEND', b''))


def require(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--tool', type=Path, default=ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll')
    a = p.parse_args()
    work = a.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    project = work / 'Project'
    (project / 'effects').mkdir(parents=True)
    (project / 'data').mkdir()
    anims = '[\r\nnull,\r\n{"id":1,"effectName":"HitPhysical","name":"Hit"},\r\n{"id":2,"effectName":"Other","name":"Other"}\r\n]'
    (project / 'data/Animations.json').write_bytes(anims.encode('utf-8'))
    src = work / 'src'
    src.mkdir()
    subprocess.run(['dotnet', str(a.tool), 'blank', str(src / 'blank.efkefc')], check=True, capture_output=True)
    png(src / 'tex/Glow.png', 255)
    spec = src / 'T.edit.json'
    spec.write_text(json.dumps({'source': 'blank.efkefc', 'edits': [{'node': '0', 'set': {'RendererCommonValues/ColorTexture': 'tex/Glow.png'}}]}))

    def install(*extra, code=0):
        r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec', str(spec),
                            '--dest', 'Edited/Test/T', '--name', 'Test T', '--tool', str(a.tool), '--work', str(work / 'stage'), *extra],
                           capture_output=True, text=True)
        require(r.returncode == code, 'exit %d, expected %d:\n%s%s' % (r.returncode, code, r.stdout, r.stderr))
        return r.stdout

    passed = 0
    out = install()
    require('INSTALL OK: Edited/Test/T as animation 3' in out, out)
    raw = (project / 'data/Animations.json').read_bytes().decode('utf-8')
    data = json.loads(raw)
    require(raw.startswith(anims[:-3]) and raw.count('\r\n') == 5 and data[3]['effectName'] == 'Edited/Test/T', raw)
    require(sorted(p.name for p in (project / 'effects/Edited/Test').iterdir()) == ['T.efkefc', 'Texture'], 'stray files in the project')
    passed += 1; print('PASS new install, id 3, format kept, no stray files')

    out = install(code=1)
    require('exists; pass --replace' in out, out)
    passed += 1; print('PASS reinstall refused without --replace')

    out = install('--replace')
    require('animation 3 already plays Edited/Test/T: kept' in out and json.loads((project / 'data/Animations.json').read_text()) == data, out)
    passed += 1; print('PASS --replace keeps the animation')

    r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec', str(spec),
                        '--dest', 'Edited/Moved/T', '--id', '3', '--tool', str(a.tool), '--work', str(work / 'stage')],
                       capture_output=True, text=True)
    moved = json.loads((project / 'data/Animations.json').read_text())
    require(r.returncode == 0 and 'animation 3 now plays Edited/Moved/T (was Edited/Test/T)' in r.stdout
            and moved[3] == dict(data[3], effectName='Edited/Moved/T') and moved[:3] == data[:3]
            and (project / 'effects/Edited/Moved/T.efkefc').exists(), r.stdout + r.stderr)
    passed += 1; print('PASS --id moves an animation to the new effect, nothing else changes')
    r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec', str(spec),
                        '--dest', 'Edited/Moved/T', '--id', '2', '--replace', '--tool', str(a.tool), '--work', str(work / 'stage')],
                       capture_output=True, text=True)
    require(r.returncode == 1 and 'animation 3 already plays Edited/Moved/T' in r.stdout, r.stdout + r.stderr)
    passed += 1; print('PASS --id refuses a second animation on the same effect')

    png(src / 'tex/Glow.png', 64)          # regenerated under the same name: the project's copy is now stale
    out = install('--replace', code=1)
    require('a different Glow.png was already beside the output' in out, out)
    passed += 1; print('PASS stale same-named texture in the project caught')
    before = (project / 'data/Animations.json').read_bytes()
    gone = src / 'G.edit.json'
    gone.write_text(json.dumps({'source': 'deleted-intermediate.efkefc', 'edits': [
        {'node': '0', 'set': {'RendererCommonValues/ColorTexture': 'tex/Missing.png'}}]}))
    r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec', str(gone),
                        '--dest', 'Edited/Test/G', '--name', 'Test G', '--tool', str(a.tool), '--work', str(work / 'stage-g')],
                       capture_output=True, text=True)
    require(r.returncode == 2 and 'source effect deleted-intermediate.efkefc' in r.stdout and 'resource tex/Missing.png' in r.stdout
            and not (project / 'effects/Edited/Test/G.efkefc').exists() and (project / 'data/Animations.json').read_bytes() == before,
            'exit %d\n%s%s' % (r.returncode, r.stdout, r.stderr))
    passed += 1; print('PASS a spec whose source effect or resources are gone is refused, each missing file named')
    print('RESULT: %d passed, 0 failed' % passed)


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print('FAIL ' + str(e)); print('RESULT: failed'); sys.exit(1)
