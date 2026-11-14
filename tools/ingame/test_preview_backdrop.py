"""Tests for the lost-backdrop check of the preview skill's capture (no browser: run.js is replaced by a stub).
usage: python tools/ingame/test_preview_backdrop.py"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('preview', ROOT / '.claude/skills/run-niagara2effekseer/preview.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
FAILS = []
W, H = 64, 36


def check(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(detail)[:400]))
    if not cond:
        FAILS.append(name)


# ASSEMBLER TEST PROJECT as captured (out/edits/preview-backdrop-check-20261114): good backdrop ~36 over the upper
# 70 %, sceneMean [26.1, 29.7, 41]; a lost one leaves sceneMean [7.9, 6.3, 10.8] and an upper frame of mean ~3.7.
GOOD, LOST_BLACK, LOST_DIM = [30, 36, 44], [0, 0, 0], [4, 3, 4]      # LOST_DIM: dark but no pure-black pixel
GOOD_SCENE, LOST_SCENE = [26.1, 29.7, 41], [7.9, 6.3, 10.8]
PROJECT = 'D:/x/ASSEMBLER TEST PROJECT'


def write_capture(out, lost, status='ok', frames=3, back=None, scene=None):
    """A run.js result: frames with or without the backdrop; the effect covers part of frames between the first and last.
    frames=1: a one-frame capture (an --efk copy with every node hidden, the 'empty' reference of debug captures)."""
    d = out / 'capture-1'
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob('f*.rgba'): f.unlink()
    back = back or (LOST_BLACK if lost else GOOD)
    frame = bytearray(bytes(back + [255]) * (W * H))
    (d / 'f000.rgba').write_bytes(frame)
    if frames > 1:
        busy = bytearray(frame)
        for y in range(H // 4):                      # a bright effect over part of the frame
            for x in range(W):
                busy[(y * W + x) * 4:(y * W + x) * 4 + 3] = bytes([250, 200, 100])
        for n in range(1, frames - 1): (d / ('f%03d.rgba' % n)).write_bytes(busy)
        (d / ('f%03d.rgba' % (frames - 1))).write_bytes(frame)
    rec = {'status': status, 'capture': {'frames': frames, 'ended': True, 'size': [W, H]}, 'failures': [], 'warnings': [],
           'sceneMean': scene if scene is not None else (LOST_SCENE if lost else GOOD_SCENE)}
    (out / 'results.jsonl').write_text(json.dumps(rec) + '\n', encoding='utf-8')


def run(tmp, script, frames=3, project=PROJECT, **kw):
    """run_capture with run.js replaced: each call takes the next (lost, returncode) from script; lost may also be a
    dict of write_capture arguments."""
    calls = []

    def fake(cmd, **_):
        lost, code = script[len(calls)]
        calls.append(cmd)
        opt = lost if isinstance(lost, dict) else {'lost': lost}
        write_capture(Path(cmd[cmd.index('--out') + 1]), **dict({'frames': frames}, **opt))
        return NS(stdout='', stderr='', returncode=code)

    args = NS(work=str(tmp), project=project, port=9000, step=1, capture_seconds=4, **kw)
    with patch.object(P.subprocess, 'run', fake), patch.object(P, 'free_port', lambda p: p):
        return P.run_capture(args, 't', 1, '', []), len(calls)


def main():
    lost = P.backdrop_lost
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        write_capture(t / 'ok', False)
        write_capture(t / 'bad', True)
        check('healthy backdrop is not flagged', not lost(t / 'ok', project=PROJECT))
        check('black backdrop is flagged', lost(t / 'bad', project=PROJECT))
        check('no frames, healthy record: nothing to flag', not lost(t / 'empty', {'sceneMean': GOOD_SCENE}, project=PROJECT))
        (t / 'bigger').mkdir()
        write_capture(t / 'bigger', False)
        rec = json.loads((t / 'bigger/results.jsonl').read_text(encoding='utf-8'))
        check('a frame of another size is skipped, not crashed on',
              not lost(t / 'bigger', {'capture': {'size': [10, 10]}, 'sceneMean': GOOD_SCENE}, project=PROJECT), rec)

        # The two 2026-11-14 misses: a one-frame empty reference and a full capture, both 'status ok'.
        for n in (1, 334):
            write_capture(t / ('g%d' % n), False, frames=n)
            check('%d-frame capture, good backdrop: not flagged' % n, not lost(t / ('g%d' % n), project=PROJECT))
            write_capture(t / ('d%d' % n), True, frames=n, back=LOST_DIM)
            check('%d-frame capture, dim lost backdrop (no pure black): flagged' % n, lost(t / ('d%d' % n), project=PROJECT))
            write_capture(t / ('s%d' % n), True, frames=n, back=GOOD)
            check('%d-frame capture, lost sceneMean but bright f000: flagged' % n,
                  'scene mean' in (lost(t / ('s%d' % n), project=PROJECT) or ''))
            write_capture(t / ('n%d' % n), True, frames=n, back=LOST_DIM, scene=[])
            check('%d-frame capture, no sceneMean: f000 floor alone flags it' % n, lost(t / ('n%d' % n), project=PROJECT))
        write_capture(t / 'u', True, frames=1, back=LOST_DIM, scene=LOST_SCENE)
        check('a project without a reference: absolute f000 floor', lost(t / 'u', project='D:/x/OTHER') is not None)
        write_capture(t / 'v', False, frames=1, back=[12, 14, 16], scene=[12, 14, 16])
        check('a project without a reference: a dim real backdrop passes', lost(t / 'v', project='D:/x/OTHER') is None)
        check('--backdrop-floor overrides the f000 floor',
              lost(t / 'v', project='D:/x/OTHER', floor=20) is not None and lost(t / 'g1', project=PROJECT, floor=2) is None)

        check('good first take: one run, exit 0', run(t / 'a', [(False, 0)]) == (0, 1))
        check('lost then good: retaken, exit 0', run(t / 'b', [(True, 0), (False, 0)]) == (0, 2))
        check('lost on every take: exit 1 after the retakes', run(t / 'c', [(True, 0)] * 3) == (1, 3))
        check('retakes 0: one run, exit 1', run(t / 'd', [(True, 0)], retakes=0) == (1, 1))
        check('black backdrop allowed: no retake, exit 0', run(t / 'e', [(True, 0)], black_backdrop_ok=True) == (0, 1))
        check('a failed run is not retaken', run(t / 'f', [(False, 1)]) == (1, 1))
        dim = {'lost': True, 'back': LOST_DIM}
        check('one-frame capture, dim lost then good: retaken, exit 0', run(t / 'g', [(dim, 0), (False, 0)], frames=1) == (0, 2))
        check('one-frame capture, lost on every take: exit 1', run(t / 'h', [(dim, 0)] * 3, frames=1) == (1, 3))
        check('one-frame capture, good: one run, exit 0', run(t / 'i', [(False, 0)], frames=1) == (0, 1))
    h = subprocess.run([sys.executable, str(ROOT / '.claude/skills/run-niagara2effekseer/preview.py'), 'capture', '--help'],
                       capture_output=True, text=True)
    check('capture --help builds (argparse checks every help string)', h.returncode == 0 and '--backdrop-floor' in h.stdout,
          h.stderr[-300:])
    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
