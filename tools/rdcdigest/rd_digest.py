"""Run inside qrenderdoc (its embedded Python 3.8 has the renderdoc module); normally started by rdcdigest.py:
    qrenderdoc.exe --python rd_digest.py
Opens RD_CAPTURE once and writes a digest that later work reads instead of replaying the capture:

  RD_STORE/captures/RD_ID/manifest.json   every draw and dispatch: event id, marker path, counts, shaders (DXBC
                                          checksum), blend/depth/stencil/raster state, viewport, targets, SRVs (slot,
                                          format, size, content hash), samplers, constant buffers (raw hex + decoded
                                          variables), vertex input layout, vertex/index buffer slices
  RD_STORE/captures/RD_ID/final.png       the swapchain image at Present
  RD_STORE/captures/RD_ID/rt/             each colour target the kept draws write to, before the first and after
                                          the last of them (.png clamped 8-bit + .exr float), and the depth target
  RD_STORE/blobs/textures/<md5>.dds       native format, all mips and slices (BCn stays compressed) + <md5>.json
  RD_STORE/blobs/meshes/<md5>.bin         the vertex/index range a draw reads, raw (layout in the manifest)
  RD_STORE/blobs/buffers/<md5>.bin        buffer SRVs bound to kept draws
  RD_STORE/blobs/shaders/<checksum>.dxbc  bytecode + .asm (disassembly) + .json (reflection)
  RD_STORE/blobs/postvs/<md5>.bin         post-VS (and GS) output of kept draws, as RenderDoc's mesh viewer has it
  RD_STORE/blobs/dynamic/<md5>.dds        render targets/UAVs/depth a kept draw reads (scene colour, depth, ...), at
                                          the first kept draw that reads each distinct content (+ <md5>.json)
  RD_STORE/captures/RD_ID/contrib/        each kept draw's own change to its colour targets: the before and after
                                          pixels of the changed box (<eid>_<slot>.raw; rdcdigest.py turns them into
                                          a difference EXR + PNG thumbnail)

v2 options (env, set by rdcdigest.py): RD_CONTRIB depth|all|off (depth: kept draws with a depth target, i.e. scene
passes), RD_POSTVS depth|all|off (same rule) and RD_POSTVS_CAP (bytes per draw), RD_DYNAMIC 1|0 and RD_DYNAMIC_CAP
(bytes per snapshot), RD_COUNTERS 1|0 (EventGPUDuration on the replay machine), RD_REHASH 1 (hash a texture SRV
at every use, not only the first, and store each new version for kept draws).

Blobs are content addressed and shared across captures. They are written only for kept draws: blend enabled on
a bound colour target, a marker path matching RD_MARKER_RE, or a pixel shader checksum listed in RD_PSLIST
(one per line); RD_ALL=1 keeps every draw. Hashes are recorded for every draw either way.
RD_RESOURCES_ALL=1 stores the resources of every draw (shaders, textures, buffers, meshes, the render targets it
reads) while contributions, post-VS output and the pass targets stay with the kept draws: the bloom, tonemap and UI
passes' inputs at about the cost of the kept set. RD_BUFFER_CAP (bytes): larger buffer SRVs are hashed only.
Writes RD_STORE/captures/RD_ID/log.txt; the last line is 'done: ...' on success. Exits qrenderdoc when finished."""
import enum as _enum
import hashlib
import json
import os
import re
import struct
import sys
import time
import traceback

import renderdoc as rd

sys.path.insert(0, os.environ.get('RD_TOOLDIR') or os.path.dirname(os.path.abspath(__file__)))
import rawdiff  # noqa: E402

CAPTURE = os.environ['RD_CAPTURE']
STORE = os.environ['RD_STORE']
CID = os.environ['RD_ID']
KEEP_ALL = os.environ.get('RD_ALL', '') == '1'
RESOURCES_ALL = os.environ.get('RD_RESOURCES_ALL', '') == '1'
MARKER_RE = re.compile(os.environ.get('RD_MARKER_RE') or
                       r'(?i)effect|particle|niagara|cascade|emitter|sprite|ribbon|beam|translucen|\bfx|vfx')
PSLIST = set()
if os.environ.get('RD_PSLIST'):
    PSLIST = {ln.strip().lower() for ln in open(os.environ['RD_PSLIST']) if ln.strip()}
STRIPPED = re.compile(r'^cb\d+_v\d+$')      # names 3Dmigoto/RenderDoc invent for stripped reflection
BUFFER_CAP = int(os.environ.get('RD_BUFFER_CAP') or 64 << 20)   # buffer SRVs larger than this are hashed only
CB_MAX = 4096 * 16                          # D3D12 constant buffer without reflection: read at most this
CONTRIB = os.environ.get('RD_CONTRIB') or 'depth'
POSTVS = os.environ.get('RD_POSTVS') or 'depth'
POSTVS_CAP = int(os.environ.get('RD_POSTVS_CAP') or 32 << 20)
DYNAMIC = os.environ.get('RD_DYNAMIC', '1') != '0'
DYNAMIC_CAP = int(os.environ.get('RD_DYNAMIC_CAP') or 64 << 20)
COUNTERS = os.environ.get('RD_COUNTERS', '1') != '0'
REHASH = os.environ.get('RD_REHASH', '') == '1'
TINY = 16                                   # targets of at most this many texels: value after every write
# ResourceUsage names that change a resource's contents (DepthStencilTarget only when the draw writes depth/stencil)
WRITE_USAGE = {'Clear', 'ColorTarget', 'DepthStencilTarget', 'CopyDst', 'ResolveDst', 'GenMips', 'CPUWrite',
               'Discard', 'StreamOut', 'Copy', 'Resolve'}

CAPDIR = os.path.join(STORE, 'captures', CID)
TMP = os.path.join(STORE, 'tmp')
for d in (CAPDIR, os.path.join(CAPDIR, 'rt'), os.path.join(CAPDIR, 'contrib'), TMP,
          os.path.join(STORE, 'blobs', 'textures'), os.path.join(STORE, 'blobs', 'meshes'),
          os.path.join(STORE, 'blobs', 'buffers'), os.path.join(STORE, 'blobs', 'shaders'),
          os.path.join(STORE, 'blobs', 'postvs'), os.path.join(STORE, 'blobs', 'dynamic')):
    os.makedirs(d, exist_ok=True)
FINAL_ONLY = os.environ.get('RD_FINAL_ONLY', '') == '1'
log = open(os.path.join(CAPDIR, 'log_final.txt' if FINAL_ONLY else 'log.txt'), 'w')
STAGES = (('vs', rd.ShaderStage.Vertex), ('hs', rd.ShaderStage.Hull), ('ds', rd.ShaderStage.Domain),
          ('gs', rd.ShaderStage.Geometry), ('ps', rd.ShaderStage.Pixel))
DYNAMIC_TEX = (rd.TextureCategory.ColorTarget | rd.TextureCategory.DepthTarget | rd.TextureCategory.ShaderReadWrite |
               rd.TextureCategory.SwapBuffer)
STATS = {'blobs_new': {}, 'blobs_reused': {}, 'bytes_new': {}}
BLOB_EXTS = ('.dds', '.bin', '.dxbc')
CONVERTED = {'.dds': ('.exr', '.png')}     # rdcdigest.py may turn blobs/dynamic/<md5>.dds into <md5>.exr/.png


def say(*a):
    log.write(' '.join(str(x) for x in a) + '\n')
    log.flush()


def rid(x):
    return str(x).replace('ResourceId::', '')


def enum(x):
    return str(x).split('.')[-1]


def null(x):
    return x == rd.ResourceId.Null()


def md5(b):
    return hashlib.md5(b).hexdigest()


