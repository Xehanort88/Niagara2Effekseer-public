"""rdcdigest: open each RenderDoc capture once, keep what effect work needs, and query it without replaying.

  python tools/rdcdigest/rdcdigest.py digest <capture.rdc | folder>... [--store out/rdcdigest] [--all]
         [--ps-list FILE] [--marker-re RE] [--id ID] [--force] [--final-only]
  python tools/rdcdigest/rdcdigest.py captures                      digested captures, sizes, times
  python tools/rdcdigest/rdcdigest.py draws [-c ID] [--shader CHK] [--texture HASH] [--marker RE] [--kept]
         [--blend additive|alpha|opaque|...] [--target RID]        list draws (prefixes match)
  python tools/rdcdigest/rdcdigest.py draw ID EID [--json]          one draw: state, shaders, SRVs, cbuffers
  python tools/rdcdigest/rdcdigest.py cbuffer ID KEY                one constant buffer as float4/uint4 rows
  python tools/rdcdigest/rdcdigest.py texture HASH [--png OUT] [--mip N] [--slice N] [--scale K] [--via-renderdoc]
  python tools/rdcdigest/rdcdigest.py shader CHK                    paths of a shader's .dxbc/.asm/.json
  python tools/rdcdigest/rdcdigest.py contrib ID EID [--png OUT] [--scale K] [--abs] [--full]
                                                                    a kept draw's own change to its targets
  python tools/rdcdigest/rdcdigest.py postvs ID EID [--gs] [--csv OUT] [--attr NAME] [--group N]
                                                                    post-VS output: screen position/size per instance
  python tools/rdcdigest/rdcdigest.py dynamic ID                    render targets/depth kept draws read, as stored
  python tools/rdcdigest/rdcdigest.py exposure ID                   1x1 targets (UE eye adaptation...) after each write
  python tools/rdcdigest/rdcdigest.py gpu ID [--top N] [--kept]     per-event GPU time on the replay machine
  python tools/rdcdigest/rdcdigest.py stats                         store size and blobs shared across captures

The digest runs rd_digest.py inside qrenderdoc (RenderDoc 1.46, set RENDERDOC_DIR or --renderdoc) and then turns
the render-target dumps (rt/*.dds) into ZIP-compressed .exr. Captures are only read, never moved or changed.
--ps-list takes a text file of pixel-shader DXBC checksums (one per line) or a UE ShaderIndex.csv (its
DXBCChecksum column, SF_Pixel rows): draws using one of them are kept even with blending off (masked effects).
"""
import argparse
import csv
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ddsio  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_STORE = os.path.join(REPO, 'out', 'rdcdigest')
DEFAULT_RD = os.environ.get('RENDERDOC_DIR', '')


def dir_size(p):
    n = 0
    for root, _, files in os.walk(p):
        for f in files:
            n += os.path.getsize(os.path.join(root, f))
    return n


def mb(n):
    return '%.1f MB' % (n / 1048576.0)


def load(store, cid):
    return json.load(open(os.path.join(store, 'captures', cid, 'manifest.json')))


def manifests(store, only=None):
    for p in sorted(glob.glob(os.path.join(store, 'captures', '*', 'manifest.json'))):
        cid = os.path.basename(os.path.dirname(p))
        if only and cid not in only:
            continue
        yield cid, json.load(open(p))


# ---- digest ----------------------------------------------------------------------------------------------------
def ps_list_file(path, store):
    """A plain checksum list for rd_digest.py, from a list file or a ShaderIndex.csv."""
    if not path.lower().endswith('.csv'):
        return os.path.abspath(path)
    out = set()
    for r in csv.DictReader(open(path, encoding='utf-8-sig')):
        if r.get('DXBCChecksum') and 'Pixel' in r.get('Stages', 'Pixel'):
            out.add(r['DXBCChecksum'].lower())
    os.makedirs(os.path.join(store, 'tmp'), exist_ok=True)
    p = os.path.join(store, 'tmp', 'pslist_%d.txt' % os.getpid())
    open(p, 'w').write('\n'.join(sorted(out)) + '\n')
    return p


def convert_targets(capdir, man):
    """rt/*.dds -> rt/*.exr (exact: half stays half, depth float32 + stencil uint), then drop the .dds."""
    done = 0
    ents = [e[k] for e in man['targets'].values() for k in ('before', 'after')]
    ents += [d['files'] for d in man.get('depth', {}).values()]
    for ent in ents:
        if 'dds' not in ent:
            continue
        p = os.path.join(capdir, 'rt', ent['dds'])
        try:
            dds = ddsio.read_dds(open(p, 'rb').read())
            ch = ddsio.decode_raw(dds)
            ent['raw_format'] = dds.format
            exr = p[:-4] + '.exr'
            ddsio.write_exr(exr, ch)
            ent['exr'] = os.path.basename(exr)
            ent['exr_channels'] = sorted(ch)
            os.remove(p)
            del ent['dds']
            done += 1
        except Exception as ex:                       # noqa: BLE001  (kept as .dds)
            ent['exr_error'] = repr(ex)
    return done


# formats whose dynamic snapshots become exact ZIP EXR (half stays half; R11G11B10 fits half exactly); 8/10-bit and
# D24 snapshots stay DDS, which is smaller and keeps the bytes
EXR_FORMATS = {'R16G16B16A16_FLOAT': None, 'R32G32B32A32_FLOAT': None, 'R16G16_FLOAT': None, 'R32G32_FLOAT': None,
               'R16_FLOAT': None, 'R32_FLOAT': None, 'D32_FLOAT': None, 'D32_FLOAT_S8X24_UINT': None,
               'R11G11B10_FLOAT': np.float16}


