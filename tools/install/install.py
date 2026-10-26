#!/usr/bin/env python3
"""Install an effect built by an `edit` spec into a game project, with an animation entry.

    python tools/install/install.py --project "../ASSEMBLER TEST PROJECT" --spec out/edits/<task>/X.edit.json \
        --dest Edited/<Folder>/X --name "Edited: X" [--replace] [--ingame] [--port 8798]

Steps, each of which stops the install on failure:
  0. refuse (exit 2) if the spec names a file that is not on disk (its source effect, add.file, a texture, model or
     material), listing each: a spec written by a post-step builds on the intermediate effect that step read;
  1. build the spec into a staging folder (--work, default out/install/<name>) and require VERIFY OK;
  2. `lint --strict --catalogue` the staged build;
  3. refuse if <project>/effects/<dest>.efkefc exists, unless --replace;
  4. build the spec straight into the project (resources are copied beside it, one shared Texture/ per folder)
     and require it to be byte-identical to the staged build;
  5. add the animation to data/Animations.json, or with --replace keep the entry that already plays this effect,
     or with --id N point existing animation N at this effect (moving an effect to another folder: its name,
     sounds, flashes and timings stay; the old file is left for you to delete).
     The file is re-read just before writing and the write is refused if it changed since it was read (another
     session may be editing it); the file's own line format (one entry per line, CRLF or LF) is kept;
  4b. HDR effects (RPG Reactor 0.99.2+): the sidecar `<spec name>.hdr.json` beside the spec (or --hdr FILE), which
     every effect whose leaf ends in -hdr must have (the engine fails it closed without one), is checked against the
     engine's contract (version 1, gradeInput hdr, a grade declaring tmOut, six bloom sizes/tints) before anything is
     built, its fallback twin must already be installed and not be HDR itself (--hdr-fallback NAME renames it), and it
     is written beside the installed .efkefc; a sidecar left over from an earlier HDR install of the same name stops a
     non-HDR reinstall;
  6. with --ingame, play it twice in a real battle with tools/ingame/run.js and report freeze, frames, ended:
     once as an uncovered cast (not preloaded) and, when the project has js/plugins/ReactorEffectPreload.js, once with
     the id preloaded (run.js --preload-id), the first-cast freeze a player sees with the plugin covering it.
Standard library only. Prints the animation id.
"""
import argparse, hashlib, json, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def fail(msg, code=1):
    print('INSTALL FAIL: ' + msg, flush=True)
    sys.exit(code)


def run(args, **kw):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, encoding='utf-8', errors='replace', **kw)


RESOURCE_EXT = ('.png', '.dds', '.tga', '.efkmodel', '.efkmat', '.efkmatd', '.efkcurve', '.wav', '.ogg')