def plain(o, depth=0):
    """A RenderDoc struct as JSON-able data: public non-callable attributes, enums as names, ids as numbers."""
    if isinstance(o, _enum.Enum):            # before int: RenderDoc enums are IntEnum/IntFlag
        return enum(o)
    if isinstance(o, (bool, int, float, str)) or o is None:
        return o
    if isinstance(o, rd.ResourceId):
        return rid(o)
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    if isinstance(o, (list, tuple)) or (hasattr(o, '__len__') and hasattr(o, '__getitem__')):
        try:
            return [plain(x, depth + 1) for x in o]
        except TypeError:
            pass
    if depth > 6:
        return str(o)
    out = {}
    for n in dir(o):
        if n.startswith('_') or n in ('this', 'thisown', 'acquire', 'append', 'disown', 'next', 'own'):
            continue
        v = getattr(o, n)
        if callable(v) and not isinstance(v, rd.ResourceId):
            continue
        out[n] = plain(v, depth + 1)
    return out


SEEN = set()


def put_blob(kind, key, data, ext, sidecar=None):
    """Write blobs/<kind>/<key><ext> unless it exists. STATS count each blob once per capture: 'new' when this
    capture wrote it, 'reused' when it was already in the store (from another capture)."""
    if (kind, key, ext) in SEEN:
        return False
    SEEN.add((kind, key, ext))
    path = os.path.join(STORE, 'blobs', kind, key + ext)
    if os.path.exists(path) or any(os.path.exists(path[:-len(ext)] + x) for x in CONVERTED.get(ext, ())):
        if ext in BLOB_EXTS:
            STATS['blobs_reused'][kind] = STATS['blobs_reused'].get(kind, 0) + 1
        return False
    tmp = os.path.join(TMP, '%d_%s%s' % (os.getpid(), key, ext))
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    if sidecar is not None:
        tmp = os.path.join(TMP, '%d_%s.json' % (os.getpid(), key))
        with open(tmp, 'w') as f:
            json.dump(sidecar, f, indent=1)
        os.replace(tmp, os.path.join(STORE, 'blobs', kind, key + '.json'))
    if ext in BLOB_EXTS:
        STATS['blobs_new'][kind] = STATS['blobs_new'].get(kind, 0) + 1
    STATS['bytes_new'][kind] = STATS['bytes_new'].get(kind, 0) + len(data)
    return True


def presented_buffer(ctrl, D, present, swap):
    """The swap buffer a Present action shows, and how it was found. A flip-model swap chain (D3D12, DXGI flip) has
    two or three buffers and presents a different one each frame: OT0's captures present ResourceId 380 of 379-381,
    and saving swap[0] gave a stale image (max difference 255, up to 63 % of pixels off by more than 2 levels;
    out/edits/rdc-digest-finalframe-ag-20261019). In order: the action's copy destination, the buffer its name gives
    ('Present(ResourceId::380)'), the swap buffer written last before the Present, the first swap buffer. The first two
    only count when the frame wrote that buffer before the Present: a D3D11 flip-model chain (Unity, WotR) draws the
    frame into one buffer (457) while its Present names the other (458) as copy destination, which saves black."""
    ids = {rid(t.resourceId): t.resourceId for t in swap}
    written = {}
    for t in swap:
        uses = [u.eventId for u in ctrl.GetUsage(t.resourceId) if u.eventId < present.eventId
                and enum(u.usage) in WRITE_USAGE]
        if uses:
            written[rid(t.resourceId)] = max(uses)
    dst = getattr(present, 'copyDestination', None)
    if dst is not None and rid(dst) in ids and (rid(dst) in written or not written):
        return ids[rid(dst)], "the Present action's copy destination"
    m = re.search(r'ResourceId::(\d+)', present.GetName(D.sdf) or '')
    if m and m.group(1) in ids and (m.group(1) in written or not written):
        return ids[m.group(1)], 'named by the Present action'
    if written:
        last = max(written, key=written.get)
        return ids[last], 'the swap buffer written last before the Present (eid %d)' % written[last]
    return swap[0].resourceId, 'the first swap buffer (no other evidence)'


def save_final(ctrl, D, flat, rows):
    """final.png: the swap buffer shown at the frame's last Present (presented_buffer), or the output of the last
    draw with a target when the capture has no Present or swap buffer."""
    present = [a for a in flat if a.flags & rd.ActionFlags.Present]
    swap = [t for t in ctrl.GetTextures() if t.creationFlags & rd.TextureCategory.SwapBuffer]
    try:
        if present and swap:
            eid = present[-1].eventId
            ctrl.SetFrameEvent(eid, False)
            res, how = presented_buffer(ctrl, D, present[-1], swap)
            note = 'swapchain at Present (%s; %d swap buffers)' % (how, len(swap))
        else:
            last = [r for r in rows if r.get('targets')][-1]
            res = next(x for x in ctrl.GetTextures() if rid(x.resourceId) == last['targets'][0]['id']).resourceId
            eid = last['eid']
            ctrl.SetFrameEvent(eid, False)
            note = 'output of the last draw'
        ts = rd.TextureSave()
        ts.resourceId = res
        ts.destType = rd.FileType.PNG
        ts.alpha = rd.AlphaMapping.Discard
        out = os.path.join(CAPDIR, 'final.png')
        if os.path.exists(out):
            os.remove(out)
        r = ctrl.SaveTexture(ts, out)
        if not os.path.exists(out):
            raise RuntimeError('SaveTexture final.png at %d: %s' % (eid, r))
        fi = D.tex.get(rid(res))
        ent = {'eid': eid, 'id': rid(res), 'format': fi.format.Name() if fi else None,
               'size': [fi.width, fi.height] if fi else None, 'png': 'final.png', 'note': note}
        # final.dds: the same buffer in its own format (the swap chains are R10G10B10A2: the PNG rounds to 8 bits)
        ts.destType = rd.FileType.DDS
        ts.alpha = rd.AlphaMapping.Preserve
        ts.mip = 0
        dds = os.path.join(CAPDIR, 'final.dds')
        if os.path.exists(dds):
            os.remove(dds)
        r = ctrl.SaveTexture(ts, dds)
        if os.path.exists(dds):
            ent['dds'] = 'final.dds'
        else:
            ent['dds_error'] = str(r)
        return ent
    except Exception:
        return {'error': traceback.format_exc().splitlines()[-1]}


def final_only():
    """RD_FINAL_ONLY=1: redo final.png and the manifest's 'final' of a digested capture, nothing else."""
    t0 = time.time()
    mpath = os.path.join(CAPDIR, 'manifest.json')
    man = json.load(open(mpath))
    rd.InitialiseReplay(rd.GlobalEnvironment(), [])
    cap = rd.OpenCaptureFile()
    if cap.OpenFile(CAPTURE, '', None) != rd.ResultCode.Succeeded:
        raise RuntimeError('open failed')
    st, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
    if st != rd.ResultCode.Succeeded:
        raise RuntimeError('replay failed: %s' % st)
    D = Digest(ctrl)
    flat = []

    def walk(actions):
        for a in actions:
            flat.append(a)
            if len(a.children):
                walk(a.children)

    walk(ctrl.GetRootActions())
    old = man.get('final') or {}
    final = save_final(ctrl, D, flat, man.get('draws') or [])
    if 'error' in final:
        raise RuntimeError(final['error'])
    final['redone'] = {'previous_id': old.get('id'), 'previous_note': old.get('note'),
                       'at': time.strftime('%Y-%m-%d %H:%M:%S')}
    man['final'] = final
    with open(mpath + '.tmp', 'w') as f:
        json.dump(man, f, separators=(',', ':'))
    os.replace(mpath + '.tmp', mpath)
    say('done: final %s (was %s), %s, %.1f s' % (final['id'], old.get('id'), final['note'], time.time() - t0))
    ctrl.Shutdown()
    cap.Shutdown()