def convert_contribs(capdir, man, dtype='half', threshold=0.0, keep_raw=False):
    """contrib/<eid>_<slot>.raw (the changed box before, then after) -> <eid>_<slot>.exr holding after - before
    (ZIP; HALF channels, or FLOAT with dtype 'float') + <eid>_<slot>.png thumbnail of |after - before|, scaled so the
    99.5th percentile of the changed pixels is white. threshold > 0 shrinks the box to pixels whose largest |change|
    exceeds it. Returns (files converted, bytes written)."""
    from PIL import Image
    n = nbytes = 0
    for r in man['draws']:
        for c in r.get('contrib', []):
            if 'raw' not in c:
                continue
            p = os.path.join(capdir, 'contrib', c['raw'])
            try:
                data = open(p, 'rb').read()
                x, y, cw, ch = c['box']
                half = len(data) // 2
                b = ddsio.decode_pixels(c['format'], data[:half], cw, ch)
                a = ddsio.decode_pixels(c['format'], data[half:], cw, ch)
                names = [k for k in 'RGBA' if k in a]
                d = {k: (a[k].astype(np.float64) - b[k].astype(np.float64)).astype(np.float32) for k in names}
                with np.errstate(invalid='ignore'):
                    mag = np.max(np.stack([np.abs(d[k]) for k in names]), axis=0)
                    mask = ~(mag <= threshold)        # NaN counts as changed
                if threshold > 0:
                    ys, xs = np.nonzero(mask)
                    if not len(ys):
                        c.update({'changed': False, 'below_threshold': threshold})
                        del c['raw'], c['box']
                        if not keep_raw:
                            os.remove(p)
                        continue
                    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                    d = {k: v[y0:y1, x0:x1] for k, v in d.items()}
                    mask, mag = mask[y0:y1, x0:x1], mag[y0:y1, x0:x1]
                    c['box'] = [x + int(x0), y + int(y0), int(x1 - x0), int(y1 - y0)]
                stem = c['raw'][:-4]
                exr = os.path.join(capdir, 'contrib', stem + '.exr')
                ddsio.write_exr(exr, {k: v.astype(np.float16) if dtype == 'half' else v for k, v in d.items()})
                c.update({'exr': stem + '.exr', 'dtype': dtype, 'pixels': int(mask.sum()),
                          'sum': {k: float(np.nansum(d[k][mask])) for k in names},
                          'min': {k: float(np.nanmin(d[k])) for k in names},
                          'max': {k: float(np.nanmax(d[k])) for k in names}})
                rgb = [k for k in 'RGB' if k in d] or names
                img = np.stack([np.abs(d[k]) for k in rgb], axis=-1)
                vals = mag[mask & np.isfinite(mag)]
                top = float(np.percentile(vals, 99.5)) if len(vals) else 1.0
                top = top if top > 0 else 1.0
                img = (np.clip(np.nan_to_num(img / top), 0, 1) * 255 + 0.5).astype(np.uint8)
                im = Image.fromarray(img[..., 0] if img.shape[-1] == 1 else img[..., :3], 'L' if img.shape[-1] == 1
                                     else 'RGB')
                if max(im.size) > 128:
                    k = 128.0 / max(im.size)
                    im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.BOX)
                im.save(os.path.join(capdir, 'contrib', stem + '.png'))
                c.update({'png': stem + '.png', 'thumb_white': top})
                nbytes += os.path.getsize(exr) + os.path.getsize(os.path.join(capdir, 'contrib', stem + '.png'))
                del c['raw']
                if not keep_raw:
                    os.remove(p)
                n += 1
            except Exception as ex:                   # noqa: BLE001  (kept as .raw)
                c['exr_error'] = repr(ex)
    return n, nbytes


def convert_dynamic(store, man):
    """blobs/dynamic/<md5>.dds of float and D32 formats (one mip, one slice) -> <md5>.exr, exact, then drop the .dds.
    The key stays the md5 of the DDS RenderDoc wrote, so later captures with the same content find the .exr."""
    n = 0
    for h in man['blobs'].get('dynamic', []):
        p = os.path.join(store, 'blobs', 'dynamic', h + '.dds')
        if not os.path.exists(p) or os.path.exists(p[:-4] + '.exr') or os.path.exists(p[:-4] + '.png'):
            continue
        try:
            dds = ddsio.read_dds(open(p, 'rb').read())
            f = ddsio.typed_name(dds.format)
            if dds.mips != 1 or dds.slices != 1 or dds.depth != 1:
                continue
            sp = p[:-4] + '.json'
            meta = json.load(open(sp)) if os.path.exists(sp) else {}
            if f in EXR_FORMATS:
                ch = ddsio.decode_raw(dds)
                cast = EXR_FORMATS[f]
                ddsio.write_exr(p[:-4] + '.exr', {k: v.astype(cast) if cast and v.dtype.kind == 'f' else v
                                                  for k, v in ch.items()})
                meta.update({'exr': h + '.exr', 'exr_channels': sorted(ch), 'raw_format': dds.format})
            elif f in ddsio.PNG_EXACT:
                ddsio.exact_png(dds).save(p[:-4] + '.png', optimize=False, compress_level=6)
                meta.update({'png': h + '.png', 'raw_format': dds.format,
                             'png_note': 'the stored bytes, as 8-bit PNG (BGRA reordered to RGBA, X8 dropped)'})
            else:
                continue
            json.dump(meta, open(sp, 'w'), indent=1)
            new = [x for x in (p[:-4] + '.exr', p[:-4] + '.png') if os.path.exists(x)]
            bn = man['blob_stats']['bytes_new']
            bn['dynamic'] = bn.get('dynamic', 0) - os.path.getsize(p) + sum(os.path.getsize(x) for x in new)
            os.remove(p)
            n += 1
        except Exception as ex:                       # noqa: BLE001  (kept as .dds)
            print('  dynamic %s: kept as DDS (%r)' % (h, ex))
    return n


def texel_values(fmt, hexstr, w=1, h=1):
    ch = ddsio.decode_pixels(fmt, bytes.fromhex(hexstr), w, h)
    return [[float(ch[k].flat[i]) for k in 'ZSRGBA' if k in ch] for i in range(w * h)]


def decode_texels(man):
    for ent in man.get('tiny', {}).values():
        w, h = ent['size'][0], ent['size'][1] * max(1, ent['size'][2])
        for wr in ent['writes'] + ([ent['first_read']] if 'first_read' in ent else []):
            try:
                wr['values'] = texel_values(ent['format'], wr['hex'], w, h)
            except Exception as ex:                   # noqa: BLE001
                wr['values_error'] = repr(ex)
    for ent in man.get('dynamic', {}).values():
        if 'texel_hex' in ent:
            try:
                ent['texel'] = texel_values(ent['format'], ent['texel_hex'], ent['size'][0],
                                            ent['size'][1] * max(1, ent['size'][2]))
            except Exception as ex:                   # noqa: BLE001
                ent['texel_error'] = repr(ex)


def gpu_name():
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              '(Get-CimInstance Win32_VideoController).Name'], capture_output=True, text=True,
                             timeout=60).stdout
        return '; '.join(x.strip() for x in out.splitlines() if x.strip())
    except Exception:                                 # noqa: BLE001
        return ''


def redo_final(qrd, script, cap, store, cid, capdir, mpath, timeout):
    """digest --final-only: redo final.png and the manifest's 'final' of a digested capture (the presented swap
    buffer, rd_digest.presented_buffer), nothing else. 1 on failure."""
    if not os.path.exists(mpath):
        print('%s: not digested' % cid)
        return 1
    if json.load(open(mpath))['capture'] != cap:
        print('%s: id belongs to another capture; pass --id' % cid)
        return 1
    env = dict(os.environ, RD_CAPTURE=cap, RD_STORE=store, RD_ID=cid, RD_TOOLDIR=HERE, RD_FINAL_ONLY='1')
    t0 = time.time()
    subprocess.run([qrd, '--python', script], env=env, timeout=timeout)
    logp = os.path.join(capdir, 'log_final.txt')
    lines = open(logp).read().strip().splitlines() if os.path.exists(logp) else []
    last = lines[-1] if lines else '(empty log: qrenderdoc ended before writing)'
    print('%s: %s%s (%.0f s)' % (cid, '' if last.startswith('done:') else 'FAILED: ', last, time.time() - t0))
    return 0 if last.startswith('done:') else 1


