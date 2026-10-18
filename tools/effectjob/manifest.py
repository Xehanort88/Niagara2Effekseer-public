"""The per-effect manifest (effect.json): load, check, and expand it into the exact builder runs.

Relative paths in a manifest are relative to the repository root (the folder holding tools/), as the build scripts in
out/edits/*/scratch were. `{variant}` in combine.name, install.dest and install.name is replaced by the variant's key.
Fields: SCHEMA below (`effectjob.py schema`); README § Effect manifest and ledger.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FPS = 60.0
TOOL_170 = ROOT / 'src/Niagara2Efk/bin/x64/Release/net9.0/Niagara2Efk.dll'
TOOL_1803 = ROOT / 'src/Niagara2Efk1803/bin/Release/net9.0/Niagara2Efk.dll'
CHECK_BUILD = ROOT / '.claude/skills/cascade-build/check_build.py'
BUILDERS = {'cascade': ROOT / 'tools/cascade/build_effekseer.py', 'niagara': ROOT / 'tools/niagara/build_niagara.py'}

SCHEMA = """effect.json (schema "effectjob/1"); * = required
  schema*            "effectjob/1"
  effect*            short name (folder-safe)
  game*, skill*      what is being rebuilt (skill = the skill or sequence as the game names it)
  sequence           {name, file: exported sequence JSON (bindings of type Emitter, Particle trigger keys),
                      skip: {template stem: reason}}  -- the systems the game plays; omit file when there is none
                      (the ledger then says so) and list them in systems[] with offset_reason
  runtime*           {efk18: bool}  1.8 tool build (src/Niagara2Efk1803) or 1.70e
  exports*           the export root (<Game>/Content under it)
  inputs             {meshes: folder of .efkmodel, vector_fields: [raw field roots] (NIAGARA_FIELDS),
                      textures: note}  -- injected as --meshes / NIAGARA_FIELDS
  footage            [{path, fps, note}]
  renderdoc          [{rdc, event_ids: [..], labels: labels.json, label_map: {label: [system/emitter, ..]}, note}]
  oracle             {script, tag, background, frames, ss, exposure, args: [..], systems: {system: {dossier, report}}}
                     report = the oracle's report.json; run by `effectjob.py oracle` or read when already there
  camera             {view_pitch, root_y, turn}: checked against the builder args (no silent drift)
  shared_args*       builder args every build gets (no --name/--only/--exports/--meshes/--efk18/--time-offset needed:
                     those come from the manifest and are checked when given)
  systems*           [{id*, builder*: cascade|niagara, source*: dump/summary path, offset_s*, offset_frames*,
                       sequence_offset_s, offset_reason, args: [..], builds*: [{name*, only: [emitters], args: [..]}],
                       excluded: {emitter: reason}, accept: {emitter: reason}}]
  variants           {key: {note, args: [..] (every build), builds: {build name: {args: [..]}}}}  default {"main": {}}
  combine            {name: "X-{variant}", end_frame}: one effect per variant, each build under a host delayed by its
                     system's offset_frames (needed when there is more than one build)
  install            {project, dest: "Edited/<Folder>/X-{variant}", name | anim_id}: the target; the runner never installs
  playback           {project, anim_id, port, max_frames}: tools/ingame end check of each variant's final build
                     (max_frames goes to run.js --max-frames, which counts browser frames, ~2.7 per game frame on a
                     164 Hz display: leave it unset)
  visual             {verdict, by, date}: the user's verdict, copied to the result line (never filled in by a tool)
