"""The emitter ledger: one row per system/emitter of every manifest system (plus every sequence system the manifest
does not list), one cell per stage. Every cell has a state:

  ok         the stage has it                    explained  an explicit reason it is not there (builder skip,
  na         the stage does not apply              excluded, switched off, oracle-gap, not installed)
  gap        a problem (input missing, node dropped, resource missing)
  missing    no status at all: nothing on disk says what happened (the job ends INCOMPLETE)

Columns: in sequence | preflight | built (node names or builder skip reason) | saved (in the final .efkefc) | oracle
(drawn px or oracle-gap) | capture (matched RenderDoc draws; empty when the manifest lists no capture) | installed
(present in the project's file, every resource on disk). A column whose input the manifest does not list (no oracle,
no capture) is `na` for every row and named as such in the summary.
"""
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import efkinfo
import manifest as MF

COLUMNS = ('in sequence', 'preflight', 'built', 'saved', 'oracle', 'capture', 'installed')


def _check_build():
    spec = importlib.util.spec_from_file_location('check_build', MF.CHECK_BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CB = _check_build()


def cell(state, text=''):
    return {'state': state, 'text': text}


_DUMPS = {}


def dump_nodes(tool, efk):
    """[(tree path, name)] of a saved .efkefc (Niagara2Efk dump headers '### 0/4/1 Name'); None when unreadable."""
    key = (str(tool), str(efk), Path(efk).stat().st_mtime if Path(efk).exists() else None)
    if key not in _DUMPS:
        if not Path(efk).is_file():
            _DUMPS[key] = None
        else:
            p = subprocess.run(['dotnet', str(tool), 'dump', str(efk)], capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            _DUMPS[key] = re.findall(r'^### ([\d/]+) (.+)$', p.stdout, re.M) if p.returncode == 0 else None
    return _DUMPS[key]


def sequence_systems(m):
    """[{binding, stem, triggers}] of the emitters the exported sequence plays, or None without a sequence file."""
    seq = m.get('sequence') or {}
    if not seq.get('file'):
        return None
    if seq.get('format') == 'ue5':
        lo,hi=seq.get('range',[0,float('inf')])
        return [q for q in m['_sequence_inventory'] if any(lo <= f < hi for f in q['start_frames'])]
    d = json.loads(MF.path(seq['file']).read_text(encoding='utf-8'))
    out = []
    for b in d.get('bindings') or []:
        if b.get('type') != 'Emitter':
            continue
        tpl = b.get('template') or ''
        trig = [k[0] for t in b.get('tracks') or [] if t.get('track') == 'Particle'
                for k in t.get('keys') or [] if isinstance(k, list) and len(k) > 1 and k[1] == 'trigger']
        out.append({'binding': b.get('binding'), 'stem': Path(tpl).name, 'triggers': trig})
    return out


def read_notes(work, name):
    f = Path(work) / 'build' / (name + '.notes.txt')
    if not f.is_file():
        return None, None
    lines = f.read_text(encoding='utf-8').splitlines()
    built = [x.strip() for x in lines[0][len('built:'):].split(',') if x.strip()] if lines and lines[0].startswith('built:') else []
    return built, lines[1:]


def host_index(m):
    """build name -> host number in the combined effect (1-based, the order the runner adds them)."""
    return {b['name']: i for i, b in enumerate([b for s in m['systems'] for b in s['builds']], 1)}


def final_effect(m, work, variant):
    nb = sum(len(s['builds']) for s in m['systems'])
    if nb > 1:
        return Path(work) / 'build' / (m['combine']['name'].replace('{variant}', variant) + '.efkefc')
    return Path(work) / 'build' / (m['systems'][0]['builds'][0]['name'] + '.efkefc')


def installed_effect(m, variant):
    ins = m.get('install') or {}
    if not ins.get('project') or not ins.get('dest'):
        return None
    dest = ins['dest'].replace('{variant}', variant).replace('\\', '/').strip('/')
    return MF.path(ins['project']) / 'effects' / (dest + '.efkefc')


def oracle_report(m, sid):
    o = ((m.get('oracle') or {}).get('systems') or {}).get(sid)
    if not o:
        return None, None
    f = MF.path(o.get('report')) if o.get('report') else None
    if not f or not f.is_file():
        return f, None
    return f, json.loads(f.read_text(encoding='utf-8'))


def capture_index(m):
    """{system/emitter: [(capture short name, label, [eids], shared by n)]} and job-level problems."""
    idx, probs = {}, []
    for c in m.get('renderdoc') or []:
        if not c.get('labels'):
            continue
        labels = json.loads(MF.path(c['labels']).read_text(encoding='utf-8'))
        by_eid = {l['eid']: l for l in labels}
        listed = set(c.get('event_ids') or [])
        short = Path(c['rdc']).stem[-10:]
        for e in sorted(listed - set(by_eid)):
            probs.append('renderdoc %s: event %s is not in %s' % (short, e, c['labels']))
        for label, keys in (c.get('label_map') or {}).items():
            eids = sorted(l['eid'] for l in labels if l['label'] == label and (not listed or l['eid'] in listed))
            if not eids:
                continue
            for k in keys:
                idx.setdefault(k, []).append((short, label, eids, len(keys)))
    return idx, probs


def build_rows(m, variant, work, pre):
    """The ledger rows of one variant (work = its folder), from what is on disk now. pre = preflight.run(m)."""
    tool = MF.tool(m)
    seq = sequence_systems(m)
    hosts = host_index(m)
    final = final_effect(m, work, variant)
    nodes = dump_nodes(tool, final) or []
    inst = installed_effect(m, variant)
    inst_nodes = dump_nodes(tool, inst) if inst and inst.is_file() else None
    inst_missing = efkinfo.missing(inst) if inst and inst.is_file() else None
    caps, cap_probs = capture_index(m)
    has_caps = bool(m.get('renderdoc'))
    rows, job_notes = [], list(cap_probs)
    nb = sum(len(s['builds']) for s in m['systems'])

    # systems: what the sequence plays against what the manifest lists
    seq_of = {}
    if seq is not None:
        for q in seq:
            hit = [s for s in m['systems'] if (s.get('actor') == q['binding'] if (m.get('sequence') or {}).get('format')=='ue5' else Path(str(s['source'])).stem == q['stem'])]
            if hit:
                seq_of[hit[0]['id']] = q
                continue
            skip = ((m.get('sequence') or {}).get('skip') or {}).get(q['stem'])
            c = cell('explained', 'in the sequence, left out: %s' % skip) if skip else \
                cell('gap', 'the sequence plays %s (trigger %s s) but the manifest lists no such system'
                     % (q['stem'], ', '.join('%g' % t for t in q['triggers']) or '?'))
            rows.append({'key': q['stem'] + '/*', 'system': q['stem'], 'index': None, 'emitter': '*', 'kind': 'system',
                         'cells': dict({k: cell('na') for k in COLUMNS}, **{'in sequence': c})})
    sys_cell = {}
    for s in m['systems']:
        q = seq_of.get(s['id'])
        used = '%g s (%d f)' % (float(s['offset_s']), int(s['offset_frames']))
        if q:
            trig = q['triggers'][0] if q['triggers'] else None
            if trig is not None and abs(trig - float(s['offset_s'])) > 1e-6:
                c = cell('explained' if s.get('offset_reason') else 'gap',
                         'sequence trigger %g s, plays at %s: %s' % (trig, used, s.get('offset_reason') or 'no offset_reason'))
            else:
                c = cell('ok', 'sequence trigger %g s = plays at %s' % (trig if trig is not None else float('nan'), used))
        elif seq is None:
            c = cell('explained' if s.get('offset_reason') or s.get('sequence_offset_s') is not None else 'gap',
                     'no sequence file; plays at %s%s' % (used, ': ' + s['offset_reason'] if s.get('offset_reason') else ''))
        else:
            c = cell('explained' if s.get('offset_reason') else 'gap',
                     'not in %s; plays at %s%s' % ((m.get('sequence') or {}).get('name'), used,
                                                   ': ' + s['offset_reason'] if s.get('offset_reason') else ' (no offset_reason)'))
        sys_cell[s['id']] = c

    pre_by = {}
    for r in pre['emitters']:
        pre_by.setdefault(r['system'], []).append(r)
    for s in m['systems']:
        recs = pre_by.get(s['id'], [])
        if not recs:
            rows.append({'key': s['id'] + '/*', 'system': s['id'], 'index': None, 'emitter': '*', 'kind': 'system',
                         'cells': dict({k: cell('missing') for k in COLUMNS}, **{'in sequence': sys_cell[s['id']],
                                       'preflight': cell('gap', 'source not read (see preflight problems)')})})
            continue
        rows.append({'key': s['id'] + '/*', 'system': s['id'], 'index': None, 'emitter': '*', 'kind': 'system',
                     'cells': dict({k: cell('na') for k in COLUMNS}, **{
                         'in sequence': sys_cell[s['id']],
                         'preflight': cell('ok', '%s, %d emitter(s)' % (Path(str(s['source'])).stem, len(recs))),
                         'built': cell('ok', '%d build(s): %s' % (len(s['builds']), ', '.join(b['name'] for b in s['builds'])))})})
        rep_f, rep = oracle_report(m, s['id'])
        orc_listed = ((m.get('oracle') or {}).get('systems') or {}).get(s['id'])
        name_count = {}
        sys_names = {r['name'] for r in recs}
        for r in recs:
            if r['enabled']:
                name_count[r['name']] = name_count.get(r['name'], 0) + 1
        for r in recs:
            c = {k: cell('na') for k in COLUMNS}
            c['in sequence'] = cell(sys_cell[s['id']]['state'], '@%g s' % float(s['offset_s']))
            st = r['status']
            c['preflight'] = {'ok': cell('ok', 'inputs ok'), 'off': cell('explained', r['reason']),
                              'excluded': cell('explained', 'excluded: ' + r['reason']),
                              'builder-skip': cell('explained', 'builder skips it: ' + r['reason']),
                              'accepted': cell('explained', 'accepted gap: ' + r['reason']),
                              'MISSING': cell('gap', r['reason'])}[st]
            built_nodes = []
            if st in ('ok', 'accepted', 'builder-skip'):
                built, notes = read_notes(work, r['build'])
                if built is None:
                    c['built'] = cell('missing', 'build %s has no notes on disk' % r['build'])
                else:
                    # check_build.node_of takes name + '_...' as this emitter's twin, which also catches another
                    # emitter's own nodes (StarBurst -> StarBurst_DOF, ParBurst -> ParBurst_Core): leave those out
                    longer = [n for n in sys_names if n != r['name'] and n.startswith(r['name'])]
                    built_nodes = [x for x in CB.node_of(r['name'], built)
                                   if not any(CB.node_of(n, [x]) for n in longer)]
                    skip = CB.skip_reason(r['name'], notes)
                    primary = [n for n in built_nodes if n in (r['name'], 'evt_' + r['name'])]
                    if built_nodes and len(primary) >= name_count.get(r['name'], 1):
                        c['built'] = cell('ok', '%s: %s' % (r['build'], ', '.join(dict.fromkeys(built_nodes))) +
                                          (' (x%d for %d same-named emitters)' % (len(primary), name_count[r['name']])
                                           if name_count.get(r['name'], 1) > 1 else ''))
                    elif built_nodes:
                        c['built'] = cell('gap', '%d node(s) named %s for %d emitter(s) of that name'
                                          % (len(primary), r['name'], name_count[r['name']]))
                    elif skip:
                        c['built'] = cell('explained', ('pending recipe (Claude Code): ' if 'no recipe' in skip else 'builder skip: ') + skip)
                    else:
                        why = [l for l in notes if l.startswith(r['name'] + ': ')
                               and re.search(r'not imported|not found|missing|not converted', l)]
                        c['built'] = cell('gap', 'no node and no skip note' + (': ' + why[0] if why else ''))
            elif st == 'excluded':
                c['built'] = cell('explained', 'excluded: ' + r['reason'])
            if c['built']['state'] == 'ok':
                sub = [n for p, n in nodes if nb == 1 or p.split('/')[0] == str(hosts.get(r['build']))]
                absent = [n for n in dict.fromkeys(built_nodes) if sub.count(n) < built_nodes.count(n)]
                if not final.is_file():
                    c['saved'] = cell('missing', '%s not on disk' % final.name)
                elif absent:
                    c['saved'] = cell('gap', 'not in %s: %s' % (final.name, ', '.join(absent)))
                else:
                    c['saved'] = cell('ok', '%d node(s) in %s%s' % (len(built_nodes), final.name,
                                                                   '' if nb == 1 else ' (host %d)' % hosts[r['build']]))
                # oracle
                if orc_listed:
                    if rep is None:
                        c['oracle'] = cell('missing', 'oracle report %s not on disk' % rep_f)
                    else:
                        keys = [r['name'], '%d_%s' % (r['index'], r['name'])]
                        lay = next((rep['layers'][k] for k in keys if k in rep.get('layers', {})), None)
                        gap = next((rep['not drawn'][k] for k in keys if k in rep.get('not drawn', {})), None)
                        if lay is not None:
                            px = int(lay.get('drawn px', 0))
                            c['oracle'] = cell('ok' if px else 'explained', 'drawn %d px' % px if px else
                                               'oracle drew 0 px in its frames (%s)' % orc_listed.get('frames', '?'))
                        elif gap is not None:
                            c['oracle'] = cell('explained', 'oracle-gap: ' + gap)
                        else:
                            c['oracle'] = cell('missing', 'not in oracle report %s' % rep_f.name)
                # capture
                if has_caps:
                    hits = caps.get('%s/%s' % (s['id'], r['name']), [])
                    if hits:
                        by_label = {}
                        for cap, lab, e, n in hits:
                            by_label.setdefault((lab, n), []).append('%s x%d' % (cap, len(e)))
                        c['capture'] = cell('ok', '; '.join('"%s"%s: %s' % (
                            lab, ' (shared by %d emitters)' % n if n > 1 else '', ', '.join(caps_))
                            for (lab, n), caps_ in by_label.items()))
                    else:
                        c['capture'] = cell('explained', 'no capture draw labelled for it')
                # installed
                if inst is None:
                    c['installed'] = cell('na', 'no install target')
                elif inst_nodes is None:
                    c['installed'] = cell('explained', 'not installed (%s not on disk)' % inst.name)
                else:
                    gone = [n for n in dict.fromkeys(built_nodes) if n not in {x for _, x in inst_nodes}]
                    if gone:
                        c['installed'] = cell('gap', 'not in the installed file: ' + ', '.join(gone))
                    elif inst_missing:
                        c['installed'] = cell('gap', '%d resource(s) of the installed file missing' % len(inst_missing))
                    else:
                        c['installed'] = cell('ok', 'in %s, every resource on disk' % inst.name)
            elif c['built']['state'] == 'explained':
                for k in ('saved', 'oracle', 'capture', 'installed'):
                    c[k] = cell('na', 'not built')
            elif c['built']['state'] in ('gap', 'missing'):
                for k in ('saved', 'oracle', 'capture', 'installed'):
                    c[k] = cell('na', 'not built')
            if not orc_listed:
                c['oracle'] = cell('na', '') if c['oracle']['state'] == 'na' else c['oracle']
            rows.append({'key': '%s/#%d %s' % (s['id'], r['index'], r['name']), 'system': s['id'], 'index': r['index'],
                         'emitter': r['name'], 'kind': r['kind'], 'build': r['build'], 'cells': c})
    if inst is not None and inst.is_file() and final.is_file():
        job_notes.append('installed %s %s the final build' % (inst, 'is byte-identical to' if inst.read_bytes() == final.read_bytes()
                                                               else 'differs from'))
    if inst_missing:
        job_notes += ['installed resource missing: %s %s' % x for x in inst_missing]
    for r in rows:
        states = [v['state'] for v in r['cells'].values()]
        r['status'] = 'INCOMPLETE' if 'missing' in states else 'GAP' if 'gap' in states else 'ok'
    return rows, job_notes


def summary(m, rows):
    n_miss = sum(r['status'] == 'INCOMPLETE' for r in rows)
    n_gap = sum(r['status'] == 'GAP' for r in rows)
    na_cols = []
    if not (m.get('oracle') or {}).get('systems'):
        na_cols.append('oracle (none in the manifest)')
    if not m.get('renderdoc'):
        na_cols.append('capture (none in the manifest)')
    if not (m.get('install') or {}).get('dest'):
        na_cols.append('installed (no install target)')
    em_rows = [r for r in rows if r['emitter'] != '*']
    built = sum(r['cells']['built']['state'] == 'ok' for r in em_rows)
    saved = sum(r['cells']['saved']['state'] == 'ok' for r in em_rows)
    return {'rows': len(rows), 'emitter_rows': len(em_rows), 'systems': len({r['system'] for r in rows}),
            'built': built, 'saved': saved, 'gap': n_gap, 'incomplete': n_miss, 'na_columns': na_cols,
            'coverage': 'COMPLETE' if not n_miss and not n_gap else 'INCOMPLETE'}


def markdown(m, variants_rows, results=None):
    out = ['# Emitter ledger: %s' % m['effect'], '',
           '%s, %s. Generated by tools/effectjob/effectjob.py from %s; do not edit by hand.' % (
               m['game'], m['skill'].rstrip('.'), Path(m['_file']).name), '']
    if results:
        out += ['## Result lines', ''] + ['- ' + l for l in results] + ['']
    for v, (rows, notes) in variants_rows.items():
        sm = summary(m, rows)
        out += ['## Variant %s' % v, '',
                '%d rows (%d systems, %d emitter rows): %d built, %d saved; %d with a gap, %d without a status: '
                'coverage %s.' % (sm['rows'], sm['systems'], sm['emitter_rows'], sm['built'], sm['saved'], sm['gap'],
                                  sm['incomplete'], sm['coverage'])]
        if sm['na_columns']:
            out.append('Not applicable for this manifest: %s.' % '; '.join(sm['na_columns']))
        out += ['- ' + n for n in notes]
        out += ['', '| status | system/emitter | kind | ' + ' | '.join(COLUMNS) + ' |',
                '|---|---|---|' + '---|' * len(COLUMNS)]
        mark = {'ok': '', 'explained': '', 'na': '', 'gap': '**GAP** ', 'missing': '**NO STATUS** '}
        for r in rows:
            cells = []
            for k in COLUMNS:
                cc = r['cells'][k]
                cells.append((mark[cc['state']] + cc['text']).replace('|', '/'))
            out.append('| %s | %s | %s | %s |' % (r['status'], r['key'], r['kind'], ' | '.join(cells)))
        out.append('')
    return '\n'.join(out) + '\n'
