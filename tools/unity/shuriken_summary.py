"""Summarise Unity particle prefabs (Shuriken): hierarchy, every ParticleSystem's enabled modules in inspector units,
renderers, materials, sub-emitters, other components, and a census across all inputs.

python tools/unity/shuriken_summary.py <prefab.prefab | dump.json | folder>... --out <new folder> [--assets <export>]

Inputs are Unity YAML (an AssetRipper export or a project's own `.prefab`) or `unity_dump.py` JSON. Folders are
searched for `*.prefab` and `*.unitydump.json`. `--assets` names folders whose `.meta` files resolve the guids a
YAML prefab points at (materials, textures, shaders, scripts); repeat it for several. Writes
`<out>/NNN-<name>/summary.json` + `summary.md` per input and `<out>/census.json` + `census.md`.
Exit 0 every input summarised, 1 one or more could not be (named in census.md), 2 usage error or no input.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shuriken as S  # noqa: E402
import unityasset as U  # noqa: E402

PATTERNS = ('*.prefab', '*.unitydump.json')
PS_CLASSES = ('ParticleSystem', 'ParticleSystemRenderer')


def find_inputs(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            for pat in PATTERNS:
                out.extend(sorted(p.rglob(pat), key=str))
        elif p.is_file():
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


def component_label(c):
    return 'MonoBehaviour %s' % U.script_name(c) if c.cls == 'MonoBehaviour' else c.cls


def other_component(c):
    """A component this tool has no reader for: its class (script) and stored fields, minus Unity bookkeeping."""
    skip = {'m_ObjectHideFlags', 'm_CorrespondingSourceObject', 'm_PrefabInstance', 'm_PrefabAsset',
            'm_GameObject', 'm_Script', 'm_EditorHideFlags', 'm_EditorClassIdentifier', 'serializedVersion',
            'm_Name', 'm_Enabled'}
    fields = {k: S.decode(v, c.cls, k, c.doc) for k, v in c.data.items() if k not in skip}
    return {'class': component_label(c), 'enabled': U.flag(c.data.get('m_Enabled', 1)), 'fields': fields}


def summarise(doc):
    paths, systems, others, materials, problems, unresolved = {}, [], [], {}, [], set()
    hierarchy = []
    tops = U.roots(doc)
    if not tops:
        problems.append('no root GameObject (no Transform without a parent)')
    for top in tops:
        for path, go, depth in U.walk(top):
            comps = U.components(go)
            paths[go.id] = path
            for c in comps:
                paths[c.id] = path
            hierarchy.append({'path': path, 'depth': depth, 'active': U.flag(go.data.get('m_IsActive', 1)),
                              'components': [component_label(c) for c in comps],
                              'transform': U.transform_summary(U.transform_of(go))})
    for top in tops:
        for path, go, _ in U.walk(top):
            comps = U.components(go)
            renderer = next((c for c in comps if c.cls == 'ParticleSystemRenderer'), None)
            for c in comps:
                if c.cls == 'ParticleSystem':
                    s = {'path': path, 'id': c.id, 'active': U.flag(go.data.get('m_IsActive', 1))}
                    s.update(S.system_summary(c, renderer))
                    s['sub_emitters'] = sub_emitters(c, paths)
                    systems.append(s)
                    if renderer is None:
                        problems.append('%s: ParticleSystem without a ParticleSystemRenderer' % path)
                elif c.cls not in PS_CLASSES + ('Transform', 'RectTransform'):
                    o = other_component(c)
                    o['path'] = path
                    others.append(o)
            for ref in (renderer.data.get('m_Materials') or []) if renderer is not None else []:
                m = doc.deref(ref)
                if m is not None and m.cls == 'Material' and m.id not in materials:
                    materials[m.id] = U.material_summary(m)
                elif m is None and doc.describe_ref(ref):
                    unresolved.add(doc.describe_ref(ref))
    nested = [{'id': o.id, 'source': doc.describe_ref(o.data.get('m_SourcePrefab'))}
              for o in doc.of_class('PrefabInstance')]
    if nested:
        problems.append('%d nested prefab instance(s) not expanded (their objects are in another file)' % len(nested))
    return {'source': doc.source, 'format': doc.kind, 'unity_version': doc.unity_version,
            'objects': len(doc.objects), 'roots': [g.name for g in tops], 'hierarchy': hierarchy,
            'systems': systems, 'materials': list(materials.values()), 'other_components': others,
            'nested_prefabs': nested, 'unresolved_materials': sorted(unresolved), 'problems': problems}


def sub_emitters(ps, paths):
    sub = ps.data.get('SubModule') or {}
    if not U.flag(sub.get('enabled', 0)):
        return []
    out = []
    for e in sub.get('subEmitters') or []:
        target = ps.doc.deref(e.get('emitter'))
        props = int(U.num(e.get('properties')))
        out.append({'emitter': paths.get(target.id) if target is not None else ps.doc.describe_ref(e.get('emitter')),
                    'type': S._enum(('SubModule', 'type'), e.get('type', 0)),
                    'inherit': [n for i, n in enumerate(S.SUB_EMITTER_INHERIT) if props >> i & 1],
                    'probability': U.num(e.get('emitProbability'), 1.0)})
    return out


# --- markdown ---------------------------------------------------------------------------------------------------

def fmt(v):
    """A decoded value on one line."""
    if isinstance(v, dict) and 'mode' in v and v.get('mode') in S.CURVE_MODES:
        u = ' ' + v['unit'] if v.get('unit') else ''
        if v['mode'] == 'constant':
            return '%s%s' % (_n(v['value']), u)
        if v['mode'] == 'two_constants':
            return '%s..%s%s' % (_n(v['min']), _n(v['max']), u)
        s = v['samples']
        pts = ', '.join('%s:%s' % (t, _span(s[i])) for t, i in (('0', 0), ('.5', len(s) // 2), ('1', len(s) - 1)))
        return '%s over %s, %s..%s%s (%s)' % (v['mode'].replace('_', ' '), v.get('axis'), _n(v['range'][0]),
                                             _n(v['range'][1]), u, pts)
    if isinstance(v, dict) and 'mode' in v and v.get('mode') in S.GRADIENT_MODES:
        mode = v['mode']
        if mode == 'color':
            return _rgba(v['color'])
        if mode == 'two_colors':
            return 'random between %s and %s' % (_rgba(v['min_color']), _rgba(v['max_color']))
        if mode == 'two_gradients':
            return 'random between gradients %s and %s over %s' % (fmt_gradient(v['min_gradient']),
                                                                   fmt_gradient(v['max_gradient']), v.get('axis'))
        return '%s %s%s' % ('gradient' if mode == 'gradient' else 'random colour from',
                            fmt_gradient(v['gradient']), ' over ' + v['axis'] if mode == 'gradient' else '')
    if isinstance(v, dict) and 'color_keys' in v:
        return fmt_gradient(v)
    if isinstance(v, (dict, list)):
        s = json.dumps(v, separators=(',', ':'))
        return s if len(s) <= 240 else s[:237] + '...'
    return str(v)


def fmt_gradient(g):
    c = ' '.join('%s@%s' % (_rgba(rgb), t) for t, rgb in g['color_keys'])
    a = ' '.join('%s@%s' % (_n(x), t) for t, x in g['alpha_keys'])
    return '[%s; colour %s; alpha %s]' % (g['blend'], c, a)


def _n(x):
    return ('%.4g' % x) if isinstance(x, float) else str(x)


def _span(p):
    return _n(p[0]) if p[0] == p[1] else '%s..%s' % (_n(p[0]), _n(p[1]))


def _rgba(c):
    return '(' + ','.join('%.3g' % x for x in c) + ')'


def markdown(s):
    L = ['# %s' % Path(s['source']).name, '', 'Source: `%s` (%s%s), %d objects.' % (
        s['source'], s['format'], ', Unity ' + s['unity_version'] if s['unity_version'] else '', s['objects']), '']
    if s['problems']:
        L += ['**Problems:**', ''] + ['- %s' % p for p in s['problems']] + ['']
    L += ['## Hierarchy', '']
    for h in s['hierarchy']:
        L.append('%s- %s%s  [%s]' % ('  ' * h['depth'], h['path'].rsplit('/', 1)[-1],
                                     '' if h['active'] else ' (inactive)', ', '.join(h['components'])))
    L += ['', '## Systems (%d)' % len(s['systems']), '']
    for sy in s['systems']:
        t = sy['timing']
        L += ['### %s%s' % (sy['path'], '' if sy['active'] else ' (inactive)'), '']
        L.append('- timing: duration %s s, %s, delay %s s, life %s s, ~%s particles/cycle (max %s)%s%s' % (
            t['duration_s'], 'looping' if t['looping'] else 'one shot', _span(t['start_delay_s']),
            _span(t['lifetime_s']), t['particles_per_cycle'], t['max_particles'],
            ', last spawn %s s' % t['last_spawn_s'] if 'last_spawn_s' in t else '',
            ', ends by %s s' % t['end_s'] if t.get('end_s') is not None else ''))
        for b in t.get('bursts', []):
            L.append('  - burst at %s s: %s particles, %s cycle(s) every %s s, p=%s, fires %s time(s) per cycle' % (
                b['time_s'], _span(b['count']), b['cycles'] or 'infinite', b['interval_s'], b['probability'],
                b['fires_in_duration']))
        r = sy['renderer']
        if r:
            L.append('- renderer: %s, align %s, materials %s%s' % (
                r.get('RenderMode'), r.get('RenderAlignment'), ', '.join(m or 'none' for m in r['Materials']),
                ', meshes ' + ', '.join(r['Meshes']) if r['Meshes'] else ''))
        main = sy['main']
        L.append('- main: ' + ', '.join('%s %s' % (k, fmt(main[k])) for k in (
            'moveWithTransform', 'scalingMode', 'simulationSpeed', 'stopAction', 'autoRandomSeed', 'randomSeed')
            if k in main))
        for name, m in sy['modules'].items():
            L.append('- **%s**' % name)
            for k, v in m.items():
                unit = S.UNITS.get((name, k)) if isinstance(v, (int, float, list)) else None
                L.append('  - %s: %s%s' % (k, fmt(v), ' ' + unit if unit else ''))
        if sy['disabled_modules']:
            L.append('- off: ' + ', '.join(sy['disabled_modules']))
        for e in sy['sub_emitters']:
            L.append('- sub-emitter on %s: %s (inherits %s, p=%s)' % (e['type'], e['emitter'],
                                                                      ', '.join(e['inherit']) or 'nothing',
                                                                      e['probability']))
        L.append('')
    if s['materials']:
        L += ['## Materials', '']
        for m in s['materials']:
            L.append('### %s' % m['name'])
            L.append('- shader: %s' % m['shader'])
            if 'blend' in m:
                L.append('- blend: %s' % ', '.join('%s %s' % kv for kv in m['blend'].items()))
            for k, t in m['textures'].items():
                L.append('- %s: %s%s' % (k, t['texture'], ' scale %s offset %s' % (t['scale'], t['offset'])
                                          if 'scale' in t else ''))
            if m['colors']:
                L.append('- colours: ' + ', '.join('%s %s' % (k, _rgba(c)) for k, c in m['colors'].items()))
            if m['floats']:
                L.append('- floats: ' + ', '.join('%s %s' % (k, _n(x)) for k, x in m['floats'].items()))
            if m['keywords']:
                L.append('- keywords: ' + ' '.join(m['keywords']))
            L.append('')
    if s['other_components']:
        L += ['## Other components', '']
        for o in s['other_components']:
            L.append('- %s on %s%s: %s' % (o['class'], o['path'], '' if o['enabled'] else ' (disabled)',
                                          fmt(o['fields'])))
        L.append('')
    return '\n'.join(L) + '\n'


# --- census -----------------------------------------------------------------------------------------------------

def census(summaries, failures):
    c = {k: Counter() for k in ('modules', 'render_mode', 'render_alignment', 'shape', 'simulation_space',
                                'shader', 'blend', 'components', 'curve_modes')}
    n_sys = looping = 0
    for s in summaries:
        for o in s['other_components']:
            c['components'][o['class']] += 1
        for m in s['materials']:
            c['shader'][m['shader']] += 1
            c['blend'][m.get('blend', {}).get('kind', 'not stated')] += 1
        for sy in s['systems']:
            n_sys += 1
            looping += sy['timing']['looping']
            for name, m in sy['modules'].items():
                c['modules'][name] += 1
                for k, v in m.items():
                    if isinstance(v, dict) and v.get('mode') in S.CURVE_MODES:
                        c['curve_modes'][v['mode']] += 1
            if sy['renderer']:
                c['render_mode'][sy['renderer'].get('RenderMode')] += 1
                c['render_alignment'][sy['renderer'].get('RenderAlignment')] += 1
            c['simulation_space'][sy['main'].get('moveWithTransform')] += 1
            if 'ShapeModule' in sy['modules']:
                c['shape'][sy['modules']['ShapeModule'].get('type')] += 1
    return {'inputs': len(summaries) + len(failures), 'summarised': len(summaries), 'failures': failures,
            'systems': n_sys, 'looping_systems': looping,
            **{k: dict(v.most_common()) for k, v in c.items()}}


def census_markdown(c):
    L = ['# Census', '', '%d inputs, %d summarised, %d systems (%d looping).' % (
        c['inputs'], c['summarised'], c['systems'], c['looping_systems']), '']
    if c['failures']:
        L += ['## Not summarised', ''] + ['- `%s`: %s' % (f['source'], ' '.join(f['error'].split()))
                                          for f in c['failures']] + ['']
    for k in ('modules', 'render_mode', 'render_alignment', 'shape', 'simulation_space', 'curve_modes', 'blend',
              'shader', 'components'):
        L += ['## %s' % k.replace('_', ' '), '', '| value | count |', '|---|---|']
        L += ['| %s | %d |' % (str(v).replace('|', '\\|'), n) for v, n in c[k].items()] + ['']
    return '\n'.join(L) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('inputs', nargs='+')
    ap.add_argument('--out', required=True, type=Path, help='new or empty output folder')
    ap.add_argument('--assets', action='append', default=[], help='folder whose .meta files resolve guids')
    a = ap.parse_args(argv)
    files = find_inputs(a.inputs)
    if not files:
        print('no input: give .prefab / .unitydump.json files or folders holding them', file=sys.stderr)
        return 2
    if a.out.exists() and (not a.out.is_dir() or any(a.out.iterdir())):
        print('--out %s exists and is not an empty folder' % a.out, file=sys.stderr)
        return 2
    index = U.AssetIndex(a.assets) if a.assets else None
    a.out.mkdir(parents=True, exist_ok=True)
    summaries, failures = [], []
    for i, f in enumerate(files):
        try:
            s = summarise(U.UnityDoc.load(f, index))
        except (U.UnityError, ValueError, KeyError, TypeError, OSError) as ex:
            failures.append({'source': str(f), 'error': '%s: %s' % (type(ex).__name__, ex)})
            print('FAILED %s: %s' % (f, ex))
            continue
        d = a.out / ('%03d-%s' % (i, f.name.split('.')[0]))
        d.mkdir()
        (d / 'summary.json').write_text(json.dumps(s, indent=1), encoding='utf-8')
        (d / 'summary.md').write_text(markdown(s), encoding='utf-8')
        summaries.append(s)
        print('%s: %d system(s), %d material(s), %d other component(s)%s' % (
            f.name, len(s['systems']), len(s['materials']), len(s['other_components']),
            ', %d problem(s)' % len(s['problems']) if s['problems'] else ''))
    c = census(summaries, failures)
    (a.out / 'census.json').write_text(json.dumps(c, indent=1), encoding='utf-8')
    (a.out / 'census.md').write_text(census_markdown(c), encoding='utf-8')
    print('census: %d input(s), %d summarised, %d system(s) -> %s' % (c['inputs'], c['summarised'], c['systems'],
                                                                     a.out / 'census.md'))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
