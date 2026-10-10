#!/usr/bin/env python3
"""
Pick one representative node per distinct part in the catalogue.

A "part" is a drawing node identified by what it renders, how it moves, what it
inherits, which resource files it loads, and the shape of its ancestor chain --
everything except its name and which effect it happens to sit in. The corpus
repeats itself heavily (28,647 drawing nodes collapse to 11,708 parts), and a
contact sheet of duplicates is a contact sheet nobody can read.

Output is a selection file the batch assembler consumes, plus the provenance a
contact sheet needs to say where each thumbnail came from.
"""
import argparse, collections, hashlib, json, os, sys


def fingerprint(node):
    key = {f: node.get(f) for f in
           ('renderer', 'common', 'motion', 'render', 'inherit', 'sound')}
    key['res'] = sorted(r.get('relative') or '' for r in node.get('resources', []))
    key['chain'] = [(a['renderer'], a['contributesTransform'])
                    for a in node.get('ancestors', [])]
    blob = json.dumps(key, sort_keys=True, default=str).encode('utf-8')
    return hashlib.md5(blob).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('catalogue')
    ap.add_argument('out')
    ap.add_argument('--renderers', default='Ring,Model,Track,Ribbon',
                    help='comma-separated; "all" for every drawing renderer')
    ap.add_argument('--limit', type=int, default=0, help='0 = no limit')
    args = ap.parse_args()

    doc = json.load(open(args.catalogue, encoding='utf-8'))
    wanted = None if args.renderers == 'all' else set(args.renderers.split(','))

    groups = collections.OrderedDict()
    skipped_missing = 0

    for effect in doc['effects']:
        if 'error' in effect:
            continue
        # An effect whose resources are not all on disk cannot be a part source:
        # the graft would copy a file that is not there.
        if any(not r['exists'] for r in effect.get('resources', [])):
            skipped_missing += 1
            continue
        for node in effect['nodes']:
            if not node['drawsSomething']:
                continue
            if wanted is not None and node['renderer'] not in wanted:
                continue
            groups.setdefault(fingerprint(node), []).append((effect, node))

    parts = []
    for fp, members in groups.items():
        # Prefer the shallowest chain, then the fewest siblings to rebuild, then
        # a stable alphabetical tie-break so reruns pick the same representative.
        effect, node = min(members, key=lambda m: (
            len(m[1]['ancestors']), m[1]['depth'], m[0]['id'], m[1]['path']))

        # How long to run the part for. A thumbnail is taken at the brightest
        # sampled frame, so the run has to outlast the part: one sample run
        # capped at 120 frames put several best frames at 117, which means the
        # real peak was off the end. Ancestors add delay this cannot see, hence
        # the generous ceiling.
        common = node.get('common') or {}
        life = (common.get('life') or {}).get('max') or 60
        offset = (common.get('generationTimeOffset') or {}).get('max') or 0
        frames = int(min(360, max(90, round(life + offset + 30))))
        parts.append({
            'id': '{0}-{1}'.format(node['renderer'].lower(), fp[:10]),
            'fingerprint': fp,
            'renderer': node['renderer'],
            'source': effect['id'],
            'node': node['path'],
            'name': node['name'],
            'namePath': node['namePath'],
            'corpus': effect['corpus'],
            'depth': node['depth'],
            'chain': len(node['ancestors']),
            'dependsOnAncestors': node['dependsOnAncestors'],
            'material': (node.get('render') or {}).get('material'),
            'resources': [r.get('relative') for r in node.get('resources', [])],
            'frames': frames,
            'copies': len(members),
            'corpora': sorted({e['corpus'] for e, _ in members}),
        })

    parts.sort(key=lambda p: (p['renderer'], -p['copies'], p['id']))
    if args.limit:
        parts = parts[:args.limit]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump({'catalogue': args.catalogue, 'parts': parts},
              open(args.out, 'w', encoding='utf-8'), indent=1)

    by = collections.Counter(p['renderer'] for p in parts)
    print('effects skipped for missing resources', skipped_missing)
    print('distinct parts selected', len(parts))
    for r, n in by.most_common():
        print('  {0:8} {1}'.format(r, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
