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

    before_display = json.loads((project / 'data/Animations.json').read_text())
    r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec', str(spec),
                        '--dest', 'Edited/Group/T', '--name', 'Group T', '--display-type', '1', '--tool', str(a.tool),
                        '--work', str(work / 'stage-group')], capture_output=True, text=True)
    group = json.loads((project / 'data/Animations.json').read_text())
    require(r.returncode == 0 and group[:-1] == before_display and group[-1]['displayType'] == 1
            and before_display[3]['displayType'] == 0, r.stdout + r.stderr)
    passed += 1; print('PASS --display-type 1 adds a group animation; default 0 and other entries unchanged')
    r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec', str(spec),
                        '--dest', 'Edited/Group/T', '--id', str(group[-1]['id']), '--replace', '--display-type', '2',
                        '--tool', str(a.tool), '--work', str(work / 'stage-group')], capture_output=True, text=True)
    changed = json.loads((project / 'data/Animations.json').read_text())
    require(r.returncode == 0 and changed[:-1] == group[:-1] and changed[-1] == dict(group[-1], displayType=2), r.stdout + r.stderr)
    passed += 1; print('PASS explicit --display-type on --replace --id changes only the selected animation')

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

    hdr_dir = project / 'effects/Edited/HDR'

    def put(dest, spec_path, *extra, code=0, name=None):
        args = ['--name', name or dest] if '--id' not in extra else []
        r = subprocess.run([sys.executable, str(ROOT / 'tools/install/install.py'), '--project', str(project), '--spec',
                            str(spec_path), '--dest', dest, *args, '--tool', str(a.tool), '--work', str(work / 'stage-hdr'),
                            *extra], capture_output=True, text=True)
        require(r.returncode == code, 'exit %d, expected %d:\n%s%s' % (r.returncode, code, r.stdout, r.stderr))
        return r.stdout

    hspec = src / 'T-hdr.edit.json'
    hspec.write_text(spec.read_text())
    before = (project / 'data/Animations.json').read_bytes()
    out = put('Edited/HDR/T-hdr', hspec, code=2)
    require('fails it closed without its sidecar' in out and not hdr_dir.exists()
            and (project / 'data/Animations.json').read_bytes() == before, out)
    passed += 1; print('PASS an -hdr effect without its sidecar is refused before anything is built')

    good = {'version': 1, 'gradeInput': 'hdr', 'grade': 'vec3 tmOut = hdr / (hdr + vec3(1.0));',
            'bloom': {'intensity': 0.675, 'sizes': [0.3, 1, 2, 10, 30, 64], 'tints': [0.3465, 0.138, 0.1176, 0.066, 0.066, 0.061],
                      'radiusScale': 2.4}, 'fallback': 'T-ldr'}
    side = src / 'T-hdr.hdr.json'
    side.write_text(json.dumps(good))
    out = put('Edited/HDR/T-hdr', hspec, code=2)
    require('fallback twin' in out and 'is not installed' in out and not hdr_dir.exists(), out)
    passed += 1; print('PASS an HDR install whose graded twin is not installed is refused')

    side.write_text(json.dumps(dict(good, version=2, bloom=dict(good['bloom'], sizes=[1, 2, 3]))))
    out = put('Edited/HDR/T-hdr', hspec, code=2)
    require('version must be 1' in out and 'bloom.sizes must be six positive numbers' in out and not hdr_dir.exists(), out)
    passed += 1; print('PASS a sidecar breaking the engine contract is refused, each problem named')

    lspec = src / 'T-ldr.edit.json'
    lspec.write_text(spec.read_text())
    put('Edited/HDR/T-ldr', lspec)
    side.write_text(json.dumps(dict(good, fallback='Wrong')))
    out = put('Edited/HDR/T-hdr', hspec, '--hdr-fallback', 'T-ldr')
    written = json.loads((hdr_dir / 'T-hdr.hdr.json').read_text(encoding='utf-8'))
    require('HDR sidecar written' in out and written == good and (hdr_dir / 'T-hdr.efkefc').exists()
            and not (hdr_dir / 'T-ldr.hdr.json').exists(), out)
    passed += 1; print('PASS an HDR install writes the checked sidecar beside it; --hdr-fallback names the twin')

    out = put('Edited/HDR/T-hdr', hspec, '--replace', '--hdr-fallback', 'T-hdr', code=2)
    require('names this effect itself' in out, out)
    side.write_text(json.dumps(dict(good, fallback='T-hdr2')))
    (hdr_dir / 'T-hdr2.efkefc').write_bytes((hdr_dir / 'T-hdr.efkefc').read_bytes())
    (hdr_dir / 'T-hdr2.hdr.json').write_text(json.dumps(good))
    out = put('Edited/HDR/T-hdr', hspec, '--replace', code=2)
    require('has its own sidecar' in out, out)
    passed += 1; print('PASS a fallback that is the effect itself or another HDR effect is refused')

    side.write_text(json.dumps(good))
    put('Edited/HDR/Glow', lspec, '--hdr', str(side))
    out = put('Edited/HDR/Glow', lspec, '--replace', code=2)
    require('is left from an HDR install of this name' in out, out)
    passed += 1; print('PASS a non-HDR reinstall over a leftover sidecar is refused')
    print('RESULT: %d passed, 0 failed' % passed)


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print('FAIL ' + str(e)); print('RESULT: failed'); sys.exit(1)
