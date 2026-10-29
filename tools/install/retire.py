#!/usr/bin/env python3
"""Retire installed animations from a game project: the counterpart of install.py, reversible.

    python tools/install/retire.py --project "../ASSEMBLER TEST PROJECT" --ids 881 882 886-903 [--apply]
    python tools/install/retire.py --project "../ASSEMBLER TEST PROJECT" --restore out/retired/<project>/<stamp>/retired.json [--apply]

Without --apply it only prints what it would do. With --apply, for each id:
  1. refuse (exit 2) if the database uses it: a skill, item or weapon's animationId, or a Show Animation (212) /
     Show Battle Animation (337) command in a troop, common event or map; ids must name an existing, non-blank entry;
  2. blank the entry in data/Animations.json in place, as the editor leaves a cleared slot (name and effectName "",
     no timings, scale and speed 100): the id stays, so no other animation is renumbered. The file is re-read just
     before writing and the write is refused if it changed since it was read (another session may be editing it); its
     line format (one entry per line, CRLF or LF) is kept;
  3. move its effects/<effectName>.efkefc (and an HDR sidecar <name>.hdr.json beside it) into the archive folder
     (--archive, default out/retired/<project>/<yyyymmdd-hhmmss>/, keeping the path under effects/), unless an
     animation that stays plays the same file. Files are moved, never deleted. --orphans DIR also moves every
     .efkefc under effects/DIR that no remaining animation plays (earlier rounds installed without an animation).
Shared resources (Texture/, Model/, Materials/ beside the effects) stay where they are: other effects in the folder
read them, and the resource copier rewrites identical files, so neither names nor dates say who owns one.
The archive's retired.json keeps every old entry and moved file; --restore puts them back into slots that are still
blank and files whose place is still free. Standard library only.
"""
import argparse, datetime, glob, hashlib, json, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# what the editor writes for a cleared slot (ASSEMBLER TEST PROJECT ids 124-126), key order included
BLANK = {'name': '', 'flashTimings': [], 'soundTimings': [], 'effectName': '', 'displayType': 0, 'offsetX': 0,
         'offsetY': 0, 'rotation': {'x': 0, 'y': 0, 'z': 0}, 'scale': 100, 'speed': 100}


def fail(msg, code=1):
    print('RETIRE FAIL: ' + msg, flush=True)
    sys.exit(code)


def parse_ids(items):
    out = []
    for it in items:
        for part in it.split(','):
            m = re.fullmatch(r'(\d+)(?:-(\d+))?', part.strip())
            if not m:
                fail('not an id or range: %r' % part, 2)
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
            out += range(lo, hi + 1)
    return sorted(set(out))


def blank(entry):
    return not entry or (not entry.get('effectName') and not entry.get('name'))


def users(project, ids):
    """{id: [where]} for the ids the database still uses."""
    found = {}
    data = project / 'data'
    for f in ('Skills.json', 'Items.json', 'Weapons.json'):
        p = data / f
        if p.is_file():
            for e in json.loads(p.read_text(encoding='utf-8-sig')):
                if e and e.get('animationId') in ids:
                    found.setdefault(e['animationId'], []).append('%s %d %s' % (f[:-5].lower()[:-1], e['id'], e.get('name', '')))
    events = [data / 'Troops.json', data / 'CommonEvents.json'] + [Path(x) for x in sorted(glob.glob(str(data / 'Map[0-9]*.json')))]

    def walk(node, where):
        if isinstance(node, dict):
            if node.get('code') in (212, 337) and isinstance(node.get('parameters'), list) and len(node['parameters']) > 1:
                anim = node['parameters'][1]
                if anim in ids:
                    found.setdefault(anim, []).append('%s (command %d)' % (where, node['code']))
            for v in node.values():
                walk(v, where)
        elif isinstance(node, list):
            for v in node:
                walk(v, where)
    for p in events:
        if p.is_file():
            walk(json.loads(p.read_text(encoding='utf-8-sig')), p.name)
    return found


def rewrite(anim_path, raw_bytes, changes):
    """Replace the given {id: entry} lines in place, checking nothing else changes and the file did not move."""
    raw = raw_bytes.decode('utf-8')
    data = json.loads(raw)
    nl = '\r\n' if '\r\n' in raw else '\n'
    lines = raw.split(nl)
    for anim_id, new in changes.items():
        hits = [k for k, line in enumerate(lines) if re.match(r'\{.*"id":%d[,}]' % anim_id, line)
                and json.loads(line.rstrip(','))['id'] == anim_id]
        if len(hits) != 1:
            fail('%s does not hold animation %d on one line; not touching it' % (anim_path, anim_id))
        k = hits[0]
        lines[k] = json.dumps(new, ensure_ascii=False, separators=(',', ':')) + (',' if lines[k].endswith(',') else '')
    out = nl.join(lines)
    check = json.loads(out)
    if len(check) != len(data) or any(e != changes.get(i, data[i]) for i, e in enumerate(check)):
        fail('the rewrite would change other entries; not writing')
    if hashlib.sha256(anim_path.read_bytes()).digest() != hashlib.sha256(raw_bytes).digest():
        fail('%s changed while this ran (another session?); rerun' % anim_path)
    anim_path.write_bytes(out.encode('utf-8'))


def effect_files(effects, name):
    f = effects / (name + '.efkefc')
    return [p for p in (f, f.with_name(f.stem + '.hdr.json')) if p.is_file()]