"""


class ManifestError(Exception):
    pass


def path(p):
    """A manifest path: absolute as written, else under the repository root."""
    if p is None:
        return None
    q = Path(str(p))
    return q if q.is_absolute() else ROOT / q


def load(fn):
    fn = Path(fn)
    try:
        m = json.loads(fn.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as e:
        raise ManifestError('cannot read manifest %s: %s' % (fn, e))
    m['_file'] = str(fn.resolve())
    return m


def tool(m):
    return TOOL_1803 if (m.get('runtime') or {}).get('efk18') else TOOL_170


def variants(m):
    return m.get('variants') or {'main': {}}


def flag_values(args, flag):
    """Every value given to `flag` in an argv list (`--f v` and `--f=v`); [True] for a bare switch."""
    out = []
    for i, a in enumerate(args):
        if a == flag:
            nxt = args[i + 1] if i + 1 < len(args) else None
            out.append(nxt if nxt is not None and not str(nxt).startswith('--') else True)
        elif str(a).startswith(flag + '='):
            out.append(a.split('=', 1)[1])
    return out


def _same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def _same_path(a, b):
    try:
        return path(a).resolve() == path(b).resolve()
    except OSError:
        return str(a) == str(b)


def builds(m, variant):
    """[(system, build, builder argv after the builder script)] for one variant, in manifest order. The argv is
    exactly what the builder receives: <work> <source> shared + system + --name/--only + build + variant args, then the
    manifest-owned flags when the args do not already give them."""
    work = None     # argv[0]: the variant folder, filled in by the runner
    v = variants(m).get(variant)
    if v is None:
        raise ManifestError('no variant %r (have %s)' % (variant, ', '.join(variants(m))))
    out = []
    for s in m['systems']:
        for b in s['builds']:
            args = [str(x) for x in (m.get('shared_args') or [])] + [str(x) for x in (s.get('args') or [])]
            args += ['--name', b['name']]
            if b.get('only'):
                args += ['--only', ','.join(b['only'])]
            args += [str(x) for x in (b.get('args') or [])]
            args += [str(x) for x in (v.get('args') or [])]
            args += [str(x) for x in ((v.get('builds') or {}).get(b['name'], {}).get('args') or [])]
            if not flag_values(args, '--exports'):
                args += ['--exports', str(path(m['exports']))]
            meshes = (m.get('inputs') or {}).get('meshes')
            if meshes and s['builder'] == 'cascade' and not flag_values(args, '--meshes'):
                args += ['--meshes', str(path(meshes))]
            if (m.get('runtime') or {}).get('efk18') and not flag_values(args, '--efk18'):
                args += ['--efk18']
            if s['builder'] == 'cascade' and not flag_values(args, '--time-offset'):
                args += ['--time-offset', repr(float(s['offset_s']))]
            out.append((s, b, [work, str(path(s['source']))] + args))
    return out


def env(m):
    """The environment every builder run gets: NIAGARA_FIELDS from inputs.vector_fields (Windows paths, so Git Bash
    path conversion cannot break them: build_effekseer.field_root_problems)."""
    e = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', MSYS_NO_PATHCONV='1')
    vf = (m.get('inputs') or {}).get('vector_fields')
    if vf:
        e['NIAGARA_FIELDS'] = os.pathsep.join(str(path(r)) for r in ([vf] if isinstance(vf, str) else vf))
    return e


def problems(m):
    """Everything wrong with the manifest itself, before any source is read: [str]. Empty = usable."""
    p = []
    for k in ('schema', 'effect', 'game', 'skill', 'runtime', 'exports', 'shared_args', 'systems'):
        if k not in m:
            p.append('manifest: %s missing' % k)
    if p:
        return p
    if m['schema'] != 'effectjob/1':
        p.append('manifest: schema %r, expected "effectjob/1"' % m['schema'])
    if not path(m['exports']).is_dir():
        p.append('exports: %s is not a folder' % m['exports'])
    if not tool(m).is_file():
        p.append('tool: %s not built (dotnet build, AGENTS.md; a worktree needs its bin junction)' % tool(m))
    inp = m.get('inputs') or {}
    if inp.get('meshes') and not path(inp['meshes']).is_dir():
        p.append('inputs.meshes: %s is not a folder' % inp['meshes'])
    vf = inp.get('vector_fields')
    for r in ([vf] if isinstance(vf, str) else vf or []):
        if not path(r).is_dir():
            p.append('inputs.vector_fields: %s is not a folder (no field would load its voxels)' % r)
    for blk in ('install', 'playback'):
        pj = (m.get(blk) or {}).get('project')
        if pj and not (path(pj) / 'data' / 'Animations.json').is_file():
            p.append('%s.project: %s is not a game project (no data/Animations.json); a project outside the repository '
                     'needs an absolute path (relative paths start at the repository root, which differs in a worktree)'
                     % (blk, pj))
    seq = m.get('sequence') or {}
    if seq.get('file') and not path(seq['file']).is_file():
        p.append('sequence.file: %s not found' % seq['file'])
    for f in m.get('footage') or []:
        if not path(f.get('path')).exists():
            p.append('footage: %s not found' % f.get('path'))
        if not f.get('fps'):
            p.append('footage: %s has no fps' % f.get('path'))
    for c in m.get('renderdoc') or []:
        if not path(c.get('rdc')).is_file():
            p.append('renderdoc: %s not found' % c.get('rdc'))
        if c.get('labels') and not path(c['labels']).is_file():
            p.append('renderdoc: labels %s not found' % c['labels'])
    orc = m.get('oracle')
    if orc:
        if orc.get('script') and not path(orc['script']).is_file():
            p.append('oracle.script: %s not found' % orc['script'])
        for sid, o in (orc.get('systems') or {}).items():
            if sid not in {s.get('id') for s in m['systems']}:
                p.append('oracle.systems: %s is not a manifest system' % sid)
    cam = m.get('camera') or {}
    names, seen = set(), {}
    for s in m['systems']:
        sid = s.get('id', '?')
        for k in ('id', 'builder', 'source', 'offset_s', 'offset_frames', 'builds'):
            if k not in s:
                p.append('system %s: %s missing' % (sid, k))
        if any(k not in s for k in ('builder', 'source', 'offset_s', 'offset_frames', 'builds')):
            continue
        if s['builder'] not in BUILDERS:
            p.append('system %s: builder %r (cascade or niagara)' % (sid, s['builder']))
            continue
        if not path(s['source']).is_file():
            p.append('system %s: source %s not found' % (sid, s['source']))
        if int(s['offset_frames']) != int(round(float(s['offset_s']) * FPS)):
            p.append('system %s: offset_frames %s is not offset_s %s x 60 = %d' % (
                sid, s['offset_frames'], s['offset_s'], round(float(s['offset_s']) * FPS)))
        so = s.get('sequence_offset_s')
        if so is not None and not _same(so, s['offset_s']) and not s.get('offset_reason'):
            p.append('system %s: plays at %s s but the sequence says %s s and offset_reason is empty'
                     % (sid, s['offset_s'], so))
        for b in s['builds']:
            if b.get('name') in names:
                p.append('build name %s used twice' % b.get('name'))
            names.add(b.get('name'))
            if b.get('only') and s['builder'] == 'niagara':
                p.append('build %s: niagara builds have no --only (use --skip in args)' % b['name'])
            for n in b.get('only') or []:
                if n in seen.get(sid, {}):
                    p.append('system %s: emitter %s is in builds %s and %s (it would draw twice)'
                             % (sid, n, seen[sid][n], b['name']))
                seen.setdefault(sid, {})[n] = b['name']
        for n in list(s.get('excluded') or {}) + list(s.get('accept') or {}):
            if not (s.get('excluded') or {}).get(n, (s.get('accept') or {}).get(n)):
                p.append('system %s: %s is excluded/accepted without a reason' % (sid, n))
    if p:
        return p
    nb = sum(len(s['builds']) for s in m['systems'])
    if nb > 1 and not (m.get('combine') or {}).get('name'):
        p.append('combine.name missing: %d builds need one combined effect per variant' % nb)
    for vk in variants(m):
        for bn in ((variants(m)[vk] or {}).get('builds') or {}):
            if bn not in names:
                p.append('variant %s: build %s is not in any system' % (vk, bn))
        for s, b, argv in builds(m, vk):
            args = argv[2:]
            for flag, want, same in (('--exports', m['exports'], _same_path),
                                     ('--meshes', inp.get('meshes'), _same_path),
                                     ('--time-offset', s['offset_s'] if s['builder'] == 'cascade' else None, _same)):
                got = flag_values(args, flag)
                if want is not None and got and any(not same(g, want) for g in got):
                    p.append('variant %s build %s: %s %s differs from the manifest (%s)' % (vk, b['name'], flag, got, want))
            if len(flag_values(args, '--name')) != 1 or len(flag_values(args, '--only')) > 1:
                p.append('variant %s build %s: --name/--only given in args; the manifest owns them' % (vk, b['name']))
            for ck, flag in (('view_pitch', '--view-pitch'), ('root_y', '--root-y'), ('turn', '--turn')):
                got = flag_values(args, flag)
                if ck in cam and got and not _same(got[-1], cam[ck]):
                    p.append('variant %s build %s: %s %s but camera.%s is %s' % (vk, b['name'], flag, got[-1], ck, cam[ck]))
                if ck in cam and not got and s['builder'] == 'cascade' and not _same(cam[ck], DEFAULTS.get(flag, 0)):
                    p.append('variant %s build %s: camera.%s %s but the build leaves %s at its default' % (
                        vk, b['name'], ck, cam[ck], flag))
            for flag in ('--exposure-curve', '--post-process'):
                for g in flag_values(args, flag):
                    if g is not True and not path(g).is_file():
                        p.append('variant %s build %s: %s %s not found (it must be absolute or relative to the repo '
                                 'root)' % (vk, b['name'], flag, g))
    return p


def structural(m):
    """True when every system can be read (required keys, a known builder, a source on disk): preflight then checks
    the emitters even when other manifest problems exist, so a missing input folder lists the emitters it breaks."""
    if any(k not in m for k in ('schema', 'effect', 'game', 'skill', 'runtime', 'exports', 'shared_args', 'systems')):
        return False
    if not path(m['exports']).is_dir():
        return False
    return all(all(k in s for k in ('id', 'builder', 'source', 'offset_s', 'offset_frames', 'builds'))
               and s['builder'] in BUILDERS and path(s['source']).is_file() for s in m['systems'])


DEFAULTS = {'--root-y': -2.5, '--view-pitch': 0.0, '--turn': 0.0}   # build_effekseer.py's own defaults


if __name__ == '__main__':
    print(SCHEMA)
    if len(sys.argv) > 1:
        pr = problems(load(sys.argv[1]))
        print('\n'.join(pr) or 'manifest OK')
        sys.exit(1 if pr else 0)