def digest(args):
    store = os.path.abspath(args.store)
    qrd = os.path.join(args.renderdoc, 'qrenderdoc.exe')
    script = os.path.join(HERE, 'rd_digest.py')
    pslist = ps_list_file(args.ps_list, store) if args.ps_list else ''
    caps = []
    for p in args.paths:
        caps += sorted(glob.glob(os.path.join(p, '*.rdc'))) if os.path.isdir(p) else [p]
    if args.id and len(caps) != 1:
        sys.exit('--id needs exactly one capture')
    failed = 0
    gpu = ''
    for cap in caps:
        cap = os.path.abspath(cap).replace('\\', '/')
        cid = args.id or os.path.splitext(os.path.basename(cap))[0]
        capdir = os.path.join(store, 'captures', cid)
        mpath = os.path.join(capdir, 'manifest.json')
        st = os.stat(cap)
        if args.final_only:
            failed += redo_final(qrd, script, cap, store, cid, capdir, mpath, args.timeout)
            continue
        if os.path.exists(mpath):
            old = json.load(open(mpath))
            if old['capture'] != cap:
                print('%s: id already used by %s; pass --id' % (cid, old['capture']))
                failed += 1
                continue
            if not args.force and old['capture_bytes'] == st.st_size and old['capture_mtime'] == int(st.st_mtime):
                print('%s: already digested (use --force to redo)' % cid)
                continue
        env = dict(os.environ, RD_CAPTURE=cap, RD_STORE=store, RD_ID=cid, RD_ALL='1' if args.all else '',
                   RD_RESOURCES_ALL='1' if args.all_resources else '', RD_BUFFER_CAP=str(args.buffer_cap << 20),
                   RD_PSLIST=pslist, RD_MARKER_RE=args.marker_re or '', RD_TOOLDIR=HERE, RD_CONTRIB=args.contrib,
                   RD_POSTVS=args.postvs, RD_POSTVS_CAP=str(args.postvs_cap << 20),
                   RD_DYNAMIC='0' if args.no_dynamic else '1', RD_DYNAMIC_CAP=str(args.dynamic_cap << 20),
                   RD_COUNTERS='0' if args.no_counters else '1', RD_REHASH='1' if args.rehash else '')
        logp = os.path.join(capdir, 'log.txt')
        for attempt in (1, 2):
            t0 = time.time()
            subprocess.run([qrd, '--python', script], env=env, timeout=args.timeout)
            wall = time.time() - t0
            lines = open(logp).read().strip().splitlines() if os.path.exists(logp) else []
            last = lines[-1] if lines else '(no log)' if not os.path.exists(logp) else '(empty log)'
            # qrenderdoc sometimes exits at once without running the script (2026-10-20, Boost lv1loop: empty
            # log.txt, no crash event; the retry digested it normally): try that case once more
            if lines or attempt == 2:
                break
            print('%s: qrenderdoc exited after %.0f s without a log line; retrying' % (cid, wall))
        if not last.startswith('done:') or not os.path.exists(mpath):
            print('%s: FAILED after %.0f s: %s' % (cid, wall, last))
            failed += 1
            continue
        man = json.load(open(mpath))
        t1 = time.time()
        n = convert_targets(capdir, man)
        nc, cbytes = convert_contribs(capdir, man, args.contrib_dtype, args.contrib_threshold, args.keep_raw)
        nd = convert_dynamic(store, man)
        decode_texels(man)
        man['options'].update({'contrib_dtype': args.contrib_dtype, 'contrib_threshold': args.contrib_threshold})
        if man.get('gpu') and 'error' not in man['gpu']:
            man['gpu']['replay_gpu'] = gpu or gpu_name()
            gpu = man['gpu']['replay_gpu']
        man['contrib_bytes'] = cbytes
        man['timing']['qrenderdoc_wall_s'] = round(wall, 1)
        man['timing']['exr_s'] = round(time.time() - t1, 1)
        man['timing']['wall_s'] = round(time.time() - t0, 1)
        man['digest_dir_bytes'] = 0
        with open(mpath + '.tmp', 'w') as f:
            json.dump(man, f, separators=(',', ':'))
        os.replace(mpath + '.tmp', mpath)
        man['digest_dir_bytes'] = dir_size(capdir)
        with open(mpath + '.tmp', 'w') as f:
            json.dump(man, f, separators=(',', ':'))
        os.replace(mpath + '.tmp', mpath)
        c = man['counts']
        print('%s: %d draws, %d dispatches, %d kept, %d errors | %d targets, %d contributions (%s), %d dynamic -> exr '
              '| capture %s, digest dir %s, new blobs %s | %.0f s' % (
                  cid, c['draws'], c['dispatches'], c['kept'], c['errors'], n, nc, mb(cbytes), nd,
                  mb(man['capture_bytes']), mb(man['digest_dir_bytes']),
                  mb(sum(man['blob_stats']['bytes_new'].values())), man['timing']['wall_s']))
    if pslist and args.ps_list.lower().endswith('.csv'):
        os.remove(pslist)
    if args.dedup:
        dedup_store(store)
    if args.compress:
        compress_store(store)
    return 1 if failed else 0


def dedup_store(store, min_bytes=1 << 20):
    """digest --dedup: a per-capture file (rt/, contrib/, final.*) whose bytes equal a blob, or another capture's file,
    becomes a hard link to it (NTFS: one copy on disk, every reader unchanged). Blobs are already one file per content;
    this catches the rest, e.g. rt/<target>_after.exr equal to the blobs/dynamic snapshot the next pass reads.
    Writers replace files (os.replace, remove + write), never write in place, so a link is never written through."""
    t0 = time.time()
    seen, n, saved = {}, 0, 0
    files = []
    for top in ('blobs', 'captures'):            # blobs first: they become the kept copy
        for r, _, fs in os.walk(os.path.join(store, top)):
            files += [os.path.join(r, f) for f in sorted(fs)]
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_size < min_bytes or p.endswith(('.tmp', '.json')):
            continue
        key = (st.st_size, hashlib.md5(open(p, 'rb').read()).hexdigest())
        first = seen.setdefault(key, p)
        if first == p or os.path.samefile(first, p):
            continue
        tmp = p + '.lnk.tmp'
        if os.path.exists(tmp):
            os.remove(tmp)
        os.link(first, tmp)
        os.replace(tmp, p)
        n += 1
        saved += st.st_size
    print('dedup %s: %d file(s) now hard links, %s saved (%.0f s)' % (store, n, mb(saved), time.time() - t0))


def compress_store(store):
    """digest --compress: Windows file compression (LZX, `compact /exe:lzx`) on every file of the store: lossless and
    transparent (every reader opens the files as before), 1.4-2.3x on the 2026-10-20 pilot stores. A file written
    again later is stored uncompressed until the next --compress; blobs are written once."""
    if os.name != 'nt':
        print('--compress: not on Windows, skipped')
        return
    t0 = time.time()
    r = subprocess.run(['compact.exe', '/c', '/s:' + store, '/exe:lzx', '/i', '/q'], capture_output=True, text=True,
                       errors='replace')
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    print('compressed %s (LZX, %.0f s): %s' % (store, time.time() - t0, ' | '.join(lines[-2:])))


# ---- queries ---------------------------------------------------------------------------------------------------
def captures(args):
    print('%-36s %-6s %6s %6s %5s %10s %10s %7s' % ('id', 'api', 'draws', 'disp', 'kept', 'capture', 'digest', 'time'))
    for cid, m in manifests(args.store):
        c = m['counts']
        print('%-36s %-6s %6d %6d %5d %10s %10s %6.0fs' % (cid, m['api'], c['draws'], c['dispatches'], c['kept'],
                                                          mb(m['capture_bytes']), mb(m.get('digest_dir_bytes', 0)),
                                                          m['timing'].get('wall_s', m['timing']['total_s'])))