def retire(a, project, anim_path):
    ids = parse_ids(a.ids)
    raw_bytes = anim_path.read_bytes()
    data = json.loads(raw_bytes.decode('utf-8'))
    bad = [i for i in ids if not (0 < i < len(data)) or blank(data[i])]
    if bad:
        fail('no animation (or a blank slot) at id %s' % ', '.join(map(str, bad)), 2)
    used = users(project, set(ids))
    if used:
        fail('the database uses these animations; repoint those first:\n%s' % '\n'.join(
            '  %d: %s' % (i, '; '.join(w)) for i, w in sorted(used.items())), 2)
    effects = project / 'effects'
    staying = {e.get('effectName') for i, e in enumerate(data) if e and i not in ids}
    archive = (a.archive or ROOT / 'out/retired' / project.name / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    if project in archive.parents or archive == project:
        fail('--archive must be outside the project', 2)
    plan = []
    for i in ids:
        name = data[i]['effectName']
        files = [] if name in staying else effect_files(effects, name)
        plan.append((i, data[i], files, name in staying))
    orphans = []
    if a.orphans:
        root = effects / a.orphans.replace('\\', '/').strip('/')
        played = {(effects / (n + '.efkefc')).resolve() for n in staying if n}
        orphans = [f for f in sorted(root.rglob('*.efkefc')) if f.resolve() not in played
                   and all(f not in fs for _, _, fs, _ in plan)]
        orphans += [f.with_name(f.stem + '.hdr.json') for f in orphans if f.with_name(f.stem + '.hdr.json').is_file()]
    for i, e, files, shared in plan:
        print('  %d %s (%s): %s' % (i, e.get('name'), e['effectName'], 'file kept, another animation plays it' if shared
                                    else ', '.join(p.name for p in files) or 'no file under effects/'))
    for f in orphans:
        print('  no animation plays %s' % f.relative_to(effects).as_posix())
    if not a.apply:
        print('DRY RUN: %d animation(s) would be blanked, %d file(s) moved to %s; pass --apply'
              % (len(plan), sum(len(f) for _, _, f, _ in plan) + len(orphans), archive))
        return
    if (archive / 'retired.json').exists():
        fail('%s already holds a retirement; pass another --archive' % archive, 2)
    archive.mkdir(parents=True, exist_ok=True)
    record = {'project': str(project), 'when': datetime.datetime.now().isoformat(timespec='seconds'), 'entries': [], 'files': []}
    for i, e, files, _ in plan:
        record['entries'].append(e)
        for p in files:
            rel = p.relative_to(effects).as_posix()
            record['files'].append(rel)
    record['files'] += [f.relative_to(effects).as_posix() for f in orphans]
    (archive / 'retired.json').write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding='utf-8')
    rewrite(anim_path, raw_bytes, {i: dict(BLANK, id=i) for i, _, _, _ in plan})
    for rel in record['files']:
        dst = archive / 'effects' / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(effects / rel), str(dst))
    print('RETIRE OK: %d animation(s) blanked (%s), %d file(s) moved to %s; undo with --restore %s'
          % (len(plan), ', '.join(str(i) for i, _, _, _ in plan), len(record['files']), archive, archive / 'retired.json'))


def restore(a, project, anim_path):
    rec_path = a.restore.resolve()
    rec = json.loads(rec_path.read_text(encoding='utf-8'))
    archive, effects = rec_path.parent, project / 'effects'
    raw_bytes = anim_path.read_bytes()
    data = json.loads(raw_bytes.decode('utf-8'))
    taken = [e['id'] for e in rec['entries'] if e['id'] >= len(data) or not blank(data[e['id']])]
    if taken:
        fail('slot(s) %s are no longer blank; not restoring' % ', '.join(map(str, taken)), 2)
    clash = [rel for rel in rec['files'] if (effects / rel).exists() or not (archive / 'effects' / rel).is_file()]
    if clash:
        fail('file(s) already back in the project or missing from the archive: %s' % ', '.join(clash), 2)
    for e in rec['entries']:
        print('  %d %s (%s)' % (e['id'], e.get('name'), e.get('effectName')))
    if not a.apply:
        print('DRY RUN: %d animation(s) and %d file(s) would be restored; pass --apply' % (len(rec['entries']), len(rec['files'])))
        return
    for rel in rec['files']:
        (effects / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archive / 'effects' / rel), str(effects / rel))
    rewrite(anim_path, raw_bytes, {e['id']: e for e in rec['entries']})
    rec_path.rename(rec_path.with_name('restored.json'))
    print('RESTORE OK: %d animation(s), %d file(s)' % (len(rec['entries']), len(rec['files'])))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--project', required=True, type=Path)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--ids', nargs='+', help='animation ids or ranges (881 886-903, or 881,882)')
    g.add_argument('--restore', type=Path, help="a retirement's retired.json: put it back")
    p.add_argument('--orphans', help='a folder under effects/ (Edited/X): also move its effects no animation plays')
    p.add_argument('--archive', type=Path, help='where the moved files go (default out/retired/<project>/<stamp>)')
    p.add_argument('--apply', action='store_true', help='do it (without: print the plan only)')
    a = p.parse_args()
    project = a.project.resolve()
    anim_path = project / 'data' / 'Animations.json'
    if not anim_path.is_file():
        fail('missing ' + str(anim_path), 2)
    (restore if a.restore else retire)(a, project, anim_path)


if __name__ == '__main__':
    main()
