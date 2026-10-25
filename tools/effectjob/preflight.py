"""Preflight: before any build, every enabled emitter of every manifest system has its inputs.

Cascade (read with tools/cascade/cascade.py, decided as build_effekseer.py's main loop decides):
  mesh       a mesh emitter's <mesh>.efkmodel is in inputs.meshes (the builder drops the layer with a note otherwise)
  recipe     the material master has a recipe in build_effekseer.RECIPES (or the builder's own skip: helper emitter
             without a material, an emitter type it does not convert, a ribbon without a particle source)
  textures   every texture parameter the material sets and its shader binds has a png in the export
  field      a GPU emitter with a live VectorFieldLocal finds its voxels under inputs.vector_fields
Niagara (summaries/<System>/summary.json): every enabled emitter's mesh renderer meshes are imported; materials and
textures are not checked here (build_niagara reads them from the decoded scripts), and the record says so.

An input that is missing stops the job unless the system's `accept` gives a reason for that emitter. An emitter in no
build is `excluded` and needs a reason in the system's `excluded`.
"""
import json
import os
import sys
from pathlib import Path

import manifest as MF

sys.path.insert(0, str(MF.ROOT / 'tools' / 'cascade'))


def _builder_module():
    import build_effekseer as B      # heavy (numpy, PIL): only when a Cascade system is checked
    return B


def ts_dossier_problems(m, s):
    """cascade-ts: the system's dossier (its annotated shaders, which the TS oracle compiles) in --tsport-dossiers."""
    sys.path.insert(0, str(MF.ROOT / 'tools' / 'cascade' / 'tsport'))
    import ts_bake
    out = []
    for vk in MF.variants(m):
        for s_, b, argv in MF.builds(m, vk):
            if s_ is not s:
                continue
            ts_bake.DOSSIERS = [Path(d) for v in MF.flag_values(argv[2:], '--tsport-dossiers') for d in v.split(os.pathsep)]
            src = MF.path(s['source']).with_suffix('')
            parts = src.parts
            rel = Path(*parts[len(parts) - parts[::-1].index('Content'):]) if 'Content' in parts else Path(src.name)
            try:
                ts_bake.find_dossier(rel)
            except ValueError as e:
                out.append('system %s, build %s: %s' % (s['id'], b['name'], e))
    return list(dict.fromkeys(out))


def cascade_emitters(m, s):
    """[(index, Emitter)] of a Cascade system; NIAGARA_FIELDS from the manifest must be set before vector_field()."""
    import cascade as C
    return list(enumerate(C.Dump(MF.path(s['source']), MF.path(m['exports'])).emitters()))


def build_of(s, name):
    if s['builder'] == 'niagara' and name in (s.get('excluded') or {}):
        return None
    hits = [b['name'] for b in s['builds'] if not b.get('only') or name in b['only']]
    return hits[0] if hits else None


def _mesh_name(em):
    mesh = (em.typedata.p.get('Mesh') or {}).get('ObjectName', '')
    return mesh.split("'")[1] if "'" in mesh else mesh


def check_cascade_emitter(m, s, em, by_name, B, C):
    """{inputs, problems, skip} for one enabled emitter, decided like build_effekseer.main()."""
    rec = {'inputs': {}, 'problems': [], 'skip': None}
    kind = em.kind
    if kind == 'TypeDataBeam2':
        rec['inputs']['recipe'] = 'beam (beam_nodes: drawn from its own shape, no material recipe)'
        return rec
    if kind == 'TypeDataRibbon':
        src = B.ribbon_source(em, by_name)
        if src is None or not src.enabled:
            rec['skip'] = 'ribbon trail without a particle source (it would follow the emitter): not converted'
            return rec
        rec['inputs']['ribbon source'] = src.name
    elif kind not in ('sprite', 'mesh', 'gpu'):
        rec['skip'] = 'emitter type %s not converted' % kind
        return rec
    mref = em.required.p.get('Material')
    mat = C.Material(MF.path(m['exports']), mref)
    rec_ = B.RECIPES.get(mat.master)
    if rec_ is None and s.get('builder') == 'cascade-port':
        import ot2port      # masters only the original-asset port draws (ky_flare01_primitive, ky_PolarGlow)
        rec_ = (lambda *a: None) if mat.master in ot2port.PORTED_MASTERS else None
    if rec_ is None and s.get('builder') == 'cascade-ts' and mat.master == 'M_EF_Common_GPU_00':
        rec_ = lambda *a: None      # the TS kit's ts_bake registers it (tools/cascade/tsport)
    recipe = C.ue418.recipe_for(mat, rec_)
    if not recipe and not mref:
        rec['skip'] = 'no material: a helper emitter, not drawn'
        return rec
    if not recipe and mat.levels and mat.levels[-1].get('summary'):
        rec['problems'].append('material %s is in the FModel summary format: re-export it as JSON properties'
                               % mat.levels[-1]['name'])
    elif not recipe:
        rec['problems'].append('no recipe for material master %s' % mat.master)
    else:
        rec['inputs']['recipe'] = '%s (%s)' % (getattr(recipe, '__name__', 'recipe'), mat.master)
    files = mat.texture_files()
    bound = B.bound_textures(mat)
    used = [k for k, v in files.items() if v['texture'] and (bound is None or k in bound)]
    rec['inputs']['textures'] = {k: files[k]['png'] for k in used}
    for k in used:
        if not files[k]['png']:
            rec['problems'].append('texture %s (%s) has no png in the export' % (files[k]['texture'], k))
    if kind == 'mesh':
        name = _mesh_name(em)
        f = MF.path((m.get('inputs') or {}).get('meshes') or '') / (name + '.efkmodel') if (m.get('inputs') or {}).get('meshes') else None
        rec['inputs']['mesh'] = str(f) if f else name
        if not f or not f.is_file():
            rec['problems'].append('mesh %s not imported%s' % (name, ' in %s' % f.parent if f else ' (no inputs.meshes)'))
    if kind == 'gpu' and em.live('VectorFieldLocal'):
        vf, why = C.vector_field(em)
        rec['inputs']['vector field'] = vf['source'] if vf else None
        if not vf:
            rec['problems'].append('vector field: %s' % why)
    return rec


