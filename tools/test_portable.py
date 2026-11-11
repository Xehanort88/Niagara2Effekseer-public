"""Checks for the pieces that let the tools run off Windows: tools/portpath.py, tools/wincache.py, hlslcheck's
'not checked' path and tools/testdata/link.sh (README § Test data for a cloud session).

    python tools/test_portable.py --work out/tests/portable

Checks of Linux-only behaviour print an 'n/a' line on Windows, where the real DLL runs and paths are native; they
are not skips (run_all_tests counts a SKIP as not passed). The link.sh round trip uses a throwaway checkout and data
folder, so it runs whether or not real test data is linked here.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import portpath  # noqa: E402
import wincache  # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append(bool(ok))
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else '  ' + str(detail)))


def not_here(name, why):
    print('n/a  %s: %s' % (name, why))


def test_split_paths():
    sep = os.pathsep
    check('split_paths: plain list', portpath.split_paths(sep.join(['a', 'b', ''])) == ['a', 'b'])
    check('split_paths: empty and None', portpath.split_paths('') == [] and portpath.split_paths(None) == [])
    if sep == ':':
        got = portpath.split_paths('E:/x/Exports:/abs/dir:D:\\y')
        check('split_paths: drive paths joined back', got == ['E:/x/Exports', '/abs/dir', 'D:\\y'], got)
        check('split_paths: one drive path', portpath.split_paths('E:/Games/a') == ['E:/Games/a'])
    else:
        check('split_paths: Windows keeps drive paths', portpath.split_paths('E:/a;D:/b') == ['E:/a', 'D:/b'])


def test_split_after_repo(work):
    if os.pathsep != ':':
        return
    repo = work / 'repo'
    (repo / 'E:' / 'fields').mkdir(parents=True)
    value = str(repo / 'E:' / 'fields') + ':' + str(work)
    got = portpath.split_paths(value)
    check('split_paths: <repo>/E + /fields joined back', got == [str(repo / 'E:' / 'fields'), str(work)], got)
    (work / 'X').mkdir()
    real = str(work / 'X') + ':' + str(work)               # a real folder named X, then another folder
    check('split_paths: a real one-letter folder stays apart', portpath.split_paths(real) == [str(work / 'X'), str(work)])


def test_under():
    check('under: relative joins the root', portpath.under('R', 'a/b.hlsl') == Path('R/a/b.hlsl'))
    check('under: no root', portpath.under(None, 'a/b.hlsl') == Path('a/b.hlsl'))
    check('under: a drive path is never joined', portpath.under('R', 'E:\\x\\b.hlsl') == Path(portpath.native('E:\\x\\b.hlsl')))


def test_runner_counts_na_as_pass():
    sys.path.insert(0, str(ROOT / 'tools'))
    import run_all_tests
    got = run_all_tests.classify(0, 'ok   a\nn/a  b: Linux only\n2 passed, 0 failed\n', False)[0]
    check('run_all_tests: an n/a line is not a SKIP', got == 'PASS', got)


def test_for_subprocess():
    spec = {'edits': [{'set': {'Tex': 'E:/x/t.png', 'Name': 'plain', 'N': 3}}, 'D:\\y\\m.efkmodel', 'rel/a.png']}
    got = portpath.for_subprocess(spec)
    if os.sep == '/':
        check('for_subprocess: drive paths absolute, the rest unchanged',
              got['edits'][0]['set']['Tex'] == os.path.abspath('E:/x/t.png') and got['edits'][1] == os.path.abspath('D:/y/m.efkmodel')
              and got['edits'][0]['set']['Name'] == 'plain' and got['edits'][0]['set']['N'] == 3 and got['edits'][2] == 'rel/a.png', got)
        check('for_subprocess: the spec itself is not changed', spec['edits'][0]['set']['Tex'] == 'E:/x/t.png')
    else:
        check('for_subprocess: unchanged on Windows', got is spec)


def test_native():
    check('native: None and non-drive unchanged', portpath.native(None) is None and portpath.native('a\\b') == 'a\\b')
    if os.sep == '/':
        check('native: drive path gets slashes', portpath.native('D:\\R\\out\\x.png') == 'D:/R/out/x.png')
    else:
        check('native: unchanged on Windows', portpath.native('D:\\R\\x.png') == 'D:\\R\\x.png')


def test_find(work):
    base = work / 'find'
    (base / 'Content' / 'Effects').mkdir(parents=True)
    (base / 'Content' / 'Effects' / 'FxTX_Glow_A.json').write_text('{}')
    exact = base / 'Content' / 'Effects' / 'FxTX_Glow_A.json'
    check('find: exact path', portpath.find(exact) == exact)
    check('find: missing gives None', portpath.find(base / 'Content' / 'nope.json') is None)
    if os.name != 'nt':
        hit = portpath.find(base / 'content' / 'EFFECTS' / 'fxtx_glow_a.json')
        check('find: other letter case found', hit == exact, hit)
        cwd = os.getcwd()
        try:
            os.chdir(base)
            rel = portpath.find('content/effects/FXTX_GLOW_A.json')
            check('find: relative path stays relative', rel == Path('Content/Effects/FxTX_Glow_A.json'), rel)
        finally:
            os.chdir(cwd)


def test_wincache(work):
    if wincache.ON_WINDOWS:
        not_here('wincache stored results', 'runs the real d3dcompiler_47 here')
        return
    old = wincache.CACHE
    wincache.CACHE = work / 'wincache'
    try:
        try:
            wincache.disassemble(b'DXBC-not-stored')
            check('wincache: missing result raises Unavailable', False, 'no exception')
        except wincache.Unavailable as e:
            check('wincache: missing result raises Unavailable', 'needs Windows' in str(e), e)
        # results as a Windows run stores them
        entry = wincache._entry('disassemble', b'DXBC-1', 0x80)
        entry.parent.mkdir(parents=True)
        entry.write_text(json.dumps({'text': 'ps_5_0\nret\n\0'}))
        check('wincache: stored disassembly read back', wincache.disassemble(b'DXBC-1') == 'ps_5_0\nret\n\0')
        entry = wincache._entry('compile', b'src', 'main', 'ps_4_0', 0)
        entry.parent.mkdir(parents=True)
        entry.write_text(json.dumps({'ok': False, 'code': base64.b64encode(b'').decode(), 'messages': 'X(1,1): error'}))
        got = wincache.compile_hlsl(b'src', 'ps_4_0', name='any/other/name.efkmat')
        check('wincache: compile read back, name not in the key', got == (False, b'', 'X(1,1): error'), got)
        entry = wincache._entry('decompress', b'raw', 64)
        entry.parent.mkdir(parents=True)
        entry.write_text(json.dumps({'data': base64.b64encode(b'DXBCxx').decode()}))
        check('wincache: decompress read back', wincache.decompress('decompress_shader.exe', b'raw', 64) == b'DXBCxx')
        trace = work / 'trace.tsv'
        os.environ['N2E_TRACE_FILE'] = str(trace)
        try:
            wincache._store(wincache._entry('disassemble', b'DXBC-2', 0x80), {'text': 't'})
        finally:
            del os.environ['N2E_TRACE_FILE']
        check('wincache: a stored result is noted for --trace-files',
              trace.read_text().startswith('read\t') and 'wincache' in trace.read_text())
    finally:
        wincache.CACHE = old


def test_hlslcheck_unavailable(work):
    if wincache.ON_WINDOWS:
        not_here('hlslcheck not checked', 'compiles for real here')
        return
    sys.path.insert(0, str(ROOT / 'tools' / 'hlslcheck'))
    import test_hlslcheck                                    # its efkmat() writes a minimal material
    mat = work / 'portable.efkmat'
    mat.write_bytes(test_hlslcheck.efkmat('$F4$ c = $TEX_P0$$UV$1$TEX_S0$;\n'))
    env = dict(os.environ, N2E_WINCACHE=str(work / 'empty-wincache'))
    r = subprocess.run([sys.executable, str(ROOT / 'tools/hlslcheck/hlslcheck.py'), str(mat)],
                       capture_output=True, text=True, env=env)
    check('hlslcheck: no stored result -> N/A, exit 2', r.returncode == 2 and 'not checked (needs Windows)' in r.stdout,
          (r.returncode, r.stdout[-300:], r.stderr[-300:]))


def test_trace(work):
    """tools/testdata/trace/sitecustomize.py records what a traced process reads, checks, copies and hands to dotnet."""
    ext = work / 'external'
    (ext / 'Model').mkdir(parents=True)
    (ext / 'tree').mkdir()
    for name in ('read.json', 'checked.json', 'copied.json', 'tree/copied2.json', 'Model/m.efkmodel', 'tex.png'):
        (ext / name).write_text('x')
    (ext / 'spec.json').write_text(json.dumps({'edits': [{'set': {'path': str(ext / 'tex.png')}}]}))
    trace = work / 'trace-check.tsv'
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'tools/testdata/trace'), N2E_TRACE_FILE=str(trace),
               N2E_TRACE_IGNORE='', N2E_TRACE_REPO=str(work / 'no-repo'), N2E_TRACE_KEEP='')
    code = ('import os, shutil, subprocess, sys; from pathlib import Path; e = Path(sys.argv[1]); '
            '(e / "read.json").read_text(); (e / "checked.json").is_file(); os.path.exists(e / "checked.json"); '
            'shutil.copyfile(e / "copied.json", e / "copy-out.json"); shutil.copytree(e / "tree", e / "tree-out"); '
            'subprocess.run([sys.executable, "-c", "pass", str(e / "spec.json"), "--meshes", str(e / "Model")])')
    subprocess.run([sys.executable, '-c', code, str(ext)], env=env, check=True)
    got = {tuple(l.split('\t', 1)) for l in trace.read_text().splitlines()}
    def has(kind, name):
        return (kind, str(ext / name)) in got
    check('trace: opened file', has('read', 'read.json'))
    check('trace: file only checked', has('stat', 'checked.json'))
    check('trace: file copied with shutil', has('read', 'copied.json'))
    check('trace: file copied with shutil.copytree (CopyFile2 on Windows)', has('read', 'tree/copied2.json'))
    check('trace: JSON spec on a command line and the file it names', has('arg', 'spec.json') and has('arg', 'tex.png'))
    check('trace: folder on a command line', has('argdir', 'Model'))
    check('trace: written file not recorded', not any(p.endswith('copy-out.json') for _, p in got))
    if os.name != 'nt':
        test_trace_behaviour(work, env)


def test_trace_behaviour(work, env):
    """The hook changes nothing for the traced program, and records reads from every thread."""
    ext = work / 'external2'
    (ext / 'many').mkdir(parents=True)
    (ext / 'bin').mkdir()
    for k in range(200):
        (ext / 'many' / ('f%03d.json' % k)).write_text('x')
    (ext / 'target.json').write_text('x')
    os.symlink(ext / 'target.json', ext / 'link.json')
    tool = ext / 'bin' / 'portable-tool'
    tool.write_text('#!/bin/sh\n')
    tool.chmod(0o755)
    trace = work / 'trace-behaviour.tsv'
    env = dict(env, N2E_TRACE_FILE=str(trace), PATH=str(ext / 'bin') + os.pathsep + env.get('PATH', ''))
    code = ('import os, shutil, sys; from pathlib import Path; from concurrent.futures import ThreadPoolExecutor; '
            'e = Path(sys.argv[1]); '
            'list(ThreadPoolExecutor(8).map(lambda p: p.read_text(), sorted((e / "many").iterdir()))); '
            'shutil.copy2(e / "link.json", e / "link-copy.json", follow_symlinks=False); '
            'assert os.path.islink(e / "link-copy.json"); '
            'assert shutil.which("portable-tool"); '
            'assert os.stat in os.supports_follow_symlinks; print("ok")')
    r = subprocess.run([sys.executable, '-c', code, str(ext)], env=env, capture_output=True, text=True)
    check('trace: copy2(follow_symlinks=False) and os.supports_* unchanged under the hook',
          r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr)
    got = {tuple(l.split('\t', 1)) for l in trace.read_text().splitlines()} if trace.exists() else set()
    reads = {p for k, p in got if k == 'read' and '/many/' in p}
    check('trace: reads from 8 threads all recorded', len(reads) == 200, len(reads))
    check('trace: a program found on PATH is not recorded', not any(p.endswith('portable-tool') for _, p in got))


def test_link(work):
    if os.name == 'nt':
        not_here('link.sh', 'Linux only: Windows reads the drives themselves')
        return
    root, data, pack = work / 'checkout', work / 'testdata', work / 'pack'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'tools').mkdir()
    (root / 'tools' / 'marker.txt').write_text('checkout')
    (root / 'out' / 'edits' / 'other-task' / 'scratch').mkdir(parents=True)   # another task's empty folder
    exports = pack / 'E' / 'Games' / 'Exports'
    (exports / 'Game' / 'Content').mkdir(parents=True)
    (exports / 'Game' / 'Content' / 'P_Fx.json').write_text('{"e": 1}')
    (pack / '_repo' / 'out' / 'edits' / 'portable-test').mkdir(parents=True)
    (pack / '_repo' / 'out' / 'edits' / 'portable-test' / 'fit.json').write_text('{}')
    (pack / 'manifest.json').write_text(json.dumps({'repo_root': 'D:\\Work\\Niagara2Effseeker', 'files': [],
                                                    'too_large': [], 'unplaced': [], 'listed_folders': []}))
    env = dict(os.environ, N2E_LINK_ROOT=str(root), N2E_TESTDATA_DIR=str(data))
    link = ['bash', str(ROOT / 'tools/testdata/link.sh')]
    r = subprocess.run(link + [str(pack)], capture_output=True, text=True, env=env)
    check('link.sh: runs', r.returncode == 0, r.stdout + r.stderr)
    cwd = os.getcwd()
    os.chdir(root)
    try:
        dump = Path('E:/Games/Exports/Game/Content/P_Fx.json')
        check('link.sh: drive path resolves', dump.is_file())
        rel = dump.resolve().relative_to(Path('E:/Games/Exports').resolve())   # build_effekseer does this
        check('link.sh: resolve() stays inside the exports root', rel == Path('Game/Content/P_Fx.json'), rel)
        check('link.sh: repository Windows path -> checkout', Path('D:/Work/Niagara2Effseeker/tools/marker.txt').is_file())
        fit = Path('out/edits/portable-test/fit.json')
        check('link.sh: out/ file copied in', fit.is_file() and not fit.is_symlink())
        status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout
        check('link.sh: git status stays clean of the links', 'E:' not in status and 'D:' not in status, status)
        r = subprocess.run(link + [str(pack)], capture_output=True, text=True, env=env)
        check('link.sh: second run', r.returncode == 0 and fit.is_file(), r.stdout + r.stderr)
    finally:
        os.chdir(cwd)
    r = subprocess.run(link + ['--remove'], capture_output=True, text=True, env=env)
    check('link.sh: --remove', r.returncode == 0 and not (root / 'E:').exists() and not (root / 'D:').exists()
          and not (root / 'out/edits/portable-test').exists() and not data.exists(), r.stdout + r.stderr)
    check('link.sh: --remove leaves other empty folders in out/', (root / 'out/edits/other-task/scratch').is_dir())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True, type=Path)
    a = ap.parse_args()
    work = a.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    test_split_paths()
    test_split_after_repo(work)
    test_under()
    test_runner_counts_na_as_pass()
    test_native()
    test_for_subprocess()
    test_find(work)
    test_wincache(work)
    test_hlslcheck_unavailable(work)
    test_trace(work)
    test_link(work)
    failed = RESULTS.count(False)
    print('%d passed, %d failed' % (len(RESULTS) - failed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
