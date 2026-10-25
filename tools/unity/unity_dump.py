"""Dump Unity prefabs straight from a game's asset bundles (UnityPy) into the JSON `shuriken_summary.py` reads.

python tools/unity/unity_dump.py --bundles <Bundles folder> --out <new folder>          (index only)
python tools/unity/unity_dump.py <bundle | "FX*">... --bundles <Bundles folder> --out <new folder> --list
       [--container REGEX]                                (one bundle loaded at a time; a pattern ignores case)
python tools/unity/unity_dump.py <bundle>... --out <new folder> [--bundles <Bundles folder> | --cab-index <csv>]
       (--container REGEX | --name REGEX | --path-id CAB:ID)... [--export-textures] [--export-meshes]

Needs UnityPy (`pip install UnityPy`). Load only the bundles you need, never a whole Bundles folder: UnityPy
decompresses every bundle it loads, and Unity game A is ~40 GB of interdependent bundles. A bundle's references
name the files inside other bundles (`CAB-<hash>`), not bundle file names, so `--bundles` first reads the directory
of every bundle in the folder (headers only, no data decompressed) into `cab_index.csv`; a dump then loads, round by
round, the bundles its unresolved references point into (`--cab-index` reuses that csv). A bundle name given as an
input is also looked up in `--bundles`. Every reference still unresolved is kept as
`{"$ref": null, "unresolved": "<external file>:<path id>"}`; the dump's `unresolved_files` names those files and
`unresolved_not_in_index` the ones no bundle holds (Unity's built-in resources live in `<game>_Data`, not in a
bundle).

What is written per root (`NNN-<name>.unitydump.json`): every GameObject of the root's hierarchy and all their
components in full (typetree fields, Unity's serialized names), every Material and MonoScript they reference in
full, and every other referenced object trimmed to its name and a few header fields (Texture2D size and format,
Mesh vertex count, Shader name), never pixel or vertex data. `--export-textures` / `--export-meshes` also save
those as PNG / OBJ under `textures/` and `meshes/` and record the file in the trimmed object. An OBJ is UnityPy's
export, which negates X (positions and normals) to make Unity's left-handed mesh right-handed: negate x and nx back
for Unity coordinates (the file's first line and the record's `obj_mirror` say so). A Unity study effect's emitter mesh,
read as is, launched every blade against its orbit.
Exit 0 every root dumped, 1 something failed (printed, and listed in `dump-report.json`), 2 usage error or no match.
"""
import argparse
import csv
import fnmatch
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
FORMAT = 'n2e-unity-dump/1'
FULL_ASSETS = ('Material', 'MonoScript')
COMPONENT_HOLDERS = ('GameObject',)
TRIM_FIELDS = {'Texture2D': ('m_Width', 'm_Height', 'm_TextureFormat', 'm_MipCount', 'm_ColorSpace'),
               'Sprite': ('m_Rect', 'm_Pivot', 'm_PixelsToUnits')}


OBJ_HEADER = ('# unity_dump: UnityPy OBJ export, X negated (positions and normals; Unity meshes are left-handed): '
              'negate x and nx back for Unity coordinates\n')


def is_pptr(v):
    return isinstance(v, dict) and set(v) == {'m_FileID', 'm_PathID'}


def safe_name(s):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', s or 'unnamed')[:80]


