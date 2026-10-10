"""Inspect binary FBX files and write a JSON inventory plus a Markdown report.

python tools/assetinspect/fbx_inspect.py <file-or-folder>... --out <new folder> [--trees]

Reads every *.fbx given (folders are searched recursively) and reports, from
the parsed records only: object counts, the Model hierarchy, whether Geometry,
Material, Texture, Video and Deformer objects are present, every animation
curve with its connected curve node, target object and property, its key times
(ticks, seconds and frames when the file stores a frame rate) and values, and
connections that name an object id the file does not contain.

Binary FBX 7.x only (32-bit records before 7500, 64-bit from 7500). ASCII FBX,
big-endian binary and the 6.x object layout are reported as unsupported.
Source files are only read. Exit status: 0 every file inspected, 1 at least one
file could not be (fully) inspected (the report is still written), 2 usage
error, nothing to inspect, or the output folder was refused.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zlib

MAGIC = b'Kaydara FBX Binary  \x00\x1a'   # 22 bytes, then endian byte, then u32 version
HEADER_SIZE = 27
KTIME_PER_SECOND = 46186158000           # ufbx.c ufbxi_read_header_extension
# GlobalSettings TimeMode -> frames per second, ufbx.c ufbxi_time_mode_fps.
# 14 (custom) reads CustomFrameRate instead.
TIME_MODE_FPS = {0: 30.0, 1: 120.0, 2: 100.0, 3: 60.0, 4: 50.0, 5: 48.0, 6: 30.0, 7: 30.0, 8: 29.97,
                 9: 29.97, 10: 25.0, 11: 24.0, 12: 1000.0, 13: 23.976, 15: 96.0, 16: 72.0, 17: 59.94}
TIME_MODE_CUSTOM = 14
SCALAR = {'Y': '<h', 'C': '<B', 'I': '<i', 'F': '<f', 'D': '<d', 'L': '<q'}
ARRAY = {'f': ('<f', 4), 'd': ('<d', 8), 'l': ('<q', 8), 'i': ('<i', 4), 'b': ('<B', 1), 'c': ('<B', 1)}
MAX_DEPTH = 128
PRESENCE = ('Geometry', 'Material', 'Texture', 'Video', 'Deformer')


class FbxError(Exception):
    """A file that cannot be parsed. kind is 'malformed' or 'unsupported'."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class UsageError(Exception):
    pass


# --- binary parsing ---------------------------------------------------------

def looks_ascii_fbx(data):
    head = data[:4096].lstrip(b'\xef\xbb\xbf').lstrip()
    return head.startswith(b';') and b'FBX' in head[:200] or b'FBXHeaderExtension:' in head


