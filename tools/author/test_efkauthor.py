#!/usr/bin/env python3
"""Tests for efkauthor.py: the pure helpers, then a real build from a `blank` base read back with `dump`.

    python tools/author/test_efkauthor.py --work out/tests/author [--tool <Niagara2Efk.dll>]
"""
import argparse, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import efkauthor as ea  # noqa: E402

passed = failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print('PASS ' + name)
    else:
        failed += 1
        print('FAIL %s %s' % (name, detail))


def close(a, b, tol=0.01):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def pure():
    rows = ea.spline4_rows([(0, 0), (1.4, 2.0), (1.4, 3.2), (0, 5)], 5)
    check('spline4_rows reproduces the fitted crystal rows',
          close(rows[1], (0.96, 1.31)) and close(rows[2], (1.61, 2.54)) and close(rows[3], (1.01, 3.75)) and close(rows[4], (0, 5)), rows)
    c = ea.card(3.6, 3.0, (0.05, 0.05), (0.9, 0.9))
    check('card arc length equals its height for a square crop', abs(3.0 * 2 * abs(c['Mesh/AngleBeginEnd'][0]) * 3.14159265 / 180 - 3.6) < 0.01, c)
    check('repeats: chain coil fits 21', round(ea.repeats(34.9, 0.6, 0.36)) == 21)
    r = ea.rising(0.06, (0, 3))
    check('rising cancels the gravity', r['LocationValues/PVA/Acceleration/Y'] == 0 and r['LocationValues/PVA/Location/Y'] == [0, 3])
    n = ea.neutral()
    check('neutral resets rotation, scale, gravity', n['RotationValues/Fixed/Rotation'] == [0, 0, 0] and n['ScalingValues/Fixed/Scale'] == [1, 1, 1]
          and n['LocationValues/PVA/Acceleration/Y'] == 0)
    s = ea.spline4([0, 0], [1, 1], [1, 2], [0, 3])
    check('spline4 sets all four points', all('Shape/Point%d' % i in s for i in range(1, 5)))
    check('facet_uv is divisions minus one', ea.facet_uv(7, 5)['UV/UVSize'] == [6, 4])


def built(work, tool):
    blank = ea.make_blank(work / 'blank.efkefc', 90, tool)
    spec = ea.Spec(blank, 'AuthorTest', end_frame=100)
    spec.model('Spike', ea.merge(ea.spline4([0.4, 0], [0.5, 0.4], [0.2, 2.6], [0, 3.1]), ea.divisions(5, 4), ea.fade_rows('Lower')))
    spec.model('Disc', ea.flat_disc())
    spec.node0('Floor', ea.merge(ea.model_node('Disc', 'Add', [120, 200, 255, 200]), ea.once(90, 4, 20), ea.floor_tilt(), ea.grow(1, 5)))
    spec.fresh('Spikes', ea.merge(ea.model_node('Spike', 'Blend', [200, 235, 255, 255], culling='Front'), ea.once(90, 4, 20), ea.ring_of(8, 3, 30)))
    spec.keys(ea.scale_keys([[0, 1], [1, 1]], [[0, 0], [8, 1.1], [12, 1]], [[0, 1], [1, 1]]))
    spec.fresh('Motes', ea.merge(ea.sprite('', 'Add', [255, 255, 255, 255]), ea.emit(10, 4, 20), ea.on_circle(2), ea.rising(0.05, (0, 2))))
    report = spec.build(work / 'AuthorTest.efkefc', tool=tool)
    check('build verifies', any(l.startswith('VERIFY OK') for l in report), report)
    check('no warnings', not any(l.startswith('WARNING') for l in report), report)
    dump = subprocess.run(['dotnet', str(tool), 'dump', str(work / 'AuthorTest.efkefc')], capture_output=True, text=True).stdout
    blocks = {}
    for block in dump.split('### ')[1:]:
        head, _, body = block.partition('\n')
        blocks[head] = dict(l.split(' = ', 1) for l in body.splitlines() if ' = ' in l)
    floor, spikes, motes = blocks.get('0 Floor', {}), blocks.get('1 Spikes', {}), blocks.get('2 Motes', {})
    check('node 0 keeps its floor tilt', floor.get('RotationValues/Fixed/Rotation') == '(65, 0, 0)', floor.get('RotationValues/Fixed/Rotation'))
    check('a fresh part inherits nothing (ring lean only, no floor tilt)', spikes.get('RotationValues/Fixed/Rotation') == '(0, 0, -30)',
          spikes.get('RotationValues/Fixed/Rotation'))
    check('scale curve sampled every frame on a frame timeline', spikes.get('ScalingValues/FCurve/FCurve/Y/Sampling') == '1'
          and spikes.get('ScalingValues/FCurve/FCurve/Timeline') == 'Time')
    check('rising motes have no gravity', motes.get('LocationValues/PVA/Acceleration/Y') == '0' and motes.get('LocationValues/PVA/Velocity/Y') == '0.05',
          (motes.get('LocationValues/PVA/Acceleration/Y'), motes.get('LocationValues/PVA/Velocity/Y')))
    model = blocks.get('procedural 0 Spike', {})
    check('model points as written', model.get('Shape/Point4') == '(0, 3.1)', model.get('Shape/Point4'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--tool', type=Path, default=ea.DEFAULT_TOOL)
    a = p.parse_args()
    work = a.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    pure()
    built(work, a.tool)
    print('RESULT: %d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
