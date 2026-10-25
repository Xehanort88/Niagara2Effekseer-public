#!/usr/bin/env python3
"""Rebuild a multi-system effect from its manifest, and account for every emitter.

    python tools/effectjob/effectjob.py preflight <task>/effect.json
    python tools/effectjob/effectjob.py run <task>/effect.json [--variants a,b] [--replace] [--playback]
    python tools/effectjob/effectjob.py ledger <task>/effect.json [--variants a,b]
    python tools/effectjob/effectjob.py playback <task>/effect.json [--variants a,b]
    python tools/effectjob/effectjob.py oracle <task>/effect.json [--systems A,B]

The task folder is the manifest's folder; each variant builds into <task>/<variant>/ (refused when it exists, unless
--replace, which says so). `run`:
  1. preflight (tools/effectjob/preflight.py): any missing input stops the job with the list, before any build
     (exit 2); written to <task>/preflight.json
  2. every build of every system: Cascade through .claude/skills/cascade-build/check_build.py (build + its checks),
     Niagara through tools/niagara/build_niagara.py; the exact argv, cwd, NIAGARA_FIELDS, exit code and time go to
     <variant>/build/<name>.argv.json before and after the run, the output to <variant>/<name>.check.log
  3. one combined effect per variant: each build under a host delayed by its system's offset_frames (the round scripts'
     combine.py), built with `edit`, then dump, lint --strict --catalogue, hlslcheck on its materials
  4. --playback: the final file in a real battle (tools/ingame/run.js time mode), the manifest's playback.anim_id
     pointed at it in memory (--map + --prepare; the project is not changed)
  5. ledger.json + ledger.md (tools/effectjob/ledger.py) and job.json
Result lines, kept apart: BUILD (build correctness), PLAYBACK (the in-game end check), COVERAGE (the ledger), VISUAL
(the user's verdict from the manifest; no tool fills it in). Last line `EFFECT JOB COMPLETE <effect>` (exit 0) or
`EFFECT JOB INCOMPLETE <effect>: <reasons>` (exit 1). It never installs: install.py prints the command to run.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import manifest as MF  # noqa: E402
import preflight as PF  # noqa: E402

ROOT = MF.ROOT


def log(msg):
    print(msg, flush=True)


def run_logged(argv, log_file, env, argv_file=None, extra=None):
    """Run argv from the repo root; record it (before and after) in argv_file; return (exit code, output)."""
    rec = {'argv': [str(a) for a in argv], 'cwd': str(ROOT), 'NIAGARA_FIELDS': env.get('NIAGARA_FIELDS'),
           'started': datetime.datetime.now().isoformat(timespec='seconds'), 'exit': None}
    rec.update(extra or {})
    if argv_file:
        Path(argv_file).parent.mkdir(parents=True, exist_ok=True)
        Path(argv_file).write_text(json.dumps(rec, indent=1), encoding='utf-8')
    t0 = time.time()
    p = subprocess.run(rec['argv'], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    out = p.stdout + p.stderr
    if log_file:
        Path(log_file).write_text(out, encoding='utf-8')
    rec.update(exit=p.returncode, seconds=round(time.time() - t0, 1))
    if argv_file:
        Path(argv_file).write_text(json.dumps(rec, indent=1), encoding='utf-8')
    return p.returncode, out


def git_head():
    p = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def do_preflight(m, task):
    res = PF.run(m)
    (task / 'preflight.json').write_text(json.dumps(res, indent=1, default=str), encoding='utf-8')
    n = len(res['emitters'])
    counts = {}
    for r in res['emitters']:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    log('preflight: %d emitter(s) in %d system(s): %s' % (n, len(m.get('systems') or []),
                                                          ', '.join('%d %s' % (v, k) for k, v in sorted(counts.items()))))
    for l in PF.report_lines(res):
        log(l)
    if not res['ok']:
        log('PREFLIGHT FAIL: %d problem(s); nothing was built' % len(res['problems']))
        for p in res['problems']:
            log('  - ' + p)
    else:
        log('PREFLIGHT OK')
    return res


def combine_spec(m, work, variant, blank):
    """The combined effect's edit spec (as the round scripts' combine.py): an unused blank node 0, then one host per
    build delayed by its system's offset_frames, the build's Root copied under it."""
    edits = [{'node': '0', 'set': {'Name': 'UnusedBlank', 'DrawingValues/Type': 'None', 'CommonValues/Life': 1}}]
    i = 0
    for s in m['systems']:
        for b in s['builds']:
            i += 1
            edits.append({'node': 'root', 'add': {'file': str(blank), 'from': '0', 'name': b['name'] + '_host'},
                          'set': {'DrawingValues/Type': 'None', 'CommonValues/MaxGeneration': 1,
                                  'CommonValues/GenerationTimeOffset': int(s['offset_frames']), 'CommonValues/Life': 1000,
                                  'CommonValues/RemoveWhenLifeIsExtinct': False,
                                  'CommonValues/RemoveWhenAllChildrenAreRemoved': True}})
            edits.append({'node': str(i), 'add': {'file': str(work / 'build' / (b['name'] + '.efkefc')), 'from': '0',
                                                  'name': 'Root'}})
    name = m['combine']['name'].replace('{variant}', variant)
    return name, {'source': str(blank), 'name': name, 'endFrame': int(m['combine'].get('end_frame', 240)), 'edits': edits}


def build_variant(m, task, variant, env, results):
    """Steps 2-3 for one variant. Appends result strings to results['build'][variant]; returns the final .efkefc."""
    work = task / variant
    work.mkdir(parents=True, exist_ok=True)
    tool = MF.tool(m)
    fails = []
    lines = results['build'].setdefault(variant, [])
    for system in m['systems']:
        if system.get('_staging'):
            f=Path(system['params_file']);f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text(json.dumps(system['_staging'],indent=1),encoding='utf-8')
    if m.get('_clock_mapping'):
        (task/'clock-mapping.json').write_text(json.dumps(m['_clock_mapping'],indent=1),encoding='utf-8')
    for s, b, argv in MF.builds(m, variant):
        argv[0] = str(work)
        if MF.cascade_like(s):
            cmd = ([sys.executable, '-B', str(MF.CHECK_BUILD)] + {'cascade-port': ['--port'], 'cascade-ts': ['--ts']}.get(
                s['builder'], []) + argv)
        else:
            meshes = (m.get('inputs') or {}).get('meshes')
            if meshes:     # build_niagara reads <work>/mesh/Model only
                (work / 'mesh' / 'Model').mkdir(parents=True, exist_ok=True)
                for f in MF.path(meshes).glob('*.efkmodel'):
                    shutil.copy2(f, work / 'mesh' / 'Model' / f.name)
            cmd = [sys.executable, '-B', str(MF.BUILDERS['niagara'])] + argv
        log('[%s] build %s (%s, %s)' % (variant, b['name'], s['id'], s['builder']))
        code, out = run_logged(cmd, work / (b['name'] + '.check.log'), env, work / 'build' / (b['name'] + '.argv.json'),
                               {'system': s['id'], 'builder': s['builder'], 'git': git_head(),
                                'builder_argv': [str(MF.BUILDERS[s['builder']])] + argv})
        if MF.cascade_like(s):
            last = [l for l in out.splitlines() if l.startswith('CASCADE BUILD ')]
            line = last[-1] if last else 'CASCADE BUILD FAIL %s: no result line (exit %d)' % (b['name'], code)
            for l in out.splitlines():
                if re.match(r'^(PASS|FAIL|NOTE) ', l):
                    log('    ' + l)
        else:
            verify = [l for l in out.splitlines() if l.startswith('VERIFY')]
            ok = code == 0 and verify and verify[-1].startswith('VERIFY OK')
            line = 'NIAGARA BUILD %s %s%s' % ('PASS' if ok else 'FAIL', b['name'], '' if ok else ': exit %d, %s' % (
                code, verify[-1] if verify else 'no VERIFY line'))
        log('  ' + line)
        lines.append(line)
        if code != 0 or ' FAIL ' in ' %s ' % line.split(':')[0]:
            fails.append(b['name'])
    nb = sum(len(s['builds']) for s in m['systems'])
    if nb == 1:
        final = work / 'build' / (m['systems'][0]['builds'][0]['name'] + '.efkefc')
    else:
        blank = work / 'blank.efkefc'
        if not blank.is_file():
            sys.path.insert(0, str(ROOT / 'tools' / 'author'))
            from efkauthor import make_blank
            make_blank(blank, 60, tool=tool)
        name, spec = combine_spec(m, work, variant, blank)
        spec_f = work / 'build' / (name + '.edit.json')
        spec_f.write_text(json.dumps(spec, indent=1), encoding='utf-8')
        final = work / 'build' / (name + '.efkefc')
        code, out = run_logged(['dotnet', str(tool), 'edit', str(spec_f), str(final)], work / (name + '.edit.log'), env,
                               work / 'build' / (name + '.argv.json'), {'step': 'combine'})
        verify = [l for l in out.splitlines() if l.startswith('VERIFY')]
        ok = code == 0 and verify and verify[-1].startswith('VERIFY OK')
        lines.append('COMBINE %s %s: %s' % ('PASS' if ok else 'FAIL', name, verify[-1] if verify else 'exit %d' % code))
        log('  ' + lines[-1])
        if not ok:
            fails.append('combine')
    # A combined HDR effect must retain its metadata; differing grades cannot be flattened into one handle.
    hdr_files = [work / 'build' / (b['name'] + '.hdr.json') for s, b, _ in MF.builds(m, variant)]
    if any(p.exists() for p in hdr_files):
        try:
            sides = [json.loads(p.read_text(encoding='utf-8')) for p in hdr_files]
            if any((x['grade'], x['bloom']) != (sides[0]['grade'], sides[0]['bloom']) for x in sides):
                raise ValueError('combined HDR systems require the same grade and bloom')
            side = dict(sides[0], fallback=final.stem.removesuffix('-hdr') + '-ldr')
            final.with_suffix('.hdr.json').write_text(json.dumps(side, indent=2) + '\n', encoding='utf-8')
        except (OSError, ValueError, KeyError) as ex:
            lines.append('HDR FAIL: ' + str(ex)); fails.append('hdr')
    if final.is_file():
        code, out = run_logged(['dotnet', str(tool), 'lint', str(final), '--strict', '--catalogue',
                                str(ROOT / 'out/catalogue.json')], work / (final.stem + '.lint.log'), env)
        mm = re.search(r'LINT: (\d+) effects, (\d+) ours / (\d+) pack findings, (\d+) unreadable', out)
        ok = code == 0 and mm and mm.group(2) == '0' and mm.group(4) == '0'
        lines.append('LINT %s %s: %s' % ('PASS' if ok else 'FAIL', final.name, mm.group(0) if mm else 'no LINT line (exit %d)' % code))
        log('  ' + lines[-1])
        if not ok:
            fails.append('lint')
        p = subprocess.run(['dotnet', str(tool), 'dump', str(final)], capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
        mats = sorted(set(re.findall(r'MaterialFile/Path = (.+\.efkmat)\s*$', p.stdout, re.M)))
        paths = [str(q) for mp in mats for q in (final.parent / mp, final.parent / 'Materials' / mp) if q.is_file()][:len(mats) * 2]
        paths = list(dict.fromkeys(paths))
        if len({Path(x).name for x in paths}) < len(mats):
            lines.append('HLSL FAIL %s: material(s) named by the effect not on disk' % final.name)
            fails.append('hlsl')
        elif paths:
            code, out = run_logged([sys.executable, '-B', str(ROOT / 'tools/hlslcheck/hlslcheck.py')] + paths,
                                   work / (final.stem + '.hlsl.log'), env)
            lines.append('HLSL %s %s: %s' % ('PASS' if code == 0 else 'FAIL', final.name,
                                            (out.strip().splitlines() or ['?'])[-1]))
            if code:
                fails.append('hlsl')
        else:
            lines.append('HLSL PASS %s: no file materials' % final.name)
        log('  ' + lines[-1])
    else:
        lines.append('FINAL FAIL: %s not on disk' % final)
        fails.append('final')
    (work / 'build-results.json').write_text(json.dumps({'lines': lines, 'final': str(final), 'final_mtime':
                                                         final.stat().st_mtime if final.is_file() else None}, indent=1),
                                             encoding='utf-8')
    return final, fails


def playback(m, task, variant, final, env):
    """tools/ingame end check of the final file: (state, text), also kept in <task>/playback-<variant>.result.json."""
    state, text = _playback(m, task, variant, final, env)
    (task / ('playback-%s.result.json' % variant)).write_text(json.dumps(
        {'state': state, 'text': text, 'final': str(final), 'final_mtime': final.stat().st_mtime if final.is_file() else None},
        indent=1), encoding='utf-8')
    return state, text


def saved_playback(task, variant, final):
    """A playback result on disk for this very file (same mtime), else None."""
    f = task / ('playback-%s.result.json' % variant)
    if not f.is_file():
        return None
    r = json.loads(f.read_text(encoding='utf-8'))
    if not final.is_file() or r.get('final_mtime') != final.stat().st_mtime:
        return 'STALE', 'the last playback result is for an older build; run playback again'
    return r['state'], r['text']


def _playback(m, task, variant, final, env):
    pb = m.get('playback') or {}
    if not pb.get('project') or not pb.get('anim_id'):
        return 'NOT RUN', 'no playback block in the manifest'
    out = task / 'playback' / variant
    if out.exists():
        shutil.rmtree(out)
    rel = 'EffectJob%s/' % variant
    effect = rel + final.stem
    prep = "$dataAnimations[%d].effectName = %s;" % (int(pb['anim_id']), json.dumps(effect))
    cmd = ['node', str(ROOT / 'tools/ingame/run.js'), '--project', str(MF.path(pb['project'])), '--out', str(out),
           '--ids', str(int(pb['anim_id'])), '--map', '%s=%s' % (rel, final.parent), '--prepare', prep,
           '--port', str(int(pb.get('port', 8941)))]
    if pb.get('max_frames'):
        # run.js counts browser frames here, not game frames: at a 164 Hz display about 2.7 per 60 fps game frame
        # (2026-10-17: --max-frames 900 cut a 331-game-frame effect off as "did not end"). Leave it unset unless needed.
        cmd += ['--max-frames', str(int(pb['max_frames']))]
    code, text = run_logged(cmd, task / ('playback-%s.log' % variant), env, task / ('playback-%s.argv.json' % variant))
    res = out / 'results.jsonl'
    recs = [json.loads(l) for l in res.read_text(encoding='utf-8').splitlines() if l.strip()] if res.is_file() else []
    if not recs:
        return 'FAIL', 'run.js exit %d, no results (%s)' % (code, task / ('playback-%s.log' % variant))
    r = recs[-1]
    cold, warm = r.get('cold') or {}, r.get('warm') or {}
    served = [x for x in r.get('servedEffectFiles') or [] if final.stem in str(x)]
    if cold.get('effectName') != effect or not served:
        return 'FAIL', ('animation %s played %r, served %d file(s) named %s: the --map/--prepare override did not take '
                        'effect' % (pb['anim_id'], cold.get('effectName'), len(served), final.stem))
    ok = r.get('status') in ('ok', 'warn') and code == 0 and cold.get('ended') and warm.get('ended')
    return ('PASS' if ok else 'FAIL',
            'status %s; %s served; cold play ended after %s game frames (freeze %s ms, not preloaded), warm after %s; '
            'peak live instances %s%s' % (r.get('status'), effect, cold.get('gameFrames'), cold.get('worstFrameDuringLoadMs'),
                                          warm.get('gameFrames'), cold.get('peakLiveInstances'),
                                          '; ' + '; '.join(r.get('failures', []) + r.get('warnings', []))
                                          if r.get('failures') or r.get('warnings') else ''))


def write_ledger(m, task, variants, pre, results):
    import ledger as L
    per = {}
    for v in variants:
        per[v] = L.build_rows(m, v, task / v, pre)
    lines = result_lines(m, variants, results, per)
    data = {'manifest': m['_file'], 'generated': datetime.datetime.now().isoformat(timespec='seconds'),
            'git': git_head(), 'columns': list(L.COLUMNS), 'results': lines,
            'variants': {v: {'summary': L.summary(m, rows), 'notes': notes, 'rows': rows} for v, (rows, notes) in per.items()}}
    (task / 'ledger.json').write_text(json.dumps(data, indent=1), encoding='utf-8')
    (task / 'ledger.md').write_text(L.markdown(m, per, lines), encoding='utf-8')
    return per, lines


def result_lines(m, variants, results, per):
    import ledger as L
    out = []
    for v in variants:
        b = results.get('build', {}).get(v)
        task = Path(m['_file']).parent
        if b is None and (task / v / 'build-results.json').is_file():
            import ledger as L_
            saved = json.loads((task / v / 'build-results.json').read_text(encoding='utf-8'))
            final = L_.final_effect(m, task / v, v)
            if final.is_file() and saved.get('final_mtime') == final.stat().st_mtime:
                b = saved['lines']
        if b is None:
            out.append('BUILD %s: no build result on disk for the current final file (run it again)' % v)
        else:
            bad = [l for l in b if ' FAIL' in l.split(':')[0]]
            out.append('BUILD %s %s: %d check line(s)%s' % ('FAIL' if bad else 'PASS', v, len(b), '; ' + '; '.join(bad) if bad else ''))
        pb = results.get('playback', {}).get(v)
        if pb is None:
            import ledger as L_
            pb = saved_playback(Path(m['_file']).parent, v, L_.final_effect(m, Path(m['_file']).parent / v, v))
        out.append('PLAYBACK %s %s' % (v, '%s: %s' % pb if pb else 'NOT RUN: pass --playback (tools/ingame end check)'))
        sm = L.summary(m, per[v][0])
        out.append('COVERAGE %s %s: %d rows, %d built, %d saved, %d gap(s), %d without status%s' % (
            sm['coverage'], v, sm['rows'], sm['built'], sm['saved'], sm['gap'], sm['incomplete'],
            '; n/a: ' + ', '.join(sm['na_columns']) if sm['na_columns'] else ''))
    vis = m.get('visual') or {}
    out.append('VISUAL: %s' % ('%s (%s, %s)' % (vis['verdict'], vis.get('by', '?'), vis.get('date', '?')) if vis.get('verdict')
                               else 'not judged; the user judges the look (no tool fills this in)'))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('preflight', 'run', 'ledger', 'playback', 'oracle', 'schema'))
    ap.add_argument('manifest', nargs='?')
    ap.add_argument('--variants', help='comma list (default: all in the manifest)')
    ap.add_argument('--replace', action='store_true', help='rebuild into variant folders that already exist')
    ap.add_argument('--playback', action='store_true', help='run the tools/ingame end check of each final file')
    ap.add_argument('--systems', help='oracle: comma list of system ids')
    a = ap.parse_args()
    if a.command == 'schema':
        print(MF.SCHEMA)
        return 0
    if not a.manifest:
        ap.error('manifest required')
    try:
        m = MF.load(a.manifest)
    except MF.ManifestError as e:
        log('EFFECT JOB INCOMPLETE ?: %s' % e)
        return 2
    task = Path(a.manifest).absolute().parent
    variants = a.variants.split(',') if a.variants else list(MF.variants(m))
    env = MF.env(m)
    if a.command == 'oracle':
        return run_oracle(m, task, env, a.systems.split(',') if a.systems else None)
    pre = do_preflight(m, task)
    if a.command == 'preflight':
        return 0 if pre['ok'] else 2
    results = {'build': {}, 'playback': {}}
    if a.command == 'run':
        if not pre['ok']:
            (task / 'job.json').write_text(json.dumps({'result': 'PREFLIGHT FAIL', 'problems': pre['problems']}, indent=1))
            log('EFFECT JOB INCOMPLETE %s: preflight' % m.get('effect'))
            return 2
        for v in variants:
            if (task / v).exists():
                if not a.replace:
                    log('EFFECT JOB INCOMPLETE %s: %s exists; pass --replace to rebuild into it' % (m['effect'], task / v))
                    return 2
                log('[%s] --replace: deleting the existing %s and rebuilding' % (v, task / v))
                shutil.rmtree(task / v)
        finals = {}
        for v in variants:
            finals[v], _ = build_variant(m, task, v, env, results)
            if a.playback and finals[v].is_file():
                results['playback'][v] = playback(m, task, v, finals[v], env)
                log('  PLAYBACK %s %s: %s' % (v, *results['playback'][v]))
    for v in variants:
        if not (task / v).exists():
            log('EFFECT JOB INCOMPLETE %s: %s not built' % (m['effect'], task / v))
            return 1
    if a.command == 'playback':
        import ledger as L
        for v in variants:
            results['playback'][v] = playback(m, task, v, L.final_effect(m, task / v, v), env)
            log('  PLAYBACK %s %s: %s' % (v, *results['playback'][v]))
    per, lines = write_ledger(m, task, variants, pre, results)
    ins = m.get('install') or {}
    install_cmds = []
    import ledger as L
    for v in variants:
        if ins.get('dest'):
            final = L.final_effect(m, task / v, v)
            spec = final.with_suffix('.edit.json') if final.with_suffix('.edit.json').is_file() else None
            install_cmds.append('python tools/install/install.py --project "%s" --spec "%s" --dest "%s" %s --tool "%s"' % (
                ins['project'], spec or final, ins['dest'].replace('{variant}', v),
                '--id %d' % ins['anim_id'] if ins.get('anim_id') else '--name "%s"' % (ins.get('name') or '').replace('{variant}', v),
                MF.tool(m)))
    job = {'manifest': m['_file'], 'git': git_head(), 'results': lines, 'build_checks': results['build'],
           'playback': results['playback'], 'install_commands_not_run': install_cmds}
    (task / 'job.json').write_text(json.dumps(job, indent=1), encoding='utf-8')
    for l in lines:
        log(l)
    log('ledger: %s' % (task / 'ledger.md'))
    reasons = [l.split(':')[0] for l in lines if l.startswith(('BUILD FAIL', 'PLAYBACK', 'COVERAGE INCOMPLETE'))
               and ('FAIL' in l.split(':')[0] or 'INCOMPLETE' in l.split(':')[0])]
    if reasons:
        log('EFFECT JOB INCOMPLETE %s: %s' % (m['effect'], ', '.join(reasons)))
        return 1
    log('EFFECT JOB COMPLETE %s' % m['effect'])
    return 0


def run_oracle(m, task, env, only=None):
    """Run the manifest's oracle script once per listed system: <dump> <dossier> <background> <report folder>."""
    o = m.get('oracle') or {}
    if not o.get('script'):
        log('ORACLE: no oracle.script in the manifest')
        return 2
    by_id = {s['id']: s for s in m['systems']}
    bad = 0
    for sid, so in (o.get('systems') or {}).items():
        if only and sid not in only:
            continue
        s = by_id[sid]
        rep = MF.path(so['report'])
        args = [sys.executable, '-B', str(MF.path(o['script'])), str(MF.path(s['source'])), str(MF.path(so['dossier'])),
                str(MF.path(o['background'])), str(rep.parent), '--exports', str(MF.path(m['exports'])),
                '--frames', str(so.get('frames', o.get('frames', '0:240:10'))), '--ss', str(o.get('ss', 1))]
        args += [str(x) for x in (o.get('args') or [])] + [str(x) for x in (so.get('args') or [])]
        log('oracle %s -> %s' % (sid, rep.parent))
        code, out = run_logged(args, rep.parent.parent / ('%s.oracle.log' % sid), env,
                               rep.parent.parent / ('%s.oracle.argv.json' % sid))
        ok = code == 0 and rep.is_file()
        bad += not ok
        log('  ORACLE %s %s%s' % ('PASS' if ok else 'FAIL', sid, '' if ok else ': exit %d' % code))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
