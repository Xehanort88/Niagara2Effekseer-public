"""Read-only digest API and incremental, manifest-derived SQLite catalogue.

Only build_index writes, and only to its database. No RenderDoc dependency.
"""
import builtins
import copy
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import numpy as np
import ddsio

DEFAULT_DB = str(Path(__file__).resolve().parents[2] / 'out/rdcdigest-index/index.sqlite')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS stores(path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS captures(
 pk INTEGER PRIMARY KEY, id TEXT, store TEXT REFERENCES stores(path) ON DELETE CASCADE,
 game TEXT, rdc_path TEXT, rdc_bytes INTEGER, version INTEGER, options TEXT,
 draws INTEGER, dispatches INTEGER, kept INTEGER, manifest_mtime INTEGER, manifest_size INTEGER,
 UNIQUE(store,id));
CREATE TABLE IF NOT EXISTS draws(
 capture INTEGER REFERENCES captures(pk) ON DELETE CASCADE, eid INTEGER, kept INTEGER, keep_reasons TEXT,
 vs_checksum TEXT, vs_md5 TEXT, ps_checksum TEXT, ps_md5 TEXT,
 blend TEXT, depth TEXT, raster TEXT, blend_summary TEXT, targets TEXT,
 vertices INTEGER, indices INTEGER, instances INTEGER, gpu_us REAL, has_contrib INTEGER, has_postvs INTEGER,
 marker TEXT, blend_kind TEXT, row_json TEXT, PRIMARY KEY(capture,eid));
CREATE TABLE IF NOT EXISTS srvs(capture INTEGER REFERENCES captures(pk) ON DELETE CASCADE,
 eid INTEGER, stage TEXT, slot INTEGER, view_format TEXT, size TEXT, hash TEXT, dynamic INTEGER,
 rewritten INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS cbuffers(capture INTEGER REFERENCES captures(pk) ON DELETE CASCADE,
 eid INTEGER, stage TEXT, slot INTEGER, data_key TEXT, bytes INTEGER);
CREATE TABLE IF NOT EXISTS blobs(kind TEXT, hash TEXT, store TEXT REFERENCES stores(path) ON DELETE CASCADE,
 relative_path TEXT, bytes INTEGER, PRIMARY KEY(store,relative_path));
CREATE TABLE IF NOT EXISTS uses(capture INTEGER REFERENCES captures(pk) ON DELETE CASCADE,
 eid INTEGER, stage TEXT, slot INTEGER, kind TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS uses_hash ON uses(hash COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS uses_draw ON uses(capture,eid,kind);
CREATE INDEX IF NOT EXISTS srvs_hash ON srvs(hash COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS srvs_capture ON srvs(capture);
CREATE INDEX IF NOT EXISTS cbuffers_capture ON cbuffers(capture);
CREATE INDEX IF NOT EXISTS captures_store ON captures(store);
CREATE INDEX IF NOT EXISTS draws_kept ON draws(kept);
CREATE INDEX IF NOT EXISTS capture_id ON captures(id);
CREATE INDEX IF NOT EXISTS blobs_hash ON blobs(hash COLLATE NOCASE);
'''


def discover(roots):
    found = set()
    for root in roots:
        p = Path(root).resolve()
        if not p.is_dir():
            raise ValueError('root is not a directory: %s' % p)
        if (p / 'captures').is_dir() and (p.name == 'rdcdigest' or (p / 'blobs').is_dir()):
            found.add(str(p))
            continue
        for directory, dirs, _ in os.walk(p):
            if Path(directory).name == 'rdcdigest' and (Path(directory) / 'captures').is_dir():
                found.add(str(Path(directory).resolve()))
                dirs[:] = []
    return sorted(found)


def game_of(store):
    parts = Path(store).parts
    for i, part in enumerate(parts[:-1]):
        if part.lower() == 'exports':
            return parts[i + 1]
    return ''


def references(r):
    for stage, sh in r.get('shaders', {}).items():
        for h in dict.fromkeys([sh.get('checksum'), sh.get('md5')]):
            if h:
                yield stage, None, 'shader', h
        for s in sh.get('srvs', []):
            if s.get('hash'):
                yield stage, s.get('register'), s.get('kind', 'buffer'), s['hash']
    for v in r.get('vb', []):
        if v.get('hash'):
            yield 'ia', v.get('slot'), 'mesh', v['hash']
    if (r.get('ib') or {}).get('hash'):
        yield 'ia', -1, 'mesh', r['ib']['hash']
    for stage, pv in (r.get('postvs') or {}).items():
        for key in ('vertex', 'index'):
            if pv.get(key):
                yield stage, None, 'postvs', pv[key]


def build_index(roots, db=DEFAULT_DB, rebuild=False):
    start = time.perf_counter()
    stores = discover(roots)
    target = Path(db).resolve()
    if any(x.lower() == 'exports' for x in target.parts) or any(
            target.is_relative_to(Path(s)) for s in stores):
        raise ValueError('index database must be outside the read-only stores and Exports')
    target.parent.mkdir(parents=True, exist_ok=True)
    counts = dict(added=0, updated=0, removed=0, skipped=0)
    con = sqlite3.connect(target)
    con.execute('PRAGMA foreign_keys=ON')
    try:
        con.executescript(SCHEMA)
        with con:
            if rebuild:
                con.execute('DELETE FROM stores')
            # Retain previously indexed stores when adding another root; still detect their deleted manifests.
            stores = sorted(set(stores) | {r[0] for r in con.execute('SELECT path FROM stores')})
            for store in stores:
                con.execute('INSERT OR IGNORE INTO stores VALUES(?)', (store,))
                old = {r[1]: r for r in con.execute(
                    'SELECT pk,id,manifest_mtime,manifest_size FROM captures WHERE store=?', (store,))}
                seen = set()
                for manifest in sorted((Path(store) / 'captures').glob('*/manifest.json')):
                    cid = manifest.parent.name
                    seen.add(cid)
                    st = manifest.stat()
                    prev = old.get(cid)
                    if prev and (prev[2], prev[3]) == (st.st_mtime_ns, st.st_size):
                        counts['skipped'] += 1
                        continue
                    with manifest.open(encoding='utf-8') as f:
                        m = json.load(f)
                    # Avoid indexing a manifest replaced concurrently by a running digest.
                    after = manifest.stat()
                    if (st.st_mtime_ns, st.st_size) != (after.st_mtime_ns, after.st_size):
                        raise RuntimeError('manifest changed while indexing; rerun: %s' % manifest)
                    if prev:
                        con.execute('DELETE FROM captures WHERE pk=?', (prev[0],))
                    c = m.get('counts', {})
                    pk = con.execute('INSERT INTO captures VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)', (
                        cid, store, game_of(store), m.get('capture'), m.get('capture_bytes'),
                        m.get('version', m.get('digest_version', 1)), json.dumps(m.get('options', {})),
                        c.get('draws', 0), c.get('dispatches', 0), c.get('kept', 0),
                        st.st_mtime_ns, st.st_size)).lastrowid
                    for r in m.get('draws', []):
                        sh = r.get('shaders', {})
                        vs, ps = sh.get('vs', {}), sh.get('ps', {})
                        bs = r.get('blend_summary') or [{}]
                        vals = (pk, r['eid'], bool(r.get('keep')), json.dumps(r.get('keep', [])),
                                vs.get('checksum'), vs.get('md5'), ps.get('checksum'), ps.get('md5'),
                                r.get('blend'), r.get('depth'), r.get('raster'), json.dumps(bs),
                                json.dumps(r.get('targets', [])), r.get('vertices',
                                    max((v.get('count', 0) for v in r.get('vb', []) if not v.get('per_instance')),
                                        default=None) if r.get('indexed') else r.get('indices')),
                                r.get('indices'), r.get('instances'), r.get('gpu_us'),
                                bool(r.get('contrib')), bool(r.get('postvs')), r.get('path', ''),
                                bs[0].get('kind'), json.dumps(r, separators=(',', ':')))
                        con.execute('INSERT INTO draws VALUES(%s)' % ','.join('?' * len(vals)), vals)
                        con.executemany('INSERT INTO uses VALUES(?,?,?,?,?,?)',
                                        ((pk, r['eid'], *v) for v in references(r)))
                        for stage, shader in sh.items():
                            con.executemany('INSERT INTO srvs VALUES(?,?,?,?,?,?,?,?,?,?)', (
                                (pk, r['eid'], stage, s.get('register'), s.get('view_format'),
                                 json.dumps(s.get('size', [s.get(k) for k in ('width', 'height', 'depth')])),
                                 s.get('hash'), bool(s.get('dynamic')), bool(s.get('rewritten')), s.get('kind'))
                                for s in shader.get('srvs', [])))
                            con.executemany('INSERT INTO cbuffers VALUES(?,?,?,?,?,?)', (
                                (pk, r['eid'], stage, cb.get('register'), cb.get('data'), cb.get('size'))
                                for cb in shader.get('cbuffers', [])))
                    counts['updated' if prev else 'added'] += 1
                for cid in old.keys() - seen:
                    con.execute('DELETE FROM captures WHERE pk=?', (old[cid][0],))
                    counts['removed'] += 1
                con.execute('DELETE FROM blobs WHERE store=?', (store,))
                for p in sorted((Path(store) / 'blobs').glob('*/*')):
                    if p.is_file():
                        con.execute('INSERT INTO blobs VALUES(?,?,?,?,?)',
                                    (p.parent.name, p.stem, store, p.relative_to(store).as_posix(), p.stat().st_size))
                if not Path(store).is_dir():
                    con.execute('DELETE FROM stores WHERE path=?', (store,))
    finally:
        con.close()
    counts['seconds'] = round(time.perf_counter() - start, 6)
    return counts


class Digest:
    def __init__(self, db):
        self.db = str(Path(db).resolve())
        self.con = sqlite3.connect(Path(self.db).as_uri() + '?mode=ro', uri=True)
        self.con.row_factory = sqlite3.Row
        self.con.create_function('regexp', 2, lambda pattern, value: bool(re.search(pattern, value or '')))
        self._cached = None

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _rows(self, sql, args=()):
        return [dict(r) for r in self.con.execute(sql, args)]

    def check_duplicates(self):
        duplicates = self._rows('SELECT id FROM captures GROUP BY id HAVING count(*)>1')
        if duplicates:
            raise ValueError('duplicate capture ids: ' + '; '.join(
                '%s: %s' % (r['id'], ', '.join(x['store'] for x in self._rows(
                    'SELECT store FROM captures WHERE id=? ORDER BY store', (r['id'],)))) for r in duplicates))

    def captures(self, game=None):
        self.check_duplicates()
        game = {}.get(game, game)
        # Synthetic and user stores may already use the short game name.
        if game and not self.con.execute('SELECT 1 FROM captures WHERE game=? LIMIT 1', (game,)).fetchone():
            game = {}.get(game, game)
        return self._rows('SELECT * FROM captures' + (' WHERE game=?' if game else '') + ' ORDER BY id',
                          (game,) if game else ())

    def resolve(self, capture):
        rows = self._rows('SELECT * FROM captures WHERE id=? ORDER BY store', (capture,))
        if len(rows) != 1:
            raise ValueError('%s: %d captures: %s' % (capture, len(rows), ', '.join(r['store'] for r in rows)))
        return rows[0]

    def manifest(self, capture):
        row = self.resolve(capture)
        key = (row['store'], capture)
        if not self._cached or self._cached[0] != key:
            with builtins.open(Path(row['store']) / 'captures' / capture / 'manifest.json', encoding='utf-8') as f:
                self._cached = (key, json.load(f))
        return self._cached[1]

    def draws(self, capture=None, kept=False, ps=None, shader=None, texture=None, marker=None, blend=None, target=None):
        self.check_duplicates()
        conditions, args = [], []
        if capture:
            ids = [capture] if isinstance(capture, str) else list(capture)
            for cid in ids:
                self.resolve(cid)
            conditions.append('c.id IN (%s)' % ','.join('?' * len(ids)))
            args.extend(ids)
        if kept:
            conditions.append('d.kept=1')
        for prefix, kind, stage in ((ps, 'shader', 'ps'), (shader, 'shader', None), (texture, 'texture', None)):
            if prefix:
                conditions.append('EXISTS (SELECT 1 FROM uses u WHERE u.capture=d.capture AND u.eid=d.eid '
                                  'AND u.kind=? AND u.hash LIKE ?' + (' AND u.stage=?' if stage else '') + ')')
                args.extend([kind, _prefix(prefix)] + ([stage] if stage else []))
        if marker:
            re.compile(marker)
            conditions.append('regexp(?,d.marker)')
            args.append(marker)
        if blend:
            conditions.append('d.blend_kind=?')
            args.append(blend)
        rows = self._rows('SELECT c.id,c.store,d.row_json FROM draws d JOIN captures c ON c.pk=d.capture' +
                          (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ' ORDER BY c.id,d.eid', args)
        out = []
        for row in rows:
            r = json.loads(row['row_json'])
            if target and not any(t['id'] == target for t in r.get('targets', [])):
                continue
            r.update(capture=row['id'], store=row['store'])
            out.append(r)
        return out

    def draw(self, capture, eid):
        m = self.manifest(capture)
        for row in m['draws']:
            if row['eid'] == eid:
                return copy.deepcopy(row)
        raise ValueError('no draw %s in %s' % (eid, capture))

    def cbuffer(self, capture, eid, stage, slot):
        import rdcdigest as cli
        m, r = self.manifest(capture), self.draw(capture, eid)
        cb = next((c for c in r.get('shaders', {}).get(stage.lower(), {}).get('cbuffers', [])
                   if c['register'] == slot), None)
        if not cb or not cb.get('data'):
            raise ValueError('constant buffer not stored: %s/%s/%s/b%s' % (capture, eid, stage, slot))
        raw = bytes.fromhex(m['tables']['cbdata'][cb['data']])
        rows = cli.floats_rows(raw.hex())
        return dict(raw=raw, float4=[v[1] for v in rows],
                    uint_rows={i // 16: u for i, f, u in rows if '(uint)' in cli.fmt_row(i, f, u)},
                    decoded=m['tables'].get('cbvars', {}).get(cb.get('vars')), key=cb['data'])

    def where(self, prefix):
        self.check_duplicates()
        return self._rows('SELECT DISTINCT c.id AS capture,c.store,u.eid,u.stage,u.slot,u.kind,u.hash '
                          'FROM uses u JOIN captures c ON c.pk=u.capture WHERE u.hash LIKE ? '
                          'ORDER BY c.id,u.eid,u.stage,u.slot,u.kind,u.hash', (_prefix(prefix),))

    def texture_path(self, prefix):
        self.check_duplicates()
        hits = self._rows("SELECT * FROM blobs WHERE kind IN ('textures','dynamic') AND hash LIKE ? "
                          "AND (relative_path LIKE '%.dds' OR relative_path LIKE '%.exr' OR relative_path LIKE '%.png') "
                          'ORDER BY store,relative_path', (_prefix(prefix),))
        if not hits or len({r['hash'] for r in hits}) != 1:
            raise ValueError('%d texture hashes match %s' % (len({r['hash'] for r in hits}), prefix))
        return str(Path(hits[0]['store']) / hits[0]['relative_path'])

    def texture(self, hash, mip=0, slice=0):
        import rdcdigest as cli
        from PIL import Image
        p = self.texture_path(hash)
        if p.endswith('.dds'):
            dds = ddsio.read_dds(Path(p).read_bytes())
            if not (0 <= mip < dds.mips and 0 <= slice < dds.slices):
                raise ValueError('mip/slice out of range')
            array = np.asarray(ddsio.to_image(dds, mip, slice))
            fmt = dds.format
        else:
            if mip or slice:
                raise ValueError('PNG/EXR snapshot has only mip 0, slice 0')
            array = np.asarray(cli.dynamic_image(p)) if p.endswith('.exr') else np.asarray(Image.open(p))
            side = Path(p).with_suffix('.json')
            fmt = json.loads(side.read_text()).get('raw_format') if side.exists() else Path(p).suffix[1:]
        return dict(array=array, format=fmt, size=(array.shape[1], array.shape[0]), path=p)

    def mesh(self, capture, eid):
        m, r = self.manifest(capture), self.draw(capture, eid)
        store = Path(self.resolve(capture)['store'])
        layout = m['tables'].get('layout', {}).get(r.get('layout'), [])
        ib = r.get('ib')
        indices = None
        if ib:
            indices = np.frombuffer((store / 'blobs/meshes' / (ib['hash'] + '.bin')).read_bytes(),
                                    '<u2' if ib['format'] == 'R16_UINT' else '<u4').copy()
        attrs, element_indices = {}, {}
        for v in r.get('vb', []):
            data = (store / 'blobs/meshes' / (v['hash'] + '.bin')).read_bytes()
            for a in layout:
                if a['slot'] != v['slot'] or not a.get('used', True):
                    continue
                attrs[a['name']] = _vertex_array(data, v['stride'], a['offset'], a['format'])
                if not v.get('bound_stride', v['stride']):
                    ids = np.zeros(max(1, r.get('instances', 1)) if a['perInstance'] else r['indices'], dtype=np.int64)
                elif a['perInstance']:
                    ids = np.arange(max(1, r.get('instances', 1))) // max(1, a.get('rate', 1))
                    ids += r.get('instance_offset', 0) - v['first_element']
                elif indices is not None:
                    ids = indices.astype(np.int64) + r.get('base_vertex', 0) - v['first_element']
                    restart = np.iinfo(indices.dtype).max
                    ids[indices == restart] = -1
                else:
                    ids = np.arange(r['indices']) + r.get('vertex_offset', 0) - v['first_element']
                element_indices[a['name']] = ids
        return dict(attributes=attrs, indices=indices, element_indices=element_indices, layout=copy.deepcopy(layout))

    def postvs(self, capture, eid, stage='vs'):
        import rdcdigest as cli
        m, r = self.manifest(capture), self.draw(capture, eid)
        pv, layout, data = cli.postvs_data(self.resolve(capture)['store'], m, r, stage)
        if data is None:
            raise ValueError('post-VS blob unavailable: %s' % pv)
        attrs = {('%s%d' % (a['name'], a['index'])):
                 data[..., a['offset'] // 4:a['offset'] // 4 + a['comps']] for a in layout or []}
        return dict(entry=pv, layout=layout, array=data, attributes=attrs,
                    screen=cli.screen_of(data[..., :4], r['viewport']))

    def contrib(self, capture, eid, slot=0):
        r = self.draw(capture, eid)
        c = next((v for v in r.get('contrib', []) if v['slot'] == slot), None)
        if not c or not c.get('exr'):
            raise ValueError('contribution EXR unavailable: %s' % c)
        path = Path(self.resolve(capture)['store']) / 'captures' / capture / 'contrib' / c['exr']
        channels = ddsio.read_exr(str(path))
        names = [k for k in 'RGBA' if k in channels] or sorted(channels)
        return dict(array=np.stack([channels[k].astype(np.float32) for k in names], -1),
                    channels=names, box=c['box'], entry=c)


def _prefix(value):
    if not re.fullmatch('[0-9a-fA-F]+', value):
        raise ValueError('expected a hexadecimal hash/checksum prefix: %s' % value)
    return value.lower() + '%'


def _vertex_array(data, stride, offset, fmt):
    """Decode scalar/vector DXGI inputs and the common packed 10:10:10:2 layout."""
    if fmt.upper() in ('R10G10B10A2_UNORM', 'R10G10B10A2_UINT'):
        n = len(data) // stride
        raw = np.ndarray((n,), dtype='<u4', buffer=data, offset=offset, strides=(stride,))
        result = np.stack([raw & 1023, (raw >> 10) & 1023, (raw >> 20) & 1023, raw >> 30], -1)
        return result / np.array([1023, 1023, 1023, 3]) if fmt.upper().endswith('UNORM') else result
    match = re.fullmatch(r'((?:[RGBA]\d+)+)_(FLOAT|UINT|SINT|UNORM|SNORM)(?:_SRGB)?', fmt.upper())
    if not match:
        raise ValueError('unsupported vertex format: %s' % fmt)
    widths = [int(x) for x in re.findall(r'\d+', match[1])]
    if len(set(widths)) != 1 or widths[0] not in (8, 16, 32):
        raise ValueError('unsupported packed vertex format: %s' % fmt)
    bits, kind = widths[0], match[2]
    code = 'f' if kind == 'FLOAT' else ('i' if kind in ('SINT', 'SNORM') else 'u')
    dtype = np.dtype('<%s%d' % (code, bits // 8))
    n = len(data) // stride
    arr = np.ndarray((n, len(widths)), dtype=dtype, buffer=data, offset=offset, strides=(stride, bits // 8)).copy()
    if kind in ('UNORM', 'SNORM'):
        arr = arr.astype(np.float64) / ((1 << (bits - (kind == 'SNORM'))) - 1)
        if kind == 'SNORM':
            arr = np.maximum(arr, -1)
    if fmt.upper().startswith('B8G8R8A8'):
        arr = arr[:, [2, 1, 0, 3]]
    return arr


def open(db=DEFAULT_DB):
    return Digest(db)