def missing_inputs(spec):
    """Files the spec builds on that are not on disk: its source effect, effects it copies nodes from (add.file) and
    the resources its values name, each looked up as written, beside the spec, then from the working directory.
    The edit tool only says it failed; a spec written by a post-step (OT2 Boost's light.py) builds on the
    intermediate effect that step read, and a build script that deleted it left `edit ... exit 1` and nothing else."""
    try:
        d = json.loads(spec.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as e:
        return [('spec', str(spec), 'unreadable: %s' % e)]
    found = lambda f: any(Path(x).exists() for x in (f, spec.parent / f, Path.cwd() / f))
    out = []
    src = d.get('source')
    if isinstance(src, str) and src.lower().endswith('.efkefc') and not found(src):
        out.append(('source effect', src, 'the effect this spec edits'))
    for e in d.get('edits') or []:
        if not isinstance(e, dict):
            continue
        add = e.get('add')
        if isinstance(add, dict) and isinstance(add.get('file'), str) and not found(add['file']):
            out.append(('node source', add['file'], 'nodes are copied from it (add.file)'))
        for k, v in (e.get('set') or {}).items():
            if isinstance(v, str) and v.lower().endswith(RESOURCE_EXT) and not found(v):
                out.append(('resource', v, k))
    return out


def build(tool, spec, out):
    r = run(['dotnet', tool, 'edit', spec, out])
    verify = [l for l in r.stdout.splitlines() if l.startswith('VERIFY')]
    if r.returncode != 0 or not verify or not verify[-1].startswith('VERIFY OK'):
        fail('edit %s -> %s exit %d\n%s\n%s' % (spec, out, r.returncode, r.stdout[-1500:], r.stderr[-1500:]))
    for line in r.stdout.splitlines():
        if line.startswith('WARNING'):
            print('  ' + line)
    return verify[-1]


def add_animation(anim_path, effect_name, name, replace, display_type=None):
    raw_bytes = anim_path.read_bytes()
    raw = raw_bytes.decode('utf-8')
    data = json.loads(raw)
    existing = [e for e in data if e and e.get('effectName') == effect_name]
    if existing:
        if not replace:
            fail('animation %d already plays %s; pass --replace to reinstall over it' % (existing[0]['id'], effect_name))
        if display_type is not None:
            return repoint_animation(anim_path, existing[0]['id'], effect_name, display_type)
        print('  animation %d already plays %s: kept' % (existing[0]['id'], effect_name))
        return existing[0]['id']
    nl = '\r\n' if '\r\n' in raw else '\n'
    body = raw.rstrip('\r\n')
    if not body.endswith('}' + nl + ']'):
        fail('%s does not end with one entry per line; not touching it' % anim_path)
    new_id = len(data)
    entry = {'id': new_id, 'displayType': 0 if display_type is None else display_type, 'effectName': effect_name, 'flashTimings': [], 'name': name,
             'offsetX': 0, 'offsetY': 0, 'rotation': {'x': 0, 'y': 0, 'z': 0}, 'scale': 100, 'soundTimings': [],
             'speed': 100, 'timings': []}
    out = body[:-len(nl + ']')] + ',' + nl + json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + nl + ']' + raw[len(body):]
    check = json.loads(out)
    if check[:len(data)] != data or check[new_id]['id'] != new_id:
        fail('appending would change existing entries; not writing')
    if hashlib.sha256(anim_path.read_bytes()).digest() != hashlib.sha256(raw_bytes).digest():
        fail('%s changed while this install was running (another session?); rerun' % anim_path)
    anim_path.write_bytes(out.encode('utf-8'))
    print('  animation %d added: %s' % (new_id, name))
    return new_id


def repoint_animation(anim_path, anim_id, effect_name, display_type=None):
    raw_bytes = anim_path.read_bytes()
    raw = raw_bytes.decode('utf-8')
    data = json.loads(raw)
    if not (0 < anim_id < len(data)) or not data[anim_id]:
        fail('animation %d does not exist' % anim_id)
    other = [e['id'] for e in data if e and e.get('effectName') == effect_name and e['id'] != anim_id]
    if other:
        fail('animation %d already plays %s' % (other[0], effect_name))
    old = data[anim_id].get('effectName')
    if old == effect_name and (display_type is None or data[anim_id].get('displayType') == display_type):
        print('  animation %d already plays %s: kept' % (anim_id, effect_name))
        return anim_id
    nl = '\r\n' if '\r\n' in raw else '\n'
    lines = raw.split(nl)
    new = dict(data[anim_id], effectName=effect_name)
    if display_type is not None:
        new['displayType'] = display_type
    hits = [k for k, line in enumerate(lines) if re.match(r'\{"id":%d,' % anim_id, line)]
    if len(hits) != 1:
        fail('%s does not hold animation %d on one line; not touching it' % (anim_path, anim_id))
    k = hits[0]
    lines[k] = json.dumps(new, ensure_ascii=False, separators=(',', ':')) + (',' if lines[k].endswith(',') else '')
    out = nl.join(lines)
    check = json.loads(out)
    if len(check) != len(data) or any(e != (new if i == anim_id else data[i]) for i, e in enumerate(check)):
        fail('repointing would change other entries; not writing')
    if hashlib.sha256(anim_path.read_bytes()).digest() != hashlib.sha256(raw_bytes).digest():
        fail('%s changed while this install was running (another session?); rerun' % anim_path)
    anim_path.write_bytes(out.encode('utf-8'))
    print('  animation %d now plays %s (was %s)' % (anim_id, effect_name, old))
    return anim_id


def hdr_sidecar(path, fallback):
    """The engine's sidecar contract (docs/HDR-EFFECT-OVERLAY.md in the Reactor source): returns the checked dict."""
    try:
        side = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as ex:
        fail('HDR sidecar %s unreadable: %s' % (path, ex), 2)
    if fallback:
        side['fallback'] = fallback
    bad = []
    if side.get('version') != 1:
        bad.append('version must be 1')
    if side.get('gradeInput') != 'hdr':
        bad.append('gradeInput must be "hdr"')
    if not isinstance(side.get('grade'), str) or 'tmOut' not in side['grade']:
        bad.append('grade must be GLSL that declares vec3 tmOut')
    b = side.get('bloom', {})
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')
    if not isinstance(b, dict):
        bad.append('bloom must be an object')
    else:
        if not (num(b.get('intensity', 0)) and b.get('intensity', 0) >= 0):
            bad.append('bloom.intensity must be a number >= 0')
        if not (num(b.get('radiusScale', 1)) and b.get('radiusScale', 1) > 0):
            bad.append('bloom.radiusScale must be a number > 0')
        if 'threshold' in b and not num(b['threshold']):
            bad.append('bloom.threshold must be a finite number')
        for k, ok in (('sizes', lambda v: v > 0), ('tints', lambda v: v >= 0)):
            v = b.get(k)
            if v is not None and not (isinstance(v, list) and len(v) == 6 and all(num(x) and ok(x) for x in v)):
                bad.append('bloom.%s must be six %s numbers' % (k, 'positive' if k == 'sizes' else 'nonnegative'))
    fb = side.get('fallback')
    if not isinstance(fb, str) or not fb.strip() or fb.lower().endswith('.efkefc'):
        bad.append('fallback must name the graded twin, without .efkefc')
    if bad:
        fail('HDR sidecar %s breaks the engine contract:\n%s' % (path, '\n'.join('  ' + x for x in bad)), 2)
    return side


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--project', required=True, type=Path)
    p.add_argument('--spec', required=True, type=Path)
    p.add_argument('--dest', required=True, help='effect name under effects/, no extension, e.g. Edited/Procedural/WireCrystal')
    p.add_argument('--name', help='animation name shown in the database (a new animation)')
    p.add_argument('--id', type=int, help='point this existing animation at the effect instead of adding one')
    p.add_argument('--replace', action='store_true', help='reinstall over an existing effect (keeps its animation)')
    p.add_argument('--display-type', type=int, choices=(0, 1, 2),
                   help='0: each target (default for new animations); 1: target group; 2: screen. Existing entries are kept when omitted.')
    p.add_argument('--tool', type=Path, default=ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll')
    p.add_argument('--catalogue', type=Path, default=ROOT / 'out/catalogue.json')
    p.add_argument('--work', type=Path, help='staging folder (default out/install/<effect name>)')
    p.add_argument('--ingame', action='store_true', help='play it in a real battle afterwards (tools/ingame/run.js)')
    p.add_argument('--port', type=int, default=8798)
    p.add_argument('--hdr', type=Path, help='HDR sidecar to install beside the effect (default: <spec name>.hdr.json beside the spec)')
    p.add_argument('--hdr-fallback', help="name of the installed graded twin, written into the sidecar's fallback")
    a = p.parse_args()
    if (a.id is None) == (a.name is None):
        fail('pass --name (a new animation) or --id (an existing one)', 2)

    project, spec = a.project.resolve(), a.spec.resolve()
    dest = a.dest.replace('\\', '/').strip('/')
    if dest.lower().endswith('.efkefc'):
        dest = dest[:-7]
    target = project / 'effects' / (dest + '.efkefc')
    anim_path = project / 'data' / 'Animations.json'
    for f in (spec, a.tool, anim_path):
        if not f.exists():
            fail('missing ' + str(f), 2)
    gone = missing_inputs(spec)
    if gone:
        fail('the spec names files that are not on disk, so it cannot be built:\n%s\nRebuild or restore them first. A spec '
             'written by a post-step (one that adds to an already built effect) builds on the effect that step read: '
             'keep that intermediate beside the final build.' % '\n'.join('  %s %s (%s)' % g for g in gone), 2)
    if target.exists() and not a.replace:
        fail('%s exists; pass --replace to reinstall over it' % target)

    side_src = a.hdr.resolve() if a.hdr else spec.with_name(re.sub(r'(\.edit)?\.json$', '', spec.name) + '.hdr.json')
    if a.hdr and not side_src.is_file():
        fail('missing ' + str(side_src), 2)
    side_dst = target.with_name(target.stem + '.hdr.json')
    side = None
    if side_src.is_file():
        side = hdr_sidecar(side_src, a.hdr_fallback)
        fb = side['fallback']
        twin = project / 'effects' / (fb + '.efkefc') if '/' in fb else target.with_name(fb + '.efkefc')
        if twin.resolve() == target.resolve():
            fail('HDR sidecar fallback %r names this effect itself; it must be the graded twin' % fb, 2)
        if not twin.is_file():
            fail('HDR fallback twin %s is not installed: install the graded twin first (or pass --hdr-fallback)' % twin, 2)
        if twin.with_name(twin.stem + '.hdr.json').exists():
            fail('HDR fallback twin %s has its own sidecar: the fallback must be a normally graded effect' % twin, 2)
        print('0. HDR sidecar %s (fallback %s)' % (side_src.name, fb))
    elif Path(dest).name.lower().endswith('-hdr'):
        fail('%s is an -hdr effect but %s is missing: the engine fails it closed without its sidecar (pass --hdr FILE)'
             % (dest, side_src), 2)
    elif side_dst.exists():
        fail('%s is left from an HDR install of this name; this build has no sidecar, so the engine would play it as HDR. '
             'Remove it or pass --hdr' % side_dst, 2)

    leaf = Path(dest).name
    work = (a.work or ROOT / 'out/install' / leaf).resolve()
    if work == target.parent or project in work.parents:
        fail('--work must be outside the project', 2)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    print('1. staging build')
    print('  ' + build(a.tool, spec, work / (leaf + '.efkefc'))[:110])
    print('2. lint --strict')
    lint_args = ['dotnet', a.tool, 'lint', work / (leaf + '.efkefc'), '--strict']
    if a.catalogue.exists():
        lint_args += ['--catalogue', a.catalogue]
    r = run(lint_args)
    summary = [l for l in r.stdout.splitlines() if l.startswith('LINT')]
    print('  ' + (summary[-1] if summary else r.stdout.strip()[-200:]))
    if r.returncode != 0:
        fail('lint exit %d\n%s' % (r.returncode, r.stdout[-1500:]))

    print('3. install into ' + str(target))
    print('  ' + build(a.tool, spec, target)[:110])
    if target.read_bytes() != (work / (leaf + '.efkefc')).read_bytes():
        fail('installed file differs from the staged build')
    print('  identical to the staged build')
    if side is not None:
        side_dst.write_text(json.dumps(side, indent=2) + '\n', encoding='utf-8')
        print('  HDR sidecar written: ' + str(side_dst))

    print('4. animation')
    anim_id = repoint_animation(anim_path, a.id, dest, a.display_type) if a.id is not None else add_animation(anim_path, dest, a.name, a.replace, a.display_type)

    if a.ingame:
        print('5. in-game run')
        cases = [('not preloaded (uncovered cast)', [])]
        if (project / 'js/plugins/ReactorEffectPreload.js').is_file():
            cases.append(('preloaded (ReactorEffectPreload)', ['--preload-id']))
        else:
            print('  (no js/plugins/ReactorEffectPreload.js in the project: preloaded case not measured)')
        for label, extra in cases:
            print('  ' + label + ':')
            r = run(['node', ROOT / 'tools/ingame/run.js', '--project', project, '--out', work / 'ingame', '--ids', anim_id,
                     '--repeat', 2, '--port', a.port] + extra, cwd=ROOT)
            for line in r.stdout.splitlines():
                if line.startswith('time-') or line.startswith('INGAME'):
                    print('    ' + re.sub(r'\s+', ' ', line))
            if r.returncode != 0:
                fail('in-game run exit %d' % r.returncode)
    print('INSTALL OK: %s as animation %d' % (dest, anim_id))


if __name__ == '__main__':
    main()