def check_niagara_system(m, s):
    """[(index, name, enabled, rec)] from a build_niagara summary.json."""
    d = json.loads(MF.path(s['source']).read_text(encoding='utf-8'))
    out = []
    meshes = (m.get('inputs') or {}).get('meshes')
    for i, e in enumerate(d.get('emitters') or []):
        rec = {'inputs': {'materials/textures': 'not checked for Niagara (build_niagara reads them itself)'},
               'problems': [], 'skip': None, 'kind': ','.join(r.get('type', '?') for r in e.get('renderers') or [])}
        for r in e.get('renderers') or []:
            for mm in r.get('Meshes') or []:
                name = mm.get('Mesh') if isinstance(mm, dict) else mm
                if not name:
                    continue
                f = MF.path(meshes) / (name + '.efkmodel') if meshes else None
                rec['inputs'].setdefault('mesh', []).append(str(f) if f else name)
                if not f or not f.is_file():
                    rec['problems'].append('mesh %s not imported%s' % (name, ' in %s' % f.parent if f else ' (no inputs.meshes)'))
        out.append((i, e.get('handle'), bool(e.get('enabled', True)), rec))
    return out


def run(m):
    """{'ok', 'problems': [str], 'emitters': [record]} for the whole manifest. Records carry system, index, name, kind,
    enabled, build, status (ok | off | excluded | builder-skip | accepted | MISSING) and reason."""
    probs = MF.problems(m)
    result = {'ok': False, 'problems': probs, 'emitters': []}
    if not MF.structural(m):
        return result       # the systems themselves cannot be read; the problems say why
    os.environ.update({k: v for k, v in MF.env(m).items() if k == 'NIAGARA_FIELDS'})
    B = C = None
    for s in m['systems']:
        excluded, accept = s.get('excluded') or {}, s.get('accept') or {}
        if MF.cascade_like(s):
            if B is None:
                B = _builder_module()
                import cascade as C
            try:
                ems = cascade_emitters(m, s)
            except Exception as e:      # not a dump the reader knows: say so, do not guess
                probs.append('system %s: cannot read %s (%s: %s)' % (s['id'], s['source'], type(e).__name__, e))
                continue
            if s['builder'] == 'cascade-ts':
                probs += ts_dossier_problems(m, s)
            by_name = {}
            for _, e_ in ems:
                by_name.setdefault(e_.name, e_)
            names = {e_.name for _, e_ in ems}
            items = []
            for i, em in ems:
                rec = check_cascade_emitter(m, s, em, by_name, B, C) if em.enabled else {'inputs': {}, 'problems': [], 'skip': None}
                items.append((i, em.name, em.enabled, dict(rec, kind=em.kind)))
        else:
            items = check_niagara_system(m, s)
            names = {n for _, n, _, _ in items}
        for b in s['builds']:
            for n in b.get('only') or []:
                if n not in names:
                    probs.append('system %s build %s: --only name %s is not an emitter of %s' % (s['id'], b['name'], n, s['source']))
        for n in list(excluded) + list(accept):
            if n not in names:
                probs.append('system %s: %s in excluded/accept is not an emitter of the source' % (s['id'], n))
        for i, name, enabled, rec in items:
            r = {'system': s['id'], 'index': i, 'name': name, 'kind': rec.get('kind'), 'enabled': enabled,
                 'build': build_of(s, name) if enabled else None, 'inputs': rec['inputs'], 'problems': rec['problems']}
            if not enabled:
                r.update(status='off', reason='switched off in the source (LOD bEnabled false): UE never spawns it')
            elif r['build'] is None:
                r.update(status='excluded' if name in excluded else 'MISSING',
                         reason=excluded.get(name) or 'in no build and not in the system\'s excluded list')
            elif rec['skip']:
                r.update(status='builder-skip', reason=rec['skip'])
            elif rec['problems'] and name in accept:
                r.update(status='accepted', reason=accept[name])
            elif rec['problems']:
                r.update(status='MISSING', reason='; '.join(rec['problems']))
            else:
                r.update(status='ok', reason='')
            result['emitters'].append(r)
    for r in result['emitters']:
        if r['status'] == 'MISSING':
            probs.append('%s/#%d %s: %s' % (r['system'], r['index'], r['name'], r['reason']))
    result['ok'] = not probs
    return result


def report_lines(res):
    lines = []
    for r in res['emitters']:
        if r['status'] in ('ok',):
            continue
        lines.append('  %-8s %-12s %s/#%d %s: %s' % ('', r['status'], r['system'], r['index'], r['name'], r['reason']))
    return lines
