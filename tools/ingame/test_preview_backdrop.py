"""Tests for the lost-backdrop check of the preview skill's capture (no browser: run.js is replaced by a stub).
usage: python tools/ingame/test_preview_backdrop.py"""
import importlib.util
import json
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


def write_capture(out, lost, status='ok'):
    """A run.js result: frames with or without the backdrop; the effect covers the middle of frames between the first and last."""
    d = out / 'capture-1'
    d.mkdir(parents=True, exist_ok=True)
    back = bytes([0, 0, 0, 255] if lost else [9, 12, 20, 255])
    frame = bytearray(back * (W * H))
    (d / 'f000.rgba').write_bytes(frame)
    busy = bytearray(frame)
    for y in range(H // 4):                      # a bright effect over part of the frame
        for x in range(W):
            busy[(y * W + x) * 4:(y * W + x) * 4 + 3] = bytes([250, 200, 100])
    (d / 'f001.rgba').write_bytes(busy)
    (d / 'f002.rgba').write_bytes(frame)
    (out / 'results.jsonl').write_text(json.dumps({'status': status, 'capture': {'frames': 3, 'ended': True, 'size': [W, H]}, 'failures': [],
                                                   'warnings': []}) + '\n', encoding='utf-8')


def run(tmp, script, **kw):
    """run_capture with run.js replaced: each call takes the next (lost, returncode) from script."""
    calls = []

    def fake(cmd, **_):
        lost, code = script[len(calls)]
        calls.append(cmd)
        write_capture(Path(cmd[cmd.index('--out') + 1]), lost)
        return NS(stdout='', stderr='', returncode=code)

    args = NS(work=str(tmp), project=str(tmp), port=9000, step=1, capture_seconds=4, **kw)
    with patch.object(P.subprocess, 'run', fake), patch.object(P, 'free_port', lambda p: p):
        return P.run_capture(args, 't', 1, '', []), len(calls)


def main():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        write_capture(t / 'ok', False)
        write_capture(t / 'bad', True)
        check('healthy backdrop is not flagged', not P.backdrop_lost(t / 'ok'))
        check('black backdrop is flagged', P.backdrop_lost(t / 'bad'))
        check('no frames: nothing to flag', not P.backdrop_lost(t / 'empty'))
        (t / 'bigger').mkdir()
        write_capture(t / 'bigger', False)
        rec = json.loads((t / 'bigger/results.jsonl').read_text(encoding='utf-8'))
        check('a frame of another size is skipped, not crashed on', not P.backdrop_lost(t / 'bigger', {'capture': {'size': [10, 10]}}), rec)

        check('good first take: one run, exit 0', run(t / 'a', [(False, 0)]) == (0, 1))
        check('lost then good: retaken, exit 0', run(t / 'b', [(True, 0), (False, 0)]) == (0, 2))
        check('lost on every take: exit 1 after the retakes', run(t / 'c', [(True, 0)] * 3) == (1, 3))
        check('retakes 0: one run, exit 1', run(t / 'd', [(True, 0)], retakes=0) == (1, 1))
        check('black backdrop allowed: no retake, exit 0', run(t / 'e', [(True, 0)], black_backdrop_ok=True) == (0, 1))
        check('a failed run is not retaken', run(t / 'f', [(False, 1)]) == (1, 1))
    print('%d failure(s)' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