def textures_of(r):
    return [s for sh in r.get('shaders', {}).values() for s in sh.get('srvs', []) if s.get('kind') == 'texture']


def draws(args):
    if getattr(args, 'db', None):
        import digestlib
        with digestlib.open(args.db) as d:
            selected = [(r.pop('capture'), r) for r in d.draws(
                args.capture, kept=args.kept, shader=args.shader, texture=args.texture,
                marker=args.marker, blend=args.blend, target=args.target)]
    else:
        selected = []
        mre = re.compile(args.marker) if args.marker else None
        for cid, m in manifests(args.store, args.capture):
            for r in m['draws']:
                if args.kept and not r.get('keep'):
                    continue
                sh = r.get('shaders', {})
                if args.shader and not any(v['checksum'].startswith(args.shader.lower()) or
                                           v['md5'].startswith(args.shader.lower()) for v in sh.values()):
                    continue
                tex = textures_of(r)
                if args.texture and not any((t.get('hash') or '').startswith(args.texture.lower()) for t in tex):
                    continue
                if mre and not mre.search(r.get('path', '')):
                    continue
                bs = r.get('blend_summary') or [{}]
                if args.blend and bs[0].get('kind') != args.blend:
                    continue
                if args.target and not any(t['id'] == args.target for t in r.get('targets', [])):
                    continue
                selected.append((cid, r))
    n = 0
    for cid, r in selected:
        sh = r.get('shaders', {})
        tex = textures_of(r)
        bs = r.get('blend_summary') or [{}]
        n += 1
        print('%-30s %6d %-8s idx %6s inst %5s  %-9s rt %-6s vs %s ps %s  tex %s' % (
            cid[-30:], r['eid'], r['kind'], r.get('indices', ''), r.get('instances', ''),
            bs[0].get('kind', '-'), (r.get('targets') or [{'id': '-'}])[0]['id'],
            sh.get('vs', sh.get('cs', {})).get('checksum', '-')[:8], sh.get('ps', {}).get('checksum', '-')[:8],
            ' '.join('t%d:%s' % (t['register'], (t.get('hash') or 'dyn')[:8]) for t in tex)))
    print('%d draws' % n)