def parse_fbx(data):
    """Parse a binary FBX into {'version', 'layout', 'nodes', 'warnings'}.

    Each node is {'name', 'offset', 'props': [(type, value)], 'children'}.
    Every read is bounds-checked against its parent record; arrays must decode
    to exactly count x element size bytes.
    """
    if data[:len(MAGIC)] != MAGIC:
        if looks_ascii_fbx(data):
            raise FbxError('unsupported', 'ASCII FBX is not supported; only binary FBX can be inspected')
        if len(data) < HEADER_SIZE:
            raise FbxError('malformed', 'file is %d bytes, shorter than the 27-byte binary FBX header' % len(data))
        raise FbxError('malformed', 'binary FBX header magic "Kaydara FBX Binary" not found')
    if len(data) < HEADER_SIZE:
        raise FbxError('malformed', 'truncated binary FBX header (%d bytes)' % len(data))
    if data[22] != 0:
        raise FbxError('unsupported', 'big-endian binary FBX (endian byte %d) is not supported' % data[22])
    version = struct.unpack_from('<I', data, 23)[0]
    wide = version >= 7500
    rec = 25 if wide else 13
    warnings = []

    def need(pos, size, limit, what):
        if size < 0 or pos + size > limit:
            raise FbxError('malformed', '%s at byte %d needs %d bytes but only %d remain before byte %d'
                           % (what, pos, size, max(0, limit - pos), limit))

    def prop(pos, limit, where):
        need(pos, 1, limit, where + ' property type')
        t = chr(data[pos])
        pos += 1
        if t in SCALAR:
            fmt = SCALAR[t]
            size = struct.calcsize(fmt)
            need(pos, size, limit, '%s property %s' % (where, t))
            v = struct.unpack_from(fmt, data, pos)[0]
            return (t, bool(v) if t == 'C' else v), pos + size
        if t in 'SR':
            need(pos, 4, limit, where + ' string length')
            n = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            need(pos, n, limit, '%s %s data' % (where, 'string' if t == 'S' else 'raw'))
            raw = data[pos:pos + n]
            return (t, raw.decode('utf-8', 'replace') if t == 'S' else bytes(raw)), pos + n
        if t in ARRAY:
            need(pos, 12, limit, where + ' array header')
            count, encoding, length = struct.unpack_from('<III', data, pos)
            pos += 12
            need(pos, length, limit, '%s array %s data' % (where, t))
            fmt, size = ARRAY[t]
            expected = count * size
            raw = data[pos:pos + length]
            if encoding == 0:
                if length != expected:
                    raise FbxError('malformed', '%s array %s at byte %d: %d elements need %d bytes, record stores %d'
                                   % (where, t, pos, count, expected, length))
            elif encoding == 1:
                d = zlib.decompressobj()
                try:
                    out = d.decompress(raw, expected + 1)
                except zlib.error as e:
                    raise FbxError('malformed', '%s array %s at byte %d: zlib data is corrupt (%s)' % (where, t, pos, e))
                if not d.eof and len(out) <= expected:
                    raise FbxError('malformed', '%s array %s at byte %d: compressed stream is truncated (%d of %d bytes)'
                                   % (where, t, pos, len(out), expected))
                if len(out) != expected or not d.eof or d.unconsumed_tail:
                    raise FbxError('malformed', '%s array %s at byte %d: compressed data decodes to %s bytes, %d expected'
                                   % (where, t, pos, len(out) if d.eof else 'more than %d' % expected, expected))
                raw = out
            else:
                raise FbxError('malformed', '%s array %s at byte %d: unknown array encoding %d' % (where, t, pos, encoding))
            values = list(struct.unpack('<%d%s' % (count, fmt[1]), raw)) if count else []
            if t == 'b':
                values = [bool(v) for v in values]
            return (t, values), pos + length
        raise FbxError('malformed', '%s: unknown property type 0x%02x at byte %d' % (where, ord(t), pos - 1))

    def node(pos, limit, depth):
        if depth > MAX_DEPTH:
            raise FbxError('malformed', 'records nested deeper than %d at byte %d' % (MAX_DEPTH, pos))
        need(pos, rec, limit, 'record header')
        if wide:
            end, nprops, plen = struct.unpack_from('<QQQ', data, pos)
        else:
            end, nprops, plen = struct.unpack_from('<III', data, pos)
        nlen = data[pos + rec - 1]
        if end == 0 and nprops == 0 and plen == 0 and nlen == 0:
            return None, pos + rec
        body = pos + rec + nlen + plen
        if end > limit:
            raise FbxError('malformed', 'record at byte %d claims to end at byte %d, past the end of its %s at byte %d'
                           % (pos, end, 'file (truncated?)' if limit == len(data) else 'parent record', limit))
        if end < body:
            raise FbxError('malformed', 'record at byte %d claims to end at byte %d, before its name and properties end '
                           '(byte %d)' % (pos, end, body))
        name = data[pos + rec:pos + rec + nlen].decode('utf-8', 'replace')
        where = '%s record at byte %d' % (name, pos)
        p = pos + rec + nlen
        props = []
        for i in range(nprops):
            if p >= body:
                raise FbxError('malformed', '%s: property %d of %d starts past its %d-byte property list'
                               % (where, i + 1, nprops, plen))
            v, p = prop(p, body, where)
            props.append(v)
        if p != body:
            raise FbxError('malformed', '%s: properties end at byte %d, property list says %d' % (where, p, body))
        children = []
        while p < end:
            child, p = node(p, end, depth + 1)
            if child is None:
                break
            children.append(child)
        if p != end:
            raise FbxError('malformed', '%s: children end at byte %d, record says %d' % (where, p, end))
        return {'name': name, 'offset': pos, 'props': props, 'children': children}, end

    nodes = []
    pos = HEADER_SIZE
    terminated = False
    while pos + rec <= len(data):
        n, pos = node(pos, len(data), 0)
        if n is None:
            terminated = True
            break
        nodes.append(n)
    if not terminated:
        if pos < len(data):
            raise FbxError('malformed', 'truncated record header at byte %d (file is %d bytes)' % (pos, len(data)))
        warnings.append('no top-level terminating record before end of file')
    if not nodes:
        raise FbxError('malformed', 'binary FBX header present but no records follow')
    return {'version': version, 'layout': '64-bit records (version >= 7500)' if wide else '32-bit records (version < 7500)',
            'nodes': nodes, 'warnings': warnings}


# --- record helpers ---------------------------------------------------------

def vals(n):
    return [v for _, v in n['props']]


def child(n, name):
    return next((c for c in n['children'] if c['name'] == name), None)


def child_value(n, name):
    c = child(n, name)
    return vals(c)[0] if c and c['props'] else None


def split_name(s):
    """FBX object names are stored as "Name\\x00\\x01Class"."""
    if not isinstance(s, str):
        return None, None
    if '\x00\x01' in s:
        name, cls = s.split('\x00\x01', 1)
        return name, cls
    return s, None


def properties70(n):
    p70 = child(n, 'Properties70') or child(n, 'Properties60')
    out = {}
    if not p70:
        return out
    for p in p70['children']:
        v = vals(p)
        if p['name'] not in ('P', 'Property') or not v or not isinstance(v[0], str):
            continue
        if p['name'] == 'P':   # name, type, label, flags, values...
            out[v[0]] = {'type': v[1] if len(v) > 1 else None, 'flags': v[3] if len(v) > 3 else None,
                         'values': [jsonable(x) for x in v[4:]]}
        else:                  # Properties60: name, type, flags, values...
            out[v[0]] = {'type': v[1] if len(v) > 1 else None, 'flags': v[2] if len(v) > 2 else None,
                         'values': [jsonable(x) for x in v[3:]]}
    return out