class Dumper:
    """Collects one root's objects. `resolve(obj, file_id, path_id)` returns the referenced object reader or raises
    (FileNotFoundError/KeyError/ValueError) with the external file it needed; readers expose `assets_file.name`,
    `path_id`, `type.name`, `class_id` and `parse_as_dict()` as UnityPy's ObjectReader does."""

    def __init__(self, resolve, export_dir=None, textures=False, meshes=False):
        self.resolve, self.export_dir, self.textures, self.meshes = resolve, export_dir, textures, meshes
        self.objects, self.order, self.unresolved, self.errors, self.unreadable = {}, [], {}, [], set()

    @staticmethod
    def oid(obj):
        return '%s:%d' % (obj.assets_file.name, obj.path_id)

    def target(self, obj, ref):
        if not is_pptr(ref) or int(ref['m_PathID']) == 0:
            return None
        try:
            return self.resolve(obj, int(ref['m_FileID']), int(ref['m_PathID']))
        except (FileNotFoundError, KeyError, ValueError, IndexError):
            return None

    def _parse(self, obj):
        try:
            return obj.parse_as_dict()
        except Exception as ex:          # a typetree that does not read is reported, never fatal for the dump
            first = '%s: %s' % (type(ex).__name__, ex)
        try:
            # a MonoBehaviour whose script fields have no typetree still yields its head (m_Script, m_Enabled)
            d = obj.parse_as_dict(check_read=False)
            self.errors.append('%s (%s): read partially (%s)' % (self.oid(obj), obj.type.name, first))
            return d
        except Exception:
            self.errors.append('%s (%s): %s' % (self.oid(obj), obj.type.name, first))
            self.unreadable.add(self.oid(obj))
            return None

    def _add(self, obj, data, trimmed=False, extra=None):
        entry = {'id': self.oid(obj), 'class': obj.type.name, 'class_id': obj.class_id, 'data': data}
        if trimmed:
            entry['trimmed'] = True
        if extra:
            entry.update(extra)
        self.objects[entry['id']] = (obj, entry)
        self.order.append(entry['id'])
        return entry

    def dump(self, root):
        """The root's hierarchy in full, then every reference followed once (see module doc)."""
        queue, seen_go = [root], set()
        while queue:
            go = queue.pop(0)
            if self.oid(go) in seen_go:
                continue
            seen_go.add(self.oid(go))
            d = self._parse(go)
            if d is None:
                continue
            self._add(go, d)
            for c in d.get('m_Component') or []:
                ref = c.get('component') if isinstance(c, dict) else (c[1] if isinstance(c, (list, tuple)) else c)
                comp = self.target(go, ref)
                if comp is None or self.oid(comp) in self.objects:
                    continue
                cd = self._parse(comp)
                if cd is None:
                    continue
                self._add(comp, cd)
                for child in cd.get('m_Children') or []:
                    ct = self.target(comp, child)
                    cgo = self.target(ct, (self._parse(ct) or {}).get('m_GameObject')) if ct is not None else None
                    if cgo is not None:
                        queue.append(cgo)
        pending = list(self.order)
        while pending:
            obj, entry = self.objects[pending.pop(0)]
            if entry.get('trimmed'):
                continue
            entry['data'] = self._rewrite(obj, entry['data'], pending)
        return {'format': FORMAT, 'unity_version': getattr(root.assets_file, 'unity_version', None),
                'root': self.oid(root), 'unresolved_files': sorted(self.unresolved),
                'errors': self.errors, 'objects': [self.objects[i][1] for i in self.order]}

    def _rewrite(self, obj, v, pending):
        if is_pptr(v):
            if int(v['m_PathID']) == 0:
                return {'$ref': None}
            t = self.target(obj, v)
            if t is None:
                ext = self._external_name(obj, int(v['m_FileID']))
                self.unresolved[ext] = self.unresolved.get(ext, 0) + 1
                return {'$ref': None, 'unresolved': '%s:%s' % (ext, v['m_PathID'])}
            tid = self.oid(t)
            if tid in self.unreadable:
                return {'$ref': None, 'unresolved': '%s (typetree did not read)' % tid}
            if tid not in self.objects:
                if t.type.name in FULL_ASSETS:
                    d = self._parse(t)
                    if d is not None:
                        self._add(t, d)
                        pending.append(tid)
                else:
                    self._add_trimmed(t)
            return {'$ref': tid if tid in self.objects else None}
        if isinstance(v, dict):
            return {k: self._rewrite(obj, x, pending) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [self._rewrite(obj, x, pending) for x in v]
        if isinstance(v, (bytes, bytearray, memoryview)):
            return {'$bytes': len(v)}
        if isinstance(v, float) and v != v:
            return 'NaN'
        return v

    def _external_name(self, obj, file_id):
        if file_id == 0:
            return obj.assets_file.name
        ext = getattr(obj.assets_file, 'externals', [])
        return ext[file_id - 1].path if 0 < file_id <= len(ext) else 'file id %d' % file_id

    def _add_trimmed(self, t):
        cls = t.type.name
        d = self._parse(t) or {}
        data = {'m_Name': d.get('m_Name')}
        for k in TRIM_FIELDS.get(cls, ()):
            if k in d:
                data[k] = d[k]
        if cls == 'Shader':
            pf = d.get('m_ParsedForm')
            data['m_ParsedForm'] = {'m_Name': pf.get('m_Name')} if isinstance(pf, dict) else None
        if cls == 'Mesh':
            vd = d.get('m_VertexData') or {}
            data['m_VertexCount'] = vd.get('m_VertexCount')
            data['m_SubMeshCount'] = len(d.get('m_SubMeshes') or [])
        extra = {}
        if self.export_dir is not None and ((cls == 'Texture2D' and self.textures) or (cls == 'Mesh' and self.meshes)):
            extra['exported'] = self._export(t, cls, data.get('m_Name'))
            if cls == 'Mesh' and extra['exported']:
                extra['obj_mirror'] = 'x'
        self._add(t, data, trimmed=True, extra=extra)

    def _export(self, t, cls, name):
        sub = 'textures' if cls == 'Texture2D' else 'meshes'
        rel = '%s/%s-%d.%s' % (sub, safe_name(name), t.path_id, 'png' if cls == 'Texture2D' else 'obj')
        path = self.export_dir / rel
        if path.exists():
            return rel
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            o = t.parse_as_object()
            if cls == 'Texture2D':
                o.image.save(path)
            else:
                path.write_text(OBJ_HEADER + o.export(), encoding='utf-8')
        except Exception as ex:
            self.errors.append('export %s (%s): %s: %s' % (self.oid(t), cls, type(ex).__name__, ex))
            return None
        return rel


def _version_tuple(revision):
    return tuple(int(x) for x in re.findall(r'\d+', revision)[:3])


def bundle_cabs(path):
    """(Unity revision, [file names inside]) of a UnityFS bundle, read from its header and directory only: no data
    block is decompressed, so a whole Bundles folder is indexed in seconds. None for a file that is not UnityFS.
    Layout as UnityPy's BundleFile.read_fs: big-endian header, 16-byte alignment from header version 7 or Unity
    2019.4.15, the block/directory info at the end of the file when flag 0x80 is set, compressed by flags & 0x3F
    (0 none, 1 LZMA, 2/3 LZ4)."""
    import struct

    def cstr(f):
        out = bytearray()
        while (c := f.read(1)) not in (b'\0', b''):
            out += c
        return out.decode('utf-8', 'replace')
    with open(path, 'rb') as f:
        if f.read(8) != b'UnityFS\0':
            return None
        version, = struct.unpack('>I', f.read(4))
        cstr(f)                                           # player version ('5.x.x')
        revision = cstr(f)
        _size, csize, usize, flags = struct.unpack('>qIII', f.read(20))
        pos = f.tell()
        if version >= 7 or _version_tuple(revision) >= (2019, 4, 15):
            pos = (pos + 15) // 16 * 16
        if flags & 0x80:
            f.seek(0, 2)
            pos = f.tell() - csize
        f.seek(pos)
        data = f.read(csize)
    comp = flags & 0x3F
    if comp == 1:                                         # raw LZMA1: 5 property bytes, then the stream
        import lzma
        props, dict_size = struct.unpack('<BI', data[:5])
        info = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{
            'id': lzma.FILTER_LZMA1, 'dict_size': dict_size, 'lc': props % 9, 'lp': props // 9 % 5,
            'pb': props // 45}]).decompress(data[5:], usize)
    elif comp in (2, 3):
        import lz4.block
        info = lz4.block.decompress(data, usize)
    elif comp == 0:
        info = data
    else:
        raise ValueError('block info compression %d not supported' % comp)
    blocks, = struct.unpack_from('>i', info, 16)
    at = 20 + blocks * 10
    nodes, = struct.unpack_from('>i', info, at)
    at += 4
    names = []
    for _ in range(nodes):
        at += 20                                          # offset, size, flags
        end = info.index(b'\0', at)
        names.append(info[at:end].decode('utf-8', 'replace'))
        at = end + 1
    return revision, names


def cab_index(folder, progress=None):
    """{lower-case file name inside a bundle: bundle path} for every UnityFS file under folder, plus the rows
    (name, bundle, revision) and the files that could not be read. `progress(done, total)` is called every 250
    files and at the end, so a long scan shows it is moving."""
    index, rows, bad = {}, [], []
    files = sorted((p for p in Path(folder).rglob('*') if p.is_file()), key=str)
    for i, p in enumerate(files, 1):
        if progress is not None and (i % 250 == 0 or i == len(files)):
            progress(i, len(files))
        try:
            got = bundle_cabs(p)
        except Exception as ex:          # unreadable header: listed, the rest of the folder still indexes
            bad.append('%s: %s: %s' % (p, type(ex).__name__, ex))
            continue
        if got is None:
            continue
        revision, names = got
        for n in names:
            index.setdefault(n.lower(), p)
            rows.append((n, str(p), revision))
    return index, rows, bad


def read_cab_index(path):
    with open(path, newline='', encoding='utf-8') as f:
        return {r['cab'].lower(): Path(r['bundle']) for r in csv.DictReader(f)}


def cab_of(external):
    """'archive:/CAB-x/CAB-x' -> 'cab-x', the key a bundle's directory lists it under."""
    return external.replace('\\', '/').rsplit('/', 1)[-1].lower()


def unitypy_resolver():
    from UnityPy.classes import PPtr

    def resolve(obj, file_id, path_id):
        return PPtr(m_FileID=file_id, m_PathID=path_id, assetsfile=obj.assets_file).deref()
    return resolve


def find_roots(env, containers, names, path_ids):
    """(label, GameObject reader) for each requested root, in request order, without duplicates."""
    out, seen = [], set()

    def add(label, obj):
        key = (obj.assets_file.name, obj.path_id)
        if key not in seen:
            seen.add(key)
            out.append((label, obj))
    for rx in containers:
        pat = re.compile(rx, re.I)
        for path, pptr in env.container.items():
            if pat.search(path):
                try:
                    obj = pptr.deref()
                except (FileNotFoundError, KeyError, ValueError):
                    continue
                if obj.type.name == 'GameObject':
                    add(path, obj)
    for rx in names:
        pat = re.compile(rx, re.I)
        for obj in env.objects:
            if obj.type.name == 'GameObject' and pat.search(obj.peek_name() or ''):
                d = obj.parse_as_dict()
                tr = None
                for c in d.get('m_Component') or []:
                    ref = c.get('component') if isinstance(c, dict) else c[1]
                    try:
                        t = obj.assets_file.objects[ref['m_PathID']] if ref['m_FileID'] == 0 else None
                    except KeyError:
                        t = None
                    if t is not None and t.type.name in ('Transform', 'RectTransform'):
                        tr = t.parse_as_dict()
                if tr is not None and int(tr.get('m_Father', {}).get('m_PathID', 0)) == 0:
                    add(obj.peek_name(), obj)
    for spec in path_ids:
        cab, _, pid = spec.rpartition(':')
        for f in env.cabs.values():
            if getattr(f, 'name', None) == cab and int(pid) in getattr(f, 'objects', {}):
                add(spec, f.objects[int(pid)])
    return out


def expand_inputs(specs, bundles=None):
    """Paths for the inputs: a path as given, else the same name inside `bundles`; a spec holding * ? or [ is a
    pattern matched against the file names of its folder (`bundles` when it names none), ignoring case, so
    `"FX*"` finds FX, fx_fire... on any system. Returns (paths without duplicates, specs that matched nothing)."""
    out, unmatched = [], []
    for s in specs:
        p = Path(s)
        if any(c in p.name for c in '*?['):
            folder = p.parent if str(p.parent) not in ('', '.') else (bundles or Path('.'))
            hits = sorted((q for q in folder.iterdir()
                           if q.is_file() and fnmatch.fnmatch(q.name.lower(), p.name.lower())),
                          key=str) if folder.is_dir() else []
        else:
            if not p.exists() and bundles is not None and (bundles / p).exists():
                p = bundles / p
            hits = [p] if p.exists() else []
        if hits:
            out.extend(hits)
        else:
            unmatched.append(s)
    return list(dict.fromkeys(out)), unmatched


def list_rows(env, pats, bundle):
    """containers.csv rows (bundle, container path, class, serialized file, path id) of one environment."""
    rows = []
    for path, pptr in sorted(env.container.items(), key=lambda kv: kv[0]):
        if pats and not any(p.search(path) for p in pats):
            continue
        try:
            obj = pptr.deref()
            rows.append([bundle, path, obj.type.name, obj.assets_file.name, obj.path_id])
        except (FileNotFoundError, KeyError, ValueError) as ex:
            rows.append([bundle, path, 'unresolved: %s' % ex, '', pptr.m_PathID])
    return rows


def dump_with_dependencies(env, roots, index, loaded, make_dumper, max_rounds):
    """Dump every root; while a dump points into a CAB that the index places in a bundle not loaded yet, load
    those bundles and dump again (the roots stay the ones found first). Returns ([(label, root, dump)], added)."""
    added, dumps = [], []
    for _ in range(max(1, max_rounds)):
        dumps = [(label, root, make_dumper().dump(root)) for label, root in roots]
        missing = {cab_of(u) for _, _, d in dumps for u in d['unresolved_files']}
        new = sorted({index[m] for m in missing if m in index} - loaded, key=str)
        if not new:
            break
        for p in new:
            env.load_file(str(p))
            loaded.add(p)
            added.append(p)
        print('loading %d dependency bundle(s): %s' % (len(new), ', '.join(p.name for p in new)))
    return dumps, added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('inputs', nargs='*', help='bundle files or folders (names are also looked up in --bundles)')
    ap.add_argument('--out', required=True, type=Path, help='new or empty output folder')
    ap.add_argument('--bundles', type=Path, help="the game's Bundles folder: indexed (cab_index.csv) so a dump "
                                                 'loads the bundles its references point into by itself')
    ap.add_argument('--cab-index', type=Path, help='reuse a cab_index.csv instead of indexing --bundles again')
    ap.add_argument('--max-rounds', type=int, default=8, help='dependency loading rounds (default 8)')
    ap.add_argument('--list', action='store_true', help='write containers.csv (filtered by --container) and stop')
    ap.add_argument('--container', action='append', default=[], help='regex on container paths (repeatable)')
    ap.add_argument('--name', action='append', default=[], help='regex on root GameObject names (repeatable)')
    ap.add_argument('--path-id', action='append', default=[], help='CAB-name:pathID of a root GameObject')
    ap.add_argument('--export-textures', action='store_true')
    ap.add_argument('--export-meshes', action='store_true')
    a = ap.parse_args(argv)
    wants_dump = bool(a.container or a.name or a.path_id)
    if not (a.list or wants_dump or a.bundles):
        ap.error('give --list, at least one of --container / --name / --path-id, or --bundles alone to index it')
    if (a.list or wants_dump) and not a.inputs:
        ap.error('give the bundle(s) to read')
    if a.out.exists() and (not a.out.is_dir() or any(a.out.iterdir())):
        print('--out %s exists and is not an empty folder' % a.out, file=sys.stderr)
        return 2
    try:
        import UnityPy
    except ImportError:
        print('unity_dump needs UnityPy: pip install UnityPy', file=sys.stderr)
        return 2
    inputs, unmatched = expand_inputs(a.inputs, a.bundles)
    if unmatched:
        print('input not found: %s' % ', '.join(unmatched), file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    index = {}
    if a.cab_index is not None:
        index = read_cab_index(a.cab_index)
    elif a.bundles is not None:
        print('indexing %s (headers only) ...' % a.bundles, flush=True)
        index, rows, bad = cab_index(a.bundles, lambda i, n: print('  %d / %d files' % (i, n), flush=True))
        with (a.out / 'cab_index.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['cab', 'bundle', 'unity_revision'])
            w.writerows(rows)
        revisions = sorted({r[2] for r in rows})
        print('indexed %d file(s) inside %d bundle(s) under %s (Unity %s)%s -> %s' % (
            len(rows), len({r[1] for r in rows}), a.bundles, ', '.join(revisions) or '?',
            ', %d unreadable' % len(bad) if bad else '', a.out / 'cab_index.csv'))
        for b in bad[:20]:
            print('  unreadable: %s' % b)
        if not (a.list or wants_dump):
            return 1 if bad else 0
    if a.list:
        pats = [re.compile(r, re.I) for r in a.container]
        files = [q for p in inputs for q in (sorted((x for x in p.rglob('*') if x.is_file()), key=str) if p.is_dir() else [p])]
        n = failed = 0
        with (a.out / 'containers.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['bundle', 'container', 'class', 'file', 'path_id'])
            for p in files:
                # one bundle at a time, each in its own environment: UnityPy decompresses all of a bundle it loads
                try:
                    if bundle_cabs(p) is None:
                        print('%s: not a UnityFS bundle, skipped' % p.name)
                        continue
                    rows = list_rows(UnityPy.Environment(str(p)), pats, p.name)
                except Exception as ex:      # one unreadable bundle is reported, the others still list
                    print('%s: FAILED %s: %s' % (p.name, type(ex).__name__, ex))
                    failed += 1
                    continue
                w.writerows(rows)
                n += len(rows)
                print('%s: %d entr%s, %d GameObject' % (p.name, len(rows), 'y' if len(rows) == 1 else 'ies',
                                                       sum(r[2] == 'GameObject' for r in rows)))
        print('%d container entr%s -> %s' % (n, 'y' if n == 1 else 'ies', a.out / 'containers.csv'))
        return 1 if failed else 0 if n else 2
    env = UnityPy.Environment(*[str(p) for p in inputs])
    roots = find_roots(env, a.container, a.name, a.path_id)
    if not roots:
        print('no root GameObject matched', file=sys.stderr)
        return 2
    loaded = {p.resolve() for p in inputs if p.is_file()} | {
        q.resolve() for p in inputs if p.is_dir() for q in p.rglob('*') if q.is_file()}
    index = {k: v.resolve() for k, v in index.items()}
    dumps, added = dump_with_dependencies(
        env, roots, index, loaded,
        lambda: Dumper(unitypy_resolver(), a.out, a.export_textures, a.export_meshes), a.max_rounds)
    report, failed = [], False
    for i, (label, root, dump) in enumerate(dumps):
        dump['source'] = label
        dump['dependency_bundles_loaded'] = [str(p) for p in added]
        dump['unresolved_not_in_index'] = sorted({u for u in dump['unresolved_files'] if cab_of(u) not in index}) \
            if index else None
        name = '%03d-%s.unitydump.json' % (i, safe_name(root.peek_name() or label.rsplit('/', 1)[-1]))
        (a.out / name).write_text(json.dumps(dump, indent=1), encoding='utf-8')
        failed |= bool(dump['errors'])
        report.append({'root': label, 'file': name, 'objects': len(dump['objects']),
                       'unresolved_files': dump['unresolved_files'],
                       'unresolved_not_in_index': dump['unresolved_not_in_index'],
                       'dependency_bundles_loaded': dump['dependency_bundles_loaded'], 'errors': dump['errors']})
        print('%s: %d objects%s%s -> %s' % (label, len(dump['objects']),
                                           ', unresolved refs into %s' % ', '.join(dump['unresolved_files'])
                                           if dump['unresolved_files'] else '',
                                           ', %d error(s)' % len(dump['errors']) if dump['errors'] else '', name))
    (a.out / 'dump-report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