def floats_rows(hexstr):
    b = bytes.fromhex(hexstr)
    rows = []
    for i in range(0, len(b) // 16 * 16, 16):
        f = struct.unpack('<4f', b[i:i + 16])
        u = struct.unpack('<4I', b[i:i + 16])
        rows.append((i, f, u))
    return rows


def fmt_row(i, f, u):
    looks_int = all(x == 0 or 0 < x < 0x00800000 or x == 0xFFFFFFFF for x in u) and any(u)
    vals = ' '.join('%11d' % x for x in u) if looks_int else ' '.join('%11.5g' % x for x in f)
    return '  [%3d] +%-5d %s%s' % (i // 16, i, vals, '   (uint)' if looks_int else '')


def draw(args):
    m = load(args.store, args.capture)
    r = next((x for x in m['draws'] if x['eid'] == args.eid), None)
    if r is None:
        sys.exit('no draw %d in %s' % (args.eid, args.capture))
    T = m['tables']
    if args.json:
        full = dict(r)
        for k in ('blend', 'depth', 'raster', 'layout'):
            if r.get(k):
                full[k] = T[k][r[k]]
        for sh in full.get('shaders', {}).values():
            for cb in sh.get('cbuffers', []):
                if cb.get('data'):
                    cb['hex'] = T['cbdata'][cb['data']]
                if cb.get('vars'):
                    cb['decoded'] = T['cbvars'][cb['vars']]
            for s in sh.get('samplers', []):
                s['state'] = T['sampler'][s['state']]
        print(json.dumps(full, indent=1))
        return
    print('%s eid %d  %s  %s' % (args.capture, r['eid'], r['name'], r.get('path', '')))
    if r['kind'] == 'draw':
        print('  indices %d instances %d indexed %s base_vertex %d index_offset %d instance_offset %d topology %s' % (
            r['indices'], r['instances'], r['indexed'], r['base_vertex'], r['index_offset'], r['instance_offset'],
            r.get('topology')))
        print('  kept: %s' % (', '.join(r.get('keep') or []) or 'no'))
        for t in r.get('targets', []):
            print('  target %d: %s %s %s' % (t['slot'], t['id'], t['format'], t['size']))
        if r.get('depth_target'):
            print('  depth: %s %s' % (r['depth_target']['id'], r['depth_target']['format']))
        print('  viewport %s scissor %s' % (r['viewport'], r['scissor']))
        for b in r.get('blend_summary', []):
            print('  blend rt%d: %s  color %s | alpha %s | mask %d' % (b['slot'], b['kind'] if b['enabled'] else 'off',
                                                                    b['color'], b['alpha'], b['writeMask']))
        d = T['depth'][r['depth']]
        print('  depth: %s' % json.dumps({k: d[k] for k in d if k in ('depthEnable', 'depthWrites', 'depthFunction',
                                                                         'stencilEnable')}))
        rs = T['raster'][r['raster']]
        print('  raster: %s' % json.dumps({k: rs[k] for k in rs if k in ('cullMode', 'fillMode', 'frontCCW',
                                                                           'depthBias', 'depthClip')}))
        if r.get('layout'):
            for v in T['layout'][r['layout']]:
                print('  input %-12s slot %d +%-3d %-20s %s' % (v['name'], v['slot'], v['offset'], v['format'],
                                                              'instance' if v['perInstance'] else 'vertex'))
        if r.get('ib'):
            print('  ib: %s' % json.dumps(r['ib']))
        for v in r.get('vb', []):
            print('  vb: %s' % json.dumps(v))
    if 'gpu_us' in r:
        print('  gpu %.2f us (replay machine)' % r['gpu_us'])
    for c in r.get('contrib', []):
        what = ('box %s, %s px changed, %s' % (c['box'], c.get('pixels'), c.get('exr')) if c.get('changed')
                else 'unchanged')
        print('  contrib rt%s: %s' % (c.get('slot'), what))
    for k, pv in (r.get('postvs') or {}).items():
        print('  postvs %s: %s' % (k, json.dumps({x: pv[x] for x in ('vertices', 'instances', 'stride', 'bytes',
                                                                    'vertex', 'truncated', 'status', 'error')
                                                 if x in pv})))
    for stage, sh in r.get('shaders', {}).items():
        print('  %s %s (md5 %s)' % (stage.upper(), sh['checksum'], sh['md5']))
        for s in sh.get('srvs', []):
            if s.get('kind') == 'texture':
                print('    t%-2d %-14s %s %dx%dx%d mips %d %s view %s %s%s%s' % (
                    s['register'], s.get('hash', 'dynamic')[:32] if s.get('hash') else 'dynamic', s['format'],
                    s['width'], s['height'], s['depth'], s['mips'], s['type'], s['view_format'],
                    'stored' if s.get('stored') else '', ' dynamic, written at eid %s' % s.get('written_at')
                    if s.get('dynamic') else '', ' REWRITTEN since first hash' if s.get('rewritten') else ''))
            else:
                print('    t%-2d %s %s' % (s['register'], s.get('kind', 'null'), json.dumps(
                    {k: s[k] for k in ('id', 'size', 'stride', 'hash', 'view_format') if k in s})))
        for s in sh.get('samplers', []):
            st = T['sampler'][s['state']]
            print('    s%-2d %s' % (s['register'], json.dumps({k: st[k] for k in st if 'address' in k.lower() or
                                                             'filter' in k.lower()})))
        for cb in sh.get('cbuffers', []):
            print('    b%-2d %s size %s data %s%s' % (cb['register'], cb['name'], cb.get('size'), cb.get('data'),
                                                   ' vars ' + cb['vars'] if cb.get('vars') else ''))
            if args.cbuffers and cb.get('data') and cb.get('size', 0) <= args.max_cb:
                for i, f, u in floats_rows(T['cbdata'][cb['data']]):
                    print('  ' + fmt_row(i, f, u))


def cbuffer(args):
    m = load(args.store, args.capture)
    T = m['tables']
    key = args.key.split('-')[0]
    hexs = T['cbdata'].get(key)
    if hexs is None:
        sys.exit('no cbuffer %s' % key)
    for i, f, u in floats_rows(hexs):
        print(fmt_row(i, f, u))
    for k, v in T['cbvars'].items():
        if k.startswith(key):
            print(json.dumps(v, indent=1))


def find_dynamic(store, h):
    hits = [x for x in glob.glob(os.path.join(store, 'blobs', 'dynamic', h.lower() + '*'))
            if os.path.splitext(x)[1] in ('.dds', '.exr', '.png')]
    return hits


def dynamic_image(path, scale=1.0, channel=None):
    """A dynamic snapshot (.exr/.png/.dds) as a PIL image; depth is normalised to its own min..max."""
    from PIL import Image
    if path.endswith('.png'):
        return Image.open(path)
    if path.endswith('.dds'):
        return ddsio.to_image(ddsio.read_dds(open(path, 'rb').read()), 0, 0, scale)
    ch = ddsio.read_exr(path)
    if channel:
        ch = {'R': ch[channel]}
    elif 'Z' in ch:
        ch = {'Z': ch['Z']}
    h, w = next(iter(ch.values())).shape
    if 'Z' in ch:
        z = ch['Z'].astype(np.float32)
        lo, hi = float(np.nanmin(z)), float(np.nanmax(z))
        v = (z - lo) / (hi - lo) if hi > lo else np.zeros_like(z)
        return Image.fromarray((np.clip(v, 0, 1) * 255 + 0.5).astype(np.uint8), 'L')
    names = [c for c in 'RGBA' if c in ch]
    if len(names) == 1:
        return Image.fromarray((np.clip(np.nan_to_num(ch[names[0]].astype(np.float32) * scale), 0, 1) * 255 + 0.5)
                               .astype(np.uint8), 'L')
    out = np.zeros((h, w, 3), np.float32)
    for i, c in enumerate('RGB'):
        if c in ch:
            out[..., i] = ch[c].astype(np.float32) * scale
    return Image.fromarray((np.clip(np.nan_to_num(out), 0, 1) * 255 + 0.5).astype(np.uint8), 'RGB')


def texture(args):
    base = os.path.join(args.store, 'blobs', 'textures')
    hits = glob.glob(os.path.join(base, args.hash.lower() + '*.dds'))
    dyn = find_dynamic(args.store, args.hash)
    if not hits and len(dyn) == 1:
        p = dyn[0]
        sp = os.path.splitext(p)[0] + '.json'
        meta = json.load(open(sp)) if os.path.exists(sp) else {}
        print('%s  dynamic snapshot  %s' % (os.path.basename(p), json.dumps(meta)))
        if args.png:
            im = dynamic_image(p, args.scale, args.channel)
            im.save(args.png)
            print('wrote %s (%dx%d %s%s)' % (args.png, im.width, im.height, im.mode,
                                             '; depth normalised to its min..max' if 'Z' in meta.get('exr_channels', [])
                                             and not args.channel else ''))
        return
    if len(hits) != 1:
        sys.exit('%d textures match %s' % (len(hits) + len(dyn), args.hash))
    p = hits[0]
    meta = json.load(open(p[:-4] + '.json')) if os.path.exists(p[:-4] + '.json') else {}
    dds = ddsio.read_dds(open(p, 'rb').read())
    print('%s  %s %dx%dx%d mips %d slices %d  %s' % (os.path.basename(p), dds.format, dds.width, dds.height,
                                                    dds.depth, dds.mips, dds.slices, json.dumps(meta)))
    users = []
    if getattr(args, 'db', None):
        import digestlib
        with digestlib.open(args.db) as d:
            captures_used = {}
            for r in d.where(os.path.basename(p)[:-4]):
                if r['kind'] == 'texture':
                    captures_used.setdefault(r['capture'], set()).add(r['eid'])
            for cid, events in sorted(captures_used.items()):
                e = sorted(events)
                users.append('%s (%d draws, eid %d..%d)' % (cid, len(e), e[0], e[-1]))
    else:
        for cid, m in manifests(args.store):
            e = sorted({r['eid'] for r in m['draws'] for t in textures_of(r) if t.get('hash') == os.path.basename(p)[:-4]})
            if e:
                users.append('%s (%d draws, eid %d..%d)' % (cid, len(e), e[0], e[-1]))
    print('used in: ' + ('; '.join(users) or 'none'))
    if args.png:
        if not (0 <= args.mip < dds.mips and 0 <= args.slice < dds.slices):
            sys.exit('mip %d / slice %d out of range (%d mips, %d slices)' % (args.mip, args.slice, dds.mips, dds.slices))
        if args.via_renderdoc:
            png = os.path.abspath(args.png)
            env = dict(os.environ, RD_DDS=os.path.abspath(p), RD_PNG=png, RD_MIP=str(args.mip), RD_SLICE=str(args.slice))
            subprocess.run([os.path.join(args.renderdoc, 'qrenderdoc.exe'), '--python',
                            os.path.join(HERE, 'rd_texpng.py')], env=env, timeout=600)
            lines = open(png + '.log').read().strip().splitlines() if os.path.exists(png + '.log') else ['(no log)']
            if lines[-1] != 'done':
                sys.exit('RenderDoc export failed: %s' % lines[-1])
            os.remove(png + '.log')
            print('wrote %s (RenderDoc decode, mip %d slice %d)' % (args.png, args.mip, args.slice))
            return
        im = ddsio.to_image(dds, args.mip, args.slice, args.scale)
        im.save(args.png)
        print('wrote %s (%dx%d %s, mip %d slice %d; Pillow decode)' % (args.png, im.width, im.height, im.mode,
                                                                      args.mip, args.slice))


def find_row(m, eid, cid):
    r = next((x for x in m['draws'] if x['eid'] == eid), None)
    if r is None:
        sys.exit('no draw %d in %s' % (eid, cid))
    return r


def contrib(args):
    from PIL import Image
    m = load(args.store, args.capture)
    r = find_row(m, args.eid, args.capture)
    cs = r.get('contrib')
    if cs is None:
        sys.exit('eid %d has no contribution (kept: %s; digest option contrib=%s)' % (
            args.eid, ', '.join(r.get('keep') or []) or 'no', m.get('options', {}).get('contrib', 'n/a (v1 digest)')))
    capdir = os.path.join(args.store, 'captures', args.capture)
    for c in cs:
        if args.slot is not None and c.get('slot') != args.slot:
            continue
        if not c.get('changed'):
            print('rt%s %s %s: unchanged%s' % (c.get('slot'), c.get('id'), c.get('format'),
                                               ' (%s)' % c['skipped'] if c.get('skipped') else ''))
            continue
        x, y, w, h = c['box']
        print('rt%d %s %s  box x %d y %d  %dx%d of %dx%d  changed px %s  before = eid %s' % (
            c['slot'], c['id'], c['format'], x, y, w, h, c['size'][0], c['size'][1], c.get('pixels'), c['before_eid']))
        if 'sum' in c:
            print('   sum  ' + '  '.join('%s %.6g' % (k, v) for k, v in c['sum'].items()))
            print('   min  ' + '  '.join('%s %.6g' % (k, v) for k, v in c['min'].items()))
            print('   max  ' + '  '.join('%s %.6g' % (k, v) for k, v in c['max'].items()))
        for k in ('exr', 'png'):
            if c.get(k):
                print('   %s %s' % (k, os.path.join(capdir, 'contrib', c[k])))
        if args.png and c.get('exr'):
            d = ddsio.read_exr(os.path.join(capdir, 'contrib', c['exr']))
            names = [k for k in 'RGB' if k in d] or sorted(d)
            img = np.stack([d[k].astype(np.float32) for k in names], -1) * args.scale
            img = np.abs(img) if args.abs else img
            img = (np.clip(np.nan_to_num(img), 0, 1) * 255 + 0.5).astype(np.uint8)
            if args.full:
                canvas = np.zeros((c['size'][1], c['size'][0], img.shape[-1]), np.uint8)
                canvas[y:y + h, x:x + w] = img
                img = canvas
            out = args.png
            if len([z for z in cs if z.get('exr')]) > 1:
                out = args.png.replace('.png', '_rt%d.png' % c['slot'])
            Image.fromarray(img[..., 0] if img.shape[-1] == 1 else img, 'L' if img.shape[-1] == 1 else 'RGB').save(out)
            print('   wrote %s (after - before) x %g%s, clamped 0..1%s' % (out, args.scale, ', abs' if args.abs else '',
                                                                       ', on the full target' if args.full else ''))


def postvs_data(store, m, r, stage='vs'):
    """(entry, layout, float32 array (instances, vertices, stride/4)) of a draw's stored post-VS output."""
    pv = (r.get('postvs') or {}).get(stage)
    if not pv or not pv.get('vertex'):
        return pv, None, None
    lay = m['tables']['postvs_layout'].get(pv.get('layout'))
    raw = open(os.path.join(store, 'blobs', 'postvs', pv['vertex'] + '.bin'), 'rb').read()
    f = np.frombuffer(raw, '<f4').reshape(-1, pv['stride'] // 4)
    per = pv['vertices'] if pv.get('inst_stride') else f.shape[0]
    return pv, lay, f[:f.shape[0] // per * per].reshape(-1, per, pv['stride'] // 4)


def screen_of(clip, viewport):
    """Clip-space xyzw -> (pixel x, pixel y, z/w) for a D3D viewport [x, y, w, h, minDepth, maxDepth]."""
    w = clip[..., 3]
    with np.errstate(divide='ignore', invalid='ignore'):
        nx, ny, nz = clip[..., 0] / w, clip[..., 1] / w, clip[..., 2] / w
    vx, vy, vw, vh = viewport[:4]
    return vx + (nx * 0.5 + 0.5) * vw, vy + (0.5 - ny * 0.5) * vh, nz


def postvs(args):
    m = load(args.store, args.capture)
    r = find_row(m, args.eid, args.capture)
    pv, lay, f = postvs_data(args.store, m, r, 'gs' if args.gs else 'vs')
    if pv is None:
        sys.exit('eid %d: no post-VS data stored (kept: %s)' % (args.eid, ', '.join(r.get('keep') or []) or 'no'))
    print('%s eid %d %s: %s' % (args.capture, args.eid, 'GS out' if args.gs else 'VS out',
                                json.dumps({k: v for k, v in pv.items() if k != 'layout'})))
    if f is None:
        return
    for x in lay or []:
        print('  +%-4d %-18s %s x%d %s' % (x['offset'], '%s%d' % (x['name'], x['index']), x['type'], x['comps'],
                                          x['system'] if x['system'] != 'Undefined' else ''))
    groups = f
    if args.group:
        flat = f.reshape(-1, f.shape[-1])
        groups = flat[:flat.shape[0] // args.group * args.group].reshape(-1, args.group, f.shape[-1])
    px, py, z = screen_of(groups[..., 0:4], r['viewport'])
    attr = None
    if args.attr:
        a = next((x for x in lay or [] if args.attr in ('%s%d' % (x['name'], x['index']), x['name'])), None)
        if a is None:
            sys.exit('no attribute %s (have %s)' % (args.attr, ', '.join('%s%d' % (x['name'], x['index'])
                                                                          for x in lay or [])))
        attr = groups[:, 0, a['offset'] // 4:a['offset'] // 4 + a['comps']]
    rows = []
    for i in range(groups.shape[0]):
        ok = groups[i, :, 3] > 0
        if not ok.any():
            rows.append((i, groups.shape[1], None))
            continue
        xs, ys = px[i][ok], py[i][ok]
        rows.append((i, groups.shape[1], (float((xs.min() + xs.max()) / 2), float((ys.min() + ys.max()) / 2),
                                          float(xs.max() - xs.min()), float(ys.max() - ys.min()),
                                          float(np.nanmean(z[i][ok])), float(np.mean(groups[i, ok, 3])))))
    unit = 'instance' if pv.get('inst_stride') and not args.group else ('group of %d' % args.group if args.group
                                                                        else 'draw')
    print('%d %s(s); pixel coordinates in the %dx%d viewport at (%g, %g)' % (
        len(rows), unit, r['viewport'][2], r['viewport'][3], r['viewport'][0], r['viewport'][1]))
    print('  %6s %5s %9s %9s %8s %8s %10s %10s%s' % ('#', 'verts', 'centre x', 'centre y', 'width', 'height', 'z/w',
                                                    'w', '  ' + args.attr if attr is not None else ''))
    for n, (i, nv, v) in enumerate(rows):
        if n >= args.limit:
            print('  ... %d more (--limit, or --csv for all)' % (len(rows) - n))
            break
        if v is None:
            print('  %6d %5d   behind the camera (w <= 0)' % (i, nv))
            continue
        print('  %6d %5d %9.1f %9.1f %8.1f %8.1f %10.6f %10.3f%s' % ((i, nv) + v + (
            ('  ' + ' '.join('%.4g' % q for q in attr[i])) if attr is not None else '',)))
    if args.csv:
        with open(args.csv, 'w', newline='') as fh:
            wr = csv.writer(fh)
            wr.writerow(['index', 'vertices', 'centre_x', 'centre_y', 'width_px', 'height_px', 'z_over_w', 'w'] +
                        (['%s_%d' % (args.attr, k) for k in range(attr.shape[1])] if attr is not None else []))
            for i, nv, v in rows:
                wr.writerow([i, nv] + (list(v) if v else [''] * 6) + (list(attr[i]) if attr is not None else []))
        print('wrote %s' % args.csv)


def dynamic(args):
    m = load(args.store, args.capture)
    dyn = m.get('dynamic')
    if dyn is None:
        sys.exit('%s: v1 digest, no dynamic snapshots' % args.capture)
    print('%-34s %-6s %-26s %-22s %-14s %6s %6s %5s  %s' % ('hash', 'id', 'name', 'format', 'size', 'first',
                                                           'written', 'kept', 'file'))
    for h, d in sorted(dyn.items(), key=lambda kv: kv[1]['first_eid']):
        f = find_dynamic(args.store, h)
        print('%-34s %-6s %-26s %-22s %-14s %6d %6s %5d  %s%s' % (
            h, d['id'], d['name'][:26], d['format'], 'x'.join(str(v) for v in d['size']), d['first_eid'],
            d['written_at'], d['kept_readers'], os.path.basename(f[0]) if f else 'missing',
            '  texel %s' % d['texel'] if d.get('texel') else ''))
    nodata = {}
    for r in m['draws']:
        for sh in r.get('shaders', {}).values():
            for sv in sh.get('srvs', []):
                if sv.get('dynamic') and sv.get('snapshot'):
                    nodata.setdefault((sv['id'], sv['snapshot']), []).append(r['eid'])
    for (k, why), es in sorted(nodata.items()):
        print('not stored: %s (%s) at %d draws, first eid %d' % (k, why, len(es), es[0]))


def exposure(args):
    m = load(args.store, args.capture)
    if 'tiny' not in m:
        sys.exit('%s: v1 digest, no tiny-target values' % args.capture)
    print('targets of at most 16 texels the frame uses (UE eye adaptation is a 1x1 float target; its x is the '
          'exposure multiplier in UE 4.25+):')
    for k, t in sorted(m['tiny'].items(), key=lambda kv: (kv[1]['writes'] or [kv[1].get('first_read')])[0]['eid']):
        flt = 'FLOAT' in t['format']
        if args.float_only and not flt:
            continue
        print('  %s %s %s %s  writes %d%s  read by kept draws: %s' % (
            k, t['name'], t['format'], 'x'.join(str(v) for v in t['size']), t.get('writes_total', len(t['writes'])),
            '' if len(t['writes']) == t.get('writes_total', len(t['writes'])) else ' (first/last 32 kept)',
            ', '.join(str(e) for e in t['kept_readers'][:10]) or 'none'))
        for wr in t['writes']:
            print('     after eid %6d: %s' % (wr['eid'], wr.get('values', wr.get('values_error'))))
        if 'first_read' in t:
            print('     not written this frame; at its first use (eid %d): %s' % (
                t['first_read']['eid'], t['first_read'].get('values', t['first_read'].get('values_error'))))
    for h, d in m.get('dynamic', {}).items():
        if d.get('texel'):
            print('  read by kept draws: %s %s %s = %s (first eid %d, written at %s)' % (
                d['id'], d['name'], d['format'], d['texel'], d['first_eid'], d['written_at']))


def gpu(args):
    m = load(args.store, args.capture)
    g = m.get('gpu')
    if not g:
        sys.exit('%s: no GPU timing (v1 digest or --no-counters)' % args.capture)
    print(json.dumps(g))
    rows = [r for r in m['draws'] if 'gpu_us' in r and (r.get('keep') or not args.kept)]
    rows.sort(key=lambda r: -r['gpu_us'])
    for r in rows[:args.top]:
        bs = (r.get('blend_summary') or [{}])[0]
        print('  %6d %-8s %9.2f us  %-9s kept %-14s idx %s inst %s' % (
            r['eid'], r['kind'], r['gpu_us'], bs.get('kind', '-'), ','.join(r.get('keep') or []) or '-',
            r.get('indices', ''), r.get('instances', '')))
    print('%d events; total %.1f us (%s)' % (len(rows), sum(r['gpu_us'] for r in rows),
                                            'kept draws' if args.kept else 'all events'))


def shader(args):
    base = os.path.join(args.store, 'blobs', 'shaders')
    hits = sorted(glob.glob(os.path.join(base, args.checksum.lower() + '*')))
    for h in hits:
        print(h)
    if not hits:
        for cid, m in manifests(args.store):
            if any(k.startswith(args.checksum.lower()) for k in m['shaders']):
                print('%s: seen in %s, not stored (no kept draw used it)' % (args.checksum, cid))


def stats(args):
    store = args.store
    print('store %s' % store)
    for kind in ('textures', 'meshes', 'buffers', 'shaders', 'postvs', 'dynamic'):
        d = os.path.join(store, 'blobs', kind)
        files = os.listdir(d) if os.path.isdir(d) else []
        print('  blobs/%-9s %5d files %10s' % (kind, len(files), mb(dir_size(d))))
    caps = list(manifests(store))
    total_cap = sum(m['capture_bytes'] for _, m in caps)
    print('  captures/        %5d dirs  %10s' % (len(caps), mb(dir_size(os.path.join(store, 'captures')))))
    print('  whole store      %10s   vs %d .rdc %s' % (mb(dir_size(store)), len(caps), mb(total_cap)))
    for kind in ('textures', 'meshes', 'buffers', 'shaders', 'postvs', 'dynamic'):
        users = {}
        for cid, m in caps:
            for h in m['blobs'].get(kind, []):
                users.setdefault(h, set()).add(cid)
        shared = sum(1 for v in users.values() if len(v) > 1)
        in_all = sum(1 for v in users.values() if len(v) == len(caps))
        print('  %-9s %5d distinct, %5d in 2+ captures, %5d in all %d' % (kind, len(users), shared, in_all, len(caps)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sources = ap.add_mutually_exclusive_group()
    sources.add_argument('--store', default=DEFAULT_STORE)
    sources.add_argument('--db')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('digest')
    p.add_argument('paths', nargs='+')
    p.add_argument('--all', action='store_true', help='store blobs for every draw, not only kept ones')
    p.add_argument('--all-resources', action='store_true',
                   help='store the resources (shaders, textures, buffers, meshes, render targets read) of every draw; '
                        'contributions, post-VS output and pass targets stay with the kept draws')
    p.add_argument('--buffer-cap', type=int, default=64, help='MB: larger buffer SRVs are hashed only (default 64)')
    p.add_argument('--dedup', action='store_true',
                   help='after digesting, hard-link per-capture files whose bytes equal a blob or another file (lossless)')
    p.add_argument('--compress', action='store_true',
                   help='after digesting, compress the store with Windows LZX file compression (lossless, transparent)')
    p.add_argument('--ps-list', help='pixel-shader checksums to keep (text file or UE ShaderIndex.csv)')
    p.add_argument('--marker-re', help='marker-path regex for kept draws (default: effect/particle/... names)')
    p.add_argument('--id')
    p.add_argument('--force', action='store_true')
    p.add_argument('--final-only', action='store_true',
                   help="redo only final.png (the presented swap buffer) and the manifest's 'final' of digested captures")
    p.add_argument('--renderdoc', default=DEFAULT_RD)
    p.add_argument('--timeout', type=int, default=7200)
    p.add_argument('--contrib', choices=('depth', 'all', 'off'), default='depth',
                   help="kept draws whose own change to their colour targets is saved: 'depth' (default) those with a "
                        "depth target (scene passes, not UI/post), 'all', or 'off'")
    p.add_argument('--contrib-dtype', choices=('half', 'float'), default='half',
                   help='EXR channel type of a contribution (after - before); half is the default')
    p.add_argument('--contrib-threshold', type=float, default=0.0,
                   help='shrink each contribution box to pixels whose largest |change| exceeds this (default 0: '
                        'every pixel whose bits changed)')
    p.add_argument('--keep-raw', action='store_true', help='keep contrib/*.raw (before+after crops) after conversion')
    p.add_argument('--postvs', choices=('depth', 'all', 'off'), default='depth',
                   help="kept draws whose post-VS/GS output is stored: 'depth' (default, as --contrib), 'all' or 'off'")
    p.add_argument('--postvs-cap', type=int, default=32, help='MB of post-VS data kept per draw (default 32)')
    p.add_argument('--no-dynamic', action='store_true', help='skip snapshots of render targets/UAVs kept draws read')
    p.add_argument('--dynamic-cap', type=int, default=64, help='MB: larger dynamic textures are hashed only')
    p.add_argument('--no-counters', action='store_true', help='skip per-event GPU durations')
    p.add_argument('--rehash', action='store_true',
                   help='hash a texture SRV at every use, not only its first, and store each new version (slower)')
    p.set_defaults(fn=digest)
    p = sub.add_parser('captures')
    p.set_defaults(fn=captures)
    p = sub.add_parser('draws')
    p.add_argument('-c', '--capture', action='append')
    p.add_argument('--shader')
    p.add_argument('--texture')
    p.add_argument('--marker')
    p.add_argument('--kept', action='store_true')
    p.add_argument('--blend')
    p.add_argument('--target')
    p.set_defaults(fn=draws)
    p = sub.add_parser('draw')
    p.add_argument('capture')
    p.add_argument('eid', type=int)
    p.add_argument('--json', action='store_true')
    p.add_argument('--cbuffers', action='store_true', help='print constant buffers as rows')
    p.add_argument('--max-cb', type=int, default=4096, help='largest cbuffer printed by --cbuffers')
    p.set_defaults(fn=draw)
    p = sub.add_parser('cbuffer')
    p.add_argument('capture')
    p.add_argument('key')
    p.set_defaults(fn=cbuffer)
    p = sub.add_parser('texture')
    p.add_argument('hash')
    p.add_argument('--png')
    p.add_argument('--mip', type=int, default=0)
    p.add_argument('--slice', type=int, default=0)
    p.add_argument('--scale', type=float, default=1.0, help='multiplier before clamping float formats')
    p.add_argument('--channel', help='dynamic EXR snapshots: one channel (e.g. S for stencil)')
    p.add_argument('--via-renderdoc', action='store_true',
                   help="decode with RenderDoc (opens the DDS, not the .rdc): matches PNGs saved from captures; "
                        "Pillow's BC1/BC4 decode can differ by a few levels")
    p.add_argument('--renderdoc', default=DEFAULT_RD)
    p.set_defaults(fn=texture)
    p = sub.add_parser('shader')
    p.add_argument('checksum')
    p.set_defaults(fn=shader)
    p = sub.add_parser('contrib')
    p.add_argument('capture')
    p.add_argument('eid', type=int)
    p.add_argument('--slot', type=int)
    p.add_argument('--png')
    p.add_argument('--scale', type=float, default=1.0)
    p.add_argument('--abs', action='store_true', help='|after - before| (shows darkening draws)')
    p.add_argument('--full', action='store_true', help='place the box on a black image of the whole target')
    p.set_defaults(fn=contrib)
    p = sub.add_parser('postvs')
    p.add_argument('capture')
    p.add_argument('eid', type=int)
    p.add_argument('--gs', action='store_true', help='geometry-shader output instead of VS output')
    p.add_argument('--csv')
    p.add_argument('--attr', help='also print this output attribute (e.g. TEXCOORD1) of each group\'s first vertex')
    p.add_argument('--group', type=int, help='vertices per particle when the draw is not instanced (e.g. 4 or 6)')
    p.add_argument('--limit', type=int, default=40)
    p.set_defaults(fn=postvs)
    p = sub.add_parser('dynamic')
    p.add_argument('capture')
    p.set_defaults(fn=dynamic)
    p = sub.add_parser('exposure')
    p.add_argument('capture')
    p.add_argument('--float-only', action='store_true')
    p.set_defaults(fn=exposure)
    p = sub.add_parser('gpu')
    p.add_argument('capture')
    p.add_argument('--top', type=int, default=30)
    p.add_argument('--kept', action='store_true')
    p.set_defaults(fn=gpu)
    p = sub.add_parser('stats')
    p.set_defaults(fn=stats)
    import digestlib
    p = sub.add_parser('index')
    p.add_argument('--root', action='append', nargs='+', required=True)
    p.add_argument('--db', dest='index_db')
    p.add_argument('--rebuild', action='store_true')
    p = sub.add_parser('where')
    p.add_argument('prefix')
    # Also accept source options after the command, without overriding global options.
    for command, parser in sub.choices.items():
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--store', default=argparse.SUPPRESS)
        if command != 'index':
            group.add_argument('--db', default=argparse.SUPPRESS)
    a = ap.parse_args()
    if any(v.split('=')[0] == '--store' for v in sys.argv[1:]) and any(
            v.split('=')[0] == '--db' for v in sys.argv[1:]):
        ap.error('--store and --db are mutually exclusive')
    try:
        if a.cmd == 'index':
            result = digestlib.build_index([r for roots in a.root for r in roots],
                                           a.index_db or a.db or digestlib.DEFAULT_DB, a.rebuild)
            print('captures added {added} / updated {updated} / removed {removed} / skipped {skipped}; '
                  '{seconds:.6f} s'.format(**result))
            return
        if a.cmd == 'where':
            if not a.db:
                ap.error('where requires --db')
            with digestlib.open(a.db) as d:
                for row in d.where(a.prefix):
                    print(json.dumps(row, sort_keys=True))
            return
        if a.db:
            if a.cmd == 'digest':
                ap.error('digest requires --store, not --db')
            with digestlib.open(a.db) as d:
                d.check_duplicates()
                if a.cmd == 'draws':
                    return draws(a)
                if a.cmd in ('draw', 'cbuffer', 'contrib', 'postvs', 'dynamic', 'exposure', 'gpu'):
                    a.store = d.resolve(a.capture)['store']
                    return a.fn(a)
                stores = [r[0] for r in d.con.execute('SELECT path FROM stores ORDER BY path')]
                if a.cmd == 'texture':
                    a.store = str(Path(d.texture_path(a.hash)).parents[2])
                    return a.fn(a)
                for store in stores:
                    a.store = store
                    a.fn(a)
                return
        sys.exit(a.fn(a) or 0)
    except (ValueError, sqlite3.Error) as ex:
        ap.exit(1, str(ex) + '\n')


if __name__ == '__main__':
    main()