class Digest:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.sdf = ctrl.GetStructuredFile()
        self.d3d12 = ctrl.GetAPIProperties().pipelineType == rd.GraphicsAPI.D3D12
        self.tex = {rid(t.resourceId): t for t in ctrl.GetTextures()}
        self.buf = {rid(b.resourceId): b for b in ctrl.GetBuffers()}
        self.names = {rid(r.resourceId): r.name for r in ctrl.GetResources()}
        self.texhash = {}                   # resource id -> (md5, dds bytes or None)
        self.shaders = {}                   # resource id -> {checksum, ...}
        self.tables = {'blend': {}, 'depth': {}, 'raster': {}, 'sampler': {}, 'layout': {}, 'cbdata': {}, 'cbvars': {},
                       'postvs_layout': {}}
        self.cb_decoded = set()
        self.kept_tex, self.kept_mesh, self.kept_buf, self.kept_shader = set(), set(), set(), set()
        self.kept_postvs, self.kept_dynamic = set(), set()
        self.usages = {}                    # resource id -> [(eid, usage name)]
        self.dsv_ro = {}                    # eid -> True when the draw binds depth but writes neither depth nor stencil
        self.snaps = {}                     # (resource id, last write eid) -> snapshot entry
        self.raw2dds = {}                   # md5 of the raw subresources -> md5 of the DDS blob
        self.dynamic = {}                   # dynamic blob md5 -> what it is (manifest 'dynamic')
        self.rtcache = {}                   # (target id, mip, slice) -> (eid, bytes after that eid)
        self.texfirst = {}                  # static texture id -> eid of its first hash
        self.prev_eid = {}                  # eid -> eid of the action before it
        self.cur = None                     # the event the replay is at
        self.phase = {'contrib_s': 0.0, 'postvs_s': 0.0, 'dynamic_s': 0.0, 'rehash_s': 0.0}

    def seek(self, eid):
        if self.cur != eid:
            self.ctrl.SetFrameEvent(eid, False)
            self.cur = eid

    # ---- resource usage: which events write a resource ----------------------------------------------------------
    def usage(self, k, res):
        if k not in self.usages:
            self.usages[k] = sorted((u.eventId, enum(u.usage)) for u in self.ctrl.GetUsage(res))
        return self.usages[k]

    def is_write(self, eid, name):
        if name.endswith('RWResource'):
            return True
        if name == 'DepthStencilTarget':
            return not self.dsv_ro.get(eid, False)
        return name in WRITE_USAGE

    def last_write(self, k, res, eid):
        """The last event before eid that may change resource k (None: not written earlier in the frame)."""
        w = None
        for e, name in self.usage(k, res):
            if e >= eid:
                break
            if self.is_write(e, name):
                w = e
        return w

    def writes_between(self, k, res, lo, hi):
        return [e for e, name in self.usage(k, res) if lo < e < hi and self.is_write(e, name)]

    def raw_subresources(self, res, t, depth_plane=False):
        """md5 of every mip and slice of a texture as GetTextureData returns them (cheaper than a DDS save).
        depth_plane: for a depth-stencil format, hash the depth values only (stencil-only changes keep the hash)."""
        h = hashlib.md5()
        n = 0
        ds = depth_plane and t.format.type in (rd.ResourceFormatType.D32S8, rd.ResourceFormatType.D24S8)
        for sl in range(max(1, t.arraysize)):
            for m in range(max(1, t.mips)):
                d = bytes(self.ctrl.GetTextureData(res, rd.Subresource(m, sl, 0)))
                n += len(d)
                px = max(1, t.width >> m) * max(1, t.height >> m) * max(1, t.depth >> m)
                if ds and len(d) == 8 * px:              # D32 float + S8 + 24 unused bits
                    d = memoryview(d).cast('I')[::2].tobytes()
                elif ds and len(d) == 4 * px:            # D24 in the low bits + S8 in the high byte
                    d = d[0::4] + d[1::4] + d[2::4]
                h.update(d)
        # the format and shape are part of the key: equal bytes in another format make another DDS
        h.update(('|%s|%d|%d|%d|%d|%d' % (t.format.Name(), t.width, t.height, t.depth, t.mips, t.arraysize)).encode())
        return ('z' if ds else '') + h.hexdigest(), n

    def save_dds(self, res, tag):
        ts = rd.TextureSave()
        ts.resourceId = res
        ts.destType = rd.FileType.DDS
        ts.mip = -1
        ts.slice.sliceIndex = -1
        ts.alpha = rd.AlphaMapping.Preserve
        tmp = os.path.join(TMP, '%d_%s.dds' % (os.getpid(), tag))
        self.ctrl.SaveTexture(ts, tmp)
        data = None
        if os.path.exists(tmp):
            data = open(tmp, 'rb').read()
            os.remove(tmp)
        return data

    # ---- dynamic textures (written during the frame) --------------------------------------------------------
    def snapshot(self, res, t, e, eid, keep):
        """A render target/UAV/depth texture a draw reads, as it is at that draw: a content hash for every draw
        (raw subresources, cheap), and for kept draws the DDS blob, once per distinct content."""
        k = rid(res)
        w = self.last_write(k, res, eid)
        e['written_at'] = w
        key = (k, w)
        hit = self.snaps.get(key)
        if hit is not None and (hit.get('stored') or not keep or 'snapshot' in hit):
            e.update({x: hit[x] for x in ('hash', 'stored', 'snapshot', 'raw_md5') if x in hit})
            if keep and hit.get('stored'):
                self.dynamic[hit['hash']]['kept_readers'] += 1
            return
        t0 = time.time()
        ent = {}
        raw = ''
        if t.msSamp > 1:
            ent['snapshot'] = 'msaa: not stored'
        else:
            self.seek(eid)
            raw, nbytes = self.raw_subresources(res, t, depth_plane=True)
            ent['raw_md5'] = raw
            if keep and nbytes > DYNAMIC_CAP:
                ent['snapshot'] = 'larger than RD_DYNAMIC_CAP (%d bytes): raw hash only' % nbytes
            elif keep:
                h = self.raw2dds.get(raw)
                if h is None:
                    data = self.save_dds(res, 'dyn_' + k)
                    if data:
                        h = md5(data)
                        self.raw2dds[raw] = h
                        meta = self.tex_meta(t)
                        meta.update({'resource': self.names.get(k, ''), 'id': k, 'capture': CID, 'eid': eid,
                                     'written_at': w, 'raw_md5': raw})
                        put_blob('dynamic', h, data, '.dds', meta)
                if h:
                    ent.update({'hash': h, 'stored': True})
                    self.kept_dynamic.add(h)
                    d = self.dynamic.setdefault(h, {'id': k, 'name': self.names.get(k, ''), 'format': t.format.Name(),
                                                    'size': [t.width, t.height, t.depth], 'mips': t.mips,
                                                    'slices': t.arraysize, 'first_eid': eid, 'written_at': w,
                                                    'raw_md5': raw, 'kept_readers': 0})
                    d['kept_readers'] += 1
                    if t.width * t.height * max(1, t.depth) <= TINY:
                        d['texel_hex'] = bytes(self.ctrl.GetTextureData(res, rd.Subresource(0, 0, 0))).hex()
        if ent.get('hash') and raw.startswith('z') and self.dynamic.get(ent['hash'], {}).get('raw_md5') != raw:
            ent['note'] = 'depth plane as in the snapshot; stencil may differ'
        if ent.get('hash') in self.dynamic and ent.get('raw_md5', '').startswith('z'):
            self.dynamic[ent['hash']]['keyed_by'] = 'depth plane (stencil-only changes reuse this snapshot)'
        self.snaps[key] = ent
        e.update({x: ent[x] for x in ('hash', 'stored', 'snapshot', 'raw_md5') if x in ent})
        self.phase['dynamic_s'] += time.time() - t0

    def rehash(self, res, t, eid, keep):
        """--rehash: the texture's content at this use, as the md5 of its DDS blob (a DDS is saved only for content
        not seen before, or not yet stored when a kept draw needs it)."""
        t0 = time.time()
        self.seek(eid)
        raw, _ = self.raw_subresources(res, t)
        h = self.raw2dds.get(raw)
        data = None
        if h is None or (keep and not os.path.exists(os.path.join(STORE, 'blobs', 'textures', h + '.dds'))
                         and h not in self.kept_tex):
            data = self.save_dds(res, 'rh_' + rid(res))
            h = md5(data) if data else None
            self.raw2dds[raw] = h
        self.phase['rehash_s'] += time.time() - t0
        return h, data

    # ---- shared tables -------------------------------------------------------------------------------------
    def table(self, kind, value):
        key = md5(json.dumps(value, sort_keys=True).encode())[:12]
        self.tables[kind].setdefault(key, value)
        return key

    # ---- shaders --------------------------------------------------------------------------------------------
    def shader(self, pipe, stage, keep):
        sid = pipe.GetShader(stage)
        if null(sid):
            return None
        k = rid(sid)
        info = self.shaders.get(k)
        if info is None:
            refl = pipe.GetShaderReflection(stage)
            raw = bytes(refl.rawBytes) if refl is not None else b''
            chk = raw[4:20].hex() if raw[:4] == b'DXBC' else md5(raw)
            info = {'id': k, 'checksum': chk, 'md5': md5(raw), 'entry': pipe.GetShaderEntryPoint(stage),
                    'refl': refl, 'raw': raw, 'stored': False}
            self.shaders[k] = info
        if keep and not info['stored']:
            self.store_shader(pipe, info)
        return info

    def store_shader(self, pipe, info):
        refl = info['refl']
        if not os.path.exists(os.path.join(STORE, 'blobs', 'shaders', info['checksum'] + '.dxbc')):
            try:  # .dxbc is written last: its presence means the set is complete
                asm = self.ctrl.DisassembleShader(pipe.GetGraphicsPipelineObject(), refl, '')
            except Exception as ex:          # noqa: BLE001
                asm = 'disassembly failed: %r' % ex
            put_blob('shaders', info['checksum'], asm.encode('utf-8'), '.asm')
            put_blob('shaders', info['checksum'], json.dumps(self.reflection(refl), indent=1).encode(), '.json')
            put_blob('shaders', info['checksum'], info['raw'], '.dxbc')
        else:
            put_blob('shaders', info['checksum'], b'', '.dxbc')      # counts the reuse
        info['stored'] = True
        self.kept_shader.add(info['checksum'])

    def reflection_block(self, c):
        return {'name': c.name, 'register': c.fixedBindNumber, 'size': c.byteSize,
                'variables': [self.var(v) for v in c.variables]}

    def var(self, v):
        t = v.type
        r = {'name': v.name, 'offset': v.byteOffset, 'type': enum(t.baseType), 'rows': t.rows, 'cols': t.columns,
             'elements': t.elements, 'rowMajor': 'RowMajor' in str(t.flags)}
        if t.members:
            r['members'] = [self.var(m) for m in t.members]
        return r

    def reflection(self, refl):
        sig = lambda s: [{'semantic': x.semanticName, 'index': x.semanticIndex, 'reg': x.regIndex,  # noqa: E731
                          'comp': enum(x.varType), 'count': x.compCount, 'system': enum(x.systemValue)} for x in s]
        return {'stage': enum(refl.stage), 'entry': refl.entryPoint,
                'constantBlocks': [self.reflection_block(c) for c in refl.constantBlocks],
                'readOnly': [{'name': r.name, 'register': r.fixedBindNumber, 'type': enum(r.textureType),
                              'isTexture': r.isTexture} for r in refl.readOnlyResources],
                'readWrite': [{'name': r.name, 'register': r.fixedBindNumber, 'type': enum(r.textureType)}
                              for r in refl.readWriteResources],
                'samplers': [{'name': s.name, 'register': s.fixedBindNumber} for s in refl.samplers],
                'inputs': sig(refl.inputSignature), 'outputs': sig(refl.outputSignature)}

    # ---- textures -------------------------------------------------------------------------------------------
    def texture_dds(self, res):
        k = rid(res)
        if k in self.texhash:
            return self.texhash[k]
        ts = rd.TextureSave()
        ts.resourceId = res
        ts.destType = rd.FileType.DDS
        ts.mip = -1
        ts.slice.sliceIndex = -1
        ts.alpha = rd.AlphaMapping.Preserve
        tmp = os.path.join(TMP, '%d_tex_%s.dds' % (os.getpid(), k))
        ok = self.ctrl.SaveTexture(ts, tmp)
        data = None
        if os.path.exists(tmp):
            data = open(tmp, 'rb').read()
            os.remove(tmp)
        h = md5(data) if data else None
        if not data:
            say('texture %s: DDS save failed (%s)' % (k, ok))
        self.texhash[k] = (h, data)
        return self.texhash[k]

    def tex_meta(self, t):
        return {'format': t.format.Name(), 'width': t.width, 'height': t.height, 'depth': t.depth, 'mips': t.mips,
                'arraysize': t.arraysize, 'type': enum(t.type), 'cubemap': bool(t.cubemap), 'msaa': t.msSamp}

    # ---- buffers --------------------------------------------------------------------------------------------
    def buffer_bytes(self, res, off, size):
        b = self.buf.get(rid(res))
        if b is None:
            return b''
        end = b.length if size in (0, 0xFFFFFFFFFFFFFFFF) or off + size > b.length else off + size
        if end <= off:
            return b''
        return bytes(self.ctrl.GetBufferData(res, off, end - off))

    # ---- one action -----------------------------------------------------------------------------------------
    def keep_reason(self, pipe, path, row):
        why = []
        if KEEP_ALL:
            why.append('all')
        if any(b['enabled'] for b in row.get('blend_summary', [])):
            why.append('blend')
        if path and MARKER_RE.search('/'.join(path)):
            why.append('marker')
        ps = row['shaders'].get('ps')
        if ps and ps['checksum'] in PSLIST:
            why.append('ps-list')
        return why

    def state(self, pipe, row):
        if pipe.IsCaptureD3D11():
            s = self.ctrl.GetD3D11PipelineState()
            om, rs = s.outputMerger, s.rasterizer.state
        elif pipe.IsCaptureD3D12():
            s = self.ctrl.GetD3D12PipelineState()
            om, rs = s.outputMerger, s.rasterizer.state
        else:
            om = rs = None
        targets = []
        for i, o in enumerate(pipe.GetOutputTargets()):
            if null(o.resource):
                continue
            t = self.tex.get(rid(o.resource))
            targets.append({'slot': i, 'id': rid(o.resource), 'name': self.names.get(rid(o.resource), ''),
                            'format': o.format.Name(), 'size': [t.width, t.height] if t else None})
            if o.firstMip or o.firstSlice:
                targets[-1].update({'mip': o.firstMip, 'slice': o.firstSlice})
        dt = pipe.GetDepthTarget()
        depth_target = None
        if not null(dt.resource):
            t = self.tex.get(rid(dt.resource))
            depth_target = {'id': rid(dt.resource), 'format': dt.format.Name(), 'size': [t.width, t.height] if t else None}
        blends = list(pipe.GetColorBlends())
        summary = []
        for tgt in targets:
            b = blends[tgt['slot']] if tgt['slot'] < len(blends) else blends[0]
            summary.append({'slot': tgt['slot'], 'enabled': bool(b.enabled), 'kind': blend_kind(b),
                            'color': '%s %s %s' % (enum(b.colorBlend.source), enum(b.colorBlend.operation),
                                                   enum(b.colorBlend.destination)),
                            'alpha': '%s %s %s' % (enum(b.alphaBlend.source), enum(b.alphaBlend.operation),
                                                   enum(b.alphaBlend.destination)), 'writeMask': int(b.writeMask)})
        if om is not None:
            bs = plain(om.blendState)
            bs.pop('resourceId', None)
            row['blend'] = self.table('blend', bs)
            dss = plain(om.depthStencilState)
            dss.pop('resourceId', None)
            if hasattr(om, 'depthReadOnly'):
                dss['depthReadOnly'], dss['stencilReadOnly'] = om.depthReadOnly, om.stencilReadOnly
            row['depth'] = self.table('depth', dss)
            writes = (dss.get('depthEnable') and dss.get('depthWrites') and not dss.get('depthReadOnly')) or \
                (dss.get('stencilEnable') and not dss.get('stencilReadOnly'))
            self.dsv_ro[row['eid']] = not writes
            rss = plain(rs)
            rss.pop('resourceId', None)
            row['raster'] = self.table('raster', rss)
        else:
            row['blend'] = self.table('blend', {'blends': [plain(b) for b in blends],
                                                'blendFactor': list(pipe.GetBlendFactor())})
            row['depth'] = self.table('depth', {'test': plain(pipe.GetDepthTestState()),
                                                'stencil': [plain(f) for f in pipe.GetStencilFaces()]})
            row['raster'] = self.table('raster', plain(pipe.GetRasterState()))
        row['blend_summary'] = summary
        row['targets'] = targets
        row['depth_target'] = depth_target
        vp, sc = pipe.GetViewport(0), pipe.GetScissor(0)
        row['viewport'] = [vp.x, vp.y, vp.width, vp.height, vp.minDepth, vp.maxDepth]
        row['scissor'] = [sc.x, sc.y, sc.width, sc.height, bool(sc.enabled)]

    def resources(self, pipe, stage, refl, keep, eid):
        srvs = []
        for u in pipe.GetReadOnlyResources(stage, True):
            d, a = u.descriptor, u.access
            r = refl.readOnlyResources[a.index] if refl is not None and 0 <= a.index < len(refl.readOnlyResources) else None
            e = {'register': r.fixedBindNumber if r else a.index, 'name': r.name if r else '', 'id': rid(d.resource),
                 'view_format': d.format.Name(), 'first_mip': d.firstMip, 'num_mips': d.numMips,
                 'first_slice': d.firstSlice, 'num_slices': d.numSlices}
            if a.arrayElement:
                e['element'] = a.arrayElement
            if null(d.resource):
                srvs.append(e)
                continue
            k = rid(d.resource)
            t = self.tex.get(k)
            if t is not None:
                e.update({'kind': 'texture', 'resource': self.names.get(k, '')})
                e.update(self.tex_meta(t))
                if t.creationFlags & DYNAMIC_TEX or t.msSamp > 1:
                    e['dynamic'] = True       # written during the frame: content depends on the event
                    if DYNAMIC:
                        self.snapshot(d.resource, t, e, eid, keep)
                else:
                    first = self.texfirst.setdefault(k, eid)
                    if REHASH:
                        h, data = self.rehash(d.resource, t, eid, keep)
                    else:
                        h, data = self.texture_dds(d.resource)
                        if first < eid and self.writes_between(k, d.resource, first - 1, eid):
                            e['rewritten'] = True     # written since its first hash: the hash may be stale (--rehash)
                    e['hash'] = h
                    if keep and h:
                        meta = self.tex_meta(t)
                        meta['resource_names'] = [self.names.get(k, '')]
                        if data:
                            put_blob('textures', h, data, '.dds', meta)
                        self.kept_tex.add(h)
                        e['stored'] = True
            elif k in self.buf:
                data = self.buffer_bytes(d.resource, d.byteOffset, d.byteSize) if self.buf[k].length <= BUFFER_CAP else b''
                e.update({'kind': 'buffer', 'resource': self.names.get(k, ''), 'offset': d.byteOffset,
                          'size': len(data), 'stride': d.elementByteSize, 'hash': md5(data) if data else None})
                if keep and data:
                    put_blob('buffers', e['hash'], data, '.bin')
                    self.kept_buf.add(e['hash'])
                    e['stored'] = True
            srvs.append(e)
        samplers = []
        for u in pipe.GetSamplers(stage, True):
            r = refl.samplers[u.access.index] if refl is not None and 0 <= u.access.index < len(refl.samplers) else None
            sd = plain(u.sampler)
            for x in ('object', 'creationTimeConstant', 'srgbBorder', 'seamlessCubemaps'):
                sd.pop(x, None)
            samplers.append({'register': r.fixedBindNumber if r else u.access.index, 'name': r.name if r else '',
                             'state': self.table('sampler', sd)})
        uavs = []
        for u in pipe.GetReadWriteResources(stage, True):
            uavs.append({'index': u.access.index, 'id': rid(u.descriptor.resource),
                         'name': self.names.get(rid(u.descriptor.resource), '')})
        return srvs, samplers, uavs

    def cbuffers(self, pipe, stage, info):
        out = []
        refl = info['refl']
        for u in pipe.GetConstantBlocks(stage, True):
            d, a = u.descriptor, u.access
            blk = refl.constantBlocks[a.index] if refl is not None and 0 <= a.index < len(refl.constantBlocks) else None
            e = {'index': a.index, 'register': blk.fixedBindNumber if blk else a.index, 'name': blk.name if blk else '',
                 'id': rid(d.resource), 'offset': d.byteOffset}
            if null(d.resource):
                out.append(e)
                continue
            size = d.byteSize
            if self.d3d12:                  # a root CBV has no size: RenderDoc gives the rest of the (upload) buffer
                size = min(size, blk.byteSize if blk is not None and blk.byteSize else CB_MAX)
            data = self.buffer_bytes(d.resource, d.byteOffset, size)
            e['size'] = len(data)
            key = md5(data)[:16]
            if key not in self.tables['cbdata']:
                self.tables['cbdata'][key] = data.hex()
            e['data'] = key
            if blk is not None and any(not STRIPPED.match(v.name) for v in blk.variables):
                lay = md5(json.dumps(self.reflection_block(blk), sort_keys=True).encode())[:8]
                vkey = key + '-' + lay
                if vkey not in self.tables['cbvars']:
                    try:
                        vs = self.ctrl.GetCBufferVariableContents(pipe.GetGraphicsPipelineObject(), refl.resourceId,
                                                                  stage, info['entry'], a.index, d.resource,
                                                                  d.byteOffset, len(data))
                        self.tables['cbvars'][vkey] = {v.name: shvar(v) for v in vs}
                    except Exception as ex:  # noqa: BLE001
                        self.tables['cbvars'][vkey] = {'_error': repr(ex)}
                e['vars'] = vkey
            out.append(e)
        return out

    def geometry(self, pipe, a, row, keep):
        """Vertex input layout, and (kept draws) the vertex/index ranges the draw reads as mesh blobs."""
        inputs = list(pipe.GetVertexInputs())
        layout = [{'name': v.name, 'slot': v.vertexBuffer, 'offset': v.byteOffset, 'format': v.format.Name(),
                   'perInstance': bool(v.perInstance), 'rate': v.instanceRate, 'used': bool(v.used)} for v in inputs]
        row['layout'] = self.table('layout', layout) if layout else None
        indexed = bool(a.flags & rd.ActionFlags.Indexed)
        ib = pipe.GetIBuffer()
        lo, hi, idx_info = None, None, None
        if indexed and not null(ib.resourceId) and ib.byteStride in (2, 4):
            start = ib.byteOffset + a.indexOffset * ib.byteStride
            data = self.buffer_bytes(ib.resourceId, start, a.numIndices * ib.byteStride)
            fmt = '<%d%s' % (len(data) // ib.byteStride, 'H' if ib.byteStride == 2 else 'I')
            idx = [i for i in struct.unpack(fmt, data) if i != (0xFFFF if ib.byteStride == 2 else 0xFFFFFFFF)]
            if idx:
                lo, hi = min(idx), max(idx)
            h = md5(data)
            idx_info = {'id': rid(ib.resourceId), 'format': 'R16_UINT' if ib.byteStride == 2 else 'R32_UINT',
                        'first_index': a.indexOffset, 'count': a.numIndices, 'min': lo, 'max': hi, 'hash': h}
            if keep and data:
                put_blob('meshes', h, data, '.bin')
                self.kept_mesh.add(h)
                idx_info['stored'] = True
        row['ib'] = idx_info
        vbs = []
        bound = list(pipe.GetVBuffers())
        for slot in sorted({v['slot'] for v in layout if v['used']}):
            if slot >= len(bound) or null(bound[slot].resourceId):
                continue
            vb = bound[slot]
            attrs = [v for v in layout if v['slot'] == slot]
            per_inst = any(v['perInstance'] for v in attrs)
            stride = vb.byteStride
            if per_inst:
                rate = max(1, max(v['rate'] for v in attrs))
                first, count = a.instanceOffset, (max(1, a.numInstances) + rate - 1) // rate
            elif indexed:
                if lo is None:
                    continue
                first, count = a.baseVertex + lo, hi - lo + 1
            else:
                first, count = a.vertexOffset, a.numIndices
            if stride == 0:                  # one element every vertex/instance reads (bound_stride 0)
                first, count = 0, 1
                stride = max(fmt_size(v['format']) + v['offset'] for v in attrs)
            data = self.buffer_bytes(vb.resourceId, vb.byteOffset + first * stride, count * stride)
            e = {'slot': slot, 'id': rid(vb.resourceId), 'stride': stride, 'bound_stride': vb.byteStride,
                 'first_element': first, 'count': count,
                 'per_instance': per_inst, 'bytes': len(data), 'hash': md5(data) if data else None}
            if keep and data:
                put_blob('meshes', e['hash'], data, '.bin')
                self.kept_mesh.add(e['hash'])
                e['stored'] = True
            vbs.append(e)
        row['vb'] = vbs
        row['topology'] = enum(pipe.GetPrimitiveTopology())

    # ---- post-transform geometry ----------------------------------------------------------------------------
    def postvs_layout(self, refl):
        """RenderDoc's D3D11 stream-out layout: every output-signature element as 32-bit components, in signature
        order, with the position element moved to the front."""
        if refl is None:
            return None
        sig = list(refl.outputSignature)
        pos = [x for x in sig if x.systemValue == rd.ShaderBuiltin.Position]
        out, off = [], 0
        for x in pos[:1] + [x for x in sig if not (pos and x is pos[0])]:
            n = 4 if x.systemValue == rd.ShaderBuiltin.Position else x.compCount
            out.append({'name': x.semanticName, 'index': x.semanticIndex, 'system': enum(x.systemValue),
                        'type': enum(x.varType), 'offset': off, 'comps': n})
            off += 4 * n
        return out

    def postvs(self, a, row):
        t0 = time.time()
        out = {}
        stages = [('vs', rd.MeshDataStage.VSOut, 'vs')]
        if 'gs' in row['shaders']:
            stages.append(('gs', rd.MeshDataStage.GSOut, 'gs'))
        elif 'ds' in row['shaders']:
            stages[0] = ('vs', rd.MeshDataStage.VSOut, 'ds')      # with tessellation VSOut is the domain output
        self.seek(a.eventId)
        n_inst = max(1, a.numInstances)
        for name, ms, shkey in stages:
            mf = self.ctrl.GetPostVSData(0, 0, ms)
            if null(mf.vertexResourceId):
                out[name] = {'status': mf.status or 'no data'}
                continue
            stride = mf.vertexByteStride
            ent = {'stride': stride, 'instances': n_inst, 'num_indices': mf.numIndices, 'topology': enum(mf.topology),
                   'near': mf.nearPlane, 'far': mf.farPlane, 'unproject': bool(mf.unproject),
                   'base_vertex': mf.baseVertex}
            if mf.status:
                ent['status'] = mf.status
            idx = b''
            if not null(mf.indexResourceId) and mf.indexByteStride in (2, 4):
                idx = bytes(self.ctrl.GetBufferData(mf.indexResourceId, mf.indexByteOffset,
                                                    mf.numIndices * mf.indexByteStride))
                code = 'H' if mf.indexByteStride == 2 else 'I'
                vals = struct.unpack('<%d%s' % (len(idx) // mf.indexByteStride, code), idx)
                restart = 0xFFFF if mf.indexByteStride == 2 else 0xFFFFFFFF
                vals = [v for v in vals if v != restart]
                nverts = (max(vals) + 1 + mf.baseVertex) if vals else 0
                ent.update({'index_stride': mf.indexByteStride})
            else:
                nverts = mf.numIndices
            inst_stride = 0
            if n_inst > 1:
                m1 = self.ctrl.GetPostVSData(1, 0, ms)
                inst_stride = m1.vertexByteOffset - mf.vertexByteOffset
                if m1.indexByteOffset != mf.indexByteOffset:
                    ent['index_inst_stride'] = m1.indexByteOffset - mf.indexByteOffset
            if inst_stride > 0:
                nverts = inst_stride // stride
                total, unit = n_inst * inst_stride, inst_stride
            else:
                total, unit = nverts * stride, stride
            ent.update({'vertices': nverts, 'inst_stride': inst_stride})
            if total > POSTVS_CAP:
                keep_units = max(1, POSTVS_CAP // unit)
                ent['truncated'] = {'instances' if inst_stride else 'vertices': keep_units}
                total = keep_units * unit
            data = bytes(self.ctrl.GetBufferData(mf.vertexResourceId, mf.vertexByteOffset, total)) if total else b''
            if data:
                ent['vertex'] = md5(data)
                ent['bytes'] = len(data)
                put_blob('postvs', ent['vertex'], data, '.bin')
                self.kept_postvs.add(ent['vertex'])
            if idx:
                ent['index'] = md5(idx)
                put_blob('postvs', ent['index'], idx, '.bin')
                self.kept_postvs.add(ent['index'])
            info = self.shaders.get(row['shaders'].get(shkey, {}).get('id'))
            lay = self.postvs_layout(info['refl'] if info else None)
            if lay is not None:
                ent['layout'] = self.table('postvs_layout', lay)
                ent['layout_ok'] = sum(4 * x['comps'] for x in lay) == stride
            out[name] = ent
        row['postvs'] = out
        self.phase['postvs_s'] += time.time() - t0

    # ---- a draw's own change to its colour targets --------------------------------------------------------------
    def contrib(self, a, row):
        t0 = time.time()
        eid = a.eventId
        res = {x['id']: self.tex[x['id']].resourceId for x in row.get('targets', []) if x['id'] in self.tex}
        afters = []
        self.seek(eid)
        for tg in row.get('targets', []):
            t = self.tex.get(tg['id'])
            if t is None or tg['id'] not in res:
                continue
            if t.msSamp > 1:
                afters.append((tg, t, None))
                continue
            mip, sl = tg.get('mip', 0), tg.get('slice', 0)
            afters.append((tg, t, bytes(self.ctrl.GetTextureData(res[tg['id']], rd.Subresource(mip, sl, 0)))))
        out = []
        for tg, t, after in afters:
            ent = {'slot': tg['slot'], 'id': tg['id'], 'format': tg['format']}
            if after is None:
                ent['skipped'] = 'msaa'
                out.append(ent)
                continue
            mip, sl = tg.get('mip', 0), tg.get('slice', 0)
            w, h = max(1, t.width >> mip), max(1, t.height >> mip)
            ck = (tg['id'], mip, sl)
            lw = self.last_write(tg['id'], res[tg['id']], eid)
            cached = self.rtcache.get(ck)
            if cached is not None and lw is not None and lw <= cached[0] < eid:
                before, bev = cached[1], cached[0]
            else:
                bev = lw if lw is not None else self.prev_eid.get(eid, eid)
                self.seek(bev)
                before = bytes(self.ctrl.GetTextureData(res[tg['id']], rd.Subresource(mip, sl, 0)))
            self.rtcache[ck] = (eid, after)
            bpp = len(after) // (w * h) if w * h else 0
            ent.update({'before_eid': bev, 'size': [w, h], 'bpp': bpp})
            box = rawdiff.changed_box(before, after, w, h, bpp) if bpp and len(before) == len(after) else None
            if box is None:
                ent['changed'] = False
            else:
                fn = '%d_%d.raw' % (eid, tg['slot'])
                with open(os.path.join(CAPDIR, 'contrib', fn), 'wb') as f:
                    f.write(rawdiff.crop(before, w, bpp, box))
                    f.write(rawdiff.crop(after, w, bpp, box))
                ent.update({'changed': True, 'box': list(box), 'raw': fn})
            out.append(ent)
        row['contrib'] = out
        self.phase['contrib_s'] += time.time() - t0

    def action(self, a, path, kind):
        self.ctrl.SetFrameEvent(a.eventId, False)
        self.cur = a.eventId
        pipe = self.ctrl.GetPipelineState()
        row = {'eid': a.eventId, 'kind': kind, 'name': a.GetName(self.sdf), 'path': '/'.join(path)}
        if kind == 'draw':
            row.update({'indices': a.numIndices, 'instances': a.numInstances, 'indexed': bool(a.flags & rd.ActionFlags.Indexed),
                        'base_vertex': a.baseVertex, 'vertex_offset': a.vertexOffset, 'index_offset': a.indexOffset,
                        'instance_offset': a.instanceOffset})
            stages = STAGES
        else:
            row.update({'dispatch': list(a.dispatchDimension), 'threads': list(a.dispatchThreadsDimension)})
            stages = (('cs', rd.ShaderStage.Compute),)
        row['shaders'] = {}
        infos = {}
        for key, st in stages:
            info = self.shader(pipe, st, False)
            if info is not None:
                infos[key] = (st, info)
                row['shaders'][key] = {'checksum': info['checksum'], 'md5': info['md5'], 'id': info['id'],
                                       'entry': info['entry']}
        if kind == 'draw':
            self.state(pipe, row)
        why = self.keep_reason(pipe, path, row)
        keep = bool(why)
        row['keep'] = why
        res_keep = keep or RESOURCES_ALL     # resources stored; contributions and post-VS stay with `keep`
        if res_keep and not keep:
            row['resources_kept'] = True
        for key, (st, info) in infos.items():
            if res_keep:
                self.shader(pipe, st, True)
            srvs, samplers, uavs = self.resources(pipe, st, info['refl'], res_keep, a.eventId)
            ent = {'cbuffers': self.cbuffers(pipe, st, info), 'srvs': srvs}
            if samplers:
                ent['samplers'] = samplers
            if uavs:
                ent['uavs'] = uavs
            row['shaders'][key].update(ent)
        if kind == 'draw':
            self.geometry(pipe, a, row, res_keep)
            if keep and (POSTVS == 'all' or (POSTVS == 'depth' and row.get('depth_target'))):
                try:
                    self.postvs(a, row)
                except Exception:            # noqa: BLE001
                    row['postvs'] = {'error': traceback.format_exc().splitlines()[-1]}
            if keep and row.get('targets') and (CONTRIB == 'all' or (CONTRIB == 'depth' and row.get('depth_target'))):
                try:
                    self.contrib(a, row)
                except Exception:            # noqa: BLE001
                    row['contrib'] = [{'error': traceback.format_exc().splitlines()[-1]}]
        return row


def blend_kind(b):
    if not b.enabled:
        return 'opaque'
    s, d, op = enum(b.colorBlend.source), enum(b.colorBlend.destination), enum(b.colorBlend.operation)
    if op in ('ReversedSubtract', 'Subtract'):
        return 'subtract'
    if d == 'One':
        return 'additive'
    if s == 'SrcAlpha' and d == 'InvSrcAlpha':
        return 'alpha'
    if s == 'One' and d == 'InvSrcAlpha':
        return 'premultiplied'
    if (s == 'DstColor' and d == 'Zero') or (s == 'Zero' and d == 'SrcColor'):
        return 'modulate'
    return 'other'


def shvar(v):
    if len(v.members):
        return {m.name: shvar(m) for m in v.members}
    n = max(1, v.rows) * max(1, v.columns)
    t = enum(v.type)
    if t in ('Float', 'Half'):
        vals = list(v.value.f32v)[:n]
    elif t == 'Double':
        vals = list(v.value.f64v)[:n]
    elif t in ('SInt', 'SShort', 'SByte', 'SLong'):
        vals = list(v.value.s32v)[:n]
    elif t == 'Bool':
        vals = [bool(x) for x in list(v.value.u32v)[:n]]
    else:
        vals = list(v.value.u32v)[:n]
    if v.rows > 1:
        return [vals[r * v.columns:(r + 1) * v.columns] for r in range(v.rows)]
    return vals if n > 1 else vals[0]


def fmt_size(name):
    m = re.findall(r'(\d+)', name.split('_')[0])
    return sum(int(x) for x in m) // 8 if m else 16


def save_target(ctrl, res, eid, base, png=True):
    """The texture at an event: <base>.png (8-bit, clamped; colour only) and <base>.dds (native format, exact bits;
    rdcdigest.py turns it into a ZIP-compressed .exr after qrenderdoc exits)."""
    ctrl.SetFrameEvent(eid, False)
    out = {}
    for ft, ext in ((rd.FileType.PNG, '.png'), (rd.FileType.DDS, '.dds')):
        if ext == '.png' and not png:
            continue
        ts = rd.TextureSave()
        ts.resourceId = res
        ts.destType = ft
        ts.mip = 0
        ts.alpha = rd.AlphaMapping.Preserve if ext == '.dds' else rd.AlphaMapping.Discard
        r = ctrl.SaveTexture(ts, base + ext)
        if os.path.exists(base + ext):
            out[ext[1:]] = os.path.basename(base + ext)
        else:
            out[ext[1:] + '_error'] = str(r)
            say('  SaveTexture %s at %d: %s' % (os.path.basename(base + ext), eid, r))
    return out


def main():
    t0 = time.time()
    rd.InitialiseReplay(rd.GlobalEnvironment(), [])
    cap = rd.OpenCaptureFile()
    if cap.OpenFile(CAPTURE, '', None) != rd.ResultCode.Succeeded:
        raise RuntimeError('open failed')
    st, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
    if st != rd.ResultCode.Succeeded:
        raise RuntimeError('replay failed: %s' % st)
    t_open = time.time() - t0
    props = ctrl.GetAPIProperties()
    D = Digest(ctrl)
    flat, work = [], []

    def walk(actions, path):
        for a in actions:
            name = a.GetName(D.sdf)
            flat.append(a)
            if a.flags & rd.ActionFlags.Drawcall:
                work.append((a, path, 'draw'))
            elif a.flags & rd.ActionFlags.Dispatch:
                work.append((a, path, 'dispatch'))
            if len(a.children):
                walk(a.children, path + [name])

    walk(ctrl.GetRootActions(), [])
    order = sorted(a.eventId for a in flat)
    D.prev_eid = {e: p for p, e in zip(order, order[1:])}
    say('opened in %.1f s: %d actions, %d draws/dispatches' % (t_open, len(flat), len(work)))
    rows = []
    for i, (a, path, kind) in enumerate(work):
        try:
            rows.append(D.action(a, path, kind))
        except Exception:
            say('eid %d failed:\n%s' % (a.eventId, traceback.format_exc()))
            rows.append({'eid': a.eventId, 'kind': kind, 'error': traceback.format_exc().splitlines()[-1]})
        if i % 200 == 0:
            say('  %d/%d  %.1f s' % (i, len(work), time.time() - t0))
    D.rtcache.clear()
    t_draws = time.time() - t0

    # tiny targets (1x1 eye adaptation, average luminance, ...): their value after every write in the frame
    tiny = {}
    t1 = time.time()
    readers = {}
    for r in rows:
        for sh in r.get('shaders', {}).values():
            for sv in sh.get('srvs', []):
                if sv.get('kind') == 'texture' and r.get('keep'):
                    readers.setdefault(sv['id'], []).append(r['eid'])
    for t in ctrl.GetTextures():
        if t.width * t.height * max(1, t.depth) > TINY or t.msSamp > 1 or not (t.creationFlags & DYNAMIC_TEX):
            continue
        k = rid(t.resourceId)
        us = D.usage(k, t.resourceId)
        ws = [e for e, name in us if D.is_write(e, name)]
        if not us:
            continue
        ent = {'name': D.names.get(k, ''), 'format': t.format.Name(), 'size': [t.width, t.height, t.depth],
               'writes': [], 'kept_readers': readers.get(k, [])}
        if not ws:                           # read but not written this frame: its value at the first read
            D.seek(us[0][0])
            ent['first_read'] = {'eid': us[0][0], 'hex': bytes(ctrl.GetTextureData(t.resourceId,
                                                                                    rd.Subresource(0, 0, 0))).hex()}
        for e in (ws if len(ws) <= 64 else ws[:32] + ws[-32:]):
            D.seek(e)
            ent['writes'].append({'eid': e, 'hex': bytes(ctrl.GetTextureData(t.resourceId,
                                                                               rd.Subresource(0, 0, 0))).hex()})
        if len(ws) > 64:
            ent['writes_total'] = len(ws)
        tiny[k] = ent
    t_tiny = time.time() - t1

    # colour targets of the kept draws that are depth tested against the scene (effects, not UI or post passes):
    # before the first and after the last kept draw on each, and the depth target before them (stored once
    # per distinct content: a depth buffer can be cleared between passes)
    eids = [a.eventId for a in flat]
    resobj = {rid(t.resourceId): t.resourceId for t in ctrl.GetTextures()}
    groups = {}
    for r in rows:
        if r.get('keep') and r.get('targets') and r.get('depth_target'):
            groups.setdefault(r['targets'][0]['id'], []).append(r)
    targets, depths = {}, {}
    for tid, rs in sorted(groups.items(), key=lambda kv: kv[1][0]['eid']):
        first, last = rs[0]['eid'], rs[-1]['eid']
        prev = max([e for e in eids if e < first] or [first])
        t = D.tex.get(tid)
        ent = {'name': D.names.get(tid, ''), 'format': t.format.Name() if t else None,
               'size': [t.width, t.height] if t else None, 'kept_draws': len(rs), 'first': first, 'last': last,
               'before_eid': prev, 'after_eid': last,
               'kinds': sorted({x['blend_summary'][0]['kind'] for x in rs if x.get('blend_summary')})}
        D.cur = None
        ent['before'] = save_target(ctrl, resobj[tid], prev, os.path.join(CAPDIR, 'rt', '%s_before' % tid))
        ent['after'] = save_target(ctrl, resobj[tid], last, os.path.join(CAPDIR, 'rt', '%s_after' % tid))
        dt = rs[0]['depth_target']
        base = os.path.join(CAPDIR, 'rt', 'depth_%s_%d' % (dt['id'], prev))
        files = save_target(ctrl, resobj[dt['id']], prev, base, png=False)
        h = md5(open(base + '.dds', 'rb').read()) if 'dds' in files else None
        same = next((k for k, v in depths.items() if h and v['md5'] == h), None)
        if same:                             # unchanged since an earlier snapshot: keep that one
            os.remove(base + '.dds')
            ent['depth'] = same
        else:
            key = '%s@%d' % (dt['id'], prev)
            depths[key] = {'id': dt['id'], 'format': dt['format'], 'eid': prev, 'md5': h, 'files': files}
            ent['depth'] = key
        targets[tid] = ent

    # the presented image
    final = save_final(ctrl, D, flat, rows)

    # GPU time of every event, measured now on this machine's GPU under replay (not the game's own timing).
    # Fetched last: on D3D12 (RenderDoc 1.46) FetchCounters leaves the replay unable to read textures back, so
    # every target dump and final.png after it failed with "Couldn't readback bytes" (OT0, 2026-10-19).
    gpu = {}
    if COUNTERS:
        t1 = time.time()
        try:
            if rd.GPUCounter.EventGPUDuration in ctrl.EnumerateCounters():
                res = ctrl.FetchCounters([rd.GPUCounter.EventGPUDuration])
                dur = {x.eventId: x.value.d for x in res}
                for r in rows:
                    if r['eid'] in dur:
                        r['gpu_us'] = round(dur[r['eid']] * 1e6, 3)
                props_ = ctrl.GetAPIProperties()
                gpu = {'counter': 'EventGPUDuration', 'unit': 'us', 'events': len(dur),
                       'sum_all_us': round(sum(dur.values()) * 1e6, 1),
                       'sum_kept_us': round(sum(r.get('gpu_us', 0) for r in rows if r.get('keep')), 1),
                       'vendor': enum(props_.vendor),
                       'note': 'replay-machine timing: this PC\'s GPU replaying the capture under RenderDoc, '
                               'one event at a time; not the game\'s frame timing'}
            else:
                gpu = {'error': 'EventGPUDuration not offered by this replay'}
        except Exception:                    # noqa: BLE001
            gpu = {'error': traceback.format_exc().splitlines()[-1]}
        gpu['fetch_s'] = round(time.time() - t1, 2)

    st_ = os.stat(CAPTURE)
    manifest = {
        'digest_version': 2, 'id': CID, 'capture': CAPTURE.replace('\\', '/'), 'capture_bytes': st_.st_size,
        'capture_mtime': int(st_.st_mtime), 'api': enum(props.pipelineType), 'keep_all': KEEP_ALL,
        'resources_all': RESOURCES_ALL,
        'marker_re': MARKER_RE.pattern, 'ps_list': os.environ.get('RD_PSLIST', ''), 'ps_list_size': len(PSLIST),
        'options': {'contrib': CONTRIB, 'postvs': POSTVS, 'postvs_cap': POSTVS_CAP, 'dynamic': DYNAMIC,
                    'dynamic_cap': DYNAMIC_CAP, 'counters': COUNTERS, 'rehash': REHASH, 'tiny_texels': TINY,
                    'buffer_cap': BUFFER_CAP},
        'counts': {'actions': len(flat), 'draws': sum(1 for r in rows if r['kind'] == 'draw'),
                   'dispatches': sum(1 for r in rows if r['kind'] == 'dispatch'),
                   'kept': sum(1 for r in rows if r.get('keep')),
                   'resources_kept': sum(1 for r in rows if r.get('resources_kept')), 'errors': sum(1 for r in rows if 'error' in r),
                   'textures_hashed': sum(1 for v in D.texhash.values() if v[0]),
                   'kept_textures': len(D.kept_tex), 'kept_meshes': len(D.kept_mesh),
                   'kept_buffers': len(D.kept_buf), 'kept_shaders': len(D.kept_shader),
                   'kept_postvs': len(D.kept_postvs), 'kept_dynamic': len(D.kept_dynamic),
                   'contrib_changed': sum(1 for r in rows for c in r.get('contrib', []) if c.get('changed')),
                   'contrib_unchanged': sum(1 for r in rows for c in r.get('contrib', []) if c.get('changed') is False),
                   'rewritten_srv_uses': sum(1 for r in rows for sh in r.get('shaders', {}).values()
                                             for sv in sh.get('srvs', []) if sv.get('rewritten'))},
        'blobs': {'textures': sorted(D.kept_tex), 'meshes': sorted(D.kept_mesh), 'buffers': sorted(D.kept_buf),
                  'shaders': sorted(D.kept_shader), 'postvs': sorted(D.kept_postvs),
                  'dynamic': sorted(D.kept_dynamic)},
        'dynamic': D.dynamic, 'tiny': tiny, 'gpu': gpu,
        'shaders': {v['checksum']: {'id': k, 'md5': v['md5'], 'entry': v['entry'], 'stage': enum(v['refl'].stage)
                                    if v['refl'] is not None else None, 'stored': v['stored']}
                    for k, v in D.shaders.items()},
        'tables': D.tables, 'targets': targets, 'depth': depths, 'final': final, 'draws': rows,
        'timing': {'open_s': round(t_open, 2), 'draws_s': round(t_draws - t_open, 2),
                   'tiny_s': round(t_tiny, 2), **{k: round(v, 2) for k, v in D.phase.items()},
                   'total_s': round(time.time() - t0, 2)},
        'blob_stats': STATS,
    }
    tmp = os.path.join(CAPDIR, 'manifest.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(manifest, f, separators=(',', ':'))
    os.replace(tmp, os.path.join(CAPDIR, 'manifest.json'))
    say('done: %d draws, %d dispatches, %d kept, %d errors, %.1f s' % (
        manifest['counts']['draws'], manifest['counts']['dispatches'], manifest['counts']['kept'],
        manifest['counts']['errors'], time.time() - t0))
    ctrl.Shutdown()
    cap.Shutdown()


try:
    final_only() if FINAL_ONLY else main()
except Exception:
    say(traceback.format_exc())
log.close()
os._exit(0)
