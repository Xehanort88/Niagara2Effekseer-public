"""Run inside qrenderdoc (started by rdcdigest.py texture --via-renderdoc): opens a DDS blob as an image (no .rdc)
and saves one mip/slice as PNG with RenderDoc's own decoder, so the pixels match PNGs earlier tasks saved from the
capture itself (Pillow's BC1/BC4 decode can differ from it by a few levels). Env: RD_DDS, RD_PNG, RD_MIP, RD_SLICE.
Writes RD_PNG + '.log'; its last line is 'done' on success."""
import os
import traceback

import renderdoc as rd

log = open(os.environ['RD_PNG'] + '.log', 'w')
try:
    rd.InitialiseReplay(rd.GlobalEnvironment(), [])
    cap = rd.OpenCaptureFile()
    if cap.OpenFile(os.environ['RD_DDS'], '', None) != rd.ResultCode.Succeeded:
        raise RuntimeError('open failed')
    st, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
    if st != rd.ResultCode.Succeeded:
        raise RuntimeError('replay failed: %s' % st)
    t = ctrl.GetTextures()[0]
    ts = rd.TextureSave()
    ts.resourceId = t.resourceId
    ts.destType = rd.FileType.PNG
    ts.mip = int(os.environ.get('RD_MIP', '0'))
    ts.slice.sliceIndex = int(os.environ.get('RD_SLICE', '0'))
    ts.alpha = rd.AlphaMapping.Preserve
    ctrl.SaveTexture(ts, os.environ['RD_PNG'])
    log.write('%s %dx%d mips %d\ndone\n' % (t.format.Name(), t.width, t.height, t.mips))
    ctrl.Shutdown()
    cap.Shutdown()
except Exception:
    log.write(traceback.format_exc())
log.close()
os._exit(0)