def jsonable(v):
    if isinstance(v, (bytes, bytearray)):
        return {'raw_bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()}
    if isinstance(v, float) and v != v:
        return 'NaN'
    if isinstance(v, float) and v in (float('inf'), float('-inf')):
        return str(v)
    return v


def clean(o):
    """Make a structure strict-JSON safe (NaN/inf as strings, bytes summarised)."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return jsonable(o)


def tree_json(n):
    return {'name': n['name'], 'offset': n['offset'],
            'props': [{'type': t, 'value': [jsonable(x) for x in v] if isinstance(v, list) else jsonable(v)}
                      for t, v in n['props']],
            'children': [tree_json(c) for c in n['children']]}


def seconds(t):
    return round(t / KTIME_PER_SECOND, 9)


# --- analysis ---------------------------------------------------------------

def analyse(parsed):
    """Derive the inventory from parsed records. Returns (result, diagnostics, complete)."""
    diags = []
    complete = True
    nodes = parsed['nodes']
    top = {}
    for n in nodes:
        top.setdefault(n['name'], n)
    res = {'top_level_records': [n['name'] for n in nodes]}

    if parsed['version'] < 7000:
        raise FbxError('unsupported', 'FBX version %d uses the 6.x object layout (objects keyed by name, no numeric ids), '
                                      'which is not analysed; only 7.x is supported' % parsed['version'])

    # Global settings: stored values only.
    gs = top.get('GlobalSettings')
    fps = None
    if gs:
        p = properties70(gs)
        settings = {k: p[k]['values'] for k in ('UpAxis', 'UpAxisSign', 'FrontAxis', 'CoordAxis', 'UnitScaleFactor',
                                                'TimeMode', 'CustomFrameRate', 'TimeSpanStart', 'TimeSpanStop') if k in p}
        mode = (settings.get('TimeMode') or [None])[0]
        if mode == TIME_MODE_CUSTOM:
            fps = (settings.get('CustomFrameRate') or [None])[0]
            fps_source = 'CustomFrameRate'
        elif mode in TIME_MODE_FPS:
            fps = TIME_MODE_FPS[mode]
            fps_source = 'TimeMode %d' % mode
        else:
            fps_source = 'TimeMode not stored' if mode is None else 'TimeMode %r not recognised' % mode
        res['global_settings'] = {'stored': settings, 'frames_per_second': fps, 'frame_rate_source': fps_source}
    else:
        res['global_settings'] = None
        diags.append('no GlobalSettings record: frame rate unknown, keys reported in ticks and seconds only')

    # Definitions: what the exporter declared.
    defs = top.get('Definitions')
    declared = {}
    if defs:
        for ot in defs['children']:
            if ot['name'] == 'ObjectType' and ot['props']:
                cnt = child_value(ot, 'Count')
                declared[vals(ot)[0]] = cnt
    res['declared_object_types'] = declared or None

    objs_rec = top.get('Objects')
    objects = {}
    order = []
    if objs_rec is None:
        complete = False
        diags.append('no Objects record: object counts, hierarchy, presence and animation could not be inspected')
    else:
        for o in objs_rec['children']:
            v = vals(o)
            if not v or not isinstance(v[0], int) or isinstance(v[0], bool):
                complete = False
                diags.append('%s record at byte %d in Objects has no numeric id; skipped' % (o['name'], o['offset']))
                continue
            name, cls = split_name(v[1] if len(v) > 1 else None)
            entry = {'id': v[0], 'record': o['name'], 'name': name, 'class': cls,
                     'subclass': v[2] if len(v) > 2 and isinstance(v[2], str) else None, 'node': o}
            if v[0] in objects:
                diags.append('object id %d appears more than once (%s and %s); the later one is ignored'
                             % (v[0], objects[v[0]]['record'], o['name']))
                continue
            objects[v[0]] = entry
            order.append(entry)
    res['object_counts'] = None if objs_rec is None else count_by(order, lambda e: e['record'])
    res['object_counts_by_subclass'] = None if objs_rec is None else count_by(
        order, lambda e: '%s:%s' % (e['record'], e['subclass'] or '-'))
    if declared and objs_rec is not None:
        for t, cnt in sorted(declared.items()):
            if t == 'GlobalSettings':
                continue
            actual = res['object_counts'].get(t, 0)
            if cnt is not None and cnt != actual:
                diags.append('Definitions declares %d %s object(s), Objects contains %d' % (cnt, t, actual))

    # Presence of the asset-bearing object types.
    presence = {}
    for t in PRESENCE:
        if objs_rec is None:
            presence[t] = {'status': 'not_inspected', 'count': None, 'reason': 'no Objects record'}
        else:
            c = res['object_counts'].get(t, 0)
            presence[t] = {'status': 'present' if c else 'absent', 'count': c}
    res['presence'] = presence

    conn_rec = top.get('Connections')
    conns = []
    if conn_rec is None:
        complete = False
        diags.append('no Connections record: hierarchy, curve targets and dangling links could not be inspected')
    else:
        for c in conn_rec['children']:
            v = vals(c)
            if (c['name'] != 'C' or len(v) < 3 or not isinstance(v[0], str)
                    or not all(isinstance(x, int) and not isinstance(x, bool) for x in v[1:3])):
                complete = False
                diags.append('%s record at byte %d in Connections is not (type, source id, destination id); skipped'
                             % (c['name'], c['offset']))
                continue
            conns.append({'type': v[0], 'src': v[1], 'dst': v[2],
                          'property': v[3] if len(v) > 3 and isinstance(v[3], str) else None})
    res['connection_count'] = None if conn_rec is None else len(conns)

    def describe(i):
        if i == 0:
            return {'id': 0, 'record': 'scene root'}
        e = objects.get(i)
        if not e:
            return {'id': i, 'record': None, 'missing': True}
        return {'id': i, 'record': e['record'], 'name': e['name'], 'subclass': e['subclass']}

    # Dangling connections: either end names an id that is not an object (0 is the scene root).
    dangling = []
    if conn_rec is not None and objs_rec is not None:
        for c in conns:
            missing = [end for end, i in (('source', c['src']), ('destination', c['dst']))
                       if i not in objects and not (end == 'destination' and i == 0)]
            if missing:
                dangling.append({'type': c['type'], 'property': c['property'], 'missing': missing,
                                 'source': describe(c['src']), 'destination': describe(c['dst'])})
    res['dangling_connections'] = None if conn_rec is None or objs_rec is None else dangling

    outgoing, incoming = {}, {}
    for c in conns:
        outgoing.setdefault(c['src'], []).append(c)
        incoming.setdefault(c['dst'], []).append(c)

    res['hierarchy'] = None
    res['geometry'] = res['materials'] = res['textures'] = res['videos'] = res['deformers'] = None
    res['animation'] = None
    if objs_rec is None:
        return res, diags, complete

    res['geometry'] = [geometry_info(e, outgoing, objects) for e in order if e['record'] == 'Geometry']
    res['materials'] = [material_info(e, incoming, outgoing, objects) for e in order if e['record'] == 'Material']
    res['textures'] = [texture_info(e, incoming, outgoing, objects) for e in order if e['record'] == 'Texture']
    res['videos'] = [video_info(e) for e in order if e['record'] == 'Video']
    res['deformers'] = [{'id': e['id'], 'name': e['name'], 'subclass': e['subclass'],
                         'indexes': len(child_value(e['node'], 'Indexes') or [])} for e in order if e['record'] == 'Deformer']

    if conn_rec is not None:
        res['hierarchy'] = hierarchy(order, objects, outgoing, incoming, diags)
    anim, anim_complete = animation(order, objects, outgoing, fps, conn_rec is not None, diags)
    res['animation'] = anim
    complete = complete and anim_complete
    return res, diags, complete


def count_by(items, key):
    out = {}
    for e in items:
        k = key(e)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def geometry_info(e, outgoing, objects):
    n = e['node']
    verts = child_value(n, 'Vertices')
    pvi = child_value(n, 'PolygonVertexIndex')
    layers = []
    for c in n['children']:
        if c['name'].startswith('LayerElement'):
            layers.append({'element': c['name'], 'name': child_value(c, 'Name'),
                           'mapping': child_value(c, 'MappingInformationType'),
                           'reference': child_value(c, 'ReferenceInformationType'),
                           'arrays': {a['name']: len(vals(a)[0]) for a in c['children']
                                      if a['props'] and isinstance(vals(a)[0], list)}})
    return {'id': e['id'], 'name': e['name'], 'subclass': e['subclass'],
            'control_points': None if verts is None else len(verts) // 3,
            'polygons': None if pvi is None else sum(1 for i in pvi if i < 0),
            'polygon_vertex_indices': None if pvi is None else len(pvi),
            'layer_elements': layers,
            'connected_to': [{'type': c['type'], **describe_obj(c['dst'], objects)} for c in outgoing.get(e['id'], [])]}


def describe_obj(i, objects):
    if i == 0:
        return {'id': 0, 'record': 'scene root'}
    o = objects.get(i)
    return {'id': i, 'record': o['record'], 'name': o['name']} if o else {'id': i, 'record': None, 'missing': True}


def material_info(e, incoming, outgoing, objects):
    return {'id': e['id'], 'name': e['name'], 'shading_model': child_value(e['node'], 'ShadingModel'),
            'stored_properties': properties70(e['node']),
            'textures': [{'property': c['property'], **describe_obj(c['src'], objects)} for c in incoming.get(e['id'], [])
                         if objects.get(c['src'], {}).get('record') == 'Texture'],
            'connected_to': [describe_obj(c['dst'], objects) for c in outgoing.get(e['id'], [])]}


def texture_info(e, incoming, outgoing, objects):
    n = e['node']
    return {'id': e['id'], 'name': e['name'], 'file_name': child_value(n, 'FileName'),
            'relative_file_name': child_value(n, 'RelativeFilename'),
            'videos': [describe_obj(c['src'], objects) for c in incoming.get(e['id'], [])
                       if objects.get(c['src'], {}).get('record') == 'Video'],
            'connected_to': [{'type': c['type'], 'property': c['property'], **describe_obj(c['dst'], objects)}
                             for c in outgoing.get(e['id'], [])]}


def video_info(e):
    n = e['node']
    content = child_value(n, 'Content')
    return {'id': e['id'], 'name': e['name'], 'type': child_value(n, 'Type'), 'file_name': child_value(n, 'FileName'),
            'relative_file_name': child_value(n, 'RelativeFilename'),
            'embedded_bytes': len(content) if isinstance(content, (bytes, bytearray)) else None}


def hierarchy(order, objects, outgoing, incoming, diags):
    models = [e for e in order if e['record'] == 'Model']
    parent = {}
    for m in models:
        ps = [c['dst'] for c in outgoing.get(m['id'], []) if c['type'] == 'OO'
              and (c['dst'] == 0 or objects.get(c['dst'], {}).get('record') == 'Model')]
        if len(ps) > 1:
            diags.append('Model "%s" (%d) has %d parent connections; using the first' % (m['name'], m['id'], len(ps)))
        parent[m['id']] = ps[0] if ps else None
    kids = {}
    for m in models:
        kids.setdefault(parent[m['id']], []).append(m)

    def build(m, seen):
        p = properties70(m['node'])
        item = {'id': m['id'], 'name': m['name'], 'subclass': m['subclass'],
                'stored_transform': {k: p[k]['values'] for k in ('Lcl Translation', 'Lcl Rotation', 'Lcl Scaling') if k in p},
                'attached': [{'record': objects[c['src']]['record'], 'name': objects[c['src']]['name'],
                              'subclass': objects[c['src']]['subclass']}
                             for c in incoming.get(m['id'], []) if c['src'] in objects
                             and objects[c['src']]['record'] not in ('Model', 'AnimationCurveNode')],
                'children': []}
        for k in kids.get(m['id'], []):
            if k['id'] in seen:
                diags.append('Model hierarchy cycle at "%s" (%d)' % (k['name'], k['id']))
                continue
            item['children'].append(build(k, seen | {k['id']}))
        return item

    roots = [build(m, {m['id']}) for m in kids.get(0, [])]
    orphans = [build(m, {m['id']}) for m in kids.get(None, [])]
    if orphans:
        diags.append('%d Model(s) have no parent connection (neither scene root nor another Model)' % len(orphans))
    placed = set()

    def mark(t):
        placed.add(t['id'])
        for c in t['children']:
            mark(c)
    for t in roots + orphans:
        mark(t)
    unplaced = [m for m in models if m['id'] not in placed]
    if unplaced:
        diags.append('%d Model(s) are only reachable through a parent cycle' % len(unplaced))
    return {'roots': roots, 'unparented': orphans}


def interpolation(flags):
    # ufbx.c ufbxi_read_animation_curve: constant first, then cubic, otherwise linear.
    if flags & 0x2:
        return 'constant-next' if flags & 0x100 else 'constant'
    if flags & 0x8:
        return 'cubic'
    if flags & 0x4:
        return 'linear'
    return 'unflagged (0x%x)' % flags


def animation(order, objects, outgoing, fps, have_conns, diags):
    complete = True
    out = {'stacks': [], 'layers': [], 'curve_nodes': [], 'curves': []}

    def links(i, rtype=None, ctype=None):
        return [c for c in outgoing.get(i, []) if (ctype is None or c['type'] == ctype)
                and (rtype is None or objects.get(c['dst'], {}).get('record') == rtype)]

    for e in order:
        if e['record'] == 'AnimationStack':
            p = properties70(e['node'])
            stack = {'id': e['id'], 'name': e['name']}
            for k in ('LocalStart', 'LocalStop', 'ReferenceStart', 'ReferenceStop'):
                if k in p and p[k]['values'] and isinstance(p[k]['values'][0], int):
                    stack[k] = {'ticks': p[k]['values'][0], 'seconds': seconds(p[k]['values'][0])}
            out['stacks'].append(stack)
        elif e['record'] == 'AnimationLayer':
            out['layers'].append({'id': e['id'], 'name': e['name'],
                                  'stacks': [objects[c['dst']]['name'] for c in links(e['id'], 'AnimationStack')]})
    node_info = {}
    for e in order:
        if e['record'] != 'AnimationCurveNode':
            continue
        p = properties70(e['node'])
        targets = [{'property': c['property'], 'connection': c['type'], **describe_obj(c['dst'], objects)}
                   for c in outgoing.get(e['id'], [])
                   if objects.get(c['dst'], {}).get('record') not in ('AnimationLayer', None)]
        dangling_targets = [c for c in outgoing.get(e['id'], []) if c['dst'] not in objects and c['dst'] != 0]
        layers = [objects[c['dst']]['name'] for c in links(e['id'], 'AnimationLayer')]
        stacks = sorted({s for c in links(e['id'], 'AnimationLayer')
                         for s in [objects[x['dst']]['name'] for x in links(c['dst'], 'AnimationStack')]})
        info = {'id': e['id'], 'name': e['name'], 'stored_defaults': {k: v['values'] for k, v in p.items()},
                'targets': targets, 'layers': layers, 'stacks': stacks,
                'targets_missing': [c['dst'] for c in dangling_targets]}
        node_info[e['id']] = info
        out['curve_nodes'].append(info)

    for e in order:
        if e['record'] != 'AnimationCurve':
            continue
        n = e['node']
        times = child_value(n, 'KeyTime')
        values = child_value(n, 'KeyValueFloat')
        if values is None:
            values = child_value(n, 'KeyValueDouble')
        curve = {'id': e['id'], 'name': e['name'], 'default': child_value(n, 'Default')}
        problems = []
        if not isinstance(times, list):
            problems.append('no KeyTime array')
        if not isinstance(values, list):
            problems.append('no KeyValueFloat array')
        if not problems and len(times) != len(values):
            problems.append('KeyTime has %d entries, KeyValueFloat %d' % (len(times), len(values)))
        if problems:
            complete = False
            curve['keys'] = None
            curve['problems'] = problems
            diags.append('AnimationCurve %d could not be read: %s' % (e['id'], '; '.join(problems)))
        else:
            curve['key_count'] = len(times)
            curve['times_ticks'] = times
            curve['times_seconds'] = [seconds(t) for t in times]
            curve['times_frames'] = [round(t / KTIME_PER_SECOND * fps, 6) for t in times] if fps else None
            curve['values'] = values
            curve['value_range'] = [min(values), max(values)] if values else None
            flags, refs = child_value(n, 'KeyAttrFlags'), child_value(n, 'KeyAttrRefCount')
            if isinstance(flags, list) and isinstance(refs, list) and len(flags) == len(refs) and sum(refs) == len(times) \
                    and all(r > 0 for r in refs):
                curve['interpolation'] = [interpolation(f) for f, r in zip(flags, refs) for _ in range(r)]
            else:
                curve['interpolation'] = None
                if flags is not None or refs is not None:
                    diags.append('AnimationCurve %d: KeyAttrFlags/KeyAttrRefCount do not cover its %d keys; '
                                 'interpolation not reported' % (e['id'], len(times)))
        owners = [c for c in outgoing.get(e['id'], []) if objects.get(c['dst'], {}).get('record') == 'AnimationCurveNode']
        curve['curve_nodes'] = []
        for c in owners:
            cn = node_info[c['dst']]
            curve['curve_nodes'].append({'id': cn['id'], 'name': cn['name'], 'channel': c['property'],
                                         'targets': cn['targets'], 'layers': cn['layers'], 'stacks': cn['stacks']})
        if have_conns and not owners:
            curve['curve_nodes'] = []
            diags.append('AnimationCurve %d is not connected to any AnimationCurveNode' % e['id'])
        out['curves'].append(curve)
    if have_conns:
        for cn in out['curve_nodes']:
            if not cn['targets'] and not cn['targets_missing']:
                diags.append('AnimationCurveNode "%s" (%d) drives no object' % (cn['name'], cn['id']))
    return out, complete


# --- driver -----------------------------------------------------------------

def collect_inputs(paths, suffixes):
    """Expand files and folders. Returns (files, unsupported_files, notes). Missing paths are a usage error."""
    files, unsupported, notes, seen = [], [], [], set()
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise UsageError('input does not exist: %s' % raw)
        if p.is_dir():
            found = sorted((f for f in p.rglob('*') if f.is_file() and f.suffix.lower() in suffixes),
                           key=lambda f: str(f).lower())
            if not found:
                notes.append('no %s files under %s' % ('/'.join(sorted(suffixes)), p))
            candidates = found
        else:
            candidates = [p]
        for f in candidates:
            key = str(f.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            (files if f.suffix.lower() in suffixes else unsupported).append(f)
    return files, unsupported, notes


def prepare_out(out, inputs):
    out = Path(out).resolve()
    for i in inputs:
        base = Path(i).resolve()
        if base.is_dir() and (out == base or base in out.parents):
            raise UsageError('output folder %s is inside the source folder %s; choose a folder outside the sources'
                             % (out, base))
    if out.exists():
        if not out.is_dir():
            raise UsageError('output path exists and is not a folder: %s' % out)
        if any(out.iterdir()):
            raise UsageError('output folder already exists and is not empty: %s (choose a new folder)' % out)
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_new(path, text):
    """Exclusive create: never replaces an existing file."""
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        f.write(text)


def safe_stem(name):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._') or 'file'


def inspect_file(path):
    rec = {'file': path.name, 'path': str(path.resolve())}
    try:
        data = path.read_bytes()
    except OSError as e:
        rec.update(status='unreadable', error=str(e))
        return rec, None
    rec['bytes'] = len(data)
    rec['sha256'] = hashlib.sha256(data).hexdigest()
    try:
        parsed = parse_fbx(data)
    except FbxError as e:
        rec.update(status=e.kind, error=str(e))
        return rec, None
    rec['version'] = parsed['version']
    rec['record_layout'] = parsed['layout']
    try:
        result, diags, complete = analyse(parsed)
    except FbxError as e:
        rec.update(status=e.kind, error=str(e), diagnostics=parsed['warnings'])
        return rec, parsed
    rec['status'] = 'ok' if complete else 'incomplete'
    rec['diagnostics'] = parsed['warnings'] + diags
    rec.update(result)
    return rec, parsed


def curve_signature(rec):
    anim = rec.get('animation')
    if not anim or not anim['curves']:
        return None
    sig = []
    for c in anim['curves']:
        for cn in c.get('curve_nodes') or [{}]:
            tgt = tuple((t.get('name'), t.get('property')) for t in cn.get('targets', []))
            sig.append((tgt, cn.get('channel'), tuple(c.get('times_ticks') or ()), tuple(c.get('values') or ())))
    return tuple(sorted(sig, key=repr))


def fmt_list(xs, nd=6):
    return '[' + ', '.join(('%.*g' % (nd + 3, x)) if isinstance(x, float) else str(x) for x in xs) + ']'


def render_tree(t, depth, lines):
    attached = ', '.join('%s "%s"' % (a['record'], a['name']) for a in t['attached'])
    lines.append('%s- %s "%s" (%s)%s' % ('  ' * depth, 'Model', t['name'], t['subclass'] or '-',
                                         ('; attached: ' + attached) if attached else ''))
    for c in t['children']:
        render_tree(c, depth + 1, lines)


def report_markdown(records, inputs, notes):
    L = ['# FBX inspection', '',
         'Generated by `tools/assetinspect/fbx_inspect.py`. Every statement below is derived from the parsed records of '
         'the files listed; sources were opened read-only.', '',
         'Inputs: ' + ', '.join('`%s`' % i for i in inputs), '']
    if notes:
        L += ['Notes:', ''] + ['- ' + n for n in notes] + ['']
    L += ['| File | Status | Version | Objects | Models | Geometry | Material | Texture | Curves | Dangling links |',
          '|---|---|---|---|---|---|---|---|---|---|']
    for r in records:
        pres = r.get('presence') or {}

        def ps(t):
            p = pres.get(t)
            if not p:
                return 'not inspected'
            return {'present': 'present (%s)' % p['count'], 'absent': 'absent'}.get(p['status'], 'not inspected')
        oc = r.get('object_counts')
        anim = r.get('animation')
        dl = r.get('dangling_connections')
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            r['file'], r['status'], r.get('version', '-'), sum(oc.values()) if oc is not None else 'not inspected',
            oc.get('Model', 0) if oc is not None else 'not inspected', ps('Geometry'), ps('Material'), ps('Texture'),
            len(anim['curves']) if anim else 'not inspected', len(dl) if dl is not None else 'not inspected'))
    L.append('')
    L.append('"absent" means the Objects record was parsed and holds no object of that type; "not inspected" means the '
             'file or section could not be read, so nothing is known.')
    for r in records:
        L += ['', '## ' + r['file'], '', '- Path: `%s`' % r['path']]
        if 'sha256' in r:
            L.append('- Size: %d bytes, SHA256 `%s`' % (r['bytes'], r['sha256']))
        L.append('- Status: **%s**' % r['status'])
        if r.get('error'):
            L.append('- Could not inspect: ' + r['error'])
        if 'version' in r:
            L.append('- FBX version %d, %s' % (r['version'], r['record_layout']))
        for d in r.get('diagnostics') or []:
            L.append('- Diagnostic: ' + d)
        if r['status'] not in ('ok', 'incomplete'):
            continue
        gs = r.get('global_settings')
        if gs:
            L.append('- Frame rate: %s (%s)' % (gs['frames_per_second'] if gs['frames_per_second'] else 'unknown',
                                                gs['frame_rate_source']))
        if r.get('object_counts') is not None:
            L.append('- Object counts: ' + (', '.join('%s %d' % (k[:-2] if k.endswith(':-') else '%s (%s)' % tuple(k.split(':', 1)), v)
                                                      for k, v in r['object_counts_by_subclass'].items())
                                            or 'none (Objects record is empty)'))
        for t in PRESENCE:
            p = r['presence'][t]
            L.append('- %s: %s' % (t, {'present': 'present, %s object(s)' % p['count'],
                                       'absent': 'absent (0 objects of this type in Objects)'}.get(
                p['status'], 'not inspected (%s)' % p.get('reason'))))
        for g in r.get('geometry') or []:
            L.append('  - Geometry "%s" (%s): %s control points, %s polygons, layer elements: %s' % (
                g['name'], g['subclass'], g['control_points'], g['polygons'],
                ', '.join(sorted({x['element'] for x in g['layer_elements']})) or 'none'))
        for m in r.get('materials') or []:
            L.append('  - Material "%s": stored ShadingModel %r, %d stored properties, textures: %s' % (
                m['name'], m['shading_model'], len(m['stored_properties']),
                ', '.join('"%s" on %s' % (t.get('name'), t['property']) for t in m['textures']) or 'none connected'))
        for t in r.get('textures') or []:
            L.append('  - Texture "%s": FileName %r, RelativeFilename %r' % (t['name'], t['file_name'], t['relative_file_name']))
        for v in r.get('videos') or []:
            L.append('  - Video "%s": FileName %r, embedded content %s' % (
                v['name'], v['file_name'], '%d bytes' % v['embedded_bytes'] if v['embedded_bytes'] is not None else 'none'))
        h = r.get('hierarchy')
        if h is not None:
            L += ['', 'Hierarchy (Model records, parent links from OO connections):', '']
            lines = []
            for t in h['roots']:
                render_tree(t, 0, lines)
            if h['unparented']:
                lines.append('- (no parent connection)')
                for t in h['unparented']:
                    render_tree(t, 1, lines)
            L += lines or ['- no Model records']
        elif r.get('object_counts') is not None:
            L += ['', 'Hierarchy: not inspected (no Connections record).']
        anim = r.get('animation')
        if anim is not None:
            L += ['', 'Animation: %d stack(s), %d layer(s), %d curve node(s), %d curve(s).' % (
                len(anim['stacks']), len(anim['layers']), len(anim['curve_nodes']), len(anim['curves']))]
            for s in anim['stacks']:
                span = ''
                if 'LocalStart' in s and 'LocalStop' in s:
                    span = ', local span %g..%g s' % (s['LocalStart']['seconds'], s['LocalStop']['seconds'])
                L.append('- Stack "%s"%s' % (s['name'], span))
            curve_links = {}
            for c in anim['curves']:
                for cn in c.get('curve_nodes') or []:
                    curve_links.setdefault(cn['id'], []).append(cn['channel'])
            for cn in anim['curve_nodes']:
                tg = ', '.join('%s "%s" / %s' % (t.get('record'), t.get('name'), t.get('property')) for t in cn['targets'])
                L.append('- Curve node "%s" -> %s; curves connected: %s%s' % (
                    cn['name'], tg or 'no target object', ', '.join(sorted(curve_links.get(cn['id'], []), key=str)) or 'none',
                    '; target id(s) missing from Objects: %s' % cn['targets_missing'] if cn['targets_missing'] else ''))
            for c in anim['curves']:
                owners = c.get('curve_nodes') or []
                if owners:
                    heads = []
                    for cn in owners:
                        tg = ', '.join('%s "%s" / %s' % (t.get('record') or 'missing object', t.get('name', t['id']),
                                                         t.get('property')) for t in cn['targets'])
                        heads.append('%s / curve node "%s" / %s' % (tg or 'no target', cn['name'], cn['channel']))
                    head = '; '.join(heads)
                else:
                    head = 'curve %d (not connected to a curve node)' % c['id']
                if c.get('keys', 1) is None:
                    L.append('- %s: could not read keys (%s)' % (head, '; '.join(c['problems'])))
                    continue
                L.append('- %s: %d key(s)' % (head, c['key_count']))
                L.append('  - seconds: ' + fmt_list(c['times_seconds']))
                if c['times_frames'] is not None:
                    L.append('  - frames: ' + fmt_list(c['times_frames']))
                L.append('  - stored values: ' + fmt_list(c['values']))
                if c['interpolation']:
                    L.append('  - interpolation: ' + ', '.join(sorted(set(c['interpolation']))))
        dl = r.get('dangling_connections')
        if dl is not None:
            L += ['', 'Connections naming an id that is not in Objects: %d' % len(dl)]
            groups = {}
            for d in dl:
                other = d['destination'] if 'source' in d['missing'] else d['source']
                key = ('/'.join(d['missing']), d['type'], d['property'], other.get('record'), other.get('name'))
                groups[key] = groups.get(key, 0) + 1
            for (miss, typ, prop, orec, oname), cnt in sorted(groups.items(), key=repr):
                L.append('- %d x %s connection, missing %s, property %r, other end: %s "%s"' % (
                    cnt, typ, miss, prop, orec or 'missing', oname))
    ok = [r for r in records if r['status'] in ('ok', 'incomplete')]
    if len(records) > 1:
        L += ['', '## Across files', '']
        by_hash = {}
        for r in records:
            if 'sha256' in r:
                by_hash.setdefault(r['sha256'], []).append(r['file'])
        same = [v for v in by_hash.values() if len(v) > 1]
        L.append('- Byte-identical files: ' + ('; '.join(', '.join(v) for v in same) if same else 'none'))
        by_sig = {}
        for r in ok:
            s = curve_signature(r)
            if s is not None:
                by_sig.setdefault(s, []).append(r['file'])
        groups = [v for v in by_sig.values() if len(v) > 1]
        L.append('- Files whose animation curves are identical (target, property, channel, key times and values): '
                 + ('; '.join(', '.join(v) for v in groups) if groups else 'none'))
        L.append('- Files with readable curves: %d of %d' % (sum(1 for r in ok if curve_signature(r) is not None),
                                                           len(records)))
    return '\n'.join(L) + '\n'


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='+', help='FBX files or folders (searched recursively for *.fbx)')
    ap.add_argument('--out', required=True, help='new or empty folder for inventory.json, REPORT.md and trees/')
    ap.add_argument('--trees', action='store_true', help='also write the full parsed record tree of each file')
    args = ap.parse_args(argv)
    try:
        files, unsupported, notes = collect_inputs(args.inputs, {'.fbx'})
        if not files and not unsupported:
            raise UsageError('nothing to inspect: no .fbx files in ' + ', '.join(args.inputs))
        out = prepare_out(args.out, args.inputs)
    except UsageError as e:
        print('error: ' + str(e), file=sys.stderr)
        return 2
    records = []
    for i, f in enumerate(files + unsupported, 1):
        if f in unsupported:
            rec, parsed = {'file': f.name, 'path': str(f.resolve()), 'status': 'unsupported',
                           'error': 'not an .fbx file (extension %r)' % f.suffix}, None
        else:
            rec, parsed = inspect_file(f)
        if args.trees and parsed is not None:
            rel = Path('trees') / ('%03d-%s.tree.json' % (i, safe_stem(f.stem)))
            (out / 'trees').mkdir(exist_ok=True)
            write_new(out / rel, json.dumps([tree_json(n) for n in parsed['nodes']], indent=1, allow_nan=False))
            rec['tree'] = rel.as_posix()
        records.append(rec)
        print('%-12s %s%s' % (rec['status'], f, ('  -- ' + rec['error']) if rec.get('error') else ''))
    inventory = {'tool': 'tools/assetinspect/fbx_inspect.py', 'inputs': [str(Path(p).resolve()) for p in args.inputs],
                 'notes': notes, 'ktime_per_second': KTIME_PER_SECOND, 'files': records}
    write_new(out / 'inventory.json', json.dumps(clean(inventory), indent=1, allow_nan=False))
    write_new(out / 'REPORT.md', report_markdown(records, args.inputs, notes))
    bad = [r for r in records if r['status'] != 'ok']
    print('inspected %d file(s): %d ok, %d not fully inspected -> %s' % (len(records), len(records) - len(bad), len(bad), out))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(run())
